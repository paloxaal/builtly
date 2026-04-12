import base64
import warnings
import io
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from fpdf import FPDF
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

try:
    import google.generativeai as genai
except Exception:
    genai = None

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

# --- Auth integration (for saving reports to user account) ---
try:
    import builtly_auth
    _HAS_AUTH = True
except ImportError:
    _HAS_AUTH = False

if _HAS_AUTH:
    if not st.session_state.get("user_authenticated"):
        builtly_auth.try_restore_from_browser()
    elif st.session_state.get("_sb_access_token"):
        builtly_auth.restore_session()



# -----------------------------------------------------------------------------
# 1. APP OPPSETT
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Brannkonsept (RIBr) | Builtly",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DB_DIR = Path("qa_database")
IMG_DIR = DB_DIR / "project_images"
SSOT_FILE = DB_DIR / "ssot.json"
DB_DIR.mkdir(exist_ok=True)
IMG_DIR.mkdir(exist_ok=True)

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
AI_AVAILABLE = bool(GOOGLE_API_KEY and genai is not None)
if AI_AVAILABLE:
    genai.configure(api_key=GOOGLE_API_KEY)


# -----------------------------------------------------------------------------
# 2. HJELPEFUNKSJONER
# -----------------------------------------------------------------------------
def render_html(html_string: str) -> None:
    st.markdown(html_string.replace("\n", " "), unsafe_allow_html=True)


def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        path = Path(candidate)
        if path.exists():
            suffix = path.suffix.lower().replace(".", "") or "png"
            return f"data:image/{suffix};base64,{base64.b64encode(path.read_bytes()).decode('utf-8')}"
    return ""


def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        page_path = Path(f"pages/{name}.py")
        if page_path.exists():
            return str(page_path)
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
        "\u00ad": "",
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


def pick_model_name() -> str:
    if not AI_AVAILABLE:
        return ""
    try:
        valid_models = [m.name for m in genai.list_models() if "generateContent" in getattr(m, "supported_generation_methods", [])]
        for preferred in ["models/gemini-1.5-pro", "models/gemini-1.5-flash", "models/gemini-pro"]:
            if preferred in valid_models:
                return preferred
        return valid_models[0]
    except Exception:
        return "models/gemini-1.5-flash"


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def as_pdf_bytes(pdf_obj: FPDF) -> bytes:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        raw = pdf_obj.output()
    if isinstance(raw, str):
        return raw.encode("latin-1")
    return bytes(raw)


def request_rerun() -> None:
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass


def safe_temp_image(img: Image.Image, suffix: str = ".png") -> str:
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    img.save(temp.name)
    temp.close()
    return temp.name


def is_subheading_line(line: str) -> bool:
    clean = line.strip()
    if not clean:
        return False
    if clean.startswith("##") or clean.startswith("###"):
        return True
    if clean.endswith(":") and len(clean) < 90 and len(clean.split()) <= 9:
        return True
    if clean == clean.upper() and any(ch.isalpha() for ch in clean) and len(clean) < 75:
        return True
    return False


def is_bullet_line(line: str) -> bool:
    return bool(re.match(r"^([-*•]|\d+\.)\s+", line.strip()))


def strip_bullet(line: str) -> str:
    return re.sub(r"^([-*•]|\d+\.)\s+", "", line.strip())


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


def sanitize_report_text(text: Any) -> str:
    value = clean_pdf_text(text or "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"```(?:markdown|md|txt)?", "", value, flags=re.I)
    value = value.replace("```", "")
    value = value.replace("**", "").replace("__", "")
    value = re.sub(r"^\s*###\s*", "## ", value, flags=re.M)
    value = re.sub(r"^\s*##(?!#)\s*", "## ", value, flags=re.M)
    value = re.sub(r"^\s*#(?!#)\s*", "# ", value, flags=re.M)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def parse_report_blocks(report_text: str) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    paragraph_lines: List[str] = []
    bullet_lines: List[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            paragraph = ironclad_text_formatter(" ".join(paragraph_lines))
            if paragraph:
                blocks.append({"type": "paragraph", "text": paragraph})
            paragraph_lines = []

    def flush_bullets() -> None:
        nonlocal bullet_lines
        if bullet_lines:
            items = [ironclad_text_formatter(item) for item in bullet_lines if ironclad_text_formatter(item)]
            if items:
                blocks.append({"type": "bullets", "items": items})
            bullet_lines = []

    for raw_line in sanitize_report_text(report_text).splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_bullets()
            continue
        if line.startswith("# "):
            flush_paragraph()
            flush_bullets()
            blocks.append({"type": "heading", "level": 1, "text": line[2:].strip()})
            continue
        if line.startswith("## "):
            flush_paragraph()
            flush_bullets()
            blocks.append({"type": "heading", "level": 2, "text": line[3:].strip()})
            continue
        if is_bullet_line(line):
            flush_paragraph()
            bullet_lines.append(strip_bullet(line))
            continue
        if is_subheading_line(line) and not line.endswith("."):
            flush_paragraph()
            flush_bullets()
            blocks.append({"type": "heading", "level": 2, "text": line.replace(":", "").strip()})
            continue
        paragraph_lines.append(line)

    flush_paragraph()
    flush_bullets()
    return blocks


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
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


def wrap_text_px(text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    text = clean_pdf_text(text)
    if not text:
        return [""]
    words = text.split()
    if not words:
        return [""]
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.getbbox(candidate)[2] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines or [""]


def extract_response_text(response: Any) -> str:
    try:
        return response.text or ""
    except Exception:
        pass
    parts: List[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", "")
            if part_text:
                parts.append(part_text)
    return "\n".join(parts)


def try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned)
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    match = re.search(r"(\{.*\})", cleaned, re.S)
    if match:
        snippet = match.group(1)
        try:
            return json.loads(snippet)
        except Exception:
            return None
    return None


def dominant_lightness_score(image: Image.Image, crop_box: Tuple[int, int, int, int]) -> float:
    gray = image.convert("L").crop(crop_box)
    stat = list(gray.getdata())
    if not stat:
        return 0.0
    return float(sum(stat)) / (255.0 * len(stat))


# -----------------------------------------------------------------------------
# 3. SESSION STATE
# -----------------------------------------------------------------------------
DEFAULT_PROJECT_DATA = {
    "p_name": "",
    "c_name": "",
    "p_desc": "",
    "adresse": "",
    "kommune": "",
    "gnr": "",
    "bnr": "",
    "b_type": "Bolig",
    "etasjer": 1,
    "bta": 0,
    "land": "Norge",
}

if "project_data" not in st.session_state:
    st.session_state.project_data = DEFAULT_PROJECT_DATA.copy()
if "brann_source_pages" not in st.session_state:
    st.session_state.brann_source_pages = []
if "brann_reference_docs" not in st.session_state:
    st.session_state.brann_reference_docs = []
if "brann_analyses" not in st.session_state:
    st.session_state.brann_analyses = []
if "generated_fire_drawings_pdf" not in st.session_state:
    st.session_state.generated_fire_drawings_pdf = None
if "generated_pdf" not in st.session_state:
    st.session_state.generated_pdf = None
if "generated_report_text" not in st.session_state:
    st.session_state.generated_report_text = ""
if "brann_manual_edits_dirty" not in st.session_state:
    st.session_state.brann_manual_edits_dirty = False

if st.session_state.project_data.get("p_name") == "" and SSOT_FILE.exists():
    try:
        st.session_state.project_data = json.loads(SSOT_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass

pd_state = st.session_state.project_data

if pd_state.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else "<h2 style='margin:0; color:white;'>Builtly</h2>"
    render_html(f"<div style='margin-bottom:2rem;'>{logo_html}</div>")
    st.warning("⚠️ **Handling kreves:** Du må sette opp prosjektdataen før du kan bruke denne modulen.")
    target_page = find_page("Project")
    if target_page and st.button("⚙️ Gå til Project Setup", type="primary"):
        st.switch_page(target_page)
    st.stop()


# -----------------------------------------------------------------------------
# 4. DATAKLASSER OG DOMENEKONSTANTER
# -----------------------------------------------------------------------------
@dataclass
class SourcePage:
    uid: str
    doc_name: str
    page_index: int
    page_number: int
    page_title: str
    page_kind: str
    image: Image.Image
    raw_text: str = ""
    keyword_hits: Dict[str, List[List[float]]] = field(default_factory=dict)
    analysis: Dict[str, Any] = field(default_factory=dict)


PAGE_KIND_LABELS = {
    "site_plan": "Utomhusplan / situasjonsplan",
    "parking_plan": "P-kjeller / parkeringsplan",
    "residential_floor_plan": "Boligplan",
    "general_plan": "Plantegning",
    "section": "Snitt / ikke prioritert for overlay",
    "unknown": "Uklassifisert",
}

DRAWABLE_PAGE_KINDS = {"site_plan", "parking_plan", "residential_floor_plan", "general_plan"}

SEARCH_TERMS = [
    "Trapp",
    "Trapp/ heis",
    "heis",
    "Sluse",
    "Inngang",
    "Innkjøring",
    "Parkering",
    "Balkong",
    "Terrasse",
    "Oppstilling",
    "oppstilling brann",
    "atkomst brann",
    "brann",
    "Korridor",
    "Teknisk",
    "Rømningsvei",
]

DEFAULT_LEGENDS = {
    "site_plan": [
        {"label": "Kjørbar atkomst brannvesen", "style": "fire_access"},
        {"label": "Oppstillingsplass", "style": "staging_area"},
        {"label": "Innsatsvei", "style": "attack_route"},
    ],
    "parking_plan": [
        {"label": "Branncelle / brannskille", "style": "fire_compartment"},
        {"label": "Rømningsvei", "style": "escape_route"},
        {"label": "Rømningsretning", "style": "escape_arrow"},
        {"label": "Innsatsvei brannvesen", "style": "attack_route"},
    ],
    "residential_floor_plan": [
        {"label": "Branncelle EI 60", "style": "fire_compartment"},
        {"label": "Rømningsvei", "style": "escape_route"},
        {"label": "Rømningsretning", "style": "escape_arrow"},
        {"label": "Redningsvei / tilgjengelig balkong", "style": "rescue_route"},
        {"label": "Innsatsvei brannvesen", "style": "attack_route"},
    ],
    "general_plan": [
        {"label": "Brannteknisk markering", "style": "fire_compartment"},
        {"label": "Rømningsvei", "style": "escape_route"},
    ],
}

FIRE_STYLE_LIBRARY = {
    "fire_compartment": {"stroke": "#e53935", "fill": None, "width": 5, "dash": (10, 7), "text": "#e53935", "alpha": 255},
    "escape_route": {"stroke": "#2e7d32", "fill": "#6ee7b7", "width": 26, "text": "#14532d", "alpha": 110},
    "escape_arrow": {"stroke": "#22c55e", "fill": None, "width": 5, "text": "#166534", "alpha": 255},
    "rescue_route": {"stroke": "#f59e0b", "fill": "#fdba74", "width": 24, "text": "#92400e", "alpha": 120},
    "attack_route": {"stroke": "#ef4444", "fill": None, "width": 5, "text": "#b91c1c", "alpha": 255},
    "fire_access": {"stroke": "#2563eb", "fill": "#dbeafe", "width": 28, "text": "#1d4ed8", "alpha": 105, "stripe": "#2563eb"},
    "staging_area": {"stroke": "#ef4444", "fill": "#fca5a5", "width": 3, "text": "#b91c1c", "alpha": 120},
    "note_box": {"stroke": "#94a3b8", "fill": "#ffffff", "width": 2, "text": "#0f172a", "alpha": 245},
    "door_class": {"stroke": "#ef4444", "fill": "#ffffff", "width": 2, "text": "#ef4444", "alpha": 250},
    "core_fill": {"stroke": "#2e7d32", "fill": "#bbf7d0", "width": 2, "text": "#14532d", "alpha": 100},
}


# -----------------------------------------------------------------------------
# 5. DOKUMENTPARSING
# -----------------------------------------------------------------------------
def guess_page_kind(raw_text: str, doc_name: str) -> str:
    haystack = f"{doc_name} {raw_text}".lower()

    parking_tokens = ["p-kjeller", "parkeringskjeller", "parkering", "parkerings", "kjeller", "rampe", "innkjøring", "hc"]
    residential_tokens = ["typisk etasjeplan", "etasjeplan", "level ", "balkong", "terrasse", "leilighet", "boenhet", "stue/ kj", "stue/kjøkken"]
    site_tokens = ["situasjonsplan", "utomhusplan", "landskapsplan", "oppstilling brann", "atkomst brann"]

    if any(token in haystack for token in parking_tokens):
        return "parking_plan"
    if any(token in haystack for token in residential_tokens):
        return "residential_floor_plan"
    if "snitt" in haystack:
        return "section"
    if any(token in haystack for token in site_tokens):
        return "site_plan"
    if "plan" in haystack:
        return "general_plan"
    return "unknown"


def guess_page_title(raw_text: str, doc_name: str, page_index: int) -> str:
    title_candidates: List[str] = []
    for line in raw_text.splitlines():
        clean = ironclad_text_formatter(line)
        low = clean.lower()
        if 4 <= len(clean) <= 90 and any(
            token in low
            for token in [
                "etasjeplan",
                "typisk etasjeplan",
                "plan ",
                "p-kjeller",
                "landskapsplan",
                "utomhusplan",
                "situasjonsplan",
                "level ",
            ]
        ):
            title_candidates.append(clean)
    if title_candidates:
        unique: List[str] = []
        for item in title_candidates:
            if item not in unique:
                unique.append(item)
        if len(unique) >= 2 and len(unique[0]) < 38 and len(unique[1]) < 50:
            return f"{unique[0]} - {unique[1]}"
        return unique[0]
    return f"{Path(doc_name).stem} - side {page_index + 1}"


def rect_to_norm(rect: Any, page_rect: Any) -> List[float]:
    return [
        round(clamp01(rect.x0 / max(page_rect.width, 1)), 4),
        round(clamp01(rect.y0 / max(page_rect.height, 1)), 4),
        round(clamp01(rect.x1 / max(page_rect.width, 1)), 4),
        round(clamp01(rect.y1 / max(page_rect.height, 1)), 4),
    ]


def extract_keyword_hits(page: Any) -> Dict[str, List[List[float]]]:
    hits: Dict[str, List[List[float]]] = {}
    for term in SEARCH_TERMS:
        try:
            rects = page.search_for(term)
        except Exception:
            rects = []
        normalized = [rect_to_norm(rect, page.rect) for rect in rects[:8]]
        if normalized:
            hits[term] = normalized
    return hits


def pdf_to_source_pages(pdf_bytes: bytes, doc_name: str, scale: float = 2.2) -> List[SourcePage]:
    pages: List[SourcePage] = []
    if fitz is None:
        return pages
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for idx in range(len(doc)):
            page = doc.load_page(idx)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            raw_text = page.get_text("text")
            pages.append(
                SourcePage(
                    uid=f"{doc_name}::{idx}",
                    doc_name=doc_name,
                    page_index=idx,
                    page_number=idx + 1,
                    page_title=guess_page_title(raw_text, doc_name, idx),
                    page_kind=guess_page_kind(raw_text, doc_name),
                    image=image,
                    raw_text=raw_text,
                    keyword_hits=extract_keyword_hits(page),
                )
            )
    finally:
        doc.close()
    return pages


def image_to_source_page(image_bytes: bytes, doc_name: str) -> List[SourcePage]:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return [
        SourcePage(
            uid=f"{doc_name}::0",
            doc_name=doc_name,
            page_index=0,
            page_number=1,
            page_title=Path(doc_name).stem,
            page_kind=guess_page_kind("", doc_name),
            image=image,
            raw_text="",
            keyword_hits={},
        )
    ]


def load_uploaded_pages(uploaded_files: Iterable[Any]) -> List[SourcePage]:
    collected: List[SourcePage] = []
    for uploaded in uploaded_files or []:
        raw = uploaded.read()
        file_name = uploaded.name
        if file_name.lower().endswith(".pdf"):
            collected.extend(pdf_to_source_pages(raw, file_name))
        else:
            collected.extend(image_to_source_page(raw, file_name))
    return collected


def build_source_register(pages: List[SourcePage]) -> pd.DataFrame:
    rows = []
    for page in pages:
        rows.append(
            {
                "Dokument": clean_pdf_text(page.doc_name),
                "Side": page.page_number,
                "Tegningstype": PAGE_KIND_LABELS.get(page.page_kind, page.page_kind),
                "Tittel": clean_pdf_text(page.page_title),
                "Teksttreff": sum(len(v) for v in page.keyword_hits.values()),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["Dokument", "Side", "Tegningstype", "Tittel", "Teksttreff"])
    return pd.DataFrame(rows)


def rect_iou(a: List[float], b: List[float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter_x0 = max(ax0, bx0)
    inter_y0 = max(ay0, by0)
    inter_x1 = min(ax1, bx1)
    inter_y1 = min(ay1, by1)
    inter_w = max(0.0, inter_x1 - inter_x0)
    inter_h = max(0.0, inter_y1 - inter_y0)
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    denom = area_a + area_b - inter
    return inter / denom if denom > 0 else 0.0



def dedupe_rects(rects: List[List[float]], max_items: int = 6, iou_threshold: float = 0.4) -> List[List[float]]:
    unique: List[List[float]] = []
    for rect in rects:
        clean = [round(clamp01(float(v)), 4) for v in rect[:4]]
        if any(rect_iou(clean, seen) >= iou_threshold for seen in unique):
            continue
        unique.append(clean)
        if len(unique) >= max_items:
            break
    return unique



def hits_for_terms(page: SourcePage, terms: List[str], max_items: int = 5) -> List[List[float]]:
    rects: List[List[float]] = []
    for term in terms:
        rects.extend(page.keyword_hits.get(term, []))
    return dedupe_rects(rects, max_items=max_items)



def resolve_page_kind_for_page(page: SourcePage, proposed_kind: str, title: str = "") -> str:
    haystack = f"{page.doc_name} {page.raw_text} {title}".lower()
    parking_tokens = ["p-kjeller", "parkeringskjeller", "parkering", "parkerings", "kjeller", "rampe", "innkjøring", "hc"]
    residential_tokens = ["typisk etasjeplan", "etasjeplan", "level ", "balkong", "terrasse", "leilighet", "boenhet"]
    site_tokens = ["situasjonsplan", "utomhusplan", "landskapsplan", "oppstilling brann", "atkomst brann"]

    if any(token in haystack for token in parking_tokens):
        return "parking_plan"
    if any(token in haystack for token in residential_tokens):
        return "residential_floor_plan"
    if "snitt" in haystack:
        return "section"
    if any(token in haystack for token in site_tokens):
        return "site_plan"
    return proposed_kind if proposed_kind in PAGE_KIND_LABELS else guess_page_kind(page.raw_text, page.doc_name)



def make_ai_detail_sheet(image: Image.Image, title: str = "") -> Image.Image:
    base = image.convert("RGB")
    w, h = base.size
    panel_w = 560
    panel_h = 360
    gap = 18
    pad = 18
    header_h = 72

    crops = [
        ("Helhet", base),
        ("Venstre halvdel", base.crop((0, 0, max(1, w // 2), h))),
        ("Høyre halvdel", base.crop((max(0, w // 2), 0, w, h))),
        ("Øvre del", base.crop((0, 0, w, max(1, h // 2)))),
        ("Nedre del", base.crop((0, max(0, h // 2), w, h))),
        ("Tittelfelt / høyre nedre", base.crop((max(0, int(w * 0.56)), max(0, int(h * 0.58)), w, h))),
    ]

    canvas_w = pad * 2 + panel_w * 2 + gap
    canvas_h = pad * 2 + header_h + panel_h * 3 + gap * 2
    canvas = Image.new("RGB", (canvas_w, canvas_h), (247, 249, 251))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((8, 8, canvas_w - 8, canvas_h - 8), radius=18, outline=(214, 219, 225), width=2, fill=(247, 249, 251))
    title_font = get_font(28, bold=True)
    sub_font = get_font(16, bold=False)
    draw.text((pad, 18), clean_pdf_text(title or "Detaljark for tegningsanalyse"), font=title_font, fill=(20, 28, 38))
    draw.text((pad, 48), "Helbilde og zoomede utsnitt for a hjelpe AI med a lese romnavn, tittelfelt og brannrelevante detaljer.", font=sub_font, fill=(92, 101, 112))

    label_font = get_font(18, bold=True)
    for idx, (label, crop) in enumerate(crops):
        row = idx // 2
        col = idx % 2
        x = pad + col * (panel_w + gap)
        y = pad + header_h + row * (panel_h + gap)
        fitted = crop.copy()
        ratio = min((panel_w - 16) / max(fitted.width, 1), (panel_h - 46) / max(fitted.height, 1))
        new_w = max(1, int(fitted.width * ratio))
        new_h = max(1, int(fitted.height * ratio))
        fitted = fitted.resize((new_w, new_h))
        draw.rounded_rectangle((x, y, x + panel_w, y + panel_h), radius=14, fill=(255, 255, 255), outline=(214, 219, 225), width=2)
        draw.text((x + 12, y + 10), clean_pdf_text(label), font=label_font, fill=(36, 50, 72))
        paste_x = x + (panel_w - new_w) // 2
        paste_y = y + 38 + (panel_h - 46 - new_h) // 2
        canvas.paste(fitted, (paste_x, paste_y))
    return canvas



def image_to_data_url(image: Image.Image, max_width: int = 1400, quality: int = 88) -> str:
    preview = image.convert("RGB")
    if preview.width > max_width:
        ratio = max_width / max(preview.width, 1)
        preview = preview.resize((int(preview.width * ratio), int(preview.height * ratio)))
    buffer = io.BytesIO()
    preview.save(buffer, format="JPEG", quality=quality)
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"



# -----------------------------------------------------------------------------
# 6. HEURISTIKK FOR BRANNMARKERING
# -----------------------------------------------------------------------------
def expand_norm_rect(rect: List[float], pad_x: float = 0.012, pad_y: float = 0.012) -> List[float]:
    x0, y0, x1, y1 = rect
    return [clamp01(x0 - pad_x), clamp01(y0 - pad_y), clamp01(x1 + pad_x), clamp01(y1 + pad_y)]


def rect_center(rect: List[float]) -> Tuple[float, float]:
    return ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)


def create_box_element(rect: List[float], style: str, label: str = "") -> Dict[str, Any]:
    return {"type": "box", "rect": expand_norm_rect(rect), "style": style, "label": label}


def create_fill_element(rect: List[float], style: str, label: str = "") -> Dict[str, Any]:
    return {"type": "area_fill", "rect": expand_norm_rect(rect, 0.01, 0.01), "style": style, "label": label}


def create_margin_arrow(rect: List[float], style: str, label: str = "") -> Dict[str, Any]:
    cx, cy = rect_center(rect)
    if cx < 0.33:
        start = [clamp01(cx - 0.08), cy]
    elif cx > 0.66:
        start = [clamp01(cx + 0.08), cy]
    else:
        start = [cx, clamp01(cy - 0.08)]
    return {"type": "arrow", "points": [start, [cx, cy]], "style": style, "label": label}


def heuristic_markup_for_page(page: SourcePage, brann_data: Dict[str, Any]) -> Dict[str, Any]:
    page_kind = resolve_page_kind_for_page(page, page.page_kind, page.page_title)
    elements: List[Dict[str, Any]] = []
    notes: List[str] = []
    assumptions: List[str] = []

    if page_kind == "site_plan":
        for rect in hits_for_terms(page, ["oppstilling brann", "Oppstilling"], max_items=2):
            elements.append(create_fill_element(rect, "staging_area", "Oppstillingsplass"))
        for rect in hits_for_terms(page, ["atkomst brann", "Innkjøring", "Inngang"], max_items=2):
            elements.append(create_margin_arrow(rect, "attack_route", "Innsatsvei"))
        for rect in hits_for_terms(page, ["atkomst brann", "Innkjøring"], max_items=2):
            elements.append(create_box_element(rect, "fire_access", "Brannatkomst"))
        notes.append("Situasjons-/utomhusplan er markert med sannsynlig brannatkomst, innsatsretning og eventuelle oppstillingsflater som kan leses ut av tegningsgrunnlaget.")
        assumptions.append("Brannvesenets oppstillingsplass og kjørbar atkomst ma verifiseres mot endelig utomhusplan og krav til bæreevne/snuareal.")

    elif page_kind == "parking_plan":
        for rect in hits_for_terms(page, ["Trapp", "Trapp/ heis", "heis"], max_items=3):
            elements.append(create_box_element(rect, "fire_compartment", "Trapperom / kjerne"))
            elements.append(create_margin_arrow(rect, "escape_arrow", "Rømningsretning"))
        for rect in hits_for_terms(page, ["Sluse"], max_items=2):
            elements.append(create_box_element(rect, "fire_compartment", "Brannsluse"))
        for rect in hits_for_terms(page, ["Korridor"], max_items=2):
            elements.append(create_fill_element(rect, "escape_route", "Rømningsvei"))
        for rect in hits_for_terms(page, ["Inngang", "Innkjøring"], max_items=2):
            elements.append(create_margin_arrow(rect, "attack_route", "Innsatsvei"))
        notes.append("P-kjeller er markert med trapperom, mulig brannsluse, rømningsretning og sannsynlig innsatsretning der dette er synlig i underlaget.")
        assumptions.append("Innsatsvei i p-kjeller og eventuell brannsluse/branngardin ma bekreftes mot detaljprosjektert RIBr-underlag.")

    elif page_kind == "residential_floor_plan":
        for rect in hits_for_terms(page, ["Trapp", "Trapp/ heis", "heis"], max_items=3):
            elements.append(create_box_element(rect, "fire_compartment", "Trapperom / kjerne"))
            elements.append(create_margin_arrow(rect, "escape_arrow", "Rømningsretning"))
        for rect in hits_for_terms(page, ["Korridor"], max_items=2):
            elements.append(create_fill_element(rect, "escape_route", "Rømningsvei"))
        for rect in hits_for_terms(page, ["Balkong", "Terrasse"], max_items=3):
            elements.append(create_fill_element(rect, "rescue_route", "Redningsmulighet"))
        for rect in hits_for_terms(page, ["Inngang"], max_items=2):
            elements.append(create_margin_arrow(rect, "attack_route", "Innsatsvei"))
        notes.append("Boligplan er markert med trapperom/kjerne, rømningsretning, sannsynlig rømningsvei og representative balkonger/terrasser som redningsmulighet.")
        assumptions.append("Brannskiller mellom boenheter, fellesarealer og eventuelle dørklasser ma fastsettes i detaljprosjekteringen.")

    else:
        for rect in hits_for_terms(page, ["Trapp", "Sluse", "Inngang"], max_items=3):
            elements.append(create_box_element(rect, "fire_compartment", "Brannrelevant punkt"))
        if elements:
            notes.append("Siden er markert rundt identifiserte brannrelevante nøkkelord, men krever normalt faglig ettersyn.")

    if not elements:
        assumptions.append("Siden inneholder for lite sikre teksttreff til et godt automatisk overlayutkast og bor vurderes manuelt.")

    return {
        "page_kind": page_kind,
        "page_title": page.page_title,
        "drawing_summary": "Forsiktig førsteutkast basert pa teksttreff, tegningstittel og sideklassifisering.",
        "analysis_notes": notes,
        "assumptions": assumptions,
        "legend_items": DEFAULT_LEGENDS.get(page_kind, DEFAULT_LEGENDS["general_plan"]),
        "elements": elements,
        "qa": {
            "confidence": 0.42 if elements else 0.2,
            "human_review_focus": [
                "Verifiser at markeringene faktisk følger prosjektets brannstrategi og planlosning.",
                "Suppler med presise brannskiller, dorklasser og detaljer nar dette er dokumentert i prosjektet.",
            ],
        },
        "mode": "heuristic",
    }


# -----------------------------------------------------------------------------
# 7. AI-PROMPTER OG ANALYSE
# -----------------------------------------------------------------------------
def keyword_summary_for_prompt(keyword_hits: Dict[str, List[List[float]]]) -> str:
    compact: Dict[str, Any] = {}
    for term, rects in keyword_hits.items():
        compact[term] = rects[:5]
    return json.dumps(compact, ensure_ascii=False)



def drawing_prompt_for_page(page: SourcePage, brann_data: Dict[str, Any], manual_notes: str) -> str:
    resolved_kind = resolve_page_kind_for_page(page, page.page_kind, page.page_title)
    default_legends = DEFAULT_LEGENDS.get(resolved_kind, DEFAULT_LEGENDS["general_plan"])
    legend_text = "; ".join([f"{item['label']} -> {item['style']}" for item in default_legends])
    text_excerpt = clean_pdf_text(page.raw_text)[:6000]

    return f"""
Du er en svært erfaren norsk brannrådgiver (RIBr). Du analyserer en opplastet arkitekttegning og skal returnere et nøkternt og faglig troverdig branntegningsutkast.

VIKTIGSTE OPPGAVE:
- Les tegningsgrunnlaget grundig før du markerer noe.
- Bruk både helbilde, zoomede utsnitt, tegningsnavn, sidetittel og lesbar PDF-tekst.
- Hvis tegningen f.eks. sier "P-kjeller - se landskapsplan for mer", er dette fortsatt en p-kjellerplan og ikke en situasjonsplan.
- Ikke marker noe du ikke kan begrunne ut fra selve tegningen eller brukerens instrukser.
- Foretrekk færre og bedre markeringer fremfor mange usikre markeringer.

STIL FOR OVERLAY:
- Bruk korte, profesjonelle etiketter som er egnet som callouts utenfor tegningen (typisk 2-6 ord).
- Unnga lange setninger inne i elementetikettene.
- Ikke oppgi presise EI-/REI-klasser eller dorklasser med mindre dette faktisk fremgar av tegningsunderlaget eller brukerens eksplisitte instruks.
- Bruk standardiserte symboler/farger:
  - fire_compartment = rødt stiplet brannskille / kjerne
  - escape_route = grønt bånd for rømningsvei / korridor
  - escape_arrow = grønn pil for rømningsretning
  - rescue_route = oransje markering for balkong / redningsmulighet
  - attack_route = rød pil for innsatsvei / angrepsvei
  - fire_access = blå markering for kjørbar brannatkomst
  - staging_area = rød/rosa markering for oppstillingsplass
  - door_class = liten dørtagg hvis dette faktisk er dokumentert
  - note_box = kort nøytral kommentarboks ved behov

KONTEKST:
Prosjekt: {pd_state.get('p_name', '-')}
Adresse: {pd_state.get('adresse', '-')}, {pd_state.get('kommune', '-')}
Risikoklasse: {brann_data.get('rkl', '-')}
Brannklasse: {brann_data.get('bkl', '-')}
Slokkeanlegg: {brann_data.get('sprinkler', '-')}
Alarm: {brann_data.get('alarm', '-')}
Regelverk/forutsetning: {brann_data.get('regelverk', 'TEK17 / VTEK17')}
Manuelle merknader fra bruker: {manual_notes or 'Ingen ekstra notater'}

TEGNINGSINFO:
Dokument: {page.doc_name}
Side: {page.page_number}
Regelbasert tegningstype-hint: {PAGE_KIND_LABELS.get(resolved_kind, resolved_kind)}
Tolket sidetittel: {page.page_title}
Viktige teksttreff med koordinater (normalisert 0-1): {keyword_summary_for_prompt(page.keyword_hits)}
Utdrag av lesbar tekst fra PDF: {text_excerpt}

MINSTE SYMBOLSETT FOR DENNE TEGNINGSTYPEN:
{legend_text}

RETURNER KUN GYLDIG JSON MED DENNE STRUKTUREN:
{json.dumps({
    'page_kind': 'site_plan | parking_plan | residential_floor_plan | general_plan',
    'page_title': 'Kort og ryddig tittel',
    'drawing_summary': '1-3 setninger om hva tegningen faktisk viser og hva som er markert',
    'analysis_notes': ['Punktvis observasjon 1', 'Punktvis observasjon 2'],
    'assumptions': ['Korte, faglige forutsetninger uten overskriftstekst'],
    'legend_items': [{'label': 'Branncelle / kjerne', 'style': 'fire_compartment'}],
    'elements': [
        {'type': 'dashed_polyline', 'points': [[0.12, 0.20], [0.32, 0.20], [0.32, 0.45]], 'style': 'fire_compartment', 'label': 'Brannskille'},
        {'type': 'band', 'points': [[0.45, 0.18], [0.45, 0.42]], 'style': 'escape_route', 'label': 'Rømningsvei'},
        {'type': 'arrow', 'points': [[0.65, 0.30], [0.72, 0.30]], 'style': 'escape_arrow', 'label': 'Rømningsretning'},
        {'type': 'area_fill', 'rect': [0.12, 0.65, 0.26, 0.77], 'style': 'rescue_route', 'label': 'Redningsmulighet'}
    ],
    'qa': {'confidence': 0.0, 'human_review_focus': ['Hva fagperson ma kontrollere']}
}, ensure_ascii=False)}

VIKTIGE REGLER:
- Koordinater skal være normaliserte 0-1.
- Elementlisten skal være praktisk og ikke overfylt. Vanligvis 4-10 gode elementer er bedre enn 16 svake.
- Hvis underlaget er uklart, reduser antall elementer og skriv en kort forutsetning fremfor å gjette.
- Ikke returner markdown, forklaring eller tekst utenfor JSON.
"""



def refine_prompt_for_page(page: SourcePage, current_analysis: Dict[str, Any], brann_data: Dict[str, Any], manual_notes: str, refine_instruction: str) -> str:
    resolved_kind = resolve_page_kind_for_page(page, current_analysis.get("page_kind", page.page_kind), current_analysis.get("page_title", page.page_title))
    compact_current = deepcopy(current_analysis)
    compact_current["elements"] = compact_current.get("elements", [])[:20]
    compact_current.pop("annotated_image", None)
    return f"""
Du reviderer en eksisterende branntegningsanalyse for en norsk RIBr-bruker.

BRUKERENS NYE INSTRUKS:
{refine_instruction}

REGLER:
- Revider dagens analyse i stedet for å starte helt på nytt.
- Behold gode elementer, fjern svake elementer, og legg bare til nye markeringer dersom de kan begrunnes av tegningen.
- Hold etiketter korte og egnet som callouts utenfor tegningen.
- Ikke skriv lange forklaringer inne i etikettene.
- Ikke oppgi presise EI-/REI-klasser eller dorklasser uten tydelig grunnlag.
- Dersom siden er en p-kjeller, skal den ikke behandles som situasjonsplan bare fordi den henviser til landskapsplan.
- Returner kun gyldig JSON i samme struktur som eksisterende analyse.

KONTEKST:
Prosjekt: {pd_state.get('p_name', '-')}
Risikoklasse: {brann_data.get('rkl', '-')}
Brannklasse: {brann_data.get('bkl', '-')}
Slokkeanlegg: {brann_data.get('sprinkler', '-')}
Alarm: {brann_data.get('alarm', '-')}
Regelverk: {brann_data.get('regelverk', 'TEK17 / VTEK17')}
Generelle brukermerknader: {manual_notes or 'Ingen ekstra notater'}
Regelbasert tegningstype-hint: {PAGE_KIND_LABELS.get(resolved_kind, resolved_kind)}
Dokument: {page.doc_name} | side {page.page_number} | tittel {page.page_title}

EKSISTERENDE ANALYSE JSON:
{json.dumps(compact_current, ensure_ascii=False)}
"""


def merge_legends(primary: List[Dict[str, Any]], fallback: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for item in primary + fallback:
        key = (item.get("label"), item.get("style"))
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def merge_analysis(ai_result: Optional[Dict[str, Any]], heuristic_result: Dict[str, Any]) -> Dict[str, Any]:
    if not ai_result:
        return heuristic_result

    ai_elements = ai_result.get("elements") or []
    if len(ai_elements) < 3 and heuristic_result.get("elements"):
        ai_elements = ai_elements + heuristic_result.get("elements", [])

    ai_result["elements"] = ai_elements[:24]
    ai_result["legend_items"] = merge_legends(ai_result.get("legend_items") or [], heuristic_result.get("legend_items") or [])

    ai_notes = ai_result.get("analysis_notes") or []
    for note in heuristic_result.get("analysis_notes") or []:
        if note not in ai_notes:
            ai_notes.append(note)
    ai_result["analysis_notes"] = ai_notes[:8]

    ai_assumptions = ai_result.get("assumptions") or []
    for assumption in heuristic_result.get("assumptions") or []:
        if assumption not in ai_assumptions:
            ai_assumptions.append(assumption)
    ai_result["assumptions"] = ai_assumptions[:6]

    qa = ai_result.get("qa") or {}
    qa.setdefault("confidence", 0.55)
    focus = qa.get("human_review_focus") or []
    for item in heuristic_result.get("qa", {}).get("human_review_focus", []):
        if item not in focus:
            focus.append(item)
    qa["human_review_focus"] = focus[:6]
    ai_result["qa"] = qa
    ai_result["mode"] = "ai+heuristic"
    return ai_result


def split_lines_to_list(text: str) -> List[str]:
    values: List[str] = []
    for raw in (text or "").splitlines():
        clean = ironclad_text_formatter(raw)
        if clean:
            values.append(clean)
    return values


def default_label_for_style(style: str) -> str:
    for legend_group in DEFAULT_LEGENDS.values():
        for item in legend_group:
            if item.get("style") == style:
                return clean_pdf_text(item.get("label", ""))
    fallback = {
        "note_box": "Kommentar",
        "door_class": "EI2 30-C Sa",
        "core_fill": "Kjerne",
    }
    return fallback.get(style, clean_pdf_text(style.replace("_", " ").title()))


def default_element_for_type(etype: str, style: str = "fire_compartment") -> Dict[str, Any]:
    label = default_label_for_style(style)
    if etype in {"box", "area_fill", "note_box", "door_tag"}:
        rect = [0.62, 0.12, 0.82, 0.22]
        if etype == "area_fill":
            rect = [0.56, 0.16, 0.84, 0.28]
        elif etype == "note_box":
            rect = [0.60, 0.72, 0.92, 0.88]
        elif etype == "door_tag":
            rect = [0.68, 0.18, 0.82, 0.24]
        return {"element_id": uuid4().hex[:10], "type": etype, "rect": rect, "style": style, "label": label}
    return {
        "element_id": uuid4().hex[:10],
        "type": etype,
        "points": [[0.60, 0.18], [0.82, 0.18]],
        "style": style,
        "label": label,
    }


def normalize_element(element: Dict[str, Any]) -> Dict[str, Any]:
    normalized = deepcopy(element or {})
    normalized["element_id"] = str(normalized.get("element_id") or uuid4().hex[:10])
    etype = str(normalized.get("type") or "box")
    style = str(normalized.get("style") or "fire_compartment")
    if style not in FIRE_STYLE_LIBRARY:
        style = "fire_compartment"
    normalized["type"] = etype
    normalized["style"] = style
    normalized["label"] = clean_pdf_text(normalized.get("label") or default_label_for_style(style))

    if etype in {"box", "area_fill", "note_box", "door_tag"}:
        rect = normalized.get("rect") or [0.62, 0.12, 0.82, 0.22]
        try:
            x0, y0, x1, y1 = [clamp01(float(v)) for v in rect[:4]]
        except Exception:
            x0, y0, x1, y1 = 0.62, 0.12, 0.82, 0.22
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        if abs(x1 - x0) < 0.01:
            x1 = clamp01(x0 + 0.04)
        if abs(y1 - y0) < 0.01:
            y1 = clamp01(y0 + 0.04)
        normalized["rect"] = [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]
        normalized.pop("points", None)
    else:
        points = normalized.get("points") or [[0.60, 0.18], [0.82, 0.18]]
        cleaned_points: List[List[float]] = []
        for pt in points:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                continue
            try:
                cleaned_points.append([round(clamp01(float(pt[0])), 4), round(clamp01(float(pt[1])), 4)])
            except Exception:
                continue
        if len(cleaned_points) < 2:
            cleaned_points = [[0.60, 0.18], [0.82, 0.18]]
        normalized["points"] = cleaned_points[:12]
        normalized.pop("rect", None)
    return normalized


def ensure_legend_defaults(page_kind: str, legends: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    seen = set()
    for item in (legends or []) + DEFAULT_LEGENDS.get(page_kind, DEFAULT_LEGENDS["general_plan"]):
        style = item.get("style") or "fire_compartment"
        label = clean_pdf_text(item.get("label") or default_label_for_style(style))
        key = (label, style)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"label": label, "style": style})
    return cleaned[:8]


def normalize_analysis_payload(analysis: Dict[str, Any], page_kind: str) -> Dict[str, Any]:
    normalized = deepcopy(analysis or {})
    normalized["page_kind"] = normalized.get("page_kind") if normalized.get("page_kind") in PAGE_KIND_LABELS else page_kind
    normalized["page_title"] = clean_pdf_text(normalized.get("page_title") or "")
    normalized["drawing_summary"] = ironclad_text_formatter(normalized.get("drawing_summary") or "")
    normalized["analysis_notes"] = [ironclad_text_formatter(v) for v in (normalized.get("analysis_notes") or []) if ironclad_text_formatter(v)][:8]
    normalized["assumptions"] = [ironclad_text_formatter(v) for v in (normalized.get("assumptions") or []) if ironclad_text_formatter(v)][:8]
    normalized["elements"] = [normalize_element(element) for element in (normalized.get("elements") or [])][:28]
    legend_seed = list(normalized.get("legend_items") or [])
    for element in normalized["elements"]:
        style = element.get("style") or "fire_compartment"
        if style == "note_box":
            continue
        legend_seed.append({"label": default_label_for_style(style), "style": style})
    normalized["legend_items"] = ensure_legend_defaults(normalized["page_kind"], legend_seed)
    qa = normalized.get("qa") or {}
    try:
        qa["confidence"] = float(qa.get("confidence", 0.45))
    except Exception:
        qa["confidence"] = 0.45
    qa["human_review_focus"] = [ironclad_text_formatter(v) for v in (qa.get("human_review_focus") or []) if ironclad_text_formatter(v)][:8]
    normalized["qa"] = qa
    normalized["mode"] = normalized.get("mode") or "heuristic"
    return normalized


def element_display_name(element: Dict[str, Any]) -> str:
    label = clean_pdf_text(element.get("label") or "Uten etikett")
    style = element.get("style") or "fire_compartment"
    etype = element.get("type") or "box"
    return f"{label} - {etype} / {style}"


def parse_points_text(raw: str) -> List[List[float]]:
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise ValueError("Punktlisten ma vaere gyldig JSON, f.eks. [[0.1, 0.2], [0.3, 0.2]].") from exc
    if not isinstance(parsed, list):
        raise ValueError("Punktlisten ma vaere en liste med koordinatpar.")
    points: List[List[float]] = []
    for item in parsed:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            raise ValueError("Hvert punkt ma besta av [x, y].")
        points.append([clamp01(float(item[0])), clamp01(float(item[1]))])
    if len(points) < 2:
        raise ValueError("Du ma angi minst to punkt.")
    return points


def points_to_text(points: List[List[float]]) -> str:
    return json.dumps(points or [], ensure_ascii=False, indent=2)


def invalidate_generated_outputs() -> None:
    st.session_state.generated_fire_drawings_pdf = None
    st.session_state.generated_pdf = None
    st.session_state.generated_report_text = ""
    st.session_state.brann_manual_edits_dirty = True



def refresh_analysis_item(item: Dict[str, Any]) -> None:
    page = item["page"]
    item["analysis"] = normalize_analysis_payload(item.get("analysis") or {}, item.get("page_kind") or page.page_kind)
    item["analysis"]["page_kind"] = resolve_page_kind_for_page(page, item["analysis"].get("page_kind", page.page_kind), item["analysis"].get("page_title") or page.page_title)
    item["page_kind"] = item["analysis"].get("page_kind", page.page_kind)
    item["annotated_image"] = render_overlay(page, item["analysis"])
    invalidate_generated_outputs()



def analyze_page(page: SourcePage, brann_data: Dict[str, Any], manual_notes: str = "") -> Dict[str, Any]:
    heuristic = normalize_analysis_payload(heuristic_markup_for_page(page, brann_data), page.page_kind)
    heuristic["page_kind"] = resolve_page_kind_for_page(page, heuristic.get("page_kind", page.page_kind), heuristic.get("page_title") or page.page_title)
    if not AI_AVAILABLE or page.page_kind not in DRAWABLE_PAGE_KINDS:
        return heuristic

    try:
        model = genai.GenerativeModel(pick_model_name())
        detail_sheet = make_ai_detail_sheet(page.image, page.page_title)
        response = model.generate_content([
            drawing_prompt_for_page(page, brann_data, manual_notes),
            page.image,
            detail_sheet,
        ])
        parsed = try_parse_json(extract_response_text(response))
        merged = merge_analysis(parsed, heuristic)
        merged["page_kind"] = resolve_page_kind_for_page(page, merged.get("page_kind", page.page_kind), merged.get("page_title") or page.page_title)
        return normalize_analysis_payload(merged, merged.get("page_kind", page.page_kind))
    except Exception:
        return heuristic



def refine_analysis_with_instruction(
    page: SourcePage,
    current_analysis: Dict[str, Any],
    brann_data: Dict[str, Any],
    manual_notes: str,
    refine_instruction: str,
) -> Dict[str, Any]:
    base = normalize_analysis_payload(current_analysis or {}, current_analysis.get("page_kind", page.page_kind) if current_analysis else page.page_kind)
    base["page_kind"] = resolve_page_kind_for_page(page, base.get("page_kind", page.page_kind), base.get("page_title") or page.page_title)

    if not refine_instruction.strip() or not AI_AVAILABLE:
        return base

    try:
        model = genai.GenerativeModel(pick_model_name())
        detail_sheet = make_ai_detail_sheet(page.image, page.page_title)
        response = model.generate_content([
            refine_prompt_for_page(page, base, brann_data, manual_notes, refine_instruction),
            page.image,
            detail_sheet,
        ])
        parsed = try_parse_json(extract_response_text(response))
        merged = merge_analysis(parsed, base)
        merged["page_kind"] = resolve_page_kind_for_page(page, merged.get("page_kind", page.page_kind), merged.get("page_title") or page.page_title)
        return normalize_analysis_payload(merged, merged.get("page_kind", page.page_kind))
    except Exception:
        return base


# -----------------------------------------------------------------------------
# 8. OVERLAY-RENDERING
# -----------------------------------------------------------------------------
def norm_pt_to_px(point: List[float], image_size: Tuple[int, int]) -> Tuple[int, int]:
    w, h = image_size
    return (int(clamp01(point[0]) * w), int(clamp01(point[1]) * h))


def norm_rect_to_px(rect: List[float], image_size: Tuple[int, int]) -> Tuple[int, int, int, int]:
    w, h = image_size
    x0 = int(clamp01(rect[0]) * w)
    y0 = int(clamp01(rect[1]) * h)
    x1 = int(clamp01(rect[2]) * w)
    y1 = int(clamp01(rect[3]) * h)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def rgba(color_hex: Optional[str], alpha: int = 255) -> Tuple[int, int, int, int]:
    if not color_hex:
        return (0, 0, 0, 0)
    rgb = ImageColor.getrgb(color_hex)
    return (rgb[0], rgb[1], rgb[2], alpha)


def draw_dashed_polyline(draw: ImageDraw.ImageDraw, points: List[Tuple[int, int]], fill: Tuple[int, int, int, int], width: int, dash: int = 12, gap: int = 8) -> None:
    if len(points) < 2:
        return
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        dx = x2 - x1
        dy = y2 - y1
        length = max((dx * dx + dy * dy) ** 0.5, 1.0)
        ux = dx / length
        uy = dy / length
        progress = 0.0
        while progress < length:
            start = (int(x1 + ux * progress), int(y1 + uy * progress))
            end_progress = min(progress + dash, length)
            end = (int(x1 + ux * end_progress), int(y1 + uy * end_progress))
            draw.line([start, end], fill=fill, width=width)
            progress += dash + gap


def draw_arrow(draw: ImageDraw.ImageDraw, p1: Tuple[int, int], p2: Tuple[int, int], fill: Tuple[int, int, int, int], width: int) -> None:
    draw.line([p1, p2], fill=fill, width=width)
    vx = p2[0] - p1[0]
    vy = p2[1] - p1[1]
    length = max((vx * vx + vy * vy) ** 0.5, 1.0)
    ux = vx / length
    uy = vy / length
    head = max(16, width * 3)
    wing = max(10, width * 2)
    left = (int(p2[0] - ux * head - uy * wing), int(p2[1] - uy * head + ux * wing))
    right = (int(p2[0] - ux * head + uy * wing), int(p2[1] - uy * head - ux * wing))
    draw.polygon([p2, left, right], fill=fill)


def draw_hatched_band(draw: ImageDraw.ImageDraw, points: List[Tuple[int, int]], style: Dict[str, Any]) -> None:
    if len(points) < 2:
        return
    fill_color = rgba(style.get("fill"), int(style.get("alpha", 100)))
    stroke_color = rgba(style.get("stroke"), 255)
    stripe_color = rgba(style.get("stripe", style.get("stroke")), 255)
    band_width = int(style.get("width", 24))

    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        draw.line([p1, p2], fill=fill_color, width=band_width)
        draw.line([p1, p2], fill=stroke_color, width=2)

        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        length = max((dx * dx + dy * dy) ** 0.5, 1.0)
        ux = dx / length
        uy = dy / length
        px = -uy
        py = ux
        spacing = max(14, band_width // 2)
        steps = int(length // spacing) + 1
        for step in range(steps):
            t = min(step * spacing, length)
            cx = p1[0] + ux * t
            cy = p1[1] + uy * t
            half = band_width * 0.5
            a = (int(cx - px * half), int(cy - py * half))
            b = (int(cx + px * half), int(cy + py * half))
            draw.line([a, b], fill=stripe_color, width=3)


def draw_band(draw: ImageDraw.ImageDraw, points: List[Tuple[int, int]], style: Dict[str, Any]) -> None:
    if len(points) < 2:
        return
    draw.line(points, fill=rgba(style.get("fill"), int(style.get("alpha", 110))), width=int(style.get("width", 20)))
    draw.line(points, fill=rgba(style.get("stroke"), 255), width=max(2, int(style.get("width", 20) * 0.08)))


def draw_text_label(
    draw: ImageDraw.ImageDraw,
    rect: Tuple[int, int, int, int],
    text: str,
    stroke_hex: str,
    fill_hex: str = "#ffffff",
    text_hex: str = "#0f172a",
    font_size: int = 20,
) -> None:
    font = get_font(font_size, bold=True)
    x0, y0, x1, y1 = rect
    draw.rounded_rectangle(rect, radius=10, outline=rgba(stroke_hex, 255), fill=rgba(fill_hex, 248), width=2)
    lines = wrap_text_px(text, font, max(40, x1 - x0 - 16))
    y = y0 + 8
    for line in lines[:3]:
        draw.text((x0 + 8, y), clean_pdf_text(line), font=font, fill=rgba(text_hex, 255))
        y += font.getbbox(line)[3] - font.getbbox(line)[1] + 2


def find_legend_anchor(image: Image.Image, box_w_ratio: float = 0.28, box_h_ratio: float = 0.24) -> Tuple[int, int, int, int]:
    w, h = image.size
    box_w = int(w * box_w_ratio)
    box_h = int(h * box_h_ratio)
    candidates = [
        (int(w * 0.68), int(h * 0.68)),
        (int(w * 0.04), int(h * 0.68)),
        (int(w * 0.68), int(h * 0.08)),
        (int(w * 0.04), int(h * 0.08)),
        (int(w * 0.62), int(h * 0.54)),
        (int(w * 0.06), int(h * 0.54)),
    ]

    thumb = image.convert("L").resize((220, 220))
    edges = thumb.filter(ImageFilter.FIND_EDGES)
    best_score = -10.0
    best_rect = (candidates[0][0], candidates[0][1], candidates[0][0] + box_w, candidates[0][1] + box_h)

    for idx, (cx, cy) in enumerate(candidates):
        x0 = clamp_int(cx, 10, max(10, w - box_w - 10))
        y0 = clamp_int(cy, 10, max(10, h - box_h - 10))
        x1 = x0 + box_w
        y1 = y0 + box_h

        tx0 = int((x0 / max(w, 1)) * 220)
        ty0 = int((y0 / max(h, 1)) * 220)
        tx1 = int((x1 / max(w, 1)) * 220)
        ty1 = int((y1 / max(h, 1)) * 220)
        crop = thumb.crop((tx0, ty0, tx1, ty1))
        edge_crop = edges.crop((tx0, ty0, tx1, ty1))
        brightness = sum(crop.getdata()) / max(len(crop.getdata()), 1)
        edge_density = sum(edge_crop.getdata()) / max(len(edge_crop.getdata()), 1)
        preference_bonus = 18 if idx == 0 else 0
        score = (brightness / 255.0) * 1.4 - (edge_density / 255.0) * 0.95 + preference_bonus / 100.0
        if score > best_score:
            best_score = score
            best_rect = (x0, y0, x1, y1)
    return best_rect


def draw_legend_box(draw: ImageDraw.ImageDraw, image: Image.Image, legend_items: List[Dict[str, Any]], title: str = "Symbolforklaring") -> None:
    if not legend_items:
        return

    rect = find_legend_anchor(image)
    x0, y0, x1, y1 = rect
    draw.rounded_rectangle(rect, radius=18, fill=rgba("#ffffff", 242), outline=rgba("#94a3b8", 255), width=2)
    title_font = get_font(max(18, int((x1 - x0) * 0.035)), bold=True)
    body_font = get_font(max(16, int((x1 - x0) * 0.028)), bold=False)
    draw.text((x0 + 18, y0 + 14), clean_pdf_text(title), font=title_font, fill=rgba("#0f172a", 255))

    line_y = y0 + 54
    swatch_x = x0 + 18
    text_x = x0 + 92
    row_gap = max(28, int((y1 - y0 - 64) / max(len(legend_items), 1)))

    for item in legend_items[:7]:
        style = FIRE_STYLE_LIBRARY.get(item.get("style", "fire_compartment"), FIRE_STYLE_LIBRARY["fire_compartment"])
        label = clean_pdf_text(item.get("label", ""))
        if item.get("style") == "fire_compartment":
            draw_dashed_polyline(draw, [(swatch_x, line_y + 8), (swatch_x + 50, line_y + 8)], rgba(style.get("stroke"), 255), 5)
        elif item.get("style") == "escape_route":
            draw_band(draw, [(swatch_x, line_y + 8), (swatch_x + 50, line_y + 8)], style)
        elif item.get("style") == "escape_arrow":
            draw_arrow(draw, (swatch_x, line_y + 8), (swatch_x + 50, line_y + 8), rgba(style.get("stroke"), 255), 5)
        elif item.get("style") == "rescue_route":
            draw_band(draw, [(swatch_x, line_y + 8), (swatch_x + 50, line_y + 8)], style)
        elif item.get("style") == "attack_route":
            draw_arrow(draw, (swatch_x, line_y + 8), (swatch_x + 50, line_y + 8), rgba(style.get("stroke"), 255), 5)
        elif item.get("style") == "fire_access":
            draw_hatched_band(draw, [(swatch_x, line_y + 8), (swatch_x + 50, line_y + 8)], style)
        elif item.get("style") == "staging_area":
            draw.rounded_rectangle((swatch_x, line_y, swatch_x + 50, line_y + 16), radius=4, fill=rgba(style.get("fill"), int(style.get("alpha", 120))), outline=rgba(style.get("stroke"), 255), width=2)
        else:
            draw.rounded_rectangle((swatch_x, line_y, swatch_x + 50, line_y + 16), radius=4, fill=rgba(style.get("fill", "#ffffff"), int(style.get("alpha", 180))), outline=rgba(style.get("stroke"), 255), width=2)
        draw.text((text_x, line_y - 4), label, font=body_font, fill=rgba("#0f172a", 255))
        line_y += row_gap


def make_comparison_image(original: Image.Image, annotated: Image.Image, title: str) -> Image.Image:
    target_height = 980
    def fit(img: Image.Image) -> Image.Image:
        ratio = target_height / max(img.height, 1)
        return img.resize((int(img.width * ratio), target_height))

    left = fit(original)
    right = fit(annotated)
    gap = 28
    pad = 24
    header_h = 86
    footer_h = 28
    canvas_w = left.width + right.width + gap + pad * 2
    canvas_h = target_height + header_h + footer_h + pad * 2
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((12, 12, canvas_w - 12, canvas_h - 12), radius=18, outline=(214, 219, 225), width=2, fill=(255, 255, 255))
    draw.rounded_rectangle((18, 18, canvas_w - 18, 18 + header_h - 18), radius=14, fill=(236, 240, 245))

    title_font = get_font(30, bold=True)
    sub_font = get_font(20, bold=False)
    draw.text((pad, 28), clean_pdf_text(title), font=title_font, fill=(22, 34, 47))
    draw.text((pad, 58), "Venstre: original tegning | Høyre: generert branntegning", font=sub_font, fill=(86, 95, 108))

    left_x = pad
    top_y = header_h + pad
    right_x = left_x + left.width + gap
    canvas.paste(left, (left_x, top_y))
    canvas.paste(right, (right_x, top_y))

    label_font = get_font(22, bold=True)
    draw.text((left_x, top_y - 28), "Original", font=label_font, fill=(71, 85, 105))
    draw.text((right_x, top_y - 28), "Branntegning", font=label_font, fill=(71, 85, 105))
    return canvas


def callout_content_box(image: Image.Image, left: int = 260, right: int = 320, top: int = 36, bottom: int = 36) -> Tuple[int, int, int, int, int, int]:
    canvas_w = image.width + left + right
    canvas_h = image.height + top + bottom
    return left, top, left + image.width, top + image.height, canvas_w, canvas_h



def norm_pt_to_px_in_box(point: List[float], box: Tuple[int, int, int, int]) -> Tuple[int, int]:
    x0, y0, x1, y1 = box
    return (
        int(x0 + clamp01(point[0]) * max(x1 - x0, 1)),
        int(y0 + clamp01(point[1]) * max(y1 - y0, 1)),
    )



def norm_rect_to_px_in_box(rect: List[float], box: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    bx0 = int(x0 + clamp01(rect[0]) * max(x1 - x0, 1))
    by0 = int(y0 + clamp01(rect[1]) * max(y1 - y0, 1))
    bx1 = int(x0 + clamp01(rect[2]) * max(x1 - x0, 1))
    by1 = int(y0 + clamp01(rect[3]) * max(y1 - y0, 1))
    return (min(bx0, bx1), min(by0, by1), max(bx0, bx1), max(by0, by1))



def anchor_for_element(element: Dict[str, Any], content_box: Tuple[int, int, int, int]) -> Optional[Tuple[int, int]]:
    etype = element.get("type")
    if etype in {"box", "area_fill", "note_box", "door_tag"} and element.get("rect"):
        x0, y0, x1, y1 = norm_rect_to_px_in_box(element.get("rect"), content_box)
        return ((x0 + x1) // 2, (y0 + y1) // 2)
    if etype in {"dashed_polyline", "polyline", "band", "arrow"} and element.get("points"):
        points = [norm_pt_to_px_in_box(pt, content_box) for pt in element.get("points") or []]
        if not points:
            return None
        return (sum(p[0] for p in points) // len(points), sum(p[1] for p in points) // len(points))
    return None



def wrap_draw_text(draw: ImageDraw.ImageDraw, text: str, box: Tuple[int, int, int, int], font: ImageFont.ImageFont, fill: Tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    lines = wrap_text_px(text, font, max(40, x1 - x0 - 22))
    yy = y0 + 12
    for line in lines[:3]:
        draw.text((x0 + 12, yy), clean_pdf_text(line), font=font, fill=fill)
        yy += font.getbbox(line)[3] - font.getbbox(line)[1] + 4



def collect_callouts(elements: List[Dict[str, Any]], content_box: Tuple[int, int, int, int], max_items: int = 10) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
    raw_elements: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for element in elements or []:
        label = clean_pdf_text(element.get("label", "")).strip()
        if not label:
            continue
        if element.get("type") in {"note_box", "door_tag"}:
            continue
        anchor = anchor_for_element(element, content_box)
        if not anchor:
            continue
        key = (label, element.get("style", "fire_compartment"))
        grouped.setdefault(key, []).append(anchor)
        raw_elements[key] = element

    candidates: List[Dict[str, Any]] = []
    for idx, (key, anchors) in enumerate(grouped.items(), start=1):
        label, style = key
        xs = [pt[0] for pt in anchors]
        ys = sorted(pt[1] for pt in anchors)
        anchor = (int(sum(xs) / len(xs)), ys[len(ys) // 2])
        candidates.append({
            "label": label,
            "style": style,
            "anchor": anchor,
            "count": len(anchors),
            "element": raw_elements[key],
        })
    candidates.sort(key=lambda item: item["anchor"][1])
    return candidates[:max_items]



def layout_callouts(candidates: List[Dict[str, Any]], canvas_size: Tuple[int, int], content_box: Tuple[int, int, int, int]) -> List[Dict[str, Any]]:
    canvas_w, canvas_h = canvas_size
    x0, y0, x1, y1 = content_box
    mid_x = (x0 + x1) / 2.0
    top_limit = y0 + 8
    bottom_limit = y1 - 8
    left_box_w = max(220, x0 - 28)
    right_box_w = max(250, canvas_w - x1 - 28)

    result: List[Dict[str, Any]] = []
    for side in ["left", "right"]:
        items = [item for item in candidates if (item["anchor"][0] < mid_x) == (side == "left")]
        last_y = top_limit - 8
        prepared: List[Dict[str, Any]] = []
        for number, item in enumerate(items, start=1):
            box_w = left_box_w if side == "left" else right_box_w
            style = FIRE_STYLE_LIBRARY.get(item["style"], FIRE_STYLE_LIBRARY["fire_compartment"])
            font = get_font(18, bold=True)
            line_count = len(wrap_text_px(item["label"], font, box_w - 56))
            box_h = 24 + line_count * 24
            desired_y = int(item["anchor"][1] - box_h / 2)
            y_pos = max(top_limit, desired_y)
            if y_pos < last_y + 10:
                y_pos = last_y + 10
            x_pos = 14 if side == "left" else canvas_w - box_w - 14
            prepared.append({
                **item,
                "side": side,
                "box": (x_pos, y_pos, x_pos + box_w, y_pos + box_h),
                "box_h": box_h,
                "number": len(result) + len(prepared) + 1,
                "style_cfg": style,
            })
            last_y = y_pos + box_h
        overflow = max(0, (prepared[-1]["box"][3] - bottom_limit) if prepared else 0)
        if overflow > 0:
            shift = overflow
            for entry in reversed(prepared):
                bx0, by0, bx1, by1 = entry["box"]
                moved = min(shift, max(0, by0 - top_limit))
                entry["box"] = (bx0, by0 - moved, bx1, by1 - moved)
                shift -= moved
        result.extend(prepared)
    return result



def draw_callouts(draw: ImageDraw.ImageDraw, elements: List[Dict[str, Any]], content_box: Tuple[int, int, int, int], canvas_size: Tuple[int, int]) -> None:
    candidates = collect_callouts(elements, content_box)
    laid_out = layout_callouts(candidates, canvas_size, content_box)
    title_font = get_font(18, bold=True)
    body_font = get_font(17, bold=False)

    for callout in laid_out:
        style = callout["style_cfg"]
        anchor_x, anchor_y = callout["anchor"]
        x0, y0, x1, y1 = callout["box"]
        stroke = rgba(style.get("stroke", "#334155"), 255)
        text_fill = rgba(style.get("text", style.get("stroke", "#0f172a")), 255)
        draw.rounded_rectangle(callout["box"], radius=16, fill=rgba("#ffffff", 248), outline=stroke, width=2)

        bubble_r = 13
        if callout["side"] == "left":
            elbow = (content_box[0] - 20, anchor_y)
            end = (x1, y0 + (y1 - y0) // 2)
        else:
            elbow = (content_box[2] + 20, anchor_y)
            end = (x0, y0 + (y1 - y0) // 2)
        draw.line([callout["anchor"], elbow, end], fill=stroke, width=3)
        draw.ellipse((anchor_x - bubble_r, anchor_y - bubble_r, anchor_x + bubble_r, anchor_y + bubble_r), fill=rgba("#ffffff", 248), outline=stroke, width=2)
        num_text = str(callout["number"])
        num_bbox = title_font.getbbox(num_text)
        draw.text((anchor_x - (num_bbox[2] - num_bbox[0]) / 2, anchor_y - (num_bbox[3] - num_bbox[1]) / 2 - 1), num_text, font=title_font, fill=text_fill)

        bubble_x = x0 + 14
        bubble_y = y0 + 12
        draw.ellipse((bubble_x, bubble_y, bubble_x + 28, bubble_y + 28), fill=rgba("#ffffff", 248), outline=stroke, width=2)
        draw.text((bubble_x + 7, bubble_y + 3), num_text, font=title_font, fill=text_fill)
        wrap_draw_text(draw, clean_pdf_text(callout["label"]), (x0 + 42, y0 + 8, x1 - 10, y1 - 8), body_font, text_fill)



def draw_legend_box_fixed(draw: ImageDraw.ImageDraw, rect: Tuple[int, int, int, int], legend_items: List[Dict[str, Any]], title: str = "Symbolforklaring") -> None:
    if not legend_items:
        return
    x0, y0, x1, y1 = rect
    draw.rounded_rectangle(rect, radius=18, fill=rgba("#ffffff", 242), outline=rgba("#94a3b8", 255), width=2)
    title_font = get_font(20, bold=True)
    body_font = get_font(17, bold=False)
    draw.text((x0 + 18, y0 + 12), clean_pdf_text(title), font=title_font, fill=rgba("#0f172a", 255))
    line_y = y0 + 48
    swatch_x = x0 + 18
    text_x = x0 + 90
    row_gap = max(24, int((y1 - y0 - 58) / max(len(legend_items), 1)))
    for item in legend_items[:7]:
        style = FIRE_STYLE_LIBRARY.get(item.get("style", "fire_compartment"), FIRE_STYLE_LIBRARY["fire_compartment"])
        label = clean_pdf_text(item.get("label", ""))
        if item.get("style") == "fire_compartment":
            draw_dashed_polyline(draw, [(swatch_x, line_y + 8), (swatch_x + 50, line_y + 8)], rgba(style.get("stroke"), 255), 5)
        elif item.get("style") == "escape_route":
            draw_band(draw, [(swatch_x, line_y + 8), (swatch_x + 50, line_y + 8)], style)
        elif item.get("style") == "escape_arrow":
            draw_arrow(draw, (swatch_x, line_y + 8), (swatch_x + 50, line_y + 8), rgba(style.get("stroke"), 255), 5)
        elif item.get("style") == "rescue_route":
            draw_band(draw, [(swatch_x, line_y + 8), (swatch_x + 50, line_y + 8)], style)
        elif item.get("style") == "attack_route":
            draw_arrow(draw, (swatch_x, line_y + 8), (swatch_x + 50, line_y + 8), rgba(style.get("stroke"), 255), 5)
        elif item.get("style") == "fire_access":
            draw_hatched_band(draw, [(swatch_x, line_y + 8), (swatch_x + 50, line_y + 8)], style)
        elif item.get("style") == "staging_area":
            draw.rounded_rectangle((swatch_x, line_y, swatch_x + 50, line_y + 16), radius=4, fill=rgba(style.get("fill"), int(style.get("alpha", 120))), outline=rgba(style.get("stroke"), 255), width=2)
        else:
            draw.rounded_rectangle((swatch_x, line_y, swatch_x + 50, line_y + 16), radius=4, fill=rgba(style.get("fill", "#ffffff"), int(style.get("alpha", 180))), outline=rgba(style.get("stroke"), 255), width=2)
        draw.text((text_x, line_y - 3), label, font=body_font, fill=rgba("#0f172a", 255))
        line_y += row_gap



def render_overlay(page: SourcePage, analysis: Dict[str, Any]) -> Image.Image:
    content_left, content_top, content_right, content_bottom, canvas_w, canvas_h = callout_content_box(page.image)
    content_box = (content_left, content_top, content_right, content_bottom)

    base_canvas = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 255))
    base_canvas.paste(page.image.convert("RGBA"), (content_left, content_top))
    overlay = Image.new("RGBA", base_canvas.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    for element in analysis.get("elements", []) or []:
        style_name = element.get("style", "fire_compartment")
        style = FIRE_STYLE_LIBRARY.get(style_name, FIRE_STYLE_LIBRARY["fire_compartment"])
        label = clean_pdf_text(element.get("label", ""))
        etype = element.get("type")

        if etype in {"dashed_polyline", "polyline", "band", "arrow"}:
            points = [norm_pt_to_px_in_box(pt, content_box) for pt in element.get("points", []) or []]
            if len(points) < 2:
                continue
            if etype == "dashed_polyline":
                draw_dashed_polyline(draw, points, rgba(style.get("stroke"), 255), int(style.get("width", 4)), *style.get("dash", (10, 6)))
            elif etype == "polyline":
                draw.line(points, fill=rgba(style.get("stroke"), 255), width=int(style.get("width", 4)))
            elif etype == "band":
                if style_name == "fire_access":
                    draw_hatched_band(draw, points, style)
                else:
                    draw_band(draw, points, style)
            elif etype == "arrow":
                draw_arrow(draw, points[0], points[-1], rgba(style.get("stroke"), 255), int(style.get("width", 4)))

        elif etype in {"box", "area_fill", "note_box"}:
            rect = element.get("rect")
            if not rect:
                continue
            px_rect = norm_rect_to_px_in_box(rect, content_box)
            if etype == "box":
                if style_name == "fire_compartment":
                    x0, y0, x1, y1 = px_rect
                    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
                    draw_dashed_polyline(draw, pts, rgba(style.get("stroke"), 255), int(style.get("width", 4)), *style.get("dash", (10, 6)))
                elif style_name == "fire_access":
                    pts = [(px_rect[0], (px_rect[1] + px_rect[3]) // 2), (px_rect[2], (px_rect[1] + px_rect[3]) // 2)]
                    draw_hatched_band(draw, pts, style)
                else:
                    draw.rounded_rectangle(px_rect, radius=12, outline=rgba(style.get("stroke"), 255), width=int(style.get("width", 3)), fill=rgba(style.get("fill"), int(style.get("alpha", 80))))
            elif etype == "area_fill":
                draw.rounded_rectangle(px_rect, radius=12, outline=rgba(style.get("stroke"), 255), width=max(2, int(style.get("width", 2) * 0.12)), fill=rgba(style.get("fill"), int(style.get("alpha", 90))))
            else:
                draw_text_label(draw, px_rect, label or "Kommentar", style.get("stroke", "#94a3b8"), style.get("fill", "#ffffff"), style.get("text", "#0f172a"), max(16, int(page.image.width * 0.012)))

        elif etype == "door_tag":
            rect = element.get("rect")
            if not rect:
                continue
            px_rect = norm_rect_to_px_in_box(rect, content_box)
            draw_text_label(draw, px_rect, label or "EI2 30 Sa", style.get("stroke", "#ef4444"), style.get("fill", "#ffffff"), style.get("text", "#ef4444"), max(14, int(page.image.width * 0.010)))

    combined = Image.alpha_composite(base_canvas, overlay).convert("RGB")
    overlay_draw = ImageDraw.Draw(combined)
    draw_callouts(overlay_draw, analysis.get("elements", []) or [], content_box, combined.size)

    legend_items = analysis.get("legend_items") or DEFAULT_LEGENDS.get(analysis.get("page_kind", page.page_kind), DEFAULT_LEGENDS["general_plan"])
    legend_rect = (content_right + 16, max(content_top + 18, content_bottom - 220), canvas_w - 16, content_bottom - 12)
    draw_legend_box_fixed(overlay_draw, legend_rect, legend_items)
    return combined


# -----------------------------------------------------------------------------
# 9. TABELLRENDERER FOR RAPPORTEN
# -----------------------------------------------------------------------------
def render_table_image(df: pd.DataFrame, title: str, subtitle: str = "") -> Image.Image:
    df = df.copy().fillna("")
    if df.empty:
        df = pd.DataFrame([{"Info": "Ingen data"}])

    title_font = get_font(30, bold=True)
    subtitle_font = get_font(18, bold=False)
    header_font = get_font(18, bold=True)
    body_font = get_font(16, bold=False)

    padding = 28
    table_width = 1500
    col_count = len(df.columns)
    max_len_by_col = []
    for col in df.columns:
        width_hint = max(len(clean_pdf_text(col)), *(len(clean_pdf_text(v)) for v in df[col].astype(str).tolist()[:15]))
        max_len_by_col.append(max(10, min(width_hint, 48)))
    total_weight = sum(max_len_by_col) or 1
    col_widths = [max(120, int(table_width * weight / total_weight)) for weight in max_len_by_col]

    header_height = 52
    row_height = 42
    title_height = 88 if subtitle else 68
    image_width = padding * 2 + sum(col_widths)
    image_height = padding * 2 + title_height + header_height + row_height * len(df) + 24

    img = Image.new("RGB", (image_width, image_height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((10, 10, image_width - 10, image_height - 10), radius=18, outline=(220, 225, 231), width=2, fill=(255, 255, 255))
    draw.rounded_rectangle((18, 18, image_width - 18, 18 + title_height - 14), radius=14, fill=(236, 240, 245))
    draw.text((padding, 28), clean_pdf_text(title), font=title_font, fill=(20, 28, 38))
    if subtitle:
        draw.text((padding, 60), clean_pdf_text(subtitle), font=subtitle_font, fill=(92, 101, 112))

    start_y = padding + title_height
    x = padding
    for col, width in zip(df.columns, col_widths):
        draw.rectangle((x, start_y, x + width, start_y + header_height), fill=(46, 62, 84))
        draw.text((x + 10, start_y + 14), clean_pdf_text(col), font=header_font, fill=(255, 255, 255))
        x += width

    y = start_y + header_height
    for ridx in range(len(df)):
        x = padding
        row_fill = (255, 255, 255) if ridx % 2 == 0 else (248, 250, 252)
        for col, width in zip(df.columns, col_widths):
            draw.rectangle((x, y, x + width, y + row_height), fill=row_fill, outline=(220, 225, 231), width=1)
            cell = clean_pdf_text(df.iloc[ridx][col])
            draw.text((x + 10, y + 12), cell[:80], font=body_font, fill=(33, 39, 45))
            x += width
        y += row_height
    return img


# -----------------------------------------------------------------------------
# 10. PDF-MOTOR (BASERT PAA GEO-UTTRYKK)
# -----------------------------------------------------------------------------
class BuiltlyCorporatePDF(FPDF):
    body_left = 20
    body_width = 170
    content_bottom = 272

    def header(self) -> None:
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

    def footer(self) -> None:
        self.set_y(-12)
        self.set_draw_color(210, 214, 220)
        self.line(18, 285, 192, 285)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(110, 114, 119)
        self.cell(60, 5, clean_pdf_text(self.doc_code), 0, 0, "L")
        self.cell(70, 5, clean_pdf_text("Utkast - krever faglig kontroll"), 0, 0, "C")
        self.cell(0, 5, clean_pdf_text(f"Side {self.page_no()}"), 0, 0, "R")

    def estimate_wrapped_lines(self, text: str, width: float) -> int:
        clean = clean_pdf_text(text)
        if not clean:
            return 1
        words = clean.split()
        if not words:
            return 1
        lines = 1
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if self.get_string_width(candidate) <= width:
                current = candidate
            else:
                lines += 1
                current = word
        return max(lines, 1)

    def estimate_multicell_height(self, text: str, width: float, line_height: float) -> float:
        return self.estimate_wrapped_lines(text, width) * line_height

    def will_overflow(self, needed_height: float, keep_with_next: float = 0.0) -> bool:
        return self.get_y() + needed_height + keep_with_next > self.content_bottom

    def ensure_space(self, needed_height: float, keep_with_next: float = 0.0) -> None:
        if self.will_overflow(needed_height, keep_with_next):
            self.add_page()

    def section_title(self, title: str, keep_with_next: float = 14.0) -> None:
        title = ironclad_text_formatter(title)
        num_match = re.match(r"^(\d+\.?\d*)\s*(.*)$", title)
        number = None
        text = title
        if num_match and (num_match.group(1).endswith(".") or num_match.group(2)):
            number = num_match.group(1).rstrip(".")
            text = num_match.group(2).strip()
        self.set_font("Helvetica", "B", 17.2)
        available_width = 156 if number else 170
        lines = self.estimate_wrapped_lines(clean_pdf_text(text.upper()), available_width)
        needed_height = 4 + (lines * 8.0) + 7
        self.ensure_space(needed_height, keep_with_next)
        self.ln(2)
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
        line_y = self.get_y() + 1.2
        self.set_draw_color(204, 209, 216)
        self.line(20, line_y, 190, line_y)
        self.ln(5)

    def subheading(self, text: str, keep_with_next: float = 10.0) -> None:
        text = ironclad_text_formatter(text.replace("##", "").replace("###", "").rstrip(":"))
        self.set_font("Helvetica", "B", 13.0)
        lines = self.estimate_wrapped_lines(clean_pdf_text(text.upper()), 170)
        needed_height = 3 + (lines * 6.2) + 4
        self.ensure_space(needed_height, keep_with_next)
        self.ln(1.6)
        self.set_x(20)
        self.set_text_color(48, 64, 86)
        self.multi_cell(170, 6.1, clean_pdf_text(text.upper()), 0, "L")
        self.set_draw_color(225, 229, 234)
        self.line(20, self.get_y() + 0.8, 190, self.get_y() + 0.8)
        self.ln(2.6)

    def appendix_item_title(self, text: str, keep_with_next: float = 18.0) -> None:
        text = ironclad_text_formatter(text)
        self.set_font("Helvetica", "B", 11.7)
        lines = self.estimate_wrapped_lines(clean_pdf_text(text.upper()), 170)
        needed_height = 2 + (lines * 5.6) + 4
        self.ensure_space(needed_height, keep_with_next)
        self.ln(1.2)
        self.set_x(20)
        self.set_text_color(48, 64, 86)
        self.multi_cell(170, 5.6, clean_pdf_text(text.upper()), 0, "L")
        self.set_draw_color(225, 229, 234)
        self.line(20, self.get_y() + 0.5, 190, self.get_y() + 0.5)
        self.ln(2)

    def body_paragraph(self, text: str, first: bool = False) -> None:
        text = ironclad_text_formatter(text)
        if not text:
            return
        line_height = 5.7 if first else 5.5
        font_size = 10.6 if first else 10.2
        self.set_font("Helvetica", "", font_size)
        estimated = self.estimate_multicell_height(text, 170, line_height)
        self.ensure_space(min(max(estimated + 2, 10), 40))
        self.set_x(20)
        self.set_text_color(35, 39, 43)
        self.multi_cell(170, line_height, text)
        self.ln(1.6)

    def bullets(self, items: Iterable[str], numbered: bool = False) -> None:
        self.set_font("Helvetica", "", 10.1)
        self.set_text_color(35, 39, 43)
        for idx, item in enumerate(items, start=1):
            clean = ironclad_text_formatter(item)
            if not clean:
                continue
            estimated = self.estimate_multicell_height(clean, 162, 5.2) + 1.4
            self.ensure_space(max(estimated, 7.2))
            start_y = self.get_y()
            self.set_xy(22, start_y)
            self.cell(6, 5.2, f"{idx}." if numbered else "-", 0, 0, "L")
            self.set_xy(28, start_y)
            self.multi_cell(162, 5.2, clean)
            self.ln(0.8)

    def rounded_rect(self, x: float, y: float, w: float, h: float, r: float, style: str = "", corners: str = "1234") -> None:
        try:
            super().rounded_rect(x, y, w, h, r, style, corners)
        except Exception:
            self.rect(x, y, w, h, style if style in {"F", "FD", "DF"} else "")

    def kv_card(self, items: List[Tuple[str, str]], x: Optional[float] = None, width: float = 80, title: Optional[str] = None) -> None:
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
            self.cell(30, 5, clean_pdf_text(label), 0, 0)
            self.set_font("Helvetica", "", 8.6)
            self.set_text_color(35, 39, 43)
            self.multi_cell(width - 36, 5, clean_pdf_text(value))
            yy = self.get_y() + 1
        self.set_y(max(self.get_y(), start_y + height))

    def highlight_box(self, title: str, items: List[str], fill: Tuple[int, int, int] = (245, 247, 250), accent: Tuple[int, int, int] = (56, 194, 201)) -> None:
        self.set_font("Helvetica", "", 10)
        total_text_h = 0
        for item in items:
            lines = self.estimate_wrapped_lines(clean_pdf_text(item), 145)
            total_text_h += (lines * 5.5) + 2
        box_h = 16 + total_text_h
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
        yy = y + 11
        for item in items:
            self.set_xy(x + 8, yy)
            self.cell(5, 5, "-", 0, 0)
            self.multi_cell(154, 5.2, clean_pdf_text(item))
            yy = self.get_y() + 2
        self.set_y(y + box_h + 3)

    def stats_row(self, stats: List[Tuple[str, str, str]]) -> None:
        if not stats:
            return
        fill_map = {
            "info": (219, 234, 254),
            "ok": (220, 252, 231),
            "warn": (254, 249, 195),
            "fire": (254, 226, 226),
        }
        self.ensure_space(26)
        box_w, gap, x0, y = 40, 3.3, 20, self.get_y()
        for idx, (label, value, class_code) in enumerate(stats):
            x = x0 + idx * (box_w + gap)
            self.set_fill_color(*fill_map.get(class_code, (245, 247, 249)))
            self.set_draw_color(216, 220, 226)
            self.rounded_rect(x, y, box_w, 20, 3, "DF")
            self.set_xy(x, y + 3)
            self.set_font("Helvetica", "B", 15)
            self.set_text_color(33, 39, 45)
            self.cell(box_w, 7, clean_pdf_text(str(value)), 0, 1, "C")
            self.set_x(x)
            self.set_font("Helvetica", "", 7.8)
            self.set_text_color(75, 80, 87)
            self.multi_cell(box_w, 4, clean_pdf_text(label), 0, "C")
        self.set_y(y + 24)

    def image_block(self, image_path: str, width: float = 170, caption: str = "") -> None:
        image = Image.open(image_path)
        height = width * (image.height / max(image.width, 1))
        self.ensure_space(height + 14)
        x, y = 20, self.get_y()
        self.image(image_path, x=x, y=y, w=width)
        self.set_y(y + height + 2)
        if caption:
            self.set_x(20)
            self.set_font("Helvetica", "I", 7.7)
            self.set_text_color(104, 109, 116)
            self.multi_cell(width, 4, clean_pdf_text(caption), 0, "L")
        self.ln(6)



def build_cover_page(pdf: BuiltlyCorporatePDF, project_data: Dict[str, Any], brann_data: Dict[str, Any], cover_img: Optional[Image.Image]) -> None:
    pdf.add_page()
    if os.path.exists("logo.png"):
        try:
            pdf.image("logo.png", x=150, y=15, w=40)
        except Exception:
            pass

    pdf.set_xy(20, 45)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(100, 105, 110)
    pdf.cell(80, 6, clean_pdf_text("RAPPORT"), 0, 1, "L")

    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 34)
    pdf.set_text_color(20, 28, 38)
    pdf.multi_cell(95, 12, clean_pdf_text(project_data.get("p_name", "Brannkonsept")), 0, "L")

    pdf.ln(4)
    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(64, 68, 74)
    pdf.multi_cell(95, 6.5, clean_pdf_text("Brannteknisk konsept, automatisert tegningsanalyse og vedlagte branntegninger"), 0, "L")

    pdf.set_xy(118, 45)
    meta_items = [
        ("Oppdragsgiver", project_data.get("c_name", "-")),
        ("Emne", "Brannkonsept (RIBr)"),
        ("Dato / revisjon", datetime.now().strftime("%d.%m.%Y") + " / 01"),
        ("Dokumentkode", "Builtly-RIBr-002"),
    ]
    pdf.kv_card(meta_items, x=118, width=72, title="Fakta")

    if cover_img is not None:
        temp_path = safe_temp_image(cover_img.convert("RGB"), ".jpg")
        with Image.open(temp_path) as img:
            aspect = img.height / max(img.width, 1)
        width = 170
        height = width * aspect
        if height > 130:
            height = 130
            width = height / aspect
        x = 20 + (170 - width) / 2
        y = max(pdf.get_y() + 15, 115)
        pdf.image(temp_path, x=x, y=y, w=width)
    else:
        pdf.set_fill_color(244, 246, 248)
        pdf.set_draw_color(220, 224, 228)
        pdf.rounded_rect(20, 115, 170, 80, 4, "DF")
        pdf.set_xy(24, 146)
        pdf.set_font("Helvetica", "I", 12)
        pdf.set_text_color(112, 117, 123)
        pdf.multi_cell(160, 6, clean_pdf_text("Branntegning eller situasjonsplan legges inn automatisk som forsidevisualisering når dette er tilgjengelig."), 0, "C")

    pdf.set_xy(20, 255)
    pdf.set_font("Helvetica", "", 8.8)
    pdf.set_text_color(104, 109, 116)
    pdf.multi_cell(170, 4.5, clean_pdf_text("Rapporten er generert av Builtly RIBr AI pa bakgrunn av prosjektdata, opplastede tegninger og automatisert tegningsanalyse. Dokumentet er et arbeidsutkast og skal underlegges faglig kontroll for bruk i prosjektering, byggesak og myndighetsdialog."))


def build_toc_page(pdf: BuiltlyCorporatePDF, include_appendices: bool = True) -> None:
    pdf.add_page()
    pdf.section_title("INNHOLDSFORTEGNELSE")
    items = [
        "1. Innledning og prosjektbeskrivelse",
        "2. Brannstrategi og hovedytelser",
        "3. Tegningsanalyse og genererte branntegninger",
        "4. Forutsetninger, fravik og kontrollpunkter",
        "5. Videre oppfolging i detaljprosjekt",
    ]
    if include_appendices:
        items.extend([
            "Vedlegg A. Tegningsregister",
            "Vedlegg B. Branntegninger (annoterte vedlegg)",
        ])
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
            "Rapporten kombinerer tekstlig brannstrategi med faktiske annoterte branntegninger utledet fra opplastet tegningsgrunnlag.",
            "Automatiske markeringer er laget for a vaere nyttige i tidligfase, men ma alltid verifiseres og eventuelt skjerpes i detaljprosjektering.",
        ],
    )


def create_analysis_register(analyses: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in analyses:
        confidence = item.get("analysis", {}).get("qa", {}).get("confidence")
        rows.append(
            {
                "Dokument": clean_pdf_text(item.get("doc_name", "")),
                "Side": item.get("page_number", ""),
                "Tegningstype": PAGE_KIND_LABELS.get(item.get("page_kind", "unknown"), item.get("page_kind", "unknown")),
                "Tittel": clean_pdf_text(item.get("page_title", "")),
                "Elementer": len(item.get("analysis", {}).get("elements") or []),
                "Konfidens": f"{float(confidence):.2f}" if confidence is not None else "-",
            }
        )
    return pd.DataFrame(rows)


def estimate_image_block_height_mm(image: Image.Image, width: float = 170, caption: str = "") -> float:
    height = width * (image.height / max(image.width, 1))
    caption_h = 6 if caption else 0
    return height + caption_h + 8


def estimate_following_block_height(pdf: BuiltlyCorporatePDF, block: Optional[Dict[str, Any]]) -> float:
    if not block:
        return 10.0
    if block.get("type") == "paragraph":
        return min(24.0, pdf.estimate_multicell_height(block.get("text", ""), 170, 5.5) + 4)
    if block.get("type") == "bullets":
        first_item = (block.get("items") or [""])[0]
        return min(22.0, pdf.estimate_multicell_height(first_item, 162, 5.2) + 5)
    if block.get("type") == "heading":
        return 15.0 if block.get("level") == 2 else 21.0
    return 10.0


def render_report_content(pdf: BuiltlyCorporatePDF, report_text: str) -> None:
    blocks = parse_report_blocks(report_text)
    if not blocks:
        return

    first_paragraph_after_heading = False
    for idx, block in enumerate(blocks):
        next_block = blocks[idx + 1] if idx + 1 < len(blocks) else None
        second_next = blocks[idx + 2] if idx + 2 < len(blocks) else None

        if block.get("type") == "heading":
            first_paragraph_after_heading = True
            if block.get("level") == 1:
                keep = estimate_following_block_height(pdf, next_block)
                if next_block and next_block.get("type") == "heading" and next_block.get("level") == 2:
                    keep += 8 + estimate_following_block_height(pdf, second_next)
                pdf.section_title(block.get("text", ""), keep_with_next=keep)
            else:
                pdf.subheading(block.get("text", ""), keep_with_next=estimate_following_block_height(pdf, next_block))
            continue

        if block.get("type") == "paragraph":
            pdf.body_paragraph(block.get("text", ""), first=first_paragraph_after_heading)
            first_paragraph_after_heading = False
            continue

        if block.get("type") == "bullets":
            pdf.bullets(block.get("items") or [])
            first_paragraph_after_heading = False


def create_fire_drawings_pdf(project_data: Dict[str, Any], brann_data: Dict[str, Any], analyses: List[Dict[str, Any]]) -> bytes:
    pdf = BuiltlyCorporatePDF("P", "mm", "A4")
    pdf.set_auto_page_break(True, margin=22)
    pdf.set_margins(18, 18, 18)
    pdf.header_left = clean_pdf_text(project_data.get("p_name", "Brannkonsept"))
    pdf.header_right = clean_pdf_text("Builtly | RIBr")
    pdf.doc_code = "Builtly-RIBr-002A"
    cover_img = analyses[0].get("annotated_image") if analyses else None
    build_cover_page(pdf, project_data, brann_data, cover_img)
    build_toc_page(pdf, include_appendices=False)

    pdf.add_page()
    pdf.section_title("GENERERTE BRANNTEGNINGER")
    pdf.highlight_box(
        "Om vedlegget",
        [
            "Hver tegning er vist som annotert vedlegg med standardisert symbolforklaring.",
            "Notater under hver tegning beskriver hvilke forhold som er markert og hva som ma kontrolleres videre.",
        ],
    )

    for item in analyses:
        title = f"{item.get('doc_name', '')} - side {item.get('page_number', '')}: {item.get('page_title', '')}"
        comparison = make_comparison_image(item["page"].image, item["annotated_image"], title)
        comparison_path = safe_temp_image(comparison, ".jpg")
        heading_text = clean_pdf_text(item.get("page_title", title))
        pdf.appendix_item_title(heading_text, keep_with_next=estimate_image_block_height_mm(comparison, width=170, caption=title) + 8)
        pdf.image_block(comparison_path, width=170, caption=title)

        notes = item.get("analysis", {}).get("analysis_notes") or []
        review = item.get("analysis", {}).get("qa", {}).get("human_review_focus") or []
        if notes:
            pdf.highlight_box("Nokkelobservasjoner", notes[:5], fill=(245, 247, 250), accent=(56, 194, 201))
        if review:
            pdf.highlight_box("Kontroller saerskilt", review[:4], fill=(255, 245, 245), accent=(229, 57, 53))

    return as_pdf_bytes(pdf)


def create_full_report_pdf(project_data: Dict[str, Any], brann_data: Dict[str, Any], report_text: str, analyses: List[Dict[str, Any]]) -> bytes:
    pdf = BuiltlyCorporatePDF("P", "mm", "A4")
    pdf.set_auto_page_break(True, margin=22)
    pdf.set_margins(18, 18, 18)
    pdf.header_left = clean_pdf_text(project_data.get("p_name", "Brannkonsept"))
    pdf.header_right = clean_pdf_text("Builtly | RIBr")
    pdf.doc_code = "Builtly-RIBr-002"
    cover_img = analyses[0].get("annotated_image") if analyses else None

    build_cover_page(pdf, project_data, brann_data, cover_img)
    build_toc_page(pdf, include_appendices=True)

    register_df = create_analysis_register(analyses)
    if not register_df.empty:
        register_path = safe_temp_image(render_table_image(register_df, "Tegningsregister", "Automatisk klassifisering og markering av opplastede sider"), ".png")
        pdf.add_page()
        pdf.section_title("TEGNINGSREGISTER")
        pdf.image_block(register_path, width=170, caption="Oversikt over sider som er analysert og konvertert til branntegningsutkast.")

    pdf.add_page()
    pdf.section_title("PROSJEKTFAKTA")
    pdf.stats_row(
        [
            ("Risikoklasse", brann_data.get("rkl", "-"), "info"),
            ("Brannklasse", brann_data.get("bkl", "-"), "fire"),
            ("Sprinkler", "Ja" if "Ja" in brann_data.get("sprinkler", "") else "Nei", "ok" if "Ja" in brann_data.get("sprinkler", "") else "warn"),
            ("Tegninger", str(len(analyses)), "info"),
        ]
    )
    facts = [
        f"Oppdragsgiver: {project_data.get('c_name', '-')}",
        f"Adresse: {project_data.get('adresse', '-')}, {project_data.get('kommune', '-')}",
        f"Gnr/Bnr: {project_data.get('gnr', '-')}/{project_data.get('bnr', '-')}",
        f"Bygningstype: {project_data.get('b_type', '-')}",
        f"Etasjer: {project_data.get('etasjer', '-')}",
        f"Areal/BTA (oppgitt): {project_data.get('bta', '-')}",
    ]
    pdf.highlight_box("Prosjektunderlag", facts)

    sanitized_report = sanitize_report_text(report_text or "")
    if sanitized_report:
        pdf.add_page()
        render_report_content(pdf, sanitized_report)

    if analyses:
        pdf.add_page()
        pdf.section_title("VEDLEGG B - BRANNTEGNINGER")
        pdf.highlight_box(
            "Vedleggene viser",
            [
                "opplastet grunnlag sammen med genererte brannmarkeringer,",
                "symbolforklaring per tegningstype, og",
                "analysepunkter / kontrollpunkter som ma gjennomgas av ansvarlig RIBr.",
            ],
        )
        for item in analyses:
            caption = f"{item.get('doc_name', '')} - side {item.get('page_number', '')} - {item.get('page_title', '')}"
            comparison = make_comparison_image(item["page"].image, item["annotated_image"], caption)
            comparison_path = safe_temp_image(comparison, ".jpg")
            heading_text = clean_pdf_text(item.get("page_title", caption))
            pdf.appendix_item_title(heading_text, keep_with_next=estimate_image_block_height_mm(comparison, width=170, caption=caption) + 8)
            pdf.image_block(comparison_path, width=170, caption=caption)
            notes = item.get("analysis", {}).get("analysis_notes") or []
            assumptions = item.get("analysis", {}).get("assumptions") or []
            if notes:
                pdf.highlight_box("Analysepunkter", notes[:5], fill=(245, 247, 250), accent=(56, 194, 201))
            if assumptions:
                pdf.highlight_box("Forutsetninger", assumptions[:4], fill=(255, 249, 235), accent=(245, 158, 11))

    return as_pdf_bytes(pdf)


# -----------------------------------------------------------------------------
# 11. RAPPORTPROMPT
# -----------------------------------------------------------------------------
def build_analysis_digest(analyses: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for item in analyses:
        analysis = item.get("analysis", {})
        lines.append(
            f"- {item.get('doc_name')} side {item.get('page_number')} ({PAGE_KIND_LABELS.get(item.get('page_kind', 'unknown'), item.get('page_kind', 'unknown'))}) - {item.get('page_title')}"
        )
        if analysis.get("drawing_summary"):
            lines.append(f"  Oppsummering: {analysis.get('drawing_summary')}")
        for note in analysis.get("analysis_notes", [])[:4]:
            lines.append(f"  Observasjon: {note}")
        for assumption in analysis.get("assumptions", [])[:3]:
            lines.append(f"  Forutsetning: {assumption}")
        if analysis.get("legend_items"):
            legend_line = ", ".join([f"{x.get('label')} ({x.get('style')})" for x in analysis.get("legend_items", [])[:6]])
            lines.append(f"  Symbolsett: {legend_line}")
    return "\n".join(lines)


def build_report_prompt(project_data: Dict[str, Any], brann_data: Dict[str, Any], analyses: List[Dict[str, Any]], manual_notes: str) -> str:
    doc_register = create_analysis_register(analyses)
    register_csv = doc_register.to_csv(index=False, sep=";") if not doc_register.empty else "Ingen analyserte sider."
    digest = build_analysis_digest(analyses)

    return f"""
Du er en senior norsk brannraadgiver (RIBr) og skal skrive et troverdig, presist og profesjonelt brannkonsept pa norsk.
Skriv som et rådgivende prosjekteringsunderlag med tydelig faghierarki, korte avsnitt og konkrete punktlister.

VIKTIG:
- Du skal bruke det analyserte tegningsgrunnlaget aktivt.
- Når du beskriver rømningsveier, innsatsveier, oppstillingsplasser, trapperom, p-kjeller eller balkonger, skal det fremga at dette er basert pa opplastet og analysert underlag.
- Ikke bruk markdown-tabeller.
- Skill tydelig mellom observerte forhold, prosjekteringsforutsetninger og forhold som ma kontrolleres i detaljfasen.
- Ikke dikt opp nøyaktige brannklasser, materialer eller mål utover det som faktisk kan begrunnes av input. Dersom noe er uklart, skriv at dette ma avklares/kontrolleres.
- Rapporten skal være bedre strukturert og mer operativ enn et enkelt AI-notat.

PROSJEKT:
Navn: {project_data.get('p_name', '-')}
Oppdragsgiver: {project_data.get('c_name', '-')}
Beskrivelse: {project_data.get('p_desc', '-')}
Adresse: {project_data.get('adresse', '-')}, {project_data.get('kommune', '-')}
Gnr/Bnr: {project_data.get('gnr', '-')}/{project_data.get('bnr', '-')}
Bygningstype: {project_data.get('b_type', '-')}
Etasjer: {project_data.get('etasjer', '-')}
BTA: {project_data.get('bta', '-')}
Regelverk: {brann_data.get('regelverk', 'TEK17 / VTEK17')}
Risikoklasse: {brann_data.get('rkl', '-')}
Brannklasse: {brann_data.get('bkl', '-')}
Sprinkler: {brann_data.get('sprinkler', '-')}
Brannalarm: {brann_data.get('alarm', '-')}
Innsatstid / lokal forutsetning: {brann_data.get('innsatstid', '-')}
Manuelle prosjektforutsetninger: {manual_notes or 'Ingen ekstra merknader'}

ANALYSERT TEGNINGSGRUNNLAG (register):
{register_csv}

OPPSUMMERT TEGNINGSANALYSE:
{digest}

SKRIV MED FOLGENDE STRUKTUR OG OVERSKRIFTER:
# 1 INNLEDNING
## 1.1 Grunnlag
## 1.2 Formelle forhold
## 1.3 Tiltakets omfang
## 1.4 Prosjekteringsforutsetninger
# 2 BRANNSTRATEGI
## 2.1 Oppsummering hovedforinger brannverntiltak
## 2.2 Fravik, usikkerheter og forhold som ma dokumenteres i detaljfase
## 2.3 Kravspesifikasjoner og ytelsesbeskrivelse
# 3 TEGNINGSANALYSE OG BRANNTEGNINGER
## 3.1 Uteomrade, atkomst og oppstillingsplasser
## 3.2 P-kjeller, innsatsvei og brannsluse
## 3.3 Boligplan, trapperom, romningsretning og redningsmulighet
# 4 OPERATIV OPPFOLGING I DETALJPROSJEKT
## 4.1 Kontrollpunkter for ARK, RIB, RIV og RIE
## 4.2 Tegninger og vedlegg som ma oppdateres
# 5 KONKLUSJON

STILKRAV:
- Tydelig faglig tone.
- Konkrete kulepunkter under kontrollpunkter og oppfolging.
- Henvis gjerne til opplastede tegninger med dokumentnavn og sidetittel nar det styrker troverdigheten.
- Ikke inkluder noen hilsen eller metakommentar.
- Start rett pa kapittel 1.
"""


def generate_report_text(project_data: Dict[str, Any], brann_data: Dict[str, Any], analyses: List[Dict[str, Any]], manual_notes: str) -> str:
    if not AI_AVAILABLE:
        return sanitize_report_text(f"""
# 1 INNLEDNING
## 1.1 Grunnlag
Rapporten er utarbeidet pa bakgrunn av prosjektdata og automatisert tegningsanalyse av opplastede tegninger. Underlaget bor kvalitetssikres av ansvarlig RIBr.
## 1.2 Formelle forhold
Brannkonseptet er et arbeidsutkast for videre prosjektering. Det ma kontrolleres mot gjeldende regelverk, kommunale forutsetninger og endelig tegningsgrunnlag.
## 1.3 Tiltakets omfang
Tiltaket omfatter {project_data.get('b_type', '-')} for prosjektet {project_data.get('p_name', '-')}. Opplastede tegninger indikerer at romning, branncelleinndeling og brannvesenets tilgang ma dokumenteres med egne branntegninger.
## 1.4 Prosjekteringsforutsetninger
Risikoklasse er satt til {brann_data.get('rkl', '-')}, brannklasse til {brann_data.get('bkl', '-')}, sprinklerstatus er {brann_data.get('sprinkler', '-')}, og alarmforutsetning er {brann_data.get('alarm', '-')}.
# 2 BRANNSTRATEGI
## 2.1 Oppsummering hovedforinger brannverntiltak
- Brannrelevante rom og soner ma markeres med tydelig branncelle- og romningslogikk i tegningene.
- Trapperom, sluser, balkonger og atkomstveier ma vises konsistent i både rapport og tegninger.
- Innsatsvei og oppstillingsplasser for brannvesenet ma dokumenteres på utomhus- og kjellerplan der dette er relevant.
## 2.2 Fravik, usikkerheter og forhold som ma dokumenteres i detaljfase
- Automatiske markeringer er forslag og ikke ferdig detaljprosjektering.
- Dorklasser, eksakte skillesoner og eventuelle fravik ma verifiseres av RIBr.
## 2.3 Kravspesifikasjoner og ytelsesbeskrivelse
Rapporten ma videreutvikles med konkrete ytelseskrav for baeree vne, brannskiller, romning, tekniske installasjoner, slokkeutstyr og tilrettelegging for rednings- og slokkemannskap.
# 3 TEGNINGSANALYSE OG BRANNTEGNINGER
## 3.1 Uteomrade, atkomst og oppstillingsplasser
Automatisert analyse identifiserer relevante utomhusplaner og markerer sannsynlig kjørbar atkomst, oppstillingsflater og innsatsveier der dette kan leses ut av underlaget.
## 3.2 P-kjeller, innsatsvei og brannsluse
P-kjellerplaner analyseres for trapper, sluser, tekniske rom og innsatsretninger. Dette ma verifiseres mot endelig strategi for angrepsvei og slangelengde.
## 3.3 Boligplan, trapperom, romningsretning og redningsmulighet
Boligplaner analyseres for trapperom, korridorer, balkonger og mulige redningsflater. Endelige brannskiller og doerklasser ma paaføres av ansvarlig fagperson.
# 4 OPERATIV OPPFOLGING I DETALJPROSJEKT
## 4.1 Kontrollpunkter for ARK, RIB, RIV og RIE
- ARK: kontroller branncellegrenser, romningsveier og dørslagretninger.
- RIB: kontroller krav til baerende konstruksjoner og dekker.
- RIV: kontroller slokkeanlegg, ventilasjon og eventuelle sjakter.
- RIE: kontroller alarm, ledesystem og styringer.
## 4.2 Tegninger og vedlegg som ma oppdateres
Branntegninger for plan, kjeller og utomhus maa oppdateres i takt med detaljprosjektering og vedlegges neste revisjon av rapporten.
# 5 KONKLUSJON
Underlaget gir et godt grunnlag for et mer profesjonelt og operativt brannkonsept, men endelige ytelser og fravik ma kvalitetssikres i detaljprosjekteringen.
""")

    model = genai.GenerativeModel(pick_model_name())
    response = model.generate_content(build_report_prompt(project_data, brann_data, analyses, manual_notes))
    text = sanitize_report_text(extract_response_text(response).strip())
    return text


# -----------------------------------------------------------------------------
# 12. UI-STYLING (BASERT PAA GEO)
# -----------------------------------------------------------------------------
st.markdown(
    """
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    :root {
        --bg: #06111a; --panel: rgba(10, 22, 35, 0.78);
        --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38bdf8; --radius-lg: 16px; --radius-xl: 24px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1320px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important; }
    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, div[data-baseweb="select"] * { background-color: transparent !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; border: none !important; box-shadow: none !important; }
    div[data-baseweb="base-input"]:focus-within, div[data-baseweb="select"] > div:focus-within, .stTextArea > div > div > div:focus-within { border-color: var(--accent) !important; box-shadow: 0 0 0 1px rgba(56, 194, 201, 0.5) !important; }
    ul[data-baseweb="menu"] { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; }
    ul[data-baseweb="menu"] li { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
    ul[data-baseweb="menu"] li:hover { background-color: rgba(56, 194, 201, 0.1) !important; }
    div[data-testid="InputInstructions"], div[data-testid="InputInstructions"] > span { color: #9fb0c3 !important; -webkit-text-fill-color: #9fb0c3 !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label, .stRadio label { color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important; }
    div[data-testid="stExpander"] details, div[data-testid="stExpander"] details summary, div[data-testid="stExpander"] { background-color: #0c1520 !important; color: #f5f7fb !important; border-radius: 12px !important; }
    div[data-testid="stExpander"] details summary p { color: #f5f7fb !important; font-weight: 650 !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; margin-bottom: 1rem !important; }
    div[data-testid="stExpanderDetails"] { background: transparent !important; color: #f5f7fb !important; }
    [data-testid="stFileUploaderDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important; border-radius: 12px !important; padding: 2rem !important; }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #38c2c9 !important; background-color: rgba(56, 194, 201, 0.05) !important; }
    [data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
    [data-testid="stFileUploaderFileData"] { background-color: rgba(255,255,255,0.02) !important; color: #f5f7fb !important; border-radius: 8px !important; }
    [data-testid="stAlert"] { background-color: rgba(56, 194, 201, 0.05) !important; border: 1px solid rgba(56, 194, 201, 0.2) !important; border-radius: 12px !important; }
    [data-testid="stAlert"] * { color: #f5f7fb !important; }
</style>
""",
    unsafe_allow_html=True,
)


def render_mouse_canvas_editor(page: SourcePage, elements: List[Dict[str, Any]], bridge_label: str, component_key: str) -> None:
    all_elements = [normalize_element(e) for e in (elements or [])]
    image_data_url = image_to_data_url(page.image, max_width=1500)
    payload = {"image": image_data_url, "elements": all_elements, "bridgeLabel": bridge_label}

    html = f"""
    <div style="font-family:system-ui,-apple-system,sans-serif;color:#e2e8f0">
      <div style="display:flex;gap:3px;padding:6px 8px;background:#0a1929;border:1px solid #1a2a3a;border-radius:10px 10px 0 0;flex-wrap:wrap;align-items:center">
        <button onclick="FE.setTool('select')" id="t_select" class="ft active" style="--tc:#38bdf8">Velg</button>
        <span style="width:1px;height:16px;background:#1a2a3a"></span>
        <button onclick="FE.setTool('branncelle')" id="t_branncelle" class="ft" style="--tc:#e53935">Branncelle</button>
        <button onclick="FE.setTool('romning')" id="t_romning" class="ft" style="--tc:#22c55e">Romningsvei</button>
        <button onclick="FE.setTool('romning_pil')" id="t_romning_pil" class="ft" style="--tc:#22c55e">Romningsretn.</button>
        <button onclick="FE.setTool('redning')" id="t_redning" class="ft" style="--tc:#f59e0b">Redningsvei</button>
        <button onclick="FE.setTool('atkomst')" id="t_atkomst" class="ft" style="--tc:#2563eb">Brannatkomst</button>
        <button onclick="FE.setTool('innsats')" id="t_innsats" class="ft" style="--tc:#ef4444">Innsatsvei</button>
        <button onclick="FE.setTool('kjerne')" id="t_kjerne" class="ft" style="--tc:#2e7d32">Kjerne</button>
        <button onclick="FE.setTool('oppstilling')" id="t_oppstilling" class="ft" style="--tc:#ef4444">Oppstilling</button>
        <button onclick="FE.setTool('dor')" id="t_dor" class="ft" style="--tc:#ef4444">Branndor</button>
        <button onclick="FE.setTool('notat')" id="t_notat" class="ft" style="--tc:#94a3b8">Notat</button>
        <span style="flex:1"></span>
        <button onclick="FE.deleteSelected()" style="background:rgba(239,68,68,0.15);color:#ef4444;border-color:rgba(239,68,68,0.3)">Slett</button>
        <button onclick="FE.sendToStreamlit()" style="background:#38bdf8;color:#06111a;font-weight:700;border-color:#38bdf8">Lagre</button>
      </div>
      <canvas id="FC" style="width:100%;display:block;background:#0d1b2a;border:1px solid #1a2a3a;border-top:none;cursor:crosshair"></canvas>
      <div style="display:flex;gap:8px;padding:5px 10px;background:#0a1929;border:1px solid #1a2a3a;border-top:none;border-radius:0 0 10px 10px;align-items:center">
        <span id="FE_status" style="font-size:10px;color:#475569;font-family:monospace;flex:1"></span>
        <label style="font-size:10px;color:#64748b">Etikett:</label>
        <input id="FE_label" type="text" style="background:#1a2a3a;border:1px solid #334155;border-radius:4px;color:#e2e8f0;padding:2px 8px;font-size:11px;width:160px" oninput="FE.updateLabel(this.value)"/>
      </div>
      <textarea id="FE_export" style="display:none">{json.dumps(all_elements, ensure_ascii=False)}</textarea>
    </div>
    <style>.ft{{background:rgba(30,41,59,0.8);color:#94a3b8;border:1px solid #334155;border-radius:5px;padding:3px 8px;font-size:11px;cursor:pointer;font-weight:500;transition:all .15s}}.ft:hover{{background:rgba(56,189,248,0.1);color:#e2e8f0}}.ft.active{{background:var(--tc,#38bdf8);color:#fff;font-weight:700;border-color:var(--tc,#38bdf8)}}</style>
    <script>
    window.FE=(function(){{
      const P={json.dumps(payload, ensure_ascii=False)};
      const cv=document.getElementById('FC'),ctx=cv.getContext('2d'),st=document.getElementById('FE_status'),lb=document.getElementById('FE_label'),ex=document.getElementById('FE_export');
      const img=new Image();img.src=P.image;
      let els=JSON.parse(JSON.stringify(P.elements||[]));
      let tool='select',sel=-1,drag=null,sp=null,IW=0,IH=0;
      const TMAP={{branncelle:['box','fire_compartment','Branncelle'],romning:['area_fill','escape_route','Romningsvei'],romning_pil:['arrow','escape_arrow','Romningsretning'],redning:['area_fill','rescue_route','Redningsvei / balkong'],atkomst:['area_fill','fire_access','Brannatkomst'],innsats:['arrow','attack_route','Innsatsvei'],kjerne:['box','core_fill','Trapperom / kjerne'],oppstilling:['box','staging_area','Oppstillingsplass'],dor:['box','door_class','EI2 30-C'],notat:['box','note_box','Notat']}};
      const SC={{fire_compartment:['#e53935',null],escape_route:['#2e7d32','rgba(110,231,183,0.35)'],escape_arrow:['#22c55e',null],rescue_route:['#f59e0b','rgba(253,186,116,0.3)'],attack_route:['#ef4444',null],fire_access:['#2563eb','rgba(219,234,254,0.35)'],staging_area:['#ef4444','rgba(252,165,165,0.3)'],note_box:['#94a3b8','rgba(255,255,255,0.85)'],door_class:['#ef4444','rgba(255,255,255,0.9)'],core_fill:['#2e7d32','rgba(187,247,208,0.3)']}};
      function uid(){{return Math.random().toString(36).slice(2,12)}}
      function n2p(v,s){{return v*s}}
      function p2n(v,s){{return Math.max(0,Math.min(1,v/s))}}
      function gP(e){{const r=cv.getBoundingClientRect();return{{x:(e.clientX-r.left)*(cv.width/r.width),y:(e.clientY-r.top)*(cv.height/r.height)}}}}
      function dist(a,b,c,d){{return Math.sqrt((c-a)**2+(d-b)**2)}}
      function render(){{
        if(!img.complete)return;ctx.clearRect(0,0,cv.width,cv.height);ctx.drawImage(img,0,0,IW,IH);
        els.forEach((e,i)=>{{
          const s=SC[e.style]||SC.fire_compartment,stroke=s[0],fill=s[1],isSel=i===sel;
          ctx.lineWidth=isSel?4:2.5;ctx.strokeStyle=stroke;
          if(e.rect){{
            const x0=n2p(e.rect[0],IW),y0=n2p(e.rect[1],IH),x1=n2p(e.rect[2],IW),y1=n2p(e.rect[3],IH),w=x1-x0,h=y1-y0;
            if(fill){{ctx.fillStyle=fill;ctx.fillRect(x0,y0,w,h)}}
            if(e.style==='fire_compartment'){{ctx.setLineDash([8,5]);ctx.strokeRect(x0,y0,w,h);ctx.setLineDash([])}}else{{ctx.strokeRect(x0,y0,w,h)}}
            if(isSel){{ctx.fillStyle='#fff';ctx.strokeStyle=stroke;ctx.lineWidth=2;ctx.beginPath();ctx.rect(x1-5,y1-5,10,10);ctx.fill();ctx.stroke()}}
            ctx.font='bold 11px system-ui';const lbl=e.label||'';const tw=ctx.measureText(lbl).width;
            ctx.fillStyle='rgba(10,25,41,0.75)';ctx.fillRect(x0,y0-16,tw+8,15);ctx.fillStyle=stroke;ctx.fillText(lbl,x0+4,y0-4);
          }}
          if(e.points&&e.points.length>=2){{
            const p1=[n2p(e.points[0][0],IW),n2p(e.points[0][1],IH)],p2=[n2p(e.points[e.points.length-1][0],IW),n2p(e.points[e.points.length-1][1],IH)];
            ctx.beginPath();ctx.moveTo(p1[0],p1[1]);ctx.lineTo(p2[0],p2[1]);ctx.stroke();
            const ang=Math.atan2(p2[1]-p1[1],p2[0]-p1[0]),hd=14;
            ctx.beginPath();ctx.moveTo(p2[0],p2[1]);ctx.lineTo(p2[0]-hd*Math.cos(ang-0.5),p2[1]-hd*Math.sin(ang-0.5));ctx.lineTo(p2[0]-hd*Math.cos(ang+0.5),p2[1]-hd*Math.sin(ang+0.5));ctx.closePath();ctx.fillStyle=stroke;ctx.fill();
            if(isSel){{[p1,p2].forEach(pt=>{{ctx.beginPath();ctx.arc(pt[0],pt[1],6,0,Math.PI*2);ctx.fillStyle='#fff';ctx.fill();ctx.strokeStyle=stroke;ctx.lineWidth=2;ctx.stroke()}})}}
            const mx=(p1[0]+p2[0])/2,my=(p1[1]+p2[1])/2;ctx.font='bold 10px system-ui';const lbl=e.label||'';const tw=ctx.measureText(lbl).width;
            ctx.fillStyle='rgba(10,25,41,0.75)';ctx.fillRect(mx-tw/2-3,my-18,tw+6,14);ctx.fillStyle=stroke;ctx.fillText(lbl,mx-tw/2,my-7);
          }}
        }});
        st.textContent=els.length+' elementer | '+(tool==='select'?'Velg/flytt':TMAP[tool]?TMAP[tool][2]:tool);
        ex.value=JSON.stringify(els,null,2);
      }}
      function resize(){{const mw=cv.parentElement.clientWidth||980,r=Math.min(1,mw/img.width);IW=Math.max(1,Math.round(img.width*r));IH=Math.max(1,Math.round(img.height*r));cv.width=IW;cv.height=IH;render()}}
      function hitTest(x,y){{for(let i=els.length-1;i>=0;i--){{const e=els[i];if(e.rect){{const x0=n2p(e.rect[0],IW),y0=n2p(e.rect[1],IH),x1=n2p(e.rect[2],IW),y1=n2p(e.rect[3],IH);if(x>=Math.min(x0,x1)&&x<=Math.max(x0,x1)&&y>=Math.min(y0,y1)&&y<=Math.max(y0,y1))return i}}if(e.points&&e.points.length>=2){{const p1=[n2p(e.points[0][0],IW),n2p(e.points[0][1],IH)],p2=[n2p(e.points[e.points.length-1][0],IW),n2p(e.points[e.points.length-1][1],IH)];const dx=p2[0]-p1[0],dy=p2[1]-p1[1],l2=dx*dx+dy*dy;let t=l2===0?0:((x-p1[0])*dx+(y-p1[1])*dy)/l2;t=Math.max(0,Math.min(1,t));if(dist(x,y,p1[0]+t*dx,p1[1]+t*dy)<14)return i}}}}return -1}}
      cv.addEventListener('mousedown',function(e){{const p=gP(e);sp=p;if(tool==='select'){{sel=hitTest(p.x,p.y);if(sel>=0){{const el=els[sel];lb.value=el.label||'';if(el.rect){{const x1=n2p(el.rect[2],IW),y1=n2p(el.rect[3],IH);drag=Math.abs(p.x-x1)<12&&Math.abs(p.y-y1)<12?'resize':'move'}}else if(el.points){{const p1=[n2p(el.points[0][0],IW),n2p(el.points[0][1],IH)],p2=[n2p(el.points[el.points.length-1][0],IW),n2p(el.points[el.points.length-1][1],IH)];drag=dist(p.x,p.y,p1[0],p1[1])<12?'pt0':dist(p.x,p.y,p2[0],p2[1])<12?'pt1':'move'}}}}else{{lb.value=''}}}}else{{const tm=TMAP[tool];if(!tm)return;const[et,es,el]=tm;if(et==='box'||et==='area_fill'){{const nn=p2n(p.x,IW),nm=p2n(p.y,IH);els.push({{element_id:uid(),type:et,style:es,label:el,rect:[nn,nm,nn,nm]}});sel=els.length-1;drag='draw';lb.value=el}}else if(et==='arrow'){{const nn=p2n(p.x,IW),nm=p2n(p.y,IH);els.push({{element_id:uid(),type:'arrow',style:es,label:el,points:[[nn,nm],[nn,nm]]}});sel=els.length-1;drag='drawArrow';lb.value=el}}}}render()}});
      cv.addEventListener('mousemove',function(e){{if(sel<0||!drag||!sp)return;const p=gP(e),el=els[sel];if(drag==='move'&&el.rect){{const dx=(p.x-sp.x)/IW,dy=(p.y-sp.y)/IH;el.rect=[el.rect[0]+dx,el.rect[1]+dy,el.rect[2]+dx,el.rect[3]+dy].map(v=>Math.max(0,Math.min(1,v)));sp=p}}else if((drag==='resize'||drag==='draw')&&el.rect){{el.rect[2]=p2n(p.x,IW);el.rect[3]=p2n(p.y,IH)}}else if(drag==='move'&&el.points){{const dx=(p.x-sp.x)/IW,dy=(p.y-sp.y)/IH;el.points=el.points.map(pt=>[Math.max(0,Math.min(1,pt[0]+dx)),Math.max(0,Math.min(1,pt[1]+dy))]);sp=p}}else if(drag==='pt0'&&el.points){{el.points[0]=[p2n(p.x,IW),p2n(p.y,IH)]}}else if((drag==='pt1'||drag==='drawArrow')&&el.points){{el.points[el.points.length-1]=[p2n(p.x,IW),p2n(p.y,IH)]}}render()}});
      window.addEventListener('mouseup',function(){{if(sel>=0&&els[sel]&&els[sel].rect){{const r=els[sel].rect;els[sel].rect=[Math.min(r[0],r[2]),Math.min(r[1],r[3]),Math.max(r[0],r[2]),Math.max(r[1],r[3])]}}drag=null;render()}});
      document.addEventListener('keydown',function(e){{if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;if((e.key==='Delete'||e.key==='Backspace')&&sel>=0){{els.splice(sel,1);sel=-1;lb.value='';render()}}}});
      return{{
        setTool:function(t){{tool=t;sel=-1;document.querySelectorAll('.ft').forEach(b=>b.classList.remove('active'));const btn=document.getElementById('t_'+t);if(btn)btn.classList.add('active');cv.style.cursor=t==='select'?'default':'crosshair'}},
        deleteSelected:function(){{if(sel>=0){{els.splice(sel,1);sel=-1;lb.value='';render()}}}},
        updateLabel:function(v){{if(sel>=0&&els[sel]){{els[sel].label=v;render()}}}},
        sendToStreamlit:function(){{
          ex.value=JSON.stringify(els,null,2);
          try{{const ta=window.parent.document.querySelector('textarea[aria-label="'+P.bridgeLabel+'"]');if(!ta)throw 0;const setter=Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;setter.call(ta,ex.value);ta.dispatchEvent(new Event('input',{{bubbles:true}}));ta.dispatchEvent(new Event('change',{{bubbles:true}}));st.textContent='Lagret! Klikk Bruk museendringer under.'}}catch(e){{st.textContent='Kopier JSON manuelt.';ex.style.display='block'}}
        }}
      }};
      img.onload=resize;window.addEventListener('resize',resize);
    }})();
    </script>
    """
    html = f"<!-- {component_key} -->\n" + html
    components.html(html, height=820, scrolling=False)



def render_analysis_editor(item: Dict[str, Any]) -> None:
    page = item["page"]
    item.setdefault("locked", False)
    item["analysis"] = normalize_analysis_payload(item.get("analysis") or {}, item.get("page_kind") or page.page_kind)
    item["analysis"]["page_kind"] = resolve_page_kind_for_page(page, item["analysis"].get("page_kind", page.page_kind), item["analysis"].get("page_title") or page.page_title)
    item["page_kind"] = item["analysis"].get("page_kind", page.page_kind)
    item["page_title"] = clean_pdf_text(item.get("page_title") or item["analysis"].get("page_title") or page.page_title)

    key_root = re.sub(r"[^0-9A-Za-z_]+", "_", page.uid)
    analysis = item["analysis"]
    page_kind_options = list(PAGE_KIND_LABELS.keys())
    current_kind = item.get("page_kind", page.page_kind)
    kind_index = page_kind_options.index(current_kind) if current_kind in page_kind_options else 0

    st.caption("V3: bedre sideklassifisering, musebasert finredigering, AI-revisjon per side og mer ryddige callouts i generert branntegning.")

    with st.form(key=f"{key_root}_page_meta_form"):
        selected_kind = st.selectbox(
            "Tegningstype for siden",
            page_kind_options,
            index=kind_index,
            format_func=lambda x: PAGE_KIND_LABELS.get(x, x),
        )
        new_title = st.text_input("Tegningstittel", value=item.get("page_title", ""))
        new_summary = st.text_area("Oppsummering", value=analysis.get("drawing_summary", ""), height=100)
        meta_cols = st.columns(2)
        new_notes = meta_cols[0].text_area(
            "Analysepunkter (en per linje)",
            value="\n".join(analysis.get("analysis_notes", [])),
            height=140,
        )
        new_assumptions = meta_cols[1].text_area(
            "Forutsetninger (en per linje)",
            value="\n".join(analysis.get("assumptions", [])),
            height=140,
        )
        lock_col, save_col = st.columns([1, 2])
        locked_value = lock_col.checkbox("Lås denne siden mot ny AI-endring", value=item.get("locked", False))
        save_page = save_col.form_submit_button("Oppdater sideinfo og tekst", use_container_width=True)
    if save_page:
        item["page_title"] = clean_pdf_text(new_title) or item.get("page_title", page.page_title)
        analysis["page_title"] = item["page_title"]
        analysis["page_kind"] = resolve_page_kind_for_page(page, selected_kind, item["page_title"])
        item["page_kind"] = analysis["page_kind"]
        item["locked"] = bool(locked_value)
        analysis["drawing_summary"] = ironclad_text_formatter(new_summary)
        analysis["analysis_notes"] = split_lines_to_list(new_notes)
        analysis["assumptions"] = split_lines_to_list(new_assumptions)
        item["analysis"] = analysis
        refresh_analysis_item(item)
        request_rerun()
    else:
        item["locked"] = bool(locked_value)

    st.markdown("**Museeditor (beta)**")
    st.caption("Museeditoren er laget for raske visuelle justeringer av bokser, felt og piler. Avanserte polylinjer kan fortsatt finjusteres lenger ned med de vanlige feltene.")
    bridge_key = f"{key_root}_mouse_bridge"
    bridge_label = f"MOUSE_BRIDGE__{key_root}"
    if bridge_key not in st.session_state:
        st.session_state[bridge_key] = json.dumps(analysis.get("elements", []), ensure_ascii=False, indent=2)
    render_mouse_canvas_editor(page, analysis.get("elements", []), bridge_label=bridge_label, component_key=f"{key_root}_mouse_component")
    st.text_area(
        "Museeditor-data (teknisk buffer / fallback)",
        key=bridge_key,
        height=120,
        help="Denne brukes av museeditoren. Du kan også lime inn JSON manuelt her hvis automatisk overføring i nettleseren din er blokkert.",
    )
    mcols = st.columns(3)
    if mcols[0].button("Bruk museendringer", key=f"{key_root}_apply_mouse", use_container_width=True):
        try:
            parsed = json.loads(st.session_state.get(bridge_key, "[]") or "[]")
            if not isinstance(parsed, list):
                raise ValueError("Museeditor-data ma vaere en liste med elementer.")
            analysis["elements"] = [normalize_element(element) for element in parsed]
            item["analysis"] = analysis
            refresh_analysis_item(item)
            st.success("Museendringer er lagt inn i siden.")
            request_rerun()
        except Exception as exc:
            st.error(f"Klarte ikke a lese museeditor-data: {exc}")
    if mcols[1].button("Nullstill musebuffer", key=f"{key_root}_reset_mouse", use_container_width=True):
        st.session_state[bridge_key] = json.dumps(item.get("analysis", {}).get("elements", []), ensure_ascii=False, indent=2)
        request_rerun()
    if mcols[2].button("Sync buffer fra dagens elementer", key=f"{key_root}_sync_mouse", use_container_width=True):
        st.session_state[bridge_key] = json.dumps(item.get("analysis", {}).get("elements", []), ensure_ascii=False, indent=2)
        st.success("Musebuffer er oppdatert fra gjeldende analyse.")

    st.markdown("**AI-revisjon av denne siden**")
    ai_instruction_key = f"{key_root}_ai_instruction"
    ai_instruction = st.text_area(
        "Gi AI en konkret instruks for denne siden",
        key=ai_instruction_key,
        height=110,
        placeholder="Eksempel: Les denne siden som p-kjeller, fjern oppstillingsplass, og trekk innsatsvei inn mot trapp i øst.",
    )
    ai_cols = st.columns(2)
    if ai_cols[0].button(
        "Kjør AI-revisjon på denne siden",
        key=f"{key_root}_run_ai_revision",
        use_container_width=True,
        disabled=item.get("locked", False),
    ):
        if not ai_instruction.strip():
            st.warning("Skriv en konkret instruks for AI-revisjonen først.")
        elif not AI_AVAILABLE:
            st.warning("Google AI er ikke tilgjengelig i denne kjøringen.")
        else:
            with st.spinner("AI reviderer markeringene for denne siden..."):
                revised = refine_analysis_with_instruction(page, item["analysis"], brann_data, manual_notes, ai_instruction)
                item["analysis"] = revised
                item["page_kind"] = revised.get("page_kind", page.page_kind)
                item["page_title"] = clean_pdf_text(revised.get("page_title") or item.get("page_title") or page.page_title)
                refresh_analysis_item(item)
            st.success("Siden er oppdatert med AI-revisjon.")
            request_rerun()
    if ai_cols[1].button("Tøm AI-instruks", key=f"{key_root}_clear_ai_revision", use_container_width=True):
        st.session_state[ai_instruction_key] = ""
        request_rerun()

    fill_style = "staging_area" if item.get("page_kind") == "site_plan" else "escape_route"
    arrow_style = "attack_route" if item.get("page_kind") in {"site_plan", "parking_plan", "general_plan"} else "escape_arrow"

    st.markdown("**Legg til nytt overlayelement**")
    add_cols = st.columns(4)
    if add_cols[0].button("Ny boks", key=f"{key_root}_add_box", use_container_width=True):
        analysis.setdefault("elements", []).append(default_element_for_type("box", "fire_compartment"))
        item["analysis"] = analysis
        refresh_analysis_item(item)
        request_rerun()
    if add_cols[1].button("Nytt felt", key=f"{key_root}_add_fill", use_container_width=True):
        analysis.setdefault("elements", []).append(default_element_for_type("area_fill", fill_style))
        item["analysis"] = analysis
        refresh_analysis_item(item)
        request_rerun()
    if add_cols[2].button("Ny pil", key=f"{key_root}_add_arrow", use_container_width=True):
        analysis.setdefault("elements", []).append(default_element_for_type("arrow", arrow_style))
        item["analysis"] = analysis
        refresh_analysis_item(item)
        request_rerun()
    if add_cols[3].button("Ny notatboks", key=f"{key_root}_add_note", use_container_width=True):
        analysis.setdefault("elements", []).append(default_element_for_type("note_box", "note_box"))
        item["analysis"] = analysis
        refresh_analysis_item(item)
        request_rerun()

    rect_types = {"box", "area_fill", "note_box", "door_tag"}
    type_options = ["box", "area_fill", "arrow", "band", "dashed_polyline", "polyline", "note_box", "door_tag"]
    style_options = list(FIRE_STYLE_LIBRARY.keys())

    if not analysis.get("elements"):
        st.info("Ingen overlay-elementer ennå. Legg til elementer over, eller kjør ny analyse.")
        return

    st.markdown("**Eksisterende elementer**")
    for idx, element in enumerate(list(analysis.get("elements") or [])):
        element = normalize_element(element)
        analysis["elements"][idx] = element
        element_id = element.get("element_id") or f"{key_root}_{idx}"
        expander_title = f"Element {idx + 1}: {element_display_name(element)}"
        with st.expander(expander_title, expanded=False):
            with st.form(key=f"{key_root}_{element_id}_form"):
                type_index = type_options.index(element.get("type")) if element.get("type") in type_options else 0
                style_index = style_options.index(element.get("style")) if element.get("style") in style_options else 0
                etype = st.selectbox("Type", type_options, index=type_index)
                style = st.selectbox("Stil", style_options, index=style_index)
                label = st.text_input("Etikett", value=element.get("label", ""))

                points_text = ""
                if etype in rect_types:
                    rect = element.get("rect") or [0.62, 0.12, 0.82, 0.22]
                    cols = st.columns(4)
                    x0 = cols[0].number_input("x0", min_value=0.0, max_value=1.0, value=float(rect[0]), step=0.01, format="%.3f", key=f"{key_root}_{element_id}_x0")
                    y0 = cols[1].number_input("y0", min_value=0.0, max_value=1.0, value=float(rect[1]), step=0.01, format="%.3f", key=f"{key_root}_{element_id}_y0")
                    x1 = cols[2].number_input("x1", min_value=0.0, max_value=1.0, value=float(rect[2]), step=0.01, format="%.3f", key=f"{key_root}_{element_id}_x1")
                    y1 = cols[3].number_input("y1", min_value=0.0, max_value=1.0, value=float(rect[3]), step=0.01, format="%.3f", key=f"{key_root}_{element_id}_y1")
                else:
                    points_text = st.text_area(
                        "Punkter (JSON)",
                        value=points_to_text(element.get("points") or []),
                        height=150,
                    )

                btn_cols = st.columns(3)
                save_element = btn_cols[0].form_submit_button("Oppdater element", use_container_width=True)
                duplicate_element = btn_cols[1].form_submit_button("Dupliser", use_container_width=True)
                delete_element = btn_cols[2].form_submit_button("Slett", use_container_width=True)

            if save_element:
                updated = deepcopy(element)
                updated["type"] = etype
                updated["style"] = style
                updated["label"] = clean_pdf_text(label) or default_label_for_style(style)
                if etype in rect_types:
                    updated["rect"] = [x0, y0, x1, y1]
                    updated.pop("points", None)
                else:
                    try:
                        updated["points"] = parse_points_text(points_text)
                        updated.pop("rect", None)
                    except ValueError as exc:
                        st.error(str(exc))
                        updated = None
                if updated is not None:
                    analysis["elements"][idx] = normalize_element(updated)
                    item["analysis"] = analysis
                    refresh_analysis_item(item)
                    request_rerun()

            if duplicate_element:
                duplicate = deepcopy(element)
                duplicate["element_id"] = uuid4().hex[:10]
                if duplicate.get("rect"):
                    x0, y0, x1, y1 = duplicate["rect"]
                    duplicate["rect"] = [clamp01(x0 + 0.02), clamp01(y0 + 0.02), clamp01(x1 + 0.02), clamp01(y1 + 0.02)]
                if duplicate.get("points"):
                    duplicate["points"] = [[clamp01(pt[0] + 0.02), clamp01(pt[1] + 0.02)] for pt in duplicate["points"]]
                analysis["elements"].insert(idx + 1, normalize_element(duplicate))
                item["analysis"] = analysis
                refresh_analysis_item(item)
                request_rerun()

            if delete_element:
                analysis["elements"].pop(idx)
                item["analysis"] = analysis
                refresh_analysis_item(item)
                request_rerun()


# -----------------------------------------------------------------------------
# 13. HEADER / TOPP
# -----------------------------------------------------------------------------
top_l, top_r = st.columns([4, 1])
with top_l:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else "<h2 style='margin:0; color:white;'>Builtly</h2>"
    render_html(logo_html)
with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("← Tilbake til SSOT", use_container_width=True, type="secondary"):
        target_page = find_page("Project")
        if target_page:
            st.switch_page(target_page)

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
st.markdown("<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🔥 Brannkonsept (RIBr)</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #9fb0c3; font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for brannteknisk konsept, tegningsanalyse og automatiserte branntegninger.</p>", unsafe_allow_html=True)

if AI_AVAILABLE:
    st.success("✅ Google AI er tilgjengelig. Modulen bruker multimodal analyse for å tolke plantegninger og generere branntegninger.")
else:
    st.warning("⚠️ Google AI-nokkel mangler eller biblioteket er ikke tilgjengelig. Modulen kjører i heuristisk fallbackmodus. Branntegninger og rapport kan fortsatt genereres, men med enklere analyse.")

st.info(f"Prosjektdata for **{pd_state['p_name']}** er synkronisert fra SSOT.")


# -----------------------------------------------------------------------------
# 14. UI - FORUTSETNINGER
# -----------------------------------------------------------------------------
with st.expander("1. Branntekniske forutsetninger", expanded=True):
    col1, col2, col3 = st.columns(3)
    rkl = col1.selectbox("Risikoklasse (RKL)", ["RKL 1", "RKL 2", "RKL 3", "RKL 4", "RKL 5", "RKL 6"], index=3)
    bkl = col2.selectbox("Brannklasse (BKL)", ["BKL 1", "BKL 2", "BKL 3", "BKL 4"], index=1)
    innsatstid = col3.text_input("Innsatstid / lokal forutsetning", value="Innenfor 10 minutter")

    col4, col5 = st.columns(2)
    spr_opt = col4.radio("Slokkeanlegg", ["Ja, fullsprinklet", "Ja, boligsprinkler + sprinkler i fellesareal", "Nei, u-sprinklet"], index=0)
    alarm_opt = col5.radio("Brannalarmanlegg", ["Heldekkende (Kategori 2)", "Delvis dekning", "Manuell varsling"], index=0)

    regelverk = st.text_input("Regelverk / prosjekteringsforutsetning", value="TEK17 / VTEK17")
    manual_notes = st.text_area(
        "Manuelle prosjektmerknader til AI-en",
        value="Bruk et profesjonelt RIBr-uttrykk. Marker kun det som er synlig eller rimelig faglig begrunnet, og legg inn tydelige kontrollpunkter for detaljprosjekt.",
        height=120,
    )

    brann_data = {
        "rkl": rkl,
        "bkl": bkl,
        "sprinkler": spr_opt,
        "alarm": alarm_opt,
        "innsatstid": innsatstid,
        "regelverk": regelverk,
    }


# -----------------------------------------------------------------------------
# 15. UI - OPPLASTING
# -----------------------------------------------------------------------------
with st.expander("2. Last opp grunnlag og referanser", expanded=True):
    source_files = st.file_uploader(
        "Last opp tegninger som skal konverteres til branntegninger (PDF / PNG / JPG)",
        accept_multiple_files=True,
        type=["pdf", "png", "jpg", "jpeg"],
        key="brann_source_upload",
    )
    reference_files = st.file_uploader(
        "Valgfrie referanser (f.eks. tidligere brannrapport eller eksempler på branntegninger)",
        accept_multiple_files=True,
        type=["pdf", "png", "jpg", "jpeg"],
        key="brann_reference_upload",
        help="Disse brukes ikke som fasit, men kan hjelpe med stil- og kontekstforstaelse dersom du velger a utvide modulen senere.",
    )

    if st.button("Klargjør dokumenter", type="secondary"):
        with st.spinner("Leser og klassifiserer opplastede dokumenter..."):
            st.session_state.brann_source_pages = load_uploaded_pages(source_files)
            st.session_state.brann_reference_docs = [f.name for f in reference_files or []]
            st.session_state.brann_analyses = []
            st.session_state.generated_fire_drawings_pdf = None
            st.session_state.generated_pdf = None
            st.session_state.generated_report_text = ""
            st.session_state.brann_manual_edits_dirty = False
        st.success(f"Klargjort {len(st.session_state.brann_source_pages)} sider for tegningsanalyse.")

    source_pages: List[SourcePage] = st.session_state.brann_source_pages
    if source_pages:
        st.markdown("##### Registrert grunnlag")
        register_df = build_source_register(source_pages)
        st.dataframe(register_df, use_container_width=True, hide_index=True)
    else:
        st.info("Ingen sider er klargjort ennå.")


# -----------------------------------------------------------------------------
# 16. UI - ANALYSE OG BRANNTEGNINGER
# -----------------------------------------------------------------------------
with st.expander("3. Analyser tegninger og generer branntegninger", expanded=True):
    source_pages = st.session_state.brann_source_pages

    if not source_pages:
        st.info("Last opp og klargjor dokumenter i steg 2 forst.")
    else:
        page_options = {
            f"{page.doc_name} | side {page.page_number} | {PAGE_KIND_LABELS.get(page.page_kind, page.page_kind)} | {page.page_title}": page.uid
            for page in source_pages
        }
        selected_labels = st.multiselect(
            "Velg hvilke sider som skal analyseres",
            list(page_options.keys()),
            default=list(page_options.keys()) if len(page_options) <= 8 else list(page_options.keys())[:8],
        )
        include_non_drawable = st.checkbox("Inkluder sider som er klassifisert som snitt / uklassifisert", value=False)
        selected_uids = {page_options[label] for label in selected_labels}

        if st.button("🎨 Analyser og konverter til branntegninger", type="primary", use_container_width=True):
            candidates = [page for page in source_pages if page.uid in selected_uids]
            if not include_non_drawable:
                candidates = [page for page in candidates if page.page_kind in DRAWABLE_PAGE_KINDS]
            if not candidates:
                st.warning("Ingen egnede sider er valgt.")
            else:
                analyses: List[Dict[str, Any]] = []
                progress = st.progress(0.0)
                for idx, page in enumerate(candidates, start=1):
                    analysis = analyze_page(page, brann_data, manual_notes)
                    annotated_image = render_overlay(page, analysis)
                    analyses.append(
                        {
                            "doc_name": page.doc_name,
                            "page_number": page.page_number,
                            "page_title": clean_pdf_text(analysis.get("page_title") or page.page_title),
                            "page_kind": analysis.get("page_kind", page.page_kind),
                            "page": page,
                            "analysis": analysis,
                            "annotated_image": annotated_image,
                        }
                    )
                    progress.progress(idx / max(len(candidates), 1))
                st.session_state.brann_analyses = analyses
                try:
                    st.session_state.generated_fire_drawings_pdf = create_fire_drawings_pdf(pd_state, brann_data, analyses)
                    st.session_state.generated_pdf = None
                    st.session_state.generated_report_text = ""
                    st.session_state.brann_manual_edits_dirty = False
                    st.success(f"Generert {len(analyses)} branntegningsutkast.")
                except Exception as exc:
                    st.session_state.generated_fire_drawings_pdf = None
                    st.session_state.generated_pdf = None
                    st.session_state.generated_report_text = ""
                    st.error(f"Branntegning-PDF feilet etter analyse: {exc}")

    analyses = st.session_state.brann_analyses
    if analyses:
        if st.session_state.brann_manual_edits_dirty:
            st.warning("Manuelle endringer er registrert. Oppdater branntegning-PDF eller generer komplett rapport pa nytt i steg 4.")
            if st.button("Oppdater branntegning-PDF fra redigerte markeringer", key="refresh_fire_pdf_after_edits", type="secondary", use_container_width=True):
                with st.spinner("Oppdaterer vedlegg med redigerte markeringer..."):
                    try:
                        st.session_state.generated_fire_drawings_pdf = create_fire_drawings_pdf(pd_state, brann_data, analyses)
                        st.session_state.brann_manual_edits_dirty = False
                        st.success("Branntegning-PDF er oppdatert.")
                    except Exception as exc:
                        st.session_state.generated_fire_drawings_pdf = None
                        st.error(f"Kunne ikke oppdatere branntegning-PDF: {exc}")

        st.markdown("##### Forhaandsvisning av genererte branntegninger")
        for item in analyses:
            item["analysis"] = normalize_analysis_payload(item.get("analysis") or {}, item.get("page_kind") or item["page"].page_kind)
            item["page_kind"] = item["analysis"].get("page_kind", item["page"].page_kind)
            item["page_title"] = clean_pdf_text(item.get("page_title") or item["analysis"].get("page_title") or item["page"].page_title)
            expander_title = f"{item['doc_name']} | side {item['page_number']} | {item['page_title']}"
            with st.expander(expander_title, expanded=False):
                preview_tab, edit_tab = st.tabs(["Forhaandsvisning", "V2 finredigering"])
                with preview_tab:
                    analysis = item["analysis"]
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown("**Original**")
                        st.image(item["page"].image, use_container_width=True)
                    with c2:
                        st.markdown("**Branntegning**")
                        st.image(item["annotated_image"], use_container_width=True)
                    st.markdown(f"**Tegningstype:** {PAGE_KIND_LABELS.get(item['page_kind'], item['page_kind'])}")
                    st.markdown(f"**Oppsummering:** {analysis.get('drawing_summary', '-')}")
                    if analysis.get("analysis_notes"):
                        st.markdown("**Analysepunkter**")
                        for note in analysis.get("analysis_notes", [])[:6]:
                            st.markdown(f"- {note}")
                    if analysis.get("assumptions"):
                        st.markdown("**Forutsetninger**")
                        for note in analysis.get("assumptions", [])[:5]:
                            st.markdown(f"- {note}")
                    if analysis.get("legend_items"):
                        legends = ", ".join([x.get("label", "") for x in analysis.get("legend_items", [])])
                        st.caption(f"Symbolsett: {legends}")
                    if item.get("locked"):
                        st.info("Denne siden er låst mot nye AI-revisjoner til du fjerner låsen i redigering-fanen.")
                with edit_tab:
                    editor_toggle_key = f"enable_editor__{re.sub(r'[^0-9A-Za-z_]+', '_', item['page'].uid)}"
                    editor_enabled = st.toggle(
                        "Aktiver finredigering for denne siden",
                        key=editor_toggle_key,
                        value=False,
                        help="Museeditoren lastes bare når du slår på denne bryteren. Det gjør generering av branntegninger mer stabil når mange sider analyseres samtidig.",
                    )
                    if editor_enabled:
                        render_analysis_editor(item)
                    else:
                        st.info("Finredigering er av for denne siden. Slå på bryteren over når du vil åpne museeditor og AI-revisjon.")


# -----------------------------------------------------------------------------
# 17. UI - RAPPORTGENERERING
# -----------------------------------------------------------------------------
st.markdown("<br>", unsafe_allow_html=True)
with st.expander("4. Generer rapport og nedlastinger", expanded=True):
    analyses = st.session_state.brann_analyses
    if not analyses:
        st.info("Du ma generere minst ett branntegningsutkast i steg 3 for a bygge rapport og vedlegg.")
    else:
        if st.session_state.brann_manual_edits_dirty:
            st.warning("Det finnes manuelle endringer som ikke er bakt inn i siste eksport ennå. Generer vedlegg eller komplett rapport pa nytt.")

        col_a, col_b = st.columns([2, 1])
        with col_a:
            st.markdown("Rapporten kombinerer tegningsanalyse, profesjonelt brannfaglig tekstutkast og vedlagte branntegninger i samme PDF.")
        with col_b:
            if st.session_state.get("generated_fire_drawings_pdf"):
                st.download_button(
                    "Last ned branntegninger (PDF)",
                    st.session_state.generated_fire_drawings_pdf,
                    file_name=f"Builtly_Branntegninger_{pd_state['p_name']}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
            elif st.button("Bygg branntegning-PDF", key="build_fire_pdf_step4", type="secondary", use_container_width=True):
                with st.spinner("Setter sammen branntegningsvedlegg..."):
                    try:
                        st.session_state.generated_fire_drawings_pdf = create_fire_drawings_pdf(pd_state, brann_data, analyses)
                        st.session_state.brann_manual_edits_dirty = False
                        st.success("Branntegning-PDF er klar.")
                    except Exception as exc:
                        st.session_state.generated_fire_drawings_pdf = None
                        st.error(f"Klarte ikke a bygge branntegning-PDF: {exc}")

        if st.button("🚀 Generer komplett brannkonsept med rapport og vedlegg", type="primary", use_container_width=True):
            with st.spinner("Skriver rapport og setter sammen vedlegg..."):
                try:
                    report_text = generate_report_text(pd_state, brann_data, analyses, manual_notes)
                    report_pdf = create_full_report_pdf(pd_state, brann_data, report_text, analyses)
                    fire_pdf = create_fire_drawings_pdf(pd_state, brann_data, analyses)
                    st.session_state.generated_report_text = report_text
                    st.session_state.generated_pdf = report_pdf
                    st.session_state.generated_fire_drawings_pdf = fire_pdf
                    st.session_state.brann_manual_edits_dirty = False
                    st.success("Brannkonsept og vedlagte branntegninger er generert.")

                    # Lagre til Supabase dashboard
                    if _HAS_AUTH:

                        try:

                            builtly_auth.save_report(
                            project_name=pd_state.get("p_name", ""),
                            report_name=f"Brannkonsept — Builtly_RIBr_{pd_state.get('p_name', '')}.pdf",
                            module="RIBr (Brannkonsept)",
                            file_path=f"Builtly_RIBr_{pd_state.get('p_name', '')}.pdf",

                            )

                        except Exception:

                            pass

                    # Lagre til disk for Dashboard
                    try:
                        report_dir = DB_DIR / "reports"
                        report_dir.mkdir(exist_ok=True)
                        p_name = pd_state.get("p_name", "prosjekt")
                        pdf_path = report_dir / f"Builtly_RIBr_{p_name}.pdf"
                        pdf_path.write_bytes(report_pdf)
                        if fire_pdf:
                            (report_dir / f"Builtly_Branntegninger_{p_name}.pdf").write_bytes(fire_pdf)
                        
                        reviews_file = DB_DIR / "pending_reviews.json"
                        existing = {}
                        if reviews_file.exists():
                            try: existing = json.loads(reviews_file.read_text(encoding="utf-8"))
                            except Exception: existing = {}
                        doc_id = f"PRJ-{datetime.now().strftime('%y')}-RIBR{len(existing)+1:03d}"
                        existing[doc_id] = {
                            "title": p_name, "module": "RIBr (Brannkonsept)",
                            "drafter": "Builtly AI", "reviewer": "Senior Brannrådgiver",
                            "status": "Pending Senior Review", "class": "badge-pending",
                            "pdf_file": str(pdf_path), "timestamp": datetime.now().isoformat(),
                        }
                        reviews_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception:
                        pass
                except Exception as exc:
                    st.session_state.generated_pdf = None
                    st.error(f"Generering av rapport/vedlegg feilet: {exc}")

        if st.session_state.get("generated_report_text"):
            with st.expander("Forhaandsvis tekstutkast", expanded=False):
                st.text_area("Generert rapporttekst", value=st.session_state.generated_report_text, height=420)

        if st.session_state.get("generated_pdf"):
            st.download_button(
                "📄 Last ned komplett RIBr-rapport",
                st.session_state.generated_pdf,
                file_name=f"Builtly_RIBr_{pd_state['p_name']}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
