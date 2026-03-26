"""
Geodata Online API-klient for Builtly Mulighetsstudie.

VERIFISERTE tjenestestier og feltnavn fra API-inspeksjon 2026-01-24.

Token: POST https://services.geodataonline.no/arcgis/tokens/generateToken
Base:  https://services.geodataonline.no/arcgis/rest/services

Matrikkel FeatureServer:
  Geomap_UTM33_EUREF89/GeomapMatrikkel/FeatureServer
    Lag 5:  DekTeigFlate   (tomtepolygoner)  - kommunenr, gardsnr, bruksnr
    Lag 9:  ByggFlate      (bygningsflater)  - antalletasjer, bebygdareal, bygningstype

Miljoevariabler i Render:
  GEODATA_ONLINE_USER
  GEODATA_ONLINE_PASS
"""

import io
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image

try:
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False

# ---------------------------------------------------------------------------
# Konstanter — verifisert mot live API
# ---------------------------------------------------------------------------
TOKEN_URL = "https://services.geodataonline.no/arcgis/tokens/generateToken"
REST_BASE = "https://services.geodataonline.no/arcgis/rest/services"

MATRIKKEL_FS = "Geomap_UTM33_EUREF89/GeomapMatrikkel/FeatureServer"
LAYER_DEKTEIGFLATE = 5
LAYER_BYGGFLATE = 9

ORTOFOTO_MS = "Geocache_UTM33_EUREF89/GeocacheBilder/MapServer"
BAKGRUNN_MS = "Geocache_UTM33_EUREF89/GeocacheBakgrunnskart/MapServer"
DOKPLAN_MS = "Geomap_UTM33_EUREF89/GeomapDOKPlan/MapServer"

DEFAULT_FLOOR_HEIGHT = 3.0


class GeodataOnlineClient:

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        self.username = username or os.environ.get("GEODATA_ONLINE_USER", "")
        self.password = password or os.environ.get("GEODATA_ONLINE_PASS", "")
        self._token = ""
        self._token_expires = 0

    def is_available(self) -> bool:
        return bool(self.username and self.password)

    def get_token(self) -> str:
        if self._token and time.time() * 1000 < (self._token_expires - 300_000):
            return self._token
        if not self.is_available():
            raise RuntimeError("GEODATA_ONLINE_USER / GEODATA_ONLINE_PASS mangler")
        resp = requests.post(TOKEN_URL, data={
            "username": self.username,
            "password": self.password,
            "client": "requestip",
            "expiration": "1440",
            "f": "json",
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Token feilet: {data['error'].get('message', data['error'])}")
        self._token = data["token"]
        self._token_expires = int(data.get("expires", 0))
        return self._token

    def token_status(self) -> Dict[str, Any]:
        remaining = max(0, (self._token_expires / 1000) - time.time())
        return {"valid": bool(self._token) and remaining > 0, "remaining_minutes": round(remaining / 60, 1)}

    def _query(self, service: str, layer_id: int, **kwargs) -> Dict[str, Any]:
        url = f"{REST_BASE}/{service}/{layer_id}/query"
        params = {"f": "json", "token": self.get_token()}
        params.update(kwargs)
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def _export_image(self, service: str, bbox: str, srid: int = 25833,
                      width: int = 1200, height: int = 1200) -> Optional[bytes]:
        url = f"{REST_BASE}/{service}/export"
        resp = requests.get(url, params={
            "bbox": bbox, "bboxSR": str(srid), "imageSR": str(srid),
            "size": f"{width},{height}", "format": "png",
            "transparent": "false", "f": "image",
            "token": self.get_token(),
        }, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 2000:
            if resp.content[:4] in (b"\x89PNG", b"\xff\xd8\xff") or "image" in resp.headers.get("Content-Type", ""):
                return resp.content
        return None

    def discover_services(self, folder: str = "") -> Dict[str, Any]:
        url = f"{REST_BASE}/{folder}" if folder else REST_BASE
        resp = requests.get(url, params={"f": "json", "token": self.get_token()}, timeout=12)
        return resp.json() if resp.status_code == 200 else {}

    # =====================================================================
    # 1. TOMTEHENTING — DekTeigFlate (lag 5)
    #    WHERE: kommunenr='5001' AND gardsnr=316 AND bruksnr=724
    # =====================================================================
    def fetch_tomt_polygon(
        self,
        kommunenr: str,
        gnr_bnr_liste: List[Tuple[str, str]],
    ) -> Tuple[Optional[Any], str]:
        if not HAS_SHAPELY:
            return None, "shapely mangler"

        knr = str(kommunenr).strip().zfill(4)
        polygoner: list = []
        feil: list = []

        for gnr, bnr in gnr_bnr_liste:
            where = f"kommunenr='{knr}' AND gardsnr={str(gnr).strip()} AND bruksnr={str(bnr).strip()}"
            try:
                data = self._query(
                    MATRIKKEL_FS, LAYER_DEKTEIGFLATE,
                    where=where,
                    outFields="kommunenr,gardsnr,bruksnr,oppgittareal,bruksnavn",
                    outSR="25833",
                    returnGeometry="true",
                    resultRecordCount="50",
                )
                features = data.get("features", [])
                if not features:
                    feil.append(f"{gnr}/{bnr} (ingen treff)")
                    continue
                for feat in features:
                    rings = feat.get("geometry", {}).get("rings")
                    if not rings:
                        continue
                    poly = Polygon(rings[0]).buffer(0)
                    if not poly.is_empty and poly.area >= 5:
                        polygoner.append(poly)
            except Exception as exc:
                feil.append(f"{gnr}/{bnr} ({str(exc)[:60]})")

        if not polygoner:
            return None, "Geodata Online: " + (" | ".join(feil) if feil else "Ingen polygoner funnet")

        samlet = unary_union(polygoner)
        msg = f"Geodata Online: Hentet {knr} " + ", ".join(f"{g}/{b}" for g, b in gnr_bnr_liste)
        if feil:
            msg += f" (mangler: {', '.join(feil)})"
        return samlet, msg

    # =====================================================================
    # 2. NABOBYGG — ByggFlate (lag 9), spatial query
    # =====================================================================
    def fetch_byggflater(
        self,
        bbox: Tuple[float, float, float, float],
        buffer_m: float = 80.0,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not HAS_SHAPELY:
            return [], {"source": "Geodata Online ByggFlate", "error": "shapely mangler"}

        minx, miny, maxx, maxy = bbox
        envelope = f"{minx - buffer_m},{miny - buffer_m},{maxx + buffer_m},{maxy + buffer_m}"

        try:
            data = self._query(
                MATRIKKEL_FS, LAYER_BYGGFLATE,
                where="1=1",
                geometry=envelope,
                geometryType="esriGeometryEnvelope",
                spatialRel="esriSpatialRelIntersects",
                inSR="25833", outSR="25833",
                outFields="antalletasjer,antallhovedetasjer,antallkjelleretasjer,bebygdareal,bygningstype,bygningstypekode,kommunenr",
                returnGeometry="true",
                resultRecordCount="500",
            )
        except Exception as exc:
            return [], {"source": "Geodata Online ByggFlate", "error": str(exc)[:80]}

        buildings: list = []
        for feat in data.get("features", []):
            rings = feat.get("geometry", {}).get("rings")
            if not rings:
                continue
            try:
                poly = Polygon(rings[0]).buffer(0)
                if poly.is_empty or poly.area < 4:
                    continue
            except Exception:
                continue

            attrs = feat.get("attributes", {})
            etasjer = attrs.get("antalletasjer") or attrs.get("antallhovedetasjer") or 0
            try:
                etasjer = int(etasjer)
            except (ValueError, TypeError):
                etasjer = 0
            height_m = max(3.0, etasjer * DEFAULT_FLOOR_HEIGHT) if etasjer > 0 else 9.0

            buildings.append({
                "polygon": poly,
                "height_m": height_m,
                "etasjer": etasjer,
                "bebygdareal": attrs.get("bebygdareal"),
                "bygningstype": attrs.get("bygningstype", "Ukjent"),
                "source": "Geodata Online ByggFlate",
                "distance_m": 0.0,
            })

        return buildings, {"source": "Geodata Online ByggFlate", "count": len(buildings)}

    # =====================================================================
    # 3. HD-ORTOFOTO
    # =====================================================================
    def fetch_ortofoto(
        self,
        bbox: Tuple[float, float, float, float],
        buffer_m: float = 80.0,
        width: int = 1200, height: int = 1200,
    ) -> Tuple[Optional[Image.Image], str]:
        minx, miny, maxx, maxy = bbox
        envelope = f"{minx - buffer_m},{miny - buffer_m},{maxx + buffer_m},{maxy + buffer_m}"
        img_bytes = self._export_image(ORTOFOTO_MS, envelope, width=width, height=height)
        if img_bytes:
            return Image.open(io.BytesIO(img_bytes)).convert("RGB"), "Geodata Online Ortofoto"
        img_bytes = self._export_image(BAKGRUNN_MS, envelope, width=width, height=height)
        if img_bytes:
            return Image.open(io.BytesIO(img_bytes)).convert("RGB"), "Geodata Online Bakgrunnskart"
        return None, "Kunne ikke hente kartbilde fra Geodata Online"


# ---------------------------------------------------------------------------
# Hjelpefunksjoner
# ---------------------------------------------------------------------------
def geodata_buildings_to_neighbors(
    buildings: List[Dict[str, Any]],
    site_polygon: Optional[Any] = None,
    max_distance_m: float = 250.0,
) -> List[Dict[str, Any]]:
    if not HAS_SHAPELY or site_polygon is None:
        return buildings
    neighbors = []
    for bld in buildings:
        poly = bld.get("polygon")
        if poly is None or poly.intersects(site_polygon):
            continue
        dist = poly.distance(site_polygon)
        if dist > max_distance_m:
            continue
        neighbors.append({
            "polygon": poly.buffer(0),
            "height_m": float(bld.get("height_m", 9.0)),
            "source": "Geodata Online ByggFlate",
            "distance_m": float(dist),
            "building_type": bld.get("bygningstype", "Ukjent"),
        })
    neighbors.sort(key=lambda n: n["distance_m"])
    return neighbors
