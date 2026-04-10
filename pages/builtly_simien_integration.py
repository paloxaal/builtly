"""
builtly_simien_integration.py
─────────────────────────────
SIMIEN energiberegning-integrasjon for Builtly.

Genererer SIMIEN input-filer (.smi / XML) fra IFC-modelldata
og parser SIMIEN resultatfiler (.smo) tilbake.

Funksjoner:
  1. Generer SIMIEN-input fra IFC-analyse (U-verdier, vinduer, soner)
  2. Generer SIMIEN-input fra manuell input
  3. Parse SIMIEN-resultatfil (.smo)
  4. TEK17 §14 energikrav-verifisering

SIMIEN fil-format: Egenutviklet XML (.smi for input, .smo for output)
SIMIEN versjon: Kompatibel med SIMIEN 6.x og nyere

Bruk:
    from builtly_simien_integration import generate_simien_input, parse_simien_results

    # Fra IFC-analyse:
    from builtly_ifc_analyzer import analyze_ifc
    ifc = analyze_ifc("model.ifc")
    
    smi_path = generate_simien_input(
        ifc_data=ifc,
        building_type="bolig",
        location="Trondheim",
        output_path="prosjekt.smi",
    )
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree.ElementTree import Element, SubElement, tostring, parse as parse_xml
from xml.dom import minidom

logger = logging.getLogger("builtly.simien")


# ─── Klimadata (NS-EN ISO 15927 / SIMIEN klimafiler) ───────────────

CLIMATE_DATA = {
    "oslo": {
        "station": "Oslo/Blindern",
        "lat": 59.94, "lon": 10.72,
        "heating_degree_days": 3752,
        "design_temp_winter": -20.0,
        "design_temp_summer": 30.0,
        "snow_load_kN_m2": 3.0,
        "wind_zone": "Sone 1",
        "simien_climate_file": "Oslo_Blindern.kli",
    },
    "trondheim": {
        "station": "Trondheim/Voll",
        "lat": 63.41, "lon": 10.45,
        "heating_degree_days": 4520,
        "design_temp_winter": -22.0,
        "design_temp_summer": 28.0,
        "snow_load_kN_m2": 3.5,
        "wind_zone": "Sone 2",
        "simien_climate_file": "Trondheim_Voll.kli",
    },
    "bergen": {
        "station": "Bergen/Florida",
        "lat": 60.39, "lon": 5.33,
        "heating_degree_days": 3280,
        "design_temp_winter": -12.0,
        "design_temp_summer": 28.0,
        "snow_load_kN_m2": 2.0,
        "wind_zone": "Sone 3",
        "simien_climate_file": "Bergen_Florida.kli",
    },
    "tromsø": {
        "station": "Tromsø",
        "lat": 69.65, "lon": 18.96,
        "heating_degree_days": 5200,
        "design_temp_winter": -18.0,
        "design_temp_summer": 25.0,
        "snow_load_kN_m2": 4.5,
        "wind_zone": "Sone 3",
        "simien_climate_file": "Tromso.kli",
    },
    "stavanger": {
        "station": "Stavanger/Sola",
        "lat": 58.88, "lon": 5.64,
        "heating_degree_days": 3100,
        "design_temp_winter": -15.0,
        "design_temp_summer": 28.0,
        "snow_load_kN_m2": 2.5,
        "wind_zone": "Sone 3",
        "simien_climate_file": "Stavanger_Sola.kli",
    },
    "kristiansand": {
        "station": "Kristiansand/Kjevik",
        "lat": 58.20, "lon": 8.08,
        "heating_degree_days": 3400,
        "design_temp_winter": -18.0,
        "design_temp_summer": 29.0,
        "snow_load_kN_m2": 3.0,
        "wind_zone": "Sone 1",
        "simien_climate_file": "Kristiansand_Kjevik.kli",
    },
    "bodø": {
        "station": "Bodø",
        "lat": 67.27, "lon": 14.40,
        "heating_degree_days": 4700,
        "design_temp_winter": -15.0,
        "design_temp_summer": 25.0,
        "snow_load_kN_m2": 3.5,
        "wind_zone": "Sone 4",
        "simien_climate_file": "Bodo.kli",
    },
}


# ─── TEK17 §14 energikrav ──────────────────────────────────────────

TEK17_ENERGY_REQUIREMENTS = {
    # Minstekrav til enkeltkomponenter (§14-3)
    "component_limits": {
        "u_wall": 0.18,          # W/(m²·K) yttervegg
        "u_roof": 0.13,          # W/(m²·K) tak
        "u_floor": 0.10,         # W/(m²·K) gulv mot grunn
        "u_window_door": 0.80,   # W/(m²·K) vindu/dør
        "u_glass_facade": 0.80,  # W/(m²·K) glassfelt
        "normalized_cold_bridge": 0.03,  # W/(m²·K) normalisert kuldebroverdi
        "air_leakage_50Pa": 0.6, # 1/h (småhus), 0.6 for leiligheter
        "air_leakage_50Pa_other": 1.5,  # andre bygg
        "sfp_ventilation": 1.5,  # kW/(m³/s) vifteffekt
        "heat_recovery_eff": 0.80,  # temperaturvirkningsgrad gjenvinner
    },
    # Energirammer (§14-2) kWh/(m²·år)
    "energy_frames": {
        "småhus": 100,
        "boligblokk": 95,
        "barnehage": 135,
        "kontorbygg": 115,
        "skolebygg": 110,
        "universitetbygg": 145,
        "sykehus": 225,
        "sykehjem": 195,
        "hotell": 170,
        "idrettsbygg": 165,
        "forretningsbygg": 180,
        "kulturbygg": 130,
        "lett_industri": 140,
    },
}


# ─── Dataklasser ────────────────────────────────────────────────────

@dataclass
class SimienZone:
    """En termisk sone i SIMIEN-modellen."""
    name: str
    floor_area_m2: float
    volume_m3: float
    heated: bool = True
    setpoint_heat: float = 21.0    # °C
    setpoint_cool: float = 26.0    # °C
    persons_per_m2: float = 0.0
    internal_gains_W_m2: float = 5.0
    lighting_W_m2: float = 8.0
    ventilation_m3_h_m2: float = 1.2   # m³/(h·m²)
    usage_hours: float = 16.0     # timer/døgn


@dataclass
class SimienConstruction:
    """En bygningsdel i SIMIEN-modellen."""
    name: str
    type: str           # wall, roof, floor, window, door
    area_m2: float
    u_value: float      # W/(m²·K)
    orientation: str    # N, NE, E, SE, S, SW, W, NW, horizontal
    zone_name: str = ""
    is_external: bool = True
    g_value: float = 0.0    # Solenergi-transmisjon (vinduer)
    frame_fraction: float = 0.2  # Karmandel (vinduer)


@dataclass
class SimienModel:
    """Komplett SIMIEN-modell."""
    project_name: str
    building_type: str
    location: str
    climate_data: Dict[str, Any] = field(default_factory=dict)
    zones: List[SimienZone] = field(default_factory=list)
    constructions: List[SimienConstruction] = field(default_factory=list)
    air_leakage_50Pa: float = 0.6
    heat_recovery_efficiency: float = 0.80
    sfp_ventilation: float = 1.5
    cold_bridge_normalized: float = 0.06
    heating_system: str = "varmepumpe"   # varmepumpe, fjernvarme, elektrisk, bio
    heating_cop: float = 3.0


# ─── IFC → SIMIEN konvertering ──────────────────────────────────────

def _guess_orientation(bbox_center_x: float, bbox_center_y: float, building_center_x: float, building_center_y: float) -> str:
    """Gjett orientering basert på posisjon relativt til bygningens senter."""
    dx = bbox_center_x - building_center_x
    dy = bbox_center_y - building_center_y
    if abs(dx) < 0.01 and abs(dy) < 0.01:
        return "horizontal"
    angle = math.degrees(math.atan2(dy, dx))
    if angle < 0:
        angle += 360
    directions = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
    idx = int((angle + 22.5) / 45) % 8
    return directions[idx]


def ifc_to_simien_model(
    ifc_data: Dict[str, Any],
    building_type: str = "boligblokk",
    location: str = "trondheim",
    project_name: str = "Builtly prosjekt",
) -> SimienModel:
    """
    Konverter IFC-analysedata til SIMIEN-modell.
    
    Args:
        ifc_data: Output fra builtly_ifc_analyzer.analyze_ifc()
        building_type: Bygningskategori for energiramme
        location: By for klimadata
    """
    climate = CLIMATE_DATA.get(location.lower(), CLIMATE_DATA["oslo"])

    model = SimienModel(
        project_name=project_name,
        building_type=building_type,
        location=location,
        climate_data=climate,
    )

    # ── Soner fra IfcSpace ──
    rooms = ifc_data.get("rooms", [])
    if rooms:
        # Grupper rom per etasje som sone
        storeys: Dict[str, List[Dict]] = {}
        for room in rooms:
            storey = room.get("storey", "Ukjent")
            storeys.setdefault(storey, []).append(room)

        for storey_name, storey_rooms in storeys.items():
            total_area = sum(r.get("area_m2", 0) or 0 for r in storey_rooms)
            avg_height = sum(r.get("height_m", 2.5) or 2.5 for r in storey_rooms) / max(len(storey_rooms), 1)
            total_volume = total_area * avg_height

            if total_area > 0:
                model.zones.append(SimienZone(
                    name=storey_name,
                    floor_area_m2=round(total_area, 1),
                    volume_m3=round(total_volume, 1),
                    heated=True,
                ))
    else:
        # Fallback: estimer fra bygningens bounding box
        extents = ifc_data.get("summary", {}).get("model_extents_m", {})
        est_area = extents.get("x", 20) * extents.get("y", 15)
        model.zones.append(SimienZone(
            name="Estimert sone",
            floor_area_m2=round(est_area, 0),
            volume_m3=round(est_area * 2.5, 0),
        ))

    # ── Bygningsdeler fra IFC-elementer ──
    elements = ifc_data.get("elements", [])

    # Beregn bygningens senter for orientering
    all_x = [e["bbox"]["min_pt"]["x"] for e in elements if "bbox" in e]
    all_y = [e["bbox"]["min_pt"]["y"] for e in elements if "bbox" in e]
    center_x = sum(all_x) / max(len(all_x), 1) if all_x else 0
    center_y = sum(all_y) / max(len(all_y), 1) if all_y else 0

    for el in elements:
        el_type = el.get("type", "")
        is_ext = el.get("is_external", False)
        u_val = el.get("u_value")
        bbox = el.get("bbox", {})

        if el_type == "wall" and is_ext and u_val:
            area = el.get("area_m2") or 0
            if area <= 0:
                # Estimer fra bbox
                length = el.get("length_m", 0) or 0
                height = el.get("height_m", 2.8) or 2.8
                area = length * height

            if area > 0:
                cx = (bbox.get("min_pt", {}).get("x", 0) + bbox.get("max_pt", {}).get("x", 0)) / 2
                cy = (bbox.get("min_pt", {}).get("y", 0) + bbox.get("max_pt", {}).get("y", 0)) / 2
                orientation = _guess_orientation(cx, cy, center_x, center_y)

                model.constructions.append(SimienConstruction(
                    name=el.get("name", "Yttervegg"),
                    type="wall",
                    area_m2=round(area, 1),
                    u_value=u_val,
                    orientation=orientation,
                ))

        elif el_type == "window":
            w = el.get("width_m", 1.2) or 1.2
            h = el.get("height_m", 1.5) or 1.5
            area = w * h
            cx = bbox.get("min_pt", {}).get("x", 0)
            cy = bbox.get("min_pt", {}).get("y", 0)
            orientation = _guess_orientation(cx, cy, center_x, center_y)

            model.constructions.append(SimienConstruction(
                name=el.get("name", "Vindu"),
                type="window",
                area_m2=round(area, 2),
                u_value=el.get("u_value", 0.80),
                orientation=orientation,
                g_value=0.50,
                frame_fraction=0.20,
            ))

        elif el_type == "slab":
            area = el.get("area_m2", 0) or 0
            u_val = el.get("u_value")
            if area > 0 and u_val:
                # Sjekk om det er tak eller gulv basert på z-posisjon
                z = bbox.get("min_pt", {}).get("z", 0)
                slab_type = "roof" if z > 3.0 else "floor"
                model.constructions.append(SimienConstruction(
                    name=el.get("name", "Dekke"),
                    type=slab_type,
                    area_m2=round(area, 1),
                    u_value=u_val,
                    orientation="horizontal",
                ))

    # ── U-verdier fra separat analyse ──
    for uv in ifc_data.get("u_values", []):
        # Sjekk om allerede lagt til via elements
        existing = any(
            c.name == uv.get("element_name", "") and abs(c.u_value - uv.get("u_value", 0)) < 0.001
            for c in model.constructions
        )
        if not existing and uv.get("is_external"):
            el_type = uv.get("type", "wall")
            simien_type = "wall"
            if "roof" in el_type or "tak" in uv.get("element_name", "").lower():
                simien_type = "roof"
            elif "floor" in el_type or "gulv" in uv.get("element_name", "").lower():
                simien_type = "floor"

            model.constructions.append(SimienConstruction(
                name=uv.get("element_name", "Bygningsdel"),
                type=simien_type,
                area_m2=10.0,  # Placeholder — trenger areal fra modell
                u_value=uv["u_value"],
                orientation="N",
            ))

    return model


# ─── SIMIEN XML-generering ─────────────────────────────────────────

def _prettify_xml(elem: Element) -> str:
    """Formatter XML med innrykk."""
    rough = tostring(elem, encoding="unicode")
    return minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


def generate_simien_xml(model: SimienModel) -> str:
    """Generer SIMIEN-kompatibel XML fra modell."""
    root = Element("SimienProject")
    root.set("version", "6.0")
    root.set("generator", "Builtly")

    # Prosjektinfo
    project = SubElement(root, "ProjectInfo")
    SubElement(project, "Name").text = model.project_name
    SubElement(project, "BuildingType").text = model.building_type
    SubElement(project, "Location").text = model.location
    SubElement(project, "Standard").text = "TEK17"

    # Klimadata
    climate = SubElement(root, "ClimateData")
    cd = model.climate_data
    SubElement(climate, "Station").text = cd.get("station", "")
    SubElement(climate, "Latitude").text = str(cd.get("lat", 0))
    SubElement(climate, "Longitude").text = str(cd.get("lon", 0))
    SubElement(climate, "HeatingDegreeDays").text = str(cd.get("heating_degree_days", 0))
    SubElement(climate, "DesignTempWinter").text = str(cd.get("design_temp_winter", -20))
    SubElement(climate, "DesignTempSummer").text = str(cd.get("design_temp_summer", 30))
    SubElement(climate, "ClimateFile").text = cd.get("simien_climate_file", "")

    # Bygningskropp
    envelope = SubElement(root, "BuildingEnvelope")
    SubElement(envelope, "AirLeakage50Pa").text = str(model.air_leakage_50Pa)
    SubElement(envelope, "ColdBridgeNormalized").text = str(model.cold_bridge_normalized)

    # Soner
    zones_el = SubElement(root, "Zones")
    total_area = 0
    total_volume = 0
    for zone in model.zones:
        z = SubElement(zones_el, "Zone")
        SubElement(z, "Name").text = zone.name
        SubElement(z, "FloorArea").text = str(zone.floor_area_m2)
        SubElement(z, "Volume").text = str(zone.volume_m3)
        SubElement(z, "Heated").text = str(zone.heated).lower()
        SubElement(z, "SetpointHeating").text = str(zone.setpoint_heat)
        SubElement(z, "SetpointCooling").text = str(zone.setpoint_cool)
        SubElement(z, "PersonsPerM2").text = str(zone.persons_per_m2)
        SubElement(z, "InternalGains").text = str(zone.internal_gains_W_m2)
        SubElement(z, "Lighting").text = str(zone.lighting_W_m2)
        SubElement(z, "VentilationRate").text = str(zone.ventilation_m3_h_m2)
        SubElement(z, "UsageHours").text = str(zone.usage_hours)
        total_area += zone.floor_area_m2
        total_volume += zone.volume_m3

    # Bygningsdeler
    constructions_el = SubElement(root, "Constructions")
    for c in model.constructions:
        ce = SubElement(constructions_el, "Construction")
        SubElement(ce, "Name").text = c.name
        SubElement(ce, "Type").text = c.type
        SubElement(ce, "Area").text = str(c.area_m2)
        SubElement(ce, "UValue").text = str(c.u_value)
        SubElement(ce, "Orientation").text = c.orientation
        SubElement(ce, "IsExternal").text = str(c.is_external).lower()
        if c.type == "window":
            SubElement(ce, "GValue").text = str(c.g_value)
            SubElement(ce, "FrameFraction").text = str(c.frame_fraction)

    # Ventilasjon
    vent = SubElement(root, "Ventilation")
    SubElement(vent, "SFP").text = str(model.sfp_ventilation)
    SubElement(vent, "HeatRecoveryEfficiency").text = str(model.heat_recovery_efficiency)

    # Oppvarming
    heating = SubElement(root, "HeatingSystem")
    SubElement(heating, "Type").text = model.heating_system
    SubElement(heating, "COP").text = str(model.heating_cop)

    # Sammendrag
    summary = SubElement(root, "Summary")
    SubElement(summary, "TotalFloorArea").text = str(round(total_area, 1))
    SubElement(summary, "TotalVolume").text = str(round(total_volume, 1))
    SubElement(summary, "NumberOfZones").text = str(len(model.zones))
    SubElement(summary, "NumberOfConstructions").text = str(len(model.constructions))

    # TEK17-grenseverdier
    tek = SubElement(root, "TEK17")
    frame = TEK17_ENERGY_REQUIREMENTS["energy_frames"].get(model.building_type.lower(), 100)
    SubElement(tek, "EnergyFrame").text = str(frame)
    limits = TEK17_ENERGY_REQUIREMENTS["component_limits"]
    for key, val in limits.items():
        SubElement(tek, key).text = str(val)

    return _prettify_xml(root)


def generate_simien_input(
    ifc_data: Optional[Dict[str, Any]] = None,
    building_type: str = "boligblokk",
    location: str = "trondheim",
    project_name: str = "Builtly prosjekt",
    output_path: str = "prosjekt.smi",
    **manual_overrides,
) -> str:
    """
    Generer SIMIEN input-fil (.smi).
    
    Args:
        ifc_data: Output fra builtly_ifc_analyzer.analyze_ifc() (valgfritt)
        building_type: Bygningskategori
        location: Lokasjon for klimadata
        output_path: Filsti for output
        **manual_overrides: Overstyring av modell-parametere
    
    Returns:
        Absolutt filsti til generert .smi-fil
    """
    if ifc_data:
        model = ifc_to_simien_model(ifc_data, building_type, location, project_name)
    else:
        # Manuell/tom modell
        climate = CLIMATE_DATA.get(location.lower(), CLIMATE_DATA["oslo"])
        model = SimienModel(
            project_name=project_name,
            building_type=building_type,
            location=location,
            climate_data=climate,
        )

    # Bruk manuelle overstyringer
    for key, val in manual_overrides.items():
        if hasattr(model, key):
            setattr(model, key, val)

    xml_content = generate_simien_xml(model)

    output_path = str(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml_content)

    return os.path.abspath(output_path)


# ─── SIMIEN resultat-parser ─────────────────────────────────────────

@dataclass
class SimienResults:
    """Parsede SIMIEN-resultater."""
    total_energy_kWh_m2: float = 0.0           # Netto energibehov
    heating_energy_kWh_m2: float = 0.0
    cooling_energy_kWh_m2: float = 0.0
    ventilation_energy_kWh_m2: float = 0.0
    dhw_energy_kWh_m2: float = 0.0             # Varmtvann
    lighting_energy_kWh_m2: float = 0.0
    equipment_energy_kWh_m2: float = 0.0
    co2_emission_kg_m2: float = 0.0
    peak_heating_W_m2: float = 0.0
    peak_cooling_W_m2: float = 0.0
    heat_loss_number_W_m2K: float = 0.0
    transmission_loss_kWh: float = 0.0
    infiltration_loss_kWh: float = 0.0
    solar_gains_kWh: float = 0.0
    internal_gains_kWh: float = 0.0
    tek17_compliant: Optional[bool] = None
    energy_frame_limit: float = 0.0
    energy_label: str = ""                     # A, B, C, D, E, F, G
    raw_data: Dict[str, Any] = field(default_factory=dict)


def parse_simien_results(filepath: str) -> SimienResults:
    """
    Parse SIMIEN resultatfil (.smo / XML).
    
    Returnerer SimienResults med energitall.
    SIMIEN-output har varierende XML-struktur avhengig av versjon,
    så vi prøver flere XPath-mønstre.
    """
    results = SimienResults()

    try:
        tree = parse_xml(filepath)
        root = tree.getroot()
    except Exception as exc:
        logger.error(f"Kunne ikke parse SIMIEN-fil: {exc}")
        results.raw_data = {"error": str(exc)}
        return results

    # Søk etter energitall med flere mulige XML-stier
    xpath_map = {
        "total_energy_kWh_m2": [
            ".//NetEnergyDemand", ".//TotalEnergy", ".//EnergyDemand/Total",
            ".//Results/NetEnergy", ".//Energibehov/Netto",
        ],
        "heating_energy_kWh_m2": [
            ".//HeatingEnergy", ".//SpaceHeating", ".//Oppvarming",
            ".//Results/Heating", ".//Energibehov/Romoppvarming",
        ],
        "cooling_energy_kWh_m2": [
            ".//CoolingEnergy", ".//SpaceCooling", ".//Kjøling",
        ],
        "ventilation_energy_kWh_m2": [
            ".//VentilationEnergy", ".//Ventilation/Energy", ".//Viftedrift",
        ],
        "dhw_energy_kWh_m2": [
            ".//DHWEnergy", ".//HotWater", ".//Varmtvann",
        ],
        "lighting_energy_kWh_m2": [
            ".//LightingEnergy", ".//Lighting", ".//Belysning",
        ],
        "co2_emission_kg_m2": [
            ".//CO2Emission", ".//CO2", ".//Klimagassutslipp",
        ],
        "heat_loss_number_W_m2K": [
            ".//HeatLossNumber", ".//Varmetapstall",
        ],
        "energy_label": [
            ".//EnergyLabel", ".//Energimerke", ".//Label",
        ],
    }

    for attr, xpaths in xpath_map.items():
        for xpath in xpaths:
            el = root.find(xpath)
            if el is not None and el.text:
                try:
                    if attr == "energy_label":
                        setattr(results, attr, el.text.strip())
                    else:
                        setattr(results, attr, float(el.text))
                    break
                except (ValueError, TypeError):
                    continue

    # TEK17-sjekk
    if results.total_energy_kWh_m2 > 0:
        # Finn energiramme
        frame_el = root.find(".//EnergyFrame") or root.find(".//Energiramme")
        if frame_el is not None:
            try:
                results.energy_frame_limit = float(frame_el.text)
            except (ValueError, TypeError):
                pass
        results.tek17_compliant = results.total_energy_kWh_m2 <= results.energy_frame_limit if results.energy_frame_limit > 0 else None

    # Lagre rå XML-data
    results.raw_data = {
        "filename": os.path.basename(filepath),
        "root_tag": root.tag,
        "child_tags": [child.tag for child in root],
    }

    return results


# ─── TEK17 §14 verifisering ────────────────────────────────────────

def verify_tek17_energy(model: SimienModel) -> List[Dict[str, Any]]:
    """
    Verifiser SIMIEN-modell mot TEK17 §14 enkeltkomponentkrav.
    
    Returnerer liste av avvik.
    """
    issues = []
    limits = TEK17_ENERGY_REQUIREMENTS["component_limits"]

    # U-verdi sjekk per bygningsdel
    type_limits = {
        "wall": ("u_wall", limits["u_wall"]),
        "roof": ("u_roof", limits["u_roof"]),
        "floor": ("u_floor", limits["u_floor"]),
        "window": ("u_window_door", limits["u_window_door"]),
        "door": ("u_window_door", limits["u_window_door"]),
    }

    for c in model.constructions:
        if not c.is_external:
            continue
        limit_key, limit_val = type_limits.get(c.type, ("", 999))
        if c.u_value > limit_val:
            issues.append({
                "parameter": f"U-verdi {c.name}",
                "type": c.type,
                "required": f"≤ {limit_val} W/(m²·K)",
                "actual": f"{c.u_value:.3f} W/(m²·K)",
                "severity": "error",
                "tek17_ref": "§14-3",
                "description": f"{c.name}: U={c.u_value:.3f} overskrider TEK17 §14-3 krav på {limit_val} W/(m²·K).",
            })

    # Lekkasjetall
    if model.air_leakage_50Pa > limits["air_leakage_50Pa"]:
        issues.append({
            "parameter": "Luftlekkasje",
            "required": f"≤ {limits['air_leakage_50Pa']} 1/h ved 50 Pa",
            "actual": f"{model.air_leakage_50Pa} 1/h",
            "severity": "error",
            "tek17_ref": "§14-3",
            "description": f"Lekkasjetall {model.air_leakage_50Pa} overskrider TEK17 §14-3.",
        })

    # SFP
    if model.sfp_ventilation > limits["sfp_ventilation"]:
        issues.append({
            "parameter": "SFP ventilasjonsanlegg",
            "required": f"≤ {limits['sfp_ventilation']} kW/(m³/s)",
            "actual": f"{model.sfp_ventilation} kW/(m³/s)",
            "severity": "error",
            "tek17_ref": "§14-3",
            "description": f"SFP {model.sfp_ventilation} overskrider TEK17 §14-3.",
        })

    # Varmegjenvinner
    if model.heat_recovery_efficiency < limits["heat_recovery_eff"]:
        issues.append({
            "parameter": "Virkningsgrad varmegjenvinner",
            "required": f"≥ {limits['heat_recovery_eff']*100:.0f}%",
            "actual": f"{model.heat_recovery_efficiency*100:.0f}%",
            "severity": "warning",
            "tek17_ref": "§14-3",
            "description": f"Varmegjenvinner {model.heat_recovery_efficiency*100:.0f}% er under TEK17 §14-3 krav.",
        })

    # Normalisert kuldebroverdi
    if model.cold_bridge_normalized > limits["normalized_cold_bridge"]:
        issues.append({
            "parameter": "Normalisert kuldebroverdi",
            "required": f"≤ {limits['normalized_cold_bridge']} W/(m²·K)",
            "actual": f"{model.cold_bridge_normalized} W/(m²·K)",
            "severity": "warning",
            "tek17_ref": "§14-3",
            "description": f"Kuldebroverdi {model.cold_bridge_normalized} overskrider TEK17 §14-3.",
        })

    # Energiramme
    frame = TEK17_ENERGY_REQUIREMENTS["energy_frames"].get(model.building_type.lower())
    if frame:
        issues.append({
            "parameter": "Energiramme",
            "required": f"≤ {frame} kWh/(m²·år)",
            "actual": "Beregnes i SIMIEN",
            "severity": "info",
            "tek17_ref": "§14-2",
            "description": f"Energiramme for {model.building_type}: {frame} kWh/(m²·år) (TEK17 §14-2). Verifiser i SIMIEN.",
        })

    return issues


# ─── Streamlit-integrasjon ──────────────────────────────────────────

def render_simien_export(
    ifc_data: Optional[Dict[str, Any]] = None,
    building_type: str = "boligblokk",
    location: str = "trondheim",
    project_name: str = "Builtly prosjekt",
) -> Optional[str]:
    """Vis SIMIEN-eksport i Streamlit med parameterjustering."""
    try:
        import streamlit as st
    except ImportError:
        return None

    st.markdown("##### SIMIEN energiberegning")

    col1, col2 = st.columns(2)
    with col1:
        building_type = st.selectbox(
            "Bygningskategori",
            list(TEK17_ENERGY_REQUIREMENTS["energy_frames"].keys()),
            index=list(TEK17_ENERGY_REQUIREMENTS["energy_frames"].keys()).index(building_type)
            if building_type in TEK17_ENERGY_REQUIREMENTS["energy_frames"] else 0,
            key="simien_building_type",
        )
    with col2:
        location = st.selectbox(
            "Lokasjon",
            list(CLIMATE_DATA.keys()),
            index=list(CLIMATE_DATA.keys()).index(location.lower())
            if location.lower() in CLIMATE_DATA else 0,
            key="simien_location",
        )

    frame = TEK17_ENERGY_REQUIREMENTS["energy_frames"].get(building_type, 100)
    climate = CLIMATE_DATA.get(location, {})
    st.caption(
        f"Energiramme TEK17: {frame} kWh/(m²·år) | "
        f"Graddagstall: {climate.get('heating_degree_days', '?')} | "
        f"DUT: {climate.get('design_temp_winter', '?')}°C"
    )

    if st.button("📊 Generer SIMIEN-fil", use_container_width=True, key="simien_gen"):
        output_dir = Path("qa_database") / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"{project_name.replace(' ', '_')}.smi")

        smi_path = generate_simien_input(
            ifc_data=ifc_data,
            building_type=building_type,
            location=location,
            project_name=project_name,
            output_path=output_path,
        )

        with open(smi_path, "r", encoding="utf-8") as f:
            xml_content = f.read()

        st.download_button(
            label="⬇️ Last ned SIMIEN-fil (.smi)",
            data=xml_content,
            file_name=os.path.basename(smi_path),
            mime="application/xml",
            key="simien_download",
        )

        # Vis TEK17-sjekk
        if ifc_data:
            model = ifc_to_simien_model(ifc_data, building_type, location, project_name)
            tek_issues = verify_tek17_energy(model)
            errors = [i for i in tek_issues if i["severity"] == "error"]
            if errors:
                st.warning(f"{len(errors)} TEK17 §14 avvik funnet:")
                for issue in errors:
                    st.markdown(f"- **{issue['parameter']}**: {issue['actual']} (krav: {issue['required']})")

        return smi_path

    return None
