"""Exercise the web worker with upstream preparation and recording."""

import json
from contextlib import contextmanager
from time import time

import duckdb
import pytest
from apitofsim.workflow.db import RealizationDatabase, SuperClusterDatabase
from starlette.requests import Request

from vms.app import app
from vms.db import current_db
from vms.forms import SettingsForm, get_histogram_precision_choices
from vms.jobs.run import simulation_events
from vms.result_plots import open_result_db


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


@pytest.mark.parametrize("outcome", ["complete", "cancelled", "failed"])
def test_recording_worker(worker_arguments, monkeypatch, outcome):
    if outcome == "cancelled":

        @contextmanager
        def cancel_immediately(control, iterator):
            iterator.request_cancel()
            yield

        monkeypatch.setattr("vms.jobs.cancellation.cancel_iterator", cancel_immediately)
        worker_arguments["config"]["N"] = 10_000_000
    elif outcome == "failed":

        def fail(*args, **kwargs):
            raise RuntimeError("Injected result write failure")

        monkeypatch.setattr(RealizationDatabase, "record_result", fail)

    events = list(simulation_events(**worker_arguments))
    messages = {event: data for event, data in events}
    result_path = worker_arguments["result_db_path"]
    with duckdb.connect(result_path, read_only=True) as db:
        run_id, metadata = db.execute(
            "SELECT id, run_config FROM experiment_run"
        ).fetchone()
        metadata = json.loads(metadata)
        assert (
            metadata["dos_histogram_id"],
            metadata["rate_histogram_id"],
        ) == worker_arguments["histogram_precision"]
        failures = db.execute("SELECT id, msg FROM experiment_failure").fetchall()
        results = db.execute(
            "SELECT id FROM multi_pathway_experiment_result"
        ).fetchall()
        assert db.execute(
            "SELECT count(*) FROM realization WHERE experiment_result_id IS NULL"
        ).fetchone() == (0,)
        if outcome == "failed":
            assert len(failures) == 1
            assert results == []
            assert b"result" not in messages
            failure = json.loads(messages[b"failure"])
            assert failure["message"] == "Injected result write failure"
            assert json.loads(failures[0][1]) == failure
            assert (
                db.execute(
                    "SELECT count(*) FROM realization WHERE experiment_result_id = ?",
                    [failures[0][0]],
                ).fetchone()[0]
                > 0
            )
        else:
            assert failures == []
            assert len(results) == 1
            if outcome == "cancelled":
                counts = json.loads(messages[b"cancelled"])["counts"]
                assert counts["completed"] < 10_000_000
                assert (b"result" in messages) == (counts["completed"] > 0)
            else:
                assert b"failure" not in messages
                assert b"cancelled" not in messages
                result = json.loads(messages[b"result"])
                assert result["experiment"] == run_id
                assert json.loads(messages[b"progress"])["completed"] == 2
    if outcome == "complete":
        db = open_result_db(worker_arguments["database_path"], result_path)
        try:
            curves = db.pathway_rate_curves(run_id, result["cluster"], results[0][0])
            assert len(curves) == len(worker_arguments["pathways"])
            assert all(curve.histogram is not None for curve in curves)
        finally:
            db.close()
