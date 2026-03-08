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
import requests
import urllib.parse
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

# --- 2. KARTVERKET MOTOR FOR BRANNSMITTE ---
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
    for q in queries:
        adr_tekst, kom, nord, ost = api_call(q)
        if nord and ost: return f"✅ Lokasjon bekreftet (N {nord}, Ø {ost}).", nord, ost

    return "❌ Fant ingen treff i Kartverket.", None, None

def fetch_kartverket_flyfoto(nord, ost):
    try:
        if not nord or not ost: return None, "Mangler koordinater"
        min_x, max_x = float(ost) - 150, float(ost) + 150
        min_y, max_y = float(nord) - 150, float(nord) + 150
        headers = {'User-Agent': 'Mozilla/5.0'}
        url_orto = f"https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
        r1 = requests.get(url_orto, headers=headers, timeout=6)
        if r1.status_code == 200 and len(r1.content) > 5000:
            return Image.open(io.BytesIO(r1.content)).convert('RGB'), "Ortofoto (For vurdering av brannsmitte)"
    except Exception: pass
    return None, "Feil ved nedlasting"

# --- 3. PREMIUM CSS ---
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
    
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }

    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }
    .brand-left { display: flex; align-items: center; gap: 0.9rem; min-width: 0; }
    .topbar-right { display: flex; align-items: center; justify-content: flex-end; gap: 0.65rem; padding: 0.35rem; border-radius: 18px; background: rgba(255,255,255,0.025); border: 1px solid rgba(120,145,170,0.12); flex-wrap: nowrap !important; }
    .top-link { display: inline-flex; align-items: center; justify-content: center; min-height: 42px; padding: 0.72rem 1.2rem; border-radius: 12px; text-decoration: none !important; font-weight: 650; font-size: 0.93rem; transition: all 0.2s ease; border: 1px solid transparent; white-space: nowrap; }
    .top-link.ghost { color: var(--soft) !important; background: rgba(255,255,255,0.04); border-color: rgba(120,145,170,0.18); }
    .top-link.ghost:hover { color: #ffffff !important; border-color: rgba(56,194,201,0.38); background: rgba(255,255,255,0.06); }
    
    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important;}

    .stTextInput input, .stNumberInput input, .stTextArea textarea { background-color: #0d1824 !important; color: #ffffff !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus { border-color: #38bdf8 !important; box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.5) !important; }
    div[data-baseweb="select"] > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    div[data-baseweb="select"] span { color: #ffffff !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label { color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important; }
    
    div[data-testid="stExpander"] { background: #0c1520 !important; border: 1px solid rgba(120,145,170,0.2) !important; border-radius: 12px !important; margin-bottom: 1rem !important; }
    div[data-testid="stExpanderDetails"] { background: transparent !important; color: #f5f7fb !important; }
    div[data-testid="stExpanderDetails"] > div > div > div { background-color: transparent !important; }
    
    [data-testid="stFileUploaderDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important; border-radius: 12px !important; padding: 2rem !important; }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #38c2c9 !important; background-color: rgba(56, 194, 201, 0.05) !important; }
    [data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
    [data-testid="stFileUploaderFileData"] { background-color: rgba(255,255,255,0.02) !important; color: #f5f7fb !important; border-radius: 8px !important;}
    
    [data-testid="stAlert"] { background-color: rgba(56, 189, 248, 0.05) !important; border: 1px solid rgba(56, 189, 248, 0.2) !important; border-radius: 12px !important
