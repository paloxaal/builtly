import streamlit as st
import os
import base64
from pathlib import Path

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="QA & Sign-off | Builtly", page_icon="✅", layout="wide", initial_sidebar_state="collapsed")

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

# --- 2. PREMIUM CSS FOR QA-DASHBOARD ---
st.markdown("""
<style>
    :root {
        --bg: #06111a; --panel: rgba(10, 22, 35, 0.78);
        --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38bdf8; --radius-lg: 16px; --radius-xl: 24px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }
    
    /* TOP SHELL & LOGO */
    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    
    /* TILBAKE-KNAPP */
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

    /* STATISTIKK KORT */
    .stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.5rem; margin-bottom: 3rem; }
    .card { background: linear-gradient(180deg, rgba(16,30,46,0.8), rgba(10,18,28,0.8)); border: 1px solid var(--stroke); border-radius: var(--radius-lg); padding: 1.5rem; box-shadow: 0 12px 30px rgba(0,0,0,0.2); }
    .stat-title { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 0.8rem; }
    .stat-value { font-size: 2.2rem; font-weight: 750; color: #fff; margin-bottom: 0.3rem; line-height: 1; }

    /* --- MAGISK CSS FOR REVIEW-KORTENE --- */
    [data-testid="column"]:has(.review-card-hook) {
        background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98)) !important;
        border: 1px solid rgba(120, 145, 170, 0.18) !important;
        border-radius: 16px !important;
        padding: 1.8rem 2rem !important;
        margin-bottom: 1.2rem !important;
        box-shadow: 0 8px 24px rgba(0,0,0,0.15) !important;
        transition: all 0.2s ease !important;
    }
    [data-testid="column"]:has(.review-card-hook):hover {
        border-color: rgba(56,194,201,0.3) !important;
        box-shadow: 0 12px 32px rgba(0,0,0,0.25) !important;
        transform: translateY(-2px);
    }
    
    /* --- LØSNING FOR DEN STYGGE HVITE KNAPPEN --- */
    [data-testid="column"]:has(.review-card-hook) [data-testid="stButton"] {
        margin-top: 0.5rem !important;
    }
    
    /* Tvinger knappen til å være mørk */
    [data-testid="stButton"] button[kind="secondary"] {
        background-color: #0d1824 !important;
        border: 1px solid rgba(120,145,170,0.4) !important;
        border-radius: 8px !important;
        padding: 8px 24px !important;
        transition: all 0.2s ease !important;
    }
    
    /* Tvinger selve teksten inni knappen til å være hvit og tydelig */
    [data-testid="stButton"] button[kind="secondary"] * {
        color: #f5f7fb !important;
        font-weight: 650 !important;
        font-size: 0.95rem !important;
    }
    
    /* Hover-effekt (Cyan) */
    [data-testid="stButton"] button[kind="secondary"]:hover {
        background-color: rgba(56,194,201,0.1) !important;
        border-color: rgba(56,194,201,0.8) !important;
    }
    [data-testid="stButton"] button[kind="secondary"]:hover * {
        color: var(--accent) !important;
    }

    /* BADGES (STATUS) */
    .status-badge {
        display: inline-flex; align-items: center; gap: 6px;
        padding: 6px 14px; border-radius: 999px;
        font-size: 0.75rem; font-weight: 650; text-transform: uppercase; letter-spacing: 0.05em;
    }
    .badge-pending { background: rgba(244, 191, 79, 0.1); border: 1px solid rgba(244, 191, 79, 0.3); color: #f4bf4f; }
    .badge-approved { background: rgba(126, 224, 129, 0.1); border: 1px solid rgba(126, 224, 129, 0.3); color: #7ee081; }

</style>
""", unsafe_allow_html=True)

# --- 3. HEADER UI ---
logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'

render_html(f"""
<div class="top-shell">
    <div>{logo_html}</div>
    <div class="topbar-right">
        <a href="/" target="_self" class="top-link ghost">← Tilbake til Portal</a>
    </div>
</div>
""")

st.markdown("<h1 style='font-size: 2.8rem; margin-bottom: 0.2rem; letter-spacing: -0.02em;'>QA & Sign-off Dashboard</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: var(--muted); font-size: 1.1rem; margin-bottom: 2.5rem;'>Kvalitetssikring av AI-genererte dokumenter før endelig leveranse.</p>", unsafe_allow_html=True)

# --- 4. TOPP STATISTIKK ---
render_html("""
<div class="stat-grid">
    <div class="card">
        <div class="stat-title">Pending Sign-offs</div>
        <div class="stat-value">3</div>
    </div>
    <div class="card">
        <div class="stat-title">Signed Today</div>
        <div class="stat-value">0</div>
    </div>
    <div class="card">
        <div class="stat-title">AI Confidence Avg.</div>
        <div class="stat-value" style="color: var(--accent);">97.2%</div>
    </div>
</div>
""")

st.markdown("<h2 style='font-size: 1.6rem; margin-bottom: 1.5rem; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 0.5rem;'>Dokumenter til behandling</h2>", unsafe_allow_html=True)

# --- 5. KØ (REVIEW CARDS) ---
def review_card(title, doc_id, module, drafter, reviewer, status_text, status_class, btn_key):
    """En funksjon som tvinger Streamlit til å tegne et feilfritt mørkt kort med en innfødt knapp"""
    col, _ = st.columns([1, 0.001])
    with col:
        st.markdown('<div class="review-card-hook"></div>', unsafe_allow_html=True)
        st.markdown(f"""
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 1rem;">
            <div style="font-size: 1.35rem; font-weight: 750; color: #fff;">{title}</div>
            <div class="status-badge {status_class}">⏳ {status_text}</div>
        </div>
        <div style="color: #9fb0c3; font-size: 0.95rem; line-height: 1.6; margin-bottom: 0.5rem;">
            <strong>ID:</strong> {doc_id} &nbsp;&bull;&nbsp; <strong>Modul:</strong> {module} <br>
            <strong>Draftet:</strong> {drafter} &nbsp;&bull;&nbsp; <strong>Sjekket av:</strong> {reviewer}
        </div>
        """, unsafe_allow_html=True)
        
        if st.button("🔍 Åpne for kontroll", key=btn_key, type="secondary"):
            st.toast(f"Åpner dokument '{title}' for sign-off...", icon="⏳")

# Rendring av elementene i køen
review_card(
    "Saga Park Næringsbygg", 
    "PRJ-2026-A1", "RIG-M (Miljø)", "Builtly AI", "Ola Nordmann (Junior)", 
    "Pending Senior Review", "badge-pending", "btn_saga"
)

review_card(
    "Fjordveien Boligsameie", 
    "PRJ-2026-B4", "RIBr (Brann)", "Builtly AI", "Kari Nilsen (Junior)", 
    "Pending Senior Review", "badge-pending", "btn_fjord"
)

review_card(
    "Sentrumsterminalen", 
    "PRJ-2026-C2", "RIB (Konstruksjon)", "Builtly AI", "Ola Nordmann (Junior)", 
    "Pending Senior Review", "badge-pending", "btn_sentrum"
)
