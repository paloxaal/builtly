import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
from datetime import datetime
import tempfile
import re
import io
from PIL import Image, ImageDraw, ImageFont
import numpy as np

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Brannkonsept (RIBr) | Builtly", layout="wide")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()

try:
    import fitz  # PyMuPDF for å lese PDF-tegninger
except ImportError:
    fitz = None

def clean_pdf_text(text):
    if not text: return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*"}
    for old, new in rep.items(): 
        text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{3,}', ' ', text)
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    return clean_pdf_text(text)

# --- 2. HENT DATA FRA SSOT (Project Setup) ---
if "project_data" in st.session_state:
    pd_state = st.session_state.project_data
else:
    pd_state = {
        "p_name": "", "c_name": "", "p_desc": "",
        "adresse": "", "kommune": "", "gnr": "", "bnr": "",
        "b_type": "Næring / Kontor", "etasjer": 4, "bta": 2500
    }

# --- 3. KONSEPTUELL BRANNCELLE-TEGNER ---
def generate_fire_diagram(img):
    w, h = img.size
    draw_img = img.convert("RGBA")
    overlay = Image.new("RGBA", draw_img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    try: font_large = ImageFont.truetype("arial.ttf", int(h/35))
    except: font_large = ImageFont.load_default()
    try: font_small = ImageFont.truetype("arial.ttf", int(h/50))
    except: font_small = ImageFont.load_default()

    gray_array = np.array(img.convert("L"))
    margin = int(w * 0.1)
    
    # Finn omrisset av bygget (mørke piksler)
    dark_y, dark_x = np.where(gray_array[margin:h-margin, margin:w-margin] < 230)
    
    if len(dark_x) > 100:
        min_x = np.min(dark_x) + margin
        max_x = np.max(dark_x) + margin
        min_y = np.min(dark_y) + margin
        max_y = np.max(dark_y) + margin
        
        # Tegn brannskiller (Røde, tykke linjer)
        mid_x = min_x + (max_x - min_x) // 2
        mid_y = min_y + (max_y - min_y) // 2
        
        draw.line([(min_x, mid_y), (max_x, mid_y)], fill=(239, 68, 68, 200), width=int(w/100))
        draw.line([(mid_x, min_y), (mid_x, max_y)], fill=(239, 68, 68, 200), width=int(w/100))
        
        # Tegn rømningsveier (Grønne piler/linjer)
        draw.line([(mid_x, mid_y), (max_x, max_y)], fill=(34, 197, 94, 255), width=int(w/150))
        draw.line([(mid_x, mid_y), (min_x, max_y)], fill=(34, 197, 94, 255), width=int(w/150))

    # Infoboks
    box_w, box_h = int(w*0.45), int(h*0.22)
    draw.rectangle([w-box_w, h-box_h, w-10, h-10], fill=(15, 23, 42, 240), outline="#ef4444", width=3)
    draw.text((w-box_w+20, h-box_h+20), "BRANNKONSTRUKSJON (TEK17)", fill="#f8fafc", font=font_large)
    
    draw.line([(w-box_w+20, h-box_h+75), (w-box_w+50, h-box_h+75)], fill=(239, 68, 68, 255), width=6)
    draw.text((w-box_w+60, h-box_h+65), "= Branncellebegrensende vegg", fill="#e2e8f0", font=font_small)
    
    draw.line([(w-box_w+20, h-box_h+115), (w-box_w+50, h-box_h+115)], fill=(34, 197, 94, 255), width=6)
    draw.text((w-box_w+60, h-box_h+105), "= Rømningsvei / Utgang", fill="#e2e8f0", font=font_small)

    out = Image.alpha_composite(draw_img, overlay)
    return out.convert("RGB")

# --- 4. DYNAMISK PDF MOTOR (RIBr) ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: RIBr-001"), 0, 1, 'R')
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

def create_full_report_pdf(name, client, content, maps):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
    pdf.add_page()
    if os.path.exists("logo.png"): 
        pdf.image("logo.png", x=25, y=20, w=50) 
    
    pdf.set_y(100)
    pdf.set_x(25)
    pdf.set_font('Helvetica', 'B', 24)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("BRANNTEKNISK KONSEPT (RIBr)"), 0, 1, 'L')
    pdf.set_x(25)
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"FORPROSJEKT: {pdf.p_name}"), 0, 1, 'L')
    pdf.ln(30)
    
    metadata = [
        ("OPPDRAGSGIVER:", client), 
        ("DATO:", datetime.now().strftime("%d. %m. %Y")), 
        ("UTARBEIDET AV:", "Builtly RIBr AI Engine"), 
        ("KONTROLLERT AV:", "[Ansvarlig Branningeniør]")
    ]
    
    for l, v in metadata:
        pdf.set_x(25)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10)
        pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    pdf.add_page()
    pdf.set_x(25)
    pdf.set_font('Helvetica', 'B', 16)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1)
    pdf.ln(5)
    
    toc = [
        "1. SAMMENDRAG OG KONKLUSJON", 
        "2. PROSJEKTBESKRIVELSE OG REGELVERK (TEK17)", 
        "3. RISIKOKLASSE OG BRANNKLASSE", 
        "4. RØMNINGSFORHOLD OG LEDESYSTEM", 
        "5. BRANNCELLER OG BRANNMOTSTAND", 
        "6. SLOKKEUTSTYR OG REDNINGSBRANNVESEN",
        "VEDLEGG: KONSEPTUELL BRANNCELLEINNDELING"
    ]
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(0, 0, 0)
    for t in toc:
        pdf.set_x(25)
        pdf.cell(0, 10, clean_pdf_text(t), 0, 1)
        pdf.set_draw_color(220, 220, 220)
        pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: 
            pdf.ln(4)
            continue
            
        if line.startswith('# '):
            pdf.check_space(30)
            pdf.ln(8)
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.ln(2)
            pdf.set_font('Helvetica', '', 10)
            pdf.set_text_color(0, 0, 0)
        
        elif line.startswith('##'):
            pdf.check_space(20)
            pdf.ln(6)
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 12)
            pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.set_font('Helvetica', '', 10)
            pdf.set_text_color(0, 0, 0)
            
        else:
            pdf.set_font('Helvetica', '', 10)
            safe_text = ironclad_text_formatter(line)
            if safe_text.strip() == "": continue
            try:
                if safe_text.startswith('- ') or safe_text.startswith('* '):
                    pdf.set_x(30)
                    pdf.multi_cell(145, 5, safe_text)
                    pdf.set_x(25)
                else:
                    pdf.set_x(25)
                    pdf.multi_cell(150, 5, safe_text)
            except Exception:
                pdf.ln(2)

    if maps:
        pdf.add_page()
        pdf.set_x(25)
        pdf.set_font('Helvetica', 'B', 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 20, "VEDLEGG: KONSEPTUELL BRANNCELLEINNDELING", 0, 1)
        for i, m in enumerate(maps):
            if i > 0: pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                m.save(tmp.name)
                img_h = 160 * (m.height / m.width)
                pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                pdf.set_y(pdf.get_y() + img_h + 5)
                pdf.set_x(25)
                pdf.set_font('Helvetica', 'I', 10)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: AI-generert prinsipp for rømning og brannceller."), 0, 1, 'C')

    return bytes(pdf.output(dest='S'))

# --- 5. STREAMLIT UI ---
st.title("🔥 RIBr — Brannkonsept")

if pd_state["p_name"]:
    st.success("✅ Prosjektdata er automatisk synkronisert fra Single Source of Truth (SSOT).")

with st.expander("1. Prosjekt & Lokasjon (Auto-synced)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state["p_name"])
    c_name = c2.text_input("Oppdragsgiver", value=pd_state["c_name"])
    adresse = st.text_input("Adresse", value=pd_state["adresse"] + ", " + pd_state["kommune"])

with st.expander("2. Bygningsdata & Klassifisering (Auto-synced)", expanded=True):
    c3, c4, c5 = st.columns(3)
    b_type = c3.text_input("Formål", value=pd_state["b_type"])
    etasjer = c4.number_input("Antall etasjer", value=int(pd_state["etasjer"]))
    bta = c5.number_input("Bruttoareal (BTA m2)", value=int(pd_state["bta"]))
    
    st.markdown("##### Brannteknisk Klassifisering")
    c6, c7 = st.columns(2)
    risikoklasse = c6.selectbox("Risikoklasse (TEK17)", ["RKL 1 (Garasjer/Lager)", "RKL 2 (Kontor)", "RKL 4 (Bolig)", "RKL 6 (Sykehus/Hotell)"], index=1)
    brannklasse = c7.selectbox("Brannklasse (BKL)", ["BKL 1", "BKL 2", "BKL 3", "BKL 4"], index=1)

with st.expander("3. Arkitektur & Plantegninger", expanded=True):
    files = st.file_uploader("Last opp arkitekttegninger for rømnings-skisse (PDF/Bilder)", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

if st.button("Kjør Brannteknisk Analyse (RIBr)", type="primary"):
    
    processed_maps = []
    if files:
        with st.spinner("📐 Arkitekt-AI tegner inn brannceller og rømningsveier..."):
            try:
                for f in files[:1]: 
                    if f.name.lower().endswith('pdf'):
                        if fitz is None: st.stop()
                        doc = fitz.open(stream=f.read(), filetype="pdf")
                        pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                        doc.close() 
                    else:
                        img = Image.open(f)
                    
                    m = generate_fire_diagram(img)
                    processed_maps.append(m)
            except Exception as e:
                pass
                
    with st.spinner("🤖 Genererer brannstrategi etter TEK17..."):
        try:
            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        valgt_modell = valid_models[0]
        for fav in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if fav in valid_models:
                valgt_modell = fav
                break
        
        model = genai.GenerativeModel(valgt_modell)

        prompt = f"""
        Du er Builtly RIBr AI, en senior branningeniør.
        Skriv et formelt "Brannteknisk konsept" for prosjektet:
        
        PROSJEKT: {p_name} ({bta} m2, {etasjer} etasjer). 
        KLASSIFISERING: {risikoklasse}, {brannklasse}.
        LOKASJON: {adresse}.
        
        KUNDENS PROSJEKTBESKRIVELSE (KONTEKST): 
        "{pd_state['p_desc']}"
        
        INSTRUKSER (Skriv formelt, teknisk tungt og presist, min 1200 ord):
        - Skriv teknisk iht. veiledning til TEK17.
        - Vurder rømning, brannceller og slokkeutstyr spesifikt basert på kundens beskrivelse og formålet ({b_type}).
        
        STRUKTUR (Bruk KUN disse nøyaktige overskriftene):
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. PROSJEKTBESKRIVELSE OG LOVER (TEK17)
        # 3. RISIKOKLASSE OG BRANNKLASSE
        # 4. RØMNINGSFORHOLD OG LEDESYSTEM
        # 5. BRANNCELLER OG BRANNMOTSTAND
        # 6. SLOKKEUTSTYR OG TILRETTELEGGING FOR REDNINGSBRANNVESEN
        """
        
        try:
            res = model.generate_content(prompt)
            with st.spinner("Kompilerer Brann-PDF..."):
                pdf_data = create_full_report_pdf(p_name, c_name, res.text, processed_maps)
            
            st.success("✅ RIBr Rapport er ferdigstilt!")
            st.download_button("📄 Last ned Brannkonsept", pdf_data, f"Builtly_RIBr_{p_name}.pdf")
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")
