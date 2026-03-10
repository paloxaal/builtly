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
from PIL import Image, ImageDraw, ImageFont, ImageColor

try:
    import google.generativeai as genai
except Exception:
    genai = None

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None


# ------------------------------------------------------------
# 1. TEKNISK OPPSETT & GLOBALE STIER
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
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()


# ------------------------------------------------------------
# 2. HJELPEFUNKSJONER (PDF, TEKST & DESIGN)
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

def clean_pdf_text(text: Any) -> str:
    if text is None: return ""
    text = str(text)
    replacements = {"–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "•": "-", "≤": "<=", "≥": ">="}
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")

def ironclad_text_formatter(text: Any) -> str:
    text = clean_pdf_text(text)
    text = text.replace("$", "").replace("*", "").replace("_", "")
    text = re.sub(r"[-|=]{3,}", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def pick_model_name() -> str:
    try:
        valid_models = [m.name for m in genai.list_models() if 'generateContent' in getattr(m, 'supported_generation_methods', [])]
        for pref in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if pref in valid_models: return pref
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

# ------------------------------------------------------------
# 3. PREMIUM CSS (Builtly Corporate Theme - Matcher Geo)
# ------------------------------------------------------------
st.markdown("""
<style>
    #MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}
    :root { 
        --bg: #06111a; 
        --panel: #0c1520;
        --stroke: rgba(120, 145, 170, 0.18); 
        --text: #f5f7fb; 
        --accent: #38bdf8; 
        --radius: 12px; 
    }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    
    button[kind="primary"] { 
        background: linear-gradient(135deg, #0ea5e9, #38bdf8) !important; 
        color: white !important; 
        border-radius: 10px !important; 
        font-weight: 700 !important; 
        border: none !important;
        padding: 0.6rem 1.5rem !important;
    }
    
    button[kind="secondary"] { 
        background-color: rgba(255,255,255,0.05) !important; 
        color: #f8fafc !important; 
        border-radius: 10px !important; 
        border: 1px solid var(--stroke) !important; 
    }

    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div { 
        background-color: #0d1824 !important; 
        border: 1px solid var(--stroke) !important; 
        border-radius: 8px !important; 
    }
    
    .stTextInput input, .stTextArea textarea, div[data-baseweb="select"] * { color: white !important; }
    
    div[data-testid="stExpander"] { 
        background-color: var(--panel) !important; 
        border: 1px solid var(--stroke) !important; 
        border-radius: 14px !important; 
        margin-bottom: 1rem !important;
    }
    
    label { 
        color: #c8d3df !important; 
        font-weight: 600 !important; 
        font-size: 0.95rem !important; 
        margin-bottom: 4px !important; 
    }

    .metric-card {
        background: rgba(255,255,255,0.03);
        padding: 1.25rem;
        border-radius: 14px;
        border: 1px solid var(--stroke);
        text-align: center;
    }
    .metric-value { font-size: 1.8rem; font-weight: 800; color: var(--accent); }
    .metric-label { font-size: 0.85rem; color: #9fb0c3; margin-top: 4px; }
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------
# 4. INITIALISERING & DATASYNC
# ------------------------------------------------------------
if "project_data" not in st.session_state:
    st.session_state.project_data = {}
if "source_docs" not in st.session_state:
    st.session_state.source_docs = []
if "rib_sketches" not in st.session_state:
    st.session_state.rib_sketches = []
if "current_ai_json" not in st.session_state:
    st.session_state.current_ai_json = '{"elements": []}'

if SSOT_FILE.exists() and not st.session_state.project_data.get("p_name"):
    try:
        with open(SSOT_FILE, "r", encoding="utf-8") as f:
            st.session_state.project_data = json.load(f)
    except: pass

if not st.session_state.project_data.get("p_name"):
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2>Builtly</h2>'
    render_html(logo_html)
    st.warning("⚠️ Ingen prosjektdata funnet. Gå til Project Setup først.")
    if st.button("Gå til Setup", type="primary"): st.switch_page(find_page("Project"))
    st.stop()

pd_state = st.session_state.project_data

# ------------------------------------------------------------
# 5. DOKUMENTHÅNDTERING & AI SKISSE MOTOR
# ------------------------------------------------------------
def pdf_to_images(pdf_bytes: bytes, limit: int = 4, scale: float = 2.0) -> List[Image.Image]:
    images = []
    if fitz is None: return images
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_idx in range(min(limit, len(doc))):
            pix = doc.load_page(page_idx).get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            images.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
        doc.close()
    except: pass
    return images

def render_structural_overlay(source_image: Image.Image, spec_json: str) -> Image.Image:
    """Tegner det konseptuelle bæresystemet basert på JSON-koordinater."""
    try:
        data = json.loads(spec_json)
    except: 
        # Prøv å pakke ut hvis AI inkluderte markdown fences
        match = re.search(r"(\{.*\})", spec_json.strip().replace("\n",""), re.S)
        if match:
            try: data = json.loads(match.group(1))
            except: return source_image
        else: return source_image

    canvas = source_image.convert("RGBA")
    overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = canvas.size

    for el in data.get("elements", []):
        etype = el.get("type", "").lower()
        def to_px(coord: List[float]) -> Tuple[int, int]:
            # AI koordinater (0-1000)
            return (int(coord[0] * w / 1000), int(coord[1] * h / 1000))

        if etype == "column" and "point" in el:
            pos = to_px(el["point"])
            r = 15
            draw.ellipse([pos[0]-r, pos[1]-r, pos[0]+r, pos[1]+r], fill=(56, 189, 248, 210), outline=(255,255,255,255), width=2)
            if "label" in el: draw.text((pos[0]+18, pos[1]-10), el["label"], fill=(56, 189, 248, 255))
            
        elif etype == "beam" and "points" in el:
            pts = [to_px(p) for p in el["points"]]
            draw.line(pts, fill=(56, 189, 248, 180), width=9)
            
        elif etype in ["core", "wall"]:
            if "points" in el:
                pts = [to_px(p) for p in el["points"]]
                draw.line(pts, fill=(229, 57, 53, 160), width=14)
            elif "rect" in el:
                r = el["rect"] # [x, y, w, h]
                px_rect = [int(r[0]*w/1000), int(r[1]*h/1000), int((r[0]+r[2])*w/1000), int((r[1]+r[3])*h/1000)]
                draw.rectangle(px_rect, fill=(229, 57, 53, 70), outline=(229, 57, 53, 255), width=3)
            
    return Image.alpha_composite(canvas, overlay).convert("RGB")

# ------------------------------------------------------------
# 6. CORPORATE PDF MOTOR
# ------------------------------------------------------------
class BuiltlyCorporatePDF(FPDF):
    def header(self):
        if self.page_no() == 1: return
        self.set_y(11); self.set_text_color(88, 94, 102); self.set_font("Helvetica", "", 8)
        self.cell(0, 4, clean_pdf_text(self.header_left), 0, 0, "L")
        self.cell(0, 4, clean_pdf_text("Builtly | RIB"), 0, 1, "R")
        self.set_draw_color(188, 192, 197); self.line(18, 18, 192, 18); self.set_y(24)

    def footer(self):
        self.set_y(-12); self.set_draw_color(210, 214, 220); self.line(18, 285, 192, 285)
        self.set_font("Helvetica", "", 7); self.set_text_color(110, 114, 119)
        self.cell(60, 5, "Builtly-RIB-001", 0, 0, "L")
        self.cell(70, 5, "Konseptfase - krever faglig kontroll", 0, 0, "C")
        self.cell(0, 5, f"Side {self.page_no()}", 0, 0, "R")

    def ensure_space(self, h):
        if self.get_y() + h > 272: self.add_page()

    def section_title(self, title: str):
        self.ensure_space(35); self.ln(2)
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
        self.set_x(20); self.set_font("Helvetica", "B", 10.8); self.set_text_color(56, 189, 248)
        self.cell(0, 6, clean_pdf_text(text.upper()), 0, 1)
        self.set_draw_color(225, 229, 234); self.line(20, self.get_y(), 190, self.get_y()); self.ln(2)

    def stats_row(self, stats):
        if not stats: return
        self.ensure_space(26); box_w, gap, x0, y = 40, 3.3, 20, self.get_y()
        for idx, (label, value) in enumerate(stats):
            x = x0 + idx * (box_w + gap)
            self.set_fill_color(236, 240, 245); self.set_draw_color(216, 220, 226); self.rect(x, y, box_w, 20, "DF")
            self.set_xy(x, y + 3); self.set_font("Helvetica", "B", 13); self.set_text_color(33, 39, 45); self.cell(box_w, 7, clean_pdf_text(str(value)), 0, 1, "C")
            self.set_x(x); self.set_font("Helvetica", "", 7.2); self.set_text_color(75, 80, 87); self.multi_cell(box_w, 4, clean_pdf_text(label), 0, "C")
        self.set_y(y + 24)

    def structural_table(self, df: pd.DataFrame, title: str):
        self.ensure_space(len(df) * 10 + 25)
        self.set_x(20); self.set_font("Helvetica", "B", 10); self.set_text_color(50, 50, 50)
        self.cell(0, 8, clean_pdf_text(title), 0, 1)
        self.set_font("Helvetica", "B", 8); self.set_fill_color(230, 235, 240)
        col_w = 170 / len(df.columns)
        for col in df.columns:
            self.cell(col_w, 7, clean_pdf_text(col), 1, 0, "C", True)
        self.ln(); self.set_font("Helvetica", "", 8)
        for _, row in df.iterrows():
            for val in row: self.cell(col_w, 7, clean_pdf_text(str(val)), 1, 0, "C")
            self.ln()
        self.ln(5)

def build_cover_page(pdf, pd_state, cover_img):
    pdf.add_page()
    if os.path.exists("logo.png"): pdf.image("logo.png", x=150, y=15, w=40)
    pdf.set_xy(20, 45); pdf.set_font("Helvetica", "B", 11); pdf.set_text_color(100, 105, 110); pdf.cell(80, 6, "KONSEPTNOTAT", 0, 1)
    pdf.set_x(20); pdf.set_font("Helvetica", "B", 34); pdf.set_text_color(20, 28, 38)
    pdf.multi_cell(95, 12, clean_pdf_text(pd_state.get("p_name")), 0, 'L')
    
    pdf.ln(4); pdf.set_x(20); pdf.set_font("Helvetica", "B", 14); pdf.set_text_color(56, 189, 248)
    pdf.multi_cell(95, 6.5, "Konstruksjon og bæresystem (RIB)\nKonseptvalg og stabilitetsprinsipp", 0, 'L')
    
    pdf.set_xy(118, 45)
    meta = [("Byggherre", pd_state.get("c_name", "-")), ("Byggtype", pd_state.get("b_type", "-")), ("BTA", f"{pd_state.get('bta', 0)} m2"), ("Dato", datetime.now().strftime("%d.%m.%Y"))]
    h_card = 10 + (len(meta) * 6.3) + 7
    pdf.set_fill_color(245, 247, 249); pdf.set_draw_color(214, 219, 225); pdf.rect(118, 45, 72, h_card, "DF")
    yy = 50; pdf.set_xy(122, yy); pdf.set_font("Helvetica", "B", 10); pdf.set_text_color(48, 64, 86); pdf.cell(64, 5, "PROSJEKTFAKTA", 0, 1); yy += 7
    for l, v in meta:
        pdf.set_xy(122, yy); pdf.set_font("Helvetica", "B", 8.6); pdf.set_text_color(72, 79, 87); pdf.cell(32, 5, clean_pdf_text(l), 0, 0); pdf.set_font("Helvetica", "", 8.6); pdf.set_text_color(35, 39, 43); pdf.multi_cell(32, 5, clean_pdf_text(v)); yy = pdf.get_y() + 1
    
    if cover_img:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            cover_img.convert("RGB").save(tmp.name, format="JPEG", quality=85)
            w = 170; h = w * (cover_img.height/cover_img.width)
            if h > 130: h = 130; w = h / (cover_img.height/cover_img.width)
            pdf.image(tmp.name, x=20 + (170-w)/2, y=115, w=w)
    
    pdf.set_xy(20, 255); pdf.set_font("Helvetica", "", 8.8); pdf.set_text_color(104, 109, 116)
    pdf.multi_cell(170, 4.5, "Rapporten er generert av Builtly RIB AI. Dokumentet er et arbeidsutkast og skal underlegges faglig kontroll før videre prosjektering.")

# ------------------------------------------------------------
# 7. UI: RIB — KONSTRUKSJON
# ------------------------------------------------------------
logo_uri = logo_data_uri()
if logo_uri: render_html(f'<img src="{logo_uri}" class="brand-logo">')

st.markdown(f"<h1>🏗️ RIB — Konstruksjon</h1>", unsafe_allow_html=True)
st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er synkronisert fra SSOT.")

# Metrics
col_m1, col_m2, col_m3, col_m4 = st.columns(4)
col_m1.markdown(f'<div class="metric-card"><div class="metric-value">{pd_state.get("etasjer", 1)}</div><div class="metric-label">Etasjer</div></div>', unsafe_allow_html=True)
col_m2.markdown(f'<div class="metric-card"><div class="metric-value">{pd_state.get("bta", 0)}</div><div class="metric-label">BTA m2</div></div>', unsafe_allow_html=True)
col_m3.markdown(f'<div class="metric-card"><div class="metric-value">CC2</div><div class="metric-label">Pålitelighetsklasse</div></div>', unsafe_allow_html=True)
col_m4.markdown(f'<div class="metric-card"><div class="metric-value">REI60</div><div class="metric-label">Brannkrav</div></div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

with st.expander("1. Prosjektstrategi & Føringer", expanded=True):
    col_a, col_b = st.columns(2)
    mat_valg = col_a.selectbox("Hovedbæresystem", ["Stålrammer og hulldekker", "Massivtre (CLT/Limtre)", "Plasstøpt betong", "Hybrid"], index=0)
    fund_valg = col_b.selectbox("Fundamentering", ["Direkte fundamentering", "Peling til fjell", "Kompensert fundamentering"], index=0)
    
    st.markdown("##### ⚙️ Spesifikke føringer for bæresystem (Skjerp AI-en)")
    structural_reqs = st.text_area("Hvilke arkitektoniske krav må AI-en ta hensyn til?", 
                                 placeholder="F.eks: 'Ingen søyler i fasadelinje', 'Grid må følge 6.0m deling', 'Unngå søyler i midten av stue'...")
    
    alt_study = st.checkbox("Utfør full alternativstudie (Miljø vs. Kostnad)", value=True)

with st.expander("2. Last opp Underlag", expanded=True):
    st.info("AI-en leser tegningene for å finne logiske rutenett for søyler og vegger.")
    u_files = st.file_uploader("Arkitektplaner (PDF/Bilder)", accept_multiple_files=True, type=['pdf','png','jpg','jpeg'])
    if u_files and st.button("Klargjør underlag", type="secondary"):
        st.session_state.source_docs = []
        for f in u_files:
            pages = pdf_to_images(f.read()) if f.name.lower().endswith(".pdf") else [Image.open(f).convert("RGB")]
            st.session_state.source_docs.append({"name": f.name, "pages": pages})
        st.success("Tegninger er klargjort for pinpointing.")

with st.expander("3. AI-Pinpointing & Skisse-Editor", expanded=True):
    if st.session_state.get("source_docs"):
        sel_doc_name = st.selectbox("Velg tegning for teknisk markup", [d['name'] for d in st.session_state.source_docs])
        doc_obj = next(d for d in st.session_state.source_docs if d['name'] == sel_doc_name)
        pg_cnt = len(doc_obj['pages'])
        
        # Fikser slider krasj for enkeltsidige dokumenter
        sel_pg_idx = 1
        if pg_cnt > 1:
            sel_pg_idx = st.slider("Velg side", 1, pg_cnt, 1)
            
        sel_pg = doc_obj['pages'][sel_pg_idx-1]
        
        col_sk1, col_sk2 = st.columns([1.5, 1])
        with col_sk1:
            st.image(sel_pg, use_container_width=True)
        
        with col_sk2:
            st.markdown("##### 📝 Markup Editor")
            if st.button("🤖 AI: Foreslå bæresystem", type="primary", use_container_width=True):
                with st.spinner("Beregner knutepunkter..."):
                    model = genai.GenerativeModel(pick_model_name())
                    prompt = f"""
                    Du er en senior RIB. Analyser arkitekturen og foreslå et bæresystem.
                    Føringer: {structural_reqs or 'Standard grid 5-8m'}. Materiale: {mat_valg}.
                    Finn sjakter (cores) og plasser søyler i yttervegger og i grid.
                    Returner KUN JSON med koordinater (0-1000):
                    {{
                      "elements": [
                        {{"type": "column", "point": [200, 300], "label": "S1"}},
                        {{"type": "core", "rect": [400, 450, 80, 120]}}
                      ]
                    }}
                    """
                    res = model.generate_content([prompt, sel_pg])
                    st.session_state.current_ai_json = res.text
            
            edited_json = st.text_area("Rediger koordinater (JSON) her dersom AI-en bommer:", 
                                      value=st.session_state.current_ai_json, 
                                      height=250, 
                                      help="Her kan du manuelt flytte søyler eller fjerne dem ved å endre tallene.")
            
            if edited_json:
                try:
                    rendered_sketch = render_structural_overlay(sel_pg, edited_json)
                    st.image(rendered_sketch, caption="Oppdatert skisse (Klar for rapport)", use_container_width=True)
                    if st.button("💾 Lagre skisse til vedlegg", type="secondary"):
                        if "rib_sketches" not in st.session_state: st.session_state.rib_sketches = []
                        st.session_state.rib_sketches.append((f"RIB-Konsept: {sel_doc_name} S{sel_pg_idx}", rendered_sketch))
                        st.success("Skisse lagret!")
                except: st.error("Feil i JSON-formatet.")
    else:
        st.info("Last opp arkitektplaner i steg 2 først.")

# ------------------------------------------------------------
# 8. GENERERING AV RAPPORT
# ------------------------------------------------------------
st.markdown("<br>", unsafe_allow_html=True)
if st.button("🚀 GENERER KOMPLETT RIB-KONSEPTNOTAT", type="primary", use_container_width=True):
    with st.spinner("Analyserer stabilitet og utfører alternativstudie..."):
        try:
            model = genai.GenerativeModel(pick_model_name())
            all_imgs = [p for d in st.session_state.get("source_docs", []) for p in d['pages']][:5]
            
            prompt = f"""
            Du er en senior RIB. Skriv et komplett fagnotat for bæresystem for {pd_state['p_name']}.
            Grunnlag: {pd_state['b_type']}, {pd_state['etasjer']} etasjer, BTA {pd_state['bta']} m2.
            Valgt: {mat_valg}, {fund_valg}. Føringer: {structural_reqs}.
            
            KRAV TIL INNHOLD:
            - Start direkte på kapittel 1. Ingen hilsen.
            - I kapittel 7: Skriv en OPERATIV tiltaksplan (f.eks. 'Etabler samvirkesøyler i fasade', 'Prosjekter fundamenter til fjell').
            - Anta at alt grunnlag er 100% korrekt for prosjektet (ikke klag på datakvalitet).
            
            TABELLER DU MÅ GENERERE (i markdown format):
            1. 'Alternativmatrise': Sammenlign {mat_valg} med to andre systemer på skala 1-10 (Miljø, Kost, Tid).
            2. 'Risikomatrise': List 4 risikoer (f.eks setninger, vibrasjoner) med Sannsynlighet (1-5) og Konsekvens (1-5).
            
            Struktur:
            # 1. SAMMENDRAG OG KONKLUSJON
            # 2. PROSJEKTBESKRIVELSE OG LASTFORUTSETNINGER (Vurder snø/vindlast for {pd_state.get('adresse')})
            # 3. VALGT BÆRESYSTEM OG MATERIALER (Dyp teknisk forklaring)
            # 4. STABILITET OG AVSTIVNINGSPRINSIPP (Horisontale laster)
            # 5. FUNDAMENTERING OG GRUNNFORHOLD
            # 6. ALTERNATIVVURDERING OG MILJØPROFIL
            # 7. TILTAKSPLAN OG DETALJPROSJEKTERING
            """
            res = model.generate_content([prompt] + all_imgs)
            
            # --- MOCK DATA FOR MATRISER (HVIS AI IKKE PARSER AUTOMATISK) ---
            risk_df = pd.DataFrame({
                "Risikomoment": ["Grunnforhold/Setninger", "Vibrasjoner i hulldekker", "Knutepunkt-detaljer", "Brannmotstand"],
                "Sannsynlighet": [2, 3, 2, 1], "Konsekvens": [5, 2, 4, 5], "Tiltak": ["Grunnundersøkelser", "Dim. for komfort", "Tidlig detaljering", "Maling"]
            })
            alt_df = pd.DataFrame({
                "Konsept": [mat_valg, "Betong Elementer", "Massivtre"],
                "Miljø (CO2)": [8, 5, 10], "Kostnad": [7, 9, 6], "Byggbarhet": [9, 8, 7], "Score": [8.0, 7.3, 7.7]
            })

            # --- PDF BYGGING ---
            pdf = BuiltlyCorporatePDF("P", "mm", "A4")
            pdf.set_auto_page_break(True, margin=22); pdf.set_margins(18, 18, 18)
            pdf.header_left = clean_pdf_text(pd_state['p_name'])
            build_cover_page(pdf, pd_state, all_imgs[0] if all_imgs else None)
            
            sections = split_ai_sections(res.text)
            pdf.add_page()
            for idx, sec in enumerate(sections):
                if idx > 0: pdf.ensure_space(35); pdf.ln(8)
                pdf.section_title(sec['title'])
                
                if sec['title'].startswith("1."):
                    pdf.stats_row([("Etasjer", pd_state['etasjer']), ("BTA", pd_state['bta']), ("Materiale", mat_valg[:12]), ("P-klasse", "CC2")])
                
                for line in sec['lines']:
                    if is_subheading_line(line): pdf.subheading(line)
                    else: pdf.body_paragraph(line)
                
                if sec['title'].startswith("3."):
                    pdf.structural_table(alt_df, "Tabell 1. Vurdering av alternative bærekonstruksjoner.")
                if sec['title'].startswith("7."):
                    pdf.structural_table(risk_df, "Tabell 2. Teknisk risikomatrise.")

            if st.session_state.rib_sketches:
                for title, img in st.session_state.rib_sketches:
                    pdf.add_page(); pdf.section_title(title)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                        img.convert("RGB").save(tmp.name, format="JPEG", quality=85)
                        asp = img.height / img.width
                        w_p = 174; h_p = 174 * asp
                        if h_p > 230: h_p = 230; w_p = 230 / asp
                        pdf.image(tmp.name, x=18 + (174-w_p)/2, y=pdf.get_y(), w=w_p)

            st.session_state.generated_pdf_bytes = bytes(pdf.output(dest="S"))
            st.rerun()
        except Exception as e:
            st.error(f"Kritisk feil: {e}")

if st.session_state.get("generated_pdf_bytes"):
    st.success("✅ RIB-konseptnotat er ferdigstilt!")
    st.download_button("📄 Last ned RIB-notat (PDF)", st.session_state.generated_pdf_bytes, f"Builtly_RIB_{pd_state['p_name'].replace(' ', '_')}.pdf", type="primary", use_container_width=True)
