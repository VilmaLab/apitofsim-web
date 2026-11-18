import shutil
import subprocess
from pathlib import Path

import pytest

from vms.resources import make_runtime_env

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_env_ships_lockfile():
    runtime_env = make_runtime_env()
    working_dir = Path(runtime_env["working_dir"])
    assert working_dir == REPO_ROOT
    assert (working_dir / "uv.lock").is_file()
    if shutil.which("git") is None:
        pytest.skip("git not available")
    # Ray packages the working dir minus the paths matching .gitignore, so an
    # ignored lock would silently make workers resolve fresh dependencies.
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "uv.lock"], cwd=working_dir
    )
    assert ignored.returncode == 1


def test_runtime_env_forwards_sentry_settings(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "test")
    monkeypatch.setenv("SENTRY_RELEASE", "test-release")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.5")
    monkeypatch.setenv("SENTRY_PROFILE_SESSION_SAMPLE_RATE", "0")
    assert "env_vars" not in make_runtime_env()

    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    assert make_runtime_env()["env_vars"] == {
        "SENTRY_DSN": "https://public@example.invalid/1",
        "SENTRY_ENVIRONMENT": "test",
        "SENTRY_RELEASE": "test-release",
        "SENTRY_TRACES_SAMPLE_RATE": "0.5",
        "SENTRY_PROFILE_SESSION_SAMPLE_RATE": "0",
    }
