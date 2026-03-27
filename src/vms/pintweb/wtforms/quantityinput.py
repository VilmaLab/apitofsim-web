from pint import get_application_registry
from wtforms.fields import Field
from wtforms.widgets.core import html_params

from vms.pintweb.alpine import quantity_input

from ..pint import _prepare_units


class QuantityInput:
    html_params = staticmethod(html_params)

    validation_attrs = [
        "required",
        "disabled",
        "readonly",
    ]

    def __init__(self, units):
        self.units = units

    def __call__(self, field, **kwargs):
        ureg = get_application_registry()
        Q_ = ureg.Quantity
        kwargs.setdefault("id", field.id)
        value = kwargs.get("value", field._value())
        # flags = getattr(field, "flags", {})
        # for k in dir(flags):
        # if k in self.validation_attrs and k not in kwargs:
        # kwargs[k] = getattr(flags, k)
        kwargs.setdefault("class", "")
        kwargs["class"] += " inline-block relative text-right w-50 pr-10"
        container_params = self.html_params(**kwargs)
        if value:
            magnitude, unit = value.rsplit(" ", 1)
            magnitude = float(magnitude)
            quantity = Q_(magnitude, unit)
        else:
            quantity = Q_(0, self.units[0])
        return quantity_input(
            quantity, self.units, name=field.name, container_params=container_params
        )


class QuantityField(Field):
    def __init__(self, label=None, validators=None, units=None, **kwargs):
        super().__init__(label, validators, **kwargs)
        try:
            default = self.default()  # type: ignore
        except TypeError:
            default = self.default
        ureg, units = _prepare_units(units or [], default, kwargs.get("ureg"))
        self.ureg = ureg
        self.units = units
        if len(units) == 0:
            raise ValueError(
                "At least one unit (including from the default value) must be given."
            )
        self.widget = QuantityInput(units)

    def _value(self):
        if self.raw_data:
            return self.raw_data[0]
        if self.data is not None:
            return f"{self.data.magnitude} {self.data.units}"
        return ""

    def process_formdata(self, valuelist):
        ureg = get_application_registry()
        Q_ = ureg.Quantity

        if not valuelist:
            return

        first = valuelist[0]
        magnitude, unit = first.rsplit(" ", 1)

        quantity = Q_(float(magnitude), unit)
        if ureg.Unit(quantity.units) not in self.units:
            raise ValueError(
                f"Unit '{quantity.units}' not in allowed units {self.units}."
            )
        self.data = quantity
