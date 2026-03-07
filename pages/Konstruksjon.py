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

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Konstruksjonsteknikk (RIB) | Builtly Portal", layout="wide")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key: 
    genai.configure(api_key=google_key)
else: 
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()

try: 
    import fitz # PyMuPDF for å lese PDF-tegninger
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

# --- 2. HENT DATA FRA SSOT (Project Setup) ---
# Dette er magien som gjør at feltene fyller seg ut selv!
if "project_data" in st.session_state:
    pd_state = st.session_state.project_data
else:
    # Sikkerhetsnett hvis kunden hopper rett inn i RIB uten å gå via Project Setup
    pd_state = {
        "p_name": "", "c_name": "", "p_desc": "",
        "adresse": "", "kommune": "", "gnr": "", "bnr": "",
        "b_type": "Næring / Kontor", "etasjer": 4, "bta": 2500
    }

# --- 3. KARTVERKET API (For klimadata som snø og vind) ---
def fetch_kartverket_data(adresse, kommune, gnr, bnr):
    adr = adresse.replace(',', '').strip() if adresse else ""
    kom = kommune.replace(',', '').strip() if kommune else ""
    g = gnr.strip() if gnr else ""
    b = bnr.strip() if bnr else ""

    def make_request(params):
        params['utkoordsys'] = '25833'
        params['treffPerSide'] = 1
        try:
            resp = requests.get("https://ws.geonorge.no/adresser/v1/sok", params=params, timeout=8)
            if resp.status_code == 200 and resp.json().get('adresser'):
                hit = resp.json()['adresser'][0]
                return hit.get('representasjonspunkt', {}).get('nord'), hit.get('representasjonspunkt', {}).get('øst'), hit.get('adressetekst', ''), hit.get('kommunenavn', '')
        except: pass
        return None, None, None, None

    if g and b and kom:
        n, o, a, k = make_request({'gardsnummer': g, 'bruksnummer': b, 'kommunenavn': kom})
        if n and o: return f"✅ Lokasjon bekreftet (Gnr/Bnr). Brukes til å bestemme snø- og vindlaster i Eurokode.", n, o

    if adr and kom:
        n, o, a, k = make_request({'sok': adr, 'kommunenavn': kom, 'fuzzy': 'true'})
        if n and o: return f"✅ Lokasjon bekreftet ({a}, {k}). Brukes til å bestemme snø- og vindlaster i Eurokode.", n, o

    return "❌ Fant ingen nøyaktig lokasjon. Standard nasjonale laster vil bli brukt.", None, None

def fetch_kartverket_flyfoto(nord, ost):
    if not nord or not ost: return None, ""
    min_x, max_x = float(ost) - 150, float(ost) + 150
    min_y, max_y = float(nord) - 150, float(nord) + 150
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        url = f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/jpeg"
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200 and len(r.content) > 5000:
            return Image.open(io.BytesIO(r.content)).convert('RGB'), "Ortofoto"
    except: pass
    return None, ""

# --- 4. AI SØYLENETT TEGNER ---
def generate_structural_grid(img, material):
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
    
    dark_y, dark_x = np.where(gray_array[margin:h-margin, margin:w-margin] < 230)
    
    if len(dark_x) > 100:
        min_x = np.min(dark_x) + margin
        max_x = np.max(dark_x) + margin
        min_y = np.min(dark_y) + margin
        max_y = np.max(dark_y) + margin
        
        spenn_deler = 4 if material == "Massivtre (CLT / Trekonstruksjon)" else 3
        step_x = (max_x - min_x) // spenn_deler
        step_y = (max_y - min_y) // spenn_deler
        
        for y in range(min_y, max_y + 1, step_y):
            draw.line([(min_x, y), (max_x, y)], fill=(56, 189, 248, 180), width=int(w/150))
            
        r = int(w/120)
        for x in range(min_x, max_x + 1, step_x):
            for y in range(min_y, max_y + 1, step_y):
                draw.ellipse([x-r, y-r, x+r, y+r], fill=(239, 68, 68, 255), outline=(153,27,27,255), width=2)

    box_w, box_h = int(w*0.4), int(h*0.22)
    draw.rectangle([w-box_w, h-box_h, w-10, h-10], fill=(15, 23, 42, 240), outline="#38bdf8", width=3)
    draw.text((w-box_w+20, h-box_h+20), "KONSEPTUELT BÆRESYSTEM", fill="#f8fafc", font=font_large)
    draw.text((w-box_w+20, h-box_h+60), f"Hovedmateriale: {material}", fill="#94a3b8", font=font_small)
    
    draw.ellipse([w-box_w+20, h-box_h+100, w-box_w+20+r*2, h-box_h+100+r*2], fill=(239, 68, 68, 255))
    draw.text((w-box_w+50, h-box_h+100), "= Bærende søyle / Knutepunkt", fill="#e2e8f0", font=font_small)

    out = Image.alpha_composite(draw_img, overlay)
    return out.convert("RGB")

# --- 5. DYNAMISK PDF MOTOR (RIB) ---
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
        ("UTARBEIDET AV:", "Builtly RIB AI Engine"), 
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
        "2. PROSJEKTBESKRIVELSE OG REGELVERK", 
        "3. LOKASJON OG MILJØLASTER", 
        "4. KONSEPT FOR HOVEDBÆRESYSTEM", 
        "5. FUNDAMENTERING OG AVSTIVNING", 
        "6. MATERIALVALG OG KLIMAGASS", 
        "VEDLEGG: KONSEPTUELL SØYLEPLAN"
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
            pdf.check_space(120) 
            pdf.ln(8)
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.ln(4)
            
            if aerial_photo:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    aerial_photo.save(tmp.name, format="PNG")
                    img_w = 120
                    img_h = img_w * (aerial_photo.height / aerial_photo.width)
                    x_center = (210 - img_w) / 2
                    pdf.image(tmp.name, x=x_center, y=pdf.get_y(), w=img_w)
                    pdf.set_y(pdf.get_y() + img_h + 5)
                    pdf.set_font('Helvetica', 'I', 9)
                    pdf.set_text_color(100, 100, 100)
                    pdf.set_x(25)
                    pdf.cell(0, 5, clean_pdf_text("Figur 1: Kartgrunnlag for snø- og vindlaster."), 0, 1, 'C')
                    pdf.ln(8)
            pdf.set_font('Helvetica', '', 10)
            pdf.set_text_color(0, 0, 0)
            continue
            
        elif line.startswith('# '):
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
        pdf.cell(0, 20, "VEDLEGG: KONSEPTUELL SØYLEPLAN", 0, 1)
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
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: AI-generert grid- og søyleplan."), 0, 1, 'C')
                
    return bytes(pdf.output(dest='S'))

# --- 6. STREAMLIT UI ---
st.title("🏗️ RIB — Konstruksjon (AI-Assisted)")

if pd_state["p_name"]:
    st.success("✅ Prosjektdata er automatisk synkronisert fra Master Setup (SSOT).")

with st.expander("1. Prosjekt & Lokasjon (Auto-synced)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state["p_name"])
    c_name = c2.text_input("Oppdragsgiver", value=pd_state["c_name"])
    
    st.markdown("##### Kartverket Lokasjon (For Eurokode Laster)")
    c3, c4 = st.columns(2)
    adresse = c3.text_input("Gatenavn og nummer", value=pd_state["adresse"])
    kommune = c4.text_input("Kommune", value=pd_state["kommune"])
    
    c5, c6 = st.columns(2)
    gnr = c5.text_input("Gårdsnummer (Gnr)", value=pd_state["gnr"])
    bnr = c6.text_input("Bruksnummer (Bnr)", value=pd_state["bnr"])

with st.expander("2. Bygningsegenskaper (Auto-synced)", expanded=True):
    col1, col2, col3 = st.columns(3)
    b_type = col1.selectbox("Konsekvensklasse (CC)", ["CC1 (Småhus)", "CC2 (Vanlige bygg under 4 etg)", "CC3 (Store bygg)"], index=1)
    etasjer = col2.number_input("Antall etasjer", value=int(pd_state["etasjer"]))
    bta = col3.number_input("Bruttoareal (BTA m2)", value=int(pd_state["bta"]))
    
    materiale = st.radio("Foretrukket hovedbæresystem:", ["Massivtre (CLT / Trekonstruksjon)", "Plass-støpt Betong", "Stålkonstruksjon / Kompositt"], index=0)

with st.expander("3. Arkitektur & Plantegninger", expanded=True):
    files = st.file_uploader("Last opp arkitekttegninger for å generere søylenett-skisse (PDF/Bilder)", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

if st.button("Kjør Konstruksjonsanalyse (RIB)", type="primary"):
    
    kartverket_info = ""
    nord = ost = None
    aerial_photo = None
    map_type = "Kart ikke tilgjengelig"
    
    with st.spinner("🌍 Slår opp lokasjon for klimadata..."):
        kartverket_info, nord, ost = fetch_kartverket_data(adresse, kommune, gnr, bnr)
        if nord and ost:
            st.success(kartverket_info)
            aerial_photo, map_type = fetch_kartverket_flyfoto(nord, ost)
        else:
            st.warning(kartverket_info)
    
    processed_maps = []
    if files:
        with st.spinner("📐 Arkitekt-AI tegner konseptuelt søylenett..."):
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
                    
                    m = generate_structural_grid(img, materiale)
                    processed_maps.append(m)
            except Exception as e:
                pass
                
    with st.spinner("🤖 Genererer omfattende RIB-rapport etter Eurokodesystemet..."):
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
        Du er Builtly RIB AI, en senior sivilingeniør innen bygg- og konstruksjonsteknikk.
        Skriv et "Konseptuelt konstruksjonsdesign (Forprosjekt)" for et nytt byggeprosjekt.
        
        PROSJEKT: {p_name}
        LOKASJON: {adresse}, {kommune}. {kartverket_info}
        
        KUNDENS PROSJEKTBESKRIVELSE (KONTEKST):
        "{pd_state['p_desc']}"
        
        BYGNINGSDATA:
        - Konsekvensklasse: {b_type}
        - Antall etasjer: {etasjer} (BTA {bta} m2)
        - Foretrukket hovedbæresystem: {materiale}
        
        INSTRUKSER (Skriv formelt, teknisk tungt og presist, min 1200 ord):
        - Bruk Eurokodene (NS-EN) som referanser.
        - Kap 3: Vurder miljølaster basert på kommunen (snø, vind, seismikk).
        - Kap 4: Vurder {materiale} som bæresystem. Hva er fordelene og ulempene for dette bygget?
        - Kap 6: Diskuter bærekraft og klimagassregnskap knyttet til materialvalget.
        
        STRUKTUR (Bruk KUN disse nøyaktige overskriftene):
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. PROSJEKTBESKRIVELSE OG REGELVERK
        # 3. LOKASJON OG MILJØLASTER
        # 4. KONSEPT FOR HOVEDBÆRESYSTEM
        # 5. FUNDAMENTERING OG AVSTIVNING
        # 6. MATERIALVALG OG KLIMAGASS
        """
        
        try:
            res = model.generate_content(prompt)
            with st.spinner("Kompilerer konstruksjons-PDF..."):
                pdf_data = create_full_report_pdf(p_name, c_name, res.text, processed_maps, aerial_photo, map_type)
            
            st.success("✅ RIB Konstruksjonsanalyse er ferdigstilt!")
            st.download_button("📄 Last ned Builtly RIB-rapport", pdf_data, f"Builtly_RIB_{p_name}.pdf")
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")
