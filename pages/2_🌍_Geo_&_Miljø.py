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
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{3,}', ' ', text)
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    return clean_pdf_text(text)

# --- 2. KARTVERKET API ---
def fetch_kartverket_data(adresse, kommune, gnr, bnr):
    adr = adresse.replace(',', '').strip() if adresse else ""
    kom = kommune.replace(',', '').strip() if kommune else ""
    g = gnr.strip() if gnr else ""
    b = bnr.strip() if bnr else ""

    def make_request(params):
        params['utkoordsys'] = '25833'
        params['treffPerSide'] = 1
        try:
            resp = requests.get("https://ws.geonorge.no/adresser/v1/sok", params=params, timeout=8)
            if resp.status_code == 200 and resp.json().get('adresser'):
                hit = resp.json()['adresser'][0]
                nord = hit.get('representasjonspunkt', {}).get('nord')
                ost = hit.get('representasjonspunkt', {}).get('øst')
                kommune_navn = hit.get('kommunenavn', '')
                adr_tekst = hit.get('adressetekst', '')
                return adr_tekst, kommune_navn, nord, ost
        except: pass
        return None, None, None, None

    if g and b and kom:
        a, k, n, o = make_request({'gardsnummer': g, 'bruksnummer': b, 'kommunenavn': kom})
        if n and o: return f"✅ Lokasjon bekreftet via Matrikkel: Gnr {g}/Bnr {b}, {k}. (N {n}, Ø {o})", n, o

    if adr and kom:
        a, k, n, o = make_request({'sok': adr, 'kommunenavn': kom, 'fuzzy': 'true'})
        if n and o: return f"✅ Lokasjon bekreftet via Adresse: {a}, {k}. (N {n}, Ø {o})", n, o

    if kom:
        try:
            safe_kom = urllib.parse.quote(kom)
            resp = requests.get(f"https://ws.geonorge.no/stedsnavn/v1/navn?sok={safe_kom}&utkoordsys=25833&treffPerSide=1", timeout=8)
            if resp.status_code == 200 and resp.json().get('navn'):
                hit = resp.json()['navn'][0]
                nord = hit.get('representasjonspunkt', {}).get('nord')
                ost = hit.get('representasjonspunkt', {}).get('øst')
                if nord and ost: return f"⚠️ Bruker senter av {kom} (N {nord}, Ø {ost}).", nord, ost
        except: pass

    return "❌ Fant ingen treff i Kartverket for lokasjon.", None, None

def fetch_kartverket_flyfoto(nord, ost):
    if not nord or not ost: return None, "Mangler koordinater"
    min_x, max_x = float(ost) - 150, float(ost) + 150
    min_y, max_y = float(nord) - 150, float(nord) + 150
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    urls = [
        ("Ortofoto (Kartverket)", f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/jpeg"),
        ("Topografisk Norgeskart", f"https://cache.kartverket.no/topo/v1/wms?service=WMS&request=GetMap&version=1.1.1&layers=topo&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png")
    ]
    
    for map_name, url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code == 200 and len(r.content) > 5000:
                return Image.open(io.BytesIO(r.content)).convert('RGB'), map_name
        except: continue
            
    return None, "Timeout/Nedetid på Kartverket API"

# --- 3. EXCEL DATA EXTRACTOR ---
def extract_drill_data(files):
    extracted_text = ""
    for f in files:
        if f.name.lower().endswith(('.xlsx', '.xls', '.csv')):
            try:
                if f.name.lower().endswith('.csv'): df = pd.read_csv(f)
                else: df = pd.read_excel(f)
                extracted_text += f"\n--- RÅDATA FRA: {f.name.upper()} ---\n"
                extracted_text += df.head(100).to_string() + "\n\n"
            except Exception as e:
                extracted_text += f"\n[Feil ved lesing av {f.name}: {e}]\n"
    return extracted_text if extracted_text else "Ingen Excel/CSV-data ble lastet opp."

# --- 4. DYNAMISK PDF MOTOR (MED SIDE-OM-SIDE LAYOUT) ---
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

def create_full_report_pdf(name, client, content, aerial_photo, map_type, hist_photo):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
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
        "3. BESKRIVELSE AV TOMT OG HISTORISK UTVIKLING", 
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
            
        # FANGER OPP NY OVERSKRIFT OG SETTER INN BILDER SIDE-OM-SIDE
        if line.startswith('# 3. BESKRIVELSE AV TOMT'):
            pdf.check_space(100) 
            pdf.ln(8)
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.ln(4)
            
            # BEGGE BILDER (Side om side)
            if aerial_photo and hist_photo:
                pdf.check_space(100) 
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp1, tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp2:
                    aerial_photo.save(tmp1.name, format="PNG")
                    hist_photo.save(tmp2.name, format="PNG")
                    
                    # Beregner proporsjoner for maks 75mm bredde pr bilde
                    img_h1 = 75 * (aerial_photo.height / aerial_photo.width)
                    img_h2 = 75 * (hist_photo.height / hist_photo.width)
                    max_h = max(img_h1, img_h2)
                    
                    y_start = pdf.get_y()
                    pdf.image(tmp1.name, x=25, y=y_start, w=75)
                    pdf.image(tmp2.name, x=110, y=y_start, w=75) # 10mm gap i midten
                    
                    pdf.set_y(y_start + max_h + 5)
                    pdf.set_font('Helvetica', 'I', 9)
                    pdf.set_text_color(100, 100, 100)
                    pdf.set_x(25)
                    pdf.cell(0, 5, clean_pdf_text(f"Figur 1: Dagens situasjon ({map_type}) til venstre. Historisk situasjon til høyre."), 0, 1, 'C')
                    pdf.ln(8)
            
            # KUN ETT BILDE (Sentrert eleganse, ikke massivt)
            elif aerial_photo or hist_photo:
                pdf.check_space(120)
                img_to_use = aerial_photo if aerial_photo else hist_photo
                label = f"Dagens situasjon ({map_type})." if aerial_photo else "Historisk situasjon."
                
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    img_to_use.save(tmp.name, format="PNG")
                    img_w = 120 # Skalert ned for å se proft ut
                    img_h = img_w * (img_to_use.height / img_to_use.width)
                    x_center = (210 - img_w) / 2 # Matematisk sentrering på A4-ark
                    
                    pdf.image(tmp.name, x=x_center, y=pdf.get_y(), w=img_w)
                    pdf.set_y(pdf.get_y() + img_h + 5)
                    pdf.set_font('Helvetica', 'I', 9)
                    pdf.set_text_color(100, 100, 100)
                    pdf.set_x(25)
                    pdf.cell(0, 5, clean_pdf_text(f"Figur 1: {label}"), 0, 1, 'C')
                    pdf.ln(8)
                
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
st.info("Analyser opplastede lab-rapporter, vurder historisk forurensning, og generer tiltaksplan.")

with st.expander("1. Prosjekt & Lokasjon", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", "Saga Park")
    c_name = c2.text_input("Oppdragsgiver", "Saga Park AS")
    
    st.markdown("##### Kartverket Oppslag")
    c3, c4 = st.columns(2)
    adresse = c3.text_input("Gatenavn og nummer", "Industriveien 1B")
    kommune = c4.text_input("Kommune", "Trondheim")
    
    c5, c6 = st.columns(2)
    gnr = c5.text_input("Gårdsnummer (Gnr)", "316")
    bnr = c6.text_input("Bruksnummer (Bnr)", "724")

with st.expander("2. Manuell Bildeopplasting (Kart & Historikk)", expanded=True):
    st.markdown("Hvis Kartverket er nede, eller hvis du vil legge ved gamle bilder for å vurdere historisk forurensning.")
    manual_aerial = st.file_uploader("Last opp Dagens Flyfoto/Situasjonskart (Valgfritt)", type=["png", "jpg", "jpeg"])
    manual_hist = st.file_uploader("Last opp Historisk Flyfoto (Valgfritt)", type=["png", "jpg", "jpeg"])

with st.expander("3. Rådata fra Laboratorium", expanded=True):
    files = st.file_uploader("Last opp boreresultater / lab-analyser (Excel/CSV)", accept_multiple_files=True)

if st.button("GENERER GEOTEKNISK & MILJØTEKNISK RAPPORT", type="primary"):
    
    kartverket_info = ""
    aerial_photo = None
    hist_photo = None
    map_type = ""
    
    if manual_aerial:
        aerial_photo = Image.open(manual_aerial).convert('RGB')
        map_type = "Manuelt innsendt situasjonskart"
        kartverket_info = "Lokasjon bekreftet. Kart innsendt manuelt av oppdragsgiver."
    else:
        with st.spinner("🌍 Slår opp i Kartverket for automatisk kart..."):
            kartverket_info, nord, ost = fetch_kartverket_data(adresse, kommune, gnr, bnr)
            if nord and ost:
                aerial_photo, map_type = fetch_kartverket_flyfoto(nord, ost)
            
            if not aerial_photo:
                st.error("🛑 **KARTVERKET SVARTE IKKE!** En gyldig miljørapport krever et lokasjonskart. Gå til 'Manuell Bildeopplasting' og last opp et bilde fra datamaskinen din for å fortsette.")
                st.stop()

    if manual_hist:
        hist_photo = Image.open(manual_hist).convert('RGB')
    
    with st.spinner("📊 Analyserer Excel-data og skanner bilder..."):
        extracted_data = extract_drill_data(files) if files else "Ingen opplastet data."
        
        historisk_prompt = ""
        if manual_hist:
            historisk_prompt = "- VIKTIG: Et historisk flyfoto er vedlagt. Diskuter risiko for at det finnes ukjent forurensning fra tidligere bruk (industri, bensintanker, fyllinger) basert på at vi har måttet se på historikken."
        
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
        Du er Builtly RIG/RIM AI, en senior miljørådgiver i Norge.
        Du skal skrive en "Miljøteknisk grunnundersøkelse og tiltaksplan for forurenset grunn".
        
        PROSJEKT: {p_name}
        OPPDRAGSGIVER: {c_name}
        ADRESSE: {adresse}, {kommune}.
        
        RÅDATA FRA BOREENTREPRENØR / LABORATORIUM:
        {extracted_data}
        
        INSTRUKSER:
        - Skriv formelt, teknisk og utfyllende (Minimum 1500 ord).
        {historisk_prompt}
        - Bruk rådataene for å vurdere grunnforholdene (Hva slags masser er det? Er det funnet forurensning? Vurder tilstandsklasser).
        - Foreslå konkrete tiltak i tiltaksplanen basert på om dataene viser forurensning.
        
        STRUKTUR (Bruk disse eksakte overskriftene, uten tillegg i parentes):
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. INNLEDNING OG PROSJEKTBESKRIVELSE
        # 3. BESKRIVELSE AV TOMT OG HISTORISK UTVIKLING
        # 4. UTFØRTE GRUNNUNDERSØKELSER
        # 5. RESULTATER: GRUNNFORHOLD OG FORURENSNING
        # 6. GEOTEKNISKE VURDERINGER (FUNDAMENTERING)
        # 7. TILTAKSPLAN OG MASSEHÅNDTERING
        """
        
        try:
            res = model.generate_content(prompt)
            with st.spinner("Kompilerer Compliance-Grade PDF..."):
                pdf_data = create_full_report_pdf(p_name, c_name, res.text, aerial_photo, map_type, hist_photo)
            
            st.success("✅ Komplett geoteknisk utredning og tiltaksplan er ferdigstilt!")
            st.download_button("📄 Last ned Builtly RIG/RIM-rapport", pdf_data, f"Builtly_GEO_MILJO_{p_name}.pdf")
        except Exception as e: 
            st.error(f"Kritisk feil under generering PDF: {e}")
