import json

import anyio
import ray
from starlette.exceptions import HTTPException
from starlette.responses import StreamingResponse

from .queue import get_status_dict


def build_input_pathways(
    config,
    histograms,
    cluster_indexed,
    name_lookup,
    pathway_lookup,
    k_rates,
    cluster_dos,
):
    from apitofsim.api import (
        Histogram,
        MassSpecInputFragmentationPathway,
        scaled_density,
        scaled_rate_const,
    )

    density_hist_params, rate_hist_params = histograms
    retval = None
    for pathway_id, (
        cluster_id,
        product1_id,
        product2_id,
    ) in pathway_lookup.items():
        cluster = cluster_indexed[cluster_id]
        if retval is None:
            density_cluster = cluster_dos[cluster_id]
            density_hist = scaled_density(
                Histogram.from_mesh(
                    *density_hist_params,
                    density_cluster,
                )
            )
            retval = {
                "pathways": [],
                "cluster": cluster,
                "cluster_id": cluster_id,
                "cluster_label": name_lookup[cluster_id],
                "density_hist": density_hist,
                "product_labels": [],
                "pathway_ids": [],
                # TODO: Should this not be included in ClusterData by now?
                "cluster_charge_sign": -1,
            }
        product1 = cluster_indexed[product1_id]
        product2 = cluster_indexed[product2_id]
        rate_const = k_rates[pathway_id]
        rate_hist = scaled_rate_const(
            Histogram.from_mesh(
                *rate_hist_params,
                rate_const,
            )
        )
        retval["pathways"].append(
            MassSpecInputFragmentationPathway(cluster, product1, product2, rate_hist)
        )
        retval["pathway_ids"].append(pathway_id)
    return retval


def setup_result_db(database_path, result_db_path):
    # Use DDL without foreign keys since they're not supported across multiple schemas in DuckDB
    import apitofsim.workflow.sql_files_nofk as sql
    import duckdb
    from apitofsim.workflow.db import RealizationDatabase

    database_path = str(database_path).replace("'", "''")
    result_db_path = str(result_db_path).replace("'", "''")
    duckdb_db = duckdb.connect()
    duckdb_db.execute(f"ATTACH '{database_path}' AS clusters_db (READ_ONLY);")
    duckdb_db.execute(f"ATTACH '{result_db_path}' AS results_db;")
    duckdb_db.execute("USE results_db;")
    duckdb_db.execute("SET search_path = 'results_db,clusters_db'")
    ddl = "\n".join(
        [
            sql.experiment,
            sql.experiment_report,
            sql.realizations,
            sql.event_report,
        ]
    )
    duckdb_db.execute(ddl)

    return RealizationDatabase(((duckdb_db, None)))


def simulation_events(**arguments):
    from vms.jobs.failures import describe_failure, save_failure

    context = {"stage": "queue"}
    result_db_path = arguments["result_db_path"]
    failure = None
    try:
        yield from _run_simulation(context=context, **arguments)
    except Exception as exc:
        failure = describe_failure(exc, context, result_db_path)
        db = context.get("db")
        if db is not None and "run_id" in context:
            try:
                db.record_failure(
                    context["run_id"],
                    failure["exception_type"],
                    json.dumps(failure),
                    cluster_id=context.get("cluster_id"),
                )
            except Exception as persistence_exc:
                failure["database_error"] = str(persistence_exc)
        save_failure(result_db_path, failure)
    finally:
        if "db" in context:
            context["db"].close()
    if failure is not None:
        yield b"failure", json.dumps(failure)


@ray.remote
def run_simulation(**arguments):
    yield from simulation_events(**arguments)


def _run_simulation(
    pathways,
    histograms,
    histogram_precision,
    config,
    database_path,
    result_db_path,
    context,
    start_time,
):
    from time import sleep, time

    from apitofsim.api import (
        CollisionEvent,
        EscapeEvent,
        FragmentationEvent,
        InitEvent,
        MassSpecFinalResult,
        MassSpecIntermediateCounter,
        MassSpecLogItem,
        MassSpectrometer,
        mass_spec_iter,
    )
    from apitofsim.workflow.db import EventRecorder
    from apitofsim.workflow.runners import DerivedDataPreparer
    from jinja2 import Environment
    from minify_html_onepass import minify

    from vms.jobs.failures import validate_output
    from vms.resources import jinja_loader

    context["counts"] = {"requested": int(config["N"])}
    db = setup_result_db(database_path, result_db_path)
    context["db"] = db
    config_id = db.insert_config("webrun", config)
    current_run_id = db.insert_run(config_id)
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

    log = []
    survived = 0
    fragmented = 0
    iterations = 0

    preparer = DerivedDataPreparer(db)

    cluster_indexed, name_lookup, pathway_lookup = db.get_all_lookups(pathways=pathways)

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
            completed=statuses["apitof"] == "done",
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

    skimmer_np = k_rates = cluster_dos = None
    mass_spec = None
    skimmer_start = None
    while 1:
        if processing == "queue":
            context["stage"] = processing
            yield b"stage", processing
            yield update_pane("queue")
            sleep(1)
            statuses["queue"] = "done"
            took_str = fmt_duration(time() - start_time)
            queue = f"Queued after {took_str}"
            yield update_pane("queue")
            statuses["skimmer"] = "processing"
            processing = "skimmer"
            yield update_pane("tabs")
        elif processing == "skimmer":
            skimmer_start = time()
            skimmer = "loading"
            context["stage"] = processing
            yield b"stage", processing
            yield update_pane("skimmer")
            skimmer_np, k_rates, cluster_dos = preparer.run_preliminaries(
                config,
                cluster_indexed,
                pathway_lookup=pathway_lookup,
                cached_densityandrate=histogram_precision,
                show_progress=False,
            )
            skimmer = "Finished after " + took_str
            yield update_pane("skimmer")
            statuses["skimmer"] = "done"
            statuses["apitof"] = "processing"
            processing = "apitof"
            took_str = fmt_duration(time() - skimmer_start)
            yield update_pane("tabs")
            mass_spec = MassSpectrometer(
                skimmer_np,
                config["lengths"],
                config["voltages"],
                config["T"],
                config["pressures"],
                quadrupole=config["quadrupole"],
            )
        elif processing == "apitof":
            context["stage"] = processing
            yield b"stage", processing
            yield update_pane("apitof")
            yield update_pane("tabs")
            group = build_input_pathways(
                config,
                histograms,
                cluster_indexed,
                name_lookup,
                pathway_lookup,
                k_rates,
                cluster_dos,
            )
            assert group is not None
            context["cluster_id"] = group["cluster_id"]

            from apitofsim.api import MassSpecSubstanceSingleInput

            realizations = config["N"]
            subs = MassSpecSubstanceSingleInput(
                group["cluster"],
                group["pathways"],
                config["gas"],
                group["density_hist"],
                group["cluster_charge_sign"],
            )

            assert mass_spec is not None
            event_recorder = EventRecorder(db, group["pathway_ids"])
            experiment_result_id = None
            with mass_spec_iter(
                mass_spec,
                subs,
                realizations,
                sample_mode=2,
                strict=True,
                logconf=(1, True),
            ) as it:
                for result in it:
                    if isinstance(
                        result, (MassSpecIntermediateCounter, MassSpecFinalResult)
                    ):
                        context["counts"]["completed"] = int(
                            result.counters.n_realizations
                        )
                    if isinstance(result, MassSpecIntermediateCounter):
                        counters = result.counters
                        survived = counters.n_fragmented_total.sum()
                        fragmented = counters.n_escaped_total
                        iterations = survived + fragmented
                        yield update_pane("apitof")
                    elif isinstance(result, MassSpecFinalResult):
                        counters = result.counters
                        survived = counters.n_fragmented_total.sum()
                        fragmented = counters.n_escaped_total
                        iterations = survived + fragmented
                        experiment_result_id = db.record_result(
                            current_run_id,
                            counters,
                            timings=result.timings,
                            cluster_id=group["cluster_id"],
                            pathway_ids=group["pathway_ids"],
                        )
                        event_recorder.relate_realizations(experiment_result_id)
                        yield update_pane("apitof")
                    elif isinstance(
                        result,
                        (InitEvent, CollisionEvent, FragmentationEvent, EscapeEvent),
                    ):
                        event_recorder(result)
                    elif isinstance(result, MassSpecLogItem):
                        log.append(f"{result.type}: {result.name}")
                        yield b"apitoflog", "\n".join(log)
            if experiment_result_id is None:
                raise RuntimeError("Simulation ended without a final result")
            context["result"] = {
                "experiment": current_run_id,
                "cluster": group["cluster_id"],
            }
            validate_output(db, counters, realizations, context)
            break
    db.db.execute("CHECKPOINT results_db")
    db.close()
    context.pop("db")
    yield (
        b"result",
        json.dumps({"experiment": current_run_id, "cluster": group["cluster_id"]}),
    )
    statuses["apitof"] = "done"
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
    from vms.jobs.failures import apply_failure, describe_failure, save_failure

    if "text/event-stream" not in request.headers.get("accept", ""):
        raise HTTPException(400)

    status = get_status_dict()
    jobid = process_jobid(request)
    info = status[jobid]  # type: ignore

    async def send_events():
        sent_something = False
        while True:
            if info["status"] == "pending":
                await anyio.sleep(1)
            elif info["status"] == "running":
                if not sent_something:
                    if "last_update" in info:
                        for event, data in info["last_update"].items():
                            for bit in sse_safe(data, event):
                                yield bit
                sent_something = True
                try:
                    # Really awaiting here(!)
                    event, data = await (await anext(info["aiter"]))
                except StopAsyncIteration:
                    if "result" not in info:
                        exc = RuntimeError(
                            "Simulation stream ended without a final result"
                        )
                        failure = describe_failure(exc, info, info["result_db_path"])
                        save_failure(info["result_db_path"], failure)
                        apply_failure(info, failure)
                        continue
                    info["status"] = "done"
                    for bit in sse_safe("", event=b"done"):
                        yield bit
                    return
                except Exception as exc:
                    # Worker crashes and Ray transport errors never reach the
                    # worker's own exception handler.
                    failure = describe_failure(exc, info, info["result_db_path"])
                    save_failure(info["result_db_path"], failure)
                    apply_failure(info, failure)
                    continue
                if event == b"stage":
                    info["stage"] = data
                    continue
                if event == b"failure":
                    apply_failure(info, json.loads(data))
                    continue
                if event == b"result":
                    info["result"] = json.loads(data)
                    continue
                info["last_update"][event] = data
                for bit in sse_safe(data, event):
                    yield bit
            elif info["status"] in ("done", "failed", "incomplete"):
                for event, data in info["last_update"].items():
                    for bit in sse_safe(data, event):
                        yield bit
                for bit in sse_safe("", event=b"done"):
                    yield bit
                return

    return StreamingResponse(
        send_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
