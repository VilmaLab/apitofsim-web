import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import duckdb
import numpy as np
import pytest
from apitofsim.api import Counters
from playwright.sync_api import expect
from starlette.requests import Request

from vms import settings
from vms.app import analysis, app
from vms.jobs import run as job_runner
from vms.jobs.failures import IncompleteOutputError, failure_path, validate_output
from vms.jobs.queue import consume_event, fail_job, finish_job, get_status_dict
from vms.jobs.run import setup_result_db, simulation_events, update_analysis
from vms.resources import templates

JOB_ID = "a" * 32
VMS_DIR = Path(__file__).resolve().parents[1] / "src/vms"


@pytest.fixture
def job(monkeypatch, tmp_path):
    result_path = tmp_path / f"{JOB_ID}.duckdb"
    monkeypatch.setattr(settings, "results_path", str(tmp_path / "results.duckdb"))
    # The app and the job queue share one status table.
    status = get_status_dict()
    status.clear()
    info = {"status": "running", "result_db_path": str(result_path), "last_update": {}}
    status[JOB_ID] = info
    yield result_path, info
    status.clear()


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
            "app": app,
            "router": app.router,
            "session": {},
        }
    )


def read_stream():
    async def read():
        info = get_status_dict().get(JOB_ID)
        if info is not None and "aiter" in info:
            # The scheduler now owns output handling; SSE only replays its state.
            references = info.pop("aiter")
            try:
                async for reference in references:
                    event, data = await reference
                    consume_event(info, event, data)
            except Exception as exc:
                fail_job(info, exc)
            finish_job(info)
        response = await update_analysis(request())
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
        db = setup_result_db(str(test_db), str(result_path))
        context["db"] = db
        context["run_id"] = db.insert_run(db.insert_config("test", {}))
        context["stage"] = "skimmer"
        yield b"stage", "skimmer"
        raise ValueError("Injected preliminary failure")

    monkeypatch.setattr(job_runner, "_run_simulation", broken_run)
    events = list(simulation_events(result_db_path=str(result_path)))
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
    get_status_dict().clear()
    assert read_stream() == stream
    response = asyncio.run(analysis(request("/analysis")))
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
    get_status_dict().clear()
    assert read_stream() == stream


def populate_output(output_db, recorded=3):
    connection = output_db.db
    connection.execute("INSERT INTO realization VALUES (1, NULL), (2, NULL), (3, NULL)")
    connection.execute(
        "INSERT INTO event_info (id, realization_id, event_type, postime, velocity, "
        "omega, rot_energy, vibrational_energy, particle_index) "
        "SELECT id, realization_id, event_type::event_type, "
        "{'x': 0, 'y': 0, 'z': 0, 't': 0}, "
        "{'x': 0, 'y': 0, 'z': 0}, {'x': 0, 'y': 0, 'z': 0}, 0, 0, 0 "
        "FROM (VALUES (1, 1, 'init'), (2, 2, 'init'), (3, 3, 'init'), "
        "(4, 1, 'collision'), (5, 2, 'collision'), (6, 3, 'collision'), "
        "(7, 1, 'escape'), (8, 2, 'escape')) e(id, realization_id, event_type)"
    )
    if recorded == 3:
        connection.execute(
            "INSERT INTO event_info SELECT 9, 3, 'fragmentation', postime, velocity, "
            "omega, rot_energy, vibrational_energy, particle_index FROM event_info LIMIT 1"
        )
        connection.execute("INSERT INTO fragmentation_event VALUES (9, NULL)")
    connection.execute(
        "INSERT INTO collision_event VALUES (4, 0, 0, true), (5, 0, 0, true), (6, 0, 0, true)"
    )
    counters = Counters(0, 3, 0, 3, 2, np.array([1]))
    return counters


@pytest.mark.parametrize("recorded", [2, 3])
def test_validate_output_detects_missing_events(recorded, output_db):
    counters = populate_output(output_db, recorded)
    context = {}
    if recorded == 2:
        with pytest.raises(IncompleteOutputError, match="terminal realizations: 2/3"):
            validate_output(output_db, counters, 3, context)
    else:
        validate_output(output_db, counters, 3, context)
    assert context["counts"] == {
        "requested": 3,
        "completed": 3,
        "escapes": 2,
        "fragmentations": 1,
        "collisions": 3,
        "recorded_realizations": 3,
        "recorded_escapes": 2,
        "recorded_fragmentations": recorded - 2,
        "recorded_collisions": 3,
        "terminal_realizations": recorded,
    }


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ("collision_event", "recorded collisions: 2/3"),
        ("fragmentation_event", "recorded fragmentations: 0/1"),
    ],
)
def test_validate_output_detects_missing_subtypes(output_db, table, message):
    counters = populate_output(output_db)
    output_db.db.execute(
        f"DELETE FROM {table} WHERE id = (SELECT max(id) FROM {table})"
    )
    with pytest.raises(IncompleteOutputError, match=message):
        validate_output(output_db, counters, 3, {})


def test_validate_output_compares_requested_count(output_db):
    counters = populate_output(output_db)
    with pytest.raises(IncompleteOutputError, match="completed: 3/4"):
        validate_output(output_db, counters, 4, {})


def test_validate_output_uses_database_scope(output_db):
    counters = Counters(0, 0, 0, 1, 1, np.array([0]))
    config_id = output_db.insert_config("test", {})
    runs = [output_db.insert_run(config_id), output_db.insert_run(config_id)]
    results = [
        output_db.record_result(run, counters, cluster_id=18, pathway_ids=[1])
        for run in runs
    ]
    output_db.db.execute(
        "INSERT INTO realization VALUES (1, ?), (2, ?), (3, NULL)", results
    )
    # Context is for one run/result, but the web owns the whole database. Both
    # other-run and unlinked records must be counted rather than hidden.
    context = {"run_id": runs[0], "result": {"experiment": runs[0], "cluster": 18}}
    with pytest.raises(IncompleteOutputError, match="recorded realizations: 3/1"):
        validate_output(output_db, counters, 1, context)
    assert context["counts"]["recorded_realizations"] == 3
    assert output_db.recorded_output_query(run_id=runs[0]).fetchone()[0] == 1
    assert output_db.recorded_output_query(result_id=results[1]).fetchone()[0] == 1


def test_validate_empty_output(output_db):
    counters = Counters(0, 0, 0, 0, 0, np.array([0]))
    context = {}
    validate_output(output_db, counters, 0, context)
    assert set(context["counts"].values()) == {0}
    with pytest.raises(IncompleteOutputError, match="completed: 0/1"):
        validate_output(output_db, counters, 1, context)


@pytest.mark.parametrize("outcome", ["failed", "incomplete"])
def test_failure_visible_in_browser(page, job, outcome, tmp_path):
    result_path, info = job
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(error))
    # Playwright's sync API already owns an event loop in this thread.
    with ThreadPoolExecutor() as executor:
        response = executor.submit(asyncio.run, analysis(request("/analysis"))).result()
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
    pane = templates.env.get_template("analysis/apitof.html").render(
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
            asset = VMS_DIR / path.lstrip("/")
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
