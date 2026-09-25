"""Serve resultviewer Bokeh plots from completed simulation databases."""

import duckdb
from apitofresview.webapp import explorer_bokeh, spectrogram_bokeh
from apitofsim.workflow.db import RealizationDatabase
from bokeh.server.asgi import BokehASGI


def open_result_db(cluster_path, result_path):
    cluster_path = str(cluster_path).replace("'", "''")
    result_path = str(result_path).replace("'", "''")
    connection = duckdb.connect()
    connection.execute(f"ATTACH '{cluster_path}' AS clusters_db (READ_ONLY)")
    connection.execute(f"ATTACH '{result_path}' AS results_db (READ_ONLY)")
    connection.execute("USE results_db")
    connection.execute("SET search_path = 'results_db,clusters_db'")
    return RealizationDatabase(((connection, None)))


def make_bokeh_app(jobs, cluster_path):
    databases = {}

    def database_for(doc):
        args = doc.session_context.request.arguments
        values = args.get("jobid", [])
        if len(values) != 1:
            raise ValueError("Missing job ID")
        jobid = values[0].decode()
        job = jobs.get(jobid)
        if job is None or "result" not in job:
            raise ValueError("Results are not available")
        for name, value in job["result"].items():
            if args.get(name) != [str(value).encode()]:
                raise ValueError("Invalid plot selection")
        if jobid not in databases:
            databases[jobid] = open_result_db(cluster_path, job["result_db_path"])
        return databases[jobid]

    def explorer(doc):
        explorer_bokeh(database_for(doc), doc)

    def spectrogram(doc):
        spectrogram_bokeh(database_for(doc), doc)

    return BokehASGI({"/explorer": explorer, "/spectrogram": spectrogram})
