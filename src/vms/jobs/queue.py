"""One lifespan-owned Ray consumer, independent of connected browsers."""

import json
from collections import deque
from contextlib import asynccontextmanager
from math import inf

import anyio
import ray

_job_status = {}


def get_status_dict():
    return _job_status


def notify_job(info):
    changed = info.get("changed")
    info["changed"] = anyio.Event()
    if changed is not None:
        changed.set()


def update_queue(waiting):
    from vms.resources import templates

    for ahead, (_, info) in enumerate(waiting):
        info["jobs_ahead"] = ahead
        info["last_update"][b"queue"] = templates.env.get_template(
            "analysis/queue.html"
        ).render(jobs_ahead=ahead)
        notify_job(info)


def mark_running(info):
    from vms.resources import templates

    info["status"] = "running"
    info.pop("jobs_ahead", None)
    info["last_update"][b"queue"] = templates.env.get_template(
        "analysis/queue.html"
    ).render(queue="Your simulation is starting.")
    notify_job(info)


def consume_event(info, event, data):
    from .failures import apply_failure

    if event == b"stage":
        info["stage"] = data
    elif event == b"failure":
        apply_failure(info, json.loads(data))
    elif event == b"result":
        info["result"] = json.loads(data)
    else:
        info["last_update"][event] = data
    notify_job(info)


def fail_job(info, exc):
    from .failures import apply_failure, describe_failure, save_failure

    if "failure" not in info:
        failure = describe_failure(exc, info, info["result_db_path"])
        save_failure(info["result_db_path"], failure)
        apply_failure(info, failure)
    notify_job(info)


def finish_job(info):
    if "failure" not in info:
        if "result" in info:
            info["status"] = "done"
        else:
            fail_job(
                info, RuntimeError("Simulation stream ended without a final result")
            )
    notify_job(info)


def start_job(info):
    from vms.jobs.run import run_simulation
    from vms.settings import database_path

    return run_simulation.remote(
        **info["arguments"],
        database_path=database_path,
        result_db_path=info["result_db_path"],
        start_time=info["start_time"],
    )


def pump_jobs(receiver):
    """Own all Ray generator access in this AnyIO worker thread."""
    waiting = deque()
    active = None
    completion = None
    exhausted = completed = False
    info = None
    try:
        while True:
            anyio.from_thread.check_cancelled()
            if active is None and not waiting:
                waiting.append(anyio.from_thread.run(receiver.receive))
                anyio.from_thread.run_sync(update_queue, waiting)
            # Only the event loop accesses the AnyIO stream and shared job state.
            while True:
                try:
                    waiting.append(anyio.from_thread.run_sync(receiver.receive_nowait))
                except anyio.WouldBlock:
                    break
                anyio.from_thread.run_sync(update_queue, waiting)
            if active is None:
                _, info = waiting.popleft()
                anyio.from_thread.run_sync(mark_running, info)
                anyio.from_thread.run_sync(update_queue, waiting)
                try:
                    active = start_job(info)
                    completion = active.completed()
                except Exception as exc:
                    anyio.from_thread.run_sync(fail_job, info, exc)
                    continue
                exhausted = completed = False

            waitables = ([] if exhausted else [active]) + (
                [] if completed else [completion]
            )
            ready, _ = ray.wait(waitables, timeout=0.1)
            if active in ready:
                try:
                    event, data = ray.get(next(active))
                    anyio.from_thread.run_sync(consume_event, info, event, data)
                except StopIteration:
                    exhausted = True
                except Exception as exc:
                    anyio.from_thread.run_sync(fail_job, info, exc)
                    exhausted = True
                    ray.cancel(active)
            if completion in ready:
                try:
                    ray.get(completion)
                except Exception as exc:
                    anyio.from_thread.run_sync(fail_job, info, exc)
                completed = True
            # Final events can precede task exit, and completion can precede
            # draining buffered events. Neither alone releases the execution slot.
            if exhausted and completed:
                anyio.from_thread.run_sync(finish_job, info)
                active = completion = None
    except anyio.EndOfStream:
        return
    finally:
        if active is not None:
            ray.cancel(active)


@asynccontextmanager
async def scheduler_context(app):
    sender, receiver = anyio.create_memory_object_stream(inf)
    async with sender, receiver, anyio.create_task_group() as tasks:
        app.state.job_sender = sender
        tasks.start_soon(anyio.to_thread.run_sync, pump_jobs, receiver)
        try:
            yield
        finally:
            await sender.aclose()
            tasks.cancel_scope.cancel()
