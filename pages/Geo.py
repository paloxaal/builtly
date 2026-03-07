import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
from datetime import datetime
import tempfile
import re
import requests
import io
from PIL import Image, ImageDraw, ImageFont
import numpy as np

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Miljø & Geo (RIG-M) | Builtly Portal", layout="wide")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()

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

# --- 3. KARTVERKET API (For miljøprøver og historikk) ---
def fetch_kartverket_data(gnr, bnr, kommune):
    k = kommune.replace(',', '').strip() if kommune else ""
    g = gnr.strip() if gnr else ""
    b = bnr.strip() if bnr else ""

    if g and b and k:
        try:
            resp = requests.get(f"https://ws.geonorge.no/adresser/v1/sok?gardsnummer={g}&bruksnummer={b}&kommunenavn={k}&utkoordsys=25833&treffPerSide=1", timeout=8)
            if resp.status_code == 200 and resp.json().get('adresser'):
                hit = resp.json()['adresser'][0]
                n = hit.get('representasjonspunkt', {}).get('nord')
                o = hit.get('representasjonspunkt', {}).get('øst')
                return f"✅ Gnr/Bnr bekreftet. Klar for miljøkartlegging.", n, o
        except: pass
    return "❌ Fant ikke nøyaktige koordinater for Gnr/Bnr. Kartlegging fortsetter med skjønn.", None, None

def fetch_and_draw_environmental_map(nord, ost):
    if not nord or not ost: return None, "Mangler koordinater"
    
    min_x, max_x = float(ost) - 100, float(ost) + 100
    min_y, max_y = float(nord) - 100, float(nord) + 100
    
    try:
        url = f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/jpeg"
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        if r.status_code == 200 and len(r.content) > 5000:
            base_img = Image.open(io.BytesIO(r.content)).convert('RGBA')
            
            w, h = base_img.size
            overlay = Image.new("RGBA", (w, h), (255, 255, 255, 0))
            draw = ImageDraw.Draw(overlay)
            
            try: font_large = ImageFont.truetype("arial.ttf", int(h/35))
            except: font_large = ImageFont.load_default()
            try: font_small = ImageFont.truetype("arial.ttf", int(h/50))
            except: font_small = ImageFont.load_default()
            
            # Tegner 3 tilfeldige "prøvepunkter" rundt senter av tomten
            points = [
                (w//2 - 50, h//2 - 20, "BP-1 (Tilstandsklasse 3)"),
                (w//2 + 60, h//2 + 40, "BP-2 (Tilstandsklasse 1)"),
                (w//2 - 10, h//2 + 80, "BP-3 (Tilstandsklasse 2)")
            ]
            
            for x, y, label in points:
                # Farge basert på tilstand (Rød for 3, Grønn for 1, Gul for 2)
                color = (239, 68, 68, 255) if "3" in label else (34, 197, 94, 255) if "1" in label else (250, 204, 21, 255)
                draw.ellipse([x-8, y-8, x+8, y+8], fill=color, outline=(255,255,255,255), width=2)
                draw.text((x+15, y-5), label, fill=(255,255,255,255), font=font_small)

            # Infoboks
            box_w, box_h = int(w*0.45), int(h*0.15)
            draw.rectangle([10, 10, 10+box_w, 10+box_h], fill=(15, 23, 42, 240), outline="#38bdf8", width=3)
            draw.text((30, 20), "PRØVETAKINGSPLAN (MILJØ)", fill="#f8fafc", font=font_large)
            draw.text((30, 60), "Posisjon: Borepunkter / Miljøsjakter", fill="#94a3b8", font=font_small)

            final_img = Image.alpha_composite(base_img, overlay).convert("RGB")
            return final_img, "Ortofoto m/ Prøvepunkter"
    except: pass
    
    return None, "Timeout på Kartverket API"

# --- 4. DYNAMISK PDF MOTOR (RIG-M) ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: RIGM-001"), 0, 1, 'R')
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

def create_full_report_pdf(name, client, content, geo_map, map_type):
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
    pdf.cell(0, 15, clean_pdf_text("TILTAKSPLAN FORURENSET GRUNN"), 0, 1, 'L')
    pdf.set_x(25)
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"PROSJEKT: {pdf.p_name}"), 0, 1, 'L')
    pdf.ln(30)
    
    metadata = [
        ("OPPDRAGSGIVER:", client), 
        ("DATO:", datetime.now().strftime("%d. %m. %Y")), 
        ("UTARBEIDET AV:", "Builtly RIG-M AI Engine"), 
        ("KONTROLLERT AV:", "[Ansvarlig Miljøingeniør]")
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
        "1. SAMMENDRAG", 
        "2. INNLEDNING OG EIENDOMSFORHOLD", 
        "3. HISTORISK BRUK AV TOMTEN", 
        "4. RESULTATER FRA GRUNNUNDERSØKELSER", 
        "5. VURDERING AV HELSE- OG MILJØRISIKO", 
        "6. TILTAKSPLAN OG KRAV TIL ENTREPRENØR",
        "VEDLEGG: KART OVER PRØVEPUNKTER"
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

    if geo_map:
        pdf.add_page()
        pdf.set_x(25)
        pdf.set_font('Helvetica', 'B', 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 20, "VEDLEGG: KART OVER PRØVEPUNKTER", 0, 1)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            geo_map.save(tmp.name)
            img_h = 160 * (geo_map.height / geo_map.width)
            pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
            pdf.set_y(pdf.get_y() + img_h + 5)
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'I', 10)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(0, 10, clean_pdf_text(f"Figur V-1: AI-estimert prøvetakingsplan ({map_type})."), 0, 1, 'C')
                
    return bytes(pdf.output(dest='S'))

# --- 5. STREAMLIT UI ---
st.title("🌍 RIG-M — Miljø & Geo")

if pd_state["p_name"]:
    st.success("✅ Eiendomsdata er automatisk synkronisert fra Single Source of Truth (SSOT).")

with st.expander("1. Eiendom & Lokasjon (Auto-synced)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state["p_name"])
    c_name = c2.text_input("Oppdragsgiver", value=pd_state["c_name"])
    
    st.markdown("##### Eiendomsidentifikasjon (Kartverket)")
    c3, c4, c5 = st.columns(3)
    kommune = c3.text_input("Kommune", value=pd_state["kommune"])
    gnr = c4.text_input("Gnr", value=pd_state["gnr"])
    bnr = c5.text_input("Bnr", value=pd_state["bnr"])

with st.expander("2. Grunnforhold & Analyseresultater", expanded=True):
    st.info("💡 Her vil portalen analysere opplastede lab-rapporter fra Eurofins / ALS Laboratory.")
    st.file_uploader("Last opp excel-fil med lab-analyser (Valgfritt)", type=['xlsx', 'csv'])
    
    tilstand = st.selectbox("Høyeste detekterte tilstandsklasse i prøvene:", ["Tilstandsklasse 1 (Rene masser)", "Tilstandsklasse 2-3 (Moderat forurenset, krever deponi)", "Tilstandsklasse 4-5 (Farlig avfall)"], index=1)

if st.button("Kjør RIG-M Analyse & Tiltaksplan", type="primary"):
    
    kartverket_info = ""
    nord = ost = None
    geo_map = None
    map_type = "Kart ikke tilgjengelig"
    
    with st.spinner("🌍 Slår opp eiendomsgrenser i Matrikkelen..."):
        kartverket_info, nord, ost = fetch_kartverket_data(gnr, bnr, kommune)
        if nord and ost:
            st.success(kartverket_info)
            geo_map, map_type = fetch_and_draw_environmental_map(nord, ost)
        else:
            st.warning(kartverket_info)
                
    with st.spinner("🤖 Skriver Tiltaksplan for forurenset grunn (Kap 2)..."):
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
        Du er Builtly RIG-M AI, en senior miljørådgiver og geotekniker.
        Skriv en formell "Tiltaksplan for forurenset grunn" for:
        
        PROSJEKT: {p_name}. 
        LOKASJON: Gnr {gnr}/ Bnr {bnr}, {kommune}.
        
        KUNDENS PROSJEKTBESKRIVELSE (Viktig for å forstå graveomfanget): 
        "{pd_state['p_desc']}"
        
        LAB-ANALYSE RESULTAT: Massene er vurdert til {tilstand}.
        
        INSTRUKSER (Skriv formelt, teknisk tungt og presist, min 1200 ord):
        - Skriv teknisk iht. Forurensningsforskriften kapittel 2.
        - Siden massene er {tilstand}, gi klare instrukser til graveentreprenør om håndtering, transport og deponering.
        
        STRUKTUR (Bruk KUN disse nøyaktige overskriftene):
        # 1. SAMMENDRAG
        # 2. INNLEDNING OG EIENDOMSFORHOLD
        # 3. HISTORISK BRUK AV TOMTEN
        # 4. RESULTATER FRA GRUNNUNDERSØKELSER
        # 5. VURDERING AV HELSE- OG MILJØRISIKO
        # 6. TILTAKSPLAN OG KRAV TIL ENTREPRENØR
        """
        
        try:
            res = model.generate_content(prompt)
            with st.spinner("Kompilerer Miljø-PDF..."):
                pdf_data = create_full_report_pdf(p_name, c_name, res.text, geo_map, map_type)
            
            st.success("✅ RIG-M Rapport er ferdigstilt!")
            st.download_button("📄 Last ned Tiltaksplan", pdf_data, f"Builtly_RIGM_{p_name}.pdf")
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")
