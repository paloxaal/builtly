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
    main_file = None
    for f in Path(".").glob("*.py"):
        if f.name.lower() not in ["setup.py", "test.py"]:
            main_file = str(f)
            break
    if main_file:
        st.switch_page(main_file)

# --- 2. LOKAL DATABASE (HARDDISK-LAGRING) ---
DB_DIR = Path("qa_database")
IMG_DIR = DB_DIR / "project_images"
SSOT_FILE = DB_DIR / "ssot.json"

def init_db():
    DB_DIR.mkdir(exist_ok=True)
    IMG_DIR.mkdir(exist_ok=True)

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

# --- 5. HEADER ---
c1, c2, c3 = st.columns([2.5, 1, 1])
with c1:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
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
        <div class="meta-row"><span class="meta-label">Lokasjon satt</span><span class="meta-value">{"
