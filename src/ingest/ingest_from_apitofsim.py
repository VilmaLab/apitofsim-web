from glob import glob
from sys import argv
from json import load
from contextlib import chdir
from os import environ
from os.path import dirname, isfile, basename
import duckdb

from apitofsim import parse_config, get_particle

db = duckdb.connect(database=environ["DATABASE"])

with open(argv[1]) as f:
    ingest_config = load(f)

for source in ingest_config:
    for filename in glob(source["path"], recursive=True):
        print("Reading", filename)
        with chdir(dirname(filename)):
            config = parse_config(filename)
            ids = []
            particle_failed = False
            for particle in ["cluster", "first_product", "second_product"]:
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
                            results = glob(
                                source["backup_search"] + "/**/" + basename(data_file),
                                recursive=True,
                            )
                            if len(results) == 1:
                                config[config_key] = results[0]
                                particle_failed = False
                        if particle_failed:
                            print(
                                f"Could not find {config[config_key]}; skipping particle"
                            )
                            continue
                particle_config = get_particle(config, particle)
                existing_id = db.execute(
                    "select id from cluster where common_name = ?",
                    (particle_config["name"],),
                ).fetchone()
                if existing_id is not None:
                    print("Skipping existing particle", particle_config["name"])
                    ids.append(existing_id[0])
                    continue
                id = db.execute(
                    "insert into cluster values (default, ?, ?, ?, ?, ?) returning id",
                    (
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
            db.execute(
                "insert into pathway values (default, ?, ?, ?)",
                (ids[0], ids[1], ids[2]),
            )
