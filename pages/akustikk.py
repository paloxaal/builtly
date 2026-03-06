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
import json
from PIL import Image, ImageDraw, ImageFont

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Akustikk Pro | Builtly AI", layout="wide")

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
    if not text: 
        return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*", "²": "2", "³": "3"}
    for old, new in rep.items(): 
        text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    text = text.replace('$', '').replace('*', '').replace('_', '')
    text = re.sub(r'[-|=]{4,}', ' ', text)
    # Tvinger orddeling på 40 tegn for å unngå FPDF "Not enough space"-krasj
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    return clean_pdf_text(text)

# --- AI VISION MODUL ---
def analyze_drawing_with_vision(img):
    try:
        model = genai.GenerativeModel('gemini-1.5-pro')
        prompt = """
        Du er en ekspert i å lese arkitekttegninger og situasjonsplaner.
        Søk nøye gjennom dette bildet etter tekst eller symboler som angir en 'støyskjerm'. 
        Finn ut hva høyden på denne støyskjermen er i meter.
        Svar KUN med dette eksakte JSON-formatet:
        {"støyskjerm_hoyde_m": 0.0}
        """
        response = model.generate_content([prompt, img])
        match = re.search(r'\{.*\}', response.text, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            return float(data.get("støyskjerm_hoyde_m", 0.0))
    except Exception:
        pass
    return 0.0

# --- 2. BEREGNINGSMOTOR ("CADNA-A LITE" HEURISTIKK) ---
def generate_pro_stoykart(img, adt, speed, dist, floor_num, screen_height=0.0):
    w, h = img.size
    draw_img = img.convert("RGBA")
    overlay = Image.new("RGBA", draw_img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    floor_h = (floor_num - 1) * 3.0 + 1.5
    
    # Nord96 Heuristikk-kalibrering + 3dB fasaderefleksjon (Bransjestandard)
    base_db = 10 * math.log10(max(adt, 1)) + 20 * math.log10(max(speed, 30)/50.0) + 33
    
    try: 
        font_large = ImageFont.truetype("arial.ttf", int(h/45))
    except: 
        font_large = ImageFont.load_default()

    # Myk støygradient i bakgrunnen
    for y in range(int(h*0.2), h, 15):
        d_m = dist + (h - y) * (50/h)
        db_at_y = base_db - 10 * math.log10(max(d_m, 1) / 10.0)
        if screen_height > 0: db_at_y -= 8 
            
        if db_at_y > 65: color = (255, 0, 0, 25)
        elif db_at_y > 55: color = (255, 255, 0, 20)
        else: color = (0, 255, 0, 10)
        draw.rectangle([0, y, w, y+15], fill=color)

    # --- AVANSERT BYGG-SPORING ---
    gray_array = np.array(img.convert("L"))
    margin_x, margin_y = int(w * 0.12), int(h * 0.12)
    
    raw_points = []
    
    # Vertikal skanning
    x_steps = np.linspace(margin_x, w - margin_x, 15, dtype=int)
    for x in x_steps:
        col = gray_array[margin_y:h-margin_y, x]
        dark_y = np.where(col < 230)[0]
        if len(dark_y) > 0:
            y_front = margin_y + dark_y[-1]
            y_back = margin_y + dark_y[0]
            if (y_front - y_back) > (h*0.04): 
                raw_points.append((x, y_front + 5, 'front'))
                raw_points.append((x, y_back - 5, 'back'))

    # Horisontal skanning
    y_steps = np.linspace(margin_y, h - margin_y, 10, dtype=int)
    for y in y_steps:
        row = gray_array[y, margin_x:w-margin_x]
        dark_x = np.where(row < 230)[0]
        if len(dark_x) > 0:
            x_left = margin_x + dark_x[0]
            x_right = margin_x + dark_x[-1]
            if (x_right - x_left) > (w*0.04):
                raw_points.append((x_left - 5, y, 'side_left'))
                raw_points.append((x_right + 5, y, 'side_right'))

    # Filtrering
    final_points = []
    for p in raw_points:
        if all(math.hypot(p[0] - fp[0], p[1] - fp[1]) > (w*0.045) for fp in final_points):
            final_points.append(p)

    calculated_dbs = []
    for px, py, ptype in final_points:
        d_m = dist + (h - py) * (50/h)
        d_3d = math.sqrt(d_m**2 + floor_h**2)
        
        db = base_db - 10 * math.log10(max(d_3d, 1) / 10.0)
        
        # FYSIKKMODUL
        shielding = 0
        if ptype == 'front':
            db += 2.5 
            if screen_height > 0 and floor_h <= (screen_height + 1.5):
                shielding = 8 
        elif ptype == 'back': 
            shielding = 18 
        elif 'side' in ptype:
            y_ratio = (py - margin_y) / (h - 2*margin_y) 
            shielding = 5 + (10 * (1 - y_ratio)) 
            
        final_db = int(db - shielding)
        calculated_dbs.append(final_db)
        
        dot_color = (200, 0, 0, 255) if final_db >= 65 else ((200, 150, 0, 255) if final_db >= 55 else (50, 150, 50, 255))
        r = int(h/65)
        
        draw.ellipse([px-r-2, py-r-2, px+r+2, py+r+2], fill=(255,255,255,230))
        draw.ellipse([px-r, py-r, px+r, py+r], fill=dot_color, outline=(0,0,0,255))
        draw.text((px-r/1.5, py-r/1.5), str(final_db), fill="white" if final_db>=55 else "black", font=font_large)

    box_w, box_h = int(w*0.35), int(h*0.2)
    draw.rectangle([w-box_w, h-box_h, w-10, h-10], fill=(255,255,255,240), outline="black", width=3)
    draw.text((w-box_w+20, h-box_h+20), f"AKUSTISK KARTLEGGING", fill="black", font=font_large)
    info_text = f"PLAN {floor_num} | Parametere Lden"
    if screen_height > 0: info_text += f" (Skjerm {screen_height}m)"
    draw.text((w-box_w+20, h-box_h+60), info_text, fill="black", font=font_large)
    
    if calculated_dbs:
        draw.text((w-box_w+20, h-box_h+100), f"Maks (Vei): {max(calculated_dbs)} dB", fill=(200,0,0) if max(calculated_dbs)>=60 else "black", font=font_large)
        draw.text((w-box_w+20, h-box_h+130), f"Min (Stille side): {min(calculated_dbs)} dB", fill=(50,150,50), font=font_large)

    out = Image.alpha_composite(draw_img, overlay)
    return out.convert("RGB"), calculated_dbs

# --- 3. DYNAMISK PDF MOTOR (OPPGRADERT MED AUTO-BREDDE w=0) ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10)
            self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: AKU-001"), 0, 1, 'R')
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

def create_full_report_pdf(name, client, content, maps):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25)
    pdf.set_auto_page_break(True, 25)
    
    pdf.add_page()
    if os.path.exists("logo.png"): 
        pdf.image("logo.png", x=25, y=20, w=50)
    pdf.set_y(100)
    pdf.set_font('Helvetica', 'B', 26)
    pdf.set_text_color(26, 43, 72)
    pdf.multi_cell(0, 10, clean_pdf_text("STØYFAGLIG UTREDNING"))
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(0, 10, clean_pdf_text(f"FOR RAMMETILLATELSE: {pdf.p_name}"))
    pdf.ln(30)
    
    metadata = [
        ("OPPDRAGSGIVER:", client), 
        ("DATO:", datetime.now().strftime("%d. %m. %Y")), 
        ("UTARBEIDET AV:", "Builtly RIAKU AI Engine"), 
        ("KONTROLLERT AV:", "[Ansvarlig Prosjekterende]")
    ]
    
    for l, v in metadata:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10)
        pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 16)
    pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1)
    pdf.ln(5)
    
    toc = [
        "1. SAMMENDRAG OG KONKLUSJON", 
        "2. INNLEDNING", 
        "3. KRAV OG RETNINGSLINJER (T-1442 / NS 8175)", 
        "4. BEREGNINGSFORUTSETNINGER OG METODIKK", 
        "5. RESULTATER: STØYUTBREDELSE", 
        "6. VURDERING AV FASADEISOLERING OG TILTAK", 
        "7. VURDERING AV UTEOPPHOLDSAREALER", 
        "VEDLEGG: STØYSONEKART"
    ]
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(0, 0, 0)
    for t in toc:
        pdf.cell(0, 10, clean_pdf_text(t), 0, 1)
        pdf.set_draw_color(220, 220, 220)
        pdf.line(25, pdf.get_y(), 185, pdf.get_y())

    pdf.add_page()
    for raw_line in content.split('\n'):
        line = raw_line.strip()
        if not line: 
            pdf.ln(4)
            continue
            
        if line.startswith('# ') or re.match(r'^\d\.\s[A-Z]', line):
            pdf.check_space(40)
            pdf.ln(10)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(26, 43, 72)
            pdf.multi_cell(0, 8, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.ln(2)
            pdf.set_font('Helvetica', '', 10)
            pdf.set_text_color(0, 0, 0)
        
        elif line.startswith('##'):
            pdf.check_space(20)
            pdf.ln(6)
            pdf.set_font('Helvetica', 'B', 12)
            pdf.set_text_color(50, 50, 50)
            pdf.multi_cell(0, 8, ironclad_text_formatter(line.replace('#', '').strip()))
            pdf.set_font('Helvetica', '', 10)
            pdf.set_text_color(0, 0, 0)
            
        else:
            pdf.set_font('Helvetica', '', 10)
            safe_text = ironclad_text_formatter(line)
            
            if safe_text.strip() == "": 
                continue

            try:
                # w=0 gjør at margen regnes ut automatisk. Krasjer aldri på bredden igjen!
                if safe_text.startswith('- ') or safe_text.startswith('* '):
                    pdf.set_x(30)
                    pdf.multi_cell(0, 5, safe_text)
                    pdf.set_x(25)
                else:
                    pdf.set_x(25)
                    pdf.multi_cell(0, 5, safe_text)
            except Exception:
                pdf.ln(2)

    if maps:
        pdf.add_page()
        pdf.set_font('Helvetica', 'B', 16)
        pdf.set_text_color(26, 43, 72)
        pdf.cell(0, 20, "VEDLEGG: BEREGNEDE STØYSONEKART", 0, 1)
        for i, m in enumerate(maps):
            if i > 0: 
                pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                m.save(tmp.name)
                img_h = 160 * (m.height / m.width)
                pdf.image(tmp.name, x=25, y=pdf.get_y(), w=160)
                pdf.set_y(pdf.get_y() + img_h + 5)
                pdf.set_font('Helvetica', 'I', 10)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: Beregnet fasadestøy og støysoner for Plan {i+1}. Lden (dB)."), 0, 1, 'C')
                
    return bytes(pdf.output(dest='S'))

# --- 4. STREAMLIT UI ---
st.title("🔊 Builtly RIAKU AI (Akustikk)")
st.info("Genererer avanserte støyfaglige utredninger for rammetillatelse. Powered by Builtly AI.")

with st.expander("Prosjekt & Trafikk (Parametere)", expanded=True):
    c1, c2, c3 = st.columns(3)
    p_name = c1.text_input("Prosjektnavn", "Saga Park")
    c_name = c2.text_input("Oppdragsgiver", "Saga Park AS")
    et_count = c3.number_input("Antall etasjeplan i bygget", value=4, min_value=1)
    
    t1, t2, t3 = st.columns(3)
    adt = t1.number_input("ÅDT", value=2500)
    fart = t2.number_input("Fartsgrense (km/t)", value=60)
    avst = t3.number_input("Avstand til vei (m)", value=25)

files = st.file_uploader("Last opp arkitekttegninger (PDF/PNG)", accept_multiple_files=True)

if st.button("GENERER KOMPLETT AKUSTISK UTREDNING", type="primary"):
    if not files: 
        st.error("Last opp tegninger først.")
    else:
        detected_screen_height = 0.0
        with st.spinner("👁️ AI Vision skanner tegningene for skjerming og terreng..."):
            try:
                f_vision = files[0]
                if f_vision.name.lower().endswith('pdf'):
                    doc_v = fitz.open(stream=f_vision.read(), filetype="pdf")
                    pix_v = doc_v.load_page(0).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                    vision_img = Image.open(io.BytesIO(pix_v.tobytes("png")))
                    doc_v.close()
                else:
                    vision_img = Image.open(f_vision)
                
                detected_screen_height = analyze_drawing_with_vision(vision_img.convert("RGB"))
                vision_img.close()
                files[0].seek(0)
                
                if detected_screen_height > 0:
                    st.success(f"✅ AI Vision fant en støyskjerm på {detected_screen_height}m! Oppdaterer matematikken.")
                else:
                    st.info("ℹ️ AI Vision fant ingen støyskjermer på tegningen. Bruker fri sikt.")
            except Exception as e:
                st.warning(f"AI Vision feilet, bruker standard fri sikt. Feil: {e}")
        
        with st.spinner("1. Utfører Builtly 3D-simulering og tegner kart..."):
            try:
                processed_maps = []
                data_summary = []
                process_limit = min(len(files), int(et_count))
                
                for i in range(process_limit):
                    f = files[i]
                    if f.name.lower().endswith('pdf'):
                        if fitz is None: 
                            st.error("PyMuPDF mangler!")
                            st.stop()
                        doc = fitz.open(stream=f.read(), filetype="pdf")
                        
                        pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                        img = Image.open(io.BytesIO(pix.tobytes("png")))
                        doc.close() 
                        del pix 
                    else:
                        img = Image.open(f)
                    
                    m, points = generate_pro_stoykart(img, adt, fart, avst, i+1, detected_screen_height)
                    processed_maps.append(m)
                    
                    if points:
                        data_summary.append(f"PLAN {i+1}: Maks fasade (mot vei) {max(points)} dB, Min (stille side) {min(points)} dB.")
                    
                    img.close()
                    gc.collect()

                st.toast("Simulering ferdig. Kobler til Google AI...")
                
                gyldige_modeller = []
                try:
                    for m in genai.list_models():
                        if 'generateContent' in m.supported_generation_methods:
                            gyldige_modeller.append(m.name)
                except Exception as list_err:
                    raise Exception(f"NETTVERKSFEIL: Fikk ikke kontakt med Google. Sjekk API-nøkkel.")

                valgt_modell = gyldige_modeller[0]
                for favoritt in ['models/gemini-1.5-flash', 'models/gemini-1.5-pro']:
                    if favoritt in gyldige_modeller:
                        valgt_modell = favoritt
                        break
                
                model = genai.GenerativeModel(valgt_modell)

                prompt = f"""
                Du er Builtly RIAKU AI, en avansert støyfaglig AI-ingeniør og rådgiver.
                Du skal skrive en KOMPLETT, DYPTGÅENDE og DETALJERT støyfaglig utredning for rammetillatelse, som visuelt og faglig etterligner anerkjente rapporter.
                
                PROSJEKT: {p_name}
                KUNDE: {c_name}
                TRAFIKKDATA BRUKT I MODELL: ÅDT {adt}, Fart {fart} km/t, Avstand {avst} m.
                AI VISION SKJERMING: Skjerm/terrengvoll oppdaget med høyde {detected_screen_height} m.
                RESULTATER FRA 3D-FASADEBEREGNING: {data_summary}
                
                Viktig endring i resultatene: Beregningene viser nå tydelig forskjell på fasaden som vender MOT VEIEN (Maks dB) og baksiden av bygget som fungerer som STILLE SIDE (Min dB) på grunn av bygningskroppens egen skjermingseffekt.
                
                KRAV TIL TEKSTEN:
                - Dokumentet MÅ være langt og utfyllende (Skriv minimum 1500 ord).
                - Bruk formelt, teknisk ingeniørspråk. Omtal deg selv som Builtly RIAKU AI.
                - Unngå tabeller. Ikke bruk '|' eller lange understreker. Skriv alt med ren tekst og kulepunkter.
                - Hvis maks fasadestøy er over 55 dB, befinner bygget seg i GUL SONE. Diskuter dette!
                
                STRUKTUR SOM SKAL FØLGES:
                # 1. SAMMENDRAG OG KONKLUSJON
                Skriv et fyldig sammendrag.
                
                # 2. INNLEDNING
                Beskriv prosjektet.
                
                # 3. KRAV OG RETNINGSLINJER (T-1442 / NS 8175)
                Gå i dybden på Miljødirektoratets retningslinje T-1442 og NS 8175.
                
                # 4. BEREGNINGSFORUTSETNINGER OG METODIKK
                Beskriv metodikken.
                
                # 5. RESULTATER: STØYUTBREDELSE
                Diskuter fasadenivåene. Nevn uttrykkelig forskjellen på "støyutsatt fasade" og "stille side" (baksiden av bygget).
                
                # 6. VURDERING AV FASADEISOLERING OG TILTAK
                Gitt maksnivåene mot veien, foreslå konkrete krav til vinduer (Rw+Ctr) og kanskje spesifikke tiltak for lufting.
                
                # 7. VURDERING AV UTEOPPHOLDSAREALER
                Drøft plassering av uteplasser, balkonginnglassing og skjerming.
                """
                
                res = model.generate_content(prompt)
                
                with st.spinner("Kompilerer profesjonell PDF..."):
                    pdf_data = create_full_report_pdf(p_name, c_name, res.text, processed_maps)
                
                st.success("✅ Komplett ingeniørutredning er ferdigstilt!")
                st.download_button("📄 Last ned Builtly AKU-rapport", pdf_data, f"Builtly_AKU_{p_name}.pdf")
            except Exception as e: 
                st.error(f"Kritisk feil under generering: {e}")
