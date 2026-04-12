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

_SLUG_MAP = {"🇳🇴 Norsk": "no", "🇺🇸 English (US)": "en-us", "🇬🇧 English (UK)": "en-gb",
             "🇸🇪 Svenska": "sv", "🇩🇰 Dansk": "da", "🇫🇮 Suomi": "fi", "🇩🇪 Deutsch": "de"}

def go_home():
    _slug = _SLUG_MAP.get(st.session_state.get("app_lang", "🇺🇸 English (US)"), "en-us")
    try:
        st.query_params["lang"] = _slug
    except Exception:
        pass
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


# --- 4. LANGUAGE & TRANSLATIONS ---
# Read language from query param (passed by frontpage navigation links)
_LANG_FROM_QUERY = {
    "no": "🇳🇴 Norsk", "en-us": "🇺🇸 English (US)", "en-gb": "🇬🇧 English (UK)",
    "sv": "🇸🇪 Svenska", "da": "🇩🇰 Dansk", "fi": "🇫🇮 Suomi", "de": "🇩🇪 Deutsch",
}
_lang_param = str(st.query_params.get("lang", "")).strip().lower()
if _lang_param in _LANG_FROM_QUERY:
    st.session_state.app_lang = _LANG_FROM_QUERY[_lang_param]

_lang = st.session_state.get("app_lang", "🇺🇸 English (US)")
_en = "English" in _lang

T = {
    "back": "← Back to Portal" if _en else "← Tilbake til Portal",
    "qa_btn": "QA & Sign-off",
    "hero_title": "Project Configuration",
    "hero_sub": ("One control center for your project's core parameters. Sync project context seamlessly "
                 "to technical engineering, sustainability analyses and project management.") if _en else
                ("Ett kontrollsenter for prosjektets kjerneparametere. Synkroniser prosjektets kontekst "
                 "sømløst til teknisk prosjektering, bærekraftsanalyser og prosjektledelse."),
    "sync_status": "Sync Status",
    "draft": "Draft",
    "ready": "Ready",
    "fields_filled": "{n} of {total} key fields are filled and available for AI modules." if _en else
                     "{n} av {total} nøkkelfelt er fylt ut og tilgjengelige for AI-modulene.",
    "completeness": "Completeness" if _en else "Kompletthet",
    "last_updated": "Last updated" if _en else "Sist oppdatert",
    "not_synced": "Not synced yet" if _en else "Ikke synket enda",
    "location_set": "Location set" if _en else "Lokasjon satt",
    "yes": "Yes" if _en else "Ja",
    "no": "No" if _en else "Nei",
    "data_completeness": "Data completeness" if _en else "Datakompletthet",
    "primary_use": "Primary Use" if _en else "Primær Bruk",
    "building_area": "Building Area" if _en else "Bygningsareal",
    "site_area": "Site Area" if _en else "Tomteareal",
    "update_title": "Update the project control center" if _en else "Oppdater prosjektets kontrollsenter",
    "update_sub": ("Fill in the data below. This is automatically fed into all AI agents to ensure consistency.") if _en else
                  ("Fyll ut dataene under. Dette mates automatisk inn i alle AI-agenter for å sikre samsvar."),
    "sec_general": "📌 01 General" if _en else "📌 01 Generelt",
    "country_label": "🌍 Country / Local Regulations" if _en else "🌍 Land / Lokalt Regelverk",
    "project_name": "Project name" if _en else "Prosjektnavn",
    "client": "Client / Owner" if _en else "Tiltakshaver / Oppdragsgiver",
    "description": "Project description / Narrative" if _en else "Prosjektbeskrivelse / Narrativ",
    "sec_location": "🌍 02 Location & API" if _en else "🌍 02 Lokasjon & API",
    "api_hint_no": ("💡 **Kartverket API:** Enter address *or* parcel number (Gnr/Bnr) and click the button to auto-fill." if _en else
                    "💡 **Kartverket API:** Skriv inn adresse *eller* Gnr/Bnr og trykk på knappen for å autoutfylle resten."),
    "api_hint_en": "💡 **Property lookup:** Enter address or parcel ID and click the button to auto-fill." if _en else
                   "💡 **Eiendomsoppslag:** Skriv inn adresse eller eiendoms-ID og klikk for å autoutfylle.",
    "address": "Street address" if _en else "Gateadresse",
    "municipality": "Municipality" if _en else "Kommune",
    "gnr": "Parcel number (Gnr)" if _en else "Gårdsnummer (Gnr)",
    "bnr": "Sub-parcel (Bnr)" if _en else "Bruksnummer (Bnr)",
    "search_btn": "🔍 Search cadastre + Fetch site data" if _en else "🔍 Søk i Matrikkel + Hent Tomtedata (Kartverket)",
    "found_address": "✅ Found property in address registry!" if _en else "✅ Fant eiendom i adresseregisteret!",
    "no_address": ("No address match. Trying to fetch parcel boundary from Gnr/Bnr...") if _en else
                  ("Fant ingen adressetreff. Prøver å hente tomtegrense direkte fra Gnr/Bnr..."),
    "parcel_ok": "✅ Parcel boundary fetched! Area: **{area} m²** ({source})" if _en else
                 "✅ Tomtegrense hentet! Areal: **{area} m²** ({source})",
    "ortofoto_ok": "✅ Aerial photo fetched!" if _en else "✅ Ortofoto hentet!",
    "ortofoto_fail": "Could not fetch aerial photo for this property." if _en else "Kunne ikke hente ortofoto for denne eiendommen.",
    "parcel_fail": ("No parcel boundary found. Site area must be entered manually.") if _en else
                   ("Fant ingen tomtegrense i WFS-tjenesten. Tomteareal må fylles inn manuelt."),
    "ortofoto_label": "📸 Aerial photo" if _en else "📸 Ortofoto",
    "sec_building": "🏢 03 Building & Site Data" if _en else "🏢 03 Bygg- og Tomtedata",
    "use_type": "Primary Use" if _en else "Primær Bruk",
    "floors": "Floors" if _en else "Etasjer",
    "bta": "Building area (GFA m²)" if _en else "Bygningsareal (BTA m²)",
    "site_m2": "Site area (m²)" if _en else "Tomteareal (m²)",
    "sec_drawings": "📁 04 Drawings (AI Quality Check)" if _en else "📁 04 Tegningsgrunnlag (AI Kvalitetssikring)",
    "drawings_hint": ("Upload drawings to let AI assess the quality of the documentation *before* it is sent to the engineering modules. "
                      "AI will check whether plan, section, elevation and site plan are complete.") if _en else
                     ("Last opp tegninger her for å la AI-en vurdere kvaliteten på underlaget *før* det sendes til fagmodulene. "
                      "AI-en vil sjekke om plan, snitt, fasade og situasjonsplan er komplett."),
    "upload_drawings": "Upload Elevation, Plan, Section and Site Plan (PDF/Images)" if _en else
                       "Last opp Fasade, Plan, Snitt og Situasjonsplan (PDF/Bilder)",
    "upload_other": "Upload other project documents (DWG, IFC, DXF, XLSX, DOCX)" if _en else
                    "Last opp andre prosjektdokumenter (DWG, IFC, DXF, XLSX, DOCX)",
    "upload_other_help": ("These files are stored in the project folder and available to all engineering modules.") if _en else
                         ("Disse filene lagres i prosjektmappen og er tilgjengelig for alle fagmoduler (TDD, Anbudskontroll, Mengde & Scope, Yield osv)."),
    "ai_analyze": "🤖 Analyze & QA Drawings with AI" if _en else "🤖 Analyser & Kvalitetssikre Tegninger med AI",
    "ai_missing_key": "Google API key missing!" if _en else "Google API-nøkkel mangler!",
    "ai_spinner": "AI is studying the drawings..." if _en else "AI studerer tegningene...",
    "ai_result_title": "📊 AI Drawing Assessment" if _en else "📊 AI-Vurdering av Tegningsgrunnlag",
    "qa_required": ("⚠️ **Action required:** You have uploaded new drawings. Run the AI quality check (button above) before saving.") if _en else
                   ("⚠️ **Handling kreves:** Du har lastet opp nye tegninger. Du må kjøre AI-kvalitetssikringen (knappen over) før du kan lagre prosjektet."),
    "save_btn": "💾 Save & Sync SSOT Data" if _en else "💾 Lagre & Synkroniser SSOT Data",
    "save_ok": "✅ Data saved! Project '{name}' is now available to all modules." if _en else
               "✅ Data er lagret trygt på serveren! Prosjektet '{name}' er nå tilgjengelig for alle moduler.",
    "snapshot_title": "Project Summary" if _en else "Prosjektsammendrag",
    "snapshot_sub": ("A quick overview of the SSOT data as it currently exists in the database.") if _en else
                    ("En rask oversikt over SSOT-data slik de nå er lagret i databasen."),
    "snap_rules": "Regulations" if _en else "Regelverk",
    "snap_project": "Project" if _en else "Prosjekt",
    "snap_client": "Client" if _en else "Oppdragsgiver",
    "snap_address": "Address" if _en else "Adresse",
    "snap_municipality": "Municipality" if _en else "Kommune",
    "snap_gnr_bnr": "Gnr / Bnr",
    "snap_type": "Type",
    "snap_volume": "Volume/Site" if _en else "Volum/Tomt",
    "snap_drawings": "Drawings stored" if _en else "Tegninger lagret",
    "snap_drawings_val": "{n} pages ready" if _en else "{n} sider klare",
    "snap_files": "Project files" if _en else "Prosjektfiler",
    "snap_files_val": "{n} files stored" if _en else "{n} filer lagret",
    "mod_title": "🛠️ Engineering & Modules" if _en else "🛠️ Prosjektering & Fagmoduler",
    "use_types": (["Residential (Apartment/Townhouse)", "Commercial / Office", "Retail", "Public / School", "Industrial / Warehouse"] if _en else
                  ["Bolig (Leilighet/Rekkehus)", "Næring / Kontor", "Handel", "Offentlig / Skole", "Industri / Lager"]),
}

# --- 5. SESSION STATE LOGIKK (MED SELVHELBRENDENDE SIKKERHETSNETT) ---
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
    logo_html = f'<a href="/?lang={_SLUG_MAP.get(_lang, "en-us")}" target="_self" style="text-decoration:none;"><img src="{logo_data_uri()}" class="brand-logo"></a>' if logo_data_uri() else f'<a href="/?lang={_SLUG_MAP.get(_lang, "en-us")}" target="_self"><h2 style="margin:0; color:white;">Builtly</h2></a>'
    render_html(logo_html)
with c2:
    st.markdown("<div style='margin-top: 0.8rem;'></div>", unsafe_allow_html=True)
    if st.button(T["back"], use_container_width=True, type="secondary"):
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
        <h1 class="hero-title">{T['hero_title']}</h1>
        <div class="hero-sub">{T['hero_sub']}</div>
    </div>
    
    <div class="card">
        <div class="status-kicker">{T['sync_status']}</div>
        <div class="status-title">{sync_status}</div>
        <div class="status-desc">{T['fields_filled'].format(n=filled_fields, total=len(fields_to_check))}</div>
        
        <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:0.5rem; color: #c8d3df;">
            <span>{T['completeness']}</span><span style="font-weight:700;">{completeness}%</span>
        </div>
        <div class="prog-bar-bg"><div style="width: {completeness}%; height: 100%; background: {progress_color}; border-radius: 999px;"></div></div>
        
        <div class="meta-row"><span class="meta-label">{T['last_updated']}</span><span class="meta-value">{pd_state.get("last_sync", T['not_synced'])}</span></div>
        <div class="meta-row"><span class="meta-label">{T['location_set']}</span><span class="meta-value">{T['yes'] if pd_state.get("adresse") or pd_state.get("gnr") else T['no']}</span></div>
    </div>
</div>

<div class="stat-grid">
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">{T['data_completeness']}</div><div class="stat-value" style="color:{progress_color};">{completeness}%</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">{T['primary_use']}</div><div class="stat-value" style="font-size:1.4rem; padding-top:0.4rem;">{pd_state.get("b_type", "-")}</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">{T['building_area']}</div><div class="stat-value">{pd_state.get("bta", "0")} m²</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">{T['site_area']}</div><div class="stat-value">{pd_state.get("tomteareal", "0")} m²</div>
    </div>
</div>
""")

# --- 7. INPUT SEKSJON ---
st.markdown(f"<h3 style='margin-top: 1rem; margin-bottom: 0.2rem;'>{T['update_title']}</h3>", unsafe_allow_html=True)
st.markdown(f"<p style='color:#9fb0c3; margin-bottom: 1.5rem;'>{T['update_sub']}</p>", unsafe_allow_html=True)

input_col, snap_col = st.columns([2, 1], gap="large")

with input_col:
    st.markdown(f"""<div style="margin-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);"><h4 style="color: #f5f7fb; margin: 0;">{T['sec_general']}</h4></div>""", unsafe_allow_html=True)
    
    land_options = ["Norge (TEK17 / Kartverket)", "Sverige (BBR)", "Danmark (BR18)", "UK (Building Regs)"]
    try: l_idx = land_options.index(pd_state.get("land", "Norge (TEK17 / Kartverket)"))
    except: l_idx = 0
    new_land = st.selectbox(T["country_label"], land_options, index=l_idx)
    
    c1, c2 = st.columns(2)
    new_p_name = c1.text_input(T["project_name"], value=pd_state.get("p_name", ""))
    new_c_name = c2.text_input(T["client"], value=pd_state.get("c_name", ""))
    
    new_p_desc = st.text_area(T["description"], value=pd_state.get("p_desc", ""), height=140)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(f"""<div style="margin-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);"><h4 style="color: #f5f7fb; margin: 0;">{T['sec_location']}</h4></div>""", unsafe_allow_html=True)
    
    if "Norge" in new_land: st.info(T["api_hint_no"])
    else: st.info(T["api_hint_en"])
        
    c3, c4 = st.columns(2)
    new_adresse = c3.text_input(T["address"], value=pd_state.get("adresse", ""))
    new_kommune = c4.text_input(T["municipality"], value=pd_state.get("kommune", ""))
    
    c5, c6 = st.columns(2)
    new_gnr = c5.text_input(T["gnr"], value=pd_state.get("gnr", ""))
    new_bnr = c6.text_input(T["bnr"], value=pd_state.get("bnr", ""))
    
    if "Norge" in new_land:
        if st.button(T["search_btn"], type="secondary"):
            st.session_state.project_data.update({"p_name": new_p_name, "c_name": new_c_name, "p_desc": new_p_desc})
            with st.spinner("..."):
                res = fetch_from_kartverket(new_adresse, new_kommune, new_gnr, new_bnr)
                if res:
                    st.session_state.project_data.update(res)
                    st.success(T["found_address"])
                else:
                    st.warning(T["no_address"])

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
                        st.success(T["parcel_ok"].format(area=f"{int(parcel['area_m2']):,}", source=parcel["source"]))

                        # Fetch ortofoto
                        with st.spinner("Henter ortofoto fra Kartverket..."):
                            ortofoto = fetch_ortofoto_thumbnail(parcel["bounds"])
                            if ortofoto:
                                buf = io.BytesIO()
                                ortofoto.save(buf, format="JPEG", quality=85)
                                st.session_state["_project_ortofoto"] = buf.getvalue()
                                st.success(T["ortofoto_ok"])
                            else:
                                st.info(T["ortofoto_fail"])
                    else:
                        st.warning(T["parcel_fail"])

            time.sleep(0.5)
            st.rerun()

    # Show ortofoto if available
    if st.session_state.get("_project_ortofoto"):
        st.markdown("""<div style="margin-top:0.5rem;margin-bottom:0.5rem;">
            <div style="font-size:0.8rem;color:var(--muted);margin-bottom:0.3rem;">{T["ortofoto_label"]}</div>
        </div>""", unsafe_allow_html=True)
        st.image(st.session_state["_project_ortofoto"], use_container_width=True)
        parcel_src = pd_state.get("_parcel_source", "")
        if parcel_src:
            st.caption(f"Kilde: {parcel_src} · Tomteareal: {pd_state.get('tomteareal', '?')} m²")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""<div style="margin-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);"><h4 style="color: #f5f7fb; margin: 0;">🏢 03 Bygg- og Tomtedata</h4></div>""", unsafe_allow_html=True)
    
    c7, c8, c9, c10 = st.columns(4)
    type_options = T["use_types"]
    try: default_idx = type_options.index(pd_state.get("b_type", "Næring / Kontor"))
    except: default_idx = 1
    
    new_b_type = c7.selectbox(T["use_type"], type_options, index=default_idx)
    new_etasjer = c8.number_input(T["floors"], value=int(pd_state.get("etasjer", 1)), min_value=1)
    new_bta = c9.number_input(T["bta"], value=int(pd_state.get("bta", 0)), step=100)
    new_tomteareal = c10.number_input(T["site_m2"], value=int(pd_state.get("tomteareal", 0)), step=100)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""<div style="margin-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);"><h4 style="color: #f5f7fb; margin: 0;">📁 04 Tegningsgrunnlag (AI Kvalitetssikring)</h4></div>""", unsafe_allow_html=True)
    st.info(T["drawings_hint"])
    
    uploaded_drawings = st.file_uploader(T["upload_drawings"], accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

    st.file_uploader(
        T["upload_other"],
        accept_multiple_files=True,
        type=['dwg', 'dxf', 'ifc', 'xlsx', 'xls', 'docx', 'csv', 'zip'],
        key="project_extra_files",
        help=T["upload_other_help"],
    )
    current_file_names = [f.name for f in uploaded_drawings] if uploaded_drawings else []
    
    if uploaded_drawings:
        if st.button(T["ai_analyze"], type="secondary"):
            if not google_key:
                st.error(T["ai_missing_key"])
            else:
                with st.spinner(T["ai_spinner"]):
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
        st.warning(T["qa_required"])
        st.button(T["save_btn"], type="primary", disabled=True, use_container_width=True)
    else:
        if st.button(T["save_btn"], type="primary", use_container_width=True):
            
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
                
            st.success(T["save_ok"].format(name=new_p_name))
            time.sleep(1)
            st.rerun()

with snap_col:
    render_html(f"""
    <div class="card" style="padding: 1.5rem; position: sticky; top: 2rem;">
        <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.1em; color:var(--muted); margin-bottom:0.2rem;">Live Snapshot</div>
        <h3 style="margin-top:0; margin-bottom:0.5rem; font-size:1.2rem;">{T["snapshot_title"]}</h3>
        <p style="color:var(--soft); font-size:0.85rem; margin-bottom:1.5rem; line-height:1.5;">{T["snapshot_sub"]}</p>
        <div class="snap-row"><div class="snap-label">{T["snap_rules"]}</div><div class="snap-val" style="color:var(--accent);">{pd_state.get("land", "-").split(' ')[0]}</div></div>
        <div class="snap-row"><div class="snap-label">{T["snap_project"]}</div><div class="snap-val">{pd_state.get("p_name") or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">{T["snap_client"]}</div><div class="snap-val">{pd_state.get("c_name") or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">{T["snap_address"]}</div><div class="snap-val">{pd_state.get("adresse") or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">{T["snap_municipality"]}</div><div class="snap-val">{pd_state.get("kommune") or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">{T["snap_gnr_bnr"]}</div><div class="snap-val">{' / '.join(filter(None, [pd_state.get("gnr"), pd_state.get("bnr")])) or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">{T["snap_type"]}</div><div class="snap-val">{pd_state.get("b_type", "-")}</div></div>
        <div class="snap-row" style="border-bottom:none;"><div class="snap-label">{T["snap_volume"]}</div><div class="snap-val">{pd_state.get("bta", "0")} m² / {pd_state.get("tomteareal", "0")} m²</div></div>
        <div class="snap-row" style="border-bottom:none; margin-top:0.5rem;"><div class="snap-label">{T["snap_drawings"]}</div><div class="snap-val" style="color:#7ee081;">{T["snap_drawings_val"].format(n=saved_image_count)}</div></div>
        <div class="snap-row" style="border-bottom:none;"><div class="snap-label">{T["snap_files"]}</div><div class="snap-val" style="color:#7ee081;">{T["snap_files_val"].format(n=saved_files_count)}</div></div>
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

if True:  # Moduler alltid synlige i utviklingsfasen
    st.markdown("<hr style='border-color: rgba(120,145,170,0.2); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    
    st.markdown(f"<h3 style='margin-bottom: 1.5rem; font-weight:750;'>{T['mod_title']}</h3>", unsafe_allow_html=True)
    
    r1c1, r1c2, r1c3 = st.columns(3)
    render_module_card(r1c1, "🌍", "Phase 1 - Priority", "badge-priority", "GEO / ENV - Ground Conditions", 
                       "Analyze lab files and excavation plans. Classifies masses, proposes disposal logic, and drafts environmental action plans." if _en else
                       "Analyserer lab-filer og graveceller. Klassifiserer masser og utarbeider tiltaksplaner.", 
                       "XLSX / CSV / PDF + plans", "Environmental action plan, logs" if _en else "Tiltaksplan, logg",
                       "Open Geo & Env" if _en else "Åpne Geo & Miljø", "Geo")
    render_module_card(r1c2, "🔊", "Phase 2", "badge-phase2", "ACOUSTICS - Noise & Sound" if _en else "AKUSTIKK - Støy & Lyd", 
                       "Ingest noise maps and floor plans. Generates facade requirements, window specifications, and mitigation strategies." if _en else
                       "Leser støykart og plantegninger. Genererer krav til fasade, vinduer og skjerming.", 
                       "Noise map + floor plan" if _en else "Støykart + Plan",
                       "Acoustics report, facade eval." if _en else "Akustikkrapport",
                       "Open Acoustics" if _en else "Åpne Akustikk", "Akustikk")
    render_module_card(r1c3, "🔥", "Phase 2", "badge-phase2", "FIRE - Safety Strategy" if _en else "BRANN - Sikkerhetskonsept", 
                       "Evaluate architectural drawings against building codes. Generates escape routes, fire cell division, and fire strategy." if _en else
                       "Vurderer arkitektur mot forskrifter. Definerer rømning og brannceller.", 
                       "Architectural drawings + class" if _en else "Tegninger + Klasse",
                       "Fire strategy concept, deviations" if _en else "Brannkonsept (RIBr)",
                       "Open Fire Strategy" if _en else "Åpne Brannkonsept", "Brannkonsept")

    st.markdown("<br>", unsafe_allow_html=True)

    r2c1, r2c2, r2c3 = st.columns(3)
    render_module_card(r2c1, "📐", "Early phase", "badge-early", "ARK - Feasibility Study" if _en else "ARK - Mulighetsstudie", 
                       "Site screening, volume analysis, and early-phase decision support before full engineering design." if _en else
                       "Tomtescreening, volumanalyse og tidligfase-beslutningsstøtte.", 
                       "Site data, zoning plans" if _en else "Tomtedata, reguleringsplaner",
                       "Feasibility report, utilization metrics" if _en else "Mulighetsstudie, utnyttelsestall",
                       "Open Feasibility" if _en else "Åpne Mulighetsstudie", "Mulighetsstudie")
    render_module_card(r2c2, "🏢", "Roadmap", "badge-roadmap", "STRUC - Structural Concept" if _en else "RIB - Konstruksjonskonsept", 
                       "Conceptual structural checks, principle dimensioning, and integration with carbon footprint estimations." if _en else
                       "Konseptuelle konstruksjonssjekker, prinsippdimensjonering og karbonfotavtrykksvurdering.", 
                       "Models, load parameters" if _en else "Modeller, lastparametre",
                       "Concept memo, grid layouts" if _en else "Konseptnotat, aksesystem",
                       "Open Structural" if _en else "Åpne Konstruksjon", "Konstruksjon")
    render_module_card(r2c3, "🚦", "Roadmap", "badge-roadmap", "TRAFFIC - Mobility" if _en else "TRAFIKK - Mobilitet", 
                       "Traffic generation, parking requirements, access logic, and soft-mobility planning for early project phases." if _en else
                       "Trafikkgenerering, parkeringskrav, adkomstlogikk og myke mobilitetstiltak.", 
                       "Site plans, local norms" if _en else "Situasjonsplan, lokale normer",
                       "Traffic memo, mobility plan" if _en else "Trafikknotat, mobilitetsplan",
                       "Open Traffic & Mobility" if _en else "Åpne Trafikk & Mobilitet", "Trafikk")

    # --- Sustainability & Management ---
    st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    st.markdown(f"<h3 style='margin-bottom: 1.5rem; font-weight:750;'>{'Sustainability & Project Management (Add-on modules)' if _en else 'Bærekraft & Prosjektstyring (Tilleggsmoduler)'}</h3>", unsafe_allow_html=True)
    
    r3c1, r3c2, r3c3 = st.columns(3)
    render_module_card(r3c1, "🦺", "Management", "badge-priority", "SHA - Safety & Health Plan" if _en else "SHA-Plan", 
                       "Safety, health, and working environment. Generates routines for site logistics and high-risk operations." if _en else
                       "Sikkerhet, helse og arbeidsmiljø. Genererer rutiner for rigg, logistikk og risikofylte operasjoner basert på tomten.", 
                       "Project data + risk factors" if _en else "Prosjektdata + risikomoment",
                       "Complete SHA plan" if _en else "Komplett SHA-plan",
                       "Open SHA Module" if _en else "Åpne SHA-modul", "SHA")
    render_module_card(r3c2, "🌿", "Sustainability", "badge-phase2", "BREEAM Assistant", 
                       "Early-phase assessment of BREEAM potential, credit targets and material strategy." if _en else
                       "Tidligfase vurdering av BREEAM-NOR potensial, poengkrav og materialstrategi for prosjektet.", 
                       "Building data + ambition level" if _en else "Byggdata + Ambisjonsnivå",
                       "BREEAM Pre-assessment",
                       "Open BREEAM" if _en else "Åpne BREEAM", "BREEAM")
    render_module_card(r3c3, "♻️", "Environment", "badge-roadmap", "Environmental Follow-up (EFP)" if _en else "Miljøoppfølging (MOP)", 
                       "Environmental follow-up plan for construction sites. Waste management, reuse, emissions and nature preservation." if _en else
                       "Miljøoppfølgingsplan for byggeplass. Vurderer avfallshåndtering, ombruk, utslipp og bevaring av natur.", 
                       "Project data + env. targets" if _en else "Prosjektdata + miljømål",
                       "EFP Document" if _en else "MOP Dokument",
                       "Open EFP" if _en else "Åpne MOP", "MOP")

    # --- Tender & Contracting ---
    st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    st.markdown(f"""
        <h3 style='margin-bottom: 0.4rem; font-weight:750;'>🏗️ {'Tender & Contracting' if _en else 'Anbud & Entreprise'}</h3>
        <p style='color:#9fb0c3; font-size:0.95rem; margin-bottom:1.5rem; line-height:1.6;'>
            {'Analyze tender documents, quantity descriptions and contracts automatically. Reduce risk and time spent on bid preparation.' if _en else
             'Analyser anbudsdokumenter, mengdebeskrivelser og kontrakter automatisk. Reduser risiko og tidsbruk i tilbudsarbeid.'}
        </p>
    """, unsafe_allow_html=True)

    r4c1, r4c2, r4c3 = st.columns(3)
    render_module_card(r4c1, "📑", "Commercial", "badge-priority",
                       "TENDER CONTROL – Bid Review & QA" if _en else "ANBUDSKONTROLL – Tilbudsgrunnlag & QA",
                       "Compares tender documents, drawings and bid input. Generates deviation matrix, deficiency log, ambiguity log and proposed RFIs." if _en else
                       "Sammenligner konkurransegrunnlag, tegninger og tilbudsinput. Genererer avviksmatrise, mangelliste, uklarhetslogg og forslag til spørsmål.",
                       "Tender docs + drawings + IFC/PDF" if _en else "Anbudsgrunnlag + tegninger + IFC/PDF",
                       "Deviation matrix, scope log, RFIs" if _en else "Avviksmatrise, scope-logg, RFIs",
                       "Open Tender Control" if _en else "Åpne Tender Control", "TenderControl")
    render_module_card(r4c2, "📏", "Core engine", "badge-phase2",
                       "QUANTITY & SCOPE – Revision & Traceability" if _en else "MENGDE & SCOPE – Revisjon og sporbarhet",
                       "Captures quantities, areas, revision changes and traceability between model, drawing and specification." if _en else
                       "Fanger mengder, arealer, revisjonsendringer og sporbarhet mellom modell, tegning og beskrivelse.",
                       "IFC / PDF / BOQ / room data" if _en else "IFC / PDF / BOQ / romdata",
                       "Quantity list, area log, delta report" if _en else "Mengdeliste, areallogg, deltarapport",
                       "Open Quantity & Scope" if _en else "Åpne Mengde & Scope", "QuantityScope")
    render_module_card(r4c3, "🏙️", "Developer-first", "badge-early",
                       "AREA & YIELD – Developer Optimization" if _en else "AREAL & YIELD – Utvikleroptimalisering",
                       "Analyzes gross/net, sellable and lettable area, core ratio, technical spaces and value optimization scenarios." if _en else
                       "Analyserer brutto/netto, salgbart og utleibart areal, kjerneandel, tekniske rom og scenarioer for mer verdiskaping.",
                       "Plan data + area program" if _en else "Plangrunnlag + arealprogram",
                       "Yield memo, scenarios, value uplift" if _en else "Yield-notat, scenarioer, verdiøkning",
                       "Open Yield Optimizer" if _en else "Åpne Yield Optimizer", "YieldOptimizer")

    # --- Climate, Portfolio & Finance ---
    st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    st.markdown(f"""
        <h3 style='margin-bottom: 0.4rem; font-weight:750;'>🏦 {'Climate, Portfolio & Finance' if _en else 'Klima, Portefølje & Finans'}</h3>
        <p style='color:#9fb0c3; font-size:0.95rem; margin-bottom:1.5rem; line-height:1.6;'>
            {'Portfolio screening, climate risk and technical due diligence for banks and real estate investors. Level 1 (Auto) – no professional review required.' if _en else
             'Porteføljescreening, klimarisiko og teknisk due diligence for banker og eiendomsinvestorer. Nivå 1 (auto) – ingen fagperson-review påkrevd.'}
        </p>
    """, unsafe_allow_html=True)

    r5c1, r5c2 = st.columns(2)
    render_module_card(r5c1, "🌊", "Portfolio", "badge-phase2",
                       "CLIMATE RISK – Property & Portfolio" if _en else "KLIMARISIKO – Eiendom & portefølje",
                       "Scores flood, landslide, sea level and heat stress per property and maps output to EU Taxonomy, SFDR and bank reporting." if _en else
                       "Skårer flom, skred, havnivå og varmestress per eiendom og mapper output mot EU Taxonomy, SFDR og bankrapportering.",
                       "Address / coordinates + exposure" if _en else "Adresse / koordinater + eksponering",
                       "Climate risk score, taxonomy mapping" if _en else "Klimarisikoscore, taxonomy-mapping",
                       "Open Climate Risk" if _en else "Åpne Klimarisiko", "ClimateRisk")
    render_module_card(r5c2, "🏦", "Finance", "badge-phase2",
                       "TECHNICAL DUE DILIGENCE (TDD)" if _en else "TEKNISK DUE DILIGENCE (TDD)",
                       "Automated TDD report for property transactions. Aggregates condition, deviations, remaining lifespan and risk profile." if _en else
                       "Automatisert TDD-rapport for eiendomstransaksjoner. Aggregerer tilstand, avvik mot TEK17, restlevetid og risikoprofil.",
                       "Drawings + completion cert. + O&M + condition report" if _en else "Tegninger + ferdigattest + FDV + tilstandsrapport",
                       "TDD report, risk matrix, action list" if _en else "TDD-rapport, risikomatrise, tiltaksliste",
                       "Open TDD" if _en else "Åpne TDD", "TDD")

    # --- Bank & Financing ---
    st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    st.markdown(f"""
        <h3 style='margin-bottom: 0.4rem; font-weight:750;'>🏗️ {'Bank & Financing' if _en else 'Bank & Finansiering'}</h3>
        <p style='color:#9fb0c3; font-size:0.95rem; margin-bottom:1.5rem; line-height:1.6;'>
            {'Construction loan control, credit assessment and decision support for banks and lenders. Automated data collection and structured reporting.' if _en else
             'Byggelånskontroll, kredittgrunnlag og beslutningsstøtte for banker og kredittgivere. Automatisert datainnhenting og strukturert rapportering.'}
        </p>
    """, unsafe_allow_html=True)

    r6c1, r6c2 = st.columns(2)
    render_module_card(r6c1, "🏗️", "Construction loan" if _en else "Byggelån", "badge-phase2",
                       "LOAN CONTROL – Draw verification & approval" if _en else "BYGGELÅNSKONTROLL – Utbetalingskontroll & verifisering",
                       "Verifies draw requests against construction budget, progress plan and contract basis. Generates bank control report with deviations and approval basis." if _en else
                       "Verifiserer trekkforespørsler mot byggebudsjett, fremdriftsplan og kontraktsgrunnlag. Genererer bankens kontrollrapport med avvik og godkjenningsgrunnlag.",
                       "Draw request + budget + progress plan" if _en else "Trekkforespørsel + budsjett + fremdriftsplan",
                       "Control report, deviation log, approval basis" if _en else "Kontrollrapport, avvikslogg, godkjenningsgrunnlag",
                       "Open Loan Control" if _en else "Åpne Byggelånskontroll", "Byggelanskontroll")
    render_module_card(r6c2, "📋", "Credit" if _en else "Kreditt", "badge-phase2",
                       "CREDIT ASSESSMENT – Decision support for credit committee" if _en else "KREDITTGRUNNLAG – Beslutningsstøtte for kredittkomité",
                       "Compiles technical, regulatory and financial data into a structured credit basis for land loans, construction loans and rental loans." if _en else
                       "Sammenstiller tekniske, regulatoriske og finansielle data til et strukturert kredittgrunnlag for tomtelån, byggelån og utleielån.",
                       "Project data + property info + financial structure" if _en else "Prosjektdata + eiendomsinfo + finansstruktur",
                       "Credit memo, risk matrix, decision basis" if _en else "Kredittmemo, risikomatrise, beslutningsgrunnlag",
                       "Open Credit Assessment" if _en else "Åpne Kredittgrunnlag", "Kredittgrunnlag")
