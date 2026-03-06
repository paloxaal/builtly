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
import urllib.parse
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
    text = re.sub(r'[-|=]{3,}', ' ', text)
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    return clean_pdf_text(text)

# --- 2. KARTVERKET API & FLYFOTO (NORGE I BILDER WMS) ---
def fetch_kartverket_data(adresse, kommune, gnr, bnr):
    query_parts = []
    if adresse: query_parts.append(adresse)
    if kommune: query_parts.append(kommune)
    if gnr and bnr: query_parts.append(f"{gnr}/{bnr}")
    
    search_query = " ".join(query_parts)
    if not search_query.strip():
        return "Ingen adresse eller Gnr/Bnr oppgitt.", None, None

    try:
        safe_query = urllib.parse.quote(search_query)
        url = f"https://ws.geonorge.no/adresser/v1/sok?sok={safe_query}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('adresser'):
                hit = data['adresser'][0]
                nord = hit.get('representasjonspunkt', {}).get('nord', None)
                ost = hit.get('representasjonspunkt', {}).get('øst', None)
                kom = hit.get('kommunenavn', 'Ukjent')
                fylke = hit.get('fylkesnavn', 'Ukjent')
                tekst = f"Bekreftet i Kartverket: {hit['adressetekst']}, {kom} ({fylke}). Koordinater: N {nord}, Ø {ost}."
                return tekst, nord, ost
            else:
                return f"Fant ingen treff i Kartverket for søket: '{search_query}'.", None, None
    except Exception:
        return "Kartverket API utilgjengelig.", None, None

def fetch_kartverket_flyfoto(nord, ost):
    """Henter høyoppløselig ortofoto (flyfoto) fra Statens Kartverk WMS"""
    try:
        if not nord or not ost: return None
        # Lager en bounding box (300x300 meter) rundt koordinaten
        min_x, max_x = float(ost) - 150, float(ost) + 150
        min_y, max_y = float(nord) - 150, float(nord) + 150
        
        # WMS-kall til 'Norge i Bilder'
        wms_url = f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
        
        response = requests.get(wms_url, timeout=10)
        if response.status_code == 200:
            return Image.open(io.BytesIO(response.content))
    except Exception as e:
        return None
    return None

# --- 3. BEREGNINGSMOTOR (AUTOMATISK BOREPLAN) ---
def generate_geo_boreplan(img, project_name):
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

    gray_array = np.array(img.convert("L"))
    margin_x, margin_y = int(w * 0.15), int(h * 0.15)
    
    dark_y, dark_x = np.where(gray_array[margin_y:h-margin_y, margin_x:w-margin_x] < 230)
    
    bore_points = []
    if len(dark_x) > 100:
        min_x = np.min(dark_x) + margin_x
        max_x = np.max(dark_x) + margin_x
        min_y = np.min(dark_y) + margin_y
        max_y = np.max(dark_y) + margin_y
        
        offset_x, offset_y = int((max_x - min_x) * 0.1), int((max_y - min_y) * 0.1)
        
        bore_points = [
            (min_x - offset_x, min_y - offset_y, "BP1"),
            (max_x + offset_x, min_y - offset_y, "BP2"),
            (max_x + offset_x, max_y + offset_y, "BP3"),
            (min_x - offset_x, max_y + offset_y, "BP4"),
            ((min_x + max_x)//2, ((min_y + max_y)//2), "BP5") 
        ]
    else:
        cx, cy = w//2, h//2
        bore_points = [(cx-100, cy-100, "BP1"), (cx+100, cy-100, "BP2"), (cx, cy+100, "BP3")]

    r = int(h/70)
    for px, py, label in bore_points:
        draw.ellipse([px-r-2, py-r-2, px+r+2, py+r+2], fill=(255,255,255,240))
        draw.ellipse([px-r, py-r, px+r, py+r], outline=(200,0,0,255), width=3)
        draw.line([px-r, py, px+r, py], fill=(200,0,0,255), width=2)
        draw.line([px, py-r, px, py+r], fill=(200,0,0,255), width=2)
        draw.text((px+r+5, py-r), label, fill=(0,0,0,255), font=font_large)

    box_w, box_h = int(w*0.35), int(h*0.2)
    draw.rectangle([w-box_w, h-box_h, w-10, h-10], fill=(255,255,255,240), outline="black", width=3)
    draw.text((w-box_w+20, h-box_h+20), f"FORESLÅTT BOREPLAN", fill="black", font=font_large)
    draw.text((w-box_w+20, h-box_h+60), f"Prosjekt: {project_name}", fill="black", font=font_large)
    draw.text((w-box_w+20, h-box_h+100), f"Antall punkt: {len(bore_points)} (Totalsondering/CPTU)", fill=(50,50,150), font=font_small)

    out = Image.alpha_composite(draw_img, overlay)
    return out.convert("RGB"), bore_points

# --- 4. DYNAMISK PDF MOTOR MED FLYFOTO-INTEGRASJON ---
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

def create_full_report_pdf(name, client, content, maps, aerial_photo):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
    # FORSIDE FIKSET (Likt Akustikk)
    pdf.add_page()
    if os.path.exists("logo.png"): 
        pdf.image("logo.png", x=25, y=20, w=50)
    pdf.set_y(100)
    pdf.set_x(25)
    pdf.set_font('Helvetica', 'B', 26)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("GEOTEKNISK VURDERING (RIG)"), 0, 1, 'L')
    pdf.set_x(25)
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"FOR RAMMETILLATELSE: {pdf.p_name}"), 0, 1, 'L')
    pdf.ln(30)
    
    metadata = [
        ("OPPDRAGSGIVER:", client), 
        ("DATO:", datetime.now().strftime("%d. %m. %Y")), 
        ("UTARBEIDET AV:", "Builtly RIG AI Engine v2.0"), 
        ("KONTROLLERT AV:", "[Ansvarlig Geotekniker]")
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
            
        # MAGISK INJEKSJON AV FLYFOTO UNDER KAPITTEL 3
        if line.startswith('# 3. KARTVERKET'):
            pdf.check_space(180) # Sikrer at det er plass til hele bildet
            pdf.ln(8)
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.ln(4)
            
            if aerial_photo:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    aerial_photo.save(tmp.name)
                    img_h = 160 * (aerial_photo.height / aerial_photo.width)
                    pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                    pdf.set_y(pdf.get_y() + img_h + 5)
                    pdf.set_font('Helvetica', 'I', 9)
                    pdf.set_text_color(100, 100, 100)
                    pdf.set_x(25)
                    pdf.cell(0, 5, clean_pdf_text("Figur 1: Ortofoto over prosjektområdet. Hentet automatisk via Kartverket WMS (Norge i Bilder)."), 0, 1, 'C')
                    pdf.ln(5)
            else:
                pdf.set_font('Helvetica', 'I', 9)
                pdf.set_text_color(150, 0, 0)
                pdf.set_x(25)
                pdf.cell(0, 5, "Merknad: Kunne ikke hente flyfoto fra Kartverket for denne adressen.", 0, 1, 'L')
                pdf.ln(5)
                
            pdf.set_font('Helvetica', '', 10)
            pdf.set_text_color(0, 0, 0)
            continue
            
        elif line.startswith('# ') or re.match(r'^\d\.\s[A-Z]', line):
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
        pdf.cell(0, 20, "VEDLEGG: FORESLÅTT BOREPLAN", 0, 1)
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
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: AI-generert forslag til geotekniske borepunkter (BP)."), 0, 1, 'C')
                
    return bytes(pdf.output(dest='S'))

# --- 5. STREAMLIT UI ---
st.title("🌍 Builtly RIG AI (Geoteknikk)")
st.info("Genererer geotekniske vurderinger og boreplaner med direkte WMS-integrasjon mot Kartverket.")

with st.expander("Prosjekt & Lokasjon", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", "Saga Park")
    c_name = c2.text_input("Oppdragsgiver", "Saga Park AS")
    
    st.markdown("##### Kartverket Oppslag")
    c3, c4 = st.columns(2)
    adresse = c3.text_input("Gatenavn og nummer", "Industriveien 1B")
    kommune = c4.text_input("Kommune", "Trondheim")
    
    c5, c6 = st.columns(2)
    gnr = c5.text_input("Gårdsnummer (Gnr)", "316")
    bnr = c6.text_input("Bruksnummer (Bnr)", "725")

files = st.file_uploader("Last opp situasjonsplan for Boreplan (Kun PDF/Bilder)", accept_multiple_files=True)

if st.button("GENERER KOMPLETT GEOTEKNISK RAPPORT", type="primary"):
    if not files: 
        st.error("Last opp en tegning først for å generere boreplan.")
    else:
        # 1. HENT DATA FRA KARTVERKET
        kartverket_info = ""
        nord = ost = None
        aerial_photo = None
        
        with st.spinner("🌍 Kobler til Geonorge API (Koordinater og WMS Flyfoto)..."):
            kartverket_info, nord, ost = fetch_kartverket_data(adresse, kommune, gnr, bnr)
            if nord and ost:
                st.success(kartverket_info)
                aerial_photo = fetch_kartverket_flyfoto(nord, ost)
                if aerial_photo:
                    st.success("✅ Lastet ned høyoppløselig flyfoto fra Norge i Bilder (WMS)!")
            else:
                st.warning(kartverket_info)
        
        # 2. GENERER GRAFIKK OG BOREPLAN
        processed_maps = []
        with st.spinner("1. Analyserer tegning og oppretter boreplan..."):
            try:
                valid_image_files = [f for f in files if f.name.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg'))]
                if not valid_image_files:
                    st.warning("Fant ingen gyldige bildefiler for å tegne boreplan. Bruk PDF eller bildefiler.")
                
                for i in range(min(len(valid_image_files), 1)): 
                    f = valid_image_files[i]
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
                ADRESSE / GNR / BNR: {adresse}, {kommune}. Gnr {gnr} / Bnr {bnr}.
                KARTVERKET-DATA: {kartverket_info}
                BOREPLAN: Generert og vedlagt rapporten.
                
                INSTRUKSER:
                - Skriv utfyllende, profesjonelt og formelt. Minimum 1500 ord.
                - Bruk Kartverket-dataen til å drøfte sannsynlige grunnforhold via NGU sine databaser (Anta forhold typisk for denne kommunen).
                - Foreslå konkrete fundamenteringsmetoder (peling vs. direkte fundamentering).
                
                STRUKTUR (Bruk disse eksakte overskriftene, uten tillegg i parentes):
                # 1. SAMMENDRAG OG KONKLUSJON
                # 2. INNLEDNING OG PROSJEKTBESKRIVELSE
                # 3. KARTVERKET OG TOPOGRAFI
                # 4. FORVENTEDE GRUNNFORHOLD (NGU)
                # 5. KVIKKLEIRE OG OMRÅDESTABILITET
                # 6. FUNDAMENTERING OG GRAVING
                # 7. ANBEFALT GRUNNUNDERSØKELSE (BOREPLAN)
                """
                
                res = model.generate_content(prompt)
                
                with st.spinner("Kompilerer profesjonell PDF..."):
                    pdf_data = create_full_report_pdf(p_name, c_name, res.text, processed_maps, aerial_photo)
                
                st.success("✅ Komplett geoteknisk utredning er ferdigstilt!")
                st.download_button("📄 Last ned Builtly GEO-rapport", pdf_data, f"Builtly_GEO_{p_name}.pdf")
            except Exception as e: 
                st.error(f"Kritisk feil under generering: {e}")
