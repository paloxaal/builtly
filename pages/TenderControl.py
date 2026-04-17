# -*- coding: utf-8 -*-
"""
Builtly | Anbudskontroll (Tender Control) — v9
─────────────────────────────────────────────────────────────────
Reell dokumentlesning • Claude primær • 3-pass analyse •
Faktabasert readiness • Prisingspakker med utsendingsflyt •
ReportLab-rapport • Persistent audit trail.

Design language: Konstruksjon (RIB) / Builtly premium.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

# Local modules
from tender_document_parser import (
    extract_document,
    quick_scan_deadlines,
    quick_scan_ns_contract,
    quick_scan_dagmulkt,
)
from tender_ai_engine import (
    run_full_analysis,
    HAS_CLAUDE, HAS_OPENAI, HAS_GEMINI, HAS_ANY_AI,
)
from tender_rules import (
    build_rule_findings,
    compute_readiness,
    persist_run,
    load_run_history,
)
from tender_pricing import (
    build_pricing_packages,
    build_rfq_docx,
    send_rfq_email,
    log_package_dispatch,
    load_dispatch_history,
)
from tender_quote_parser import (
    parse_and_persist_quote,
    consolidate_quotes_by_package,
    load_quotes,
)
from tender_portal_fetch import fetch_from_url
from tender_report import build_pdf_report, build_markdown_report


# ═════════════════════════════════════════════════════════════════
# 1. PAGE CONFIG + CONSTANTS
# ═════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Builtly | Anbudskontroll",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DB_DIR = Path("qa_database")
FILES_DIR = DB_DIR / "project_files"
SSOT_FILE = DB_DIR / "ssot.json"
RAPPORT_DIR = DB_DIR / "tender_reports"
RAPPORT_DIR.mkdir(parents=True, exist_ok=True)

ACCEPTED_EXTS = ["pdf", "ifc", "xlsx", "xls", "xlsm", "docx", "csv", "txt", "md", "zip", "dwg", "dxf"]


# ═════════════════════════════════════════════════════════════════
# 2. SMALL HELPERS
# ═════════════════════════════════════════════════════════════════
def render_html(html: str) -> None:
    st.markdown(html, unsafe_allow_html=True)


def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            with open(candidate, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/png;base64,{encoded}"
    return ""


def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{name}.py")
        if p.exists():
            return str(p)
    return ""


def nb_value(v: Any) -> str:
    if v is None or v == "":
        return "-"
    if isinstance(v, float):
        return f"{v:.0f}" if v.is_integer() else f"{v:.2f}".replace(".", ",")
    return str(v)


# ═════════════════════════════════════════════════════════════════
# 3. PREMIUM CSS
# ═════════════════════════════════════════════════════════════════
st.markdown("""
<style>
#MainMenu, footer, header { visibility: hidden; }

:root {
    --bg: #06111a;
    --panel: rgba(10, 22, 35, 0.78);
    --stroke: rgba(120, 145, 170, 0.18);
    --text: #f5f7fb;
    --muted: #9fb0c3;
    --soft: #c8d3df;
    --accent: #38bdf8;
    --accent-warm: #f59e0b;
    --success: #10b981;
    --danger: #ef4444;
    --warning: #f59e0b;
    --radius-lg: 16px;
    --radius-xl: 24px;
}
html, body, [class*="css"] {
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
}
.stApp { background-color: var(--bg) !important; color: var(--text); }
header[data-testid="stHeader"] { visibility: hidden; }

/* Buttons */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    background-color: rgba(56,189,248,0.1) !important;
    color: #38bdf8 !important;
    border: 1px solid rgba(56,189,248,0.3) !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    transition: all 0.2s !important;
}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {
    background-color: rgba(56,189,248,0.2) !important;
    border-color: #38bdf8 !important;
}
.stFormSubmitButton > button {
    background-color: #38bdf8 !important;
    color: #06111a !important;
    font-weight: 700 !important;
}

/* Inputs */
.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stNumberInput > div > div > input,
.stSelectbox > div > div > div,
.stMultiSelect > div > div > div {
    background-color: rgba(10,22,35,0.5) !important;
    color: #f5f7fb !important;
    border: 1px solid rgba(120,145,170,0.25) !important;
    border-radius: 8px !important;
}
.stTextInput label, .stTextArea label, .stNumberInput label,
.stSelectbox label, .stMultiSelect label, .stSelectSlider label,
.stFileUploader label, .stToggle label {
    color: #c8d3df !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
}

/* Multiselect piller + dropdown-innside */
.stMultiSelect [data-baseweb="tag"] {
    background-color: rgba(56,189,248,0.15) !important;
    border: 1px solid rgba(56,189,248,0.35) !important;
    color: #f5f7fb !important;
    border-radius: 6px !important;
}
.stMultiSelect [data-baseweb="tag"] span,
.stMultiSelect [data-baseweb="tag"] div {
    color: #f5f7fb !important;
}
.stMultiSelect [data-baseweb="tag"] [role="presentation"] {
    color: #f5f7fb !important;
}
.stMultiSelect [data-baseweb="select"] > div,
.stSelectbox [data-baseweb="select"] > div {
    background-color: rgba(10,22,35,0.5) !important;
    color: #f5f7fb !important;
    border: 1px solid rgba(120,145,170,0.25) !important;
}
.stMultiSelect [data-baseweb="select"] input,
.stSelectbox [data-baseweb="select"] input {
    color: #f5f7fb !important;
}
.stSelectbox [data-baseweb="select"] > div > div:first-child {
    color: #f5f7fb !important;
}
/* Dropdown-lista (popover når du åpner) */
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="menu"] {
    background-color: #0a1623 !important;
    border: 1px solid rgba(120,145,170,0.3) !important;
}
[data-baseweb="popover"] [role="option"],
[data-baseweb="menu"] li {
    background-color: transparent !important;
    color: #f5f7fb !important;
}
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="menu"] li:hover {
    background-color: rgba(56,189,248,0.15) !important;
}

/* Number input pluss/minus-knapper */
.stNumberInput button {
    background-color: rgba(10,22,35,0.7) !important;
    color: #f5f7fb !important;
    border: 1px solid rgba(120,145,170,0.25) !important;
}
.stNumberInput button:hover {
    background-color: rgba(56,189,248,0.15) !important;
}

/* File uploader */
[data-testid="stFileUploaderDropzone"] {
    background-color: rgba(10,22,35,0.5) !important;
    border: 1.5px dashed rgba(120,145,170,0.35) !important;
    border-radius: 12px !important;
}
[data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
[data-testid="stFileUploaderFile"] {
    background-color: rgba(56,189,248,0.05) !important;
    border-radius: 8px !important;
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px;
    background-color: rgba(10, 22, 35, 0.5);
    border-radius: 12px;
    padding: 4px;
}
.stTabs [data-baseweb="tab"] {
    background-color: transparent !important;
    color: #9fb0c3 !important;
    border-radius: 8px !important;
    padding: 8px 16px !important;
    font-weight: 600 !important;
}
.stTabs [aria-selected="true"] {
    background-color: rgba(56,189,248,0.15) !important;
    color: #38bdf8 !important;
}
.stTabs [data-baseweb="tab-highlight"] { background-color: #38bdf8 !important; }

/* DataFrames */
.stDataFrame, [data-testid="stDataFrame"] {
    border: 1px solid rgba(120,145,170,0.2) !important;
    border-radius: 12px !important;
    overflow: hidden !important;
}

/* Form */
[data-testid="stForm"] {
    background-color: rgba(10, 22, 35, 0.5) !important;
    border: 1px solid rgba(120,145,170,0.2) !important;
    border-radius: 16px !important;
    padding: 1.5rem !important;
}

/* Markdown text */
.stMarkdown, .stMarkdown p, .stMarkdown li,
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 {
    color: #f5f7fb !important;
}

/* Hero */
.hero-card {
    background: linear-gradient(135deg, rgba(10,22,35,0.95), rgba(16,32,50,0.95));
    border: 1px solid rgba(120,145,170,0.15);
    border-radius: 20px;
    padding: 2.5rem 2.8rem 2rem;
    margin-bottom: 2rem;
}
.hero-eyebrow {
    font-size: 0.78rem; font-weight: 700; letter-spacing: 0.12em;
    text-transform: uppercase; color: #38bdf8; margin-bottom: 0.4rem;
}
.hero-title {
    font-size: 1.85rem; font-weight: 800; line-height: 1.2;
    color: #f5f7fb; margin-bottom: 0.75rem;
}
.hero-subtitle {
    font-size: 0.98rem; color: #9fb0c3; line-height: 1.6; max-width: 750px;
}
.hero-pills { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 1.1rem; }
.hero-pill {
    background: rgba(56,189,248,0.08);
    border: 1px solid rgba(56,189,248,0.25);
    border-radius: 20px; padding: 4px 14px;
    font-size: 0.8rem; font-weight: 600; color: #38bdf8;
}
.hero-badge {
    display: inline-block; background: rgba(245,158,11,0.12);
    border: 1px solid rgba(245,158,11,0.35); border-radius: 6px;
    padding: 2px 10px; font-size: 0.72rem; font-weight: 700;
    color: #f59e0b; text-transform: uppercase; letter-spacing: 0.08em;
    margin-top: 1rem;
}

/* Metric cards */
.metric-row { display: flex; gap: 12px; margin-bottom: 1rem; flex-wrap: wrap; }
.metric-card {
    flex: 1; min-width: 140px;
    background: rgba(10, 22, 35, 0.6);
    border: 1px solid rgba(120,145,170,0.18);
    border-radius: 14px; padding: 1.0rem 1.2rem;
}
.metric-card .mc-value {
    font-size: 1.7rem; font-weight: 800; color: #38bdf8; margin-bottom: 2px;
}
.metric-card .mc-label {
    font-size: 0.72rem; font-weight: 700; color: #c8d3df;
    text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 3px;
}
.metric-card .mc-desc { font-size: 0.75rem; color: #9fb0c3; line-height: 1.35; }
.metric-card.warn .mc-value { color: #f59e0b; }
.metric-card.danger .mc-value { color: #ef4444; }
.metric-card.success .mc-value { color: #10b981; }

/* Section headers */
.section-header { margin-top: 2rem; margin-bottom: 1rem; }
.section-header h3 {
    color: #f5f7fb !important; font-weight: 750 !important;
    font-size: 1.2rem !important; margin-bottom: 4px !important;
}
.section-header p {
    color: #9fb0c3 !important; font-size: 0.9rem !important;
}
.section-badge {
    display: inline-block; background: rgba(56,189,248,0.1);
    border: 1px solid rgba(56,189,248,0.25); border-radius: 6px;
    padding: 1px 8px; font-size: 0.7rem; font-weight: 700;
    color: #38bdf8; text-transform: uppercase; letter-spacing: 0.06em;
    margin-bottom: 4px;
}

/* Recommendation banner */
.reco-banner {
    background: linear-gradient(135deg, rgba(16,185,129,0.08), rgba(56,189,248,0.05));
    border: 1px solid rgba(16,185,129,0.3);
    border-radius: 14px; padding: 1.2rem 1.4rem; margin-bottom: 1rem;
}
.reco-banner.warn {
    background: linear-gradient(135deg, rgba(245,158,11,0.08), rgba(56,189,248,0.03));
    border-color: rgba(245,158,11,0.35);
}
.reco-banner.danger {
    background: linear-gradient(135deg, rgba(239,68,68,0.08), rgba(56,189,248,0.03));
    border-color: rgba(239,68,68,0.35);
}
.reco-label {
    font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; color: #10b981;
}
.reco-banner.warn .reco-label { color: #f59e0b; }
.reco-banner.danger .reco-label { color: #ef4444; }
.reco-value {
    font-size: 1.3rem; font-weight: 800; color: #f5f7fb; margin-top: 4px;
}
.reco-text {
    font-size: 0.9rem; color: #c8d3df; line-height: 1.5; margin-top: 8px;
}

/* Readiness bar */
.readiness-bar {
    background: rgba(10,22,35,0.6);
    border: 1px solid rgba(120,145,170,0.2);
    border-radius: 10px; padding: 1rem 1.2rem; margin-bottom: 1rem;
}
.rb-label {
    display: flex; justify-content: space-between;
    font-size: 0.85rem; color: #c8d3df; margin-bottom: 6px;
}
.rb-track {
    background: rgba(120,145,170,0.15); height: 8px; border-radius: 4px; overflow: hidden;
}
.rb-fill {
    height: 100%; background: linear-gradient(90deg, #38bdf8, #10b981);
    border-radius: 4px; transition: width 0.4s;
}

/* Snapshot card */
.snapshot-card {
    background: rgba(10, 22, 35, 0.6);
    border: 1px solid rgba(120,145,170,0.18);
    border-radius: 14px; padding: 1.2rem 1.4rem; margin-bottom: 1rem;
}
.sc-badge {
    display: inline-block; background: rgba(56,189,248,0.1);
    border: 1px solid rgba(56,189,248,0.2); border-radius: 5px;
    padding: 1px 8px; font-size: 0.68rem; font-weight: 700;
    color: #38bdf8; text-transform: uppercase; letter-spacing: 0.06em;
    margin-bottom: 6px;
}
.sc-name {
    font-size: 1.05rem; font-weight: 750; color: #f5f7fb; margin-bottom: 3px;
}
.sc-row { font-size: 0.82rem; color: #9fb0c3; margin-bottom: 1px; }

/* Backend status */
.backend-status {
    display: inline-flex; gap: 6px; align-items: center;
    padding: 4px 10px; border-radius: 6px;
    font-size: 0.72rem; font-weight: 600;
    background: rgba(16,185,129,0.1);
    border: 1px solid rgba(16,185,129,0.3);
    color: #10b981;
}
.backend-status.missing {
    background: rgba(239,68,68,0.1);
    border-color: rgba(239,68,68,0.3);
    color: #ef4444;
}
.backend-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: currentColor;
}

/* Disclaimer */
.disclaimer-banner {
    background: rgba(245, 158, 11, 0.06);
    border: 1px solid rgba(245,158,11,0.25);
    border-radius: 12px; padding: 0.9rem 1.3rem; margin-bottom: 1.5rem;
}
.db-title { font-size: 0.88rem; font-weight: 700; color: #f59e0b; }
.db-text { font-size: 0.82rem; color: #c8a94e; margin-top: 2px; }

/* Panel box */
.panel-box {
    background: rgba(10, 22, 35, 0.5);
    border: 1px solid rgba(120,145,170,0.15);
    border-radius: 16px;
    padding: 1.5rem 1.8rem;
    margin-top: 2rem; margin-bottom: 1.5rem;
}
.panel-box.gold {
    border-color: rgba(245,158,11,0.25);
    background: rgba(245,158,11,0.03);
}
.panel-box h4 { color: #f5f7fb !important; font-weight: 750 !important; }
.panel-box p, .panel-box li { color: #9fb0c3 !important; font-size: 0.9rem !important; }
</style>
""", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════
# 4. SESSION STATE / PROJECT DATA
# ═════════════════════════════════════════════════════════════════
DEFAULT_PROJECT = {
    "p_name": "", "c_name": "", "p_desc": "", "adresse": "",
    "kommune": "", "gnr": "", "bnr": "", "b_type": "Næring / Kontor",
    "etasjer": 1, "bta": 0, "land": "Norge",
}

if "project_data" not in st.session_state or not st.session_state.project_data.get("p_name"):
    if SSOT_FILE.exists():
        try:
            with open(SSOT_FILE, "r", encoding="utf-8") as f:
                st.session_state.project_data = json.load(f)
        except Exception:
            st.session_state.project_data = DEFAULT_PROJECT.copy()
    else:
        st.session_state.project_data = DEFAULT_PROJECT.copy()

pd_state = st.session_state.project_data

# Session-level state for tender analysis
if "tender_documents" not in st.session_state:
    st.session_state.tender_documents = []
if "tender_analysis" not in st.session_state:
    st.session_state.tender_analysis = None
if "tender_rule_findings" not in st.session_state:
    st.session_state.tender_rule_findings = None
if "tender_readiness" not in st.session_state:
    st.session_state.tender_readiness = None
if "tender_run_meta" not in st.session_state:
    st.session_state.tender_run_meta = None


# ═════════════════════════════════════════════════════════════════
# 5. HEADER
# ═════════════════════════════════════════════════════════════════
top_l, top_r = st.columns([4, 1])
with top_l:
    logo = logo_data_uri()
    if logo:
        render_html(f'<img src="{logo}" style="height: 48px;">')
    else:
        render_html('<h2 style="margin:0; color:white;">Builtly</h2>')

with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("\u2190 Tilbake til prosjekt", use_container_width=True):
        target = find_page("Project")
        if target:
            st.switch_page(target)
        else:
            st.warning("Fant ikke Project-siden.")

st.markdown(
    "<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 1.5rem;'>",
    unsafe_allow_html=True,
)

# Check project exists
if pd_state.get("p_name") in ["", "Nytt Prosjekt", None]:
    st.warning("Du må sette opp prosjektdata før du kan bruke denne modulen.")
    if find_page("Project"):
        if st.button("Gå til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()


# ═════════════════════════════════════════════════════════════════
# 6. HERO
# ═════════════════════════════════════════════════════════════════
if HAS_CLAUDE:
    backend_badge = '<span class="backend-status"><span class="backend-dot"></span>Claude primær</span>'
elif HAS_ANY_AI:
    backend_badge = '<span class="backend-status"><span class="backend-dot"></span>AI tilgjengelig</span>'
else:
    backend_badge = '<span class="backend-status missing"><span class="backend-dot"></span>Ingen AI-backend</span>'

render_html(f"""
<div class="hero-card">
    <div class="hero-eyebrow">Tender Control</div>
    <div class="hero-title">Anbudskontroll som finner hullene før markedet gjør det.</div>
    <div class="hero-subtitle">
        Reell dokumentlesning (PDF, DOCX, XLSX, IFC, DXF). 3-pass AI-analyse: per dokument,
        krysskontroll, og strategisk tilbudsvurdering. Genererer forespørselspakker for
        ekstern prising, og gir vektet readiness-score med full revisjonslogg.
    </div>
    <div class="hero-pills">
        <span class="hero-pill">Entreprenør</span>
        <span class="hero-pill">Rådgiver</span>
        <span class="hero-pill">Utbygger</span>
        <span class="hero-pill">RFQ-utsendelse</span>
        <span class="hero-pill">Audit trail</span>
    </div>
    <div style="margin-top: 0.8rem;">{backend_badge}</div>
</div>
""")


# ═════════════════════════════════════════════════════════════════
# 7. INTAKE FORM + SNAPSHOT
# ═════════════════════════════════════════════════════════════════
left, right = st.columns([1.25, 0.75], gap="large")

with left:
    render_html("""
    <div class="section-header">
        <span class="section-badge">Tender intake</span>
        <h3>Inntak og kontrollparametere</h3>
        <p>Definer anskaffelsen og last opp konkurransegrunnlag, tegninger og tilbudsdokumenter.</p>
    </div>
    """)

    # ── URL-inntak fra Doffin (utenfor form, så knappen fungerer uavhengig) ──
    with st.expander("📥 Hent fra Doffin-lenke (valgfritt)", expanded=False):
        st.caption(
            "Lim inn en lenke til en Doffin-kunngjøring, så henter vi metadata "
            "og tilgjengelige vedlegg automatisk. For Mercell og andre portaler "
            "må konkurransegrunnlaget lastes ned manuelt."
        )
        url_col1, url_col2 = st.columns([4, 1])
        with url_col1:
            portal_url = st.text_input(
                "URL til Doffin-kunngjøring",
                value="",
                key="tender_portal_url",
                placeholder="https://www.doffin.no/notices/...",
                label_visibility="collapsed",
            )
        with url_col2:
            fetch_clicked = st.button("Hent", use_container_width=True, key="tender_fetch_btn")

        if fetch_clicked and portal_url.strip():
            with st.spinner(f"Henter fra {portal_url}..."):
                fetch_result = fetch_from_url(portal_url.strip())
            st.session_state.tender_portal_fetch = fetch_result

        fetch_result = st.session_state.get("tender_portal_fetch")
        if fetch_result:
            if not fetch_result.get("ok"):
                st.error(f"Henting feilet: {fetch_result.get('error')}")
            else:
                meta = fetch_result.get("metadata", {})
                fetched_files = fetch_result.get("files", [])
                st.success(f"Hentet {len(fetched_files)} vedlegg fra {fetch_result.get('portal', 'portal')}")
                if meta.get("title"):
                    st.caption(f"**Tittel:** {meta['title']}")
                if meta.get("buyer"):
                    st.caption(f"**Oppdragsgiver:** {meta['buyer']}")
                if meta.get("deadline_raw"):
                    st.caption(f"**Frist:** {meta['deadline_raw']}")
                if meta.get("reference"):
                    st.caption(f"**Referanse:** {meta['reference']}")
                if fetched_files:
                    st.caption("Vedlegg som blir inkludert i analysen:")
                    for name, data in fetched_files[:15]:
                        st.caption(f"  • {name} ({len(data)/1024:.0f} KB)")
                    if len(fetched_files) > 15:
                        st.caption(f"  … og {len(fetched_files) - 15} til")
                if fetch_result.get("errors"):
                    with st.expander(f"Advarsler ({len(fetch_result['errors'])})"):
                        for err in fetch_result["errors"]:
                            st.caption(f"• {err}")

    with st.form("tender_control_form"):
        c1, c2 = st.columns(2)
        with c1:
            procurement_mode = st.selectbox(
                "Anskaffelsesform",
                ["Totalentreprise", "Utførelsesentreprise", "Samspillsentreprise", "Design & Build"],
                index=0,
            )
            discipline_focus = st.multiselect(
                "Fag / scope som skal kvalitetssikres først",
                ["ARK", "RIB", "RIV", "RIE", "Brann", "Akustikk", "Geo", "Trafikk", "SHA", "MOP", "BREEAM"],
                default=["ARK", "RIB", "Geo", "Brann"],
            )
            delivery_level = st.selectbox("Leveransenivå", ["auto", "reviewed", "attested"], index=1)

        with c2:
            packages = st.multiselect(
                "Pakker / delentrepriser",
                ["Grunnarbeid", "Betong", "Stål", "Fasade", "Tømrer", "Tak", "VVS", "Elektro", "Utomhus"],
                default=["Grunnarbeid", "Betong", "Fasade", "VVS", "Elektro"],
            )
            qa_level = st.select_slider(
                "Kontrolldybde",
                options=["Lett", "Standard", "Dyp", "Pre-bid review"],
                value="Dyp",
            )
            bid_value_mnok = st.number_input(
                "Estimert tilbudsverdi (MNOK)",
                min_value=1.0, value=120.0, step=1.0,
            )

        required_outputs = st.multiselect(
            "Ønskede leveranser",
            ["Avviksmatrise", "Mangelliste", "Uklarhetslogg", "Scopesammenstilling",
             "RFI-kø", "Prisingspakker (RFQ)", "Go/No-go-vurdering", "Submission readiness"],
            default=["Avviksmatrise", "Mangelliste", "Uklarhetslogg",
                     "RFI-kø", "Prisingspakker (RFQ)", "Go/No-go-vurdering"],
        )

        files = st.file_uploader(
            "Last opp konkurransegrunnlag, tegninger, IFC/PDF og tilbudsdokumenter",
            type=ACCEPTED_EXTS,
            accept_multiple_files=True,
            key="tender_files_v9",
            help="PDF, DOCX, XLSX og IFC leses med strukturert ekstraksjon. DWG/DXF gir blokklayer-oversikt.",
        )

        notes = st.text_area(
            "Prosjektspesifikke forhold som bør vektes høyt",
            value="Særskilt fokus på grensesnitt mellom grunnarbeid, betong og fasade. "
                  "Kontroller at rigg/logistikk, SHA og ytre miljø er konsistente i alle dokumenter.",
            height=90,
        )

        submitted = st.form_submit_button("Kjør anbudskontroll", use_container_width=True)


# ═════════════════════════════════════════════════════════════════
# 8. DISCLAIMER
# ═════════════════════════════════════════════════════════════════
disclaimer_text = {
    "auto": "Automatisk nivåvurdering — beslutningsstøtte for tilbudsteamet.",
    "reviewed": "Gjennomgått nivå — analyse klar for intern beslutning og go/no-go-vurdering.",
    "attested": "Attestert nivå — full gjennomgang med tilbudsansvarlig.",
}
render_html(f"""
<div class="disclaimer-banner">
    <div class="db-title">Leveransenivå: {delivery_level}</div>
    <div class="db-text">{disclaimer_text.get(delivery_level, '')}</div>
</div>
""")


# ═════════════════════════════════════════════════════════════════
# 9. RUN ANALYSIS
# ═════════════════════════════════════════════════════════════════
config = {
    "procurement_mode": procurement_mode,
    "discipline_focus": discipline_focus,
    "packages": packages,
    "qa_level": qa_level,
    "required_outputs": required_outputs,
    "bid_value_mnok": bid_value_mnok,
    "notes": notes,
    "delivery_level": delivery_level,
}


def _parse_uploaded_files(uploaded) -> List[Dict[str, Any]]:
    """Read bytes and extract content from each uploaded file."""
    results = []
    total = len(uploaded)
    progress = st.progress(0.0, text="Leser dokumenter...")
    for i, f in enumerate(uploaded, 1):
        progress.progress(i / max(total, 1), text=f"Leser {f.name} ({i}/{total})...")
        try:
            data = f.read()
            parsed = extract_document(f.name, data)
            results.append(parsed)
        except Exception as e:
            results.append({
                "filename": f.name,
                "category": "annet",
                "error": f"{type(e).__name__}: {e}",
                "text": "", "text_excerpt": "", "size_kb": 0,
            })
    progress.empty()
    return results


if submitted:
    # Kombiner opplastede filer med portal-hentede filer
    portal_fetched = st.session_state.get("tender_portal_fetch") or {}
    portal_files = portal_fetched.get("files") or []  # List[Tuple[str, bytes]]

    total_input_count = (len(files) if files else 0) + len(portal_files)

    if total_input_count == 0:
        st.error("Last opp minst ett dokument — eller hent fra Doffin — før kjøring.")
    else:
        # Step 1: Parse documents (både opplastede og portal-hentede)
        parsed_docs = _parse_uploaded_files(files) if files else []

        if portal_files:
            portal_progress = st.progress(0.0, text="Parser portal-vedlegg...")
            for i, (pname, pbytes) in enumerate(portal_files, 1):
                portal_progress.progress(i / len(portal_files), text=f"Parser {pname} ({i}/{len(portal_files)})...")
                try:
                    parsed = extract_document(pname, pbytes)
                    parsed["source"] = "doffin"
                    parsed_docs.append(parsed)
                except Exception as e:
                    parsed_docs.append({
                        "filename": pname,
                        "category": "annet",
                        "error": f"{type(e).__name__}: {e}",
                        "text": "", "text_excerpt": "", "size_kb": 0,
                        "source": "doffin",
                    })
            portal_progress.empty()

        st.session_state.tender_documents = parsed_docs
        st.success(f"Leste {len(parsed_docs)} dokument(er).")

        # Step 2: Rule-based findings (deterministic, instant)
        st.session_state.tender_rule_findings = build_rule_findings(
            st.session_state.tender_documents, config,
        )

        # Step 3: AI analysis (3-pass)
        if HAS_ANY_AI:
            ai_progress = st.progress(0.0, text="Starter AI-analyse...")

            def _cb(stage: str, pct: float, detail: str):
                ai_progress.progress(min(pct, 1.0), text=f"[{stage}] {detail}")

            with st.spinner("Kjører Claude 3-pass-analyse (per dokument → krysskontroll → strategi)..."):
                st.session_state.tender_analysis = run_full_analysis(
                    st.session_state.tender_documents, config, progress_callback=_cb,
                )
            ai_progress.empty()
        else:
            st.session_state.tender_analysis = {
                "pass1": [], "pass2": {"data": None}, "pass3": {"data": None},
                "attempt_log": [{"stage": "init", "status": "no_backend"}],
                "backend_summary": {"primary": "none"},
            }
            st.warning("Ingen AI-backend tilgjengelig. Sett ANTHROPIC_API_KEY.")

        # Step 4: Readiness score
        p2d = (st.session_state.tender_analysis.get("pass2") or {}).get("data")
        p3d = (st.session_state.tender_analysis.get("pass3") or {}).get("data")
        # Ny analyse = nullstill RFI-svar (gamle spørsmål er ikke lenger relevante)
        st.session_state.tender_rfi_state = {}
        st.session_state.tender_readiness = compute_readiness(
            st.session_state.tender_rule_findings, p2d, p3d,
            st.session_state.tender_documents, config,
            rfi_state=st.session_state.tender_rfi_state,
        )

        # Step 5: Persist audit trail
        user_id = os.environ.get("BUILTLY_USER", pd_state.get("user_email", "anonymous"))
        st.session_state.tender_run_meta = persist_run(
            project_name=pd_state.get("p_name", "-"),
            user_id=user_id,
            config=config,
            documents=st.session_state.tender_documents,
            rule_findings=st.session_state.tender_rule_findings,
            ai_result=st.session_state.tender_analysis,
            readiness=st.session_state.tender_readiness,
        )
        st.success(
            f"Analyse lagret (run_id: `{st.session_state.tender_run_meta['run_id'][:8]}`, "
            f"lagring: {st.session_state.tender_run_meta['stored_in']})"
        )


# ═════════════════════════════════════════════════════════════════
# 10. RIGHT PANEL — Project snapshot + live metrics
# ═════════════════════════════════════════════════════════════════
with right:
    render_html(f"""
    <div class="snapshot-card">
        <span class="sc-badge">Tender context</span>
        <div class="sc-name">{pd_state.get('p_name', 'Prosjekt')}</div>
        <div class="sc-row">Type: {pd_state.get('b_type', '-')}</div>
        <div class="sc-row">Sted: {pd_state.get('adresse', '-')}, {pd_state.get('kommune', '-')}</div>
        <div class="sc-row">Etasjer: {nb_value(pd_state.get('etasjer', '-'))} | BTA: {nb_value(pd_state.get('bta', '-'))} m²</div>
    </div>
    """)

    # Live metrics from state
    docs = st.session_state.tender_documents
    rf = st.session_state.tender_rule_findings or {}
    readiness = st.session_state.tender_readiness or {}
    analysis = st.session_state.tender_analysis or {}
    pass3_data = (analysis.get("pass3") or {}).get("data") or {}

    readiness_overall = readiness.get("overall", 0)
    readiness_band = readiness.get("band", "Ikke kjørt")
    readiness_css = (
        "success" if readiness_overall >= 70 else
        "warn" if readiness_overall >= 40 else "danger"
    ) if readiness else ""

    high_risks = sum(
        1 for r in (pass3_data.get("risk_matrix") or []) + rf.get("risk_items", [])
        if (r.get("severity") or "").upper() == "HIGH"
    )
    missing_count = len(rf.get("missing_categories", []))

    render_html(f"""
    <div class="metric-row">
        <div class="metric-card">
            <div class="mc-value">{len(docs)}</div>
            <div class="mc-label">Dokumenter</div>
            <div class="mc-desc">Analysert</div>
        </div>
        <div class="metric-card">
            <div class="mc-value">{missing_count}</div>
            <div class="mc-label">Mangler kat.</div>
            <div class="mc-desc">Forventede typer</div>
        </div>
    </div>
    <div class="metric-row">
        <div class="metric-card {'danger' if high_risks >= 3 else 'warn' if high_risks >= 1 else ''}">
            <div class="mc-value">{high_risks}</div>
            <div class="mc-label">Høy risiko</div>
            <div class="mc-desc">Bør lukkes</div>
        </div>
        <div class="metric-card {readiness_css}">
            <div class="mc-value">{readiness_overall:.0f}%</div>
            <div class="mc-label">Readiness</div>
            <div class="mc-desc">{readiness_band}</div>
        </div>
    </div>
    """)

    # Go/no-go banner (if analysis has run)
    if pass3_data.get("go_no_go"):
        go = pass3_data["go_no_go"]
        reco = go.get("recommendation", "?")
        banner_class = (
            "reco-banner" if reco == "GO" else
            "reco-banner warn" if reco in ("GO_WITH_CONDITIONS", "INSUFFICIENT_DATA") else
            "reco-banner danger"
        )
        render_html(f"""
        <div class="{banner_class}">
            <div class="reco-label">Anbefaling</div>
            <div class="reco-value">{reco} · konfidens {go.get('confidence', '-')}</div>
            <div class="reco-text">{go.get('rationale', '')[:280]}</div>
        </div>
        """)


# ═════════════════════════════════════════════════════════════════
# 11. RESULTS
# ═════════════════════════════════════════════════════════════════
render_html("""
<div class="section-header">
    <span class="section-badge">Review</span>
    <h3>Analyse og eksport</h3>
    <p>3-pass AI-analyse, regelfunn, prisingspakker og eksport — i samme arbeidsflate.</p>
</div>
""")

tab_names = [
    "Sammendrag",
    "Risikomatrise",
    "RFI-kø",
    "Prisingspakker",
    "Tilbudsrespons",
    "Dokumenter",
    "Sjekkliste",
    "Krysskontroll",
    "Audit trail",
]
tabs = st.tabs(tab_names)


# ── TAB 0: Sammendrag ────────────────────────────────────────────
with tabs[0]:
    docs = st.session_state.tender_documents
    analysis = st.session_state.tender_analysis
    readiness = st.session_state.tender_readiness
    rf = st.session_state.tender_rule_findings
    run_meta = st.session_state.tender_run_meta

    if not analysis:
        st.info("Kjør anbudskontroll for å se resultater.")
    else:
        pass3 = (analysis.get("pass3") or {}).get("data") or {}

        st.markdown("### Executive summary")
        summary = pass3.get("executive_summary")
        if summary:
            st.write(summary)
        else:
            st.info("Ingen AI-oppsummering. Resultatet baseres på regelmotor.")

        # Go/no-go
        go = pass3.get("go_no_go") or {}
        if go:
            st.markdown("### Go / No-go")
            c1, c2 = st.columns([1, 3])
            with c1:
                st.metric("Anbefaling", go.get("recommendation", "-"))
                st.caption(f"Konfidens: {go.get('confidence', '-')}")
            with c2:
                st.write(go.get("rationale", ""))
                if go.get("conditions"):
                    st.markdown("**Betingelser:**")
                    for c in go["conditions"]:
                        st.markdown(f"- {c}")

        # Readiness breakdown
        if readiness:
            st.markdown("### Readiness-sammensetning")
            st.markdown(
                f"**Total:** {readiness.get('overall', 0):.0f}% — _{readiness.get('band', '-')}_"
            )

            comp = readiness.get("components", {})
            weights = readiness.get("weights", {})
            labels = {
                "document_completeness": "Dokumentkomplettet",
                "scope_clarity": "Klart scope",
                "contract_risk": "Kontraktsrisiko",
                "pricing_readiness": "Prisingsklart",
                "qualification_fit": "Kvalifikasjoner",
            }
            for k, lbl in labels.items():
                val = comp.get(k, 0)
                w = weights.get(k, 0) * 100
                render_html(f"""
                <div class="readiness-bar">
                    <div class="rb-label"><span>{lbl} ({w:.0f}% vekt)</span><span>{val:.0f}%</span></div>
                    <div class="rb-track"><div class="rb-fill" style="width: {val}%"></div></div>
                </div>
                """)

        # Downloads
        st.markdown("### Eksport")
        col_md, col_pdf, col_json = st.columns(3)

        md_report = build_markdown_report(
            pd_state, config, docs, rf or {}, analysis, readiness or {}, run_meta,
        )
        with col_md:
            st.download_button(
                "Last ned rapport (.md)",
                data=md_report,
                file_name=f"anbudskontroll_{pd_state.get('p_name', 'prosjekt').replace(' ', '_')}.md",
                mime="text/markdown",
                use_container_width=True,
            )

        pdf_bytes = build_pdf_report(
            pd_state, config, docs, rf or {}, analysis, readiness or {}, run_meta,
        )
        with col_pdf:
            if pdf_bytes:
                st.download_button(
                    "Last ned rapport (.pdf)",
                    data=pdf_bytes,
                    file_name=f"anbudskontroll_{pd_state.get('p_name', 'prosjekt').replace(' ', '_')}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
                # Also persist to report directory
                try:
                    pdf_path = RAPPORT_DIR / f"{run_meta['run_id'] if run_meta else uuid.uuid4()}.pdf"
                    pdf_path.write_bytes(pdf_bytes)
                except Exception:
                    pass
            else:
                st.info("ReportLab ikke tilgjengelig")

        with col_json:
            ai_json = {
                "pass1": analysis.get("pass1", []),
                "pass2": (analysis.get("pass2") or {}).get("data"),
                "pass3": (analysis.get("pass3") or {}).get("data"),
                "readiness": readiness,
                "run_meta": run_meta,
            }
            st.download_button(
                "Last ned AI-resultat (.json)",
                data=json.dumps(ai_json, indent=2, ensure_ascii=False, default=str),
                file_name="tender_ai_result.json",
                mime="application/json",
                use_container_width=True,
            )


# ── TAB 1: Risikomatrise ─────────────────────────────────────────
with tabs[1]:
    analysis = st.session_state.tender_analysis
    rf = st.session_state.tender_rule_findings

    if not analysis:
        st.info("Kjør anbudskontroll for å se risikomatrise.")
    else:
        pass3 = (analysis.get("pass3") or {}).get("data") or {}
        pass2 = (analysis.get("pass2") or {}).get("data") or {}

        all_risks = list(pass3.get("risk_matrix") or [])
        all_risks.extend(rf.get("risk_items", []) if rf else [])
        for c in pass2.get("cross_document_conflicts") or []:
            all_risks.append({
                "title": f"Motstrid: {c.get('title', '')}",
                "severity": c.get("severity", "MEDIUM"),
                "category": "grensesnitt",
                "impact": (c.get("description", "") + " " + (c.get("economic_impact") or "")).strip(),
                "mitigation": c.get("recommended_rfi") or "Krever avklaring",
                "rfi_needed": True,
                "source": "Krysskontroll",
            })

        if all_risks:
            risk_df = pd.DataFrame(all_risks)
            # Order severity
            sev_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            risk_df["_sev"] = risk_df["severity"].map(lambda s: sev_order.get((s or "").upper(), 9))
            risk_df = risk_df.sort_values("_sev").drop(columns=["_sev"])

            display_cols = [c for c in ["title", "severity", "category", "impact", "mitigation", "source"]
                            if c in risk_df.columns]
            st.dataframe(risk_df[display_cols], use_container_width=True, hide_index=True)

            csv = risk_df[display_cols].to_csv(index=False).encode("utf-8")
            st.download_button(
                "Last ned risikomatrise (.csv)",
                data=csv, file_name="tender_risk_matrix.csv", mime="text/csv",
            )
        else:
            st.success("Ingen vesentlige risikoer identifisert.")


# ── TAB 2: RFI-kø ────────────────────────────────────────────────
with tabs[2]:
    analysis = st.session_state.tender_analysis
    if not analysis:
        st.info("Kjør anbudskontroll for å se RFI-kø.")
    else:
        pass3 = (analysis.get("pass3") or {}).get("data") or {}
        rfis = pass3.get("rfi_queue") or []

        # Init/keep RFI state dict so svar overlever rerender
        if "tender_rfi_state" not in st.session_state:
            st.session_state.tender_rfi_state = {}
        rfi_state = st.session_state.tender_rfi_state

        if rfis:
            # Seed state for new RFIs
            for idx, rfi in enumerate(rfis):
                key = f"rfi_{idx}"
                if key not in rfi_state:
                    rfi_state[key] = {
                        "status": "open",
                        "answer": "",
                        "answered_at": None,
                    }

            answered_count = sum(1 for v in rfi_state.values() if v.get("status") == "answered")
            total_count = len(rfis)

            col_m1, col_m2, col_m3 = st.columns(3)
            col_m1.metric("Totalt", total_count)
            col_m2.metric("Besvart", answered_count)
            col_m3.metric("Åpne", total_count - answered_count)

            st.markdown("")

            # Sorted by priority
            prio_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            sorted_rfis = sorted(
                enumerate(rfis),
                key=lambda t: prio_order.get((t[1].get("priority") or "").upper(), 9),
            )

            for idx, rfi in sorted_rfis:
                key = f"rfi_{idx}"
                state = rfi_state[key]
                is_answered = state.get("status") == "answered"

                priority = (rfi.get("priority") or "MEDIUM").upper()
                badge_color = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#10b981"}.get(priority, "#64748b")
                status_label = "✓ Besvart" if is_answered else "● Åpen"

                with st.expander(
                    f"{status_label}  ·  [{priority}]  {rfi.get('question', '(uten spørsmål)')[:120]}",
                    expanded=not is_answered and priority == "HIGH",
                ):
                    if rfi.get("why_it_matters"):
                        st.caption(f"**Hvorfor det betyr noe:** {rfi['why_it_matters']}")
                    if rfi.get("owner"):
                        st.caption(f"**Ansvar:** {rfi['owner']}")
                    if rfi.get("deadline_before"):
                        st.caption(f"**Må avklares før:** {rfi['deadline_before']}")

                    answer_val = st.text_area(
                        "Svar / status",
                        value=state.get("answer", ""),
                        key=f"rfi_answer_{idx}",
                        height=90,
                        placeholder="Skriv svaret fra byggherre eller egen intern avklaring her...",
                    )

                    bc1, bc2, bc3 = st.columns([1, 1, 3])
                    if not is_answered:
                        if bc1.button("Marker besvart", key=f"rfi_mark_{idx}", type="primary"):
                            rfi_state[key] = {
                                "status": "answered",
                                "answer": answer_val,
                                "answered_at": datetime.now().isoformat(),
                            }
                            # Recompute readiness with RFI boost
                            p2d = (analysis.get("pass2") or {}).get("data")
                            p3d = (analysis.get("pass3") or {}).get("data")
                            new_readiness = compute_readiness(
                                st.session_state.tender_rule_findings,
                                p2d, p3d,
                                st.session_state.tender_documents,
                                config,
                                rfi_state=rfi_state,
                            )
                            st.session_state.tender_readiness = new_readiness
                            st.rerun()
                    else:
                        if bc1.button("Gjenåpne", key=f"rfi_reopen_{idx}"):
                            rfi_state[key]["status"] = "open"
                            p2d = (analysis.get("pass2") or {}).get("data")
                            p3d = (analysis.get("pass3") or {}).get("data")
                            new_readiness = compute_readiness(
                                st.session_state.tender_rule_findings,
                                p2d, p3d,
                                st.session_state.tender_documents,
                                config,
                                rfi_state=rfi_state,
                            )
                            st.session_state.tender_readiness = new_readiness
                            st.rerun()
                        if state.get("answered_at"):
                            bc3.caption(f"Besvart: {state['answered_at'][:16].replace('T', ' ')}")

            st.markdown("---")
            # CSV-eksport med besvart-status
            export_rows = []
            for idx, rfi in enumerate(rfis):
                state = rfi_state.get(f"rfi_{idx}", {})
                export_rows.append({
                    **rfi,
                    "status": state.get("status", "open"),
                    "answer": state.get("answer", ""),
                    "answered_at": state.get("answered_at", ""),
                })
            rfi_df = pd.DataFrame(export_rows)
            csv = rfi_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Last ned RFI-kø (.csv)",
                data=csv, file_name="tender_rfi_queue.csv", mime="text/csv",
            )
        else:
            st.info("Ingen RFI-forslag generert. Dette kan skyldes at dokumentgrunnlaget "
                    "var for begrenset eller at AI ikke identifiserte uklarheter.")


# ── TAB 3: Prisingspakker ────────────────────────────────────────
with tabs[3]:
    analysis = st.session_state.tender_analysis
    if not analysis:
        st.info("Kjør anbudskontroll for å generere prisingspakker.")
    else:
        pass3_data = (analysis.get("pass3") or {}).get("data") or {}
        pass1_results = analysis.get("pass1", [])

        packages_out = build_pricing_packages(pass3_data, pass1_results, config)
        # Add stable ID per package
        for p in packages_out:
            if "package_id" not in p:
                p["package_id"] = f"RFQ-{uuid.uuid4().hex[:8].upper()}"

        if not packages_out:
            st.info("Ingen prisingspakker identifisert ennå.")
        else:
            st.markdown(f"**{len(packages_out)} pakke(r) klargjort.** "
                        "Velg pakke for å generere forespørsel og sende til underentreprenør.")

            pkg_summary_rows = [
                {
                    "Pakke": p.get("package", "").title(),
                    "Ekstern?": "Ja" if p.get("send_to_external") else "Nei",
                    "Anslått verdi (MNOK)": p.get("estimated_value_mnok") or "-",
                    "Scope-poster": len(p.get("scope_items", [])),
                    "Åpne spørsmål": len(p.get("open_questions", [])),
                    "Begrunnelse": (p.get("rationale") or "")[:120],
                }
                for p in packages_out
            ]
            st.dataframe(pd.DataFrame(pkg_summary_rows), use_container_width=True, hide_index=True)

            st.markdown("---")
            st.markdown("### Generer forespørselspakke (RFQ)")

            pkg_names = [
                f"{p.get('package', '').title()} — {p.get('package_id')}"
                for p in packages_out
            ]
            selected_idx = st.selectbox(
                "Velg pakke", options=range(len(packages_out)),
                format_func=lambda i: pkg_names[i],
            )
            selected_pkg = packages_out[selected_idx]

            with st.expander(f"Detaljer: {selected_pkg.get('package', '').title()}", expanded=True):
                st.markdown(f"**Begrunnelse:** {selected_pkg.get('rationale', '-')}")
                if selected_pkg.get("suggested_suppliers_hint"):
                    st.markdown(f"**Leverandørtype:** {selected_pkg['suggested_suppliers_hint']}")
                if selected_pkg.get("key_specifications"):
                    st.markdown("**Sentrale krav:**")
                    for k in selected_pkg["key_specifications"]:
                        st.markdown(f"- {k}")
                if selected_pkg.get("open_questions"):
                    st.markdown("**Åpne spørsmål UE bør svare på:**")
                    for q in selected_pkg["open_questions"]:
                        st.markdown(f"- {q}")
                if selected_pkg.get("scope_items"):
                    st.markdown("**Scope-poster (fra parsing):**")
                    st.dataframe(
                        pd.DataFrame(selected_pkg["scope_items"]).head(30),
                        use_container_width=True, hide_index=True,
                    )

            st.markdown("#### Mottaker")
            m1, m2, m3 = st.columns(3)
            with m1:
                rec_name = st.text_input("Kontaktperson", key=f"rec_name_{selected_pkg['package_id']}")
            with m2:
                rec_email = st.text_input("E-post", key=f"rec_email_{selected_pkg['package_id']}")
            with m3:
                rec_company = st.text_input("Selskap", key=f"rec_company_{selected_pkg['package_id']}")

            response_days = st.slider("Svarfrist (dager)", 3, 30, 10,
                                       key=f"resp_days_{selected_pkg['package_id']}")

            col_build, col_send = st.columns(2)
            with col_build:
                if st.button("Generer DOCX-forespørsel", use_container_width=True,
                             key=f"build_{selected_pkg['package_id']}"):
                    docx_bytes = build_rfq_docx(
                        selected_pkg, pd_state, response_deadline_days=response_days,
                        contact_name=rec_name, contact_email=rec_email,
                    )
                    if docx_bytes:
                        st.session_state[f"rfq_bytes_{selected_pkg['package_id']}"] = docx_bytes
                        st.success("DOCX generert.")
                    else:
                        st.error("python-docx ikke tilgjengelig.")

                docx_cached = st.session_state.get(f"rfq_bytes_{selected_pkg['package_id']}")
                if docx_cached:
                    st.download_button(
                        "Last ned RFQ (.docx)",
                        data=docx_cached,
                        file_name=f"RFQ_{selected_pkg.get('package')}_{selected_pkg['package_id']}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                        key=f"dl_{selected_pkg['package_id']}",
                    )

            with col_send:
                if st.button("Send via e-post (Resend)", use_container_width=True,
                             key=f"send_{selected_pkg['package_id']}"):
                    if not rec_email:
                        st.error("Mottaker-e-post mangler.")
                    else:
                        docx_bytes = st.session_state.get(f"rfq_bytes_{selected_pkg['package_id']}")
                        if not docx_bytes:
                            docx_bytes = build_rfq_docx(
                                selected_pkg, pd_state, response_deadline_days=response_days,
                                contact_name=rec_name, contact_email=rec_email,
                            )
                        body = (
                            f"Hei {rec_name or ''},\n\n"
                            f"I forbindelse med prosjektet {pd_state.get('p_name', '')} "
                            f"ønsker vi tilbud på pakken **{selected_pkg.get('package', '').title()}**.\n\n"
                            f"Detaljert forespørsel er vedlagt som DOCX. Svarfrist er "
                            f"{response_days} dager fra i dag.\n\n"
                            f"Mvh,\n{pd_state.get('c_name', 'Builtly-bruker')}"
                        )
                        send_result = send_rfq_email(
                            to_email=rec_email,
                            to_name=rec_name,
                            subject=f"Forespørsel om pris — {selected_pkg.get('package', '').title()} — {pd_state.get('p_name', '')}",
                            body_markdown=body,
                            docx_bytes=docx_bytes or b"",
                            docx_filename=f"RFQ_{selected_pkg.get('package')}_{selected_pkg['package_id']}.docx",
                        )
                        if send_result.get("status") == "sent":
                            log_package_dispatch(
                                pd_state.get("p_name", "-"),
                                selected_pkg,
                                {"name": rec_name, "email": rec_email, "company": rec_company},
                                send_result,
                                run_id=(st.session_state.tender_run_meta or {}).get("run_id", "-"),
                            )
                            st.success(f"Sendt til {rec_email}. Resend-ID: {send_result.get('resend_id')}")
                        elif send_result.get("status") == "skipped":
                            st.warning(send_result.get("reason", "Utsendelse hoppet over."))
                        else:
                            st.error(f"Feil ved utsendelse: {send_result}")

            # Dispatch history for this project
            st.markdown("---")
            st.markdown("### Utsendelseshistorikk")
            dispatches = load_dispatch_history(pd_state.get("p_name", "-"))
            if dispatches:
                disp_df = pd.DataFrame(dispatches)[[
                    c for c in ["sent_at", "package_name", "recipient_company", "recipient_email", "status"]
                    if c in pd.DataFrame(dispatches).columns
                ]]
                st.dataframe(disp_df, use_container_width=True, hide_index=True)
            else:
                st.caption("Ingen forespørsler er sendt ut for dette prosjektet ennå.")


# ── TAB 4: Tilbudsrespons (UE-parsing) ───────────────────────────
with tabs[4]:
    docs = st.session_state.tender_documents
    analysis = st.session_state.tender_analysis

    if not analysis:
        st.info(
            "Kjør anbudskontroll først. Når prisingspakker er generert og sendt ut, "
            "laster du opp mottatte UE-tilbud her for automatisk parsing og konsolidering."
        )
    else:
        pass3_data = (analysis.get("pass3") or {}).get("data") or {}
        pricing_packages_list = pass3_data.get("pricing_packages") or []
        package_names = [p.get("package_name") or p.get("name") or f"Pakke {i+1}"
                         for i, p in enumerate(pricing_packages_list)]
        if not package_names:
            package_names = ["Uspesifisert pakke"]

        project_name = pd_state.get("p_name", "-")
        run_id = (st.session_state.tender_run_meta or {}).get("run_id")

        st.markdown("### Last opp mottatte pristilbud fra underentreprenører")
        st.caption(
            "Hvert tilbud parses med AI: leverandørnavn, totalpris, gyldighet, "
            "forbehold, opsjoner og risikoflagg. Tilbudene aggregeres per pakke."
        )

        qcol1, qcol2 = st.columns([2, 1])
        with qcol1:
            quote_files = st.file_uploader(
                "Pristilbud (PDF, DOCX, XLSX)",
                type=["pdf", "docx", "xlsx", "xlsm", "xls"],
                accept_multiple_files=True,
                key="tender_quote_files",
            )
        with qcol2:
            selected_package = st.selectbox(
                "Hvilken pakke gjelder tilbudene?",
                package_names,
                key="tender_quote_package_sel",
            )

        if quote_files and st.button("Parse pristilbud", type="primary", key="tender_parse_quotes"):
            progress = st.progress(0.0, text="Starter parsing...")
            parsed_new: List[Dict[str, Any]] = []
            for i, qf in enumerate(quote_files, 1):
                progress.progress(i / len(quote_files), text=f"Parser {qf.name} ({i}/{len(quote_files)})...")
                try:
                    data = qf.read()
                    record = parse_and_persist_quote(
                        project_name=project_name,
                        run_id=run_id,
                        package_name=selected_package,
                        filename=qf.name,
                        file_bytes=data,
                        qa_level=config.get("qa_level", "Standard"),
                    )
                    parsed_new.append(record)
                except Exception as e:
                    parsed_new.append({
                        "filename": qf.name,
                        "package_name": selected_package,
                        "parsed": {"ok": False, "error": str(e)},
                    })
            progress.empty()
            st.success(f"Parset {len(parsed_new)} tilbud.")

        # Vis alle lagrede tilbud
        st.markdown("---")
        st.markdown("### Konsolidert tilbudsoversikt")

        stored_quotes = load_quotes(project_name)

        if not stored_quotes:
            st.info("Ingen pristilbud registrert for dette prosjektet ennå.")
        else:
            # Normaliser form for consolidate_quotes_by_package
            quotes_for_agg: List[Dict[str, Any]] = []
            for sq in stored_quotes:
                quotes_for_agg.append({
                    "package_name": sq.get("package_name"),
                    "filename": sq.get("filename"),
                    "parsed": {
                        "ok": sq.get("parsed_data") is not None,
                        "data": sq.get("parsed_data") or {},
                        "error": sq.get("parse_error"),
                    },
                    "received_at": sq.get("received_at"),
                    "quote_id": sq.get("quote_id"),
                })

            consolidated = consolidate_quotes_by_package(quotes_for_agg)

            # Oversiktskort per pakke
            for pkg in consolidated["packages"]:
                with st.expander(
                    f"📦 {pkg['package_name']}  ·  {pkg['num_priced']}/{pkg['num_quotes']} med pris",
                    expanded=pkg["num_priced"] > 0,
                ):
                    if pkg["num_priced"] >= 2:
                        mcol1, mcol2, mcol3, mcol4 = st.columns(4)
                        mcol1.metric("Laveste", f"{pkg['lowest']['price']:,.0f} kr".replace(",", " "))
                        mcol2.metric("Høyeste", f"{pkg['highest']['price']:,.0f} kr".replace(",", " "))
                        mcol3.metric("Snitt", f"{pkg['mean_price']:,.0f} kr".replace(",", " ") if pkg['mean_price'] else "–")
                        mcol4.metric("Spredning", f"{pkg['spread_pct']:.1f} %")

                        if pkg["lowest"]:
                            st.caption(f"**Laveste tilbyder:** {pkg['lowest']['supplier']}")
                    elif pkg["num_priced"] == 1 and pkg["lowest"]:
                        st.metric("Pris", f"{pkg['lowest']['price']:,.0f} kr".replace(",", " "))
                        st.caption(f"**Tilbyder:** {pkg['lowest']['supplier']}")

                    # Tabell over alle tilbud i pakken
                    rows = []
                    for q in pkg["quotes"]:
                        data = (q.get("parsed") or {}).get("data") or {}
                        err = (q.get("parsed") or {}).get("error")
                        rows.append({
                            "Fil": q.get("filename", "(uten navn)"),
                            "Leverandør": data.get("supplier_name") or "–",
                            "Pris (ex. mva)": (
                                f"{data['total_price_ex_vat']:,.0f}".replace(",", " ")
                                if isinstance(data.get("total_price_ex_vat"), (int, float))
                                else "–"
                            ),
                            "Gyldighet": data.get("validity_until") or "–",
                            "Forbehold": len(data.get("reservations") or []) if data else 0,
                            "Risiko": len(data.get("risk_flags") or []) if data else 0,
                            "Status": "OK" if not err else f"Feil: {err[:40]}",
                        })
                    if rows:
                        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                    # Detaljer per tilbud
                    for q in pkg["quotes"]:
                        data = (q.get("parsed") or {}).get("data")
                        if not data:
                            continue
                        st.markdown(f"**{data.get('supplier_name', q.get('filename'))}** — detaljer")
                        if data.get("reservations"):
                            st.caption("Forbehold:")
                            for r in data["reservations"][:10]:
                                st.caption(f"  • {r}")
                        if data.get("exclusions"):
                            st.caption("Eksklusjoner:")
                            for e in data["exclusions"][:10]:
                                st.caption(f"  • {e}")
                        if data.get("risk_flags"):
                            st.caption("Risikoflagg:")
                            for rf_item in data["risk_flags"][:10]:
                                sev = (rf_item.get("severity") or "").upper()
                                st.caption(f"  [{sev}] {rf_item.get('issue', '')} — {rf_item.get('impact', '')}")

            # Total-CSV-eksport
            st.markdown("---")
            all_rows = []
            for pkg in consolidated["packages"]:
                for q in pkg["quotes"]:
                    data = (q.get("parsed") or {}).get("data") or {}
                    all_rows.append({
                        "package": pkg["package_name"],
                        "filename": q.get("filename"),
                        "supplier": data.get("supplier_name"),
                        "supplier_org_no": data.get("supplier_org_no"),
                        "total_ex_vat": data.get("total_price_ex_vat"),
                        "total_inc_vat": data.get("total_price_inc_vat"),
                        "currency": data.get("currency"),
                        "validity_until": data.get("validity_until"),
                        "price_basis": data.get("price_basis"),
                        "reservations_count": len(data.get("reservations") or []),
                        "risk_count": len(data.get("risk_flags") or []),
                        "received_at": q.get("received_at"),
                    })
            if all_rows:
                csv = pd.DataFrame(all_rows).to_csv(index=False).encode("utf-8")
                st.download_button(
                    "Last ned konsolidert tilbudsmatrise (.csv)",
                    data=csv,
                    file_name=f"tilbudsmatrise_{project_name}.csv",
                    mime="text/csv",
                )


# ── TAB 5: Dokumenter ────────────────────────────────────────────
with tabs[5]:
    docs = st.session_state.tender_documents
    if not docs:
        st.info("Ingen dokumenter lastet opp ennå.")
    else:
        st.markdown(f"### {len(docs)} dokument(er) analysert")

        manifest_rows = []
        for d in docs:
            # Bygg merknad-kolonne: OCR, DWG-konvertering, portal-kilde
            flags = []
            if d.get("ocr_pages"):
                flags.append(f"OCR {d['ocr_pages']}s")
            if d.get("converted_from_dwg"):
                flags.append("DWG→DXF")
            if d.get("source") == "doffin":
                flags.append("Doffin")
            manifest_rows.append({
                "Filnavn": d.get("filename"),
                "Kategori": d.get("category"),
                "Størrelse (KB)": d.get("size_kb"),
                "Sider": d.get("page_count") or "-",
                "Tabeller": len(d.get("tables", [])),
                "Merknad": " · ".join(flags) if flags else "",
                "Feil": d.get("error") or "",
            })
        st.dataframe(pd.DataFrame(manifest_rows), use_container_width=True, hide_index=True)

        # Quick-scan highlights
        st.markdown("### Hurtigscan")
        combined_text = "\n".join((d.get("text") or "") for d in docs)
        deadlines = quick_scan_deadlines(combined_text)
        ns_contract = quick_scan_ns_contract(combined_text)
        dagmulkt = quick_scan_dagmulkt(combined_text)

        q1, q2, q3 = st.columns(3)
        with q1:
            st.markdown("**Frister funnet i tekst:**")
            if deadlines:
                for d in deadlines[:5]:
                    st.markdown(f"- `{d}`")
            else:
                st.caption("Ingen eksplisitte frister funnet automatisk.")
        with q2:
            st.markdown("**Kontraktsstandard:**")
            st.markdown(f"`{ns_contract}`" if ns_contract else "_Ikke identifisert_")
        with q3:
            st.markdown("**Dagmulkt-klausul:**")
            if dagmulkt:
                st.caption(dagmulkt[:180] + "...")
            else:
                st.caption("_Ikke funnet_")

        # Expandable per-doc detail
        with st.expander("Dokumentdetaljer"):
            for d in docs:
                st.markdown(f"**{d.get('filename')}** — {d.get('category')}")
                st.caption(f"Førstegjett (filnavn): `{d.get('category_filename')}` → "
                           f"bekreftet: `{d.get('category')}`")
                if d.get("error"):
                    st.error(d.get("error"))
                excerpt = (d.get("text_excerpt") or "")[:800]
                if excerpt:
                    st.code(excerpt, language=None)
                st.markdown("---")


# ── TAB 6: Sjekkliste ────────────────────────────────────────────
with tabs[6]:
    rf = st.session_state.tender_rule_findings
    if not rf:
        st.info("Kjør anbudskontroll for å generere sjekkliste.")
    else:
        checks = rf.get("checklist_items", [])
        if checks:
            check_df = pd.DataFrame(checks)
            # Order: MANGLER first, ADVARSEL next, OK last
            status_order = {"MANGLER": 0, "ADVARSEL": 1, "OK": 2}
            check_df["_o"] = check_df["status"].map(lambda s: status_order.get(s, 9))
            check_df = check_df.sort_values("_o").drop(columns=["_o"])
            st.dataframe(check_df, use_container_width=True, hide_index=True)

            if rf.get("missing_categories"):
                st.warning(f"Manglende kategorier: {', '.join(rf['missing_categories'])}")

            csv = check_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Last ned sjekkliste (.csv)",
                data=csv, file_name="tender_checklist.csv", mime="text/csv",
            )
        else:
            st.info("Ingen sjekkpunkter generert.")


# ── TAB 7: Krysskontroll ─────────────────────────────────────────
with tabs[7]:
    analysis = st.session_state.tender_analysis
    if not analysis:
        st.info("Kjør anbudskontroll for krysskontroll-resultater.")
    else:
        pass2 = (analysis.get("pass2") or {}).get("data") or {}

        st.markdown("### Motstrid mellom dokumenter")
        conflicts = pass2.get("cross_document_conflicts") or []
        if conflicts:
            conf_df = pd.DataFrame(conflicts)
            st.dataframe(conf_df, use_container_width=True, hide_index=True)
        else:
            st.success("Ingen motstrid identifisert.")

        st.markdown("### Scope-gap")
        gaps = pass2.get("scope_gaps") or []
        if gaps:
            gap_df = pd.DataFrame(gaps)
            st.dataframe(gap_df, use_container_width=True, hide_index=True)
        else:
            st.success("Ingen scope-gap identifisert.")

        st.markdown("### Samlet frist-oversikt")
        deadlines = pass2.get("unified_deadlines") or []
        if deadlines:
            dl_df = pd.DataFrame(deadlines)
            st.dataframe(dl_df, use_container_width=True, hide_index=True)
        else:
            st.caption("Ingen konsoliderte frister identifisert.")

        st.markdown("### Samlede kontraktsvilkår")
        terms = pass2.get("unified_contract_terms") or {}
        if terms:
            st.json(terms)
        else:
            st.caption("Ingen konsoliderte kontraktsvilkår identifisert.")


# ── TAB 8: Audit trail ───────────────────────────────────────────
with tabs[8]:
    history = load_run_history(pd_state.get("p_name", "-"))

    st.markdown("### Denne kjøringen")
    rm = st.session_state.tender_run_meta
    if rm:
        st.code(
            f"Run ID:         {rm.get('run_id')}\n"
            f"Tidsstempel:    {rm.get('timestamp')}\n"
            f"Dokument-hash:  {rm.get('document_hash')}\n"
            f"Lagret i:       {rm.get('stored_in')}",
            language=None,
        )
    else:
        st.caption("Ingen kjøring i denne sesjonen ennå.")

    st.markdown("### Historikk for dette prosjektet")
    if history:
        hist_df = pd.DataFrame(history)
        display_cols = [c for c in ["timestamp", "user_id", "document_count",
                                      "readiness_overall", "readiness_band", "run_id"]
                        if c in hist_df.columns]
        st.dataframe(hist_df[display_cols], use_container_width=True, hide_index=True)
    else:
        st.caption("Ingen tidligere kjøringer for dette prosjektet.")

    # AI backend status
    st.markdown("### AI-backend status")
    st.write({
        "Claude tilgjengelig": HAS_CLAUDE,
        "OpenAI tilgjengelig": HAS_OPENAI,
        "Gemini tilgjengelig": HAS_GEMINI,
    })

    # Pass-wise attempt log
    if st.session_state.tender_analysis:
        with st.expander("AI-forsøkslogg"):
            for entry in st.session_state.tender_analysis.get("attempt_log", []):
                st.write(entry)


# ═════════════════════════════════════════════════════════════════
# 12. FOOTER
# ═════════════════════════════════════════════════════════════════
render_html("""
<div class="panel-box" style="margin-top:2rem;text-align:center;opacity:0.7;font-size:0.85rem;">
    <p style="margin:0;">Builtly Anbudskontroll · AI-assistert analyse av konkurransegrunnlag ·
       Beslutningsstøtte for tilbudsteam.</p>
</div>
""")
