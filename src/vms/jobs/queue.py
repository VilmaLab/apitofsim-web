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

    waiting = [(jobid, info) for jobid, info in waiting if info["status"] == "pending"]
    for ahead, (_, info) in enumerate(waiting):
        info["jobs_ahead"] = ahead
        info["last_update"][b"queue"] = templates.env.get_template(
            "analysis/queue.html"
        ).render(jobs_ahead=ahead)
        notify_job(info)


def mark_running(info):
    from vms.resources import templates

    if info["status"] != "pending":
        return False
    info["status"] = "running"
    info.pop("jobs_ahead", None)
    info["last_update"][b"queue"] = templates.env.get_template(
        "analysis/queue.html"
    ).render(queue="Your simulation is starting.")
    notify_job(info)
    return True


def consume_event(info, event, data):
    from .failures import apply_failure

    if event == b"progress":
        info["counts"] = json.loads(data)
    elif event == b"cancelled":
        cancellation = json.loads(data)
        info["cancelled_stage"] = cancellation["stage"]
        info["counts"] = cancellation.get("counts", {})
    elif event == b"stage":
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
    from .cancellation import finish_cancelled, publish_state

    if "failure" not in info and "cancelled_stage" in info:
        try:
            finish_cancelled(info)
        except Exception as exc:
            fail_job(info, exc)
    elif "failure" not in info:
        if "result" in info:
            info["status"] = "done"
        else:
            fail_job(
                info, RuntimeError("Simulation stream ended without a final result")
            )
    publish_state(info)
    notify_job(info)


def start_job(info):
    from vms.jobs.run import run_simulation
    from vms.settings import database_path

    from .cancellation import CancellationControl

    info["cancel_control"] = CancellationControl.remote()
    return run_simulation.remote(
        cancel_control=info["cancel_control"],
        **info["arguments"],
        database_path=database_path,
        result_db_path=info["result_db_path"],
        start_time=info["start_time"],
    )


def mark_cancelling(info, stage):
    info["cancel_signalled"] = True
    info["cancelled_stage"] = stage
    notify_job(info)


def expected_cancellation(info, exc):
    return info.get("cancel_signalled") and isinstance(
        exc, ray.exceptions.TaskCancelledError
    )


def dispose_control(info):
    control = info.pop("cancel_control", None)
    if control is not None:
        ray.kill(control)


def request_cancellation(info):
    from .cancellation import publish_state

    if info["status"] not in ("pending", "running") or info.get("cancel_requested"):
        return
    info["cancel_requested"] = True
    if info["status"] == "pending":
        info["cancelled_stage"] = "queue"
        finish_job(info)
        update_queue(
            [
                (jobid, job)
                for jobid, job in _job_status.items()
                if job["status"] == "pending"
            ]
        )
    else:
        publish_state(info)
    notify_job(info)


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
            remaining = deque(
                (jobid, job) for jobid, job in waiting if job["status"] == "pending"
            )
            if len(remaining) != len(waiting):
                waiting = remaining
                anyio.from_thread.run_sync(update_queue, waiting)
            if active is None and not waiting:
                continue
            if active is None:
                _, info = waiting.popleft()
                if not anyio.from_thread.run_sync(mark_running, info):
                    continue
                anyio.from_thread.run_sync(update_queue, waiting)
                try:
                    active = start_job(info)
                    completion = active.completed()
                except Exception as exc:
                    anyio.from_thread.run_sync(fail_job, info, exc)
                    dispose_control(info)
                    continue
                exhausted = completed = False

            if info.get("cancel_requested") and not info.get("cancel_signalled"):
                try:
                    stage = ray.get(info["cancel_control"].request_cancel.remote())
                except Exception as exc:
                    anyio.from_thread.run_sync(fail_job, info, exc)
                    ray.cancel(active)
                else:
                    anyio.from_thread.run_sync(mark_cancelling, info, stage)
                    if stage != "apitof":
                        ray.cancel(active)

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
                    if not expected_cancellation(info, exc):
                        anyio.from_thread.run_sync(fail_job, info, exc)
                    exhausted = True
                    ray.cancel(active)
            if completion in ready:
                try:
                    ray.get(completion)
                except Exception as exc:
                    if not expected_cancellation(info, exc):
                        anyio.from_thread.run_sync(fail_job, info, exc)
                completed = True
            # Final events can precede task exit, and completion can precede
            # draining buffered events. Neither alone releases the execution slot.
            if exhausted and completed:
                anyio.from_thread.run_sync(finish_job, info)
                dispose_control(info)
                active = completion = None
    except anyio.EndOfStream:
        return
    finally:
        if active is not None:
            ray.cancel(active)
        if info is not None:
            dispose_control(info)


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
