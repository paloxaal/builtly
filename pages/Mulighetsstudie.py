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
            raise RuntimeError("pyproj er ikke installert i miljøet.")

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


def _find_dejavu_font(style: str = "") -> Optional[str]:
    """Finn DejaVuSans TTF-font på systemet."""
    suffix_map = {"": "DejaVuSans.ttf", "B": "DejaVuSans-Bold.ttf", "I": "DejaVuSans-Oblique.ttf", "BI": "DejaVuSans-BoldOblique.ttf"}
    filename = suffix_map.get(style, "DejaVuSans.ttf")
    search_dirs = [
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/dejavu",
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
    ]
    for d in search_dirs:
        candidate = os.path.join(d, filename)
        if os.path.isfile(candidate):
            return candidate
    return None

HAS_DEJAVU = _find_dejavu_font("") is not None


def clean_pdf_text(text: Any) -> str:
    if text is None:
        return ""
    text = str(text)
    # Universelle typografi-erstatninger
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u2026", "...").replace("\u2022", "*")
    if HAS_DEJAVU:
        return text
    # Fallback: latin-1 for Helvetica — æøå er gyldige i latin-1, behold dem
    text = text.replace("\u00b2", "2").replace("\u00b3", "3")
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
    """Normaliser tekst for regex-matching — behold originaltekst men gjør case-insensitive."""
    return text or ""


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
        notes.append("pyproj mangler: GeoJSON i lon/lat og OSM-nabohenting blir deaktivert eller mindre presis.")
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
    # 0. Ortofoto (førstevalg)
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

def fetch_noise_map_image(
    bbox_utm: Tuple[float, float, float, float],
    buffer_m: float = 150.0,
    gdo_client: Any = None,
    width: int = 800,
    height: int = 800,
) -> Tuple[Optional[Image.Image], str]:
    """
    Henter et visuelt støykart fra Geodata Online DOK Forurensning MapServer.
    Returnerer (PIL Image, kilde-tekst) eller (None, feilmelding).
    """
    minx, miny, maxx, maxy = bbox_utm
    minx -= buffer_m
    miny -= buffer_m
    maxx += buffer_m
    maxy += buffer_m

    gdo_token = ""
    if gdo_client is not None:
        for attr in ['_token', 'token', '_access_token', 'access_token']:
            tkn = getattr(gdo_client, attr, None)
            if tkn and isinstance(tkn, str) and len(tkn) > 10:
                gdo_token = tkn
                break
        if not gdo_token:
            try:
                sc = gdo_client.fetch_scene_config()
                gdo_token = sc.get("token", "")
            except Exception:
                pass

    if not gdo_token:
        return None, "Ingen GDO-token tilgjengelig"

    gdo_base = "https://services.geodataonline.no/arcgis/rest/services/Geomap_UTM33_EUREF89/GeomapDOKForurensning/MapServer"
    # Vis alle støylag: 211 (veg-gruppe), 212 (veg-flate), 203-208 (andre)
    try:
        params = {
            "bbox": f"{minx},{miny},{maxx},{maxy}",
            "bboxSR": "25833",
            "imageSR": "25833",
            "size": f"{width},{height}",
            "format": "png32",
            "transparent": "true",
            "layers": "show:212,204,206,208,214",
            "f": "image",
            "token": gdo_token,
        }
        resp = requests.get(f"{gdo_base}/export", params=params, timeout=15)
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
            from io import BytesIO
            img = Image.open(BytesIO(resp.content)).convert("RGBA")
            # Sjekk om bildet er helt tomt/transparent (ingen støy i området)
            extrema = img.getextrema()
            if extrema[3][1] < 10:  # Alpha-kanal maks < 10 = helt transparent
                return None, "Ingen støysoner i dette området"
            return img, "Geodata Online DOK Forurensning"
        else:
            return None, f"Støykart HTTP {resp.status_code}"
    except Exception as exc:
        return None, f"Støykart-feil: {str(exc)[:60]}"


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
    # Behold ALLE deler av en MultiPolygon via unary_union — ikke bare den største
    if poly is None:
        return None, None, {"is_geographic": False}
    if hasattr(poly, 'geoms'):
        poly = unary_union(list(poly.geoms)).buffer(0)
    elif isinstance(poly, Polygon):
        poly = poly.buffer(0)
    else:
        poly = largest_polygon(poly)
    if poly is None or poly.is_empty:
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
        info["source"] = st.session_state.get("auto_site_msg", "Eksakt polygon")
        if "Eksakt" not in info["source"]:
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
            raise ValueError("Støtter foreløpig kun CSV/TXT for terreng.")

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
        # Bruk hele polygonen — unary_union samler alle deler i stedet for å kaste bort teiger
        if hasattr(site_polygon_input, 'geoms'):
            site_polygon = unary_union(list(site_polygon_input.geoms)).buffer(0)
        else:
            site_polygon = site_polygon_input.buffer(0) if site_polygon_input is not None else None
        if site_polygon is None or site_polygon.is_empty:
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
        # Behold hele geometrien, ikke bare største del
        if hasattr(buildable_polygon, 'geoms'):
            buildable_polygon = unary_union(list(buildable_polygon.geoms)).buffer(0)
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
    "Punkthus":      {"bld_w": 18.0, "bld_d": 18.0, "sp_along": 16.0, "sp_across": 16.0, "max_n": 6},
    "Rekke":         {"bld_w": 50.0, "bld_d": 14.0, "sp_along": 18.0, "sp_across": 24.0, "max_n": 12},
    "Tun":           {"bld_w": 42.0, "bld_d": 11.0, "sp_along": 14.0, "sp_across": 16.0, "max_n": 6},
    "Karré":         {"bld_w": 45.0, "bld_d": 45.0, "sp_along": 22.0, "sp_across": 22.0, "max_n": 4, "ring_d": 11.0},
    "Tårn":          {"bld_w": 18.0, "bld_d": 18.0, "sp_along": 28.0, "sp_across": 28.0, "max_n": 1},
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

    # Tilpass bygningsdybde — men behold typologisk karakter
    # Reduser spacing ved høy utnyttelse
    high_utilization = target_footprint_m2 > area * 0.4
    sp_factor = 0.5 if high_utilization else 1.0

    if typology == "Punkthus":
        # Flere SEPARATE kvadratiske hus (14-18m), med god avstand mellom
        bld_w = min(18.0, minor * 0.35)
        bld_d = bld_w  # Alltid kvadratisk
        bld_w = max(14.0, bld_w)
        bld_d = max(14.0, bld_d)
    elif typology == "Tårn":
        # LITE fotavtrykk — høyden gjør jobben (10+ etasjer)
        bld_w = min(18.0, minor * 0.30)
        bld_d = bld_w
        bld_w = max(14.0, bld_w)
        bld_d = max(14.0, bld_d)
    elif typology == "Rekke":
        # Fallback til Lamell-oppførsel
        bld_w = min(limits.get("bld_w", 50.0), major * 0.85)
        bld_d = min(14.0, minor * 0.40)
        bld_w = max(20.0, bld_w)
        bld_d = max(11.0, bld_d)
    elif typology == "Karré":
        bld_w = min(limits["bld_w"], minor * 0.8)
        bld_d = bld_w  # Kvadratisk ytre
        bld_w = max(20.0, bld_w)
        bld_d = max(20.0, bld_d)
    elif typology == "Tun":
        bld_w = min(limits["bld_w"], major * 0.55)
        bld_d = min(12.0, minor * 0.40)
        bld_w = max(15.0, bld_w)
        bld_d = max(8.0, bld_d)
    elif typology == "Lamell":
        # Lamell: lang, 12-14m dyp, strekk langs major
        bld_w = min(limits["bld_w"], major * 0.85)
        bld_d = min(14.0, minor * 0.40)
        bld_w = max(20.0, bld_w)
        bld_d = max(11.0, bld_d)
    else:
        # Podium+Tårn: standard
        bld_w = min(limits["bld_w"], major * 0.7)
        bld_d = min(limits["bld_d"], minor * 0.45)
        bld_w = max(12.0, bld_w)
        bld_d = max(10.0, bld_d)

    # Juster spacing for høy utnyttelse
    effective_sp_along = limits["sp_along"] * sp_factor
    effective_sp_across = limits["sp_across"] * sp_factor

    if typology == 'Karré':
        # Ekte kvartaler med gaardrom
        ring_d = limits.get("ring_d", 11.0)
        karre_side = min(bld_w, bld_d)
        single_area = karre_side * karre_side - max(0, (karre_side - 2*ring_d))**2
        n_needed = max(1, min(limits["max_n"], math.ceil(target_footprint_m2 / max(single_area, 1.0))))

        # Plasser kvartaler i grid
        step = karre_side + effective_sp_along
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
        # Lamell, Punkthus, Rekke, Tårn: 2D-grid plassering
        single_area = bld_w * bld_d
        n_needed = max(1, min(limits["max_n"], math.ceil(target_footprint_m2 / max(single_area, 1.0))))

        # Minimum antall bygg per typologi for visuell differensiering
        min_buildings = {"Punkthus": 3, "Tårn": 1, "Lamell": 1, "Rekke": 1}.get(typology, 1)
        # Tårn: ALDRI mer enn 1 bygning — hele poenget er høyde
        if typology == "Tårn":
            n_needed = 1
        else:
            n_needed = max(min_buildings, n_needed)

        buildings = _place_grid_buildings(
            buildable_polygon, bld_w, bld_d, angle,
            spacing_along=effective_sp_along,
            spacing_across=effective_sp_across,
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
    utnyttelsesgrad_bra_pct: float = 0.0  # %-BRA: overstyrer BYA som volumdriver


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
        r"gesimsh[oø]yde[^0-9]*(\d+(?:[.,]\d+)?)\s*m",
        r"byggeh[oø]yde[^0-9]*(\d+(?:[.,]\d+)?)\s*m",
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

    floors_from_height = max(1, int(site.max_height_m // max(site.floor_to_floor_m, 2.8))) if site.max_height_m > 0 else site.max_floors
    allowed_floors = max(1, min(site.max_floors, floors_from_height))

    # %-BRA overstyrer ALT — bruker tomteareal, ikke bebbyggbart areal
    if site.utnyttelsesgrad_bra_pct > 0:
        target_bra = site_area * site.utnyttelsesgrad_bra_pct / 100.0
        target_bta = target_bra / max(site.efficiency_ratio, 0.6)
        needed_footprint = target_bta / max(allowed_floors, 1)
        # Bruk tomteareal som øvre grense (ikke setback-krympet felt)
        max_footprint = min(site_area * 0.95, needed_footprint * 1.1)
        buildable_area = max(buildable_area, max_footprint)
    else:
        max_footprint_by_bya = site_area * (site.max_bya_pct / 100.0) if site.max_bya_pct > 0 else buildable_area
        max_footprint = min(buildable_area, max_footprint_by_bya)

    return {
        "buildable_width": buildable_width,
        "buildable_depth": buildable_depth,
        "buildable_area": buildable_area,
        "max_footprint": max_footprint,
        "allowed_floors": float(allowed_floors),
        "target_bra_from_pct": round(site_area * site.utnyttelsesgrad_bra_pct / 100.0, 0) if site.utnyttelsesgrad_bra_pct > 0 else 0.0,
        "volume_driver": "%-BRA" if site.utnyttelsesgrad_bra_pct > 0 else "BYA",
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
        # Podium: lav og bred (2 etg). Tårn: høyt og smalt (dobbel høyde av podium eller mer)
        sorted_comps = sorted(components, key=lambda p: p.area, reverse=True)
        podium_floors = 2
        podium_height = podium_floors * floor_to_floor_m
        tower_floors = max(floors, podium_floors + 4)  # Tårnet minst 4 etg over podium
        tower_height = tower_floors * floor_to_floor_m

        # Podium
        podium = sorted_comps[0]
        parts.append({
            "name": "Podium",
            "height_m": round(podium_height, 1),
            "floors": podium_floors,
            "color": [180, 180, 190, 0.65],
            "coords": geometry_to_coord_groups(podium),
        })

        # Tårn: plasser i sentrum av podium, maks 35% av podiumets areal
        if len(sorted_comps) > 1:
            for i, comp in enumerate(sorted_comps[1:], start=1):
                parts.append({
                    "name": f"Tårn {i}",
                    "height_m": round(tower_height, 1),
                    "floors": tower_floors,
                    "color": COLORS.get("Tårn", base_color),
                    "coords": geometry_to_coord_groups(comp),
                })
        else:
            # Lag et tårn-fotavtrykk fra sentrum av podium — 30% av podiumets areal
            cx, cy = podium.centroid.x, podium.centroid.y
            tower_side = min(18.0, math.sqrt(podium.area * 0.30))
            tower_side = max(12.0, tower_side)
            half = tower_side / 2.0
            tower_box = box(cx - half, cy - half, cx + half, cy + half)
            tower_clipped = tower_box.intersection(podium).buffer(0)
            if not tower_clipped.is_empty and tower_clipped.area > 20:
                parts.append({
                    "name": "Tårn",
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


def _typology_polygon_fill(
    placement_polygon: Polygon,
    typology: str,
    target_footprint_m2: float,
    angle_deg: float,
) -> Tuple[Optional[Polygon], Dict[str, Any]]:
    """
    Typologi-differensiert polygon-fill for høy utnyttelse.

    I stedet for å skalere tomtepolygonen generisk, lages ULIKE former
    per typologi slik at volumskissene er visuelt distinkte.

    Returnerer (footprint_polygon, placement_info_updates) eller (None, {})
    hvis fallback ikke lykkes.
    """
    rad = math.radians(angle_deg)
    pcx, pcy = placement_polygon.centroid.x, placement_polygon.centroid.y
    perp_rad = rad + math.pi / 2.0
    shape = _analyze_polygon(placement_polygon)
    major, minor = shape['major_m'], shape['minor_m']
    poly_area = shape['area_m2']
    result: Optional[Polygon] = None
    info: Dict[str, Any] = {"source": "polygon-fill"}

    if typology == "Lamell":
        # 2-3 parallelle rektangler langs major-aksen, 8-12m gap
        bld_depth = min(13.0, minor * 0.28)
        bld_depth = max(11.0, bld_depth)
        gap = min(12.0, max(8.0, minor * 0.15))
        n_bars = max(2, min(3, int(minor / (bld_depth + gap))))
        bld_width = min(major * 0.85, target_footprint_m2 / max(n_bars * bld_depth, 1.0))
        bld_width = max(20.0, bld_width)

        bars: List[Polygon] = []
        total_span = (n_bars - 1) * (bld_depth + gap)
        for i in range(n_bars):
            offset_across = -total_span / 2.0 + i * (bld_depth + gap)
            bx = pcx + offset_across * math.cos(perp_rad)
            by = pcy + offset_across * math.sin(perp_rad)
            bar = _make_oriented_rect(bx, by, bld_width, bld_depth, rad)
            clipped = bar.intersection(placement_polygon).buffer(0)
            if not clipped.is_empty and clipped.area > 40:
                bars.append(clipped)
        if bars:
            result = unary_union(bars).buffer(0)
            info["n_buildings"] = len(bars)

    elif typology == "Punkthus":
        # 2-4 kvadratiske bokser (16×16m) med 12-18m mellomrom
        side = min(18.0, minor * 0.35)
        side = max(14.0, side)
        spacing = min(18.0, max(12.0, minor * 0.2))
        n_pts = max(2, min(4, math.ceil(target_footprint_m2 / max(side * side, 1.0))))
        n_along = max(1, min(n_pts, int(major / (side + spacing)) + 1))
        n_across = max(1, math.ceil(n_pts / n_along))
        step_a = side + spacing
        step_c = side + spacing

        boxes: List[Polygon] = []
        span_a = (n_along - 1) * step_a
        span_c = (n_across - 1) * step_c
        for row in range(n_along):
            for col in range(n_across):
                if len(boxes) >= n_pts:
                    break
                oa = -span_a / 2.0 + row * step_a
                oc = -span_c / 2.0 + col * step_c
                bx = pcx + oa * math.cos(rad) + oc * math.cos(perp_rad)
                by = pcy + oa * math.sin(rad) + oc * math.sin(perp_rad)
                bx_box = _make_oriented_rect(bx, by, side, side, rad)
                clipped = bx_box.intersection(placement_polygon).buffer(0)
                if not clipped.is_empty and clipped.area > 40:
                    boxes.append(clipped)
        if boxes:
            result = unary_union(boxes).buffer(0)
            info["n_buildings"] = len(boxes)

    elif typology == "Karré":
        # Ring-form med gårdsrom via buffer-difference
        ring_depth = min(12.0, minor * 0.18)
        ring_depth = max(8.0, ring_depth)
        # Skaler polygonen ned til å matche target
        sf = math.sqrt(min(target_footprint_m2 * 1.8, poly_area * 0.85) / max(poly_area, 1.0))
        sf = min(sf, 0.92)
        outer = affinity.scale(placement_polygon, xfact=sf, yfact=sf,
                               origin=placement_polygon.centroid).buffer(0)
        inner = outer.buffer(-ring_depth)
        if inner.is_valid and not inner.is_empty and inner.area > 30:
            ring = outer.difference(inner).buffer(0)
            if not ring.is_empty and ring.area > 50:
                result = ring
                info["n_buildings"] = 1
                info["courtyard_count"] = 1

    elif typology == "Tun":
        # L-form eller U-form — hovedfløy langs major + 1-2 sidefløyer
        wing_depth = min(12.0, minor * 0.25)
        wing_depth = max(8.0, wing_depth)
        main_w = min(major * 0.75, target_footprint_m2 * 0.45 / max(wing_depth, 1.0))
        main_w = max(18.0, main_w)

        # Hovedfløy
        main_rect = _make_oriented_rect(pcx, pcy, main_w, wing_depth, rad)
        main_clipped = main_rect.intersection(placement_polygon).buffer(0)
        wings: List[Polygon] = []

        if not main_clipped.is_empty and main_clipped.area > 30:
            # Plasser sidefløyer i ender (U-form)
            for side_sign in [1.0, -1.0]:
                wing_w = min(minor * 0.5, target_footprint_m2 * 0.2 / max(wing_depth, 1.0))
                wing_w = max(12.0, wing_w)
                end_offset = (main_w / 2.0 - wing_depth / 2.0) * side_sign
                wx = pcx + end_offset * math.cos(rad) + (wing_w / 2.0 + wing_depth / 2.0) * 0.5 * math.cos(perp_rad)
                wy = pcy + end_offset * math.sin(rad) + (wing_w / 2.0 + wing_depth / 2.0) * 0.5 * math.sin(perp_rad)
                wing_rect = _make_oriented_rect(wx, wy, wing_depth, wing_w, rad)
                wing_clipped = wing_rect.intersection(placement_polygon).buffer(0)
                if not wing_clipped.is_empty and wing_clipped.area > 30:
                    wings.append(wing_clipped)

            parts = [main_clipped] + wings
            result = unary_union(parts).buffer(0)
            info["n_buildings"] = len(parts)

    elif typology == "Tårn":
        # Lite fotavtrykk (20×20m), mange etasjer — overstyrer floor_range
        side = min(20.0, minor * 0.35)
        side = max(16.0, side)
        tower = _make_oriented_rect(pcx, pcy, side, side, rad)
        clipped = tower.intersection(placement_polygon).buffer(0)
        if not clipped.is_empty and clipped.area > 100:
            result = clipped
            info["n_buildings"] = 1
            info["tower_override_floors"] = True  # Signal til generate_options

    elif typology == "Podium + Tårn":
        # Stort podium (dekker mye av tomten) + lite tårn oppå
        pod_sf = math.sqrt(min(target_footprint_m2 * 0.7, poly_area * 0.6) / max(poly_area, 1.0))
        pod_sf = min(pod_sf, 0.88)
        podium = affinity.scale(placement_polygon, xfact=pod_sf, yfact=pod_sf,
                                origin=placement_polygon.centroid).buffer(0)
        tower_side = min(20.0, math.sqrt(podium.area) * 0.4)
        tower_side = max(14.0, tower_side)
        tower = _make_oriented_rect(pcx, pcy, tower_side, tower_side, rad)
        tower_clipped = tower.intersection(podium).buffer(0)

        if not podium.is_empty and podium.area > 80:
            parts_list = [podium]
            if not tower_clipped.is_empty and tower_clipped.area > 50:
                parts_list.append(tower_clipped)
            result = unary_union(parts_list).buffer(0)
            info["n_buildings"] = 2
            info["podium_tower_split"] = True

    elif typology == "Rekke":
        # 1 lang smal bygning langs major
        bld_depth = min(10.0, minor * 0.25)
        bld_depth = max(8.0, bld_depth)
        bld_width = min(major * 0.90, target_footprint_m2 / max(bld_depth, 1.0))
        bld_width = max(25.0, bld_width)
        row_rect = _make_oriented_rect(pcx, pcy, bld_width, bld_depth, rad)
        clipped = row_rect.intersection(placement_polygon).buffer(0)
        if not clipped.is_empty and clipped.area > 50:
            result = clipped
            info["n_buildings"] = 1

    # Generic fallback: skalert polygon (gammel oppførsel)
    if result is None or result.is_empty or result.area < 30:
        return None, {}

    info["fit_scale"] = round(float(result.area) / max(target_footprint_m2, 1.0), 3)
    return result, info


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

    # Når %-BRA overstyrer, bruk STØRSTE tilgjengelige polygon — ignorer setbacks
    placement_polygon = buildable_polygon
    if site.utnyttelsesgrad_bra_pct > 0:
        # Prøv site_polygon først, deretter buildable, velg den største
        candidates = [p for p in [site_polygon, buildable_polygon] if p is not None and not p.is_empty]
        if candidates:
            biggest = max(candidates, key=lambda p: p.area)
            # Minimal 1m buffer for å ikke treffe tomtegrensen eksakt
            try:
                buffered = biggest.buffer(-1.0)
                if buffered is not None and not buffered.is_empty and buffered.area > biggest.area * 0.3:
                    placement_polygon = buffered
                else:
                    placement_polygon = biggest
            except Exception:
                placement_polygon = biggest

    templates = [
        {"name": "Alt A - Lamell", "typology": "Lamell", "coverage": 0.75, "floor_range": (3, 6), "eff_adj": 0.02},
        {"name": "Alt B - Karré", "typology": "Karré", "coverage": 0.80, "floor_range": (3, 6), "eff_adj": 0.00},
        {"name": "Alt C - Punkthus", "typology": "Punkthus", "coverage": 0.35, "floor_range": (4, 8), "eff_adj": -0.01},
        {"name": "Alt D - Tårn", "typology": "Tårn", "coverage": 0.15, "floor_range": (10, 15), "eff_adj": -0.03},
        {"name": "Alt E - Podium + Tårn", "typology": "Podium + Tårn", "coverage": 0.55, "floor_range": (5, 10), "eff_adj": -0.02},
        {"name": "Alt F - Tun", "typology": "Tun", "coverage": 0.70, "floor_range": (3, 5), "eff_adj": -0.02},
    ]

    options: List[OptionResult] = []
    # Mål-BTA: bruk %-BRA hvis satt, ellers desired_bta_m2
    if site.utnyttelsesgrad_bra_pct > 0:
        site_area_for_pct = max(1.0, geodata_context.get("site_area_m2", site.site_area_m2))
        target_bra = site_area_for_pct * site.utnyttelsesgrad_bra_pct / 100.0
        target_bta = max(target_bra / max(site.efficiency_ratio, 0.6), 1.0)
    else:
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

        # Tårn krever minimum 10 etasjer — hopp over hvis ikke mulig
        if typology == "Tårn" and allowed_floors < 10:
            continue

        # Når %-BRA overstyrer: beregn target_footprint direkte fra BTA-mål
        if site.utnyttelsesgrad_bra_pct > 0:
            fl_min, fl_max = template.get("floor_range", (3, 5))
            # Høy %-BRA (>150%): ignorer template floor_range og bruk allowed_floors
            # slik at motoren kan nå målet med færre m² fotavtrykk
            if site.utnyttelsesgrad_bra_pct > 150:
                fl_max_eff = allowed_floors
                fl_min_eff = max(fl_min, 2)
            else:
                fl_max_eff = min(fl_max, allowed_floors)
                fl_min_eff = max(fl_min, 2)
            # Velg etasjer som gir fotavtrykk innenfor TOMTEAREAL (ikke placement_polygon)
            site_area_limit = max(geodata_context.get("site_area_m2", site.site_area_m2), 100.0)
            best_fp = 0
            best_fl = fl_min_eff
            for f in range(fl_min_eff, fl_max_eff + 1):
                fp = target_bta / max(f, 1)
                if fp <= site_area_limit * 0.92 and fp > best_fp:
                    best_fp = fp
                    best_fl = f
            if best_fp < 50:
                best_fp = target_bta / max(fl_max_eff, 1)
                best_fl = fl_max_eff
            # INGEN cap mot placement_polygon — %-BRA overstyrer alt
            target_footprint = best_fp
        else:
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
            footprint_polygon, placement = create_typology_footprint(placement_polygon, typology, target_footprint)

        footprint_area = float(footprint_polygon.area)

        # --- HØY UTNYTTELSE FALLBACK (typologi-differensiert) ---
        # Når %-BRA er aktiv og fotavtrykket er under 85% av mål:
        # lager ULIKE former per typologi i stedet for generisk skalert polygon
        pct_bra_fill_threshold = 0.85 if site.utnyttelsesgrad_bra_pct > 150 else 0.50
        if site.utnyttelsesgrad_bra_pct > 0 and footprint_area < target_footprint * pct_bra_fill_threshold:
            fill_angle = placement.get("orientation_deg", 0.0)
            filled_fp, fill_info = _typology_polygon_fill(
                placement_polygon, typology, target_footprint, fill_angle,
            )
            if filled_fp is not None and not filled_fp.is_empty and filled_fp.area > footprint_area * 1.05:
                footprint_polygon = filled_fp
                footprint_area = float(footprint_polygon.area)
                placement.update(fill_info)
            else:
                # Siste utvei: generisk skalert polygon (gammel oppførsel)
                scale_ratio = math.sqrt(target_footprint / max(placement_polygon.area, 1.0))
                max_scale = 0.98 if site.utnyttelsesgrad_bra_pct > 200 else 0.95
                scale_ratio = min(scale_ratio, max_scale)
                filled_fp = affinity.scale(placement_polygon, xfact=scale_ratio, yfact=scale_ratio,
                                           origin=placement_polygon.centroid).buffer(0)
                if filled_fp is not None and not filled_fp.is_empty and filled_fp.area > footprint_area * 1.05:
                    footprint_polygon = filled_fp
                    footprint_area = float(footprint_polygon.area)
                    placement["source"] = "polygon-fill-generic"
                    placement["n_buildings"] = 1
                    placement["fit_scale"] = round(footprint_area / max(target_footprint, 1.0), 3)

        # Etasjer: bruk typologiens floor_range, begrenset av allowed_floors
        fl_min, fl_max = template.get("floor_range", (3, 5))
        # Høy %-BRA (>150%): bruk allowed_floors som tak for ALLE typologier
        if site.utnyttelsesgrad_bra_pct > 150:
            fl_max = allowed_floors
        # Tårn: ALLTID minimum 10 etasjer (det er definisjonen av et tårn)
        if typology == "Tårn":
            fl_min = max(fl_min, 10)
            fl_max = max(fl_max, min(15, allowed_floors))
        fl_min = max(2, min(fl_min, allowed_floors))
        fl_max = min(fl_max, allowed_floors)
        if fl_min > fl_max:
            fl_max = fl_min

        # Finn optimalt etasjetall som treffer nærmest target_bta
        best_floor_fit = fl_min
        best_delta = float("inf")
        for f_candidate in range(fl_min, fl_max + 1):
            candidate_bta = footprint_area * f_candidate
            delta = abs(candidate_bta - target_bta)
            if delta < best_delta:
                best_delta = delta
                best_floor_fit = f_candidate
        floors = best_floor_fit

        gross_bta = footprint_area * floors
        if site.max_bra_m2 > 0 and site.utnyttelsesgrad_bra_pct <= 0:
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
            notes.append("Rekkehus gir flest enheter, lav byggehøyde og effektiv arealbruk, men gir lavere BTA per tomt enn blokk.")
        else:
            notes.append("Tun/U-form gir høy arealutnyttelse og tydelig uterom, men er mest sårbar for skygge fra egne fløyer og naboer.")

        score = rank_score(
            target_fit_pct=target_fit_pct,
            solar_score=solar["solar_score"],
            open_space_ratio=open_space_ratio,
            efficiency_ratio=actual_efficiency,
            parking_pressure_pct=parking_pressure_pct,
        )

        # Tomteform-bonus: favoriser typologier som passer tomtens form
        site_shape = _analyze_polygon(placement_polygon)
        if site_shape.get('is_elongated', False):
            # Smal/avlang tomt → Lamell passer klart best
            shape_bonus = {"Lamell": 10.0, "Tun": 4.0, "Karré": -6.0, "Punkthus": -5.0, "Tårn": -2.0, "Podium + Tårn": -3.0}
        else:
            # Kompakt/kvadratisk tomt → Karré og Punkthus passer godt
            shape_bonus = {"Lamell": 0.0, "Tun": 1.0, "Karré": 4.0, "Punkthus": 3.0, "Tårn": 2.0, "Podium + Tårn": 3.0}
        score = round(score + shape_bonus.get(typology, 0.0), 1)

        # Ekstra straff: typologier som brukte polygon-fill mister differensiering
        if placement.get("source", "").startswith("polygon-fill"):
            # Lamell er den naturlige polygon-fill-formen — andre typologier straffes
            if typology != "Lamell":
                score = round(score - 3.0, 1)

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


# --- AI-RAFFINERING AV SKISSE ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")


def _call_claude_json(system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> Optional[Dict[str, Any]]:
    """Kall Claude API og returner parset JSON."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=60,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
        # Ekstraher JSON fra respons
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    except Exception:
        return None


def _call_claude_text(system_prompt: str, user_prompt: str, max_tokens: int = 6000) -> Optional[str]:
    """Kall Claude API og returner ren tekst (ikke JSON)."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=90,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
        return text.strip() if text.strip() else None
    except Exception:
        return None


def generate_ai_report_for_locked_sketch(
    sketch_option: "OptionResult",
    motor_options: List["OptionResult"],
    site: "SiteInputs",
    geodata_context: Dict[str, Any],
    environment_data: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Sekundær AI-analyse: Claude skriver en profesjonell mulighetsstudie-rapport
    basert på den låste skissen, sammenlignet med motorens alternativer.
    
    Returnerer fullstendig rapporttekst (markdown-format) eller None ved feil.
    """
    # Bygg kontekst for AI
    pct_bra_active = site.utnyttelsesgrad_bra_pct > 0
    site_area = max(site.site_area_m2, 1.0)
    sketch_bra = sketch_option.saleable_area_m2
    sketch_pct_bra = round(sketch_bra / site_area * 100, 0) if pct_bra_active else 0

    motor_summary = []
    for opt in motor_options[:6]:
        bra = opt.gross_bta_m2 * opt.efficiency_ratio
        motor_summary.append(
            f"{opt.name} ({opt.typology}): {opt.gross_bta_m2:.0f} m² BTA, ~{bra:.0f} m² BRA, "
            f"{opt.unit_count} boliger, {opt.floors} etg, sol {opt.solar_score:.0f}/100"
        )

    terrain_info = ""
    terrain = geodata_context.get("terrain")
    if terrain and terrain.get("slope_pct", 0) > 0:
        terrain_info = f"Terreng: {terrain.get('slope_pct', 0):.1f}% fall, {terrain.get('relief_m', 0):.1f} m relieff."

    # Miljødata
    env = environment_data or {}
    noise_info = ""
    noise = env.get("noise", {})
    if noise.get("available") and noise.get("zones"):
        worst = max(noise["zones"], key=lambda z: z.get("db", 0))
        noise_info = f"Støy: {worst.get('zone', '')} — {worst.get('db', 0):.0f} dB Lden fra {worst.get('source_type', 'vei')}. Kilde: {noise.get('source', 'kartlegging')}."
        if len(noise["zones"]) > 1:
            noise_info += f" Totalt {len(noise['zones'])} støysoner registrert."
    daylight_info = ""
    dl = env.get("daylight", {})
    if dl.get("available") and dl.get("overall_score", 0) > 0:
        daylight_info = f"Dagslysindikator: {dl['overall_score']:.0f}/100."
    wind_info = ""
    wc = env.get("wind_comfort", {})
    if wc.get("available"):
        wind_info = f"Vindkomfort: Klasse {wc.get('lawson_class', '?')} ({wc.get('overall', '')})."

    system_prompt = """Du er en erfaren norsk arkitekt som skriver profesjonelle mulighetsstudier.
Du skriver konsise, faglig presise rapporter på norsk bokmål. Bruk riktig norsk (æ, ø, å).

Skriv rapporten med disse seksjonene, markert med # for overskrift:
# 1. OPPSUMMERING
# 2. GRUNNLAG
# 3. VALGT VOLUMLØSNING
# 4. ARKITEKTONISK VURDERING
# 5. SOL- OG DAGSLYSFORHOLD
# 6. STØY OG MILJØFORHOLD
# 7. SAMMENLIGNING MED ALTERNATIVER
# 8. RISIKO OG AVKLARINGSPUNKTER
# 9. ANBEFALING OG NESTE STEG

Regler:
- Bruk BRA (salgbart/bruksareal) som primærtall, BTA som sekundærtall
- Vær konkret om styrker og svakheter ved den valgte løsningen
- Kommenter sol/skygge basert på solscore og plassering
- Hvis støydata finnes, kommenter konsekvenser for planløsning (gjennomgående leiligheter, stille side, balkongplassering)
- Nevn kort motorens alternativer som referanse, men fokuser på den valgte løsningen
- Skriv 600-900 ord totalt. Ikke bruk bullet points med - i rapporten, skriv sammenhengende tekst.
- Ikke dikter opp tall — bruk KUN tallene du får i konteksten
"""

    user_prompt = f"""Skriv en mulighetsstudie-rapport for dette prosjektet.

TOMT OG REGULERING:
- Adresse/prosjekt: {sketch_option.name}
- Tomteareal: {site_area:.0f} m²
- Byggefelt: {sketch_option.buildable_area_m2:.0f} m²
- Maks BYA: {site.max_bya_pct:.1f}%
- Maks etasjer: {site.max_floors}
- Maks høyde: {site.max_height_m:.1f} m
- Geometri: {site.site_geometry_source}
- Nabobygg: {site.neighbor_count} stk i modellen
{terrain_info}
{"- %-BRA mål: " + str(site.utnyttelsesgrad_bra_pct) + "% → " + str(round(site_area * site.utnyttelsesgrad_bra_pct / 100)) + " m² BRA" if pct_bra_active else "- Ønsket BTA: " + str(site.desired_bta_m2) + " m²"}

MILJØFORHOLD:
{noise_info if noise_info else "Ingen støydata registrert."}
{daylight_info if daylight_info else ""}
{wind_info if wind_info else ""}

VALGT LØSNING (manuell skisse):
- Antall bygg: {sketch_option.geometry.get('component_count', '?')}
- Fotavtrykk: {sketch_option.footprint_area_m2:.0f} m²
- BTA: {sketch_option.gross_bta_m2:.0f} m²
- BRA (salgbart): {sketch_bra:.0f} m²
{"- %-BRA oppnådd: " + str(sketch_pct_bra) + "%" if pct_bra_active else ""}
- Etasjer: {sketch_option.floors}
- Byggehøyde: {sketch_option.building_height_m:.1f} m
- Leiligheter: {sketch_option.unit_count}
- Fordeling: {json.dumps(sketch_option.mix_counts, ensure_ascii=False)}
- Solscore: {sketch_option.solar_score:.0f}/100
- Solbelyst uteareal: {sketch_option.sunlit_open_space_pct:.0f}%
- Vinterskygge kl 12: {sketch_option.winter_noon_shadow_m:.0f} m

MOTORENS ALTERNATIVER (referanse):
{chr(10).join(motor_summary)}
"""

    return _call_claude_text(system_prompt, user_prompt, max_tokens=4000)


def refine_sketch_with_ai(
    sketch_buildings: List[Dict[str, Any]],
    site_polygon_coords: List[List[float]],
    site_area_m2: float,
    latitude_deg: float,
    max_bya_pct: float,
    max_floors: int,
    max_height_m: float,
    floor_to_floor_m: float,
    neighbors: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Optional[List[Dict[str, Any]]]:
    """
    AI-raffinering: Claude optimerer skissens bygningsplassering.

    Returnerer liste med raffinerte bygninger eller None.
    """
    system = """Du er en norsk arkitekt som optimerer bygningsplassering på tomter.
Du mottar en bruker-skisse med bygningsbokser og tomtekontekst.
Optimer plasseringen med fokus på:
1. FASADEORIENTERING: Roter bygninger for å maksimere sør/sørvest-vendte fasader og dagslys
2. AVSTAND: Sørg for min. 8m mellom bygninger (TEK17), helst 12-18m for lameller
3. SOLFORHOLD: Plasser lavere bygg mot sør, høyere mot nord for å unngå skygge på uteareal
4. UTEROM: Skap tydelige, solrike uterom mellom bygningene
5. ADKOMST: Plasser innganger mot vei/tilkomst
6. STØY: Hvis det er støy fra vei, plasser soverom/stille sider bort fra støykilden, bruk bygningskroppen som skjerm
7. DAGSLYS (TEK17 §13-7): Sørg for at alle leiligheter har tilstrekkelig dagslys — unngå at bygg skygger for hverandre
8. UTSIKT: Orienter bygninger for å maksimere utsikt mot åpne retninger, unngå å blokkere nabobygningers utsikt
9. VIND: Unngå smale passasjer mellom bygninger (venturi-effekt), plasser lavere bygg i dominerende vindretning

Svar KUN med en JSON-array med bygninger. Hver bygning har:
{"name": "str", "cx": float, "cy": float, "w": float, "d": float, "angle_deg": float, "floors": int, "role": "main|wing|tower", "reasoning": "kort begrunnelse for endring inkl. miljøhensyn"}

Hold deg innenfor tomtegrensen. Behold omtrent samme totale BTA (±15%).
IKKE inkluder noe annet enn JSON-arrayen i svaret."""

    user_data = {
        "sketch_buildings": sketch_buildings,
        "site_polygon": site_polygon_coords[:50],  # Begrens antall punkter
        "site_area_m2": round(site_area_m2, 0),
        "latitude_deg": round(latitude_deg, 2),
        "max_bya_pct": max_bya_pct,
        "max_floors": max_floors,
        "max_height_m": max_height_m,
        "floor_to_floor_m": floor_to_floor_m,
        "neighbor_count": len(neighbors or []),
        "nearby_neighbors": [
            {"height_m": n.get("height_m", 9), "distance_m": round(n.get("distance_m", 50), 0)}
            for n in (neighbors or [])[:10]
        ],
    }

    # Legg til miljødata hvis tilgjengelig
    if kwargs.get("environment"):
        env = kwargs["environment"]
        env_summary = {}
        if env.get("noise", {}).get("available"):
            zones = env["noise"].get("zones", [])
            if zones:
                env_summary["noise"] = f"Støysone: {zones[0].get('zone', '–')}, {zones[0].get('db', 0):.0f} dB fra {zones[0].get('source_type', 'vei')}"
        if env.get("wind", {}).get("available"):
            env_summary["wind"] = f"Dominerende vind: {env['wind'].get('dominant_direction', '–')}, snitt {env['wind'].get('avg_speed_ms', 0):.1f} m/s"
        if env.get("daylight", {}).get("available"):
            env_summary["daylight_score"] = env["daylight"].get("overall_score", 0)
        if env.get("views", {}).get("available"):
            env_summary["view_score"] = env["views"].get("overall_score", 0)
        if env_summary:
            user_data["environment"] = env_summary

    user_prompt = f"""Optimer denne bygningsskissen for tomten.

SKISSE-DATA:
{json.dumps(user_data, ensure_ascii=False, indent=2)}

Returner den optimerte bygningslisten som JSON-array. Behold antall bygg og omtrent samme dimensjoner,
men juster posisjon (cx/cy), rotasjon (angle_deg) og eventuelt dybde/bredde for bedre arkitektonisk kvalitet.
Ta hensyn til miljøforhold (støy, vind, dagslys, utsikt) der dette er oppgitt.
Forklar kort i "reasoning" hva du endret for hvert bygg, inkludert miljøhensyn."""

    result = _call_claude_json(system, user_prompt)
    if isinstance(result, list) and len(result) > 0:
        return result
    return None


def generate_sketch_variants(
    sketch_buildings: List[Dict[str, Any]],
    site_polygon_coords: List[List[float]],
    site_area_m2: float,
    latitude_deg: float,
    max_bya_pct: float,
    max_floors: int,
    max_height_m: float,
    floor_to_floor_m: float,
    neighbors: Optional[List[Dict[str, Any]]] = None,
    n_variants: int = 2,
) -> Optional[List[Dict[str, Any]]]:
    """
    AI-generering av alternative volumløsninger innenfor skissens bounding box.
    Returnerer dict med "variants" liste, hver med "name", "buildings", "description".
    """
    system = """Du er en norsk arkitekt som genererer alternative volumløsninger for tomter.
Du mottar en bruker-skisse og skal lage varianter som holder seg innenfor omtrent
samme bounding box og BTA, men varierer typologisk grep.

Eksempler på varianter:
- Variant A: "Kompakt lamell" — færre, lengre bygninger med flere etasjer
- Variant B: "Punkthus-grep" — flere, mindre bygninger med mer åpent mellomrom
- Variant C: "L-form / Tun" — bygninger i vinkel som skaper tydelig uterom

Svar KUN med JSON:
{
  "variants": [
    {
      "name": "Variant A - Kompakt lamell",
      "description": "Kort begrunnelse",
      "buildings": [{"name":"Bygg A","cx":...,"cy":...,"w":...,"d":...,"angle_deg":...,"floors":...}]
    }
  ]
}"""

    user_data = {
        "sketch_buildings": sketch_buildings,
        "site_polygon": site_polygon_coords[:50],
        "site_area_m2": round(site_area_m2, 0),
        "latitude_deg": round(latitude_deg, 2),
        "max_bya_pct": max_bya_pct,
        "max_floors": max_floors,
        "max_height_m": max_height_m,
        "floor_to_floor_m": floor_to_floor_m,
        "n_variants": n_variants,
    }

    user_prompt = f"""Generer {n_variants} alternative volumløsninger basert på denne skissen.
Hold deg innenfor tomtens bounding box og ±20% av skissens totale BTA.

SKISSE-DATA:
{json.dumps(user_data, ensure_ascii=False, indent=2)}

Returner JSON med "variants"-array."""

    result = _call_claude_json(system, user_prompt, max_tokens=6000)
    if isinstance(result, dict) and "variants" in result:
        return result
    return None


def _deterministic_solar_refinement(
    sketch_buildings: List[Dict[str, Any]],
    latitude_deg: float,
) -> List[Dict[str, Any]]:
    """Deterministisk fallback: roter bygninger for optimal solorientering."""
    # Optimal langside-orientering for skandinavisk breddegrad: øst-vest (vinkelrett på sør)
    # dvs. bygningsdybden (kort side) peker mot sør for maks dagslys
    optimal_angle = 0.0  # 0° = lang side øst-vest, kort side mot sør

    refined = []
    for bld in sketch_buildings:
        b = dict(bld)
        current = float(b.get("angle_deg", 0))
        w = float(b.get("w", 40))
        d = float(b.get("d", 14))

        # Hvis bygningen er dyp (>16m), er det en lamell — orienter lang side øst-vest
        if w > d * 1.5:
            # Allerede bred — sjekk om den bør roteres
            delta = abs(current - optimal_angle) % 180
            if delta > 45 and delta < 135:
                b["angle_deg"] = round(optimal_angle, 1)
                b["reasoning"] = f"Rotert til {optimal_angle}° for å orientere langfasade øst-vest (best dagslys på breddegrad {latitude_deg:.0f}°)"
            else:
                b["reasoning"] = "Beholdt orientering — allerede god solretning"
        else:
            b["reasoning"] = "Kompakt fotavtrykk — orientering påvirker dagslys minimalt"

        refined.append(b)
    return refined


# --- MILJØANALYSE: STØY, DAGSLYS, UTSIKT, VIND ---

@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def fetch_noise_zones(bbox_utm: Tuple[float, float, float, float], buffer_m: float = 100.0, gdo_client: Any = None) -> Dict[str, Any]:
    """Hent støysonekart. Prøver Geodata Online DOK Forurensning først, deretter Geonorge WFS."""
    minx, miny, maxx, maxy = bbox_utm
    minx -= buffer_m
    miny -= buffer_m
    maxx += buffer_m
    maxy += buffer_m

    result: Dict[str, Any] = {"available": False, "zones": [], "source": "Ingen støydata", "debug": []}

    result["debug"].append(f"bbox: {minx:.0f},{miny:.0f},{maxx:.0f},{maxy:.0f} | gdo: {'ja' if gdo_client else 'nei'}")

    # --- 1. GEODATA ONLINE: DOK Forurensning ---
    gdo_base = "https://services.geodataonline.no/arcgis/rest/services/Geomap_UTM33_EUREF89/GeomapDOKForurensning/MapServer"

    # Ekstraher token fra GDO-klienten
    gdo_token = ""
    if gdo_client is not None:
        for attr in ['_token', 'token', '_access_token', 'access_token']:
            tkn = getattr(gdo_client, attr, None)
            if tkn and isinstance(tkn, str) and len(tkn) > 10:
                gdo_token = tkn
                break
        # Prøv scene_config som siste utvei
        if not gdo_token:
            try:
                sc = gdo_client.fetch_scene_config()
                gdo_token = sc.get("token", "")
            except Exception:
                pass

    if gdo_token:
        result["debug"].append(f"token: {gdo_token[:8]}...")
        # Strategi A: identify med hele kartet
        try:
            identify_url = f"{gdo_base}/identify"
            params = {
                "geometry": json.dumps({"xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy, "spatialReference": {"wkid": 25833}}),
                "geometryType": "esriGeometryEnvelope",
                "sr": "25833",
                "layers": "all",
                "tolerance": "10",
                "mapExtent": f"{minx},{miny},{maxx},{maxy}",
                "imageDisplay": "600,600,96",
                "returnGeometry": "false",
                "f": "json",
                "token": gdo_token,
            }
            resp = requests.get(identify_url, params=params, timeout=15)
            result["debug"].append(f"identify: HTTP {resp.status_code}")

            if resp.status_code == 200:
                data = resp.json()
                if "error" in data:
                    result["debug"].append(f"identify error: {data['error'].get('message', '')[:80]}")
                else:
                    _parse_gdo_noise_results(data.get("results", []), result)
        except Exception as exc:
            result["debug"].append(f"identify exception: {str(exc)[:60]}")

        # Strategi B: query kjente støylag direkte (hardkodede lag-IDer fra DOK Forurensning)
        if not result["available"]:
            noise_layers = [
                (212, "Støykartlegging veg T-1442", "veg"),
                (206, "Støysoner jernbane", "jernbane"),
                (204, "Støysoner lufthavn", "flyplass"),
                (214, "Støysoner Forsvarets flyplasser", "flyplass"),
                (208, "Støysoner skyte- og øvingsfelt", "skytefelt"),
            ]
            geom_json = json.dumps({
                "xmin": minx, "ymin": miny, "xmax": maxx, "ymax": maxy,
                "spatialReference": {"wkid": 25833}
            })
            for layer_id, layer_label, src_type in noise_layers:
                try:
                    query_url = f"{gdo_base}/{layer_id}/query"
                    q_params = {
                        "where": "1=1",
                        "geometry": geom_json,
                        "geometryType": "esriGeometryEnvelope",
                        "inSR": "25833",
                        "spatialRel": "esriSpatialRelIntersects",
                        "outFields": "stoysonekategori,stoykilde,stoykildenavn,kommune,objtype",
                        "returnGeometry": "false",
                        "f": "json",
                        "token": gdo_token,
                    }
                    q_resp = requests.get(query_url, params=q_params, timeout=10)
                    if q_resp.status_code == 200:
                        q_data = q_resp.json()
                        if "error" in q_data:
                            result["debug"].append(f"layer {layer_id}: {q_data['error'].get('message', '')[:50]}")
                            continue
                        features = q_data.get("features", [])
                        result["debug"].append(f"lag {layer_id}: {len(features)} treff")
                        for feat in features:
                            attrs = feat.get("attributes", {})
                            _parse_single_noise_feature(attrs, layer_label, result)
                            if result["zones"]:
                                result["zones"][-1]["source_type"] = src_type
                    else:
                        result["debug"].append(f"lag {layer_id}: HTTP {q_resp.status_code}")
                except Exception as exc:
                    result["debug"].append(f"layer {layer_id} feil: {str(exc)[:40]}")
                    continue

            # Sjekk også forurenset grunn (lag 202) for kontekst
            try:
                q_resp = requests.get(f"{gdo_base}/202/query", params={
                    "geometry": geom_json,
                    "geometryType": "esriGeometryEnvelope",
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "*",
                    "returnGeometry": "false",
                    "f": "json",
                    "token": gdo_token,
                }, timeout=10)
                if q_resp.status_code == 200:
                    q_data = q_resp.json()
                    features = q_data.get("features", [])
                    if features:
                        result["debug"].append(f"Forurenset grunn: {len(features)} treff")
                        result["contaminated_ground"] = True
                        result["contaminated_count"] = len(features)
            except Exception:
                pass

        if result["zones"]:
            result["available"] = True
            result["source"] = "Geodata Online DOK Forurensning"
            result["zones"].sort(key=lambda z: z.get("db", 0), reverse=True)
            return result
    else:
        result["debug"].append("Ingen GDO-token funnet")

    # --- 2. FALLBACK: Geonorge WFS ---
    services = [
        ("https://wfs.geonorge.no/skwms1/wfs.stoykartlegging", "Stoykartlegging:StoysoneFelles"),
        ("https://wfs.geonorge.no/skwms1/wfs.stoykartlegging", "Stoykartlegging:StoysoneVeg"),
    ]
    for url, layer in services:
        try:
            resp = requests.get(url, params={
                "service": "WFS", "version": "2.0.0", "request": "GetFeature",
                "typenames": layer, "srsName": "EPSG:25833",
                "outputFormat": "application/json",
                "bbox": f"{minx},{miny},{maxx},{maxy},EPSG:25833",
                "count": "50",
            }, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                for f in features:
                    props = f.get("properties", {})
                    zone = props.get("stoysone", props.get("sone", props.get("navn", "ukjent")))
                    db_val = safe_float(props.get("db_verdi", props.get("lden", props.get("lydniva", 0))), 0)
                    result["zones"].append({
                        "zone": str(zone),
                        "db": round(db_val, 1),
                        "source_type": props.get("kildetype", props.get("type", "vei")),
                    })
                if features:
                    result["available"] = True
                    result["source"] = "Geonorge støykartlegging"
                    break
        except Exception:
            continue
    return result


def _parse_gdo_noise_results(gdo_results: List[Dict[str, Any]], result: Dict[str, Any]) -> None:
    """Parser GDO identify-resultater til støysoner."""
    for feat in gdo_results:
        attrs = feat.get("attributes", {})
        layer_name = feat.get("layerName", "Støysone")
        _parse_single_noise_feature(attrs, layer_name, result)


def _parse_single_noise_feature(attrs: Dict[str, Any], layer_name: str, result: Dict[str, Any]) -> None:
    """Parser ett støy-feature fra GDO DOK Forurensning. Bruker T-1442 feltnavn."""
    db_val = 0.0
    zone_name = ""
    source_type = "ukjent"

    # T-1442 kategori: G = Gul (55-65 dB Lden), R = Rød (>65 dB Lden)
    kategori = str(attrs.get("stoysonekategori", attrs.get("Stoysonekategori", ""))).strip().upper()
    if kategori == "R":
        zone_name = "Rød støysone"
        db_val = 65.0
    elif kategori == "G":
        zone_name = "Gul støysone"
        db_val = 55.0

    # Direkte dB-verdier (andre lag)
    if db_val == 0:
        for db_key in ["Lden", "LDEN", "lden", "db", "lydniva", "stoyniva", "desibel",
                        "Lnight", "lnight", "DB", "db_verdi"]:
            val = attrs.get(db_key)
            if val is not None:
                db_val = safe_float(val, 0)
                if db_val > 0:
                    break

    # Sonenavn
    if not zone_name:
        for zone_key in ["stoysone", "sone", "navn", "klasse", "objtype"]:
            val = attrs.get(zone_key)
            if val is not None and str(val).strip():
                zone_name = str(val).strip()
                break
    if not zone_name:
        zone_name = layer_name

    # Kildetype fra felt eller lagnavn
    stoykilde = str(attrs.get("stoykilde", "")).strip()
    stoykildenavn = str(attrs.get("stoykildenavn", "")).strip()
    if stoykilde:
        source_type = stoykilde.lower()
    else:
        ln = layer_name.lower()
        if any(k in ln for k in ["veg", "vei", "road"]):
            source_type = "veg"
        elif any(k in ln for k in ["jernbane", "bane", "rail"]):
            source_type = "jernbane"
        elif any(k in ln for k in ["fly", "luft"]):
            source_type = "flyplass"
        elif any(k in ln for k in ["skyte"]):
            source_type = "skytefelt"
        else:
            source_type = "sammensatt"

    if db_val > 0 or (zone_name and zone_name != layer_name):
        entry: Dict[str, Any] = {
            "zone": zone_name,
            "db": round(db_val, 1),
            "source_type": source_type,
            "layer": layer_name,
        }
        if stoykildenavn:
            entry["source_name"] = stoykildenavn
        if kategori:
            entry["kategori"] = kategori
        result["zones"].append(entry)


def calculate_daylight_tek17(
    site_polygon: Polygon,
    building_polygons: List[Polygon],
    building_heights: List[float],
    neighbor_polygons: List[Dict[str, Any]],
    latitude_deg: float,
) -> Dict[str, Any]:
    """
    Forenklet TEK17 §13-7 dagslysanalyse.

    Beregner sky view factor (SVF) og dagslysindikator for sør/nord/øst/vest-fasader.
    Ikke en full Radiance-simulering, men gir indikasjon på dagslystilgang.
    """
    result = {"available": True, "facades": [], "overall_score": 0.0}
    if not building_polygons:
        result["available"] = False
        return result

    all_obstructions = []
    for nb in neighbor_polygons:
        nb_poly = nb.get("polygon")
        if nb_poly is None:
            continue
        all_obstructions.append({"polygon": nb_poly, "height": float(nb.get("height_m", 9))})

    facade_scores = []
    cardinal_names = ["Sør", "Øst", "Nord", "Vest"]
    cardinal_azimuths = [180, 90, 0, 270]

    for bld_idx, (bld_poly, bld_h) in enumerate(zip(building_polygons, building_heights)):
        centroid = bld_poly.centroid
        bld_name = f"Bygg {chr(65 + bld_idx)}"

        for card_idx, (card_name, azimuth) in enumerate(zip(cardinal_names, cardinal_azimuths)):
            # Sky view factor: sjekk obstruksjoner i denne retningen
            check_dist = 80.0
            az_rad = math.radians(azimuth)
            check_x = centroid.x + math.sin(az_rad) * check_dist
            check_y = centroid.y + math.cos(az_rad) * check_dist

            max_obstruction_angle = 0.0
            for obs in all_obstructions:
                obs_poly = obs["polygon"]
                obs_h = obs["height"]
                dist = bld_poly.distance(obs_poly)
                if dist < 1.0 or dist > check_dist:
                    continue
                # Er obstruksjonen i denne retningen?
                obs_cx = obs_poly.centroid.x - centroid.x
                obs_cy = obs_poly.centroid.y - centroid.y
                obs_az = math.degrees(math.atan2(obs_cx, obs_cy)) % 360
                angle_diff = abs(obs_az - azimuth)
                if angle_diff > 180:
                    angle_diff = 360 - angle_diff
                if angle_diff < 60:  # Innenfor ±60° av fasaderetningen
                    obstruction_angle = math.degrees(math.atan2(max(obs_h - bld_h * 0.5, 0), max(dist, 1)))
                    max_obstruction_angle = max(max_obstruction_angle, obstruction_angle)

            # Dagslysindikator: 0-100 basert på obstruksjonsvinkel
            # <10° = utmerket, 10-20° = god, 20-30° = akseptabel, >30° = svak
            if max_obstruction_angle < 10:
                score = 95.0
                rating = "Utmerket"
            elif max_obstruction_angle < 20:
                score = 75.0
                rating = "God"
            elif max_obstruction_angle < 30:
                score = 55.0
                rating = "Akseptabel"
            elif max_obstruction_angle < 45:
                score = 35.0
                rating = "Svak"
            else:
                score = 15.0
                rating = "Utilstrekkelig"

            # Sør-fasade har naturlig mer dagslys
            if card_name == "Sør":
                score = min(100, score * 1.15)
            elif card_name == "Nord":
                score = score * 0.85

            facade_scores.append({
                "building": bld_name,
                "direction": card_name,
                "score": round(score, 0),
                "rating": rating,
                "obstruction_deg": round(max_obstruction_angle, 1),
            })

    result["facades"] = facade_scores
    if facade_scores:
        result["overall_score"] = round(sum(f["score"] for f in facade_scores) / len(facade_scores), 1)
    return result


def calculate_view_score(
    building_polygons: List[Polygon],
    building_heights: List[float],
    neighbor_polygons: List[Dict[str, Any]],
    terrain: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Utsiktsanalyse: beregner fri horisont per bygg og retning.

    Bruker nabobygg og terreng for å estimere visuell åpenhet.
    """
    result = {"available": True, "buildings": [], "overall_score": 0.0}
    if not building_polygons:
        result["available"] = False
        return result

    building_scores = []
    for bld_idx, (bld_poly, bld_h) in enumerate(zip(building_polygons, building_heights)):
        centroid = bld_poly.centroid
        bld_name = f"Bygg {chr(65 + bld_idx)}"

        # Sjekk 8 retninger for fri sikt fra øverste etasje
        directions = ["N", "NØ", "Ø", "SØ", "S", "SV", "V", "NV"]
        azimuths = [0, 45, 90, 135, 180, 225, 270, 315]
        dir_scores = []

        for direction, azimuth in zip(directions, azimuths):
            az_rad = math.radians(azimuth)
            max_block_angle = 0.0

            for nb in neighbor_polygons:
                nb_poly = nb.get("polygon")
                if nb_poly is None:
                    continue
                nb_h = float(nb.get("height_m", 9))
                dist = bld_poly.distance(nb_poly)
                if dist < 1.0 or dist > 200.0:
                    continue

                nb_cx = nb_poly.centroid.x - centroid.x
                nb_cy = nb_poly.centroid.y - centroid.y
                nb_az = math.degrees(math.atan2(nb_cx, nb_cy)) % 360
                angle_diff = abs(nb_az - azimuth)
                if angle_diff > 180:
                    angle_diff = 360 - angle_diff
                if angle_diff < 30:
                    height_diff = max(nb_h - bld_h, 0)
                    if height_diff > 0:
                        block_angle = math.degrees(math.atan2(height_diff, max(dist, 1)))
                        max_block_angle = max(max_block_angle, block_angle)

            # Score: 100 = helt fri sikt, 0 = fullstendig blokkert
            view = max(0.0, 100.0 - max_block_angle * 4.0)
            dir_scores.append({"direction": direction, "score": round(view, 0)})

        avg = sum(d["score"] for d in dir_scores) / max(len(dir_scores), 1)
        best_dir = max(dir_scores, key=lambda d: d["score"])
        building_scores.append({
            "building": bld_name,
            "average_score": round(avg, 0),
            "best_direction": best_dir["direction"],
            "best_score": best_dir["score"],
            "directions": dir_scores,
        })

    result["buildings"] = building_scores
    if building_scores:
        result["overall_score"] = round(sum(b["average_score"] for b in building_scores) / len(building_scores), 1)
    return result


@st.cache_data(show_spinner=False, ttl=60 * 60 * 12)
def fetch_wind_data(latitude: float, longitude: float) -> Dict[str, Any]:
    """Hent vinddata fra MET Frost API (nærmeste stasjon)."""
    client_id = os.environ.get("MET_FROST_CLIENT_ID", "")
    result: Dict[str, Any] = {"available": False, "source": "Ingen vinddata"}

    if not client_id:
        # Fallback: bruk generelle norske vinddata basert på kystlinje/innland
        is_coastal = longitude > 5.0 and latitude > 58.0  # Grov sjekk
        result["available"] = True
        result["source"] = "Estimat basert på plassering"
        result["dominant_direction"] = "SV" if is_coastal else "S"
        result["avg_speed_ms"] = 4.5 if is_coastal else 2.8
        result["max_gust_ms"] = 18.0 if is_coastal else 12.0
        result["exposure"] = "Moderat eksponert" if is_coastal else "Lav eksponering"
        return result

    try:
        # Finn nærmeste stasjon
        resp = requests.get(
            "https://frost.met.no/sources/v0.jsonld",
            params={"geometry": f"nearest(POINT({longitude} {latitude}))", "nearestmaxcount": "1"},
            auth=(client_id, ""),
            timeout=10,
        )
        if resp.status_code == 200:
            sources = resp.json().get("data", [])
            if sources:
                station_id = sources[0].get("id", "")
                station_name = sources[0].get("name", "")
                # Hent vindstatistikk
                wind_resp = requests.get(
                    "https://frost.met.no/observations/v0.jsonld",
                    params={
                        "sources": station_id,
                        "elements": "wind_speed,wind_from_direction,max(wind_speed_of_gust PT1H)",
                        "referencetime": "latest",
                    },
                    auth=(client_id, ""),
                    timeout=10,
                )
                if wind_resp.status_code == 200:
                    obs = wind_resp.json().get("data", [])
                    if obs:
                        result["available"] = True
                        result["source"] = f"MET Frost: {station_name}"
                        result["station"] = station_name
                        for o in obs:
                            for v in o.get("observations", []):
                                eid = v.get("elementId", "")
                                val = safe_float(v.get("value"), 0)
                                if "wind_speed" in eid and "gust" not in eid:
                                    result["avg_speed_ms"] = round(val, 1)
                                elif "direction" in eid:
                                    result["dominant_direction_deg"] = round(val, 0)
                                    dirs = ["N", "NØ", "Ø", "SØ", "S", "SV", "V", "NV"]
                                    result["dominant_direction"] = dirs[int((val + 22.5) % 360 / 45)]
                                elif "gust" in eid:
                                    result["max_gust_ms"] = round(val, 1)
    except Exception:
        pass
    return result


def _wind_comfort_estimate(
    building_polygons: List[Polygon],
    building_heights: List[float],
    wind_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Forenklet vindkomfort basert på Lawson-kriteriene."""
    result = {"available": bool(wind_data.get("available")), "zones": [], "overall": "Ikke vurdert"}
    if not result["available"] or not building_polygons:
        return result

    avg_wind = float(wind_data.get("avg_speed_ms", 3.0))
    n_buildings = len(building_polygons)

    # Forenklet vurdering basert på bygningskonfigurasjon
    if n_buildings <= 1:
        amplification = 1.0
    else:
        # Sjekk avstand mellom bygninger — trang passasje gir venturi-effekt
        min_gap = float("inf")
        for i in range(n_buildings):
            for j in range(i + 1, n_buildings):
                gap = building_polygons[i].distance(building_polygons[j])
                if gap > 0:
                    min_gap = min(min_gap, gap)

        max_h = max(building_heights) if building_heights else 10
        if min_gap < max_h * 0.5:
            amplification = 1.6  # Sterk venturi
        elif min_gap < max_h:
            amplification = 1.3  # Moderat
        else:
            amplification = 1.1  # Minimal

    effective_wind = avg_wind * amplification
    # Lawson-kriterier (forenklet)
    if effective_wind < 3.5:
        result["overall"] = "Komfortabel (sitting/opphold)"
        result["lawson_class"] = "A"
    elif effective_wind < 5.5:
        result["overall"] = "Akseptabel (gange)"
        result["lawson_class"] = "B"
    elif effective_wind < 8.0:
        result["overall"] = "Ukomfortabel (rask gange)"
        result["lawson_class"] = "C"
    else:
        result["overall"] = "Ubehagelig (vurdér vindskjerming)"
        result["lawson_class"] = "D"

    result["effective_wind_ms"] = round(effective_wind, 1)
    result["amplification_factor"] = round(amplification, 2)
    result["min_building_gap_m"] = round(min_gap, 1) if min_gap < float("inf") else None
    return result


def build_environment_analysis(
    site_polygon: Optional[Polygon],
    building_polygons: List[Polygon],
    building_heights: List[float],
    neighbors: List[Dict[str, Any]],
    latitude_deg: float,
    longitude_deg: Optional[float] = None,
    terrain: Optional[Dict[str, Any]] = None,
    gdo_client: Any = None,
) -> Dict[str, Any]:
    """Kjør komplett miljøanalyse: støy, dagslys, utsikt, vind."""
    env: Dict[str, Any] = {"available": False}

    # 1. Støy (Geodata Online DOK Forurensning → Geonorge fallback)
    if site_polygon is not None:
        try:
            env["noise"] = fetch_noise_zones(site_polygon.bounds, gdo_client=gdo_client)
        except Exception as noise_exc:
            env["noise"] = {"available": False, "debug": [f"Exception: {str(noise_exc)[:80]}"]}
    else:
        env["noise"] = {"available": False, "debug": ["Ingen site_polygon"]}

    # 2. Dagslys (TEK17 §13-7)
    try:
        env["daylight"] = calculate_daylight_tek17(
            site_polygon or Polygon(),
            building_polygons,
            building_heights,
            neighbors,
            latitude_deg,
        )
    except Exception:
        env["daylight"] = {"available": False}

    # 3. Utsikt
    try:
        env["views"] = calculate_view_score(
            building_polygons,
            building_heights,
            neighbors,
            terrain,
        )
    except Exception:
        env["views"] = {"available": False}

    # 4. Vind
    try:
        wind = fetch_wind_data(latitude_deg, longitude_deg or 10.4)
        env["wind"] = wind
        env["wind_comfort"] = _wind_comfort_estimate(building_polygons, building_heights, wind)
    except Exception:
        env["wind"] = {"available": False}
        env["wind_comfort"] = {"available": False}

    env["available"] = any(
        env.get(k, {}).get("available", False)
        for k in ["noise", "daylight", "views", "wind"]
    )
    return env


def render_plan_diagram(site: SiteInputs, option: OptionResult) -> Image.Image:
    """
    Isometrisk 3D-volumskisse.
    Viser foreslatte volumer, nabobygg og tomtegrense fra skraa vinkel
    slik at siktlinjer, hoyder og romlige forhold er tydelige.
    """
    canvas_w, canvas_h = 1100, 900
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
    Z_SCALE = 1.2

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
    screen_cy = canvas_h * 0.65  # Langt ned for å gi maks plass til høyde

    def iso_project(x: float, y: float, z: float = 0.0) -> Tuple[float, float]:
        dx = (x - cx) * pixel_scale
        dy = (y - cy) * pixel_scale
        sx = screen_cx + (dx - dy) * COS_A
        sy = screen_cy + (dx + dy) * SIN_A * 0.5 - z * pixel_scale * Z_SCALE
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
    view_radius = site_span * 0.45
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


def render_plan_view(site: SiteInputs, option: OptionResult) -> Image.Image:
    """
    Planvisning (fugleperspektiv / top-down) av volumskisse.
    Viser tomtegrense, byggefelt, fotavtrykk og bygninger med etasjefarge-koding.
    """
    canvas_w, canvas_h = 1100, 780
    img = Image.new('RGBA', (canvas_w, canvas_h), (240, 243, 248, 255))
    draw = ImageDraw.Draw(img, 'RGBA')
    font = ImageFont.load_default()

    site_coords = option.geometry.get('site_polygon_coords') or geometry_to_coord_groups(box(0, 0, site.site_width_m, site.site_depth_m))
    buildable_coords = option.geometry.get('buildable_polygon_coords') or site_coords
    massing_parts = option.geometry.get('massing_parts', []) or []
    neighbor_polys = option.geometry.get('neighbor_polygons', [])

    site_pts = flatten_coord_groups(site_coords)
    if not site_pts:
        site_pts = [[0.0, 0.0], [site.site_width_m, site.site_depth_m]]
    sxs = [p[0] for p in site_pts]
    sys_ = [p[1] for p in site_pts]
    cx = (min(sxs) + max(sxs)) / 2.0
    cy = (min(sys_) + max(sys_)) / 2.0
    site_span = max(max(sxs) - min(sxs), max(sys_) - min(sys_), 1.0)

    margin = 60
    target_span = min(canvas_w, canvas_h) - 2 * margin
    scale = target_span / site_span
    ox = canvas_w / 2.0
    oy = canvas_h / 2.0

    def proj(x: float, y: float) -> Tuple[float, float]:
        return ox + (x - cx) * scale, oy + (y - cy) * scale

    def pts(coords):
        return [proj(p[0], p[1]) for p in coords if len(p) >= 2]

    # Tomtegrense
    sp = pts(flatten_coord_groups(site_coords))
    if len(sp) >= 3:
        draw.polygon(sp, fill=(220, 225, 233, 180), outline=(100, 110, 130, 220))

    # Byggefelt
    bp = pts(flatten_coord_groups(buildable_coords))
    if len(bp) >= 3:
        draw.polygon(bp, fill=(200, 218, 240, 60), outline=(56, 140, 248, 120))

    # Nabobygg
    for neighbor in neighbor_polys:
        ncoords = neighbor.get('coords') or neighbor.get('polygon_coords', [])
        if isinstance(ncoords, str):
            continue
        np_ = pts(flatten_coord_groups(ncoords))
        if len(np_) >= 3:
            draw.polygon(np_, fill=(180, 185, 195, 120), outline=(140, 145, 155, 180))

    # Bygningsvolumer
    for part in massing_parts:
        pcoords = flatten_coord_groups(part.get('coords', []))
        if not pcoords:
            continue
        pp = pts(pcoords)
        if len(pp) < 3:
            continue
        base_c = part.get('color', [34, 197, 94, 200])
        base_c = tuple(int(v) if v > 1 else int(v * 255) for v in base_c)
        if len(base_c) < 4:
            base_c = (base_c[0], base_c[1], base_c[2], 200)
        draw.polygon(pp, fill=base_c, outline=(255, 255, 255, 220))
        draw.line(pp + [pp[0]], fill=(255, 255, 255, 220), width=2)

        # Label
        avg_x = sum(p[0] for p in pp) / len(pp)
        avg_y = sum(p[1] for p in pp) / len(pp)
        floors = part.get('floors', 0)
        name = part.get('name', '')
        draw.text((avg_x - 20, avg_y - 8), f"{name}", fill=(30, 30, 30, 255), font=font)
        draw.text((avg_x - 15, avg_y + 4), f"{floors} et.", fill=(60, 60, 60, 220), font=font)

    # Nordpil
    ax, ay = canvas_w - 55, 50
    draw.line((ax, ay + 22, ax, ay - 16), fill=(60, 70, 90, 200), width=3)
    draw.polygon([(ax, ay - 25), (ax - 7, ay - 7), (ax + 7, ay - 7)], fill=(60, 70, 90, 200))
    draw.text((ax - 4, ay + 26), 'N', fill=(60, 70, 90, 200), font=font)

    # Målestokk
    scale_bar_m = 10.0
    while scale_bar_m * scale < 40:
        scale_bar_m *= 2
    while scale_bar_m * scale > 200:
        scale_bar_m /= 2
    bar_px = scale_bar_m * scale
    bx, by_s = 40, canvas_h - 50
    draw.line([(bx, by_s), (bx + bar_px, by_s)], fill=(60, 70, 90, 200), width=2)
    draw.line([(bx, by_s - 4), (bx, by_s + 4)], fill=(60, 70, 90, 200), width=2)
    draw.line([(bx + bar_px, by_s - 4), (bx + bar_px, by_s + 4)], fill=(60, 70, 90, 200), width=2)
    draw.text((bx + bar_px / 2 - 10, by_s - 16), f"{scale_bar_m:.0f} m", fill=(60, 70, 90, 220), font=font)

    # Infopanel
    yt = canvas_h - 35
    draw.rectangle([(0, yt - 2), (canvas_w, canvas_h)], fill=(240, 243, 248, 240))
    title = f"PLANVISNING | {option.name} | {option.typology}"
    draw.text((30, yt), title, fill=(26, 43, 72, 255), font=font)
    draw.text((30, yt + 14), f"BTA {option.gross_bta_m2:.0f} m2 | {option.unit_count} boliger | Fotavtrykk {option.footprint_area_m2:.0f} m2", fill=(80, 90, 110, 220), font=font)

    return img.convert('RGB')


def render_sketch_views(site: SiteInputs, sketch_option: OptionResult) -> List[Image.Image]:
    """
    Renderer multiple visninger av en manuell skisse for PDF-rapport.

    Returnerer [isometrisk, planvisning] — begge som PIL Image.
    """
    views: List[Image.Image] = []

    # 1. Isometrisk volumskisse (standard)
    try:
        iso_img = render_plan_diagram(site, sketch_option)
        views.append(iso_img)
    except Exception:
        pass

    # 2. Planvisning (fugleperspektiv)
    try:
        plan_img = render_plan_view(site, sketch_option)
        views.append(plan_img)
    except Exception:
        pass

    return views


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
        'stats': {
            'bta_m2': round(option.gross_bta_m2, 0),
            'bra_m2': round(option.saleable_area_m2, 0),
            'pct_bra': round(option.gross_bta_m2 * option.efficiency_ratio / max(site.site_area_m2, 1) * 100, 0),
            'boliger': option.unit_count,
            'etasjer': option.floors,
            'hoyde_m': round(option.building_height_m, 1),
            'sol': round(option.solar_score, 0),
            'fotavtrykk_m2': round(option.footprint_area_m2, 0),
        },
    }


def _build_batch_capture_html(all_payloads_json: str, height_px: int = 620) -> str:
    """
    Lager en ArcGIS SceneView som automatisk cycler gjennom alle alternativer,
    tar screenshot av hvert, og laster dem ned som PNG-filer.
    """
    return f"""
    <div id="batchContainer" style="position:relative;width:100%;height:{height_px}px;border-radius:14px;overflow:hidden;border:2px solid rgba(56,189,248,0.5);">
      <div id="batchView" style="width:100%;height:100%;"></div>
      <div id="batchStatus" style="position:absolute;top:12px;left:12px;background:rgba(6,17,26,0.92);border:1px solid rgba(56,189,248,0.4);border-radius:8px;padding:10px 16px;color:#38bdf8;font-family:system-ui;font-size:13px;font-weight:600;">
        Starter batch-capture...
      </div>
      <div id="batchStats" style="position:absolute;top:12px;right:12px;background:rgba(6,17,26,0.88);border:1px solid rgba(56,189,248,0.3);border-radius:10px;padding:12px 16px;color:#ecf0f5;font-family:system-ui;font-size:13px;line-height:1.6;min-width:180px;"></div>
    </div>
    <script src="https://js.arcgis.com/4.30/"></script>
    <link rel="stylesheet" href="https://js.arcgis.com/4.30/esri/themes/dark/main.css">
    <script>
    const allPayloads = {all_payloads_json};
    let currentIdx = 0;

    require([
      'esri/Map', 'esri/views/SceneView', 'esri/layers/ImageryLayer',
      'esri/layers/ElevationLayer', 'esri/layers/GraphicsLayer', 'esri/Graphic',
      'esri/geometry/Polygon', 'esri/geometry/Point', 'esri/geometry/SpatialReference',
      'esri/geometry/Extent', 'esri/identity/IdentityManager'
    ], function(Map, SceneView, ImageryLayer, ElevationLayer, GraphicsLayer, Graphic, Polygon, Point, SpatialReference, Extent, IdentityManager) {{
      const sr = new SpatialReference({{ wkid: 4326 }});
      const p0 = allPayloads[0] || {{}};
      const sc = p0.scene_config || {{}};
      const services = sc.services || {{}};
      const tkn = sc.token || '';

      if (tkn) {{
        IdentityManager.registerToken({{ server: 'https://services.geodataonline.no/arcgis', token: tkn }});
      }}

      const map = new Map({{ basemap: 'satellite', ground: 'world-elevation' }});
      if (services.elevation_url && tkn) {{
        map.ground.layers.add(new ElevationLayer({{ url: services.elevation_url, customParameters: {{ token: tkn }} }}));
      }}
      if (services.imagery_latest_url && tkn) {{
        map.add(new ImageryLayer({{ url: services.imagery_latest_url, opacity: 0.92, customParameters: {{ token: tkn }} }}));
      }}

      const graphicsLayer = new GraphicsLayer();
      map.add(graphicsLayer);

      function polygonFromRings(rings) {{
        return new Polygon({{ rings: rings, spatialReference: sr }});
      }}

      function loadOption(payload) {{
        graphicsLayer.removeAll();
        // Tomtegrense
        (payload.site.rings || []).forEach(ring => {{
          graphicsLayer.add(new Graphic({{
            geometry: polygonFromRings([ring]),
            symbol: {{ type: 'simple-fill', color: [255,255,255,0.02], outline: {{ color: [210,220,235,0.6], width: 1.2 }} }}
          }}));
        }});
        // Volumer
        (payload.massing_parts || []).forEach(item => {{
          (item.rings || []).forEach(ring => {{
            const useColor = Array.isArray(item.color) ? [item.color[0], item.color[1], item.color[2], 0.82] : [34,197,94,0.82];
            graphicsLayer.add(new Graphic({{
              geometry: polygonFromRings([ring]),
              symbol: {{ type: 'polygon-3d', symbolLayers: [{{ type: 'extrude', size: item.height_m || 3, material: {{ color: useColor }}, edges: {{ type: 'solid', color: [255,255,255,0.45], size: 0.6 }} }}] }}
            }}));
          }});
        }});
        // Naboer
        (payload.neighbors || []).forEach(item => {{
          (item.rings || []).forEach(ring => {{
            graphicsLayer.add(new Graphic({{
              geometry: polygonFromRings([ring]),
              symbol: {{ type: 'polygon-3d', symbolLayers: [{{ type: 'extrude', size: item.height_m || 3, material: {{ color: [140,140,150,0.32] }}, edges: {{ type: 'solid', color: [200,200,210,0.25], size: 0.6 }} }}] }}
            }}));
          }});
        }});
        // Nøkkeltall
        const s = payload.stats || {{}};
        document.getElementById('batchStats').innerHTML =
          '<div style="color:#38bdf8;font-weight:700;font-size:11px;letter-spacing:0.5px;margin-bottom:4px;">' + (payload.site_name || '') + '</div>' +
          '<div style="font-weight:700;font-size:15px;">BTA: ' + (s.bta_m2||0).toLocaleString('nb-NO') + ' m²</div>' +
          '<div style="font-size:12px;color:#9fb0c3;">BRA: ' + (s.bra_m2||0).toLocaleString('nb-NO') + ' m²</div>' +
          '<div style="font-size:12px;color:#9fb0c3;">Boliger: ~' + (s.boliger||0) + '</div>' +
          '<div style="font-size:12px;color:#9fb0c3;">' + (s.etasjer||0) + ' et. / ' + (s.hoyde_m||0) + ' m</div>' +
          '<div style="font-size:12px;color:#9fb0c3;">Sol: ' + (s.sol||0) + '/100</div>';
      }}

      const centroid = p0.site_centroid || [10.75, 59.91];
      const view = new SceneView({{
        container: 'batchView', map: map, qualityProfile: 'high',
        camera: {{ position: {{ x: centroid[0], y: centroid[1], z: 900 }}, tilt: 68, heading: 20, spatialReference: sr }},
        environment: {{ atmosphereEnabled: true, starsEnabled: false }}
      }});

      function captureAndNext() {{
        if (currentIdx >= allPayloads.length) {{
          document.getElementById('batchStatus').textContent = 'Ferdig! ' + allPayloads.length + ' bilder lastet ned.';
          document.getElementById('batchStatus').style.color = '#22c55e';
          return;
        }}
        const payload = allPayloads[currentIdx];
        document.getElementById('batchStatus').textContent = 'Rendrer ' + (currentIdx+1) + '/' + allPayloads.length + ': ' + (payload.site_name || '');
        loadOption(payload);

        setTimeout(function() {{
          view.takeScreenshot({{ format: 'png', quality: 95, width: 1920, height: 1080 }}).then(function(screenshot) {{
            const a = document.createElement('a');
            a.href = screenshot.dataUrl;
            a.download = 'terreng_' + (payload.site_name || 'alt').replace(/[^a-zA-Z0-9]/g, '_') + '.png';
            a.click();
            currentIdx++;
            setTimeout(captureAndNext, 1500);
          }});
        }}, 3000);
      }}

      view.when(function() {{
        // Zoom til tomten
        let extent = null;
        const rings = [].concat(p0.site.rings || []);
        rings.forEach(ring => {{
          ring.forEach(pt => {{
            if (!extent) {{ extent = new Extent({{ xmin: pt[0], ymin: pt[1], xmax: pt[0], ymax: pt[1], spatialReference: sr }}); }}
            else {{ extent.xmin = Math.min(extent.xmin, pt[0]); extent.ymin = Math.min(extent.ymin, pt[1]); extent.xmax = Math.max(extent.xmax, pt[0]); extent.ymax = Math.max(extent.ymax, pt[1]); }}
          }});
        }});
        if (extent) {{
          view.goTo(extent.expand(1.8)).then(function() {{
            setTimeout(captureAndNext, 2000);
          }});
        }} else {{
          setTimeout(captureAndNext, 2000);
        }}
      }});
    }});
    </script>
    """


def render_geodata_scene(site: SiteInputs, option: OptionResult, scene_config: Dict[str, Any], height_px: int = 640) -> None:
    payload = build_geodata_scene_payload(site, option, scene_config)
    payload_json = json.dumps(payload, ensure_ascii=False)
    html_template = """
    <div id="sceneContainer" style="position:relative;width:100%;height:__HEIGHT__px;border-radius:14px;overflow:hidden;border:1px solid rgba(255,255,255,0.08);">
      <div id="viewDiv" style="width:100%;height:100%;"></div>
      <div id="statsPanel" style="position:absolute;top:12px;right:12px;background:rgba(6,17,26,0.88);border:1px solid rgba(56,189,248,0.3);border-radius:10px;padding:12px 16px;color:#ecf0f5;font-family:-apple-system,system-ui,sans-serif;font-size:13px;line-height:1.6;pointer-events:none;min-width:180px;backdrop-filter:blur(8px);">
        <div style="color:#38bdf8;font-weight:700;font-size:11px;letter-spacing:0.5px;margin-bottom:4px;">NØKKELTALL</div>
        <div style="font-weight:700;font-size:16px;" id="statBTA"></div>
        <div style="font-size:12px;color:#9fb0c3;" id="statBRA"></div>
        <div style="font-size:12px;color:#f87171;font-weight:600;" id="statPctBRA"></div>
        <div style="font-size:12px;color:#9fb0c3;" id="statBoliger"></div>
        <div style="font-size:12px;color:#9fb0c3;" id="statEtasjer"></div>
        <div style="font-size:12px;color:#9fb0c3;" id="statSol"></div>
      </div>
      <button id="screenshotBtn" onclick="captureScene()" style="position:absolute;bottom:12px;right:12px;background:rgba(56,189,248,0.9);color:#fff;border:none;border-radius:8px;padding:8px 16px;font-size:12px;font-weight:600;cursor:pointer;pointer-events:auto;backdrop-filter:blur(4px);display:flex;align-items:center;gap:6px;">
        📸 Last ned bilde
      </button>
    </div>
    <script src="https://js.arcgis.com/4.30/"></script>
    <link rel="stylesheet" href="https://js.arcgis.com/4.30/esri/themes/dark/main.css">
    <script>
    const payload = __PAYLOAD__;
    // Fyll stats-panel
    const s = payload.stats || {};
    document.getElementById('statBTA').textContent = 'BTA: ' + (s.bta_m2 || 0).toLocaleString('nb-NO') + ' m²';
    document.getElementById('statBRA').textContent = 'BRA: ' + (s.bra_m2 || 0).toLocaleString('nb-NO') + ' m²';
    document.getElementById('statPctBRA').textContent = '%-BRA: ' + (s.pct_bra || 0) + '%';
    document.getElementById('statBoliger').textContent = 'Boliger: ~' + (s.boliger || 0);
    document.getElementById('statEtasjer').textContent = (s.etasjer || 0) + ' etasjer / ' + (s.hoyde_m || 0) + ' m';
    document.getElementById('statSol').textContent = 'Sol: ' + (s.sol || 0) + '/100';

    let _sceneView = null;
    function captureScene() {
      if (!_sceneView) return;
      _sceneView.takeScreenshot({ format: 'png', quality: 95, width: 1920, height: 1080 }).then(function(screenshot) {
        const a = document.createElement('a');
        a.href = screenshot.dataUrl;
        a.download = 'terrengscene_' + (payload.site_name || 'scene').replace(/[^a-zA-Z0-9]/g, '_') + '.png';
        a.click();
      });
    }

    require([
      'esri/Map',
      'esri/views/SceneView',
      'esri/layers/ImageryLayer',
      'esri/layers/ElevationLayer',
      'esri/layers/GraphicsLayer',
      'esri/Graphic',
      'esri/geometry/Polygon',
      'esri/geometry/Point',
      'esri/geometry/SpatialReference',
      'esri/geometry/Extent',
      'esri/identity/IdentityManager'
    ], function(Map, SceneView, ImageryLayer, ElevationLayer, GraphicsLayer, Graphic, Polygon, Point, SpatialReference, Extent, IdentityManager) {
      const sr = new SpatialReference({ wkid: 4326 });
      const sc = payload.scene_config || {};
      const services = sc.services || {};
      const tkn = sc.token || '';

      // Pre-register token for map services
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

      // Høyde-labels over foreslåtte bygninger
      (payload.massing_parts || []).forEach(item => {
        (item.rings || []).forEach(ring => {
          if (!ring || ring.length < 3) return;
          let cx = 0, cy = 0;
          ring.forEach(pt => { cx += pt[0]; cy += pt[1]; });
          cx /= ring.length; cy /= ring.length;
          const h = item.height_m || 3;
          const fl = item.floors || '?';
          const label = (item.name || '') + '\\n' + fl + ' et. / ' + h.toFixed(0) + ' m';
          graphicsLayer.add(new Graphic({
            geometry: new Point({ x: cx, y: cy, z: 0, spatialReference: sr }),
            symbol: {
              type: 'point-3d',
              verticalOffset: { screenLength: 30, maxWorldLength: h + 12, minWorldLength: h + 2 },
              callout: { type: 'line', size: 1, color: [255, 255, 255, 150] },
              symbolLayers: [{
                type: 'text',
                material: { color: [255, 255, 255] },
                text: label,
                size: 11,
                font: { weight: 'bold' },
                halo: { color: [0, 0, 0], size: 1.5 }
              }]
            }
          }));
        });
      });

      // Høyde-labels over nabobygg (dempet stil)
      (payload.neighbors || []).forEach(item => {
        if (!item.height_m || item.height_m < 3) return;
        (item.rings || []).forEach(ring => {
          if (!ring || ring.length < 3) return;
          let cx = 0, cy = 0;
          ring.forEach(pt => { cx += pt[0]; cy += pt[1]; });
          cx /= ring.length; cy /= ring.length;
          const h = item.height_m || 3;
          graphicsLayer.add(new Graphic({
            geometry: new Point({ x: cx, y: cy, z: 0, spatialReference: sr }),
            symbol: {
              type: 'point-3d',
              verticalOffset: { screenLength: 20, maxWorldLength: h + 8, minWorldLength: h },
              symbolLayers: [{
                type: 'text',
                material: { color: [200, 210, 225] },
                text: h.toFixed(0) + ' m',
                size: 9,
                font: { weight: 'normal' },
                halo: { color: [0, 0, 0], size: 1.2 }
              }]
            }
          }));
        });
      });

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
      _sceneView = view;
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
<div id="help">Venstre mus: roter | Scroll: zoom | Shift+dra: panorer</div>
<div id="sunControls" style="position:absolute;bottom:40px;left:14px;right:14px;background:rgba(6,17,26,0.88);border:1px solid rgba(56,189,248,0.25);border-radius:10px;padding:10px 16px;display:flex;gap:16px;align-items:center;font:12px -apple-system,sans-serif;">
  <span style="color:#38bdf8;font-weight:700;white-space:nowrap;">☀ Sol/skygge</span>
  <label style="color:#9fb0c3;white-space:nowrap;">Kl:
    <input id="sunHour" type="range" min="6" max="20" step="0.5" value="12" style="width:120px;accent-color:#38bdf8;vertical-align:middle;">
    <span id="sunHourLabel" style="color:#f5f7fb;font-weight:600;">12:00</span>
  </label>
  <label style="color:#9fb0c3;white-space:nowrap;">Dato:
    <select id="sunSeason" style="background:#0d1824;border:1px solid rgba(120,145,170,0.3);color:#fff;padding:2px 6px;border-radius:4px;font-size:11px;">
      <option value="80">Vår/høst</option>
      <option value="172">Sommer</option>
      <option value="355">Vinter</option>
    </select>
  </label>
  <span id="sunInfo" style="color:#c8d3df;font-size:11px;"></span>
</div>
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
scene.add(sun.target);
scene.add(new THREE.DirectionalLight(0x99bbdd, 0.25).translateX(-camDist).translateY(camDist * 0.3));
scene.add(new THREE.HemisphereLight(0x8899cc, 0x334422, 0.3));

// --- SOL/SKYGGE MOTOR ---
const LAT = __LATITUDE__;
function solarDeclRad(doy) { return 0.4093 * Math.sin(2 * Math.PI / 365 * (doy - 81)); }
function solarAlt(lat, doy, hour) {
  const latR = lat * Math.PI / 180;
  const decl = solarDeclRad(doy);
  const ha = (hour - 12) * 15 * Math.PI / 180;
  const sinAlt = Math.sin(latR) * Math.sin(decl) + Math.cos(latR) * Math.cos(decl) * Math.cos(ha);
  return Math.asin(Math.max(-1, Math.min(1, sinAlt))) * 180 / Math.PI;
}
function solarAz(lat, doy, hour) {
  const latR = lat * Math.PI / 180;
  const decl = solarDeclRad(doy);
  const ha = (hour - 12) * 15 * Math.PI / 180;
  const az = Math.atan2(Math.sin(ha), Math.cos(ha) * Math.sin(latR) - Math.tan(decl) * Math.cos(latR));
  return (az * 180 / Math.PI + 180) % 360;
}
function updateSunPosition(hour, doy) {
  const alt = solarAlt(LAT, doy, hour);
  const az = solarAz(LAT, doy, hour);
  const dist = D.site_span * 1.5;
  if (alt <= 0) {
    sun.intensity = 0.05;
    sun.position.set(0, -dist * 0.2, 0);
    document.getElementById('sunInfo').textContent = 'Under horisonten';
  } else {
    const altRad = alt * Math.PI / 180;
    const azRad = (az - 180) * Math.PI / 180;
    sun.position.set(
      dist * Math.cos(altRad) * Math.sin(azRad),
      dist * Math.sin(altRad),
      dist * Math.cos(altRad) * Math.cos(azRad)
    );
    sun.target.position.set(0, 0, 0);
    sun.intensity = 0.4 + 0.8 * Math.sin(altRad);
    const warmth = Math.max(0, 1 - alt / 50);
    sun.color.setRGB(1.0, 0.96 - warmth * 0.08, 0.88 - warmth * 0.15);
    const shadowLen = alt > 1 ? (16 / Math.tan(altRad)).toFixed(0) : '∞';
    document.getElementById('sunInfo').textContent = 'Solhøyde: ' + alt.toFixed(1) + '° | Skygge ~' + shadowLen + 'm (16m bygg)';
  }
  const hh = Math.floor(hour); const mm = Math.round((hour - hh) * 60);
  document.getElementById('sunHourLabel').textContent = hh + ':' + (mm < 10 ? '0' : '') + mm;
}
document.getElementById('sunHour').addEventListener('input', function() {
  updateSunPosition(parseFloat(this.value), parseInt(document.getElementById('sunSeason').value));
});
document.getElementById('sunSeason').addEventListener('change', function() {
  updateSunPosition(parseFloat(document.getElementById('sunHour').value), parseInt(this.value));
});
updateSunPosition(12, 80);

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

// Nabobygg — med høyde-labels
D.neighbors.forEach(n => {
  const cx = n.rings[0] ? n.rings[0].reduce((s,p) => s + p[0], 0) / n.rings[0].length : 0;
  const cy = n.rings[0] ? n.rings[0].reduce((s,p) => s + p[1], 0) / n.rings[0].length : 0;
  const baseY = D.terrain ? getTerrainY(cx, cy) : 0;
  addVolume(n.rings, n.height, 0x8a8e99, 0.50, false, baseY);
  // Diskret høyde-label
  if (n.height > 0) {
    const lc = document.createElement('canvas');
    lc.width = 128; lc.height = 32;
    const lx = lc.getContext('2d');
    lx.fillStyle = 'rgba(0,0,0,0.0)';
    lx.fillRect(0, 0, 128, 32);
    lx.fillStyle = 'rgba(180,185,195,0.8)';
    lx.font = '14px sans-serif';
    lx.textAlign = 'center';
    lx.fillText(n.height.toFixed(0) + 'm', 64, 20);
    const lt = new THREE.CanvasTexture(lc);
    const lm = new THREE.SpriteMaterial({ map: lt, transparent: true, opacity: 0.7, depthTest: false });
    const ls = new THREE.Sprite(lm);
    ls.position.set(cx, baseY + n.height + D.site_span * 0.015, cy);
    ls.scale.set(D.site_span * 0.10, D.site_span * 0.025, 1);
    scene.add(ls);
  }
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
        '__LATITUDE__', str(round(site.latitude_deg, 4))
    ).replace(
        '__INFO__',
        f"{option.name} | {option.typology} | BTA {option.gross_bta_m2:.0f} m² | {option.unit_count} boliger"
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
    manual_override: Optional[Dict[str, Any]] = None,
    environment_data: Optional[Dict[str, Any]] = None,
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
    has_manual = manual_override is not None
    lines = []
    lines.append("# 1. OPPSUMMERING")

    if has_manual:
        mo = manual_override
        pct_bra_active = site.utnyttelsesgrad_bra_pct > 0
        manual_bra = mo.get('saleable_area_m2', 0)
        manual_pct_bra = round(manual_bra / max(site.site_area_m2, 1) * 100, 0) if pct_bra_active else 0
        if pct_bra_active:
            lines.append(
                f"Brukeren har manuelt overstyrt volumforslaget. "
                f"Den manuelle løsningen gir {manual_bra:.0f} m² BRA ({manual_pct_bra:.0f}% utnyttelse), "
                f"{mo.get('gross_bta_m2', 0):.0f} m² BTA og ca. {mo.get('unit_count', 0)} boliger."
            )
        else:
            lines.append(
                f"Brukeren har manuelt overstyrt volumforslaget. "
                f"Den manuelle løsningen gir {mo.get('gross_bta_m2', 0):.0f} m² BTA, "
                f"{manual_bra:.0f} m² BRA og ca. {mo.get('unit_count', 0)} boliger."
            )
    else:
        best_bra = best.gross_bta_m2 * best.efficiency_ratio
        if site.utnyttelsesgrad_bra_pct > 0:
            best_pct_bra = round(best_bra / max(site.site_area_m2, 1) * 100, 0)
            lines.append(
                f"Beste indikative alternativ er {best.name} ({best.typology}) med score {best.score}/100. "
                f"Det gir omtrent {best_bra:.0f} m² BRA ({best_pct_bra:.0f}% utnyttelse), "
                f"{best.gross_bta_m2:.0f} m² BTA og ca. {best.unit_count} boliger."
            )
        else:
            lines.append(
                f"Beste indikative alternativ er {best.name} ({best.typology}) med score {best.score}/100. "
                f"Det gir omtrent {best.gross_bta_m2:.0f} m² BTA, {best_bra:.0f} m² BRA "
                f"og ca. {best.unit_count} boliger."
            )
    if using_polygon:
        lines.append(
            f"Analysen bruker faktisk tomtepolygon, reelt byggefelt på ca. {best.buildable_area_m2:.0f} m² "
            f"og {best.neighbor_count} nabobygg i sol/skygge-vurderingen."
        )
    if terrain_active:
        lines.append(
            f"Terrenggrunnlag er brukt som en forenklet flate med ca. {best.terrain_slope_pct:.1f}% gjennomsnittlig fall og "
            f"{best.terrain_relief_m:.1f} m lokalt relieff."
        )
    lines.append("")
    lines.append("# 2. GRUNNLAG")
    lines.append(f"- Tomteareal brukt i motor: {site.site_area_m2:.0f} m²")
    lines.append(f"- Tomtedimensjon (omsluttende orientert rektangel): ca. {site.site_width_m:.1f} x {site.site_depth_m:.1f} m")
    lines.append(
        f"- Byggegrenser / inntrekk: front {site.front_setback_m:.1f} m, bak {site.rear_setback_m:.1f} m, side {site.side_setback_m:.1f} m, "
        f"polygonbuffer {site.polygon_setback_m:.1f} m"
    )
    lines.append(f"- Maks BYA: {site.max_bya_pct:.1f}%")
    lines.append(f"- Maks BRA: {'ikke satt' if site.max_bra_m2 <= 0 else f'{site.max_bra_m2:.0f} m²'}")
    lines.append(f"- Maks etasjer: {site.max_floors}")
    lines.append(f"- Maks høyde: {site.max_height_m:.1f} m")
    if site.utnyttelsesgrad_bra_pct > 0:
        target_bra = site.site_area_m2 * site.utnyttelsesgrad_bra_pct / 100.0
        lines.append(f"- %-BRA mål: {site.utnyttelsesgrad_bra_pct:.0f}% → {target_bra:.0f} m² BRA")
    else:
        lines.append(f"- Ønsket BTA: {site.desired_bta_m2:.0f} m²")
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

    # Miljødata: støy, dagslys, vind
    env = environment_data or {}
    noise = env.get("noise", {})
    if noise.get("available") and noise.get("zones"):
        worst = max(noise["zones"], key=lambda z: z.get("db", 0))
        db = worst.get("db", 0)
        src = worst.get("source_type", "vei")
        zone = worst.get("zone", "")
        lines.append("")
        lines.append(f"Støydata fra {noise.get('source', 'kartlegging')}: Tomten er berørt av støysone {zone} ({db:.0f} dB Lden fra {src}).")
        if db >= 65:
            lines.append(
                "Støynivået er over 65 dB og krever spesiell oppmerksomhet ved boligprosjektering. "
                "TEK17 stiller krav til innendørs lydnivå og det vil sannsynligvis kreves gjennomgående leiligheter med stille side, "
                "lydskjermende fasadeløsninger og/eller støyskjerm."
            )
        elif db >= 55:
            lines.append(
                "Støynivået er moderat (55-65 dB). Gjennomgående leiligheter med stille side anbefales. "
                "Balkong og uteoppholdsareal bør orienteres bort fra støykilden."
            )
        else:
            lines.append("Støynivået er under 55 dB, noe som gir gode rammer for boligutvikling uten spesielle støytiltak.")
        if len(noise["zones"]) > 1:
            other_sources = set(z.get("source_type", "") for z in noise["zones"] if z.get("source_type") != src)
            if other_sources:
                lines.append(f"Det er også registrert støy fra: {', '.join(other_sources)}.")
    elif noise.get("source", "").startswith("Geodata"):
        lines.append("")
        lines.append("Støydata er sjekket mot DOK Forurensning. Ingen registrerte støysoner berører tomten direkte.")

    daylight = env.get("daylight", {})
    if daylight.get("available") and daylight.get("overall_score", 0) > 0:
        dl = daylight["overall_score"]
        lines.append(f"Dagslysindikator (forenklet TEK17 §13-7): {dl:.0f}/100.")

    wind_c = env.get("wind_comfort", {})
    if wind_c.get("available"):
        lines.append(f"Vindkomfort: Klasse {wind_c.get('lawson_class', '?')} ({wind_c.get('overall', 'ikke vurdert')}).")

    lines.append("")
    lines.append("# 5. REGULERINGSMESSIGE FORHOLD")
    lines.append(
        f"Maks fotavtrykk styres av kombinasjonen av BYA og faktisk byggefelt. I denne runden er beregnet bebbyggbar flate ca. {best.buildable_area_m2:.0f} m². "
        f"Høydebegrensning og etasjeantall gir et indikativt tak på {min(site.max_floors, max(1, int(site.max_height_m // max(site.floor_to_floor_m, 2.8))))} etasjer."
    )
    lines.append("")

    # --- Når manuell overstyring er aktiv: kompakt rapport uten gammel alternativliste ---
    if has_manual:
        mo = manual_override
        pct_bra_active = site.utnyttelsesgrad_bra_pct > 0
        site_area = max(site.site_area_m2, 1.0)
        manual_bra = mo.get('saleable_area_m2', 0)
        manual_bta = mo.get('gross_bta_m2', 0)
        manual_pct_bra = round(manual_bra / site_area * 100, 0) if pct_bra_active else 0

        lines.append("# 6. VALGT VOLUMLØSNING (MANUELL OVERSTYRING)")
        lines.append(
            f"Brukeren har overstyrt motorens forslag med en manuell volumplassering. "
            f"Den manuelle skissen består av {mo.get('n_buildings', '?')} bygg."
        )
        lines.append("")

        if pct_bra_active:
            lines.append(f"- BRA (salgbart areal): {manual_bra:.0f} m²")
            lines.append(f"- %-BRA: {manual_pct_bra:.0f}% (mål: {site.utnyttelsesgrad_bra_pct:.0f}%)")
            lines.append(f"- BTA (bruttoareal): {manual_bta:.0f} m²")
        else:
            lines.append(f"- BTA (bruttoareal): {manual_bta:.0f} m²")
            lines.append(f"- BRA (salgbart areal): {manual_bra:.0f} m²")

        lines.append(f"- Fotavtrykk: {mo.get('footprint_area_m2', 0):.0f} m²")
        lines.append(f"- Leiligheter: {mo.get('unit_count', 0)}")
        mix_c = mo.get("mix_counts")
        if mix_c:
            lines.append(f"- Fordeling: {json.dumps(mix_c, ensure_ascii=False)}")
        lines.append(f"- Etasjer (maks): {mo.get('floors', '?')}")
        lines.append(f"- Byggehøyde: {mo.get('building_height_m', 0):.1f} m")
        lines.append(f"- Solscore: {mo.get('solar_score', 0):.0f}/100")
        lines.append(f"- Solbelyst uteareal: {mo.get('sunlit_open_space_pct', 0):.0f}%")
        lines.append("")

        lines.append("# 7. SAMMENLIGNING MED MOTORENS FORSLAG")
        lines.append("Motorens automatiske alternativ ble beregnet som referanse. Kun sammendrag vises:")
        for option in options[:5]:  # maks 5 alternativer i kort form
            bra_est = option.gross_bta_m2 * option.efficiency_ratio
            lines.append(
                f"- {option.name}: {option.gross_bta_m2:.0f} m² BTA, "
                f"~{bra_est:.0f} m² BRA, {option.unit_count} boliger, sol {option.solar_score:.0f}/100"
            )
        lines.append("")

        lines.append("# 8. RISIKO OG AVKLARINGSPUNKTER")
        lines.append("- Verifiser reguleringsbestemmelser, kote, gesims, parkeringskrav og uteoppholdsareal mot faktisk plan.")
        if best.neighbor_count > 0 and "Eksakt" in site.site_geometry_source:
            lines.append("- Nabohøyder er hentet automatisk fra matrikkelen. Verifiser mot faktisk situasjon.")
        elif best.neighbor_count > 0:
            lines.append("- Nabohøyder fra GeoJSON/OSM må kvalitetssikres.")
        lines.append("- Terrengmodellen er forenklet og bør erstattes med detaljert kotegrunnlag ved videre prosjektering.")

        if pct_bra_active and manual_pct_bra < site.utnyttelsesgrad_bra_pct * 0.9:
            lines.append(
                f"- OBS: Oppnådd %-BRA ({manual_pct_bra:.0f}%) er lavere enn mål ({site.utnyttelsesgrad_bra_pct:.0f}%). "
                f"Vurder høyere etasjetall eller større fotavtrykk."
            )
        lines.append("")

        lines.append("# 9. ANBEFALING / NESTE STEG")
        lines.append(
            f"Den manuelle skissen er valgt som utgangspunkt for videre bearbeiding. "
            f"Neste steg er å finjustere kjerner og trapper, teste uteopphold og adkomst mot terreng, "
            f"og kontrollere kritiske skyggeforhold i en mer detaljert 3D-modell."
        )
    else:
        # --- STANDARD RAPPORT (uten manuell overstyring) ---
        lines.append("# 6. ARKITEKTONISK VURDERING")
        lines.append(
            f"{best.typology} fremstår som sterkest i denne runden fordi kombinasjonen av volumtreff, solscore ({best.solar_score:.0f}/100), "
            f"solbelyst uteareal ({best.sunlit_open_space_pct:.0f}%) og utnyttelse av faktisk byggefelt er best balansert."
        )
        lines.append("")
        lines.append("# 7. MULIGE UTVIKLINGSGREP")
        for option in options:
            bra_est = option.gross_bta_m2 * option.efficiency_ratio
            if site.utnyttelsesgrad_bra_pct > 0:
                lines.append(
                    f"- {option.name}: {option.typology}, {option.floors} etasjer, ~{bra_est:.0f} m² BRA, "
                    f"{option.unit_count} boliger, solscore {option.solar_score:.0f}/100."
                )
            else:
                lines.append(
                    f"- {option.name}: {option.typology}, {option.floors} etasjer, {option.gross_bta_m2:.0f} m² BTA, "
                    f"{option.unit_count} boliger, solscore {option.solar_score:.0f}/100."
                )
        lines.append("")
        lines.append("# 8. ALTERNATIVER")
        for option in options:
            bra_est = option.gross_bta_m2 * option.efficiency_ratio
            lines.append(f"## {option.name}")
            lines.append(
                f"- Typologi: {option.typology}\n"
                f"- Fotavtrykk: {option.footprint_area_m2:.0f} m²\n"
                f"- BTA: {option.gross_bta_m2:.0f} m²\n"
                f"- BRA (salgbart): ~{bra_est:.0f} m²\n"
                f"- Leiligheter: {option.unit_count} ({json.dumps(option.mix_counts, ensure_ascii=False)})\n"
                f"- Parkering: {option.parking_spaces} plasser\n"
                f"- Solbelyst uteareal: ca. {option.sunlit_open_space_pct:.0f}%\n"
                f"- Vinterskygge kl 12: ca. {option.winter_noon_shadow_m:.0f} m"
            )
            for note in option.notes:
                lines.append(f"- {note}")
        lines.append("")
        lines.append("# 9. RISIKO OG AVKLARINGSPUNKTER")
        lines.append("- Verifiser reguleringsbestemmelser, kote, gesims, parkeringskrav og uteoppholdsareal mot faktisk plan.")
        if best.neighbor_count > 0 and "Eksakt" in site.site_geometry_source:
            lines.append("- Nabohøyder er hentet automatisk fra matrikkelen. Verifiser mot faktisk situasjon.")
        elif best.neighbor_count > 0:
            lines.append("- Nabohøyder fra GeoJSON/OSM må kvalitetssikres.")
        lines.append("- Terrengmodellen er forenklet og bør erstattes med detaljert kotegrunnlag ved videre prosjektering.")
        lines.append("")
        lines.append("# 10. ANBEFALING / NESTE STEG")
        lines.append(
            f"Start videre bearbeiding med {best.name}. Neste steg er å finjustere kjerner og trapper, teste uteopphold og adkomst mot terreng, "
            f"og kontrollere kritiske skyggeforhold i en mer detaljert 3D-modell."
        )

    return "\n".join(lines)


# --- 5. PDF ---
PDF_FONT = "DejaVu" if HAS_DEJAVU else "Helvetica"


def _register_fonts(pdf: FPDF) -> None:
    """Registrer DejaVuSans for UTF-8-støtte (æøå, ², ³) når tilgjengelig."""
    if not HAS_DEJAVU:
        return
    for style in ["", "B", "I"]:
        path = _find_dejavu_font(style)
        if path:
            try:
                pdf.add_font("DejaVu", style, path, uni=True)
            except Exception:
                pass


class BuiltlyProPDF(FPDF):
    def header(self) -> None:
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font(PDF_FONT, "B", 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: ARK-002"), 0, 1, "R")
            self.set_draw_color(200, 200, 200)
            self.line(25, 25, 185, 25)
            self.set_y(30)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font(PDF_FONT, "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f"UTKAST — KREVER FAGLIG KONTROLL | Side {self.page_no()}"), 0, 0, "C")

    def check_space(self, height: float) -> None:
        if self.get_y() + height > 270:
            self.add_page()
            self.set_margins(25, 25, 25)
            self.set_x(25)


def add_pdf_table(pdf: BuiltlyProPDF, headers: List[str], rows: List[List[str]], widths: List[float]) -> None:
    pdf.set_font(PDF_FONT, "B", 9)
    pdf.set_fill_color(232, 239, 247)
    for idx, header in enumerate(headers):
        pdf.cell(widths[idx], 8, clean_pdf_text(header), 1, 0, "C", fill=True)
    pdf.ln()

    pdf.set_font(PDF_FONT, "", 8)
    for row in rows:
        pdf.check_space(8)
        for idx, value in enumerate(row):
            pdf.cell(widths[idx], 8, clean_pdf_text(value), 1, 0, "C")
        pdf.ln()
    pdf.ln(4)


def _render_solar_chart(options: List[OptionResult]) -> Image.Image:
    """Rendrer et horisontalt bar-chart som sammenligner solscore, BRA og boliger per alternativ."""
    w, h = 800, max(280, 50 + len(options) * 40)
    img = Image.new('RGB', (w, h), (6, 17, 26))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    draw.text((20, 10), "SOLANALYSE OG VOLUMSAMMENLIGNING", fill=(56, 189, 248), font=font)

    bar_h = 22
    y_start = 45
    max_sol = max((o.solar_score for o in options), default=100)
    max_bra = max((o.gross_bta_m2 * o.efficiency_ratio for o in options), default=1)

    for i, opt in enumerate(options):
        y = y_start + i * 40
        bra = opt.gross_bta_m2 * opt.efficiency_ratio
        sol = opt.solar_score

        # Typologi-label
        label = f"{opt.typology}"
        draw.text((20, y + 2), label, fill=(200, 211, 223), font=font)

        # Sol-bar (cyan)
        sol_w = max(4, int(sol / max(max_sol, 1) * 280))
        draw.rectangle([(160, y), (160 + sol_w, y + bar_h // 2 - 1)], fill=(56, 189, 248))
        draw.text((165 + sol_w, y - 1), f"Sol {sol:.0f}", fill=(56, 189, 248), font=font)

        # BRA-bar (grønn)
        bra_w = max(4, int(bra / max(max_bra, 1) * 280))
        draw.rectangle([(160, y + bar_h // 2 + 1), (160 + bra_w, y + bar_h)], fill=(34, 197, 94))
        draw.text((165 + bra_w, y + bar_h // 2 + 1), f"BRA {bra:.0f} m²", fill=(34, 197, 94), font=font)

        # Boliger (høyre side)
        draw.text((620, y + 4), f"{opt.unit_count} bol.", fill=(159, 176, 195), font=font)
        draw.text((700, y + 4), f"{opt.floors} et.", fill=(130, 145, 165), font=font)

    # Legende
    ly = h - 25
    draw.rectangle([(20, ly), (32, ly + 10)], fill=(56, 189, 248))
    draw.text((38, ly - 1), "Solscore", fill=(159, 176, 195), font=font)
    draw.rectangle([(120, ly), (132, ly + 10)], fill=(34, 197, 94))
    draw.text((138, ly - 1), "BRA (salgbart areal)", fill=(159, 176, 195), font=font)

    return img


def _render_context_summary(options: List[OptionResult], site: "SiteInputs", environment_data: Optional[Dict[str, Any]] = None) -> Image.Image:
    """Rendrer en kompakt stedskontekst-oppsummering med nøkkeltall og miljødata."""
    env = environment_data or {}
    has_env = bool(env.get("available"))
    h = 200 if not has_env else 270
    w = 800
    img = Image.new('RGB', (w, h), (6, 17, 26))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    draw.text((20, 10), "TOMTE- OG STEDSKONTEKST", fill=(56, 189, 248), font=font)

    best = options[0] if options else None
    if best is None:
        return img

    # Nøkkeltall i rutenett
    items = [
        ("Tomteareal", f"{site.site_area_m2:.0f} m²"),
        ("Byggefelt", f"{best.buildable_area_m2:.0f} m²"),
        ("Nabobygg", f"{site.neighbor_count} stk"),
        ("Maks etasjer", f"{site.max_floors}"),
        ("Maks høyde", f"{site.max_height_m:.0f} m"),
        ("Maks BYA", f"{site.max_bya_pct:.0f}%"),
    ]
    if site.utnyttelsesgrad_bra_pct > 0:
        items.append(("%-BRA mål", f"{site.utnyttelsesgrad_bra_pct:.0f}%"))

    col_w = 190
    for i, (label, value) in enumerate(items):
        col = i % 4
        row = i // 4
        x = 20 + col * col_w
        y = 45 + row * 65

        draw.rectangle([(x, y), (x + col_w - 10, y + 50)], outline=(56, 189, 248, 80))
        draw.text((x + 8, y + 6), label.upper(), fill=(130, 145, 165), font=font)
        draw.text((x + 8, y + 24), value, fill=(245, 247, 251), font=font)

    # Miljødata-rad
    if has_env:
        env_y = 175
        draw.text((20, env_y - 10), "MILJØFORHOLD", fill=(56, 189, 248), font=font)

        noise = env.get("noise", {})
        daylight = env.get("daylight", {})
        wind_c = env.get("wind_comfort", {})

        env_items = []
        if noise.get("available") and noise.get("zones"):
            worst = max(noise["zones"], key=lambda z: z.get("db", 0))
            db = worst.get("db", 0)
            color = (248, 113, 113) if db > 65 else (245, 158, 11) if db > 55 else (52, 211, 153)
            env_items.append(("STØY", f"{db:.0f} dB ({worst.get('source_type', 'vei')})", color))
        else:
            env_items.append(("STØY", "Ingen data", (130, 145, 165)))

        if daylight.get("available"):
            dl = daylight.get("overall_score", 0)
            color = (52, 211, 153) if dl >= 70 else (245, 158, 11) if dl >= 50 else (248, 113, 113)
            env_items.append(("DAGSLYS", f"{dl:.0f}/100", color))

        if wind_c.get("available"):
            wclass = wind_c.get("lawson_class", "?")
            color = (52, 211, 153) if wclass in ["A", "B"] else (245, 158, 11) if wclass == "C" else (248, 113, 113)
            env_items.append(("VIND", f"Klasse {wclass}", color))

        for i, (label, value, color) in enumerate(env_items):
            x = 20 + i * col_w
            draw.rectangle([(x, env_y + 5), (x + col_w - 10, env_y + 55)], outline=(56, 189, 248, 80))
            draw.text((x + 8, env_y + 11), label, fill=(130, 145, 165), font=font)
            draw.text((x + 8, env_y + 29), value, fill=color, font=font)

    return img


def create_full_report_pdf(
    name: str,
    client: str,
    land: str,
    report_text: str,
    options: List[OptionResult],
    option_images: List[Image.Image],
    visual_attachments: List[Image.Image],
    manual_sketch_images: Optional[List[Image.Image]] = None,
    site: Optional["SiteInputs"] = None,
    environment_data: Optional[Dict[str, Any]] = None,
) -> bytes:
    pdf = BuiltlyProPDF()
    _register_fonts(pdf)
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)

    pdf.add_page()
    if os.path.exists("logo.png"):
        pdf.image("logo.png", x=25, y=20, w=50)

    pdf.set_y(95)
    pdf.set_font(PDF_FONT, "B", 22)
    pdf.set_text_color(26, 43, 72)
    pdf.multi_cell(0, 12, clean_pdf_text("MULIGHETSSTUDIE OG TOMTEANALYSE (ARK)"), 0, "L")
    pdf.ln(2)
    pdf.set_font(PDF_FONT, "", 16)
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
        pdf.set_font(PDF_FONT, "B", 10)
        pdf.cell(50, 8, clean_pdf_text(label), 0, 0)
        pdf.set_font(PDF_FONT, "", 10)
        pdf.cell(0, 8, clean_pdf_text(value), 0, 1)

    if options:
        pdf.add_page()
        pdf.set_font(PDF_FONT, "B", 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 12, clean_pdf_text("NØKKELTALL FRA MOTOR"), 0, 1)
        pdf.ln(2)
        rows = []
        for option in options:
            bra_est = option.gross_bta_m2 * option.efficiency_ratio
            rows.append(
                [
                    option.name.replace("Alt ", ""),
                    option.typology,
                    f"{option.gross_bta_m2:.0f}",
                    f"{bra_est:.0f}",
                    str(option.unit_count),
                    f"{option.solar_score:.0f}",
                    f"{option.score:.0f}",
                ]
            )
        add_pdf_table(
            pdf,
            headers=["Alt", "Typologi", "BTA", "BRA", "Enheter", "Sol", "Score"],
            rows=rows,
            widths=[30, 30, 22, 22, 20, 18, 18],
        )

        # --- SOLANALYSE-DIAGRAM ---
        try:
            solar_chart = _render_solar_chart(options)
            pdf.ln(4)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                solar_chart.convert("RGB").save(tmp.name, format="JPEG", quality=92)
                pdf.check_space(70)
                pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                pdf.ln(65)
        except Exception:
            pass

    # --- TOMTEKONTEKST ---
    if options and site is not None:
        try:
            ctx_chart = _render_context_summary(options, site, environment_data=environment_data)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                ctx_chart.convert("RGB").save(tmp.name, format="JPEG", quality=92)
                pdf.check_space(55)
                pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                pdf.ln(50)
        except Exception:
            pass

    # --- TOP 3 VOLUMSKISSER (automatisk) ---
    if option_images and not manual_sketch_images:
        pdf.add_page()
        pdf.set_font(PDF_FONT, "B", 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 12, clean_pdf_text("VOLUMSKISSER — TOPP 3 ALTERNATIVER"), 0, 1)
        pdf.ln(2)
        for i, image in enumerate(option_images[:3]):
            pdf.check_space(88)
            if i < len(options):
                opt = options[i]
                bra = opt.gross_bta_m2 * opt.efficiency_ratio
                pdf.set_font(PDF_FONT, "B", 11)
                pdf.set_text_color(50, 50, 50)
                pdf.cell(0, 8, clean_pdf_text(
                    f"{opt.name} — {opt.typology} | ~{bra:.0f} m² BRA | {opt.unit_count} bol. | Sol {opt.solar_score:.0f}/100"
                ), 0, 1)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                image.convert("RGB").save(tmp.name, format="JPEG", quality=88)
                pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                pdf.ln(82)

    # Volumskisser: vis KUN manuell skisse hvis bruker har overstyrt
    if manual_sketch_images:
        pdf.add_page()
        pdf.set_font(PDF_FONT, "B", 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 12, clean_pdf_text("VALGT VOLUMLØSNING"), 0, 1)
        pdf.ln(2)
        pdf.set_font(PDF_FONT, "", 10)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(150, 5, clean_pdf_text(
            "Bildene nedenfor viser den manuelt redigerte volumplasseringen "
            "som er valgt for videre bearbeiding."
        ))
        pdf.ln(4)
        view_labels = ["Isometrisk volumskisse", "Planvisning (fugleperspektiv)", "3D-terrengscene", "Detalj"]
        for i, image in enumerate(manual_sketch_images):
            pdf.check_space(100)
            label = view_labels[i] if i < len(view_labels) else f"Visning {i + 1}"
            pdf.set_font(PDF_FONT, "B", 11)
            pdf.set_text_color(50, 50, 50)
            pdf.cell(0, 8, clean_pdf_text(label), 0, 1)
            pdf.set_font(PDF_FONT, "", 10)
            pdf.set_text_color(0, 0, 0)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                image.convert("RGB").save(tmp.name, format="JPEG", quality=90)
                pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                pdf.ln(90)

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
            pdf.set_font(PDF_FONT, "B", 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace("#", "").strip()))
            pdf.ln(1)
            pdf.set_font(PDF_FONT, "", 10)
            pdf.set_text_color(0, 0, 0)
        elif line.startswith("##"):
            pdf.check_space(20)
            pdf.ln(4)
            pdf.set_x(25)
            pdf.set_font(PDF_FONT, "B", 12)
            pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace("#", "").strip()))
            pdf.set_font(PDF_FONT, "", 10)
            pdf.set_text_color(0, 0, 0)
        else:
            pdf.set_x(25)
            pdf.set_font(PDF_FONT, "", 10)
            pdf.multi_cell(150, 5, ironclad_text_formatter(line))

    if visual_attachments:
        pdf.add_page()
        pdf.set_x(25)
        pdf.set_font(PDF_FONT, "B", 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 12, clean_pdf_text("VEDLEGG: FLYFOTO, 3D-SCENE OG REFERANSER"), 0, 1)
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
                pdf.set_font(PDF_FONT, "I", 9)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 8, clean_pdf_text(f"Figur V-{idx}: visuelt grunnlag brukt i analysen."), 0, 1)

    output = pdf.output(dest="S")
    if isinstance(output, bytes):
        return output
    elif isinstance(output, str):
        return output.encode("latin-1")
    else:
        return bytes(output)


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
        --accent2: #34d399;
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
        padding: 14px 28px !important;
        font-size: 1.08rem !important;
        letter-spacing: 0.02em !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 12px 28px rgba(56,194,201,0.3) !important;
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
    /* KPI Cards — enhanced with accent bar */
    .kpi-card {
        padding: 1.1rem 1.3rem;
        background: linear-gradient(145deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01));
        border: 1px solid rgba(120,145,170,0.18);
        border-radius: 16px;
        margin-bottom: 1rem;
        position: relative;
        overflow: hidden;
        backdrop-filter: blur(8px);
    }
    .kpi-card::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
        background: linear-gradient(90deg, #38bdf8, #34d399);
        border-radius: 16px 16px 0 0;
    }
    .kpi-card-hero {
        padding: 1.3rem 1.5rem;
        background: linear-gradient(145deg, rgba(56,194,201,0.08), rgba(52,211,153,0.04));
        border: 1px solid rgba(56,194,201,0.25);
        border-radius: 16px;
        margin-bottom: 1rem;
        position: relative;
        overflow: hidden;
    }
    .kpi-card-hero::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
        background: linear-gradient(90deg, #38bdf8, #34d399, #38bdf8);
    }
    .metric-title {
        color: #9fb0c3;
        font-size: 0.82rem;
        margin-bottom: 0.25rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        font-weight: 500;
    }
    .metric-value {
        color: #f5f7fb;
        font-size: 1.45rem;
        font-weight: 700;
        line-height: 1.2;
    }
    .metric-value-hero {
        color: #38bdf8;
        font-size: 1.6rem;
        font-weight: 800;
        line-height: 1.2;
    }
    /* Section headers */
    .section-header {
        font-size: 1.35rem;
        font-weight: 700;
        color: #f5f7fb;
        margin-top: 2rem;
        margin-bottom: 1rem;
        padding-bottom: 0.5rem;
        border-bottom: 1px solid rgba(120,145,170,0.15);
    }
    /* Dataframe table styling */
    [data-testid="stDataFrame"] {
        border: 1px solid rgba(120,145,170,0.15) !important;
        border-radius: 12px !important;
        overflow: hidden !important;
    }
    /* Download button */
    [data-testid="stDownloadButton"] button {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important;
        border: none !important;
        font-weight: 700 !important;
        border-radius: 12px !important;
        transition: all 0.2s ease !important;
    }
    [data-testid="stDownloadButton"] button:hover {
        transform: translateY(-1px) !important;
        box-shadow: 0 8px 20px rgba(56,194,201,0.25) !important;
    }
    /* Volume sketch cards */
    .volume-card {
        background: rgba(255,255,255,0.02);
        border: 1px solid rgba(120,145,170,0.15);
        border-radius: 12px;
        padding: 0.8rem;
        transition: all 0.2s ease;
    }
    .volume-card:hover {
        border-color: rgba(56,194,201,0.4);
        background: rgba(56,194,201,0.03);
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
    st.warning("Du må sette opp prosjektdata før du kan bruke denne modulen.")
    if find_page("Project"):
        if st.button("Gå til Project Setup", type="primary"):
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
    "<h1 style='font-size: 2.5rem; margin-bottom: 0; font-weight: 800; letter-spacing: -0.02em;'>Mulighetsstudie</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='color: var(--muted); font-size: 1.1rem; margin-bottom: 1.5rem;'>"
    "Volumstudie og tomteanalyse med faktisk tomtepolygon, nabohøyder, terreng og AI-plassering."
    " <span style='color:rgba(56,189,248,0.5);font-size:0.75rem;'>v9.5</span>"
    "</p>",
    unsafe_allow_html=True,
)

if llm_available:
    st.success("AI-tekst er tilgjengelig. Tallsiden beregnes alltid deterministisk først.")
else:
    st.info("AI-tekst er ikke tilgjengelig akkurat nå. Modulen kjører fortsatt hele feasibility-motoren deterministisk.")

for geo_note in geo_runtime_notes():
    st.warning(geo_note)

if geodata_token_ok:
    st.success("Eiendomsdata tilkoblet — tomtehenting, nabobygg og ortofoto er tilgjengelig.")
elif HAS_GEODATA_ONLINE and gdo is not None and gdo.is_available():
    st.warning("Eiendomsdata: tilkobling feilet. Sjekk brukernavn/passord.")


# --- 9. INPUT UI ---
with st.expander("1. Prosjekt og lokasjon (SSOT)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state.get("p_name"), disabled=True)
    b_type = c2.text_input("Formål / bygningstype", value=pd_state.get("b_type", "Bolig"), disabled=True)
    adresse_vis = f"{pd_state.get('adresse', '')}, {pd_state.get('kommune', '')}".strip(", ")
    adresse = st.text_input("Adresse", value=adresse_vis, disabled=True)
    c3, c4, c5 = st.columns(3)
    c3.text_input("Kunde", value=pd_state.get("c_name", ""), disabled=True)
    c4.number_input("Ønsket BTA fra prosjektdata", value=int(pd_state.get("bta", 0)), disabled=True)
    c5.text_input("Land", value=pd_state.get("land", "Norge"), disabled=True)

with st.expander("2. Tomtegeometri og regulering", expanded=True):
    st.info(
        "Dere kan bruke rektangulære fallback-tall, men modulen støtter også faktisk tomtepolygon, naboer og terreng."
    )
    regulation_text = st.text_area(
        "Fritekst fra reguleringsplan (valgfritt, motoren henter ut BYA/BRA/høyde hvis den finner noe)",
        placeholder="Lim inn planbestemmelser, f.eks. %-BYA 35, maks gesimshøyde 12 m, 4 etasjer ...",
        height=110,
    )
    parsed = parse_regulation_hints(regulation_text)
    if parsed:
        st.caption(f"Tolket fra tekst: {parsed}")

    d1, d2, d3 = st.columns(3)
    default_site_area = max(1500.0, float(pd_state.get("bta", 0)) * 1.25) if pd_state.get("bta", 0) else 2500.0
    site_area_m2 = d1.number_input("Tomteareal fallback (m²)", min_value=100.0, value=float(default_site_area), step=50.0)
    site_width_m = d2.number_input("Tomtebredde fallback (m)", min_value=10.0, value=45.0, step=1.0)
    site_depth_m = d3.number_input("Tomtedybde fallback (m)", min_value=10.0, value=55.0, step=1.0)

    s1, s2, s3, s4 = st.columns(4)
    front_setback_m = s1.number_input("Byggegrense mot gate / front (m)", min_value=0.0, value=4.0, step=0.5)
    rear_setback_m = s2.number_input("Bakre byggegrense (m)", min_value=0.0, value=4.0, step=0.5)
    side_setback_m = s3.number_input("Sideavstand (m)", min_value=0.0, value=4.0, step=0.5)
    polygon_setback_m = s4.number_input("Polygonbuffer / inntrekk (m)", min_value=0.0, value=4.0, step=0.5)

    r1, r2, r3, r4 = st.columns(4)
    max_bya_pct = r1.number_input("Maks BYA (%)", min_value=1.0, max_value=100.0, value=float(parsed.get("max_bya_pct", 35.0)), step=1.0)
    max_bra_m2 = r2.number_input("Maks BRA (m², 0 = ikke satt)", min_value=0.0, value=float(parsed.get("max_bra_m2", 0.0)), step=50.0)
    max_floors = r3.number_input("Maks etasjer", min_value=1, max_value=30, value=int(parsed.get("max_floors", max(3, int(pd_state.get("etasjer", 4))))), step=1)
    max_height_m = r4.number_input("Maks høyde (m)", min_value=3.0, value=float(parsed.get("max_height_m", max(10.0, float(pd_state.get("etasjer", 4)) * 3.2))), step=0.5)

    st.markdown("---")
    u1, u2, u3 = st.columns([1.2, 1, 1.5])
    bra_pct_override = u1.checkbox("Styr volum fra %-BRA", value=False, key="bra_override",
                                    help="Når aktivert overstyrer %-BRA alle andre begrensende faktorer (BYA, maks BRA). Brukes typisk for leilighetsprosjekter.")
    utnyttelsesgrad_bra_pct = u2.number_input(
        "Forutsatt %-BRA",
        min_value=50.0, max_value=500.0, value=150.0, step=10.0,
        disabled=not bra_pct_override,
    )
    if not bra_pct_override:
        utnyttelsesgrad_bra_pct = 0.0
    if bra_pct_override:
        target_bra_from_pct = site_area_m2 * utnyttelsesgrad_bra_pct / 100.0
        u3.markdown(
            f"<div class='kpi-card-hero' style='padding:10px 14px;'>"
            f"<div class='metric-title'>Mål-BRA fra {utnyttelsesgrad_bra_pct:.0f}%</div>"
            f"<div class='metric-value-hero'>{target_bra_from_pct:,.0f} m²</div></div>",
            unsafe_allow_html=True,
        )
    else:
        u3.caption("Aktiver «Styr volum fra %-BRA» for å bruke utnyttelsesgrad som primær volumdriver.")

with st.expander("2B. Ekte tomtepolygon, nabohøyder og terreng", expanded=True):
    st.markdown("##### 1. Hent eiendom fra matrikkel")
    if not geodata_token_ok:
        st.warning("Eiendomsdata er ikke tilkoblet. Kontakt administrator for oppsett.")
    st.info("Skriv inn kommune (f.eks. Trondheim eller 5001) og Gnr/Bnr. For flere tomter, separer med komma (f.eks. 15/2, 15/4).")

    c_k, c_g = st.columns(2)
    kommune_nr_input = c_k.text_input("Kommune (Navn eller 4-sifret nummer)", value=pd_state.get('kommune', ''))

    default_gnr_bnr = ""
    if pd_state.get('gnr') and pd_state.get('bnr'):
        default_gnr_bnr = f"{pd_state.get('gnr')}/{pd_state.get('bnr')}"

    gnr_bnr_input = c_g.text_input("Gnr/Bnr (Bruk komma for flere)", value=default_gnr_bnr)

    if st.button("Søk opp og lagre tomt", type="secondary"):
        if not kommune_nr_input or not gnr_bnr_input:
            st.warning("Fyll inn både kommune og Gnr/Bnr.")
        elif not geodata_token_ok:
            st.error("Eiendomsdata er ikke tilkoblet. Kan ikke hente tomt.")
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
                with st.spinner("Henter tomtegrense fra matrikkelen..."):
                    knr = get_kommunenummer(kommune_nr_input) or kommune_nr_input.strip().zfill(4)
                    poly, msg = gdo.fetch_tomt_polygon(knr, pairs)
                    if poly:
                        st.session_state.auto_site_polygon = poly
                        st.session_state.auto_site_msg = msg
                        st.rerun()
                    else:
                        st.error(f"Feilet: {msg}")
                    
    if st.session_state.get("auto_site_polygon") is not None:
        st.success(f"✅ **Klar til bruk!** Tomtegrense hentet. Nøyaktig areal: ca. {int(st.session_state.auto_site_polygon.area)} m²")
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
    default_neighbor_height_m = n2.number_input("Fallback nabohøyde (m)", min_value=3.0, max_value=80.0, value=9.0, step=0.5)
    neighbor_radius_m = n3.number_input("Radius for nabosøk (m)", min_value=30.0, max_value=400.0, value=160.0, step=10.0)
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
        "Ønsket BTA i studien (m²)",
        min_value=100.0,
        value=float(pd_state.get("bta", 0) or 2500.0),
        step=50.0,
    )
    efficiency_ratio = a2.number_input("Salgbarhetsfaktor", min_value=0.55, max_value=0.9, value=0.78, step=0.01)
    floor_to_floor_m = a3.number_input("Etasjehøyde brutto (m)", min_value=2.8, max_value=5.5, value=3.2, step=0.1)
    latitude_manual = a4.number_input("Breddegrad for solanalyse", min_value=45.0, max_value=72.0, value=59.91, step=0.01)

    p1, p2, p3 = st.columns(3)
    parking_ratio_per_unit = p1.number_input("Parkering pr. bolig", min_value=0.0, max_value=3.0, value=0.8, step=0.05)
    parking_area_per_space_m2 = p2.number_input("Areal pr. p-plass (m²)", min_value=15.0, max_value=50.0, value=28.0, step=1.0)
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
            avg_size = st.number_input(f"Gj.sn. størrelse {label} (m²)", min_value=20.0, max_value=180.0, value=size_default, step=1.0, key=f"size_{idx}")
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

        # Støykart — hentes automatisk og legges over ortofoto
        if "ark_stoykart" not in st.session_state:
            st.session_state.ark_stoykart = None
        if st.session_state.ark_kart is not None and st.session_state.ark_stoykart is None:
            auto_poly = st.session_state.get("auto_site_polygon")
            if auto_poly is not None and geodata_token_ok:
                with st.spinner("Henter støykart fra DOK Forurensning..."):
                    # Bruk SAMME bounds som ortofoto (80m buffer)
                    noise_img, noise_src = fetch_noise_map_image(
                        auto_poly.bounds,
                        buffer_m=80.0,  # Match ortofoto-buffer
                        gdo_client=gdo,
                        width=1200,
                        height=1200,
                    )
                    if noise_img is not None:
                        # Komponer støykart over ortofoto
                        combined = None
                        try:
                            ortofoto_raw = st.session_state.ark_kart
                            if isinstance(ortofoto_raw, Image.Image):
                                ortofoto = ortofoto_raw.convert("RGBA")
                                noise_rgba = noise_img.convert("RGBA")
                                # Resize til samme størrelse
                                target_size = ortofoto.size
                                if noise_rgba.size != target_size:
                                    try:
                                        noise_rgba = noise_rgba.resize(target_size, Image.Resampling.LANCZOS)
                                    except AttributeError:
                                        noise_rgba = noise_rgba.resize(target_size, Image.LANCZOS)
                                # Forsterk alpha med point() (rask)
                                r, g, b, a = noise_rgba.split()
                                a = a.point(lambda x: min(int(x * 1.3), 180) if x > 5 else 0)
                                noise_rgba = Image.merge("RGBA", (r, g, b, a))
                                combined = Image.alpha_composite(ortofoto, noise_rgba).convert("RGB")
                        except Exception as comp_err:
                            st.caption(f"Kompositt-feil: {comp_err}")

                        if combined is not None:
                            st.session_state.ark_stoykart = combined
                            st.success(f"Støykart lagt over ortofoto ({noise_src})")
                        else:
                            st.session_state.ark_stoykart = noise_img.convert("RGB")
                            st.success(f"Støykart hentet ({noise_src})")
                    else:
                        st.session_state.ark_stoykart = "empty"
                        st.caption(f"Støykart: {noise_src}")
        if st.session_state.get("ark_stoykart") is not None and st.session_state.ark_stoykart != "empty":
            st.image(st.session_state.ark_stoykart, caption="Ortofoto med støysoner (T-1442 — Gul: 55-65 dB, Rød: >65 dB)", use_container_width=True)

    with c_upload:
        uploaded_files = st.file_uploader(
            "Last opp kart, situasjonsplan, PDF eller skisser",
            accept_multiple_files=True,
            type=["png", "jpg", "jpeg", "pdf"],
        )

with st.expander("5. Hva modulen faktisk gjør nå", expanded=False):
    st.markdown(
        """
- Leser **ekte tomtepolygon** fra matrikkel, GeoJSON eller koordinatliste.
- Regner **7 volumalternativer** (lamell, karre, punkthus, tarn, podium+tarn, tun/U-form og rekke) innenfor faktisk byggefelt.
- Lager **sammensatte volumdeler** som kan vises videre i 3D-scene.
- Bruker **stedsintelligens** for plan, utbygging og mobilitet i rangering av typologier.
- Kan vise volumene i **3D terrengscene** med terrengmodell som grunnlag.
- Leser **nabobebyggelse** automatisk fra kartdata, GeoJSON eller OSM og bruker høyder i sol/skygge.
- Henter **HD-ortofoto** for bedre kartgrunnlag i rapporten.
- Leser **terreng** via punktfil eller raster og estimerer fall/relieff.
- Degraderer kontrollert til fallback hvis geostacken i deployen mangler pyproj eller rasterio.
- Regner **fotavtrykk, BTA, salgbart areal, boligantall, leilighetsmiks og parkeringstrykk**.
- Bruker eventuelt AI bare til å forklare funnene. Tallene kommer fra motoren.
"""
    )


# --- 10. KJOR ANALYSE ---
run_analysis = st.button("Kjør tomtestudie / volumstudie", type="primary", use_container_width=True)

if run_analysis:
    images_for_context = list(saved_images)
    if st.session_state.ark_kart is not None:
        images_for_context.append(st.session_state.ark_kart)
    stoykart = st.session_state.get("ark_stoykart")
    if stoykart is not None and stoykart != "empty":
        images_for_context.append(stoykart)
    images_for_context.extend(load_uploaded_visuals(uploaded_files))

    auto_poly = st.session_state.get("auto_site_polygon")
    site_polygon_input, site_crs, polygon_meta = load_site_polygon_input(auto_poly, site_polygon_upload, site_polygon_text)
    
    # Skuddsikker lat/lon henting
    if auto_poly is not None and HAS_PYPROJ:
        try:
            centroid = auto_poly.centroid
            transformer = Transformer.from_crs(CRS.from_epsg(25833), CRS.from_epsg(4326), always_xy=True)
            lon_geocoded, lat_geocoded = transformer.transform(centroid.x, centroid.y)
            geo_source = "Matrikkel" if geodata_token_ok else "Kartverket"
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
        with st.spinner("Henter nabobebyggelse..."):
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
                    st.success(f"Hentet {len(neighbor_inputs)} nabobygg i nærheten")
                else:
                    st.info("Ingen nabobygg funnet innenfor søkeradius.")
            except Exception as exc:
                st.warning(f"Nabohenting feilet: {exc}")

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
        with st.spinner("Henter HD-ortofoto..."):
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
                    st.success("HD-ortofoto hentet")
            except Exception as exc:
                st.caption(f"Ortofoto-henting feilet: {exc}")

    terrain_ctx, terrain_meta = load_terrain_input(terrain_upload, site_polygon_input, site_crs)
    if terrain_ctx is None and geodata_token_ok and site_polygon_input is not None and gdo is not None:
        try:
            terrain_ctx = gdo.fetch_terrain_model(site_polygon_input, sample_spacing_m=10.0, max_points=180)
            if terrain_ctx is not None:
                terrain_meta = {'source': terrain_ctx.get('source', 'Terrengmodell'), 'point_count': terrain_ctx.get('point_count', 0)}
        except Exception as exc:
            terrain_meta = {'source': 'Terrengmodell', 'error': str(exc)[:120]}

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
        utnyttelsesgrad_bra_pct=utnyttelsesgrad_bra_pct,
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
    bra_label = f" | %-BRA {site.utnyttelsesgrad_bra_pct:.0f}%" if site.utnyttelsesgrad_bra_pct > 0 else ""
    with st.spinner(f"Regner volumalternativer{ai_label}{bra_label} ..."):
        options = generate_options(site, mix_inputs, geodata_context=geodata_context)

    # Diagnostikk — lagre i session_state for visning i resultatene
    if site.utnyttelsesgrad_bra_pct > 0:
        limits_diag = derive_limits(site, geodata_context)
        sp_area = float(geodata_context["site_polygon"].area) if geodata_context.get("site_polygon") else 0
        bp_area = float(geodata_context["buildable_polygon"].area) if geodata_context.get("buildable_polygon") else 0
        pp_area = float(placement_polygon.area) if 'placement_polygon' in dir() else 0
        target_bra_diag = site.site_area_m2 * site.utnyttelsesgrad_bra_pct / 100.0
        target_bta_diag = target_bra_diag / max(site.efficiency_ratio, 0.6)
        best_bta = max((o.gross_bta_m2 for o in options), default=0) if options else 0
        st.session_state["_motor_diag"] = (
            f"v9.5 | tomteareal={site.site_area_m2:.0f} m² | site_poly={sp_area:.0f} m² | "
            f"buildable_poly={bp_area:.0f} m² | maks_fotavtrykk={limits_diag['max_footprint']:.0f} m² | "
            f"mål_BRA={target_bra_diag:.0f} m² | mål_BTA={target_bta_diag:.0f} m² | oppnådd_BTA={best_bta:.0f} m²"
        )

    if HAS_SITE_INTELLIGENCE and site_intelligence_bundle.get('available'):
        options = apply_site_intelligence_to_options(options, site_intelligence_bundle)

    if not options:
        st.error("Klarte ikke å generere alternativer. Kontroller tomtepolygon, byggegrenser og BYA.")
        st.stop()

    # --- MILJØANALYSE ---
    environment_data: Dict[str, Any] = {"available": False}
    if site_polygon_input is not None:
        with st.spinner("Analyserer miljøforhold: støy, dagslys, utsikt, vind..."):
            try:
                best_option = max(options, key=lambda o: o.score)
                fp_polys = split_geometry_to_polygons(
                    Polygon(flatten_coord_groups(best_option.geometry.get("footprint_polygon_coords", [])))
                ) if best_option.geometry.get("footprint_polygon_coords") else []
                fp_heights = [best_option.building_height_m] * max(len(fp_polys), 1)

                environment_data = build_environment_analysis(
                    site_polygon=site_polygon_input,
                    building_polygons=fp_polys,
                    building_heights=fp_heights,
                    neighbors=[n for n in geodata_context.get("neighbors", []) if n.get("polygon") is not None],
                    latitude_deg=latitude_deg,
                    longitude_deg=longitude_deg,
                    terrain=terrain_ctx,
                    gdo_client=gdo if geodata_token_ok else None,
                )
            except Exception as exc:
                environment_data = {"available": False, "error": str(exc)[:120]}

    option_images = [render_plan_diagram(site, option) for option in options]
    deterministic_report = build_deterministic_report(site, options, parsed, has_visual_input=bool(images_for_context), environment_data=environment_data)
    if HAS_SITE_INTELLIGENCE and site_intelligence_bundle.get('available'):
        si_markdown = build_site_intelligence_markdown(site_intelligence_bundle)
        # Rekke er slått sammen med Lamell — erstatt i output
        si_markdown = si_markdown.replace("Rekke:", "Lamell (tidl. Rekke):").replace("favoriserer mest Rekke", "favoriserer mest Lamell")
        deterministic_report = deterministic_report + "\n\n" + si_markdown
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

    try:
        pdf_bytes = create_full_report_pdf(
            name=p_name,
            client=pd_state.get("c_name", "Ukjent"),
            land=pd_state.get("land", "Norge"),
            report_text=final_report_text,
            options=options,
            option_images=option_images,
            visual_attachments=images_for_context,
            site=site,
            environment_data=environment_data,
        )
    except Exception as pdf_exc:
        st.warning(f"PDF-generering feilet: {pdf_exc}")
        pdf_bytes = b""

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
        "environment": environment_data,
    }
    st.session_state.generated_ark_pdf = pdf_bytes
    st.session_state.generated_ark_filename = f"Builtly_ARK_{p_name}_v3.pdf"

    # Save report to user dashboard
    try:
        from builtly_auth import save_report
        save_report(
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

    best = options[0]
    site_result = result.get("site", {})

    # Hero section — recommended option
    st.markdown(
        "<div style='margin-top:1rem; margin-bottom:0.5rem;'>"
        "<span style='color:#38bdf8; font-size:0.9rem; text-transform:uppercase; letter-spacing:0.08em; font-weight:600;'>Anbefalt alternativ</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown("<div class='kpi-card-hero'><div class='metric-title'>Typologi</div><div class='metric-value-hero'>{}</div></div>".format(best.typology), unsafe_allow_html=True)
    with k2:
        st.markdown("<div class='kpi-card-hero'><div class='metric-title'>BTA</div><div class='metric-value-hero'>{:,.0f} m²</div></div>".format(best.gross_bta_m2), unsafe_allow_html=True)
    with k3:
        st.markdown("<div class='kpi-card-hero'><div class='metric-title'>Boliger</div><div class='metric-value-hero'>{}</div></div>".format(best.unit_count), unsafe_allow_html=True)
    with k4:
        st.markdown("<div class='kpi-card-hero'><div class='metric-title'>Score</div><div class='metric-value-hero'>{:.0f}/100</div></div>".format(best.score), unsafe_allow_html=True)

    g1, g2, g3, g4 = st.columns(4)
    geo_src_raw = site_result.get("site_geometry_source", "-")
    geo_src_display = "Eksakt polygon" if any(k in geo_src_raw for k in ["Eksakt", "Hentet", "Geodata"]) else ("GeoJSON" if "GeoJSON" in geo_src_raw else geo_src_raw)

    # Beregn %-BRA og BYA for best alternativ
    sa = max(site_result.get("site_area_m2", 1.0), 1.0)
    actual_bra_pct = (best.saleable_area_m2 / sa) * 100.0
    actual_bya_pct = (best.footprint_area_m2 / sa) * 100.0

    with g1:
        st.markdown(f"<div class='kpi-card'><div class='metric-title'>%-BRA (utnyttelse)</div><div class='metric-value'>{actual_bra_pct:.0f}%</div></div>", unsafe_allow_html=True)
    with g2:
        st.markdown(f"<div class='kpi-card'><div class='metric-title'>BYA (bebygd)</div><div class='metric-value'>{actual_bya_pct:.0f}%</div></div>", unsafe_allow_html=True)
    with g3:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Solscore</div><div class='metric-value'>{:.0f}/100</div></div>".format(best.solar_score), unsafe_allow_html=True)
    with g4:
        st.markdown("<div class='kpi-card'><div class='metric-title'>Nabobygg i modell</div><div class='metric-value'>{}</div></div>".format(best.neighbor_count), unsafe_allow_html=True)

    polygon_meta = result.get("polygon_meta", {})
    neighbor_meta = result.get("neighbor_meta", {})
    terrain_meta = result.get("terrain_meta", {})
    site_intelligence_bundle = result.get('site_intelligence', {}) or {}

    if site_intelligence_bundle.get('available'):
        st.markdown(
            "<div class='section-header'>Stedsintelligens</div>",
            unsafe_allow_html=True,
        )
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            st.markdown("<div class='kpi-card'><div class='metric-title'>Stedscore</div><div class='metric-value'>{:.0f}/100</div></div>".format(float(site_intelligence_bundle.get('site_score', 0.0))), unsafe_allow_html=True)
        with s2:
            st.markdown("<div class='kpi-card'><div class='metric-title'>Mulighet</div><div class='metric-value'>{:.0f}/100</div></div>".format(float(site_intelligence_bundle.get('opportunity_score', 0.0))), unsafe_allow_html=True)
        with s3:
            st.markdown("<div class='kpi-card'><div class='metric-title'>Plan-/stedsrisiko</div><div class='metric-value'>{:.0f}/100</div></div>".format(float(site_intelligence_bundle.get('risk_score', 0.0))), unsafe_allow_html=True)
        with s4:
            favored = sorted((site_intelligence_bundle.get('typology_score_adjustments') or {}).items(), key=lambda item: item[1], reverse=True)
            favored_text = favored[0][0] if favored else '-'
            st.markdown("<div class='kpi-card'><div class='metric-title'>Favorisert grep</div><div class='metric-value'>{}</div></div>".format(favored_text), unsafe_allow_html=True)

    # Data source caption
    meta_lines = []
    if polygon_meta:
        src = polygon_meta.get('source', '-')
        clean_src = "Eksakt polygon" if any(k in src for k in ["Eksakt", "Hentet", "Geodata"]) else src
        meta_lines.append(f"Tomt: {clean_src}")
    if neighbor_meta:
        n_count = neighbor_meta.get('count', best.neighbor_count)
        if n_count:
            meta_lines.append(f"Naboer: {n_count} stk")
    if terrain_meta and not terrain_meta.get("error"):
        meta_lines.append("Terreng: aktiv")
    if best.terrain_slope_pct > 0:
        meta_lines.append(f"Fall: {best.terrain_slope_pct:.1f}%")
    if meta_lines:
        st.caption(" · ".join(meta_lines))

    # Motor-diagnostikk (persistent)
    diag = st.session_state.get("_motor_diag")
    if diag:
        st.caption(f"🔧 {diag}")

    st.markdown("<div class='section-header'>Alternativsammenligning</div>", unsafe_allow_html=True)
    comparison_df = pd.DataFrame(
        [
            {
                "Alternativ": option.name,
                "Typologi": option.typology,
                "Etasjer": option.floors,
                "Fotavtrykk m²": round(option.footprint_area_m2, 0),
                "BTA m²": round(option.gross_bta_m2, 0),
                "BRA m²": round(option.saleable_area_m2, 0),
                "%-BRA": round(option.saleable_area_m2 / max(sa, 1) * 100, 0),
                "Boliger": option.unit_count,
                "Solscore": round(option.solar_score, 0),
                "Score": round(option.score, 1),
            }
            for option in options
        ]
    )
    st.dataframe(comparison_df, use_container_width=True, hide_index=True)

    # --- INTERAKTIV RADARDIAGRAM ---
    st.markdown("<div class='section-header'>Visuell sammenligning</div>", unsafe_allow_html=True)
    radar_data = json.dumps([
        {
            "name": opt.name.replace("Alt ", ""),
            "typology": opt.typology,
            "score": round(opt.score, 1),
            "solar": round(opt.solar_score, 1),
            "bta_pct": round(100.0 * opt.gross_bta_m2 / max(best.gross_bta_m2, 1.0), 1),
            "open_space": round(opt.open_space_ratio * 100, 1),
            "efficiency": round(opt.efficiency_ratio * 100, 1),
            "target_fit": round(min(100.0, opt.target_fit_pct), 1),
        }
        for opt in options
    ], ensure_ascii=False)
    radar_html = f"""
<div id="radar-chart" style="width:100%;height:420px;position:relative;overflow:hidden;">
<canvas id="radarCanvas" style="width:100%;height:100%;"></canvas>
</div>
<script>
(function() {{
  const D = {radar_data};
  const canvas = document.getElementById('radarCanvas');
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.parentElement.clientWidth;
  const H = 420;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  ctx.scale(dpr, dpr);

  const axes = ['Score', 'Sol', 'BTA-treff', 'Frirom', 'Effektivitet', 'Måloppnåelse'];
  const keys = ['score', 'solar', 'bta_pct', 'open_space', 'efficiency', 'target_fit'];
  const colors = ['#38bdf8','#34d399','#f59e0b','#ef4444','#a78bfa','#ec4899','#06b6d4'];
  const cx = W * 0.42, cy = H * 0.50, R = Math.min(W * 0.30, H * 0.40);
  const n = axes.length;

  function angle(i) {{ return (Math.PI * 2 * i / n) - Math.PI / 2; }}

  // Grid
  for (let r = 0.2; r <= 1.0; r += 0.2) {{
    ctx.beginPath();
    for (let i = 0; i <= n; i++) {{
      const a = angle(i % n);
      const x = cx + Math.cos(a) * R * r, y = cy + Math.sin(a) * R * r;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }}
    ctx.strokeStyle = 'rgba(120,145,170,0.15)'; ctx.stroke();
  }}
  // Axes + labels
  ctx.font = '11px Inter, sans-serif'; ctx.fillStyle = '#9fb0c3';
  for (let i = 0; i < n; i++) {{
    const a = angle(i);
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
    ctx.strokeStyle = 'rgba(120,145,170,0.2)'; ctx.stroke();
    const lx = cx + Math.cos(a) * (R + 18), ly = cy + Math.sin(a) * (R + 18);
    ctx.textAlign = Math.cos(a) > 0.1 ? 'left' : Math.cos(a) < -0.1 ? 'right' : 'center';
    ctx.textBaseline = Math.sin(a) > 0.1 ? 'top' : Math.sin(a) < -0.1 ? 'bottom' : 'middle';
    ctx.fillText(axes[i], lx, ly);
  }}
  // Data polygons
  D.forEach((d, di) => {{
    ctx.beginPath();
    keys.forEach((k, i) => {{
      const v = (d[k] || 0) / 100.0;
      const a = angle(i);
      const x = cx + Math.cos(a) * R * v, y = cy + Math.sin(a) * R * v;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }});
    ctx.closePath();
    const c = colors[di % colors.length];
    ctx.fillStyle = c + '18'; ctx.fill();
    ctx.strokeStyle = c; ctx.lineWidth = 2; ctx.stroke();
  }});
  // Legend
  const lgX = W * 0.78, lgY = 20;
  ctx.font = 'bold 10px Inter, sans-serif';
  D.forEach((d, i) => {{
    ctx.fillStyle = colors[i % colors.length];
    ctx.fillRect(lgX, lgY + i * 20, 12, 12);
    ctx.fillStyle = '#c8d3df';
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.fillText(d.name + ' (' + d.typology + ')', lgX + 18, lgY + i * 20 + 6);
  }});
}})();
</script>
"""
    components.html(radar_html, height=440, scrolling=False)

    # --- SIDE-BY-SIDE SAMMENLIGNING ---
    st.markdown("<div class='section-header'>Sammenlign to alternativer</div>", unsafe_allow_html=True)
    cmp_col1, cmp_col2 = st.columns(2)
    opt_names = [opt.name for opt in options]
    with cmp_col1:
        cmp_a_name = st.selectbox("Alternativ A", opt_names, index=0, key="cmp_a")
    with cmp_col2:
        cmp_b_name = st.selectbox("Alternativ B", opt_names, index=min(1, len(opt_names)-1), key="cmp_b")
    cmp_a = next((o for o in options if o.name == cmp_a_name), options[0])
    cmp_b = next((o for o in options if o.name == cmp_b_name), options[-1])

    def _delta_html(label: str, val_a: float, val_b: float, fmt: str = ".0f", unit: str = "", higher_better: bool = True) -> str:
        diff = val_b - val_a
        arrow = ""
        if abs(diff) > 0.1:
            is_better = (diff > 0) == higher_better
            color = "#34d399" if is_better else "#f87171"
            arrow = f"<span style='color:{color};font-size:0.8rem;margin-left:6px;'>{'▲' if diff > 0 else '▼'} {abs(diff):{fmt}}{unit}</span>"
        return (
            f"<div style='display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(120,145,170,0.1);'>"
            f"<span style='color:#9fb0c3;font-size:0.9rem;'>{label}</span>"
            f"<span style='color:#f5f7fb;font-weight:600;'>{val_a:{fmt}}{unit} vs {val_b:{fmt}}{unit}{arrow}</span>"
            f"</div>"
        )

    cmp_html = (
        f"<div style='background:rgba(255,255,255,0.02);border:1px solid rgba(120,145,170,0.15);border-radius:12px;padding:16px 20px;'>"
        f"<div style='display:flex;justify-content:space-between;margin-bottom:12px;'>"
        f"<span style='color:#38bdf8;font-weight:700;font-size:1.1rem;'>{cmp_a.name} ({cmp_a.typology})</span>"
        f"<span style='color:#34d399;font-weight:700;font-size:1.1rem;'>{cmp_b.name} ({cmp_b.typology})</span>"
        f"</div>"
        + _delta_html("Score", cmp_a.score, cmp_b.score, ".1f", "/100")
        + _delta_html("BTA", cmp_a.gross_bta_m2, cmp_b.gross_bta_m2, ",.0f", " m²")
        + _delta_html("Salgbart areal", cmp_a.saleable_area_m2, cmp_b.saleable_area_m2, ",.0f", " m²")
        + _delta_html("Boliger", cmp_a.unit_count, cmp_b.unit_count, ".0f")
        + _delta_html("Solscore", cmp_a.solar_score, cmp_b.solar_score, ".0f", "/100")
        + _delta_html("Sol uteareal", cmp_a.sunlit_open_space_pct, cmp_b.sunlit_open_space_pct, ".0f", "%")
        + _delta_html("Etasjer", cmp_a.floors, cmp_b.floors, ".0f", "", False)
        + _delta_html("Fotavtrykk", cmp_a.footprint_area_m2, cmp_b.footprint_area_m2, ",.0f", " m²", False)
        + _delta_html("Parkering", cmp_a.parking_spaces, cmp_b.parking_spaces, ".0f")
        + _delta_html("Vinterskygge kl.12", cmp_a.winter_noon_shadow_m, cmp_b.winter_noon_shadow_m, ".1f", " m", False)
        + "</div>"
    )
    st.markdown(cmp_html, unsafe_allow_html=True)

    # --- INTERAKTIV SOL/SKYGGE-KLOKKE ---
    st.markdown("<div class='section-header'>Sol og skygge gjennom dagen</div>", unsafe_allow_html=True)
    shadow_opt_name = st.selectbox("Velg alternativ for solanalyse", opt_names, index=0, key="shadow_opt")
    shadow_opt = next((o for o in options if o.name == shadow_opt_name), options[0])
    shadow_season = st.radio("Sesong", ["Vår/høst (mars)", "Vinter (desember)", "Sommer (juni)"], horizontal=True, key="shadow_season")
    season_doy = {"Vår/høst (mars)": 80, "Vinter (desember)": 355, "Sommer (juni)": 172}.get(shadow_season, 80)

    shadow_hours = list(range(7, 21))
    shadow_fracs = []
    for h in shadow_hours:
        alt_deg = solar_altitude_deg(site_result.get("latitude_deg", 59.91), season_doy, float(h))
        if alt_deg <= 0.5:
            shadow_fracs.append(0.0)
        else:
            shadow_fracs.append(round(alt_deg, 1))

    shadow_chart_data = json.dumps({"hours": shadow_hours, "altitudes": shadow_fracs, "typology": shadow_opt.typology, "height_m": shadow_opt.building_height_m, "lat": site_result.get("latitude_deg", 59.91), "season": shadow_season})
    shadow_html = f"""
<div style="width:100%;height:280px;position:relative;">
<canvas id="shadowCanvas" style="width:100%;height:100%;"></canvas>
</div>
<script>
(function() {{
  const D = {shadow_chart_data};
  const canvas = document.getElementById('shadowCanvas');
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.parentElement.clientWidth, H = 280;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  ctx.scale(dpr, dpr);

  const pad = {{l:55, r:20, t:30, b:45}};
  const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
  const hours = D.hours, alts = D.altitudes;
  const maxAlt = Math.max(...alts, 1);

  // Background gradient (day/night)
  const grad = ctx.createLinearGradient(pad.l, 0, pad.l + cw, 0);
  hours.forEach((h, i) => {{
    const t = i / (hours.length - 1);
    const a = alts[i];
    grad.addColorStop(t, a > 0 ? 'rgba(56,189,248,0.06)' : 'rgba(15,23,42,0.15)');
  }});
  ctx.fillStyle = grad;
  ctx.fillRect(pad.l, pad.t, cw, ch);

  // Grid
  ctx.strokeStyle = 'rgba(120,145,170,0.1)'; ctx.lineWidth = 1;
  for (let g = 0; g <= 4; g++) {{
    const y = pad.t + ch - (g / 4) * ch;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + cw, y); ctx.stroke();
    ctx.fillStyle = '#9fb0c3'; ctx.font = '10px Inter, sans-serif';
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText(Math.round(maxAlt * g / 4) + '°', pad.l - 8, y);
  }}

  // Bars
  const bw = cw / hours.length * 0.7;
  hours.forEach((h, i) => {{
    const x = pad.l + (i + 0.5) * (cw / hours.length);
    const val = alts[i] / maxAlt;
    const bh = val * ch;
    const hue = alts[i] > 20 ? '48,96%' : alts[i] > 10 ? '35,90%' : alts[i] > 0 ? '200,70%' : '220,20%';
    ctx.fillStyle = `hsla(${{hue}},${{alts[i] > 0 ? '60%' : '20%'}})`;
    ctx.fillRect(x - bw/2, pad.t + ch - bh, bw, bh);
    // Shadow length indicator on top
    if (alts[i] > 0.5) {{
      const shadowLen = D.height_m / Math.tan(alts[i] * Math.PI / 180);
      ctx.fillStyle = '#f5f7fb'; ctx.font = 'bold 9px Inter'; ctx.textAlign = 'center';
      ctx.fillText(Math.round(shadowLen) + 'm', x, pad.t + ch - bh - 6);
    }}
    // Hour label
    ctx.fillStyle = '#9fb0c3'; ctx.font = '10px Inter'; ctx.textAlign = 'center';
    ctx.fillText(h + ':00', x, pad.t + ch + 16);
  }});

  // Title
  ctx.fillStyle = '#c8d3df'; ctx.font = 'bold 12px Inter'; ctx.textAlign = 'left';
  ctx.fillText('Solhøyde og skyggelengde — ' + D.typology + ' (' + D.height_m.toFixed(1) + ' m) — ' + D.season, pad.l, 18);
  ctx.fillStyle = '#9fb0c3'; ctx.font = '10px Inter';
  ctx.fillText('Breddegrad: ' + D.lat.toFixed(2) + '° | Tall over søylene = skyggelengde fra bygghøyde', pad.l, pad.t + ch + 38);
}})();
</script>
"""
    components.html(shadow_html, height=300, scrolling=False)

    if site_intelligence_bundle.get('available'):
        st.markdown("<div class='section-header'>Stedskontekst</div>", unsafe_allow_html=True)
        gi_plan, gi_projects, gi_transport = st.columns(3)

        # Plan og regulering — lesbar oppsummering
        plan_data = site_intelligence_bundle.get('plan', {})
        with gi_plan:
            nearby = plan_data.get('nearby_plan_count', plan_data.get('feature_count', 0))
            risk = plan_data.get('regulatory_risk_score', 0)
            risk_label = "Lav" if risk < 30 else "Moderat" if risk < 60 else "Høy"
            risk_color = "#34d399" if risk < 30 else "#f59e0b" if risk < 60 else "#f87171"
            st.markdown(
                f"<div class='kpi-card'><div class='metric-title'>Plan og regulering</div>"
                f"<div class='metric-value' style='font-size:1.1rem;'>{nearby} planer i nærheten</div>"
                f"<div style='color:{risk_color};font-size:0.85rem;margin-top:4px;'>Planrisiko: {risk_label}</div>"
                f"</div>", unsafe_allow_html=True)

        # Utbyggingsaktivitet
        proj_data = site_intelligence_bundle.get('projects', {})
        with gi_projects:
            proj_count = proj_data.get('feature_count', proj_data.get('nearby_count', 0))
            st.markdown(
                f"<div class='kpi-card'><div class='metric-title'>Utbyggingsaktivitet</div>"
                f"<div class='metric-value' style='font-size:1.1rem;'>{proj_count} prosjekter</div>"
                f"<div style='color:#9fb0c3;font-size:0.85rem;margin-top:4px;'>I nærområdet</div>"
                f"</div>", unsafe_allow_html=True)

        # Mobilitet og adkomst
        transport = site_intelligence_bundle.get('transport', {})
        with gi_transport:
            mob_score = transport.get('mobility_score', 0)
            transit_m = transport.get('nearest_transit_m')
            parking_n = transport.get('parking_within_300_m', 0)
            transit_txt = f"{transit_m:.0f} m til kollektiv" if transit_m and transit_m > 0 else "Ingen kollektiv funnet"
            mob_color = "#34d399" if mob_score >= 60 else "#f59e0b" if mob_score >= 30 else "#f87171"
            st.markdown(
                f"<div class='kpi-card'><div class='metric-title'>Mobilitet</div>"
                f"<div class='metric-value' style='font-size:1.1rem;color:{mob_color};'>{mob_score}/100</div>"
                f"<div style='color:#9fb0c3;font-size:0.85rem;margin-top:4px;'>{transit_txt} · {parking_n} p-plasser</div>"
                f"</div>", unsafe_allow_html=True)

    # --- MILJØANALYSE RESULTATER ---
    env_data = result.get("environment", {})
    if env_data.get("available"):
        st.markdown("<div class='section-header'>Miljøanalyse</div>", unsafe_allow_html=True)

        env_cols = st.columns(4)

        # Støy
        noise = env_data.get("noise", {})
        with env_cols[0]:
            if noise.get("available") and noise.get("zones"):
                worst_zone = max(noise["zones"], key=lambda z: z.get("db", 0))
                db_val = worst_zone.get("db", 0)
                noise_color = "#f87171" if db_val > 65 else "#f59e0b" if db_val > 55 else "#34d399"
                st.markdown(f"<div class='kpi-card'><div class='metric-title'>Støy (T-1442)</div><div class='metric-value' style='color:{noise_color}'>{worst_zone.get('zone', '–')}</div></div>", unsafe_allow_html=True)
                if db_val > 0:
                    st.caption(f"{db_val:.0f} dB, {worst_zone.get('source_type', 'vei')} | Kilde: {noise.get('source', '?')}")
            else:
                st.markdown("<div class='kpi-card'><div class='metric-title'>Støy</div><div class='metric-value' style='color:#34d399'>Ingen data</div></div>", unsafe_allow_html=True)
                debug = noise.get("debug", [])
                if debug:
                    st.caption(f"Debug: {' | '.join(debug[:3])}")

        # Dagslys
        daylight = env_data.get("daylight", {})
        with env_cols[1]:
            dl_score = daylight.get("overall_score", 0)
            dl_color = "#34d399" if dl_score >= 70 else "#f59e0b" if dl_score >= 50 else "#f87171"
            st.markdown(f"<div class='kpi-card'><div class='metric-title'>Dagslys §13-7</div><div class='metric-value' style='color:{dl_color}'>{dl_score:.0f}/100</div></div>", unsafe_allow_html=True)

        # Utsikt
        views = env_data.get("views", {})
        with env_cols[2]:
            view_score = views.get("overall_score", 0)
            view_color = "#34d399" if view_score >= 70 else "#f59e0b" if view_score >= 50 else "#f87171"
            st.markdown(f"<div class='kpi-card'><div class='metric-title'>Utsikt</div><div class='metric-value' style='color:{view_color}'>{view_score:.0f}/100</div></div>", unsafe_allow_html=True)

        # Vind
        wind_comfort = env_data.get("wind_comfort", {})
        with env_cols[3]:
            wc_class = wind_comfort.get("lawson_class", "–")
            wc_color = "#34d399" if wc_class in ["A", "B"] else "#f59e0b" if wc_class == "C" else "#f87171"
            wc_label = wind_comfort.get("overall", "Ikke vurdert")
            st.markdown(f"<div class='kpi-card'><div class='metric-title'>Vindkomfort</div><div class='metric-value' style='color:{wc_color}'>Klasse {wc_class}</div></div>", unsafe_allow_html=True)

        # Detaljer i expander
        with st.expander("Miljødetaljer", expanded=False):
            # Dagslys per fasade
            if daylight.get("facades"):
                st.markdown("**Dagslys per fasade (TEK17 §13-7 — forenklet)**")
                dl_rows = [{"Bygg": f["building"], "Retning": f["direction"], "Score": f["score"], "Vurdering": f["rating"], "Obstruksjon": f"{f['obstruction_deg']}°"} for f in daylight["facades"]]
                st.dataframe(pd.DataFrame(dl_rows), use_container_width=True, hide_index=True)

            # Utsikt per bygg
            if views.get("buildings"):
                st.markdown("**Utsikt per bygg**")
                for vb in views["buildings"]:
                    st.caption(f"**{vb['building']}**: snitt {vb['average_score']:.0f}/100, best mot {vb['best_direction']} ({vb['best_score']:.0f}/100)")

            # Vind
            wind = env_data.get("wind", {})
            if wind.get("available"):
                st.markdown("**Vindforhold**")
                st.caption(f"Kilde: {wind.get('source', '–')} | Dominerende retning: {wind.get('dominant_direction', '–')} | Snitt: {wind.get('avg_speed_ms', 0):.1f} m/s")
                if wind_comfort.get("available"):
                    st.caption(f"Vindkomfort: {wc_label} | Effektiv vind: {wind_comfort.get('effective_wind_ms', 0):.1f} m/s | Forsterkningsfaktor: {wind_comfort.get('amplification_factor', 1):.2f}")
                    gap = wind_comfort.get("min_building_gap_m")
                    if gap is not None:
                        st.caption(f"Minste avstand mellom bygg: {gap:.0f} m" + (" ⚠️ Venturi-risiko" if gap < 12 else ""))

    st.markdown("<div class='section-header'>Volumskisser</div>", unsafe_allow_html=True)
    # Vis maks 4 per rad for lesbarhet
    per_row = min(4, len(options))
    option_image_pairs = list(zip(options, result["option_images"]))
    for row_start in range(0, len(option_image_pairs), per_row):
        row_items = option_image_pairs[row_start:row_start + per_row]
        cols = st.columns(per_row)
        for col_idx, (option, image) in enumerate(row_items):
            with cols[col_idx]:
                st.image(image, use_container_width=True)
                is_best = (option.name == best.name)
                badge = "⭐ " if is_best else ""
                st.markdown(
                    f"<div style='text-align:center;'>"
                    f"<div style='color:{'#38bdf8' if is_best else '#c8d3df'};font-weight:{'700' if is_best else '500'};font-size:0.95rem;'>{badge}{option.typology}</div>"
                    f"<div style='color:#9fb0c3;font-size:0.82rem;'>"
                    f"BTA {option.gross_bta_m2:,.0f} m² · {option.unit_count} bol. · sol {option.solar_score:.0f}/100"
                    f"</div></div>",
                    unsafe_allow_html=True,
                )

    # --- INTERAKTIV BYGNINGSEDITOR ---
    st.markdown("<div class='section-header'>Planeditor — tegn og juster bygningsvolumer</div>", unsafe_allow_html=True)
    st.caption("Klikk og dra for å plassere bygninger. Dra hjørner for å endre størrelse. Klikk på bygg for å endre etasjer. Dobbeltklikk for å slette.")

    # Prepare site data for the editor
    editor_site_coords = best.geometry.get("site_polygon_coords", [])
    editor_buildable_coords = best.geometry.get("buildable_polygon_coords", [])
    editor_neighbor_polys = best.geometry.get("neighbor_polygons", [])
    editor_site_area = site_result.get("site_area_m2", 1000)
    editor_max_bya = site_result.get("max_bya_pct", 35)
    editor_efficiency = site_result.get("efficiency_ratio", 0.78)
    editor_floor_h = site_result.get("floor_to_floor_m", 3.0)
    editor_avg_unit = 55.0  # gjennomsnittlig leilighet m²

    editor_payload = json.dumps({
        "site": editor_site_coords,
        "buildable": editor_buildable_coords,
        "neighbors": editor_neighbor_polys,
        "site_area": round(editor_site_area, 1),
        "max_bya_pct": editor_max_bya,
        "efficiency": editor_efficiency,
        "floor_height": editor_floor_h,
        "avg_unit_m2": editor_avg_unit,
    }, ensure_ascii=False)

    editor_html = """
<div id="editor-wrap" style="width:100%;background:#0a1520;border:1px solid rgba(120,145,170,0.2);border-radius:12px;overflow:hidden;position:relative;">
<canvas id="editorCanvas" style="width:100%;cursor:crosshair;display:block;"></canvas>
<div id="editorHUD" style="position:absolute;top:12px;right:12px;background:rgba(6,17,26,0.92);border:1px solid rgba(56,189,248,0.3);border-radius:10px;padding:12px 16px;font-family:Inter,sans-serif;min-width:200px;pointer-events:none;">
  <div style="color:#38bdf8;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;font-weight:600;">Live beregning</div>
  <div id="hudBTA" style="color:#f5f7fb;font-size:14px;font-weight:700;">BTA: 0 m²</div>
  <div id="hudBRA" style="color:#c8d3df;font-size:12px;">BRA: 0 m²</div>
  <div id="hudBRApct" style="color:#38bdf8;font-size:13px;font-weight:600;">%-BRA: 0%</div>
  <div id="hudUnits" style="color:#c8d3df;font-size:12px;">Boliger: 0</div>
  <div id="hudBuildings" style="color:#9fb0c3;font-size:11px;margin-top:4px;">Bygg: 0</div>
</div>
<div id="editorToolbar" style="position:absolute;bottom:12px;left:12px;display:flex;gap:8px;">
  <button onclick="addBuilding()" style="background:linear-gradient(135deg,rgba(56,194,201,0.9),rgba(120,220,225,0.9));border:none;color:#041018;font-weight:700;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13px;">+ Legg til bygg</button>
  <button onclick="clearAll()" style="background:rgba(255,255,255,0.08);border:1px solid rgba(120,145,170,0.3);color:#f5f7fb;font-weight:600;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13px;">Tøm alt</button>
  <button onclick="exportSketch()" style="background:linear-gradient(135deg,rgba(250,180,60,0.9),rgba(245,158,11,0.9));border:none;color:#041018;font-weight:700;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13px;">📋 Kopier skisse</button>
  <button onclick="lockAndRunSketch()" style="background:linear-gradient(135deg,rgba(34,197,94,0.9),rgba(22,163,74,0.9));border:none;color:#fff;font-weight:700;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:13px;">🔒 Lås og kjør motor</button>
  <select id="floorSelect" onchange="setFloors()" style="background:#0d1824;border:1px solid rgba(120,145,170,0.4);color:#fff;padding:8px 12px;border-radius:8px;font-size:13px;">
    <option value="2">2 etasjer</option><option value="3">3 etasjer</option><option value="4" selected>4 etasjer</option>
    <option value="5">5 etasjer</option><option value="6">6 etasjer</option><option value="7">7 etasjer</option><option value="8">8 etasjer</option>
  </select>
</div>
<div id="exportOverlay" style="display:none;position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);background:rgba(6,17,26,0.96);border:1px solid rgba(56,189,248,0.4);border-radius:12px;padding:20px;z-index:10;max-width:90%;text-align:center;">
  <div style="color:#38bdf8;font-weight:700;font-size:14px;margin-bottom:8px;">Skisse kopiert til utklippstavle!</div>
  <div style="color:#9fb0c3;font-size:12px;">JSON kopiert. Du kan også bruke «🔒 Lås og kjør motor» for direkte kjøring.</div>
  <button onclick="document.getElementById('exportOverlay').style.display='none'" style="margin-top:12px;background:rgba(56,194,201,0.2);border:1px solid rgba(56,194,201,0.4);color:#38bdf8;padding:6px 16px;border-radius:8px;cursor:pointer;font-size:12px;">OK</button>
</div>
</div>
<script>
(function() {
const P = __PAYLOAD__;
const canvas = document.getElementById('editorCanvas');
const ctx = canvas.getContext('2d');
const dpr = window.devicePixelRatio || 1;
const W = canvas.parentElement.clientWidth;
const H = Math.min(W * 0.65, 620);
canvas.width = W * dpr; canvas.height = H * dpr;
canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
ctx.scale(dpr, dpr);

// Parse site polygon
function flatCoords(groups) {
  if (!groups || !groups.length) return [];
  if (typeof groups[0][0] === 'number') return groups;
  let flat = [];
  groups.forEach(g => { if (g && g.length) flat = flat.concat(g); });
  return flat;
}
const siteCoords = flatCoords(P.site);
const buildableCoords = flatCoords(P.buildable);
if (!siteCoords.length) return;

// Compute bounds
let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
siteCoords.forEach(p => { minX = Math.min(minX, p[0]); minY = Math.min(minY, p[1]); maxX = Math.max(maxX, p[0]); maxY = Math.max(maxY, p[1]); });
const spanX = maxX - minX || 1, spanY = maxY - minY || 1;
const margin = 40;
const scaleX = (W - 2*margin) / spanX, scaleY = (H - 2*margin) / spanY;
const scale = Math.min(scaleX, scaleY);
const offX = margin + (W - 2*margin - spanX*scale)/2;
const offY = margin + (H - 2*margin - spanY*scale)/2;

function toScreen(x, y) { return [offX + (x - minX) * scale, offY + (maxY - y) * scale]; }
function toWorld(sx, sy) { return [(sx - offX) / scale + minX, maxY - (sy - offY) / scale]; }

// Buildings state
let buildings = [];
let selectedIdx = -1;
let dragging = false, resizing = false, dragOff = [0,0], resizeCorner = -1;
let defaultFloors = 4;

function drawPoly(coords, fill, stroke, lw) {
  if (!coords.length) return;
  ctx.beginPath();
  coords.forEach((p, i) => { const s = toScreen(p[0], p[1]); i === 0 ? ctx.moveTo(s[0], s[1]) : ctx.lineTo(s[0], s[1]); });
  ctx.closePath();
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw || 1; ctx.stroke(); }
}

function drawBuilding(b, idx) {
  const cos = Math.cos(b.angle), sin = Math.sin(b.angle);
  const hw = b.w/2, hd = b.d/2;
  const corners = [
    [b.cx + hw*cos - hd*sin, b.cy + hw*sin + hd*cos],
    [b.cx - hw*cos - hd*sin, b.cy - hw*sin + hd*cos],
    [b.cx - hw*cos + hd*sin, b.cy - hw*sin - hd*cos],
    [b.cx + hw*cos + hd*sin, b.cy + hw*sin - hd*cos],
  ];
  const sel = idx === selectedIdx;
  const alpha = sel ? '90' : '60';
  const colors = ['#22c55e','#38bdf8','#a78bfa','#f59e0b','#ec4899','#06b6d4','#ef4444'];
  const c = colors[idx % colors.length];
  drawPoly(corners, c + alpha, sel ? '#fff' : c, sel ? 2.5 : 1.5);
  // Label
  const sc = toScreen(b.cx, b.cy);
  ctx.fillStyle = '#fff'; ctx.font = 'bold 12px Inter'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(b.name || ('B'+(idx+1)), sc[0], sc[1] - 8);
  ctx.font = '10px Inter'; ctx.fillStyle = '#c8d3df';
  ctx.fillText(b.floors + ' etg | ' + Math.round(b.w * b.d) + ' m²', sc[0], sc[1] + 8);

  // Veggmål — bredde og dybde langs kanter
  const s0 = toScreen(corners[0][0], corners[0][1]);
  const s1 = toScreen(corners[1][0], corners[1][1]);
  const s2 = toScreen(corners[2][0], corners[2][1]);
  const s3 = toScreen(corners[3][0], corners[3][1]);
  ctx.font = 'bold 10px Inter'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  // Bredde (topp-kant: s0→s1)
  const wMidX = (s0[0]+s1[0])/2, wMidY = (s0[1]+s1[1])/2;
  const wTxt = b.w.toFixed(1) + ' m';
  const wTw = ctx.measureText(wTxt).width;
  ctx.fillStyle = 'rgba(6,17,26,0.85)'; ctx.fillRect(wMidX-wTw/2-3, wMidY-14, wTw+6, 13);
  ctx.fillStyle = '#38bdf8'; ctx.fillText(wTxt, wMidX, wMidY-8);
  // Dybde (høyre-kant: s0→s3)
  const dMidX = (s0[0]+s3[0])/2, dMidY = (s0[1]+s3[1])/2;
  const dTxt = b.d.toFixed(1) + ' m';
  const dTw = ctx.measureText(dTxt).width;
  ctx.fillStyle = 'rgba(6,17,26,0.85)'; ctx.fillRect(dMidX+6, dMidY-7, dTw+6, 13);
  ctx.fillStyle = '#38bdf8'; ctx.fillText(dTxt, dMidX+9+dTw/2, dMidY);

  // Resize handles when selected
  if (sel) {
    corners.forEach(c => {
      const s = toScreen(c[0], c[1]);
      ctx.fillStyle = '#38bdf8'; ctx.fillRect(s[0]-4, s[1]-4, 8, 8);
    });
  }
  return corners;
}

function computeStats() {
  let totalFootprint = 0, totalBTA = 0;
  buildings.forEach(b => { const fp = b.w * b.d; totalFootprint += fp; totalBTA += fp * b.floors; });
  const bra = totalBTA * P.efficiency;
  const units = Math.round(bra / P.avg_unit_m2);
  const braPct = P.site_area > 0 ? (bra / P.site_area * 100) : 0;
  document.getElementById('hudBTA').textContent = 'BTA: ' + Math.round(totalBTA).toLocaleString() + ' m²';
  document.getElementById('hudBRA').textContent = 'BRA: ' + Math.round(bra).toLocaleString() + ' m²';
  document.getElementById('hudBRApct').textContent = '%-BRA: ' + braPct.toFixed(0) + '%';
  document.getElementById('hudBRApct').style.color = braPct > 300 ? '#f87171' : braPct > 200 ? '#f59e0b' : '#38bdf8';
  document.getElementById('hudUnits').textContent = 'Boliger: ~' + units;
  document.getElementById('hudBuildings').textContent = 'Bygg: ' + buildings.length;
}

// Avstandsmåling fra valgt bygg til tomtegrense
function drawDistances(b) {
  if (!siteCoords.length || siteCoords.length < 3) return;
  const cos = Math.cos(b.angle), sin = Math.sin(b.angle);
  const hw = b.w/2, hd = b.d/2;
  // Midtpunkt på hver side av bygget
  const sides = [
    {label:'N', mx: b.cx - hd*sin, my: b.cy + hd*cos, dx: -sin, dy: cos},
    {label:'S', mx: b.cx + hd*sin, my: b.cy - hd*cos, dx: sin, dy: -cos},
    {label:'Ø', mx: b.cx + hw*cos, my: b.cy + hw*sin, dx: cos, dy: sin},
    {label:'V', mx: b.cx - hw*cos, my: b.cy - hw*sin, dx: -cos, dy: -sin},
  ];
  sides.forEach(side => {
    // Finn nærmeste punkt på tomtegrensen fra dette midtpunktet i denne retningen
    let minDist = Infinity;
    for (let i = 0; i < siteCoords.length; i++) {
      const ax = siteCoords[i][0], ay = siteCoords[i][1];
      const bx = siteCoords[(i+1)%siteCoords.length][0], by = siteCoords[(i+1)%siteCoords.length][1];
      // Avstand fra punkt til linjestykke
      const dx = bx-ax, dy = by-ay;
      const len2 = dx*dx + dy*dy;
      if (len2 < 0.01) continue;
      let t = ((side.mx-ax)*dx + (side.my-ay)*dy) / len2;
      t = Math.max(0, Math.min(1, t));
      const px = ax + t*dx, py = ay + t*dy;
      const d = Math.hypot(side.mx-px, side.my-py);
      if (d < minDist) minDist = d;
    }
    if (minDist < Infinity && minDist < 200) {
      const endX = side.mx + side.dx * minDist;
      const endY = side.my + side.dy * minDist;
      const s1 = toScreen(side.mx, side.my);
      const s2 = toScreen(endX, endY);
      // Stiplet linje
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = 'rgba(248,250,252,0.5)';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(s1[0], s1[1]); ctx.lineTo(s2[0], s2[1]); ctx.stroke();
      ctx.setLineDash([]);
      // Avstandslabel
      const midSx = (s1[0]+s2[0])/2, midSy = (s1[1]+s2[1])/2;
      ctx.font = 'bold 11px Inter, sans-serif';
      ctx.fillStyle = '#fff';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      const txt = minDist.toFixed(1) + ' m';
      const tw = ctx.measureText(txt).width;
      ctx.fillStyle = 'rgba(6,17,26,0.8)';
      ctx.fillRect(midSx - tw/2 - 3, midSy - 7, tw + 6, 14);
      ctx.fillStyle = '#f5f7fb';
      ctx.fillText(txt, midSx, midSy);
    }
  });
}

function render() {
  ctx.clearRect(0, 0, W, H);
  // Neighbors — med høyde-labels
  (P.neighbors || []).forEach(n => {
    const nc = flatCoords(n.coords || []);
    if (!nc.length) return;
    drawPoly(nc, 'rgba(100,100,120,0.25)', 'rgba(150,150,170,0.4)', 1);
    // Høyde-label — diskret, alltid synlig
    const h = n.height_m || 0;
    if (h > 0 && nc.length >= 3) {
      let sx = 0, sy = 0;
      nc.forEach(p => { const s = toScreen(p[0], p[1]); sx += s[0]; sy += s[1]; });
      sx /= nc.length; sy /= nc.length;
      ctx.font = '9px Inter, sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      const txt = h.toFixed(0) + 'm';
      ctx.fillStyle = 'rgba(150,155,170,0.7)';
      ctx.fillText(txt, sx, sy);
    }
  });
  // Site
  drawPoly(siteCoords, 'rgba(56,189,248,0.06)', 'rgba(56,189,248,0.5)', 1.5);
  // Buildable
  if (buildableCoords.length) drawPoly(buildableCoords, null, 'rgba(52,211,153,0.35)', 1);
  // Buildings
  buildings.forEach((b, i) => drawBuilding(b, i));
  // Avstandsmål for valgt bygg
  if (selectedIdx >= 0 && selectedIdx < buildings.length) {
    drawDistances(buildings[selectedIdx]);
  }
  // Scale bar
  const barM = Math.pow(10, Math.floor(Math.log10(spanX * 0.3)));
  const barPx = barM * scale;
  ctx.fillStyle = '#9fb0c3'; ctx.font = '10px Inter'; ctx.textAlign = 'left';
  ctx.fillRect(14, H - 20, barPx, 3); ctx.fillText(barM + ' m', 14, H - 26);
  computeStats();
}

window.addBuilding = function() {
  const cx = (minX + maxX) / 2 + (Math.random() - 0.5) * spanX * 0.3;
  const cy = (minY + maxY) / 2 + (Math.random() - 0.5) * spanY * 0.3;
  buildings.push({ cx, cy, w: 40, d: 14, angle: 0, floors: defaultFloors, name: 'Bygg ' + String.fromCharCode(65 + buildings.length) });
  selectedIdx = buildings.length - 1;
  render();
};

window.clearAll = function() { buildings = []; selectedIdx = -1; render(); };

window.setFloors = function() {
  defaultFloors = parseInt(document.getElementById('floorSelect').value) || 4;
  if (selectedIdx >= 0) { buildings[selectedIdx].floors = defaultFloors; render(); }
};

window.exportSketch = function() {
  const data = buildings.map(b => ({
    name: b.name, cx: Math.round(b.cx*100)/100, cy: Math.round(b.cy*100)/100,
    w: Math.round(b.w*10)/10, d: Math.round(b.d*10)/10,
    angle_deg: Math.round(b.angle * 180 / Math.PI * 10) / 10,
    floors: b.floors, footprint_m2: Math.round(b.w * b.d),
    bta_m2: Math.round(b.w * b.d * b.floors),
  }));
  const json = JSON.stringify(data, null, 2);
  navigator.clipboard.writeText(json).then(() => {
    document.getElementById('exportOverlay').style.display = 'block';
    setTimeout(() => { document.getElementById('exportOverlay').style.display = 'none'; }, 3000);
  }).catch(() => {
    prompt('Kopier denne teksten:', json);
  });
};

window.lockAndRunSketch = function() {
  const data = buildings.map(b => ({
    name: b.name, cx: Math.round(b.cx*100)/100, cy: Math.round(b.cy*100)/100,
    w: Math.round(b.w*10)/10, d: Math.round(b.d*10)/10,
    angle_deg: Math.round(b.angle * 180 / Math.PI * 10) / 10,
    floors: b.floors, footprint_m2: Math.round(b.w * b.d),
    bta_m2: Math.round(b.w * b.d * b.floors),
  }));
  if (data.length === 0) { alert('Tegn minst ett bygg først!'); return; }
  const json = JSON.stringify(data, null, 2);
  // Skriv direkte til Streamlit textarea og klikk «Kjør motor»
  try {
    const parent = window.parent.document;
    const textareas = parent.querySelectorAll('textarea');
    let filled = false;
    for (const ta of textareas) {
      const label = ta.getAttribute('aria-label') || '';
      if (label.includes('Skisse-data') || label.includes('skisse')) {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
        setter.call(ta, json);
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        ta.dispatchEvent(new Event('change', { bubbles: true }));
        filled = true;
        break;
      }
    }
    if (filled) {
      // Klikk «Kjør motor fra skisse»-knappen
      setTimeout(() => {
        const buttons = parent.querySelectorAll('button');
        for (const btn of buttons) {
          if (btn.textContent && btn.textContent.includes('motor fra skisse')) {
            btn.click();
            return;
          }
        }
      }, 300);
    } else {
      // Fallback: kopier til utklippstavle
      navigator.clipboard.writeText(json);
      alert('Kunne ikke fylle feltet automatisk. Skissen er kopiert — lim inn manuelt.');
    }
  } catch(e) {
    navigator.clipboard.writeText(json);
    alert('Automatisk utfylling støttes ikke. Skissen er kopiert — lim inn manuelt.');
  }
};

// Hit testing
function hitTest(sx, sy) {
  for (let i = buildings.length - 1; i >= 0; i--) {
    const b = buildings[i];
    const [wx, wy] = toWorld(sx, sy);
    const dx = wx - b.cx, dy = wy - b.cy;
    const cos = Math.cos(-b.angle), sin = Math.sin(-b.angle);
    const lx = dx*cos - dy*sin, ly = dx*sin + dy*cos;
    if (Math.abs(lx) <= b.w/2 + 2 && Math.abs(ly) <= b.d/2 + 2) return i;
  }
  return -1;
}

function cornerHit(sx, sy) {
  if (selectedIdx < 0) return -1;
  const b = buildings[selectedIdx];
  const cos = Math.cos(b.angle), sin = Math.sin(b.angle);
  const hw = b.w/2, hd = b.d/2;
  const corners = [
    [b.cx + hw*cos - hd*sin, b.cy + hw*sin + hd*cos],
    [b.cx - hw*cos - hd*sin, b.cy - hw*sin + hd*cos],
    [b.cx - hw*cos + hd*sin, b.cy - hw*sin - hd*cos],
    [b.cx + hw*cos + hd*sin, b.cy + hw*sin - hd*cos],
  ];
  for (let i = 0; i < 4; i++) {
    const s = toScreen(corners[i][0], corners[i][1]);
    if (Math.hypot(sx - s[0], sy - s[1]) < 10) return i;
  }
  return -1;
}

let lastClick = 0;
canvas.addEventListener('mousedown', e => {
  const rect = canvas.getBoundingClientRect();
  const sx = (e.clientX - rect.left), sy = (e.clientY - rect.top);
  const now = Date.now();
  // Double click = delete
  if (now - lastClick < 350) {
    const idx = hitTest(sx, sy);
    if (idx >= 0) { buildings.splice(idx, 1); selectedIdx = -1; render(); lastClick = 0; return; }
  }
  lastClick = now;
  // Corner resize
  const ci = cornerHit(sx, sy);
  if (ci >= 0) { resizing = true; resizeCorner = ci; return; }
  // Building drag
  const idx = hitTest(sx, sy);
  if (idx >= 0) {
    selectedIdx = idx;
    const [wx, wy] = toWorld(sx, sy);
    dragOff = [wx - buildings[idx].cx, wy - buildings[idx].cy];
    dragging = true;
    document.getElementById('floorSelect').value = buildings[idx].floors;
    render();
    return;
  }
  selectedIdx = -1;
  render();
});

canvas.addEventListener('mousemove', e => {
  const rect = canvas.getBoundingClientRect();
  const sx = (e.clientX - rect.left), sy = (e.clientY - rect.top);
  if (dragging && selectedIdx >= 0) {
    const [wx, wy] = toWorld(sx, sy);
    buildings[selectedIdx].cx = wx - dragOff[0];
    buildings[selectedIdx].cy = wy - dragOff[1];
    render();
  } else if (resizing && selectedIdx >= 0) {
    const [wx, wy] = toWorld(sx, sy);
    const b = buildings[selectedIdx];
    const dx = wx - b.cx, dy = wy - b.cy;
    b.w = Math.max(8, Math.abs(dx) * 2);
    b.d = Math.max(8, Math.abs(dy) * 2);
    render();
  } else {
    // Cursor hint
    const ci = cornerHit(sx, sy);
    canvas.style.cursor = ci >= 0 ? 'nwse-resize' : hitTest(sx, sy) >= 0 ? 'move' : 'crosshair';
  }
});

canvas.addEventListener('mouseup', () => { dragging = false; resizing = false; });
canvas.addEventListener('mouseleave', () => { dragging = false; resizing = false; });

// Touch support
canvas.addEventListener('touchstart', e => {
  e.preventDefault();
  const t = e.touches[0], rect = canvas.getBoundingClientRect();
  const sx = t.clientX - rect.left, sy = t.clientY - rect.top;
  const idx = hitTest(sx, sy);
  if (idx >= 0) { selectedIdx = idx; const [wx, wy] = toWorld(sx, sy); dragOff = [wx - buildings[idx].cx, wy - buildings[idx].cy]; dragging = true; render(); }
}, {passive: false});
canvas.addEventListener('touchmove', e => {
  e.preventDefault();
  if (!dragging || selectedIdx < 0) return;
  const t = e.touches[0], rect = canvas.getBoundingClientRect();
  const [wx, wy] = toWorld(t.clientX - rect.left, t.clientY - rect.top);
  buildings[selectedIdx].cx = wx - dragOff[0]; buildings[selectedIdx].cy = wy - dragOff[1];
  render();
}, {passive: false});
canvas.addEventListener('touchend', () => { dragging = false; });

// Rotation with scroll on selected building
canvas.addEventListener('wheel', e => {
  if (selectedIdx >= 0) {
    e.preventDefault();
    buildings[selectedIdx].angle += e.deltaY > 0 ? 0.05 : -0.05;
    render();
  }
}, {passive: false});

render();
})();
</script>
""".replace("__PAYLOAD__", editor_payload)
    components.html(editor_html, height=680, scrolling=False)

    # --- SKISSE TIL MOTOR ---
    with st.expander("Kjør motor fra manuell skisse", expanded=False):
        st.caption("Bruk «🔒 Lås og kjør motor» i editoren for direkte kjøring, eller lim inn manuelt her.")
        sketch_json = st.text_area(
            "Skisse-data",
            height=60,
            placeholder='Fylles automatisk fra «🔒 Lås og kjør motor» eller lim inn manuelt',
            key="sketch_json_input",
        )
        sk_c1, sk_c2 = st.columns(2)
        sketch_floors_override = sk_c1.number_input("Overstyr etasjer (0 = fra skisse)", min_value=0, max_value=12, value=0, key="sketch_floors")
        sketch_efficiency = sk_c2.number_input("Salgbarhetsfaktor", min_value=0.55, max_value=0.90, value=0.78, step=0.01, key="sketch_eff")

        if st.button("Kjør motor fra skisse", type="primary", use_container_width=True, key="run_sketch"):
            if sketch_json and sketch_json.strip().startswith("["):
                try:
                    sketch_buildings = json.loads(sketch_json)
                    SKETCH_COLORS = [
                        [34, 197, 94, 200], [56, 189, 248, 200], [168, 130, 240, 200],
                        [250, 180, 60, 200], [220, 80, 120, 200], [100, 200, 180, 200],
                    ]

                    total_footprint = 0.0
                    total_bta = 0.0
                    sketch_parts = []
                    sketch_fp_polygons = []
                    floor_to_floor = site_result.get("floor_to_floor_m", 3.0)

                    for idx, bld in enumerate(sketch_buildings):
                        floors = sketch_floors_override if sketch_floors_override > 0 else int(bld.get("floors", 4))
                        w = float(bld.get("w", 40))
                        d = float(bld.get("d", 14))
                        cx_val = float(bld.get("cx", 0))
                        cy_val = float(bld.get("cy", 0))
                        angle = math.radians(float(bld.get("angle_deg", 0)))
                        fp = w * d
                        total_footprint += fp
                        total_bta += fp * floors

                        cos_a, sin_a = math.cos(angle), math.sin(angle)
                        hw, hd = w / 2.0, d / 2.0
                        corners = [
                            (cx_val + hw*cos_a - hd*sin_a, cy_val + hw*sin_a + hd*cos_a),
                            (cx_val - hw*cos_a - hd*sin_a, cy_val - hw*sin_a + hd*cos_a),
                            (cx_val - hw*cos_a + hd*sin_a, cy_val - hw*sin_a - hd*cos_a),
                            (cx_val + hw*cos_a + hd*sin_a, cy_val + hw*sin_a - hd*cos_a),
                        ]
                        bld_poly = Polygon(corners).buffer(0)
                        sketch_fp_polygons.append(bld_poly)
                        sketch_parts.append({
                            "name": bld.get("name", f"Bygg {chr(65+idx)}"),
                            "height_m": round(floors * floor_to_floor, 1),
                            "floors": floors,
                            "color": SKETCH_COLORS[idx % len(SKETCH_COLORS)],
                            "coords": geometry_to_coord_groups(bld_poly),
                        })

                    # Bygg fullverdig OptionResult fra skissen
                    site_area_val = site_result.get("site_area_m2", 1800)
                    bya_pct = (total_footprint / max(site_area_val, 1.0)) * 100.0
                    saleable = total_bta * sketch_efficiency
                    avg_unit = sum(spec.share_pct * spec.avg_size_m2 for spec in mix_inputs) / max(sum(spec.share_pct for spec in mix_inputs), 1.0)
                    mix_counts, _ = allocate_unit_mix(saleable, mix_inputs)
                    unit_count = sum(mix_counts.values())
                    max_floors = max((sketch_floors_override if sketch_floors_override > 0 else int(b.get("floors", 4))) for b in sketch_buildings)
                    height_m = max_floors * floor_to_floor
                    open_space_ratio = max(0.0, 1.0 - (total_footprint / max(site_area_val, 1.0)))
                    parking_spaces = int(math.ceil(unit_count * site_result.get("parking_ratio_per_unit", 0.8)))

                    # Sol/skygge
                    combined_fp = unary_union(sketch_fp_polygons).buffer(0)
                    site_poly = best.geometry.get("site_polygon_coords", [])
                    site_polygon_obj = Polygon(flatten_coord_groups(site_poly)) if site_poly else box(0, 0, 50, 50)
                    solar = evaluate_solar(
                        site=SiteInputs(**site_result),
                        site_polygon=site_polygon_obj,
                        footprint_polygon=combined_fp,
                        building_height_m=height_m,
                        typology="Manuell",
                    )

                    sketch_option = OptionResult(
                        name="Skisse (manuell)",
                        typology="Manuell plassering",
                        floors=max_floors,
                        building_height_m=round(height_m, 1),
                        footprint_area_m2=round(total_footprint, 1),
                        gross_bta_m2=round(total_bta, 1),
                        saleable_area_m2=round(saleable, 1),
                        footprint_width_m=0.0,
                        footprint_depth_m=0.0,
                        buildable_area_m2=round(site_area_val, 1),
                        open_space_ratio=round(open_space_ratio, 3),
                        target_fit_pct=round(100.0 * total_bta / max(site_result.get("desired_bta_m2", total_bta), 1.0), 1),
                        unit_count=unit_count,
                        mix_counts=mix_counts,
                        parking_spaces=parking_spaces,
                        parking_pressure_pct=0.0,
                        solar_score=round(solar["solar_score"], 1),
                        estimated_equinox_sun_hours=round(solar["estimated_equinox_sun_hours"], 1),
                        estimated_winter_sun_hours=round(solar["estimated_winter_sun_hours"], 1),
                        sunlit_open_space_pct=round(solar["sunlit_open_space_pct"], 1),
                        winter_noon_shadow_m=round(solar["winter_noon_shadow_m"], 1),
                        equinox_noon_shadow_m=round(solar["equinox_noon_shadow_m"], 1),
                        summer_afternoon_shadow_m=round(solar["summer_afternoon_shadow_m"], 1),
                        efficiency_ratio=round(sketch_efficiency, 3),
                        neighbor_count=best.neighbor_count,
                        terrain_slope_pct=best.terrain_slope_pct,
                        terrain_relief_m=best.terrain_relief_m,
                        notes=[f"Manuell skisse med {len(sketch_buildings)} bygg.", f"BYA {bya_pct:.1f}% av tomteareal {site_area_val:.0f} m²."],
                        score=round(solar["solar_score"] * 0.5 + min(100, 100 * total_bta / max(site_result.get("desired_bta_m2", total_bta), 1)) * 0.5, 1),
                        geometry={
                            "site_polygon_coords": best.geometry.get("site_polygon_coords", []),
                            "buildable_polygon_coords": best.geometry.get("buildable_polygon_coords", []),
                            "footprint_polygon_coords": geometry_to_coord_groups(combined_fp),
                            "winter_shadow_polygon_coords": [],
                            "neighbor_polygons": best.geometry.get("neighbor_polygons", []),
                            "terrain_summary": best.geometry.get("terrain_summary", {}),
                            "placement": {"source": "Manuell skisse"},
                            "massing_parts": sketch_parts,
                            "component_count": len(sketch_buildings),
                        },
                    )

                    # Sett skissen som første (anbefalt) alternativ og behold de andre
                    existing_options = result.get("options", [])
                    new_options = [asdict(sketch_option)] + existing_options

                    # Render flere visninger av den manuelle skissen
                    site_obj = SiteInputs(**site_result)
                    sketch_views = render_sketch_views(site_obj, sketch_option)
                    sketch_image = sketch_views[0] if sketch_views else render_plan_diagram(site_obj, sketch_option)

                    # --- SEKUNDÆR AI-ANALYSE av den låste skissen ---
                    motor_options = [OptionResult(**opt) if isinstance(opt, dict) else opt for opt in existing_options]
                    manual_override_data = {
                        "gross_bta_m2": sketch_option.gross_bta_m2,
                        "saleable_area_m2": sketch_option.saleable_area_m2,
                        "unit_count": sketch_option.unit_count,
                        "footprint_area_m2": sketch_option.footprint_area_m2,
                        "floors": sketch_option.floors,
                        "building_height_m": sketch_option.building_height_m,
                        "solar_score": sketch_option.solar_score,
                        "sunlit_open_space_pct": sketch_option.sunlit_open_space_pct,
                        "mix_counts": sketch_option.mix_counts,
                        "n_buildings": len(sketch_buildings),
                    }

                    # AI-rapport: Claude analyserer den låste skissen
                    updated_report = None
                    if ANTHROPIC_API_KEY:
                        with st.spinner("Claude analyserer den valgte volumløsningen..."):
                            try:
                                # Bygg enkel geodata-kontekst for AI
                                ai_geodata = {
                                    "site_area_m2": site_result.get("site_area_m2", 1800),
                                    "terrain": {
                                        "slope_pct": site_result.get("terrain_slope_pct", 0),
                                        "relief_m": site_result.get("terrain_relief_m", 0),
                                    },
                                }
                                updated_report = generate_ai_report_for_locked_sketch(
                                    sketch_option=sketch_option,
                                    motor_options=motor_options,
                                    site=site_obj,
                                    geodata_context=ai_geodata,
                                    environment_data=result.get("environment"),
                                )
                            except Exception:
                                updated_report = None

                    # Fallback: deterministisk rapport hvis AI feiler
                    if not updated_report:
                        try:
                            updated_report = build_deterministic_report(
                                site_obj, motor_options, {}, has_visual_input=True,
                                manual_override=manual_override_data,
                                environment_data=result.get("environment"),
                            )
                        except Exception:
                            updated_report = result.get("report_text", "")

                    # --- REGENERER PDF med skissebilder og AI-rapport ---
                    all_option_images = [sketch_image] + result.get("option_images", [])
                    try:
                        pd_state = st.session_state.get("project_data", {})
                        new_pdf_bytes = create_full_report_pdf(
                            name=pd_state.get("p_name", "Prosjekt"),
                            client=pd_state.get("c_name", "Ukjent"),
                            land=pd_state.get("land", "Norge"),
                            report_text=updated_report,
                            options=motor_options,
                            option_images=all_option_images,
                            visual_attachments=[],
                            manual_sketch_images=sketch_views,
                            site=site_obj,
                            environment_data=result.get("environment"),
                        )
                        st.session_state.generated_ark_pdf = new_pdf_bytes
                        st.session_state.generated_ark_filename = f"Builtly_ARK_{pd_state.get('p_name', 'Prosjekt')}_manuell.pdf"
                    except Exception:
                        pass  # Behold gammel PDF hvis regen feiler

                    # Oppdater analysis_results
                    st.session_state.analysis_results["options"] = new_options
                    st.session_state.analysis_results["option_images"] = all_option_images
                    st.session_state.analysis_results["report_text"] = updated_report
                    st.session_state.analysis_results["manual_override"] = manual_override_data
                    st.session_state.analysis_results["manual_sketch_views"] = sketch_views
                    st.rerun()

                except json.JSONDecodeError:
                    st.error("Ugyldig JSON. Trykk «Kopier skisse» i editoren og lim inn på nytt.")
                except Exception as exc:
                    st.error(f"Feil ved behandling av skisse: {exc}")
            else:
                st.warning("Lim inn skisse-data fra planediteren (JSON-format, starter med [).")

    # --- AI-RAFFINERING OG ALTERNATIVGENERERING ---
    with st.expander("AI-raffinering og alternative volumløsninger", expanded=False):
        ai_available = bool(ANTHROPIC_API_KEY)
        if not ai_available:
            st.info("Claude API er ikke konfigurert. Sett ANTHROPIC_API_KEY for AI-raffinering. Deterministisk soloptimering er tilgjengelig.")

        st.caption("Bruk skissen fra editoren som utgangspunkt. AI optimerer plassering, eller genererer alternativer innenfor samme bounding box.")

        ai_sketch_json = st.text_area(
            "Skisse-data for AI",
            height=80,
            key="ai_sketch_json",
            placeholder='Lim inn fra «Kopier skisse»',
        )

        ai_col1, ai_col2 = st.columns(2)

        # --- RAFFINER SKISSE ---
        with ai_col1:
            if st.button("Raffiner skisse med AI", type="primary", use_container_width=True, key="btn_refine_ai",
                         disabled=not ai_sketch_json):
                if ai_sketch_json and ai_sketch_json.strip().startswith("["):
                    try:
                        raw_buildings = json.loads(ai_sketch_json)
                        site_poly_coords = flatten_coord_groups(best.geometry.get("site_polygon_coords", []))
                        neighbor_data = best.geometry.get("neighbor_polygons", [])

                        with st.spinner("Claude analyserer skissen og optimerer plassering..."):
                            if ai_available:
                                refined = refine_sketch_with_ai(
                                    sketch_buildings=raw_buildings,
                                    site_polygon_coords=site_poly_coords,
                                    site_area_m2=site_result.get("site_area_m2", 2000),
                                    latitude_deg=site_result.get("latitude_deg", 59.91),
                                    max_bya_pct=site_result.get("max_bya_pct", 35),
                                    max_floors=site_result.get("max_floors", 5),
                                    max_height_m=site_result.get("max_height_m", 16),
                                    floor_to_floor_m=site_result.get("floor_to_floor_m", 3.0),
                                    neighbors=neighbor_data,
                                    environment=result.get("environment", {}),
                                )
                            else:
                                refined = None

                            if refined is None:
                                st.info("AI-kall feilet eller utilgjengelig — bruker deterministisk soloptimering.")
                                refined = _deterministic_solar_refinement(raw_buildings, site_result.get("latitude_deg", 59.91))

                        st.success(f"Raffinering fullført — {len(refined)} bygg optimert")

                        # Vis endringer
                        for bld in refined:
                            reasoning = bld.get("reasoning", "")
                            st.caption(f"**{bld.get('name', '?')}**: {bld.get('w', 0):.0f}×{bld.get('d', 0):.0f} m, "
                                      f"{bld.get('floors', 4)} etg, vinkel {bld.get('angle_deg', 0):.0f}° — _{reasoning}_")

                        # Lagre raffinert JSON for bruk i motor
                        refined_json = json.dumps(refined, ensure_ascii=False, indent=2)
                        st.text_area("Raffinert skisse (kopier til «Kjør motor fra skisse»)", value=refined_json, height=150, key="refined_output")

                    except Exception as exc:
                        st.error(f"Feil: {exc}")

        # --- GENERER ALTERNATIVER ---
        with ai_col2:
            if st.button("Generer alternativer fra skisse", type="secondary", use_container_width=True, key="btn_variants",
                         disabled=not ai_sketch_json or not ai_available):
                if ai_sketch_json and ai_sketch_json.strip().startswith("["):
                    try:
                        raw_buildings = json.loads(ai_sketch_json)
                        site_poly_coords = flatten_coord_groups(best.geometry.get("site_polygon_coords", []))

                        with st.spinner("Claude genererer alternative volumløsninger..."):
                            variants_result = generate_sketch_variants(
                                sketch_buildings=raw_buildings,
                                site_polygon_coords=site_poly_coords,
                                site_area_m2=site_result.get("site_area_m2", 2000),
                                latitude_deg=site_result.get("latitude_deg", 59.91),
                                max_bya_pct=site_result.get("max_bya_pct", 35),
                                max_floors=site_result.get("max_floors", 5),
                                max_height_m=site_result.get("max_height_m", 16),
                                floor_to_floor_m=site_result.get("floor_to_floor_m", 3.0),
                                neighbors=best.geometry.get("neighbor_polygons", []),
                            )

                        if variants_result and variants_result.get("variants"):
                            for var in variants_result["variants"]:
                                var_name = var.get("name", "Variant")
                                var_desc = var.get("description", "")
                                var_buildings = var.get("buildings", [])
                                total_bta = sum(b.get("w", 0) * b.get("d", 0) * b.get("floors", 4) for b in var_buildings)

                                st.markdown(f"**{var_name}** — {var_desc}")
                                st.caption(f"{len(var_buildings)} bygg, ~{total_bta:,.0f} m² BTA")

                                var_json = json.dumps(var_buildings, ensure_ascii=False, indent=2)
                                st.text_area(f"JSON for {var_name} (kopier til motor)", value=var_json, height=100, key=f"var_{var_name}")
                        else:
                            st.warning("Kunne ikke generere alternativer. Sjekk at Claude API er tilkoblet.")

                    except Exception as exc:
                        st.error(f"Feil: {exc}")

    st.markdown("<div class='section-header'>Leilighetsmiks per alternativ</div>", unsafe_allow_html=True)
    mix_rows = []
    for option in options:
        row = {"Alternativ": option.name}
        row.update(option.mix_counts)
        row["Totalt"] = option.unit_count
        mix_rows.append(row)
    mix_df = pd.DataFrame(mix_rows).fillna(0)
    st.dataframe(mix_df, use_container_width=True, hide_index=True)

    st.markdown("<div class='section-header'>Sol, skygge og terreng</div>", unsafe_allow_html=True)
    solar_df = pd.DataFrame(
        {
            option.name: {
                "Solbelyst uteareal %": option.sunlit_open_space_pct,
                "Vår/høst soltimer": option.estimated_equinox_sun_hours,
                "Vinter soltimer": option.estimated_winter_sun_hours,
                "Vinterskygge kl. 12 (m)": option.winter_noon_shadow_m,
                "Sommerskygge kl. 15 (m)": option.summer_afternoon_shadow_m,
                "Terrengfall %": option.terrain_slope_pct,
                "Terreng relieff m": option.terrain_relief_m,
            }
            for option in options
        }
    ).T
    st.dataframe(solar_df, use_container_width=True)

    if geodata_token_ok and gdo is not None:
        st.markdown("<div class='section-header'>3D Terrengscene</div>", unsafe_allow_html=True)
        selected_name = st.selectbox('Velg volum for 3D-scene', [opt.name for opt in options], index=0)
        selected_option = next((opt for opt in options if opt.name == selected_name), options[0])
        try:
            scene_config = gdo.fetch_scene_config()
            render_geodata_scene(SiteInputs(**site_result), selected_option, scene_config, height_px=620)

            # --- BATCH CAPTURE: ta bilder av alle alternativer ---
            if st.button("📸 Ta bilder av alle alternativer", use_container_width=True, key="batch_capture_btn"):
                all_payloads = []
                for opt in options:
                    p = build_geodata_scene_payload(SiteInputs(**site_result), opt, scene_config)
                    all_payloads.append(p)
                batch_json = json.dumps(all_payloads, ensure_ascii=False)
                batch_html = _build_batch_capture_html(batch_json, height_px=620)
                components.html(batch_html, height=660, scrolling=False)
        except Exception as exc:
            st.caption(f'3D-scene kunne ikke rendres akkurat nå: {exc}')

    # --- Interaktiv Three.js 3D-modell ---
    st.markdown("<div class='section-header'>3D Volummodell (interaktiv)</div>", unsafe_allow_html=True)
    sel3d_name = st.selectbox('Velg alternativ for 3D-visning', [opt.name for opt in options], index=0, key='sel3d')
    sel3d_opt = next((opt for opt in options if opt.name == sel3d_name), options[0])
    try:
        render_interactive_3d(SiteInputs(**site_result), sel3d_opt, height_px=650, terrain_ctx=result.get('terrain_ctx'))
    except Exception as exc:
        st.caption(f'3D-modell kunne ikke rendres: {exc}')

    # --- 3D-scene bilder til rapport ---
    with st.expander("Legg til 3D-scenebilder i rapporten", expanded=False):
        st.caption("Bruk «📸 Last ned bilde» i 3D-terrengscenen for å ta bilder fra ønsket vinkel, "
                   "og last dem opp her for å inkludere dem i PDF-rapporten.")
        scene_uploads = st.file_uploader(
            "Last opp 3D-scenebilder (PNG/JPG)",
            type=["png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key="scene_image_uploads",
        )
        if scene_uploads:
            scene_images_for_pdf = []
            for f in scene_uploads:
                try:
                    img_uploaded = Image.open(f).convert("RGB")
                    scene_images_for_pdf.append(img_uploaded)
                except Exception:
                    pass
            if scene_images_for_pdf:
                st.success(f"{len(scene_images_for_pdf)} bilde(r) lastet opp — klikk «Oppdater PDF» for å inkludere i rapporten.")
                if st.button("Oppdater PDF med 3D-bilder", type="primary", use_container_width=True, key="regen_pdf_3d"):
                    try:
                        pd_state = st.session_state.get("project_data", {})
                        motor_options = [OptionResult(**opt) if isinstance(opt, dict) else opt for opt in result.get("options", [])]
                        manual_ov = result.get("manual_override")
                        updated_report = result.get("report_text", "")
                        manual_views = result.get("manual_sketch_views", [])
                        all_images = result.get("option_images", [])
                        new_pdf_bytes = create_full_report_pdf(
                            name=pd_state.get("p_name", "Prosjekt"),
                            client=pd_state.get("c_name", "Ukjent"),
                            land=pd_state.get("land", "Norge"),
                            report_text=updated_report,
                            options=motor_options,
                            option_images=all_images,
                            visual_attachments=scene_images_for_pdf,
                            manual_sketch_images=manual_views if manual_views else None,
                            site=SiteInputs(**site_result) if site_result else None,
                            environment_data=result.get("environment"),
                        )
                        st.session_state.generated_ark_pdf = new_pdf_bytes
                        st.session_state.generated_ark_filename = f"Builtly_ARK_{pd_state.get('p_name', 'Prosjekt')}_3D.pdf"
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Feil ved PDF-oppdatering: {exc}")

    st.markdown("<div class='section-header'>Rapport</div>", unsafe_allow_html=True)
    st.markdown(result["report_text"])

    st.markdown("<div class='section-header'>Nedlasting</div>", unsafe_allow_html=True)
    cdl, cqa = st.columns(2)
    with cdl:
        pdf_data = st.session_state.get("generated_ark_pdf")
        pdf_name = st.session_state.get("generated_ark_filename", "Builtly_ARK_rapport.pdf")
        if pdf_data:
            st.download_button(
                "Last ned mulighetsstudie (PDF)",
                data=pdf_data,
                file_name=pdf_name,
                mime="application/pdf",
                type="primary",
                use_container_width=True,
            )
        else:
            st.warning("PDF er ikke generert ennå. Kjør tomtestudie først.")
    with cqa:
        if find_page("Review"):
            if st.button("Gå til QA for godkjenning", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))
