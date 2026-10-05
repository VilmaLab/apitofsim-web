from pathlib import Path

import pytest
from playwright.sync_api import expect
from starlette.datastructures import FormData
from starlette.requests import Request
from wtforms import FormField, SelectField

from vms.app import app, templates
from vms.forms import SettingsForm, SimulationForm, settings_errors

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
