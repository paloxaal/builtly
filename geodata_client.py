from __future__ import annotations

"""
Geodata Online client for Builtly feasibility studies.

This module keeps the original parcel/building/imagery functionality, but also
adds generic ArcGIS REST helpers so the architecture module can pull stronger
site context from Geodata:
- plan / regulation context
- development activity context
- transport / access context
- geology context
- terrain samples from ImageServer
- service catalog for ArcGIS JS 3D scenes
"""

import io
import json
import math
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from PIL import Image

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore[assignment]

try:
    from shapely.geometry import LineString, MultiLineString, Point, Polygon
    from shapely.ops import unary_union

    HAS_SHAPELY = True
except Exception:  # pragma: no cover
    HAS_SHAPELY = False
    LineString = MultiLineString = Point = Polygon = None  # type: ignore[assignment]
    unary_union = None  # type: ignore[assignment]


TOKEN_URL = "https://services.geodataonline.no/arcgis/tokens/generateToken"
REST_BASE = "https://services.geodataonline.no/arcgis/rest/services"
PROXY_BASE = f"{REST_BASE}/ProxyServices"

# Core services already used by the app
MATRIKKEL_FS = "Geomap_UTM33_EUREF89/GeomapMatrikkel/FeatureServer"
MATRIKKEL_MS = "Geomap_UTM33_EUREF89/GeomapMatrikkel/MapServer"
LAYER_DEKTEIGFLATE = 5
LAYER_BYGGFLATE = 9

# Basemap / imagery
GEOCACHE_BILDER_MS = "Geocache_UTM33_EUREF89/GeocacheBilder/MapServer"
GEOCACHE_LANDSKAP_MS = "Geocache_UTM33_EUREF89/GeocacheLandskap/MapServer"
GEOCACHE_TERRENGSKYGGE_MS = "Geocache_UTM33_EUREF89/GeocacheTerrengskygge/MapServer"
GEOCACHE_TERRENG_IS = "Geocache_UTM33_EUREF89/GeocacheTerreng/ImageServer"
GEOMAP_BILDER_NYESTE_IS = "Geomap_UTM33_EUREF89/GeomapBilderNyeste/ImageServer"

# Thematic / analysis services (VERIFIED against bolignorge2 account 2026-01-24)
GEOMAP_DOKPLAN_MS = "Geomap_UTM33_EUREF89/GeomapDOKPlan/MapServer"
GEOMAP_REGULERINGSPLAN_MS = "Geomap_UTM33_EUREF89/GeomapDOKPlan/MapServer"  # NB: GeomapReguleringsplan finnes IKKE - bruk DOKPlan
GEOMAP_UTBYGGER_MS = "Geomap_UTM33_EUREF89/GeomapDOKAnnen/MapServer"  # NB: GeomapUtbygger finnes IKKE - bruk DOKAnnen som fallback
GEOMAP_SAMFERDSEL_MS = "Geomap_UTM33_EUREF89/GeomapDOKSamferdsel/MapServer"  # NB: het GeomapDOKSamferdsel, ikke GeomapSamferdsel
GEOMAP_DOKGEOLOGI_MS = "Geomap_UTM33_EUREF89/GeomapDOKGeologi/MapServer"
GEOMAP_DTM_IS = "Geocache_UTM33_EUREF89/GeocacheTerreng/ImageServer"  # NB: GeomapDTM finnes IKKE - bruk GeocacheTerreng

ADDRESS_GEOCODER = f"{PROXY_BASE}/Adresse/GeocodeServer"
DEFAULT_FLOOR_HEIGHT = 3.0
DEFAULT_SRID = 25833


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip().replace(" ", "").replace(",", ".")
        return float(text)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _clean_name(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = text.replace("oe", "o").replace("ae", "a")
    return text.lower()


def _expanded_bbox(bounds: Tuple[float, float, float, float], buffer_m: float) -> Tuple[float, float, float, float]:
    minx, miny, maxx, maxy = bounds
    return (minx - buffer_m, miny - buffer_m, maxx + buffer_m, maxy + buffer_m)


def _bbox_string(bounds: Tuple[float, float, float, float]) -> str:
    minx, miny, maxx, maxy = bounds
    return f"{minx},{miny},{maxx},{maxy}"


def _iter_grid_points(poly: Any, spacing_m: float = 12.0, max_points: int = 220) -> List[Tuple[float, float]]:
    if not HAS_SHAPELY or poly is None or poly.is_empty:
        return []
    spacing_m = max(2.0, float(spacing_m))
    minx, miny, maxx, maxy = poly.bounds
    pts: List[Tuple[float, float]] = []
    x = minx + (spacing_m / 2.0)
    while x < maxx:
        y = miny + (spacing_m / 2.0)
        while y < maxy:
            pt = Point(x, y)
            if poly.contains(pt):
                pts.append((float(x), float(y)))
            y += spacing_m
        x += spacing_m
    if not pts:
        rp = poly.representative_point()
        pts = [(float(rp.x), float(rp.y))]
    if len(pts) > max_points:
        if np is not None:
            idx = np.linspace(0, len(pts) - 1, max_points).astype(int)
            pts = [pts[i] for i in idx]
        else:
            step = max(1, int(math.ceil(len(pts) / max_points)))
            pts = pts[::step][:max_points]
    return pts


def _geometry_from_arcgis(geometry: Dict[str, Any]) -> Any:
    if not HAS_SHAPELY or not geometry:
        return None
    try:
        if "rings" in geometry:
            polys = []
            for ring in geometry.get("rings") or []:
                if not ring or len(ring) < 4:
                    continue
                poly = Polygon(ring)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if not poly.is_empty:
                    polys.append(poly)
            if not polys:
                return None
            merged = unary_union(polys).buffer(0)
            return merged if not merged.is_empty else None
        if "paths" in geometry:
            lines = []
            for path in geometry.get("paths") or []:
                if path and len(path) >= 2:
                    lines.append(LineString(path))
            if not lines:
                return None
            if len(lines) == 1:
                return lines[0]
            return MultiLineString(lines)
        if "x" in geometry and "y" in geometry:
            return Point(float(geometry["x"]), float(geometry["y"]))
    except Exception:
        return None
    return None


class GeodataOnlineClient:
    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        self.username = username or os.environ.get("GEODATA_ONLINE_USER", "")
        self.password = password or os.environ.get("GEODATA_ONLINE_PASS", "")
        self._token = ""
        self._token_expires = 0
        self._meta_cache: Dict[str, Dict[str, Any]] = {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "BuiltlyFeasibility/2.0"})

    def is_available(self) -> bool:
        return bool(self.username and self.password)

    def get_token(self, force_refresh: bool = False) -> str:
        if not force_refresh and self._token and time.time() * 1000 < (self._token_expires - 300_000):
            return self._token
        if not self.is_available():
            raise RuntimeError("GEODATA_ONLINE_USER / GEODATA_ONLINE_PASS mangler")

        resp = self.session.post(
            TOKEN_URL,
            data={
                "username": self.username,
                "password": self.password,
                "client": "requestip",
                "expiration": "1440",
                "f": "json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Token feilet: {data['error'].get('message', data['error'])}")
        self._token = data.get("token", "")
        self._token_expires = int(data.get("expires", 0))
        if not self._token:
            raise RuntimeError("Geodata returnerte ikke token")
        return self._token

    def token_status(self) -> Dict[str, Any]:
        remaining = max(0.0, (self._token_expires / 1000.0) - time.time())
        return {"valid": bool(self._token) and remaining > 0.0, "remaining_minutes": round(remaining / 60.0, 1)}

    def _service_url(self, service: str, suffix: str = "") -> str:
        url = f"{REST_BASE}/{service}"
        if suffix:
            return f"{url}/{suffix.lstrip('/')}"
        return url

    def _arcgis_json(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        method: str = "get",
        timeout: int = 20,
        retry_on_auth: bool = True,
    ) -> Dict[str, Any]:
        payload = dict(params or data or {})
        payload.setdefault("f", "json")
        if url != TOKEN_URL:
            payload.setdefault("token", self.get_token())

        if method.lower() == "post":
            resp = self.session.post(url, data=payload, timeout=timeout)
        else:
            resp = self.session.get(url, params=payload, timeout=timeout)
        resp.raise_for_status()
        result = resp.json()

        if "error" in result:
            code = _safe_int((result.get("error") or {}).get("code"), 0)
            if code in (498, 499) and retry_on_auth and url != TOKEN_URL:
                self.get_token(force_refresh=True)
                return self._arcgis_json(url, params=params, data=data, method=method, timeout=timeout, retry_on_auth=False)
            raise RuntimeError((result.get("error") or {}).get("message", "ArcGIS-feil"))

        return result

    def _image_export(
        self,
        service: str,
        bounds: Tuple[float, float, float, float],
        *,
        srid: int = DEFAULT_SRID,
        width: int = 1200,
        height: int = 1200,
    ) -> Optional[bytes]:
        if service.endswith("/ImageServer"):
            endpoint = self._service_url(service, "exportImage")
        else:
            endpoint = self._service_url(service, "export")
        resp = self.session.get(
            endpoint,
            params={
                "bbox": _bbox_string(bounds),
                "bboxSR": str(srid),
                "imageSR": str(srid),
                "size": f"{int(width)},{int(height)}",
                "format": "png",
                "transparent": "false",
                "f": "image",
                "token": self.get_token(),
            },
            timeout=20,
        )
        if resp.status_code != 200:
            return None
        content = resp.content or b""
        if len(content) < 1000:
            return None
        ctype = resp.headers.get("Content-Type", "")
        if content[:4] in (b"\x89PNG", b"\xff\xd8\xff") or "image" in ctype:
            return content
        return None

    def discover_services(self, folder: str = "") -> Dict[str, Any]:
        url = f"{REST_BASE}/{folder}" if folder else REST_BASE
        return self._arcgis_json(url, params={})

    def service_metadata(self, service: str, force_refresh: bool = False) -> Dict[str, Any]:
        if not force_refresh and service in self._meta_cache:
            return self._meta_cache[service]
        meta = self._arcgis_json(self._service_url(service), params={})
        self._meta_cache[service] = meta
        return meta

    def list_layers(self, service: str, leaf_only: bool = True) -> List[Dict[str, Any]]:
        meta = self.service_metadata(service)
        layers = list(meta.get("layers") or [])
        if not leaf_only:
            return layers
        return [layer for layer in layers if not layer.get("subLayerIds")]

    def find_layers_by_name(self, service: str, preferred_terms: Sequence[str]) -> List[Dict[str, Any]]:
        terms = [_clean_name(term) for term in preferred_terms if str(term).strip()]
        matches = []
        for layer in self.list_layers(service, leaf_only=True):
            cleaned = _clean_name(layer.get("name"))
            if any(term in cleaned for term in terms):
                matches.append(layer)
        return matches

    def _query_layer(self, service: str, layer_id: int, **kwargs: Any) -> Dict[str, Any]:
        return self._arcgis_json(self._service_url(service, f"{layer_id}/query"), params=kwargs)

    def _query_layer_in_bbox(
        self,
        service: str,
        layer_id: int,
        bounds: Tuple[float, float, float, float],
        *,
        out_fields: str = "*",
        max_records: int = 200,
    ) -> Dict[str, Any]:
        return self._query_layer(
            service,
            layer_id,
            where="1=1",
            geometry=_bbox_string(bounds),
            geometryType="esriGeometryEnvelope",
            spatialRel="esriSpatialRelIntersects",
            inSR=str(DEFAULT_SRID),
            outSR=str(DEFAULT_SRID),
            outFields=out_fields,
            returnGeometry="true",
            resultRecordCount=str(max_records),
        )

    def _serialize_features(
        self,
        service: str,
        layer: Dict[str, Any],
        raw_features: Iterable[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        layer_id = int(layer.get("id", -1))
        layer_name = str(layer.get("name", f"Lag {layer_id}"))
        out: List[Dict[str, Any]] = []
        for feature in raw_features:
            attrs = dict(feature.get("attributes") or {})
            geom = _geometry_from_arcgis(feature.get("geometry") or {})
            out.append(
                {
                    "attributes": attrs,
                    "geometry": geom,
                    "layer_id": layer_id,
                    "layer_name": layer_name,
                    "service": service,
                }
            )
        return out

    def fetch_context_from_service(
        self,
        service: str,
        site_polygon: Any,
        *,
        buffer_m: float = 300.0,
        source_label: Optional[str] = None,
        max_records_per_layer: int = 200,
        preferred_layers: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        if not HAS_SHAPELY or site_polygon is None or site_polygon.is_empty:
            return {"source": source_label or service, "service": service, "features": [], "errors": ["shapely/polygon mangler"]}

        bounds = _expanded_bbox(tuple(site_polygon.bounds), float(buffer_m))
        errors: List[str] = []
        features: List[Dict[str, Any]] = []

        try:
            if preferred_layers:
                layers = self.find_layers_by_name(service, preferred_layers)
                if not layers:
                    layers = self.list_layers(service, leaf_only=True)
            else:
                layers = self.list_layers(service, leaf_only=True)
        except Exception as exc:
            return {
                "source": source_label or service,
                "service": service,
                "features": [],
                "errors": [str(exc)],
            }

        for layer in layers:
            layer_id = int(layer.get("id", -1))
            if layer_id < 0:
                continue
            try:
                data = self._query_layer_in_bbox(
                    service,
                    layer_id,
                    bounds,
                    out_fields="*",
                    max_records=max_records_per_layer,
                )
                layer_features = self._serialize_features(service, layer, data.get("features") or [])
                features.extend(layer_features)
            except Exception as exc:
                errors.append(f"{layer.get('name', layer_id)}: {str(exc)[:120]}")

        return {
            "source": source_label or service,
            "service": service,
            "features": features,
            "layer_count": len(layers),
            "errors": errors,
            "query_bounds": bounds,
        }

    def address_search(
        self,
        adresse: str,
        kommune: str = "",
        *,
        limit: int = 5,
        out_srid: int = DEFAULT_SRID,
    ) -> List[Dict[str, Any]]:
        query = ", ".join([part.strip() for part in [adresse, kommune] if part and str(part).strip()])
        if not query:
            return []

        data = self._arcgis_json(
            f"{ADDRESS_GEOCODER}/findAddressCandidates",
            params={
                "SingleLine": query,
                "maxLocations": str(max(1, int(limit))),
                "outSR": str(out_srid),
            },
        )
        out: List[Dict[str, Any]] = []
        for candidate in data.get("candidates") or []:
            location = candidate.get("location") or {}
            x = _safe_float(location.get("x"))
            y = _safe_float(location.get("y"))
            if x is None or y is None:
                continue
            hit = {
                "label": candidate.get("address") or (candidate.get("attributes") or {}).get("Match_addr") or query,
                "score": round(float(candidate.get("score", 0.0)), 1),
                "x": float(x),
                "y": float(y),
                "attributes": dict(candidate.get("attributes") or {}),
            }
            try:
                from pyproj import Transformer  # type: ignore

                transformer = Transformer.from_crs(25833, 4326, always_xy=True)
                lon, lat = transformer.transform(float(x), float(y))
                hit["lon"] = float(lon)
                hit["lat"] = float(lat)
            except Exception:
                pass
            out.append(hit)
        return out

    def fetch_tomt_polygon(
        self,
        kommunenr: str,
        gnr_bnr_liste: List[Tuple[str, str]],
    ) -> Tuple[Optional[Any], str]:
        if not HAS_SHAPELY:
            return None, "shapely mangler"

        knr = str(kommunenr).strip().zfill(4)
        polygons: List[Any] = []
        errors: List[str] = []

        for gnr, bnr in gnr_bnr_liste:
            where = f"kommunenr='{knr}' AND gardsnr={str(gnr).strip()} AND bruksnr={str(bnr).strip()}"
            try:
                data = self._query_layer(
                    MATRIKKEL_FS,
                    LAYER_DEKTEIGFLATE,
                    where=where,
                    outFields="kommunenr,gardsnr,bruksnr,oppgittareal,bruksnavn",
                    outSR=str(DEFAULT_SRID),
                    returnGeometry="true",
                    resultRecordCount="50",
                )
                for feat in data.get("features") or []:
                    geom = _geometry_from_arcgis(feat.get("geometry") or {})
                    if geom is not None and not geom.is_empty and float(getattr(geom, "area", 0.0)) >= 5.0:
                        polygons.append(geom)
                if not data.get("features"):
                    errors.append(f"{gnr}/{bnr} (ingen treff)")
            except Exception as exc:
                errors.append(f"{gnr}/{bnr} ({str(exc)[:60]})")

        if not polygons:
            return None, "Geodata Online: " + (" | ".join(errors) if errors else "Ingen polygoner funnet")

        merged = unary_union(polygons).buffer(0)
        msg = f"Geodata Online: Hentet {knr} " + ", ".join(f"{g}/{b}" for g, b in gnr_bnr_liste)
        if errors:
            msg += f" (mangler: {', '.join(errors)})"
        return merged, msg

    def fetch_byggflater(
        self,
        bbox: Tuple[float, float, float, float],
        buffer_m: float = 80.0,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not HAS_SHAPELY:
            return [], {"source": "Geodata Online ByggFlate", "error": "shapely mangler"}

        expanded = _expanded_bbox(bbox, float(buffer_m))
        try:
            data = self._query_layer(
                MATRIKKEL_FS,
                LAYER_BYGGFLATE,
                where="1=1",
                geometry=_bbox_string(expanded),
                geometryType="esriGeometryEnvelope",
                spatialRel="esriSpatialRelIntersects",
                inSR=str(DEFAULT_SRID),
                outSR=str(DEFAULT_SRID),
                outFields="antalletasjer,antallhovedetasjer,antallkjelleretasjer,bebygdareal,bygningstype,bygningstypekode,kommunenr",
                returnGeometry="true",
                resultRecordCount="500",
            )
        except Exception as exc:
            return [], {"source": "Geodata Online ByggFlate", "error": str(exc)[:120]}

        buildings: List[Dict[str, Any]] = []
        for feat in data.get("features") or []:
            geom = _geometry_from_arcgis(feat.get("geometry") or {})
            if geom is None or geom.is_empty or float(getattr(geom, "area", 0.0)) < 4.0:
                continue
            attrs = dict(feat.get("attributes") or {})
            floors = _safe_int(attrs.get("antalletasjer") or attrs.get("antallhovedetasjer") or 0, 0)
            floors = max(floors, 1)
            height_m = max(DEFAULT_FLOOR_HEIGHT * floors, DEFAULT_FLOOR_HEIGHT)
            buildings.append(
                {
                    "polygon": geom,
                    "etasjer": floors,
                    "height_m": round(float(height_m), 1),
                    "bebygdareal": round(float(_safe_float(attrs.get("bebygdareal"), float(getattr(geom, "area", 0.0))) or 0.0), 1),
                    "bygningstype": attrs.get("bygningstype") or attrs.get("bygningstypekode") or "Ukjent",
                    "attributes": attrs,
                }
            )

        meta = {
            "source": "Geodata Online ByggFlate",
            "service": MATRIKKEL_FS,
            "count": len(buildings),
            "bbox": expanded,
        }
        return buildings, meta

    def fetch_ortofoto(
        self,
        bbox: Tuple[float, float, float, float],
        *,
        buffer_m: float = 80.0,
        width: int = 1200,
        height: int = 1200,
    ) -> Tuple[Optional[Image.Image], str]:
        bounds = _expanded_bbox(bbox, float(buffer_m))
        candidates = [
            (GEOMAP_BILDER_NYESTE_IS, "Geodata Online GeomapBilderNyeste"),
            (GEOCACHE_BILDER_MS, "Geodata Online GeocacheBilder"),
            (GEOCACHE_LANDSKAP_MS, "Geodata Online GeocacheLandskap"),
        ]
        for service, label in candidates:
            try:
                content = self._image_export(service, bounds, width=width, height=height)
                if content:
                    return Image.open(io.BytesIO(content)).convert("RGB"), label
            except Exception:
                continue
        return None, "Geodata Online: ortofoto/bakgrunnskart utilgjengelig"

    def fetch_plan_context(self, site_polygon: Any, buffer_m: float = 300.0) -> Dict[str, Any]:
        primary = self.fetch_context_from_service(
            GEOMAP_REGULERINGSPLAN_MS,
            site_polygon,
            buffer_m=buffer_m,
            source_label="Geodata Online Reguleringsplan",
        )
        if primary.get("features") or not primary.get("errors"):
            return primary
        fallback = self.fetch_context_from_service(
            GEOMAP_DOKPLAN_MS,
            site_polygon,
            buffer_m=buffer_m,
            source_label="Geodata Online DOK Plan",
        )
        fallback["errors"] = list(primary.get("errors") or []) + list(fallback.get("errors") or [])
        return fallback

    def fetch_utbygging_context(self, site_polygon: Any, buffer_m: float = 500.0) -> Dict[str, Any]:
        return self.fetch_context_from_service(
            GEOMAP_UTBYGGER_MS,
            site_polygon,
            buffer_m=buffer_m,
            source_label="Geodata Online Utbygger",
        )

    def fetch_transport_context(self, site_polygon: Any, buffer_m: float = 800.0) -> Dict[str, Any]:
        return self.fetch_context_from_service(
            GEOMAP_SAMFERDSEL_MS,
            site_polygon,
            buffer_m=buffer_m,
            source_label="Geodata Online Samferdsel",
        )

    def fetch_geology_context(self, site_polygon: Any, buffer_m: float = 300.0) -> Dict[str, Any]:
        return self.fetch_context_from_service(
            GEOMAP_DOKGEOLOGI_MS,
            site_polygon,
            buffer_m=buffer_m,
            source_label="Geodata Online DOK Geologi",
        )

    def fetch_site_context_bundle(
        self,
        site_polygon: Any,
        *,
        neighbor_buffer_m: float = 300.0,
        transport_buffer_m: float = 700.0,
    ) -> Dict[str, Any]:
        return {
            "plan": self.fetch_plan_context(site_polygon, buffer_m=max(180.0, float(neighbor_buffer_m))),
            "utbygging": self.fetch_utbygging_context(site_polygon, buffer_m=max(250.0, float(neighbor_buffer_m) * 1.4)),
            "transport": self.fetch_transport_context(site_polygon, buffer_m=max(500.0, float(transport_buffer_m))),
            "geology": self.fetch_geology_context(site_polygon, buffer_m=max(150.0, float(neighbor_buffer_m))),
            "eiendom": {
                "source": "Geodata Online Matrikkel",
                "service": MATRIKKEL_MS,
            },
        }

    def fetch_terrain_samples(
        self,
        site_polygon: Any,
        *,
        sample_spacing_m: float = 12.0,
        max_points: int = 220,
        service: str = GEOMAP_DTM_IS,
    ) -> Dict[str, Any]:
        if not HAS_SHAPELY or site_polygon is None or site_polygon.is_empty:
            return {"source": "Geodata Online Terreng", "service": service, "samples": [], "error": "polygon mangler"}

        points = _iter_grid_points(site_polygon, spacing_m=sample_spacing_m, max_points=max_points)
        if not points:
            return {"source": "Geodata Online Terreng", "service": service, "samples": [], "error": "ingen sample-punkter"}

        geometry = {
            "points": [[round(x, 3), round(y, 3)] for x, y in points],
            "spatialReference": {"wkid": DEFAULT_SRID},
        }
        data = self._arcgis_json(
            self._service_url(service, "getSamples"),
            params={
                "geometry": json.dumps(geometry, separators=(",", ":")),
                "geometryType": "esriGeometryMultipoint",
                "returnFirstValueOnly": "false",
                "returnGeometry": "true",
            },
            timeout=30,
        )

        samples: List[Dict[str, float]] = []
        raw_samples = list(data.get("samples") or data.get("results") or [])
        for sample in raw_samples:
            location = sample.get("location") or {}
            value: Any = sample.get("value")
            if value is None:
                values = sample.get("values") or []
                value = values[0] if values else None
            x = _safe_float(location.get("x") if location else sample.get("x"))
            y = _safe_float(location.get("y") if location else sample.get("y"))
            z = _safe_float(value)
            if x is None or y is None or z is None:
                continue
            samples.append({"x": float(x), "y": float(y), "z": float(z)})

        return {
            "source": "Geodata Online Terrengmodell",
            "service": service,
            "samples": samples,
            "point_count": len(samples),
        }

    def fetch_terrain_model(
        self,
        site_polygon: Any,
        *,
        sample_spacing_m: float = 12.0,
        max_points: int = 220,
        service: str = GEOMAP_DTM_IS,
    ) -> Optional[Dict[str, Any]]:
        if np is None:
            return None
        sample_payload = self.fetch_terrain_samples(
            site_polygon,
            sample_spacing_m=sample_spacing_m,
            max_points=max_points,
            service=service,
        )
        samples = list(sample_payload.get("samples") or [])
        if len(samples) < 3:
            return None

        x = np.asarray([sample["x"] for sample in samples], dtype=float)
        y = np.asarray([sample["y"] for sample in samples], dtype=float)
        z = np.asarray([sample["z"] for sample in samples], dtype=float)
        A = np.column_stack([x, y, np.ones(len(x))])
        coeff, *_ = np.linalg.lstsq(A, z, rcond=None)
        a, b, c = [float(value) for value in coeff]
        z_pred = A @ coeff
        rmse = float(np.sqrt(np.mean((z - z_pred) ** 2)))
        return {
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
            "point_count": int(len(samples)),
            "source": sample_payload.get("source", "Geodata Online Terrengmodell"),
            "service": service,
            "sample_points": samples[:40],
        }

    def fetch_scene_config(self) -> Dict[str, Any]:
        return {
            "token": self.get_token(),
            "rest_base": REST_BASE,
            "spatial_reference": DEFAULT_SRID,
            "services": {
                "elevation_url": self._service_url(GEOMAP_DTM_IS),
                "terrain_cache_url": self._service_url(GEOCACHE_TERRENG_IS),
                "imagery_url": self._service_url(GEOCACHE_BILDER_MS),
                "imagery_latest_url": self._service_url(GEOMAP_BILDER_NYESTE_IS),
                "landscape_url": self._service_url(GEOCACHE_LANDSKAP_MS),
                "hillshade_url": self._service_url(GEOCACHE_TERRENGSKYGGE_MS),
                "plan_url": self._service_url(GEOMAP_DOKPLAN_MS),
                "regulation_url": self._service_url(GEOMAP_REGULERINGSPLAN_MS),
                "geology_url": self._service_url(GEOMAP_DOKGEOLOGI_MS),
            },
        }


# ---------------------------------------------------------------------------
# Compatibility helper used by the Streamlit app
# ---------------------------------------------------------------------------
def geodata_buildings_to_neighbors(
    buildings: List[Dict[str, Any]],
    *,
    site_polygon: Any,
    max_distance_m: float = 160.0,
) -> List[Dict[str, Any]]:
    if not HAS_SHAPELY or site_polygon is None:
        return []

    neighbors: List[Dict[str, Any]] = []
    for building in buildings:
        poly = building.get("polygon")
        if poly is None or poly.is_empty:
            continue
        if poly.intersects(site_polygon):
            continue
        distance_m = float(poly.distance(site_polygon))
        if distance_m > max_distance_m:
            continue
        neighbors.append(
            {
                "polygon": poly.buffer(0),
                "height_m": float(building.get("height_m", 9.0)),
                "source": "Geodata Online ByggFlate",
                "distance_m": distance_m,
                "building_type": building.get("bygningstype", "Ukjent"),
            }
        )
    neighbors.sort(key=lambda item: float(item.get("distance_m", 0.0)))
    return neighbors
