import duckdb
import pytest
from apitofsim.workflow import sql_files_nofk as sql
from apitofsim.workflow.db import SuperClusterDatabase

from vms.app import current_db
from vms.forms import get_default_histogram_precision, get_histogram_precision_choices


@pytest.fixture
def histograms():
    db = SuperClusterDatabase((duckdb.connect(), None))
    db.db.execute(sql.pathway + sql.histograms)
    token = current_db.set(db)
    get_histogram_precision_choices.cache_clear()
    try:
        yield db.db
    finally:
        get_histogram_precision_choices.cache_clear()
        current_db.reset(token)
        db.close()


def add_histogram(connection, table, histogram_id, width):
    connection.execute(
        "INSERT INTO histogram_params VALUES (?, ?, 1000)", [histogram_id, width]
    )
    if table == "cluster_dos":
        connection.execute(
            "INSERT INTO cluster_dos VALUES (default, ?, 1, [])", [histogram_id]
        )
    elif table == "products_dos":
        connection.execute(
            "INSERT INTO products_dos VALUES (default, ?, 1, 2, [])", [histogram_id]
        )
    else:
        connection.execute(
            "INSERT INTO k_rate VALUES (default, 1, ?, [])", [histogram_id]
        )


@pytest.mark.parametrize("dos_table", ["cluster_dos", "products_dos"])
def test_reduced_histogram_choices(histograms, dos_table):
    add_histogram(histograms, dos_table, 10, 20)
    add_histogram(histograms, "k_rate", 30, 20)
    # Multiple available rows using a configuration must not duplicate choices.
    histograms.execute("INSERT INTO k_rate VALUES (default, 2, 30, [])")
    assert get_histogram_precision_choices() == [("10,30", "[bin width = 20.0 K]")]
    assert get_default_histogram_precision() == "10,30"


def test_full_histogram_choices(histograms):
    for histogram_id, width in [(1, 30), (2, 10), (3, 20)]:
        add_histogram(histograms, "cluster_dos", histogram_id, width)
        add_histogram(histograms, "k_rate", histogram_id + 3, width)
    assert get_histogram_precision_choices() == [
        ("2,5", "superfine (slow) [bin width = 10.0 K]"),
        ("3,6", "fine [bin width = 20.0 K]"),
        ("1,4", "coarse (fast) [bin width = 30.0 K]"),
    ]
    assert get_default_histogram_precision() == "3,6"


def test_mismatched_histogram_sets_pair_by_width(histograms):
    for histogram_id, width in [(1, 10), (2, 20), (3, 30)]:
        add_histogram(histograms, "cluster_dos", histogram_id, width)
    for histogram_id, width in [(4, 20), (5, 40)]:
        add_histogram(histograms, "k_rate", histogram_id, width)
    assert get_histogram_precision_choices() == [("2,4", "[bin width = 20.0 K]")]


def test_distinct_configurations_with_same_width(histograms):
    for table, histogram_id in [
        ("cluster_dos", 1),
        ("products_dos", 2),
        ("k_rate", 3),
        ("k_rate", 4),
    ]:
        add_histogram(histograms, table, histogram_id, 20)
    assert [value for value, _ in get_histogram_precision_choices()] == [
        "1,3",
        "1,4",
        "2,3",
        "2,4",
    ]


@pytest.mark.parametrize("available", ["empty", "dos", "rate", "incompatible"])
def test_no_compatible_histogram_choices(histograms, available):
    if available in ("dos", "incompatible"):
        add_histogram(histograms, "cluster_dos", 1, 10)
    if available in ("rate", "incompatible"):
        add_histogram(histograms, "k_rate", 2, 20)
    assert get_histogram_precision_choices() == []
    assert get_default_histogram_precision() is None
