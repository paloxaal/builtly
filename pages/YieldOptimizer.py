
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib import error as urlerror
from urllib import request as urlrequest

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

try:
    import fitz
except Exception:
    fitz = None

try:
    from fpdf import FPDF
except Exception:
    FPDF = None

THIS_FILE = Path(__file__).resolve()
if (THIS_FILE.parent / "builtly_module_kit.py").exists():
    ROOT = THIS_FILE.parent
elif (THIS_FILE.parent.parent / "builtly_module_kit.py").exists():
    ROOT = THIS_FILE.parent.parent
else:
    ROOT = THIS_FILE.parent

if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from builtly_module_kit import configure_page, render_hero, render_metric_cards, render_panel, render_project_snapshot, render_section  # type: ignore

DB_DIR = ROOT / "qa_database"
IMG_DIR = DB_DIR / "project_images"
FILES_DIR = DB_DIR / "project_files"
SSOT_FILE = DB_DIR / "ssot.json"
IMG_DIR.mkdir(parents=True, exist_ok=True)
FILES_DIR.mkdir(parents=True, exist_ok=True)

project = configure_page("Builtly | Area & Yield", "🏙️")

APP_LANG = st.session_state.get("app_lang", "🇳🇴 Norsk")
UI_LANG = "no" if APP_LANG == "🇳🇴 Norsk" else "en"
LANG_NAME_MAP = {
    "🇳🇴 Norsk": "norsk bokmal",
    "🇸🇪 Svenska": "svenska",
    "🇩🇰 Dansk": "dansk",
    "🇫🇮 Suomi": "finnish",
    "🇩🇪 Deutsch": "Deutsch",
    "🇬🇧 English (UK)": "English (UK)",
    "🇺🇸 English (US)": "English (US)",
}
REPORT_LANGUAGE = LANG_NAME_MAP.get(APP_LANG, "English")

TEXT = {
    "no": {
        "module": "Areal & Yield",
        "hero_title": "Finn arealet som kan skape mer verdi.",
        "hero_sub": "Last opp tegninger, velg hva du vil optimalisere, og generer en rapport med scenarioer, før/etter-tabeller, PDF og Excel.",
        "eyebrow": "Arealanalyse",
        "pills": ["Tegningsanalyse", "Salgbart areal", "Utleibart areal", "Før/etter", "PDF + Excel"],
        "back": "Tilbake",
        "open_project": "Prosjektoppsett",
        "open_front": "Forside",
        "goal": "Hva vil du oppnå?",
        "goals": ["Mer salgbart areal", "Mer utleibart areal", "Mindre tekniske rom", "Mindre kjerne", "Bedre miks og flyt"],
        "mode": "Ambisjonsnivå",
        "modes": ["Konservativ", "Balansert", "Ambisjøs"],
        "use": "Primær bruk",
        "uses": ["Kontor", "Bolig", "Mixed-use", "Hotell"],
        "gross": "Bruttoareal (m²)",
        "net": "Nettoareal (m²)",
        "core": "Kjerne (m²)",
        "tech": "Tekniske rom (m²)",
        "circ": "Kommunikasjon (m²)",
        "common": "Felles / støtte (m²)",
        "floors": "Etasjer",
        "value": "Verdi per m²",
        "friction": "Markedsfriksjon",
        "level": "Leveransenivå",
        "levels": ["Auto", "Gjennomgått"],
        "constraints": "Prosjektbegrensninger",
        "constraint_labels": [
            "Ikke svekk brann- og rømningslogikk",
            "Behold tilgjengelighet og universell utforming",
            "Ta hensyn til struktur og sjakter",
            "Behold dagslys og brukskvalitet",
        ],
        "drawings": "Tegningsgrunnlag",
        "drawing_help": "Last opp plan- eller etasjetegninger. Analysen ser etter stor kjerne, fragmenterte tekniske rom, lang kommunikasjon og areal som kan omdisponeres.",
        "schedule": "Valgfri arealoversikt (XLSX / CSV)",
        "generate": "Generer analyse og rapport",
        "ready": "Analysen er klar. Last ned rapport og Excel under.",
        "empty": "Kjør analysen for å se scenarioer, tegningsfunn og nedlastinger.",
        "downloads": "Nedlastinger",
        "dl_pdf": "Last ned rapport (PDF)",
        "dl_xlsx": "Last ned før/etter (Excel)",
        "dl_json": "Last ned analysegrunnlag (JSON)",
        "baseline": "Baseline",
        "scenarios": "Scenarioer",
        "before_after": "Før/etter",
        "findings": "Tegningsfunn",
        "report": "Rapportutkast",
        "sources": "Kilder",
        "what_you_get": "Dette får du",
        "what_items": [
            "Baseline for brutto, netto, salgbart, utleibart og serviceareal",
            "Tre scenarioer med før/etter og estimert verdipotensial",
            "Tegningsbaserte funn og konkrete forbedringsgrep",
            "PDF-rapport og Excel med før/etter-tabeller",
        ],
        "how_use": "Slik bruker du modulen",
        "how_items": [
            "Legg inn dagens arealtall",
            "Velg mål og ambisjonsnivå",
            "Last opp tegningsgrunnlag",
            "Generer analyse, rapport og før/etter-uttrekk",
        ],
        "ai_note": "Alle scenarioer må vurderes mot brann, akustikk, struktur, teknikk, dagslys og marked før de brukes eksternt.",
        "best": "Beste scenario",
        "sale_gain": "Ekstra salgbart areal",
        "let_gain": "Ekstra utleibart areal",
        "value_up": "Potensiell verdi",
        "yield_now": "Dagens yield",
        "service": "Serviceandel",
        "data": "Datagrunnlag",
        "data_labels": {"strong": "Sterkt grunnlag", "partial": "Delvis grunnlag", "weak": "Svakere grunnlag"},
        "find_sub": "Analysen bruker tegningsgrunnlag og arealdata sammen for å foreslå realistiske forbedringsgrep.",
        "need_input": "Legg inn arealtall og gjerne last opp minst én plantegning for et bedre resultat.",
        "running": "Analyserer tegninger, bygger scenarioer og genererer rapport...",
        "report_title": "Areal- og yieldrapport",
        "report_subject": "Areal, scenariovurdering og forbedringsgrep",
        "disclaimer_auto": "Dette er et AI-generert beslutningsgrunnlag. Det er ikke en faglig attestasjon og må kvalitetssikres før bruk i investering, prosjektering eller avtale.",
        "disclaimer_review": "Dette utkastet er laget for gjennomgang. Det er ikke en attestert fagrapport og må kvalitetssikres før bruk i investering, prosjektering eller avtale.",
        "analysis_caption": "Maskinell tegningsvurdering – ikke arbeidstegning",
    },
    "en": {
        "module": "Area & Yield",
        "hero_title": "Find the area that can create more value.",
        "hero_sub": "Upload drawings, choose what to optimise, and generate a report with scenarios, before/after tables, PDF and Excel.",
        "eyebrow": "Area analysis",
        "pills": ["Drawing analysis", "Saleable area", "Lettable area", "Before/after", "PDF + Excel"],
        "back": "Back",
        "open_project": "Project setup",
        "open_front": "Front page",
        "goal": "What do you want to optimise?",
        "goals": ["More saleable area", "More lettable area", "Less technical space", "Smaller core", "Better mix and flow"],
        "mode": "Ambition level",
        "modes": ["Conservative", "Balanced", "Ambitious"],
        "use": "Primary use",
        "uses": ["Office", "Residential", "Mixed-use", "Hotel"],
        "gross": "Gross area (m2)",
        "net": "Net area (m2)",
        "core": "Core (m2)",
        "tech": "Technical rooms (m2)",
        "circ": "Circulation (m2)",
        "common": "Common / support (m2)",
        "floors": "Floors",
        "value": "Value per m2",
        "friction": "Market friction",
        "level": "Delivery level",
        "levels": ["Auto", "Reviewed"],
        "constraints": "Project constraints",
        "constraint_labels": [
            "Do not weaken fire or egress logic",
            "Maintain accessibility and usability",
            "Respect structure and shaft logic",
            "Maintain daylight and user quality",
        ],
        "drawings": "Drawing basis",
        "drawing_help": "Upload floor plans or storey drawings. The analysis looks for oversized cores, fragmented technical rooms, long circulation and space that can be reallocated.",
        "schedule": "Optional area schedule (XLSX / CSV)",
        "generate": "Generate analysis and report",
        "ready": "The analysis is ready. Download the report and the Excel workbook below.",
        "empty": "Run the analysis to see scenarios, drawing findings and downloads.",
        "downloads": "Downloads",
        "dl_pdf": "Download report (PDF)",
        "dl_xlsx": "Download before/after (Excel)",
        "dl_json": "Download analysis basis (JSON)",
        "baseline": "Baseline",
        "scenarios": "Scenarios",
        "before_after": "Before/after",
        "findings": "Drawing findings",
        "report": "Report draft",
        "sources": "Sources",
        "what_you_get": "What you get",
        "what_items": [
            "A baseline for gross, net, saleable, lettable and service area",
            "Three scenarios with before/after and estimated value potential",
            "Drawing-based findings and concrete improvement actions",
            "A PDF report and an Excel workbook with before/after tables",
        ],
        "how_use": "How to use the module",
        "how_items": [
            "Enter the current area values",
            "Choose the target and ambition level",
            "Upload the drawing basis",
            "Generate the analysis, report and before/after extracts",
        ],
        "ai_note": "All scenarios should still be checked against fire, acoustics, structure, MEP, daylight and market fit before they are used externally.",
        "best": "Best scenario",
        "sale_gain": "Extra saleable area",
        "let_gain": "Extra lettable area",
        "value_up": "Potential value",
        "yield_now": "Current yield",
        "service": "Service ratio",
        "data": "Data basis",
        "data_labels": {"strong": "Strong basis", "partial": "Partial basis", "weak": "Weaker basis"},
        "find_sub": "The analysis uses the drawing basis and area data together to suggest realistic improvement actions.",
        "need_input": "Please enter area values and ideally upload at least one floor plan for a better result.",
        "running": "Analysing drawings, building scenarios and generating the report...",
        "report_title": "Area and yield report",
        "report_subject": "Area, scenario assessment and improvement actions",
        "disclaimer_auto": "This is an AI-generated decision product. It is not a professional attestation and must be quality checked before it is used for investment, design or contractual purposes.",
        "disclaimer_review": "This draft has been generated for review. It is not an attested professional report and must be quality checked before it is used for investment, design or contractual purposes.",
        "analysis_caption": "Machine-led drawing review - not a working drawing",
    },
}
T = TEXT[UI_LANG]

COUNTRY_RULES = {
    "NO": ("NOK", "Check against TEK17/VTEK, fire strategy, accessibility, daylight, structure and MEP coordination."),
    "SE": ("SEK", "Check against PBL/PBF, BBR, fire, accessibility, daylight, services and structural logic."),
    "DK": ("DKK", "Check against BR18, fire strategy, accessibility, daylight, services and structural logic."),
    "FI": ("EUR", "Check against Finnish building regulations, fire, accessibility, daylight, services and structural logic."),
    "DE": ("EUR", "Check against Landesbauordnung, fire safety, accessibility, daylight, TGA and structural logic."),
    "UK": ("GBP", "Check against Building Regulations, Approved Documents, fire, accessibility, daylight, services and structure."),
    "US": ("USD", "Check against the adopted IBC/IRC and local code, fire, accessibility, daylight, services and structure."),
}

def tx(key: str) -> Any:
    return T[key]

def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    repl = {"–": "-", "—": "-", "“": '"', "”": '"', "’": "'", "‘": "'", "…": "...", "•": "-", "\u00a0": " "}
    for old, new in repl.items():
        text = text.replace(old, new)
    return text.strip()

def clean_pdf_text(value: Any) -> str:
    return clean_text(value).encode("latin-1", "replace").decode("latin-1")

def short_text(value: Any, max_len: int = 120) -> str:
    text = clean_text(value)
    return text if len(text) <= max_len else text[: max_len - 3].rstrip() + "..."

def project_state() -> Dict[str, Any]:
    default = {
        "p_name": "Nytt prosjekt" if UI_LANG == "no" else "New project",
        "c_name": "",
        "p_desc": "",
        "adresse": "",
        "kommune": "",
        "gnr": "",
        "bnr": "",
        "b_type": "",
        "etasjer": 1,
        "bta": 2500,
        "land": "Norge",
    }
    if "project_data" in st.session_state and isinstance(st.session_state.project_data, dict):
        data = dict(default)
        data.update(st.session_state.project_data)
        return data
    if SSOT_FILE.exists():
        try:
            data = dict(default)
            data.update(json.loads(SSOT_FILE.read_text(encoding="utf-8")))
            return data
        except Exception:
            return default
    return default

project = project_state()

def market_code() -> str:
    land = clean_text(project.get("land", "")).lower()
    if "swed" in land or "sverige" in land:
        return "SE"
    if "dan" in land:
        return "DK"
    if "fin" in land:
        return "FI"
    if "germ" in land or "tysk" in land or "deutsch" in land:
        return "DE"
    if "united kingdom" in land or land == "uk" or "england" in land:
        return "UK"
    if "usa" in land or "united states" in land or "america" in land:
        return "US"
    return "NO"

def current_currency() -> str:
    return COUNTRY_RULES[market_code()][0]

def market_rule_text() -> str:
    return COUNTRY_RULES[market_code()][1]

def fmt_money(value: float) -> str:
    return f"{int(round(float(value))):,}".replace(",", " ") + f" {current_currency()}"

st.markdown("""
<style>
    .stApp { color:#f5f7fb !important; }
    .block-container { max-width:1440px !important; padding-top:1.4rem !important; }
    header[data-testid="stHeader"], #MainMenu, footer { visibility:hidden; }
    button[kind="primary"] {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color:#041018 !important; border:none !important; font-weight:750 !important; border-radius:12px !important;
    }
    button[kind="secondary"], button[kind="tertiary"] {
        background-color: rgba(255,255,255,0.05) !important;
        color:#f8fafc !important; border:1px solid rgba(120,145,170,0.3) !important; border-radius:12px !important;
    }
    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div, [data-baseweb="input"] > div {
        background-color:#0d1824 !important; border:1px solid rgba(120,145,170,0.42) !important; border-radius:10px !important;
    }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, [data-baseweb="input"] input,
    div[data-baseweb="select"] input, div[data-baseweb="select"] span, div[data-baseweb="select"] div {
        color:#ffffff !important; -webkit-text-fill-color:#ffffff !important; background-color:transparent !important;
    }
    ul[data-baseweb="menu"] { background-color:#0d1824 !important; border:1px solid rgba(120,145,170,0.42) !important; }
    ul[data-baseweb="menu"] li { color:#ffffff !important; -webkit-text-fill-color:#ffffff !important; background-color:#0d1824 !important; }
    ul[data-baseweb="menu"] li:hover { background-color:rgba(56,194,201,0.12) !important; }
    ul[data-baseweb="menu"] li[aria-selected="true"] { background-color:rgba(56,194,201,0.18) !important; }
    [data-baseweb="popover"] { background-color:#0d1824 !important; }
    [data-baseweb="popover"] > div { background-color:#0d1824 !important; border:1px solid rgba(120,145,170,0.42) !important; border-radius:10px !important; }
    [role="listbox"] { background-color:#0d1824 !important; }
    [role="option"] { color:#ffffff !important; -webkit-text-fill-color:#ffffff !important; background-color:#0d1824 !important; }
    [role="option"]:hover, [role="option"][aria-selected="true"] { background-color:rgba(56,194,201,0.12) !important; }
    [data-testid="stFileUploaderDropzone"] {
        background:#0d1824 !important; border:1px dashed rgba(120,145,170,0.56) !important; border-radius:14px !important;
    }
    [data-testid="stFileUploaderDropzone"] * { color:#d8e2f1 !important; }
    .stTabs [data-baseweb="tab-list"] { gap:0.45rem; }
    .stTabs [data-baseweb="tab"] {
        border-radius:999px; border:1px solid rgba(255,255,255,0.08); background:rgba(255,255,255,0.03); color:#dce7fa !important; padding:0.55rem 0.95rem;
    }
    .stTabs [aria-selected="true"] {
        background: rgba(110,168,254,0.16) !important; border-color: rgba(110,168,254,0.24) !important; color:#fff !important;
    }
</style>
""", unsafe_allow_html=True)

def go(candidate: Path):
    if candidate.exists() and hasattr(st, "switch_page"):
        try:
            st.switch_page(str(candidate))
        except Exception:
            pass

nav1, nav2, nav3 = st.columns([1.1, 1.2, 4], gap="small")
with nav1:
    if st.button("← " + tx("back"), use_container_width=True):
        for cand in [ROOT / "Project_Builtly_Redesign.py", ROOT / "Builtly_AI_frontpage_access_gate_v7_contact.py", ROOT / "Builtly_AI_frontpage_access_gate_v6_balanced.py", ROOT / "Builtly_AI_frontpage_access_gate.py"]:
            go(cand)
            break
with nav2:
    if st.button(tx("open_project"), use_container_width=True):
        go(ROOT / "Project_Builtly_Redesign.py")
with nav3:
    st.caption(market_rule_text())

render_hero(eyebrow=tx("eyebrow"), title=tx("hero_title"), subtitle=tx("hero_sub"), pills=tx("pills"), badge=tx("module"))

def provider_order() -> List[str]:
    order = clean_text(os.getenv("BUILTLY_PROVIDER_ORDER", "openai,anthropic,gemini"))
    values = [x.strip().lower() for x in order.split(",") if x.strip().lower() in {"openai", "anthropic", "gemini"}]
    return values or ["openai", "anthropic", "gemini"]

def provider_ready(provider: str) -> bool:
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    if provider == "anthropic":
        return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY"))
    if provider == "gemini":
        return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    return False

def model_for(provider: str) -> str:
    if provider == "openai":
        return clean_text(os.getenv("BUILTLY_OPENAI_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4.1")
    if provider == "anthropic":
        return clean_text(os.getenv("BUILTLY_CLAUDE_MODEL") or os.getenv("ANTHROPIC_MODEL") or "claude-3-5-sonnet-latest")
    return clean_text(os.getenv("BUILTLY_GEMINI_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash")

def http_post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 120) -> Dict[str, Any]:
    req = urlrequest.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")
        except Exception:
            detail = str(exc)
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"Connection error: {exc}") from exc

def image_b64(img: Image.Image) -> str:
    bio = io.BytesIO()
    img.convert("RGB").save(bio, format="PNG")
    return base64.b64encode(bio.getvalue()).decode("utf-8")

def openai_call(system_prompt: str, user_prompt: str, images: Sequence[Image.Image], json_mode: bool = False) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    content = [{"type": "input_text", "text": user_prompt}]
    for img in images:
        content.append({"type": "input_image", "image_url": "data:image/png;base64," + image_b64(img), "detail": "high"})
    payload: Dict[str, Any] = {
        "model": model_for("openai"),
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": content},
        ],
        "max_output_tokens": 2200,
        "temperature": 0.1,
    }
    if json_mode:
        payload["text"] = {"format": {"type": "json_object"}}
    data = http_post_json("https://api.openai.com/v1/responses", payload, {"Authorization": f"Bearer {api_key}"})
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return clean_text(data["output_text"])
    out = []
    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            for part in item.get("content", []) or []:
                if part.get("text"):
                    out.append(part["text"])
    if out:
        return clean_text("\n".join(out))
    raise RuntimeError("OpenAI returned no text")

def anthropic_call(system_prompt: str, user_prompt: str, images: Sequence[Image.Image]) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
    content: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    for img in images:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64(img)}})
    payload = {
        "model": model_for("anthropic"),
        "system": system_prompt,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 2200,
        "temperature": 0.1,
    }
    data = http_post_json("https://api.anthropic.com/v1/messages", payload, {"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    out = [item.get("text", "") for item in data.get("content", []) or [] if item.get("type") == "text"]
    if out:
        return clean_text("\n".join(out))
    raise RuntimeError("Anthropic returned no text")

def gemini_call(system_prompt: str, user_prompt: str, images: Sequence[Image.Image], json_mode: bool = False) -> str:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    parts: List[Dict[str, Any]] = [{"text": system_prompt + "\n\n" + user_prompt}]
    for img in images:
        parts.append({"inline_data": {"mime_type": "image/png", "data": image_b64(img)}})
    payload: Dict[str, Any] = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2200}}
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
    model_name = model_for("gemini")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    data = http_post_json(url, payload, {})
    out = []
    for cand in data.get("candidates", []) or []:
        for part in (cand.get("content", {}) or {}).get("parts", []) or []:
            if part.get("text"):
                out.append(part["text"])
    if out:
        return clean_text("\n".join(out))
    raise RuntimeError("Gemini returned no text")

def ai_text(system_prompt: str, user_prompt: str, images: Sequence[Image.Image], json_mode: bool = False) -> Tuple[str, Optional[str]]:
    attempts = []
    for provider in provider_order():
        if not provider_ready(provider):
            continue
        try:
            if provider == "openai":
                return openai_call(system_prompt, user_prompt, images, json_mode=json_mode), provider
            if provider == "anthropic":
                return anthropic_call(system_prompt, user_prompt, images), provider
            if provider == "gemini":
                return gemini_call(system_prompt, user_prompt, images, json_mode=json_mode), provider
        except Exception as exc:
            attempts.append(f"{provider}: {type(exc).__name__}: {exc}")
    return "", None

def extract_json_blob(text: str) -> str:
    cleaned = clean_text(text)
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:].strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last != -1 and last > first:
        cleaned = cleaned[first:last+1]
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return cleaned

def safe_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(extract_json_blob(text))
    except Exception:
        return None

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

def thumb(img: Image.Image, size: Tuple[int, int] = (1800, 1800)) -> Image.Image:
    out = img.convert("RGB")
    out.thumbnail(size)
    return out

def drawing_hint(name: str) -> str:
    low = clean_text(name).lower()
    if any(k in low for k in ["plan", "plantegning", "floor", "etg", "level"]):
        return "plan"
    if any(k in low for k in ["section", "snitt"]):
        return "section"
    if any(k in low for k in ["facade", "fasade", "elevation"]):
        return "facade"
    if low.endswith(".dxf") or low.endswith(".dwg"):
        return "cad"
    return "unknown"

def build_record(name: str, image: Optional[Image.Image], source: str) -> Dict[str, Any]:
    return {"name": clean_text(name), "label": clean_text(Path(name).stem), "source": clean_text(source), "hint": drawing_hint(name), "image": thumb(image) if isinstance(image, Image.Image) else None}

def priority(record: Dict[str, Any]) -> int:
    return {"plan": 100, "section": 70, "facade": 40, "cad": 10}.get(record.get("hint", "unknown"), 0)

def saved_drawings() -> List[Dict[str, Any]]:
    drawings: List[Dict[str, Any]] = []
    has_pdf_originals = False

    # 1. PREFER original PDFs from project_files at high resolution for AI analysis
    if FILES_DIR.exists() and fitz is not None:
        for p in sorted(FILES_DIR.iterdir()):
            if p.suffix.lower() == ".pdf":
                has_pdf_originals = True
                try:
                    doc = fitz.open(str(p))
                    for page_num in range(min(6, len(doc))):
                        page = doc.load_page(page_num)
                        # High resolution for AI vision — 2.0x gives ~144 DPI
                        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                        drawings.append(build_record(f"{p.name} - side {page_num+1}", img, "Project Setup (PDF original)"))
                    doc.close()
                except Exception:
                    drawings.append(build_record(p.name, None, "Project Setup (PDF)"))
            elif p.suffix.lower() in SUPPORTED_IMAGE_EXTS:
                # Original images from project_files (full quality)
                try:
                    drawings.append(build_record(p.name, Image.open(str(p)).convert("RGB"), "Project Setup (original)"))
                except Exception:
                    pass
            elif p.suffix.lower() in {".dwg", ".dxf", ".ifc"}:
                drawings.append(build_record(p.name, None, "Project Setup (CAD/IFC)"))

    # 2. JPG previews from project_images ONLY if no PDF originals were found
    #    (these are low-res 1200px thumbnails — worse for AI analysis)
    if not has_pdf_originals and IMG_DIR.exists():
        for p in sorted(IMG_DIR.iterdir()):
            if p.suffix.lower() in SUPPORTED_IMAGE_EXTS:
                try:
                    drawings.append(build_record(p.name, Image.open(p), "Project Setup (preview)"))
                except Exception:
                    pass

    # 3. Session state images
    imgs = st.session_state.get("project_images", [])
    if isinstance(imgs, list):
        for idx, img in enumerate(imgs, start=1):
            if isinstance(img, Image.Image):
                drawings.append(build_record(f"project_image_{idx}.png", img, "Session State"))

    drawings.sort(key=priority, reverse=True)
    for idx, record in enumerate(drawings):
        record["page_index"] = idx
    return drawings

def uploaded_drawings(files: Sequence[Any]) -> List[Dict[str, Any]]:
    drawings: List[Dict[str, Any]] = []
    for f in files or []:
        try:
            f.seek(0)
        except Exception:
            pass
        name = clean_text(getattr(f, "name", "uploaded_file"))
        suffix = Path(name.lower()).suffix
        if suffix == ".pdf":
            if fitz is not None:
                try:
                    raw = f.read()
                    doc = fitz.open(stream=raw, filetype="pdf")
                    for page_num in range(min(6, len(doc))):
                        page = doc.load_page(page_num)
                        # High resolution for AI vision — 2.0x gives ~144 DPI
                        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
                        drawings.append(build_record(f"{name} - page {page_num+1}", img, "Uploaded PDF"))
                    doc.close()
                    continue
                except Exception:
                    pass
            drawings.append(build_record(name, None, "Uploaded PDF"))
        elif suffix in SUPPORTED_IMAGE_EXTS:
            try:
                drawings.append(build_record(name, Image.open(f).convert("RGB"), "Uploaded image"))
            except Exception:
                drawings.append(build_record(name, None, "Uploaded image"))
        else:
            drawings.append(build_record(name, None, "Uploaded file"))
    drawings.sort(key=priority, reverse=True)
    for idx, record in enumerate(drawings):
        record["page_index"] = idx
    return drawings

def parse_schedule(file_obj: Optional[Any]) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    if file_obj is None:
        return None, None
    try:
        suffix = Path(clean_text(file_obj.name)).suffix.lower()
        df = pd.read_csv(file_obj) if suffix == ".csv" else pd.read_excel(file_obj)
    except Exception:
        return None, None
    if df.empty:
        return df, None
    area_col = None
    type_col = None
    for col in df.columns:
        low = clean_text(col).lower()
        if area_col is None and any(k in low for k in ["area", "areal", "m2", "sqm", "size"]):
            area_col = col
        if type_col is None and any(k in low for k in ["type", "category", "kategori", "room", "space", "bruk"]):
            type_col = col
    if area_col is None:
        return df, None
    work = df.copy()
    work[area_col] = pd.to_numeric(work[area_col], errors="coerce").fillna(0.0)
    if type_col is None:
        return df, pd.DataFrame([{"Category": "Uploaded total", "m2": round(float(work[area_col].sum()), 1)}])
    def bucket(label: str) -> str:
        low = clean_text(label).lower()
        if any(k in low for k in ["core", "kjerne", "stairs", "elevator", "lift", "shaft", "sjakt"]):
            return tx("core")
        if any(k in low for k in ["tech", "tekn", "mep", "hvac", "vent", "elec", "plant"]):
            return tx("tech")
        if any(k in low for k in ["corr", "gang", "lobby", "circul", "hall", "vestibule"]):
            return tx("circ")
        if any(k in low for k in ["common", "support", "felles", "shared", "meeting", "reception", "lounge"]):
            return tx("common")
        return tx("net")
    work["_bucket"] = work[type_col].map(bucket)
    summary = work.groupby("_bucket", dropna=False)[area_col].sum().reset_index().rename(columns={"_bucket": "Category", area_col: "m2"})
    summary["m2"] = summary["m2"].round(1)
    return df, summary

def wrap_text_px(text: str, font: Any, max_width: int) -> List[str]:
    text = clean_text(text)
    if not text:
        return [""]
    words = text.split()
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = current + " " + word
        if font.getbbox(candidate)[2] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines

def get_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()

def annotate_preview(record: Dict[str, Any], notes: Sequence[str], target_label: str) -> Optional[Image.Image]:
    if not isinstance(record.get("image"), Image.Image):
        return None
    base = record["image"].convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    w, h = base.size
    pad = max(16, int(min(w, h) * 0.02))
    title_font = get_font(max(18, int(min(w, h) * 0.024)), bold=True)
    body_font = get_font(max(13, int(min(w, h) * 0.017)), bold=False)
    box_h = max(84, int(h * 0.15))
    draw.rounded_rectangle((pad, pad, w - pad, pad + box_h), radius=18, fill=(6, 17, 26, 210), outline=(56, 194, 201, 255), width=2)
    draw.text((pad + 16, pad + 12), clean_text(record.get("label")), font=title_font, fill=(255, 255, 255, 255))
    draw.text((pad + 16, pad + 18 + title_font.getbbox("Ag")[3]), clean_text(target_label), font=body_font, fill=(210, 220, 232, 255))
    if notes:
        note_y = pad + box_h + 12
        draw.rounded_rectangle((pad, note_y, w - pad, min(h - pad, note_y + 100)), radius=16, fill=(6, 17, 26, 190))
        yy = note_y + 10
        for note in notes[:3]:
            for line in wrap_text_px(note, body_font, w - 2 * pad - 24):
                draw.text((pad + 12, yy), "- " + clean_text(line), font=body_font, fill=(245, 247, 251, 255))
                yy += body_font.getbbox("Ag")[3] + 4
    return Image.alpha_composite(base, overlay).convert("RGB")

def baseline_metrics(gross: float, net: float, core: float, tech: float, circ: float, common: float) -> Dict[str, float]:
    gross = max(float(gross), 0.0)
    net = max(float(net), 0.0)
    core = max(float(core), 0.0)
    tech = max(float(tech), 0.0)
    circ = max(float(circ), 0.0)
    common = max(float(common), 0.0)
    sale = max(net - common * 0.40 - tech * 0.20 - circ * 0.05, 0.0)
    let = max(net - common * 0.18 - circ * 0.03, 0.0)
    return {
        "gross": gross, "net": net, "core": core, "tech": tech, "circ": circ, "common": common,
        "sale": sale, "let": let, "yield": sale / gross if gross else 0.0, "service": (core + tech + circ) / gross if gross else 0.0,
    }

GOAL_CODES = ["saleable", "lettable", "technical", "core", "mix"]
MODE_CODES = ["conservative", "balanced", "ambitious"]

def goal_code(selected: str) -> str:
    return GOAL_CODES[tx("goals").index(selected)]

def mode_code(selected: str) -> str:
    return MODE_CODES[tx("modes").index(selected)]

def default_profile(goal: str, mode: str) -> Dict[str, float]:
    mult = {"conservative": 0.75, "balanced": 1.0, "ambitious": 1.30}[mode]
    profiles = {
        "saleable": {"core": 0.03, "tech": 0.05, "circ": 0.04, "common": 0.03, "sale_capture": 0.82, "let_capture": 0.45},
        "lettable": {"core": 0.02, "tech": 0.06, "circ": 0.05, "common": 0.03, "sale_capture": 0.45, "let_capture": 0.84},
        "technical": {"core": 0.01, "tech": 0.09, "circ": 0.03, "common": 0.02, "sale_capture": 0.55, "let_capture": 0.75},
        "core": {"core": 0.07, "tech": 0.03, "circ": 0.03, "common": 0.02, "sale_capture": 0.70, "let_capture": 0.64},
        "mix": {"core": 0.03, "tech": 0.04, "circ": 0.05, "common": 0.04, "sale_capture": 0.66, "let_capture": 0.68},
    }
    base = profiles[goal]
    return {k: (v * mult if "capture" not in k else v) for k, v in base.items()}

def deterministic_findings(goal: str) -> List[Dict[str, str]]:
    base = {
        "saleable": [
            {"title": "Tighten the core", "effect": "Release more directly saleable area", "confidence": "High", "constraint": "Check against fire, vertical circulation and shafts"},
            {"title": "Reduce support area", "effect": "Shift more space into value-creating zones", "confidence": "Medium", "constraint": "Must fit the concept and market strategy"},
            {"title": "Shorter circulation", "effect": "Improves the net/gross relation", "confidence": "Medium", "constraint": "Check wayfinding and accessibility"},
        ],
        "lettable": [
            {"title": "Consolidate technical rooms", "effect": "Less fragmented lettable area", "confidence": "High", "constraint": "Coordinate with MEP and shafts"},
            {"title": "Streamline circulation", "effect": "Improves the efficiency of lease zones", "confidence": "Medium", "constraint": "Check against fire and operations"},
            {"title": "Clean up support space", "effect": "Makes more area directly usable by tenants", "confidence": "Medium", "constraint": "Check against concept and user quality"},
        ],
        "technical": [
            {"title": "Consolidate plant areas", "effect": "Reduce technical fragmentation", "confidence": "High", "constraint": "Confirm with MEP strategy"},
            {"title": "Rationalise shaft layout", "effect": "Less duplicated technical area", "confidence": "Medium", "constraint": "Check against structure and risers"},
            {"title": "Free hidden technical buffers", "effect": "Recover some non-value-creating area", "confidence": "Low", "constraint": "Requires technical verification"},
        ],
        "core": [
            {"title": "Compact the core", "effect": "Reduce core share without losing function", "confidence": "Medium", "constraint": "Check stairs, lifts, escape and capacity"},
            {"title": "Move technical functions out of the core", "effect": "Use the core more efficiently", "confidence": "Medium", "constraint": "Coordinate with MEP and fire"},
            {"title": "Simplify vertical service strategy", "effect": "The core can be tightened further", "confidence": "Low", "constraint": "Depends on building-wide services logic"},
        ],
        "mix": [
            {"title": "Rebalance value and support zones", "effect": "Cleaner plan logic and more usable net area", "confidence": "Medium", "constraint": "Must fit the market and concept"},
            {"title": "Clean up edge conditions", "effect": "More usable pockets and less dead area", "confidence": "Medium", "constraint": "Check facade, daylight and furniture logic"},
            {"title": "Prioritise one main move for core and circulation", "effect": "Improves flow and efficiency at the same time", "confidence": "Medium", "constraint": "Check against fire and operations"},
        ],
    }
    return base[goal]

def analysis_prompt(system_lang: str, baseline: Dict[str, float], goal: str, mode: str, drawings: Sequence[Dict[str, Any]], constraints: Sequence[str]) -> Tuple[str, str]:
    manifest = "\n".join(
        f"- page {d.get('page_index')}: {d.get('name')} | source: {d.get('source')} | hint: {d.get('hint')}"
        for d in drawings
    ) or "- none"
    system_prompt = (
        "You are Builtly Area and Yield AI. "
        "You support property developers and project teams with early-stage area efficiency analysis. "
        f"Respond in {system_lang}. "
        "Do not claim exact measured areas from drawings unless dimensions are explicit. "
        "Use the drawings to identify likely inefficiencies such as oversized cores, fragmented technical rooms, long circulation, duplicated shafts, weak plan flow and non-value-creating support zones. "
        "Return only valid JSON."
    )
    user_prompt = f"""
Project:
- Name: {clean_text(project.get('p_name'))}
- Type: {clean_text(project.get('b_type'))}
- Address: {clean_text(project.get('adresse'))}, {clean_text(project.get('kommune'))}
- Regulatory context: {market_rule_text()}
- Floors: {clean_text(project.get('etasjer'))}

Current area split:
- Gross area: {baseline['gross']} m2
- Net area: {baseline['net']} m2
- Core: {baseline['core']} m2
- Technical rooms: {baseline['tech']} m2
- Circulation: {baseline['circ']} m2
- Common/support: {baseline['common']} m2
- Saleable baseline: {baseline['sale']} m2
- Lettable baseline: {baseline['let']} m2

Target:
- Primary goal: {goal}
- Ambition level: {mode}

Project constraints:
{chr(10).join("- " + clean_text(x) for x in constraints) or "- none"}

Drawing manifest:
{manifest}

Return JSON with:
{{
  "data_status": "strong | partial | weak",
  "data_reason": "short text",
  "drawing_findings": [
    {{"page_label":"name","finding":"short observation","impact":"saleable | lettable | technical | core | mix","confidence":"High | Medium | Low","evidence":"what in the drawing suggests this"}}
  ],
  "opportunities": [
    {{"title":"short title","effect":"expected benefit","confidence":"High | Medium | Low","constraint":"what must be checked"}}
  ],
  "scenario_overrides": [
    {{"name":"Scenario A","description":"short user-facing description","core_delta_pct":0.00,"tech_delta_pct":0.00,"circ_delta_pct":0.00,"common_delta_pct":0.00,"sale_capture":0.00,"let_capture":0.00,"constraint":"main caution"}}
  ],
  "regulatory_checks":["plain-language checks"],
  "next_steps":["plain-language next steps"]
}}
""".strip()
    return system_prompt, user_prompt

def normalise_analysis(raw: Optional[Dict[str, Any]], goal: str, drawings: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    base = {
        "data_status": "partial" if drawings else "weak",
        "data_reason": "The result is based mainly on your area inputs and deterministic scenario logic.",
        "drawing_findings": [],
        "opportunities": deterministic_findings(goal),
        "scenario_overrides": [],
        "regulatory_checks": [market_rule_text()],
        "next_steps": [
            "Check the preferred scenario against fire, structure and MEP before you lock the plan.",
            "Verify the assumptions against the latest plan set or area schedule.",
        ],
    }
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    out.update(raw)
    if not isinstance(out.get("drawing_findings"), list):
        out["drawing_findings"] = []
    if not isinstance(out.get("opportunities"), list) or not out["opportunities"]:
        out["opportunities"] = deterministic_findings(goal)
    if not isinstance(out.get("scenario_overrides"), list):
        out["scenario_overrides"] = []
    if not isinstance(out.get("regulatory_checks"), list):
        out["regulatory_checks"] = [market_rule_text()]
    if not isinstance(out.get("next_steps"), list):
        out["next_steps"] = base["next_steps"]
    status = clean_text(out.get("data_status", "")).lower()
    out["data_status"] = status if status in {"strong", "partial", "weak"} else base["data_status"]
    return out

def visual_analysis(drawings: Sequence[Dict[str, Any]], baseline: Dict[str, float], goal: str, mode: str, constraints: Sequence[str]) -> Tuple[Dict[str, Any], Optional[str]]:
    preview = [d for d in drawings if isinstance(d.get("image"), Image.Image)][:3]
    if not preview:
        return normalise_analysis(None, goal, drawings), None
    images = [d["image"] for d in preview]
    system_prompt, user_prompt = analysis_prompt(REPORT_LANGUAGE, baseline, goal, mode, preview, constraints)
    text, provider = ai_text(system_prompt, user_prompt, images, json_mode=True)
    data = safe_json(text) if text else None
    return normalise_analysis(data, goal, drawings), provider

def build_scenario_rows(baseline: Dict[str, float], goal: str, mode: str, value_per_m2: float, friction: int, analysis: Dict[str, Any]) -> pd.DataFrame:
    profile = default_profile(goal, mode)
    multipliers = [0.8, 1.0, 1.25]
    labels = ["Scenario A", "Scenario B", "Scenario C"]
    rows = []
    overrides = analysis.get("scenario_overrides") or []
    for idx, mult in enumerate(multipliers):
        ov = overrides[idx] if idx < len(overrides) and isinstance(overrides[idx], dict) else {}
        p = dict(profile)
        if ov:
            p["core"] = max(0.0, float(ov.get("core_delta_pct", p["core"])))
            p["tech"] = max(0.0, float(ov.get("tech_delta_pct", p["tech"])))
            p["circ"] = max(0.0, float(ov.get("circ_delta_pct", p["circ"])))
            p["common"] = max(0.0, float(ov.get("common_delta_pct", p["common"])))
            p["sale_capture"] = min(max(float(ov.get("sale_capture", p["sale_capture"])), 0.15), 1.0)
            p["let_capture"] = min(max(float(ov.get("let_capture", p["let_capture"])), 0.15), 1.0)
        core_after = max(baseline["core"] * (1 - p["core"] * mult), 0.0)
        tech_after = max(baseline["tech"] * (1 - p["tech"] * mult), 0.0)
        circ_after = max(baseline["circ"] * (1 - p["circ"] * mult), 0.0)
        common_after = max(baseline["common"] * (1 - p["common"] * mult), 0.0)
        freed = max((baseline["core"] - core_after) + (baseline["tech"] - tech_after) + (baseline["circ"] - circ_after) + (baseline["common"] - common_after), 0.0)
        sale_gain = freed * p["sale_capture"]
        let_gain = freed * p["let_capture"]
        value_up = max(sale_gain, let_gain) * max(float(value_per_m2), 0.0) * max(0.75, 1 - friction / 100)
        rows.append({
            "Scenario": clean_text(ov.get("name")) or labels[idx],
            "Recommendation": clean_text(ov.get("description")) or clean_text((analysis.get("opportunities") or [{}])[0].get("effect", "")),
            "Constraint": clean_text(ov.get("constraint")) or market_rule_text(),
            "Source": "AI + deterministic" if ov else "Deterministic",
            "core_after": round(core_after, 1),
            "tech_after": round(tech_after, 1),
            "circ_after": round(circ_after, 1),
            "common_after": round(common_after, 1),
            "sale_after": round(baseline["sale"] + sale_gain, 1),
            "let_after": round(baseline["let"] + let_gain, 1),
            "sale_gain": round(sale_gain, 1),
            "let_gain": round(let_gain, 1),
            "freed_area": round(freed, 1),
            "value_uplift": round(value_up),
            "yield_after": round((baseline["sale"] + sale_gain) / baseline["gross"] * 100 if baseline["gross"] else 0.0, 1),
        })
    return pd.DataFrame(rows)

def best_scenario(df: pd.DataFrame, goal: str) -> pd.Series:
    metric = {"saleable": "sale_gain", "lettable": "let_gain", "technical": "freed_area", "core": "freed_area", "mix": "value_uplift"}[goal]
    return df.loc[df[metric].idxmax()]

def baseline_df(b: Dict[str, float]) -> pd.DataFrame:
    gross = b["gross"] or 1.0
    return pd.DataFrame([
        {"Category": tx("gross"), "m2": round(b["gross"], 1), "Share": "100.0%"},
        {"Category": tx("net"), "m2": round(b["net"], 1), "Share": f"{b['net'] / gross * 100:.1f}%"},
        {"Category": tx("core"), "m2": round(b["core"], 1), "Share": f"{b['core'] / gross * 100:.1f}%"},
        {"Category": tx("tech"), "m2": round(b["tech"], 1), "Share": f"{b['tech'] / gross * 100:.1f}%"},
        {"Category": tx("circ"), "m2": round(b["circ"], 1), "Share": f"{b['circ'] / gross * 100:.1f}%"},
        {"Category": tx("common"), "m2": round(b["common"], 1), "Share": f"{b['common'] / gross * 100:.1f}%"},
        {"Category": "Saleable / sellable baseline", "m2": round(b["sale"], 1), "Share": f"{b['sale'] / gross * 100:.1f}%"},
        {"Category": "Lettable / leasable baseline", "m2": round(b["let"], 1), "Share": f"{b['let'] / gross * 100:.1f}%"},
    ])

def before_after_df(b: Dict[str, float], best: pd.Series) -> pd.DataFrame:
    rows = [
        (tx("gross"), b["gross"], b["gross"]),
        (tx("net"), b["net"], b["net"]),
        (tx("core"), b["core"], float(best["core_after"])),
        (tx("tech"), b["tech"], float(best["tech_after"])),
        (tx("circ"), b["circ"], float(best["circ_after"])),
        (tx("common"), b["common"], float(best["common_after"])),
        ("Saleable / sellable area", b["sale"], float(best["sale_after"])),
        ("Lettable / leasable area", b["let"], float(best["let_after"])),
        ("Yield ratio", round(b["yield"] * 100, 1), float(best["yield_after"])),
    ]
    out = []
    for label, before, after in rows:
        delta = after - before
        delta_pct = (delta / before * 100) if before else 0.0
        out.append({"Category": label, "Before": round(before, 1), "After": round(after, 1), "Delta": round(delta, 1), "Delta %": round(delta_pct, 1)})
    return pd.DataFrame(out)

def opportunities_df(analysis: Dict[str, Any], goal: str) -> pd.DataFrame:
    items = analysis.get("opportunities") or deterministic_findings(goal)
    return pd.DataFrame([
        {
            "Recommendation": clean_text(item.get("title")),
            "Effect": clean_text(item.get("effect")),
            "Confidence": clean_text(item.get("confidence", "Medium")),
            "Constraint": clean_text(item.get("constraint")),
        }
        for item in items if isinstance(item, dict)
    ])

def findings_df(analysis: Dict[str, Any]) -> pd.DataFrame:
    items = analysis.get("drawing_findings") or []
    return pd.DataFrame([
        {
            "Page": clean_text(item.get("page_label")),
            "Finding": clean_text(item.get("finding")),
            "Impact": clean_text(item.get("impact")),
            "Confidence": clean_text(item.get("confidence")),
            "Evidence": clean_text(item.get("evidence")),
        }
        for item in items if isinstance(item, dict)
    ])

def sources_df(drawings: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([
        {"File": clean_text(d.get("name")), "Source": clean_text(d.get("source")), "Hint": clean_text(d.get("hint")), "Previewable": "Yes" if isinstance(d.get("image"), Image.Image) else "No"}
        for d in drawings
    ])

def ai_report_text(analysis: Dict[str, Any], delivery_level: str, bdf: pd.DataFrame, sdf: pd.DataFrame, badf: pd.DataFrame, odf: pd.DataFrame) -> str:
    system_prompt = (
        "You are Builtly Area and Yield AI. "
        f"Write a practical, client-facing report in {REPORT_LANGUAGE}. "
        "Do not overclaim accuracy. State clearly what is based on current inputs and what must be verified. "
        "Use these section headings only: "
        "# 1. Summary and conclusion "
        "# 2. Data basis "
        "# 3. Goal and assumptions "
        "# 4. Current area profile "
        "# 5. Scenario analysis "
        "# 6. Drawing-based findings "
        "# 7. Recommended next steps"
    )
    payload = {
        "project": project,
        "delivery_level": delivery_level,
        "market_rule": market_rule_text(),
        "analysis": analysis,
        "baseline": bdf.to_dict(orient="records"),
        "scenarios": sdf.to_dict(orient="records"),
        "before_after": badf.to_dict(orient="records"),
        "opportunities": odf.to_dict(orient="records"),
    }
    user_prompt = "Write the report from this payload: " + json.dumps(payload, ensure_ascii=False)
    text, provider = ai_text(system_prompt, user_prompt, [], json_mode=False)
    if text.strip():
        return text
    best = sdf.loc[sdf["value_uplift"].idxmax()]
    return f"""# 1. Summary and conclusion
The preferred scenario is {best['Scenario']} with an estimated value uplift of {best['value_uplift']}.

# 2. Data basis
{clean_text(analysis.get('data_reason'))}

# 3. Goal and assumptions
- Delivery level: {delivery_level}
- Regulatory context: {market_rule_text()}
- All values are indicative and should be checked before external use.

# 4. Current area profile
- Gross area: {bdf.iloc[0]['m2']} m2
- Net area: {bdf.iloc[1]['m2']} m2
- Saleable baseline: {bdf.iloc[6]['m2']} m2
- Lettable baseline: {bdf.iloc[7]['m2']} m2

# 5. Scenario analysis
- Best scenario recommendation: {clean_text(best['Recommendation'])}
- Main check point: {clean_text(best['Constraint'])}

# 6. Drawing-based findings
- {clean_text((analysis.get('drawing_findings') or [{'finding':'Drawing-based findings were limited in this run.'}])[0].get('finding'))}

# 7. Recommended next steps
- Check the preferred scenario against fire, structure and MEP before you lock the plan.
- Verify the assumptions against the latest plan set or area schedule.
"""

class BuiltlyPDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_y(11)
        self.set_text_color(88, 94, 102)
        self.set_font("Helvetica", "", 8)
        self.cell(0, 4, clean_pdf_text(self.header_left), 0, 0, "L")
        self.cell(0, 4, clean_pdf_text(self.header_right), 0, 1, "R")
        self.set_draw_color(188, 192, 197)
        self.line(18, 18, 192, 18)
        self.set_y(24)
    def footer(self):
        self.set_y(-12)
        self.set_draw_color(210, 214, 220)
        self.line(18, 285, 192, 285)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(110, 114, 119)
        self.cell(60, 5, "Builtly-Yield-001", 0, 0, "L")
        self.cell(70, 5, clean_pdf_text(self.footer_center), 0, 0, "C")
        self.cell(0, 5, f"Page {self.page_no()}", 0, 0, "R")
    def ensure_space(self, needed: float):
        if self.get_y() + needed > 272:
            self.add_page()
    def rounded_rect(self, x, y, w, h, r, style=""):
        try:
            super().rounded_rect(x, y, w, h, r, style)
        except Exception:
            self.rect(x, y, w, h, style if style in {"F", "FD", "DF"} else "")
    def section_title(self, title: str):
        self.ensure_space(20)
        self.ln(2)
        title = clean_pdf_text(title)
        num_match = re.match(r"^(\d+\.?\d*)\s*(.*)$", title)
        if num_match and (num_match.group(1).endswith(".") or num_match.group(2)):
            number = num_match.group(1).rstrip(".")
            text = num_match.group(2).strip()
        else:
            number = None
            text = title
        self.set_font("Helvetica", "B", 17)
        self.set_text_color(36, 50, 72)
        sy = self.get_y()
        if number:
            self.set_xy(20, sy)
            self.cell(12, 8, number, 0, 0, "L")
            self.set_xy(34, sy)
            self.multi_cell(156, 8, clean_pdf_text(text.upper()), 0, "L")
        else:
            self.set_xy(20, sy)
            self.multi_cell(170, 8, clean_pdf_text(text.upper()), 0, "L")
        self.set_draw_color(204, 209, 216)
        self.line(20, self.get_y() + 1, 190, self.get_y() + 1)
        self.ln(5)
    def body_paragraph(self, text: str):
        self.set_x(20)
        self.set_font("Helvetica", "", 10.2)
        self.set_text_color(35, 39, 43)
        self.multi_cell(170, 5.5, clean_pdf_text(text))
        self.ln(1.5)
    def bullets(self, items: Sequence[str]):
        for item in items:
            self.ensure_space(10)
            sy = self.get_y()
            self.set_xy(22, sy)
            self.cell(6, 5.2, "-", 0, 0, "L")
            self.set_xy(28, sy)
            self.multi_cell(162, 5.2, clean_pdf_text(item))
            self.ln(0.8)
    def kv_card(self, items: Sequence[Tuple[str, str]], x=118, width=72, title=""):
        height = 10 + len(items) * 6.3 + (7 if title else 0)
        self.ensure_space(height + 3)
        sy = self.get_y()
        self.set_fill_color(245, 247, 249)
        self.set_draw_color(214, 219, 225)
        self.rounded_rect(x, sy, width, height, 4, "DF")
        yy = sy + 5
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
        self.set_y(max(self.get_y(), sy + height))
    def highlight_box(self, title: str, items: Sequence[str]):
        self.set_font("Helvetica", "", 10)
        text_h = 0
        for item in items:
            text_h += 7 + int(self.get_string_width(clean_pdf_text(item)) / 145) * 5
        box_h = 14 + text_h
        self.ensure_space(box_h + 5)
        x, y = 20, self.get_y()
        self.set_fill_color(245, 247, 250)
        self.set_draw_color(217, 223, 230)
        self.rounded_rect(x, y, 170, box_h, 4, "DF")
        self.set_fill_color(50, 77, 106)
        self.rect(x, y, 3, box_h, "F")
        self.set_xy(x + 6, y + 4)
        self.set_font("Helvetica", "B", 10.5)
        self.set_text_color(50, 77, 106)
        self.cell(0, 5, clean_pdf_text(title.upper()), 0, 1)
        self.set_text_color(35, 39, 43)
        self.set_font("Helvetica", "", 10)
        yy = y + 10
        for item in items:
            self.set_xy(x + 8, yy)
            self.cell(5, 5, "-", 0, 0)
            self.multi_cell(154, 5.2, clean_pdf_text(item))
            yy = self.get_y() + 2
        self.set_y(y + box_h + 3)
    def image_box(self, path: str, width=170, caption=""):
        img = Image.open(path)
        height = width * (img.height / img.width)
        self.ensure_space(height + 15)
        x, y = 20, self.get_y()
        self.image(path, x=x, y=y, w=width)
        self.set_y(y + height + 2)
        if caption:
            self.set_x(20)
            self.set_font("Helvetica", "I", 7.7)
            self.set_text_color(104, 109, 116)
            self.multi_cell(width, 4, clean_pdf_text(caption), 0, "L")
        self.ln(6)

def table_image(df: pd.DataFrame, title: str, subtitle: str = "") -> Image.Image:
    df = df.copy().fillna("")
    font_title = get_font(34, bold=True)
    font_sub = get_font(18, bold=False)
    font_head = get_font(18, bold=True)
    font_body = get_font(16, bold=False)
    side_pad = 28
    table_w = 1540
    weights = []
    for col in df.columns:
        low = clean_text(col).lower()
        if "delta" in low or "%" in low:
            weights.append(0.9)
        elif any(k in low for k in ["constraint", "recommend", "effect", "evidence"]):
            weights.append(2.2)
        else:
            weights.append(1.2)
    total = sum(weights) or 1
    col_ws = [max(100, int(table_w * w / total)) for w in weights]
    header_h = 48
    row_h = 36
    img_h = 180 + header_h + row_h * max(len(df), 1)
    img = Image.new("RGB", (table_w + side_pad * 2, img_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((12, 12, img.width - 12, img.height - 12), radius=18, fill=(255,255,255), outline=(219,225,232), width=2)
    draw.rounded_rectangle((18, 18, img.width - 18, 104), radius=16, fill=(236,240,245))
    draw.text((side_pad, 28), clean_pdf_text(title), font=font_title, fill=(29,45,68))
    if subtitle:
        draw.text((side_pad, 70), clean_pdf_text(subtitle), font=font_sub, fill=(96,108,122))
    x = side_pad
    y = 116
    for col, width in zip(df.columns, col_ws):
        draw.rectangle((x, y, x + width, y + header_h), fill=(46,62,84))
        draw.text((x + 10, y + 12), clean_pdf_text(col), font=font_head, fill=(255,255,255))
        x += width
    y += header_h
    for ridx, row in df.iterrows():
        x = side_pad
        fill = (255,255,255) if ridx % 2 == 0 else (248,250,252)
        for col, width in zip(df.columns, col_ws):
            draw.rectangle((x, y, x + width, y + row_h), fill=fill, outline=(205,212,220), width=1)
            text = short_text(row[col], 64)
            draw.text((x + 10, y + 10), clean_pdf_text(text), font=font_body, fill=(35,38,43))
            x += width
        y += row_h
    return img

def save_temp(img: Image.Image, suffix: str = ".png") -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    img.save(tmp.name)
    tmp.close()
    return tmp.name

def split_sections(text: str) -> List[Dict[str, Any]]:
    sections = []
    current = None
    for raw in text.splitlines():
        line = clean_text(raw)
        if line.startswith("#"):
            if current:
                sections.append(current)
            current = {"title": clean_text(line.lstrip("#")), "lines": []}
        elif current is not None:
            current["lines"].append(line)
    if current:
        sections.append(current)
    return sections

def report_pdf(project: Dict[str, Any], level: str, report_text: str, before_df: pd.DataFrame, scenario_df: pd.DataFrame, findings: pd.DataFrame, sources: pd.DataFrame, cover: Optional[Image.Image]) -> Optional[bytes]:
    if FPDF is None:
        return None
    pdf = BuiltlyPDF("P", "mm", "A4")
    pdf.set_auto_page_break(True, margin=22)
    pdf.set_margins(18, 18, 18)
    pdf.header_left = clean_pdf_text(project.get("p_name", tx("module")))
    pdf.header_right = clean_pdf_text(tx("module"))
    pdf.footer_center = clean_pdf_text(tx("disclaimer_auto")[:42] + "...")
    pdf.add_page()
    logo = ROOT / "logo.png"
    if logo.exists():
        try:
            pdf.image(str(logo), x=150, y=15, w=40)
        except Exception:
            pass
    pdf.set_xy(20, 45)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(100, 105, 110)
    pdf.cell(80, 6, clean_pdf_text(tx("report_title").upper()), 0, 1, "L")
    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 34)
    pdf.set_text_color(20, 28, 38)
    pdf.multi_cell(95, 12, clean_pdf_text(project.get("p_name", tx("module"))), 0, "L")
    pdf.ln(4)
    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(64, 68, 74)
    pdf.multi_cell(95, 6.5, clean_pdf_text(tx("report_subject")), 0, "L")
    pdf.set_xy(118, 45)
    pdf.kv_card([
        ("Client", clean_text(project.get("c_name")) or "-"),
        ("Module", clean_text(tx("module"))),
        ("Date / rev", datetime.now().strftime("%d.%m.%Y") + " / 01"),
        ("Country", market_code()),
        ("Level", level),
    ], title="Meta")
    if cover is not None:
        path = save_temp(cover.convert("RGB"), ".jpg")
        pdf.image_box(path, width=165, caption=tx("analysis_caption"))
    else:
        pdf.highlight_box(tx("drawings"), [tx("need_input")])
    disclaimer = tx("disclaimer_auto") if level == tx("levels")[0] else tx("disclaimer_review")
    pdf.set_xy(20, 252)
    pdf.set_font("Helvetica", "", 8.8)
    pdf.set_text_color(104, 109, 116)
    pdf.multi_cell(170, 4.5, clean_pdf_text(disclaimer))

    pdf.add_page()
    pdf.section_title("Contents")
    for item in [
        "1. Summary and conclusion",
        "2. Data basis",
        "3. Goal and assumptions",
        "4. Current area profile",
        "5. Scenario analysis",
        "6. Drawing-based findings",
        "7. Recommended next steps",
        "Appendix A. Before and after",
        "Appendix B. Scenario matrix",
        "Appendix C. Source files",
    ]:
        y = pdf.get_y()
        pdf.set_x(22)
        pdf.cell(0, 6, clean_pdf_text(item), 0, 0, "L")
        pdf.set_draw_color(225, 229, 234)
        pdf.line(22, y + 6, 188, y + 6)
        pdf.ln(8)
    pdf.ln(6)
    pdf.highlight_box(tx("what_you_get"), [clean_text(x) for x in tx("what_items")])

    pdf.add_page()
    for section in split_sections(report_text):
        pdf.section_title(section["title"])
        bullets = []
        para = []
        for line in section["lines"]:
            if not line:
                continue
            if re.match(r"^[-*•]\s+", line):
                if para:
                    pdf.body_paragraph(" ".join(para))
                    para = []
                bullets.append(re.sub(r"^[-*•]\s+", "", line))
            else:
                if bullets:
                    pdf.bullets(bullets)
                    bullets = []
                para.append(line)
        if para:
            pdf.body_paragraph(" ".join(para))
        if bullets:
            pdf.bullets(bullets)

    for title, df, subtitle in [
        ("Appendix A. Before and after", before_df, "Preferred scenario compared with the current baseline"),
        ("Appendix B. Scenario matrix", scenario_df[["Scenario", "Recommendation", "sale_gain", "let_gain", "value_uplift", "Constraint", "Source"]], "Three generated scenarios"),
        ("Appendix C. Drawing findings", findings if not findings.empty else pd.DataFrame([{"Finding": "No strong drawing findings were available in this run."}]), tx("find_sub")),
        ("Appendix D. Source files", sources if not sources.empty else pd.DataFrame([{"File": "No files uploaded"}]), tx("drawings")),
    ]:
        pdf.add_page()
        pdf.section_title(title)
        img = table_image(df, title, subtitle)
        path = save_temp(img, ".png")
        pdf.image_box(path, width=170, caption=subtitle)

    out = pdf.output(dest="S")
    return bytes(out) if isinstance(out, (bytes, bytearray)) else out.encode("latin-1")

def excel_bytes(frames: Dict[str, pd.DataFrame]) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        for name, frame in frames.items():
            safe = clean_text(name)[:31] or "Sheet1"
            frame.to_excel(writer, sheet_name=safe, index=False)
            ws = writer.book[safe]
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(12, width + 2), 48)
    bio.seek(0)
    return bio.getvalue()

left, right = st.columns([1.35, 0.65], gap="large")

with left:
    render_section(tx("module"), tx("find_sub"), tx("goal"))
    use_label = st.selectbox(tx("use"), tx("uses"), index=0)
    goal_label = st.selectbox(tx("goal"), tx("goals"), index=1)
    mode_label = st.selectbox(tx("mode"), tx("modes"), index=1)
    level_label = st.selectbox(tx("level"), tx("levels"), index=0)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        gross = st.number_input(tx("gross"), min_value=150.0, value=float(project.get("bta", 2500) or 2500), step=50.0)
        floors = st.number_input(tx("floors"), min_value=1, value=int(project.get("etasjer", 4) or 4), step=1)
    with c2:
        net = st.number_input(tx("net"), min_value=0.0, value=round(float(gross) * 0.81, 1), step=10.0)
        value_per_m2 = st.number_input(tx("value"), min_value=1000.0, value=45000.0 if current_currency() in {"NOK", "SEK", "DKK"} else 4500.0, step=500.0)
    with c3:
        core = st.number_input(tx("core"), min_value=0.0, value=round(float(gross) * 0.10, 1), step=5.0)
        tech = st.number_input(tx("tech"), min_value=0.0, value=round(float(gross) * 0.08, 1), step=5.0)
    with c4:
        circ = st.number_input(tx("circ"), min_value=0.0, value=round(float(gross) * 0.12, 1), step=5.0)
        common = st.number_input(tx("common"), min_value=0.0, value=round(float(gross) * 0.06, 1), step=5.0)

    friction = st.slider(tx("friction"), 0, 10, 4)
    render_section(tx("constraints"), market_rule_text(), tx("constraints"))
    selected_constraints = []
    cols = st.columns(4)
    for idx, label in enumerate(tx("constraint_labels")):
        with cols[idx]:
            if st.checkbox(label, value=True):
                selected_constraints.append(label)

    render_section(tx("drawings"), tx("drawing_help"), tx("drawings"))
    saved = saved_drawings()
    if saved:
        img_count = len([d for d in saved if isinstance(d.get("image"), Image.Image)])
        pdf_originals = len([d for d in saved if "PDF original" in (d.get("source") or "")])
        file_count = len([d for d in saved if not isinstance(d.get("image"), Image.Image)])
        if UI_LANG == "no":
            if pdf_originals:
                msg = f"{img_count} tegning(er) hentet fra prosjektoppsettet i full oppløsning (fra original-PDF)."
            else:
                msg = f"{img_count} tegning(er) hentet fra prosjektoppsettet."
            if file_count:
                msg += f" {file_count} fil(er) uten forhåndsvisning (DWG/IFC/CAD) er også registrert."
            msg += " Du kan laste opp flere under."
            st.success(msg)
        else:
            if pdf_originals:
                msg = f"{img_count} drawing(s) loaded from Project Setup at full resolution (from original PDF)."
            else:
                msg = f"{img_count} drawing(s) loaded from Project Setup."
            if file_count:
                msg += f" {file_count} file(s) without preview (DWG/IFC/CAD) also registered."
            msg += " You can upload more below."
            st.success(msg)
        cols = st.columns(min(3, len(saved)))
        for idx, record in enumerate(saved[:6]):
            with cols[idx % len(cols)]:
                if isinstance(record.get("image"), Image.Image):
                    st.image(record["image"], caption=f"{record['name']} ({record['hint']})", use_container_width=True)
    else:
        if UI_LANG == "no":
            st.info("Ingen tegninger funnet fra prosjektoppsettet. Last opp tegninger under, eller ga til Project Setup og last opp der forst.")
        else:
            st.info("No drawings found from Project Setup. Upload drawings below, or go to Project Setup and upload there first.")

    if UI_LANG == "no":
        _extra_label = "Last opp flere tegninger (valgfritt)" if saved else tx("drawings")
    else:
        _extra_label = "Upload additional drawings (optional)" if saved else tx("drawings")
    files = st.file_uploader(_extra_label, type=["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "pdf", "dwg", "dxf", "zip"], accept_multiple_files=True, help=tx("drawing_help"))
    uploads = uploaded_drawings(files or [])
    if uploads:
        cols = st.columns(min(3, len([u for u in uploads if isinstance(u.get("image"), Image.Image)]) or 1))
        preview = [u for u in uploads if isinstance(u.get("image"), Image.Image)]
        for idx, record in enumerate(preview[:6]):
            with cols[idx % len(cols)]:
                st.image(record["image"], caption=f"{record['name']} ({record['hint']})", use_container_width=True)

    schedule_file = st.file_uploader(tx("schedule"), type=["xlsx", "xls", "csv"], accept_multiple_files=False)
    schedule_raw, schedule_summary = parse_schedule(schedule_file)
    if schedule_summary is not None:
        st.dataframe(schedule_summary, use_container_width=True, hide_index=True)

    base = baseline_metrics(gross, net, core, tech, circ, common)
    render_metric_cards([
        {"label": tx("yield_now"), "value": f"{base['yield'] * 100:.1f}%", "desc": "Share of value-creating area in the current layout." if UI_LANG == "en" else "Andel verdiskapende areal i dagens løsning."},
        {"label": tx("service"), "value": f"{base['service'] * 100:.1f}%", "desc": "Core, technical rooms and circulation." if UI_LANG == "en" else "Kjerne, tekniske rom og kommunikasjon."},
        {"label": tx("data"), "value": tx("data_labels")["strong" if [d for d in saved + uploads if isinstance(d.get('image'), Image.Image)] else "partial"], "desc": current_currency()},
        {"label": tx("value_up"), "value": fmt_money(value_per_m2), "desc": "Input value basis" if UI_LANG == "en" else "Verdi per m2"},
    ])

    if st.button(tx("generate"), type="primary", use_container_width=True):
        if gross <= 0 or net <= 0:
            st.warning(tx("need_input"))
        else:
            with st.spinner(tx("running")):
                goal = goal_code(goal_label)
                mode = mode_code(mode_label)
                all_drawings = saved + uploads
                analysis, provider = visual_analysis(all_drawings, base, goal, mode, selected_constraints)
                scenario_data = build_scenario_rows(base, goal, mode, value_per_m2, friction, analysis)
                best = best_scenario(scenario_data, goal)
                bdf = baseline_df(base)
                badf = before_after_df(base, best)
                odf = opportunities_df(analysis, goal)
                fdf = findings_df(analysis)
                sdf = sources_df(all_drawings)
                notes = [clean_text(item.get("finding")) for item in (analysis.get("drawing_findings") or [])[:3] if isinstance(item, dict)]
                cover = annotate_preview((saved + uploads)[0], notes, goal_label) if [d for d in saved + uploads if isinstance(d.get("image"), Image.Image)] else None
                report_text = ai_report_text(analysis, level_label, bdf, scenario_data, badf, odf)
                pdf = report_pdf(project, level_label, report_text, badf, scenario_data, fdf, sdf, cover)
                xlsx = excel_bytes({"Baseline": bdf, "Scenarios": scenario_data, "Before_After": badf, "Opportunities": odf, "Drawing_Findings": fdf, "Sources": sdf, "Area_Schedule": schedule_summary if schedule_summary is not None else pd.DataFrame()})
                st.session_state["yield_bundle"] = {
                    "analysis": analysis,
                    "provider": provider,
                    "baseline_df": bdf,
                    "scenario_df": scenario_data,
                    "before_after_df": badf,
                    "opportunities_df": odf,
                    "findings_df": fdf,
                    "sources_df": sdf,
                    "report_text": report_text,
                    "pdf": pdf,
                    "xlsx": xlsx,
                    "json": json.dumps({"project": project, "analysis": analysis, "scenarios": scenario_data.to_dict(orient="records"), "before_after": badf.to_dict(orient="records")}, ensure_ascii=False, indent=2).encode("utf-8"),
                    "cover": cover,
                    "goal": goal,
                }
            st.rerun()

with right:
    render_project_snapshot(project, badge="SSOT synced" if UI_LANG == "en" else "SSOT synkronisert")
    render_panel(tx("what_you_get"), tx("ai_note"), tx("what_items"), tone="blue", badge=tx("module"))
    render_panel(tx("how_use"), market_rule_text(), tx("how_items"), tone="gold", badge=tx("drawings"))

bundle = st.session_state.get("yield_bundle")
if not bundle:
    st.info(tx("empty"))
else:
    st.success(tx("ready"))
    m_best = best_scenario(bundle["scenario_df"], bundle["goal"])
    render_metric_cards([
        {"label": tx("best"), "value": clean_text(m_best["Scenario"]), "desc": clean_text(m_best["Recommendation"])},
        {"label": tx("sale_gain"), "value": f"{float(m_best['sale_gain']):.1f} m2", "desc": tx("goals")[0]},
        {"label": tx("let_gain"), "value": f"{float(m_best['let_gain']):.1f} m2", "desc": tx("goals")[1]},
        {"label": tx("value_up"), "value": fmt_money(float(m_best["value_uplift"])), "desc": tx("data_labels").get(bundle["analysis"].get("data_status", "partial"), bundle["analysis"].get("data_status", "partial"))},
    ])

    d1, d2, d3 = st.columns(3, gap="small")
    with d1:
        if bundle.get("pdf"):
            st.download_button(tx("dl_pdf"), bundle["pdf"], file_name="builtly_yield_report.pdf", mime="application/pdf", use_container_width=True)
    with d2:
        st.download_button(tx("dl_xlsx"), bundle["xlsx"], file_name="builtly_yield_before_after.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    with d3:
        st.download_button(tx("dl_json"), bundle["json"], file_name="builtly_yield_analysis.json", mime="application/json", use_container_width=True)

    tabs = st.tabs([tx("baseline"), tx("scenarios"), tx("before_after"), tx("findings"), tx("report"), tx("sources")])
    with tabs[0]:
        st.dataframe(bundle["baseline_df"], use_container_width=True, hide_index=True)
        if schedule_summary is not None:
            st.dataframe(schedule_summary, use_container_width=True, hide_index=True)
    with tabs[1]:
        scen = bundle["scenario_df"][["Scenario", "Recommendation", "sale_gain", "let_gain", "freed_area", "value_uplift", "Constraint", "Source"]].copy()
        st.dataframe(scen, use_container_width=True, hide_index=True)
        if not bundle["opportunities_df"].empty:
            st.dataframe(bundle["opportunities_df"], use_container_width=True, hide_index=True)
    with tabs[2]:
        st.dataframe(bundle["before_after_df"], use_container_width=True, hide_index=True)
    with tabs[3]:
        if not bundle["findings_df"].empty:
            st.dataframe(bundle["findings_df"], use_container_width=True, hide_index=True)
        else:
            st.info(tx("drawing_help"))
        if bundle.get("cover") is not None:
            st.image(bundle["cover"], caption=tx("analysis_caption"), use_container_width=True)
    with tabs[4]:
        st.markdown(bundle["report_text"].replace("\n", "  \n"))
        if bundle.get("provider"):
            st.caption("AI provider: " + bundle["provider"])
    with tabs[5]:
        st.dataframe(bundle["sources_df"], use_container_width=True, hide_index=True)
