
# -*- coding: utf-8 -*-
import base64
import io
import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

try:
    import google.generativeai as genai
except Exception:
    genai = None

try:
    import fitz
except Exception:
    fitz = None


# ------------------------------------------------------------
# 1. TEKNISK OPPSETT
# ------------------------------------------------------------
st.set_page_config(
    page_title="Konstruksjon (RIB) | Builtly",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DB_DIR = Path("qa_database")
IMG_DIR = DB_DIR / "project_images"
SSOT_FILE = DB_DIR / "ssot.json"
DB_DIR.mkdir(exist_ok=True)
IMG_DIR.mkdir(exist_ok=True)

google_key = os.environ.get("GOOGLE_API_KEY")
if genai is None:
    st.error("Kritisk feil: Python-pakken 'google.generativeai' er ikke tilgjengelig i miljøet.")
    st.stop()

if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()


# ------------------------------------------------------------
# 2. HJELPEFUNKSJONER
# ------------------------------------------------------------
def render_html(html_string: str) -> None:
    st.markdown(html_string.replace("\n", " "), unsafe_allow_html=True)


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


def clean_pdf_text(text: Any) -> str:
    if text is None:
        return ""
    text = str(text)
    replacements = {
        "–": "-",
        "—": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "…": "...",
        "•": "-",
        "≤": "<=",
        "≥": ">=",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def ironclad_text_formatter(text: Any) -> str:
    text = clean_pdf_text(text)
    text = text.replace("$", "").replace("*", "").replace("_", "")
    text = re.sub(r"[-|=]{3,}", " ", text)
    text = re.sub(r"([^\s]{40})", r"\1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def nb_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        if abs(value) >= 100:
            txt = f"{value:.0f}"
        elif abs(value) >= 10:
            txt = f"{value:.1f}"
        else:
            txt = f"{value:.2f}".rstrip("0").rstrip(".")
        return txt.replace(".", ",")
    if isinstance(value, int):
        return str(value)
    return clean_pdf_text(value)


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def normalize_relative_value(value: Any, default: float = 0.5) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if abs(v) > 100:
        v = v / 1000.0
    elif abs(v) > 1.5:
        v = v / 100.0
    return clamp(v, 0.02, 0.98)


def get_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def wrap_text_px(text: str, font, max_width: int) -> List[str]:
    text = clean_pdf_text(text)
    if not text:
        return [""]
    words = text.split()
    if not words:
        return [""]
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.getbbox(candidate)[2] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    out = []
    for line in lines:
        if font.getbbox(line)[2] <= max_width:
            out.append(line)
            continue
        current = ""
        for char in line:
            probe = current + char
            if font.getbbox(probe + "...")[2] <= max_width:
                current = probe
            else:
                break
        out.append((current or line[:10]) + "...")
    return out or [""]


def save_temp_image(img: Image.Image, suffix: str = ".png") -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    img.save(tmp.name)
    tmp.close()
    return tmp.name


def png_bytes_from_image(img: Image.Image) -> bytes:
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()


def safe_session_state_get(key: str, default: Any) -> Any:
    return st.session_state[key] if key in st.session_state else default


def short_text(text: str, max_len: int = 120) -> str:
    text = ironclad_text_formatter(text)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


# ------------------------------------------------------------
# 3. PREMIUM CSS (samme formspråk som Geo.py)
# ------------------------------------------------------------
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
    button[kind="primary"] {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important;
        border: none !important;
        font-weight: 750 !important;
        border-radius: 12px !important;
        padding: 12px 24px !important;
        font-size: 1.05rem !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover {
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
    div[data-testid="InputInstructions"],
    div[data-testid="InputInstructions"] > span {
        color: #9fb0c3 !important;
        -webkit-text-fill-color: #9fb0c3 !important;
    }
    .stTextInput label,
    .stSelectbox label,
    .stNumberInput label,
    .stTextArea label,
    .stFileUploader label {
        color: #c8d3df !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        margin-bottom: 4px !important;
    }
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
    div[data-testid="stExpanderDetails"] > div > div > div {
        background-color: transparent !important;
    }
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
    [data-testid="stAlert"] {
        background-color: rgba(56, 194, 201, 0.05) !important;
        border: 1px solid rgba(56, 194, 201, 0.2) !important;
        border-radius: 12px !important;
    }
    [data-testid="stAlert"] * {
        color: #f5f7fb !important;
    }
</style>
""",
    unsafe_allow_html=True,
)


# ------------------------------------------------------------
# 4. SESSION STATE OG PROSJEKTDATA
# ------------------------------------------------------------
DEFAULT_PROJECT_DATA = {
    "p_name": "",
    "c_name": "",
    "p_desc": "",
    "adresse": "",
    "kommune": "",
    "gnr": "",
    "bnr": "",
    "b_type": "Næring",
    "etasjer": 1,
    "bta": 0,
    "land": "Norge",
}

if "project_data" not in st.session_state or st.session_state.project_data.get("p_name") == "":
    if SSOT_FILE.exists():
        try:
            with open(SSOT_FILE, "r", encoding="utf-8") as f:
                st.session_state.project_data = json.load(f)
        except Exception:
            st.session_state.project_data = DEFAULT_PROJECT_DATA.copy()
    else:
        st.session_state.project_data = DEFAULT_PROJECT_DATA.copy()

if st.session_state.project_data.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_html = (
        f'<img src="{logo_data_uri()}" class="brand-logo">'
        if logo_data_uri()
        else '<h2 style="margin:0; color:white;">Builtly</h2>'
    )
    render_html(f"<div style='margin-bottom:2rem;'>{logo_html}</div>")
    st.warning("⚠️ Handling kreves: Du må sette opp prosjektdata før du kan bruke denne modulen.")
    st.info("RIB-agenten trenger prosjektkontekst for å vurdere bæresystem, sikkerhet og rapportgrunnlag.")
    if find_page("Project"):
        if st.button("⚙️ Gå til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()

pd_state = st.session_state.project_data


# ------------------------------------------------------------
# 5. VISUELL HEADER
# ------------------------------------------------------------
top_l, top_r = st.columns([4, 1])
with top_l:
    logo_html = (
        f'<img src="{logo_data_uri()}" class="brand-logo">'
        if logo_data_uri()
        else '<h2 style="margin:0; color:white;">Builtly</h2>'
    )
    render_html(logo_html)

with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("← Tilbake til SSOT", use_container_width=True, type="secondary"):
        st.switch_page(find_page("Project"))

st.markdown(
    "<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>",
    unsafe_allow_html=True,
)


# ------------------------------------------------------------
# 6. TEGNINGSINNHENTING OG PREPROSESSERING
# ------------------------------------------------------------
SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def copy_rgb(img: Image.Image) -> Image.Image:
    out = img.copy()
    if out.mode != "RGB":
        out = out.convert("RGB")
    return out


def thumbnail_image(img: Image.Image, size: Tuple[int, int] = (1600, 1600)) -> Image.Image:
    out = copy_rgb(img)
    out.thumbnail(size)
    return out


def detect_drawing_hint(name: str) -> str:
    low = clean_pdf_text(name).lower()
    if any(k in low for k in ["plan", "plantegning", "etg", "floor", "plan1", "plan 1"]):
        return "plan"
    if any(k in low for k in ["snitt", "section", "cut"]):
        return "section"
    if any(k in low for k in ["fasade", "facade", "elevation"]):
        return "facade"
    if any(k in low for k in ["detalj", "detail"]):
        return "detail"
    return "unknown"


def drawing_priority(record: Dict[str, Any]) -> int:
    hint = record.get("hint", "unknown")
    score = 0
    if hint == "plan":
        score += 100
    elif hint == "section":
        score += 80
    elif hint == "facade":
        score += 50
    elif hint == "detail":
        score += 20
    name = clean_pdf_text(record.get("name", "")).lower()
    if any(k in name for k in ["1.etg", "1 etg", "plan 1", "plan1", "ground", "u. etg"]):
        score += 10
    if "arkitekt" in name:
        score += 5
    return score


def build_drawing_record(name: str, image: Image.Image, source: str) -> Dict[str, Any]:
    return {
        "name": clean_pdf_text(name),
        "label": clean_pdf_text(Path(name).stem),
        "source": clean_pdf_text(source),
        "hint": detect_drawing_hint(name),
        "image": thumbnail_image(image),
    }


def load_saved_project_drawings() -> List[Dict[str, Any]]:
    drawings: List[Dict[str, Any]] = []

    if IMG_DIR.exists():
        for p in sorted(IMG_DIR.iterdir()):
            if p.suffix.lower() in SUPPORTED_IMAGE_EXTS:
                try:
                    drawings.append(build_drawing_record(p.name, Image.open(p), "Project Setup"))
                except Exception:
                    pass

    project_images = safe_session_state_get("project_images", [])
    if isinstance(project_images, list):
        for idx, img in enumerate(project_images, start=1):
            if isinstance(img, Image.Image):
                drawings.append(build_drawing_record(f"project_image_{idx}.png", img, "Session State"))

    drawings.sort(key=drawing_priority, reverse=True)
    for idx, record in enumerate(drawings):
        record["page_index"] = idx
    return drawings


def load_uploaded_drawings(files: Optional[List[Any]], max_pdf_pages: int = 4) -> List[Dict[str, Any]]:
    drawings: List[Dict[str, Any]] = []
    if not files:
        return drawings

    for f in files:
        try:
            f.seek(0)
        except Exception:
            pass

        name = clean_pdf_text(getattr(f, "name", "ukjent_fil"))
        low = name.lower()

        if low.endswith(".pdf"):
            if fitz is None:
                continue
            try:
                pdf_bytes = f.read()
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                for page_num in range(min(max_pdf_pages, len(doc))):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
                    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                    drawings.append(build_drawing_record(f"{name} - side {page_num + 1}", img, "Opplastet PDF"))
                doc.close()
            except Exception:
                continue
        elif Path(low).suffix.lower() in SUPPORTED_IMAGE_EXTS:
            try:
                img = Image.open(f).convert("RGB")
                drawings.append(build_drawing_record(name, img, "Opplastet bilde"))
            except Exception:
                continue

    drawings.sort(key=drawing_priority, reverse=True)
    for idx, record in enumerate(drawings):
        record["page_index"] = idx
    return drawings


def prioritize_drawings(drawings: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    out = sorted(drawings, key=drawing_priority, reverse=True)[:limit]
    for idx, record in enumerate(out):
        record["page_index"] = idx
    return out


def add_analysis_badge(img: Image.Image, idx: int, label: str) -> Image.Image:
    out = copy_rgb(img)
    draw = ImageDraw.Draw(out, "RGBA")
    w, h = out.size
    pad = max(18, int(min(w, h) * 0.02))
    badge_w = int(w * 0.34)
    badge_h = int(h * 0.09)
    font_big = get_font(max(18, int(badge_h * 0.32)), bold=True)
    font_small = get_font(max(14, int(badge_h * 0.22)), bold=False)

    draw.rounded_rectangle(
        (pad, pad, pad + badge_w, pad + badge_h),
        radius=18,
        fill=(6, 17, 26, 210),
        outline=(56, 194, 201, 255),
        width=3,
    )
    draw.text((pad + 16, pad + 10), f"Tegning {idx}", font=font_big, fill=(255, 255, 255, 255))
    wrapped = wrap_text_px(short_text(label, 34), font_small, badge_w - 28)
    y = pad + 10 + font_big.getbbox("Ag")[3] + 8
    for line in wrapped[:2]:
        draw.text((pad + 16, y), line, font=font_small, fill=(200, 211, 223, 255))
        y += font_small.getbbox("Ag")[3] + 3
    return out


def prepare_ai_images(drawings: List[Dict[str, Any]]) -> List[Image.Image]:
    ai_images = []
    for record in drawings:
        ai_images.append(add_analysis_badge(record["image"], record["page_index"], record["label"]))
    return ai_images


def drawing_manifest_text(drawings: List[Dict[str, Any]]) -> str:
    if not drawings:
        return "- Ingen tegninger er tilgjengelige."
    lines = []
    for record in drawings:
        lines.append(
            f"- side_index {record['page_index']}: {record['name']} | kilde: {record['source']} | hint: {record['hint']}"
        )
    return "\n".join(lines)


# ------------------------------------------------------------
# 7. RASJONALITETSMOTOR FOR BÆRESYSTEM
# ------------------------------------------------------------
def building_context_text(project_data: Dict[str, Any]) -> str:
    parts = [
        clean_pdf_text(project_data.get("b_type", "")),
        clean_pdf_text(project_data.get("p_desc", "")),
        clean_pdf_text(project_data.get("p_name", "")),
    ]
    return " ".join(parts).lower()


def build_structural_system_candidates(
    project_data: Dict[str, Any],
    material_preference: str,
    optimization_mode: str,
    foundation_preference: str,
) -> List[Dict[str, Any]]:
    desc = building_context_text(project_data)
    floors = int(project_data.get("etasjer") or 1)
    area = float(project_data.get("bta") or 0)

    systems: List[Dict[str, Any]] = [
        {
            "system_name": "Massivtre med limtre/CLT og lokal betongkjerne",
            "material": "Massivtre / hybrid",
            "typical_span": "ca 4,5-7,5 m",
            "best_for": "Bolig, skole, mindre kontor, påbygg og prosjekter med vektfokus",
            "stability": "CLT-skiver kombinert med betong- eller stålkjerne",
            "watchouts": "Vibrasjoner, lyd, brannstrategi og tunge transfer-soner må kontrolleres nøye",
            "max_floors_soft": 6,
            "long_span_capability": 1,
            "weight": "Lav",
        },
        {
            "system_name": "Stålrammer med hulldekker",
            "material": "Stål + prefabrikkert dekke",
            "typical_span": "ca 7,5-12 m",
            "best_for": "Kontor, næring, parkering, handel og arealer med behov for fleksible plan",
            "stability": "Stålkryss/betongkjerne og stive dekker som horisontal skive",
            "watchouts": "Brannbeskyttelse, knutepunkter og vibrasjon må prosjekteres presist",
            "max_floors_soft": 8,
            "long_span_capability": 3,
            "weight": "Middels",
        },
        {
            "system_name": "Plasstøpt betong med flatdekker og sjakter/kjerner",
            "material": "Plasstøpt betong",
            "typical_span": "ca 6-9 m",
            "best_for": "Komplekse bygg, kjellere, høye bygg og uregelmessig geometri",
            "stability": "Betongkjerner, skiver og robuste veggskiver",
            "watchouts": "Tørketid, egenvekt og fremdrift påvirkes mer enn ved prefabrikkerte systemer",
            "max_floors_soft": 20,
            "long_span_capability": 2,
            "weight": "Høy",
        },
        {
            "system_name": "Prefabrikkert betong med repeterbart søyle-bjelkesystem",
            "material": "Prefabrikkert betong",
            "typical_span": "ca 7-10 m",
            "best_for": "Repeterbare boligbygg, hotell, lager og større volum med få varianter",
            "stability": "Betongkjerne, prefabrikkerte veggskiver og stive dekker",
            "watchouts": "Transport, montasjeplan og repetisjon i geometri er avgjørende",
            "max_floors_soft": 10,
            "long_span_capability": 2,
            "weight": "Høy",
        },
        {
            "system_name": "Hybrid: betongkjerne med stål- eller trekonstruksjon over",
            "material": "Hybrid / kombinasjon",
            "typical_span": "ca 6-10 m",
            "best_for": "Prosjekter som trenger både robusthet, fleksibilitet og lavere vekt i overbygg",
            "stability": "Betongkjerner for avstivning, lettere dekker og søylesystem i overbygg",
            "watchouts": "Grensesnitt, toleranser og lastoverføring mellom materialer må tydelig defineres",
            "max_floors_soft": 14,
            "long_span_capability": 3,
            "weight": "Middels",
        },
    ]

    def matches_any(keywords: List[str]) -> bool:
        return any(k in desc for k in keywords)

    for system in systems:
        rationality = 58.0
        robustness = 60.0
        notes: List[str] = []

        # Materialpreferanse
        pref_low = material_preference.lower()
        if "massivtre" in pref_low and "massivtre" in system["material"].lower():
            rationality += 16
            notes.append("Matcher valgt materialstrategi.")
        if "stål" in pref_low and "stål" in system["material"].lower():
            rationality += 16
            notes.append("Matcher ønsket stålbasert hovedsystem.")
        if "plasstøpt" in pref_low and "plasstøpt" in system["material"].lower():
            rationality += 16
            notes.append("Matcher valgt plasstøpt system.")
        if "prefabrikkert" in pref_low and "prefabrikkert" in system["material"].lower():
            rationality += 16
            notes.append("Matcher valgt prefabrikkert system.")
        if "hybrid" in pref_low and "hybrid" in system["material"].lower():
            rationality += 16
            notes.append("Matcher valgt hybridstrategi.")

        # Bygningstype og nøkkelord
        if matches_any(["kontor", "næring", "handel", "butikk", "retail", "parkering"]):
            if "stål" in system["material"].lower():
                rationality += 12
            if "prefabrikkert betong" in system["material"].lower():
                rationality += 8
        if matches_any(["bolig", "leilighet", "student", "hotel", "hotell"]):
            if "massivtre" in system["material"].lower():
                rationality += 10
            if "prefabrikkert betong" in system["material"].lower():
                rationality += 12
        if matches_any(["skole", "barnehage"]):
            if "massivtre" in system["material"].lower():
                rationality += 10
            if "hybrid" in system["material"].lower():
                rationality += 8
        if matches_any(["rehabilitering", "transformasjon", "påbygg", "eksisterende"]):
            if system["weight"] == "Lav":
                rationality += 10
                robustness += 4
            if "hybrid" in system["material"].lower():
                rationality += 8

        # Størrelse og etasjer
        if floors > system["max_floors_soft"]:
            rationality -= min(24, (floors - system["max_floors_soft"]) * 4)
            notes.append("Får trekk fordi bygget virker høyere enn systemet normalt er mest rasjonelt for.")
        else:
            robustness += 4

        if floors >= 7:
            if "betong" in system["material"].lower() or "hybrid" in system["material"].lower():
                robustness += 12
        if floors <= 3 and system["weight"] == "Lav":
            rationality += 6

        if area >= 4000:
            if system["long_span_capability"] >= 2:
                rationality += 8
            if "prefabrikkert" in system["material"].lower():
                rationality += 6
        elif area <= 1500 and "massivtre" in system["material"].lower():
            rationality += 6

        # Optimaliseringsmodus
        opt_low = optimization_mode.lower()
        if "rasjonalitet" in opt_low or "repeterbarhet" in opt_low:
            if "prefabrikkert" in system["material"].lower() or "stål" in system["material"].lower():
                rationality += 10
        if "lav egenvekt" in opt_low or "påbygg" in opt_low:
            if system["weight"] == "Lav":
                rationality += 14
                robustness += 4
            if system["weight"] == "Høy":
                rationality -= 8
        if "store spenn" in opt_low or "fleksibilitet" in opt_low:
            rationality += float(system["long_span_capability"] * 4)
        if "robusthet" in opt_low or "stivhet" in opt_low:
            if "betong" in system["material"].lower() or "hybrid" in system["material"].lower():
                robustness += 12
        if "lav karbon" in opt_low or "treandel" in opt_low:
            if "massivtre" in system["material"].lower() or "hybrid" in system["material"].lower():
                rationality += 10

        # Fundamentering
        f_low = foundation_preference.lower()
        if "peling" in f_low and system["weight"] == "Høy":
            rationality -= 4
            notes.append("Noe trekk fordi tungt system kan gi høyere pelingsomfang.")
        if ("fjell" in f_low or "direkte" in f_low) and "betong" in system["material"].lower():
            robustness += 4

        system["rationality_score"] = int(clamp(round(rationality), 25, 99))
        system["robustness_score"] = int(clamp(round(robustness), 25, 99))
        system["total_score"] = int(
            clamp(round(system["rationality_score"] * 0.7 + system["robustness_score"] * 0.3), 25, 99)
        )
        system["selection_notes"] = notes

    systems.sort(key=lambda x: (x["total_score"], x["rationality_score"], x["robustness_score"]), reverse=True)
    for idx, system in enumerate(systems, start=1):
        system["priority"] = idx
        system["recommended"] = idx == 1
    return systems


def build_candidate_dataframe(candidates: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for system in candidates:
        rows.append(
            {
                "Prioritet": system.get("priority"),
                "Anbefalt": "JA" if system.get("recommended") else "",
                "System": system.get("system_name"),
                "Materiale": system.get("material"),
                "Typisk spenn": system.get("typical_span"),
                "Stabilitetsprinsipp": system.get("stability"),
                "Rasjonalitet": system.get("rationality_score"),
                "Robusthet": system.get("robustness_score"),
                "Total": system.get("total_score"),
                "Typisk bruk": system.get("best_for"),
                "Forbehold": system.get("watchouts"),
            }
        )
    return pd.DataFrame(rows)


def candidate_matrix_text(candidates: List[Dict[str, Any]], limit: int = 5) -> str:
    lines = []
    for system in candidates[:limit]:
        lines.append(
            f"- Prioritet {system['priority']}: {system['system_name']} | materiale: {system['material']} | "
            f"typisk spenn: {system['typical_span']} | rasjonalitet: {system['rationality_score']} | "
            f"robusthet: {system['robustness_score']} | total: {system['total_score']} | "
            f"typisk bruk: {system['best_for']} | forbehold: {system['watchouts']}"
        )
    return "\n".join(lines)


# ------------------------------------------------------------
# 8. AI-MOTOR: STRUKTURERT ANALYSE OG RAPPORT
# ------------------------------------------------------------
def list_available_models() -> List[str]:
    try:
        return [m.name for m in genai.list_models() if "generateContent" in getattr(m, "supported_generation_methods", [])]
    except Exception:
        return []


def pick_model(valid_models: List[str]) -> Optional[str]:
    for fav in ["models/gemini-1.5-pro", "models/gemini-1.5-flash", "models/gemini-pro-vision"]:
        if fav in valid_models:
            return fav
    return valid_models[0] if valid_models else None


def generate_text(model, parts: List[Any], temperature: float = 0.2) -> str:
    try:
        response = model.generate_content(parts, generation_config={"temperature": temperature})
    except Exception:
        response = model.generate_content(parts)
    return clean_pdf_text(getattr(response, "text", "")).strip()


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
        cleaned = cleaned[first : last + 1]
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
            repaired = re.sub(r"\n\s*\n", "\n", repaired)
            repaired = re.sub(r"(?<!\\)'", '"', repaired)
            repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
            return json.loads(repaired)
        except Exception:
            return None


def default_analysis_result(candidates: List[Dict[str, Any]], drawings: List[Dict[str, Any]]) -> Dict[str, Any]:
    top = candidates[0] if candidates else {}
    return {
        "grunnlag_status": "DELVIS" if drawings else "FOR_SVAKT",
        "grunnlag_begrunnelse": "Maskinell strukturert analyse kunne ikke tolkes fullt ut. Resultatet er derfor bygget på heuristisk konseptvalg.",
        "observasjoner": [],
        "mangler": ["Mangler strukturert maskinell tegningstolkning."],
        "drawings": [
            {
                "page_index": record.get("page_index"),
                "page_label": record.get("label"),
                "drawing_role": record.get("hint", "unknown"),
                "usable_for_overlay": record.get("hint") in {"plan", "section"},
                "observations": [],
            }
            for record in drawings
        ],
        "recommended_system": {
            "system_name": top.get("system_name", "Ikke fastlagt"),
            "material": top.get("material", "-"),
            "deck_type": top.get("typical_span", "-"),
            "vertical_system": "Søyle-/veggsystem må bekreftes mot plantegning",
            "stability_system": top.get("stability", "-"),
            "foundation_strategy": "Må verifiseres mot geoteknikk",
            "typical_span_m": top.get("typical_span", "-"),
            "rationality_reason": "Valgt ut fra maskinell alternativstudie og prosjektkontekst.",
            "safety_reason": "Konseptet søker korte lastveier og tydelig avstivning.",
            "buildability_notes": ["Konseptet må avstemmes mot arkitekttegninger og geotekniske data."],
            "load_path": ["Laster føres via dekker til primærsystem og videre til fundament."],
        },
        "alternatives": [
            {
                "system_name": c.get("system_name"),
                "why": c.get("best_for"),
                "when_better": c.get("watchouts"),
                "rationality_score": c.get("rationality_score"),
                "safety_score": c.get("robustness_score"),
            }
            for c in candidates[:3]
        ],
        "sketches": [],
        "risk_register": [],
        "load_assumptions": [
            "Eksakte snø-, vind- og nyttelaster må fastsettes prosjektspesifikt mot lokasjon, kategori og geometri."
        ],
        "foundation_assumptions": ["Fundamenteringsprinsipp må bekreftes av geoteknikk."],
        "next_steps": [
            "Innhent plan med mål, snitt, akser og nivåer.",
            "Lås valgt bæresystem sammen med arkitekt før detaljering.",
        ],
    }


def normalize_analysis_result(
    analysis: Optional[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    drawings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(analysis, dict):
        analysis = default_analysis_result(candidates, drawings)

    base = default_analysis_result(candidates, drawings)
    merged = base.copy()
    merged.update(analysis)

    recommended = merged.get("recommended_system") or {}
    if not isinstance(recommended, dict):
        recommended = {}
    base_recommended = base["recommended_system"].copy()
    base_recommended.update(recommended)
    merged["recommended_system"] = base_recommended

    for key in ["observasjoner", "mangler", "load_assumptions", "foundation_assumptions", "next_steps"]:
        if not isinstance(merged.get(key), list):
            merged[key] = base.get(key, [])

    if not isinstance(merged.get("drawings"), list):
        merged["drawings"] = base["drawings"]

    if not isinstance(merged.get("alternatives"), list) or not merged["alternatives"]:
        merged["alternatives"] = base["alternatives"]

    valid_page_indexes = {record["page_index"] for record in drawings}
    sketches = merged.get("sketches", [])
    if not isinstance(sketches, list):
        sketches = []

    normalized_sketches = []
    for sketch in sketches:
        if not isinstance(sketch, dict):
            continue
        page_index = sketch.get("page_index")
        try:
            page_index = int(page_index)
        except Exception:
            continue
        if page_index not in valid_page_indexes:
            continue
        elements = sketch.get("elements", [])
        if not isinstance(elements, list):
            elements = []
        notes = sketch.get("notes", [])
        if not isinstance(notes, list):
            notes = []
        normalized_sketches.append(
            {
                "page_index": page_index,
                "page_label": clean_pdf_text(sketch.get("page_label") or f"Tegning {page_index}"),
                "notes": [clean_pdf_text(x) for x in notes if clean_pdf_text(x)],
                "elements": [e for e in elements if isinstance(e, dict)],
            }
        )
    merged["sketches"] = normalized_sketches

    risks = merged.get("risk_register", [])
    if not isinstance(risks, list):
        risks = []
    normalized_risks = []
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        normalized_risks.append(
            {
                "topic": clean_pdf_text(risk.get("topic") or "Ikke navngitt risiko"),
                "severity": clean_pdf_text(risk.get("severity") or "Middels"),
                "mitigation": clean_pdf_text(risk.get("mitigation") or "-"),
            }
        )
    merged["risk_register"] = normalized_risks

    status = clean_pdf_text(merged.get("grunnlag_status") or base["grunnlag_status"]).upper()
    if status not in {"FULLSTENDIG", "DELVIS", "FOR_SVAKT"}:
        status = "DELVIS" if drawings else "FOR_SVAKT"
    merged["grunnlag_status"] = status
    merged["grunnlag_begrunnelse"] = clean_pdf_text(
        merged.get("grunnlag_begrunnelse") or base["grunnlag_begrunnelse"]
    )

    return merged


def run_structured_drawing_analysis(
    model,
    drawings: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    project_data: Dict[str, Any],
    material_preference: str,
    foundation_preference: str,
    optimization_mode: str,
    safety_mode: str,
) -> Dict[str, Any]:
    ai_images = prepare_ai_images(drawings)
    manifest = drawing_manifest_text(drawings)
    matrix_txt = candidate_matrix_text(candidates)

    prompt = f"""
Du er Builtly RIB AI, en senior rådgivende ingeniør bygg med fokus på bæresystem, stabilitet, rasjonalitet og sikkerhet.

Du skal analysere arkitekttegninger og returnere KUN gyldig JSON. Ingen markdown. Ingen forklaringer utenfor JSON.

PROSJEKT:
- Navn: {clean_pdf_text(project_data.get('p_name'))}
- Type: {clean_pdf_text(project_data.get('b_type'))}
- BTA: {nb_value(project_data.get('bta'))} m2
- Etasjer: {nb_value(project_data.get('etasjer'))}
- Sted: {clean_pdf_text(project_data.get('adresse'))}, {clean_pdf_text(project_data.get('kommune'))}
- Beskrivelse: {clean_pdf_text(project_data.get('p_desc'))}
- Regelverk: {clean_pdf_text(project_data.get('land', 'Norge'))}

BRUKERENS FØRINGER:
- Foretrukket materiale: {clean_pdf_text(material_preference)}
- Fundamentering: {clean_pdf_text(foundation_preference)}
- Optimaliser for: {clean_pdf_text(optimization_mode)}
- Sikkerhetsmodus: {clean_pdf_text(safety_mode)}

MASKINELL ALTERNATIVMATRISEN SOM STARTPUNKT:
{matrix_txt}

TEGNINGSMANIFEST I SAMME REKKEFØLGE SOM BILDENE SENDES:
{manifest}

VIKTIGE REGLER:
1. Prioriter rasjonelle og repeterbare systemer med korte lastveier.
2. Ikke finn på eksakte kapasitetstall, dimensjoner eller grunnforhold.
3. Skisser bare der tegningen faktisk gjør det mulig.
4. Hvis grunnlaget er for svakt, skal sketches være tom liste.
5. Når du skisserer på plan, bruk punkter for søyler og tydelige kjerner/skiver.
6. Koordinater skal være normaliserte mellom 0 og 1.
7. Maks 3 sketch-sider.
8. Hvis sikkerhetsmodus er konservativ, velg heller mer robust og tydelig avstivning enn aggressiv optimalisering.
9. Du skal tydelig vurdere både rasjonalitet og sikkerhet.
10. Du må være ærlig om mangler og usikkerhet.

JSON-SKJEMA:
{{
  "grunnlag_status": "FULLSTENDIG | DELVIS | FOR_SVAKT",
  "grunnlag_begrunnelse": "kort tekst",
  "observasjoner": ["..."],
  "mangler": ["..."],
  "drawings": [
    {{
      "page_index": 0,
      "page_label": "kort navn",
      "drawing_role": "plan | section | facade | detail | unknown",
      "usable_for_overlay": true,
      "observations": ["..."]
    }}
  ],
  "recommended_system": {{
    "system_name": "navn på anbefalt system",
    "material": "materiale",
    "deck_type": "dekke/hovedspenn-prinsipp",
    "vertical_system": "søyle/vegg/bjelke-prinsipp",
    "stability_system": "kjerne/skive/avstivningsprinsipp",
    "foundation_strategy": "overordnet strategi",
    "typical_span_m": "tekstlig spennvurdering",
    "rationality_reason": "hvorfor dette er mest rasjonelt",
    "safety_reason": "hvorfor dette er robust og sikkert",
    "buildability_notes": ["..."],
    "load_path": ["..."]
  }},
  "alternatives": [
    {{
      "system_name": "alternativ",
      "why": "hvorfor alternativet kan fungere",
      "when_better": "når alternativet er bedre",
      "rationality_score": 0,
      "safety_score": 0
    }}
  ],
  "sketches": [
    {{
      "page_index": 0,
      "page_label": "1. etg plan",
      "notes": ["kort note om valg av system, lastvei eller risiko"],
      "elements": [
        {{"type": "column", "x": 0.1, "y": 0.2, "label": "C1"}},
        {{"type": "core", "x": 0.45, "y": 0.35, "w": 0.14, "h": 0.18, "label": "K1"}},
        {{"type": "wall", "x1": 0.2, "y1": 0.2, "x2": 0.2, "y2": 0.7, "label": "Skive"}},
        {{"type": "beam", "x1": 0.1, "y1": 0.2, "x2": 0.75, "y2": 0.2, "label": "Primærdrager"}},
        {{"type": "grid", "orientation": "vertical", "x": 0.1, "label": "A"}},
        {{"type": "grid", "orientation": "horizontal", "y": 0.2, "label": "1"}},
        {{"type": "span_arrow", "x1": 0.1, "y1": 0.78, "x2": 0.4, "y2": 0.78, "label": "ca 7,2 m"}}
      ]
    }}
  ],
  "risk_register": [
    {{"topic": "kort risiko", "severity": "Lav | Middels | Høy", "mitigation": "tiltak"}}
  ],
  "load_assumptions": ["..."],
  "foundation_assumptions": ["..."],
  "next_steps": ["..."]
}}

Returner kun JSON.
""".strip()

    raw_text = generate_text(model, [prompt] + ai_images, temperature=0.15)
    parsed = safe_json_loads(raw_text)
    return normalize_analysis_result(parsed, candidates, drawings)


def build_fallback_report(analysis_result: Dict[str, Any], project_data: Dict[str, Any]) -> str:
    rec = analysis_result.get("recommended_system", {})
    alt_lines = []
    for alt in analysis_result.get("alternatives", [])[:3]:
        alt_lines.append(
            f"- {alt.get('system_name', '-')}: {alt.get('why', '-')}. Når bedre: {alt.get('when_better', '-')}. "
            f"Rasjonalitet {alt.get('rationality_score', '-')}, sikkerhet {alt.get('safety_score', '-')}"
        )
    risk_lines = []
    for risk in analysis_result.get("risk_register", [])[:6]:
        risk_lines.append(f"- {risk.get('topic', '-')}: {risk.get('mitigation', '-')} ({risk.get('severity', '-')})")

    observation_lines = [f"- {x}" for x in analysis_result.get("observasjoner", [])[:8]] or ["- Ingen maskinelle observasjoner tilgjengelig."]
    missing_lines = [f"- {x}" for x in analysis_result.get("mangler", [])[:8]] or ["- Ingen spesifikke mangler registrert."]
    load_lines = [f"- {x}" for x in analysis_result.get("load_assumptions", [])[:8]] or ["- Laster må avklares i videre prosjektering."]
    foundation_lines = [f"- {x}" for x in analysis_result.get("foundation_assumptions", [])[:8]] or ["- Fundamentering må bekreftes."]
    buildability = [f"- {x}" for x in rec.get("buildability_notes", [])[:8]] or ["- Byggbarhet må avstemmes mot tegningene."]
    load_path = [f"- {x}" for x in rec.get("load_path", [])[:8]] or ["- Lastvei må dokumenteres tydelig i videre prosjektering."]
    next_steps = [f"- {x}" for x in analysis_result.get("next_steps", [])[:10]] or ["- Innhent mer tegningsunderlag."]

    return f"""
# 1. SAMMENDRAG OG KONKLUSJON
Datagrunnlaget er vurdert som **{analysis_result.get('grunnlag_status', 'DELVIS')}**.
Anbefalt konsept er **{rec.get('system_name', 'Ikke fastlagt')}**.
Begrunnelse: {analysis_result.get('grunnlag_begrunnelse', '-')}

# 2. VURDERING AV DATAGRUNNLAG
## Observerte forhold
{'\n'.join(observation_lines)}

## Mangelpunkter
{'\n'.join(missing_lines)}

# 3. LASTER OG FORUTSETNINGER
{'\n'.join(load_lines)}

# 4. KONSEPT FOR BÆRESYSTEM OG STABILITET
## Anbefalt system
- Materiale: {rec.get('material', '-')}
- Dekke / spenn: {rec.get('deck_type', rec.get('typical_span_m', '-'))}
- Vertikalsystem: {rec.get('vertical_system', '-')}
- Stabilitet: {rec.get('stability_system', '-')}
- Rasjonalitet: {rec.get('rationality_reason', '-')}
- Sikkerhet: {rec.get('safety_reason', '-')}

## Lastvei og byggbarhet
{'\n'.join(load_path)}
{'\n'.join(buildability)}

# 5. RASJONALITET, BYGGBARHET OG ALTERNATIVE SYSTEMER
{'\n'.join(alt_lines) if alt_lines else "- Ingen alternativer registrert."}

# 6. FUNDAMENTERING OG EKSISTERENDE KONSTRUKSJONER
{'\n'.join(foundation_lines)}

# 7. RISIKO, SÅRBARHET OG NESTE STEG
## Risiko
{'\n'.join(risk_lines) if risk_lines else "- Ingen eksplisitt maskinell risikoliste tilgjengelig."}

## Neste steg
{'\n'.join(next_steps)}
""".strip()


def run_report_writer(
    model,
    analysis_result: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    project_data: Dict[str, Any],
    material_preference: str,
    foundation_preference: str,
    optimization_mode: str,
) -> str:
    analysis_json = json.dumps(analysis_result, ensure_ascii=False, indent=2)
    matrix_txt = candidate_matrix_text(candidates)

    prompt = f"""
Du er Builtly RIB AI og skal skrive et stramt konseptnotat for bæresystem basert KUN på strukturerte analyseresultater nedenfor.

PROSJEKT:
- Navn: {clean_pdf_text(project_data.get('p_name'))}
- Type: {clean_pdf_text(project_data.get('b_type'))}
- BTA: {nb_value(project_data.get('bta'))} m2
- Etasjer: {nb_value(project_data.get('etasjer'))}
- Lokasjon: {clean_pdf_text(project_data.get('adresse'))}, {clean_pdf_text(project_data.get('kommune'))}
- Prosjektbeskrivelse: {clean_pdf_text(project_data.get('p_desc'))}
- Materialpreferanse: {clean_pdf_text(material_preference)}
- Fundamentpreferanse: {clean_pdf_text(foundation_preference)}
- Optimaliseringsmodus: {clean_pdf_text(optimization_mode)}

STRUKTURERT ANALYSE:
{analysis_json}

MASKINELL ALTERNATIVMATRISEN:
{matrix_txt}

REGLER:
- Ikke finn på tall eller kapasiteter.
- Vær tydelig på hva som er dokumentert, antatt og mangler.
- Skriv som RIB: konkret, kortfattet og profesjonelt.
- Beskriv lastveier, avstivning, grid-logikk og byggbarhet der grunnlaget tillater det.
- Når grunnlaget er svakt, vær streng og presis.
- Ikke bruk markdown-tabeller.
- Bruk gjerne underoverskrifter og punktlister.
- Henvis til at konseptskisser er maskinelle konseptskisser, ikke arbeidstegninger.

BRUK KUN DISSE OVERSKRIFTENE:
# 1. SAMMENDRAG OG KONKLUSJON
# 2. VURDERING AV DATAGRUNNLAG
# 3. LASTER OG FORUTSETNINGER
# 4. KONSEPT FOR BÆRESYSTEM OG STABILITET
# 5. RASJONALITET, BYGGBARHET OG ALTERNATIVE SYSTEMER
# 6. FUNDAMENTERING OG EKSISTERENDE KONSTRUKSJONER
# 7. RISIKO, SÅRBARHET OG NESTE STEG

Svar kun med rapporttekst.
""".strip()

    try:
        report_text = generate_text(model, [prompt], temperature=0.2)
        if report_text.strip():
            return report_text.strip()
    except Exception:
        pass
    return build_fallback_report(analysis_result, project_data)


# ------------------------------------------------------------
# 9. MASKINELLE KONSEPTSKISSER OPPÅ TEGNING
# ------------------------------------------------------------
OVERLAY_COLORS = {
    "column": (56, 194, 201, 255),
    "beam": (120, 220, 225, 255),
    "grid": (125, 140, 160, 220),
    "core_fill": (255, 196, 64, 75),
    "core_stroke": (255, 196, 64, 255),
    "wall": (255, 153, 153, 255),
    "span": (196, 235, 176, 255),
    "text": (245, 247, 251, 255),
    "dark": (6, 17, 26, 220),
    "white": (255, 255, 255, 255),
}


def draw_arrow(draw: ImageDraw.ImageDraw, start: Tuple[int, int], end: Tuple[int, int], fill, width: int = 4) -> None:
    draw.line([start, end], fill=fill, width=width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = max(10, width * 3)
    for sign in (-1, 1):
        a = angle + sign * math.pi / 7
        p = (
            int(end[0] - math.cos(a) * size),
            int(end[1] - math.sin(a) * size),
        )
        draw.line([end, p], fill=fill, width=width)


def draw_label(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], text: str, font, fill_bg, fill_text) -> None:
    text = clean_pdf_text(text)
    if not text:
        return
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x, y = xy
    draw.rounded_rectangle((x, y, x + w + 12, y + h + 8), radius=8, fill=fill_bg)
    draw.text((x + 6, y + 4), text, font=font, fill=fill_text)


def lookup_record_by_page(drawings: List[Dict[str, Any]], page_index: int) -> Optional[Dict[str, Any]]:
    for record in drawings:
        if record.get("page_index") == page_index:
            return record
    return None


def render_overlay_image(
    drawing_record: Dict[str, Any],
    sketch: Dict[str, Any],
    concept_name: str,
    grunnlag_status: str,
) -> Image.Image:
    base = copy_rgb(drawing_record["image"]).convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    w, h = base.size
    min_dim = min(w, h)

    font_title = get_font(max(22, int(min_dim * 0.028)), bold=True)
    font_body = get_font(max(16, int(min_dim * 0.018)), bold=False)
    font_small = get_font(max(14, int(min_dim * 0.015)), bold=False)

    # Toppbånd
    pad = max(20, int(min_dim * 0.025))
    ribbon_h = max(72, int(h * 0.10))
    draw.rounded_rectangle(
        (pad, pad, w - pad, pad + ribbon_h),
        radius=18,
        fill=OVERLAY_COLORS["dark"],
        outline=OVERLAY_COLORS["column"],
        width=3,
    )
    draw.text(
        (pad + 18, pad + 12),
        short_text(sketch.get("page_label") or drawing_record.get("label") or "Konseptskisse", 52),
        font=font_title,
        fill=OVERLAY_COLORS["white"],
    )
    draw.text(
        (pad + 18, pad + 16 + font_title.getbbox("Ag")[3]),
        short_text(concept_name or "Anbefalt bæresystem", 72),
        font=font_body,
        fill=(200, 211, 223, 255),
    )
    status_text = f"Grunnlag: {grunnlag_status}"
    status_bbox = draw.textbbox((0, 0), status_text, font=font_small)
    sw = status_bbox[2] - status_bbox[0]
    sh = status_bbox[3] - status_bbox[1]
    sx = w - pad - sw - 28
    sy = pad + 18
    draw.rounded_rectangle((sx, sy, sx + sw + 16, sy + sh + 10), radius=12, fill=(10, 22, 35, 220))
    draw.text((sx + 8, sy + 5), status_text, font=font_small, fill=OVERLAY_COLORS["white"])

    # Tegn elementer
    elements = sketch.get("elements", [])
    for element in elements:
        e_type = clean_pdf_text(element.get("type", "")).lower()

        if e_type == "grid":
            orientation = clean_pdf_text(element.get("orientation", "")).lower()
            label = clean_pdf_text(element.get("label", ""))
            if orientation.startswith("v"):
                x = int(normalize_relative_value(element.get("x"), 0.2) * w)
                draw.line((x, pad + ribbon_h + 8, x, h - pad - 40), fill=OVERLAY_COLORS["grid"], width=3)
                if label:
                    draw_label(draw, (x - 10, pad + ribbon_h + 16), label, font_small, OVERLAY_COLORS["dark"], OVERLAY_COLORS["white"])
                    draw_label(draw, (x - 10, h - pad - 52), label, font_small, OVERLAY_COLORS["dark"], OVERLAY_COLORS["white"])
            elif orientation.startswith("h"):
                y = int(normalize_relative_value(element.get("y"), 0.2) * h)
                draw.line((pad + 10, y, w - pad - 10, y), fill=OVERLAY_COLORS["grid"], width=3)
                if label:
                    draw_label(draw, (pad + 14, y - 14), label, font_small, OVERLAY_COLORS["dark"], OVERLAY_COLORS["white"])
                    draw_label(draw, (w - pad - 44, y - 14), label, font_small, OVERLAY_COLORS["dark"], OVERLAY_COLORS["white"])

        elif e_type == "column":
            x = int(normalize_relative_value(element.get("x"), 0.5) * w)
            y = int(normalize_relative_value(element.get("y"), 0.5) * h)
            r = max(8, int(min_dim * 0.012))
            draw.ellipse((x - r, y - r, x + r, y + r), fill=OVERLAY_COLORS["column"], outline=OVERLAY_COLORS["white"], width=3)
            label = clean_pdf_text(element.get("label", ""))
            if label:
                draw_label(draw, (x + r + 6, y - r - 10), label, font_small, OVERLAY_COLORS["dark"], OVERLAY_COLORS["white"])

        elif e_type == "core":
            x = int(normalize_relative_value(element.get("x"), 0.45) * w)
            y = int(normalize_relative_value(element.get("y"), 0.45) * h)
            cw = int(normalize_relative_value(element.get("w"), 0.12) * w)
            ch = int(normalize_relative_value(element.get("h"), 0.16) * h)
            rect = (x, y, min(w - pad, x + cw), min(h - pad, y + ch))
            draw.rounded_rectangle(rect, radius=10, fill=OVERLAY_COLORS["core_fill"], outline=OVERLAY_COLORS["core_stroke"], width=4)
            label = clean_pdf_text(element.get("label", "Kjerne"))
            draw_label(draw, (rect[0] + 8, rect[1] + 8), label, font_small, (255, 196, 64, 230), (30, 30, 30, 255))

        elif e_type == "wall":
            x1 = int(normalize_relative_value(element.get("x1"), 0.2) * w)
            y1 = int(normalize_relative_value(element.get("y1"), 0.2) * h)
            x2 = int(normalize_relative_value(element.get("x2"), 0.2) * w)
            y2 = int(normalize_relative_value(element.get("y2"), 0.7) * h)
            draw.line((x1, y1, x2, y2), fill=OVERLAY_COLORS["wall"], width=max(6, int(min_dim * 0.01)))
            label = clean_pdf_text(element.get("label", "Skive"))
            if label:
                draw_label(draw, (min(x1, x2) + 8, min(y1, y2) + 8), label, font_small, (255, 153, 153, 220), (35, 35, 35, 255))

        elif e_type == "beam":
            x1 = int(normalize_relative_value(element.get("x1"), 0.1) * w)
            y1 = int(normalize_relative_value(element.get("y1"), 0.2) * h)
            x2 = int(normalize_relative_value(element.get("x2"), 0.7) * w)
            y2 = int(normalize_relative_value(element.get("y2"), 0.2) * h)
            draw.line((x1, y1, x2, y2), fill=OVERLAY_COLORS["beam"], width=max(5, int(min_dim * 0.008)))
            label = clean_pdf_text(element.get("label", "Bjelke"))
            if label:
                mx, my = int((x1 + x2) / 2), int((y1 + y2) / 2)
                draw_label(draw, (mx + 8, my - 24), label, font_small, (10, 22, 35, 220), OVERLAY_COLORS["white"])

        elif e_type == "span_arrow":
            x1 = int(normalize_relative_value(element.get("x1"), 0.1) * w)
            y1 = int(normalize_relative_value(element.get("y1"), 0.8) * h)
            x2 = int(normalize_relative_value(element.get("x2"), 0.4) * w)
            y2 = int(normalize_relative_value(element.get("y2"), 0.8) * h)
            draw_arrow(draw, (x1, y1), (x2, y2), OVERLAY_COLORS["span"], width=max(4, int(min_dim * 0.006)))
            label = clean_pdf_text(element.get("label", "Spenn"))
            if label:
                mx, my = int((x1 + x2) / 2), int((y1 + y2) / 2)
                draw_label(draw, (mx - 30, my - 34), label, font_small, (196, 235, 176, 230), (30, 30, 30, 255))

    # Fotnote
    notes = [clean_pdf_text(x) for x in sketch.get("notes", []) if clean_pdf_text(x)]
    footer_lines = ["Maskinell konseptskisse - ikke arbeidstegning."] + notes[:3]
    footer_h = max(96, int(h * 0.12))
    fy1 = h - pad - footer_h
    draw.rounded_rectangle(
        (pad, fy1, w - pad, h - pad),
        radius=18,
        fill=(6, 17, 26, 220),
        outline=(120, 145, 170, 120),
        width=2,
    )
    ty = fy1 + 12
    draw.text((pad + 16, ty), "Vurdering", font=font_body, fill=OVERLAY_COLORS["white"])
    ty += font_body.getbbox("Ag")[3] + 8
    for line in footer_lines[:4]:
        wrapped = wrap_text_px(line, font_small, w - (pad * 2) - 26)
        for subline in wrapped[:2]:
            draw.text((pad + 18, ty), f"- {subline}", font=font_small, fill=(210, 218, 228, 255))
            ty += font_small.getbbox("Ag")[3] + 4

    return Image.alpha_composite(base, overlay).convert("RGB")


def build_fallback_sketch(drawings: List[Dict[str, Any]], concept_name: str) -> Optional[Dict[str, Any]]:
    if not drawings:
        return None
    chosen = None
    for record in drawings:
        if record.get("hint") == "plan":
            chosen = record
            break
    if chosen is None:
        chosen = drawings[0]

    aspect = chosen["image"].size[0] / max(chosen["image"].size[1], 1)
    if aspect >= 1.15:
        cols, rows = 4, 3
    else:
        cols, rows = 3, 4

    left, right, top, bottom = 0.14, 0.86, 0.2, 0.82
    xs = [left + i * ((right - left) / max(cols - 1, 1)) for i in range(cols)]
    ys = [top + i * ((bottom - top) / max(rows - 1, 1)) for i in range(rows)]

    elements: List[Dict[str, Any]] = []
    for i, x in enumerate(xs, start=1):
        elements.append({"type": "grid", "orientation": "vertical", "x": x, "label": chr(64 + i)})
    for j, y in enumerate(ys, start=1):
        elements.append({"type": "grid", "orientation": "horizontal", "y": y, "label": str(j)})

    label_no = 1
    for y in ys:
        for x in xs:
            elements.append({"type": "column", "x": x, "y": y, "label": f"C{label_no}"})
            label_no += 1

    elements.append({"type": "core", "x": 0.44, "y": 0.36, "w": 0.14, "h": 0.18, "label": "K1"})
    elements.append({"type": "span_arrow", "x1": xs[0], "y1": bottom + 0.05, "x2": xs[1], "y2": bottom + 0.05, "label": "Repeterbart modulspenn"})
    elements.append({"type": "beam", "x1": xs[0], "y1": ys[0], "x2": xs[-1], "y2": ys[0], "label": "Primærretning"})
    elements.append({"type": "wall", "x1": 0.58, "y1": top, "x2": 0.58, "y2": bottom, "label": "Stabiliserende skive"})

    return {
        "page_index": chosen["page_index"],
        "page_label": f"{chosen['label']} - fallback-skisse",
        "notes": [
            "Fallback-skisse basert på heuristisk grid fordi AI ikke returnerte brukbare koordinater.",
            "Søylepunkter er lagt for repeterbarhet og korte lastveier.",
            f"Konseptet er knyttet til: {concept_name}.",
        ],
        "elements": elements,
    }


def build_overlay_package(
    drawings: List[Dict[str, Any]],
    analysis_result: Dict[str, Any],
    max_sketches: int = 3,
) -> List[Dict[str, Any]]:
    concept_name = analysis_result.get("recommended_system", {}).get("system_name", "Anbefalt system")
    out: List[Dict[str, Any]] = []

    sketches = analysis_result.get("sketches", [])[:max_sketches]
    if not sketches and drawings and analysis_result.get("grunnlag_status") != "FOR_SVAKT":
        fallback = build_fallback_sketch(drawings, concept_name)
        if fallback:
            sketches = [fallback]

    for sketch in sketches:
        record = lookup_record_by_page(drawings, int(sketch.get("page_index", -1)))
        if record is None:
            continue
        overlay = render_overlay_image(record, sketch, concept_name, analysis_result.get("grunnlag_status", "-"))
        out.append(
            {
                "page_index": record["page_index"],
                "caption": clean_pdf_text(
                    f"Konseptskisse på {record['name']} - {short_text(sketch.get('page_label', record['label']), 80)}"
                ),
                "image": overlay,
            }
        )
    return out


# ------------------------------------------------------------
# 10. GENERISK TABELLRENDER TIL PDF
# ------------------------------------------------------------
ROW_CLASS_FILL = {
    "JA": (196, 235, 176),
    "ANBEFALT": (196, 235, 176),
    "Lav": (214, 236, 255),
    "Middels": (255, 242, 153),
    "Høy": (255, 153, 153),
    "Hoy": (255, 153, 153),
}


def render_table_image(
    df: pd.DataFrame,
    title: str,
    subtitle: str = "",
    row_fill_column: Optional[str] = None,
    cell_fill_lookup: Optional[Dict[Tuple[int, str], Tuple[int, int, int]]] = None,
    note: str = "",
) -> Image.Image:
    df = df.copy().fillna("")
    title = clean_pdf_text(title)
    subtitle = clean_pdf_text(subtitle)
    note = clean_pdf_text(note)

    font_title = get_font(34, bold=True)
    font_subtitle = get_font(18, bold=False)
    font_header = get_font(18, bold=True)
    font_body = get_font(16, bold=False)

    side_pad = 28
    top_pad = 24
    cell_pad_x = 10
    cell_pad_y = 9
    table_width = 1540

    width_weights = []
    for col in df.columns:
        col_txt = str(col)
        if col_txt in {"Prioritet", "Anbefalt", "Rasjonalitet", "Robusthet", "Total", "Alvorlighet"}:
            width_weights.append(0.85)
        elif "spenn" in col_txt.lower() or "material" in col_txt.lower():
            width_weights.append(1.1)
        elif any(key in col_txt.lower() for key in ["forbehold", "kommentar", "tiltak", "bruk", "stabilitet"]):
            width_weights.append(2.2)
        else:
            width_weights.append(1.4)
    total_weight = sum(width_weights) or 1
    col_widths = [max(90, int(table_width * w / total_weight)) for w in width_weights]

    header_height = 0
    header_wrapped: Dict[str, List[str]] = {}
    for col, width in zip(df.columns, col_widths):
        wrapped = wrap_text_px(str(col), font_header, width - (cell_pad_x * 2))
        header_wrapped[col] = wrapped
        header_height = max(header_height, len(wrapped) * 24 + (cell_pad_y * 2))

    row_heights: List[int] = []
    wrapped_cells: List[Dict[str, List[str]]] = []
    for ridx in range(len(df)):
        row = df.iloc[ridx]
        row_wrap: Dict[str, List[str]] = {}
        row_height = 0
        for col, width in zip(df.columns, col_widths):
            wrapped = wrap_text_px(str(row[col]), font_body, width - (cell_pad_x * 2))
            row_wrap[col] = wrapped
            row_height = max(row_height, len(wrapped) * 22 + (cell_pad_y * 2))
        row_heights.append(max(34, row_height))
        wrapped_cells.append(row_wrap)

    title_height = 66
    subtitle_height = 28 if subtitle else 0
    note_height = 34 if note else 0
    total_height = top_pad + title_height + subtitle_height + 14 + header_height + sum(row_heights) + note_height + 28

    image_width = table_width + side_pad * 2
    image_height = total_height + 10
    img = Image.new("RGB", (image_width, image_height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    band_fill = (236, 240, 245)
    header_fill = (46, 62, 84)
    alt_fill = (248, 250, 252)
    grid_fill = (205, 212, 220)
    title_fill = (29, 45, 68)
    subtitle_fill = (96, 108, 122)
    text_fill = (35, 38, 43)

    draw.rounded_rectangle((12, 12, image_width - 12, image_height - 12), radius=18, outline=(219, 225, 232), width=2, fill=(255, 255, 255))
    draw.rounded_rectangle((18, 18, image_width - 18, 18 + title_height + subtitle_height + 10), radius=16, fill=band_fill)
    draw.text((side_pad, 28), title, font=font_title, fill=title_fill)
    if subtitle:
        draw.text((side_pad, 28 + 40), subtitle, font=font_subtitle, fill=subtitle_fill)

    x = side_pad
    y = top_pad + title_height + subtitle_height + 10
    for col, width in zip(df.columns, col_widths):
        draw.rectangle((x, y, x + width, y + header_height), fill=header_fill)
        yy = y + cell_pad_y
        for line in header_wrapped[col]:
            draw.text((x + cell_pad_x, yy), clean_pdf_text(line), font=font_header, fill=(255, 255, 255))
            yy += 24
        x += width
    draw.rectangle((side_pad, y, side_pad + sum(col_widths), y + header_height), outline=grid_fill, width=1)

    y += header_height
    for ridx in range(len(df)):
        row = df.iloc[ridx]
        base_fill = alt_fill if ridx % 2 else (255, 255, 255)
        if row_fill_column and row_fill_column in row:
            fill_key = str(row[row_fill_column])
            if fill_key in ROW_CLASS_FILL:
                rgb = ROW_CLASS_FILL[fill_key]
                base_fill = tuple(int((c + 255 * 2) / 3) for c in rgb)
        x = side_pad
        row_height = row_heights[ridx]
        for col, width in zip(df.columns, col_widths):
            cell_fill = base_fill
            if cell_fill_lookup and (ridx, str(col)) in cell_fill_lookup:
                cell_fill = cell_fill_lookup[(ridx, str(col))]
            draw.rectangle((x, y, x + width, y + row_height), fill=cell_fill, outline=grid_fill, width=1)
            yy = y + cell_pad_y
            for line in wrapped_cells[ridx][col]:
                draw.text((x + cell_pad_x, yy), clean_pdf_text(line), font=font_body, fill=text_fill)
                yy += 22
            x += width
        y += row_height

    if note:
        draw.text((side_pad, y + 8), note, font=font_subtitle, fill=subtitle_fill)
    return img


# ------------------------------------------------------------
# 11. PDF-BYGGER I SAMME FORMSPRÅK SOM GEO
# ------------------------------------------------------------
def split_ai_sections(content: str) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            if current:
                sections.append(current)
            current = {"title": ironclad_text_formatter(line.lstrip("#").strip()), "lines": []}
            continue
        if current is None:
            continue
        current["lines"].append(raw_line.rstrip())
    if current:
        sections.append(current)
    return sections


def is_subheading_line(line: str) -> bool:
    clean = line.strip()
    if not clean:
        return False
    if clean.startswith("##"):
        return True
    if clean.endswith(":") and len(clean) < 90 and len(clean.split()) <= 8:
        return True
    if clean == clean.upper() and any(ch.isalpha() for ch in clean) and len(clean) < 70:
        return True
    return False


def is_bullet_line(line: str) -> bool:
    return bool(re.match(r"^([-*•]|\d+\.)\s+", line.strip()))


def strip_bullet(line: str) -> str:
    return re.sub(r"^([-*•]|\d+\.)\s+", "", line.strip())


class BuiltlyCorporatePDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_y(11)
        self.set_text_color(88, 94, 102)
        self.set_font("Helvetica", "", 8)
        self.cell(0, 4, clean_pdf_text(self.header_left), 0, 0, "L")
        self.cell(0, 4, clean_pdf_text(self.header_right), 0, 1, "R")
        self.set_draw_color(188, 192, 197)
        self.line(18, 18, 192, 18)
        self.set_y(24)

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(210, 214, 220)
        self.line(18, 285, 192, 285)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(110, 114, 119)
        self.cell(60, 5, clean_pdf_text(self.doc_code), 0, 0, "L")
        self.cell(70, 5, clean_pdf_text("Utkast - krever faglig kontroll"), 0, 0, "C")
        self.cell(0, 5, clean_pdf_text(f"Side {self.page_no()}"), 0, 0, "R")

    def ensure_space(self, needed_height: float):
        if self.get_y() + needed_height > 272:
            self.add_page()

    def rounded_rect(self, x, y, w, h, r, style=""):
        try:
            super().rounded_rect(x, y, w, h, r, style)
        except Exception:
            self.rect(x, y, w, h, style if style in {"F", "FD", "DF"} else "")

    def section_title(self, title: str):
        self.ensure_space(20)
        self.ln(2)
        title = ironclad_text_formatter(title)
        num_match = re.match(r"^(\d+\.?\d*)\s*(.*)$", title)
        if num_match and (num_match.group(1).endswith(".") or num_match.group(2)):
            number = num_match.group(1).rstrip(".")
            text = num_match.group(2).strip()
        else:
            number = None
            text = title

        self.set_font("Helvetica", "B", 17)
        self.set_text_color(36, 50, 72)
        start_y = self.get_y()
        if number:
            self.set_xy(20, start_y)
            self.cell(12, 8, clean_pdf_text(number), 0, 0, "L")
            self.set_xy(34, start_y)
            self.multi_cell(156, 8, clean_pdf_text(text.upper()), 0, "L")
        else:
            self.set_xy(20, start_y)
            self.multi_cell(170, 8, clean_pdf_text(text.upper()), 0, "L")
        self.set_draw_color(204, 209, 216)
        self.line(20, self.get_y() + 1, 190, self.get_y() + 1)
        self.ln(5)

    def body_paragraph(self, text: str, first: bool = False):
        text = ironclad_text_formatter(text)
        if not text:
            return
        self.set_x(20)
        self.set_font("Helvetica", "", 10.2 if not first else 10.6)
        self.set_text_color(35, 39, 43)
        self.multi_cell(170, 5.5 if not first else 5.7, clean_pdf_text(text))
        self.ln(1.6)

    def subheading(self, text: str):
        text = ironclad_text_formatter(text.replace("##", "").rstrip(":"))
        self.ensure_space(14)
        self.ln(2)
        self.set_x(20)
        self.set_font("Helvetica", "B", 10.8)
        self.set_text_color(48, 64, 86)
        self.cell(0, 6, clean_pdf_text(text.upper()), 0, 1)
        self.set_draw_color(225, 229, 234)
        self.line(20, self.get_y(), 190, self.get_y())
        self.ln(2)

    def bullets(self, items: List[str], numbered: bool = False):
        for idx, item in enumerate(items, start=1):
            clean = ironclad_text_formatter(item)
            if not clean:
                continue
            self.ensure_space(10)
            self.set_font("Helvetica", "", 10.1)
            self.set_text_color(35, 39, 43)
            start_y = self.get_y()
            self.set_xy(22, start_y)
            self.cell(6, 5.2, f"{idx}." if numbered else "-", 0, 0, "L")
            self.set_xy(28, start_y)
            self.multi_cell(162, 5.2, clean_pdf_text(clean))
            self.ln(0.8)

    def kv_card(self, items: List[Tuple[str, str]], x=None, width=80, title=None):
        if x is None:
            x = self.get_x()
        height = 10 + (len(items) * 6.3) + (7 if title else 0)
        self.ensure_space(height + 3)
        start_y = self.get_y()
        self.set_fill_color(245, 247, 249)
        self.set_draw_color(214, 219, 225)
        self.rounded_rect(x, start_y, width, height, 4, "DF")
        yy = start_y + 5
        if title:
            self.set_xy(x + 4, yy)
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(48, 64, 86)
            self.cell(width - 8, 5, clean_pdf_text(title.upper()), 0, 1)
            yy += 7
        for label, value in items:
            self.set_xy(x + 4, yy)
            self.set_font("Helvetica", "B", 8.6)
            self.set_text_color(72, 79, 87)
            self.cell(28, 5, clean_pdf_text(label), 0, 0)
            self.set_font("Helvetica", "", 8.6)
            self.set_text_color(35, 39, 43)
            self.multi_cell(width - 34, 5, clean_pdf_text(value))
            yy = self.get_y() + 1
        self.set_y(max(self.get_y(), start_y + height))

    def highlight_box(self, title: str, items: List[str], fill=(245, 247, 250), accent=(50, 77, 106)):
        self.set_font("Helvetica", "", 10)
        total_text_h = 0
        for item in items:
            w = self.get_string_width(clean_pdf_text(item))
            lines = int((w / 145)) + 1
            total_text_h += (lines * 5.5) + 2
        box_h = 14 + total_text_h
        self.ensure_space(box_h + 5)
        x, y = 20, self.get_y()

        self.set_fill_color(*fill)
        self.set_draw_color(217, 223, 230)
        self.rounded_rect(x, y, 170, box_h, 4, "DF")
        self.set_fill_color(*accent)
        self.rect(x, y, 3, box_h, "F")
        self.set_xy(x + 6, y + 4)
        self.set_font("Helvetica", "B", 10.5)
        self.set_text_color(*accent)
        self.cell(0, 5, clean_pdf_text(title.upper()), 0, 1)
        self.set_text_color(35, 39, 43)
        self.set_font("Helvetica", "", 10)

        yy = y + 10
        for item in items:
            self.set_xy(x + 8, yy)
            self.cell(5, 5, "-", 0, 0)
            self.multi_cell(154, 5.2, clean_pdf_text(item))
            yy = self.get_y() + 2

        self.set_y(y + box_h + 3)

    def figure_image(self, image_path: str, width=82, caption=""):
        img = Image.open(image_path)
        height = width * (img.height / img.width)
        self.ensure_space(height + 15)
        x, y = self.get_x(), self.get_y()
        self.set_draw_color(219, 223, 228)
        self.rect(x, y, width, height)
        self.image(image_path, x=x, y=y, w=width)
        self.set_y(y + height + 2)
        if caption:
            self.set_x(x)
            self.set_font("Helvetica", "I", 7.7)
            self.set_text_color(104, 109, 116)
            self.multi_cell(width, 4, clean_pdf_text(caption), 0, "C")
        self.set_y(y + height + 10)

    def table_image(self, img_path: str, width=170, caption=""):
        img = Image.open(img_path)
        height = width * (img.height / img.width)
        self.ensure_space(height + 15)
        x, y = 20, self.get_y()
        self.image(img_path, x=x, y=y, w=width)
        self.set_y(y + height + 2)
        if caption:
            self.set_x(20)
            self.set_font("Helvetica", "I", 7.7)
            self.set_text_color(104, 109, 116)
            self.multi_cell(width, 4, clean_pdf_text(caption), 0, "L")
        self.ln(6)


def render_ai_section_body(pdf: BuiltlyCorporatePDF, lines: List[str]):
    paragraph_buffer: List[str] = []
    bullet_buffer: List[str] = []
    first_para = True
    empty_line_count = 0

    def flush_paragraph():
        nonlocal paragraph_buffer, first_para
        if paragraph_buffer:
            text = " ".join(line.strip() for line in paragraph_buffer if line.strip())
            if text:
                pdf.body_paragraph(text, first=first_para)
                first_para = False
        paragraph_buffer = []

    def flush_bullets():
        nonlocal bullet_buffer
        if bullet_buffer:
            numbered = all(re.match(r"^\d+\.\s+", item.strip()) for item in bullet_buffer)
            pdf.bullets([strip_bullet(item) for item in bullet_buffer], numbered=numbered)
        bullet_buffer = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_bullets()
            empty_line_count += 1
            if empty_line_count == 1:
                pdf.ln(3)
            continue
        empty_line_count = 0
        if is_subheading_line(line):
            flush_paragraph()
            flush_bullets()
            pdf.subheading(line)
            continue
        if is_bullet_line(line):
            flush_paragraph()
            bullet_buffer.append(line)
            continue
        flush_bullets()
        paragraph_buffer.append(line)

    flush_paragraph()
    flush_bullets()


def build_cover_page(
    pdf: BuiltlyCorporatePDF,
    project_data: Dict[str, Any],
    client: str,
    cover_image: Optional[Image.Image],
    analysis_result: Dict[str, Any],
):
    pdf.add_page()

    if os.path.exists("logo.png"):
        try:
            pdf.image("logo.png", x=150, y=15, w=40)
        except Exception:
            pass

    pdf.set_xy(20, 45)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(100, 105, 110)
    pdf.cell(80, 6, clean_pdf_text("KONSEPTNOTAT"), 0, 1, "L")

    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 34)
    pdf.set_text_color(20, 28, 38)
    pdf.multi_cell(95, 12, clean_pdf_text(project_data.get("p_name", "Konstruksjon")), 0, "L")

    pdf.ln(4)
    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(64, 68, 74)
    pdf.multi_cell(
        95,
        6.5,
        clean_pdf_text("Bæresystem, stabilitet, rasjonalitet og maskinell konseptskisse"),
        0,
        "L",
    )

    pdf.set_xy(118, 45)
    meta_items = [
        ("Oppdragsgiver", client or "-"),
        ("Emne", "RIB / Konstruksjon"),
        ("Dato / revisjon", datetime.now().strftime("%d.%m.%Y") + " / 01"),
        ("Dokumentkode", "Builtly-RIB-001"),
        ("Status", analysis_result.get("grunnlag_status", "-")),
    ]
    pdf.kv_card(meta_items, x=118, width=72)

    if cover_image is not None:
        img_path = save_temp_image(cover_image.convert("RGB"), ".jpg")
        with Image.open(img_path) as tmp_img:
            aspect = tmp_img.height / max(tmp_img.width, 1)

        w = 170
        h = w * aspect
        if h > 130:
            h = 130
            w = h / aspect

        x = 20 + (170 - w) / 2
        y = max(pdf.get_y() + 15, 115)

        pdf.set_xy(x, y)
        pdf.figure_image(img_path, width=w, caption="Maskinell konseptskisse eller tegningsgrunnlag brukt som forsidefigur.")
    else:
        pdf.set_fill_color(244, 246, 248)
        pdf.set_draw_color(220, 224, 228)
        pdf.rounded_rect(20, 115, 170, 80, 4, "DF")
        pdf.set_xy(24, 146)
        pdf.set_font("Helvetica", "I", 12)
        pdf.set_text_color(112, 117, 123)
        pdf.multi_cell(160, 6, clean_pdf_text("Tegningsgrunnlag legges inn automatisk fra Project Setup eller via manuell opplasting i modulen."), 0, "C")

    pdf.set_xy(20, 252)
    pdf.set_font("Helvetica", "", 8.8)
    pdf.set_text_color(104, 109, 116)
    pdf.multi_cell(
        170,
        4.5,
        clean_pdf_text(
            "Rapporten er generert av Builtly RIB AI på bakgrunn av prosjektdata og opplastet tegningsgrunnlag. "
            "Dokumentet er et arbeidsutkast og konseptskissene er ikke arbeidstegninger. Resultatet skal fagkontrolleres før bruk i prosjektering, byggesak eller utførelse."
        ),
    )


def build_toc_page(pdf: BuiltlyCorporatePDF, include_appendices: bool = True):
    pdf.add_page()
    pdf.section_title("INNHOLDSFORTEGNELSE")
    items = [
        "1. Sammendrag og konklusjon",
        "2. Vurdering av datagrunnlag",
        "3. Laster og forutsetninger",
        "4. Konsept for bæresystem og stabilitet",
        "5. Rasjonalitet, byggbarhet og alternative systemer",
        "6. Fundamentering og eksisterende konstruksjoner",
        "7. Risiko, sårbarhet og neste steg",
    ]
    if include_appendices:
        items.extend(
            [
                "Vedlegg A. Maskinelle konseptskisser",
                "Vedlegg B. Alternativmatrise for bæresystem",
                "Vedlegg C. Vurdert tegningsgrunnlag",
            ]
        )

    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(45, 49, 55)
    for item in items:
        pdf.ensure_space(9)
        y = pdf.get_y()
        pdf.set_x(22)
        pdf.cell(0, 6, clean_pdf_text(item), 0, 0, "L")
        pdf.set_draw_color(225, 229, 234)
        pdf.line(22, y + 6, 188, y + 6)
        pdf.ln(8)
    pdf.ln(6)
    pdf.highlight_box(
        "Dokumentoppsett",
        [
            "Rapporten kombinerer tekstlig RIB-vurdering med maskinelle konseptskisser oppå arkitekttegningene.",
            "Konseptskissene viser foreslått punktbæresystem, kjerner, skiver og spennretninger for tidligfase beslutning.",
        ],
    )


def create_risk_table_image(analysis_result: Dict[str, Any]) -> Optional[Image.Image]:
    risks = analysis_result.get("risk_register", [])
    if not risks:
        return None
    df = pd.DataFrame(
        [
            {
                "Risiko": risk.get("topic", "-"),
                "Alvorlighet": risk.get("severity", "Middels"),
                "Tiltak": risk.get("mitigation", "-"),
            }
            for risk in risks[:12]
        ]
    )
    return render_table_image(
        df,
        title="Risikoregister for konseptfasen",
        subtitle="Maskinelt identifiserte forhold som må følges opp",
        row_fill_column="Alvorlighet",
        note="Risikoregisteret er et konseptgrunnlag og må suppleres i videre prosjektering.",
    )


def create_candidate_table_image(candidate_df: pd.DataFrame) -> Optional[Image.Image]:
    if candidate_df is None or candidate_df.empty:
        return None
    cell_fill_lookup: Dict[Tuple[int, str], Tuple[int, int, int]] = {}
    for ridx, row in candidate_df.iterrows():
        if str(row.get("Anbefalt", "")).strip().upper() == "JA":
            for col in candidate_df.columns:
                cell_fill_lookup[(ridx, str(col))] = (232, 246, 233)
    return render_table_image(
        candidate_df,
        title="Maskinell alternativmatrise for bæresystem",
        subtitle="Rangert etter rasjonalitet og robusthet",
        cell_fill_lookup=cell_fill_lookup,
        note="Førsterangert system brukes som startpunkt og kalibreres mot tegningsgrunnlaget.",
    )


def create_full_report_pdf(
    name: str,
    client: str,
    content: str,
    analysis_result: Dict[str, Any],
    candidate_df: pd.DataFrame,
    overlay_package: List[Dict[str, Any]],
    source_drawings: List[Dict[str, Any]],
    project_data: Dict[str, Any],
) -> bytes:
    pdf = BuiltlyCorporatePDF("P", "mm", "A4")
    pdf.set_auto_page_break(True, margin=22)
    pdf.set_margins(18, 18, 18)
    pdf.header_left = clean_pdf_text(project_data.get("p_name", name))
    pdf.header_right = clean_pdf_text("Builtly | RIB")
    pdf.doc_code = clean_pdf_text("Builtly-RIB-001")

    cover_image = overlay_package[0]["image"] if overlay_package else (source_drawings[0]["image"] if source_drawings else None)
    build_cover_page(pdf, project_data, client, cover_image, analysis_result)
    build_toc_page(pdf, include_appendices=True)

    sections = split_ai_sections(content) or [{"title": "1. SAMMENDRAG OG KONKLUSJON", "lines": [content]}]
    candidate_table = create_candidate_table_image(candidate_df)
    risk_table = create_risk_table_image(analysis_result)

    pdf.add_page()
    rendered_intro_boxes = False
    rendered_candidate_table = False
    rendered_risk_table = False
    rendered_hero_sketch = False

    for idx, section in enumerate(sections):
        title = section.get("title", "")

        if idx > 0 and pdf.get_y() > 30:
            pdf.ln(6)

        if title.startswith("4.") and overlay_package:
            pdf.ensure_space(130)
        elif title.startswith("5.") and candidate_table is not None:
            pdf.ensure_space(100)
        elif title.startswith("7.") and risk_table is not None:
            pdf.ensure_space(100)

        pdf.section_title(title)

        if title.startswith("1.") and not rendered_intro_boxes:
            pdf.ensure_space(65)
            start_y = pdf.get_y()
            pdf.kv_card(
                [
                    ("Prosjekt", project_data.get("p_name", name)),
                    ("Lokasjon", f"{project_data.get('adresse', '')}, {project_data.get('kommune', '')}".strip(", ")),
                    ("Gnr/Bnr", f"{project_data.get('gnr', '-')}/{project_data.get('bnr', '-')}"),
                    ("Byggtype", project_data.get("b_type", "-")),
                    ("BTA", f"{project_data.get('bta', 0)} m2"),
                ],
                x=20,
                width=82,
                title="Prosjektgrunnlag",
            )
            end_left = pdf.get_y()

            pdf.set_xy(108, start_y)
            pdf.kv_card(
                [
                    ("Status", analysis_result.get("grunnlag_status", "-")),
                    ("Skisser", str(len(overlay_package))),
                    ("Tegninger", str(len(source_drawings))),
                    ("Regelverk", project_data.get("land", "Norge")),
                ],
                x=108,
                width=82,
                title="Datagrunnlag",
            )
            end_right = pdf.get_y()
            pdf.set_y(max(end_left, end_right) + 6)

            summary_box = [
                f"Datagrunnlaget er vurdert som {analysis_result.get('grunnlag_status', '-')}.",
                analysis_result.get("grunnlag_begrunnelse", "-"),
                f"Anbefalt konsept: {analysis_result.get('recommended_system', {}).get('system_name', '-')}.",
            ]
            pdf.highlight_box("Kjernevurdering", summary_box)
            rendered_intro_boxes = True

        if title.startswith("4.") and overlay_package and not rendered_hero_sketch:
            pdf.highlight_box(
                "Maskinell konseptskisse",
                [
                    "Skissen viser foreslått bæresystem lagt oppå arkitekttegningen.",
                    "Punktmarkeringer illustrerer søylepunkter, mens kjerne/skive og spennretning vises for å støtte tidligfase beslutning.",
                ],
            )
            img_path = save_temp_image(overlay_package[0]["image"], ".jpg")
            pdf.figure_image(img_path, width=165, caption=overlay_package[0]["caption"])
            rendered_hero_sketch = True

        if title.startswith("5.") and candidate_table is not None and not rendered_candidate_table:
            table_path = save_temp_image(candidate_table, ".png")
            pdf.table_image(
                table_path,
                width=170,
                caption="Tabell 1. Maskinell rangering av alternative bæresystemer med fokus på rasjonalitet og robusthet.",
            )
            rendered_candidate_table = True

        if title.startswith("7.") and risk_table is not None and not rendered_risk_table:
            risk_path = save_temp_image(risk_table, ".png")
            pdf.table_image(
                risk_path,
                width=170,
                caption="Tabell 2. Risikoregister for konseptfasen med anbefalt oppfølging.",
            )
            rendered_risk_table = True

        render_ai_section_body(pdf, section.get("lines", []))

    # Vedlegg A: konseptskisser
    if overlay_package:
        for idx, item in enumerate(overlay_package, start=1):
            pdf.add_page()
            pdf.section_title(f"Vedlegg A. Maskinell konseptskisse {idx}")
            img_path = save_temp_image(item["image"], ".jpg")
            pdf.figure_image(
                img_path,
                width=170,
                caption=f"Vedlegg A{idx}. {item['caption']}",
            )

    # Vedlegg B: alternativmatrise
    if candidate_table is not None:
        pdf.add_page()
        pdf.section_title("Vedlegg B. Alternativmatrise for bæresystem")
        table_path = save_temp_image(candidate_table, ".png")
        pdf.table_image(
            table_path,
            width=170,
            caption="Vedlegg B. Strukturert maskinell vurdering av aktuelle bæresystemer for prosjektet.",
        )

    # Vedlegg C: rått tegningsgrunnlag
    if source_drawings:
        for idx, record in enumerate(source_drawings[:6], start=1):
            pdf.add_page()
            pdf.section_title(f"Vedlegg C. Vurdert tegningsgrunnlag {idx}")
            img_path = save_temp_image(record["image"], ".jpg")
            pdf.figure_image(
                img_path,
                width=170,
                caption=f"Vedlegg C{idx}. {record['name']} ({record['source']}, hint: {record['hint']}).",
            )

    out = pdf.output(dest="S")
    return bytes(out) if isinstance(out, (bytes, bytearray)) else out.encode("latin-1")


# ------------------------------------------------------------
# 12. STATUSMAPPING OG QA-LAGRING
# ------------------------------------------------------------
def qa_status_from_analysis(analysis_result: Dict[str, Any]) -> Tuple[str, str]:
    status = analysis_result.get("grunnlag_status", "DELVIS")
    if status == "FOR_SVAKT":
        return "Rejected - Needs Architecture Data", "badge-early"
    if status == "DELVIS":
        return "Indicative Structural Concept", "badge-roadmap"
    return "Pending Senior RIB Review", "badge-pending"


def persist_generation_to_session(
    pdf_data: bytes,
    filename: str,
    analysis_result: Dict[str, Any],
    report_text: str,
    candidate_df: pd.DataFrame,
    overlay_package: List[Dict[str, Any]],
):
    st.session_state.generated_rib_pdf = pdf_data
    st.session_state.generated_rib_filename = filename
    st.session_state.generated_rib_analysis = analysis_result
    st.session_state.generated_rib_report_text = report_text
    st.session_state.generated_rib_candidate_df = candidate_df
    st.session_state.generated_rib_overlay_package = [
        {
            "page_index": item["page_index"],
            "caption": item["caption"],
            "png_bytes": png_bytes_from_image(item["image"]),
        }
        for item in overlay_package
    ]


# ------------------------------------------------------------
# 13. STREAMLIT-UI
# ------------------------------------------------------------
st.markdown("<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🏗️ RIB — Konstruksjon</h1>", unsafe_allow_html=True)
st.markdown(
    "<p style='color: #9fb0c3; font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for bæresystem, lastveier, stabilitet og maskinelle konseptskisser oppå arkitekttegninger.</p>",
    unsafe_allow_html=True,
)

st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er synkronisert fra Project SSOT.")

with st.expander("1. Prosjekt & lokasjon (SSOT)", expanded=True):
    c1, c2 = st.columns(2)
    c1.text_input("Prosjektnavn", value=pd_state["p_name"], disabled=True)
    c2.text_input("Bygningstype", value=pd_state["b_type"], disabled=True)
    st.text_input("Adresse", value=f"{pd_state.get('adresse', '')}, {pd_state.get('kommune', '')}".strip(", "), disabled=True)
    c3, c4, c5 = st.columns(3)
    c3.text_input("Gnr/Bnr", value=f"{pd_state.get('gnr', '-')}/{pd_state.get('bnr', '-')}", disabled=True)
    c4.text_input("Etasjer", value=str(pd_state.get("etasjer", "-")), disabled=True)
    c5.text_input("BTA", value=f"{pd_state.get('bta', 0)} m2", disabled=True)
    st.info("RIB-agenten bruker prosjektdata som rammeverk, men tegningene er styrende for plassering av bæresystem og vurdering av lastveier.")

with st.expander("2. Strategi for bæresystem og sikkerhet", expanded=True):
    col_a, col_b = st.columns(2)
    material_valg = col_a.selectbox(
        "Foretrukket hovedbæresystem",
        [
            "Massivtre (CLT / limtre)",
            "Stål og hulldekker",
            "Plasstøpt betong",
            "Prefabrikkert betong",
            "Hybrid / kombinasjon",
        ],
    )
    fundamentering = col_b.selectbox(
        "Forventet fundamenteringsstrategi",
        [
            "Direkte fundamentering (fjell/faste masser)",
            "Peling til fjell",
            "Sålefundament / kompensert fundamentering",
            "Uavklart - må vurderes mot geoteknikk",
        ],
    )

    col_c, col_d = st.columns(2)
    optimaliser_for = col_c.selectbox(
        "Optimaliseringsmodus",
        [
            "Maks rasjonalitet / repeterbarhet",
            "Lav egenvekt / påbygg",
            "Store spenn / fleksibilitet",
            "Maks robusthet / stivhet",
            "Lav karbon / treandel",
        ],
    )
    safety_mode = col_d.selectbox(
        "Sikkerhetsmodus",
        [
            "Balansert",
            "Konservativ",
        ],
    )

    preview_candidates = build_structural_system_candidates(pd_state, material_valg, optimaliser_for, fundamentering)
    preview_df = build_candidate_dataframe(preview_candidates)
    st.markdown("##### Maskinell alternativstudie")
    st.dataframe(
        preview_df[["Prioritet", "Anbefalt", "System", "Typisk spenn", "Rasjonalitet", "Robusthet", "Total"]],
        use_container_width=True,
        hide_index=True,
    )
    st.caption("Denne matrisen brukes som startpunkt før AI-en ser på tegningene og kalibrerer anbefalingen mot faktisk geometri.")

with st.expander("3. Tegningsgrunnlag og opplasting", expanded=True):
    st.info(
        "Last opp plan, snitt og eventuelt fasader. Agenten prioriterer plan og snitt, analyserer tegningsgrunnlaget og legger foreslått bæresystem som punkter, kjerner og spennretninger på de mest relevante sidene."
    )

    saved_drawings = load_saved_project_drawings()
    if saved_drawings:
        st.success(f"📎 Fant {len(saved_drawings)} tegningsbilder fra Project Setup / lagret prosjektgrunnlag.")
        preview_cols = st.columns(min(3, len(saved_drawings)))
        for idx, record in enumerate(saved_drawings[:3]):
            with preview_cols[idx % len(preview_cols)]:
                st.image(record["image"], caption=f"{record['name']} ({record['hint']})", use_container_width=True)
    else:
        st.warning("Ingen felles tegninger ble funnet automatisk. Last opp plan og snitt manuelt.")

    files = st.file_uploader(
        "Last opp arkitekttegninger / snitt / PDF-er",
        accept_multiple_files=True,
        type=["png", "jpg", "jpeg", "webp", "pdf"],
    )

with st.expander("4. Hva modulen gjør i denne versjonen", expanded=False):
    st.markdown(
        """
- Leser inn plan/snitt/fasader fra Project Setup og opplasting.
- Kjører maskinell alternativstudie for å søke det mest rasjonelle bæresystemet.
- Bruker multimodal AI til å analysere tegningene og foreslå:
  - søylepunkter,
  - kjerner og stabiliserende skiver,
  - spennretninger og lastveier,
  - anbefalt konsept og alternativsystemer,
  - risikoregister og neste steg.
- Legger skissene inn direkte i PDF-rapporten.
"""
    )

st.markdown("<br>", unsafe_allow_html=True)

if st.button("🚀 GENERER RIB-KONSEPT MED SKISSER", type="primary", use_container_width=True):
    uploaded_drawings = load_uploaded_drawings(files, max_pdf_pages=4) if files else []
    all_drawings = prioritize_drawings(saved_drawings + uploaded_drawings, limit=10)

    st.info(f"Klar! Sender totalt {len(all_drawings)} tegninger/bilder til RIB-agenten for vurdering.")

    with st.spinner("🤖 Analyserer tegninger, velger bæresystem og bygger maskinelle konseptskisser..."):
        valid_models = list_available_models()
        valgt_modell = pick_model(valid_models)
        if not valgt_modell:
            st.error("Kunne ikke finne en tilgjengelig Gemini-modell i miljøet.")
            st.stop()

        model = genai.GenerativeModel(valgt_modell)
        candidates = build_structural_system_candidates(pd_state, material_valg, optimaliser_for, fundamentering)
        candidate_df = build_candidate_dataframe(candidates)

        analysis_result = run_structured_drawing_analysis(
            model=model,
            drawings=all_drawings,
            candidates=candidates,
            project_data=pd_state,
            material_preference=material_valg,
            foundation_preference=fundamentering,
            optimization_mode=optimaliser_for,
            safety_mode=safety_mode,
        )

        report_text = run_report_writer(
            model=model,
            analysis_result=analysis_result,
            candidates=candidates,
            project_data=pd_state,
            material_preference=material_valg,
            foundation_preference=fundamentering,
            optimization_mode=optimaliser_for,
        )

        overlay_package = build_overlay_package(all_drawings, analysis_result, max_sketches=3)

        with st.spinner("Kompilerer PDF med konseptskisser og vedlegg..."):
            pdf_data = create_full_report_pdf(
                name=pd_state["p_name"],
                client=pd_state.get("c_name", ""),
                content=report_text,
                analysis_result=analysis_result,
                candidate_df=candidate_df,
                overlay_package=overlay_package,
                source_drawings=all_drawings,
                project_data=pd_state,
            )

            if "pending_reviews" not in st.session_state:
                st.session_state.pending_reviews = {}
            if "review_counter" not in st.session_state:
                st.session_state.review_counter = 1

            doc_id = f"PRJ-{datetime.now().strftime('%y')}-RIB{st.session_state.review_counter:03d}"
            st.session_state.review_counter += 1

            status, badge = qa_status_from_analysis(analysis_result)
            st.session_state.pending_reviews[doc_id] = {
                "title": pd_state["p_name"],
                "module": "RIB (Konstruksjon)",
                "drafter": "Builtly AI",
                "reviewer": "Senior Konstruktør",
                "status": status,
                "class": badge,
                "pdf_bytes": pdf_data,
            }

            safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", clean_pdf_text(pd_state["p_name"]).strip()) or "prosjekt"
            persist_generation_to_session(
                pdf_data=pdf_data,
                filename=f"Builtly_RIB_{safe_name}.pdf",
                analysis_result=analysis_result,
                report_text=report_text,
                candidate_df=candidate_df,
                overlay_package=overlay_package,
            )

        st.rerun()


# ------------------------------------------------------------
# 14. RESULTATVISNING ETTER GENERERING
# ------------------------------------------------------------
if "generated_rib_pdf" in st.session_state:
    analysis_result = safe_session_state_get("generated_rib_analysis", {})
    candidate_df = safe_session_state_get("generated_rib_candidate_df", pd.DataFrame())
    overlay_package = safe_session_state_get("generated_rib_overlay_package", [])
    report_text = safe_session_state_get("generated_rib_report_text", "")

    st.success("✅ RIB-notat er ferdigstilt, lagt i QA-køen og klar for nedlasting.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Datagrunnlag", analysis_result.get("grunnlag_status", "-"))
    m2.metric("Anbefalt system", short_text(analysis_result.get("recommended_system", {}).get("system_name", "-"), 20))
    m3.metric("Konseptskisser", str(len(overlay_package)))
    risks_for_metric = analysis_result.get("risk_register") or [{}]
    top_risk = risks_for_metric[0]
    m4.metric("Topprisiko", short_text(top_risk.get("topic", "-"), 22))

    st.warning("Konseptskissene er laget for tidligfase konseptvalg. De skal fagkontrolleres og videreutvikles før arbeidstegninger eller utførelse.")

    if overlay_package:
        st.markdown("### Konseptskisser")
        for item in overlay_package:
            try:
                img = Image.open(io.BytesIO(item["png_bytes"]))
                st.image(img, caption=item["caption"], use_container_width=True)
            except Exception:
                pass

    with st.expander("Vis rapportutkast", expanded=False):
        st.markdown(report_text if report_text else "_Ingen rapporttekst lagret._")

    with st.expander("Vis alternativstudie", expanded=False):
        if isinstance(candidate_df, pd.DataFrame) and not candidate_df.empty:
            st.dataframe(candidate_df, use_container_width=True, hide_index=True)
        else:
            st.info("Ingen alternativmatrise lagret.")

    col_dl, col_qa = st.columns(2)
    with col_dl:
        st.download_button(
            "📄 Last ned RIB-rapport",
            st.session_state.generated_rib_pdf,
            st.session_state.generated_rib_filename,
            type="primary",
            use_container_width=True,
        )
    with col_qa:
        if find_page("Review"):
            if st.button("🔍 Gå til QA for å godkjenne", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))
