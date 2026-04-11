import streamlit as st
import os
import base64
from pathlib import Path
import requests
import urllib.parse
import re
import json
from datetime import datetime
import time
import io
from PIL import Image

import google.generativeai as genai
try:
    import fitz  
except ImportError:
    fitz = None

# --- 1. GRUNNINNSTILLINGER & API ---
st.set_page_config(page_title="Project Setup | Builtly", page_icon="⚙️", layout="wide", initial_sidebar_state="collapsed")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)

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

def go_home():
    st.switch_page("Builtly_AI_frontpage_access_gate_expanded.py")

# --- 2. LOKAL DATABASE (HARDDISK-LAGRING) ---
DB_DIR = Path("qa_database")
IMG_DIR = DB_DIR / "project_images"
FILES_DIR = DB_DIR / "project_files"
SSOT_FILE = DB_DIR / "ssot.json"

def init_db():
    DB_DIR.mkdir(exist_ok=True)
    IMG_DIR.mkdir(exist_ok=True)
    FILES_DIR.mkdir(exist_ok=True)

init_db()

# --- 3. PREMIUM CSS ---
st.markdown("""
<style>
    :root { --bg: #06111a; --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df; --accent: #38c2c9; --radius-xl: 24px; --radius-lg: 16px; }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); margin-bottom: 1rem; }
    
    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="primary"][disabled] { background: rgba(120,145,170,0.2) !important; color: rgba(255,255,255,0.3) !important; box-shadow: none !important; transform: none !important; cursor: not-allowed !important; }
    button[kind="secondary"] { background: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 10px 20px !important; transition: all 0.2s ease !important; }
    button[kind="secondary"]:hover { background: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important; }

    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, div[data-baseweb="select"] * { background-color: transparent !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; border: none !important; box-shadow: none !important; }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus { border: none !important; }
    div[data-baseweb="base-input"]:focus-within, div[data-baseweb="select"] > div:focus-within, .stTextArea > div > div > div:focus-within { border-color: var(--accent) !important; box-shadow: 0 0 0 1px rgba(56, 194, 201, 0.5) !important; }
    ul[data-baseweb="menu"] { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; }
    ul[data-baseweb="menu"] li { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
    ul[data-baseweb="menu"] li:hover { background-color: rgba(56, 194, 201, 0.1) !important; }
    div[data-testid="InputInstructions"], div[data-testid="InputInstructions"] > span { color: #9fb0c3 !important; -webkit-text-fill-color: #9fb0c3 !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label { color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important; }

    [data-testid="stFileUploaderDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important; border-radius: 12px !important; padding: 2rem !important; }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #38c2c9 !important; background-color: rgba(56, 194, 201, 0.05) !important; }
    [data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
    [data-testid="stFileUploaderFileData"] { background-color: rgba(255,255,255,0.02) !important; color: #f5f7fb !important; border-radius: 8px !important;}

    .dash-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 1.5rem; margin-bottom: 1.5rem; }
    .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1.5rem; margin-bottom: 2.5rem; }
    .card { background: linear-gradient(180deg, rgba(16,30,46,0.8), rgba(10,18,28,0.8)); border: 1px solid var(--stroke); border-radius: var(--radius-lg); padding: 1.8rem; box-shadow: 0 12px 30px rgba(0,0,0,0.2); }
    .card-hero { background: linear-gradient(135deg, rgba(16,30,46,0.9), rgba(6,17,26,0.9)); position: relative; overflow: hidden; border-radius: var(--radius-xl);}
    
    .hero-kicker { display: inline-flex; align-items: center; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 1rem; border: 1px solid var(--stroke); padding: 4px 12px; border-radius: 999px; background: rgba(255,255,255,0.02); }
    .hero-title { font-size: 2.8rem; font-weight: 800; margin: 0 0 0.5rem 0; letter-spacing: -0.03em; color: #fff; }
    .hero-sub { color: var(--soft); font-size: 1.05rem; line-height: 1.6; max-width: 50ch; margin-bottom: 1.5rem; }
    
    .status-kicker { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 0.2rem; }
    .status-title { font-size: 2rem; font-weight: 750; color: #fff; margin-bottom: 1rem; }
    .status-desc { color: var(--soft); font-size: 0.9rem; line-height: 1.5; margin-bottom: 1.5rem; }
    .prog-bar-bg { width: 100%; height: 6px; background: rgba(255,255,255,0.1); border-radius: 999px; overflow: hidden; margin-bottom: 1.5rem; }
    
    .meta-row { display: flex; justify-content: space-between; padding: 0.6rem 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 0.85rem; }
    .meta-row:last-child { border-bottom: none; }
    .meta-label { color: var(--muted); }
    .meta-value { color: var(--text); font-weight: 600; text-align: right; }

    .stat-title { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 0.8rem; }
    .stat-value { font-size: 1.8rem; font-weight: 750; color: #fff; margin-bottom: 0.3rem; }
    .stat-desc { font-size: 0.85rem; color: var(--soft); line-height: 1.4; }

    .snap-row { display: flex; justify-content: space-between; align-items: flex-start; padding: 0.8rem 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 0.9rem; }
    .snap-label { color: var(--muted); width: 35%; flex-shrink: 0; }
    .snap-val { color: var(--text); font-weight: 500; text-align: right; width: 65%; word-wrap: break-word;}
    
    [data-testid="stAlert"] { background-color: rgba(56, 194, 201, 0.1) !important; border: 1px solid rgba(56, 194, 201, 0.3) !important; border-radius: 8px !important; }
    [data-testid="stAlert"] * { color: #f5f7fb !important; }

    .module-badge { display: inline-flex; align-items: center; justify-content: center; padding: 0.32rem 0.62rem; border-radius: 999px; border: 1px solid rgba(120,145,170,0.18); background: rgba(255,255,255,0.03); color: var(--muted); font-size: 0.75rem; font-weight: 650; }
    .badge-priority { color: #8ef0c0; border-color: rgba(142,240,192,0.25); background: rgba(126,224,129,0.08); }
    .badge-phase2 { color: #9fe7ff; border-color: rgba(120,220,225,0.22); background: rgba(56,194,201,0.08); }
    .badge-early { color: #d7def7; border-color: rgba(215,222,247,0.18); background: rgba(255,255,255,0.03); }
    .badge-roadmap { color: #f4bf4f; border-color: rgba(244,191,79,0.22); background: rgba(244,191,79,0.08); }
    .module-icon { width: 46px; height: 46px; border-radius: 14px; display: inline-flex; align-items: center; justify-content: center; background: rgba(56,194,201,0.1); border: 1px solid rgba(56,194,201,0.18); font-size: 1.32rem; flex-shrink: 0; }
    [data-testid="column"]:has(.module-card-hook) { background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98)) !important; border: 1px solid rgba(120, 145, 170, 0.18) !important; border-radius: 22px !important; padding: 1.5rem !important; box-shadow: 0 12px 38px rgba(0,0,0,0.18) !important; transition: all 0.2s ease !important; margin-bottom: 1rem !important; }
    [data-testid="column"]:has(.module-card-hook):hover { border-color: rgba(56,194,201,0.24) !important; box-shadow: 0 16px 42px rgba(0,0,0,0.24) !important; }
    [data-testid="column"]:has(.module-card-hook) > div { height: 100% !important; display: flex !important; flex-direction: column !important; }
    [data-testid="column"]:has(.module-card-hook) [data-testid="stButton"] { margin-top: auto !important; width: 100% !important; padding-top: 1rem; }
    [data-testid="column"]:has(.module-card-hook) button[kind="secondary"] { background: rgba(56,194,201,0.1) !important; border: 1px solid rgba(56,194,201,0.28) !important; color: #f5f7fb !important; border-radius: 12px !important; min-height: 46px !important; font-weight: 650 !important; font-size: 0.94rem !important; width: 100% !important; }
    [data-testid="column"]:has(.module-card-hook) button[kind="secondary"]:hover { border-color: rgba(56,194,201,0.8) !important; background: rgba(56,194,201,0.2) !important; transform: translateY(-2px) !important; }
</style>
""", unsafe_allow_html=True)


# --- 4. SESSION STATE LOGIKK (MED SELVHELBRENDENDE SIKKERHETSNETT) ---
default_data = {
    "land": "Norge (TEK17 / Kartverket)", "p_name": "", "c_name": "", "p_desc": "",
    "adresse": "", "kommune": "", "gnr": "", "bnr": "",
    "b_type": "Næring / Kontor", "etasjer": 4, "bta": 2500, "tomteareal": 0, 
    "last_sync": "Ikke synket enda"
}

if "project_data" not in st.session_state:
    if SSOT_FILE.exists():
        with open(SSOT_FILE, "r", encoding="utf-8") as f:
            st.session_state.project_data = json.load(f)
    else:
        st.session_state.project_data = default_data.copy()

for k, v in default_data.items():
    if k not in st.session_state.project_data:
        st.session_state.project_data[k] = v

if "ai_drawing_analysis" not in st.session_state:
    st.session_state.ai_drawing_analysis = None
if "analyzed_file_names" not in st.session_state:
    st.session_state.analyzed_file_names = []

pd_state = st.session_state.project_data
saved_image_count = len(list(IMG_DIR.glob("*.jpg")))
saved_files_count = len(list(FILES_DIR.iterdir())) if FILES_DIR.exists() else 0

fields_to_check = ["p_name", "c_name", "p_desc", "adresse", "kommune", "gnr", "bnr", "b_type", "etasjer", "bta", "tomteareal"]
filled_fields = sum(1 for field in fields_to_check if bool(pd_state.get(field)))
completeness = int((filled_fields / len(fields_to_check)) * 100)
sync_status = "Draft" if completeness < 100 else "Ready"
progress_color = "#38c2c9" if completeness > 80 else "#f4bf4f" if completeness > 40 else "#ef4444"

def fetch_from_kartverket(adresse, kommune, gnr, bnr):
    adr_clean = adresse.replace(',', '').strip() if adresse else ""
    kom_clean = kommune.replace(',', '').strip() if kommune else ""

    def api_call(query_string):
        if not query_string.strip(): return None
        safe_query = urllib.parse.quote(query_string)
        url = f"https://ws.geonorge.no/adresser/v1/sok?sok={safe_query}&fuzzy=true&utkoordsys=25833&treffPerSide=1"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200 and resp.json().get('adresser'):
                hit = resp.json()['adresser'][0]
                return {
                    "adresse": hit.get('adressetekst', ''),
                    "kommune": hit.get('kommunenavn', ''),
                    "gnr": str(hit.get('gardsnummer', '')),
                    "bnr": str(hit.get('bruksnummer', ''))
                }
        except Exception: pass
        return None

    queries = []
    if adr_clean and kom_clean: queries.append(f"{adr_clean} {kom_clean}")
    if adr_clean: queries.append(adr_clean)
    if adr_clean: 
        base_num = re.sub(r'(\d+)[a-zA-Z]+', r'\1', adr_clean)
        if base_num != adr_clean: queries.append(base_num)
    if gnr and bnr and kom_clean: queries.append(f"{kom_clean} {gnr}/{bnr}")
        
    for q in queries:
        res = api_call(q)
        if res: return res
    return None


def fetch_parcel_from_wfs(kommune: str, gnr: str, bnr: str):
    """Fetch parcel polygon from Kartverket WFS (same as Mulighetsstudie)."""
    # Resolve kommunenummer
    knr = None
    s = kommune.strip()
    if s.isdigit() and len(s) >= 3:
        knr = s.zfill(4)
    else:
        try:
            resp = requests.get("https://ws.geonorge.no/kommuneinfo/v1/kommuner", timeout=5)
            if resp.status_code == 200:
                for k in resp.json():
                    if k.get("kommunenavn", "").lower() == s.lower():
                        knr = k.get("kommunenummer")
                        break
        except Exception:
            pass
    if not knr:
        return None

    gnr_clean = str(gnr).strip()
    bnr_clean = str(bnr).strip()
    cql = f"kommunenummer='{knr}' AND gardsnummer={gnr_clean} AND bruksnummer={bnr_clean}"

    services = [
        ("https://wfs.geonorge.no/skwms1/wfs.matrikkelen-teig", "matrikkelen-teig:Teig"),
        ("https://wfs.geonorge.no/skwms1/wfs.matrikkelkart", "matrikkelkart:Teig"),
    ]

    for url, layer in services:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typenames": layer, "srsName": "EPSG:25833",
            "outputFormat": "application/json", "cql_filter": cql,
        }
        try:
            resp = requests.get(url, params=params, timeout=12)
            if resp.status_code != 200:
                params["outputFormat"] = "json"
                resp = requests.get(url, params=params, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                features = data.get("features", [])
                if features:
                    geom = features[0].get("geometry", {})
                    coords = geom.get("coordinates", [])
                    # Calculate bounds and area from polygon coordinates
                    if geom.get("type") == "Polygon" and coords:
                        ring = coords[0]
                        xs = [p[0] for p in ring]
                        ys = [p[1] for p in ring]
                        # Shoelace formula for area
                        n = len(ring)
                        area = 0.0
                        for i in range(n - 1):
                            area += ring[i][0] * ring[i + 1][1]
                            area -= ring[i + 1][0] * ring[i][1]
                        area = abs(area) / 2.0
                        return {
                            "area_m2": round(area, 1),
                            "bounds": (min(xs), min(ys), max(xs), max(ys)),
                            "source": f"Kartverket WFS ({layer})",
                        }
                    elif geom.get("type") == "MultiPolygon" and coords:
                        all_xs, all_ys, total_area = [], [], 0.0
                        for polygon in coords:
                            ring = polygon[0]
                            all_xs.extend(p[0] for p in ring)
                            all_ys.extend(p[1] for p in ring)
                            n = len(ring)
                            a = 0.0
                            for i in range(n - 1):
                                a += ring[i][0] * ring[i + 1][1]
                                a -= ring[i + 1][0] * ring[i][1]
                            total_area += abs(a) / 2.0
                        return {
                            "area_m2": round(total_area, 1),
                            "bounds": (min(all_xs), min(all_ys), max(all_xs), max(all_ys)),
                            "source": f"Kartverket WFS ({layer})",
                        }
        except Exception:
            continue
    return None


def fetch_ortofoto_thumbnail(bounds, buffer_m=80):
    """Fetch ortofoto from Kartverket WMS for the given EPSG:25833 bounds."""
    if not bounds:
        return None
    minx, miny, maxx, maxy = bounds
    url = (
        "https://wms.geonorge.no/skwms1/wms.nib"
        "?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto"
        f"&styles=&srs=EPSG:25833&bbox={minx - buffer_m},{miny - buffer_m},{maxx + buffer_m},{maxy + buffer_m}"
        "&width=800&height=800&format=image/png"
    )
    try:
        resp = requests.get(url, timeout=12)
        if resp.status_code == 200 and len(resp.content) > 3000:
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            return img
    except Exception:
        pass
    return None

# --- 5. HEADER ---
c1, c2, c3 = st.columns([2.5, 1, 1])
with c1:
    logo_html = f'<a href="/" target="_self" style="text-decoration:none;"><img src="{logo_data_uri()}" class="brand-logo"></a>' if logo_data_uri() else '<a href="/" target="_self"><h2 style="margin:0; color:white;">Builtly</h2></a>'
    render_html(logo_html)
with c2:
    st.markdown("<div style='margin-top: 0.8rem;'></div>", unsafe_allow_html=True)
    if st.button("← Tilbake til Portal", use_container_width=True, type="secondary"):
        go_home()
with c3:
    st.markdown("<div style='margin-top: 0.8rem;'></div>", unsafe_allow_html=True)
    if find_page("Review"):
        if st.button("QA & Sign-off", type="primary", use_container_width=True):
            st.switch_page(find_page("Review"))

# --- 6. DASHBOARD UI ---
render_html(f"""
<div class="dash-grid">
    <div class="card card-hero">
        <div class="hero-kicker">✦ Builtly AI • Project SSOT</div>
        <h1 class="hero-title">Project Configuration</h1>
        <div class="hero-sub">Ett kontrollsenter for prosjektets kjerneparametere. Synkroniser prosjektets kontekst sømløst til teknisk prosjektering, bærekraftsanalyser og prosjektledelse.</div>
    </div>
    
    <div class="card">
        <div class="status-kicker">Sync Status</div>
        <div class="status-title">{sync_status}</div>
        <div class="status-desc">{filled_fields} av {len(fields_to_check)} nøkkelfelt er fylt ut og tilgjengelige for AI-modulene.</div>
        
        <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:0.5rem; color: #c8d3df;">
            <span>Kompletthet</span><span style="font-weight:700;">{completeness}%</span>
        </div>
        <div class="prog-bar-bg"><div style="width: {completeness}%; height: 100%; background: {progress_color}; border-radius: 999px;"></div></div>
        
        <div class="meta-row"><span class="meta-label">Sist oppdatert</span><span class="meta-value">{pd_state.get("last_sync", "Ikke synket enda")}</span></div>
        <div class="meta-row"><span class="meta-label">Lokasjon satt</span><span class="meta-value">{"Ja" if pd_state.get("adresse") or pd_state.get("gnr") else "Nei"}</span></div>
    </div>
</div>

<div class="stat-grid">
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Datakompletthet</div><div class="stat-value" style="color:{progress_color};">{completeness}%</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Primær Bruk</div><div class="stat-value" style="font-size:1.4rem; padding-top:0.4rem;">{pd_state.get("b_type", "-")}</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Bygningsareal</div><div class="stat-value">{pd_state.get("bta", "0")} m²</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Tomteareal</div><div class="stat-value">{pd_state.get("tomteareal", "0")} m²</div>
    </div>
</div>
""")

# --- 7. INPUT SEKSJON ---
st.markdown("<h3 style='margin-top: 1rem; margin-bottom: 0.2rem;'>Oppdater prosjektets kontrollsenter</h3>", unsafe_allow_html=True)
st.markdown("<p style='color:#9fb0c3; margin-bottom: 1.5rem;'>Fyll ut dataene under. Dette mates automatisk inn i alle AI-agenter for å sikre samsvar.</p>", unsafe_allow_html=True)

input_col, snap_col = st.columns([2, 1], gap="large")

with input_col:
    st.markdown("""<div style="margin-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);"><h4 style="color: #f5f7fb; margin: 0;">📌 01 Generelt</h4></div>""", unsafe_allow_html=True)
    
    land_options = ["Norge (TEK17 / Kartverket)", "Sverige (BBR)", "Danmark (BR18)", "UK (Building Regs)"]
    try: l_idx = land_options.index(pd_state.get("land", "Norge (TEK17 / Kartverket)"))
    except: l_idx = 0
    new_land = st.selectbox("🌍 Land / Lokalt Regelverk", land_options, index=l_idx)
    
    c1, c2 = st.columns(2)
    new_p_name = c1.text_input("Prosjektnavn", value=pd_state.get("p_name", ""))
    new_c_name = c2.text_input("Tiltakshaver / Oppdragsgiver", value=pd_state.get("c_name", ""))
    
    new_p_desc = st.text_area("Prosjektbeskrivelse / Narrativ", value=pd_state.get("p_desc", ""), height=140)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""<div style="margin-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);"><h4 style="color: #f5f7fb; margin: 0;">🌍 02 Lokasjon & API</h4></div>""", unsafe_allow_html=True)
    
    if "Norge" in new_land: st.info("💡 **Kartverket API:** Skriv inn adresse *eller* Gnr/Bnr og trykk på knappen for å autoutfylle resten.")
        
    c3, c4 = st.columns(2)
    new_adresse = c3.text_input("Gateadresse", value=pd_state.get("adresse", ""))
    new_kommune = c4.text_input("Kommune", value=pd_state.get("kommune", ""))
    
    c5, c6 = st.columns(2)
    new_gnr = c5.text_input("Gårdsnummer (Gnr)", value=pd_state.get("gnr", ""))
    new_bnr = c6.text_input("Bruksnummer (Bnr)", value=pd_state.get("bnr", ""))
    
    if "Norge" in new_land:
        if st.button("🔍 Søk i Matrikkel + Hent Tomtedata (Kartverket)", type="secondary"):
            st.session_state.project_data.update({"p_name": new_p_name, "c_name": new_c_name, "p_desc": new_p_desc})
            with st.spinner("Søker i Nasjonalt Adresseregister..."):
                res = fetch_from_kartverket(new_adresse, new_kommune, new_gnr, new_bnr)
                if res:
                    st.session_state.project_data.update(res)
                    st.success("✅ Fant eiendom i adresseregisteret!")
                else:
                    st.warning("Fant ingen adressetreff. Prøver å hente tomtegrense direkte fra Gnr/Bnr...")

            # Use resolved values (from API or user input)
            resolved_kommune = st.session_state.project_data.get("kommune", new_kommune)
            resolved_gnr = st.session_state.project_data.get("gnr", new_gnr)
            resolved_bnr = st.session_state.project_data.get("bnr", new_bnr)

            if resolved_gnr and resolved_bnr and resolved_kommune:
                with st.spinner("Henter tomtegrense fra Kartverket WFS..."):
                    parcel = fetch_parcel_from_wfs(resolved_kommune, resolved_gnr, resolved_bnr)
                    if parcel:
                        st.session_state.project_data["tomteareal"] = int(parcel["area_m2"])
                        st.session_state.project_data["_parcel_bounds"] = list(parcel["bounds"])
                        st.session_state.project_data["_parcel_source"] = parcel["source"]
                        st.success(f"✅ Tomtegrense hentet! Areal: **{int(parcel['area_m2']):,} m²** ({parcel['source']})")

                        # Fetch ortofoto
                        with st.spinner("Henter ortofoto fra Kartverket..."):
                            ortofoto = fetch_ortofoto_thumbnail(parcel["bounds"])
                            if ortofoto:
                                buf = io.BytesIO()
                                ortofoto.save(buf, format="JPEG", quality=85)
                                st.session_state["_project_ortofoto"] = buf.getvalue()
                                st.success("✅ Ortofoto hentet!")
                            else:
                                st.info("Kunne ikke hente ortofoto for denne eiendommen.")
                    else:
                        st.warning("Fant ingen tomtegrense i WFS-tjenesten. Tomteareal må fylles inn manuelt.")

            time.sleep(0.5)
            st.rerun()

    # Show ortofoto if available
    if st.session_state.get("_project_ortofoto"):
        st.markdown("""<div style="margin-top:0.5rem;margin-bottom:0.5rem;">
            <div style="font-size:0.8rem;color:var(--muted);margin-bottom:0.3rem;">📸 Ortofoto fra Kartverket</div>
        </div>""", unsafe_allow_html=True)
        st.image(st.session_state["_project_ortofoto"], use_container_width=True)
        parcel_src = pd_state.get("_parcel_source", "")
        if parcel_src:
            st.caption(f"Kilde: {parcel_src} · Tomteareal: {pd_state.get('tomteareal', '?')} m²")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""<div style="margin-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);"><h4 style="color: #f5f7fb; margin: 0;">🏢 03 Bygg- og Tomtedata</h4></div>""", unsafe_allow_html=True)
    
    c7, c8, c9, c10 = st.columns(4)
    type_options = ["Bolig (Blokk/Rekkehus)", "Næring / Kontor", "Handel / Kjøpesenter", "Offentlig / Skole", "Industri / Lager"]
    try: default_idx = type_options.index(pd_state.get("b_type", "Næring / Kontor"))
    except: default_idx = 1
    
    new_b_type = c7.selectbox("Primær Bruk", type_options, index=default_idx)
    new_etasjer = c8.number_input("Etasjer", value=int(pd_state.get("etasjer", 1)), min_value=1)
    new_bta = c9.number_input("Bygningsareal (BTA m²)", value=int(pd_state.get("bta", 0)), step=100)
    new_tomteareal = c10.number_input("Tomteareal (m²)", value=int(pd_state.get("tomteareal", 0)), step=100)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""<div style="margin-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);"><h4 style="color: #f5f7fb; margin: 0;">📁 04 Tegningsgrunnlag (AI Kvalitetssikring)</h4></div>""", unsafe_allow_html=True)
    st.info("Last opp tegninger her for å la AI-en vurdere kvaliteten på underlaget *før* det sendes til fagmodulene. AI-en vil sjekke om plan, snitt, fasade og situasjonsplan er komplett.")
    
    uploaded_drawings = st.file_uploader("Last opp Fasade, Plan, Snitt og Situasjonsplan (PDF/Bilder)", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

    st.file_uploader(
        "Last opp øvrige prosjektdokumenter (DWG, IFC, DXF, XLSX, DOCX)",
        accept_multiple_files=True,
        type=['dwg', 'dxf', 'ifc', 'xlsx', 'xls', 'docx', 'csv', 'zip'],
        key="project_extra_files",
        help="Disse filene lagres i prosjektmappen og er tilgjengelig for alle fagmoduler (TDD, Anbudskontroll, Mengde & Scope, Yield osv).",
    )
    current_file_names = [f.name for f in uploaded_drawings] if uploaded_drawings else []
    
    if uploaded_drawings:
        if st.button("🤖 Analyser & Kvalitetssikre Tegninger med AI", type="secondary"):
            if not google_key:
                st.error("Google API-nøkkel mangler!")
            else:
                with st.spinner("AI studerer tegningene. Større filer komprimeres automatisk for å forhindre minnekrasj..."):
                    images_for_qa = []
                    try:
                        for f in uploaded_drawings: 
                            f.seek(0)
                            if f.name.lower().endswith('pdf'):
                                if fitz is None: 
                                    st.error("Mangler PDF-modul (PyMuPDF).")
                                    break
                                doc = fitz.open(stream=f.read(), filetype="pdf")
                                for page_num in range(min(4, len(doc))): 
                                    pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(1.0, 1.0))
                                    img = Image.open(io.BytesIO(pix.tobytes("jpeg"))).convert("RGB")
                                    img.thumbnail((1200, 1200))
                                    images_for_qa.append(img)
                                doc.close() 
                            else:
                                img = Image.open(f).convert("RGB")
                                img.thumbnail((1200, 1200))
                                images_for_qa.append(img)
                                
                        if images_for_qa:
                            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                            valgt_modell = valid_models[0]
                            for fav in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
                                if fav in valid_models: valgt_modell = fav; break
                            
                            model = genai.GenerativeModel(valgt_modell)
                            
                            qa_prompt = f"""
                            Du er en Senior Rådgivende Ingeniør og Arkitekt for prosjektet '{new_p_name}'.
                            Din oppgave er å utføre en streng kvalitetskontroll (QA) av de vedlagte tegningene før de sendes videre til andre fagfelt (brann, akustikk, konstruksjon).
                            Prosjektinfo: {new_b_type}, {new_etasjer} etasjer, Bygg BTA {new_bta} m2, Tomteareal {new_tomteareal} m2.
                            Vurder følgende:
                            1. ER GRUNNLAGET KOMPLETT? Ser du både plantegninger, snitt, fasader og situasjonsplan? Hvis noe mangler, si ifra tydelig!
                            2. KVALITET & LESBARHET: Er tegningene tydelige? Er det satt på mål og akser der det er nødvendig?
                            3. POTENSIELLE UTFORDRINGER: Ut fra det du ser, er det noe arkitektonisk som kan by på problemer for Brannkonsept, Konstruksjon eller Akustikk?
                            4. KONKRETE FORSLAG: Gi 2-3 konkrete forslag til endringer i tegningsgrunnlaget før videre prosjektering.
                            Svar formatert pent med Markdown, bruk emojis, og vær direkte og profesjonell.
                            """
                            
                            res = model.generate_content([qa_prompt] + images_for_qa)
                            
                            try:
                                analysis_result = res.text
                            except ValueError:
                                analysis_result = "⚠️ **Merk:** AI-en klarte ikke å skrive en vurdering av disse spesifikke filene (returnerte et tomt svar). Dette kan skje med svært komplekse arkitekttegninger. **Du kan likevel trykke 'Lagre' for å gå videre til fagmodulene!**"
                            
                            st.session_state.ai_drawing_analysis = analysis_result
                            st.session_state.analyzed_file_names = current_file_names
                            st.rerun()
                    except Exception as e:
                        st.error(f"Feil under bildebehandling: {e}")

    if st.session_state.ai_drawing_analysis:
        st.markdown("<div class='card' style='margin-top: 1rem; border-color: #38c2c9;'>", unsafe_allow_html=True)
        st.markdown("<h4 style='margin-top: 0; color: #38c2c9;'>📊 AI-Vurdering av Tegningsgrunnlag</h4>", unsafe_allow_html=True)
        st.markdown(st.session_state.ai_drawing_analysis)
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<br><br>", unsafe_allow_html=True)
    
    mangler_qa = uploaded_drawings and set(current_file_names) != set(st.session_state.analyzed_file_names)
    
    if mangler_qa:
        st.warning("⚠️ **Handling kreves:** Du har lastet opp nye tegninger. Du må kjøre AI-kvalitetssikringen (knappen over) før du kan lagre prosjektet.")
        st.button("💾 Lagre & Synkroniser SSOT Data", type="primary", disabled=True, use_container_width=True)
    else:
        if st.button("💾 Lagre & Synkroniser SSOT Data", type="primary", use_container_width=True):
            
            for p in IMG_DIR.glob("*.jpg"):
                p.unlink()
            for p in FILES_DIR.iterdir():
                try:
                    p.unlink()
                except Exception:
                    pass

            if uploaded_drawings:
                try:
                    img_count = 0
                    for f in uploaded_drawings: 
                        f.seek(0)
                        if f.name.lower().endswith('pdf'):
                            # Save original PDF to project_files
                            f.seek(0)
                            (FILES_DIR / f.name).write_bytes(f.read())
                            # Also convert to JPG previews
                            if fitz is not None: 
                                f.seek(0)
                                doc = fitz.open(stream=f.read(), filetype="pdf")
                                for page_num in range(min(4, len(doc))):
                                    pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(1.0, 1.0))
                                    img = Image.open(io.BytesIO(pix.tobytes("jpeg"))).convert("RGB")
                                    img.thumbnail((1200, 1200))
                                    img.save(IMG_DIR / f"tegning_{img_count}.jpg", "JPEG", quality=85)
                                    img_count += 1
                                doc.close() 
                        else:
                            img = Image.open(f).convert("RGB")
                            img.thumbnail((1200, 1200))
                            img.save(IMG_DIR / f"tegning_{img_count}.jpg", "JPEG", quality=85)
                            img_count += 1
                            # Also save original image to project_files
                            f.seek(0)
                            (FILES_DIR / f.name).write_bytes(f.read())
                except Exception as e:
                    st.warning(f"Kunne ikke lagre alle filer: {e}")

            # Save extra project files (DWG, IFC, DXF, XLSX, DOCX etc)
            extra_files = st.session_state.get("project_extra_files", [])
            if extra_files:
                try:
                    for f in extra_files:
                        f.seek(0)
                        (FILES_DIR / f.name).write_bytes(f.read())
                except Exception as e:
                    st.warning(f"Kunne ikke lagre tilleggsfiler: {e}")
                
            st.session_state.project_data.update({
                "land": new_land, "p_name": new_p_name, "c_name": new_c_name, "p_desc": new_p_desc,
                "adresse": new_adresse, "kommune": new_kommune, "gnr": new_gnr, "bnr": new_bnr,
                "b_type": new_b_type, "etasjer": new_etasjer, "bta": new_bta, "tomteareal": new_tomteareal,
                "last_sync": datetime.now().strftime("%d. %b %Y kl %H:%M")
            })
            
            with open(SSOT_FILE, "w", encoding="utf-8") as f:
                json.dump(st.session_state.project_data, f, ensure_ascii=False, indent=4)
                
            st.success(f"✅ Data er lagret trygt på serveren! Prosjektet '{new_p_name}' er nå tilgjengelig for alle moduler.")
            time.sleep(1)
            st.rerun()

with snap_col:
    render_html(f"""
    <div class="card" style="padding: 1.5rem; position: sticky; top: 2rem;">
        <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.1em; color:var(--muted); margin-bottom:0.2rem;">Live Snapshot</div>
        <h3 style="margin-top:0; margin-bottom:0.5rem; font-size:1.2rem;">Prosjektsammendrag</h3>
        <p style="color:var(--soft); font-size:0.85rem; margin-bottom:1.5rem; line-height:1.5;">Et raskt overblikk over SSOT-dataene slik de ligger i databasen nå.</p>
        <div class="snap-row"><div class="snap-label">Regelverk</div><div class="snap-val" style="color:var(--accent);">{pd_state.get("land", "-").split(' ')[0]}</div></div>
        <div class="snap-row"><div class="snap-label">Prosjekt</div><div class="snap-val">{pd_state.get("p_name") or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Oppdragsgiver</div><div class="snap-val">{pd_state.get("c_name") or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Adresse</div><div class="snap-val">{pd_state.get("adresse") or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Kommune</div><div class="snap-val">{pd_state.get("kommune") or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Gnr / Bnr</div><div class="snap-val">{' / '.join(filter(None, [pd_state.get("gnr"), pd_state.get("bnr")])) or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Type</div><div class="snap-val">{pd_state.get("b_type", "-")}</div></div>
        <div class="snap-row" style="border-bottom:none;"><div class="snap-label">Volum/Tomt</div><div class="snap-val">{pd_state.get("bta", "0")} m² / {pd_state.get("tomteareal", "0")} m²</div></div>
        <div class="snap-row" style="border-bottom:none; margin-top:0.5rem;"><div class="snap-label">Tegninger lagret</div><div class="snap-val" style="color:#7ee081;">{saved_image_count} sider klare</div></div>
        <div class="snap-row" style="border-bottom:none;"><div class="snap-label">Prosjektfiler</div><div class="snap-val" style="color:#7ee081;">{saved_files_count} filer lagret</div></div>
    </div>
    """)

# --- 8. LAUNCHPAD ---
def render_module_card(col, icon, badge, badge_class, title, desc, input_txt, output_txt, btn_label, page_target):
    with col:
        st.markdown('<div class="module-card-hook"></div>', unsafe_allow_html=True)
        st.markdown(f"""
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:1rem;">
                <div class="module-icon">{icon}</div>
                <div class="module-badge {badge_class}">{badge}</div>
            </div>
            <div style="font-size:1.08rem; font-weight:720; color:#f5f7fb; margin-bottom:0.5rem; line-height: 1.35;">{title}</div>
            <div style="font-size:0.95rem; color:#9fb0c3; line-height:1.6; margin-bottom:1rem; min-height: 75px;">{desc}</div>
            <div style="font-size:0.86rem; color:#c8d3df; padding-top:0.95rem; border-top:1px solid rgba(120,145,170,0.14); min-height: 65px;">
                <strong>Input:</strong> {input_txt}<br>
                <strong>Output:</strong> {output_txt}
            </div>
        """, unsafe_allow_html=True)

        if page_target and find_page(page_target):
            if st.button(btn_label, key=f"btn_{page_target}", type="secondary", use_container_width=True):
                st.switch_page(find_page(page_target))
        else:
            st.button("In development", key=f"btn_{page_target}_dev", type="secondary", disabled=True, use_container_width=True)

if True:  # Moduler alltid synlige i utviklingsfasen – fjern denne linjen for å aktivere kompletthetskravet (completeness > 30)
    st.markdown("<hr style='border-color: rgba(120,145,170,0.2); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    
    st.markdown("<h3 style='margin-bottom: 1.5rem; font-weight:750;'>🛠️ Prosjektering & Fagmoduler</h3>", unsafe_allow_html=True)
    
    r1c1, r1c2, r1c3 = st.columns(3)
    render_module_card(r1c1, "🌍", "Phase 1 - Priority", "badge-priority", "GEO / ENV - Ground Conditions", 
                       "Analyze lab files and excavation plans. Classifies masses, proposes disposal logic, and drafts environmental action plans.", 
                       "XLSX / CSV / PDF + plans", "Environmental action plan, logs", "Åpne Geo & Miljø", "Geo")
    render_module_card(r1c2, "🔊", "Phase 2", "badge-phase2", "ACOUSTICS - Noise & Sound", 
                       "Ingest noise maps and floor plans. Generates facade requirements, window specifications, and mitigation strategies.", 
                       "Noise map + floor plan", "Acoustics report, facade eval.", "Åpne Akustikk", "Akustikk")
    render_module_card(r1c3, "🔥", "Phase 2", "badge-phase2", "FIRE - Safety Strategy", 
                       "Evaluate architectural drawings against building codes. Generates escape routes, fire cell division, and fire strategy.", 
                       "Architectural drawings + class", "Fire strategy concept, deviations", "Åpne Brannkonsept", "Brannkonsept")

    st.markdown("<br>", unsafe_allow_html=True)

    r2c1, r2c2, r2c3 = st.columns(3)
    render_module_card(r2c1, "📐", "Early phase", "badge-early", "ARK - Feasibility Study", 
                       "Site screening, volume analysis, and early-phase decision support before full engineering design.", 
                       "Site data, zoning plans", "Feasibility report, utilization metrics", "Åpne Mulighetsstudie", "Mulighetsstudie")
    render_module_card(r2c2, "🏢", "Roadmap", "badge-roadmap", "STRUC - Structural Concept", 
                       "Conceptual structural checks, principle dimensioning, and integration with carbon footprint estimations.", 
                       "Models, load parameters", "Concept memo, grid layouts", "Åpne Konstruksjon", "Konstruksjon")
    render_module_card(r2c3, "🚦", "Roadmap", "badge-roadmap", "TRAFFIC - Mobility", 
                       "Traffic generation, parking requirements, access logic, and soft-mobility planning for early project phases.", 
                       "Site plans, local norms", "Traffic memo, mobility plan", "Åpne Trafikk & Mobilitet", "Trafikk")

    # --- NY SEKSJON: LEDELSE & BÆREKRAFT ---
    st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    st.markdown("<h3 style='margin-bottom: 1.5rem; font-weight:750;'>Bærekraft & Prosjektstyring (Tilleggsmoduler)</h3>", unsafe_allow_html=True)
    
    r3c1, r3c2, r3c3 = st.columns(3)
    render_module_card(r3c1, "🦺", "Management", "badge-priority", "SHA-Plan", 
                       "Sikkerhet, helse og arbeidsmiljø. Genererer rutiner for rigg, logistikk og risikofylte operasjoner basert på tomten.", 
                       "Prosjektdata + risikomoment", "Komplett SHA-plan", "Åpne SHA-modul", "SHA")
    render_module_card(r3c2, "🌿", "Sustainability", "badge-phase2", "BREEAM Assistant", 
                       "Tidligfase vurdering av BREEAM-NOR potensial, poengkrav og materialstrategi for prosjektet.", 
                       "Byggdata + Ambisjonsnivå", "BREEAM Pre-assessment", "Åpne BREEAM", "BREEAM")
    render_module_card(r3c3, "♻️", "Environment", "badge-roadmap", "Miljøoppfølging (MOP)", 
                       "Miljøoppfølgingsplan for byggeplass. Vurderer avfallshåndtering, ombruk, utslipp og bevaring av natur.", 
                       "Prosjektdata + miljømål", "MOP Dokument", "Åpne MOP", "MOP")

    # --- SEKSJON: ANBUD & ENTREPRISE ---
    st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    st.markdown("""
        <h3 style='margin-bottom: 0.4rem; font-weight:750;'>🏗️ Anbud & Entreprise</h3>
        <p style='color:#9fb0c3; font-size:0.95rem; margin-bottom:1.5rem; line-height:1.6;'>
            Analyser anbudsdokumenter, mengdebeskrivelser og kontrakter automatisk.
            Reduser risiko og tidsbruk i tilbudsarbeid.
        </p>
    """, unsafe_allow_html=True)

    r4c1, r4c2, r4c3 = st.columns(3)
    render_module_card(r4c1, "📑", "Commercial", "badge-priority", "ANBUDSKONTROLL – Tilbudsgrunnlag & QA",
                       "Sammenligner konkurransegrunnlag, tegninger og tilbudsinput. Genererer avviksmatrise, mangelliste, uklarhetslogg og forslag til spørsmål.",
                       "Anbudsgrunnlag + tegninger + IFC/PDF", "Avviksmatrise, scope-logg, RFIs",
                       "Åpne Tender Control", "TenderControl")
    render_module_card(r4c2, "📏", "Core engine", "badge-phase2", "MENGDE & SCOPE – Revisjon og sporbarhet",
                       "Fanger mengder, arealer, revisjonsendringer og sporbarhet mellom modell, tegning og beskrivelse.",
                       "IFC / PDF / BOQ / romdata", "Mengdeliste, areallogg, deltarapport",
                       "Åpne Mengde & Scope", "QuantityScope")
    render_module_card(r4c3, "🏙️", "Developer-first", "badge-early", "AREAL & YIELD – Utvikleroptimalisering",
                       "Analyserer brutto/netto, salgbart og utleibart areal, kjerneandel, tekniske rom og scenarioer for mer verdiskaping.",
                       "Plangrunnlag + arealprogram", "Yield-notat, scenarioer, verdiøkning",
                       "Åpne Yield Optimizer", "YieldOptimizer")

    # --- SEKSJON: KLIMA, PORTEFØLJE & FINANS ---
    st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    st.markdown("""
        <h3 style='margin-bottom: 0.4rem; font-weight:750;'>🏦 Klima, Portefølje & Finans</h3>
        <p style='color:#9fb0c3; font-size:0.95rem; margin-bottom:1.5rem; line-height:1.6;'>
            Porteføljescreening, klimarisiko og teknisk due diligence for banker,
            forsikringsselskaper og eiendomsinvestorer. Nivå 1 (auto) – ingen fagperson-review påkrevd.
        </p>
    """, unsafe_allow_html=True)

    r5c1, r5c2 = st.columns(2)
    render_module_card(r5c1, "🌊", "Portfolio", "badge-phase2", "KLIMARISIKO – Eiendom & portefølje",
                       "Skårer flom, skred, havnivå og varmestress per eiendom og mapper output mot EU Taxonomy, SFDR og bankrapportering.",
                       "Adresse / koordinater + eksponering", "Klimarisikoscore, taxonomy-mapping",
                       "Åpne Klimarisiko", "ClimateRisk")
    render_module_card(r5c2, "🏦", "Finance", "badge-phase2", "TEKNISK DUE DILIGENCE (TDD)",
                       "Automatisert TDD-rapport for eiendomstransaksjoner. Aggregerer tilstand, avvik mot TEK17, restlevetid og risikoprofil.",
                       "Tegninger + ferdigattest + FDV + tilstandsrapport", "TDD-rapport, risikomatrise, tiltaksliste",
                       "Åpne TDD", "TDD")

    # --- SEKSJON: BANK & FINANSIERING ---
    st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    st.markdown("""
        <h3 style='margin-bottom: 0.4rem; font-weight:750;'>🏗️ Bank & Finansiering</h3>
        <p style='color:#9fb0c3; font-size:0.95rem; margin-bottom:1.5rem; line-height:1.6;'>
            Byggelånskontroll, kredittgrunnlag og beslutningsstøtte for banker og kredittgivere.
            Automatisert datainnhenting og strukturert rapportering.
        </p>
    """, unsafe_allow_html=True)

    r6c1, r6c2 = st.columns(2)
    render_module_card(r6c1, "🏗️", "Byggelån", "badge-phase2", "BYGGELÅNSKONTROLL – Utbetalingskontroll & verifisering",
                       "Verifiserer trekkforespørsler mot byggebudsjett, fremdriftsplan og kontraktsgrunnlag. Genererer bankens kontrollrapport med avvik og godkjenningsgrunnlag.",
                       "Trekkforespørsel + budsjett + fremdriftsplan", "Kontrollrapport, avvikslogg, godkjenningsgrunnlag",
                       "Åpne Byggelånskontroll", "Byggelanskontroll")
    render_module_card(r6c2, "📋", "Kreditt", "badge-phase2", "KREDITTGRUNNLAG – Beslutningsstøtte for kredittkomité",
                       "Sammenstiller tekniske, regulatoriske og finansielle data til et strukturert kredittgrunnlag for tomtelån, byggelån og utleielån.",
                       "Prosjektdata + eiendomsinfo + finansstruktur", "Kredittmemo, risikomatrise, beslutningsgrunnlag",
                       "Åpne Kredittgrunnlag", "Kredittgrunnlag")
