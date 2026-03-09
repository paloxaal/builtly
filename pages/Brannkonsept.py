import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
import base64
from datetime import datetime
import tempfile
import re
import requests
import urllib.parse
import io
import json
from PIL import Image, ImageDraw, ImageColor, ImageFont
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Brannkonsept (RIBr) | Builtly", layout="wide", initial_sidebar_state="collapsed")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

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

def clean_pdf_text(text):
    if text is None: return ""
    text = str(text)
    rep = {"–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "•": "-", "≤": "<=", "≥": ">="}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")

def ironclad_text_formatter(text):
    text = clean_pdf_text(text)
    text = text.replace("$", "").replace("*", "").replace("_", "")
    text = re.sub(r"[-|=]{3,}", " ", text)
    text = re.sub(r"([^\s]{40})", r"\1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

# --- 2. PREMIUM CSS (MØRKT TEMA) ---
st.markdown("<style>#MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}</style>", unsafe_allow_html=True)
st.markdown("""
<style>
    :root { --bg: #06111a; --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df; --accent: #e53935; --radius-lg: 16px; }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(229,57,53,0.15)); }
    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }
    
    button[kind="primary"] { background: linear-gradient(135deg, #e53935, #ff5252) !important; color: #ffffff !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(229,57,53,0.3) !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s; }
    button[kind="secondary"]:hover { background-color: rgba(229,57,53,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important;}

    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, div[data-baseweb="select"] * { background-color: transparent !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; border: none !important; box-shadow: none !important; }
    div[data-baseweb="base-input"]:focus-within, div[data-baseweb="select"] > div:focus-within, .stTextArea > div > div > div:focus-within { border-color: var(--accent) !important; box-shadow: 0 0 0 1px rgba(229,57,53, 0.5) !important; }
    ul[data-baseweb="menu"] { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; }
    ul[data-baseweb="menu"] li { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
    ul[data-baseweb="menu"] li:hover { background-color: rgba(229,57,53, 0.1) !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label { color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important; }
    
    div[data-testid="stExpander"] details, div[data-testid="stExpander"] details summary, div[data-testid="stExpander"] { background-color: #0c1520 !important; color: #f5f7fb !important; border-radius: 12px !important; }
    div[data-testid="stExpander"] details summary p { color: #f5f7fb !important; font-weight: 650 !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; margin-bottom: 1rem !important; }
    div[data-testid="stExpanderDetails"] { background: transparent !important; color: #f5f7fb !important; }
    
    [data-testid="stFileUploaderDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important; border-radius: 12px !important; padding: 2rem !important; }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #e53935 !important; background-color: rgba(229,57,53, 0.05) !important; }
    [data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
</style>
""", unsafe_allow_html=True)

# --- 3. DATAKLASSER FOR BRANNTEGNINGER ---
@dataclass
class UploadedSource:
    name: str
    category: str
    pages: List[Image.Image] = field(default_factory=list)

@dataclass
class FireMarkupSpec:
    page_title: str = ""
    notes: List[str] = field(default_factory=list)
    legend: List[Dict[str, str]] = field(default_factory=list)
    elements: List[Dict[str, Any]] = field(default_factory=list)

STYLE_MAP = {
    "dashed_red": {"stroke": "#d32f2f", "fill": None, "width": 4},
    "green_band": {"stroke": "#2e7d32", "fill": "#9be79b", "width": 2},
    "orange_band": {"stroke": "#ef6c00", "fill": "#f6c27a", "width": 2},
    "blue_access": {"stroke": "#1e5cc6", "fill": None, "width": 6},
    "pink_fill": {"stroke": "#d16b8d", "fill": "#f6c0d0", "width": 2},
    "red_arrow": {"stroke": "#d32f2f", "fill": None, "width": 4},
    "green_arrow": {"stroke": "#2e7d32", "fill": None, "width": 4},
    "red_callout": {"stroke": "#c62828", "fill": "#fff7f7", "width": 2},
}

# --- 4. SESSION STATE ---
DB_DIR = Path("qa_database")
SSOT_FILE = DB_DIR / "ssot.json"
IMG_DIR = DB_DIR / "project_images"

if "project_data" not in st.session_state:
    st.session_state.project_data = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": "", "b_type": "Bolig", "etasjer": 4, "bta": 0, "land": "Norge"}
if "brann_kart" not in st.session_state:
    st.session_state.brann_kart = None
if "source_documents" not in st.session_state:
    st.session_state.source_documents = []
if "generated_fire_drawings" not in st.session_state:
    st.session_state.generated_fire_drawings = []

if st.session_state.project_data.get("p_name") == "" and SSOT_FILE.exists():
    with open(SSOT_FILE, "r", encoding="utf-8") as f:
        st.session_state.project_data = json.load(f)

pd_state = st.session_state.project_data

if pd_state.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(f"<div style='margin-bottom:2rem;'>{logo_html}</div>")
    st.warning("⚠️ **Handling kreves:** Du må sette opp prosjektdataen før du kan bruke denne modulen.")
    if find_page("Project"):
        if st.button("⚙️ Gå til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()

# --- 5. HJELPEFUNKSJONER (PDF TIL BILDE) ---
def pdf_to_images(pdf_bytes: bytes, limit: int = 3, scale: float = 1.8) -> List[Image.Image]:
    images = []
    if fitz is None: return images
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_idx in range(min(limit, len(doc))):
            pix = doc.load_page(page_idx).get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            images.append(image)
        doc.close()
    except Exception: pass
    return images

def ingest_streamlit_files(files) -> List[UploadedSource]:
    documents = []
    for file in files:
        raw = file.read()
        name = file.name
        pages = []
        if name.lower().endswith(".pdf"):
            pages = pdf_to_images(raw, limit=4)
        else:
            try: pages = [Image.open(io.BytesIO(raw)).convert("RGB")]
            except Exception: pass
        if pages:
            documents.append(UploadedSource(name=name, category="Tegningsgrunnlag", pages=pages))
    return documents

def fetch_map_image(adresse, kommune, gnr, bnr, api_key):
    nord, ost = None, None
    adr_clean = adresse.replace(',', '').strip() if adresse else ""
    kom_clean = kommune.replace(',', '').strip() if kommune else ""
    queries = []
    if adr_clean and kom_clean: queries.append(f"{adr_clean} {kom_clean}")
    if adr_clean: queries.append(adr_clean)
    if gnr and bnr and kom_clean: queries.append(f"{kom_clean} {gnr}/{bnr}")
    for q in queries:
        safe_query = urllib.parse.quote(q)
        url = f"https://ws.geonorge.no/adresser/v1/sok?sok={safe_query}&fuzzy=true&utkoordsys=25833&treffPerSide=1"
        try:
            resp = requests.get(url, timeout=4)
            if resp.status_code == 200 and resp.json().get('adresser'):
                hit = resp.json()['adresser'][0]
                nord, ost = hit.get('representasjonspunkt', {}).get('nord'), hit.get('representasjonspunkt', {}).get('øst')
                break
        except: pass
    if nord and ost:
        min_x, max_x = float(ost) - 150, float(ost) + 150
        min_y, max_y = float(nord) - 150, float(nord) + 150
        url_orto = f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
        try:
            r1 = requests.get(url_orto, timeout=5)
            if r1.status_code == 200 and len(r1.content) > 5000:
                return Image.open(io.BytesIO(r1.content)).convert('RGB'), "Kartverket (Norge i Bilder)"
        except: pass
    return None, "Kunne ikke hente kart."

# --- 6. AI TEGNINGS-MOTOR (MAGIEN) ---
def _safe_color(value: Optional[str], fallback: str = "#000000") -> Tuple[int, int, int]:
    try: return ImageColor.getrgb(value or fallback)
    except Exception: return ImageColor.getrgb(fallback)

def draw_dashed_line(draw, p1, p2, fill, width, dash=10, gap=6):
    x1, y1 = p1
    x2, y2 = p2
    length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1.0)
    dx, dy = (x2 - x1) / length, (y2 - y1) / length
    position = 0.0
    while position < length:
        start = position
        end = min(position + dash, length)
        s = (int(x1 + dx * start), int(y1 + dy * start))
        e = (int(x1 + dx * end), int(y1 + dy * end))
        draw.line([s, e], fill=fill, width=width)
        position += dash + gap

def draw_arrow(draw, p1, p2, fill, width):
    draw.line([p1, p2], fill=fill, width=width)
    x1, y1 = p1
    x2, y2 = p2
    vx, vy = x2 - x1, y2 - y1
    length = max((vx * vx + vy * vy) ** 0.5, 1.0)
    ux, uy = vx / length, vy / length
    wing, back = 16, 18
    left = (int(x2 - ux * back - uy * wing), int(y2 - uy * back + ux * wing))
    right = (int(x2 - ux * back + uy * wing), int(y2 - uy * back - ux * wing))
    draw.polygon([p2, left, right], fill=fill)

def render_fire_overlay(source_image: Image.Image, spec: FireMarkupSpec) -> Image.Image:
    canvas = source_image.convert("RGBA")
    overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    for element in spec.elements:
        style_key = element.get("style", "dashed_red")
        style = STYLE_MAP.get(style_key, STYLE_MAP["dashed_red"])
        stroke = _safe_color(style.get("stroke"), "#000000")
        fill = style.get("fill")
        fill_rgba = (_safe_color(fill)[0], _safe_color(fill)[1], _safe_color(fill)[2], 110) if fill else None
        width = int(style.get("width", 3))
        etype = element.get("type")

        if etype == "line" and len(element.get("points", [])) >= 2:
            points = element["points"]
            for idx in range(len(points) - 1):
                p1, p2 = tuple(map(int, points[idx])), tuple(map(int, points[idx + 1]))
                if style_key == "dashed_red": draw_dashed_line(draw, p1, p2, stroke, width)
                else: draw.line([p1, p2], fill=stroke, width=width)
        elif etype == "polyline" and len(element.get("points", [])) >= 2:
            points = [tuple(map(int, p)) for p in element["points"]]
            draw.line(points, fill=stroke, width=width)
        elif etype == "arrow" and len(element.get("points", [])) == 2:
            draw_arrow(draw, tuple(map(int, element["points"][0])), tuple(map(int, element["points"][1])), stroke, width)
        elif etype == "rect" and len(element.get("rect", [])) == 4:
            x, y, w, h = map(int, element["rect"])
            draw.rectangle([x, y, x + w, y + h], outline=stroke, width=width, fill=fill_rgba)
        elif etype == "note" and len(element.get("at", [])) == 2:
            x, y = map(int, element["at"])
            text = str(element.get("text", "Merknad"))
            w = max(200, min(360, 8 * len(text)))
            draw.rectangle([x, y, x + w, y + 44], outline=stroke, width=2, fill=(255, 255, 255, 225))
            draw.text((x + 8, y + 12), text, fill=stroke)

        if label := element.get("label"):
            if etype == "rect" and len(element.get("rect", [])) == 4:
                x, y = int(element["rect"][0]), int(element["rect"][1])
                draw.text((x + 6, y - 18), str(label), fill=stroke)
            elif etype in {"line", "polyline", "arrow"} and element.get("points"):
                x, y = map(int, element["points"][0])
                draw.text((x + 6, y + 6), str(label), fill=stroke)

    return Image.alpha_composite(canvas, overlay).convert("RGB")

def try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if not text: return None
    fence_match = re.search(r"
http://googleusercontent.com/immersive_entry_chip/0
