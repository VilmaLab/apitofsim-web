from contextvars import ContextVar

from apitofsim.workflow.db import SuperClusterDatabase, guess_ase_db_filename

from vms.settings import database_path

current_db = ContextVar("current_db")


def connect_db():
    return SuperClusterDatabase(
        database_path, ase_filename=guess_ase_db_filename(database_path), readonly=True
    )


def get_connection():
    return current_db.get()


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
