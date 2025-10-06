import ray
import asyncio
from dataclasses import dataclass
import typing
import os
from quart import (
    Quart,
    render_template,
    redirect,
    request,
    abort,
    make_response,
)
from .forms import SettingsForm, BuiltInInstrumentForm, CustomInstrumentForm
from .utils import parse_config_list
from uuid import uuid4


app = Quart(__name__)
app.config["SECRET_KEY"] = "a-secret-key"

status = {}
CHAINS = parse_config_list(os.environ["CHAINS"])


@app.while_serving
async def lifespan():
    my_ray = await asyncio.to_thread(
        ray.init,
        address="local",
        include_dashboard=True,
        runtime_env={"pip": ["jinja2", "minify-html-onepass"]},
    )
    print("Dashboard url", my_ray.dashboard_url)
    # XXX: This complains about blocking in debug mode on shutdown since the shutdown blocks
    # Maybe try to get ray to add an __aexit__ method?
    with my_ray:
        yield


def vibrations_plot(particle):
    from matplotlib import pyplot as plt
    from io import StringIO

    if particle["vibrational_temperatures"] is None:
        return

    plt.figure()
    plt.hlines(1, 1, 20)
    plt.eventplot(
        particle["vibrational_temperatures"], orientation="horizontal", colors="b"
    )
    plt.axis("off")
    f = StringIO()
    plt.savefig(f, format="svg")
    particle["vibrations_plot"] = f.getvalue()


for chain in CHAINS.values():
    for particle in ["cluster", "first_product", "second_product"]:
        vibrations_plot(chain[particle])


jinja_loader = app.jinja_loader
print(jinja_loader)


@ray.remote
def run_simulation(realizations):
    from time import sleep
    from random import random
    from jinja2 import Environment
    from minify_html_onepass import minify

    jinja_env = Environment(loader=jinja_loader)

    print("run_simulation")

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
                realizations=realizations,
                iterations=iterations,
                ratio=survived / iterations if iterations > 0 else 0,
            )
        )

    def update_pane(message):
        return message.encode("utf-8"), render_template(f"analysis/{message}.html")

    # def update_all():
    # return b"body", render_template("analysis/body.html")

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
            sleep(1)
            skimmer += "Skimming...<br>"
            yield update_pane("skimmer")
            sleep(1)
            skimmer = "Skimmed after XXXs<br>"
            yield update_pane("skimmer")
            sleep(1)
            skimmer += "#Distance / Velocity / Temperature / Pressure / GasMassDensity / SpeedOfSound"
            yield update_pane("skimmer")
            statuses["skimmer"] = "done"
            statuses["densityandr"] = "processing"
            processing = "densityandr"
            yield update_pane("tabs")
        elif processing == "densityandr":
            yield update_pane("densityandr")
            sleep(1)
            densityandr += "Calculating density and rate...<br>"
            yield update_pane("densityandr")
            sleep(1)
            densityandr = "Calculated density and rate after XXXs<br>"
            densityandr += "Density 1, 2, 3, comb, rate constant"
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

    simulation_call = run_simulation.remote(info["realizations"])
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
        job_id = uuid4()
        status[job_id.hex] = {  # type: ignore
            "status": "pending",
            "realizations": form.simulation.realizations.data,
            "last_update": {},
        }
        pump_jobs()
        return redirect("/analysis?jobid=" + job_id.hex)
    return await render_template(
        "settings/settings.html",
        form=form,
        chain=CHAINS[form.pathways.chain.data],
    )


@app.route("/fragments/chain")
async def chain_fragment():
    chain = request.args.get("chain")
    if chain in CHAINS:
        return await render_template("_render_chain.html", chain=chain)
    else:
        abort(400, description="Invalid chain parameter")


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
    asyncio.run(app.run_task(debug=True))
