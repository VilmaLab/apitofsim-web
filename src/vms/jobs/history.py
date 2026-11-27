"""Recover browseable results from the persisted job directories."""

import json
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

import duckdb
from apitofsim.workflow import DatabasePaths

from vms.settings import is_job_id

from .failures import apply_failure, failure_path

logger = logging.getLogger(__name__)

SAVED_FIELDS = (
    "status",
    "result",
    "owner",
    "cluster_label",
    "submitted_at",
    "start_time",
    "counts",
    "cancelled_stage",
)


def save_result_job(info):
    path = Path(info["result_db_path"]) / "job.json"
    saved = {key: info[key] for key in SAVED_FIELDS if key in info}
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(saved, output)
        temporary.replace(path)
    except FileNotFoundError:
        # The result directory may already have been evicted.
        pass
    except OSError:
        logger.exception("Could not save result history for %s", path.parent.name)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def legacy_result(paths):
    """Read the final result row without opening or scanning the event archive."""
    if not paths.parquet.is_file() and not paths.arrow.is_file():
        return None
    with duckdb.connect(paths.duckdb, read_only=True, config={"threads": 1}) as db:
        rows = db.execute("""
            SELECT r.experiment_run_id, r.cluster_id, e.status,
                   c.config->>'N' AS requested,
                   r.n_escaped_total + COALESCE((
                       SELECT sum(f.count) FROM pathway_fragmentation f
                       WHERE f.experiment_result_id = r.id
                   ), 0) AS completed
            FROM multi_pathway_experiment_result r
            JOIN event_execution e ON e.result_id = r.id
                AND e.run_id = r.experiment_run_id AND e.cluster_id = r.cluster_id
            JOIN experiment_run run ON run.id = r.experiment_run_id
            JOIN experiment_config c ON c.id = run.experiment_config_id
            WHERE e.recorded AND e.event_count > 0
                AND e.status IN ('complete', 'cancelled')
            """).fetchmany(2)
    if len(rows) != 1:
        return None
    experiment, cluster, status, requested, completed = rows[0]
    info = {
        "status": "done",
        "result": {"experiment": experiment, "cluster": cluster},
        "counts": {"requested": int(requested or completed), "completed": completed},
    }
    if status == "cancelled":
        info["cancelled_stage"] = "apitof"
    return info


def restore_result_jobs(results_dir):
    jobs = {}
    for directory in results_dir.glob("*.apitofsim"):
        jobid = directory.stem
        if not is_job_id(jobid) or not directory.is_dir():
            continue
        try:
            paths = DatabasePaths(directory)
            paths.require_database()
            manifest = directory / "job.json"
            diagnostic = failure_path(directory)
            if manifest.is_file():
                info = json.loads(manifest.read_text())
            elif diagnostic.is_file():
                # Older failed and incomplete runs already reopen via their ID.
                continue
            else:
                info = legacy_result(paths)
            if info is None:
                continue
            info.update(result_db_path=str(directory), last_update={}, restored=True)
            if info["status"] == "incomplete":
                apply_failure(info, json.loads(diagnostic.read_text()))
            result = info["result"]
            if info["status"] in ("done", "incomplete") and all(
                type(result.get(key)) is int for key in ("experiment", "cluster")
            ):
                jobs[jobid] = info
        except FileNotFoundError:
            # Empty, interrupted, or concurrently evicted result directory.
            continue
        except duckdb.Error, OSError, ValueError, KeyError, TypeError, AttributeError:
            logger.warning("Could not restore result %s", jobid, exc_info=True)
    return jobs
