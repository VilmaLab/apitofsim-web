#!/usr/bin/env python3
"""Regenerate the instrument diagram sprite from the Inkscape drawing.

The drawing in ``assets/diagram_allannotations.svg`` has one layer per group of
related settings.  ``<svg-focus>`` needs three separate things per layer: the
clean picture, a shape to crop/highlight, and an annotation layer.  This script
splits the drawing accordingly and writes a single zero-sized SVG sprite to
``src/vms/templates/settings/_diagram_source.html``:

* ``#diagram`` -- the clean raster (``assets/diagram_clean.png``), used as the
  master picture by every ``<svg-focus>``,
* ``#region-<layer>`` -- the layer's rectangle, used for cropping and for the
  lightbox dimming mask,
* ``#labels-<layer>`` -- the layer's text and dimension lines.

Inkscape does the plain-SVG conversion (namespace and attribute cleanup).
Everything after that is lxml, so the output is stable and idempotent.

Usage:
    uv run python scripts/build_diagram.py
"""

from __future__ import annotations

import base64
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_SVG = REPO_ROOT / "assets" / "diagram_allannotations.svg"
SOURCE_PNG = REPO_ROOT / "assets" / "diagram_clean.png"
STATIC_PNG = REPO_ROOT / "src" / "vms" / "static" / "img" / "diagram.png"
TEMPLATE = REPO_ROOT / "src" / "vms" / "templates" / "settings" / "_diagram_source.html"

SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
XML_NS = "http://www.w3.org/XML/1998/namespace"

SPRITE_ID = "diagram-sprite"
DIAGRAM_ID = "diagram"
IMAGE_ID = "diagram-image"
IMAGE_URL = "{{ url_for('static', path='/img/diagram.png') }}"

# Matches the Tailwind default sans stack used by the rest of the site.
FONT_STACK = (
    "var(--font-sans, ui-sans-serif, system-ui, sans-serif, "
    "'Apple Color Emoji', 'Segoe UI Emoji', 'Segoe UI Symbol', 'Noto Color Emoji')"
)

# Inkscape layer name -> element id suffix for ``#region-*``/``#labels-*``.
# The ``voltages`` layer is deliberately absent: it is still empty.
LAYERS = (
    "geometry",
    "skimmer",
    "quadrupole",
    "pressure_temperature",
)

# Inkscape duplicates the label font on every <text>/<tspan>; a single
# font-family on the group replaces all of it.  ``white-space`` and
# ``xml:space`` would make the indentation of the exported XML visible.
STRIPPED_STYLE_DECLARATIONS = frozenset(
    {"font-family", "-inkscape-font-specification", "white-space"}
)


def run_inkscape(source: Path) -> etree._ElementTree:
    """Export ``source`` as plain SVG, dropping Inkscape's own metadata."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "plain.svg"
        subprocess.run(
            [
                "inkscape",
                "--export-type=svg",
                "--export-plain-svg",
                f"--export-filename={target}",
                str(source),
            ],
            check=True,
            capture_output=True,
        )
        return etree.parse(target)


def layer_labels(source: Path) -> dict[str, str]:
    """Map layer element id -> Inkscape layer name (plain export drops these)."""
    label_attr = f"{{{INKSCAPE_NS}}}label"
    groupmode_attr = f"{{{INKSCAPE_NS}}}groupmode"
    return {
        layer.get("id"): layer.get(label_attr)
        for layer in etree.parse(source).getroot().iter(f"{{{SVG_NS}}}g")
        if layer.get(groupmode_attr) == "layer"
    }


def find(root: etree._Element, element_id: str) -> etree._Element:
    matches = root.xpath(f'//*[@id="{element_id}"]')
    if not matches:
        raise ValueError(f"no element with id {element_id!r}")
    return matches[0]


def embedded_png(source: Path) -> bytes:
    """Return the raster embedded in the drawing."""
    image = find(etree.parse(source).getroot(), "image1")
    href = next(
        v for k, v in image.attrib.items() if etree.QName(k).localname == "href"
    )
    encoded = href.partition("base64,")[2]
    return base64.b64decode("".join(encoded.split()))


def strip_declarations(style: str) -> str:
    kept = [
        declaration
        for declaration in style.split(";")
        if declaration.partition(":")[0].strip() not in STRIPPED_STYLE_DECLARATIONS
    ]
    return ";".join(kept)


def build_region(rect: etree._Element, name: str) -> etree._Element:
    """Invisible shape marking the part of the instrument a layer describes.

    Deliberately unpainted: the referencing ``<use>`` supplies the fill, so the
    same shape is black inside the dimming mask and unfilled in the outline.
    """
    region = deepcopy(rect)
    region.attrib.pop(f"{{{INKSCAPE_NS}}}label", None)
    region.set("id", f"region-{name}")
    del region.attrib["style"]
    return region


def build_labels(group: etree._Element, name: str) -> etree._Element:
    labels = deepcopy(group)
    labels.set("id", f"labels-{name}")
    for element in labels.iter():
        element.attrib.pop(f"{{{XML_NS}}}space", None)
        style = element.get("style")
        if style is not None:
            element.set("style", strip_declarations(style))
    # One font-family on the group replaces the copy on every <text>/<tspan>.
    labels.set("style", f"font-family:{FONT_STACK}")
    return labels


def build_diagram(image: etree._Element) -> etree._Element:
    diagram = etree.Element(f"{{{SVG_NS}}}g", nsmap={None: SVG_NS})
    diagram.set("id", DIAGRAM_ID)
    master = deepcopy(image)
    for key in [k for k in master.attrib if etree.QName(k).localname == "href"]:
        del master.attrib[key]
    master.set("id", IMAGE_ID)
    master.set("href", IMAGE_URL)
    diagram.append(master)
    return diagram


def strip_ignorable_whitespace(root: etree._Element) -> None:
    """Drop whitespace-only text so pretty-printing controls the layout."""
    for element in root.iter():
        if element.text is not None and not element.text.strip():
            element.text = None
        if element.tail is not None and not element.tail.strip():
            element.tail = None


def build_sprite(source: Path) -> etree._Element:
    plain = run_inkscape(source).getroot()
    labels = layer_labels(source)

    sprite = etree.Element(f"{{{SVG_NS}}}svg", nsmap={None: SVG_NS})
    sprite.set("id", SPRITE_ID)
    # Zero-sized but not hidden: <svg-focus> measures the regions with getBBox(),
    # which some browsers report as empty for display:none subtrees.
    sprite.set("width", "0")
    sprite.set("height", "0")
    sprite.set("viewBox", plain.get("viewBox"))
    sprite.set("aria-hidden", "true")
    sprite.set("focusable", "false")
    sprite.set("style", "position:absolute")

    defs = find(plain, "defs1")
    if defs is not None:
        sprite.append(deepcopy(defs))

    sprite.append(build_diagram(find(plain, "image1")))

    by_label = {name: id_ for id_, name in labels.items() if name is not None}
    for name in LAYERS:
        layer = find(plain, by_label[name])
        rects = layer.findall(f"{{{SVG_NS}}}rect")
        if not rects:
            raise ValueError(f"layer {name!r} has no rectangle to use as its region")
        sprite.append(build_region(rects[0], name))

    for name in LAYERS:
        layer = find(plain, by_label[name])
        for group in layer.findall(f"{{{SVG_NS}}}g"):
            sprite.append(build_labels(group, name))

    strip_ignorable_whitespace(sprite)
    etree.cleanup_namespaces(sprite)
    return sprite


def write_if_changed(path: Path, data: bytes) -> bool:
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def main() -> None:
    png = SOURCE_PNG.read_bytes()
    if embedded_png(SOURCE_SVG) != png:
        raise SystemExit(
            f"{SOURCE_SVG.name} no longer embeds {SOURCE_PNG.name}; "
            "re-export the clean raster before regenerating"
        )

    sprite = build_sprite(SOURCE_SVG)
    markup = etree.tostring(sprite, pretty_print=True).decode("utf-8")
    template = (
        "{# Generated by scripts/build_diagram.py -- do not edit by hand. #}\n" + markup
    )

    changed = [
        str(path.relative_to(REPO_ROOT))
        for path, data in (
            (STATIC_PNG, png),
            (TEMPLATE, template.encode("utf-8")),
        )
        if write_if_changed(path, data)
    ]
    print("updated: " + ", ".join(changed) if changed else "already up to date")


if __name__ == "__main__":
    main()
