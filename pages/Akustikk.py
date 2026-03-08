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
st.set_page_config(page_title="Akustikk (RIAku) | Builtly", layout="wide", initial_sidebar_state="collapsed")

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

# --- 5. DYNAMISK PDF MOTOR FOR AKUSTIKK ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15); self.set_font('Helvetica', 'B', 10); self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: RIAku-001"), 0, 1, 'R')
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
    pdf.cell(0, 15, clean_pdf_text("AKUSTIKKRAPPORT (RIAku)"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 16); pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"FORPROSJEKT: {pdf.p_name}"), 0, 1, 'L'); pdf.ln(30)
    
    for l, v in [("OPPDRAGSGIVER:", client), ("DATO:", datetime.now().strftime("%d. %m. %Y")), ("UTARBEIDET AV:", "Builtly RIAku AI Engine"), ("REGELVERK:", pd_state.get('land', 'Norge (NS 8175)'))]:
        pdf.set_x(25); pdf.set_font('Helvetica', 'B', 10); pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10); pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1); pdf.ln(5)
    
    for t in ["1. SAMMENDRAG OG KONKLUSJON", "2. VURDERING AV DATAGRUNNLAG", "3. LYDFORHOLD UTENDØRS", "4. LYDFORHOLD INNENDØRS", "5. FASADEISOLASJON", "6. TILTAK OG VIDERE PROSJEKTERING", "VEDLEGG: VURDERT DATAGRUNNLAG"]:
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

    if maps and len(maps) > 0:
        pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72); pdf.cell(0, 20, "VEDLEGG: VURDERT DATAGRUNNLAG", 0, 1)
        for i, m in enumerate(maps):
            if i > 0: pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                m.convert("RGB").save(tmp.name, format="JPEG")
                img_w = 160
                img_h = 160 * (m.height / m.width)
                if img_h > 240: 
                    img_h = 240
                    img_w = 240 * (m.width / m.height)
                x_pos = 105 - (img_w / 2)
                pdf.image(tmp.name, x=x_pos, y=pdf.get_y(), w=img_w)
                pdf.set_y(pdf.get_y() + img_h + 5)
                pdf.set_x(25); pdf.set_font('Helvetica', 'I', 10); pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: Dokument/Kart visuelt analysert av RIAku-agenten."), 0, 1, 'C')

    return bytes(pdf.output(dest='S'))


# --- 6. UI FOR AKUSTIKK MODUL ---
st.markdown(f"<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🔊 Lyd & Akustikk (RIAku)</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #9fb0c3; font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for støyvurdering, fasadeisolasjon og romakustikk.</p>", unsafe_allow_html=True)

st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er automatisk synkronisert.")

with st.expander("1. Prosjekt & Lokasjon (Auto-synced)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state["p_name"], disabled=True)
    c_name = c2.text_input("Oppdragsgiver", value=pd_state["c_name"], disabled=True)
    adresse = st.text_input("Adresse", value=f"{pd_state['adresse']}, {pd_state['kommune']}", disabled=True)

with st.expander("2. Bygningsdata & Lydklasse", expanded=True):
    c3, c4, c5 = st.columns(3)
    b_type = c3.text_input("Formål", value=pd_state["b_type"], disabled=True)
    etasjer = c4.number_input("Antall etasjer", value=int(pd_state["etasjer"]), disabled=True)
    bta = c5.number_input("Bruttoareal (BTA m2)", value=int(pd_state["bta"]), disabled=True)
    
    st.markdown("##### Akustisk Klassifisering (NS 8175)")
    c6, c7 = st.columns(2)
    # Lydklasse C er standard minimumskrav i Norge for nye boliger
    lydklasse = c6.selectbox("Lydklasse (NS 8175)", ["Klasse A (Spesielt gode)", "Klasse B (Gode)", "Klasse C (Minimumskrav i TEK)", "Klasse D (Eldre bygg)"], index=2)
    stoykilde = c7.selectbox("Dominerende Støykilde", ["Veitrafikk", "Bane/Tog", "Flystøy", "Industri/Næring", "Lite støy (Stille område)"], index=0)

with st.expander("3. Visuelt Grunnlag & Støykart", expanded=True):
    st.info("Viktig: For å beregne fasadeisolasjon trenger AI-en støykart (f.eks. fra kommunen) og plan/fasadetegninger.")
    
    if "project_images" in st.session_state and len(st.session_state.project_images) > 0:
        st.success(f"📎 Fant {len(st.session_state.project_images)} felles arkitekttegninger fra Project Setup. Disse inkluderes automatisk i analysen!")
    else:
        st.warning("Ingen felles tegninger funnet. Du bør laste opp plan og fasade under.")
        
    st.markdown("##### Last opp spesifikke Akustikk-vedlegg")
    files = st.file_uploader("Last opp Støykart, Trafikkdata eller Planløsninger (PDF/Bilder)", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

st.markdown("<br>", unsafe_allow_html=True)

if st.button("🚀 Kjør Akustisk Analyse (RIAku)", type="primary", use_container_width=True):
    
    images_for_ai = [] 
    if "project_images" in st.session_state and isinstance(st.session_state.project_images, list):
        images_for_ai.extend(st.session_state.project_images)
        
    if files:
        with st.spinner("📐 Leser ut støykart og supplerende filer..."):
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
                
    st.info(f"Klar! Sender totalt {len(images_for_ai)} bilder/tegninger til AI-en for vurdering.")
                
    with st.spinner(f"🤖 Vurderer datagrunnlaget og genererer Akustikkrapport..."):
        try:
            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        valgt_modell = valid_models[0]
        for fav in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if fav in valid_models: valgt_modell = fav; break
        
        model = genai.GenerativeModel(valgt_modell)

        # --- DEN STRENGE "PORTVAKT"-PROMPTEN ---
        prompt_text = f"""
        Du er Builtly RIAku AI, en streng, autoritær og svært grundig senior rådgivende ingeniør innen akustikk.
        
        PROSJEKT: {p_name} ({bta} m2, {etasjer} etasjer, Formål: {b_type}). 
        LOKASJON: {adresse}.
        MÅL-LYDKLASSE: {lydklasse}.
        DOMINERENDE STØYKILDE: {stoykilde}.
        
        KUNDENS PROSJEKTBESKRIVELSE: 
        "{pd_state['p_desc']}"
        
        VIKTIG - VURDERING AV DATAGRUNNLAG:
        Du har nå mottatt et sett med bilder (plantegninger, snitt, og eventuelt støykart). 
        Din aller første oppgave er å vurdere om dette datagrunnlaget er godt nok. 
        Du MÅ aktivt beskrive hva du ser på bildene.
        
        Bestem deg for ett av tre spor for rapporten:
        
        SPOR 1: FULLSTENDIG GRUNNLAG (Du ser både tydelige støykart/trafikktall OG plantegninger)
        -> Utfør beregninger, oppgi krav til fasadeisolasjon, trinnlyd og gi en endelig faglig konklusjon.
        
        SPOR 2: DELVIS GRUNNLAG (Du har plantegninger, men mangler støykart, eller vice versa)
        -> Forklar manglene tydelig. Skriv "INDIKATIV VURDERING" i konklusjonen. Gi generelle råd og anslag, men nekt å sette to streker under svaret før du får mer data.
        
        SPOR 3: FOR SVAKT GRUNNLAG (Du har ingen relevante tegninger eller kart)
        -> Ikke gjør beregninger. Rapporten skal fungere som et "Avviksnotat". Lever KUN en liste over mangler (hva kunden må laste opp) og en overordnet, teoretisk risikovurdering av prosjektet basert på adressen og formålet.
        
        STRUKTUR PÅ RAPPORTEN (Bruk KUN disse eksakte overskriftene, uansett spor):
        # 1. SAMMENDRAG OG KONKLUSJON (Skriv tydelig om dette er en endelig, indikativ eller avvist vurdering)
        # 2. VURDERING AV DATAGRUNNLAG (Er det fullstendig, delvis eller for svakt? Vær streng og spesifikk på hva du ser og ikke ser i vedleggene!)
        # 3. LYDFORHOLD UTENDØRS (Støy fra vei/industri basert på kart eller antakelser)
        # 4. LYDFORHOLD INNENDØRS (Luftlyd, trinnlyd, romakustikk. Kritiser planløsningen hvis du ser uheldige rom-kombinasjoner, f.eks. soverom mot støyende gate)
        # 5. FASADEISOLASJON (Krav til vinduer og yttervegg)
        # 6. TILTAK OG VIDERE PROSJEKTERING (Hva er neste steg for kunden?)
        """
        
        prompt_parts = [prompt_text] + images_for_ai

        try:
            res = model.generate_content(prompt_parts)
            
            with st.spinner("Kompilerer Akustikk-PDF og fletter inn tegninger som vedlegg..."):
                pdf_data = create_full_report_pdf(p_name, c_name, res.text, images_for_ai)
                
                if "pending_reviews" not in st.session_state:
                    st.session_state.pending_reviews = {}
                if "review_counter" not in st.session_state:
                    st.session_state.review_counter = 1
                    
                doc_id = f"PRJ-{datetime.now().strftime('%y')}-AKU{st.session_state.review_counter:03d}"
                st.session_state.review_counter += 1
                
                # Fargekoder status basert på om AI-en var fornøyd eller ikke
                ai_text_lower = res.text.lower()
                if "for svakt" in ai_text_lower or "avvist" in ai_text_lower:
                    status = "Rejected - Needs Data"
                    badge = "badge-early"
                elif "indikativ" in ai_text_lower or "delvis" in ai_text_lower:
                    status = "Indicative Assessment"
                    badge = "badge-roadmap"
                else:
                    status = "Pending Senior Review"
                    badge = "badge-pending"
                
                st.session_state.pending_reviews[doc_id] = {
                    "title": pd_state['p_name'],
                    "module": "RIAku (Akustikk)",
                    "drafter": "Builtly AI",
                    "reviewer": "Senior Akustiker",
                    "status": status,
                    "class": badge,
                    "pdf_bytes": pdf_data
                }
                
                st.session_state.generated_aku_pdf = pdf_data
                st.session_state.generated_aku_filename = f"Builtly_RIAku_{p_name}.pdf"
                st.rerun() 
                
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")

# --- NEDLASTING OG NAVIGASJON ---
if "generated_aku_pdf" in st.session_state:
    st.success("✅ RIAku Rapport er generert og lagt i QA-køen!")
    
    col_dl, col_qa = st.columns(2)
    with col_dl:
        st.download_button("📄 Last ned Akustikkrapport", st.session_state.generated_aku_pdf, st.session_state.generated_aku_filename, type="primary", use_container_width=True)
    with col_qa:
        if find_page("Review"):
            if st.button("🔍 Gå til QA for å vurdere", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))
