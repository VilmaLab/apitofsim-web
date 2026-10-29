import os
import tempfile

os.environ["MPLCONFIGDIR"] = tempfile.mkdtemp()
os.environ["RAY_DISABLE_FAILURE_SIGNAL_HANDLER"] = "1"

import os
from datetime import datetime, timezone
from io import BytesIO
from os import environ
from pathlib import Path
from uuid import uuid4

import holoviews as hv
import matplotlib
import numpy
from apitofsim.api import ureg
from apitofsim.transmission import TRANSMISSION_MODELS
from lxml import etree
from pint import set_application_registry
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette_wtf import CSRFProtectMiddleware, csrf_protect, csrf_token

from .cluster_info import enrich_cluster
from .db import DatabaseMiddleware, get_connection
from .forms import (
    INSTRUMENT_FORMS,
    SettingsForm,
    instrument_form_class,
    settings_errors,
)
from .jobs import get_status_dict, update_analysis
from .resources import bokeh_application, lifespan, templates
from .settings import database_path, results_path

matplotlib.use("SVG")
hv.extension("matplotlib")  # type: ignore

set_application_registry(ureg)
status = get_status_dict()


def render_template(request, path, **context):
    return templates.TemplateResponse(request, path, context)


@csrf_protect
async def settings(request):
    from time import time

    form = await SettingsForm.from_formdata(request)
    if await form.validate_on_submit():
        arguments = form.get_data()
        job_id = uuid4()
        result_db_path = Path(results_path).with_name(f"{job_id.hex}.duckdb")
        result_db_path.parent.mkdir(parents=True, exist_ok=True)
        status[job_id.hex] = {  # type: ignore
            "status": "pending",
            "owner": request.session.setdefault("job_owner", uuid4().hex),
            "cluster_label": get_connection()
            .clusters_df(parent=int(form.cluster.data))
            .set_index("id")
            .loc[int(form.cluster.data), "common_name"],
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "counts": {"requested": int(arguments["config"]["N"]), "completed": 0},
            "arguments": arguments,
            "result_db_path": str(result_db_path),
            "last_update": {},
            "start_time": time(),
        }
        await request.app.state.job_sender.send((job_id.hex, status[job_id.hex]))
        return RedirectResponse(f"/analysis?jobid={job_id.hex}#queue", status_code=303)
    context = {"primary_cluster": None}
    if (
        request.method == "POST"
        and not form.cluster.errors
        and form.cluster.data not in (None, "None", "")
    ):
        context = pathways_context(form, int(form.cluster.data), submitted=True)
    form_errors, field_errors = settings_errors(form)
    return render_template(
        request,
        "settings/settings.html",
        form=form,
        form_errors=form_errors,
        field_errors=field_errors,
        **context,
    )


def hypothetical_spectrogram(cluster_ids, masses, max_mass=None):
    if max_mass is None:
        max_mass = (
            get_connection()
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
    return render_template(
        request, "settings/_render_pathways.html", **pathways_context(form, cluster_id)
    )


def pathways_context(form, cluster_id, *, submitted=False):
    db = get_connection()
    cluster_df = db.clusters_df(parent=cluster_id)
    cluster_ids = cluster_df["id"].to_numpy()
    masses = cluster_df["atomic_mass"].to_numpy()
    clusters = {}
    for cluster in cluster_df.itertuples():
        cluster = cluster._asdict()
        enrich_cluster(db.ase_db, cluster)
        clusters[cluster["id"]] = cluster
    pathways_relations = db.pathways_query(parent=cluster_id).fetchdf()
    pathways = []

    submitted_entries = (
        {entry.form.pathway.data: entry.form for entry in form.pathways}
        if submitted
        else {}
    )
    if not submitted:
        # FieldList's placeholder is not a real pathway and must not be submitted.
        while form.pathways.entries:
            form.pathways.pop_entry()
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
                "form": submitted_entries.get(str(pathway.id))
                if submitted
                else form.pathways.append_entry({"pathway": pathway.id}).form,
            }
        )

    return dict(
        pathways=[pathway for pathway in pathways if pathway["form"] is not None],
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
        get_connection()
        .clusters_query(pathways=pathway_ids)
        .select("id, atomic_mass")
        .fetchnumpy()
    )
    return HTMLResponse(
        hypothetical_spectrogram(cluster_infos["id"], cluster_infos["atomic_mass"])
    )


async def instrument_fragment(request):
    model = request.query_params.get("instrument_model")
    if model not in INSTRUMENT_FORMS:
        raise HTTPException(400, detail="Invalid instrument parameter")
    return render_template(
        request,
        "settings/_render_instrument.html",
        form=instrument_form_class(model)(prefix="instrument-"),
    )


@csrf_protect
async def cancel_analysis(request):
    from .jobs import process_jobid
    from .jobs.queue import request_cancellation

    jobid = process_jobid(request)
    info = status[jobid]
    if info.get("owner") is None or info["owner"] != request.session.get("job_owner"):
        raise HTTPException(403)
    request_cancellation(info)
    return RedirectResponse(f"/analysis?jobid={jobid}", status_code=303)


async def jobs(request):
    owner = request.session.get("job_owner")
    entries = [
        {**info, "jobid": jobid}
        for jobid, info in status.items()
        if owner is not None
        and info.get("owner") == owner
        and info["status"] != "cancelled"
    ]
    entries.sort(key=lambda info: info["start_time"], reverse=True)
    return render_template(
        request,
        "jobs/list.html" if request.url.path.endswith("/list") else "jobs/jobs.html",
        jobs=entries,
        active=any(info["status"] in ("pending", "running") for info in entries),
    )


async def analysis(request):
    from vms.jobs.cancellation import cancelled_statuses
    from vms.jobs.failures import failure_statuses

    from .jobs import process_jobid

    jobid = process_jobid(request)
    info = status[jobid]
    completed = "result" in info
    can_cancel = info.get("owner") is not None and info.get(
        "owner"
    ) == request.session.get("job_owner")
    failure = info.get("failure")
    return render_template(
        request,
        "analysis/analysis.html",
        jobid=jobid,
        info=info,
        can_cancel=can_cancel,
        cancel_token=csrf_token(request) if can_cancel else None,
        completed=completed,
        failure=failure,
        job_status=info["status"],
        statuses=(
            failure_statuses(failure)
            if failure
            else cancelled_statuses(info)
            if "cancelled_stage" in info
            else {"queue": "done", "skimmer": "done", "apitof": "done"}
            if completed
            else {"queue": "processing", "skimmer": "pending", "apitof": "pending"}
        ),
        infos={
            "queue": "Queuing",
            "skimmer": "",
            "densityandr": "",
            "apitof": "",
        },
        queue=None,
        jobs_ahead=info.get("jobs_ahead"),
        survived=0,
        fragmented=0,
        realizations=0,
        iterations=0,
        ratio=0,
    )


def prepare_plot(request, plot, jobid=None, **kwargs):
    if jobid is None:
        jobid = request.path_params["jobid"]
    info = status.get(jobid)
    if info is None or "result" not in info:
        raise HTTPException(404)
    from bokeh.embed import server_document

    result = info["result"]
    plot_url = str(request.url_for("bokeh", path=f"/{plot}"))
    return server_document(plot_url, arguments={"jobid": jobid, **result, **kwargs})


async def explorer_plot(request):
    plot_script = prepare_plot(request, "explorer")
    return render_template(
        request,
        "analysis/plot.html",
        plot="explorer",
        plot_script=plot_script,
    )


async def transmission(request):
    jobid = request.path_params["jobid"]
    model = request.path_params["model"]
    plot_script = prepare_plot(
        request, "spectrogram", jobid=jobid, transmission_model=model
    )
    return render_template(
        request,
        "analysis/transmission.html",
        model=model,
        jobid=jobid,
        plot_script=plot_script,
    )


async def transmissioncalc(request):
    from apitofsim.plotting.spectrogram import get_intensities
    from apitofsim.transmission import transform_intensity

    from vms.result_plots import open_result_db

    jobid = request.path_params.get("jobid")
    model = request.path_params.get("model")

    if jobid is None or model is None:
        raise HTTPException(404)
    job = status.get(jobid)
    if job is None:
        raise HTTPException(404)
    result = job.get("result")
    experiment = result["experiment"]
    cluster = result["cluster"]
    db = open_result_db(database_path, job["result_db_path"])
    df = get_intensities(
        db,
        experiment_id=experiment,
        cluster_id=cluster,
        is_single_pathway=False,
    )
    transform_intensity(df, model)
    counts = {
        "input_clusters": None,
        "output_clusters_total": None,
        "output_clusters": [None] * len(df),
    }
    intensities = df["intensity"].to_numpy()
    total_intensity = intensities.sum()
    if request.query_params.get("input_clusters"):
        counts["input_clusters"] = float(request.query_params["input_clusters"])
    if request.query_params.get("output_clusters_total"):
        counts["output_clusters_total"] = float(
            request.query_params["output_clusters_total"]
        )

    if counts["input_clusters"] is not None:
        counts["output_clusters_total"] = counts["input_clusters"] * total_intensity
    elif counts["output_clusters_total"] is not None and total_intensity:
        counts["input_clusters"] = counts["output_clusters_total"] / total_intensity

    clusters = []
    for _, row in df.iterrows():
        if row["intensity"] == 0:
            continue
        clusters.append(
            {
                "cluster": row["product_name"],
                "mass": row["atomic_mass"],
                "count": (
                    counts["input_clusters"] * row["intensity"]
                    if counts["input_clusters"] is not None
                    else None
                ),
            }
        )

    return render_template(
        request,
        "analysis/transmissioncalc.html",
        counts=counts,
        clusters=clusters,
        jobid=jobid,
        model=model,
    )


async def spectrogram_plot(request):
    # Get result db
    from vms.result_plots import open_result_db

    jobid = request.path_params["jobid"]
    job = status.get(jobid)
    if job is None:
        raise HTTPException(404)
    db = open_result_db(database_path, job["result_db_path"])
    result = job["result"]
    experiment = list(db.iter_configs())
    if len(experiment) != 1:
        raise HTTPException(
            500, detail="Result database has unexpected number of configs"
        )
    experiment = experiment[0]
    voltages = experiment.config["voltages"]
    cluster_charge = (
        db.experiment_clusters_query(result["experiment"], result["cluster"])
        .select("cluster_charge")
        .fetchone()[0]
    )
    plot_script = prepare_plot(request, "spectrogram")
    return render_template(
        request,
        "analysis/spectrogram.html",
        jobid=jobid,
        plot_script=plot_script,
        transmission_models=TRANSMISSION_MODELS,
        cluster_charge=cluster_charge,
        voltages=voltages,
    )


app = Starlette(
    routes=[
        Route("/", settings, methods=["GET", "POST"]),
        Route("/fragments/pathways", pathways_fragment),
        Route("/fragments/hypothetical-spectrogram", hypothetical_spectrogram_fragment),
        Route("/fragments/instrument", instrument_fragment),
        Route("/jobs", jobs),
        Route("/jobs/list", jobs),
        Route("/analysis", analysis),
        Route("/analysis/cancel", cancel_analysis, methods=["POST"]),
        Route("/analysis/updates", update_analysis),
        Route("/analysis/results/{jobid}/explorer", explorer_plot, name="explorer"),
        Route(
            "/analysis/results/{jobid}/spectrogram",
            spectrogram_plot,
            name="spectrogram",
        ),
        Route(
            "/analysis/results/{jobid}/transmission/{model}",
            transmission,
            name="transmission",
        ),
        Route(
            "/analysis/results/{jobid}/transmission/{model}/calc",
            transmissioncalc,
            name="transmissioncalc",
        ),
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
