import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
from datetime import datetime
import tempfile
import re
import numpy as np
import io
import math
import gc
from PIL import Image, ImageDraw, ImageFont

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Brannkonsept Pro | Builtly AI", layout="wide")

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

def clean_pdf_text(text):
    if not text: 
        return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*", "²": "2", "³": "3"}
    for old, new in rep.items(): 
        text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    """Den ultimate PDF-beskytteren"""
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{3,}', ' ', text)
    # Tvinger mellomrom inn i alle ord over 30 tegn
    text = re.sub(r'([^\s]{30})', r'\1 ', text)
    return clean_pdf_text(text)

# --- 2. BEREGNINGSMOTOR (BRANNSKILLER OG RØMNING) ---
def generate_fire_plan(img, floor_num):
    w, h = img.size
    draw_img = img.convert("RGBA")
    overlay = Image.new("RGBA", draw_img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    try: 
        font_large = ImageFont.truetype("arial.ttf", int(h/40))
        font_small = ImageFont.truetype("arial.ttf", int(h/60))
    except: 
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    gray_array = np.array(img.convert("L"))
    margin_x, margin_y = int(w * 0.1), int(h * 0.1)
    
    # Finner ytterkantene av selve bygget
    dark_y, dark_x = np.where(gray_array[margin_y:h-margin_y, margin_x:w-margin_x] < 230)
    
    if len(dark_x) > 100:
        min_x = np.min(dark_x) + margin_x
        max_x = np.max(dark_x) + margin_x
        min_y = np.min(dark_y) + margin_y
        max_y = np.max(dark_y) + margin_y
        
        # 1. Tegner Brannskiller (Deler bygget i brannceller)
        mid_x = (min_x + max_x) // 2
        mid_y = (min_y + max_y) // 2
        
        # Hovedbrannskille tvers over (Rød stiplet linje)
        dash_length = int(h/40)
        for y in range(min_y, max_y, dash_length * 2):
            draw.line([(mid_x, y), (mid_x, min(y + dash_length, max_y))], fill=(220, 0, 0, 200), width=int(w/150))
            
        draw.text((mid_x + 10, min_y + 20), "EI60 / REI60", fill=(200,0,0,255), font=font_small)

        # 2. Tegner Rømningsveier (Grønne piler)
        arrow_color = (0, 180, 0, 220)
        arrow_w = int(w/200)
        
        # Rømning fra venstre branncelle
        start_xl = min_x + int((mid_x - min_x)/2)
        draw.line([(start_xl, mid_y), (start_xl, max_y + 50)], fill=arrow_color, width=arrow_w)
        # Pilhode
        draw.polygon([(start_xl, max_y + 50), (start_xl - 10, max_y + 30), (start_xl + 10, max_y + 30)], fill=arrow_color)
        draw.text((start_xl + 15, max_y + 10), "Rømning", fill=(0,150,0,255), font=font_small)

        # Rømning fra høyre branncelle
        start_xr = mid_x + int((max_x - mid_x)/2)
        draw.line([(start_xr, mid_y), (start_xr, max_y + 50)], fill=arrow_color, width=arrow_w)
        # Pilhode
        draw.polygon([(start_xr, max_y + 50), (start_xr - 10, max_y + 30), (start_xr + 10, max_y + 30)], fill=arrow_color)
        draw.text((start_xr + 15, max_y + 10), "Rømning", fill=(0,150,0,255), font=font_small)

    # Info-boks
    box_w, box_h = int(w*0.35), int(h*0.2)
    draw.rectangle([w-box_w, h-box_h, w-10, h-10], fill=(255,255,255,240), outline="black", width=3)
    draw.text((w-box_w+20, h-box_h+20), f"BRANNTEKNISK KONSEPT", fill="black", font=font_large)
    draw.text((w-box_w+20, h-box_h+60), f"PLAN {floor_num}", fill="black", font=font_large)
    
    # Tegnforklaring i boksen
    draw.line([(w-box_w+20, h-box_h+110), (w-box_w+60, h-box_h+110)], fill=(220,0,0,255), width=4)
    draw.text((w-box_w+70, h-box_h+100), "= Brannskille (Branncelle)", fill="black", font=font_small)
    
    draw.line([(w-box_w+20, h-box_h+140), (w-box_w+60, h-box_h+140)], fill=(0,180,0,255), width=4)
    draw.text((w-box_w+70, h-box_h+130), "= Rømningsvei", fill="black", font=font_small)

    out = Image.alpha_composite(draw_img, overlay)
    return out.convert("RGB")

# --- 3. DYNAMISK PDF MOTOR (LÅSTE MARGER) ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: BRANN-001"), 0, 1, 'R')
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
    
    # FORSIDE
    pdf.add_page()
    if os.path.exists("logo.png"): 
        pdf.image("logo.png", x=25, y=20, w=50)
    pdf.set_y(100)
    pdf.set_x(25)
    pdf.set_font('Helvetica', 'B', 26)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("BRANNTEKNISK KONSEPT (RIBr)"), 0, 1, 'L')
    pdf.set_x(25)
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"FOR RAMMETILLATELSE: {pdf.p_name}"), 0, 1, 'L')
    pdf.ln(30)
    
    metadata = [
        ("OPPDRAGSGIVER:", client), 
        ("DATO:", datetime.now().strftime("%d. %m. %Y")), 
        ("UTARBEIDET AV:", "Builtly RIBr AI Engine (Senior)"), 
        ("KONTROLLERT AV:", "[Ansvarlig Brannrådgiver]")
    ]
    
    for l, v in metadata:
        pdf.set_x(25)
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10)
        pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    # INNHOLDSFORTEGNELSE
    pdf.add_page()
    pdf.set_x(25)
    pdf.set_font('Helvetica', 'B', 16)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1)
    pdf.ln(5)
    
    toc = [
        "1. SAMMENDRAG OG KONKLUSJON", 
        "2. INNLEDNING OG PROSJEKTBESKRIVELSE", 
        "3. RISIKOKLASSE OG BRANNKLASSE", 
        "4. BRANNSKILLER OG SEKSJONERING", 
        "5. RØMNING OG REDNING", 
        "6. TILRETTELEGGING FOR BRANNVESENET", 
        "7. RASJONELLE OPTIMALISERINGER (KOST/NYTTE)", 
        "VEDLEGG: BRANNTEKNISKE TEGNINGER"
    ]
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(0, 0, 0)
    for t in toc:
        pdf.set_x(25)
        pdf.cell(0, 10, clean_pdf_text(t), 0, 1)
        pdf.set_draw_color(220, 220, 220)
        pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    # RAPPORTTEKST
    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: 
            pdf.ln(4)
            continue
            
        if line.startswith('# ') or re.match(r'^\d\.\s[A-Z]', line):
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

    # VEDLEGG
    if maps:
        pdf.add_page()
        pdf.set_x(25)
        pdf.set_font('Helvetica', 'B', 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 20, "VEDLEGG: BRANNTEKNISKE TEGNINGER", 0, 1)
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
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: AI-generert brannkonsept for Plan {i+1} (Rømning og seksjonering)."), 0, 1, 'C')
                
    return bytes(pdf.output(dest='S'))

# --- 4. STREAMLIT UI ---
st.title("🔥 Builtly RIBr AI (Brannsikkerhet)")
st.info("Genererer brannteknisk konsept for rammetillatelse. Optimalisert for rasjonelle TEK17-løsninger.")

with st.expander("Prosjekt & Bygningsdata", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", "Saga Park")
    c_name = c2.text_input("Oppdragsgiver", "Saga Park AS")
    
    st.markdown("##### Parametere (TEK17)")
    c3, c4, c5 = st.columns(3)
    b_type = c3.selectbox("Hovedformål", ["Bolig (Blokk)", "Kontor", "Næring/Industri", "Skole/Barnehage"])
    et_count = c4.number_input("Antall etasjer", value=4, min_value=1)
    bta = c5.number_input("Bruttoareal (BTA m2)", value=2500, step=100)

files = st.file_uploader("Last opp plantegninger (PDF/Bilder)", accept_multiple_files=True)

if st.button("GENERER BRANNTEKNISK KONSEPT", type="primary"):
    if not files: 
        st.error("Last opp plantegninger først for å tegne rømningsveier.")
    else:
        processed_maps = []
        with st.spinner("1. Analyserer tegninger og tegner brannceller/rømningsveier..."):
            try:
                valid_files = [f for f in files if f.name.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg'))]
                if not valid_files:
                    st.warning("Fant ingen gyldige bildefiler. Genererer kun tekst-rapport.")
                
                # Begrenser til max 2 etasjer for demo, for å spare minne
                for i in range(min(len(valid_files), 2)): 
                    f = valid_files[i]
                    if f.name.lower().endswith('pdf'):
                        if fitz is None: st.stop()
                        doc = fitz.open(stream=f.read(), filetype="pdf")
                        pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                        doc.close() 
                        del pix 
                    else:
                        img = Image.open(f)
                    
                    m = generate_fire_plan(img, i+1)
                    processed_maps.append(m)
                    img.close()
                    gc.collect()

                st.toast("Tegninger ferdig. Kobler til Senior Brann-AI...")
                
                gyldige_modeller = []
                for mod in genai.list_models():
                    if 'generateContent' in mod.supported_generation_methods:
                        gyldige_modeller.append(mod.name)

                valgt_modell = gyldige_modeller[0]
                for favoritt in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
                    if favoritt in gyldige_modeller:
                        valgt_modell = favoritt
                        break
                
                model = genai.GenerativeModel(valgt_modell)

                prompt = f"""
                Du er Builtly RIBr AI, en svært erfaren og ettertraktet senior brannrådgiver i Norge.
                Du skal skrive et brannteknisk konsept for rammetillatelse.
                
                PROSJEKT: {p_name}
                OPPDRAGSGIVER: {c_name}
                BYGG: {b_type}, {et_count} etasjer, BTA {bta} m2.
                
                DIN FILOSOFI SOM SENIOR:
                Du kjenner TEK17 og veiledningen (VTEK) til fingerspissene. Du er kjent for å finne de mest *rasjonelle* og kostnadseffektive løsningene for utbygger, uten at det går på bekostning av sikkerheten. Du unngår unødvendig overprosjektering.
                
                INSTRUKSER TIL TEKSTEN:
                - Skriv utfyllende, profesjonelt og med tyngde. Minimum 1500 ord.
                - Begrunn valg av Risikoklasse (sannsynligvis 4 for bolig, 2 for kontor) og Brannklasse (1, 2 eller 3 avhengig av etasjer).
                - Identifiser hvor vi kan spare penger (F.eks: Kan vi unngå fullsprinkling ved spesifikke tiltak? Er det nok med trapperom type A fremfor B under visse forutsetninger?).
                
                STRUKTUR (Bruk disse eksakte overskriftene, uten tillegg i parentes):
                # 1. SAMMENDRAG OG KONKLUSJON
                # 2. INNLEDNING OG PROSJEKTBESKRIVELSE
                # 3. RISIKOKLASSE OG BRANNKLASSE
                # 4. BRANNSKILLER OG SEKSJONERING
                # 5. RØMNING OG REDNING
                # 6. TILRETTELEGGING FOR BRANNVESENET
                # 7. RASJONELLE OPTIMALISERINGER (VIKTIG KAPITTEL: Beskriv kostnadseffektive TEK17-valg for dette bygget)
                """
                
                res = model.generate_content(prompt)
                
                with st.spinner("Kompilerer profesjonell PDF..."):
                    pdf_data = create_full_report_pdf(p_name, c_name, res.text, processed_maps)
                
                st.success("✅ Komplett brannkonsept er ferdigstilt!")
                st.download_button("📄 Last ned Builtly BRANN-rapport", pdf_data, f"Builtly_BRANN_{p_name}.pdf")
            except Exception as e: 
                st.error(f"Kritisk feil under generering: {e}")
