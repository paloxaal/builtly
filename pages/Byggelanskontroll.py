# -*- coding: utf-8 -*-
"""
Builtly | Byggelånskontroll
Utbetalingskontroll og trekkforespørsel-verifisering for bank og byggelånskontrollør.
Self-contained Streamlit module – no external builtly_* dependencies.
"""
from __future__ import annotations

import base64, io, json, os, re, textwrap
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    import google.generativeai as genai
except Exception:
    genai = None

# ────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Builtly | Byggelånskontroll", layout="wide", initial_sidebar_state="collapsed")


# ────────────────────────────────────────────────────────────────
# HELPERS
# ────────────────────────────────────────────────────────────────
def render_html(html: str) -> None:
    st.markdown(html.replace("\n", " "), unsafe_allow_html=True)


def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            suffix = Path(candidate).suffix.lower().replace(".", "")
            mime = f"image/{'jpeg' if suffix in ('jpg','jpeg') else suffix}"
            with open(candidate, "rb") as f:
                return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"
    return ""


def render_hero(eyebrow, title, subtitle, pills, badge):
    pills_html = "".join(f'<span class="hero-pill">{p}</span>' for p in pills)
    render_html(f"""
    <div class="hero-card">
        <div class="hero-eyebrow">{eyebrow}</div>
        <div class="hero-title">{title}</div>
        <div class="hero-subtitle">{subtitle}</div>
        <div class="hero-pills">{pills_html}</div>
        <div class="hero-badge">{badge}</div>
    </div>""")


def render_section(title, desc, badge):
    render_html(f"""
    <div class="section-header">
        <span class="section-badge">{badge}</span>
        <h3>{title}</h3>
        <p>{desc}</p>
    </div>""")


def render_panel(title, desc, bullets, tone="blue", badge=""):
    color_map = {"blue": ("#38bdf8", "rgba(56,194,201,0.06)", "rgba(56,194,201,0.18)"),
                 "gold": ("#f59e0b", "rgba(245,158,11,0.06)", "rgba(245,158,11,0.18)"),
                 "green": ("#22c55e", "rgba(34,197,94,0.06)", "rgba(34,197,94,0.18)"),
                 "red": ("#ef4444", "rgba(239,68,68,0.06)", "rgba(239,68,68,0.18)")}
    accent, bg, border = color_map.get(tone, color_map["blue"])
    badge_html = f'<span style="display:inline-block;background:{bg};border:1px solid {border};border-radius:6px;padding:1px 8px;font-size:0.7rem;font-weight:700;color:{accent};text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">{badge}</span>' if badge else ""
    bullets_html = "".join(f'<li style="color:#c8d3df;margin-bottom:6px;font-size:0.88rem;line-height:1.5;">{b}</li>' for b in bullets)
    render_html(f"""
    <div class="panel-box" style="background:{bg};border:1px solid {border};border-radius:14px;padding:1.3rem 1.5rem;margin-bottom:1rem;">
        {badge_html}
        <div style="font-weight:700;font-size:0.98rem;color:#f5f7fb;margin-bottom:4px;">{title}</div>
        <div style="font-size:0.85rem;color:#9fb0c3;margin-bottom:10px;line-height:1.5;">{desc}</div>
        <ul style="margin:0;padding-left:1.2rem;">{bullets_html}</ul>
    </div>""")


def render_metric_cards(metrics):
    cards = ""
    for val, label, desc in metrics:
        cards += f"""<div class="metric-card">
            <div class="mc-value">{val}</div>
            <div class="mc-label">{label}</div>
            <div class="mc-desc">{desc}</div>
        </div>"""
    render_html(f'<div class="metric-row">{cards}</div>')


def safe_get(obj, key, default=""):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


# ────────────────────────────────────────────────────────────────
# AI ENGINE
# ────────────────────────────────────────────────────────────────
def get_ai_client():
    """Returns (client_type, client) – 'openai'|'gemini'|None."""
    oai_key = os.environ.get("OPENAI_API_KEY", "")
    gem_key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if oai_key and OpenAI:
        return "openai", OpenAI(api_key=oai_key)
    if gem_key and genai:
        genai.configure(api_key=gem_key)
        return "gemini", genai.GenerativeModel("gemini-2.0-flash")
    return None, None


def extract_text_from_uploads(files) -> str:
    """Extracts text from uploaded PDFs/text files."""
    all_text = []
    for f in files:
        raw = f.read()
        f.seek(0)
        name = f.name.lower()
        if name.endswith(".pdf") and fitz:
            try:
                doc = fitz.open(stream=raw, filetype="pdf")
                for page in doc:
                    all_text.append(page.get_text())
                doc.close()
            except Exception:
                pass
        elif name.endswith((".csv", ".txt", ".md")):
            try:
                all_text.append(raw.decode("utf-8", errors="replace"))
            except Exception:
                pass
        elif name.endswith((".xlsx", ".xls")):
            try:
                df = pd.read_excel(io.BytesIO(raw))
                all_text.append(df.to_string())
            except Exception:
                pass
    return "\n\n".join(all_text)[:80000]


def run_draw_request_analysis(client_type, client, project_info: dict, doc_text: str) -> dict:
    """AI-analyse av trekkforespørsel mot budsjett og fremdrift."""
    system_prompt = textwrap.dedent("""
    Du er en erfaren byggelånskontrollør i Norge. Du skal vurdere en trekkforespørsel
    fra en utbygger mot byggelånet. Du får prosjektinfo og dokumentgrunnlag.

    Returner KUN gyldig JSON med denne strukturen:
    {
        "sammendrag": "Kort oppsummering av trekkforespørselen (2-3 setninger)",
        "anbefalt_utbetaling_mnok": 0.0,
        "anbefalt_tilbakehold_mnok": 0.0,
        "godkjenningsstatus": "Anbefalt godkjent | Anbefalt med forbehold | Ikke anbefalt",
        "budsjett_vs_paloept": {
            "totalbudsjett_mnok": 0.0,
            "paloept_foer_trekk_mnok": 0.0,
            "dette_trekk_mnok": 0.0,
            "gjenstaaende_etter_trekk_mnok": 0.0,
            "forbruksprosent": 0.0
        },
        "fremdriftsvurdering": {
            "planlagt_fremdrift_pst": 0,
            "estimert_faktisk_fremdrift_pst": 0,
            "avvik_kommentar": "..."
        },
        "risikoer": [
            {"risiko": "...", "alvorlighet": "Lav|Middels|Høy|Kritisk", "tiltak": "..."}
        ],
        "kontrollpunkter": [
            {"punkt": "...", "status": "OK|Avvik|Mangler|Ikke vurdert", "kommentar": "..."}
        ],
        "dokumentasjonskontroll": [
            {"dokument": "...", "mottatt": true/false, "kommentar": "..."}
        ],
        "vilkaar_for_utbetaling": ["..."],
        "anbefalinger": ["..."]
    }
    """)

    user_prompt = f"""
Prosjektinformasjon:
- Prosjekt: {project_info.get('navn', 'Ikke oppgitt')}
- Utbygger: {project_info.get('utbygger', 'Ikke oppgitt')}
- Totalbudsjett: {project_info.get('totalbudsjett_mnok', 0)} MNOK
- Byggelån innvilget: {project_info.get('byggelaan_mnok', 0)} MNOK
- Tidligere utbetalt: {project_info.get('tidligere_utbetalt_mnok', 0)} MNOK
- Forespurt trekk: {project_info.get('forespurt_trekk_mnok', 0)} MNOK
- Entrepriseform: {project_info.get('entrepriseform', 'Ikke oppgitt')}
- Planlagt ferdigstillelse: {project_info.get('ferdigstillelse', 'Ikke oppgitt')}
- Forhåndssalg/utleiegrad: {project_info.get('forhaandssalg_pst', 'Ikke oppgitt')}%
- Trekkforespørsel nr: {project_info.get('trekk_nr', 1)}

Dokumentgrunnlag (utdrag):
{doc_text[:40000]}

Vurder trekkforespørselen og returner JSON.
"""

    try:
        if client_type == "openai":
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                temperature=0.2, max_tokens=4000,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        elif client_type == "gemini":
            resp = client.generate_content(system_prompt + "\n\n" + user_prompt,
                                           generation_config={"temperature": 0.2, "max_output_tokens": 4000})
            text = resp.text.strip()
            text = re.sub(r"^```json\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            return json.loads(text)
    except Exception as e:
        st.error(f"AI-analyse feilet: {e}")
    return {}


# ────────────────────────────────────────────────────────────────
# PDF REPORT
# ────────────────────────────────────────────────────────────────
class LoanControlPDF(FPDF if FPDF else object):
    """PDF-rapport for byggelånskontroll."""

    def __init__(self):
        super().__init__("P", "mm", "A4")
        self.set_auto_page_break(auto=True, margin=25)
        self._add_fonts()
        self.accent = (56, 194, 201)
        self.dark = (6, 17, 26)
        self.muted = (159, 176, 195)

    def _add_fonts(self):
        self.add_font("Inter", "", os.path.join(os.path.dirname(__file__), "Inter-Regular.ttf"), uni=True) if os.path.exists(os.path.join(os.path.dirname(__file__), "Inter-Regular.ttf")) else None
        self.add_font("Inter", "B", os.path.join(os.path.dirname(__file__), "Inter-Bold.ttf"), uni=True) if os.path.exists(os.path.join(os.path.dirname(__file__), "Inter-Bold.ttf")) else None

    def _font(self, style="", size=10):
        try:
            self.set_font("Inter", style, size)
        except Exception:
            self.set_font("Helvetica", style, size)

    def header(self):
        self._font("B", 8)
        self.set_text_color(159, 176, 195)
        self.cell(0, 6, "Builtly | Byggelånskontroll", align="L")
        self.cell(0, 6, datetime.now().strftime("%d.%m.%Y"), align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*self.accent)
        self.set_line_width(0.3)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self._font("", 7)
        self.set_text_color(159, 176, 195)
        self.cell(0, 8, f"Side {self.page_no()}/{{nb}} — Utkast, krever faglig kontroll", align="C")

    def cover_page(self, project_name, utbygger, trekk_nr):
        self.add_page()
        self.ln(60)
        self._font("B", 28)
        self.set_text_color(6, 17, 26)
        self.cell(0, 14, "Byggelånskontroll", align="C", new_x="LMARGIN", new_y="NEXT")
        self._font("", 14)
        self.set_text_color(80, 100, 120)
        self.cell(0, 10, f"Trekkforespørsel #{trekk_nr}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(8)
        self._font("B", 16)
        self.set_text_color(6, 17, 26)
        self.cell(0, 10, project_name, align="C", new_x="LMARGIN", new_y="NEXT")
        self._font("", 11)
        self.set_text_color(80, 100, 120)
        self.cell(0, 8, f"Utbygger: {utbygger}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 8, f"Dato: {datetime.now().strftime('%d.%m.%Y')}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(20)
        self.set_draw_color(*self.accent)
        self.set_line_width(0.8)
        self.line(60, self.get_y(), 150, self.get_y())

    def section_title(self, num, title):
        self.ln(6)
        self._font("B", 13)
        self.set_text_color(*self.accent)
        self.cell(0, 8, f"{num}. {title}", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*self.accent)
        self.set_line_width(0.2)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def body_text(self, text):
        self._font("", 10)
        self.set_text_color(40, 50, 60)
        self.multi_cell(0, 5.5, text)
        self.ln(2)

    def key_value(self, key, value):
        self._font("B", 9)
        self.set_text_color(80, 100, 120)
        self.cell(65, 5.5, key)
        self._font("", 10)
        self.set_text_color(6, 17, 26)
        self.cell(0, 5.5, str(value), new_x="LMARGIN", new_y="NEXT")

    def status_box(self, status, text):
        color_map = {"Anbefalt godkjent": (34, 197, 94), "Anbefalt med forbehold": (245, 158, 11), "Ikke anbefalt": (239, 68, 68)}
        color = color_map.get(status, (56, 194, 201))
        self.ln(3)
        self.set_fill_color(*color)
        self.set_draw_color(*color)
        self.rect(10, self.get_y(), 190, 18, style="D")
        self.set_fill_color(color[0], color[1], color[2])
        self.rect(10, self.get_y(), 4, 18, style="F")
        self._font("B", 12)
        self.set_text_color(*color)
        self.set_xy(18, self.get_y() + 2)
        self.cell(0, 7, status)
        self._font("", 9)
        self.set_text_color(40, 50, 60)
        self.set_xy(18, self.get_y() + 7)
        self.cell(0, 5, text)
        self.ln(22)

    def risk_table(self, risks):
        self._font("B", 8)
        self.set_fill_color(240, 244, 248)
        self.set_text_color(80, 100, 120)
        self.cell(70, 7, "Risiko", border=1, fill=True)
        self.cell(25, 7, "Alvorlighet", border=1, fill=True)
        self.cell(0, 7, "Tiltak", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")
        self._font("", 8)
        self.set_text_color(40, 50, 60)
        for r in risks:
            y_start = self.get_y()
            self.multi_cell(70, 5, safe_get(r, "risiko", "-"), border="LR")
            y1 = self.get_y()
            self.set_xy(80, y_start)
            sev = safe_get(r, "alvorlighet", "-")
            sev_color = {"Kritisk": (239, 68, 68), "Høy": (245, 158, 11), "Middels": (56, 194, 201), "Lav": (34, 197, 94)}.get(sev, (80, 100, 120))
            self.set_text_color(*sev_color)
            self.multi_cell(25, 5, sev, border="LR")
            y2 = self.get_y()
            self.set_text_color(40, 50, 60)
            self.set_xy(105, y_start)
            self.multi_cell(0, 5, safe_get(r, "tiltak", "-"), border="LR")
            y3 = self.get_y()
            max_y = max(y1, y2, y3)
            self.set_y(max_y)


def generate_loan_control_pdf(project_info, analysis) -> bytes:
    if not FPDF:
        return b""
    pdf = LoanControlPDF()
    pdf.alias_nb_pages()
    pdf.cover_page(
        project_info.get("navn", "Prosjekt"),
        project_info.get("utbygger", "-"),
        project_info.get("trekk_nr", 1),
    )
    pdf.add_page()

    # 1. Sammendrag og status
    pdf.section_title(1, "Sammendrag og anbefaling")
    status = safe_get(analysis, "godkjenningsstatus", "Ikke vurdert")
    pdf.status_box(status, safe_get(analysis, "sammendrag", ""))

    # 2. Budsjett vs påløpt
    pdf.section_title(2, "Budsjett vs. påløpt")
    bvp = safe_get(analysis, "budsjett_vs_paloept", {})
    if isinstance(bvp, dict):
        pdf.key_value("Totalbudsjett:", f"{safe_get(bvp, 'totalbudsjett_mnok', 0)} MNOK")
        pdf.key_value("Påløpt før dette trekk:", f"{safe_get(bvp, 'paloept_foer_trekk_mnok', 0)} MNOK")
        pdf.key_value("Forespurt trekk:", f"{safe_get(bvp, 'dette_trekk_mnok', 0)} MNOK")
        pdf.key_value("Gjenstående etter trekk:", f"{safe_get(bvp, 'gjenstaaende_etter_trekk_mnok', 0)} MNOK")
        pdf.key_value("Forbruksprosent:", f"{safe_get(bvp, 'forbruksprosent', 0)}%")

    # 3. Fremdrift
    pdf.section_title(3, "Fremdriftsvurdering")
    fv = safe_get(analysis, "fremdriftsvurdering", {})
    if isinstance(fv, dict):
        pdf.key_value("Planlagt fremdrift:", f"{safe_get(fv, 'planlagt_fremdrift_pst', 0)}%")
        pdf.key_value("Estimert faktisk:", f"{safe_get(fv, 'estimert_faktisk_fremdrift_pst', 0)}%")
        pdf.body_text(safe_get(fv, "avvik_kommentar", ""))

    # 4. Kontrollpunkter
    pdf.section_title(4, "Kontrollpunkter")
    for kp in safe_get(analysis, "kontrollpunkter", []):
        if isinstance(kp, dict):
            s = safe_get(kp, "status", "?")
            pdf.key_value(f"[{s}] {safe_get(kp, 'punkt', '')}", safe_get(kp, "kommentar", ""))

    # 5. Risikoer
    pdf.section_title(5, "Risikovurdering")
    risks = safe_get(analysis, "risikoer", [])
    if risks:
        pdf.risk_table(risks)

    # 6. Dokumentasjonskontroll
    pdf.section_title(6, "Dokumentasjonskontroll")
    for d in safe_get(analysis, "dokumentasjonskontroll", []):
        if isinstance(d, dict):
            icon = "✓" if safe_get(d, "mottatt", False) else "✗"
            pdf.key_value(f"{icon} {safe_get(d, 'dokument', '')}", safe_get(d, "kommentar", ""))

    # 7. Vilkår og anbefalinger
    pdf.section_title(7, "Vilkår for utbetaling")
    for v in safe_get(analysis, "vilkaar_for_utbetaling", []):
        pdf.body_text(f"• {v}")

    pdf.section_title(8, "Anbefalinger")
    for a in safe_get(analysis, "anbefalinger", []):
        pdf.body_text(f"• {a}")

    # Disclaimer
    pdf.ln(10)
    pdf.set_fill_color(255, 248, 230)
    pdf.set_draw_color(245, 158, 11)
    pdf.rect(10, pdf.get_y(), 190, 14, style="DF")
    pdf._font("B", 8)
    pdf.set_text_color(180, 120, 0)
    pdf.set_xy(14, pdf.get_y() + 2)
    pdf.cell(0, 5, "Utkast — krever faglig kontroll")
    pdf._font("", 7)
    pdf.set_xy(14, pdf.get_y() + 5)
    pdf.cell(0, 5, "Analysen er automatisk generert og skal gjennomgås av kvalifisert byggelånskontrollør før bruk.")

    return pdf.output()


# ────────────────────────────────────────────────────────────────
# PREMIUM CSS
# ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    #MainMenu, footer, header {visibility: hidden;}
    header[data-testid="stHeader"] {visibility: hidden; height: 0;}
    :root {
        --bg: #06111a; --panel: rgba(10,22,35,0.78); --stroke: rgba(120,145,170,0.18);
        --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38bdf8; --accent-warm: #f59e0b; --radius-lg: 16px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; }

    /* Hero */
    .hero-card { background: linear-gradient(135deg, rgba(10,22,35,0.95), rgba(16,32,50,0.95));
        border: 1px solid rgba(120,145,170,0.15); border-radius: 20px; padding: 2.5rem 2.8rem 2rem; margin-bottom: 2rem; }
    .hero-eyebrow { font-size: 0.78rem; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: #38bdf8; margin-bottom: 0.4rem; }
    .hero-title { font-size: 1.85rem; font-weight: 800; line-height: 1.2; color: #f5f7fb; margin-bottom: 0.75rem; }
    .hero-subtitle { font-size: 0.98rem; color: #9fb0c3; line-height: 1.6; max-width: 750px; }
    .hero-pills { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 1.1rem; }
    .hero-pill { background: rgba(56,194,201,0.08); border: 1px solid rgba(56,194,201,0.25); border-radius: 20px; padding: 4px 14px; font-size: 0.8rem; font-weight: 600; color: #38bdf8; }
    .hero-badge { display: inline-block; background: rgba(245,158,11,0.12); border: 1px solid rgba(245,158,11,0.35); border-radius: 6px; padding: 2px 10px; font-size: 0.72rem; font-weight: 700; color: #f59e0b; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 1rem; }

    /* Metrics */
    .metric-row { display: flex; gap: 16px; margin-bottom: 1.5rem; flex-wrap: wrap; }
    .metric-card { flex: 1; min-width: 200px; background: rgba(10,22,35,0.6); border: 1px solid rgba(120,145,170,0.18); border-radius: 14px; padding: 1.1rem 1.3rem; }
    .metric-card .mc-value { font-size: 1.6rem; font-weight: 800; color: #38bdf8; margin-bottom: 2px; }
    .metric-card .mc-label { font-size: 0.82rem; font-weight: 700; color: #c8d3df; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
    .metric-card .mc-desc { font-size: 0.78rem; color: #9fb0c3; line-height: 1.4; }

    /* Sections */
    .section-header { margin-top: 2.5rem; margin-bottom: 1rem; }
    .section-header h3 { color: #f5f7fb !important; font-weight: 750 !important; font-size: 1.2rem !important; margin-bottom: 4px !important; }
    .section-header p { color: #9fb0c3 !important; font-size: 0.9rem !important; }
    .section-badge { display: inline-block; background: rgba(56,194,201,0.1); border: 1px solid rgba(56,194,201,0.25); border-radius: 6px; padding: 1px 8px; font-size: 0.7rem; font-weight: 700; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }

    /* Inputs */
    .stTextInput input, .stTextArea textarea, .stNumberInput input, .stSelectbox > div > div,
    .stMultiSelect > div > div { background-color: rgba(10,22,35,0.6) !important; color: #f5f7fb !important;
        border: 1px solid rgba(120,145,170,0.2) !important; border-radius: 12px !important; }
    .stSelectbox label, .stMultiSelect label, .stTextInput label, .stTextArea label,
    .stNumberInput label, .stFileUploader label, .stToggle label, .stRadio label,
    .stDateInput label { color: #c8d3df !important; font-weight: 600 !important; }
    div[data-baseweb="select"] > div { background-color: rgba(10,22,35,0.6) !important; border-color: rgba(120,145,170,0.2) !important; }

    /* Tabs */
    .stTabs [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid rgba(120,145,170,0.15); }
    .stTabs [data-baseweb="tab"] { background: transparent !important; color: #9fb0c3 !important; border-radius: 10px 10px 0 0 !important; padding: 8px 18px !important; font-weight: 600 !important; }
    .stTabs [aria-selected="true"] { background: rgba(56,194,201,0.08) !important; color: #38bdf8 !important; border-bottom: 2px solid #38bdf8 !important; }

    /* Buttons */
    button[kind="primary"], .stFormSubmitButton > button {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important; border: none !important; font-weight: 750 !important;
        border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; }
    .stDownloadButton > button { background-color: rgba(255,255,255,0.04) !important; color: #c8d3df !important;
        border: 1px solid rgba(120,145,170,0.25) !important; border-radius: 10px !important; font-weight: 600 !important; }

    /* DataFrame */
    .stDataFrame { border-radius: 12px; overflow: hidden; }
    .stDataFrame [data-testid="stDataFrameResizable"] { border: 1px solid rgba(120,145,170,0.15) !important; border-radius: 12px !important; }

    /* Disclaimer */
    .disclaimer-banner { background: rgba(245,158,11,0.06); border: 1px solid rgba(245,158,11,0.25); border-radius: 14px; padding: 1.1rem 1.4rem; margin-top: 2rem; }
    .disclaimer-banner .db-title { font-weight: 700; font-size: 0.9rem; color: #f59e0b; margin-bottom: 4px; }
    .disclaimer-banner .db-text { font-size: 0.82rem; color: #9fb0c3; line-height: 1.5; }

    /* Markdown */
    .stMarkdown, .stMarkdown p, .stMarkdown li, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 { color: #f5f7fb !important; }

    /* Status colors */
    .status-green { color: #22c55e; font-weight: 700; }
    .status-yellow { color: #f59e0b; font-weight: 700; }
    .status-red { color: #ef4444; font-weight: 700; }
</style>
""", unsafe_allow_html=True)


# ────────────────────────────────────────────────────────────────
# BACK BUTTON + LOGO
# ────────────────────────────────────────────────────────────────
top_l, top_r = st.columns([6, 1])
with top_l:
    logo = logo_data_uri()
    if logo:
        render_html(f'<img src="{logo}" class="brand-logo" alt="Builtly">')
with top_r:
    if st.button("← Tilbake", type="secondary", key="back_btn"):
        try:
            st.switch_page("pages/Project.py")
        except Exception:
            st.info("Naviger tilbake til prosjektoversikten manuelt.")


# ────────────────────────────────────────────────────────────────
# HERO
# ────────────────────────────────────────────────────────────────
render_hero(
    eyebrow="Byggelånskontroll",
    title="Verifiser trekkforespørsler mot budsjett, fremdrift og dokumentasjon.",
    subtitle=(
        "Last opp entreprisekontrakt, budsjett, faktureringsplan, fremdriftsrapport og byggeplassdokumentasjon. "
        "Du får en strukturert trekkanbefaling med avviksrapport, risikoflagg og kontrollpunkter — klar for bankens byggelånskontrollør."
    ),
    pills=["Budsjett vs. påløpt", "Fremdriftskontroll", "Fakturakontroll", "Byggeplassbevis", "Trekkanbefaling"],
    badge="Byggelånskontroll",
)


# ────────────────────────────────────────────────────────────────
# MAIN LAYOUT
# ────────────────────────────────────────────────────────────────
left, right = st.columns([3, 2], gap="large")

with left:
    render_section("1. Prosjekt og lånedetaljer", "Registrer nøkkeldata for byggelånet og trekkforespørselen.", "Input")

    c1, c2 = st.columns(2)
    with c1:
        prosjekt_navn = st.text_input("Prosjektnavn", value="", placeholder="F.eks. Fjordparken Bolig Trinn 2")
        utbygger = st.text_input("Utbygger / låntaker", value="", placeholder="Selskap AS")
        totalbudsjett = st.number_input("Totalbudsjett (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f")
        byggelaan = st.number_input("Byggelån innvilget (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f")
    with c2:
        tidligere_utbetalt = st.number_input("Tidligere utbetalt (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f")
        forespurt_trekk = st.number_input("Forespurt trekk dette (MNOK)", min_value=0.0, value=0.0, step=0.5, format="%.1f")
        entrepriseform = st.selectbox("Entrepriseform", ["Totalentreprise", "Hovedentreprise", "Delte entrepriser", "Byggherrestyrt", "Annet"])
        trekk_nr = st.number_input("Trekkforespørsel nr.", min_value=1, value=1, step=1)

    c3, c4 = st.columns(2)
    with c3:
        ferdigstillelse = st.date_input("Planlagt ferdigstillelse", value=date(2027, 6, 30))
        forhaandssalg = st.number_input("Forhåndssalg/utleiegrad (%)", min_value=0, max_value=100, value=0, step=5)
    with c4:
        egenkapital_pst = st.number_input("Egenkapitalandel (%)", min_value=0, max_value=100, value=25, step=1)
        garanti_type = st.selectbox("Garantistillelse", ["Bankgaranti §12", "Selvskyldnergaranti", "Eiendomspant", "Kombinert", "Annet"])

    render_section("2. Last opp dokumentasjon", "Last opp alt tilgjengelig grunnlag for trekkforespørselen.", "Dokumenter")

    uploads = st.file_uploader(
        "Entreprisekontrakt, budsjett, faktureringsplan, fremdriftsrapport, fakturaer, byggeplassbilder, kontrollrapporter",
        type=["pdf", "xlsx", "xls", "csv", "docx", "jpg", "jpeg", "png", "zip"],
        accept_multiple_files=True,
        key="loan_uploads",
    )

    fokus = st.text_area(
        "Spesielle forhold å fokusere på",
        value="",
        placeholder="F.eks. entrepenør har varslet tillegg, det er forsinkelser på fundamentering, etc.",
        height=90,
    )

    run_analysis = st.button("Analyser trekkforespørsel", type="primary", use_container_width=True)

with right:
    render_section("Om byggelånskontroll", "Modulen verifiserer at trekkforespørselen er i tråd med budsjett, fremdrift og dokumentasjon.", "Info")

    render_panel(
        "Hva modulen kontrollerer",
        "Hver trekkforespørsel vurderes mot flere kontrollpunkter.",
        [
            "Budsjett vs. påløpt — er trekket innenfor rammen?",
            "Fremdrift vs. fakturering — samsvarer fysisk fremdrift med fakturert beløp?",
            "Fakturakontroll — er fakturaene korrekte og dokumenterte?",
            "Byggeplassbevis — tyder bildedokumentasjon på at arbeidet er utført?",
            "Garantier og sikkerheter — er nødvendige garantier på plass?",
        ],
        tone="blue",
        badge="Kontrollpunkter",
    )

    render_panel(
        "Slik bruker du modulen",
        "Fyll inn prosjektdetaljer og last opp tilgjengelig dokumentasjon.",
        [
            "Registrer prosjekt- og lånedetaljer i skjemaet til venstre",
            "Last opp entreprisekontrakt, budsjett og faktureringsplan",
            "Legg ved fremdriftsrapport og fakturaer for denne perioden",
            "Bilder fra byggeplass styrker vurderingsgrunnlaget",
            "Trykk «Analyser» for å få strukturert trekkanbefaling",
        ],
        tone="gold",
        badge="Kom i gang",
    )

    render_panel(
        "Rapport og eksport",
        "Alle resultater kan lastes ned som PDF-rapport.",
        [
            "Trekkanbefaling med godkjenningsstatus",
            "Budsjett vs. påløpt-oversikt",
            "Risikovurdering med alvorlighetsgrad",
            "Dokumentasjonskontroll med mangler",
            "Vilkår for utbetaling",
        ],
        tone="green",
        badge="Output",
    )


# ────────────────────────────────────────────────────────────────
# ANALYSIS
# ────────────────────────────────────────────────────────────────
if run_analysis:
    project_info = {
        "navn": prosjekt_navn or "Ikke oppgitt",
        "utbygger": utbygger or "Ikke oppgitt",
        "totalbudsjett_mnok": totalbudsjett,
        "byggelaan_mnok": byggelaan,
        "tidligere_utbetalt_mnok": tidligere_utbetalt,
        "forespurt_trekk_mnok": forespurt_trekk,
        "entrepriseform": entrepriseform,
        "ferdigstillelse": str(ferdigstillelse),
        "forhaandssalg_pst": forhaandssalg,
        "egenkapital_pst": egenkapital_pst,
        "garanti_type": garanti_type,
        "trekk_nr": trekk_nr,
        "fokus": fokus,
    }

    client_type, client = get_ai_client()
    if not client:
        st.error("Ingen AI-nøkkel konfigurert. Sett OPENAI_API_KEY eller GOOGLE_API_KEY i miljøvariablene.")
        st.stop()

    doc_text = ""
    if uploads:
        with st.spinner("Leser dokumenter..."):
            doc_text = extract_text_from_uploads(uploads)

    with st.spinner("Analyserer trekkforespørsel..."):
        analysis = run_draw_request_analysis(client_type, client, project_info, doc_text)

    if not analysis:
        st.error("Analysen returnerte ingen resultater. Sjekk dokumentgrunnlaget og prøv igjen.")
        st.stop()

    st.session_state["loan_analysis"] = analysis
    st.session_state["loan_project_info"] = project_info

# ── Display results ──
if "loan_analysis" in st.session_state:
    analysis = st.session_state["loan_analysis"]
    project_info = st.session_state.get("loan_project_info", {})

    render_section("Resultat", "Trekkanbefaling basert på innsendt dokumentasjon og prosjektdata.", "Analyse")

    # Status banner
    status = safe_get(analysis, "godkjenningsstatus", "Ikke vurdert")
    status_class = {"Anbefalt godkjent": "status-green", "Anbefalt med forbehold": "status-yellow", "Ikke anbefalt": "status-red"}.get(status, "")
    render_html(f"""
    <div style="background:rgba(10,22,35,0.7);border:1px solid rgba(120,145,170,0.2);border-radius:16px;padding:1.5rem 2rem;margin-bottom:1.5rem;">
        <div style="font-size:0.78rem;color:#9fb0c3;text-transform:uppercase;font-weight:700;letter-spacing:0.08em;margin-bottom:4px;">Anbefaling</div>
        <div class="{status_class}" style="font-size:1.5rem;margin-bottom:6px;">{status}</div>
        <div style="color:#c8d3df;font-size:0.92rem;line-height:1.6;">{safe_get(analysis, 'sammendrag', '')}</div>
    </div>""")

    # Budget metrics
    bvp = safe_get(analysis, "budsjett_vs_paloept", {})
    if isinstance(bvp, dict):
        render_metric_cards([
            (f"{safe_get(bvp, 'totalbudsjett_mnok', 0)} MNOK", "Totalbudsjett", "Samlet prosjektbudsjett"),
            (f"{safe_get(bvp, 'paloept_foer_trekk_mnok', 0)} MNOK", "Påløpt før trekk", "Akkumulert forbruk"),
            (f"{safe_get(bvp, 'dette_trekk_mnok', 0)} MNOK", "Dette trekket", "Forespurt beløp"),
            (f"{safe_get(bvp, 'forbruksprosent', 0)}%", "Forbruksprosent", "Andel av totalbudsjett brukt"),
        ])

    # Tabs
    tabs = st.tabs(["Kontrollpunkter", "Risikoer", "Dokumentkontroll", "Fremdrift", "Vilkår", "Eksport"])

    with tabs[0]:
        kp_list = safe_get(analysis, "kontrollpunkter", [])
        if kp_list:
            rows = []
            for kp in kp_list:
                if isinstance(kp, dict):
                    rows.append({"Kontrollpunkt": safe_get(kp, "punkt"), "Status": safe_get(kp, "status"), "Kommentar": safe_get(kp, "kommentar")})
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tabs[1]:
        risks = safe_get(analysis, "risikoer", [])
        if risks:
            rows = []
            for r in risks:
                if isinstance(r, dict):
                    rows.append({"Risiko": safe_get(r, "risiko"), "Alvorlighet": safe_get(r, "alvorlighet"), "Tiltak": safe_get(r, "tiltak")})
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tabs[2]:
        docs = safe_get(analysis, "dokumentasjonskontroll", [])
        if docs:
            rows = []
            for d in docs:
                if isinstance(d, dict):
                    rows.append({"Dokument": safe_get(d, "dokument"), "Mottatt": "✓" if safe_get(d, "mottatt", False) else "✗", "Kommentar": safe_get(d, "kommentar")})
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tabs[3]:
        fv = safe_get(analysis, "fremdriftsvurdering", {})
        if isinstance(fv, dict):
            fc1, fc2 = st.columns(2)
            with fc1:
                render_metric_cards([
                    (f"{safe_get(fv, 'planlagt_fremdrift_pst', 0)}%", "Planlagt fremdrift", "Iht. fremdriftsplan"),
                ])
            with fc2:
                render_metric_cards([
                    (f"{safe_get(fv, 'estimert_faktisk_fremdrift_pst', 0)}%", "Estimert faktisk", "Basert på dokumentasjon"),
                ])
            st.markdown(safe_get(fv, "avvik_kommentar", ""))

    with tabs[4]:
        vilkaar = safe_get(analysis, "vilkaar_for_utbetaling", [])
        if vilkaar:
            for i, v in enumerate(vilkaar, 1):
                st.markdown(f"**{i}.** {v}")
        st.markdown("---")
        st.markdown("**Anbefalinger:**")
        for a in safe_get(analysis, "anbefalinger", []):
            st.markdown(f"• {a}")

    with tabs[5]:
        # PDF download
        pdf_bytes = generate_loan_control_pdf(project_info, analysis)
        if pdf_bytes:
            st.download_button(
                "Last ned PDF-rapport",
                data=pdf_bytes,
                file_name=f"byggelanskontroll_{project_info.get('navn', 'prosjekt').replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        # JSON
        st.download_button(
            "Last ned analyse (JSON)",
            data=json.dumps({"prosjekt": project_info, "analyse": analysis}, ensure_ascii=False, indent=2),
            file_name=f"byggelanskontroll_{datetime.now().strftime('%Y%m%d')}.json",
            mime="application/json",
            use_container_width=True,
        )


# ────────────────────────────────────────────────────────────────
# DISCLAIMER
# ────────────────────────────────────────────────────────────────
render_html("""
<div class="disclaimer-banner" style="margin-top: 2rem;">
    <div class="db-title">Utkast — krever faglig kontroll</div>
    <div class="db-text">
        Analysen er automatisk generert basert på innsendt dokumentasjon og oppgitte prosjektdata.
        Resultatet skal gjennomgås og verifiseres av kvalifisert byggelånskontrollør før det benyttes
        som grunnlag for utbetalingsbeslutning.
    </div>
</div>
""")
