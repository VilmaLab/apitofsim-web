"""Serve resultviewer Bokeh plots from completed simulation databases."""

from apitofresview.webapp import explorer_bokeh, spectrogram_bokeh
from bokeh.core.properties import value as literal_value
from bokeh.models import Div, GlyphRenderer, Text
from bokeh.server.asgi import BokehASGI

from vms.db import open_result_db


def make_bokeh_app(cluster_path):
    from vms.jobs import get_status_dict

    jobs = get_status_dict()

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
        return open_result_db(cluster_path, job["result_db_path"])

    def render_plot(doc, plot, **kwargs):
        try:
            db = database_for(doc)
            try:
                plot(db, doc, **kwargs)
            except Exception:
                db.close()
                raise
        except FileNotFoundError:
            doc.clear()
            doc.add_root(
                Div(
                    text="The result was deleted to make room for other results. "
                    '<a href="/jobs" target="_top">Return to the list of experiments</a>'
                )
            )
            return
        doc.on_session_destroyed(lambda session_context: db.close())

    def explorer(doc):
        render_plot(doc, explorer_bokeh, layout="contained")

    def spectrogram(doc):
        render_plot(doc, spectrogram_bokeh)
        for renderer in doc.select({"type": GlyphRenderer}):
            glyph = renderer.glyph
            if isinstance(glyph, Text) and glyph.text_font == "sans-serif":
                # The viewer passes a bare font name, which Bokeh treats as a
                # column name for this glyph property.
                glyph.text_font = literal_value("sans-serif")

    return BokehASGI({"/explorer": explorer, "/spectrogram": spectrogram})
