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

# --- 2. PREMIUM CSS ---
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

    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    .brand-left { display: flex; align-items: center; gap: 0.9rem; min-width: 0; }
    
    .topbar-right {
        display: flex; align-items: center; justify-content: flex-end; gap: 0.65rem;
        padding: 0.35rem; border-radius: 18px; background: rgba(255,255,255,0.025);
        border: 1px solid rgba(120,145,170,0.12); flex-wrap: nowrap !important;
    }
    .top-link {
        display: inline-flex; align-items: center; justify-content: center; min-height: 42px;
        padding: 0.72rem 1.2rem; border-radius: 12px; text-decoration: none !important;
        font-weight: 650; font-size: 0.93rem; transition: all 0.2s ease; border: 1px solid transparent;
        white-space: nowrap;
    }
    .top-link.ghost { color: var(--soft) !important; background: rgba(255,255,255,0.04); border-color: rgba(120,145,170,0.18); }
    .top-link.ghost:hover { color: #ffffff !important; border-color: rgba(56,194,201,0.38); background: rgba(255,255,255,0.06); }

    button[kind="primary"] {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important; border: none !important; font-weight: 750 !important;
        border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    
    .stTextInput input, .stNumberInput input, .stTextArea textarea {
        background-color: #0d1824 !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important;
        border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {
        border-color: #38bdf8 !important; box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.5) !important;
    }
    div[data-baseweb="select"] > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    div[data-baseweb="select"] span { color: #ffffff !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label {
        color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important;
    }
    
    div[data-testid="stExpander"] { background: #0c1520 !important; border: 1px solid rgba(120,145,170,0.2) !important; border-radius: 12px !important; margin-bottom: 1rem !important; }
    div[data-testid="stExpanderDetails"] { background: transparent !important; }
    div[data-testid="stExpander"] summary:hover { background: rgba(255,255,255,0.02) !important; }
    
    [data-testid="stFileDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; padding: 2rem !important; }
    [data-testid="stFileDropzone"]:hover { border-color: #38bdf8 !important; background-color: rgba(56, 189, 248, 0.05) !important; }
    [data-testid="stFileDropzone"] * { color: #c8d3df !important; }
    
    .card { background: linear-gradient(180deg, rgba(16,30,46,0.8), rgba(10,18,28,0.8)); border: 1px solid var(--stroke); border-radius: var(--radius-xl); padding: 1.8rem; box-shadow: 0 12px 30px rgba(0,0,0,0.2); }
</style>
""", unsafe_allow_html=True)

# --- 3. HEADER UI ---
logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
home_link = '<a href="Project" target="_self" class="top-link ghost">← Tilbake til SSOT</a>'

render_html(f"""
<div class="top-shell">
    <div class="brand-left">
        {logo_html}
    </div>
    <div class="topbar-right">
        {home_link}
    </div>
</div>
""")

# --- 4. GUARDRAIL LÅS ---
if "project_data" not in st.session_state or st.session_state.project_data.get("p_name") in ["", "Nytt Prosjekt"]:
    render_html("""
    <div style="display: flex; justify-content: center; margin-top: 4rem;">
        <div class="card" style="max-width: 600px; text-align: center; padding: 4rem 3rem; border-color: rgba(244, 191, 79, 0.3);">
            <div style="font-size: 4rem; margin-bottom: 1rem;">🚧</div>
            <h2 style="color: #f5f7fb; font-size: 2.2rem; font-weight: 800; margin-bottom: 1rem; letter-spacing: -0.03em;">Prosjektdata mangler</h2>
            <p style="color: #9fb0c3; line-height: 1.7; font-size: 1.05rem; margin-bottom: 2.5rem;">
                For at AI-agenten skal kunne koble seg på riktig regelverk og analysere riktig bygningsmasse, må du definere prosjektet i Master Data (SSOT) først.
            </p>
            <div class="topbar-right" style="justify-content: center; border: none; background: transparent;">
                <a href="Project" target="_self" class="top-link primary" style="font-size: 1.05rem; padding: 0.8rem 1.5rem;">⚙️ Åpne Project Setup</a>
            </div>
        </div>
    </div>
    """)
    st.stop()

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
    
    for t in ["1. SAMMENDRAG OG KONKLUSJON", "2. PROSJEKTBESKRIVELSE OG REGELVERK", "3. KLASSIFISERING", "4. RØMNINGSFORHOLD OG LEDESYSTEM", "5. BRANNCELLER OG BRANNMOTSTAND", "6. SLOKKEUTSTYR OG REDNINGSBRANNVESEN", "VEDLEGG: TEGNINGSGRUNNLAG"]:
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
                if img_h > 240: # Skalerer ned hvis bildet er for høyt for siden
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
st.markdown("<p style='color: var(--muted); font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for generering av teknisk brannkonsept basert på arkitektur og situasjonsplan.</p>", unsafe_allow_html=True)

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

with st.expander("3. Visuelt Grunnlag (Arkitektur & Situasjonsplan)", expanded=True):
    st.info("Viktig: For en komplett vurdering må AI-en se både innvendig planløsning (rømning) og utvendig situasjonsplan (tilkomst brannbil, smittefare).")
    files = st.file_uploader("Last opp plantegninger, snitt og SITUASJONSPLAN (PDF/Bilder)", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🚀 Kjør Brannteknisk Analyse (RIBr)", type="primary", use_container_width=True):
    
    images_for_ai = [] 
    
    if files:
        with st.spinner("📐 Henter ut bilder fra dokumentene for visuell AI-analyse..."):
            try:
                for f in files: 
                    if f.name.lower().endswith('pdf'):
                        if fitz is None: st.stop()
                        doc = fitz.open(stream=f.read(), filetype="pdf")
                        for page_num in range(min(3, len(doc))): # Henter max 3 sider per PDF for å ikke sprenge minnet
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
        
        VISUELL ANALYSE AV VEDLAGTE TEGNINGER OG SITUASJONSPLAN:
        Jeg har lagt ved bilder av bygget og/eller terrenget rundt. Din oppgave er å NØYE analysere disse:
        1. Innvendig (Plantegninger): Identifiser trapperom, rømningsveier og rominndeling. Hvor bør branncellene gå?
        2. Utvendig (Situasjonsplan/Utomhus): Vurder hvor brannbiler kan kjøre inn og plasseres (oppstillingsplass / tilkomst for innsatsmannskaper). Vurder rømning ut på terreng til sikkert sted. Vurder også avstand til nabobygg med tanke på smittefare.
        
        INSTRUKSER (Skriv formelt, teknisk tungt og presist, min 1200 ord):
        - Flett inn dine spesifikke observasjoner fra bildene i teksten.
        - Ta spesifikt stilling til adkomst for brannvesen og brannsmitte i rapporten.
        
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
            
            with st.spinner("Kompilerer Brann-PDF med vedlagte tegninger..."):
                pdf_data = create_full_report_pdf(p_name, c_name, res.text, images_for_ai)
            st.success("✅ RIBr Rapport er ferdigstilt!")
            st.download_button("📄 Last ned Brannkonsept", pdf_data, f"Builtly_RIBr_{p_name}.pdf", type="primary")
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")
