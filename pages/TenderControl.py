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
import io
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
    expand_zip_to_documents,
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

# Nye moduler for tilbudsgrunnlag og tilbudsbesvarelse
from tender_company_profile import (
    get_profile as get_company_profile,
    save_profile as save_company_profile,
    profile_is_complete,
    EMPTY_PROFILE as EMPTY_COMPANY_PROFILE,
    COMMON_CERTIFICATIONS,
    COMMON_APPROVAL_AREAS,
)
from tender_packages import (
    generate_all_packages,
    package_module_selfcheck,
)
from tender_response import (
    generate_all_response_sections,
    build_response_zip,
    response_module_selfcheck,
    SECTION_PROMPTS as RESPONSE_SECTION_PROMPTS,
)
from tender_projects import (
    list_tender_projects,
    get_tender_project,
    create_tender_project,
    rename_tender_project,
    hard_delete_tender_project,
    set_active_tender,
    get_active_tender_id,
    get_active_tender_name,
    clear_active_tender,
    save_current_state as save_tender_state,
    load_into_session as load_tender_into_session,
)


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

/* Dyp override - alle wrapper-div-er rundt text/number/textarea */
.stTextInput > div, .stTextInput > div > div,
.stTextArea > div, .stTextArea > div > div,
.stNumberInput > div, .stNumberInput > div > div,
.stNumberInput [data-baseweb="input"],
.stNumberInput [data-baseweb="input"] > div,
.stTextInput [data-baseweb="input"],
.stTextInput [data-baseweb="input"] > div,
[data-baseweb="input"],
[data-baseweb="base-input"] {
    background-color: rgba(10,22,35,0.5) !important;
    background: rgba(10,22,35,0.5) !important;
    color: #f5f7fb !important;
}

/* Textarea trenger ekstra kjærlighet */
.stTextArea [data-baseweb="textarea"],
.stTextArea [data-baseweb="textarea"] > div,
textarea {
    background-color: rgba(10,22,35,0.5) !important;
    background: rgba(10,22,35,0.5) !important;
    color: #f5f7fb !important;
}

/* Placeholder-tekst */
input::placeholder, textarea::placeholder {
    color: rgba(159,176,195,0.5) !important;
}

.stTextInput label, .stTextArea label, .stNumberInput label,
.stSelectbox label, .stMultiSelect label, .stSelectSlider label,
.stFileUploader label, .stToggle label {
    color: #c8d3df !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
}

/* Multiselect piller + dropdown-innside */
div[data-baseweb="tag"],
.stMultiSelect div[data-baseweb="tag"] {
    background-color: rgba(56,189,248,0.18) !important;
    background: rgba(56,189,248,0.18) !important;
    border: 1px solid rgba(56,189,248,0.45) !important;
    color: #f5f7fb !important;
    border-radius: 6px !important;
}
div[data-baseweb="tag"] *,
.stMultiSelect div[data-baseweb="tag"] * {
    color: #f5f7fb !important;
    background-color: transparent !important;
}
div[data-baseweb="tag"] span[role="img"],
div[data-baseweb="tag"] [role="presentation"] {
    color: #f5f7fb !important;
}

/* Selectbox + Multiselect ytre boks (hele feltet) */
div[data-baseweb="select"] > div {
    background-color: rgba(10,22,35,0.5) !important;
    background: rgba(10,22,35,0.5) !important;
    border: 1px solid rgba(120,145,170,0.25) !important;
    color: #f5f7fb !important;
}
div[data-baseweb="select"] > div > div {
    background-color: transparent !important;
    color: #f5f7fb !important;
}
div[data-baseweb="select"] input {
    background-color: transparent !important;
    color: #f5f7fb !important;
}
div[data-baseweb="select"] svg {
    fill: #c8d3df !important;
    color: #c8d3df !important;
}

/* Dropdown-lista (popover når du åpner) - aggressive regler */
/* BaseWeb bruker portal-rendering, så vi treffer både i og utenfor app-rot */
div[data-baseweb="popover"],
div[data-baseweb="popover"] > div,
div[data-baseweb="popover"] > div > div,
ul[data-baseweb="menu"],
div[data-baseweb="menu"],
div[data-baseweb="menu"] > div,
[role="listbox"],
div[role="listbox"],
ul[role="listbox"] {
    background-color: #0a1623 !important;
    background: #0a1623 !important;
    border: 1px solid rgba(120,145,170,0.35) !important;
    box-shadow: 0 10px 30px rgba(0,0,0,0.4) !important;
    border-radius: 8px !important;
}

/* Alle option-elementer - dyp spesifisitet mot BaseWeb list items */
[data-baseweb="popover"] [role="option"],
[data-baseweb="menu"] [role="option"],
[data-baseweb="menu"] li,
[role="listbox"] [role="option"],
[role="listbox"] li,
li[role="option"],
div[role="option"] {
    background-color: transparent !important;
    background: transparent !important;
    color: #f5f7fb !important;
}

/* Tekst-innside options (span, div, p) */
[role="option"] *,
[data-baseweb="menu"] li *,
[role="listbox"] li * {
    color: #f5f7fb !important;
    background-color: transparent !important;
}

/* Hover-state */
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="menu"] [role="option"]:hover,
[data-baseweb="menu"] li:hover,
[role="listbox"] [role="option"]:hover,
[role="listbox"] li:hover,
li[role="option"]:hover,
div[role="option"]:hover,
[aria-selected="true"],
[role="option"][aria-selected="true"] {
    background-color: rgba(56,189,248,0.18) !important;
    background: rgba(56,189,248,0.18) !important;
    color: #f5f7fb !important;
}

[role="option"]:hover *,
[aria-selected="true"] * {
    color: #f5f7fb !important;
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
    background: rgba(10,22,35,0.5) !important;
    border: 1.5px dashed rgba(120,145,170,0.35) !important;
    border-radius: 12px !important;
}
[data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
[data-testid="stFileUploaderDropzone"] button,
[data-testid="stFileUploaderDropzoneInstructions"] button,
.stFileUploader button {
    background-color: rgba(56,189,248,0.12) !important;
    background: rgba(56,189,248,0.12) !important;
    color: #f5f7fb !important;
    border: 1px solid rgba(56,189,248,0.4) !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
}
[data-testid="stFileUploaderDropzone"] button:hover,
.stFileUploader button:hover {
    background-color: rgba(56,189,248,0.22) !important;
    border-color: #38bdf8 !important;
}
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
# 3b. AGGRESSIVE BASEWEB-OVERRIDES
# Streamlit bruker BaseWeb-komponenter som har egne lysstilte defaults.
# Disse overstyrer spesifisert -- må komme ETTER Streamlit's egen CSS.
# ═════════════════════════════════════════════════════════════════
st.markdown("""
<style>
/* ═══ MULTISELECT PILLER ═══ */
/* Treffer alle tag-elementer i hele appen */
.stApp div[data-baseweb="tag"],
.stApp [data-baseweb="tag"],
div[data-baseweb="tag"] {
    background: rgba(56,189,248,0.18) !important;
    background-color: rgba(56,189,248,0.18) !important;
    border: 1px solid rgba(56,189,248,0.45) !important;
    border-radius: 6px !important;
}
div[data-baseweb="tag"] span,
div[data-baseweb="tag"] div,
div[data-baseweb="tag"] path {
    color: #f5f7fb !important;
    fill: #f5f7fb !important;
    background: transparent !important;
    background-color: transparent !important;
}

/* ═══ SELECTBOX + MULTISELECT YTRE FELT ═══ */
.stApp div[data-baseweb="select"],
.stApp div[data-baseweb="select"] > div,
div[data-baseweb="select"] > div:first-child {
    background: rgba(10,22,35,0.6) !important;
    background-color: rgba(10,22,35,0.6) !important;
    border: 1px solid rgba(120,145,170,0.3) !important;
    color: #f5f7fb !important;
}
div[data-baseweb="select"] > div > div {
    background: transparent !important;
    background-color: transparent !important;
    color: #f5f7fb !important;
}
div[data-baseweb="select"] input {
    color: #f5f7fb !important;
    background: transparent !important;
    background-color: transparent !important;
}
div[data-baseweb="select"] svg,
div[data-baseweb="select"] path {
    fill: #c8d3df !important;
}

/* ═══ DROPDOWN-POPOVER (portal-rendered, utenfor app-rot!) ═══ */
/* BaseWeb renderer popovers direkte på <body> via React Portal.
   Vi bruker maximum-spesifisitet og dekker alle kjente BaseWeb-varianter. */

/* Alle portal-containere med hvit bakgrunn */
body > div[data-baseweb],
body > div[data-baseweb] *,
body > div[class*="Popover"],
body > div[class*="Popover"] *,
body > div[class*="Menu"],
body > div[class*="Menu"] * {
    color: #f5f7fb !important;
}

/* Forsøk å treffe ALT som ser ut som en dropdown-container på body-nivå */
body > div[role="tooltip"],
body > div[data-baseweb="popover"],
body > div[data-baseweb="popover"] *,
body > div[data-baseweb="tooltip"],
body > div[data-baseweb="layer"],
body > div[data-baseweb="layer"] *,
body div[data-baseweb="popover"] > div,
body div[data-baseweb="popover"] > div > div {
    background: #0a1623 !important;
    background-color: #0a1623 !important;
    color: #f5f7fb !important;
}

body div[data-baseweb="popover"],
body [data-baseweb="menu"],
body ul[data-baseweb="menu"],
body div[role="listbox"],
body ul[role="listbox"] {
    background: #0a1623 !important;
    background-color: #0a1623 !important;
    border: 1px solid rgba(120,145,170,0.35) !important;
    border-radius: 10px !important;
    box-shadow: 0 20px 50px rgba(0,0,0,0.5) !important;
    color: #f5f7fb !important;
}

/* Alle children inne i popover — tvinger alt til mørkt tema */
body div[data-baseweb="popover"] *,
body [data-baseweb="menu"] *,
body [role="listbox"] * {
    background: transparent !important;
    background-color: transparent !important;
    color: #f5f7fb !important;
}

/* Spesifikk regel for li-elementer (options) */
body div[data-baseweb="popover"] li,
body div[data-baseweb="popover"] [role="option"],
body [data-baseweb="menu"] li,
body [data-baseweb="menu"] [role="option"],
body ul[role="listbox"] li,
body div[role="listbox"] [role="option"],
body [role="option"] {
    background: transparent !important;
    background-color: transparent !important;
    color: #f5f7fb !important;
    padding: 0.5rem 1rem !important;
}

/* Tekst-innhold i options */
body [role="option"] span,
body [role="option"] div,
body [role="option"] p,
body li[role="option"] * {
    color: #f5f7fb !important;
    background: transparent !important;
    background-color: transparent !important;
}

/* Hover + selected state */
body [role="option"]:hover,
body [role="option"][aria-selected="true"],
body li[aria-selected="true"],
body div[data-baseweb="popover"] li:hover,
body [data-baseweb="menu"] li:hover {
    background: rgba(56,189,248,0.2) !important;
    background-color: rgba(56,189,248,0.2) !important;
    color: #f5f7fb !important;
}

body [role="option"]:hover *,
body [role="option"][aria-selected="true"] *,
body li[aria-selected="true"] * {
    color: #f5f7fb !important;
}

/* Fallback: HVIS BaseWeb setter hvit bakgrunn via inline style eller
   class som vi ikke fanger opp, forsøk å overstyre alle white BG-regler
   på dropdown-elementer direkte */
body [data-baseweb] [style*="background: white"],
body [data-baseweb] [style*="background: rgb(255"],
body [data-baseweb] [style*="background-color: white"],
body [data-baseweb] [style*="background-color: rgb(255"] {
    background: #0a1623 !important;
    background-color: #0a1623 !important;
    color: #f5f7fb !important;
}

/* ═══ NUMBER INPUT (pluss/minus + felt) ═══ */
.stNumberInput > div > div,
.stNumberInput > div > div > input {
    background: rgba(10,22,35,0.6) !important;
    background-color: rgba(10,22,35,0.6) !important;
    color: #f5f7fb !important;
    border-color: rgba(120,145,170,0.3) !important;
}
.stNumberInput button,
.stNumberInput button[kind="header"],
.stNumberInput [data-testid="stNumberInputStepUp"],
.stNumberInput [data-testid="stNumberInputStepDown"] {
    background: rgba(10,22,35,0.8) !important;
    background-color: rgba(10,22,35,0.8) !important;
    color: #f5f7fb !important;
    border: 1px solid rgba(120,145,170,0.3) !important;
}
.stNumberInput button:hover,
.stNumberInput [data-testid="stNumberInputStepUp"]:hover,
.stNumberInput [data-testid="stNumberInputStepDown"]:hover {
    background: rgba(56,189,248,0.2) !important;
    background-color: rgba(56,189,248,0.2) !important;
}
.stNumberInput svg,
.stNumberInput button svg path {
    fill: #f5f7fb !important;
    color: #f5f7fb !important;
}

/* ═══ TEXT AREA ═══ */
.stTextArea textarea,
.stTextArea > div > div > textarea,
textarea[data-baseweb="textarea"] {
    background: rgba(10,22,35,0.6) !important;
    background-color: rgba(10,22,35,0.6) !important;
    color: #f5f7fb !important;
    border: 1px solid rgba(120,145,170,0.3) !important;
}

/* ═══ TEXT INPUT ═══ */
.stTextInput input,
.stTextInput > div > div > input,
input[data-baseweb="input"] {
    background: rgba(10,22,35,0.6) !important;
    background-color: rgba(10,22,35,0.6) !important;
    color: #f5f7fb !important;
    border: 1px solid rgba(120,145,170,0.3) !important;
}

/* ═══ FILE UPLOADER ═══ */
[data-testid="stFileUploaderDropzone"],
[data-testid="stFileUploader"] section {
    background: rgba(10,22,35,0.5) !important;
    background-color: rgba(10,22,35,0.5) !important;
    border: 1.5px dashed rgba(120,145,170,0.4) !important;
}
[data-testid="stFileUploaderDropzone"] *,
[data-testid="stFileUploader"] section * {
    color: #c8d3df !important;
}
[data-testid="stFileUploaderDropzone"] button,
[data-testid="stFileUploader"] button {
    background: rgba(10,22,35,0.8) !important;
    background-color: rgba(10,22,35,0.8) !important;
    color: #f5f7fb !important;
    border: 1px solid rgba(120,145,170,0.3) !important;
}

/* ═══ EXPANDER ═══ */
/* Streamlit-expander har hvit bakgrunn by default — tving mørk */
[data-testid="stExpander"],
.streamlit-expanderHeader,
details[data-testid="stExpander"] {
    background: rgba(10,22,35,0.5) !important;
    background-color: rgba(10,22,35,0.5) !important;
    border: 1px solid rgba(120,145,170,0.2) !important;
    border-radius: 10px !important;
}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] > details > summary,
details[data-testid="stExpander"] > summary {
    background: transparent !important;
    background-color: transparent !important;
    color: #f5f7fb !important;
    font-weight: 600 !important;
    padding: 0.8rem 1rem !important;
}
[data-testid="stExpander"] summary:hover {
    background: rgba(56,189,248,0.08) !important;
    background-color: rgba(56,189,248,0.08) !important;
}
[data-testid="stExpander"] summary *,
[data-testid="stExpander"] summary p,
[data-testid="stExpander"] summary span,
[data-testid="stExpander"] [data-testid="stMarkdownContainer"] p {
    color: #f5f7fb !important;
    background: transparent !important;
    background-color: transparent !important;
}
[data-testid="stExpander"] svg,
[data-testid="stExpander"] summary svg {
    fill: #c8d3df !important;
    color: #c8d3df !important;
}
[data-testid="stExpander"] > div:not(summary),
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
    background: transparent !important;
    background-color: transparent !important;
    color: #f5f7fb !important;
    padding: 0 1rem 1rem 1rem !important;
}

/* ═══ ALERT / INFO / WARNING / ERROR / SUCCESS BOXES ═══ */
[data-testid="stAlert"],
.stAlert {
    background-color: rgba(10,22,35,0.6) !important;
    border: 1px solid rgba(120,145,170,0.2) !important;
    border-radius: 8px !important;
}
[data-testid="stAlert"] * {
    color: #c8d3df !important;
}
/* Success (grønn-tint) */
[data-baseweb="notification"][kind="success"],
.stAlert[data-baseweb="notification"][kind="success"] {
    background-color: rgba(16,185,129,0.12) !important;
    border-color: rgba(16,185,129,0.35) !important;
}
/* Error (rød-tint) */
[data-baseweb="notification"][kind="negative"] {
    background-color: rgba(239,68,68,0.12) !important;
    border-color: rgba(239,68,68,0.35) !important;
}
/* Warning (oransj-tint) */
[data-baseweb="notification"][kind="warning"] {
    background-color: rgba(245,158,11,0.12) !important;
    border-color: rgba(245,158,11,0.35) !important;
}
/* Info (blå-tint) */
[data-baseweb="notification"][kind="info"] {
    background-color: rgba(56,189,248,0.1) !important;
    border-color: rgba(56,189,248,0.3) !important;
}

/* ═══ CODE BLOCK (st.code) ═══ */
.stCode,
[data-testid="stCode"],
pre {
    background-color: rgba(10,22,35,0.8) !important;
    border: 1px solid rgba(120,145,170,0.2) !important;
    color: #c8d3df !important;
}
</style>

<script>
// BaseWeb setter noen ganger farger via JavaScript runtime (theming-systemet).
// MutationObserver lytter etter DOM-endringer og tvinger mørk tema når popovers åpnes.
(function() {
    if (window._builtlyDarkModeApplied) return;
    window._builtlyDarkModeApplied = true;

    const darkBg = '#0a1623';
    const darkText = '#f5f7fb';

    function applyDarkToPopover(el) {
        if (!el || !el.style) return;
        // Portal-containers som har hvit bakgrunn
        const bg = el.style.backgroundColor || '';
        if (bg.includes('255') || bg === 'white' || bg === 'rgb(255, 255, 255)') {
            el.style.setProperty('background-color', darkBg, 'important');
            el.style.setProperty('color', darkText, 'important');
        }
        // Sjekk attributter som tyder på popover
        const baseweb = el.getAttribute && el.getAttribute('data-baseweb');
        const role = el.getAttribute && el.getAttribute('role');
        if (baseweb === 'popover' || baseweb === 'menu' || role === 'listbox' || role === 'tooltip') {
            el.style.setProperty('background-color', darkBg, 'important');
            el.style.setProperty('color', darkText, 'important');
            el.style.setProperty('border', '1px solid rgba(120,145,170,0.35)', 'important');
            el.style.setProperty('border-radius', '10px', 'important');
            // Også alle children
            el.querySelectorAll('*').forEach(child => {
                if (child.style) {
                    const cbg = child.style.backgroundColor || '';
                    if (cbg.includes('255') || cbg === 'white') {
                        child.style.setProperty('background-color', 'transparent', 'important');
                    }
                    child.style.setProperty('color', darkText, 'important');
                }
            });
        }
    }

    function scanBody() {
        document.body.querySelectorAll('[data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"], [role="tooltip"]').forEach(applyDarkToPopover);
    }

    // Initial scan
    scanBody();

    // Observer fanger nye popovers når de åpnes
    const observer = new MutationObserver((mutations) => {
        for (const m of mutations) {
            m.addedNodes.forEach(node => {
                if (node.nodeType === 1) {  // ELEMENT_NODE
                    applyDarkToPopover(node);
                    // Sjekk også children
                    if (node.querySelectorAll) {
                        node.querySelectorAll('[data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"], [role="tooltip"]').forEach(applyDarkToPopover);
                    }
                }
            });
        }
    });

    observer.observe(document.body, { childList: true, subtree: true });
})();
</script>
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
if "tender_project" not in st.session_state:
    st.session_state.tender_project = {}
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
if "tender_rfi_queue" not in st.session_state:
    st.session_state.tender_rfi_queue = []


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

# Note: TenderControl er en selvstendig anbudskontroll-modul og trenger
# IKKE et eksisterende Project-oppsett. Brukeren kan jobbe i scratch-modus
# eller velge/opprette et anbud i "Lagrede anbud"-velgeren under.
# Prosjektnavn hentes fra (prioritert): aktivt anbud > p_name > "Anbud".
if pd_state.get("p_name") in ["", "Nytt Prosjekt", None]:
    # Sett et nøytralt default hvis ikke satt — brukeren fyller inn i intake-form
    pd_state["p_name"] = pd_state.get("p_name") or "Anbud"


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
# 6b. PROSJEKT-VELGER (lagrede anbud)
# ═════════════════════════════════════════════════════════════════
_tp_user_email = (
    st.session_state.get("user_email")
    or st.session_state.get("current_user_email")
    or os.environ.get("BUILTLY_USER", "demo@builtly.ai")
)
_tp_projects = list_tender_projects(user_email=_tp_user_email, include_archived=False, limit=50)
_tp_active_id = get_active_tender_id()
_tp_active_name = get_active_tender_name()

render_html("""
<div class="section-header" style="margin-top: 2rem;">
    <span class="section-badge">Lagrede anbud</span>
    <h3>Velg eller opprett anbudsprosjekt</h3>
    <p>Lagre ditt arbeid for å kunne komme tilbake senere — eller jobb i scratch-modus (alt mistes ved refresh).</p>
</div>
""")

# Aktivt-prosjekt-kort
_active_status = ""
if _tp_active_id:
    _active_record = get_tender_project(_tp_active_id)
    if _active_record:
        _active_status = _active_record.get("status", "draft")

# Bygg HTML-deler separat for å unngå f-string-kompleksitet med render_html
_tp_bg = (
    "linear-gradient(135deg, rgba(56,189,248,0.12), rgba(56,189,248,0.04))"
    if _tp_active_id else "rgba(10,22,35,0.4)"
)
_tp_border = "rgba(56,189,248,0.3)" if _tp_active_id else "rgba(120,145,170,0.2)"
_tp_label_color = "#38bdf8" if _tp_active_id else "#9fb0c3"
_tp_display_name = _tp_active_name if _tp_active_id else "— Scratch-modus (ikke lagret)"
_tp_status_html = (
    f'<div style="font-size:0.8rem; color:#9fb0c3; margin-top:0.2rem;">Status: {_active_status}</div>'
    if _tp_active_id else ""
)

st.markdown(
    f'<div style="background: {_tp_bg}; border: 1px solid {_tp_border}; '
    f'border-radius: 12px; padding: 1.2rem 1.5rem; margin-bottom: 1.2rem;">'
    f'<div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.1em; '
    f'color:{_tp_label_color}; font-weight:600; margin-bottom: 0.3rem;">Aktivt anbud</div>'
    f'<div style="font-size:1.15rem; font-weight:700; color:#f5f7fb;">{_tp_display_name}</div>'
    f'{_tp_status_html}'
    f'</div>',
    unsafe_allow_html=True,
)

_tp_c1, _tp_c2, _tp_c3 = st.columns([3, 1, 1])

with _tp_c1:
    _tp_options = ["— Scratch-modus (ikke lagre) —"] + [
        f"{p['name']}  ·  {p.get('buyer_name') or '?'}  ·  {p.get('status', 'draft')}"
        for p in _tp_projects
    ]
    _tp_ids = [""] + [p["tender_id"] for p in _tp_projects]
    _tp_current_idx = 0
    if _tp_active_id and _tp_active_id in _tp_ids:
        _tp_current_idx = _tp_ids.index(_tp_active_id)

    _tp_selected_idx = st.selectbox(
        "Velg lagret anbud",
        options=list(range(len(_tp_options))),
        format_func=lambda i: _tp_options[i],
        index=_tp_current_idx,
        key="tender_project_selector",
        label_visibility="collapsed",
    )
    _tp_selected_id = _tp_ids[_tp_selected_idx]

with _tp_c2:
    if st.button("Åpne", key="tp_load_btn", use_container_width=True,
                 disabled=(not _tp_selected_id or _tp_selected_id == _tp_active_id)):
        ok, err = load_tender_into_session(_tp_selected_id)
        if ok:
            st.success(f"Anbud lastet: {get_active_tender_name()}")
            st.rerun()
        else:
            st.error(f"Kunne ikke laste: {err}")

    if not _tp_selected_id and _tp_active_id:
        if st.button("Gå til scratch", key="tp_clear_btn", use_container_width=True):
            clear_active_tender()
            # Rens session state
            st.session_state.tender_project = {}
            st.session_state.tender_documents = []
            st.session_state.tender_analysis = None
            st.session_state.tender_rule_findings = None
            st.session_state.tender_readiness = None
            st.session_state.tender_rfi_queue = []
            st.rerun()

with _tp_c3:
    _tp_show_admin = st.toggle("Admin", value=False, key="tp_show_admin", help="Opprett nytt / omdøp / slett")

# Admin-panel under raden — bruker expander for å unngå popover-styling-problemer
if _tp_show_admin:
    with st.container(border=True):
        st.markdown("**Opprett nytt tomt anbud**")
        _tp_nc1, _tp_nc2 = st.columns([3, 1])
        with _tp_nc1:
            _tp_new_name = st.text_input("Navn", value="", key="tp_new_name",
                                          placeholder="F.eks. 'Saga Park Q2 2026'",
                                          label_visibility="collapsed")
        with _tp_nc2:
            if st.button("Opprett", key="tp_create_btn", type="primary",
                         disabled=not _tp_new_name.strip(),
                         use_container_width=True):
                ok, new_id, err = create_tender_project(
                    user_email=_tp_user_email,
                    name=_tp_new_name.strip(),
                )
                if ok and new_id:
                    st.session_state.tender_project = {"name": _tp_new_name.strip()}
                    st.session_state.tender_documents = []
                    st.session_state.tender_analysis = None
                    st.session_state.tender_rule_findings = None
                    st.session_state.tender_readiness = None
                    st.session_state.tender_rfi_queue = []
                    set_active_tender(new_id, name=_tp_new_name.strip())
                    st.success("Opprettet")
                    st.rerun()
                else:
                    st.error(f"Feilet: {err}")

        if _tp_active_id:
            st.markdown("---")
            st.markdown(f"**Administrer aktivt anbud:** {_tp_active_name}")
            _tp_rc1, _tp_rc2 = st.columns([3, 1])
            with _tp_rc1:
                _tp_rename = st.text_input("Nytt navn", value=_tp_active_name,
                                            key="tp_rename_input",
                                            label_visibility="collapsed")
            with _tp_rc2:
                if st.button("Omdøp", key="tp_rename_btn",
                             disabled=(_tp_rename == _tp_active_name or not _tp_rename.strip()),
                             use_container_width=True):
                    ok, err = rename_tender_project(_tp_active_id, _tp_rename.strip())
                    if ok:
                        set_active_tender(_tp_active_id, name=_tp_rename.strip())
                        st.success("Omdøpt")
                        st.rerun()
                    else:
                        st.error(err)

            st.markdown("---")
            _tp_dc1, _tp_dc2 = st.columns([3, 1])
            with _tp_dc1:
                _tp_confirm = st.checkbox(
                    "Bekreft permanent sletting av dette anbudet (kan ikke angres)",
                    key="tp_delete_confirm",
                )
            with _tp_dc2:
                if st.button("Slett", key="tp_delete_btn",
                             disabled=not _tp_confirm,
                             use_container_width=True):
                    ok, err = hard_delete_tender_project(_tp_active_id)
                    if ok:
                        clear_active_tender()
                        st.session_state.tender_project = {}
                        st.session_state.tender_documents = []
                        st.session_state.tender_analysis = None
                        st.session_state.tender_rule_findings = None
                        st.session_state.tender_readiness = None
                        st.session_state.tender_rfi_queue = []
                        st.success("Slettet")
                        st.rerun()
                    else:
                        st.error(err)


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
    with st.expander("📥 Hent metadata fra Doffin-lenke (valgfritt)", expanded=False):
        st.caption(
            "Lim inn en lenke til en Doffin-kunngjøring, så henter vi metadata "
            "(tittel, frist, oppdragsgiver, CPV, beskrivelse) automatisk. "
            "Selve konkurransegrunnlaget ligger alltid i oppdragsgivers KGV-portal "
            "(Mercell, Visma TendSign, EU-Supply osv.) — vi viser deg lenken dit."
        )
        url_col1, url_col2 = st.columns([4, 1])
        with url_col1:
            portal_url = st.text_input(
                "URL til Doffin-kunngjøring",
                value="",
                key="tender_portal_url",
                placeholder="https://www.doffin.no/notices/2025-115155",
                label_visibility="collapsed",
            )
        with url_col2:
            fetch_clicked = st.button("Hent", use_container_width=True, key="tender_fetch_btn")

        if fetch_clicked and portal_url.strip():
            with st.spinner("Henter fra Doffin..."):
                fetch_result = fetch_from_url(portal_url.strip())
            st.session_state.tender_portal_fetch = fetch_result

        fetch_result = st.session_state.get("tender_portal_fetch")
        if fetch_result:
            if not fetch_result.get("ok"):
                st.error(f"Henting feilet: {fetch_result.get('error')}")
            elif fetch_result.get("api_degraded"):
                # API er nede — vis fallback med Doffin-lenke
                meta = fetch_result.get("metadata", {})
                doffin_url = meta.get("doffin_url", "")
                st.warning(
                    "Doffin sitt API er for øyeblikket ikke tilgjengelig. "
                    f"Du kan åpne kunngjøringen direkte på Doffin — der finner du tittel, "
                    f"frist, oppdragsgiver og lenken videre til konkurransegrunnlaget i KGV-portalen."
                )
                st.markdown(
                    f"""
                    <div style="
                        background: linear-gradient(135deg, rgba(56,189,248,0.12), rgba(56,189,248,0.04));
                        border: 1px solid rgba(56,189,248,0.35);
                        border-radius: 12px;
                        padding: 1.2rem;
                        margin: 0.5rem 0;
                    ">
                        <div style="font-size: 0.82rem; color: #38bdf8; font-weight: 600;
                                    letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 0.4rem;">
                            Doffin-kunngjøring
                        </div>
                        <div style="font-size: 1.05rem; color: #f5f7fb; font-weight: 600; margin-bottom: 0.3rem;">
                            Referanse: {meta.get('reference', '')}
                        </div>
                        <div style="font-size: 0.9rem; color: #c8d3df; margin-bottom: 1rem;">
                            Åpne kunngjøringen på Doffin for å se full metadata og lenke til konkurransegrunnlaget.
                        </div>
                        <a href="{doffin_url}" target="_blank" rel="noopener" style="
                            display: inline-block;
                            background: #38bdf8;
                            color: #06111a;
                            padding: 0.6rem 1.2rem;
                            border-radius: 8px;
                            font-weight: 700;
                            text-decoration: none;
                            font-size: 0.95rem;
                        ">Åpne på Doffin →</a>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if fetch_result.get("api_error_detail"):
                    with st.expander("Teknisk detalj"):
                        st.code(fetch_result["api_error_detail"])
            else:
                meta = fetch_result.get("metadata", {})
                kgv_url = fetch_result.get("kgv_url")
                kgv_provider = fetch_result.get("kgv_provider") or "Ekstern KGV"

                st.success("Metadata hentet fra Doffin")

                # Strukturert metadata-visning
                mcol1, mcol2 = st.columns(2)
                with mcol1:
                    if meta.get("title"):
                        st.markdown(f"**Tittel**  \n{meta['title']}")
                    if meta.get("buyer"):
                        buyer_line = meta["buyer"]
                        if meta.get("buyer_org_no"):
                            buyer_line += f"  \nOrg.nr: `{meta['buyer_org_no']}`"
                        st.markdown(f"**Oppdragsgiver**  \n{buyer_line}")
                    if meta.get("main_activity"):
                        st.markdown(f"**Hovedaktivitet**  \n{meta['main_activity']}")
                    if meta.get("location"):
                        st.markdown(f"**Utførelsessted**  \n{meta['location']}")

                with mcol2:
                    if meta.get("deadline"):
                        st.markdown(f"**Frist**  \n{meta['deadline']}")
                    if meta.get("publication_date"):
                        st.markdown(f"**Publisert**  \n{meta['publication_date']}")
                    if meta.get("estimated_value"):
                        st.markdown(
                            f"**Estimert verdi**  \n"
                            f"{meta['estimated_value']:,.0f} {meta.get('currency', 'NOK')}".replace(",", " ")
                        )
                    if meta.get("reference"):
                        st.markdown(f"**Referanse**  \n`{meta['reference']}`")
                    if meta.get("procedure_type"):
                        st.markdown(f"**Prosedyre**  \n{meta['procedure_type']}")

                if meta.get("cpv_codes"):
                    st.markdown(f"**CPV-koder:** {', '.join(meta['cpv_codes'][:8])}")

                if meta.get("description"):
                    with st.expander("Beskrivelse"):
                        st.write(meta["description"])

                # KGV-knapp — fremhevet som neste steg
                st.markdown("---")
                if kgv_url:
                    st.markdown(
                        f"""
                        <div style="
                            background: linear-gradient(135deg, rgba(56,189,248,0.12), rgba(56,189,248,0.04));
                            border: 1px solid rgba(56,189,248,0.35);
                            border-radius: 12px;
                            padding: 1.2rem;
                            margin: 0.5rem 0;
                        ">
                            <div style="font-size: 0.82rem; color: #38bdf8; font-weight: 600;
                                        letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 0.4rem;">
                                Neste steg
                            </div>
                            <div style="font-size: 1.05rem; color: #f5f7fb; font-weight: 600; margin-bottom: 0.3rem;">
                                Last ned konkurransegrunnlaget fra {kgv_provider}
                            </div>
                            <div style="font-size: 0.9rem; color: #c8d3df; margin-bottom: 1rem;">
                                Konkurransegrunnlaget ligger i {kgv_provider}-portalen. Åpne lenken,
                                last ned alle vedlegg (vanligvis som ZIP), og dra filene inn i
                                filopplasteren under.
                            </div>
                            <a href="{kgv_url}" target="_blank" rel="noopener" style="
                                display: inline-block;
                                background: #38bdf8;
                                color: #06111a;
                                padding: 0.6rem 1.2rem;
                                border-radius: 8px;
                                font-weight: 700;
                                text-decoration: none;
                                font-size: 0.95rem;
                            ">Åpne i {kgv_provider} →</a>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.info(
                        "Fant ikke direktelenke til konkurransegrunnlag i Doffin-dataen. "
                        "Søk opp kunngjøringen manuelt i KGV-portalen for å laste ned dokumenter."
                    )

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
            help=(
                "Maks 200 MB per fil. PDF, DOCX, XLSX og IFC leses med strukturert ekstraksjon. "
                "DWG/DXF gir blokklayer-oversikt. ZIP-filer pakkes ut automatisk — "
                "alle dokumenter inni behandles individuelt (opptil 150 filer per ZIP)."
            ),
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
    """
    Les bytes og ekstraher innhold fra hver opplastet fil.
    ZIP-filer pakkes automatisk ut — hver fil inni behandles som
    et separat dokument.
    """
    from pathlib import Path as _P
    import gc
    results: List[Dict[str, Any]] = []
    total = len(uploaded)
    progress = st.progress(0.0, text="Leser dokumenter...")

    for i, f in enumerate(uploaded, 1):
        ext = _P(f.name).suffix.lower()
        progress.progress((i - 1) / max(total, 1), text=f"Leser {f.name} ({i}/{total})...")
        try:
            data = f.read()

            if ext == ".zip":
                progress.progress(
                    (i - 1) / max(total, 1),
                    text=f"Pakker ut {f.name} ({len(data) / 1024 / 1024:.0f} MB)...",
                )

                # Progress-callback som oppdaterer UI mens hver fil i ZIP
                # prosesseres — gir bedre feedback og lar Streamlit heartbeat
                def _zip_progress(inner_i: int, inner_total: int, inner_name: str):
                    # Ikke overstig 1.0 uansett
                    pct = min(1.0, (i - 1 + inner_i / max(inner_total, 1)) / max(total, 1))
                    progress.progress(pct, text=f"{f.name}: {inner_i}/{inner_total} — {inner_name}")

                inner_docs = expand_zip_to_documents(
                    zip_filename=f.name,
                    zip_data=data,
                    max_files=100,
                    max_file_size_mb=50,
                    max_text_chars_per_doc=200_000,
                    progress_callback=_zip_progress,
                )
                results.extend(inner_docs)
                # Frigjør ZIP-bytes
                data = None
                gc.collect()
            else:
                parsed = extract_document(f.name, data)
                # Cap tekst også for direkte-opplastede filer (store PDF-er)
                if parsed.get("text") and len(parsed["text"]) > 200_000:
                    parsed["text"] = parsed["text"][:200_000] + "\n[... tekst kuttet av minne-grunner ...]"
                results.append(parsed)
                data = None
                gc.collect()

        except MemoryError:
            results.append({
                "filename": f.name,
                "category": "annet",
                "error": "Minnemangel — filen er for stor",
                "text": "", "text_excerpt": "", "size_kb": 0,
            })
            gc.collect()
        except Exception as e:
            results.append({
                "filename": f.name,
                "category": "annet",
                "error": f"{type(e).__name__}: {e}",
                "text": "", "text_excerpt": "", "size_kb": 0,
            })

    progress.progress(1.0, text="Ferdig lest")
    progress.empty()
    return results


if submitted:
    # Kombiner opplastede filer med portal-hentede filer
    portal_fetched = st.session_state.get("tender_portal_fetch") or {}
    portal_files = portal_fetched.get("files") or []  # List[Tuple[str, bytes]]

    total_input_count = (len(files) if files else 0) + len(portal_files)

    if total_input_count == 0:
        st.error(
            "Last opp minst ett dokument — eller hent fra Doffin — før kjøring.\n\n"
            f"Debug: files={type(files).__name__ if files is not None else 'None'}, "
            f"len={len(files) if files else 0}, "
            f"portal={len(portal_files)}"
        )
        # Vis råverdier for ytterligere debugging
        with st.expander("Teknisk debug"):
            st.write({"files_raw": files, "portal_files_count": len(portal_files)})
    else:
        # Step 1: Parse documents (både opplastede og portal-hentede)
        with st.status("Leser og analyserer dokumenter...", expanded=True) as status:
            st.write(f"Fant {total_input_count} fil(er) totalt — starter parsing...")
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
            status.update(label=f"✓ Leste {len(parsed_docs)} dokument(er)", state="complete")

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

        # ── Synkroniser tender_project fra intake + config ──
        # Dette gjør at UE-pakker og Tilbudsbesvarelse-tabs har tilgang
        # til alle nødvendige felt.
        st.session_state.tender_project = {
            "name": pd_state.get("p_name", ""),
            "buyer_name": pd_state.get("buyer_name") or pd_state.get("byggherre", ""),
            "deadline": pd_state.get("tilbudsfrist", "") or config.get("deadline", ""),
            "contract_form": pd_state.get("contract_form") or config.get("tender_type", "Totalentreprise"),
            "estimated_value_mnok": config.get("estimated_value_mnok") or pd_state.get("estimated_value_mnok"),
            "description": pd_state.get("description", ""),
            "packages": config.get("packages", []) or pd_state.get("packages", []),
            "disciplines": config.get("disciplines", []),
            "role": config.get("role", ""),
            "notes": notes if "notes" in dir() else "",
            # Lokasjons-felt fra Project SSOT
            "b_type": pd_state.get("b_type", ""),
            "adresse": pd_state.get("adresse", ""),
            "kommune": pd_state.get("kommune", ""),
            "etasjer": pd_state.get("etasjer", ""),
            "bta": pd_state.get("bta", ""),
        }

        # ── Auto-lagre til tender_projects hvis brukeren har et aktivt anbud ──
        _auto_active_id = get_active_tender_id()
        if _auto_active_id:
            _auto_ok, _auto_saved_id, _auto_err = save_tender_state(
                project=st.session_state.tender_project,
                documents=st.session_state.tender_documents,
                analysis=st.session_state.tender_analysis,
                readiness=st.session_state.tender_readiness,
                rule_findings=st.session_state.tender_rule_findings or [],
                rfi_queue=st.session_state.get("tender_rfi_queue") or [],
                tender_id=_auto_active_id,
            )
            if _auto_ok:
                st.caption(f"✓ Auto-lagret til anbud «{get_active_tender_name()}»")
            else:
                st.caption(f"⚠ Auto-lagring feilet: {_auto_err}")
        else:
            st.info(
                "ⓘ  Du er i scratch-modus — analysen er ikke lagret under et anbud. "
                "Opprett eller velg et anbud i prosjekt-velgeren øverst for å lagre og "
                "komme tilbake senere."
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
    "UE-pakker",
    "Tilbudsbesvarelse",
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

                # Lagre til brukerens rapport-dashboard (Supabase Storage)
                _tc_proj_name = (
                    get_active_tender_name()
                    or pd_state.get("p_name", "Uten prosjekt")
                )
                try:
                    from builtly_auth import save_report as _save_report
                    _save_report(
                        project_name=_tc_proj_name,
                        report_name=f"Anbudskontroll — {_tc_proj_name}",
                        module="Anbudskontroll",
                        pdf_bytes=pdf_bytes,
                        content_type="application/pdf",
                    )
                except ImportError:
                    pass  # Auth-modul ikke tilgjengelig
                except Exception:
                    pass  # Upload-feil skal ikke blokkere UI
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


# ── TAB 5: UE-pakker (tilbudsgrunnlag til UE-er) ─────────────────
with tabs[5]:
    docs = st.session_state.tender_documents
    analysis = st.session_state.tender_analysis
    project = st.session_state.tender_project

    if not analysis:
        st.info("Kjør anbudskontroll for å kunne generere UE-tilbudsgrunnlag.")
    else:
        # Hent selskapsprofil
        user_email = os.environ.get("BUILTLY_USER", "demo@builtly.ai")
        company_profile = get_company_profile(user_email)

        st.markdown("### Selskapsprofil")
        st.caption(
            "Profilen brukes som firmabrev-header og kontaktinfo i alle UE-tilbudsgrunnlag. "
            "Fylles ut én gang og gjenbrukes på tvers av prosjekter."
        )

        profile_ok = profile_is_complete(company_profile)
        with st.expander(
            f"{'✓ Profil komplett' if profile_ok else '⚠ Profil ikke fullført — må fylles ut før generering'}",
            expanded=not profile_ok,
        ):
            with st.form("company_profile_form"):
                c1, c2 = st.columns(2)
                with c1:
                    cp_name = st.text_input("Selskapsnavn *", value=company_profile.get("company_name", ""))
                    cp_org = st.text_input("Organisasjonsnummer *", value=company_profile.get("company_org_no", ""))
                    cp_addr = st.text_input("Adresse", value=company_profile.get("company_address", ""))
                    cp_postcode = st.text_input("Postnummer", value=company_profile.get("company_postcode", ""))
                    cp_city = st.text_input("Sted", value=company_profile.get("company_city", ""))
                    cp_ceo = st.text_input("Daglig leder", value=company_profile.get("ceo_name", ""))
                with c2:
                    cp_contact = st.text_input("Tilbudsansvarlig (kontaktperson) *", value=company_profile.get("contact_person", ""))
                    cp_title = st.text_input("Tittel", value=company_profile.get("contact_title", ""))
                    cp_email = st.text_input("Kontakt-epost *", value=company_profile.get("contact_email", ""))
                    cp_phone = st.text_input("Kontakttelefon", value=company_profile.get("contact_phone", ""))

                cp_desc = st.text_area(
                    "Selskapsbeskrivelse (2-4 setninger, brukes i tilbudsbesvarelse)",
                    value=company_profile.get("company_description", ""),
                    height=80,
                )

                cp_approvals = st.multiselect(
                    "Sentral godkjenning / godkjenningsområder",
                    options=COMMON_APPROVAL_AREAS,
                    default=company_profile.get("approval_areas", []) if isinstance(company_profile.get("approval_areas"), list) else [],
                )
                cp_certs = st.multiselect(
                    "Sertifiseringer",
                    options=COMMON_CERTIFICATIONS,
                    default=company_profile.get("certifications", []) if isinstance(company_profile.get("certifications"), list) else [],
                )

                cp_hms = st.text_area("HMS-politikk (stikkord)", value=company_profile.get("hms_policy", ""), height=60)
                cp_quality = st.text_area("Kvalitetspolitikk (stikkord)", value=company_profile.get("quality_policy", ""), height=60)

                # Referanseprosjekter
                st.markdown("**Referanseprosjekter**")
                existing_refs = company_profile.get("reference_projects") or []
                if not isinstance(existing_refs, list):
                    existing_refs = []
                refs_text = st.text_area(
                    "Én referanse per linje, format: 'Navn | År | Verdi MNOK | Rolle | Beskrivelse'",
                    value="\n".join(
                        f"{r.get('name', '')} | {r.get('year', '')} | {r.get('value_mnok', '')} | {r.get('role', '')} | {r.get('description', '')}"
                        for r in existing_refs if isinstance(r, dict)
                    ),
                    height=140,
                )

                save_btn = st.form_submit_button("Lagre profil", type="primary")
                if save_btn:
                    refs = []
                    for line in refs_text.split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        parts = [p.strip() for p in line.split("|")]
                        refs.append({
                            "name": parts[0] if len(parts) > 0 else "",
                            "year": parts[1] if len(parts) > 1 else "",
                            "value_mnok": parts[2] if len(parts) > 2 else "",
                            "role": parts[3] if len(parts) > 3 else "",
                            "description": parts[4] if len(parts) > 4 else "",
                        })
                    ok = save_company_profile(user_email, {
                        "company_name": cp_name,
                        "company_org_no": cp_org,
                        "company_address": cp_addr,
                        "company_postcode": cp_postcode,
                        "company_city": cp_city,
                        "ceo_name": cp_ceo,
                        "contact_person": cp_contact,
                        "contact_title": cp_title,
                        "contact_email": cp_email,
                        "contact_phone": cp_phone,
                        "company_description": cp_desc,
                        "approval_areas": cp_approvals,
                        "certifications": cp_certs,
                        "hms_policy": cp_hms,
                        "quality_policy": cp_quality,
                        "reference_projects": refs,
                    })
                    if ok:
                        st.success("Profil lagret.")
                        st.rerun()
                    else:
                        st.error("Lagring feilet. Sjekk Supabase-tilkobling.")

        # Generer UE-pakker
        st.markdown("---")
        st.markdown("### Generer tilbudsgrunnlag til underentreprenører")

        pkg_list = project.get("packages", [])
        if not pkg_list:
            st.warning("Ingen pakker definert i intake. Gå tilbake til intake-skjemaet og legg til pakker.")
        elif not profile_ok:
            st.warning("Fullfør selskapsprofilen over før du kan generere UE-grunnlag.")
        else:
            selected_pkgs = st.multiselect(
                "Velg hvilke pakker du vil generere tilbudsgrunnlag for",
                options=pkg_list,
                default=pkg_list,
            )

            col_deadline, col_response = st.columns(2)
            with col_deadline:
                ue_deadline = st.text_input(
                    "Ønsket oppstart / kontraktsdato (tekst)",
                    value=project.get("deadline", ""),
                    help="F.eks. 'Q3 2026' eller '15.08.2026'",
                )
            with col_response:
                ue_response_dl = st.text_input(
                    "Tilbudsfrist fra UE",
                    value="",
                    help="F.eks. '14.05.2026 kl. 12:00'",
                )

            st.caption(
                f"Estimert AI-kost: ~{len(selected_pkgs) * 0.5:.1f} kr total "
                f"({len(selected_pkgs)} pakker × ~0,5 kr/pakke med Sonnet 4.5)"
            )

            if st.button("Generer UE-tilbudsgrunnlag", type="primary", disabled=not selected_pkgs):
                # Hent pass-data
                pass1_data = analysis.get("pass1", {}).get("data") if isinstance(analysis.get("pass1"), dict) else []
                if isinstance(pass1_data, dict):
                    pass1_data = list(pass1_data.values()) if pass1_data else []
                if not isinstance(pass1_data, list):
                    pass1_data = docs  # Fallback til rå dokumenter
                pass2_data = analysis.get("pass2", {}).get("data") if isinstance(analysis.get("pass2"), dict) else {}
                if not isinstance(pass2_data, dict):
                    pass2_data = {}

                progress_ue = st.progress(0.0, text="Starter generering...")

                def _ue_progress(i, total, name):
                    progress_ue.progress(i / max(total, 1), text=f"Genererer {name} ({i}/{total})...")

                try:
                    results = generate_all_packages(
                        packages=selected_pkgs,
                        pass1_data=pass1_data,
                        pass2_data=pass2_data,
                        company_profile=company_profile,
                        project_name=project.get("name", ""),
                        tender_type=project.get("contract_form", "Totalentreprise"),
                        buyer_name=project.get("buyer_name", ""),
                        deadline=ue_deadline or None,
                        response_deadline=ue_response_dl or None,
                        progress_callback=_ue_progress,
                    )
                    progress_ue.empty()

                    # Totalkost
                    total_tokens_in = sum(r.get("extraction_metadata", {}).get("tokens_in", 0) for r in results)
                    total_tokens_out = sum(r.get("extraction_metadata", {}).get("tokens_out", 0) for r in results)
                    total_cost_usd = total_tokens_in / 1_000_000 * 3 + total_tokens_out / 1_000_000 * 15
                    total_cost_nok = total_cost_usd * 10.8

                    ok_count = sum(1 for r in results if r.get("bytes"))
                    st.success(
                        f"Generert {ok_count} av {len(results)} UE-tilbudsgrunnlag. "
                        f"AI-kost: ~{total_cost_nok:.2f} kr ({total_tokens_in} in + {total_tokens_out} out tokens)."
                    )

                    # Vis per-pakke-resultat + individuelle download-knapper
                    import zipfile as _zip
                    zip_buf = io.BytesIO()
                    with _zip.ZipFile(zip_buf, "w", _zip.ZIP_DEFLATED) as zf:
                        for r in results:
                            if r.get("bytes") and r.get("filename"):
                                zf.writestr(r["filename"], r["bytes"])

                    for r in results:
                        if r.get("bytes"):
                            cols = st.columns([4, 1])
                            cols[0].caption(f"✓  **{r['package_name']}**  →  `{r['filename']}`")
                            cols[1].download_button(
                                "↓ DOCX",
                                data=r["bytes"],
                                file_name=r["filename"],
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key=f"dl_pkg_{r['package_name']}",
                            )
                        else:
                            st.caption(f"✗  **{r['package_name']}**  — feil: {r.get('error', 'ukjent')}")

                    st.markdown("---")
                    _ue_zip_bytes = zip_buf.getvalue()
                    st.download_button(
                        label="↓ Last ned alle UE-pakker som ZIP",
                        data=_ue_zip_bytes,
                        file_name=f"UE-tilbudsgrunnlag_{project.get('name', 'anbud')[:30]}.zip",
                        mime="application/zip",
                        type="primary",
                    )

                    # Lagre til brukerens rapport-dashboard
                    try:
                        from builtly_auth import save_report as _save_report
                        _ue_proj = (
                            get_active_tender_name()
                            or project.get("name", "Uten prosjekt")
                        )
                        _save_report(
                            project_name=_ue_proj,
                            report_name=f"UE-tilbudsgrunnlag — {_ue_proj}",
                            module="Anbudskontroll",
                            pdf_bytes=_ue_zip_bytes,
                            content_type="application/zip",
                        )
                    except ImportError:
                        pass
                    except Exception:
                        pass

                except Exception as e:
                    progress_ue.empty()
                    st.error(f"Generering feilet: {type(e).__name__}: {e}")


# ── TAB 6: Tilbudsbesvarelse (entreprenør → byggherre) ───────────
with tabs[6]:
    docs = st.session_state.tender_documents
    analysis = st.session_state.tender_analysis
    project = st.session_state.tender_project

    if not analysis:
        st.info("Kjør anbudskontroll før du genererer tilbudsbesvarelse.")
    else:
        user_email = os.environ.get("BUILTLY_USER", "demo@builtly.ai")
        company_profile = get_company_profile(user_email)

        st.markdown("### AI-utkast til tilbudsbesvarelse")
        st.caption(
            "Genererer utkast til 7 separate Word-filer (én per seksjon) "
            "basert på konkurransegrunnlaget og selskapsprofilen. "
            "Claude Opus brukes for dypere tekstgenerering."
        )

        if not profile_is_complete(company_profile):
            st.warning(
                "Selskapsprofilen er ikke fullført. Gå til 'UE-pakker'-fanen og fyll ut "
                "profilen før du genererer tilbudsbesvarelse."
            )
        else:
            # Vis seksjoner som kan genereres
            st.markdown("#### Seksjoner som vil bli generert")
            sections_info = [
                (key, prompt["title"], prompt["filename"])
                for key, prompt in RESPONSE_SECTION_PROMPTS.items()
            ]
            for i, (key, title, fname) in enumerate(sections_info, 1):
                st.caption(f"{i:02d}.  **{title}**  →  `{fname}`")

            st.markdown("---")
            col_opus1, col_opus2 = st.columns(2)
            with col_opus1:
                use_opus = st.checkbox(
                    "Bruk Claude Opus (dypere tekst, dyrere)",
                    value=True,
                    help="Opus gir bedre tekst men bruker mer tokens. Skru av for å bruke Sonnet 4.5 (60% billigere).",
                )
            with col_opus2:
                cost_per_section = 5 if use_opus else 2
                st.caption(
                    f"Estimert AI-kost: ~{len(sections_info) * cost_per_section} kr total "
                    f"({len(sections_info)} seksjoner × ~{cost_per_section} kr/seksjon)"
                )

            if st.button("Generer tilbudsbesvarelse", type="primary"):
                pass1_data = analysis.get("pass1", {}).get("data") if isinstance(analysis.get("pass1"), dict) else []
                if isinstance(pass1_data, dict):
                    pass1_data = list(pass1_data.values()) if pass1_data else []
                if not isinstance(pass1_data, list):
                    pass1_data = docs
                pass2_data = analysis.get("pass2", {}).get("data") if isinstance(analysis.get("pass2"), dict) else {}
                if not isinstance(pass2_data, dict):
                    pass2_data = {}

                progress_resp = st.progress(0.0, text="Starter generering...")

                def _resp_progress(i, total, title):
                    progress_resp.progress(i / max(total, 1), text=f"Skriver {title} ({i}/{total})...")

                try:
                    results = generate_all_response_sections(
                        company_profile=company_profile,
                        project_name=project.get("name", ""),
                        buyer_name=project.get("buyer_name", ""),
                        tender_type=project.get("contract_form", "Totalentreprise"),
                        pass1_data=pass1_data,
                        pass2_data=pass2_data,
                        packages=project.get("packages", []),
                        deadline=project.get("deadline", ""),
                        use_opus=use_opus,
                        progress_callback=_resp_progress,
                    )
                    progress_resp.empty()

                    # Kost
                    total_in = sum(r.get("tokens_in", 0) for r in results)
                    total_out = sum(r.get("tokens_out", 0) for r in results)
                    if use_opus:
                        cost_usd = total_in / 1_000_000 * 15 + total_out / 1_000_000 * 75
                    else:
                        cost_usd = total_in / 1_000_000 * 3 + total_out / 1_000_000 * 15
                    cost_nok = cost_usd * 10.8

                    ok_count = sum(1 for r in results if r.get("bytes"))
                    st.success(
                        f"Generert {ok_count} av {len(results)} seksjoner. "
                        f"AI-kost: ~{cost_nok:.2f} kr ({total_in} in + {total_out} out tokens)."
                    )

                    # Individuelle download-knapper per seksjon
                    for r in results:
                        if r.get("bytes"):
                            cols = st.columns([4, 1])
                            cols[0].caption(f"✓  **{r['title']}**  →  `{r['filename']}`")
                            cols[1].download_button(
                                "↓ DOCX",
                                data=r["bytes"],
                                file_name=r["filename"],
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key=f"dl_resp_{r['section_key']}",
                            )
                        else:
                            st.caption(f"✗  **{r['title']}**  — feil: {r.get('error', 'ukjent')}")

                    # Samlet ZIP-nedlasting
                    zip_bytes = build_response_zip(results, project.get("name", ""))
                    st.markdown("---")
                    st.download_button(
                        label="↓ Last ned hele tilbudsbesvarelsen som ZIP",
                        data=zip_bytes,
                        file_name=f"Tilbudsbesvarelse_{project.get('name', 'anbud')[:30]}.zip",
                        mime="application/zip",
                        type="primary",
                    )

                    # Lagre til brukerens rapport-dashboard
                    try:
                        from builtly_auth import save_report as _save_report
                        _resp_proj = (
                            get_active_tender_name()
                            or project.get("name", "Uten prosjekt")
                        )
                        _save_report(
                            project_name=_resp_proj,
                            report_name=f"Tilbudsbesvarelse — {_resp_proj}",
                            module="Anbudskontroll",
                            pdf_bytes=zip_bytes,
                            content_type="application/zip",
                        )
                    except ImportError:
                        pass
                    except Exception:
                        pass

                    st.info(
                        "ⓘ  Dette er AI-genererte utkast. Tilbudsansvarlig må gjennomgå, "
                        "supplere og tilpasse hvert dokument før utsending. Alle steder hvor "
                        "det står `[fyll inn: ...]` må manuelt fullføres."
                    )

                except Exception as e:
                    progress_resp.empty()
                    st.error(f"Generering feilet: {type(e).__name__}: {e}")


# ── TAB 7: Dokumenter ────────────────────────────────────────────
with tabs[7]:
    docs = st.session_state.tender_documents
    if not docs:
        st.info("Ingen dokumenter lastet opp ennå.")
    else:
        st.markdown(f"### {len(docs)} dokument(er) analysert")

        manifest_rows = []
        for d in docs:
            # Bygg merknad-kolonne: OCR, DWG-konvertering, portal-kilde, ZIP-kilde
            flags = []
            if d.get("ocr_pages"):
                flags.append(f"OCR {d['ocr_pages']}s")
            if d.get("converted_from_dwg"):
                flags.append("DWG→DXF")
            if d.get("source") == "doffin":
                flags.append("Doffin")
            if d.get("zip_source"):
                flags.append(f"ZIP: {d['zip_source'][:25]}")
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


# ── TAB 8: Sjekkliste ────────────────────────────────────────────
with tabs[8]:
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


# ── TAB 9: Krysskontroll ─────────────────────────────────────────
with tabs[9]:
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


# ── TAB 10: Audit trail ──────────────────────────────────────────
with tabs[10]:
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
