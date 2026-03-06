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
import gc

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Konstruksjonsteknikk (RIB) | Builtly AI", layout="wide")

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
    if not text: return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*", "²": "2", "³": "3"}
    for old, new in rep.items(): 
        text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{3,}', ' ', text)
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    return clean_pdf_text(text)

# --- 2. KARTKATALOGEN API (MILJØLASTER / LOKASJON) ---
def fetch_kartverket_data(adresse, kommune, gnr, bnr):
    adr_clean = adresse.replace(',', '').strip() if adresse else ""
    kom_clean = kommune.replace(',', '').strip() if kommune else ""

    def api_call(query_string):
        if not query_string.strip(): return None, None, None, None
        safe_query = urllib.parse.quote(query_string)
        url = f"https://ws.geonorge.no/adresser/v1/sok?sok={safe_query}&fuzzy=true&utkoordsys=25833&treffPerSide=1"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200 and resp.json().get('adresser'):
                hit = resp.json()['adresser'][0]
                nord = hit.get('representasjonspunkt', {}).get('nord')
                ost = hit.get('representasjonspunkt', {}).get('øst')
                kom = hit.get('kommunenavn', 'Ukjent')
                adr_tekst = hit.get('adressetekst', query_string)
                return adr_tekst, kom, nord, ost
        except Exception: pass
        return None, None, None, None

    queries = []
    if adr_clean:
        if kom_clean: queries.append(f"{adr_clean} {kom_clean}")
        queries.append(adr_clean) 
        base_num = re.sub(r'(\d+)[a-zA-Z]+', r'\1', adr_clean)
        if base_num != adr_clean: queries.append(base_num)
        street_only = re.sub(r'\d+.*', '', adr_clean).strip()
        if street_only: queries.append(street_only)

    for q in queries:
        adr_tekst, kom, nord, ost = api_call(q)
        if nord and ost:
            return f"✅ Lokasjon bekreftet for lastberegning: {adr_tekst}, {kom}. (UTM33: N {nord}, Ø {ost}).", nord, ost

    if kom_clean:
        safe_kom = urllib.parse.quote(kom_clean)
        url_sted = f"https://ws.geonorge.no/stedsnavn/v1/navn?sok={safe_kom}&utkoordsys=25833&treffPerSide=1"
        try:
            resp = requests.get(url_sted, timeout=5)
            if resp.status_code == 200 and resp.json().get('navn'):
                hit = resp.json()['navn'][0]
                nord = hit.get('representasjonspunkt', {}).get('nord')
                ost = hit.get('representasjonspunkt', {}).get('øst')
                stedsnavn = hit.get('stedsnavn', kom_clean)
                if nord and ost:
                    return f"⚠️ Bruker senter av {stedsnavn} (N {nord}, Ø {ost}) for fastsettelse av klimadatagrunnlag.", nord, ost
        except Exception: pass

    return "❌ Fant ingen treff i Kartverket. Appen fortsetter uten nøyaktig lokasjon.", None, None

def fetch_kartverket_flyfoto(nord, ost):
    try:
        if not nord or not ost: return None, "Mangler koordinater"
        min_x, max_x = float(ost) - 150, float(ost) + 150
        min_y, max_y = float(nord) - 150, float(nord) + 150
        
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36'}
        
        url_orto = f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
        url_topo_cache = f"https://cache.kartverket.no/topo/v1/wms?service=WMS&request=GetMap&version=1.1.1&layers=topo&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"

        try:
            r1 = requests.get(url_orto, headers=headers, timeout=6)
            if r1.status_code == 200 and len(r1.content) > 5000:
                img = Image.open(io.BytesIO(r1.content)).convert('RGB')
                return img, "Ortofoto (Flyfoto)"
        except Exception: pass

        try:
            r2 = requests.get(url_topo_cache, headers=headers, timeout=6)
            if r2.status_code == 200 and len(r2.content) > 5000:
                img = Image.open(io.BytesIO(r2.content)).convert('RGB')
                return img, "Topografisk Norgeskart"
        except Exception: pass

    except Exception: pass
    return None, "Serverfeil hos Kartverket"

# --- 3. BEREGNINGSMOTOR (SØYLENETT OG BÆRESYSTEM) ---
def generate_structural_grid(img, material):
    w, h = img.size
    draw_img = img.convert("RGBA")
    overlay = Image.new("RGBA", draw_img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    try: 
        font_large = ImageFont.truetype("arial.ttf", int(h/35))
        font_small = ImageFont.truetype("arial.ttf", int(h/50))
    except: 
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    gray_array = np.array(img.convert("L"))
    margin_x, margin_y = int(w * 0.1), int(h * 0.1)
    
    dark_y, dark_x = np.where(gray_array[margin_y:h-margin_y, margin_x:w-margin_x] < 230)
    
    if len(dark_x) > 100:
        min_x = np.min(dark_x) + margin_x
        max_x = np.max(dark_x) + margin_x
        min_y = np.min(dark_y) + margin_y
        max_y = np.max(dark_y) + margin_y
        
        # Definerer rutenett (Spennvidder basert på materiale)
        # Massivtre krever tettere søyler/bærevegger enn Stål/Betong
        spenn_deler = 4 if material == "Massivtre (CLT / Trekonstruksjon)" else 3
        
        step_x = (max_x - min_x) // spenn_deler
        step_y = (max_y - min_y) // spenn_deler
        
        # Tegner Hoveddragere (Blå linjer)
        for y in range(min_y, max_y + 1, step_y):
            draw.line([(min_x, y), (max_x, y)], fill=(0, 100, 200, 180), width=int(w/150))
            
        # Tegner Søylepunkter (Røde sirkler) i knutepunktene
        r = int(w/120)
        for x in range(min_x, max_x + 1, step_x):
            for y in range(min_y, max_y + 1, step_y):
                draw.ellipse([x-r, y-r, x+r, y+r], fill=(220, 20, 20, 255), outline=(100,0,0,255), width=2)

    # Info-boks
    box_w, box_h = int(w*0.38), int(h*0.22)
    draw.rectangle([w-box_w, h-box_h, w-10, h-10], fill=(255,255,255,240), outline="black", width=3)
    draw.text((w-box_w+20, h-box_h+20), "KONSEPTUELT BÆRESYSTEM", fill="black", font=font_large)
    draw.text((w-box_w+20, h-box_h+60), f"Hovedmateriale: {material}", fill=(100,100,100), font=font_small)
    
    # Tegnforklaring
    draw.ellipse([w-box_w+20, h-box_h+100, w-box_w+20+r*2, h-box_h+100+r*2], fill=(220, 20, 20, 255))
    draw.text((w-box_w+50, h-box_h+100), "= Bærende søyle / Knutepunkt", fill="black", font=font_small)
    
    draw.line([(w-box_w+20, h-box_h+135), (w-box_w+40, h-box_h+135)], fill=(0, 100, 200, 255), width=5)
    draw.text((w-box_w+50, h-box_h+130), "= Hoveddrager / Spennretning", fill="black", font=font_small)

    out = Image.alpha_composite(draw_img, overlay)
    return out.convert("RGB")

# --- 4. DYNAMISK PDF MOTOR (RIB) ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: RIB-001"), 0, 1, 'R')
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

def create_full_report_pdf(name, client, content, maps, aerial_photo, map_type):
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
    pdf.set_font('Helvetica', 'B', 24)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("KONSEPTUELL KONSTRUKSJONSANALYSE (RIB)"), 0, 1, 'L')
    pdf.set_x(25)
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"FORPROSJEKT: {pdf.p_name}"), 0, 1, 'L')
    pdf.ln(30)
    
    metadata = [
        ("OPPDRAGSGIVER:", client), 
        ("DATO:", datetime.now().strftime("%d. %m. %Y")), 
        ("UTARBEIDET AV:", "Builtly RIB AI Engine (Sivilingeniør)"), 
        ("KONTROLLERT AV:", "[Ansvarlig Konstruksjonstekniker]")
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
        "2. PROSJEKTBESKRIVELSE OG REGELVERK (TEK17 / EUROKODER)", 
        "3. LOKASJON OG MILJØLASTER (SNØ, VIND, SEISMIKK)", 
        "4. KONSEPT FOR HOVEDBÆRESYSTEM", 
        "5. FUNDAMENTERING OG AVSTIVNING (STABILITET)", 
        "6. MATERIALVALG, BÆREKRAFT OG KLIMAGASS", 
        "VEDLEGG: KONSEPTUELL SØYLE- OG SPENNPLAN"
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
            
        if line.startswith('# 3. LOKASJON'):
            pdf.check_space(180) 
            pdf.ln(8)
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.ln(4)
            
            if aerial_photo:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    aerial_photo.save(tmp.name, format="PNG")
                    img_h = 160 * (aerial_photo.height / aerial_photo.width)
                    pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                    pdf.set_y(pdf.get_y() + img_h + 5)
                    pdf.set_font('Helvetica', 'I', 9)
                    pdf.set_text_color(100, 100, 100)
                    pdf.set_x(25)
                    pdf.cell(0, 5, clean_pdf_text(f"Figur 1: Kartgrunnlag ({map_type}) for fastsettelse av klimadata (Snø/Vind/Seismikk)."), 0, 1, 'C')
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
        pdf.cell(0, 20, "VEDLEGG: KONSEPTUELL SØYLE- OG SPENNPLAN", 0, 1)
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
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: AI-generert konsept for bæresystem og rutenett."), 0, 1, 'C')
                
    return bytes(pdf.output(dest='S'))

# --- 5. STREAMLIT UI ---
st.title("🏗️ Builtly RIB AI (Konstruksjonsteknikk)")
st.info("Genererer konseptuell konstruksjonsanalyse med miljølaster (Eurokode), systemvalg og bærekraftvurdering.")

with st.expander("Prosjekt & Miljølaster (Lokasjon)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", "Saga Park")
    c_name = c2.text_input("Oppdragsgiver", "Saga Park AS")
    
    c3, c4 = st.columns(2)
    adresse = c3.text_input("Gatenavn og nummer", "Industriveien 1")
    kommune = c4.text_input("Kommune", "Trondheim")

with st.expander("Bygningsegenskaper & Materialvalg", expanded=True):
    col1, col2, col3 = st.columns(3)
    b_type = col1.selectbox("Bygningskategori (Konsekvensklasse)", ["CC2 (Bolig/Kontor under 4 etg)", "CC3 (Store bolig/næringsbygg, skoler)", "CC1 (Småhus, garasjer)"], index=1)
    etasjer = col2.number_input("Antall etasjer", value=4)
    bta = col3.number_input("Bruttoareal (BTA m2)", value=2500, step=100)
    
    materiale = st.radio("Ønsket hovedbæresystem (Dette påvirker spennvidder og klimagassregnskap):", 
                         ["Massivtre (CLT / Trekonstruksjon)", "Plass-støpt Betong", "Prefabrikkert Betong (Hulldekker)", "Stålkonstruksjon / Kompositt", "Hybrid (Stål/Tre/Betong)"], 
                         index=0)

files = st.file_uploader("Last opp arkitekttegninger for å generere søylenett-skisse (PDF/Bilder)", accept_multiple_files=True)

if st.button("GENERER KONSTRUKSJONSANALYSE (RIB)", type="primary"):
    
    kartverket_info = ""
    nord = ost = None
    aerial_photo = None
    map_type = "Ikke funnet"
    
    with st.spinner("🌍 Kobler til Geonorge API (Beregner lokasjon for Snø/Vind-laster)..."):
        kartverket_info, nord, ost = fetch_kartverket_data(adresse, kommune, "", "")
        if nord and ost:
            st.success(kartverket_info)
            aerial_photo, map_type = fetch_kartverket_flyfoto(nord, ost)
        else:
            st.warning(kartverket_info)
    
    processed_maps = []
    if files:
        with st.spinner("📐 Arkitekt-AI tegner konseptuelt søylenett..."):
            try:
                valid_image_files = [f for f in files if f.name.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg'))]
                for i in range(min(len(valid_image_files), 1)): 
                    f = valid_image_files[i]
                    if f.name.lower().endswith('pdf'):
                        if fitz is None: st.stop()
                        doc = fitz.open(stream=f.read(), filetype="pdf")
                        pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                        doc.close() 
                    else:
                        img = Image.open(f)
                    
                    m = generate_structural_grid(img, materiale)
                    processed_maps.append(m)
            except Exception as e:
                pass
                
    with st.spinner("🤖 Genererer omfattende RIB-rapport etter Eurokodesystemet..."):
        gyldige_modeller = []
        try:
            for mod in genai.list_models():
                if 'generateContent' in mod.supported_generation_methods:
                    gyldige_modeller.append(mod.name)
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        valgt_modell = gyldige_modeller[0]
        for favoritt in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if favoritt in gyldige_modeller:
                valgt_modell = favoritt
                break
        
        model = genai.GenerativeModel(valgt_modell)

        prompt = f"""
        Du er Builtly RIB AI, en senior sivilingeniør innen bygg- og konstruksjonsteknikk i Norge.
        Skriv et "Konseptuelt konstruksjonsdesign (Forprosjekt)" for et nytt byggeprosjekt.
        
        PROSJEKT: {p_name}
        LOKASJON: {adresse}, {kommune}. {kartverket_info}
        
        BYGNINGSDATA:
        - Konsekvensklasse/Pålitelighetsklasse: {b_type}
        - Antall etasjer: {etasjer} (BTA {bta} m2)
        - Foretrukket hovedbæresystem: {materiale}
        
        INSTRUKSER (Skriv formelt, teknisk tungt og presist, min 1500 ord):
        - Bruk Eurokodene (NS-EN 1990, 1991, 1992, 1993, 1995, 1998) som referanser.
        - Kap 3: Vurder miljølaster basert på kommunen (Er det typisk mye snø her? Kystnært og mye vindlaster (NS-EN 1991-1-4)? Fare for seismikk i dette fylket (NS-EN 1998)?).
        - Kap 4: Vurder {materiale} som bæresystem. Hva er fordelene og ulempene for dette spesifikke bygget? Hvordan løses knutepunkter og avstivning?
        - Kap 6: Diskuter bærekraft og klimagassregnskap. Argumenter for hvordan CO2-fotavtrykket påvirkes av materialvalget ({materiale}).
        
        STRUKTUR (Bruk KUN disse nøyaktige overskriftene):
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. PROSJEKTBESKRIVELSE OG REGELVERK (TEK17 / EUROKODER)
        # 3. LOKASJON OG MILJØLASTER (SNØ, VIND, SEISMIKK)
        # 4. KONSEPT FOR HOVEDBÆRESYSTEM
        # 5. FUNDAMENTERING OG AVSTIVNING (STABILITET)
        # 6. MATERIALVALG, BÆREKRAFT OG KLIMAGASS
        """
        
        try:
            res = model.generate_content(prompt)
            with st.spinner("Kompilerer konstruksjons-PDF..."):
                pdf_data = create_full_report_pdf(p_name, c_name, res.text, processed_maps, aerial_photo, map_type)
            
            st.success("✅ RIB Konstruksjonsanalyse er ferdigstilt!")
            st.download_button("📄 Last ned Builtly RIB-rapport", pdf_data, f"Builtly_RIB_{p_name}.pdf")
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")
