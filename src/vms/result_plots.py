"""Serve resultviewer Bokeh plots from completed simulation databases."""

import duckdb
from apitofresview.webapp import explorer_bokeh, spectrogram_bokeh
from apitofsim.workflow.db import RealizationDatabase
from bokeh.models import InlineStyleSheet
from bokeh.server.asgi import BokehASGI


def open_result_db(cluster_path, result_path):
    cluster_path = str(cluster_path).replace("'", "''")
    result_path = str(result_path).replace("'", "''")
    connection = duckdb.connect()
    connection.execute(f"ATTACH '{cluster_path}' AS clusters_db (READ_ONLY)")
    connection.execute(f"ATTACH '{result_path}' AS results_db (READ_ONLY)")
    connection.execute("SET search_path = 'results_db,clusters_db'")
    db = RealizationDatabase(((connection, None)))
    db.create_temp_views()
    connection.execute("USE results_db")
    connection.execute("SET search_path = 'results_db,clusters_db'")
    return db


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
        frame = doc.select_one({"css_classes": ["explorer-bokeh-frame"]})
        if frame is None:
            return
        layout = frame.children[0]
        sidebar = layout.children[0]
        layout.styles = {**layout.styles, "min-height": "calc(100vh - 2rem)"}
        sidebar.height_policy = "max"
        # Let controls grow with the page, including after fullscreen changes.
        sidebar.children[1].stylesheets.append(
            InlineStyleSheet(
                css=":host { max-height: none !important; flex: 1 0 auto !important; }"
            )
        )

    def spectrogram(doc):
        spectrogram_bokeh(database_for(doc), doc)

    return BokehASGI({"/explorer": explorer, "/spectrogram": spectrogram})
