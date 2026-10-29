import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = REPO_ROOT / "datasets"
TEST_DB_BASE_URL = "https://a3s.fi/swift/v1/apitofsim-data-public"
TEST_DB_FILES = ["database_test.duckdb", "database_test.ase.sqlite.db"]

# Ray's head node takes a while to come up inside the web process
SERVER_STARTUP_TIMEOUT = 300


def pytest_configure(config):
    # App imports happen during collection, before the server fixture runs.
    results_dir = tempfile.TemporaryDirectory(prefix="apitofsim-tests-")
    config.add_cleanup(results_dir.cleanup)
    env = pytest.MonkeyPatch()
    config.add_cleanup(env.undo)
    env.setenv("DATABASE", str(DATASETS_DIR / TEST_DB_FILES[0]))
    env.setenv("RESULTS", str(Path(results_dir.name) / "results.duckdb"))


def download_if_missing(name):
    dest = DATASETS_DIR / name
    if dest.exists():
        return dest
    DATASETS_DIR.mkdir(exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    print(f"Downloading {name}...", file=sys.stderr)
    with urllib.request.urlopen(f"{TEST_DB_BASE_URL}/{name}") as response:
        part.write_bytes(response.read())
    # Only publish under the real name once complete, so an interrupted download
    # is not mistaken for a cached one
    part.replace(dest)
    return dest


@pytest.fixture(scope="session")
def test_db():
    return [download_if_missing(name) for name in TEST_DB_FILES][0]


def wait_until_serving(base_url, process, log_path):
    deadline = time.monotonic() + SERVER_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Server exited with {process.returncode}:\n{log_path.read_text()}"
            )
        try:
            with urllib.request.urlopen(base_url, timeout=5) as response:
                if response.status == 200:
                    return
        except urllib.error.URLError, ConnectionError, TimeoutError, OSError:
            pass
        time.sleep(1)
    raise TimeoutError(
        f"Server did not come up within {SERVER_STARTUP_TIMEOUT}s:\n{log_path.read_text()}"
    )


@pytest.fixture(scope="session")
def server(test_db, tmp_path_factory):
    """A real Starlette server, with Ray started automatically inside it."""
    # Reserve the port throughout Ray startup; its services also bind ephemeral ports.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        env = {
            **os.environ,
            "DATABASE": str(test_db),
            "RESULTS": str(tmp_path_factory.mktemp("results") / "results.duckdb"),
            "RAY_AUTH_MODE": "token",
            "RAY_AUTH_TOKEN_PATH": str(
                tmp_path_factory.mktemp("ray-auth") / "auth_token"
            ),
            "PYTHONPATH": str(REPO_ROOT / "src")
            + os.pathsep
            + os.environ.get("PYTHONPATH", ""),
        }
        # Unset so that ray.init() falls back to address="local" and brings up its
        # own single node cluster rather than looking for an external one
        env.pop("RAY_ADDRESS", None)
        env.pop("RAY_AUTH_TOKEN", None)
        # An explicit token path must already exist; ray.init only generates at the
        # default path. Keep test credentials separate from the developer's token.
        token_path = Path(env["RAY_AUTH_TOKEN_PATH"])
        token_path.touch(mode=0o600)
        token_path.write_text(secrets.token_hex(32))

        log_path = Path(__file__).parent / "server.log"
        base_url = f"http://127.0.0.1:{port}"
        with log_path.open("w") as log:
            # No --debug: the reloader would fork a second copy of the app, and so
            # of Ray too
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "vms:app",
                    "--fd",
                    str(listener.fileno()),
                ],
                cwd=REPO_ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                # Own process group, so that Ray's children can be cleaned up along
                # with the server if it does not go quietly
                start_new_session=True,
                pass_fds=(listener.fileno(),),
            )
            try:
                wait_until_serving(base_url, process, log_path)
                yield base_url
            finally:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


@pytest.fixture
def output_db(tmp_path):
    """Use the same attached cluster/result schemas as a web worker."""
    import duckdb
    from apitofsim.workflow import sql_files_nofk as sql

    from vms.jobs.run import setup_result_db

    clusters = tmp_path / "clusters.duckdb"
    with duckdb.connect(str(clusters)) as connection:
        connection.execute(sql.pathway + sql.histograms + sql.pathway_report)
    db = setup_result_db(clusters, tmp_path / "output.duckdb")
    try:
        yield db
    finally:
        db.close()
