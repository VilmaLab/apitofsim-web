import asyncio
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urlparse

import duckdb
import numpy as np
import pytest
from apitofsim.api import Counters
from playwright.sync_api import expect
from starlette.requests import Request

from vms.job_failures import IncompleteOutputError, failure_path, validate_output

app_module = importlib.import_module("vms.app")
JOB_ID = "a" * 32


@pytest.fixture
def job(monkeypatch, tmp_path):
    result_path = tmp_path / f"{JOB_ID}.duckdb"
    monkeypatch.setattr(app_module, "results_path", str(tmp_path / "results.duckdb"))
    monkeypatch.setattr(app_module, "status", {})
    info = {"status": "running", "result_db_path": str(result_path), "last_update": {}}
    app_module.status[JOB_ID] = info
    return result_path, info


def request(path="/analysis/updates"):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "root_path": "",
            "query_string": f"jobid={JOB_ID}".encode(),
            "headers": [(b"accept", b"text/event-stream")],
            "server": ("testserver", 80),
            "app": app_module.app,
            "router": app_module.app.router,
            "session": {},
        }
    )


def read_stream():
    async def read():
        response = await app_module.update_analysis(request())
        return b"".join([chunk async for chunk in response.body_iterator]).decode()

    return asyncio.run(read())


class References:
    """Ray's generator yields awaitable object references, including failed ones."""

    def __init__(self, events):
        self.events = iter(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            event = next(self.events)
        except StopIteration:
            raise StopAsyncIteration from None

        async def resolve():
            if isinstance(event, Exception):
                raise event
            return event

        return resolve()


def test_worker_failure_saved_in_database_and_sidecar(monkeypatch, job, test_db):
    result_path, _ = job

    def broken_run(context, **arguments):
        db = app_module.setup_result_db(str(test_db), str(result_path))
        context["db"] = db
        context["run_id"] = db.insert_run(db.insert_config("test", {}))
        context["stage"] = "skimmer"
        yield b"stage", "skimmer"
        raise ValueError("Injected preliminary failure")

    monkeypatch.setattr(app_module, "_run_simulation", broken_run)
    events = list(app_module.simulation_events(result_db_path=str(result_path)))
    assert events[-1][0] == b"failure"
    failure = json.loads(events[-1][1])
    assert failure["stage"] == "skimmer"
    assert "ValueError: Injected preliminary failure" in failure["traceback"]
    assert json.loads(failure_path(result_path).read_text()) == failure
    with duckdb.connect(str(result_path), read_only=True) as connection:
        name, message = connection.execute(
            "SELECT exc_name, msg FROM experiment_failure"
        ).fetchone()
    assert name == "ValueError"
    assert json.loads(message)["run_id"] == failure["run_id"]


@pytest.mark.parametrize("error", [RuntimeError("Worker died <script>"), None])
def test_sse_failure_replays_and_survives_restart(job, error):
    result_path, info = job
    events = [(b"stage", "apitof")]
    if error:
        events.append(error)
    info["aiter"] = References(events)
    stream = read_stream()
    assert "event: failure" in stream
    assert "Simulation failed" in stream
    assert 'data-status="failed"' in stream
    assert stream.endswith("event: done\r\n\r\n")
    assert info["status"] == "failed"
    failure = json.loads(failure_path(result_path).read_text())
    assert failure["stage"] == "apitof"
    if error:
        assert "Worker died &lt;script&gt;" in stream
        assert "Worker died <script>" not in stream
    else:
        assert "without a final result" in failure["message"]
    assert read_stream() == stream

    # Simulate a server restart: restore the job from its durable diagnostics.
    app_module.status.clear()
    assert read_stream() == stream
    response = asyncio.run(app_module.analysis(request("/analysis")))
    assert b"Simulation failed" in response.body
    assert b'data-status="failed"' in response.body


def test_incomplete_output_keeps_partial_results(job):
    result_path, info = job
    failure = {
        "status": "incomplete",
        "stage": "apitof",
        "job_id": JOB_ID,
        "message": "terminal realizations: 294/1000",
        "time": "2026-10-06T10:00:00+00:00",
        "exception_type": "IncompleteOutputError",
        "traceback": "",
        "result": {"experiment": 1, "cluster": 18},
    }
    failure_path(result_path).write_text(json.dumps(failure))
    info["aiter"] = References([(b"failure", json.dumps(failure))])
    stream = read_stream()
    assert "Simulation output incomplete" in stream
    assert "Partial results" in stream
    assert 'data-status="incomplete"' in stream
    assert 'href="#explorer"' in stream
    assert info["result"] == failure["result"]
    app_module.status.clear()
    assert read_stream() == stream


@pytest.mark.parametrize("recorded", [2, 3])
def test_validate_output_detects_missing_events(recorded):
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE realization (id INTEGER)")
        connection.execute(
            "CREATE TYPE event_type AS ENUM ('init', 'collision', 'fragmentation', 'escape')"
        )
        connection.execute(
            "CREATE TABLE event_info (id INTEGER, realization_id INTEGER, event_type event_type)"
        )
        for table in ("fragmentation_event", "collision_event"):
            connection.execute(f"CREATE TABLE {table} (id INTEGER)")
        connection.execute("INSERT INTO realization VALUES (1), (2), (3)")
        connection.execute(
            "INSERT INTO event_info VALUES "
            "(1, 1, 'init'), (2, 2, 'init'), (3, 3, 'init'), "
            "(4, 1, 'collision'), (5, 2, 'collision'), (6, 3, 'collision'), "
            "(7, 1, 'escape'), (8, 2, 'escape')"
        )
        if recorded == 3:
            connection.execute("INSERT INTO event_info VALUES (9, 3, 'fragmentation')")
            connection.execute("INSERT INTO fragmentation_event VALUES (9)")
        connection.execute("INSERT INTO collision_event VALUES (4), (5), (6)")
        counters = Counters(0, 3, 0, 3, 2, np.array([1]))
        context = {}
        if recorded == 2:
            with pytest.raises(
                IncompleteOutputError, match="terminal realizations: 2/3"
            ):
                validate_output(SimpleNamespace(db=connection), counters, 3, context)
        else:
            validate_output(SimpleNamespace(db=connection), counters, 3, context)
        assert context["counts"]["terminal_realizations"] == recorded
        assert context["counts"]["recorded_escapes"] == 2
        assert context["counts"]["recorded_fragmentations"] == recorded - 2
        assert context["counts"]["recorded_collisions"] == 3


@pytest.mark.parametrize("outcome", ["failed", "incomplete"])
def test_failure_visible_in_browser(page, job, outcome, tmp_path):
    result_path, info = job
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(error))
    # Playwright's sync API already owns an event loop in this thread.
    with ThreadPoolExecutor() as executor:
        response = executor.submit(
            asyncio.run, app_module.analysis(request("/analysis"))
        ).result()
    failure = {
        "status": outcome,
        "stage": "apitof",
        "job_id": JOB_ID,
        "message": "Injected failure"
        if outcome == "failed"
        else "terminal realizations: 294/1000",
        "time": "2026-10-06T10:00:00+00:00",
        "exception_type": "RuntimeError",
        "traceback": "RuntimeError: test failure",
    }
    if outcome == "incomplete":
        failure["result"] = {"experiment": 1, "cluster": 18}
    pane = app_module.templates.env.get_template("analysis/apitof.html").render(
        iterations=296,
        realizations=1000,
        fragmented=79,
        survived=215,
        ratio=0.215,
        log=[],
    )
    info["aiter"] = References([(b"apitof", pane), (b"failure", json.dumps(failure))])
    with ThreadPoolExecutor() as executor:
        stream = executor.submit(read_stream).result()

    def serve(route):
        path = urlparse(route.request.url).path
        if path.startswith("/static/"):
            asset = app_module.Path(app_module.__file__).parent / path.lstrip("/")
            route.fulfill(path=str(asset))
        elif path == "/analysis/updates":
            route.fulfill(body=stream, content_type="text/event-stream")
        else:
            route.fulfill(body=response.body, content_type="text/html")

    page.route("http://testserver/**", serve)
    page.goto(f"http://testserver/analysis?jobid={JOB_ID}")
    alert = page.get_by_role("alert")
    expect(alert).to_be_visible()
    expect(alert).to_contain_text(failure["message"])
    expect(page.locator('li[data-tab="apitof"]')).to_have_attribute(
        "data-status", outcome
    )
    expect(page.locator("#status")).to_be_hidden()
    alert.get_by_text("Failure details").click()
    expect(alert).to_contain_text("RuntimeError: test failure")
    page.screenshot(path=str(tmp_path / f"{outcome}.png"))
    assert page_errors == []
