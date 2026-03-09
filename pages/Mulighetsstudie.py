
import base64
import io
import json
import math
import os
import re
import tempfile
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
import streamlit as st
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

try:
    import google.generativeai as genai
except Exception:
    genai = None

try:
    import fitz
except ImportError:
    fitz = None


# --- 1. TEKNISK OPPSETT ---
st.set_page_config(
    page_title="Mulighetsstudie (ARK) | Builtly",
    layout="wide",
    initial_sidebar_state="collapsed",
)

google_key = os.environ.get("GOOGLE_API_KEY")
llm_available = bool(google_key and genai is not None)
if llm_available:
    try:
        genai.configure(api_key=google_key)
    except Exception:
        llm_available = False


# --- 2. HJELPEFUNKSJONER ---
def render_html(html_string: str) -> None:
    st.markdown(html_string.replace("\n", " "), unsafe_allow_html=True)


def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            suffix = Path(candidate).suffix.lower().replace(".", "") or "png"
            with open(candidate, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/{suffix};base64,{encoded}"
    return ""


def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{name}.py")
        if p.exists():
            return str(p)
    return ""


def clean_pdf_text(text: Any) -> str:
    if text is None:
        return ""
    text = str(text)
    rep = {
        "–": "-",
        "—": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "…": "...",
        "•": "*",
        "²": "2",
        "³": "3",
        "ø": "o",
        "Ø": "O",
        "å": "a",
        "Å": "A",
        "æ": "ae",
        "Æ": "AE",
    }
    for old, new in rep.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def ironclad_text_formatter(text: str) -> str:
    text = str(text).replace("$", "").replace("*", "").replace("_", "")
    text = re.sub(r"[-|=]{3,}", " ", text)
    text = re.sub(r"([^\s]{40})", r"\1 ", text)
    return clean_pdf_text(text)


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        cleaned = str(value).strip().replace(" ", "").replace(",", ".")
        match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if match:
            return float(match.group(0))
    except Exception:
        pass
    return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_norwegian_text(text: str) -> str:
    text = text or ""
    replacements = {
        "ø": "o",
        "Ø": "O",
        "å": "a",
        "Å": "A",
        "æ": "ae",
        "Æ": "AE",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def pick_model_name() -> Optional[str]:
    if not llm_available:
        return None
    try:
        valid_models = [
            model.name
            for model in genai.list_models()
            if "generateContent" in getattr(model, "supported_generation_methods", [])
        ]
    except Exception:
        return None

    preferred_fragments = [
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-pro",
    ]
    for fragment in preferred_fragments:
        for candidate in valid_models:
            if fragment in candidate:
                return candidate
    return valid_models[0] if valid_models else None


# --- 3. GEODATA / KART ---
@st.cache_data(show_spinner=False, ttl=60 * 60 * 12)
def fetch_lat_lon(adresse: str, kommune: str) -> Tuple[Optional[float], Optional[float], str]:
    query = ", ".join([x for x in [adresse, kommune, "Norway"] if x])
    if not query:
        return None, None, "Ingen adresse oppgitt"

    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "jsonv2", "limit": 1},
            headers={"User-Agent": "BuiltlyFeasibility/1.0"},
            timeout=8,
        )
        if resp.status_code == 200:
            hits = resp.json()
            if hits:
                return float(hits[0]["lat"]), float(hits[0]["lon"]), "OpenStreetMap/Nominatim"
    except Exception:
        pass
    return None, None, "Kunne ikke geokode med Nominatim"


@st.cache_data(show_spinner=False, ttl=60 * 60 * 6)
def fetch_map_image(adresse: str, kommune: str, gnr: str, bnr: str, api_key: str) -> Tuple[Optional[Image.Image], str]:
    nord, ost = None, None
    adr_clean = adresse.replace(",", "").strip() if adresse else ""
    kom_clean = kommune.replace(",", "").strip() if kommune else ""

    queries = []
    if adr_clean and kom_clean:
        queries.append(f"{adr_clean} {kom_clean}")
    if adr_clean:
        queries.append(adr_clean)
    if gnr and bnr and kom_clean:
        queries.append(f"{kom_clean} {gnr}/{bnr}")

    for q in queries:
        safe_query = urllib.parse.quote(q)
        url = (
            "https://ws.geonorge.no/adresser/v1/sok"
            f"?sok={safe_query}&fuzzy=true&utkoordsys=25833&treffPerSide=1"
        )
        try:
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200 and resp.json().get("adresser"):
                hit = resp.json()["adresser"][0]
                nord = hit.get("representasjonspunkt", {}).get("nord")
                ost = hit.get("representasjonspunkt", {}).get("øst")
                break
        except Exception:
            pass

    if nord and ost:
        min_x, max_x = float(ost) - 180, float(ost) + 180
        min_y, max_y = float(nord) - 180, float(nord) + 180
        url_orto = (
            "https://wms.geonorge.no/skwms1/wms.nib"
            "?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto"
            f"&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}"
            "&width=900&height=900&format=image/png"
        )
        try:
            r1 = requests.get(url_orto, timeout=8)
            if r1.status_code == 200 and len(r1.content) > 5000:
                return Image.open(io.BytesIO(r1.content)).convert("RGB"), "Kartverket (Norge i Bilder)"
        except Exception:
            pass

    if api_key and (adr_clean or kom_clean):
        query = f"{adr_clean}, {kom_clean}, Norway"
        safe_query = urllib.parse.quote(query)
        url_gmaps = (
            "https://maps.googleapis.com/maps/api/staticmap"
            f"?center={safe_query}&zoom=19&size=700x700&maptype=satellite&key={api_key}"
        )
        try:
            r2 = requests.get(url_gmaps, timeout=8)
            if r2.status_code == 200 and len(r2.content) > 1000:
                return Image.open(io.BytesIO(r2.content)).convert("RGB"), "Google Maps Satellite"
        except Exception:
            pass

    return None, "Kunne ikke hente kart fra verken Kartverket eller Google."


def load_uploaded_visuals(uploaded_files: Optional[List[Any]]) -> List[Image.Image]:
    images: List[Image.Image] = []
    if not uploaded_files:
        return images

    for uploaded in uploaded_files:
        try:
            uploaded.seek(0)
            if uploaded.name.lower().endswith(".pdf"):
                if fitz is None:
                    continue
                data = uploaded.read()
                doc = fitz.open(stream=data, filetype="pdf")
                for page_num in range(min(4, len(doc))):
                    pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                    img.thumbnail((1400, 1400))
                    images.append(img)
                doc.close()
            else:
                img = Image.open(uploaded).convert("RGB")
                img.thumbnail((1400, 1400))
                images.append(img)
        except Exception:
            continue
    return images


# --- 4. ANALYSEMOTOR ---
@dataclass
class MixSpec:
    name: str
    share_pct: float
    avg_size_m2: float


@dataclass
class SiteInputs:
    site_area_m2: float
    site_width_m: float
    site_depth_m: float
    front_setback_m: float
    rear_setback_m: float
    side_setback_m: float
    max_bya_pct: float
    max_bra_m2: float
    desired_bta_m2: float
    max_floors: int
    max_height_m: float
    floor_to_floor_m: float
    efficiency_ratio: float
    parking_ratio_per_unit: float
    parking_area_per_space_m2: float
    latitude_deg: float
    north_rotation_deg: float


@dataclass
class OptionResult:
    name: str
    typology: str
    floors: int
    building_height_m: float
    footprint_area_m2: float
    gross_bta_m2: float
    saleable_area_m2: float
    footprint_width_m: float
    footprint_depth_m: float
    open_space_ratio: float
    target_fit_pct: float
    unit_count: int
    mix_counts: Dict[str, int]
    parking_spaces: int
    parking_pressure_pct: float
    solar_score: float
    estimated_equinox_sun_hours: float
    estimated_winter_sun_hours: float
    winter_noon_shadow_m: float
    equinox_noon_shadow_m: float
    summer_afternoon_shadow_m: float
    efficiency_ratio: float
    notes: List[str]
    score: float
    geometry: Dict[str, Any]


def parse_regulation_hints(free_text: str) -> Dict[str, float]:
    text = normalize_norwegian_text(free_text or "")
    out: Dict[str, float] = {}

    bya_patterns = [
        r"%-?\s*BYA[^0-9]*(\d+(?:[.,]\d+)?)",
        r"BYA[^0-9]*(\d+(?:[.,]\d+)?)\s*%",
        r"utnyttelse[^0-9]*(\d+(?:[.,]\d+)?)\s*%",
    ]
    bra_patterns = [
        r"BRA[^0-9]*(\d+(?:[.,]\d+)?)\s*m",
        r"maks[^0-9]*(\d+(?:[.,]\d+)?)\s*m2",
    ]
    floor_patterns = [
        r"(\d+)\s*etasj",
        r"maks[^0-9]*(\d+)\s*plan",
    ]
    height_patterns = [
        r"gesimshoyde[^0-9]*(\d+(?:[.,]\d+)?)\s*m",
        r"byggehoyde[^0-9]*(\d+(?:[.,]\d+)?)\s*m",
        r"maks[^0-9]*(\d+(?:[.,]\d+)?)\s*m",
    ]

    for pattern in bya_patterns:
        if match := re.search(pattern, text, re.IGNORECASE):
            out["max_bya_pct"] = safe_float(match.group(1))
            break
    for pattern in bra_patterns:
        if match := re.search(pattern, text, re.IGNORECASE):
            out["max_bra_m2"] = safe_float(match.group(1))
            break
    for pattern in floor_patterns:
        if match := re.search(pattern, text, re.IGNORECASE):
            out["max_floors"] = int(safe_float(match.group(1), 0))
            break
    for pattern in height_patterns:
        if match := re.search(pattern, text, re.IGNORECASE):
            out["max_height_m"] = safe_float(match.group(1))
            break

    return out


def normalize_mix_specs(mix_specs: List[MixSpec]) -> List[MixSpec]:
    cleaned = [spec for spec in mix_specs if spec.avg_size_m2 > 0 and spec.share_pct >= 0]
    if not cleaned:
        cleaned = [
            MixSpec("1-rom", 15, 38),
            MixSpec("2-rom", 35, 52),
            MixSpec("3-rom", 35, 72),
            MixSpec("4-rom+", 15, 95),
        ]
    total_share = sum(spec.share_pct for spec in cleaned)
    if total_share <= 0:
        equal = 100.0 / len(cleaned)
        return [MixSpec(spec.name, equal, spec.avg_size_m2) for spec in cleaned]
    return [MixSpec(spec.name, (spec.share_pct / total_share) * 100.0, spec.avg_size_m2) for spec in cleaned]


def allocate_unit_mix(saleable_area_m2: float, mix_specs: List[MixSpec]) -> Tuple[Dict[str, int], float]:
    specs = normalize_mix_specs(mix_specs)
    if saleable_area_m2 <= 0:
        return {spec.name: 0 for spec in specs}, 0.0

    target_areas = [saleable_area_m2 * (spec.share_pct / 100.0) for spec in specs]
    counts = [int(area // spec.avg_size_m2) for area, spec in zip(target_areas, specs)]
    used_area = sum(count * spec.avg_size_m2 for count, spec in zip(counts, specs))

    remainders = [
        ((target_areas[idx] / specs[idx].avg_size_m2) - counts[idx], idx)
        for idx in range(len(specs))
    ]
    remainders.sort(reverse=True)

    leftover = saleable_area_m2 - used_area
    guard = 0
    while leftover >= min(spec.avg_size_m2 for spec in specs) and guard < 50:
        placed = False
        for _, idx in remainders:
            size = specs[idx].avg_size_m2
            if leftover >= size:
                counts[idx] += 1
                leftover -= size
                placed = True
                break
        if not placed:
            smallest_idx = min(range(len(specs)), key=lambda i: specs[i].avg_size_m2)
            size = specs[smallest_idx].avg_size_m2
            if leftover >= size:
                counts[smallest_idx] += 1
                leftover -= size
            else:
                break
        guard += 1

    return {spec.name: count for spec, count in zip(specs, counts)}, saleable_area_m2 - leftover


def solar_declination_rad(day_of_year: int) -> float:
    return math.radians(23.44) * math.sin(math.radians((360.0 / 365.0) * (day_of_year - 81)))


def solar_altitude_deg(latitude_deg: float, day_of_year: int, solar_hour: float) -> float:
    lat = math.radians(latitude_deg)
    decl = solar_declination_rad(day_of_year)
    hour_angle = math.radians(15.0 * (solar_hour - 12.0))
    sin_alt = (
        math.sin(lat) * math.sin(decl)
        + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    )
    sin_alt = clamp(sin_alt, -1.0, 1.0)
    return math.degrees(math.asin(sin_alt))


def shadow_length_m(height_m: float, altitude_deg: float) -> float:
    if altitude_deg <= 0.5:
        return height_m * 50.0
    return height_m / max(0.03, math.tan(math.radians(altitude_deg)))


def derive_limits(site: SiteInputs) -> Dict[str, float]:
    buildable_width = max(8.0, site.site_width_m - (2.0 * site.side_setback_m))
    buildable_depth = max(8.0, site.site_depth_m - site.front_setback_m - site.rear_setback_m)
    buildable_area = buildable_width * buildable_depth
    max_footprint_by_bya = site.site_area_m2 * (site.max_bya_pct / 100.0) if site.max_bya_pct > 0 else buildable_area
    max_footprint = min(buildable_area, max_footprint_by_bya)
    floors_from_height = max(1, int(site.max_height_m // max(site.floor_to_floor_m, 2.8))) if site.max_height_m > 0 else site.max_floors
    allowed_floors = max(1, min(site.max_floors, floors_from_height))
    return {
        "buildable_width": buildable_width,
        "buildable_depth": buildable_depth,
        "buildable_area": buildable_area,
        "max_footprint": max_footprint,
        "allowed_floors": float(allowed_floors),
    }


def build_typology_geometry(
    typology: str,
    target_footprint_m2: float,
    buildable_width_m: float,
    buildable_depth_m: float,
) -> Dict[str, Any]:
    rects: List[Dict[str, float]] = []

    if typology == "Lamell":
        depth = clamp(14.0, 10.0, max(10.0, buildable_depth_m * 0.75))
        depth = min(depth, buildable_depth_m * 0.75)
        depth = max(10.0, min(depth, buildable_depth_m))
        width = min(buildable_width_m, max(18.0, target_footprint_m2 / max(depth, 1.0)))
        if width * depth > target_footprint_m2 * 1.08:
            width = target_footprint_m2 / max(depth, 1.0)
        width = min(width, buildable_width_m)
        x = (buildable_width_m - width) / 2.0
        y = (buildable_depth_m - depth) / 2.0
        rects = [{"x": x, "y": y, "w": width, "h": depth}]
        area = width * depth
        return {
            "rects": rects,
            "footprint_width_m": width,
            "footprint_depth_m": depth,
            "footprint_area_m2": area,
            "clear_south_m": max(4.0, y),
            "courtyard_width_m": 0.0,
        }

    if typology == "Punkthus":
        side = min(buildable_width_m * 0.82, buildable_depth_m * 0.82, math.sqrt(max(target_footprint_m2, 1.0)))
        side = max(14.0, side)
        x = (buildable_width_m - side) / 2.0
        y = (buildable_depth_m - side) / 2.0
        rects = [{"x": x, "y": y, "w": side, "h": side}]
        area = side * side
        return {
            "rects": rects,
            "footprint_width_m": side,
            "footprint_depth_m": side,
            "footprint_area_m2": area,
            "clear_south_m": max(6.0, y),
            "courtyard_width_m": 0.0,
        }

    # Tun / U-form
    wing = min(11.5, buildable_width_m * 0.22, buildable_depth_m * 0.22)
    outer_w = min(buildable_width_m, max(24.0, buildable_width_m * 0.82))
    outer_d = min(buildable_depth_m, max(22.0, buildable_depth_m * 0.78))
    x0 = (buildable_width_m - outer_w) / 2.0
    y0 = (buildable_depth_m - outer_d) / 2.0

    rects = [
        {"x": x0, "y": y0, "w": outer_w, "h": wing},
        {"x": x0, "y": y0, "w": wing, "h": outer_d},
        {"x": x0 + outer_w - wing, "y": y0, "w": wing, "h": outer_d},
    ]
    area = sum(rect["w"] * rect["h"] for rect in rects)
    if area > max(target_footprint_m2, 1.0):
        scale = math.sqrt(target_footprint_m2 / area)
        center_x = buildable_width_m / 2.0
        center_y = buildable_depth_m / 2.0
        scaled_rects = []
        for rect in rects:
            cx = rect["x"] + rect["w"] / 2.0
            cy = rect["y"] + rect["h"] / 2.0
            new_w = rect["w"] * scale
            new_h = rect["h"] * scale
            new_cx = center_x + (cx - center_x) * scale
            new_cy = center_y + (cy - center_y) * scale
            scaled_rects.append(
                {
                    "x": new_cx - new_w / 2.0,
                    "y": new_cy - new_h / 2.0,
                    "w": new_w,
                    "h": new_h,
                }
            )
        rects = scaled_rects
        area = sum(rect["w"] * rect["h"] for rect in rects)

    width = max(rect["x"] + rect["w"] for rect in rects) - min(rect["x"] for rect in rects)
    depth = max(rect["y"] + rect["h"] for rect in rects) - min(rect["y"] for rect in rects)
    courtyard_width = max(0.0, width - (2.0 * wing))
    clear_south = max(6.0, depth - wing)
    return {
        "rects": rects,
        "footprint_width_m": width,
        "footprint_depth_m": depth,
        "footprint_area_m2": area,
        "clear_south_m": clear_south,
        "courtyard_width_m": courtyard_width,
    }


def evaluate_solar(site: SiteInputs, geometry: Dict[str, Any], building_height_m: float, typology: str) -> Dict[str, float]:
    winter_alt = solar_altitude_deg(site.latitude_deg, 355, 12.0)
    equinox_alt = solar_altitude_deg(site.latitude_deg, 80, 12.0)
    summer_alt = solar_altitude_deg(site.latitude_deg, 172, 15.0)

    winter_shadow = shadow_length_m(building_height_m, winter_alt)
    equinox_shadow = shadow_length_m(building_height_m, equinox_alt)
    summer_shadow = shadow_length_m(building_height_m, summer_alt)

    clear_south = geometry.get("clear_south_m", 8.0)
    courtyard_bonus = min(0.18, geometry.get("courtyard_width_m", 0.0) / 120.0)
    typology_bonus = {"Punkthus": 0.08, "Lamell": 0.04, "Tun": -0.02}.get(typology, 0.0)

    equinox_ratio = clear_south / max(1.0, equinox_shadow)
    winter_ratio = clear_south / max(1.0, winter_shadow)

    equinox_hours = clamp(2.5 + (equinox_ratio * 4.6), 1.0, 8.0)
    winter_hours = clamp(0.4 + (winter_ratio * 2.2), 0.0, 4.0)

    orientation_penalty = 0.0
    if typology == "Lamell":
        orientation_penalty = abs(math.sin(math.radians(site.north_rotation_deg))) * 8.0
    elif typology == "Tun":
        orientation_penalty = abs(math.sin(math.radians(site.north_rotation_deg))) * 4.0
    else:
        orientation_penalty = abs(math.sin(math.radians(site.north_rotation_deg))) * 2.0

    solar_score = 100.0 * clamp(
        0.56 * min(1.0, equinox_ratio)
        + 0.19 * min(1.0, winter_ratio)
        + courtyard_bonus
        + typology_bonus,
        0.18,
        1.0,
    ) - orientation_penalty
    solar_score = clamp(solar_score, 18.0, 100.0)

    return {
        "solar_score": solar_score,
        "estimated_equinox_sun_hours": equinox_hours,
        "estimated_winter_sun_hours": winter_hours,
        "winter_noon_shadow_m": winter_shadow,
        "equinox_noon_shadow_m": equinox_shadow,
        "summer_afternoon_shadow_m": summer_shadow,
    }


def rank_score(
    target_fit_pct: float,
    solar_score: float,
    open_space_ratio: float,
    efficiency_ratio: float,
    parking_pressure_pct: float,
) -> float:
    target_score = max(0.0, 100.0 - abs(100.0 - target_fit_pct))
    return round(
        0.34 * target_score
        + 0.28 * solar_score
        + 0.18 * (open_space_ratio * 100.0)
        + 0.14 * (efficiency_ratio * 100.0)
        + 0.06 * max(0.0, 100.0 - parking_pressure_pct),
        1,
    )


def generate_options(site: SiteInputs, mix_specs: List[MixSpec]) -> List[OptionResult]:
    limits = derive_limits(site)
    buildable_width = limits["buildable_width"]
    buildable_depth = limits["buildable_depth"]
    max_footprint = limits["max_footprint"]
    allowed_floors = int(limits["allowed_floors"])

    if max_footprint <= 0:
        return []

    templates = [
        {"name": "Alt A - Lamell", "typology": "Lamell", "coverage": 0.74, "floor_bias": 0, "eff_adj": 0.02},
        {"name": "Alt B - Punkthus", "typology": "Punkthus", "coverage": 0.56, "floor_bias": 1, "eff_adj": -0.01},
        {"name": "Alt C - Tun", "typology": "Tun", "coverage": 0.84, "floor_bias": -1, "eff_adj": -0.03},
    ]

    options: List[OptionResult] = []
    target_bta = max(site.desired_bta_m2, 1.0)

    for template in templates:
        typology = template["typology"]
        target_footprint = max_footprint * template["coverage"]
        geometry = build_typology_geometry(typology, target_footprint, buildable_width, buildable_depth)
        footprint_area = geometry["footprint_area_m2"]

        floors_needed = math.ceil(target_bta / max(footprint_area, 1.0))
        floors = clamp(floors_needed + template["floor_bias"], 2, allowed_floors)
        floors = int(floors)

        gross_bta = footprint_area * floors
        if site.max_bra_m2 > 0:
            gross_bta = min(gross_bta, site.max_bra_m2)

        actual_efficiency = clamp(site.efficiency_ratio + template["eff_adj"], 0.66, 0.88)
        saleable_area = gross_bta * actual_efficiency

        mix_counts, mix_area_used = allocate_unit_mix(saleable_area, mix_specs)
        unit_count = sum(mix_counts.values())
        parking_spaces = int(math.ceil(unit_count * site.parking_ratio_per_unit)) if unit_count > 0 else 0
        parking_pressure_area = parking_spaces * site.parking_area_per_space_m2
        open_space_ratio = max(0.0, 1.0 - (footprint_area / max(site.site_area_m2, 1.0)))
        parking_pressure_pct = (
            100.0 * parking_pressure_area / max(site.site_area_m2 * open_space_ratio, 1.0)
            if open_space_ratio > 0
            else 100.0
        )

        height_m = floors * site.floor_to_floor_m
        solar = evaluate_solar(site, geometry, height_m, typology)
        target_fit_pct = 100.0 * gross_bta / target_bta if target_bta > 0 else 100.0

        notes: List[str] = []
        if target_fit_pct < 85:
            notes.append("Lav måloppnåelse mot ønsket volum; krever høyere utnyttelse eller omprosjektering.")
        elif target_fit_pct > 110:
            notes.append("Ligger over ønsket volum; vurder nedskalering eller større leiligheter.")
        else:
            notes.append("Treffer ønsket volum relativt godt i tidligfase.")

        if solar["solar_score"] < 55:
            notes.append("Svak indikativ sol/situasjon; særlig vinter og skuldersesong kan bli krevende.")
        elif solar["solar_score"] < 70:
            notes.append("Middels indikativ sol/situasjon; bør testes videre med mer presis solmodell.")
        else:
            notes.append("God indikativ soltilgang i forhold til høyde og åpne flater.")

        if parking_pressure_pct > 90:
            notes.append("Parkering legger stort press på tilgjengelig uteareal dersom alt skal løses på terreng.")
        elif parking_pressure_pct > 65:
            notes.append("Parkering er håndterbar, men bør optimaliseres med kjeller eller mobilitetsgrep.")

        if typology == "Lamell":
            notes.append("Lamell er som regel sterkest på effektivitet, dagslys og repetérbar boliglogikk.")
        elif typology == "Punkthus":
            notes.append("Punkthus gir ofte best lys og sikt, men taper gjerne litt effektivitet og kjerneøkonomi.")
        else:
            notes.append("Tun/U-form gir høy arealutnyttelse og tydelig uterom, men kan gi mer krevende solforhold.")

        score = rank_score(
            target_fit_pct=target_fit_pct,
            solar_score=solar["solar_score"],
            open_space_ratio=open_space_ratio,
            efficiency_ratio=actual_efficiency,
            parking_pressure_pct=parking_pressure_pct,
        )

        options.append(
            OptionResult(
                name=template["name"],
                typology=typology,
                floors=floors,
                building_height_m=height_m,
                footprint_area_m2=round(footprint_area, 1),
                gross_bta_m2=round(gross_bta, 1),
                saleable_area_m2=round(saleable_area, 1),
                footprint_width_m=round(geometry["footprint_width_m"], 1),
                footprint_depth_m=round(geometry["footprint_depth_m"], 1),
                open_space_ratio=round(open_space_ratio, 3),
                target_fit_pct=round(target_fit_pct, 1),
                unit_count=unit_count,
                mix_counts=mix_counts,
                parking_spaces=parking_spaces,
                parking_pressure_pct=round(parking_pressure_pct, 1),
                solar_score=round(solar["solar_score"], 1),
                estimated_equinox_sun_hours=round(solar["estimated_equinox_sun_hours"], 1),
                estimated_winter_sun_hours=round(solar["estimated_winter_sun_hours"], 1),
                winter_noon_shadow_m=round(solar["winter_noon_shadow_m"], 1),
                equinox_noon_shadow_m=round(solar["equinox_noon_shadow_m"], 1),
                summer_afternoon_shadow_m=round(solar["summer_afternoon_shadow_m"], 1),
                efficiency_ratio=round(actual_efficiency, 3),
                notes=notes,
                score=score,
                geometry=geometry,
            )
        )

    options.sort(key=lambda option: option.score, reverse=True)
    return options


def render_plan_diagram(site: SiteInputs, option: OptionResult) -> Image.Image:
    canvas_w, canvas_h = 900, 620
    margin = 60
    img = Image.new("RGBA", (canvas_w, canvas_h), (6, 17, 26, 255))
    draw = ImageDraw.Draw(img, "RGBA")
    font = ImageFont.load_default()

    scale = min(
        (canvas_w - (2 * margin)) / max(site.site_width_m, 1.0),
        (canvas_h - (2 * margin)) / max(site.site_depth_m, 1.0),
    )

    def sx(x_m: float) -> float:
        return margin + (x_m * scale)

    def sy(y_m: float) -> float:
        return margin + (y_m * scale)

    site_box = [sx(0), sy(0), sx(site.site_width_m), sy(site.site_depth_m)]
    buildable_x = site.side_setback_m
    buildable_y = site.front_setback_m
    buildable_w = max(0.0, site.site_width_m - (2.0 * site.side_setback_m))
    buildable_h = max(0.0, site.site_depth_m - site.front_setback_m - site.rear_setback_m)
    buildable_box = [sx(buildable_x), sy(buildable_y), sx(buildable_x + buildable_w), sy(buildable_y + buildable_h)]

    draw.rounded_rectangle(site_box, radius=12, outline=(130, 151, 178, 255), width=3, fill=(13, 24, 36, 255))
    draw.rounded_rectangle(buildable_box, radius=10, outline=(56, 189, 248, 220), width=2, fill=(56, 189, 248, 26))

    # North arrow
    arrow_x = canvas_w - 70
    arrow_y = 70
    draw.line((arrow_x, arrow_y + 30, arrow_x, arrow_y - 20), fill=(245, 247, 251, 255), width=4)
    draw.polygon(
        [(arrow_x, arrow_y - 32), (arrow_x - 10, arrow_y - 8), (arrow_x + 10, arrow_y - 8)],
        fill=(245, 247, 251, 255),
    )
    draw.text((arrow_x - 7, arrow_y + 36), "N", fill=(245, 247, 251, 255), font=font)
    draw.text((arrow_x - 22, arrow_y + 52), f"rot {site.north_rotation_deg:.0f}°", fill=(159, 176, 195, 255), font=font)

    rects = option.geometry.get("rects", [])
    footprint_bbox = [1e9, 1e9, -1e9, -1e9]
    for rect in rects:
        px0, py0 = sx(buildable_x + rect["x"]), sy(buildable_y + rect["y"])
        px1, py1 = sx(buildable_x + rect["x"] + rect["w"]), sy(buildable_y + rect["y"] + rect["h"])
        footprint_bbox[0] = min(footprint_bbox[0], px0)
        footprint_bbox[1] = min(footprint_bbox[1], py0)
        footprint_bbox[2] = max(footprint_bbox[2], px1)
        footprint_bbox[3] = max(footprint_bbox[3], py1)

    if footprint_bbox[0] < footprint_bbox[2]:
        shadow_px = option.winter_noon_shadow_m * scale
        shadow_box = [
            footprint_bbox[0],
            max(margin, footprint_bbox[1] - shadow_px),
            footprint_bbox[2],
            footprint_bbox[1],
        ]
        draw.rectangle(shadow_box, fill=(255, 213, 79, 50), outline=(255, 213, 79, 140))

    for rect in rects:
        px0, py0 = sx(buildable_x + rect["x"]), sy(buildable_y + rect["y"])
        px1, py1 = sx(buildable_x + rect["x"] + rect["w"]), sy(buildable_y + rect["y"] + rect["h"])
        draw.rounded_rectangle(
            [px0, py0, px1, py1],
            radius=8,
            fill=(34, 197, 94, 215),
            outline=(220, 252, 231, 255),
            width=2,
        )

    draw.text((margin, 16), f"{option.name} | {option.typology}", fill=(245, 247, 251, 255), font=font)
    draw.text(
        (margin, canvas_h - 48),
        f"Fotavtrykk {option.footprint_area_m2:.0f} m2 | Hoyde {option.building_height_m:.1f} m | Vinterskygge kl 12 ca. {option.winter_noon_shadow_m:.0f} m",
        fill=(200, 211, 223, 255),
        font=font,
    )
    draw.text(
        (margin, canvas_h - 28),
        f"Tomt {site.site_width_m:.0f} x {site.site_depth_m:.0f} m | Byggefelt markert med bla ramme",
        fill=(159, 176, 195, 255),
        font=font,
    )

    return img.convert("RGB")


def option_to_record(option: OptionResult) -> Dict[str, Any]:
    record = asdict(option)
    record["mix_counts"] = json.dumps(option.mix_counts, ensure_ascii=False)
    record["notes"] = " | ".join(option.notes)
    record.pop("geometry", None)
    return record


def build_deterministic_report(
    site: SiteInputs,
    options: List[OptionResult],
    parsed_hints: Dict[str, float],
    has_visual_input: bool,
) -> str:
    if not options:
        return (
            "# 1. OPPSUMMERING\n"
            "Ingen alternativ kunne genereres fordi tomtegeometri eller reguleringsgrenser er for svake.\n\n"
            "# 2. GRUNNLAG\n"
            "- Kontroller tomteareal, bredde/dybde, byggegrenser og BYA/BRA.\n"
        )

    best = options[0]
    lines = []
    lines.append("# 1. OPPSUMMERING")
    lines.append(
        f"Beste indikative alternativ er {best.name} ({best.typology}) med score {best.score}/100. "
        f"Det gir omtrent {best.gross_bta_m2:.0f} m2 BTA, {best.saleable_area_m2:.0f} m2 salgbart areal "
        f"og ca. {best.unit_count} boliger innenfor dagens oppgitte rammer."
    )
    lines.append("")
    lines.append("# 2. GRUNNLAG")
    lines.append(f"- Tomteareal: {site.site_area_m2:.0f} m2")
    lines.append(f"- Tomtedimensjon: ca. {site.site_width_m:.1f} x {site.site_depth_m:.1f} m")
    lines.append(
        f"- Byggegrenser: front {site.front_setback_m:.1f} m, bak {site.rear_setback_m:.1f} m, side {site.side_setback_m:.1f} m"
    )
    lines.append(f"- Maks BYA: {site.max_bya_pct:.1f}%")
    lines.append(
        f"- Maks BRA: {'ikke satt' if site.max_bra_m2 <= 0 else f'{site.max_bra_m2:.0f} m2'}"
    )
    lines.append(f"- Maks etasjer: {site.max_floors}")
    lines.append(f"- Maks hoyde: {site.max_height_m:.1f} m")
    lines.append(f"- Onsket BTA: {site.desired_bta_m2:.0f} m2")
    lines.append(f"- Solanalyse basert pa breddegrad: {site.latitude_deg:.3f}")
    lines.append(f"- Visuelt grunnlag lastet opp: {'ja' if has_visual_input else 'nei'}")
    if parsed_hints:
        lines.append(f"- Tolket fra fritekst: {json.dumps(parsed_hints, ensure_ascii=False)}")
    lines.append("")
    lines.append("# 3. VIKTIGSTE FORUTSETNINGER")
    lines.append("- Analysen er deterministisk og skjematisk; den erstatter ikke detaljert reguleringstolkning.")
    lines.append("- Sol/skygge er indikativ og er ikke en full 3D-simulering mot faktisk nabobebyggelse og terreng.")
    lines.append("- Leilighetsmiks beregnes ut fra salgbart areal og gjennomsnittsstorrelser, ikke full planlosning.")
    lines.append("")
    lines.append("# 4. TOMT OG KONTEKST")
    lines.append(
        "Tomten analyseres som et forenklet byggefelt innenfor oppgitte tomtedimensjoner, byggegrenser og breddegrad. "
        "Eventuelle opplastede kart og ortofoto brukes kun som visuell kontekst i rapporten."
    )
    lines.append("")
    lines.append("# 5. REGULERINGSMESSIGE FORHOLD")
    lines.append(
        f"Maks fotavtrykk styres primart av BYA og byggegrenser. Høydebegrensning og etasjeantall gir et indikativt tak pa {min(site.max_floors, max(1, int(site.max_height_m // max(site.floor_to_floor_m, 2.8))))} etasjer."
    )
    lines.append("")
    lines.append("# 6. ARKITEKTONISK VURDERING")
    lines.append(
        f"{best.typology} fremstar som sterkest i denne runden fordi kombinasjonen av volumtreff, solscore ({best.solar_score:.0f}/100) "
        f"og utnyttelse av byggefeltet er best balansert."
    )
    lines.append("")
    lines.append("# 7. MULIGE UTVIKLINGSGREP")
    for option in options:
        lines.append(
            f"- {option.name}: {option.typology}, {option.floors} etasjer, {option.gross_bta_m2:.0f} m2 BTA, "
            f"{option.unit_count} boliger, solscore {option.solar_score:.0f}/100."
        )
    lines.append("")
    lines.append("# 8. ALTERNATIVER")
    for option in options:
        lines.append(f"## {option.name}")
        lines.append(
            f"- Typologi: {option.typology}\n"
            f"- Fotavtrykk: {option.footprint_area_m2:.0f} m2 ({option.footprint_width_m:.1f} x {option.footprint_depth_m:.1f} m)\n"
            f"- BTA: {option.gross_bta_m2:.0f} m2\n"
            f"- Salgbart areal: {option.saleable_area_m2:.0f} m2\n"
            f"- Leiligheter: {option.unit_count} ({json.dumps(option.mix_counts, ensure_ascii=False)})\n"
            f"- Parkering: {option.parking_spaces} plasser\n"
            f"- Vinterskygge kl 12: ca. {option.winter_noon_shadow_m:.0f} m\n"
            f"- Skuldersesong soltimer: ca. {option.estimated_equinox_sun_hours:.1f} timer"
        )
        for note in option.notes:
            lines.append(f"- {note}")
    lines.append("")
    lines.append("# 9. RISIKO OG AVKLARINGSPUNKTER")
    lines.append("- Verifiser reguleringsbestemmelser, kote, gesims, parkeringskrav og uteoppholdsareal mot faktisk plan.")
    lines.append("- Legg inn reell tomtepolygon og nabohoyder for neste trinn dersom dere vil ha presis sol/skygge.")
    lines.append("- Koble gjerne modulen videre til faktiske planlosningsregler eller BIM for a ga fra volum til plan.")
    lines.append("")
    lines.append("# 10. ANBEFALING / NESTE STEG")
    lines.append(
        f"Start videre bearbeiding med {best.name}. Neste steg er a lage presis tomtepolygon, justere kjerner og trapper, "
        f"og kontrollere sol/skygge mot naboer og uteareal i et mer detaljert analyseoppsett."
    )
    return "\n".join(lines)


# --- 5. PDF ---
class BuiltlyProPDF(FPDF):
    def header(self) -> None:
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: ARK-002"), 0, 1, "R")
            self.set_draw_color(200, 200, 200)
            self.line(25, 25, 185, 25)
            self.set_y(30)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f"UTKAST - KREVER FAGLIG KONTROLL | Side {self.page_no()}"), 0, 0, "C")

    def check_space(self, height: float) -> None:
        if self.get_y() + height > 270:
            self.add_page()
            self.set_margins(25, 25, 25)
            self.set_x(25)


def add_pdf_table(pdf: BuiltlyProPDF, headers: List[str], rows: List[List[str]], widths: List[float]) -> None:
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(232, 239, 247)
    for idx, header in enumerate(headers):
        pdf.cell(widths[idx], 8, clean_pdf_text(header), 1, 0, "C", fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for row in rows:
        pdf.check_space(8)
        for idx, value in enumerate(row):
            pdf.cell(widths[idx], 8, clean_pdf_text(value), 1, 0, "C")
        pdf.ln()
    pdf.ln(4)


def create_full_report_pdf(
    name: str,
    client: str,
    land: str,
    report_text: str,
    options: List[OptionResult],
    option_images: List[Image.Image],
    visual_attachments: List[Image.Image],
) -> bytes:
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)

    pdf.add_page()
    if os.path.exists("logo.png"):
        pdf.image("logo.png", x=25, y=20, w=50)

    pdf.set_y(95)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(26, 43, 72)
    pdf.multi_cell(0, 12, clean_pdf_text("MULIGHETSSTUDIE OG TOMTEANALYSE (ARK)"), 0, "L")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 16)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(0, 10, clean_pdf_text(f"KONSEPTVURDERING: {name.upper()}"), 0, "L")
    pdf.ln(25)

    for label, value in [
        ("OPPDRAGSGIVER:", client or "Ukjent"),
        ("DATO:", datetime.now().strftime("%d.%m.%Y")),
        ("UTARBEIDET AV:", "Builtly ARK Motor + AI"),
        ("REGELVERK:", land),
    ]:
        pdf.set_x(25)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(50, 8, clean_pdf_text(label), 0, 0)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 8, clean_pdf_text(value), 0, 1)

    if options:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 12, clean_pdf_text("NOKKELTALL FRA MOTOR"), 0, 1)
        pdf.ln(2)
        rows = []
        for option in options:
            rows.append(
                [
                    option.name.replace("Alt ", ""),
                    option.typology,
                    f"{option.gross_bta_m2:.0f}",
                    f"{option.saleable_area_m2:.0f}",
                    str(option.unit_count),
                    f"{option.solar_score:.0f}",
                    f"{option.score:.0f}",
                ]
            )
        add_pdf_table(
            pdf,
            headers=["Alt", "Typologi", "BTA", "Salgb.", "Enheter", "Sol", "Score"],
            rows=rows,
            widths=[22, 28, 22, 22, 20, 18, 18],
        )

    if option_images:
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 12, clean_pdf_text("VOLUMSKISSER"), 0, 1)
        pdf.ln(2)
        for image in option_images:
            pdf.check_space(88)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                image.convert("RGB").save(tmp.name, format="JPEG", quality=88)
                pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                pdf.ln(86)

    pdf.add_page()
    for raw_line in report_text.split("\n"):
        line = raw_line.strip()
        if not line:
            pdf.ln(4)
            continue

        if line.startswith("# ") or re.match(r"^\d+\.\s[A-Z]", line):
            pdf.check_space(30)
            pdf.ln(6)
            pdf.set_x(25)
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace("#", "").strip()))
            pdf.ln(1)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(0, 0, 0)
        elif line.startswith("##"):
            pdf.check_space(20)
            pdf.ln(4)
            pdf.set_x(25)
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace("#", "").strip()))
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(0, 0, 0)
        else:
            pdf.set_x(25)
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(150, 5, ironclad_text_formatter(line))

    if visual_attachments:
        pdf.add_page()
        pdf.set_x(25)
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 12, clean_pdf_text("VEDLEGG: KART OG REFERANSER"), 0, 1)
        pdf.ln(2)
        for idx, image in enumerate(visual_attachments, start=1):
            if idx > 1:
                pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                image.convert("RGB").save(tmp.name, format="JPEG", quality=86)
                ratio_h = 160 * (image.height / max(image.width, 1))
                if ratio_h > 230:
                    ratio_h = 230
                    ratio_w = ratio_h * (image.width / max(image.height, 1))
                    pdf.image(tmp.name, x=105 - (ratio_w / 2), y=pdf.get_y(), w=ratio_w)
                else:
                    pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                pdf.set_y(pdf.get_y() + ratio_h + 4)
                pdf.set_x(25)
                pdf.set_font("Helvetica", "I", 9)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 8, clean_pdf_text(f"Figur V-{idx}: visuelt grunnlag brukt i analysen."), 0, 1)

    return bytes(pdf.output(dest="S"))


# --- 6. STYLING ---
st.markdown(
    """
<style>
    :root {
        --bg: #06111a;
        --panel: rgba(10, 22, 35, 0.78);
        --stroke: rgba(120, 145, 170, 0.18);
        --text: #f5f7fb;
        --muted: #9fb0c3;
        --soft: #c8d3df;
        --accent: #38bdf8;
        --radius-lg: 16px;
        --radius-xl: 24px;
    }
    html, body, [class*="css"] {
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
    }
    .stApp {
        background-color: var(--bg) !important;
        color: var(--text);
    }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container {
        max-width: 1320px !important;
        padding-top: 1.5rem !important;
        padding-bottom: 4rem !important;
    }
    .brand-logo {
        height: 65px;
        filter: drop-shadow(0 0 18px rgba(120,220,225,0.08));
    }
    button[kind="primary"] {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important;
        border: none !important;
        font-weight: 750 !important;
        border-radius: 12px !important;
        padding: 12px 24px !important;
        font-size: 1.05rem !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important;
    }
    button[kind="secondary"] {
        background-color: rgba(255,255,255,0.05) !important;
        color: #f8fafc !important;
        border: 1px solid rgba(120,145,170,0.3) !important;
        border-radius: 12px !important;
        font-weight: 650 !important;
        padding: 10px 24px !important;
        transition: all 0.2s;
    }
    button[kind="secondary"]:hover {
        background-color: rgba(56,194,201,0.1) !important;
        border-color: var(--accent) !important;
        color: var(--accent) !important;
        transform: translateY(-2px) !important;
    }
    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div {
        background-color: #0d1824 !important;
        border: 1px solid rgba(120, 145, 170, 0.4) !important;
        border-radius: 8px !important;
    }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, div[data-baseweb="select"] * {
        background-color: transparent !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        border: none !important;
        box-shadow: none !important;
    }
    div[data-baseweb="base-input"]:focus-within, div[data-baseweb="select"] > div:focus-within, .stTextArea > div > div > div:focus-within {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 1px rgba(56, 194, 201, 0.5) !important;
    }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label {
        color: #c8d3df !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        margin-bottom: 4px !important;
    }
    div[data-testid="stExpander"] {
        border: 1px solid rgba(120,145,170,0.2) !important;
        margin-bottom: 1rem !important;
        border-radius: 12px !important;
    }
    div[data-testid="stExpander"] details, div[data-testid="stExpander"] details summary {
        background-color: #0c1520 !important;
        color: #f5f7fb !important;
        border-radius: 12px !important;
    }
    [data-testid="stFileUploaderDropzone"] {
        background-color: #0d1824 !important;
        border: 1px dashed rgba(120, 145, 170, 0.6) !important;
        border-radius: 12px !important;
        padding: 2rem !important;
    }
    [data-testid="stAlert"] {
        background-color: rgba(56, 194, 201, 0.05) !important;
        border: 1px solid rgba(56, 194, 201, 0.2) !important;
        border-radius: 12px !important;
    }
    [data-testid="stAlert"] * {
        color: #f5f7fb !important;
    }
    .kpi-card {
        padding: 1rem 1.2rem;
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(120,145,170,0.2);
        border-radius: 16px;
        margin-bottom: 1rem;
    }
    .metric-title {
        color: #9fb0c3;
        font-size: 0.85rem;
        margin-bottom: 0.2rem;
    }
    .metric-value {
        color: #f5f7fb;
        font-size: 1.5rem;
        font-weight: 700;
    }
</style>
""",
    unsafe_allow_html=True,
)


# --- 7. SESSION STATE ---
DB_DIR = Path("qa_database")
SSOT_FILE = DB_DIR / "ssot.json"
IMG_DIR = DB_DIR / "project_images"

if "project_data" not in st.session_state:
    st.session_state.project_data = {
        "p_name": "",
        "c_name": "",
        "p_desc": "",
        "adresse": "",
        "kommune": "",
        "gnr": "",
        "bnr": "",
        "b_type": "Bolig",
        "etasjer": 4,
        "bta": 0,
        "land": "Norge",
    }
if "ark_kart" not in st.session_state:
    st.session_state.ark_kart = None

if st.session_state.project_data.get("p_name") == "" and SSOT_FILE.exists():
    with open(SSOT_FILE, "r", encoding="utf-8") as f:
        st.session_state.project_data = json.load(f)

if st.session_state.project_data.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_html = (
        f'<img src="{logo_data_uri()}" class="brand-logo">'
        if logo_data_uri()
        else '<h2 style="margin:0; color:white;">Builtly</h2>'
    )
    render_html(f"<div style='margin-bottom:2rem;'>{logo_html}</div>")
    st.warning("Du ma sette opp prosjektdata for du kan bruke denne modulen.")
    if find_page("Project"):
        if st.button("Ga til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()

pd_state = st.session_state.project_data


# --- 8. HEADER ---
top_l, top_r = st.columns([4, 1])
with top_l:
    logo_html = (
        f'<img src="{logo_data_uri()}" class="brand-logo">'
        if logo_data_uri()
        else '<h2 style="margin:0; color:white;">Builtly</h2>'
    )
    render_html(logo_html)
with top_r:
    st.markdown("<div style='margin-top:0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("Tilbake til SSOT", use_container_width=True, type="secondary"):
        st.switch_page(find_page("Project"))

st.markdown(
    "<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>",
    unsafe_allow_html=True,
)
st.markdown(
    "<h1 style='font-size: 2.5rem; margin-bottom: 0;'>ARK - Mulighetsstudie / Tomtemotor</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='color: var(--muted); font-size: 1.1rem; margin-bottom: 1.5rem;'>"
    "Oppgradert modul med faktisk feasibility-motor: volumalternativer, areal, leilighetsmiks og indikativ sol/skygge."
    "</p>",
    unsafe_allow_html=True,
)

if llm_available:
    st.success("AI-tekst er tilgjengelig. Tallsiden beregnes alltid deterministisk først.")
else:
    st.info("AI-tekst er ikke tilgjengelig akkurat na. Modulen kjører fortsatt hele feasibility-motoren deterministisk.")


# --- 9. INPUT UI ---
with st.expander("1. Prosjekt og lokasjon (SSOT)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state.get("p_name"), disabled=True)
    b_type = c2.text_input("Formaal / bygningstype", value=pd_state.get("b_type", "Bolig"), disabled=True)
    adresse_vis = f"{pd_state.get('adresse', '')}, {pd_state.get('kommune', '')}".strip(", ")
    adresse = st.text_input("Adresse", value=adresse_vis, disabled=True)
    c3, c4, c5 = st.columns(3)
    c3.text_input("Kunde", value=pd_state.get("c_name", ""), disabled=True)
    c4.number_input("Onsket BTA fra prosjektdata", value=int(pd_state.get("bta", 0)), disabled=True)
    c5.text_input("Land", value=pd_state.get("land", "Norge"), disabled=True)

with st.expander("2. Tomtegeometri og regulering", expanded=True):
    st.info("Denne delen er ny: her legger dere inn faktisk tomtestorrelse, byggegrenser og reguleringsrammer som grunnlag for volumstudien.")
    regulation_text = st.text_area(
        "Fritekst fra reguleringsplan (valgfritt, motoren henter ut BYA/BRA/hoyde hvis den finner noe)",
        placeholder="Lim inn planbestemmelser, f.eks. %-BYA 35, maks gesimshoyde 12 m, 4 etasjer ...",
        height=110,
    )
    parsed = parse_regulation_hints(regulation_text)
    if parsed:
        st.caption(f"Tolket fra tekst: {parsed}")

    d1, d2, d3 = st.columns(3)
    default_site_area = max(1500.0, float(pd_state.get("bta", 0)) * 1.25) if pd_state.get("bta", 0) else 2500.0
    site_area_m2 = d1.number_input("Tomteareal (m2)", min_value=100.0, value=float(default_site_area), step=50.0)
    site_width_m = d2.number_input("Tomtebredde (m)", min_value=10.0, value=45.0, step=1.0)
    site_depth_m = d3.number_input("Tomtedybde (m)", min_value=10.0, value=55.0, step=1.0)

    s1, s2, s3 = st.columns(3)
    front_setback_m = s1.number_input("Byggegrense mot gate / front (m)", min_value=0.0, value=4.0, step=0.5)
    rear_setback_m = s2.number_input("Bakre byggegrense (m)", min_value=0.0, value=4.0, step=0.5)
    side_setback_m = s3.number_input("Sideavstand (m)", min_value=0.0, value=4.0, step=0.5)

    r1, r2, r3, r4 = st.columns(4)
    max_bya_pct = r1.number_input("Maks BYA (%)", min_value=1.0, max_value=100.0, value=float(parsed.get("max_bya_pct", 35.0)), step=1.0)
    max_bra_m2 = r2.number_input("Maks BRA (m2, 0 = ikke satt)", min_value=0.0, value=float(parsed.get("max_bra_m2", 0.0)), step=50.0)
    max_floors = r3.number_input("Maks etasjer", min_value=1, max_value=30, value=int(parsed.get("max_floors", max(3, int(pd_state.get("etasjer", 4))))), step=1)
    max_height_m = r4.number_input("Maks hoyde (m)", min_value=3.0, value=float(parsed.get("max_height_m", max(10.0, float(pd_state.get("etasjer", 4)) * 3.2))), step=0.5)

with st.expander("3. Produktforutsetninger og leilighetsmiks", expanded=True):
    st.info("Her styrer dere hvor aggressivt motoren skal sikte mot volum, effektivitet og miks.")
    a1, a2, a3, a4 = st.columns(4)
    desired_bta_m2 = a1.number_input(
        "Onsket BTA i studien (m2)",
        min_value=100.0,
        value=float(pd_state.get("bta", 0) or 2500.0),
        step=50.0,
    )
    efficiency_ratio = a2.number_input("Salgbarhetsfaktor", min_value=0.55, max_value=0.9, value=0.78, step=0.01)
    floor_to_floor_m = a3.number_input("Etasjehoyde brutto (m)", min_value=2.8, max_value=5.5, value=3.2, step=0.1)
    latitude_manual = a4.number_input("Breddegrad for solanalyse", min_value=45.0, max_value=72.0, value=59.91, step=0.01)

    p1, p2, p3 = st.columns(3)
    parking_ratio_per_unit = p1.number_input("Parkering pr. bolig", min_value=0.0, max_value=3.0, value=0.8, step=0.05)
    parking_area_per_space_m2 = p2.number_input("Areal pr. p-plass (m2)", min_value=15.0, max_value=50.0, value=28.0, step=1.0)
    north_rotation_deg = p3.number_input("Nordretning i modell (grader)", min_value=0.0, max_value=359.0, value=0.0, step=1.0)

    st.markdown("##### Leilighetsmiks")
    mcols = st.columns(4)
    mix_inputs = []
    defaults = [
        ("1-rom", 15.0, 38.0),
        ("2-rom", 35.0, 52.0),
        ("3-rom", 35.0, 72.0),
        ("4-rom+", 15.0, 95.0),
    ]
    for idx, (label, share_default, size_default) in enumerate(defaults):
        with mcols[idx]:
            st.markdown(f"**{label}**")
            share = st.number_input(f"Andel {label} (%)", min_value=0.0, max_value=100.0, value=share_default, step=1.0, key=f"share_{idx}")
            avg_size = st.number_input(f"Gj.sn. storrelse {label} (m2)", min_value=20.0, max_value=180.0, value=size_default, step=1.0, key=f"size_{idx}")
            mix_inputs.append(MixSpec(label, share, avg_size))
    share_sum = sum(item.share_pct for item in mix_inputs)
    if abs(share_sum - 100.0) > 0.01:
        st.warning(f"Andelene summerer til {share_sum:.1f}%. Motoren normaliserer dette automatisk.")

with st.expander("4. Visuelt grunnlag (kart og skisser)", expanded=True):
    saved_images: List[Image.Image] = []
    if IMG_DIR.exists():
        for path in sorted(IMG_DIR.glob("*.jpg")):
            try:
                saved_images.append(Image.open(path).convert("RGB"))
            except Exception:
                continue

    if saved_images:
        st.success(f"Fant {len(saved_images)} tegninger/skisser fra Project Setup.")
    else:
        st.warning("Ingen felles tegninger funnet. Kart eller opplastede skisser anbefales for bedre kontekst.")

    c_map, c_upload = st.columns(2)
    with c_map:
        if st.button("Hent kart automatisk for tomten", type="secondary"):
            with st.spinner("Henter kart ..."):
                img, source = fetch_map_image(
                    pd_state.get("adresse", ""),
                    pd_state.get("kommune", ""),
                    pd_state.get("gnr", ""),
                    pd_state.get("bnr", ""),
                    google_key or "",
                )
                if img is not None:
                    st.session_state.ark_kart = img
                    st.success(f"Kart hentet fra {source}")
                else:
                    st.error(source)
        if st.session_state.ark_kart is not None:
            st.image(st.session_state.ark_kart, caption="Situasjonskart", use_container_width=True)

    with c_upload:
        uploaded_files = st.file_uploader(
            "Last opp kart, situasjonsplan, PDF eller skisser",
            accept_multiple_files=True,
            type=["png", "jpg", "jpeg", "pdf"],
        )

with st.expander("5. Hva modulen faktisk gjor na", expanded=False):
    st.markdown(
        """
- Lager **3 volumalternativer** (lamell, punkthus, tun/U-form).
- Regner **fotavtrykk, BTA, salgbarhetsareal, boligantall og leilighetsmiks**.
- Estimerer **parkeringstrykk** mot uteareal.
- Lager **indikativ sol/skygge** ut fra breddegrad, hoyde og aapne flater.
- Bruker eventuelt AI bare til a forklare funnene. Tallene kommer fra motoren.
"""
    )


# --- 10. KJOR ANALYSE ---
run_analysis = st.button("Kjor tomtestudie / volumstudie", type="primary", use_container_width=True)

if run_analysis:
    images_for_context = list(saved_images)
    if st.session_state.ark_kart is not None:
        images_for_context.append(st.session_state.ark_kart)
    images_for_context.extend(load_uploaded_visuals(uploaded_files))

    lat_geocoded, lon_geocoded, geo_source = fetch_lat_lon(pd_state.get("adresse", ""), pd_state.get("kommune", ""))
    latitude_deg = lat_geocoded if lat_geocoded is not None else latitude_manual

    site = SiteInputs(
        site_area_m2=site_area_m2,
        site_width_m=site_width_m,
        site_depth_m=site_depth_m,
        front_setback_m=front_setback_m,
        rear_setback_m=rear_setback_m,
        side_setback_m=side_setback_m,
        max_bya_pct=max_bya_pct,
        max_bra_m2=max_bra_m2,
        desired_bta_m2=desired_bta_m2,
        max_floors=int(max_floors),
        max_height_m=max_height_m,
        floor_to_floor_m=floor_to_floor_m,
        efficiency_ratio=efficiency_ratio,
        parking_ratio_per_unit=parking_ratio_per_unit,
        parking_area_per_space_m2=parking_area_per_space_m2,
        latitude_deg=latitude_deg,
        north_rotation_deg=north_rotation_deg,
    )

    with st.spinner("Regner volumalternativer, areal og indikativ sol/skygge ..."):
        options = generate_options(site, mix_inputs)

    if not options:
        st.error("Klarte ikke a generere alternativer. Kontroller tomtedimensjoner, byggegrenser og BYA.")
        st.stop()

    option_images = [render_plan_diagram(site, option) for option in options]
    deterministic_report = build_deterministic_report(site, options, parsed, has_visual_input=bool(images_for_context))
    final_report_text = deterministic_report

    if llm_available:
        model_name = pick_model_name()
        if model_name:
            try:
                model = genai.GenerativeModel(model_name)
                analysis_payload = {
                    "site": asdict(site),
                    "alternatives": [asdict(option) | {"geometry": None} for option in options],
                    "parsed_regulation_hints": parsed,
                    "visual_input_count": len(images_for_context),
                    "geocoding_source": geo_source,
                }
                prompt = f"""
Du er senior arkitekt og utviklingsradgiver. Du far et ferdig, deterministisk analysegrunnlag i JSON.
Du skal forklare, prioritere og skrive rapporten for bruk i Builtly. Du MA IKKE endre tallene.
Hvis det finnes svakheter i grunnlaget, skal du si det tydelig.

JSON-GRUNNLAG:
{json.dumps(analysis_payload, ensure_ascii=False, indent=2)}

KRAV:
- Bruk nøyaktig disse overskriftene:
1. OPPSUMMERING
2. GRUNNLAG
3. VIKTIGSTE FORUTSETNINGER
4. TOMT OG KONTEKST
5. REGULERINGSMESSIGE FORHOLD
6. ARKITEKTONISK VURDERING
7. MULIGE UTVIKLINGSGREP
8. ALTERNATIVER
9. RISIKO OG AVKLARINGSPUNKTER
10. ANBEFALING / NESTE STEG

- Tallene i JSON er kilde til sannhet.
- Sol/skygge skal omtales som indikativ.
- Leilighetsmiks skal beskrives som kapasitetsestimat.
- Ikke skriv om noe du ikke vet.
"""
                parts = [prompt] + images_for_context[:6]
                response = model.generate_content(parts)
                if getattr(response, "text", "").strip():
                    final_report_text = response.text.strip()
            except Exception:
                final_report_text = deterministic_report

    pdf_bytes = create_full_report_pdf(
        name=p_name,
        client=pd_state.get("c_name", "Ukjent"),
        land=pd_state.get("land", "Norge"),
        report_text=final_report_text,
        options=options,
        option_images=option_images,
        visual_attachments=images_for_context,
    )

    if "pending_reviews" not in st.session_state:
        st.session_state.pending_reviews = {}
    if "review_counter" not in st.session_state:
        st.session_state.review_counter = 1

    doc_id = f"PRJ-{datetime.now().strftime('%y')}-ARK{st.session_state.review_counter:03d}"
    st.session_state.review_counter += 1

    best = options[0]
    st.session_state.pending_reviews[doc_id] = {
        "title": pd_state.get("p_name", "Nytt Prosjekt"),
        "module": "ARK (Mulighetsstudie v2)",
        "drafter": "Builtly AI + Feasibility Engine",
        "reviewer": "Senior Arkitekt",
        "status": "Pending Lead Architect Review",
        "class": "badge-pending",
        "pdf_bytes": pdf_bytes,
    }

    st.session_state.analysis_results = {
        "site": asdict(site),
        "options": [asdict(option) for option in options],
        "report_text": final_report_text,
        "geo_source": geo_source,
        "option_images": option_images,
    }
    st.session_state.generated_ark_pdf = pdf_bytes
    st.session_state.generated_ark_filename = f"Builtly_ARK_{p_name}_v2.pdf"
    st.rerun()


# --- 11. RENDER RESULTATER ---
if "analysis_results" in st.session_state:
    result = st.session_state.analysis_results
    options = []
    for option_data in result["options"]:
        options.append(OptionResult(**option_data))

    st.success("Mulighetsstudie er generert med volumalternativer, leilighetsmiks og indikativ sol/skygge.")
    best = options[0]

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Anbefalt alternativ</div><div class='metric-value'>{}</div></div>".format(best.typology), unsafe_allow_html=True)
    with k2:
        st.markdown("<div class='kpi-card'><div class='metric-title'>BTA</div><div class='metric-value'>{:.0f} m2</div></div>".format(best.gross_bta_m2), unsafe_allow_html=True)
    with k3:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Boliger</div><div class='metric-value'>{}</div></div>".format(best.unit_count), unsafe_allow_html=True)
    with k4:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Solscore</div><div class='metric-value'>{:.0f}/100</div></div>".format(best.solar_score), unsafe_allow_html=True)

    st.markdown("### Alternativsammenligning")
    comparison_df = pd.DataFrame(
        [
            {
                "Alternativ": option.name,
                "Typologi": option.typology,
                "Etasjer": option.floors,
                "Fotavtrykk m2": option.footprint_area_m2,
                "BTA m2": option.gross_bta_m2,
                "Salgbart m2": option.saleable_area_m2,
                "Boliger": option.unit_count,
                "Parkering": option.parking_spaces,
                "Solscore": option.solar_score,
                "Skuldersesong soltimer": option.estimated_equinox_sun_hours,
                "Vinter skygge kl12 m": option.winter_noon_shadow_m,
                "Open space %": round(option.open_space_ratio * 100.0, 1),
                "Score": option.score,
            }
            for option in options
        ]
    )
    st.dataframe(comparison_df, use_container_width=True, hide_index=True)

    st.markdown("### Volumskisser")
    cols = st.columns(len(options))
    for col, option, image in zip(cols, options, result["option_images"]):
        with col:
            st.image(image, caption=f"{option.name} - {option.typology}", use_container_width=True)
            st.caption(
                f"BTA {option.gross_bta_m2:.0f} m2 | {option.unit_count} boliger | "
                f"solscore {option.solar_score:.0f}/100"
            )

    st.markdown("### Leilighetsmiks per alternativ")
    mix_rows = []
    for option in options:
        row = {"Alternativ": option.name}
        row.update(option.mix_counts)
        row["Totalt"] = option.unit_count
        mix_rows.append(row)
    mix_df = pd.DataFrame(mix_rows).fillna(0)
    st.dataframe(mix_df, use_container_width=True, hide_index=True)

    st.markdown("### Sol/skyggeindikatorer")
    solar_df = pd.DataFrame(
        {
            option.name: {
                "Skuldersesong soltimer": option.estimated_equinox_sun_hours,
                "Vinter soltimer": option.estimated_winter_sun_hours,
                "Vinterskygge kl 12 (m)": option.winter_noon_shadow_m,
                "Sommerskygge kl 15 (m)": option.summer_afternoon_shadow_m,
            }
            for option in options
        }
    ).T
    st.dataframe(solar_df, use_container_width=True)

    st.markdown("### Rapport")
    st.markdown(result["report_text"])

    st.markdown("### Nedlasting og QA")
    cdl, cqa = st.columns(2)
    with cdl:
        st.download_button(
            "Last ned mulighetsstudie (PDF)",
            st.session_state.generated_ark_pdf,
            st.session_state.generated_ark_filename,
            type="primary",
            use_container_width=True,
        )
    with cqa:
        if find_page("Review"):
            if st.button("Ga til QA for godkjenning", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))
