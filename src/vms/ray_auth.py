import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


@contextmanager
def local_ray_auth(address):
    """Keep embedded Ray usable when the runtime UID cannot write its home."""
    token = Path.home() / ".ray" / "auth_token"
    directory = token.parent if token.parent.exists() else Path.home()
    if (
        address != "local"
        or os.environ.get("RAY_AUTH_MODE", "token").lower() != "token"
        or os.environ.get("RAY_AUTH_TOKEN")
        or os.environ.get("RAY_AUTH_TOKEN_PATH")
        or token.is_file()
        or os.access(directory, os.W_OK)
    ):
        yield
        return

    # Also covers deployments that launch uvicorn directly, bypassing the
    # container entrypoint. Retain the file until all Ray processes shut down.
    with TemporaryDirectory(prefix="vms-ray-auth-") as temporary:
        token = Path(temporary) / "auth_token"
        token.touch(mode=0o600)
        token.write_text(secrets.token_hex(32))
        previous = os.environ.get("RAY_AUTH_TOKEN_PATH")
        os.environ["RAY_AUTH_TOKEN_PATH"] = str(token)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("RAY_AUTH_TOKEN_PATH", None)
            else:
                os.environ["RAY_AUTH_TOKEN_PATH"] = previous
