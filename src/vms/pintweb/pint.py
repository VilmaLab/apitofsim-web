from typing import List, Optional

from pint import Quantity
from pint._typing import UnitLike
from sympy import symbols


def unitsystem_from_pint(ureg, quantities):
    in_sym = symbols("x")
    bits = ["["]
    for out_idx, output_quantity in enumerate(quantities):
        bits.append("{")
        bits.append(f" name: '{output_quantity}',")
        bits.append(f" label: '{output_quantity:~H}',")
        bits.append(" conversions: { ")
        has_conversions = False
        for in_idx, input_quantity in enumerate(quantities):
            if input_quantity == output_quantity:
                continue
            has_conversions = True
            sym_out_quant = ureg.Quantity(in_sym, input_quantity).to(output_quantity)
            function_body = str(sym_out_quant.m)
            bits.append(f"'{input_quantity}': ((x) => {function_body}) ")
            bits.append(", ")
        if has_conversions:
            bits.pop()
        bits.append(" } }")
        bits.append(", ")
    bits.pop()
    bits.append("]")
    return "".join(bits)


def _prepare_units(units, value=None, ureg=None):
    from pint import get_application_registry

    if ureg is None:
        ureg = get_application_registry()

    full_units = []
    if value is not None:
        full_units.append(ureg.Unit(value.units))
    for unit in units:
        unit = ureg.Unit(unit)
        if unit not in full_units:
            full_units.append(unit)

    return ureg, full_units


def _make_unitsystem(value: Optional[Quantity], units: List[UnitLike]) -> str:
    ureg, units = _prepare_units(units, value)
    return unitsystem_from_pint(ureg, units)
