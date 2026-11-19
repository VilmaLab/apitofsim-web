"""Serve resultviewer Bokeh plots from completed simulation databases."""

from apitofresview.webapp import explorer_bokeh, spectrogram_bokeh
from bokeh.core.properties import value as literal_value
from bokeh.models import GlyphRenderer, Text
from bokeh.server.asgi import BokehASGI

from vms.db import open_result_db


def make_bokeh_app(cluster_path):
    from vms.jobs import get_status_dict

    jobs = get_status_dict()
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
        explorer_bokeh(database_for(doc), doc, layout="contained")

    def spectrogram(doc):
        spectrogram_bokeh(database_for(doc), doc)
        for renderer in doc.select({"type": GlyphRenderer}):
            glyph = renderer.glyph
            if isinstance(glyph, Text) and glyph.text_font == "sans-serif":
                # The viewer passes a bare font name, which Bokeh treats as a
                # column name for this glyph property.
                glyph.text_font = literal_value("sans-serif")

    return BokehASGI({"/explorer": explorer, "/spectrogram": spectrogram})
