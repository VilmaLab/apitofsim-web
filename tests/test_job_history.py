import json
from base64 import b64encode
from pathlib import Path
from time import time

import pytest
from apitofsim.workflow import DatabasePaths
from itsdangerous import TimestampSigner
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient
from starlette_wtf import CSRFProtectMiddleware

from vms.app import app
from vms.jobs.cancellation import CancellationControl, JobCancelled, enter_stage
from vms.jobs.failures import failure_path
from vms.jobs.queue import finish_job, get_status_dict


def touch_result(path):
    paths = DatabasePaths(path)
    paths.create()
    paths.duckdb.touch()
    return paths


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
            "result_db_path": str(tmp_path / f"{jobid}.apitofsim"),
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
    touch_result(Path(info["result_db_path"]))
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
    touch_result(Path(info["result_db_path"]))
    for response in [
        client.get("/analysis?jobid=partial"),
        client.get(
            "/analysis/updates?jobid=partial", headers={"accept": "text/event-stream"}
        ),
    ]:
        assert "Finished — stopped early" in response.text
        assert 'data-tab="apitof" data-status="cancelled"' in response.text
        assert 'href="#explorer"' in response.text
        assert 'href="#spectrogram"' in response.text
    assert "stopped early" in client.get("/jobs").text


def test_deleted_results_redirect_and_remain_in_history(client, history):
    from lxml import html

    info = history("done", state="done", result={"experiment": 1, "cluster": 18})
    result_path = Path(info["result_db_path"])
    paths = touch_result(result_path)
    assert client.get("/analysis?jobid=done").status_code == 200
    paths.duckdb.unlink()

    for path in (
        "/analysis?jobid=done",
        "/analysis/results/done/explorer",
        "/analysis/results/done/spectrogram",
        "/analysis/results/done/transmission/model",
        "/analysis/results/done/transmission/model/calc",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/jobs"
    response = client.get(
        "/analysis/results/done/transmission/model/calc",
        headers={"HX-Request": "true"},
    )
    assert response.headers["HX-Redirect"] == "/jobs"
    stream = client.get(
        "/analysis/updates?jobid=done", headers={"accept": "text/event-stream"}
    )
    assert "event: result-deleted" in stream.text
    assert "event: done" in stream.text

    history("pending")
    for path in ("/jobs", "/jobs/list"):
        rows = html.fromstring(client.get(path).text).xpath('//*[@id="job-list"]/ul/li')
        pending, deleted = rows
        assert "opacity-50" not in pending.attrib["class"].split()
        assert "opacity-50" in deleted.attrib["class"].split()
        assert (
            "The result was deleted to make room for other results."
            in deleted.text_content()
        )
        assert not deleted.xpath(".//a")


@pytest.mark.parametrize("sidecar", ["diagnostics", "event archive"])
def test_deleting_sidecar_fades_the_history_entry(client, history, sidecar):
    if sidecar == "diagnostics":
        info = history(
            "partial",
            state="failed",
            failure={"status": "failed", "stage": "apitof", "run_id": 1},
        )
    else:
        info = history(
            "partial",
            state="done",
            result={"experiment": 1, "cluster": 18},
            counts={"completed": 3, "requested": 3},
        )
    result_path = Path(info["result_db_path"])
    paths = touch_result(result_path)
    sidecar_path = (
        failure_path(result_path) if sidecar == "diagnostics" else paths.parquet
    )
    sidecar_path.touch()
    assert "opacity-50" not in client.get("/jobs/list").text
    sidecar_path.unlink()
    response = client.get("/jobs/list")
    assert "opacity-50" in response.text
    assert (
        "Some result files were deleted to make room for other results."
        in response.text
    )
    assert "jobid=partial" in response.text


def test_result_deleted_while_opening_plot(client, history, tmp_path, monkeypatch):
    import duckdb

    from vms import db

    info = history("done", state="done", result={"experiment": 1, "cluster": 18})
    result_path = Path(info["result_db_path"])
    cluster_path = tmp_path / "clusters.apitofsim"
    clusters = DatabasePaths(cluster_path)
    paths = DatabasePaths(result_path)
    for bundle in (clusters, paths):
        bundle.create()
        duckdb.connect(bundle.duckdb).close()

    connect = duckdb.connect

    def connect_after_deletion():
        paths.duckdb.unlink()
        return connect()

    monkeypatch.setattr("vms.app.database_path", cluster_path)
    monkeypatch.setattr(db.duckdb, "connect", connect_after_deletion)
    response = client.get("/analysis/results/done/spectrogram", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/jobs"
    assert not paths.duckdb.exists()


def test_browser_redirects_when_result_is_deleted_during_page_load(
    page, client, history
):
    from urllib.parse import urlsplit

    from playwright.sync_api import expect

    info = history("done", state="done", result={"experiment": 1, "cluster": 18})
    result_path = Path(info["result_db_path"])
    paths = touch_result(result_path)
    static = Path(__file__).resolve().parents[1] / "src/vms/static"

    def serve(route):
        url = urlsplit(route.request.url)
        if url.path.startswith("/static/"):
            route.fulfill(path=str(static / url.path.removeprefix("/static/")))
            return
        if url.path == "/analysis/updates":
            paths.duckdb.unlink(missing_ok=True)
        response = client.get(
            url.path + ("?" + url.query if url.query else ""),
            headers=route.request.headers,
        )
        route.fulfill(
            body=response.content, content_type=response.headers["content-type"]
        )

    page.route("http://testserver/**", serve)
    page.goto("http://testserver/analysis?jobid=done")
    expect(page).to_have_url("http://testserver/jobs")
    row = page.locator("#job-list li")
    expect(row).to_have_css("opacity", "0.5")
    expect(row).to_contain_text(
        "The result was deleted to make room for other results."
    )


@pytest.mark.parametrize("live_update", [False, True])
def test_stopped_early_notice_and_heading(page, client, history, live_update):
    from lxml import html
    from playwright.sync_api import expect

    from vms.resources import templates

    info = history(
        "partial",
        state="running" if live_update else "done",
        **({} if live_update else {"cancelled_stage": "apitof"}),
        counts={"requested": 100, "completed": 3},
    )
    if not live_update:
        touch_result(Path(info["result_db_path"]))
    document = html.fromstring(client.get("/analysis?jobid=partial").text)
    body = document.xpath('//div[@class="tab-page"]')[0]
    pane = body.xpath('.//div[@sse-swap="apitof"]')[0]
    pane.append(
        html.fromstring(
            templates.env.get_template("analysis/apitof.html").render(
                iterations=3,
                realizations=100,
                fragmented=1,
                survived=2,
                ratio=2 / 3,
            )
        )
    )
    page.set_content(html.tostring(body, encoding="unicode"))
    page.evaluate("window.location.hash = 'apitof'")
    page.add_script_tag(
        path=str(Path(__file__).resolve().parents[1] / "src/vms/static/index.js")
    )
    if live_update:
        expect(page.locator("#status")).to_have_text("Running simulation...")
        info.update(status="done", cancelled_stage="apitof")
        state = templates.env.get_template("analysis/state.html").render(info=info)
        page.locator('[sse-swap="state"]').evaluate(
            "(el, state) => el.innerHTML = state", state
        )
    notice = page.get_by_role("heading", name="Finished — stopped early")
    expect(notice).to_be_visible()
    expect(page.locator("#status")).to_have_text("Simulation stopped early")
    page.get_by_role("button", name="Dismiss stopped early notice").click()
    expect(notice).to_be_hidden()
    expect(page.locator("#status")).to_be_visible()
    expect(page.locator("#status")).to_have_text("Simulation stopped early")


def test_preparation_handoff_refuses_simulation_after_cancel():
    # Instantiate the actor implementation locally to test the atomic protocol.
    cls = CancellationControl.__ray_metadata__.modified_class
    control = cls()
    control.begin("first")
    assert control.enter("first", "skimmer")
    assert control.request_cancel("first") == "skimmer"
    assert not control.enter("first", "apitof")
    assert control.requested("first")

    control.begin("second")
    assert control.requested("first")
    assert not control.enter("first", "apitof")
    with pytest.raises(ValueError, match="another job"):
        control.request_cancel("first")
    assert not control.requested("second")
    assert control.request_cancel("second") == "queue"


def test_enter_cancelled_stage(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("vms.jobs.cancellation.ray.get", lambda value: value)
    control = SimpleNamespace(enter=SimpleNamespace(remote=lambda jobid, stage: False))
    with pytest.raises(JobCancelled):
        enter_stage(control, "job", "apitof")


def test_cancellation_wakes_blocked_iterator(monkeypatch):
    from threading import Event
    from types import SimpleNamespace

    from vms.jobs.cancellation import cancel_iterator

    cancelled = Event()
    monkeypatch.setattr("vms.jobs.cancellation.ray.get", lambda value: value)
    control = SimpleNamespace(requested=SimpleNamespace(remote=lambda jobid: True))
    iterator = SimpleNamespace(request_cancel=cancelled.set)
    with cancel_iterator(control, "job", iterator):
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
