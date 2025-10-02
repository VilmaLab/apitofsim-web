from quart_wtf import QuartForm
from wtforms import Form
from wtforms import SelectField, FloatField, FormField, HiddenField, IntegerField
from wtforms.validators import InputRequired
from vms.utils import PairedRangeInputWidget


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
    voltage1 = mk_voltage_field("Voltage 1", default=-19)
    voltage2 = mk_voltage_field("Voltage 2", default=-9)
    voltage3 = mk_voltage_field("Voltage 3", default=-7)
    voltage4 = mk_voltage_field("Voltage 4", default=-6)
    voltage5 = mk_voltage_field("Voltage 5", default=11)


def mk_instrument_form(hidden):
    def maybe_field(mk_field, *args, **kwargs):
        if hidden:
            return HiddenField(*args, **kwargs)
        else:
            return mk_field(*args, **kwargs)

    class InstrumentForm(Form):
        temperature_ = maybe_field(
            FloatField,
            "Temperature (K)",
            validators=[InputRequired()],
            default=300.0,
        )
        pressure_first_chamber = maybe_field(
            FloatField,
            "Pressure first chamber (Pa)",
            default=182.0,
            validators=[InputRequired()],
        )
        pressure_second_chamber = maybe_field(
            FloatField,
            "Pressure second chamber (Pa)",
            default=3.53,
            validators=[InputRequired()],
        )
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

    return InstrumentForm


BuiltInInstrumentForm = mk_instrument_form(hidden=True)
CustomInstrumentForm = mk_instrument_form(hidden=False)


class GasForm(Form):
    gas_molecule_radius = FloatField(default=2.46e-10, validators=[InputRequired()])
    gas_molecule_mass = FloatField(default=4.8506e-26, validators=[InputRequired()])
    adiabatic_index = FloatField(default=1.4, validators=[InputRequired()])


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


def get_chain_choices():
    from vms.app import CHAINS

    return [(chain, chain) for chain in CHAINS.keys()]


def get_chain_default():
    from vms.app import CHAINS

    return list(CHAINS.keys())[0]


class FragmentationPathwayForm(Form):
    chain = SelectField(
        "Fragmentation pathway",
        choices=get_chain_choices,
        default=get_chain_default,
        validators=[InputRequired()],
    )
    fragmentation_energy = FloatField()


class SettingsForm(QuartForm):
    voltage = FormField(VoltageForm)
    instrument = FormField(BuiltInInstrumentForm)
    pathways = FormField(FragmentationPathwayForm)
    gas = FormField(GasForm)
    simulation = FormField(SimulationForm)
