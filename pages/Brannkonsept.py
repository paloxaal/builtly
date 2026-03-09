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

# --- 1. TEKNISK OPPSETT & STIER ---
st.set_page_config(page_title="Brannkonsept (RIBr) | Builtly", layout="wide", initial_sidebar_state="collapsed")

DB_DIR = Path("qa_database")
IMG_DIR = DB_DIR / "project_images"
SSOT_FILE = DB_DIR / "ssot.json"

DB_DIR.mkdir(exist_ok=True)
IMG_DIR.mkdir(exist_ok=True)

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

# --- 2. DATAKLASSER FOR BRANNTEGNINGER ---
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

# --- 3. HJELPEFUNKSJONER ---
def render_html(html_string: str):
    st.markdown(html_string.replace('\n', ' '), unsafe_allow_html=True)

def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        p = Path(candidate)
        if p.exists():
            suffix = p.suffix.lower().replace(".", "") or "png"
            return f"data:image/{suffix};base64,{base64.b64encode(p.read_bytes()).decode('utf-8')}"
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

def pdf_to_images(pdf_bytes: bytes, limit: int = 5, scale: float = 2.0) -> List[Image.Image]:
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
        pages = pdf_to_images(raw) if name.lower().endswith(".pdf") else [Image.open(io.BytesIO(raw)).convert("RGB")]
        if pages:
            documents.append(UploadedSource(name=name, category="Underlag", pages=pages))
    return documents

# --- 4. KART & AI TEGNING ---
def _safe_color(value: Optional[str], fallback: str = "#000000") -> Tuple[int, int, int]:
    try: return ImageColor.getrgb(value or fallback)
    except Exception: return ImageColor.getrgb(fallback)

def draw_dashed_line(draw, p1, p2, fill, width, dash=10, gap=6):
    x1, y1, x2, y2 = p1[0], p1[1], p2[0], p2[1]
    length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 1.0)
    dx, dy = (x2 - x1) / length, (y2 - y1) / length
    pos = 0.0
    while pos < length:
        draw.line([(int(x1 + dx * pos), int(y1 + dy * pos)), (int(x1 + dx * min(pos + dash, length)), int(y1 + dy * min(pos + dash, length)))], fill=fill, width=width)
        pos += dash + gap

def draw_arrow(draw, p1, p2, fill, width):
    draw.line([p1, p2], fill=fill, width=width)
    vx, vy = p2[0] - p1[0], p2[1] - p1[1]
    length = max((vx**2 + vy**2)**0.5, 1.0)
    ux, uy = vx / length, vy / length
    wing, back = 16, 18
    left = (int(p2[0] - ux * back - uy * wing), int(p2[1] - uy * back + ux * wing))
    right = (int(p2[0] - ux * back + uy * wing), int(p2[1] - uy * back - ux * wing))
    draw.polygon([p2, left, right], fill=fill)

def render_fire_overlay(source_image: Image.Image, spec: FireMarkupSpec) -> Image.Image:
    canvas = source_image.convert("RGBA")
    overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    for element in spec.elements:
        style = STYLE_MAP.get(element.get("style", "dashed_red"), STYLE_MAP["dashed_red"])
        stroke = _safe_color(style.get("stroke"))
        width = int(style.get("width", 3))
        etype = element.get("type")
        if etype == "line" and len(element.get("points", [])) >= 2:
            pts = element["points"]
            for i in range(len(pts)-1):
                if element.get("style") == "dashed_red": draw_dashed_line(draw, pts[i], pts[i+1], stroke, width)
                else: draw.line([tuple(pts[i]), tuple(pts[i+1])], fill=stroke, width=width)
        elif etype == "arrow" and len(element.get("points", [])) == 2:
            draw_arrow(draw, tuple(element["points"][0]), tuple(element["points"][1]), stroke, width)
        elif etype == "rect" and len(element.get("rect", [])) == 4:
            x, y, w, h = element["rect"]
            f = style.get("fill")
            fill_rgba = (_safe_color(f)[0], _safe_color(f)[1], _safe_color(f)[2], 100) if f else None
            draw.rectangle([x, y, x + w, y + h], outline=stroke, width=width, fill=fill_rgba)
    return Image.alpha_composite(canvas, overlay).convert("RGB")

def try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    match = re.search(r"(\{.*\})", text.strip().replace("\n", ""), re.S)
    if match:
        try: return json.loads(match.group(1))
        except: return None
    return None

# --- 5. CORPORATE PDF MOTOR ---
class BuiltlyCorporatePDF(FPDF):
    def header(self):
        if self.page_no() == 1: return
        self.set_y(11); self.set_text_color(88, 94, 102); self.set_font("Helvetica", "", 8)
        self.cell(0, 4, clean_pdf_text(self.header_left), 0, 0, "L")
        self.cell(0, 4, clean_pdf_text("Builtly | RIBr"), 0, 1, "R")
        self.set_draw_color(188, 192, 197); self.line(18, 18, 192, 18); self.set_y(24)

    def footer(self):
        self.set_y(-12); self.set_draw_color(210, 214, 220); self.line(18, 285, 192, 285)
        self.set_font("Helvetica", "", 7); self.set_text_color(110, 114, 119)
        self.cell(60, 5, "Builtly-RIBr-001", 0, 0, "L")
        self.cell(70, 5, "Utkast - krever faglig kontroll", 0, 0, "C")
        self.cell(0, 5, f"Side {self.page_no()}", 0, 0, "R")

    def ensure_space(self, needed_height: float):
        if self.get_y() + needed_height > 272: self.add_page()

    def section_title(self, title: str):
        self.ensure_space(25); self.ln(2)
        self.set_font("Helvetica", "B", 17); self.set_text_color(36, 50, 72)
        self.set_x(20); self.multi_cell(170, 8, clean_pdf_text(title.upper()), 0, "L")
        self.set_draw_color(204, 209, 216); self.line(20, self.get_y() + 1, 190, self.get_y() + 1); self.ln(5)

    def body_paragraph(self, text, first=False):
        text = ironclad_text_formatter(text)
        if not text: return
        self.set_x(20); self.set_font("Helvetica", "", 10.2 if not first else 10.6); self.set_text_color(35, 39, 43)
        self.multi_cell(170, 5.5, text, align='L')
        self.ln(1.6)

    def stats_row(self, stats):
        if not stats: return
        self.ensure_space(26); box_w, gap, x0, y = 40, 3.3, 20, self.get_y()
        fill_map = {"TK1": (236, 240, 245), "TK2": (220, 252, 231), "TK4": (254, 240, 138), "TK5": (254, 202, 202)}
        for idx, (label, value, class_code) in enumerate(stats):
            x = x0 + idx * (box_w + gap)
            self.set_fill_color(*fill_map.get(class_code, (245, 247, 249)))
            self.set_draw_color(216, 220, 226); self.rect(x, y, box_w, 20, "DF")
            self.set_xy(x, y + 3); self.set_font("Helvetica", "B", 15); self.set_text_color(33, 39, 45); self.cell(box_w, 7, clean_pdf_text(str(value)), 0, 1, "C")
            self.set_x(x); self.set_font("Helvetica", "", 7.8); self.set_text_color(75, 80, 87); self.multi_cell(box_w, 4, clean_pdf_text(label), 0, "C")
        self.set_y(y + 24)

    def kv_card(self, items, x, width, title=None):
        h = 10 + (len(items) * 6.3) + (7 if title else 0)
        self.ensure_space(h + 3); start_y = self.get_y()
        self.set_fill_color(245, 247, 249); self.set_draw_color(214, 219, 225); self.rect(x, start_y, width, h, "DF")
        yy = start_y + 5
        if title:
            self.set_xy(x + 4, yy); self.set_font("Helvetica", "B", 10); self.set_text_color(48, 64, 86); self.cell(width - 8, 5, clean_pdf_text(title.upper()), 0, 1); yy += 7
        for l, v in items:
            self.set_xy(x + 4, yy); self.set_font("Helvetica", "B", 8.6); self.set_text_color(72, 79, 87); self.cell(32, 5, clean_pdf_text(l), 0, 0); self.set_font("Helvetica", "", 8.6); self.set_text_color(35, 39, 43); self.multi_cell(width - 38, 5, clean_pdf_text(v)); yy = self.get_y() + 1
        self.set_y(max(self.get_y(), start_y + h))

def build_cover_page(pdf, pd_state, brann_data, cover_img):
    pdf.add_page()
    if os.path.exists("logo.png"): pdf.image("logo.png", x=150, y=15, w=40)
    pdf.set_xy(20, 45); pdf.set_font("Helvetica", "B", 11); pdf.set_text_color(100, 105, 110); pdf.cell(80, 6, "RAPPORT", 0, 1)
    pdf.set_x(20); pdf.set_font("Helvetica", "B", 34); pdf.set_text_color(20, 28, 38); pdf.multi_cell(95, 12, clean_pdf_text(pd_state.get("p_name")), 0, 'L')
    pdf.ln(4); pdf.set_x(20); pdf.set_font("Helvetica", "B", 14); pdf.set_text_color(229, 57, 53); pdf.multi_cell(95, 6.5, "Brannteknisk konsept (RIBr)\nTEK17 Dokumentasjon", 0, 'L')
    pdf.set_xy(118, 45); pdf.kv_card([("Oppdragsgiver", pd_state.get("c_name")), ("RKL", brann_data['rkl']), ("BKL", brann_data['bkl']), ("Dato", datetime.now().strftime("%d.%m.%Y"))], x=118, width=72, title="Fakta")
    if cover_img:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            cover_img.convert("RGB").save(tmp.name, format="JPEG", quality=85)
            w = 170; h = w * (cover_img.height/cover_img.width)
            if h > 130: h = 130; w = h / (cover_img.height/cover_img.width)
            pdf.image(tmp.name, x=20 + (170-w)/2, y=115, w=w)
    pdf.set_xy(20, 255); pdf.set_font("Helvetica", "", 8.8); pdf.set_text_color(104, 109, 116); pdf.multi_cell(170, 4.5, "Rapporten er generert av Builtly RIBr AI. Dette er et forprosjektutkast og skal underlegges kontroll før innsending.")

# --- 6. PREMIUM CSS & UI ---
st.markdown("""
<style>
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(229,57,53,0.15)); }
    button[kind="primary"] { background: linear-gradient(135deg, #e53935, #ff5252) !important; color: white !important; font-weight: 750 !important; border-radius: 12px !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; background-color: #0c1520 !important; border-radius: 12px !important; }
</style>
""", unsafe_allow_html=True)

# --- 7. UI MODULEN ---
top_l, top_r = st.columns([4, 1])
with top_l:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2>Builtly</h2>'
    render_html(logo_html)
with top_r:
    if st.button("← Tilbake til SSOT", type="secondary"): st.switch_page(find_page("Project"))

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem;'>", unsafe_allow_html=True)
st.markdown("<h1>🔥 Brannkonsept (RIBr)</h1>", unsafe_allow_html=True)
st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er synkronisert.")

with st.expander("1. Branntekniske Forutsetninger", expanded=True):
    c1, c2 = st.columns(2)
    rkl = c1.selectbox("Risikoklasse (RKL)", ["RKL 1", "RKL 2", "RKL 3", "RKL 4", "RKL 5", "RKL 6"], index=3)
    bkl = c2.selectbox("Brannklasse (BKL)", ["BKL 1", "BKL 2", "BKL 3", "BKL 4"], index=1)
    c3, c4 = st.columns(2)
    sprinkler = c3.radio("Slokkeanlegg", ["Ja, fullsprinklet", "Nei, u-sprinklet"])
    alarm = c4.radio("Brannalarmanlegg", ["Kategori 2 (Heldekkende)", "Manuell varsling"])

with st.expander("2. Visuelt Underlag (Arkitektur & Kart)", expanded=True):
    st.info("Last opp arkitekttegninger. AI-en vil analysere dem for rømning og brannceller.")
    source_files = st.file_uploader("Last opp PDF eller bilder", accept_multiple_files=True, type=['pdf','png','jpg','jpeg'])
    if st.button("Oppdater dokumentsett", type="secondary"):
        if source_files:
            st.session_state.source_documents = ingest_streamlit_files(source_files, "uploaded")
            st.success(f"Lagt til {len(st.session_state.source_documents)} dokumenter.")
        else: st.warning("Velg filer først.")

with st.expander("3. AI-Assistert Branntegning (Tegn på plan)", expanded=True):
    sources = [doc for doc in st.session_state.source_documents if doc.pages]
    if sources:
        sel_name = st.selectbox("Velg kildetegning", [d.name for d in sources])
        sel_doc = next(d for d in sources if d.name == sel_name)
        pg_cnt = len(sel_doc.pages)
        sel_pg_idx = st.slider("Side", 1, pg_cnt, 1) if pg_cnt > 1 else 1
        sel_pg = sel_doc.pages[sel_pg_idx - 1]
        st.image(sel_pg, width=600)
        if st.button("Foreslå brann-overlay med AI", type="primary"):
            with st.spinner("AI studerer planen og tegner inn brannkrav..."):
                model = genai.GenerativeModel("gemini-1.5-pro")
                prompt = f"Lag et førsteutkast til brannoverlay for '{sel_name}'. RKL: {rkl}, BKL: {bkl}. " + """
                Marker branncellegrenser, rømningsvei og retning. Returner KUN gyldig JSON: 
                {"page_title":"Branntegning","elements":[{"type":"line","points":[[100,100],[200,200]],"style":"dashed_red","label":"EI60"}]}
                """
                res = model.generate_content([prompt, sel_pg])
                parsed = try_parse_json(res.text)
                if parsed:
                    spec = FireMarkupSpec(page_title=parsed.get("page_title"), elements=parsed.get("elements", []))
                    rendered = render_fire_overlay(sel_pg, spec)
                    st.session_state.generated_fire_drawings.append((f"AI-Skisse: {sel_name} (Side {sel_pg_idx})", rendered))
                    st.success("Tegning generert og lagt til i vedlegg!")
                    st.image(rendered, width=600)
    else: st.info("Last opp tegninger i steg 2 først.")

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🚀 GENERER KOMPLETT BRANNKONSEPT", type="primary", use_container_width=True):
    with st.spinner("Analyserer underlag og skriver rapport..."):
        try:
            model = genai.GenerativeModel("gemini-1.5-pro")
            all_imgs = [p for d in st.session_state.source_documents for p in d.pages][:5]
            prompt = f"""
            Du er en senior RIBr. Skriv et komplett Brannkonsept iht. TEK17 for {pd_state['p_name']}.
            Forutsetninger: {rkl}, {bkl}, Sprinkler: {sprinkler}, Alarm: {alarm}.
            START DIREKTE PÅ KAPITTEL 1. Ingen hilsen.
            I KAPITTEL 7: Skriv en KONKRET og operativ tiltaksplan (f.eks. hvilke skiller som skal være EI60).
            Anta at alt datagrunnlag er 100% korrekt.
            Struktur:
            # 1. SAMMENDRAG
            # 2. PROSJEKTBESKRIVELSE
            # 3. BÆREEVNE OG BRANNSKILLER
            # 4. RØMNING OG LEDESYSTEM
            # 5. SLOKKING OG TILKOMST
            # 6. FRAVIK
            # 7. TILTAKSPLAN FOR DETALJPROSJEKT
            """
            res = model.generate_content([prompt] + all_imgs)
            
            # --- PDF GENERERING ---
            pdf = BuiltlyCorporatePDF("P", "mm", "A4")
            pdf.set_auto_page_break(True, margin=22); pdf.set_margins(18, 18, 18)
            pdf.header_left = clean_pdf_text(pd_state['p_name'])
            build_cover_page(pdf, pd_state, {'rkl':rkl, 'bkl':bkl}, all_imgs[0] if all_imgs else None)
            
            sections = split_ai_sections(res.text)
            pdf.add_page()
            for idx, sec in enumerate(sections):
                if idx > 0: pdf.ensure_space(30); pdf.ln(8)
                pdf.section_title(sec['title'])
                if sec['title'].startswith("1."):
                    pdf.stats_row([("Risikoklasse", rkl, "TK1"), ("Brannklasse", bkl, "TK4"), ("Sprinkler", "Ja" if "Ja" in sprinkler else "Nei", "TK2" if "Ja" in sprinkler else "TK5"), ("Etasjer", str(pd_state['etasjer']), "TK1")])
                for line in sec['lines']:
                    if is_subheading_line(line): pdf.subheading(line)
                    else: pdf.body_paragraph(line)

            # Legger til tegninger
            for title, img in st.session_state.generated_fire_drawings:
                pdf.add_page(); pdf.section_title(title)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                    img.convert("RGB").save(tmp.name, format="JPEG", quality=85)
                    pdf.image(tmp.name, x=18, y=pdf.get_y(), w=174)

            st.session_state.generated_pdf = bytes(pdf.output(dest="S"))
            st.rerun()
        except Exception as e: st.error(f"Feil: {e}")

if st.session_state.generated_pdf:
    st.success("✅ Brannkonseptet er ferdigstilt!")
    st.download_button("📄 Last ned Brannkonsept (PDF)", st.session_state.generated_pdf, f"Builtly_RIBr_{pd_state['p_name']}.pdf", type="primary", use_container_width=True)
