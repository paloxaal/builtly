"""
Geodata Online API-klient for Builtly Mulighetsstudie.

Token-basert autentisering mot ArcGIS REST-tjenester på
services.geodataonline.no. Gir tilgang til:
  - FKB-Bygning (nabobygg med ekte høyder)
  - Reguleringsplan (arealformål, utnyttelsesgrad, byggegrenser)
  - Bakgrunnskart / ortofoto (HD-kartbilder)
  - DTM terrengdata (høydemodell)

Sett miljøvariabler i Render:
  GEODATA_ONLINE_USER
  GEODATA_ONLINE_PASS

Bruk:
  from geodata_client import GeodataOnlineClient
  gdo = GeodataOnlineClient()
  if gdo.is_available():
      buildings = gdo.fetch_fkb_buildings(bbox, srid=25833)
"""

import io
import math
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image

try:
    from shapely.geometry import Polygon, MultiPolygon, shape, box
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False

# ---------------------------------------------------------------------------
# Token-generator og klient
# ---------------------------------------------------------------------------

TOKEN_URL = "https://services.geodataonline.no/arcgis/tokens/generateToken"
REST_BASE = "https://services.geodataonline.no/arcgis/rest/services"

# Kjente tjenestestier (verifiseres i discover_services)
# Disse er de vanligste – discover() oppdaterer med faktiske stier
SERVICE_PATHS = {
    "bakgrunnskart": "Geocache_WMAS_WGS84/GeocacheBakgrunnskart/MapServer",
    "bakgrunnskart_utm33": "Geocache_UTM33_EUREF89/GeocacheBakgrunnskart/MapServer",
    "ortofoto": "Geocache_WMAS_WGS84/GeocacheBilder/MapServer",
    "ortofoto_utm33": "Geocache_UTM33_EUREF89/GeocacheBilder/MapServer",
    "fkb": "Geomap_UTM33_EUREF89/GeomapFKB/MapServer",
    "fkb_bygning": "Geomap_UTM33_EUREF89/GeomapByggning/MapServer",
    "regulering": "Geomap_UTM33_EUREF89/GeomapRegulering/MapServer",
    "reguleringsplan": "Geomap_UTM33_EUREF89/GeomapReguleringsplan/MapServer",
    "dtm": "Geocache_UTM33_EUREF89/GeocacheTerreng/MapServer",
    "hoydedata": "Geomap_UTM33_EUREF89/GeomapHoydedata/MapServer",
    "matrikkel": "Geomap_UTM33_EUREF89/GeomapMatrikkel/MapServer",
}


@dataclass
class TokenInfo:
    token: str = ""
    expires_epoch_ms: int = 0
    ssl: bool = True


class GeodataOnlineClient:
    """Klient for Geodata Online ArcGIS REST-tjenester."""

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        default_expiration_min: int = 1440,  # 24 timer
    ):
        self.username = username or os.environ.get("GEODATA_ONLINE_USER", "")
        self.password = password or os.environ.get("GEODATA_ONLINE_PASS", "")
        self.default_expiration = default_expiration_min
        self._token_info = TokenInfo()
        self._discovered_services: Dict[str, str] = {}
        self._discovery_done = False

    # --- Tilgjengelighet ---
    def is_available(self) -> bool:
        return bool(self.username and self.password)

    # --- Token-håndtering ---
    def _token_is_valid(self) -> bool:
        if not self._token_info.token:
            return False
        # 5 min margin for å unngå race
        return time.time() * 1000 < (self._token_info.expires_epoch_ms - 300_000)

    def get_token(self) -> str:
        """Hent gyldig token, generer ny hvis utløpt."""
        if self._token_is_valid():
            return self._token_info.token
        return self._generate_token()

    def _generate_token(self) -> str:
        """POST til generateToken-endepunktet. Returnerer token-streng."""
        if not self.is_available():
            raise RuntimeError("Geodata Online credentials mangler. Sett GEODATA_ONLINE_USER og GEODATA_ONLINE_PASS.")

        payload = {
            "username": self.username,
            "password": self.password,
            "client": "requestip",
            "expiration": str(self.default_expiration),
            "f": "json",
        }

        try:
            resp = requests.post(
                TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"Geodata Online token-forespørsel feilet: {exc}") from exc

        if "error" in data:
            err = data["error"]
            msg = err.get("message", str(err))
            raise RuntimeError(f"Geodata Online autentisering feilet: {msg}")

        token = data.get("token", "")
        expires = int(data.get("expires", 0))
        ssl_flag = data.get("ssl", True)

        if not token:
            raise RuntimeError("Geodata Online returnerte tomt token.")

        self._token_info = TokenInfo(token=token, expires_epoch_ms=expires, ssl=ssl_flag)
        return token

    def token_status(self) -> Dict[str, Any]:
        """Debug-info om token-tilstand."""
        remaining_s = max(0, (self._token_info.expires_epoch_ms / 1000) - time.time())
        return {
            "has_token": bool(self._token_info.token),
            "valid": self._token_is_valid(),
            "remaining_minutes": round(remaining_s / 60, 1),
            "ssl": self._token_info.ssl,
        }

    # --- Service discovery ---
    def discover_services(self, folder: str = "") -> Dict[str, Any]:
        """List tilgjengelige tjenester i en mappe (eller rot)."""
        url = f"{REST_BASE}/{folder}" if folder else REST_BASE
        try:
            resp = requests.get(
                url,
                params={"f": "json", "token": self.get_token()},
                timeout=12,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return {}

    def discover_all(self) -> Dict[str, List[str]]:
        """Oppdag alle mapper og tjenester kontoen har tilgang til."""
        if self._discovery_done and self._discovered_services:
            return {"services": list(self._discovered_services.keys())}

        result: Dict[str, List[str]] = {"folders": [], "services": []}
        try:
            root = self.discover_services()
            folders = root.get("folders", [])
            result["folders"] = folders

            services = root.get("services", [])
            for svc in services:
                name = svc.get("name", "")
                stype = svc.get("type", "")
                key = f"{name}/{stype}"
                result["services"].append(key)
                self._discovered_services[key] = f"{name}/{stype}"

            for folder_name in folders:
                folder_data = self.discover_services(folder_name)
                for svc in folder_data.get("services", []):
                    name = svc.get("name", "")
                    stype = svc.get("type", "")
                    key = f"{name}/{stype}"
                    result["services"].append(key)
                    self._discovered_services[key] = f"{name}/{stype}"

            self._discovery_done = True
        except Exception as exc:
            result["error"] = str(exc)

        return result

    # --- Generisk ArcGIS REST-kall ---
    def _rest_get(self, service_path: str, operation: str = "", params: Optional[Dict] = None, timeout: int = 15) -> Dict[str, Any]:
        """Generisk GET mot en ArcGIS REST-tjeneste."""
        url = f"{REST_BASE}/{service_path}"
        if operation:
            url = f"{url}/{operation}"

        query = {"f": "json", "token": self.get_token()}
        if params:
            query.update(params)

        resp = requests.get(url, params=query, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _rest_image(self, service_path: str, params: Dict, timeout: int = 15) -> Optional[bytes]:
        """Hent kartbilde (PNG/JPEG) fra en MapServer/export."""
        url = f"{REST_BASE}/{service_path}/export"
        query = {"token": self.get_token()}
        query.update(params)

        resp = requests.get(url, params=query, timeout=timeout)
        if resp.status_code == 200 and len(resp.content) > 2000:
            content_type = resp.headers.get("Content-Type", "")
            if "image" in content_type or resp.content[:4] in (b"\x89PNG", b"\xff\xd8\xff"):
                return resp.content
        return None

    # --- MapServer-info (lag, felter) ---
    def get_service_info(self, service_path: str) -> Dict[str, Any]:
        """Hent metadata for en MapServer/FeatureServer."""
        return self._rest_get(service_path)

    def get_layer_info(self, service_path: str, layer_id: int) -> Dict[str, Any]:
        """Hent metadata for et spesifikt lag."""
        return self._rest_get(service_path, str(layer_id))

    # --- QUERY: Spatial / attributtforespørsler ---
    def query_layer(
        self,
        service_path: str,
        layer_id: int,
        geometry: Optional[str] = None,
        geometry_type: str = "esriGeometryEnvelope",
        spatial_rel: str = "esriSpatialRelIntersects",
        where: str = "1=1",
        out_fields: str = "*",
        out_sr: int = 25833,
        return_geometry: bool = True,
        result_record_count: int = 200,
    ) -> Dict[str, Any]:
        """Generisk spatial/attributt-query mot et lag."""
        params: Dict[str, Any] = {
            "where": where,
            "outFields": out_fields,
            "outSR": str(out_sr),
            "returnGeometry": "true" if return_geometry else "false",
            "resultRecordCount": str(result_record_count),
        }
        if geometry:
            params["geometry"] = geometry
            params["geometryType"] = geometry_type
            params["spatialRel"] = spatial_rel
            params["inSR"] = str(out_sr)

        return self._rest_get(service_path, f"{layer_id}/query", params)

    # =========================================================================
    # HØYNIVÅ-METODER FOR MULIGHETSSTUDIEN
    # =========================================================================

    # --- 1. FKB-Bygning: Nabobygg med ekte høyder ---
    def fetch_fkb_buildings(
        self,
        bbox: Tuple[float, float, float, float],
        srid: int = 25833,
        buffer_m: float = 80.0,
        service_path: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Hent bygningsfotavtrykk fra FKB-Bygning innenfor en bounding box.
        
        Returnerer:
            (buildings, meta) der buildings er liste med:
                polygon: Shapely Polygon
                height_m: float
                building_type: str
                source: str
        """
        if not HAS_SHAPELY:
            return [], {"source": "Geodata Online FKB", "error": "shapely mangler"}

        minx, miny, maxx, maxy = bbox
        env = f"{minx - buffer_m},{miny - buffer_m},{maxx + buffer_m},{maxy + buffer_m}"

        # Prøv kjente FKB-bygning-stier
        paths_to_try = []
        if service_path:
            paths_to_try.append(service_path)
        paths_to_try.extend([
            SERVICE_PATHS["fkb_bygning"],
            SERVICE_PATHS["fkb"],
        ])

        buildings: List[Dict[str, Any]] = []
        last_error = ""

        for path in paths_to_try:
            # Først: finn riktig layer-ID for bygninger
            layer_ids_to_try = [0, 1, 2, 3]  # Prøv de første lagene
            
            for layer_id in layer_ids_to_try:
                try:
                    data = self.query_layer(
                        service_path=path,
                        layer_id=layer_id,
                        geometry=env,
                        geometry_type="esriGeometryEnvelope",
                        out_sr=srid,
                        result_record_count=500,
                    )
                    
                    features = data.get("features", [])
                    if not features:
                        continue

                    for feat in features:
                        geom = feat.get("geometry", {})
                        attrs = feat.get("attributes", {})
                        
                        rings = geom.get("rings")
                        if not rings:
                            continue
                        
                        try:
                            poly = Polygon(rings[0])
                            if poly.is_empty or poly.area < 4.0:
                                continue
                            poly = poly.buffer(0)
                            
                            # Hent høyde fra attributter
                            height_m = self._extract_building_height(attrs)
                            building_type = self._extract_building_type(attrs)
                            
                            buildings.append({
                                "polygon": poly,
                                "height_m": height_m,
                                "building_type": building_type,
                                "source": "Geodata Online FKB",
                                "distance_m": 0.0,  # Beregnes av kaller
                            })
                        except Exception:
                            continue

                    if buildings:
                        return buildings, {
                            "source": f"Geodata Online FKB ({path}, lag {layer_id})",
                            "count": len(buildings),
                        }

                except Exception as exc:
                    last_error = str(exc)[:80]
                    continue

        return [], {"source": "Geodata Online FKB", "error": last_error or "Ingen bygg funnet", "count": 0}

    def _extract_building_height(self, attrs: Dict[str, Any]) -> float:
        """Trekk ut bygningshøyde fra FKB-attributter."""
        # FKB bruker ulike feltnavn avhengig av versjon
        height_fields = [
            "HOYDE", "Hoyde", "hoyde",
            "H_GESIMS", "h_gesims", "GESIMSHOYDE", "gesimshoyde",
            "MOENEHOYDE", "moenehoyde", "H_MOENE", "h_moene",
            "BYGNINGSHOY", "bygningshoy",
            "HEIGHT", "height",
            "MAXHOYDE", "maxhoyde",
        ]
        for f in height_fields:
            val = attrs.get(f)
            if val is not None:
                try:
                    h = float(val)
                    if 2.0 < h < 200.0:
                        return h
                except (ValueError, TypeError):
                    continue

        # Fallback: estimer fra etasjer
        floor_fields = ["ETASJER", "etasjer", "ANTALL_ETASJER", "antall_etasjer", "ANT_ETASJ"]
        for f in floor_fields:
            val = attrs.get(f)
            if val is not None:
                try:
                    floors = int(float(val))
                    if 1 <= floors <= 50:
                        return floors * 3.0
                except (ValueError, TypeError):
                    continue

        return 9.0  # Fallback

    def _extract_building_type(self, attrs: Dict[str, Any]) -> str:
        """Trekk ut bygningstype fra FKB-attributter."""
        type_fields = ["BYGNINGSTYPE", "bygningstype", "BYGNTYPE", "bygntype", "OBJTYPE", "objtype"]
        for f in type_fields:
            val = attrs.get(f)
            if val:
                return str(val)
        return "Ukjent"

    # --- 2. Reguleringsplan: Arealformål og utnyttelse ---
    def fetch_reguleringsplan(
        self,
        bbox: Tuple[float, float, float, float],
        srid: int = 25833,
        service_path: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Hent reguleringsdata (arealformål, utnyttelsesgrad) for en tomt.
        
        Returnerer:
            (reg_data, meta) der reg_data inneholder:
                arealformaal: str (f.eks. "Boligbebyggelse")
                utnyttelsesgrad: float (%-BYA eller annet)
                plannavn: str
                planid: str
                vedtaksdato: str
                bestemmelser: dict med tolkede grenser
        """
        minx, miny, maxx, maxy = bbox
        # Bruk senterpunkt for identify
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        env = f"{minx},{miny},{maxx},{maxy}"

        paths_to_try = []
        if service_path:
            paths_to_try.append(service_path)
        paths_to_try.extend([
            SERVICE_PATHS["reguleringsplan"],
            SERVICE_PATHS["regulering"],
        ])

        reg_data: Dict[str, Any] = {}
        last_error = ""

        for path in paths_to_try:
            # Prøv arealformål-laget (vanligvis lag 0 eller 1)
            for layer_id in range(6):
                try:
                    data = self.query_layer(
                        service_path=path,
                        layer_id=layer_id,
                        geometry=f"{cx},{cy}",
                        geometry_type="esriGeometryPoint",
                        out_sr=srid,
                        return_geometry=False,
                        result_record_count=5,
                    )
                    
                    features = data.get("features", [])
                    if not features:
                        continue

                    # Parse attributter
                    attrs = features[0].get("attributes", {})
                    if not attrs:
                        continue

                    parsed = self._parse_regulation_attributes(attrs)
                    if parsed:
                        parsed["_layer_id"] = layer_id
                        parsed["_service_path"] = path
                        reg_data.update(parsed)
                        
                        # Hvis vi har arealformål, stopp – dette er hovedlaget
                        if parsed.get("arealformaal"):
                            return reg_data, {
                                "source": f"Geodata Online Reguleringsplan ({path}, lag {layer_id})",
                                "plannavn": reg_data.get("plannavn", ""),
                            }

                except Exception as exc:
                    last_error = str(exc)[:80]
                    continue

        if reg_data:
            return reg_data, {"source": "Geodata Online Reguleringsplan (delvis)", "warning": "Ikke alle felt funnet"}
        return {}, {"source": "Geodata Online Reguleringsplan", "error": last_error or "Ingen reguleringsdata funnet"}

    def _parse_regulation_attributes(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Parse reguleringsattributter fra ArcGIS-respons."""
        result: Dict[str, Any] = {}

        # Arealformål
        formaal_fields = [
            "AREALFORMAAL", "arealformaal", "AREALFORM", "arealform",
            "FORMAAL", "formaal", "AREALDEL", "arealdel",
            "ARTYPE", "artype", "PLANNAVN_FORMAAL",
        ]
        for f in formaal_fields:
            val = attrs.get(f)
            if val:
                result["arealformaal"] = str(val)
                break

        # Utnyttelsesgrad (%-BYA, %-BRA, etc.)
        utnyttelse_fields = [
            "UTNYTTINGSGRAD", "utnyttingsgrad", "UTNYTTING", "utnytting",
            "BYA_PROSENT", "bya_prosent", "BYA", "bya",
            "GRAD_AV_UTNYTTING", "grad_av_utnytting",
            "PROSENT_BYA", "prosent_bya",
        ]
        for f in utnyttelse_fields:
            val = attrs.get(f)
            if val is not None:
                try:
                    result["utnyttelsesgrad"] = float(val)
                    result["utnyttelsesgrad_type"] = f.upper()
                    break
                except (ValueError, TypeError):
                    continue

        # Maks høyde
        height_fields = [
            "MAKS_HOYDE", "maks_hoyde", "MAKSHOYDE", "makshoyde",
            "BYGGEHOYDE", "byggehoyde", "GESIMSHOYDE_MAKS",
        ]
        for f in height_fields:
            val = attrs.get(f)
            if val is not None:
                try:
                    h = float(val)
                    if 2.0 < h < 100.0:
                        result["maks_hoyde_m"] = h
                        break
                except (ValueError, TypeError):
                    continue

        # Maks etasjer
        floor_fields = ["MAKS_ETASJER", "maks_etasjer", "ETASJER", "etasjer", "ANT_ETASJER"]
        for f in floor_fields:
            val = attrs.get(f)
            if val is not None:
                try:
                    result["maks_etasjer"] = int(float(val))
                    break
                except (ValueError, TypeError):
                    continue

        # Plannavn og ID
        plan_fields = {"PLANNAVN": "plannavn", "plannavn": "plannavn", "PLANID": "planid", "planid": "planid",
                       "PLAN_ID": "planid", "VEDTAKSDATO": "vedtaksdato", "vedtaksdato": "vedtaksdato"}
        for src, dst in plan_fields.items():
            val = attrs.get(src)
            if val:
                result[dst] = str(val)

        return result

    # --- 3. Ortofoto / bakgrunnskart (HD) ---
    def fetch_map_image(
        self,
        bbox: Tuple[float, float, float, float],
        srid: int = 25833,
        width: int = 1200,
        height: int = 1200,
        map_type: str = "ortofoto",
        buffer_m: float = 80.0,
    ) -> Tuple[Optional[Image.Image], str]:
        """
        Hent HD-kartbilde fra Geodata Online.
        
        map_type: "ortofoto" | "bakgrunnskart" | "grunnkart"
        """
        minx, miny, maxx, maxy = bbox
        env = f"{minx - buffer_m},{miny - buffer_m},{maxx + buffer_m},{maxy + buffer_m}"

        # Velg riktig tjenestesti basert på type og SRID
        if srid == 25833:
            if map_type == "ortofoto":
                paths = [SERVICE_PATHS["ortofoto_utm33"]]
            else:
                paths = [SERVICE_PATHS["bakgrunnskart_utm33"]]
        else:
            if map_type == "ortofoto":
                paths = [SERVICE_PATHS["ortofoto"]]
            else:
                paths = [SERVICE_PATHS["bakgrunnskart"]]

        params = {
            "bbox": env,
            "bboxSR": str(srid),
            "imageSR": str(srid),
            "size": f"{width},{height}",
            "format": "png",
            "transparent": "false",
            "f": "image",
        }

        for path in paths:
            try:
                img_bytes = self._rest_image(path, params, timeout=15)
                if img_bytes:
                    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                    return img, f"Geodata Online {map_type.capitalize()} ({path})"
            except Exception:
                continue

        return None, f"Kunne ikke hente {map_type} fra Geodata Online"

    # --- 4. DTM-terreng: Høydemodell via identify ---
    def fetch_terrain_profile(
        self,
        bbox: Tuple[float, float, float, float],
        srid: int = 25833,
        grid_size: int = 10,
        service_path: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """
        Hent terrengprofil fra Geodata Online DTM.
        
        Sampler et grid av punkter og returnerer terrengstatistikk
        kompatibelt med load_terrain_input()-formatet.
        """
        import numpy as np

        minx, miny, maxx, maxy = bbox
        dx = maxx - minx
        dy = maxy - miny

        if dx < 5 or dy < 5:
            return None, {"source": "Geodata Online DTM", "error": "Tomt for lite for DTM-sampling"}

        paths_to_try = []
        if service_path:
            paths_to_try.append(service_path)
        paths_to_try.extend([
            SERVICE_PATHS["dtm"],
            SERVICE_PATHS["hoydedata"],
        ])

        step_x = dx / max(1, grid_size - 1)
        step_y = dy / max(1, grid_size - 1)

        points_x, points_y, points_z = [], [], []

        for path in paths_to_try:
            points_x, points_y, points_z = [], [], []
            
            for i in range(grid_size):
                for j in range(grid_size):
                    px = minx + i * step_x
                    py = miny + j * step_y

                    try:
                        result = self._rest_get(
                            path,
                            "identify",
                            params={
                                "geometry": f"{px},{py}",
                                "geometryType": "esriGeometryPoint",
                                "sr": str(srid),
                                "layers": "all",
                                "tolerance": "1",
                                "mapExtent": f"{minx},{miny},{maxx},{maxy}",
                                "imageDisplay": "100,100,96",
                                "returnGeometry": "false",
                            },
                            timeout=8,
                        )
                        
                        results_list = result.get("results", [])
                        for r in results_list:
                            val = r.get("attributes", {}).get("Pixel Value")
                            if val is None:
                                val = r.get("value")
                            if val is not None:
                                try:
                                    z = float(val)
                                    if -500 < z < 3000:
                                        points_x.append(px)
                                        points_y.append(py)
                                        points_z.append(z)
                                except (ValueError, TypeError):
                                    continue
                    except Exception:
                        continue

            if len(points_z) >= 3:
                break

        if len(points_z) < 3:
            return None, {"source": "Geodata Online DTM", "error": "For få terrengpunkter hentet"}

        x = np.array(points_x)
        y = np.array(points_y)
        z = np.array(points_z)

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
            "slope_pct": float(np.sqrt(a**2 + b**2) * 100.0),
            "grade_ew_pct": float(a * 100.0),
            "grade_ns_pct": float(b * 100.0),
            "rmse_m": rmse,
            "point_count": len(points_z),
            "source": "Geodata Online DTM",
        }

        return terrain, {"source": "Geodata Online DTM", "point_count": len(points_z)}

    # --- 5. Matrikkel: Eiendomsinfo ---
    def fetch_eiendom_info(
        self,
        kommunenr: str,
        gnr: int,
        bnr: int,
        srid: int = 25833,
        service_path: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Hent eiendomsinformasjon fra matrikkeltjenesten."""
        paths_to_try = []
        if service_path:
            paths_to_try.append(service_path)
        paths_to_try.append(SERVICE_PATHS["matrikkel"])

        for path in paths_to_try:
            for layer_id in range(5):
                try:
                    where = f"KOMMUNENR='{kommunenr}' AND GARDSNR={gnr} AND BRUKSNR={bnr}"
                    data = self.query_layer(
                        service_path=path,
                        layer_id=layer_id,
                        where=where,
                        out_sr=srid,
                        return_geometry=True,
                        result_record_count=5,
                    )
                    features = data.get("features", [])
                    if features:
                        return features[0].get("attributes", {}), {
                            "source": f"Geodata Online Matrikkel ({path}, lag {layer_id})",
                        }
                except Exception:
                    continue

        return {}, {"source": "Geodata Online Matrikkel", "error": "Ikke funnet"}


# ---------------------------------------------------------------------------
# Hjelpefunksjoner for integrasjon med eksisterende modul
# ---------------------------------------------------------------------------

def geodata_buildings_to_neighbors(
    buildings: List[Dict[str, Any]],
    site_polygon: Optional[Any] = None,
    max_distance_m: float = 250.0,
) -> List[Dict[str, Any]]:
    """Konverter FKB-bygningsdata til nabolisten som modulen forventer."""
    if not HAS_SHAPELY or site_polygon is None:
        return buildings

    neighbors = []
    for bld in buildings:
        poly = bld.get("polygon")
        if poly is None:
            continue
        if poly.intersects(site_polygon):
            continue
        dist = poly.distance(site_polygon)
        if dist > max_distance_m:
            continue
        neighbors.append({
            "polygon": poly.buffer(0),
            "height_m": float(bld.get("height_m", 9.0)),
            "source": "Geodata Online FKB",
            "distance_m": float(dist),
            "building_type": bld.get("building_type", "Ukjent"),
        })
    neighbors.sort(key=lambda n: n["distance_m"])
    return neighbors


def regulation_to_parsed_hints(reg_data: Dict[str, Any]) -> Dict[str, float]:
    """
    Konverter reguleringsdata fra Geodata Online til formatet
    parse_regulation_hints() returnerer, slik at det kan
    brukes direkte som override i UI-feltene.
    """
    hints: Dict[str, float] = {}
    if reg_data.get("utnyttelsesgrad"):
        hints["max_bya_pct"] = float(reg_data["utnyttelsesgrad"])
    if reg_data.get("maks_hoyde_m"):
        hints["max_height_m"] = float(reg_data["maks_hoyde_m"])
    if reg_data.get("maks_etasjer"):
        hints["max_floors"] = float(reg_data["maks_etasjer"])
    return hints
