from pathlib import Path

import pytest
from apitofsim.api import ureg
from lxml import html
from markupsafe import Markup
from pint import DimensionalityError
from playwright.sync_api import expect
from starlette.datastructures import FormData

from vms.app import templates
from vms.forms import BuiltInInstrumentForm, CustomInstrumentForm, GasForm

STATIC_JS = Path(__file__).resolve().parents[1] / "src/vms/static/index.js"
STATIC_CSS = STATIC_JS.with_suffix(".css")


@pytest.mark.parametrize(
    "value",
    [
        "4.8506e-26 kg",
        "4.8506e-23 g",
        "29.210995970456 g/mol",
        "29.210995970456 gram / mole",
    ],
)
def test_gas_mass_submission_is_particle_mass(value):
    field = GasForm(FormData({"gas_molecule_mass": value})).gas_molecule_mass
    assert not field.process_errors
    assert field.data.units == ureg.kg
    assert field.data.magnitude == pytest.approx(4.8506e-26, rel=1e-12, abs=0)
    # The particle-mass interpretation is local to this field.
    with pytest.raises(DimensionalityError):
        ureg.Quantity(1, "g/mol").to("kg")


def test_gas_mass_switches_between_particle_and_molar_mass(page):
    field = GasForm().gas_molecule_mass
    page.set_content(f"<form>{field()}</form>")
    page.add_script_tag(path=str(STATIC_JS))
    selector = page.get_by_role("combobox", name="Unit")
    value = page.locator("input[type=text]")
    for unit, magnitude in [
        ("gram / mole", "29.211"),
        ("gram", "4.8506e-23"),
        ("kilogram", "4.8506e-26"),
    ]:
        selector.select_option(unit)
        expect(value).to_have_value(magnitude)
    selector.select_option("gram / mole")
    value.fill("28.014")
    hidden = page.locator("input[type=hidden]")
    expect(hidden).to_have_value("28.014 gram / mole")
    submitted = hidden.input_value()
    field = GasForm(FormData({"gas_molecule_mass": submitted})).gas_molecule_mass
    assert field.data.to("kg").magnitude == pytest.approx(
        28.014 / 6.02214076e26, rel=1e-12, abs=0
    )


def render_display(value, units, **attributes):
    return templates.env.from_string(
        "{{ value|quantity_display(units, **attributes) }}"
    ).render(value=value, units=units, attributes=attributes)


def test_display_attributes_are_escaped():
    rendered = render_display(
        ureg.Quantity(1, "m"), ["cm"], class_="border", title='"><script>'
    )
    display = html.fromstring(rendered)
    assert display.get("class") == "border"
    assert display.get("title") == '"><script>'
    assert not display.xpath(".//script | .//input")
    assert isinstance(
        templates.env.filters["quantity_display"](ureg.Quantity(1, "m"), ["cm"]),
        Markup,
    )


def test_display_converts_units_without_editing(page):
    page.set_content(render_display(ureg.Quantity(273.15, "K"), ["°C"]))
    page.add_script_tag(path=str(STATIC_JS))
    selector = page.get_by_role("combobox", name="Unit")
    expect(selector).to_have_value("kelvin")
    selector.select_option("degree_Celsius")
    expect(page.locator("span")).to_have_text("0")
    selector.select_option("kelvin")
    expect(page.locator("span")).to_have_text("273.15")
    expect(page.locator("input")).to_have_count(0)


def test_table_display_keeps_submitted_quantity(page):
    form = BuiltInInstrumentForm()
    field = form.instrument_geometry.length_of_first_chamber
    field.data = ureg.Quantity(0.002, "m")
    rendered = templates.env.get_template("ui/_table_form.html").render(
        form=form.instrument_geometry, include_fields=[field.short_name]
    )
    page.set_content(f"<form>{rendered}</form>")
    page.add_script_tag(path=str(STATIC_JS))
    selector = page.get_by_role("combobox", name="Unit")
    selector.select_option("millimeter")
    expect(page.locator("span[x-text='viewValue']")).to_have_text("2")
    assert page.locator(f"input[name='{field.name}']").input_value() == "0.002 meter"
    assert page.locator("form").evaluate(
        "form => Object.fromEntries(new FormData(form))"
    ) == {field.name: "0.002 meter"}


def test_table_keeps_plain_readonly_fields():
    form = BuiltInInstrumentForm().skimmer_geometry
    form.angle_of_skimmer.data = 0.5
    rendered = templates.env.get_template("ui/_table_form.html").render(
        form=form, include_fields=["angle_of_skimmer"]
    )
    assert "0.5" in html.fromstring(rendered).text_content()
    assert "quantityinput" not in rendered


def test_input_reserves_unit_width_and_thin_space(page):
    field = CustomInstrumentForm().instrument_geometry.length_of_first_chamber
    page.set_content(str(field(class_="border border-black p-1 text-right w-50")))
    page.add_style_tag(path=str(STATIC_CSS))
    page.add_script_tag(path=str(STATIC_JS))
    selector = page.get_by_role("combobox", name="Unit")
    expect(selector).to_have_value("meter")
    value_input = page.locator("input[type='text']")
    original_input_width = value_input.bounding_box()["width"]
    original_unit_width = selector.bounding_box()["width"]
    selector.select_option("millimeter")
    expect(value_input).to_have_value("1")
    assert selector.bounding_box()["width"] > original_unit_width
    assert value_input.bounding_box()["width"] < original_input_width
    input_box = value_input.bounding_box()
    gap = selector.bounding_box()["x"] - input_box["x"] - input_box["width"]
    font_size = value_input.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
    assert abs(gap - font_size * 0.2) < 0.1
    assert value_input.get_attribute("id") == field.id
    expect(page.locator(f"input[name='{field.name}']")).to_have_value("0.001 meter")
