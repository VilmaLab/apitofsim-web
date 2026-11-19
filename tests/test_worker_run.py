"""Exercise the web worker with upstream preparation and recording."""

import json
from contextlib import contextmanager
from pathlib import Path
from time import time

import pytest
from apitofsim.workflow.db import ExperimentDatabase, SuperClusterDatabase
from starlette.requests import Request

from vms.app import app
from vms.db import current_db, open_result_db
from vms.forms import SettingsForm, get_histogram_precision_choices
from vms.jobs.run import simulation_events


@pytest.fixture
def worker_arguments(test_db, tmp_path):
    db = SuperClusterDatabase(str(test_db), readonly=True)
    token = current_db.set(db)
    get_histogram_precision_choices.cache_clear()
    try:
        form = SettingsForm(
            Request({"type": "http", "method": "GET", "headers": [], "app": app})
        )
        _, _, pathways = db.get_all_lookups()
        parent = next(iter(pathways.values()))[0]
        selected = [key for key, value in pathways.items() if value[0] == parent]
        form.pathways[0].pathway.process_formdata([str(selected[0])])
        form.simulation.realizations.data = 2
        arguments = form.get_data()
        assert arguments["pathways"] == [selected[0]]
    finally:
        get_histogram_precision_choices.cache_clear()
        current_db.reset(token)
        db.close()
    return dict(
        arguments,
        database_path=str(test_db),
        result_db_path=str(tmp_path / "output.duckdb"),
        start_time=time(),
    )


@pytest.mark.parametrize(
    "outcome",
    ["complete", "cancelled", "failed", "startup_failed", "diagnostics_failed"],
)
def test_recording_worker(worker_arguments, monkeypatch, outcome):
    if outcome == "cancelled":

        @contextmanager
        def cancel_immediately(control, iterator):
            iterator.request_cancel()
            yield

        monkeypatch.setattr("vms.jobs.cancellation.cancel_iterator", cancel_immediately)
        worker_arguments["config"]["N"] = 10_000_000
    elif outcome in ("failed", "startup_failed", "diagnostics_failed"):

        def fail(*args, **kwargs):
            raise RuntimeError("Injected result write failure")

        if outcome == "startup_failed":
            monkeypatch.setattr("apitofsim.api._mass_spec_batches", fail)
        else:
            monkeypatch.setattr(ExperimentDatabase, "record_result", fail)
        if outcome == "diagnostics_failed":

            def fail_diagnostics(*args, **kwargs):
                raise OSError("Cannot enrich diagnostics")

            monkeypatch.setattr(
                ExperimentDatabase, "add_failure_diagnostics", fail_diagnostics
            )

    events = list(simulation_events(**worker_arguments))
    messages = {event: data for event, data in events}
    result_path = worker_arguments["result_db_path"]
    with open_result_db(worker_arguments["database_path"], result_path) as output:
        db = output.db
        run_id, metadata = db.execute(
            "SELECT id, run_config FROM experiment_run"
        ).fetchone()
        metadata = json.loads(metadata)
        assert (
            metadata["dos_histogram_id"],
            metadata["rate_histogram_id"],
        ) == worker_arguments["histogram_precision"]
        failures = db.execute(
            "SELECT id, msg, diagnostics FROM experiment_failure"
        ).fetchall()
        results = db.execute(
            "SELECT id FROM multi_pathway_experiment_result"
        ).fetchall()
        if outcome in ("failed", "startup_failed", "diagnostics_failed"):
            assert len(failures) == 1
            assert results == []
            assert b"result" not in messages
            failure = json.loads(messages[b"failure"])
            assert failure["message"] == "Injected result write failure"
            assert failures[0][1] == "Injected result write failure"
            assert (
                json.loads(Path(result_path).with_suffix(".failure.json").read_text())
                == failure
            )
            if outcome == "diagnostics_failed":
                assert failures[0][2] is None
                assert failure["database_error"] == "Cannot enrich diagnostics"
            else:
                assert json.loads(failures[0][2]) == {"web": failure}
            recorded_realizations = db.execute(
                "SELECT count(*) FROM realization JOIN event_execution "
                "ON realization.experiment_result_id = event_execution.result_id "
                "WHERE event_execution.failure_id = ?",
                [failures[0][0]],
            ).fetchone()[0]
            assert (
                (recorded_realizations == 0)
                if outcome == "startup_failed"
                else (recorded_realizations > 0)
            )
        else:
            assert failures == []
            if outcome == "cancelled":
                counts = json.loads(messages[b"cancelled"])["counts"]
                assert counts["completed"] < 10_000_000
                assert len(results) == int(counts["completed"] > 0)
                assert db.execute("SELECT status FROM event_execution").fetchone() == (
                    "cancelled",
                )
                assert (b"result" in messages) == (counts["completed"] > 0)
            else:
                assert len(results) == 1
                assert b"failure" not in messages
                assert b"cancelled" not in messages
                result = json.loads(messages[b"result"])
                assert result["experiment"] == run_id
                assert json.loads(messages[b"progress"])["completed"] == 2
                assert output.events_path.suffix == ".parquet"
                assert output.events_path.is_file()
                assert output.realizations_query(run_id=run_id).count(
                    "*"
                ).fetchone() == (2,)
                curves = output.pathway_rate_curves(
                    run_id, result["cluster"], results[0][0]
                )
                assert len(curves) == len(worker_arguments["pathways"])
                assert all(curve.histogram is not None for curve in curves)
