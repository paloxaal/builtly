"""
builtly_ifc_analyzer.py
───────────────────────
IFC-analyse for Builtly RIB, akustikk, brann og TEK17-moduler.

Leser IFC2x3 og IFC4 via ifcopenshell. Ekstraherer:
  - IfcWall / IfcWallStandardCase   → bærevegger, lette vegger
  - IfcColumn                       → søyler
  - IfcSlab                         → dekker
  - IfcBeam                         → bjelker
  - IfcSpace                        → rom (for akustikk, brann, tilgjengelighet)
  - IfcWindow                       → vinduer
  - IfcDoor                         → dører
  - IfcMaterialLayerSet             → U-verdi-beregning fra materiallag
  - IfcBuildingStorey               → etasjer

Output: Normalisert intern modell kompatibel med:
  - Konstruksjon.py sketch["elements"] format (0–1 normaliserte koordinater)
  - RIB-editorens JSON-format
  - Akustikk/brann/TEK17-modulenes romgeometri

Krever: pip install ifcopenshell --break-system-packages

Bruk:
    from builtly_ifc_analyzer import analyze_ifc
    result = analyze_ifc("/path/to/model.ifc")
    sketch_elements = result["sketch_elements"]   # for RIB-editor
    rooms = result["rooms"]                        # for akustikk/brann
    u_values = result["u_values"]                  # for energi/TEK17
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("builtly.ifc")

try:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element as ifc_util
    IFC_AVAILABLE = True
except ImportError:
    IFC_AVAILABLE = False
    logger.warning("ifcopenshell ikke tilgjengelig — pip install ifcopenshell")


# ─── Dataklasser ────────────────────────────────────────────────────

@dataclass
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

@dataclass
class BBox:
    min_pt: Vec3 = field(default_factory=Vec3)
    max_pt: Vec3 = field(default_factory=Vec3)

    @property
    def width(self) -> float: return abs(self.max_pt.x - self.min_pt.x)

    @property
    def depth(self) -> float: return abs(self.max_pt.y - self.min_pt.y)

    @property
    def height(self) -> float: return abs(self.max_pt.z - self.min_pt.z)

    @property
    def center(self) -> Vec3:
        return Vec3(
            (self.min_pt.x + self.max_pt.x) / 2,
            (self.min_pt.y + self.max_pt.y) / 2,
            (self.min_pt.z + self.max_pt.z) / 2,
        )

@dataclass
class MaterialLayer:
    name: str
    thickness_m: float
    conductivity: Optional[float] = None  # W/(m·K)

@dataclass
class IfcElement:
    """Normalisert bygningselement fra IFC."""
    ifc_id: int
    global_id: str
    type: str           # wall, column, slab, beam, window, door, space, core
    name: str
    storey: str
    bbox: BBox = field(default_factory=BBox)
    bearing: bool = False
    material_name: str = ""
    material_layers: List[MaterialLayer] = field(default_factory=list)
    u_value: Optional[float] = None         # W/(m²·K)
    area_m2: Optional[float] = None
    volume_m3: Optional[float] = None
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    is_external: bool = False
    space_type: str = ""                    # for IfcSpace: BADEROM, SOVEROM, etc.
    properties: Dict[str, Any] = field(default_factory=dict)


# ─── U-verdi-beregning ──────────────────────────────────────────────

# Typiske varmekonduktiviteter (W/(m·K)) for vanlige materialer
DEFAULT_CONDUCTIVITY = {
    "concrete": 1.65, "betong": 1.65, "beton": 1.65,
    "reinforced concrete": 2.30, "armert betong": 2.30,
    "brick": 0.77, "tegl": 0.77, "mur": 0.77,
    "timber": 0.13, "tre": 0.13, "wood": 0.13, "kl-tre": 0.13, "clt": 0.13,
    "steel": 50.0, "stål": 50.0,
    "mineral wool": 0.037, "mineralull": 0.037, "rockwool": 0.037, "glava": 0.037,
    "eps": 0.036, "xps": 0.034,
    "gypsum": 0.22, "gips": 0.22,
    "air": 0.025, "luft": 0.025,
    "glass": 1.0,
    "plywood": 0.13, "kryssfiner": 0.13,
    "osb": 0.13,
}

R_SI = 0.13   # Innvendig overgangsmostand (m²·K/W)
R_SE = 0.04   # Utvendig overgangsmotstand


def _guess_conductivity(material_name: str) -> Optional[float]:
    """Forsøk å gjette varmekonduktivitet fra materialnavn."""
    name_lower = material_name.lower().strip()
    for key, val in DEFAULT_CONDUCTIVITY.items():
        if key in name_lower:
            return val
    return None


def calculate_u_value(layers: List[MaterialLayer], is_external: bool = True) -> Optional[float]:
    """
    Beregn U-verdi fra materiallag (NS-EN ISO 6946).
    Returnerer None hvis ikke nok data.
    """
    if not layers:
        return None

    R_total = R_SI + R_SE if is_external else R_SI + R_SI

    for layer in layers:
        conductivity = layer.conductivity or _guess_conductivity(layer.name)
        if conductivity is None or conductivity <= 0 or layer.thickness_m <= 0:
            return None  # Kan ikke beregne uten alle lag
        R_total += layer.thickness_m / conductivity

    if R_total <= 0:
        return None

    return round(1.0 / R_total, 3)


# ─── IFC-parsing ────────────────────────────────────────────────────

def _get_placement(element) -> Vec3:
    """Hent plassering fra IfcLocalPlacement."""
    try:
        placement = element.ObjectPlacement
        if placement and hasattr(placement, "RelativePlacement"):
            rp = placement.RelativePlacement
            if hasattr(rp, "Location") and rp.Location:
                coords = rp.Location.Coordinates
                return Vec3(
                    float(coords[0]) / 1000.0,  # mm → m
                    float(coords[1]) / 1000.0,
                    float(coords[2]) / 1000.0 if len(coords) > 2 else 0.0,
                )
    except Exception:
        pass
    return Vec3()


def _get_bbox_from_geometry(element, settings=None) -> Optional[BBox]:
    """Beregn bounding box via ifcopenshell.geom."""
    try:
        if settings is None:
            settings = ifcopenshell.geom.settings()
            settings.set(settings.USE_WORLD_COORDS, True)

        shape = ifcopenshell.geom.create_shape(settings, element)
        verts = shape.geometry.verts
        if not verts:
            return None

        xs = verts[0::3]
        ys = verts[1::3]
        zs = verts[2::3]

        return BBox(
            min_pt=Vec3(min(xs), min(ys), min(zs)),
            max_pt=Vec3(max(xs), max(ys), max(zs)),
        )
    except Exception:
        return None


def _get_storey_name(element) -> str:
    """Finn etasjenavn via IfcRelContainedInSpatialStructure."""
    try:
        for rel in element.ContainedInStructure:
            structure = rel.RelatingStructure
            if structure.is_a("IfcBuildingStorey"):
                return structure.Name or f"Etasje {structure.Elevation:.0f}"
    except Exception:
        pass
    return "Ukjent etasje"


def _extract_material_layers(element) -> Tuple[str, List[MaterialLayer]]:
    """Ekstraher materiallag fra IfcMaterialLayerSet eller IfcMaterial."""
    layers = []
    material_name = ""

    try:
        # Prøv IfcMaterialLayerSetUsage / IfcMaterialLayerSet
        for rel in getattr(element, "HasAssociations", []):
            if rel.is_a("IfcRelAssociatesMaterial"):
                mat = rel.RelatingMaterial

                if mat.is_a("IfcMaterialLayerSetUsage"):
                    mat = mat.ForLayerSet

                if mat.is_a("IfcMaterialLayerSet"):
                    material_name = mat.LayerSetName or ""
                    for layer in mat.MaterialLayers:
                        lname = ""
                        conductivity = None
                        if layer.Material:
                            lname = layer.Material.Name or ""
                            conductivity = _guess_conductivity(lname)
                            # Sjekk IfcMaterialProperties for eksakt konduktivitet
                            for prop_set in getattr(layer.Material, "HasProperties", []):
                                if prop_set.is_a("IfcMaterialProperties"):
                                    for prop in prop_set.Properties:
                                        if hasattr(prop, "Name") and "thermal" in (prop.Name or "").lower():
                                            try:
                                                conductivity = float(prop.NominalValue.wrappedValue)
                                            except Exception:
                                                pass

                        thickness = float(layer.LayerThickness) / 1000.0  # mm → m
                        layers.append(MaterialLayer(
                            name=lname,
                            thickness_m=thickness,
                            conductivity=conductivity,
                        ))
                    break

                elif mat.is_a("IfcMaterial"):
                    material_name = mat.Name or ""
                    break

    except Exception as exc:
        logger.debug(f"Material-ekstraksjon feilet: {exc}")

    return material_name, layers


def _is_bearing_wall(element) -> bool:
    """Sjekk om vegg er bærende via Pset_WallCommon.LoadBearing."""
    try:
        # Sjekk property sets
        for rel in getattr(element, "IsDefinedBy", []):
            if rel.is_a("IfcRelDefinesByProperties"):
                pset = rel.RelatingPropertyDefinition
                if hasattr(pset, "HasProperties"):
                    for prop in pset.HasProperties:
                        if hasattr(prop, "Name") and prop.Name == "LoadBearing":
                            try:
                                return bool(prop.NominalValue.wrappedValue)
                            except Exception:
                                pass

        # Fallback: sjekk type-navn
        type_name = (element.Name or "").lower()
        if any(kw in type_name for kw in ["bærende", "bearing", "bære", "load"]):
            return True
        if element.is_a("IfcWallStandardCase"):
            # Standard case er ofte bærende, men ikke alltid
            pass

    except Exception:
        pass
    return False


def _is_external(element) -> bool:
    """Sjekk om element er utvendig via Pset_*Common.IsExternal."""
    try:
        for rel in getattr(element, "IsDefinedBy", []):
            if rel.is_a("IfcRelDefinesByProperties"):
                pset = rel.RelatingPropertyDefinition
                if hasattr(pset, "HasProperties"):
                    for prop in pset.HasProperties:
                        if hasattr(prop, "Name") and prop.Name == "IsExternal":
                            try:
                                return bool(prop.NominalValue.wrappedValue)
                            except Exception:
                                pass
    except Exception:
        pass
    return False


def _get_quantity(element, quantity_name: str) -> Optional[float]:
    """Hent kvantitet fra IfcElementQuantity."""
    try:
        for rel in getattr(element, "IsDefinedBy", []):
            if rel.is_a("IfcRelDefinesByProperties"):
                qset = rel.RelatingPropertyDefinition
                if qset.is_a("IfcElementQuantity"):
                    for q in qset.Quantities:
                        if hasattr(q, "Name") and q.Name == quantity_name:
                            for attr in ["AreaValue", "LengthValue", "VolumeValue", "WidthValue", "HeightValue"]:
                                val = getattr(q, attr, None)
                                if val is not None:
                                    return float(val)
    except Exception:
        pass
    return None


def _parse_space_type(space_name: str) -> str:
    """Gjett romtype fra IfcSpace.Name / LongName."""
    name = space_name.lower()
    mapping = {
        "bad": "bad", "wc": "bad", "toalett": "bad", "bathroom": "bad",
        "soverom": "soverom", "bedroom": "soverom", "sov": "soverom",
        "stue": "stue", "living": "stue", "opphold": "stue",
        "kjøkken": "kjoekken", "kitchen": "kjoekken",
        "gang": "gang", "corridor": "gang", "hall": "gang",
        "bod": "bod", "storage": "bod", "lager": "bod",
        "entre": "entre", "entrance": "entre",
        "vaskerom": "vaskerom", "laundry": "vaskerom",
        "trapp": "trapp", "stair": "trapp",
        "heis": "heis", "elevator": "heis",
        "teknisk": "teknisk", "technical": "teknisk",
        "kontor": "kontor", "office": "kontor",
    }
    for key, val in mapping.items():
        if key in name:
            return val
    return "annet"


# ─── Hovedfunksjon ──────────────────────────────────────────────────

def analyze_ifc(filepath: str, compute_geometry: bool = True) -> Dict[str, Any]:
    """
    Analyser IFC-fil og returner normalisert bygningsmodell.

    Args:
        filepath: Sti til .ifc-fil
        compute_geometry: Om bounding boxes skal beregnes via geometri-motor

    Returns:
        {
            "file_info": { schema, name, description, ... },
            "storeys": [ { name, elevation, elements_count } ],
            "elements": [ IfcElement as dict ],
            "rooms": [ { rom for akustikk/brann/TEK17 } ],
            "u_values": [ { element_name, u_value, layers } ],
            "sketch_elements": [ { Konstruksjon.py-format } ],
            "summary": { totals },
            "warnings": [ str ],
        }
    """
    if not IFC_AVAILABLE:
        return {"error": "ifcopenshell er ikke installert. Kjør: pip install ifcopenshell --break-system-packages"}

    filepath = str(filepath)
    if not os.path.exists(filepath):
        return {"error": f"Fil ikke funnet: {filepath}"}

    try:
        model = ifcopenshell.open(filepath)
    except Exception as exc:
        return {"error": f"Kunne ikke åpne IFC-fil: {exc}"}

    warnings: List[str] = []

    # ─── Filinfo ───
    header = model.wrapped_data.header
    file_info = {
        "schema": model.schema,
        "filename": os.path.basename(filepath),
        "description": str(getattr(header, "file_description", "")),
    }

    # ─── Geometry settings ───
    geom_settings = None
    if compute_geometry:
        try:
            geom_settings = ifcopenshell.geom.settings()
            geom_settings.set(geom_settings.USE_WORLD_COORDS, True)
        except Exception:
            warnings.append("Geometri-motor ikke tilgjengelig — bruker kun plasseringer.")
            geom_settings = None

    # ─── Etasjer ───
    storeys_data = []
    for storey in model.by_type("IfcBuildingStorey"):
        storeys_data.append({
            "name": storey.Name or f"Etasje {storey.Elevation:.0f}",
            "elevation": float(storey.Elevation or 0),
            "global_id": storey.GlobalId,
        })
    storeys_data.sort(key=lambda s: s["elevation"])

    # ─── Parse elementer ───
    parsed_elements: List[IfcElement] = []

    # Vegger
    for wall in model.by_type("IfcWall"):
        material_name, layers = _extract_material_layers(wall)
        bearing = _is_bearing_wall(wall)
        external = _is_external(wall)
        u_val = calculate_u_value(layers, external) if layers else None

        el = IfcElement(
            ifc_id=wall.id(),
            global_id=wall.GlobalId,
            type="wall",
            name=wall.Name or "Vegg",
            storey=_get_storey_name(wall),
            bearing=bearing,
            material_name=material_name,
            material_layers=layers,
            u_value=u_val,
            is_external=external,
            length_m=_get_quantity(wall, "Length"),
            height_m=_get_quantity(wall, "Height"),
            width_m=_get_quantity(wall, "Width"),
            area_m2=_get_quantity(wall, "NetSideArea") or _get_quantity(wall, "GrossSideArea"),
        )

        bbox = _get_bbox_from_geometry(wall, geom_settings) if geom_settings else None
        if bbox:
            el.bbox = bbox
            if el.length_m is None:
                el.length_m = max(bbox.width, bbox.depth)
            if el.height_m is None:
                el.height_m = bbox.height
        else:
            placement = _get_placement(wall)
            el.bbox = BBox(min_pt=placement, max_pt=placement)

        parsed_elements.append(el)

    # Søyler
    for col in model.by_type("IfcColumn"):
        material_name, layers = _extract_material_layers(col)
        el = IfcElement(
            ifc_id=col.id(),
            global_id=col.GlobalId,
            type="column",
            name=col.Name or "Søyle",
            storey=_get_storey_name(col),
            material_name=material_name,
            height_m=_get_quantity(col, "Length") or _get_quantity(col, "Height"),
        )
        bbox = _get_bbox_from_geometry(col, geom_settings) if geom_settings else None
        if bbox:
            el.bbox = bbox
        else:
            placement = _get_placement(col)
            el.bbox = BBox(min_pt=placement, max_pt=placement)
        parsed_elements.append(el)

    # Bjelker
    for beam in model.by_type("IfcBeam"):
        material_name, _ = _extract_material_layers(beam)
        el = IfcElement(
            ifc_id=beam.id(),
            global_id=beam.GlobalId,
            type="beam",
            name=beam.Name or "Bjelke",
            storey=_get_storey_name(beam),
            material_name=material_name,
            length_m=_get_quantity(beam, "Length"),
        )
        bbox = _get_bbox_from_geometry(beam, geom_settings) if geom_settings else None
        if bbox:
            el.bbox = bbox
        else:
            placement = _get_placement(beam)
            el.bbox = BBox(min_pt=placement, max_pt=placement)
        parsed_elements.append(el)

    # Dekker
    for slab in model.by_type("IfcSlab"):
        material_name, layers = _extract_material_layers(slab)
        el = IfcElement(
            ifc_id=slab.id(),
            global_id=slab.GlobalId,
            type="slab",
            name=slab.Name or "Dekke",
            storey=_get_storey_name(slab),
            material_name=material_name,
            material_layers=layers,
            area_m2=_get_quantity(slab, "GrossArea") or _get_quantity(slab, "NetArea"),
        )
        bbox = _get_bbox_from_geometry(slab, geom_settings) if geom_settings else None
        if bbox:
            el.bbox = bbox
        parsed_elements.append(el)

    # Rom (IfcSpace)
    rooms = []
    for space in model.by_type("IfcSpace"):
        el = IfcElement(
            ifc_id=space.id(),
            global_id=space.GlobalId,
            type="space",
            name=space.LongName or space.Name or "Rom",
            storey=_get_storey_name(space),
            area_m2=_get_quantity(space, "GrossFloorArea") or _get_quantity(space, "NetFloorArea"),
            volume_m3=_get_quantity(space, "GrossVolume") or _get_quantity(space, "NetVolume"),
            height_m=_get_quantity(space, "Height") or _get_quantity(space, "FinishCeilingHeight"),
        )
        el.space_type = _parse_space_type(el.name)

        bbox = _get_bbox_from_geometry(space, geom_settings) if geom_settings else None
        if bbox:
            el.bbox = bbox
            if el.area_m2 is None:
                el.area_m2 = round(bbox.width * bbox.depth, 1)

        parsed_elements.append(el)

        rooms.append({
            "name": el.name,
            "type": el.space_type,
            "storey": el.storey,
            "area_m2": el.area_m2,
            "volume_m3": el.volume_m3,
            "height_m": el.height_m,
            "width_m": bbox.width if bbox else None,
            "depth_m": bbox.depth if bbox else None,
            "global_id": el.global_id,
        })

    # Vinduer
    for window in model.by_type("IfcWindow"):
        el = IfcElement(
            ifc_id=window.id(),
            global_id=window.GlobalId,
            type="window",
            name=window.Name or "Vindu",
            storey=_get_storey_name(window),
            width_m=float(window.OverallWidth or 0) / 1000.0 if window.OverallWidth else None,
            height_m=float(window.OverallHeight or 0) / 1000.0 if window.OverallHeight else None,
        )
        bbox = _get_bbox_from_geometry(window, geom_settings) if geom_settings else None
        if bbox:
            el.bbox = bbox
        else:
            placement = _get_placement(window)
            el.bbox = BBox(min_pt=placement, max_pt=placement)
        parsed_elements.append(el)

    # Dører
    for door in model.by_type("IfcDoor"):
        el = IfcElement(
            ifc_id=door.id(),
            global_id=door.GlobalId,
            type="door",
            name=door.Name or "Dør",
            storey=_get_storey_name(door),
            width_m=float(door.OverallWidth or 0) / 1000.0 if door.OverallWidth else None,
            height_m=float(door.OverallHeight or 0) / 1000.0 if door.OverallHeight else None,
        )
        placement = _get_placement(door)
        el.bbox = BBox(min_pt=placement, max_pt=placement)
        parsed_elements.append(el)

    # ─── Beregn global bounding box for normalisering ───
    all_min_x = min((e.bbox.min_pt.x for e in parsed_elements if e.bbox), default=0)
    all_max_x = max((e.bbox.max_pt.x for e in parsed_elements if e.bbox), default=1)
    all_min_y = min((e.bbox.min_pt.y for e in parsed_elements if e.bbox), default=0)
    all_max_y = max((e.bbox.max_pt.y for e in parsed_elements if e.bbox), default=1)
    range_x = max(all_max_x - all_min_x, 0.001)
    range_y = max(all_max_y - all_min_y, 0.001)

    def norm_x(val: float) -> float:
        return round(max(0, min(1, (val - all_min_x) / range_x)), 6)

    def norm_y(val: float) -> float:
        return round(max(0, min(1, (val - all_min_y) / range_y)), 6)

    # ─── Konverter til sketch_elements (Konstruksjon.py-format) ───
    sketch_elements = []
    col_counter = 0

    for el in parsed_elements:
        if el.type == "wall":
            # Vegg som linje fra min til max
            x1 = norm_x(el.bbox.min_pt.x)
            y1 = norm_y(el.bbox.min_pt.y)
            x2 = norm_x(el.bbox.max_pt.x)
            y2 = norm_y(el.bbox.max_pt.y)

            # Bestem orientering — bruk den lengste aksen
            dx = abs(el.bbox.max_pt.x - el.bbox.min_pt.x)
            dy = abs(el.bbox.max_pt.y - el.bbox.min_pt.y)
            if dy > dx:
                # Vertikal vegg
                mid_x = (x1 + x2) / 2
                sketch_elements.append({
                    "type": "wall",
                    "x1": mid_x, "y1": y1,
                    "x2": mid_x, "y2": y2,
                    "label": el.name,
                    "bearing": el.bearing,
                    "material": el.material_name,
                    "ifc_global_id": el.global_id,
                })
            else:
                # Horisontal vegg
                mid_y = (y1 + y2) / 2
                sketch_elements.append({
                    "type": "wall",
                    "x1": x1, "y1": mid_y,
                    "x2": x2, "y2": mid_y,
                    "label": el.name,
                    "bearing": el.bearing,
                    "material": el.material_name,
                    "ifc_global_id": el.global_id,
                })

        elif el.type == "column":
            col_counter += 1
            sketch_elements.append({
                "type": "column",
                "x": norm_x(el.bbox.center.x),
                "y": norm_y(el.bbox.center.y),
                "label": f"C{col_counter}",
                "material": el.material_name,
                "ifc_global_id": el.global_id,
            })

        elif el.type == "beam":
            sketch_elements.append({
                "type": "beam",
                "x1": norm_x(el.bbox.min_pt.x), "y1": norm_y(el.bbox.min_pt.y),
                "x2": norm_x(el.bbox.max_pt.x), "y2": norm_y(el.bbox.max_pt.y),
                "label": el.name,
                "material": el.material_name,
                "ifc_global_id": el.global_id,
            })

    # ─── U-verdier ───
    u_values = []
    for el in parsed_elements:
        if el.u_value is not None:
            u_values.append({
                "element_name": el.name,
                "type": el.type,
                "u_value": el.u_value,
                "is_external": el.is_external,
                "layers": [asdict(l) for l in el.material_layers],
                "global_id": el.global_id,
            })

    # ─── Tilgjengelighetskrav (fra romgeometri) ───
    accessibility_checks = []
    for room in rooms:
        issues = []
        if room["type"] == "bad" and room.get("area_m2") and room["area_m2"] < 3.3:
            issues.append(f"Bad {room['name']}: {room['area_m2']:.1f} m² < 3.3 m² (TEK17 §12-9)")
        if room["type"] == "soverom" and room.get("area_m2") and room["area_m2"] < 5.7:
            issues.append(f"Soverom {room['name']}: {room['area_m2']:.1f} m² < 5.7 m² (TEK17 §12-7)")
        min_dim = min(room.get("width_m") or 999, room.get("depth_m") or 999)
        if min_dim < 1.5 and room["type"] in ("bad", "soverom", "stue", "kjoekken"):
            issues.append(f"{room['name']}: bredde {min_dim:.1f}m < 1.5m (tilgjengelighet)")
        if issues:
            accessibility_checks.append({"room": room["name"], "issues": issues})

    # ─── Sammendrag ───
    summary = {
        "schema": model.schema,
        "storeys": len(storeys_data),
        "total_elements": len(parsed_elements),
        "walls": sum(1 for e in parsed_elements if e.type == "wall"),
        "bearing_walls": sum(1 for e in parsed_elements if e.type == "wall" and e.bearing),
        "columns": sum(1 for e in parsed_elements if e.type == "column"),
        "beams": sum(1 for e in parsed_elements if e.type == "beam"),
        "slabs": sum(1 for e in parsed_elements if e.type == "slab"),
        "rooms": len(rooms),
        "windows": sum(1 for e in parsed_elements if e.type == "window"),
        "doors": sum(1 for e in parsed_elements if e.type == "door"),
        "u_values_computed": len(u_values),
        "accessibility_issues": sum(len(c["issues"]) for c in accessibility_checks),
        "model_extents_m": {"x": round(range_x, 1), "y": round(range_y, 1)},
    }

    return {
        "file_info": file_info,
        "storeys": storeys_data,
        "elements": [asdict(e) for e in parsed_elements],
        "rooms": rooms,
        "u_values": u_values,
        "sketch_elements": sketch_elements,
        "accessibility_checks": accessibility_checks,
        "summary": summary,
        "warnings": warnings,
    }


# ─── Hjelpefunksjon for Streamlit-integrasjon ───────────────────────

def ifc_to_rib_sketch(filepath: str) -> Dict[str, Any]:
    """
    Snarvei: Parse IFC-fil og returner sketch-dict direkte kompatibelt
    med Konstruksjon.py's draft_sketches format.

    Bruk:
        sketch = ifc_to_rib_sketch("model.ifc")
        st.session_state.rib_draft_sketches = [sketch]
    """
    result = analyze_ifc(filepath)
    if "error" in result:
        return {"elements": [], "error": result["error"]}

    return {
        "sketch_name": f"IFC: {result['file_info']['filename']}",
        "elements": result["sketch_elements"],
        "ifc_summary": result["summary"],
        "plan_bbox": [0.0, 0.0, 1.0, 1.0],
        "notes": [
            f"Importert fra {result['file_info']['filename']} ({result['file_info']['schema']})",
            f"{result['summary']['bearing_walls']} bærevegger, {result['summary']['columns']} søyler, {result['summary']['beams']} bjelker",
        ],
    }
