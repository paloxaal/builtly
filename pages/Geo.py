import streamlit as st
import google.generativeai as genai
from fpdf import FPDF
import os
from datetime import datetime
import re

st.set_page_config(page_title="Miljø & Geo (RIG-M) | Builtly", layout="wide")

if os.environ.get("GOOGLE_API_KEY"): genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
else: st.error("Mangler API-nøkkel!"); st.stop()

def clean_pdf_text(text):
    if not text: return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*"}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    text = text.replace('$', '').replace('*', '').replace('_', '')
    return clean_pdf_text(re.sub(r'[-|=]{3,}', ' ', text))

if "project_data" in st.session_state: pd_state = st.session_state.project_data
else: pd_state = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": ""}

class BuiltlyProPDF(FPDF):
    def footer(self):
        self.set_y(-15); self.set_font('Helvetica', 'I', 8); self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f'UTKAST - KREVER FAGLIG KONTROLL | Side {self.page_no()}'), 0, 0, 'C')

def create_full_report_pdf(name, client, content):
    pdf = BuiltlyProPDF(); pdf.set_margins(25, 25, 25); pdf.set_auto_page_break(True, 25); pdf.add_page()
    if os.path.exists("logo.png"): pdf.image("logo.png", x=25, y=20, w=50)
    pdf.set_y(100); pdf.set_font('Helvetica', 'B', 24); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("TILTAKSPLAN FORURENSET GRUNN (RIG-M)"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 16); pdf.set_text_color(0, 0, 0); pdf.cell(0, 10, clean_pdf_text(f"PROSJEKT: {name.upper()}"), 0, 1, 'L'); pdf.ln(30)
    for l, v in [("OPPDRAGSGIVER:", client), ("DATO:", datetime.now().strftime("%d. %m. %Y")), ("UTARBEIDET AV:", "Builtly RIG-M AI")]:
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
    return bytes(pdf.output(dest='S'))

st.title("🌍 RIG-M — Miljø & Geo")
if pd_state["p_name"]: st.success("✅ Eiendomsdata synkronisert fra SSOT.")

with st.expander("1. Eiendom & Lokasjon (Auto-synced)", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", value=pd_state["p_name"])
    c_name = c2.text_input("Oppdragsgiver", value=pd_state["c_name"])
    c3, c4, c5 = st.columns(3)
    kommune = c3.text_input("Kommune", value=pd_state["kommune"])
    gnr = c4.text_input("Gnr", value=pd_state["gnr"])
    bnr = c5.text_input("Bnr", value=pd_state["bnr"])

with st.expander("2. Grunnforhold & Analyseresultater", expanded=True):
    st.info("💡 I produksjon vil denne modulen lese Excel-filer fra ALS Laboratory direkte. Her setter vi foreløpig status.")
    tilstand = st.selectbox("Høyeste påviste tilstandsklasse i masser:", ["Tilstandsklasse 1 (Rene masser)", "Tilstandsklasse 2-3 (Moderat forurenset)", "Tilstandsklasse 4-5 (Farlig avfall)"])
    st.file_uploader("Last opp excel-fil fra ALS / Eurofins (Valgfritt)")

if st.button("Kjør RIG-M Analyse", type="primary"):
    with st.spinner("🤖 Genererer Tiltaksplan iht. Forurensningsforskriften..."):
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""
        Skriv en formell "Tiltaksplan for forurenset grunn" for:
        PROSJEKT: {p_name}. LOKASJON: Gnr {gnr}/ Bnr {bnr}, {kommune}.
        PÅVIST FORURENSNING: {tilstand}.
        KUNDENS BESKRIVELSE AV GRAVEARBEIDER: "{pd_state['p_desc']}"
        
        INSTRUKSER: Skriv iht. Forurensningsforskriften kapittel 2. Gi tydelige krav til entreprenør for håndtering av massene, mellomlagring og levering til godkjent deponi.
        
        # 1. SAMMENDRAG
        # 2. INNLEDNING OG EIENDOMSFORHOLD
        # 3. HISTORISK BRUK AV TOMTEN
        # 4. RESULTATER FRA GRUNNUNDERSØKELSER
        # 5. VURDERING AV HELSE- OG MILJØRISIKO
        # 6. TILTAKSPLAN OG KRAV TIL ENTREPRENØR
        """
        try:
            res = model.generate_content(prompt)
            pdf_data = create_full_report_pdf(p_name, c_name, res.text)
            st.success("✅ RIG-M Rapport ferdig!")
            st.download_button("📄 Last ned Tiltaksplan", pdf_data, f"Builtly_RIGM_{p_name}.pdf")
        except Exception as e: st.error(e)
