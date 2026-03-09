
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



def geo_runtime_notes() -> List[str]:
    notes: List[str] = []
    if not HAS_PYPROJ:
        notes.append("pyproj mangler: GeoJSON i lon/lat og OSM-nabohenting blir deaktivert eller mindre presist.")
    if not HAS_RASTERIO:
        notes.append("rasterio mangler: GeoTIFF/ASC terreng er deaktivert, men CSV/TXT med x,y,z virker fortsatt.")
    return notes


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

# --- 4. GEOSPATIAL HJELPERE ---
DEFAULT_FLOOR_HEIGHT_M = 3.2


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
    return [[round(float(x), precision), round(float(y), precision)] for x, y in list(poly.exterior.coords)]


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
            info["warning"] = "pyproj mangler; lon/lat-GeoJSON kan ikke transformeres til meter i denne deployen. Bruk UTM/EPSG:25833 eller installer pyproj."
            return None, None, info
        info["crs"] = dst_crs.to_string()
        return transform_polygon(poly, CRS.from_epsg(4326), dst_crs), dst_crs, info
    return poly, None, info


def load_site_polygon_input(uploaded_geojson: Any, coordinate_text: str) -> Tuple[Optional[Polygon], Optional[CRS], Dict[str, Any]]:
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
    direct_keys = [
        "height_m", "height", "hoyde", "building:height", "gesimshoyde", "max_height", "z", "elevation_m"
    ]
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


def terrain_points_from_raster_bytes(data: bytes, site_polygon: Optional[Polygon], site_crs: Optional[CRS]) -> np.ndarray:
    if not HAS_RASTERIO or MemoryFile is None:
        raise ValueError("RasterIO er ikke installert i deployen. Bruk CSV/TXT med x,y,z eller installer rasterio.")
    if site_polygon is None or site_crs is None:
        raise ValueError("Rasterterreng krever georeferert tomtepolygon.")
    with MemoryFile(data) as memfile:
        with memfile.open() as ds:
            if ds.crs is None:
                raise ValueError("Raster mangler CRS.")
            to_raster = Transformer.from_crs(site_crs, ds.crs, always_xy=True)
            to_site = Transformer.from_crs(ds.crs, site_crs, always_xy=True)
            site_coords = [to_raster.transform(x, y) for x, y in list(site_polygon.buffer(50).exterior.coords)]
            site_raster = Polygon(site_coords).buffer(0)
            minx, miny, maxx, maxy = site_raster.bounds
            xs = np.linspace(minx, maxx, 18)
            ys = np.linspace(miny, maxy, 18)
            pts: List[Tuple[float, float, float]] = []
            for rx in xs:
                for ry in ys:
                    try:
                        value = next(ds.sample([(float(rx), float(ry))]))[0]
                    except Exception:
                        continue
                    if value is None or not np.isfinite(value):
                        continue
                    lx, ly = to_site.transform(float(rx), float(ry))
                    if site_polygon.buffer(60).contains(Point(lx, ly)):
                        pts.append((float(lx), float(ly), float(value)))
            if len(pts) < 3:
                raise ValueError("Kunne ikke hente nok terrengpunkter fra rasteret.")
            return np.asarray(pts, dtype=float)


def load_terrain_input(uploaded_terrain: Any, site_polygon: Optional[Polygon], site_crs: Optional[CRS]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    if uploaded_terrain is None:
        return None, {"source": "Ingen terrengfil"}
    try:
        uploaded_terrain.seek(0)
        raw = uploaded_terrain.read()
        suffix = Path(getattr(uploaded_terrain, "name", "terrain")).suffix.lower()
        if suffix in {".csv", ".txt"}:
            points = terrain_points_from_csv_bytes(raw, site_crs)
        elif suffix in {".tif", ".tiff", ".asc"}:
            points = terrain_points_from_raster_bytes(raw, site_polygon, site_crs)
        else:
            raise ValueError("Stotter forelopig CSV/TXT eller GeoTIFF/ASC for terreng.")

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
        buildable_polygon = site_polygon.buffer(-max(0.0, polygon_setback_m)) if polygon_setback_m > 0 else site_polygon
        buildable_polygon = largest_polygon(buildable_polygon)
        if buildable_polygon is None or buildable_polygon.is_empty or buildable_polygon.area < 20.0:
            buildable_polygon = largest_polygon(site_polygon.buffer(-max(0.5, polygon_setback_m * 0.35))) or site_polygon

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


def fit_footprint_into_polygon(footprint: Polygon, container: Polygon) -> Tuple[Polygon, float, float]:
    if container.contains(footprint):
        return footprint, 1.0, 1.0

    footprint = affinity.translate(
        footprint,
        xoff=container.centroid.x - footprint.centroid.x,
        yoff=container.centroid.y - footprint.centroid.y,
    )
    best = footprint
    best_ratio = footprint.intersection(container).area / max(footprint.area, 1e-6)

    minx, miny, maxx, maxy = container.bounds
    step_x = max((maxx - minx) / 8.0, 2.0)
    step_y = max((maxy - miny) / 8.0, 2.0)
    offsets = [(i * step_x, j * step_y) for i in range(-2, 3) for j in range(-2, 3)]
    for dx, dy in offsets:
        candidate = affinity.translate(footprint, xoff=dx, yoff=dy)
        ratio = candidate.intersection(container).area / max(candidate.area, 1e-6)
        if container.contains(candidate):
            return candidate, 1.0, 1.0
        if ratio > best_ratio:
            best = candidate
            best_ratio = ratio

    contained = None
    lo, hi = 0.22, 1.0
    base = best
    for _ in range(24):
        mid = (lo + hi) / 2.0
        scaled = affinity.scale(base, xfact=mid, yfact=mid, origin=base.centroid)
        if container.contains(scaled):
            contained = scaled
            lo = mid
        else:
            hi = mid
    if contained is not None:
        return contained, lo, 1.0
    fallback = affinity.scale(base, xfact=0.55, yfact=0.55, origin=base.centroid)
    return fallback.intersection(container).buffer(0), 0.55, best_ratio



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

def create_typology_footprint(buildable_polygon: Polygon, typology: str, target_footprint_m2: float) -> Tuple[Polygon, Dict[str, Any]]:
    width, depth, orientation_deg = minimum_rotated_dims(buildable_polygon)
    rotated = affinity.rotate(buildable_polygon, -orientation_deg, origin=buildable_polygon.centroid)
    minx, miny, maxx, maxy = rotated.bounds
    geom = build_typology_geometry(typology, target_footprint_m2, maxx - minx, maxy - miny)
    footprint = rects_to_polygon(geom["rects"])
    footprint = affinity.translate(footprint, xoff=minx, yoff=miny)
    footprint, fit_scale, containment_ratio = fit_footprint_into_polygon(footprint, rotated)
    footprint = affinity.rotate(footprint, orientation_deg, origin=buildable_polygon.centroid).buffer(0)
    rotated_footprint = affinity.rotate(footprint, -orientation_deg, origin=buildable_polygon.centroid)
    fx0, fy0, fx1, fy1 = rotated_footprint.bounds
    return footprint, {
        "fit_scale": round(float(fit_scale), 3),
        "containment_ratio": round(float(containment_ratio), 3),
        "footprint_width_m": round(float(fx1 - fx0), 1),
        "footprint_depth_m": round(float(fy1 - fy0), 1),
        "orientation_deg": round(float(orientation_deg), 1),
    }


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
                "coords": polygon_to_coords(neighbor.get("polygon")),
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

    typology_bonus = {"Punkthus": 0.06, "Lamell": 0.04, "Tun": -0.02}.get(typology, 0.0)
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
        {"name": "Alt A - Lamell", "typology": "Lamell", "coverage": 0.74, "floor_bias": 0, "eff_adj": 0.02},
        {"name": "Alt B - Punkthus", "typology": "Punkthus", "coverage": 0.56, "floor_bias": 1, "eff_adj": -0.01},
        {"name": "Alt C - Tun", "typology": "Tun", "coverage": 0.84, "floor_bias": -1, "eff_adj": -0.03},
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
        footprint_polygon, placement = create_typology_footprint(buildable_polygon, typology, target_footprint)
        footprint_area = float(footprint_polygon.area)

        floors_needed = math.ceil(target_bta / max(footprint_area, 1.0))
        floors = clamp(floors_needed + template["floor_bias"], 2, allowed_floors)
        floors = int(floors)

        gross_bta = footprint_area * floors
        if site.max_bra_m2 > 0:
            gross_bta = min(gross_bta, site.max_bra_m2)

        actual_efficiency = clamp(site.efficiency_ratio + template["eff_adj"], 0.66, 0.88)
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

        if parking_pressure_pct > 90:
            notes.append("Parkering legger stort press på tilgjengelig uteareal dersom alt skal løses på terreng.")
        elif parking_pressure_pct > 65:
            notes.append("Parkering er håndterbar, men bør optimaliseres med kjeller eller mobilitetsgrep.")

        if typology == "Lamell":
            notes.append("Lamell er som regel sterkest på effektivitet, dagslys og repetérbar boliglogikk.")
        elif typology == "Punkthus":
            notes.append("Punkthus gir ofte best lys og sikt, men taper gjerne litt effektivitet og kjerneøkonomi.")
        else:
            notes.append("Tun/U-form gir høy arealutnyttelse og tydelig uterom, men er mest sårbar for skygge fra egne fløyer og naboer.")

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

        geometry = {
            "site_polygon_coords": polygon_to_coords(site_polygon),
            "buildable_polygon_coords": polygon_to_coords(buildable_polygon),
            "footprint_polygon_coords": polygon_to_coords(footprint_polygon),
            "winter_shadow_polygon_coords": polygon_to_coords(winter_shadow_poly) if winter_shadow_poly is not None else [],
            "neighbor_polygons": serialized_neighbors,
            "terrain_summary": terrain_summary,
            "placement": placement,
            "site_source": geodata_context.get("source", "Tomt"),
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
    canvas_w, canvas_h = 900, 620
    margin = 60
    img = Image.new("RGBA", (canvas_w, canvas_h), (6, 17, 26, 255))
    draw = ImageDraw.Draw(img, "RGBA")
    font = ImageFont.load_default()

    site_coords = option.geometry.get("site_polygon_coords") or polygon_to_coords(box(0, 0, site.site_width_m, site.site_depth_m))
    buildable_coords = option.geometry.get("buildable_polygon_coords") or site_coords
    footprint_coords = option.geometry.get("footprint_polygon_coords") or []
    shadow_coords = option.geometry.get("winter_shadow_polygon_coords") or []
    neighbor_polys = option.geometry.get("neighbor_polygons", [])
    terrain_summary = option.geometry.get("terrain_summary", {})

    all_coords = list(site_coords)
    for item in neighbor_polys[:20]:
        all_coords.extend(item.get("coords", []))
    if shadow_coords:
        all_coords.extend(shadow_coords)
    if not all_coords:
        all_coords = [[0.0, 0.0], [site.site_width_m, site.site_depth_m]]

    xs = [pt[0] for pt in all_coords]
    ys = [pt[1] for pt in all_coords]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    width = max(1.0, maxx - minx)
    height = max(1.0, maxy - miny)
    scale = min((canvas_w - (2 * margin)) / width, (canvas_h - (2 * margin)) / height)

    def map_pt(pt: List[float]) -> Tuple[float, float]:
        x, y = pt
        px = margin + ((x - minx) * scale)
        py = canvas_h - margin - ((y - miny) * scale)
        return px, py

    def draw_poly(coords: List[List[float]], fill: Tuple[int, int, int, int], outline: Tuple[int, int, int, int], width_px: int = 2) -> None:
        if not coords:
            return
        pts = [map_pt(pt) for pt in coords]
        if len(pts) < 3:
            return
        draw.polygon(pts, fill=fill, outline=outline)
        if width_px > 1:
            draw.line(pts + [pts[0]], fill=outline, width=width_px)

    draw_poly(site_coords, fill=(13, 24, 36, 255), outline=(130, 151, 178, 255), width_px=3)
    draw_poly(buildable_coords, fill=(56, 189, 248, 28), outline=(56, 189, 248, 230), width_px=2)

    for neighbor in neighbor_polys[:20]:
        draw_poly(neighbor.get("coords", []), fill=(120, 130, 145, 120), outline=(180, 190, 205, 220), width_px=1)

    if shadow_coords:
        draw_poly(shadow_coords, fill=(255, 213, 79, 45), outline=(255, 213, 79, 150), width_px=1)
    draw_poly(footprint_coords, fill=(34, 197, 94, 215), outline=(220, 252, 231, 255), width_px=2)

    arrow_x = canvas_w - 70
    arrow_y = 70
    draw.line((arrow_x, arrow_y + 30, arrow_x, arrow_y - 20), fill=(245, 247, 251, 255), width=4)
    draw.polygon(
        [(arrow_x, arrow_y - 32), (arrow_x - 10, arrow_y - 8), (arrow_x + 10, arrow_y - 8)],
        fill=(245, 247, 251, 255),
    )
    draw.text((arrow_x - 7, arrow_y + 36), "N", fill=(245, 247, 251, 255), font=font)
    draw.text((arrow_x - 30, arrow_y + 52), f"rot {site.north_rotation_deg:.0f}°", fill=(159, 176, 195, 255), font=font)

    placement = option.geometry.get("placement", {})
    draw.text((margin, 16), f"{option.name} | {option.typology}", fill=(245, 247, 251, 255), font=font)
    draw.text(
        (margin, canvas_h - 60),
        f"Tomt via {option.geometry.get('site_source', 'geometri')} | Byggefelt {option.buildable_area_m2:.0f} m2 | Naboer {option.neighbor_count}",
        fill=(200, 211, 223, 255),
        font=font,
    )
    draw.text(
        (margin, canvas_h - 40),
        f"Fotavtrykk {option.footprint_area_m2:.0f} m2 | Høyde {option.building_height_m:.1f} m | Vinterskygge kl 12 ca. {option.winter_noon_shadow_m:.0f} m",
        fill=(200, 211, 223, 255),
        font=font,
    )
    terrain_line = (
        f"Terreng fall {terrain_summary.get('slope_pct', 0):.1f}% | Relieff {terrain_summary.get('relief_m', 0):.1f} m"
        if terrain_summary.get("point_count", 0) > 0
        else f"Plasseringstilpasning {placement.get('fit_scale', 1.0):.2f} | Solbelyst uteareal {option.sunlit_open_space_pct:.0f}%"
    )
    draw.text((margin, canvas_h - 20), terrain_line, fill=(159, 176, 195, 255), font=font)

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
    lines.append("- Nabohøyder fra GeoJSON/OSM må kvalitetssikres dersom de skal brukes beslutningskritisk; OSM-data er ofte ufullstendig.")
    lines.append("- Terrengmodellen er forenklet og bør erstattes med detaljert kotegrunnlag hvis prosjektet går videre til konkret skisse.")
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
    st.markdown("##### Tomtepolygon")
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
- Leser **ekte tomtepolygon** via GeoJSON eller koordinatliste.
- Regner **3 volumalternativer** (lamell, punkthus, tun/U-form) innenfor faktisk byggefelt.
- Leser **nabobebyggelse** via GeoJSON eller OSM og bruker hoyder i sol/skygge.
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

    lat_geocoded, lon_geocoded, geo_source = fetch_lat_lon(pd_state.get("adresse", ""), pd_state.get("kommune", ""))

    site_polygon_input, site_crs, polygon_meta = load_site_polygon_input(site_polygon_upload, site_polygon_text)
    latitude_deg = lat_geocoded if lat_geocoded is not None else polygon_meta.get("centroid_lat", latitude_manual)
    longitude_deg = lon_geocoded if lon_geocoded is not None else polygon_meta.get("centroid_lon")

    neighbor_inputs: List[Dict[str, Any]] = []
    neighbor_meta: Dict[str, Any] = {"source": "Ingen naboer"}
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

    terrain_ctx, terrain_meta = load_terrain_input(terrain_upload, site_polygon_input, site_crs)

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

    with st.spinner("Regner volumalternativer med faktisk tomtepolygon, naboer og terreng ..."):
        options = generate_options(site, mix_inputs, geodata_context=geodata_context)

    if not options:
        st.error("Klarte ikke å generere alternativer. Kontroller tomtepolygon, byggegrenser og BYA.")
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
                    "polygon_meta": polygon_meta,
                    "neighbor_meta": neighbor_meta,
                    "terrain_meta": terrain_meta,
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
    }
    st.session_state.generated_ark_pdf = pdf_bytes
    st.session_state.generated_ark_filename = f"Builtly_ARK_{p_name}_v3.pdf"
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

