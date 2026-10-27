"""Cancellation control shared by the scheduler and an already-running worker."""

from contextlib import contextmanager
from pathlib import Path
from shutil import rmtree
from threading import Event, Thread

import ray


class JobCancelled(Exception):
    pass


@ray.remote(num_cpus=0)
class CancellationControl:
    def __init__(self):
        self.cancelled = False
        self.stage = "queue"

    def enter(self, stage):
        if self.cancelled:
            return False
        self.stage = stage
        return True

    def request_cancel(self):
        self.cancelled = True
        return self.stage

    def requested(self):
        return self.cancelled


def enter_stage(control, stage):
    if control is not None and not ray.get(control.enter.remote(stage)):
        raise JobCancelled


@contextmanager
def cancel_iterator(control, iterator):
    """Signal even when next(iterator) is blocked; keep draining on the caller."""
    stopped = Event()
    errors = []

    def watch():
        try:
            while not stopped.is_set():
                if ray.get(control.requested.remote()):
                    iterator.request_cancel()
                    return
                stopped.wait(0.1)
        except Exception as exc:
            errors.append(exc)
            iterator.request_cancel()

    thread = Thread(target=watch) if control is not None else None
    if thread is not None:
        thread.start()
    try:
        yield
    finally:
        stopped.set()
        if thread is not None:
            thread.join()
    if errors:
        raise errors[0]


def clean_job_files(info):
    path = Path(info["result_db_path"])
    # Explicit job-owned names only; never remove shared input/cache files.
    for owned in (path, Path(str(path) + ".wal"), path.with_suffix(".failure.json")):
        owned.unlink(missing_ok=True)
    temporary = Path(str(path) + ".tmp")
    if temporary.is_dir():
        rmtree(temporary)


def cancelled_statuses(info):
    stages = ("queue", "skimmer", "apitof")
    index = stages.index(info["cancelled_stage"])
    return {
        stage: "done" if i < index else "cancelled" if i == index else "pending"
        for i, stage in enumerate(stages)
    }


def publish_state(info):
    from vms.resources import templates

    info["last_update"][b"state"] = templates.env.get_template(
        "analysis/state.html"
    ).render(info=info)


def finish_cancelled(info):
    from vms.resources import templates

    if "result" in info:
        info["status"] = "done"
    else:
        clean_job_files(info)
        info["status"] = "cancelled"
    info["last_update"][b"tabs"] = templates.env.get_template(
        "analysis/tabs.html"
    ).render(statuses=cancelled_statuses(info), completed="result" in info)
    publish_state(info)
