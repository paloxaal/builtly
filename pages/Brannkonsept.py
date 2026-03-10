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
    if any(token in haystack for token in ["landskapsplan", "utomhusplan", "oppstilling brann", "atkomst brann", "situasjonsplan"]):
        return "site_plan"
    if any(token in haystack for token in ["p-kjeller", "parkering", "rampe", "hc", "parkerings", "kjeller"]):
        return "parking_plan"
    if any(token in haystack for token in ["etasjeplan", "typisk etasjeplan", "plan 1", "plan 1-5", "stue/ kj", "stue/kjøkken", "balkong", "terrasse"]):
        return "residential_floor_plan"
    if "snitt" in haystack:
        return "section"
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
    page_kind = page.page_kind if page.page_kind in DEFAULT_LEGENDS else "general_plan"
    elements: List[Dict[str, Any]] = []
    notes: List[str] = []
    assumptions: List[str] = []

    def first_hit(term: str) -> Optional[List[float]]:
        values = page.keyword_hits.get(term) or []
        return values[0] if values else None

    if page_kind == "site_plan":
        for term in ["oppstilling brann", "Oppstilling", "Oppstilling"]:
            for rect in page.keyword_hits.get(term, [])[:3]:
                elements.append(create_fill_element(rect, "staging_area", "Oppstillingsplass"))
        for term in ["atkomst brann", "Innkjøring", "Inngang"]:
            for rect in page.keyword_hits.get(term, [])[:3]:
                elements.append(create_margin_arrow(rect, "attack_route", "Innsatsvei"))
        for term in ["atkomst brann", "Innkjøring", "VEI", "Parkering"]:
            for rect in page.keyword_hits.get(term, [])[:2]:
                elements.append(create_box_element(rect, "fire_access", "Brannatkomst"))
        notes.append("Uteplan er heuristisk markert med atkomst, innsatsvei og oppstillingsflater der relevante teksttreff er funnet.")
        assumptions.append("Plassering og geometri for kjørbar atkomst/oppstillingsplass må kontrolleres mot landskapsplan og veioppbygging.")

    elif page_kind == "parking_plan":
        for term in ["Trapp", "Trapp/ heis", "heis", "Korridor"]:
            for rect in page.keyword_hits.get(term, [])[:6]:
                elements.append(create_fill_element(rect, "escape_route", "Rømningskjerne"))
        for term in ["Sluse", "Teknisk"]:
            for rect in page.keyword_hits.get(term, [])[:4]:
                elements.append(create_box_element(rect, "fire_compartment", term))
        for term in ["Inngang", "Innkjøring"]:
            for rect in page.keyword_hits.get(term, [])[:3]:
                elements.append(create_margin_arrow(rect, "attack_route", "Innsatsvei"))
        notes.append("P-kjeller er heuristisk markert med trapper/sluser som sannsynlige rømnings- og innsatsknutepunkt.")
        assumptions.append("Brannsluse, branngardin og maksimal slangelengde må bekreftes i detaljprosjektering.")

    elif page_kind == "residential_floor_plan":
        for term in ["Trapp", "Trapp/ heis", "heis", "Korridor"]:
            for rect in page.keyword_hits.get(term, [])[:5]:
                elements.append(create_fill_element(rect, "escape_route", "Rømningsvei"))
        for term in ["Balkong", "Terrasse"]:
            for rect in page.keyword_hits.get(term, [])[:8]:
                elements.append(create_fill_element(rect, "rescue_route", "Balkong / redningsmulighet"))
        for term in ["Inngang"]:
            for rect in page.keyword_hits.get(term, [])[:2]:
                elements.append(create_margin_arrow(rect, "attack_route", "Angrepsvei"))
        for term in ["Trapp", "Trapp/ heis"]:
            for rect in page.keyword_hits.get(term, [])[:2]:
                elements.append(create_box_element(rect, "fire_compartment", "Brannskille / kjerne"))
        notes.append("Boligplan er heuristisk markert med trapperom, rømningsretning og balkonger/redningsflater der dette kan leses ut av tegningen.")
        assumptions.append("Branncellebegrensende skiller på leilighetsnivå må verifiseres mot endelig planløsning og detaljert RIBr-underlag.")

    else:
        for term in ["Trapp", "Sluse", "Inngang"]:
            for rect in page.keyword_hits.get(term, [])[:3]:
                elements.append(create_box_element(rect, "fire_compartment", term))
        if elements:
            notes.append("Generell plantegning er markert rundt identifiserte brannrelevante nøkkelord.")

    if not elements:
        assumptions.append("Ingen sikre teksttreff for automatisk overlay; siden bør vurderes manuelt.")

    return {
        "page_kind": page_kind,
        "page_title": page.page_title,
        "drawing_summary": "Heuristisk førsteutkast basert på teksttreff i tegningen.",
        "analysis_notes": notes,
        "assumptions": assumptions,
        "legend_items": DEFAULT_LEGENDS.get(page_kind, DEFAULT_LEGENDS["general_plan"]),
        "elements": elements,
        "qa": {
            "confidence": 0.45 if elements else 0.2,
            "human_review_focus": [
                "Kontroller at markerte rømningsveier og innsatsveier faktisk samsvarer med prosjektets brannstrategi.",
                "Kontroller at branncelleskiller og dørklasser er riktig spesifisert før bruk i byggesak eller detaljprosjekt.",
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
    default_legends = DEFAULT_LEGENDS.get(page.page_kind, DEFAULT_LEGENDS["general_plan"])
    legend_text = "; ".join([f"{item['label']} -> {item['style']}" for item in default_legends])
    text_excerpt = clean_pdf_text(page.raw_text)[:4500]

    return f"""
Du er en svært erfaren norsk brannrådgiver (RIBr). Du analyserer en opplastet arkitekttegning og skal returnere et branntegningsutkast som ligger tett på profesjonelle branntegninger.

OPPGAVE:
1. Forsta hvilken tegningstype dette er.
2. Marker kun forhold som faktisk kan begrunnes ut fra tegningen, synlige romnavn og teksttreff.
3. Returner et nøkternt, faglig og anvendbart overlayutkast som kan legges direkte over plantegningen.
4. Bruk standardiserte brannsymboler og farger inspirert av profesjonelle branntegninger:
   - fire_compartment = rødt stiplet skille / branncellegrense
   - escape_route = grønt transparent bånd for rømningsvei / trapp / korridor
   - escape_arrow = grønn pil for rømningsretning
   - rescue_route = orange transparent markering for balkong / redningsmulighet
   - attack_route = rød pil for innsatsvei / angrepsvei
   - fire_access = blå skravur/bånd for kjørbar atkomst brannvesen
   - staging_area = rødt/rosa felt for oppstillingsplass
   - door_class = liten hvit tagg med rød kant for dørklasse eller referanse
   - note_box = diskret hvit kommentarboks
5. Ikke lag en perfekt detaljprosjektert fasit. Lever et godt RIBr-utkast som er konkret, lesbart og egnet for faglig kontroll.
6. Ikke dekk til tittelblokk, målestokk eller revisjonsfelt dersom det finnes stor ledig hvitflate et annet sted.

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
Tolket tegningstype: {PAGE_KIND_LABELS.get(page.page_kind, page.page_kind)}
Tolkning av sidetittel: {page.page_title}
Viktige teksttreff med koordinater (normalisert 0-1): {keyword_summary_for_prompt(page.keyword_hits)}
Utdrag av lesbar tekst fra PDF: {text_excerpt}

MINSTE SYMBOLSETT FOR DENNE TEGNINGSTYPEN:
{legend_text}

RETURNER KUN GYLDIG JSON MED DENNE STRUKTUREN:
{json.dumps({
    'page_kind': 'site_plan | parking_plan | residential_floor_plan | general_plan',
    'page_title': 'Kort og ryddig tittel',
    'drawing_summary': '1-3 setninger om hva tegningen viser og hva som er markert',
    'analysis_notes': ['Punktvis observasjon 1', 'Punktvis observasjon 2'],
    'assumptions': ['Hva som er antatt eller ma kontrolleres'],
    'legend_items': [{'label': 'Branncelle EI 60', 'style': 'fire_compartment'}],
    'elements': [
        {'type': 'dashed_polyline', 'points': [[0.12, 0.20], [0.32, 0.20], [0.32, 0.45]], 'style': 'fire_compartment', 'label': 'EI 60'},
        {'type': 'band', 'points': [[0.45, 0.18], [0.45, 0.42]], 'style': 'escape_route', 'label': 'Rømningsvei'},
        {'type': 'arrow', 'points': [[0.65, 0.30], [0.72, 0.30]], 'style': 'escape_arrow', 'label': 'Rømningsretning'},
        {'type': 'box', 'rect': [0.41, 0.17, 0.50, 0.32], 'style': 'door_class', 'label': 'EI2 30 Sa'},
        {'type': 'area_fill', 'rect': [0.12, 0.65, 0.26, 0.77], 'style': 'rescue_route', 'label': 'Balkong / redningsmulighet'},
        {'type': 'note_box', 'rect': [0.62, 0.70, 0.90, 0.88], 'style': 'note_box', 'label': 'Kort notat'}
    ],
    'qa': {'confidence': 0.0, 'human_review_focus': ['Hva fagperson ma kontrollere']}
}, ensure_ascii=False)}

VIKTIGE REGLER:
- Koordinater skal være normaliserte 0-1.
- Elementlisten skal være praktisk og ikke overfylt. Vanligvis 4-14 elementer.
- Bruk dashed_polyline eller box for brannskiller.
- Bruk band og arrow for rømningslinjer og innsatsveier.
- Legg legend_items og note_box slik at de kan brukes direkte i en rapport.
- Ikke returner markdown, forklaring eller tekst utenfor JSON.
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
    item["page_kind"] = item["analysis"].get("page_kind", page.page_kind)
    item["annotated_image"] = render_overlay(page, item["analysis"])
    invalidate_generated_outputs()


def analyze_page(page: SourcePage, brann_data: Dict[str, Any], manual_notes: str = "") -> Dict[str, Any]:
    heuristic = normalize_analysis_payload(heuristic_markup_for_page(page, brann_data), page.page_kind)
    if not AI_AVAILABLE or page.page_kind not in DRAWABLE_PAGE_KINDS:
        return heuristic

    try:
        model = genai.GenerativeModel(pick_model_name())
        response = model.generate_content([drawing_prompt_for_page(page, brann_data, manual_notes), page.image])
        parsed = try_parse_json(extract_response_text(response))
        merged = merge_analysis(parsed, heuristic)
        return normalize_analysis_payload(merged, page.page_kind)
    except Exception:
        return heuristic
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


def render_overlay(page: SourcePage, analysis: Dict[str, Any]) -> Image.Image:
    canvas = page.image.convert("RGBA")
    overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    label_font = get_font(max(18, int(canvas.width * 0.018)), bold=True)

    for element in analysis.get("elements", []) or []:
        style_name = element.get("style", "fire_compartment")
        style = FIRE_STYLE_LIBRARY.get(style_name, FIRE_STYLE_LIBRARY["fire_compartment"])
        label = clean_pdf_text(element.get("label", ""))
        etype = element.get("type")

        if etype in {"dashed_polyline", "polyline", "band", "arrow"}:
            points = [norm_pt_to_px(pt, canvas.size) for pt in element.get("points", []) or []]
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
            if label:
                mx = int(sum(p[0] for p in points) / len(points))
                my = int(sum(p[1] for p in points) / len(points))
                bbox = (mx + 10, my - 10, mx + 180, my + 38)
                draw_text_label(draw, bbox, label, style.get("stroke", "#0f172a"), "#ffffff", style.get("text", "#0f172a"), max(16, int(canvas.width * 0.012)))

        elif etype in {"box", "area_fill", "note_box"}:
            rect = element.get("rect")
            if not rect:
                continue
            px_rect = norm_rect_to_px(rect, canvas.size)
            if etype == "box":
                if style_name == "fire_compartment":
                    x0, y0, x1, y1 = px_rect
                    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
                    draw_dashed_polyline(draw, pts, rgba(style.get("stroke"), 255), int(style.get("width", 4)), *style.get("dash", (10, 6)))
                elif style_name == "fire_access":
                    pts = [(px_rect[0], (px_rect[1] + px_rect[3]) // 2), (px_rect[2], (px_rect[1] + px_rect[3]) // 2)]
                    draw_hatched_band(draw, pts, style)
                else:
                    draw.rounded_rectangle(px_rect, radius=12, outline=rgba(style.get("stroke"), 255), width=int(style.get("width", 3)), fill=rgba(style.get("fill"), int(style.get("alpha", 90))))
                if label:
                    label_rect = (px_rect[0], max(10, px_rect[1] - 42), min(canvas.width - 10, px_rect[0] + 220), max(44, px_rect[1] - 6))
                    draw_text_label(draw, label_rect, label, style.get("stroke", "#0f172a"), "#ffffff", style.get("text", "#0f172a"), max(16, int(canvas.width * 0.012)))
            elif etype == "area_fill":
                draw.rounded_rectangle(px_rect, radius=12, outline=rgba(style.get("stroke"), 255), width=max(2, int(style.get("width", 2) * 0.12)), fill=rgba(style.get("fill"), int(style.get("alpha", 100))))
                if label:
                    label_rect = (px_rect[0], max(10, px_rect[1] - 42), min(canvas.width - 10, px_rect[0] + 260), max(44, px_rect[1] - 6))
                    draw_text_label(draw, label_rect, label, style.get("stroke", "#0f172a"), "#ffffff", style.get("text", "#0f172a"), max(16, int(canvas.width * 0.012)))
            else:
                draw_text_label(draw, px_rect, label or "Kommentar", style.get("stroke", "#94a3b8"), style.get("fill", "#ffffff"), style.get("text", "#0f172a"), max(16, int(canvas.width * 0.012)))

        elif etype == "door_tag":
            rect = element.get("rect")
            if not rect:
                continue
            px_rect = norm_rect_to_px(rect, canvas.size)
            draw_text_label(draw, px_rect, label or "EI2 30 Sa", style.get("stroke", "#ef4444"), style.get("fill", "#ffffff"), style.get("text", "#ef4444"), max(14, int(canvas.width * 0.010)))

    combined = Image.alpha_composite(canvas, overlay).convert("RGB")
    overlay_draw = ImageDraw.Draw(combined)
    legend_items = analysis.get("legend_items") or DEFAULT_LEGENDS.get(page.page_kind, DEFAULT_LEGENDS["general_plan"])
    draw_legend_box(overlay_draw, combined, legend_items)
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
                pdf.highlight_box("Forutsetninger / ma avklares", assumptions[:4], fill=(255, 249, 235), accent=(245, 158, 11))

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


def render_analysis_editor(item: Dict[str, Any]) -> None:
    page = item["page"]
    item["analysis"] = normalize_analysis_payload(item.get("analysis") or {}, item.get("page_kind") or page.page_kind)
    item["page_kind"] = item["analysis"].get("page_kind", page.page_kind)
    item["page_title"] = clean_pdf_text(item.get("page_title") or item["analysis"].get("page_title") or page.page_title)

    key_root = re.sub(r"[^0-9A-Za-z_]+", "_", page.uid)
    analysis = item["analysis"]
    page_kind_options = list(PAGE_KIND_LABELS.keys())
    current_kind = item.get("page_kind", page.page_kind)
    kind_index = page_kind_options.index(current_kind) if current_kind in page_kind_options else 0

    st.caption("V2: finrediger sideinfo, kontrollpunkter og overlay-elementer direkte i appen før PDF genereres.")

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
            "Forutsetninger / ma avklares (en per linje)",
            value="\n".join(analysis.get("assumptions", [])),
            height=140,
        )
        save_page = st.form_submit_button("Oppdater sideinfo og tekst", use_container_width=True)
    if save_page:
        item["page_title"] = clean_pdf_text(new_title) or item.get("page_title", page.page_title)
        analysis["page_title"] = item["page_title"]
        analysis["page_kind"] = selected_kind
        item["page_kind"] = selected_kind
        analysis["drawing_summary"] = ironclad_text_formatter(new_summary)
        analysis["analysis_notes"] = split_lines_to_list(new_notes)
        analysis["assumptions"] = split_lines_to_list(new_assumptions)
        item["analysis"] = analysis
        refresh_analysis_item(item)
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
                st.session_state.generated_fire_drawings_pdf = create_fire_drawings_pdf(pd_state, brann_data, analyses)
                st.session_state.generated_pdf = None
                st.session_state.generated_report_text = ""
                st.session_state.brann_manual_edits_dirty = False
                st.success(f"Generert {len(analyses)} branntegningsutkast.")

    analyses = st.session_state.brann_analyses
    if analyses:
        if st.session_state.brann_manual_edits_dirty:
            st.warning("Manuelle endringer er registrert. Oppdater branntegning-PDF eller generer komplett rapport pa nytt i steg 4.")
            if st.button("Oppdater branntegning-PDF fra redigerte markeringer", key="refresh_fire_pdf_after_edits", type="secondary", use_container_width=True):
                with st.spinner("Oppdaterer vedlegg med redigerte markeringer..."):
                    st.session_state.generated_fire_drawings_pdf = create_fire_drawings_pdf(pd_state, brann_data, analyses)
                    st.session_state.brann_manual_edits_dirty = False
                st.success("Branntegning-PDF er oppdatert.")

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
                        st.markdown("**Forutsetninger / ma avklares**")
                        for note in analysis.get("assumptions", [])[:5]:
                            st.markdown(f"- {note}")
                    if analysis.get("legend_items"):
                        legends = ", ".join([x.get("label", "") for x in analysis.get("legend_items", [])])
                        st.caption(f"Symbolsett: {legends}")
                with edit_tab:
                    render_analysis_editor(item)


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
                    st.session_state.generated_fire_drawings_pdf = create_fire_drawings_pdf(pd_state, brann_data, analyses)
                    st.session_state.brann_manual_edits_dirty = False
                st.success("Branntegning-PDF er klar.")

        if st.button("🚀 Generer komplett brannkonsept med rapport og vedlegg", type="primary", use_container_width=True):
            with st.spinner("Skriver rapport og setter sammen vedlegg..."):
                report_text = generate_report_text(pd_state, brann_data, analyses, manual_notes)
                report_pdf = create_full_report_pdf(pd_state, brann_data, report_text, analyses)
                st.session_state.generated_report_text = report_text
                st.session_state.generated_pdf = report_pdf
                st.session_state.generated_fire_drawings_pdf = create_fire_drawings_pdf(pd_state, brann_data, analyses)
                st.session_state.brann_manual_edits_dirty = False
            st.success("Brannkonsept og vedlagte branntegninger er generert.")

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
