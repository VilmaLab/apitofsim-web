from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import duckdb
import pytest
from apitofsim.transmission import VOLTAGES_NEGATIVE, VOLTAGES_POSITIVE
from apitofsim.workflow.db import ClusterDatabase
from playwright.sync_api import expect
from starlette.datastructures import FormData
from starlette.requests import Request
from wtforms import FormField, SelectField

from vms.app import app, pathways_context, templates
from vms.db import current_db
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
            SettingsForm.cluster.kwargs
            | {
                "choices": [
                    ("", ""),
                    ("1", "Test cluster", {"data-charge": -1}),
                    ("2", "Positive cluster", {"data-charge": 1}),
                    ("3", "Another positive cluster", {"data-charge": 2}),
                    ("4", "Neutral cluster", {"data-charge": 0}),
                ]
            }
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
        "electronic_energy DOUBLE, ase_mol_id INTEGER, charge INTEGER DEFAULT -1)"
    )
    db.db.execute(
        "INSERT INTO cluster (id, common_name, atomic_mass, electronic_energy, ase_mol_id) "
        "VALUES (1, 'Parent', 30, -3, NULL), "
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
    assert sorted(choices[1:]) == [
        (1, "Parent", {"data-charge": -1}),
        (2, "Other parent", {"data-charge": -1}),
    ]


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
    assert [entry.name for entry in form.pathways] == ["pathways-0", "pathways-1"]


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


def assert_voltages(page, values):
    for index, value in enumerate(values, 1):
        for kind in ("number", "range"):
            expect(
                page.locator(f'input[type="{kind}"][name="voltage-voltage{index}"]')
            ).to_have_value(str(value))


def test_automatic_voltage_defaults_and_notices(page, form):
    form.cluster.data = ""
    load(page, form, "fragmentationpathways")
    negative = VOLTAGES_NEGATIVE.magnitude.tolist()
    positive = VOLTAGES_POSITIVE.magnitude.tolist()
    assert_voltages(page, negative)
    notice = page.locator('[role="status"]').filter(has_text="Voltages have changed")
    expect(notice).to_have_count(0)
    page.select_option("#cluster", "2")
    assert_voltages(page, positive)
    expect(notice).to_have_text(
        "Voltages have changed to the default for positive clusters. ×"
    )
    page.set_viewport_size({"width": 375, "height": 800})
    assert notice.bounding_box()["width"] <= 375
    page.get_by_role("button", name="Dismiss voltage notice").click()
    page.select_option("#cluster", "3")
    expect(notice).to_have_count(0)
    assert_voltages(page, positive)
    page.select_option("#cluster", "1")
    assert_voltages(page, negative)
    expect(notice).to_contain_text("default for negative clusters")
    page.get_by_role("button", name="Dismiss voltage notice").click()
    page.select_option("#cluster", "4")
    page.select_option("#cluster", "")
    expect(notice).to_have_count(0)
    assert_voltages(page, negative)


@pytest.mark.parametrize("kind", ["number", "range"])
def test_edited_voltages_require_explicit_reset(page, form, kind):
    load(page, form)
    control = page.locator(f'input[type="{kind}"][name="voltage-voltage1"]')
    control.fill("-23")
    control.fill("-19")
    page.click('a[href="#fragmentationpathways"]')
    page.select_option("#cluster", "2")
    assert_voltages(page, VOLTAGES_NEGATIVE.magnitude.tolist())
    warning = page.locator('[role="status"]').filter(
        has_text="voltages have not changed"
    )
    expect(warning).to_contain_text("cluster charge has changed to positive")
    page.get_by_role("button", name="Dismiss voltage notice").click()
    page.select_option("#cluster", "1")
    expect(warning).to_contain_text("cluster charge has changed to negative")
    page.select_option("#cluster", "2")
    warning.get_by_role("link", name="Voltages page").click()
    expect(page.locator('div[name="voltagecontrols"]')).to_be_visible()
    page.get_by_role("button", name="Set to default").click()
    assert_voltages(page, VOLTAGES_POSITIVE.magnitude.tolist())
    expect(page.locator('[name="voltages_edited"]')).to_have_value("false")
    expect(warning).to_have_count(0)
    page.click('a[href="#fragmentationpathways"]')
    page.select_option("#cluster", "1")
    assert_voltages(page, VOLTAGES_NEGATIVE.magnitude.tolist())


def test_voltage_reset_clears_errors_without_selected_cluster(page, form):
    form.cluster.data = ""
    form.voltage.voltage1.data = -23
    form.voltage.voltage1.errors = ["Invalid voltage"]
    load(page, form)
    page.click('a[href="#voltagecontrols"]')
    page.get_by_role("button", name="Set to default").click()
    assert_voltages(page, VOLTAGES_NEGATIVE.magnitude.tolist())
    number = page.locator('input[type="number"][name="voltage-voltage1"]')
    assert number.evaluate("el => el.validity.customError") is False
    expect(number).not_to_have_attribute("aria-invalid", "true")


@pytest.mark.parametrize("edited", [False, True])
def test_voltage_state_survives_failed_submission(page, form, edited):
    load(page, form, "fragmentationpathways")
    page.select_option("#cluster", "2")
    if edited:
        page.click('a[href="#voltagecontrols"]')
        page.locator('input[type="number"][name="voltage-voltage1"]').fill("23")
    submitted = ExampleSettingsForm(
        form._request,
        formdata=FormData(page.locator("form").evaluate("el => [...new FormData(el)]")),
    )
    submitted.simulation.realizations.errors = ["Invalid realizations"]
    load(page, submitted, "fragmentationpathways")
    expect(page.locator('[name="voltages_edited"]')).to_have_value(str(edited).lower())
    expected = VOLTAGES_POSITIVE.magnitude.tolist()
    if edited:
        expected[0] = 23
    assert_voltages(page, expected)
    page.click('a[href="#fragmentationpathways"]')
    page.select_option("#cluster", "1")
    if edited:
        assert_voltages(page, expected)
        expect(
            page.locator('[role="status"]').filter(has_text="voltages have not changed")
        ).to_be_visible()
    else:
        assert_voltages(page, VOLTAGES_NEGATIVE.magnitude.tolist())


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
        "voltages_edited",
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
    enabled = page.locator('input[name^="pathways-"][name$="-enabled"]').first
    enabled.uncheck()
    energy_name = energy.get_attribute("name")
    enabled_name = enabled.get_attribute("name")
    page.click('a[href="#instrument"]')
    page.select_option("#instrument-select", "custom")
    angle = page.locator("#instrument-skimmer_geometry-angle_of_skimmer")
    expect(angle).to_be_visible()
    angle_name = angle.get_attribute("name")

    # Numeric controls cannot contain arbitrary text. Simulate a malformed POST
    # to exercise server validation and restoration of the rest of the form.
    def invalid_post(route):
        data = dict(parse_qsl(route.request.post_data, keep_blank_values=True))
        data[energy_name] = "invalid energy"
        data[angle_name] = "invalid angle"
        route.continue_(post_data=urlencode(data))

    page.route(server + "/", invalid_post, times=1)
    page.click('a[href="#simulation"]')
    page.click('button[type="submit"]')
    expect(page.locator(f'input[name="{energy_name}"]')).to_be_focused()
    expect(page.locator(f'input[name="{energy_name}"]')).to_have_attribute(
        "value", "invalid energy"
    )
    expect(page.locator(f'input[name="{enabled_name}"]')).not_to_be_checked()
    expect(page.locator('[role="alert"]')).to_have_count(0)
    page.locator(f'input[name="{energy_name}"]').fill("0.1")
    page.click('button[type="submit"]')
    expect(angle).to_be_focused()
    expect(angle).to_have_attribute("value", "invalid angle")
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
    # Form iteration order must not determine the simulator's voltage order.
    form.voltage.form._fields = dict(reversed(form.voltage.form._fields.items()))
    form.gas.pressure_first_chamber.data = Q_(0.195, "kPa")
    form.gas.pressure_second_chamber.data = Q_(0.035, "mbar")
    form.instrument.quadrupole.dc_quadrupole.data = Q_(0.015, "kV")
    form.instrument.quadrupole.ac_quadrupole.data = Q_(0.25, "kV")
    form.instrument.quadrupole.radiofrequency_quadrupole.data = Q_(1.5, "MHz")
    form.instrument.quadrupole.half_distance_between_quadrupole_rods.data = Q_(6, "mm")
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
    form.pathways[0].pathway.process_formdata(["12"])
    arguments = form.get_data()
    assert arguments["pathways"] == [12]
    config = arguments["config"]
    assert set(arguments) == {"config", "pathways", "histogram_precision"}
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
    np.testing.assert_allclose(stored["pressures"].to("Pa").magnitude, [195, 3.5])
    assert stored["alpha_factor"] == Q_(0.25, "halfturn")
    assert stored["quadrupole"].dc_field == Q_(15, "V")
    assert stored["quadrupole"].ac_field == Q_(250, "V")
    assert stored["quadrupole"].radiofrequency == Q_(1.5, "MHz")
    assert stored["quadrupole"].r_quadrupole == Q_(6, "mm")
    assert stored["gas"].radius == config["gas"].radius
    assert json.loads(dump_to_raw(stored)) == json.loads(dump_to_raw(config))

    # The upstream skimmer reads the final length as rmax.
    import apitofsim

    captured = {}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            apitofsim.api,
            "_skimmer_with_parameters",
            lambda T0, P0, rmax, *args, **kwargs: captured.update(rmax=rmax),
        )
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


@pytest.mark.parametrize("tab", ["gas", "simulation"])
@pytest.mark.parametrize("width", [320, 360, 390, 640, 768, 1280])
def test_settings_forms_fit_viewport(page, form, tab, width):
    page.set_viewport_size({"width": width, "height": 844})
    page.route("http://testserver/", lambda route: route.fulfill(body=render(form)))
    page.route(
        "http://testserver/static/**",
        lambda route: route.fulfill(
            path=STATIC_JS.parent
            / route.request.url.split("/static/", 1)[1].split("?")[0]
        ),
    )
    page.goto(f"http://testserver/#{tab}")
    panel = page.locator(f'div[name="{tab}"]')
    expect(panel).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for control in panel.locator('input:not([type="hidden"]), select').all():
        bounds = control.bounding_box()
        assert 0 <= bounds["x"] < bounds["x"] + bounds["width"] <= width
