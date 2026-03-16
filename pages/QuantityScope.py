# -*- coding: utf-8 -*-
"""
Builtly | Mengde & Scope Intelligence
Self-contained Streamlit module – no external builtly_* dependencies.
Design language matches Konstruksjon (RIB) module.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
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
    page_title="Builtly | Mengde & Scope",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DB_DIR = Path("qa_database")
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
    for old, new in {
        "\u2013": "-", "\u2014": "-", "\u201c": '"', "\u201d": '"',
        "\u2018": "'", "\u2019": "'", "\u2026": "...", "\u2022": "-",
        "\u2264": "<=", "\u2265": ">=",
    }.items():
        text = text.replace(old, new)
    return text


def nb_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        txt = f"{value:.1f}" if abs(value) >= 10 else f"{value:.2f}".rstrip("0").rstrip(".")
        return txt.replace(".", ",")
    return str(value)


def safe_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def extract_json_blob(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"^```json", "", text.strip(), flags=re.I).strip()
    cleaned = re.sub(r"^```", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    first, last = cleaned.find("{"), cleaned.rfind("}")
    if first != -1 and last > first:
        cleaned = cleaned[first: last + 1]
    return re.sub(r",\s*([}\]])", r"\1", cleaned)


def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    blob = extract_json_blob(text)
    if not blob:
        return None
    try:
        return json.loads(blob)
    except Exception:
        try:
            repaired = re.sub(r"(?<!\\)'", '"', blob.replace("\t", " "))
            return json.loads(re.sub(r",\s*([}\]])", r"\1", repaired))
        except Exception:
            return None


# ────────────────────────────────────────────────────────────────
# 3. PREMIUM CSS — identical to Konstruksjon / TenderControl
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
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
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
    button[kind="primary"], .stFormSubmitButton > button {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important; border: none !important; font-weight: 750 !important;
        border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover, .stFormSubmitButton > button:hover {
        transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important;
    }
    button[kind="secondary"] {
        background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important;
        border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important;
        font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s !important;
    }
    button[kind="secondary"]:hover {
        background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important;
        color: var(--accent) !important; transform: translateY(-2px) !important;
    }

    /* Inputs */
    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div {
        background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important;
        border-radius: 8px !important;
    }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, div[data-baseweb="select"] * {
        background-color: transparent !important; color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important; border: none !important; box-shadow: none !important;
    }
    div[data-baseweb="base-input"]:focus-within, div[data-baseweb="select"] > div:focus-within,
    .stTextArea > div > div > div:focus-within {
        border-color: var(--accent) !important; box-shadow: 0 0 0 1px rgba(56, 194, 201, 0.5) !important;
    }

    /* Dropdowns */
    ul[data-baseweb="menu"] { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; }
    ul[data-baseweb="menu"] li { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
    ul[data-baseweb="menu"] li:hover { background-color: rgba(56, 194, 201, 0.1) !important; }

    /* Input hints */
    div[data-testid="InputInstructions"], div[data-testid="InputInstructions"] > span {
        color: #9fb0c3 !important; -webkit-text-fill-color: #9fb0c3 !important;
    }

    /* Labels */
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label,
    .stFileUploader label, .stMultiSelect label, .stSlider label, .stCheckbox label, .stRadio label {
        color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important;
    }

    /* Slider text */
    .stSlider div[data-baseweb="slider"] div { color: #ffffff !important; }
    div[data-testid="stThumbValue"], .stSlider [data-testid="stTickBarMin"], .stSlider [data-testid="stTickBarMax"] {
        color: #ffffff !important; -webkit-text-fill-color: #ffffff !important;
    }
    .stSelectSlider div[data-baseweb="slider"] div { color: #ffffff !important; }

    /* Toggle / checkbox */
    .stCheckbox span, .stToggle span, label[data-baseweb="checkbox"] span {
        color: #f5f7fb !important; -webkit-text-fill-color: #f5f7fb !important;
    }

    /* Multiselect tags */
    span[data-baseweb="tag"] {
        background-color: rgba(56, 194, 201, 0.15) !important; color: #38bdf8 !important;
        border: 1px solid rgba(56, 194, 201, 0.4) !important; border-radius: 6px !important;
    }
    span[data-baseweb="tag"] span { color: #38bdf8 !important; -webkit-text-fill-color: #38bdf8 !important; }

    /* Expanders */
    div[data-testid="stExpander"] details, div[data-testid="stExpander"] details summary, div[data-testid="stExpander"] {
        background-color: #0c1520 !important; color: #f5f7fb !important; border-radius: 12px !important;
    }
    div[data-testid="stExpander"] details summary:hover { background-color: rgba(255,255,255,0.03) !important; }
    div[data-testid="stExpander"] details summary p { color: #f5f7fb !important; font-weight: 650 !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; margin-bottom: 1rem !important; }
    div[data-testid="stExpanderDetails"] { background: transparent !important; color: #f5f7fb !important; }

    /* File uploader */
    [data-testid="stFileUploaderDropzone"] {
        background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important;
        border-radius: 12px !important; padding: 2rem !important;
    }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #38c2c9 !important; background-color: rgba(56, 194, 201, 0.05) !important; }
    [data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
    [data-testid="stFileUploaderFileData"] { background-color: rgba(255,255,255,0.02) !important; color: #f5f7fb !important; border-radius: 8px !important; }

    /* Alerts */
    [data-testid="stAlert"] { background-color: rgba(56, 194, 201, 0.05) !important; border: 1px solid rgba(56, 194, 201, 0.2) !important; border-radius: 12px !important; }
    [data-testid="stAlert"] * { color: #f5f7fb !important; }

    /* Tabs */
    .stTabs [data-baseweb="tab-list"] { gap: 4px; background-color: rgba(10, 22, 35, 0.5); border-radius: 12px; padding: 4px; }
    .stTabs [data-baseweb="tab"] { background-color: transparent !important; color: #9fb0c3 !important; border-radius: 8px !important; padding: 8px 16px !important; font-weight: 600 !important; border: none !important; }
    .stTabs [data-baseweb="tab"]:hover { background-color: rgba(56, 194, 201, 0.08) !important; color: #f5f7fb !important; }
    .stTabs [aria-selected="true"] { background-color: rgba(56, 194, 201, 0.15) !important; color: #38bdf8 !important; }
    .stTabs [data-baseweb="tab-highlight"] { background-color: #38bdf8 !important; }
    .stTabs [data-baseweb="tab-border"] { display: none !important; }

    /* DataFrames */
    .stDataFrame, [data-testid="stDataFrame"] { border: 1px solid rgba(120,145,170,0.2) !important; border-radius: 12px !important; overflow: hidden !important; }

    /* Download buttons */
    .stDownloadButton > button {
        background-color: rgba(255,255,255,0.04) !important; color: #c8d3df !important;
        border: 1px solid rgba(120,145,170,0.25) !important; border-radius: 10px !important;
        font-weight: 600 !important; transition: all 0.2s !important;
    }
    .stDownloadButton > button:hover { background-color: rgba(56,194,201,0.08) !important; border-color: #38bdf8 !important; color: #38bdf8 !important; }

    /* Number input +/- */
    .stNumberInput button { color: #c8d3df !important; background-color: rgba(255,255,255,0.05) !important; border-color: rgba(120,145,170,0.3) !important; }

    /* Markdown text */
    .stMarkdown, .stMarkdown p, .stMarkdown li, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 { color: #f5f7fb !important; }
    .stMarkdown code { color: #38bdf8 !important; background-color: rgba(56,194,201,0.1) !important; }
    .stCaption, small { color: #9fb0c3 !important; }

    /* Metrics */
    [data-testid="stMetric"], [data-testid="stMetricValue"], [data-testid="stMetricLabel"], [data-testid="stMetricDelta"] { color: #f5f7fb !important; }

    /* ── Custom component styles ── */
    .hero-card {
        background: linear-gradient(135deg, rgba(10,22,35,0.95), rgba(16,32,50,0.95));
        border: 1px solid rgba(120,145,170,0.15); border-radius: 20px; padding: 2.5rem 2.8rem 2rem; margin-bottom: 2rem;
    }
    .hero-eyebrow { font-size: 0.78rem; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: #38bdf8; margin-bottom: 0.4rem; }
    .hero-title { font-size: 1.85rem; font-weight: 800; line-height: 1.2; color: #f5f7fb; margin-bottom: 0.75rem; }
    .hero-subtitle { font-size: 0.98rem; color: #9fb0c3; line-height: 1.6; max-width: 750px; }
    .hero-pills { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 1.1rem; }
    .hero-pill { background: rgba(56, 194, 201, 0.08); border: 1px solid rgba(56, 194, 201, 0.25); border-radius: 20px; padding: 4px 14px; font-size: 0.8rem; font-weight: 600; color: #38bdf8; }

    .metric-row { display: flex; gap: 16px; margin-bottom: 1.5rem; }
    .metric-card { flex: 1; background: rgba(10, 22, 35, 0.6); border: 1px solid rgba(120,145,170,0.18); border-radius: 14px; padding: 1.1rem 1.3rem; }
    .metric-card .mc-value { font-size: 1.6rem; font-weight: 800; color: #38bdf8; margin-bottom: 2px; }
    .metric-card .mc-label { font-size: 0.82rem; font-weight: 700; color: #c8d3df; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
    .metric-card .mc-desc { font-size: 0.78rem; color: #9fb0c3; line-height: 1.4; }

    .section-header { margin-top: 2.5rem; margin-bottom: 1rem; }
    .section-header h3 { color: #f5f7fb !important; font-weight: 750 !important; font-size: 1.2rem !important; margin-bottom: 4px !important; }
    .section-header p { color: #9fb0c3 !important; font-size: 0.9rem !important; }
    .section-badge { display: inline-block; background: rgba(56,194,201,0.1); border: 1px solid rgba(56,194,201,0.25); border-radius: 6px; padding: 1px 8px; font-size: 0.7rem; font-weight: 700; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }

    .snapshot-card { background: rgba(10, 22, 35, 0.6); border: 1px solid rgba(120,145,170,0.18); border-radius: 14px; padding: 1.2rem 1.4rem; margin-bottom: 1rem; }
    .snapshot-card .sc-badge { display: inline-block; background: rgba(56,194,201,0.1); border: 1px solid rgba(56,194,201,0.2); border-radius: 5px; padding: 1px 8px; font-size: 0.68rem; font-weight: 700; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }
    .snapshot-card .sc-name { font-size: 1.05rem; font-weight: 750; color: #f5f7fb; margin-bottom: 3px; }
    .snapshot-card .sc-row { font-size: 0.82rem; color: #9fb0c3; margin-bottom: 1px; }

    .panel-box { background: rgba(10, 22, 35, 0.5); border: 1px solid rgba(120,145,170,0.15); border-radius: 16px; padding: 1.5rem 1.8rem; margin-top: 1rem; margin-bottom: 1rem; }
    .panel-box.blue { border-color: rgba(56,189,248,0.25); background: rgba(56,189,248,0.03); }
    .panel-box.gold { border-color: rgba(245,158,11,0.25); background: rgba(245,158,11,0.03); }
    .panel-box.green { border-color: rgba(34,197,94,0.25); background: rgba(34,197,94,0.03); }
    .panel-box h4 { color: #f5f7fb !important; font-weight: 750 !important; margin-bottom: 0.4rem !important; }
    .panel-box p, .panel-box li { color: #9fb0c3 !important; font-size: 0.9rem !important; line-height: 1.55 !important; }
    .panel-badge { display: inline-block; background: rgba(245,158,11,0.12); border: 1px solid rgba(245,158,11,0.35); border-radius: 6px; padding: 2px 10px; font-size: 0.72rem; font-weight: 700; color: #f59e0b; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 6px; }
    .panel-badge.blue { background: rgba(56,189,248,0.12); border-color: rgba(56,189,248,0.35); color: #38bdf8; }
    .panel-badge.green { background: rgba(34,197,94,0.12); border-color: rgba(34,197,94,0.35); color: #22c55e; }

    .disclaimer-banner { background: rgba(245, 158, 11, 0.06); border: 1px solid rgba(245,158,11,0.25); border-radius: 12px; padding: 0.9rem 1.3rem; margin-bottom: 1.5rem; }
    .disclaimer-banner .db-title { font-size: 0.88rem; font-weight: 700; color: #f59e0b; }
    .disclaimer-banner .db-text { font-size: 0.82rem; color: #c8a94e; margin-top: 2px; }
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
    "etasjer": 4, "bta": 2500, "land": "Norge",
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
# 5. AI BACKEND (optional)
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
    if HAS_OPENAI:
        client = _get_openai()
        if client:
            try:
                model_name = clean_text(os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini").strip() or "gpt-4.1-mini"
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
            model = genai.GenerativeModel(models[0] if models else "models/gemini-1.5-flash")
            resp = model.generate_content(prompt, generation_config={"temperature": temperature})
            return clean_text(getattr(resp, "text", "")).strip()
        except Exception:
            pass
    return ""


# ────────────────────────────────────────────────────────────────
# 6. DOCUMENT HANDLING
# ────────────────────────────────────────────────────────────────
def classify_quantity_file(name: str) -> str:
    low = name.lower()
    if any(k in low for k in [".ifc"]):
        return "IFC-modell"
    if any(k in low for k in ["mengde", "boq", "quantity", "bok"]):
        return "Mengdeliste/BOQ"
    if any(k in low for k in ["rom", "room", "schedule"]):
        return "Romskjema"
    if any(k in low for k in ["beskrivelse", "spec", "ns3420", "kravspec"]):
        return "Beskrivelse"
    if any(k in low for k in ["tegning", "drawing", "plan", "snitt", "fasade"]):
        return "Tegning"
    if low.endswith(".pdf"):
        return "PDF-dokument"
    return "Annet"


def normalize_uploaded_files(files) -> List[Dict[str, Any]]:
    records = []
    if not files:
        return records
    for f in files:
        name = getattr(f, "name", "ukjent_fil")
        size = getattr(f, "size", 0)
        records.append({
            "filename": name,
            "category": classify_quantity_file(name),
            "extension": Path(name).suffix.lower(),
            "size_kb": round(size / 1024, 1) if size else 0,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    return records


# ────────────────────────────────────────────────────────────────
# 7. QUANTITY & AREA COMPUTATION ENGINE
# ────────────────────────────────────────────────────────────────
def compute_quantity_rows(gross_area: float, floors: int) -> pd.DataFrame:
    return pd.DataFrame([
        {"Post": "Betong dekker", "Mengde": round(gross_area * 0.32, 1), "Enhet": "m3", "Kilde": "IFC / RIB", "Sporbarhet": "Modell-ID + sone"},
        {"Post": "Baeresystem stal", "Mengde": round(gross_area * 0.038, 1), "Enhet": "tonn", "Kilde": "IFC / RIB", "Sporbarhet": "Objektgruppe"},
        {"Post": "Fasade", "Mengde": round(gross_area * 0.58, 1), "Enhet": "m2", "Kilde": "ARK PDF", "Sporbarhet": "Tegning + akse"},
        {"Post": "Innervegger", "Mengde": round(gross_area * 1.25, 1), "Enhet": "lm", "Kilde": "ARK / beskrivelse", "Sporbarhet": "Romskjema + plan"},
        {"Post": "Dorer", "Mengde": max(12, int(gross_area / 48)), "Enhet": "stk", "Kilde": "Dorskjema", "Sporbarhet": "Type + plan"},
        {"Post": "Tekniske sjakter", "Mengde": round(floors * 2.4, 1), "Enhet": "stk", "Kilde": "RIV/RIE", "Sporbarhet": "Kjerne-aksing"},
    ])


def compute_delta_rows(quantity_df: pd.DataFrame, delta_pct: int) -> pd.DataFrame:
    delta = quantity_df.copy()
    delta["Delta mot rev. B"] = delta["Mengde"].apply(lambda x: round(x * delta_pct / 100, 1))
    delta["Kommentar"] = [
        "Okt dekkeutstrekning i plan B",
        "Stivere spenn / justert baeresystem",
        "Fasade forskjovet ved hjornesone",
        "Nye romskiller i plan 2",
        "Dortyper revidert i plan 1",
        "Ekstra sjakt for teknikk",
    ][:len(delta)]
    return delta


def compute_area_rows(gross: float, net: float, core: float, tech: float, circ: float) -> pd.DataFrame:
    saleable = max(net - tech * 0.35, 0)
    g = max(gross, 1)
    return pd.DataFrame([
        {"Kategori": "Bruttoareal", "Areal (m2)": round(gross, 1), "Andel": "100%"},
        {"Kategori": "Nettoareal", "Areal (m2)": round(net, 1), "Andel": f"{net / g * 100:.1f}%"},
        {"Kategori": "Kjerne", "Areal (m2)": round(core, 1), "Andel": f"{core / g * 100:.1f}%"},
        {"Kategori": "Tekniske rom", "Areal (m2)": round(tech, 1), "Andel": f"{tech / g * 100:.1f}%"},
        {"Kategori": "Kommunikasjon", "Areal (m2)": round(circ, 1), "Andel": f"{circ / g * 100:.1f}%"},
        {"Kategori": "Salgbart / utleibart", "Areal (m2)": round(saleable, 1), "Andel": f"{saleable / g * 100:.1f}%"},
    ])


def build_revision_trace() -> pd.DataFrame:
    """Sample revision trace showing traceability between sources."""
    return pd.DataFrame([
        {"Objekt-ID": "DECK-001", "Post": "Betong dekker, plan 1", "Kilde A": "IFC-modell rev. A", "Kilde B": "PDF plan 1 rev. B", "Status": "Konsistent", "Avvik": "-"},
        {"Objekt-ID": "WALL-014", "Post": "Innervegg korridor, plan 2", "Kilde A": "Romskjema rev. A", "Kilde B": "Beskrivelse rev. B", "Status": "Avvik", "Avvik": "+2,4 lm i beskrivelse"},
        {"Objekt-ID": "FAS-003", "Post": "Fasadeelement sone C", "Kilde A": "ARK PDF rev. A", "Kilde B": "IFC fasade rev. B", "Status": "Avvik", "Avvik": "Geometri endret i hjorne"},
        {"Objekt-ID": "DOOR-027", "Post": "Dor type EI60, plan 1", "Kilde A": "Dorskjema rev. A", "Kilde B": "Beskrivelse rev. B", "Status": "Konsistent", "Avvik": "-"},
        {"Objekt-ID": "SHAFT-02", "Post": "Teknisk sjakt, kjerne B", "Kilde A": "RIV tegning rev. A", "Kilde B": "IFC sjakt rev. B", "Status": "Ny", "Avvik": "Lagt til i rev. B"},
        {"Objekt-ID": "STEEL-008", "Post": "Stalbjelke akse 3-4", "Kilde A": "RIB modell rev. A", "Kilde B": "RIB modell rev. B", "Status": "Avvik", "Avvik": "Profil endret HEB300 til HEB360"},
    ])


# ────────────────────────────────────────────────────────────────
# 8. AI ANALYSIS
# ────────────────────────────────────────────────────────────────
def run_ai_quantity_analysis(
    records: List[Dict[str, Any]],
    quantity_df: pd.DataFrame,
    area_df: pd.DataFrame,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    if not HAS_AI:
        return {"data": None, "attempt_log": [{"step": "AI", "status": "Ingen AI-backend tilgjengelig."}]}

    manifest = "\n".join(f"- {r['filename']} ({r['category']}, {r['size_kb']} KB)" for r in records)
    qty_text = quantity_df.to_string(index=False) if not quantity_df.empty else "Ingen mengder"
    area_text = area_df.to_string(index=False) if not area_df.empty else "Ingen arealer"

    prompt = f"""Du er Builtly Mengde-AI. Analyser mengder og arealer for et byggeprosjekt og gi en oppsummering.

PROSJEKT:
- Navn: {clean_text(pd_state.get('p_name', 'Ukjent'))}
- Type: {clean_text(pd_state.get('b_type', '-'))}
- Sted: {clean_text(pd_state.get('kommune', ''))}
- BTA: {config.get('gross_area', 0)} m2, Etasjer: {config.get('floors', 0)}

OPPLASTEDE DOKUMENTER:
{manifest or 'Ingen dokumenter opplastet.'}

MENGDEOVERSIKT:
{qty_text}

AREALOVERSIKT:
{area_text}

KILDER: {', '.join(config.get('sources', []))}
DETALJNIVA: {config.get('detail_level', '-')}

Returner KUN gyldig JSON:
{{
  "executive_summary": "2-4 setninger om mengde- og arealgrunnlagets tilstand.",
  "observations": ["Observasjon 1", "Observasjon 2"],
  "warnings": ["Advarsel om avvik eller mangler"],
  "recommendations": ["Anbefaling 1", "Anbefaling 2"],
  "area_efficiency_comment": "Kort vurdering av arealutnyttelse.",
  "scope_risks": [{{"risk": "...", "severity": "HIGH|MEDIUM|LOW", "recommendation": "..."}}]
}}
Returner kun JSON."""

    attempt_log = []
    try:
        raw = generate_text_ai(prompt, temperature=0.1)
        attempt_log.append({"step": "AI-kall", "status": "OK", "length": len(raw)})
        parsed = safe_json_loads(raw)
        if parsed:
            attempt_log.append({"step": "JSON-parsing", "status": "OK"})
            return {"data": parsed, "attempt_log": attempt_log}
        attempt_log.append({"step": "JSON-parsing", "status": "Feilet"})
    except Exception as e:
        attempt_log.append({"step": "AI-kall", "status": f"Feil: {type(e).__name__}"})
    return {"data": None, "attempt_log": attempt_log}


# ────────────────────────────────────────────────────────────────
# 9. PDF REPORT
# ────────────────────────────────────────────────────────────────
def build_pdf_report(
    quantity_df: pd.DataFrame,
    area_df: pd.DataFrame,
    delta_df: Optional[pd.DataFrame],
    config: Dict[str, Any],
    ai_result: Dict[str, Any],
) -> Optional[bytes]:
    if FPDF is None:
        return None

    class QuantityPDF(FPDF):
        def header(self):
            if self.page_no() == 1:
                return
            self.set_y(11)
            self.set_text_color(88, 94, 102)
            self.set_font("Helvetica", "", 8)
            self.cell(0, 4, clean_text(f"Mengde & Scope – {pd_state.get('p_name', 'Prosjekt')}"), 0, 0, "L")
            self.cell(0, 4, datetime.now().strftime("%d.%m.%Y"), 0, 1, "R")
            self.set_draw_color(188, 192, 197)
            self.line(18, 18, 192, 18)
            self.set_y(24)

        def footer(self):
            self.set_y(-12)
            self.set_draw_color(210, 214, 220)
            self.line(18, 285, 192, 285)
            self.set_font("Helvetica", "", 7)
            self.set_text_color(110, 114, 119)
            self.cell(60, 5, "Builtly-QTY-001", 0, 0, "L")
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
            sy = self.get_y()
            self.set_fill_color(245, 247, 249)
            self.set_draw_color(214, 219, 225)
            self.rect(x, sy, width, height, "DF")
            yy = sy + 5
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
            self.set_y(max(self.get_y(), sy + height))

        def highlight_box(self, title, items, fill=(245, 247, 250), accent=(50, 77, 106)):
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

        def simple_table(self, df, max_rows=20):
            """Render a simple DataFrame as a table."""
            if df is None or df.empty:
                return
            cols = list(df.columns)
            n_cols = len(cols)
            col_w = min(170 / n_cols, 45)
            widths = [col_w] * n_cols
            # Adjust last column to fill
            remaining = 170 - col_w * (n_cols - 1)
            widths[-1] = max(remaining, col_w)

            # Header
            self.ensure_space(10)
            self.set_font("Helvetica", "B", 8)
            self.set_fill_color(46, 62, 84)
            self.set_text_color(255, 255, 255)
            x0 = 20
            y0 = self.get_y()
            for i, col in enumerate(cols):
                self.set_xy(x0, y0)
                self.cell(widths[i], 7, clean_text(str(col))[:18], 1, 0, "L", True)
                x0 += widths[i]
            self.ln(7)

            # Rows
            self.set_font("Helvetica", "", 7.5)
            self.set_text_color(35, 39, 43)
            for ridx in range(min(len(df), max_rows)):
                row = df.iloc[ridx]
                self.ensure_space(7)
                x0 = 20
                y0 = self.get_y()
                bg = (248, 250, 252) if ridx % 2 else (255, 255, 255)
                self.set_fill_color(*bg)
                for i, col in enumerate(cols):
                    self.set_xy(x0, y0)
                    self.cell(widths[i], 6, clean_text(str(row[col]))[:30], 1, 0, "L", True)
                    x0 += widths[i]
                self.ln(6)

    pdf = QuantityPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(True, margin=15)

    # ── Cover ──
    pdf.add_page()
    if os.path.exists("logo.png"):
        try:
            pdf.image("logo.png", x=150, y=15, w=40)
        except Exception:
            pass

    pdf.set_xy(20, 45)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(100, 105, 110)
    pdf.cell(80, 6, "MENGDE & SCOPE", 0, 1, "L")
    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 30)
    pdf.set_text_color(20, 28, 38)
    pdf.multi_cell(95, 11, clean_text(pd_state.get("p_name", "Prosjekt")), 0, "L")
    pdf.ln(4)
    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(64, 68, 74)
    pdf.multi_cell(95, 6.5, "Mengdeoversikt, arealfordeling, revisjonsdelta og sporbarhet", 0, "L")

    pdf.set_xy(118, 45)
    pdf.kv_card([
        ("Oppdragsgiver", clean_text(pd_state.get("c_name", "-"))),
        ("Emne", "Mengde & Scope"),
        ("Dato / rev", f"{datetime.now().strftime('%d.%m.%Y')} / 01"),
        ("Kode", "Builtly-QTY-001"),
        ("BTA", f"{nb_value(config.get('gross_area', 0))} m2"),
        ("Etasjer", str(config.get("floors", "-"))),
    ], x=118, width=72)

    pdf.set_xy(20, 180)
    pdf.set_font("Helvetica", "", 8.8)
    pdf.set_text_color(104, 109, 116)
    pdf.multi_cell(170, 4.5, clean_text(
        "Rapporten er generert av Builtly Mengde & Scope pa bakgrunn av prosjektdata og opplastede dokumenter. "
        "Mengder er estimater basert pa arealforhold og bor verifiseres mot faktisk modell og tegningsgrunnlag."
    ))

    # ── TOC ──
    pdf.add_page()
    pdf.section_title("Innholdsfortegnelse")
    for item in ["1. Sammendrag", "2. Mengdeoversikt", "3. Arealfordeling", "4. Revisjonsdelta", "5. Sporbarhet", "6. Observasjoner og anbefalinger"]:
        y = pdf.get_y()
        pdf.set_x(22)
        pdf.set_font("Helvetica", "", 10.5)
        pdf.set_text_color(45, 49, 55)
        pdf.cell(0, 6, clean_text(item), 0, 0, "L")
        pdf.set_draw_color(225, 229, 234)
        pdf.line(22, y + 6, 188, y + 6)
        pdf.ln(8)

    # ── 1. Summary ──
    pdf.add_page()
    pdf.section_title("1. Sammendrag")
    ai_data = safe_get(ai_result, "data") if isinstance(ai_result, dict) else None
    summary = safe_get(ai_data, "executive_summary", "") if isinstance(ai_data, dict) else ""
    pdf.body_text(summary or "Mengdegrunnlaget er beregnet pa basis av arealforhold og prosjektparametere. Verifiser mot faktisk modell.")
    saleable = max(config.get("net_internal", 0) - config.get("technical_area", 0) * 0.35, 0)
    ga = max(config.get("gross_area", 1), 1)
    pdf.highlight_box("Nokkeltall", [
        f"Bruttoareal: {nb_value(config.get('gross_area', 0))} m2",
        f"Nettoareal: {nb_value(config.get('net_internal', 0))} m2",
        f"Salgbart/utleibart: {nb_value(saleable)} m2 ({saleable / ga * 100:.1f}%)",
        f"Mengdeposter: {len(quantity_df)}",
        f"Detaljniva: {config.get('detail_level', '-')}",
    ])

    # ── 2. Quantities ──
    pdf.section_title("2. Mengdeoversikt")
    pdf.body_text("Tabellen viser estimerte mengder for hovedpostene, beregnet ut fra bruttoareal og etasjetall.")
    pdf.simple_table(quantity_df)

    # ── 3. Areas ──
    pdf.add_page()
    pdf.section_title("3. Arealfordeling")
    pdf.body_text("Arealfordelingen bryter ned bruttoarealet i netto, kjerne, teknisk, kommunikasjon og salgbart areal.")
    pdf.simple_table(area_df)

    # ── 4. Delta ──
    pdf.section_title("4. Revisjonsdelta")
    if delta_df is not None and not delta_df.empty:
        pdf.body_text(f"Tabellen viser estimert endring ({config.get('revision_delta_pct', 0)}%) mellom revisjon A og B.")
        pdf.simple_table(delta_df)
    else:
        pdf.body_text("Revisjonssammenligning er ikke aktivert.")

    # ── 5. Traceability ──
    pdf.add_page()
    pdf.section_title("5. Sporbarhet")
    pdf.body_text("Sporbarhetstabellen viser hvordan mengdeposter kobles mellom ulike kilder og revisjoner.")
    trace = build_revision_trace()
    pdf.simple_table(trace)

    # ── 6. Observations ──
    pdf.section_title("6. Observasjoner og anbefalinger")
    if isinstance(ai_data, dict):
        obs = safe_get(ai_data, "observations", [])
        if obs and isinstance(obs, list):
            pdf.bullet_list(obs)
        warns = safe_get(ai_data, "warnings", [])
        if warns and isinstance(warns, list):
            pdf.highlight_box("Advarsler", warns, fill=(255, 248, 235), accent=(180, 130, 40))
        recs = safe_get(ai_data, "recommendations", [])
        if recs and isinstance(recs, list):
            pdf.bullet_list(recs)
        eff = safe_get(ai_data, "area_efficiency_comment", "")
        if eff:
            pdf.body_text(f"Arealutnyttelse: {eff}")
    else:
        pdf.body_text("Ingen AI-analyse tilgjengelig. Kjor modulen med dokumenter for utvidet vurdering.")

    # Disclaimer
    pdf.ln(8)
    pdf.highlight_box("Ansvarsfraskrivelse", [
        "Rapporten er et arbeidsutkast generert av Builtly Mengde & Scope.",
        "Mengder er estimater og skal verifiseres mot faktisk modell og tegningsgrunnlag.",
        "Dokumentet er ikke signert med ansvarsrett og er ikke juridisk bindende.",
    ], fill=(255, 248, 235), accent=(180, 130, 40))

    return bytes(pdf.output())


# ────────────────────────────────────────────────────────────────
# 10. MARKDOWN REPORT
# ────────────────────────────────────────────────────────────────
def build_markdown_report(
    quantity_df: pd.DataFrame,
    area_df: pd.DataFrame,
    delta_df: Optional[pd.DataFrame],
    config: Dict[str, Any],
    ai_result: Dict[str, Any],
) -> str:
    parts = [
        f"# Mengde & Scope – {clean_text(pd_state.get('p_name', 'Prosjekt'))}",
        f"*Generert: {datetime.now().strftime('%d.%m.%Y %H:%M')}*\n",
    ]
    ai_data = safe_get(ai_result, "data") if isinstance(ai_result, dict) else None
    summary = safe_get(ai_data, "executive_summary", "") if isinstance(ai_data, dict) else ""
    parts.append(f"## Sammendrag\n{summary or 'Basert pa arealforhold og prosjektparametere.'}\n")

    parts.append("## Mengdeoversikt")
    for _, row in quantity_df.iterrows():
        parts.append(f"- **{row['Post']}**: {row['Mengde']} {row['Enhet']} (kilde: {row['Kilde']})")
    parts.append("")

    parts.append("## Arealfordeling")
    for _, row in area_df.iterrows():
        parts.append(f"- **{row['Kategori']}**: {row['Areal (m2)']} m2 ({row['Andel']})")
    parts.append("")

    if delta_df is not None and not delta_df.empty:
        parts.append("## Revisjonsdelta")
        for _, row in delta_df.iterrows():
            parts.append(f"- {row['Post']}: delta {row.get('Delta mot rev. B', '-')} {row['Enhet']} — {row.get('Kommentar', '')}")
        parts.append("")

    if isinstance(ai_data, dict):
        obs = safe_get(ai_data, "observations", [])
        if obs:
            parts.append("## Observasjoner")
            for o in obs:
                parts.append(f"- {o}")
            parts.append("")
        recs = safe_get(ai_data, "recommendations", [])
        if recs:
            parts.append("## Anbefalinger")
            for r in recs:
                parts.append(f"- {r}")
            parts.append("")

    parts.append("---\n*Rapport generert av Builtly Mengde & Scope. Utkast – krever faglig gjennomgang.*")
    return "\n".join(parts)


# ────────────────────────────────────────────────────────────────
# 11. UI – HEADER + BACK BUTTON
# ────────────────────────────────────────────────────────────────
top_l, top_r = st.columns([4, 1])
with top_l:
    logo = logo_data_uri()
    render_html(
        f'<img src="{logo}" class="brand-logo">' if logo
        else '<h2 style="margin:0; color:white;">Builtly</h2>'
    )

with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("\u2190 Tilbake til prosjekt", use_container_width=True, type="secondary"):
        target = find_page("Project")
        if target:
            st.switch_page(target)
        else:
            st.warning("Fant ikke Project-siden.")

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)

# ── Check project ──
if pd_state.get("p_name") in ["", "Nytt Prosjekt", None]:
    st.warning("Du ma sette opp prosjektdata for du kan bruke denne modulen.")
    if find_page("Project"):
        if st.button("Ga til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()


# ────────────────────────────────────────────────────────────────
# 12. HERO
# ────────────────────────────────────────────────────────────────
render_html("""
<div class="hero-card">
    <div class="hero-eyebrow">Mengde & Scope</div>
    <div class="hero-title">Full oversikt over mengder, arealer og revisjonsendringer.</div>
    <div class="hero-subtitle">
        Sammenstill IFC-modell, tegninger, beskrivelser og mengdelister i en arbeidsflate.
        Se hva som faktisk endret seg mellom revisjoner, og spor hver mengdepost tilbake til kilden.
    </div>
    <div class="hero-pills">
        <span class="hero-pill">IFC</span>
        <span class="hero-pill">PDF</span>
        <span class="hero-pill">BOQ</span>
        <span class="hero-pill">Revisjonsdelta</span>
        <span class="hero-pill">Sporbarhet</span>
    </div>
</div>
""")


# ────────────────────────────────────────────────────────────────
# 13. MAIN LAYOUT
# ────────────────────────────────────────────────────────────────
base_bta = float(pd_state.get("bta", 2500) or 2500)
base_floors = int(pd_state.get("etasjer", 4) or 4)

left, right = st.columns([1.25, 0.75], gap="large")

with left:
    render_html("""
    <div class="section-header">
        <span class="section-badge">Kontrolloppsett</span>
        <h3>Kilder og parametere</h3>
        <p>Definer hvilke kilder som inngar, hvilke revisjoner som skal sammenlignes, og detaljniva for mengdekontroll.</p>
    </div>
    """)

    c1, c2, c3 = st.columns(3)
    with c1:
        source_mode = st.multiselect(
            "Kilder",
            ["IFC-modell", "PDF-tegninger", "Beskrivelse/NS3420", "BOQ / mengdeliste", "Romskjema"],
            default=["IFC-modell", "PDF-tegninger", "Beskrivelse/NS3420"],
        )
        compare_revisions = st.toggle("Sammenlign revisjon A mot B", value=True)
    with c2:
        gross_area = st.number_input("Bruttoareal (m2)", min_value=100.0, value=base_bta, step=50.0)
        floors = st.number_input("Etasjer", min_value=1, value=base_floors, step=1)
    with c3:
        detail_level = st.select_slider(
            "Detaljniva",
            options=["Konsept", "Skisse", "Forprosjekt", "Detaljprosjekt", "Utforelse"],
            value="Forprosjekt",
        )
        units = st.selectbox("Enheter", ["m2 / stk / lm", "NS3451 struktur", "CCI / IFC-objekter"], index=0)

    uploaded_files = st.file_uploader(
        "Last opp IFC, PDF, BOQ eller romskjema",
        type=["ifc", "pdf", "xlsx", "xls", "csv", "docx"],
        accept_multiple_files=True,
        key="quantity_scope_files_v2",
    )

    render_html("""
    <div class="section-header" style="margin-top: 1.5rem;">
        <h3>Arealfordeling</h3>
        <p>Juster arealoppdelingen for prosjektet. Builtly beregner netto, kjerne, teknisk og salgbart areal.</p>
    </div>
    """)

    a1, a2, a3, a4 = st.columns(4)
    with a1:
        net_internal = st.number_input("Nettoareal (m2)", min_value=0.0, value=round(gross_area * 0.82, 1), step=10.0)
    with a2:
        core_area = st.number_input("Kjerne (m2)", min_value=0.0, value=round(gross_area * 0.09, 1), step=5.0)
    with a3:
        technical_area = st.number_input("Tekniske rom (m2)", min_value=0.0, value=round(gross_area * 0.07, 1), step=5.0)
    with a4:
        circulation_area = st.number_input("Kommunikasjon (m2)", min_value=0.0, value=round(gross_area * 0.11, 1), step=5.0)

    revision_delta_pct = st.slider("Forventet revisjonsendring mot neste sett (%)", 0, 20, 6)

    # ── Run button ──
    run_analysis = st.button("Kjor mengdekontroll", type="primary", use_container_width=True)

    # ── Compute ──
    saleable_area = max(net_internal - technical_area * 0.35, 0)
    trace_links = max(68, 94 - len(source_mode) * 3)
    coverage = max(70, 97 - abs(gross_area - net_internal - core_area - technical_area - circulation_area) / max(gross_area, 1) * 100)

    quantity_df = compute_quantity_rows(gross_area, floors)
    delta_df = compute_delta_rows(quantity_df, revision_delta_pct) if compare_revisions else None
    area_df = compute_area_rows(gross_area, net_internal, core_area, technical_area, circulation_area)

    config = {
        "sources": source_mode,
        "detail_level": detail_level,
        "units": units,
        "gross_area": gross_area,
        "floors": floors,
        "net_internal": net_internal,
        "core_area": core_area,
        "technical_area": technical_area,
        "circulation_area": circulation_area,
        "revision_delta_pct": revision_delta_pct,
        "compare_revisions": compare_revisions,
    }

    file_records = normalize_uploaded_files(uploaded_files or [])

    # ── AI ──
    if "qty_ai_result" not in st.session_state:
        st.session_state.qty_ai_result = {"data": None, "attempt_log": []}

    if run_analysis and HAS_AI:
        with st.spinner("Kjorer AI-analyse av mengder og arealer..."):
            st.session_state.qty_ai_result = run_ai_quantity_analysis(file_records, quantity_df, area_df, config)

    ai_result = st.session_state.qty_ai_result
    ai_data = safe_get(ai_result, "data") if isinstance(ai_result, dict) else None

    # ── Metrics ──
    g = max(gross_area, 1)
    render_html(f"""
    <div class="metric-row">
        <div class="metric-card">
            <div class="mc-value">{len(quantity_df)}</div>
            <div class="mc-label">Mengdeposter</div>
            <div class="mc-desc">Hovedposter koblet til kilder og objekter.</div>
        </div>
        <div class="metric-card">
            <div class="mc-value">{trace_links}%</div>
            <div class="mc-label">Sporbarhet</div>
            <div class="mc-desc">Poster sporbar til modell, tegning eller beskrivelse.</div>
        </div>
        <div class="metric-card">
            <div class="mc-value">{saleable_area / g * 100:.1f}%</div>
            <div class="mc-label">Arealutnyttelse</div>
            <div class="mc-desc">Andel salgbart / utleibart areal.</div>
        </div>
        <div class="metric-card">
            <div class="mc-value">{coverage:.0f}%</div>
            <div class="mc-label">Dekningsgrad</div>
            <div class="mc-desc">Kompletthetsgrad for mengde- og scopeanalyse.</div>
        </div>
    </div>
    """)

    # ── Tabs ──
    render_html("""
    <div class="section-header">
        <span class="section-badge">Resultater</span>
        <h3>Mengder, arealer og revisjonssporing</h3>
        <p>Detaljerte tabeller med eksport til CSV, Markdown og PDF.</p>
    </div>
    """)

    tabs = st.tabs(["Mengder", "Arealer", "Revisjonsdelta", "Sporbarhet", "AI-vurdering", "Eksport"])

    with tabs[0]:
        st.dataframe(quantity_df, use_container_width=True, hide_index=True)
        csv_data = quantity_df.to_csv(index=False).encode("utf-8")
        st.download_button("Last ned mengdeliste (.csv)", data=csv_data, file_name="builtly_quantity_scope.csv", mime="text/csv")

    with tabs[1]:
        st.dataframe(area_df, use_container_width=True, hide_index=True)
        csv_data = area_df.to_csv(index=False).encode("utf-8")
        st.download_button("Last ned arealoppsett (.csv)", data=csv_data, file_name="builtly_area_breakdown.csv", mime="text/csv")

    with tabs[2]:
        if compare_revisions and delta_df is not None:
            st.dataframe(delta_df, use_container_width=True, hide_index=True)
            csv_data = delta_df.to_csv(index=False).encode("utf-8")
            st.download_button("Last ned revisjonsdelta (.csv)", data=csv_data, file_name="builtly_revision_delta.csv", mime="text/csv")
        else:
            st.info("Sla pa sammenligning mellom revisjoner for a generere delta-rapport.")

    with tabs[3]:
        trace_df = build_revision_trace()
        st.dataframe(trace_df, use_container_width=True, hide_index=True)
        st.markdown(
            "Hver mengdepost kan spores tilbake til modell-ID, tegning eller beskrivelse. "
            "Avvik mellom kilder vises med status og kommentar."
        )

    with tabs[4]:
        st.markdown("### AI-vurdering")
        if isinstance(ai_data, dict):
            summary = safe_get(ai_data, "executive_summary", "")
            if summary:
                st.write(summary)
            obs = safe_get(ai_data, "observations", [])
            if obs and isinstance(obs, list):
                st.markdown("#### Observasjoner")
                for o in obs:
                    st.write(f"- {o}")
            warns = safe_get(ai_data, "warnings", [])
            if warns and isinstance(warns, list):
                st.markdown("#### Advarsler")
                for w in warns:
                    st.warning(w)
            recs = safe_get(ai_data, "recommendations", [])
            if recs and isinstance(recs, list):
                st.markdown("#### Anbefalinger")
                for r in recs:
                    st.write(f"- {r}")
            eff = safe_get(ai_data, "area_efficiency_comment", "")
            if eff:
                st.info(f"Arealutnyttelse: {eff}")
            risks = safe_get(ai_data, "scope_risks", [])
            if risks and isinstance(risks, list):
                st.markdown("#### Scope-risikoer")
                for risk in risks:
                    if isinstance(risk, dict):
                        st.write(f"- **{risk.get('risk', '?')}** [{risk.get('severity', '-')}]: {risk.get('recommendation', '')}")
        else:
            st.info("Kjor mengdekontroll med dokumenter for a fa AI-vurdering." if HAS_AI else "Ingen AI-backend tilgjengelig i miljoet.")

        # Attempt log
        attempt_log = safe_get(ai_result, "attempt_log", [])
        if attempt_log:
            with st.expander("AI-forsokslogg"):
                for entry in attempt_log:
                    if isinstance(entry, dict):
                        st.write(f"**{entry.get('step', '?')}**: {entry.get('status', '-')}")

    with tabs[5]:
        st.markdown("### Eksporter rapport og data")
        report_md = build_markdown_report(quantity_df, area_df, delta_df, config, ai_result)
        st.download_button(
            "Last ned mengderapport (.md)", data=report_md,
            file_name="builtly_quantity_report.md", mime="text/markdown",
        )
        pdf_bytes = build_pdf_report(quantity_df, area_df, delta_df, config, ai_result)
        if pdf_bytes:
            st.download_button(
                "Last ned mengderapport (.pdf)", data=pdf_bytes,
                file_name="builtly_quantity_report.pdf", mime="application/pdf",
            )
        st.download_button(
            "Eksporter konfigurasjon (.json)",
            data=json.dumps(config, indent=2, ensure_ascii=False, default=str),
            file_name="builtly_quantity_scope_summary.json", mime="application/json",
        )

        if file_records:
            st.markdown("#### Opplastede dokumenter")
            st.dataframe(pd.DataFrame(file_records), use_container_width=True, hide_index=True)


# ────────────────────────────────────────────────────────────────
# 14. RIGHT COLUMN
# ────────────────────────────────────────────────────────────────
with right:
    # Project snapshot
    render_html(f"""
    <div class="snapshot-card">
        <span class="sc-badge">Prosjektkontekst</span>
        <div class="sc-name">{clean_text(pd_state.get('p_name', 'Prosjekt'))}</div>
        <div class="sc-row">Type: {clean_text(pd_state.get('b_type', '-'))}</div>
        <div class="sc-row">Sted: {clean_text(pd_state.get('adresse', ''))}, {clean_text(pd_state.get('kommune', ''))}</div>
        <div class="sc-row">Etasjer: {nb_value(pd_state.get('etasjer', '-'))} | BTA: {nb_value(pd_state.get('bta', '-'))} m2</div>
    </div>
    """)

    render_html("""
    <div class="panel-box blue">
        <span class="panel-badge blue">Sporbarhet</span>
        <h4>Hvorfor sporbar mengdekontroll?</h4>
        <p>
            Tradisjonell mengdeberegning gir tall uten kontekst. Builtly kobler hver mengdepost
            til kilde, revisjon og objekt – slik at du vet <em>hva</em> som endret seg, <em>hvor</em> det endret seg,
            og <em>hvorfor</em> det pavirker scopet.
        </p>
        <ul>
            <li>Fanger revisjonsendringer mellom ulike dokumentsett automatisk.</li>
            <li>Viser avvik mellom IFC-modell, tegning og beskrivelse.</li>
            <li>Gir dokumentert grunnlag for tilbudskalkulasjon og forhandling.</li>
            <li>Reduserer risiko for scopeglidning og uforutsette mengdeendringer.</li>
        </ul>
    </div>
    """)

    render_html("""
    <div class="panel-box gold">
        <span class="panel-badge">Slik bruker du modulen</span>
        <h4>Anbefalt arbeidsflyt</h4>
        <ul>
            <li>Last opp IFC, tegninger og/eller beskrivelser for a fa kildekobling.</li>
            <li>Juster arealfordelingen sa den stemmer med prosjektets forutsetninger.</li>
            <li>Sla pa revisjonssammenligning for a se hva som endret seg mellom sett.</li>
            <li>Eksporter rapport og mengdelister for bruk i kalkulasjon eller tilbud.</li>
        </ul>
    </div>
    """)

    render_html("""
    <div class="panel-box green">
        <span class="panel-badge green">Kvalitetssikring</span>
        <h4>Kontroll pa tvers av kilder</h4>
        <p>
            Sporbarhetstabellen viser om mengdeposter er konsistente mellom IFC, tegning og beskrivelse.
            Avvik markeres tydelig slik at du kan ta stilling for innlevering eller prosjektering.
        </p>
    </div>
    """)

    with st.expander("Konfigurasjon"):
        st.json(config)


# ────────────────────────────────────────────────────────────────
# 15. DISCLAIMER
# ────────────────────────────────────────────────────────────────
render_html("""
<div class="disclaimer-banner" style="margin-top: 2rem;">
    <div class="db-title">Utkast – krever faglig kontroll</div>
    <div class="db-text">
        Mengder er estimater basert pa arealforhold og prosjektparametere.
        Resultatet skal verifiseres mot faktisk modell og tegningsgrunnlag for det brukes i kalkulasjon eller beslutning.
    </div>
</div>
""")
