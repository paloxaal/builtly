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

# --- 1. TEKNISK OPPSETT & GLOBALE STIER ---
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

# --- 2. HJELPEFUNKSJONER & DATA-HÅNDTERING ---
def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        p = Path(candidate)
        if p.exists():
            suffix = p.suffix.lower().replace(".", "") or "png"
            return f"data:image/{suffix};base64,{base64.b64encode(p.read_bytes()).decode('utf-8')}"
    return ""

def render_html(html_string: str):
    st.markdown(html_string.replace('\n', ' '), unsafe_allow_html=True)

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

def pick_model_name() -> str:
    try:
        valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for preferred in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash', 'models/gemini-pro']:
            if preferred in valid_models: return preferred
        return valid_models[0]
    except:
        return "models/gemini-1.5-flash"

def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{name}.py")
        if p.exists(): return str(p)
    return ""

def split_ai_sections(content: str):
    sections = []
    current = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            if current: sections.append(current)
            current = {"title": ironclad_text_formatter(line.lstrip("#").strip()), "lines": []}
            continue
        if current is None: continue
        current["lines"].append(raw_line.rstrip())
    if current: sections.append(current)
    return sections

def is_subheading_line(line: str) -> bool:
    clean = line.strip()
    if not clean: return False
    if clean.startswith("##"): return True
    if clean.endswith(":") and len(clean) < 80 and len(clean.split()) <= 7: return True
    if clean == clean.upper() and any(ch.isalpha() for ch in clean) and len(clean) < 70: return True
    return False

def is_bullet_line(line: str) -> bool:
    return bool(re.match(r"^([-*•]|\d+\.)\s+", line.strip()))

def strip_bullet(line: str) -> str:
    return re.sub(r"^([-*•]|\d+\.)\s+", "", line.strip())

# --- INITIALISERER PROSJEKTDATA ---
if "project_data" not in st.session_state:
    st.session_state.project_data = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": "", "b_type": "Bolig", "etasjer": 1, "bta": 0, "land": "Norge"}
if "source_documents" not in st.session_state:
    st.session_state.source_documents = []
if "generated_fire_drawings" not in st.session_state:
    st.session_state.generated_fire_drawings = []
if "generated_pdf" not in st.session_state:
    st.session_state.generated_pdf = None

if st.session_state.project_data.get("p_name") == "" and SSOT_FILE.exists():
    st.session_state.project_data = json.loads(SSOT_FILE.read_text(encoding="utf-8"))

pd_state = st.session_state.project_data

if pd_state.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_uri = logo_data_uri()
    if logo_uri: render_html(f"<div style='margin-bottom:2rem;'><img src='{logo_uri}' class='brand-logo'></div>")
    st.warning("⚠️ **Handling kreves:** Du må sette opp prosjektdataen før du kan bruke denne modulen.")
    target = find_page("Project")
    if target and st.button("⚙️ Gå til Project Setup", type="primary"):
        st.switch_page(target)
    st.stop()

# --- 3. DATAKLASSER FOR BRANNTEGNINGER ---
@dataclass
class FireMarkupSpec:
    page_title: str = ""
    elements: List[Dict[str, Any]] = field(default_factory=list)

STYLE_MAP = {
    "dashed_red": {"stroke": "#d32f2f", "width": 4},
    "green_band": {"stroke": "#2e7d32", "fill": "#9be79b", "width": 2},
    "green_arrow": {"stroke": "#2e7d32", "width": 4},
    "pink_fill": {"stroke": "#d16b8d", "fill": "#f6c0d0", "width": 2},
}

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

# --- 4. PDF MOTOR (CORPORATE LAYOUT) ---
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

    def ensure_space(self, h):
        if self.get_y() + h > 272: self.add_page()

    def section_title(self, title: str):
        self.ensure_space(30); self.ln(2)
        self.set_font("Helvetica", "B", 17); self.set_text_color(36, 50, 72)
        self.set_x(20); self.multi_cell(170, 8, clean_pdf_text(title.upper()), 0, "L")
        self.set_draw_color(204, 209, 216); self.line(20, self.get_y() + 1, 190, self.get_y() + 1); self.ln(5)

    def body_paragraph(self, text, first=False):
        text = ironclad_text_formatter(text)
        if not text: return
        self.set_x(20); self.set_font("Helvetica", "", 10.2 if not first else 10.6); self.set_text_color(35, 39, 43)
        self.multi_cell(170, 5.5, text, align='L')
        self.ln(1.6)

    def subheading(self, text):
        text = ironclad_text_formatter(text.replace("##", "").rstrip(":"))
        self.ensure_space(14); self.ln(2)
        self.set_x(20); self.set_font("Helvetica", "B", 10.8); self.set_text_color(229, 57, 53)
        self.cell(0, 6, clean_pdf_text(text.upper()), 0, 1)
        self.set_draw_color(225, 229, 234); self.line(20, self.get_y(), 190, self.get_y()); self.ln(2)

    def bullets(self, items, numbered=False):
        for idx, item in enumerate(items, start=1):
            clean = ironclad_text_formatter(item)
            if not clean: continue
            self.ensure_space(10); self.set_font("Helvetica", "", 10.1); self.set_text_color(35, 39, 43)
            start_y = self.get_y(); self.set_xy(22, start_y)
            self.cell(6, 5.2, f"{idx}." if numbered else "-", 0, 0, "L")
            self.set_xy(28, start_y); self.multi_cell(162, 5.2, clean); self.ln(0.8)

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
    pdf.set_xy(118, 45); pdf.kv_card([("Byggherre", pd_state.get("c_name")), ("RKL", brann_data['rkl']), ("BKL", brann_data['bkl']), ("Dato", datetime.now().strftime("%d.%m.%Y"))], x=118, width=72, title="Fakta")
    if cover_img:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            cover_img.convert("RGB").save(tmp.name, format="JPEG")
            w = 170; h = w * (cover_img.height/cover_img.width)
            if h > 130: h = 130; w = h / (cover_img.height/cover_img.width)
            pdf.image(tmp.name, x=20 + (170-w)/2, y=115, w=w)
    pdf.set_xy(20, 255); pdf.set_font("Helvetica", "", 8.8); pdf.set_text_color(104, 109, 116); pdf.multi_cell(170, 4.5, "Rapporten er generert av Builtly RIBr AI. Dette er et forprosjektutkast og skal underlegges kontroll før innsending.")

def create_full_report_pdf(pd_state, brann_data, content, sketches, raw_imgs):
    pdf = BuiltlyCorporatePDF("P", "mm", "A4")
    pdf.set_auto_page_break(True, margin=22); pdf.set_margins(18, 18, 18)
    pdf.header_left = clean_pdf_text(pd_state['p_name'])
    build_cover_page(pdf, pd_state, brann_data, raw_imgs[0] if raw_imgs else None)
    
    sections = split_ai_sections(content)
    pdf.add_page()
    for idx, sec in enumerate(sections):
        if idx > 0: pdf.ensure_space(35); pdf.ln(8)
        pdf.section_title(sec['title'])
        if sec['title'].startswith("1."):
            pdf.stats_row([("Risikoklasse", brann_data['rkl'], "TK1"), ("Brannklasse", brann_data['bkl'], "TK4"), ("Sprinkler", "Ja" if "Ja" in brann_data['sprinkler'] else "Nei", "TK2"), ("Etasjer", str(pd_state['etasjer']), "TK1")])
        for line in sec['lines']:
            if is_subheading_line(line): pdf.subheading(line)
            elif is_bullet_line(line): pdf.bullets([strip_bullet(line)])
            else: pdf.body_paragraph(line)

    for title, img in sketches:
        pdf.add_page(); pdf.section_title(title)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            img.convert("RGB").save(tmp.name, format="JPEG", quality=85)
            pdf.image(tmp.name, x=18, y=pdf.get_y(), w=174)
    return bytes(pdf.output(dest="S"))

# --- 5. AI TEGNE-LOGIKK ---
def draw_dashed_line(draw, p1, p2, fill, width, dash=10, gap=6):
    x1, y1, x2, y2 = p1[0], p1[1], p2[0], p2[1]
    length = max(((x2 - x1)**2 + (y2 - y1)**2)**0.5, 1.0)
    dx, dy = (x2-x1)/length, (y2-y1)/length
    curr = 0.0
    while curr < length:
        draw.line([(int(x1+dx*curr), int(y1+dy*curr)), (int(x1+dx*min(curr+dash, length)), int(y1+dy*min(curr+dash, length)))], fill=fill, width=width)
        curr += dash + gap

def draw_arrow(draw, p1, p2, fill, width):
    draw.line([p1, p2], fill=fill, width=width)
    vx, vy = p2[0]-p1[0], p2[1]-p1[1]
    L = max((vx**2 + vy**2)**0.5, 1.0)
    ux, uy = vx/L, vy/L
    w, b = 16, 18
    left = (int(p2[0]-ux*b-uy*w), int(p2[1]-uy*b+ux*w))
    right = (int(p2[0]-ux*b+uy*w), int(p2[1]-uy*b-ux*w))
    draw.polygon([p2, left, right], fill=fill)

def try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    match = re.search(r"(\{.*\})", text.strip().replace("\n",""), re.S)
    if match:
        try: return json.loads(match.group(1))
        except: return None
    return None

def render_fire_overlay(source_image: Image.Image, spec_json: str) -> Image.Image:
    parsed = try_parse_json(spec_json)
    if not parsed: return source_image
    canvas = source_image.convert("RGBA")
    overlay = Image.new("RGBA", canvas.size, (255,255,255,0))
    draw = ImageDraw.Draw(overlay)
    for el in parsed.get("elements", []):
        style = STYLE_MAP.get(el.get("style", "dashed_red"), STYLE_MAP["dashed_red"])
        stroke = ImageColor.getrgb(style.get("stroke"))
        width = int(style.get("width", 3))
        if el["type"] == "line" and len(el.get("points", [])) >= 2:
            for i in range(len(el["points"])-1):
                p1, p2 = tuple(el["points"][i]), tuple(el["points"][i+1])
                if el.get("style") == "dashed_red": draw_dashed_line(draw, p1, p2, stroke, width)
                else: draw.line([p1, p2], fill=stroke, width=width)
        elif el["type"] == "arrow" and len(el.get("points", [])) == 2:
            draw_arrow(draw, tuple(el["points"][0]), tuple(el["points"][1]), stroke, width)
    return Image.alpha_composite(canvas, overlay).convert("RGB")

# --- 6. UI ---
st.markdown("""
<style>
    :root { --bg: #06111a; --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df; --accent: #e53935; --radius-lg: 16px; }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(229,57,53,0.15)); }
    button[kind="primary"] { background: linear-gradient(135deg, #e53935, #ff5252) !important; color: white !important; font-weight: 750 !important; border-radius: 12px !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; background-color: #0c1520 !important; border-radius: 12px !important; }
    div[data-baseweb="base-input"], div[data-baseweb="select"] > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input, div[data-baseweb="select"] * { color: #ffffff !important; }
</style>
""", unsafe_allow_html=True)

top_l, top_r = st.columns([4, 1])
with top_l:
    logo_uri = logo_data_uri()
    if logo_uri: render_html(f"<div style='margin-bottom:2rem;'><img src='{logo_uri}' class='brand-logo'></div>")
    else: st.title("Builtly")
with top_r:
    if st.button("← Tilbake til SSOT", type="secondary", use_container_width=True): st.switch_page(find_page("Project"))

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem;'>", unsafe_allow_html=True)
st.markdown("<h1>🔥 Brannkonsept (RIBr)</h1>", unsafe_allow_html=True)
st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er synkronisert fra SSOT.")

with st.expander("1. Branntekniske Forutsetninger", expanded=True):
    c1, c2 = st.columns(2)
    rkl = c1.selectbox("Risikoklasse (RKL)", ["RKL 1", "RKL 2", "RKL 4", "RKL 6"], index=2)
    bkl = c2.selectbox("Brannklasse (BKL)", ["BKL 1", "BKL 2", "BKL 3", "BKL 4"], index=1)
    spr_opt = st.radio("Slokkeanlegg", ["Ja, fullsprinklet", "Nei, u-sprinklet"])
    alarm_opt = st.radio("Brannalarmanlegg", ["Heldekkende (Kategori 2)", "Manuell varsling"])

with st.expander("2. Last opp Grunnlag & Tegninger", expanded=True):
    source_files = st.file_uploader("Last opp arkitekttegninger (PDF/Bilder)", accept_multiple_files=True, type=['pdf','png','jpg','jpeg'])
    if source_files and st.button("Behandle og klargjør tegninger", type="secondary"):
        st.session_state.source_documents = []
        for f in source_files:
            raw = f.read()
            pages = pdf_to_images(raw) if f.name.lower().endswith(".pdf") else [Image.open(io.BytesIO(raw)).convert("RGB")]
            st.session_state.source_documents.append({"name": f.name, "pages": pages})
        st.success(f"Klargjort {len(st.session_state.source_documents)} dokumenter.")

with st.expander("3. AI-Assistert Branntegning (Tegn på plan)", expanded=True):
    if st.session_state.get("source_documents"):
        sel_doc_name = st.selectbox("Velg tegning for AI-skisse", [d['name'] for d in st.session_state.source_documents])
        sel_doc = next(d for d in st.session_state.source_documents if d['name'] == sel_doc_name)
        pg_cnt = len(sel_doc['pages'])
        sel_pg_idx = st.slider("Velg side", 1, pg_cnt, 1) if pg_cnt > 1 else 1
        sel_pg = sel_doc['pages'][sel_pg_idx-1]
        st.image(sel_pg, use_container_width=True)
        if st.button("🎨 Foreslå brann-overlay med AI", type="primary"):
            with st.spinner("AI studerer planen og tegner inn brannkrav..."):
                model = genai.GenerativeModel(pick_model_name())
                prompt = f"Lag et førsteutkast til brannoverlay for '{sel_doc_name}'. RKL: {rkl}, BKL: {bkl}. Returner KUN JSON: " + '{"elements":[{"type":"line","points":[[100,100],[500,100]],"style":"dashed_red","label":"EI60"}]}'
                res = model.generate_content([prompt, sel_pg])
                rendered = render_fire_overlay(sel_pg, res.text)
                st.session_state.generated_fire_drawings.append((f"AI-Skisse: {sel_doc_name} S{sel_pg_idx}", rendered))
                st.success("Tegning generert og lagt til i vedlegg!")
                st.image(rendered, width=600)
    else: st.info("Last opp tegninger i steg 2 først.")

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🚀 GENERER KOMPLETT BRANNKONSEPT (TEK17)", type="primary", use_container_width=True):
    with st.spinner("Analyserer underlag og skriver rapport..."):
        try:
            model = genai.GenerativeModel(pick_model_name())
            all_imgs = [p for d in st.session_state.get("source_documents", []) for p in d['pages']][:5]
            prompt = f"""
            Du er en senior RIBr. Skriv et komplett Brannkonsept iht. TEK17 for {pd_state['p_name']}.
            Forutsetninger: {rkl}, {bkl}, Sprinkler: {spr_opt}, Alarm: {alarm_opt}.
            START DIREKTE PÅ KAPITTEL 1. Ingen hilsen.
            I KAPITTEL 7: Skriv en KONKRET og operativ tiltaksplan (f.eks. hvilke skiller som skal være EI60).
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
            pdf_data = create_full_report_pdf(pd_state, {'rkl':rkl, 'bkl':bkl, 'sprinkler':spr_opt, 'alarm':alarm_opt}, res.text, st.session_state.get("generated_fire_drawings", []), all_imgs)
            st.session_state.generated_pdf = pdf_data
            st.rerun()
        except Exception as e: st.error(f"Feil: {e}")

if st.session_state.get("generated_pdf"):
    st.success("✅ Brannkonsept ferdigstilt!")
    st.download_button("📄 Last ned RIBr-rapport", st.session_state.generated_pdf, f"Builtly_RIBr_{pd_state['p_name']}.pdf", type="primary", use_container_width=True)
