# -*- coding: utf-8 -*-
"""
Builtly | Anbudskontroll (Tender Control)
Self-contained Streamlit module – no external builtly_* dependencies.
Design language matches Konstruksjon (RIB) module.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None  # type: ignore[assignment, misc]

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None  # type: ignore[assignment, misc]

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# AI backends (optional)
try:
    import google.generativeai as genai
except Exception:
    genai = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


# ────────────────────────────────────────────────────────────────
# 1. PAGE CONFIG
# ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Builtly | Anbudskontroll",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DB_DIR = Path("qa_database")
FILES_DIR = DB_DIR / "project_files"
SSOT_FILE = DB_DIR / "ssot.json"


# ────────────────────────────────────────────────────────────────
# 2. HELPERS
# ────────────────────────────────────────────────────────────────
def render_html(html: str) -> None:
    st.markdown(html.replace("\n", " "), unsafe_allow_html=True)


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


def clean_text(text: Any) -> str:
    if text is None:
        return ""
    text = str(text)
    replacements = {
        "\u2013": "-", "\u2014": "-", "\u201c": '"', "\u201d": '"',
        "\u2018": "'", "\u2019": "'", "\u2026": "...", "\u2022": "-",
        "\u2264": "<=", "\u2265": ">=",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def safe_get(obj: Any, key: str, default: Any = None) -> Any:
    """Safely get a value from a dict-like object; returns default for non-dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def nb_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        txt = f"{value:.1f}" if abs(value) >= 10 else f"{value:.2f}".rstrip("0").rstrip(".")
        return txt.replace(".", ",")
    return str(value)


def list_to_dataframe(items: Any, columns: List[str]) -> pd.DataFrame:
    """Turn a list-of-dicts (or similar) into a tidy DataFrame."""
    if not items:
        return pd.DataFrame(columns=columns)
    if isinstance(items, list):
        rows = []
        for item in items:
            if isinstance(item, dict):
                rows.append({c: item.get(c, "") for c in columns})
            elif isinstance(item, (list, tuple)):
                row = {}
                for i, c in enumerate(columns):
                    row[c] = item[i] if i < len(item) else ""
                rows.append(row)
        return pd.DataFrame(rows, columns=columns)
    return pd.DataFrame(columns=columns)


def extract_json_blob(text: str) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    cleaned = re.sub(r"^```json", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"^```", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last != -1 and last > first:
        cleaned = cleaned[first: last + 1]
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return cleaned


def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    blob = extract_json_blob(text)
    if not blob:
        return None
    try:
        return json.loads(blob)
    except Exception:
        try:
            repaired = blob.replace("\t", " ").replace("\r", " ")
            repaired = re.sub(r"(?<!\\)'", '"', repaired)
            repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
            return json.loads(repaired)
        except Exception:
            return None


# ────────────────────────────────────────────────────────────────
# 3. PREMIUM CSS — same design language as Konstruksjon (RIB)
# ────────────────────────────────────────────────────────────────
st.markdown(
    """
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    :root {
        --bg: #06111a;
        --panel: rgba(10, 22, 35, 0.78);
        --stroke: rgba(120, 145, 170, 0.18);
        --text: #f5f7fb;
        --muted: #9fb0c3;
        --soft: #c8d3df;
        --accent: #38bdf8;
        --accent-warm: #f59e0b;
        --radius-lg: 16px;
        --radius-xl: 24px;
    }
    html, body, [class*="css"] {
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
    }
    .stApp {
        background-color: var(--bg) !important;
        color: var(--text);
    }
    header[data-testid="stHeader"] {
        visibility: hidden;
        height: 0;
    }
    .block-container {
        max-width: 1280px !important;
        padding-top: 1.5rem !important;
        padding-bottom: 4rem !important;
    }
    .brand-logo {
        height: 65px;
        filter: drop-shadow(0 0 18px rgba(120,220,225,0.08));
    }

    /* Buttons */
    button[kind="primary"],
    .stFormSubmitButton > button {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important;
        border: none !important;
        font-weight: 750 !important;
        border-radius: 12px !important;
        padding: 12px 24px !important;
        font-size: 1.05rem !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover,
    .stFormSubmitButton > button:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important;
    }
    button[kind="secondary"] {
        background-color: rgba(255,255,255,0.05) !important;
        color: #f8fafc !important;
        border: 1px solid rgba(120,145,170,0.3) !important;
        border-radius: 12px !important;
        font-weight: 650 !important;
        padding: 10px 24px !important;
        transition: all 0.2s !important;
    }
    button[kind="secondary"]:hover {
        background-color: rgba(56,194,201,0.1) !important;
        border-color: var(--accent) !important;
        color: var(--accent) !important;
        transform: translateY(-2px) !important;
    }

    /* Inputs – dark backgrounds with light text */
    div[data-baseweb="base-input"],
    div[data-baseweb="select"] > div,
    .stTextArea > div > div > div {
        background-color: #0d1824 !important;
        border: 1px solid rgba(120, 145, 170, 0.4) !important;
        border-radius: 8px !important;
    }
    .stTextInput input,
    .stNumberInput input,
    .stTextArea textarea,
    div[data-baseweb="select"] * {
        background-color: transparent !important;
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        border: none !important;
        box-shadow: none !important;
    }
    .stTextInput input:focus,
    .stNumberInput input:focus,
    .stTextArea textarea:focus {
        border: none !important;
    }
    div[data-baseweb="base-input"]:focus-within,
    div[data-baseweb="select"] > div:focus-within,
    .stTextArea > div > div > div:focus-within {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 1px rgba(56, 194, 201, 0.5) !important;
    }

    /* Dropdowns / menus */
    ul[data-baseweb="menu"] {
        background-color: #0d1824 !important;
        border: 1px solid rgba(120, 145, 170, 0.4) !important;
    }
    ul[data-baseweb="menu"] li {
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }
    ul[data-baseweb="menu"] li:hover {
        background-color: rgba(56, 194, 201, 0.1) !important;
    }

    /* Input hint text */
    div[data-testid="InputInstructions"],
    div[data-testid="InputInstructions"] > span {
        color: #9fb0c3 !important;
        -webkit-text-fill-color: #9fb0c3 !important;
    }

    /* Labels */
    .stTextInput label,
    .stSelectbox label,
    .stNumberInput label,
    .stTextArea label,
    .stFileUploader label,
    .stMultiSelect label,
    .stSlider label,
    .stCheckbox label,
    .stRadio label {
        color: #c8d3df !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        margin-bottom: 4px !important;
    }

    /* Slider – ensure value text is visible */
    .stSlider div[data-baseweb="slider"] div {
        color: #ffffff !important;
    }
    div[data-testid="stThumbValue"],
    .stSlider [data-testid="stTickBarMin"],
    .stSlider [data-testid="stTickBarMax"] {
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
    }

    /* Toggle / checkbox text */
    .stCheckbox span,
    .stToggle span,
    label[data-baseweb="checkbox"] span {
        color: #f5f7fb !important;
        -webkit-text-fill-color: #f5f7fb !important;
    }

    /* Multiselect tags */
    span[data-baseweb="tag"] {
        background-color: rgba(56, 194, 201, 0.15) !important;
        color: #38bdf8 !important;
        border: 1px solid rgba(56, 194, 201, 0.4) !important;
        border-radius: 6px !important;
    }
    span[data-baseweb="tag"] span {
        color: #38bdf8 !important;
        -webkit-text-fill-color: #38bdf8 !important;
    }

    /* Expanders */
    div[data-testid="stExpander"] details,
    div[data-testid="stExpander"] details summary,
    div[data-testid="stExpander"] {
        background-color: #0c1520 !important;
        color: #f5f7fb !important;
        border-radius: 12px !important;
    }
    div[data-testid="stExpander"] details summary:hover {
        background-color: rgba(255,255,255,0.03) !important;
    }
    div[data-testid="stExpander"] details summary p {
        color: #f5f7fb !important;
        font-weight: 650 !important;
    }
    div[data-testid="stExpander"] {
        border: 1px solid rgba(120,145,170,0.2) !important;
        margin-bottom: 1rem !important;
    }
    div[data-testid="stExpanderDetails"] {
        background: transparent !important;
        color: #f5f7fb !important;
    }

    /* File uploader */
    [data-testid="stFileUploaderDropzone"] {
        background-color: #0d1824 !important;
        border: 1px dashed rgba(120, 145, 170, 0.6) !important;
        border-radius: 12px !important;
        padding: 2rem !important;
    }
    [data-testid="stFileUploaderDropzone"]:hover {
        border-color: #38c2c9 !important;
        background-color: rgba(56, 194, 201, 0.05) !important;
    }
    [data-testid="stFileUploaderDropzone"] * {
        color: #c8d3df !important;
    }
    [data-testid="stFileUploaderFileData"] {
        background-color: rgba(255,255,255,0.02) !important;
        color: #f5f7fb !important;
        border-radius: 8px !important;
    }

    /* Alerts */
    [data-testid="stAlert"] {
        background-color: rgba(56, 194, 201, 0.05) !important;
        border: 1px solid rgba(56, 194, 201, 0.2) !important;
        border-radius: 12px !important;
    }
    [data-testid="stAlert"] * {
        color: #f5f7fb !important;
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
        border: none !important;
    }
    .stTabs [data-baseweb="tab"]:hover {
        background-color: rgba(56, 194, 201, 0.08) !important;
        color: #f5f7fb !important;
    }
    .stTabs [aria-selected="true"] {
        background-color: rgba(56, 194, 201, 0.15) !important;
        color: #38bdf8 !important;
    }
    .stTabs [data-baseweb="tab-highlight"] {
        background-color: #38bdf8 !important;
    }
    .stTabs [data-baseweb="tab-border"] {
        display: none !important;
    }

    /* DataFrames */
    .stDataFrame, [data-testid="stDataFrame"] {
        border: 1px solid rgba(120,145,170,0.2) !important;
        border-radius: 12px !important;
        overflow: hidden !important;
    }

    /* Metric cards */
    [data-testid="stMetric"],
    [data-testid="stMetricValue"],
    [data-testid="stMetricLabel"],
    [data-testid="stMetricDelta"] {
        color: #f5f7fb !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 2rem !important;
        font-weight: 800 !important;
    }

    /* Form */
    [data-testid="stForm"] {
        background-color: rgba(10, 22, 35, 0.5) !important;
        border: 1px solid rgba(120,145,170,0.2) !important;
        border-radius: 16px !important;
        padding: 1.5rem !important;
    }

    /* Select slider text */
    .stSelectSlider div[data-baseweb="slider"] div {
        color: #ffffff !important;
    }

    /* Download buttons */
    .stDownloadButton > button {
        background-color: rgba(255,255,255,0.04) !important;
        color: #c8d3df !important;
        border: 1px solid rgba(120,145,170,0.25) !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        transition: all 0.2s !important;
    }
    .stDownloadButton > button:hover {
        background-color: rgba(56,194,201,0.08) !important;
        border-color: #38bdf8 !important;
        color: #38bdf8 !important;
    }

    /* Number input +/- buttons */
    .stNumberInput button {
        color: #c8d3df !important;
        background-color: rgba(255,255,255,0.05) !important;
        border-color: rgba(120,145,170,0.3) !important;
    }

    /* Fix markdown text color globally */
    .stMarkdown, .stMarkdown p, .stMarkdown li,
    .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 {
        color: #f5f7fb !important;
    }
    .stMarkdown code {
        color: #38bdf8 !important;
        background-color: rgba(56,194,201,0.1) !important;
    }

    /* Caption text */
    .stCaption, small {
        color: #9fb0c3 !important;
    }

    /* Hero section */
    .hero-card {
        background: linear-gradient(135deg, rgba(10,22,35,0.95), rgba(16,32,50,0.95));
        border: 1px solid rgba(120,145,170,0.15);
        border-radius: 20px;
        padding: 2.5rem 2.8rem 2rem;
        margin-bottom: 2rem;
    }
    .hero-eyebrow {
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: #38bdf8;
        margin-bottom: 0.4rem;
    }
    .hero-title {
        font-size: 1.85rem;
        font-weight: 800;
        line-height: 1.2;
        color: #f5f7fb;
        margin-bottom: 0.75rem;
    }
    .hero-subtitle {
        font-size: 0.98rem;
        color: #9fb0c3;
        line-height: 1.6;
        max-width: 750px;
    }
    .hero-pills {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-top: 1.1rem;
    }
    .hero-pill {
        background: rgba(56, 194, 201, 0.08);
        border: 1px solid rgba(56, 194, 201, 0.25);
        border-radius: 20px;
        padding: 4px 14px;
        font-size: 0.8rem;
        font-weight: 600;
        color: #38bdf8;
    }
    .hero-badge {
        display: inline-block;
        background: rgba(245,158,11,0.12);
        border: 1px solid rgba(245,158,11,0.35);
        border-radius: 6px;
        padding: 2px 10px;
        font-size: 0.72rem;
        font-weight: 700;
        color: #f59e0b;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-top: 1rem;
    }

    /* Metric card custom */
    .metric-row {
        display: flex;
        gap: 16px;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        flex: 1;
        background: rgba(10, 22, 35, 0.6);
        border: 1px solid rgba(120,145,170,0.18);
        border-radius: 14px;
        padding: 1.1rem 1.3rem;
    }
    .metric-card .mc-value {
        font-size: 1.6rem;
        font-weight: 800;
        color: #38bdf8;
        margin-bottom: 2px;
    }
    .metric-card .mc-label {
        font-size: 0.82rem;
        font-weight: 700;
        color: #c8d3df;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 4px;
    }
    .metric-card .mc-desc {
        font-size: 0.78rem;
        color: #9fb0c3;
        line-height: 1.4;
    }

    /* Section title */
    .section-header {
        margin-top: 2.5rem;
        margin-bottom: 1rem;
    }
    .section-header h3 {
        color: #f5f7fb !important;
        font-weight: 750 !important;
        font-size: 1.2rem !important;
        margin-bottom: 4px !important;
    }
    .section-header p {
        color: #9fb0c3 !important;
        font-size: 0.9rem !important;
    }
    .section-badge {
        display: inline-block;
        background: rgba(56,194,201,0.1);
        border: 1px solid rgba(56,194,201,0.25);
        border-radius: 6px;
        padding: 1px 8px;
        font-size: 0.7rem;
        font-weight: 700;
        color: #38bdf8;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 4px;
    }

    /* Disclaimer banner */
    .disclaimer-banner {
        background: rgba(245, 158, 11, 0.06);
        border: 1px solid rgba(245,158,11,0.25);
        border-radius: 12px;
        padding: 0.9rem 1.3rem;
        margin-bottom: 1.5rem;
    }
    .disclaimer-banner .db-title {
        font-size: 0.88rem;
        font-weight: 700;
        color: #f59e0b;
    }
    .disclaimer-banner .db-text {
        font-size: 0.82rem;
        color: #c8a94e;
        margin-top: 2px;
    }

    /* Panel box */
    .panel-box {
        background: rgba(10, 22, 35, 0.5);
        border: 1px solid rgba(120,145,170,0.15);
        border-radius: 16px;
        padding: 1.5rem 1.8rem;
        margin-top: 2rem;
        margin-bottom: 1.5rem;
    }
    .panel-box.gold {
        border-color: rgba(245,158,11,0.25);
        background: rgba(245,158,11,0.03);
    }
    .panel-box h4 {
        color: #f5f7fb !important;
        font-weight: 750 !important;
        margin-bottom: 0.4rem !important;
    }
    .panel-box p, .panel-box li {
        color: #9fb0c3 !important;
        font-size: 0.9rem !important;
    }

    /* Snapshot card */
    .snapshot-card {
        background: rgba(10, 22, 35, 0.6);
        border: 1px solid rgba(120,145,170,0.18);
        border-radius: 14px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 1rem;
    }
    .snapshot-card .sc-badge {
        display: inline-block;
        background: rgba(56,194,201,0.1);
        border: 1px solid rgba(56,194,201,0.2);
        border-radius: 5px;
        padding: 1px 8px;
        font-size: 0.68rem;
        font-weight: 700;
        color: #38bdf8;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 6px;
    }
    .snapshot-card .sc-name {
        font-size: 1.05rem;
        font-weight: 750;
        color: #f5f7fb;
        margin-bottom: 3px;
    }
    .snapshot-card .sc-row {
        font-size: 0.82rem;
        color: #9fb0c3;
        margin-bottom: 1px;
    }
</style>
""",
    unsafe_allow_html=True,
)


# ────────────────────────────────────────────────────────────────
# 4. SESSION STATE / PROJECT DATA
# ────────────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────────────
# 5. AI BACKEND
# ────────────────────────────────────────────────────────────────
google_key = os.environ.get("GOOGLE_API_KEY", "").strip()
openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

HAS_GEMINI = bool(google_key and genai is not None)
HAS_OPENAI = bool(openai_key and OpenAI is not None)

if HAS_GEMINI:
    try:
        genai.configure(api_key=google_key)
    except Exception:
        HAS_GEMINI = False

HAS_AI = HAS_GEMINI or HAS_OPENAI
_OAI_CLIENT = None


def _get_openai():
    global _OAI_CLIENT
    if _OAI_CLIENT is None and HAS_OPENAI and OpenAI is not None:
        try:
            _OAI_CLIENT = OpenAI(api_key=openai_key)
        except Exception:
            pass
    return _OAI_CLIENT


def generate_text_ai(prompt: str, temperature: float = 0.15) -> str:
    """Call available AI backend with a text prompt; returns raw text."""
    if HAS_OPENAI:
        client = _get_openai()
        if client:
            try:
                model_name = clean_text(
                    os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini"
                ).strip() or "gpt-4.1-mini"
                resp = client.responses.create(
                    model=model_name,
                    input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                    temperature=temperature,
                )
                return clean_text(getattr(resp, "output_text", "")).strip()
            except Exception:
                pass

    if HAS_GEMINI and genai is not None:
        try:
            models = [m.name for m in genai.list_models() if "generateContent" in getattr(m, "supported_generation_methods", [])]
            model_name = models[0] if models else "models/gemini-1.5-flash"
            model = genai.GenerativeModel(model_name)
            resp = model.generate_content(prompt, generation_config={"temperature": temperature})
            return clean_text(getattr(resp, "text", "")).strip()
        except Exception:
            pass

    return ""


# ────────────────────────────────────────────────────────────────
# 6. DOCUMENT HANDLING
# ────────────────────────────────────────────────────────────────
TENDER_FILE_CATEGORIES = {
    "konkurransegrunnlag": ["konkurransegrunnlag", "contest", "tender doc", "utlysning"],
    "beskrivelse": ["beskrivelse", "specification", "spesifikasjon", "kravspec"],
    "tegning": ["tegning", "drawing", "plan", "snitt", "fasade", "dwg"],
    "kontrakt": ["kontrakt", "contract", "avtale", "agreement"],
    "tilbud": ["tilbud", "offer", "bid", "pris", "price"],
    "bok": ["bok", "book", "mengde", "quantity"],
    "sha": ["sha", "hms", "safety", "sikkerhet"],
    "miljo": ["miljo", "environment", "breeam", "ceequal"],
    "rigg": ["rigg", "logistikk", "rigging", "logistics"],
    "ifc": [".ifc"],
    "annet": [],
}


def classify_file(name: str) -> str:
    low = name.lower()
    for cat, keywords in TENDER_FILE_CATEGORIES.items():
        if any(kw in low for kw in keywords):
            return cat
    return "annet"


def normalize_uploaded_files(files) -> List[Dict[str, Any]]:
    """Turn uploaded files into flat manifest records."""
    records = []
    if not files:
        return records
    for f in files:
        name = getattr(f, "name", "ukjent_fil")
        size = getattr(f, "size", 0)
        records.append({
            "filename": name,
            "category": classify_file(name),
            "extension": Path(name).suffix.lower(),
            "size_kb": round(size / 1024, 1) if size else 0,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    return records


def load_project_files() -> List[Dict[str, Any]]:
    """Load files saved in Project Setup (qa_database/project_files/)."""
    records = []
    if not FILES_DIR.exists():
        return records
    for p in sorted(FILES_DIR.iterdir()):
        if p.is_file():
            records.append({
                "filename": p.name,
                "category": classify_file(p.name),
                "extension": p.suffix.lower(),
                "size_kb": round(p.stat().st_size / 1024, 1),
                "timestamp": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "source": "Project Setup",
            })
    return records


def expected_categories(procurement_mode: str, packages: List[str]) -> List[str]:
    base = ["konkurransegrunnlag", "beskrivelse", "tegning"]
    if procurement_mode in ["Totalentreprise", "Design & Build"]:
        base.append("kontrakt")
    if len(packages) > 3:
        base.append("bok")
    base.extend(["sha", "miljo"])
    return base


# ────────────────────────────────────────────────────────────────
# 7. RULES-BASED TENDER ANALYSIS
# ────────────────────────────────────────────────────────────────
SEVERITY_MAP = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


def build_tender_rules(
    records: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Heuristic analysis based on uploaded document manifest and config.
    Returns checklist items, risk items, RFI suggestions, etc.
    """
    procurement_mode = config.get("procurement_mode", "Totalentreprise")
    discipline_focus = config.get("discipline_focus", [])
    packages = config.get("packages", [])
    qa_level = config.get("qa_level", "Standard")
    bid_value = config.get("bid_value_mnok", 100)
    notes = config.get("notes", "")
    include_bid = config.get("include_bid_documents", True)

    found_cats = set(r["category"] for r in records)
    expected = expected_categories(procurement_mode, packages)
    missing = [c for c in expected if c not in found_cats]

    # Data completeness score
    total_expected = max(len(expected), 1)
    completeness = max(0.0, min(1.0, (total_expected - len(missing)) / total_expected))

    # Checklist items
    checklist: List[Dict[str, Any]] = []
    for cat in expected:
        status = "OK" if cat in found_cats else "MANGLER"
        severity = "HIGH" if status == "MANGLER" and cat in ["konkurransegrunnlag", "beskrivelse", "tegning"] else "MEDIUM" if status == "MANGLER" else "LOW"
        checklist.append({
            "topic": f"Dokumentkategori: {cat}",
            "status": status,
            "severity": severity,
            "paragraph_ref": "-",
            "reason": f"{'Funnet i opplastede dokumenter' if status == 'OK' else 'Ikke funnet blant opplastede filer – bor kontrolleres'}.",
            "source": "Regelmotor",
        })

    # Discipline-specific checks
    for disc in discipline_focus:
        found = any(disc.lower() in r["filename"].lower() for r in records)
        checklist.append({
            "topic": f"Fagdokumenter: {disc}",
            "status": "OK" if found else "ADVARSEL",
            "severity": "MEDIUM" if not found else "LOW",
            "paragraph_ref": "-",
            "reason": f"{'Fag-fil funnet' if found else 'Ingen fil med referanse til ' + disc + ' funnet'}.",
            "source": "Regelmotor",
        })

    # Package coverage
    for pkg in packages:
        found = any(pkg.lower() in r["filename"].lower() for r in records)
        checklist.append({
            "topic": f"Pakkedekning: {pkg}",
            "status": "OK" if found else "ADVARSEL",
            "severity": "MEDIUM" if not found else "LOW",
            "paragraph_ref": "-",
            "reason": f"{'Dekket' if found else 'Ingen fil refererer til pakke ' + pkg}.",
            "source": "Regelmotor",
        })

    if not include_bid:
        checklist.append({
            "topic": "Tilbudsdokumenter",
            "status": "MANGLER",
            "severity": "HIGH",
            "paragraph_ref": "-",
            "reason": "Tilbudsdokumenter er ikke markert som opplastet – begrenset kontroll mulig.",
            "source": "Regelmotor",
        })

    # Risk items
    risk_items: List[Dict[str, Any]] = []
    if len(missing) > 2:
        risk_items.append({
            "title": "Ufullstendig dokumentgrunnlag",
            "severity": "HIGH",
            "impact": "Risiko for at sentrale krav eller forutsetninger ikke fanges opp i tilbudet.",
            "recommendation": "Etterspor manglende dokumentkategorier for innlevering.",
            "source": "Regelmotor",
            "paragraph_ref": "-",
        })
    if bid_value > 200:
        risk_items.append({
            "title": "Hoy tilbudsverdi krever utvidet kontroll",
            "severity": "HIGH",
            "impact": f"Tilbudsverdi {nb_value(bid_value)} MNOK overstiger 200 MNOK – anbefaler forsterket kontrollregime.",
            "recommendation": "Vurder pre-bid review med uavhengig tredjepart.",
            "source": "Regelmotor",
            "paragraph_ref": "-",
        })
    if "sha" in missing:
        risk_items.append({
            "title": "SHA-dokumentasjon mangler",
            "severity": "HIGH",
            "impact": "Byggherreforskriften krever SHA-plan. Manglende SHA kan gi sanksjon.",
            "recommendation": "Etterspor SHA-plan og risikovurdering for utforelsefasen.",
            "source": "Regelmotor",
            "paragraph_ref": "-",
        })
    for disc in discipline_focus:
        if not any(disc.lower() in r["filename"].lower() for r in records):
            risk_items.append({
                "title": f"Manglende {disc}-dokumentasjon",
                "severity": "MEDIUM",
                "impact": f"Fag {disc} er prioritert men ingen dokumenter refererer til det.",
                "recommendation": f"Be om {disc}-underlag eller avklar scope.",
                "source": "Regelmotor",
                "paragraph_ref": "-",
            })

    # Interface risks from notes
    if "grensesnitt" in notes.lower():
        risk_items.append({
            "title": "Grensesnittrisiko identifisert",
            "severity": "MEDIUM",
            "impact": "Prosjektspesifikke grensesnitt krever sarskilt oppfolging mellom pakker.",
            "recommendation": "Lag grensesnittmatrise og avklar eierskap/ansvar for delingslinjer.",
            "source": "Prosjektnotat",
            "paragraph_ref": "-",
        })

    # RFI suggestions
    rfi_suggestions: List[Dict[str, Any]] = []
    for cat in missing[:5]:
        rfi_suggestions.append({
            "priority": "Hoy" if cat in ["konkurransegrunnlag", "beskrivelse", "tegning"] else "Middels",
            "question": f"Dokumentkategori '{cat}' er ikke funnet i konkurransegrunnlaget. Kan dette ettersendes eller avklares?",
            "why": f"Kategorien '{cat}' er forventet for {procurement_mode} og mangler i underlaget.",
            "owner": "Byggherre / tilbyder",
        })
    if bid_value > 100:
        rfi_suggestions.append({
            "priority": "Middels",
            "question": "Kan det bekreftes at alle mengder er endelige, eller skal tilbyder kalkulere egne mengder?",
            "why": "Ved hoy tilbudsverdi er mengderisiko vesentlig.",
            "owner": "Byggherre",
        })

    # Contract fields (summary)
    contract_fields = [
        {"field": "Anskaffelsesform", "value": procurement_mode, "source": "Konfigurasjon"},
        {"field": "Pakker", "value": ", ".join(packages), "source": "Konfigurasjon"},
        {"field": "Estimert verdi", "value": f"{nb_value(bid_value)} MNOK", "source": "Konfigurasjon"},
        {"field": "Kontrolldybde", "value": qa_level, "source": "Konfigurasjon"},
        {"field": "Prioriterte fag", "value": ", ".join(discipline_focus), "source": "Konfigurasjon"},
        {"field": "Antall dokumenter", "value": str(len(records)), "source": "Opplasting"},
        {"field": "Manglende kategorier", "value": ", ".join(missing) if missing else "Ingen", "source": "Regelmotor"},
    ]

    return {
        "checklist_items": checklist,
        "risk_items": risk_items,
        "rfi_suggestions": rfi_suggestions,
        "missing_categories": missing,
        "data_completeness_score": completeness,
        "contract_fields": contract_fields,
        "packages": packages,
    }


# ────────────────────────────────────────────────────────────────
# 8. AI ANALYSIS (optional enhancement)
# ────────────────────────────────────────────────────────────────
def run_ai_tender_analysis(
    records: List[Dict[str, Any]],
    config: Dict[str, Any],
    rules: Dict[str, Any],
) -> Dict[str, Any]:
    """
    If AI backend is available, enrich the rules-based result with AI summary.
    Returns a dict compatible with rules output, with additional AI fields.
    """
    if not HAS_AI:
        return {"data": None, "attempt_log": [{"step": "AI", "status": "Ingen AI-backend tilgjengelig."}]}

    manifest_text = "\n".join(
        f"- {r['filename']} ({r['category']}, {r['size_kb']} KB)" for r in records
    )
    missing_text = ", ".join(rules.get("missing_categories", [])) or "Ingen"
    risk_text = "\n".join(
        f"- {r['title']} [{r['severity']}]: {r['impact']}" for r in rules.get("risk_items", [])
    )

    prompt = f"""Du er Builtly Tender AI. Du skal analysere et tilbudsgrunnlag og gi en kort oppsummering.

PROSJEKT:
- Navn: {clean_text(pd_state.get('p_name', 'Ukjent'))}
- Type: {clean_text(pd_state.get('b_type', 'Ukjent'))}
- Sted: {clean_text(pd_state.get('kommune', ''))}

KONFIGURASJON:
- Anskaffelsesform: {config.get('procurement_mode')}
- Pakker: {', '.join(config.get('packages', []))}
- Kontrolldybde: {config.get('qa_level')}
- Verdi: {config.get('bid_value_mnok')} MNOK
- Prosjektnotater: {config.get('notes', '')}

OPPLASTEDE DOKUMENTER:
{manifest_text or 'Ingen dokumenter opplastet.'}

MANGLENDE KATEGORIER: {missing_text}

IDENTIFISERTE RISIKOER:
{risk_text or 'Ingen heuristiske risikoer.'}

Returner KUN gyldig JSON med dette skjemaet:
{{
  "executive_summary": "Kort sammendrag av tilbudsgrunnlagets tilstand og viktigste funn (2-4 setninger).",
  "contract_fields": [{{"field": "...", "value": "...", "source": "AI"}}],
  "checklist_items": [{{"topic": "...", "status": "OK|ADVARSEL|MANGLER", "severity": "HIGH|MEDIUM|LOW", "paragraph_ref": "-", "reason": "...", "source": "AI"}}],
  "risk_items": [{{"title": "...", "severity": "HIGH|MEDIUM|LOW", "impact": "...", "recommendation": "...", "source": "AI", "paragraph_ref": "-"}}],
  "rfi_suggestions": [{{"priority": "Hoy|Middels|Lav", "question": "...", "why": "...", "owner": "..."}}]
}}

Returner kun JSON, ingen annen tekst."""

    attempt_log = []
    try:
        raw = generate_text_ai(prompt, temperature=0.1)
        attempt_log.append({"step": "AI-kall", "status": "OK", "length": len(raw)})
        parsed = safe_json_loads(raw)
        if parsed:
            attempt_log.append({"step": "JSON-parsing", "status": "OK"})
            return {"data": parsed, "attempt_log": attempt_log}
        else:
            attempt_log.append({"step": "JSON-parsing", "status": "Feilet – kunne ikke tolke respons."})
            return {"data": None, "attempt_log": attempt_log}
    except Exception as e:
        attempt_log.append({"step": "AI-kall", "status": f"Feil: {type(e).__name__}: {e}"})
        return {"data": None, "attempt_log": attempt_log}


# ────────────────────────────────────────────────────────────────
# 9. REPORT BUILDER (Markdown)
# ────────────────────────────────────────────────────────────────
def build_markdown_report(
    records: List[Dict[str, Any]],
    rules: Dict[str, Any],
    ai_result: Dict[str, Any],
    config: Dict[str, Any],
) -> str:
    parts = []
    parts.append(f"# Anbudskontroll – {clean_text(pd_state.get('p_name', 'Prosjekt'))}")
    parts.append(f"*Generert: {datetime.now().strftime('%d.%m.%Y %H:%M')}*\n")

    # Executive summary
    ai_data = ai_result.get("data") if isinstance(ai_result, dict) else None
    summary = safe_get(ai_data, "executive_summary", "") if ai_data else ""
    if summary:
        parts.append(f"## Sammendrag\n{clean_text(summary)}\n")
    else:
        parts.append("## Sammendrag\nIngen AI-oppsummering tilgjengelig. Resultatet er basert pa regelmotor.\n")

    # Config
    parts.append("## Konfigurasjon")
    for cf in rules.get("contract_fields", []):
        if isinstance(cf, dict):
            parts.append(f"- **{cf.get('field', '')}**: {cf.get('value', '')}")
    parts.append("")

    # Checklist
    parts.append("## Sjekkliste")
    all_checks = rules.get("checklist_items", [])
    if ai_data and isinstance(safe_get(ai_data, "checklist_items"), list):
        all_checks = all_checks + safe_get(ai_data, "checklist_items", [])
    for item in all_checks:
        if isinstance(item, dict):
            parts.append(f"- [{item.get('status', '?')}] {item.get('topic', '')} — {item.get('reason', '')}")
    parts.append("")

    # Risks
    parts.append("## Risikoer")
    all_risks = rules.get("risk_items", [])
    if ai_data and isinstance(safe_get(ai_data, "risk_items"), list):
        all_risks = all_risks + safe_get(ai_data, "risk_items", [])
    for item in all_risks:
        if isinstance(item, dict):
            parts.append(f"- **{item.get('title', '')}** [{item.get('severity', '')}]: {item.get('impact', '')}")
    parts.append("")

    # RFI
    parts.append("## Forslag til RFI / sporsmal")
    all_rfi = rules.get("rfi_suggestions", [])
    if ai_data and isinstance(safe_get(ai_data, "rfi_suggestions"), list):
        all_rfi = all_rfi + safe_get(ai_data, "rfi_suggestions", [])
    for item in all_rfi:
        if isinstance(item, dict):
            parts.append(f"- [{item.get('priority', '')}] {item.get('question', '')}")
    parts.append("")

    # Manifest
    parts.append("## Dokumentmanifest")
    for r in records:
        parts.append(f"- {r['filename']} ({r['category']}, {r['size_kb']} KB)")
    parts.append("")

    parts.append("---\n*Rapport generert av Builtly Anbudskontroll. Utkast – krever faglig gjennomgang.*")
    return "\n".join(parts)


# ────────────────────────────────────────────────────────────────
# 10. PDF REPORT BUILDER
# ────────────────────────────────────────────────────────────────
def build_pdf_report(
    records: List[Dict[str, Any]],
    rules: Dict[str, Any],
    ai_result: Dict[str, Any],
    config: Dict[str, Any],
) -> Optional[bytes]:
    """Build a professional PDF matching the Konstruksjon report style."""
    if FPDF is None:
        return None

    class TenderPDF(FPDF):
        def header(self):
            if self.page_no() == 1:
                return
            self.set_y(11)
            self.set_text_color(88, 94, 102)
            self.set_font("Helvetica", "", 8)
            self.cell(0, 4, clean_text(f"Anbudskontroll – {pd_state.get('p_name', 'Prosjekt')}"), 0, 0, "L")
            self.cell(0, 4, clean_text(datetime.now().strftime("%d.%m.%Y")), 0, 1, "R")
            self.set_draw_color(188, 192, 197)
            self.line(18, 18, 192, 18)
            self.set_y(24)

        def footer(self):
            self.set_y(-12)
            self.set_draw_color(210, 214, 220)
            self.line(18, 285, 192, 285)
            self.set_font("Helvetica", "", 7)
            self.set_text_color(110, 114, 119)
            self.cell(60, 5, "Builtly-TENDER-001", 0, 0, "L")
            self.cell(70, 5, clean_text("Utkast - krever faglig kontroll"), 0, 0, "C")
            self.cell(0, 5, clean_text(f"Side {self.page_no()}"), 0, 0, "R")

        def ensure_space(self, h):
            if self.get_y() + h > 272:
                self.add_page()

        def section_title(self, title):
            self.ensure_space(20)
            self.ln(2)
            self.set_font("Helvetica", "B", 17)
            self.set_text_color(36, 50, 72)
            self.set_x(20)
            self.multi_cell(170, 8, clean_text(title.upper()), 0, "L")
            self.set_draw_color(204, 209, 216)
            self.line(20, self.get_y() + 1, 190, self.get_y() + 1)
            self.ln(5)

        def body_text(self, text):
            if not text:
                return
            self.set_x(20)
            self.set_font("Helvetica", "", 10.2)
            self.set_text_color(35, 39, 43)
            self.multi_cell(170, 5.5, clean_text(text))
            self.ln(1.6)

        def bullet_list(self, items):
            for item in items:
                if not item:
                    continue
                self.ensure_space(10)
                self.set_font("Helvetica", "", 10.1)
                self.set_text_color(35, 39, 43)
                y = self.get_y()
                self.set_xy(22, y)
                self.cell(6, 5.2, "-", 0, 0, "L")
                self.set_xy(28, y)
                self.multi_cell(162, 5.2, clean_text(item))
                self.ln(0.8)

        def kv_card(self, items, x=None, width=80, title=None):
            if x is None:
                x = self.get_x()
            height = 10 + (len(items) * 6.3) + (7 if title else 0)
            self.ensure_space(height + 3)
            start_y = self.get_y()
            self.set_fill_color(245, 247, 249)
            self.set_draw_color(214, 219, 225)
            self.rect(x, start_y, width, height, "DF")
            yy = start_y + 5
            if title:
                self.set_xy(x + 4, yy)
                self.set_font("Helvetica", "B", 10)
                self.set_text_color(48, 64, 86)
                self.cell(width - 8, 5, clean_text(title.upper()), 0, 1)
                yy += 7
            for label, value in items:
                self.set_xy(x + 4, yy)
                self.set_font("Helvetica", "B", 8.6)
                self.set_text_color(72, 79, 87)
                self.cell(28, 5, clean_text(label), 0, 0)
                self.set_font("Helvetica", "", 8.6)
                self.set_text_color(35, 39, 43)
                self.multi_cell(width - 34, 5, clean_text(value))
                yy = self.get_y() + 1
            self.set_y(max(self.get_y(), start_y + height))

        def highlight_box(self, title, items, fill=(245, 247, 250), accent=(50, 77, 106)):
            self.set_font("Helvetica", "", 10)
            total_h = 14 + sum(8 for _ in items)
            self.ensure_space(total_h + 5)
            x, y = 20, self.get_y()
            self.set_fill_color(*fill)
            self.set_draw_color(217, 223, 230)
            self.rect(x, y, 170, total_h, "DF")
            self.set_fill_color(*accent)
            self.rect(x, y, 3, total_h, "F")
            self.set_xy(x + 6, y + 4)
            self.set_font("Helvetica", "B", 10.5)
            self.set_text_color(*accent)
            self.cell(0, 5, clean_text(title.upper()), 0, 1)
            self.set_text_color(35, 39, 43)
            self.set_font("Helvetica", "", 10)
            yy = y + 10
            for item in items:
                self.set_xy(x + 8, yy)
                self.cell(5, 5, "-", 0, 0)
                self.multi_cell(154, 5.2, clean_text(item))
                yy = self.get_y() + 2
            self.set_y(y + total_h + 3)

    pdf = TenderPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(True, margin=15)

    # ── Cover page ──
    pdf.add_page()
    if os.path.exists("logo.png"):
        try:
            pdf.image("logo.png", x=150, y=15, w=40)
        except Exception:
            pass

    pdf.set_xy(20, 45)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(100, 105, 110)
    pdf.cell(80, 6, "ANBUDSKONTROLL", 0, 1, "L")

    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 30)
    pdf.set_text_color(20, 28, 38)
    pdf.multi_cell(95, 11, clean_text(pd_state.get("p_name", "Prosjekt")), 0, "L")

    pdf.ln(4)
    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(64, 68, 74)
    pdf.multi_cell(95, 6.5, clean_text("Avviksmatrise, mangelliste, uklarhetslogg og scopesammenstilling"), 0, "L")

    pdf.set_xy(118, 45)
    pdf.kv_card([
        ("Oppdragsgiver", clean_text(pd_state.get("c_name", "-"))),
        ("Emne", "Anbudskontroll"),
        ("Dato / rev", f"{datetime.now().strftime('%d.%m.%Y')} / 01"),
        ("Dokumentkode", "Builtly-TENDER-001"),
        ("Anskaffelse", config.get("procurement_mode", "-")),
        ("Verdi", f"{nb_value(config.get('bid_value_mnok', 0))} MNOK"),
    ], x=118, width=72)

    pdf.set_xy(20, 180)
    pdf.set_font("Helvetica", "", 8.8)
    pdf.set_text_color(104, 109, 116)
    pdf.multi_cell(170, 4.5, clean_text(
        "Rapporten er generert av Builtly Anbudskontroll pa bakgrunn av opplastede dokumenter og prosjektkonfigurasjon. "
        "Dokumentet er et arbeidsutkast og skal fagkontrolleres for bruk i tilbudsinnlevering eller beslutning."
    ))

    # ── Table of contents ──
    pdf.add_page()
    pdf.section_title("Innholdsfortegnelse")
    toc = [
        "1. Sammendrag", "2. Konfigurasjon og forutsetninger",
        "3. Dokumentmanifest", "4. Sjekkliste",
        "5. Risikoer og avvik", "6. Forslag til RFI / sporsmal",
        "7. Scopeoversikt",
    ]
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(45, 49, 55)
    for item in toc:
        y = pdf.get_y()
        pdf.set_x(22)
        pdf.cell(0, 6, clean_text(item), 0, 0, "L")
        pdf.set_draw_color(225, 229, 234)
        pdf.line(22, y + 6, 188, y + 6)
        pdf.ln(8)

    # ── 1. Summary ──
    pdf.add_page()
    pdf.section_title("1. Sammendrag")
    ai_data = safe_get(ai_result, "data")
    summary = safe_get(ai_data, "executive_summary", "") if isinstance(ai_data, dict) else ""
    if summary:
        pdf.body_text(summary)
    else:
        pdf.body_text("Ingen AI-oppsummering tilgjengelig. Resultatet er basert pa heuristisk regelmotor.")

    completeness = rules.get("data_completeness_score", 0)
    high_risks = len([r for r in rules.get("risk_items", []) if isinstance(r, dict) and r.get("severity") == "HIGH"])
    pdf.highlight_box("Nokkeltall", [
        f"Dokumenter lastet opp: {len(records)}",
        f"Datakompletthet: {int(completeness * 100)}%",
        f"Manglende kategorier: {len(rules.get('missing_categories', []))}",
        f"Hoyrisikoer: {high_risks}",
    ])

    # ── 2. Config ──
    pdf.section_title("2. Konfigurasjon og forutsetninger")
    for cf in rules.get("contract_fields", []):
        if isinstance(cf, dict):
            pdf.body_text(f"{cf.get('field', '')}: {cf.get('value', '')}")

    # ── 3. Manifest ──
    pdf.section_title("3. Dokumentmanifest")
    pdf.bullet_list([f"{r['filename']} — {r['category']} ({r['size_kb']} KB)" for r in records])

    # ── 4. Checklist ──
    pdf.section_title("4. Sjekkliste")
    all_checks = rules.get("checklist_items", [])
    if isinstance(ai_data, dict) and isinstance(safe_get(ai_data, "checklist_items"), list):
        all_checks = all_checks + safe_get(ai_data, "checklist_items", [])
    for item in all_checks:
        if isinstance(item, dict):
            pdf.ensure_space(12)
            pdf.body_text(f"[{item.get('status', '?')}] {item.get('topic', '')} — {item.get('reason', '')}")

    # ── 5. Risks ──
    pdf.section_title("5. Risikoer og avvik")
    all_risks = rules.get("risk_items", [])
    if isinstance(ai_data, dict) and isinstance(safe_get(ai_data, "risk_items"), list):
        all_risks = all_risks + safe_get(ai_data, "risk_items", [])
    for item in all_risks:
        if isinstance(item, dict):
            pdf.ensure_space(14)
            pdf.body_text(f"{item.get('title', '')} [{item.get('severity', '')}]")
            pdf.body_text(f"  Konsekvens: {item.get('impact', '-')}")
            pdf.body_text(f"  Anbefaling: {item.get('recommendation', '-')}")

    # ── 6. RFI ──
    pdf.section_title("6. Forslag til RFI / sporsmal")
    all_rfi = rules.get("rfi_suggestions", [])
    if isinstance(ai_data, dict) and isinstance(safe_get(ai_data, "rfi_suggestions"), list):
        all_rfi = all_rfi + safe_get(ai_data, "rfi_suggestions", [])
    pdf.bullet_list([
        f"[{r.get('priority', '')}] {r.get('question', '')}" for r in all_rfi if isinstance(r, dict)
    ])

    # ── 7. Scope ──
    pdf.section_title("7. Scopeoversikt")
    packages = config.get("packages", [])
    for idx, pkg in enumerate(packages):
        status = "Dekket" if idx < max(1, len(packages) - 1) else "Krevende grensesnitt"
        pdf.body_text(f"{pkg}: {status}")

    # Disclaimer
    pdf.ln(10)
    pdf.highlight_box(
        "Ansvarsfraskrivelse",
        [
            "Rapporten er et arbeidsutkast generert av Builtly Anbudskontroll.",
            "Dokumentet er ikke signert med ansvarsrett og er ikke juridisk bindende.",
            "Resultatet skal fagkontrolleres for bruk.",
        ],
        fill=(255, 248, 235),
        accent=(180, 130, 40),
    )

    return bytes(pdf.output())


# ────────────────────────────────────────────────────────────────
# 11. UI – HEADER + BACK BUTTON
# ────────────────────────────────────────────────────────────────
top_l, top_r = st.columns([4, 1])
with top_l:
    logo = logo_data_uri()
    logo_html = (
        f'<img src="{logo}" class="brand-logo">' if logo
        else '<h2 style="margin:0; color:white;">Builtly</h2>'
    )
    render_html(logo_html)

with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("\u2190 Tilbake til prosjekt", use_container_width=True, type="secondary"):
        target = find_page("Project")
        if target:
            st.switch_page(target)
        else:
            st.warning("Fant ikke Project-siden.")

st.markdown(
    "<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>",
    unsafe_allow_html=True,
)

# ── Check project exists ──
if pd_state.get("p_name") in ["", "Nytt Prosjekt", None]:
    st.warning("Du ma sette opp prosjektdata for du kan bruke denne modulen.")
    if find_page("Project"):
        if st.button("Ga til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()


# ────────────────────────────────────────────────────────────────
# 12. HERO
# ────────────────────────────────────────────────────────────────
render_html(f"""
<div class="hero-card">
    <div class="hero-eyebrow">Tender Control</div>
    <div class="hero-title">Anbudskontroll som finner hull for markedet gjor det.</div>
    <div class="hero-subtitle">
        Sammenstill konkurransegrunnlag, beskrivelser, tegninger, IFC/PDF og tilbudsdokumenter i en arbeidsflate.
        Modulen lager avviksmatrise, mangelliste, uklarhetslogg, scopesammenstilling og forslag til sporsmal for innlevering.
    </div>
    <div class="hero-pills">
        <span class="hero-pill">Entreprenor</span>
        <span class="hero-pill">Radgiver</span>
        <span class="hero-pill">Utbygger</span>
        <span class="hero-pill">Audit trail</span>
        <span class="hero-pill">RFI-generator</span>
    </div>
    <div class="hero-badge">Horizontal engine</div>
</div>
""")


# ────────────────────────────────────────────────────────────────
# 13. FORM – Intake
# ────────────────────────────────────────────────────────────────
left, right = st.columns([1.25, 0.75], gap="large")

with left:
    render_html("""
    <div class="section-header">
        <span class="section-badge">Tender intake</span>
        <h3>Inntak og kontrollparametere</h3>
        <p>Definer hva som inngar i tilbudspakken og hvilke kontroller Builtly skal prioritere.</p>
    </div>
    """)

    with st.form("tender_control_form"):
        c1, c2 = st.columns(2)
        with c1:
            procurement_mode = st.selectbox(
                "Anskaffelsesform",
                ["Totalentreprise", "Utforelsesentreprise", "Samspillsentreprise", "Design & Build"],
                index=0,
            )
            discipline_focus = st.multiselect(
                "Fag / scope som skal kvalitetssikres forst",
                ["ARK", "RIB", "RIV", "RIE", "Brann", "Akustikk", "Geo", "Trafikk", "SHA", "MOP", "BREEAM"],
                default=["ARK", "RIB", "Geo", "Brann"],
            )
            delivery_level = st.selectbox("Leveranseniva", ["auto", "reviewed", "attested"], index=1)
            include_bid_documents = st.toggle("Tilbudsdokumenter er lastet opp", value=True)

        with c2:
            packages = st.multiselect(
                "Pakker / delentrepriser",
                ["Grunnarbeid", "Betong", "Stal", "Fasade", "Tomrer", "Tak", "VVS", "Elektro", "Utomhus"],
                default=["Grunnarbeid", "Betong", "Fasade", "VVS", "Elektro"],
            )
            qa_level = st.select_slider(
                "Kontrolldybde",
                options=["Lett", "Standard", "Dyp", "Pre-bid review"],
                value="Dyp",
            )
            required_outputs = st.multiselect(
                "Onskede leveranser",
                ["Avviksmatrise", "Mangelliste", "Uklarhetslogg", "Scopesammenstilling", "Forslag til sporsmal/RFI", "Submission readiness"],
                default=["Avviksmatrise", "Mangelliste", "Uklarhetslogg", "Scopesammenstilling", "Forslag til sporsmal/RFI"],
            )
            bid_value_mnok = st.number_input("Estimert tilbudsverdi (MNOK)", min_value=1.0, value=120.0, step=1.0)

        files = st.file_uploader(
            "Last opp konkurransegrunnlag, tegninger, IFC/PDF og tilbudsdokumenter",
            type=["pdf", "ifc", "xlsx", "xls", "docx", "csv", "zip", "dwg", "dxf"],
            accept_multiple_files=True,
            key="tender_files_v6",
        )
        rulepack_upload = st.file_uploader(
            "Valgfritt: last opp eget regelbibliotek (JSON/CSV/XLSX)",
            type=["json", "csv", "xlsx", "xls"],
            key="tender_rulepack_v2",
        )
        notes = st.text_area(
            "Prosjektspesifikke forhold som bor vektes hoyt",
            value="Saerskilt fokus pa grensesnitt mellom grunnarbeid, betong og fasade. Kontroller at rigg/logistikk, SHA og ytre miljo er konsistente i alle dokumenter.",
            height=110,
        )
        submitted = st.form_submit_button("Kjor anbudskontroll")


# ────────────────────────────────────────────────────────────────
# 14. DISCLAIMER BANNER
# ────────────────────────────────────────────────────────────────
disclaimer_text = {
    "auto": "Automatisk nivavurdering – resultatet er et forsteutkast.",
    "reviewed": "Dette utkastet er ment for fagperson-gjennomgang. Det er ikke signert med ansvarsrett og er ikke juridisk bindende.",
    "attested": "Markert som attestert – men krever fortsatt signatur og kontroll for juridisk gyldighet.",
}
render_html(f"""
<div class="disclaimer-banner">
    <div class="db-title">Leveranseniva: {delivery_level}</div>
    <div class="db-text">{disclaimer_text.get(delivery_level, '')}</div>
</div>
""")


# ────────────────────────────────────────────────────────────────
# 15. RUN ANALYSIS
# ────────────────────────────────────────────────────────────────
config = {
    "procurement_mode": procurement_mode,
    "discipline_focus": discipline_focus,
    "packages": packages,
    "qa_level": qa_level,
    "required_outputs": required_outputs,
    "bid_value_mnok": bid_value_mnok,
    "notes": notes,
    "include_bid_documents": include_bid_documents,
    "delivery_level": delivery_level,
}

records = normalize_uploaded_files(files if submitted else files or [])
project_records = load_project_files()
if project_records:
    # Avoid duplicates by filename
    existing_names = {r["filename"] for r in records}
    for pr in project_records:
        if pr["filename"] not in existing_names:
            records.append(pr)
    st.success(f"{len(project_records)} fil(er) hentet automatisk fra prosjektoppsettet.")
rules = build_tender_rules(records, config)

# AI analysis (run when submitted or when files present)
if "tender_ai_result" not in st.session_state:
    st.session_state.tender_ai_result = {"data": None, "attempt_log": []}

if submitted and records and HAS_AI:
    with st.spinner("Kjorer AI-analyse..."):
        st.session_state.tender_ai_result = run_ai_tender_analysis(records, config, rules)

ai_result = st.session_state.tender_ai_result
ai_data = safe_get(ai_result, "data") if isinstance(ai_result, dict) else None


# ────────────────────────────────────────────────────────────────
# 16. RIGHT COLUMN – Project snapshot + metrics
# ────────────────────────────────────────────────────────────────
with right:
    # Project snapshot
    render_html(f"""
    <div class="snapshot-card">
        <span class="sc-badge">Tender context</span>
        <div class="sc-name">{clean_text(pd_state.get('p_name', 'Prosjekt'))}</div>
        <div class="sc-row">Type: {clean_text(pd_state.get('b_type', '-'))}</div>
        <div class="sc-row">Sted: {clean_text(pd_state.get('adresse', ''))}, {clean_text(pd_state.get('kommune', ''))}</div>
        <div class="sc-row">Etasjer: {nb_value(pd_state.get('etasjer', '-'))} | BTA: {nb_value(pd_state.get('bta', '-'))} m2</div>
    </div>
    """)

    # Compute metrics
    risk_items = rules.get("risk_items", [])
    if isinstance(ai_data, dict) and isinstance(safe_get(ai_data, "risk_items"), list):
        risk_items = risk_items + safe_get(ai_data, "risk_items", [])
    high_risks = len([r for r in risk_items if isinstance(r, dict) and r.get("severity") == "HIGH"])
    completeness = rules.get("data_completeness_score", 0.0)
    readiness = max(35, int(completeness * 100) - 4 * len(rules.get("missing_categories", [])) - 3 * high_risks)

    render_html(f"""
    <div class="metric-row">
        <div class="metric-card">
            <div class="mc-value">{len(records)}</div>
            <div class="mc-label">Dokumenter</div>
            <div class="mc-desc">I kontrollsloyfe</div>
        </div>
        <div class="metric-card">
            <div class="mc-value">{len(rules.get('missing_categories', []))}</div>
            <div class="mc-label">Manglende kat.</div>
            <div class="mc-desc">Forventede typer</div>
        </div>
    </div>
    <div class="metric-row">
        <div class="metric-card">
            <div class="mc-value">{high_risks}</div>
            <div class="mc-label">Hoy risiko</div>
            <div class="mc-desc">Bor lukkes/RFI</div>
        </div>
        <div class="metric-card">
            <div class="mc-value">{readiness}%</div>
            <div class="mc-label">Readiness</div>
            <div class="mc-desc">Modenhetsvurdering</div>
        </div>
    </div>
    """)

    # Config payload preview
    with st.expander("Bestillingspayload"):
        st.json({
            "delivery_level": delivery_level,
            "procurement_mode": procurement_mode,
            "packages": packages,
            "required_outputs": required_outputs,
            "qa_level": qa_level,
            "bid_value_mnok": bid_value_mnok,
        })


# ────────────────────────────────────────────────────────────────
# 17. RESULTS – Tabs
# ────────────────────────────────────────────────────────────────
render_html("""
<div class="section-header">
    <span class="section-badge">Review</span>
    <h3>Analyse og eksport</h3>
    <p>Rules-first sjekkliste, AI-oppsummering og revisjonsspor i samme arbeidsflate.</p>
</div>
""")

tabs = st.tabs(["AI-utkast", "Dokumentmanifest", "Sjekkliste", "Risiko og RFI", "Scope / pakker", "Audit trail"])

# ── Tab 0: AI Draft ──
with tabs[0]:
    st.markdown("### Sammendrag")
    summary = safe_get(ai_data, "executive_summary", "") if isinstance(ai_data, dict) else ""
    st.write(summary or "Ingen AI-oppsummering tilgjengelig. Kjor analysen med dokumenter for a fa AI-resultat.")

    # Contract fields
    cf_rules = rules.get("contract_fields", [])
    cf_ai = safe_get(ai_data, "contract_fields", []) if isinstance(ai_data, dict) else []
    all_cf = cf_rules + (cf_ai if isinstance(cf_ai, list) else [])
    if all_cf:
        cf_df = list_to_dataframe(all_cf, ["field", "value", "source"])
        st.dataframe(cf_df, use_container_width=True, hide_index=True)

    # Downloads
    report_md = build_markdown_report(records, rules, ai_result, config)
    st.download_button(
        "Last ned anbudsrapport (.md)",
        data=report_md,
        file_name="tender_report.md",
        mime="text/markdown",
    )

    pdf_bytes = build_pdf_report(records, rules, ai_result, config)
    if pdf_bytes:
        st.download_button(
            "Last ned anbudsrapport (.pdf)",
            data=pdf_bytes,
            file_name="tender_report.pdf",
            mime="application/pdf",
        )

    ai_json = safe_get(ai_result, "data") or {}
    st.download_button(
        "Last ned AI-resultat (.json)",
        data=json.dumps(ai_json, indent=2, ensure_ascii=False, default=str),
        file_name="tender_result.json",
        mime="application/json",
    )

    # Attempt log
    attempt_log = safe_get(ai_result, "attempt_log", [])
    if attempt_log:
        with st.expander("AI-forsokslogg"):
            for entry in attempt_log:
                if isinstance(entry, dict):
                    st.write(f"**{entry.get('step', '?')}**: {entry.get('status', '-')}")

# ── Tab 1: Document Manifest ──
with tabs[1]:
    if records:
        manifest_df = pd.DataFrame(records)
        st.dataframe(manifest_df, use_container_width=True, hide_index=True)
        csv_data = manifest_df.to_csv(index=False).encode("utf-8")
        st.download_button("Last ned dokumentmanifest (.csv)", data=csv_data, file_name="tender_manifest.csv", mime="text/csv")
    else:
        st.info("Ingen dokumenter lastet opp enna.")

# ── Tab 2: Checklist ──
with tabs[2]:
    all_checks = rules.get("checklist_items", [])
    if isinstance(ai_data, dict) and isinstance(safe_get(ai_data, "checklist_items"), list):
        all_checks = all_checks + safe_get(ai_data, "checklist_items", [])
    if all_checks:
        checklist_df = list_to_dataframe(all_checks, ["topic", "status", "severity", "paragraph_ref", "reason", "source"])
        st.dataframe(checklist_df, use_container_width=True, hide_index=True)
        csv_data = checklist_df.to_csv(index=False).encode("utf-8")
        st.download_button("Last ned sjekkliste (.csv)", data=csv_data, file_name="tender_checklist.csv", mime="text/csv")
    else:
        st.info("Kjor analysen for a se sjekkliste.")

    if rules.get("missing_categories"):
        st.warning("Manglende kategorier: " + ", ".join(rules["missing_categories"]))

# ── Tab 3: Risk & RFI ──
with tabs[3]:
    all_risks = rules.get("risk_items", [])
    if isinstance(ai_data, dict) and isinstance(safe_get(ai_data, "risk_items"), list):
        all_risks = all_risks + safe_get(ai_data, "risk_items", [])

    all_rfi = rules.get("rfi_suggestions", [])
    if isinstance(ai_data, dict) and isinstance(safe_get(ai_data, "rfi_suggestions"), list):
        all_rfi = all_rfi + safe_get(ai_data, "rfi_suggestions", [])

    st.markdown("#### Risikoer")
    if all_risks:
        risk_df = list_to_dataframe(all_risks, ["title", "severity", "impact", "recommendation", "source", "paragraph_ref"])
        st.dataframe(risk_df, use_container_width=True, hide_index=True)
        csv_data = risk_df.to_csv(index=False).encode("utf-8")
        st.download_button("Last ned risikologg (.csv)", data=csv_data, file_name="tender_risk.csv", mime="text/csv")
    else:
        st.info("Ingen risikoer identifisert.")

    st.markdown("#### RFI-forslag")
    if all_rfi:
        rfi_df = list_to_dataframe(all_rfi, ["priority", "question", "why", "owner"])
        st.dataframe(rfi_df, use_container_width=True, hide_index=True)
        csv_data = rfi_df.to_csv(index=False).encode("utf-8")
        st.download_button("Last ned RFI-utkast (.csv)", data=csv_data, file_name="tender_rfi.csv", mime="text/csv")
    else:
        st.info("Ingen RFI-forslag generert.")

# ── Tab 4: Scope / packages ──
with tabs[4]:
    pkg_list = packages if packages else ["Grunnarbeid", "Betong", "Fasade"]
    scope_rows = []
    for idx, pkg in enumerate(pkg_list):
        status = "Dekket" if idx < max(1, len(pkg_list) - 1) else "Krevende grensesnitt"
        scope_rows.append({
            "pakke": pkg,
            "status": status,
            "kommentar": "Kontroller mengder, delingslinjer og ansvar mot dokumentgrunnlaget.",
        })
    scope_df = pd.DataFrame(scope_rows)
    st.dataframe(scope_df, use_container_width=True, hide_index=True)
    csv_data = scope_df.to_csv(index=False).encode("utf-8")
    st.download_button("Last ned scopeoversikt (.csv)", data=csv_data, file_name="tender_scope.csv", mime="text/csv")

# ── Tab 5: Audit trail ──
with tabs[5]:
    audit_rows = [
        {
            "tidspunkt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "modul": "Tender Control",
            "leveranseniva": delivery_level,
            "handling": "Anbudskontroll kjort",
            "dokumenter": len(records),
            "ai_status": "OK" if (isinstance(ai_data, dict) and ai_data) else "Ikke kjort / feilet",
            "bruker": "Builtly-bruker",
        }
    ]
    audit_df = pd.DataFrame(audit_rows)
    st.dataframe(audit_df, use_container_width=True, hide_index=True)


# ────────────────────────────────────────────────────────────────
# 18. NEXT STEPS PANEL
# ────────────────────────────────────────────────────────────────
render_html("""
<div class="panel-box gold">
    <span class="hero-badge">Pilot backlog</span>
    <h4>Neste naturlige steg i pilot</h4>
    <p>Denne modulen er laget som en horisontal motor for entreprenor, radgiver og utbygger.
       Knytt parser, review og eksport enda tettere til live konkurransegrunnlag i neste iterasjon.</p>
    <ul>
        <li>Koble dokumentindeks og revisjonssammenligning til tilbudsbok og kontraktsgrunnlag.</li>
        <li>Legg til klikkbar avvikslogg med manuell overstyring og RFI-status.</li>
        <li>Bygg pre-fylt tilbudsgrunnlag for DOCX og signert review-flyt.</li>
        <li>Koble batch/API-laget mot partner- eller bankkanaler uten a fronte det offentlig.</li>
    </ul>
</div>
""")
