# -*- coding: utf-8 -*-
import base64
import io
import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import google.generativeai as genai
except Exception:
    genai = None

try:
    import fitz
except Exception:
    fitz = None

# ------------------------------------------------------------
# 1. TEKNISK OPPSETT
# ------------------------------------------------------------
st.set_page_config(
    page_title="Konstruksjon (RIB) | Builtly",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DB_DIR = Path("qa_database")
IMG_DIR = DB_DIR / "project_images"
SSOT_FILE = DB_DIR / "ssot.json"
DB_DIR.mkdir(exist_ok=True)
IMG_DIR.mkdir(exist_ok=True)

google_key = os.environ.get("GOOGLE_API_KEY")
if genai is None:
    st.error("Kritisk feil: Python-pakken 'google.generativeai' er ikke tilgjengelig.")
    st.stop()
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables'.")
    st.stop()


# ------------------------------------------------------------
# 2. HJELPEFUNKSJONER & CSS
# ------------------------------------------------------------
def render_html(html_string: str) -> None:
    st.markdown(html_string.replace("\n", " "), unsafe_allow_html=True)

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

def clean_pdf_text(text: Any) -> str:
    if text is None: return ""
    text = str(text)
    rep = {"–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "•": "-", "≤": "<=", "≥": ">="}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")

def ironclad_text_formatter(text: Any) -> str:
    text = clean_pdf_text(text)
    text = text.replace("$", "").replace("*", "").replace("_", "")
    text = re.sub(r"[-|=]{3,}", " ", text)
    text = re.sub(r"([^\s]{40})", r"\1 ", text)
    return re.sub(r"\s+", " ", text).strip()

def get_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size=size)
            except Exception: pass
    return ImageFont.load_default()

def wrap_text_px(text: str, font, max_width: int) -> List[str]:
    text = clean_pdf_text(text)
    if not text: return [""]
    words = text.split()
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.getbbox(candidate)[2] <= max_width: current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines

def save_temp_image(img: Image.Image, suffix: str = ".png") -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    img.save(tmp.name)
    tmp.close()
    return tmp.name

st.markdown("""
<style>
    #MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}
    :root { --bg: #06111a; --panel: rgba(10, 22, 35, 0.78); --text: #f5f7fb; --accent: #38bdf8; }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1400px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; font-weight: 750 !important; border-radius: 12px !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; }
    div[data-baseweb="base-input"], div[data-baseweb="select"] > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; }
    .stTextInput input, .stNumberInput input, div[data-baseweb="select"] * { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; background-color: transparent !important; }
    div[data-testid="stExpander"] details { background-color: #0c1520 !important; border: 1px solid rgba(120,145,170,0.2) !important; border-radius: 12px !important; }
    [data-testid="stAlert"] { background-color: rgba(56, 194, 201, 0.05) !important; border: 1px solid rgba(56, 194, 201, 0.2) !important; border-radius: 12px !important; color: #f5f7fb !important; }
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------
# 3. STATE & PROSJEKTDATA
# ------------------------------------------------------------
if "project_data" not in st.session_state:
    st.session_state.project_data = {"p_name": "", "b_type": "Næring", "etasjer": 1, "bta": 0}
    if SSOT_FILE.exists():
        try:
            with open(SSOT_FILE, "r", encoding="utf-8") as f:
                st.session_state.project_data = json.load(f)
        except Exception: pass

pd_state = st.session_state.project_data
if not pd_state.get("p_name"):
    st.warning("⚠️ Handling kreves: Du må sette opp prosjektdata i Project Setup.")
    if find_page("Project") and st.button("⚙️ Gå til Project Setup", type="primary"): st.switch_page(find_page("Project"))
    st.stop()

if "rib_phase" not in st.session_state: st.session_state.rib_phase = "setup" 

top_l, top_r = st.columns([4, 1])
with top_l: render_html(f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>')
with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("← Tilbake til SSOT", use_container_width=True, type="secondary"): st.switch_page(find_page("Project"))
st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)


# ------------------------------------------------------------
# 4. ALTERNATIVSTUDIE BÆRESYSTEM
# ------------------------------------------------------------
def build_structural_candidates(mat: str, opt: str, fund: str) -> pd.DataFrame:
    systems = [
        {"System": "Plasstøpt betong med flatdekker", "Materiale": "Plasstøpt", "Spenn": "6-9 m", "Avstivning": "Kjerner/skiver", "Rasjonalitet": 60, "Robusthet": 80, "Vekt": "Høy"},
        {"System": "Prefabrikkert betong/stål", "Materiale": "Prefab", "Spenn": "7-12 m", "Avstivning": "Kjerne/kryss", "Rasjonalitet": 75, "Robusthet": 70, "Vekt": "Høy"},
        {"System": "Massivtre (CLT/limtre)", "Materiale": "Tre", "Spenn": "4-7 m", "Avstivning": "CLT-skiver", "Rasjonalitet": 65, "Robusthet": 60, "Vekt": "Lav"},
        {"System": "Stålrammer m/ hulldekker", "Materiale": "Stål", "Spenn": "8-14 m", "Avstivning": "Kryss/Kjerne", "Rasjonalitet": 80, "Robusthet": 65, "Vekt": "Middels"},
    ]
    for s in systems:
        if mat.lower() in s["Materiale"].lower(): s["Rasjonalitet"] += 15
        if "Lav vekt" in opt and s["Vekt"] == "Lav": s["Rasjonalitet"] += 15
        if "Rasjonalitet" in opt and s["Materiale"] in ["Prefab", "Stål"]: s["Rasjonalitet"] += 10
        if "Robusthet" in opt and s["Materiale"] == "Plasstøpt": s["Robusthet"] += 15
        if "Fjell" in fund and s["Vekt"] == "Høy": s["Robusthet"] += 5
        s["Total"] = int(s["Rasjonalitet"] * 0.6 + s["Robusthet"] * 0.4)
        
    df = pd.DataFrame(systems).sort_values("Total", ascending=False).reset_index(drop=True)
    df.insert(0, "Prioritet", range(1, len(df)+1))
    df.insert(1, "Anbefalt", ["JA"] + [""] * (len(df)-1))
    return df


# ------------------------------------------------------------
# 5. TEGNINGSPARSING
# ------------------------------------------------------------
def classify_plan(name: str) -> Tuple[str, int]:
    low = name.lower()
    if any(k in low for k in ["kjeller", "u1", "u2", "parkering", "basement", "p-", "underetasje"]): return "Kjeller / Parkering", -1
    if any(k in low for k in ["1. etg", "plan 1", "næring", "ground"]): return "1. Etasje / Næring", 0
    if any(k in low for k in ["tak", "roof"]): return "Takplan", 99
    nums = re.findall(r'\d+', low)
    return "Boligplan", int(nums[0]) if nums else 1

def load_drawings(files) -> List[Dict]:
    drawings = []
    if not files: return drawings
    for f in files:
        name = clean_pdf_text(f.name)
        if name.lower().endswith(".pdf") and fitz:
            doc = fitz.open(stream=f.read(), filetype="pdf")
            for page_num in range(min(4, len(doc))):
                pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                ptype, lvl = classify_plan(f"{name} (s.{page_num+1})")
                drawings.append({"name": f"{name} (s.{page_num+1})", "image": img, "level": lvl, "type": ptype})
        else:
            try: 
                ptype, lvl = classify_plan(name)
                drawings.append({"name": name, "image": Image.open(f).convert("RGB"), "level": lvl, "type": ptype})
            except: pass
    
    drawings.sort(key=lambda x: x["level"]) # Sorter bottom-up for vertikal kontinuitet
    return drawings


# ------------------------------------------------------------
# 6. AI GEOMETRIMOTOR & TRANSFER LOGIKK
# ------------------------------------------------------------
def extract_structural_geometry_ai(model, img: Image.Image, ptype: str) -> List[Dict]:
    prompt = f"""
    Du er en senior RIB (Rådgivende Ingeniør Bygg) AI.
    Analyser denne arkitekttegningen (plantype: {ptype}) for å foreslå et rasjonelt bæresystem.
    
    VIKTIGE REGLER FOR BÆRESYSTEMET:
    1. Orto-grid: Søyler og vegger MÅ ligge på linje. Gjenbruk de samme X- og Y-koordinatene for å danne et rutenett (f.eks. X-akser på 0.20, 0.50, 0.80 og Y-akser på 0.30, 0.60).
    2. Kjeller / Parkering: Plasser "Søyle" i grid-kryssene. Unngå søyler midt i kjørebaner.
    3. Boligplan: Bruk bærende "Skive" i leilighetsskiller (typisk på tvers). Unngå søyler midt i soverom/stuer.
    4. Kjerner: Finn trappe-/heissjakter og plasser en "Kjerne".
    5. Koordinater (X, Y) er normaliserte fra 0.05 til 0.95 (0,0 er øverst til venstre). Hold deg unna tittelfeltet (X>0.8, Y>0.8).
    
    Svar KUN med et gyldig JSON-array. Ikke skriv markdown, kun selve dataene.
    Format:
    [
      {{"Type": "Søyle", "X": 0.25, "Y": 0.30, "W": 0.0, "H": 0.0, "Label": "S1"}},
      {{"Type": "Kjerne", "X": 0.45, "Y": 0.45, "W": 0.08, "H": 0.12, "Label": "Trapp"}},
      {{"Type": "Skive", "X": 0.60, "Y": 0.30, "W": 0.02, "H": 0.25, "Label": "V1"}}
    ]
    """
    try:
        # Sender høyoppløselig bilde for at AI skal kunne lese romtyper
        img_copy = img.copy()
        img_copy.thumbnail((2048, 2048)) 
        resp = model.generate_content([prompt, img_copy], generation_config={"temperature": 0.1})
        
        txt = resp.text.strip()
        txt = re.sub(r"^```(?:json)?", "", txt, flags=re.IGNORECASE).strip()
        txt = re.sub(r"```$", "", txt).strip()
        
        start = txt.find("[")
        end = txt.rfind("]")
        if start != -1 and end != -1:
            return json.loads(txt[start:end+1])
        return []
    except Exception as e:
        print(f"AI Geometriforslag feilet: {e}")
        return []

def apply_transfer_logic(drawings: List[Dict]):
    """ Sjekker om bæring i etasjen over lander utenfor bæring i etasjen under (F.eks over P-kjeller). """
    for i in range(1, len(drawings)):
        lower, upper = drawings[i-1]["elements_df"], drawings[i]["elements_df"]
        if upper.empty or lower.empty: continue
        
        for idx, u_row in upper.iterrows():
            if u_row["Type"] in ["Søyle", "Skive", "Kjerne"]:
                supported = False
                for _, l_row in lower.iterrows():
                    if l_row["Type"] in ["Søyle", "Skive", "Kjerne"]:
                        dist = math.hypot(u_row["X"] - l_row["X"], u_row["Y"] - l_row["Y"])
                        if dist < 0.08: # Toleranse på 8%
                            supported = True
                            break
                drawings[i]["elements_df"].at[idx, "Transfer"] = not supported


# ------------------------------------------------------------
# 7. VISUELL RENDERER (OVERLAY PÅ TEGNING)
# ------------------------------------------------------------
def render_overlay(img_pil: Image.Image, df: pd.DataFrame) -> Image.Image:
    base = img_pil.copy().convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0,0,0,0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    w, h = base.size
    font = get_font(max(14, int(min(w,h)*0.015)), bold=True)
            
    for _, row in df.iterrows():
        if not row.get("Behold", True): continue
        x, y = int(row["X"]*w), int(row["Y"]*h)
        ew, eh = int(row.get("W", 0.0)*w), int(row.get("H", 0.0)*h)
        lbl = str(row.get("Label", ""))
        is_transfer = row.get("Transfer", False)
        
        if row["Type"] == "Søyle":
            r = max(8, int(min(w,h)*0.012))
            col = (255, 80, 80, 255) if is_transfer else (56, 194, 201, 255)
            draw.ellipse((x-r, y-r, x+r, y+r), fill=col, outline=(255,255,255,255), width=2)
            draw.text((x+r+4, y-r), lbl + (" (Transfer!)" if is_transfer else ""), fill=(10,22,35,255), font=font)
            
        elif row["Type"] == "Kjerne":
            ew, eh = ew if ew > 0 else int(0.08*w), eh if eh > 0 else int(0.12*h)
            draw.rounded_rectangle((x, y, x+ew, y+eh), radius=6, fill=(255, 196, 64, 180), outline=(255, 196, 64, 255), width=3)
            draw.text((x+4, y+4), lbl, fill=(30,30,30,255), font=font)
            
        elif row["Type"] == "Skive":
            ew, eh = ew if ew > 0 else int(0.02*w), eh if eh > 0 else int(0.3*h)
            col = (255, 80, 80, 255) if is_transfer else (255, 153, 153, 255)
            draw.line((x, y, x, y+eh), fill=col, width=max(6, int(min(w,h)*0.008)))
            draw.text((x+10, y+eh//2), lbl + (" (Transfer!)" if is_transfer else ""), fill=(35,35,35,255), font=font)
            
        elif row["Type"] == "Spennpil":
            ew = ew if ew > 0 else int(0.3*w)
            draw.line((x, y, x+ew, y), fill=(196, 235, 176, 255), width=4)
            draw.polygon([(x+ew, y), (x+ew-15, y-10), (x+ew-15, y+10)], fill=(196, 235, 176, 255))
            draw.text((x+ew//2, y-20), lbl, fill=(30,30,30,255), font=font)

    return Image.alpha_composite(base, overlay).convert("RGB")


# ------------------------------------------------------------
# 8. AI RAPPORT MOTOR (Skriver ut fra godkjent geometri)
# ------------------------------------------------------------
def run_ai_report(model, pd_state, drawings, recommended_sys):
    locked_geom = []
    for d in drawings:
        df = d["elements_df"]
        active = df[df["Behold"] == True].to_dict(orient="records")
        locked_geom.append({"Tegning": d["name"], "Type": d["type"], "Elementer": active})
        
    prompt = f"""
    Du er Builtly RIB AI, en senior rådgivende ingeniør.
    Brukeren har fagkontrollert og låst bæresystemet for {pd_state['p_name']}.
    
    LÅST GEOMETRI (ETASJE FOR ETASJE):
    {json.dumps(locked_geom, ensure_ascii=False)}
    
    ANBEFALT SYSTEM FRA MATRISE: {recommended_sys}
    
    Skriv et konkret konseptnotat. Beskriv lastveiene NØYAKTIG ut fra den låste geometrien over.
    Dersom du ser elementer som har "Transfer: true", SKAL du kommentere at dette krever forsterkninger, drager eller tykkere dekke, da lasten ikke føres direkte ned til fundament.
    
    Svar KUN med JSON (Uten markdown formatting):
    {{
      "grunnlag_status": "DELVIS",
      "risk_register": [ {{"topic": "risiko", "severity": "Middels", "mitigation": "tiltak"}} ],
      "report_markdown": "# 1. SAMMENDRAG OG KONKLUSJON\\n...\\n# 2. VURDERING AV DATAGRUNNLAG\\n...\\n# 3. KONSEPT FOR BÆRESYSTEM OG STABILITET\\n...\\n# 4. VERTIKAL KONTINUITET OG TRANSFER\\n...\\n# 5. FUNDAMENTERING OG LASTER\\n...\\n# 6. RISIKO OG NESTE STEG"
    }}
    """
    try:
        resp = model.generate_content([prompt], generation_config={"temperature": 0.2})
        cleaned = re.sub(r"^```json", "", resp.text.strip(), flags=re.I).strip()
        cleaned = re.sub(r"^```", "", cleaned).strip()
        return json.loads(cleaned)
    except Exception:
        return {"report_markdown": "# 1. Feil\nKunne ikke generere rapport pga AI timeout.", "risk_register": []}


# ------------------------------------------------------------
# 9. TABELLRENDERER FOR PDF
# ------------------------------------------------------------
def render_table_image(df: pd.DataFrame, title: str, subtitle: str = "", row_fill_column: str = None) -> Image.Image:
    df = df.copy().fillna("")
    title, subtitle = clean_pdf_text(title), clean_pdf_text(subtitle)
    font_title, font_subtitle = get_font(34, bold=True), get_font(18, bold=False)
    font_header, font_body = get_font(18, bold=True), get_font(16, bold=False)

    side_pad, top_pad, cell_pad_x, cell_pad_y, table_width = 28, 24, 10, 9, 1540
    width_weights = [0.85 if str(c) in {"Prioritet", "Anbefalt", "Total", "Alvorlighet"} else 2.2 if "tiltak" in str(c).lower() else 1.4 for c in df.columns]
    col_widths = [max(90, int(table_width * w / sum(width_weights))) for w in width_weights]

    header_height = 0
    header_wrapped = {}
    for col, width in zip(df.columns, col_widths):
        wrapped = wrap_text_px(str(col), font_header, width - (cell_pad_x * 2))
        header_wrapped[col] = wrapped
        header_height = max(header_height, len(wrapped) * 24 + (cell_pad_y * 2))

    row_heights, wrapped_cells = [], []
    for ridx in range(len(df)):
        row = df.iloc[ridx]
        row_wrap, row_height = {}, 0
        for col, width in zip(df.columns, col_widths):
            wrapped = wrap_text_px(str(row[col]), font_body, width - (cell_pad_x * 2))
            row_wrap[col] = wrapped
            row_height = max(row_height, len(wrapped) * 22 + (cell_pad_y * 2))
        row_heights.append(max(34, row_height))
        wrapped_cells.append(row_wrap)

    image_width, image_height = table_width + side_pad * 2, top_pad + 66 + 28 + 14 + header_height + sum(row_heights) + 28
    img = Image.new("RGB", (image_width, image_height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((12, 12, image_width - 12, image_height - 12), radius=18, outline=(219, 225, 232), width=2, fill=(255, 255, 255))
    draw.rounded_rectangle((18, 18, image_width - 18, 18 + 66 + 28 + 10), radius=16, fill=(236, 240, 245))
    draw.text((side_pad, 28), title, font=font_title, fill=(29, 45, 68))
    if subtitle: draw.text((side_pad, 28 + 40), subtitle, font=font_subtitle, fill=(96, 108, 122))

    x, y = side_pad, top_pad + 66 + 28 + 10
    for col, width in zip(df.columns, col_widths):
        draw.rectangle((x, y, x + width, y + header_height), fill=(46, 62, 84))
        yy = y + cell_pad_y
        for line in header_wrapped[col]:
            draw.text((x + cell_pad_x, yy), clean_pdf_text(line), font=font_header, fill=(255, 255, 255))
            yy += 24
        x += width

    y += header_height
    for ridx in range(len(df)):
        row = df.iloc[ridx]
        base_fill = (248, 250, 252) if ridx % 2 else (255, 255, 255)
        if row_fill_column and row_fill_column in row:
            val = str(row[row_fill_column]).strip()
            if val == "JA": base_fill = (232, 246, 233)
            elif val == "Høy": base_fill = (255, 204, 204)
            elif val == "Middels": base_fill = (255, 242, 153)
            
        x, row_height = side_pad, row_heights[ridx]
        for col, width in zip(df.columns, col_widths):
            draw.rectangle((x, y, x + width, y + row_height), fill=base_fill, outline=(205, 212, 220), width=1)
            yy = y + cell_pad_y
            for line in wrapped_cells[ridx][col]:
                draw.text((x + cell_pad_x, yy), clean_pdf_text(line), font=font_body, fill=(35, 38, 43))
                yy += 22
            x += width
        y += row_height
    return img


# ------------------------------------------------------------
# 10. PDF BYGGER (MED FIX FOR BYTEARRAY ERROR)
# ------------------------------------------------------------
class BuiltlyCorporatePDF(FPDF):
    def header(self):
        if self.page_no() == 1: return
        self.set_y(11)
        self.set_text_color(88, 94, 102)
        self.set_font("Helvetica", "", 8)
        self.cell(0, 4, clean_pdf_text(getattr(self, "header_left", "Prosjekt")), 0, 0, "L")
        self.cell(0, 4, clean_pdf_text("Builtly | RIB"), 0, 1, "R")
        self.set_draw_color(188, 192, 197)
        self.line(18, 18, 192, 18)
        self.set_y(24)

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(210, 214, 220)
        self.line(18, 285, 192, 285)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(110, 114, 119)
        self.cell(60, 5, clean_pdf_text("Builtly-RIB-001"), 0, 0, "L")
        self.cell(70, 5, clean_pdf_text("Fagkontrollert konsept"), 0, 0, "C")
        self.cell(0, 5, clean_pdf_text(f"Side {self.page_no()}"), 0, 0, "R")

    def ensure_space(self, needed_height: float):
        if self.get_y() + needed_height > 272: self.add_page()

    def section_title(self, title: str):
        self.ensure_space(20)
        self.ln(2)
        title = ironclad_text_formatter(title)
        num_match = re.match(r"^(\d+\.?\d*)\s*(.*)$", title)
        number = num_match.group(1).rstrip(".") if num_match and (num_match.group(1).endswith(".") or num_match.group(2)) else None
        text = num_match.group(2).strip() if number else title

        self.set_font("Helvetica", "B", 17)
        self.set_text_color(36, 50, 72)
        start_y = self.get_y()
        if number:
            self.set_xy(20, start_y)
            self.cell(12, 8, clean_pdf_text(number), 0, 0, "L")
            self.set_xy(34, start_y)
            self.multi_cell(156, 8, clean_pdf_text(text.upper()), 0, "L")
        else:
            self.set_xy(20, start_y)
            self.multi_cell(170, 8, clean_pdf_text(text.upper()), 0, "L")
        self.set_draw_color(204, 209, 216)
        self.line(20, self.get_y() + 1, 190, self.get_y() + 1)
        self.ln(5)

    def body_paragraph(self, text: str, first: bool = False):
        text = ironclad_text_formatter(text)
        if not text: return
        self.set_x(20)
        self.set_font("Helvetica", "", 10.2 if not first else 10.6)
        self.set_text_color(35, 39, 43)
        self.multi_cell(170, 5.5 if not first else 5.7, clean_pdf_text(text))
        self.ln(1.6)

    def subheading(self, text: str):
        text = ironclad_text_formatter(text.replace("##", "").rstrip(":"))
        self.ensure_space(14)
        self.ln(2)
        self.set_x(20)
        self.set_font("Helvetica", "B", 10.8)
        self.set_text_color(48, 64, 86)
        self.cell(0, 6, clean_pdf_text(text.upper()), 0, 1)
        self.set_draw_color(225, 229, 234)
        self.line(20, self.get_y(), 190, self.get_y())
        self.ln(2)

    def bullets(self, items: List[str]):
        for idx, item in enumerate(items, start=1):
            clean = ironclad_text_formatter(item)
            if not clean: continue
            self.ensure_space(10)
            self.set_font("Helvetica", "", 10.1)
            self.set_text_color(35, 39, 43)
            start_y = self.get_y()
            self.set_xy(22, start_y)
            self.cell(6, 5.2, "-", 0, 0, "L")
            self.set_xy(28, start_y)
            self.multi_cell(162, 5.2, clean_pdf_text(clean))
            self.ln(0.8)

    def kv_card(self, items, x=None, width=80, title=None):
        if x is None: x = self.get_x()
        height = 10 + (len(items) * 6.3) + (7 if title else 0)
        self.ensure_space(height + 3)
        start_y = self.get_y()
        self.set_fill_color(245, 247, 249)
        self.set_draw_color(214, 219, 225)
        try: self.rounded_rect(x, start_y, width, height, 4, "DF")
        except: self.rect(x, start_y, width, height, "DF")
        yy = start_y + 5
        if title:
            self.set_xy(x + 4, yy)
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(48, 64, 86)
            self.cell(width - 8, 5, clean_pdf_text(title.upper()), 0, 1)
            yy += 7
        for label, value in items:
            self.set_xy(x + 4, yy)
            self.set_font("Helvetica", "B", 8.6)
            self.set_text_color(72, 79, 87)
            self.cell(28, 5, clean_pdf_text(label), 0, 0)
            self.set_font("Helvetica", "", 8.6)
            self.set_text_color(35, 39, 43)
            self.multi_cell(width - 34, 5, clean_pdf_text(value))
            yy = self.get_y() + 1
        self.set_y(max(self.get_y(), start_y + height))

    def figure_image(self, image_path: str, width=170, caption=""):
        img = Image.open(image_path)
        height = width * (img.height / img.width)
        self.ensure_space(height + 15)
        x, y = 20, self.get_y()
        self.image(image_path, x=x, y=y, w=width)
        self.set_y(y + height + 2)
        if caption:
            self.set_x(x)
            self.set_font("Helvetica", "I", 7.7)
            self.set_text_color(104, 109, 116)
            self.multi_cell(width, 4, clean_pdf_text(caption), 0, "C")
        self.set_y(y + height + 10)

def build_full_pdf(pd_state, report_json, cand_df, drawings) -> bytes:
    pdf = BuiltlyCorporatePDF("P", "mm", "A4")
    pdf.header_left = pd_state.get("p_name", "Prosjekt")
    pdf.set_auto_page_break(True, margin=22)
    pdf.set_margins(18, 18, 18)
    
    # Forside
    pdf.add_page()
    pdf.set_xy(20, 45)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(100, 105, 110)
    pdf.cell(80, 6, "KONSEPTNOTAT KONSTRUKSJON", 0, 1, "L")
    pdf.set_font("Helvetica", "B", 34)
    pdf.set_text_color(20, 28, 38)
    pdf.multi_cell(120, 12, clean_pdf_text(pd_state.get("p_name", "Konstruksjon")), 0, "L")
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(64, 68, 74)
    pdf.multi_cell(120, 6.5, "Bæresystem, stabilitet og verifiserte konseptskisser", 0, "L")
    
    pdf.set_xy(118, 45)
    pdf.kv_card([
        ("Oppdragsgiver", pd_state.get("c_name", "-")),
        ("Emne", "RIB / Konstruksjon"),
        ("Dato", datetime.now().strftime("%d.%m.%Y")),
        ("Status", report_json.get("grunnlag_status", "-"))
    ], x=118, width=72)
    
    if drawings:
        img_path = save_temp_image(drawings[0]["final_img"], ".jpg")
        pdf.set_xy(20, 115)
        pdf.figure_image(img_path, width=170, caption="Forsidefigur: Fagkontrollert konseptskisse")

    pdf.add_page()
    pdf.ensure_space(60)
    pdf.kv_card([
        ("Prosjekt", pd_state.get("p_name", "-")),
        ("Type", pd_state.get("b_type", "-")),
        ("Etasjer", str(pd_state.get("etasjer", "-"))),
        ("BTA", f"{pd_state.get('bta', 0)} m2")
    ], x=20, width=82, title="Prosjektgrunnlag")
    
    pdf.set_xy(108, pdf.get_y() - 41)
    pdf.kv_card([
        ("Status", report_json.get("grunnlag_status", "-")),
        ("Tegninger", str(len(drawings))),
        ("Regelverk", pd_state.get("land", "Norge"))
    ], x=108, width=82, title="Datagrunnlag")
    pdf.ln(8)

    # Innhold AI
    sections, current = [], None
    for raw_line in report_json.get("report_markdown", "").splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            if current: sections.append(current)
            current = {"title": ironclad_text_formatter(line.lstrip("#").strip()), "lines": []}
        elif current is not None:
            current["lines"].append(raw_line.rstrip())
    if current: sections.append(current)
    
    for sec in sections:
        pdf.section_title(sec["title"])
        paragraph_buffer, bullet_buffer = [], []
        
        def flush_p():
            if paragraph_buffer: pdf.body_paragraph(" ".join(paragraph_buffer))
            paragraph_buffer.clear()
        def flush_b():
            if bullet_buffer: pdf.bullets([re.sub(r"^([-*•]|\d+\.)\s+", "", item.strip()) for item in bullet_buffer])
            bullet_buffer.clear()

        for raw_line in sec.get("lines", []):
            line = raw_line.strip()
            if not line:
                flush_p(); flush_b(); continue
            if line.startswith("##"):
                flush_p(); flush_b(); pdf.subheading(line); continue
            if bool(re.match(r"^([-*•]|\d+\.)\s+", line)):
                flush_p(); bullet_buffer.append(line); continue
            flush_b(); paragraph_buffer.append(line)
        flush_p(); flush_b()

    # Matriser og Tabeller
    if not cand_df.empty:
        pdf.add_page()
        pdf.section_title("Vedlegg A: Alternativmatrise for bæresystem")
        cand_img = render_table_image(cand_df, "Alternativmatrise", "Maskinelt vurdert og rangert", row_fill_column="Anbefalt")
        pdf.figure_image(save_temp_image(cand_img), width=170, caption="Tabell A1: Vurdering av rasjonalitet og robusthet.")

    risks = report_json.get("risk_register", [])
    if risks:
        pdf.add_page()
        pdf.section_title("Vedlegg B: Risikoregister")
        risk_df = pd.DataFrame([{"Risiko": r.get("topic"), "Alvorlighet": r.get("severity"), "Tiltak": r.get("mitigation")} for r in risks])
        risk_img = render_table_image(risk_df, "Risikoregister", "Prosjektspesifikke faglige vurderinger", row_fill_column="Alvorlighet")
        pdf.figure_image(save_temp_image(risk_img), width=170, caption="Tabell B1: Identifiserte risikoer og tiltak for konseptfasen.")

    # Skisser
    for idx, dwg in enumerate(drawings):
        pdf.add_page()
        pdf.section_title(f"Vedlegg C{idx+1}: Konseptskisse ({dwg['name']})")
        pdf.figure_image(save_temp_image(dwg["final_img"], ".jpg"), width=170, caption=f"Fagkontrollert konseptskisse for {dwg['type']}.")

    # --- SIKKER PDF OUTPUT HÅNDTERING (Fikser Feilmeldingen) ---
    out = pdf.output(dest="S")
    if isinstance(out, bytearray):
        return bytes(out)
    elif isinstance(out, bytes):
        return out
    elif isinstance(out, str):
        return out.encode("latin-1", "replace")
    return bytes(out)


# ------------------------------------------------------------
# 11. STREAMLIT UI - HUMAN IN THE LOOP
# ------------------------------------------------------------
st.markdown("<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🏗️ RIB — Konstruksjon</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #9fb0c3; font-size: 1.1rem; margin-bottom: 2rem;'>AI Geometri-deteksjon, vertikal sporing og interaktiv fagkontroll før PDF-generering.</p>", unsafe_allow_html=True)

if st.session_state.rib_phase == "setup":
    with st.expander("1. Strategi og Opplasting", expanded=True):
        c1, c2 = st.columns(2)
        mat = c1.selectbox("Materialpreferanse", ["Plasstøpt betong", "Prefabrikkert betong/stål", "Massivtre (CLT)", "Stålrammer m/ hulldekker"])
        opt = c2.selectbox("Optimaliseringsmodus", ["Maks rasjonalitet / Repeterbarhet", "Lav egenvekt / Påbygg", "Maks robusthet"])
        fund = st.selectbox("Fundamentering", ["Fjell (Direkte)", "Peling", "Såle / Kompensert"])
        
        files = st.file_uploader("Last opp plan- og snittegninger (PDF/Bilde)", accept_multiple_files=True, type=["pdf", "png", "jpg"])

    if st.button("🚀 Kjør AI Geometrianalyse (Steg 1)", type="primary", use_container_width=True):
        if not files:
            st.error("Last opp minst én tegning først.")
            st.stop()
        with st.spinner("AI Vision analyserer planene for logiske bæringspunkter og bygningskonturer..."):
            valid_models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
            model_name = "models/gemini-1.5-pro" if "models/gemini-1.5-pro" in valid_models else valid_models[0]
            model = genai.GenerativeModel(model_name)
            
            drawings = load_drawings(files)
            for dwg in drawings:
                # 2. AI foreslår elementer semantisk
                elements = extract_structural_geometry_ai(model, dwg["image"], dwg["type"])
                
                valid_elements = []
                for e in elements:
                    valid_elements.append({
                        "Behold": True,
                        "Type": e.get("Type", "Søyle"),
                        "X": float(e.get("X", 0.5)),
                        "Y": float(e.get("Y", 0.5)),
                        "W": float(e.get("W", 0.0)),
                        "H": float(e.get("H", 0.0)),
                        "Label": str(e.get("Label", "")),
                        "Transfer": False
                    })
                    
                # Sikkerhetsnett hvis AI feiler totalt
                if not valid_elements:
                    valid_elements = [
                        {"Behold": True, "Type": "Kjerne", "X": 0.45, "Y": 0.45, "W": 0.1, "H": 0.12, "Label": "Trapp", "Transfer": False},
                        {"Behold": True, "Type": "Søyle", "X": 0.20, "Y": 0.20, "W": 0.0, "H": 0.0, "Label": "S1", "Transfer": False},
                        {"Behold": True, "Type": "Søyle", "X": 0.80, "Y": 0.20, "W": 0.0, "H": 0.0, "Label": "S2", "Transfer": False}
                    ]
                    
                dwg["elements_df"] = pd.DataFrame(valid_elements)
                
            apply_transfer_logic(drawings)
            
            st.session_state.rib_drawings = drawings
            st.session_state.cand_df = build_structural_candidates(mat, opt, fund)
            st.session_state.rib_phase = "review"
            st.rerun()

elif st.session_state.rib_phase == "review":
    st.success("✅ AI-forslag er klart. Du er nå i **Faglig Gjennomgang (Review)**.")
    st.info("Fjern haken ved 'Behold' for å slette feilplasserte elementer. For å legge til nye, skriv i den tomme raden nederst i tabellen. Skissene oppdateres live når du klikker utenfor tabellen.")

    drawings = st.session_state.rib_drawings
    
    for idx, dwg in enumerate(drawings):
        st.markdown(f"#### 📄 {dwg['name']} - Tolket som: `{dwg['type'].upper()}`")
        col_data, col_img = st.columns([1, 1.2])
        
        with col_data:
            df = dwg["elements_df"]
            edited_df = st.data_editor(
                df, key=f"editor_{idx}", num_rows="dynamic", use_container_width=True,
                column_config={
                    "Behold": st.column_config.CheckboxColumn("Behold?", default=True),
                    "Type": st.column_config.SelectboxColumn("Type", options=["Søyle", "Kjerne", "Skive", "Spennpil"]),
                    "X": st.column_config.NumberColumn("X (0-1)", min_value=0.0, max_value=1.0, format="%.3f", step=0.01),
                    "Y": st.column_config.NumberColumn("Y (0-1)", min_value=0.0, max_value=1.0, format="%.3f", step=0.01),
                    "W": st.column_config.NumberColumn("Bredde", min_value=0.0, max_value=1.0, format="%.3f"),
                    "H": st.column_config.NumberColumn("Høyde", min_value=0.0, max_value=1.0, format="%.3f"),
                    "Transfer": st.column_config.CheckboxColumn("Transfer", disabled=True)
                }
            )
            # Vasker tabellen slik at nye manuelt tillagte rader får trygge data (Hindre NaN krasj)
            edited_df["Behold"] = edited_df["Behold"].fillna(True).astype(bool)
            edited_df["Transfer"] = edited_df["Transfer"].fillna(False).astype(bool)
            if "Type" in edited_df.columns:
                edited_df["Type"] = edited_df["Type"].fillna("Søyle").astype(str)
            if "Label" in edited_df.columns:
                edited_df["Label"] = edited_df["Label"].fillna("Ny").astype(str)
            for col in ["X", "Y", "W", "H"]:
                if col in edited_df.columns:
                    edited_df[col] = pd.to_numeric(edited_df[col], errors="coerce").fillna(0.0)
                    
            dwg["elements_df"] = edited_df

        with col_img:
            img_overlay = render_overlay(dwg["image"], edited_df)
            st.image(img_overlay, use_container_width=True)
            dwg["final_img"] = img_overlay
            
        st.divider()

    col_back, col_go = st.columns(2)
    if col_back.button("← Avbryt og start på nytt", use_container_width=True):
        st.session_state.rib_phase = "setup"
        st.rerun()

    if col_go.button("🔒 Lås Geometri og Skriv Fagrapport", type="primary", use_container_width=True):
        with st.spinner("AI-analytiker vurderer den låste geometrien, lastveier og transfer-soner..."):
            valid_models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
            model_name = "models/gemini-1.5-pro" if "models/gemini-1.5-pro" in valid_models else valid_models[0]
            model = genai.GenerativeModel(model_name)
            
            top_sys = st.session_state.cand_df.iloc[0]["System"]
            report_json = run_ai_report(model, pd_state, st.session_state.rib_drawings, top_sys)
            
            with st.spinner("Bygger Builtly Corporate PDF..."):
                pdf_bytes = build_full_pdf(pd_state, report_json, st.session_state.cand_df, st.session_state.rib_drawings)
                
                st.session_state.rib_report_json = report_json
                st.session_state.rib_pdf_bytes = pdf_bytes
                
                if "pending_reviews" not in st.session_state: st.session_state.pending_reviews = {}
                doc_id = f"PRJ-{datetime.now().strftime('%y')}-RIB001"
                st.session_state.pending_reviews[doc_id] = {
                    "title": pd_state["p_name"], "module": "RIB (Konstruksjon)", "drafter": "Builtly AI",
                    "reviewer": "Senior Konstruktør", "status": "Pending Senior Review", "class": "badge-pending",
                    "pdf_bytes": pdf_bytes
                }
                
                st.session_state.rib_phase = "generated"
                st.rerun()

elif st.session_state.rib_phase == "generated":
    st.success("✅ Rapport og konseptskisser er ferdig fagkontrollert, skrevet og låst!")
    
    with st.expander("Vis tekstlig rapportutkast", expanded=False):
        st.markdown(st.session_state.rib_report_json.get("report_markdown", ""))
        
    c_dl, c_qa = st.columns(2)
    with c_dl:
        st.download_button(
            "📄 Last ned RIB Konseptnotat (PDF)", 
            st.session_state.rib_pdf_bytes, 
            f"Builtly_RIB_{pd_state.get('p_name','Prosjekt').replace(' ','_')}.pdf", 
            type="primary", use_container_width=True
        )
    with c_qa:
        if find_page("Review"):
            if st.button("🔍 Gå til QA for å godkjenne", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))
                
    if st.button("↻ Start ny analyse", type="secondary"):
        st.session_state.rib_phase = "setup"
        st.rerun()
