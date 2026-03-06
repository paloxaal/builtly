import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
from datetime import datetime
import tempfile
import re
import numpy as np
import io
import math
import gc
import requests
import urllib.parse
from PIL import Image, ImageDraw, ImageFont

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Mulighetsstudie Pro | Builtly AI", layout="wide")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

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

# --- 2. KARTVERKET DOBBEL-API (FLYFOTO + TOPOKART) ---
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
        except Exception: pass
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

    return "Fant ingen eksakte treff i Kartverket. Bruker generiske data for rapporten.", None, None

def fetch_maps_from_kartverket(nord, ost):
    if not nord or not ost: return None, None
    min_x, max_x = float(ost) - 200, float(ost) + 200
    min_y, max_y = float(nord) - 200, float(nord) + 200
    
    wms_ortho = f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
    wms_topo = f"https://opencache.statkart.no/gatekeeper/gk/gk.open_wms?service=WMS&request=GetMap&version=1.1.1&layers=topo4&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
    
    img_ortho = img_topo = None
    try:
        resp_ortho = requests.get(wms_ortho, timeout=10)
        if resp_ortho.status_code == 200: img_ortho = Image.open(io.BytesIO(resp_ortho.content))
        
        resp_topo = requests.get(wms_topo, timeout=10)
        if resp_topo.status_code == 200: img_topo = Image.open(io.BytesIO(resp_topo.content))
    except Exception: pass
    
    return img_ortho, img_topo

# --- 3. BEREGNINGSMOTOR (VOLUMSTUDIE) ---
def generate_volume_study(img, is_bolig):
    w, h = img.size
    draw_img = img.convert("RGBA")
    overlay = Image.new("RGBA", draw_img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    try: 
        font_large = ImageFont.truetype("arial.ttf", int(h/35))
        font_small = ImageFont.truetype("arial.ttf", int(h/50))
    except: 
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    cx, cy = w // 2, h // 2
    
    # Byggeområde
    bw, bh = int(w*0.3), int(h*0.2)
    b_rect = [cx - bw//2, cy - bh//2, cx + bw//2, cy + bh//2]
    draw.rectangle(b_rect, fill=(220, 50, 50, 140), outline=(200, 0, 0, 255), width=4)
    draw.text((cx - bw//4, cy - 10), "VOLUM 1 / BYGGEGRENSE", fill=(255,255,255,255), font=font_large)

    # Utomhus / MUA
    gw, gh = int(w*0.2), int(h*0.15)
    g_rect = [cx - bw//2, cy + bh//2 + 20, cx - bw//2 + gw, cy + bh//2 + 20 + gh]
    draw.rectangle(g_rect, fill=(50, 200, 50, 120), outline=(0, 150, 0, 255), width=3)
    
    green_label = "MUA / UTEOPPHOLD" if is_bolig else "GRØNTSTRUKTUR / OVERVANN"
    draw.text((g_rect[0] + 10, g_rect[1] + gh//3), green_label, fill=(0,100,0,255), font=font_small)

    # Adkomst / Logistikk
    aw, ah = int(w*0.15), int(h*0.1)
    a_rect = [cx + bw//2 - aw, cy + bh//2 + 20, cx + bw//2, cy + bh//2 + 20 + ah]
    draw.rectangle(a_rect, fill=(50, 50, 220, 120), outline=(0, 0, 150, 255), width=3)
    
    blue_label = "ADKOMST / P-PLASS" if is_bolig else "LOGISTIKK / VAREMOTTAK"
    draw.text((a_rect[0] + 10, a_rect[1] + ah//3), blue_label, fill=(0,0,100,255), font=font_small)

    # Boks
    box_w, box_h = int(w*0.35), int(h*0.2)
    draw.rectangle([w-box_w, h-box_h, w-10, h-10], fill=(255,255,255,240), outline="black", width=3)
    draw.text((w-box_w+20, h-box_h+20), "KONSEPTUELL VOLUMSKISSE", fill="black", font=font_large)
    draw.text((w-box_w+20, h-box_h+60), "Fase: Mulighetsstudie", fill=(100,100,100), font=font_small)
    
    out = Image.alpha_composite(draw_img, overlay)
    return out.convert("RGB")

# --- 4. DYNAMISK PDF MOTOR ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: ARK-001"), 0, 1, 'R')
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

def create_full_report_pdf(name, client, content, maps, img_ortho, img_topo):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
    pdf.add_page()
    if os.path.exists("logo.png"): 
        pdf.image("logo.png", x=25, y=20, w=50)
    pdf.set_y(100)
    pdf.set_x(25)
    pdf.set_font('Helvetica', 'B', 26)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("MULIGHETSSTUDIE OG VOLUMANALYSE"), 0, 1, 'L')
    pdf.set_x(25)
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"PROSJEKT: {pdf.p_name}"), 0, 1, 'L')
    pdf.ln(30)
    
    metadata = [
        ("OPPDRAGSGIVER:", client), 
        ("DATO:", datetime.now().strftime("%d. %m. %Y")), 
        ("UTARBEIDET AV:", "Builtly Arkitektur AI Engine"), 
        ("KONTROLLERT AV:", "[Ansvarlig Sivilarkitekt / Utvikler]")
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
        "2. EIENDOMSANALYSE OG KARTVERKET", 
        "3. KRAV I KOMMUNEPLAN (KPA) OG UTNYTTELSE", 
        "4. AREALANALYSE OG LØNNSOMHET (BTA / NETTO)",
        "5. UTOMHUSPLAN OG GRØNTSTRUKTUR", 
        "6. SOL, SKYGGE OG OMGIVELSER", 
        "7. ANBEFALING FOR VIDERE PROSJEKTERING", 
        "VEDLEGG: KONSEPTUELL VOLUMSKISSE"
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
            
        if line.startswith('# 2. EIENDOMSANALYSE'):
            pdf.check_space(220) 
            pdf.ln(8)
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(150, 7, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.ln(4)
            
            if img_ortho and img_topo:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp1, tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp2:
                    img_ortho.save(tmp1.name)
                    img_topo.save(tmp2.name)
                    pdf.image(tmp1.name, x=25, y=pdf.get_y(), w=75)
                    pdf.image(tmp2.name, x=110, y=pdf.get_y(), w=75)
                    pdf.set_y(pdf.get_y() + 80)
                    pdf.set_font('Helvetica', 'I', 9)
                    pdf.set_text_color(100, 100, 100)
                    pdf.set_x(25)
                    pdf.cell(0, 5, clean_pdf_text("Figur 1: Ortofoto (venstre) og Norgeskart (høyre) hentet via Kartverket WMS."), 0, 1, 'C')
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

    if maps:
        pdf.add_page()
        pdf.set_x(25)
        pdf.set_font('Helvetica', 'B', 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 20, "VEDLEGG: KONSEPTUELL VOLUMSKISSE", 0, 1)
        for i, m in enumerate(maps):
            if i > 0: pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                m.save(tmp.name)
                img_h = 160 * (m.height / m.width)
                pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                pdf.set_y(pdf.get_y() + img_h + 5)
                pdf.set_x(25)
                pdf.set_font('Helvetica', 'I', 10)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: AI-generert mulighetsstudie og volumallokering."), 0, 1, 'C')
                
    return bytes(pdf.output(dest='S'))

# --- 5. STREAMLIT UI ---
st.title("🏗️ Builtly Arkitektur AI (Mulighetsstudie)")
st.info("Genererer profesjonelle mulighetsstudier for Bolig og Næring. Inkluderer arealberegninger etter ny NS 3940:2023.")

prosjekttype = st.radio("Hovedformål for prosjektet:", ["Bolig", "Næring (Kontor/Handel/Lager)"], horizontal=True)
is_bolig = prosjekttype == "Bolig"

with st.expander("Prosjekt & Geografi", expanded=True):
    c1, c2 = st.columns(2)
    p_name = c1.text_input("Prosjektnavn", "Saga Park")
    c_name = c2.text_input("Oppdragsgiver", "Saga Park AS")
    
    c3, c4 = st.columns(2)
    adresse = c3.text_input("Gatenavn og nummer", "Industriveien 1")
    kommune = c4.text_input("Kommune", "Trondheim")

with st.expander("Arealberegning & Utnyttelse (NS 3940:2023)", expanded=True):
    st.markdown(f"**(BTA = Bruttoareal | BRA = Bruksareal | {'BRA-i = Salgbart Boligareal' if is_bolig else 'UBA = Utleibart areal'})**")
    
    col1, col2, col3 = st.columns(3)
    tomt_m2 = col1.number_input("Tomteareal (m²)", value=5000)
    bygg_bya = col2.number_input("Ønsket fotavtrykk BYA (m²)", value=1500)
    etasjer = col3.number_input("Antall etasjer (Snitt)", value=4)
    
    col4, col5 = st.columns(2)
    bta_bra_faktor = col4.slider("Konvertering BTA til BRA", 0.70, 0.95, 0.85, 0.01, help="Korrigerer for yttervegger og sjakter")
    
    if is_bolig:
        bra_netto_faktor = col5.slider("Konvertering BRA til BRA-i", 0.70, 0.95, 0.82, 0.01, help="Korrigerer for fellesganger. Gir salgbart areal.")
        
        col6, col7, col8 = st.columns(3)
        boenheter = col6.number_input("Antall boenheter", value=50)
        kommune_max_bya = col7.number_input("Kommunes maks tillatt %-BYA", value=35)
        kommune_mua_krav = col8.number_input("MUA-krav (m² uteopphold pr enhet)", value=15)
    else:
        bra_netto_faktor = col5.slider("Konvertering BRA til UBA (Utleibart)", 0.70, 0.98, 0.90, 0.01, help="Næring har ofte høyere konvertering til UBA.")
        
        col6, col7 = st.columns(2)
        kommune_max_bya = col6.number_input("Kommunes maks tillatt %-BYA", value=60)
        kommune_gront_krav = col7.number_input("Krav til grøntareal (% av tomt)", value=10)

files = st.file_uploader("Last opp et blankt kart (PDF/Bilde) for AI volumskisse", accept_multiple_files=True)

if st.button("GENERER MULIGHETSSTUDIE OG AREALANALYSE", type="primary"):
    
    # Matematikk-motor
    bya_prosent = (bygg_bya / tomt_m2) * 100 if tomt_m2 > 0 else 0
    bta_tot = bygg_bya * etasjer
    bra_tot = bta_tot * bta_bra_faktor
    netto_tot = bra_tot * bra_netto_faktor # Enten BRA-i eller UBA
    total_netto_brutto = (netto_tot / bta_tot) * 100 if bta_tot > 0 else 0
    
    kartverket_info = ""
    img_ortho = img_topo = None
    
    with st.spinner("🌍 Kobler til Geonorge API for Ortofoto og Norgeskart..."):
        kartverket_info, nord, ost = fetch_kartverket_data(adresse, kommune, "", "")
        if nord and ost:
            st.success(kartverket_info)
            img_ortho, img_topo = fetch_maps_from_kartverket(nord, ost)
        else:
            st.warning(kartverket_info)
    
    processed_maps = []
    if files:
        with st.spinner("📐 Arkitekt-AI tegner konseptuelle volumskisser..."):
            try:
                valid_image_files = [f for f in files if f.name.lower().endswith(('.pdf', '.png', '.jpg', '.jpeg'))]
                for i in range(min(len(valid_image_files), 1)): 
                    f = valid_image_files[i]
                    if f.name.lower().endswith('pdf'):
                        if fitz is None: st.stop()
                        doc = fitz.open(stream=f.read(), filetype="pdf")
                        pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                        doc.close() 
                    else:
                        img = Image.open(f)
                    
                    m = generate_volume_study(img, is_bolig)
                    processed_maps.append(m)
            except Exception as e:
                pass
                
    with st.spinner("🤖 Genererer omfattende byplanleggingsrapport..."):
        gyldige_modeller = []
        try:
            for mod in genai.list_models():
                if 'generateContent' in mod.supported_generation_methods:
                    gyldige_modeller.append(mod.name)
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        valgt_modell = gyldige_modeller[0]
        for favoritt in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if favoritt in gyldige_modeller:
                valgt_modell = favoritt
                break
        
        model = genai.GenerativeModel(valgt_modell)

        # Skreddersydd tekst basert på Bolig vs Næring
        if is_bolig:
            snitt_pr_enhet = netto_tot / boenheter if boenheter > 0 else 0
            mua_behov = boenheter * kommune_mua_krav
            areal_tekst = f"""
            - Salgbart areal (Internt bruksareal BRA-i): {netto_tot:.0f} m2
            - Prosjektets totale Brutto/Netto-faktor: {total_netto_brutto:.1f} % av BTA er salgbart areal.
            - Antall boenheter: {boenheter} (Gir en snittstørrelse på {snitt_pr_enhet:.0f} m2 BRA-i pr. enhet)
            - MUA-krav (Uteoppholdsareal): Trenger minimum {mua_behov} m2 for å tilfredsstille kommunens krav.
            """
            rolle_tekst = "Fokuser på bokvalitet, salgbarhet, løsning av MUA, og fortetting i tråd med KPA."
        else:
            gront_behov = tomt_m2 * (kommune_gront_krav / 100)
            areal_tekst = f"""
            - Utleibart areal (UBA): {netto_tot:.0f} m2
            - Prosjektets totale Brutto/Netto-faktor: {total_netto_brutto:.1f} % av BTA er utleibart.
            - Grøntarealkrav: Kommunen krever {kommune_gront_krav}% av tomt ({gront_behov:.0f} m2).
            """
            rolle_tekst = "Fokuser på næringsutvikling, Yield/leiepotensial (UBA), rasjonell logistikk/adkomst, og overvannshåndtering/grøntareal."

        prompt = f"""
        Du er Builtly Arkitektur AI, en kommersielt anlagt sivilarkitekt og eiendomsutvikler.
        Skriv en mulighetsstudie for et nytt prosjekt av type: {prosjekttype.upper()}.
        Bruk ny NS 3940:2023 for arealbegrepene.
        
        PROSJEKT: {p_name}
        LOKASJON: {adresse}, {kommune}. {kartverket_info}
        
        NØKKELTALL BEREGNET AV SYSTEMET (Må integreres og drøftes!):
        - Tomtestørrelse: {tomt_m2} m2
        - Fotavtrykk (Bebygd Areal - BYA): {bygg_bya} m2
        - Utnyttelsesgrad: {bya_prosent:.1f} %-BYA (Kommuneplanens grense er satt til {kommune_max_bya} %)
        - Bruttoareal (BTA): {bta_tot:.0f} m2 ({etasjer} etasjer)
        - Bruksareal (BRA): {bra_tot:.0f} m2 
        {areal_tekst}
        
        INSTRUKSER:
        - {rolle_tekst}
        - Skriv formelt og teknisk korrekt (Minimum 1500 ord).
        - Bruk kapittel 4 til å drøfte prosjektets lønnsomhet i lys av konverteringen fra BTA til {('BRA-i' if is_bolig else 'UBA')}. 
        - Er en brutto/netto-faktor på {total_netto_brutto:.1f}% god nok for denne typen eiendom?
        
        STRUKTUR (Bruk KUN disse nøyaktige overskriftene):
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. EIENDOMSANALYSE OG KARTVERKET
        # 3. KRAV I KOMMUNEPLAN (KPA) OG UTNYTTELSE
        # 4. AREALANALYSE OG LØNNSOMHET (BTA / NETTO)
        # 5. UTOMHUSPLAN OG GRØNTSTRUKTUR
        # 6. SOL, SKYGGE OG OMGIVELSER
        # 7. ANBEFALING FOR VIDERE PROSJEKTERING
        """
        
        try:
            res = model.generate_content(prompt)
            with st.spinner("Kompilerer arkitekt-PDF..."):
                pdf_data = create_full_report_pdf(p_name, c_name, res.text, processed_maps, img_ortho, img_topo)
            
            st.success("✅ Mulighetsstudie er ferdigstilt!")
            st.download_button("📄 Last ned Builtly ARKITEKTUR-rapport", pdf_data, f"Builtly_MULIGHET_{p_name}.pdf")
        except Exception as e: 
            st.error(f"Kritisk feil under generering: {e}")
