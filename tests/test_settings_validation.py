from pathlib import Path

import duckdb
import pytest
from apitofsim.workflow.db import ClusterDatabase
from playwright.sync_api import expect
from starlette.datastructures import FormData
from starlette.requests import Request
from wtforms import FormField, SelectField

from vms.app import app, current_db, pathways_context, templates
from vms.forms import (
    SettingsForm,
    SimulationForm,
    get_cluster_choices,
    settings_errors,
)

STATIC_JS = Path(__file__).resolve().parents[1] / "src/vms/static/index.js"
STATIC_CSS = STATIC_JS.with_suffix(".css")


class ExampleSimulationForm(SimulationForm):
    histogram_precision = SelectField(
        choices=[("1,1", "Test precision")], default="1,1"
    )


class ExampleSettingsForm(SettingsForm):
    cluster = SelectField(
        *SettingsForm.cluster.args,
        **(
            SettingsForm.cluster.kwargs | {"choices": [("", ""), ("1", "Test cluster")]}
        ),
    )
    simulation = FormField(ExampleSimulationForm)


@pytest.fixture
def form():
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "root_path": "",
            "headers": [],
            "server": ("testserver", 80),
            "app": app,
        }
    )
    settings = ExampleSettingsForm(request)
    settings.cluster.data = "1"
    return settings


@pytest.fixture
def cluster_db():
    db = ClusterDatabase((duckdb.connect(), None))
    db.db.execute(
        "CREATE TABLE cluster (id INTEGER, common_name VARCHAR, atomic_mass DOUBLE, "
        "electronic_energy DOUBLE, ase_mol_id INTEGER)"
    )
    db.db.execute(
        "INSERT INTO cluster VALUES (1, 'Parent', 30, -3, NULL), "
        "(2, 'Other parent', 40, -4, NULL), (3, 'Product A', 10, -1, NULL), "
        "(4, 'Product B', 20, -1, NULL), (5, 'Unrelated', 50, 0, NULL)"
    )
    db.db.execute(
        "CREATE TABLE pathway (id INTEGER, cluster_id INTEGER, product1_id INTEGER, product2_id INTEGER)"
    )
    db.db.execute(
        "INSERT INTO pathway VALUES (10, 1, 3, 4), (11, 1, 3, 3), (12, 2, 3, 4)"
    )
    db.ase_db = None
    token = current_db.set(db)
    try:
        yield db
    finally:
        current_db.reset(token)
        db.close()


def test_cluster_choices_include_each_parent_once(cluster_db):
    choices = get_cluster_choices()
    assert choices[0] == ("", "")
    assert sorted(choices[1:]) == [(1, "Parent"), (2, "Other parent")]


def test_pathways_context_includes_only_selected_parent_and_products(form, cluster_db):
    context = pathways_context(form, 1)
    assert context["primary_cluster"] == (1, "Parent")
    clusters = list(context["clusters"])
    assert {cluster["id"] for cluster in clusters} == {1, 3, 4}
    assert all(cluster["has_ase"] is False for cluster in clusters)
    pathways = {
        int(pathway["form"].pathway.data): pathway for pathway in context["pathways"]
    }
    assert set(pathways) == {10, 11}
    assert pathways[10]["product1"] == (3, "Product A")
    assert pathways[10]["product2"] == (4, "Product B")
    assert pathways[11]["product2"] == (3, "Product A")
    assert all(pathway["bonding_energy"] == 1 for pathway in pathways.values())


def render(form):
    form_errors, field_errors = settings_errors(form)
    return templates.env.get_template("settings/settings.html").render(
        request=form._request,
        form=form,
        form_errors=form_errors,
        field_errors=field_errors,
        primary_cluster=None,
    )


def load(page, form, hash="voltagecontrols"):
    # A route preserves a real origin and hash without starting Ray.
    page.route(
        "http://testserver/",
        lambda route: route.fulfill(
            body=render(form) if route.request.method == "GET" else "<h1>Submitted</h1>"
        ),
    )
    page.route("http://testserver/static/**", lambda route: route.abort())
    page.goto(f"http://testserver/#{hash}")
    page.add_style_tag(path=str(STATIC_CSS))
    page.add_script_tag(path=str(STATIC_JS))


def test_error_collection_and_order(form):
    form.form_errors = ["Form error"]
    form.gas.form.form_errors = ["Gas form error"]
    form.simulation.skimmer.tolerance.errors = ["Invalid tolerance"]
    form.pathways[0].fragmentation_energy.errors = ["Invalid energy"]
    form.gas.gas_molecule_mass.errors = ["Invalid mass"]
    form_errors, field_errors = settings_errors(form)
    assert form_errors == ["Form error", "Gas form error"]
    assert [error["tab"] for error in field_errors] == [
        "fragmentationpathways",
        "gas",
        "simulation",
    ]
    # Overrides in the test subclass get new WTForms creation counters.
    declared = [
        (name, field.creation_counter)
        for name, field in vars(SettingsForm).items()
        if hasattr(field, "creation_counter")
    ]
    assert [name for name, _ in sorted(declared, key=lambda item: item[1])] == [
        "cluster",
        "pathways",
        "gas",
        "instrument_model",
        "instrument",
        "voltage",
        "simulation",
    ]


def test_native_validation_reveals_first_tab(page, form):
    load(page, form, "gas")
    page.fill("#gas-adiabatic_index", "")
    page.click('a[href="#simulation"]')
    page.fill("#simulation-realizations", "")
    page.click('button[type="submit"]')
    expect(page.locator('div[name="gas"]')).to_be_visible()
    expect(page.locator("#gas-adiabatic_index")).to_be_focused()
    assert page.locator("#gas-adiabatic_index").evaluate("el => el.validationMessage")
    page.fill("#gas-adiabatic_index", "1.4")
    page.click('button[type="submit"]')
    expect(page.locator('div[name="simulation"]')).to_be_visible()
    expect(page.locator("#simulation-realizations")).to_be_focused()
    expect(page.locator('[role="alert"]')).to_have_count(0)


def test_server_errors_override_hash_and_clear(page, form):
    form.gas.gas_molecule_mass.errors = ["Invalid mass"]
    form.voltage.voltage1.errors = ["Invalid voltage"]
    form.simulation.skimmer.tolerance.errors = ["Invalid tolerance"]
    load(page, form, "simulation")
    mass = page.locator("#gas-gas_molecule_mass")
    expect(mass).to_be_focused()
    assert mass.evaluate("el => el.validationMessage") == "Invalid mass"
    page.locator('div[name="gas"] select').nth(1).select_option("gram")
    assert mass.evaluate("el => el.validity.customError") is False
    page.click('button[type="submit"]')
    number = page.locator('input[type="number"][name="voltage-voltage1"]')
    expect(number).to_be_focused()
    # Changing the paired slider clears the number input's custom error.
    page.locator('input[type="range"][name="voltage-voltage1"]').fill("-18")
    assert number.evaluate("el => el.validity.customError") is False
    page.click('button[type="submit"]')
    tolerance = page.locator("#simulation-skimmer-tolerance")
    expect(tolerance).to_be_focused()
    page.fill("#simulation-skimmer-tolerance", "0.00001")
    assert tolerance.evaluate("el => el.validity.customError") is False
    page.click('button[type="submit"]')
    expect(page.get_by_role("heading", name="Submitted")).to_be_visible()


def test_form_alert_dismissal_and_hidden_field_error(page, form):
    form.form_errors = ["Choose a compatible configuration. <script>alert(1)</script>"]
    form.gas.form.form_errors = ["Gas settings conflict."]
    form.instrument.instrument_geometry.length_of_first_chamber.errors = [
        "Invalid fixed length"
    ]
    load(page, form)
    alert = page.locator('[role="alert"]')
    expect(alert).to_contain_text("Unable to run simulation")
    expect(alert).to_contain_text("Gas settings conflict.")
    expect(alert).not_to_contain_text("Invalid fixed length")
    expect(page.locator('div[name="instrument"]')).to_be_visible()
    expect(page.get_by_text("L0: Invalid fixed length", exact=True)).to_be_visible()
    expect(page.locator('a[href="#instrument"]')).to_be_focused()
    page.set_viewport_size({"width": 375, "height": 800})
    assert alert.bounding_box()["width"] <= 375 - 48
    page.get_by_role("button", name="Dismiss errors").click()
    expect(alert).not_to_be_visible()
    # A new response resets the local Alpine dismissal state.
    page.reload()
    page.add_script_tag(path=str(STATIC_JS))
    expect(alert).to_be_visible()


def test_invalid_post_preserves_pathways_and_custom_instrument(page, server):
    page.goto(server)
    page.click('a[href="#fragmentationpathways"]')
    page.select_option("#cluster", index=1)
    energy = page.locator(
        'input[name^="pathways-"][name$="-fragmentation_energy"]'
    ).first
    expect(energy).to_be_visible()
    energy.fill("invalid energy")
    enabled = page.locator('input[name^="pathways-"][name$="-enabled"]').first
    enabled.uncheck()
    energy_name = energy.get_attribute("name")
    enabled_name = enabled.get_attribute("name")
    page.click('a[href="#instrument"]')
    page.select_option("#instrument-select", "custom")
    angle = page.locator("#instrument-skimmer_geometry-angle_of_skimmer")
    expect(angle).to_be_visible()
    angle.fill("invalid angle")
    page.click('a[href="#simulation"]')
    page.click('button[type="submit"]')
    expect(page.locator(f'input[name="{energy_name}"]')).to_be_focused()
    expect(page.locator(f'input[name="{energy_name}"]')).to_have_value("invalid energy")
    expect(page.locator(f'input[name="{enabled_name}"]')).not_to_be_checked()
    expect(page.locator('[role="alert"]')).to_have_count(0)
    page.locator(f'input[name="{energy_name}"]').fill("0.1")
    page.click('button[type="submit"]')
    expect(angle).to_be_focused()
    expect(angle).to_have_value("invalid angle")
    assert angle.evaluate("el => el.validationMessage") == "Not a valid float value."


@pytest.mark.parametrize("value", ["", "None"])
def test_missing_cluster_errors_belong_to_cluster(form, value):
    submitted = ExampleSettingsForm(
        form._request, formdata=FormData({"cluster": value})
    )
    assert not submitted.cluster.validate(submitted)
    assert not submitted.pathways.validate(submitted)
    form_errors, field_errors = settings_errors(submitted)
    assert form_errors == []
    assert [error["name"] for error in field_errors] == ["cluster"]
    assert field_errors[0]["message"] == "This field is required."


def test_missing_cluster_native_and_server_errors(page, form):
    form.cluster.data = ""
    load(page, form, "simulation")
    page.click('button[type="submit"]')
    cluster = page.locator("#cluster")
    expect(cluster).to_be_focused()
    assert cluster.evaluate("el => el.validity.valueMissing")
    expect(
        page.get_by_text("Fragmentation pathway: This field is required.", exact=True)
    ).to_have_count(0)
    cluster.select_option("1")
    assert cluster.evaluate("el => el.validity.valid")
    page.click('button[type="submit"]')
    expect(page.get_by_role("heading", name="Submitted")).to_be_visible()

    # A bypassed native check must still report the missing cluster on the server.
    form.cluster.raw_data = [""]
    form.cluster.validate(form)
    form.pathways.validate(form)
    page.goto("about:blank")
    page.goto("http://testserver/#simulation")
    page.add_script_tag(path=str(STATIC_JS))
    expect(cluster).to_be_focused()
    assert cluster.evaluate("el => el.validationMessage") == "This field is required."
    expect(
        page.get_by_text("Fragmentation pathway: This field is required.", exact=True)
    ).to_have_count(0)
    cluster.select_option("1")
    assert cluster.evaluate("el => el.validity.customError") is False


@pytest.fixture
def config_form(form):
    from types import SimpleNamespace

    from vms.forms import Q_

    params = {
        10: (Q_(20, "K"), Q_(200_000, "K")),
        20: (Q_(20, "K"), Q_(100_000, "K")),
    }
    token = current_db.set(SimpleNamespace(get_histogram_params=params.get))
    form.simulation.histogram_precision.data = "10,20"
    form.simulation.realizations.data = 5
    form.voltage.voltage1.data = -23
    # Exercise unit conversion as well as the geometry's positional contract.
    form.instrument.instrument_geometry.length_of_skimmer.data = Q_(0.7, "mm")
    try:
        yield form, params
    finally:
        current_db.reset(token)


def test_config_round_trip_uses_apitofsim_schema(config_form, output_db):
    import json

    import numpy as np
    from apitofsim.config import dump_to_raw
    from apitofsim.workflow.runners import DerivedDataPreparer

    from vms.forms import Q_

    form, _ = config_form
    arguments = form.get_data()
    config = arguments["config"]
    assert set(arguments) == {"config", "pathways", "histograms", "histogram_precision"}
    assert arguments["histogram_precision"] == (10, 20)
    assert set(config) == {
        "voltages",
        "quadrupole",
        "N",
        "bin_width",
        "energy_max",
        "energy_max_rate",
        "dc",
        "alpha_factor",
        "N_iter",
        "M_iter",
        "resolution",
        "tolerance",
        "T",
        "gas",
        "pressures",
        "lengths",
    }
    output_db.insert_config("webrun", config)
    stored = next(output_db.iter_configs()).config
    assert stored["N"] == 5
    np.testing.assert_array_equal(
        stored["voltages"].to("V").magnitude, [-23, -9, -7, -6, 11]
    )
    np.testing.assert_allclose(
        stored["lengths"].to("m").magnitude,
        [0.001, 0.00244, 0.101, 0.00448, 0.0007],
    )
    assert stored["bin_width"] == Q_(20, "K")
    assert stored["energy_max"] == Q_(200_000, "K")
    assert stored["energy_max_rate"] == Q_(100_000, "K")
    assert stored["quadrupole"].ac_field == Q_(200, "V")
    assert stored["gas"].radius == config["gas"].radius
    assert json.loads(dump_to_raw(stored)) == json.loads(dump_to_raw(config))

    # The upstream skimmer reads the final length as rmax.
    import apitofsim

    captured = {}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(apitofsim, "skimmer", lambda **kwargs: captured.update(kwargs))
        DerivedDataPreparer(output_db)._run_skimmer(stored)
    assert captured["rmax"] == Q_(0.7, "mm")


@pytest.mark.parametrize("invalid", ["missing", "width"])
def test_config_rejects_incompatible_histograms(config_form, invalid):
    from vms.forms import Q_

    form, params = config_form
    if invalid == "missing":
        del params[20]
        message = "Selected histogram configuration is missing"
    else:
        params[20] = (Q_(10, "K"), Q_(100_000, "K"))
        message = "Density and rate histograms must use the same bin width"
    with pytest.raises(ValueError, match=message):
        form.get_data()
