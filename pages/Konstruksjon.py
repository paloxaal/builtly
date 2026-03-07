import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
from datetime import datetime
import tempfile
import re
import requests
import urllib.parse
import io
from PIL import Image, ImageDraw, ImageFont
import numpy as np

# --- 1. GRUNNINNSTILLINGER & DARK MODE CSS ---
st.set_page_config(page_title="RIB Structural | Builtly", layout="wide")

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

# --- 2. KARTVERKET API (Fallback for internasjonale prosjekter) ---
def fetch_kartverket_data(adresse, kommune, gnr, bnr):
    adr = adresse.replace(',', '').strip() if adresse else ""
    kom = kommune.replace(',', '').strip() if kommune else ""
    g = gnr.strip() if gnr else ""
    b = bnr.strip() if bnr else ""

    def make_request(params):
        params['utkoordsys'] = '25833'
        params['treffPerSide'] = 1
        try:
            resp = requests.get("https://ws.geonorge.no/adresser/v1/sok", params=params, timeout=5)
            if resp.status_code == 200 and resp.json().get('adresser'):
                hit = resp.json()['adresser'][0]
                return hit.get('adressetekst', ''), hit.get('kommunenavn', ''), hit.get('representasjonspunkt', {}).get('nord'), hit.get('representasjonspunkt', {}).get('øst')
        except: pass
        return None, None, None, None

    if g and b and kom:
        a, k, n, o = make_request({'gardsnummer': g, 'bruksnummer': b, 'kommunenavn': kom})
        if n and o: return f"✅ Location verified: {k}. (N {n}, Ø {o})", n, o
    if adr and kom:
        a, k, n, o = make_request({'sok': adr, 'kommunenavn': kom, 'fuzzy': 'true'})
        if n and o: return f"✅ Location verified: {a}, {k}. (N {n}, Ø {o})", n, o

    return "Location mapping pending.", None, None

def fetch_kartverket_flyfoto(nord, ost):
    if not nord or not ost: return None, "Mangler koordinater"
    min_x, max_x = float(ost) - 150, float(ost) + 150
    min_y, max_y = float(nord) - 150, float(nord) + 150
    url = f"https://cache.kartverket.no/topo/v1/wms?service=WMS&request=GetMap&version=1.1.1&layers=topo&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if r.status_code == 200: return Image.open(io.BytesIO(r.content)).convert('RGB'), "Topographic Map"
    except: pass
    return None, "Map API Timeout"

# --- 3. BEREGNINGSMOTOR ---
def generate_structural_grid(img, material):
    w, h = img.size
    draw_img = img.convert("RGBA")
    overlay = Image.new("RGBA", draw_img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    gray_array = np.array(img.convert("L"))
    margin_x, margin_y = int(w * 0.1), int(h * 0.1)
    dark_y, dark_x = np.where(gray_array[margin_y:h-margin_y, margin_x:w-margin_x] < 230)
    
    if len(dark_x) > 100:
        min_x = np.min(dark_x) + margin_x
        max_x = np.max(dark_x) + margin_x
        min_y = np.min(dark_y) + margin_y
        max_y = np.max(dark_y) + margin_y
        spenn_deler = 4 if "Timber" in material or "Massivtre" in material else 3
        step_x = (max_x - min_x) // spenn_deler
        step_y = (max_y - min_y) // spenn_deler
        
        for y in range(min_y, max_y + 1, step_y):
            draw.line([(min_x, y), (max_x, y)], fill=(56, 189, 248, 180), width=int(w/150))
            
        r = int(w/120)
        for x in range(min_x, max_x + 1, step_x):
            for y in range(min_y, max_y + 1, step_y):
                draw.ellipse([x-r, y-r, x+r, y+r], fill=(239, 68, 68, 255), outline=(153,27,27,255), width=2)

    return Image.alpha_composite(draw_img, overlay).convert("RGB")

# --- 4. DYNAMISK PDF MOTOR ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROJECT: {self.p_name} | Doc: STRUC-001"), 0, 1, 'R')
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

def create_full_report_pdf(name, client, content, maps, aerial_photo):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
    pdf.add_page()
    pdf.set_y(80)
    pdf.set_font('Helvetica', 'B', 24)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("CONCEPTUAL STRUCTURAL DESIGN"), 0, 1, 'L')
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

    if maps:
        pdf.add_page()
        pdf.set_font('Helvetica', 'B', 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 20, "APPENDIX: CONCEPTUAL GRID LAYOUT", 0, 1)
        for i, m in enumerate(maps):
            if i > 0: pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                m.save(tmp.name)
                img_h = 160 * (m.height / m.width)
                pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                pdf.set_y(pdf.get_y() + img_h + 5)
                
    return bytes(pdf.output(dest='S'))

# --- 5. UI & LOGIKK ---
st.markdown("<div class='mod-title'>STRUC — Structural Concept</div>", unsafe_allow_html=True)
st.markdown("<div class='mod-sub'>Generate conceptual structural reports adapted to local building codes and languages.</div>", unsafe_allow_html=True)

# NYE REGIONER LAGT TIL HER
region = st.selectbox("🌍 Compliance Region & Output Language", 
                      [
                       "Norway (TEK17 / Eurocode - Norsk)", 
                       "Sweden (BBR / EKS - Svenska)", 
                       "Denmark (BR18 / Eurocode - Dansk)",
                       "Finland (RakMK / Eurocode - Suomi)",
                       "United Kingdom (BS EN - English)", 
                       "United States (IBC / ASCE 7 - English)"
                      ])

with st.expander("1. Project Details & Location", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Project Name", "Saga Park")
    c_name = c2.text_input("Client", "Saga Park AS")
    c3, c4 = st.columns(2)
    adresse = c3.text_input("Address", "Industriveien 1")
    kommune = c4.text_input("City/Municipality", "Trondheim")

with st.expander("2. Building Parameters", expanded=True):
    col1, col2 = st.columns(2)
    etasjer = col1.number_input("Number of floors", value=4)
    materiale = col2.selectbox("Preferred Main Structure:", ["Mass Timber (CLT / Glulam)", "Cast-in-place Concrete", "Precast Concrete", "Steel Frame", "Hybrid"])

files = st.file_uploader("Upload Architectural Plans (PDF/Image) to generate grid", accept_multiple_files=True)

if st.button("Generate Structural Report", type="primary", use_container_width=True):
    
    kartverket_info = ""
    aerial_photo = None
    
    # Norsk API kun for norske adresser
    if "Norway" in region:
        with st.spinner("Fetching Norwegian climate data (Kartverket)..."):
            kartverket_info, nord, ost = fetch_kartverket_data(adresse, kommune, "", "")
            if nord and ost: aerial_photo, _ = fetch_kartverket_flyfoto(nord, ost)
    
    processed_maps = []
    if files:
        with st.spinner("Analyzing plans and generating structural grid..."):
            try:
                for f in [f for f in files if f.name.lower().endswith(('.pdf', '.png', '.jpg'))][:1]: 
                    if f.name.lower().endswith('pdf') and fitz:
                        doc = fitz.open(stream=f.read(), filetype="pdf")
                        pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                    else: img = Image.open(f)
                    processed_maps.append(generate_structural_grid(img, materiale))
            except: pass
                
    with st.spinner(f"Generating Compliance-grade report for {region}..."):
        model = genai.GenerativeModel('gemini-1.5-pro' if 'gemini-1.5-pro' in [m.name for m in genai.list_models()] else 'gemini-1.5-flash')

        # DYNAMISK SPRÅK OG REGELVERK FOR HELE NORDEN
        if "Norway" in region:
            lang_instruction = "Skriv på profesjonell, teknisk Norsk."
            code_instruction = "Bruk Norske standarder: TEK17, NS-EN 1990, 1991, 1992, 1993, 1995 med nasjonale tillegg."
        elif "Sweden" in region:
            lang_instruction = "Skriv på professionell, teknisk Svenska."
            code_instruction = "Använd Svenska standarder: Boverkets byggregler (BBR) och EKS (Eurokoder)."
        elif "Denmark" in region:
            lang_instruction = "Skriv på professionelt, teknisk Dansk."
            code_instruction = "Brug Danske standarder: Bygningsreglementet (BR18) og Eurocodes (DS/EN) med nationale tilføjelser."
        elif "Finland" in region:
            lang_instruction = "Kirjoita ammattimaisella ja teknisellä suomen kielellä."
            code_instruction = "Käytä Suomen rakentamismääräyskokoelmaa (RakMK) ja Eurokoodeja (SFS-EN) kansallisine liitteineen."
        elif "United Kingdom" in region:
            lang_instruction = "Write in professional, technical British English."
            code_instruction = "Use UK Building Regulations and BS EN (Eurocodes with UK National Annexes)."
        else:
            lang_instruction = "Write in professional, technical US English."
            code_instruction = "Use International Building Code (IBC) and ASCE 7 for loads."

        prompt = f"""
        You are Builtly AI, a Senior Structural Engineer.
        Write a "Conceptual Structural Design Report" for a new building project.
        
        PROJECT DATA:
        Name: {p_name}
        Location: {adresse}, {kommune}.
        Floors: {etasjer}. Main System: {materiale}.
        
        CRITICAL INSTRUCTIONS:
        1. {lang_instruction}
        2. {code_instruction}
        3. Discuss {materiale} as the primary system, including sustainability/carbon footprint.
        4. Evaluate local environmental loads (snow, wind, seismic) typical for the city of {kommune}.
        
        STRUCTURE (Translate these headings to the chosen language):
        # 1. EXECUTIVE SUMMARY
        # 2. BUILDING CODES AND STANDARDS
        # 3. ENVIRONMENTAL LOADS (WIND/SNOW/SEISMIC)
        # 4. STRUCTURAL CONCEPT & MATERIALS
        # 5. SUSTAINABILITY & CARBON FOOTPRINT
        """
        
        try:
            res = model.generate_content(prompt)
            pdf_data = create_full_report_pdf(p_name, c_name, res.text, processed_maps, aerial_photo)
            st.success(f"✅ Report generated successfully according to {region.split('(')[1].replace(')','')}!")
            st.download_button("📄 Download Structural Package", pdf_data, f"Builtly_STRUC_{p_name}.pdf")
        except Exception as e: 
            st.error(f"Generation failed: {e}")
