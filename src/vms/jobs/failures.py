"""Durable diagnostics for worker errors and incomplete simulation output."""

import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import sentry_sdk
from apitofsim.workflow import DatabasePaths
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


class IncompleteOutputError(RuntimeError):
    pass


def failure_path(result_db_path):
    return DatabasePaths(result_db_path).root / "failure.json"


def result_db_missing(info):
    failure = info.get("failure", {})
    expected = (
        "result" in info
        or info["status"] == "done"
        or failure.get("had_result_db")
        or failure.get("run_id") is not None
    )
    return expected and not DatabasePaths(info["result_db_path"]).duckdb.is_file()


def result_sidecar_missing(info):
    path = info["result_db_path"]
    if "result" in info and info.get("counts", {}).get("completed", 0) > 0:
        if not DatabasePaths(path).parquet.is_file():
            return True
    failure = info.get("failure")
    return (
        failure is not None
        and "persistence_error" not in failure
        and not failure_path(path).is_file()
    )


def describe_failure(exc, context, result_db_path):
    failure = {
        "status": "incomplete" if isinstance(exc, IncompleteOutputError) else "failed",
        "job_id": Path(result_db_path).stem,
        "stage": context.get("stage", "queue"),
        "time": datetime.now(timezone.utc).isoformat(),
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(exc)),
        "run_id": context.get("run_id"),
        "had_result_db": "db" in context
        or DatabasePaths(result_db_path).duckdb.is_file(),
        "cluster_id": context.get("cluster_id"),
        "counts": context.get("counts", {}),
    }
    if failure["status"] == "incomplete" and "result" in context:
        failure["result"] = context["result"]
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("job_id", failure["job_id"])
        scope.set_tag("job_stage", failure["stage"])
        logger.error(
            "Job %s failed during %s",
            failure["job_id"],
            failure["stage"],
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    return failure


def save_failure(result_db_path, failure):
    """Keep diagnostics even if DuckDB is unavailable or the worker has died."""
    path = failure_path(result_db_path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(failure, output, indent=2)
        temporary.replace(path)
    except Exception as exc:
        logger.exception("Could not persist diagnostics for job %s", failure["job_id"])
        failure["persistence_error"] = str(exc)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_output(db, counters, requested, context, *, cancelled=False):
    counts = {
        "requested": int(requested),
        "completed": int(counters.n_realizations),
        "escapes": int(counters.n_escaped_total),
        "fragmentations": int(counters.n_fragmented_total.sum()),
        "collisions": int(counters.ncoll_total),
    }
    # Each web job owns its result database. Database scope also includes
    # unlinked realizations, which must remain visible when output is incomplete.
    recorded = db.recorded_output_query(run_id=None, parent=None, result_id=None)
    diagnostics = dict(zip(recorded.columns, recorded.fetchone(), strict=True))
    for name, column in (
        ("recorded_realizations", "realization_count"),
        ("recorded_escapes", "escape_count"),
        ("recorded_fragmentations", "fragmentation_subtype_count"),
        ("recorded_collisions", "collision_subtype_count"),
        ("terminal_realizations", "terminal_realization_count"),
    ):
        counts[name] = diagnostics[column]
    context["counts"] = counts
    checks = (
        ("completed", "requested"),
        ("recorded_realizations", "completed"),
        ("terminal_realizations", "completed"),
        ("recorded_escapes", "escapes"),
        ("recorded_fragmentations", "fragmentations"),
        ("recorded_collisions", "collisions"),
    )
    if cancelled:
        # Interrupted realizations retain their events but do not contribute to
        # final counters. Only terminal records must match completed counters.
        checks = (
            ("terminal_realizations", "completed"),
            ("recorded_escapes", "escapes"),
            ("recorded_fragmentations", "fragmentations"),
        )
    mismatches = [
        f"{recorded.replace('_', ' ')}: {counts[recorded]}/{counts[expected]}"
        for recorded, expected in checks
        if counts[recorded] != counts[expected]
    ]
    if mismatches:
        raise IncompleteOutputError("Output is incomplete; " + "; ".join(mismatches))


def process_jobid(request, *, jobid=None):
    from time import time

    from vms.jobs.queue import get_status_dict
    from vms.settings import is_job_id, job_result_path

    status = get_status_dict()
    if jobid is None:
        jobid = request.query_params.get("jobid")
    if jobid is None:
        raise HTTPException(400, detail="Missing jobid parameter")
    if jobid not in status:  # type: ignore
        # Only accept the UUID hex used for result filenames.
        if not is_job_id(jobid):
            raise HTTPException(404, detail="Job ID not found")
        result_db_path = job_result_path(jobid)
        try:
            failure = json.loads(failure_path(result_db_path).read_text())
        except FileNotFoundError, ValueError:
            raise HTTPException(404, detail="Job ID not found") from None
        info = {
            "result_db_path": str(result_db_path),
            "last_update": {},
            "start_time": time(),
        }
        apply_failure(info, failure)
        status[jobid] = info
    return jobid


def failure_statuses(failure):
    stages = ["queue", "skimmer", "apitof"]
    stage = failure["stage"]
    index = stages.index(stage) if stage in stages else 2
    return {
        name: ("done" if i < index else failure["status"] if i == index else "pending")
        for i, name in enumerate(stages)
    }


def apply_failure(info, failure):
    from vms.resources import templates

    info["status"] = failure["status"]
    info["failure"] = failure
    if "result" in failure:
        info["result"] = failure["result"]
    else:
        info.pop("result", None)
    info["last_update"][b"failure"] = templates.env.get_template(
        "analysis/failure.html"
    ).render(failure=failure)
    info["last_update"][b"tabs"] = templates.env.get_template(
        "analysis/tabs.html"
    ).render(
        statuses=failure_statuses(failure),
        completed="result" in info,
    )

    from .cancellation import publish_state

    publish_state(info)
