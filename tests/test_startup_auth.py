import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from vms.ray_auth import local_ray_auth

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "credentials", ["generated", "file", "environment", "disabled"]
)
def test_container_auth_startup(tmp_path, credentials):
    """Exercise the entrypoint without downloading data or starting the app."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    fixture = tmp_path / "fixture" / "database.apitofsim"
    fixture.mkdir(parents=True)
    for name in ("database.duckdb", "ase.sqlite.db", "histograms_1000.mmap"):
        (fixture / name).write_text(name)
    archive = tmp_path / "fixture.tar.zst"
    with tarfile.open(archive, "w:zst") as bundle:
        bundle.add(fixture, arcname=fixture.name)
    rclone = bin_dir / "rclone"
    rclone.write_text('#!/bin/sh\ncp -- "$TEST_DATABASE_ARCHIVE" "$4"\n')
    rclone.chmod(0o755)
    ray = bin_dir / "ray"
    ray.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = status ]; then\n'
        '    [ -f "$TMPDIR/probe-attempted" ] && exit 0\n'
        '    touch "$TMPDIR/probe-attempted"\n'
        '    echo "Ray status output"\n'
        '    echo "Ray status error" >&2\n'
        "    exit 1\n"
        "fi\n"
        "exec sleep 60\n"
    )
    ray.chmod(0o755)
    dirroof = bin_dir / "dirroof"
    dirroof.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['TMPDIR'], 'dirroof-args').write_text(\n"
        "    json.dumps(sys.argv[1:]))\n"
        "os.execlp('sleep', 'sleep', '60')\n"
    )
    dirroof.chmod(0o755)
    uvicorn = bin_dir / "uvicorn"
    uvicorn.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, stat\n"
        "path = os.environ.get('RAY_AUTH_TOKEN_PATH')\n"
        "print(json.dumps(dict(mode=os.environ['RAY_AUTH_MODE'], path=path,\n"
        "    token=os.environ.get('RAY_AUTH_TOKEN'),\n"
        "    length=len(pathlib.Path(path).read_text().strip()) if path else None,\n"
        "    directory_mode=stat.S_IMODE(pathlib.Path(path).parent.stat().st_mode) if path else None,\n"
        "    permissions=stat.S_IMODE(pathlib.Path(path).stat().st_mode) if path else None)))\n"
    )
    uvicorn.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("RAY_AUTH_") and key != "RAY_ADDRESS"
    }
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        DATABASE_DIR=str(tmp_path),
        TEST_DATABASE_ARCHIVE=str(archive),
        RESULTS_DIR=str(tmp_path / "results"),
        RESULTS_MAX_SIZE="4G",
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
        ["bash", str(REPO_ROOT / "fetch-and-run.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "Ray status error" in result.stderr
    assert "Web exited unexpectedly (status 0)." in result.stderr
    assert json.loads((tmp_path / "dirroof-args").read_text()) == [
        "--size",
        "4G",
        "--recursive",
        "--evict-depth",
        "1",
        str(tmp_path / "results"),
    ]
    for name in ("database.duckdb", "ase.sqlite.db", "histograms_1000.mmap"):
        assert (tmp_path / "database.apitofsim" / name).read_text() == name
    assert not list(tmp_path.glob(".download.*"))
    status_output, settings_json = result.stdout.splitlines()
    assert status_output == "Ray status output"
    settings = json.loads(settings_json)
    assert settings["mode"] == ("disabled" if credentials == "disabled" else "token")
    if credentials == "generated":
        token_path = Path(settings["path"])
        assert settings["length"] == 64
        assert settings["permissions"] == 0o600
        assert settings["directory_mode"] == 0o700
        assert not token_path.exists()
    elif credentials == "file":
        assert settings["path"] == str(supplied)
        assert supplied.read_text() == "test-token"
    else:
        assert settings["path"] is None
        assert settings["token"] == (
            "test-token" if credentials == "environment" else None
        )


@pytest.fixture
def unwritable_ray_home(monkeypatch, tmp_path):
    for key in ("RAY_AUTH_TOKEN", "RAY_AUTH_TOKEN_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("RAY_AUTH_MODE", "token")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("vms.ray_auth.os.access", lambda *args: False)
    return tmp_path


def test_embedded_ray_token_without_writable_home(unwritable_ray_home):
    with pytest.raises(RuntimeError, match="startup failed"):
        with local_ray_auth("local"):
            token = Path(os.environ["RAY_AUTH_TOKEN_PATH"])
            assert len(token.read_text()) == 64
            assert token.stat().st_mode & 0o777 == 0o600
            assert token.parent.stat().st_mode & 0o777 == 0o700
            raise RuntimeError("startup failed")
    assert not token.exists()
    assert "RAY_AUTH_TOKEN_PATH" not in os.environ
    assert not (unwritable_ray_home / ".ray").exists()


@pytest.mark.parametrize(
    "configuration",
    ["file", "environment", "default", "disabled", "remote", "writable"],
)
def test_embedded_ray_preserves_auth_configuration(
    unwritable_ray_home, monkeypatch, configuration
):
    address = "local"
    if configuration == "file":
        # A missing explicitly configured file must fail rather than silently
        # switching to newly generated credentials.
        monkeypatch.setenv("RAY_AUTH_TOKEN_PATH", "/missing/token")
    elif configuration == "environment":
        monkeypatch.setenv("RAY_AUTH_TOKEN", "test-token")
    elif configuration == "default":
        token = unwritable_ray_home / ".ray" / "auth_token"
        token.parent.mkdir()
        token.write_text("test-token")
    elif configuration == "disabled":
        monkeypatch.setenv("RAY_AUTH_MODE", "disabled")
    elif configuration == "remote":
        address = "127.0.0.1:6379"
    else:
        monkeypatch.setattr("vms.ray_auth.os.access", lambda *args: True)
    original = {
        key: value for key, value in os.environ.items() if key.startswith("RAY_AUTH_")
    }
    with local_ray_auth(address):
        assert {
            key: value
            for key, value in os.environ.items()
            if key.startswith("RAY_AUTH_")
        } == original
