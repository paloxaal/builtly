import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
import base64
from datetime import datetime
import tempfile
import re
import json
import io
import math
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from pathlib import Path

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Akustikk (RIAku) | Builtly", layout="wide", initial_sidebar_state="collapsed")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()

try:
    import fitz  
except ImportError:
    fitz = None

# --- Auth integration (for saving reports to user account) ---
try:
    import builtly_auth
    _HAS_AUTH = True
except ImportError:
    _HAS_AUTH = False

if _HAS_AUTH:
    if not st.session_state.get("user_authenticated"):
        builtly_auth.try_restore_from_browser()
    elif st.session_state.get("_sb_access_token"):
        builtly_auth.restore_session()

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

import streamlit.components.v1 as components


# ═══════════════════════════════════════════════════════════════════════
# DETERMINISTISK AKUSTISK BEREGNINGSMOTOR
# ═══════════════════════════════════════════════════════════════════════

class AcousticEngine:
    """
    Deterministisk beregningsmotor for akustikkanalyse iht. norske krav.
    
    Beregner:
    - T-1442/2016 støysoneklassifisering
    - Krav til fasadeisolasjon (R'w + Ctr) basert på utendørs Lden
    - Innendørs lydnivå fra utendørs Lden gitt fasadedata
    - Screening-effekter (støyskjerm, rekkverk, absorbent)
    - NS 8175 lydklasse-compliance per rom
    - Balkongstøy med tiltak
    """

    # --- T-1442/2016 Grenseverdier ---
    T1442_LIMITS = {
        "vei":  {"uteareal_lden": 55, "soverom_natt_l5af": 70},
        "bane": {"uteareal_lden": 58, "soverom_natt_l5af": 75},
        "fly":  {"uteareal_lden": 52, "soverom_natt_l5af": 80},
    }
    
    # --- NS 8175 Lydklasse - Innendørs krav (Lden) ---
    NS8175_INDOOR = {
        "A": {"soverom": 25, "stue_kjokken": 30, "max_natt_soverom": 40},
        "B": {"soverom": 28, "stue_kjokken": 32, "max_natt_soverom": 43},
        "C": {"soverom": 30, "stue_kjokken": 35, "max_natt_soverom": 45},
        "D": {"soverom": 35, "stue_kjokken": 40, "max_natt_soverom": 50},
    }

    # --- Støysonekategorier ---
    ZONES = {
        "gul_vei":  (55, 65),   # Gul sone veitrafikk
        "rod_vei":  (65, 999),  # Rød sone veitrafikk
        "gul_bane": (58, 68),
        "rod_bane": (68, 999),
    }

    # --- Standard fasadedata (konservative antakelser) ---
    FACADE_DEFAULTS = {
        "standard_vegg_rw": 45,           # Typisk Rw for standard yttervegg
        "standard_vindu_rw_ctr": 30,      # Typisk Rw+Ctr for standard vindu  
        "vindusandel": 0.25,              # 25% vindusareal i fasade
        "safety_margin": 3,               # Sikkerhetsfaktor dB
    }

    @staticmethod
    def classify_zone(lden: float, kilde: str = "vei") -> dict:
        """Klassifiser et punkt iht. T-1442 støysoner."""
        if kilde == "vei":
            if lden >= 65:
                return {"zone": "rod", "color": "#ef4444", "text": "Rød sone", 
                        "desc": "Normalt uegnet for støyfølsomt bruksformål"}
            elif lden >= 55:
                return {"zone": "gul", "color": "#f59e0b", "text": "Gul sone",
                        "desc": "Vurderingssone — kan tillates med avbøtende tiltak"}
            else:
                return {"zone": "gronn", "color": "#22c55e", "text": "Grønn sone",
                        "desc": "Tilfredsstillende støynivå"}
        elif kilde == "bane":
            if lden >= 68:
                return {"zone": "rod", "color": "#ef4444", "text": "Rød sone", 
                        "desc": "Normalt uegnet for støyfølsomt bruksformål"}
            elif lden >= 58:
                return {"zone": "gul", "color": "#f59e0b", "text": "Gul sone",
                        "desc": "Vurderingssone"}
            else:
                return {"zone": "gronn", "color": "#22c55e", "text": "Grønn sone",
                        "desc": "Tilfredsstillende støynivå"}
        return {"zone": "ukjent", "color": "#94a3b8", "text": "Ukjent", "desc": ""}

    @staticmethod
    def required_facade_reduction(lden_outdoor: float, target_indoor: float, 
                                   safety_margin: float = 3.0) -> float:
        """
        Beregn krevd total lydreduksjon gjennom fasade.
        
        R'w_req = Lden_ute - Lden_inne_krav + sikkerhet
        
        Merk: Forenklet beregning. Detaljprosjektering krever
        full beregning iht. NS-EN 12354-3 med spektrumkorreksjon.
        """
        return lden_outdoor - target_indoor + safety_margin

    @staticmethod
    def required_window_rw_ctr(lden_outdoor: float, target_indoor: float,
                                wall_rw: float = 50, window_fraction: float = 0.25,
                                safety_margin: float = 3.0) -> float:
        """
        Estimere krevd Rw+Ctr for vinduer basert på samlet fasadekrav.
        
        Forenklet beregning basert på vegg/vindu-arealandeler.
        Ctr-korreksjon for veitrafikkstøy er typisk -5 til -8 dB.
        
        Bruker forenklet sammensatt lydisolasjon:
        R'_total = -10*log10( f_vindu * 10^(-Rw_vindu/10) + f_vegg * 10^(-Rw_vegg/10) )
        """
        r_total_req = lden_outdoor - target_indoor + safety_margin
        
        # Løs for Rw_vindu gitt R_total, Rw_vegg og vindusandel
        # 10^(-R_total/10) = f*10^(-Rw_vindu/10) + (1-f)*10^(-Rw_vegg/10)
        target_total = 10 ** (-r_total_req / 10)
        wall_contrib = (1 - window_fraction) * (10 ** (-wall_rw / 10))
        
        window_target = target_total - wall_contrib
        if window_target <= 0:
            # Veggen alene gir nok - men vinduer må fortsatt ha rimelig isolasjon
            return max(30, r_total_req - 5)
        
        rw_window = -10 * math.log10(window_target / window_fraction)
        return round(rw_window, 0)

    @staticmethod
    def screening_effect(barrier_height: float, source_dist: float, 
                          receiver_height: float = 1.5, barrier_dist: float = 2.0) -> float:
        """
        Estimere skjermingseffekt av støyskjerm/rekkverk (Maekawa-metoden forenklet).
        
        Args:
            barrier_height: Skjermhøyde over bakkenivå (m)
            source_dist: Avstand fra kilde til skjerm (m)
            receiver_height: Mottakerhøyde over dekke (m)
            barrier_dist: Avstand fra skjerm til mottaker (m)
        
        Returns:
            Estimert reduksjon i dB (typisk 5-15 dB for realistiske scenarier)
        """
        # Effektiv skjermhøyde over siktlinje
        h_eff = barrier_height - receiver_height * (source_dist / (source_dist + barrier_dist))
        
        if h_eff <= 0:
            return 0  # Skjermen er for lav
        
        # Fresnel-tall (forenklet for 500 Hz)
        wavelength = 0.68  # m, ved 500 Hz
        delta = math.sqrt(h_eff**2 + barrier_dist**2) + math.sqrt(h_eff**2 + source_dist**2) - (source_dist + barrier_dist)
        N = 2 * delta / wavelength
        
        if N <= 0:
            return 0
        
        # Maekawa-formel
        reduction = 10 * math.log10(3 + 20 * N)
        return min(reduction, 20)  # Max ~20 dB for enkelt skjerm

    @staticmethod
    def balcony_noise_with_measures(lden_facade: float, 
                                     tett_rekkverk: bool = False,
                                     rekkverk_height: float = 1.3,
                                     absorbent_himling: bool = False,
                                     skjermvegg: bool = False,
                                     skjermvegg_height: float = 2.2) -> float:
        """
        Estimere støynivå på balkong med ulike tiltak.
        
        Typiske reduksjoner (basert på Brekke & Strand erfaringsdata):
        - Tett rekkverk (1.3m): 3-5 dB for punkt bak rekkverk
        - Absorbent i himling: 2-3 dB (reduserer refleksjoner)
        - Tett skjermvegg (2.2m): 5-8 dB (avhengig av orientering)
        """
        reduction = 0
        if tett_rekkverk:
            # Rekkverk gir mest effekt for punkt lavere enn rekkverk
            reduction += min(5, rekkverk_height * 3)
        if absorbent_himling:
            reduction += 2.5
        if skjermvegg:
            reduction += min(8, skjermvegg_height * 3)
        
        return round(lden_facade - reduction, 0)

    @staticmethod
    def indoor_from_outdoor(lden_outdoor: float, facade_rw: float) -> float:
        """Estimere innendørs Lden gitt utendørs nivå og fasadeisolasjon."""
        return lden_outdoor - facade_rw

    @classmethod
    def full_facade_analysis(cls, lden_outdoor: float, lydklasse: str = "C",
                              kilde: str = "vei", vindusandel: float = 0.25) -> dict:
        """
        Komplett fasadeanalyse for et punkt.
        
        Returns dict med alle relevante beregninger.
        """
        zone = cls.classify_zone(lden_outdoor, kilde)
        indoor_req = cls.NS8175_INDOOR.get(lydklasse, cls.NS8175_INDOOR["C"])
        
        # Krevd reduksjon for soverom (strengeste krav)
        req_reduction_soverom = cls.required_facade_reduction(
            lden_outdoor, indoor_req["soverom"])
        req_reduction_stue = cls.required_facade_reduction(
            lden_outdoor, indoor_req["stue_kjokken"])
        
        # Krevd vindu Rw+Ctr
        req_window_soverom = cls.required_window_rw_ctr(
            lden_outdoor, indoor_req["soverom"], 
            wall_rw=50, window_fraction=vindusandel)
        req_window_stue = cls.required_window_rw_ctr(
            lden_outdoor, indoor_req["stue_kjokken"],
            wall_rw=50, window_fraction=vindusandel)
        
        # T-1442 stille-side sjekk
        is_stille_side = lden_outdoor <= cls.T1442_LIMITS.get(kilde, {}).get("uteareal_lden", 55)
        
        return {
            "lden_outdoor": lden_outdoor,
            "zone": zone,
            "lydklasse": lydklasse,
            "indoor_req_soverom": indoor_req["soverom"],
            "indoor_req_stue": indoor_req["stue_kjokken"],
            "req_reduction_soverom": round(req_reduction_soverom, 0),
            "req_reduction_stue": round(req_reduction_stue, 0),
            "req_window_rw_ctr_soverom": round(req_window_soverom, 0),
            "req_window_rw_ctr_stue": round(req_window_stue, 0),
            "req_wall_rw": round(req_reduction_soverom + 10, 0),  # Vegg bør ha ~10 dB margin over vindu
            "is_stille_side": is_stille_side,
            "balansert_ventilasjon_paakrevd": lden_outdoor > 55,
            "luftevindu_mulig": lden_outdoor <= 55,
        }

    @classmethod
    def generate_facade_table(cls, facade_data: list, lydklasse: str = "C",
                               kilde: str = "vei") -> pd.DataFrame:
        """
        Generer komplett fasadetabell fra AI-ekstraherte verdier.
        
        Args:
            facade_data: Liste av dicts med {"bygg", "fasade", "lden", "etasje"}
        """
        rows = []
        for fd in facade_data:
            lden = fd.get("lden", 55)
            analysis = cls.full_facade_analysis(lden, lydklasse, kilde)
            rows.append({
                "Bygg": fd.get("bygg", "?"),
                "Fasade": fd.get("fasade", "?"),
                "Etasje": fd.get("etasje", "alle"),
                "Lden (dB)": lden,
                "Støysone": analysis["zone"]["text"],
                "Krav Rw vegg": int(analysis["req_wall_rw"]),
                "Krav Rw+Ctr vindu (soverom)": int(analysis["req_window_rw_ctr_soverom"]),
                "Krav Rw+Ctr vindu (stue)": int(analysis["req_window_rw_ctr_stue"]),
                "Stille side": "Ja" if analysis["is_stille_side"] else "Nei",
                "Bal.vent. påkrevd": "Ja" if analysis["balansert_ventilasjon_paakrevd"] else "Nei",
            })
        return pd.DataFrame(rows)

    @classmethod
    def reguleringsplan_compliance(cls, facade_data: list, 
                                    reg_bestemmelser: dict = None) -> list:
        """
        Vurder samsvar med reguleringsplanbestemmelser.
        
        Standard reguleringsplanbestemmelser (typisk for støyutsatt bolig):
        - Alle boenheter skal ha tilgang til uterom på stille side (<55 dB)
        - Gul sone: stille side + minst ett soverom mot stille side
        - Rød sone: gjennomgående, halvparten av oppholdsrom mot stille side
        """
        if reg_bestemmelser is None:
            reg_bestemmelser = {
                "stille_side_krav": True,
                "stille_side_limit": 55,
                "gul_soverom_stille": True,
                "rod_gjennomgaaende": True,
                "rod_halvpart_stille": True,
            }
        
        issues = []
        for fd in facade_data:
            lden = fd.get("lden", 55)
            bygg = fd.get("bygg", "?")
            fasade = fd.get("fasade", "?")
            
            if lden > 65:
                issues.append({
                    "bygg": bygg,
                    "fasade": fasade,
                    "lden": lden,
                    "severity": "KRITISK",
                    "krav": f"Rød sone ({lden} dB): Boenhet skal være gjennomgående. "
                            f"Min. halvparten av oppholdsrom mot stille side. "
                            f"Alle soverom mot stille side.",
                })
            elif lden > 55:
                issues.append({
                    "bygg": bygg,
                    "fasade": fasade,
                    "lden": lden,
                    "severity": "VIKTIG",
                    "krav": f"Gul sone ({lden} dB): Boenhet skal ha stille side (<55 dB). "
                            f"Minst ett soverom mot stille side.",
                })
        
        return issues


# ═══════════════════════════════════════════════════════════════════════
# STØYKART WMS/ArcGIS (beholdt fra original)
# ═══════════════════════════════════════════════════════════════════════

STOY_DB_RANGES = {
    "Lden 55-60 dB": {"min": 55, "max": 60, "color": "#22c55e"},
    "Lden 60-65 dB": {"min": 60, "max": 65, "color": "#f59e0b"},
    "Lden 65-70 dB": {"min": 65, "max": 70, "color": "#ef4444"},
    "Lden 70-75 dB": {"min": 70, "max": 75, "color": "#dc2626"},
    "Lden >75 dB":   {"min": 75, "max": 999, "color": "#991b1b"},
}


def _latlon_to_utm33(lat: float, lon: float) -> tuple:
    """Convert lat/lon (WGS84) to UTM33N (EPSG:25833) for Norwegian maps."""
    k0, a, e, lon0 = 0.9996, 6378137.0, 0.0818192, 15.0
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    lon0_rad = math.radians(lon0)
    N = a / math.sqrt(1 - e**2 * math.sin(lat_rad)**2)
    T = math.tan(lat_rad)**2
    C = (e**2 / (1 - e**2)) * math.cos(lat_rad)**2
    A_val = (lon_rad - lon0_rad) * math.cos(lat_rad)
    M = a * ((1 - e**2/4 - 3*e**4/64) * lat_rad
            - (3*e**2/8 + 3*e**4/32) * math.sin(2*lat_rad)
            + (15*e**4/256) * math.sin(4*lat_rad))
    easting = k0 * N * (A_val + (1-T+C) * A_val**3/6) + 500000
    northing = k0 * (M + N * math.tan(lat_rad) * (A_val**2/2))
    return easting, northing


def _fetch_wms_image(base_url, layers_to_try, bbox_25833, width=800, height=600, auth=None, extra_params=None):
    xmin, ymin, xmax, ymax = bbox_25833
    for layer in layers_to_try:
        params = {"service": "WMS", "request": "GetMap", "version": "1.1.1",
                  "layers": layer, "styles": "", "srs": "EPSG:25833",
                  "bbox": f"{xmin},{ymin},{xmax},{ymax}",
                  "width": str(width), "height": str(height),
                  "format": "image/png", "transparent": "true"}
        if extra_params: params.update(extra_params)
        try:
            resp = requests.get(base_url, params=params, timeout=15, auth=auth)
            if resp.status_code == 200 and len(resp.content) > 2000:
                if b"ServiceException" not in resp.content[:1000]:
                    return Image.open(io.BytesIO(resp.content)).convert("RGBA"), layer
        except Exception:
            continue
    return None, None


def _get_gdo_client():
    """Hent en GeodataOnlineClient instans (cached)."""
    try:
        from geodata_client import GeodataOnlineClient, GEOMAP_DOKFORURENSNING_MS
        gdo = GeodataOnlineClient()
        if gdo.is_available():
            return gdo
    except ImportError:
        pass
    return None


DOK_FORURENSNING_URL = "https://services.geodataonline.no/arcgis/rest/services/Geomap_UTM33_EUREF89/GeomapDOKForurensning/MapServer"


def _fetch_arcgis_export(service_url, bbox_25833, width=800, height=600, 
                          token=None, layers=None, transparent=True):
    """Fetch image from ArcGIS MapServer export med full kontroll."""
    xmin, ymin, xmax, ymax = bbox_25833
    params = {
        "bbox": f"{xmin},{ymin},{xmax},{ymax}",
        "bboxSR": "25833", "imageSR": "25833",
        "size": f"{width},{height}",
        "format": "png32" if transparent else "png",
        "transparent": "true" if transparent else "false",
        "f": "image",
    }
    if token: params["token"] = token
    if layers: params["layers"] = layers
    try:
        resp = requests.get(f"{service_url}/export", params=params, timeout=20)
        if resp.status_code == 200 and len(resp.content) > 500:
            if resp.content[:1] == b'{':
                return None  # JSON feilrespons
            ctype = resp.headers.get("content-type", "")
            if "image" in ctype or resp.content[:4] in (b"\x89PNG", b"\xff\xd8\xff"):
                return Image.open(io.BytesIO(resp.content)).convert("RGBA" if transparent else "RGB")
    except Exception:
        pass
    return None


def fetch_stoykart_image(lat, lon, kilde="vei", width=800, height=600, buffer_m=300):
    """Hent støykart fra Geodata Online DOK Forurensning."""
    if not HAS_REQUESTS: return None, "requests-biblioteket mangler"
    try:
        easting, northing = _latlon_to_utm33(lat, lon)
        bbox = (easting - buffer_m, northing - buffer_m, easting + buffer_m, northing + buffer_m)
    except Exception:
        return None, "Koordinatkonvertering feilet"

    errors = []
    gdo = _get_gdo_client()
    
    if gdo:
        try:
            token = gdo.get_token()
            
            # Steg 1: Finn støy-lag i DOK Forurensning
            stoey_layer_ids = []
            try:
                layers = gdo.list_layers("Geomap_UTM33_EUREF89/GeomapDOKForurensning/MapServer", leaf_only=False)
                for lyr in layers:
                    name = (lyr.get("name") or "").lower()
                    if any(w in name for w in ["støy", "stoy", "noise", "lden", "veg", "bane", "fly"]):
                        stoey_layer_ids.append(str(lyr.get("id")))
            except Exception:
                pass
            
            # Steg 2: Hent støy-overlay med spesifikke lag (eller alle)
            noise_overlay = None
            if stoey_layer_ids:
                layer_spec = "show:" + ",".join(stoey_layer_ids)
                noise_overlay = _fetch_arcgis_export(DOK_FORURENSNING_URL, bbox, width, height, 
                                                       token=token, layers=layer_spec, transparent=True)
            
            if noise_overlay is None:
                # Fallback: alle lag
                noise_overlay = _fetch_arcgis_export(DOK_FORURENSNING_URL, bbox, width, height,
                                                       token=token, transparent=True)
            
            if noise_overlay is None:
                # Siste fallback: prøv med show:0-20
                noise_overlay = _fetch_arcgis_export(DOK_FORURENSNING_URL, bbox, width, height,
                                                       token=token, 
                                                       layers="show:0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20",
                                                       transparent=True)
            
            if noise_overlay is not None:
                # Sjekk at overlay har innhold
                alpha = np.array(noise_overlay.split()[3])
                has_content = np.count_nonzero(alpha > 10) > 50
                
                if has_content:
                    # Hent bakgrunnskart (gråtone) for compositing
                    basemap_url = "https://services.geodataonline.no/arcgis/rest/services/Geocache_UTM33_EUREF89/GeocacheGraatone/MapServer"
                    basemap = _fetch_arcgis_export(basemap_url, bbox, width, height, 
                                                     token=token, transparent=False)
                    if basemap is not None:
                        basemap_rgba = basemap.convert("RGBA")
                        result = Image.alpha_composite(basemap_rgba, noise_overlay)
                        return result.convert("RGB"), None
                    
                    # Fallback: støy på lys grå bakgrunn
                    bg = Image.new("RGBA", noise_overlay.size, (245, 245, 240, 255))
                    result = Image.alpha_composite(bg, noise_overlay)
                    return result.convert("RGB"), None
                else:
                    errors.append("DOK Forurensning: bildet er transparent — ingen støydata i dette området")
            else:
                errors.append("DOK Forurensning: MapServer returnerte ingen data")
                
        except Exception as e:
            errors.append(f"DOK Forurensning: {str(e)[:100]}")
    else:
        errors.append("Geodata Online er ikke tilgjengelig — sjekk GEODATA_ONLINE_USER/PASS")

    # --- SEKUNDÆR: Geonorge WMS ---
    gdo_user = os.environ.get("GEODATA_ONLINE_USER", "")
    gdo_pass = os.environ.get("GEODATA_ONLINE_PASS", "")
    gdo_auth = (gdo_user, gdo_pass) if gdo_user and gdo_pass else None

    wms_sources = {
        "vei": [("https://wms.geonorge.no/skwms1/wms.stoykartleggingveg",
                 ["Støyvarselkart_Lden", "stoyvarselkart_lden", "Støyvarselkart", "0", "1", "2"])],
        "bane": [("https://wms.geonorge.no/skwms1/wms.stoysonerjernbanenett",
                  ["Støysoner", "stoysoner", "0", "1"])],
        "fly": [("https://wms.geonorge.no/skwms1/wms.stoysonerlufthavn",
                 ["Støysoner", "stoysoner", "0", "1"])],
        "industri": [("https://wms.geonorge.no/skwms1/wms.stoykartleggingveg",
                      ["Støyvarselkart_Lden", "0"])],
    }

    for wms_url, layer_candidates in wms_sources.get(kilde.lower(), []):
        img, hit_layer = _fetch_wms_image(wms_url, layer_candidates, bbox, width, height, auth=gdo_auth)
        if img:
            bg = Image.new("RGBA", img.size, (245, 245, 240, 255))
            result = Image.alpha_composite(bg, img)
            return result.convert("RGB"), None
        errors.append(f"WMS {wms_url.split('/')[-1]}: ingen treff")

    return None, f"Kunne ikke hente støykart: {'; '.join(errors)}"


def fetch_stoykart_contours(lat, lon, kilde="vei", buffer_m=300):
    """Hent støykontur-data fra DOK Forurensning."""
    if not HAS_REQUESTS: return []
    try:
        easting, northing = _latlon_to_utm33(lat, lon)
    except Exception:
        return []

    gdo = _get_gdo_client()
    if not gdo: return []
    
    try:
        token = gdo.get_token()
    except Exception:
        return []

    params = {
        "geometry": f"{easting-buffer_m},{northing-buffer_m},{easting+buffer_m},{northing+buffer_m}",
        "geometryType": "esriGeometryEnvelope", "inSR": "25833", "outSR": "25833",
        "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "returnGeometry": "false", "f": "json",
        "token": token,
    }
    contours = []
    for layer_id in range(0, 25):
        try:
            resp = requests.get(f"{DOK_FORURENSNING_URL}/{layer_id}/query", params=params, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                for feat in data.get("features", []):
                    attrs = feat.get("attributes", {})
                    db_val = None
                    for key in ["DB_LOW", "db_low", "Lden", "dB", "LDEN", "lden",
                                "DB_HIGH", "db_high", "StoyNiva", "stoyniva"]:
                        if key in attrs and attrs[key] is not None:
                            db_val = attrs[key]; break
                    if db_val is not None:
                        contours.append({"layer": layer_id, "db": str(db_val),
                                         "name": attrs.get("NAVN", attrs.get("navn", "")), "type": kilde})
        except Exception:
            continue
    return contours


# ═══════════════════════════════════════════════════════════════════════
# INTERAKTIV AKUSTIKK-EDITOR (beholdt fra original)
# ═══════════════════════════════════════════════════════════════════════

def render_acoustic_editor(images_with_markers, bridge_label: str, component_key: str):
    if not images_with_markers: return
    img_data = images_with_markers[0]
    image = img_data["image"]
    markers = img_data.get("markers", [])
    
    buf = io.BytesIO()
    thumb = image.copy()
    if max(thumb.size) > 1400:
        ratio = 1400 / max(thumb.size)
        thumb = thumb.resize((int(thumb.width * ratio), int(thumb.height * ratio)), Image.LANCZOS)
    thumb.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    img_uri = f"data:image/png;base64,{b64}"
    bl_escaped = bridge_label.replace("'", "\\'")
    marker_json = json.dumps(markers, ensure_ascii=False)
    
    html = f"""
    <div style="font-family:system-ui;color:#e2e8f0">
      <div style="display:flex;gap:3px;padding:6px 8px;background:#0a1929;border:1px solid #1a2a3a;border-radius:10px 10px 0 0;flex-wrap:wrap;align-items:center">
        <button onclick="AE.setTool('select')" id="at_select" class="at active" style="--tc:#38bdf8">Velg/Flytt</button>
        <span style="width:1px;height:16px;background:#1a2a3a"></span>
        <button onclick="AE.setTool('db_darkred')" id="at_db_darkred" class="at" style="--tc:#991b1b">&#9679; &gt;70 dB</button>
        <button onclick="AE.setTool('db_red')" id="at_db_red" class="at" style="--tc:#ef4444">&#9679; 65-70 dB</button>
        <button onclick="AE.setTool('db_yellow')" id="at_db_yellow" class="at" style="--tc:#f59e0b">&#9679; 55-65 dB</button>
        <button onclick="AE.setTool('db_green')" id="at_db_green" class="at" style="--tc:#22c55e">&#9679; &lt;55 dB</button>
        <span style="width:1px;height:16px;background:#1a2a3a"></span>
        <button onclick="AE.setTool('zone')" id="at_zone" class="at" style="--tc:#a78bfa">&#9645; Støysone</button>
        <button onclick="AE.setTool('barrier')" id="at_barrier" class="at" style="--tc:#94a3b8">&#9644; Støyskjerm</button>
        <button onclick="AE.setTool('facade')" id="at_facade" class="at" style="--tc:#f59e0b">&#9644; Utsatt fasade</button>
        <span style="flex:1"></span>
        <button onclick="AE.deleteSelected()" style="background:rgba(239,68,68,0.15);color:#ef4444;border-color:rgba(239,68,68,0.3)">Slett</button>
        <button onclick="AE.save()" style="background:#38bdf8;color:#06111a;font-weight:700;border-color:#38bdf8">Lagre</button>
      </div>
      <canvas id="AC" style="width:100%;display:block;background:#0d1b2a;border:1px solid #1a2a3a;border-top:none;cursor:crosshair"></canvas>
      <div style="display:flex;gap:8px;padding:5px 10px;background:#0a1929;border:1px solid #1a2a3a;border-top:none;border-radius:0 0 10px 10px;align-items:center">
        <span id="AE_status" style="font-size:10px;color:#475569;font-family:monospace;flex:1"></span>
        <label style="font-size:10px;color:#64748b">dB-verdi:</label>
        <input id="AE_db" type="text" value="65" style="background:#1a2a3a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;padding:2px 8px;font-size:11px;width:80px" oninput="AE.updateDb(this.value)"/>
      </div>
      <textarea id="AE_export" style="display:none"></textarea>
    </div>
    <style>.at{{background:rgba(30,41,59,0.8);color:#94a3b8;border:1px solid #334155;border-radius:5px;padding:3px 8px;font-size:11px;cursor:pointer;font-weight:500}}.at:hover{{background:rgba(56,189,248,0.1);color:#e2e8f0}}.at.active{{background:var(--tc,#38bdf8);color:#fff;font-weight:700;border-color:var(--tc)}}</style>
    <script>
    window.AE=(function(){{
      var BRIDGE_LABEL='{bl_escaped}';
      var cv=document.getElementById('AC'),ctx=cv.getContext('2d'),sts=document.getElementById('AE_status'),dbIn=document.getElementById('AE_db'),ex=document.getElementById('AE_export');
      var img=new Image();var els={marker_json};
      els=els.map(function(m,i){{m.id='m'+i;return m}});
      var tool='select',sel=-1,drag=null,sp=null,IW=0,IH=0;
      var TMAP={{db_darkred:['circle','#991b1b','75'],db_red:['circle','#ef4444','67'],db_yellow:['circle','#f59e0b','60'],db_green:['circle','#22c55e','50'],zone:['rect','rgba(168,85,247,0.3)','Støysone'],barrier:['line','#94a3b8','Støyskjerm'],facade:['line','#f59e0b','Utsatt fasade']}};
      function uid(){{return Math.random().toString(36).slice(2,10)}}
      function dist(a,b,c,d){{return Math.sqrt((c-a)*(c-a)+(d-b)*(d-b))}}
      function render(){{if(!img.complete||IW<1)return;ctx.clearRect(0,0,cv.width,cv.height);ctx.drawImage(img,0,0,IW,IH);for(var i=0;i<els.length;i++){{var e=els[i],isSel=i===sel;var x=(e.x_pct/100)*IW,y=(e.y_pct/100)*IH;var c=e.color||'#ef4444';ctx.lineWidth=isSel?4:2.5;ctx.strokeStyle=c;if(e.type==='circle'||!e.type){{var r=Math.max(8,IW*0.015);ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.stroke();if(isSel){{ctx.fillStyle='rgba(255,255,255,0.15)';ctx.fill()}}var txt=(e.db||'??');ctx.font='bold '+Math.max(8,IW*0.01)+'px system-ui';var tw=ctx.measureText(txt).width;ctx.fillStyle=c;ctx.fillRect(x-tw/2-2,y-5,tw+4,11);ctx.fillStyle='#fff';ctx.fillText(txt,x-tw/2,y+4);if(e.label){{ctx.font=Math.max(7,IW*0.008)+'px system-ui';ctx.fillStyle=c;ctx.fillText(e.label,x-r,y+r+10)}}}}else if(e.type==='rect'){{var w=(e.w_pct||10)/100*IW,h=(e.h_pct||8)/100*IH;ctx.fillStyle=c;ctx.fillRect(x,y,w,h);ctx.strokeStyle='#a78bfa';ctx.lineWidth=isSel?3:1.5;ctx.strokeRect(x,y,w,h);ctx.font='bold 11px system-ui';ctx.fillStyle='#a78bfa';ctx.fillText(e.label||'Sone',x+4,y+14)}}else if(e.type==='line'){{var x2=(e.x2_pct||e.x_pct+10)/100*IW,y2=(e.y2_pct||e.y_pct)/100*IH;ctx.strokeStyle=c;ctx.lineWidth=isSel?5:3;ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x2,y2);ctx.stroke();ctx.font='bold 10px system-ui';ctx.fillStyle=c;ctx.fillText(e.label||'',((x+x2)/2),((y+y2)/2)-6)}}}}sts.textContent=els.length+' markører | '+(tool==='select'?'Velg/flytt':tool);ex.value=JSON.stringify(els,null,2)}}
      function resize(){{var mw=cv.parentElement?cv.parentElement.clientWidth:900;if(mw<100)mw=900;var r=Math.min(1,mw/Math.max(img.naturalWidth||img.width||900,1));IW=Math.max(100,Math.round((img.naturalWidth||img.width||900)*r));IH=Math.max(100,Math.round((img.naturalHeight||img.height||600)*r));cv.width=IW;cv.height=IH;render()}}
      function gP(e){{var r=cv.getBoundingClientRect();return{{x:(e.clientX-r.left)*(cv.width/r.width),y:(e.clientY-r.top)*(cv.height/r.height)}}}}
      function hitTest(x,y){{for(var i=els.length-1;i>=0;i--){{var e=els[i],ex=(e.x_pct/100)*IW,ey=(e.y_pct/100)*IH;if(dist(x,y,ex,ey)<IW*0.025)return i;if(e.type==='rect'){{var w=(e.w_pct||10)/100*IW,h=(e.h_pct||8)/100*IH;if(x>=ex&&x<=ex+w&&y>=ey&&y<=ey+h)return i}}}}return -1}}
      cv.addEventListener('mousedown',function(ev){{var p=gP(ev);sp=p;if(tool==='select'){{sel=hitTest(p.x,p.y);if(sel>=0){{drag='move';dbIn.value=els[sel].db||els[sel].label||''}}else{{dbIn.value=''}}}}else{{var tm=TMAP[tool];if(!tm)return;var xp=p.x/IW*100,yp=p.y/IH*100;if(tm[0]==='circle'){{els.push({{id:uid(),type:'circle',x_pct:xp,y_pct:yp,db:dbIn.value||tm[2],color:tm[1],label:''}})}}else if(tm[0]==='rect'){{els.push({{id:uid(),type:'rect',x_pct:xp,y_pct:yp,w_pct:10,h_pct:8,color:tm[1],label:tm[2]}});drag='drawRect'}}else if(tm[0]==='line'){{els.push({{id:uid(),type:'line',x_pct:xp,y_pct:yp,x2_pct:xp,y2_pct:yp,color:tm[1],label:tm[2]}});drag='drawLine'}}sel=els.length-1}}render()}});
      cv.addEventListener('mousemove',function(ev){{if(sel<0||!drag||!sp)return;var p=gP(ev),el=els[sel];if(drag==='move'){{var dx=(p.x-sp.x)/IW*100,dy=(p.y-sp.y)/IH*100;el.x_pct=Math.max(0,Math.min(100,(el.x_pct||0)+dx));el.y_pct=Math.max(0,Math.min(100,(el.y_pct||0)+dy));sp=p}}else if(drag==='drawRect'&&el.type==='rect'){{el.w_pct=(p.x/IW*100)-(el.x_pct||0);el.h_pct=(p.y/IH*100)-(el.y_pct||0)}}else if(drag==='drawLine'&&el.type==='line'){{el.x2_pct=p.x/IW*100;el.y2_pct=p.y/IH*100}}render()}});
      window.addEventListener('mouseup',function(){{drag=null;render()}});
      document.addEventListener('keydown',function(ev){{if(ev.target.tagName==='INPUT'||ev.target.tagName==='TEXTAREA')return;if((ev.key==='Delete'||ev.key==='Backspace')&&sel>=0){{els.splice(sel,1);sel=-1;render()}}}});
      img.onload=function(){{resize()}};img.src='{img_uri}';window.addEventListener('resize',function(){{resize()}});setTimeout(function(){{if(img.complete&&IW<1)resize()}},200);
      return {{
        setTool:function(t){{tool=t;sel=-1;document.querySelectorAll('.at').forEach(function(b){{b.classList.remove('active')}});var btn=document.getElementById('at_'+t);if(btn)btn.classList.add('active');cv.style.cursor=t==='select'?'default':'crosshair'}},
        deleteSelected:function(){{if(sel>=0){{els.splice(sel,1);sel=-1;render()}}}},
        updateDb:function(v){{if(sel>=0&&els[sel]){{els[sel].db=v;render()}}}},
        save:function(){{ex.value=JSON.stringify(els,null,2);try{{var pd=window.parent.document;var ta=pd.querySelector('textarea[aria-label="'+BRIDGE_LABEL+'"]');if(!ta){{var all=pd.querySelectorAll('textarea');for(var i=0;i<all.length;i++){{var lbl=all[i].closest('[data-testid="stTextArea"]');if(lbl&&lbl.textContent.indexOf(BRIDGE_LABEL)>=0){{ta=all[i];break}}var al=all[i].getAttribute('aria-label')||'';if(al.indexOf('MARKER_BRIDGE')>=0){{ta=all[i];break}}}}}}if(!ta)throw new Error('not found');var setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;setter.call(ta,ex.value);ta.dispatchEvent(new Event('input',{{bubbles:true}}));ta.dispatchEvent(new Event('change',{{bubbles:true}}));sts.textContent='Lagret!'}}catch(err){{sts.textContent='Kopier JSON manuelt.';ex.style.display='block'}}}}
      }};
    }})();
    </script>
    """
    components.html(f"<!-- {component_key} -->\n" + html, height=750, scrolling=False)


# ═══════════════════════════════════════════════════════════════════════
# HJELPEFUNKSJONER
# ═══════════════════════════════════════════════════════════════════════

def render_html(html_string: str):
    st.markdown(html_string.replace('\n', ' '), unsafe_allow_html=True)

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
        if p.exists(): return str(p)
    return ""

def clean_pdf_text(text):
    if not text: return ""
    # Norwegian æøå → latin-1 safe equivalents for FPDF
    nor = {"æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "å": "aa", "Å": "AA"}
    for old, new in nor.items(): text = text.replace(old, new)
    # Unicode punctuation → ASCII
    rep = {"\u2013": "-", "\u2014": "-", "\u201c": "\"", "\u201d": "\"", 
           "\u2018": "'", "\u2019": "'", "\u2026": "...", "\u2022": "*",
           "\u2264": "<=", "\u2265": ">="}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{3,}', ' ', text)
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    return clean_pdf_text(text)


# ═══════════════════════════════════════════════════════════════════════
# PDF MOTOR
# ═══════════════════════════════════════════════════════════════════════

class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15); self.set_font('Helvetica', 'B', 10); self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: RIAku-001"), 0, 1, 'R')
            self.set_draw_color(200, 200, 200); self.line(25, 25, 185, 25); self.set_y(30)
    def footer(self):
        self.set_y(-15); self.set_font('Helvetica', 'I', 8); self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f'UTKAST - KREVER FAGLIG KONTROLL | Side {self.page_no()}'), 0, 0, 'C')
    def check_space(self, height):
        if self.get_y() + height > 270: 
            self.add_page(); self.set_margins(25, 25, 25); self.set_x(25)


def create_full_report_pdf(name, client, content, maps, facade_table_df=None, calc_summary=None):
    """Generer komplett RIAku-rapport med beregningsvedlegg."""
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
    # --- Forside ---
    pdf.add_page()
    if os.path.exists("logo.png"): pdf.image("logo.png", x=25, y=20, w=50) 
    
    pdf.set_y(100); pdf.set_font('Helvetica', 'B', 24); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("AKUSTIKKRAPPORT (RIAku)"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 16); pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"FORPROSJEKT: {pdf.p_name}"), 0, 1, 'L'); pdf.ln(30)
    
    regelverk = "Norge (TEK17 / NS 8175 / T-1442)"
    for l, v in [("OPPDRAGSGIVER:", client), ("DATO:", datetime.now().strftime("%d. %m. %Y")), 
                 ("UTARBEIDET AV:", "Builtly RIAku AI Engine"), ("REGELVERK:", regelverk)]:
        pdf.set_x(25); pdf.set_font('Helvetica', 'B', 10); pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10); pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    # --- Innholdsfortegnelse ---
    pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1); pdf.ln(5)
    
    toc = [
        "1. SAMMENDRAG OG KONKLUSJON", 
        "2. MYNDIGHETSKRAV OG REGULERINGSBESTEMMELSER",
        "3. STOYSONER OG FASADENIVAAER", 
        "4. KRAV TIL FASADEISOLASJON (Beregnet)",
        "5. LYDFORHOLD INNENDORS OG PLANLOSNING", 
        "6. BALKONGER OG UTEOPPHOLDSAREAL",
        "7. TILTAK OG VIDERE PROSJEKTERING", 
        "VEDLEGG A: BEREGNINGSTABELL FASADER",
        "VEDLEGG B: VURDERT DATAGRUNNLAG"
    ]
    for t in toc:
        pdf.set_x(25); pdf.set_font('Helvetica', '', 11); pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 10, clean_pdf_text(t), 0, 1)
        pdf.set_draw_color(220, 220, 220); pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    # --- AI-tekst innhold ---
    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: pdf.ln(4); continue
        if line.startswith('# ') or re.match(r'^\d+\.\s[A-Z]', line):
            pdf.check_space(30); pdf.ln(8); pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 14); pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.ln(2); pdf.set_font('Helvetica', '', 10); pdf.set_text_color(0, 0, 0)
        elif line.startswith('##'):
            pdf.check_space(20); pdf.ln(6); pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 12); pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.set_font('Helvetica', '', 10); pdf.set_text_color(0, 0, 0)
        else:
            pdf.set_font('Helvetica', '', 10)
            safe_text = ironclad_text_formatter(line)
            if safe_text.strip() == "": continue
            try:
                if safe_text.startswith('- ') or safe_text.startswith('* '):
                    pdf.set_x(30); pdf.multi_cell(145, 5, safe_text)
                else:
                    pdf.set_x(25); pdf.multi_cell(150, 5, safe_text)
            except Exception: pdf.ln(2)

    # --- VEDLEGG A: Beregningstabell ---
    if facade_table_df is not None and not facade_table_df.empty:
        pdf.add_page()
        pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 15, clean_pdf_text("VEDLEGG A: BEREGNINGSTABELL FASADER"), 0, 1)
        pdf.ln(5)
        
        pdf.set_font('Helvetica', 'I', 9); pdf.set_text_color(100, 100, 100)
        pdf.set_x(25)
        pdf.multi_cell(150, 5, clean_pdf_text(
            "Deterministisk beregning basert paa AI-ekstraherte Lden-verdier. "
            "Krav til Rw+Ctr er beregnet iht. forenklet metode med 3 dB sikkerhetsmargin. "
            "Detaljprosjektering krever full beregning iht. NS-EN 12354-3."
        ))
        pdf.ln(5)
        
        # Tabellheader
        col_widths = [14, 22, 12, 14, 24, 24, 24, 16]
        headers = ["Bygg", "Fasade", "Etg.", "Lden\n(dB)", "Stoysone", "Rw+Ctr vindu\n(soverom)", "Rw+Ctr vindu\n(stue)", "Stille\nside"]
        
        pdf.set_font('Helvetica', 'B', 7); pdf.set_text_color(255, 255, 255)
        pdf.set_fill_color(26, 43, 72)
        for i, h in enumerate(headers):
            pdf.cell(col_widths[i], 10, clean_pdf_text(h), 1, 0, 'C', True)
        pdf.ln()
        
        pdf.set_font('Helvetica', '', 7); pdf.set_text_color(0, 0, 0)
        for _, row in facade_table_df.iterrows():
            # Fargekode rad etter støysone
            zone_text = str(row.get("Støysone", ""))
            if "Rød" in zone_text or "Rod" in zone_text:
                pdf.set_fill_color(255, 230, 230)
            elif "Gul" in zone_text:
                pdf.set_fill_color(255, 245, 220)
            else:
                pdf.set_fill_color(230, 255, 230)
            
            vals = [
                str(row.get("Bygg", "")),
                str(row.get("Fasade", "")),
                str(row.get("Etasje", "")),
                str(int(row.get("Lden (dB)", 0))),
                clean_pdf_text(str(row.get("Støysone", ""))),
                str(int(row.get("Krav Rw+Ctr vindu (soverom)", 0))),
                str(int(row.get("Krav Rw+Ctr vindu (stue)", 0))),
                str(row.get("Stille side", "")),
            ]
            for i, v in enumerate(vals):
                pdf.cell(col_widths[i], 6, clean_pdf_text(v), 1, 0, 'C', True)
            pdf.ln()
    
    # --- VEDLEGG B: Kart/bilder ---
    if maps and len(maps) > 0:
        pdf.add_page()
        pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 20, clean_pdf_text("VEDLEGG B: VURDERT DATAGRUNNLAG"), 0, 1)
        for i, m in enumerate(maps):
            if i > 0: pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                m.convert("RGB").save(tmp.name, format="JPEG", quality=90)
                img_w = 160
                img_h = 160 * (m.height / m.width)
                if img_h > 240: img_h = 240; img_w = 240 * (m.width / m.height)
                x_pos = 105 - (img_w / 2)
                pdf.image(tmp.name, x=x_pos, y=pdf.get_y(), w=img_w)
                pdf.set_y(pdf.get_y() + img_h + 5)
                pdf.set_x(25); pdf.set_font('Helvetica', 'I', 10); pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: Datagrunnlag - kartutsnitt/tegning."), 0, 1, 'C')

    return bytes(pdf.output(dest='S'))


# ═══════════════════════════════════════════════════════════════════════
# PREMIUM CSS (beholdt fra original)
# ═══════════════════════════════════════════════════════════════════════

st.markdown("""
<style>
    :root { --bg: #06111a; --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df; --accent: #38bdf8; --radius-xl: 24px; --radius-lg: 16px; }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; }
    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, div[data-baseweb="select"] * { background-color: transparent !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; border: none !important; }
    div[data-testid="stExpander"] details, div[data-testid="stExpander"] details summary { background-color: #0c1520 !important; color: #f5f7fb !important; border-radius: 12px !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; margin-bottom: 1rem !important; }
    [data-testid="stFileUploaderDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important; border-radius: 12px !important; }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #38c2c9 !important; }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════
# SESSION STATE
# ═══════════════════════════════════════════════════════════════════════

DB_DIR = Path("qa_database")
SSOT_FILE = DB_DIR / "ssot.json"
IMG_DIR = DB_DIR / "project_images"

if "project_data" not in st.session_state:
    st.session_state.project_data = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", 
                                      "kommune": "", "gnr": "", "bnr": "", "b_type": "Næring", 
                                      "etasjer": 1, "bta": 0, "land": "Norge"}

if st.session_state.project_data.get("p_name") == "":
    if SSOT_FILE.exists():
        with open(SSOT_FILE, "r", encoding="utf-8") as f:
            st.session_state.project_data = json.load(f)

if st.session_state.project_data.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(f"<div style='margin-bottom:2rem;'>{logo_html}</div>")
    st.warning("Du må sette opp prosjektdataen før du kan bruke denne modulen.")
    if find_page("Project"):
        if st.button("Gå til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()


# ═══════════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════════

top_l, top_r = st.columns([4, 1])
with top_l:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(logo_html)
with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("← Tilbake til SSOT", use_container_width=True, type="secondary"):
        st.switch_page(find_page("Project"))

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
pd_state = st.session_state.project_data


# ═══════════════════════════════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════════════════════════════

st.markdown(f"<h1 style='font-size: 2.5rem; margin-bottom: 0;'>Lyd & Akustikk (RIAku)</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #9fb0c3; font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent med deterministisk beregningsmotor for støyvurdering, fasadeisolasjon og romakustikk.</p>", unsafe_allow_html=True)

st.success(f"Prosjektdata for **{pd_state['p_name']}** er automatisk synkronisert.")

with st.expander("1. Prosjekt & Lokasjon (Auto-synced)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state["p_name"], disabled=True)
    c_name = c2.text_input("Oppdragsgiver", value=pd_state["c_name"], disabled=True)
    adresse = st.text_input("Adresse", value=f"{pd_state['adresse']}, {pd_state['kommune']}", disabled=True)

with st.expander("2. Bygningsdata & Lydklasse", expanded=True):
    c3, c4, c5 = st.columns(3)
    b_type = c3.text_input("Formål", value=pd_state["b_type"], disabled=True)
    etasjer = c4.number_input("Antall etasjer", value=int(pd_state["etasjer"]), disabled=True)
    bta = c5.number_input("Bruttoareal (BTA m2)", value=int(pd_state["bta"]), disabled=True)
    
    st.markdown("##### Akustisk Klassifisering")
    c6, c7, c8 = st.columns(3)
    lydklasse = c6.selectbox("Lydklasse (NS 8175)", 
                              ["Klasse A (Spesielt gode)", "Klasse B (Gode)", 
                               "Klasse C (Minimumskrav i TEK)", "Klasse D (Eldre bygg)"], index=2)
    stoykilde = c7.selectbox("Dominerende støykilde", 
                              ["Veitrafikk", "Bane/Tog", "Flystøy", "Industri/Næring", 
                               "Lite støy (Stille område)"], index=0)
    vindusandel = c8.slider("Vindusandel i fasade (%)", 15, 50, 25, 5,
                             help="Prosentandel av fasadeareal som er vindu/dor. Pavirker krav til vinduisolasjon.")

    # Parse lydklasse-bokstav
    lydklasse_key = lydklasse.split("(")[0].strip().replace("Klasse ", "")

    st.markdown("##### Reguleringsplanbestemmelser")
    reg_col1, reg_col2 = st.columns(2)
    reg_stille_side = reg_col1.checkbox("Krav om stille side (<55 dB) for alle boenheter", value=True)
    reg_gjennomgaaende = reg_col2.checkbox("Krav om gjennomgående leiligheter i rød sone", value=True)
    reg_soverom_stille = reg_col1.checkbox("Soverom mot stille side (gul sone)", value=True)
    reg_halvpart_stille = reg_col2.checkbox("Halvparten av oppholdsrom mot stille side (rød sone)", value=True)

with st.expander("3. Visuelt Grunnlag & Støykart", expanded=True):
    st.info("For presise resultater: Last opp støyrapport fra akustiker (PDF), støykart med dB-verdier, og plantegninger.")
    
    st.markdown("##### Automatisk stoykart fra Geodata Online")
    stoy_col1, stoy_col2, stoy_col3 = st.columns(3)
    stoy_lat = stoy_col1.number_input("Breddegrad (lat)", value=63.43, format="%.4f", key="stoy_lat")
    stoy_lon = stoy_col2.number_input("Lengdegrad (lon)", value=10.40, format="%.4f", key="stoy_lon")
    stoy_buffer = stoy_col3.number_input("Buffer (m)", value=300, min_value=100, max_value=1000, key="stoy_buffer")
    
    stoy_kilde_map = {"Veitrafikk": "vei", "Bane/Tog": "bane", "Flystøy": "fly", "Industri/Næring": "industri"}
    stoy_api_kilde = stoy_kilde_map.get(stoykilde, "vei")
    
    btn_col1, btn_col2 = st.columns([3, 1])
    
    if btn_col1.button("📡 Hent støykart fra Geodata", key="fetch_stoykart", use_container_width=True):
        with st.spinner(f"Henter støykart ({stoy_api_kilde})..."):
            stoy_img, stoy_err = fetch_stoykart_image(stoy_lat, stoy_lon, stoy_api_kilde, buffer_m=stoy_buffer)
            if stoy_img:
                if stoy_img.mode == "RGBA":
                    bg = Image.new("RGB", stoy_img.size, (255, 255, 255))
                    bg.paste(stoy_img, mask=stoy_img.split()[3])
                    stoy_img = bg
                else:
                    stoy_img = stoy_img.convert("RGB")
                st.session_state["stoykart_image"] = stoy_img
                st.success("Støykart hentet!")
            else:
                st.warning(f"Kunne ikke hente: {stoy_err}. Last opp manuelt.")
            contours = fetch_stoykart_contours(stoy_lat, stoy_lon, stoy_api_kilde, buffer_m=stoy_buffer)
            if contours:
                st.session_state["stoykart_contours"] = contours
    
    if btn_col2.button("🔍 Diagnostikk", key="diag_gdo", use_container_width=True):
        st.markdown("---")
        st.markdown("**Geodata Online diagnostikk:**")
        
        # Steg 1: Env vars
        gdo_u = os.environ.get("GEODATA_ONLINE_USER", "")
        gdo_p = os.environ.get("GEODATA_ONLINE_PASS", "")
        if gdo_u:
            st.caption(f"✅ GEODATA_ONLINE_USER = '{gdo_u[:3]}***' ({len(gdo_u)} tegn)")
        else:
            st.error("❌ GEODATA_ONLINE_USER er IKKE satt i miljøvariabler")
        if gdo_p:
            st.caption(f"✅ GEODATA_ONLINE_PASS = satt ({len(gdo_p)} tegn)")
        else:
            st.error("❌ GEODATA_ONLINE_PASS er IKKE satt")
        
        # Steg 2: Token via GeodataOnlineClient
        st.caption("Prøver å hente token via GeodataOnlineClient...")
        gdo = _get_gdo_client()
        token = None
        if gdo:
            try:
                token = gdo.get_token()
                st.caption(f"✅ Token hentet OK (lengde: {len(token)} tegn, starter med: {token[:10]}...)")
            except Exception as e:
                st.error(f"❌ Token-generering feilet: {e}")
        else:
            st.error("❌ GeodataOnlineClient ikke tilgjengelig — sjekk at geodata_client.py finnes og GEODATA_ONLINE_USER/PASS er satt")
        
        if token and gdo:
            # Steg 3: Liste lag i DOK Forurensning
            st.caption("Henter lag-liste fra DOK Forurensning...")
            try:
                layers = gdo.list_layers("Geomap_UTM33_EUREF89/GeomapDOKForurensning/MapServer", leaf_only=False)
                st.caption(f"✅ Fant {len(layers)} lag i tjenesten:")
                stoey_ids = []
                for lyr in layers[:40]:
                    name = lyr.get("name", "?")
                    lid = lyr.get("id", "?")
                    vis = "👁️" if lyr.get("defaultVisibility", False) else "  "
                    is_noise = any(w in name.lower() for w in ["støy", "stoy", "noise", "lden", "veg", "bane", "fly"])
                    prefix = "🔊" if is_noise else "  "
                    if is_noise:
                        stoey_ids.append(str(lid))
                    st.caption(f"   {vis} {prefix} Lag {lid}: {name}")
                if stoey_ids:
                    st.success(f"Støy-lag funnet: {', '.join(stoey_ids)}")
            except Exception as e:
                st.error(f"   Feil: {e}")
            
            # Steg 4: Prøv MapServer export
            st.caption("Prøver MapServer export...")
            try:
                easting, northing = _latlon_to_utm33(stoy_lat, stoy_lon)
                bbox = (easting - stoy_buffer, northing - stoy_buffer, 
                        easting + stoy_buffer, northing + stoy_buffer)
                st.caption(f"   UTM33 bbox: {bbox[0]:.0f}, {bbox[1]:.0f} → {bbox[2]:.0f}, {bbox[3]:.0f}")
                
                xmin, ymin, xmax, ymax = bbox
                export_params = {
                    "bbox": f"{xmin},{ymin},{xmax},{ymax}",
                    "bboxSR": "25833", "imageSR": "25833",
                    "size": "800,600", "format": "png32", 
                    "transparent": "true", "f": "image",
                    "token": token,
                }
                export_resp = requests.get(f"{DOK_FORURENSNING_URL}/export", 
                                           params=export_params, timeout=20)
                st.caption(f"   HTTP status: {export_resp.status_code}")
                st.caption(f"   Content-Type: {export_resp.headers.get('content-type', 'ukjent')}")
                st.caption(f"   Størrelse: {len(export_resp.content)} bytes")
                
                if export_resp.content[:1] == b'{':
                    st.error(f"   ❌ JSON-feilrespons: {export_resp.text[:300]}")
                elif len(export_resp.content) > 500:
                    test_img = Image.open(io.BytesIO(export_resp.content)).convert("RGBA")
                    st.caption(f"   Bildestørrelse: {test_img.size}")
                    alpha = np.array(test_img.split()[3])
                    non_transp = np.count_nonzero(alpha > 10)
                    total = alpha.size
                    st.caption(f"   Ikke-transparente piksler: {non_transp} av {total} ({100*non_transp/total:.1f}%)")
                    if non_transp > 50:
                        st.success(f"✅ Bildet har innhold!")
                        bg = Image.new("RGBA", test_img.size, (245, 245, 240, 255))
                        result = Image.alpha_composite(bg, test_img)
                        st.image(result.convert("RGB"), caption="Test-bilde fra DOK Forurensning", use_container_width=True)
                    else:
                        st.warning("⚠️ Bildet er nesten helt transparent — ingen støydata synlig i dette området")
                        # Vis uansett
                        bg = Image.new("RGBA", test_img.size, (245, 245, 240, 255))
                        result = Image.alpha_composite(bg, test_img)
                        st.image(result.convert("RGB"), caption="Test-bilde (nesten tomt)", use_container_width=True)
                else:
                    st.warning(f"   ⚠️ For lite data: {len(export_resp.content)} bytes")
            except Exception as e:
                st.error(f"   Export feilet: {e}")
    
    if "stoykart_image" in st.session_state:
        st.image(st.session_state["stoykart_image"], caption="Støykart fra Geodata Online", use_container_width=True)
    
    st.markdown("##### Tegninger fra prosjektet")
    saved_images = []
    if IMG_DIR.exists():
        for p in sorted(IMG_DIR.glob("*.jpg")):
            saved_images.append(Image.open(p).convert("RGB"))
    if saved_images:
        st.success(f"Fant {len(saved_images)} felles tegninger fra Project Setup.")
    else:
        st.warning("Ingen felles tegninger funnet. Last opp under.")
        
    st.markdown("##### Last opp Akustikk-vedlegg")
    files = st.file_uploader("Last opp Støyrapporter (PDF), Støykart, Plantegninger", 
                              accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

st.markdown("<br>", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════
# MULTI-PASS AI ANALYSE MED DETERMINISTISK MOTOR
# ═══════════════════════════════════════════════════════════════════════

if st.button("Kjør Akustisk Analyse (RIAku)", type="primary", use_container_width=True):
    
    images_for_ai = saved_images.copy()
    
    if "stoykart_image" in st.session_state:
        stoy_rgb = st.session_state["stoykart_image"].convert("RGB")
        stoy_rgb.thumbnail((1200, 1200))
        images_for_ai.insert(0, stoy_rgb)
        
    if files:
        with st.spinner("Leser ut støykart og filer..."):
            try:
                for f in files: 
                    f.seek(0)
                    if f.name.lower().endswith('pdf'):
                        if fitz is not None: 
                            doc = fitz.open(stream=f.read(), filetype="pdf")
                            for page_num in range(min(6, len(doc))): 
                                pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                                img = Image.open(io.BytesIO(pix.tobytes("jpeg"))).convert("RGB")
                                img.thumbnail((1200, 1200))
                                images_for_ai.append(img)
                            doc.close() 
                    else:
                        img = Image.open(f).convert("RGB")
                        img.thumbnail((1200, 1200))
                        images_for_ai.append(img)
            except Exception as e: 
                st.error(f"Feil under bildebehandling: {e}")
                
    st.info(f"Sender {len(images_for_ai)} bilder/tegninger til AI.")
    saved_images_clean = [img.copy() for img in images_for_ai]

    # --- Støykildetype for beregninger ---
    kilde_type = stoy_api_kilde if stoy_api_kilde != "industri" else "vei"

    # --- Reguleringsbestemmelser-sammendrag ---
    reg_text = []
    if reg_stille_side: reg_text.append("Alle boenheter skal ha tilgang til uterom på stille side (<55 dB)")
    if reg_soverom_stille: reg_text.append("Boenheter mot gul støysone: minst ett soverom mot stille side")
    if reg_gjennomgaaende: reg_text.append("Boenheter mot rød støysone: skal være gjennomgående, tosidige")
    if reg_halvpart_stille: reg_text.append("Rød sone: halvparten av rom for varig opphold mot stille side")
    reg_summary = "; ".join(reg_text) if reg_text else "Ingen spesifikke reguleringsbestemmelser angitt"
    
    with st.spinner("PASS 1: AI leser støykart og ekstraherer dB-verdier per fasade..."):
        try:
            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        valgt_modell = valid_models[0]
        for fav in ['models/gemini-2.5-flash-preview-04-17', 'models/gemini-2.5-pro-preview-03-25', 'models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if fav in valid_models: valgt_modell = fav; break
        
        model = genai.GenerativeModel(valgt_modell)

        # ═══════════════════════════════════════════════════════════
        # PASS 1: STRUKTURERT DATAEKSTRAKSJON
        # Les dB-verdier fra kart, identifiser bygg og fasader
        # ═══════════════════════════════════════════════════════════
        
        pass1_prompt = f"""Du er en senior akustiker som leser stoykart og plantegninger.

OPPGAVE: Ekstraher ALLE synlige dB-verdier og støynivåer fra bildene.

VIKTIG - LES NØYE:
1. Hvis bildene inneholder støykart med TALL (dB-verdier) skrevet på kartet, les disse EKSAKT.
2. Hvis bildene har fargekoder uten tall, estimer nivåer KONSERVATIVT ut fra farger:
   - Gult = 55-60 dB, Oransje = 60-65 dB, Rødt = 65-70 dB, Mørkerødt = >70 dB
3. Hvis det finnes en profesjonell støyrapport (Brekke & Strand, Multiconsult etc.), 
   bruk DERES tall som autoritative. De har gjort profesjonelle CadnaA-beregninger.
4. IKKE overestimer. Hvis du er i tvil mellom to verdier, velg den LAVERE.
5. Les alle synlige beregningspunkter på fasader med nøyaktige verdier.

FOR HVERT BYGG OG HVER FASADERETNING, angi:
- Byggnavn (A1, A2, B, C osv.)
- Fasaderetning (nord, sør, øst, vest, nordøst osv.)
- Etasje(r) det gjelder
- Lden-verdi i dB (heltall)
- Om det er beregningspunkt fra profesjonell rapport eller AI-estimat

Svar KUN med JSON i dette formatet:
```json
{{
  "kilde_kvalitet": "profesjonell_rapport|stoykart_med_tall|stoykart_farger|ai_estimat",
  "max_lden": 64,
  "stoysone_klassifisering": "gul|rod|gronn",
  "facade_data": [
    {{"bygg": "A1", "fasade": "nordost", "etasje": "1-5", "lden": 64, "kilde": "beregnet"}},
    {{"bygg": "A1", "fasade": "vest", "etasje": "1-5", "lden": 43, "kilde": "beregnet"}},
    {{"bygg": "A2", "fasade": "ost", "etasje": "1-4", "lden": 63, "kilde": "beregnet"}}
  ],
  "balkonger": [
    {{"bygg": "A1", "retning": "ost", "etasje": "1", "lden_uten_tiltak": 64, "lden_med_tiltak": 55}}
  ],
  "eksisterende_tiltak": ["Støyskjerm mellom A1-A2 og A2-B, hoeyde 2m"],
  "stoykilde_beskrivelse": "Veitrafikk fra Industriveien"
}}
```"""
        
        pass1_parts = [pass1_prompt] + images_for_ai
        
        try:
            pass1_res = model.generate_content(pass1_parts)
            pass1_raw = pass1_res.text
            
            # Parse JSON fra pass 1
            json_match = re.search(r'```json\s*(.*?)\s*```', pass1_raw, re.DOTALL)
            if not json_match:
                json_match = re.search(r'\{[\s\S]*"facade_data"[\s\S]*\}', pass1_raw)
            
            extracted_data = {}
            if json_match:
                try:
                    json_str = json_match.group(1) if json_match.lastindex else json_match.group(0)
                    extracted_data = json.loads(json_str)
                except Exception as parse_err:
                    st.warning(f"JSON-parsing feilet i Pass 1: {parse_err}. Bruker fallback.")
            
            facade_data = extracted_data.get("facade_data", [])
            kilde_kvalitet = extracted_data.get("kilde_kvalitet", "ai_estimat")
            max_lden = extracted_data.get("max_lden", 65)
            
            st.success(f"Pass 1 ferdig: Fant {len(facade_data)} fasadepunkter. "
                       f"Kildekvalitet: {kilde_kvalitet}. Hoeyeste Lden: {max_lden} dB.")
            
        except Exception as e:
            st.error(f"Pass 1 feilet: {e}")
            facade_data = []
            kilde_kvalitet = "ai_estimat"
            max_lden = 65

    # ═══════════════════════════════════════════════════════════
    # DETERMINISTISK BEREGNING
    # ═══════════════════════════════════════════════════════════
    
    with st.spinner("PASS 2: Deterministisk beregning av fasadekrav og compliance..."):
        
        # Generer beregningstabell
        facade_table_df = None
        calc_summary = {}
        
        if facade_data:
            facade_table_df = AcousticEngine.generate_facade_table(
                facade_data, lydklasse=lydklasse_key, kilde=kilde_type)
            
            # Reguleringsplan-compliance
            reg_issues = AcousticEngine.reguleringsplan_compliance(facade_data)
            
            # Beregn balkongstøy med tiltak
            balcony_calcs = []
            for bd in extracted_data.get("balkonger", []):
                lden_base = bd.get("lden_uten_tiltak", 60)
                lden_tiltak = AcousticEngine.balcony_noise_with_measures(
                    lden_base, tett_rekkverk=True, absorbent_himling=True, skjermvegg=True)
                balcony_calcs.append({
                    "bygg": bd.get("bygg", "?"),
                    "retning": bd.get("retning", "?"),
                    "lden_uten": lden_base,
                    "lden_med": lden_tiltak,
                    "tilfredsstillende": lden_tiltak <= 55,
                })
            
            # Sammenstill beregningsresultater
            calc_summary = {
                "antall_fasadepunkter": len(facade_data),
                "max_lden": max_lden,
                "stoysone": AcousticEngine.classify_zone(max_lden, kilde_type)["text"],
                "antall_rod_sone": sum(1 for fd in facade_data if fd.get("lden", 0) >= 65),
                "antall_gul_sone": sum(1 for fd in facade_data if 55 <= fd.get("lden", 0) < 65),
                "antall_gronn_sone": sum(1 for fd in facade_data if fd.get("lden", 0) < 55),
                "reg_issues": reg_issues,
                "balcony_calcs": balcony_calcs,
                "kilde_kvalitet": kilde_kvalitet,
            }
            
            st.success(f"Pass 2 ferdig: {calc_summary['antall_rod_sone']} punkt i rod sone, "
                       f"{calc_summary['antall_gul_sone']} i gul, {calc_summary['antall_gronn_sone']} i gronn.")
            
            if facade_table_df is not None and not facade_table_df.empty:
                with st.expander("Beregningstabell (deterministisk)", expanded=True):
                    st.dataframe(facade_table_df, use_container_width=True, hide_index=True)
        else:
            st.warning("Ingen fasadedata ekstrahert. Rapporten baseres på AI-estimater.")

    # ═══════════════════════════════════════════════════════════
    # PASS 3: AI SKRIVER RAPPORT MED BEREGNINGSRESULTATER
    # ═══════════════════════════════════════════════════════════
    
    with st.spinner("PASS 3: AI skriver profesjonell rapport med beregningsresultater..."):
        
        # Bygg beregningsdata-streng for AI
        calc_data_str = ""
        if facade_table_df is not None and not facade_table_df.empty:
            calc_data_str = f"""
DETERMINISTISK BEREGNINGSRESULTATER (Python AcousticEngine):
Kildekvalitet: {kilde_kvalitet}
Høyeste Lden: {max_lden} dB
Støysoneklassifisering: {calc_summary.get('stoysone', 'ukjent')}

FASADETABELL:
{facade_table_df.to_string(index=False)}

REGULERINGSPLAN-ISSUES:
{json.dumps(calc_summary.get('reg_issues', []), ensure_ascii=False, indent=2)}

BALKONGBEREGNINGER:
{json.dumps(calc_summary.get('balcony_calcs', []), ensure_ascii=False, indent=2)}
"""
        
        pass3_prompt = f"""Du er Builtly RIAku AI, en profesjonell senior akustiker som skriver 
presise støyfaglige utredninger for rammetillatelse.

PROSJEKT: {p_name} ({bta} m2, {etasjer} etasjer, {b_type}). 
LOKASJON: {adresse}.
MÅL-LYDKLASSE: {lydklasse} (NS 8175).
DOMINERENDE STØYKILDE: {stoykilde}.

REGULERINGSPLANBESTEMMELSER: {reg_summary}

VIKTIGE REGLER - FØLG DISSE NØYE:
1. Bruk beregningsresultatene fra AcousticEngine som AUTORITATIVE tall.
2. IKKE overestimer støynivåer. Hvis beregning sier 64 dB, skriv 64 dB — IKKE 66-68 dB.
3. Henvis til T-1442/2016 for støysoner og grenseverdier.
4. Henvis til NS 8175 {lydklasse} for innendørs krav.
5. Henvis til TEK17 §13-6 for fasadekrav.
6. Vær KONKRET om tiltak med dimensjoner (støyskjerm høyde, rekkverk høyde osv.)
7. Vurder HVER boenhet/leilighetskategori for compliance med reguleringsplan.
8. Skill mellom beregningspunkter fra profesjonell rapport vs AI-estimater.

{calc_data_str}

SKRIV RAPPORT MED FØLGENDE STRUKTUR:

1. SAMMENDRAG OG KONKLUSJON
- Hovedfunn i ett avsnitt
- Støysoneklassifisering (gul/rød)
- Kan krav tilfredsstilles med tiltak? Ja/nei med begrunnelse.
- Viktigste tiltak oppsummert

2. MYNDIGHETSKRAV OG REGULERINGSBESTEMMELSER
- T-1442/2016 grenseverdier (Lden 55 dB vei, L5AF 70 dB natt)
- Reguleringsplanens støybestemmelser
- NS 8175 lydklasse {lydklasse_key}: innendørs krav

3. STØYSONER OG FASADENIVÅER
- Klassifisering per bygg og fasade (bruk beregnede verdier)
- Stille sider identifisert
- Merk: angi kildekvalitet (profesjonelle beregninger vs. estimat)
- Bruk presise verdier fra beregningstabell

4. KRAV TIL FASADEISOLASJON (Beregnet)
- Krevd Rw+Ctr for vinduer per fasadekategori
- Krevd Rw for yttervegger
- Ventilasjonskrav (balansert ventilasjon med lyddempere)
- Spesielle tiltak for enkeltleiligheter (lavt luftevindu etc.)

5. LYDFORHOLD INNENDØRS OG PLANLØSNING
- Vurdering av romfordeling vs. støyutsatte fasader
- Soverom mot stille side?
- Gjennomgående leiligheter for rød sone?
- Spesifikke problematiske leiligheter identifisert

6. BALKONGER OG UTEOPPHOLDSAREAL
- Støynivå på balkonger uten tiltak
- Støynivå med tiltak (tett rekkverk, absorbent, skjermvegg)
- Hvilke balkonger kan regnes som tilfredsstillende uteareal
- Felles uteareal vurdering

7. TILTAK OG VIDERE PROSJEKTERING
- Støyskjerm: plassering, høyde (minimum), materialvalg
- Tett rekkverk: høyde (typisk 1,3 m), utførelse (glass/tett)
- Absorbent i himling: plassering
- Skjermvegg på balkonger: høyde (typisk 2,2 m), utførelse
- Lavt åpningsbart vindu: hvilke leiligheter, prinsipp
- Lokalt skjermingstiltak foran vindu: detaljer
- Videre prosjektering: detaljert akustisk prosjektering påkrevd

DERETTER: Returner en JSON-blokk med støymarkører for tegningene:
```json
[
  {{"image_index": 0, "x_pct": 72, "y_pct": 35, "db": "64", "color": "yellow", "label": "A1 NO-fasade"}},
  {{"image_index": 0, "x_pct": 65, "y_pct": 45, "db": "43", "color": "green", "label": "A1 stille side V"}}
]
```
Regler for JSON:
- color: "red" KUN for Lden >= 65 dB, "yellow" for 55-64 dB, "green" for < 55 dB
- db: EKSAKT verdi fra beregningstabell, IKKE avrund oppover
- Plasser punkter på FASADELINJER, ikke midt i bygg
- Ett punkt per fasaderetning per bygg
"""
        
        pass3_parts = [pass3_prompt] + images_for_ai

        try:
            res = model.generate_content(pass3_parts)
            ai_raw_text = res.text
            
            # --- Tegne markører ---
            clean_text = ai_raw_text
            json_match = re.search(r'```json\s*(.*?)\s*```', ai_raw_text, re.DOTALL)
            
            markers = []
            if json_match:
                try:
                    markers = json.loads(json_match.group(1))
                    clean_text = re.sub(r'```json\s*.*?\s*```', '', ai_raw_text, flags=re.DOTALL)
                    
                    for marker in markers:
                        idx = int(marker.get("image_index", 0))
                        if idx < len(images_for_ai):
                            img = images_for_ai[idx]
                            draw = ImageDraw.Draw(img)
                            w, h = img.size
                            x = int((marker.get("x_pct", 50) / 100.0) * w)
                            y = int((marker.get("y_pct", 50) / 100.0) * h)
                            db_str = str(marker.get("db", "??"))
                            label = str(marker.get("label", ""))
                            
                            color_name = marker.get("color", "red").lower()
                            if "green" in color_name:
                                color_rgb = (46, 204, 113)
                            elif "yellow" in color_name:
                                color_rgb = (241, 196, 15)
                            elif "darkred" in color_name:
                                color_rgb = (153, 27, 27)
                            else:
                                color_rgb = (231, 76, 60)
                            
                            radius = int(w * 0.014)
                            draw.ellipse((x-radius, y-radius, x+radius, y+radius), 
                                         outline=color_rgb, width=max(2, int(w*0.003)))
                            
                            try:
                                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(w * 0.013))
                                font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", int(w * 0.009))
                            except:
                                font = ImageFont.load_default()
                                font_small = font
                            
                            try:
                                bbox = draw.textbbox((0,0), db_str, font=font)
                                tw = bbox[2] - bbox[0]; th = bbox[3] - bbox[1]
                            except:
                                tw, th = 20, 12
                            
                            draw.rectangle((x-tw/2-2, y-th/2-1, x+tw/2+2, y+th/2+1), fill=color_rgb)
                            draw.text((x-tw/2, y-th/2), db_str, fill=(255,255,255), font=font)
                            
                            if label:
                                try: lw = draw.textbbox((0,0), label, font=font_small)[2]
                                except: lw = len(label)*6
                                draw.text((x-lw/2, y+radius+3), label, fill=color_rgb, font=font_small)
                            
                            images_for_ai[idx] = img
                except Exception as e:
                    print(f"Feil under tegning: {e}")
            
            # Lagre for redigering
            st.session_state["aku_markers"] = markers
            st.session_state["aku_images"] = images_for_ai
            st.session_state["aku_images_original"] = [img.copy() for img in saved_images_clean]
            st.session_state["aku_ai_text"] = clean_text
            
            with st.spinner("Kompilerer PDF med beregningsvedlegg..."):
                pdf_data = create_full_report_pdf(
                    p_name, pd_state['c_name'], clean_text, images_for_ai,
                    facade_table_df=facade_table_df, calc_summary=calc_summary)
                
                if "pending_reviews" not in st.session_state:
                    st.session_state.pending_reviews = {}
                if "review_counter" not in st.session_state:
                    st.session_state.review_counter = 1
                    
                doc_id = f"PRJ-{datetime.now().strftime('%y')}-AKU{st.session_state.review_counter:03d}"
                st.session_state.review_counter += 1
                
                ai_text_lower = clean_text.lower()
                if "for svakt" in ai_text_lower or "avvist" in ai_text_lower:
                    status = "Rejected - Needs Data"
                    badge = "badge-early"
                elif "indikativ" in ai_text_lower or "delvis" in ai_text_lower:
                    status = "Indicative Assessment"
                    badge = "badge-roadmap"
                else:
                    status = "Pending Senior Review"
                    badge = "badge-pending"
                
                st.session_state.pending_reviews[doc_id] = {
                    "title": pd_state['p_name'],
                    "module": "RIAku (Akustikk)",
                    "drafter": "Builtly AI",
                    "reviewer": "Senior Akustiker",
                    "status": status,
                    "class": badge,
                    "pdf_bytes": pdf_data
                }
                
                st.session_state.generated_aku_pdf = pdf_data
                st.session_state.generated_aku_filename = f"Builtly_RIAku_{p_name}.pdf"

                if _HAS_AUTH:
                    try:
                        builtly_auth.save_report(
                            project_name=pd_state.get("p_name", p_name),
                            report_name=f"Akustikk - Builtly_RIAku_{p_name}.pdf",
                            module="RIAku (Akustikk)",
                            file_path=f"Builtly_RIAku_{p_name}.pdf")
                    except Exception:
                        pass

                try:
                    report_dir = DB_DIR / "reports"
                    report_dir.mkdir(exist_ok=True)
                    pdf_path = report_dir / f"Builtly_RIAku_{p_name}.pdf"
                    pdf_path.write_bytes(pdf_data)
                    
                    reviews_file = DB_DIR / "pending_reviews.json"
                    existing_reviews = {}
                    if reviews_file.exists():
                        try: existing_reviews = json.loads(reviews_file.read_text(encoding="utf-8"))
                        except: existing_reviews = {}
                    
                    existing_reviews[doc_id] = {
                        "title": pd_state['p_name'], "module": "RIAku (Akustikk)",
                        "drafter": "Builtly AI", "reviewer": "Senior Akustiker",
                        "status": status, "class": badge, "pdf_file": str(pdf_path),
                        "timestamp": datetime.now().isoformat(),
                    }
                    reviews_file.write_text(json.dumps(existing_reviews, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as save_exc:
                    st.caption(f"Rapport generert men disk-lagring feilet: {save_exc}")

                st.rerun() 
                
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")


# ═══════════════════════════════════════════════════════════════════════
# NEDLASTING OG REDIGERING
# ═══════════════════════════════════════════════════════════════════════

if "generated_aku_pdf" in st.session_state:
    st.success("RIAku Rapport er generert og lagt i QA-koeen!")
    
    col_dl, col_qa = st.columns(2)
    with col_dl:
        st.download_button("Last ned Akustikkrapport", st.session_state.generated_aku_pdf, 
                           st.session_state.generated_aku_filename, type="primary", use_container_width=True)
    with col_qa:
        if find_page("Review"):
            if st.button("Gaa til QA for aa vurdere", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))

    # Interaktiv redigering
    if st.session_state.get("aku_markers") is not None and st.session_state.get("aku_images"):
        with st.expander("Rediger støymarkører (dB-sirkler)", expanded=False):
            st.caption("Flytt, legg til eller slett dB-sirkler. Klikk Lagre, deretter Bruk endringer.")
            
            all_images = st.session_state.get("aku_images", [])
            num_images = len(all_images)
            
            if num_images > 1:
                img_tabs = st.tabs([f"Bilde {i+1}" for i in range(min(num_images, 6))])
            else:
                img_tabs = [st.container()]
            
            for img_idx, tab in enumerate(img_tabs):
                if img_idx >= num_images: break
                with tab:
                    all_markers = st.session_state.get("aku_markers", [])
                    bridge_label = "AKU_MARKER_BRIDGE"
                    images_data = [{"image": all_images[img_idx], "markers": all_markers}]
                    render_acoustic_editor(images_data, bridge_label=bridge_label, 
                                           component_key=f"aku_editor_{img_idx}")
            
            marker_buffer_key = "aku_marker_buffer"
            if marker_buffer_key not in st.session_state:
                st.session_state[marker_buffer_key] = json.dumps(
                    st.session_state.get("aku_markers", []), ensure_ascii=False, indent=2)
            
            st.text_area("AKU_MARKER_BRIDGE", key=marker_buffer_key, height=60, 
                         label_visibility="visible",
                         help="Teknisk buffer — editoren skriver data hit.")
            
            edit_cols = st.columns(3)
            if edit_cols[0].button("Bruk endringer og oppdater PDF", key="aku_apply_markers", 
                                   use_container_width=True, type="primary"):
                try:
                    new_markers = json.loads(st.session_state.get(marker_buffer_key, "[]") or "[]")
                    if not isinstance(new_markers, list): raise ValueError("Må være en liste")
                    
                    originals = st.session_state.get("aku_images_original", [])
                    if not originals: originals = st.session_state.get("aku_images", [])
                    fresh_images = [img.copy() for img in originals]
                    
                    for marker in new_markers:
                        idx = int(marker.get("image_index", 0))
                        if idx < len(fresh_images):
                            img = fresh_images[idx]
                            draw = ImageDraw.Draw(img)
                            w, h = img.size
                            mx = int((marker.get("x_pct", 50) / 100.0) * w)
                            my = int((marker.get("y_pct", 50) / 100.0) * h)
                            db_str = str(marker.get("db", "??"))
                            label = str(marker.get("label", ""))
                            color_name = marker.get("color", "red").lower()
                            color_rgb = (46,204,113) if "green" in color_name else (241,196,15) if "yellow" in color_name else (153,27,27) if "darkred" in color_name else (231,76,60)
                            radius = int(w * 0.014)
                            draw.ellipse((mx-radius, my-radius, mx+radius, my+radius), outline=color_rgb, width=max(2, int(w*0.003)))
                            try:
                                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(w * 0.013))
                                font_s = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", int(w * 0.009))
                            except:
                                font = ImageFont.load_default(); font_s = font
                            try: bb = draw.textbbox((0,0), db_str, font=font); tw=bb[2]-bb[0]; th=bb[3]-bb[1]
                            except: tw, th = 20, 12
                            draw.rectangle((mx-tw/2-2, my-th/2-1, mx+tw/2+2, my+th/2+1), fill=color_rgb)
                            draw.text((mx-tw/2, my-th/2), db_str, fill=(255,255,255), font=font)
                            if label:
                                try: lw = draw.textbbox((0,0), label, font=font_s)[2]
                                except: lw = len(label)*6
                                draw.text((mx-lw/2, my+radius+3), label, fill=color_rgb, font=font_s)
                    
                    st.session_state["aku_markers"] = new_markers
                    st.session_state["aku_images"] = fresh_images
                    
                    clean_text = st.session_state.get("aku_ai_text", "")
                    pdf_data = create_full_report_pdf(p_name, pd_state['c_name'], clean_text, fresh_images)
                    st.session_state.generated_aku_pdf = pdf_data
                    
                    if _HAS_AUTH:
                        try:
                            builtly_auth.save_report(project_name=pd_state.get("p_name", p_name),
                                                     report_name=f"Akustikk - Builtly_RIAku_{p_name}.pdf (revidert)",
                                                     module="RIAku (Akustikk)", 
                                                     file_path=f"Builtly_RIAku_{p_name}.pdf")
                        except Exception: pass
                    
                    st.success("Markører oppdatert og PDF regenerert!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Feil: {exc}")
            
            if edit_cols[1].button("Nullstill markører", key="aku_reset_markers", use_container_width=True):
                st.session_state[marker_buffer_key] = json.dumps(
                    st.session_state.get("aku_markers", []), ensure_ascii=False, indent=2)
                st.rerun()
            
            if edit_cols[2].button("Tøm alle markører", key="aku_clear_markers", use_container_width=True):
                st.session_state["aku_markers"] = []
                st.session_state[marker_buffer_key] = "[]"
                st.rerun()
