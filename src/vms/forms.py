from quart_wtf import QuartForm
from wtforms import Form
from wtforms.widgets import core as wtforms_widgets_core_module
from wtforms import (
    SelectField,
    FloatField,
    FormField,
    FieldList,
    HiddenField,
    IntegerField,
    BooleanField,
)
from wtforms.validators import InputRequired, Optional
from vms.utils import PairedRangeInputWidget
from quart import g


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


def mk_voltage_field(label, **kwargs):
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
    voltage1 = mk_voltage_field(r"\(V_0\)", default=-19)
    voltage2 = mk_voltage_field(r"\(V_1\)", default=-9)
    voltage3 = mk_voltage_field(r"\(V_2\)", default=-7)
    voltage4 = mk_voltage_field(r"\(V_3\)", default=-6)
    voltage5 = mk_voltage_field(r"\(V_4\)", default=11)


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
    gas_molecule_radius = FloatField(default=2.46e-10, validators=[InputRequired()])
    gas_molecule_mass = FloatField(default=4.8506e-26, validators=[InputRequired()])
    adiabatic_index = FloatField(default=1.4, validators=[InputRequired()])

    temperature_ = FloatField(
        "Temperature (K)",
        validators=[InputRequired()],
        default=300.0,
    )
    pressure_first_chamber = FloatField(
        "Pressure first chamber (Pa)",
        default=182.0,
        validators=[InputRequired()],
    )
    pressure_second_chamber = FloatField(
        "Pressure second chamber (Pa)",
        default=3.53,
        validators=[InputRequired()],
    )


class SimulationForm(Form):
    energy_max_density_of_state = FloatField(
        default=2.0e5, validators=[InputRequired()]
    )
    energy_max_rate_constant = FloatField(default=3.0e4, validators=[InputRequired()])
    energy_resolution = FloatField(default=1.0, validators=[InputRequired()])
    realizations = IntegerField(default=1000, validators=[InputRequired()])
    iterations_eq1 = IntegerField(default=1000, validators=[InputRequired()])
    iterations_eq2 = IntegerField(default=1000, validators=[InputRequired()])
    solved_points = IntegerField(default=1000, validators=[InputRequired()])
    tolerance = FloatField(default=1.0e-8, validators=[InputRequired()])


def get_cluster_choices():
    clusters = g.db.execute(
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
        from apitofsim import get_clusters, Gas
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
