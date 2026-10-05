from pathlib import Path

from apitofsim.api import ureg
from lxml import html
from markupsafe import Markup
from playwright.sync_api import expect

from vms.app import templates
from vms.forms import BuiltInInstrumentForm, CustomInstrumentForm

STATIC_JS = Path(__file__).resolve().parents[1] / "src/vms/static/index.js"
STATIC_CSS = STATIC_JS.with_suffix(".css")


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
