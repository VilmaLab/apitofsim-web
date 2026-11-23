import json
import logging
from collections import deque
from pathlib import Path

import ray
import sentry_sdk
from starlette.exceptions import HTTPException
from starlette.responses import StreamingResponse

from vms.db import open_result_db

from .queue import get_status_dict

logger = logging.getLogger(__name__)


def simulation_events(**arguments):
    from apitofsim.workflow import recorded_failure

    from vms.jobs.failures import describe_failure, save_failure

    from .cancellation import JobCancelled

    context = {"stage": "queue"}
    result_db_path = arguments["result_db_path"]

    failure = None
    cancelled = False
    try:
        yield from _run_simulation(context=context, **arguments)
    except JobCancelled:
        cancelled = True
    except Exception as exc:
        failure = describe_failure(exc, context, result_db_path)
        db = context.get("db")
        if db is not None and "run_id" in context:
            try:
                recorded = recorded_failure(exc)
                failure_id = (
                    recorded.failure_id
                    if recorded is not None
                    else db.record_failure(
                        context["run_id"],
                        failure["exception_type"],
                        str(exc),
                        cluster_id=context.get("cluster_id"),
                    )
                )
                db.add_failure_diagnostics(failure_id, {"web": failure})
            except Exception as persistence_exc:
                failure["database_error"] = str(persistence_exc)
                logger.exception(
                    "Could not record diagnostics for job %s", failure["job_id"]
                )
        save_failure(result_db_path, failure)
    finally:
        if "db" in context:
            context["db"].close()
    if cancelled:
        yield (
            b"cancelled",
            json.dumps(
                {"stage": context["stage"], "counts": context.get("counts", {})}
            ),
        )
    if failure is not None:
        yield b"failure", json.dumps(failure)


@ray.remote
def warm_simulation_worker():
    """Load simulation dependencies in a regular Ray task worker."""
    from importlib import import_module

    import_module("apitofsim.workflow.runners")
    import_module("minify_html_onepass")


@ray.remote
def run_simulation(*, trace_headers=None, **arguments):
    if not sentry_sdk.get_client().is_active():
        yield from simulation_events(**arguments)
        return

    with sentry_sdk.isolation_scope() as scope:
        scope.set_tag("job_id", Path(arguments["result_db_path"]).stem)
        transaction = sentry_sdk.continue_trace(
            trace_headers or {}, op="queue.process", name="run_simulation"
        )
        with sentry_sdk.start_transaction(transaction):
            # Ray reports exceptions that escape the generator to the scheduler.
            # Handled simulation failures are logged here while the trace is active.
            for event, data in simulation_events(**arguments):
                if event == b"failure":
                    transaction.set_status("internal_error")
                elif event == b"cancelled":
                    transaction.set_status("cancelled")
                yield event, data


def _run_simulation(
    pathways,
    histogram_precision,
    config,
    database_path,
    result_db_path,
    context,
    start_time,
    cancel_control=None,
    jobid=None,
):
    from time import monotonic, time

    from apitofsim.api import (
        MassSpecIntermediateCounter,
        MassSpecLogItem,
        SampleMode,
    )
    from apitofsim.workflow.db_utils import transaction_scope
    from apitofsim.workflow.runners import ExperimentRunner, RecordedMassSpecResult
    from jinja2 import Environment
    from minify_html_onepass import minify

    from vms.jobs.cancellation import cancel_iterator, enter_stage
    from vms.jobs.failures import validate_output
    from vms.resources import jinja_loader

    context["counts"] = {"requested": int(config["N"])}
    db = open_result_db(database_path, result_db_path, readonly=False)
    context["db"] = db
    config_id = db.insert_config("webrun", config)
    runner = ExperimentRunner(db)
    runner.start_run(config_id)
    current_run_id = runner.current_run_id
    context["run_id"] = current_run_id
    jinja_env = Environment(loader=jinja_loader)

    processing = "queue"
    statuses = {
        "queue": "processing",
        "skimmer": "pending",
        "densityandr": "pending",
        "apitof": "pending",
    }

    queue = ""
    skimmer = ""
    densityandr = ""
    apitof = ""

    log = deque(maxlen=100)
    survived = 0
    fragmented = 0
    iterations = 0

    def render_template(path):
        html = jinja_env.get_template(path).render(
            processing=processing,
            statuses=statuses,
            queue=queue,
            skimmer=skimmer,
            densityandr=densityandr,
            apitof=apitof,
            log=log,
            survived=survived,
            fragmented=fragmented,
            realizations=config["N"],
            iterations=iterations,
            ratio=survived / iterations if iterations > 0 else 0,
            completed=statuses["apitof"] in ("done", "cancelled") and iterations > 0,
        )
        try:
            return minify(html)
        except SyntaxError as e:
            # XXX: minify raises the builtin SyntaxError for malformed markup.
            # Ray's RayTaskError wrapper doesn't copy SyntaxError's msg/lineno
            # slots, and traceback.py reads those slots instead of __str__ for
            # anything deriving from SyntaxError, so the message is lost as
            # "<no detail available>". Re-raise as something unremarkable.
            raise RuntimeError(f"Could not minify {path}: {e.msg}") from None

    def update_pane(message):
        return message.encode("utf-8"), render_template(f"analysis/{message}.html")

    def fmt_duration(took):
        return f"{took:.1f}s" if took >= 1 else f"{took * 1000:.0f}ms"

    prepared = None
    skimmer_start = None
    while 1:
        if processing == "queue":
            context["stage"] = processing
            yield b"stage", processing
            statuses["queue"] = "done"
            took_str = fmt_duration(time() - start_time)
            queue = f"Started after {took_str} in the queue"
            yield update_pane("queue")
            statuses["skimmer"] = "processing"
            processing = "skimmer"
            yield update_pane("tabs")
        elif processing == "skimmer":
            enter_stage(cancel_control, jobid, processing)
            skimmer_start = time()
            skimmer = "loading"
            context["stage"] = processing
            yield b"stage", processing
            yield update_pane("skimmer")
            with sentry_sdk.start_span(
                op="simulation.skimmer", name="Prepare simulation"
            ):
                prepared = runner.prepare_from_config(
                    config,
                    pathways=pathways,
                    cached_densityandrate=histogram_precision,
                    show_progress=False,
                )
            took_str = fmt_duration(time() - skimmer_start)
            skimmer = "Finished after " + took_str
            yield update_pane("skimmer")
            statuses["skimmer"] = "done"
            statuses["apitof"] = "processing"
            processing = "apitof"
            yield update_pane("tabs")
        elif processing == "apitof":
            enter_stage(cancel_control, jobid, processing)
            context["stage"] = processing
            yield b"stage", processing
            yield update_pane("apitof")
            yield update_pane("tabs")
            assert prepared is not None
            if len(prepared.groups) != 1:
                raise ValueError("Web runs require pathways from exactly one cluster")
            group = prepared.groups[0]
            context["cluster_id"] = group.cluster_id
            realizations = config["N"]
            terminal = None
            next_log_update = monotonic() + 0.25
            # The public event iterator streams trajectory batches to Python.
            # Use the workflow stream for progress and logs with native recording.
            with (
                sentry_sdk.start_span(op="simulation.apitof", name="Run APi-ToF"),
                runner._iter_mass_spec(
                    prepared,
                    group,
                    realizations,
                    sample_mode=SampleMode.rejection,
                    loglevel=1,
                ) as it,
                cancel_iterator(cancel_control, jobid, it),
            ):
                for result in it:
                    if isinstance(
                        result, (MassSpecIntermediateCounter, RecordedMassSpecResult)
                    ):
                        if isinstance(result, RecordedMassSpecResult):
                            terminal = result
                            counters = result.result.counters
                        else:
                            counters = result.counters
                        context["counts"]["completed"] = int(counters.n_realizations)
                        yield b"progress", json.dumps(context["counts"])
                        survived = counters.n_escaped_total
                        fragmented = counters.n_fragmented_total.sum()
                        iterations = survived + fragmented
                        yield update_pane("apitof")
                    elif isinstance(result, MassSpecLogItem):
                        log.append(f"{result.type}: {result.name}")
                        # The simulator can log per collision. Send a bounded
                        # snapshot so it cannot delay progress or cancellation.
                        now = monotonic()
                        if now >= next_log_update:
                            yield b"apitoflog", "\n".join(log)
                            next_log_update = now + 0.25
            if log:
                yield b"apitoflog", "\n".join(log)
            if terminal is None:
                raise RuntimeError("Simulation ended without a final result")
            final_cancelled = terminal.result.cancelled
            if final_cancelled and counters.n_realizations > 0:
                # Keep the spectrogram summary linked to the cancelled archive.
                with transaction_scope(db.db):
                    db.record_result(
                        terminal.run_id,
                        counters,
                        terminal.result.timings,
                        cluster_id=terminal.cluster_id,
                        pathway_ids=terminal.pathway_ids,
                        result_id=terminal.result_id,
                    )
            context["result"] = {
                "experiment": current_run_id,
                "cluster": group.cluster_id,
            }
            validate_output(
                db, counters, realizations, context, cancelled=final_cancelled
            )
            break
    db.db.execute("CHECKPOINT results_db")
    db.close()
    context.pop("db")
    if final_cancelled:
        yield b"cancelled", json.dumps({"stage": "apitof", "counts": context["counts"]})
    if not final_cancelled or counters.n_realizations > 0:
        yield (
            b"result",
            json.dumps({"experiment": current_run_id, "cluster": group.cluster_id}),
        )
    statuses["apitof"] = "cancelled" if final_cancelled else "done"
    yield update_pane("tabs")


def sse_safe(data, event=None):
    for line in data.split("\n"):
        yield b"data: "
        yield line.encode("utf-8")
        yield b"\n"
    if event is not None:
        yield b"event: "
        yield event
    yield b"\r\n\r\n"


def sse_yolo(data, event=None):
    for line in data.split("\n"):
        yield b"data: "
        yield line.encode("utf-8")
        yield b"\n"
    if event is not None:
        yield b"event: "
        yield event
    yield b"\r\n\r\n"


async def update_analysis(request):
    from vms.jobs import process_jobid
    from vms.jobs.failures import result_db_missing

    from .queue import notify_job

    if "text/event-stream" not in request.headers.get("accept", ""):
        raise HTTPException(400)

    status = get_status_dict()
    jobid = process_jobid(request)
    info = status[jobid]  # type: ignore

    async def send_events():
        if "changed" not in info:
            notify_job(info)
        sent = {}
        while True:
            # Capture before taking a snapshot: a concurrent update wakes us even
            # when it arrives while the response is yielding its previous snapshot.
            changed = info["changed"]
            terminal = info["status"] in ("done", "failed", "incomplete", "cancelled")
            if terminal and result_db_missing(info):
                for event in (b"result-deleted", b"done"):
                    for bit in sse_safe("", event=event):
                        yield bit
                return
            snapshot = dict(info["last_update"])
            for event, data in snapshot.items():
                if sent.get(event) != data:
                    for bit in sse_safe(data, event):
                        yield bit
            sent = snapshot
            if terminal:
                for bit in sse_safe("", event=b"done"):
                    yield bit
                return
            await changed.wait()

    return StreamingResponse(
        send_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
