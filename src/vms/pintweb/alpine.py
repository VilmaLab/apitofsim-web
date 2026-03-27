from typing import List

from jinja2 import Environment
from markupsafe import Markup
from pint import Quantity
from pint._typing import UnitLike
from quart import current_app

from vms.pintweb.pint import _make_unitsystem


def get_jinja_env():
    jinja_loader = current_app.jinja_loader
    return Environment(loader=jinja_loader)


def quantity_display(value: Quantity, units: List[UnitLike]):
    jinja_env = get_jinja_env()
    unit_system = _make_unitsystem(value, units)
    magnitude = value.magnitude
    unit = value.units
    jinja_env.get_template("pintweb/_quantity_display.html").render(
        {
            "unit_system": unit_system,
            "magnitude": magnitude,
            "unit": unit,
        }
    )


def quantity_input(
    value: Quantity,
    units: List[UnitLike],
    *,
    name,
    container_params,
    unit_switcher="select",
):
    jinja_env = get_jinja_env()
    unit_system = _make_unitsystem(value, units)
    magnitude = value.magnitude
    unit = value.units

    return Markup(
        jinja_env.get_template("pintweb/_quantity_input.html").render(
            {
                "name": name,
                "unit_system": unit_system,
                "magnitude": magnitude,
                "unit": unit,
                "container_params": container_params,
                "unit_select": unit_switcher == "select",
            }
        )
    )
