# -*- coding: utf-8 -*-
"""
Builtly | Teknisk Due Diligence (TDD)
Self-contained Streamlit module – no external builtly_* dependencies.
Multi-language support: NO, EN, DE, SV, DA.
Design language matches Konstruksjon (RIB) module.
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    from fpdf import FPDF
except ImportError:
    FPDF = None  # type: ignore[assignment, misc]

try:
    import google.generativeai as genai
except Exception:
    genai = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


# ────────────────────────────────────────────────────────────────
# 1. PAGE CONFIG
# ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Builtly | Technical Due Diligence", layout="wide", initial_sidebar_state="collapsed")

DB_DIR = Path("qa_database")
SSOT_FILE = DB_DIR / "ssot.json"


# ────────────────────────────────────────────────────────────────
# 2. HELPERS
# ────────────────────────────────────────────────────────────────
def render_html(html: str) -> None:
    st.markdown(html.replace("\n", " "), unsafe_allow_html=True)

def logo_data_uri() -> str:
    for c in ["logo-white.png", "logo.png"]:
        if os.path.exists(c):
            s = Path(c).suffix.lower().replace(".", "") or "png"
            with open(c, "rb") as f:
                return f"data:image/{s};base64,{base64.b64encode(f.read()).decode('utf-8')}"
    return ""

def find_page(base_name: str) -> str:
    for n in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{n}.py")
        if p.exists():
            return str(p)
    return ""

def clean_text(text: Any) -> str:
    if text is None:
        return ""
    t = str(text)
    for old, new in {"\u2013": "-", "\u2014": "-", "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u2026": "...", "\u2022": "-"}.items():
        t = t.replace(old, new)
    return t

def safe_get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else default

def nb_value(v: Any) -> str:
    if v is None or v == "":
        return "-"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else (f"{v:.1f}" if abs(v) >= 10 else f"{v:.2f}".rstrip("0").rstrip(".")).replace(".", ",")
    return str(v)

def sanitize_filename(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^\w.\-]", "_", ascii_name).strip("_") or "file"

def extract_json_blob(text: str) -> str:
    if not text:
        return ""
    c = re.sub(r"^```json", "", text.strip(), flags=re.I).strip()
    c = re.sub(r"^```", "", c).strip()
    c = re.sub(r"```$", "", c).strip()
    f, l = c.find("{"), c.rfind("}")
    if f != -1 and l > f:
        c = c[f: l + 1]
    return re.sub(r",\s*([}\]])", r"\1", c)

def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    blob = extract_json_blob(text)
    if not blob:
        return None
    try:
        return json.loads(blob)
    except Exception:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", re.sub(r"(?<!\\)'", '"', blob.replace("\t", " "))))
        except Exception:
            return None

def list_to_dataframe(items: Any, columns: List[str]) -> pd.DataFrame:
    if not items:
        return pd.DataFrame(columns=columns)
    if isinstance(items, list):
        rows = []
        for item in items:
            if isinstance(item, dict):
                rows.append({c: item.get(c, "") for c in columns})
            elif isinstance(item, (list, tuple)):
                rows.append({c: item[i] if i < len(item) else "" for i, c in enumerate(columns)})
        return pd.DataFrame(rows, columns=columns)
    return pd.DataFrame(columns=columns)

def fmt_nok(v: Any) -> str:
    try:
        n = int(float(v))
        return f"{n:,}".replace(",", " ")
    except Exception:
        return str(v)


# ────────────────────────────────────────────────────────────────
# 3. MULTI-LANGUAGE SYSTEM
# ────────────────────────────────────────────────────────────────
LANG_LABELS = {
    "NO": {
        "lang_name": "Norsk",
        "page_title": "Teknisk Due Diligence",
        "hero_title": "Fa oversikt over teknisk tilstand, risiko og vedlikeholdsbehov.",
        "hero_subtitle": "Last opp tegninger, ferdigattest, tilstandsrapport, energimerke og FDV. Du far en strukturert gjennomgang med tilstandsgrader, regelverksavvik, estimert utbedringskost og komplett dokumentoversikt.",
        "back_btn": "\u2190 Tilbake til prosjekt",
        "section_upload": "Last opp dokumentasjon",
        "section_upload_desc": "Last opp alt tilgjengelig underlag for eiendommen. Jo mer dokumentasjon, desto bedre analyse.",
        "delivery_level": "Leveranseniva",
        "transaction_stage": "Brukssituasjon",
        "property_type": "Eiendomstype",
        "build_year": "Byggar",
        "market_value": "Estimert markedsverdi (MNOK)",
        "include_portfolio": "Inkluder i portefoljeanalyse",
        "notes_label": "Er det noe spesielt du onsker at analysen skal fokusere pa?",
        "notes_default": "Jeg onsker vurdering av tilstandsgrad per bygningsdel, eventuelle regelverksavvik, estimert utbedringskostnad og en oversikt over hva som mangler av dokumentasjon.",
        "upload_label": "Last opp tegninger, ferdigattest, tilstandsrapport, energimerke, FDV og tidligere rapporter",
        "run_btn": "Kjor TDD-analyse",
        "currency": "NOK",
        "building_code": "TEK17",
        "condition_standard": "NS 3600/3424",
        "tab_summary": "Sammendrag", "tab_documents": "Dokumentoversikt", "tab_building_parts": "Bygningsdeler",
        "tab_risk": "Risikomatrise", "tab_capex": "CAPEX / vedlikehold", "tab_energy": "Energi og miljo",
        "tab_compliance": "Regelverkskontroll", "tab_portfolio": "Portefolje", "tab_audit": "Endringslogg", "tab_export": "Eksport",
        "stages": ["Screening", "Transaksjon", "Bank / kreditt", "Portefolje"],
        "prop_types": ["Bolig", "Kontor", "Handel", "Logistikk", "Kombinert", "Hotell", "Helse/omsorg", "Industri"],
        "metric_completeness": "Datakompletthet", "metric_class": "Samlet klasse",
        "metric_cost": "Utbedringskost", "metric_docs": "Dokumenter",
        "disclaimer_reviewed": "Dette utkastet er ment for fagperson-gjennomgang. Det er ikke signert med ansvarsrett og er ikke juridisk bindende.",
        "disclaimer_auto": "Automatisk nivavurdering – resultatet er et forsteutkast.",
        "disclaimer_attested": "Markert som attestert – krever fortsatt signatur og kontroll for juridisk gyldighet.",
        "report_title": "Teknisk Due Diligence – Rapport",
    },
    "EN": {
        "lang_name": "English",
        "page_title": "Technical Due Diligence",
        "hero_title": "Understand technical condition, risk and maintenance needs before you commit.",
        "hero_subtitle": "Upload drawings, completion certificates, condition reports, energy ratings and O&M documentation. Get a structured assessment with condition grades, code compliance, remediation cost estimates and full document overview.",
        "back_btn": "\u2190 Back to project",
        "section_upload": "Upload documentation",
        "section_upload_desc": "Upload all available documentation for the property. More documentation means better analysis.",
        "delivery_level": "Delivery level",
        "transaction_stage": "Use case",
        "property_type": "Property type",
        "build_year": "Year built",
        "market_value": "Estimated market value (MNOK)",
        "include_portfolio": "Include in portfolio analysis",
        "notes_label": "Is there anything specific you want the analysis to focus on?",
        "notes_default": "I want assessment of condition grade per building element, building code deviations, estimated remediation cost and an overview of missing documentation.",
        "upload_label": "Upload drawings, certificates, condition reports, energy ratings, O&M and previous reports",
        "run_btn": "Run TDD analysis",
        "currency": "NOK",
        "building_code": "TEK17 / local code",
        "condition_standard": "NS 3600/3424",
        "tab_summary": "Summary", "tab_documents": "Documents", "tab_building_parts": "Building parts",
        "tab_risk": "Risk matrix", "tab_capex": "CAPEX / maintenance", "tab_energy": "Energy & environment",
        "tab_compliance": "Code compliance", "tab_portfolio": "Portfolio", "tab_audit": "Audit trail", "tab_export": "Export",
        "stages": ["Screening", "Transaction", "Bank / lending", "Portfolio"],
        "prop_types": ["Residential", "Office", "Retail", "Logistics", "Mixed-use", "Hotel", "Healthcare", "Industrial"],
        "metric_completeness": "Data completeness", "metric_class": "Overall class",
        "metric_cost": "Remediation cost", "metric_docs": "Documents",
        "disclaimer_reviewed": "This draft is intended for professional review. It is not signed with liability and is not legally binding.",
        "disclaimer_auto": "Automatic level assessment – the result is a first draft.",
        "disclaimer_attested": "Marked as attested – still requires signature and review for legal validity.",
        "report_title": "Technical Due Diligence – Report",
    },
    "DE": {
        "lang_name": "Deutsch",
        "page_title": "Technische Due Diligence",
        "hero_title": "Technischen Zustand, Risiken und Instandhaltungsbedarf verstehen.",
        "hero_subtitle": "Laden Sie Zeichnungen, Fertigstellungsbescheinigungen, Zustandsberichte, Energieausweise und Wartungsdokumentationen hoch. Sie erhalten eine strukturierte Bewertung mit Zustandsgraden, Normenabweichungen, geschaetzten Sanierungskosten und vollstaendiger Dokumentenuebersicht.",
        "back_btn": "\u2190 Zurueck zum Projekt",
        "section_upload": "Dokumentation hochladen",
        "section_upload_desc": "Laden Sie alle verfuegbaren Unterlagen fuer die Immobilie hoch. Mehr Dokumentation bedeutet bessere Analyse.",
        "delivery_level": "Lieferniveau",
        "transaction_stage": "Anwendungsfall",
        "property_type": "Immobilientyp",
        "build_year": "Baujahr",
        "market_value": "Geschaetzter Marktwert (MNOK)",
        "include_portfolio": "In Portfolioanalyse einbeziehen",
        "notes_label": "Gibt es etwas Bestimmtes, auf das die Analyse fokussieren soll?",
        "notes_default": "Ich wuensche eine Bewertung des Zustands je Bauteil, Abweichungen von Bauvorschriften, geschaetzte Sanierungskosten und eine Uebersicht fehlender Dokumentation.",
        "upload_label": "Zeichnungen, Bescheinigungen, Zustandsberichte, Energieausweise und fruehere Berichte hochladen",
        "run_btn": "TDD-Analyse starten",
        "currency": "NOK",
        "building_code": "TEK17 / EnEV / GEG",
        "condition_standard": "NS 3600 / DIN 276",
        "tab_summary": "Zusammenfassung", "tab_documents": "Dokumente", "tab_building_parts": "Bauteile",
        "tab_risk": "Risikomatrix", "tab_capex": "CAPEX / Instandhaltung", "tab_energy": "Energie & Umwelt",
        "tab_compliance": "Normenkonformitaet", "tab_portfolio": "Portfolio", "tab_audit": "Aenderungsprotokoll", "tab_export": "Export",
        "stages": ["Screening", "Transaktion", "Bank / Kredit", "Portfolio"],
        "prop_types": ["Wohnen", "Buero", "Handel", "Logistik", "Gemischt", "Hotel", "Gesundheit", "Industrie"],
        "metric_completeness": "Datenvollstaendigkeit", "metric_class": "Gesamtklasse",
        "metric_cost": "Sanierungskosten", "metric_docs": "Dokumente",
        "disclaimer_reviewed": "Dieser Entwurf ist fuer die fachliche Ueberpruefung bestimmt. Er ist nicht haftungsrechtlich unterzeichnet und nicht rechtsverbindlich.",
        "disclaimer_auto": "Automatische Niveaubewertung – das Ergebnis ist ein erster Entwurf.",
        "disclaimer_attested": "Als attestiert markiert – erfordert dennoch Unterschrift und Pruefung fuer Rechtsgueltigkeit.",
        "report_title": "Technische Due Diligence – Bericht",
    },
    "SV": {
        "lang_name": "Svenska",
        "page_title": "Teknisk Due Diligence",
        "hero_title": "Fa oversikt over tekniskt skick, risk och underhallsbehov.",
        "hero_subtitle": "Ladda upp ritningar, slutbevis, tillstandsrapporter, energideklaration och driftdokumentation. Du far en strukturerad genomgang med tillstandsgrader, regelverksavvikelser, uppskattade atgardskosnader och komplett dokumentoversikt.",
        "back_btn": "\u2190 Tillbaka till projekt",
        "section_upload": "Ladda upp dokumentation",
        "section_upload_desc": "Ladda upp all tillganglig dokumentation for fastigheten.",
        "delivery_level": "Leveransniva",
        "transaction_stage": "Anvandningsfall",
        "property_type": "Fastighetstyp",
        "build_year": "Byggar",
        "market_value": "Uppskattat marknadsvarde (MNOK)",
        "include_portfolio": "Inkludera i portfoljanalys",
        "notes_label": "Finns det nagot specifikt du vill att analysen ska fokusera pa?",
        "notes_default": "Jag onskar bedomning av tillstandsgrad per byggnadsdel, regelverksavvikelser, uppskattade atgardskostnader och en oversikt over saknad dokumentation.",
        "upload_label": "Ladda upp ritningar, intyg, tillstandsrapporter, energideklaration och tidigare rapporter",
        "run_btn": "Kor TDD-analys",
        "currency": "NOK",
        "building_code": "BBR / TEK17",
        "condition_standard": "NS 3600 / SS",
        "tab_summary": "Sammanfattning", "tab_documents": "Dokument", "tab_building_parts": "Byggnadsdelar",
        "tab_risk": "Riskmatris", "tab_capex": "CAPEX / underhall", "tab_energy": "Energi & miljo",
        "tab_compliance": "Regelverkskontroll", "tab_portfolio": "Portfolj", "tab_audit": "Andringslogg", "tab_export": "Export",
        "stages": ["Screening", "Transaktion", "Bank / kredit", "Portfolj"],
        "prop_types": ["Bostad", "Kontor", "Handel", "Logistik", "Blandat", "Hotell", "Vard", "Industri"],
        "metric_completeness": "Datakompletthet", "metric_class": "Samlad klass",
        "metric_cost": "Atgardskostnad", "metric_docs": "Dokument",
        "disclaimer_reviewed": "Detta utkast ar avsett for fackmannagenomgang. Det ar inte undertecknat med ansvar och ar inte juridiskt bindande.",
        "disclaimer_auto": "Automatisk nivabedomning – resultatet ar ett forstautkast.",
        "disclaimer_attested": "Markerat som attesterat – kraver fortfarande underskrift och kontroll.",
        "report_title": "Teknisk Due Diligence – Rapport",
    },
    "DA": {
        "lang_name": "Dansk",
        "page_title": "Teknisk Due Diligence",
        "hero_title": "Fa overblik over teknisk tilstand, risiko og vedligeholdelsesbehov.",
        "hero_subtitle": "Upload tegninger, ibrugtagningstilladelse, tilstandsrapport, energimaerke og driftdokumentation. Du far en struktureret gennemgang med tilstandsgrader, regelafvigelser, estimeret udbedringspris og komplet dokumentoversigt.",
        "back_btn": "\u2190 Tilbage til projekt",
        "section_upload": "Upload dokumentation",
        "section_upload_desc": "Upload al tilgaengelig dokumentation for ejendommen.",
        "delivery_level": "Leveranceniveau",
        "transaction_stage": "Anvendelsessituation",
        "property_type": "Ejendomstype",
        "build_year": "Byggear",
        "market_value": "Estimeret markedsvaerdi (MNOK)",
        "include_portfolio": "Inkluder i portefoljeananalyse",
        "notes_label": "Er der noget specifikt du onsker analysen skal fokusere pa?",
        "notes_default": "Jeg onsker vurdering af tilstandsgrad per bygningsdel, regelafvigelser, estimerede udbedringomkostninger og en oversigt over manglende dokumentation.",
        "upload_label": "Upload tegninger, attester, tilstandsrapporter, energimaerker og tidligere rapporter",
        "run_btn": "Kor TDD-analyse",
        "currency": "NOK",
        "building_code": "BR18 / TEK17",
        "condition_standard": "NS 3600 / DS",
        "tab_summary": "Resumee", "tab_documents": "Dokumenter", "tab_building_parts": "Bygningsdele",
        "tab_risk": "Risikomatrice", "tab_capex": "CAPEX / vedligehold", "tab_energy": "Energi & miljo",
        "tab_compliance": "Regelvaerkskontrol", "tab_portfolio": "Portefolje", "tab_audit": "Aendringslog", "tab_export": "Eksport",
        "stages": ["Screening", "Transaktion", "Bank / kredit", "Portefolje"],
        "prop_types": ["Bolig", "Kontor", "Detail", "Logistik", "Blandet", "Hotel", "Sundhed", "Industri"],
        "metric_completeness": "Datakomplethed", "metric_class": "Samlet klasse",
        "metric_cost": "Udbedringspris", "metric_docs": "Dokumenter",
        "disclaimer_reviewed": "Dette udkast er beregnet til faglig gennemgang. Det er ikke underskrevet med ansvar og er ikke juridisk bindende.",
        "disclaimer_auto": "Automatisk niveauvurdering – resultatet er et forsteutkast.",
        "disclaimer_attested": "Markeret som attesteret – kraever fortsat underskrift og kontrol.",
        "report_title": "Teknisk Due Diligence – Rapport",
    },
}

def L(key: str) -> str:
    lang = st.session_state.get("tdd_lang", "NO")
    return LANG_LABELS.get(lang, LANG_LABELS["NO"]).get(key, LANG_LABELS["NO"].get(key, key))

def L_list(key: str) -> list:
    lang = st.session_state.get("tdd_lang", "NO")
    return LANG_LABELS.get(lang, LANG_LABELS["NO"]).get(key, LANG_LABELS["NO"].get(key, []))


# ────────────────────────────────────────────────────────────────
# 4. PREMIUM CSS
# ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    #MainMenu {visibility: hidden;} footer {visibility: hidden;} header {visibility: hidden;}
    :root { --bg: #06111a; --panel: rgba(10, 22, 35, 0.78); --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df; --accent: #38bdf8; --accent-warm: #f59e0b; }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1320px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    button[kind="primary"], .stFormSubmitButton > button { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover, .stFormSubmitButton > button:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s !important; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important; }
    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, div[data-baseweb="select"] * { background-color: transparent !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; border: none !important; box-shadow: none !important; }
    div[data-baseweb="base-input"]:focus-within, div[data-baseweb="select"] > div:focus-within, .stTextArea > div > div > div:focus-within { border-color: var(--accent) !important; box-shadow: 0 0 0 1px rgba(56, 194, 201, 0.5) !important; }
    ul[data-baseweb="menu"] { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; }
    ul[data-baseweb="menu"] li { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
    ul[data-baseweb="menu"] li:hover { background-color: rgba(56, 194, 201, 0.1) !important; }
    div[data-testid="InputInstructions"], div[data-testid="InputInstructions"] > span { color: #9fb0c3 !important; -webkit-text-fill-color: #9fb0c3 !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label, .stMultiSelect label, .stSlider label, .stCheckbox label, .stRadio label { color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important; }
    .stSlider div[data-baseweb="slider"] div, .stSelectSlider div[data-baseweb="slider"] div { color: #ffffff !important; }
    div[data-testid="stThumbValue"], .stSlider [data-testid="stTickBarMin"], .stSlider [data-testid="stTickBarMax"] { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
    .stCheckbox span, .stToggle span, label[data-baseweb="checkbox"] span { color: #f5f7fb !important; -webkit-text-fill-color: #f5f7fb !important; }
    span[data-baseweb="tag"] { background-color: rgba(56, 194, 201, 0.15) !important; color: #38bdf8 !important; border: 1px solid rgba(56, 194, 201, 0.4) !important; border-radius: 6px !important; }
    span[data-baseweb="tag"] span { color: #38bdf8 !important; -webkit-text-fill-color: #38bdf8 !important; }
    div[data-testid="stExpander"] details, div[data-testid="stExpander"] details summary, div[data-testid="stExpander"] { background-color: #0c1520 !important; color: #f5f7fb !important; border-radius: 12px !important; }
    div[data-testid="stExpander"] details summary:hover { background-color: rgba(255,255,255,0.03) !important; }
    div[data-testid="stExpander"] details summary p { color: #f5f7fb !important; font-weight: 650 !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; margin-bottom: 1rem !important; }
    div[data-testid="stExpanderDetails"] { background: transparent !important; color: #f5f7fb !important; }
    [data-testid="stFileUploaderDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important; border-radius: 12px !important; padding: 2rem !important; }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #38c2c9 !important; background-color: rgba(56, 194, 201, 0.05) !important; }
    [data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
    [data-testid="stFileUploaderFileData"] { background-color: rgba(255,255,255,0.02) !important; color: #f5f7fb !important; border-radius: 8px !important; }
    [data-testid="stAlert"] { background-color: rgba(56, 194, 201, 0.05) !important; border: 1px solid rgba(56, 194, 201, 0.2) !important; border-radius: 12px !important; }
    [data-testid="stAlert"] * { color: #f5f7fb !important; }
    .stTabs [data-baseweb="tab-list"] { gap: 4px; background-color: rgba(10, 22, 35, 0.5); border-radius: 12px; padding: 4px; }
    .stTabs [data-baseweb="tab"] { background-color: transparent !important; color: #9fb0c3 !important; border-radius: 8px !important; padding: 8px 14px !important; font-weight: 600 !important; border: none !important; }
    .stTabs [data-baseweb="tab"]:hover { background-color: rgba(56, 194, 201, 0.08) !important; color: #f5f7fb !important; }
    .stTabs [aria-selected="true"] { background-color: rgba(56, 194, 201, 0.15) !important; color: #38bdf8 !important; }
    .stTabs [data-baseweb="tab-highlight"] { background-color: #38bdf8 !important; }
    .stTabs [data-baseweb="tab-border"] { display: none !important; }
    .stDataFrame, [data-testid="stDataFrame"] { border: 1px solid rgba(120,145,170,0.2) !important; border-radius: 12px !important; overflow: hidden !important; }
    .stDownloadButton > button { background-color: rgba(255,255,255,0.04) !important; color: #c8d3df !important; border: 1px solid rgba(120,145,170,0.25) !important; border-radius: 10px !important; font-weight: 600 !important; transition: all 0.2s !important; }
    .stDownloadButton > button:hover { background-color: rgba(56,194,201,0.08) !important; border-color: #38bdf8 !important; color: #38bdf8 !important; }
    .stNumberInput button { color: #c8d3df !important; background-color: rgba(255,255,255,0.05) !important; border-color: rgba(120,145,170,0.3) !important; }
    .stMarkdown, .stMarkdown p, .stMarkdown li, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 { color: #f5f7fb !important; }
    .stMarkdown code { color: #38bdf8 !important; background-color: rgba(56,194,201,0.1) !important; }
    .stCaption, small { color: #9fb0c3 !important; }
    [data-testid="stMetric"], [data-testid="stMetricValue"], [data-testid="stMetricLabel"], [data-testid="stMetricDelta"] { color: #f5f7fb !important; }
    .hero-card { background: linear-gradient(135deg, rgba(10,22,35,0.95), rgba(16,32,50,0.95)); border: 1px solid rgba(120,145,170,0.15); border-radius: 20px; padding: 2.5rem 2.8rem 2rem; margin-bottom: 2rem; }
    .hero-eyebrow { font-size: 0.78rem; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: #38bdf8; margin-bottom: 0.4rem; }
    .hero-title { font-size: 1.85rem; font-weight: 800; line-height: 1.2; color: #f5f7fb; margin-bottom: 0.75rem; }
    .hero-subtitle { font-size: 0.98rem; color: #9fb0c3; line-height: 1.6; max-width: 750px; }
    .hero-pills { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 1.1rem; }
    .hero-pill { background: rgba(56, 194, 201, 0.08); border: 1px solid rgba(56, 194, 201, 0.25); border-radius: 20px; padding: 4px 14px; font-size: 0.8rem; font-weight: 600; color: #38bdf8; }
    .metric-row { display: flex; gap: 16px; margin-bottom: 1.5rem; }
    .metric-card { flex: 1; background: rgba(10, 22, 35, 0.6); border: 1px solid rgba(120,145,170,0.18); border-radius: 14px; padding: 1.1rem 1.3rem; }
    .metric-card .mc-value { font-size: 1.6rem; font-weight: 800; color: #38bdf8; margin-bottom: 2px; }
    .metric-card .mc-label { font-size: 0.82rem; font-weight: 700; color: #c8d3df; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 4px; }
    .metric-card .mc-desc { font-size: 0.78rem; color: #9fb0c3; line-height: 1.4; }
    .section-header { margin-top: 2.5rem; margin-bottom: 1rem; }
    .section-header h3 { color: #f5f7fb !important; font-weight: 750 !important; font-size: 1.2rem !important; margin-bottom: 4px !important; }
    .section-header p { color: #9fb0c3 !important; font-size: 0.9rem !important; }
    .section-badge { display: inline-block; background: rgba(56,194,201,0.1); border: 1px solid rgba(56,194,201,0.25); border-radius: 6px; padding: 1px 8px; font-size: 0.7rem; font-weight: 700; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }
    .snapshot-card { background: rgba(10, 22, 35, 0.6); border: 1px solid rgba(120,145,170,0.18); border-radius: 14px; padding: 1.2rem 1.4rem; margin-bottom: 1rem; }
    .snapshot-card .sc-badge { display: inline-block; background: rgba(56,194,201,0.1); border: 1px solid rgba(56,194,201,0.2); border-radius: 5px; padding: 1px 8px; font-size: 0.68rem; font-weight: 700; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }
    .snapshot-card .sc-name { font-size: 1.05rem; font-weight: 750; color: #f5f7fb; margin-bottom: 3px; }
    .snapshot-card .sc-row { font-size: 0.82rem; color: #9fb0c3; margin-bottom: 1px; }
    .disclaimer-banner { background: rgba(245, 158, 11, 0.06); border: 1px solid rgba(245,158,11,0.25); border-radius: 12px; padding: 0.9rem 1.3rem; margin-bottom: 1.5rem; }
    .disclaimer-banner .db-title { font-size: 0.88rem; font-weight: 700; color: #f59e0b; }
    .disclaimer-banner .db-text { font-size: 0.82rem; color: #c8a94e; margin-top: 2px; }
    .panel-box { background: rgba(10, 22, 35, 0.5); border: 1px solid rgba(120,145,170,0.15); border-radius: 16px; padding: 1.5rem 1.8rem; margin-top: 1rem; margin-bottom: 1rem; }
    .panel-box h4 { color: #f5f7fb !important; font-weight: 750 !important; margin-bottom: 0.4rem !important; }
    .panel-box p, .panel-box li { color: #9fb0c3 !important; font-size: 0.9rem !important; line-height: 1.55 !important; }
</style>
""", unsafe_allow_html=True)


# ────────────────────────────────────────────────────────────────
# 5. SESSION STATE / PROJECT
# ────────────────────────────────────────────────────────────────
DEFAULT_PROJECT = {"p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": "", "b_type": "Næring / Kontor", "etasjer": 1, "bta": 0, "land": "Norge"}
if "project_data" not in st.session_state or not st.session_state.project_data.get("p_name"):
    if SSOT_FILE.exists():
        try:
            with open(SSOT_FILE, "r", encoding="utf-8") as f:
                st.session_state.project_data = json.load(f)
        except Exception:
            st.session_state.project_data = DEFAULT_PROJECT.copy()
    else:
        st.session_state.project_data = DEFAULT_PROJECT.copy()
pd_state = st.session_state.project_data


# ────────────────────────────────────────────────────────────────
# 6. AI BACKEND
# ────────────────────────────────────────────────────────────────
google_key = os.environ.get("GOOGLE_API_KEY", "").strip()
openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
HAS_GEMINI = bool(google_key and genai is not None)
HAS_OPENAI = bool(openai_key and OpenAI is not None)
if HAS_GEMINI:
    try:
        genai.configure(api_key=google_key)
    except Exception:
        HAS_GEMINI = False
HAS_AI = HAS_GEMINI or HAS_OPENAI
_OAI_CLIENT = None

def _get_openai():
    global _OAI_CLIENT
    if _OAI_CLIENT is None and HAS_OPENAI and OpenAI is not None:
        try:
            _OAI_CLIENT = OpenAI(api_key=openai_key)
        except Exception:
            pass
    return _OAI_CLIENT

def generate_text_ai(prompt: str, temperature: float = 0.12) -> str:
    if HAS_OPENAI:
        client = _get_openai()
        if client:
            try:
                mn = clean_text(os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini").strip() or "gpt-4.1-mini"
                resp = client.responses.create(model=mn, input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}], temperature=temperature)
                return clean_text(getattr(resp, "output_text", "")).strip()
            except Exception:
                pass
    if HAS_GEMINI and genai is not None:
        try:
            ms = [m.name for m in genai.list_models() if "generateContent" in getattr(m, "supported_generation_methods", [])]
            resp = genai.GenerativeModel(ms[0] if ms else "models/gemini-1.5-flash").generate_content(prompt, generation_config={"temperature": temperature})
            return clean_text(getattr(resp, "text", "")).strip()
        except Exception:
            pass
    return ""


# ────────────────────────────────────────────────────────────────
# 7. DOCUMENT & RULES ENGINE
# ────────────────────────────────────────────────────────────────
TDD_CATEGORIES = {
    "tilstandsrapport": ["tilstand", "condition", "zustand", "tillstand"],
    "tegning": ["tegning", "drawing", "zeichnung", "ritning", "plan", "snitt", "fasade"],
    "ferdigattest": ["ferdigattest", "completion", "brukstillatelse", "ibrugtagningstilladelse", "slutbevis"],
    "energimerke": ["energi", "energy", "energieausweis", "energideklaration"],
    "fdv": ["fdv", "drift", "vedlikehold", "maintenance", "o&m", "wartung"],
    "brann": ["brann", "fire", "brand", "feuer"],
    "miljo": ["miljo", "environment", "umwelt"],
    "kontrakt": ["kontrakt", "contract", "avtale", "vertrag"],
    "offentlig": ["matrikkel", "regulering", "cadaster", "zoning"],
}

EXPECTED_TDD_DOCS = ["tilstandsrapport", "tegning", "ferdigattest", "energimerke", "fdv"]

BUILDING_PARTS = [
    {"part": "Tak og taktekking", "typical_life_years": 30, "cost_factor": 0.08},
    {"part": "Fasade og kledning", "typical_life_years": 40, "cost_factor": 0.10},
    {"part": "Vinduer og dorer", "typical_life_years": 30, "cost_factor": 0.06},
    {"part": "VVS-anlegg", "typical_life_years": 25, "cost_factor": 0.07},
    {"part": "Elektrisk anlegg", "typical_life_years": 30, "cost_factor": 0.05},
    {"part": "Ventilasjon", "typical_life_years": 20, "cost_factor": 0.06},
    {"part": "Heis", "typical_life_years": 25, "cost_factor": 0.04},
    {"part": "Baeresystem / fundament", "typical_life_years": 80, "cost_factor": 0.15},
    {"part": "Brannsikring", "typical_life_years": 15, "cost_factor": 0.03},
    {"part": "Innvendig overflate", "typical_life_years": 15, "cost_factor": 0.04},
    {"part": "Utomhus / parkering", "typical_life_years": 25, "cost_factor": 0.03},
    {"part": "Energisystem / SD", "typical_life_years": 15, "cost_factor": 0.04},
]

def classify_tdd_file(name: str) -> str:
    low = name.lower()
    for cat, keywords in TDD_CATEGORIES.items():
        if any(kw in low for kw in keywords):
            return cat
    return "annet"

def normalize_uploaded_files(files) -> List[Dict[str, Any]]:
    records = []
    if not files:
        return records
    for f in files:
        name = getattr(f, "name", "ukjent_fil")
        size = getattr(f, "size", 0)
        records.append({
            "filename": name, "safe_name": sanitize_filename(name),
            "category": classify_tdd_file(name), "extension": Path(name).suffix.lower(),
            "size_kb": round(size / 1024, 1) if size else 0,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    return records

def build_tdd_rules(records: List[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    build_year = config.get("build_year", 2000)
    market_value = config.get("market_value_mnok", 100)
    prop_type = config.get("property_type", "Kontor")
    age = max(0, datetime.now().year - build_year)

    found_cats = set(r["category"] for r in records)
    missing = [c for c in EXPECTED_TDD_DOCS if c not in found_cats]
    completeness = max(0.0, min(1.0, (len(EXPECTED_TDD_DOCS) - len(missing)) / max(len(EXPECTED_TDD_DOCS), 1)))

    # Building parts assessment
    building_parts = []
    total_remediation = 0
    for bp in BUILDING_PARTS:
        remaining = max(0, bp["typical_life_years"] - age)
        age_ratio = min(1.0, age / max(bp["typical_life_years"], 1))
        if age_ratio < 0.3:
            tg = "TG0"
        elif age_ratio < 0.6:
            tg = "TG1"
        elif age_ratio < 0.85:
            tg = "TG2"
        else:
            tg = "TG3"
        cost_est = int(market_value * 1_000_000 * bp["cost_factor"] * max(0, age_ratio - 0.4))
        total_remediation += cost_est
        building_parts.append({
            "part": bp["part"], "tg": tg, "remaining_life_years": remaining,
            "remediation_cost_nok": cost_est,
            "reason": f"Alder {age} ar, typisk levetid {bp['typical_life_years']} ar.",
            "source": "Regelmotor",
        })

    # Risk classification
    if age > 50 or total_remediation > market_value * 1_000_000 * 0.15:
        overall_class = "C (High risk)"
    elif age > 25 or total_remediation > market_value * 1_000_000 * 0.08:
        overall_class = "B (Medium risk)"
    else:
        overall_class = "A (Low risk)"

    tech_risk = "HIGH" if age > 40 else "MEDIUM" if age > 20 else "LOW"
    fin_risk = "HIGH" if total_remediation > market_value * 500_000 else "MEDIUM" if total_remediation > market_value * 200_000 else "LOW"
    reg_risk = "HIGH" if build_year < 1997 else "MEDIUM" if build_year < 2010 else "LOW"

    risk_matrix = {
        "technical_risk": tech_risk, "financial_risk": fin_risk, "regulatory_risk": reg_risk,
        "overall_class": overall_class, "remediation_cost_total_nok": total_remediation,
    }

    # CAPEX forecast (10-year)
    capex_rows = []
    for year_offset in range(1, 11):
        year = datetime.now().year + year_offset
        yearly_cost = 0
        items = []
        for bp in BUILDING_PARTS:
            remaining = max(0, bp["typical_life_years"] - (age + year_offset))
            if remaining <= 3 and remaining >= 0:
                cost = int(market_value * 1_000_000 * bp["cost_factor"] * 0.5)
                yearly_cost += cost
                items.append(bp["part"])
        capex_rows.append({"year": year, "estimated_cost_nok": yearly_cost, "items": ", ".join(items) if items else "-"})

    # TEK17 / code compliance
    tek_deviations = []
    if build_year < 1997:
        tek_deviations.append({"title": "Brannsikkerhet for TEK97", "category": "KRITISK", "recommendation": "Krever full branntilstandsanalyse.", "source": "Regelmotor"})
    if build_year < 2007:
        tek_deviations.append({"title": "Energikrav for TEK07", "category": "VESENTLIG", "recommendation": "Vurder energioppgradering av klimaskjerm.", "source": "Regelmotor"})
    if build_year < 2010:
        tek_deviations.append({"title": "Universell utforming for TEK10", "category": "VESENTLIG", "recommendation": "Kartlegg tilgjengelighet og UU-avvik.", "source": "Regelmotor"})
    if build_year < 2017:
        tek_deviations.append({"title": "Energiforsyning TEK17", "category": "MIDDELS", "recommendation": "Vurder fornybar energi og fjernvarmetilkobling.", "source": "Regelmotor"})

    # Energy assessment
    energy_class_estimate = "A" if build_year >= 2020 else "B" if build_year >= 2012 else "C" if build_year >= 2007 else "D" if build_year >= 1997 else "E" if build_year >= 1980 else "F"
    energy_data = {
        "estimated_class": energy_class_estimate,
        "build_year": build_year,
        "estimated_kwh_per_m2": 80 if energy_class_estimate <= "B" else 120 if energy_class_estimate <= "C" else 160 if energy_class_estimate <= "D" else 200,
        "eu_taxonomy_aligned": energy_class_estimate <= "B",
        "recommendations": [],
    }
    if energy_class_estimate >= "D":
        energy_data["recommendations"].append("Etterisolering av fasade og tak anbefales.")
    if energy_class_estimate >= "E":
        energy_data["recommendations"].append("Vindusutskifting til 3-lags energiglass anbefales.")
    if not energy_data["eu_taxonomy_aligned"]:
        energy_data["recommendations"].append("Bygget oppfyller ikke EU-taksonomikravet for naer-nullenergibygg.")

    return {
        "building_parts": building_parts, "risk_matrix": risk_matrix,
        "missing_categories": missing, "data_completeness_score": completeness,
        "tek17_deviations": tek_deviations, "capex_forecast": capex_rows,
        "energy_data": energy_data, "total_remediation_nok": total_remediation,
    }


# ────────────────────────────────────────────────────────────────
# 8. AI ANALYSIS
# ────────────────────────────────────────────────────────────────
def run_ai_tdd(records, config, rules) -> Dict[str, Any]:
    if not HAS_AI:
        return {"data": None, "attempt_log": [{"step": "AI", "status": "No AI backend available."}]}
    manifest = "\n".join(f"- {r['filename']} ({r['category']}, {r['size_kb']} KB)" for r in records)
    bp_text = "\n".join(f"- {b['part']}: {b['tg']}, remaining {b['remaining_life_years']}yr, cost {fmt_nok(b['remediation_cost_nok'])} NOK" for b in rules.get("building_parts", []))
    prompt = f"""You are Builtly TDD AI. Analyse a technical due diligence for a property and return a structured assessment.

PROJECT: {clean_text(pd_state.get('p_name','Unknown'))} | Type: {config.get('property_type','-')} | Built: {config.get('build_year','-')} | Value: {config.get('market_value_mnok','-')} MNOK
Location: {clean_text(pd_state.get('adresse',''))}, {clean_text(pd_state.get('kommune',''))}
Notes: {config.get('notes','')}

DOCUMENTS:\n{manifest or 'None uploaded.'}

BUILDING PARTS ASSESSMENT:\n{bp_text}

MISSING DOC CATEGORIES: {', '.join(rules.get('missing_categories',[])) or 'None'}

Return ONLY valid JSON:
{{
  "executive_summary": "3-5 sentence summary of the property's technical condition and key risks.",
  "next_actions": [{{"action":"...","owner":"...","priority":"HIGH|MEDIUM|LOW","why":"..."}}],
  "gaps": [{{"value":"Missing document or information"}}],
  "building_parts_ai": [{{"part":"...","tg":"TG0-TG3","comment":"..."}}],
  "tek17_deviations_ai": [{{"title":"...","category":"KRITISK|VESENTLIG|MIDDELS","recommendation":"..."}}],
  "insurance_notes": ["Risk factor for insurance assessment"],
  "bank_lending_notes": ["Observation relevant for lending decision"],
  "environmental_flags": ["Environmental concern or opportunity"]
}}
Return only JSON."""

    log = []
    try:
        raw = generate_text_ai(prompt, 0.1)
        log.append({"step": "AI call", "status": "OK", "length": len(raw)})
        parsed = safe_json_loads(raw)
        if parsed:
            log.append({"step": "JSON parse", "status": "OK"})
            return {"data": parsed, "attempt_log": log}
        log.append({"step": "JSON parse", "status": "Failed"})
    except Exception as e:
        log.append({"step": "AI call", "status": f"Error: {type(e).__name__}"})
    return {"data": None, "attempt_log": log}


# ────────────────────────────────────────────────────────────────
# 9. PDF REPORT
# ────────────────────────────────────────────────────────────────
def build_tdd_pdf(records, rules, config, ai_result) -> Optional[bytes]:
    if FPDF is None:
        return None

    class TDDPDF(FPDF):
        def header(self):
            if self.page_no() == 1: return
            self.set_y(11); self.set_text_color(88, 94, 102); self.set_font("Helvetica", "", 8)
            self.cell(0, 4, clean_text(f"TDD – {pd_state.get('p_name','Prosjekt')}"), 0, 0, "L")
            self.cell(0, 4, datetime.now().strftime("%d.%m.%Y"), 0, 1, "R")
            self.set_draw_color(188, 192, 197); self.line(18, 18, 192, 18); self.set_y(24)
        def footer(self):
            self.set_y(-12); self.set_draw_color(210, 214, 220); self.line(18, 285, 192, 285)
            self.set_font("Helvetica", "", 7); self.set_text_color(110, 114, 119)
            self.cell(60, 5, "Builtly-TDD-001", 0, 0, "L")
            self.cell(70, 5, clean_text("Draft - requires professional review"), 0, 0, "C")
            self.cell(0, 5, clean_text(f"Page {self.page_no()}"), 0, 0, "R")
        def ensure_space(self, h):
            if self.get_y() + h > 272: self.add_page()
        def section_title(self, title):
            self.ensure_space(20); self.ln(2); self.set_font("Helvetica", "B", 17); self.set_text_color(36, 50, 72); self.set_x(20)
            self.multi_cell(170, 8, clean_text(title.upper()), 0, "L")
            self.set_draw_color(204, 209, 216); self.line(20, self.get_y() + 1, 190, self.get_y() + 1); self.ln(5)
        def body_text(self, text):
            if not text: return
            self.set_x(20); self.set_font("Helvetica", "", 10.2); self.set_text_color(35, 39, 43)
            self.multi_cell(170, 5.5, clean_text(text)); self.ln(1.6)
        def bullet_list(self, items):
            for item in items:
                if not item: continue
                self.ensure_space(10); self.set_font("Helvetica", "", 10.1); self.set_text_color(35, 39, 43)
                y = self.get_y(); self.set_xy(22, y); self.cell(6, 5.2, "-", 0, 0, "L"); self.set_xy(28, y)
                self.multi_cell(162, 5.2, clean_text(item)); self.ln(0.8)
        def kv_card(self, items, x=None, width=80, title=None):
            if x is None: x = self.get_x()
            height = 10 + (len(items) * 6.3) + (7 if title else 0); self.ensure_space(height + 3); sy = self.get_y()
            self.set_fill_color(245, 247, 249); self.set_draw_color(214, 219, 225); self.rect(x, sy, width, height, "DF")
            yy = sy + 5
            if title:
                self.set_xy(x + 4, yy); self.set_font("Helvetica", "B", 10); self.set_text_color(48, 64, 86)
                self.cell(width - 8, 5, clean_text(title.upper()), 0, 1); yy += 7
            for label, value in items:
                self.set_xy(x + 4, yy); self.set_font("Helvetica", "B", 8.6); self.set_text_color(72, 79, 87)
                self.cell(28, 5, clean_text(label), 0, 0); self.set_font("Helvetica", "", 8.6); self.set_text_color(35, 39, 43)
                self.multi_cell(width - 34, 5, clean_text(value)); yy = self.get_y() + 1
            self.set_y(max(self.get_y(), sy + height))
        def highlight_box(self, title, items, fill=(245, 247, 250), accent=(50, 77, 106)):
            total_h = 14 + sum(8 for _ in items); self.ensure_space(total_h + 5); x, y = 20, self.get_y()
            self.set_fill_color(*fill); self.set_draw_color(217, 223, 230); self.rect(x, y, 170, total_h, "DF")
            self.set_fill_color(*accent); self.rect(x, y, 3, total_h, "F"); self.set_xy(x + 6, y + 4)
            self.set_font("Helvetica", "B", 10.5); self.set_text_color(*accent); self.cell(0, 5, clean_text(title.upper()), 0, 1)
            self.set_text_color(35, 39, 43); self.set_font("Helvetica", "", 10); yy = y + 10
            for item in items:
                self.set_xy(x + 8, yy); self.cell(5, 5, "-", 0, 0); self.multi_cell(154, 5.2, clean_text(item)); yy = self.get_y() + 2
            self.set_y(y + total_h + 3)

    pdf = TDDPDF("P", "mm", "A4"); pdf.set_auto_page_break(True, 15)

    # Cover
    pdf.add_page()
    if os.path.exists("logo.png"):
        try: pdf.image("logo.png", x=150, y=15, w=40)
        except Exception: pass
    pdf.set_xy(20, 45); pdf.set_font("Helvetica", "B", 11); pdf.set_text_color(100, 105, 110); pdf.cell(80, 6, L("page_title").upper(), 0, 1, "L")
    pdf.set_x(20); pdf.set_font("Helvetica", "B", 30); pdf.set_text_color(20, 28, 38); pdf.multi_cell(95, 11, clean_text(pd_state.get("p_name", "Prosjekt")), 0, "L")
    pdf.ln(4); pdf.set_x(20); pdf.set_font("Helvetica", "B", 13); pdf.set_text_color(64, 68, 74)
    pdf.multi_cell(95, 6.5, "Tilstandsvurdering, risiko, regelverksavvik, CAPEX og dokumentkontroll", 0, "L")
    pdf.set_xy(118, 45); pdf.kv_card([
        ("Oppdragsgiver", clean_text(pd_state.get("c_name", "-"))), ("Emne", "TDD"),
        ("Dato / rev", f"{datetime.now().strftime('%d.%m.%Y')} / 01"), ("Kode", "Builtly-TDD-001"),
        ("Byggar", str(config.get("build_year", "-"))), ("Verdi", f"{nb_value(config.get('market_value_mnok', 0))} MNOK"),
        ("Type", config.get("property_type", "-")), ("Klasse", rules.get("risk_matrix", {}).get("overall_class", "-")),
    ], x=118, width=72)

    # TOC
    pdf.add_page(); pdf.section_title("Innholdsfortegnelse")
    for item in ["1. Sammendrag", "2. Bygningsdeler og tilstand", "3. Risikomatrise", "4. Regelverksavvik", "5. CAPEX-prognose (10 ar)", "6. Energi og miljo", "7. Dokumentoversikt", "8. Anbefalte tiltak"]:
        y = pdf.get_y(); pdf.set_x(22); pdf.set_font("Helvetica", "", 10.5); pdf.set_text_color(45, 49, 55)
        pdf.cell(0, 6, clean_text(item), 0, 0, "L"); pdf.set_draw_color(225, 229, 234); pdf.line(22, y + 6, 188, y + 6); pdf.ln(8)

    ai_data = safe_get(ai_result, "data") if isinstance(ai_result, dict) else None

    # 1. Summary
    pdf.add_page(); pdf.section_title("1. Sammendrag")
    summary = safe_get(ai_data, "executive_summary", "") if isinstance(ai_data, dict) else ""
    pdf.body_text(summary or f"Eiendommen er bygget i {config.get('build_year','-')}. Samlet risikoklassifisering: {rules.get('risk_matrix',{}).get('overall_class','-')}. Estimert utbedringskostnad: {fmt_nok(rules.get('total_remediation_nok', 0))} NOK.")
    rm = rules.get("risk_matrix", {})
    pdf.highlight_box("Nokkeltall", [
        f"Risikoklasse: {rm.get('overall_class', '-')}",
        f"Teknisk risiko: {rm.get('technical_risk', '-')}",
        f"Finansiell risiko: {rm.get('financial_risk', '-')}",
        f"Regulatorisk risiko: {rm.get('regulatory_risk', '-')}",
        f"Utbedringskostnad: {fmt_nok(rm.get('remediation_cost_total_nok', 0))} NOK",
        f"Datakompletthet: {int(rules.get('data_completeness_score', 0) * 100)}%",
    ])

    # 2. Building parts
    pdf.section_title("2. Bygningsdeler og tilstand")
    for bp in rules.get("building_parts", []):
        pdf.ensure_space(12)
        pdf.body_text(f"{bp['part']}: {bp['tg']} | Restlevetid: {bp['remaining_life_years']} ar | Kost: {fmt_nok(bp['remediation_cost_nok'])} NOK")

    # 3. Risk matrix
    pdf.add_page(); pdf.section_title("3. Risikomatrise")
    pdf.body_text(f"Teknisk: {rm.get('technical_risk','-')} | Finansiell: {rm.get('financial_risk','-')} | Regulatorisk: {rm.get('regulatory_risk','-')}")
    pdf.body_text(f"Samlet klasse: {rm.get('overall_class','-')}")

    # 4. Code compliance
    pdf.section_title("4. Regelverksavvik")
    for d in rules.get("tek17_deviations", []):
        pdf.ensure_space(10); pdf.body_text(f"[{d.get('category','')}] {d.get('title','')}: {d.get('recommendation','')}")

    # 5. CAPEX
    pdf.add_page(); pdf.section_title("5. CAPEX-prognose (10 ar)")
    for row in rules.get("capex_forecast", []):
        if row.get("estimated_cost_nok", 0) > 0:
            pdf.body_text(f"{row['year']}: {fmt_nok(row['estimated_cost_nok'])} NOK — {row.get('items', '-')}")

    # 6. Energy
    pdf.section_title("6. Energi og miljo")
    ed = rules.get("energy_data", {})
    pdf.body_text(f"Estimert energiklasse: {ed.get('estimated_class','-')} | {ed.get('estimated_kwh_per_m2','-')} kWh/m2")
    pdf.body_text(f"EU-taksonomi: {'Ja' if ed.get('eu_taxonomy_aligned') else 'Nei'}")
    pdf.bullet_list(ed.get("recommendations", []))

    # 7. Documents
    pdf.section_title("7. Dokumentoversikt")
    pdf.bullet_list([f"{r['filename']} — {r['category']} ({r['size_kb']} KB)" for r in records])
    if rules.get("missing_categories"):
        pdf.highlight_box("Manglende dokumentkategorier", rules["missing_categories"], fill=(255, 248, 235), accent=(180, 130, 40))

    # 8. Actions
    pdf.section_title("8. Anbefalte tiltak")
    actions = safe_get(ai_data, "next_actions", []) if isinstance(ai_data, dict) else []
    if actions:
        pdf.bullet_list([f"[{a.get('priority','')}] {a.get('action','')}: {a.get('why','')}" for a in actions if isinstance(a, dict)])
    else:
        pdf.body_text("Kjor analyse med dokumenter for a generere anbefalinger.")

    # Disclaimer
    pdf.ln(8); pdf.highlight_box("Ansvarsfraskrivelse", [
        "Rapporten er et arbeidsutkast generert av Builtly TDD.", "Dokumentet er ikke signert med ansvarsrett.",
        "Resultatet skal fagkontrolleres for bruk i transaksjon eller beslutning.",
    ], fill=(255, 248, 235), accent=(180, 130, 40))

    return bytes(pdf.output())


# ────────────────────────────────────────────────────────────────
# 10. MARKDOWN REPORT
# ────────────────────────────────────────────────────────────────
def build_md_report(records, rules, config, ai_result) -> str:
    ai_data = safe_get(ai_result, "data") if isinstance(ai_result, dict) else None
    rm = rules.get("risk_matrix", {})
    parts = [
        f"# {L('report_title')} – {clean_text(pd_state.get('p_name','Prosjekt'))}",
        f"*Generert: {datetime.now().strftime('%d.%m.%Y %H:%M')}*\n",
        f"## Sammendrag\n{safe_get(ai_data, 'executive_summary', 'Basert pa regelmotor.') if isinstance(ai_data, dict) else 'Basert pa regelmotor.'}\n",
        f"## Nokkeltall\n- Risikoklasse: {rm.get('overall_class','-')}\n- Utbedringskostnad: {fmt_nok(rm.get('remediation_cost_total_nok',0))} NOK\n- Datakompletthet: {int(rules.get('data_completeness_score',0)*100)}%\n",
        "## Bygningsdeler",
    ]
    for bp in rules.get("building_parts", []):
        parts.append(f"- **{bp['part']}**: {bp['tg']} | Restlevetid: {bp['remaining_life_years']} ar | Kost: {fmt_nok(bp['remediation_cost_nok'])} NOK")
    parts.append("\n## Regelverksavvik")
    for d in rules.get("tek17_deviations", []):
        parts.append(f"- [{d.get('category','')}] {d.get('title','')}: {d.get('recommendation','')}")
    parts.append("\n## CAPEX-prognose")
    for row in rules.get("capex_forecast", []):
        if row.get("estimated_cost_nok", 0) > 0:
            parts.append(f"- {row['year']}: {fmt_nok(row['estimated_cost_nok'])} NOK — {row.get('items','-')}")
    parts.append("\n## Energi")
    ed = rules.get("energy_data", {})
    parts.append(f"- Klasse: {ed.get('estimated_class','-')} | {ed.get('estimated_kwh_per_m2','-')} kWh/m2 | EU-taksonomi: {'Ja' if ed.get('eu_taxonomy_aligned') else 'Nei'}")
    parts.append("\n---\n*Rapport generert av Builtly TDD. Utkast – krever faglig gjennomgang.*")
    return "\n".join(parts)


# ════════════════════════════════════════════════════════════════
# UI
# ════════════════════════════════════════════════════════════════

# ── Header + Language + Back ──
top_l, top_m, top_r = st.columns([3, 1, 1])
with top_l:
    logo = logo_data_uri()
    render_html(f'<img src="{logo}" class="brand-logo">' if logo else '<h2 style="margin:0;color:white;">Builtly</h2>')
with top_m:
    if "tdd_lang" not in st.session_state:
        st.session_state.tdd_lang = "NO"
    lang_choice = st.selectbox("Language", list(LANG_LABELS.keys()), format_func=lambda x: LANG_LABELS[x]["lang_name"], index=list(LANG_LABELS.keys()).index(st.session_state.tdd_lang), key="tdd_lang_sel", label_visibility="collapsed")
    st.session_state.tdd_lang = lang_choice
with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button(L("back_btn"), use_container_width=True, type="secondary"):
        t = find_page("Project")
        if t: st.switch_page(t)

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)

if pd_state.get("p_name") in ["", "Nytt Prosjekt", None]:
    st.warning("Set up project data first.")
    if find_page("Project"):
        if st.button("Go to Project Setup", type="primary"): st.switch_page(find_page("Project"))
    st.stop()

# ── Hero ──
render_html(f"""
<div class="hero-card">
    <div class="hero-eyebrow">{L('page_title')}</div>
    <div class="hero-title">{L('hero_title')}</div>
    <div class="hero-subtitle">{L('hero_subtitle')}</div>
    <div class="hero-pills">
        <span class="hero-pill">{L('condition_standard')}</span>
        <span class="hero-pill">{L('building_code')}</span>
        <span class="hero-pill">TG0-3</span>
        <span class="hero-pill">CAPEX</span>
        <span class="hero-pill">EU Taxonomy</span>
    </div>
</div>
""")

# ── Main layout ──
left, right = st.columns([1.35, 0.65], gap="large")

with left:
    render_html(f'<div class="section-header"><span class="section-badge">Input</span><h3>{L("section_upload")}</h3><p>{L("section_upload_desc")}</p></div>')
    c1, c2 = st.columns(2)
    with c1:
        delivery_level = st.selectbox(L("delivery_level"), ["auto", "reviewed", "attested"], index=1)
        transaction_stage = st.selectbox(L("transaction_stage"), L_list("stages"), index=1)
        property_type = st.selectbox(L("property_type"), L_list("prop_types"), index=1)
    with c2:
        build_year = st.number_input(L("build_year"), min_value=1850, max_value=2100, value=2008, step=1)
        market_value_mnok = st.number_input(L("market_value"), min_value=1.0, value=145.0, step=1.0)
        include_portfolio = st.toggle(L("include_portfolio"), value=False)

    notes = st.text_area(L("notes_label"), value=L("notes_default"), height=110)
    uploads = st.file_uploader(L("upload_label"), type=["pdf", "docx", "xlsx", "xls", "csv", "ifc", "dwg", "dxf", "zip"], accept_multiple_files=True, key="tdd_uploads_v3")

    # Sanitized filename notice
    if uploads:
        sanitised = [f for f in uploads if sanitize_filename(f.name) != f.name]
        if sanitised:
            with st.expander("Filnavn tilpasset for opplasting"):
                st.dataframe([{"Original": f.name, "API": sanitize_filename(f.name)} for f in sanitised], use_container_width=True, hide_index=True)

    run_btn = st.button(L("run_btn"), type="primary", use_container_width=True)

# ── Analysis ──
records = normalize_uploaded_files(uploads or [])
config = {"transaction_stage": transaction_stage, "property_type": property_type, "build_year": build_year, "market_value_mnok": market_value_mnok, "include_portfolio": include_portfolio, "notes": notes, "delivery_level": delivery_level}
rules = build_tdd_rules(records, config)

if "tdd_ai_result" not in st.session_state:
    st.session_state.tdd_ai_result = {"data": None, "attempt_log": []}
if run_btn and HAS_AI:
    with st.spinner("Running AI analysis..."):
        st.session_state.tdd_ai_result = run_ai_tdd(records, config, rules)
ai_result = st.session_state.tdd_ai_result
ai_data = safe_get(ai_result, "data") if isinstance(ai_result, dict) else None

# ── Disclaimer ──
disc_key = f"disclaimer_{delivery_level}"
render_html(f'<div class="disclaimer-banner"><div class="db-title">{L("delivery_level")}: {delivery_level}</div><div class="db-text">{L(disc_key)}</div></div>')

# ── Right column ──
with right:
    render_html(f'<div class="snapshot-card"><span class="sc-badge">TDD context</span><div class="sc-name">{clean_text(pd_state.get("p_name","Prosjekt"))}</div><div class="sc-row">Type: {clean_text(pd_state.get("b_type","-"))}</div><div class="sc-row">{clean_text(pd_state.get("adresse",""))}, {clean_text(pd_state.get("kommune",""))}</div><div class="sc-row">GNR/BNR: {pd_state.get("gnr","-")}/{pd_state.get("bnr","-")}</div></div>')
    rm = rules.get("risk_matrix", {})
    render_html(f"""
    <div class="metric-row"><div class="metric-card"><div class="mc-value">{int(rules.get('data_completeness_score',0)*100)}%</div><div class="mc-label">{L('metric_completeness')}</div><div class="mc-desc">TDD document coverage</div></div><div class="metric-card"><div class="mc-value">{rm.get('overall_class','-')}</div><div class="mc-label">{L('metric_class')}</div><div class="mc-desc">Technical + financial + regulatory</div></div></div>
    <div class="metric-row"><div class="metric-card"><div class="mc-value">{fmt_nok(rm.get('remediation_cost_total_nok',0))}</div><div class="mc-label">{L('metric_cost')} ({L('currency')})</div><div class="mc-desc">Estimated total</div></div><div class="metric-card"><div class="mc-value">{len(records)}</div><div class="mc-label">{L('metric_docs')}</div><div class="mc-desc">Uploaded files</div></div></div>
    """)
    with st.expander("Analysis payload"):
        st.json({"delivery_level": delivery_level, "risk_matrix": rm, "completeness": rules.get("data_completeness_score", 0), "energy_class": rules.get("energy_data", {}).get("estimated_class", "-")})

# ── Tabs ──
render_html(f'<div class="section-header"><span class="section-badge">Review</span><h3>{L("tab_summary")} & {L("tab_export")}</h3><p>Full assessment with condition grades, risk matrix, CAPEX forecast, energy analysis and reports.</p></div>')

tabs = st.tabs([L("tab_summary"), L("tab_building_parts"), L("tab_risk"), L("tab_compliance"), L("tab_capex"), L("tab_energy"), L("tab_documents"), L("tab_portfolio"), L("tab_audit"), L("tab_export")])

with tabs[0]:
    st.markdown(f"### {L('tab_summary')}")
    st.write(safe_get(ai_data, "executive_summary", "") if isinstance(ai_data, dict) else "Run the analysis with documents to generate a summary.")
    actions = safe_get(ai_data, "next_actions", []) if isinstance(ai_data, dict) else []
    if actions:
        st.markdown("#### Recommended actions")
        st.dataframe(list_to_dataframe(actions, ["action", "owner", "priority", "why"]), use_container_width=True, hide_index=True)
    gaps = safe_get(ai_data, "gaps", []) if isinstance(ai_data, dict) else []
    if gaps:
        st.markdown("#### Identified gaps")
        st.dataframe(list_to_dataframe(gaps, ["value"]), use_container_width=True, hide_index=True)
    # Bank/insurance notes
    bank_notes = safe_get(ai_data, "bank_lending_notes", []) if isinstance(ai_data, dict) else []
    ins_notes = safe_get(ai_data, "insurance_notes", []) if isinstance(ai_data, dict) else []
    if bank_notes:
        st.markdown("#### Bank / lending observations")
        for n in bank_notes: st.write(f"- {n}")
    if ins_notes:
        st.markdown("#### Insurance observations")
        for n in ins_notes: st.write(f"- {n}")
    log = safe_get(ai_result, "attempt_log", [])
    if log:
        with st.expander("AI attempt log"):
            for e in log:
                if isinstance(e, dict): st.write(f"**{e.get('step','?')}**: {e.get('status','-')}")

with tabs[1]:
    bp_df = list_to_dataframe(rules.get("building_parts", []), ["part", "tg", "remaining_life_years", "remediation_cost_nok", "reason", "source"])
    st.dataframe(bp_df, use_container_width=True, hide_index=True)
    st.download_button("Download (.csv)", bp_df.to_csv(index=False).encode("utf-8"), "tdd_building_parts.csv", "text/csv")

with tabs[2]:
    rm = rules.get("risk_matrix", {})
    st.dataframe(list_to_dataframe([rm], ["technical_risk", "financial_risk", "regulatory_risk", "overall_class", "remediation_cost_total_nok"]), use_container_width=True, hide_index=True)

with tabs[3]:
    tek_df = list_to_dataframe(rules.get("tek17_deviations", []), ["title", "category", "recommendation", "source"])
    st.dataframe(tek_df, use_container_width=True, hide_index=True)
    if isinstance(ai_data, dict):
        ai_tek = safe_get(ai_data, "tek17_deviations_ai", [])
        if ai_tek:
            st.markdown("#### AI-identified deviations")
            st.dataframe(list_to_dataframe(ai_tek, ["title", "category", "recommendation"]), use_container_width=True, hide_index=True)

with tabs[4]:
    capex_df = pd.DataFrame(rules.get("capex_forecast", []))
    st.dataframe(capex_df, use_container_width=True, hide_index=True)
    st.download_button("Download CAPEX (.csv)", capex_df.to_csv(index=False).encode("utf-8"), "tdd_capex.csv", "text/csv")

with tabs[5]:
    ed = rules.get("energy_data", {})
    st.markdown(f"**Estimated energy class:** {ed.get('estimated_class', '-')} | **kWh/m2:** {ed.get('estimated_kwh_per_m2', '-')} | **EU Taxonomy:** {'Yes' if ed.get('eu_taxonomy_aligned') else 'No'}")
    if ed.get("recommendations"):
        st.markdown("#### Recommendations")
        for r in ed["recommendations"]: st.write(f"- {r}")
    env_flags = safe_get(ai_data, "environmental_flags", []) if isinstance(ai_data, dict) else []
    if env_flags:
        st.markdown("#### Environmental flags (AI)")
        for f in env_flags: st.write(f"- {f}")

with tabs[6]:
    if records:
        st.dataframe(pd.DataFrame(records), use_container_width=True, hide_index=True)
        st.download_button("Download manifest (.csv)", pd.DataFrame(records).to_csv(index=False).encode("utf-8"), "tdd_manifest.csv", "text/csv")
    else:
        st.info("No documents uploaded yet.")
    if rules.get("missing_categories"):
        st.warning("Missing: " + ", ".join(rules["missing_categories"]))

with tabs[7]:
    if include_portfolio:
        st.info("Portfolio mode is enabled. Upload documents for multiple properties to compare risk profiles across your portfolio.")
        st.markdown("Portfolio batch analysis will aggregate risk class, CAPEX and remediation costs across properties.")
    else:
        st.info("Enable portfolio mode above to see aggregated analysis across properties.")

with tabs[8]:
    audit_rows = [{"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"), "module": "TDD", "level": delivery_level, "action": "Analysis run", "documents": len(records), "ai_status": "OK" if isinstance(ai_data, dict) else "Not run", "user": "Builtly user"}]
    st.dataframe(pd.DataFrame(audit_rows), use_container_width=True, hide_index=True)

with tabs[9]:
    st.markdown(f"### {L('tab_export')}")
    md_report = build_md_report(records, rules, config, ai_result)
    st.download_button("Download report (.md)", md_report, "tdd_report.md", "text/markdown")
    pdf_bytes = build_tdd_pdf(records, rules, config, ai_result)
    if pdf_bytes:
        st.download_button("Download report (.pdf)", pdf_bytes, "tdd_report.pdf", "application/pdf")
    st.download_button("Download AI result (.json)", json.dumps(safe_get(ai_result, "data") or {}, indent=2, ensure_ascii=False, default=str), "tdd_result.json", "application/json")
    st.download_button("Download config (.json)", json.dumps(config, indent=2, ensure_ascii=False, default=str), "tdd_config.json", "application/json")
