import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import os
import base64
from datetime import datetime
import tempfile
import re
import json
import io
from PIL import Image, ImageDraw, ImageFont
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

# --- 2. PREMIUM CSS ---
st.markdown("""
<style>
    :root { --bg: #06111a; --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df; --accent: #38bdf8; --radius-xl: 24px; --radius-lg: 16px; }
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

# --- 3. SESSION STATE & HARDDISK ---
DB_DIR = Path("qa_database")
SSOT_FILE = DB_DIR / "ssot.json"
IMG_DIR = DB_DIR / "project_images"

if "project_data" not in st.session_state:
    st.session_state.project_data = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": "", "b_type": "Næring", "etasjer": 1, "bta": 0, "land": "Norge"}

if st.session_state.project_data.get("p_name") == "":
    if SSOT_FILE.exists():
        with open(SSOT_FILE, "r", encoding="utf-8") as f:
            st.session_state.project_data = json.load(f)

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
    
    toc = [
        "1. SAMMENDRAG OG KONKLUSJON", 
        "2. VURDERING AV DATAGRUNNLAG", 
        "3. KARTLEGGING OG PINPOINTING AV STØY", 
        "4. LYDFORHOLD INNENDØRS OG PLANLØSNING", 
        "5. KRAV TIL FASADEISOLASJON", 
        "6. TILTAK OG VIDERE PROSJEKTERING", 
        "VEDLEGG: VURDERT DATAGRUNNLAG"
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
        pdf.add_page(); pdf.set_x(25); pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72); pdf.cell(0, 20, "VEDLEGG: VURDERT DATAGRUNNLAG", 0, 1)
        for i, m in enumerate(maps):
            if i > 0: pdf.add_page()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                m.convert("RGB").save(tmp.name, format="JPEG", quality=90)
                img_w = 160
                img_h = 160 * (m.height / m.width)
                if img_h > 240: 
                    img_h = 240
                    img_w = 240 * (m.width / m.height)
                x_pos = 105 - (img_w / 2)
                pdf.image(tmp.name, x=x_pos, y=pdf.get_y(), w=img_w)
                pdf.set_y(pdf.get_y() + img_h + 5)
                pdf.set_x(25); pdf.set_font('Helvetica', 'I', 10); pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 10, clean_pdf_text(f"Figur V-{i+1}: AI-analysert kartutsnitt med estimerte støysoner."), 0, 1, 'C')

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
    lydklasse = c6.selectbox("Lydklasse (NS 8175)", ["Klasse A (Spesielt gode)", "Klasse B (Gode)", "Klasse C (Minimumskrav i TEK)", "Klasse D (Eldre bygg)"], index=2)
    stoykilde = c7.selectbox("Dominerende Støykilde", ["Veitrafikk", "Bane/Tog", "Flystøy", "Industri/Næring", "Lite støy (Stille område)"], index=0)

with st.expander("3. Visuelt Grunnlag & Støykart", expanded=True):
    st.info("Viktig: For at AI-en skal kunne pinpointe fasadene, kreves støykart lagt over eller sammenholdt med situasjonsplanen.")
    
    saved_images = []
    if IMG_DIR.exists():
        for p in sorted(IMG_DIR.glob("*.jpg")):
            saved_images.append(Image.open(p).convert("RGB"))
            
    if len(saved_images) > 0:
        st.success(f"📎 Fant {len(saved_images)} felles arkitekttegninger/kart fra Project Setup. Disse inkluderes automatisk i analysen!")
    else:
        st.warning("Ingen felles tegninger funnet. Du bør laste opp plan og støykart under.")
        
    st.markdown("##### Last opp spesifikke Akustikk-vedlegg")
    files = st.file_uploader("Last opp Støykart, Trafikkdata eller Planløsninger (PDF/Bilder)", accept_multiple_files=True, type=['png', 'jpg', 'jpeg', 'pdf'])

st.markdown("<br>", unsafe_allow_html=True)

if st.button("🚀 Kjør Akustisk Analyse (RIAku)", type="primary", use_container_width=True):
    
    images_for_ai = saved_images.copy()
        
    if files:
        with st.spinner("📐 Leser ut støykart og supplerende filer..."):
            try:
                for f in files: 
                    f.seek(0)
                    if f.name.lower().endswith('pdf'):
                        if fitz is not None: 
                            doc = fitz.open(stream=f.read(), filetype="pdf")
                            for page_num in range(min(4, len(doc))): 
                                pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                                img = Image.open(io.BytesIO(pix.tobytes("jpeg"))).convert("RGB")
                                img.thumbnail((1200, 1200))
                                images_for_ai.append(img)
                            doc.close() 
                    else:
                        img = Image.open(f).convert("RGB")
                        img.thumbnail((1200, 1200))
                        images_for_ai.append(img)
            except Exception as e: 
                st.error(f"Feil under bildebehandling: {e}")
                
    st.info(f"Klar! Sender totalt {len(images_for_ai)} bilder/tegninger til AI-en for vurdering.")
                
    with st.spinner(f"🤖 Pinpointer støysoner og tegner fysiske sirkler på bildene..."):
        try:
            valid_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        except:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        valgt_modell = valid_models[0]
        for fav in ['models/gemini-1.5-pro', 'models/gemini-1.5-flash']:
            if fav in valid_models: valgt_modell = fav; break
        
        model = genai.GenerativeModel(valgt_modell)

        # --- DEN MAGISKE "TEGNE"-PROMPTEN ---
        prompt_text = f"""
        Du er Builtly RIAku AI, en streng og nøyaktig senior akustiker.
        
        PROSJEKT: {p_name} ({bta} m2, {etasjer} etasjer). 
        LOKASJON: {adresse}.
        MÅL-LYDKLASSE: {lydklasse}.
        DOMINERENDE STØYKILDE: {stoykilde}.
        
        EKSTREMT VIKTIG FOR TEGNING AV STØY-SIRKLER PÅ BILDENE:
        For at kodesystemet mitt skal kunne stemple fysiske røde sirkler med dB-verdier oppå bildene i PDF-en, 
        MÅ du returnere en maskinlesbar JSON-blokk HELT NEDERST i teksten din, uansett hva!
        Du må anslå X- og Y-koordinater (i prosent av bildet) for hvor fasadene treffes av støyen.
        
        Bruk NØYAKTIG dette formatet (kopier strukturen):
        ```json
        [
          {{"image_index": 0, "x_pct": 50, "y_pct": 20, "db": "72", "color": "red"}},
          {{"image_index": 0, "x_pct": 80, "y_pct": 80, "db": "54", "color": "green"}}
        ]
        ```
        Forklaring for JSON:
        - `image_index`: Hvilket opplastet bilde (0, 1, 2). Bruk 0 hvis du ser situasjonsplanen/kartet der.
        - `x_pct` og `y_pct`: Prosent (0-100) fra øverst til venstre. (0,0 er oppe til venstre, 50,50 er midten). Prøv å treffe bygningskroppene du ser.
        - `color`: "red" (>65 dB), "yellow" (55-65 dB), eller "green" (<55 dB).
        
        Din tekstlige vurdering skal følge denne strukturen:
        # 1. SAMMENDRAG OG KONKLUSJON (Skriv tydelig om vurderingen er endelig, indikativ eller avvist)
        # 2. VURDERING AV DATAGRUNNLAG
        # 3. KARTLEGGING OG PINPOINTING AV STØY (Henvis gjerne til de fargede sirklene på vedleggene bakerst)
        # 4. LYDFORHOLD INNENDØRS OG PLANLØSNING
        # 5. KRAV TIL FASADEISOLASJON
        # 6. TILTAK OG VIDERE PROSJEKTERING
        """
        
        prompt_parts = [prompt_text] + images_for_ai

        try:
            res = model.generate_content(prompt_parts)
            ai_raw_text = res.text
            
            # --- PYTHON TEGNE-ROBOTEN ---
            clean_text = ai_raw_text
            json_match = re.search(r'```json\s*(.*?)\s*```', ai_raw_text, re.DOTALL)
            
            if json_match:
                try:
                    markers = json.loads(json_match.group(1))
                    clean_text = re.sub(r'```json\s*.*?\s*```', '', ai_raw_text, flags=re.DOTALL) # Fjerner JSON fra rapporten
                    
                    for marker in markers:
                        idx = int(marker.get("image_index", 0))
                        if idx < len(images_for_ai):
                            img = images_for_ai[idx]
                            draw = ImageDraw.Draw(img)
                            w, h = img.size
                            
                            # Regn ut piksel-posisjon ut fra prosent
                            x = int((marker.get("x_pct", 50) / 100.0) * w)
                            y = int((marker.get("y_pct", 50) / 100.0) * h)
                            db_str = str(marker.get("db", "??")) + " dB"
                            
                            color_name = marker.get("color", "red").lower()
                            if "green" in color_name:
                                color_rgb = (46, 204, 113) # Pen grønnfarge
                            elif "yellow" in color_name:
                                color_rgb = (241, 196, 15) # Pen gulfarge
                            else:
                                color_rgb = (231, 76, 60) # Alvorlig rødfarge
                            
                            # Tegn en stor, tydelig sirkel
                            radius = int(w * 0.05)
                            draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color_rgb, width=max(4, int(w*0.008)))
                            
                            # Prøv å bruke en font, eller fallback til standard tegning
                            try:
                                font = ImageFont.truetype("arial.ttf", int(w * 0.03))
                            except:
                                font = ImageFont.load_default()
                            
                            # Tegn bakgrunnsboks for teksten slik at den blir lesbar over kartet
                            try:
                                bbox = draw.textbbox((0,0), db_str, font=font)
                                tw = bbox[2] - bbox[0]
                                th = bbox[3] - bbox[1]
                            except:
                                tw, th = 60, 20
                                
                            draw.rectangle((x - tw/2 - 8, y - th/2 - 8, x + tw/2 + 8, y + th/2 + 8), fill=color_rgb)
                            draw.text((x - tw/2, y - th/2), db_str, fill=(255, 255, 255), font=font)
                            
                            # Oppdaterer bildet i listen slik at FPDF får den overtegnede versjonen
                            images_for_ai[idx] = img

                except Exception as e:
                    print(f"Feil under tegning: {e}")
            
            with st.spinner("Kompilerer Akustikk-PDF og fletter inn tegninger med tegnede sirkler..."):
                pdf_data = create_full_report_pdf(p_name, pd_state['c_name'], clean_text, images_for_ai)
                
                if "pending_reviews" not in st.session_state:
                    st.session_state.pending_reviews = {}
                if "review_counter" not in st.session_state:
                    st.session_state.review_counter = 1
                    
                doc_id = f"PRJ-{datetime.now().strftime('%y')}-AKU{st.session_state.review_counter:03d}"
                st.session_state.review_counter += 1
                
                ai_text_lower = clean_text.lower()
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
