import os
import tempfile

os.environ["MPLCONFIGDIR"] = tempfile.mkdtemp()

import json
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import partial
from io import BytesIO
from os import environ
from pathlib import Path
from uuid import uuid4

import anyio
import holoviews as hv
import matplotlib
import numpy
import pandas
import ray
from anyio import to_thread
from apitofsim.api import ureg
from apitofsim.workflow.db import SuperClusterDatabase, guess_ase_db_filename
from lxml import etree
from pint import set_application_registry
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse, RedirectResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette_wtf import CSRFProtectMiddleware, csrf_protect

from .cluster_info import enrich_cluster
from .forms import BuiltInInstrumentForm, CustomInstrumentForm, SettingsForm
from .result_plots import make_bokeh_app

database_path = environ["DATABASE"]
results_path = environ["RESULTS"]

matplotlib.use("SVG")
hv.extension("matplotlib")  # type: ignore

set_application_registry(ureg)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
jinja_loader = templates.env.loader
current_db = ContextVar("current_db")
status = {}
bokeh_application = make_bokeh_app(status, database_path)


def connect_db():
    return SuperClusterDatabase(
        database_path, ase_filename=guess_ase_db_filename(database_path), readonly=True
    )


class DatabaseMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "").startswith(
            "/analysis/bokeh/"
        ):
            await self.app(scope, receive, send)
            return
        token = current_db.set(connect_db())
        try:
            await self.app(scope, receive, send)
        finally:
            current_db.reset(token)


def worker_process_setup_hook():
    from apitofsim.api import ureg
    from pint import set_application_registry

    set_application_registry(ureg)


@asynccontextmanager
async def lifespan(app):
    runtime_env = {
        "worker_process_setup_hook": worker_process_setup_hook,
    }
    if os.environ.get("RAY_USE_PIP") == "1":
        runtime_env["pip"] = ["jinja2", "minify-html-onepass"]
    my_ray = await to_thread.run_sync(
        partial(
            ray.init,
            address=environ.get("RAY_ADDRESS", "local"),
            log_to_driver=False,
            runtime_env=runtime_env,
        )
    )
    print("Dashboard url", my_ray.dashboard_url)
    # XXX: This complains about blocking in debug mode on shutdown since the shutdown blocks
    # Maybe try to get ray to add an __aexit__ method?
    with my_ray:
        await bokeh_application.core.start()
        try:
            yield
        finally:
            await bokeh_application.core.stop()


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
    import apitofsim.workflow.sql_files as sql_files
    import duckdb
    from apitofsim.workflow.db import RealizationDatabase

    database_path = str(database_path).replace("'", "''")
    result_db_path = str(result_db_path).replace("'", "''")
    duckdb_db = duckdb.connect()
    duckdb_db.execute(f"ATTACH '{database_path}' AS clusters_db (READ_ONLY);")
    duckdb_db.execute(f"ATTACH '{result_db_path}' AS results_db;")
    duckdb_db.execute("USE results_db;")
    duckdb_db.execute("SET search_path = 'results_db,clusters_db'")
    sql = "\n".join(
        [
            sql_files.experiment,
            sql_files.experiment_report,
            sql_files.realizations,
            sql_files.event_report,
        ]
    )
    # We need to filter these out since they're not supported across multiple schemas in DuckDB
    sql = "\n".join((line for line in sql.split("\n") if "foreign key" not in line))
    duckdb_db.execute(sql)

    return RealizationDatabase(((duckdb_db, None)))


@ray.remote
def run_simulation(
    voltage,
    pathways,
    gas,
    quadrupole,
    histograms,
    config,
    database_path,
    result_db_path,
):
    from time import sleep

    from apitofsim.api import (
        CollisionEvent,
        EscapeEvent,
        FragmentationEvent,
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

    db = setup_result_db(database_path, result_db_path)
    config_id = db.insert_config("webrun", {**config, "quadrupole": quadrupole})
    jinja_env = Environment(loader=jinja_loader)

    processing = "queue"
    statuses = {
        "queue": "processing",
        "skimmer": "pending",
        "densityandr": "pending",
        "apitof": "pending",
    }

    queue = "Queuing"
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
            realizations=config["realizations"],
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

    skimmer_np = k_rates = cluster_dos = None
    mass_spec = None
    while 1:
        if processing == "queue":
            yield update_pane("queue")
            sleep(1)
            statuses["queue"] = "done"
            queue = "Queued after XXXs"
            yield update_pane("queue")
            statuses["skimmer"] = "processing"
            processing = "skimmer"
            yield update_pane("tabs")
        elif processing == "skimmer":
            skimmer += "Skimming...<br>"
            yield update_pane("skimmer")
            skimmer_np, k_rates, cluster_dos = preparer.run_preliminaries(
                config,
                cluster_indexed,
                pathway_lookup=pathway_lookup,
                cached_densityandrate=config["histogram_precision"],
                show_progress=False,
            )
            yield update_pane("skimmer")
            statuses["skimmer"] = "done"
            statuses["apitof"] = "processing"
            processing = "apitof"
            yield update_pane("tabs")
            mass_spec = MassSpectrometer(
                skimmer_np,
                config["lengths"],
                voltage,
                config["T"],
                config["pressures"],
                quadrupole=quadrupole,
            )
        elif processing == "apitof":
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

            from apitofsim.api import MassSpecSubstanceSingleInput

            realizations = config["realizations"]
            subs = MassSpecSubstanceSingleInput(
                group["cluster"],
                group["pathways"],
                gas,
                group["density_hist"],
                group["cluster_charge_sign"],
            )

            assert mass_spec is not None
            event_recorder = EventRecorder(db, group["pathway_ids"])
            current_run_id = db.insert_run(config_id)
            experiment_result_id = None
            with mass_spec_iter(
                mass_spec,
                subs,
                realizations,
                sample_mode=2,
                strict=True,
                logconf=(0, True),
            ) as it:
                for result in it:
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
                        result, (CollisionEvent, FragmentationEvent, EscapeEvent)
                    ):
                        event_recorder(result)
                    elif isinstance(result, MassSpecLogItem):
                        log.append(f"{result.type}: {result.name}")
                        yield update_pane("apitof")
            if experiment_result_id is None:
                raise RuntimeError("Simulation ended without a final result")
            break
    db.db.execute("CHECKPOINT results_db")
    db.close()
    yield (
        b"result",
        json.dumps({"experiment": current_run_id, "cluster": group["cluster_id"]}),
    )
    statuses["apitof"] = "done"
    yield update_pane("tabs")


def start_job(job_id):
    info = status[job_id]  # type: ignore

    simulation_call = run_simulation.remote(
        **info["arguments"],
        database_path=database_path,
        result_db_path=info["result_db_path"],
    )
    new_info = {
        **info,  # type: ignore
        "status": "running",
        "task": simulation_call,
        "aiter": aiter(simulation_call),
    }
    status[job_id] = new_info  # type: ignore
    return new_info


def pump_jobs():

    # XXX: 0.01s per job. Run in thread?
    for job_id, info in status.items():  # type: ignore
        if info["status"] == "pending":
            start_job(job_id)


def join_abbrv_filter(s, sep="<br>"):
    from itertools import chain

    def joined(s):
        return sep.join((str(e) for e in s))

    if len(s) <= 5:
        return joined(s)
    else:
        return joined(chain(s[:2], ["..."], s[-2:]))


templates.env.filters["join_abbrv"] = join_abbrv_filter


def render_template(request, path, **context):
    return templates.TemplateResponse(request, path, context)


@csrf_protect
async def settings(request):
    form = await SettingsForm.from_formdata(request)
    if await form.validate_on_submit():
        arguments = form.get_data()
        job_id = uuid4()
        result_db_path = Path(results_path).with_name(f"{job_id.hex}.duckdb")
        result_db_path.parent.mkdir(parents=True, exist_ok=True)
        status[job_id.hex] = {  # type: ignore
            "status": "pending",
            "arguments": arguments,
            "result_db_path": str(result_db_path),
            "last_update": {},
        }
        pump_jobs()
        return RedirectResponse("/analysis?jobid=" + job_id.hex, status_code=303)
    return render_template(
        request,
        "settings/settings.html",
        form=form,
        primary_cluster=None,
    )


def hypothetical_spectrogram(cluster_ids, masses, max_mass=None):
    if max_mass is None:
        max_mass = (
            current_db.get()
            .db.sql("select max(atomic_mass) from cluster")
            .fetchone()[0]
        )
    spectrogram = hv.Spikes(
        (masses, 1),
        hv.Dimension("m/z", soft_range=(0, max_mass)),
        "Intensity",
    ).opts(fig_inches=(6, 3), aspect=2)
    matplotlib_fig = hv.render(spectrogram)
    ax = matplotlib_fig.axes[0]
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_yticks([])
    f = BytesIO()
    matplotlib_fig.savefig(f, format="svg")
    f.seek(0)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    tree = etree.parse(f)
    lines = tree.getroot().xpath(
        "//svg:g[@id='LineCollection_1']/svg:path", namespaces=ns
    )
    for line, cluster_id in zip(lines, cluster_ids, strict=True):
        line.attrib["id"] = "spectrogram-line-cluster-" + str(cluster_id)
        line.attrib["__AT_SYMBOL__click"] = f"current_cluster = {cluster_id}"
        line.attrib["__AT_SYMBOL__mouseover"] = f"active_cluster = {cluster_id}"
        line.attrib["__AT_SYMBOL__mouseout"] = "active_cluster = null"
        line.attrib["__COLON__style"] = (
            f"(active_cluster || current_cluster) == {cluster_id} ? {{'stroke': 'var(--color-blue-700)', 'z-index': 1}} : {{}}"
        )
    # Draw the current cluster on top of the others
    lines[-1].addnext(
        etree.XML(
            """
            <g style="pointer-events: none;">
                <use __COLON__href="'#spectrogram-line-cluster-' + (active_cluster || current_cluster)"/>
            </g>
            """
        )
    )
    return (
        etree.tostring(tree, encoding="unicode")
        .replace("__AT_SYMBOL__", "@")
        .replace("__COLON__", ":")
    )


async def pathways_fragment(request):
    form = await SettingsForm.from_formdata(request)
    if "cluster" not in request.query_params:
        raise HTTPException(400, detail="Missing cluster parameter")

    try:
        cluster_id = int(request.query_params["cluster"])
    except ValueError:
        raise HTTPException(400, detail="Invalid cluster parameter") from None
    db = current_db.get()
    relevant_cluster_ids = db.db.sql(
        """
        select distinct unnest([cluster_id, product1_id, product2_id]) as relevant_cluster_id
        from pathway
        where cluster_id = ?
        """,
        params=(cluster_id,),
    ).fetchdf()
    cluster_df = (
        db.db.table("cluster")
        .join(
            db.db.from_df(relevant_cluster_ids).set_alias("relevant"),
            condition="relevant.relevant_cluster_id = cluster.id",
        )
        .fetchdf()
        .replace({pandas.NA: None})
    )
    cluster_ids = cluster_df["id"].to_numpy()
    masses = cluster_df["atomic_mass"].to_numpy()
    clusters = {}
    print(cluster_df)
    for cluster in cluster_df.itertuples():
        cluster = cluster._asdict()
        enrich_cluster(db.ase_db, cluster)
        clusters[cluster["id"]] = cluster
    pathways_relations = db.db.sql(
        """select * from pathway where cluster_id = ?""",
        params=(cluster_id,),
    ).fetchdf()
    pathways = []

    for pathway in pathways_relations.itertuples():
        cluster = clusters[pathway.cluster_id]
        product1 = clusters[pathway.product1_id]
        product2 = clusters[pathway.product2_id]
        bonding_energy = (
            product1["electronic_energy"]
            + product2["electronic_energy"]
            - cluster["electronic_energy"]
        )

        pathways.append(
            {
                "cluster": (pathway.cluster_id, cluster["common_name"]),
                "product1": (pathway.product1_id, product1["common_name"]),
                "product2": (pathway.product2_id, product2["common_name"]),
                "bonding_energy": bonding_energy,
                "form": form.pathways.append_entry({"pathway": pathway.id}),
            }
        )

    return render_template(
        request,
        "settings/_render_pathways.html",
        pathways=pathways,
        clusters=clusters.values(),
        mass_spectrogram=hypothetical_spectrogram(cluster_ids, masses),
        primary_cluster=(cluster_id, clusters[cluster_id]["common_name"]),
    )


async def hypothetical_spectrogram_fragment(request):
    form = SettingsForm(request, formdata=request.query_params)
    if not form.pathways.validate(form) or not form.cluster.validate(form):
        raise HTTPException(400, detail="Invalid pathways data")
    # cluster_id = int(form.cluster.data)
    pathway_ids = numpy.array(
        [
            int(pathway["pathway"])
            for pathway in form.pathways.data
            if pathway["enabled"]
        ]
    )
    cluster_infos = (
        current_db.get()
        .clusters_query(pathways=pathway_ids)
        .select("id, atomic_mass")
        .fetchnumpy()
    )
    return HTMLResponse(
        hypothetical_spectrogram(cluster_infos["id"], cluster_infos["atomic_mass"])
    )


async def instrument_fragment(request):
    instrument = request.query_params.get("instrument")
    if instrument == "custom":
        return render_template(
            request,
            "settings/_render_instrument.html",
            form=CustomInstrumentForm(prefix="instrument-"),
        )
    elif instrument == "default3000":
        return render_template(
            request,
            "settings/_render_instrument.html",
            form=BuiltInInstrumentForm(prefix="instrument-"),
        )
    else:
        raise HTTPException(400, detail="Invalid instrument parameter")


def process_jobid(request):
    jobid = request.query_params.get("jobid")
    if jobid is None:
        raise HTTPException(400, detail="Missing jobid parameter")
    if jobid not in status:  # type: ignore
        raise HTTPException(404, detail="Job ID not found")
    return jobid


async def analysis(request):
    jobid = process_jobid(request)
    completed = "result" in status[jobid]
    return render_template(
        request,
        "analysis/analysis.html",
        jobid=jobid,
        completed=completed,
        statuses=(
            {"queue": "done", "skimmer": "done", "apitof": "done"}
            if completed
            else {"queue": "processing", "skimmer": "pending", "apitof": "pending"}
        ),
        infos={
            "queue": "Queuing",
            "skimmer": "",
            "densityandr": "",
            "apitof": "",
        },
        survived=0,
        fragmented=0,
        realizations=0,
        iterations=0,
        ratio=0,
    )


async def result_plot(request):
    jobid = request.path_params["jobid"]
    plot = request.path_params["plot"]
    if plot not in ("explorer", "spectrogram"):
        raise HTTPException(404)
    info = status.get(jobid)
    if info is None or "result" not in info:
        raise HTTPException(404)
    from bokeh.embed import server_document

    result = info["result"]
    plot_url = str(request.url_for("bokeh", path=f"/{plot}"))
    plot_script = server_document(plot_url, arguments={"jobid": jobid, **result})
    return render_template(
        request,
        "analysis/plot.html",
        plot=plot,
        plot_script=plot_script,
    )


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
    if "text/event-stream" not in request.headers.get("accept", ""):
        raise HTTPException(400)

    jobid = process_jobid(request)

    async def send_events():
        sent_something = False
        while True:
            info = status[jobid]  # type: ignore
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
                    info["status"] = "done"
                    for bit in sse_safe("", event=b"done"):
                        yield bit
                    return
                if event == b"result":
                    info["result"] = json.loads(data)
                    continue
                info["last_update"][event] = data
                for bit in sse_safe(data, event):
                    yield bit
            elif info["status"] == "done":
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


app = Starlette(
    routes=[
        Route("/", settings, methods=["GET", "POST"]),
        Route("/fragments/pathways", pathways_fragment),
        Route("/fragments/hypothetical-spectrogram", hypothetical_spectrogram_fragment),
        Route("/fragments/instrument", instrument_fragment),
        Route("/analysis", analysis),
        Route("/analysis/updates", update_analysis),
        Route("/analysis/plots/{jobid}/{plot}", result_plot),
        Mount("/analysis/bokeh", bokeh_application, name="bokeh"),
        Mount(
            "/static",
            StaticFiles(directory=Path(__file__).parent / "static"),
            name="static",
        ),
    ],
    middleware=[
        Middleware(
            SessionMiddleware, secret_key=environ.get("SECRET_KEY", "a-secret-key")
        ),
        Middleware(
            CSRFProtectMiddleware, csrf_secret=environ.get("SECRET_KEY", "a-secret-key")
        ),
        Middleware(DatabaseMiddleware),
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app)
