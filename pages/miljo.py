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
import json
import requests
from PIL import Image, ImageDraw, ImageFont

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Geoteknikk Pro | Builtly AI", layout="wide")

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
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{4,}', ' ', text)
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    return clean_pdf_text(text)

# --- NY: KARTVERKET API INTEGRASJON ---
def fetch_kartverket_data(adresse):
    """Henter ekte stedsdata og koordinater fra Norges offisielle Geonorge API"""
    try:
        url = f"https://ws.geonorge.no/adresser/v1/sok?sok={adresse}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('adresser'):
                hit = data['adresser'][0]
                nord = hit.get('representasjonspunkt', {}).get('nord', 'Ukjent')
                ost = hit.get('representasjonspunkt', {}).get('øst', 'Ukjent')
                kommune = hit.get('kommunenavn', 'Ukjent')
                fylke = hit.get('fylkesnavn', 'Ukjent')
                return f"Bekreftet i Kartverket: {hit['adressetekst']}, {kommune} ({fylke}). Koordinater: Nord {nord}, Øst {ost}."
    except Exception as e:
        pass
    return f"Kartverket (Frakoblet/Fiktiv): Adresse '{adresse}' antatt i marin grense, Trøndelag. Koordinatsystem EUREF89."

# --- 2. BEREGNINGSMOTOR (AUTOMATISK BOREPLAN) ---
def generate_geo_boreplan(img, project_name):
    """Tegner automatisk inn geotekniske borepunkter (BP) rundt bygningskroppen"""
    w, h = img.size
    draw_img = img.convert("RGBA")
    overlay = Image.new("RGBA", draw_img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    try: 
        font_large = ImageFont.truetype("arial.ttf", int(h/45))
        font_small = ImageFont.truetype("arial.ttf", int(h/60))
    except: 
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # --- AVANSERT BYGG-SPORING ---
    gray_array = np.array(img.convert("L"))
    margin_x, margin_y = int(w * 0.15), int(h * 0.15)
    
    # Finner hjørnene av bygget for å plassere borepunkter
    dark_y, dark_x = np.where(gray_array[margin_y:h-margin_y, margin_x:w-margin_x] < 230)
    
    bore_points = []
    if len(dark_x) > 100:
        # Justerer koordinatene tilbake til originalbildet
        min_x = np.min(dark_x) + margin_x
        max_x = np.max(dark_x) + margin_x
        min_y = np.min(dark_y) + margin_y
        max_y = np.max(dark_y) + margin_y
        
        # Plasserer 4 borepunkter i hjørnene, med 10% offset ut fra bygget
        offset_x, offset_y = int((max_x - min_x) * 0.1), int((max_y - min_y) * 0.1)
        
        bore_points = [
            (min_x - offset_x, min_y - offset_y, "BP1"), # Topp Venstre
            (max_x + offset_x, min_y - offset_y, "BP2"), # Topp Høyre
            (max_x + offset_x, max_y + offset_y, "BP3"), # Bunn Høyre
            (min_x - offset_x, max_y + offset_y, "BP4"), # Bunn Venstre
            ((min_x + max_x)//2, ((min_y + max_y)//2), "BP5") # Senter (Kjerneboring)
        ]
    else:
        # Fallback hvis tegningen er blank
        cx, cy = w//2, h//2
        bore_points = [(cx-100, cy-100, "BP1"), (cx+100, cy-100, "BP2"), (cx, cy+100, "BP3")]

    # Tegner bransjestandard symbol for Borepunkt (Sirkel med kryss)
    r = int(h/70)
    for px, py, label in bore_points:
        # Hvit bakgrunn for lesbarhet
        draw.ellipse([px-r-2, py-r-2, px+r+2, py+r+2], fill=(255,255,255,240))
        # Rød sirkel
        draw.ellipse([px-r, py-r, px+r, py+r], outline=(200,0,0,255), width=3)
        # Kryss inni sirkelen
        draw.line([px-r, py, px+r, py], fill=(200,0,0,255), width=2)
        draw.line([px, py-r, px, py+r], fill=(200,0,0,255), width=2)
        # Etikett (BP1, BP2 etc)
        draw.text((px+r+5, py-r), label, fill=(0,0,0,255), font=font_large)

    # Profesjonell Info-boks
    box_w, box_h = int(w*0.35), int(h*0.2)
    draw.rectangle([w-box_w, h-box_h, w-10, h-10], fill=(255,255,255,240), outline="black", width=3)
    draw.text((w-box_w+20, h-box_h+20), f"FORESLÅTT BOREPLAN", fill="black", font=font_large)
    draw.text((w-box_w+20, h-box_h+60), f"Prosjekt: {project_name}", fill="black", font=font_large)
    draw.text((w-box_w+20, h-box_h+100), f"Antall punkt: {len(bore_points)} (Totalsondering/CPTU)", fill=(50,50,150), font=font_small)

    out = Image.alpha_composite(draw_img, overlay)
    return out.convert("RGB"), bore_points

# --- 3. DYNAMISK PDF MOTOR (AUTO-BREDDE w=0) ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: GEO-001"), 0, 1, 'R')
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
    pdf.set_font('Helvetica', 'B', 26)
    pdf.set_text_color(26, 43, 72)
    pdf.multi_cell(0, 10, clean_pdf_text("GEOTEKNISK VURDERING (RIG)"))
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(0, 10, clean_pdf_text(f"FOR RAMMETILLATELSE: {pdf.p_name}"))
    pdf.ln(30)
    
    metadata = [
        ("OPPDRAGSGIVER:", client), 
        ("DATO:", datetime.now().strftime("%d. %m. %Y")), 
        ("UTARBEIDET AV:", "Builtly RIG AI Engine v2.0"), 
        ("KONTROLLERT AV:", "[Ansvarlig Geotekniker]")
    ]
    
    for l, v in metadata:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10)
        pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 16)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1)
    pdf.ln(5)
    
    toc = [
        "1. SAMMENDRAG OG KONKLUSJON", 
        "2. INNLEDNING OG PROSJEKTBESKRIVELSE", 
        "3. KARTVERKET OG TOPOGRAFI", 
        "4. FORVENTEDE GRUNNFORHOLD (NGU)", 
        "5. KVIKKLEIRE OG OMRÅDESTABILITET", 
        "6. FUNDAMENTERING OG GRAVING", 
        "7. ANBEFALT GRUNNUNDERSØKELSE (BOREPLAN)", 
        "VEDLEGG: FORESLÅTT BOREPLAN"
    ]
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(0, 0, 0)
    for t in toc:
        pdf.cell(0, 10, clean_pdf_text(t), 0, 1)
        pdf.set_draw_color(220, 220, 220)
        pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: 
            pdf.ln(4)
            continue
            
        if line.startswith('# ') or re.match(r'^\d\.\s[A-Z]', line):
            pdf.check_space(40)
            pdf.ln(10)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(0, 8, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.ln(2)
            pdf.set_font('Helvetica', '', 10)
            pdf.set_text_color(0, 0, 0)
        
        elif line.startswith('##'):
            pdf.check_space(20)
            pdf.ln(6)
            pdf.set_font('Helvetica', 'B', 12)
            pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(0, 8, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.set_font('Helvetica', '', 10)
            pdf.set_text_color(0, 0, 0)
            
        else:
            pdf.set_font('Helvetica', '', 10)
            safe_text = ironclad_text_formatter(line)
            
            if safe_text.strip() == "": 
                continue

            try:
                if safe_text.startswith('- ') or safe_text.startswith('* '):
                    pdf.set_x(30)
                    pdf.multi_cell(0, 5, safe_text)
                    pdf.set_x(25)
                else:
                    pdf.set_x(25)
                    pdf.multi_cell(0, 5, safe_text)
            except Exception:
                pdf.ln(2)

    if maps:
        pdf.add_page()
        pdf.set_font('Helvetica', 'B', 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 20, "VEDLEGG: FORESLÅTT BOREPLAN", 0, 1)
        for i, m in enumerate(maps):
            if i > 0: 
                pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                m.save(tmp.name)
                img_h = 160 * (m.height / m.width)
                pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                pdf.set_y(pdf.get_y() + img_h + 5)
                pdf.set_font('Helvetica', 'I', 10)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: AI-generert forslag til geotekniske borepunkter (BP)."), 0, 1, 'C')
                
    return bytes(pdf.output(dest='S'))

# --- 4. STREAMLIT UI ---
st.title("🌍 Builtly RIG AI (Geoteknikk)")
st.info("Genererer geotekniske vurderinger og boreplaner med integrasjon mot Kartverket.")

with st.expander("Prosjekt & Lokasjon", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", "Saga Park")
    c_name = c2.text_input("Oppdragsgiver", "Saga Park AS")
    
    adresse = st.text_input("Prosjektadresse (Brukt til Kartverket-oppslag)", "Industriveien, Trondheim")

files = st.file_uploader("Last opp situasjonsplan for Boreplan (PDF/PNG)", accept_multiple_files=True)

if st.button("GENERER KOMPLETT GEOTEKNISK RAPPORT", type="primary"):
    if not files: 
        st.error("Last opp en tegning først for å generere boreplan.")
    else:
        # 1. HENT DATA FRA KARTVERKET
        kartverket_info = ""
        with st.spinner("🌍 Kobler til Geonorge/Kartverket API..."):
            kartverket_info = fetch_kartverket_data(adresse)
            st.success(kartverket_info)
        
        # 2. GENERER GRAFIKK OG BOREPLAN
        processed_maps = []
        with st.spinner("1. Analyserer tegning og oppretter boreplan..."):
            try:
                for i in range(min(len(files), 1)): # Lager kun boreplan for første tegning for demo
                    f = files[i]
                    if f.name.lower().endswith('pdf'):
                        if fitz is None: st.stop()
                        doc = fitz.open(stream=f.read(), filetype="pdf")
                        pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                        doc.close() 
                        del pix 
                    else:
                        img = Image.open(f)
                    
                    m, b_points = generate_geo_boreplan(img, p_name)
                    processed_maps.append(m)
                    img.close()
                    gc.collect()

                st.toast("Boreplan ferdig. Kobler til Google AI...")
                
                # 3. KOBLE TIL AI OG SKRIV RAPPORT
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
                Du er Builtly RIG AI, en fagekspert innen geoteknikk i Norge (Rådgivende Ingeniør Geoteknikk).
                Skriv en detaljert skrivebordsstudie og geoteknisk vurdering for rammetillatelse.
                
                PROSJEKT: {p_name}
                OPPDRAGSGIVER: {c_name}
                ADRESSE: {adresse}
                KARTVERKET-DATA: {kartverket_info}
                BOREPLAN: Generert med {len(b_points)} borepunkter.
                
                INSTRUKSER:
                - Skriv utfyllende, profesjonelt og formelt. Minimum 1500 ord.
                - Bruk Kartverket-dataen til å drøfte sannsynlige grunnforhold via NGU sine databaser (Anta forhold typisk for denne kommunen, f.eks. marin grense/kvikkleire hvis det er i Trøndelag/Østlandet, eller berg/morene andre steder).
                - Foreslå konkrete fundamenteringsmetoder (peling vs. direkte fundamentering).
                
                STRUKTUR:
                # 1. SAMMENDRAG OG KONKLUSJON
                # 2. INNLEDNING OG PROSJEKTBESKRIVELSE
                # 3. KARTVERKET OG TOPOGRAFI (Bruk koordinatene og dataen)
                # 4. FORVENTEDE GRUNNFORHOLD (NGU) (Drøft løsmasser, berg, grunnvann)
                # 5. KVIKKLEIRE OG OMRÅDESTABILITET (Svært viktig vurdering)
                # 6. FUNDAMENTERING OG GRAVING (Anbefalinger)
                # 7. ANBEFALT GRUNNUNDERSØKELSE (BOREPLAN) (Forklar at vi har lagt opp {len(b_points)} punkter i vedlegget for totalsondering/CPTU).
                """
                
                res = model.generate_content(prompt)
                
                with st.spinner("Kompilerer profesjonell PDF..."):
                    pdf_data = create_full_report_pdf(p_name, c_name, res.text, processed_maps)
                
                st.success("✅ Komplett geoteknisk utredning er ferdigstilt!")
                st.download_button("📄 Last ned Builtly GEO-rapport", pdf_data, f"Builtly_GEO_{p_name}.pdf")
            except Exception as e: 
                st.error(f"Kritisk feil under generering: {e}")
