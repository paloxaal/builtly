from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import pandas as pd

ADDRESS_API_URL = os.getenv("BUILTLY_ADDRESS_API_URL", "https://ws.geonorge.no/adresser/v1/sok")
NVE_FLOOD_SERVICE_URL = os.getenv("BUILTLY_NVE_FLOOD_SERVICE_URL", "https://nve.geodataonline.no/arcgis/rest/services/FlomAktsomhet/MapServer")
NVE_LANDSLIDE_SERVICE_URL = os.getenv("BUILTLY_NVE_LANDSLIDE_SERVICE_URL", "https://kart.nve.no/enterprise/rest/services/JordFlomskredAktsomhet/MapServer")
PLAN_API_URL = os.getenv("BUILTLY_PLAN_API_URL", "")
PLAN_API_KEY = os.getenv("BUILTLY_PLAN_API_KEY", "")
MATRIKKEL_PROXY_URL = os.getenv("BUILTLY_MATRIKKEL_PROXY_URL", "")
MATRIKKEL_PROXY_TOKEN = os.getenv("BUILTLY_MATRIKKEL_PROXY_TOKEN", "")
ENERGY_PROXY_URL = os.getenv("BUILTLY_ENERGY_PROXY_URL", "")
ENERGY_PROXY_TOKEN = os.getenv("BUILTLY_ENERGY_PROXY_TOKEN", "")
PREINDEXED_SNAPSHOT_PATH = os.getenv("BUILTLY_PUBLIC_SNAPSHOT_PATH", "")
CLIMATE_SNAPSHOT_PATH = os.getenv("BUILTLY_CLIMATE_SNAPSHOT_PATH", PREINDEXED_SNAPSHOT_PATH)
STATIC_MAP_URL = os.getenv("BUILTLY_STATIC_MAP_URL", "")


def _http_request_json(
    url: str,
    *,
    params: Optional[Dict] = None,
    payload: Optional[Dict] = None,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
) -> Tuple[Optional[Dict], Optional[str]]:
    if not url:
        return None, "Ingen URL konfigurert"

    query_url = url
    if params:
        encoded = urlparse.urlencode({k: v for k, v in params.items() if v is not None})
        sep = "&" if "?" in url else "?"
        query_url = f"{url}{sep}{encoded}"

    req_headers = {"Accept": "application/json", **(headers or {})}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urlrequest.Request(query_url, data=data, headers=req_headers, method=method.upper())
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body), None
    except urlerror.HTTPError as exc:
        try:
            details = exc.read().decode("utf-8")
        except Exception:
            details = str(exc)
        return None, f"HTTP {exc.code}: {details}"
    except urlerror.URLError as exc:
        return None, f"Tilkoblingsfeil: {exc}"
    except Exception as exc:
        return None, str(exc)


def _extract_coordinates(item: Dict) -> Tuple[Optional[float], Optional[float]]:
    if not isinstance(item, dict):
        return None, None
    representasjonspunkt = item.get("representasjonspunkt") or item.get("point") or {}
    for lat_key, lon_key in [("lat", "lon"), ("y", "x")]:
        lat = representasjonspunkt.get(lat_key)
        lon = representasjonspunkt.get(lon_key)
        if lat is not None and lon is not None:
            try:
                return float(lat), float(lon)
            except Exception:
                pass
    geometry = item.get("geometry") or {}
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if isinstance(coords, (list, tuple)) and len(coords) >= 2:
        try:
            return float(coords[1]), float(coords[0])
        except Exception:
            pass
    for lat_key, lon_key in [("lat", "lon"), ("latitude", "longitude")]:
        if item.get(lat_key) is not None and item.get(lon_key) is not None:
            try:
                return float(item[lat_key]), float(item[lon_key])
            except Exception:
                pass
    return None, None


def geocode_address(address: str, municipality: str = "") -> Dict:
    search = " ".join(part for part in [str(address).strip(), str(municipality).strip()] if part).strip()
    if not search:
        return {"status": "missing", "note": "Adresse mangler", "source": "Kartverket Adresse API"}
    data, err = _http_request_json(ADDRESS_API_URL, params={"sok": search, "treffPerSide": 5, "side": 0}, timeout=15)
    if err:
        return {"status": "error", "note": err, "source": "Kartverket Adresse API", "query": search}
    candidates = []
    if isinstance(data, dict):
        candidates = data.get("adresser") or data.get("items") or data.get("results") or []
    elif isinstance(data, list):
        candidates = data
    if not candidates:
        return {"status": "missing", "note": "Ingen treff", "source": "Kartverket Adresse API", "query": search}
    first = candidates[0] if isinstance(candidates[0], dict) else {}
    lat, lon = _extract_coordinates(first)
    return {
        "status": "ok" if lat is not None and lon is not None else "partial",
        "source": "Kartverket Adresse API",
        "query": search,
        "resolved_text": first.get("adressetekst") or first.get("fullAddress") or search,
        "municipality": first.get("kommunenavn") or municipality,
        "lat": lat,
        "lon": lon,
        "raw": first,
    }


def _discover_arcgis_layer_id(service_url: str) -> Tuple[Optional[int], Optional[str], Optional[Dict]]:
    info, err = _http_request_json(service_url, params={"f": "pjson"}, timeout=15)
    if err:
        return None, err, None
    layer_id = None
    layers = info.get("layers") if isinstance(info, dict) else None
    if isinstance(layers, list) and layers:
        first = layers[0]
        if isinstance(first, dict) and first.get("id") is not None:
            layer_id = int(first["id"])
    if layer_id is None and service_url.rstrip("/").split("/")[-1].isdigit():
        layer_id = int(service_url.rstrip("/").split("/")[-1])
    if layer_id is None:
        layer_id = 0
    return layer_id, None, info if isinstance(info, dict) else {}


def query_arcgis_point(service_url: str, lat: float, lon: float, *, timeout: int = 18) -> Dict:
    layer_id, discover_err, info = _discover_arcgis_layer_id(service_url)
    if discover_err:
        return {"status": "error", "note": discover_err, "source": service_url}
    query_url = service_url.rstrip("/")
    if not query_url.endswith(f"/{layer_id}"):
        query_url = f"{query_url}/{layer_id}"
    query_url = f"{query_url}/query"
    params = {
        "f": "json",
        "where": "1=1",
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "false",
        "outFields": "*",
    }
    data, err = _http_request_json(query_url, params=params, timeout=timeout)
    if err:
        return {"status": "error", "note": err, "source": query_url, "layer_id": layer_id}
    features = data.get("features") if isinstance(data, dict) else None
    attrs: List[Dict] = []
    if isinstance(features, list):
        for feature in features[:5]:
            if isinstance(feature, dict):
                attrs.append(feature.get("attributes") or feature.get("properties") or {})
    return {
        "status": "ok",
        "source": query_url,
        "layer_id": layer_id,
        "feature_count": len(features or []),
        "attributes": attrs,
        "service_name": (info or {}).get("mapName") or (info or {}).get("name") or service_url.split("/")[-1],
    }


def _load_snapshot_table(path_str: str) -> pd.DataFrame:
    path = Path(path_str)
    if not path_str or not path.exists() or not path.is_file():
        return pd.DataFrame()
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload = payload.get("rows") or payload.get("items") or payload.get("data") or []
            return pd.DataFrame(payload)
        if path.suffix.lower() in {".csv", ".txt"}:
            return pd.read_csv(path)
        if path.suffix.lower() in {".xlsx", ".xls"}:
            return pd.read_excel(path)
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()


def _match_snapshot_row(df: pd.DataFrame, *, municipality: str = "", address: str = "", asset_id: str = "", matrikkel_id: str = "") -> Dict:
    if df.empty:
        return {}
    working = df.copy()
    working.columns = [str(col).strip().lower() for col in working.columns]
    matches = [
        (municipality, ["municipality", "kommune"]),
        (address, ["address", "adresse"]),
        (asset_id, ["asset_id", "eiendom_id", "id"]),
        (matrikkel_id, ["matrikkel_id", "matrikkelnummer"]),
    ]
    for value, cols in matches:
        if not value:
            continue
        for col in cols:
            if col in working.columns:
                matched = working[working[col].astype(str).str.lower() == str(value).lower()]
                if not matched.empty:
                    return matched.iloc[0].to_dict()
    return {}


def _custom_proxy_fetch(url: str, token: str, payload: Dict) -> Dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data, err = _http_request_json(url, payload=payload, method="POST", headers=headers, timeout=20)
    if err:
        return {"status": "error", "note": err, "source": url}
    return {"status": "ok", "source": url, "payload": data}


def _plan_lookup(lat: Optional[float], lon: Optional[float], municipality: str) -> Dict:
    if not PLAN_API_URL:
        return {"status": "manual", "note": "Plan-API er ikke konfigurert", "source": "DiBK / Fellestjenester plan og bygg"}
    if lat is None or lon is None:
        return {"status": "missing", "note": "Koordinater mangler for planoppslag", "source": PLAN_API_URL}
    bbox_size = 0.0008
    params = {"bbox": f"{lon-bbox_size},{lat-bbox_size},{lon+bbox_size},{lat+bbox_size}", "limit": 10}
    headers = {"X-Api-Key": PLAN_API_KEY} if PLAN_API_KEY else {}
    data, err = _http_request_json(PLAN_API_URL, params=params, headers=headers, timeout=20)
    if err:
        return {"status": "error", "note": err, "source": PLAN_API_URL}
    features = []
    if isinstance(data, dict):
        features = data.get("features") or data.get("items") or []
    names: List[str] = []
    for feature in features[:5]:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or feature
        name = props.get("planNavn") or props.get("plannavn") or props.get("name") or props.get("plan_name")
        if name:
            names.append(str(name))
    return {"status": "ok", "source": PLAN_API_URL, "feature_count": len(features), "plans": names, "municipality": municipality}


def _numeric(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def build_static_map_url(lat: Optional[float], lon: Optional[float]) -> str:
    if lat is None or lon is None:
        return ""
    if STATIC_MAP_URL:
        return STATIC_MAP_URL.format(lat=lat, lon=lon)
    return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}"


def compute_climate_scores(
    *,
    flood_hit: bool,
    landslide_hit: bool,
    elevation_m: float,
    distance_coast_km: float,
    heat_index: float,
    scenario: str,
    horizon: str,
    manual_overrides: Optional[Dict] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Dict:
    manual_overrides = manual_overrides or {}
    weights = weights or {"flood": 0.35, "landslide": 0.25, "sea_level": 0.25, "heat_stress": 0.15}

    flood_score = manual_overrides.get("flood_score")
    if flood_score is None:
        flood_score = 4.6 if flood_hit else 1.6
        flood_score += max(0.0, 0.5 - manual_overrides.get("distance_river_km", 0.6)) * 1.8
        flood_score -= elevation_m / 120.0
    flood_score = max(1.0, min(5.0, float(flood_score)))

    landslide_score = manual_overrides.get("landslide_score")
    if landslide_score is None:
        slope_deg = float(manual_overrides.get("slope_deg", 8.0))
        soil_modifier = {
            "leire": 0.7,
            "marine avsetninger": 0.8,
            "fyllmasser": 0.5,
            "morene": 0.1,
            "berg": -0.3,
        }.get(str(manual_overrides.get("soil_type", "morene")).strip().lower(), 0.0)
        landslide_score = (4.2 if landslide_hit else 1.5) + slope_deg / 20.0 + soil_modifier
    landslide_score = max(1.0, min(5.0, float(landslide_score)))

    sea_score = manual_overrides.get("sea_level_score")
    if sea_score is None:
        climate_modifier = 0.35 if "8.5" in scenario or "85" in scenario.upper() else 0.1
        sea_score = 4.3 - elevation_m / 22.0 - distance_coast_km / 1.8 + climate_modifier
    sea_score = max(1.0, min(5.0, float(sea_score)))

    heat_score = manual_overrides.get("heat_stress_score")
    if heat_score is None:
        horizon_modifier = 0.35 if str(horizon) == "2100" else 0.18 if str(horizon) == "2050" else 0.05
        heat_score = 1.4 + heat_index / 2.1 + horizon_modifier
    heat_score = max(1.0, min(5.0, float(heat_score)))

    aggregate_score = round(
        flood_score * float(weights.get("flood", 0.35))
        + landslide_score * float(weights.get("landslide", 0.25))
        + sea_score * float(weights.get("sea_level", 0.25))
        + heat_score * float(weights.get("heat_stress", 0.15)),
        2,
    )
    uncertainty = round(max(0.25, 1.1 - (0.2 if flood_hit else 0.0) - (0.15 if landslide_hit else 0.0)), 2)
    return {
        "flood_score": round(flood_score, 2),
        "landslide_score": round(landslide_score, 2),
        "sea_level_score": round(sea_score, 2),
        "heat_stress_score": round(heat_score, 2),
        "aggregate_score": aggregate_score,
        "uncertainty_interval": uncertainty,
    }


def climate_regulatory_outputs(snapshot: Dict) -> List[Dict]:
    score = float(snapshot.get("aggregate_score", 0.0) or 0.0)
    confidence = max(0.0, 1.0 - float(snapshot.get("uncertainty_interval", 0.5) or 0.5) / 2.0)
    status = "ready" if confidence >= 0.55 else "partial"
    return [
        {"framework": "EU Taxonomy", "status": status, "note": f"DNSH mapping klar med klimarisikoscore {score}"},
        {"framework": "SFDR", "status": status, "note": "Fysiske klimarisikofaktorer kan eksporteres videre i maskinlesbart format."},
        {"framework": "ECB", "status": "ready" if score > 0 else "needs_review", "note": "Felter for portefoljescreening og batch-kjoring er fylt ut i preview."},
        {"framework": "Finanstilsynet", "status": status, "note": "Kan brukes som underlag i intern klimarisikorapportering."},
    ]


def gather_climate_snapshot(asset: Dict, *, scenario: str = "RCP 4.5", horizon: str = "2050", weights: Optional[Dict[str, float]] = None) -> Dict:
    address = str(asset.get("address") or asset.get("adresse") or "").strip()
    municipality = str(asset.get("municipality") or asset.get("kommune") or "").strip()
    asset_id = str(asset.get("asset_id") or asset.get("id") or "").strip()
    lat = asset.get("lat")
    lon = asset.get("lon")
    geocode = None
    if lat is None or lon is None:
        geocode = geocode_address(address, municipality)
        lat = geocode.get("lat")
        lon = geocode.get("lon")

    source_rows: List[Dict] = []
    if geocode:
        source_rows.append({"source": geocode.get("source"), "status": geocode.get("status"), "note": geocode.get("note") or geocode.get("resolved_text") or "", "version": "live"})

    flood_data = {"status": "missing", "note": "Koordinater mangler", "source": NVE_FLOOD_SERVICE_URL}
    landslide_data = {"status": "missing", "note": "Koordinater mangler", "source": NVE_LANDSLIDE_SERVICE_URL}
    if lat is not None and lon is not None:
        flood_data = query_arcgis_point(NVE_FLOOD_SERVICE_URL, float(lat), float(lon))
        landslide_data = query_arcgis_point(NVE_LANDSLIDE_SERVICE_URL, float(lat), float(lon))
    source_rows.append({"source": "NVE flom", "status": flood_data.get("status"), "note": f"Treff: {flood_data.get('feature_count', 0)}" if flood_data.get("status") == "ok" else flood_data.get("note", ""), "version": "live"})
    source_rows.append({"source": "NVE skred", "status": landslide_data.get("status"), "note": f"Treff: {landslide_data.get('feature_count', 0)}" if landslide_data.get("status") == "ok" else landslide_data.get("note", ""), "version": "live"})

    climate_df = _load_snapshot_table(CLIMATE_SNAPSHOT_PATH)
    snapshot_row = _match_snapshot_row(climate_df, municipality=municipality, address=address, asset_id=asset_id)
    if snapshot_row:
        source_rows.append({"source": str(snapshot_row.get("source") or "Pre-indeksert klimadatasett"), "status": "ok", "note": "Treff i lokal snapshot", "version": str(snapshot_row.get("version") or "snapshot")})

    elevation_m = _numeric(asset.get("elevation_m") or snapshot_row.get("elevation_m") or snapshot_row.get("elevation") or 12.0)
    distance_coast_km = _numeric(asset.get("distance_coast_km") or snapshot_row.get("distance_coast_km") or 1.5)
    heat_index = _numeric(asset.get("heat_index") or snapshot_row.get("heat_index") or snapshot_row.get("urban_heat") or 5.0)

    scores = compute_climate_scores(
        flood_hit=bool(flood_data.get("feature_count")),
        landslide_hit=bool(landslide_data.get("feature_count")),
        elevation_m=elevation_m,
        distance_coast_km=distance_coast_km,
        heat_index=heat_index,
        scenario=scenario,
        horizon=horizon,
        manual_overrides=asset,
        weights=weights,
    )
    result = {
        "asset_id": asset_id,
        "address": address,
        "municipality": municipality,
        "lat": lat,
        "lon": lon,
        "scenario": scenario,
        "horizon": horizon,
        "snapshot_row": snapshot_row,
        "flood": flood_data,
        "landslide": landslide_data,
        "source_rows": source_rows,
        "map_url": build_static_map_url(lat, lon),
        **scores,
    }
    result["regulatory_outputs"] = climate_regulatory_outputs(result)
    return result


def adapter_status() -> List[Dict]:
    return [
        {"source": "Kartverket Adresse API", "configured": True, "endpoint": ADDRESS_API_URL, "note": "Apen geokoding / adressesok"},
        {"source": "NVE flom", "configured": True, "endpoint": NVE_FLOOD_SERVICE_URL, "note": "GIS lag for flom"},
        {"source": "NVE skred", "configured": True, "endpoint": NVE_LANDSLIDE_SERVICE_URL, "note": "GIS lag for skred og jord/flomskred"},
        {"source": "DiBK plan / Fellestjenester plan og bygg", "configured": bool(PLAN_API_URL), "endpoint": PLAN_API_URL or "sett BUILTLY_PLAN_API_URL", "note": "Planoppslag med app key ved behov"},
        {"source": "Kartverket Matrikkel proxy", "configured": bool(MATRIKKEL_PROXY_URL), "endpoint": MATRIKKEL_PROXY_URL or "sett BUILTLY_MATRIKKEL_PROXY_URL", "note": "Proxy for matrikkel og gnr/bnr-data"},
        {"source": "Energi / EPC proxy", "configured": bool(ENERGY_PROXY_URL), "endpoint": ENERGY_PROXY_URL or "sett BUILTLY_ENERGY_PROXY_URL", "note": "Proxy for energimerke / EPC / Enova-liknende kilder"},
        {"source": "Lokal snapshot", "configured": bool(PREINDEXED_SNAPSHOT_PATH), "endpoint": PREINDEXED_SNAPSHOT_PATH or "ikke satt", "note": "Pre-indeksert cache for batch og rask respons"},
    ]


def gather_tdd_public_snapshot(property_input: Dict) -> Dict:
    address = str(property_input.get("address") or property_input.get("adresse") or "").strip()
    municipality = str(property_input.get("municipality") or property_input.get("kommune") or "").strip()
    gnr = str(property_input.get("gnr") or "").strip()
    bnr = str(property_input.get("bnr") or "").strip()
    matrikkel_id = str(property_input.get("matrikkel_id") or property_input.get("matrikkelnummer") or "").strip()
    asset_id = str(property_input.get("asset_id") or property_input.get("id") or "").strip()

    geocode = geocode_address(address, municipality) if address else {"status": "missing", "note": "Adresse mangler", "source": "Kartverket Adresse API"}
    lat = property_input.get("lat") or geocode.get("lat")
    lon = property_input.get("lon") or geocode.get("lon")

    rows: List[Dict] = [{"source": geocode.get("source"), "status": geocode.get("status"), "note": geocode.get("note") or geocode.get("resolved_text") or "", "version": "live"}]

    plan_result = _plan_lookup(float(lat), float(lon), municipality) if lat is not None and lon is not None else {"status": "missing", "note": "Koordinater mangler", "source": "DiBK / Fellestjenester plan og bygg"}
    rows.append({"source": "DiBK plan / NAP", "status": plan_result.get("status"), "note": ", ".join(plan_result.get("plans", [])[:3]) or plan_result.get("note", ""), "version": "live" if plan_result.get("status") == "ok" else "n/a"})

    property_payload = {"address": address, "municipality": municipality, "gnr": gnr, "bnr": bnr, "matrikkel_id": matrikkel_id, "asset_id": asset_id, "lat": lat, "lon": lon}
    matrikkel_result = _custom_proxy_fetch(MATRIKKEL_PROXY_URL, MATRIKKEL_PROXY_TOKEN, property_payload) if MATRIKKEL_PROXY_URL else {"status": "manual", "note": "Matrikkel-proxy ikke konfigurert", "source": "Kartverket Matrikkel"}
    rows.append({"source": "Kartverket Matrikkel", "status": matrikkel_result.get("status"), "note": matrikkel_result.get("note") or ("Proxy-svar mottatt" if matrikkel_result.get("status") == "ok" else ""), "version": "proxy"})

    energy_result = _custom_proxy_fetch(ENERGY_PROXY_URL, ENERGY_PROXY_TOKEN, property_payload) if ENERGY_PROXY_URL else {"status": "manual", "note": "Energidata-proxy ikke konfigurert", "source": "Energidata"}
    rows.append({"source": "Energi / EPC", "status": energy_result.get("status"), "note": energy_result.get("note") or ("Proxy-svar mottatt" if energy_result.get("status") == "ok" else ""), "version": "proxy"})

    flood_result = query_arcgis_point(NVE_FLOOD_SERVICE_URL, float(lat), float(lon)) if lat is not None and lon is not None else {"status": "missing", "note": "Koordinater mangler", "source": NVE_FLOOD_SERVICE_URL}
    rows.append({"source": "NVE flom", "status": flood_result.get("status"), "note": f"Treff: {flood_result.get('feature_count', 0)}" if flood_result.get("status") == "ok" else flood_result.get("note", ""), "version": "live"})

    snapshot_df = _load_snapshot_table(PREINDEXED_SNAPSHOT_PATH)
    snapshot_row = _match_snapshot_row(snapshot_df, municipality=municipality, address=address, asset_id=asset_id, matrikkel_id=matrikkel_id)
    if snapshot_row:
        rows.append({"source": str(snapshot_row.get("source") or "Lokal snapshot"), "status": "ok", "note": "Treff i lokal snapshot", "version": str(snapshot_row.get("version") or "snapshot")})

    resolved = {
        "address": address,
        "municipality": municipality,
        "gnr": gnr,
        "bnr": bnr,
        "matrikkel_id": matrikkel_id,
        "lat": lat,
        "lon": lon,
        "geocode": geocode,
        "plan": plan_result,
        "matrikkel": matrikkel_result,
        "energy": energy_result,
        "flood": flood_result,
        "snapshot_row": snapshot_row,
        "map_url": build_static_map_url(lat, lon),
    }
    return {"rows": rows, "resolved": resolved}


def _score_to_class(score: float) -> str:
    if score >= 3.6:
        return "HOY"
    if score >= 2.2:
        return "MIDDELS"
    return "LAV"


def _short_hash(payload: Dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def run_climate_portfolio_batch(properties: Sequence[Dict], *, partner_id: str = "", scenario: str = "RCP 4.5", horizon: str = "2050", weights: Optional[Dict[str, float]] = None) -> Dict:
    rows: List[Dict] = []
    for item in properties:
        snap = gather_climate_snapshot(item, scenario=scenario, horizon=horizon, weights=weights)
        rows.append({
            "id": item.get("id") or item.get("asset_id") or item.get("address") or "ukjent",
            "address": snap.get("address") or item.get("address") or "",
            "aggregate_score": snap.get("aggregate_score"),
            "class": _score_to_class(float(snap.get("aggregate_score", 0.0) or 0.0)),
            "uncertainty_interval": snap.get("uncertainty_interval"),
            "report_url": f"/api/v1/climate-risk/{item.get('id') or item.get('asset_id') or 'asset'}",
        })
    batch_body = {"partner_id": partner_id, "count": len(rows), "scenario": scenario, "horizon": horizon}
    batch_id = f"clm_{_short_hash(batch_body)}"
    return {
        "batch_id": batch_id,
        "estimated_completion_hours": 24 if len(rows) >= 1000 else 4,
        "webhook_preview": {
            "event": "climate_risk.batch.completed",
            "batch_id": batch_id,
            "partner_id": partner_id,
            "retry_policy": "exponential_backoff",
        },
        "properties": rows,
    }


def run_tdd_portfolio_batch(properties: Sequence[Dict], *, partner_id: str = "") -> Dict:
    rows: List[Dict] = []
    for item in properties:
        snap = gather_tdd_public_snapshot(item)
        completeness = sum(1 for row in snap.get("rows", []) if row.get("status") in {"ok", "partial"}) / max(1, len(snap.get("rows", [])))
        score = 1.4 + (1.0 - completeness) * 2.2
        plan_risk = 0.5 if snap["resolved"].get("plan", {}).get("status") not in {"ok", "partial"} else 0.0
        score += plan_risk
        overall = _score_to_class(score)
        remediation = int(250000 + score * 450000)
        rows.append({
            "matrikkel_id": item.get("matrikkel_id") or item.get("matrikkelnummer") or item.get("gnr") or "ukjent",
            "label": item.get("label") or item.get("address") or "Eiendom",
            "overall_class": overall,
            "remediation_cost_total": remediation,
            "report_url": f"/api/v1/tdd/{item.get('matrikkel_id') or item.get('label') or 'asset'}",
            "data_status": "manglende data" if completeness < 0.35 else "ok",
        })
    batch_body = {"partner_id": partner_id, "count": len(rows)}
    batch_id = f"tdd_{_short_hash(batch_body)}"
    return {
        "batch_id": batch_id,
        "estimated_completion_hours": 24 if len(rows) >= 1000 else 6,
        "webhook_preview": {
            "event": "tdd.batch.completed",
            "batch_id": batch_id,
            "partner_id": partner_id,
            "retry_policy": "exponential_backoff",
        },
        "properties": rows,
    }
