"""Shared asset builds for serving and packaging, without importing the app."""

from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from workingtitle.devserver import AssetPlan, NpmBuild

PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]
SOURCE = (PROJECT / "package.json").is_file()


@lru_cache(maxsize=8)
def _asset_digest(path, modified, size):
    return sha256(path.read_bytes()).hexdigest()[:16]


def asset_version(name):
    path = PACKAGE / "static" / name
    stat = path.stat()
    return _asset_digest(path, stat.st_mtime_ns, stat.st_size)


ASSETS = AssetPlan(
    root=PROJECT if SOURCE else PACKAGE,
    source=SOURCE,
    steps=(
        NpmBuild(
            "frontend",
            inputs=("src/css/**", "src/js/**", "src/vms/**/*.html", "src/vms/**/*.py"),
            outputs=tuple(
                f"{'src/vms/' if SOURCE else ''}static/index.{extension}"
                for extension in ("css", "js")
            ),
        ),
    ),
)
