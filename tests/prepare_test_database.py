"""Build the Playwright fixture from the public cluster tables and ASE data."""

import shutil
import tempfile
import tomllib
import urllib.request
from pathlib import Path

from apitofsim.config import import_raw_config
from apitofsim.workflow import sql_files
from apitofsim.workflow.db import SuperClusterDatabase, connection_scope
from apitofsim.workflow.runners import DerivedDataPreparer


def prepare_test_database(dest: Path, base_url: str) -> None:
    datasets = dest.parent
    with (datasets / "datasets_test.toml").open("rb") as source:
        configs = tomllib.load(source)["densityandrate_configs"]

    with tempfile.TemporaryDirectory(dir=datasets) as directory:
        stage = Path(directory) / dest.name
        stage.mkdir()
        for source_name, target_name in (
            ("database_test.duckdb", "database.duckdb"),
            ("database_test.ase.sqlite.db", "ase.sqlite.db"),
        ):
            with (
                urllib.request.urlopen(
                    f"{base_url}/{source_name}", timeout=60
                ) as response,
                (stage / target_name).open("wb") as target,
            ):
                shutil.copyfileobj(response, target)
        with connection_scope(SuperClusterDatabase, stage, ase=True) as db:
            for table in (
                "k_rate_mesh",
                "k_rate",
                "products_dos",
                "cluster_dos",
                "histogram_params",
            ):
                db.db.execute(f"drop table if exists {table}")
            for sequence in (
                "k_rate_mesh",
                "k_rate",
                "products_dos",
                "dos",
                "histogram_params",
            ):
                db.db.execute(f"drop sequence if exists {sequence}_sequence")
            db.db.execute(sql_files.histograms)
            clusters, names, pathways = db.get_all_lookups()
            preparer = DerivedDataPreparer(db)
            for config in configs:
                preparer.run_densityandrate(
                    import_raw_config(config),
                    clusters,
                    pathways,
                    name_lookup=names,
                    show_progress=False,
                )
        stage.rename(dest)
