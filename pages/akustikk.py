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
import gc  # MINNERYDDER FOR STREAMLIT CLOUD
from PIL import Image, ImageDraw, ImageFont

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Akustikk Pro | Builtly AI", layout="wide")
genai.configure(api_key="AIzaSyCMsSGwIy7necJYMEjI1BSNY4A-OEHW9eM")

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

def get_model():
    """Skuddsikker tilkobling for Streamlit Cloud (Bypasser list_models som gir 404)"""
    return genai.GenerativeModel('gemini-1.5-flash')

def clean_pdf_text(text):
    """Renser tekst for PDF-motoren, men bevarer ÆØÅ"""
    if not text: return ""
    rep = {"–": "-", "—": "-", "“": "\"", "”": "\"", "‘": "'", "’": "'", "…": "...", "•": "*", "²": "2", "³": "3"}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode('latin-1', 'replace').decode('latin-1')

def ironclad_text_formatter(text):
    """Fjerner alt som kan krasje PDF-en"""
    text = re.sub(r'[-|_|=]{4,}', ' ', text)
    text = re.sub(r'([^\s]{40})', r'\1 ', text)
    text = text.replace('**', '').replace('__', '')
    return clean_pdf_text(text)

# --- 2. BEREGNINGSMOTOR (GRAFIKK) ---
def generate_pro_stoykart(img, adt, speed, dist, floor_num):
    w, h = img.size
    draw_img = img.convert("RGBA")
    overlay = Image.new("RGBA", draw_img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    floor_h = (floor_num - 1) * 3.0 + 1.5
    base_db = 10 * math.log10(max(adt, 1)) + 20 * math.log10(max(speed, 30)/50.0) + 14
    
    try: font_large = ImageFont.truetype("arial.ttf", int(h/35))
    except: font_large = ImageFont.load_default()

    # Tegn støysoner (gradient fra kilden)
    for y in range(int(h*0.3), h, 10):
        d_m = dist + (h - y) * (40/h)
        db_at_y = base_db - 10 * math.log10(max(d_m, 1) / 10.0)
        if db_at_y > 60: color = (255, 0, 0, 40)
        elif db_at_y > 55: color = (255, 255, 0, 40)
        else: color = (0, 255, 0, 20)
        draw.rectangle([0, y, w, y+10], fill=color)

    # Tegn Fasadepunkter
    points = []
    x_positions = np.linspace(0.15*w, 0.85*w, 8)
    y_positions = [0.45*h, 0.6*h] 
    for x in x_positions:
        for y in y_positions:
            d_m = dist + (h - y) * (40/h)
            d_3d = math.sqrt(d_m**2 + floor_h**2)
            db = int(base_db - 10 * math.log10(d_3d / 10.0))
            dot_color = (200, 0, 0, 255) if db >= 60 else ((200, 150, 0, 255) if db >= 55 else (50, 150, 50, 255))
            r = int(h/70)
            
            draw.ellipse([x-r-2, y-r-2, x+r+2, y+r+2], fill=(255,255,255,200))
            draw.ellipse([x-r, y-r, x+r, y+r], fill=dot_color, outline=(0,0,0,255))
            draw.text((x-r/1.5, y-r/1.5), str(db), fill="white" if db>=55 else "black", font=font_large)
            points.append(db)

    # Tegningsramme (Legend)
    box_w, box_h = int(w*0.3), int(h*0.2)
    draw.rectangle([w-box_w, h-box_h, w-10, h-10], fill=(255,255,255,240), outline="black", width=3)
    draw.text((w-box_w+20, h-box_h+20), f"AKUSTISK KARTLEGGING", fill="black", font=font_large)
    draw.text((w-box_w+20, h-box_h+60), f"PLAN {floor_num} | Parametere Lden", fill="black", font=font_large)
    draw.text((w-box_w+20, h-box_h+100), f"Maksimalt beregnet nivå: {max(points)} dB", fill=(200,0,0) if max(points)>=60 else "black", font=font_large)

    out = Image.alpha_composite(draw_img, overlay)
    return out.convert("RGB"), points

# --- 3. DYNAMISK PDF MOTOR ---
class BuiltlyProPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_y(15)
            self.set_font('Helvetica', 'B', 10); self.set_text_color(26, 43, 72)
            self.cell(0, 10, clean_pdf_text(f"PROSJEKT: {self.p_name} | Dokumentnr: AKU-001"), 0, 1, 'R')
            self.set_draw_color(200, 200, 200); self.line(25, 25, 185, 25)
            self.set_y(30)

    def footer(self):
        self.set_y(-15); self.set_font('Helvetica', 'I', 8); self.set_text_color(150, 150, 150)
        self.cell(0, 10, clean_pdf_text(f'UTKAST - KREVER FAGLIG KONTROLL | Side {self.page_no()}'), 0, 0, 'C')

    def check_space(self, height):
        if self.get_y() + height > 270: self.add_page()

def create_full_report_pdf(name, client, content, maps):
    pdf = BuiltlyProPDF()
    pdf.p_name = name.upper()
    pdf.set_margins(25, 25, 25); pdf.set_auto_page_break(True, 25)
    
    # FORSIDE
    pdf.add_page()
    if os.path.exists("logo.png"): pdf.image("logo.png", x=25, y=20, w=50)
    pdf.set_y(100); pdf.set_font('Helvetica', 'B', 28); pdf.set_text_color(26, 43, 72)
    pdf.cell(0, 15, clean_pdf_text("STØYFAGLIG UTREDNING"), 0, 1, 'L')
    pdf.set_font('Helvetica', '', 18); pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, clean_pdf_text(f"FOR RAMMETILLATELSE: {pdf.p_name}"), 0, 1, 'L')
    pdf.ln(40)
    
    for l, v in [("OPPDRAGSGIVER:", client), ("DATO:", datetime.now().strftime("%d. %m. %Y")), ("UTARBEIDET AV:", "Builtly RIAKU AI Engine"), ("KONTROLLERT AV:", "[Ansvarlig Prosjekterende]")]:
        pdf.set_font('Helvetica', 'B', 10); pdf.cell(50, 8, clean_pdf_text(l), 0, 0)
        pdf.set_font('Helvetica', '', 10); pdf.cell(0, 8, clean_pdf_text(v), 0, 1)

    # INNHOLDSFORTEGNELSE
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 16); pdf.set_text_color(26, 43, 72); pdf.cell(0, 20, "INNHOLDSFORTEGNELSE", 0, 1); pdf.ln(5)
    toc = [
        "1. SAMMENDRAG OG KONKLUSJON", "2. INNLEDNING", "3. KRAV OG RETNINGSLINJER (T-1442 / NS
