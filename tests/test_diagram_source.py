"""The diagram sprite and the fragments the templates ask it for must agree."""

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from lxml import etree

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "src" / "vms" / "templates"
SVG_NS = "http://www.w3.org/2000/svg"
STATIC_URL = "/static/img/diagram.png"

CONSUMERS = (
    "settings/_render_instrument.html",
    "settings/settings.html",
)


@pytest.fixture(scope="module")
def sprite() -> etree._Element:
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template("settings/_diagram_source.html")
    markup = template.render(url_for=lambda *args, **kwargs: STATIC_URL)
    return etree.fromstring(markup.encode())


def test_sprite_does_not_embed_the_raster(sprite: etree._Element) -> None:
    image = sprite.xpath('//*[@id="diagram-image"]')[0]
    assert image.get("href") == STATIC_URL
    assert "base64" not in etree.tostring(sprite).decode()


def test_sprite_is_zero_sized(sprite: etree._Element) -> None:
    # getBBox still works on a zero-sized SVG, but not on a display:none one.
    assert (sprite.get("width"), sprite.get("height")) == ("0", "0")
    assert sprite.get("style") != "display:none"


def test_every_layer_has_a_region_and_optional_labels(sprite: etree._Element) -> None:
    present = {
        element.get("id")
        for element in sprite.iter(f"{{{SVG_NS}}}rect", f"{{{SVG_NS}}}g")
    }

    assert "diagram" in present
    assert {"labels-geometry", "labels-pressure_temperature"} <= present
    for layer in ("geometry", "skimmer", "quadrupole", "pressure_temperature"):
        assert f"region-{layer}" in present


def test_templates_only_reference_fragments_the_sprite_defines(
    sprite: etree._Element,
) -> None:
    known = {
        element.get("id")
        for element in sprite.iter(
            f"{{{SVG_NS}}}g", f"{{{SVG_NS}}}rect", f"{{{SVG_NS}}}image"
        )
    }

    references: set[str] = set()
    for name in CONSUMERS:
        markup = (TEMPLATE_DIR / name).read_text()
        for attribute in ("diagram", "region", "labels"):
            references.update(re.findall(rf'{attribute}="#([^"]+)"', markup))

    assert references
    assert references <= known, f"unknown fragments: {sorted(references - known)}"
