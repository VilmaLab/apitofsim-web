import duckdb
from os import environ
import os

script_dir = os.path.dirname(os.path.realpath(__file__))
db = duckdb.connect(database=environ["DATABASE"])
db.execute(open(script_dir + "/tables.sql", "r").read())
