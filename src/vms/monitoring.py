"""Sentry configuration shared by the web process and Ray workers."""

from os import environ

import sentry_sdk

SENTRY_ENV_VARS = (
    "SENTRY_DSN",
    "SENTRY_ENVIRONMENT",
    "SENTRY_RELEASE",
    "SENTRY_TRACES_SAMPLE_RATE",
    "SENTRY_PROFILE_SESSION_SAMPLE_RATE",
)


def init_sentry(component):
    if not environ.get("SENTRY_DSN"):
        return

    sentry_sdk.init(
        traces_sample_rate=float(environ.get("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        profile_session_sample_rate=float(
            environ.get("SENTRY_PROFILE_SESSION_SAMPLE_RATE", "0.1")
        ),
        profile_lifecycle="trace",
        enable_logs=True,
        max_request_body_size="never",
        include_local_variables=False,
    )
    sentry_sdk.get_global_scope().set_tag("component", component)
