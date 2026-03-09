import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
import base64
import json
from datetime import datetime
import tempfile
import re
import io
from PIL import Image
import numpy as np
from pathlib import Path

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="SHA-Plan | Builtly", layout="wide", initial_sidebar_state="collapsed")

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
    if not text: return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "-"}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{3,}', ' ', text)
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    return clean_pdf_text(text)

# --- 2. PREMIUM CSS ---
st.markdown("""
<style>
    :root { --bg: #06111a; --panel: rgba(10, 22, 35, 0.78); --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df; --accent: #38bdf8; --radius-lg: 16px; --radius-xl: 24px; }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }
    
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }
    
    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important;}

    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, div[data-baseweb="select"] * { background-color: transparent !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; border: none !important; box-shadow: none !important; }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus { border: none !important; }
    div[data-baseweb="base-input"]:focus-within, div[data-baseweb="select"] > div:focus-within, .stTextArea > div > div > div:focus-within { border-color: var(--accent) !important; box-shadow: 0 0 0 1px rgba(56, 194, 201, 0.5) !important; }
    ul[data-baseweb="menu"] { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; }
    ul[data-baseweb="menu"] li { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
    ul[data-baseweb="menu"] li:hover { background-color: rgba(56, 194, 201, 0.1) !important; }
    div[data-testid="InputInstructions"], div[data-testid="InputInstructions"] > span { color: #9fb0c3 !important; -webkit-text-fill-color: #9fb0c3 !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label, .stMultiSelect label { color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important; }
    
    div[data-testid="stExpander"] details, div[data-testid="stExpander"] details summary, div[data-testid="stExpander"] { background-color: #0c1520 !important; color: #f5f7fb !important; border-radius: 12px !important; }
    div[data-testid="stExpander"] details summary:hover { background-color: rgba(255,255,255,0.03) !important; }
    div[data-testid="stExpander"] details summary p { color: #f5f7fb !important; font-weight: 650 !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; margin-bottom: 1rem !important; }
    div[data-testid="stExpanderDetails"] { background: transparent !important; color: #f5f7fb !important; }
    div[data-testid="stExpanderDetails"] > div > div > div { background-color: transparent !important; }
    
    [data-testid="stFileUploaderDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important; border-radius: 12px !important; padding: 2rem !important; }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #38c2c9 !important; background-color: rgba(56, 194, 201, 0.05) !important; }
    [data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
    [data-testid="stFileUploaderFileData"] { background-color: rgba(255,255,255,0.02) !important; color: #f5f7fb !important; border-radius: 8px !important;}
    [data-testid="stAlert"] { background-color: rgba(56, 194, 201, 0.05) !important; border: 1px solid rgba(56, 194, 201, 0.2) !important; border-radius: 12px !important; }
    [data-testid="stAlert"] * { color: #f5f7fb !important; }
</style>
""", unsafe_allow_html=True)

# --- 3. SESSION STATE & HARDDISK GJENOPPRETTING ---
DB_DIR = Path("qa_database")
SSOT_FILE = DB_DIR / "ssot.json"
IMG_DIR = DB_DIR / "project_images"

if "project_data" not in st.session_state:
    st.session_state.project_data = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": "", "b_type": "Næring", "etasjer": 1, "bta": 0, "land": "Norge"}

if st.session_state.project_data.get("p_name") == "":
    if SSOT_FILE.exists():
        with open(SSOT_FILE, "r", encoding="utf-8") as f:
            st.session_state.project_data = json.load(f)

if st.session_state.project_data.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(f"<div style='margin-bottom:2rem;'>{logo_html}</div>")
    st.warning("⚠️ **Handling kreves:** Du må sette opp prosjektdataen før du kan bruke denne modulen.")
    st.info("SHA-agenten trenger kontekst om prosjektet for å generere prosjektspesifikke risikomomenter.")
    if find_page("Project"):
        if st.button("⚙️ Gå til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()

# --- 4. HEADER ---
top_l, top_r = st.columns([4, 1])
with top_l:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(logo_html)
with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("← Tilbake til SSOT", use_container_width=True, type="secondary"):
        st.switch_page(find_page("Project"))

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
pd_state = st.session_state.project_data

# --- 5. DYNAMISK PDF MOTOR FOR SHA (CORPORATE EDITION - STRAMME MARGER) ---


def canonical_label(text):
    text = clean_pdf_text(text or "")
    text = text.strip().strip(':').strip()
    text = re.sub(r'\s+', ' ', text)
    return text.upper()


def split_short_label(text, known_labels=None):
    work = (text or "").strip()
    if work.startswith('- ') or work.startswith('* '):
        work = work[2:].strip()
    if ':' not in work:
        return None
    label, value = work.split(':', 1)
    label = label.strip()
    value = value.strip()
    if not label or not value:
        return None
    if len(label) > 42 or len(label.split()) > 5:
        return None
    canon = canonical_label(label)
    if known_labels and canon in known_labels:
        return label.rstrip(':'), value
    if re.match(r'^[A-Za-z0-9ÆØÅæøå /()\-]+$', label):
        return label.rstrip(':'), value
    return None


class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: SHA-001"), 0, 1, 'R')
            self.set_draw_color(200, 200, 200)
            self.line(25, 25, 185, 25)
            self.set_y(30)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f'UTKAST - KREVER FAGLIG KONTROLL | Side {self.page_no()}'), 0, 0, 'C')

    def check_space(self, height):
        if self.get_y() + height > 270:
            self.add_page()
            self.set_margins(25, 25, 25)
            self.set_x(25)

    def draw_rule(self, y=None, x1=25, x2=185, color=(224, 228, 233), width=0.2):
        if y is None:
            y = self.get_y()
        self.set_draw_color(*color)
        self.set_line_width(width)
        self.line(x1, y, x2, y)
        self.set_line_width(0.2)

    def draw_section_heading(self, text, level=1):
        text = clean_pdf_text(text.replace('#', '').strip())
        if level == 1:
            self.check_space(18)
            self.ln(8)
            self.set_x(25)
            self.set_font('Helvetica', 'B', 14)
            self.set_text_color(26, 43, 72)
            self.multi_cell(0, 7, text)
            y = self.get_y() + 0.8
            self.draw_rule(y=y, x1=25, x2=58, color=(76, 100, 133), width=0.6)
            self.ln(4)
        else:
            self.check_space(14)
            self.ln(5)
            self.set_x(25)
            self.set_font('Helvetica', 'B', 11.5)
            self.set_text_color(54, 69, 92)
            self.multi_cell(0, 6, text.upper())
            self.ln(1.5)

    def draw_topic_heading(self, text):
        text = clean_pdf_text(text.strip())
        self.check_space(14)
        if self.get_y() > 55:
            self.draw_rule(y=self.get_y() + 1.5, x1=25, x2=185, color=(232, 235, 239), width=0.2)
            self.ln(6)
        self.set_x(25)
        self.set_font('Helvetica', 'B', 12)
        self.set_text_color(50, 65, 85)
        self.multi_cell(0, 6, text)
        self.ln(1.5)

    def draw_bullet(self, x, y, size=1.35, color=(56, 70, 92)):
        self.set_fill_color(*color)
        self.rect(x, y, size, size, 'F')

    def draw_meta_row(self, label, value, with_bullet=False):
        label = clean_pdf_text(label.rstrip(':').strip() + ':')
        value = clean_pdf_text(value.strip())
        self.check_space(10)
        base_x = 25
        if with_bullet:
            self.draw_bullet(base_x + 0.6, self.get_y() + 2.2, size=1.2, color=(105, 123, 148))
            base_x += 5
        label_w = min(max(self.get_string_width(label) + 6, 30), 62)
        self.set_x(base_x)
        self.set_font('Helvetica', 'B', 9.3)
        self.set_text_color(55, 68, 90)
        self.cell(label_w, 5.5, label, 0, 0)
        self.set_text_color(40, 40, 40)
        self.set_font('Helvetica', '', 10)
        self.multi_cell(185 - base_x - label_w, 5.5, value)
        self.ln(0.8)

    def draw_field_block(self, label, value):
        label = canonical_label(label)
        value = clean_pdf_text(value.strip())
        if not value:
            return
        self.check_space(14)
        chip_w = min(max(self.get_string_width(label) + 6, 22), 60)
        self.set_x(25)
        self.set_fill_color(236, 241, 246)
        self.set_text_color(104, 122, 145)
        self.set_font('Helvetica', 'B', 7.6)
        self.cell(chip_w, 5.2, label, 0, 1, 'C', True)
        self.ln(1.0)
        self.set_x(31)
        self.set_font('Helvetica', '', 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(154, 5.25, value)
        self.ln(2.0)

    def draw_bullet_item(self, text):
        text = clean_pdf_text(text.strip())
        if not text:
            return
        self.check_space(8)
        bullet_x = 29
        y = self.get_y() + 2.2
        self.draw_bullet(bullet_x, y, size=1.2, color=(92, 108, 130))
        self.set_x(34)
        self.set_font('Helvetica', '', 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(151, 5.25, text)
        self.ln(0.8)

    def draw_paragraph(self, text):
        text = clean_pdf_text(text.strip())
        if not text:
            return
        self.check_space(8)
        self.set_x(25)
        self.set_font('Helvetica', '', 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(160, 5.35, text)
        self.ln(1.2)

    def draw_cover_meta_items(self, items):
        for label, value in items:
            self.draw_meta_row(label.rstrip(':'), value, with_bullet=False)


def render_report_body(pdf, content, field_labels=None, metadata_sections=None):
    field_labels = {canonical_label(x) for x in (field_labels or [])}
    metadata_sections = {canonical_label(x) for x in (metadata_sections or [])}
    current_section = ""
    lines = [ironclad_text_formatter(x) for x in content.split('\n')]
    i = 0

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line:
            pdf.ln(2)
            i += 1
            continue

        safe = line.replace('**', '').replace('_', '').strip()
        canon = canonical_label(safe)

        if safe.startswith('# ') or re.match(r'^\d+\.\s', safe):
            current_section = canonical_label(re.sub(r'^#+\s*', '', safe))
            pdf.draw_section_heading(re.sub(r'^#+\s*', '', safe), level=1)
            i += 1
            continue

        if safe.startswith('## ') or safe.startswith('### '):
            pdf.draw_section_heading(re.sub(r'^#+\s*', '', safe), level=2)
            i += 1
            continue

        if canon in field_labels and len(canon.split()) <= 4:
            value_parts = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt:
                    if value_parts:
                        break
                    j += 1
                    continue
                nxt_safe = nxt.replace('**', '').replace('_', '').strip()
                nxt_canon = canonical_label(nxt_safe)
                if nxt_safe.startswith('#') or re.match(r'^\d+\.\s', nxt_safe):
                    break
                if nxt_safe.startswith('## ') or nxt_safe.startswith('### '):
                    break
                if nxt_canon in field_labels:
                    break
                if nxt_safe.startswith('- ') or nxt_safe.startswith('* '):
                    if value_parts:
                        break
                value_parts.append(nxt_safe)
                j += 1
            if value_parts:
                pdf.draw_field_block(canon, ' '.join(value_parts))
                i = j
                continue

        short = split_short_label(safe, known_labels=field_labels)
        if short:
            label, value = short
            if current_section in metadata_sections:
                pdf.draw_meta_row(label, value, with_bullet=False)
            elif canonical_label(label) in field_labels:
                pdf.draw_field_block(label, value)
            else:
                pdf.draw_meta_row(label, value, with_bullet=safe.startswith('- ') or safe.startswith('* '))
            i += 1
            continue

        if (safe.upper() == safe and len(safe) <= 85 and len(safe.split()) <= 10 and not safe.endswith(':')
                and canon not in field_labels and not re.match(r'^[A-Z0-9]+$', safe)):
            pdf.draw_topic_heading(safe)
            i += 1
            continue

        if safe.startswith('- ') or safe.startswith('* '):
            pdf.draw_bullet_item(safe[2:])
            i += 1
            continue

        pdf.draw_paragraph(safe)
        i += 1


def create_full_report_pdf(name, client, content, maps):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)

    pdf.add_page()
    if os.path.exists("logo.png"):
        pdf.image("logo.png", x=25, y=20, w=50)

    
    pdf.set_y(95)
    pdf.set_font('Helvetica', 'B', 24)
    pdf.set_text_color(26, 43, 72)
    pdf.multi_cell(0, 12, clean_pdf_text('SHA-PLAN (UTKAST)'), 0, 'L')
    pdf.ln(2)
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(0, 10, clean_pdf_text(f"PROSJEKT: {pdf.p_name}"), 0, 'L')
    pdf.ln(25)


    pdf.draw_cover_meta_items([("BYGGHERRE:", client), ("DATO:", datetime.now().strftime("%d. %m. %Y")), ("UTARBEIDET AV:", "Builtly SHA AI Engine"), ("REGELVERK:", "Byggherreforskriften (BHF)")])

    pdf.add_page()
    pdf.set_x(25)
    pdf.set_font('Helvetica', 'B', 16)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1)
    pdf.ln(5)

    toc = ['1. DOKUMENTINFORMASJON', '2. FORMÅL OG OMFANG', '3. PROSJEKTBESKRIVELSE', '4. ROLLER OG ANSVAR', '5. ORGANISASJONSKART', '6. FREMDRIFT OG KONSEKVENS', '7. PROSJEKTSPESIFIKKE RISIKOFORHOLD OG TILTAK', '8. RUTINER FOR OPPFØLGING', '9. FORUTSETNINGER OG AVKLARINGER', '10. HANDLINGSLISTE / MANGLER']
    for t in toc:
        pdf.set_x(25)
        pdf.set_font('Helvetica', '', 11)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 10, clean_pdf_text(t), 0, 1)
        pdf.set_draw_color(220, 220, 220)
        pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    pdf.add_page()
    render_report_body(pdf, content, field_labels=['Aktivitet', 'Årsak', 'Konsekvens', 'Fase', 'Tiltak', 'Forebyggende tiltak', 'Ansvarlig', 'Rolle', 'Status', 'Koordinering', 'Frist'], metadata_sections=['1. DOKUMENTINFORMASJON'])

    
    if maps and len(maps) > 0:
        pdf.add_page()
        pdf.set_x(25)
        pdf.set_font('Helvetica', 'B', 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 20, 'VEDLEGG: VISUELT GRUNNLAG', 0, 1)
        for i, m in enumerate(maps):
            if i > 0:
                pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
                m.convert('RGB').save(tmp.name, format='JPEG', quality=90)
                img_w = 160
                img_h = 160 * (m.height / m.width)
                if img_h > 240:
                    img_h = 240
                    img_w = 240 * (m.width / m.height)
                x_pos = 105 - (img_w / 2)
                y_pos = pdf.get_y()
                pdf.image(tmp.name, x=x_pos, y=y_pos, w=img_w)
                pdf.set_y(y_pos + img_h + 6)
                pdf.set_x(25)
                pdf.set_font('Helvetica', 'I', 10)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: Dokument visuelt vurdert som SHA-grunnlag av AI-agenten."), 0, 1, 'C')


    return bytes(pdf.output(dest='S'))


# --- 6. STREAMLIT UI ---
st.markdown(f"<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🦺 SHA-Plan (Sikkerhet, Helse og Arbeidsmiljø)</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: var(--muted); font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for generering av prosjektspesifikke SHA-plan ihht. Byggherreforskriften.</p>", unsafe_allow_html=True)

st.success(f"✅ Prosjektdata for **{pd_state.get('p_name')}** er automatisk synkronisert (SSOT).")

with st.expander("1. Prosjekt & Lokasjon (Auto-synced)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state.get("p_name"), disabled=True)
    c_name = c2.text_input("Byggherre", value=pd_state.get("c_name"), disabled=True)
    adresse = st.text_input("Adresse", value=f"{pd_state.get('adresse')}, {pd_state.get('kommune')}", disabled=True)
    bta = st.number_input("Bruttoareal (BTA m2)", value=int(pd_state.get("bta", 0)), disabled=True)

with st.expander("2. Gjennomføringsmodell & Risikoforhold", expanded=True):
    st.info("Angi overordnede rammer for prosjektet slik at AI-en kan vurdere grensesnitt og spesifikke tiltak.")
    c3, c4 = st.columns(2)
    entrepriseform = c3.selectbox("Forventet Entrepriseform", ["Totalentreprise (Én hovedbedrift)", "Delt entreprise (Flere likestilte entreprenører)", "Generalentreprise", "Uavklart"])
    fremdrift = c4.selectbox("Kritisk fremdrift / Fase", ["Rivning og sanering", "Grunnarbeid og fundamentering", "Råbyggsfasen", "Tett bygg og innredning", "Hele livsløpet (Flerfase)"], index=4)
    
    st.markdown("##### Spesifikke Risikoforhold (Velg alle som gjelder)")
    risiko_liste = st.multiselect(
        "Kjente faremomenter på byggeplassen:",
        ["Arbeid i høyden (over 2m)", "Tunge løft / Kraning", "Dype grøfter (> 2m) / Sjakter", "Nærhet til trafikkert vei", "Forurensede masser", "Asbest / PCB / Miljøgifter i eksist. bygg", "Sprengningsarbeid", "Arbeid nær høyspent", "Støy og vibrasjoner (Naboer)", "Trang riggplass / Logistikkutfordringer"],
        default=["Arbeid i høyden (over 2m)"]
    )

with st.expander("3. Visuelt Grunnlag (Riggplan / Fremdrift)", expanded=True):
    st.info("Last opp riggplan, situasjonskart eller fremdriftsplan. Agenten vil vurdere logistikk, kranplassering og konfliktområder.")
    
    saved_images = []
    if IMG_DIR.exists():
        for p in sorted(IMG_DIR.glob("*.jpg")):
            saved_images.append(Image.open(p).convert("RGB"))
            
    if len(saved_images) > 0:
        st.success(f"📎 Fant {len(saved_images)} felles arkitekttegninger fra Project Setup. Disse inkluderes automatisk for å vurdere bygningsvolum!")
    else:
        st.warning("Ingen felles tegninger funnet. Du bør laste opp situasjonsplan under.")
        
    files = st.file_uploader("Last opp spesifikke SHA-dokumenter (Riggplan, Snitt, etc)", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

st.markdown("<br>", unsafe_allow_html=True)

if st.button("🚀 Kjør SHA-analyse og generer plan", type="primary", use_container_width=True):
    
    images_for_ai = saved_images.copy()
        
    if files:
        with st.spinner("📐 Leser ut supplerende filer..."):
            try:
                for f in files: 
                    f.seek(0)
                    if f.name.lower().endswith('pdf'):
                        if fitz is not None: 
                            doc = fitz.open(stream=f.read(), filetype="pdf")
                            for page_num in range(min(4, len(doc))): 
                                pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                                img = Image.open(io.BytesIO(pix.tobytes("jpeg"))).convert("RGB")
                                img.thumbnail((1200, 1200))
                                images_for_ai.append(img)
                            doc.close() 
                    else:
                        img = Image.open(f).convert("RGB")
                        img.thumbnail((1200, 1200))
                        images_for_ai.append(img)
            except Exception as e: 
                st.error(f"Feil under bildebehandling: {e}")
                
    st.info(f"Klar! Sender totalt {len(images_for_ai)} bilder/tegninger til SHA-agenten for vurdering.")
                
    with st.spinner(f"🤖 Vurderer risikoforhold og genererer SHA-plan for {pd_state.get('land', 'Norge')}..."):
        try:
            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        valgt_modell = valid_models[0]
        for fav in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if fav in valid_models: valgt_modell = fav; break
        
        model = genai.GenerativeModel(valgt_modell)

        risiko_str = ", ".join(risiko_liste) if risiko_liste else "Ingen spesifikke krysset av."

        prompt_text = f"""
        Du er en senior SHA-rådgiver for norske bygge- og anleggsprosjekter. Din oppgave er utelukkende å skrive innholdet i en formell SHA-plan.

        PROSJEKT: {p_name} ({pd_state.get('b_type', 'Ukjent type')}, Bygg {bta} m2, Tomt {pd_state.get('tomteareal')} m2, {pd_state.get('etasjer', 1)} etasjer).
        LOKASJON: {adresse}.
        ENTREPRISEFORM: {entrepriseform}.
        KRITISK FASE: {fremdrift}.
        
        KUNDENS PROSJEKTBESKRIVELSE: 
        "{pd_state.get('p_desc', '')}"
        
        IDENTIFISERTE RISIKOFORHOLD AV BRUKER:
        {risiko_str}
        
        EKSTREMT VIKTIGE REGLER FOR FORMATERING:
        1. START RESPONSEN DIREKTE med overskriften "# 1. DOKUMENTINFORMASJON". IKKE skriv noen form for introduksjon, hilsen, forbehold eller bekreftelse.
        2. IKKE bruk Markdown-tabeller (forbudt tegn: "|").
        3. For underoverskrifter (f.eks. spesifikke risikoforhold), bruk alltid ### foran (f.eks. "### Arbeid i høyden").
        4. For utdyping av risikoer og tiltak, MÅ du skrive NØYAKTIG disse nøkkelordene etterfulgt av kolon (IKKE bruk bindestrek foran ordene):
        
        Aktivitet: [Tekst]
        Årsak: [Tekst]
        Konsekvens: [Tekst]
        Tiltak: [Tekst]
        Ansvarlig: [Tekst]
        Status: [Tekst]
        
        MANDAT:
        Lag et førsteutkast til SHA-plan som er konkret, prosjektspesifikk og egnet for videre kvalitetssikring.
        - Bruk prosjektets faktiske data, valgte risikoforhold, og trekk inn funn fra de vedlagte tegningene.
        - Hvis tegningene viser trang tomt ({pd_state.get('tomteareal')} m2 vs {bta} m2 BTA), påpek logistikkutfordringer.
        
        STRUKTUR PÅ RAPPORTEN:
        # 1. DOKUMENTINFORMASJON (Bruk en enkel liste med bindestrek)
        # 2. FORMÅL OG OMFANG
        # 3. PROSJEKTBESKRIVELSE
        # 4. ROLLER OG ANSVAR (Hvem gjør hva ihht. Byggherreforskriften for entrepriseformen {entrepriseform}?)
        # 5. ORGANISASJONSKART (Lag et tekstlig hierarki, bruk bindestrek)
        # 6. FREMDRIFT OG FASEKRITISKE AKTIVITETER
        # 7. PROSJEKTSPESIFIKKE RISIKOFORHOLD OG TILTAK (Bruk ### for hver risiko, og bruk nøkkelordene over for corporate formatering!)
        # 8. RUTINER FOR OPPFØLGING OG OPPDATERING
        # 9. FORUTSETNINGER OG AVKLARINGER
        # 10. HANDLINGSLISTE / MANGLER
        """
        
        prompt_parts = [prompt_text] + images_for_ai

        try:
            res = model.generate_content(prompt_parts)
            
            with st.spinner("Kompilerer SHA-PDF med corporate design..."):
                pdf_data = create_full_report_pdf(p_name, pd_state.get('c_name', ''), res.text, images_for_ai)
                
                # --- SENDER TIL QA-KØ ---
                if "pending_reviews" not in st.session_state:
                    st.session_state.pending_reviews = {}
                if "review_counter" not in st.session_state:
                    st.session_state.review_counter = 1
                    
                doc_id = f"PRJ-{datetime.now().strftime('%y')}-SHA{st.session_state.review_counter:03d}"
                st.session_state.review_counter += 1
                
                st.session_state.pending_reviews[doc_id] = {
                    "title": pd_state.get('p_name', 'Nytt Prosjekt'),
                    "module": "SHA-Plan (Ledelse)",
                    "drafter": "Builtly AI",
                    "reviewer": "Senior SHA-rådgiver / KP",
                    "status": "Pending Coordinator Review",
                    "class": "badge-priority",
                    "pdf_bytes": pdf_data
                }

            st.session_state.generated_sha_pdf = pdf_data
            st.session_state.generated_sha_filename = f"Builtly_SHA_{p_name}.pdf"
            st.rerun() 
                
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")

# --- NEDLASTING OG NAVIGASJON ---
if "generated_sha_pdf" in st.session_state:
    st.success("✅ SHA-Plan er ferdigstilt og lagt i QA-køen for godkjenning av koordinator (KP/KU)!")
    
    col_dl, col_qa = st.columns(2)
    with col_dl:
        st.download_button("📄 Last ned SHA-Plan utkast", st.session_state.generated_sha_pdf, st.session_state.generated_sha_filename, type="primary", use_container_width=True)
    with col_qa:
        if find_page("Review"):
            if st.button("🔍 Gå til QA for å verifisere", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))
