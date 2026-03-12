
# -*- coding: utf-8 -*-
import base64
import importlib.util
import io
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
try:
    import streamlit.components.v1 as components
except Exception:
    components = None
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

try:
    import google.generativeai as genai
except Exception:
    genai = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

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

google_key = os.environ.get("GOOGLE_API_KEY", "").strip()
openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

HAS_GEMINI_BACKEND = bool(google_key and genai is not None)
HAS_OPENAI_BACKEND = bool(openai_key and OpenAI is not None)

if HAS_GEMINI_BACKEND:
    try:
        genai.configure(api_key=google_key)
    except Exception:
        HAS_GEMINI_BACKEND = False

if not HAS_GEMINI_BACKEND and not HAS_OPENAI_BACKEND:
    if google_key and genai is None:
        st.error("Kritisk feil: GOOGLE_API_KEY er satt, men pakken 'google.generativeai' er ikke tilgjengelig i miljøet.")
    elif openai_key and OpenAI is None:
        st.error("Kritisk feil: OPENAI_API_KEY er satt, men pakken 'openai' er ikke tilgjengelig i miljøet.")
    else:
        st.error("Kritisk feil: Fant verken en brukbar Gemini-backend eller en brukbar OpenAI-backend. Sjekk Environment Variables i Render.")
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


# V11: tidlig, selvstendig fallback slik at veggmetrikker finnes før analyse-knapper/patcher evalueres
def _page_norm_to_local_crop_v11(
    x_norm: float,
    y_norm: float,
    image_size: Tuple[int, int],
    region_bbox_px: Tuple[int, int, int, int],
) -> Tuple[float, float]:
    try:
        image_w, image_h = image_size
    except Exception:
        image_w, image_h = 1, 1
    rx, ry, rw, rh = region_bbox_px
    px = float(x_norm) * float(max(int(image_w or 1), 1))
    py = float(y_norm) * float(max(int(image_h or 1), 1))
    local_x = px - float(rx)
    local_y = py - float(ry)
    if rw and rw > 1:
        local_x = max(0.0, min(local_x, float(rw) - 1.0))
    if rh and rh > 1:
        local_y = max(0.0, min(local_y, float(rh) - 1.0))
    return float(local_x), float(local_y)


def _wall_metrics_local_v11(
    element: Dict[str, Any],
    record: Dict[str, Any],
    region_bbox_px: Tuple[int, int, int, int],
) -> Dict[str, float]:
    image_size = (1, 1)
    try:
        image_size = tuple(record.get('image').size)  # type: ignore[arg-type]
    except Exception:
        pass
    x1, y1 = _page_norm_to_local_crop_v11(float(element.get('x1', 0.0)), float(element.get('y1', 0.0)), image_size, region_bbox_px)
    x2, y2 = _page_norm_to_local_crop_v11(float(element.get('x2', 0.0)), float(element.get('y2', 0.0)), image_size, region_bbox_px)
    return {
        'x1': float(x1),
        'y1': float(y1),
        'x2': float(x2),
        'y2': float(y2),
        'x_mid': float((x1 + x2) / 2.0),
        'y_mid': float((y1 + y2) / 2.0),
        'length': float(math.hypot(x2 - x1, y2 - y1)),
        'vertical': bool(abs(x2 - x1) <= abs(y2 - y1)),
    }


# Hvis nyere patch-funksjoner kjøres før den gamle definisjonen lenger ned i filen,
# må navnet fortsatt eksistere for å unngå NameError i analysefasen.
_wall_metrics_local_v7 = _wall_metrics_local_v11


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
_RUNTIME_OPENAI_CLIENT: Any = None


def optional_openai_client() -> Any:
    global _RUNTIME_OPENAI_CLIENT
    if not HAS_OPENAI_BACKEND or OpenAI is None:
        return None
    if _RUNTIME_OPENAI_CLIENT is None:
        try:
            _RUNTIME_OPENAI_CLIENT = OpenAI(api_key=openai_key)
        except Exception:
            _RUNTIME_OPENAI_CLIENT = None
    return _RUNTIME_OPENAI_CLIENT


def list_gemini_models() -> List[str]:
    if not HAS_GEMINI_BACKEND or genai is None:
        return []
    try:
        return [
            m.name
            for m in genai.list_models()
            if "generateContent" in getattr(m, "supported_generation_methods", [])
        ]
    except Exception:
        return []


def list_available_models() -> List[str]:
    models: List[str] = []
    if HAS_OPENAI_BACKEND:
        preferred_openai = clean_pdf_text(
            os.environ.get("OPENAI_VISION_MODEL") or os.environ.get("OPENAI_MODEL") or ""
        ).strip()
        if preferred_openai:
            models.append(f"openai:{preferred_openai}")
        for candidate in ["gpt-4.1", "gpt-4o", "gpt-4.1-mini"]:
            tagged = f"openai:{candidate}"
            if tagged not in models:
                models.append(tagged)
    for gemini_name in list_gemini_models():
        if gemini_name not in models:
            models.append(gemini_name)
    return models


def pick_model(valid_models: List[str]) -> Optional[str]:
    preferred: List[str] = []
    preferred_openai = clean_pdf_text(
        os.environ.get("OPENAI_VISION_MODEL") or os.environ.get("OPENAI_MODEL") or ""
    ).strip()
    if preferred_openai:
        preferred.append(f"openai:{preferred_openai}")
    preferred.extend([
        "openai:gpt-4.1",
        "openai:gpt-4o",
        "openai:gpt-4.1-mini",
        "models/gemini-1.5-pro",
        "models/gemini-1.5-flash",
        "models/gemini-pro-vision",
    ])
    for fav in preferred:
        if fav in valid_models:
            return fav
    return valid_models[0] if valid_models else None


def build_runtime_ai_model(model_name: str) -> Any:
    if isinstance(model_name, dict):
        return model_name
    selected = clean_pdf_text(model_name or "").strip()
    if selected.startswith("openai:"):
        client = optional_openai_client()
        return {"provider": "openai", "client": client, "model_name": selected.split(":", 1)[1]}
    if genai is None:
        raise RuntimeError("Ingen Gemini-backend er tilgjengelig i miljøet.")
    return genai.GenerativeModel(selected)


def pil_image_to_data_uri_for_ai(img: Image.Image) -> str:
    encoded = base64.b64encode(png_bytes_from_image(copy_rgb(img))).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def generate_text(model, parts: List[Any], temperature: float = 0.2) -> str:
    if isinstance(model, dict) and model.get("provider") == "openai":
        client = model.get("client") or optional_openai_client()
        if client is None:
            raise RuntimeError("OpenAI-backend er valgt, men klienten kunne ikke initialiseres.")

        model_name = clean_pdf_text(model.get("model_name") or "gpt-4.1").strip() or "gpt-4.1"
        content: List[Dict[str, Any]] = []
        text_parts: List[str] = []
        for part in parts:
            if isinstance(part, Image.Image):
                content.append(
                    {
                        "type": "input_image",
                        "image_url": pil_image_to_data_uri_for_ai(part),
                        "detail": "high",
                    }
                )
            else:
                txt = clean_pdf_text(part)
                if txt:
                    text_parts.append(txt)
                    content.append({"type": "input_text", "text": txt})

        wants_json = any("JSON" in item.upper() for item in text_parts)
        request: Dict[str, Any] = {
            "model": model_name,
            "input": [{"role": "user", "content": content}],
        }
        if wants_json:
            request["text"] = {"format": {"type": "json_object"}}
        if temperature is not None:
            request["temperature"] = float(temperature)

        try:
            response = client.responses.create(**request)
        except Exception as exc:
            if HAS_GEMINI_BACKEND and genai is not None:
                fallback_name = next((name for name in list_gemini_models() if name), None)
                if fallback_name:
                    st.session_state["rib_ai_backend_warning"] = short_text(
                        f"OpenAI-feil, falt tilbake til Gemini: {type(exc).__name__}: {exc}",
                        240,
                    )
                    return generate_text(genai.GenerativeModel(fallback_name), parts, temperature=temperature)
            raise

        output_text = clean_pdf_text(getattr(response, "output_text", "")).strip()
        if output_text:
            return output_text

        try:
            chunks: List[str] = []
            for item in getattr(response, "output", []) or []:
                for piece in getattr(item, "content", []) or []:
                    piece_text = getattr(piece, "text", None)
                    if piece_text:
                        chunks.append(clean_pdf_text(piece_text))
            return "\n".join(chunk for chunk in chunks if chunk).strip()
        except Exception:
            return ""

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
    def normalize_plan_bbox(plan_bbox: Any) -> Optional[Dict[str, float]]:
        if not isinstance(plan_bbox, dict):
            return None
        try:
            x = normalize_relative_value(plan_bbox.get("x"), 0.10)
            y = normalize_relative_value(plan_bbox.get("y"), 0.10)
            w = normalize_relative_value(plan_bbox.get("w"), 0.60)
            h = normalize_relative_value(plan_bbox.get("h"), 0.60)
        except Exception:
            return None
        w = clamp(w, 0.04, max(0.04, 0.98 - x))
        h = clamp(h, 0.04, max(0.04, 0.98 - y))
        return {"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)}

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

        normalized_entry: Dict[str, Any] = {
            "page_index": page_index,
            "page_label": clean_pdf_text(sketch.get("page_label") or f"Tegning {page_index}"),
            "notes": [clean_pdf_text(x) for x in notes if clean_pdf_text(x)],
            "elements": [e for e in elements if isinstance(e, dict)],
        }
        plan_bbox = normalize_plan_bbox(sketch.get("plan_bbox"))
        if plan_bbox is not None:
            normalized_entry["plan_bbox"] = plan_bbox
        try:
            confidence_value = float(sketch.get("confidence"))
            normalized_entry["confidence"] = float(clamp(confidence_value, 0.0, 1.0))
        except Exception:
            pass
        normalized_sketches.append(normalized_entry)
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

EKSTRA FAGRUTINER SOM MÅ FØLGES:
1. Skill tydelig mellom papirramme / titleblock / naboplan / situasjonsinnstikk og selve byggets plan. Papirkanter, tegningsrammer, tittelfelt, utsnittsbokser, terrasseomriss, nabobygg og cropkanter er IKKE bæresystem.
2. Hvis én tegningsside inneholder flere planvarianter side om side, skal du identifisere riktig planområde og returnere plan_bbox for hver skisse. plan_bbox skal avgrense selve planutsnittet, ikke hele siden.
3. Yttervegg er ikke automatisk bærende. Ikke marker hele byggets perimeter som bærevegger med mindre tegningen tydelig viser en kontinuerlig massiv yttervegg som faktisk er del av primær bærestruktur.
4. For boligplaner i overetasjer skal du prioritere kjerner, korridorvegger og leilighetsskillevegger før du eventuelt tar med perimetervegger.
5. For P-kjeller / transfer-nivå skal søyler bare brukes der det er sannsynlig lastnedføring fra overliggende vegger eller kjerner. Unngå generiske rutenett som ikke er begrunnet i planen.
6. Når du er usikker, skal du returnere færre elementer, ikke flere. Det er bedre å utelate enn å finne på.
7. Aldri tegn et fullstendig rektangel rundt hele planområdet som "bærevegger" bare fordi konturen er tydelig.
8. Koordinater skal være normaliserte mellom 0 og 1 relativt til HELE siden.
9. Maks 3 sketch-sider. Maks 1 plan_bbox per skisse.
10. Vær eksplisitt om usikkerhet i observasjoner og mangler.

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
      "plan_bbox": {{"x": 0.10, "y": 0.12, "w": 0.55, "h": 0.68}},
      "confidence": 0.0,
      "notes": ["kort note om valg av system, lastvei eller risiko"],
      "elements": [
        {{"type": "column", "x": 0.1, "y": 0.2, "label": "C1"}},
        {{"type": "core", "x": 0.45, "y": 0.35, "w": 0.14, "h": 0.18, "label": "K1"}},
        {{"type": "wall", "x1": 0.2, "y1": 0.2, "x2": 0.2, "y2": 0.7, "label": "Skive"}},
        {{"type": "beam", "x1": 0.1, "y1": 0.2, "x2": 0.75, "y2": 0.2, "label": "Primærdrager"}},
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

    raw_text = generate_text(model, [prompt] + ai_images, temperature=0.08)
    parsed = safe_json_loads(raw_text)
    normalized = normalize_analysis_result(parsed, candidates, drawings)
    return normalized


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
        "Last opp plan, snitt, fasader og gjerne IFC/DXF/DWG. Agenten prioriterer IFC der det finnes, bruker PDF/bilder som fallback og legger foreslått bæresystem som punkter, kjerner og spennretninger på de mest relevante sidene."
    )

    saved_drawings = load_saved_project_drawings()
    if saved_drawings:
        st.success(f"📎 Fant {len(saved_drawings)} tegningsbilder fra Project Setup / lagret prosjektgrunnlag.")
        preview_cols = st.columns(min(3, len(saved_drawings)))
        for idx, record in enumerate(saved_drawings[:3]):
            with preview_cols[idx % len(preview_cols)]:
                st.image(record["image"], caption=f"{record['name']} ({record['hint']})", use_container_width=True)
    else:
        st.info("Ingen lagrede tegninger fra Project Setup ble funnet. Du kan fortsatt laste opp IFC, PDF, DXF eller DWG manuelt her.")

    files = st.file_uploader(
        "Last opp arkitekttegninger / snitt / PDF-er / IFC / DXF / DWG / ZIP",
        accept_multiple_files=True,
        type=["png", "jpg", "jpeg", "webp", "pdf", "ifc", "ifczip", "dxf", "dwg", "zip"],
    )
    st.caption("Tips ved 400-feil i Streamlit/Render: bruk ASCII-filnavn uten æ/ø/å og last gjerne opp IFC/DWG samlet i en ZIP med enkelt navn, f.eks. prosjekt_upload.zip.")

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


# ------------------------------------------------------------
# 13A. GEOMETRIFORANKRET SKISSEMOTOR OG REDIGERING FØR LÅSING
# ------------------------------------------------------------
def optional_cv_stack():
    try:
        import cv2
        import numpy as np
        return cv2, np
    except Exception:
        return None, None


def optional_plotly_go():
    try:
        import plotly.graph_objects as go
        return go
    except Exception:
        return None


_EDITOR_COMPONENT_CACHE: Dict[str, Any] = {}


def draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    start: Tuple[float, float],
    end: Tuple[float, float],
    fill,
    width: int = 3,
    dash_length: int = 12,
    gap_length: int = 7,
) -> None:
    x1, y1 = float(start[0]), float(start[1])
    x2, y2 = float(end[0]), float(end[1])
    total = math.hypot(x2 - x1, y2 - y1)
    if total <= 1:
        draw.line((x1, y1, x2, y2), fill=fill, width=width)
        return
    dx = (x2 - x1) / total
    dy = (y2 - y1) / total
    distance = 0.0
    while distance < total:
        seg_end = min(total, distance + dash_length)
        sx = x1 + dx * distance
        sy = y1 + dy * distance
        ex = x1 + dx * seg_end
        ey = y1 + dy * seg_end
        draw.line((sx, sy, ex, ey), fill=fill, width=width)
        distance += dash_length + gap_length


def build_editor_crop_overlay_image(
    drawing_record: Dict[str, Any],
    sketch: Dict[str, Any],
    show_guides: bool = True,
) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    geometry = get_geometry_for_sketch(drawing_record, sketch)
    image_w, image_h = drawing_record["image"].size
    if geometry:
        region_bbox = geometry["bbox_px"]
    else:
        region_bbox = norm_bbox_to_px(sketch.get("plan_bbox"), image_w, image_h)

    rx, ry, rw, rh = region_bbox
    base_crop = copy_rgb(drawing_record["image"]).crop((rx, ry, rx + rw, ry + rh)).convert("RGBA")
    overlay = Image.new("RGBA", base_crop.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    min_dim = max(1, min(rw, rh))
    font_small = get_font(max(14, int(min_dim * 0.028)), bold=False)
    font_micro = get_font(max(12, int(min_dim * 0.022)), bold=False)
    border_radius = max(10, int(min_dim * 0.03))

    draw.rounded_rectangle(
        (3, 3, max(4, rw - 4), max(4, rh - 4)),
        radius=border_radius,
        outline=(56, 194, 201, 190),
        width=2,
    )

    if show_guides and geometry:
        for seg in geometry.get("vertical_segments", []):
            if float(seg.get("length", 0.0)) < max(20, int(rh * 0.08)):
                continue
            draw_dashed_line(
                draw,
                (float(seg["center"]), float(seg["y"])),
                (float(seg["center"]), float(seg["y"] + seg["h"])),
                fill=(88, 120, 180, 92),
                width=2,
                dash_length=10,
                gap_length=6,
            )
        for seg in geometry.get("horizontal_segments", []):
            if float(seg.get("length", 0.0)) < max(20, int(rw * 0.08)):
                continue
            draw_dashed_line(
                draw,
                (float(seg["x"]), float(seg["center"])),
                (float(seg["x"] + seg["w"]), float(seg["center"])),
                fill=(60, 170, 110, 88),
                width=2,
                dash_length=10,
                gap_length=6,
            )
        core_x, core_y, core_w, core_h = geometry.get("core_bbox", (0, 0, 0, 0))
        if core_w > 0 and core_h > 0:
            draw.rounded_rectangle(
                (core_x, core_y, core_x + core_w, core_y + core_h),
                radius=max(8, int(min_dim * 0.02)),
                outline=(255, 196, 64, 160),
                fill=(255, 196, 64, 32),
                width=2,
            )
        junction_r = max(3, int(min_dim * 0.008))
        for item in geometry.get("junctions", [])[:64]:
            x = float(item.get("x", 0.0))
            y = float(item.get("y", 0.0))
            draw.ellipse(
                (x - junction_r, y - junction_r, x + junction_r, y + junction_r),
                fill=(56, 194, 201, 110),
            )

    for element in sketch.get("elements", []):
        e_type = clean_pdf_text(element.get("type", "")).lower()

        if e_type == "column":
            x_local, y_local = page_norm_to_local_crop(
                float(element.get("x", 0.0)),
                float(element.get("y", 0.0)),
                drawing_record["image"].size,
                region_bbox,
            )
            r = max(8, int(min_dim * 0.018))
            draw.ellipse(
                (x_local - r, y_local - r, x_local + r, y_local + r),
                fill=OVERLAY_COLORS["column"],
                outline=OVERLAY_COLORS["white"],
                width=2,
            )
            label = clean_pdf_text(element.get("label", ""))
            if label:
                label_x = int(clamp(x_local + r + 6, 6, max(8, rw - 120)))
                label_y = int(clamp(y_local - r - 14, 6, max(8, rh - 34)))
                draw_label(draw, (label_x, label_y), short_text(label, 12), font_micro, OVERLAY_COLORS["dark"], OVERLAY_COLORS["white"])

        elif e_type == "core":
            x_local, y_local = page_norm_to_local_crop(
                float(element.get("x", 0.0)),
                float(element.get("y", 0.0)),
                drawing_record["image"].size,
                region_bbox,
            )
            w_local = float(element.get("w", 0.0)) * image_w
            h_local = float(element.get("h", 0.0)) * image_h
            left = clamp(x_local, 0, max(rw - 2, 1))
            top = clamp(y_local, 0, max(rh - 2, 1))
            right = clamp(x_local + w_local, left + 2, max(rw - 1, left + 2))
            bottom = clamp(y_local + h_local, top + 2, max(rh - 1, top + 2))
            draw.rounded_rectangle(
                (left, top, right, bottom),
                radius=max(8, int(min_dim * 0.02)),
                fill=OVERLAY_COLORS["core_fill"],
                outline=OVERLAY_COLORS["core_stroke"],
                width=3,
            )
            draw_label(
                draw,
                (int(clamp(left + 8, 6, max(8, rw - 130))), int(clamp(top + 8, 6, max(8, rh - 30)))),
                short_text(clean_pdf_text(element.get("label", "Kjerne")) or "Kjerne", 18),
                font_micro,
                (255, 196, 64, 235),
                (30, 30, 30, 255),
            )

        elif e_type in {"wall", "beam", "span_arrow"}:
            x1_local, y1_local = page_norm_to_local_crop(
                float(element.get("x1", 0.0)),
                float(element.get("y1", 0.0)),
                drawing_record["image"].size,
                region_bbox,
            )
            x2_local, y2_local = page_norm_to_local_crop(
                float(element.get("x2", 0.0)),
                float(element.get("y2", 0.0)),
                drawing_record["image"].size,
                region_bbox,
            )
            if e_type == "wall":
                draw.line(
                    (x1_local, y1_local, x2_local, y2_local),
                    fill=OVERLAY_COLORS["wall"],
                    width=max(6, int(min_dim * 0.014)),
                )
                label_fill = (255, 153, 153, 225)
            elif e_type == "beam":
                draw.line(
                    (x1_local, y1_local, x2_local, y2_local),
                    fill=OVERLAY_COLORS["beam"],
                    width=max(5, int(min_dim * 0.011)),
                )
                label_fill = (10, 22, 35, 220)
            else:
                draw_arrow(
                    draw,
                    (int(x1_local), int(y1_local)),
                    (int(x2_local), int(y2_local)),
                    OVERLAY_COLORS["span"],
                    width=max(4, int(min_dim * 0.009)),
                )
                label_fill = (196, 235, 176, 230)

            label = clean_pdf_text(element.get("label", ""))
            if label:
                mid_x = int((x1_local + x2_local) / 2.0)
                mid_y = int((y1_local + y2_local) / 2.0)
                draw_label(
                    draw,
                    (
                        int(clamp(mid_x + 6, 6, max(8, rw - 140))),
                        int(clamp(mid_y - 24, 6, max(8, rh - 30))),
                    ),
                    short_text(label, 22),
                    font_micro,
                    label_fill,
                    OVERLAY_COLORS["white"] if e_type == "beam" else (30, 30, 30, 255),
                )

        elif e_type == "grid":
            orientation = clean_pdf_text(element.get("orientation", "")).lower()
            label = clean_pdf_text(element.get("label", ""))
            if orientation.startswith("v"):
                x_local, _ = page_norm_to_local_crop(
                    float(element.get("x", 0.0)),
                    0.0,
                    drawing_record["image"].size,
                    region_bbox,
                )
                draw_dashed_line(draw, (x_local, 0), (x_local, rh), fill=OVERLAY_COLORS["grid"], width=2, dash_length=8, gap_length=6)
                if label:
                    draw_label(draw, (int(clamp(x_local + 4, 4, max(8, rw - 42))), 6), short_text(label, 6), font_micro, OVERLAY_COLORS["dark"], OVERLAY_COLORS["white"])
            else:
                _, y_local = page_norm_to_local_crop(
                    0.0,
                    float(element.get("y", 0.0)),
                    drawing_record["image"].size,
                    region_bbox,
                )
                draw_dashed_line(draw, (0, y_local), (rw, y_local), fill=OVERLAY_COLORS["grid"], width=2, dash_length=8, gap_length=6)
                if label:
                    draw_label(draw, (6, int(clamp(y_local + 4, 4, max(8, rh - 30)))), short_text(label, 6), font_micro, OVERLAY_COLORS["dark"], OVERLAY_COLORS["white"])

    editor_img = Image.alpha_composite(base_crop, overlay).convert("RGB")
    return editor_img, region_bbox


def ensure_inline_click_canvas_component_dir() -> Optional[Path]:
    if components is None:
        return None
    component_dir = DB_DIR / "_inline_components" / "rib_click_canvas_v5"
    component_dir.mkdir(parents=True, exist_ok=True)
    index_path = component_dir / "index.html"
    version_marker = "Builtly RIB click canvas v5"
    html = """<!DOCTYPE html>
<html lang="no">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    html, body {
      margin: 0;
      padding: 0;
      background: transparent;
      overflow: hidden;
    }
    #wrap {
      width: 100%;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    #canvas {
      width: 100%;
      height: auto;
      display: block;
      border-radius: 12px;
      cursor: crosshair;
      background: #07111a;
      box-shadow: inset 0 0 0 1px rgba(120,145,170,0.22);
      touch-action: none;
    }
    #status {
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 12px;
      line-height: 1.4;
      color: rgba(207, 218, 227, 0.74);
      padding: 0 2px 4px 2px;
    }
  </style>
</head>
<body>
  <div id="wrap">
    <canvas id="canvas"></canvas>
    <div id="status">Klikk i planutsnittet for å redigere bæresystemet.</div>
  </div>

  <script>
    (function() {
      const root = document.getElementById("wrap");
      const canvas = document.getElementById("canvas");
      const ctx = canvas.getContext("2d");
      const status = document.getElementById("status");

      let currentImage = null;
      let argsState = {};
      let lastArgsSignature = "";

      function sendMessage(type, data) {
        const payload = Object.assign({isStreamlitMessage: true, type: type}, data || {});
        window.parent.postMessage(payload, "*");
      }

      function setComponentReady() {
        sendMessage("streamlit:componentReady", {apiVersion: 1});
      }

      function setFrameHeight() {
        const height = Math.ceil(root.getBoundingClientRect().height + 4);
        sendMessage("streamlit:setFrameHeight", {height: height});
      }

      function setComponentValue(value) {
        sendMessage("streamlit:setComponentValue", {value: value});
      }

      function clamp(value, minValue, maxValue) {
        return Math.max(minValue, Math.min(maxValue, value));
      }

      function drawPlaceholder(message) {
        const w = Number(argsState.natural_width || 960);
        const h = Number(argsState.natural_height || argsState.desired_height || 540);
        canvas.width = Math.max(32, w);
        canvas.height = Math.max(32, h);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = "#07111a";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = "#dbe7f0";
        ctx.font = "16px sans-serif";
        ctx.fillText(message || "Laster editor...", 24, 34);
        setFrameHeight();
      }

      function drawCurrentImage() {
        if (!currentImage) {
          drawPlaceholder("Laster editor...");
          return;
        }
        const w = currentImage.naturalWidth || currentImage.width || Number(argsState.natural_width || 960);
        const h = currentImage.naturalHeight || currentImage.height || Number(argsState.natural_height || 540);
        canvas.width = Math.max(32, w);
        canvas.height = Math.max(32, h);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(currentImage, 0, 0, canvas.width, canvas.height);

        if (argsState.last_click && typeof argsState.last_click.x === "number" && typeof argsState.last_click.y === "number") {
          const x = clamp(argsState.last_click.x, 0, canvas.width);
          const y = clamp(argsState.last_click.y, 0, canvas.height);
          ctx.save();
          ctx.strokeStyle = "rgba(255,255,255,0.96)";
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(x, y, 10, 0, Math.PI * 2);
          ctx.stroke();
          ctx.beginPath();
          ctx.moveTo(x - 14, y);
          ctx.lineTo(x + 14, y);
          ctx.moveTo(x, y - 14);
          ctx.lineTo(x, y + 14);
          ctx.stroke();
          ctx.restore();
        }
        setFrameHeight();
      }

      function applyArgs(nextArgs) {
        argsState = nextArgs || {};
        status.textContent = argsState.status_text || "Klikk i planutsnittet for å redigere bæresystemet.";
        const sig = JSON.stringify({
          len: argsState.image_data ? argsState.image_data.length : 0,
          head: argsState.image_data ? argsState.image_data.slice(0, 80) : "",
          marker: argsState.version_marker || "",
          width: argsState.natural_width || 0,
          height: argsState.natural_height || 0,
          click: argsState.last_click || null,
        });
        if (sig === lastArgsSignature && currentImage) {
          drawCurrentImage();
          return;
        }
        lastArgsSignature = sig;

        if (!argsState.image_data) {
          currentImage = null;
          drawPlaceholder("Fant ikke editorbildet.");
          return;
        }

        const img = new Image();
        img.onload = function() {
          currentImage = img;
          drawCurrentImage();
        };
        img.onerror = function() {
          currentImage = null;
          drawPlaceholder("Klarte ikke å laste editorbildet.");
        };
        img.src = argsState.image_data;
      }

      function readPointFromEvent(event) {
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / Math.max(rect.width, 1);
        const scaleY = canvas.height / Math.max(rect.height, 1);
        const clientX = event.touches && event.touches.length ? event.touches[0].clientX : event.clientX;
        const clientY = event.touches && event.touches.length ? event.touches[0].clientY : event.clientY;
        const x = clamp((clientX - rect.left) * scaleX, 0, canvas.width);
        const y = clamp((clientY - rect.top) * scaleY, 0, canvas.height);
        return {x: x, y: y};
      }

      function commitClick(point) {
        argsState = argsState || {};
        argsState.last_click = {x: point.x, y: point.y};
        drawCurrentImage();
        const eventId = String(Date.now()) + "-" + Math.random().toString(36).slice(2, 8);
        setComponentValue({
          x: Number(point.x.toFixed(2)),
          y: Number(point.y.toFixed(2)),
          event_id: eventId
        });
      }

      canvas.addEventListener("click", function(event) {
        commitClick(readPointFromEvent(event));
      });

      canvas.addEventListener("touchstart", function(event) {
        event.preventDefault();
        commitClick(readPointFromEvent(event));
      }, {passive: false});

      window.addEventListener("resize", function() {
        setFrameHeight();
      });

      window.addEventListener("message", function(event) {
        const data = event.data;
        if (!data || data.type !== "streamlit:render") {
          return;
        }
        applyArgs(data.args || {});
      });

      setComponentReady();
      drawPlaceholder("Laster editor...");
    })();
  </script>
</body>
</html>
"""
    if (not index_path.exists()) or (version_marker not in index_path.read_text(encoding="utf-8", errors="ignore")):
        index_path.write_text(html, encoding="utf-8")
    return component_dir


def ensure_inline_component_bridge_module() -> Optional[Path]:
    if components is None:
        return None
    bridge_dir = DB_DIR / "_inline_components"
    bridge_dir.mkdir(parents=True, exist_ok=True)
    bridge_path = bridge_dir / "_rib_component_bridge_v5.py"
    bridge_marker = "Builtly RIB component bridge v5"
    bridge_code = """# Builtly RIB component bridge v5
from pathlib import Path

import streamlit.components.v1 as components

_COMPONENT_CACHE = {}


def get_declared_component(name: str, path: str):
    resolved_path = str(Path(path).resolve())
    cache_key = (name, resolved_path)
    if cache_key not in _COMPONENT_CACHE:
        _COMPONENT_CACHE[cache_key] = components.declare_component(name, path=resolved_path)
    return _COMPONENT_CACHE[cache_key]
"""
    if (not bridge_path.exists()) or (bridge_marker not in bridge_path.read_text(encoding="utf-8", errors="ignore")):
        bridge_path.write_text(bridge_code, encoding="utf-8")
    return bridge_path


def load_inline_component_bridge_module() -> Any:
    bridge_path = ensure_inline_component_bridge_module()
    if bridge_path is None:
        return None
    module_name = "_builtly_rib_component_bridge_v5"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(module_name, bridge_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def get_inline_click_canvas_component():
    if components is None:
        return None
    component_dir = ensure_inline_click_canvas_component_dir()
    if component_dir is None:
        return None
    bridge_module = load_inline_component_bridge_module()
    if bridge_module is None:
        st.session_state["rib_click_canvas_error"] = "Kunne ikke laste bro-modul for Streamlit-komponenten."
        return None
    cache_key = str(component_dir.resolve())
    if cache_key not in _EDITOR_COMPONENT_CACHE:
        try:
            _EDITOR_COMPONENT_CACHE[cache_key] = bridge_module.get_declared_component(
                "rib_click_canvas_v5",
                str(component_dir.resolve()),
            )
            st.session_state.pop("rib_click_canvas_error", None)
        except Exception as exc:
            st.session_state["rib_click_canvas_error"] = short_text(f"{type(exc).__name__}: {exc}", 240)
            _EDITOR_COMPONENT_CACHE[cache_key] = None
    return _EDITOR_COMPONENT_CACHE.get(cache_key)


def render_inline_click_canvas_editor(
    drawing_record: Dict[str, Any],
    sketch: Dict[str, Any],
    editor_key: str,
) -> Optional[Dict[str, float]]:
    component = get_inline_click_canvas_component()
    if component is None:
        st.info("Klikk-editoren kunne ikke startes i dette miljøet. Bruk tabellredigeringen under som fallback.")
        error_text = safe_session_state_get("rib_click_canvas_error", "")
        if error_text:
            st.caption(f"Teknisk info: {error_text}")
        return None

    show_guides = bool(st.session_state.get("rib_editor_show_guides", True))
    editor_img, region_bbox = build_editor_crop_overlay_image(drawing_record, sketch, show_guides=show_guides)
    rw, rh = editor_img.size
    marker_key = f"{editor_key}_canvas_last_click"
    last_click = st.session_state.get(marker_key)
    if not isinstance(last_click, dict):
        last_click = None

    status_text = (
        "Innebygd canvas-editor er aktiv. Klikk direkte i planutsnittet for å legge inn korrigeringer."
    )
    try:
        value = component(
            image_data=editor_image_data_uri(editor_img),
            natural_width=rw,
            natural_height=rh,
            desired_height=int(min(980, max(420, rh / max(rw, 1) * 920))),
            status_text=status_text,
            version_marker=f"{st.session_state.get('rib_draft_updated_at', '')}|{st.session_state.get('rib_editor_show_guides', True)}",
            last_click=last_click,
            key=f"{editor_key}_canvas",
            default=None,
        )
    except Exception as exc:
        st.session_state["rib_click_canvas_error"] = short_text(f"{type(exc).__name__}: {exc}", 240)
        st.info("Klikk-editoren kunne ikke rendres. Bruk tabellredigeringen under som fallback.")
        st.caption(f"Teknisk info: {st.session_state['rib_click_canvas_error']}")
        return None

    st.caption("Innebygd canvas-editor er aktiv i stedet for Plotly. Klikk direkte i planutsnittet.")

    if isinstance(value, dict) and "x" in value and "y" in value:
        click = {
            "x": float(value.get("x", 0.0)),
            "y": float(value.get("y", 0.0)),
            "event_id": clean_pdf_text(value.get("event_id", "")),
        }
        st.session_state[marker_key] = {"x": click["x"], "y": click["y"]}
        return click
    return None


def click_event_signature(selected_sketch_uid: str, tool: str, click: Dict[str, Any]) -> str:
    if not isinstance(click, dict):
        return f"{selected_sketch_uid}|{tool}|none"
    event_id = clean_pdf_text(click.get("event_id", ""))
    if event_id:
        return f"{selected_sketch_uid}|{tool}|{event_id}"
    return (
        f"{selected_sketch_uid}|{tool}|"
        f"{round(float(click.get('x', 0.0)), 1)}|{round(float(click.get('y', 0.0)), 1)}"
    )


def deep_copy_jsonable(data: Any) -> Any:
    try:
        return json.loads(json.dumps(data))
    except Exception:
        return data


def editor_image_data_uri(img: Image.Image) -> str:
    return f"data:image/png;base64,{base64.b64encode(png_bytes_from_image(copy_rgb(img))).decode('utf-8')}"


def sketch_uid(sketch: Dict[str, Any]) -> str:
    return f"page_{int(sketch.get('page_index', 0))}_region_{int(sketch.get('region_index', 0))}"


def bbox_area(bbox: Tuple[int, int, int, int]) -> int:
    return max(0, int(bbox[2])) * max(0, int(bbox[3]))


def bbox_center(bbox: Tuple[int, int, int, int]) -> Tuple[float, float]:
    return float(bbox[0] + bbox[2] / 2.0), float(bbox[1] + bbox[3] / 2.0)


def bbox_iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    union = bbox_area(a) + bbox_area(b) - inter
    return (inter / union) if union else 0.0


def bbox_contains(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int], min_cover: float = 0.9) -> bool:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    return inter >= bbox_area(b) * min_cover if bbox_area(b) else False


def px_bbox_to_norm(bbox: Tuple[int, int, int, int], width: int, height: int) -> Dict[str, float]:
    x, y, w, h = bbox
    return {
        "x": round(clamp(x / max(width, 1), 0.0, 1.0), 6),
        "y": round(clamp(y / max(height, 1), 0.0, 1.0), 6),
        "w": round(clamp(w / max(width, 1), 0.0, 1.0), 6),
        "h": round(clamp(h / max(height, 1), 0.0, 1.0), 6),
    }


def norm_bbox_to_px(norm_bbox: Optional[Dict[str, Any]], width: int, height: int) -> Tuple[int, int, int, int]:
    if not isinstance(norm_bbox, dict):
        return (0, 0, width, height)
    x = int(clamp(float(norm_bbox.get("x", 0.0)), 0.0, 1.0) * width)
    y = int(clamp(float(norm_bbox.get("y", 0.0)), 0.0, 1.0) * height)
    w = int(clamp(float(norm_bbox.get("w", 1.0)), 0.02, 1.0) * width)
    h = int(clamp(float(norm_bbox.get("h", 1.0)), 0.02, 1.0) * height)
    return (x, y, w, h)


def local_point_to_page_norm(
    region_bbox_px: Tuple[int, int, int, int],
    local_x: float,
    local_y: float,
    image_w: int,
    image_h: int,
) -> Tuple[float, float]:
    rx, ry, rw, rh = region_bbox_px
    px = rx + clamp(local_x, 0, max(rw - 1, 1))
    py = ry + clamp(local_y, 0, max(rh - 1, 1))
    return (
        round(clamp(px / max(image_w, 1), 0.02, 0.98), 6),
        round(clamp(py / max(image_h, 1), 0.02, 0.98), 6),
    )


def page_norm_to_px(x_norm: float, y_norm: float, image_w: int, image_h: int) -> Tuple[float, float]:
    return float(x_norm) * image_w, float(y_norm) * image_h


def alpha_grid_label(index: int) -> str:
    index = max(1, int(index))
    result = ""
    current = index
    while current > 0:
        current -= 1
        result = chr(65 + (current % 26)) + result
        current //= 26
    return result or "A"


def integral_rect_sum(ii, x: int, y: int, w: int, h: int) -> int:
    x1 = max(0, int(x))
    y1 = max(0, int(y))
    x2 = max(x1, int(x + max(w, 1) - 1))
    y2 = max(y1, int(y + max(h, 1) - 1))
    x2 = min(x2, ii.shape[1] - 2)
    y2 = min(y2, ii.shape[0] - 2)
    return int(ii[y2 + 1, x2 + 1] - ii[y1, x2 + 1] - ii[y2 + 1, x1] + ii[y1, x1])


def collect_bbox_candidates_from_mask(
    mask,
    origin: Tuple[int, int] = (0, 0),
    min_area_ratio: float = 0.02,
    max_area_ratio: float = 0.9,
    kernel_px: int = 21,
) -> List[Dict[str, Any]]:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None:
        return []
    height, width = mask.shape[:2]
    kernel_px = max(5, int(kernel_px))
    closed = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_px, kernel_px)),
    )
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    out: List[Dict[str, Any]] = []
    ox, oy = origin
    total_area = float(max(width * height, 1))
    for idx in range(1, num_labels):
        x, y, w, h, area = stats[idx]
        area_ratio = float(area) / total_area
        aspect = float(w) / float(max(h, 1))
        if area_ratio < min_area_ratio or area_ratio > max_area_ratio:
            continue
        if w < width * 0.12 or h < height * 0.12:
            continue
        if aspect < 0.35 or aspect > 2.6:
            continue
        density = float(mask[y:y + h, x:x + w].mean()) / 255.0
        if density < 0.015:
            continue
        bbox = (int(ox + x), int(oy + y), int(w), int(h))
        out.append(
            {
                "bbox_px": bbox,
                "area": int(area),
                "density": density,
            }
        )
    return out


def dedupe_region_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = sorted(candidates, key=lambda item: item.get("score", 0.0), reverse=True)
    keep: List[Dict[str, Any]] = []
    for candidate in ranked:
        bbox = candidate["bbox_px"]
        area = bbox_area(bbox)
        skip = False
        for existing in keep:
            existing_bbox = existing["bbox_px"]
            existing_area = bbox_area(existing_bbox)
            if bbox_iou(bbox, existing_bbox) > 0.65:
                skip = True
                break
            if bbox_contains(existing_bbox, bbox, 0.92) and existing_area <= area * 1.4:
                skip = True
                break
            if bbox_contains(bbox, existing_bbox, 0.96) and area >= existing_area * 1.8:
                skip = True
                break
        if not skip:
            keep.append(candidate)
    final_keep: List[Dict[str, Any]] = []
    for candidate in keep:
        bbox = candidate["bbox_px"]
        area = bbox_area(bbox)
        if any(
            other is not candidate
            and bbox_contains(bbox, other["bbox_px"], 0.96)
            and area >= bbox_area(other["bbox_px"]) * 2.0
            for other in keep
        ):
            continue
        final_keep.append(candidate)
    return final_keep


def detect_plan_regions_grounded(image: Image.Image) -> List[Dict[str, Any]]:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None:
        return []
    arr = np.array(copy_rgb(image))
    height, width = arr.shape[:2]
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    base_mask = ((gray < 245).astype(np.uint8)) * 255

    base_candidates = collect_bbox_candidates_from_mask(
        base_mask,
        origin=(0, 0),
        min_area_ratio=0.02,
        max_area_ratio=0.9,
        kernel_px=max(11, int(min(width, height) * 0.018)),
    )

    nested_candidates: List[Dict[str, Any]] = []
    for candidate in base_candidates:
        x, y, w, h = candidate["bbox_px"]
        area_ratio = float(w * h) / float(max(width * height, 1))
        if area_ratio < 0.24 and not (w > width * 0.65 and h > height * 0.34):
            continue
        crop_mask = base_mask[y:y + h, x:x + w]
        nested_candidates.extend(
            collect_bbox_candidates_from_mask(
                crop_mask,
                origin=(x, y),
                min_area_ratio=0.03,
                max_area_ratio=0.55,
                kernel_px=max(9, int(min(w, h) * 0.025)),
            )
        )

    candidates = base_candidates + nested_candidates
    enriched: List[Dict[str, Any]] = []
    for candidate in candidates:
        bbox = candidate["bbox_px"]
        x, y, w, h = bbox
        cx, cy = bbox_center(bbox)
        centrality = max(
            0.15,
            1.0 - abs((cx / max(width, 1)) - 0.5) * 0.8 - abs((cy / max(height, 1)) - 0.45) * 1.0,
        )
        titleblock_penalty = 0.55 if (x > width * 0.72 and y > height * 0.50) else 1.0
        bottom_penalty = 0.60 if y > height * 0.78 else 1.0
        huge_penalty = 0.45 if (w > width * 0.88 and h > height * 0.72) else 1.0
        candidate["score"] = float(w * h) * candidate.get("density", 0.1) * centrality * titleblock_penalty * bottom_penalty * huge_penalty
        candidate["bbox_norm"] = px_bbox_to_norm(bbox, width, height)
        enriched.append(candidate)

    deduped = dedupe_region_candidates(enriched)
    deduped.sort(key=lambda item: item.get("score", 0.0), reverse=True)

    if not deduped and width > 0 and height > 0:
        fallback_bbox = (
            int(width * 0.10),
            int(height * 0.14),
            int(width * 0.80),
            int(height * 0.68),
        )
        return [{"bbox_px": fallback_bbox, "bbox_norm": px_bbox_to_norm(fallback_bbox, width, height), "score": 1.0}]

    return deduped[:3]


def build_plan_footprint_from_binary(bw) -> Any:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None:
        return bw
    height, width = bw.shape[:2]
    closed = cv2.morphologyEx(
        bw,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)),
    )
    dilated = cv2.dilate(
        closed,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
        iterations=1,
    )
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(dilated, 8)
    footprint = np.zeros_like(dilated)
    area_threshold = max(80, int(width * height * 0.004))
    for idx in range(1, num_labels):
        x, y, w, h, area = stats[idx]
        if int(area) >= area_threshold:
            footprint[labels == idx] = 255
    footprint = cv2.morphologyEx(
        footprint,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11)),
    )
    return footprint


def extract_linear_segments(mask, orientation: str, min_len: int) -> List[Dict[str, Any]]:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None:
        return []
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    segments: List[Dict[str, Any]] = []
    for idx in range(1, num_labels):
        x, y, w, h, area = stats[idx]
        if orientation == "vertical":
            length = int(h)
            thickness = int(w)
            center = float(x + w / 2.0)
            if length < min_len or thickness > max(18, int(mask.shape[1] * 0.18)):
                continue
        else:
            length = int(w)
            thickness = int(h)
            center = float(y + h / 2.0)
            if length < min_len or thickness > max(18, int(mask.shape[0] * 0.18)):
                continue
        segments.append(
            {
                "kind": orientation,
                "x": int(x),
                "y": int(y),
                "w": int(w),
                "h": int(h),
                "length": int(length),
                "thickness": int(thickness),
                "center": center,
            }
        )
    return segments


def cluster_weighted_positions(points: List[Tuple[float, float]], tolerance: float) -> List[Dict[str, Any]]:
    if not points:
        return []
    ordered = sorted((float(pos), float(weight)) for pos, weight in points)
    clusters: List[Dict[str, Any]] = []
    for pos, weight in ordered:
        if not clusters or abs(pos - clusters[-1]["pos"]) > tolerance:
            clusters.append(
                {
                    "pos": pos,
                    "sum": pos * weight,
                    "weight": max(weight, 1.0),
                    "count": 1,
                }
            )
        else:
            cluster = clusters[-1]
            cluster["sum"] += pos * weight
            cluster["weight"] += max(weight, 1.0)
            cluster["count"] += 1
            cluster["pos"] = cluster["sum"] / max(cluster["weight"], 1e-6)
    for cluster in clusters:
        cluster["pos"] = float(cluster["pos"])
    return clusters


def choose_grid_lines_from_junctions(
    junctions: List[Dict[str, Any]],
    axis: str,
    extent: int,
    target: int,
) -> List[Dict[str, Any]]:
    if not junctions:
        return []
    points = [(float(item.get(axis, 0.0)), float(item.get("score", 1.0))) for item in junctions]
    clusters = cluster_weighted_positions(points, tolerance=max(10, int(extent * 0.05)))
    clusters.sort(key=lambda item: (item.get("count", 0), item.get("weight", 0.0)), reverse=True)
    selected: List[Dict[str, Any]] = []
    min_spacing = max(22, int(extent * 0.14))
    for cluster in clusters:
        if all(abs(cluster["pos"] - other["pos"]) >= min_spacing for other in selected):
            selected.append(cluster)
        if len(selected) >= target:
            break
    if len(selected) < 2:
        selected = sorted(clusters, key=lambda item: item["pos"])[: max(2, target)]
    return sorted(selected, key=lambda item: item["pos"])


def point_inside_local_box(x: float, y: float, bbox: Tuple[int, int, int, int], margin: int = 0) -> bool:
    bx, by, bw, bh = bbox
    return (bx - margin) <= x <= (bx + bw + margin) and (by - margin) <= y <= (by + bh + margin)


def build_junction_candidates(
    vertical_segments: List[Dict[str, Any]],
    horizontal_segments: List[Dict[str, Any]],
    footprint_mask,
    core_bbox: Tuple[int, int, int, int],
) -> List[Dict[str, Any]]:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None:
        return []
    footprint = (footprint_mask > 0).astype(np.uint8)
    if footprint.max() <= 0:
        return []
    distance = cv2.distanceTransform(footprint, cv2.DIST_L2, 3)
    candidates: List[Dict[str, Any]] = []
    height, width = footprint.shape[:2]
    for vseg in vertical_segments:
        vx = int(round(vseg["center"]))
        for hseg in horizontal_segments:
            hy = int(round(hseg["center"]))
            if not (hseg["x"] - 3 <= vx <= hseg["x"] + hseg["w"] + 3):
                continue
            if not (vseg["y"] - 3 <= hy <= vseg["y"] + vseg["h"] + 3):
                continue
            if not (0 <= vx < width and 0 <= hy < height):
                continue
            if footprint[hy, vx] <= 0:
                continue
            if point_inside_local_box(vx, hy, core_bbox, margin=8):
                continue
            score = float(vseg["length"] + hseg["length"] + distance[hy, vx] * 0.75)
            candidates.append({"x": float(vx), "y": float(hy), "score": score})
    candidates.sort(key=lambda item: item["score"], reverse=True)
    pruned: List[Dict[str, Any]] = []
    min_dist = max(18, int(min(height, width) * 0.10))
    for candidate in candidates:
        if all(
            ((candidate["x"] - kept["x"]) ** 2 + (candidate["y"] - kept["y"]) ** 2) > (min_dist ** 2)
            for kept in pruned
        ):
            pruned.append(candidate)
        if len(pruned) >= 48:
            break
    return pruned


def snap_edge_to_lines(value: float, lines: List[Dict[str, Any]], max_dist: int) -> float:
    if not lines:
        return value
    nearest = min(lines, key=lambda item: abs(float(item.get("center", item.get("pos", value))) - value))
    nearest_value = float(nearest.get("center", nearest.get("pos", value)))
    return nearest_value if abs(nearest_value - value) <= max_dist else value


def detect_core_bbox_from_geometry(
    width: int,
    height: int,
    line_mask,
    footprint_mask,
    vertical_segments: List[Dict[str, Any]],
    horizontal_segments: List[Dict[str, Any]],
) -> Tuple[int, int, int, int]:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None:
        return (int(width * 0.40), int(height * 0.35), int(width * 0.18), int(height * 0.22))
    footprint = (footprint_mask > 0).astype(np.uint8)
    line_binary = (line_mask > 0).astype(np.uint8)
    if footprint.max() <= 0 or line_binary.max() <= 0:
        return (int(width * 0.40), int(height * 0.35), int(width * 0.18), int(height * 0.22))

    ii_lines = cv2.integral(line_binary)
    ii_foot = cv2.integral(footprint)
    box_w = int(clamp(width * 0.22, max(40, width * 0.15), max(60, width * 0.34)))
    box_h = int(clamp(height * 0.24, max(44, height * 0.16), max(70, height * 0.36)))
    step = max(4, int(min(width, height) * 0.03))
    best: Optional[Tuple[float, int, int]] = None
    center_x = width / 2.0
    center_y = height / 2.0

    max_x = max(2, width - box_w - 2)
    max_y = max(2, height - box_h - 2)
    for x in range(max(2, int(width * 0.10)), max_x + 1, step):
        for y in range(max(2, int(height * 0.10)), max_y + 1, step):
            footprint_cover = integral_rect_sum(ii_foot, x, y, box_w, box_h) / float(max(box_w * box_h, 1))
            if footprint_cover < 0.20:
                continue
            line_score = integral_rect_sum(ii_lines, x, y, box_w, box_h)
            dist = math.hypot((x + box_w / 2.0) - center_x, (y + box_h / 2.0) - center_y)
            score = line_score + footprint_cover * 400.0 - dist * 2.5
            if best is None or score > best[0]:
                best = (score, x, y)

    if best is None:
        x = int(width * 0.40)
        y = int(height * 0.35)
    else:
        _, x, y = best

    x0 = int(x)
    y0 = int(y)
    x1 = int(x0 + box_w)
    y1 = int(y0 + box_h)
    max_snap_x = max(12, int(width * 0.06))
    max_snap_y = max(12, int(height * 0.06))
    x0 = int(snap_edge_to_lines(x0, vertical_segments, max_snap_x))
    x1 = int(snap_edge_to_lines(x1, vertical_segments, max_snap_x))
    y0 = int(snap_edge_to_lines(y0, horizontal_segments, max_snap_y))
    y1 = int(snap_edge_to_lines(y1, horizontal_segments, max_snap_y))

    if x1 <= x0 + 18:
        x0 = int(clamp(x0, 0, width - box_w - 1))
        x1 = x0 + box_w
    if y1 <= y0 + 18:
        y0 = int(clamp(y0, 0, height - box_h - 1))
        y1 = y0 + box_h

    x0 = int(clamp(x0, 2, max(2, width - 24)))
    y0 = int(clamp(y0, 2, max(2, height - 24)))
    x1 = int(clamp(x1, x0 + 18, width - 2))
    y1 = int(clamp(y1, y0 + 18, height - 2))
    return (x0, y0, x1 - x0, y1 - y0)


def build_plan_geometry_grounded(
    image: Image.Image,
    region_bbox_px: Tuple[int, int, int, int],
) -> Dict[str, Any]:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None:
        return {}
    arr = np.array(copy_rgb(image))
    rx, ry, rw, rh = region_bbox_px
    crop = arr[ry:ry + rh, rx:rx + rw]
    if crop.size == 0:
        return {}
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    bw = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        5,
    )
    footprint_mask = build_plan_footprint_from_binary(bw)
    vertical_mask = cv2.morphologyEx(
        bw,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(21, int(rh * 0.06)))),
    )
    horizontal_mask = cv2.morphologyEx(
        bw,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(21, int(rw * 0.06)), 1)),
    )
    vertical_segments = extract_linear_segments(vertical_mask, "vertical", max(28, int(rh * 0.10)))
    horizontal_segments = extract_linear_segments(horizontal_mask, "horizontal", max(28, int(rw * 0.10)))
    line_mask = cv2.bitwise_or(vertical_mask, horizontal_mask)
    core_bbox = detect_core_bbox_from_geometry(
        rw,
        rh,
        line_mask,
        footprint_mask,
        vertical_segments,
        horizontal_segments,
    )
    junctions = build_junction_candidates(vertical_segments, horizontal_segments, footprint_mask, core_bbox)
    grid_x = choose_grid_lines_from_junctions(junctions, "x", rw, target=4 if rw >= rh * 0.85 else 3)
    grid_y = choose_grid_lines_from_junctions(junctions, "y", rh, target=4 if rh > rw * 0.95 else 3)
    return {
        "bbox_px": region_bbox_px,
        "crop_size": (rw, rh),
        "footprint_mask": footprint_mask,
        "vertical_segments": vertical_segments,
        "horizontal_segments": horizontal_segments,
        "junctions": junctions,
        "grid_x": grid_x,
        "grid_y": grid_y,
        "core_bbox": core_bbox,
    }


def structural_mode_from_analysis(analysis_result: Dict[str, Any], material_preference: str) -> str:
    txt = " ".join(
        [
            clean_pdf_text(material_preference),
            clean_pdf_text((analysis_result or {}).get("recommended_system", {}).get("system_name", "")),
            clean_pdf_text((analysis_result or {}).get("recommended_system", {}).get("material", "")),
            clean_pdf_text((analysis_result or {}).get("recommended_system", {}).get("vertical_system", "")),
            clean_pdf_text((analysis_result or {}).get("recommended_system", {}).get("stability_system", "")),
        ]
    ).lower()
    if any(word in txt for word in ["clt", "massivtre", "bærende vegg", "veggsystem", "leilighetsskille"]):
        return "wall_core"
    if any(word in txt for word in ["stål", "hulldekke", "søyle", "søyle-bjelke", "flatdekke", "prefabrikkert betong"]):
        return "column_core"
    if "hybrid" in txt and "betongkjerne" in txt and "tre" in txt:
        return "wall_core"
    if "hybrid" in txt:
        return "column_core"
    return "column_core"


def nearest_junction_to_point(
    x: float,
    y: float,
    junctions: List[Dict[str, Any]],
    max_dist: float,
) -> Optional[Dict[str, Any]]:
    if not junctions:
        return None
    best = min(junctions, key=lambda item: (item["x"] - x) ** 2 + (item["y"] - y) ** 2)
    dist = math.hypot(best["x"] - x, best["y"] - y)
    return best if dist <= max_dist else None


def next_column_label(elements: List[Dict[str, Any]]) -> str:
    used = []
    for element in elements:
        if clean_pdf_text(element.get("type", "")).lower() != "column":
            continue
        match = re.search(r"(\d+)", clean_pdf_text(element.get("label", "")))
        if match:
            used.append(int(match.group(1)))
    next_id = max(used, default=0) + 1
    return f"C{next_id}"


def select_spaced_segments(
    segments: List[Dict[str, Any]],
    region_size: Tuple[int, int],
    axis: str,
    max_items: int,
    core_bbox: Tuple[int, int, int, int],
) -> List[Dict[str, Any]]:
    rw, rh = region_size
    mid_x = rw / 2.0
    mid_y = rh / 2.0
    min_spacing = max(18, int((rw if axis == "vertical" else rh) * 0.12))
    scored: List[Dict[str, Any]] = []
    for segment in segments:
        center = float(segment.get("center", 0.0))
        if axis == "vertical":
            if point_inside_local_box(center, segment["y"] + segment["h"] / 2.0, core_bbox, margin=8):
                continue
            edge_bias = min(center, rw - center)
            score = float(segment["length"]) + edge_bias * 0.10 - abs(center - mid_x) * 0.12
        else:
            if point_inside_local_box(segment["x"] + segment["w"] / 2.0, center, core_bbox, margin=8):
                continue
            edge_bias = min(center, rh - center)
            score = float(segment["length"]) + edge_bias * 0.10 - abs(center - mid_y) * 0.12
        scored.append({"score": score, "segment": segment})
    scored.sort(key=lambda item: item["score"], reverse=True)

    chosen: List[Dict[str, Any]] = []
    for item in scored:
        segment = item["segment"]
        center = float(segment.get("center", 0.0))
        if all(abs(center - float(prev.get("center", 0.0))) >= min_spacing for prev in chosen):
            chosen.append(segment)
        if len(chosen) >= max_items:
            break
    return chosen


def generate_column_core_elements_grounded(
    geometry: Dict[str, Any],
    image_size: Tuple[int, int],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None:
        return [], []
    image_w, image_h = image_size
    region_bbox_px = geometry["bbox_px"]
    rw, rh = geometry["crop_size"]
    footprint = geometry["footprint_mask"]
    core_bbox = geometry["core_bbox"]
    junctions = geometry["junctions"]

    x_lines = choose_grid_lines_from_junctions(junctions, "x", rw, target=4 if rw >= rh * 0.80 else 3)
    y_lines = choose_grid_lines_from_junctions(junctions, "y", rh, target=4 if rh >= rw * 0.95 else 3)

    elements: List[Dict[str, Any]] = []
    for idx, x_line in enumerate(x_lines, start=1):
        x_norm, _ = local_point_to_page_norm(region_bbox_px, x_line["pos"], 0, image_w, image_h)
        elements.append({"type": "grid", "orientation": "vertical", "x": x_norm, "label": alpha_grid_label(idx)})
    for idx, y_line in enumerate(y_lines, start=1):
        _, y_norm = local_point_to_page_norm(region_bbox_px, 0, y_line["pos"], image_w, image_h)
        elements.append({"type": "grid", "orientation": "horizontal", "y": y_norm, "label": str(idx)})

    core_x, core_y, core_w, core_h = core_bbox
    core_x_norm, core_y_norm = local_point_to_page_norm(region_bbox_px, core_x, core_y, image_w, image_h)
    elements.append(
        {
            "type": "core",
            "x": core_x_norm,
            "y": core_y_norm,
            "w": round(clamp(core_w / max(image_w, 1), 0.03, 0.30), 6),
            "h": round(clamp(core_h / max(image_h, 1), 0.03, 0.30), 6),
            "label": "Kjerne",
        }
    )

    candidate_columns: List[Dict[str, Any]] = []
    snap_dist = max(18, int(min(rw, rh) * 0.09))
    for x_line in x_lines:
        for y_line in y_lines:
            snapped = nearest_junction_to_point(x_line["pos"], y_line["pos"], junctions, max_dist=snap_dist)
            if snapped is None:
                px = float(x_line["pos"])
                py = float(y_line["pos"])
            else:
                px = float(snapped["x"])
                py = float(snapped["y"])
            if int(clamp(py, 0, rh - 1)) >= footprint.shape[0] or int(clamp(px, 0, rw - 1)) >= footprint.shape[1]:
                continue
            if footprint[int(py), int(px)] <= 0:
                continue
            if point_inside_local_box(px, py, core_bbox, margin=10):
                continue
            x_norm, y_norm = local_point_to_page_norm(region_bbox_px, px, py, image_w, image_h)
            candidate_columns.append({"type": "column", "x": x_norm, "y": y_norm, "label": ""})

    pruned_columns: List[Dict[str, Any]] = []
    min_px_spacing = max(22, int(min(rw, rh) * 0.14))
    for column in candidate_columns:
        cx, cy = page_norm_to_px(column["x"], column["y"], image_w, image_h)
        if all((cx - px) ** 2 + (cy - py) ** 2 > (min_px_spacing ** 2) for px, py in [
            page_norm_to_px(existing["x"], existing["y"], image_w, image_h) for existing in pruned_columns
        ]):
            pruned_columns.append(column)
        if len(pruned_columns) >= 14:
            break

    pruned_columns.sort(key=lambda item: (item["y"], item["x"]))
    for idx, column in enumerate(pruned_columns, start=1):
        column["label"] = f"C{idx}"
    elements.extend(pruned_columns)

    x_positions = [line["pos"] for line in x_lines]
    y_positions = [line["pos"] for line in y_lines]
    if len(x_positions) >= 2:
        span_y = min(rh - 16, int(core_y + core_h + max(18, rh * 0.08)))
        x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, x_positions[0], span_y, image_w, image_h)
        x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, x_positions[min(1, len(x_positions) - 1)], span_y, image_w, image_h)
        elements.append(
            {
                "type": "span_arrow",
                "x1": x1_norm,
                "y1": y1_norm,
                "x2": x2_norm,
                "y2": y2_norm,
                "label": "Typisk modul",
            }
        )

    if len(x_positions) >= 2 and y_positions:
        beam_y = y_positions[min(len(y_positions) // 2, len(y_positions) - 1)]
        x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, x_positions[0], beam_y, image_w, image_h)
        x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, x_positions[-1], beam_y, image_w, image_h)
        elements.append(
            {
                "type": "beam",
                "x1": x1_norm,
                "y1": y1_norm,
                "x2": x2_norm,
                "y2": y2_norm,
                "label": "Primærretning",
            }
        )

    notes = [
        "Søyler er snappet til kryss mellom detekterte linjeføringer i plan.",
        "Kjerne er plassert i tett, sentral geometri for korte lastveier og tydelig avstivning.",
        "Skissen er geometri-forankret og mindre avhengig av frie AI-koordinater.",
    ]
    return elements, notes


def generate_wall_core_elements_grounded(
    geometry: Dict[str, Any],
    image_size: Tuple[int, int],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    image_w, image_h = image_size
    region_bbox_px = geometry["bbox_px"]
    rw, rh = geometry["crop_size"]
    core_bbox = geometry["core_bbox"]

    elements: List[Dict[str, Any]] = []
    core_x, core_y, core_w, core_h = core_bbox
    core_x_norm, core_y_norm = local_point_to_page_norm(region_bbox_px, core_x, core_y, image_w, image_h)
    elements.append(
        {
            "type": "core",
            "x": core_x_norm,
            "y": core_y_norm,
            "w": round(clamp(core_w / max(image_w, 1), 0.03, 0.30), 6),
            "h": round(clamp(core_h / max(image_h, 1), 0.03, 0.30), 6),
            "label": "Betongkjerne",
        }
    )

    chosen_vertical = select_spaced_segments(
        geometry.get("vertical_segments", []),
        geometry["crop_size"],
        axis="vertical",
        max_items=4,
        core_bbox=core_bbox,
    )
    chosen_horizontal = select_spaced_segments(
        geometry.get("horizontal_segments", []),
        geometry["crop_size"],
        axis="horizontal",
        max_items=4,
        core_bbox=core_bbox,
    )

    wall_count = 1
    for segment in chosen_vertical + chosen_horizontal:
        if segment["kind"] == "vertical":
            x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, segment["center"], segment["y"], image_w, image_h)
            x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, segment["center"], segment["y"] + segment["h"], image_w, image_h)
        else:
            x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, segment["x"], segment["center"], image_w, image_h)
            x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, segment["x"] + segment["w"], segment["center"], image_w, image_h)
        elements.append(
            {
                "type": "wall",
                "x1": x1_norm,
                "y1": y1_norm,
                "x2": x2_norm,
                "y2": y2_norm,
                "label": f"Vegg {wall_count}",
            }
        )
        wall_count += 1

    if geometry.get("junctions"):
        edge_dist = max(22, int(min(rw, rh) * 0.16))
        support_points: List[Dict[str, Any]] = []
        for junction in geometry["junctions"]:
            if point_inside_local_box(junction["x"], junction["y"], core_bbox, margin=10):
                continue
            if all(
                (junction["x"] - other["x"]) ** 2 + (junction["y"] - other["y"]) ** 2 > (edge_dist ** 2)
                for other in support_points
            ):
                support_points.append(junction)
            if len(support_points) >= 4:
                break
        for idx, point in enumerate(sorted(support_points, key=lambda item: (item["y"], item["x"])), start=1):
            x_norm, y_norm = local_point_to_page_norm(region_bbox_px, point["x"], point["y"], image_w, image_h)
            elements.append({"type": "column", "x": x_norm, "y": y_norm, "label": f"P{idx}"})

    span_candidates = [seg for seg in chosen_vertical if seg["length"] >= max(40, int(rh * 0.20))]
    if len(span_candidates) >= 2:
        left = min(span_candidates, key=lambda seg: seg["center"])
        right = max(span_candidates, key=lambda seg: seg["center"])
        arrow_y = min(rh - 16, int(core_y + core_h + max(18, rh * 0.08)))
        x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, left["center"], arrow_y, image_w, image_h)
        x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, right["center"], arrow_y, image_w, image_h)
        elements.append({"type": "span_arrow", "x1": x1_norm, "y1": y1_norm, "x2": x2_norm, "y2": y2_norm, "label": "Veggretning / spenn"})

    notes = [
        "Tolkningen er vegg-/kjernebåret, derfor er søyler bevisst nedtonet til støttepunkter.",
        "Bærende vegger er snappet til lange, detekterte linjeføringer i planen.",
        "Kjerne brukes som hovedavstivning og vertikal lastvei.",
    ]
    return elements, notes


def generate_grounded_sketches(
    drawings: List[Dict[str, Any]],
    analysis_result: Dict[str, Any],
    material_preference: str,
    max_sketches: int = 3,
) -> List[Dict[str, Any]]:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None:
        return []
    strategy = structural_mode_from_analysis(analysis_result, material_preference)
    concept_name = clean_pdf_text(analysis_result.get("recommended_system", {}).get("system_name", "Anbefalt system"))
    sketches: List[Dict[str, Any]] = []

    for record in drawings:
        if record.get("hint") != "plan":
            continue
        regions = detect_plan_regions_grounded(record["image"])
        if not regions:
            width, height = record["image"].size
            full_bbox = (int(width * 0.08), int(height * 0.14), int(width * 0.84), int(height * 0.68))
            regions = [{"bbox_px": full_bbox, "bbox_norm": px_bbox_to_norm(full_bbox, width, height), "score": 1.0}]
        ordered_regions = sorted(regions[:2], key=lambda item: (item["bbox_px"][1], item["bbox_px"][0]))
        for region_index, region in enumerate(ordered_regions):
            geometry = build_plan_geometry_grounded(record["image"], region["bbox_px"])
            if not geometry:
                continue
            if strategy == "wall_core":
                elements, notes = generate_wall_core_elements_grounded(geometry, record["image"].size)
            else:
                elements, notes = generate_column_core_elements_grounded(geometry, record["image"].size)
            if not elements:
                continue
            page_label = clean_pdf_text(record["label"])
            if len(ordered_regions) > 1:
                page_label = f"{page_label} - delplan {region_index + 1}"
            notes = [
                f"Konsept: {concept_name}.",
                "Elementer er geometri-snappet før visning og PDF.",
            ] + notes[:3]
            sketches.append(
                {
                    "page_index": record["page_index"],
                    "region_index": region_index,
                    "page_label": page_label,
                    "plan_bbox": region["bbox_norm"],
                    "notes": [clean_pdf_text(item) for item in notes if clean_pdf_text(item)],
                    "elements": elements,
                    "grounded_engine": True,
                }
            )
            if len(sketches) >= max_sketches:
                return sketches

    return sketches[:max_sketches]


def replace_analysis_sketches_with_grounded(
    analysis_result: Dict[str, Any],
    drawings: List[Dict[str, Any]],
    material_preference: str,
) -> Dict[str, Any]:
    try:
        grounded_sketches = generate_grounded_sketches(drawings, analysis_result, material_preference, max_sketches=3)
    except Exception as exc:
        observations = [clean_pdf_text(item) for item in analysis_result.get("observasjoner", []) if clean_pdf_text(item)]
        warning = f"Geometri-kalibrering hoppet over i v11 etter feil: {type(exc).__name__}: {short_text(exc, 160)}"
        if warning not in observations:
            analysis_result["observasjoner"] = [warning] + observations
        st.session_state["rib_grounding_error"] = warning
        return analysis_result

    if grounded_sketches:
        analysis_result["sketches"] = grounded_sketches
        try:
            analysis_result = calibrate_analysis_from_refined_sketches_v6(analysis_result, grounded_sketches, drawings)
        except Exception as exc:
            observations = [clean_pdf_text(item) for item in analysis_result.get("observasjoner", []) if clean_pdf_text(item)]
            warning = f"Skissekalibrering beholdt grunnutkastet etter feil: {type(exc).__name__}: {short_text(exc, 160)}"
            if warning not in observations:
                analysis_result["observasjoner"] = [warning] + observations
        observations = analysis_result.get("observasjoner", [])
        note = "Konseptskissene er v11-snappet og kalibrert mot geometri når det lykkes; ved feil beholdes AI-utkastet i stedet for å krasje analysen."
        if note not in observations:
            analysis_result["observasjoner"] = [note] + observations
    return analysis_result


def clear_generated_rib_session() -> None:
    for key in [
        "generated_rib_pdf",
        "generated_rib_filename",
        "generated_rib_analysis",
        "generated_rib_report_text",
        "generated_rib_candidate_df",
        "generated_rib_overlay_package",
    ]:
        st.session_state.pop(key, None)


def persist_rib_draft_to_session(
    analysis_result: Dict[str, Any],
    report_text: str,
    candidate_df: pd.DataFrame,
    candidates: List[Dict[str, Any]],
    drawings: List[Dict[str, Any]],
    material_preference: str,
    foundation_preference: str,
    optimization_mode: str,
    safety_mode: str,
) -> None:
    st.session_state.rib_draft_analysis = deep_copy_jsonable(analysis_result)
    st.session_state.rib_draft_sketches = deep_copy_jsonable(analysis_result.get("sketches", []))
    st.session_state.rib_draft_original_sketches = deep_copy_jsonable(analysis_result.get("sketches", []))
    st.session_state.rib_draft_report_text = report_text
    st.session_state.rib_draft_candidate_df = candidate_df.copy()
    st.session_state.rib_draft_candidates = deep_copy_jsonable(candidates)
    st.session_state.rib_draft_drawings = drawings
    st.session_state.rib_draft_material = material_preference
    st.session_state.rib_draft_foundation = foundation_preference
    st.session_state.rib_draft_optimization = optimization_mode
    st.session_state.rib_draft_safety_mode = safety_mode
    st.session_state.rib_draft_last_click_sig = ""
    st.session_state.rib_draft_selected_sketch = sketch_uid(analysis_result.get("sketches", [{}])[0]) if analysis_result.get("sketches") else ""
    st.session_state.rib_draft_move_target = None
    st.session_state.rib_draft_updated_at = datetime.now().isoformat()


def draft_sketch_bundle_exists() -> bool:
    return "rib_draft_analysis" in st.session_state and "rib_draft_sketches" in st.session_state


def get_draft_sketch_by_uid(sketch_uid_value: str) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    sketches = st.session_state.get("rib_draft_sketches", [])
    for idx, sketch in enumerate(sketches):
        if sketch_uid(sketch) == sketch_uid_value:
            return idx, sketch
    return None, None


def get_geometry_for_sketch(drawing_record: Dict[str, Any], sketch: Dict[str, Any]) -> Dict[str, Any]:
    image_w, image_h = drawing_record["image"].size
    region_bbox_px = norm_bbox_to_px(sketch.get("plan_bbox"), image_w, image_h)
    return build_plan_geometry_grounded(drawing_record["image"], region_bbox_px)


def snap_local_point_to_geometry(local_x: float, local_y: float, geometry: Dict[str, Any], prefer: str = "column") -> Tuple[float, float]:
    rw, rh = geometry.get("crop_size", (0, 0))
    local_x = float(clamp(local_x, 0, max(rw - 1, 1)))
    local_y = float(clamp(local_y, 0, max(rh - 1, 1)))
    junctions = geometry.get("junctions", [])
    max_dist = max(18, int(min(rw, rh) * 0.12)) if rw and rh else 24
    snapped = nearest_junction_to_point(local_x, local_y, junctions, max_dist=max_dist)
    if snapped is not None:
        return float(snapped["x"]), float(snapped["y"])
    if geometry.get("grid_x"):
        local_x = float(min(geometry["grid_x"], key=lambda item: abs(float(item["pos"]) - local_x))["pos"])
    if geometry.get("grid_y"):
        local_y = float(min(geometry["grid_y"], key=lambda item: abs(float(item["pos"]) - local_y))["pos"])
    return local_x, local_y


def find_nearest_element_index(
    elements: List[Dict[str, Any]],
    image_size: Tuple[int, int],
    click_x_norm: float,
    click_y_norm: float,
    element_type: str,
) -> Optional[int]:
    image_w, image_h = image_size
    cx, cy = page_norm_to_px(click_x_norm, click_y_norm, image_w, image_h)
    best_idx = None
    best_dist = None
    for idx, element in enumerate(elements):
        if clean_pdf_text(element.get("type", "")).lower() != element_type:
            continue
        ex = ey = None
        if element_type == "column":
            ex, ey = page_norm_to_px(float(element.get("x", 0.0)), float(element.get("y", 0.0)), image_w, image_h)
        elif element_type == "core":
            x = float(element.get("x", 0.0))
            y = float(element.get("y", 0.0))
            w = float(element.get("w", 0.0))
            h = float(element.get("h", 0.0))
            ex, ey = page_norm_to_px(x + w / 2.0, y + h / 2.0, image_w, image_h)
        if ex is None or ey is None:
            continue
        dist = math.hypot(ex - cx, ey - cy)
        if best_dist is None or dist < best_dist:
            best_idx = idx
            best_dist = dist
    return best_idx


def apply_click_edit_to_sketch(
    sketch: Dict[str, Any],
    drawing_record: Dict[str, Any],
    tool: str,
    click_x_px: float,
    click_y_px: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    sketch = deep_copy_jsonable(sketch)
    geometry = get_geometry_for_sketch(drawing_record, sketch)
    if not geometry:
        return False, "Fant ikke brukbar geometri for valgt skisse.", sketch
    image_w, image_h = drawing_record["image"].size
    region_bbox_px = geometry["bbox_px"]
    rx, ry, rw, rh = region_bbox_px
    local_x = click_x_px - rx
    local_y = click_y_px - ry
    local_x = float(clamp(local_x, 0, max(rw - 1, 1)))
    local_y = float(clamp(local_y, 0, max(rh - 1, 1)))
    snapped_x, snapped_y = snap_local_point_to_geometry(local_x, local_y, geometry, prefer="column")
    x_norm, y_norm = local_point_to_page_norm(region_bbox_px, snapped_x, snapped_y, image_w, image_h)

    if tool == "add_column":
        sketch.setdefault("elements", []).append({"type": "column", "x": x_norm, "y": y_norm, "label": next_column_label(sketch.get("elements", []))})
        return True, "Søylepunkt lagt til og snappet til nærmeste geometri.", sketch

    if tool == "delete_column":
        idx = find_nearest_element_index(sketch.get("elements", []), drawing_record["image"].size, x_norm, y_norm, "column")
        if idx is None:
            return False, "Fant ingen søyle å slette i valgt skisse.", sketch
        sketch["elements"].pop(idx)
        return True, "Nærmeste søyle er slettet.", sketch

    if tool == "move_column":
        state_key = f"rib_move_target_{sketch_uid(sketch)}"
        target = st.session_state.get(state_key)
        if target is None:
            idx = find_nearest_element_index(sketch.get("elements", []), drawing_record["image"].size, x_norm, y_norm, "column")
            if idx is None:
                return False, "Klikk nærmere en eksisterende søyle for å velge flytting.", sketch
            st.session_state[state_key] = idx
            return False, "Søyle valgt. Klikk nå på nytt sted for å flytte den.", sketch
        if 0 <= int(target) < len(sketch.get("elements", [])):
            sketch["elements"][int(target)]["x"] = x_norm
            sketch["elements"][int(target)]["y"] = y_norm
            st.session_state.pop(state_key, None)
            return True, "Søyle flyttet og snappet til ny posisjon.", sketch
        st.session_state.pop(state_key, None)
        return False, "Flyttestatus ble nullstilt. Prøv på nytt.", sketch

    if tool == "move_core":
        idx = find_nearest_element_index(sketch.get("elements", []), drawing_record["image"].size, x_norm, y_norm, "core")
        if idx is None:
            idx = next((i for i, element in enumerate(sketch.get("elements", [])) if clean_pdf_text(element.get("type", "")).lower() == "core"), None)
        if idx is None:
            return False, "Fant ingen kjerne å flytte.", sketch
        core = sketch["elements"][int(idx)]
        core_w_px = float(core.get("w", 0.12)) * image_w
        core_h_px = float(core.get("h", 0.16)) * image_h
        local_left = clamp(snapped_x - (core_w_px / 2.0), 0, max(rw - core_w_px, 1))
        local_top = clamp(snapped_y - (core_h_px / 2.0), 0, max(rh - core_h_px, 1))
        core["x"], core["y"] = local_point_to_page_norm(region_bbox_px, local_left, local_top, image_w, image_h)
        return True, "Kjernen er flyttet og beholdt sin størrelse.", sketch

    return False, "Ingen endring utført.", sketch


def sketch_elements_to_editor_df(sketch: Dict[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for element in sketch.get("elements", []):
        e_type = clean_pdf_text(element.get("type", "")).lower()
        rows.append(
            {
                "type": e_type,
                "label": clean_pdf_text(element.get("label", "")),
                "orientation": clean_pdf_text(element.get("orientation", "")),
                "x_pct": round(float(element.get("x", element.get("x1", 0.0))) * 100.0, 2) if "x" in element or "x1" in element else None,
                "y_pct": round(float(element.get("y", element.get("y1", 0.0))) * 100.0, 2) if "y" in element or "y1" in element else None,
                "x2_pct": round(float(element.get("x2", 0.0)) * 100.0, 2) if "x2" in element else None,
                "y2_pct": round(float(element.get("y2", 0.0)) * 100.0, 2) if "y2" in element else None,
                "w_pct": round(float(element.get("w", 0.0)) * 100.0, 2) if "w" in element else None,
                "h_pct": round(float(element.get("h", 0.0)) * 100.0, 2) if "h" in element else None,
            }
        )
    return pd.DataFrame(rows)


def editor_df_to_sketch_elements(df: pd.DataFrame) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    if df is None or df.empty:
        return elements

    def pct_to_norm(value: Any, default: Optional[float] = None) -> Optional[float]:
        if value in ("", None):
            return default
        try:
            return round(clamp(float(value) / 100.0, 0.0, 1.0), 6)
        except Exception:
            return default

    for _, row in df.iterrows():
        e_type = clean_pdf_text(row.get("type", "")).lower()
        label = clean_pdf_text(row.get("label", ""))
        orientation = clean_pdf_text(row.get("orientation", ""))
        if not e_type:
            continue

        if e_type == "column":
            x = pct_to_norm(row.get("x_pct"))
            y = pct_to_norm(row.get("y_pct"))
            if x is None or y is None:
                continue
            elements.append({"type": "column", "x": x, "y": y, "label": label or "C"})
        elif e_type == "core":
            x = pct_to_norm(row.get("x_pct"))
            y = pct_to_norm(row.get("y_pct"))
            w = pct_to_norm(row.get("w_pct"), 0.10)
            h = pct_to_norm(row.get("h_pct"), 0.12)
            if x is None or y is None:
                continue
            elements.append({"type": "core", "x": x, "y": y, "w": max(w or 0.08, 0.03), "h": max(h or 0.08, 0.03), "label": label or "Kjerne"})
        elif e_type in {"wall", "beam", "span_arrow"}:
            x1 = pct_to_norm(row.get("x_pct"))
            y1 = pct_to_norm(row.get("y_pct"))
            x2 = pct_to_norm(row.get("x2_pct"))
            y2 = pct_to_norm(row.get("y2_pct"))
            if None in {x1, y1, x2, y2}:
                continue
            elements.append({"type": e_type, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "label": label})
        elif e_type == "grid":
            if orientation.lower().startswith("v"):
                x = pct_to_norm(row.get("x_pct"))
                if x is None:
                    continue
                elements.append({"type": "grid", "orientation": "vertical", "x": x, "label": label or "A"})
            else:
                y = pct_to_norm(row.get("y_pct"))
                if y is None:
                    continue
                elements.append({"type": "grid", "orientation": "horizontal", "y": y, "label": label or "1"})
    return elements


def extract_plotly_click(event_state: Any) -> Optional[Dict[str, float]]:
    if event_state is None:
        return None
    selection = getattr(event_state, "selection", None)
    if selection is None and isinstance(event_state, dict):
        selection = event_state.get("selection", event_state)
    if selection is None:
        return None
    points = getattr(selection, "points", None)
    if points is None and isinstance(selection, dict):
        points = selection.get("points", [])
    if not points:
        return None
    point = points[-1]
    if hasattr(point, "x") and hasattr(point, "y"):
        return {"x": float(point.x), "y": float(point.y)}
    if isinstance(point, dict) and "x" in point and "y" in point:
        return {"x": float(point["x"]), "y": float(point["y"])}
    return None


def render_plotly_sketch_editor(drawing_record: Dict[str, Any], sketch: Dict[str, Any], editor_key: str) -> Optional[Dict[str, float]]:
    go = optional_plotly_go()
    if go is None:
        return render_inline_click_canvas_editor(drawing_record, sketch, editor_key)

    image = copy_rgb(drawing_record["image"])
    image_w, image_h = image.size
    region_bbox = norm_bbox_to_px(sketch.get("plan_bbox"), image_w, image_h)
    rx, ry, rw, rh = region_bbox
    step = max(14, int(min(rw, rh) * 0.06))
    grid_x = []
    grid_y = []
    for yy in range(ry, ry + rh + 1, step):
        for xx in range(rx, rx + rw + 1, step):
            grid_x.append(xx)
            grid_y.append(yy)

    fig = go.Figure()
    fig.add_layout_image(
        dict(
            source=editor_image_data_uri(image),
            xref="x",
            yref="y",
            x=0,
            y=0,
            sizex=image_w,
            sizey=image_h,
            yanchor="top",
            sizing="stretch",
            layer="below",
        )
    )

    fig.add_shape(
        type="rect",
        x0=rx,
        y0=ry,
        x1=rx + rw,
        y1=ry + rh,
        line=dict(color="rgba(56,194,201,0.75)", width=2, dash="dot"),
    )

    fig.add_trace(
        go.Scatter(
            x=grid_x,
            y=grid_y,
            mode="markers",
            marker=dict(size=max(8, int(step * 0.70)), color="rgba(0,0,0,0.003)"),
            hoverinfo="skip",
            name="Klikkflate",
            showlegend=False,
        )
    )

    column_x = []
    column_y = []
    column_text = []
    for element in sketch.get("elements", []):
        e_type = clean_pdf_text(element.get("type", "")).lower()
        if e_type == "column":
            px, py = page_norm_to_px(float(element.get("x", 0.0)), float(element.get("y", 0.0)), image_w, image_h)
            column_x.append(px)
            column_y.append(py)
            column_text.append(clean_pdf_text(element.get("label", "")))
        elif e_type == "core":
            x = float(element.get("x", 0.0)) * image_w
            y = float(element.get("y", 0.0)) * image_h
            w = float(element.get("w", 0.0)) * image_w
            h = float(element.get("h", 0.0)) * image_h
            fig.add_shape(type="rect", x0=x, y0=y, x1=x + w, y1=y + h, line=dict(color="rgba(255,196,64,0.95)", width=3), fillcolor="rgba(255,196,64,0.18)")
            fig.add_trace(go.Scatter(x=[x + w / 2.0], y=[y + h / 2.0], mode="text", text=[clean_pdf_text(element.get("label", "Kjerne"))], textposition="middle center", showlegend=False))
        elif e_type in {"wall", "beam", "span_arrow"}:
            x1 = float(element.get("x1", 0.0)) * image_w
            y1 = float(element.get("y1", 0.0)) * image_h
            x2 = float(element.get("x2", 0.0)) * image_w
            y2 = float(element.get("y2", 0.0)) * image_h
            dash = "solid" if e_type != "span_arrow" else "dot"
            width = 6 if e_type == "wall" else 4
            color = "rgba(255,153,153,0.95)" if e_type == "wall" else ("rgba(120,220,225,0.95)" if e_type == "beam" else "rgba(196,235,176,0.95)")
            fig.add_shape(type="line", x0=x1, y0=y1, x1=x2, y1=y2, line=dict(color=color, width=width, dash=dash))
        elif e_type == "grid":
            orientation = clean_pdf_text(element.get("orientation", "")).lower()
            if orientation.startswith("v"):
                x = float(element.get("x", 0.0)) * image_w
                fig.add_shape(type="line", x0=x, y0=ry, x1=x, y1=ry + rh, line=dict(color="rgba(125,140,160,0.75)", width=2, dash="dot"))
            else:
                y = float(element.get("y", 0.0)) * image_h
                fig.add_shape(type="line", x0=rx, y0=y, x1=rx + rw, y1=y, line=dict(color="rgba(125,140,160,0.75)", width=2, dash="dot"))

    if column_x:
        fig.add_trace(
            go.Scatter(
                x=column_x,
                y=column_y,
                mode="markers+text",
                marker=dict(size=14, color="rgba(56,194,201,0.98)", line=dict(color="white", width=1.5)),
                text=column_text,
                textposition="top center",
                showlegend=False,
                name="Søyler",
            )
        )

    fig.update_xaxes(visible=False, range=[0, image_w])
    fig.update_yaxes(visible=False, range=[0, image_h], autorange="reversed", scaleanchor="x", scaleratio=1)
    fig.update_layout(
        height=int(min(950, max(460, image_h / max(image_w, 1) * 950))),
        margin=dict(l=0, r=0, t=0, b=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        dragmode="select",
        clickmode="event+select",
        showlegend=False,
    )

    try:
        event_state = st.plotly_chart(
            fig,
            key=editor_key,
            use_container_width=True,
            on_select="rerun",
            selection_mode=("points",),
        )
        return extract_plotly_click(event_state)
    except TypeError:
        st.caption("Miljøet støtter ikke Plotly-klikk direkte. Bytter til innebygd canvas-editor.")
        return render_inline_click_canvas_editor(drawing_record, sketch, editor_key)
    except Exception:
        st.caption("Plotly-editoren kunne ikke startes. Bytter til innebygd canvas-editor.")
        return render_inline_click_canvas_editor(drawing_record, sketch, editor_key)


def finalize_rib_draft_to_pdf() -> bool:
    if not draft_sketch_bundle_exists():
        return False

    analysis_result = deep_copy_jsonable(st.session_state.get("rib_draft_analysis", {}))
    draft_sketches = deep_copy_jsonable(st.session_state.get("rib_draft_sketches", []))
    original_sketches = deep_copy_jsonable(st.session_state.get("rib_draft_original_sketches", []))
    candidate_df = st.session_state.get("rib_draft_candidate_df", pd.DataFrame())
    candidates = deep_copy_jsonable(st.session_state.get("rib_draft_candidates", []))
    drawings = st.session_state.get("rib_draft_drawings", [])
    report_text = st.session_state.get("rib_draft_report_text", "")
    manual_changed = json.dumps(draft_sketches, sort_keys=True) != json.dumps(original_sketches, sort_keys=True)

    analysis_result["sketches"] = draft_sketches
    if manual_changed:
        observations = analysis_result.get("observasjoner", [])
        edit_note = "Konseptskissene ble manuelt kalibrert i klikk-editor før rapportlåsing."
        if edit_note not in observations:
            analysis_result["observasjoner"] = [edit_note] + observations

    valid_models = list_available_models()
    valgt_modell = pick_model(valid_models)
    if valgt_modell:
        try:
            model = build_runtime_ai_model(valgt_modell)
            report_text = run_report_writer(
                model=model,
                analysis_result=analysis_result,
                candidates=candidates,
                project_data=pd_state,
                material_preference=st.session_state.get("rib_draft_material", material_valg),
                foundation_preference=st.session_state.get("rib_draft_foundation", fundamentering),
                optimization_mode=st.session_state.get("rib_draft_optimization", optimaliser_for),
            )
        except Exception:
            pass

    overlay_package = build_overlay_package(drawings, analysis_result, max_sketches=6)
    pdf_data = create_full_report_pdf(
        name=pd_state["p_name"],
        client=pd_state.get("c_name", ""),
        content=report_text,
        analysis_result=analysis_result,
        candidate_df=candidate_df,
        overlay_package=overlay_package,
        source_drawings=drawings,
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
    return True


def render_rib_draft_editor_ui() -> None:
    if not draft_sketch_bundle_exists():
        return

    analysis_result = st.session_state.get("rib_draft_analysis", {})
    draft_sketches = st.session_state.get("rib_draft_sketches", [])
    drawings = st.session_state.get("rib_draft_drawings", [])
    if not isinstance(draft_sketches, list):
        return

    st.markdown("### Utkast før rapportlåsing")
    st.info(
        "Skissene under er geometri-forankret og kan justeres før PDF låses. "
        "Du kan klikke direkte i planen for å legge til, slette eller flytte søyler, "
        "eller finjustere alle elementer i tabellen."
    )

    top1, top2, top3, top4 = st.columns(4)
    top1.metric("Skisser i utkast", str(len(draft_sketches)))
    top2.metric("Datagrunnlag", clean_pdf_text(analysis_result.get("grunnlag_status", "-")))
    top3.metric("Anbefalt konsept", short_text(analysis_result.get("recommended_system", {}).get("system_name", "-"), 24))
    top4.metric("Klikk-redigering", "Aktiv")

    if not draft_sketches:
        st.warning("Det finnes ingen skisser i utkastet. Du kan likevel låse rapporten direkte.")
        lock_only = st.button("🔒 Lås rapport og generer PDF", type="primary", use_container_width=True, key="rib_lock_without_sketch")
        if lock_only:
            with st.spinner("Låser rapport og bygger PDF..."):
                finalize_rib_draft_to_pdf()
            st.rerun()
        return

    sketch_options = [sketch_uid(sketch) for sketch in draft_sketches]
    default_option = st.session_state.get("rib_draft_selected_sketch", sketch_options[0])
    if default_option not in sketch_options:
        default_option = sketch_options[0]
    selected_sketch_uid = st.selectbox(
        "Velg skisse som skal justeres",
        sketch_options,
        index=sketch_options.index(default_option),
        format_func=lambda value: next(
            (
                f"Tegning {sketch.get('page_index', 0) + 1} - {clean_pdf_text(sketch.get('page_label', 'Konseptskisse'))}"
                for sketch in draft_sketches
                if sketch_uid(sketch) == value
            ),
            value,
        ),
        key="rib_draft_sketch_selector",
    )
    st.session_state.rib_draft_selected_sketch = selected_sketch_uid

    sketch_idx, selected_sketch = get_draft_sketch_by_uid(selected_sketch_uid)
    if selected_sketch is None or sketch_idx is None:
        return

    drawing_record = lookup_record_by_page(drawings, int(selected_sketch.get("page_index", -1)))
    if drawing_record is None:
        st.warning("Fant ikke tegningen som hører til skissen.")
        return

    left_col, right_col = st.columns([1.5, 1.0])
    with left_col:
        tool = st.radio(
            "Klikkverktøy",
            options=[
                ("add_column", "Legg til søyle"),
                ("delete_column", "Slett nærmeste søyle"),
                ("move_column", "Flytt søyle (to klikk)"),
                ("move_core", "Flytt kjerne"),
                ("none", "Ingen endring"),
            ],
            format_func=lambda item: item[1],
            horizontal=False,
            key="rib_editor_tool_choice",
        )[0]
        st.caption("Flytt søyle: klikk først nær søylen, deretter på ny posisjon.")
        click = render_plotly_sketch_editor(
            drawing_record,
            selected_sketch,
            editor_key=f"rib_plotly_editor_{selected_sketch_uid}_{st.session_state.get('rib_draft_updated_at', '')}",
        )
        if click and tool != "none":
            click_sig = click_event_signature(selected_sketch_uid, tool, click)
            if st.session_state.get("rib_draft_last_click_sig") != click_sig:
                changed, message, updated_sketch = apply_click_edit_to_sketch(
                    selected_sketch,
                    drawing_record,
                    tool,
                    click["x"],
                    click["y"],
                )
                st.session_state.rib_draft_last_click_sig = click_sig
                if changed:
                    draft_sketches[sketch_idx] = updated_sketch
                    st.session_state.rib_draft_sketches = draft_sketches
                    st.session_state.rib_draft_updated_at = datetime.now().isoformat()
                    st.success(message)
                    st.rerun()
                else:
                    st.info(message)

        preview_img = render_overlay_image(
            drawing_record,
            selected_sketch,
            analysis_result.get("recommended_system", {}).get("system_name", "Anbefalt system"),
            analysis_result.get("grunnlag_status", "-"),
        )
        st.image(preview_img, caption="Rapportpreview av valgt skisse", use_container_width=True)

    with right_col:
        st.markdown("##### Tabellredigering")
        edited_df = st.data_editor(
            sketch_elements_to_editor_df(selected_sketch),
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            key=f"rib_elements_table_{selected_sketch_uid}",
        )
        apply_table = st.button("Bruk tabellendringer", use_container_width=True, key=f"rib_apply_table_{selected_sketch_uid}")
        reset_this = st.button("Nullstill valgt skisse", use_container_width=True, key=f"rib_reset_one_{selected_sketch_uid}")
        if apply_table:
            draft_sketches[sketch_idx]["elements"] = editor_df_to_sketch_elements(edited_df)
            st.session_state.rib_draft_sketches = draft_sketches
            st.session_state.rib_draft_updated_at = datetime.now().isoformat()
            st.success("Tabellendringer er lagret i utkastet.")
            st.rerun()

        if reset_this:
            original_sketches = st.session_state.get("rib_draft_original_sketches", [])
            for original in original_sketches:
                if sketch_uid(original) == selected_sketch_uid:
                    draft_sketches[sketch_idx] = deep_copy_jsonable(original)
                    st.session_state.rib_draft_sketches = draft_sketches
                    st.session_state.rib_draft_updated_at = datetime.now().isoformat()
                    st.success("Valgt skisse er nullstilt til auto-generert versjon.")
                    st.rerun()
                    break

        st.markdown("##### Notater for valgt skisse")
        note_lines = selected_sketch.get("notes", [])
        if note_lines:
            for line in note_lines:
                st.write(f"- {clean_pdf_text(line)}")
        else:
            st.caption("Ingen notater registrert.")

    bottom_left, bottom_mid, bottom_right = st.columns([1, 1, 2])
    with bottom_left:
        if st.button("Nullstill alle skisser", use_container_width=True, key="rib_reset_all"):
            st.session_state.rib_draft_sketches = deep_copy_jsonable(st.session_state.get("rib_draft_original_sketches", []))
            st.session_state.rib_draft_updated_at = datetime.now().isoformat()
            st.success("Alle skisser er nullstilt.")
            st.rerun()
    with bottom_mid:
        if st.button("🔄 Oppdater kun rapporttekst", use_container_width=True, key="rib_refresh_report"):
            valid_models = list_available_models()
            valgt_modell = pick_model(valid_models)
            if valgt_modell:
                analysis_copy = deep_copy_jsonable(st.session_state.get("rib_draft_analysis", {}))
                analysis_copy["sketches"] = deep_copy_jsonable(st.session_state.get("rib_draft_sketches", []))
                try:
                    model = build_runtime_ai_model(valgt_modell)
                    st.session_state.rib_draft_report_text = run_report_writer(
                        model=model,
                        analysis_result=analysis_copy,
                        candidates=deep_copy_jsonable(st.session_state.get("rib_draft_candidates", [])),
                        project_data=pd_state,
                        material_preference=st.session_state.get("rib_draft_material", material_valg),
                        foundation_preference=st.session_state.get("rib_draft_foundation", fundamentering),
                        optimization_mode=st.session_state.get("rib_draft_optimization", optimaliser_for),
                    )
                    st.success("Rapportteksten er oppdatert mot siste skisseutkast.")
                except Exception:
                    st.warning("Rapportteksten kunne ikke oppdateres nå. Eksisterende utkast beholdes.")
            else:
                st.warning("Fant ingen tilgjengelig modell for å oppdatere rapporttekst.")
    with bottom_right:
        if st.button("🔒 Lås skisser og generer rapport", type="primary", use_container_width=True, key="rib_lock_report"):
            with st.spinner("Låser skisser, oppdaterer rapporttekst og bygger PDF..."):
                finalize_rib_draft_to_pdf()
            st.rerun()



# ------------------------------------------------------------
# V3 OVERRIDES - zoomed pointer editor + AI reanalysis
# ------------------------------------------------------------
def count_elements_by_type(sketch: Dict[str, Any]) -> Dict[str, int]:
    counts = {"column": 0, "wall": 0, "beam": 0, "core": 0, "span_arrow": 0, "grid": 0}
    for element in sketch.get("elements", []) if isinstance(sketch, dict) else []:
        e_type = clean_pdf_text(element.get("type", "")).lower()
        if e_type in counts:
            counts[e_type] += 1
    return counts


def next_element_label(elements: List[Dict[str, Any]], element_type: str) -> str:
    prefix_map = {
        "column": "C",
        "wall": "V",
        "beam": "B",
        "core": "K",
        "span_arrow": "Spenn",
    }
    prefix = prefix_map.get(element_type, "E")
    used = []
    for element in elements:
        if clean_pdf_text(element.get("type", "")).lower() != element_type:
            continue
        label = clean_pdf_text(element.get("label", ""))
        match = re.search(r"(\d+)", label)
        if match:
            used.append(int(match.group(1)))
    next_id = max(used, default=0) + 1
    if element_type == "span_arrow":
        return f"Spenn {next_id}"
    return f"{prefix}{next_id}"


def point_to_segment_distance(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx = x2 - x1
    dy = y2 - y1
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / float(dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def page_norm_to_local_crop(
    x_norm: float,
    y_norm: float,
    image_size: Tuple[int, int],
    region_bbox_px: Tuple[int, int, int, int],
) -> Tuple[float, float]:
    image_w, image_h = image_size
    page_x, page_y = page_norm_to_px(x_norm, y_norm, image_w, image_h)
    rx, ry, _, _ = region_bbox_px
    return float(page_x - rx), float(page_y - ry)


def find_nearest_pointlike_element_index(
    elements: List[Dict[str, Any]],
    image_size: Tuple[int, int],
    click_x_norm: float,
    click_y_norm: float,
    element_types: List[str],
) -> Tuple[Optional[int], Optional[float]]:
    image_w, image_h = image_size
    cx, cy = page_norm_to_px(click_x_norm, click_y_norm, image_w, image_h)
    best_idx = None
    best_dist = None
    for idx, element in enumerate(elements):
        e_type = clean_pdf_text(element.get("type", "")).lower()
        if e_type not in element_types:
            continue
        if e_type == "column":
            ex, ey = page_norm_to_px(float(element.get("x", 0.0)), float(element.get("y", 0.0)), image_w, image_h)
        elif e_type == "core":
            ex, ey = page_norm_to_px(
                float(element.get("x", 0.0)) + float(element.get("w", 0.0)) / 2.0,
                float(element.get("y", 0.0)) + float(element.get("h", 0.0)) / 2.0,
                image_w,
                image_h,
            )
        else:
            continue
        dist = math.hypot(ex - cx, ey - cy)
        if best_dist is None or dist < best_dist:
            best_idx = idx
            best_dist = dist
    return best_idx, best_dist


def find_nearest_linear_element_index(
    elements: List[Dict[str, Any]],
    image_size: Tuple[int, int],
    click_x_norm: float,
    click_y_norm: float,
    element_types: List[str],
    max_dist_px: float,
) -> Optional[int]:
    image_w, image_h = image_size
    cx, cy = page_norm_to_px(click_x_norm, click_y_norm, image_w, image_h)
    best_idx = None
    best_dist = None
    for idx, element in enumerate(elements):
        e_type = clean_pdf_text(element.get("type", "")).lower()
        if e_type not in element_types:
            continue
        x1 = float(element.get("x1", 0.0)) * image_w
        y1 = float(element.get("y1", 0.0)) * image_h
        x2 = float(element.get("x2", 0.0)) * image_w
        y2 = float(element.get("y2", 0.0)) * image_h
        dist = point_to_segment_distance(cx, cy, x1, y1, x2, y2)
        if best_dist is None or dist < best_dist:
            best_idx = idx
            best_dist = dist
    if best_dist is None or best_dist > max_dist_px:
        return None
    return best_idx


def pointer_state_key(sketch_uid_value: str) -> str:
    return f"rib_pointer_state_{sketch_uid_value}"


def get_pointer_state(sketch_uid_value: str) -> Dict[str, Any]:
    state = st.session_state.get(pointer_state_key(sketch_uid_value), {})
    return state if isinstance(state, dict) else {}


def clear_pointer_state(sketch_uid_value: str) -> None:
    st.session_state.pop(pointer_state_key(sketch_uid_value), None)


def push_draft_history() -> None:
    current = deep_copy_jsonable(st.session_state.get("rib_draft_sketches", []))
    history = deep_copy_jsonable(st.session_state.get("rib_draft_history", []))
    if not history or json.dumps(history[-1], sort_keys=True) != json.dumps(current, sort_keys=True):
        history.append(current)
    st.session_state.rib_draft_history = history[-30:]


def undo_draft_history() -> bool:
    history = deep_copy_jsonable(st.session_state.get("rib_draft_history", []))
    if not history:
        return False
    previous = history.pop()
    st.session_state.rib_draft_history = history
    st.session_state.rib_draft_sketches = previous
    st.session_state.rib_draft_updated_at = datetime.now().isoformat()
    return True


def mark_draft_changed() -> None:
    st.session_state.rib_draft_updated_at = datetime.now().isoformat()


def mark_ai_reanalysis_synced() -> None:
    st.session_state.rib_draft_ai_sync_token = st.session_state.get("rib_draft_updated_at", "")


def draft_ai_reanalysis_needed() -> bool:
    return st.session_state.get("rib_draft_updated_at", "") != st.session_state.get("rib_draft_ai_sync_token", "")


def snap_local_point_to_axis(
    local_x: float,
    local_y: float,
    geometry: Dict[str, Any],
    axis: str,
) -> Tuple[float, float, Optional[Dict[str, Any]]]:
    rw, rh = geometry.get("crop_size", (0, 0))
    local_x = float(clamp(local_x, 0, max(rw - 1, 1)))
    local_y = float(clamp(local_y, 0, max(rh - 1, 1)))
    threshold = max(20, int(min(rw, rh) * 0.11))

    if axis == "vertical":
        segments = [seg for seg in geometry.get("vertical_segments", []) if float(seg.get("length", 0.0)) >= max(26, int(rh * 0.16))]
        if segments:
            def score(seg: Dict[str, Any]) -> float:
                center = float(seg.get("center", local_x))
                y0 = float(seg.get("y", 0.0))
                y1 = y0 + float(seg.get("h", 0.0))
                off_axis = abs(center - local_x)
                outside = 0.0 if (y0 - 12) <= local_y <= (y1 + 12) else min(abs(local_y - y0), abs(local_y - y1))
                return off_axis + outside * 0.35
            candidate = min(segments, key=score)
            if score(candidate) <= threshold:
                return (
                    float(candidate["center"]),
                    float(clamp(local_y, candidate["y"], candidate["y"] + candidate["h"])),
                    candidate,
                )
        if geometry.get("grid_x"):
            local_x = float(min(geometry["grid_x"], key=lambda item: abs(float(item["pos"]) - local_x))["pos"])
        return local_x, local_y, None

    segments = [seg for seg in geometry.get("horizontal_segments", []) if float(seg.get("length", 0.0)) >= max(26, int(rw * 0.16))]
    if segments:
        def score(seg: Dict[str, Any]) -> float:
            center = float(seg.get("center", local_y))
            x0 = float(seg.get("x", 0.0))
            x1 = x0 + float(seg.get("w", 0.0))
            off_axis = abs(center - local_y)
            outside = 0.0 if (x0 - 12) <= local_x <= (x1 + 12) else min(abs(local_x - x0), abs(local_x - x1))
            return off_axis + outside * 0.35
        candidate = min(segments, key=score)
        if score(candidate) <= threshold:
            return (
                float(clamp(local_x, candidate["x"], candidate["x"] + candidate["w"])),
                float(candidate["center"]),
                candidate,
            )
    if geometry.get("grid_y"):
        local_y = float(min(geometry["grid_y"], key=lambda item: abs(float(item["pos"]) - local_y))["pos"])
    return local_x, local_y, None


def build_snapped_linear_element(
    tool: str,
    start_local: Tuple[float, float],
    end_local: Tuple[float, float],
    geometry: Dict[str, Any],
    image_size: Tuple[int, int],
    existing_elements: List[Dict[str, Any]],
) -> Dict[str, Any]:
    image_w, image_h = image_size
    region_bbox_px = geometry["bbox_px"]
    start_x, start_y = start_local
    end_x, end_y = end_local
    axis = "vertical" if abs(end_y - start_y) > abs(end_x - start_x) else "horizontal"
    sx, sy, start_seg = snap_local_point_to_axis(start_x, start_y, geometry, axis)
    ex, ey, end_seg = snap_local_point_to_axis(end_x, end_y, geometry, axis)
    ref_seg = start_seg or end_seg
    rw, rh = geometry.get("crop_size", (0, 0))
    min_len = max(24, int(min(rw, rh) * 0.10))

    if axis == "vertical":
        x_ref = float(ref_seg["center"]) if ref_seg is not None else float((sx + ex) / 2.0)
        if ref_seg is not None:
            y0 = max(min(sy, ey), float(ref_seg["y"]))
            y1 = min(max(sy, ey), float(ref_seg["y"] + ref_seg["h"]))
            if (y1 - y0) < min_len:
                y0 = float(ref_seg["y"])
                y1 = float(ref_seg["y"] + ref_seg["h"])
        else:
            y0, y1 = sorted([sy, ey])
        x_ref = float(clamp(x_ref, 0, max(rw - 1, 1)))
        y0 = float(clamp(y0, 0, max(rh - 1, 1)))
        y1 = float(clamp(y1, y0 + 2, max(rh - 1, 1)))
        x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, x_ref, y0, image_w, image_h)
        x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, x_ref, y1, image_w, image_h)
    else:
        y_ref = float(ref_seg["center"]) if ref_seg is not None else float((sy + ey) / 2.0)
        if ref_seg is not None:
            x0 = max(min(sx, ex), float(ref_seg["x"]))
            x1 = min(max(sx, ex), float(ref_seg["x"] + ref_seg["w"]))
            if (x1 - x0) < min_len:
                x0 = float(ref_seg["x"])
                x1 = float(ref_seg["x"] + ref_seg["w"])
        else:
            x0, x1 = sorted([sx, ex])
        y_ref = float(clamp(y_ref, 0, max(rh - 1, 1)))
        x0 = float(clamp(x0, 0, max(rw - 1, 1)))
        x1 = float(clamp(x1, x0 + 2, max(rw - 1, 1)))
        x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, x0, y_ref, image_w, image_h)
        x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, x1, y_ref, image_w, image_h)

    type_map = {
        "add_wall": "wall",
        "add_beam": "beam",
        "add_span": "span_arrow",
    }
    element_type = type_map.get(tool, "beam")
    label_map = {
        "wall": next_element_label(existing_elements, "wall"),
        "beam": next_element_label(existing_elements, "beam"),
        "span_arrow": "Spennretning",
    }
    return {
        "type": element_type,
        "x1": x1_norm,
        "y1": y1_norm,
        "x2": x2_norm,
        "y2": y2_norm,
        "label": label_map[element_type],
    }


def build_resized_core_from_clicks(
    sketch: Dict[str, Any],
    anchor_local: Tuple[float, float],
    end_local: Tuple[float, float],
    geometry: Dict[str, Any],
    image_size: Tuple[int, int],
) -> Tuple[bool, str, Dict[str, Any]]:
    sketch = deep_copy_jsonable(sketch)
    image_w, image_h = image_size
    region_bbox_px = geometry["bbox_px"]
    x0, y0 = anchor_local
    x1, y1 = end_local
    left = float(min(x0, x1))
    right = float(max(x0, x1))
    top = float(min(y0, y1))
    bottom = float(max(y0, y1))
    max_snap_x = max(12, int(geometry["crop_size"][0] * 0.06))
    max_snap_y = max(12, int(geometry["crop_size"][1] * 0.06))
    left = float(snap_edge_to_lines(left, geometry.get("vertical_segments", []), max_snap_x))
    right = float(snap_edge_to_lines(right, geometry.get("vertical_segments", []), max_snap_x))
    top = float(snap_edge_to_lines(top, geometry.get("horizontal_segments", []), max_snap_y))
    bottom = float(snap_edge_to_lines(bottom, geometry.get("horizontal_segments", []), max_snap_y))

    if (right - left) < 18 or (bottom - top) < 18:
        return False, "Kjernen ble for liten. Klikk to hjørner med litt større avstand.", sketch

    core_idx = next(
        (idx for idx, element in enumerate(sketch.get("elements", [])) if clean_pdf_text(element.get("type", "")).lower() == "core"),
        None,
    )
    if core_idx is None:
        sketch.setdefault("elements", []).append({"type": "core", "x": 0.0, "y": 0.0, "w": 0.12, "h": 0.16, "label": "Kjerne"})
        core_idx = len(sketch["elements"]) - 1

    core = sketch["elements"][int(core_idx)]
    core["x"], core["y"] = local_point_to_page_norm(region_bbox_px, left, top, image_w, image_h)
    core["w"] = round(clamp((right - left) / max(image_w, 1), 0.03, 0.35), 6)
    core["h"] = round(clamp((bottom - top) / max(image_h, 1), 0.03, 0.35), 6)
    core["label"] = clean_pdf_text(core.get("label", "")) or "Kjerne"
    return True, "Kjerne oppdatert fra to klikk og snappet til planlinjer.", sketch


def apply_pointer_click_to_sketch(
    sketch: Dict[str, Any],
    drawing_record: Dict[str, Any],
    tool: str,
    click_x_local: float,
    click_y_local: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    sketch = deep_copy_jsonable(sketch)
    geometry = get_geometry_for_sketch(drawing_record, sketch)
    if not geometry:
        return False, "Fant ikke brukbar geometri for valgt skisse.", sketch

    image_w, image_h = drawing_record["image"].size
    region_bbox_px = geometry["bbox_px"]
    _, _, rw, rh = region_bbox_px
    local_x = float(clamp(click_x_local, 0, max(rw - 1, 1)))
    local_y = float(clamp(click_y_local, 0, max(rh - 1, 1)))
    sketch_key = sketch_uid(sketch)
    state_key = pointer_state_key(sketch_key)
    state = get_pointer_state(sketch_key)
    if state.get("tool") and state.get("tool") != tool:
        clear_pointer_state(sketch_key)
        state = {}

    point_max_dist = max(28, int(min(rw, rh) * 0.08))
    line_max_dist = max(24, int(min(rw, rh) * 0.07))
    x_norm_raw, y_norm_raw = local_point_to_page_norm(region_bbox_px, local_x, local_y, image_w, image_h)
    snapped_x, snapped_y = snap_local_point_to_geometry(local_x, local_y, geometry, prefer="column")
    x_norm, y_norm = local_point_to_page_norm(region_bbox_px, snapped_x, snapped_y, image_w, image_h)

    if tool == "add_column":
        sketch.setdefault("elements", []).append(
            {"type": "column", "x": x_norm, "y": y_norm, "label": next_element_label(sketch.get("elements", []), "column")}
        )
        clear_pointer_state(sketch_key)
        return True, "Søylepunkt lagt til og snappet til nærmeste knutepunkt.", sketch

    if tool == "delete_column":
        idx, dist = find_nearest_pointlike_element_index(sketch.get("elements", []), drawing_record["image"].size, x_norm_raw, y_norm_raw, ["column"])
        if idx is None or (dist is not None and dist > point_max_dist):
            return False, "Fant ingen søyle nær klikket.", sketch
        sketch["elements"].pop(idx)
        clear_pointer_state(sketch_key)
        return True, "Nærmeste søyle er slettet.", sketch

    if tool == "move_column":
        if "target_idx" not in state:
            idx, dist = find_nearest_pointlike_element_index(sketch.get("elements", []), drawing_record["image"].size, x_norm_raw, y_norm_raw, ["column"])
            if idx is None or (dist is not None and dist > point_max_dist):
                return False, "Klikk nærmere en eksisterende søyle for å velge den.", sketch
            st.session_state[state_key] = {"tool": tool, "target_idx": int(idx)}
            return False, "Søyle valgt. Klikk nå på ny plassering.", sketch
        idx = int(state["target_idx"])
        if 0 <= idx < len(sketch.get("elements", [])):
            sketch["elements"][idx]["x"] = x_norm
            sketch["elements"][idx]["y"] = y_norm
            clear_pointer_state(sketch_key)
            return True, "Søyle flyttet og snappet til ny posisjon.", sketch
        clear_pointer_state(sketch_key)
        return False, "Flyttevalg ble nullstilt. Prøv på nytt.", sketch

    if tool == "move_core":
        idx, dist = find_nearest_pointlike_element_index(sketch.get("elements", []), drawing_record["image"].size, x_norm_raw, y_norm_raw, ["core"])
        if idx is None:
            idx = next((i for i, element in enumerate(sketch.get("elements", [])) if clean_pdf_text(element.get("type", "")).lower() == "core"), None)
        if idx is None:
            return False, "Fant ingen kjerne å flytte.", sketch
        core = sketch["elements"][int(idx)]
        core_w_px = float(core.get("w", 0.12)) * image_w
        core_h_px = float(core.get("h", 0.16)) * image_h
        local_left = clamp(snapped_x - (core_w_px / 2.0), 0, max(rw - core_w_px, 1))
        local_top = clamp(snapped_y - (core_h_px / 2.0), 0, max(rh - core_h_px, 1))
        core["x"], core["y"] = local_point_to_page_norm(region_bbox_px, local_left, local_top, image_w, image_h)
        clear_pointer_state(sketch_key)
        return True, "Kjernen er flyttet og beholdt sin størrelse.", sketch

    if tool == "resize_core":
        anchor = state.get("anchor")
        if anchor is None:
            st.session_state[state_key] = {"tool": tool, "anchor": {"x": local_x, "y": local_y}}
            return False, "Første hjørne for kjerne valgt. Klikk motsatt hjørne.", sketch
        clear_pointer_state(sketch_key)
        return build_resized_core_from_clicks(
            sketch,
            (float(anchor["x"]), float(anchor["y"])),
            (local_x, local_y),
            geometry,
            drawing_record["image"].size,
        )

    if tool in {"add_wall", "add_beam", "add_span"}:
        anchor = state.get("anchor")
        if anchor is None:
            st.session_state[state_key] = {"tool": tool, "anchor": {"x": local_x, "y": local_y}}
            label_lookup = {
                "add_wall": "bærende vegg",
                "add_beam": "bjelke",
                "add_span": "spennpil",
            }
            return False, f"Startpunkt for {label_lookup[tool]} valgt. Klikk sluttpunkt.", sketch
        element = build_snapped_linear_element(
            tool=tool,
            start_local=(float(anchor["x"]), float(anchor["y"])),
            end_local=(local_x, local_y),
            geometry=geometry,
            image_size=drawing_record["image"].size,
            existing_elements=sketch.get("elements", []),
        )
        sketch.setdefault("elements", []).append(element)
        clear_pointer_state(sketch_key)
        label_lookup = {
            "add_wall": "Bærende vegg lagt til og ortogonalt snappet til planlinjene.",
            "add_beam": "Bjelke lagt til og snappet til planlinjene.",
            "add_span": "Spennpil lagt til.",
        }
        return True, label_lookup[tool], sketch

    if tool == "delete_wall":
        idx = find_nearest_linear_element_index(
            sketch.get("elements", []),
            drawing_record["image"].size,
            x_norm_raw,
            y_norm_raw,
            ["wall"],
            max_dist_px=line_max_dist,
        )
        if idx is None:
            return False, "Fant ingen bærende vegg nær klikket.", sketch
        sketch["elements"].pop(idx)
        clear_pointer_state(sketch_key)
        return True, "Nærmeste bærende vegg er slettet.", sketch

    if tool == "delete_beam":
        idx = find_nearest_linear_element_index(
            sketch.get("elements", []),
            drawing_record["image"].size,
            x_norm_raw,
            y_norm_raw,
            ["beam"],
            max_dist_px=line_max_dist,
        )
        if idx is None:
            return False, "Fant ingen bjelke nær klikket.", sketch
        sketch["elements"].pop(idx)
        clear_pointer_state(sketch_key)
        return True, "Nærmeste bjelke er slettet.", sketch

    if tool == "delete_span":
        idx = find_nearest_linear_element_index(
            sketch.get("elements", []),
            drawing_record["image"].size,
            x_norm_raw,
            y_norm_raw,
            ["span_arrow"],
            max_dist_px=line_max_dist,
        )
        if idx is None:
            return False, "Fant ingen spennpil nær klikket.", sketch
        sketch["elements"].pop(idx)
        clear_pointer_state(sketch_key)
        return True, "Nærmeste spennpil er slettet.", sketch

    clear_pointer_state(sketch_key)
    return False, "Ingen endring utført.", sketch


def summarize_manual_sketches(sketches: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for sketch in sketches:
        counts = count_elements_by_type(sketch)
        lines.append(
            f"- page_index {sketch.get('page_index')}: {clean_pdf_text(sketch.get('page_label', 'Skisse'))} | "
            f"columns={counts['column']}, walls={counts['wall']}, beams={counts['beam']}, "
            f"core={counts['core']}, span_arrows={counts['span_arrow']}"
        )
    return "\n".join(lines) if lines else "- Ingen korrigerte skisser tilgjengelig."


def build_ai_review_images(
    drawings: List[Dict[str, Any]],
    sketches: List[Dict[str, Any]],
    analysis_result: Dict[str, Any],
) -> List[Image.Image]:
    images: List[Image.Image] = []
    used_pages: List[int] = []
    for sketch in sketches:
        page_index = int(sketch.get("page_index", -1))
        if page_index in used_pages:
            continue
        used_pages.append(page_index)
        record = lookup_record_by_page(drawings, page_index)
        if record is not None:
            images.append(add_analysis_badge(record["image"], page_index, f"{record['label']} (grunnlag)"))

    concept_name = clean_pdf_text(analysis_result.get("recommended_system", {}).get("system_name", "Anbefalt system"))
    data_status = clean_pdf_text(analysis_result.get("grunnlag_status", "-"))
    for sketch in sketches[:3]:
        record = lookup_record_by_page(drawings, int(sketch.get("page_index", -1)))
        if record is None:
            continue
        overlay = render_overlay_image(record, sketch, concept_name, data_status)
        image_w, image_h = record["image"].size
        rx, ry, rw, rh = norm_bbox_to_px(sketch.get("plan_bbox"), image_w, image_h)
        pad = max(12, int(min(rw, rh) * 0.08))
        crop = overlay.crop(
            (
                max(0, rx - pad),
                max(0, ry - pad),
                min(overlay.size[0], rx + rw + pad),
                min(overlay.size[1], ry + rh + pad),
            )
        )
        images.append(crop)
    return images


def run_ai_reanalysis_from_corrected_sketches(
    model,
    analysis_result: Dict[str, Any],
    drawings: List[Dict[str, Any]],
    corrected_sketches: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    project_data: Dict[str, Any],
    material_preference: str,
    foundation_preference: str,
    optimization_mode: str,
    user_note: str = "",
) -> Dict[str, Any]:
    if model is None:
        return deep_copy_jsonable(analysis_result)

    review_images = build_ai_review_images(drawings, corrected_sketches, analysis_result)
    sketch_json = json.dumps(corrected_sketches, ensure_ascii=False)
    prompt = f"""
Du er Builtly RIB AI og skal REKALIBRERE en tidligere analyse.

Du får:
1. opprinnelige planbilder,
2. manuelt korrigerte skissebilder med overlagt bæresystem,
3. JSON for de korrigerte skissene.

De korrigerte skissene er autoritative og skal ha høyere prioritet enn tidligere autoanalyse.
Bruk dem til å korrigere vurderingen av søyler, bærende vegger, bjelker, kjerne, spennretning, lastvei og risiko.

PROSJEKT:
- Navn: {clean_pdf_text(project_data.get('p_name'))}
- Type: {clean_pdf_text(project_data.get('b_type'))}
- BTA: {nb_value(project_data.get('bta'))} m2
- Etasjer: {nb_value(project_data.get('etasjer'))}
- Sted: {clean_pdf_text(project_data.get('adresse'))}, {clean_pdf_text(project_data.get('kommune'))}
- Foretrukket materiale: {clean_pdf_text(material_preference)}
- Fundamentering: {clean_pdf_text(foundation_preference)}
- Optimaliser for: {clean_pdf_text(optimization_mode)}

TIDLIGERE ANBEFALING:
{json.dumps(analysis_result.get('recommended_system', {}), ensure_ascii=False)}

KORRIGERTE SKISSER - KORT OPPSUMMERING:
{summarize_manual_sketches(corrected_sketches)}

KORRIGERTE SKISSER - JSON:
{sketch_json}

BRUKERENS EGEN FAGKOMMENTAR:
{clean_pdf_text(user_note) if clean_pdf_text(user_note) else "-"}

VIKTIGE REGLER:
1. Ikke generer nye tilfeldige søyler, bærelinjer eller kjerner som strider mot de korrigerte skissene.
2. Hvis de korrigerte skissene viser vegg-/kjernebæring, skal det komme tydelig frem.
3. Hvis de korrigerte skissene viser søyle-/bjelkesystem, skal det komme tydelig frem.
4. Vær ærlig om usikkerhet i grunnlaget.
5. Oppdater observasjoner, mangler, anbefalt system, stabilitetsprinsipp, lastvei, risiko og neste steg.
6. "sketches" skal returneres som tom liste [], fordi manuell skisse beholdes uendret i appen.
7. Returner KUN gyldig JSON.

JSON-SKJEMA:
{{
  "grunnlag_status": "FULLSTENDIG | DELVIS | FOR_SVAKT",
  "grunnlag_begrunnelse": "kort tekst",
  "observasjoner": ["..."],
  "mangler": ["..."],
  "drawings": [],
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
  "alternatives": [],
  "sketches": [],
  "risk_register": [
    {{"topic": "kort risiko", "severity": "Lav | Middels | Høy", "mitigation": "tiltak"}}
  ],
  "load_assumptions": ["..."],
  "foundation_assumptions": ["..."],
  "next_steps": ["..."]
}}
""".strip()

    raw_text = generate_text(model, [prompt] + review_images, temperature=0.10)
    parsed = safe_json_loads(raw_text)
    updated = normalize_analysis_result(parsed, candidates, drawings)
    updated["sketches"] = deep_copy_jsonable(corrected_sketches)
    obs = updated.get("observasjoner", [])
    marker = "AI reanalyse er kalibrert mot manuelt korrigerte skisser før rapportgenerering."
    if marker not in obs:
        updated["observasjoner"] = [marker] + obs
    if clean_pdf_text(user_note):
        note_line = f"Brukerkommentar før reanalyse: {clean_pdf_text(user_note)}"
        if note_line not in updated["observasjoner"]:
            updated["observasjoner"] = [note_line] + updated["observasjoner"]
    return updated


def persist_rib_draft_to_session(
    analysis_result: Dict[str, Any],
    report_text: str,
    candidate_df: pd.DataFrame,
    candidates: List[Dict[str, Any]],
    drawings: List[Dict[str, Any]],
    material_preference: str,
    foundation_preference: str,
    optimization_mode: str,
    safety_mode: str,
) -> None:
    st.session_state.rib_draft_analysis = deep_copy_jsonable(analysis_result)
    st.session_state.rib_draft_sketches = deep_copy_jsonable(analysis_result.get("sketches", []))
    st.session_state.rib_draft_original_sketches = deep_copy_jsonable(analysis_result.get("sketches", []))
    st.session_state.rib_draft_report_text = report_text
    st.session_state.rib_draft_candidate_df = candidate_df.copy()
    st.session_state.rib_draft_candidates = deep_copy_jsonable(candidates)
    st.session_state.rib_draft_drawings = drawings
    st.session_state.rib_draft_material = material_preference
    st.session_state.rib_draft_foundation = foundation_preference
    st.session_state.rib_draft_optimization = optimization_mode
    st.session_state.rib_draft_safety_mode = safety_mode
    st.session_state.rib_draft_last_click_sig = ""
    st.session_state.rib_draft_selected_sketch = sketch_uid(analysis_result.get("sketches", [{}])[0]) if analysis_result.get("sketches") else ""
    st.session_state.rib_draft_move_target = None
    st.session_state.rib_draft_updated_at = datetime.now().isoformat()
    st.session_state.rib_draft_ai_sync_token = st.session_state.rib_draft_updated_at
    st.session_state.rib_draft_ai_user_note = ""
    st.session_state.rib_draft_history = []
    if analysis_result.get("sketches"):
        for sketch in analysis_result["sketches"]:
            clear_pointer_state(sketch_uid(sketch))


def structural_mode_from_analysis(analysis_result: Dict[str, Any], material_preference: str) -> str:
    txt = " ".join(
        [
            clean_pdf_text(material_preference),
            clean_pdf_text((analysis_result or {}).get("recommended_system", {}).get("system_name", "")),
            clean_pdf_text((analysis_result or {}).get("recommended_system", {}).get("material", "")),
            clean_pdf_text((analysis_result or {}).get("recommended_system", {}).get("vertical_system", "")),
            clean_pdf_text((analysis_result or {}).get("recommended_system", {}).get("stability_system", "")),
        ]
    ).lower()
    wall_markers = ["clt", "massivtre", "bærende vegg", "veggsystem", "skive", "leilighetsskille", "hybrid massivtre"]
    column_markers = ["søyle", "søyle-bjelke", "flatdekke", "hulldekke", "stålramme", "prefabrikkert betong", "ramme"]
    wall_hits = sum(1 for word in wall_markers if word in txt)
    column_hits = sum(1 for word in column_markers if word in txt)
    if wall_hits >= column_hits and wall_hits > 0:
        return "wall_core"
    if column_hits > wall_hits and column_hits > 0:
        return "column_core"
    if "hybrid" in txt and ("tre" in txt or "massivtre" in txt or "clt" in txt):
        return "wall_core"
    return "column_core"


def generate_column_core_elements_grounded(
    geometry: Dict[str, Any],
    image_size: Tuple[int, int],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    image_w, image_h = image_size
    region_bbox_px = geometry["bbox_px"]
    rw, rh = geometry["crop_size"]
    footprint = geometry.get("footprint_mask")
    core_bbox = geometry["core_bbox"]
    junctions = geometry.get("junctions", [])

    x_lines = choose_grid_lines_from_junctions(junctions, "x", rw, target=4 if rw >= rh * 0.80 else 3)
    y_lines = choose_grid_lines_from_junctions(junctions, "y", rh, target=4 if rh >= rw * 0.95 else 3)

    elements: List[Dict[str, Any]] = []
    for idx, x_line in enumerate(x_lines, start=1):
        x_norm, _ = local_point_to_page_norm(region_bbox_px, x_line["pos"], 0, image_w, image_h)
        elements.append({"type": "grid", "orientation": "vertical", "x": x_norm, "label": alpha_grid_label(idx)})
    for idx, y_line in enumerate(y_lines, start=1):
        _, y_norm = local_point_to_page_norm(region_bbox_px, 0, y_line["pos"], image_w, image_h)
        elements.append({"type": "grid", "orientation": "horizontal", "y": y_norm, "label": str(idx)})

    core_x, core_y, core_w, core_h = core_bbox
    core_x_norm, core_y_norm = local_point_to_page_norm(region_bbox_px, core_x, core_y, image_w, image_h)
    elements.append(
        {
            "type": "core",
            "x": core_x_norm,
            "y": core_y_norm,
            "w": round(clamp(core_w / max(image_w, 1), 0.03, 0.30), 6),
            "h": round(clamp(core_h / max(image_h, 1), 0.03, 0.30), 6),
            "label": "Kjerne",
        }
    )

    tol_x = max(10, int(rw * 0.04))
    tol_y = max(10, int(rh * 0.04))
    candidates: List[Dict[str, Any]] = []
    for junction in junctions:
        jx = float(junction["x"])
        jy = float(junction["y"])
        if point_inside_local_box(jx, jy, core_bbox, margin=10):
            continue
        if footprint is not None:
            iy = int(clamp(jy, 0, footprint.shape[0] - 1))
            ix = int(clamp(jx, 0, footprint.shape[1] - 1))
            if footprint[iy, ix] <= 0:
                continue
        if x_lines and min(abs(jx - float(line["pos"])) for line in x_lines) > tol_x:
            continue
        if y_lines and min(abs(jy - float(line["pos"])) for line in y_lines) > tol_y:
            continue
        center_penalty = math.hypot(jx - (rw / 2.0), jy - (rh / 2.0)) * 0.10
        edge_bonus = min(jx, rw - jx, jy, rh - jy) * 0.18
        score = float(junction.get("score", 0.0)) + edge_bonus - center_penalty
        candidates.append({"x": jx, "y": jy, "score": score})

    candidates.sort(key=lambda item: item["score"], reverse=True)
    min_spacing = max(26, int(min(rw, rh) * 0.16))
    chosen: List[Dict[str, Any]] = []
    max_columns = 10 if (rw * rh) >= 90000 else 8
    for candidate in candidates:
        if all(
            ((candidate["x"] - kept["x"]) ** 2 + (candidate["y"] - kept["y"]) ** 2) > (min_spacing ** 2)
            for kept in chosen
        ):
            chosen.append(candidate)
        if len(chosen) >= max_columns:
            break

    if len(chosen) < 4:
        for junction in sorted(junctions, key=lambda item: float(item.get("score", 0.0)), reverse=True):
            if point_inside_local_box(float(junction["x"]), float(junction["y"]), core_bbox, margin=10):
                continue
            if all(
                ((float(junction["x"]) - kept["x"]) ** 2 + (float(junction["y"]) - kept["y"]) ** 2) > (min_spacing ** 2)
                for kept in chosen
            ):
                chosen.append({"x": float(junction["x"]), "y": float(junction["y"]), "score": float(junction.get("score", 0.0))})
            if len(chosen) >= 6:
                break

    chosen.sort(key=lambda item: (item["y"], item["x"]))
    for idx, column in enumerate(chosen, start=1):
        x_norm, y_norm = local_point_to_page_norm(region_bbox_px, column["x"], column["y"], image_w, image_h)
        elements.append({"type": "column", "x": x_norm, "y": y_norm, "label": f"C{idx}"})

    if len(x_lines) >= 2 and y_lines:
        y_ref = float(y_lines[min(len(y_lines) // 2, len(y_lines) - 1)]["pos"])
        x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, float(x_lines[0]["pos"]), y_ref, image_w, image_h)
        x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, float(x_lines[-1]["pos"]), y_ref, image_w, image_h)
        elements.append({"type": "beam", "x1": x1_norm, "y1": y1_norm, "x2": x2_norm, "y2": y2_norm, "label": "Primærretning"})

    if len(x_lines) >= 2 and y_lines:
        arrow_y = float(min(rh - 16, core_y + core_h + max(18, rh * 0.08)))
        x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, float(x_lines[0]["pos"]), arrow_y, image_w, image_h)
        x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, float(x_lines[min(1, len(x_lines) - 1)]["pos"]), arrow_y, image_w, image_h)
        elements.append({"type": "span_arrow", "x1": x1_norm, "y1": y1_norm, "x2": x2_norm, "y2": y2_norm, "label": "Typisk modul"})

    notes = [
        "Søyler er valgt fra høy-konfidens knutepunkter i planen, ikke fra frie rutenettkryss.",
        "Kjerne er holdt sentral for tydelig avstivning og korte lastveier.",
        "Kolonneplasseringene er mer konservative og mindre tilfeldige enn i forrige versjon.",
    ]
    return elements, notes


def generate_wall_core_elements_grounded(
    geometry: Dict[str, Any],
    image_size: Tuple[int, int],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    image_w, image_h = image_size
    region_bbox_px = geometry["bbox_px"]
    rw, rh = geometry["crop_size"]
    core_bbox = geometry["core_bbox"]

    elements: List[Dict[str, Any]] = []
    core_x, core_y, core_w, core_h = core_bbox
    core_x_norm, core_y_norm = local_point_to_page_norm(region_bbox_px, core_x, core_y, image_w, image_h)
    elements.append(
        {
            "type": "core",
            "x": core_x_norm,
            "y": core_y_norm,
            "w": round(clamp(core_w / max(image_w, 1), 0.03, 0.30), 6),
            "h": round(clamp(core_h / max(image_h, 1), 0.03, 0.30), 6),
            "label": "Betongkjerne",
        }
    )

    chosen_vertical = [
        seg for seg in select_spaced_segments(
            geometry.get("vertical_segments", []),
            geometry["crop_size"],
            axis="vertical",
            max_items=4,
            core_bbox=core_bbox,
        )
        if float(seg.get("length", 0.0)) >= max(34, int(rh * 0.18))
    ]
    chosen_horizontal = [
        seg for seg in select_spaced_segments(
            geometry.get("horizontal_segments", []),
            geometry["crop_size"],
            axis="horizontal",
            max_items=3,
            core_bbox=core_bbox,
        )
        if float(seg.get("length", 0.0)) >= max(34, int(rw * 0.18))
    ]

    wall_count = 1
    for segment in chosen_vertical + chosen_horizontal:
        if segment["kind"] == "vertical":
            x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, segment["center"], segment["y"], image_w, image_h)
            x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, segment["center"], segment["y"] + segment["h"], image_w, image_h)
        else:
            x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, segment["x"], segment["center"], image_w, image_h)
            x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, segment["x"] + segment["w"], segment["center"], image_w, image_h)
        elements.append(
            {
                "type": "wall",
                "x1": x1_norm,
                "y1": y1_norm,
                "x2": x2_norm,
                "y2": y2_norm,
                "label": f"Vegg {wall_count}",
            }
        )
        wall_count += 1

    if len(chosen_vertical) >= 2:
        left = min(chosen_vertical, key=lambda seg: float(seg["center"]))
        right = max(chosen_vertical, key=lambda seg: float(seg["center"]))
        arrow_y = float(min(rh - 16, core_y + core_h + max(18, rh * 0.08)))
        x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, left["center"], arrow_y, image_w, image_h)
        x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, right["center"], arrow_y, image_w, image_h)
        elements.append({"type": "span_arrow", "x1": x1_norm, "y1": y1_norm, "x2": x2_norm, "y2": y2_norm, "label": "Spennretning"})
    elif len(chosen_horizontal) >= 2:
        top = min(chosen_horizontal, key=lambda seg: float(seg["center"]))
        bottom = max(chosen_horizontal, key=lambda seg: float(seg["center"]))
        arrow_x = float(min(rw - 16, core_x + core_w + max(18, rw * 0.08)))
        x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, arrow_x, top["center"], image_w, image_h)
        x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, arrow_x, bottom["center"], image_w, image_h)
        elements.append({"type": "span_arrow", "x1": x1_norm, "y1": y1_norm, "x2": x2_norm, "y2": y2_norm, "label": "Spennretning"})

    notes = [
        "Vegg-/kjernebæring prioriteres og tilfeldige støttepunkter er bevisst fjernet i auto-skissen.",
        "Bærende vegger er snappet til lange, tydelige linjeføringer i planen.",
        "Kjerne brukes som hovedavstivning og tydelig vertikal lastvei.",
    ]
    return elements, notes


def replace_analysis_sketches_with_grounded(
    analysis_result: Dict[str, Any],
    drawings: List[Dict[str, Any]],
    material_preference: str,
) -> Dict[str, Any]:
    try:
        grounded_sketches = generate_grounded_sketches(drawings, analysis_result, material_preference, max_sketches=3)
    except Exception as exc:
        observations = [clean_pdf_text(item) for item in analysis_result.get("observasjoner", []) if clean_pdf_text(item)]
        warning = f"Geometri-kalibrering hoppet over i v11 etter feil: {type(exc).__name__}: {short_text(exc, 160)}"
        if warning not in observations:
            analysis_result["observasjoner"] = [warning] + observations
        st.session_state["rib_grounding_error"] = warning
        return analysis_result

    if grounded_sketches:
        analysis_result["sketches"] = grounded_sketches
        try:
            analysis_result = calibrate_analysis_from_refined_sketches_v6(analysis_result, grounded_sketches, drawings)
        except Exception as exc:
            observations = [clean_pdf_text(item) for item in analysis_result.get("observasjoner", []) if clean_pdf_text(item)]
            warning = f"Skissekalibrering beholdt grunnutkastet etter feil: {type(exc).__name__}: {short_text(exc, 160)}"
            if warning not in observations:
                analysis_result["observasjoner"] = [warning] + observations
        observations = analysis_result.get("observasjoner", [])
        note = "Konseptskissene er v11-snappet og kalibrert mot geometri når det lykkes; ved feil beholdes AI-utkastet i stedet for å krasje analysen."
        if note not in observations:
            analysis_result["observasjoner"] = [note] + observations
    return analysis_result


def render_plotly_sketch_editor(drawing_record: Dict[str, Any], sketch: Dict[str, Any], editor_key: str) -> Optional[Dict[str, float]]:
    go = optional_plotly_go()
    if go is None:
        return render_inline_click_canvas_editor(drawing_record, sketch, editor_key)

    geometry = get_geometry_for_sketch(drawing_record, sketch)
    image_w, image_h = drawing_record["image"].size
    if geometry:
        rx, ry, rw, rh = geometry["bbox_px"]
    else:
        rx, ry, rw, rh = norm_bbox_to_px(sketch.get("plan_bbox"), image_w, image_h)
    crop = copy_rgb(drawing_record["image"]).crop((rx, ry, rx + rw, ry + rh))

    fig = go.Figure()
    fig.add_layout_image(
        dict(
            source=editor_image_data_uri(crop),
            xref="x",
            yref="y",
            x=0,
            y=0,
            sizex=rw,
            sizey=rh,
            yanchor="top",
            sizing="stretch",
            layer="below",
        )
    )

    show_guides = bool(st.session_state.get("rib_editor_show_guides", True))
    if show_guides and geometry:
        for seg in geometry.get("vertical_segments", []):
            if float(seg.get("length", 0.0)) < max(20, int(rh * 0.08)):
                continue
            fig.add_shape(
                type="line",
                x0=float(seg["center"]),
                y0=float(seg["y"]),
                x1=float(seg["center"]),
                y1=float(seg["y"] + seg["h"]),
                line=dict(color="rgba(88,120,180,0.26)", width=2, dash="dot"),
            )
        for seg in geometry.get("horizontal_segments", []):
            if float(seg.get("length", 0.0)) < max(20, int(rw * 0.08)):
                continue
            fig.add_shape(
                type="line",
                x0=float(seg["x"]),
                y0=float(seg["center"]),
                x1=float(seg["x"] + seg["w"]),
                y1=float(seg["center"]),
                line=dict(color="rgba(60,170,110,0.24)", width=2, dash="dot"),
            )
        core_x, core_y, core_w, core_h = geometry.get("core_bbox", (0, 0, 0, 0))
        if core_w > 0 and core_h > 0:
            fig.add_shape(
                type="rect",
                x0=core_x,
                y0=core_y,
                x1=core_x + core_w,
                y1=core_y + core_h,
                line=dict(color="rgba(255,196,64,0.55)", width=2, dash="dot"),
                fillcolor="rgba(255,196,64,0.08)",
            )
        junctions = geometry.get("junctions", [])[:48]
        if junctions:
            fig.add_trace(
                go.Scatter(
                    x=[float(item["x"]) for item in junctions],
                    y=[float(item["y"]) for item in junctions],
                    mode="markers",
                    marker=dict(size=7, color="rgba(56,194,201,0.35)"),
                    hoverinfo="skip",
                    showlegend=False,
                    name="Snappunkt",
                )
            )

    step = max(6, int(min(rw, rh) * 0.018))
    grid_x: List[int] = []
    grid_y: List[int] = []
    for yy in range(0, rh + 1, step):
        for xx in range(0, rw + 1, step):
            grid_x.append(xx)
            grid_y.append(yy)
    fig.add_trace(
        go.Scatter(
            x=grid_x,
            y=grid_y,
            mode="markers",
            marker=dict(size=max(10, int(step * 1.15)), color="rgba(0,0,0,0.003)"),
            hoverinfo="skip",
            name="Klikkflate",
            showlegend=False,
        )
    )

    column_x: List[float] = []
    column_y: List[float] = []
    column_text: List[str] = []

    for element in sketch.get("elements", []):
        e_type = clean_pdf_text(element.get("type", "")).lower()
        if e_type == "column":
            px, py = page_norm_to_local_crop(float(element.get("x", 0.0)), float(element.get("y", 0.0)), drawing_record["image"].size, (rx, ry, rw, rh))
            column_x.append(px)
            column_y.append(py)
            column_text.append(clean_pdf_text(element.get("label", "")))
        elif e_type == "core":
            x_local, y_local = page_norm_to_local_crop(float(element.get("x", 0.0)), float(element.get("y", 0.0)), drawing_record["image"].size, (rx, ry, rw, rh))
            w_local = float(element.get("w", 0.0)) * image_w
            h_local = float(element.get("h", 0.0)) * image_h
            fig.add_shape(
                type="rect",
                x0=x_local,
                y0=y_local,
                x1=x_local + w_local,
                y1=y_local + h_local,
                line=dict(color="rgba(255,196,64,0.95)", width=3),
                fillcolor="rgba(255,196,64,0.18)",
            )
            fig.add_trace(
                go.Scatter(
                    x=[x_local + w_local / 2.0],
                    y=[y_local + h_local / 2.0],
                    mode="text",
                    text=[clean_pdf_text(element.get("label", "Kjerne"))],
                    textposition="middle center",
                    showlegend=False,
                )
            )
        elif e_type in {"wall", "beam", "span_arrow"}:
            x1_local, y1_local = page_norm_to_local_crop(float(element.get("x1", 0.0)), float(element.get("y1", 0.0)), drawing_record["image"].size, (rx, ry, rw, rh))
            x2_local, y2_local = page_norm_to_local_crop(float(element.get("x2", 0.0)), float(element.get("y2", 0.0)), drawing_record["image"].size, (rx, ry, rw, rh))
            dash = "solid" if e_type != "span_arrow" else "dot"
            width = 6 if e_type == "wall" else 4
            color = "rgba(255,153,153,0.95)" if e_type == "wall" else ("rgba(120,220,225,0.95)" if e_type == "beam" else "rgba(196,235,176,0.95)")
            fig.add_shape(type="line", x0=x1_local, y0=y1_local, x1=x2_local, y1=y2_local, line=dict(color=color, width=width, dash=dash))
        elif e_type == "grid":
            orientation = clean_pdf_text(element.get("orientation", "")).lower()
            if orientation.startswith("v"):
                x_local, _ = page_norm_to_local_crop(float(element.get("x", 0.0)), 0.0, drawing_record["image"].size, (rx, ry, rw, rh))
                fig.add_shape(type="line", x0=x_local, y0=0, x1=x_local, y1=rh, line=dict(color="rgba(125,140,160,0.65)", width=2, dash="dot"))
            else:
                _, y_local = page_norm_to_local_crop(0.0, float(element.get("y", 0.0)), drawing_record["image"].size, (rx, ry, rw, rh))
                fig.add_shape(type="line", x0=0, y0=y_local, x1=rw, y1=y_local, line=dict(color="rgba(125,140,160,0.65)", width=2, dash="dot"))

    if column_x:
        fig.add_trace(
            go.Scatter(
                x=column_x,
                y=column_y,
                mode="markers+text",
                marker=dict(size=15, color="rgba(56,194,201,0.98)", line=dict(color="white", width=1.5)),
                text=column_text,
                textposition="top center",
                showlegend=False,
                name="Søyler",
            )
        )

    fig.update_xaxes(visible=False, range=[0, rw], fixedrange=True)
    fig.update_yaxes(visible=False, range=[0, rh], autorange="reversed", scaleanchor="x", scaleratio=1, fixedrange=True)
    fig.update_layout(
        height=int(min(980, max(520, rh / max(rw, 1) * 1180))),
        margin=dict(l=0, r=0, t=0, b=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        dragmode="select",
        clickmode="event+select",
        showlegend=False,
    )

    try:
        event_state = st.plotly_chart(
            fig,
            key=editor_key,
            use_container_width=True,
            on_select="rerun",
            selection_mode=("points",),
        )
        return extract_plotly_click(event_state)
    except TypeError:
        st.caption("Miljøet støtter ikke Plotly-klikk direkte. Bytter til innebygd canvas-editor.")
        return render_inline_click_canvas_editor(drawing_record, sketch, editor_key)
    except Exception:
        st.caption("Plotly-editoren kunne ikke startes. Bytter til innebygd canvas-editor.")
        return render_inline_click_canvas_editor(drawing_record, sketch, editor_key)


def finalize_rib_draft_to_pdf() -> bool:
    if not draft_sketch_bundle_exists():
        return False

    analysis_result = deep_copy_jsonable(st.session_state.get("rib_draft_analysis", {}))
    draft_sketches = deep_copy_jsonable(st.session_state.get("rib_draft_sketches", []))
    original_sketches = deep_copy_jsonable(st.session_state.get("rib_draft_original_sketches", []))
    candidate_df = st.session_state.get("rib_draft_candidate_df", pd.DataFrame())
    candidates = deep_copy_jsonable(st.session_state.get("rib_draft_candidates", []))
    drawings = st.session_state.get("rib_draft_drawings", [])
    report_text = st.session_state.get("rib_draft_report_text", "")
    manual_changed = json.dumps(draft_sketches, sort_keys=True) != json.dumps(original_sketches, sort_keys=True)

    analysis_result["sketches"] = draft_sketches
    if manual_changed:
        observations = analysis_result.get("observasjoner", [])
        edit_note = "Konseptskissene ble manuelt kalibrert i zoomet musepeker-editor før rapportlåsing."
        if edit_note not in observations:
            analysis_result["observasjoner"] = [edit_note] + observations

    valid_models = list_available_models()
    valgt_modell = pick_model(valid_models)
    if valgt_modell:
        try:
            model = build_runtime_ai_model(valgt_modell)
            if draft_ai_reanalysis_needed():
                analysis_result = run_ai_reanalysis_from_corrected_sketches(
                    model=model,
                    analysis_result=analysis_result,
                    drawings=drawings,
                    corrected_sketches=draft_sketches,
                    candidates=candidates,
                    project_data=pd_state,
                    material_preference=st.session_state.get("rib_draft_material", material_valg),
                    foundation_preference=st.session_state.get("rib_draft_foundation", fundamentering),
                    optimization_mode=st.session_state.get("rib_draft_optimization", optimaliser_for),
                    user_note=st.session_state.get("rib_draft_ai_user_note", ""),
                )
                st.session_state.rib_draft_analysis = deep_copy_jsonable(analysis_result)
                mark_ai_reanalysis_synced()

            report_text = run_report_writer(
                model=model,
                analysis_result=analysis_result,
                candidates=candidates,
                project_data=pd_state,
                material_preference=st.session_state.get("rib_draft_material", material_valg),
                foundation_preference=st.session_state.get("rib_draft_foundation", fundamentering),
                optimization_mode=st.session_state.get("rib_draft_optimization", optimaliser_for),
            )
            st.session_state.rib_draft_report_text = report_text
        except Exception:
            pass

    overlay_package = build_overlay_package(drawings, analysis_result, max_sketches=6)
    pdf_data = create_full_report_pdf(
        name=pd_state["p_name"],
        client=pd_state.get("c_name", ""),
        content=report_text,
        analysis_result=analysis_result,
        candidate_df=candidate_df,
        overlay_package=overlay_package,
        source_drawings=drawings,
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
    return True


def render_rib_draft_editor_ui() -> None:
    if not draft_sketch_bundle_exists():
        return

    analysis_result = st.session_state.get("rib_draft_analysis", {})
    draft_sketches = st.session_state.get("rib_draft_sketches", [])
    drawings = st.session_state.get("rib_draft_drawings", [])
    if not isinstance(draft_sketches, list):
        return

    st.markdown("### Utkast før rapportlåsing")
    st.info(
        "Denne versjonen bruker et zoomet planutsnitt for musepeker-redigering. "
        "Du kan korrigere søyler, bærende vegger, bjelker, kjerne og spenn direkte med klikk i planen, "
        "deretter kjøre ny AI-analyse før rapporten låses."
    )

    ai_status = "Må reanalyseres" if draft_ai_reanalysis_needed() else "Synkronisert"
    top1, top2, top3, top4 = st.columns(4)
    top1.metric("Skisser i utkast", str(len(draft_sketches)))
    top2.metric("Datagrunnlag", clean_pdf_text(analysis_result.get("grunnlag_status", "-")))
    top3.metric("Anbefalt konsept", short_text(analysis_result.get("recommended_system", {}).get("system_name", "-"), 24))
    top4.metric("AI-status", ai_status)

    if not draft_sketches:
        st.warning("Det finnes ingen skisser i utkastet. Du kan likevel låse rapporten direkte.")
        if st.button("🔒 Lås rapport og generer PDF", type="primary", use_container_width=True, key="rib_lock_without_sketch"):
            with st.spinner("Låser rapport og bygger PDF..."):
                finalize_rib_draft_to_pdf()
            st.rerun()
        return

    sketch_options = [sketch_uid(sketch) for sketch in draft_sketches]
    default_option = st.session_state.get("rib_draft_selected_sketch", sketch_options[0])
    if default_option not in sketch_options:
        default_option = sketch_options[0]

    toolbar_left, toolbar_mid, toolbar_right = st.columns([1.4, 1.0, 1.6])
    with toolbar_left:
        selected_sketch_uid = st.selectbox(
            "Velg skisse som skal justeres",
            sketch_options,
            index=sketch_options.index(default_option),
            format_func=lambda value: next(
                (
                    f"Tegning {sketch.get('page_index', 0) + 1} - {clean_pdf_text(sketch.get('page_label', 'Konseptskisse'))}"
                    for sketch in draft_sketches
                    if sketch_uid(sketch) == value
                ),
                value,
            ),
            key="rib_draft_sketch_selector_v3",
        )
        st.session_state.rib_draft_selected_sketch = selected_sketch_uid
    with toolbar_mid:
        st.toggle("Vis snappinghjelp", value=True, key="rib_editor_show_guides")
    with toolbar_right:
        st.text_area(
            "Faglig kommentar til AI før ny analyse",
            key="rib_draft_ai_user_note",
            height=90,
            placeholder="Eks.: Søylelinje skal ut, bæring tas i stedet i leilighetsskille mot kjerne.",
        )

    sketch_idx, selected_sketch = get_draft_sketch_by_uid(selected_sketch_uid)
    if selected_sketch is None or sketch_idx is None:
        return

    drawing_record = lookup_record_by_page(drawings, int(selected_sketch.get("page_index", -1)))
    if drawing_record is None:
        st.warning("Fant ikke tegningen som hører til skissen.")
        return

    tool_options = [
        ("none", "Ingen endring"),
        ("add_column", "Legg til søyle"),
        ("move_column", "Flytt søyle"),
        ("delete_column", "Slett søyle"),
        ("add_wall", "Legg til bærende vegg"),
        ("delete_wall", "Slett bærende vegg"),
        ("add_beam", "Legg til bjelke"),
        ("delete_beam", "Slett bjelke"),
        ("move_core", "Flytt kjerne"),
        ("resize_core", "Endre kjerne (to klikk)"),
        ("add_span", "Sett spennpil"),
        ("delete_span", "Slett spennpil"),
    ]

    info_left, info_mid, info_right = st.columns([1.5, 1.0, 1.0])
    with info_left:
        tool = st.selectbox(
            "Musepeker-verktøy",
            options=tool_options,
            format_func=lambda item: item[1],
            key="rib_editor_tool_choice_v3",
        )[0]
        current_state = get_pointer_state(selected_sketch_uid)
        if current_state.get("tool") and current_state.get("tool") != tool:
            clear_pointer_state(selected_sketch_uid)
            current_state = {}
        pending_msg = ""
        if current_state.get("anchor"):
            pending_msg = "Ventende handling: andre klikk mangler."
        elif "target_idx" in current_state:
            pending_msg = "Ventende handling: klikk ny plassering."
        if pending_msg:
            st.warning(pending_msg)
        else:
            st.caption(
                "Søyler/kjerner snappes til geometri. Vegger, bjelker og spennpiler opprettes med to klikk og låses ortogonalt."
            )
    with info_mid:
        counts = count_elements_by_type(selected_sketch)
        st.metric("Søyler / vegger", f"{counts['column']} / {counts['wall']}")
    with info_right:
        st.metric("Bjelker / kjerne", f"{counts['beam']} / {counts['core']}")

    left_col, right_col = st.columns([1.6, 1.0])
    with left_col:
        st.markdown("##### Zoomet planutsnitt for klikk-redigering")
        click = render_plotly_sketch_editor(
            drawing_record,
            selected_sketch,
            editor_key=f"rib_plotly_editor_v3_{selected_sketch_uid}_{st.session_state.get('rib_draft_updated_at', '')}",
        )
        action_row = st.columns([1, 1, 1])
        with action_row[0]:
            if st.button("↩ Angre siste endring", use_container_width=True, key=f"rib_undo_{selected_sketch_uid}"):
                if undo_draft_history():
                    clear_pointer_state(selected_sketch_uid)
                    st.success("Siste skisseendring er angret.")
                    st.rerun()
                else:
                    st.info("Det finnes ingen endring å angre.")
        with action_row[1]:
            if st.button("Avbryt ventende verktøy", use_container_width=True, key=f"rib_cancel_pending_{selected_sketch_uid}"):
                clear_pointer_state(selected_sketch_uid)
                st.info("Ventende klikksekvens er nullstilt.")
                st.rerun()
        with action_row[2]:
            if st.button("Nullstill valgt skisse", use_container_width=True, key=f"rib_reset_one_v3_{selected_sketch_uid}"):
                original_sketches = st.session_state.get("rib_draft_original_sketches", [])
                for original in original_sketches:
                    if sketch_uid(original) == selected_sketch_uid:
                        push_draft_history()
                        draft_sketches[sketch_idx] = deep_copy_jsonable(original)
                        st.session_state.rib_draft_sketches = draft_sketches
                        clear_pointer_state(selected_sketch_uid)
                        mark_draft_changed()
                        st.success("Valgt skisse er nullstilt til auto-generert versjon.")
                        st.rerun()
                        break

        if click and tool != "none":
            click_sig = click_event_signature(selected_sketch_uid, tool, click)
            if st.session_state.get("rib_draft_last_click_sig") != click_sig:
                changed, message, updated_sketch = apply_pointer_click_to_sketch(
                    selected_sketch,
                    drawing_record,
                    tool,
                    click["x"],
                    click["y"],
                )
                st.session_state.rib_draft_last_click_sig = click_sig
                if changed:
                    push_draft_history()
                    draft_sketches[sketch_idx] = updated_sketch
                    st.session_state.rib_draft_sketches = draft_sketches
                    mark_draft_changed()
                    st.success(message)
                    st.rerun()
                else:
                    st.info(message)

    with right_col:
        st.markdown("##### Helside-preview")
        preview_img = render_overlay_image(
            drawing_record,
            selected_sketch,
            analysis_result.get("recommended_system", {}).get("system_name", "Anbefalt system"),
            analysis_result.get("grunnlag_status", "-"),
        )
        st.image(preview_img, caption="Preview slik skissen vil se ut i rapporten", use_container_width=True)

        st.markdown("##### Notater for valgt skisse")
        note_lines = selected_sketch.get("notes", [])
        if note_lines:
            for line in note_lines:
                st.write(f"- {clean_pdf_text(line)}")
        else:
            st.caption("Ingen notater registrert.")

        with st.expander("Avansert tabellredigering / fallback", expanded=False):
            edited_df = st.data_editor(
                sketch_elements_to_editor_df(selected_sketch),
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
                key=f"rib_elements_table_v3_{selected_sketch_uid}",
            )
            if st.button("Bruk tabellendringer", use_container_width=True, key=f"rib_apply_table_v3_{selected_sketch_uid}"):
                push_draft_history()
                draft_sketches[sketch_idx]["elements"] = editor_df_to_sketch_elements(edited_df)
                st.session_state.rib_draft_sketches = draft_sketches
                mark_draft_changed()
                st.success("Tabellendringer er lagret i utkastet.")
                st.rerun()

    bottom_left, bottom_mid, bottom_right = st.columns([1.0, 1.2, 1.4])
    with bottom_left:
        if st.button("Nullstill alle skisser", use_container_width=True, key="rib_reset_all_v3"):
            push_draft_history()
            st.session_state.rib_draft_sketches = deep_copy_jsonable(st.session_state.get("rib_draft_original_sketches", []))
            mark_draft_changed()
            st.success("Alle skisser er nullstilt.")
            st.rerun()

    with bottom_mid:
        if st.button("🤖 Re-analyser bæresystem med AI", use_container_width=True, key="rib_reanalyze_ai_v3"):
            valid_models = list_available_models()
            valgt_modell = pick_model(valid_models)
            if valgt_modell:
                try:
                    with st.spinner("AI reanalyserer korrigerte skisser og oppdaterer lastvei, bæring og risiko..."):
                        model = build_runtime_ai_model(valgt_modell)
                        analysis_copy = run_ai_reanalysis_from_corrected_sketches(
                            model=model,
                            analysis_result=deep_copy_jsonable(st.session_state.get("rib_draft_analysis", {})),
                            drawings=drawings,
                            corrected_sketches=deep_copy_jsonable(st.session_state.get("rib_draft_sketches", [])),
                            candidates=deep_copy_jsonable(st.session_state.get("rib_draft_candidates", [])),
                            project_data=pd_state,
                            material_preference=st.session_state.get("rib_draft_material", material_valg),
                            foundation_preference=st.session_state.get("rib_draft_foundation", fundamentering),
                            optimization_mode=st.session_state.get("rib_draft_optimization", optimaliser_for),
                            user_note=st.session_state.get("rib_draft_ai_user_note", ""),
                        )
                        st.session_state.rib_draft_analysis = analysis_copy
                        st.session_state.rib_draft_report_text = run_report_writer(
                            model=model,
                            analysis_result=analysis_copy,
                            candidates=deep_copy_jsonable(st.session_state.get("rib_draft_candidates", [])),
                            project_data=pd_state,
                            material_preference=st.session_state.get("rib_draft_material", material_valg),
                            foundation_preference=st.session_state.get("rib_draft_foundation", fundamentering),
                            optimization_mode=st.session_state.get("rib_draft_optimization", optimaliser_for),
                        )
                        mark_ai_reanalysis_synced()
                    st.success("AI-analysen og rapportutkastet er oppdatert mot siste korrigerte skisser.")
                    st.rerun()
                except Exception:
                    st.warning("AI-reanalysen feilet akkurat nå. Eksisterende utkast beholdes.")
            else:
                st.warning("Fant ingen tilgjengelig modell for AI-reanalyse.")

    with bottom_right:
        if st.button("🔒 Lås skisser, AI-oppdater og generer rapport", type="primary", use_container_width=True, key="rib_lock_report_v3"):
            with st.spinner("Låser skisser, oppdaterer AI-vurdering og bygger PDF..."):
                finalize_rib_draft_to_pdf()
            st.rerun()




# ------------------------------------------------------------
# 13B. V6 HYBRID AI + GEOMETRISK KALIBRERING AV BÆRESYSTEM
# ------------------------------------------------------------
BASEMENT_KEYWORDS_V6 = [
    "kjeller", "p-kjeller", "parkering", "parkering", "parking", "garage", "garasje",
    "underetasje", "underetg", "u.etg", "u-etg", "u1", "u2", "basement",
]
PLAN_KEYWORDS_V6 = [
    "plan", "plantegning", "etg", "etasje", "etasjeplan", "typisk", "level", "nivå",
]


def detect_drawing_hint(name: str) -> str:
    low = clean_pdf_text(name).lower()
    if any(k in low for k in PLAN_KEYWORDS_V6 + BASEMENT_KEYWORDS_V6 + ["floor", "plan1", "plan 1"]):
        return "plan"
    if any(k in low for k in ["snitt", "section", "cut"]):
        return "section"
    if any(k in low for k in ["fasade", "facade", "elevation"]):
        return "facade"
    if any(k in low for k in ["detalj", "detail"]):
        return "detail"
    return "unknown"



def _record_text_v6(record: Dict[str, Any], sketch: Optional[Dict[str, Any]] = None) -> str:
    parts = [
        clean_pdf_text(record.get("name", "")),
        clean_pdf_text(record.get("label", "")),
        clean_pdf_text(record.get("source", "")),
        clean_pdf_text(record.get("hint", "")),
    ]
    if isinstance(sketch, dict):
        parts.append(clean_pdf_text(sketch.get("page_label", "")))
    return " ".join(part for part in parts if part).lower()



def _is_basement_like_text_v6(text: str) -> bool:
    low = clean_pdf_text(text).lower()
    return any(word in low for word in BASEMENT_KEYWORDS_V6)



def is_basement_like_record_v6(record: Dict[str, Any], sketch: Optional[Dict[str, Any]] = None) -> bool:
    return _is_basement_like_text_v6(_record_text_v6(record, sketch))



def get_plan_regions_for_record_v6(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    cache_key = "_plan_regions_v6"
    cached = record.get(cache_key)
    if isinstance(cached, list) and cached:
        return cached
    regions = detect_plan_regions_grounded(record["image"])
    if not regions:
        width, height = record["image"].size
        fallback_bbox = (
            int(width * 0.08),
            int(height * 0.12),
            int(width * 0.84),
            int(height * 0.72),
        )
        regions = [{"bbox_px": fallback_bbox, "bbox_norm": px_bbox_to_norm(fallback_bbox, width, height), "score": 1.0}]
    record[cache_key] = regions
    return regions



def is_plan_like_record_v6(record: Dict[str, Any]) -> bool:
    hint = clean_pdf_text(record.get("hint", "")).lower()
    if hint == "plan":
        return True
    if hint in {"section", "facade", "detail"}:
        return False
    if is_basement_like_record_v6(record):
        return True
    regions = get_plan_regions_for_record_v6(record)
    if not regions:
        return False
    image_w, image_h = record["image"].size
    best_area = max(bbox_area(region["bbox_px"]) for region in regions)
    return (best_area / float(max(image_w * image_h, 1))) >= 0.12



def prioritize_drawings(drawings: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    out = []
    for record in drawings:
        try:
            if is_plan_like_record_v6(record):
                record["hint"] = "plan"
        except Exception:
            pass
        out.append(record)
    out = sorted(out, key=drawing_priority, reverse=True)[:limit]
    for idx, record in enumerate(out):
        record["page_index"] = idx
    return out



def _element_center_norm_v6(element: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    e_type = clean_pdf_text(element.get("type", "")).lower()
    try:
        if e_type == "column":
            return float(element.get("x", 0.0)), float(element.get("y", 0.0))
        if e_type == "core":
            return (
                float(element.get("x", 0.0)) + float(element.get("w", 0.0)) / 2.0,
                float(element.get("y", 0.0)) + float(element.get("h", 0.0)) / 2.0,
            )
        if e_type in {"wall", "beam", "span_arrow"}:
            return (
                (float(element.get("x1", 0.0)) + float(element.get("x2", 0.0))) / 2.0,
                (float(element.get("y1", 0.0)) + float(element.get("y2", 0.0))) / 2.0,
            )
        if e_type == "grid":
            orientation = clean_pdf_text(element.get("orientation", "")).lower()
            if orientation.startswith("v"):
                return float(element.get("x", 0.0)), 0.5
            return 0.5, float(element.get("y", 0.0))
    except Exception:
        return None
    return None



def _point_in_bbox_v6(px: float, py: float, bbox: Tuple[int, int, int, int]) -> bool:
    x, y, w, h = bbox
    return x <= px <= x + w and y <= py <= y + h



def _choose_region_for_sketch_v6(record: Dict[str, Any], sketch: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], int]:
    regions = get_plan_regions_for_record_v6(record)
    if not regions:
        width, height = record["image"].size
        full_bbox = (0, 0, width, height)
        return {"bbox_px": full_bbox, "bbox_norm": px_bbox_to_norm(full_bbox, width, height), "score": 1.0}, 0

    image_w, image_h = record["image"].size
    ai_bbox = None
    if isinstance(sketch, dict) and isinstance(sketch.get("plan_bbox"), dict):
        ai_bbox = norm_bbox_to_px(sketch.get("plan_bbox"), image_w, image_h)

    centers_px: List[Tuple[float, float]] = []
    if isinstance(sketch, dict):
        for element in sketch.get("elements", []):
            center_norm = _element_center_norm_v6(element)
            if center_norm is None:
                continue
            centers_px.append(page_norm_to_px(center_norm[0], center_norm[1], image_w, image_h))

    best_idx = 0
    best_score = None
    for idx, region in enumerate(regions):
        bbox = region["bbox_px"]
        inside_count = sum(1 for px, py in centers_px if _point_in_bbox_v6(px, py, bbox))
        region_score = float(region.get("score", 0.0))
        if ai_bbox is not None:
            region_score += bbox_iou(bbox, ai_bbox) * 2500.0
        if centers_px:
            cx, cy = bbox_center(bbox)
            avg_dist = sum(math.hypot(px - cx, py - cy) for px, py in centers_px) / max(len(centers_px), 1)
        else:
            avg_dist = 0.0
        total_score = region_score + inside_count * 1800.0 - avg_dist * 0.12
        if best_score is None or total_score > best_score:
            best_idx = idx
            best_score = total_score
    return regions[best_idx], best_idx



def _clusters_from_relative_pairs_v6(pairs: List[Tuple[float, float]], tolerance: float = 0.06) -> List[Dict[str, Any]]:
    clean_pairs = []
    for pos, weight in pairs:
        try:
            pos_v = float(pos)
            weight_v = max(0.1, float(weight))
        except Exception:
            continue
        if 0.0 <= pos_v <= 1.0:
            clean_pairs.append((pos_v, weight_v))
    if not clean_pairs:
        return []
    clusters = cluster_weighted_positions(clean_pairs, tolerance)
    return sorted(clusters, key=lambda item: (float(item.get("weight", 0.0)), int(item.get("count", 0))), reverse=True)



def collect_transfer_hints_v6(sketches: List[Dict[str, Any]], drawings: List[Dict[str, Any]]) -> Dict[str, Any]:
    x_pairs: List[Tuple[float, float]] = []
    y_pairs: List[Tuple[float, float]] = []
    core_boxes: List[Dict[str, float]] = []

    for sketch in sketches or []:
        if not isinstance(sketch, dict):
            continue
        record = lookup_record_by_page(drawings, int(sketch.get("page_index", -1)))
        if record is None or is_basement_like_record_v6(record, sketch):
            continue
        counts = count_elements_by_type(sketch)
        if (counts["wall"] + counts["core"]) <= 0:
            continue
        region, _ = _choose_region_for_sketch_v6(record, sketch)
        region_bbox = region["bbox_px"]
        rw, rh = region_bbox[2], region_bbox[3]
        image_w, image_h = record["image"].size
        for element in sketch.get("elements", []):
            e_type = clean_pdf_text(element.get("type", "")).lower()
            if e_type == "wall":
                x1, y1 = page_norm_to_local_crop(float(element.get("x1", 0.0)), float(element.get("y1", 0.0)), record["image"].size, region_bbox)
                x2, y2 = page_norm_to_local_crop(float(element.get("x2", 0.0)), float(element.get("y2", 0.0)), record["image"].size, region_bbox)
                dx = x2 - x1
                dy = y2 - y1
                if abs(dx) <= abs(dy):
                    x_pairs.append((clamp((x1 + x2) / 2.0 / max(rw, 1), 0.0, 1.0), max(0.6, abs(dy) / max(rh, 1))))
                else:
                    y_pairs.append((clamp((y1 + y2) / 2.0 / max(rh, 1), 0.0, 1.0), max(0.6, abs(dx) / max(rw, 1))))
            elif e_type == "core":
                x_local, y_local = page_norm_to_local_crop(float(element.get("x", 0.0)), float(element.get("y", 0.0)), record["image"].size, region_bbox)
                w_local = float(element.get("w", 0.0)) * image_w
                h_local = float(element.get("h", 0.0)) * image_h
                if w_local <= 4 or h_local <= 4:
                    continue
                rel_box = {
                    "x": clamp(x_local / max(rw, 1), 0.0, 1.0),
                    "y": clamp(y_local / max(rh, 1), 0.0, 1.0),
                    "w": clamp(w_local / max(rw, 1), 0.04, 0.40),
                    "h": clamp(h_local / max(rh, 1), 0.04, 0.45),
                    "weight": 1.0 + min(2.0, (w_local * h_local) / float(max(rw * rh, 1)) * 20.0),
                }
                core_boxes.append(rel_box)
                x_pairs.append((clamp((x_local + w_local / 2.0) / max(rw, 1), 0.0, 1.0), 1.6))
                y_pairs.append((clamp((y_local + h_local / 2.0) / max(rh, 1), 0.0, 1.0), 1.0))

    deduped_core_boxes: List[Dict[str, float]] = []
    for box in sorted(core_boxes, key=lambda item: float(item.get("weight", 0.0)), reverse=True):
        if any(
            abs((box["x"] + box["w"] / 2.0) - (other["x"] + other["w"] / 2.0)) < 0.08
            and abs((box["y"] + box["h"] / 2.0) - (other["y"] + other["h"] / 2.0)) < 0.10
            for other in deduped_core_boxes
        ):
            continue
        deduped_core_boxes.append(box)
        if len(deduped_core_boxes) >= 4:
            break

    return {
        "x_clusters": _clusters_from_relative_pairs_v6(x_pairs, tolerance=0.055)[:6],
        "y_clusters": _clusters_from_relative_pairs_v6(y_pairs, tolerance=0.075)[:5],
        "core_boxes": deduped_core_boxes,
    }



def _nearest_support_position_px_v6(value_px: float, extent_px: int, clusters: List[Dict[str, Any]], max_dist_px: float) -> Optional[float]:
    if not clusters or extent_px <= 0:
        return None
    positions = [float(item.get("pos", 0.0)) * extent_px for item in clusters if 0.0 <= float(item.get("pos", 0.0)) <= 1.0]
    if not positions:
        return None
    nearest = min(positions, key=lambda item: abs(item - value_px))
    return nearest if abs(nearest - value_px) <= max_dist_px else None



def _snap_point_with_transfer_hints_v6(
    local_x: float,
    local_y: float,
    geometry: Dict[str, Any],
    transfer_hints: Optional[Dict[str, Any]] = None,
    prefer_transfer: bool = False,
) -> Tuple[float, float]:
    rw, rh = geometry.get("crop_size", (0, 0))
    local_x = float(clamp(local_x, 0, max(rw - 1, 1)))
    local_y = float(clamp(local_y, 0, max(rh - 1, 1)))
    junctions = geometry.get("junctions", [])
    footprint = geometry.get("footprint_mask")
    core_bbox = geometry.get("core_bbox", (0, 0, 0, 0))
    if not junctions:
        return snap_local_point_to_geometry(local_x, local_y, geometry, prefer="column")

    support_x = None
    support_y = None
    if transfer_hints:
        support_x = _nearest_support_position_px_v6(local_x, rw, transfer_hints.get("x_clusters", []), max(22, int(rw * (0.18 if prefer_transfer else 0.10))))
        support_y = _nearest_support_position_px_v6(local_y, rh, transfer_hints.get("y_clusters", []), max(22, int(rh * (0.18 if prefer_transfer else 0.12))))

    best = None
    for junction in junctions:
        jx = float(junction.get("x", 0.0))
        jy = float(junction.get("y", 0.0))
        if point_inside_local_box(jx, jy, core_bbox, margin=12):
            continue
        if footprint is not None:
            iy = int(clamp(jy, 0, footprint.shape[0] - 1))
            ix = int(clamp(jx, 0, footprint.shape[1] - 1))
            if footprint[iy, ix] <= 0:
                continue
        score = float(junction.get("score", 0.0))
        score -= math.hypot(jx - local_x, jy - local_y) * 0.95
        if support_x is not None:
            dist_x = abs(jx - support_x)
            score -= dist_x * (1.15 if prefer_transfer else 0.70)
            if dist_x <= max(20, int(rw * 0.08)):
                score += 22.0
        if support_y is not None:
            dist_y = abs(jy - support_y)
            score -= dist_y * (0.95 if prefer_transfer else 0.55)
            if dist_y <= max(18, int(rh * 0.08)):
                score += 10.0
        if best is None or score > best[0]:
            best = (score, jx, jy)

    if best is not None:
        return float(best[1]), float(best[2])
    return snap_local_point_to_geometry(local_x, local_y, geometry, prefer="column")



def _snap_bbox_to_geometry_v6(x0: float, y0: float, x1: float, y1: float, geometry: Dict[str, Any]) -> Tuple[float, float, float, float]:
    rw, rh = geometry.get("crop_size", (0, 0))
    x0 = float(clamp(x0, 0, max(rw - 8, 1)))
    x1 = float(clamp(x1, x0 + 8, max(rw - 1, x0 + 8)))
    y0 = float(clamp(y0, 0, max(rh - 8, 1)))
    y1 = float(clamp(y1, y0 + 8, max(rh - 1, y0 + 8)))
    max_snap_x = max(14, int(rw * 0.08))
    max_snap_y = max(14, int(rh * 0.08))
    x0 = float(snap_edge_to_lines(x0, geometry.get("vertical_segments", []), max_snap_x))
    x1 = float(snap_edge_to_lines(x1, geometry.get("vertical_segments", []), max_snap_x))
    y0 = float(snap_edge_to_lines(y0, geometry.get("horizontal_segments", []), max_snap_y))
    y1 = float(snap_edge_to_lines(y1, geometry.get("horizontal_segments", []), max_snap_y))
    if x1 <= x0 + 10:
        x1 = min(float(rw - 2), x0 + max(20.0, rw * 0.12))
    if y1 <= y0 + 10:
        y1 = min(float(rh - 2), y0 + max(24.0, rh * 0.14))
    return x0, y0, x1, y1



def _core_element_from_local_bbox_v6(
    region_bbox_px: Tuple[int, int, int, int],
    bbox_local: Tuple[float, float, float, float],
    image_size: Tuple[int, int],
    label: str,
) -> Dict[str, Any]:
    image_w, image_h = image_size
    x0, y0, x1, y1 = bbox_local
    x_norm, y_norm = local_point_to_page_norm(region_bbox_px, x0, y0, image_w, image_h)
    return {
        "type": "core",
        "x": x_norm,
        "y": y_norm,
        "w": round(clamp((x1 - x0) / max(image_w, 1), 0.03, 0.35), 6),
        "h": round(clamp((y1 - y0) / max(image_h, 1), 0.03, 0.35), 6),
        "label": clean_pdf_text(label) or "Kjerne",
    }



def _column_element_from_local_v6(
    region_bbox_px: Tuple[int, int, int, int],
    local_x: float,
    local_y: float,
    image_size: Tuple[int, int],
    label: str,
) -> Dict[str, Any]:
    image_w, image_h = image_size
    x_norm, y_norm = local_point_to_page_norm(region_bbox_px, local_x, local_y, image_w, image_h)
    return {"type": "column", "x": x_norm, "y": y_norm, "label": clean_pdf_text(label)}



def _line_element_from_local_v6(
    kind: str,
    region_bbox_px: Tuple[int, int, int, int],
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    image_size: Tuple[int, int],
    label: str,
) -> Optional[Dict[str, Any]]:
    image_w, image_h = image_size
    if math.hypot(x2 - x1, y2 - y1) < 18:
        return None
    x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, x1, y1, image_w, image_h)
    x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, x2, y2, image_w, image_h)
    return {
        "type": kind,
        "x1": x1_norm,
        "y1": y1_norm,
        "x2": x2_norm,
        "y2": y2_norm,
        "label": clean_pdf_text(label),
    }



def _segment_to_wall_element_v6(
    segment: Dict[str, Any],
    region_bbox_px: Tuple[int, int, int, int],
    image_size: Tuple[int, int],
    label: str,
) -> Optional[Dict[str, Any]]:
    if segment.get("kind") == "vertical":
        return _line_element_from_local_v6(
            "wall",
            region_bbox_px,
            float(segment["center"]),
            float(segment["y"]),
            float(segment["center"]),
            float(segment["y"] + segment["h"]),
            image_size,
            label,
        )
    return _line_element_from_local_v6(
        "wall",
        region_bbox_px,
        float(segment["x"]),
        float(segment["center"]),
        float(segment["x"] + segment["w"]),
        float(segment["center"]),
        image_size,
        label,
    )



def _select_perimeter_segments_v6(geometry: Dict[str, Any], max_items: int = 3) -> List[Dict[str, Any]]:
    rw, rh = geometry.get("crop_size", (0, 0))
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    for segment in geometry.get("vertical_segments", []):
        center = float(segment.get("center", 0.0))
        edge_dist = min(center, max(0.0, rw - center))
        if edge_dist > rw * 0.22:
            continue
        score = float(segment.get("length", 0.0)) - edge_dist * 0.4
        candidates.append((score, segment))
    for segment in geometry.get("horizontal_segments", []):
        center = float(segment.get("center", 0.0))
        edge_dist = min(center, max(0.0, rh - center))
        if edge_dist > rh * 0.22:
            continue
        score = float(segment.get("length", 0.0)) - edge_dist * 0.4
        candidates.append((score, segment))
    selected: List[Dict[str, Any]] = []
    for _, segment in sorted(candidates, key=lambda item: item[0], reverse=True):
        if any(
            segment.get("kind") == other.get("kind")
            and abs(float(segment.get("center", 0.0)) - float(other.get("center", 0.0))) < (24 if segment.get("kind") == "vertical" else 18)
            for other in selected
        ):
            continue
        selected.append(segment)
        if len(selected) >= max_items:
            break
    return selected



def generate_transfer_basement_elements_v6(
    geometry: Dict[str, Any],
    image_size: Tuple[int, int],
    transfer_hints: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    region_bbox_px = geometry["bbox_px"]
    rw, rh = geometry.get("crop_size", (0, 0))
    elements: List[Dict[str, Any]] = []

    projected_core_boxes = []
    if transfer_hints:
        for box in transfer_hints.get("core_boxes", [])[:3]:
            x0 = float(box.get("x", 0.0)) * rw
            y0 = float(box.get("y", 0.0)) * rh
            x1 = x0 + float(box.get("w", 0.16)) * rw
            y1 = y0 + float(box.get("h", 0.18)) * rh
            projected_core_boxes.append(_snap_bbox_to_geometry_v6(x0, y0, x1, y1, geometry))
    if not projected_core_boxes:
        core_x, core_y, core_w, core_h = geometry.get("core_bbox", (rw * 0.40, rh * 0.35, rw * 0.18, rh * 0.22))
        projected_core_boxes.append((float(core_x), float(core_y), float(core_x + core_w), float(core_y + core_h)))

    for idx, bbox_local in enumerate(projected_core_boxes, start=1):
        elements.append(_core_element_from_local_bbox_v6(region_bbox_px, bbox_local, image_size, f"Kjerne {idx}"))

    for segment in _select_perimeter_segments_v6(geometry, max_items=3):
        wall = _segment_to_wall_element_v6(segment, region_bbox_px, image_size, "Perimetervegg")
        if wall is not None:
            elements.append(wall)

    x_targets = [float(item.get("pos", 0.0)) * rw for item in (transfer_hints or {}).get("x_clusters", [])[:5]]
    if not x_targets:
        x_targets = [float(line.get("pos", 0.0)) for line in geometry.get("grid_x", [])[:4]]
    y_targets = [float(line.get("pos", 0.0)) for line in geometry.get("grid_y", [])[:3]]
    if not y_targets and geometry.get("junctions"):
        y_targets = [float(item.get("y", 0.0)) for item in sorted(geometry.get("junctions", []), key=lambda item: float(item.get("score", 0.0)), reverse=True)[:3]]

    support_columns: List[Dict[str, Any]] = []
    for x_target in x_targets:
        for y_target in y_targets:
            snapped_x, snapped_y = _snap_point_with_transfer_hints_v6(x_target, y_target, geometry, transfer_hints, prefer_transfer=True)
            support_columns.append({"x": snapped_x, "y": snapped_y})
    if len(support_columns) < 6:
        for junction in sorted(geometry.get("junctions", []), key=lambda item: float(item.get("score", 0.0)), reverse=True):
            support_columns.append({"x": float(junction.get("x", 0.0)), "y": float(junction.get("y", 0.0))})
            if len(support_columns) >= 10:
                break

    chosen_points: List[Tuple[float, float]] = []
    min_spacing = max(24, int(min(rw, rh) * 0.12))
    for candidate in support_columns:
        cx = float(candidate["x"])
        cy = float(candidate["y"])
        if any((cx - px) ** 2 + (cy - py) ** 2 <= (min_spacing ** 2) for px, py in chosen_points):
            continue
        if any(point_inside_local_box(cx, cy, (int(b[0]), int(b[1]), int(b[2] - b[0]), int(b[3] - b[1])), margin=10) for b in projected_core_boxes):
            continue
        chosen_points.append((cx, cy))
        if len(chosen_points) >= 12:
            break

    for idx, (cx, cy) in enumerate(sorted(chosen_points, key=lambda item: (item[1], item[0])), start=1):
        elements.append(_column_element_from_local_v6(region_bbox_px, cx, cy, image_size, f"C{idx}"))

    notes = [
        "Kjeller tolkes som transfer-sone: søyler søkes under overliggende vegg- og kjernelinjer.",
        "Perimetervegger holdes ved åpne randsoner der kjellergeometrien tillater det.",
        "Auto-plasseringen er kalibrert for mindre tilfeldig søylegrid i parkeringsplan.",
    ]
    return elements, notes



def _refine_core_element_v6(
    element: Dict[str, Any],
    drawing_record: Dict[str, Any],
    region_bbox_px: Tuple[int, int, int, int],
    geometry: Dict[str, Any],
    fallback_label: str,
) -> Optional[Dict[str, Any]]:
    image_w, image_h = drawing_record["image"].size
    x_local, y_local = page_norm_to_local_crop(float(element.get("x", 0.0)), float(element.get("y", 0.0)), drawing_record["image"].size, region_bbox_px)
    w_local = float(element.get("w", 0.0)) * image_w
    h_local = float(element.get("h", 0.0)) * image_h
    rw, rh = geometry.get("crop_size", (0, 0))
    if w_local <= 8 or h_local <= 8:
        core_x, core_y, core_w, core_h = geometry.get("core_bbox", (rw * 0.40, rh * 0.35, rw * 0.18, rh * 0.22))
        x_local, y_local, w_local, h_local = float(core_x), float(core_y), float(core_w), float(core_h)
    bbox_local = _snap_bbox_to_geometry_v6(x_local, y_local, x_local + w_local, y_local + h_local, geometry)
    return _core_element_from_local_bbox_v6(region_bbox_px, bbox_local, drawing_record["image"].size, clean_pdf_text(element.get("label", "")) or fallback_label)



def _refine_column_element_v6(
    element: Dict[str, Any],
    drawing_record: Dict[str, Any],
    region_bbox_px: Tuple[int, int, int, int],
    geometry: Dict[str, Any],
    transfer_hints: Optional[Dict[str, Any]],
    prefer_transfer: bool,
    fallback_label: str,
) -> Optional[Dict[str, Any]]:
    local_x, local_y = page_norm_to_local_crop(float(element.get("x", 0.0)), float(element.get("y", 0.0)), drawing_record["image"].size, region_bbox_px)
    snapped_x, snapped_y = _snap_point_with_transfer_hints_v6(local_x, local_y, geometry, transfer_hints, prefer_transfer=prefer_transfer)
    return _column_element_from_local_v6(region_bbox_px, snapped_x, snapped_y, drawing_record["image"].size, clean_pdf_text(element.get("label", "")) or fallback_label)



def _refine_linear_element_v6(
    element: Dict[str, Any],
    drawing_record: Dict[str, Any],
    region_bbox_px: Tuple[int, int, int, int],
    geometry: Dict[str, Any],
    kind: str,
    fallback_label: str,
) -> Optional[Dict[str, Any]]:
    x1, y1 = page_norm_to_local_crop(float(element.get("x1", 0.0)), float(element.get("y1", 0.0)), drawing_record["image"].size, region_bbox_px)
    x2, y2 = page_norm_to_local_crop(float(element.get("x2", 0.0)), float(element.get("y2", 0.0)), drawing_record["image"].size, region_bbox_px)
    rw, rh = geometry.get("crop_size", (0, 0))
    dx = x2 - x1
    dy = y2 - y1
    vertical = abs(dx) <= abs(dy)

    if vertical:
        center_x = (x1 + x2) / 2.0
        candidates = [seg for seg in geometry.get("vertical_segments", []) if float(seg.get("length", 0.0)) >= max(28, int(rh * 0.14))]
        segment = min(candidates, key=lambda seg: abs(float(seg.get("center", 0.0)) - center_x)) if candidates else None
        if segment is not None and abs(float(segment.get("center", 0.0)) - center_x) <= max(22, int(rw * 0.10)):
            x_line = float(segment.get("center", center_x))
            y_start = max(min(y1, y2), float(segment.get("y", min(y1, y2))))
            y_end = min(max(y1, y2), float(segment.get("y", 0.0) + segment.get("h", 0.0)))
            if (y_end - y_start) < max(26, int(rh * 0.08)):
                y_start = float(segment.get("y", y_start))
                y_end = float(segment.get("y", 0.0) + segment.get("h", 0.0))
        else:
            x_line = float(min(geometry.get("grid_x", [{"pos": center_x}]), key=lambda item: abs(float(item.get("pos", center_x)) - center_x)).get("pos", center_x)) if geometry.get("grid_x") else center_x
            y_start = float(clamp(min(y1, y2), 0, max(rh - 2, 1)))
            y_end = float(clamp(max(y1, y2), y_start + 12, max(rh - 1, y_start + 12)))
        return _line_element_from_local_v6(kind, region_bbox_px, x_line, y_start, x_line, y_end, drawing_record["image"].size, clean_pdf_text(element.get("label", "")) or fallback_label)

    center_y = (y1 + y2) / 2.0
    candidates = [seg for seg in geometry.get("horizontal_segments", []) if float(seg.get("length", 0.0)) >= max(28, int(rw * 0.14))]
    segment = min(candidates, key=lambda seg: abs(float(seg.get("center", 0.0)) - center_y)) if candidates else None
    if segment is not None and abs(float(segment.get("center", 0.0)) - center_y) <= max(22, int(rh * 0.10)):
        y_line = float(segment.get("center", center_y))
        x_start = max(min(x1, x2), float(segment.get("x", min(x1, x2))))
        x_end = min(max(x1, x2), float(segment.get("x", 0.0) + segment.get("w", 0.0)))
        if (x_end - x_start) < max(26, int(rw * 0.08)):
            x_start = float(segment.get("x", x_start))
            x_end = float(segment.get("x", 0.0) + segment.get("w", 0.0))
    else:
        y_line = float(min(geometry.get("grid_y", [{"pos": center_y}]), key=lambda item: abs(float(item.get("pos", center_y)) - center_y)).get("pos", center_y)) if geometry.get("grid_y") else center_y
        x_start = float(clamp(min(x1, x2), 0, max(rw - 2, 1)))
        x_end = float(clamp(max(x1, x2), x_start + 12, max(rw - 1, x_start + 12)))
    return _line_element_from_local_v6(kind, region_bbox_px, x_start, y_line, x_end, y_line, drawing_record["image"].size, clean_pdf_text(element.get("label", "")) or fallback_label)



def _dedupe_refined_elements_v6(elements: List[Dict[str, Any]], image_size: Tuple[int, int]) -> List[Dict[str, Any]]:
    image_w, image_h = image_size
    columns: List[Dict[str, Any]] = []
    cores: List[Dict[str, Any]] = []
    lines: List[Dict[str, Any]] = []
    others: List[Dict[str, Any]] = []

    def line_px(element: Dict[str, Any]) -> Tuple[float, float, float, float]:
        return (
            float(element.get("x1", 0.0)) * image_w,
            float(element.get("y1", 0.0)) * image_h,
            float(element.get("x2", 0.0)) * image_w,
            float(element.get("y2", 0.0)) * image_h,
        )

    for element in elements:
        e_type = clean_pdf_text(element.get("type", "")).lower()
        if e_type == "column":
            px, py = page_norm_to_px(float(element.get("x", 0.0)), float(element.get("y", 0.0)), image_w, image_h)
            if any((px - ox) ** 2 + (py - oy) ** 2 <= (22 ** 2) for ox, oy in [page_norm_to_px(float(item.get("x", 0.0)), float(item.get("y", 0.0)), image_w, image_h) for item in columns]):
                continue
            columns.append(element)
        elif e_type == "core":
            candidate_bbox = (
                int(float(element.get("x", 0.0)) * image_w),
                int(float(element.get("y", 0.0)) * image_h),
                int(float(element.get("w", 0.0)) * image_w),
                int(float(element.get("h", 0.0)) * image_h),
            )
            if any(bbox_iou(candidate_bbox, (int(float(item.get("x", 0.0)) * image_w), int(float(item.get("y", 0.0)) * image_h), int(float(item.get("w", 0.0)) * image_w), int(float(item.get("h", 0.0)) * image_h))) > 0.45 for item in cores):
                continue
            cores.append(element)
        elif e_type in {"wall", "beam", "span_arrow"}:
            cx1, cy1, cx2, cy2 = line_px(element)
            vertical = abs(cx2 - cx1) <= abs(cy2 - cy1)
            midpoint = ((cx1 + cx2) / 2.0, (cy1 + cy2) / 2.0)
            length = math.hypot(cx2 - cx1, cy2 - cy1)
            duplicate = False
            for other in lines:
                if clean_pdf_text(other.get("type", "")).lower() != e_type:
                    continue
                ox1, oy1, ox2, oy2 = line_px(other)
                other_vertical = abs(ox2 - ox1) <= abs(oy2 - oy1)
                if other_vertical != vertical:
                    continue
                other_midpoint = ((ox1 + ox2) / 2.0, (oy1 + oy2) / 2.0)
                other_length = math.hypot(ox2 - ox1, oy2 - oy1)
                if math.hypot(midpoint[0] - other_midpoint[0], midpoint[1] - other_midpoint[1]) <= 24 and abs(length - other_length) <= 80:
                    duplicate = True
                    break
            if not duplicate:
                lines.append(element)
        else:
            others.append(element)

    ordered = cores + lines + columns + others
    col_idx = 1
    core_idx = 1
    wall_idx = 1
    beam_idx = 1
    span_idx = 1
    for element in ordered:
        e_type = clean_pdf_text(element.get("type", "")).lower()
        label = clean_pdf_text(element.get("label", ""))
        if e_type == "column":
            element["label"] = label or f"C{col_idx}"
            col_idx += 1
        elif e_type == "core":
            element["label"] = label or f"Kjerne {core_idx}"
            core_idx += 1
        elif e_type == "wall":
            element["label"] = label or f"Bærevegg {wall_idx}"
            wall_idx += 1
        elif e_type == "beam":
            element["label"] = label or ("Primærdrager" if beam_idx == 1 else f"Bjelke {beam_idx}")
            beam_idx += 1
        elif e_type == "span_arrow":
            element["label"] = label or ("Spennretning" if span_idx == 1 else f"Spenn {span_idx}")
            span_idx += 1
    return ordered



def infer_sketch_mode_v6(
    record: Dict[str, Any],
    sketch: Optional[Dict[str, Any]],
    analysis_result: Dict[str, Any],
    material_preference: str,
) -> str:
    counts = count_elements_by_type(sketch or {}) if isinstance(sketch, dict) else {"column": 0, "wall": 0, "beam": 0, "core": 0, "span_arrow": 0}
    if is_basement_like_record_v6(record, sketch):
        return "transfer_basement"
    if counts.get("wall", 0) >= max(3, counts.get("column", 0) + 1):
        return "wall_core"
    if counts.get("core", 0) >= 2 and counts.get("column", 0) <= counts.get("wall", 0) + 1:
        return "wall_core"
    if counts.get("column", 0) >= max(5, counts.get("wall", 0) + 2):
        return "column_core"
    return structural_mode_from_analysis(analysis_result, material_preference)



def refine_sketch_with_geometry_v6(
    record: Dict[str, Any],
    sketch: Optional[Dict[str, Any]],
    analysis_result: Dict[str, Any],
    material_preference: str,
    transfer_hints: Optional[Dict[str, Any]] = None,
    forced_region: Optional[Dict[str, Any]] = None,
    forced_region_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if not is_plan_like_record_v6(record):
        return None
    if forced_region is None:
        region, region_index = _choose_region_for_sketch_v6(record, sketch)
    else:
        region = forced_region
        region_index = int(forced_region_index or 0)
    geometry = build_plan_geometry_grounded(record["image"], region["bbox_px"])
    if not geometry:
        return None

    mode = infer_sketch_mode_v6(record, sketch, analysis_result, material_preference)
    if mode == "transfer_basement":
        fallback_elements, fallback_notes = generate_transfer_basement_elements_v6(geometry, record["image"].size, transfer_hints)
    elif mode == "wall_core":
        fallback_elements, fallback_notes = generate_wall_core_elements_grounded(geometry, record["image"].size)
        fallback_elements = [element for element in fallback_elements if clean_pdf_text(element.get("type", "")).lower() != "column"]
    else:
        fallback_elements, fallback_notes = generate_column_core_elements_grounded(geometry, record["image"].size)
        fallback_elements = [element for element in fallback_elements if clean_pdf_text(element.get("type", "")).lower() != "grid"]

    ai_elements = sketch.get("elements", []) if isinstance(sketch, dict) and isinstance(sketch.get("elements"), list) else []
    refined_elements: List[Dict[str, Any]] = []
    fallback_by_type: Dict[str, List[Dict[str, Any]]] = {}
    for element in fallback_elements:
        e_type = clean_pdf_text(element.get("type", "")).lower()
        fallback_by_type.setdefault(e_type, []).append(element)

    ai_cores = [element for element in ai_elements if clean_pdf_text(element.get("type", "")).lower() == "core"]
    if ai_cores:
        for idx, element in enumerate(ai_cores[:4], start=1):
            refined = _refine_core_element_v6(element, record, region["bbox_px"], geometry, f"Kjerne {idx}")
            if refined is not None:
                refined_elements.append(refined)
    else:
        refined_elements.extend(fallback_by_type.get("core", [])[: (3 if mode == "transfer_basement" else 2)])

    ai_walls = [element for element in ai_elements if clean_pdf_text(element.get("type", "")).lower() == "wall"]
    if ai_walls:
        for idx, element in enumerate(ai_walls[:10], start=1):
            refined = _refine_linear_element_v6(element, record, region["bbox_px"], geometry, "wall", f"Bærevegg {idx}")
            if refined is not None:
                refined_elements.append(refined)
    else:
        if mode in {"wall_core", "transfer_basement"}:
            refined_elements.extend(fallback_by_type.get("wall", [])[:6])

    ai_columns = [element for element in ai_elements if clean_pdf_text(element.get("type", "")).lower() == "column"]
    if mode != "wall_core":
        if ai_columns:
            for idx, element in enumerate(ai_columns[:16], start=1):
                refined = _refine_column_element_v6(element, record, region["bbox_px"], geometry, transfer_hints, mode == "transfer_basement", f"C{idx}")
                if refined is not None:
                    refined_elements.append(refined)
        needed_columns = 0
        if mode == "transfer_basement":
            needed_columns = max(6, len(fallback_by_type.get("column", [])))
        elif mode == "column_core" and not ai_columns:
            needed_columns = max(4, len(fallback_by_type.get("column", [])))
        current_columns = sum(1 for element in refined_elements if clean_pdf_text(element.get("type", "")).lower() == "column")
        if needed_columns > current_columns:
            refined_elements.extend(fallback_by_type.get("column", [])[: needed_columns - current_columns])

    ai_beams = [element for element in ai_elements if clean_pdf_text(element.get("type", "")).lower() == "beam"]
    if ai_beams:
        for idx, element in enumerate(ai_beams[:4], start=1):
            refined = _refine_linear_element_v6(element, record, region["bbox_px"], geometry, "beam", f"Bjelke {idx}")
            if refined is not None:
                refined_elements.append(refined)
    elif mode == "column_core":
        refined_elements.extend(fallback_by_type.get("beam", [])[:1])

    ai_spans = [element for element in ai_elements if clean_pdf_text(element.get("type", "")).lower() == "span_arrow"]
    if ai_spans:
        for idx, element in enumerate(ai_spans[:3], start=1):
            refined = _refine_linear_element_v6(element, record, region["bbox_px"], geometry, "span_arrow", f"Spenn {idx}")
            if refined is not None:
                refined_elements.append(refined)
    elif mode in {"wall_core", "column_core"}:
        refined_elements.extend(fallback_by_type.get("span_arrow", [])[:1])

    refined_elements = _dedupe_refined_elements_v6(refined_elements, record["image"].size)
    if not refined_elements:
        return None

    page_label = clean_pdf_text((sketch or {}).get("page_label", "")) or clean_pdf_text(record.get("label", "Tegning"))
    if forced_region is not None and len(get_plan_regions_for_record_v6(record)) > 1 and "delplan" not in page_label.lower():
        page_label = f"{page_label} - delplan {region_index + 1}"

    notes = []
    if mode == "transfer_basement":
        notes.append("Auto-skissen projiserer bæring i kjeller mot overliggende vegg- og kjerneprinsipper før rapport.")
    else:
        notes.append("AI-skissen er beholdt semantisk, men snappet til detektert plan-geometri før rapport.")
    notes.extend([clean_pdf_text(item) for item in fallback_notes[:3] if clean_pdf_text(item)])
    if isinstance(sketch, dict):
        for item in sketch.get("notes", [])[:3]:
            cleaned = clean_pdf_text(item)
            if cleaned and cleaned not in notes:
                notes.append(cleaned)

    return {
        "page_index": int(record.get("page_index", 0)),
        "region_index": int(region_index),
        "page_label": page_label,
        "plan_bbox": region.get("bbox_norm") or px_bbox_to_norm(region["bbox_px"], record["image"].size[0], record["image"].size[1]),
        "notes": notes[:5],
        "elements": refined_elements,
        "grounded_engine": True,
        "grounded_mode": mode,
    }



def calibrate_analysis_from_refined_sketches_v6(
    analysis_result: Dict[str, Any],
    refined_sketches: List[Dict[str, Any]],
    drawings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    analysis_result = deep_copy_jsonable(analysis_result)
    rec = deep_copy_jsonable(analysis_result.get("recommended_system", {}))
    upper_wall = upper_core = upper_column = 0
    basement_wall = basement_core = basement_column = 0

    for sketch in refined_sketches:
        record = lookup_record_by_page(drawings, int(sketch.get("page_index", -1)))
        if record is None:
            continue
        counts = count_elements_by_type(sketch)
        if is_basement_like_record_v6(record, sketch):
            basement_wall += counts["wall"]
            basement_core += counts["core"]
            basement_column += counts["column"]
        else:
            upper_wall += counts["wall"]
            upper_core += counts["core"]
            upper_column += counts["column"]

    observations = [clean_pdf_text(item) for item in analysis_result.get("observasjoner", []) if clean_pdf_text(item)]
    injected_obs = []

    if upper_wall + upper_core >= max(4, upper_column * 2):
        rec["vertical_system"] = "Tolket som veggbærende overetasjer med kjerner/skiver som hovedbæring og avstivning."
        rec["stability_system"] = "Kjerner og bærende veggskiver, med dekker som horisontale skiver."
        injected_obs.append("Autoanalysen er etterkalibrert mot vegg-/kjernebæring i overliggende plan før rapportskriving.")
    elif basement_column + upper_column >= max(5, upper_wall + basement_wall):
        rec["vertical_system"] = "Tolket som søylepreget eller blandet søyle-/veggsystem med kjerner for stabilitet."

    if basement_column > 0 and upper_wall > 0:
        rec["vertical_system"] = "Tolket som veggbærende overetasjer med transfer til søyler/vegger/kjerner i kjeller der planen åpner seg."
        rec["load_path"] = [
            "Dekker i boligetasjer -> bærende vegger og kjerner.",
            "Lastene føres videre til søyler, vegger og kjerner i kjeller der parkeringsplanen åpner bæresystemet.",
            "Kjerner/skiver og fundamenter tar laster videre til grunnen.",
        ]
        injected_obs.append("Kjeller er tolket som transfer-sone der søyleplassering søkes under overliggende vegg- og kjerne-laster.")

    note = "Auto-skissene er ikke lenger generert som frie standardoppsett; AI-semantikk og plan-geometri er slått sammen før rapport og redigering."
    if note not in observations:
        observations = [note] + observations
    for item in reversed(injected_obs):
        if item not in observations:
            observations = [item] + observations

    analysis_result["recommended_system"] = rec
    analysis_result["observasjoner"] = observations[:10]
    return analysis_result



def generate_grounded_sketches(
    drawings: List[Dict[str, Any]],
    analysis_result: Dict[str, Any],
    material_preference: str,
    max_sketches: int = 3,
) -> List[Dict[str, Any]]:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None:
        return []

    ai_sketches = deep_copy_jsonable(analysis_result.get("sketches", [])) if isinstance(analysis_result.get("sketches", []), list) else []
    transfer_hints = collect_transfer_hints_v6(ai_sketches, drawings)
    refined: List[Dict[str, Any]] = []
    used_keys = set()

    for sketch in ai_sketches:
        if not isinstance(sketch, dict):
            continue
        record = lookup_record_by_page(drawings, int(sketch.get("page_index", -1)))
        if record is None:
            continue
        refined_sketch = refine_sketch_with_geometry_v6(record, sketch, analysis_result, material_preference, transfer_hints)
        if refined_sketch is None:
            continue
        key = (int(refined_sketch.get("page_index", -1)), int(refined_sketch.get("region_index", 0)))
        if key in used_keys:
            continue
        refined.append(refined_sketch)
        used_keys.add(key)
        if len(refined) >= max_sketches:
            return refined[:max_sketches]

    for record in drawings:
        if not is_plan_like_record_v6(record):
            continue
        regions = get_plan_regions_for_record_v6(record)
        for region_index, region in enumerate(regions[:2]):
            key = (int(record.get("page_index", -1)), int(region_index))
            if key in used_keys:
                continue
            seed_sketch = {
                "page_index": int(record.get("page_index", 0)),
                "page_label": clean_pdf_text(record.get("label", "Tegning")),
                "elements": [],
                "notes": [],
            }
            refined_sketch = refine_sketch_with_geometry_v6(
                record,
                seed_sketch,
                analysis_result,
                material_preference,
                transfer_hints,
                forced_region=region,
                forced_region_index=region_index,
            )
            if refined_sketch is None:
                continue
            refined.append(refined_sketch)
            used_keys.add(key)
            if len(refined) >= max_sketches:
                return refined[:max_sketches]

    return refined[:max_sketches]



def replace_analysis_sketches_with_grounded(
    analysis_result: Dict[str, Any],
    drawings: List[Dict[str, Any]],
    material_preference: str,
) -> Dict[str, Any]:
    try:
        grounded_sketches = generate_grounded_sketches(drawings, analysis_result, material_preference, max_sketches=3)
    except Exception as exc:
        observations = [clean_pdf_text(item) for item in analysis_result.get("observasjoner", []) if clean_pdf_text(item)]
        warning = f"Geometri-kalibrering hoppet over i v11 etter feil: {type(exc).__name__}: {short_text(exc, 160)}"
        if warning not in observations:
            analysis_result["observasjoner"] = [warning] + observations
        st.session_state["rib_grounding_error"] = warning
        return analysis_result

    if grounded_sketches:
        analysis_result["sketches"] = grounded_sketches
        try:
            analysis_result = calibrate_analysis_from_refined_sketches_v6(analysis_result, grounded_sketches, drawings)
        except Exception as exc:
            observations = [clean_pdf_text(item) for item in analysis_result.get("observasjoner", []) if clean_pdf_text(item)]
            warning = f"Skissekalibrering beholdt grunnutkastet etter feil: {type(exc).__name__}: {short_text(exc, 160)}"
            if warning not in observations:
                analysis_result["observasjoner"] = [warning] + observations
        observations = analysis_result.get("observasjoner", [])
        note = "Konseptskissene er v11-snappet og kalibrert mot geometri når det lykkes; ved feil beholdes AI-utkastet i stedet for å krasje analysen."
        if note not in observations:
            analysis_result["observasjoner"] = [note] + observations
    return analysis_result




# ------------------------------------------------------------
# 13C. V9 OPENAI VISION + KONSERVATIV GEOMETRIFORANKRING
# ------------------------------------------------------------
_RUN_STRUCTURED_DRAWING_ANALYSIS_V8_BASE = run_structured_drawing_analysis
_BUILD_PLAN_GEOMETRY_GROUNDED_V8_BASE = build_plan_geometry_grounded
_GENERATE_WALL_CORE_ELEMENTS_GROUNDED_V8_BASE = generate_wall_core_elements_grounded
_REFINE_SKETCH_WITH_GEOMETRY_V8_BASE = refine_sketch_with_geometry_v6


def _norm01_v9(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except Exception:
        return float(clamp(default, 0.0, 1.0))
    if abs(v) > 100:
        v = v / 1000.0
    elif abs(v) > 1.5:
        v = v / 100.0
    return float(clamp(v, 0.0, 1.0))


def _normalize_bbox_v9(bbox: Any) -> Optional[Dict[str, float]]:
    if not isinstance(bbox, dict):
        return None
    x = _norm01_v9(bbox.get("x"), 0.0)
    y = _norm01_v9(bbox.get("y"), 0.0)
    w = _norm01_v9(bbox.get("w"), 0.0)
    h = _norm01_v9(bbox.get("h"), 0.0)
    if w <= 0.01 or h <= 0.01:
        return None
    w = float(clamp(w, 0.02, max(0.02, 1.0 - x)))
    h = float(clamp(h, 0.02, max(0.02, 1.0 - y)))
    return {"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)}


def _normalize_line_candidate_v9(candidate: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(candidate, dict):
        return None
    x1 = _norm01_v9(candidate.get("x1"), 0.0)
    y1 = _norm01_v9(candidate.get("y1"), 0.0)
    x2 = _norm01_v9(candidate.get("x2"), 0.0)
    y2 = _norm01_v9(candidate.get("y2"), 0.0)
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 0.02:
        return None
    orientation = "vertical" if abs(x2 - x1) <= abs(y2 - y1) else "horizontal"
    return {
        "x1": round(x1, 6),
        "y1": round(y1, 6),
        "x2": round(x2, 6),
        "y2": round(y2, 6),
        "confidence": float(clamp(float(candidate.get("confidence", 0.65) or 0.65), 0.0, 1.0)),
        "reason": clean_pdf_text(candidate.get("reason", "")),
        "orientation": orientation,
    }


def _normalize_point_candidate_v9(candidate: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(candidate, dict):
        return None
    x = _norm01_v9(candidate.get("x"), 0.0)
    y = _norm01_v9(candidate.get("y"), 0.0)
    return {
        "x": round(x, 6),
        "y": round(y, 6),
        "confidence": float(clamp(float(candidate.get("confidence", 0.55) or 0.55), 0.0, 1.0)),
        "reason": clean_pdf_text(candidate.get("reason", "")),
    }


def _normalize_page_cue_v9(cue: Any, record: Dict[str, Any]) -> Dict[str, Any]:
    page_index = int(record.get("page_index", 0))
    hint = clean_pdf_text(record.get("hint", "unknown")).lower() or "unknown"
    if not isinstance(cue, dict):
        cue = {}
    role = clean_pdf_text(cue.get("drawing_role", hint)).lower()
    if role not in {"plan", "section", "facade", "detail", "unknown"}:
        role = "plan" if hint == "plan" else "unknown"
    level_type = clean_pdf_text(cue.get("level_type", "unknown")).lower()
    if level_type not in {"upper_floor", "ground_floor", "basement", "parking", "roof", "unknown"}:
        level_type = "unknown"
    plan_bbox = _normalize_bbox_v9(cue.get("plan_bbox"))
    envelope_bbox = _normalize_bbox_v9(cue.get("exterior_envelope_bbox"))
    try:
        plan_confidence = float(cue.get("plan_confidence", 0.0))
    except Exception:
        plan_confidence = 0.0
    plan_confidence = float(clamp(plan_confidence, 0.0, 1.0))
    if role != "plan" and plan_confidence < 0.30:
        plan_bbox = None

    core_candidates = []
    for item in cue.get("core_candidates", []) if isinstance(cue.get("core_candidates"), list) else []:
        bbox = _normalize_bbox_v9(item)
        if bbox is None:
            continue
        try:
            conf = float(item.get("confidence", 0.65))
        except Exception:
            conf = 0.65
        bbox["confidence"] = float(clamp(conf, 0.0, 1.0))
        core_candidates.append(bbox)
    core_candidates = core_candidates[:2]

    wall_candidates = []
    for item in cue.get("bearing_wall_candidates", []) if isinstance(cue.get("bearing_wall_candidates"), list) else []:
        norm = _normalize_line_candidate_v9(item)
        if norm is not None:
            wall_candidates.append(norm)
    wall_candidates = wall_candidates[:5]

    column_candidates = []
    for item in cue.get("column_candidates", []) if isinstance(cue.get("column_candidates"), list) else []:
        norm = _normalize_point_candidate_v9(item)
        if norm is not None:
            column_candidates.append(norm)
    column_candidates = column_candidates[:10]

    notes = []
    for note in cue.get("notes", []) if isinstance(cue.get("notes"), list) else []:
        cleaned = clean_pdf_text(note)
        if cleaned and cleaned not in notes:
            notes.append(cleaned)

    return {
        "page_index": page_index,
        "drawing_role": role,
        "plan_confidence": plan_confidence,
        "multi_plan": bool(cue.get("multi_plan", False)),
        "level_type": level_type,
        "plan_bbox": plan_bbox,
        "exterior_envelope_bbox": envelope_bbox,
        "core_candidates": core_candidates,
        "bearing_wall_candidates": wall_candidates,
        "column_candidates": column_candidates,
        "notes": notes[:5],
    }


def _page_cue_prompt_v9(record: Dict[str, Any]) -> str:
    return f"""
Du analyserer EN arkitekt-/plantegningsside for tidligfase RIB-konsept.

Returner KUN gyldig JSON. Ingen markdown. Ingen tekst utenfor JSON.

SIDE:
- page_index: {int(record.get('page_index', 0))}
- filnavn: {clean_pdf_text(record.get('name', ''))}
- etikett: {clean_pdf_text(record.get('label', ''))}
- hint_fra_filnavn: {clean_pdf_text(record.get('hint', 'unknown'))}

FAGRUTINER:
1. Skille alltid mellom tegningsramme, titleblock, målsetting, situasjonsinnstikk, terrasse-/landskapskontur, naboplan og selve byggets plan.
2. plan_bbox skal avgrense det relevante planutsnittet av bygget, ikke hele arket.
3. Yttervegg er IKKE automatisk bærende. Ikke marker full perimeter som bærevegg.
4. For bolig-/typiske etasjeplan skal leilighetsskiller, korridorvegger og kjerner prioriteres foran fasadelinjer.
5. For kjeller/parkering/transfer skal søyler bare foreslås der lastnedføring fra overliggende vegger/kjerner virker sannsynlig.
6. Når du er usikker: returner færre elementer, ikke flere.
7. Koordinater skal være normaliserte 0-1 relativt til HELE siden.

RETURNER DETTE JSON-OBJEKTET:
{{
  "page_index": {int(record.get('page_index', 0))},
  "drawing_role": "plan | section | facade | detail | unknown",
  "plan_confidence": 0.0,
  "multi_plan": false,
  "level_type": "upper_floor | ground_floor | basement | parking | roof | unknown",
  "plan_bbox": {{"x": 0.10, "y": 0.10, "w": 0.60, "h": 0.70}},
  "exterior_envelope_bbox": {{"x": 0.10, "y": 0.10, "w": 0.55, "h": 0.65}},
  "core_candidates": [
    {{"x": 0.45, "y": 0.35, "w": 0.10, "h": 0.16, "confidence": 0.0}}
  ],
  "bearing_wall_candidates": [
    {{"x1": 0.20, "y1": 0.22, "x2": 0.20, "y2": 0.72, "confidence": 0.0, "reason": "kort grunn"}}
  ],
  "column_candidates": [
    {{"x": 0.32, "y": 0.48, "confidence": 0.0, "reason": "kort grunn"}}
  ],
  "notes": ["kort observasjon"]
}}

Regler for innhold:
- Maks 2 core_candidates.
- Maks 5 bearing_wall_candidates.
- Maks 10 column_candidates.
- Hvis siden ikke er planlik: lav plan_confidence, tomme kandidatlister.
- Ikke bruk bearing_wall_candidates til å tegne en stor rektangulær boks rundt hele bygget.
""".strip()


def _detect_page_cues_with_openai_v9(model: Any, drawings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not (isinstance(model, dict) and model.get("provider") == "openai"):
        return []
    cues: List[Dict[str, Any]] = []
    for record in drawings[: min(len(drawings), 6)]:
        try:
            prompt = _page_cue_prompt_v9(record)
            raw = generate_text(model, [prompt, add_analysis_badge(record["image"], int(record.get("page_index", 0)), clean_pdf_text(record.get("label", "Tegning")))], temperature=0.02)
            cue = _normalize_page_cue_v9(safe_json_loads(raw), record)
        except Exception as exc:
            st.session_state["rib_ai_backend_warning"] = short_text(
                f"OpenAI sideanalyse delvis feilet: {type(exc).__name__}: {exc}",
                220,
            )
            cue = _normalize_page_cue_v9({}, record)
        cues.append(cue)
    return cues


def _expand_norm_bbox_v9(bbox: Optional[Dict[str, float]], margin: float = 0.03) -> Optional[Dict[str, float]]:
    if not isinstance(bbox, dict):
        return None
    x = float(clamp(float(bbox.get("x", 0.0)) - margin, 0.0, 1.0))
    y = float(clamp(float(bbox.get("y", 0.0)) - margin, 0.0, 1.0))
    w = float(bbox.get("w", 0.0)) + (margin * 2.0)
    h = float(bbox.get("h", 0.0)) + (margin * 2.0)
    w = float(clamp(w, 0.02, max(0.02, 1.0 - x)))
    h = float(clamp(h, 0.02, max(0.02, 1.0 - y)))
    return {"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)}


def _crop_image_from_norm_bbox_v9(image: Image.Image, bbox: Optional[Dict[str, float]]) -> Image.Image:
    if not isinstance(bbox, dict):
        return copy_rgb(image)
    x, y, w, h = norm_bbox_to_px(bbox, image.size[0], image.size[1])
    x0 = int(clamp(x, 0, max(image.size[0] - 2, 0)))
    y0 = int(clamp(y, 0, max(image.size[1] - 2, 0)))
    x1 = int(clamp(x + w, x0 + 2, image.size[0]))
    y1 = int(clamp(y + h, y0 + 2, image.size[1]))
    return copy_rgb(image).crop((x0, y0, x1, y1))


def _prepare_focus_images_from_page_cues_v9(drawings: List[Dict[str, Any]], page_cues: List[Dict[str, Any]]) -> List[Image.Image]:
    focus_images: List[Image.Image] = []
    for cue in sorted(page_cues, key=lambda item: float(item.get("plan_confidence", 0.0)), reverse=True):
        if clean_pdf_text(cue.get("drawing_role", "")).lower() != "plan":
            continue
        record = lookup_record_by_page(drawings, int(cue.get("page_index", -1)))
        if record is None:
            continue
        focus_bbox = _expand_norm_bbox_v9(cue.get("plan_bbox"), margin=0.04)
        cropped = _crop_image_from_norm_bbox_v9(record["image"], focus_bbox)
        focus_images.append(add_analysis_badge(cropped, int(record.get("page_index", 0)), f"Fokus - {clean_pdf_text(record.get('label', 'Tegning'))}"))
        if len(focus_images) >= 4:
            break
    if not focus_images:
        for record in drawings[: min(len(drawings), 3)]:
            focus_images.append(add_analysis_badge(record["image"], int(record.get("page_index", 0)), clean_pdf_text(record.get("label", "Tegning"))))
    return focus_images


def _build_seed_sketch_from_page_cue_v9(cue: Optional[Dict[str, Any]], record: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not isinstance(cue, dict):
        return None
    page_index = int(cue.get("page_index", record.get("page_index", 0) if isinstance(record, dict) else 0))
    page_label = clean_pdf_text(record.get("label", "")) if isinstance(record, dict) else f"Tegning {page_index + 1}"
    elements: List[Dict[str, Any]] = []

    for idx, core in enumerate(cue.get("core_candidates", [])[:2], start=1):
        bbox = _normalize_bbox_v9(core)
        if bbox is None:
            continue
        elements.append({
            "type": "core",
            "x": bbox["x"],
            "y": bbox["y"],
            "w": bbox["w"],
            "h": bbox["h"],
            "label": f"Kjerne {idx}",
        })

    for idx, wall in enumerate(cue.get("bearing_wall_candidates", [])[:5], start=1):
        line = _normalize_line_candidate_v9(wall)
        if line is None:
            continue
        elements.append({
            "type": "wall",
            "x1": line["x1"],
            "y1": line["y1"],
            "x2": line["x2"],
            "y2": line["y2"],
            "label": f"Bærevegg {idx}",
        })

    level_type = clean_pdf_text(cue.get("level_type", "unknown")).lower()
    if level_type in {"basement", "parking"}:
        for idx, point in enumerate(cue.get("column_candidates", [])[:8], start=1):
            norm = _normalize_point_candidate_v9(point)
            if norm is None:
                continue
            elements.append({
                "type": "column",
                "x": norm["x"],
                "y": norm["y"],
                "label": f"C{idx}",
            })

    notes = []
    for item in cue.get("notes", [])[:4]:
        cleaned = clean_pdf_text(item)
        if cleaned and cleaned not in notes:
            notes.append(cleaned)
    if cue.get("plan_confidence", 0.0) > 0.0:
        notes.insert(0, f"OpenAI fokuserte planutvalg: confidens ca {round(float(cue.get('plan_confidence', 0.0)), 2)}.")

    return {
        "page_index": page_index,
        "page_label": page_label,
        "plan_bbox": cue.get("plan_bbox"),
        "confidence": float(cue.get("plan_confidence", 0.0) or 0.0),
        "notes": notes[:5],
        "elements": elements,
    }


def _merge_sketch_with_page_cue_v9(sketch: Optional[Dict[str, Any]], cue: Optional[Dict[str, Any]], record: Dict[str, Any]) -> Dict[str, Any]:
    base = deep_copy_jsonable(sketch if isinstance(sketch, dict) else {})
    seed = _build_seed_sketch_from_page_cue_v9(cue, record)
    if not seed:
        return base
    original_elements = [item for item in base.get("elements", []) if isinstance(item, dict)]
    seed_elements = [item for item in seed.get("elements", []) if isinstance(item, dict)]

    merged_elements: List[Dict[str, Any]] = []
    seed_types = {clean_pdf_text(item.get("type", "")).lower() for item in seed_elements}
    merged_elements.extend(seed_elements)
    for element in original_elements:
        e_type = clean_pdf_text(element.get("type", "")).lower()
        if e_type in {"beam", "span_arrow", "grid"}:
            merged_elements.append(element)
        elif e_type not in seed_types:
            merged_elements.append(element)

    base["elements"] = merged_elements
    if seed.get("plan_bbox") and (not isinstance(base.get("plan_bbox"), dict)):
        base["plan_bbox"] = seed.get("plan_bbox")
    elif seed.get("plan_bbox") and isinstance(base.get("plan_bbox"), dict):
        page_area = float(record["image"].size[0] * record["image"].size[1])
        old_bbox = norm_bbox_to_px(base.get("plan_bbox"), record["image"].size[0], record["image"].size[1])
        old_area = float(max(old_bbox[2] * old_bbox[3], 1))
        if old_area > page_area * 0.72:
            base["plan_bbox"] = seed.get("plan_bbox")
    notes: List[str] = []
    for source in [seed.get("notes", []), base.get("notes", [])]:
        for item in source:
            cleaned = clean_pdf_text(item)
            if cleaned and cleaned not in notes:
                notes.append(cleaned)
    base["notes"] = notes[:6]
    if seed.get("confidence") is not None:
        try:
            base["confidence"] = max(float(base.get("confidence", 0.0) or 0.0), float(seed.get("confidence", 0.0) or 0.0))
        except Exception:
            base["confidence"] = float(seed.get("confidence", 0.0) or 0.0)
    return base


def _merge_page_cues_into_analysis_v9(
    analysis_result: Dict[str, Any],
    page_cues: List[Dict[str, Any]],
    drawings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    analysis_result = deep_copy_jsonable(analysis_result)
    analysis_result["page_cues"] = deep_copy_jsonable(page_cues)

    cue_map = {int(cue.get("page_index", -1)): cue for cue in page_cues if isinstance(cue, dict)}
    for record in drawings:
        cue = cue_map.get(int(record.get("page_index", -1)))
        if not cue:
            continue
        record["ai_page_cue_v9"] = deep_copy_jsonable(cue)
        role = clean_pdf_text(cue.get("drawing_role", "")).lower()
        if role in {"plan", "section", "facade", "detail"}:
            record["hint"] = role

    drawing_entries = []
    existing_drawings = analysis_result.get("drawings", []) if isinstance(analysis_result.get("drawings"), list) else []
    existing_map = {int(entry.get("page_index", -1)): deep_copy_jsonable(entry) for entry in existing_drawings if isinstance(entry, dict)}
    for record in drawings:
        idx = int(record.get("page_index", -1))
        entry = existing_map.get(idx, {
            "page_index": idx,
            "page_label": clean_pdf_text(record.get("label", f"Tegning {idx + 1}")),
            "drawing_role": clean_pdf_text(record.get("hint", "unknown")),
            "usable_for_overlay": clean_pdf_text(record.get("hint", "")).lower() == "plan",
            "observations": [],
        })
        cue = cue_map.get(idx)
        if cue:
            role = clean_pdf_text(cue.get("drawing_role", entry.get("drawing_role", "unknown"))).lower()
            entry["drawing_role"] = role if role in {"plan", "section", "facade", "detail", "unknown"} else entry.get("drawing_role", "unknown")
            entry["usable_for_overlay"] = bool(entry["drawing_role"] == "plan" and float(cue.get("plan_confidence", 0.0)) >= 0.30)
            observations = []
            for item in entry.get("observations", []):
                cleaned = clean_pdf_text(item)
                if cleaned and cleaned not in observations:
                    observations.append(cleaned)
            for item in cue.get("notes", [])[:3]:
                cleaned = clean_pdf_text(item)
                if cleaned and cleaned not in observations:
                    observations.append(cleaned)
            entry["observations"] = observations[:5]
        drawing_entries.append(entry)
    analysis_result["drawings"] = drawing_entries

    sketches = analysis_result.get("sketches", []) if isinstance(analysis_result.get("sketches"), list) else []
    normalized_sketches: List[Dict[str, Any]] = []
    seen_keys = set()
    for sketch in sketches:
        if not isinstance(sketch, dict):
            continue
        page_index = int(sketch.get("page_index", -1))
        record = lookup_record_by_page(drawings, page_index)
        cue = cue_map.get(page_index)
        merged = _merge_sketch_with_page_cue_v9(sketch, cue, record or {"image": Image.new("RGB", (1000, 1000)), "label": f"Tegning {page_index + 1}"})
        key = (page_index, clean_pdf_text(merged.get("page_label", "")))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        normalized_sketches.append(merged)

    if not normalized_sketches:
        for cue in sorted(page_cues, key=lambda item: float(item.get("plan_confidence", 0.0)), reverse=True):
            if clean_pdf_text(cue.get("drawing_role", "")).lower() != "plan":
                continue
            record = lookup_record_by_page(drawings, int(cue.get("page_index", -1)))
            seed = _build_seed_sketch_from_page_cue_v9(cue, record)
            if seed is None:
                continue
            normalized_sketches.append(seed)
            if len(normalized_sketches) >= 3:
                break

    if normalized_sketches:
        analysis_result["sketches"] = normalized_sketches[:3]

    observations = [clean_pdf_text(item) for item in analysis_result.get("observasjoner", []) if clean_pdf_text(item)]
    note = "OpenAI side-for-side planfokusering brukes for å avgrense riktig planutsnitt og redusere feil fra papirramme/perimeter før geometri-snapping."
    if note not in observations:
        observations = [note] + observations
    analysis_result["observasjoner"] = observations[:10]
    return analysis_result


def _largest_component_bbox_v9(mask) -> Optional[Tuple[int, int, int, int]]:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None or mask is None:
        return None
    binary = ((mask > 0).astype(np.uint8) * 255)
    if binary.size == 0 or binary.max() <= 0:
        return None
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if num_labels <= 1:
        return None
    best = None
    for idx in range(1, num_labels):
        x, y, w, h, area = stats[idx]
        if best is None or int(area) > best[4]:
            best = (int(x), int(y), int(w), int(h), int(area))
    if best is None:
        return None
    return (best[0], best[1], best[2], best[3])


def _segment_overlap_ratio_v9(segment: Dict[str, Any], footprint_bbox: Tuple[int, int, int, int], axis: str) -> float:
    fx, fy, fw, fh = footprint_bbox
    if axis == "vertical":
        overlap = max(0.0, min(float(segment["y"] + segment["h"]), float(fy + fh)) - max(float(segment["y"]), float(fy)))
        return overlap / max(float(segment["h"]), 1.0)
    overlap = max(0.0, min(float(segment["x"] + segment["w"]), float(fx + fw)) - max(float(segment["x"]), float(fx)))
    return overlap / max(float(segment["w"]), 1.0)


def _choose_spaced_segments_v9(items: List[Dict[str, Any]], value_key: str, limit: int, min_spacing: float) -> List[Dict[str, Any]]:
    chosen: List[Dict[str, Any]] = []
    for item in sorted(items, key=lambda entry: float(entry.get("score", 0.0)), reverse=True):
        value = float(item.get(value_key, 0.0))
        if all(abs(value - float(prev.get(value_key, 0.0))) >= min_spacing for prev in chosen):
            chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def _select_support_segments_v9(geometry: Dict[str, Any], axis: str) -> List[Dict[str, Any]]:
    segments = geometry.get("vertical_segments", []) if axis == "vertical" else geometry.get("horizontal_segments", [])
    rw, rh = geometry.get("crop_size", (0, 0))
    if not segments or rw <= 0 or rh <= 0:
        return segments
    footprint_bbox = geometry.get("footprint_bbox_local", (0, 0, rw, rh))
    core_bbox = geometry.get("core_bbox", (rw * 0.40, rh * 0.35, rw * 0.18, rh * 0.22))
    core_cx = float(core_bbox[0] + core_bbox[2] / 2.0)
    core_cy = float(core_bbox[1] + core_bbox[3] / 2.0)

    scored: List[Dict[str, Any]] = []
    for seg in segments:
        center = float(seg.get("center", 0.0))
        length = float(seg.get("length", 0.0))
        thickness = float(seg.get("thickness", 1.0))
        overlap_ratio = _segment_overlap_ratio_v9(seg, footprint_bbox, axis)
        if overlap_ratio < 0.35:
            continue

        if axis == "vertical":
            edge_gap = min(abs(center - footprint_bbox[0]), abs((footprint_bbox[0] + footprint_bbox[2]) - center))
            if length < max(42.0, rh * 0.18):
                continue
            if edge_gap < max(12.0, rw * 0.08) and length > rh * 0.52:
                continue
            score = (
                length * 1.0
                + overlap_ratio * 90.0
                + min(edge_gap, rw * 0.25) * 0.65
                - abs(center - core_cx) * 0.22
                + min(thickness, 12.0) * 2.5
            )
            if 0.22 <= (center / max(rw, 1.0)) <= 0.78:
                score += 14.0
            scored.append({**seg, "score": score, "axis_pos": center})
        else:
            edge_gap = min(abs(center - footprint_bbox[1]), abs((footprint_bbox[1] + footprint_bbox[3]) - center))
            if length < max(36.0, rw * 0.14):
                continue
            if edge_gap < max(12.0, rh * 0.10) and length > rw * 0.58:
                continue
            score = (
                length * 0.85
                + overlap_ratio * 80.0
                + min(edge_gap, rh * 0.24) * 0.50
                - abs(center - core_cy) * 0.14
                + min(thickness, 12.0) * 2.2
            )
            if 0.20 <= (center / max(rh, 1.0)) <= 0.80:
                score += 8.0
            scored.append({**seg, "score": score, "axis_pos": center})

    if not scored:
        return segments
    min_spacing = max(26.0, (rw if axis == "vertical" else rh) * 0.15)
    limited = _choose_spaced_segments_v9(scored, "axis_pos", 3 if axis == "vertical" else 2, min_spacing)
    return limited or segments


def build_plan_geometry_grounded(
    image: Image.Image,
    region_bbox_px: Tuple[int, int, int, int],
) -> Dict[str, Any]:
    geometry = _BUILD_PLAN_GEOMETRY_GROUNDED_V8_BASE(image, region_bbox_px)
    if not geometry:
        return geometry
    rw, rh = geometry.get("crop_size", (0, 0))
    footprint_bbox = _largest_component_bbox_v9(geometry.get("footprint_mask"))
    if footprint_bbox is None:
        footprint_bbox = (0, 0, rw, rh)
    geometry["footprint_bbox_local"] = footprint_bbox
    geometry["vertical_segments_all_v9"] = deep_copy_jsonable(geometry.get("vertical_segments", []))
    geometry["horizontal_segments_all_v9"] = deep_copy_jsonable(geometry.get("horizontal_segments", []))
    geometry["support_vertical_v9"] = _select_support_segments_v9(geometry, "vertical")
    geometry["support_horizontal_v9"] = _select_support_segments_v9(geometry, "horizontal")
    if geometry.get("support_vertical_v9"):
        geometry["vertical_segments"] = deep_copy_jsonable(geometry.get("support_vertical_v9", []))
    if geometry.get("support_horizontal_v9"):
        geometry["horizontal_segments"] = deep_copy_jsonable(geometry.get("support_horizontal_v9", []))
    return geometry


def generate_wall_core_elements_grounded(
    geometry: Dict[str, Any],
    image_size: Tuple[int, int],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    image_w, image_h = image_size
    region_bbox_px = geometry["bbox_px"]
    rw, rh = geometry["crop_size"]
    core_bbox = geometry["core_bbox"]
    core_x, core_y, core_w, core_h = core_bbox
    core_x_norm, core_y_norm = local_point_to_page_norm(region_bbox_px, core_x, core_y, image_w, image_h)

    elements: List[Dict[str, Any]] = [{
        "type": "core",
        "x": core_x_norm,
        "y": core_y_norm,
        "w": round(clamp(core_w / max(image_w, 1), 0.03, 0.30), 6),
        "h": round(clamp(core_h / max(image_h, 1), 0.03, 0.30), 6),
        "label": "Betongkjerne",
    }]

    chosen_vertical = geometry.get("support_vertical_v9", [])[:2]
    chosen_horizontal = geometry.get("support_horizontal_v9", [])[:1]

    wall_count = 1
    for segment in chosen_vertical + chosen_horizontal:
        if clean_pdf_text(segment.get("kind", "")).lower() == "vertical":
            x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, segment["center"], segment["y"], image_w, image_h)
            x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, segment["center"], segment["y"] + segment["h"], image_w, image_h)
        else:
            x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, segment["x"], segment["center"], image_w, image_h)
            x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, segment["x"] + segment["w"], segment["center"], image_w, image_h)
        elements.append({
            "type": "wall",
            "x1": x1_norm,
            "y1": y1_norm,
            "x2": x2_norm,
            "y2": y2_norm,
            "label": f"Bærevegg {wall_count}",
        })
        wall_count += 1

    if len(chosen_vertical) >= 2:
        left = min(chosen_vertical, key=lambda seg: float(seg.get("center", 0.0)))
        right = max(chosen_vertical, key=lambda seg: float(seg.get("center", 0.0)))
        arrow_y = min(rh - 16, int(core_y + core_h + max(16, rh * 0.07)))
        x1_norm, y1_norm = local_point_to_page_norm(region_bbox_px, left["center"], arrow_y, image_w, image_h)
        x2_norm, y2_norm = local_point_to_page_norm(region_bbox_px, right["center"], arrow_y, image_w, image_h)
        elements.append({
            "type": "span_arrow",
            "x1": x1_norm,
            "y1": y1_norm,
            "x2": x2_norm,
            "y2": y2_norm,
            "label": "Typisk spenn",
        })

    notes = [
        "Vegg-/kjerneforslaget er filtrert hardere mot indre, sannsynlige bæresoner i stedet for full perimeter.",
        "Perimeterlinjer og tegningsramme nedprioriteres før veggskisse genereres.",
        "Overetasjer holdes konservative: færre, men mer sannsynlige bærevegger.",
    ]
    return elements, notes


def _get_page_cue_for_record_v9(analysis_result: Dict[str, Any], record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cues = analysis_result.get("page_cues", []) if isinstance(analysis_result.get("page_cues"), list) else []
    page_index = int(record.get("page_index", -1))
    for cue in cues:
        if isinstance(cue, dict) and int(cue.get("page_index", -999)) == page_index:
            return cue
    cached = record.get("ai_page_cue_v9")
    return cached if isinstance(cached, dict) else None


def _cue_wall_locals_v9(cue: Optional[Dict[str, Any]], record: Dict[str, Any], region_bbox_px: Tuple[int, int, int, int]) -> List[Dict[str, float]]:
    out: List[Dict[str, float]] = []
    if not isinstance(cue, dict):
        return out
    for item in cue.get("bearing_wall_candidates", [])[:6]:
        line = _normalize_line_candidate_v9(item)
        if line is None:
            continue
        x1, y1 = page_norm_to_local_crop(line["x1"], line["y1"], record["image"].size, region_bbox_px)
        x2, y2 = page_norm_to_local_crop(line["x2"], line["y2"], record["image"].size, region_bbox_px)
        out.append({
            "x_mid": float((x1 + x2) / 2.0),
            "y_mid": float((y1 + y2) / 2.0),
            "vertical": 1.0 if abs(x2 - x1) <= abs(y2 - y1) else 0.0,
        })
    return out


def _cue_core_centers_local_v9(cue: Optional[Dict[str, Any]], record: Dict[str, Any], region_bbox_px: Tuple[int, int, int, int]) -> List[Tuple[float, float]]:
    centers: List[Tuple[float, float]] = []
    if not isinstance(cue, dict):
        return centers
    for item in cue.get("core_candidates", [])[:3]:
        bbox = _normalize_bbox_v9(item)
        if bbox is None:
            continue
        cx_norm = float(bbox["x"]) + float(bbox["w"]) / 2.0
        cy_norm = float(bbox["y"]) + float(bbox["h"]) / 2.0
        cx, cy = page_norm_to_local_crop(cx_norm, cy_norm, record["image"].size, region_bbox_px)
        centers.append((float(cx), float(cy)))
    return centers


def _wall_score_v9(
    metrics: Dict[str, float],
    geometry: Dict[str, Any],
    cue_walls: List[Dict[str, float]],
    cue_cores: List[Tuple[float, float]],
) -> float:
    rw, rh = geometry.get("crop_size", (0, 0))
    fx, fy, fw, fh = geometry.get("footprint_bbox_local", (0, 0, rw, rh))
    core_bbox = geometry.get("core_bbox", (rw * 0.40, rh * 0.35, rw * 0.18, rh * 0.22))
    core_cx = float(core_bbox[0] + core_bbox[2] / 2.0)
    core_cy = float(core_bbox[1] + core_bbox[3] / 2.0)

    if metrics["vertical"]:
        edge_gap = min(abs(metrics["x_mid"] - fx), abs((fx + fw) - metrics["x_mid"]))
        if metrics["length"] > fh * 0.72 and edge_gap < max(12.0, fw * 0.09):
            return -1e9
        overlap = max(0.0, min(metrics["y2"], fy + fh) - max(metrics["y1"], fy)) / max(metrics["length"], 1.0)
        score = metrics["length"] + overlap * 32.0 + min(edge_gap, fw * 0.24) * 0.55 - abs(metrics["x_mid"] - core_cx) * 0.16
    else:
        edge_gap = min(abs(metrics["y_mid"] - fy), abs((fy + fh) - metrics["y_mid"]))
        if metrics["length"] > fw * 0.64 and edge_gap < max(12.0, fh * 0.12):
            return -1e9
        overlap = max(0.0, min(metrics["x2"], fx + fw) - max(metrics["x1"], fx)) / max(metrics["length"], 1.0)
        score = metrics["length"] * 0.80 + overlap * 26.0 + min(edge_gap, fh * 0.22) * 0.40 - abs(metrics["y_mid"] - core_cy) * 0.10
        if metrics["length"] > fw * 0.55:
            score -= 18.0

    if cue_walls:
        compatible = [item for item in cue_walls if bool(round(item["vertical"])) == bool(metrics["vertical"])]
        if compatible:
            best = min(math.hypot(metrics["x_mid"] - item["x_mid"], metrics["y_mid"] - item["y_mid"]) for item in compatible)
            score += max(0.0, 70.0 - best * 1.5)

    if cue_cores:
        best_core = min(math.hypot(metrics["x_mid"] - cx, metrics["y_mid"] - cy) for cx, cy in cue_cores)
        score += max(0.0, 20.0 - best_core * 0.25)

    return score


def _best_wall_cue_distance_v11(metrics: Dict[str, float], cue_walls: List[Dict[str, float]]) -> float:
    compatible = [item for item in cue_walls if bool(round(item.get("vertical", 0.0))) == bool(metrics.get("vertical", False))]
    if not compatible:
        return float("inf")
    return float(min(math.hypot(metrics["x_mid"] - item["x_mid"], metrics["y_mid"] - item["y_mid"]) for item in compatible))


def _filter_upper_floor_elements_v9_impl(sketch: Dict[str, Any], record: Dict[str, Any], analysis_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    sketch = deep_copy_jsonable(sketch)
    geometry = get_geometry_for_sketch(record, sketch)
    if not geometry:
        return sketch

    cue = _get_page_cue_for_record_v9(analysis_result or {}, record) if isinstance(analysis_result, dict) else (record.get("ai_page_cue_v9") if isinstance(record.get("ai_page_cue_v9"), dict) else None)
    region_bbox_px = geometry["bbox_px"]
    cue_walls = _cue_wall_locals_v9(cue, record, region_bbox_px)
    cue_cores = _cue_core_centers_local_v9(cue, record, region_bbox_px)

    kept_cores = [element for element in sketch.get("elements", []) if clean_pdf_text(element.get("type", "")).lower() == "core"][:2]
    spans = [element for element in sketch.get("elements", []) if clean_pdf_text(element.get("type", "")).lower() == "span_arrow"][:1]
    beams = [element for element in sketch.get("elements", []) if clean_pdf_text(element.get("type", "")).lower() == "beam"][:1]

    rw, rh = geometry.get("crop_size", (0, 0))
    fx, fy, fw, fh = geometry.get("footprint_bbox_local", (0, 0, rw, rh))
    core_bbox = geometry.get("core_bbox", (rw * 0.40, rh * 0.35, rw * 0.18, rh * 0.22))
    core_cx = float(core_bbox[0] + core_bbox[2] / 2.0)
    core_cy = float(core_bbox[1] + core_bbox[3] / 2.0)

    vertical_candidates: List[Dict[str, Any]] = []
    horizontal_candidates: List[Dict[str, Any]] = []
    for element in sketch.get("elements", []):
        if clean_pdf_text(element.get("type", "")).lower() != "wall":
            continue
        metrics = _wall_metrics_local_v11(element, record, region_bbox_px)
        if metrics["length"] <= max(18.0, min(rw or 0, rh or 0) * 0.05):
            continue

        inside_x = min(abs(metrics["x_mid"] - fx), abs((fx + fw) - metrics["x_mid"])) / max(float(fw or 1.0), 1.0)
        inside_y = min(abs(metrics["y_mid"] - fy), abs((fy + fh) - metrics["y_mid"])) / max(float(fh or 1.0), 1.0)
        cue_dist = _best_wall_cue_distance_v11(metrics, cue_walls)

        if metrics["vertical"]:
            core_offset = abs(metrics["x_mid"] - core_cx)
            if metrics["length"] < max(28.0, float(fh or 0) * 0.12):
                continue
            if inside_x < 0.14:
                continue
            if metrics["length"] > float(fh or 0) * 0.62 and inside_x < 0.22 and cue_dist > max(32.0, float(fw or 0) * 0.16):
                continue
            if cue_walls:
                if cue_dist > max(52.0, float(fw or 0) * 0.24) and core_offset > float(fw or 0) * 0.30:
                    continue
            elif core_offset > float(fw or 0) * 0.26:
                continue
        else:
            core_offset = abs(metrics["y_mid"] - core_cy)
            if metrics["length"] < max(26.0, float(fw or 0) * 0.09):
                continue
            if inside_y < 0.16:
                continue
            if metrics["length"] > float(fw or 0) * 0.40 and inside_y < 0.22 and cue_dist > max(30.0, float(fh or 0) * 0.16):
                continue
            if metrics["length"] > float(fw or 0) * 0.50:
                continue
            if cue_walls:
                if cue_dist > max(46.0, float(fh or 0) * 0.22) and core_offset > float(fh or 0) * 0.24:
                    continue
            elif core_offset > float(fh or 0) * 0.18:
                continue

        score = _wall_score_v9(metrics, geometry, cue_walls, cue_cores)
        if cue_walls and math.isfinite(cue_dist):
            score += max(0.0, 55.0 - cue_dist * 1.15)
        if score <= -1e8:
            continue

        item = {"element": element, "score": score, "x_mid": metrics["x_mid"], "y_mid": metrics["y_mid"]}
        if metrics["vertical"]:
            vertical_candidates.append(item)
        else:
            horizontal_candidates.append(item)

    chosen_vertical = _choose_spaced_segments_v9(vertical_candidates, "x_mid", 2, max(30.0, float(rw or 0) * 0.18))
    chosen_horizontal = _choose_spaced_segments_v9(horizontal_candidates, "y_mid", 1, max(24.0, float(rh or 0) * 0.16))
    kept_walls = [item["element"] for item in chosen_vertical + chosen_horizontal]

    notes = [clean_pdf_text(item) for item in sketch.get("notes", []) if clean_pdf_text(item)]
    note = "Vegger i overetasjer er v11-filtrert mer konservativt mot kjerne, indre bæresoner og AI-cues for å unngå yttervegg/perimeter-feil."
    if note not in notes:
        notes = [note] + notes

    sketch["elements"] = kept_cores + kept_walls[:3] + spans + beams
    sketch["notes"] = notes[:6]
    return sketch


def _filter_transfer_basement_elements_v9_impl(sketch: Dict[str, Any], record: Dict[str, Any], analysis_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    sketch = deep_copy_jsonable(sketch)
    cue = _get_page_cue_for_record_v9(analysis_result or {}, record) if isinstance(analysis_result, dict) else (record.get("ai_page_cue_v9") if isinstance(record.get("ai_page_cue_v9"), dict) else None)
    if not isinstance(cue, dict):
        return sketch
    geometry = get_geometry_for_sketch(record, sketch)
    if not geometry:
        return sketch

    region_bbox_px = geometry["bbox_px"]
    cue_columns_local: List[Tuple[float, float]] = []
    for item in cue.get("column_candidates", [])[:10]:
        point = _normalize_point_candidate_v9(item)
        if point is None:
            continue
        px, py = page_norm_to_local_crop(point["x"], point["y"], record["image"].size, region_bbox_px)
        cue_columns_local.append((float(px), float(py)))

    kept_cores = [element for element in sketch.get("elements", []) if clean_pdf_text(element.get("type", "")).lower() == "core"][:2]
    walls = [element for element in sketch.get("elements", []) if clean_pdf_text(element.get("type", "")).lower() == "wall"]
    beams = [element for element in sketch.get("elements", []) if clean_pdf_text(element.get("type", "")).lower() == "beam"][:1]
    spans = [element for element in sketch.get("elements", []) if clean_pdf_text(element.get("type", "")).lower() == "span_arrow"][:1]

    ranked_columns: List[Dict[str, Any]] = []
    for element in sketch.get("elements", []):
        if clean_pdf_text(element.get("type", "")).lower() != "column":
            continue
        cx, cy = page_norm_to_local_crop(float(element.get("x", 0.0)), float(element.get("y", 0.0)), record["image"].size, region_bbox_px)
        if cue_columns_local:
            best = min(math.hypot(cx - px, cy - py) for px, py in cue_columns_local)
            score = max(0.0, 80.0 - best * 1.4)
        else:
            score = 0.0
        ranked_columns.append({"element": element, "score": score, "x": float(cx), "y": float(cy)})
    ranked_columns = _choose_spaced_segments_v9(ranked_columns, "x", min(8, max(4, len(cue_columns_local) or 6)), max(22.0, geometry.get("crop_size", (0, 0))[0] * 0.08))
    kept_columns = [item["element"] for item in sorted(ranked_columns, key=lambda item: (item["y"], item["x"]))]

    sketch["elements"] = kept_cores + walls[:3] + kept_columns[:8] + spans + beams
    notes = [clean_pdf_text(item) for item in sketch.get("notes", []) if clean_pdf_text(item)]
    note = "Kjeller/transfer er filtrert mot sannsynlig lastnedføring og OpenAI-kolonnepunkter før rapport."
    if note not in notes:
        notes = [note] + notes
    sketch["notes"] = notes[:6]
    return sketch


def _refine_sketch_with_geometry_v9_impl(
    record: Dict[str, Any],
    sketch: Optional[Dict[str, Any]],
    analysis_result: Dict[str, Any],
    material_preference: str,
    transfer_hints: Optional[Dict[str, Any]] = None,
    forced_region: Optional[Dict[str, Any]] = None,
    forced_region_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    cue = _get_page_cue_for_record_v9(analysis_result, record)
    working_sketch = _merge_sketch_with_page_cue_v9(sketch, cue, record)
    refined = _REFINE_SKETCH_WITH_GEOMETRY_V8_BASE(
        record,
        working_sketch,
        analysis_result,
        material_preference,
        transfer_hints,
        forced_region,
        forced_region_index,
    )
    if refined is None:
        return None
    if isinstance(cue, dict) and isinstance(cue.get("plan_bbox"), dict):
        refined["plan_bbox"] = cue.get("plan_bbox")
    mode = clean_pdf_text(refined.get("grounded_mode", "")).lower()
    if mode == "wall_core" and not is_basement_like_record_v6(record, refined):
        refined = _filter_upper_floor_elements_v9_impl(refined, record, analysis_result)
    elif mode == "transfer_basement":
        refined = _filter_transfer_basement_elements_v9_impl(refined, record, analysis_result)
    notes = [clean_pdf_text(item) for item in refined.get("notes", []) if clean_pdf_text(item)]
    if cue and cue.get("notes"):
        for item in cue.get("notes", [])[:2]:
            cleaned = clean_pdf_text(item)
            if cleaned and cleaned not in notes:
                notes.append(cleaned)
    refined["notes"] = notes[:6]
    return refined


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
    page_cues = _detect_page_cues_with_openai_v9(model, drawings)
    if not page_cues:
        return _RUN_STRUCTURED_DRAWING_ANALYSIS_V8_BASE(
            model=model,
            drawings=drawings,
            candidates=candidates,
            project_data=project_data,
            material_preference=material_preference,
            foundation_preference=foundation_preference,
            optimization_mode=optimization_mode,
            safety_mode=safety_mode,
        )

    focus_images = _prepare_focus_images_from_page_cues_v9(drawings, page_cues)
    matrix_txt = candidate_matrix_text(candidates)
    manifest = drawing_manifest_text(drawings)
    page_cues_json = json.dumps(page_cues, ensure_ascii=False)

    prompt = f"""
Du er Builtly RIB AI. Du skal lage en strukturert konseptanalyse for bæresystem.

Du får:
1. fokusbilder av planområder,
2. en side-for-side OpenAI-vision persepsjon som allerede har forsøkt å isolere riktig plan_bbox,
3. maskinell alternativmatrise.

VIKTIG:
- Bruk page_cues som primær persepsjon for hva som faktisk er planområde, kjerne, sannsynlige bærevegger og eventuelle søylepunkter.
- Ikke tegn papirramme, titleblock, målsetting, sitasjonsinnstikk eller full perimeter som bærevegger.
- For bolig-/typiske overetasjer: prioriter kjerne + indre bærevegger / leilighetsskiller / korridorvegger.
- For kjeller/parkering/transfer: bruk søyler bare der page_cues eller planen tilsier lastnedføring fra overliggende vegger/kjerner.
- Når du er usikker: færre elementer.
- Koordinater i sketches skal være normaliserte 0-1 relativt til HELE siden.

PROSJEKT:
- Navn: {clean_pdf_text(project_data.get('p_name'))}
- Type: {clean_pdf_text(project_data.get('b_type'))}
- BTA: {nb_value(project_data.get('bta'))} m2
- Etasjer: {nb_value(project_data.get('etasjer'))}
- Sted: {clean_pdf_text(project_data.get('adresse'))}, {clean_pdf_text(project_data.get('kommune'))}
- Foretrukket materiale: {clean_pdf_text(material_preference)}
- Fundamentering: {clean_pdf_text(foundation_preference)}
- Optimaliser for: {clean_pdf_text(optimization_mode)}
- Sikkerhetsmodus: {clean_pdf_text(safety_mode)}

MASKINELL ALTERNATIVMATRISEN:
{matrix_txt}

TEGNINGSMANIFEST:
{manifest}

PAGE_CUES JSON:
{page_cues_json}

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
      "page_label": "kort navn",
      "plan_bbox": {{"x": 0.10, "y": 0.12, "w": 0.55, "h": 0.68}},
      "confidence": 0.0,
      "notes": ["kort note"],
      "elements": [
        {{"type": "column", "x": 0.1, "y": 0.2, "label": "C1"}},
        {{"type": "core", "x": 0.45, "y": 0.35, "w": 0.14, "h": 0.18, "label": "K1"}},
        {{"type": "wall", "x1": 0.2, "y1": 0.2, "x2": 0.2, "y2": 0.7, "label": "Skive"}},
        {{"type": "beam", "x1": 0.1, "y1": 0.2, "x2": 0.75, "y2": 0.2, "label": "Primærdrager"}},
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

Viktige grenser:
- Maks 3 sketches.
- Skissene skal følge page_cues og være konservative.
- Ikke bruk store perimeter- eller rektangelskissser som bærevegger.
- Returner kun JSON.
""".strip()

    raw_text = generate_text(model, [prompt] + focus_images, temperature=0.04)
    parsed = safe_json_loads(raw_text)
    if not isinstance(parsed, dict):
        parsed = _RUN_STRUCTURED_DRAWING_ANALYSIS_V8_BASE(
            model=model,
            drawings=drawings,
            candidates=candidates,
            project_data=project_data,
            material_preference=material_preference,
            foundation_preference=foundation_preference,
            optimization_mode=optimization_mode,
            safety_mode=safety_mode,
        )
    normalized = normalize_analysis_result(parsed, candidates, drawings)
    normalized = _merge_page_cues_into_analysis_v9(normalized, page_cues, drawings)
    return normalized


refine_sketch_with_geometry_v6 = _refine_sketch_with_geometry_v9_impl
filter_upper_floor_elements_v7 = _filter_upper_floor_elements_v9_impl



# ------------------------------------------------------------
# 13B-13D PATCH BLOKK FLYTTET OPP I v10
# Må ligge over analyseknappen slik at regionvalg, wall-filter og editor-hooks
# er definert før første analyse-kjøring og før st.rerun().
# ------------------------------------------------------------
# ------------------------------------------------------------
# 13B. V7 PATCH - bedre planvalg, mer konservativ veggbæring,
#      og mer robust musepeker-editor for Streamlit.
# ------------------------------------------------------------
_RENDER_INLINE_CLICK_CANVAS_EDITOR_V6 = render_inline_click_canvas_editor
_RENDER_PLOTLY_SKETCH_EDITOR_V6 = render_plotly_sketch_editor
_GET_PLAN_REGIONS_FOR_RECORD_V6_BASE = get_plan_regions_for_record_v6
_CHOOSE_REGION_FOR_SKETCH_V6_BASE = _choose_region_for_sketch_v6
_REFINE_SKETCH_WITH_GEOMETRY_V6_BASE = refine_sketch_with_geometry_v6
_V7_COMPONENT_CACHE: Dict[str, Any] = {}


def optional_components_v2_v7() -> Any:
    try:
        import streamlit.components.v2 as components_v2  # type: ignore
        return components_v2
    except Exception:
        return None


def get_click_canvas_component_v2_v7() -> Any:
    components_v2 = optional_components_v2_v7()
    if components_v2 is None:
        return None
    cache_key = 'builtly_rib_click_canvas_v7'
    if cache_key in _V7_COMPONENT_CACHE:
        return _V7_COMPONENT_CACHE[cache_key]

    html = """
<div id="wrap">
  <canvas id="canvas"></canvas>
  <div id="status">Klikk i planutsnittet for å redigere bæresystemet.</div>
</div>
"""
    css = """
#wrap {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
#canvas {
  width: 100%;
  height: auto;
  display: block;
  border-radius: 12px;
  cursor: crosshair;
  background: #07111a;
  box-shadow: inset 0 0 0 1px rgba(120,145,170,0.22);
  touch-action: none;
}
#status {
  font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 12px;
  line-height: 1.4;
  color: rgba(207, 218, 227, 0.74);
  padding: 0 2px 4px 2px;
}
"""
    js = """
export default function(component) {
    const { data, setTriggerValue, parentElement } = component;
    const root = parentElement.querySelector('#wrap');
    const canvas = parentElement.querySelector('#canvas');
    const status = parentElement.querySelector('#status');
    if (!root || !canvas || !status) {
        return;
    }
    const ctx = canvas.getContext('2d');
    const state = parentElement.__builtlyRibV7 || (parentElement.__builtlyRibV7 = {});
    state.data = data || {};

    function clamp(value, minValue, maxValue) {
        return Math.max(minValue, Math.min(maxValue, value));
    }

    function drawCrosshair(point) {
        if (!point || typeof point.x !== 'number' || typeof point.y !== 'number') {
            return;
        }
        const x = clamp(point.x, 0, canvas.width);
        const y = clamp(point.y, 0, canvas.height);
        ctx.save();
        ctx.strokeStyle = 'rgba(255,255,255,0.96)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, 10, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(x - 14, y);
        ctx.lineTo(x + 14, y);
        ctx.moveTo(x, y - 14);
        ctx.lineTo(x, y + 14);
        ctx.stroke();
        ctx.restore();
    }

    function drawPlaceholder(message) {
        const w = Number(state.data.natural_width || 960);
        const h = Number(state.data.natural_height || 540);
        canvas.width = Math.max(32, w);
        canvas.height = Math.max(32, h);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#07111a';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#dbe7f0';
        ctx.font = '16px sans-serif';
        ctx.fillText(message || 'Laster editor...', 24, 34);
    }

    function renderImage() {
        status.textContent = state.data.status_text || 'Klikk i planutsnittet for å redigere bæresystemet.';
        if (!state.image) {
            drawPlaceholder('Laster editor...');
            return;
        }
        const w = Number(state.data.natural_width || state.image.naturalWidth || state.image.width || 960);
        const h = Number(state.data.natural_height || state.image.naturalHeight || state.image.height || 540);
        canvas.width = Math.max(32, w);
        canvas.height = Math.max(32, h);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);
        drawCrosshair(state.data.last_click || null);
    }

    function loadImage() {
        const imageData = state.data.image_data || '';
        if (!imageData) {
            state.image = null;
            drawPlaceholder('Fant ikke editorbildet.');
            return;
        }
        const img = new Image();
        img.onload = function() {
            state.image = img;
            renderImage();
        };
        img.onerror = function() {
            state.image = null;
            drawPlaceholder('Klarte ikke å laste editorbildet.');
        };
        img.src = imageData;
    }

    function readPoint(event) {
        const rect = canvas.getBoundingClientRect();
        const clientX = (event.clientX !== undefined) ? event.clientX : ((event.touches && event.touches.length) ? event.touches[0].clientX : 0);
        const clientY = (event.clientY !== undefined) ? event.clientY : ((event.touches && event.touches.length) ? event.touches[0].clientY : 0);
        const scaleX = canvas.width / Math.max(rect.width, 1);
        const scaleY = canvas.height / Math.max(rect.height, 1);
        return {
            x: clamp((clientX - rect.left) * scaleX, 0, canvas.width),
            y: clamp((clientY - rect.top) * scaleY, 0, canvas.height),
        };
    }

    function commit(event) {
        if (event && event.preventDefault) {
            event.preventDefault();
        }
        if (event && event.stopPropagation) {
            event.stopPropagation();
        }
        const point = readPoint(event || {});
        const payload = {
            x: Number(point.x.toFixed(2)),
            y: Number(point.y.toFixed(2)),
            event_id: String(Date.now()) + '-' + Math.random().toString(36).slice(2, 8),
        };
        state.data = Object.assign({}, state.data, { last_click: payload });
        renderImage();
        setTriggerValue('clicked', payload);
    }

    canvas.onpointerdown = commit;
    canvas.ontouchstart = commit;

    const signature = JSON.stringify({
        marker: state.data.version_marker || '',
        len: state.data.image_data ? state.data.image_data.length : 0,
        head: state.data.image_data ? state.data.image_data.slice(0, 80) : '',
        click: state.data.last_click || null,
        width: state.data.natural_width || 0,
        height: state.data.natural_height || 0,
    });

    if (state.signature !== signature) {
        state.signature = signature;
        loadImage();
    } else {
        renderImage();
    }
}
"""

    try:
        _V7_COMPONENT_CACHE[cache_key] = components_v2.component(
            cache_key,
            html=html,
            css=css,
            js=js,
        )
        st.session_state.pop('rib_click_canvas_error', None)
    except Exception as exc:
        st.session_state['rib_click_canvas_error'] = short_text(f"{type(exc).__name__}: {exc}", 240)
        _V7_COMPONENT_CACHE[cache_key] = None
    return _V7_COMPONENT_CACHE.get(cache_key)


def _projection_segments_v7(values: Any, min_size: int, threshold: float) -> List[Tuple[int, int, float]]:
    segments: List[Tuple[int, int, float]] = []
    start = None
    for idx, flag in enumerate(values >= threshold):
        if flag and start is None:
            start = idx
        elif (not flag) and start is not None:
            if (idx - start) >= min_size:
                segments.append((start, idx - 1, float(values[start:idx].mean())))
            start = None
    if start is not None and (len(values) - start) >= min_size:
        segments.append((start, len(values) - 1, float(values[start:].mean())))
    return segments


def split_large_plan_region_v7(image: Image.Image, bbox: Tuple[int, int, int, int]) -> List[Dict[str, Any]]:
    cv2, np = optional_cv_stack()
    if cv2 is None or np is None:
        return []
    image_w, image_h = image.size
    x, y, w, h = bbox
    if w <= 180 or h <= 140:
        return []

    arr = np.array(copy_rgb(image))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    crop = gray[y:y + h, x:x + w]
    if crop.size == 0:
        return []

    bw = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 5)
    opened = cv2.morphologyEx(
        bw,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
    )
    dilate_k = max(7, int(min(w, h) * 0.010))
    close_k = max(17, int(min(w, h) * 0.028))
    blob = cv2.dilate(opened, cv2.getStructuringElement(cv2.MORPH_RECT, (dilate_k, dilate_k)), iterations=1)
    blob = cv2.morphologyEx(blob, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (close_k, close_k)))

    x_proj = (blob > 0).mean(axis=0)
    smooth_k = max(15, int(w * 0.015))
    x_smooth = np.convolve(x_proj, np.ones(smooth_k) / smooth_k, mode='same')
    x_threshold = max(float(np.percentile(x_smooth, 20)) * 0.60, 0.005)
    x_segments = _projection_segments_v7(x_smooth, max(40, int(w * 0.18)), x_threshold)
    if len(x_segments) < 2 or len(x_segments) > 4:
        return []

    children: List[Dict[str, Any]] = []
    for x0, x1, seg_score in x_segments:
        child_w = int(x1 - x0 + 1)
        if child_w < int(w * 0.18):
            continue
        segment_blob = blob[:, x0:x1 + 1]
        y_proj = (segment_blob > 0).mean(axis=1)
        y_smooth = np.convolve(y_proj, np.ones(max(11, int(h * 0.015))) / max(11, int(h * 0.015)), mode='same')
        y_threshold = max(float(np.percentile(y_smooth, 20)) * 0.65, 0.005)
        y_segments = _projection_segments_v7(y_smooth, max(30, int(h * 0.20)), y_threshold)
        if not y_segments:
            continue
        y0 = min(seg[0] for seg in y_segments)
        y1 = max(seg[1] for seg in y_segments)
        child_h = int(y1 - y0 + 1)
        if child_h < int(h * 0.32):
            continue
        margin_x = max(8, int(w * 0.010))
        margin_y = max(8, int(h * 0.012))
        bx0 = int(clamp(x + x0 - margin_x, x, x + w - 12))
        by0 = int(clamp(y + y0 - margin_y, y, y + h - 12))
        bx1 = int(clamp(x + x1 + margin_x, bx0 + 12, x + w))
        by1 = int(clamp(y + y1 + margin_y, by0 + 12, y + h))
        bbox_px = (bx0, by0, bx1 - bx0, by1 - by0)
        density = float(segment_blob[max(0, y0):min(segment_blob.shape[0], y1 + 1), :].mean()) / 255.0
        cx, cy = bbox_center(bbox_px)
        centrality = max(0.35, 1.0 - abs((cx / max(image_w, 1)) - 0.5) * 0.55 - abs((cy / max(image_h, 1)) - 0.48) * 0.40)
        score = float((bbox_px[2] * bbox_px[3]) * max(0.03, density) * centrality * max(0.4, seg_score * 4.0))
        children.append({
            'bbox_px': bbox_px,
            'bbox_norm': px_bbox_to_norm(bbox_px, image_w, image_h),
            'score': score,
            'density': density,
            'split_child': True,
        })

    if len(children) < 2:
        return []
    children = dedupe_region_candidates(children)
    children.sort(key=lambda item: item.get('score', 0.0), reverse=True)
    return children[:4]


def regions_form_horizontal_strip_v7(regions: List[Dict[str, Any]]) -> bool:
    if len(regions) < 3:
        return False
    ordered = sorted(regions, key=lambda item: bbox_center(item['bbox_px'])[0])
    ys = [bbox_center(item['bbox_px'])[1] for item in ordered[:3]]
    widths = [item['bbox_px'][2] for item in ordered[:3]]
    heights = [item['bbox_px'][3] for item in ordered[:3]]
    if max(ys) - min(ys) > max(24, min(heights) * 0.20):
        return False
    if min(widths) <= 0:
        return False
    if (max(widths) / max(min(widths), 1)) > 2.4:
        return False
    return True


def get_plan_regions_for_record_v6(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    cache_key = '_plan_regions_v7'
    cached = record.get(cache_key)
    if isinstance(cached, list) and cached:
        return cached

    base_regions = deep_copy_jsonable(_GET_PLAN_REGIONS_FOR_RECORD_V6_BASE(record))
    expanded: List[Dict[str, Any]] = []
    for region in base_regions:
        bbox = tuple(region.get('bbox_px', (0, 0, 0, 0)))
        area_ratio = bbox_area(bbox) / float(max(record['image'].size[0] * record['image'].size[1], 1))
        should_try_split = area_ratio >= 0.22 or (bbox[2] >= record['image'].size[0] * 0.68 and bbox[3] >= record['image'].size[1] * 0.30)
        children = split_large_plan_region_v7(record['image'], bbox) if should_try_split else []
        if len(children) >= 2:
            expanded.extend(children)
        else:
            expanded.append(region)

    if not expanded:
        expanded = base_regions

    expanded = dedupe_region_candidates(expanded)

    if regions_form_horizontal_strip_v7(expanded) and not is_basement_like_record_v6(record):
        ordered_x = sorted(expanded, key=lambda item: bbox_center(item['bbox_px'])[0])
        middle_region = ordered_x[len(ordered_x) // 2]
        for region in expanded:
            if region is middle_region:
                region['score'] = float(region.get('score', 0.0)) * 1.35
            else:
                region['score'] = float(region.get('score', 0.0)) * 0.92

    expanded.sort(key=lambda item: item.get('score', 0.0), reverse=True)
    record[cache_key] = expanded[:3]
    return record[cache_key]


def _choose_region_for_sketch_v6(record: Dict[str, Any], sketch: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], int]:
    regions = get_plan_regions_for_record_v6(record)
    if not regions:
        return _CHOOSE_REGION_FOR_SKETCH_V6_BASE(record, sketch)

    if regions_form_horizontal_strip_v7(regions) and not is_basement_like_record_v6(record):
        text = _record_text_v6(record, sketch).lower()
        prefer_middle = any(token in text for token in ['typisk', 'typical', '2-4', '2 4'])
        ai_bbox = None
        if isinstance(sketch, dict) and isinstance(sketch.get('plan_bbox'), dict):
            ai_bbox = norm_bbox_to_px(sketch.get('plan_bbox'), record['image'].size[0], record['image'].size[1])
        if ai_bbox is not None:
            prefer_middle = prefer_middle or (bbox_area(ai_bbox) > (record['image'].size[0] * record['image'].size[1] * 0.22))
        if prefer_middle or not sketch or not sketch.get('elements'):
            ordered = sorted(regions, key=lambda item: bbox_center(item['bbox_px'])[0])
            chosen = ordered[len(ordered) // 2]
            return chosen, regions.index(chosen)

    return _CHOOSE_REGION_FOR_SKETCH_V6_BASE(record, sketch)


def _wall_metrics_local_v11(element: Dict[str, Any], record: Dict[str, Any], region_bbox_px: Tuple[int, int, int, int]) -> Dict[str, float]:
    x1, y1 = page_norm_to_local_crop(float(element.get('x1', 0.0)), float(element.get('y1', 0.0)), record['image'].size, region_bbox_px)
    x2, y2 = page_norm_to_local_crop(float(element.get('x2', 0.0)), float(element.get('y2', 0.0)), record['image'].size, region_bbox_px)
    return {
        'x1': float(x1),
        'y1': float(y1),
        'x2': float(x2),
        'y2': float(y2),
        'x_mid': float((x1 + x2) / 2.0),
        'y_mid': float((y1 + y2) / 2.0),
        'length': float(math.hypot(x2 - x1, y2 - y1)),
        'vertical': bool(abs(x2 - x1) <= abs(y2 - y1)),
    }


def _select_spaced_items_v7(items: List[Dict[str, Any]], value_key: str, limit: int, min_spacing: float) -> List[Dict[str, Any]]:
    chosen: List[Dict[str, Any]] = []
    for item in sorted(items, key=lambda entry: float(entry.get('score', 0.0)), reverse=True):
        value = float(item.get(value_key, 0.0))
        if all(abs(value - float(prev.get(value_key, 0.0))) >= min_spacing for prev in chosen):
            chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def filter_upper_floor_elements_v7(sketch: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    sketch = deep_copy_jsonable(sketch)
    geometry = get_geometry_for_sketch(record, sketch)
    if not geometry:
        return sketch

    region_bbox_px = geometry['bbox_px']
    rw, rh = geometry.get('crop_size', (0, 0))
    if rw <= 0 or rh <= 0:
        return sketch

    core_centers_local: List[Tuple[float, float]] = []
    for element in sketch.get('elements', []):
        if clean_pdf_text(element.get('type', '')).lower() != 'core':
            continue
        cx_norm = float(element.get('x', 0.0)) + float(element.get('w', 0.0)) / 2.0
        cy_norm = float(element.get('y', 0.0)) + float(element.get('h', 0.0)) / 2.0
        cx_local, cy_local = page_norm_to_local_crop(cx_norm, cy_norm, record['image'].size, region_bbox_px)
        core_centers_local.append((float(cx_local), float(cy_local)))
    if not core_centers_local:
        core_box = geometry.get('core_bbox', (rw * 0.40, rh * 0.35, rw * 0.18, rh * 0.22))
        core_centers_local = [(float(core_box[0] + core_box[2] / 2.0), float(core_box[1] + core_box[3] / 2.0))]

    cores = [element for element in sketch.get('elements', []) if clean_pdf_text(element.get('type', '')).lower() == 'core']
    spans = [element for element in sketch.get('elements', []) if clean_pdf_text(element.get('type', '')).lower() == 'span_arrow']
    beams = [element for element in sketch.get('elements', []) if clean_pdf_text(element.get('type', '')).lower() == 'beam']

    vertical_candidates: List[Dict[str, Any]] = []
    horizontal_candidates: List[Dict[str, Any]] = []
    for element in sketch.get('elements', []):
        if clean_pdf_text(element.get('type', '')).lower() != 'wall':
            continue
        metrics = _wall_metrics_local_v11(element, record, region_bbox_px)
        edge_frac_x = min(metrics['x_mid'] / max(rw, 1), 1.0 - (metrics['x_mid'] / max(rw, 1)))
        edge_frac_y = min(metrics['y_mid'] / max(rh, 1), 1.0 - (metrics['y_mid'] / max(rh, 1)))
        if metrics['vertical']:
            if metrics['length'] < max(46, rh * 0.16):
                continue
            if edge_frac_x < 0.12:
                continue
            if metrics['length'] > rh * 0.78 and edge_frac_x < 0.18:
                continue
            core_dist = min(abs(metrics['x_mid'] - cx) for cx, _ in core_centers_local)
            center_penalty = abs(metrics['x_mid'] - (rw / 2.0)) * 0.10
            score = metrics['length'] - core_dist * 0.45 - center_penalty
            if 0.24 <= (metrics['x_mid'] / max(rw, 1)) <= 0.76:
                score += 16.0
            vertical_candidates.append({'element': element, 'score': score, 'x_mid': metrics['x_mid']})
        else:
            if metrics['length'] < max(34, rw * 0.12):
                continue
            if edge_frac_y < 0.18:
                continue
            if metrics['length'] > rw * 0.58:
                continue
            score = metrics['length'] * 0.55 - abs(metrics['y_mid'] - (rh * 0.50)) * 0.22
            horizontal_candidates.append({'element': element, 'score': score, 'y_mid': metrics['y_mid']})

    chosen_vertical = _select_spaced_items_v7(vertical_candidates, 'x_mid', limit=2, min_spacing=max(28.0, rw * 0.16))
    chosen_horizontal = _select_spaced_items_v7(horizontal_candidates, 'y_mid', limit=1, min_spacing=max(26.0, rh * 0.12))
    kept_walls = [item['element'] for item in chosen_vertical + chosen_horizontal]

    cleaned_walls: List[Dict[str, Any]] = []
    for element in kept_walls:
        metrics = _wall_metrics_local_v11(element, record, region_bbox_px)
        edge_frac_x = min(metrics['x_mid'] / max(rw, 1), 1.0 - (metrics['x_mid'] / max(rw, 1)))
        edge_frac_y = min(metrics['y_mid'] / max(rh, 1), 1.0 - (metrics['y_mid'] / max(rh, 1)))
        if metrics['vertical'] and metrics['length'] > rh * 0.70 and edge_frac_x < 0.16:
            continue
        if (not metrics['vertical']) and metrics['length'] > rw * 0.54 and edge_frac_y < 0.20:
            continue
        cleaned_walls.append(element)
    kept_walls = cleaned_walls[:3]

    if not kept_walls:
        fallback_elements, _ = generate_wall_core_elements_grounded(geometry, record['image'].size)
        fallback_vertical: List[Dict[str, Any]] = []
        for element in fallback_elements:
            if clean_pdf_text(element.get('type', '')).lower() != 'wall':
                continue
            metrics = _wall_metrics_local_v11(element, record, region_bbox_px)
            edge_frac_x = min(metrics['x_mid'] / max(rw, 1), 1.0 - (metrics['x_mid'] / max(rw, 1)))
            if metrics['vertical'] and edge_frac_x >= 0.14 and metrics['length'] >= max(46, rh * 0.16):
                core_dist = min(abs(metrics['x_mid'] - cx) for cx, _ in core_centers_local)
                fallback_vertical.append({'element': element, 'score': metrics['length'] - core_dist * 0.40, 'x_mid': metrics['x_mid']})
        kept_walls = [item['element'] for item in _select_spaced_items_v7(fallback_vertical, 'x_mid', limit=2, min_spacing=max(28.0, rw * 0.16))]

    cleaned_notes = [clean_pdf_text(item) for item in sketch.get('notes', []) if clean_pdf_text(item)]
    note = 'Overetasjer er etterfiltrert hardere mot indre vegglinjer og kjerner for å unngå at ytterkontur eller hele planomriss blir tolket som bærevegger.'
    if note not in cleaned_notes:
        cleaned_notes = [note] + cleaned_notes

    sketch['elements'] = cores[:2] + kept_walls + spans[:1] + beams[:1]
    sketch['notes'] = cleaned_notes[:5]
    return sketch


def refine_sketch_with_geometry_v6(
    record: Dict[str, Any],
    sketch: Optional[Dict[str, Any]],
    analysis_result: Dict[str, Any],
    material_preference: str,
    transfer_hints: Optional[Dict[str, Any]] = None,
    forced_region: Optional[Dict[str, Any]] = None,
    forced_region_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    refined = _REFINE_SKETCH_WITH_GEOMETRY_V6_BASE(
        record,
        sketch,
        analysis_result,
        material_preference,
        transfer_hints,
        forced_region,
        forced_region_index,
    )
    if refined is None:
        return None
    mode = clean_pdf_text(refined.get('grounded_mode', '')).lower()
    if mode == 'wall_core' and not is_basement_like_record_v6(record, refined):
        refined = filter_upper_floor_elements_v7(refined, record)
    return refined


def render_inline_click_canvas_editor(
    drawing_record: Dict[str, Any],
    sketch: Dict[str, Any],
    editor_key: str,
) -> Optional[Dict[str, float]]:
    st.caption('Klikk-editor kjører i stabil canvas-modus. Ett klikk skal gi ett punkt og oppdatere skissen direkte.')
    click = _RENDER_INLINE_CLICK_CANVAS_EDITOR_V6(drawing_record, sketch, editor_key)
    marker_key = f'{editor_key}_canvas_last_click'
    if isinstance(click, dict) and 'x' in click and 'y' in click:
        st.session_state[marker_key] = {'x': float(click['x']), 'y': float(click['y'])}
        st.caption(f"Siste registrerte klikk: x={round(float(click['x']), 1)}, y={round(float(click['y']), 1)}")
        return click
    last_click = st.session_state.get(marker_key)
    if isinstance(last_click, dict) and 'x' in last_click and 'y' in last_click:
        st.caption(f"Sist lagrede klikk: x={round(float(last_click['x']), 1)}, y={round(float(last_click['y']), 1)}")
    return None


def render_plotly_sketch_editor(drawing_record: Dict[str, Any], sketch: Dict[str, Any], editor_key: str) -> Optional[Dict[str, float]]:
    return render_inline_click_canvas_editor(drawing_record, sketch, editor_key)




# ------------------------------------------------------------
# 13D. V9 STABIL MUSEPEKER-EDITOR + SLUTTOPPSTRAMMING
# ------------------------------------------------------------
_RENDER_INLINE_CLICK_CANVAS_EDITOR_V8_ORIG = render_inline_click_canvas_editor


def _optional_streamlit_image_coordinates_v9():
    try:
        from streamlit_image_coordinates import streamlit_image_coordinates as sic
        return sic
    except Exception:
        return None


def _render_with_streamlit_image_coordinates_v9(
    drawing_record: Dict[str, Any],
    sketch: Dict[str, Any],
    editor_key: str,
) -> Optional[Dict[str, float]]:
    sic = _optional_streamlit_image_coordinates_v9()
    if sic is None:
        return None

    show_guides = bool(st.session_state.get("rib_editor_show_guides", True))
    editor_img, _ = build_editor_crop_overlay_image(drawing_record, sketch, show_guides=show_guides)
    marker_key = f"{editor_key}_sic_last_click"
    value = None
    last_exc = None

    for kwargs in [
        {"key": f"{editor_key}_sic", "use_column_width": True},
        {"key": f"{editor_key}_sic", "width": int(editor_img.size[0])},
        {"key": f"{editor_key}_sic"},
    ]:
        try:
            value = sic(editor_img, **kwargs)
            last_exc = None
            break
        except TypeError as exc:
            last_exc = exc
            continue
        except Exception as exc:
            last_exc = exc
            break

    st.caption("Klikk-editor kjører i bildekoordinat-modus. Ett museklikk skal registrere et punkt direkte i planutsnittet.")

    if isinstance(value, dict) and "x" in value and "y" in value:
        event_id = clean_pdf_text(value.get("event_id") or value.get("time") or value.get("timestamp") or "")
        if not event_id:
            event_id = f"{editor_key}|{datetime.utcnow().timestamp()}"
        click = {
            "x": float(value.get("x", 0.0)),
            "y": float(value.get("y", 0.0)),
            "event_id": event_id,
        }
        st.session_state[marker_key] = click
        st.caption(f"Siste registrerte klikk: x={round(click['x'], 1)}, y={round(click['y'], 1)}")
        st.session_state.pop("rib_click_canvas_error", None)
        return click

    if last_exc is not None:
        st.session_state["rib_click_canvas_error"] = short_text(f"{type(last_exc).__name__}: {last_exc}", 220)

    last_click = st.session_state.get(marker_key)
    if isinstance(last_click, dict) and "x" in last_click and "y" in last_click:
        st.caption(f"Sist lagrede klikk: x={round(float(last_click['x']), 1)}, y={round(float(last_click['y']), 1)}")
    return None


def render_inline_click_canvas_editor(
    drawing_record: Dict[str, Any],
    sketch: Dict[str, Any],
    editor_key: str,
) -> Optional[Dict[str, float]]:
    click = _render_with_streamlit_image_coordinates_v9(drawing_record, sketch, editor_key)
    if click is not None:
        return click
    return _RENDER_INLINE_CLICK_CANVAS_EDITOR_V8_ORIG(drawing_record, sketch, editor_key)


def render_plotly_sketch_editor(drawing_record: Dict[str, Any], sketch: Dict[str, Any], editor_key: str) -> Optional[Dict[str, float]]:
    return render_inline_click_canvas_editor(drawing_record, sketch, editor_key)


refine_sketch_with_geometry_v6 = _refine_sketch_with_geometry_v9_impl
filter_upper_floor_elements_v7 = _filter_upper_floor_elements_v9_impl





# ------------------------------------------------------------
# 13E. V12 DELPLAN-SPLITTING + BEDRE KJERNE + ENKLERE MUSEFLYTT
# ------------------------------------------------------------
_REFINE_SKETCH_WITH_GEOMETRY_V11_BASE = refine_sketch_with_geometry_v6
_FIND_NEAREST_POINTLIKE_ELEMENT_INDEX_V11_BASE = find_nearest_pointlike_element_index
_APPLY_POINTER_CLICK_TO_SKETCH_V11_BASE = apply_pointer_click_to_sketch
_BUILD_EDITOR_CROP_OVERLAY_IMAGE_V11_BASE = build_editor_crop_overlay_image
_PAGE_CUE_PROMPT_V11_BASE = _page_cue_prompt_v9


def _page_cue_prompt_v9(record: Dict[str, Any]) -> str:
    base = _PAGE_CUE_PROMPT_V11_BASE(record)
    extra = """

V12-tillegg for bedre kjernedeteksjon:
- core_candidates skal primart representere trapperom, heis-/sjaktkjerner eller tydelige avstivningskjerner i indre sone.
- Ikke bruk balkonger, nisjer, titleblock, terrasser, situasjonsinnstikk eller andre randobjekter som kjerne.
- Hvis siden viser flere distinkte planpaneler av samme bygg, sett multi_plan=true og fordel core_candidates på de panelene som faktisk har trapperom/heiskjerne.
- Vær mer konservativ med kjerne enn med vegg: returner heller ingen kjerne enn en feilplassert kjerne.
""".strip()
    return f"{base}\n\n{extra}"


def _element_center_px_v12(element: Dict[str, Any], image_size: Tuple[int, int]) -> Tuple[float, float]:
    image_w, image_h = image_size
    e_type = clean_pdf_text(element.get("type", "")).lower()
    if e_type == "column":
        return float(element.get("x", 0.0)) * image_w, float(element.get("y", 0.0)) * image_h
    if e_type == "core":
        return (
            (float(element.get("x", 0.0)) + float(element.get("w", 0.0)) / 2.0) * image_w,
            (float(element.get("y", 0.0)) + float(element.get("h", 0.0)) / 2.0) * image_h,
        )
    if e_type in {"wall", "beam", "span_arrow"}:
        return (
            ((float(element.get("x1", 0.0)) + float(element.get("x2", 0.0))) / 2.0) * image_w,
            ((float(element.get("y1", 0.0)) + float(element.get("y2", 0.0))) / 2.0) * image_h,
        )
    return 0.0, 0.0


def _core_bbox_px_v12(element: Dict[str, Any], image_size: Tuple[int, int]) -> Tuple[float, float, float, float]:
    image_w, image_h = image_size
    x = float(element.get("x", 0.0)) * image_w
    y = float(element.get("y", 0.0)) * image_h
    w = float(element.get("w", 0.0)) * image_w
    h = float(element.get("h", 0.0)) * image_h
    return x, y, w, h


def _point_to_bbox_distance_v12(px: float, py: float, bbox: Tuple[float, float, float, float]) -> float:
    x, y, w, h = bbox
    dx = max(x - px, 0.0, px - (x + w))
    dy = max(y - py, 0.0, py - (y + h))
    if dx <= 0.0 and dy <= 0.0:
        return 0.0
    return float(math.hypot(dx, dy))


def find_nearest_pointlike_element_index(
    elements: List[Dict[str, Any]],
    image_size: Tuple[int, int],
    click_x_norm: float,
    click_y_norm: float,
    element_types: List[str],
) -> Tuple[Optional[int], Optional[float]]:
    image_w, image_h = image_size
    cx, cy = page_norm_to_px(click_x_norm, click_y_norm, image_w, image_h)
    best_idx: Optional[int] = None
    best_dist: Optional[float] = None
    wanted = {clean_pdf_text(item).lower() for item in element_types}
    for idx, element in enumerate(elements):
        e_type = clean_pdf_text(element.get("type", "")).lower()
        if e_type not in wanted:
            continue
        if e_type == "core":
            dist = _point_to_bbox_distance_v12(float(cx), float(cy), _core_bbox_px_v12(element, image_size))
        elif e_type == "column":
            ex, ey = _element_center_px_v12(element, image_size)
            dist = float(math.hypot(ex - cx, ey - cy))
        else:
            ex, ey = _element_center_px_v12(element, image_size)
            dist = float(math.hypot(ex - cx, ey - cy))
        if best_dist is None or dist < best_dist:
            best_idx = idx
            best_dist = dist
    return best_idx, best_dist


def _region_union_bbox_v12(regions: List[Dict[str, Any]]) -> Optional[Tuple[int, int, int, int]]:
    boxes = [tuple(item.get("bbox_px", (0, 0, 0, 0))) for item in regions if isinstance(item, dict)]
    boxes = [box for box in boxes if bbox_area(box) > 0]
    if not boxes:
        return None
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[0] + box[2] for box in boxes)
    y1 = max(box[1] + box[3] for box in boxes)
    return (int(x0), int(y0), int(max(0, x1 - x0)), int(max(0, y1 - y0)))


def _is_horizontal_multi_panel_v12(regions: List[Dict[str, Any]], image_size: Tuple[int, int]) -> bool:
    if len(regions) < 2 or len(regions) > 4:
        return False
    ordered = sorted(regions, key=lambda item: bbox_center(tuple(item.get("bbox_px", (0, 0, 0, 0))))[0])
    centers_y = [bbox_center(tuple(item.get("bbox_px", (0, 0, 0, 0))))[1] for item in ordered]
    widths = [tuple(item.get("bbox_px", (0, 0, 0, 0)))[2] for item in ordered]
    heights = [tuple(item.get("bbox_px", (0, 0, 0, 0)))[3] for item in ordered]
    if not widths or min(widths) <= 0 or min(heights) <= 0:
        return False
    if (max(centers_y) - min(centers_y)) > max(32.0, min(heights) * 0.22):
        return False
    if (max(widths) / max(min(widths), 1)) > 2.8:
        return False
    page_w, page_h = image_size
    total_area = sum(bbox_area(tuple(item.get("bbox_px", (0, 0, 0, 0)))) for item in ordered)
    if total_area < (page_w * page_h * 0.08):
        return False
    return True


def _element_belongs_to_region_v12(element: Dict[str, Any], region_bbox_px: Tuple[int, int, int, int], image_size: Tuple[int, int]) -> bool:
    rx, ry, rw, rh = region_bbox_px
    margin_x = max(18.0, rw * 0.06)
    margin_y = max(18.0, rh * 0.06)
    e_type = clean_pdf_text(element.get("type", "")).lower()
    cx, cy = _element_center_px_v12(element, image_size)
    inside_center = (rx - margin_x) <= cx <= (rx + rw + margin_x) and (ry - margin_y) <= cy <= (ry + rh + margin_y)
    if e_type != "core":
        return inside_center
    bx, by, bw, bh = _core_bbox_px_v12(element, image_size)
    core_box = (int(bx), int(by), int(max(1, bw)), int(max(1, bh)))
    return inside_center or bbox_iou(core_box, region_bbox_px) >= 0.10 or bbox_contains(region_bbox_px, core_box, min_cover=0.45)


def _build_region_seed_sketch_v12(
    record: Dict[str, Any],
    sketch: Optional[Dict[str, Any]],
    region: Dict[str, Any],
    region_index: int,
    analysis_result: Dict[str, Any],
) -> Dict[str, Any]:
    image_size = record["image"].size
    region_bbox_px = tuple(region.get("bbox_px", (0, 0, image_size[0], image_size[1])))
    source_elements = []
    if isinstance(sketch, dict) and isinstance(sketch.get("elements"), list):
        source_elements.extend([deep_copy_jsonable(item) for item in sketch.get("elements", []) if isinstance(item, dict)])

    cue = _get_page_cue_for_record_v9(analysis_result, record)
    cue_seed = _build_seed_sketch_from_page_cue_v9(cue, record)
    if isinstance(cue_seed, dict) and isinstance(cue_seed.get("elements"), list):
        source_elements.extend([deep_copy_jsonable(item) for item in cue_seed.get("elements", []) if isinstance(item, dict)])

    picked: List[Dict[str, Any]] = []
    for element in source_elements:
        if _element_belongs_to_region_v12(element, region_bbox_px, image_size):
            picked.append(element)

    notes: List[str] = []
    for source in [((sketch or {}).get("notes", []) if isinstance(sketch, dict) else []), ((cue_seed or {}).get("notes", []) if isinstance(cue_seed, dict) else [])]:
        for item in source[:4]:
            cleaned = clean_pdf_text(item)
            if cleaned and cleaned not in notes:
                notes.append(cleaned)

    return {
        "page_index": int(record.get("page_index", 0)),
        "region_index": int(region_index),
        "page_label": clean_pdf_text((sketch or {}).get("page_label", "")) or clean_pdf_text(record.get("label", "Tegning")),
        "plan_bbox": region.get("bbox_norm") or px_bbox_to_norm(region_bbox_px, image_size[0], image_size[1]),
        "notes": notes[:4],
        "elements": picked,
    }


def _replicate_missing_core_to_region_v12(
    record: Dict[str, Any],
    template_core: Dict[str, Any],
    template_region_bbox_px: Tuple[int, int, int, int],
    target_region_bbox_px: Tuple[int, int, int, int],
) -> Optional[Dict[str, Any]]:
    template_w = max(float(template_region_bbox_px[2]), 1.0)
    template_h = max(float(template_region_bbox_px[3]), 1.0)
    target_w = max(float(target_region_bbox_px[2]), 1.0)
    target_h = max(float(target_region_bbox_px[3]), 1.0)

    tx, ty, tw, th = _core_bbox_px_v12(template_core, record["image"].size)
    rel_x = (tx - float(template_region_bbox_px[0])) / template_w
    rel_y = (ty - float(template_region_bbox_px[1])) / template_h
    rel_w = tw / template_w
    rel_h = th / template_h

    if not (0.0 <= rel_x <= 0.95 and 0.0 <= rel_y <= 0.95):
        return None
    rel_w = float(clamp(rel_w, 0.06, 0.35))
    rel_h = float(clamp(rel_h, 0.08, 0.38))

    local_x0 = float(target_region_bbox_px[2]) * float(clamp(rel_x, 0.02, 0.86))
    local_y0 = float(target_region_bbox_px[3]) * float(clamp(rel_y, 0.02, 0.82))
    local_x1 = local_x0 + float(target_region_bbox_px[2]) * rel_w
    local_y1 = local_y0 + float(target_region_bbox_px[3]) * rel_h
    geometry = build_plan_geometry_grounded(record["image"], target_region_bbox_px)
    if not geometry:
        return None
    snapped = _snap_bbox_to_geometry_v6(local_x0, local_y0, local_x1, local_y1, geometry)
    label = clean_pdf_text(template_core.get("label", "")) or "Kjerne"
    return _core_element_from_local_bbox_v6(target_region_bbox_px, snapped, record["image"].size, label)


def _merge_subregion_results_v12(
    record: Dict[str, Any],
    region_results: List[Dict[str, Any]],
    regions: List[Dict[str, Any]],
    sketch: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    valid_results = [item for item in region_results if isinstance(item, dict) and item.get("elements")]
    if not valid_results:
        return None

    if len(valid_results) >= 2:
        template_idx = None
        template_core = None
        for idx, result in enumerate(valid_results):
            cores = [el for el in result.get("elements", []) if clean_pdf_text(el.get("type", "")).lower() == "core"]
            if cores:
                template_idx = idx
                template_core = cores[0]
                break
        if template_core is not None and template_idx is not None:
            template_region = regions[template_idx]
            for idx, result in enumerate(valid_results):
                has_core = any(clean_pdf_text(el.get("type", "")).lower() == "core" for el in result.get("elements", []))
                if has_core:
                    continue
                src_box = tuple(template_region.get("bbox_px", (0, 0, 0, 0)))
                dst_box = tuple(regions[idx].get("bbox_px", (0, 0, 0, 0)))
                if min(src_box[2], dst_box[2]) <= 0 or min(src_box[3], dst_box[3]) <= 0:
                    continue
                width_ratio = max(src_box[2], dst_box[2]) / max(min(src_box[2], dst_box[2]), 1)
                height_ratio = max(src_box[3], dst_box[3]) / max(min(src_box[3], dst_box[3]), 1)
                if width_ratio > 1.9 or height_ratio > 1.9:
                    continue
                replicated = _replicate_missing_core_to_region_v12(record, template_core, src_box, dst_box)
                if replicated is not None:
                    result.setdefault("elements", []).append(replicated)
                    note = "Kjerne er v12-replikert fra søskenplan med lignende geometri for bedre konsistens mellom delplaner."
                    result.setdefault("notes", [])
                    if note not in result["notes"]:
                        result["notes"].append(note)

    merged_elements: List[Dict[str, Any]] = []
    notes: List[str] = [
        "Skissen er v12-splittet per delplan før sammenslåing, slik at kjerne og bærelinjer vurderes på hvert planpanel separat.",
    ]
    for result in valid_results:
        for element in result.get("elements", []):
            if isinstance(element, dict):
                merged_elements.append(element)
        for item in result.get("notes", [])[:3]:
            cleaned = clean_pdf_text(item)
            if cleaned and cleaned not in notes:
                notes.append(cleaned)

    merged_elements = _dedupe_refined_elements_v6(merged_elements, record["image"].size)
    if not merged_elements:
        return None

    union_bbox = _region_union_bbox_v12(regions[:len(valid_results)])
    page_label = clean_pdf_text((sketch or {}).get("page_label", "")) or clean_pdf_text(record.get("label", "Tegning"))
    result = {
        "page_index": int(record.get("page_index", 0)),
        "region_index": 0,
        "page_label": page_label,
        "plan_bbox": px_bbox_to_norm(union_bbox, record["image"].size[0], record["image"].size[1]) if union_bbox else (sketch or {}).get("plan_bbox"),
        "notes": notes[:6],
        "elements": merged_elements,
        "grounded_engine": True,
        "grounded_mode": "wall_core",
        "multi_region_merge_v12": True,
    }
    return result


def _refine_sketch_with_geometry_v12_impl(
    record: Dict[str, Any],
    sketch: Optional[Dict[str, Any]],
    analysis_result: Dict[str, Any],
    material_preference: str,
    transfer_hints: Optional[Dict[str, Any]] = None,
    forced_region: Optional[Dict[str, Any]] = None,
    forced_region_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if forced_region is not None:
        return _REFINE_SKETCH_WITH_GEOMETRY_V11_BASE(
            record,
            sketch,
            analysis_result,
            material_preference,
            transfer_hints,
            forced_region,
            forced_region_index,
        )

    mode_probe = infer_sketch_mode_v6(record, sketch, analysis_result, material_preference)
    if mode_probe != "wall_core" or is_basement_like_record_v6(record, sketch):
        return _REFINE_SKETCH_WITH_GEOMETRY_V11_BASE(record, sketch, analysis_result, material_preference, transfer_hints, forced_region, forced_region_index)

    regions = get_plan_regions_for_record_v6(record)
    if not _is_horizontal_multi_panel_v12(regions, record["image"].size):
        return _REFINE_SKETCH_WITH_GEOMETRY_V11_BASE(record, sketch, analysis_result, material_preference, transfer_hints, forced_region, forced_region_index)

    ordered_regions = sorted(regions, key=lambda item: bbox_center(tuple(item.get("bbox_px", (0, 0, 0, 0))))[0])[:4]
    sub_results: List[Dict[str, Any]] = []
    for idx, region in enumerate(ordered_regions):
        seed_sketch = _build_region_seed_sketch_v12(record, sketch, region, idx, analysis_result)
        sub = _REFINE_SKETCH_WITH_GEOMETRY_V8_BASE(
            record,
            seed_sketch,
            analysis_result,
            material_preference,
            transfer_hints,
            forced_region=region,
            forced_region_index=idx,
        )
        if sub is None:
            continue
        sub = _filter_upper_floor_elements_v9_impl(sub, record, analysis_result)
        sub_results.append(sub)

    merged = _merge_subregion_results_v12(record, sub_results, ordered_regions, sketch)
    if merged is not None:
        return merged
    return _REFINE_SKETCH_WITH_GEOMETRY_V11_BASE(record, sketch, analysis_result, material_preference, transfer_hints, forced_region, forced_region_index)


def build_editor_crop_overlay_image(
    drawing_record: Dict[str, Any],
    sketch: Dict[str, Any],
    show_guides: bool = True,
) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    base_img, region_bbox = _BUILD_EDITOR_CROP_OVERLAY_IMAGE_V11_BASE(drawing_record, sketch, show_guides=show_guides)
    sketch_key = sketch_uid(sketch)
    state = get_pointer_state(sketch_key)
    target_idx = state.get("target_idx") if isinstance(state, dict) else None
    if target_idx is None:
        return base_img, region_bbox
    try:
        idx = int(target_idx)
    except Exception:
        return base_img, region_bbox
    elements = sketch.get("elements", []) if isinstance(sketch.get("elements"), list) else []
    if not (0 <= idx < len(elements)):
        return base_img, region_bbox
    element = elements[idx]
    e_type = clean_pdf_text(element.get("type", "")).lower()
    overlay = copy_rgb(base_img).convert("RGBA")
    draw = ImageDraw.Draw(overlay, "RGBA")
    rx, ry, rw, rh = region_bbox
    min_dim = max(1, min(rw, rh))
    accent = (56, 194, 201, 255)
    halo = (56, 194, 201, 90)
    if e_type == "core":
        x_local, y_local = page_norm_to_local_crop(float(element.get("x", 0.0)), float(element.get("y", 0.0)), drawing_record["image"].size, region_bbox)
        ew = float(element.get("w", 0.0)) * drawing_record["image"].size[0]
        eh = float(element.get("h", 0.0)) * drawing_record["image"].size[1]
        left = clamp(x_local, 0, max(rw - 2, 1))
        top = clamp(y_local, 0, max(rh - 2, 1))
        right = clamp(x_local + ew, left + 2, max(rw - 1, left + 2))
        bottom = clamp(y_local + eh, top + 2, max(rh - 1, top + 2))
        draw.rounded_rectangle((left - 6, top - 6, right + 6, bottom + 6), radius=max(10, int(min_dim * 0.03)), outline=accent, fill=halo, width=4)
    elif e_type == "column":
        cx, cy = page_norm_to_local_crop(float(element.get("x", 0.0)), float(element.get("y", 0.0)), drawing_record["image"].size, region_bbox)
        r = max(12, int(min_dim * 0.028))
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=accent, fill=halo, width=4)
    return overlay.convert("RGB"), region_bbox


def _move_core_element_to_local_v12(
    sketch: Dict[str, Any],
    drawing_record: Dict[str, Any],
    geometry: Dict[str, Any],
    target_idx: int,
    local_x: float,
    local_y: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    sketch = deep_copy_jsonable(sketch)
    region_bbox_px = geometry["bbox_px"]
    rw, rh = geometry.get("crop_size", (0, 0))
    if not (0 <= target_idx < len(sketch.get("elements", []))):
        return False, "Kjernevalg ble ugyldig. Prøv på nytt.", sketch
    core = sketch["elements"][int(target_idx)]
    if clean_pdf_text(core.get("type", "")).lower() != "core":
        return False, "Valgt element er ikke en kjerne.", sketch
    image_w, image_h = drawing_record["image"].size
    core_w_px = max(float(core.get("w", 0.12)) * image_w, 18.0)
    core_h_px = max(float(core.get("h", 0.16)) * image_h, 18.0)
    left = float(clamp(local_x - (core_w_px / 2.0), 0, max(rw - core_w_px, 1)))
    top = float(clamp(local_y - (core_h_px / 2.0), 0, max(rh - core_h_px, 1)))
    snapped = _snap_bbox_to_geometry_v6(left, top, left + core_w_px, top + core_h_px, geometry)
    snapped_element = _core_element_from_local_bbox_v6(region_bbox_px, snapped, drawing_record["image"].size, clean_pdf_text(core.get("label", "")) or "Kjerne")
    sketch["elements"][int(target_idx)] = snapped_element
    return True, "Kjernen er flyttet med v12-boks-snapping og beholdt faglig størrelse.", sketch


def apply_pointer_click_to_sketch(
    sketch: Dict[str, Any],
    drawing_record: Dict[str, Any],
    tool: str,
    click_x_local: float,
    click_y_local: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    if tool != "move_core":
        return _APPLY_POINTER_CLICK_TO_SKETCH_V11_BASE(sketch, drawing_record, tool, click_x_local, click_y_local)

    sketch = deep_copy_jsonable(sketch)
    geometry = get_geometry_for_sketch(drawing_record, sketch)
    if not geometry:
        return False, "Fant ikke brukbar geometri for valgt skisse.", sketch

    image_w, image_h = drawing_record["image"].size
    region_bbox_px = geometry["bbox_px"]
    _, _, rw, rh = region_bbox_px
    local_x = float(clamp(click_x_local, 0, max(rw - 1, 1)))
    local_y = float(clamp(click_y_local, 0, max(rh - 1, 1)))
    x_norm_raw, y_norm_raw = local_point_to_page_norm(region_bbox_px, local_x, local_y, image_w, image_h)
    sketch_key = sketch_uid(sketch)
    state_key = pointer_state_key(sketch_key)
    state = get_pointer_state(sketch_key)
    if state.get("tool") and state.get("tool") != tool:
        clear_pointer_state(sketch_key)
        state = {}

    core_indices = [idx for idx, element in enumerate(sketch.get("elements", [])) if clean_pdf_text(element.get("type", "")).lower() == "core"]
    if not core_indices:
        return False, "Fant ingen kjerne å flytte.", sketch

    if len(core_indices) == 1 and "target_idx" not in state:
        changed, message, moved = _move_core_element_to_local_v12(sketch, drawing_record, geometry, int(core_indices[0]), local_x, local_y)
        clear_pointer_state(sketch_key)
        return changed, message, moved

    if "target_idx" not in state:
        idx, dist = find_nearest_pointlike_element_index(sketch.get("elements", []), drawing_record["image"].size, x_norm_raw, y_norm_raw, ["core"])
        pick_dist = max(34.0, min(rw, rh) * 0.11)
        if idx is None or (dist is not None and dist > pick_dist):
            return False, "Klikk inne i eller tett ved kjernen du vil flytte.", sketch
        st.session_state[state_key] = {"tool": tool, "target_idx": int(idx)}
        return False, "Kjerne valgt. Klikk nå ny plassering for kjernesenter.", sketch

    idx = int(state.get("target_idx", -1))
    clear_pointer_state(sketch_key)
    return _move_core_element_to_local_v12(sketch, drawing_record, geometry, idx, local_x, local_y)


refine_sketch_with_geometry_v6 = _refine_sketch_with_geometry_v12_impl



# ------------------------------------------------------------
# 13F. v13 FRI IFC/DXF/DWG-MOTOR UTEN ODA-KONTO
# IFC prioriteres som semantisk sannhet, DXF brukes som vektorraster,
# og DWG forsøkes gratis via LibreDWG hvis dwgread er tilgjengelig.
# ------------------------------------------------------------
import copy as _copy_v13
import shutil as _shutil_v13
import subprocess as _subprocess_v13

try:
    import numpy as _np_v13
except Exception:
    _np_v13 = None

try:
    import ifcopenshell as _ifcopenshell_v13
except Exception:
    _ifcopenshell_v13 = None

try:
    import ifcopenshell.geom as _ifc_geom_v13  # type: ignore
except Exception:
    _ifc_geom_v13 = None

try:
    from ifcopenshell.util import element as _ifc_element_v13  # type: ignore
except Exception:
    _ifc_element_v13 = None

try:
    import ezdxf as _ezdxf_v13
except Exception:
    _ezdxf_v13 = None

_V13_IMAGE_GEOMETRY_CACHE: Dict[str, Dict[str, Any]] = {}
_V13_IMAGE_REGION_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_IFC_GEOM_SETTINGS_V13: Any = None

_LOAD_UPLOADED_DRAWINGS_V12_BASE = load_uploaded_drawings
_DRAWING_PRIORITY_V12_BASE = drawing_priority
_PRIORITIZE_DRAWINGS_V12_BASE = prioritize_drawings
_DRAWING_MANIFEST_TEXT_V12_BASE = drawing_manifest_text
_GET_PLAN_REGIONS_FOR_RECORD_V12_BASE = get_plan_regions_for_record_v6
_DETECT_PAGE_CUES_WITH_OPENAI_V12_BASE = _detect_page_cues_with_openai_v9
_GET_GEOMETRY_FOR_SKETCH_V12_BASE = get_geometry_for_sketch
_BUILD_PLAN_GEOMETRY_GROUNDED_V12_BASE = build_plan_geometry_grounded


def _safe_upload_bytes_v13(file_obj: Any) -> bytes:
    try:
        file_obj.seek(0)
    except Exception:
        pass
    data = file_obj.read()
    try:
        file_obj.seek(0)
    except Exception:
        pass
    return data if isinstance(data, (bytes, bytearray)) else bytes(data or b"")


def _append_upload_warning_v13(message: str) -> None:
    cleaned = clean_pdf_text(message)
    if not cleaned:
        return
    warnings = st.session_state.get("rib_upload_warnings_v13", [])
    if cleaned not in warnings:
        warnings = list(warnings) + [cleaned]
    st.session_state["rib_upload_warnings_v13"] = warnings


def _append_upload_info_v14(message: str) -> None:
    cleaned = clean_pdf_text(message)
    if not cleaned:
        return
    infos = st.session_state.get("rib_upload_infos_v14", [])
    if cleaned not in infos:
        infos = list(infos) + [cleaned]
    st.session_state["rib_upload_infos_v14"] = infos


def _safe_float_v14(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _ifc_relevant_product_types_v14() -> List[str]:
    return [
        "IfcWall",
        "IfcWallStandardCase",
        "IfcCurtainWall",
        "IfcColumn",
        "IfcPile",
        "IfcSlab",
        "IfcRoof",
        "IfcBeam",
        "IfcMember",
        "IfcStair",
        "IfcStairFlight",
        "IfcRamp",
        "IfcRampFlight",
        "IfcTransportElement",
        "IfcSpace",
    ]


def _ifc_collect_storey_product_meta_v14(
    storey: Any,
    storeys_sorted: List[Any],
    storey_index: int,
    meta_by_gid: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    storey_gid = clean_pdf_text(getattr(storey, "GlobalId", "")) or str(id(storey))
    elevation = _safe_float_v14(getattr(storey, "Elevation", 0.0), 0.0)
    next_elev = None
    for later in storeys_sorted[storey_index + 1:]:
        later_elev = _safe_float_v14(getattr(later, "Elevation", elevation), elevation)
        if later_elev > elevation + 0.01:
            next_elev = later_elev
            break
    z_low = elevation - 1.25
    z_high = (next_elev + 1.25) if next_elev is not None else (elevation + 6.0)

    selected: List[Dict[str, Any]] = []
    seen: set = set()

    def add_meta(meta: Optional[Dict[str, Any]]) -> None:
        if not isinstance(meta, dict):
            return
        gid = clean_pdf_text(meta.get("gid", "")) or str(id(meta))
        if gid in seen:
            return
        selected.append(meta)
        seen.add(gid)

    for product in _ifc_storey_elements_v13(storey):
        gid = clean_pdf_text(getattr(product, "GlobalId", ""))
        add_meta(meta_by_gid.get(gid))

    if len(selected) < 8:
        for meta in list(meta_by_gid.values()):
            gid = clean_pdf_text(meta.get("gid", ""))
            if gid in seen:
                continue
            product = meta.get("_product")
            matched = False
            for rel in list(getattr(product, "ContainedInStructure", []) or []):
                structure = getattr(rel, "RelatingStructure", None)
                structure_gid = clean_pdf_text(getattr(structure, "GlobalId", ""))
                if structure is storey or structure_gid == storey_gid:
                    matched = True
                    break
            if matched:
                add_meta(meta)

    if len(selected) < 8:
        for meta in list(meta_by_gid.values()):
            gid = clean_pdf_text(meta.get("gid", ""))
            if gid in seen:
                continue
            z0, z1 = meta.get("z_range", (0.0, 0.0))
            overlap = min(float(z1), float(z_high)) - max(float(z0), float(z_low))
            if overlap > max(0.15, (float(z1) - float(z0)) * 0.10):
                add_meta(meta)

    return selected


def _norm_bbox_from_px_v13(bbox_px: Tuple[int, int, int, int], size: Tuple[int, int]) -> Dict[str, float]:
    return px_bbox_to_norm(bbox_px, int(size[0]), int(size[1]))



def _semantic_level_type_v13(label: str) -> str:
    low = clean_pdf_text(label).lower()
    if any(word in low for word in ["kjeller", "u.etg", "underet", "parkering", "basement", "garage", "p-"]):
        return "basement"
    if any(word in low for word in ["tak", "roof"]):
        return "roof"
    if any(word in low for word in ["1. etg", "1.etg", "plan 1", "ground"]):
        return "ground_floor"
    if any(word in low for word in ["plan", "etg", "etasje", "level", "typisk"]):
        return "upper_floor"
    return "unknown"



def _bbox_intersects_v13(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float], gap: float = 0.0) -> bool:
    return not (a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1])



def _bbox_union_v13(boxes: List[Tuple[float, float, float, float]]) -> Optional[Tuple[float, float, float, float]]:
    if not boxes:
        return None
    xs0 = [float(b[0]) for b in boxes]
    ys0 = [float(b[1]) for b in boxes]
    xs1 = [float(b[2]) for b in boxes]
    ys1 = [float(b[3]) for b in boxes]
    return (min(xs0), min(ys0), max(xs1), max(ys1))



def _expand_bbox_v13(bbox: Tuple[float, float, float, float], margin: float, limits: Optional[Tuple[float, float, float, float]] = None) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = bbox
    out = (x0 - margin, y0 - margin, x1 + margin, y1 + margin)
    if limits is None:
        return out
    lx0, ly0, lx1, ly1 = limits
    return (
        float(clamp(out[0], lx0, lx1)),
        float(clamp(out[1], ly0, ly1)),
        float(clamp(out[2], lx0, lx1)),
        float(clamp(out[3], ly0, ly1)),
    )



def _ifc_geom_settings_v13() -> Any:
    global _IFC_GEOM_SETTINGS_V13
    if _IFC_GEOM_SETTINGS_V13 is not None:
        return _IFC_GEOM_SETTINGS_V13
    if _ifc_geom_v13 is None:
        return None
    try:
        settings = _ifc_geom_v13.settings()
        for attr_name, value in [("USE_WORLD_COORDS", True), ("APPLY_DEFAULT_MATERIALS", False)]:
            try:
                settings.set(getattr(settings, attr_name), value)
            except Exception:
                pass
        _IFC_GEOM_SETTINGS_V13 = settings
    except Exception:
        _IFC_GEOM_SETTINGS_V13 = None
    return _IFC_GEOM_SETTINGS_V13



def _ifc_is_loadbearing_v13(product: Any) -> Optional[bool]:
    if _ifc_element_v13 is None:
        return None
    try:
        psets = _ifc_element_v13.get_psets(product) or {}
    except Exception:
        return None
    for pset_name in ["Pset_WallCommon", "Pset_ColumnCommon", "Pset_BeamCommon", "Pset_MemberCommon"]:
        pset = psets.get(pset_name)
        if isinstance(pset, dict) and "LoadBearing" in pset:
            try:
                return bool(pset.get("LoadBearing"))
            except Exception:
                return None
    return None



def _ifc_product_kind_v13(product: Any) -> str:
    cls = clean_pdf_text(product.is_a() if hasattr(product, "is_a") else "")
    name_low = " ".join(
        [
            clean_pdf_text(getattr(product, "Name", "")),
            clean_pdf_text(getattr(product, "ObjectType", "")),
            clean_pdf_text(getattr(product, "Description", "")),
        ]
    ).lower()
    if cls in {"IfcWall", "IfcWallStandardCase", "IfcCurtainWall"}:
        return "wall"
    if cls in {"IfcColumn", "IfcPile"}:
        return "column"
    if cls in {"IfcSlab", "IfcRoof"}:
        return "slab"
    if cls in {"IfcBeam", "IfcMember"}:
        return "beam"
    if cls in {"IfcStair", "IfcStairFlight", "IfcRamp", "IfcRampFlight"} or any(word in name_low for word in ["trapp", "stair", "rampe"]):
        return "stair"
    if cls == "IfcTransportElement" or any(word in name_low for word in ["heis", "lift", "elevator"]):
        return "transport"
    if cls == "IfcSpace" and any(word in name_low for word in ["sjakt", "shaft", "heis", "lift", "trapp", "stair"]):
        return "core_space"
    return ""



def _ifc_bbox_v13(product: Any) -> Optional[Tuple[float, float, float, float, float, float]]:
    if _ifc_geom_v13 is None:
        return None
    settings = _ifc_geom_settings_v13()
    if settings is None:
        return None
    try:
        shape = _ifc_geom_v13.create_shape(settings, product)
        geometry = getattr(shape, "geometry", shape)
        verts = getattr(geometry, "verts", None)
        if not verts:
            return None
        xs = verts[0::3]
        ys = verts[1::3]
        zs = verts[2::3]
        if not xs or not ys or not zs:
            return None
        return (
            float(min(xs)),
            float(min(ys)),
            float(min(zs)),
            float(max(xs)),
            float(max(ys)),
            float(max(zs)),
        )
    except Exception:
        return None



def _ifc_storey_elements_v13(storey: Any) -> List[Any]:
    elements: List[Any] = []
    seen: set = set()
    for rel in list(getattr(storey, "ContainsElements", []) or []):
        for elem in list(getattr(rel, "RelatedElements", []) or []):
            gid = clean_pdf_text(getattr(elem, "GlobalId", "")) or str(id(elem))
            if gid in seen:
                continue
            seen.add(gid)
            elements.append(elem)
    return elements



def _cluster_boxes_v13(boxes: List[Tuple[float, float, float, float]], gap: float) -> List[Tuple[float, float, float, float]]:
    clusters: List[Tuple[float, float, float, float]] = []
    for box in boxes:
        merged = False
        for idx, cluster in enumerate(list(clusters)):
            if _bbox_intersects_v13(cluster, box, gap=gap):
                clusters[idx] = (
                    min(cluster[0], box[0]),
                    min(cluster[1], box[1]),
                    max(cluster[2], box[2]),
                    max(cluster[3], box[3]),
                )
                merged = True
                break
        if not merged:
            clusters.append(box)
    changed = True
    while changed:
        changed = False
        new_clusters: List[Tuple[float, float, float, float]] = []
        while clusters:
            cluster = clusters.pop(0)
            merged_any = False
            for idx, other in enumerate(list(clusters)):
                if _bbox_intersects_v13(cluster, other, gap=gap):
                    cluster = (
                        min(cluster[0], other[0]),
                        min(cluster[1], other[1]),
                        max(cluster[2], other[2]),
                        max(cluster[3], other[3]),
                    )
                    clusters.pop(idx)
                    clusters.insert(0, cluster)
                    merged_any = True
                    changed = True
                    break
            if not merged_any:
                new_clusters.append(cluster)
        clusters = new_clusters
    return clusters



def _map_bbox_to_px_v13(
    bbox: Tuple[float, float, float, float],
    extents: Tuple[float, float, float, float],
    canvas_size: Tuple[int, int],
    pad: int,
) -> Tuple[int, int, int, int]:
    minx, miny, maxx, maxy = extents
    width = max(maxx - minx, 1e-6)
    height = max(maxy - miny, 1e-6)
    canvas_w, canvas_h = canvas_size
    scale = min((canvas_w - 2 * pad) / width, (canvas_h - 2 * pad) / height)
    x0, y0, x1, y1 = bbox
    px0 = int(round(pad + (x0 - minx) * scale))
    px1 = int(round(pad + (x1 - minx) * scale))
    py1 = int(round(canvas_h - pad - (y0 - miny) * scale))
    py0 = int(round(canvas_h - pad - (y1 - miny) * scale))
    return (
        int(clamp(min(px0, px1), 0, canvas_w - 1)),
        int(clamp(min(py0, py1), 0, canvas_h - 1)),
        int(clamp(max(px0, px1), 1, canvas_w - 1)),
        int(clamp(max(py0, py1), 1, canvas_h - 1)),
    )



def _local_geometry_from_semantic_objects_v13(
    image_size: Tuple[int, int],
    region_bbox_px: Tuple[int, int, int, int],
    objects_px: List[Dict[str, Any]],
    core_boxes_px: List[Tuple[int, int, int, int]],
) -> Dict[str, Any]:
    if _np_v13 is None:
        import numpy as _np_v13_local  # type: ignore
    else:
        _np_v13_local = _np_v13

    rx, ry, rw, rh = region_bbox_px
    rw = max(int(rw), 2)
    rh = max(int(rh), 2)
    mask = _np_v13_local.zeros((rh, rw), dtype="uint8")
    vertical_segments: List[Dict[str, Any]] = []
    horizontal_segments: List[Dict[str, Any]] = []
    columns_local: List[Tuple[float, float]] = []

    for obj in objects_px:
        kind = clean_pdf_text(obj.get("kind", "")).lower()
        x0, y0, x1, y1 = [int(v) for v in obj.get("bbox_px", (0, 0, 0, 0))]
        lx0 = int(clamp(x0 - rx, 0, rw - 1))
        ly0 = int(clamp(y0 - ry, 0, rh - 1))
        lx1 = int(clamp(x1 - rx, lx0 + 1, rw - 1))
        ly1 = int(clamp(y1 - ry, ly0 + 1, rh - 1))
        if kind in {"slab", "wall", "column", "stair", "transport", "core_space"}:
            mask[ly0:ly1 + 1, lx0:lx1 + 1] = 255
        width = max(1, lx1 - lx0)
        height = max(1, ly1 - ly0)
        if kind == "wall":
            if height >= width:
                vertical_segments.append({
                    "kind": "vertical",
                    "center": float((lx0 + lx1) / 2.0),
                    "x": float(lx0),
                    "y": float(ly0),
                    "w": float(width),
                    "h": float(height),
                    "length": float(height),
                    "thickness": float(width),
                })
            else:
                horizontal_segments.append({
                    "kind": "horizontal",
                    "center": float((ly0 + ly1) / 2.0),
                    "x": float(lx0),
                    "y": float(ly0),
                    "w": float(width),
                    "h": float(height),
                    "length": float(width),
                    "thickness": float(height),
                })
        elif kind == "column":
            columns_local.append((float((lx0 + lx1) / 2.0), float((ly0 + ly1) / 2.0)))

    core_bbox = None
    for box in core_boxes_px[:1]:
        x0, y0, x1, y1 = [int(v) for v in box]
        lx0 = int(clamp(x0 - rx, 0, rw - 1))
        ly0 = int(clamp(y0 - ry, 0, rh - 1))
        lx1 = int(clamp(x1 - rx, lx0 + 1, rw - 1))
        ly1 = int(clamp(y1 - ry, ly0 + 1, rh - 1))
        core_bbox = (lx0, ly0, max(8, lx1 - lx0), max(8, ly1 - ly0))
        break
    if core_bbox is None:
        core_bbox = (int(rw * 0.42), int(rh * 0.34), max(18, int(rw * 0.12)), max(24, int(rh * 0.18)))

    support_vertical = sorted(vertical_segments, key=lambda item: (float(item.get("length", 0.0)), -abs(float(item.get("center", 0.0)) - (rw / 2.0))), reverse=True)[:3]
    support_horizontal = sorted(horizontal_segments, key=lambda item: (float(item.get("length", 0.0)), -abs(float(item.get("center", 0.0)) - (rh / 2.0))), reverse=True)[:2]

    grid_x = [{"pos": float(item[0]), "score": 1.0} for item in columns_local]
    grid_y = [{"pos": float(item[1]), "score": 1.0} for item in columns_local]
    for segment in vertical_segments:
        grid_x.append({"pos": float(segment.get("center", 0.0)), "score": min(1.0, float(segment.get("length", 0.0)) / max(rh, 1.0))})
    for segment in horizontal_segments:
        grid_y.append({"pos": float(segment.get("center", 0.0)), "score": min(1.0, float(segment.get("length", 0.0)) / max(rw, 1.0))})

    def _dedupe_positions(items: List[Dict[str, float]], min_gap: float) -> List[Dict[str, float]]:
        ordered = sorted(items, key=lambda item: (-float(item.get("score", 0.0)), float(item.get("pos", 0.0))))
        kept: List[Dict[str, float]] = []
        for item in ordered:
            pos = float(item.get("pos", 0.0))
            if all(abs(pos - float(other.get("pos", 0.0))) >= min_gap for other in kept):
                kept.append({"pos": pos, "score": float(item.get("score", 0.0))})
        return sorted(kept, key=lambda item: float(item.get("pos", 0.0)))

    grid_x = _dedupe_positions(grid_x, max(14.0, rw * 0.05))
    grid_y = _dedupe_positions(grid_y, max(14.0, rh * 0.05))

    junctions: List[Dict[str, Any]] = []
    for x_item in grid_x:
        x = float(x_item.get("pos", 0.0))
        for y_item in grid_y:
            y = float(y_item.get("pos", 0.0))
            if 0 <= int(clamp(y, 0, rh - 1)) < rh and 0 <= int(clamp(x, 0, rw - 1)) < rw and mask[int(y), int(x)] > 0:
                junctions.append({"x": x, "y": y, "score": float(x_item.get("score", 0.0)) + float(y_item.get("score", 0.0))})
    for cx, cy in columns_local:
        junctions.append({"x": float(cx), "y": float(cy), "score": 2.0})

    min_gap = max(16.0, min(rw, rh) * 0.05)
    deduped_junctions: List[Dict[str, Any]] = []
    for item in sorted(junctions, key=lambda obj: float(obj.get("score", 0.0)), reverse=True):
        x = float(item.get("x", 0.0))
        y = float(item.get("y", 0.0))
        if all((x - float(prev.get("x", 0.0))) ** 2 + (y - float(prev.get("y", 0.0))) ** 2 > (min_gap ** 2) for prev in deduped_junctions):
            deduped_junctions.append({"x": x, "y": y, "score": float(item.get("score", 0.0))})
        if len(deduped_junctions) >= 64:
            break

    return {
        "bbox_px": (int(rx), int(ry), int(rw), int(rh)),
        "crop_size": (int(rw), int(rh)),
        "footprint_mask": mask,
        "footprint_bbox_local": (0, 0, int(rw), int(rh)),
        "vertical_segments": vertical_segments,
        "horizontal_segments": horizontal_segments,
        "support_vertical_v9": support_vertical,
        "support_horizontal_v9": support_horizontal,
        "grid_x": grid_x,
        "grid_y": grid_y,
        "junctions": deduped_junctions,
        "core_bbox": core_bbox,
    }



def _render_ifc_storey_record_v13(file_name: str, storey_label: str, objects: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not objects:
        return None
    extents = _bbox_union_v13([tuple(obj["bbox_world_xy"]) for obj in objects if obj.get("bbox_world_xy")])
    if extents is None:
        return None
    minx, miny, maxx, maxy = extents
    dx = max(maxx - minx, 0.1)
    dy = max(maxy - miny, 0.1)
    canvas_w = 1500
    canvas_h = 1150
    if dx > dy * 1.65:
        canvas_w, canvas_h = 1580, 980
    elif dy > dx * 1.30:
        canvas_w, canvas_h = 1180, 1580
    pad = 64
    image = Image.new("RGB", (canvas_w, canvas_h), (247, 247, 244))
    draw = ImageDraw.Draw(image, "RGBA")
    objects_px: List[Dict[str, Any]] = []
    for obj in objects:
        bbox_px = _map_bbox_to_px_v13(tuple(obj["bbox_world_xy"]), extents, image.size, pad)
        x0, y0, x1, y1 = bbox_px
        if x1 <= x0 or y1 <= y0:
            continue
        obj_px = {**obj, "bbox_px": bbox_px}
        objects_px.append(obj_px)

    slabs = [obj for obj in objects_px if clean_pdf_text(obj.get("kind", "")).lower() == "slab"]
    walls = [obj for obj in objects_px if clean_pdf_text(obj.get("kind", "")).lower() == "wall"]
    columns = [obj for obj in objects_px if clean_pdf_text(obj.get("kind", "")).lower() == "column"]
    stairs = [obj for obj in objects_px if clean_pdf_text(obj.get("kind", "")).lower() in {"stair", "transport", "core_space"}]
    beams = [obj for obj in objects_px if clean_pdf_text(obj.get("kind", "")).lower() == "beam"]

    for obj in slabs:
        x0, y0, x1, y1 = obj["bbox_px"]
        draw.rounded_rectangle((x0, y0, x1, y1), radius=4, fill=(230, 225, 214, 120), outline=(222, 214, 198, 100), width=1)
    for obj in walls:
        x0, y0, x1, y1 = obj["bbox_px"]
        draw.rounded_rectangle((x0, y0, x1, y1), radius=2, fill=(118, 122, 126, 245), outline=(72, 76, 80, 255), width=1)
    for obj in beams:
        x0, y0, x1, y1 = obj["bbox_px"]
        draw.line((x0, (y0 + y1) / 2.0, x1, (y0 + y1) / 2.0), fill=(154, 160, 170, 200), width=max(2, min(8, int(min(abs(y1 - y0), abs(x1 - x0)) or 2))))
    for obj in columns:
        x0, y0, x1, y1 = obj["bbox_px"]
        draw.rounded_rectangle((x0, y0, x1, y1), radius=3, fill=(37, 84, 107, 255), outline=(255, 255, 255, 230), width=1)
    for obj in stairs:
        x0, y0, x1, y1 = obj["bbox_px"]
        draw.rounded_rectangle((x0, y0, x1, y1), radius=6, fill=(255, 193, 94, 95), outline=(225, 145, 31, 255), width=3)

    envelope_boxes = [tuple(obj["bbox_px"]) for obj in walls + slabs] or [tuple(obj["bbox_px"]) for obj in objects_px]
    envelope_px = _bbox_union_v13(envelope_boxes) or (pad, pad, canvas_w - pad, canvas_h - pad)
    seed_core_boxes = [tuple(obj["bbox_px"]) for obj in stairs]
    if seed_core_boxes:
        core_gap = max(18.0, min(canvas_w, canvas_h) * 0.025)
        core_boxes_px = _cluster_boxes_v13(seed_core_boxes, core_gap)
    else:
        core_boxes_px = []

    expanded_core_boxes: List[Tuple[int, int, int, int]] = []
    near_gap = max(22.0, min(canvas_w, canvas_h) * 0.035)
    for core_box in core_boxes_px[:2]:
        merged = list(core_box)
        for obj in walls:
            wall_box = tuple(obj["bbox_px"])
            if _bbox_intersects_v13(_expand_bbox_v13(tuple(merged), near_gap), wall_box, gap=0):
                merged = [min(merged[0], wall_box[0]), min(merged[1], wall_box[1]), max(merged[2], wall_box[2]), max(merged[3], wall_box[3])]
        expanded_core_boxes.append((int(merged[0]), int(merged[1]), int(merged[2]), int(merged[3])))

    if not expanded_core_boxes:
        env_x0, env_y0, env_x1, env_y1 = [int(v) for v in envelope_px]
        cx = int((env_x0 + env_x1) / 2)
        cy = int((env_y0 + env_y1) / 2)
        cw = max(54, int((env_x1 - env_x0) * 0.12))
        ch = max(72, int((env_y1 - env_y0) * 0.18))
        expanded_core_boxes = [(cx - cw // 2, cy - ch // 2, cx + cw // 2, cy + ch // 2)]

    for idx, core_box in enumerate(expanded_core_boxes[:2], start=1):
        x0, y0, x1, y1 = core_box
        draw.rounded_rectangle((x0, y0, x1, y1), radius=8, fill=(255, 196, 64, 56), outline=(230, 158, 41, 255), width=3)
        font = get_font(20, bold=True)
        draw_label(draw, (x0 + 8, y0 + 8), f"Kjerne {idx}", font, (80, 60, 10, 255), (255, 230, 180, 235))

    env_x0, env_y0, env_x1, env_y1 = [int(v) for v in envelope_px]
    region_margin = max(26, int(min(canvas_w, canvas_h) * 0.03))
    plan_bbox_px = (
        int(clamp(env_x0 - region_margin, 0, canvas_w - 2)),
        int(clamp(env_y0 - region_margin, 0, canvas_h - 2)),
        int(clamp((env_x1 - env_x0) + (region_margin * 2), 2, canvas_w)),
        int(clamp((env_y1 - env_y0) + (region_margin * 2), 2, canvas_h)),
    )

    wall_candidates_scored: List[Dict[str, Any]] = []
    edge_margin_px = max(24.0, min(canvas_w, canvas_h) * 0.06)
    for obj in walls:
        x0, y0, x1, y1 = [float(v) for v in obj["bbox_px"]]
        width_px = max(x1 - x0, 1.0)
        height_px = max(y1 - y0, 1.0)
        orientation = "horizontal" if width_px >= height_px else "vertical"
        center_x = (x0 + x1) / 2.0
        center_y = (y0 + y1) / 2.0
        length = max(width_px, height_px)
        edge_penalty = 0.0
        if orientation == "vertical":
            if min(abs(center_x - env_x0), abs(env_x1 - center_x)) < edge_margin_px:
                edge_penalty += 80.0
        else:
            if min(abs(center_y - env_y0), abs(env_y1 - center_y)) < edge_margin_px:
                edge_penalty += 80.0
        loadbearing = obj.get("load_bearing") is True
        score = length + (55.0 if loadbearing else 0.0) - edge_penalty
        if expanded_core_boxes:
            core_cx = sum((box[0] + box[2]) / 2.0 for box in expanded_core_boxes) / len(expanded_core_boxes)
            core_cy = sum((box[1] + box[3]) / 2.0 for box in expanded_core_boxes) / len(expanded_core_boxes)
            score -= abs((center_x if orientation == "vertical" else center_y) - (core_cx if orientation == "vertical" else core_cy)) * 0.07
        wall_candidates_scored.append({**obj, "score": score, "orientation": orientation})

    chosen_walls: List[Dict[str, Any]] = []
    min_wall_spacing = max(24.0, min(canvas_w, canvas_h) * 0.10)
    for obj in sorted(wall_candidates_scored, key=lambda item: float(item.get("score", 0.0)), reverse=True):
        x0, y0, x1, y1 = [float(v) for v in obj["bbox_px"]]
        axis_value = ((x0 + x1) / 2.0) if obj.get("orientation") == "vertical" else ((y0 + y1) / 2.0)
        if all(abs(axis_value - (prev.get("axis_value", axis_value))) >= min_wall_spacing for prev in chosen_walls):
            obj = {**obj, "axis_value": axis_value}
            chosen_walls.append(obj)
        if len(chosen_walls) >= 4:
            break

    cue = {
        "page_index": 0,
        "drawing_role": "plan",
        "plan_confidence": 0.99,
        "multi_plan": False,
        "level_type": _semantic_level_type_v13(storey_label),
        "plan_bbox": _norm_bbox_from_px_v13(plan_bbox_px, image.size),
        "exterior_envelope_bbox": _norm_bbox_from_px_v13((env_x0, env_y0, env_x1 - env_x0, env_y1 - env_y0), image.size),
        "core_candidates": [],
        "bearing_wall_candidates": [],
        "column_candidates": [],
        "notes": [
            "IFC-basert semantisk plan er brukt som primær tegnekilde.",
            "Yttervegg/perimeter er filtrert separat fra sannsynlige indre bærelinjer.",
        ],
    }

    for idx, box in enumerate(expanded_core_boxes[:2], start=1):
        x0, y0, x1, y1 = [int(v) for v in box]
        cue["core_candidates"].append({
            "x": round(x0 / max(canvas_w, 1), 6),
            "y": round(y0 / max(canvas_h, 1), 6),
            "w": round((x1 - x0) / max(canvas_w, 1), 6),
            "h": round((y1 - y0) / max(canvas_h, 1), 6),
            "confidence": 0.94 - (idx * 0.03),
        })

    for obj in chosen_walls:
        x0, y0, x1, y1 = [float(v) for v in obj["bbox_px"]]
        orientation = obj.get("orientation")
        if orientation == "vertical":
            cue["bearing_wall_candidates"].append({
                "x1": round(((x0 + x1) / 2.0) / max(canvas_w, 1), 6),
                "y1": round(y0 / max(canvas_h, 1), 6),
                "x2": round(((x0 + x1) / 2.0) / max(canvas_w, 1), 6),
                "y2": round(y1 / max(canvas_h, 1), 6),
                "confidence": 0.88 if obj.get("load_bearing") is True else 0.74,
                "reason": "IFC-vegg i indre sone",
            })
        else:
            cue["bearing_wall_candidates"].append({
                "x1": round(x0 / max(canvas_w, 1), 6),
                "y1": round(((y0 + y1) / 2.0) / max(canvas_h, 1), 6),
                "x2": round(x1 / max(canvas_w, 1), 6),
                "y2": round(((y0 + y1) / 2.0) / max(canvas_h, 1), 6),
                "confidence": 0.84 if obj.get("load_bearing") is True else 0.70,
                "reason": "IFC-vegg i indre sone",
            })

    for obj in sorted(columns, key=lambda item: (item["bbox_px"][1], item["bbox_px"][0]))[:12]:
        x0, y0, x1, y1 = [float(v) for v in obj["bbox_px"]]
        cue["column_candidates"].append({
            "x": round(((x0 + x1) / 2.0) / max(canvas_w, 1), 6),
            "y": round(((y0 + y1) / 2.0) / max(canvas_h, 1), 6),
            "confidence": 0.92,
            "reason": "IFC-søyle",
        })

    semantic_geometry = _local_geometry_from_semantic_objects_v13(image.size, plan_bbox_px, objects_px, expanded_core_boxes)
    cache_key = f"ifc-sem-{file_name}-{storey_label}-{abs(hash((file_name, storey_label, len(objects_px))))}"
    try:
        image.info["_semantic_geometry_key_v13"] = cache_key
    except Exception:
        pass
    _V13_IMAGE_GEOMETRY_CACHE[cache_key] = semantic_geometry
    _V13_IMAGE_REGION_CACHE[cache_key] = [{"bbox_px": semantic_geometry["bbox_px"], "bbox_norm": cue["plan_bbox"], "score": 1.0}]

    record = build_drawing_record(f"{file_name} - {storey_label}", image, "Opplastet IFC")
    try:
        record["image"].info["_semantic_geometry_key_v13"] = cache_key
    except Exception:
        pass
    record.update(
        {
            "name": clean_pdf_text(f"{file_name} - {storey_label}"),
            "label": clean_pdf_text(storey_label),
            "hint": "plan",
            "drawing_format": "ifc",
            "ifc_storey_name": clean_pdf_text(storey_label),
            "semantic_page_cue_v13": cue,
            "semantic_geometry_v13": semantic_geometry,
            "semantic_plan_regions_v13": _V13_IMAGE_REGION_CACHE.get(cache_key, []),
            "semantic_summary_v13": {
                "walls": len(walls),
                "columns": len(columns),
                "stairs_or_transport": len(stairs),
                "cores": len(expanded_core_boxes),
            },
        }
    )
    return record



def _load_ifc_drawings_v13(file_name: str, file_bytes: bytes, suffix: str) -> List[Dict[str, Any]]:
    if _ifcopenshell_v13 is None:
        _append_upload_warning_v13("IFC-fil ble lastet opp, men pakken 'ifcopenshell' er ikke installert i miljøet. Legg den til i requirements for å bruke IFC som primærkilde.")
        return []
    if _ifc_geom_v13 is None:
        _append_upload_warning_v13("IFC-fil ble lastet opp, men 'ifcopenshell.geom' er ikke tilgjengelig i miljøet. IFC kan ikke rasteriseres til planbilder i denne installasjonen.")
        return []

    tmp_path = None
    drawings: List[Dict[str, Any]] = []
    try:
        suffix_low = clean_pdf_text(suffix).lower() or ".ifc"
        if suffix_low == ".ifczip":
            import zipfile
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                members = [name for name in zf.namelist() if clean_pdf_text(name).lower().endswith('.ifc')]
                if not members:
                    _append_upload_warning_v13(f"IFCZIP-filen '{file_name}' inneholdt ingen .ifc-fil.")
                    return []
                with tempfile.NamedTemporaryFile(delete=False, suffix='.ifc') as tmp:
                    tmp.write(zf.read(members[0]))
                    tmp.flush()
                    tmp_path = tmp.name
        else:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or '.ifc') as tmp:
                tmp.write(file_bytes)
                tmp.flush()
                tmp_path = tmp.name

        model = _ifcopenshell_v13.open(tmp_path)
        storeys = list(model.by_type('IfcBuildingStorey') or [])
        if not storeys:
            _append_upload_warning_v13(f"IFC-filen '{file_name}' inneholdt ingen IfcBuildingStorey og ble hoppet over.")
            return []

        storeys = sorted(storeys, key=lambda item: _safe_float_v14(getattr(item, 'Elevation', 0.0), 0.0))
        relevant_products: List[Any] = []
        seen_products: set = set()
        for type_name in _ifc_relevant_product_types_v14():
            try:
                for product in list(model.by_type(type_name) or []):
                    gid = clean_pdf_text(getattr(product, 'GlobalId', '')) or str(id(product))
                    if gid in seen_products:
                        continue
                    seen_products.add(gid)
                    relevant_products.append(product)
            except Exception:
                continue

        meta_by_gid: Dict[str, Dict[str, Any]] = {}
        for product in relevant_products:
            kind = _ifc_product_kind_v13(product)
            if not kind:
                continue
            bbox6 = _ifc_bbox_v13(product)
            if bbox6 is None:
                continue
            x0, y0, z0, x1, y1, z1 = bbox6
            if x1 - x0 <= 0 or y1 - y0 <= 0:
                continue
            gid = clean_pdf_text(getattr(product, 'GlobalId', '')) or str(id(product))
            meta_by_gid[gid] = {
                'gid': gid,
                'name': clean_pdf_text(getattr(product, 'Name', '')),
                'kind': kind,
                'class_name': clean_pdf_text(product.is_a() if hasattr(product, 'is_a') else ''),
                'bbox_world_xy': (float(x0), float(y0), float(x1), float(y1)),
                'z_range': (float(z0), float(z1)),
                'load_bearing': _ifc_is_loadbearing_v13(product),
                '_product': product,
            }

        if not meta_by_gid:
            _append_upload_warning_v13(f"IFC-filen '{file_name}' kunne leses, men ingen brukbare IFC-objekter med geometri ble funnet.")
            return []

        for storey_index, storey in enumerate(storeys[:12]):
            storey_label = clean_pdf_text(getattr(storey, 'Name', '')) or f"Storey {len(drawings) + 1}"
            selected_meta = _ifc_collect_storey_product_meta_v14(storey, storeys, storey_index, meta_by_gid)
            objects: List[Dict[str, Any]] = []
            for meta in selected_meta:
                bbox_world_xy = meta.get('bbox_world_xy')
                if not isinstance(bbox_world_xy, tuple) or len(bbox_world_xy) != 4:
                    continue
                x0, y0, x1, y1 = bbox_world_xy
                objects.append(
                    {
                        'gid': meta.get('gid', ''),
                        'name': meta.get('name', ''),
                        'kind': meta.get('kind', ''),
                        'class_name': meta.get('class_name', ''),
                        'bbox_world_xy': (float(x0), float(y0), float(x1), float(y1)),
                        'z_range': tuple(meta.get('z_range', (0.0, 0.0))),
                        'load_bearing': meta.get('load_bearing'),
                    }
                )
            record = _render_ifc_storey_record_v13(file_name, storey_label, objects)
            if record is not None:
                drawings.append(record)

        if drawings:
            _append_upload_info_v14(f"IFC lest OK: {len(drawings)} etasje-/planbilder generert fra '{file_name}'.")
        else:
            _append_upload_warning_v13(f"IFC-filen '{file_name}' kunne leses, men ingen brukbare planbilder ble generert fra modellen.")
        return drawings
    except Exception as exc:
        _append_upload_warning_v13(f"IFC-lesing feilet for '{file_name}': {type(exc).__name__}: {short_text(exc, 180)}")
        return []
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _collect_dxf_primitives_v13(doc: Any) -> List[Dict[str, Any]]:
    primitives: List[Dict[str, Any]] = []
    try:
        msp = doc.modelspace()
    except Exception:
        return primitives
    for entity in msp:
        try:
            etype = clean_pdf_text(entity.dxftype()).upper()
        except Exception:
            continue
        try:
            if etype == "LINE":
                start = entity.dxf.start
                end = entity.dxf.end
                primitives.append({"type": "line", "points": [(float(start.x), float(start.y)), (float(end.x), float(end.y))]})
            elif etype == "LWPOLYLINE":
                pts = [(float(point[0]), float(point[1])) for point in entity.get_points("xy")]
                if len(pts) >= 2:
                    primitives.append({"type": "poly", "points": pts, "closed": bool(entity.closed)})
            elif etype == "POLYLINE":
                pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
                if len(pts) >= 2:
                    primitives.append({"type": "poly", "points": pts, "closed": bool(getattr(entity, "is_closed", False))})
            elif etype == "CIRCLE":
                center = entity.dxf.center
                radius = float(entity.dxf.radius)
                primitives.append({"type": "circle", "center": (float(center.x), float(center.y)), "radius": radius})
            elif etype == "ARC":
                center = entity.dxf.center
                radius = float(entity.dxf.radius)
                primitives.append({"type": "circle", "center": (float(center.x), float(center.y)), "radius": radius})
        except Exception:
            continue
    return primitives



def _dxf_extents_v13(primitives: List[Dict[str, Any]]) -> Optional[Tuple[float, float, float, float]]:
    xs: List[float] = []
    ys: List[float] = []
    for item in primitives:
        if item.get("type") in {"line", "poly"}:
            for x, y in item.get("points", []):
                xs.append(float(x))
                ys.append(float(y))
        elif item.get("type") == "circle":
            cx, cy = item.get("center", (0.0, 0.0))
            radius = float(item.get("radius", 0.0))
            xs.extend([float(cx) - radius, float(cx) + radius])
            ys.extend([float(cy) - radius, float(cy) + radius])
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))



def _rasterize_dxf_primitives_v13(file_name: str, primitives: List[Dict[str, Any]], source_label: str) -> Optional[Dict[str, Any]]:
    extents = _dxf_extents_v13(primitives)
    if extents is None:
        return None
    minx, miny, maxx, maxy = extents
    dx = max(maxx - minx, 0.1)
    dy = max(maxy - miny, 0.1)
    canvas_w = 1500
    canvas_h = 1150
    if dx > dy * 1.65:
        canvas_w, canvas_h = 1580, 980
    elif dy > dx * 1.25:
        canvas_w, canvas_h = 1180, 1580
    pad = 64
    image = Image.new("RGB", (canvas_w, canvas_h), (252, 252, 251))
    draw = ImageDraw.Draw(image, "RGBA")

    def map_point(point: Tuple[float, float]) -> Tuple[float, float]:
        x, y = float(point[0]), float(point[1])
        scale = min((canvas_w - 2 * pad) / dx, (canvas_h - 2 * pad) / dy)
        px = pad + (x - minx) * scale
        py = canvas_h - pad - (y - miny) * scale
        return float(px), float(py)

    for item in primitives:
        if item.get("type") == "line":
            p1, p2 = item.get("points", [(0.0, 0.0), (0.0, 0.0)])[:2]
            x1, y1 = map_point(p1)
            x2, y2 = map_point(p2)
            draw.line((x1, y1, x2, y2), fill=(90, 94, 98, 255), width=2)
        elif item.get("type") == "poly":
            pts = [map_point(pt) for pt in item.get("points", [])]
            if len(pts) >= 2:
                draw.line(pts + ([pts[0]] if item.get("closed") else []), fill=(80, 84, 89, 255), width=2)
        elif item.get("type") == "circle":
            cx, cy = map_point(item.get("center", (0.0, 0.0)))
            radius = float(item.get("radius", 0.0))
            scale = min((canvas_w - 2 * pad) / dx, (canvas_h - 2 * pad) / dy)
            rr = max(1.0, radius * scale)
            draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(110, 110, 110, 255), width=2)

    region_bbox_px = (pad // 2, pad // 2, canvas_w - pad, canvas_h - pad)
    cache_key = f"dxf-sem-{file_name}-{abs(hash((file_name, len(primitives), source_label)))}"
    try:
        image.info["_semantic_geometry_key_v13"] = cache_key
    except Exception:
        pass
    _V13_IMAGE_REGION_CACHE[cache_key] = [{"bbox_px": region_bbox_px, "bbox_norm": _norm_bbox_from_px_v13(region_bbox_px, image.size), "score": 0.8}]

    record = build_drawing_record(file_name, image, source_label)
    try:
        record["image"].info["_semantic_geometry_key_v13"] = cache_key
    except Exception:
        pass
    record.update(
        {
            "name": clean_pdf_text(file_name),
            "label": clean_pdf_text(Path(file_name).stem),
            "hint": detect_drawing_hint(file_name),
            "drawing_format": "dxf" if source_label.lower().endswith("dxf") else ("dwg" if "dwg" in source_label.lower() else "dxf"),
            "semantic_plan_regions_v13": _V13_IMAGE_REGION_CACHE.get(cache_key, []),
            "semantic_page_cue_v13": {
                "page_index": 0,
                "drawing_role": "plan" if detect_drawing_hint(file_name) == "plan" else detect_drawing_hint(file_name),
                "plan_confidence": 0.82,
                "multi_plan": False,
                "level_type": _semantic_level_type_v13(file_name),
                "plan_bbox": _norm_bbox_from_px_v13(region_bbox_px, image.size),
                "exterior_envelope_bbox": _norm_bbox_from_px_v13(region_bbox_px, image.size),
                "core_candidates": [],
                "bearing_wall_candidates": [],
                "column_candidates": [],
                "notes": ["DXF/DWG-vektor er rasterisert lokalt uten ODA-konto."],
            },
        }
    )
    return record



def _load_dxf_or_dwg_drawings_v13(file_name: str, file_bytes: bytes, suffix: str) -> List[Dict[str, Any]]:
    if _ezdxf_v13 is None:
        _append_upload_warning_v13("DXF/DWG-fil ble lastet opp, men pakken 'ezdxf' er ikke installert i miljøet.")
        return []
    tmp_in = None
    tmp_dxf = None
    try:
        suffix = clean_pdf_text(suffix).lower() or ".dxf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            tmp_in = tmp.name

        source_label = "Opplastet DXF"
        parse_path = tmp_in
        if suffix == ".dwg":
            dwgread = _shutil_v13.which("dwgread")
            if not dwgread:
                _append_upload_warning_v13(
                    f"DWG-filen '{file_name}' kan ikke leses direkte i denne installasjonen fordi 'dwgread' ikke er tilgjengelig. Last opp IFC/PDF eller eksporter DXF, eller installer LibreDWG på serveren."
                )
                return []
            with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as out_dxf:
                tmp_dxf = out_dxf.name
            command = [dwgread, "-O", "DXF", "-o", tmp_dxf, tmp_in]
            result = _subprocess_v13.run(command, stdout=_subprocess_v13.PIPE, stderr=_subprocess_v13.PIPE, text=True, timeout=120)
            if int(result.returncode or 0) != 0 or not os.path.exists(tmp_dxf) or os.path.getsize(tmp_dxf) <= 0:
                stderr = short_text(result.stderr or result.stdout or "ukjent feil", 180)
                _append_upload_warning_v13(f"DWG-konvertering via LibreDWG feilet for '{file_name}': {stderr}")
                return []
            parse_path = tmp_dxf
            source_label = "Opplastet DWG via LibreDWG"

        doc = _ezdxf_v13.readfile(parse_path)
        primitives = _collect_dxf_primitives_v13(doc)
        record = _rasterize_dxf_primitives_v13(file_name, primitives, source_label)
        return [record] if record is not None else []
    except Exception as exc:
        _append_upload_warning_v13(f"DXF/DWG-lesing feilet for '{file_name}': {type(exc).__name__}: {short_text(exc, 180)}")
        return []
    finally:
        for candidate in [tmp_in, tmp_dxf]:
            if candidate and os.path.exists(candidate):
                try:
                    os.remove(candidate)
                except Exception:
                    pass



def load_uploaded_drawings(files: Optional[List[Any]], max_pdf_pages: int = 4) -> List[Dict[str, Any]]:
    drawings: List[Dict[str, Any]] = []
    st.session_state["rib_upload_warnings_v13"] = []
    st.session_state["rib_upload_infos_v14"] = []
    if not files:
        return drawings

    grouped: Dict[str, List[Tuple[Any, str, str, bytes]]] = {"ifc": [], "dxf": [], "dwg": [], "pdf": [], "image": [], "other": []}
    for f in files:
        name = clean_pdf_text(getattr(f, "name", "ukjent_fil"))
        suffix = Path(name).suffix.lower()
        file_bytes = _safe_upload_bytes_v13(f)
        if not file_bytes:
            continue
        bucket = "other"
        if suffix in {".ifc", ".ifczip"}:
            bucket = "ifc"
        elif suffix == ".dxf":
            bucket = "dxf"
        elif suffix == ".dwg":
            bucket = "dwg"
        elif suffix == ".pdf":
            bucket = "pdf"
        elif suffix in SUPPORTED_IMAGE_EXTS:
            bucket = "image"
        grouped[bucket].append((f, name, suffix, file_bytes))

    for f, name, suffix, file_bytes in grouped["ifc"]:
        drawings.extend(_load_ifc_drawings_v13(name, file_bytes, suffix))

    ifc_count = sum(1 for record in drawings if clean_pdf_text(record.get("drawing_format", "")).lower() == "ifc")

    for f, name, suffix, file_bytes in grouped["pdf"]:
        drawings.extend(_LOAD_UPLOADED_DRAWINGS_V12_BASE([f], max_pdf_pages=max_pdf_pages))

    for f, name, suffix, file_bytes in grouped["image"]:
        drawings.extend(_LOAD_UPLOADED_DRAWINGS_V12_BASE([f], max_pdf_pages=max_pdf_pages))

    for f, name, suffix, file_bytes in grouped["dxf"]:
        drawings.extend(_load_dxf_or_dwg_drawings_v13(name, file_bytes, suffix))

    if grouped["dwg"]:
        dwgread = _shutil_v13.which("dwgread")
        if not dwgread:
            if ifc_count > 0:
                _append_upload_info_v14(
                    f"{len(grouped['dwg'])} DWG-fil(er) ble hoppet over. IFC ble lest og brukes som primær modellkilde i denne kjøringen."
                )
            else:
                sample_names = ", ".join(name for _, name, _, _ in grouped["dwg"][:3])
                _append_upload_warning_v13(
                    f"{len(grouped['dwg'])} DWG-fil(er) kunne ikke leses direkte fordi 'dwgread' ikke er tilgjengelig i miljøet. Last opp IFC/PDF eller eksporter DXF. Eksempel: {sample_names}."
                )
        else:
            before_dwg = len(drawings)
            for f, name, suffix, file_bytes in grouped["dwg"]:
                drawings.extend(_load_dxf_or_dwg_drawings_v13(name, file_bytes, suffix))
            loaded_dwg = max(0, len(drawings) - before_dwg)
            if loaded_dwg > 0:
                _append_upload_info_v14(f"DWG lest via LibreDWG: {loaded_dwg} tegningsbilde(r) generert i denne kjøringen.")

    for _, name, suffix, _ in grouped["other"]:
        _append_upload_warning_v13(f"Filtypen '{suffix or name}' støttes ikke i denne versjonen.")

    drawings = prioritize_drawings(drawings, limit=18)
    for idx, record in enumerate(drawings):
        record["page_index"] = idx

    if not drawings and files:
        _append_upload_warning_v13("Ingen brukbare tegninger kunne genereres fra opplastingen. Kontroller at IFC/DXF-pakker er installert i miljøet eller bruk PDF som fallback.")
    return drawings


def drawing_priority(record: Dict[str, Any]) -> int:
    score = _DRAWING_PRIORITY_V12_BASE(record)
    fmt = clean_pdf_text(record.get("drawing_format", "")).lower()
    if fmt == "ifc":
        score += 40
    elif fmt in {"dxf", "dwg"}:
        score += 18
    if isinstance(record.get("semantic_page_cue_v13"), dict):
        score += 20
    if isinstance(record.get("semantic_geometry_v13"), dict):
        score += 15
    if clean_pdf_text(record.get("ifc_storey_name", "")).lower():
        score += 5
    return score



def prioritize_drawings(drawings: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    ranked = _PRIORITIZE_DRAWINGS_V12_BASE(drawings, limit=max(limit, len(drawings)))
    out = ranked[:limit]
    for idx, record in enumerate(out):
        record["page_index"] = idx
    return out



def drawing_manifest_text(drawings: List[Dict[str, Any]]) -> str:
    if not drawings:
        return _DRAWING_MANIFEST_TEXT_V12_BASE(drawings)
    lines = []
    for record in drawings:
        extra: List[str] = []
        fmt = clean_pdf_text(record.get("drawing_format", ""))
        if fmt:
            extra.append(f"format: {fmt}")
        storey = clean_pdf_text(record.get("ifc_storey_name", ""))
        if storey:
            extra.append(f"etasje: {storey}")
        summary = record.get("semantic_summary_v13") if isinstance(record.get("semantic_summary_v13"), dict) else {}
        if summary:
            extra.append(
                "semantikk: " + ", ".join(
                    f"{key}={summary.get(key)}" for key in ["walls", "columns", "stairs_or_transport", "cores"] if summary.get(key) is not None
                )
            )
        lines.append(
            f"- side_index {record.get('page_index', 0)}: {record.get('name', '')} | kilde: {record.get('source', '')} | hint: {record.get('hint', '')}" + (f" | {' | '.join(extra)}" if extra else "")
        )
    return "\n".join(lines)



def get_plan_regions_for_record_v6(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    semantic_regions = record.get("semantic_plan_regions_v13")
    if isinstance(semantic_regions, list) and semantic_regions:
        return semantic_regions
    cache_key = None
    try:
        cache_key = record.get("image", Image.new("RGB", (1, 1))).info.get("_semantic_geometry_key_v13")
    except Exception:
        cache_key = None
    cached_regions = _V13_IMAGE_REGION_CACHE.get(str(cache_key)) if cache_key else None
    if isinstance(cached_regions, list) and cached_regions:
        return cached_regions
    return _GET_PLAN_REGIONS_FOR_RECORD_V12_BASE(record)



def _merge_page_cues_v13(primary: Dict[str, Any], secondary: Optional[Dict[str, Any]], record: Dict[str, Any]) -> Dict[str, Any]:
    merged = _normalize_page_cue_v9(primary or {}, record)
    other = _normalize_page_cue_v9(secondary or {}, record) if isinstance(secondary, dict) else None
    if not other:
        return merged
    if float(other.get("plan_confidence", 0.0)) > float(merged.get("plan_confidence", 0.0)):
        merged["plan_confidence"] = float(other.get("plan_confidence", 0.0))
    for key in ["plan_bbox", "exterior_envelope_bbox"]:
        if merged.get(key) is None and other.get(key) is not None:
            merged[key] = other.get(key)
    for key in ["core_candidates", "bearing_wall_candidates", "column_candidates"]:
        merged_list = list(merged.get(key, []) or [])
        for item in other.get(key, []) or []:
            if len(merged_list) >= (2 if key == "core_candidates" else 5 if key == "bearing_wall_candidates" else 10):
                break
            merged_list.append(item)
        merged[key] = merged_list
    notes = [clean_pdf_text(item) for item in merged.get("notes", []) if clean_pdf_text(item)]
    for item in other.get("notes", []) or []:
        cleaned = clean_pdf_text(item)
        if cleaned and cleaned not in notes:
            notes.append(cleaned)
    merged["notes"] = notes[:6]
    return merged



def _detect_page_cues_with_openai_v9(model: Any, drawings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    openai_enabled = isinstance(model, dict) and model.get("provider") == "openai"
    for record in drawings[: min(len(drawings), 8)]:
        semantic = record.get("semantic_page_cue_v13") if isinstance(record.get("semantic_page_cue_v13"), dict) else None
        if semantic and clean_pdf_text(record.get("drawing_format", "")).lower() == "ifc":
            cue = _normalize_page_cue_v9(semantic, record)
            record["ai_page_cue_v9"] = cue
            cues.append(cue)
            continue
        ai_cue = None
        if openai_enabled:
            try:
                ai_cue_list = _DETECT_PAGE_CUES_WITH_OPENAI_V12_BASE(model, [record])
                ai_cue = ai_cue_list[0] if ai_cue_list else None
            except Exception:
                ai_cue = None
        cue = _merge_page_cues_v13(semantic or {}, ai_cue, record) if semantic else (_normalize_page_cue_v9(ai_cue or {}, record))
        record["ai_page_cue_v9"] = cue
        cues.append(cue)
    return cues



def build_plan_geometry_grounded(
    image: Image.Image,
    region_bbox_px: Tuple[int, int, int, int],
) -> Dict[str, Any]:
    cache_key = None
    try:
        cache_key = image.info.get("_semantic_geometry_key_v13")
    except Exception:
        cache_key = None
    if cache_key and str(cache_key) in _V13_IMAGE_GEOMETRY_CACHE:
        try:
            return _copy_v13.deepcopy(_V13_IMAGE_GEOMETRY_CACHE[str(cache_key)])
        except Exception:
            return _V13_IMAGE_GEOMETRY_CACHE[str(cache_key)]
    return _BUILD_PLAN_GEOMETRY_GROUNDED_V12_BASE(image, region_bbox_px)



def get_geometry_for_sketch(drawing_record: Dict[str, Any], sketch: Dict[str, Any]) -> Dict[str, Any]:
    semantic_geometry = drawing_record.get("semantic_geometry_v13")
    if isinstance(semantic_geometry, dict):
        try:
            return _copy_v13.deepcopy(semantic_geometry)
        except Exception:
            return semantic_geometry
    return _GET_GEOMETRY_FOR_SKETCH_V12_BASE(drawing_record, sketch)



def _render_upload_warnings_v13() -> None:
    infos = st.session_state.get("rib_upload_infos_v14", [])
    if isinstance(infos, list) and infos:
        for item in infos[:4]:
            st.info(item)
    warnings = st.session_state.get("rib_upload_warnings_v13", [])
    if isinstance(warnings, list) and warnings:
        for item in warnings[:4]:
            st.warning(item)



# ------------------------------------------------------------
# 15. PDF-RELEVANSUTVALG FOR STORE TEGNINGSSETT
# ------------------------------------------------------------
_LOAD_UPLOADED_DRAWINGS_V14_BASE = load_uploaded_drawings
_DRAWING_PRIORITY_V14_BASE = drawing_priority
_PRIORITIZE_DRAWINGS_V14_BASE = prioritize_drawings

_PDF_ROLE_KEYWORDS_V15: Dict[str, List[Tuple[str, int]]] = {
    "plan": [
        ("plantegning", 28), ("etasjeplan", 28), ("plan", 20), ("typisk etasje", 24),
        ("typisk plan", 22), ("level", 16), ("nivå", 16), ("floor plan", 26),
        ("floor", 10), ("etasje", 14), ("etg", 12), ("kjeller", 16), ("u. etg", 16),
        ("underetasje", 16), ("takplan", 18), ("roof plan", 18), ("loft", 10),
    ],
    "section": [
        ("snitt", 24), ("section", 22), ("schnitt", 18), ("cut", 12), ("longitudinal", 8),
    ],
    "facade": [
        ("fasade", 22), ("facade", 22), ("elevation", 20), ("oppstalt", 16),
    ],
    "site": [
        ("situasjonsplan", 12), ("site plan", 12), ("utomhus", 8), ("landskap", 6),
    ],
}
_PDF_NEGATIVE_KEYWORDS_V15: List[Tuple[str, int]] = [
    ("tegningsliste", -34), ("drawing list", -30), ("innholdsfortegnelse", -34), ("contents", -28),
    ("forside", -24), ("cover", -22), ("legend", -18), ("forklaring", -18), ("symbolforklaring", -18),
    ("detalj", -24), ("detail", -22), ("details", -18), ("dørskjema", -22), ("vindusskjema", -22),
    ("romskjema", -22), ("schedule", -20), ("tabell", -16), ("general notes", -22), ("notes", -14),
    ("beskrivelse", -18), ("prinsipp", -10), ("diagram", -8), ("skjema", -16),
]
_PDF_TOKEN_STOPWORDS_V15 = {
    "plan", "snitt", "fasade", "tegning", "drawing", "project", "prosjekt", "bygg", "block", "blok", "arkitekt",
    "ark", "sheet", "side", "page", "ifc", "dwg", "dxf", "pdf", "etasje", "etg", "level", "nivå", "typisk",
    "builtly", "rib", "modul", "konstruksjon", "modell", "model", "norge", "norway",
}


def _tokenize_context_v15(text: Any) -> List[str]:
    tokens: List[str] = []
    for token in re.findall(r"[A-Za-zÆØÅæøå0-9\-]{4,}", clean_pdf_text(text).lower()):
        normalized = token.strip("-_ ")
        if not normalized or normalized in _PDF_TOKEN_STOPWORDS_V15:
            continue
        if normalized.isdigit() and len(normalized) < 4:
            continue
        if normalized not in tokens:
            tokens.append(normalized)
    return tokens



def _pdf_context_tokens_v15(files: Optional[List[Any]] = None) -> List[str]:
    pieces: List[str] = []
    project_data = safe_session_state_get("project_data", {})
    if isinstance(project_data, dict):
        for key in ["p_name", "adresse", "kommune", "p_desc", "b_type"]:
            pieces.append(clean_pdf_text(project_data.get(key, "")))
    if isinstance(files, list):
        for item in files[:20]:
            pieces.append(clean_pdf_text(getattr(item, "name", "")))
    tokens = _tokenize_context_v15(" ".join(pieces))
    return tokens[:18]



def _count_keyword_hits_v15(text: str, keyword_weights: List[Tuple[str, int]]) -> Tuple[int, List[str]]:
    score = 0
    hits: List[str] = []
    for keyword, weight in keyword_weights:
        if keyword and keyword in text:
            score += int(weight)
            if keyword not in hits:
                hits.append(keyword)
    return score, hits



def _infer_pdf_page_role_v15(file_name: str, page_text: str) -> Tuple[str, Dict[str, int], List[str]]:
    combined = f"{clean_pdf_text(file_name)}\n{clean_pdf_text(page_text)}".lower()
    role_scores: Dict[str, int] = {"plan": 0, "section": 0, "facade": 0, "site": 0}
    reasons: List[str] = []
    for role, keyword_weights in _PDF_ROLE_KEYWORDS_V15.items():
        role_score, hits = _count_keyword_hits_v15(combined, keyword_weights)
        role_scores[role] = role_score
        if hits:
            reasons.append(f"{role}: " + ", ".join(hits[:3]))
    best_role = max(role_scores, key=role_scores.get)
    if role_scores.get(best_role, 0) < 10:
        fallback_role = detect_drawing_hint(file_name)
        if fallback_role in {"plan", "section", "facade"}:
            best_role = fallback_role
    return best_role, role_scores, reasons



def _project_token_score_v15(text: str, tokens: List[str]) -> Tuple[int, List[str]]:
    score = 0
    hits: List[str] = []
    for token in tokens:
        if token and token in text:
            score += 5 if any(ch.isdigit() for ch in token) else 4
            if token not in hits:
                hits.append(token)
    return score, hits[:5]



def _render_pdf_thumb_v15(page: Any, zoom: float = 0.48) -> Optional[Image.Image]:
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    except Exception:
        return None



def _visual_pdf_page_score_v15(page: Any) -> Tuple[int, List[str]]:
    if fitz is None:
        return 0, []
    img = _render_pdf_thumb_v15(page, zoom=0.46)
    if img is None:
        return 0, []
    score = 0
    reasons: List[str] = []
    try:
        regions = detect_plan_regions_grounded(img)
    except Exception:
        regions = []
    valid_ratios: List[float] = []
    for region in regions[:6]:
        try:
            _, _, rw, rh = region.get("bbox_px", (0, 0, 0, 0))
            ratio = float(rw * rh) / float(max(1, img.width * img.height))
        except Exception:
            ratio = 0.0
        if ratio > 0.03:
            valid_ratios.append(ratio)
    if valid_ratios:
        best_ratio = max(valid_ratios)
        region_bonus = min(38, int(best_ratio * 120.0) + min(8, len(valid_ratios) * 2))
        score += region_bonus
        reasons.append(f"planregion={best_ratio:.2f}")
        if len(valid_ratios) >= 2:
            score += 4
            reasons.append("flere delplan")

    try:
        gray = img.convert("L").resize((140, 140))
        hist = gray.histogram()
        total = max(1, int(sum(hist)))
        dark_ratio = float(sum(hist[:190])) / float(total)
        mid_ratio = float(sum(hist[190:245])) / float(total)
        if 0.03 <= dark_ratio <= 0.34 and mid_ratio >= 0.18:
            score += 6
            reasons.append("linjetett planark")
        elif dark_ratio < 0.01:
            score -= 6
    except Exception:
        pass
    return score, reasons



def _score_pdf_page_v15(
    doc: Any,
    page_num: int,
    file_name: str,
    project_tokens: List[str],
    force_visual: bool = False,
) -> Dict[str, Any]:
    page = doc.load_page(page_num)
    try:
        page_text = clean_pdf_text(page.get_text("text"))
    except Exception:
        page_text = ""
    normalized = f"{clean_pdf_text(file_name)}\n{page_text}".lower()
    role, role_scores, role_reasons = _infer_pdf_page_role_v15(file_name, page_text)
    project_score, project_hits = _project_token_score_v15(normalized, project_tokens)
    negative_score, negative_hits = _count_keyword_hits_v15(normalized, _PDF_NEGATIVE_KEYWORDS_V15)

    score = int(role_scores.get(role, 0)) + int(project_score) + int(negative_score)
    reasons: List[str] = []
    reasons.extend(role_reasons[:2])
    if project_hits:
        reasons.append("prosjektmatch: " + ", ".join(project_hits[:3]))
    if negative_hits:
        reasons.append("minus: " + ", ".join(negative_hits[:3]))

    visual_score = 0
    visual_reasons: List[str] = []
    text_len = len(page_text.strip())
    if force_visual:
        visual_score, visual_reasons = _visual_pdf_page_score_v15(page)
        score += int(visual_score)
        reasons.extend(visual_reasons[:2])

    return {
        "page_num": int(page_num),
        "role": clean_pdf_text(role or "unknown") or "unknown",
        "score": int(score),
        "text_len": int(text_len),
        "page_text": short_text(page_text, 2200),
        "role_scores": role_scores,
        "project_hits": project_hits,
        "negative_hits": negative_hits,
        "visual_score": int(visual_score),
        "reasons": reasons[:6],
    }



def _select_pdf_pages_v15(page_infos: List[Dict[str, Any]], target_pages: int) -> List[Dict[str, Any]]:
    if not page_infos:
        return []
    target_pages = max(1, int(target_pages or 4))
    ranked = sorted(page_infos, key=lambda item: (float(item.get("score", 0.0)), float(item.get("visual_score", 0.0))), reverse=True)
    selected: List[Dict[str, Any]] = []
    selected_ids: set = set()

    def _push(candidate: Optional[Dict[str, Any]]) -> None:
        if not candidate:
            return
        page_num = int(candidate.get("page_num", -1))
        if page_num < 0 or page_num in selected_ids or len(selected) >= target_pages:
            return
        selected.append(candidate)
        selected_ids.add(page_num)

    plan_candidates = [item for item in ranked if item.get("role") == "plan" and float(item.get("score", 0.0)) >= 12]
    section_candidates = [item for item in ranked if item.get("role") == "section" and float(item.get("score", 0.0)) >= 10]
    facade_candidates = [item for item in ranked if item.get("role") == "facade" and float(item.get("score", 0.0)) >= 10]

    desired_plans = 2 if target_pages >= 4 else 1
    for candidate in plan_candidates[:desired_plans]:
        _push(candidate)

    if target_pages >= 4:
        _push(section_candidates[0] if section_candidates else None)
        _push(facade_candidates[0] if facade_candidates else None)

    if len(selected) < target_pages:
        for candidate in ranked:
            _push(candidate)
            if len(selected) >= target_pages:
                break

    if not selected:
        selected = sorted(page_infos, key=lambda item: int(item.get("page_num", 0)))[:target_pages]
    return sorted(selected, key=lambda item: int(item.get("page_num", 0)))



def _load_pdf_drawings_relevant_v15(file_name: str, file_bytes: bytes, max_pdf_pages: int = 6, batch_tokens: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    drawings: List[Dict[str, Any]] = []
    if fitz is None:
        _append_upload_warning_v13(f"PDF-filen '{file_name}' kan ikke leses fordi PyMuPDF ('fitz') ikke er tilgjengelig i miljøet.")
        return drawings
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        _append_upload_warning_v13(f"PDF-lesing feilet for '{file_name}': {type(exc).__name__}: {short_text(exc, 180)}")
        return drawings

    page_count = len(doc)
    if page_count <= 0:
        doc.close()
        return drawings

    target_pages = max(4, int(max_pdf_pages or 4)) if page_count > 8 else max(1, int(max_pdf_pages or 4))
    target_pages = min(target_pages, max(1, page_count), 8)
    project_tokens = list(batch_tokens or [])
    for token in _pdf_context_tokens_v15([]):
        if token not in project_tokens:
            project_tokens.append(token)

    page_infos: List[Dict[str, Any]] = []
    for page_num in range(page_count):
        info = _score_pdf_page_v15(doc, page_num, file_name, project_tokens, force_visual=False)
        page_infos.append(info)

    visual_candidate_ids = set()
    if page_count <= 24:
        visual_candidate_ids.update(range(page_count))
    else:
        prelim_ranked = sorted(page_infos, key=lambda item: float(item.get("score", 0.0)), reverse=True)
        for item in prelim_ranked[: min(28, len(prelim_ranked))]:
            visual_candidate_ids.add(int(item.get("page_num", 0)))
        step = max(1, page_count // 18)
        visual_candidate_ids.update(range(0, page_count, step))
        visual_candidate_ids.update({0, page_count - 1, page_count // 2})

    for page_num in sorted(x for x in visual_candidate_ids if 0 <= x < page_count):
        rescored = _score_pdf_page_v15(doc, page_num, file_name, project_tokens, force_visual=True)
        page_infos[page_num] = rescored

    selected_pages = _select_pdf_pages_v15(page_infos, target_pages)
    if not selected_pages:
        doc.close()
        return drawings

    chosen_numbers = [int(item.get("page_num", 0)) + 1 for item in selected_pages]
    role_summary: Dict[str, int] = {}
    for item in selected_pages:
        role = clean_pdf_text(item.get("role", "unknown")) or "unknown"
        role_summary[role] = int(role_summary.get(role, 0)) + 1

    for item in selected_pages:
        page_num = int(item.get("page_num", 0))
        page = doc.load_page(page_num)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        record = build_drawing_record(f"{file_name} - side {page_num + 1}", img, "Opplastet PDF (auto-utvalg)")
        record.update(
            {
                "hint": clean_pdf_text(item.get("role")) or record.get("hint", "unknown"),
                "drawing_format": "pdf",
                "pdf_page_number": page_num + 1,
                "pdf_page_score_v15": float(item.get("score", 0.0)),
                "pdf_selection_reasons_v15": list(item.get("reasons", [])[:4]),
                "pdf_page_text_v15": clean_pdf_text(item.get("page_text", "")),
            }
        )
        drawings.append(record)

    role_bits = ", ".join(f"{role}={count}" for role, count in sorted(role_summary.items()))
    _append_upload_info_v14(
        f"PDF '{file_name}': skannet {page_count} side(r) og valgte side {', '.join(str(x) for x in chosen_numbers)} som mest relevante" + (f" ({role_bits})." if role_bits else ".")
    )
    doc.close()
    return drawings



def drawing_priority(record: Dict[str, Any]) -> int:
    score = _DRAWING_PRIORITY_V14_BASE(record)
    fmt = clean_pdf_text(record.get("drawing_format", "")).lower()
    if fmt == "pdf":
        try:
            score += min(18, int(float(record.get("pdf_page_score_v15", 0.0)) * 0.18))
        except Exception:
            pass
    return score



def prioritize_drawings(drawings: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    ranked = sorted(drawings, key=drawing_priority, reverse=True)
    out = ranked[:limit]
    for idx, record in enumerate(out):
        record["page_index"] = idx
    return out



def load_uploaded_drawings(files: Optional[List[Any]], max_pdf_pages: int = 6) -> List[Dict[str, Any]]:
    drawings: List[Dict[str, Any]] = []
    pdf_files: List[Any] = []
    other_files: List[Any] = []
    file_list = list(files or [])
    batch_tokens = _pdf_context_tokens_v15(file_list)

    for file_obj in file_list:
        name = clean_pdf_text(getattr(file_obj, "name", "ukjent_fil"))
        suffix = Path(name).suffix.lower()
        if suffix == ".pdf":
            pdf_files.append(file_obj)
        else:
            other_files.append(file_obj)

    if other_files:
        drawings.extend(_LOAD_UPLOADED_DRAWINGS_V14_BASE(other_files, max_pdf_pages=max_pdf_pages))
    else:
        st.session_state["rib_upload_warnings_v13"] = []
        st.session_state["rib_upload_infos_v14"] = []

    for file_obj in pdf_files:
        name = clean_pdf_text(getattr(file_obj, "name", "ukjent_fil"))
        file_bytes = _safe_upload_bytes_v13(file_obj)
        if not file_bytes:
            continue
        drawings.extend(_load_pdf_drawings_relevant_v15(name, file_bytes, max_pdf_pages=max_pdf_pages, batch_tokens=batch_tokens))

    drawings = prioritize_drawings(drawings, limit=18)
    for idx, record in enumerate(drawings):
        record["page_index"] = idx

    if not drawings and file_list:
        _append_upload_warning_v13("Ingen brukbare tegninger kunne genereres fra opplastingen. Kontroller IFC/DXF-pakker i miljøet eller bruk PDF som fallback.")
    return drawings



# ------------------------------------------------------------
# 15B. V16 UPLOAD-ROBUSTHET FOR IFC/DWG/ZIP I DEPLOYED STREAMLIT
# ------------------------------------------------------------

import unicodedata as _unicodedata_v16
import zipfile as _zipfile_v16

_SUPPORTED_ARCHIVE_SUFFIXES_V16 = {
    ".ifc", ".ifczip", ".dxf", ".dwg", ".pdf", ".png", ".jpg", ".jpeg", ".webp"
}


def _ascii_safe_filename_v16(name: str) -> str:
    raw = clean_pdf_text(Path(clean_pdf_text(name or "fil")).name) or "fil"
    stem = Path(raw).stem or "fil"
    suffix = Path(raw).suffix.lower()
    try:
        stem_ascii = _unicodedata_v16.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    except Exception:
        stem_ascii = stem
    stem_ascii = re.sub(r"[^A-Za-z0-9._-]+", "_", stem_ascii).strip("._-") or "fil"
    suffix_ascii = re.sub(r"[^A-Za-z0-9.]+", "", suffix).lower()
    if suffix_ascii and not suffix_ascii.startswith("."):
        suffix_ascii = "." + suffix_ascii
    return f"{stem_ascii}{suffix_ascii}" if suffix_ascii else stem_ascii


class _UploadedBytesV16(io.BytesIO):
    def __init__(self, name: str, data: bytes):
        super().__init__(data)
        self.name = name
        self.type = None
        self.size = len(data or b"")


def _expand_archives_v16(files: Optional[List[Any]]) -> List[Any]:
    expanded: List[Any] = []
    used_names: Dict[str, int] = {}
    for file_obj in list(files or []):
        name = clean_pdf_text(getattr(file_obj, "name", "ukjent_fil"))
        suffix = Path(name).suffix.lower()
        if suffix != ".zip":
            expanded.append(file_obj)
            continue
        file_bytes = _safe_upload_bytes_v13(file_obj)
        if not file_bytes:
            _append_upload_warning_v13(f"ZIP-filen '{name}' var tom eller kunne ikke leses.")
            continue
        try:
            with _zipfile_v16.ZipFile(io.BytesIO(file_bytes)) as zf:
                members = [m for m in zf.namelist() if m and not m.endswith("/")]
                extracted_count = 0
                skipped_count = 0
                for member in members:
                    inner_name = Path(member).name
                    inner_suffix = Path(inner_name).suffix.lower()
                    if inner_suffix not in _SUPPORTED_ARCHIVE_SUFFIXES_V16:
                        skipped_count += 1
                        continue
                    try:
                        data = zf.read(member)
                    except Exception:
                        skipped_count += 1
                        continue
                    safe_name = _ascii_safe_filename_v16(inner_name)
                    if not safe_name:
                        safe_name = f"fil_{extracted_count + 1}{inner_suffix}"
                    base_key = safe_name.lower()
                    seq = used_names.get(base_key, 0)
                    used_names[base_key] = seq + 1
                    if seq > 0:
                        safe_name = f"{Path(safe_name).stem}_{seq + 1}{Path(safe_name).suffix}"
                    expanded.append(_UploadedBytesV16(safe_name, data))
                    extracted_count += 1
                if extracted_count > 0:
                    msg = f"ZIP '{name}': pakket ut {extracted_count} støttet fil(er) for videre analyse."
                    if skipped_count:
                        msg += f" {skipped_count} andre fil(er) ble ignorert."
                    _append_upload_info_v14(msg)
                else:
                    _append_upload_warning_v13(
                        f"ZIP-filen '{name}' inneholdt ingen støttede IFC/DXF/DWG/PDF/bildefiler."
                    )
        except Exception as exc:
            _append_upload_warning_v13(f"ZIP-lesing feilet for '{name}': {type(exc).__name__}: {short_text(exc, 180)}")
    return expanded


_LOAD_UPLOADED_DRAWINGS_V15_BASE = load_uploaded_drawings


def load_uploaded_drawings(files: Optional[List[Any]], max_pdf_pages: int = 6) -> List[Dict[str, Any]]:
    st.session_state["rib_upload_warnings_v13"] = []
    st.session_state["rib_upload_infos_v14"] = []
    expanded_files = _expand_archives_v16(files)
    return _LOAD_UPLOADED_DRAWINGS_V15_BASE(expanded_files, max_pdf_pages=max_pdf_pages)



# Synliggjør eventuelle DWG/IFC-varsel i UI like før analyseknappene.
_render_upload_warnings_v13()
backend_status_parts = [
    "IFC=" + ("aktiv" if (_ifcopenshell_v13 is not None and _ifc_geom_v13 is not None) else "mangler pakke"),
    "DXF=" + ("aktiv" if _ezdxf_v13 is not None else "mangler pakke"),
    "DWG-konvertering=" + ("aktiv" if bool(_shutil_v13.which('dwgread')) else "ikke tilgjengelig"),
]
st.caption("CAD-backend: " + " | ".join(backend_status_parts))

action_col1, action_col2 = st.columns(2)
analyze_clicked = action_col1.button(
    "1️⃣ ANALYSER TEGNINGSGRUNNLAG",
    type="primary",
    use_container_width=True,
)
direct_pdf_clicked = action_col2.button(
    "⚡ DIREKTE PDF UTEN MANUELL REDIGERING",
    type="secondary",
    use_container_width=True,
)

if analyze_clicked or direct_pdf_clicked:
    uploaded_drawings = load_uploaded_drawings(files, max_pdf_pages=6) if files else []
    _render_upload_warnings_v13()
    all_drawings = prioritize_drawings(saved_drawings + uploaded_drawings, limit=10)

    if not all_drawings:
        st.error("Fant ingen tegninger å analysere. Last opp minst én plan eller hent tegninger fra Project Setup.")
    else:
        clear_generated_rib_session()
        st.info(f"Klar! Sender totalt {len(all_drawings)} tegninger/bilder til RIB-agenten for vurdering.")
        backend_warning = st.session_state.pop("rib_ai_backend_warning", None)
        if backend_warning:
            st.caption(f"AI-backend: {backend_warning}")

        with st.spinner("🤖 Analyserer tegninger, velger bæresystem og bygger geometri-forankrede konseptskisser..."):
            valid_models = list_available_models()
            valgt_modell = pick_model(valid_models)
            if not valgt_modell:
                st.error("Kunne ikke finne en tilgjengelig AI-modell (OpenAI/Gemini) i miljøet.")
                st.stop()

            model = build_runtime_ai_model(valgt_modell)
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
            analysis_result = replace_analysis_sketches_with_grounded(
                analysis_result=analysis_result,
                drawings=all_drawings,
                material_preference=material_valg,
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

            persist_rib_draft_to_session(
                analysis_result=analysis_result,
                report_text=report_text,
                candidate_df=candidate_df,
                candidates=candidates,
                drawings=all_drawings,
                material_preference=material_valg,
                foundation_preference=fundamentering,
                optimization_mode=optimaliser_for,
                safety_mode=safety_mode,
            )

        if direct_pdf_clicked:
            with st.spinner("Låser auto-skissene og bygger PDF..."):
                finalize_rib_draft_to_pdf()
        st.rerun()




if draft_sketch_bundle_exists():
    render_rib_draft_editor_ui()


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
