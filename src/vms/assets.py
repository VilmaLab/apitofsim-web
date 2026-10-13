"""Shared asset builds for serving and packaging, without importing the app."""

from pathlib import Path

from workingtitle.devserver import AssetPlan, NpmBuild

PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE.parents[1]
SOURCE = (PROJECT / "package.json").is_file()

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
