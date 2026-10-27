import json
from base64 import b64encode
from time import time

import pytest
from itsdangerous import TimestampSigner
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient
from starlette_wtf import CSRFProtectMiddleware

from vms.app import app
from vms.jobs.cancellation import CancellationControl, JobCancelled, enter_stage
from vms.jobs.queue import finish_job, get_status_dict


@pytest.fixture
def history(tmp_path):
    status = get_status_dict()
    status.clear()

    def add(jobid, owner="owner", state="pending", **extra):
        info = {
            "owner": owner,
            "status": state,
            "cluster_label": "Test cluster",
            "submitted_at": "2026-10-26T12:00:00+00:00",
            "start_time": time(),
            "result_db_path": str(tmp_path / f"{jobid}.duckdb"),
            "counts": {"completed": 0, "requested": 100},
            "last_update": {},
            **extra,
        }
        status[jobid] = info
        return info

    yield add
    status.clear()


@pytest.fixture
def client():
    # Exercise the real routes and middleware without starting a Ray cluster.
    application = Starlette(
        routes=app.routes,
        middleware=[
            Middleware(SessionMiddleware, secret_key="test"),
            Middleware(CSRFProtectMiddleware, csrf_secret="test"),
        ],
    )
    with TestClient(application) as client:
        session = b64encode(json.dumps({"job_owner": "owner"}).encode())
        client.cookies.set("session", TimestampSigner("test").sign(session).decode())
        yield client


def token(client, jobid):
    from lxml import html

    response = client.get(f"/analysis?jobid={jobid}")
    assert response.status_code == 200
    return html.fromstring(response.text).xpath('//input[@name="csrf_token"]/@value')[0]


def test_history_owner_order_and_cancellation(client, history):
    history("first")
    history("foreign", owner="someone else")
    failed = history("failed", state="failed")
    newest = history("newest")
    response = client.get("/jobs")
    assert response.status_code == 200
    assert "foreign" not in response.text
    assert (
        response.text.index("jobid=newest")
        < response.text.index("jobid=failed")
        < response.text.index("jobid=first")
    )
    assert "every 2s" in response.text
    assert "Failed" in response.text
    assert 'href="/"' in response.text
    csrf = token(client, "newest")
    assert client.post("/analysis/cancel?jobid=newest").status_code == 403
    response = client.post("/analysis/cancel?jobid=newest", data={"csrf_token": csrf})
    assert response.status_code == 200
    assert newest["status"] == "cancelled"
    assert 'data-tab="queue" data-status="cancelled"' in response.text
    assert "No realizations completed" in response.text
    assert "jobid=newest" not in client.get("/jobs").text
    assert failed["status"] == "failed"
    assert (
        client.post(
            "/analysis/cancel?jobid=foreign", data={"csrf_token": csrf}
        ).status_code
        == 403
    )
    assert client.get("/analysis?jobid=foreign").status_code == 200
    assert 'action="/analysis/cancel' not in client.get("/analysis?jobid=foreign").text


def test_terminal_cancel_is_noop_and_list_stops_polling(client, history):
    info = history("done", state="done", result={"experiment": 1, "cluster": 18})
    csrf = token(client, "done")
    client.post("/analysis/cancel?jobid=done", data={"csrf_token": csrf})
    assert "cancel_requested" not in info
    assert "every 2s" not in client.get("/jobs/list").text
    with TestClient(client.app) as anonymous:
        assert "No jobs yet" in anonymous.get("/jobs").text


def test_retained_cancelled_results_reload_and_sse(client, history):
    info = history(
        "partial",
        state="running",
        result={"experiment": 1, "cluster": 18},
        cancelled_stage="apitof",
        counts={"requested": 100, "completed": 3},
    )
    client.portal.call(finish_job, info)
    assert info["status"] == "done"
    for response in [
        client.get("/analysis?jobid=partial"),
        client.get(
            "/analysis/updates?jobid=partial", headers={"accept": "text/event-stream"}
        ),
    ]:
        assert "Finished — cancelled early" in response.text
        assert 'data-tab="apitof" data-status="cancelled"' in response.text
        assert 'href="#explorer"' in response.text
        assert 'href="#spectrogram"' in response.text
    assert "cancelled early" in client.get("/jobs").text


def test_preparation_handoff_refuses_simulation_after_cancel(monkeypatch):
    # Instantiate the actor implementation locally to test the atomic protocol.
    cls = CancellationControl.__ray_metadata__.modified_class
    control = cls()
    assert control.enter("skimmer")
    assert control.request_cancel() == "skimmer"
    assert not control.enter("apitof")
    assert control.requested()


def test_enter_cancelled_stage(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("vms.jobs.cancellation.ray.get", lambda value: value)
    control = SimpleNamespace(enter=SimpleNamespace(remote=lambda stage: False))
    with pytest.raises(JobCancelled):
        enter_stage(control, "apitof")


def test_cancellation_wakes_blocked_iterator(monkeypatch):
    from threading import Event
    from types import SimpleNamespace

    from vms.jobs.cancellation import cancel_iterator

    cancelled = Event()
    monkeypatch.setattr("vms.jobs.cancellation.ray.get", lambda value: value)
    control = SimpleNamespace(requested=SimpleNamespace(remote=lambda: True))
    iterator = SimpleNamespace(request_cancel=cancelled.set)
    with cancel_iterator(control, iterator):
        assert cancelled.wait(2)


def test_cleanup_failure_is_reported(client, history, monkeypatch):
    from vms.jobs.queue import request_cancellation

    info = history("cleanup")

    def fail_cleanup(info):
        raise PermissionError("Output could not be removed")

    monkeypatch.setattr("vms.jobs.cancellation.clean_job_files", fail_cleanup)
    client.portal.call(request_cancellation, info)
    assert info["status"] == "failed"
    assert "Output could not be removed" in info["failure"]["message"]
