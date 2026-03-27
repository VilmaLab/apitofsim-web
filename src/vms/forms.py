import functools

from pint import get_application_registry
from quart import Markup, g
from quart_wtf import QuartForm
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
from wtforms.widgets import core as wtforms_widgets_core_module

from vms.pintweb.wtforms import QuantityField
from vms.utils import PairedRangeInputWidget

ureg = get_application_registry()
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


def mk_instrument_form(hidden):
    def maybe_field(mk_field, *args, **kwargs):
        if hidden:
            return HiddenField(*args, **kwargs)
        else:
            return mk_field(*args, **kwargs)

    class InstrumentGeometryForm(Form):
        length_of_first_chamber = maybe_field(
            FloatField,
            "Length of 1st chamber (meters)",
            default=1.0e-3,
            validators=[InputRequired()],
        )
        length_of_skimmer = maybe_field(
            FloatField,
            "Length of skimmer (meters)",
            default=5.0e-4,
            validators=[InputRequired()],
        )
        length_between_skimmer_and_front_quadrupole = maybe_field(
            FloatField,
            "Length between skimmer and front quadrupole",
            default=2.44e-3,
            validators=[InputRequired()],
        )
        length_between_front_quadrupole_and_back_quadrupole = maybe_field(
            FloatField,
            "Length between front quadrupole and back quadrupole (meters)",
            default=0.101,
            validators=[InputRequired()],
        )
        length_between_back_quadrupole_and_2nd_skimmer = maybe_field(
            FloatField,
            "Length between back quadrupole and 2nd skimmer (meters)",
            default=4.48e-3,
            validators=[InputRequired()],
        )

    class SkimmerGeometryForm(Form):
        radius_at_smallest_cross_section_skimmer = maybe_field(
            FloatField,
            "Radius at smallest cross section skimmer (m)",
            default=5.0e-4,
            validators=[InputRequired()],
        )
        angle_of_skimmer = maybe_field(
            FloatField,
            "Angle of skimmer (multiple of PI)",
            default=0.25,
            validators=[InputRequired()],
        )

    class QuadrupoleForm(Form):
        dc_quadrupole = maybe_field(
            FloatField,
            "DC quadrupole",
            default=0.0,
            validators=[InputRequired()],
        )
        ac_quadrupole = maybe_field(
            FloatField,
            "AC quadrupole",
            default=200.0,
            validators=[InputRequired()],
        )
        radiofrequency_quadrupole = maybe_field(
            FloatField,
            "Radiofrequency quadrupole",
            default=1.3e6,
            validators=[InputRequired()],
        )
        half_distance_between_quadrupole_rods = maybe_field(
            FloatField,
            "Half-distance between quadrupole rods",
            default=6.0e-3,
            validators=[InputRequired()],
        )

    class InstrumentForm(Form):
        instrument_geometry = FormField(InstrumentGeometryForm)
        skimmer_geometry = FormField(SkimmerGeometryForm)
        quadrupole = FormField(QuadrupoleForm)

    return InstrumentForm


BuiltInInstrumentForm = mk_instrument_form(hidden=True)
CustomInstrumentForm = mk_instrument_form(hidden=False)


class GasForm(Form):
    gas_molecule_radius = QuantityField(
        "Gas molecule radius",
        validators=[InputRequired()],
        default=Q_(2.46e-10, "meters"),
    )
    gas_molecule_mass = QuantityField(
        "Gas molecule mass", default=Q_(4.8506e-26, "kg"), validators=[InputRequired()]
    )
    adiabatic_index = FloatField(default=1.4, validators=[InputRequired()])

    temperature_ = QuantityField(
        "Temperature",
        validators=[InputRequired()],
        default=Q_(300.0, "K"),
        units=["K", "°C"],
    )
    pressure_first_chamber = QuantityField(
        "Pressure first chamber",
        default=Q_(300.0, "Pa"),
        validators=[InputRequired()],
    )
    pressure_second_chamber = QuantityField(
        "Pressure second chamber",
        default=Q_(3.53, "Pa"),
        validators=[InputRequired()],
    )


@functools.cache
def get_histogram_precision_choices():
    dos_histograms = g.db.db.execute(
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
    k_rate_histograms = g.db.db.execute(
        """
        select distinct histogram_params_id, bin_width
        from histogram_params
        join k_rate
        on k_rate.histogram_params_id = histogram_params.id
        order by bin_width
        """
    ).fetchall()

    NAMES = ["superfine (slow)", "fine", "coarse (fast)"]
    choices = []
    for name, dos_hist, k_rate_hist in zip(
        NAMES, dos_histograms, k_rate_histograms, strict=True
    ):
        if dos_hist[1] != k_rate_hist[1]:
            raise ValueError(
                f"Mismatch in histogram precision choices: {dos_hist} vs {k_rate_hist}"
            )
        choices.append(
            ((dos_hist[0], k_rate_hist[0]), f"{name} [bin width = {dos_hist[1]:.1f} K]")
        )
    return choices


class SkimmerPrecisionForm(Form):
    iterations_eq1 = IntegerField(default=1000, validators=[InputRequired()])
    iterations_eq2 = IntegerField(default=1000, validators=[InputRequired()])
    solved_points = IntegerField(default=1000, validators=[InputRequired()])
    tolerance = FloatField(default=1.0e-8, validators=[InputRequired()])


class SimulationForm(Form):
    realizations = IntegerField(default=1000, validators=[InputRequired()])
    histogram_precision = SelectField(
        "Histogram precision",
        choices=get_histogram_precision_choices,
        validators=[InputRequired()],
        default=lambda: get_histogram_precision_choices()[1][0],
    )
    skimmer = FormField(SkimmerPrecisionForm)


def get_cluster_choices():
    clusters = g.db.db.execute(
        "SELECT cluster.id, cluster.common_name FROM cluster JOIN pathway ON pathway.cluster_id = cluster.id"
    ).fetchall()

    return [(None, "")] + clusters


class SingleFragmentationPathwayForm(Form):
    pathway = HiddenField(
        "Fragmentation pathway",
        validators=[InputRequired()],
    )
    enabled = BooleanField("Enabled", default=True)
    fragmentation_energy = FloatField(validators=[Optional()])


class SettingsForm(QuartForm):
    voltage = FormField(VoltageForm)
    instrument = FormField(BuiltInInstrumentForm)
    cluster = SelectField("Cluster", choices=get_cluster_choices)
    pathways = FieldList(FormField(SingleFragmentationPathwayForm))
    gas = FormField(GasForm)
    simulation = FormField(SimulationForm)

    def get_data(self):
        from apitofsim import Gas
        from numpy import array

        data = self.data
        result = {}
        result["voltage"] = array((v for v in data["voltage"].values()))
        """
        result["pathways"] = [
            {
                "fragmentation_energy": data["pathways"]["fragmentation_energy"],
                "pathway": get_clusters(CHAINS[data["pathways"]["chain"]]),
            }
        ]
        """
        gas = data["gas"]
        result["gas"] = Gas(
            *(
                gas[k]
                for k in ("gas_molecule_radius", "gas_molecule_mass", "adiabatic_index")
            )
        )
        result["config"] = {**data["instrument"], **data["simulation"]}
        return result
