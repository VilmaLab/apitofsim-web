import base64
import os
import warnings
from io import StringIO, UnsupportedOperation

from ase.io import write as ase_write
from molify import ase2rdkit
from rdkit import Chem as rdchem
from rdkit.Chem import Draw as rddraw


def ase_write_string(atoms, format):
    f = StringIO()
    try:
        ase_write(f, atoms, format=format)
    except UnsupportedOperation:
        if not hasattr(os, "memfd_create"):
            warnings.warn(
                f"Could not convert to {format}. StringIO failed and memfd_create not supported."
            )
            return None
        fd = os.memfd_create("ase_convert_" + format)
        f = os.fdopen(fd, "w+")
        ase_write(f, atoms, format=format)
        f.seek(0)
        return f.read()
    else:
        return f.getvalue()


def rddraw_html(rdkit_atoms, image_type="png", **kwargs):
    if image_type not in ("png", "svg", "acs1996svg"):
        raise ValueError(f"Unknown image type {image_type}")
    drawfn = None
    if image_type == "png":
        drawfn = rddraw._moltoimg
        kwargs["returnPNG"] = True
    elif image_type == "svg":
        drawfn = rddraw._moltoSVG
    if image_type == "acs1996svg":
        img = rddraw.MolToACS1996SVG(rdkit_atoms, **kwargs)
    else:
        assert drawfn is not None
        kwargs.setdefault("sz", kwargs.get("size", (300, 300)))
        kwargs.setdefault("highlights", kwargs.get("highlightBonds", []))
        kwargs.setdefault("legend", "")
        kwargs.setdefault("kekulize", True)
        kwargs.setdefault("wedgeBonds", True)
        img = drawfn(rdkit_atoms, **kwargs, options={"bgColor": (1, 1, 1, 0)})
    if image_type == "png":
        encoded = base64.b64encode(img).decode("utf-8")
        return f'<img src="data:image/png;base64, {encoded}">'
    else:
        return img


def enrich_cluster(ase_db, cluster):
    cluster["has_ase"] = cluster["ase_mol_id"] is not None
    if not cluster["has_ase"]:
        return
    atoms = ase_db.get_atoms(cluster["ase_mol_id"])
    cluster["formula"] = atoms.get_chemical_formula()
    cluster["symbols"] = str(atoms.symbols)
    cluster["ase_xyz"] = ase_write_string(atoms, "xyz")
    cluster["ase_cube"] = ase_write_string(atoms, "cube")
    try:
        rdkit_atoms = ase2rdkit(atoms)
    except ValueError as e:
        cluster["conversion_error"] = e.args[0]
        cluster["has_rdkit"] = False
    else:
        cluster["has_rdkit"] = True
        cluster["smiles"] = rdchem.MolToSmiles(rdkit_atoms)
        cluster["rd_molblock"] = rdchem.MolToMolBlock(rdkit_atoms)
        cluster["rd_png"] = rddraw_html(rdkit_atoms, size=(400, 400))
        cluster["rd_svg"] = rddraw_html(rdkit_atoms, size=(400, 400), image_type="svg")
        try:
            cluster["rd_svgacs"] = rddraw_html(rdkit_atoms, image_type="acs1996svg")
        except ValueError:
            # The ACS style needs a mean bond length, so it cannot draw
            # atom-like products
            pass
