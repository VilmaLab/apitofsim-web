from contextvars import ContextVar

import duckdb
from apitofsim.workflow import sql_files_nofk as sql
from apitofsim.workflow.db import (
    ExperimentDatabase,
    SuperClusterDatabase,
    guess_ase_db_filename,
)

from vms.settings import database_path

current_db = ContextVar("current_db")


def connect_db():
    return SuperClusterDatabase(
        database_path, ase_filename=guess_ase_db_filename(database_path), readonly=True
    )


def get_connection():
    return current_db.get()


def open_result_db(cluster_path, result_path, *, readonly=True):
    """Open a job's results and event archive alongside the shared cluster data."""
    clusters = str(cluster_path).replace("'", "''")
    results = str(result_path).replace("'", "''")
    access = " (READ_ONLY)" if readonly else ""
    connection = duckdb.connect()
    try:
        connection.execute(f"ATTACH '{clusters}' AS clusters_db (READ_ONLY)")
        connection.execute(f"ATTACH '{results}' AS results_db{access}")
        connection.execute("USE results_db")
        connection.execute("SET search_path = 'results_db,clusters_db'")
        if not readonly:
            # Read cluster and histogram data from the input database. New
            # preparation caches must be writable even when the inputs are old.
            connection.execute(
                "\n".join(
                    [
                        sql.vib_energy_index,
                        sql.rf_basis,
                        sql.fragmentation_dos,
                        sql.experiment,
                        sql.event_recording,
                    ]
                )
            )
        db = ExperimentDatabase(
            (connection, None),
            readonly=readonly,
            record_events=not readonly,
            recording_format="parquet",
            recording_compression="zstd",
            _database_path=result_path,
        )
        db.create_temp_views()
        return db
    except BaseException:
        connection.close()
        raise


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
