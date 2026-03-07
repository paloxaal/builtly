import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
from datetime import datetime
import tempfile
import re

# --- 1. GRUNNINNSTILLINGER ---
st.set_page_config(page_title="GEO / ENV | Builtly", layout="wide", initial_sidebar_state="collapsed")

if os.path.exists("logo.png"):
    st.logo("logo.png", size="large")

# --- 2. PREMIUM DARK MODE CSS (Samme som forsiden) ---
st.markdown("""
<style>
    :root {
        --bg: #06111a;
        --panel: rgba(13, 27, 42, 0.6);
        --stroke: rgba(120, 145, 170, 0.2);
        --text: #f5f7fb;
        --muted: #9fb0c3;
        --accent-2: #78dce1;
    }

    html, body, [class*="css"] {
        font-family: Inter, ui-sans-serif, system-ui, sans-serif;
    }

    /* Lik bakgrunn som forsiden */
    .stApp {
        background: radial-gradient(1100px 500px at 15% -5%, rgba(56,194,201,0.12), transparent 50%),
                    radial-gradient(900px 500px at 100% 0%, rgba(64,170,255,0.08), transparent 45%),
                    linear-gradient(180deg, #071018 0%, #08131d 35%, #071018 100%) !important;
        color: var(--text);
    }
    
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    [data-testid="stSidebar"] { background: rgba(7, 16, 24, 0.96); border-right: 1px solid var(--stroke); }
    .block-container { max-width: 1000px !important; padding-top: 3rem !important; padding-bottom: 4rem !important; }

    /* Custom Header Design */
    .mod-title { font-size: 2.8rem; font-weight: 800; color: #ffffff; letter-spacing: -0.03em; margin-bottom: 0.5rem; }
    .mod-sub { color: var(--muted); font-size: 1.1rem; margin-bottom: 2.5rem; border-bottom: 1px solid var(--stroke); padding-bottom: 1.5rem; line-height: 1.6;}

    /* TVINGER INPUTS TIL DARK MODE */
    [data-testid="stTextInput"] input, 
    [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
        background-color: rgba(255,255,255,0.03) !important;
        border: 1px solid var(--stroke) !important;
        color: var(--text) !important;
        border-radius: 8px !important;
    }
    [data-testid="stTextInput"] input:focus, 
    [data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within {
        border-color: var(--accent-2) !important;
    }
    
    /* TVINGER EXPANDERS TIL DARK MODE */
    [data-testid="stExpander"] {
        background-color: var(--panel) !important;
        border: 1px solid var(--stroke) !important;
        border-radius: 12px !important;
        margin-bottom: 1rem;
    }
    [data-testid="stExpander"] summary {
        background-color: transparent !important;
        padding: 1rem !important;
    }
    [data-testid="stExpander"] summary p {
        font-size: 1.1rem !important;
        font-weight: 600 !important;
        color: var(--accent-2) !important;
    }
    [data-testid="stExpanderDetails"] {
        background-color: transparent !important;
        padding: 0 1rem 1rem 1rem !important;
    }

    /* TVINGER FILE UPLOADER TIL DARK MODE */
    [data-testid="stFileUploadDropzone"] {
        background-color: rgba(255,255,255,0.02) !important;
        border: 1px dashed rgba(56,194,201,0.4) !important;
        border-radius: 12px !important;
        padding: 2rem !important;
        transition: all 0.3s ease;
    }
    [data-testid="stFileUploadDropzone"]:hover {
        background-color: rgba(56,194,201,0.05) !important;
        border-color: var(--accent-2) !important;
    }
    [data-testid="stFileUploadDropzone"] div { color: var(--text) !important; }
    [data-testid="stFileUploadDropzone"] small { color: var(--muted) !important; }
    
    /* PRIMARY BUTTON (Generate) */
    [data-testid="baseButton-primary"] {
        background: linear-gradient(135deg, rgba(56,194,201,0.8), rgba(18,49,76,0.9)) !important;
        border: 1px solid rgba(56,194,201,0.5) !important;
        color: white !important;
        font-weight: 600 !important;
        border-radius: 12px !important;
        padding: 1.5rem !important;
        font-size: 1.1rem !important;
        transition: all 0.3s ease;
    }
    [data-testid="baseButton-primary"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 10px 20px rgba(56,194,201,0.2) !important;
    }
    
    /* Typografi for labels */
    label, .stMarkdown p { color: var(--muted) !important; font-size: 0.95rem; }

    /* Footer */
    .builtly-footer { text-align: center; color: #71717a; font-size: 0.9rem; margin-top: 4rem; padding-top: 2rem; border-top: 1px solid var(--stroke); }
    .builtly-footer strong { color: #a1a1aa; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# --- 3. GOOGLE AI SETUP ---
google_key = os.environ.get("GOOGLE_API_KEY")
if google_key: genai.configure(api_key=google_key)
else: st.error("Missing Google API Key.")

try: import fitz
except ImportError: fitz = None

def clean_pdf_text(text):
    if not text: return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*", "²": "2", "³": "3"}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{3,}', ' ', text)
    return clean_pdf_text(text)

# --- 4. DYNAMISK PDF MOTOR ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROJECT: {self.p_name} | Doc: GEO-001"), 0, 1, 'R')
            self.set_draw_color(200, 200, 200)
            self.line(25, 25, 185, 25)
            self.set_y(30)
    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f'DRAFT - REQUIRES PROFESSIONAL REVIEW | Page {self.page_no()}'), 0, 0, 'C')
    def check_space(self, height):
        if self.get_y() + height > 270: 
            self.add_page()
            self.set_margins(25, 25, 25)
            self.set_x(25)

def create_geo_report_pdf(name, client, content):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
    pdf.add_page()
    pdf.set_y(80)
    pdf.set_font('Helvetica', 'B', 24)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("ENVIRONMENTAL SOIL ACTION PLAN"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"PROJECT: {pdf.p_name}"), 0, 1, 'L')
    pdf.ln(30)
    
    metadata = [("CLIENT:", client), ("DATE:", datetime.now().strftime("%d. %b %Y")), ("PREPARED BY:", "Builtly AI Engine")]
    for l, v in metadata:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10)
        pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: 
            pdf.ln(4)
            continue
            
        if line.startswith('# ') or re.match(r'^\d\.\s[A-Z]', line):
            pdf.check_space(30)
            pdf.ln(8)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.ln(2)
        elif line.startswith('##'):
            pdf.check_space(20)
            pdf.ln(6)
            pdf.set_font('Helvetica', 'B', 12)
            pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
        else:
            pdf.set_font('Helvetica', '', 10)
            pdf.set_text_color(0, 0, 0)
            safe_text = ironclad_text_formatter(line)
            if safe_text.strip() == "": continue
            try:
                if safe_text.startswith('- ') or safe_text.startswith('* '):
                    pdf.set_x(30)
                    pdf.multi_cell(145, 5, safe_text)
                    pdf.set_x(25)
                else:
                    pdf.multi_cell(150, 5, safe_text)
            except Exception: pass
                
    return bytes(pdf.output(dest='S'))

# --- 5. UI & LOGIKK ---
st.markdown("<div class='mod-title'>GEO / ENV — Ground Conditions</div>", unsafe_allow_html=True)
st.markdown("<div class='mod-sub'>Agent specialized in analyzing laboratory test files and excavation plans. Calculates soil classification according to local regulations and generates detailed disposal action plans.</div>", unsafe_allow_html=True)

# THE MAGIC: Compliance & Language Selector
region = st.selectbox("🌍 Compliance Region & Output Language", 
                      [
                       "Norway (Forurensningsforskriften - Norsk)", 
                       "Sweden (Naturvårdsverket - Svenska)", 
                       "Denmark (Jordforureningsloven - Dansk)",
                       "Finland (Ympäristöministeriö - Suomi)",
                       "United Kingdom (EA / DEFRA - English)", 
                       "United States (EPA / RCRA - English)"
                      ])

with st.expander("1. Project Details", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Project Name", "Saga Park")
    c_name = c2.text_input("Client", "Saga Park AS")
    c3, c4 = st.columns(2)
    adresse = c3.text_input("Address", "Industriveien 1")
    kommune = c4.text_input("City/Municipality", "Trondheim")

with st.expander("2. Data Ingestion (Raw Files)", expanded=True):
    st.markdown("Upload environmental laboratory results (Excel/CSV) and optional site plans (PDF). The AI agent will parse the tables and highlight toxic hotspots automatically.")
    lab_files = st.file_uploader("Upload Lab Data (ALS, Eurofins etc.)", accept_multiple_files=True, type=['xlsx', 'xls', 'csv'])
    plan_files = st.file_uploader("Upload Excavation / Site Plans (PDF)", accept_multiple_files=True, type=['pdf'])

st.write("") # Luft før knappen

if st.button("Calculate & Generate Action Plan", type="primary", use_container_width=True):
    
    extracted_data = ""
    
    with st.spinner("Extracting and parsing raw data..."):
        if lab_files:
            for f in lab_files:
                try:
                    if f.name.endswith('.csv'): df = pd.read_csv(f)
                    else: df = pd.read_excel(f)
                    extracted_data += f"\n--- LAB DATA: {f.name} ---\n{df.head(20).to_string()}\n"
                except Exception as e:
                    st.warning(f"Could not parse {f.name}: {e}")
        
        if plan_files and fitz:
            for f in plan_files:
                try:
                    doc = fitz.open(stream=f.read(), filetype="pdf")
                    text = ""
                    for page in doc: text += page.get_text()
                    extracted_data += f"\n--- SITE PLAN EXTRACT: {f.name} ---\n{text[:1500]}\n"
                except Exception as e:
                    pass
                    
    if not extracted_data:
        st.warning("Please upload at least one lab file or PDF to generate the report based on actual data. Generating template...")
        extracted_data = "No file data provided. Generate a generic template based on best practices."
                
    with st.spinner(f"Classifying soil and generating compliance report for {region}..."):
        model = genai.GenerativeModel('gemini-1.5-pro' if 'gemini-1.5-pro' in [m.name for m in genai.list_models()] else 'gemini-1.5-flash')

        if "Norway" in region:
            lang_instruction = "Skriv på profesjonell, teknisk Norsk."
            code_instruction = "Bruk Forurensningsforskriften kapittel 2 og Miljødirektoratets veileder for klassifisering av forurenset grunn (Tilstandsklasse 1-5)."
        elif "Sweden" in region:
            lang_instruction = "Skriv på professionell, teknisk Svenska."
            code_instruction = "Använd Naturvårdsverkets riktvärden för förorenad mark (KM och MKM)."
        elif "Denmark" in region:
            lang_instruction = "Skriv på professionelt, teknisk Dansk."
            code_instruction = "Brug Jordforureningsloven og Miljøstyrelsens vejledninger (Kategori 1, 2 og Ren jord)."
        elif "Finland" in region:
            lang_instruction = "Kirjoita ammattimaisella ja teknisellä suomen kielellä."
            code_instruction = "Käytä Ympäristöministeriön (PIMA) ohjearvoja ja kynnysarvoja pilaantuneen maaperän arviointiin."
        elif "United Kingdom" in region:
            lang_instruction = "Write in professional, technical British English."
            code_instruction = "Use Environment Agency (EA) / DEFRA guidelines and BS 10175 for investigation of potentially contaminated sites."
        else:
            lang_instruction = "Write in professional, technical US English."
            code_instruction = "Use EPA guidelines and RCRA standards for soil contamination and hazardous waste classification."

        prompt = f"""
        You are Builtly AI, a Senior Environmental & Geotechnical Engineer.
        Write an "Environmental Soil Action Plan" (Tiltaksplan for forurenset grunn) based on the provided raw data.
        
        PROJECT DATA:
        Name: {p_name}
        Location: {adresse}, {kommune}.
        
        CRITICAL INSTRUCTIONS:
        1. {lang_instruction}
        2. {code_instruction}
        3. Analyze the provided lab data. If values exceed the regulatory limits, state the correct contamination class.
        4. Recommend correct handling, transport, and disposal methods for the excavated soil.
        
        RAW EXTRACTED DATA TO ANALYZE:
        {extracted_data}
        
        STRUCTURE (Translate these headings to the chosen language):
        # 1. EXECUTIVE SUMMARY
        # 2. REGULATORY FRAMEWORK
        # 3. SOIL CLASSIFICATION & LAB ANALYSIS
        # 4. ACTION PLAN (HANDLING & DISPOSAL)
        # 5. HEALTH, SAFETY & ENVIRONMENT (HSE)
        """
        
        try:
            res = model.generate_content(prompt)
            pdf_data = create_geo_report_pdf(p_name, c_name, res.text)
            st.success(f"✅ Environmental Action Plan generated successfully according to {region.split('(')[1].replace(')','')}!")
            st.download_button("📄 Download Action Plan Package", pdf_data, f"Builtly_GEO_{p_name}.pdf")
        except Exception as e: 
            st.error(f"Generation failed: {e}")

# --- FOOTER ---
st.markdown("""
<div class="builtly-footer">
    <strong>&copy; 2026 Builtly Engineering AS.</strong> All rights reserved.<br>
    <span style="font-size: 0.8rem; margin-top: 5px; display: inline-block;">Setting the global standard for compliance-grade engineering deliverables.</span>
</div>
""", unsafe_allow_html=True)
