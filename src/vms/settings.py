from os import environ
from pathlib import Path

database_path = environ["DATABASE"]
legacy_results_path = environ.get("RESULTS")
results_dir = Path(
    environ.get("RESULTS_DIR")
    or (Path(legacy_results_path).parent if legacy_results_path else "resultdbs")
).absolute()


def job_result_path(jobid):
    return results_dir / f"{jobid}.apitofsim"


def submitted_settings_path(result_db_path):
    return Path(result_db_path) / "settings.json"
