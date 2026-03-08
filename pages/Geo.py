import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
import base64
from datetime import datetime
import tempfile
import re
import requests
import urllib.parse
import io
from PIL import Image
from pathlib import Path

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Geo & Miljø (RIG-M) | Builtly", layout="wide", initial_sidebar_state="collapsed")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()

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

# --- 2. PREMIUM CSS (SAMME SOM PROJECT & BRANN) ---
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
    
    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important;}

    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, div[data-baseweb="select"] * { background-color: transparent !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; border: none !important; box-shadow: none !important; }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus { border: none !important; }
    div[data-baseweb="base-input"]:focus-within, div[data-baseweb="select"] > div:focus-within, .stTextArea > div > div > div:focus-within { border-color: var(--accent) !important; box-shadow: 0 0 0 1px rgba(56, 194, 201, 0.5) !important; }
    ul[data-baseweb="menu"] { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; }
    ul[data-baseweb="menu"] li { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
    ul[data-baseweb="menu"] li:hover { background-color: rgba(56, 194, 201, 0.1) !important; }
    div[data-testid="InputInstructions"], div[data-testid="InputInstructions"] > span { color: #9fb0c3 !important; -webkit-text-fill-color: #9fb0c3 !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label { color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important; }
    
    div[data-testid="stExpander"] details, div[data-testid="stExpander"] details summary, div[data-testid="stExpander"] { background-color: #0c1520 !important; color: #f5f7fb !important; border-radius: 12px !important; }
    div[data-testid="stExpander"] details summary:hover { background-color: rgba(255,255,255,0.03) !important; }
    div[data-testid="stExpander"] details summary p { color: #f5f7fb !important; font-weight: 650 !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; margin-bottom: 1rem !important; }
    div[data-testid="stExpanderDetails"] { background: transparent !important; color: #f5f7fb !important; }
    div[data-testid="stExpanderDetails"] > div > div > div { background-color: transparent !important; }
    
    [data-testid="stFileUploaderDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important; border-radius: 12px !important; padding: 2rem !important; }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #38c2c9 !important; background-color: rgba(56, 194, 201, 0.05) !important; }
    [data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
    [data-testid="stFileUploaderFileData"] { background-color: rgba(255,255,255,0.02) !important; color: #f5f7fb !important; border-radius: 8px !important;}
    [data-testid="stAlert"] { background-color: rgba(56, 194, 201, 0.05) !important; border: 1px solid rgba(56, 194, 201, 0.2) !important; border-radius: 12px !important; }
    [data-testid="stAlert"] * { color: #f5f7fb !important; }
</style>
""", unsafe_allow_html=True)

# --- 3. SESSION STATE LOGIKK ---
if "project_data" not in st.session_state:
    st.session_state.project_data = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": "", "b_type": "Næring", "etasjer": 1, "bta": 0, "land": "Norge"}
if "geo_maps" not in st.session_state:
    st.session_state.geo_maps = {"recent": None, "historical": None, "source": "Ikke hentet"}
if "project_images" not in st.session_state:
    st.session_state.project_images = []

if st.session_state.project_data.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(f"<div style='margin-bottom:2rem;'>{logo_html}</div>")
    st.warning("⚠️ **Handling kreves:** Du må sette opp prosjektdataen før du kan bruke denne modulen.")
    if find_page("Project"):
        if st.button("⚙️ Gå til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()

# --- 4. HEADER ---
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

# --- 5. KARTVERKET + GOOGLE MAPS FALLBACK ---
def fetch_kartverket_og_google(adresse, kommune, gnr, bnr, api_key):
    nord, ost = None, None
    adr_clean = adresse.replace(',', '').strip() if adresse else ""
    kom_clean = kommune.replace(',', '').strip() if kommune else ""
    
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
        
    if nord and ost:
        min_x, max_x = float(ost) - 150, float(ost) + 150
        min_y, max_y = float(nord) - 150, float(nord) + 150
        url_orto = f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
        try:
            r1 = requests.get(url_orto, timeout=5)
            if r1.status_code == 200 and len(r1.content) > 5000:
                return Image.open(io.BytesIO(r1.content)).convert('RGB'), "Kartverket (Norge i Bilder)"
        except: pass
        
    if api_key and (adr_clean or kom_clean):
        query = f"{adr_clean}, {kom_clean}, Norway"
        safe_query = urllib.parse.quote(query)
        url_gmaps = f"https://maps.googleapis.com/maps/api/staticmap?center={safe_query}&zoom=19&size=600x600&maptype=satellite&key={api_key}"
        try:
            r2 = requests.get(url_gmaps, timeout=5)
            if r2.status_code == 200:
                return Image.open(io.BytesIO(r2.content)).convert('RGB'), "Google Maps Satellite"
        except: pass
        
    return None, "Kunne ikke hente kart."

# --- 6. EXCEL/CSV DATA EXTRACTOR ---
def extract_drill_data(files):
    extracted_text = ""
    for f in files:
        if f.name.lower().endswith(('.xlsx', '.xls', '.csv')):
            try:
                df = pd.read_csv(f) if f.name.lower().endswith('.csv') else pd.read_excel(f)
                extracted_text += f"\n--- RÅDATA FRA LABORATORIUM: {f.name.upper()} ---\n{df.head(100).to_string()}\n\n"
            except Exception as e:
                extracted_text += f"\n[Feil ved lesing av {f.name}: {e}]\n"
    return extracted_text if extracted_text else "Ingen Excel/CSV-data ble lastet opp."

# --- 7. DYNAMISK PDF MOTOR FOR GEO (Fikset for JPEG/RGB) ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15); self.set_font('Helvetica', 'B', 10); self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: RIG-M-001"), 0, 1, 'R')
            self.set_draw_color(200, 200, 200); self.line(25, 25, 185, 25); self.set_y(30)
    def footer(self):
        self.set_y(-15); self.set_font('Helvetica', 'I', 8); self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f'UTKAST - KREVER FAGLIG KONTROLL | Side {self.page_no()}'), 0, 0, 'C')
    def check_space(self, height):
        if self.get_y() + height > 270: 
            self.add_page(); self.set_margins(25, 25, 25); self.set_x(25)

def create_full_report_pdf(name, client, content, recent_img, hist_img, source_text):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
    pdf.add_page()
    if os.path.exists("logo.png"): pdf.image("logo.png", x=25, y=20, w=50)
    pdf.set_y(100); pdf.set_font('Helvetica', 'B', 24); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("GEOTEKNISK & MILJØTEKNISK RAPPORT"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 16); pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"INKLUDERT TILTAKSPLAN: {pdf.p_name}"), 0, 1, 'L'); pdf.ln(30)
    
    metadata = [("OPPDRAGSGIVER:", client), ("DATO:", datetime.now().strftime("%d. %m. %Y")), ("UTARBEIDET AV:", "Builtly RIG-M AI Engine"), ("REGELVERK:", pd_state.get('land', 'Ukjent'))]
    for l, v in metadata:
        pdf.set_x(25); pdf.set_font('Helvetica', 'B', 10); pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10); pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1); pdf.ln(5)
    toc = ["1. SAMMENDRAG OG KONKLUSJON", "2. INNLEDNING OG PROSJEKTBESKRIVELSE", "3. KARTVERKET OG HISTORISK LOKASJON", "4. UTFØRTE GRUNNUNDERSØKELSER", "5. RESULTATER: GRUNNFORHOLD OG FORURENSNING", "6. GEOTEKNISKE VURDERINGER", "7. TILTAKSPLAN OG MASSEHÅNDTERING"]
    pdf.set_font('Helvetica', '', 11); pdf.set_text_color(0, 0, 0)
    for t in toc:
        pdf.set_x(25); pdf.cell(0, 10, clean_pdf_text(t), 0, 1); pdf.set_draw_color(220, 220, 220); pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: pdf.ln(4); continue
            
        if line.startswith('# 3. KARTVERKET'):
            pdf.check_space(180); pdf.ln(8); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 14); pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip())); pdf.ln(4)
            
            # Kartbilder med JPEG konvertering for sikker utskrift
            if recent_img and hist_img:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as t1, tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as t2:
                    recent_img.convert("RGB").save(t1.name, format="JPEG"); hist_img.convert("RGB").save(t2.name, format="JPEG")
                    y_pos = pdf.get_y()
                    pdf.image(t1.name, x=25, y=y_pos, w=75); pdf.image(t2.name, x=110, y=y_pos, w=75)
                    pdf.set_y(y_pos + 75 * (recent_img.height / recent_img.width) + 5)
                    pdf.set_font('Helvetica', 'I', 8); pdf.set_text_color(100, 100, 100); pdf.set_x(25)
                    pdf.cell(75, 5, clean_pdf_text(f"Fig 1: Nyere ({source_text})"), 0, 0, 'C'); pdf.set_x(110); pdf.cell(75, 5, clean_pdf_text("Fig 2: Historisk"), 0, 1, 'C'); pdf.ln(5)
            elif recent_img:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as t1:
                    recent_img.convert("RGB").save(t1.name, format="JPEG")
                    pdf.image(t1.name, x=45, y=pdf.get_y(), w=120)
                    pdf.set_y(pdf.get_y() + 120 * (recent_img.height / recent_img.width) + 5)
                    pdf.set_font('Helvetica', 'I', 9); pdf.set_text_color(100, 100, 100); pdf.set_x(25); pdf.cell(0, 5, clean_pdf_text(f"Figur 1: Kartgrunnlag. Kilde: {source_text}"), 0, 1, 'C'); pdf.ln(5)
            else:
                pdf.set_font('Helvetica', 'I', 9); pdf.set_text_color(150, 0, 0); pdf.set_x(25); pdf.cell(0, 5, f"Merknad: Intet kartgrunnlag er innhentet.", 0, 1, 'L'); pdf.ln(5)
            pdf.set_font('Helvetica', '', 10); pdf.set_text_color(0, 0, 0); continue
            
        elif line.startswith('# ') or re.match(r'^\d\.\s[A-Z]', line):
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
                
    return bytes(pdf.output(dest='S'))

# --- 8. UI FOR GEO MODUL ---
st.markdown(f"<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🌍 Geo & Miljø (RIG-M)</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #9fb0c3; font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for miljøteknisk grunnundersøkelse og tiltaksplan.</p>", unsafe_allow_html=True)

st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er synkronisert fra Project SSOT.")

with st.expander("1. Prosjekt & Lokasjon (SSOT)", expanded=False):
    c1, c2 = st.columns(2)
    st.text_input("Prosjektnavn", value=pd_state["p_name"], disabled=True)
    st.text_input("Gnr/Bnr", value=f"{pd_state['gnr']} / {pd_state['bnr']}", disabled=True)

with st.expander("2. Kartgrunnlag & Ortofoto (Påkrevd)", expanded=True):
    st.markdown("For å vurdere potensialet for forurenset grunn, krever veilederen en visuell bedømming av nyere og historiske flyfoto. AI-en integrerer disse i rapporten.")
    
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🌐 Hent kart automatisk", type="secondary"):
            with st.spinner("Søker i Matrikkel og Kartkatalog..."):
                img, source = fetch_kartverket_og_google(pd_state["adresse"], pd_state["kommune"], pd_state["gnr"], pd_state["bnr"], google_key)
                if img:
                    st.session_state.geo_maps["recent"] = img
                    st.session_state.geo_maps["source"] = source
                    st.success(f"✅ Hentet fra {source}!")
                else: 
                    st.error("Fant ikke kart. Vennligst last opp manuelt.")
                    
        if st.session_state.geo_maps["recent"]:
            st.image(st.session_state.geo_maps["recent"], caption=f"Valgt: {st.session_state.geo_maps['source']}", use_container_width=True)

    with col_b:
        st.markdown("##### ⚠️ Manuell opplasting (Fallback)")
        man_recent = st.file_uploader("Last opp nyere Ortofoto (Valgfritt)", type=['png', 'jpg', 'jpeg'])
        if man_recent:
            st.session_state.geo_maps["recent"] = Image.open(man_recent).convert("RGB")
            st.session_state.geo_maps["source"] = "Manuelt opplastet"
            
        man_hist = st.file_uploader("Last opp historisk flyfoto (F.eks. fra 1950 for å sjekke tidl. industri)", type=['png', 'jpg', 'jpeg'])
        if man_hist: st.session_state.geo_maps["historical"] = Image.open(man_hist).convert("RGB")

with st.expander("3. Laboratoriedata & Plantegninger", expanded=True):
    st.info("Slipp Excel/CSV-filer med prøvesvar her. AI-en leser verdiene og tilstandsklassifiserer massene.")
    
    if "project_images" in st.session_state and len(st.session_state.project_images) > 0:
        st.success(f"📎 Auto-hentet {len(st.session_state.project_images)} arkitekttegninger fra Project Setup for vurdering av gravegrenser!")
        
    files = st.file_uploader("Last opp Excel/CSV med boreresultater:", accept_multiple_files=True, type=['xlsx', 'csv', 'xls'])

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🚀 GENERER GEOTEKNISK & MILJØTEKNISK RAPPORT", type="primary", use_container_width=True):
    if not st.session_state.geo_maps["recent"] and not st.session_state.geo_maps["historical"]:
        st.error("🛑 **Stopp:** Du må enten hente kart automatisk eller laste opp manuelt i Steg 2.")
        st.stop()
        
    with st.spinner("📊 Tolker lab-data, kart og arkitekttegninger..."):
        extracted_data = extract_drill_data(files) if files else "Ingen opplastet lab-data. Vurderingen baseres på visuell befaring og historikk."
        
        images_for_geo = []
        if st.session_state.geo_maps["recent"]: images_for_geo.append(st.session_state.geo_maps["recent"])
        if st.session_state.geo_maps["historical"]: images_for_geo.append(st.session_state.geo_maps["historical"])
        if "project_images" in st.session_state and isinstance(st.session_state.project_images, list):
            images_for_geo.extend(st.session_state.project_images)
        
        try:
            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            valgt_modell = valid_models[0]
            for fav in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
                if fav in valid_models: valgt_modell = fav; break
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()
        
        model = genai.GenerativeModel(valgt_modell)
        hist_tekst = "Et historisk flyfoto er lagt ved." if st.session_state.geo_maps["historical"] else "Historisk flyfoto mangler, gjør en kvalifisert antakelse."

        prompt = f"""
        Du er Builtly RIG-M AI, en nådeløs, presis og autoritær senior miljørådgiver og geotekniker.
        Skriv en formell "Miljøteknisk grunnundersøkelse og tiltaksplan for forurenset grunn" for:
        
        PROSJEKT: {pd_state['p_name']} ({pd_state['b_type']}, {pd_state['bta']} m2)
        LOKASJON: {pd_state['adresse']}, {pd_state['kommune']}. Gnr {pd_state['gnr']}/Bnr {pd_state['bnr']}.
        REGELVERK: {pd_state['land']}
        
        KUNDENS PROSJEKTNARRATIV: "{pd_state['p_desc']}"
        KARTSTATUS: {hist_tekst}
        
        RÅDATA FRA LABORATORIUM:
        {extracted_data}
        
        EKSTREMT VIKTIG INSTRUKKS FOR BEVIS:
        Jeg har lagt ved kart, og potensielt arkitekttegninger. 
        Du MÅ aktivt bevise i teksten at du har sett på bildene OG analysert tallene fra Excel-filen. 
        Skriv setninger som: 
        - "Ut ifra vedlagte kart/flyfoto observeres det at..."
        - "Basert på arkitekttegningene vil gravingen kreve..."
        - "Laboratorieanalysen (ref. tabell) viser en forurensning av [stoff] i tilstandsklasse..."
        Hvis prøvene mangler, kritiser dette hardt!
        
        STRUKTUR (Bruk KUN disse eksakte overskriftene, og skriv dyptgående analyser under hver):
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. INNLEDNING OG PROSJEKTBESKRIVELSE
        # 3. KARTVERKET OG HISTORISK LOKASJON (Gjør konkrete observasjoner fra kartbildene)
        # 4. UTFØRTE GRUNNUNDERSØKELSER
        # 5. RESULTATER: GRUNNFORHOLD OG FORURENSNING (Dra inn tall og data fra Excel-uttrekket)
        # 6. GEOTEKNISKE VURDERINGER (Gjør vurderinger basert på situasjonsplan og omfang av bygg)
        # 7. TILTAKSPLAN OG MASSEHÅNDTERING (Skriv strenge, konkrete tiltak for fjerning/deponering)
        """
        
        try:
            res = model.generate_content([prompt] + images_for_geo)
            with st.spinner("Kompilerer RIG-PDF og sender til QA-kø..."):
                pdf_data = create_full_report_pdf(pd_state['p_name'], pd_state['c_name'], res.text, st.session_state.geo_maps["recent"], st.session_state.geo_maps["historical"], st.session_state.geo_maps["source"])
                
                # Legger i Review-køen
                if "pending_reviews" not in st.session_state:
                    st.session_state.pending_reviews = {}
                if "review_counter" not in st.session_state:
                    st.session_state.review_counter = 1
                    
                doc_id = f"PRJ-{datetime.now().strftime('%y')}-GEO{st.session_state.review_counter:03d}"
                st.session_state.review_counter += 1
                
                st.session_state.pending_reviews[doc_id] = {
                    "title": pd_state['p_name'],
                    "module": "RIG-M (Geo & Miljø)",
                    "drafter": "Builtly AI",
                    "reviewer": "Senior Miljørådgiver",
                    "status": "Pending Senior Review",
                    "class": "badge-pending",
                    "pdf_bytes": pdf_data
                }
                
                st.session_state.generated_geo_pdf = pdf_data
                st.session_state.generated_geo_filename = f"Builtly_GEO_{pd_state['p_name'].replace(' ', '_')}.pdf"
                st.rerun()
                
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")

# --- NEDLASTING OG NAVIGASJON ---
if "generated_geo_pdf" in st.session_state:
    st.success("✅ RIG-M Rapport er ferdigstilt og sendt til QA-køen!")
    
    col_dl, col_qa = st.columns(2)
    with col_dl:
        st.download_button("📄 Last ned Geo/Miljø-rapport", st.session_state.generated_geo_pdf, st.session_state.generated_geo_filename, type="primary", use_container_width=True)
    with col_qa:
        if find_page("Review"):
            if st.button("🔍 Gå til QA for å godkjenne", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))
