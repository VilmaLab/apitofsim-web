import json
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import partial, wraps
from time import time
from types import SimpleNamespace

import anyio
import pytest
from apitofsim.workflow import DatabasePaths
from starlette.requests import Request

from vms.jobs import queue
from vms.jobs.cancellation import CancellationControl
from vms.jobs.run import update_analysis


def run_async(test):
    @wraps(test)
    def run(*args, **kwargs):
        # Playwright's session-scoped sync API owns the main thread's event loop.
        with ThreadPoolExecutor() as executor:
            return executor.submit(anyio.run, partial(test, *args, **kwargs)).result()

    return run


class Generator:
    def __init__(self, events, gate=None, completion_gate=None):
        self.events = deque(events)
        self.gate = gate
        self.completion_gate = completion_gate
        self.completion = object()
        self.exhausted = False
        self.cancelled = False

    def completed(self):
        return self.completion

    def __next__(self):
        if self.events:
            return self.events.popleft()
        self.exhausted = True
        raise StopIteration


@pytest.fixture
def scheduler(monkeypatch, tmp_path):
    queue.get_status_dict().clear()
    started = []
    generators = []
    controls = []
    killed = []

    def create_control():
        impl = CancellationControl.__ray_metadata__.modified_class()
        control = SimpleNamespace(
            **{
                name: SimpleNamespace(remote=getattr(impl, name))
                for name in ("begin", "enter", "request_cancel", "requested")
            }
        )
        controls.append(control)
        return control

    def start(info, jobid, control):
        # No later task may start until both completion and exhaustion.
        if generators:
            previous = generators[-1]
            assert previous.exhausted or previous.cancelled
            assert previous.completion_gate is None or previous.completion_gate.is_set()
        started.append(info["id"])
        if info.get("submission_error"):
            raise RuntimeError("submission failed")
        if stage := info.get("cancel_stage"):
            assert control.enter.remote(jobid, stage)
        generator = info["generator"]
        generators.append(generator)
        return generator

    def wait(waitables, timeout):
        ready = []
        for ref in waitables:
            if isinstance(ref, Generator):
                if ref.gate is None or ref.gate.is_set():
                    ready.append(ref)
            else:
                generator = next(g for g in generators if g.completion is ref)
                if (
                    generator.completion_gate is None
                    or generator.completion_gate.is_set()
                ):
                    ready.append(ref)
        if not ready:
            threading.Event().wait(min(timeout, 0.005))
        return ready, [ref for ref in waitables if ref not in ready]

    def get(ref):
        if isinstance(ref, Exception):
            raise ref
        return ref

    monkeypatch.setattr(CancellationControl, "remote", create_control)
    monkeypatch.setattr(queue, "start_job", start)
    monkeypatch.setattr(queue.ray, "kill", killed.append)
    monkeypatch.setattr(queue.ray, "wait", wait)
    monkeypatch.setattr(queue.ray, "get", get)
    monkeypatch.setattr(
        queue.ray, "cancel", lambda gen: setattr(gen, "cancelled", True)
    )

    async def submit(app, name, events=None, **kwargs):
        info = {
            "id": name,
            "status": "pending",
            "last_update": {},
            "result_db_path": str(tmp_path / f"{name}.apitofsim"),
            "start_time": time(),
            "generator": Generator(
                events
                if events is not None
                else [(b"result", json.dumps({"experiment": 1}))],
                kwargs.pop("gate", None),
                kwargs.pop("completion_gate", None),
            ),
            **kwargs,
        }
        queue.get_status_dict()[name] = info
        await app.state.job_sender.send((name, info))
        return info

    yield SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace()),
        submit=submit,
        started=started,
        controls=controls,
        killed=killed,
    )
    queue.get_status_dict().clear()


async def until(predicate):
    with anyio.fail_after(5):
        while not predicate():
            await anyio.sleep(0.005)


@run_async
async def test_fifo_positions_and_completion_without_viewers(scheduler):
    gate = threading.Event()
    completion = threading.Event()
    second_gate = threading.Event()
    async with queue.scheduler_context(scheduler.app):
        first = await scheduler.submit(
            scheduler.app, "first", gate=gate, completion_gate=completion
        )
        await until(lambda: first["status"] == "running")
        second = await scheduler.submit(scheduler.app, "second", gate=second_gate)
        third = await scheduler.submit(scheduler.app, "third")
        fourth = await scheduler.submit(scheduler.app, "fourth")
        await until(lambda: fourth.get("jobs_ahead") == 2)
        assert second["jobs_ahead"] == 0
        assert third["jobs_ahead"] == 1
        assert "front of the queue" in second["last_update"][b"queue"]
        assert "1 job ahead" in third["last_update"][b"queue"]
        assert "2 jobs ahead" in fourth["last_update"][b"queue"]
        gate.set()
        await until(lambda: first["generator"].exhausted)
        assert scheduler.started == ["first"]
        completion.set()
        await until(lambda: fourth.get("jobs_ahead") == 1)
        assert third["jobs_ahead"] == 0
        assert "front of the queue" in third["last_update"][b"queue"]
        assert "1 job ahead" in fourth["last_update"][b"queue"]
        second_gate.set()
        await until(lambda: fourth["status"] == "done")
        assert scheduler.started == ["first", "second", "third", "fourth"]


@run_async
async def test_completion_before_buffered_output_is_drained(scheduler):
    gate = threading.Event()
    async with queue.scheduler_context(scheduler.app):
        first = await scheduler.submit(scheduler.app, "first", gate=gate)
        second = await scheduler.submit(scheduler.app, "second")
        await until(
            lambda: second.get("jobs_ahead") == 0 and scheduler.started == ["first"]
        )
        assert scheduler.started == ["first"]
        assert first["status"] == "running"
        gate.set()
        await until(lambda: second["status"] == "done")
        assert first["result"] == {"experiment": 1}


@run_async
@pytest.mark.parametrize("failure", ["submission", "worker", "missing", "reported"])
async def test_failure_advances_queue(scheduler, failure):
    failure_event = {
        "status": "incomplete",
        "stage": "apitof",
        "message": "partial",
        "result": {"experiment": 1},
    }
    events = {
        "worker": [(b"stage", "apitof"), RuntimeError("worker died")],
        "missing": [],
        "reported": [(b"failure", json.dumps(failure_event))],
    }.get(failure)
    completion = threading.Event() if failure in ("worker", "reported") else None
    async with queue.scheduler_context(scheduler.app):
        first = await scheduler.submit(
            scheduler.app,
            "first",
            events,
            submission_error=failure == "submission",
            completion_gate=completion,
        )
        second = await scheduler.submit(scheduler.app, "second")
        if completion is not None:
            await until(lambda: "failure" in first)
            assert scheduler.started == ["first"]
            completion.set()
        await until(lambda: second["status"] == "done")
        assert first["status"] == ("incomplete" if failure == "reported" else "failed")
        assert scheduler.started == ["first", "second"]
        if failure == "worker":
            assert first["failure"]["stage"] == "apitof"


def request(jobid):
    return Request(
        {
            "type": "http",
            "query_string": f"jobid={jobid}".encode(),
            "headers": [(b"accept", b"text/event-stream")],
        }
    )


async def read(jobid):
    response = await update_analysis(request(jobid))
    return b"".join([chunk async for chunk in response.body_iterator]).decode()


@run_async
async def test_multiple_viewers_disconnect_and_replay(scheduler):
    gate = threading.Event()
    received = []

    async def viewer():
        received.append(await read("second"))

    async with queue.scheduler_context(scheduler.app):
        await scheduler.submit(scheduler.app, "first", gate=gate)
        second = await scheduler.submit(scheduler.app, "second")
        output = DatabasePaths(second["result_db_path"])
        output.create()
        output.duckdb.touch()
        await until(lambda: second.get("jobs_ahead") == 0)
        disconnected = (await update_analysis(request("second"))).body_iterator
        assert await anext(disconnected)
        await disconnected.aclose()
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(viewer)
            tasks.start_soon(viewer)
            await anyio.sleep(0.02)
            gate.set()
        assert len(received) == 2
        assert all(
            "front of the queue" in stream and "event: done" in stream
            for stream in received
        )
        assert "event: done" in await read("second")
        assert second["status"] == "done"


@run_async
async def test_notification_during_snapshot_yield(scheduler, tmp_path):
    result_path = tmp_path / "job.apitofsim"
    paths = DatabasePaths(result_path)
    paths.create()
    paths.duckdb.touch()
    info = {
        "status": "pending",
        "last_update": {b"queue": "old"},
        "result_db_path": str(result_path),
    }
    queue.get_status_dict()["job"] = info
    iterator = (await update_analysis(request("job"))).body_iterator
    assert await anext(iterator) == b"data: "
    info["last_update"][b"queue"] = "new"
    info["status"] = "done"
    queue.notify_job(info)
    with anyio.fail_after(1):
        rest = b"".join([chunk async for chunk in iterator]).decode()
    assert "new" in rest and "event: done" in rest


@run_async
@pytest.mark.parametrize("active", [False, True])
async def test_shutdown_stops_idle_or_active_worker(scheduler, active):
    info = None
    with anyio.fail_after(2):
        async with queue.scheduler_context(scheduler.app):
            if active:
                info = await scheduler.submit(
                    scheduler.app,
                    "job",
                    gate=threading.Event(),
                    completion_gate=threading.Event(),
                )
                await until(lambda: scheduler.started == ["job"])
            else:
                await anyio.sleep(0.02)
    assert scheduler.app.state.job_sender._closed
    if active:
        assert info["generator"].cancelled


@run_async
async def test_cancel_queued_removes_files_and_updates_positions(
    scheduler, monkeypatch
):
    gate = threading.Event()
    async with queue.scheduler_context(scheduler.app):
        first = await scheduler.submit(scheduler.app, "first", gate=gate)
        await until(lambda: first["status"] == "running")
        second = await scheduler.submit(scheduler.app, "second")
        third = await scheduler.submit(scheduler.app, "third")
        await until(lambda: third.get("jobs_ahead") == 1)
        output = DatabasePaths(second["result_db_path"])
        output.create()
        for member in ("database.duckdb", "database.duckdb.wal", "failure.json"):
            (output.root / member).touch()
        shared = output.root.parent / "shared.apitofsim"
        shared.mkdir()
        queue.request_cancellation(second)
        assert second["status"] == "cancelled"
        assert not output.root.exists()
        assert shared.is_dir()
        assert third["jobs_ahead"] == 0
        assert "cancel_control" not in second
        gate.set()
        await until(lambda: third["status"] == "done")
        assert scheduler.started == ["first", "third"]
        assert 'data-status="cancelled"' in second["last_update"][b"tabs"]


@run_async
@pytest.mark.parametrize("stage", ["skimmer", "apitof"])
async def test_cancel_running_waits_for_exit_and_drains(scheduler, stage):
    gate = threading.Event()
    completion = threading.Event()
    async with queue.scheduler_context(scheduler.app):
        events = [(b"progress", json.dumps({"requested": 10, "completed": 3}))]
        if stage == "apitof":
            events.append((b"result", json.dumps({"experiment": 1, "cluster": 18})))
        else:
            events.append(queue.ray.exceptions.TaskCancelledError())
        first = await scheduler.submit(
            scheduler.app,
            "first",
            events=events,
            gate=gate,
            completion_gate=completion,
            cancel_stage=stage,
        )
        await until(lambda: first["status"] == "running")
        second = await scheduler.submit(scheduler.app, "second")
        queue.request_cancellation(first)
        queue.request_cancellation(first)
        await until(lambda: first.get("cancel_signalled"))
        assert first["generator"].cancelled == (stage == "skimmer")
        gate.set()
        await until(
            lambda: first["generator"].exhausted or first["generator"].cancelled
        )
        assert scheduler.started == ["first"]
        assert first["status"] == "running"
        completion.set()
        await until(lambda: second["status"] == "done")
        assert first["status"] == ("done" if stage == "apitof" else "cancelled")
        assert "failure" not in first
        assert len(scheduler.controls) == 1
        assert not scheduler.controls[0].requested.remote("second")
        assert not scheduler.killed
        assert (
            f'data-tab="{stage}" data-status="cancelled"'
            in first["last_update"][b"tabs"]
        )
        assert ('href="#explorer"' in first["last_update"][b"tabs"]) == (
            stage == "apitof"
        )

    assert scheduler.killed == scheduler.controls


@run_async
async def test_scheduler_replaces_failed_cancellation_actor(scheduler):
    async with queue.scheduler_context(scheduler.app):
        await until(lambda: len(scheduler.controls) == 1)
        failed = scheduler.controls[0]
        failed.begin.remote = lambda jobid: queue.ray.exceptions.ActorDiedError()
        job = await scheduler.submit(scheduler.app, "job")
        await until(lambda: job["status"] == "done")
        assert len(scheduler.controls) == 2
        assert scheduler.killed == [failed]
    assert scheduler.killed == scheduler.controls
