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

# --- 1. TEKNISK OPPSETT & ANTI-BUG RENDERER ---
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

# --- 2. PREMIUM CSS (MÅ VÆRE HER OPPE!) ---
st.markdown("""
<style>
    :root {
        --bg: #06111a; --panel: rgba(10, 22, 35, 0.78);
        --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3;
        --accent: #38bdf8; --radius-lg: 16px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }

    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    
    .back-btn {
        color: var(--accent); text-decoration: none; font-weight: 600; 
        display: inline-flex; align-items: center; gap: 8px; font-size: 1.05rem;
        padding: 8px 16px; border-radius: 8px; border: 1px solid rgba(56,189,248,0.2);
        background: rgba(56,189,248,0.05); transition: all 0.2s;
    }
    .back-btn:hover { background: rgba(56,189,248,0.15); transform: translateX(-2px); }

    button[kind="primary"] {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important; border: none !important; font-weight: 750 !important;
        border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] {
        background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important;
        border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 8px !important; font-weight: 600 !important;
    }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: #38bdf8 !important; color: #38bdf8 !important; }

    .stTextInput input, .stNumberInput input, .stTextArea textarea {
        background-color: #0d1824 !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important;
        border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {
        border-color: #38bdf8 !important; box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.5) !important;
    }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label {
        color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important;
    }
    
    div[data-testid="stExpander"] { background: rgba(16, 30, 46, 0.5); border: 1px solid rgba(120,145,170,0.2); border-radius: 12px; margin-bottom: 1rem; }
</style>
""", unsafe_allow_html=True)

# --- 3. HENT DATA FRA SSOT (MED GUARDRAIL LÅS) ---
logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0;">Builtly</h2>'
home_link = '<a href="Project" target="_self" class="back-btn">← Tilbake til SSOT</a>'

if "project_data" not in st.session_state or st.session_state.project_data.get("p_name") in ["", "Nytt Prosjekt"]:
    render_html(f"""<div class="top-shell"><div>{logo_html}</div></div>""")
    st.warning("⚠️ **Handling kreves: Du må sette opp prosjektet først.**")
    st.info("Denne AI-agenten krever at adresse, bygningsdata og regelverk er satt opp i Master Data (SSOT) før den kan generere en faglig korrekt miljørapport.")
    st.markdown("<br>", unsafe_allow_html=True)
    st.page_link("pages/Project.py", label="Gå til Project Setup", icon="⚙️")
    st.stop()

pd_state = st.session_state.project_data
if "geo_maps" not in st.session_state:
    st.session_state.geo_maps = {"recent": None, "historical": None, "source": "Ikke hentet"}

# --- 4. HEADER ---
render_html(f"""<div class="top-shell"><div>{logo_html}</div><div>{home_link}</div></div>""")
st.markdown(f"<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🌍 Geo & Miljø (RIG-M)</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: var(--muted); font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for miljøteknisk grunnundersøkelse og tiltaksplan.</p>", unsafe_allow_html=True)

# --- 5. KARTKATALOGEN API ---
def fetch_kartverket_data(adresse, kommune, gnr, bnr):
    adr_clean = adresse.replace(',', '').strip() if adresse else ""
    kom_clean = kommune.replace(',', '').strip() if kommune else ""

    def api_call(query_string):
        if not query_string.strip(): return None, None, None, None
        safe_query = urllib.parse.quote(query_string)
        url = f"https://ws.geonorge.no/adresser/v1/sok?sok={safe_query}&fuzzy=true&utkoordsys=25833&treffPerSide=1"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200 and resp.json().get('adresser'):
                hit = resp.json()['adresser'][0]
                return hit.get('adressetekst', query_string), hit.get('kommunenavn', 'Ukjent'), hit.get('representasjonspunkt', {}).get('nord'), hit.get('representasjonspunkt', {}).get('øst')
        except Exception: pass
        return None, None, None, None

    queries = []
    if adr_clean:
        if kom_clean: queries.append(f"{adr_clean} {kom_clean}")
        queries.append(adr_clean) 
        base_num = re.sub(r'(\d+)[a-zA-Z]+', r'\1', adr_clean)
        if base_num != adr_clean: queries.append(base_num)
            
    for q in queries:
        adr_tekst, kom, nord, ost = api_call(q)
        if nord and ost: return f"✅ Lokasjon bekreftet (N {nord}, Ø {ost}).", nord, ost

    if kom_clean:
        url_sted = f"https://ws.geonorge.no/stedsnavn/v1/navn?sok={urllib.parse.quote(kom_clean)}&utkoordsys=25833&treffPerSide=1"
        try:
            resp = requests.get(url_sted, timeout=5)
            if resp.status_code == 200 and resp.json().get('navn'):
                hit = resp.json()['navn'][0]
                nord, ost = hit.get('representasjonspunkt', {}).get('nord'), hit.get('representasjonspunkt', {}).get('øst')
                if nord and ost: return f"⚠️ Bruker senter av {kom_clean} (N {nord}, Ø {ost}).", nord, ost
        except Exception: pass

    return "❌ Fant ingen treff i Kartverket. Last opp kart manuelt.", None, None

def fetch_kartverket_flyfoto(nord, ost):
    try:
        if not nord or not ost: return None, "Mangler koordinater"
        min_x, max_x = float(ost) - 150, float(ost) + 150
        min_y, max_y = float(nord) - 150, float(nord) + 150
        headers = {'User-Agent': 'Mozilla/5.0'}
        url_orto = f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
        try:
            r1 = requests.get(url_orto, headers=headers, timeout=6)
            if r1.status_code == 200 and len(r1.content) > 5000:
                return Image.open(io.BytesIO(r1.content)).convert('RGB'), "Ortofoto (Flyfoto)"
        except Exception: pass
    except Exception: pass
    return None, "Feil ved nedlasting"

# --- 6. DATA EXTRACTOR ---
def extract_drill_data(files):
    extracted_text = ""
    for f in files:
        if f.name.lower().endswith(('.xlsx', '.xls', '.csv')):
            try:
                df = pd.read_csv(f) if f.name.lower().endswith('.csv') else pd.read_excel(f)
                extracted_text += f"\n--- RÅDATA FRA FIL: {f.name.upper()} ---\n{df.head(100).to_string()}\n\n"
            except Exception as e:
                extracted_text += f"\n[Feil ved lesing av {f.name}: {e}]\n"
    return extracted_text if extracted_text else "Ingen Excel/CSV-data ble lastet opp."

# --- 7. DYNAMISK PDF MOTOR ---
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
    
    # FORSIDE
    pdf.add_page()
    if os.path.exists("logo.png"): pdf.image("logo.png", x=25, y=20, w=50)
    pdf.set_y(100); pdf.set_font('Helvetica', 'B', 24); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("GEOTEKNISK & MILJØTEKNISK RAPPORT"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 16); pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"INKLUDERT TILTAKSPLAN: {pdf.p_name}"), 0, 1, 'L'); pdf.ln(30)
    
    metadata = [
        ("OPPDRAGSGIVER:", client), 
        ("DATO:", datetime.now().strftime("%d. %m. %Y")), 
        ("UTARBEIDET AV:", "Builtly RIG-M AI Engine"), 
        ("REGELVERK:", pd_state.get('land', 'Ukjent'))
    ]
    for l, v in metadata:
        pdf.set_x(25); pdf.set_font('Helvetica', 'B', 10); pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10); pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    # INNHOLDSFORTEGNELSE
    pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1); pdf.ln(5)
    toc = [
        "1. SAMMENDRAG OG KONKLUSJON", "2. INNLEDNING OG PROSJEKTBESKRIVELSE", 
        "3. KARTVERKET OG HISTORISK LOKASJON", "4. UTFØRTE GRUNNUNDERSØKELSER", 
        "5. RESULTATER: GRUNNFORHOLD OG FORURENSNING", "6. GEOTEKNISKE VURDERINGER", 
        "7. TILTAKSPLAN OG MASSEHÅNDTERING"
    ]
    pdf.set_font('Helvetica', '', 11); pdf.set_text_color(0, 0, 0)
    for t in toc:
        pdf.set_x(25); pdf.cell(0, 10, clean_pdf_text(t), 0, 1); pdf.set_draw_color(220, 220, 220); pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    # INNHOLD
    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: pdf.ln(4); continue
            
        if line.startswith('# 3. KARTVERKET'):
            pdf.check_space(180); pdf.ln(8); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 14); pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip())); pdf.ln(4)
            
            if recent_img and hist_img:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as t1, tempfile.NamedTemporaryFile(delete=False, suffix=".png") as t2:
                    recent_img.save(t1.name, format="PNG"); hist_img.save(t2.name, format="PNG")
                    y_pos = pdf.get_y()
                    pdf.image(t1.name, x=25, y=y_pos, w=75); pdf.image(t2.name, x=110, y=y_pos, w=75)
                    pdf.set_y(y_pos + 75 * (recent_img.height / recent_img.width) + 5)
                    pdf.set_font('Helvetica', 'I', 8); pdf.set_text_color(100, 100, 100); pdf.set_x(25)
                    pdf.cell(75, 5, clean_pdf_text("Fig 1: Nyere situasjon"), 0, 0, 'C'); pdf.set_x(110); pdf.cell(75, 5, clean_pdf_text("Fig 2: Historisk situasjon"), 0, 1, 'C'); pdf.ln(5)
            elif recent_img:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as t1:
                    recent_img.save(t1.name, format="PNG")
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
st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er synkronisert fra Master Setup.")

with st.expander("1. Prosjekt & Lokasjon (SSOT)", expanded=False):
    st.info("Disse dataene hentes fra Project Setup og kan ikke endres her.")
    c1, c2 = st.columns(2)
    st.text_input("Prosjektnavn", value=pd_state["p_name"], disabled=True)
    st.text_input("Gnr/Bnr", value=f"{pd_state['gnr']} / {pd_state['bnr']}", disabled=True)

with st.expander("2. Kartgrunnlag & Ortofoto (Påkrevd)", expanded=True):
    st.markdown("For å vurdere potensialet for forurenset grunn, krever veilederen en visuell bedømming av nyere og historiske flyfoto. AI-en integrerer disse i rapporten.")
    
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🌐 Hent kart fra Kartverket", type="secondary"):
            with st.spinner("Søker i Matrikkel og Kartkatalog..."):
                info, nord, ost = fetch_kartverket_data(pd_state["adresse"], pd_state["kommune"], pd_state["gnr"], pd_state["bnr"])
                if nord and ost:
                    img, source = fetch_kartverket_flyfoto(nord, ost)
                    if img:
                        st.session_state.geo_maps["recent"] = img
                        st.session_state.geo_maps["source"] = source
                        st.success(f"✅ Hentet {source}!")
                    else: st.error("Kunne ikke laste ned bilde fra Kartverket. Vennligst last opp manuelt.")
                else: st.error("Fant ikke koordinater. Vennligst last opp kart manuelt.")
                    
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

with st.expander("3. Laboratoriedata & Borerapport", expanded=True):
    st.info("Last opp Excel/CSV fra miljølaboratorium. AI-en analyserer tilstandsklasser automatisk.")
    files = st.file_uploader("Slipp Excel/CSV-filer her", accept_multiple_files=True, type=['xlsx', 'csv'])

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🚀 GENERER GEOTEKNISK & MILJØTEKNISK RAPPORT", type="primary", use_container_width=True):
    if not st.session_state.geo_maps["recent"] and not st.session_state.geo_maps["historical"]:
        st.error("🛑 **Stopp:** Du må enten hente kart automatisk eller laste opp manuelt i Steg 2.")
        st.stop()
        
    with st.spinner("📊 Analyserer lab-data og prosjektkontekst..."):
        extracted_data = extract_drill_data(files) if files else "Ingen opplastet lab-data. Vurderingen baseres på visuell befaring og historikk."
        
        try:
            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            valgt_modell = 'models/gemini-1.5-pro' if 'models/gemini-1.5-pro' in valid_models else valid_models[0]
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()
        
        model = genai.GenerativeModel(valgt_modell)
        hist_tekst = "Et historisk flyfoto er vedlagt rapporten." if st.session_state.geo_maps["historical"] else "Historisk flyfoto mangler."

        prompt = f"""
        Du er Builtly RIG-M AI, en fagekspert innen geoteknikk og miljøteknikk.
        Skriv en detaljert "Miljøteknisk grunnundersøkelse og tiltaksplan for forurenset grunn" for:
        
        PROSJEKT: {pd_state['p_name']} ({pd_state['b_type']}, {pd_state['bta']} m2)
        LOKASJON: {pd_state['adresse']}, {pd_state['kommune']}. Gnr {pd_state['gnr']}/Bnr {pd_state['bnr']}.
        REGELVERK: {pd_state['land']}
        
        KUNDENS PROSJEKTNARRATIV: "{pd_state['p_desc']}"
        KARTSTATUS: {hist_tekst}
        
        RÅDATA FRA LABORATORIUM (TILSTANDSKLASSER):
        {extracted_data}
        
        INSTRUKSER (Min 1500 ord, formelt fagspråk):
        - Bruk rådataene for å vurdere grunnforhold og forurensning i Kap 5.
        - Skriv konkrete tiltak for håndtering og levering til deponi i Kap 7.
        - Ta utgangspunkt i regelverket ({pd_state['land']}).
        
        STRUKTUR (Bruk KUN disse eksakte overskriftene):
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. INNLEDNING OG PROSJEKTBESKRIVELSE
        # 3. KARTVERKET OG HISTORISK LOKASJON
        # 4. UTFØRTE GRUNNUNDERSØKELSER
        # 5. RESULTATER: GRUNNFORHOLD OG FORURENSNING
        # 6. GEOTEKNISKE VURDERINGER
        # 7. TILTAKSPLAN OG MASSEHÅNDTERING
        """
        
        try:
            res = model.generate_content(prompt)
            with st.spinner("Kompilerer profesjonell PDF med kart..."):
                pdf_data = create_full_report_pdf(pd_state['p_name'], pd_state['c_name'], res.text, st.session_state.geo_maps["recent"], st.session_state.geo_maps["historical"], st.session_state.geo_maps["source"])
            st.success("✅ Komplett geoteknisk utredning og tiltaksplan er ferdigstilt!")
            st.download_button("📄 Last ned Builtly RIG/RIM-rapport", pdf_data, f"Builtly_GEO_{pd_state['p_name'].replace(' ', '_')}.pdf", type="primary")
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")
