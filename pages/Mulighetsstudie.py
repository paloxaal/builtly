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
import streamlit.components.v1 as components
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont
from shapely import affinity
from shapely.geometry import MultiPolygon, Point, Polygon, box, shape
from shapely.ops import unary_union

try:
    from pyproj import CRS, Transformer
    HAS_PYPROJ = True
except Exception:
    HAS_PYPROJ = False

    class CRS:  # type: ignore[override]
        @staticmethod
        def from_epsg(_: int) -> None:
            return None

        def to_string(self) -> str:
            return ""

    class Transformer:  # type: ignore[override]
        @staticmethod
        def from_crs(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("pyproj er ikke installert i miljoet.")

try:
    from rasterio.io import MemoryFile
    HAS_RASTERIO = True
except Exception:
    HAS_RASTERIO = False
    MemoryFile = None  # type: ignore[assignment]

try:
    import google.generativeai as genai
except Exception:
    genai = None

try:
    import fitz
except ImportError:
    fitz = None

try:
    from geodata_client import GeodataOnlineClient, geodata_buildings_to_neighbors
    HAS_GEODATA_ONLINE = True
except ImportError:
    HAS_GEODATA_ONLINE = False

try:
    from site_intelligence import (
        apply_site_intelligence_to_options,
        build_site_intelligence_bundle,
        build_site_intelligence_markdown,
    )
    HAS_SITE_INTELLIGENCE = True
except ImportError:
    HAS_SITE_INTELLIGENCE = False

try:
    import ai_site_planner
    HAS_AI_PLANNER = bool(ai_site_planner.is_available())
except ImportError:
    HAS_AI_PLANNER = False
    ai_site_planner = None  # type: ignore[assignment]


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

gdo = GeodataOnlineClient() if HAS_GEODATA_ONLINE else None
geodata_token_ok = False
if gdo is not None and gdo.is_available():
    try:
        gdo.get_token()
        geodata_token_ok = True
    except Exception:
        geodata_token_ok = False


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


def geo_runtime_notes() -> List[str]:
    notes: List[str] = []
    if not HAS_PYPROJ:
        notes.append("pyproj mangler: GeoJSON i lon/lat og OSM-nabohenting blir deaktivert eller mindre presist.")
    if not HAS_RASTERIO:
        notes.append("rasterio mangler: GeoTIFF/ASC terreng er deaktivert, men CSV/TXT med x,y,z virker fortsatt.")
    return notes


# --- 3. GEODATA / KART (SKUDDSIKKER VERSJON) ---

def get_kommunenummer(input_str: str) -> Optional[str]:
    """Oversatt bynavn til riktig 4-sifret kommunenummer fra Kartverket API."""
    s = str(input_str).strip()
    if s.isdigit() and len(s) >= 3:
        return s.zfill(4)
    try:
        resp = requests.get("https://ws.geonorge.no/kommuneinfo/v1/kommuner", timeout=5)
        if resp.status_code == 200:
            for k in resp.json():
                if k.get("kommunenavn", "").lower() == s.lower():
                    return k.get("kommunenummer")
    except Exception:
        pass
    return None


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
def fetch_map_image(adresse: str, kommune: str, gnr: str, bnr: str, api_key: str, bounds: Optional[Tuple[float, float, float, float]] = None, _gdo_client: Any = None) -> Tuple[Optional[Image.Image], str]:
    # 0. Geodata Online ortofoto (foerstevalg)
    if bounds is not None and _gdo_client is not None:
        try:
            img, source = _gdo_client.fetch_ortofoto(bbox=bounds, buffer_m=80.0, width=1200, height=1200)
            if img:
                return img, source
        except Exception:
            pass

    # 1. HØYESTE PRIORITET: Bruk eksakte koordinater hvis tomt er hentet!
    if bounds is not None:
        minx, miny, maxx, maxy = bounds
        # Legg på 80 meter margin rundt tomten for å se naboskapet
        url_orto = (
            "https://wms.geonorge.no/skwms1/wms.nib"
            "?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto"
            f"&styles=&srs=EPSG:25833&bbox={minx-80},{miny-80},{maxx+80},{maxy+80}"
            "&width=1000&height=1000&format=image/png"
        )
        try:
            r1 = requests.get(url_orto, timeout=12)
            if r1.status_code == 200 and len(r1.content) > 5000:
                return Image.open(io.BytesIO(r1.content)).convert("RGB"), "Kartverket Ortofoto (via Eksakt Tomtegrense)"
        except Exception:
            pass

    # 2. MELLOMPRIORITET: Hvis vi ikke har tomt, prøv vanlig adressesøk
    nord, ost = None, None
    adr_clean = adresse.replace(",", "").strip() if adresse else ""
    kom_clean = kommune.replace(",", "").strip() if kommune else ""

    if adr_clean and kom_clean:
        query = f"{adr_clean} {kom_clean}"
        safe_query = urllib.parse.quote(query)
        url = f"https://ws.geonorge.no/adresser/v1/sok?sok={safe_query}&fuzzy=true&utkoordsys=25833&treffPerSide=1"
        try:
            resp = requests.get(url, timeout=6)
            if resp.status_code == 200 and resp.json().get("adresser"):
                hit = resp.json()["adresser"][0]
                nord = hit.get("representasjonspunkt", {}).get("nord")
                ost = hit.get("representasjonspunkt", {}).get("øst")
        except Exception:
            pass

    if nord and ost:
        min_x, max_x = float(ost) - 100, float(ost) + 100
        min_y, max_y = float(nord) - 100, float(nord) + 100
        url_orto = (
            "https://wms.geonorge.no/skwms1/wms.nib"
            "?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto"
            f"&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}"
            "&width=900&height=900&format=image/png"
        )
        try:
            r1 = requests.get(url_orto, timeout=8)
            if r1.status_code == 200 and len(r1.content) > 5000:
                return Image.open(io.BytesIO(r1.content)).convert("RGB"), "Kartverket Adressesøk"
        except Exception:
            pass

    return None, "Kunne ikke hente kart. Tips: Sørg for å trykke 'Søk opp og lagre tomt' i trinn 2B først, da vet systemet nøyaktig hvor det skal zoome!"


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


# --- 4. GEOSPATIAL HJELPERE ---
DEFAULT_FLOOR_HEIGHT_M = 3.2

def extract_geojson_features(obj: Any) -> List[Dict[str, Any]]:
    if not isinstance(obj, dict):
        return []
    gtype = obj.get("type")
    if gtype == "FeatureCollection":
        return [feature for feature in obj.get("features", []) if isinstance(feature, dict)]
    if gtype == "Feature":
        return [obj]
    if gtype in {"Polygon", "MultiPolygon"}:
        return [{"type": "Feature", "geometry": obj, "properties": {}}]
    return []

def hent_tomt_fra_geonorge(kommune_input: str, gnr_bnr_liste: List[Tuple[str, str]]) -> Tuple[Optional[Polygon], str]:
    """SKUDDSIKKER VERSJON: Henter eiendomsgrenser fra Kartverket."""
    knr = get_kommunenummer(kommune_input)
    if not knr:
        return None, f"Gjenkjente ikke kommunen '{kommune_input}'. Skriv f.eks. 'Oslo' eller '0301'."
        
    polygoner = []
    feil = []
    
    for gnr, bnr in gnr_bnr_liste:
        gnr_clean = str(gnr).strip()
        bnr_clean = str(bnr).strip()
        
        # Kartverkets WFS servere (fallback innebygd)
        services = [
            ("https://wfs.geonorge.no/skwms1/wfs.matrikkelen-teig", "matrikkelen-teig:Teig"),
            ("https://wfs.geonorge.no/skwms1/wfs.matrikkelkart", "matrikkelkart:Teig")
        ]
        
        # VIKTIG: Ingen fnutter (') rundt tallene i CQL! GeoServer krasjer hvis Gnr (int) har fnutter.
        cql = f"kommunenummer='{knr}' AND gardsnummer={gnr_clean} AND bruksnummer={bnr_clean}"
        
        success_for_this_parcel = False
        last_error = ""

        for url, layer in services:
            if success_for_this_parcel: break
            
            params = {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typenames": layer,
                "srsName": "EPSG:25833",
                "outputFormat": "application/json",
                "cql_filter": cql
            }
            
            try:
                resp = requests.get(url, params=params, timeout=12)
                # Hvis application/json feiler, prøv bare "json"
                if resp.status_code != 200:
                    params["outputFormat"] = "json"
                    resp = requests.get(url, params=params, timeout=12)

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        features = extract_geojson_features(data)
                        if features:
                            for feature in features:
                                geom = shape(feature["geometry"])
                                poly = largest_polygon(geom)
                                if poly:
                                    polygoner.append(poly)
                            success_for_this_parcel = True
                            break
                        else:
                            last_error = "Ingen polygon funnet på dette Gnr/Bnr i matrikkelen."
                    except json.JSONDecodeError:
                        last_error = "Server returnerte ikke gyldig JSON."
                else:
                    last_error = f"API-feil (Kode: {resp.status_code})"
            except Exception as e:
                last_error = f"Nettverksfeil: {str(e)[:30]}"
                
        if not success_for_this_parcel:
            feil.append(f"{gnr}/{bnr} ({last_error})")
            
    if not polygoner:
        return None, "Feilet: " + " | ".join(feil)
        
    try:
        samlet = unary_union(polygoner)
        msg = f"Suksess! Hentet tomt i {knr}: " + ", ".join([f"{g}/{b}" for g,b in gnr_bnr_liste])
        if feil:
            msg += f" (Mangler: {', '.join(feil)})"
        return samlet, msg
    except Exception as e:
        return None, f"Feil ved sammenslåing: {e}"


def largest_polygon(geom: Any) -> Optional[Polygon]:
    if geom is None:
        return None
    if isinstance(geom, Polygon):
        return geom.buffer(0)
    if isinstance(geom, MultiPolygon):
        if not geom.geoms:
            return None
        return max((g.buffer(0) for g in geom.geoms), key=lambda g: g.area, default=None)
    try:
        if getattr(geom, "geom_type", "") == "Polygon":
            return Polygon(geom).buffer(0)
    except Exception:
        pass
    return None


def polygon_to_coords(poly: Optional[Polygon], precision: int = 2) -> List[List[float]]:
    if poly is None or poly.is_empty:
        return []
    if isinstance(poly, MultiPolygon):
        largest = max(poly.geoms, key=lambda g: g.area, default=None)
        if largest is None:
            return []
        poly = largest
    return [[round(float(x), precision), round(float(y), precision)] for x, y in list(poly.exterior.coords)]


def geometry_to_coord_groups(geom: Any, precision: int = 2) -> List[List[List[float]]]:
    if geom is None or getattr(geom, 'is_empty', True):
        return []
    if isinstance(geom, Polygon):
        return [polygon_to_coords(geom, precision=precision)]
    if isinstance(geom, MultiPolygon):
        groups: List[List[List[float]]] = []
        for part in geom.geoms:
            coords = polygon_to_coords(part, precision=precision)
            if coords:
                groups.append(coords)
        return groups
    if getattr(geom, 'geom_type', '') == 'Polygon':
        return [polygon_to_coords(geom, precision=precision)]
    return []


def flatten_coord_groups(groups: Any) -> List[List[float]]:
    flat: List[List[float]] = []
    if not groups:
        return flat
    if isinstance(groups, list) and groups and isinstance(groups[0], list) and groups[0] and isinstance(groups[0][0], (int, float)):
        return groups
    for group in groups:
        if not group:
            continue
        if isinstance(group, list) and group and isinstance(group[0], list) and group[0] and isinstance(group[0][0], (int, float)):
            flat.extend(group)
    return flat


def project_coord_groups_to_lonlat(groups: List[List[List[float]]], src_crs: str = 'EPSG:25833') -> List[List[List[float]]]:
    if not groups:
        return []
    if not HAS_PYPROJ:
        return groups
    try:
        transformer = Transformer.from_crs(CRS.from_string(src_crs), CRS.from_epsg(4326), always_xy=True)
    except Exception:
        try:
            transformer = Transformer.from_crs(25833, 4326, always_xy=True)
        except Exception:
            return groups
    projected: List[List[List[float]]] = []
    for group in groups:
        ring: List[List[float]] = []
        for x, y in group:
            lon, lat = transformer.transform(float(x), float(y))
            ring.append([round(float(lon), 7), round(float(lat), 7)])
        if ring:
            projected.append(ring)
    return projected


def split_geometry_to_polygons(geom: Any) -> List[Polygon]:
    if geom is None or getattr(geom, 'is_empty', True):
        return []
    if isinstance(geom, Polygon):
        return [geom.buffer(0)]
    if isinstance(geom, MultiPolygon):
        return [part.buffer(0) for part in geom.geoms if not part.is_empty]
    return []


def bounds_look_like_lonlat(bounds: Tuple[float, float, float, float]) -> bool:
    minx, miny, maxx, maxy = bounds
    return (
        -180.0 <= minx <= 180.0
        and -180.0 <= maxx <= 180.0
        and -90.0 <= miny <= 90.0
        and -90.0 <= maxy <= 90.0
    )


def utm_crs_from_lonlat(lon: float, lat: float) -> Optional[CRS]:
    if not HAS_PYPROJ:
        return None
    zone = int((lon + 180.0) // 6.0) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    return CRS.from_epsg(epsg)


def transform_polygon(poly: Polygon, src_crs: Optional[CRS], dst_crs: Optional[CRS]) -> Polygon:
    if poly is None or src_crs is None or dst_crs is None or src_crs == dst_crs or not HAS_PYPROJ:
        return poly
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    coords = [transformer.transform(x, y) for x, y in list(poly.exterior.coords)]
    return Polygon(coords).buffer(0)


def parse_coordinate_text(text: str) -> Optional[Polygon]:
    if not text or not text.strip():
        return None
    coords: List[Tuple[float, float]] = []
    normalized = text.replace(";", "\n")
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in re.split(r"[\s,]+", line) if part.strip()]
        if len(parts) < 2:
            continue
        x = safe_float(parts[0], None)
        y = safe_float(parts[1], None)
        if x is None or y is None:
            continue
        coords.append((float(x), float(y)))
    if len(coords) < 3:
        return None
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    poly = Polygon(coords).buffer(0)
    return largest_polygon(poly)


def normalize_polygon_to_local(poly: Polygon) -> Tuple[Optional[Polygon], Optional[CRS], Dict[str, Any]]:
    poly = largest_polygon(poly)
    if poly is None:
        return None, None, {"is_geographic": False}
    info: Dict[str, Any] = {"is_geographic": False}
    if bounds_look_like_lonlat(poly.bounds):
        centroid = poly.centroid
        info = {
            "is_geographic": True,
            "centroid_lon": float(centroid.x),
            "centroid_lat": float(centroid.y),
        }
        dst_crs = utm_crs_from_lonlat(float(centroid.x), float(centroid.y))
        if dst_crs is None:
            info["warning"] = "pyproj mangler; lon/lat-GeoJSON kan ikke transformeres til meter i denne deployen."
            return None, None, info
        info["crs"] = dst_crs.to_string()
        return transform_polygon(poly, CRS.from_epsg(4326), dst_crs), dst_crs, info
    return poly, None, info


def load_site_polygon_input(auto_polygon: Optional[Polygon], uploaded_geojson: Any, coordinate_text: str) -> Tuple[Optional[Polygon], Optional[CRS], Dict[str, Any]]:
    
    # 1. Høyeste prioritet: Tomt hentet fra Kartverket (Allerede i meter/UTM33)
    if auto_polygon is not None:
        poly_local, _, info = normalize_polygon_to_local(auto_polygon)
        crs_obj = CRS.from_epsg(25833) if HAS_PYPROJ else None
        info["source"] = st.session_state.get("auto_site_msg", "Geodata Online (Eksakt polygon)")
        if "Geodata Online" not in info["source"]:
            info["source"] = "Eksakt polygon"
        info["crs"] = "EPSG:25833"
        return poly_local, crs_obj, info

    # 2. Mellomprioritet: Opplastet GeoJSON
    if uploaded_geojson is not None:
        try:
            uploaded_geojson.seek(0)
            raw = uploaded_geojson.read()
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            obj = json.loads(raw.decode("utf-8-sig"))
            features = extract_geojson_features(obj)
            polys: List[Polygon] = []
            for feature in features:
                geom = feature.get("geometry")
                if not geom:
                    continue
                poly = largest_polygon(shape(geom))
                if poly is not None:
                    polys.append(poly)
            if polys:
                poly = max(polys, key=lambda p: p.area)
                poly_local, crs_obj, info = normalize_polygon_to_local(poly)
                info["source"] = f"GeoJSON: {getattr(uploaded_geojson, 'name', 'polygon')}"
                return poly_local, crs_obj, info
        except Exception as exc:
            return None, None, {"source": "GeoJSON", "error": str(exc)}

    # 3. Laveste prioritet: Tekstkoordinater
    poly = parse_coordinate_text(coordinate_text)
    if poly is not None:
        poly_local, crs_obj, info = normalize_polygon_to_local(poly)
        info["source"] = "Koordinatliste"
        return poly_local, crs_obj, info

    return None, None, {"source": "Manuell rektangeltomt"}


def coerce_height_from_properties(properties: Dict[str, Any], default_height_m: float = 9.0) -> float:
    if not properties:
        return default_height_m
    props = {str(k).lower(): v for k, v in properties.items()}
    direct_keys = ["height_m", "height", "hoyde", "building:height", "gesimshoyde", "max_height", "z", "elevation_m"]
    level_keys = ["building:levels", "levels", "etasjer", "floors", "stories"]
    for key in direct_keys:
        if key in props:
            value = safe_float(props.get(key), 0.0)
            if value > 0:
                return value
    for key in level_keys:
        if key in props:
            value = safe_float(props.get(key), 0.0)
            if value > 0:
                return value * DEFAULT_FLOOR_HEIGHT_M
    return default_height_m


def normalize_polygon_for_site(poly: Polygon, site_crs: Optional[CRS]) -> Tuple[Optional[Polygon], Optional[CRS]]:
    poly = largest_polygon(poly)
    if poly is None:
        return None, site_crs
    if bounds_look_like_lonlat(poly.bounds):
        if not HAS_PYPROJ:
            return None, site_crs
        centroid = poly.centroid
        src_crs = CRS.from_epsg(4326)
        dst_crs = site_crs or utm_crs_from_lonlat(float(centroid.x), float(centroid.y))
        return transform_polygon(poly, src_crs, dst_crs), dst_crs
    return poly, site_crs


def load_neighbors_from_geojson(uploaded_geojson: Any, site_polygon: Optional[Polygon], site_crs: Optional[CRS], default_height_m: float) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    neighbors: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {"source": "Ingen nabofil"}
    if uploaded_geojson is None:
        return neighbors, meta
    try:
        uploaded_geojson.seek(0)
        raw = uploaded_geojson.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        obj = json.loads(raw.decode("utf-8-sig"))
        features = extract_geojson_features(obj)
        for feature in features:
            geom = feature.get("geometry")
            if not geom:
                continue
            poly = largest_polygon(shape(geom))
            poly, site_crs = normalize_polygon_for_site(poly, site_crs)
            if poly is None:
                continue
            if site_polygon is not None and poly.distance(site_polygon) > 250.0:
                continue
            height_m = coerce_height_from_properties(feature.get("properties", {}), default_height_m=default_height_m)
            neighbors.append(
                {
                    "polygon": poly.buffer(0),
                    "height_m": float(height_m),
                    "source": "GeoJSON",
                    "distance_m": float(poly.distance(site_polygon)) if site_polygon is not None else 0.0,
                }
            )
        meta = {"source": f"GeoJSON: {getattr(uploaded_geojson, 'name', 'naboer')}", "count": len(neighbors)}
    except Exception as exc:
        meta = {"source": "GeoJSON", "error": str(exc), "count": len(neighbors)}
    return neighbors, meta


def fetch_osm_neighbors(lat: Optional[float], lon: Optional[float], site_polygon: Optional[Polygon], site_crs: Optional[CRS], radius_m: float, default_height_m: float) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if lat is None or lon is None:
        return [], {"source": "OSM", "error": "Mangler lat/lon for OSM-oppslag"}
    if not HAS_PYPROJ:
        return [], {"source": "OSM", "error": "pyproj mangler i deployen; OSM-nabohenting krever pyproj for sikker koordinattransformasjon."}
    query = (
        f'[out:json][timeout:25];'
        f'(way["building"](around:{int(radius_m)},{lat},{lon});'
        f'relation["building"](around:{int(radius_m)},{lat},{lon}););'
        'out geom tags;'
    )
    try:
        resp = requests.post(
            "https://overpass-api.de/api/interpreter",
            data=query.encode("utf-8"),
            headers={"User-Agent": "BuiltlyFeasibility/1.0"},
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        return [], {"source": "OSM", "error": str(exc)}

    neighbors: List[Dict[str, Any]] = []
    dst_crs = site_crs or utm_crs_from_lonlat(lon, lat)
    transformer = Transformer.from_crs(CRS.from_epsg(4326), dst_crs, always_xy=True)
    for element in payload.get("elements", []):
        geometry = element.get("geometry") or []
        if len(geometry) < 3:
            continue
        try:
            coords = [transformer.transform(float(node["lon"]), float(node["lat"])) for node in geometry]
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            poly = largest_polygon(Polygon(coords).buffer(0))
            if poly is None:
                continue
            if site_polygon is not None and poly.distance(site_polygon) > radius_m + 20.0:
                continue
            tags = element.get("tags", {}) or {}
            height_m = coerce_height_from_properties(tags, default_height_m=default_height_m)
            neighbors.append(
                {
                    "polygon": poly,
                    "height_m": float(height_m),
                    "source": "OSM",
                    "distance_m": float(poly.distance(site_polygon)) if site_polygon is not None else 0.0,
                }
            )
        except Exception:
            continue
    return neighbors, {"source": "OSM Overpass", "count": len(neighbors)}


def terrain_points_from_csv_bytes(data: bytes, site_crs: Optional[CRS]) -> np.ndarray:
    df = pd.read_csv(io.BytesIO(data), sep=None, engine="python")
    cols = {str(col).lower(): col for col in df.columns}

    x_col = next((cols[key] for key in ["x", "east", "easting", "ost", "utm_x", "lon", "longitude"] if key in cols), None)
    y_col = next((cols[key] for key in ["y", "north", "northing", "nord", "utm_y", "lat", "latitude"] if key in cols), None)
    z_col = next((cols[key] for key in ["z", "elev", "elevation", "kote", "height", "h"] if key in cols), None)
    if x_col is None or y_col is None or z_col is None:
        raise ValueError("Fant ikke x/y/z-kolonner i terrengfilen.")

    x = df[x_col].apply(lambda v: safe_float(v, np.nan)).to_numpy(dtype=float)
    y = df[y_col].apply(lambda v: safe_float(v, np.nan)).to_numpy(dtype=float)
    z = df[z_col].apply(lambda v: safe_float(v, np.nan)).to_numpy(dtype=float)
    mask = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    x, y, z = x[mask], y[mask], z[mask]
    if len(x) < 3:
        raise ValueError("Terrengfilen trenger minst 3 gyldige punkter.")

    if np.nanmax(np.abs(x)) <= 180 and np.nanmax(np.abs(y)) <= 90:
        if not HAS_PYPROJ:
            raise ValueError("Terreng i lon/lat krever pyproj. Last opp UTM/EPSG:25833 eller installer pyproj.")
        if site_crs is None:
            site_crs = utm_crs_from_lonlat(float(np.nanmean(x)), float(np.nanmean(y)))
        transformer = Transformer.from_crs(CRS.from_epsg(4326), site_crs, always_xy=True)
        x, y = transformer.transform(x.tolist(), y.tolist())
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
    return np.column_stack([x, y, z])


def load_terrain_input(uploaded_terrain: Any, site_polygon: Optional[Polygon], site_crs: Optional[CRS]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    if uploaded_terrain is None:
        return None, {"source": "Ingen terrengfil"}
    try:
        uploaded_terrain.seek(0)
        raw = uploaded_terrain.read()
        suffix = Path(getattr(uploaded_terrain, "name", "terrain")).suffix.lower()
        if suffix in {".csv", ".txt"}:
            points = terrain_points_from_csv_bytes(raw, site_crs)
        else:
            raise ValueError("Stotter forelopig CSV/TXT for terreng.")

        x = points[:, 0]
        y = points[:, 1]
        z = points[:, 2]
        A = np.column_stack([x, y, np.ones(len(x))])
        coeff, *_ = np.linalg.lstsq(A, z, rcond=None)
        a, b, c = [float(v) for v in coeff]
        z_pred = A @ coeff
        rmse = float(np.sqrt(np.mean((z - z_pred) ** 2)))
        terrain = {
            "a": a,
            "b": b,
            "c": c,
            "min_elev_m": float(np.min(z)),
            "max_elev_m": float(np.max(z)),
            "relief_m": float(np.max(z) - np.min(z)),
            "slope_pct": float(np.sqrt(a ** 2 + b ** 2) * 100.0),
            "grade_ew_pct": float(a * 100.0),
            "grade_ns_pct": float(b * 100.0),
            "rmse_m": rmse,
            "point_count": int(len(points)),
            "source": getattr(uploaded_terrain, "name", "terreng"),
        }
        return terrain, {"source": terrain["source"], "point_count": int(len(points))}
    except Exception as exc:
        return None, {"source": "Terreng", "error": str(exc)}


def terrain_elevation_at(x: float, y: float, terrain: Optional[Dict[str, Any]]) -> float:
    if not terrain:
        return 0.0
    return float(terrain["a"] * x + terrain["b"] * y + terrain["c"])


def terrain_slope_along_azimuth(terrain: Optional[Dict[str, Any]], azimuth_deg: float) -> float:
    if not terrain:
        return 0.0
    ux = math.sin(math.radians(azimuth_deg))
    uy = math.cos(math.radians(azimuth_deg))
    return float((terrain["a"] * ux) + (terrain["b"] * uy))


def minimum_rotated_dims(poly: Polygon) -> Tuple[float, float, float]:
    rect = poly.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)[:4]
    edges = []
    for i in range(4):
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % 4]
        dist = math.hypot(x2 - x1, y2 - y1)
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        edges.append((dist, angle))
    edges.sort(key=lambda item: item[0], reverse=True)
    width = edges[0][0]
    depth = edges[1][0] if len(edges) > 1 else edges[0][0]
    angle = edges[0][1]
    return float(width), float(depth), float(angle)


def prepare_site_context(site: "SiteInputs", site_polygon_input: Optional[Polygon], polygon_setback_m: float, neighbors: Optional[List[Dict[str, Any]]] = None, terrain: Optional[Dict[str, Any]] = None, polygon_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    neighbors = neighbors or []
    polygon_meta = polygon_meta or {}
    if site_polygon_input is None:
        site_polygon = box(0.0, 0.0, site.site_width_m, site.site_depth_m)
        buildable_polygon = box(
            max(0.0, site.side_setback_m),
            max(0.0, site.front_setback_m),
            max(site.side_setback_m + 8.0, site.site_width_m - site.side_setback_m),
            max(site.front_setback_m + 8.0, site.site_depth_m - site.rear_setback_m),
        ).intersection(site_polygon)
        source = "Rektangulert fallback"
    else:
        site_polygon = largest_polygon(site_polygon_input)
        source = polygon_meta.get("source", "Tomtepolygon")

        # ADAPTIV BUFFER: reduser buffer hvis den spiser for mye av smal dimensjon
        major, minor, _ = minimum_rotated_dims(site_polygon)
        effective_setback = polygon_setback_m
        if minor > 0 and polygon_setback_m > 0:
            # Buffer paa begge sider = 2x setback. Behold maks 35% av smal side.
            max_allowed = minor * 0.175  # 17.5% per side = 35% totalt
            effective_setback = min(polygon_setback_m, max(1.5, max_allowed))

        buildable_polygon = site_polygon.buffer(-effective_setback) if effective_setback > 0 else site_polygon
        buildable_polygon = largest_polygon(buildable_polygon)
        if buildable_polygon is None or buildable_polygon.is_empty or buildable_polygon.area < 20.0:
            # Progressiv fallback: proev halvert buffer, deretter 1.5m, deretter 0
            for fallback_buf in [effective_setback * 0.5, 1.5, 0.5, 0.0]:
                buildable_polygon = largest_polygon(site_polygon.buffer(-fallback_buf))
                if buildable_polygon is not None and not buildable_polygon.is_empty and buildable_polygon.area >= 20.0:
                    break
            if buildable_polygon is None or buildable_polygon.is_empty:
                buildable_polygon = site_polygon

    site_width, site_depth, orientation_deg = minimum_rotated_dims(site_polygon)
    buildable_area = float(buildable_polygon.area) if buildable_polygon is not None else 0.0
    site_area = float(site_polygon.area)
    filtered_neighbors = []
    for neighbor in neighbors:
        poly = largest_polygon(neighbor.get("polygon"))
        if poly is None:
            continue
        if poly.intersects(site_polygon):
            continue
        if poly.distance(site_polygon) > 250.0:
            continue
        filtered_neighbors.append(
            {
                **neighbor,
                "polygon": poly,
                "distance_m": float(poly.distance(site_polygon)),
            }
        )
    filtered_neighbors.sort(key=lambda item: item.get("distance_m", 0.0))

    return {
        "site_polygon": site_polygon,
        "buildable_polygon": buildable_polygon,
        "site_area_m2": site_area,
        "site_width_m": site_width,
        "site_depth_m": site_depth,
        "buildable_area_m2": buildable_area,
        "orientation_deg": orientation_deg,
        "neighbors": filtered_neighbors,
        "terrain": terrain,
        "source": source,
        "polygon_meta": polygon_meta,
    }


def rects_to_polygon(rects: List[Dict[str, float]]) -> Polygon:
    polys = [box(r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]) for r in rects]
    return unary_union(polys).buffer(0)


# --- POLYGON-NATIVE FOTAVTRYKK-MOTOR (erstatter gammel bounding-box-logikk) ---

def _analyze_polygon(poly: Polygon) -> Dict[str, Any]:
    """Analyser tomtens form: aspektratio, orientering, kompakthet."""
    major, minor, angle = minimum_rotated_dims(poly)
    aspect = major / max(minor, 1.0)
    # Kompakthet: 1.0 = sirkel, lavere = mer irregulaer
    compactness = (4.0 * math.pi * poly.area) / max(poly.length ** 2, 1.0)
    # Rektangularitet: hvor mye av bounding-boksen fylles
    rect_area = major * minor
    rectangularity = poly.area / max(rect_area, 1.0)
    return {
        "major_m": major,
        "minor_m": minor,
        "orientation_deg": angle,
        "aspect_ratio": aspect,
        "compactness": compactness,
        "rectangularity": rectangularity,
        "area_m2": poly.area,
        "is_elongated": aspect > 2.2,
        "is_very_elongated": aspect > 3.5,
        "is_narrow": minor < 18.0,
        "is_compact": aspect < 1.8 and compactness > 0.6,
    }


def _find_inscribed_rect(poly: Polygon, target_depth_m: float, angle_deg: float,
                         max_width_m: float = 200.0) -> Optional[Polygon]:
    """
    Finn det stoerste rektangelet med gitt dybde som passer inne i polygonet
    orientert langs angle_deg. Bruker binaert soek paa bredde.
    """
    rad = math.radians(angle_deg)
    cx, cy = poly.centroid.x, poly.centroid.y

    # Soek langs hovedaksen for beste plassering
    best_rect = None
    best_area = 0.0

    # Proev forskjellige posisjoner langs aksen
    for offset_frac in [0.0, -0.1, 0.1, -0.2, 0.2, -0.3, 0.3]:
        ox = cx + offset_frac * poly.length * 0.15 * math.cos(rad)
        oy = cy + offset_frac * poly.length * 0.15 * math.sin(rad)

        # Binaert soek paa bredde
        lo, hi = 8.0, max_width_m
        best_w = 0.0
        for _ in range(20):
            mid = (lo + hi) / 2.0
            hw, hd = mid / 2.0, target_depth_m / 2.0
            corners = [
                (ox + hw * math.cos(rad) - hd * math.sin(rad),
                 oy + hw * math.sin(rad) + hd * math.cos(rad)),
                (ox - hw * math.cos(rad) - hd * math.sin(rad),
                 oy - hw * math.sin(rad) + hd * math.cos(rad)),
                (ox - hw * math.cos(rad) + hd * math.sin(rad),
                 oy - hw * math.sin(rad) - hd * math.cos(rad)),
                (ox + hw * math.cos(rad) + hd * math.sin(rad),
                 oy + hw * math.sin(rad) - hd * math.cos(rad)),
            ]
            candidate = Polygon(corners)
            if poly.contains(candidate):
                best_w = mid
                lo = mid
            else:
                hi = mid

        if best_w >= 8.0:
            hw, hd = best_w / 2.0, target_depth_m / 2.0
            corners = [
                (ox + hw * math.cos(rad) - hd * math.sin(rad),
                 oy + hw * math.sin(rad) + hd * math.cos(rad)),
                (ox - hw * math.cos(rad) - hd * math.sin(rad),
                 oy - hw * math.sin(rad) + hd * math.cos(rad)),
                (ox - hw * math.cos(rad) + hd * math.sin(rad),
                 oy - hw * math.sin(rad) - hd * math.cos(rad)),
                (ox + hw * math.cos(rad) + hd * math.sin(rad),
                 oy + hw * math.sin(rad) - hd * math.cos(rad)),
            ]
            rect = Polygon(corners)
            if rect.area > best_area:
                best_rect = rect
                best_area = rect.area

    return best_rect


def _make_oriented_rect(cx: float, cy: float, width: float, depth: float, angle_rad: float) -> Polygon:
    """Lag et rektangel sentrert paa (cx, cy) med gitt orientering."""
    hw, hd = width / 2.0, depth / 2.0
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    corners = [
        (cx + hw * cos_a - hd * sin_a, cy + hw * sin_a + hd * cos_a),
        (cx - hw * cos_a - hd * sin_a, cy - hw * sin_a + hd * cos_a),
        (cx - hw * cos_a + hd * sin_a, cy - hw * sin_a - hd * cos_a),
        (cx + hw * cos_a + hd * sin_a, cy + hw * sin_a - hd * cos_a),
    ]
    return Polygon(corners)


def _place_grid_buildings(
    poly: Polygon,
    building_width_m: float,
    building_depth_m: float,
    angle_deg: float,
    spacing_along: float = 18.0,
    spacing_across: float = 22.0,
    max_buildings: int = 12,
    max_footprint_m2: float = 99999.0,
) -> List[Polygon]:
    """
    2D-grid plassering: fordeler bygninger over HELE tomten i et rutenett
    langs og paa tvers av hovedaksen, ikke bare langs en linje.
    """
    rad = math.radians(angle_deg)
    perp_rad = rad + math.pi / 2.0
    cx, cy = poly.centroid.x, poly.centroid.y
    major, minor, _ = minimum_rotated_dims(poly)

    # Beregn antall rader og kolonner
    step_along = building_depth_m + spacing_along    # langs hovedaksen
    step_across = building_width_m + spacing_across  # paa tvers

    n_along = max(1, int((major - building_depth_m) / max(step_along, 1.0)) + 1)
    n_across = max(1, int((minor - building_width_m) / max(step_across, 1.0)) + 1)

    # Begrens totalt antall
    total_slots = n_along * n_across
    if total_slots > max_buildings:
        # Reduser symmetrisk
        ratio = math.sqrt(max_buildings / max(total_slots, 1))
        n_along = max(1, int(n_along * ratio))
        n_across = max(1, int(n_across * ratio))

    # Sentrer rutenettet
    span_along = (n_along - 1) * step_along
    span_across = (n_across - 1) * step_across
    start_along = -span_along / 2.0
    start_across = -span_across / 2.0

    buildings: List[Polygon] = []
    total_area = 0.0

    for row in range(n_along):
        for col in range(n_across):
            if len(buildings) >= max_buildings or total_area >= max_footprint_m2:
                break
            offset_along = start_along + row * step_along
            offset_across = start_across + col * step_across

            bx = cx + offset_along * math.cos(perp_rad) + offset_across * math.cos(rad)
            by = cy + offset_along * math.sin(perp_rad) + offset_across * math.sin(rad)

            # Proev aa plassere bygning her — tilpass bredde til hva som passer
            remaining = max_footprint_m2 - total_area
            max_w = min(building_width_m, remaining / max(building_depth_m, 1.0))

            lo, hi = 6.0, max_w
            best_w = 0.0
            for _ in range(16):
                mid = (lo + hi) / 2.0
                candidate = _make_oriented_rect(bx, by, mid, building_depth_m, rad)
                if poly.contains(candidate):
                    best_w = mid
                    lo = mid
                else:
                    hi = mid

            if best_w >= 6.0:
                bld = _make_oriented_rect(bx, by, best_w, building_depth_m, rad)
                buildings.append(bld)
                total_area += bld.area

        if total_area >= max_footprint_m2:
            break

    return buildings


def _make_courtyard_block(
    poly: Polygon,
    outer_side: float,
    ring_depth: float,
    angle_deg: float,
    cx: float,
    cy: float,
) -> Optional[Polygon]:
    """Lag en karre-blokk med gaardrom paa angitt posisjon."""
    rad = math.radians(angle_deg)
    outer = _make_oriented_rect(cx, cy, outer_side, outer_side, rad)
    if not poly.contains(outer):
        # Skalere ned til det passer
        lo_s, hi_s = 0.5, 1.0
        best_s = 0.0
        for _ in range(14):
            mid = (lo_s + hi_s) / 2.0
            scaled = affinity.scale(outer, xfact=mid, yfact=mid, origin=(cx, cy))
            if poly.contains(scaled):
                best_s = mid
                lo_s = mid
            else:
                hi_s = mid
        if best_s < 0.5:
            return None
        outer = affinity.scale(outer, xfact=best_s, yfact=best_s, origin=(cx, cy))

    # Lag gaardrom (indre rektangel)
    inner_inset = min(ring_depth, math.sqrt(outer.area) * 0.25)
    inner = outer.buffer(-inner_inset)
    if inner is not None and not inner.is_empty and inner.area > 30:
        result = outer.difference(inner).buffer(0)
        if result.area > 50:
            return result
    return outer  # Fallback: solid blokk


# --- REALISTISKE BYGNINGSDIMENSJONER ---
TYPOLOGY_LIMITS = {
    "Lamell":        {"bld_w": 50.0, "bld_d": 14.0, "sp_along": 18.0, "sp_across": 24.0, "max_n": 12},
    "Punkthus":      {"bld_w": 20.0, "bld_d": 20.0, "sp_along": 24.0, "sp_across": 24.0, "max_n": 9},
    "Rekke":         {"bld_w": 55.0, "bld_d": 10.0, "sp_along": 14.0, "sp_across": 18.0, "max_n": 12},
    "Tun":           {"bld_w": 42.0, "bld_d": 11.0, "sp_along": 14.0, "sp_across": 16.0, "max_n": 6},
    "Karré":         {"bld_w": 45.0, "bld_d": 45.0, "sp_along": 22.0, "sp_across": 22.0, "max_n": 4, "ring_d": 11.0},
    "Tårn":          {"bld_w": 22.0, "bld_d": 22.0, "sp_along": 28.0, "sp_across": 28.0, "max_n": 6},
    "Podium + Tårn": {"bld_w": 45.0, "bld_d": 22.0, "sp_along": 22.0, "sp_across": 22.0, "max_n": 3},
}


def create_typology_footprint(buildable_polygon: Polygon, typology: str, target_footprint_m2: float) -> Tuple[Polygon, Dict[str, Any]]:
    """
    REALISTISK fotavtrykk-motor med 2D-grid-plassering.

    Fordeler bygninger over hele tomten i et rutenett — ikke bare langs en linje.
    Karré-typologien lager ekte kvartaler med gaardrom.
    """
    shape_info = _analyze_polygon(buildable_polygon)
    major = shape_info['major_m']
    minor = shape_info['minor_m']
    angle = shape_info['orientation_deg']
    area = shape_info['area_m2']

    target_footprint_m2 = min(target_footprint_m2, area * 0.92)
    limits = TYPOLOGY_LIMITS.get(typology, TYPOLOGY_LIMITS["Lamell"])

    placement_info = {
        'fit_scale': 1.0, 'containment_ratio': 1.0,
        'footprint_width_m': 0.0, 'footprint_depth_m': 0.0,
        'orientation_deg': round(angle, 1),
        'polygon_shape': 'elongated' if shape_info['is_elongated'] else 'compact',
        'n_buildings': 1,
    }

    footprint: Any = None

    # Tilpass bygningsdybde til smal side
    bld_w = min(limits["bld_w"], minor * 0.75)
    bld_d = min(limits["bld_d"], minor * 0.55 if typology != "Karré" else minor * 0.7)
    bld_w = max(8.0, bld_w)
    bld_d = max(8.0, bld_d)

    if typology == 'Karré':
        # Ekte kvartaler med gaardrom
        ring_d = limits.get("ring_d", 11.0)
        karre_side = min(bld_w, bld_d)
        single_area = karre_side * karre_side - max(0, (karre_side - 2*ring_d))**2
        n_needed = max(1, min(limits["max_n"], math.ceil(target_footprint_m2 / max(single_area, 1.0))))

        # Plasser kvartaler i grid
        step = karre_side + limits["sp_along"]
        n_along = max(1, int(math.sqrt(n_needed) + 0.5))
        n_across = max(1, math.ceil(n_needed / n_along))

        rad = math.radians(angle)
        perp_rad = rad + math.pi / 2.0
        pcx, pcy = buildable_polygon.centroid.x, buildable_polygon.centroid.y
        span_a = (n_along - 1) * step
        span_c = (n_across - 1) * step

        blocks: List[Polygon] = []
        for row in range(n_along):
            for col in range(n_across):
                if len(blocks) >= n_needed:
                    break
                oa = -span_a / 2.0 + row * step
                oc = -span_c / 2.0 + col * step
                bx = pcx + oa * math.cos(perp_rad) + oc * math.cos(rad)
                by = pcy + oa * math.sin(perp_rad) + oc * math.sin(rad)
                block = _make_courtyard_block(buildable_polygon, karre_side, ring_d, angle, bx, by)
                if block is not None and block.area > 50:
                    blocks.append(block)

        if blocks:
            footprint = unary_union(blocks).buffer(0)
            placement_info['n_buildings'] = len(blocks)
            placement_info['courtyard_count'] = len(blocks)

    elif typology == 'Tun':
        # L/U-form: hoveddel + vinkelrette floeyer
        wing_d = bld_d
        main_w = min(bld_w, target_footprint_m2 * 0.40 / max(wing_d, 1.0))
        main = _find_inscribed_rect(buildable_polygon, wing_d, angle, max_width_m=main_w)
        if main is not None:
            remaining_poly = buildable_polygon.difference(main.buffer(3.0))
            wings: List[Polygon] = []
            for wing_angle in [angle + 90, angle - 90]:
                w_w = min(bld_w * 0.7, target_footprint_m2 * 0.22 / max(wing_d, 1.0))
                wr = _find_inscribed_rect(remaining_poly, wing_d, wing_angle, max_width_m=w_w)
                if wr is not None and wr.area > 40:
                    wings.append(wr)
                    remaining_poly = remaining_poly.difference(wr.buffer(2.0))
            footprint = unary_union([main] + wings).buffer(0) if wings else main
            placement_info['n_buildings'] = 1 + len(wings)

    elif typology == 'Podium + Tårn':
        # Podium (stort, lavt) + taarn plassert paa toppen
        podium_w = min(bld_w, target_footprint_m2 * 0.5 / max(bld_d, 1.0))
        podium = _find_inscribed_rect(buildable_polygon, bld_d, angle, max_width_m=podium_w)
        if podium is not None:
            tower_side = min(18.0, math.sqrt(podium.area) * 0.35)
            pcx2, pcy2 = podium.centroid.x, podium.centroid.y
            tower = _make_oriented_rect(pcx2, pcy2, tower_side, tower_side, math.radians(angle))
            tower_clipped = tower.intersection(podium).buffer(0)
            if not tower_clipped.is_empty and tower_clipped.area > 60:
                footprint = unary_union([podium, tower_clipped]).buffer(0)
                placement_info['n_buildings'] = 2
            else:
                footprint = podium

    else:
        # Lamell, Punkthus, Rekke, Taarn: 2D-grid plassering
        single_area = bld_w * bld_d
        n_needed = max(1, min(limits["max_n"], math.ceil(target_footprint_m2 / max(single_area, 1.0))))

        buildings = _place_grid_buildings(
            buildable_polygon, bld_w, bld_d, angle,
            spacing_along=limits["sp_along"],
            spacing_across=limits["sp_across"],
            max_buildings=n_needed,
            max_footprint_m2=target_footprint_m2,
        )
        if buildings:
            footprint = unary_union(buildings).buffer(0)
            placement_info['n_buildings'] = len(buildings)

    # Fallback
    if footprint is None or footprint.is_empty or float(getattr(footprint, 'area', 0.0)) < 30:
        for d_try in [14.0, 12.0, 10.0, 8.0]:
            if d_try > minor * 0.85:
                continue
            footprint = _find_inscribed_rect(buildable_polygon, d_try, angle, max_width_m=min(bld_w, major * 0.6))
            if footprint is not None and not footprint.is_empty and float(getattr(footprint, 'area', 0.0)) >= 30:
                break

    if footprint is None or footprint.is_empty or float(getattr(footprint, 'area', 0.0)) < 30:
        sf = math.sqrt(min(target_footprint_m2, area * 0.4) / max(area, 1.0))
        footprint = affinity.scale(buildable_polygon, xfact=sf, yfact=sf, origin=buildable_polygon.centroid).buffer(0)

    fp_parts = split_geometry_to_polygons(footprint)
    if fp_parts:
        fp_major = max(minimum_rotated_dims(p)[0] for p in fp_parts)
        fp_minor = max(minimum_rotated_dims(p)[1] for p in fp_parts)
    else:
        fp_major, fp_minor, _ = minimum_rotated_dims(largest_polygon(footprint) or buildable_polygon)
    placement_info['footprint_width_m'] = round(float(fp_major), 1)
    placement_info['footprint_depth_m'] = round(float(fp_minor), 1)
    placement_info['fit_scale'] = round(float(getattr(footprint, 'area', 0.0) / max(target_footprint_m2, 1.0)), 3)
    placement_info['containment_ratio'] = round(float(footprint.intersection(buildable_polygon).area / max(getattr(footprint, 'area', 1.0), 1.0)), 3)
    placement_info['component_count'] = len(fp_parts)

    return footprint.buffer(0), placement_info



def sample_points_in_polygon(poly: Polygon, spacing_m: float = 6.0, max_points: int = 180) -> List[Point]:
    poly = largest_polygon(poly) or poly
    if poly is None or poly.is_empty:
        return []
    minx, miny, maxx, maxy = poly.bounds
    points: List[Point] = []
    x = minx + (spacing_m / 2.0)
    while x < maxx:
        y = miny + (spacing_m / 2.0)
        while y < maxy:
            p = Point(x, y)
            if poly.contains(p):
                points.append(p)
            y += spacing_m
        x += spacing_m
    if not points:
        points = [poly.representative_point()]
    if len(points) > max_points:
        idx = np.linspace(0, len(points) - 1, max_points).astype(int)
        points = [points[i] for i in idx]
    return points


def solar_azimuth_deg(latitude_deg: float, day_of_year: int, solar_hour: float) -> float:
    lat = math.radians(latitude_deg)
    decl = solar_declination_rad(day_of_year)
    hour_angle = math.radians(15.0 * (solar_hour - 12.0))
    az = math.degrees(
        math.atan2(
            math.sin(hour_angle),
            (math.cos(hour_angle) * math.sin(lat)) - (math.tan(decl) * math.cos(lat)),
        )
    )
    return (az + 180.0) % 360.0


def adjusted_shadow_length_m(height_m: float, altitude_deg: float, terrain: Optional[Dict[str, Any]], shadow_azimuth_deg: float) -> float:
    if altitude_deg <= 0.5:
        return height_m * 50.0
    slope = terrain_slope_along_azimuth(terrain, shadow_azimuth_deg)
    denom = math.tan(math.radians(altitude_deg)) + slope
    denom = max(0.02, denom)
    return float(height_m / denom)


def build_shadow_polygon(footprint: Polygon, height_m: float, sun_azimuth_deg: float, altitude_deg: float, terrain: Optional[Dict[str, Any]]) -> Optional[Polygon]:
    if footprint is None or footprint.is_empty or altitude_deg <= 0.5 or height_m <= 0.0:
        return None
    shadow_az = (sun_azimuth_deg + 180.0) % 360.0
    length = adjusted_shadow_length_m(height_m, altitude_deg, terrain, shadow_az)
    dx = math.sin(math.radians(shadow_az)) * length
    dy = math.cos(math.radians(shadow_az)) * length
    translated = affinity.translate(footprint, xoff=dx, yoff=dy)
    return unary_union([footprint, translated]).convex_hull.buffer(0)


def serialize_neighbor_geometries(neighbors: List[Dict[str, Any]], max_neighbors: int = 20) -> List[Dict[str, Any]]:
    serialized = []
    for neighbor in neighbors[:max_neighbors]:
        serialized.append(
            {
                "coords": geometry_to_coord_groups(neighbor.get("polygon")),
                "height_m": round(float(neighbor.get("height_m", 0.0)), 1),
                "distance_m": round(float(neighbor.get("distance_m", 0.0)), 1),
            }
        )
    return serialized


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
    polygon_setback_m: float = 0.0
    site_geometry_source: str = "Rektangel"
    polygon_crs: str = ""
    neighbor_count: int = 0
    terrain_slope_pct: float = 0.0
    terrain_relief_m: float = 0.0


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
    buildable_area_m2: float
    open_space_ratio: float
    target_fit_pct: float
    unit_count: int
    mix_counts: Dict[str, int]
    parking_spaces: int
    parking_pressure_pct: float
    solar_score: float
    estimated_equinox_sun_hours: float
    estimated_winter_sun_hours: float
    sunlit_open_space_pct: float
    winter_noon_shadow_m: float
    equinox_noon_shadow_m: float
    summer_afternoon_shadow_m: float
    efficiency_ratio: float
    neighbor_count: int
    terrain_slope_pct: float
    terrain_relief_m: float
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


def derive_limits(site: SiteInputs, geodata_context: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    if geodata_context:
        buildable_width = max(8.0, geodata_context.get("site_width_m", site.site_width_m))
        buildable_depth = max(8.0, geodata_context.get("site_depth_m", site.site_depth_m))
        buildable_area = max(0.0, geodata_context.get("buildable_area_m2", site.site_area_m2))
        site_area = max(1.0, geodata_context.get("site_area_m2", site.site_area_m2))
    else:
        buildable_width = max(8.0, site.site_width_m - (2.0 * site.side_setback_m))
        buildable_depth = max(8.0, site.site_depth_m - site.front_setback_m - site.rear_setback_m)
        buildable_area = buildable_width * buildable_depth
        site_area = site.site_area_m2
    max_footprint_by_bya = site_area * (site.max_bya_pct / 100.0) if site.max_bya_pct > 0 else buildable_area
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


def evaluate_solar(
    site: SiteInputs,
    site_polygon: Polygon,
    footprint_polygon: Polygon,
    building_height_m: float,
    typology: str,
    neighbors: Optional[List[Dict[str, Any]]] = None,
    terrain: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    neighbors = neighbors or []
    open_space = site_polygon.difference(footprint_polygon).buffer(0)
    if open_space.is_empty:
        open_space = site_polygon

    spacing = max(4.5, min(10.0, math.sqrt(max(open_space.area, 1.0) / 85.0)))
    sample_points = sample_points_in_polygon(largest_polygon(open_space) or open_space, spacing_m=spacing)
    if not sample_points:
        sample_points = [site_polygon.representative_point()]

    def sunlit_fraction(day_of_year: int, solar_hour: float) -> float:
        altitude = solar_altitude_deg(site.latitude_deg, day_of_year, solar_hour)
        if altitude <= 0.5:
            return 0.0
        azimuth = (solar_azimuth_deg(site.latitude_deg, day_of_year, solar_hour) - site.north_rotation_deg) % 360.0
        shadow_polys: List[Polygon] = []
        own_shadow = build_shadow_polygon(footprint_polygon, building_height_m, azimuth, altitude, terrain)
        if own_shadow is not None:
            shadow_polys.append(own_shadow)
        for neighbor in neighbors:
            shadow = build_shadow_polygon(neighbor["polygon"], float(neighbor.get("height_m", 0.0)), azimuth, altitude, terrain)
            if shadow is not None:
                shadow_polys.append(shadow)
        if not shadow_polys:
            return 1.0
        shadow_union = unary_union(shadow_polys).buffer(0)
        sunlit = 0
        for point in sample_points:
            if not shadow_union.covers(point):
                sunlit += 1
        return float(sunlit / max(1, len(sample_points)))

    equinox_hours = [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
    winter_hours = [10.0, 11.0, 12.0, 13.0, 14.0]

    equinox_fracs = [sunlit_fraction(80, hour) for hour in equinox_hours]
    winter_fracs = [sunlit_fraction(355, hour) for hour in winter_hours]
    equinox_hours_sum = float(sum(equinox_fracs))
    winter_hours_sum = float(sum(winter_fracs))
    mean_equinox = float(np.mean(equinox_fracs)) if equinox_fracs else 0.0
    winter_noon_frac = sunlit_fraction(355, 12.0)
    noon_equinox_frac = sunlit_fraction(80, 12.0)

    winter_alt = solar_altitude_deg(site.latitude_deg, 355, 12.0)
    equinox_alt = solar_altitude_deg(site.latitude_deg, 80, 12.0)
    summer_alt = solar_altitude_deg(site.latitude_deg, 172, 15.0)
    winter_shadow_az = ((solar_azimuth_deg(site.latitude_deg, 355, 12.0) - site.north_rotation_deg) + 180.0) % 360.0
    equinox_shadow_az = ((solar_azimuth_deg(site.latitude_deg, 80, 12.0) - site.north_rotation_deg) + 180.0) % 360.0
    summer_shadow_az = ((solar_azimuth_deg(site.latitude_deg, 172, 15.0) - site.north_rotation_deg) + 180.0) % 360.0
    winter_shadow = adjusted_shadow_length_m(building_height_m, winter_alt, terrain, winter_shadow_az)
    equinox_shadow = adjusted_shadow_length_m(building_height_m, equinox_alt, terrain, equinox_shadow_az)
    summer_shadow = adjusted_shadow_length_m(building_height_m, summer_alt, terrain, summer_shadow_az)

    typology_bonus = {"Punkthus": 0.06, "Lamell": 0.04, "Tun": -0.02, "Rekke": 0.05}.get(typology, 0.0)
    neighbor_penalty = min(0.12, 0.012 * len(neighbors))
    solar_score = 100.0 * clamp(
        (0.54 * mean_equinox) + (0.26 * winter_noon_frac) + (0.14 * noon_equinox_frac) + typology_bonus - neighbor_penalty,
        0.18,
        1.0,
    )
    solar_score = clamp(solar_score, 18.0, 100.0)

    return {
        "solar_score": solar_score,
        "estimated_equinox_sun_hours": round(equinox_hours_sum, 2),
        "estimated_winter_sun_hours": round(winter_hours_sum, 2),
        "sunlit_open_space_pct": round(mean_equinox * 100.0, 1),
        "winter_noon_shadow_m": round(winter_shadow, 1),
        "equinox_noon_shadow_m": round(equinox_shadow, 1),
        "summer_afternoon_shadow_m": round(summer_shadow, 1),
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


def build_massing_parts(
    footprint_polygon: Any,
    typology: str,
    floors: int,
    floor_to_floor_m: float,
) -> List[Dict[str, Any]]:
    """
    Bryt et fotavtrykk-polygon ned i volumdeler med individuelle hoyder.

    Returnerer liste med dicts:
        name, height_m, floors, color, coords (geometry_to_coord_groups-format)
    """
    parts: List[Dict[str, Any]] = []
    components = split_geometry_to_polygons(footprint_polygon)
    if not components:
        return parts

    full_height = max(floor_to_floor_m, 2.8) * max(floors, 1)

    # Fargepalett per typologi
    COLORS = {
        "Lamell":        [34, 197, 94, 0.80],    # groenn
        "Punkthus":      [56, 189, 248, 0.80],   # blaa
        "Tun":           [168, 130, 240, 0.80],   # lilla
        "Rekke":         [250, 180, 60, 0.80],    # gul/oransje
        "Podium + Tårn": [220, 80, 120, 0.80],    # rosa/roed
        "Karré":         [100, 200, 180, 0.80],   # teal
        "Tårn":          [56, 140, 248, 0.80],    # moerkere blaa
    }
    base_color = COLORS.get(typology, [34, 197, 94, 0.80])

    if typology == "Podium + Tårn" and len(components) >= 1:
        # Stoerste del = podium (lavere), resten = taarn (full hoyde)
        sorted_comps = sorted(components, key=lambda p: p.area, reverse=True)
        podium_floors = max(2, min(floors - 2, int(floors * 0.35)))
        podium_height = podium_floors * floor_to_floor_m
        tower_floors = floors
        tower_height = full_height

        # Podium
        podium = sorted_comps[0]
        parts.append({
            "name": "Podium",
            "height_m": round(podium_height, 1),
            "floors": podium_floors,
            "color": [180, 180, 190, 0.65],
            "coords": geometry_to_coord_groups(podium),
        })

        # Taarn(er): plasser i sentrum av podium, eller bruk oevrige deler
        if len(sorted_comps) > 1:
            for i, comp in enumerate(sorted_comps[1:], start=1):
                parts.append({
                    "name": f"Taarn {i}",
                    "height_m": round(tower_height, 1),
                    "floors": tower_floors,
                    "color": COLORS.get("Tårn", base_color),
                    "coords": geometry_to_coord_groups(comp),
                })
        else:
            # Lag et taarn-fotavtrykk fra sentrum av podium
            cx, cy = podium.centroid.x, podium.centroid.y
            tower_side = min(18.0, math.sqrt(podium.area) * 0.45)
            half = tower_side / 2.0
            tower_box = box(cx - half, cy - half, cx + half, cy + half)
            tower_clipped = tower_box.intersection(podium).buffer(0)
            if not tower_clipped.is_empty and tower_clipped.area > 20:
                parts.append({
                    "name": "Taarn",
                    "height_m": round(tower_height, 1),
                    "floors": tower_floors,
                    "color": COLORS.get("Tårn", base_color),
                    "coords": geometry_to_coord_groups(tower_clipped),
                })

    elif typology == "Tun" and len(components) >= 2:
        # Stoerste = hovedfloey (full hoyde), resten = sidefloyer (1 etasje lavere)
        sorted_comps = sorted(components, key=lambda p: p.area, reverse=True)
        for i, comp in enumerate(sorted_comps):
            if i == 0:
                part_floors = floors
                part_name = "Hovedfloey"
            else:
                part_floors = max(2, floors - 1)
                part_name = f"Sidefloey {i}"
            parts.append({
                "name": part_name,
                "height_m": round(part_floors * floor_to_floor_m, 1),
                "floors": part_floors,
                "color": base_color if i == 0 else [base_color[0], base_color[1], base_color[2], 0.65],
                "coords": geometry_to_coord_groups(comp),
            })

    elif typology == "Rekke":
        # Alle enheter paa samme hoyde (typisk 2-3 etasjer)
        for i, comp in enumerate(components):
            parts.append({
                "name": f"Enhet {i + 1}",
                "height_m": round(full_height, 1),
                "floors": floors,
                "color": base_color,
                "coords": geometry_to_coord_groups(comp),
            })

    else:
        # Lamell, Punkthus, Karre, Taarn, og alt annet: hver komponent paa full hoyde
        for i, comp in enumerate(components):
            label = typology if len(components) == 1 else f"{typology} {i + 1}"
            parts.append({
                "name": label,
                "height_m": round(full_height, 1),
                "floors": floors,
                "color": base_color,
                "coords": geometry_to_coord_groups(comp),
            })

    return parts


def generate_options(site: SiteInputs, mix_specs: List[MixSpec], geodata_context: Optional[Dict[str, Any]] = None) -> List[OptionResult]:
    geodata_context = geodata_context or prepare_site_context(site, None, 0.0)
    limits = derive_limits(site, geodata_context)
    site_polygon = geodata_context["site_polygon"]
    buildable_polygon = geodata_context["buildable_polygon"]
    neighbors = geodata_context.get("neighbors", [])
    terrain = geodata_context.get("terrain")

    max_footprint = limits["max_footprint"]
    allowed_floors = int(limits["allowed_floors"])
    if max_footprint <= 0 or buildable_polygon is None or buildable_polygon.is_empty:
        return []

    templates = [
        {"name": "Alt A - Lamell", "typology": "Lamell", "coverage": 0.78, "floor_bias": 0, "eff_adj": 0.02},
        {"name": "Alt B - Karré", "typology": "Karré", "coverage": 0.84, "floor_bias": 0, "eff_adj": 0.00},
        {"name": "Alt C - Punkthus", "typology": "Punkthus", "coverage": 0.52, "floor_bias": 2, "eff_adj": -0.01},
        {"name": "Alt D - Tårn", "typology": "Tårn", "coverage": 0.36, "floor_bias": 4, "eff_adj": -0.03},
        {"name": "Alt E - Podium + Tårn", "typology": "Podium + Tårn", "coverage": 0.58, "floor_bias": 3, "eff_adj": -0.02},
        {"name": "Alt F - Tun", "typology": "Tun", "coverage": 0.82, "floor_bias": -1, "eff_adj": -0.02},
        {"name": "Alt G - Rekke", "typology": "Rekke", "coverage": 0.68, "floor_bias": -1, "eff_adj": 0.04},
    ]

    options: List[OptionResult] = []
    target_bta = max(site.desired_bta_m2, 1.0)
    serialized_neighbors = serialize_neighbor_geometries(neighbors)
    terrain_summary = {
        "slope_pct": round(float((terrain or {}).get("slope_pct", 0.0)), 1),
        "relief_m": round(float((terrain or {}).get("relief_m", 0.0)), 1),
        "grade_ns_pct": round(float((terrain or {}).get("grade_ns_pct", 0.0)), 2),
        "grade_ew_pct": round(float((terrain or {}).get("grade_ew_pct", 0.0)), 2),
        "point_count": int((terrain or {}).get("point_count", 0)),
        "source": (terrain or {}).get("source", ""),
    }

    for template in templates:
        typology = template["typology"]
        target_footprint = max_footprint * template["coverage"]

        ai_result = None
        ai_massing = None

        # --- AI-DREVET PLASSERING (foerstevalg) ---
        if HAS_AI_PLANNER and ai_site_planner is not None and ai_site_planner.is_available():
            try:
                ai_result = ai_site_planner.plan_site(
                    site_polygon=site_polygon,
                    buildable_polygon=buildable_polygon,
                    typology=typology,
                    neighbors=neighbors,
                    terrain=terrain,
                    site_intelligence=geodata_context.get('site_intelligence'),
                    site_inputs={"latitude_deg": site.latitude_deg, "site_area_m2": site.site_area_m2},
                    target_bta_m2=target_bta,
                    max_floors=int(allowed_floors),
                    max_height_m=site.max_height_m,
                    max_bya_pct=site.max_bya_pct,
                    floor_to_floor_m=site.floor_to_floor_m,
                )
                if ai_result and ai_result.get("buildings") and ai_result.get("footprint"):
                    footprint_polygon = ai_result["footprint"]
                    ai_buildings = ai_result["buildings"]
                    footprint_area = float(footprint_polygon.area)
                    placement = {
                        "fit_scale": round(footprint_area / max(target_footprint, 1.0), 3),
                        "containment_ratio": 1.0,
                        "footprint_width_m": round(max(b.get("width_m", 0) for b in ai_buildings), 1),
                        "footprint_depth_m": round(max(b.get("depth_m", 0) for b in ai_buildings), 1),
                        "orientation_deg": round(ai_buildings[0].get("angle_deg", 0), 1) if ai_buildings else 0.0,
                        "n_buildings": len(ai_buildings),
                        "component_count": len(ai_buildings),
                        "source": ai_result.get("source", "AI"),
                    }
                    # Bygg massing_parts fra AI-bygninger
                    PART_COLORS = {
                        "Lamell": [34, 197, 94, 200], "Punkthus": [56, 189, 248, 200],
                        "Tun": [168, 130, 240, 200], "Rekke": [250, 180, 60, 200],
                        "Karré": [100, 200, 180, 200], "Tårn": [56, 140, 248, 200],
                        "Podium + Tårn": [220, 80, 120, 200],
                    }
                    base_color = PART_COLORS.get(typology, [34, 197, 94, 200])
                    ai_massing = []
                    for bld in ai_buildings:
                        bld_poly = bld.get("polygon")
                        if bld_poly is None:
                            continue
                        role = bld.get("role", "main")
                        color = list(base_color)
                        if role == "wing":
                            color = [int(c * 0.8) for c in base_color[:3]] + [180]
                        elif role == "tower":
                            color = [56, 140, 248, 230]
                        ai_massing.append({
                            "name": bld.get("name", typology),
                            "height_m": float(bld.get("height_m", site.floor_to_floor_m * 4)),
                            "floors": int(bld.get("floors", 4)),
                            "color": color,
                            "coords": geometry_to_coord_groups(bld_poly),
                        })
                else:
                    ai_result = None  # Fell through to geometric
            except Exception:
                ai_result = None

        # --- GEOMETRISK FALLBACK ---
        if ai_result is None or not ai_result.get("buildings"):
            footprint_polygon, placement = create_typology_footprint(buildable_polygon, typology, target_footprint)

        footprint_area = float(footprint_polygon.area)

        floors_needed = math.ceil(target_bta / max(footprint_area, 1.0))
        floors = clamp(floors_needed + template["floor_bias"], 2, allowed_floors)
        floors = int(floors)

        if typology == "Tårn":
            floors = int(clamp(max(floors, min(allowed_floors, 8)), 4, allowed_floors))
        elif typology == "Podium + Tårn":
            floors = int(clamp(max(floors, min(allowed_floors, 7)), 4, allowed_floors))
        elif typology == "Karré":
            floors = int(clamp(max(floors, 3), 2, allowed_floors))

        gross_bta = footprint_area * floors
        if site.max_bra_m2 > 0:
            gross_bta = min(gross_bta, site.max_bra_m2)

        actual_efficiency = clamp(site.efficiency_ratio + template["eff_adj"], 0.64, 0.88)
        saleable_area = gross_bta * actual_efficiency

        mix_counts, _ = allocate_unit_mix(saleable_area, mix_specs)
        unit_count = sum(mix_counts.values())
        parking_spaces = int(math.ceil(unit_count * site.parking_ratio_per_unit)) if unit_count > 0 else 0
        open_space_ratio = max(0.0, 1.0 - (footprint_area / max(geodata_context["site_area_m2"], 1.0)))
        parking_pressure_area = parking_spaces * site.parking_area_per_space_m2
        parking_pressure_pct = (
            100.0 * parking_pressure_area / max(geodata_context["site_area_m2"] * open_space_ratio, 1.0)
            if open_space_ratio > 0
            else 100.0
        )

        height_m = floors * site.floor_to_floor_m
        solar = evaluate_solar(
            site=site,
            site_polygon=site_polygon,
            footprint_polygon=footprint_polygon,
            building_height_m=height_m,
            typology=typology,
            neighbors=neighbors,
            terrain=terrain,
        )
        target_fit_pct = 100.0 * gross_bta / target_bta if target_bta > 0 else 100.0

        notes: List[str] = []
        if target_fit_pct < 85:
            notes.append("Lav måloppnåelse mot ønsket volum; krever høyere utnyttelse eller omprosjektering.")
        elif target_fit_pct > 110:
            notes.append("Ligger over ønsket volum; vurder nedskalering eller større leiligheter.")
        else:
            notes.append("Treffer ønsket volum relativt godt i tidligfase.")

        if placement.get("fit_scale", 1.0) < 0.92:
            notes.append("Tomtepolygonen gir et mer krevende byggefelt; volumet er skalert ned for å holde seg innenfor reelle grenser.")

        if solar["solar_score"] < 55:
            notes.append("Svakere solforhold når faktisk tomtepolygon og naboer tas med; videre 3D-kontroll anbefales.")
        elif solar["solar_score"] < 70:
            notes.append("Middels solforhold med reell kontekst; verifiser uteareal og nord-/sydvendte fasader videre.")
        else:
            notes.append("God indikativ soltilgang også når nabohøyder og terreng legges inn i modellen.")

        if terrain and terrain.get("slope_pct", 0.0) > 12.0:
            notes.append("Terrenget er relativt bratt; sokkel, kjeller og adkomst bør testes videre mot kotegrunnlag.")
        elif terrain and terrain.get("slope_pct", 0.0) > 5.0:
            notes.append("Terrenget er merkbart skrånende og vil påvirke parkering, innganger og uteopphold.")

        if typology == "Lamell":
            notes.append("Lamell er som regel sterkest på effektivitet, dagslys og repetérbar boliglogikk.")
        elif typology == "Karré":
            notes.append("Karré gir tydelig byrom og robust kvartalsstruktur, men krever mer presis kontroll på gårdsrom, lys og innkjøring.")
        elif typology == "Punkthus":
            notes.append("Punkthus gir ofte best lys og sikt, men taper gjerne litt effektivitet og kjerneøkonomi.")
        elif typology == "Tårn":
            notes.append("Tårn kan gi høy måloppnåelse på små fotavtrykk, men er mest sårbart for regulering, kjerneøkonomi og vind/skygge.")
        elif typology == "Podium + Tårn":
            notes.append("Podium + tårn kombinerer urbant gategrep med høyde, men krever presis kontroll på sokkel, uteareal og planrisiko.")
        elif typology == "Rekke":
            notes.append("Rekkehus gir flest enheter, lav byggehoeyde og effektiv arealbruk, men gir lavere BTA per tomt enn blokk.")
        else:
            notes.append("Tun/U-form gir hoey arealutnyttelse og tydelig uterom, men er mest saarbar for skygge fra egne floeyer og naboer.")

        score = rank_score(
            target_fit_pct=target_fit_pct,
            solar_score=solar["solar_score"],
            open_space_ratio=open_space_ratio,
            efficiency_ratio=actual_efficiency,
            parking_pressure_pct=parking_pressure_pct,
        )

        winter_alt = solar_altitude_deg(site.latitude_deg, 355, 12.0)
        winter_az = (solar_azimuth_deg(site.latitude_deg, 355, 12.0) - site.north_rotation_deg) % 360.0
        winter_shadow_poly = build_shadow_polygon(footprint_polygon, height_m, winter_az, winter_alt, terrain)

        massing_parts = ai_massing if ai_massing else build_massing_parts(footprint_polygon, typology, floors, site.floor_to_floor_m)
        ai_source = placement.get("source", "") if ai_result else ""
        geometry = {
            "site_polygon_coords": geometry_to_coord_groups(site_polygon),
            "buildable_polygon_coords": geometry_to_coord_groups(buildable_polygon),
            "footprint_polygon_coords": geometry_to_coord_groups(footprint_polygon),
            "winter_shadow_polygon_coords": geometry_to_coord_groups(winter_shadow_poly) if winter_shadow_poly is not None else [],
            "neighbor_polygons": serialized_neighbors,
            "terrain_summary": terrain_summary,
            "placement": placement,
            "site_source": (ai_source + " + " if ai_source else "") + geodata_context.get("source", "Tomt"),
            "massing_parts": massing_parts,
            "component_count": len(split_geometry_to_polygons(footprint_polygon)),
        }

        options.append(
            OptionResult(
                name=template["name"],
                typology=typology,
                floors=floors,
                building_height_m=round(height_m, 1),
                footprint_area_m2=round(footprint_area, 1),
                gross_bta_m2=round(gross_bta, 1),
                saleable_area_m2=round(saleable_area, 1),
                footprint_width_m=placement["footprint_width_m"],
                footprint_depth_m=placement["footprint_depth_m"],
                buildable_area_m2=round(geodata_context["buildable_area_m2"], 1),
                open_space_ratio=round(open_space_ratio, 3),
                target_fit_pct=round(target_fit_pct, 1),
                unit_count=unit_count,
                mix_counts=mix_counts,
                parking_spaces=parking_spaces,
                parking_pressure_pct=round(parking_pressure_pct, 1),
                solar_score=round(solar["solar_score"], 1),
                estimated_equinox_sun_hours=round(solar["estimated_equinox_sun_hours"], 1),
                estimated_winter_sun_hours=round(solar["estimated_winter_sun_hours"], 1),
                sunlit_open_space_pct=round(solar["sunlit_open_space_pct"], 1),
                winter_noon_shadow_m=round(solar["winter_noon_shadow_m"], 1),
                equinox_noon_shadow_m=round(solar["equinox_noon_shadow_m"], 1),
                summer_afternoon_shadow_m=round(solar["summer_afternoon_shadow_m"], 1),
                efficiency_ratio=round(actual_efficiency, 3),
                neighbor_count=len(neighbors),
                terrain_slope_pct=round(float((terrain or {}).get("slope_pct", 0.0)), 1),
                terrain_relief_m=round(float((terrain or {}).get("relief_m", 0.0)), 1),
                notes=notes,
                score=score,
                geometry=geometry,
            )
        )

    options.sort(key=lambda option: option.score, reverse=True)
    return options


def render_plan_diagram(site: SiteInputs, option: OptionResult) -> Image.Image:
    """
    Isometrisk 3D-volumskisse.
    Viser foreslatte volumer, nabobygg og tomtegrense fra skraa vinkel
    slik at siktlinjer, hoyder og romlige forhold er tydelige.
    """
    canvas_w, canvas_h = 1100, 780
    img = Image.new('RGBA', (canvas_w, canvas_h), (6, 17, 26, 255))
    draw = ImageDraw.Draw(img, 'RGBA')
    font = ImageFont.load_default()

    site_coords = option.geometry.get('site_polygon_coords') or geometry_to_coord_groups(box(0, 0, site.site_width_m, site.site_depth_m))
    buildable_coords = option.geometry.get('buildable_polygon_coords') or site_coords
    footprint_coords = option.geometry.get('footprint_polygon_coords') or []
    shadow_coords = option.geometry.get('winter_shadow_polygon_coords') or []
    neighbor_polys = option.geometry.get('neighbor_polygons', [])
    massing_parts = option.geometry.get('massing_parts', []) or []

    # --- Isometrisk projeksjon ---
    ISO_ANGLE = math.radians(30)
    COS_A = math.cos(ISO_ANGLE)
    SIN_A = math.sin(ISO_ANGLE)
    Z_SCALE = 1.5

    site_pts = flatten_coord_groups(site_coords)
    if not site_pts:
        site_pts = [[0.0, 0.0], [site.site_width_m, site.site_depth_m]]
    sxs = [p[0] for p in site_pts]
    sys_ = [p[1] for p in site_pts]
    cx = (min(sxs) + max(sxs)) / 2.0
    cy = (min(sys_) + max(sys_)) / 2.0
    site_span = max(max(sxs) - min(sxs), max(sys_) - min(sys_), 1.0)

    target_screen_span = min(canvas_w, canvas_h) * 0.48
    pixel_scale = target_screen_span / site_span

    screen_cx = canvas_w * 0.50
    screen_cy = canvas_h * 0.50

    def iso_project(x: float, y: float, z: float = 0.0) -> Tuple[float, float]:
        dx = (x - cx) * pixel_scale
        dy = (y - cy) * pixel_scale
        sx = screen_cx + (dx - dy) * COS_A
        sy = screen_cy + (dx + dy) * SIN_A * 0.5 - z * pixel_scale * Z_SCALE * 0.01
        return sx, sy

    def iso_pts(coords, z=0.0):
        return [iso_project(p[0], p[1], z) for p in coords if len(p) >= 2]

    def darken(c, f):
        return (int(c[0]*f), int(c[1]*f), int(c[2]*f), int(c[3]) if len(c)>3 else 255)

    def lighten(c, a):
        return (min(255,int(c[0]+a)), min(255,int(c[1]+a)), min(255,int(c[2]+a)), int(c[3]) if len(c)>3 else 255)

    def draw_iso_flat(coords, z, fill, outline, w=1):
        pts = iso_pts(coords, z)
        if len(pts) < 3:
            return
        draw.polygon(pts, fill=fill, outline=outline)
        if w > 1:
            draw.line(pts + [pts[0]], fill=outline, width=w)

    def draw_extruded(coords, h, top_c, side_c, out_c, w=1):
        if not coords or len(coords) < 3 or h <= 0:
            return 0.0
        top_pts = iso_pts(coords, h)
        base_pts = iso_pts(coords, 0.0)
        if len(top_pts) < 3:
            return 0.0
        n = len(coords)
        for i in range(n):
            j = (i + 1) % n
            bt0, bt1 = base_pts[i], base_pts[j]
            tp0, tp1 = top_pts[i], top_pts[j]
            edge_dx = bt1[0] - bt0[0]
            edge_dy = bt1[1] - bt0[1]
            if edge_dy < 0 or (edge_dy == 0 and edge_dx > 0):
                draw.polygon([bt0, bt1, tp1, tp0], fill=darken(side_c, 0.60), outline=out_c)
            elif edge_dx > 0 or edge_dy > 0:
                draw.polygon([bt0, bt1, tp1, tp0], fill=side_c, outline=out_c)
        draw.polygon(top_pts, fill=top_c, outline=out_c)
        if w > 1:
            draw.line(top_pts + [top_pts[0]], fill=out_c, width=w)
        return sum(p[0] for p in coords)/len(coords) - cx + sum(p[1] for p in coords)/len(coords) - cy

    # --- Samle volumer for depth-sorting ---
    volumes = []
    view_radius = site_span * 0.65
    for neighbor in neighbor_polys:
        ncoords = flatten_coord_groups(neighbor.get('coords', []))
        if not ncoords:
            continue
        avg_x = sum(p[0] for p in ncoords) / len(ncoords)
        avg_y = sum(p[1] for p in ncoords) / len(ncoords)
        if math.hypot(avg_x - cx, avg_y - cy) > view_radius:
            continue
        volumes.append({'coords': ncoords, 'height_m': float(neighbor.get('height_m', 9.0)),
                        'type': 'neighbor', 'depth': (avg_x - cx) + (avg_y - cy)})

    if massing_parts:
        for part in massing_parts:
            pcoords = flatten_coord_groups(part.get('coords', []))
            if not pcoords:
                continue
            avg_x = sum(p[0] for p in pcoords) / len(pcoords)
            avg_y = sum(p[1] for p in pcoords) / len(pcoords)
            volumes.append({'coords': pcoords, 'height_m': float(part.get('height_m', option.building_height_m)),
                            'name': part.get('name', option.typology),
                            'color': tuple(part.get('color', [34,197,94,200])),
                            'floors': int(part.get('floors', option.floors)),
                            'type': 'proposed', 'depth': (avg_x - cx) + (avg_y - cy)})
    else:
        fcoords = flatten_coord_groups(footprint_coords)
        if fcoords:
            avg_x = sum(p[0] for p in fcoords) / len(fcoords)
            avg_y = sum(p[1] for p in fcoords) / len(fcoords)
            volumes.append({'coords': fcoords, 'height_m': option.building_height_m,
                            'name': option.typology, 'color': (34,197,94,200),
                            'floors': option.floors, 'type': 'proposed',
                            'depth': (avg_x - cx) + (avg_y - cy)})

    volumes.sort(key=lambda v: v['depth'])

    # --- TEGNING ---
    # Himmelgradient
    for row in range(canvas_h // 2):
        t = row / (canvas_h / 2.0)
        draw.line([(0, row), (canvas_w, row)], fill=(int(6+t*10), int(17+t*18), int(26+t*28), 255))

    # Bakkeplan: tomt
    draw_iso_flat(flatten_coord_groups(site_coords), 0.0, (15,28,42,200), (80,100,130,180), 2)
    draw_iso_flat(flatten_coord_groups(buildable_coords), 0.0, (56,189,248,15), (56,189,248,80), 1)
    draw_iso_flat(flatten_coord_groups(shadow_coords), 0.0, (255,213,79,20), (255,213,79,50), 1)

    # Volumer
    for vol in volumes:
        coords, h = vol['coords'], vol['height_m']
        if vol['type'] == 'neighbor':
            alpha = min(180, int(80 + h * 6))
            draw_extruded(coords, h, (130,140,155,alpha), (100,110,125,alpha), (160,170,185,min(220,alpha+30)), 1)
        else:
            base = vol.get('color', (34,197,94,200))
            base = tuple(int(v) if v > 1 else int(v * 255) for v in base)  # handle 0.0-1.0 alpha
            if len(base) < 4:
                base = (base[0], base[1], base[2], 220)
            draw_extruded(coords, h, (int(base[0]),int(base[1]),int(base[2]),230), darken(base, 0.72), lighten(base, 50), 2)
            # Hoyde-label
            avg_x = sum(p[0] for p in coords) / len(coords)
            avg_y = sum(p[1] for p in coords) / len(coords)
            lx, ly = iso_project(avg_x, avg_y, h * 1.08)
            floors = vol.get('floors', 0)
            draw.text((lx - 22, ly - 10), f"{floors}et / {h:.0f}m", fill=(255,255,255,240), font=font)

    # Nordpil
    ax, ay = canvas_w - 55, 50
    draw.line((ax, ay+22, ax, ay-16), fill=(245,247,251,200), width=3)
    draw.polygon([(ax, ay-25), (ax-7, ay-7), (ax+7, ay-7)], fill=(245,247,251,200))
    draw.text((ax-4, ay+26), 'N', fill=(245,247,251,180), font=font)

    # Infopanel
    yt = canvas_h - 75
    draw.rectangle([(0, yt-4), (canvas_w, canvas_h)], fill=(6,17,26,230))
    n_parts = len(massing_parts)
    title = f"{option.name} | {option.typology}"
    if n_parts > 1:
        title += f" | {n_parts} deler"
    draw.text((30, yt), title, fill=(245,247,251,255), font=font)
    draw.text((30, yt+16), f"BTA {option.gross_bta_m2:.0f} m2 | {option.unit_count} boliger | {option.floors} et. | Hoyde {option.building_height_m:.1f} m | Sol {option.solar_score:.0f}/100", fill=(200,211,223,255), font=font)
    draw.text((30, yt+32), f"Fotavtrykk {option.footprint_area_m2:.0f} m2 | Uteareal sol {option.sunlit_open_space_pct:.0f}% | Naboer {option.neighbor_count} | Byggefelt {option.buildable_area_m2:.0f} m2", fill=(159,176,195,255), font=font)
    draw.text((30, yt+48), f"Vinterskygge {option.winter_noon_shadow_m:.0f} m | Score {option.score:.0f}/100 | {option.geometry.get('site_source', '')}", fill=(130,145,165,255), font=font)

    return img.convert('RGB')


def build_geodata_scene_payload(site: SiteInputs, option: OptionResult, scene_config: Dict[str, Any]) -> Dict[str, Any]:
    geometry = option.geometry or {}
    src_crs = site.polygon_crs or 'EPSG:25833'
    site_rings = project_coord_groups_to_lonlat(geometry.get('site_polygon_coords') or [], src_crs=src_crs)
    buildable_rings = project_coord_groups_to_lonlat(geometry.get('buildable_polygon_coords') or [], src_crs=src_crs)
    footprint_rings = project_coord_groups_to_lonlat(geometry.get('footprint_polygon_coords') or [], src_crs=src_crs)

    massing_parts = []
    for part in geometry.get('massing_parts', []) or []:
        massing_parts.append({
            'name': part.get('name', option.typology),
            'height_m': float(part.get('height_m', option.building_height_m)),
            'floors': int(part.get('floors', option.floors)),
            'color': part.get('color', [34, 197, 94, 0.80]),
            'rings': project_coord_groups_to_lonlat(part.get('coords') or [], src_crs=src_crs),
        })

    neighbors = []
    for neighbor in geometry.get('neighbor_polygons', []) or []:
        neighbors.append({
            'height_m': float(neighbor.get('height_m', 9.0)),
            'distance_m': float(neighbor.get('distance_m', 0.0)),
            'rings': project_coord_groups_to_lonlat(neighbor.get('coords') or [], src_crs=src_crs),
        })

    # Beregn senterpunkt i lon/lat for kameraposisjon
    site_centroid_lonlat = [10.75, 59.91]  # fallback Oslo
    all_site_pts = [pt for ring in site_rings for pt in ring]
    if all_site_pts:
        avg_lon = sum(pt[0] for pt in all_site_pts) / len(all_site_pts)
        avg_lat = sum(pt[1] for pt in all_site_pts) / len(all_site_pts)
        site_centroid_lonlat = [avg_lon, avg_lat]

    return {
        'site_name': option.name,
        'typology': option.typology,
        'scene_config': scene_config,
        'site_centroid': site_centroid_lonlat,
        'site': {'rings': site_rings},
        'buildable': {'rings': buildable_rings},
        'footprint': {'rings': footprint_rings, 'height_m': float(option.building_height_m)},
        'massing_parts': massing_parts,
        'neighbors': neighbors[:40],
        'shadow': {'rings': project_coord_groups_to_lonlat(geometry.get('winter_shadow_polygon_coords') or [], src_crs=src_crs)},
        'placement': geometry.get('placement', {}),
    }


def render_geodata_scene(site: SiteInputs, option: OptionResult, scene_config: Dict[str, Any], height_px: int = 640) -> None:
    payload = build_geodata_scene_payload(site, option, scene_config)
    payload_json = json.dumps(payload, ensure_ascii=False)
    html_template = """
    <div id="viewDiv" style="width:100%;height:__HEIGHT__px;border-radius:14px;overflow:hidden;border:1px solid rgba(255,255,255,0.08);"></div>
    <script src="https://js.arcgis.com/4.30/"></script>
    <link rel="stylesheet" href="https://js.arcgis.com/4.30/esri/themes/dark/main.css">
    <script>
    const payload = __PAYLOAD__;
    require([
      'esri/Map',
      'esri/views/SceneView',
      'esri/layers/ImageryLayer',
      'esri/layers/ElevationLayer',
      'esri/layers/GraphicsLayer',
      'esri/Graphic',
      'esri/geometry/Polygon',
      'esri/geometry/SpatialReference',
      'esri/geometry/Extent',
      'esri/identity/IdentityManager'
    ], function(Map, SceneView, ImageryLayer, ElevationLayer, GraphicsLayer, Graphic, Polygon, SpatialReference, Extent, IdentityManager) {
      const sr = new SpatialReference({ wkid: 4326 });
      const sc = payload.scene_config || {};
      const services = sc.services || {};
      const tkn = sc.token || '';

      // Pre-register token for all Geodata Online services
      if (tkn) {
        IdentityManager.registerToken({
          server: 'https://services.geodataonline.no/arcgis',
          token: tkn
        });
      }

      const map = new Map({ basemap: 'satellite', ground: 'world-elevation' });
      if (services.elevation_url && tkn) {
        map.ground.layers.add(new ElevationLayer({ url: services.elevation_url, customParameters: { token: tkn } }));
      }
      if (services.imagery_latest_url && tkn) {
        map.add(new ImageryLayer({ url: services.imagery_latest_url, opacity: 0.92, customParameters: { token: tkn } }));
      }
      const graphicsLayer = new GraphicsLayer();
      map.add(graphicsLayer);

      function polygonFromRings(rings) {
        return new Polygon({ rings: rings, spatialReference: sr });
      }
      function addExtrusions(items, fallbackColor, opacity, edgeColor) {
        (items || []).forEach(item => {
          (item.rings || []).forEach(ring => {
            const polygon = polygonFromRings([ring]);
            const useColor = Array.isArray(item.color) ? [item.color[0], item.color[1], item.color[2], opacity] : [fallbackColor[0], fallbackColor[1], fallbackColor[2], opacity];
            graphicsLayer.add(new Graphic({
              geometry: polygon,
              symbol: {
                type: 'polygon-3d',
                symbolLayers: [{
                  type: 'extrude',
                  size: item.height_m || 3,
                  material: { color: useColor },
                  edges: { type: 'solid', color: edgeColor || [255,255,255,0.35], size: 0.6 }
                }]
              },
              popupTemplate: { title: item.name || payload.typology, content: 'Høyde: ' + (item.height_m || 0) + ' m' }
            }));
          });
        });
      }
      function addSurface(rings, fillColor, outlineColor) {
        (rings || []).forEach(ring => {
          graphicsLayer.add(new Graphic({
            geometry: polygonFromRings([ring]),
            symbol: { type: 'simple-fill', color: fillColor, outline: { color: outlineColor, width: 1.2 } }
          }));
        });
      }

      addSurface(payload.site.rings, [255,255,255,0.02], [210,220,235,0.6]);
      addSurface(payload.buildable.rings, [56,189,248,0.08], [56,189,248,0.8]);
      addSurface(payload.shadow.rings, [255,213,79,0.10], [255,213,79,0.28]);
      addExtrusions(payload.neighbors, [140,140,150], 0.32, [200,200,210,0.25]);
      addExtrusions(payload.massing_parts, [34,197,94], 0.82, [255,255,255,0.45]);

      let extent = null;
      const allRings = [].concat(payload.site.rings || [], payload.buildable.rings || [], payload.footprint.rings || []);
      allRings.forEach(ring => {
        ring.forEach(pt => {
          const x = pt[0], y = pt[1];
          if (!extent) {
            extent = new Extent({ xmin: x, ymin: y, xmax: x, ymax: y, spatialReference: sr });
          } else {
            extent.xmin = Math.min(extent.xmin, x);
            extent.ymin = Math.min(extent.ymin, y);
            extent.xmax = Math.max(extent.xmax, x);
            extent.ymax = Math.max(extent.ymax, y);
          }
        });
      });

      const centroid = payload.site_centroid || [10.75, 59.91];
      const view = new SceneView({
        container: 'viewDiv',
        map: map,
        qualityProfile: 'high',
        camera: { position: { x: centroid[0], y: centroid[1], z: 900 }, tilt: 68, heading: 20, spatialReference: sr },
        environment: { atmosphereEnabled: true, starsEnabled: false }
      });
      view.when(() => { if (extent) { view.goTo(extent.expand(1.8)).catch(() => {}); } });
    });
    </script>
    """
    html = html_template.replace('__PAYLOAD__', payload_json).replace('__HEIGHT__', str(int(height_px)))
    components.html(html, height=height_px + 20, scrolling=False)


def render_interactive_3d(site: SiteInputs, option: OptionResult, height_px: int = 650, terrain_ctx: Optional[Dict[str, Any]] = None) -> None:
    """Interaktiv Three.js 3D-modell med terreng, nabobygg og volumalternativer."""
    geometry = option.geometry or {}
    site_coords = geometry.get('site_polygon_coords') or []
    buildable_coords = geometry.get('buildable_polygon_coords') or []
    footprint_coords = geometry.get('footprint_polygon_coords') or []
    neighbor_polys = geometry.get('neighbor_polygons', [])
    massing_parts = geometry.get('massing_parts', []) or []

    flat_site = flatten_coord_groups(site_coords)
    if not flat_site:
        st.warning("Ingen tomtegeometri tilgjengelig for 3D-visning.")
        return
    center_x = sum(p[0] for p in flat_site) / len(flat_site)
    center_y = sum(p[1] for p in flat_site) / len(flat_site)
    site_span = max(
        max(p[0] for p in flat_site) - min(p[0] for p in flat_site),
        max(p[1] for p in flat_site) - min(p[1] for p in flat_site),
        1.0
    )

    def to_local(groups):
        out = []
        for ring in groups:
            local_ring = []
            for pt in ring:
                local_ring.append([round(pt[0] - center_x, 2), round(pt[1] - center_y, 2)])
            out.append(local_ring)
        return out

    scene_data = {
        "site_span": round(site_span, 1),
        "site_rings": to_local(flatten_coord_groups(site_coords) and [flatten_coord_groups(site_coords)] or []),
        "buildable_rings": to_local(flatten_coord_groups(buildable_coords) and [flatten_coord_groups(buildable_coords)] or []),
        "volumes": [],
        "neighbors": [],
        "terrain": None,
    }

    # Terrengdata
    if terrain_ctx and terrain_ctx.get('sample_points'):
        samples = terrain_ctx['sample_points']
        min_elev = terrain_ctx.get('min_elev_m', 0.0)
        scene_data["terrain"] = {
            "points": [
                {"x": round(s["x"] - center_x, 2), "y": round(s["y"] - center_y, 2), "z": round(s["z"] - min_elev, 2)}
                for s in samples
            ],
            "min_elev": round(float(min_elev), 2),
            "max_elev": round(float(terrain_ctx.get('max_elev_m', min_elev)), 2),
            "relief": round(float(terrain_ctx.get('relief_m', 0)), 2),
            "a": float(terrain_ctx.get('a', 0)),
            "b": float(terrain_ctx.get('b', 0)),
            "c": float(terrain_ctx.get('c', 0)),
            "center_x": round(center_x, 2),
            "center_y": round(center_y, 2),
        }

    if massing_parts:
        for part in massing_parts:
            pc = flatten_coord_groups(part.get('coords', []))
            if not pc:
                continue
            color = part.get('color', [34, 197, 94, 200])
            scene_data["volumes"].append({
                "rings": to_local([pc]),
                "height": float(part.get('height_m', option.building_height_m)),
                "name": part.get('name', option.typology),
                "color": [int(c) for c in color[:3]],
                "floors": int(part.get('floors', option.floors)),
            })
    else:
        fc = flatten_coord_groups(footprint_coords)
        if fc:
            scene_data["volumes"].append({
                "rings": to_local([fc]),
                "height": float(option.building_height_m),
                "name": option.typology,
                "color": [34, 197, 94],
                "floors": int(option.floors),
            })

    view_r = site_span * 0.7
    for nb in neighbor_polys:
        nc = flatten_coord_groups(nb.get('coords', []))
        if not nc:
            continue
        avg_x = sum(p[0] for p in nc) / len(nc) - center_x
        avg_y = sum(p[1] for p in nc) / len(nc) - center_y
        if math.hypot(avg_x, avg_y) > view_r:
            continue
        scene_data["neighbors"].append({
            "rings": to_local([nc]),
            "height": float(nb.get('height_m', 9.0)),
        })

    payload_json = json.dumps(scene_data, ensure_ascii=False)

    html = """
<!DOCTYPE html>
<html><head><style>
  body { margin: 0; overflow: hidden; background: #060d14; }
  canvas { display: block; }
  #info {
    position: absolute; bottom: 10px; left: 14px; color: #b0bec5;
    font: 11px/1.4 -apple-system, sans-serif; pointer-events: none;
    text-shadow: 0 1px 3px rgba(0,0,0,0.7);
  }
  #help {
    position: absolute; top: 10px; right: 14px; color: #78909c;
    font: 10px/1.3 -apple-system, sans-serif; pointer-events: none;
    text-align: right;
  }
</style></head><body>
<div id="info">__INFO__</div>
<div id="help">Venstre mus: roter<br>Scroll: zoom<br>Shift+dra: panorer<br>Hoyre mus: panorer</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
const D = __DATA__;
const W = window.innerWidth, H = __HEIGHT__;
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0a1628);
scene.fog = new THREE.FogExp2(0x0a1628, 0.0008);

const camera = new THREE.PerspectiveCamera(50, W / H, 0.5, D.site_span * 10);
const camDist = D.site_span * 0.85;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(W, H);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.1;
document.body.appendChild(renderer.domElement);

// Lys
scene.add(new THREE.AmbientLight(0x8899aa, 0.5));
const sun = new THREE.DirectionalLight(0xfff5e0, 1.1);
sun.position.set(camDist * 0.6, camDist * 1.4, camDist * 0.9);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
const sh = D.site_span * 0.9;
sun.shadow.camera.left = -sh; sun.shadow.camera.right = sh;
sun.shadow.camera.top = sh; sun.shadow.camera.bottom = -sh;
sun.shadow.camera.near = 0.5; sun.shadow.camera.far = D.site_span * 4;
scene.add(sun);
scene.add(new THREE.DirectionalLight(0x99bbdd, 0.25).translateX(-camDist).translateY(camDist * 0.3));
scene.add(new THREE.HemisphereLight(0x8899cc, 0x334422, 0.3));

// --- TERRENG ---
const terrainGroup = new THREE.Group();
scene.add(terrainGroup);

function getTerrainY(lx, ly) {
  // Bruk regresjonsplan for aa beregne terrenghoeyde
  if (!D.terrain) return 0;
  const t = D.terrain;
  const wx = lx + t.center_x;
  const wy = ly + t.center_y;
  return (t.a * wx + t.b * wy + t.c) - t.min_elev;
}

if (D.terrain && D.terrain.points && D.terrain.points.length >= 3) {
  const t = D.terrain;
  const gridSize = D.site_span * 2.5;
  const segs = 60;
  const geo = new THREE.PlaneGeometry(gridSize, gridSize, segs, segs);
  const positions = geo.attributes.position.array;
  const colors = new Float32Array(positions.length);

  const maxRelief = Math.max(t.relief, 1.0);

  for (let i = 0; i < positions.length; i += 3) {
    const lx = positions[i];
    const ly = positions[i + 1];
    const elev = getTerrainY(lx, ly);
    positions[i + 2] = elev;

    // Fargegradient basert paa hoeyde
    const frac = Math.max(0, Math.min(1, elev / maxRelief));
    colors[i]     = 0.12 + frac * 0.15;  // R
    colors[i + 1] = 0.22 + frac * 0.12;  // G
    colors[i + 2] = 0.10 + frac * 0.06;  // B
  }

  geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geo.computeVertexNormals();

  const mat = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.92,
    metalness: 0.0,
    side: THREE.DoubleSide,
    flatShading: false,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.rotation.x = -Math.PI / 2;
  mesh.receiveShadow = true;
  terrainGroup.add(mesh);

  // Hoeydelinjer (konturlinjer)
  const contourInterval = Math.max(0.5, maxRelief / 8);
  for (let elev = contourInterval; elev < maxRelief; elev += contourInterval) {
    const pts = [];
    const halfGrid = gridSize / 2;
    const step = gridSize / 80;
    for (let x = -halfGrid; x <= halfGrid; x += step) {
      for (let y = -halfGrid; y <= halfGrid; y += step) {
        const z = getTerrainY(x, y);
        if (Math.abs(z - elev) < contourInterval * 0.12) {
          pts.push(new THREE.Vector3(x, elev + 0.05, y));
        }
      }
    }
    if (pts.length > 5) {
      const cGeo = new THREE.BufferGeometry().setFromPoints(pts);
      const cMat = new THREE.PointsMaterial({ color: 0x5a6a5a, size: 0.6, transparent: true, opacity: 0.35 });
      terrainGroup.add(new THREE.Points(cGeo, cMat));
    }
  }
} else {
  // Flatt bakkeplan hvis ingen terrengdata
  const gGeo = new THREE.PlaneGeometry(D.site_span * 4, D.site_span * 4);
  const gMat = new THREE.MeshStandardMaterial({ color: 0x1a2a3a, roughness: 0.95 });
  const gMesh = new THREE.Mesh(gGeo, gMat);
  gMesh.rotation.x = -Math.PI / 2;
  gMesh.position.y = -0.05;
  gMesh.receiveShadow = true;
  terrainGroup.add(gMesh);
}

// Grid
const grid = new THREE.GridHelper(D.site_span * 2.5, 30, 0x2a3a4a, 0x1a2530);
grid.position.y = 0.01;
scene.add(grid);

function shapeFromRing(ring) {
  const shape = new THREE.Shape();
  ring.forEach((pt, i) => {
    if (i === 0) shape.moveTo(pt[0], pt[1]);
    else shape.lineTo(pt[0], pt[1]);
  });
  shape.closePath();
  return shape;
}

function addFlatPoly(rings, color, opacity, y) {
  (rings || []).forEach(ring => {
    if (ring.length < 3) return;
    const shape = shapeFromRing(ring);
    const geo = new THREE.ShapeGeometry(shape);
    const mat = new THREE.MeshStandardMaterial({
      color: color, transparent: true, opacity: opacity,
      roughness: 0.8, side: THREE.DoubleSide
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.y = y;
    mesh.receiveShadow = true;
    scene.add(mesh);
  });
}

function addVolume(rings, height, color, opacity, castShadow, baseY) {
  (rings || []).forEach(ring => {
    if (ring.length < 3) return;
    const shape = shapeFromRing(ring);
    const geo = new THREE.ExtrudeGeometry(shape, {
      depth: height, bevelEnabled: false
    });
    const mat = new THREE.MeshStandardMaterial({
      color: color, roughness: 0.50, metalness: 0.06,
      transparent: opacity < 1.0, opacity: opacity
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.y = baseY || 0;
    mesh.castShadow = castShadow;
    mesh.receiveShadow = true;
    scene.add(mesh);

    const edges = new THREE.EdgesGeometry(geo);
    const lineMat = new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.12 });
    const line = new THREE.LineSegments(edges, lineMat);
    line.rotation.x = -Math.PI / 2;
    line.position.y = baseY || 0;
    scene.add(line);
  });
}

// Tomtegrense
addFlatPoly(D.site_rings, 0x38bdf8, 0.15, 0.05);

// Nabobygg
D.neighbors.forEach(n => {
  const cx = n.rings[0] ? n.rings[0].reduce((s,p) => s + p[0], 0) / n.rings[0].length : 0;
  const cy = n.rings[0] ? n.rings[0].reduce((s,p) => s + p[1], 0) / n.rings[0].length : 0;
  const baseY = D.terrain ? getTerrainY(cx, cy) : 0;
  addVolume(n.rings, n.height, 0x8a8e99, 0.50, false, baseY);
});

// Foreslatte volumer
D.volumes.forEach(v => {
  const c = new THREE.Color('rgb(' + v.color[0] + ',' + v.color[1] + ',' + v.color[2] + ')');
  const cx = v.rings[0] ? v.rings[0].reduce((s,p) => s + p[0], 0) / v.rings[0].length : 0;
  const cy = v.rings[0] ? v.rings[0].reduce((s,p) => s + p[1], 0) / v.rings[0].length : 0;
  const baseY = D.terrain ? getTerrainY(cx, cy) : 0;
  addVolume(v.rings, v.height, c, 0.92, true, baseY);

  // 3D-label
  const canvas2 = document.createElement('canvas');
  canvas2.width = 256; canvas2.height = 64;
  const ctx = canvas2.getContext('2d');
  ctx.fillStyle = 'rgba(0,0,0,0.6)';
  ctx.fillRect(0, 0, 256, 64);
  ctx.fillStyle = '#ffffff';
  ctx.font = 'bold 18px sans-serif';
  ctx.fillText(v.name + '  ' + v.floors + 'et / ' + v.height.toFixed(0) + 'm', 8, 24);
  ctx.font = '14px sans-serif';
  ctx.fillStyle = '#aabbcc';
  const tex = new THREE.CanvasTexture(canvas2);
  const spriteMat = new THREE.SpriteMaterial({ map: tex, transparent: true, opacity: 0.9 });
  const sprite = new THREE.Sprite(spriteMat);
  sprite.position.set(cx, baseY + v.height + D.site_span * 0.04, cy);
  sprite.scale.set(D.site_span * 0.22, D.site_span * 0.055, 1);
  scene.add(sprite);
});

// --- ORBIT CONTROLS ---
let isDown = false, isPan = false, prevX = 0, prevY = 0;
let theta = Math.PI / 4, phi = Math.PI / 4.5, radius = camDist;
const target = new THREE.Vector3(0, D.site_span * 0.04, 0);

function updateCamera() {
  camera.position.x = target.x + radius * Math.sin(phi) * Math.cos(theta);
  camera.position.y = target.y + radius * Math.cos(phi);
  camera.position.z = target.z + radius * Math.sin(phi) * Math.sin(theta);
  camera.lookAt(target);
}
updateCamera();

renderer.domElement.addEventListener('mousedown', e => {
  isDown = true;
  isPan = e.button === 2 || e.shiftKey;
  prevX = e.clientX; prevY = e.clientY;
  e.preventDefault();
});
renderer.domElement.addEventListener('contextmenu', e => e.preventDefault());
window.addEventListener('mouseup', () => { isDown = false; });
window.addEventListener('mousemove', e => {
  if (!isDown) return;
  const dx = e.clientX - prevX, dy = e.clientY - prevY;
  prevX = e.clientX; prevY = e.clientY;
  if (isPan) {
    const panSpeed = radius * 0.002;
    const right = new THREE.Vector3();
    right.crossVectors(camera.up, new THREE.Vector3().subVectors(target, camera.position)).normalize();
    target.addScaledVector(right, dx * panSpeed);
    target.y -= dy * panSpeed;
    updateCamera();
  } else {
    theta -= dx * 0.006;
    phi = Math.max(0.08, Math.min(Math.PI / 2.1, phi + dy * 0.006));
    updateCamera();
  }
});
renderer.domElement.addEventListener('wheel', e => {
  radius = Math.max(D.site_span * 0.12, Math.min(D.site_span * 5, radius * (1 + e.deltaY * 0.001)));
  updateCamera();
  e.preventDefault();
}, { passive: false });

// Touch
let touchDist = 0;
renderer.domElement.addEventListener('touchstart', e => {
  if (e.touches.length === 1) {
    isDown = true; isPan = false;
    prevX = e.touches[0].clientX; prevY = e.touches[0].clientY;
  } else if (e.touches.length === 2) {
    touchDist = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
  }
  e.preventDefault();
}, { passive: false });
renderer.domElement.addEventListener('touchmove', e => {
  if (e.touches.length === 1 && isDown) {
    const dx = e.touches[0].clientX - prevX, dy = e.touches[0].clientY - prevY;
    prevX = e.touches[0].clientX; prevY = e.touches[0].clientY;
    theta -= dx * 0.006;
    phi = Math.max(0.08, Math.min(Math.PI / 2.1, phi + dy * 0.006));
    updateCamera();
  } else if (e.touches.length === 2) {
    const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
    radius = Math.max(D.site_span * 0.12, Math.min(D.site_span * 5, radius * (touchDist / Math.max(d, 1))));
    touchDist = d;
    updateCamera();
  }
  e.preventDefault();
}, { passive: false });
renderer.domElement.addEventListener('touchend', () => { isDown = false; });

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / H;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, H);
});

function animate() {
  requestAnimationFrame(animate);
  renderer.render(scene, camera);
}
animate();
</script></body></html>
""".replace('__DATA__', payload_json).replace('__HEIGHT__', str(int(height_px))).replace(
        '__INFO__',
        f"{option.name} | {option.typology} | BTA {option.gross_bta_m2:.0f} m2 | {option.unit_count} boliger"
        + (f" | Terreng: {terrain_ctx.get('relief_m', 0):.1f}m relieff" if terrain_ctx and terrain_ctx.get('relief_m') else "")
    )

    components.html(html, height=height_px + 10, scrolling=False)


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
            "- Kontroller tomtepolygon, byggegrenser, BYA/BRA og evt. terreng- eller nabodata.\n"
        )

    best = options[0]
    using_polygon = site.site_geometry_source not in {"Rektangulert fallback", "Rektangel", "Manuell rektangeltomt"}
    terrain_active = best.terrain_relief_m > 0.0 or best.terrain_slope_pct > 0.0
    lines = []
    lines.append("# 1. OPPSUMMERING")
    lines.append(
        f"Beste indikative alternativ er {best.name} ({best.typology}) med score {best.score}/100. "
        f"Det gir omtrent {best.gross_bta_m2:.0f} m2 BTA, {best.saleable_area_m2:.0f} m2 salgbart areal "
        f"og ca. {best.unit_count} boliger innenfor dagens oppgitte rammer."
    )
    if using_polygon:
        lines.append(
            f"Analysen bruker faktisk tomtepolygon ({site.site_geometry_source}), reelt byggefelt på ca. {best.buildable_area_m2:.0f} m2 "
            f"og {best.neighbor_count} nabobygg i sol/skygge-vurderingen."
        )
    if terrain_active:
        lines.append(
            f"Terrenggrunnlag er brukt som en forenklet flate med ca. {best.terrain_slope_pct:.1f}% gjennomsnittlig fall og "
            f"{best.terrain_relief_m:.1f} m lokalt relieff."
        )
    lines.append("")
    lines.append("# 2. GRUNNLAG")
    lines.append(f"- Tomteareal brukt i motor: {site.site_area_m2:.0f} m2")
    lines.append(f"- Tomtedimensjon (omsluttende orientert rektangel): ca. {site.site_width_m:.1f} x {site.site_depth_m:.1f} m")
    lines.append(
        f"- Byggegrenser / inntrekk: front {site.front_setback_m:.1f} m, bak {site.rear_setback_m:.1f} m, side {site.side_setback_m:.1f} m, "
        f"polygonbuffer {site.polygon_setback_m:.1f} m"
    )
    lines.append(f"- Maks BYA: {site.max_bya_pct:.1f}%")
    lines.append(f"- Maks BRA: {'ikke satt' if site.max_bra_m2 <= 0 else f'{site.max_bra_m2:.0f} m2'}")
    lines.append(f"- Maks etasjer: {site.max_floors}")
    lines.append(f"- Maks høyde: {site.max_height_m:.1f} m")
    lines.append(f"- Ønsket BTA: {site.desired_bta_m2:.0f} m2")
    lines.append(f"- Solanalyse basert på breddegrad: {site.latitude_deg:.3f}")
    lines.append(f"- Geometrikilde: {site.site_geometry_source}")
    lines.append(f"- Nabobygg brukt i analysen: {site.neighbor_count}")
    lines.append(f"- Visuelt grunnlag lastet opp: {'ja' if has_visual_input else 'nei'}")
    if site.polygon_crs:
        lines.append(f"- CRS / projeksjon for polygon: {site.polygon_crs}")
    if parsed_hints:
        lines.append(f"- Tolket fra fritekst: {json.dumps(parsed_hints, ensure_ascii=False)}")
    lines.append("")
    lines.append("# 3. VIKTIGSTE FORUTSETNINGER")
    lines.append("- Analysen er deterministisk og skjematisk; den erstatter ikke detaljert reguleringstolkning.")
    lines.append("- Sol/skygge er oppgradert til en 2.5D-vurdering med faktisk tomtepolygon, nabohøyder og enkel terrengflate når dette er lagt inn.")
    lines.append("- Leilighetsmiks beregnes ut fra salgbart areal og gjennomsnittsstørrelser, ikke full planløsning.")
    lines.append("- Terreng brukes som et regressjonsplan / forenklet flate, ikke full detaljmodell av murer, skjæringer eller støttemurer.")
    lines.append("")
    lines.append("# 4. TOMT OG KONTEKST")
    if using_polygon:
        lines.append(
            f"Tomten er analysert som faktisk polygon i stedet for rektangulær boks. Dette gir mer realistisk byggefelt, "
            f"bedre kontroll på fotavtrykk og en mer troverdig sol-/skyggevurdering mot omkringliggende volum."
        )
    else:
        lines.append(
            "Tomten er analysert som rektangulær fallback fordi faktisk polygon ikke er lastet inn. Resultatene er fortsatt nyttige, "
            "men geometrisk presisjon blir svakere enn med ekte tomtegrense."
        )
    if best.neighbor_count > 0:
        lines.append(
            f"Det er brukt {best.neighbor_count} nabobygg i analysen. Disse påvirker særlig solbelyst uteareal og vinter-/skuldersesongskygger."
        )
    else:
        lines.append("Det er ikke lagt inn nabovolumer, så skyggevurderingen gjelder primært eget volum på tomten.")
    if terrain_active:
        lines.append(
            f"Terrengflaten viser omtrent {best.terrain_slope_pct:.1f}% gjennomsnittlig fall. Dette påvirker adkomst, underetasje, parkering og skyggeutbredelse."
        )
    lines.append("")
    lines.append("# 5. REGULERINGSMESSIGE FORHOLD")
    lines.append(
        f"Maks fotavtrykk styres av kombinasjonen av BYA og faktisk byggefelt. I denne runden er beregnet buildbar flate ca. {best.buildable_area_m2:.0f} m2. "
        f"Høydebegrensning og etasjeantall gir et indikativt tak på {min(site.max_floors, max(1, int(site.max_height_m // max(site.floor_to_floor_m, 2.8))))} etasjer."
    )
    lines.append("")
    lines.append("# 6. ARKITEKTONISK VURDERING")
    lines.append(
        f"{best.typology} fremstår som sterkest i denne runden fordi kombinasjonen av volumtreff, solscore ({best.solar_score:.0f}/100), "
        f"solbelyst uteareal ({best.sunlit_open_space_pct:.0f}%) og utnyttelse av faktisk byggefelt er best balansert."
    )
    lines.append("")
    lines.append("# 7. MULIGE UTVIKLINGSGREP")
    for option in options:
        lines.append(
            f"- {option.name}: {option.typology}, {option.floors} etasjer, {option.gross_bta_m2:.0f} m2 BTA, "
            f"{option.unit_count} boliger, solscore {option.solar_score:.0f}/100 og ca. {option.sunlit_open_space_pct:.0f}% solbelyst uteareal."
        )
    lines.append("")
    lines.append("# 8. ALTERNATIVER")
    for option in options:
        lines.append(f"## {option.name}")
        lines.append(
            f"- Typologi: {option.typology}\n"
            f"- Fotavtrykk: {option.footprint_area_m2:.0f} m2 ({option.footprint_width_m:.1f} x {option.footprint_depth_m:.1f} m)\n"
            f"- Buildbar flate: {option.buildable_area_m2:.0f} m2\n"
            f"- BTA: {option.gross_bta_m2:.0f} m2\n"
            f"- Salgbart areal: {option.saleable_area_m2:.0f} m2\n"
            f"- Leiligheter: {option.unit_count} ({json.dumps(option.mix_counts, ensure_ascii=False)})\n"
            f"- Parkering: {option.parking_spaces} plasser\n"
            f"- Solbelyst uteareal (skuldersesong): ca. {option.sunlit_open_space_pct:.0f}%\n"
            f"- Vinterskygge kl 12: ca. {option.winter_noon_shadow_m:.0f} m\n"
            f"- Skuldersesong soltimer: ca. {option.estimated_equinox_sun_hours:.1f} timer"
        )
        for note in option.notes:
            lines.append(f"- {note}")
    lines.append("")
    lines.append("# 9. RISIKO OG AVKLARINGSPUNKTER")
    lines.append("- Verifiser reguleringsbestemmelser, kote, gesims, parkeringskrav og uteoppholdsareal mot faktisk plan.")
    if best.neighbor_count > 0 and "Geodata Online" in site.site_geometry_source:
        lines.append("- Nabohoyder er hentet fra Geodata Online ByggFlate og baserer seg paa registrerte etasjer i matrikkelen. "
                     "Verifiser mot faktisk situasjon for naerliggende bygg.")
    elif best.neighbor_count > 0:
        lines.append("- Nabohoyder fra GeoJSON/OSM maa kvalitetssikres dersom de skal brukes beslutningskritisk; OSM-data er ofte ufullstendig.")
    else:
        lines.append("- Ingen nabobygg er lagt inn. Skyggevurderingen gjelder kun eget volum. "
                     "For mer realistisk analyse, sjekk at Geodata Online er tilkoblet og at sokeradius er tilstrekkelig.")
    lines.append("- Terrengmodellen er forenklet og boer erstattes med detaljert kotegrunnlag hvis prosjektet gaar videre til konkret skisse.")
    lines.append("")
    lines.append("# 10. ANBEFALING / NESTE STEG")
    lines.append(
        f"Start videre bearbeiding med {best.name}. Neste steg er å finjustere kjerner og trapper, teste uteopphold og adkomst mot terreng, "
        f"og kontrollere kritiske skyggeforhold i en mer detaljert 3D-modell dersom prosjektet skal løftes videre."
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
    "Oppgradert modul med geospatial feasibility-motor: faktisk tomtepolygon, nabohoyder, terreng, volumalternativer og leilighetsmiks."
    "</p>",
    unsafe_allow_html=True,
)

if llm_available:
    st.success("AI-tekst er tilgjengelig. Tallsiden beregnes alltid deterministisk først.")
else:
    st.info("AI-tekst er ikke tilgjengelig akkurat na. Modulen kjører fortsatt hele feasibility-motoren deterministisk.")

for geo_note in geo_runtime_notes():
    st.warning(geo_note)

if geodata_token_ok:
    st.success("Geodata Online tilkoblet — tomtehenting, nabobygg, ortofoto tilgjengelig via GeomapMatrikkel.")
elif HAS_GEODATA_ONLINE and gdo is not None and gdo.is_available():
    st.warning("Geodata Online: token-generering feilet. Sjekk brukernavn/passord.")


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
    st.info(
        "Denne delen er ny: dere kan fortsatt bruke rektangulære fallback-tall, men modulen støtter nå også faktisk tomtepolygon, naboer og terreng."
    )
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
    site_area_m2 = d1.number_input("Tomteareal fallback (m2)", min_value=100.0, value=float(default_site_area), step=50.0)
    site_width_m = d2.number_input("Tomtebredde fallback (m)", min_value=10.0, value=45.0, step=1.0)
    site_depth_m = d3.number_input("Tomtedybde fallback (m)", min_value=10.0, value=55.0, step=1.0)

    s1, s2, s3, s4 = st.columns(4)
    front_setback_m = s1.number_input("Byggegrense mot gate / front (m)", min_value=0.0, value=4.0, step=0.5)
    rear_setback_m = s2.number_input("Bakre byggegrense (m)", min_value=0.0, value=4.0, step=0.5)
    side_setback_m = s3.number_input("Sideavstand (m)", min_value=0.0, value=4.0, step=0.5)
    polygon_setback_m = s4.number_input("Polygonbuffer / inntrekk (m)", min_value=0.0, value=4.0, step=0.5)

    r1, r2, r3, r4 = st.columns(4)
    max_bya_pct = r1.number_input("Maks BYA (%)", min_value=1.0, max_value=100.0, value=float(parsed.get("max_bya_pct", 35.0)), step=1.0)
    max_bra_m2 = r2.number_input("Maks BRA (m2, 0 = ikke satt)", min_value=0.0, value=float(parsed.get("max_bra_m2", 0.0)), step=50.0)
    max_floors = r3.number_input("Maks etasjer", min_value=1, max_value=30, value=int(parsed.get("max_floors", max(3, int(pd_state.get("etasjer", 4))))), step=1)
    max_height_m = r4.number_input("Maks hoyde (m)", min_value=3.0, value=float(parsed.get("max_height_m", max(10.0, float(pd_state.get("etasjer", 4)) * 3.2))), step=0.5)

with st.expander("2B. Ekte tomtepolygon, nabohoyder og terreng", expanded=True):
    st.markdown("##### 1. Hent eiendom fra Geodata Online")
    if not geodata_token_ok:
        st.warning("Geodata Online er ikke tilkoblet. Sett GEODATA_ONLINE_USER og GEODATA_ONLINE_PASS.")
    st.info("Skriv inn kommune (f.eks. Trondheim eller 5001) og Gnr/Bnr. For flere tomter, separer med komma (f.eks. 15/2, 15/4).")

    c_k, c_g = st.columns(2)
    kommune_nr_input = c_k.text_input("Kommune (Navn eller 4-sifret nummer)", value=pd_state.get('kommune', ''))

    default_gnr_bnr = ""
    if pd_state.get('gnr') and pd_state.get('bnr'):
        default_gnr_bnr = f"{pd_state.get('gnr')}/{pd_state.get('bnr')}"

    gnr_bnr_input = c_g.text_input("Gnr/Bnr (Bruk komma for flere)", value=default_gnr_bnr)

    if st.button("Sok opp og lagre tomt", type="secondary"):
        if not kommune_nr_input or not gnr_bnr_input:
            st.warning("Fyll inn baade kommune og Gnr/Bnr.")
        elif not geodata_token_ok:
            st.error("Geodata Online er ikke tilkoblet. Kan ikke hente tomt.")
        else:
            pairs = []
            # Stoett komma-separert ("57/270, 57/156") og slash-separert ("57/270/57/156")
            raw = gnr_bnr_input.replace(" ", "")
            # Splitt paa komma foerst
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                segments = part.split("/")
                if len(segments) == 2:
                    # Standard: "57/270"
                    pairs.append((segments[0], segments[1]))
                elif len(segments) >= 4 and len(segments) % 2 == 0:
                    # Flere par i ett: "57/270/57/156" -> (57,270), (57,156)
                    for i in range(0, len(segments), 2):
                        pairs.append((segments[i], segments[i + 1]))
                elif len(segments) == 1 and segments[0].isdigit():
                    continue  # Bare et tall, ignorer
                else:
                    st.warning(f"Kunne ikke tolke '{part}'. Bruk formatet Gnr/Bnr (f.eks. 57/270).")
            if not pairs:
                st.warning("Ugyldig format. Bruk formatet 15/2.")
            else:
                with st.spinner("Henter tomt fra Geodata Online (DekTeigFlate)..."):
                    knr = get_kommunenummer(kommune_nr_input) or kommune_nr_input.strip().zfill(4)
                    poly, msg = gdo.fetch_tomt_polygon(knr, pairs)
                    if poly:
                        st.session_state.auto_site_polygon = poly
                        st.session_state.auto_site_msg = msg
                        st.rerun()
                    else:
                        st.error(f"Feilet: {msg}")
                    
    if st.session_state.get("auto_site_polygon") is not None:
        st.success(f"✅ **Klar til bruk!** {st.session_state.get('auto_site_msg')} (Nøyaktig areal via UTM33: ca {int(st.session_state.auto_site_polygon.area)} m²)")
        if st.button("Tøm hentet tomt", type="secondary"):
            st.session_state.auto_site_polygon = None
            st.rerun()

    st.markdown("---")
    st.markdown("##### 📎 2. Eller bruk manuell opplasting")
    g1, g2 = st.columns(2)
    with g1:
        site_polygon_upload = st.file_uploader(
            "Last opp tomtepolygon (GeoJSON)",
            type=["geojson", "json"],
            key="site_polygon_geojson",
        )
    with g2:
        site_polygon_text = st.text_area(
            "Eller lim inn koordinater (x,y eller lon,lat per linje)",
            height=120,
            placeholder="597380,6643012\n597412,6643005\n597428,6643046\n...",
        )

    st.markdown("---")
    st.markdown("##### Nabobebyggelse")
    n1, n2, n3 = st.columns([1.5, 1, 1])
    neighbor_mode = n1.radio(
        "Kilde for naboer",
        ["Ingen", "Last opp GeoJSON", "Hent fra OSM rundt tomten"],
        horizontal=False,
    )
    default_neighbor_height_m = n2.number_input("Fallback nabohoeyde (m)", min_value=3.0, max_value=80.0, value=9.0, step=0.5)
    neighbor_radius_m = n3.number_input("Radius for nabosok (m)", min_value=30.0, max_value=400.0, value=160.0, step=10.0)
    neighbor_geojson = None
    if neighbor_mode == "Last opp GeoJSON":
        neighbor_geojson = st.file_uploader(
            "Nabobygg (GeoJSON med polygoner og gjerne height / levels / etasjer)",
            type=["geojson", "json"],
            key="neighbor_geojson",
        )

    st.markdown("##### Terreng")
    terrain_upload = st.file_uploader(
        "Terrenggrunnlag (CSV/TXT med x,y,z eller GeoTIFF/ASC)",
        type=["csv", "txt", "tif", "tiff", "asc"],
        key="terrain_upload",
    )
    st.caption(
        "GeoJSON for tomt/naboer kan ligge i lon/lat eller i meter. Terreng kan lastes opp som punktfil med x,y,z eller georeferert raster."
    )

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
                auto_poly = st.session_state.get("auto_site_polygon")
                bounds_for_map = auto_poly.bounds if auto_poly else None
                
                img, source = fetch_map_image(
                    pd_state.get("adresse", ""),
                    pd_state.get("kommune", ""),
                    pd_state.get("gnr", ""),
                    pd_state.get("bnr", ""),
                    google_key or "",
                    bounds=bounds_for_map,
                    _gdo_client=gdo if geodata_token_ok else None,
                )
                if img is not None:
                    st.session_state.ark_kart = img
                    st.success(f"Kart hentet! (Kilde: {source})")
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
- Leser **ekte tomtepolygon** via Geodata Online (GeomapMatrikkel/FeatureServer), GeoJSON eller koordinatliste.
- Regner **7 volumalternativer** (lamell, karre, punkthus, tarn, podium+tarn, tun/U-form og rekke) innenfor faktisk byggefelt.
- Lager **sammensatte volumdeler** som kan vises videre i 3D-scene.
- Bruker **Geodata site intelligence** for plan, utbygging og mobilitet i rangering av typologier.
- Kan vise volumene i **3D Geodata-scene** med Geodata-terreng som grunnlag.
- Leser **nabobebyggelse** via Geodata Online ByggFlate, GeoJSON eller OSM og bruker hoyder i sol/skygge.
- Henter **HD-ortofoto** fra Geodata Online for bedre kartgrunnlag i rapporten.
- Leser **terreng** via punktfil eller raster og estimerer fall/relieff.
- Degraderer kontrollert til fallback hvis geostacken i deployen mangler pyproj eller rasterio.
- Regner **fotavtrykk, BTA, salgbarhetsareal, boligantall, leilighetsmiks og parkeringstrykk**.
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

    auto_poly = st.session_state.get("auto_site_polygon")
    site_polygon_input, site_crs, polygon_meta = load_site_polygon_input(auto_poly, site_polygon_upload, site_polygon_text)
    
    # Skuddsikker lat/lon henting
    if auto_poly is not None and HAS_PYPROJ:
        try:
            centroid = auto_poly.centroid
            transformer = Transformer.from_crs(CRS.from_epsg(25833), CRS.from_epsg(4326), always_xy=True)
            lon_geocoded, lat_geocoded = transformer.transform(centroid.x, centroid.y)
            geo_source = "Geodata Online" if geodata_token_ok else "Kartverket Polygon"
        except:
            lat_geocoded, lon_geocoded, geo_source = fetch_lat_lon(pd_state.get("adresse", ""), pd_state.get("kommune", ""))
    else:
        lat_geocoded, lon_geocoded, geo_source = fetch_lat_lon(pd_state.get("adresse", ""), pd_state.get("kommune", ""))

    latitude_deg = lat_geocoded if lat_geocoded is not None else polygon_meta.get("centroid_lat", latitude_manual)
    longitude_deg = lon_geocoded if lon_geocoded is not None else polygon_meta.get("centroid_lon")

    # === GEODATA ONLINE: AUTOMATISK HENTING AV ALT ===
    neighbor_inputs: List[Dict[str, Any]] = []
    neighbor_meta: Dict[str, Any] = {"source": "Ingen naboer"}

    # A) Nabobygg fra ByggFlate — ALLTID naar GDO er tilkoblet og tomt finnes
    if geodata_token_ok and site_polygon_input is not None:
        with st.spinner("Henter nabobygg fra Geodata Online ByggFlate..."):
            try:
                fkb_buildings, fkb_meta = gdo.fetch_byggflater(
                    bbox=site_polygon_input.bounds,
                    buffer_m=float(neighbor_radius_m),
                )
                if fkb_buildings:
                    neighbor_inputs = geodata_buildings_to_neighbors(
                        fkb_buildings,
                        site_polygon=site_polygon_input,
                        max_distance_m=float(neighbor_radius_m) + 20,
                    )
                    neighbor_meta = fkb_meta
                    st.success(f"Hentet {len(neighbor_inputs)} nabobygg fra Geodata Online ByggFlate")
                else:
                    st.info("Ingen nabobygg funnet i ByggFlate innenfor sokeradius.")
            except Exception as exc:
                st.warning(f"ByggFlate feilet: {exc}")

    # B) Fallback til GeoJSON/OSM KUN hvis GDO ikke ga resultat
    if not neighbor_inputs:
        if neighbor_mode == "Last opp GeoJSON":
            neighbor_inputs, neighbor_meta = load_neighbors_from_geojson(
                neighbor_geojson,
                site_polygon_input,
                site_crs,
                default_neighbor_height_m,
            )
        elif neighbor_mode == "Hent fra OSM rundt tomten":
            neighbor_inputs, neighbor_meta = fetch_osm_neighbors(
                latitude_deg,
                longitude_deg,
                site_polygon_input,
                site_crs,
                neighbor_radius_m,
                default_neighbor_height_m,
            )

    # C) Ortofoto — AUTOMATISK naar GDO er tilkoblet og tomt finnes
    if geodata_token_ok and site_polygon_input is not None and st.session_state.ark_kart is None:
        with st.spinner("Henter HD-ortofoto fra Geodata Online..."):
            try:
                hd_img, hd_source = gdo.fetch_ortofoto(
                    bbox=site_polygon_input.bounds,
                    buffer_m=100.0,
                    width=1400,
                    height=1400,
                )
                if hd_img:
                    st.session_state.ark_kart = hd_img
                    images_for_context.append(hd_img)
                    st.success(f"HD-ortofoto hentet: {hd_source}")
            except Exception as exc:
                st.caption(f"Ortofoto-henting feilet: {exc}")

    terrain_ctx, terrain_meta = load_terrain_input(terrain_upload, site_polygon_input, site_crs)
    if terrain_ctx is None and geodata_token_ok and site_polygon_input is not None and gdo is not None:
        try:
            terrain_ctx = gdo.fetch_terrain_model(site_polygon_input, sample_spacing_m=10.0, max_points=180)
            if terrain_ctx is not None:
                terrain_meta = {'source': terrain_ctx.get('source', 'Geodata Online Terrengmodell'), 'point_count': terrain_ctx.get('point_count', 0)}
        except Exception as exc:
            terrain_meta = {'source': 'Geodata Online Terrengmodell', 'error': str(exc)[:120]}

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
        polygon_setback_m=polygon_setback_m,
        site_geometry_source=polygon_meta.get("source", "Rektangel"),
        polygon_crs=(site_crs.to_string() if site_crs is not None else polygon_meta.get("crs", "")),
        neighbor_count=len(neighbor_inputs),
        terrain_slope_pct=float((terrain_ctx or {}).get("slope_pct", 0.0)),
        terrain_relief_m=float((terrain_ctx or {}).get("relief_m", 0.0)),
    )

    geodata_context = prepare_site_context(
        site=site,
        site_polygon_input=site_polygon_input,
        polygon_setback_m=polygon_setback_m,
        neighbors=neighbor_inputs,
        terrain=terrain_ctx,
        polygon_meta=polygon_meta,
    )
    site.site_area_m2 = float(geodata_context["site_area_m2"])
    site.site_width_m = float(geodata_context["site_width_m"])
    site.site_depth_m = float(geodata_context["site_depth_m"])
    site.site_geometry_source = geodata_context.get("source", site.site_geometry_source)
    site.neighbor_count = len(geodata_context.get("neighbors", []))
    site.terrain_slope_pct = float((terrain_ctx or {}).get("slope_pct", 0.0))
    site.terrain_relief_m = float((terrain_ctx or {}).get("relief_m", 0.0))

    site_intelligence_bundle: Dict[str, Any] = {}
    if HAS_SITE_INTELLIGENCE and geodata_token_ok and site_polygon_input is not None and gdo is not None:
        try:
            site_intelligence_bundle = build_site_intelligence_bundle(gdo, geodata_context['site_polygon'], search_buffer_m=350.0)
            geodata_context['site_intelligence'] = site_intelligence_bundle
        except Exception as exc:
            site_intelligence_bundle = {'available': False, 'error': str(exc)[:160]}

    ai_label = " + AI-plassering (Claude)" if HAS_AI_PLANNER else ""
    with st.spinner(f"Regner volumalternativer med faktisk tomtepolygon, naboer og terreng{ai_label} ..."):
        options = generate_options(site, mix_inputs, geodata_context=geodata_context)

    if HAS_SITE_INTELLIGENCE and site_intelligence_bundle.get('available'):
        options = apply_site_intelligence_to_options(options, site_intelligence_bundle)

    if not options:
        st.error("Klarte ikke å generere alternativer. Kontroller tomtepolygon, byggegrenser og BYA.")
        st.stop()

    option_images = [render_plan_diagram(site, option) for option in options]
    deterministic_report = build_deterministic_report(site, options, parsed, has_visual_input=bool(images_for_context))
    if HAS_SITE_INTELLIGENCE and site_intelligence_bundle.get('available'):
        deterministic_report = deterministic_report + "\n\n" + build_site_intelligence_markdown(site_intelligence_bundle)
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
                    "polygon_meta": polygon_meta,
                    "neighbor_meta": neighbor_meta,
                    "terrain_meta": terrain_meta,
                    "site_intelligence": site_intelligence_bundle,
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
- Sol/skygge skal omtales som indikativ 2.5D, ikke full detaljsimulering.
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
        "module": "ARK (Mulighetsstudie v3)",
        "drafter": "Builtly AI + Geospatial Feasibility Engine",
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
        "polygon_meta": polygon_meta,
        "neighbor_meta": neighbor_meta,
        "terrain_meta": terrain_meta,
        "terrain_ctx": terrain_ctx,
        "site_intelligence": site_intelligence_bundle,
    }
    st.session_state.generated_ark_pdf = pdf_bytes
    st.session_state.generated_ark_filename = f"Builtly_ARK_{p_name}_v3.pdf"

    # Save report to user dashboard
    try:
        from Builtly_AI_frontpage_access_gate_expanded import save_user_report
        save_user_report(
            project_name=st.session_state.get("project_data", {}).get("p_name", p_name),
            report_name=f"Mulighetsstudie — {p_name}",
            module="Mulighetsstudie",
            file_path=st.session_state.generated_ark_filename,
        )
    except ImportError:
        pass  # Frontpage not available (standalone run)

    st.rerun()


# --- 11. RENDER RESULTATER ---
if "analysis_results" in st.session_state:
    result = st.session_state.analysis_results
    options = []
    for option_data in result["options"]:
        options.append(OptionResult(**option_data))

    st.success("Mulighetsstudie er generert med faktisk tomtepolygon, nabohoyder og terreng der dette er lagt inn.")
    best = options[0]
    site_result = result.get("site", {})

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Anbefalt alternativ</div><div class='metric-value'>{}</div></div>".format(best.typology), unsafe_allow_html=True)
    with k2:
        st.markdown("<div class='kpi-card'><div class='metric-title'>BTA</div><div class='metric-value'>{:.0f} m2</div></div>".format(best.gross_bta_m2), unsafe_allow_html=True)
    with k3:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Boliger</div><div class='metric-value'>{}</div></div>".format(best.unit_count), unsafe_allow_html=True)
    with k4:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Solscore</div><div class='metric-value'>{:.0f}/100</div></div>".format(best.solar_score), unsafe_allow_html=True)

    g1, g2, g3, g4 = st.columns(4)
    with g1:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Geometrikilde</div><div class='metric-value'>{}</div></div>".format(site_result.get("site_geometry_source", "-")), unsafe_allow_html=True)
    with g2:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Buildbart areal</div><div class='metric-value'>{:.0f} m2</div></div>".format(best.buildable_area_m2), unsafe_allow_html=True)
    with g3:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Naboer brukt</div><div class='metric-value'>{}</div></div>".format(best.neighbor_count), unsafe_allow_html=True)
    with g4:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Terreng fall</div><div class='metric-value'>{:.1f}%</div></div>".format(best.terrain_slope_pct), unsafe_allow_html=True)

    polygon_meta = result.get("polygon_meta", {})
    neighbor_meta = result.get("neighbor_meta", {})
    terrain_meta = result.get("terrain_meta", {})
    site_intelligence_bundle = result.get('site_intelligence', {}) or {}

    if site_intelligence_bundle.get('available'):
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            st.markdown("<div class='kpi-card'><div class='metric-title'>Site score</div><div class='metric-value'>{:.0f}/100</div></div>".format(float(site_intelligence_bundle.get('site_score', 0.0))), unsafe_allow_html=True)
        with s2:
            st.markdown("<div class='kpi-card'><div class='metric-title'>Mulighet</div><div class='metric-value'>{:.0f}/100</div></div>".format(float(site_intelligence_bundle.get('opportunity_score', 0.0))), unsafe_allow_html=True)
        with s3:
            st.markdown("<div class='kpi-card'><div class='metric-title'>Plan-/stedsrisiko</div><div class='metric-value'>{:.0f}/100</div></div>".format(float(site_intelligence_bundle.get('risk_score', 0.0))), unsafe_allow_html=True)
        with s4:
            favored = sorted((site_intelligence_bundle.get('typology_score_adjustments') or {}).items(), key=lambda item: item[1], reverse=True)
            favored_text = favored[0][0] if favored else '-'
            st.markdown("<div class='kpi-card'><div class='metric-title'>Favorisert grep</div><div class='metric-value'>{}</div></div>".format(favored_text), unsafe_allow_html=True)
    meta_lines = []
    if polygon_meta:
        meta_lines.append(f"Tomt: {polygon_meta.get('source', '-')}")
    if neighbor_meta:
        meta_lines.append(f"Naboer: {neighbor_meta.get('source', '-')} ({neighbor_meta.get('count', best.neighbor_count)})")
        if neighbor_meta.get("error"):
            meta_lines.append(f"Nabo-feil: {neighbor_meta.get('error')}")
    if terrain_meta:
        meta_lines.append(f"Terreng: {terrain_meta.get('source', '-')}")
        if terrain_meta.get("error"):
            meta_lines.append(f"Terreng-feil: {terrain_meta.get('error')}")
    if meta_lines:
        st.caption(" | ".join(meta_lines))

    st.markdown("### Alternativsammenligning")
    comparison_df = pd.DataFrame(
        [
            {
                "Alternativ": option.name,
                "Typologi": option.typology,
                "Etasjer": option.floors,
                "Fotavtrykk m2": option.footprint_area_m2,
                "Buildbart areal m2": option.buildable_area_m2,
                "BTA m2": option.gross_bta_m2,
                "Salgbart m2": option.saleable_area_m2,
                "Boliger": option.unit_count,
                "Parkering": option.parking_spaces,
                "Solscore": option.solar_score,
                "Solbelyst uteareal %": option.sunlit_open_space_pct,
                "Skuldersesong soltimer": option.estimated_equinox_sun_hours,
                "Vinter skygge kl12 m": option.winter_noon_shadow_m,
                "Terreng fall %": option.terrain_slope_pct,
                "Naboer": option.neighbor_count,
                "Score": option.score,
                "Bygningsdeler": len((option.geometry or {}).get('massing_parts', []) or []),
            }
            for option in options
        ]
    )
    st.dataframe(comparison_df, use_container_width=True, hide_index=True)

    if site_intelligence_bundle.get('available'):
        st.markdown('### Geodata-kontekst')
        gi_plan, gi_projects, gi_transport = st.columns(3)
        with gi_plan:
            st.caption('Plan og regulering')
            st.json(site_intelligence_bundle.get('plan', {}), expanded=False)
        with gi_projects:
            st.caption('Utbyggingsaktivitet')
            st.json(site_intelligence_bundle.get('projects', {}), expanded=False)
        with gi_transport:
            st.caption('Mobilitet og adkomst')
            st.json(site_intelligence_bundle.get('transport', {}), expanded=False)

    st.markdown("### Volumskisser")
    cols = st.columns(len(options))
    for col, option, image in zip(cols, options, result["option_images"]):
        with col:
            st.image(image, caption=f"{option.name} - {option.typology}", use_container_width=True)
            st.caption(
                f"BTA {option.gross_bta_m2:.0f} m2 | {option.unit_count} boliger | "
                f"solscore {option.solar_score:.0f}/100 | uteareal sol {option.sunlit_open_space_pct:.0f}%"
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

    st.markdown("### Sol/skygge og terreng")
    solar_df = pd.DataFrame(
        {
            option.name: {
                "Solbelyst uteareal %": option.sunlit_open_space_pct,
                "Skuldersesong soltimer": option.estimated_equinox_sun_hours,
                "Vinter soltimer": option.estimated_winter_sun_hours,
                "Vinterskygge kl 12 (m)": option.winter_noon_shadow_m,
                "Sommerskygge kl 15 (m)": option.summer_afternoon_shadow_m,
                "Terreng fall %": option.terrain_slope_pct,
                "Terreng relieff m": option.terrain_relief_m,
            }
            for option in options
        }
    ).T
    st.dataframe(solar_df, use_container_width=True)

    if geodata_token_ok and gdo is not None:
        st.markdown('### 3D Geodata-scene')
        selected_name = st.selectbox('Velg volum for 3D-scene', [opt.name for opt in options], index=0)
        selected_option = next((opt for opt in options if opt.name == selected_name), options[0])
        try:
            scene_config = gdo.fetch_scene_config()
            render_geodata_scene(SiteInputs(**site_result), selected_option, scene_config, height_px=620)
            scene_payload = build_geodata_scene_payload(SiteInputs(**site_result), selected_option, scene_config)
            st.download_button('Last ned scene-payload (JSON)', json.dumps(scene_payload, ensure_ascii=False, indent=2), file_name=f'scene_{selected_option.typology.lower().replace(" ", "_")}.json', use_container_width=True)
        except Exception as exc:
            st.caption(f'3D-scene kunne ikke rendres akkurat nå: {exc}')

    # --- Interaktiv Three.js 3D-modell ---
    st.markdown('### 3D Volummodell (interaktiv)')
    sel3d_name = st.selectbox('Velg alternativ for 3D-visning', [opt.name for opt in options], index=0, key='sel3d')
    sel3d_opt = next((opt for opt in options if opt.name == sel3d_name), options[0])
    try:
        render_interactive_3d(SiteInputs(**site_result), sel3d_opt, height_px=650, terrain_ctx=result.get('terrain_ctx'))
    except Exception as exc:
        st.caption(f'3D-modell kunne ikke rendres: {exc}')

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
