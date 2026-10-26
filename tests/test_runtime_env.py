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
