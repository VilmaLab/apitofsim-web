from math import pi

from apitofsim import CONFIG_FIELDS


def recorded_settings(config):
    """Map the settings older result databases can recover to their form fields."""
    lengths = dict(
        zip(CONFIG_FIELDS["lengths"].components, config["lengths"], strict=True)
    )
    pressures = dict(
        zip(CONFIG_FIELDS["pressures"].components, config["pressures"], strict=True)
    )
    gas = config["gas"]
    quadrupole = config["quadrupole"]
    return {
        "gas": {
            "gas_molecule_radius": gas.radius,
            "gas_molecule_mass": gas.mass,
            "adiabatic_index": gas.adiabatic_index,
            "temperature_": config["T"],
            "pressure_first_chamber": pressures["first"],
            "pressure_second_chamber": pressures["second"],
        },
        "instrument": {
            "instrument_geometry": {
                "length_of_first_chamber": lengths["L0"],
                "length_of_skimmer": lengths["Lsk"],
                "length_between_skimmer_and_front_quadrupole": lengths["L1"],
                "length_between_front_quadrupole_and_back_quadrupole": lengths["L2"],
                "length_between_back_quadrupole_and_2nd_skimmer": lengths["L3"],
            },
            "skimmer_geometry": {
                "radius_at_smallest_cross_section_skimmer": config["dc"],
                "angle_of_skimmer": config["alpha_factor"].to("radian").magnitude / pi,
            },
            "quadrupole": {
                "dc_quadrupole": quadrupole.dc_field,
                "ac_quadrupole": quadrupole.ac_field,
                "radiofrequency_quadrupole": quadrupole.radiofrequency,
                "half_distance_between_quadrupole_rods": quadrupole.r_quadrupole,
            },
        },
        "voltage": {
            f"voltage{i}": voltage.magnitude
            for i, voltage in enumerate(config["voltages"].to("V"), 1)
        },
        "simulation": {
            "realizations": config["N"],
            "skimmer": {
                "iterations_eq1": config["N_iter"],
                "iterations_eq2": config["M_iter"],
                "solved_points": config["resolution"],
                "tolerance": config["tolerance"],
            },
        },
    }
