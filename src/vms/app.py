import duckdb
import ray
import asyncio
from dataclasses import dataclass
import typing
import numpy
import os
from os import environ
from quart import Quart, render_template, redirect, request, abort, make_response, g
from .forms import SettingsForm, BuiltInInstrumentForm, CustomInstrumentForm
from io import BytesIO
import matplotlib
import holoviews as hv
from lxml import etree

from uuid import uuid4

from pint import UnitRegistry, set_application_registry

matplotlib.use("SVG")
hv.extension("matplotlib")  # type: ignore

ureg = UnitRegistry()
set_application_registry(ureg)

app = Quart(__name__)
app.config["SECRET_KEY"] = "a-secret-key"

status = {}


@app.before_request
async def before_request():
    g.db = duckdb.connect(database=environ["DATABASE"])


@app.while_serving
async def lifespan():
    my_ray = await asyncio.to_thread(
        ray.init,
        address=environ.get("RAY_ADDRESS", "local"),
        log_to_driver=False,
        runtime_env={"pip": ["jinja2", "minify-html-onepass"]},
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


@ray.remote
def run_simulation(voltage, pathways, gas, config):
    from time import sleep
    from random import random
    from jinja2 import Environment
    from minify_html_onepass import minify
    from apitofsim import skimmer_pandas

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

    survived = 0
    fragmented = 0
    iterations = 0

    def render_template(path):
        return minify(
            jinja_env.get_template(path).render(
                processing=processing,
                statuses=statuses,
                queue=queue,
                skimmer=skimmer,
                densityandr=densityandr,
                apitof=apitof,
                survived=survived,
                fragmented=fragmented,
                realizations=1000,
                iterations=iterations,
                ratio=survived / iterations if iterations > 0 else 0,
            )
        )

    def update_pane(message):
        return message.encode("utf-8"), render_template(f"analysis/{message}.html")

    # def update_all():
    # return b"body", render_template("analysis/body.html")

    df = rhos = k_rate = None
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
            df = skimmer_pandas(
                config[c]
                for c in (
                    "T",
                    "pressure_first",
                    "Lsk",
                    "dc",
                    "alpha_factor",
                    "m_gas",
                    "ga",
                    "N_iter",
                    "M_iter",
                    "resolution",
                    "tolerance",
                )
            )
            skimmer = df.to_html()
            yield update_pane("skimmer")
            statuses["skimmer"] = "done"
            statuses["densityandr"] = "processing"
            processing = "densityandr"
            yield update_pane("tabs")
        elif processing == "densityandr":
            densityandr += "Calculating density and rate...<br>"
            yield update_pane("densityandr")
            rhos, k_rate = densityandrate(
                *clusters,
                *(
                    config[setting]
                    for setting in (
                        "energy_max",
                        "energy_max_rate",
                        "bin_width",
                        "bonding_energy",
                    )
                ),
            )
            densityandr = "Done"
            yield update_pane("densityandr")
            statuses["densityandr"] = "done"
            statuses["apitof"] = "processing"
            processing = "apitof"
            yield update_pane("tabs")
        elif processing == "apitof":
            yield update_pane("tabs")
            for _ in range(1, realizations + 1):
                yield update_pane("apitof")
                sleep(2)
                iterations += 1
                if random() < 0.5:
                    survived += 1
                else:
                    fragmented += 1
            break


def start_job(job_id):
    info = status[job_id]  # type: ignore

    simulation_call = run_simulation.remote(**info["arguments"])
    new_info = {
        **info,  # type: ignore
        "status": "running",
        "task": simulation_call,
        "aiter": aiter(simulation_call),
    }
    status[job_id] = new_info  # type: ignore
    return new_info


def pump_jobs():
    import time

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
        max_mass = g.db.sql("select max(atomic_mass) from cluster").fetchone()[0]
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
    relevant_cluster_ids = g.db.sql(
        """
        select distinct unnest([cluster_id, product1_id, product2_id]) as relevant_cluster_id
        from pathway
        where cluster_id = ?
        """,
        params=(cluster_id,),
    ).fetchdf()
    cluster_df = (
        g.db.table("cluster")
        .join(
            g.db.from_df(relevant_cluster_ids).set_alias("relevant"),
            condition="relevant.relevant_cluster_id = cluster.id",
        )
        .fetchdf()
    )
    cluster_ids = cluster_df["id"].to_numpy()
    masses = cluster_df["atomic_mass"].to_numpy()
    clusters = {}
    for cluster in cluster_df.itertuples():
        clusters[cluster.id] = cluster
    pathways_relations = g.db.sql(
        """select * from pathway where cluster_id = ?""",
        params=(cluster_id,),
    ).fetchdf()
    pathways = []

    for pathway in pathways_relations.itertuples():
        cluster = clusters[pathway.cluster_id]
        product1 = clusters[pathway.product1_id]
        product2 = clusters[pathway.product2_id]
        bonding_energy = (
            product1.electronic_energy
            + product2.electronic_energy
            - cluster.electronic_energy
        )

        pathways.append(
            {
                "cluster": (pathway.cluster_id, cluster.common_name),
                "product1": (pathway.product1_id, product1.common_name),
                "product2": (pathway.product2_id, product2.common_name),
                "bonding_energy": bonding_energy,
                "form": form.pathways.append_entry({"pathway": pathway.id}),
            }
        )

    return await render_template(
        "settings/_render_pathways.html",
        pathways=pathways,
        clusters=clusters.values(),
        mass_spectrogram=hypothetical_spectrogram(cluster_ids, masses),
        primary_cluster=(cluster_id, clusters[cluster_id].common_name),
    )


@app.route("/fragments/hypothetical-spectrogram")
async def hypothetical_spectrogram_fragment():
    form = SettingsForm(request.args)
    if typing.TYPE_CHECKING:
        form = typing.cast(SettingsForm, form)
    if not form.pathways.validate(form) or not form.cluster.validate(form):
        abort(400, description="Invalid pathways data")
    cluster_id = int(form.cluster.data)
    pathway_ids = numpy.array(
        [
            int(pathway["pathway"])
            for pathway in form.pathways.data
            if pathway["enabled"]
        ]
    )
    pathway_ids = g.db.table("pathway_ids").set_alias("selected_pathways")
    relevant_cluster_ids = (
        g.db.table("pathway")
        .join(pathway_ids, condition="selected_pathways.column0 = pathway.id")
        .select(
            "distinct unnest([cluster_id, product1_id, product2_id]) as relevant_cluster_id"
        )
        .fetchnumpy()
    )["relevant_cluster_id"]
    if cluster_id not in relevant_cluster_ids:
        relevant_cluster_ids = numpy.append(relevant_cluster_ids, [cluster_id])
    cluster_infos = (
        g.db.table("cluster")
        .join(
            g.db.table("relevant_cluster_ids").set_alias("relevant"),
            condition="relevant.column0 = cluster.id",
        )
        .select("id, atomic_mass")
        .fetchnumpy()
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
                await asyncio.sleep(1)
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
                except StopIteration:
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
    asyncio.run(app.run_task())
