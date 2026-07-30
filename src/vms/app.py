import os
import tempfile

os.environ["MPLCONFIGDIR"] = tempfile.mkdtemp()

import os
import typing
from functools import partial
from io import BytesIO
from os import environ
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
from quart import Quart, abort, g, make_response, redirect, render_template, request

from .cluster_info import enrich_cluster
from .forms import BuiltInInstrumentForm, CustomInstrumentForm, SettingsForm

database_path = environ["DATABASE"]
results_path = environ["RESULTS"]

matplotlib.use("SVG")
hv.extension("matplotlib")  # type: ignore

set_application_registry(ureg)

app = Quart(__name__)
app.config["SECRET_KEY"] = "a-secret-key"
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
status = {}


def connect_db():
    return SuperClusterDatabase(
        database_path, ase_filename=guess_ase_db_filename(database_path), readonly=True
    )


@app.before_request
async def before_request():
    g.db = connect_db()


def worker_process_setup_hook():
    from apitofsim.api import ureg
    from pint import set_application_registry

    set_application_registry(ureg)


@app.while_serving
async def lifespan():
    my_ray = await to_thread.run_sync(
        partial(
            ray.init,
            address=environ.get("RAY_ADDRESS", "local"),
            log_to_driver=False,
            runtime_env={
                "pip": ["jinja2", "minify-html-onepass"],
                "worker_process_setup_hook": worker_process_setup_hook,
            },
        )
    )
    print("Dashboard url", my_ray.dashboard_url)
    # XXX: This complains about blocking in debug mode on shutdown since the shutdown blocks
    # Maybe try to get ray to add an __aexit__ method?
    with my_ray:
        yield


"""
def vibrations_plot(particle):
    from matplotlib import pyplot as plt
    from io import StringIO

    if particle.vibrational_temperatures is None:
        return

    plt.figure()
    plt.hlines(1, 1, 20)
    plt.eventplot(
        particle.vibrational_temperatures, orientation="horizontal", colors="b"
    )
    plt.axis("off")
    f = StringIO()
    plt.savefig(f, format="svg")
    particle["vibrations_plot"] = f.getvalue()


for config in DATA.values():
    for pathway in config["pathways"]:
        for particle in pathway:
            vibrations_plot(particle)
"""


jinja_loader = app.jinja_loader


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


def setup_result_db(database_path):
    from tempfile import mkdtemp

    import apitofsim.workflow.sql_files as sql_files
    import duckdb
    from apitofsim.workflow.db import RealizationDatabase

    results_path = mkdtemp(prefix="apitofsim") + "/results.duckdb"
    duckdb_db = duckdb.connect()
    duckdb_db.execute(f"ATTACH '{database_path}' AS clusters_db (READ_ONLY);")
    duckdb_db.execute(f"ATTACH '{results_path}' AS results_db;")
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

    return RealizationDatabase(((duckdb_db, None))), results_path


@ray.remote
def run_simulation(
    voltage, pathways, gas, quadrupole, histograms, config, database_path
):
    from time import sleep

    from apitofsim.api import (
        MassSpecIntermediateCounter,
        MassSpecLogItem,
        MassSpectrometer,
        mass_spec_iter,
    )

    # from apitofsim.workflow.db import EventRecorder
    from apitofsim.workflow.runners import DerivedDataPreparer
    from jinja2 import Environment
    from minify_html_onepass import minify

    db, results_db_path = setup_result_db(database_path)
    config_id = db.insert_config("webrun", config)
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
            # event_recorder = EventRecorder(db, group["pathway_ids"])
            with mass_spec_iter(
                mass_spec,
                subs,
                realizations,
                sample_mode=2,
                strict=True,
            ) as it:
                for result in it:
                    if isinstance(result, MassSpecIntermediateCounter):
                        counters = result.counters
                        survived = counters.n_fragmented_total.sum()
                        fragmented = counters.n_escaped_total
                        iterations = survived + fragmented
                        yield update_pane("apitof")
                        current_run_id = db.insert_run(config_id)
                        # experiment_result_id =
                        db.record_result(
                            current_run_id,
                            counters,
                            cluster_id=group["cluster_id"],
                            pathway_ids=group["pathway_ids"],
                        )
                    elif isinstance(result, MassSpecLogItem):
                        log.append(f"{result.type}: {result.name}")
                        yield update_pane("apitof")
            break
    statuses["apitof"] = "done"
    yield update_pane("tabs")


def start_job(job_id):
    info = status[job_id]  # type: ignore

    simulation_call = run_simulation.remote(
        **info["arguments"], database_path=database_path
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


@app.template_filter("join_abbrv")
def join_abbrv_filter(s, sep="<br>"):
    from itertools import chain

    def joined(s):
        return sep.join((str(e) for e in s))

    if len(s) <= 5:
        return joined(s)
    else:
        return joined(chain(s[:2], ["..."], s[-2:]))


@app.route("/", methods=["GET", "POST"])
async def settings():
    form = await SettingsForm.create_form()
    if typing.TYPE_CHECKING:
        form = typing.cast(SettingsForm, form)
    if await form.validate_on_submit():
        arguments = form.get_data()
        job_id = uuid4()
        status[job_id.hex] = {  # type: ignore
            "status": "pending",
            "arguments": arguments,
            "last_update": {},
        }
        pump_jobs()
        return redirect("/analysis?jobid=" + job_id.hex)
    return await render_template(
        "settings/settings.html",
        form=form,
        primary_cluster=None,
    )


def hypothetical_spectrogram(cluster_ids, masses, max_mass=None):
    if max_mass is None:
        max_mass = g.db.db.sql("select max(atomic_mass) from cluster").fetchone()[0]
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


@app.route("/fragments/pathways")
async def pathways_fragment():
    form = await SettingsForm.create_form()
    if typing.TYPE_CHECKING:
        form = typing.cast(SettingsForm, form)
    if "cluster" not in request.args:
        abort(400, description="Missing cluster parameter")

    cluster_id = int(request.args["cluster"])
    if cluster_id is None:
        abort(400, description="Invalid cluster parameter")
    relevant_cluster_ids = g.db.db.sql(
        """
        select distinct unnest([cluster_id, product1_id, product2_id]) as relevant_cluster_id
        from pathway
        where cluster_id = ?
        """,
        params=(cluster_id,),
    ).fetchdf()
    cluster_df = (
        g.db.db.table("cluster")
        .join(
            g.db.db.from_df(relevant_cluster_ids).set_alias("relevant"),
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
        enrich_cluster(g.db.ase_db, cluster)
        clusters[cluster["id"]] = cluster
    pathways_relations = g.db.db.sql(
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

    return await render_template(
        "settings/_render_pathways.html",
        pathways=pathways,
        clusters=clusters.values(),
        mass_spectrogram=hypothetical_spectrogram(cluster_ids, masses),
        primary_cluster=(cluster_id, clusters[cluster_id]["common_name"]),
    )


@app.route("/fragments/hypothetical-spectrogram")
async def hypothetical_spectrogram_fragment():
    form = SettingsForm(request.args)
    if typing.TYPE_CHECKING:
        form = typing.cast(SettingsForm, form)
    if not form.pathways.validate(form) or not form.cluster.validate(form):
        abort(400, description="Invalid pathways data")
    # cluster_id = int(form.cluster.data)
    pathway_ids = numpy.array(
        [
            int(pathway["pathway"])
            for pathway in form.pathways.data
            if pathway["enabled"]
        ]
    )
    cluster_infos = (
        g.db.clusters_query(pathways=pathway_ids).select("id, atomic_mass").fetchnumpy()
    )
    return hypothetical_spectrogram(cluster_infos["id"], cluster_infos["atomic_mass"])


@app.route("/fragments/instrument")
async def instrument_fragment():
    instrument = request.args.get("instrument")
    if instrument == "custom":
        return await render_template(
            "settings/_render_instrument.html",
            form=CustomInstrumentForm(prefix="instrument-"),
        )
    elif instrument == "default3000":
        return await render_template(
            "settings/_render_instrument.html",
            form=BuiltInInstrumentForm(prefix="instrument-"),
        )
    else:
        abort(400, description="Invalid instrument parameter")


def process_jobid(request):
    jobid = request.args.get("jobid")
    if jobid is None:
        abort(400, description="Missing jobid parameter")
    if jobid not in status:  # type: ignore
        abort(404, description="Job ID not found")
    return jobid


@app.route("/analysis")
async def analysis():
    jobid = process_jobid(request)
    return await render_template(
        "analysis/analysis.html",
        jobid=jobid,
        statuses={
            "queue": "processing",
            "skimmer": "pending",
            "densityandr": "pending",
            "apitof": "pending",
        },
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


@app.route("/analysis/updates")
async def update_analysis():
    if "text/event-stream" not in request.accept_mimetypes:
        abort(400)

    jobid = process_jobid(request)
    print("jobid", jobid)
    print(status)

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
                info["last_update"][event] = data
                for bit in sse_safe(data, event):
                    yield bit

    response = await make_response(
        send_events(),
        {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Transfer-Encoding": "chunked",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    response.timeout = None  # type: ignore
    return response


if __name__ == "__main__":
    anyio.run(app.run_task())
