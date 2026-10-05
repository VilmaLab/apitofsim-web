import functools

from apitofsim.api import ureg
from markupsafe import Markup
from starlette_wtf import StarletteForm
from wtforms import (
    BooleanField,
    FieldList,
    FloatField,
    Form,
    FormField,
    HiddenField,
    IntegerField,
    SelectField,
)
from wtforms.validators import InputRequired, Optional
from wtforms.widgets import HiddenInput
from wtforms.widgets import core as wtforms_widgets_core_module

from vms.pintweb.wtforms import QuantityField
from vms.utils import PairedRangeInputWidget

Q_ = ureg.Quantity


# Cheeky monkeypatch to allow usage of htmx/alpine
def clean_key(key):
    key = key.rstrip("_")
    if (
        key.startswith("data_")
        or key.startswith("aria_")
        or key.startswith("hx_")
        or key.startswith("x_")
    ):
        key = key.replace("_", "-")
    return key


wtforms_widgets_core_module.clean_key = clean_key


def mk_voltage_field(num, **kwargs):
    label = Markup(rf'<span class="italic">V<sub>{num}</sub></em>')
    return FloatField(
        label,
        **kwargs,
        validators=[InputRequired()],
        render_kw={
            "type": "range",
            "min": "-50",
            "max": "50",
            "step": "1",
        },
        widget=PairedRangeInputWidget(),
    )


class VoltageForm(Form):
    voltage1 = mk_voltage_field(0, default=-19)
    voltage2 = mk_voltage_field(1, default=-9)
    voltage3 = mk_voltage_field(2, default=-7)
    voltage4 = mk_voltage_field(3, default=-6)
    voltage5 = mk_voltage_field(4, default=11)


class HiddenFloatField(FloatField):
    widget = HiddenInput()


class HiddenQuantityField(QuantityField):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # QuantityField.__init__ installs a QuantityInput on the instance, so the
        # class-level widget would otherwise be shadowed.
        self.widget = HiddenInput()


def mk_instrument_form(hidden):
    def maybe_quantity_field(*args, **kwargs):
        if hidden:
            return HiddenQuantityField(*args, **kwargs)
        else:
            return QuantityField(*args, **kwargs)

    def maybe_float_field(*args, **kwargs):
        if hidden:
            return HiddenFloatField(*args, **kwargs)
        else:
            return FloatField(*args, **kwargs)

    class InstrumentGeometryForm(Form):
        length_of_first_chamber = maybe_quantity_field(
            Markup("L<sub>0</sub>"),
            description="Length of the first chamber (up until the skimmer)",
            default=Q_(1.0e-3, "m"),
            units=["m", "mm"],
            validators=[InputRequired()],
        )
        length_of_skimmer = maybe_quantity_field(
            Markup("L<sub>sk</sub>"),
            description="Axial length of the skimmer",
            default=Q_(5.0e-4, "m"),
            units=["m", "mm"],
            validators=[InputRequired()],
        )
        length_between_skimmer_and_front_quadrupole = maybe_quantity_field(
            Markup("L<sub>1</sub>"),
            description="Distance from the skimmer to the front of the quadrupole in the second chamber",
            default=Q_(2.44e-3, "m"),
            units=["m", "mm"],
            validators=[InputRequired()],
        )
        length_between_front_quadrupole_and_back_quadrupole = maybe_quantity_field(
            Markup("L<sub>2</sub>"),
            description="Length of the quadrupole in the second chamber",
            default=Q_(0.101, "m"),
            units=["m", "mm"],
            validators=[InputRequired()],
        )
        length_between_back_quadrupole_and_2nd_skimmer = maybe_quantity_field(
            Markup("L<sub>3</sub>"),
            description="Distance from the back of the quadrupole in the second chamber up to the end of the chamber (up to the second skimmer)",
            default=Q_(4.48e-3, "m"),
            units=["m", "mm"],
            validators=[InputRequired()],
        )

    class SkimmerGeometryForm(Form):
        radius_at_smallest_cross_section_skimmer = maybe_quantity_field(
            "Radius",
            description="Radius of the skimmer opening at its narrowest point.",
            default=Q_(5.0e-4, "m"),
            units=["m", "mm"],
            validators=[InputRequired()],
        )
        angle_of_skimmer = maybe_float_field(
            "Angle",
            description="Skimmer angle expressed as a multiple of pi; 0.25 means pi/4.",
            default=0.25,
            validators=[InputRequired()],
        )

    class QuadrupoleForm(Form):
        dc_quadrupole = maybe_quantity_field(
            "DC",
            description="Direct-current voltage applied to the quadrupole.",
            default=Q_(0.0, "V"),
            units=["V", "kV"],
            validators=[InputRequired()],
        )
        ac_quadrupole = maybe_quantity_field(
            "AC",
            description="Alternating-current voltage applied to the quadrupole.",
            default=Q_(200.0, "V"),
            units=["V", "kV"],
            validators=[InputRequired()],
        )
        radiofrequency_quadrupole = maybe_quantity_field(
            "RF",
            description="Radio frequency of the quadrupole's alternating voltage.",
            default=Q_(1.3e6, "Hz"),
            units=["Hz", "kHz", "MHz"],
            validators=[InputRequired()],
        )
        half_distance_between_quadrupole_rods = maybe_quantity_field(
            "Radius",
            description="Half of the spacing between opposing quadrupole rods.",
            default=Q_(6.0e-3, "m"),
            units=["m", "mm"],
            validators=[InputRequired()],
        )

    class InstrumentForm(Form):
        instrument_geometry = FormField(InstrumentGeometryForm)
        skimmer_geometry = FormField(SkimmerGeometryForm)
        quadrupole = FormField(QuadrupoleForm)

    return InstrumentForm


BuiltInInstrumentForm = mk_instrument_form(hidden=True)
CustomInstrumentForm = mk_instrument_form(hidden=False)

INSTRUMENT_CHOICES = [("default", "Default"), ("custom", "Custom")]
INSTRUMENT_FORMS = {
    "default": BuiltInInstrumentForm,
    "custom": CustomInstrumentForm,
}


def instrument_form_class(model):
    # Unknown or missing models fall back to the fixed default instrument
    return INSTRUMENT_FORMS.get(model, BuiltInInstrumentForm)


class GasForm(Form):
    gas_molecule_radius = QuantityField(
        "Radius",
        description="Effective radius of a gas molecule used by the model.",
        validators=[InputRequired()],
        default=Q_(2.46e-10, "meters"),
    )
    gas_molecule_mass = QuantityField(
        "Mass",
        description="Mass of one gas molecule.",
        default=Q_(4.8506e-26, "kg"),
        validators=[InputRequired()],
    )
    adiabatic_index = FloatField(
        description="Ratio of the gas heat capacities at constant pressure and volume.",
        default=1.4,
        validators=[InputRequired()],
    )

    temperature_ = QuantityField(
        "T",
        description="Gas temperature; you can enter it in kelvin or degrees Celsius.",
        validators=[InputRequired()],
        default=Q_(300.0, "K"),
        units=["K", "°C"],
    )
    pressure_first_chamber = QuantityField(
        Markup("P<sub>1</sub>"),
        description="Gas pressure in the first chamber.",
        default=Q_(300.0, "Pa"),
        validators=[InputRequired()],
    )
    pressure_second_chamber = QuantityField(
        Markup("P<sub>2</sub>"),
        description="Gas pressure in the second chamber.",
        default=Q_(3.53, "Pa"),
        validators=[InputRequired()],
    )


@functools.cache
def get_histogram_precision_choices():
    from vms.app import current_db

    db = current_db.get()
    dos_histograms = db.db.execute(
        """
        with dos_histogram_ids as (
            select distinct histogram_params_id from cluster_dos
            union
            select distinct histogram_params_id from products_dos
        )
        select histogram_params_id, bin_width
        from histogram_params
        join dos_histogram_ids
        on dos_histogram_ids.histogram_params_id = histogram_params.id
        order by bin_width
        """
    ).fetchall()
    k_rate_histograms = db.db.execute(
        """
        select distinct histogram_params_id, bin_width
        from histogram_params
        join k_rate
        on k_rate.histogram_params_id = histogram_params.id
        order by bin_width
        """
    ).fetchall()

    NAMES = ["superfine (slow)", "fine", "coarse (fast)"]
    # Databases built from a reduced dataset (such as the test database) can have
    # a different number of histogram configurations, in which case the only
    # meaningful label is the bin width.
    names = NAMES if len(dos_histograms) == len(NAMES) else [""] * len(dos_histograms)
    choices = []
    for name, dos_hist, k_rate_hist in zip(
        names, dos_histograms, k_rate_histograms, strict=True
    ):
        if dos_hist[1] != k_rate_hist[1]:
            raise ValueError(
                f"Mismatch in histogram precision choices: {dos_hist} vs {k_rate_hist}"
            )
        dos_hid = dos_hist[0]
        k_rate_hid = k_rate_hist[0]
        choices.append(
            (
                f"{dos_hid},{k_rate_hid}",
                f"{name} [bin width = {dos_hist[1]:.1f} K]".strip(),
            )
        )
    return choices


def get_default_histogram_precision():
    choices = get_histogram_precision_choices()
    return choices[1 if len(choices) > 1 else 0][0]


class SkimmerPrecisionForm(Form):
    iterations_eq1 = IntegerField(
        description="Iteration limit for the first skimmer equation.",
        default=1000,
        validators=[InputRequired()],
    )
    iterations_eq2 = IntegerField(
        description="Iteration limit for the second skimmer equation.",
        default=1000,
        validators=[InputRequired()],
    )
    solved_points = IntegerField(
        description="Number of points used to solve the skimmer equations.",
        default=1000,
        validators=[InputRequired()],
    )
    tolerance = FloatField(
        description="Numerical tolerance for the skimmer calculation.",
        default=1.0e-8,
        validators=[InputRequired()],
    )


class SimulationForm(Form):
    realizations = IntegerField(
        description="Number of simulated particle runs.",
        default=1000,
        validators=[InputRequired()],
    )
    histogram_precision = SelectField(
        "Histogram precision",
        description="Bin width for the input histograms; finer bins take longer to process.",
        choices=get_histogram_precision_choices,
        validators=[InputRequired()],
        default=get_default_histogram_precision,
    )
    skimmer = FormField(SkimmerPrecisionForm)


def get_cluster_choices():
    from vms.app import current_db

    clusters = (
        current_db.get()
        .db.execute(
            "SELECT cluster.id, cluster.common_name FROM cluster JOIN pathway ON pathway.cluster_id = cluster.id"
        )
        .fetchall()
    )

    return [(None, "")] + clusters


class SingleFragmentationPathwayForm(Form):
    pathway = HiddenField(
        "Fragmentation pathway",
        validators=[InputRequired()],
    )
    enabled = BooleanField("Enabled", default=True)
    fragmentation_energy = FloatField(validators=[Optional()])


def canonical_numpy_quantity(unit, *quantities):
    from numpy import array

    return Q_(array([quantity.to(unit).magnitude for quantity in quantities]), unit)


class SettingsForm(StarletteForm):
    voltage = FormField(VoltageForm)
    instrument_model = SelectField(
        "Instrument",
        choices=INSTRUMENT_CHOICES,
        default="default3000",
    )
    instrument = FormField(BuiltInInstrumentForm)
    cluster = SelectField(
        "Cluster",
        choices=get_cluster_choices,
    )
    pathways = FieldList(FormField(SingleFragmentationPathwayForm), min_entries=1)
    gas = FormField(GasForm)
    simulation = FormField(SimulationForm)

    def __init__(self, request, *args, **kwargs):
        formdata = kwargs.get("formdata")
        model = formdata.get("instrument_model") if formdata is not None else None
        instrument_cls = instrument_form_class(model)
        # Bind the same instrument schema the rendered fragment used, so the
        # server parses and validates what the client actually submitted.
        self._unbound_fields = [
            (name, FormField(instrument_cls) if name == "instrument" else field)
            for name, field in type(self)._unbound_fields
        ]
        super().__init__(request, *args, **kwargs)

    def get_data(self):
        from apitofsim import Gas, Quadrupole
        from numpy import array

        from vms.app import current_db

        data = self.data
        result = {}
        result["voltage"] = Q_(array(list(data["voltage"].values())), "V")
        result["pathways"] = [
            pathway["pathway"] for pathway in data["pathways"] if pathway["enabled"]
        ]
        gas = data["gas"]
        result["gas"] = Gas(
            *(
                gas[k]
                for k in ("gas_molecule_radius", "gas_molecule_mass", "adiabatic_index")
            )
        )
        pressures = canonical_numpy_quantity(
            "Pa",
            gas["pressure_first_chamber"],
            gas["pressure_second_chamber"],
        )
        lengths = canonical_numpy_quantity(
            "m",
            data["instrument"]["instrument_geometry"]["length_of_first_chamber"],
            data["instrument"]["instrument_geometry"]["length_of_skimmer"],
            data["instrument"]["instrument_geometry"][
                "length_between_skimmer_and_front_quadrupole"
            ],
            data["instrument"]["instrument_geometry"][
                "length_between_front_quadrupole_and_back_quadrupole"
            ],
            data["instrument"]["instrument_geometry"][
                "length_between_back_quadrupole_and_2nd_skimmer"
            ],
        )
        quadrupole_dict = data["instrument"]["quadrupole"]
        quadrupole = Quadrupole(
            quadrupole_dict["dc_quadrupole"],
            quadrupole_dict["ac_quadrupole"],
            quadrupole_dict["radiofrequency_quadrupole"],
            quadrupole_dict["half_distance_between_quadrupole_rods"],
        )
        result["quadrupole"] = quadrupole
        histogram_precision = tuple(
            int(x) for x in data["simulation"]["histogram_precision"].split(",")
        )
        result["config"] = {
            **data["instrument"],
            **data["simulation"],
            "dc": Q_(
                data["instrument"]["skimmer_geometry"][
                    "radius_at_smallest_cross_section_skimmer"
                ],
                "meter",
            ),
            "alpha_factor": Q_(
                data["instrument"]["skimmer_geometry"]["angle_of_skimmer"],
                "multiple_of_PI",
            ),
            "N_iter": data["simulation"]["skimmer"]["iterations_eq1"],
            "M_iter": data["simulation"]["skimmer"]["iterations_eq2"],
            "resolution": data["simulation"]["skimmer"]["solved_points"],
            "tolerance": data["simulation"]["skimmer"]["tolerance"],
            "T": gas["temperature_"],
            "gas": result["gas"],
            "pressures": pressures,
            "lengths": lengths,
            "histogram_precision": histogram_precision,
        }
        result["histograms"] = tuple(
            (current_db.get().get_histogram_params(x) for x in histogram_precision)
        )
        return result
