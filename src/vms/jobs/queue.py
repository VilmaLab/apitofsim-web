_job_status = {}


def get_status_dict():
    return _job_status


def start_job(job_id):
    from vms.jobs.run import run_simulation
    from vms.settings import database_path

    status = get_status_dict()
    info = status[job_id]  # type: ignore

    simulation_call = run_simulation.remote(
        **info["arguments"],
        database_path=database_path,
        result_db_path=info["result_db_path"],
        start_time=info["start_time"],
    )
    new_info = {
        **info,  # type: ignore
        "status": "running",
        "task": simulation_call,
        "aiter": aiter(simulation_call),
    }
    status[job_id] = new_info  # type: ignore
    return new_info


def pump_jobs():
    from .queue import get_status_dict

    status = get_status_dict()

    # XXX: 0.01s per job. Run in thread?
    for job_id, info in status.items():  # type: ignore
        if info["status"] == "pending":
            start_job(job_id)
