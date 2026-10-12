"""Durable diagnostics for worker errors and incomplete simulation output."""

import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

logger = logging.getLogger(__name__)


class IncompleteOutputError(RuntimeError):
    pass


def failure_path(result_db_path):
    return Path(result_db_path).with_suffix(".failure.json")


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
        "cluster_id": context.get("cluster_id"),
        "counts": context.get("counts", {}),
    }
    if failure["status"] == "incomplete" and "result" in context:
        failure["result"] = context["result"]
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


def validate_output(db, counters, requested, context):
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
    mismatches = [
        f"{recorded.replace('_', ' ')}: {counts[recorded]}/{counts[expected]}"
        for recorded, expected in checks
        if counts[recorded] != counts[expected]
    ]
    if mismatches:
        raise IncompleteOutputError("Output is incomplete; " + "; ".join(mismatches))
