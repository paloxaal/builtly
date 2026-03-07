import streamlit as st
import os
import base64
from pathlib import Path
import requests
from datetime import datetime

# --- 1. GRUNNINNSTILLINGER ---
st.set_page_config(
    page_title="Project Setup | Builtly", 
    page_icon="⚙️", 
    layout="wide", 
    initial_sidebar_state="collapsed"
)

# --- 2. ANTI-BUG RENDERER & LOGO ---
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
        if p.exists():
            return str(p)
    return ""

# --- 3. SESSION STATE LOGIKK (SSOT) ---
if "project_data" not in st.session_state:
    st.session_state.project_data = {
        "land": "Norge (TEK17 / Kartverket)",
        "p_name": "",
        "c_name": "",
        "p_desc": "",
        "adresse": "",
        "kommune": "",
        "gnr": "",
        "bnr": "",
        "b_type": "Næring / Kontor",
        "etasjer": 4,
        "bta": 2500,
        "last_sync": "Ikke synket enda"
    }

pd = st.session_state.project_data

# Kalkulerer data-kompletthet automatisk
fields_to_check = ["p_name", "c_name", "p_desc", "adresse", "kommune", "gnr", "bnr", "b_type", "etasjer", "bta"]
filled_fields = sum(1 for field in fields_to_check if bool(pd[field]))
completeness = int((filled_fields / len(fields_to_check)) * 100)
sync_status = "Draft" if completeness < 100 else "Ready"
progress_color = "#38bdf8" if completeness > 80 else "#f4bf4f" if completeness > 40 else "#ef4444"

# --- 4. KARTVERKET API ---
def fetch_from_kartverket(sok_adresse="", kommune="", gnr="", bnr=""):
    params = {'treffPerSide': 1, 'utkoordsys': 25833}
    if sok_adresse: params['sok'] = sok_adresse
    if kommune: params['kommunenavn'] = kommune
    if gnr: params['gardsnummer'] = gnr
    if bnr: params['bruksnummer'] = bnr

    try:
        r = requests.get("https://ws.geonorge.no/adresser/v1/sok", params=params, timeout=5)
        if r.status_code == 200 and r.json().get('adresser'):
            hit = r.json()['adresser'][0]
            return {
                "adresse": hit.get('adressetekst', ''),
                "kommune": hit.get('kommunenavn', ''),
                "gnr": str(hit.get('gardsnummer', '')),
                "bnr": str(hit.get('bruksnummer', ''))
            }
    except Exception:
        return None
    return None

# --- 5. PREMIUM CSS ---
st.markdown(
    """
<style>
    :root {
        --bg: #06111a; --panel: rgba(10, 22, 35, 0.78); --panel-2: rgba(13, 27, 42, 0.94);
        --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38bdf8; --radius-xl: 24px; --radius-lg: 16px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }

    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    
    /* --- FIKSET TILBAKE-KNAPP (Likt som forsiden) --- */
    .topbar-right {
        display: flex; align-items: center; justify-content: flex-end; gap: 0.65rem;
        padding: 0.35rem; border-radius: 18px; background: rgba(255,255,255,0.025);
        border: 1px solid rgba(120,145,170,0.12); flex-wrap: nowrap !important;
    }
    .top-link {
        display: inline-flex; align-items: center; justify-content: center; min-height: 42px;
        padding: 0.72rem 1.2rem; border-radius: 12px; text-decoration: none !important;
        font-weight: 650; font-size: 0.93rem; transition: all 0.2s ease; border: 1px solid transparent;
        white-space: nowrap;
    }
    .top-link.ghost { color: var(--soft) !important; background: rgba(255,255,255,0.04); border-color: rgba(120,145,170,0.18); }
    .top-link.ghost:hover { color: #ffffff !important; border-color: rgba(56,194,201,0.38); background: rgba(255,255,255,0.06); }

    /* KNAPPE-DESIGN */
    button[kind="primary"] {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important; border: none !important; font-weight: 750 !important;
        border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] {
        background: rgba(255,255,255,0.05) !important; color: #f8fafc !important;
        border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 8px !important; font-weight: 600 !important;
    }
    button[kind="secondary"]:hover { background: rgba(56,194,201,0.1) !important; border-color: #38bdf8 !important; color: #38bdf8 !important; }

    /* INPUT-FELT DESIGN */
    .stTextInput input, .stNumberInput input, .stTextArea textarea {
        background-color: #0d1824 !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important;
        border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {
        border-color: #38bdf8 !important; box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.5) !important;
    }
    div[data-baseweb="select"] > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    div[data-baseweb="select"] span { color: #ffffff !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label {
        color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important;
    }

    /* DASHBOARD BOKSER */
    .dash-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 1.5rem; margin-bottom: 1.5rem; }
    .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1.5rem; margin-bottom: 2.5rem; }
    .card { background: linear-gradient(180deg, rgba(16,30,46,0.8), rgba(10,18,28,0.8)); border: 1px solid var(--stroke); border-radius: var(--radius-lg); padding: 1.8rem; box-shadow: 0 12px 30px rgba(0,0,0,0.2); }
    .card-hero { background: linear-gradient(135deg, rgba(16,30,46,0.9), rgba(6,17,26,0.9)); position: relative; overflow: hidden; }
    .card-hero::after { content: ""; position: absolute; top: -50%; right: -20%; width: 400px; height: 400px; background: radial-gradient(circle, rgba(56,189,248,0.1) 0%, transparent 60%); pointer-events: none; }
    .hero-kicker { display: inline-flex; align-items: center; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 1rem; border: 1px solid var(--stroke); padding: 4px 12px; border-radius: 999px; background: rgba(255,255,255,0.02); }
    .hero-title { font-size: 2.8rem; font-weight: 800; margin: 0 0 0.5rem 0; letter-spacing: -0.03em; color: #fff; }
    .hero-sub { color: var(--soft); font-size: 1.05rem; line-height: 1.6; max-width: 50ch; margin-bottom: 1.5rem; }
    .tag-container { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .tag { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); padding: 6px 14px; border-radius: 999px; font-size: 0.8rem; color: var(--soft); }
    
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

    div[data-baseweb="tab-list"] { background-color: transparent; gap: 8px; }
    div[data-baseweb="tab"] { background-color: rgba(255,255,255,0.03); border: 1px solid rgba(120,145,170,0.15); border-radius: 8px; padding: 10px 16px; color: var(--muted); }
    div[data-baseweb="tab"][aria-selected="true"] { background-color: rgba(56,189,248,0.1); border-color: var(--accent); color: #fff; }
    div[data-baseweb="tab-highlight"] { display: none; }
    
    [data-testid="stPageLink-NavLink"] {
        background-color: rgba(16, 30, 46, 0.8); border: 1px solid rgba(56,194,201,0.3); border-radius: 12px; padding: 16px; transition: all 0.2s; margin-top: 8px;
        display: flex; justify-content: center; text-align: center;
    }
    [data-testid="stPageLink-NavLink"]:hover { background-color: rgba(56,194,201,0.15); border-color: rgba(56,194,201,0.8); transform: translateY(-2px); }
    [data-testid="stPageLink-NavLink"] * { color: #ffffff !important; font-weight: 650 !important; font-size: 1.05rem !important;}
</style>
""", unsafe_allow_html=True)

# --- 6. HEADER & DASHBOARD UI ---
logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0;">Builtly</h2>'

render_html(f"""
<div class="top-shell">
    <div>{logo_html}</div>
    <div class="topbar-right">
        <a href="/" target="_self" class="top-link ghost">← Tilbake til Portal</a>
    </div>
</div>

<div class="dash-grid">
    <div class="card card-hero">
        <div class="hero-kicker">✦ Builtly AI • Project SSOT</div>
        <h1 class="hero-title">Project Configuration</h1>
        <div class="hero-sub">Ett kontrollsenter for prosjektets kjerneparametere. Oppdater disse feltene én gang, og la Builtly synke kontekst til analyse, kalkyle og dokumentasjon.</div>
        <div class="tag-container">
            <div class="tag">AI-native proptech UX</div>
            <div class="tag">Enterprise-grade dataflyt</div>
            <div class="tag">Single Source of Truth</div>
        </div>
    </div>
    
    <div class="card">
        <div class="status-kicker">Sync Status</div>
        <div class="status-title">{sync_status}</div>
        <div class="status-desc">{filled_fields} av {len(fields_to_check)} nøkkelfelt er fylt ut og tilgjengelige for AI-modulene.</div>
        
        <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:0.5rem; color: #c8d3df;">
            <span>Kompletthet</span>
            <span style="font-weight:700;">{completeness}%</span>
        </div>
        <div class="prog-bar-bg">
            <div style="width: {completeness}%; height: 100%; background: {progress_color}; border-radius: 999px;"></div>
        </div>
        
        <div class="meta-row"><span class="meta-label">Sist oppdatert</span><span class="meta-value">{pd["last_sync"]}</span></div>
        <div class="meta-row"><span class="meta-label">Lokasjon satt</span><span class="meta-value">{"Ja" if pd["adresse"] or pd["gnr"] else "Nei"}</span></div>
    </div>
</div>

<div class="stat-grid">
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Datakompletthet</div>
        <div class="stat-value" style="color:{progress_color};">{completeness}%</div>
        <div class="stat-desc">{filled_fields} / {len(fields_to_check)} SSOT-felt er fylt ut.</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Primær Bruk</div>
        <div class="stat-value" style="font-size:1.4rem; padding-top:0.4rem;">{pd["b_type"]}</div>
        <div class="stat-desc">Styrer brannklasse og normer.</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Bruttoareal</div>
        <div class="stat-value">{pd["bta"]} m²</div>
        <div class="stat-desc">Grunnlag for analyse og estimering.</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Etasjer</div>
        <div class="stat-value">{pd["etasjer"]}</div>
        <div class="stat-desc">Ca. {int(pd["bta"]/max(1, pd["etasjer"]))} m² per etasje.</div>
    </div>
</div>
""")

# --- 7. INPUT SEKSJON & LIVE SNAPSHOT ---
st.markdown("<h3 style='margin-top: 1rem; margin-bottom: 0.2rem;'>Oppdater prosjektets kontrollsenter</h3>", unsafe_allow_html=True)
st.markdown("<p style='color:#9fb0c3; margin-bottom: 1.5rem;'>Naviger gjennom fanene under for å etablere prosjektdata. Feltene er nå designet for optimal lesbarhet.</p>", unsafe_allow_html=True)

input_col, snap_col = st.columns([2, 1], gap="large")

with input_col:
    tab1, tab2, tab3 = st.tabs(["📌 01 Generelt", "🌍 02 Lokasjon & API", "🏢 03 Byggdata"])
    
    with tab1:
        st.markdown("<br>", unsafe_allow_html=True)
        land_options = ["Norge (TEK17 / Kartverket)", "Sverige (BBR)", "Danmark (
