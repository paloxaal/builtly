import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
from datetime import datetime
import tempfile
import re

# --- 1. GRUNNINNSTILLINGER & DARK MODE CSS ---
st.set_page_config(page_title="GEO / ENV | Builtly", layout="wide")

if os.path.exists("logo.png"):
    st.logo("logo.png", size="large")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    .stApp { background-color: #050505 !important; font-family: 'Inter', sans-serif; color: #e4e4e7; }
    header {visibility: hidden;}
    [data-testid="stSidebar"] { background-color: #0a0a0b !important; border-right: 1px solid #1f1f22 !important; }
    .block-container { padding-top: 3rem !important; max-width: 1200px !important; }
    
    .stTextInput>div>div>input, .stSelectbox>div>div>select, .stNumberInput>div>div>input {
        background-color: #18181b !important;
        color: #fafafa !important;
        border: 1px solid #27272a !important;
        border-radius: 6px;
    }
    .stTextInput>div>div>input:focus { border-color: #38bdf8 !important; box-shadow: 0 0 0 1px #38bdf8 !important; }
    label { color: #a1a1aa !important; font-weight: 500 !important; }
    
    .streamlit-expanderHeader { background-color: #09090b !important; color: #fafafa !important; border-bottom: 1px solid #27272a; }
    .streamlit-expanderContent { border: 1px solid #27272a; border-top: none; background-color: #09090b; }
    
    .mod-title { font-size: 2.2rem; font-weight: 700; color: #ffffff; margin-bottom: 0.5rem; letter-spacing: -0.02em; }
    .mod-sub { color: #a1a1aa; font-size: 1rem; margin-bottom: 2rem; border-bottom: 1px solid #27272a; padding-bottom: 1.5rem; }
    
    /* File uploader styling */
    [data-testid="stFileUploadDropzone"] { background-color: #09090b !important; border: 1px dashed #3f3f46 !important; }
</style>
""", unsafe_allow_html=True)

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

# --- 2. DYNAMISK PDF MOTOR ---
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

# --- 3. UI & LOGIKK ---
st.markdown("<div class='mod-title'>GEO / ENV — Ground Conditions</div>", unsafe_allow_html=True)
st.markdown("<div class='mod-sub'>Analyze lab files and excavation plans. Outputs soil classification and disposal action plans.</div>", unsafe_allow_html=True)

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
    st.markdown("Upload environmental laboratory results (Excel/CSV) and optional site plans (PDF).")
    lab_files = st.file_uploader("Upload Lab Data (e.g. ALS, Eurofins)", accept_multiple_files=True, type=['xlsx', 'xls', 'csv'])
    plan_files = st.file_uploader("Upload Excavation / Site Plans (PDF)", accept_multiple_files=True, type=['pdf'])

if st.button("Generate Environmental Action Plan", type="primary", use_container_width=True):
    
    extracted_data = ""
    
    with st.spinner("Extracting and parsing raw data..."):
        # Les lab-data (Excel/CSV)
        if lab_files:
            for f in lab_files:
                try:
                    if f.name.endswith('.csv'): df = pd.read_csv(f)
                    else: df = pd.read_excel(f)
                    extracted_data += f"\n--- LAB DATA: {f.name} ---\n{df.head(20).to_string()}\n"
                except Exception as e:
                    st.warning(f"Could not parse {f.name}: {e}")
        
        # Les planer (PDF)
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
        st.warning("Please upload at least one lab file or PDF to generate the report based on actual data.")
        extracted_data = "No file data provided. Generate a generic template based on best practices."
                
    with st.spinner(f"Classifying soil and generating compliance report for {region}..."):
        model = genai.GenerativeModel('gemini-1.5-pro' if 'gemini-1.5-pro' in [m.name for m in genai.list_models()] else 'gemini-1.5-flash')

        # DYNAMISK SPRÅK OG REGELVERK FOR MILJØ
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
