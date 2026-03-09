from __future__ import annotations

import base64
import io
import json
import os
import re
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import streamlit as st
from fpdf import FPDF
from PIL import Image, ImageColor, ImageDraw

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None

try:
    import google.generativeai as genai
except ImportError:  # pragma: no cover
    genai = None


# -----------------------------------------------------------------------------
# Page setup
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Brannkonsept | Builtly",
    page_icon="BI",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# -----------------------------------------------------------------------------
# Data structures
# -----------------------------------------------------------------------------
@dataclass
class ProjectData:
    p_name: str = ""
    c_name: str = ""
    p_desc: str = ""
    adresse: str = ""
    kommune: str = ""
    gnr: str = ""
    bnr: str = ""
    b_type: str = "Næring"
    etasjer: int = 1
    bta: int = 0
    land: str = "Norge"

    @property
    def full_address(self) -> str:
        parts = [self.adresse, self.kommune]
        return ", ".join([p for p in parts if p])


@dataclass
class UploadedSource:
    name: str
    category: str
    pages: List[Image.Image] = field(default_factory=list)
    source_kind: str = "uploaded"


@dataclass
class FireMarkupSpec:
    page_title: str = ""
    notes: List[str] = field(default_factory=list)
    legend: List[Dict[str, str]] = field(default_factory=list)
    elements: List[Dict[str, Any]] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
DB_DIR = Path("qa_database")
SSOT_FILE = DB_DIR / "ssot.json"
IMG_DIR = DB_DIR / "project_images"
DEFAULT_REPORT_HEADINGS = [
    "1. Dokumentinformasjon og grunnlag",
    "2. Formelle forhold og regelverk",
    "3. Tiltakets omfang og prosjekteringsforutsetninger",
    "4. Oppsummering hovedføringer brannverntiltak",
    "5. Fravik og særskilte vurderinger",
    "6. Kravspesifikasjon",
    "7. Uavklarte forhold og videre prosjektering",
    "8. Vedlegg og tegningsliste",
]
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
BENCHMARK_CONVENTIONS = [
    "egen utomhus-branntegning med kjørbar atkomst, oppstillingsplass og innsatsvei",
    "egen kjeller-branntegning med avstander, sluser og angrepsvei",
    "planvise bolig-branntegninger med branncellegrenser, dørklasser, rømningsvei og rømningsretning",
    "rapport med tegningsliste, prosjekteringsforutsetninger, hovedføringer, fravik og kravspesifikasjoner",
]


# -----------------------------------------------------------------------------
# State helpers
# -----------------------------------------------------------------------------
def init_state() -> None:
    if "project_data" not in st.session_state:
        st.session_state.project_data = ProjectData().__dict__.copy()
    if SSOT_FILE.exists() and not st.session_state.project_data.get("p_name"):
        try:
            st.session_state.project_data = json.loads(SSOT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    defaults = {
        "fire_map": None,
        "fire_map_source": None,
        "source_documents": [],
        "benchmark_documents": [],
        "generated_fire_drawings": [],
        "generated_report_text": "",
        "generated_pdf": None,
        "drawing_manifest": [],
        "include_only_module_drawings": True,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------
def project_data() -> ProjectData:
    data = st.session_state.project_data
    return ProjectData(**{k: data.get(k, getattr(ProjectData(), k)) for k in ProjectData().__dict__.keys()})


def render_html(html_string: str) -> None:
    st.markdown(html_string.replace("\n", " "), unsafe_allow_html=True)


def clean_pdf_text(text: str) -> str:
    if not text:
        return ""
    rep = {
        "–": "-",
        "—": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "…": "...",
        "•": "-",
    }
    for old, new in rep.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def sanitize_multiline(text: str) -> str:
    text = text.replace("$", "").replace("_", " ")
    text = re.sub(r"[-|=]{4,}", " ", text)
    return clean_pdf_text(text)


def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        path = Path(candidate)
        if path.exists():
            suffix = path.suffix.lower().replace(".", "") or "png"
            encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
            return f"data:image/{suffix};base64,{encoded}"
    return ""


def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{name}.py")
        if p.exists():
            return str(p)
    return ""


def category_from_name(name: str) -> str:
    lower = name.lower()
    if "branntegning" in lower:
        return "Benchmark branntegning"
    if "ribr" in lower or "brannstrategi" in lower or "brannkonsept" in lower:
        return "Benchmark rapport"
    if "landskapsplan" in lower or "utomhus" in lower or "situasjon" in lower:
        return "Utomhus / situasjon"
    if "kjeller" in lower or "u1" in lower:
        return "Kjeller / underetasje"
    if "plan" in lower:
        return "Arkitektplan"
    return "Annet underlag"


def get_model() -> Optional[Any]:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key or genai is None:
        return None
    genai.configure(api_key=api_key)
    try:
        valid_models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
    except Exception:
        return None
    preferred = next((m for m in ["models/gemini-1.5-pro", "models/gemini-1.5-flash"] if m in valid_models), None)
    chosen = preferred or (valid_models[0] if valid_models else None)
    return genai.GenerativeModel(chosen) if chosen else None


# -----------------------------------------------------------------------------
# Document handling
# -----------------------------------------------------------------------------
def pdf_to_images(pdf_bytes: bytes, limit: int = 3, scale: float = 1.8) -> List[Image.Image]:
    images: List[Image.Image] = []
    if fitz is None:
        return images
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_idx in range(min(limit, len(doc))):
            pix = doc.load_page(page_idx).get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            images.append(image)
        doc.close()
    except Exception:
        return []
    return images


def load_shared_project_images() -> List[UploadedSource]:
    docs: List[UploadedSource] = []
    if not IMG_DIR.exists():
        return docs
    for path in sorted(IMG_DIR.glob("*.jpg")):
        try:
            img = Image.open(path).convert("RGB")
            docs.append(UploadedSource(name=path.name, category="Felles prosjektbilde", pages=[img], source_kind="shared"))
        except Exception:
            continue
    return docs


def ingest_streamlit_files(files: Iterable[Any], source_kind: str) -> List[UploadedSource]:
    documents: List[UploadedSource] = []
    for file in files:
        raw = file.read()
        name = file.name
        category = category_from_name(name)
        pages: List[Image.Image] = []
        if name.lower().endswith(".pdf"):
            pages = pdf_to_images(raw, limit=4)
        else:
            try:
                pages = [Image.open(io.BytesIO(raw)).convert("RGB")]
            except Exception:
                pages = []
        documents.append(UploadedSource(name=name, category=category, pages=pages, source_kind=source_kind))
    return documents


def build_drawing_manifest(sources: Iterable[UploadedSource]) -> List[Dict[str, str]]:
    manifest: List[Dict[str, str]] = []
    for src in sources:
        manifest.append(
            {
                "fil": src.name,
                "kategori": src.category,
                "sider lastet": str(len(src.pages)),
                "kilde": src.source_kind,
            }
        )
    return manifest


# -----------------------------------------------------------------------------
# Map fetch
# -----------------------------------------------------------------------------
def fetch_map_image(adresse: str, kommune: str, gnr: str, bnr: str, api_key: Optional[str]) -> Tuple[Optional[Image.Image], str]:
    north: Optional[float] = None
    east: Optional[float] = None
    adr_clean = adresse.replace(",", "").strip() if adresse else ""
    kom_clean = kommune.replace(",", "").strip() if kommune else ""

    queries = []
    if adr_clean and kom_clean:
        queries.append(f"{adr_clean} {kom_clean}")
    if adr_clean:
        queries.append(adr_clean)
    if gnr and bnr and kom_clean:
        queries.append(f"{kom_clean} {gnr}/{bnr}")

    for q in queries:
        safe_query = urllib.parse.quote(q)
        url = f"https://ws.geonorge.no/adresser/v1/sok?sok={safe_query}&fuzzy=true&utkoordsys=25833&treffPerSide=1"
        try:
            response = requests.get(url, timeout=4)
            if response.status_code == 200 and response.json().get("adresser"):
                hit = response.json()["adresser"][0]
                point = hit.get("representasjonspunkt", {})
                north = point.get("nord")
                east = point.get("øst")
                break
        except Exception:
            continue

    if north and east:
        min_x, max_x = float(east) - 150, float(east) + 150
        min_y, max_y = float(north) - 150, float(north) + 150
        ortho = (
            "https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1"
            f"&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=900&height=900&format=image/png"
        )
        try:
            response = requests.get(ortho, timeout=5)
            if response.status_code == 200 and len(response.content) > 5000:
                return Image.open(io.BytesIO(response.content)).convert("RGB"), "Kartverket (Norge i bilder)"
        except Exception:
            pass

    if api_key and (adr_clean or kom_clean):
        query = urllib.parse.quote(f"{adr_clean}, {kom_clean}, Norway")
        url = f"https://maps.googleapis.com/maps/api/staticmap?center={query}&zoom=19&size=700x700&maptype=satellite&key={api_key}"
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                return Image.open(io.BytesIO(response.content)).convert("RGB"), "Google Maps Satellite"
        except Exception:
            pass

    return None, "Kart kunne ikke hentes automatisk."


# -----------------------------------------------------------------------------
# AI prompt builders
# -----------------------------------------------------------------------------
def build_report_prompt(
    pd: ProjectData,
    rk: str,
    bk: str,
    sprinkler: str,
    alarm: str,
    unresolved: str,
    deviations: str,
    manifest: List[Dict[str, str]],
) -> str:
    manifest_lines = [f"- {m['fil']} | {m['kategori']} | sider: {m['sider lastet']}" for m in manifest]
    manifest_text = "\n".join(manifest_lines) if manifest_lines else "- Ingen filer registrert"
    return f"""
Du er en senior RIBr som skal skrive et profesjonelt brannkonsept på norsk.

MÅL:
- bruk konsulentpreg, nøktern tone og tydelige forutsetninger
- skriv som et reelt prosjekteringsunderlag, ikke som markedsføring
- vær eksplisitt på hva som er observert og hva som er forutsatt
- avslutt med en tydelig liste over uavklarte forhold

PROSJEKT:
- Prosjektnavn: {pd.p_name}
- Oppdragsgiver: {pd.c_name}
- Adresse: {pd.full_address}
- Formål: {pd.b_type}
- Etasjer: {pd.etasjer}
- BTA: {pd.bta}
- Regelverkland: {pd.land}
- Foreslått risikoklasse: {rk}
- Foreslått brannklasse: {bk}
- Sprinklerforutsetning: {sprinkler}
- Alarmforutsetning: {alarm}

PROSJEKTBESKRIVELSE FRA BRUKER:
{pd.p_desc or 'Ingen fritekst angitt.'}

TEGNINGSMANIFEST:
{manifest_text}

BENCHMARK-KRAV TIL LEVERANSE:
- egen utomhus-del med atkomst, oppstillingsplass og innsatsvei
- egen kjeller-del med sluser, trapperom, kjellerforhold og avstander
- planvis omtale av boligplaner med rømningsforhold, brannceller og dørkrav
- struktur som ligner en rådgiverrapport med grunnlag, formelle forhold, prosjekteringsforutsetninger, hovedføringer, fravik og kravspesifikasjoner

ANGITTE FRAVIK / SÆRSKILTE VURDERINGER FRA BRUKER:
{deviations or 'Ingen særskilte fravik angitt manuelt.'}

ANGITTE UAVKLARTE FORHOLD FRA BRUKER:
{unresolved or 'Ingen uavklarte forhold angitt manuelt.'}

SKRIV KUN MED DISSE OVERSKRIFTENE:
# 1. Dokumentinformasjon og grunnlag
# 2. Formelle forhold og regelverk
# 3. Tiltakets omfang og prosjekteringsforutsetninger
# 4. Oppsummering hovedføringer brannverntiltak
# 5. Fravik og særskilte vurderinger
# 6. Kravspesifikasjon
# 7. Uavklarte forhold og videre prosjektering
# 8. Vedlegg og tegningsliste

KRITISK:
- bruk formuleringer som 'Av plantegningen fremgår det at ...' når du ser noe konkret
- skill tydelig mellom observert, forutsatt og anbefalt
- ikke oppfinn mål eller klasser som ikke er faglig begrunnet
""".strip()


_DRAWING_JSON_INSTRUCTION = """
Returner KUN gyldig JSON med denne strukturen:
{
  "page_title": "tekst",
  "notes": ["tekst", "tekst"],
  "legend": [
    {"label": "Branncelle EI 60", "style": "dashed_red"},
    {"label": "Rømningsvei", "style": "green_band"},
    {"label": "Rømningsretning", "style": "green_arrow"},
    {"label": "Redningsvei", "style": "orange_band"},
    {"label": "Innsatsvei", "style": "red_arrow"},
    {"label": "Kjørbar atkomst", "style": "blue_access"},
    {"label": "Oppstillingsplass", "style": "pink_fill"}
  ],
  "elements": [
    {"type": "line", "points": [[x1,y1],[x2,y2]], "style": "dashed_red", "label": "EI60"},
    {"type": "arrow", "points": [[x1,y1],[x2,y2]], "style": "green_arrow", "label": "Rømning"},
    {"type": "rect", "rect": [x,y,w,h], "style": "pink_fill", "label": "Oppstilling"},
    {"type": "polyline", "points": [[x1,y1],[x2,y2],[x3,y3]], "style": "blue_access", "label": "Atkomst"},
    {"type": "note", "at": [x,y], "style": "red_callout", "text": "Merknad"}
  ]
}
Koordinater skal oppgis i pikselkoordinater til originalbildet. Ingen markdown. Ingen forklarende tekst utenfor JSON.
""".strip()


def build_drawing_prompt(pd: ProjectData, drawing_name: str) -> str:
    return f"""
Du skal lage et førsteutkast til brannoverlay for tegningen '{drawing_name}'.
Prosjekt: {pd.p_name}
Adresse: {pd.full_address}
Formål: {pd.b_type}
Etasjer: {pd.etasjer}

Lag et konservativt forslag som ligner rådgivertegningsspråk:
- marker sannsynlige branncellegrenser
- marker rømningsvei og rømningsretning
- marker redningsvei ved balkong/vindu når det er naturlig
- marker innsatsvei og oppstillingsplass der dette kan utledes av situasjonsplan / kjellerplan
- bruk korte callouts når forhold bør kontrolleres manuelt

Legg inn note dersom du er usikker. Ikke overtegn hele planløsningen.
{_DRAWING_JSON_INSTRUCTION}
""".strip()


# -----------------------------------------------------------------------------
# Drawing overlay rendering
# -----------------------------------------------------------------------------
def _safe_color(value: Optional[str], fallback: str = "#000000") -> Tuple[int, int, int]:
    try:
        return ImageColor.getrgb(value or fallback)
    except Exception:
        return ImageColor.getrgb(fallback)


def draw_dashed_line(draw: ImageDraw.ImageDraw, p1: Tuple[int, int], p2: Tuple[int, int], fill: Tuple[int, int, int], width: int, dash: int = 10, gap: int = 6) -> None:
    x1, y1 = p1
    x2, y2 = p2
    length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1.0)
    dx = (x2 - x1) / length
    dy = (y2 - y1) / length
    position = 0.0
    while position < length:
        start = position
        end = min(position + dash, length)
        s = (int(x1 + dx * start), int(y1 + dy * start))
        e = (int(x1 + dx * end), int(y1 + dy * end))
        draw.line([s, e], fill=fill, width=width)
        position += dash + gap


def draw_arrow(draw: ImageDraw.ImageDraw, p1: Tuple[int, int], p2: Tuple[int, int], fill: Tuple[int, int, int], width: int) -> None:
    draw.line([p1, p2], fill=fill, width=width)
    x1, y1 = p1
    x2, y2 = p2
    vx = x2 - x1
    vy = y2 - y1
    length = max((vx * vx + vy * vy) ** 0.5, 1.0)
    ux = vx / length
    uy = vy / length
    wing = 16
    back = 18
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
        fill_rgba = None
        if fill:
            r, g, b = _safe_color(fill, "#ffffff")
            fill_rgba = (r, g, b, 110)
        width = int(style.get("width", 3))
        etype = element.get("type")

        if etype == "line":
            points = element.get("points", [])
            if len(points) >= 2:
                for idx in range(len(points) - 1):
                    p1 = tuple(map(int, points[idx]))
                    p2 = tuple(map(int, points[idx + 1]))
                    if style_key == "dashed_red":
                        draw_dashed_line(draw, p1, p2, stroke, width)
                    else:
                        draw.line([p1, p2], fill=stroke, width=width)
        elif etype == "polyline":
            points = [tuple(map(int, p)) for p in element.get("points", [])]
            if len(points) >= 2:
                draw.line(points, fill=stroke, width=width)
        elif etype == "arrow":
            points = element.get("points", [])
            if len(points) == 2:
                draw_arrow(draw, tuple(map(int, points[0])), tuple(map(int, points[1])), stroke, width)
        elif etype == "rect":
            rect = element.get("rect", [])
            if len(rect) == 4:
                x, y, w, h = map(int, rect)
                draw.rectangle([x, y, x + w, y + h], outline=stroke, width=width, fill=fill_rgba)
        elif etype == "note":
            at = element.get("at", [])
            text = str(element.get("text", "Merknad"))
            if len(at) == 2:
                x, y = map(int, at)
                w = max(200, min(360, 8 * len(text)))
                h = 44
                draw.rectangle([x, y, x + w, y + h], outline=stroke, width=2, fill=(255, 255, 255, 225))
                draw.text((x + 8, y + 12), text, fill=stroke)

        label = element.get("label")
        if label:
            if etype in {"rect"} and len(element.get("rect", [])) == 4:
                x, y, _, _ = map(int, element.get("rect", []))
                draw.text((x + 6, y - 18), str(label), fill=stroke)
            elif etype in {"line", "polyline", "arrow"} and element.get("points"):
                x, y = map(int, element["points"][0])
                draw.text((x + 6, y + 6), str(label), fill=stroke)

    composed = Image.alpha_composite(canvas, overlay).convert("RGB")
    return add_standard_legend(composed, spec)


def add_standard_legend(image: Image.Image, spec: FireMarkupSpec) -> Image.Image:
    canvas = image.copy().convert("RGB")
    draw = ImageDraw.Draw(canvas)
    legend_entries = spec.legend or [
        {"label": "Branncelle", "style": "dashed_red"},
        {"label": "Rømningsvei", "style": "green_band"},
        {"label": "Rømningsretning", "style": "green_arrow"},
        {"label": "Innsatsvei", "style": "red_arrow"},
    ]
    width, height = canvas.size
    box_w = min(420, int(width * 0.38))
    row_h = 28
    box_h = 78 + row_h * len(legend_entries)
    x0 = width - box_w - 24
    y0 = height - box_h - 24
    draw.rectangle([x0, y0, x0 + box_w, y0 + box_h], fill=(245, 247, 250), outline=(80, 95, 120), width=2)
    draw.text((x0 + 16, y0 + 14), spec.page_title or "Branntegning", fill=(30, 45, 70))
    draw.text((x0 + 16, y0 + 38), "Symbolforklaring", fill=(45, 60, 90))
    for idx, item in enumerate(legend_entries):
        y = y0 + 64 + idx * row_h
        style = STYLE_MAP.get(item.get("style", "dashed_red"), STYLE_MAP["dashed_red"])
        stroke = _safe_color(style.get("stroke"), "#000000")
        draw.text((x0 + 16, y), item.get("label", ""), fill=(20, 20, 20))
        sx = x0 + box_w - 110
        if item.get("style") == "dashed_red":
            draw_dashed_line(draw, (sx, y + 10), (sx + 70, y + 10), stroke, int(style.get("width", 3)))
        elif item.get("style") in {"red_arrow", "green_arrow"}:
            draw_arrow(draw, (sx, y + 10), (sx + 70, y + 10), stroke, int(style.get("width", 3)))
        elif item.get("style") == "pink_fill":
            fill = _safe_color(style.get("fill"), "#f6c0d0")
            draw.rectangle([sx, y, sx + 70, y + 16], outline=stroke, width=2, fill=fill)
        else:
            fill = style.get("fill")
            fill_rgb = _safe_color(fill, "#ffffff") if fill else None
            draw.rectangle([sx, y, sx + 70, y + 16], outline=stroke, width=2, fill=fill_rgb)
    return canvas


def try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if not text:
        return None
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence_match:
        text = fence_match.group(1)
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None
    return None


def ai_generate_report(images: List[Image.Image], prompt_text: str) -> str:
    model = get_model()
    if model is None:
        return "\n\n".join([
            "# 1. Dokumentinformasjon og grunnlag\nGenerer rapport med AI for full funksjon. Denne demoversjonen viser riktig struktur og leveranseformat.",
            "# 2. Formelle forhold og regelverk\nRegelverk og formelle forhold må bekreftes i prosjektet.",
            "# 3. Tiltakets omfang og prosjekteringsforutsetninger\nProsjekteringsforutsetninger hentes fra prosjektdata og tegningsgrunnlag.",
            "# 4. Oppsummering hovedføringer brannverntiltak\nHovedføringer bør omfatte brannklasse, risikoklasse, rømningsprinsipp, alarm og slokkeanlegg.",
            "# 5. Fravik og særskilte vurderinger\nFravik må dokumenteres eksplisitt og skilles fra preaksepterte løsninger.",
            "# 6. Kravspesifikasjon\nKravspesifikasjonen bør splittes per tema med ansvarlig fag.",
            "# 7. Uavklarte forhold og videre prosjektering\nSamle åpne avklaringer som må videreføres til detaljprosjekt.",
            "# 8. Vedlegg og tegningsliste\nVedlegg skal begrenses til modulens egne branntegninger og tegningsmanifest.",
        ])
    parts = [prompt_text] + images
    response = model.generate_content(parts)
    return getattr(response, "text", "") or ""


def ai_generate_markup(source_image: Image.Image, benchmark_images: List[Image.Image], prompt_text: str) -> FireMarkupSpec:
    model = get_model()
    if model is None:
        return FireMarkupSpec(
            page_title="Automatisk brannskisse – utkast",
            notes=["AI-modell ikke tilgjengelig. Dette er et standardisert demonstrasjonsutkast."],
            legend=[
                {"label": "Branncelle EI 60", "style": "dashed_red"},
                {"label": "Rømningsretning", "style": "green_arrow"},
                {"label": "Rømningsvei", "style": "green_band"},
            ],
            elements=[
                {"type": "note", "at": [80, 80], "style": "red_callout", "text": "Kontroller branncellegrenser manuelt."},
                {"type": "arrow", "points": [[120, 220], [260, 220]], "style": "green_arrow", "label": "Rømning"},
            ],
        )
    parts = [prompt_text, source_image] + benchmark_images[:2]
    response = model.generate_content(parts)
    parsed = try_parse_json(getattr(response, "text", "") or "")
    if not parsed:
        return FireMarkupSpec(page_title="Brannskisse – manglende JSON", notes=["AI-respons kunne ikke parses."], elements=[])
    return FireMarkupSpec(
        page_title=str(parsed.get("page_title", "Branntegning")),
        notes=list(parsed.get("notes", [])),
        legend=list(parsed.get("legend", [])),
        elements=list(parsed.get("elements", [])),
    )


# -----------------------------------------------------------------------------
# PDF generator
# -----------------------------------------------------------------------------
class CorporateFirePDF(FPDF):
    def header(self) -> None:  # pragma: no cover - PDF rendering helper
        if self.page_no() == 1:
            return
        self.set_y(12)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(37, 52, 77)
        self.cell(0, 8, clean_pdf_text(self.header_title), 0, 1, "R")
        self.set_draw_color(200, 208, 220)
        self.line(18, 20, 192, 20)
        self.set_y(25)

    def footer(self) -> None:  # pragma: no cover - PDF rendering helper
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(115, 125, 140)
        self.cell(0, 8, clean_pdf_text(f"Side {self.page_no()}"), 0, 0, "C")

    def chapter_title(self, title: str) -> None:  # pragma: no cover
        self.ln(4)
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(20, 37, 63)
        self.multi_cell(0, 8, clean_pdf_text(title))
        self.ln(1)

    def paragraph(self, text: str) -> None:  # pragma: no cover
        self.set_font("Helvetica", "", 10)
        self.set_text_color(0, 0, 0)
        self.multi_cell(0, 5, sanitize_multiline(text))
        self.ln(1)


def create_corporate_report_pdf(
    pd: ProjectData,
    report_text: str,
    manifest: List[Dict[str, str]],
    fire_drawings: List[Tuple[str, Image.Image]],
) -> bytes:
    pdf = CorporateFirePDF()
    pdf.header_title = f"{pd.p_name} | Brannkonsept"
    pdf.set_auto_page_break(True, 15)
    pdf.set_margins(18, 18, 18)

    # Cover page
    pdf.add_page()
    logo = logo_data_uri()
    if logo:
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                header_bytes = base64.b64decode(logo.split(",", 1)[1])
                Path(tmp.name).write_bytes(header_bytes)
                pdf.image(tmp.name, x=18, y=18, w=38)
        except Exception:
            pass
    pdf.set_y(52)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(15, 33, 58)
    pdf.cell(0, 12, clean_pdf_text("BRANNKONSEPT"), 0, 1)
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(55, 68, 89)
    pdf.multi_cell(0, 8, clean_pdf_text(pd.p_name))
    pdf.ln(14)

    meta = [
        ("Oppdragsgiver", pd.c_name or "Ikke angitt"),
        ("Adresse", pd.full_address or "Ikke angitt"),
        ("Formål", pd.b_type or "Ikke angitt"),
        ("Dato", datetime.now().strftime("%d.%m.%Y")),
        ("Dokument", "Brannkonsept – utkast for intern kvalitetssikring"),
    ]
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(32, 47, 70)
    for key, value in meta:
        pdf.cell(42, 7, clean_pdf_text(f"{key}:"), 0, 0)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(0, 7, clean_pdf_text(str(value)))
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(32, 47, 70)

    # Manifest page
    pdf.add_page()
    pdf.chapter_title("1. Tegningsmanifest")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(232, 237, 244)
    headers = [(68, "Fil"), (46, "Kategori"), (28, "Sider"), (28, "Kilde")]
    for width, title in headers:
        pdf.cell(width, 8, clean_pdf_text(title), 1, 0, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for item in manifest or [{"fil": "Ingen filer registrert", "kategori": "-", "sider lastet": "-", "kilde": "-"}]:
        pdf.cell(68, 8, clean_pdf_text(item["fil"][:42]), 1)
        pdf.cell(46, 8, clean_pdf_text(item["kategori"][:26]), 1)
        pdf.cell(28, 8, clean_pdf_text(item["sider lastet"]), 1)
        pdf.cell(28, 8, clean_pdf_text(item["kilde"]), 1)
        pdf.ln()

    # Content pages
    current_title = None
    buffer: List[str] = []

    def flush_buffer() -> None:
        nonlocal current_title, buffer
        if current_title is None:
            return
        pdf.add_page()
        pdf.chapter_title(current_title)
        body = "\n".join(buffer).strip() or "Ingen tekst generert."
        for paragraph in re.split(r"\n\s*\n", body):
            if paragraph.strip():
                pdf.paragraph(paragraph.strip())
        buffer = []

    for raw_line in report_text.splitlines():
        line = raw_line.strip()
        if line.startswith("# "):
            flush_buffer()
            current_title = line[2:].strip()
        else:
            buffer.append(raw_line)
    flush_buffer()

    # Append fire drawings only
    for title, image in fire_drawings:
        pdf.add_page()
        pdf.chapter_title(title)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            image.convert("RGB").save(tmp.name, format="JPEG", quality=92)
            available_w = 174
            img_w, img_h = image.size
            aspect = img_h / max(img_w, 1)
            render_w = available_w
            render_h = render_w * aspect
            if render_h > 240:
                render_h = 240
                render_w = render_h / max(aspect, 0.01)
            pdf.image(tmp.name, x=18 + (available_w - render_w) / 2, y=36, w=render_w)

    return bytes(pdf.output(dest="S"))


# -----------------------------------------------------------------------------
# Styling
# -----------------------------------------------------------------------------
st.markdown(
    """
<style>
:root {
    --bg: #f4f6fa;
    --surface: #ffffff;
    --surface-2: #f8f9fc;
    --stroke: #d9dee8;
    --text: #10233d;
    --muted: #61708a;
    --accent: #163b6b;
    --accent-soft: #e9eef7;
    --ok: #1d6f42;
    --radius: 16px;
}
html, body, [class*="css"] {
    font-family: Inter, ui-sans-serif, system-ui, sans-serif;
}
.stApp {
    background: var(--bg);
    color: var(--text);
}
header[data-testid="stHeader"] { visibility: hidden; height: 0; }
.block-container {
    max-width: 1380px !important;
    padding-top: 1.3rem !important;
    padding-bottom: 4rem !important;
}
.brand {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-bottom: 1.25rem;
}
.brand img { height: 48px; }
.metric-card {
    background: var(--surface);
    border: 1px solid var(--stroke);
    border-radius: var(--radius);
    padding: 1rem 1.1rem;
}
.metric-card .label {
    font-size: 0.82rem;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
.metric-card .value {
    font-size: 1.25rem;
    font-weight: 700;
    color: var(--text);
    margin-top: 0.2rem;
}
div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div {
    background: var(--surface) !important;
    border: 1px solid var(--stroke) !important;
    border-radius: 12px !important;
}
.stTabs [data-baseweb="tab-list"] {
    gap: 0.5rem;
}
.stTabs [data-baseweb="tab"] {
    background: var(--surface);
    border: 1px solid var(--stroke);
    border-radius: 12px;
    padding: 0.65rem 1rem;
    color: var(--text);
}
.stTabs [aria-selected="true"] {
    border-color: var(--accent) !important;
    box-shadow: inset 0 0 0 1px var(--accent);
}
button[kind="primary"] {
    background: var(--accent) !important;
    color: white !important;
    border: none !important;
    border-radius: 12px !important;
    font-weight: 700 !important;
}
button[kind="secondary"] {
    background: var(--surface) !important;
    color: var(--text) !important;
    border: 1px solid var(--stroke) !important;
    border-radius: 12px !important;
}
div[data-testid="stExpander"] {
    border: 1px solid var(--stroke) !important;
    border-radius: 14px !important;
    background: var(--surface) !important;
}
div[data-testid="stAlert"] {
    border-radius: 14px !important;
}
</style>
""",
    unsafe_allow_html=True,
)


# -----------------------------------------------------------------------------
# App
# -----------------------------------------------------------------------------
init_state()
pd = project_data()

if pd.p_name in {"", "Nytt Prosjekt"}:
    logo = logo_data_uri()
    if logo:
        render_html(f"<div class='brand'><img src='{logo}'><div><h2 style='margin:0;'>Builtly</h2></div></div>")
    st.warning("Prosjektdata må være satt opp før modulen brukes.")
    target = find_page("Project")
    if target and st.button("Gå til prosjektoppsett", type="primary"):
        st.switch_page(target)
    st.stop()

logo = logo_data_uri()
if logo:
    render_html(
        f"<div class='brand'><img src='{logo}'><div><div style='font-size:0.8rem;color:#61708a;'>Builtly</div>"
        f"<h1 style='margin:0;color:#10233d;font-size:2rem;'>Brannkonsept</h1></div></div>"
    )
else:
    st.title("Brannkonsept")

st.caption("Corporate RIBr-modul med delt leveranse for grunnlag, branntegninger og rapport.")

head1, head2, head3, head4 = st.columns(4)
for col, label, value in [
    (head1, "Prosjekt", pd.p_name),
    (head2, "Adresse", pd.full_address or "Ikke angitt"),
    (head3, "Etasjer", str(pd.etasjer)),
    (head4, "BTA", f"{pd.bta} m²"),
]:
    with col:
        render_html(f"<div class='metric-card'><div class='label'>{label}</div><div class='value'>{value}</div></div>")

with st.sidebar:
    st.write("Dokumentkonvensjoner")
    for item in BENCHMARK_CONVENTIONS:
        st.write(f"- {item}")
    target = find_page("Project")
    if target and st.button("Til prosjektoppsett", type="secondary"):
        st.switch_page(target)

# Defaults derived from project type
b_type_lower = (pd.b_type or "").lower()
default_rk = "RKL 2"
if "bolig" in b_type_lower:
    default_rk = "RKL 4"
elif "sykehus" in b_type_lower or "hotell" in b_type_lower:
    default_rk = "RKL 6"
elif "lager" in b_type_lower or "industri" in b_type_lower:
    default_rk = "RKL 1"
default_bk = "BKL 1"
if pd.etasjer in [3, 4]:
    default_bk = "BKL 2"
elif pd.etasjer >= 5:
    default_bk = "BKL 3"


tab_underlag, tab_strategi, tab_drawings, tab_report = st.tabs(
    ["Underlag", "Brannstrategi", "Branntegninger", "Rapport"]
)

with tab_underlag:
    st.subheader("1. Dokumentgrunnlag")
    left, right = st.columns([1.15, 1])
    with left:
        st.markdown("**Arkitekt- og prosjektunderlag**")
        source_files = st.file_uploader(
            "Last opp arkitektplaner, situasjonsplan eller andre kildetegninger",
            type=["pdf", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key="source_upload",
        )
        st.markdown("**Benchmark fra brannrådgiver**")
        benchmark_files = st.file_uploader(
            "Last opp referanse-branntegninger og rapporter som benchmark",
            type=["pdf", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key="benchmark_upload",
        )

        btn1, btn2, btn3 = st.columns(3)
        with btn1:
            if st.button("Oppdater dokumentsett", type="primary", use_container_width=True):
                shared = load_shared_project_images()
                st.session_state.source_documents = shared + ingest_streamlit_files(source_files or [], "uploaded")
                st.session_state.benchmark_documents = ingest_streamlit_files(benchmark_files or [], "benchmark")
                all_docs = st.session_state.source_documents + st.session_state.benchmark_documents
                st.session_state.drawing_manifest = build_drawing_manifest(all_docs)
                st.success(f"Dokumentsett oppdatert. Totalt {len(all_docs)} filer registrert.")
        with btn2:
            if st.button("Hent prosjektkart", type="secondary", use_container_width=True):
                img, source = fetch_map_image(pd.adresse, pd.kommune, pd.gnr, pd.bnr, os.environ.get("GOOGLE_API_KEY"))
                if img is not None:
                    st.session_state.fire_map = img
                    st.session_state.fire_map_source = source
                    st.success(f"Kart hentet fra {source}.")
                else:
                    st.warning(source)
        with btn3:
            st.checkbox(
                "Rapport skal kun vedlegge brannmodulens egne tegninger",
                key="include_only_module_drawings",
            )

    with right:
        st.markdown("**Prosjektoppsummering**")
        st.write(f"- Oppdragsgiver: {pd.c_name or 'Ikke angitt'}")
        st.write(f"- Formål: {pd.b_type or 'Ikke angitt'}")
        st.write(f"- Beskrivelse: {pd.p_desc or 'Ingen beskrivelse registrert'}")
        if st.session_state.fire_map is not None:
            st.image(st.session_state.fire_map, caption=st.session_state.fire_map_source, use_container_width=True)

    docs_to_show: List[UploadedSource] = st.session_state.source_documents + st.session_state.benchmark_documents
    if docs_to_show:
        st.markdown("**Manifest**")
        st.dataframe(st.session_state.drawing_manifest, use_container_width=True, hide_index=True)
        with st.expander("Forhåndsvis dokumenter", expanded=False):
            for doc in docs_to_show:
                st.markdown(f"**{doc.name}** · {doc.category} · {doc.source_kind}")
                cols = st.columns(min(3, max(1, len(doc.pages))))
                for idx, page in enumerate(doc.pages[:3]):
                    with cols[idx % len(cols)]:
                        st.image(page, caption=f"Side {idx + 1}", use_container_width=True)
    else:
        st.info("Ingen dokumenter er lastet inn ennå.")

with tab_strategi:
    st.subheader("2. Faglige forutsetninger")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        rk = st.selectbox("Risikoklasse", ["RKL 1", "RKL 2", "RKL 4", "RKL 6"], index=["RKL 1", "RKL 2", "RKL 4", "RKL 6"].index(default_rk))
    with c2:
        bk = st.selectbox("Brannklasse", ["BKL 1", "BKL 2", "BKL 3", "BKL 4"], index=["BKL 1", "BKL 2", "BKL 3", "BKL 4"].index(default_bk))
    with c3:
        sprinkler = st.selectbox("Slokkeanlegg", ["Ikke avklart", "Boligsprinkler", "Konvensjonell sprinkler", "Kombinasjon boligsprinkler og sprinkler"], index=0)
    with c4:
        alarm = st.selectbox("Brannalarm", ["Ikke avklart", "Heldekkende adresserbart anlegg", "Manuell varsling", "Blandet løsning"], index=1)

    unresolved = st.text_area(
        "Uavklarte forhold som skal inn i rapporten",
        placeholder="Eksempel: innsatsvei til p-kjeller må dokumenteres nærmere i detaljfasen.",
        height=120,
    )
    deviations = st.text_area(
        "Fravik / særskilte vurderinger",
        placeholder="Eksempel: ledesystem, ventilasjon, overskridelse av avstand til rømningsvei eller andre fravik som skal vurderes særskilt.",
        height=120,
    )

    st.markdown("**Målbilde for rapporten**")
    for heading in DEFAULT_REPORT_HEADINGS:
        st.write(f"- {heading}")

    sources_for_report = [page for doc in st.session_state.source_documents for page in doc.pages]
    if st.session_state.fire_map is not None:
        sources_for_report = sources_for_report + [st.session_state.fire_map]

    if st.button("Generer rapportutkast", type="primary"):
        prompt = build_report_prompt(
            pd=pd,
            rk=rk,
            bk=bk,
            sprinkler=sprinkler,
            alarm=alarm,
            unresolved=unresolved,
            deviations=deviations,
            manifest=st.session_state.drawing_manifest,
        )
        with st.spinner("Genererer rapportutkast..."):
            st.session_state.generated_report_text = ai_generate_report(sources_for_report, prompt)
        st.success("Rapportutkast generert.")

    if st.session_state.generated_report_text:
        st.markdown("**Rapportutkast**")
        st.text_area("Utkast", st.session_state.generated_report_text, height=420)

with tab_drawings:
    st.subheader("3. Branntegninger fra vanlige tegninger")
    source_candidates = [doc for doc in st.session_state.source_documents if doc.pages]
    benchmark_candidates = [doc for doc in st.session_state.benchmark_documents if doc.pages]
    if source_candidates:
        chosen_name = st.selectbox("Velg kildetegning", [doc.name for doc in source_candidates])
        selected_doc = next(doc for doc in source_candidates if doc.name == chosen_name)
        selected_page_idx = st.slider("Side", 1, len(selected_doc.pages), 1)
        selected_page = selected_doc.pages[selected_page_idx - 1]

        prev1, prev2 = st.columns([1.25, 1])
        with prev1:
            st.image(selected_page, caption=f"Kildetegning: {selected_doc.name} · side {selected_page_idx}", use_container_width=True)
        with prev2:
            st.markdown("**Benchmark-konvensjoner brukt som referanse**")
            for item in BENCHMARK_CONVENTIONS:
                st.write(f"- {item}")
            if benchmark_candidates:
                bench_preview = benchmark_candidates[0].pages[0]
                st.image(bench_preview, caption=f"Benchmark: {benchmark_candidates[0].name}", use_container_width=True)

        if st.button("Foreslå brannoverlay", type="primary"):
            prompt = build_drawing_prompt(pd, selected_doc.name)
            benchmark_pages = [doc.pages[0] for doc in benchmark_candidates if doc.pages]
            with st.spinner("Genererer tegningsforslag..."):
                spec = ai_generate_markup(selected_page, benchmark_pages, prompt)
                rendered = render_fire_overlay(selected_page, spec)
                st.session_state.generated_fire_drawings.append(
                    {
                        "title": spec.page_title or f"Branntegning – {selected_doc.name}",
                        "source": selected_doc.name,
                        "page": selected_page_idx,
                        "spec": spec,
                        "image": rendered,
                    }
                )
            st.success("Brannskisse generert som førsteutkast.")

    else:
        st.info("Last inn minst én kildetegning i fanen Underlag for å generere branntegning.")

    if st.session_state.generated_fire_drawings:
        st.markdown("**Genererte branntegninger**")
        for idx, item in enumerate(st.session_state.generated_fire_drawings, start=1):
            with st.expander(f"{idx}. {item['title']}", expanded=(idx == len(st.session_state.generated_fire_drawings))):
                st.image(item["image"], caption=f"Kilde: {item['source']} · side {item['page']}", use_container_width=True)
                notes = item["spec"].notes if isinstance(item["spec"], FireMarkupSpec) else []
                if notes:
                    st.write("Merknader:")
                    for note in notes:
                        st.write(f"- {note}")

with tab_report:
    st.subheader("4. Eksport")
    report_text = st.text_area(
        "Endelig rapporttekst",
        value=st.session_state.generated_report_text,
        height=360,
        placeholder="Generer rapportutkast i fanen Brannstrategi eller lim inn redigert tekst her.",
    )

    drawings_for_export: List[Tuple[str, Image.Image]] = []
    for item in st.session_state.generated_fire_drawings:
        drawings_for_export.append((item["title"], item["image"]))

    if st.button("Bygg PDF", type="primary"):
        pdf_bytes = create_corporate_report_pdf(
            pd=pd,
            report_text=report_text,
            manifest=st.session_state.drawing_manifest,
            fire_drawings=drawings_for_export,
        )
        st.session_state.generated_pdf = pdf_bytes
        st.success("PDF bygget.")

    if st.session_state.generated_pdf:
        st.download_button(
            "Last ned brannkonsept PDF",
            data=st.session_state.generated_pdf,
            file_name=f"Brannkonsept_{pd.p_name.replace(' ', '_')}.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )

        st.caption(
            "Eksporten følger manifest + rapportutkast + genererte branntegninger. Inputtegninger vedlegges ikke ukritisk."
        )
