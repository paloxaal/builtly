import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
import base64
from datetime import datetime
import tempfile
import re
import io
import requests
import urllib.parse
from PIL import Image
import numpy as np
from pathlib import Path

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Brannkonsept (RIBr) | Builtly", layout="wide", initial_sidebar_state="collapsed")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()

try:
    import fitz  
except ImportError:
    fitz = None

def render_html(html_string: str):
    st.markdown(html_string.replace('\n', ' '), unsafe_allow_html=True)

def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            suffix = Path(candidate).suffix.lower().replace(".", "") or "png"
            with open(candidate, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/{suffix};base64,{encoded}"
    return ""

def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{name}.py")
        if p.exists(): return str(p)
    return ""

def clean_pdf_text(text):
    if not text: return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*"}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{3,}', ' ', text)
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    return clean_pdf_text(text)

# --- 2. KARTVERKET + GOOGLE MAPS FALLBACK MOTOR ---
def fetch_map_image(adresse, kommune, gnr, bnr, api_key):
    """Prøver Kartverket først. Hvis det feiler, bruker vi Google Maps Static API."""
    nord, ost = None, None
    adr_clean = adresse.replace(',', '').strip() if adresse else ""
    kom_clean = kommune.replace(',', '').strip() if kommune else ""
    
    # 1. Prøv Kartverket (Finn koordinater)
    queries = []
    if adr_clean and kom_clean: queries.append(f"{adr_clean} {kom_clean}")
    if adr_clean: queries.append(adr_clean)
    if gnr and bnr and kom_clean: queries.append(f"{kom_clean} {gnr}/{bnr}")
    
    for q in queries:
        safe_query = urllib.parse.quote(q)
        url = f"https://ws.geonorge.no/adresser/v1/sok?sok={safe_query}&fuzzy=true&utkoordsys=25833&treffPerSide=1"
        try:
            resp = requests.get(url, timeout=4)
            if resp.status_code == 200 and resp.json().get('adresser'):
                hit = resp.json()['adresser'][0]
                nord = hit.get('representasjonspunkt', {}).get('nord')
                ost = hit.get('representasjonspunkt', {}).get('øst')
                break
        except: pass
        
    # 1B. Hent bilde fra Kartverket WMS
    if nord and ost:
        min_x, max_x = float(ost) - 150, float(ost) + 150
        min_y, max_y = float(nord) - 150, float(nord) + 150
        url_orto = f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
        try:
            r1 = requests.get(url_orto, timeout=5)
            if r1.status_code == 200 and len(r1.content) > 5000:
                return Image.open(io.BytesIO(r1.content)).convert('RGB'), "Kartverket (Norge i Bilder)"
        except: pass
        
    # 2. FALLBACK TIL GOOGLE MAPS SATELLITE
    if api_key and (adr_clean or kom_clean):
        query = f"{adr_clean}, {kom_clean}, Norway"
        safe_query = urllib.parse.quote(query)
        # Henter et 600x600 satellittbilde
        url_gmaps = f"https://maps.googleapis.com/maps/api/staticmap?center={safe_query}&zoom=19&size=600x600&maptype=satellite&key={api_key}"
        try:
            r2 = requests.get(url_gmaps, timeout=5)
            if r2.status_code == 200:
                return Image.open(io.BytesIO(r2.content)).convert('RGB'), "Google Maps Satellite"
        except: pass
        
    return None, "Kunne ikke hente kart fra verken Kartverket eller Google."

# --- 3. PREMIUM CSS (MED FIKS FOR HVITE EXPANDERS) ---
st.markdown("""
<style>
    :root {
        --bg: #06111a; --panel: rgba(10, 22, 35, 0.78);
        --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38bdf8; --radius-lg: 16px; --radius-xl: 24px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }
    
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }

    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }
    .brand-left { display: flex; align-items: center; gap: 0.9rem; min-width: 0; }
    .topbar-right { display: flex; align-items: center; justify-content: flex-end; gap: 0.65rem; padding: 0.35rem; border-radius: 18px; background: rgba(255,255,255,0.025); border: 1px solid rgba(120,145,170,0.12); flex-wrap: nowrap !important; }
    .top-link { display: inline-flex; align-items: center; justify-content: center; min-height: 42px; padding: 0.72rem 1.2rem; border-radius: 12px; text-decoration: none !important; font-weight: 650; font-size: 0.93rem; transition: all 0.2s ease; border: 1px solid transparent; white-space: nowrap; }
    .top-link.ghost { color: var(--soft) !important; background: rgba(255,255,255,0.04); border-color: rgba(120,145,170,0.18); }
    .top-link.ghost:hover { color: #ffffff !important; border-color: rgba(56,194,201,0.38); background: rgba(255,255,255,0.06); }
    
    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important;}

    .stTextInput input, .stNumberInput input, .stTextArea textarea { background-color: #0d1824 !important; color: #ffffff !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus { border-color: #38bdf8 !important; box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.5) !important; }
    div[data-baseweb="select"] > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    div[data-baseweb="select"] span { color: #ffffff !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label { color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important; }
    
    /* --- DEN NYE AGGRESSIVE FIKSEN FOR HVITE EXPANDER-BOKSER --- */
    div[data-testid="stExpander"] details, 
    div[data-testid="stExpander"] details summary, 
    div[data-testid="stExpander"] { 
        background-color: #0c1520 !important; 
        color: #f5f7fb !important; 
        border-radius: 12px !important; 
    }
    div[data-testid="stExpander"] details summary:hover { background-color: rgba(255,255,255,0.03) !important; }
    div[data-testid="stExpander"] details summary p { color: #f5f7fb !important; font-weight: 650 !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; margin-bottom: 1rem !important; }
    div[data-testid="stExpanderDetails"] { background: transparent !important; color: #f5f7fb !important; }
    div[data-testid="stExpanderDetails"] > div > div > div { background-color: transparent !important; }
    /* ----------------------------------------------------------- */
    
    [data-testid="stFileUploaderDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important; border-radius: 12px !important; padding: 2rem !important; }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #38c2c9 !important; background-color: rgba(56, 194, 201, 0.05) !important; }
    [data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
    [data-testid="stFileUploaderFileData"] { background-color: rgba(255,255,255,0.02) !important; color: #f5f7fb !important; border-radius: 8px !important;}
    
    [data-testid="stAlert"] { background-color: rgba(56, 189, 248, 0.05) !important; border: 1px solid rgba(56, 189, 248, 0.2) !important; border-radius: 12px !important; }
    [data-testid="stAlert"] * { color: #f5f7fb !important; }
</style>
""", unsafe_allow_html=True)

# Sikkerhetsnett for minnet
if "project_data" not in st.session_state:
    st.session_state.project_data = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": "", "b_type": "Næring", "etasjer": 1, "bta": 0, "land": "Norge"}
if "brann_kart" not in st.session_state:
    st.session_state.brann_kart = None
if "brann_kart_kilde" not in st.session_state:
    st.session_state.brann_kart_kilde = None

# --- 4. GUARDRAIL LÅS ---
if st.session_state.project_data.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(f"<div style='margin-bottom:2rem;'>{logo_html}</div>")
    
    st.warning("⚠️ **Handling kreves:** Du må sette opp prosjektdataen før du kan bruke denne modulen.")
    st.info("AI-agenten trenger kontekst om bygget for å kunne generere en faglig og juridisk korrekt rapport.")
    
    if find_page("Project"):
        if st.button("⚙️ Gå til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()

# --- 5. HEADER ---
top_l, top_r = st.columns([4, 1])
with top_l:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(logo_html)
with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("← Tilbake til SSOT", use_container_width=True, type="secondary"):
        st.switch_page(find_page("Project"))

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)

pd_state = st.session_state.project_data

# --- DYNAMISK PDF MOTOR ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15); self.set_font('Helvetica', 'B', 10); self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: RIBr-001"), 0, 1, 'R')
            self.set_draw_color(200, 200, 200); self.line(25, 25, 185, 25); self.set_y(30)
    def footer(self):
        self.set_y(-15); self.set_font('Helvetica', 'I', 8); self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f'UTKAST - KREVER FAGLIG KONTROLL | Side {self.page_no()}'), 0, 0, 'C')
    def check_space(self, height):
        if self.get_y() + height > 270: 
            self.add_page(); self.set_margins(25, 25, 25); self.set_x(25)

def create_full_report_pdf(name, client, content, maps):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
    pdf.add_page()
    if os.path.exists("logo.png"): pdf.image("logo.png", x=25, y=20, w=50) 
    
    pdf.set_y(100); pdf.set_font('Helvetica', 'B', 24); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("BRANNTEKNISK KONSEPT (RIBr)"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 16); pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"FORPROSJEKT: {pdf.p_name}"), 0, 1, 'L'); pdf.ln(30)
    
    for l, v in [("OPPDRAGSGIVER:", client), ("DATO:", datetime.now().strftime("%d. %m. %Y")), ("UTARBEIDET AV:", "Builtly RIBr AI Engine"), ("REGELVERK:", pd_state.get('land', 'Norge'))]:
        pdf.set_x(25); pdf.set_font('Helvetica', 'B', 10); pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10); pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1); pdf.ln(5)
    
    for t in ["1. SAMMENDRAG OG KONKLUSJON", "2. PROSJEKTBESKRIVELSE OG REGELVERK", "3. KLASSIFISERING", "4. RØMNINGSFORHOLD OG LEDESYSTEM", "5. BRANNCELLER OG BRANNMOTSTAND", "6. SLOKKEUTSTYR OG REDNINGSBRANNVESEN", "VEDLEGG: VURDERT TEGNINGSGRUNNLAG"]:
        pdf.set_x(25); pdf.set_font('Helvetica', '', 11); pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 10, clean_pdf_text(t), 0, 1); pdf.set_draw_color(220, 220, 220); pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: pdf.ln(4); continue
        if line.startswith('# '):
            pdf.check_space(30); pdf.ln(8); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 14); pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip())); pdf.ln(2); pdf.set_font('Helvetica', '', 10); pdf.set_text_color(0, 0, 0)
        elif line.startswith('##'):
            pdf.check_space(20); pdf.ln(6); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 12); pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip())); pdf.set_font('Helvetica', '', 10); pdf.set_text_color(0, 0, 0)
        else:
            pdf.set_font('Helvetica', '', 10)
            safe_text = ironclad_text_formatter(line)
            if safe_text.strip() == "": continue
            try:
                if safe_text.startswith('- ') or safe_text.startswith('* '):
                    pdf.set_x(30); pdf.multi_cell(145, 5, safe_text); pdf.set_x(25)
                else:
                    pdf.set_x(25); pdf.multi_cell(150, 5, safe_text)
            except Exception: pdf.ln(2)

    if maps:
        pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72); pdf.cell(0, 20, "VEDLEGG: VURDERT TEGNINGSGRUNNLAG", 0, 1)
        for i, m in enumerate(maps):
            if i > 0: pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                m.save(tmp.name, format="PNG")
                img_h = 160 * (m.height / m.width)
                if img_h > 240: 
                    img_h = 240
                    img_w = 240 * (m.width / m.height)
                    pdf.image(tmp.name, x=105-(img_w/2), y=pdf.get_y(), w=img_w)
                else:
                    pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                
                pdf.set_y(pdf.get_y() + img_h + 5)
                pdf.set_x(25); pdf.set_font('Helvetica', 'I', 10); pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: Dokument visuelt analysert av AI-agenten."), 0, 1, 'C')

    return bytes(pdf.output(dest='S'))

# --- STREAMLIT UI ---
st.markdown(f"<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🔥 RIBr — Brannkonsept</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #9fb0c3; font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for generering av teknisk brannkonsept basert på arkitektur og situasjonsplan.</p>", unsafe_allow_html=True)

st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er automatisk synkronisert.")

with st.expander("1. Prosjekt & Lokasjon (Auto-synced)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state["p_name"], disabled=True)
    c_name = c2.text_input("Oppdragsgiver", value=pd_state["c_name"], disabled=True)
    adresse = st.text_input("Adresse", value=f"{pd_state['adresse']}, {pd_state['kommune']}", disabled=True)
    st.info(f"📍 **Regelverk:** Agenten vil bruke **{pd_state.get('land', 'Norge')}** som juridisk utgangspunkt.")

with st.expander("2. Bygningsdata & Klassifisering", expanded=True):
    c3, c4, c5 = st.columns(3)
    b_type = c3.text_input("Formål", value=pd_state["b_type"], disabled=True)
    etasjer = c4.number_input("Antall etasjer", value=int(pd_state["etasjer"]), disabled=True)
    bta = c5.number_input("Bruttoareal (BTA m2)", value=int(pd_state["bta"]), disabled=True)
    
    st.markdown("##### Brannteknisk Klassifisering")
    c6, c7 = st.columns(2)
    risikoklasse = c6.selectbox("Risikoklasse / Verksamhetsklass", ["RKL 1 (Garasjer/Lager)", "RKL 2 (Kontor)", "RKL 4 (Bolig)", "RKL 6 (Sykehus/Hotell)"], index=1)
    brannklasse = c7.selectbox("Brannklasse / Byggnadsklass", ["BKL 1", "BKL 2", "BKL 3", "BKL 4"], index=1)

with st.expander("3. Visuelt Grunnlag (Kart, Arkitektur & Snitt)", expanded=True):
    st.info("Viktig: For å vurdere brannsmitte og tilkomst for brannbil, trenger AI-en et kart. Du kan hente det automatisk eller laste opp situasjonsplan manuelt.")
    
    col_map1, col_map2 = st.columns(2)
    with col_map1:
        if st.button("🌐 Hent kart automatisk for prosjektet", type="secondary"):
            with st.spinner("Søker i Kartverket og Google Maps..."):
                img, source = fetch_map_image(pd_state["adresse"], pd_state["kommune"], pd_state["gnr"], pd_state["bnr"], google_key)
                if img:
                    st.session_state.brann_kart = img
                    st.session_state.brann_kart_kilde = source
                    st.success(f"✅ Kart hentet fra: {source}")
                else:
                    st.error(source)
        
        if st.session_state.brann_kart:
            st.image(st.session_state.brann_kart, caption=f"Kilde: {st.session_state.brann_kart_kilde}", use_container_width=True)

    with col_map2:
        st.markdown("##### Plantegninger og Snitt")
        files = st.file_uploader("Last opp plantegninger/snitt (PDF/Bilder)", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

st.markdown("<br>", unsafe_allow_html=True)

if st.button("🚀 Kjør Brannteknisk Analyse (RIBr)", type="primary", use_container_width=True):
    
    images_for_ai = [] 
    if st.session_state.brann_kart:
        images_for_ai.append(st.session_state.brann_kart)
        
    if files:
        with st.spinner("📐 Henter ut bilder fra dokumentene for visuell AI-analyse..."):
            try:
                for f in files: 
                    if f.name.lower().endswith('pdf'):
                        if fitz is None: st.stop()
                        doc = fitz.open(stream=f.read(), filetype="pdf")
                        for page_num in range(min(3, len(doc))): 
                            pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
                            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                            images_for_ai.append(img)
                        doc.close() 
                    else:
                        img = Image.open(f).convert("RGB")
                        images_for_ai.append(img)
            except Exception as e: 
                st.error(f"Feil under bildebehandling: {e}")
                
    with st.spinner(f"🤖 Analyserer tegninger og genererer brannstrategi etter {pd_state.get('land', 'Norge')}..."):
        try:
            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        valgt_modell = valid_models[0]
        for fav in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if fav in valid_models: valgt_modell = fav; break
        
        model = genai.GenerativeModel(valgt_modell)

        prompt_text = f"""
        Du er Builtly RIBr AI, en senior branningeniør.
        Skriv et formelt "Brannteknisk konsept" for prosjektet:
        
        PROSJEKT: {p_name} ({bta} m2, {etasjer} etasjer). 
        KLASSIFISERING: {risikoklasse}, {brannklasse}.
        LOKASJON: {adresse}.
        
        REGELVERK: {pd_state.get('land', 'Norge (TEK17)')}
        Skriv på språket og referer til det nasjonale regelverket som er angitt over.
        
        KUNDENS PROSJEKTBESKRIVELSE: 
        "{pd_state['p_desc']}"
        
        VISUELL ANALYSE AV VEDLAGTE TEGNINGER OG KART:
        Jeg har lagt ved bilder av bygget og et kart over lokasjonen. Din oppgave er å NØYE analysere disse:
        1. Innvendig (Plantegninger): Identifiser trapperom, rømningsveier og rominndeling. Hvor bør branncellene gå?
        2. Utvendig (Kart/Utomhus): Vurder hvor brannbiler kan kjøre inn og plasseres (oppstillingsplass / tilkomst for innsatsmannskaper). Vurder rømning ut på terreng til sikkert sted. Vurder også avstand til nabobygg med tanke på smittefare.
        
        INSTRUKSER (Skriv formelt, teknisk tungt og presist, min 1200 ord):
        - Flett inn dine spesifikke observasjoner fra bildene i teksten.
        - Ta spesifikt stilling til adkomst for brannvesen og brannsmitte i rapporten basert på kartet.
        
        STRUKTUR (Bruk KUN disse nøyaktige overskriftene):
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. PROSJEKTBESKRIVELSE OG REGELVERK
        # 3. KLASSIFISERING
        # 4. RØMNINGSFORHOLD OG LEDESYSTEM (Beskriv innvendig rømning og rømning på terreng basert på tegning)
        # 5. BRANNCELLER OG BRANNMOTSTAND (Inkluder avstand til nabobygg/smittefare)
        # 6. SLOKKEUTSTYR OG REDNINGSBRANNVESEN (Beskriv tilkomstvei og oppstillingsplass for brannbil basert på situasjonsplan)
        """
        
        prompt_parts = [prompt_text] + images_for_ai

        try:
            res = model.generate_content(prompt_parts)
            
            with st.spinner("Kompilerer Brann-PDF og sender til QA-kø..."):
                pdf_data = create_full_report_pdf(p_name, c_name, res.text, images_for_ai)
                
                # --- HER ER MAGIEN! VI LAGRER PDF-EN TIL QA-KØEN I MINNET! ---
                if "pending_reviews" not in st.session_state:
                    st.session_state.pending_reviews = {}
                if "review_counter" not in st.session_state:
                    st.session_state.review_counter = 1
                    
                doc_id = f"PRJ-{datetime.now().strftime('%y')}-BR{st.session_state.review_counter:03d}"
                st.session_state.review_counter += 1
                
                st.session_state.pending_reviews[doc_id] = {
                    "title": pd_state['p_name'],
                    "module": "RIBr (Brannkonsept)",
                    "drafter": "Builtly AI",
                    "reviewer": "Senior Branningeniør",
                    "status": "Pending Senior Review",
                    "class": "badge-pending",
                    "pdf_bytes": pdf_data
                }
                
                # Lagrer PDF-en lokalt i session state for nedlastingsknappen
                st.session_state.generated_brann_pdf = pdf_data
                st.session_state.generated_brann_filename = f"Builtly_RIBr_{p_name}.pdf"
                st.rerun() # Tvinger oppdatering så knappene vises
                
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")

# --- NEDLASTING OG NAVIGASJON ---
if "generated_brann_pdf" in st.session_state:
    st.success("✅ RIBr Rapport er ferdigstilt og sendt til QA-køen!")
    
    col_dl, col_qa = st.columns(2)
    with col_dl:
        st.download_button("📄 Last ned Brannkonsept direkte", st.session_state.generated_brann_pdf, st.session_state.generated_brann_filename, type="primary", use_container_width=True)
    with col_qa:
        if find_page("Review"):
            if st.button("🔍 Gå til QA for å godkjenne", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))
