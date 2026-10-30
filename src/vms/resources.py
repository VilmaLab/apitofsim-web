import os
import tempfile

os.environ["MPLCONFIGDIR"] = tempfile.mkdtemp()
os.environ["RAY_DISABLE_FAILURE_SIGNAL_HANDLER"] = "1"
os.environ.setdefault("RAY_AUTH_MODE", "token")

import os
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from os import environ
from pathlib import Path

import ray
from anyio import to_thread
from apitofsim.api import ureg
from starlette.templating import Jinja2Templates

from .assets import ASSETS, asset_version
from .jobs.queue import scheduler_context
from .markdown_ext import MarkdownExtension
from .pintweb.alpine import quantity_display
from .ray_auth import local_ray_auth
from .result_plots import make_bokeh_app
from .settings import database_path

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals["asset_version"] = asset_version
jinja_loader = templates.env.loader
bokeh_application = make_bokeh_app(database_path)


def add_jinja_extensions(env):
    env.add_extension(MarkdownExtension)
    env.filters["sign"] = lambda x: "+" if x > 0 else "-" if x < 0 else "0"


add_jinja_extensions(templates.env)


def worker_process_setup_hook():
    from pint import set_application_registry

    set_application_registry(ureg)


def compose_async_ctx(*ctx_managers):
    @asynccontextmanager
    async def composed_async_context_managers(*args, **kwargs):
        async with AsyncExitStack() as stack:
            for manager in ctx_managers:
                await stack.enter_async_context(manager(*args, **kwargs))
            yield

    return composed_async_context_managers


@asynccontextmanager
async def ensure_assets_context(app):
    await to_thread.run_sync(ASSETS.ensure_built)
    yield


project_dir = Path(__file__).resolve().parents[2]


def make_runtime_env():
    runtime_env = {"worker_process_setup_hook": worker_process_setup_hook}
    # A driver started with `uv run` also runs its workers under `uv run`, which
    # then syncs the worker environment from the project. Package the project
    # explicitly so workers see uv.lock and install the same versions as the
    # driver instead of resolving fresh ones.
    if (project_dir / "pyproject.toml").is_file():
        runtime_env["working_dir"] = str(project_dir)
    if os.environ.get("RAY_USE_PIP") == "1":
        runtime_env["pip"] = ["jinja2", "minify-html-onepass"]
    return runtime_env


@asynccontextmanager
async def ray_context(app):
    address = environ.get("RAY_ADDRESS", "local")
    with local_ray_auth(address):
        my_ray = await to_thread.run_sync(
            partial(
                ray.init,
                address=address,
                log_to_driver=False,
                runtime_env=make_runtime_env(),
            )
        )
        with my_ray:
            yield


@asynccontextmanager
async def bokeh_context(app):
    await bokeh_application.core.start()
    try:
        yield
    finally:
        await bokeh_application.core.stop()


lifespan = compose_async_ctx(
    ensure_assets_context, ray_context, scheduler_context, bokeh_context
)


def join_abbrv_filter(s, sep="<br>"):
    from itertools import chain

    def joined(s):
        return sep.join((str(e) for e in s))

    if len(s) <= 5:
        return joined(s)
    else:
        return joined(chain(s[:2], ["..."], s[-2:]))


templates.env.filters["join_abbrv"] = join_abbrv_filter
templates.env.filters["quantity_display"] = quantity_display
