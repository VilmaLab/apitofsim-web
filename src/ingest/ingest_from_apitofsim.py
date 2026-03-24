import os
from contextlib import chdir
from glob import glob
from json import load
from os.path import basename, dirname, expanduser, isfile
from pathlib import Path
from sys import argv

import duckdb
from apitofsim.config import get_particle, parse_config
from ase.db import connect as connect_ase_db
from ase.io import read as ase_read


def backup_search(source, data_file):
    if "backup_search" in source:
        results = glob(
            source["backup_search"] + "/**/" + basename(data_file),
            recursive=True,
        )
        if len(results) == 1:
            return results[0]


def fixup_config(source, config, particle):
    for quantity in [
        "vibrational_temperatures",
        "rotational_temperatures",
        "electronic_energy",
    ]:
        config_key = f"file_{quantity}_{particle}"
        data_file = config[config_key]
        if not isfile(data_file):
            particle_failed = True
            if "backup_search" in source:
                result = backup_search(source, data_file)
                if result is not None:
                    config[config_key] = result
                    particle_failed = False
            if particle_failed:
                print(f"Could not find {config[config_key]}; skipping particle")
                return True
    return False


def get_gaussian_log(source, config, particle):
    paths = [
        config[f"file_{quantity}_{particle}"]
        for quantity in [
            "vibrational_temperatures",
            "rotational_temperatures",
            "electronic_energy",
        ]
    ]
    log_file = os.path.commonprefix(paths).rstrip("_/.") + ".log"
    if not isfile(log_file):
        return backup_search(source, log_file)
    return log_file


def ingest_source(ddb, ase_db, source):
    filenames = glob(expanduser(source["path"]), recursive=True)
    for filename in filenames:
        print("Reading", filename)
        with chdir(dirname(filename)):
            config = parse_config(filename)
            ids = []
            particle_failed = False
            for particle in ["cluster", "first_product", "second_product"]:
                if fixup_config(source, config, particle):
                    particle_failed = True
                    continue
                particle_config = get_particle(config, particle)
                particle_name = particle_config["name"]
                existing_id = ddb.execute(
                    "select id from cluster where common_name = ?",
                    (particle_name,),
                ).fetchone()
                if existing_id is not None:
                    print("Skipping existing particle", particle_config["name"])
                    ids.append(existing_id[0])
                    continue
                gaussian_path = get_gaussian_log(source, config, particle)
                if gaussian_path is None:
                    print(
                        f"Could not find Guassian log for {particle_name}. Carrying on without."
                    )
                    ase_id = None
                else:
                    particle = ase_read(gaussian_path, format="gaussian-out")
                    ase_id = ase_db.write(particle)
                id = ddb.execute(
                    "insert into cluster values (default, ?, ?, ?, ?, ?, ?) returning id",
                    (
                        ase_id,
                        particle_config["name"],
                        particle_config["atomic_mass"],
                        particle_config["electronic_energy"],
                        particle_config["rotational_temperatures"],
                        particle_config["vibrational_temperatures"],
                    ),
                ).fetchone()
                assert id is not None
                ids.append(id[0])
            if particle_failed:
                print("Skipping pathway due to missing particles")
                continue
            ddb.execute(
                "insert into pathway values (default, ?, ?, ?)",
                (ids[0], ids[1], ids[2]),
            )


def main():
    infn = argv[1]
    with open(infn) as f:
        ingest_config = load(f)
    print(ingest_config)

    out_path = argv[2]

    ddb_path = Path(out_path + ".duckdb")
    ddb_path.unlink(missing_ok=True)
    ddb = duckdb.connect(ddb_path)

    asedb_path = Path(out_path + ".ase.sqlite")
    asedb_path.unlink(missing_ok=True)

    script_dir = os.path.dirname(os.path.realpath(__file__))
    ddb.execute(open(script_dir + "/tables.sql", "r").read())
    with connect_ase_db(asedb_path, type="db") as ase_db:
        for source in ingest_config:
            ingest_source(ddb, ase_db, source)


if __name__ == "__main__":
    main()
