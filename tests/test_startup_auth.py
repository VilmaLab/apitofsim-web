import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "credentials", ["generated", "file", "environment", "disabled"]
)
def test_container_auth_startup(tmp_path, credentials):
    """Exercise the entrypoint without downloading data or starting the app."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    rclone = bin_dir / "rclone"
    rclone.write_text("#!/bin/sh\nexit 0\n")
    rclone.chmod(0o755)
    uvicorn = bin_dir / "uvicorn"
    uvicorn.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, stat\n"
        "path = os.environ.get('RAY_AUTH_TOKEN_PATH')\n"
        "print(json.dumps(dict(mode=os.environ['RAY_AUTH_MODE'], path=path,\n"
        "    token=os.environ.get('RAY_AUTH_TOKEN'),\n"
        "    permissions=stat.S_IMODE(pathlib.Path(path).stat().st_mode) if path else None)))\n"
    )
    uvicorn.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("RAY_AUTH_")
    }
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        DATABASE_DIR=str(tmp_path),
        TMPDIR=str(tmp_path),
        HOME=str(tmp_path / "nonexistent-home"),
    )
    supplied = tmp_path / "supplied-token"
    if credentials == "file":
        supplied.write_text("test-token")
        env["RAY_AUTH_TOKEN_PATH"] = str(supplied)
    elif credentials == "environment":
        env["RAY_AUTH_TOKEN"] = "test-token"
    elif credentials == "disabled":
        env["RAY_AUTH_MODE"] = "disabled"

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "fetch-and-start.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    settings = json.loads(result.stdout)
    assert settings["mode"] == ("disabled" if credentials == "disabled" else "token")
    if credentials == "generated":
        token_path = Path(settings["path"])
        assert len(token_path.read_text().strip()) == 64
        assert settings["permissions"] == 0o600
        assert token_path.parent.stat().st_mode & 0o777 == 0o700
    elif credentials == "file":
        assert settings["path"] == str(supplied)
        assert supplied.read_text() == "test-token"
    else:
        assert settings["path"] is None
        assert settings["token"] == (
            "test-token" if credentials == "environment" else None
        )
