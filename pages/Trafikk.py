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

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Trafikk & Mobilitet (RIT) | Builtly Portal", layout="wide")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key: genai.configure(api_key=google_key)
else: st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render."); st.stop()

try: import fitz
except ImportError: fitz = None

def clean_pdf_text(text):
    if not text: return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*"}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{3,}', ' ', text)
    return clean_pdf_text(text)

# --- 2. HENT DATA FRA SSOT (Project Setup) ---
if "project_data" in st.session_state:
    pd_state = st.session_state.project_data
else:
    # Hvis brukeren går direkte til Trafikk uten å ha satt opp prosjektet først
    pd_state = {
        "p_name": "", "c_name": "", "p_desc": "",
        "adresse": "", "kommune": "", "gnr": "", "bnr": "",
        "b_type": "Næring / Kontor", "etasjer": 4, "bta": 2500
    }

# --- KARTVERKET API ---
def fetch_kartverket_data(adresse, kommune):
    adr = adresse.replace(',', '').strip() if adresse else ""
    kom = kommune.replace(',', '').strip() if kommune else ""
    if adr and kom:
        try:
            resp = requests.get(f"https://ws.geonorge.no/adresser/v1/sok?sok={adr}&kommunenavn={kom}&utkoordsys=25833&treffPerSide=1", timeout=8)
            if resp.status_code == 200 and resp.json().get('adresser'):
                hit = resp.json()['adresser'][0]
                n = hit.get('representasjonspunkt', {}).get('nord')
                o = hit.get('representasjonspunkt', {}).get('øst')
                return f"✅ Lokasjon bekreftet for trafikkvurdering: {adr}, {kom}.", n, o
        except: pass
    return "❌ Fant ingen treff for lokasjon. Analysen fortsetter med generiske data.", None, None

def fetch_kartverket_flyfoto(nord, ost):
    if not nord or not ost: return None, "Mangler koordinater"
    min_x, max_x, min_y, max_y = float(ost) - 150, float(ost) + 150, float(nord) - 150, float(nord) + 150
    try:
        r = requests.get(f"https://opencache.statkart.no/gatekeeper/gk/gk.open_wms?service=WMS&request=GetMap&version=1.1.1&layers=norges_grunnkart&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png", timeout=8)
        if r.status_code == 200: return Image.open(io.BytesIO(r.content)).convert('RGB'), "Norges Grunnkart (Veinett)"
    except: pass
    return None, "Timeout på Kartverket API"

# --- KONSEPTUELL TRAFIKKTEGNER ---
def generate_traffic_diagram(img):
    w, h = img.size
    draw_img = img.convert("RGBA")
    overlay = Image.new("RGBA", draw_img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    margin = int(w * 0.15)
    
    draw.line([(0, h-margin), (w, h-margin)], fill=(100, 100, 100, 150), width=int(h/15))
    draw.line([(w/2, h-margin), (w/2, margin)], fill=(56, 189, 248, 200), width=int(h/30))
    draw.polygon([(w/2, margin), (w/2 - 15, margin + 20), (w/2 + 15, margin + 20)], fill=(56, 189, 248, 255))
    draw.line([(w/2, h-margin), (margin, h-margin)], fill=(250, 204, 21, 200), width=4)
    draw.line([(w/2, h-margin), (w-margin, h-margin)], fill=(250, 204, 21, 200), width=4)
    
    box_w, box_h = int(w*0.45), int(h*0.2)
    draw.rectangle([w-box_w, 10, w-10, 10+box_h], fill=(15, 23, 42, 240), outline="#38bdf8", width=3)
    try: font_large = ImageFont.truetype("arial.ttf", int(h/35)); font_small = ImageFont.truetype("arial.ttf", int(h/50))
    except: font_large = font_small = ImageFont.load_default()
    draw.text((w-box_w+20, 20), "KONSEPTUELL TRAFIKKAVIKLING", fill="#f8fafc", font=font_large)
    draw.line([(w-box_w+20, 65), (w-box_w+50, 65)], fill=(56, 189, 248, 255), width=4)
    draw.text((w-box_w+60, 55), "= Kjøreadkomst", fill="#e2e8f0", font=font_small)
    draw.line([(w-box_w+20, 95), (w-box_w+50, 95)], fill=(250, 204, 21, 255), width=4)
    draw.text((w-box_w+60, 85), "= Siktlinjer (N100)", fill="#e2e8f0", font=font_small)

    return Image.alpha_composite(draw_img, overlay).convert("RGB")

# --- PDF MOTOR ---
class BuiltlyProPDF(FPDF):
    def footer(self):
        self.set_y(-15); self.set_font('Helvetica', 'I', 8); self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f'UTKAST - KREVER FAGLIG KONTROLL | Side {self.page_no()}'), 0, 0, 'C')

def create_full_report_pdf(name, client, content, maps, aerial_photo, map_type):
    pdf = BuiltlyProPDF()
    pdf.set_margins(25, 25, 25); pdf.set_auto_page_break(True, 25); pdf.add_page()
    if os.path.exists("logo.png"): pdf.image("logo.png", x=25, y=20, w=50)
    
    pdf.set_y(100); pdf.set_font('Helvetica', 'B', 24); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("TRAFIKKNOTAT & MOBILITETSPLAN (RIT)"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 16); pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"PROSJEKT: {name.upper()}"), 0, 1, 'L'); pdf.ln(30)
    
    for l, v in [("OPPDRAGSGIVER:", client), ("DATO:", datetime.now().strftime("%d. %m. %Y")), ("UTARBEIDET AV:", "Builtly RIT AI Engine")]:
        pdf.set_font('Helvetica', 'B', 10); pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10); pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: pdf.ln(4); continue
        if line.startswith('# '):
            pdf.ln(8); pdf.set_font('Helvetica', 'B', 14); pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip())); pdf.ln(2)
        elif line.startswith('##'):
            pdf.ln(6); pdf.set_font('Helvetica', 'B', 12); pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
        else:
            pdf.set_font('Helvetica', '', 10); pdf.set_text_color(0, 0, 0)
            pdf.multi_cell(150, 5, ironclad_text_formatter(line))
            
        if line.startswith('# 2.') and aerial_photo:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                aerial_photo.save(tmp.name, format="PNG")
                img_w = 120; x_center = (210 - img_w) / 2
                pdf.image(tmp.name, x=x_center, y=pdf.get_y()+5, w=img_w)
                pdf.set_y(pdf.get_y() + img_w * (aerial_photo.height / aerial_photo.width) + 10)

    if maps:
        pdf.add_page(); pdf.set_font('Helvetica', 'B', 16); pdf.cell(0, 20, "VEDLEGG: KONSEPTUELL TRAFIKKAVIKLING", 0, 1)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            maps[0].save(tmp.name)
            pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
            
    return bytes(pdf.output(dest='S'))

# --- 5. STREAMLIT UI ---
st.title("🚦 RIT — Trafikk & Mobilitet")

if pd_state["p_name"]:
    st.success("✅ Prosjektdata er automatisk synkronisert fra Single Source of Truth (SSOT).")

with st.expander("1. Prosjektinformasjon (Hentes fra SSOT)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state["p_name"])
    c_name = c2.text_input("Oppdragsgiver", value=pd_state["c_name"])
    c3, c4 = st.columns(2)
    adresse = c3.text_input("Gatenavn og nummer", value=pd_state["adresse"])
    kommune = c4.text_input("Kommune", value=pd_state["kommune"])

with st.expander("2. Trafikkgrunnlag & Dimensjonering (Hentes fra SSOT)", expanded=True):
    col1, col2, col3 = st.columns(3)
    type_options = ["Bolig (Blokk/Rekkehus)", "Næring / Kontor", "Handel / Kjøpesenter", "Offentlig / Skole"]
    try: idx = type_options.index(pd_state["b_type"])
    except: idx = 1
    b_type = col1.selectbox("Hovedformål", type_options, index=idx)
    enheter = col2.number_input("Antall boenheter / ansatte (Estimat basert på BTA)", value=int(pd_state["bta"]/50) if pd_state["bta"] else 40)
    bta = col3.number_input("Bruttoareal (BTA m2)", value=int(pd_state["bta"]), step=100)
    
    st.markdown("##### Kommunens Parkeringsnorm")
    p1, p2 = st.columns(2)
    p_norm_bil = p1.number_input("Krav til bilparkering (f.eks. plasser pr. enhet)", value=1.0, step=0.1)
    p_norm_sykkel = p2.number_input("Krav til sykkelparkering (plasser pr. enhet)", value=2.0, step=0.5)

with st.expander("3. Situasjonsplan (For siktlinjer)", expanded=True):
    files = st.file_uploader("Last opp plan for adkomstskisse", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

if st.button("Kjør Trafikkanalyse (RIT)", type="primary"):
    
    kartverket_info = ""
    nord = ost = None
    aerial_photo = None
    map_type = "Kart ikke tilgjengelig"
    
    with st.spinner("🌍 Slår opp lokasjon for veinett..."):
        kartverket_info, nord, ost = fetch_kartverket_data(adresse, kommune)
        if nord and ost: aerial_photo, map_type = fetch_kartverket_flyfoto(nord, ost)
    
    processed_maps = []
    if files:
        with st.spinner("📐 Tegner siktlinjer..."):
            try:
                for f in files[:1]: 
                    if f.name.lower().endswith('pdf'):
                        if fitz is None: st.stop()
                        doc = fitz.open(stream=f.read(), filetype="pdf")
                        pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                    else: img = Image.open(f)
                    processed_maps.append(generate_traffic_diagram(img))
            except: pass
                
    with st.spinner("🤖 Skriver profesjonelt Trafikknotat..."):
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
        Du er Builtly RIT AI, en senior trafikkkonsulent og veiplanlegger.
        Skriv et "Trafikknotat og Mobilitetsplan" for et nytt prosjekt.
        
        PROSJEKT: {p_name} ({b_type}, {bta} m2)
        LOKASJON: {adresse}, {kommune}. {kartverket_info}
        
        KUNDENS EGEN BESKRIVELSE AV PROSJEKTET (SVÆRT VIKTIG KONTEKST):
        "{pd_state['p_desc']}"
        
        INSTRUKSER:
        - Bruk Håndbok N100 for adkomst og sikt.
        - Beregn trafikkgenerering og parkeringsbehov ({p_norm_bil} bil / {p_norm_sykkel} sykkel pr enhet).
        
        STRUKTUR:
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. PROSJEKTBESKRIVELSE OG LOKASJON
        # 3. TRAFIKKGENERERING OG KAPASITET
        # 4. PARKERINGSDEKNING (BIL OG SYKKEL)
        # 5. ADKOMST, SIKTLINJER OG VARELEVERING
        # 6. MOBILITET OG MYKE TRAFIKANTER
        """
        
        try:
            res = model.generate_content(prompt)
            pdf_data = create_full_report_pdf(p_name, c_name, res.text, processed_maps, aerial_photo, map_type)
            st.success("✅ RIT Trafikknotat er ferdigstilt!")
            st.download_button("📄 Last ned Trafikk-rapport", pdf_data, f"Builtly_RIT_{p_name}.pdf")
        except Exception as e: st.error(f"Feil: {e}")
