import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
from datetime import datetime
import tempfile
import re
import requests
import numpy as np
from PIL import Image

# --- 1. KONFIGURASJON ---
st.set_page_config(page_title="Miljøgeologi | Builtly AI", page_icon="🌍", layout="wide")
genai.configure(api_key="AIzaSyCMsSGwIy7necJYMEjI1BSNY4A-OEHW9eM")

st.markdown("""
    <style>
    div.stButton > button[kind="primary"] {
        height: 50px !important; width: 100% !important;
        font-size: 16px !important; font-weight: bold !important;
        background-color: #1A2B48 !important; border-radius: 8px !important;
        border: none !important; margin-top: 15px;
    }
    div.stButton > button[kind="secondary"] { border-radius: 8px !important; }
    .stTextArea textarea { font-size: 14px !important; }
    </style>
""", unsafe_allow_html=True)

# --- 2. API INTEGRASJON ---
def fetch_kartverket_data(kommune, gnr, bnr):
    if not gnr or not bnr: return None, "Vennligst fyll ut minst Gnr og Bnr."
    sokestreng = f"{kommune} {gnr}/{bnr}".strip()
    url = f"https://ws.geonorge.no/adresser/v1/sok?sok={sokestreng}&treffPerSide=1"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            adresser = data.get("adresser", [])
            if adresser:
                adr = adresser[0]
                nord = adr.get("representasjonspunkt", {}).get("nord", "Ukjent")
                ost = adr.get("representasjonspunkt", {}).get("øst", "Ukjent")
                adressenavn = adr.get("adressetekst", "Uten offisiell vegadresse")
                kommune_navn = adr.get("kommunenavn", kommune)
                info = f"Eiendommen ligger i {kommune_navn} kommune. Adresse: {adressenavn}. Senterkoordinater (UTM33): Nord {nord}, Øst {ost}."
                return info, "✅ Eiendomsdata hentet suksessfullt."
            return None, f"Fant ingen data på '{sokestreng}' i Kartverkets base."
        return None, f"API-feil (Status {response.status_code})."
    except Exception as e: return None, f"Tilkoblingsfeil: {e}"

# --- 3. PROFESJONELL PDF-MOTOR ---
class BuiltlyPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font('Helvetica', '', 8); self.set_text_color(100, 100, 100)
            self.set_xy(25, 10)
            self.cell(100, 10, clean_pdf_text("Builtly AI | Miljøgeologisk rapport"), 0, 0, 'L')
            self.set_xy(155, 10)
            self.cell(30, 10, "builtly.ai", 0, 1, 'R')
            self.set_draw_color(200, 200, 200)
            self.line(25, 18, 185, 18)
            self.set_xy(25, 25)

    def footer(self):
        self.set_y(-15); self.set_font('Helvetica', 'I', 8); self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f'UTKAST - KREVER FAGLIG KONTROLL | Side {self.page_no()}'), 0, 0, 'C')

    def check_space(self, height):
        if self.get_y() + height > 270: self.add_page()

    def add_side_by_side_images(self, img1, desc1, img2, desc2):
        if not img1 and not img2: return
        self.check_space(100); self.ln(6)
        y_start = self.get_y()
        x1, x2, w_img = 25, 110, 75
        h1 = h2 = 0
        
        if img1:
            img_obj = Image.open(img1)
            h1 = w_img * (img_obj.height / img_obj.width)
            img1.seek(0)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp1:
                tmp1.write(img1.getvalue()); tmp1_path = tmp1.name
            self.image(tmp1_path, x=x1, y=y_start, w=w_img); os.unlink(tmp1_path)
            
        if img2:
            img_obj = Image.open(img2)
            h2 = w_img * (img_obj.height / img_obj.width)
            img2.seek(0)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp2:
                tmp2.write(img2.getvalue()); tmp2_path = tmp2.name
            self.image(tmp2_path, x=x2, y=y_start, w=w_img); os.unlink(tmp2_path)
            
        self.set_y(y_start + max(h1, h2) + 3)
        curr_y = self.get_y()
        self.set_font('Helvetica', 'B', 8); self.set_text_color(50, 50, 50)
        if img1: self.set_xy(x1, curr_y); self.cell(75, 4, clean_pdf_text("Figur 1: Dagens situasjon"), 0, 0, 'C')
        if img2: self.set_xy(x2, curr_y); self.cell(75, 4, clean_pdf_text("Figur 2: Historisk bilde"), 0, 0, 'C')
        self.ln(5)
        
        desc_y = self.get_y()
        self.set_font('Helvetica', 'I', 8); self.set_text_color(100, 100, 100)
        max_y = desc_y
        if img1: self.set_xy(x1, desc_y); self.multi_cell(75, 4, clean_pdf_text(desc1), 0, 'C'); max_y = max(max_y, self.get_y())
        if img2: self.set_xy(x2, desc_y); self.multi_cell(75, 4, clean_pdf_text(desc2), 0, 'C'); max_y = max(max_y, self.get_y())
        self.set_y(max_y + 10); self.set_x(25)

def clean_pdf_text(text):
    if not text: return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*"}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def create_pdf(project_name, client_name, location, content, img_today, desc_today, img_hist, desc_hist, df):
    pdf = BuiltlyPDF()
    pdf.set_margins(left=25, top=25, right=25)
    pdf.set_auto_page_break(auto=True, margin=25)
    
    # --- SIDE 1: FORSIDE ---
    pdf.add_page()
    if os.path.exists("logo.png"): pdf.image("logo.png", x=25, y=15, w=85)
    pdf.set_y(100); pdf.set_font('Helvetica', 'B', 28); pdf.set_text_color(26, 43, 72)
    pdf.multi_cell(0, 12, clean_pdf_text('MILJØTEKNISK RAPPORT OG TILTAKSPLAN'), 0, 'L')
    pdf.ln(15); pdf.set_font('Helvetica', '', 18); pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"PROSJEKT: {project_name.upper()}"), 0, 1, 'L')
    pdf.ln(35)
    for l, v in [("OPPDRAGSGIVER:", client_name), ("LOKASJON:", location), ("DATO:", datetime.now().strftime("%d. %m. %Y"))]:
        pdf.set_font('Helvetica', 'B', 10); pdf.set_text_color(120, 120, 120); pdf.cell(50, 8, l, 0, 0, 'L')
        pdf.set_font('Helvetica', '', 10); pdf.set_text_color(0, 0, 0); pdf.cell(0, 8, clean_pdf_text(v), 0, 1, 'L')

    pdf.set_y(220); pdf.set_font('Helvetica', 'B', 12); pdf.set_text_color(200, 50, 50)
    pdf.cell(0, 8, clean_pdf_text("DOKUMENTSTATUS: UTKAST (IKKE GODKJENT)"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 10); pdf.set_text_color(100, 100, 100)
    disclaimer = "Dette dokumentet er generert av Builtly AI og er å anse som et foreløpig utkast. Dokumentet er ikke juridisk eller faglig gyldig før innholdet er kvalitetssikret, og rapporten er formelt signert av ansvarlig senior fagingeniør (RIG-M)."
    pdf.multi_cell(0, 5, clean_pdf_text(disclaimer), 0, 'L')

    # --- SIDE 2: INNHOLDSFORTEGNELSE ---
    pdf.add_page(); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72); pdf.cell(0, 20, 'INNHOLDSFORTEGNELSE', 0, 1, 'L'); pdf.ln(5)
    toc = ["1. SAMMENDRAG", "2. INNLEDNING OG REGELVERK", "3. OMRÅDEBESKRIVELSE OG HISTORIKK", "4. UTFØRTE UNDERSØKELSER OG METODIKK", "5. RESULTATER OG VURDERING", "6. TILTAKSPLAN FOR MASSEHÅNDTERING", "7. KONKLUSJON", "VEDLEGG 1: ANALYSEDATA"]
    pdf.set_font('Helvetica', '', 12); pdf.set_text_color(30, 30, 30)
    for i in toc:
        pdf.cell(0, 10, clean_pdf_text(i), 0, 1, 'L'); pdf.set_draw_color(220, 220, 220); pdf.line(25, pdf.get_y(), 185, pdf.get_y()); pdf.ln(2)

    # --- SIDE 3+: RAPPORTSTART ---
    pdf.add_page()
    content = content[content.find('#'):] if '#' in content else content
    images_placed = False  # BILDELÅS FOR Å HINDRE DUPLIKATER
    
    for line in content.split('\n'):
        line = line.strip()
        if not line: 
            pdf.ln(3); continue
        if "INNHOLDSFORTEGNELSE" in line.upper(): continue

        if line.startswith('#'):
            pdf.check_space(35)
            level = line.count('#')
            title = line.replace('#', '').strip()
            if level == 1 and pdf.page_no() > 2 and pdf.get_y() > 60: pdf.add_page()
                
            pdf.ln(8)
            if level == 1: pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72)
            elif level == 2: pdf.set_font('Helvetica', 'B', 13); pdf.set_text_color(40, 40, 40)
            else: pdf.set_font('Helvetica', 'B', 11); pdf.set_text_color(60, 60, 60)
            
            pdf.cell(0, 8, clean_pdf_text(title), 0, 1, 'L'); pdf.ln(2)
            pdf.set_font('Helvetica', '', 10); pdf.set_text_color(30, 30, 30)
            
            # Setter kun inn bilder ÉN gang, og utelukkende under kapittel 3
            if title.startswith('3') and not images_placed:
                pdf.add_side_by_side_images(img_today, desc_today, img_hist, desc_hist)
                images_placed = True
                
        else:
            if line.startswith('**') or line.startswith('- **') or line.startswith('* **'): pdf.ln(4) 
            parts = re.split(r'(\*\*.*?\*\*)', line)
            for p in parts:
                if p.startswith('**') and p.endswith('**'):
                    pdf.set_font('Helvetica', 'B', 10); pdf.write(5, clean_pdf_text(p.replace('**', '')))
                else:
                    pdf.set_font('Helvetica', '', 10); pdf.write(5, clean_pdf_text(p))
            pdf.ln(6)

    # --- VEDLEGG 1: PERFEKT VASKET TABELL ---
    if df is not None and not df.empty:
        pdf.add_page()
        pdf.set_font('Helvetica', 'B', 14); pdf.set_text_color(26, 43, 72); pdf.cell(0, 10, 'VEDLEGG 1: Analysedata', 0, 1, 'L'); pdf.ln(5)
        
        # Setter en trygg grense på maks 7 kolonner for A4-ark
        num_cols = min(7, len(df.columns))
        plot_df = df.iloc[:, :num_cols].copy()
        
        # Korter ned tekst som er altfor lang
        for col in plot_df.columns:
            plot_df[col] = plot_df[col].astype(str).apply(lambda x: x[:30] + '..' if len(x) > 30 else x)
            
        col_w = 160 / num_cols if num_cols > 0 else 160
        line_h = 4
        
        def print_table_header():
            pdf.set_font('Helvetica', 'B', 7); pdf.set_fill_color(240, 240, 240); pdf.set_text_color(0, 0, 0)
            for c in plot_df.columns: 
                pdf.cell(col_w, 8, clean_pdf_text(str(c)[:25]), 1, 0, 'C', True)
            pdf.ln()

        if num_cols > 0:
            print_table_header()
            pdf.set_font('Helvetica', '', 6)
            
            for _, r in plot_df.iterrows():
                # Hopper over rader som er helt tomme
                if all(v == '' for v in r): continue
                
                if pdf.get_y() > 250: 
                    pdf.add_page(); print_table_header(); pdf.set_font('Helvetica', '', 6)
                
                lines = [len(pdf.multi_cell(col_w, line_h, clean_pdf_text(str(v)), split_only=True)) for v in r]
                row_h = (max(lines) * line_h) + 2 if lines else line_h + 2 
                
                x, y = pdf.get_x(), pdf.get_y()
                for v in r:
                    pdf.rect(x, y, col_w, row_h)
                    pdf.set_xy(x, y + 1)
                    pdf.multi_cell(col_w, line_h, clean_pdf_text(str(v)), 0, 'C')
                    x += col_w
                pdf.set_xy(25, y + row_h)

    return bytes(pdf.output(dest='S'))

# --- 4. UI ---
def get_model():
    try:
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        flash_model = next((m for m in models if "flash" in m), "models/gemini-1.5-flash")
        return genai.GenerativeModel(flash_model)
    except:
        return genai.GenerativeModel('gemini-1.5-flash')

if st.button("⬅ Dashboard", type="secondary"): st.switch_page("Builtly_AI.py")
st.title("🌍 Miljøgeologi (RIG-M)")
st.divider()

st.subheader("Eiendom & API Oppslag")
c1, c2, c3, c4 = st.columns(4)
with c1: land = st.selectbox("Land", ["Norge", "Sverige", "Danmark"])
with c2: kommune = st.text_input("Kommune", placeholder="f.eks. Trondheim")
with c3: gnr = st.text_input("Gnr", placeholder="316")
with c4: bnr = st.text_input("Bnr", placeholder="689")

if st.button("Hent data fra Kartverket 🔍", type="secondary"):
    with st.spinner("Søker i Geonorge API..."):
        api_data, msg = fetch_kartverket_data(kommune, gnr, bnr)
        if api_data:
            st.session_state['eiendomsdata'] = api_data
            st.success(msg); st.info(api_data)
        else:
            st.session_state['eiendomsdata'] = f"Gnr {gnr} / Bnr {bnr} i {kommune}."
            st.warning(msg)

st.divider()

st.subheader("Prosjektdetaljer")
col_p1, col_p2, col_p3 = st.columns(3)
with col_p1: prosjekt = st.text_input("Prosjektnavn", placeholder="Saga Park")
with col_p2: kunde = st.text_input("Oppdragsgiver")
with col_p3:
    arealbruk = st.selectbox("Planlagt arealbruk", ["Bolig, barnehage, lekeplass", "Kontor, handel, park", "Industri, lager, samferdsel"])

tiltaksbeskrivelse = st.text_area("Kort beskrivelse av planlagt tiltak (Byggeprosjektet)", value="Prosjektet omfatter oppføring av boliger med underliggende kjeller.", height=100)

st.divider()
st.subheader("📸 Dokumentasjon")
c_img1, c_img2 = st.columns(2)
with c_img1:
    img_today = st.file_uploader("Figur 1: Dagens situasjon", type=['jpg', 'png', 'jpeg'])
    desc_today = st.text_area("Beskrivelse (Dagens)")
with c_img2:
    img_hist = st.file_uploader("Figur 2: Historisk bilde", type=['jpg', 'png', 'jpeg'])
    desc_hist = st.text_area("Beskrivelse (Historisk)")

st.divider()
uploaded_file = st.file_uploader("Last opp Excel-analysedata", type=['xlsx'])

if uploaded_file:
    df = pd.read_excel(uploaded_file)
    
    # --- KRAFTIG DATAVASKEMASKIN FOR EXCEL ---
    df.dropna(how='all', inplace=True) # Fjerner rader som er helt tomme
    df.dropna(axis=1, how='all', inplace=True) # Fjerner kolonner som er helt tomme
    df = df.fillna('') # Bytter ut 'nan' med tomme strenger
    
    # Fjerner "Unnamed" fra kolonneoverskriftene
    renamed_cols = []
    for col in df.columns:
        if str(col).startswith("Unnamed"): renamed_cols.append("")
        else: renamed_cols.append(str(col))
    df.columns = renamed_cols

    c_btn1, c_btn2, c_btn3 = st.columns([1, 2, 1])
    with c_btn2:
        if st.button("Generer Miljøteknisk rapport", type="primary"):
            with st.spinner("AI utarbeider komplett rapport..."):
                try:
                    model = get_model()
                    api_facts = st.session_state.get('eiendomsdata', f"{kommune}, Gnr/Bnr {gnr}/{bnr}")
                    lokasjon_tekst = f"{kommune}, Gnr/Bnr {gnr}/{bnr}" if kommune else "Lokasjon ikke angitt"
                    
                    # STRENG PROMPT FOR Å HINDRE FEIL FIRMANAVN
                    prompt = f"""
                    Du er senior miljørådgiver (RIG-M) hos Builtly AI. 
                    Skriv en dyptgående, profesjonell miljøteknisk rapport for '{prosjekt}'.
                    
                    FAKTAGRUNNLAG:
                    - Eiendomsdata: {api_facts}
                    - Analysedata: {df.head(60).to_string()}
                    - Planlagt arealbruk: {arealbruk}
                    - Byggetiltak: {tiltaksbeskrivelse}
                    - Bilder (Figur 1: {desc_today}, Figur 2: {desc_hist})
                    
                    EKSTREMT VIKTIG KRAV TIL DEG:
                    Oppdragsgiveren din er KUN '{kunde}'. Hvis du ser navn som "Multiconsult", andre firmanavn, eller personnavn inne i analysedataene/Excel, skal du ABSOLUTT IKKE nevne dem! Bruk kun tallene og forurensningsverdiene fra dataene.
                    
                    OBLIGATORISK STRUKTUR:
                    # 1. SAMMENDRAG
                    # 2. INNLEDNING OG REGELVERK
                    # 3. OMRÅDEBESKRIVELSE OG HISTORIKK
                    # 4. UTFØRTE UNDERSØKELSER OG METODIKK
                    # 5. RESULTATER OG VURDERING
                    # 6. TILTAKSPLAN FOR MASSEHÅNDTERING
                    # 7. KONKLUSJON
                    """
                    
                    response = model.generate_content(prompt)
                    pdf_data = create_pdf(prosjekt, kunde, lokasjon_tekst, response.text, img_today, desc_today, img_hist, desc_hist, df)
                    st.success("✅ Rapport generert uten støydata!")
                    st.download_button("📄 Last ned rapport (PDF)", pdf_data, f"Builtly_Miljo_{prosjekt}.pdf", "application/pdf")
                except Exception as e: st.error(f"Feil ved generering: {e}")