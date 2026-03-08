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
from PIL import Image
import numpy as np
from pathlib import Path

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Konstruksjon (RIB) | Builtly", layout="wide", initial_sidebar_state="collapsed")

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

# --- 2. PREMIUM CSS (SKUDDSIKKER) ---
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

    /* INPUT-FELT DESIGN (KRYSTALLKLAR TEKST) */
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

# --- 3. SESSION STATE / SIKKERHET ---
if "project_data" not in st.session_state:
    st.session_state.project_data = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": "", "b_type": "Næring", "etasjer": 1, "bta": 0, "land": "Norge"}
if "project_images" not in st.session_state:
    st.session_state.project_images = []

if st.session_state.project_data.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(f"<div style='margin-bottom:2rem;'>{logo_html}</div>")
    
    st.warning("⚠️ **Handling kreves:** Du må sette opp prosjektdataen før du kan bruke denne modulen.")
    st.info("RIB-agenten trenger kontekst om bygget (areal, etasjer, adresse og regelverk) for å kunne generere en faglig og juridisk korrekt rapport.")
    
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

# --- 5. DYNAMISK PDF MOTOR (RIB) ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15); self.set_font('Helvetica', 'B', 10); self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: RIB-001"), 0, 1, 'R')
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
    pdf.cell(0, 15, clean_pdf_text("KONSEPTNOTAT: KONSTRUKSJON (RIB)"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 16); pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"FORPROSJEKT: {pdf.p_name}"), 0, 1, 'L'); pdf.ln(30)
    
    for l, v in [("OPPDRAGSGIVER:", client), ("DATO:", datetime.now().strftime("%d. %m. %Y")), ("UTARBEIDET AV:", "Builtly RIB AI Engine"), ("REGELVERK:", pd_state.get('land', 'Norge'))]:
        pdf.set_x(25); pdf.set_font('Helvetica', 'B', 10); pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10); pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1); pdf.ln(5)
    
    # Oppdatert TOC basert på ny senior-prompt
    toc = [
        "1. SAMMENDRAG OG KONKLUSJON", 
        "2. VURDERING AV DATAGRUNNLAG", 
        "3. LASTER OG FORUTSETNINGER", 
        "4. KONSEPT FOR BÆRESYSTEM OG STABILITET", 
        "5. FUNDAMENTERING OG EKSISTERENDE KONSTRUKSJONER", 
        "6. RISIKO, SÅRBARHET OG NESTE STEG", 
        "VEDLEGG: VURDERT TEGNINGSGRUNNLAG"
    ]
    for t in toc:
        pdf.set_x(25); pdf.set_font('Helvetica', '', 11); pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 10, clean_pdf_text(t), 0, 1); pdf.set_draw_color(220, 220, 220); pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: pdf.ln(4); continue
        if line.startswith('# ') or re.match(r'^\d+\.\s[A-Z]', line):
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

    if maps and len(maps) > 0:
        pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72); pdf.cell(0, 20, "VEDLEGG: VURDERT BILDEDOKUMENTASJON", 0, 1)
        for i, m in enumerate(maps):
            if i > 0: pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                # Tvinger JPEG for å sikre stabilitet for gjennomsiktige PNG-er
                m.convert("RGB").save(tmp.name, format="JPEG")
                img_h = 160 * (m.height / m.width)
                if img_h > 240: 
                    img_h = 240
                    img_w = 240 * (m.width / m.height)
                    pdf.image(tmp.name, x=105-(img_w/2), y=pdf.get_y(), w=img_w)
                else:
                    pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                
                pdf.set_y(pdf.get_y() + img_h + 5)
                pdf.set_x(25); pdf.set_font('Helvetica', 'I', 10); pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: Dokument visuelt analysert av RIB-agenten."), 0, 1, 'C')

    return bytes(pdf.output(dest='S'))

# --- STREAMLIT UI ---
st.markdown(f"<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🏢 RIB — Konstruksjon</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: var(--muted); font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for konseptuell dimensjonering, lastveier og stabilitet.</p>", unsafe_allow_html=True)

st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er automatisk synkronisert (SSOT).")

with st.expander("1. Prosjekt & Lokasjon (Auto-synced)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state["p_name"], disabled=True)
    b_type = c2.text_input("Formål / Bygningstype", value=pd_state["b_type"], disabled=True)
    adresse = st.text_input("Adresse", value=f"{pd_state['adresse']}, {pd_state['kommune']}", disabled=True)
    st.info(f"📍 **Regelverk & Laster:** Agenten vil bruke **{pd_state.get('land', 'Norge')}** for å vurdere snølaster, vindlaster og nasjonale annex for Eurokoder.")

with st.expander("2. Valg av Bæresystem & Grunnforhold", expanded=True):
    c3, c4 = st.columns(2)
    material_valg = c3.selectbox("Foretrukket hovedbæresystem", ["Massivtre (Krysslimt tre / CLT)", "Stål og Hulldekker", "Plasstøpt Betong", "Prefabrikkert Betong", "Hybrid / Kombinasjon"])
    fundamentering = c4.selectbox("Forventet fundamenteringsmetode", ["Direkte fundamentering (fjell/faste masser)", "Peling til fjell", "Sålefundament / Kompensert fundamentering", "Uavklart - Vurderes basert på bygg"])

with st.expander("3. Visuelt Grunnlag (Arkitektur / Snitt)", expanded=True):
    st.info("Viktig: RIB trenger plantegninger og snitt for å analysere bygningsvolumet, foreslå grid-system (spennvidder for dekker), søyleplassering og stabiliserende kjerner.")
    
    if "project_images" in st.session_state and len(st.session_state.project_images) > 0:
        st.success(f"📎 Fant {len(st.session_state.project_images)} felles arkitekttegninger fra Project Setup. Disse inkluderes automatisk i RIB-analysen!")
    else:
        st.warning("Ingen felles tegninger funnet. Du bør laste opp plan og snitt under.")
        
    files = st.file_uploader("Last opp spesifikke konstruksjonstegninger / supplerende snitt", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🚀 Kjør Konstruksjonsanalyse (RIB)", type="primary", use_container_width=True):
    
    images_for_ai = [] 
    
    # Hent globale tegninger fra minnet
    if "project_images" in st.session_state and isinstance(st.session_state.project_images, list):
        images_for_ai.extend(st.session_state.project_images)
        
    if files:
        with st.spinner("📐 Leser ut supplerende lokale filer..."):
            try:
                for f in files: 
                    f.seek(0)
                    if f.name.lower().endswith('pdf'):
                        if fitz is not None: 
                            doc = fitz.open(stream=f.read(), filetype="pdf")
                            for page_num in range(min(4, len(doc))): 
                                pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
                                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                                images_for_ai.append(img)
                            doc.close() 
                    else:
                        img = Image.open(f).convert("RGB")
                        images_for_ai.append(img)
            except Exception as e: 
                st.error(f"Feil under bildebehandling: {e}")
                
    st.info(f"Klar! Sender totalt {len(images_for_ai)} bilder/tegninger til RIB-agenten for vurdering.")
                
    with st.spinner(f"🤖 Vurderer bæresystem og genererer RIB-notat for {pd_state.get('land', 'Norge')}..."):
        try:
            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        valgt_modell = valid_models[0]
        for fav in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if fav in valid_models: valgt_modell = fav; break
        
        model = genai.GenerativeModel(valgt_modell)

        # --- DEN STRENGE SENIOR RIB-PROMPTEN MED 3-TRINNS LOGIKK ---
        prompt_text = f"""
        Du er Builtly RIB AI, en senior rådgivende ingeniør bygg (RIB) med tung erfaring fra bolig, næring, rehabilitering, transformasjon og nybygg.
        Du vurderer bæresystem, lastnedføring, stabilitet, spenn, materialvalg, fundamentering og byggbarhet.
        
        PROSJEKT: {p_name} ({b_type}, {pd_state['bta']} m2, {pd_state['etasjer']} etasjer). 
        LOKASJON: {adresse}.
        FORETRUKKET MATERIALE: {material_valg}.
        FUNDAMENTERING: {fundamentering}.
        REGELVERK: {pd_state.get('land', 'Norge (TEK17 / Eurokoder)')}. 
        
        KUNDENS PROSJEKTBESKRIVELSE: 
        "{pd_state['p_desc']}"
        
        REGLER FOR DIN VURDERING (ALDRI BRYT DISSE):
        - Du skal aldri gjette kapasitet, materialstyrker eller eksakte grunnforhold uten data.
        - Du skal aldri anta at eksisterende bygg tåler nye laster uten grunnlag.
        - Du må være tydelig på hva som er dokumentert, antatt, usikkert og ikke vurderbart.
        - Ikke presenter grove vurderinger som detaljprosjektering. Skjul aldri usikkerhet.
        
        VIKTIG - VURDERING AV TEGNINGSGRUNNLAG:
        Du har nå mottatt et sett med bilder (plantegninger, snitt, fasader). 
        Din aller første oppgave er å vurdere om dette datagrunnlaget er godt nok for å sette et konsept. 
        Du MÅ aktivt beskrive hva du ser på bildene.
        
        Bestem deg for ett av tre spor for rapporten:
        
        SPOR 1: FULLSTENDIG GRUNNLAG (Du ser tydelige plantegninger og snitt som tillater vurdering av grid, spenn og lastveier)
        -> Lever en tydelig konstruksjonsfaglig vurdering. Foreslå grid-system, avstivende kjerner og robuste løsninger.
        
        SPOR 2: DELVIS GRUNNLAG (Du har noen skisser, men mangler f.eks snitt eller aksemål)
        -> Lever en foreløpig indikativ vurdering. Skriv "INDIKATIV VURDERING" i konklusjonen. Diskuter mulige spennvidder for {material_valg}, men presiser høyt og tydelig hva som mangler for å låse konseptet.
        
        SPOR 3: FOR SVAKT GRUNNLAG (Ingen relevante tegninger som viser bygningskroppen)
        -> Ikke gjør beregninger eller anta spennvidder. Lever KUN en presis mangelliste for hva arkitekten må levere, samt en overordnet teoretisk risikovurdering for et bygg av typen {b_type}. Skriv "AVVIST / FOR SVAKT GRUNNLAG" i konklusjonen.
        
        STRUKTUR PÅ RAPPORTEN (Bruk KUN disse eksakte overskriftene, uansett spor):
        # 1. SAMMENDRAG OG KONKLUSJON (Angi tydelig om dette er en endelig, indikativ eller avvist vurdering)
        # 2. VURDERING AV DATAGRUNNLAG (Vær streng! Hva ser du og hva mangler på tegningene?)
        # 3. LASTER OG FORUTSETNINGER (Snø, vind, nyttelast - angi hva som krever avklaring)
        # 4. KONSEPT FOR BÆRESYSTEM OG STABILITET (Spenn, geometri, materialvalg, avstivende skiver/kjerner basert på det du ser)
        # 5. FUNDAMENTERING OG EKSISTERENDE KONSTRUKSJONER (Vurder byggbarhet og risiko knyttet til grunn)
        # 6. RISIKO, SÅRBARHET OG NESTE STEG (Behov for forsterkning, spesifikke avklaringer mot arkitekt/geotekniker)
        """
        
        prompt_parts = [prompt_text] + images_for_ai

        try:
            res = model.generate_content(prompt_parts)
            
            with st.spinner("Kompilerer RIB-PDF og fletter inn tegninger som vedlegg..."):
                # HER LÅ FEILEN! Vi må sende pd_state['c_name'] til PDF-motoren, ikke c_name
                pdf_data = create_full_report_pdf(p_name, pd_state['c_name'], res.text, images_for_ai)
                
                # --- SENDER TIL QA-KØ ---
                if "pending_reviews" not in st.session_state:
                    st.session_state.pending_reviews = {}
                if "review_counter" not in st.session_state:
                    st.session_state.review_counter = 1
                    
                doc_id = f"PRJ-{datetime.now().strftime('%y')}-RIB{st.session_state.review_counter:03d}"
                st.session_state.review_counter += 1
                
                # Fargekoder status basert på AI-ens vurdering (Spor 1, 2 eller 3)
                ai_text_lower = res.text.lower()
                if "for svakt" in ai_text_lower or "avvist" in ai_text_lower:
                    status = "Rejected - Needs Architecture Data"
                    badge = "badge-early"
                elif "indikativ" in ai_text_lower or "delvis" in ai_text_lower:
                    status = "Indicative Structural Concept"
                    badge = "badge-roadmap"
                else:
                    status = "Pending Senior RIB Review"
                    badge = "badge-pending"
                
                st.session_state.pending_reviews[doc_id] = {
                    "title": pd_state['p_name'],
                    "module": "RIB (Konstruksjon)",
                    "drafter": "Builtly AI",
                    "reviewer": "Senior Konstruktør",
                    "status": status,
                    "class": badge,
                    "pdf_bytes": pdf_data
                }

            st.session_state.generated_rib_pdf = pdf_data
            st.session_state.generated_rib_filename = f"Builtly_RIB_{p_name}.pdf"
            st.rerun() 
                
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")

# --- NEDLASTING OG NAVIGASJON ---
if "generated_rib_pdf" in st.session_state:
    st.success("✅ RIB-notat er ferdigstilt og lagt i QA-køen!")
    
    col_dl, col_qa = st.columns(2)
    with col_dl:
        st.download_button("📄 Last ned RIB-rapport", st.session_state.generated_rib_pdf, st.session_state.generated_rib_filename, type="primary", use_container_width=True)
    with col_qa:
        if find_page("Review"):
            if st.button("🔍 Gå til QA for å godkjenne", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))
