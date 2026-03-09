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
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Brannkonsept (RIBr) | Builtly", layout="wide", initial_sidebar_state="collapsed")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()

try:
    import fitz  
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

# --- 2. PREMIUM CSS ---
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

# --- 3. SESSION STATE ---
DB_DIR = Path("qa_database")
SSOT_FILE = DB_DIR / "ssot.json"

if "project_data" not in st.session_state:
    st.session_state.project_data = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": "", "b_type": "Bolig", "etasjer": 4, "bta": 0, "land": "Norge"}

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

# --- 4. DYNAMISK PDF MOTOR (CORPORATE LAYOUT) ---
def split_ai_sections(content: str):
    sections = []
    current = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            if current: sections.append(current)
            current = {"title": ironclad_text_formatter(line.lstrip("#").strip()), "lines": []}
            continue
        if current is None: 
            continue # Ignorerer AI intro
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

class BuiltlyCorporatePDF(FPDF):
    def header(self):
        if self.page_no() == 1: return
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

    def body_paragraph(self, text, first=False):
        text = ironclad_text_formatter(text)
        if not text: return
        self.set_x(20)
        self.set_font("Helvetica", "", 10.2 if not first else 10.6)
        self.set_text_color(35, 39, 43)
        self.multi_cell(170, 5.5 if not first else 5.7, text)
        self.ln(1.6)

    def subheading(self, text):
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

    def bullets(self, items, numbered=False):
        for idx, item in enumerate(items, start=1):
            clean = ironclad_text_formatter(item)
            if not clean: continue
            self.ensure_space(10)
            self.set_font("Helvetica", "", 10.1)
            self.set_text_color(35, 39, 43)
            start_y = self.get_y()
            self.set_xy(22, start_y)
            self.cell(6, 5.2, f"{idx}." if numbered else "-", 0, 0, "L")
            self.set_xy(28, start_y)
            self.multi_cell(162, 5.2, clean)
            self.ln(0.8)

    def section_title(self, title: str):
        self.ensure_space(35)
        self.ln(2)
        title = ironclad_text_formatter(title)
        num_match = re.match(r"^(\d+\.?\d*)\s*(.*)$", title)
        number, text = (num_match.group(1).rstrip("."), num_match.group(2).strip()) if num_match and (num_match.group(1).endswith(".") or num_match.group(2)) else (None, title)
        
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

    def rounded_rect(self, x, y, w, h, r, style="", corners="1234"):
        try: super().rounded_rect(x, y, w, h, r, style, corners)
        except Exception: self.rect(x, y, w, h, style if style in {"F", "FD", "DF"} else "")

    def kv_card(self, items, x=None, width=80, title=None):
        if x is None: x = self.get_x()
        height = 10 + (len(items) * 6.3) + (7 if title else 0)
        self.ensure_space(height + 3)
        start_y = self.get_y()
        self.set_fill_color(245, 247, 249)
        self.set_draw_color(214, 219, 225)
        self.rounded_rect(x, start_y, width, height, 4, "1234", "DF")
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
            self.cell(32, 5, clean_pdf_text(label), 0, 0)
            self.set_font("Helvetica", "", 8.6)
            self.set_text_color(35, 39, 43)
            self.multi_cell(width - 38, 5, clean_pdf_text(value))
            yy = self.get_y() + 1
        self.set_y(max(self.get_y(), start_y + height))
        
    def highlight_box(self, title: str, items, fill=(255, 245, 245), accent=(229, 57, 53)):
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
        self.set_draw_color(245, 200, 200)
        self.rounded_rect(x, y, 170, box_h, 4, "1234", "DF")
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

    # --- DEN MANGLENDE FUNKSJONEN SOM KAN LAGE FAKTABOKSENE PÅ SIDE 2 ---
    def stats_row(self, stats):
        if not stats: return
        self.ensure_space(26)
        box_w, gap, x0, y = 40, 3.3, 20, self.get_y()
        
        # Farger skreddersydd for brannrapporten (Grønt = Bra, Gult = Obs, Rødt = Risiko)
        fill_map = {
            "TK1": (236, 240, 245), # Nøytral grå/blå
            "TK2": (220, 252, 231), # Lysegrønn (F.eks Sprinklet)
            "TK4": (254, 240, 138), # Gul (BKL3 etc)
            "TK5": (254, 202, 202), # Rød (F.eks Usprinklet)
        }

        for idx, (label, value, class_code) in enumerate(stats):
            x = x0 + idx * (box_w + gap)
            self.set_fill_color(*fill_map.get(class_code, (245, 247, 249)))
            self.set_draw_color(216, 220, 226)
            self.rounded_rect(x, y, box_w, 20, 3, "1234", "DF")
            self.set_xy(x, y + 3)
            self.set_font("Helvetica", "B", 15)
            self.set_text_color(33, 39, 45)
            self.cell(box_w, 7, clean_pdf_text(str(value)), 0, 1, "C")
            self.set_x(x)
            self.set_font("Helvetica", "", 7.8)
            self.set_text_color(75, 80, 87)
            self.multi_cell(box_w, 4, clean_pdf_text(label), 0, "C")
        self.set_y(y + 24)

def build_cover_page(pdf, project_data, brann_data):
    pdf.add_page()
    pdf.set_draw_color(120, 124, 130)
    pdf.line(18, 18, 192, 18)
    
    if os.path.exists("logo.png"):
        try: pdf.image("logo.png", x=150, y=15, w=40)
        except: pass

    pdf.set_xy(20, 45)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(100, 105, 110)
    pdf.cell(80, 6, clean_pdf_text("RAPPORT"), 0, 1, "L")

    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 34) 
    pdf.set_text_color(20, 28, 38)
    pdf.multi_cell(95, 12, clean_pdf_text(project_data.get("p_name", "Brannkonsept")), 0, 'L')
    
    pdf.ln(4)
    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(229, 57, 53) # Rød accentfarge for Brann
    pdf.multi_cell(95, 6.5, clean_pdf_text("Brannteknisk konsept (RIBr)\nTEK17 Dokumentasjon"), 0, 'L')

    pdf.set_xy(118, 45)
    meta_items = [
        ("Oppdragsgiver", project_data.get("c_name") or "-"),
        ("Kommune", project_data.get("kommune") or "-"),
        ("Risikoklasse", brann_data['rkl']),
        ("Brannklasse", brann_data['bkl']),
        ("Sprinklet", brann_data['sprinkler']),
        ("Brannalarm", brann_data['alarm']),
        ("Dato / revisjon", datetime.now().strftime("%d.%m.%Y") + " / 01"),
        ("Dokumentkode", "Builtly-RIBr-001"),
    ]
    pdf.kv_card(meta_items, x=118, width=72)

    pdf.set_fill_color(244, 246, 248)
    pdf.set_draw_color(220, 224, 228)
    pdf.rounded_rect(20, 140, 170, 70, 4, "1234", "DF")
    pdf.set_xy(24, 165)
    pdf.set_font("Helvetica", "I", 12)
    pdf.set_text_color(112, 117, 123)
    pdf.multi_cell(160, 6, clean_pdf_text("Bildevedlegg genereres automatisk bak i rapporten basert på opplastede branntegninger og arkitektur."), 0, "C")

    pdf.set_xy(20, 255)
    pdf.set_font("Helvetica", "", 8.8)
    pdf.set_text_color(104, 109, 116)
    pdf.multi_cell(170, 4.5, clean_pdf_text("Rapporten er generert av Builtly RIBr AI på bakgrunn av prosjektdata, opplastede branntegninger og TEK17 preaksepterte ytelser. Dokumentet er et forprosjektutkast og skal underlegges uavhengig kontroll (tiltaksklasse 3) før innsending."))

def render_ai_section_body(pdf, lines):
    paragraph_buffer, bullet_buffer, first_para, empty_line_count = [], [], True, 0

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
            pdf.bullets([strip_bullet(item) for item in bullet_buffer], numbered=all(re.match(r"^\d+\.\s+", item.strip()) for item in bullet_buffer))
        bullet_buffer = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_bullets()
            empty_line_count += 1
            if empty_line_count == 1: pdf.ln(3) 
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

def create_full_report_pdf(project_data, brann_data, content, maps):
    pdf = BuiltlyCorporatePDF("P", "mm", "A4")
    pdf.set_auto_page_break(True, margin=22) 
    pdf.set_margins(18, 18, 18)
    pdf.header_left, pdf.header_right, pdf.doc_code = clean_pdf_text(project_data.get("p_name", "Brannkonsept")), clean_pdf_text("Builtly | RIBr"), clean_pdf_text("Builtly-RIBr-001")

    build_cover_page(pdf, project_data, brann_data)

    # Innholdsfortegnelse
    pdf.add_page()
    pdf.section_title("INNHOLDSFORTEGNELSE")
    items = ["1. Sammendrag og hovedføringer", "2. Prosjektbeskrivelse og forutsetninger", "3. Bæreevne og brannskiller (TEK17 §11-4 - §11-8)", "4. Rømning og ledesystem (TEK17 §11-11 - §11-14)", "5. Slokking og brannvesenets tilkomst (TEK17 §11-16 - §11-17)", "Vedlegg: Vurdert tegningsgrunnlag"]
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
    pdf.highlight_box("Om dokumentet", ["Brannkonseptet er strukturert iht. TEK17 kapittel 11.", "Visuelle tolkninger av branntegninger er integrert direkte i teksten under relevante avsnitt."])

    sections = split_ai_sections(content) or [{"title": "1. SAMMENDRAG OG KONKLUSJON", "lines": [content]}]
    
    pdf.add_page()
    for idx, section in enumerate(sections):
        title = section.get("title", "")
        
        if title.startswith("Vedlegg"):
            pdf.add_page()
        elif idx > 0:
            pdf.ensure_space(35)
            if pdf.get_y() > 35: pdf.ln(8)

        pdf.section_title(title)

        if title.startswith("1."):
            pdf.ensure_space(30)
            stats = [
                ("Risikoklasse", brann_data['rkl'], "TK1"),
                ("Brannklasse", brann_data['bkl'], "TK4"),
                ("Sprinkler", "Ja" if "Ja" in brann_data['sprinkler'] else "Nei", "TK2" if "Ja" in brann_data['sprinkler'] else "TK5"),
                ("Etasjer", str(project_data.get('etasjer', 1)), "TK1")
            ]
            pdf.stats_row(stats)

        render_ai_section_body(pdf, section.get("lines", []))

    if maps and len(maps) > 0:
        pdf.add_page()
        pdf.section_title("Vedlegg: Vurdert tegningsgrunnlag")
        for i, m in enumerate(maps):
            if i > 0: pdf.add_page()
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                m.convert("RGB").save(tmp.name, format="JPEG", quality=85)
                
                # Beregn aspektratio for å maksimere bilde på A4
                aspect = m.height / max(m.width, 1)
                w = 174
                h = w * aspect
                
                # Sjekk om det blir for høyt for siden (Maks høyde ca 240mm på A4 med marger)
                if h > 240: 
                    h = 240
                    w = h / aspect
                
                x = 18 + (174 - w) / 2
                pdf.image(tmp.name, x=x, y=pdf.get_y(), w=w)
                
                pdf.set_y(pdf.get_y() + h + 5)
                pdf.set_x(18)
                pdf.set_font('Helvetica', 'I', 9)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 8, clean_pdf_text(f"Figur V-{i+1}: Tegningsgrunnlag analysert av Builtly RIBr AI."), 0, 1, 'C')

    out = pdf.output(dest="S")
    return bytes(out) if isinstance(out, (bytes, bytearray)) else out.encode("latin-1")

# --- 5. HEADER ---
top_l, top_r = st.columns([4, 1])
with top_l:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(logo_html)
with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("← Tilbake til SSOT", use_container_width=True, type="secondary"):
        st.switch_page(find_page("Project"))

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)

# --- 6. UI FOR BRANN MODUL ---
st.markdown("<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🔥 Brannkonsept (RIBr)</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #9fb0c3; font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for generering av brannteknisk konsept iht. TEK17/VTEK17 med visuell tegningstolkning.</p>", unsafe_allow_html=True)

st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er synkronisert fra Project SSOT.")

with st.expander("1. Prosjekt & Lokasjon (SSOT)", expanded=False):
    c1, c2 = st.columns(2)
    st.text_input("Prosjektnavn", value=pd_state["p_name"], disabled=True)
    st.text_input("Adresse", value=f"{pd_state['adresse']}, {pd_state['kommune']}", disabled=True)

with st.expander("2. Branntekniske Forutsetninger (VTEK17)", expanded=True):
    st.info("Angi de tekniske forutsetningene for bygget. Disse legger sterke føringer for AI-ens krav til brannmotstand og rømningsveier.")
    
    # Auto-kalkuler default verdier
    default_rkl = "RKL 4 (Bolig)" if "bolig" in pd_state.get('b_type', '').lower() else "RKL 2 (Kontor)"
    etasjer = int(pd_state.get('etasjer', 1))
    default_bkl = "BKL 1" if etasjer <= 2 else "BKL 2" if etasjer <= 4 else "BKL 3"
    
    b1, b2 = st.columns(2)
    rkl = b1.selectbox("Risikoklasse (RKL)", ["RKL 1 (Lager/Garasje)", "RKL 2 (Kontor/Næring)", "RKL 3 (Skole/Bhg)", "RKL 4 (Bolig)", "RKL 5 (Forsamling)", "RKL 6 (Hotell/Sykehus)"], index=["RKL 1", "RKL 2", "RKL 3", "RKL 4", "RKL 5", "RKL 6"].index(default_rkl[:5]))
    bkl = b2.selectbox("Brannklasse (BKL)", ["BKL 1", "BKL 2", "BKL 3", "BKL 4"], index=["BKL 1", "BKL 2", "BKL 3", "BKL 4"].index(default_bkl))
    
    b3, b4 = st.columns(2)
    sprinkler = b3.radio("Automatisk slokkeanlegg (Sprinkler)", ["Ja, fullsprinklet (NS-EN 12845 / 16925)", "Delvis sprinklet", "Nei, u-sprinklet"])
    alarm = b4.radio("Brannalarmanlegg", ["Kategori 1 (Optisk/Akustisk)", "Kategori 2 (Heldekkende)", "Ingen / Lokal varsling"])

with st.expander("3. Visuelt Grunnlag (Branntegninger & Utomhusplan)", expanded=True):
    st.markdown("For at AI-en skal kunne vurdere rømning (rømningsveier, trapperom) og tilkomst for brannvesen (oppstillingsplasser), MÅ du laste opp spesifikke branntegninger og utomhusplaner.")
    
    files = st.file_uploader("Last opp Branntegninger (Plan, Snitt, Utomhus)", accept_multiple_files=True, type=["pdf", "png", "jpg"])

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🚀 GENERER BRANNKONSEPT (TEK17)", type="primary", use_container_width=True):
    if not files:
        st.warning("⚠️ For å få en god brannrapport, bør du laste opp minst én branntegning eller planløsning.")

    with st.spinner("📊 Tolker branntegninger for brannceller (røde linjer), rømning (grønne piler) og tilkomst (blå skravur)..."):
        images_for_brann = []
        if files:
            for f in files: 
                f.seek(0)
                if f.name.lower().endswith('pdf'):
                    if fitz is not None: 
                        doc = fitz.open(stream=f.read(), filetype="pdf")
                        for page_num in range(min(5, len(doc))): # Begrenser til 5 sider for memory
                            # BRUKER HØYERE MATRIX (2.0) FOR Å FÅ MED SMÅ TEKSTER (EI60 etc)
                            pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
                            img = Image.open(io.BytesIO(pix.tobytes("jpeg"))).convert("RGB")
                            img.thumbnail((2000, 2000))
                            images_for_brann.append(img)
                        doc.close() 
                else:
                    img = Image.open(f).convert("RGB")
                    img.thumbnail((2000, 2000))
                    images_for_brann.append(img)

        try:
            valid_models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
            valgt_modell = valid_models[0]
            for fav in ["models/gemini-1.5-pro", "models/gemini-1.5-flash"]:
                if fav in valid_models:
                    valgt_modell = fav
                    break
        except Exception:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        model = genai.GenerativeModel(valgt_modell)
        
        brann_data = {
            'rkl': rkl.split(" ")[0] + " " + rkl.split(" ")[1], 
            'bkl': bkl, 
            'sprinkler': sprinkler, 
            'alarm': alarm
        }

        prompt = f"""
        Du er Builtly RIBr AI, en ledende, autoritær og svært grundig senior branningeniør.
        Skriv et formelt og komplett "Brannteknisk konsept" (Brannstrategi) i henhold til TEK17 og VTEK17.

        PROSJEKT: {pd_state['p_name']} ({pd_state['b_type']}, {pd_state['bta']} m2, {pd_state['etasjer']} etasjer)
        LOKASJON: {pd_state['adresse']}, {pd_state['kommune']}. 
        REGELVERK: {pd_state['land']}

        KUNDENS PROSJEKTNARRATIV: "{pd_state['p_desc']}"

        LÅSTE BRANNTEKNISKE FORUTSETNINGER:
        - Risikoklasse: {rkl}
        - Brannklasse: {bkl}
        - Slokkeanlegg: {sprinkler}
        - Alarmanlegg: {alarm}

        KRITISKE INSTRUKSER FOR VISUELL ANALYSE (BRANNTEGNINGER):
        Jeg har lagt ved branntegninger. Du SKAL studere dem nøye og se etter standard fargekoder:
        - Røde linjer: Dette er brannceller (Ofte merket med EI30, EI60, EI90).
        - Grønne felt/piler: Dette er rømningsveier og trapperom (f.eks. Tr1).
        - Blått / Rosa felt utendørs: Dette er oppstillingsplasser og kjørbar atkomst for brannbil (110).
        
        DU MÅ BEVISE AT DU HAR SETT PÅ BILDENE. Skriv setninger som:
        - "Av vedlagte branntegning (plan X) fremgår det at rømning foregår via..."
        - "Tegningen viser branncellebegrensende vegger klassifisert som..."
        - "Utomhusplanen viser oppstillingsplass for stigebil plassert..."
        Hvis tegningene mangler viktig info (f.eks. avstander, rømning over terreng), må du påpeke dette som en risiko!

        KRAV TIL FORMATERING:
        - Start direkte på kapittel 1. Ingen hilsen eller metatekst!
        - Bruk punktlister med bindestrek (-) for oppramsing.
        - IKKE bruk tabeller (forbudt tegn: "|").
        - Bruk teknisk, presist språk (R60, EI60, A2-s1,d0, Tr1-trapperom).
        - Forklar konsekvensen av forutsetningene (F.eks: Siden bygget har {sprinkler}, kan krav til rømningstid økes/krav til materialer lempes).

        STRUKTUR (Bruk KUN disse eksakte overskriftene med #):
        # 1. SAMMENDRAG OG HOVEDFØRINGER
        # 2. PROSJEKTBESKRIVELSE OG FORUTSETNINGER
        # 3. BÆREEVNE OG BRANNSKILLER (TEK17 §11-4 - §11-8) (Konkretiser krav til konstruksjoner og brannceller her, bruk tegningene!)
        # 4. RØMNING OG LEDESYSTEM (TEK17 §11-11 - §11-14) (Beskriv rømningstraseer, Tr1/Tr2, avstander sett på tegning)
        # 5. SLOKKING OG BRANNVESENETS TILKOMST (TEK17 §11-16 - §11-17) (Vurder innsatstid, oppstillingsplass og brannsmitte mellom bygg)
        """

        try:
            res = model.generate_content([prompt] + images_for_brann)
            with st.spinner("Kompilerer RIBr-PDF og sender til QA-kø..."):
                pdf_data = create_full_report_pdf(pd_state, brann_data, res.text, images_for_brann)

                if "pending_reviews" not in st.session_state:
                    st.session_state.pending_reviews = {}
                if "review_counter" not in st.session_state:
                    st.session_state.review_counter = 1

                doc_id = f"PRJ-{datetime.now().strftime('%y')}-RIBR{st.session_state.review_counter:03d}"
                st.session_state.review_counter += 1

                st.session_state.pending_reviews[doc_id] = {
                    "title": pd_state["p_name"],
                    "module": "RIBr (Brannkonsept)",
                    "drafter": "Builtly AI",
                    "reviewer": "Senior Branningeniør",
                    "status": "Pending Senior Review",
                    "class": "badge-phase2",
                    "pdf_bytes": pdf_data,
                }

                st.session_state.generated_brann_pdf = pdf_data
                st.session_state.generated_brann_filename = f"Builtly_RIBr_{pd_state['p_name'].replace(' ', '_')}.pdf"
                st.rerun()

        except Exception as e:
            st.error(f"Kritisk feil under generering: {e}")

# --- NEDLASTING OG NAVIGASJON ---
if "generated_brann_pdf" in st.session_state:
    st.success("✅ Brannkonsept er ferdigstilt og sendt til QA-køen!")

    col_dl, col_qa = st.columns(2)
    with col_dl:
        st.download_button("📄 Last ned Brannkonsept (PDF)", st.session_state.generated_brann_pdf, st.session_state.generated_brann_filename, type="primary", use_container_width=True)
    with col_qa:
        if find_page("Review"):
            if st.button("🔍 Gå til QA for å godkjenne", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))
