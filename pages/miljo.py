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
from PIL import Image

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Geo & Miljø Pro | Builtly AI", layout="wide")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()

def clean_pdf_text(text):
    if not text: return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*", "²": "2", "³": "3"}
    for old, new in rep.items(): 
        text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    """Den ultimate PDF-beskytteren som forhindrer krasj"""
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{3,}', ' ', text)
    # Tvinger orddeling på 40 tegn
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    return clean_pdf_text(text)

# --- 2. KARTVERKET API & FLYFOTO ---
def fetch_kartverket_data(adresse, kommune, gnr, bnr):
    adr_clean = adresse.replace(',', '').strip() if adresse else ""
    kom_clean = kommune.replace(',', '').strip() if kommune else ""
    gnr_clean = gnr.strip() if gnr else ""
    bnr_clean = bnr.strip() if bnr else ""

    def api_call(query_string):
        if not query_string.strip(): return None, None, None, None
        safe_query = urllib.parse.quote(query_string)
        url = f"https://ws.geonorge.no/adresser/v1/sok?sok={safe_query}&utkoordsys=25833&treffPerSide=1"
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200 and resp.json().get('adresser'):
                hit = resp.json()['adresser'][0]
                nord = hit.get('representasjonspunkt', {}).get('nord', None)
                ost = hit.get('representasjonspunkt', {}).get('øst', None)
                kom = hit.get('kommunenavn', 'Ukjent')
                adr_tekst = hit.get('adressetekst', query_string)
                return adr_tekst, kom, nord, ost
        except Exception:
            pass
        return None, None, None, None

    queries = []
    if adr_clean and kom_clean:
        queries.append(f"{adr_clean} {kom_clean}")
        base_num = re.sub(r'(\d+)[a-zA-Z]+', r'\1', adr_clean)
        if base_num != adr_clean: queries.append(f"{base_num} {kom_clean}")
        street_only = re.sub(r'\d+.*', '', adr_clean).strip()
        if street_only: queries.append(f"{street_only} {kom_clean}")
    if gnr_clean and bnr_clean:
        queries.append(f"{kom_clean} {gnr_clean}/{bnr_clean}")

    for q in queries:
        adr_tekst, kom, nord, ost = api_call(q)
        if nord and ost:
            return f"✅ Bekreftet i Kartverket: {adr_tekst}, {kom}. (Koordinater: N {nord}, Ø {ost}).", nord, ost

    return "Fant ingen eksakte treff. Appen fortsetter uten flyfoto.", None, None

def fetch_kartverket_flyfoto(nord, ost):
    try:
        if not nord or not ost: return None
        min_x, max_x = float(ost) - 150, float(ost) + 150
        min_y, max_y = float(nord) - 150, float(nord) + 150
        wms_url = f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
        response = requests.get(wms_url, timeout=10)
        if response.status_code == 200:
            return Image.open(io.BytesIO(response.content))
    except Exception:
        pass
    return None

# --- 3. EXCEL / CSV DATA EXTRACTOR ---
def extract_drill_data(files):
    """Leser Excel-filer fra boreentreprenør og gjør dem om til tekst for AI-en"""
    extracted_text = ""
    for f in files:
        if f.name.lower().endswith(('.xlsx', '.xls', '.csv')):
            try:
                if f.name.lower().endswith('.csv'):
                    df = pd.read_csv(f)
                else:
                    df = pd.read_excel(f)
                
                # Konverterer de første 100 radene til tekst
                extracted_text += f"\n--- RÅDATA FRA FIL: {f.name.upper()} ---\n"
                extracted_text += df.head(100).to_string() + "\n\n"
            except Exception as e:
                extracted_text += f"\n[Feil ved lesing av {f.name}: {e}]\n"
    
    return extracted_text if extracted_text else "Ingen Excel/CSV-data ble lastet opp."

# --- 4. DYNAMISK PDF MOTOR ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: RIM-001"), 0, 1, 'R')
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

def create_full_report_pdf(name, client, content, aerial_photo):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
    # FORSIDE
    pdf.add_page()
    if os.path.exists("logo.png"): 
        pdf.image("logo.png", x=25, y=20, w=50)
    pdf.set_y(100)
    pdf.set_x(25)
    pdf.set_font('Helvetica', 'B', 24)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("GEOTEKNISK & MILJØTEKNISK RAPPORT"), 0, 1, 'L')
    pdf.set_x(25)
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"INKLUDERT TILTAKSPLAN: {pdf.p_name}"), 0, 1, 'L')
    pdf.ln(30)
    
    metadata = [
        ("OPPDRAGSGIVER:", client), 
        ("DATO:", datetime.now().strftime("%d. %m. %Y")), 
        ("UTARBEIDET AV:", "Builtly RIG/RIM AI Engine"), 
        ("KONTROLLERT AV:", "[Ansvarlig Geotekniker/Miljørådgiver]")
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
        "2. INNLEDNING OG PROSJEKTBESKRIVELSE", 
        "3. KARTVERKET OG LOKASJON", 
        "4. UTFØRTE GRUNNUNDERSØKELSER (METODIKK)", 
        "5. RESULTATER: GRUNNFORHOLD OG FORURENSNING", 
        "6. GEOTEKNISKE VURDERINGER (FUNDAMENTERING)", 
        "7. TILTAKSPLAN OG MASSEHÅNDTERING"
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
            
        if line.startswith('# 3. KARTVERKET'):
            pdf.check_space(180) 
            pdf.ln(8)
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.ln(4)
            
            if aerial_photo:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    aerial_photo.save(tmp.name)
                    img_h = 160 * (aerial_photo.height / aerial_photo.width)
                    pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                    pdf.set_y(pdf.get_y() + img_h + 5)
                    pdf.set_font('Helvetica', 'I', 9)
                    pdf.set_text_color(100, 100, 100)
                    pdf.set_x(25)
                    pdf.cell(0, 5, clean_pdf_text("Figur 1: Ortofoto over prosjektområdet. Hentet direkte via Kartverket WMS."), 0, 1, 'C')
                    pdf.ln(5)
            else:
                pdf.set_font('Helvetica', 'I', 9)
                pdf.set_text_color(150, 0, 0)
                pdf.set_x(25)
                pdf.cell(0, 5, "Merknad: Kunne ikke hente flyfoto fra Kartverket for dette søket.", 0, 1, 'L')
                pdf.ln(5)
                
            pdf.set_font('Helvetica', '', 10)
            pdf.set_text_color(0, 0, 0)
            continue
            
        elif line.startswith('# ') or re.match(r'^\d\.\s[A-Z]', line):
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
                
    return bytes(pdf.output(dest='S'))

# --- 5. STREAMLIT UI ---
st.title("🌍 Builtly RIG/RIM AI (Geoteknikk & Miljø)")
st.info("Analyser Excel-data fra boreentreprenør/laboratorium og generer automatisk grunnundersøkelse og tiltaksplan.")

with st.expander("Prosjekt & Lokasjon", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", "Saga Park")
    c_name = c2.text_input("Oppdragsgiver", "Saga Park AS")
    
    st.markdown("##### Kartverket Oppslag")
    c3, c4 = st.columns(2)
    adresse = c3.text_input("Gatenavn og nummer", "Industriveien 1")
    kommune = c4.text_input("Kommune", "Trondheim")
    
    c5, c6 = st.columns(2)
    gnr = c5.text_input("Gårdsnummer (Gnr)", "316")
    bnr = c6.text_input("Bruksnummer (Bnr)", "689")

files = st.file_uploader("Last opp boreresultater / lab-analyser (Excel/CSV)", accept_multiple_files=True)

if st.button("GENERER GEOTEKNISK & MILJØTEKNISK RAPPORT", type="primary"):
    if not files: 
        st.warning("Du har ikke lastet opp rådata (Excel). Genererer en generell rapport kun basert på lokasjon.")
    
    kartverket_info = ""
    nord = ost = None
    aerial_photo = None
    
    with st.spinner("🌍 Kobler til Geonorge API og henter flyfoto..."):
        kartverket_info, nord, ost = fetch_kartverket_data(adresse, kommune, gnr, bnr)
        if nord and ost:
            st.success(kartverket_info)
            aerial_photo = fetch_kartverket_flyfoto(nord, ost)
        else:
            st.warning(kartverket_info)
    
    with st.spinner("📊 Analyserer Excel-data og borerapporter..."):
        extracted_data = extract_drill_data(files) if files else "Ingen opplastet data. Be brukeren ettersende boreprøver."
        st.toast("Data analysert. Skriver rapport...")
        
        gyldige_modeller = []
        try:
            for mod in genai.list_models():
                if 'generateContent' in mod.supported_generation_methods:
                    gyldige_modeller.append(mod.name)
        except:
            st.error("Kunne ikke koble til Google AI. Sjekk API-nøkkel.")
            st.stop()

        valgt_modell = gyldige_modeller[0]
        for favoritt in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if favoritt in gyldige_modeller:
                valgt_modell = favoritt
                break
        
        model = genai.GenerativeModel(valgt_modell)

        prompt = f"""
        Du er Builtly RIG/RIM AI, en fagekspert innen geoteknikk (RIG) og miljøteknikk (RIM) i Norge.
        Du skal skrive en detaljert "Miljøteknisk grunnundersøkelse og tiltaksplan for forurenset grunn" samt geoteknisk vurdering.
        
        PROSJEKT: {p_name}
        OPPDRAGSGIVER: {c_name}
        ADRESSE: {adresse}, {kommune}. Gnr {gnr}/Bnr {bnr}.
        KARTVERKET-DATA: {kartverket_info}
        
        RÅDATA FRA BOREENTREPRENØR / LABORATORIUM:
        {extracted_data}
        
        INSTRUKSER:
        - Skriv formelt, teknisk og utfyllende (Minimum 1500 ord).
        - Bruk rådataene over for å vurdere grunnforholdene (Hva slags masser er det? Er det funnet forurensning? Vurder tilstandsklasser ihht. Miljødirektoratets veileder).
        - Foreslå konkrete tiltak i tiltaksplanen basert på om dataene viser forurensning eller rene masser.
        
        STRUKTUR (Bruk disse eksakte overskriftene, uten tillegg i parentes):
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. INNLEDNING OG PROSJEKTBESKRIVELSE
        # 3. KARTVERKET OG LOKASJON
        # 4. UTFØRTE GRUNNUNDERSØKELSER
        # 5. RESULTATER: GRUNNFORHOLD OG FORURENSNING
        # 6. GEOTEKNISKE VURDERINGER (FUNDAMENTERING)
        # 7. TILTAKSPLAN OG MASSEHÅNDTERING
        """
        
        try:
            res = model.generate_content(prompt)
            with st.spinner("Kompilerer profesjonell PDF..."):
                pdf_data = create_full_report_pdf(p_name, c_name, res.text, aerial_photo)
            
            st.success("✅ Komplett geoteknisk utredning og tiltaksplan er ferdigstilt!")
            st.download_button("📄 Last ned Builtly RIG/RIM-rapport", pdf_data, f"Builtly_GEO_MILJO_{p_name}.pdf")
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")
