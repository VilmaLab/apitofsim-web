from contextvars import ContextVar

import duckdb
from apitofsim.workflow import DatabasePaths, ExperimentDatabase, SuperClusterDatabase
from apitofsim.workflow import sql_files_nofk as sql

from vms.settings import database_path

current_db = ContextVar("current_db")


def connect_db():
    return SuperClusterDatabase(database_path, readonly=True, ase=True)


def get_connection():
    return current_db.get()


def open_result_db(cluster_path, result_path, *, readonly=True):
    """Open a job's results and event archive alongside the shared cluster data."""
    clusters = DatabasePaths(cluster_path).require_database()
    results = DatabasePaths(result_path)
    if readonly:
        results.require_database()
    else:
        results.root.parent.mkdir(parents=True, exist_ok=True)
        results.create()
    clusters_file = str(clusters).replace("'", "''")
    results_file = str(results.duckdb).replace("'", "''")
    access = " (READ_ONLY)" if readonly else ""
    config = {"temp_directory": str(results.root / "database.duckdb.tmp")}
    connection = duckdb.connect() if readonly else duckdb.connect(config=config)
    try:
        connection.execute(f"ATTACH '{clusters_file}' AS clusters_db (READ_ONLY)")
        connection.execute(f"ATTACH '{results_file}' AS results_db{access}")
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
            database_paths=results,
        )
        db.create_temp_views()
        if readonly:
            results.require_database()
        return db
    except BaseException as exc:
        connection.close()
        if readonly and isinstance(exc, duckdb.Error) and not results.duckdb.is_file():
            raise FileNotFoundError(result_path) from exc
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
