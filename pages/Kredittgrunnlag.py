# -*- coding: utf-8 -*-
"""
Builtly | Kredittgrunnlag
Beslutningsstøtte for kredittkomité — tomtelån, byggelån og utleielån.
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
    import fitz
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
st.set_page_config(page_title="Builtly | Kredittgrunnlag", layout="wide", initial_sidebar_state="collapsed")


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
    oai_key = os.environ.get("OPENAI_API_KEY", "")
    gem_key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if oai_key and OpenAI:
        return "openai", OpenAI(api_key=oai_key)
    if gem_key and genai:
        genai.configure(api_key=gem_key)
        return "gemini", genai.GenerativeModel("gemini-2.0-flash")
    return None, None


def extract_text_from_uploads(files) -> str:
    all_text = []
    for f in files:
        raw = f.read(); f.seek(0)
        name = f.name.lower()
        if name.endswith(".pdf") and fitz:
            try:
                doc = fitz.open(stream=raw, filetype="pdf")
                for page in doc: all_text.append(page.get_text())
                doc.close()
            except Exception: pass
        elif name.endswith((".csv", ".txt", ".md")):
            try: all_text.append(raw.decode("utf-8", errors="replace"))
            except Exception: pass
        elif name.endswith((".xlsx", ".xls")):
            try:
                df = pd.read_excel(io.BytesIO(raw))
                all_text.append(df.to_string())
            except Exception: pass
    return "\n\n".join(all_text)[:80000]


def run_credit_analysis(client_type, client, project_info: dict, doc_text: str) -> dict:
    """AI-analyse for kredittgrunnlag."""
    system_prompt = textwrap.dedent("""
    Du er en erfaren kredittanalytiker i en norsk bank som vurderer eiendomsprosjekter.
    Du skal lage et strukturert kredittnotat basert på prosjektinfo og dokumentgrunnlag.

    VIKTIG OM VERDIVURDERING:
    Du skal ALLTID gjøre en selvstendig verdivurdering basert på riktig metode for prosjekttypen.
    En takst alene er IKKE tilstrekkelig — du må vurdere om taksten er rimelig gitt underliggende økonomi.

    For BOLIG (salg):
    - Bruk residualverdimetoden: Tomteverdi = Forventet salgsverdi - Total utbyggingskost - Utviklermargin (min. 12%)
    - En tomt er aldri verdt mer enn det som gir utbygger minst 12% margin på prosjektet
    - Vurder: Antall enheter × pris per kvm vs. total prosjektkost
    - Flagg dersom oppgitt tomteverdi/takst overstiger residualverdi
    - LTV skal beregnes mot residualverdi, IKKE bare oppgitt takst

    For NÆRING (utleie — kontor, handel, logistikk, hotell):
    - Bruk yield-basert verdi: Verdi = Netto leieinntekt / Markedsyield
    - Beregn yield on cost: Netto leieinntekt / Total prosjektkost (inkl. tomt)
    - Yield on cost skal normalt være høyere enn antatt markedsyield (ellers skapes ingen verdi)
    - Vurder WAULT (vektet gjennomsnittlig gjenstående leietid)
    - Flagg dersom yield on cost < markedsyield (prosjektet skaper ikke verdi)

    For KOMBINERT (mixed-use):
    - Del opp i bolig- og næringsdel, verdivurder hver for seg
    - Summer delene og sammenlign med totalinvestering

    Returner KUN gyldig JSON med denne strukturen:
    {
        "sammendrag": "Kort oppsummering for kredittkomité (3-4 setninger)",
        "anbefaling": "Anbefalt innvilget | Anbefalt med vilkår | Ikke anbefalt",
        "laanetype": "Tomtelån | Byggelån | Langsiktig lån | Kombinert",
        "noekkeltall": {
            "totalinvestering_mnok": 0.0,
            "soekt_laan_mnok": 0.0,
            "egenkapital_mnok": 0.0,
            "egenkapitalprosent": 0.0,
            "belaaningsgrad_ltv": 0.0,
            "estimert_markedsverdi_mnok": 0.0,
            "netto_yield_pst": 0.0,
            "dscr": 0.0,
            "icr": 0.0,
            "forhaandssalg_utleie_pst": 0
        },
        "verdivurdering": {
            "metode": "Residualverdi|Yield-basert|Kombinert",
            "oppgitt_takst_mnok": 0.0,
            "beregnet_verdi_mnok": 0.0,
            "avvik_takst_vs_beregnet_pst": 0.0,
            "takst_er_rimelig": true,
            "kommentar_takst": "Vurdering av om oppgitt takst er realistisk gitt prosjektøkonomien",
            "bolig_residual": {
                "forventet_salgsverdi_mnok": 0.0,
                "total_utbyggingskost_eks_tomt_mnok": 0.0,
                "minimummargin_12pst_mnok": 0.0,
                "residual_tomteverdi_mnok": 0.0,
                "oppgitt_tomtekost_mnok": 0.0,
                "tomtekost_innenfor_residual": true,
                "faktisk_margin_pst": 0.0,
                "salgsverdi_per_kvm_bra": 0,
                "byggekost_per_kvm_bta": 0,
                "kommentar": "..."
            },
            "naering_yield": {
                "brutto_leieinntekt_mnok": 0.0,
                "eierkostnader_mnok": 0.0,
                "netto_leieinntekt_mnok": 0.0,
                "yield_on_cost_pst": 0.0,
                "antatt_markedsyield_pst": 0.0,
                "yield_spread_pst": 0.0,
                "verdi_ved_markedsyield_mnok": 0.0,
                "wault_aar": 0.0,
                "vakansrisiko_pst": 0,
                "verdiskaping_positiv": true,
                "kommentar": "..."
            },
            "ltv_mot_beregnet_verdi_pst": 0.0,
            "bankens_verdianslag_mnok": 0.0,
            "forsiktig_verdi_70pst_mnok": 0.0
        },
        "regulering_og_tomt": {
            "reguleringsplan": "...",
            "utnyttelsesgrad_bya_pst": 0,
            "tillatt_vs_planlagt_bta": "...",
            "rammegodkjenning_status": "Godkjent | Søkt | Ikke søkt",
            "kommentar": "..."
        },
        "oekonomisk_analyse": {
            "totalkostnadskalkyle_mnok": 0.0,
            "entreprisekostnad_mnok": 0.0,
            "tomtekostnad_mnok": 0.0,
            "offentlige_avgifter_mnok": 0.0,
            "prosjektkostnader_mnok": 0.0,
            "finanskostnader_mnok": 0.0,
            "forventet_salgsverdi_mnok": 0.0,
            "forventet_resultat_mnok": 0.0,
            "resultatmargin_pst": 0.0
        },
        "rentesensitivitet": [
            {"rentenivaa": "+0%", "aarsresultat_mnok": 0.0, "dscr": 0.0, "betjeningsevne": "God|Akseptabel|Svak"},
            {"rentenivaa": "+1%", "aarsresultat_mnok": 0.0, "dscr": 0.0, "betjeningsevne": "God|Akseptabel|Svak"},
            {"rentenivaa": "+2%", "aarsresultat_mnok": 0.0, "dscr": 0.0, "betjeningsevne": "God|Akseptabel|Svak"},
            {"rentenivaa": "+3%", "aarsresultat_mnok": 0.0, "dscr": 0.0, "betjeningsevne": "God|Akseptabel|Svak"}
        ],
        "sikkerheter": [
            {"type": "...", "verdi_mnok": 0.0, "prioritet": "1. prioritet|2. prioritet", "kommentar": "..."}
        ],
        "risikovurdering": [
            {"risiko": "...", "sannsynlighet": "Lav|Middels|Høy", "konsekvens": "Lav|Middels|Høy", "mitigering": "..."}
        ],
        "styrker": ["..."],
        "svakheter": ["..."],
        "vilkaar": ["..."],
        "covenants": [
            {"covenant": "...", "grenseverdi": "...", "maalefrekvens": "Kvartalsvis|Halvårlig|Årlig"}
        ]
    }

    VIKTIG: Fyll kun ut den relevante delen av verdivurdering (bolig_residual ELLER naering_yield)
    basert på prosjekttype. For mixed-use, fyll ut begge. Sett irrelevante felter til 0 eller null.
    """)

    user_prompt = f"""
Prosjektinformasjon:
- Prosjekt: {project_info.get('navn', '')}
- Låntaker: {project_info.get('laantaker', '')}
- Organisasjonsnr: {project_info.get('orgnr', '')}
- Lånetype: {project_info.get('laanetype', '')}
- Søkt lån: {project_info.get('soekt_laan_mnok', 0)} MNOK
- Totalinvestering: {project_info.get('totalinvestering_mnok', 0)} MNOK
- Egenkapital: {project_info.get('egenkapital_mnok', 0)} MNOK
- Prosjekttype: {project_info.get('prosjekttype', '')}
- Antall enheter: {project_info.get('antall_enheter', '')}
- BTA: {project_info.get('bta_kvm', '')} kvm
- Tomt: {project_info.get('tomt_kvm', '')} kvm
- Reguleringsplan: {project_info.get('reguleringsplan', '')}
- Rammegodkjenning: {project_info.get('rammegodkjenning', '')}
- Entrepriseform: {project_info.get('entrepriseform', '')}
- Planlagt byggestart: {project_info.get('byggestart', '')}
- Planlagt ferdigstillelse: {project_info.get('ferdigstillelse', '')}
- Forhåndssalg/utleiegrad: {project_info.get('forhaandssalg_pst', 0)}%
- Forventet leie/salgsinntekt: {project_info.get('inntekt_mnok', 0)} MNOK
- Eksisterende gjeld: {project_info.get('eksisterende_gjeld_mnok', 0)} MNOK
- Pantesikkerhet: {project_info.get('pantesikkerhet', '')}
- Spesielle forhold: {project_info.get('spesielle_forhold', '')}

Verdivurdering og dokumentasjon:
- Har takst: {project_info.get('har_takst', False)}
- Takstverdi: {project_info.get('takst_mnok', 0)} MNOK
- Takstkilde: {project_info.get('takst_kilde', 'Ikke oppgitt')}
- Betalt/avtalt tomtepris: {project_info.get('tomtekost_mnok', 0)} MNOK
- Entreprisekost: {project_info.get('entreprisekost_mnok', 0)} MNOK

Bolig (residualverdi):
- Forventet salgspris: {project_info.get('forventet_salgspris_kvm', 0)} kr/kvm BRA
- Salgbart areal BRA: {project_info.get('bra_kvm', 0)} kvm
- Byggekost: {project_info.get('byggekost_kvm', 0)} kr/kvm BTA
- Minimum utviklermargin: {project_info.get('target_margin', 12)}%

Næring (yield-metode):
- Brutto leieinntekt: {project_info.get('brutto_leie_mnok', 0)} MNOK/år
- Eierkostnader: {project_info.get('eierkost_mnok', 0)} MNOK/år
- Antatt markedsyield: {project_info.get('antatt_markedsyield', 0)}%
- WAULT: {project_info.get('wault', 0)} år
- Strukturell vakanse: {project_info.get('vakanse_pst', 0)}%
- Antatt exit-yield: {project_info.get('exit_yield', 0)}%

Dokumentgrunnlag (utdrag):
{doc_text[:40000]}

Lag et komplett kredittnotat med fokus på korrekt verdivurdering basert på prosjekttype. Returner JSON.
"""

    try:
        if client_type == "openai":
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                temperature=0.2, max_tokens=5000,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        elif client_type == "gemini":
            resp = client.generate_content(system_prompt + "\n\n" + user_prompt,
                                           generation_config={"temperature": 0.2, "max_output_tokens": 5000})
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
class CreditPDF(FPDF if FPDF else object):
    def __init__(self):
        super().__init__("P", "mm", "A4")
        self.set_auto_page_break(auto=True, margin=25)
        self._add_fonts()
        self.accent = (56, 194, 201)
        self.dark = (6, 17, 26)

    def _add_fonts(self):
        for style, name in [("", "Inter-Regular.ttf"), ("B", "Inter-Bold.ttf")]:
            path = os.path.join(os.path.dirname(__file__), name)
            if os.path.exists(path):
                self.add_font("Inter", style, path, uni=True)

    def _font(self, style="", size=10):
        try: self.set_font("Inter", style, size)
        except: self.set_font("Helvetica", style, size)

    def header(self):
        self._font("B", 8)
        self.set_text_color(159, 176, 195)
        self.cell(0, 6, "Builtly | Kredittgrunnlag", align="L")
        self.cell(0, 6, datetime.now().strftime("%d.%m.%Y"), align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*self.accent)
        self.set_line_width(0.3)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self._font("", 7)
        self.set_text_color(159, 176, 195)
        self.cell(0, 8, f"Side {self.page_no()}/{{nb}} — Konfidensielt — Utkast, krever faglig kontroll", align="C")

    def cover_page(self, project_name, laantaker, laanetype):
        self.add_page()
        self.ln(50)
        self._font("B", 11)
        self.set_text_color(56, 194, 201)
        self.cell(0, 8, "KONFIDENSIELT", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)
        self._font("B", 28)
        self.set_text_color(6, 17, 26)
        self.cell(0, 14, "Kredittnotat", align="C", new_x="LMARGIN", new_y="NEXT")
        self._font("", 14)
        self.set_text_color(80, 100, 120)
        self.cell(0, 10, laanetype, align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(8)
        self._font("B", 16)
        self.set_text_color(6, 17, 26)
        self.cell(0, 10, project_name, align="C", new_x="LMARGIN", new_y="NEXT")
        self._font("", 11)
        self.set_text_color(80, 100, 120)
        self.cell(0, 8, f"Låntaker: {laantaker}", align="C", new_x="LMARGIN", new_y="NEXT")
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
        self.multi_cell(0, 5.5, str(text))
        self.ln(2)

    def key_value(self, key, value):
        self._font("B", 9)
        self.set_text_color(80, 100, 120)
        self.cell(70, 5.5, key)
        self._font("", 10)
        self.set_text_color(6, 17, 26)
        self.cell(0, 5.5, str(value), new_x="LMARGIN", new_y="NEXT")

    def status_box(self, status, text):
        color_map = {"Anbefalt innvilget": (34, 197, 94), "Anbefalt med vilkår": (245, 158, 11), "Ikke anbefalt": (239, 68, 68)}
        color = color_map.get(status, (56, 194, 201))
        self.ln(3)
        self.set_draw_color(*color)
        self.rect(10, self.get_y(), 190, 18, style="D")
        self.set_fill_color(*color)
        self.rect(10, self.get_y(), 4, 18, style="F")
        self._font("B", 12)
        self.set_text_color(*color)
        self.set_xy(18, self.get_y() + 2)
        self.cell(0, 7, status)
        self._font("", 9)
        self.set_text_color(40, 50, 60)
        self.set_xy(18, self.get_y() + 7)
        self.multi_cell(178, 5, text)
        self.ln(6)


def generate_credit_pdf(project_info, analysis) -> bytes:
    if not FPDF:
        return b""
    pdf = CreditPDF()
    pdf.alias_nb_pages()

    # Cover
    pdf.cover_page(
        project_info.get("navn", "Prosjekt"),
        project_info.get("laantaker", "-"),
        safe_get(analysis, "laanetype", project_info.get("laanetype", "")),
    )

    pdf.add_page()

    # 1. Sammendrag
    pdf.section_title(1, "Sammendrag og anbefaling")
    pdf.status_box(safe_get(analysis, "anbefaling", "Ikke vurdert"), safe_get(analysis, "sammendrag", ""))

    # 2. Nøkkeltall
    pdf.section_title(2, "Nøkkeltall")
    nt = safe_get(analysis, "noekkeltall", {})
    if isinstance(nt, dict):
        pdf.key_value("Totalinvestering:", f"{safe_get(nt, 'totalinvestering_mnok', 0)} MNOK")
        pdf.key_value("Søkt lån:", f"{safe_get(nt, 'soekt_laan_mnok', 0)} MNOK")
        pdf.key_value("Egenkapital:", f"{safe_get(nt, 'egenkapital_mnok', 0)} MNOK ({safe_get(nt, 'egenkapitalprosent', 0)}%)")
        pdf.key_value("Belåningsgrad (LTV):", f"{safe_get(nt, 'belaaningsgrad_ltv', 0)}%")
        pdf.key_value("Estimert markedsverdi:", f"{safe_get(nt, 'estimert_markedsverdi_mnok', 0)} MNOK")
        pdf.key_value("Netto yield:", f"{safe_get(nt, 'netto_yield_pst', 0)}%")
        pdf.key_value("DSCR:", f"{safe_get(nt, 'dscr', 0)}")
        pdf.key_value("ICR:", f"{safe_get(nt, 'icr', 0)}")
        pdf.key_value("Forhåndssalg/utleie:", f"{safe_get(nt, 'forhaandssalg_utleie_pst', 0)}%")

    # 3. Verdivurdering
    pdf.section_title(3, "Verdivurdering")
    vv = safe_get(analysis, "verdivurdering", {})
    if isinstance(vv, dict):
        pdf.key_value("Metode:", safe_get(vv, "metode", "-"))
        pdf.key_value("Oppgitt takst:", f"{safe_get(vv, 'oppgitt_takst_mnok', 0)} MNOK")
        pdf.key_value("Beregnet verdi:", f"{safe_get(vv, 'beregnet_verdi_mnok', 0)} MNOK")
        pdf.key_value("Avvik takst vs. beregnet:", f"{safe_get(vv, 'avvik_takst_vs_beregnet_pst', 0)}%")
        takst_ok = safe_get(vv, "takst_er_rimelig", True)
        pdf.key_value("Takst rimelig:", "Ja" if takst_ok else "NEI — se kommentar")
        pdf.body_text(safe_get(vv, "kommentar_takst", ""))

        br = safe_get(vv, "bolig_residual", {})
        if isinstance(br, dict) and safe_get(br, "residual_tomteverdi_mnok", 0):
            pdf.ln(2)
            pdf._font("B", 10)
            pdf.set_text_color(56, 194, 201)
            pdf.cell(0, 6, "Residualverdiberegning (Bolig)", new_x="LMARGIN", new_y="NEXT")
            pdf._font("", 9)
            pdf.set_text_color(40, 50, 60)
            pdf.key_value("Forventet salgsverdi:", f"{safe_get(br, 'forventet_salgsverdi_mnok', 0)} MNOK")
            pdf.key_value("Salgspris per kvm BRA:", f"{safe_get(br, 'salgsverdi_per_kvm_bra', 0)} kr")
            pdf.key_value("Byggekost per kvm BTA:", f"{safe_get(br, 'byggekost_per_kvm_bta', 0)} kr")
            pdf.key_value("Utbyggingskost eks. tomt:", f"{safe_get(br, 'total_utbyggingskost_eks_tomt_mnok', 0)} MNOK")
            pdf.key_value("Min. margin (12%):", f"{safe_get(br, 'minimummargin_12pst_mnok', 0)} MNOK")
            pdf.key_value("Residual tomteverdi:", f"{safe_get(br, 'residual_tomteverdi_mnok', 0)} MNOK")
            pdf.key_value("Oppgitt tomtekost:", f"{safe_get(br, 'oppgitt_tomtekost_mnok', 0)} MNOK")
            tomte_ok = safe_get(br, "tomtekost_innenfor_residual", True)
            pdf.key_value("Tomtekost innenfor residual:", "Ja" if tomte_ok else "NEI — for høy tomtepris")
            pdf.key_value("Faktisk margin:", f"{safe_get(br, 'faktisk_margin_pst', 0)}%")
            pdf.body_text(safe_get(br, "kommentar", ""))

        ny = safe_get(vv, "naering_yield", {})
        if isinstance(ny, dict) and safe_get(ny, "yield_on_cost_pst", 0):
            pdf.ln(2)
            pdf._font("B", 10)
            pdf.set_text_color(245, 158, 11)
            pdf.cell(0, 6, "Yield-analyse (Næring)", new_x="LMARGIN", new_y="NEXT")
            pdf._font("", 9)
            pdf.set_text_color(40, 50, 60)
            pdf.key_value("Brutto leieinntekt:", f"{safe_get(ny, 'brutto_leieinntekt_mnok', 0)} MNOK/år")
            pdf.key_value("Eierkostnader:", f"{safe_get(ny, 'eierkostnader_mnok', 0)} MNOK/år")
            pdf.key_value("Netto leieinntekt:", f"{safe_get(ny, 'netto_leieinntekt_mnok', 0)} MNOK/år")
            pdf.key_value("Yield on cost:", f"{safe_get(ny, 'yield_on_cost_pst', 0)}%")
            pdf.key_value("Antatt markedsyield:", f"{safe_get(ny, 'antatt_markedsyield_pst', 0)}%")
            pdf.key_value("Yield spread:", f"{safe_get(ny, 'yield_spread_pst', 0)}%")
            pdf.key_value("Verdi ved markedsyield:", f"{safe_get(ny, 'verdi_ved_markedsyield_mnok', 0)} MNOK")
            pdf.key_value("WAULT:", f"{safe_get(ny, 'wault_aar', 0)} år")
            pdf.key_value("Vakansrisiko:", f"{safe_get(ny, 'vakansrisiko_pst', 0)}%")
            verdiskaping = safe_get(ny, "verdiskaping_positiv", True)
            pdf.key_value("Verdiskaping:", "Positiv" if verdiskaping else "NEGATIV — yield on cost < markedsyield")
            pdf.body_text(safe_get(ny, "kommentar", ""))

        pdf.key_value("Bankens verdianslag:", f"{safe_get(vv, 'bankens_verdianslag_mnok', 0)} MNOK")
        pdf.key_value("Forsiktig verdi (70%):", f"{safe_get(vv, 'forsiktig_verdi_70pst_mnok', 0)} MNOK")
        pdf.key_value("LTV mot beregnet verdi:", f"{safe_get(vv, 'ltv_mot_beregnet_verdi_pst', 0)}%")

    # 4. Regulering
    pdf.section_title(4, "Regulering og tomt")
    reg = safe_get(analysis, "regulering_og_tomt", {})
    if isinstance(reg, dict):
        pdf.key_value("Reguleringsplan:", safe_get(reg, "reguleringsplan", "-"))
        pdf.key_value("Utnyttelsesgrad (BYA):", f"{safe_get(reg, 'utnyttelsesgrad_bya_pst', 0)}%")
        pdf.key_value("Tillatt vs. planlagt BTA:", safe_get(reg, "tillatt_vs_planlagt_bta", "-"))
        pdf.key_value("Rammegodkjenning:", safe_get(reg, "rammegodkjenning_status", "-"))
        pdf.body_text(safe_get(reg, "kommentar", ""))

    # 5. Økonomi
    pdf.section_title(5, "Økonomisk analyse")
    oek = safe_get(analysis, "oekonomisk_analyse", {})
    if isinstance(oek, dict):
        pdf.key_value("Totalkostnadskalkyle:", f"{safe_get(oek, 'totalkostnadskalkyle_mnok', 0)} MNOK")
        pdf.key_value("Entreprisekostnad:", f"{safe_get(oek, 'entreprisekostnad_mnok', 0)} MNOK")
        pdf.key_value("Tomtekostnad:", f"{safe_get(oek, 'tomtekostnad_mnok', 0)} MNOK")
        pdf.key_value("Offentlige avgifter:", f"{safe_get(oek, 'offentlige_avgifter_mnok', 0)} MNOK")
        pdf.key_value("Prosjektkostnader:", f"{safe_get(oek, 'prosjektkostnader_mnok', 0)} MNOK")
        pdf.key_value("Finanskostnader:", f"{safe_get(oek, 'finanskostnader_mnok', 0)} MNOK")
        pdf.key_value("Forventet salgsverdi:", f"{safe_get(oek, 'forventet_salgsverdi_mnok', 0)} MNOK")
        pdf.key_value("Forventet resultat:", f"{safe_get(oek, 'forventet_resultat_mnok', 0)} MNOK")
        pdf.key_value("Resultatmargin:", f"{safe_get(oek, 'resultatmargin_pst', 0)}%")

    # 6. Rentesensitivitet
    pdf.section_title(6, "Rentesensitivitet")
    rente = safe_get(analysis, "rentesensitivitet", [])
    if rente:
        pdf._font("B", 8)
        pdf.set_fill_color(240, 244, 248)
        pdf.set_text_color(80, 100, 120)
        pdf.cell(40, 7, "Rentenivå", border=1, fill=True)
        pdf.cell(50, 7, "Årsresultat (MNOK)", border=1, fill=True)
        pdf.cell(30, 7, "DSCR", border=1, fill=True)
        pdf.cell(0, 7, "Betjeningsevne", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf._font("", 9)
        pdf.set_text_color(40, 50, 60)
        for r in rente:
            if isinstance(r, dict):
                pdf.cell(40, 6, safe_get(r, "rentenivaa", "-"), border=1)
                pdf.cell(50, 6, str(safe_get(r, "aarsresultat_mnok", 0)), border=1)
                pdf.cell(30, 6, str(safe_get(r, "dscr", 0)), border=1)
                be = safe_get(r, "betjeningsevne", "-")
                be_color = {"God": (34, 197, 94), "Akseptabel": (245, 158, 11), "Svak": (239, 68, 68)}.get(be, (40, 50, 60))
                pdf.set_text_color(*be_color)
                pdf.cell(0, 6, be, border=1, new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(40, 50, 60)

    # 7. Sikkerheter
    pdf.section_title(7, "Sikkerheter og pant")
    for s in safe_get(analysis, "sikkerheter", []):
        if isinstance(s, dict):
            pdf.key_value(f"{safe_get(s, 'type', '')} ({safe_get(s, 'prioritet', '')})",
                          f"{safe_get(s, 'verdi_mnok', 0)} MNOK — {safe_get(s, 'kommentar', '')}")

    # 8. Risikovurdering
    pdf.section_title(8, "Risikovurdering")
    for r in safe_get(analysis, "risikovurdering", []):
        if isinstance(r, dict):
            pdf.key_value(safe_get(r, "risiko", ""), f"S: {safe_get(r, 'sannsynlighet', '-')} / K: {safe_get(r, 'konsekvens', '-')}")
            pdf.body_text(f"  Mitigering: {safe_get(r, 'mitigering', '-')}")

    # 9. Styrker / svakheter
    pdf.section_title(9, "Styrker og svakheter")
    pdf._font("B", 10)
    pdf.set_text_color(34, 197, 94)
    pdf.cell(0, 6, "Styrker:", new_x="LMARGIN", new_y="NEXT")
    pdf._font("", 9)
    pdf.set_text_color(40, 50, 60)
    for s in safe_get(analysis, "styrker", []):
        pdf.body_text(f"+ {s}")
    pdf._font("B", 10)
    pdf.set_text_color(239, 68, 68)
    pdf.cell(0, 6, "Svakheter:", new_x="LMARGIN", new_y="NEXT")
    pdf._font("", 9)
    pdf.set_text_color(40, 50, 60)
    for s in safe_get(analysis, "svakheter", []):
        pdf.body_text(f"- {s}")

    # 10. Vilkår
    pdf.section_title(10, "Foreslåtte vilkår")
    for i, v in enumerate(safe_get(analysis, "vilkaar", []), 1):
        pdf.body_text(f"{i}. {v}")

    # 11. Covenants
    pdf.section_title(11, "Covenants")
    cov = safe_get(analysis, "covenants", [])
    if cov:
        pdf._font("B", 8)
        pdf.set_fill_color(240, 244, 248)
        pdf.set_text_color(80, 100, 120)
        pdf.cell(70, 7, "Covenant", border=1, fill=True)
        pdf.cell(50, 7, "Grenseverdi", border=1, fill=True)
        pdf.cell(0, 7, "Målefrekvens", border=1, fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf._font("", 9)
        pdf.set_text_color(40, 50, 60)
        for c in cov:
            if isinstance(c, dict):
                pdf.cell(70, 6, safe_get(c, "covenant", ""), border=1)
                pdf.cell(50, 6, safe_get(c, "grenseverdi", ""), border=1)
                pdf.cell(0, 6, safe_get(c, "maalefrekvens", ""), border=1, new_x="LMARGIN", new_y="NEXT")

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
    pdf.cell(0, 5, "Kredittnotatet er automatisk generert og skal gjennomgås av kredittavdelingen før fremleggelse for kredittkomité.")

    return pdf.output()


# ────────────────────────────────────────────────────────────────
# PREMIUM CSS (same as other Builtly modules)
# ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    #MainMenu, footer, header {visibility: hidden;}
    header[data-testid="stHeader"] {visibility: hidden; height: 0;}
    :root {
        --bg: #06111a; --panel: rgba(10,22,35,0.78); --stroke: rgba(120,145,170,0.18);
        --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38bdf8; --accent-warm: #f59e0b;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; }

    .hero-card { background: linear-gradient(135deg, rgba(10,22,35,0.95), rgba(16,32,50,0.95));
        border: 1px solid rgba(120,145,170,0.15); border-radius: 20px; padding: 2.5rem 2.8rem 2rem; margin-bottom: 2rem; }
    .hero-eyebrow { font-size: 0.78rem; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: #38bdf8; margin-bottom: 0.4rem; }
    .hero-title { font-size: 1.85rem; font-weight: 800; line-height: 1.2; color: #f5f7fb; margin-bottom: 0.75rem; }
    .hero-subtitle { font-size: 0.98rem; color: #9fb0c3; line-height: 1.6; max-width: 750px; }
    .hero-pills { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 1.1rem; }
    .hero-pill { background: rgba(56,194,201,0.08); border: 1px solid rgba(56,194,201,0.25); border-radius: 20px; padding: 4px 14px; font-size: 0.8rem; font-weight: 600; color: #38bdf8; }
    .hero-badge { display: inline-block; background: rgba(245,158,11,0.12); border: 1px solid rgba(245,158,11,0.35); border-radius: 6px; padding: 2px 10px; font-size: 0.72rem; font-weight: 700; color: #f59e0b; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 1rem; }

    .metric-row { display: flex; gap: 16px; margin-bottom: 1.5rem; flex-wrap: wrap; }
    .metric-card { flex: 1; min-width: 180px; background: rgba(10,22,35,0.6); border: 1px solid rgba(120,145,170,0.18); border-radius: 14px; padding: 1.1rem 1.3rem; }
    .metric-card .mc-value { font-size: 1.6rem; font-weight: 800; color: #38bdf8; margin-bottom: 2px; }
    .metric-card .mc-label { font-size: 0.82rem; font-weight: 700; color: #c8d3df; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
    .metric-card .mc-desc { font-size: 0.78rem; color: #9fb0c3; line-height: 1.4; }

    .section-header { margin-top: 2.5rem; margin-bottom: 1rem; }
    .section-header h3 { color: #f5f7fb !important; font-weight: 750 !important; font-size: 1.2rem !important; margin-bottom: 4px !important; }
    .section-header p { color: #9fb0c3 !important; font-size: 0.9rem !important; }
    .section-badge { display: inline-block; background: rgba(56,194,201,0.1); border: 1px solid rgba(56,194,201,0.25); border-radius: 6px; padding: 1px 8px; font-size: 0.7rem; font-weight: 700; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }

    .stTextInput input, .stTextArea textarea, .stNumberInput input, .stSelectbox > div > div,
    .stMultiSelect > div > div { background-color: rgba(10,22,35,0.6) !important; color: #f5f7fb !important;
        border: 1px solid rgba(120,145,170,0.2) !important; border-radius: 12px !important; }
    .stSelectbox label, .stMultiSelect label, .stTextInput label, .stTextArea label,
    .stNumberInput label, .stFileUploader label, .stToggle label, .stRadio label,
    .stDateInput label { color: #c8d3df !important; font-weight: 600 !important; }
    div[data-baseweb="select"] > div { background-color: rgba(10,22,35,0.6) !important; border-color: rgba(120,145,170,0.2) !important; }

    .stTabs [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid rgba(120,145,170,0.15); }
    .stTabs [data-baseweb="tab"] { background: transparent !important; color: #9fb0c3 !important; border-radius: 10px 10px 0 0 !important; padding: 8px 18px !important; font-weight: 600 !important; }
    .stTabs [aria-selected="true"] { background: rgba(56,194,201,0.08) !important; color: #38bdf8 !important; border-bottom: 2px solid #38bdf8 !important; }

    button[kind="primary"], .stFormSubmitButton > button {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important; border: none !important; font-weight: 750 !important;
        border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; }
    .stDownloadButton > button { background-color: rgba(255,255,255,0.04) !important; color: #c8d3df !important;
        border: 1px solid rgba(120,145,170,0.25) !important; border-radius: 10px !important; font-weight: 600 !important; }

    .stDataFrame { border-radius: 12px; overflow: hidden; }
    .stMarkdown, .stMarkdown p, .stMarkdown li, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 { color: #f5f7fb !important; }

    .disclaimer-banner { background: rgba(245,158,11,0.06); border: 1px solid rgba(245,158,11,0.25); border-radius: 14px; padding: 1.1rem 1.4rem; margin-top: 2rem; }
    .disclaimer-banner .db-title { font-weight: 700; font-size: 0.9rem; color: #f59e0b; margin-bottom: 4px; }
    .disclaimer-banner .db-text { font-size: 0.82rem; color: #9fb0c3; line-height: 1.5; }

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
    eyebrow="Kredittgrunnlag",
    title="Strukturert kredittnotat for tomtelån, byggelån og langsiktig finansiering.",
    subtitle=(
        "Last opp reguleringsplan, prosjektkalkyle, leieavtaler, grunnboksutskrift og lånesøknad. "
        "Du får et komplett kredittnotat med nøkkeltall, rentesensitivitet, risikovurdering, "
        "sikkerheter og foreslåtte vilkår — tilpasset kredittkomiteens beslutningsformat."
    ),
    pills=["LTV / DSCR / ICR", "Rentesensitivitet", "Pantesikkerhet", "Regulering", "Covenants", "Risikovurdering"],
    badge="Kredittgrunnlag",
)


# ────────────────────────────────────────────────────────────────
# MAIN LAYOUT
# ────────────────────────────────────────────────────────────────
left, right = st.columns([3, 2], gap="large")

with left:
    render_section("1. Prosjekt og låntaker", "Registrer nøkkeldata for prosjektet og lånesøknaden.", "Input")

    c1, c2 = st.columns(2)
    with c1:
        prosjekt_navn = st.text_input("Prosjektnavn", value="", placeholder="F.eks. Havneparken Trinn 3")
        laantaker = st.text_input("Låntaker / utbygger", value="", placeholder="Selskap AS")
        orgnr = st.text_input("Org.nr.", value="", placeholder="999 888 777")
        laanetype = st.selectbox("Lånetype", ["Tomtelån", "Byggelån", "Langsiktig lån (utleie)", "Kombinert tomte- og byggelån", "Refinansiering"])
        soekt_laan = st.number_input("Søkt lån (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f")
    with c2:
        totalinvestering = st.number_input("Totalinvestering (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f")
        egenkapital = st.number_input("Egenkapital (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f")
        prosjekttype = st.selectbox("Prosjekttype", ["Bolig - salg", "Bolig - utleie", "Kontor", "Handel/retail", "Logistikk", "Mixed-use", "Hotell", "Annet"])
        entrepriseform = st.selectbox("Entrepriseform", ["Totalentreprise", "Hovedentreprise", "Delte entrepriser", "Byggherrestyrt", "Annet"])

    render_section("2. Tomt og regulering", "Detaljer om tomt, regulering og godkjenningsstatus.", "Regulering")

    c3, c4 = st.columns(2)
    with c3:
        antall_enheter = st.number_input("Antall enheter", min_value=0, value=0, step=1)
        bta_kvm = st.number_input("BTA (kvm)", min_value=0, value=0, step=100)
        tomt_kvm = st.number_input("Tomt (kvm)", min_value=0, value=0, step=100)
        reguleringsplan = st.selectbox("Reguleringsplan", ["Vedtatt", "Under behandling", "Ikke påbegynt", "Krever omregulering"])
    with c4:
        rammegodkjenning = st.selectbox("Rammegodkjenning / IG", ["Godkjent", "Søkt", "Ikke søkt"])
        byggestart = st.date_input("Planlagt byggestart", value=date(2026, 9, 1))
        ferdigstillelse = st.date_input("Planlagt ferdigstillelse", value=date(2028, 12, 31))
        forhaandssalg = st.number_input("Forhåndssalg/utleiegrad (%)", min_value=0, max_value=100, value=0, step=5)

    render_section("3. Økonomi og sikkerheter", "Inntekter, gjeld og pantesikkerhet.", "Økonomi")

    c5, c6 = st.columns(2)
    with c5:
        inntekt = st.number_input("Forventet salgs-/leieinntekt (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f")
        eksisterende_gjeld = st.number_input("Eksisterende gjeld (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f")
    with c6:
        pantesikkerhet = st.selectbox("Primær pantesikkerhet", ["1. prioritet pant i eiendom", "2. prioritet pant", "Pant i tomt + fremtidig bygg", "Selvskyldnergaranti", "Kombinert"])
        garanti = st.multiselect("Tilleggsgarantier", ["Bankgaranti §12", "Morselskapsgaranti", "Personlig garanti", "Depositum", "Ingen"])

    render_section("3b. Verdivurdering og dokumentasjon på verdi",
                   "En takst alene er ikke tilstrekkelig. For bolig beregnes residualverdi, for næring brukes yield-metode.",
                   "Verdivurdering")

    is_bolig = prosjekttype in ["Bolig - salg", "Mixed-use"]
    is_naering = prosjekttype in ["Bolig - utleie", "Kontor", "Handel/retail", "Logistikk", "Hotell", "Mixed-use", "Annet"]

    cv1, cv2 = st.columns(2)
    with cv1:
        har_takst = st.toggle("Foreligger det takst?", value=False)
        takst_mnok = st.number_input("Takstverdi (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f") if har_takst else 0.0
        if har_takst:
            takst_kilde = st.selectbox("Takstkilde", ["Ekstern takstmann", "Internvurdering bank", "Megler", "Utbyggers eget estimat", "Annet"])
            takst_dato = st.date_input("Takstdato", value=date.today())
    with cv2:
        tomtekost_mnok = st.number_input("Betalt / avtalt tomtepris (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f")
        entreprisekost_mnok = st.number_input("Entreprisekost (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f")

    if is_bolig:
        render_html("""<div style="background:rgba(56,194,201,0.06);border:1px solid rgba(56,194,201,0.18);border-radius:12px;padding:1rem 1.2rem;margin:0.8rem 0;">
            <div style="font-weight:700;font-size:0.85rem;color:#38bdf8;margin-bottom:4px;">Residualverdimetode (Bolig)</div>
            <div style="font-size:0.82rem;color:#9fb0c3;">En tomt er aldri verdt mer enn det som gir utbygger minimum 12% margin. Residualverdi = Salgsverdi - Utbyggingskost - Margin (12%).</div>
        </div>""")
        cb1, cb2 = st.columns(2)
        with cb1:
            forventet_salgspris_kvm = st.number_input("Forventet salgspris (kr/kvm BRA)", min_value=0, value=0, step=1000)
            bra_kvm = st.number_input("Salgbart areal BRA (kvm)", min_value=0, value=0, step=10)
        with cb2:
            byggekost_kvm = st.number_input("Byggekost (kr/kvm BTA)", min_value=0, value=0, step=500)
            target_margin = st.number_input("Minimum utviklermargin (%)", min_value=0.0, value=12.0, step=1.0, format="%.1f")
    else:
        forventet_salgspris_kvm = 0; bra_kvm = 0; byggekost_kvm = 0; target_margin = 12.0

    if is_naering:
        render_html("""<div style="background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.18);border-radius:12px;padding:1rem 1.2rem;margin:0.8rem 0;">
            <div style="font-weight:700;font-size:0.85rem;color:#f59e0b;margin-bottom:4px;">Yield-metode (Næring)</div>
            <div style="font-size:0.82rem;color:#9fb0c3;">Verdi = Netto leieinntekt / Markedsyield. Yield on cost må være høyere enn markedsyield for å skape verdi.</div>
        </div>""")
        cn1, cn2 = st.columns(2)
        with cn1:
            brutto_leie_mnok = st.number_input("Brutto leieinntekt (MNOK/år)", min_value=0.0, value=0.0, step=0.5, format="%.1f")
            eierkost_mnok = st.number_input("Eierkostnader (MNOK/år)", min_value=0.0, value=0.0, step=0.1, format="%.1f")
            antatt_markedsyield = st.number_input("Antatt markedsyield (%)", min_value=0.0, value=5.0, step=0.25, format="%.2f")
        with cn2:
            wault = st.number_input("WAULT (år)", min_value=0.0, value=0.0, step=0.5, format="%.1f")
            vakanse_pst = st.number_input("Strukturell vakanse (%)", min_value=0, max_value=100, value=5, step=1)
            exit_yield = st.number_input("Antatt exit-yield (%)", min_value=0.0, value=5.5, step=0.25, format="%.2f")
    else:
        brutto_leie_mnok = 0.0; eierkost_mnok = 0.0; antatt_markedsyield = 5.0
        wault = 0.0; vakanse_pst = 5; exit_yield = 5.5

    render_section("4. Dokumentasjon", "Last opp grunnlag for kredittanalysen.", "Dokumenter")

    uploads = st.file_uploader(
        "Reguleringsplan, prosjektkalkyle, leieavtaler, grunnboksutskrift, lånesøknad, takst, regnskap, budsjett",
        type=["pdf", "xlsx", "xls", "csv", "docx", "jpg", "jpeg", "png", "zip"],
        accept_multiple_files=True,
        key="credit_uploads",
    )

    spesielle_forhold = st.text_area(
        "Spesielle forhold",
        value="",
        placeholder="F.eks. tomten har kulturminnekrav, utbygger har pågående tvistesak, det er krav om rekkefølgebestemmelser...",
        height=90,
    )

    run_analysis = st.button("Generer kredittnotat", type="primary", use_container_width=True)

with right:
    render_section("Om kredittgrunnlag", "Modulen bygger et strukturert beslutningsgrunnlag for kredittkomitéen.", "Info")

    render_panel(
        "Hva kredittnotatet inneholder",
        "Alle nøkkeltall og vurderinger kredittkomitéen trenger for å fatte beslutning.",
        [
            "Nøkkeltall: LTV, DSCR, ICR, egenkapitalprosent, yield",
            "Regulering: vedtatt plan, utnyttelse, rammegodkjenning",
            "Totalkostnadskalkyle med fordeling på poster",
            "Rentesensitivitet: betjeningsevne ved +1%, +2%, +3%",
            "Sikkerheter og pantevurdering",
            "Risikovurdering med sannsynlighet og konsekvens",
            "Foreslåtte vilkår og covenants",
        ],
        tone="blue",
        badge="Innhold",
    )

    render_panel(
        "Støttet for alle lånetyper",
        "Modulen håndterer ulike lånestrukturer med tilpassede analyser.",
        [
            "Tomtelån — reguleringsrisiko, utnyttelse, rekkefølgekrav",
            "Byggelån — fremdrift, entreprisekost, forhåndssalg",
            "Langsiktig utleielån — yield, leiekontrakter, WAULT, DSCR",
            "Kombinert — totalvurdering med faseinndeling",
        ],
        tone="gold",
        badge="Lånetyper",
    )

    render_panel(
        "Rapport og eksport",
        "Komplett kredittnotat som PDF, klar for kredittkomité.",
        [
            "Profesjonell PDF med alle seksjoner",
            "Konfidensialitetsmerking på alle sider",
            "Rentesensitivitetstabell",
            "Covenant-oversikt med grenseverdier",
            "JSON-eksport for videre bearbeiding",
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
        "laantaker": laantaker or "Ikke oppgitt",
        "orgnr": orgnr,
        "laanetype": laanetype,
        "soekt_laan_mnok": soekt_laan,
        "totalinvestering_mnok": totalinvestering,
        "egenkapital_mnok": egenkapital,
        "prosjekttype": prosjekttype,
        "entrepriseform": entrepriseform,
        "antall_enheter": antall_enheter,
        "bta_kvm": bta_kvm,
        "tomt_kvm": tomt_kvm,
        "reguleringsplan": reguleringsplan,
        "rammegodkjenning": rammegodkjenning,
        "byggestart": str(byggestart),
        "ferdigstillelse": str(ferdigstillelse),
        "forhaandssalg_pst": forhaandssalg,
        "inntekt_mnok": inntekt,
        "eksisterende_gjeld_mnok": eksisterende_gjeld,
        "pantesikkerhet": pantesikkerhet,
        "garantier": garanti,
        "spesielle_forhold": spesielle_forhold,
        # Verdivurdering
        "har_takst": har_takst,
        "takst_mnok": takst_mnok,
        "takst_kilde": takst_kilde if har_takst else "Ikke oppgitt",
        "tomtekost_mnok": tomtekost_mnok,
        "entreprisekost_mnok": entreprisekost_mnok,
        # Bolig residual
        "forventet_salgspris_kvm": forventet_salgspris_kvm,
        "bra_kvm": bra_kvm,
        "byggekost_kvm": byggekost_kvm,
        "target_margin": target_margin,
        # Næring yield
        "brutto_leie_mnok": brutto_leie_mnok,
        "eierkost_mnok": eierkost_mnok,
        "antatt_markedsyield": antatt_markedsyield,
        "wault": wault,
        "vakanse_pst": vakanse_pst,
        "exit_yield": exit_yield,
    }

    client_type, client = get_ai_client()
    if not client:
        st.error("Ingen AI-nøkkel konfigurert. Sett OPENAI_API_KEY eller GOOGLE_API_KEY i miljøvariablene.")
        st.stop()

    doc_text = ""
    if uploads:
        with st.spinner("Leser dokumenter..."):
            doc_text = extract_text_from_uploads(uploads)

    with st.spinner("Genererer kredittnotat..."):
        analysis = run_credit_analysis(client_type, client, project_info, doc_text)

    if not analysis:
        st.error("Analysen returnerte ingen resultater. Sjekk dokumentgrunnlaget og prøv igjen.")
        st.stop()

    st.session_state["credit_analysis"] = analysis
    st.session_state["credit_project_info"] = project_info


# ── Display results ──
if "credit_analysis" in st.session_state:
    analysis = st.session_state["credit_analysis"]
    project_info = st.session_state.get("credit_project_info", {})

    render_section("Kredittnotat", "Strukturert beslutningsgrunnlag basert på innsendt dokumentasjon og prosjektdata.", "Resultat")

    # Status banner
    anbefaling = safe_get(analysis, "anbefaling", "Ikke vurdert")
    status_class = {"Anbefalt innvilget": "status-green", "Anbefalt med vilkår": "status-yellow", "Ikke anbefalt": "status-red"}.get(anbefaling, "")
    render_html(f"""
    <div style="background:rgba(10,22,35,0.7);border:1px solid rgba(120,145,170,0.2);border-radius:16px;padding:1.5rem 2rem;margin-bottom:1.5rem;">
        <div style="font-size:0.78rem;color:#9fb0c3;text-transform:uppercase;font-weight:700;letter-spacing:0.08em;margin-bottom:4px;">Anbefaling til kredittkomité</div>
        <div class="{status_class}" style="font-size:1.5rem;margin-bottom:6px;">{anbefaling}</div>
        <div style="color:#c8d3df;font-size:0.92rem;line-height:1.6;">{safe_get(analysis, 'sammendrag', '')}</div>
    </div>""")

    # Key metrics
    nt = safe_get(analysis, "noekkeltall", {})
    if isinstance(nt, dict):
        render_metric_cards([
            (f"{safe_get(nt, 'soekt_laan_mnok', 0)} MNOK", "Søkt lån", "Forespurt lånebeløp"),
            (f"{safe_get(nt, 'egenkapitalprosent', 0)}%", "Egenkapital", "Andel egenkapital"),
            (f"{safe_get(nt, 'belaaningsgrad_ltv', 0)}%", "LTV", "Loan-to-value"),
            (f"{safe_get(nt, 'dscr', 0)}", "DSCR", "Debt service coverage ratio"),
        ])
        render_metric_cards([
            (f"{safe_get(nt, 'netto_yield_pst', 0)}%", "Netto yield", "Løpende avkastning"),
            (f"{safe_get(nt, 'icr', 0)}", "ICR", "Interest coverage ratio"),
            (f"{safe_get(nt, 'estimert_markedsverdi_mnok', 0)} MNOK", "Markedsverdi", "Estimert ved ferdigstillelse"),
            (f"{safe_get(nt, 'forhaandssalg_utleie_pst', 0)}%", "Forhåndssalg/utleie", "Sikret inntektsgrunnlag"),
        ])

    # Tabs
    tabs = st.tabs(["Verdivurdering", "Regulering", "Økonomi", "Rentesensitivitet", "Sikkerheter", "Risiko", "Styrker/svakheter", "Vilkår & covenants", "Eksport"])

    with tabs[0]:
        vv = safe_get(analysis, "verdivurdering", {})
        if isinstance(vv, dict):
            metode = safe_get(vv, "metode", "Ikke vurdert")
            takst_rimelig = safe_get(vv, "takst_er_rimelig", True)
            takst_color = "status-green" if takst_rimelig else "status-red"

            render_metric_cards([
                (metode, "Metode", "Verdivurderingsmetode benyttet"),
                (f"{safe_get(vv, 'oppgitt_takst_mnok', 0)} MNOK", "Oppgitt takst", "Takstverdi fra ekstern/intern"),
                (f"{safe_get(vv, 'beregnet_verdi_mnok', 0)} MNOK", "Beregnet verdi", "Builtly-beregnet verdi"),
                (f"{safe_get(vv, 'ltv_mot_beregnet_verdi_pst', 0)}%", "LTV (beregnet)", "Belåningsgrad mot beregnet verdi"),
            ])

            # Takst-vurdering
            if not takst_rimelig:
                render_html(f"""<div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.25);border-radius:12px;padding:1rem 1.2rem;margin:0.8rem 0;">
                    <div style="font-weight:700;font-size:0.9rem;color:#ef4444;margin-bottom:4px;">⚠ Takst vurdert som urealistisk</div>
                    <div style="font-size:0.85rem;color:#c8d3df;line-height:1.5;">{safe_get(vv, 'kommentar_takst', '')}</div>
                    <div style="font-size:0.82rem;color:#9fb0c3;margin-top:6px;">Avvik takst vs. beregnet: <strong style="color:#ef4444;">{safe_get(vv, 'avvik_takst_vs_beregnet_pst', 0)}%</strong></div>
                </div>""")
            else:
                render_html(f"""<div style="background:rgba(34,197,94,0.06);border:1px solid rgba(34,197,94,0.18);border-radius:12px;padding:1rem 1.2rem;margin:0.8rem 0;">
                    <div style="font-weight:700;font-size:0.9rem;color:#22c55e;margin-bottom:4px;">✓ Takst vurdert som rimelig</div>
                    <div style="font-size:0.85rem;color:#c8d3df;line-height:1.5;">{safe_get(vv, 'kommentar_takst', '')}</div>
                </div>""")

            # Bolig residual
            br = safe_get(vv, "bolig_residual", {})
            if isinstance(br, dict) and safe_get(br, "residual_tomteverdi_mnok", 0):
                st.markdown("---")
                st.markdown("**Residualverdiberegning (Bolig)**")
                tomte_ok = safe_get(br, "tomtekost_innenfor_residual", True)
                render_metric_cards([
                    (f"{safe_get(br, 'forventet_salgsverdi_mnok', 0)} MNOK", "Forventet salgsverdi", f"{safe_get(br, 'salgsverdi_per_kvm_bra', 0)} kr/kvm BRA"),
                    (f"{safe_get(br, 'residual_tomteverdi_mnok', 0)} MNOK", "Residual tomteverdi", "Maks tomteverdi med 12% margin"),
                    (f"{safe_get(br, 'oppgitt_tomtekost_mnok', 0)} MNOK", "Oppgitt tomtekost", "✓ OK" if tomte_ok else "⚠ Over residual"),
                    (f"{safe_get(br, 'faktisk_margin_pst', 0)}%", "Faktisk margin", "Minimum 12% for boligutvikling"),
                ])
                if not tomte_ok:
                    render_html("""<div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.25);border-radius:12px;padding:0.8rem 1rem;margin:0.5rem 0;">
                        <div style="font-size:0.85rem;color:#ef4444;font-weight:700;">⚠ Tomtekost overstiger residualverdi — prosjektet har for lav margin</div>
                    </div>""")
                st.markdown(safe_get(br, "kommentar", ""))

            # Næring yield
            ny = safe_get(vv, "naering_yield", {})
            if isinstance(ny, dict) and safe_get(ny, "yield_on_cost_pst", 0):
                st.markdown("---")
                st.markdown("**Yield-analyse (Næring)**")
                verdiskaping = safe_get(ny, "verdiskaping_positiv", True)
                render_metric_cards([
                    (f"{safe_get(ny, 'netto_leieinntekt_mnok', 0)} MNOK", "Netto leie/år", "Etter eierkost og vakanse"),
                    (f"{safe_get(ny, 'yield_on_cost_pst', 0)}%", "Yield on cost", "Netto leie / total prosjektkost"),
                    (f"{safe_get(ny, 'antatt_markedsyield_pst', 0)}%", "Markedsyield", "Antatt kjøpers avkastningskrav"),
                    (f"{safe_get(ny, 'yield_spread_pst', 0)}%", "Yield spread", "YoC minus markedsyield"),
                ])
                render_metric_cards([
                    (f"{safe_get(ny, 'verdi_ved_markedsyield_mnok', 0)} MNOK", "Verdi v/markedsyield", "Netto leie / markedsyield"),
                    (f"{safe_get(ny, 'wault_aar', 0)} år", "WAULT", "Vektet gjenstående leietid"),
                    (f"{safe_get(ny, 'vakansrisiko_pst', 0)}%", "Vakansrisiko", "Strukturell vakanse"),
                ])
                if not verdiskaping:
                    render_html("""<div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.25);border-radius:12px;padding:0.8rem 1rem;margin:0.5rem 0;">
                        <div style="font-size:0.85rem;color:#ef4444;font-weight:700;">⚠ Yield on cost &lt; markedsyield — prosjektet skaper ikke verdi</div>
                    </div>""")
                st.markdown(safe_get(ny, "kommentar", ""))

            # Bank's own value
            render_metric_cards([
                (f"{safe_get(vv, 'bankens_verdianslag_mnok', 0)} MNOK", "Bankens verdianslag", "Anbefalt verdi for belåning"),
                (f"{safe_get(vv, 'forsiktig_verdi_70pst_mnok', 0)} MNOK", "Forsiktig verdi (70%)", "Konservativt scenario"),
            ])

    with tabs[1]:
        reg = safe_get(analysis, "regulering_og_tomt", {})
        if isinstance(reg, dict):
            render_metric_cards([
                (safe_get(reg, "rammegodkjenning_status", "-"), "Rammegodkjenning", "Status for byggetillatelse"),
                (f"{safe_get(reg, 'utnyttelsesgrad_bya_pst', 0)}%", "Utnyttelsesgrad", "BYA i prosent"),
            ])
            st.markdown(f"**Reguleringsplan:** {safe_get(reg, 'reguleringsplan', '-')}")
            st.markdown(f"**Tillatt vs. planlagt BTA:** {safe_get(reg, 'tillatt_vs_planlagt_bta', '-')}")
            st.markdown(safe_get(reg, "kommentar", ""))

    with tabs[2]:
        oek = safe_get(analysis, "oekonomisk_analyse", {})
        if isinstance(oek, dict):
            rows = [
                {"Post": "Totalkostnadskalkyle", "MNOK": safe_get(oek, "totalkostnadskalkyle_mnok", 0)},
                {"Post": "Entreprisekostnad", "MNOK": safe_get(oek, "entreprisekostnad_mnok", 0)},
                {"Post": "Tomtekostnad", "MNOK": safe_get(oek, "tomtekostnad_mnok", 0)},
                {"Post": "Offentlige avgifter", "MNOK": safe_get(oek, "offentlige_avgifter_mnok", 0)},
                {"Post": "Prosjektkostnader", "MNOK": safe_get(oek, "prosjektkostnader_mnok", 0)},
                {"Post": "Finanskostnader", "MNOK": safe_get(oek, "finanskostnader_mnok", 0)},
                {"Post": "Forventet salgsverdi", "MNOK": safe_get(oek, "forventet_salgsverdi_mnok", 0)},
                {"Post": "Forventet resultat", "MNOK": safe_get(oek, "forventet_resultat_mnok", 0)},
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            render_metric_cards([
                (f"{safe_get(oek, 'resultatmargin_pst', 0)}%", "Resultatmargin", "Forventet prosjektmargin"),
            ])

    with tabs[3]:
        rente = safe_get(analysis, "rentesensitivitet", [])
        if rente:
            rows = []
            for r in rente:
                if isinstance(r, dict):
                    rows.append({
                        "Rentenivå": safe_get(r, "rentenivaa", "-"),
                        "Årsresultat (MNOK)": safe_get(r, "aarsresultat_mnok", 0),
                        "DSCR": safe_get(r, "dscr", 0),
                        "Betjeningsevne": safe_get(r, "betjeningsevne", "-"),
                    })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tabs[4]:
        sik = safe_get(analysis, "sikkerheter", [])
        if sik:
            rows = []
            for s in sik:
                if isinstance(s, dict):
                    rows.append({
                        "Sikkerhet": safe_get(s, "type", ""),
                        "Verdi (MNOK)": safe_get(s, "verdi_mnok", 0),
                        "Prioritet": safe_get(s, "prioritet", ""),
                        "Kommentar": safe_get(s, "kommentar", ""),
                    })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tabs[5]:
        risks = safe_get(analysis, "risikovurdering", [])
        if risks:
            rows = []
            for r in risks:
                if isinstance(r, dict):
                    rows.append({
                        "Risiko": safe_get(r, "risiko", ""),
                        "Sannsynlighet": safe_get(r, "sannsynlighet", ""),
                        "Konsekvens": safe_get(r, "konsekvens", ""),
                        "Mitigering": safe_get(r, "mitigering", ""),
                    })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tabs[6]:
        sc1, sc2 = st.columns(2)
        with sc1:
            st.markdown("**Styrker:**")
            for s in safe_get(analysis, "styrker", []):
                st.markdown(f"✅ {s}")
        with sc2:
            st.markdown("**Svakheter:**")
            for s in safe_get(analysis, "svakheter", []):
                st.markdown(f"⚠️ {s}")

    with tabs[7]:
        st.markdown("**Foreslåtte vilkår:**")
        for i, v in enumerate(safe_get(analysis, "vilkaar", []), 1):
            st.markdown(f"**{i}.** {v}")

        st.markdown("---")
        st.markdown("**Covenants:**")
        cov = safe_get(analysis, "covenants", [])
        if cov:
            rows = []
            for c in cov:
                if isinstance(c, dict):
                    rows.append({
                        "Covenant": safe_get(c, "covenant", ""),
                        "Grenseverdi": safe_get(c, "grenseverdi", ""),
                        "Målefrekvens": safe_get(c, "maalefrekvens", ""),
                    })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tabs[8]:
        pdf_bytes = generate_credit_pdf(project_info, analysis)
        if pdf_bytes:
            st.download_button(
                "Last ned kredittnotat (PDF)",
                data=pdf_bytes,
                file_name=f"kredittnotat_{project_info.get('navn', 'prosjekt').replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        st.download_button(
            "Last ned analyse (JSON)",
            data=json.dumps({"prosjekt": project_info, "analyse": analysis}, ensure_ascii=False, indent=2),
            file_name=f"kredittnotat_{datetime.now().strftime('%Y%m%d')}.json",
            mime="application/json",
            use_container_width=True,
        )


# ────────────────────────────────────────────────────────────────
# DISCLAIMER
# ────────────────────────────────────────────────────────────────
render_html("""
<div class="disclaimer-banner" style="margin-top: 2rem;">
    <div class="db-title">Konfidensielt utkast — krever faglig kontroll</div>
    <div class="db-text">
        Kredittnotatet er automatisk generert basert på innsendt dokumentasjon og oppgitte prosjektdata.
        Resultatet skal gjennomgås og kvalitetssikres av kredittavdelingen før det fremlegges for
        kredittkomité. Alle nøkkeltall, vurderinger og anbefalinger må verifiseres mot faktiske forhold.
    </div>
</div>
""")
