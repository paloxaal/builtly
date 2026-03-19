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




def prefill_from_docs(client_type, client, doc_text: str) -> dict:
    """AI leser opplastede dokumenter og returnerer strukturerte prosjektdata for forhåndsutfylling."""
    prompt = """Du er en norsk kredittanalytiker. Les dokumentteksten og ekstraher all prosjekt- og finansieringsdata.
Returner KUN gyldig JSON med disse feltene (null for ukjente):
{
  "prosjekt_navn": "",
  "laantaker": "",
  "orgnr": "",
  "laanetype": "Byggelån",
  "soekt_laan_mnok": 0.0,
  "totalinvestering_mnok": 0.0,
  "egenkapital_mnok": 0.0,
  "prosjekttype": "Bolig - salg",
  "entrepriseform": "Totalentreprise",
  "antall_enheter": 0,
  "bra_i_kvm": 0,
  "tomt_kvm": 0,
  "gnr_bnr": "",
  "kommune": "",
  "planident": "",
  "tomtekost_mnok": 0.0,
  "entreprisekost_mnok": 0.0,
  "forhaandssalg_pst": 0,
  "inntekt_mnok": 0.0,
  "forventet_salgspris_kvm": 0,
  "byggekost_kvm_bra_i": 0,
  "nibor_margin_pst": 0.0,
  "provisjon_pst_kvartal": 0.0,
  "etableringsgebyr_nok": 0,
  "loepetid_mnd": 0,
  "tomtelaan_mnok": 0.0,
  "tomtelaan_tomt_takst_mnok": 0.0,
  "infralaan_mnok": 0.0,
  "kausjoner": [],
  "vilkaar_foer_utbetaling": [],
  "covenants_liste": [],
  "spesielle_forhold": ""
}

KRITISK for kausjoner — les NØYE:
- Finn ALLE selskaper/personer som stiller kausjon, garanti eller sikkerhet i dokumentet
- For hvert selskap: hent fullstendig navn OG organisasjonsnummer (org.nr.) fra dokumentet
- Eks: "Selvskyldnerkausjon fra Fredensborg Bolig AS (org.nr. 919 998 296) pålydende 70 MNOK" → en rad
- Eks: "Selvskyldnerkausjon fra Bolig Norge AS (org.nr. 923 733 345) pålydende 70 MNOK" → en ny rad
- Hvis to selskaper nevnes som kausjonister, skal "kausjoner" ha TO elementer i listen
- Hvert element: {"kausjonist": "Selskapsnavn AS", "orgnr": "XXX XXX XXX", "beloep_mnok": 70.0, "type": "Selvskyldner", "kommentar": ""}
- "type" er alltid "Selvskyldner" med mindre "simpel" eksplisitt nevnes
- Returner ALDRI en tom kausjoner-liste hvis det finnes kausjonister i dokumentet

KRITISK for gnr/bnr og matrikkeldata:
- Finn ALLE gnr (gårdsnummer) og bnr (bruksnummer) som nevnes som panteobjekt
- Format: "Gnr. 81, bnr. 56, 57, 10, 154, 155, 156"
- Disse er bankens panteobjekt og er kritisk informasjon

KRITISK for finansieringsstruktur:
- Prosjekter kan ha FLERE lånetyper: byggelån, tomtelån og infralån
- Tomtelån: separat lån for tomtekjøp/areal som ikke bygges på ennå
- Infralån: forskuttering av infrastruktur som betjener flere byggetrinn
  Banken behandler infralån som tomtelån — egen trekkramme, trekkes løpende, samlet LTV med tomtelån maks 70%
- Byggelån: lån for selve byggeprosjektet (produksjon)
- VIKTIG: Infra-andelen kan ligge INNENFOR byggelånet i søknaden!
  Eks: "Byggelån BT3 NOK 532 000 000, hvor av forskuttering infra utgjør 77,357m"
  → soekt_laan_mnok = 532 (hele søkte byggelånet som det står i søknaden, INKL infra)
  → infralaan_mnok = 77.357 (infra-andelen som banken skiller ut og behandler som tomtelån)
  → Reelt byggelån for LTV-beregning = 532 - 77.357 = 454.643 MNOK
- "soekt_laan_mnok" = hele søkte beløpet som oppgitt i søknaden (kan inkludere infra)
- "tomtelaan_mnok" = eventuelt separat tomtelån
- "infralaan_mnok" = infra-andel (enten separat eller utskilt fra byggelånet)
- Sett laanetype til "Kombinert tomte- og byggelån" hvis flere lånetyper finnes

VIKTIG: BRA-i er det salgbare innendørsarealet (SBRA) brukt for entreprisekost og salgsinntekt per kvm.
Dokumenttekst:
""" + doc_text[:60000]
    try:
        if client_type == "openai":
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=3000,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        elif client_type == "gemini":
            resp = client.generate_content(prompt,
                generation_config={"temperature": 0.1, "max_output_tokens": 3000})
            text = resp.text.strip()
            text = re.sub(r"^```json\s*", "", text); text = re.sub(r"\s*```$", "", text)
            return json.loads(text)
    except Exception as e:
        st.warning(f"Forhåndsutfylling feilet: {e}")
    return {}


def lookup_company(orgnr_or_name: str) -> dict:
    """
    Slår opp selskapsinfo fra Brønnøysundregistrenes åpne API-er:
    - Enhetsregisteret: navn, bransje, adresse, stiftelsesdato, konkurs/avvikling
    - Regnskapsregisteret: siste 2 år med omsetning, driftsresultat, årsresultat, EK, totalkapital
    - Roller: styreleder, daglig leder
    Ingen API-nøkkel nødvendig – alle API-er er åpne.
    """
    import urllib.request, urllib.parse
    result = {
        "navn": "", "orgnr": "", "organisasjonsform": "", "bransje": "",
        "adresse": "", "stiftelsesaar": "", "ansatte": "",
        "konkurs": False, "under_avvikling": False,
        "regnskap": [],          # liste med årsregnskap
        "styreleder": "", "daglig_leder": "",
        "kilde": "", "feil": "",
    }
    if not orgnr_or_name.strip():
        return result

    headers = {"Accept": "application/json",
               "User-Agent": "Builtly-Kredittgrunnlag/1.0 (kontakt@builtly.ai)"}

    def _get(url: str) -> dict | list | None:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            return None

    def _fmt_nok(val) -> str:
        """Formater NOK-beløp fra Brreg (i hele kroner) til lesbar streng."""
        if val is None:
            return "-"
        try:
            v = int(val)
            if abs(v) >= 1_000_000:
                return f"{v/1_000_000:.1f} MNOK"
            elif abs(v) >= 1_000:
                return f"{v/1_000:.0f} TNOK"
            return f"{v} kr"
        except Exception:
            return str(val)

    # ── 1. Enhetsregisteret ─────────────────────────────────────────────────
    clean = re.sub(r"\s", "", orgnr_or_name.strip())
    if clean.isdigit() and len(clean) == 9:
        enhet = _get(f"https://data.brreg.no/enhetsregisteret/api/enheter/{clean}")
    else:
        q = urllib.parse.quote(orgnr_or_name.strip())
        resp = _get(f"https://data.brreg.no/enhetsregisteret/api/enheter?navn={q}&size=1")
        enheter = (resp or {}).get("_embedded", {}).get("enheter", [])
        enhet = enheter[0] if enheter else None

    if not enhet:
        result["feil"] = "Fant ikke selskap i Enhetsregisteret"
        return result

    orgnr_found = str(enhet.get("organisasjonsnummer", ""))
    result["navn"]              = enhet.get("navn", "")
    result["orgnr"]             = orgnr_found
    result["organisasjonsform"] = enhet.get("organisasjonsform", {}).get("beskrivelse", "") if isinstance(enhet.get("organisasjonsform"), dict) else ""
    result["bransje"]           = enhet.get("naeringskode1", {}).get("beskrivelse", "") if isinstance(enhet.get("naeringskode1"), dict) else ""
    result["ansatte"]           = str(enhet.get("antallAnsatte", "") or "")
    result["konkurs"]           = bool(enhet.get("konkurs"))
    result["under_avvikling"]   = bool(enhet.get("underAvvikling"))
    result["stiftelsesaar"]     = str(enhet.get("stiftelsesdato", "") or "")[:4]
    adr = enhet.get("forretningsadresse", {})
    if isinstance(adr, dict):
        parts = [", ".join(adr.get("adresse", [])), adr.get("postnummer", ""), adr.get("poststed", ""), adr.get("kommune", "")]
        result["adresse"] = " ".join(p for p in parts if p).strip()
    result["kilde"] = "Brreg Enhetsregisteret"

    # ── 2. Regnskapsregisteret ─────────────────────────────────────────────
    regnskap_data = _get(f"https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr_found}")
    if regnskap_data and isinstance(regnskap_data, list):
        # Sort by year descending, take last 3
        def _year(r):
            try: return int(str(r.get("regnskapsperiode", {}).get("tilDato", r.get("regnskapsperiode", {}).get("tilOgMed", "0")))[:4])
            except: return 0
        sorted_r = sorted(regnskap_data, key=_year, reverse=True)[:3]
        parsed_regnskap = []
        for r in sorted_r:
            rp = r.get("regnskapsperiode", {}) or {}
            aar = str(rp.get("tilDato", rp.get("tilOgMed", "")))[:4]

            # ── Resultatregnskap ──
            res_r = r.get("resultatregnskapResultat", {}) or {}
            drift = res_r.get("driftsresultat", {}) or {}
            driftsinnt = res_r.get("driftsinntekter", {}) or {}

            # Omsetning: try multiple paths
            omsetning_raw = (
                res_r.get("sumInntekter")
                or (driftsinnt.get("sumDriftsinntekter") if isinstance(driftsinnt, dict) else None)
                or (driftsinnt.get("salgsinntekter") if isinstance(driftsinnt, dict) else None)
                or res_r.get("driftsinntekter") if not isinstance(res_r.get("driftsinntekter"), dict) else None
            )

            aarsresultat_raw = (
                res_r.get("aarsresultat")
                or res_r.get("totalresultat")
                or res_r.get("ordinaertResultatFoerSkattekostnad")
            )

            driftsresultat_raw = (
                drift.get("driftsresultat") if isinstance(drift, dict) else None
            )

            # ── Balanse — try multiple structures ──
            # Structure A: "egenkapitalGjeld" (most common in Brreg API)
            ekg = r.get("egenkapitalGjeld", {}) or {}
            ek_section = ekg.get("egenkapital", {}) or {}
            gjeld_section = ekg.get("gjeldOversikt", ekg.get("gjeld", {})) or {}

            # Structure B: "balanseregnskapSumVerdier" (legacy/alternative)
            bal_r = r.get("balanseregnskapSumVerdier", {}) or {}

            # Structure C: "eiendeler"
            eiendeler = r.get("eiendeler", {}) or {}

            # EK: try all paths
            ek_raw = (
                ek_section.get("sumEgenkapital") if isinstance(ek_section, dict) else None
            ) or (
                ekg.get("sumEgenkapital")
            ) or (
                bal_r.get("sumEgenkapital")
            )

            # Totalkapital: try all paths
            totalkapital_raw = (
                ekg.get("sumEgenkapitalOgGjeld")
            ) or (
                bal_r.get("sumEgenkapitalOgGjeld")
            ) or (
                bal_r.get("sumGjeldOgEgenkapital")
            ) or (
                eiendeler.get("sumEiendeler")
            )

            # Gjeld: try all paths
            gjeld_raw = (
                gjeld_section.get("sumGjeld") if isinstance(gjeld_section, dict) else None
            ) or (
                ekg.get("sumGjeld")
            ) or (
                bal_r.get("sumGjeld")
            )

            # If we have totalkapital and EK but no gjeld, calculate it
            if not gjeld_raw and totalkapital_raw and ek_raw:
                try:
                    gjeld_raw = int(totalkapital_raw) - int(ek_raw)
                except (ValueError, TypeError):
                    pass

            # If we have EK and gjeld but no totalkapital, calculate it
            if not totalkapital_raw and ek_raw and gjeld_raw:
                try:
                    totalkapital_raw = int(ek_raw) + int(gjeld_raw)
                except (ValueError, TypeError):
                    pass

            parsed_regnskap.append({
                "aar": aar,
                "omsetning": _fmt_nok(omsetning_raw),
                "driftsresultat": _fmt_nok(driftsresultat_raw),
                "aarsresultat": _fmt_nok(aarsresultat_raw),
                "egenkapital": _fmt_nok(ek_raw),
                "totalkapital": _fmt_nok(totalkapital_raw),
                "gjeld": _fmt_nok(gjeld_raw),
                "ek_andel": f"{round(int(ek_raw or 0)/int(totalkapital_raw or 1)*100, 1)}%" if ek_raw and totalkapital_raw else "-",
                # Raw values for compute_traffic_light
                "_omsetning_nok": omsetning_raw,
                "_ek_nok": ek_raw,
                "_aarsresultat_nok": aarsresultat_raw,
            })
        result["regnskap"] = parsed_regnskap
        if parsed_regnskap:
            result["kilde"] += " + Regnskapsregisteret"

    # ── 3. Roller (styreleder + daglig leder) ─────────────────────────────
    roller_data = _get(f"https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr_found}/roller")
    if roller_data and isinstance(roller_data, dict):
        roller_list = roller_data.get("rollegrupper", [])
        for gruppe in roller_list:
            kode = gruppe.get("type", {}).get("kode", "")
            for rolle in gruppe.get("roller", []):
                person = rolle.get("person", {}) or {}
                navn_p = person.get("navn", {}) or {}
                fullt = f"{navn_p.get('fornavn', '')} {navn_p.get('etternavn', '')}".strip()
                if kode == "STYR" and not result["styreleder"]:
                    result["styreleder"] = fullt
                elif kode == "DAGL" and not result["daglig_leder"]:
                    result["daglig_leder"] = fullt

    return result



def analyse_kausjonist(orgnr_or_name: str, kausjon_beloep_mnok: float) -> dict:
    """
    Henter regnskap fra Brreg og gjør en utvidet soliditetsanalyse av kausjonisten.
    Inkluderer: EK-dekning, gjeldsgrad, totalkapital, lønnsomhet, trend.
    Returnerer vurdering: sterk / akseptabel / svak / ukjent.
    """
    si = lookup_company(orgnr_or_name)
    if not si.get("navn") or not si.get("regnskap"):
        return {"navn": si.get("navn", orgnr_or_name), "orgnr": si.get("orgnr", ""), "vurdering": "ukjent",
                "farge": "#9fb0c3", "begrunnelse": ["Regnskapstall ikke tilgjengelig i Brreg."], "regnskap": []}

    reg = si["regnskap"]  # siste 3 år, nyeste først
    siste = reg[0]

    ek_raw     = siste.get("_ek_nok") or 0
    res_raw    = siste.get("_aarsresultat_nok") or 0
    omset_raw  = siste.get("_omsetning_nok") or 0

    ek_mnok    = round(ek_raw / 1_000_000, 1)
    res_mnok   = round(res_raw / 1_000_000, 1)
    omset_mnok = round(omset_raw / 1_000_000, 1)

    # Parse totalkapital og gjeld fra regnskapet
    tk_str = siste.get("totalkapital", "-")
    gjeld_str = siste.get("gjeld", "-")
    tk_mnok = None
    gjeld_mnok = None
    gjeldsgrad = None

    try:
        tk_val = tk_str.replace("MNOK", "").replace("TNOK", "").replace("kr", "").replace(",", ".").strip()
        if "MNOK" in siste.get("totalkapital", ""):
            tk_mnok = float(tk_val)
        elif "TNOK" in siste.get("totalkapital", ""):
            tk_mnok = float(tk_val) / 1000
    except Exception:
        pass

    try:
        gj_val = gjeld_str.replace("MNOK", "").replace("TNOK", "").replace("kr", "").replace(",", ".").strip()
        if "MNOK" in siste.get("gjeld", ""):
            gjeld_mnok = float(gj_val)
        elif "TNOK" in siste.get("gjeld", ""):
            gjeld_mnok = float(gj_val) / 1000
    except Exception:
        pass

    if gjeld_mnok is not None and ek_mnok and ek_mnok > 0:
        gjeldsgrad = round(gjeld_mnok / ek_mnok, 2)

    # Nøkkeltall
    ek_vs_kausjon = round(ek_mnok / kausjon_beloep_mnok, 1) if kausjon_beloep_mnok > 0 else None

    punkter = []
    score = 0  # 0=ukjent, 1=svak, 2=akseptabel, 3=sterk

    # EK-dekning av kausjonsbeløp
    if ek_mnok > 0 and kausjon_beloep_mnok > 0:
        if ek_vs_kausjon >= 3:
            punkter.append(f"✅ Egenkapital {siste['egenkapital']} dekker {ek_vs_kausjon:.1f}x kausjonsbeløpet – sterk dekning")
            score = max(score, 3)
        elif ek_vs_kausjon >= 1.5:
            punkter.append(f"⚠️ Egenkapital {siste['egenkapital']} dekker {ek_vs_kausjon:.1f}x kausjonsbeløpet – akseptabel dekning")
            score = max(score, 2)
        elif ek_vs_kausjon >= 1:
            punkter.append(f"⚠️ Egenkapital {siste['egenkapital']} dekker akkurat kausjonsbeløpet ({ek_vs_kausjon:.1f}x) – begrenset buffer")
            score = max(score, 1)
        else:
            punkter.append(f"🔴 Egenkapital {siste['egenkapital']} dekker kun {ek_vs_kausjon:.1f}x av kausjonsbeløpet – utilstrekkelig")
            score = max(score, 1)
    elif ek_mnok < 0:
        punkter.append(f"🔴 Negativ egenkapital {siste['egenkapital']} – kausjonisten er teknisk insolvent")
        score = max(score, 1)

    # Gjeldsgrad
    if gjeldsgrad is not None:
        if gjeldsgrad <= 2:
            punkter.append(f"✅ Gjeldsgrad {gjeldsgrad:.1f}x – moderat gearing")
            score = max(score, 2)
        elif gjeldsgrad <= 5:
            punkter.append(f"⚠️ Gjeldsgrad {gjeldsgrad:.1f}x – høy gearing, men kan aksepteres for eiendomsselskap")
        elif gjeldsgrad <= 10:
            punkter.append(f"⚠️ Gjeldsgrad {gjeldsgrad:.1f}x – svært høy gearing")
            score = min(score, 2) if score > 0 else 1
        else:
            punkter.append(f"🔴 Gjeldsgrad {gjeldsgrad:.1f}x – ekstremt høy gearing, svekker kausjonsverdi")
            score = min(score, 1)

    # Totalkapital (størrelse)
    if tk_mnok is not None:
        if tk_mnok >= 1000:
            punkter.append(f"✅ Totalkapital {siste['totalkapital']} – stort og veletablert selskap")
        elif tk_mnok >= 100:
            punkter.append(f"✅ Totalkapital {siste['totalkapital']} – betydelig balanse")
        elif tk_mnok >= 10:
            punkter.append(f"⚠️ Totalkapital {siste['totalkapital']} – begrenset balanse relativt til kausjonsbeløp")

    # Total gjeld
    if gjeld_mnok is not None and kausjon_beloep_mnok > 0:
        gjeld_vs_kausjon = round(gjeld_mnok / kausjon_beloep_mnok, 1) if kausjon_beloep_mnok > 0 else None
        if gjeld_vs_kausjon and gjeld_vs_kausjon > 50:
            punkter.append(f"⚠️ Total gjeld {siste['gjeld']} ({gjeld_vs_kausjon:.0f}x kausjonsbeløpet) – vurder eksponering mot andre forpliktelser")

    # Omsetning
    if omset_mnok > 0:
        punkter.append(f"📊 Omsetning {siste['omsetning']} ({siste['aar']})")
    else:
        punkter.append(f"⚠️ Ingen rapportert omsetning ({siste['aar']}) – typisk holdingselskap")

    # Lønnsomhet siste år
    if res_mnok > 0:
        punkter.append(f"✅ Positiv årsresultat {siste['aarsresultat']} ({siste['aar']})")
        score = max(score, 2)
    elif res_mnok < 0:
        punkter.append(f"⚠️ Negativt årsresultat {siste['aarsresultat']} ({siste['aar']}) – vurder trend")

    # EK-andel
    ek_andel_str = siste.get("ek_andel", "-")
    try:
        ek_pst = float(ek_andel_str.replace("%", "").strip())
        if ek_pst >= 30:
            punkter.append(f"✅ EK-andel {ek_andel_str} – solid balanse")
        elif ek_pst >= 15:
            punkter.append(f"⚠️ EK-andel {ek_andel_str} – akseptabel")
        else:
            punkter.append(f"🔴 EK-andel {ek_andel_str} – svak soliditet")
            score = min(score, 1) if score > 0 else 1
    except Exception:
        pass

    # Konkurs / avvikling
    if si.get("konkurs"):
        punkter.append("🔴 Selskapet er under konkursbehandling – kausjon har ingen verdi")
        score = 1
    if si.get("under_avvikling"):
        punkter.append("🔴 Selskapet er under avvikling")
        score = min(score, 1)

    # Trend: sammenlign 2 siste år EK
    if len(reg) >= 2:
        ek_prev = reg[1].get("_ek_nok") or 0
        if ek_raw > ek_prev and ek_prev > 0:
            punkter.append(f"✅ EK vokste fra {round(ek_prev/1e6,1)} til {ek_mnok} MNOK – positiv trend")
        elif ek_raw < ek_prev and ek_prev > 0:
            punkter.append(f"⚠️ EK falt fra {round(ek_prev/1e6,1)} til {ek_mnok} MNOK – negativ trend")

    # Lønnsomhetstrend
    if len(reg) >= 2:
        res_prev = reg[1].get("_aarsresultat_nok") or 0
        if res_raw > 0 and res_prev > 0:
            punkter.append(f"✅ Positiv lønnsomhet i {reg[0]['aar']} og {reg[1]['aar']} – stabil inntjening")
        elif res_raw > 0 and res_prev < 0:
            punkter.append(f"⚠️ Snuoperasjon: negativt i {reg[1]['aar']}, positivt i {reg[0]['aar']}")
        elif res_raw < 0 and res_prev < 0:
            punkter.append(f"🔴 Negativt resultat to år på rad – svekker kausjonsverdi")
            score = min(score, 1)

    # Konklusjon
    if score == 0:
        vurdering, farge = "ukjent", "#9fb0c3"
    elif score == 1:
        vurdering, farge = "svak", "#ef4444"
    elif score == 2:
        vurdering, farge = "akseptabel", "#f59e0b"
    else:
        vurdering, farge = "sterk", "#22c55e"

    return {
        "navn": si.get("navn", ""),
        "orgnr": si.get("orgnr", ""),
        "vurdering": vurdering,
        "farge": farge,
        "begrunnelse": punkter,
        "regnskap": reg,
        "ek_mnok": ek_mnok,
        "ek_vs_kausjon": ek_vs_kausjon,
        "gjeldsgrad": gjeldsgrad,
        "gjeld_mnok": gjeld_mnok,
        "totalkapital_mnok": tk_mnok,
        "omsetning_mnok": omset_mnok,
        "kilde": si.get("kilde", ""),
    }


def compute_traffic_light(project_info: dict, analysis: dict) -> dict:
    """Beregn finansieringsstatus. 85%-regel byggelån (ekskl infra), 50-70% tomte-/infralån."""
    nt = safe_get(analysis, "noekkeltall", {})
    oek = safe_get(analysis, "oekonomisk_analyse", {})
    anbefaling = safe_get(analysis, "anbefaling", "")

    total = float(safe_get(nt, "totalinvestering_mnok", 0) or project_info.get("totalinvestering_mnok", 0) or 0)
    soekt_raw = float(safe_get(nt, "soekt_laan_mnok", 0) or project_info.get("soekt_laan_mnok", 0) or 0)
    ek_pst = float(safe_get(nt, "egenkapitalprosent", 0) or 0)
    margin_pst = float(safe_get(oek, "resultatmargin_pst", 0) or 0)
    forhaandssalg = int(project_info.get("forhaandssalg_pst", 0) or 0)
    markedsverdi = float(safe_get(nt, "estimert_markedsverdi_mnok", 0) or 0)

    # Separate loan types
    tomtelaan = float(project_info.get("tomtelaan_mnok", 0) or 0)
    infralaan = float(project_info.get("infralaan_mnok", 0) or 0)
    tomt_takst = float(project_info.get("tomtelaan_tomt_takst_mnok", 0) or 0)
    tomt_infra_sum = tomtelaan + infralaan

    # Pure byggelån = søkt minus infra (infra behandles som tomtelån)
    soekt_bygg = soekt_raw - infralaan if infralaan > 0 else soekt_raw

    # LTV for byggelån: rent byggelån / markedsverdi
    if markedsverdi > 0:
        ltv = round(soekt_bygg / markedsverdi * 100, 1)
    else:
        ltv = float(safe_get(nt, "belaaningsgrad_ltv", 0) or 0)

    # 85%-regel for byggelån (rent byggelån ekskl infra)
    bank_max = round(total * 0.85, 1) if total > 0 else 0
    soekt_ok = soekt_bygg <= bank_max if bank_max > 0 and soekt_bygg > 0 else True

    kausjoner = project_info.get("kausjoner", [])
    total_kausjon = sum(float(k.get("beloep_mnok", 0) or 0) for k in kausjoner if isinstance(k, dict))

    red_flags, yellow_flags, betingelser = [], [], []

    if margin_pst > 0 and margin_pst < 8:
        red_flags.append(f"Margin {margin_pst:.1f}% under bankens minstekrav på 8%")
    if ek_pst > 0 and ek_pst < 15:
        red_flags.append(f"Egenkapital {ek_pst:.1f}% under minimumskravet 15%")
    if ltv > 90:
        red_flags.append(f"LTV byggelån {ltv:.1f}% er over bankens øvre grense 90%")
    if anbefaling == "Ikke anbefalt":
        red_flags.append("AI-analyse anbefaler ikke innvilgelse basert på dokumentgrunnlaget")

    if not soekt_ok and soekt_bygg > 0:
        yellow_flags.append(f"Rent byggelån {soekt_bygg:.1f} MNOK over 85%-grensen {bank_max:.1f} MNOK")
        betingelser.append(f"Banken kan tilby inntil {bank_max:.1f} MNOK byggelån (85% av {total:.1f} MNOK). Resterende {round(soekt_bygg-bank_max,1)} MNOK må dekkes av egenkapital eller kausjon.")
    if ltv > 80 and ltv <= 90:
        yellow_flags.append(f"LTV byggelån {ltv:.1f}% over anbefalt 80% — krever tilleggssikkerhet")
        betingelser.append("LTV over 80% krever kausjon eller annen tilleggssikkerhet for overskytende del")
    if forhaandssalg > 0 and forhaandssalg < 60:
        yellow_flags.append(f"Forhåndssalg {forhaandssalg}% under anbefalt 60%")
        betingelser.append(f"Forhåndssalg bør økes til 60%+ (nå {forhaandssalg}%) — alternativt kreves høyere kausjonsandel")
    if margin_pst >= 8 and margin_pst < 12:
        yellow_flags.append(f"Margin {margin_pst:.1f}% akseptabel men under preferert 12%")

    # Tomtelån + infralån vurdering (behandles som tomtelån, 50-70% LTV)
    if tomt_infra_sum > 0:
        if tomt_takst > 0:
            tomt_ltv = round(tomtelaan / tomt_takst * 100, 1)
            if tomt_ltv > 70:
                yellow_flags.append(f"Tomtelån LTV {tomt_ltv:.0f}% over 70% — krever sterk kausjonist")
                betingelser.append(f"Tomtelån {tomtelaan:.1f} MNOK mot tomteverdi {tomt_takst:.1f} MNOK (LTV {tomt_ltv:.0f}%). Banknorm 50-70%.")
            elif tomt_ltv > 50:
                betingelser.append(f"Tomtelån LTV {tomt_ltv:.0f}% innenfor norm med kausjon (50-70% av tomteverdi)")
        if infralaan > 0:
            betingelser.append(f"Infralån {infralaan:.1f} MNOK er en egen trekkramme som behandles som tomtelån (maks 70% LTV samlet med tomtelånet). Trekkes etter hvert som infraarbeid utføres. Nedkvitteres i takt med salg/overlevering.")
            if tomt_takst > 0:
                infra_tomt_ltv = round((tomtelaan + infralaan) / tomt_takst * 100, 1)
                if infra_tomt_ltv > 70:
                    yellow_flags.append(f"Sum tomte-/infralån {tomt_infra_sum:.1f} MNOK mot tomteverdi {tomt_takst:.1f} MNOK (LTV {infra_tomt_ltv:.0f}%) — over 70%-norm")
                betingelser.append(f"Sum tomte-/infralån {tomt_infra_sum:.1f} MNOK mot tomteverdi {tomt_takst:.1f} MNOK (samlet LTV {infra_tomt_ltv:.0f}%). Banknorm maks 70%.")

    # Kausjon kan løfte rød til gul
    kan_kausjon_loefte = False
    if red_flags and total_kausjon > 0:
        gap = soekt_raw - bank_max if soekt_raw > bank_max else 0
        if total_kausjon >= gap * 0.5:
            kan_kausjon_loefte = True
            betingelser.append(f"Kausjoner totalt {total_kausjon:.1f} MNOK kan delvis mitigere — innvilgelse mulig med forsterkede vilkår")

    if red_flags and not kan_kausjon_loefte:
        farge, status = "rød", "Ikke anbefalt innvilget"
        kan_tilby = False
        bankens_tilbud = None
    elif red_flags and kan_kausjon_loefte:
        farge, status = "rød-betinget", "Betinget med tilleggssikkerhet"
        kan_tilby = True
        bankens_tilbud = f"Inntil {bank_max:.1f} MNOK byggelån mot kausjon {total_kausjon:.1f} MNOK og oppfyllelse av vilkår"
        if tomt_infra_sum > 0:
            bankens_tilbud += f". Tomte-/infralån {tomt_infra_sum:.1f} MNOK separat."
    elif yellow_flags:
        farge, status = "gul", "Kan innvilges med betingelser"
        kan_tilby = True
        bankens_tilbud = f"Byggelån inntil {bank_max:.1f} MNOK mot oppfyllelse av vilkår"
        if tomt_infra_sum > 0:
            bankens_tilbud += f". Tomte-/infralån {tomt_infra_sum:.1f} MNOK separat."
    else:
        farge, status = "grønn", "Anbefalt innvilget"
        kan_tilby = True
        bankens_tilbud = f"Byggelån opp til {bank_max:.1f} MNOK (85% av {total:.1f} MNOK)"
        if tomt_infra_sum > 0:
            bankens_tilbud += f". Tomte-/infralån {tomt_infra_sum:.1f} MNOK i tillegg."

    # Add infra explanation if LTV was corrected
    if infralaan > 0 and soekt_bygg != soekt_raw:
        betingelser.insert(0, f"LTV byggelån beregnet på rent byggelån {soekt_bygg:.1f} MNOK (søkt {soekt_raw:.1f} minus infra {infralaan:.1f}). LTV = {ltv:.1f}%.")

    return {
        "farge": farge, "status": status, "red_flags": red_flags,
        "yellow_flags": yellow_flags, "betingelser": betingelser,
        "bank_max_mnok": bank_max, "total_kausjon_mnok": total_kausjon,
        "kan_tilby": kan_tilby, "bankens_tilbud": bankens_tilbud,
        "tomt_infra_sum_mnok": tomt_infra_sum,
        "ltv_bygg_pst": ltv, "soekt_bygg_mnok": soekt_bygg,
    }

def run_credit_analysis(client_type, client, project_info: dict, doc_text: str) -> dict:
    """AI-analyse for kredittgrunnlag."""
    system_prompt = textwrap.dedent("""
    Du er en erfaren kredittanalytiker i en norsk bank som vurderer eiendomsprosjekter.
    Du skal lage et strukturert kredittnotat basert på prosjektinfo og dokumentgrunnlag.

    VIKTIG OM FINANSIERINGSSTRUKTUR:
    Prosjekter kan ha FLERE lånetyper som skal behandles SEPARAT:
    - BYGGELÅN: For selve byggeprosjektet (produksjon). Banknorm: maks 85-90% av prosjektkost.
    - TOMTELÅN: For tomtekjøp/areal som ikke bygges ennå. Banknorm: maks 50-70% av tomteverdi (avhengig av kausjon).
    - INFRALÅN: Forskuttering av infrastruktur for flere byggetrinn. Banken behandler infralån som tomtelån
      (50-70% LTV mot infraverdi), og det tilbakebetales prorata etterhvert som byggetrinn ferdigstilles.
      Infra-andel kan ligge "innenfor" byggelånet i søknaden (f.eks. "byggelån 532m hvorav infra utgjør 77m"),
      men skal skilles ut som egen post i analysen.
    Hvis dokumentene viser flere lånetyper, sett lånetype til "Kombinert tomte- og byggelån".
    I nøkkeltall:
    - soekt_laan_mnok = det rene byggelånet EKSKL infra-andelen. Hvis søkt 532 MNOK inkl 77.4 infra, sett soekt_laan_mnok = 454.6.
    - belaaningsgrad_ltv = (soekt_laan_mnok / estimert_markedsverdi_mnok) × 100. Bruk det RENE byggelånet ekskl infra.
    - Tomtelån og infralån nevnes separat i sammendraget, de er IKKE del av byggelånets LTV.
    Beregn EK-grad separat: byggelån mot prosjektkost, tomtelån+infralån mot tomt/infraverdi.

    VIKTIG OM VERDIVURDERING:
    Du skal ALLTID gjøre en selvstendig verdivurdering basert på riktig metode for prosjekttypen.
    En takst alene er IKKE tilstrekkelig — du må vurdere om taksten er rimelig gitt underliggende økonomi.

    For BOLIG (salg):
    - Bruk residualverdimetoden for å vurdere om tomtekostnaden er rimelig:
      Residual tomteverdi = Forventet salgsverdi - Utbyggingskost EKSKL tomt - Utviklermargin (min. 12% av salgsverdi)
    - KRITISK: "total_utbyggingskost_eks_tomt_mnok" = Alle prosjektkostnader UNNTATT tomtekjøp og UNNTATT finanskostnader.
      Det er: entreprise + offentlige gebyrer + prosjektledelse + honorarer + infrastruktur + uforutsett + diverse.
      Eksempel Steinan: Sum prosjektkost 503 MNOK (fra kalkyle) - tomt 94 MNOK = utbyggingskost eks tomt 409 MNOK.
      FEIL: Bruk ALDRI "totalinvestering" (som inkluderer finanskostnader/renter) som basis for utbyggingskost.
    - minimummargin_12pst_mnok = forventet_salgsverdi_mnok × 0.12
    - residual_tomteverdi_mnok = forventet_salgsverdi_mnok - total_utbyggingskost_eks_tomt_mnok - minimummargin_12pst_mnok
    - Sammenlign residual med oppgitt tomtekost. Hvis oppgitt tomtekost ≈ residual (innenfor 5%), er det rimelig.
    - faktisk_margin_pst = (salgsverdi - (utbyggingskost_eks_tomt + tomtekost)) / salgsverdi × 100
    - En tomt er aldri verdt mer enn det som gir utbygger minst 12% margin
    - Flagg dersom oppgitt tomteverdi/takst overstiger residualverdi vesentlig (>10%)
    - LTV skal beregnes mot beregnet verdi (salgsverdi), IKKE bare oppgitt takst

    For NÆRING (utleie — kontor, handel, logistikk, hotell):
    - Bruk yield-basert verdi: Verdi = Netto leieinntekt / Markedsyield
    - Beregn yield on cost: Netto leieinntekt / Total prosjektkost (inkl. tomt)
    - Yield on cost skal normalt være høyere enn antatt markedsyield (ellers skapes ingen verdi)
    - Vurder WAULT (vektet gjennomsnittlig gjenstående leietid)
    - Flagg dersom yield on cost < markedsyield (prosjektet skaper ikke verdi)

    For KOMBINERT (mixed-use):
    - Del opp i bolig- og næringsdel, verdivurder hver for seg
    - Summer delene og sammenlign med totalinvestering

    Returner KUN gyldig JSON med denne strukturen.
    VIKTIG: Rund alle MNOK-beløp til maks 1 desimal (f.eks. 580.4 MNOK, ikke 580.368225 MNOK).
    Rund prosenter til maks 1 desimal. Bruk hele tall for kr/kvm.
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
- Søkt byggelån: {project_info.get('soekt_laan_mnok', 0)} MNOK
- Totalinvestering: {project_info.get('totalinvestering_mnok', 0)} MNOK
- Egenkapital: {project_info.get('egenkapital_mnok', 0)} MNOK
- Prosjekttype: {project_info.get('prosjekttype', '')}
- Antall enheter: {project_info.get('antall_enheter', '')}
- BRA-i / SBRA: {project_info.get('bra_i_kvm', '')} kvm (salgbart innendørs areal — brukes for entreprisekost og salgsinntekt)
- Tomt: {project_info.get('tomt_kvm', '')} kvm
- Gnr/bnr (panteobjekt): {project_info.get('gnr_bnr', 'Ikke oppgitt')}
- Kommune: {project_info.get('kommune', 'Ikke oppgitt')}
- Planident: {project_info.get('planident', 'Ikke oppgitt')}
- Reguleringsplan: {project_info.get('reguleringsplan', '')}
- Rammegodkjenning: {project_info.get('rammegodkjenning', '')}
- Entrepriseform: {project_info.get('entrepriseform', '')}
- Planlagt byggestart: {project_info.get('byggestart', '')}
- Planlagt ferdigstillelse: {project_info.get('ferdigstillelse', '')}
- Forhåndssalg/utleiegrad: {project_info.get('forhaandssalg_pst', 0)}%
- Forventet leie/salgsinntekt: {project_info.get('inntekt_mnok', 0)} MNOK
- Eksisterende gjeld: {project_info.get('eksisterende_gjeld_mnok', 0)} MNOK
- Pantesikkerhet: {project_info.get('pantesikkerhet', '')}
- Rentevilkår: NIBOR 3MND + {project_info.get('nibor_margin_pst', 0)}% margin, provisjon {project_info.get('provisjon_pst_kvartal', 0)}% per kvartal, etablering NOK {project_info.get('etableringsgebyr_nok', 0)}, løpetid {project_info.get('loepetid_mnd', 0)} mnd

Finansieringsstruktur (kan ha flere lånetyper):
- Byggelån (søkt): {project_info.get('soekt_laan_mnok', 0)} MNOK
- Tomtelån (separat): {project_info.get('tomtelaan_mnok', 0)} MNOK (takst/verdi tomt: {project_info.get('tomtelaan_tomt_takst_mnok', 0)} MNOK)
- Infralån (forskuttering): {project_info.get('infralaan_mnok', 0)} MNOK
- NB: Infralån er en egen trekkramme som behandles som tomtelån. Tomtelån utbetales dag 1, infralån trekkes løpende. Samlet LTV for tomt+infra maks 70%. Nedkvitteres i takt med salg.
- NB: Hvis byggelånet inkluderer en infra-andel ("hvorav infra utgjør X"), skal denne skilles ut som infralån.
- Tomtelån+infralån: bank finansierer normalt 50-70% av verdi, avhengig av kausjoner. 50% uten kausjon, 60-70% med solid kausjonist.
- Byggelån: bank finansierer normalt 85-90% av prosjektkost (ekskl. infra).
- 85%-regelen byggelån: Maks 85% av prosjektkost = {round(float(project_info.get('totalinvestering_mnok', 0) or 0) * 0.85, 1)} MNOK.

Sikkerheter og kausjoner:
- Kausjoner og tilleggsgarantier: {json.dumps(project_info.get('kausjoner', []), ensure_ascii=False)}
- Selskapsinformasjon låntaker (Brreg/Proff): {json.dumps(project_info.get('selskapsinfo', {}), ensure_ascii=False)}
- Spesielle forhold: {project_info.get('spesielle_forhold', '')}

Verdivurdering og dokumentasjon:
- Har takst: {project_info.get('har_takst', False)}
- Takstverdi: {project_info.get('takst_mnok', 0)} MNOK
- Takstkilde: {project_info.get('takst_kilde', 'Ikke oppgitt')}
- Betalt/avtalt tomtepris: {project_info.get('tomtekost_mnok', 0)} MNOK
- Entreprisekost: {project_info.get('entreprisekost_mnok', 0)} MNOK
- Totalinvestering (inkl finans og infra): {project_info.get('totalinvestering_mnok', 0)} MNOK

KRITISK for residualberegning:
- "total_utbyggingskost_eks_tomt_mnok" er ALLE prosjektkostnader UNNTATT tomtekjøpet.
  Det inkluderer entreprise, offentlige avgifter, prosjektledelse, honorarer, infrastruktur, uforutsett — men IKKE tomtekost og IKKE finanskostnader (renter/provisjon).
- FEIL: totalinvestering - tomtekost (dette inkluderer finanskostnader og gir for høy utbyggingskost)
- RIKTIG: Bruk sum av entreprise + offentlige avgifter + prosjektledelse + honorarer + infra + diverse + uforutsett fra kalkylen.
  Alternativt: "Sum prosjektkostnader" fra kalkylen minus tomtekostnaden.
- Residual = Forventet salgsverdi - Utbyggingskost eks tomt - (12% × Salgsverdi)

Bolig (residualverdi):
- Forventet salgspris: {project_info.get('forventet_salgspris_kvm', 0)} kr/kvm BRA-i
- Salgbart areal BRA-i: {project_info.get('bra_i_kvm', project_info.get('bra_kvm', 0))} kvm
- Byggekost entreprise: {project_info.get('byggekost_kvm', 0)} kr/kvm BRA-i
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

Lag et komplett kredittnotat med fokus på korrekt verdivurdering basert på prosjekttype.
Husk å vurdere tomtelån og byggelån separat hvis begge er oppgitt.
For tomtelån: vurder LTV mot tomteverdi/takst og kausjonsdekning.
For byggelån: vurder LTV mot prosjektkost og 85%-regelen.
Inkluder gnr/bnr i sikkerheter-seksjonen som panteobjekt.
Returner JSON.
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
    """
    Corporate-grade PDF report in McKinsey / investment bank style.
    Navy + teal accent color scheme with Builtly branding.
    """

    # Brand colors
    NAVY        = (6, 17, 36)
    DARK_NAVY   = (3, 10, 22)
    TEAL        = (56, 194, 201)
    WARM        = (245, 158, 11)
    GREEN       = (34, 197, 94)
    RED         = (239, 68, 68)
    WHITE       = (255, 255, 255)
    LIGHT_GRAY  = (245, 247, 251)
    MID_GRAY    = (159, 176, 195)
    DARK_GRAY   = (80, 100, 120)
    BODY_TEXT   = (30, 40, 55)
    TABLE_HEAD  = (12, 28, 50)
    TABLE_ALT   = (240, 244, 250)

    def __init__(self):
        super().__init__("P", "mm", "A4")
        self.set_auto_page_break(auto=True, margin=28)
        self._has_unicode_font = self._add_fonts()
        self._logo_path = self._find_logo()
        self._logo_white_path = self._find_logo_white()
        self.accent = self.TEAL
        self.dark = self.NAVY

    def _safe(self, text):
        """Sanitize text for PDF output, preserving Norwegian chars (ø, å, æ, etc.)."""
        if not text:
            return ""
        import unicodedata
        s = unicodedata.normalize('NFC', str(text))
        # Replace problematic Unicode chars that break even with Unicode fonts
        for old, new in [('\u2014','\u002d'),('\u2013','\u002d'),('\u2018',"'"),('\u2019',"'"),('\u201c','"'),('\u201d','"'),('\u2022','\u002d'),('\u2192','->')]:
            s = s.replace(old, new)
        if self._has_unicode_font:
            return s
        # Fallback: only strip truly unsupported chars for Helvetica (latin-1)
        return s.encode('latin-1', errors='replace').decode('latin-1')

    def _add_fonts(self):
        found = False
        for style, name in [("", "Inter-Regular.ttf"), ("B", "Inter-Bold.ttf")]:
            path = os.path.join(os.path.dirname(__file__), name)
            if os.path.exists(path):
                self.add_font("Inter", style, path, uni=True)
                found = True
        return found

    def _find_logo(self):
        """Find regular logo for header pages."""
        for p in ["logo.png", os.path.join(os.path.dirname(__file__), "logo.png"), "/app/logo.png"]:
            if os.path.exists(p):
                return p
        return ""

    def _find_logo_white(self):
        """Find white logo for dark cover page."""
        for p in ["logo-white.png", os.path.join(os.path.dirname(__file__), "logo-white.png"), "/app/logo-white.png"]:
            if os.path.exists(p):
                return p
        return self._logo_path  # fallback to regular logo

    def _font(self, style="", size=10):
        try:
            self.set_font("Inter", style, size)
        except Exception:
            self.set_font("Helvetica", style, size)

    def header(self):
        if self.page_no() <= 1:
            return
        y0 = 8
        if self._logo_path:
            try:
                self.image(self._logo_path, 10, y0, 28)
            except Exception:
                self._font("B", 8); self.set_text_color(*self.TEAL); self.set_xy(10, y0+1); self.cell(28, 5, "BUILTLY")
        else:
            self._font("B", 8); self.set_text_color(*self.TEAL); self.set_xy(10, y0+1); self.cell(28, 5, "BUILTLY")
        self._font("B", 7); self.set_text_color(*self.MID_GRAY); self.set_xy(42, y0+1); self.cell(100, 5, self._safe("KREDITTNOTAT"))
        self._font("", 7); self.set_text_color(*self.MID_GRAY); self.set_xy(150, y0+1); self.cell(50, 5, datetime.now().strftime("%d.%m.%Y"), align="R")
        self.set_draw_color(*self.TEAL); self.set_line_width(0.6); self.line(10, y0+7, 200, y0+7)
        self.set_draw_color(220, 225, 235); self.set_line_width(0.15); self.line(10, y0+7.8, 200, y0+7.8)
        self.set_y(y0 + 12)

    def footer(self):
        self.set_y(-18)
        self.set_draw_color(200, 210, 225); self.set_line_width(0.15); self.line(10, self.get_y(), 200, self.get_y())
        self.ln(2); self._font("", 6.5); self.set_text_color(*self.MID_GRAY)
        self.cell(120, 4, self._safe("KONFIDENSIELT - Kun for intern bruk i kredittvurderingsprosessen"), align="L")
        pg = self.page_no() - 1
        if pg > 0:
            self.cell(0, 4, self._safe(f"Side {pg}"), align="R")
        self.ln(3); self._font("", 5.5); self.set_text_color(180, 190, 205)
        self.cell(0, 3, self._safe("Generert av Builtly | builtly.ai - Utkast, krever kvalitetssikring av kredittavdeling"), align="L")

    def cover_page(self, project_name, laantaker, laanetype):
        self.add_page()
        self.set_fill_color(*self.DARK_NAVY); self.rect(0, 0, 210, 297, style="F")
        # White logo on dark background
        cover_logo = self._logo_white_path or self._logo_path
        if cover_logo:
            try:
                self.image(cover_logo, 20, 22, 45)
            except Exception:
                self._font("B", 14); self.set_text_color(*self.TEAL); self.set_xy(20, 25); self.cell(35, 10, "BUILTLY")
        else:
            self._font("B", 14); self.set_text_color(*self.TEAL); self.set_xy(20, 25); self.cell(35, 10, "BUILTLY")
        self.set_xy(20, 60); self.set_fill_color(*self.TEAL); self._font("B", 8); self.set_text_color(*self.DARK_NAVY)
        self.cell(42, 7, "  KONFIDENSIELT  ", fill=True, align="C")
        self.set_xy(20, 80); self._font("B", 34); self.set_text_color(*self.WHITE); self.cell(0, 16, "Kredittnotat")
        self.set_xy(20, 100); self._font("", 14); self.set_text_color(*self.TEAL); self.cell(0, 8, self._safe(laanetype))
        self.set_draw_color(*self.TEAL); self.set_line_width(1.2); self.line(20, 115, 90, 115)
        self.set_xy(20, 125); self._font("B", 20); self.set_text_color(*self.WHITE); self.multi_cell(170, 10, self._safe(project_name))
        y = self.get_y() + 8
        self.set_xy(20, y); self._font("", 11); self.set_text_color(*self.MID_GRAY); self.cell(0, 6, self._safe(f"L\u00e5ntaker: {laantaker}"))
        box_y = 210
        self.set_fill_color(15, 30, 50); self.rect(20, box_y, 170, 40, style="F")
        self.set_draw_color(40, 60, 85); self.rect(20, box_y, 170, 40, style="D")
        items = [("Dato", datetime.now().strftime("%d.%m.%Y")), ("Klassifisering", "Konfidensielt"),
                 ("Utarbeidet av", "Builtly AI-assistert kredittanalyse"), ("Status", "Utkast - krever faglig gjennomgang")]
        for i, (label, val) in enumerate(items):
            col_x = 25 + (i % 2) * 85; row_y = box_y + 6 + (i // 2) * 16
            self._font("B", 7); self.set_text_color(*self.TEAL); self.set_xy(col_x, row_y); self.cell(80, 4, label.upper())
            self._font("", 9); self.set_text_color(*self.WHITE); self.set_xy(col_x, row_y+5); self.cell(80, 4, self._safe(val))
        # Bottom teal accent stripe only
        self.set_fill_color(*self.TEAL); self.rect(0, 293, 210, 4, style="F")

    def section_title(self, num, title):
        # Ensure enough room for title (15mm) + at least 40mm of content
        if self.get_y() > 225:
            self.add_page()
        self.ln(8)
        self._font("B", 8); self.set_fill_color(*self.TEAL); self.set_text_color(*self.WHITE)
        num_str = str(num); pill_w = max(8, len(num_str) * 3.5 + 5)
        self.cell(pill_w, 6, f" {num_str} ", fill=True, align="C"); self.cell(3, 6, "")
        self._font("B", 13); self.set_text_color(*self.NAVY)
        self.cell(0, 6, self._safe(title), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*self.TEAL); self.set_line_width(0.5); self.line(10, self.get_y()+1, 200, self.get_y()+1)
        self.ln(4)

    def body_text(self, text):
        if self.get_y() > 260: self.add_page()
        self._font("", 9.5); self.set_text_color(*self.BODY_TEXT)
        self.multi_cell(0, 5, self._safe(str(text))); self.ln(1.5)

    def key_value(self, key, value, highlight=False):
        if self.get_y() > 265: self.add_page()
        safe_val = self._safe(str(value))
        if highlight:
            self.set_fill_color(*self.TABLE_ALT); self.rect(10, self.get_y(), 190, 5.5, style="F")
        self._font("B", 8.5); self.set_text_color(*self.DARK_GRAY)
        self.cell(72, 5.5, self._safe(key))
        self._font("", 9.5); self.set_text_color(*self.NAVY)
        # Use multi_cell for long values to avoid overflow
        if len(safe_val) > 60:
            x_save = self.get_x(); y_save = self.get_y()
            self.set_xy(82, y_save)
            self.multi_cell(118, 5, safe_val)
            self.ln(0.5)
        else:
            self.cell(118, 5.5, safe_val, new_x="LMARGIN", new_y="NEXT")

    def status_box(self, status, text):
        if self.get_y() > 220: self.add_page()
        color_map = {"Anbefalt innvilget": self.GREEN, "Anbefalt med vilkår": self.WARM, "Anbefalt med vilkar": self.WARM, "Anbefalt med vilkaar": self.WARM, "Ikke anbefalt": self.RED}
        color = color_map.get(status, self.TEAL)
        safe_text = self._safe(text)
        # Estimate height: ~3.2 chars per mm at 8.5pt, width 176mm
        text_lines = max(1, -(-len(safe_text) // 560))  # ~176mm * 3.2
        box_h = 15 + text_lines * 5
        self.ln(3); y = self.get_y()
        if y + box_h > 270: self.add_page(); y = self.get_y()
        self.set_fill_color(*color); self.rect(10, y, 190, 1.5, style="F")
        self.set_fill_color(min(color[0]+220,255), min(color[1]+220,255), min(color[2]+220,255))
        self.rect(10, y+1.5, 190, box_h - 1.5, style="F")
        self.set_draw_color(*color); self.set_line_width(0.3); self.rect(10, y, 190, box_h, style="D")
        self._font("B", 13); self.set_text_color(*color); self.set_xy(16, y+4); self.cell(170, 7, self._safe(status))
        self._font("", 8.5); self.set_text_color(*self.BODY_TEXT); self.set_xy(16, y+13)
        self.multi_cell(172, 4.5, safe_text)
        self.set_y(y + box_h + 3)

    def metric_row(self, metrics):
        if self.get_y() > 240: self.add_page()
        n = len(metrics)
        if n == 0: return
        card_w = (190 - (n-1)*4) / n; y = self.get_y()
        for i, (value, label, sublabel) in enumerate(metrics):
            x = 10 + i * (card_w + 4)
            self.set_fill_color(*self.LIGHT_GRAY); self.set_draw_color(220,225,235); self.set_line_width(0.2)
            self.rect(x, y, card_w, 22, style="DF")
            self.set_fill_color(*self.TEAL); self.rect(x, y, card_w, 1, style="F")
            self._font("B", 14); self.set_text_color(*self.NAVY); self.set_xy(x+4, y+3); self.cell(card_w-8, 7, self._safe(str(value)))
            self._font("B", 7); self.set_text_color(*self.DARK_GRAY); self.set_xy(x+4, y+11); self.cell(card_w-8, 4, self._safe(label.upper()))
            self._font("", 6.5); self.set_text_color(*self.MID_GRAY); self.set_xy(x+4, y+15.5); self.cell(card_w-8, 4, self._safe(sublabel))
        self.set_y(y + 26)

    def traffic_light(self, color_name, status_text, details):
        cmap = {"gronn": self.GREEN, "gul": self.WARM, "rod-betinget": (249,115,22), "rod": self.RED}
        color = cmap.get(color_name.replace("\u00f8","o").replace("\u00e6","ae"), self.MID_GRAY)
        y = self.get_y()
        self.set_fill_color(*color); self.ellipse(12, y+1, 8, 8, style="F")
        self._font("B", 12); self.set_text_color(*color); self.set_xy(24, y+1); self.cell(0, 8, self._safe(status_text))
        self.ln(10); self._font("", 9); self.set_text_color(*self.BODY_TEXT)
        for d in details:
            self.set_x(14); self.multi_cell(180, 4.5, self._safe(f"  {d}")); self.ln(1)

    def pro_table(self, headers, rows, col_widths=None):
        """Professional table with text wrapping and consistent alignment."""
        if self.get_y() > 240: self.add_page()
        n = len(headers)
        if col_widths is None: col_widths = [190/n]*n
        x_start = 10

        # Header row
        self.set_fill_color(*self.TABLE_HEAD); self.set_text_color(*self.WHITE); self._font("B", 7.5)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 7, self._safe(h), border=0, fill=True, align="L")
        self.ln()

        # Data rows with text wrapping
        self._font("", 8)
        row_h = 5.5
        for ri, row in enumerate(rows):
            if self.get_y() > 260: self.add_page()
            y_start = self.get_y()
            bg = self.TABLE_ALT if ri % 2 == 0 else self.WHITE

            # First pass: calculate max height needed
            max_lines = 1
            for i, cell_val in enumerate(row):
                cs = self._safe(str(cell_val))
                # Estimate lines needed (approx 3.5 chars per mm at 8pt)
                chars_per_line = max(1, int(col_widths[i] * 3.2))
                lines = max(1, -(-len(cs) // chars_per_line))  # ceiling division
                max_lines = max(max_lines, lines)
            cell_h = max_lines * row_h

            # Background fill for entire row
            self.set_fill_color(*bg)
            self.rect(x_start, y_start, 190, cell_h, style="F")

            # Draw cells
            for i, cell_val in enumerate(row):
                cs = self._safe(str(cell_val))
                # Color coding
                if any(k in cs.lower() for k in ["god","sterk","positiv","godkjent","mottatt","ja"]): self.set_text_color(*self.GREEN)
                elif any(k in cs.lower() for k in ["svak","negativ","ikke","kritisk","nei"]): self.set_text_color(*self.RED)
                elif any(k in cs.lower() for k in ["akseptabel","middels","forbehold"]): self.set_text_color(*self.WARM)
                else: self.set_text_color(*self.BODY_TEXT)

                x_pos = x_start + sum(col_widths[:i])
                self.set_xy(x_pos, y_start)
                self.multi_cell(col_widths[i], row_h, cs, border=0, align="L")

            self.set_y(y_start + cell_h)

        # Bottom line
        self.set_draw_color(200,210,225); self.set_line_width(0.3)
        self.line(x_start, self.get_y(), x_start + 190, self.get_y()); self.ln(2)

    def callout(self, title, text, tone="blue"):
        if self.get_y() > 245: self.add_page()
        tmap = {"blue":(self.TEAL,(230,248,250)), "green":(self.GREEN,(235,250,240)), "yellow":(self.WARM,(255,248,230)), "red":(self.RED,(255,235,235))}
        accent, bg = tmap.get(tone, tmap["blue"])
        safe_text = self._safe(text)
        text_lines = max(1, -(-len(safe_text) // 560))
        h = max(16, 10 + text_lines * 5)
        y = self.get_y()
        if y + h > 270: self.add_page(); y = self.get_y()
        self.set_fill_color(*bg); self.set_draw_color(*accent); self.set_line_width(0.3); self.rect(10, y, 190, h, style="DF")
        self.set_fill_color(*accent); self.rect(10, y, 3, h, style="F")
        self._font("B", 8.5); self.set_text_color(*accent); self.set_xy(17, y+2); self.cell(170, 5, self._safe(title))
        self._font("", 8); self.set_text_color(*self.BODY_TEXT); self.set_xy(17, y+8)
        self.multi_cell(178, 4, safe_text)
        self.set_y(y + h + 3)


def _fmt_v(val, suffix="MNOK"):
    """Format value for PDF display - round MNOK to 1 decimal, percentages to 1 decimal."""
    try:
        v = float(val)
        if suffix == "MNOK":
            return f"{v:.1f} MNOK"
        elif suffix == "%":
            return f"{v:.1f}%"
        elif suffix == "kr":
            return f"{int(v):,} kr".replace(",", " ")
        else:
            return f"{v:.1f} {suffix}" if v != int(v) else f"{int(v)} {suffix}"
    except (ValueError, TypeError):
        return str(val)


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

    # 0. Finansieringsvurdering (trafikklys)
    tl = project_info.get("_traffic_light", {})
    if tl:
        farge = tl.get("farge", "gronn")
        pdf.section_title(1, "Finansieringsvurdering")
        all_details = []
        pdf.key_value("85%-grense:", f"{tl.get('bank_max_mnok',0):.1f} MNOK", highlight=True)
        pdf.key_value("Sum kausjoner:", f"{tl.get('total_kausjon_mnok',0):.1f} MNOK")
        if tl.get("bankens_tilbud"):
            pdf.key_value("Bankens tilbud:", tl.get("bankens_tilbud",""), highlight=True)
        for f in tl.get("red_flags", []) + tl.get("yellow_flags", []):
            all_details.append(f)
        for b in tl.get("betingelser", []):
            all_details.append(f"-> {b}")
        if all_details:
            pdf.ln(2)
            pdf.traffic_light(farge, tl.get("status", ""), all_details)
        pdf.ln(3)

    # 1. Sammendrag
    pdf.section_title(2, "Sammendrag og anbefaling")
    pdf.status_box(safe_get(analysis, "anbefaling", "Ikke vurdert"), safe_get(analysis, "sammendrag", ""))

    # 2. Nøkkeltall (dual metric dashboards)
    pdf.section_title(3, "Nøkkeltall")
    nt = safe_get(analysis, "noekkeltall", {})
    if isinstance(nt, dict):
        pdf.metric_row([
            (_fmt_v(safe_get(nt, 'totalinvestering_mnok', 0)), "Totalinvestering", "Sum prosjektkost"),
            (_fmt_v(safe_get(nt, 'soekt_laan_mnok', 0)), "Søkt lån", "Omsøkt finansiering"),
            (f"{safe_get(nt, 'egenkapitalprosent', 0)}%", "Egenkapitalandel", _fmt_v(safe_get(nt, 'egenkapital_mnok', 0))),
            (f"{safe_get(nt, 'belaaningsgrad_ltv', 0)}%", "LTV", "Belåningsgrad"),
        ])
        pdf.metric_row([
            (f"{safe_get(nt, 'dscr', 0)}", "DSCR", "Debt Service Coverage"),
            (f"{safe_get(nt, 'icr', 0)}", "ICR", "Interest Coverage"),
            (f"{safe_get(nt, 'netto_yield_pst', 0)}%", "Netto yield", "Avkastning"),
            (f"{safe_get(nt, 'forhaandssalg_utleie_pst', 0)}%", "Forhåndssalg", "Salgs-/utleiegrad"),
        ])
        pdf.key_value("Estimert markedsverdi:", _fmt_v(safe_get(nt, 'estimert_markedsverdi_mnok', 0)), highlight=True)

    # 3. Verdivurdering
    pdf.section_title(4, "Verdivurdering")
    vv = safe_get(analysis, "verdivurdering", {})
    if isinstance(vv, dict):
        pdf.key_value("Metode:", safe_get(vv, "metode", "-"))
        pdf.key_value("Oppgitt takst:", _fmt_v(safe_get(vv, 'oppgitt_takst_mnok', 0)))
        pdf.key_value("Beregnet verdi:", _fmt_v(safe_get(vv, 'beregnet_verdi_mnok', 0)))
        pdf.key_value("Avvik takst vs. beregnet:", f"{safe_get(vv, 'avvik_takst_vs_beregnet_pst', 0)}%")
        takst_ok = safe_get(vv, "takst_er_rimelig", True)
        pdf.key_value("Takst rimelig:", "Ja" if takst_ok else "NEI - se kommentar")
        pdf.body_text(safe_get(vv, "kommentar_takst", ""))

        br = safe_get(vv, "bolig_residual", {})
        if isinstance(br, dict) and safe_get(br, "residual_tomteverdi_mnok", 0):
            pdf.callout("Residualverdiberegning (Bolig / BRA-i)", "Tomteverdi = Salgsverdi - Utbyggingskost - Utviklermargin (min 12%)", "blue")
            residual_rows = [
                ["Forventet salgsverdi", _fmt_v(safe_get(br, 'forventet_salgsverdi_mnok', 0))],
                ["Salgspris per kvm BRA-i", _fmt_v(safe_get(br, 'salgsverdi_per_kvm_bra', 0), "kr")],
                ["Entreprisekost per kvm BRA-i", _fmt_v(safe_get(br, 'byggekost_per_kvm_bta', 0), "kr")],
                ["Utbyggingskost ekskl. tomt", _fmt_v(safe_get(br, 'total_utbyggingskost_eks_tomt_mnok', 0))],
                ["Minimummargin 12%", _fmt_v(safe_get(br, 'minimummargin_12pst_mnok', 0))],
                ["Residual tomteverdi", _fmt_v(safe_get(br, 'residual_tomteverdi_mnok', 0))],
                ["Oppgitt tomtekostnad", _fmt_v(safe_get(br, 'oppgitt_tomtekost_mnok', 0))],
                ["Innenfor residual", "Ja" if safe_get(br, "tomtekost_innenfor_residual", True) else "NEI"],
                ["Faktisk margin", f"{safe_get(br, 'faktisk_margin_pst', 0)}%"],
            ]
            pdf.pro_table(["Post", "Verdi"], residual_rows, [100, 90])
            if safe_get(br, "kommentar"):
                pdf.body_text(safe_get(br, "kommentar", ""))

        ny = safe_get(vv, "naering_yield", {})
        if isinstance(ny, dict) and safe_get(ny, "yield_on_cost_pst", 0):
            pdf.ln(2)
            pdf._font("B", 10)
            pdf.set_text_color(245, 158, 11)
            pdf.cell(0, 6, "Yield-analyse (Næring)", new_x="LMARGIN", new_y="NEXT")
            pdf._font("", 9)
            pdf.set_text_color(40, 50, 60)
            pdf.key_value("Brutto leieinntekt:", _fmt_v(safe_get(ny, 'brutto_leieinntekt_mnok', 0)))
            pdf.key_value("Eierkostnader:", _fmt_v(safe_get(ny, 'eierkostnader_mnok', 0)))
            pdf.key_value("Netto leieinntekt:", _fmt_v(safe_get(ny, 'netto_leieinntekt_mnok', 0)))
            pdf.key_value("Yield on cost:", f"{safe_get(ny, 'yield_on_cost_pst', 0)}%")
            pdf.key_value("Antatt markedsyield:", f"{safe_get(ny, 'antatt_markedsyield_pst', 0)}%")
            pdf.key_value("Yield spread:", f"{safe_get(ny, 'yield_spread_pst', 0)}%")
            pdf.key_value("Verdi ved markedsyield:", _fmt_v(safe_get(ny, 'verdi_ved_markedsyield_mnok', 0)))
            pdf.key_value("WAULT:", f"{safe_get(ny, 'wault_aar', 0)} år")
            pdf.key_value("Vakansrisiko:", f"{safe_get(ny, 'vakansrisiko_pst', 0)}%")
            verdiskaping = safe_get(ny, "verdiskaping_positiv", True)
            pdf.key_value("Verdiskaping:", "Positiv" if verdiskaping else "NEGATIV - yield on cost < markedsyield")
            pdf.body_text(safe_get(ny, "kommentar", ""))

        pdf.metric_row([
            (_fmt_v(safe_get(vv, 'bankens_verdianslag_mnok', 0)), "Bankens verdianslag", "Anbefalt verdi for belåning"),
            (_fmt_v(safe_get(vv, 'forsiktig_verdi_70pst_mnok', 0)), "Forsiktig verdi (70%)", "Konservativt scenario"),
        ])
        pdf.key_value("LTV mot beregnet verdi:", f"{safe_get(vv, 'ltv_mot_beregnet_verdi_pst', 0)}%", highlight=True)

    # 4. Regulering
    pdf.section_title(5, "Regulering og tomt")
    reg = safe_get(analysis, "regulering_og_tomt", {})
    if isinstance(reg, dict):
        pdf.key_value("Reguleringsplan:", safe_get(reg, "reguleringsplan", "-"))
        pdf.key_value("Utnyttelsesgrad (BYA):", f"{safe_get(reg, 'utnyttelsesgrad_bya_pst', 0)}%")
        pdf.key_value("Tillatt vs. planlagt BTA:", safe_get(reg, "tillatt_vs_planlagt_bta", "-"))
        pdf.key_value("Rammegodkjenning:", safe_get(reg, "rammegodkjenning_status", "-"))
        if project_info.get("gnr_bnr"):
            pdf.key_value("Gnr/bnr (panteobjekt):", project_info["gnr_bnr"])
        if project_info.get("kommune"):
            pdf.key_value("Kommune:", project_info["kommune"])
        if project_info.get("planident"):
            pdf.key_value("Planident:", project_info["planident"])
        pdf.body_text(safe_get(reg, "kommentar", ""))

    # 4b. Finansieringsstruktur (multi-lån)
    if project_info.get("tomtelaan_mnok", 0) > 0 or project_info.get("infralaan_mnok", 0) > 0:
        pdf.section_title("5b", "Finansieringsstruktur")
        pdf.key_value("Byggelån (søkt):", f"{project_info.get('soekt_laan_mnok', 0)} MNOK")
        if project_info.get("tomtelaan_mnok", 0) > 0:
            pdf.key_value("Tomtelån (separat):", f"{project_info['tomtelaan_mnok']} MNOK", highlight=True)
            if project_info.get("tomtelaan_tomt_takst_mnok", 0) > 0:
                ltv_tomt = round(project_info["tomtelaan_mnok"] / project_info["tomtelaan_tomt_takst_mnok"] * 100, 1)
                pdf.key_value("Takst/verdi tomt:", f"{project_info['tomtelaan_tomt_takst_mnok']} MNOK (LTV tomt: {ltv_tomt}%)")
        if project_info.get("infralaan_mnok", 0) > 0:
            pdf.key_value("Infralån (forskuttering):", f"{project_info['infralaan_mnok']} MNOK", highlight=True)
            pdf.body_text("Infralån er en egen trekkramme som behandles som tomtelån (maks 70% LTV samlet med tomtelånet). Trekkes etter hvert som infraarbeid utføres, nedkvitteres i takt med salg.")
        # Sum tomtelån + infralån
        tomt_infra_sum = project_info.get("tomtelaan_mnok", 0) + project_info.get("infralaan_mnok", 0)
        if tomt_infra_sum > 0:
            pdf.key_value("Sum tomte-/infralån:", f"{tomt_infra_sum:.1f} MNOK (banknorm 50-70% LTV)")
        total_laan = project_info.get("soekt_laan_mnok", 0) + project_info.get("tomtelaan_mnok", 0) + project_info.get("infralaan_mnok", 0)
        pdf.key_value("Sum alle lån:", f"{total_laan:.1f} MNOK", highlight=True)

    # 5. Økonomi
    pdf.section_title(6, "Økonomisk analyse")
    oek = safe_get(analysis, "oekonomisk_analyse", {})
    if isinstance(oek, dict):
        oek_rows = [
            ["Totalkostnadskalkyle", _fmt_v(safe_get(oek, 'totalkostnadskalkyle_mnok', 0))],
            ["Entreprisekostnad", _fmt_v(safe_get(oek, 'entreprisekostnad_mnok', 0))],
            ["Tomtekostnad", _fmt_v(safe_get(oek, 'tomtekostnad_mnok', 0))],
            ["Offentlige avgifter", _fmt_v(safe_get(oek, 'offentlige_avgifter_mnok', 0))],
            ["Prosjektkostnader", _fmt_v(safe_get(oek, 'prosjektkostnader_mnok', 0))],
            ["Finanskostnader", _fmt_v(safe_get(oek, 'finanskostnader_mnok', 0))],
            ["Forventet salgsverdi", _fmt_v(safe_get(oek, 'forventet_salgsverdi_mnok', 0))],
            ["Forventet resultat", _fmt_v(safe_get(oek, 'forventet_resultat_mnok', 0))],
            ["Resultatmargin", f"{safe_get(oek, 'resultatmargin_pst', 0)}%"],
        ]
        pdf.pro_table(["Post", "MNOK"], oek_rows, [120, 70])

    # 6. Rentesensitivitet
    pdf.section_title(7, "Rentesensitivitet")
    rente = safe_get(analysis, "rentesensitivitet", [])
    if rente:
        headers = ["Rentenivå", "Årsresultat (MNOK)", "DSCR", "Betjeningsevne"]
        rows = []
        for r in rente:
            if isinstance(r, dict):
                aarsres = safe_get(r, "aarsresultat_mnok", 0)
                try:
                    aarsres = f"{float(aarsres):.1f}"
                except (ValueError, TypeError):
                    aarsres = str(aarsres)
                rows.append([
                    safe_get(r, "rentenivaa", "-"),
                    aarsres,
                    str(safe_get(r, "dscr", 0)),
                    safe_get(r, "betjeningsevne", "-"),
                ])
        if rows:
            pdf.pro_table(headers, rows, [45, 50, 35, 60])

    # 7. Sikkerheter
    pdf.section_title(8, "Sikkerheter og pant")
    if project_info.get("gnr_bnr"):
        pdf.callout("Panteobjekt", f"Matrikkel: {project_info['gnr_bnr']}" + (f" - {project_info.get('kommune', '')}" if project_info.get('kommune') else ""), "blue")
    sikkerheter = safe_get(analysis, "sikkerheter", [])
    if sikkerheter:
        sik_rows = []
        for s in sikkerheter:
            if isinstance(s, dict):
                sik_rows.append([safe_get(s, 'type', ''), safe_get(s, 'prioritet', ''), _fmt_v(safe_get(s, 'verdi_mnok', 0)), safe_get(s, 'kommentar', '')])
        if sik_rows:
            pdf.pro_table(["Type", "Prioritet", "Verdi", "Kommentar"], sik_rows, [50, 25, 30, 85])

    kausjoner = project_info.get("kausjoner", [])
    aktive = [k for k in kausjoner if isinstance(k, dict) and k.get("kausjonist")]
    if aktive:
        pdf.callout("Kausjoner og morselskapsgarantier", f"{len(aktive)} kausjonist(er), sum {sum(float(k.get('beloep_mnok',0) or 0) for k in aktive):.1f} MNOK", "yellow")
        kausk_rows = []
        for k in aktive:
            kausk_rows.append([k.get("kausjonist", ""), k.get("type", "Selvskyldner"), f"{float(k.get('beloep_mnok', 0) or 0):.1f} MNOK", k.get("orgnr", "")])
        pdf.pro_table(["Kausjonist", "Type", "Beløp", "Org.nr."], kausk_rows, [60, 40, 35, 55])

    # 8. Risikovurdering
    pdf.section_title(9, "Risikovurdering")
    risiko_list = safe_get(analysis, "risikovurdering", [])
    if risiko_list:
        risk_rows = []
        for r in risiko_list:
            if isinstance(r, dict):
                risk_rows.append([safe_get(r, "risiko", ""), safe_get(r, "sannsynlighet", "-"), safe_get(r, "konsekvens", "-"), safe_get(r, "mitigering", "-")])
        if risk_rows:
            pdf.pro_table(["Risiko", "Sannsynlighet", "Konsekvens", "Mitigering"], risk_rows, [50, 28, 28, 84])

    # 9. Styrker / svakheter
    pdf.section_title(10, "Styrker og svakheter")
    styrker = safe_get(analysis, "styrker", [])
    svakheter = safe_get(analysis, "svakheter", [])
    max_rows = max(len(styrker), len(svakheter))
    if max_rows > 0:
        ss_rows = []
        for i in range(max_rows):
            s = f"+ {styrker[i]}" if i < len(styrker) else ""
            w = f"- {svakheter[i]}" if i < len(svakheter) else ""
            ss_rows.append([s, w])
        pdf.pro_table(["Styrker", "Svakheter"], ss_rows, [95, 95])

    # 10. Vilkår
    pdf.section_title(11, "Foreslåtte vilkår")
    for i, v in enumerate(safe_get(analysis, "vilkaar", []), 1):
        pdf.body_text(f"{i}. {v}")

    # 11. Covenants
    pdf.section_title(12, "Covenants")
    cov = safe_get(analysis, "covenants", [])
    if cov:
        cov_rows = []
        for c in cov:
            if isinstance(c, dict):
                cov_rows.append([safe_get(c, "covenant", ""), safe_get(c, "grenseverdi", ""), safe_get(c, "maalefrekvens", "")])
        if cov_rows:
            pdf.pro_table(["Covenant", "Grenseverdi", "Målefrekvens"], cov_rows, [80, 55, 55])

    # Disclaimer
    pdf.ln(10)
    pdf.callout(
        "UTKAST — KREVER FAGLIG KONTROLL",
        "Kredittnotatet er automatisk generert av Builtly og skal gjennomgås og kvalitetssikres av kredittavdelingen før fremleggelse for kredittkomité. Alle nøkkeltall, vurderinger og anbefalinger må verifiseres mot faktiske forhold.",
        "yellow"
    )

    return bytes(pdf.output())
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
    /* ── Deep input fix – webkit autofill + number input ── */
    div[data-baseweb='base-input'],
    div[data-baseweb='input'] {
        background-color: rgba(10,22,35,0.6) !important;
        border-color: rgba(120,145,170,0.2) !important;
    }
    div[data-baseweb='base-input'] input,
    div[data-baseweb='input'] input,
    .stTextInput input,
    .stTextArea textarea,
    .stNumberInput input {
        color: #f5f7fb !important;
        -webkit-text-fill-color: #f5f7fb !important;
        caret-color: #38c2c9 !important;
        background: transparent !important;
        background-color: transparent !important;
    }
    input:-webkit-autofill,
    textarea:-webkit-autofill {
        -webkit-box-shadow: 0 0 0px 1000px rgba(10,22,35,0.95) inset !important;
        -webkit-text-fill-color: #f5f7fb !important;
        caret-color: #38c2c9 !important;
    }
    input::placeholder,
    textarea::placeholder {
        color: rgba(159,176,195,0.6) !important;
        -webkit-text-fill-color: rgba(159,176,195,0.6) !important;
    }
    /* stNumberInput stepper buttons */
    .stNumberInput div[data-baseweb='base-input'] {
        background-color: rgba(10,22,35,0.6) !important;
    }
    /* Dropdown menu options */
    ul[data-baseweb='menu'],
    ul[data-baseweb='menu'] li {
        background-color: rgba(10,22,35,0.95) !important;
        color: #f5f7fb !important;
        -webkit-text-fill-color: #f5f7fb !important;
    }
    ul[data-baseweb='menu'] li:hover,
    li[aria-selected='true'] {
        background-color: rgba(56,194,201,0.12) !important;
    }

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

    /* Back button and secondary buttons – force dark-mode visible */
    button[kind="secondary"],
    .stButton > button[kind="secondary"] {
        background-color: rgba(10,22,35,0.7) !important;
        color: #c8d3df !important;
        border: 1px solid rgba(120,145,170,0.35) !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
    }
    button[kind="secondary"]:hover,
    .stButton > button[kind="secondary"]:hover {
        background-color: rgba(56,194,201,0.08) !important;
        border-color: rgba(56,194,201,0.4) !important;
        color: #38bdf8 !important;
    }

    .stDataFrame { border-radius: 12px; overflow: hidden; }
    .stMarkdown, .stMarkdown p, .stMarkdown li, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4 { color: #f5f7fb !important; }

    .disclaimer-banner { background: rgba(245,158,11,0.06); border: 1px solid rgba(245,158,11,0.25); border-radius: 14px; padding: 1.1rem 1.4rem; margin-top: 2rem; }
    .disclaimer-banner .db-title { font-weight: 700; font-size: 0.9rem; color: #f59e0b; margin-bottom: 4px; }
    .disclaimer-banner .db-text { font-size: 0.82rem; color: #9fb0c3; line-height: 1.5; }

    .status-green { color: #22c55e; font-weight: 700; }
    .status-yellow { color: #f59e0b; font-weight: 700; }
    .status-red { color: #ef4444; font-weight: 700; }

    /* ══ COMPREHENSIVE DARK-THEME FIXES ══ */
    /* DataFrames/Tables */
    .stDataFrame [data-testid="glideDataEditor"], .dvn-scroller, .dvn-scroller div { background-color: #0c1c2c !important; }
    .stDataFrame th, .stDataFrame [role="columnheader"], .gdg-header,
    [data-testid="stDataFrameResizable"] [role="columnheader"] { background-color: #112236 !important; color: #c8d3df !important; border-color: rgba(120,145,170,0.18) !important; }
    .stDataFrame td, .stDataFrame [role="gridcell"],
    [data-testid="stDataFrameResizable"] [role="gridcell"] { background-color: #0c1c2c !important; color: #f5f7fb !important; border-color: rgba(120,145,170,0.18) !important; }
    .stDataFrame tr:hover td, .stDataFrame [role="row"]:hover [role="gridcell"] { background-color: #112236 !important; }
    .gdg-cell, .gdg-cell-text { color: #f5f7fb !important; }
    .stDataFrame [data-testid="stDataFrameResizable"] { border: 1px solid rgba(120,145,170,0.15) !important; border-radius: 12px !important; }

    /* Alerts */
    .stAlert, div[data-testid="stAlert"], .stAlert > div, div[role="alert"] { background-color: #112236 !important; color: #f5f7fb !important; border-color: rgba(120,145,170,0.18) !important; }
    .stAlert p, .stAlert span, .stAlert div, div[role="alert"] p, div[role="alert"] span { color: #f5f7fb !important; }

    /* File uploader */
    .stFileUploader, .stFileUploader > div, [data-testid="stFileUploader"], [data-testid="stFileUploaderDropzone"] { background-color: #0c1c2c !important; border-color: rgba(120,145,170,0.18) !important; color: #f5f7fb !important; }
    .stFileUploader small, .stFileUploader span, [data-testid="stFileUploaderDropzone"] span, [data-testid="stFileUploaderDropzone"] small { color: #9fb0c3 !important; }
    [data-testid="stFileUploaderFile"] { background-color: #112236 !important; color: #f5f7fb !important; }
    [data-testid="stFileUploaderFile"] span, [data-testid="stFileUploaderFile"] small { color: #f5f7fb !important; }

    /* Date picker / calendar */
    .stDateInput input { background-color: #0c1c2c !important; color: #f5f7fb !important; border-color: rgba(120,145,170,0.18) !important; }
    div[data-baseweb="calendar"], div[data-baseweb="calendar"] * { background-color: #162a42 !important; color: #f5f7fb !important; }
    div[data-baseweb="calendar"] [aria-selected="true"] { background-color: #38bdf8 !important; color: #041018 !important; }
    div[data-baseweb="datepicker"] { background-color: #162a42 !important; }

    /* Tooltips */
    div[data-testid="stTooltipIcon"] + div, .stTooltipContent, [data-testid="stTooltipContent"],
    div[data-baseweb="tooltip"] > div { background-color: #162a42 !important; color: #f5f7fb !important; border: 1px solid rgba(120,145,170,0.18) !important; }

    /* Expander */
    .stExpander, details[data-testid="stExpander"], .streamlit-expanderHeader,
    details[data-testid="stExpander"] summary, details[data-testid="stExpander"] > div { background-color: #0c1c2c !important; color: #f5f7fb !important; border-color: rgba(120,145,170,0.18) !important; }

    /* Tab panel */
    .stTabs [data-baseweb="tab-panel"], div[role="tabpanel"] { background-color: transparent !important; color: #f5f7fb !important; }

    /* Number input stepper */
    .stNumberInput button { background-color: #112236 !important; color: #c8d3df !important; border-color: rgba(120,145,170,0.18) !important; }

    /* Toggle */
    .stToggle span, .stCheckbox span, .stRadio span { color: #f5f7fb !important; }

    /* Multi-select tags */
    span[data-baseweb="tag"] { background-color: rgba(56,194,201,0.15) !important; color: #38bdf8 !important; }

    /* Popover/modal */
    div[data-baseweb="modal"] > div, div[data-baseweb="popover"], div[data-baseweb="popover"] > div { background-color: #162a42 !important; color: #f5f7fb !important; }

    /* Toast */
    div[data-baseweb="toast"], div[data-baseweb="snackbar"], .stToast, [data-testid="stToast"] { background-color: #162a42 !important; color: #f5f7fb !important; border: 1px solid rgba(120,145,170,0.18) !important; }

    /* Scrollbars */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: #0c1c2c; border-radius: 4px; }
    ::-webkit-scrollbar-thumb { background: rgba(120,145,170,0.3); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(120,145,170,0.5); }

    /* Catch-all white backgrounds */
    .stApp div[style*="background-color: white"], .stApp div[style*="background-color: rgb(255"],
    .stApp div[style*="background: white"], .stApp div[style*="background: rgb(255"] { background-color: #0c1c2c !important; }
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

# ── Session state defaults ──
for _k, _v in {
    "pf": {},           # prefill data
    "selskap_info": {}, # company lookup result
    "kausjon_rows": [{"_uid": "default0", "kausjonist": "", "orgnr": "", "beloep_mnok": 0.0, "type": "Selvskyldner"}],
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

pf = st.session_state.pf

# ── TOP: Upload first ─────────────────────────────────────────────────
render_section("0. Last opp dokumentgrunnlag", "Last opp tilbudbrev, prosjektkalkyle, budsjett og finansieringsgrunnlag — AI forhåndsutfyller alle felt.", "Dokumenter")

uploads = st.file_uploader(
    "Tilbudsbrev, prosjektkalkyle, budsjett, reguleringsplan, takst, leieavtaler, regnskap",
    type=["pdf", "xlsx", "xls", "csv", "docx", "txt"],
    accept_multiple_files=True,
    key="credit_uploads",
)

col_pf, col_clr = st.columns([3, 1])
with col_pf:
    do_prefill = st.button("🔍  Hent data fra dokumenter og forhåndsutfyll", type="primary", use_container_width=True, disabled=not uploads)
with col_clr:
    if st.button("Nullstill skjema", use_container_width=True):
        st.session_state.pf = {}
        st.session_state.selskap_info = {}
        st.session_state.kausjon_rows = [{"_uid": "reset0", "kausjonist": "", "orgnr": "", "beloep_mnok": 0.0, "type": "Selvskyldner"}]
        st.rerun()

if do_prefill and uploads:
    client_type_pf, client_pf = get_ai_client()
    if client_pf:
        with st.spinner("Leser dokumenter og forhåndsutfyller..."):
            raw_text = extract_text_from_uploads(uploads)
            extracted = prefill_from_docs(client_type_pf, client_pf, raw_text)
        if extracted:
            st.session_state.pf = extracted
            # Replace kausjon rows completely – filter out empty/zero default rows first
            kausjoner = extracted.get("kausjoner", [])
            filled = [k for k in kausjoner if k.get("kausjonist") or k.get("beloep_mnok", 0) > 0]
            if filled:
                # Auto-lookup missing org.nr from Brreg for each kausjonist
                import uuid as _uuid
                enriched = []
                for k in filled:
                    if not k.get("orgnr") and k.get("kausjonist"):
                        try:
                            info = lookup_company(k["kausjonist"])
                            if info.get("orgnr"):
                                k["orgnr"] = info["orgnr"]
                                k["kausjonist"] = info.get("navn", k["kausjonist"]) or k["kausjonist"]
                        except Exception:
                            pass
                    k["_uid"] = str(_uuid.uuid4())[:8]
                    enriched.append(k)
                st.session_state.kausjon_rows = enriched
            else:
                # Keep one blank row as placeholder
                st.session_state.kausjon_rows = [{"_uid": "pf_empty", "kausjonist": "", "orgnr": "", "beloep_mnok": 0.0, "type": "Selvskyldner"}]
            st.success(f"✓ Data hentet fra dokumentene ({len(filled)} kausjon(er) funnet) — kontroller og juster feltene nedenfor.")
            st.rerun()
        else:
            st.warning("Ingen data funnet i dokumentene. Fyll inn manuelt.")
    else:
        st.error("Ingen AI-nøkkel konfigurert.")

if pf:
    render_html('''<div style="background:rgba(34,197,94,0.06);border:1px solid rgba(34,197,94,0.2);border-radius:12px;padding:0.7rem 1.1rem;margin-bottom:1rem;">
        <span style="color:#22c55e;font-weight:700;font-size:0.85rem;">✓ Forhåndsutfylt fra dokumenter</span>
        <span style="color:#9fb0c3;font-size:0.82rem;"> — kontroller at alle felt er korrekte</span>
    </div>''')

left, right = st.columns([3, 2], gap="large")

with left:
    render_section("1. Prosjekt og låntaker", "Nøkkeldata for prosjektet og lånesøknaden.", "Input")

    c1, c2 = st.columns(2)
    with c1:
        prosjekt_navn = st.text_input("Prosjektnavn", value=pf.get("prosjekt_navn", ""), placeholder="F.eks. Steinan Park BT3")
        laantaker = st.text_input("Låntaker / utbygger", value=pf.get("laantaker", ""), placeholder="Selskap AS")
        orgnr_raw = pf.get("orgnr", "")
        orgnr = st.text_input("Org.nr.", value=orgnr_raw, placeholder="999 888 777")
        laanetype_opts = ["Byggelån", "Tomtelån", "Kombinert tomte- og byggelån", "Infralån", "Langsiktig lån (utleie)", "Refinansiering"]
        pf_lt = pf.get("laanetype", "Byggelån")
        lt_idx = laanetype_opts.index(pf_lt) if pf_lt in laanetype_opts else 0
        laanetype = st.selectbox("Lånetype", laanetype_opts, index=lt_idx)
        soekt_laan = st.number_input("Søkt lån (MNOK)", min_value=0.0, value=float(pf.get("soekt_laan_mnok", 0) or 0), step=1.0, format="%.1f")
    with c2:
        totalinvestering = st.number_input("Totalinvestering (MNOK)", min_value=0.0, value=float(pf.get("totalinvestering_mnok", 0) or 0), step=1.0, format="%.1f")
        egenkapital = st.number_input("Egenkapital (MNOK)", min_value=0.0, value=float(pf.get("egenkapital_mnok", 0) or 0), step=1.0, format="%.1f")
        pt_opts = ["Bolig - salg", "Bolig - utleie", "Kontor", "Handel/retail", "Logistikk", "Mixed-use", "Hotell", "Annet"]
        pf_pt = pf.get("prosjekttype", "Bolig - salg")
        pt_idx = pt_opts.index(pf_pt) if pf_pt in pt_opts else 0
        prosjekttype = st.selectbox("Prosjekttype", pt_opts, index=pt_idx)
        ef_opts = ["Totalentreprise", "Hovedentreprise", "Delte entrepriser", "Byggherrestyrt", "Annet"]
        pf_ef = pf.get("entrepriseform", "Totalentreprise")
        ef_idx = ef_opts.index(pf_ef) if pf_ef in ef_opts else 0
        entrepriseform = st.selectbox("Entrepriseform", ef_opts, index=ef_idx)

    # Company lookup
    col_lu, col_lu_btn = st.columns([3, 1])
    with col_lu:
        lu_query = st.text_input("Selskapssøk (navn eller org.nr.)", value=orgnr or laantaker, label_visibility="collapsed", placeholder="Søk selskap på proff.no / Brreg...")
    with col_lu_btn:
        do_lookup = st.button("🔎 Søk", use_container_width=True)
    if do_lookup and lu_query:
        with st.spinner("Søker i Brønnøysundregistrene..."):
            st.session_state.selskap_info = lookup_company(lu_query)
    if st.session_state.selskap_info:
        si = st.session_state.selskap_info
        if si.get("feil"):
            st.warning(f"Selskapssøk: {si['feil']}")
        else:
            # Header line
            konkurs_tag = ' <span style="background:#ef4444;color:#fff;padding:1px 6px;border-radius:4px;font-size:0.72rem;font-weight:700;">KONKURS</span>' if si.get("konkurs") else ""
            avv_tag = ' <span style="background:#f59e0b;color:#fff;padding:1px 6px;border-radius:4px;font-size:0.72rem;font-weight:700;">UNDER AVVIKLING</span>' if si.get("under_avvikling") else ""
            meta = []
            if si.get("orgnr"): meta.append(f"Org.nr: {si['orgnr']}")
            if si.get("organisasjonsform"): meta.append(si["organisasjonsform"])
            if si.get("bransje"): meta.append(si["bransje"])
            if si.get("ansatte"): meta.append(f"{si['ansatte']} ansatte")
            if si.get("stiftelsesaar"): meta.append(f"Stiftet {si['stiftelsesaar']}")
            if si.get("adresse"): meta.append(si["adresse"])
            roller_str = " · ".join(filter(None, [
                f"Styreleder: {si['styreleder']}" if si.get("styreleder") else "",
                f"Daglig leder: {si['daglig_leder']}" if si.get("daglig_leder") else "",
            ]))

            # Regnskap table
            reg_rows = si.get("regnskap", [])
            reg_html = ""
            if reg_rows:
                reg_html = '<table style="width:100%;border-collapse:collapse;margin-top:0.5rem;font-size:0.82rem;">'
                reg_html += '<tr style="color:#9fb0c3;border-bottom:1px solid rgba(120,145,170,0.2);">'
                for col in ["År", "Omsetning", "Driftsres.", "Årsres.", "EK", "EK-andel", "Totalkapital"]:
                    reg_html += f'<th style="text-align:right;padding:3px 8px;font-weight:600;">{col}</th>'
                reg_html += "</tr>"
                for r in reg_rows:
                    aarsres_raw = r.get("_aarsresultat_nok") or 0
                    res_color = "#22c55e" if (aarsres_raw or 0) >= 0 else "#ef4444"
                    reg_html += f'<tr style="border-bottom:1px solid rgba(120,145,170,0.1);">'
                    reg_html += f'<td style="text-align:right;padding:3px 8px;color:#c8d3df;font-weight:600;">{r["aar"]}</td>'
                    reg_html += f'<td style="text-align:right;padding:3px 8px;color:#f5f7fb;">{r["omsetning"]}</td>'
                    reg_html += f'<td style="text-align:right;padding:3px 8px;color:#f5f7fb;">{r["driftsresultat"]}</td>'
                    reg_html += f'<td style="text-align:right;padding:3px 8px;color:{res_color};font-weight:700;">{r["aarsresultat"]}</td>'
                    reg_html += f'<td style="text-align:right;padding:3px 8px;color:#f5f7fb;">{r["egenkapital"]}</td>'
                    reg_html += f'<td style="text-align:right;padding:3px 8px;color:#9fb0c3;">{r["ek_andel"]}</td>'
                    reg_html += f'<td style="text-align:right;padding:3px 8px;color:#9fb0c3;">{r["totalkapital"]}</td>'
                    reg_html += "</tr>"
                reg_html += '</table>'

            render_html(f'''
            <div style="background:rgba(56,194,201,0.04);border:1px solid rgba(56,194,201,0.18);border-radius:12px;padding:0.9rem 1.1rem;margin:0.5rem 0;">
                <div style="font-size:0.98rem;font-weight:700;color:#f5f7fb;margin-bottom:3px;">
                    {si.get("navn","")}{konkurs_tag}{avv_tag}
                </div>
                <div style="font-size:0.82rem;color:#9fb0c3;margin-bottom:2px;">{" · ".join(meta)}</div>
                {f'<div style="font-size:0.8rem;color:#9fb0c3;margin-bottom:4px;">{roller_str}</div>' if roller_str else ""}
                {reg_html if reg_html else '<div style="font-size:0.8rem;color:#9fb0c3;margin-top:4px;">Regnskapstall ikke tilgjengelig i Regnskapsregisteret</div>'}
                <div style="font-size:0.72rem;color:#555;margin-top:5px;">Kilde: {si.get("kilde","")}</div>
            </div>''')

    render_section("2. Tomt og regulering", "Tomt, BRA-i, regulering, gnr/bnr og godkjenningsstatus.", "Regulering")

    c3, c4 = st.columns(2)
    with c3:
        antall_enheter = st.number_input("Antall enheter", min_value=0, value=int(pf.get("antall_enheter", 0) or 0), step=1)
        bra_i_kvm = st.number_input("BRA-i / SBRA (kvm)", min_value=0, value=int(pf.get("bra_i_kvm", 0) or 0), step=100, help="Salgbart innendørs bruksareal — brukes for entreprisekost og salgsinntekt per kvm")
        tomt_kvm = st.number_input("Tomt (kvm)", min_value=0, value=int(pf.get("tomt_kvm", 0) or 0), step=100)
        reg_opts = ["Vedtatt", "Under behandling", "Ikke påbegynt", "Krever omregulering"]
        reguleringsplan = st.selectbox("Reguleringsplan", reg_opts)
    with c4:
        gnr_bnr = st.text_input("Gnr/bnr (panteobjekt)", value=pf.get("gnr_bnr", ""), placeholder="Gnr. 81, bnr. 56, 57, 10, 154, 155, 156", help="Matrikkeldata — grunnlag for bankens pant")
        rg_opts = ["Godkjent", "Søkt", "Ikke søkt"]
        rammegodkjenning = st.selectbox("Rammegodkjenning / IG", rg_opts)
        byggestart = st.date_input("Planlagt byggestart", value=date(2026, 9, 1))
        ferdigstillelse = st.date_input("Planlagt ferdigstillelse", value=date(2028, 12, 31))

    c3b, c4b = st.columns(2)
    with c3b:
        forhaandssalg = st.number_input("Forhåndssalg/utleiegrad (%)", min_value=0, max_value=100, value=int(pf.get("forhaandssalg_pst", 0) or 0), step=5)
        kommune = st.text_input("Kommune", value=pf.get("kommune", ""), placeholder="Trondheim")
    with c4b:
        planident = st.text_input("Planident / reg.plan", value=pf.get("planident", ""), placeholder="r20200040")

    # ── Multi-lån: Tomtelån + Byggelån + Infralån ──
    render_section("2b. Finansieringsstruktur", "Prosjekter kan ha flere lånetyper. Spesifiser alle aktuelle.", "Lån")

    render_html('''<div style="background:rgba(56,194,201,0.06);border:1px solid rgba(56,194,201,0.18);border-radius:12px;padding:0.8rem 1.1rem;margin:0.6rem 0;">
        <div style="font-weight:700;font-size:0.85rem;color:#38bdf8;margin-bottom:3px;">Flere lånetyper</div>
        <div style="font-size:0.82rem;color:#9fb0c3;">Utviklingsprosjekter kan ha tomtelån (for ubebygd tomt), byggelån (for produksjon) og infralån (for infrastruktur som betjener flere byggetrinn). Spesifiser alle aktuelle lån.</div>
    </div>''')

    har_tomtelaan = st.toggle("Tomtelån (separat)", value=bool(pf.get("tomtelaan_mnok", 0)))
    tomtelaan_mnok = 0.0
    tomtelaan_tomt_takst = 0.0
    if har_tomtelaan:
        tl1, tl2 = st.columns(2)
        with tl1:
            tomtelaan_mnok = st.number_input("Tomtelån (MNOK)", min_value=0.0, value=float(pf.get("tomtelaan_mnok", 0) or 0), step=1.0, format="%.1f",
                                              help="Separat tomtelån for arealer som ikke er del av byggelånet")
        with tl2:
            tomtelaan_tomt_takst = st.number_input("Takst/verdi tomt (MNOK)", min_value=0.0, value=float(pf.get("tomtelaan_tomt_takst_mnok", 0) or 0), step=1.0, format="%.1f",
                                                    help="Takstverdi eller avtalt kjøpspris for tomten som sikkerhet for tomtelånet")

    har_infralaan = st.toggle("Infralån (forskuttering infrastruktur)", value=bool(pf.get("infralaan_mnok", 0)))
    infralaan_mnok = 0.0
    if har_infralaan:
        il1, il2 = st.columns(2)
        with il1:
            infralaan_mnok = st.number_input("Infralån (MNOK)", min_value=0.0, value=float(pf.get("infralaan_mnok", 0) or 0), step=1.0, format="%.1f",
                                              help="Forskuttering av infrastruktur som betjener flere byggetrinn. Behandles som tomtelån av banken.")
        with il2:
            render_html('''<div style="background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.18);border-radius:10px;padding:0.6rem 0.9rem;margin-top:1.4rem;">
                <div style="font-size:0.78rem;color:#f59e0b;font-weight:700;">Bankbehandling</div>
                <div style="font-size:0.76rem;color:#9fb0c3;line-height:1.4;">Infralån er en egen trekkramme som behandles som tomtelån (maks 70% LTV samlet med tomtelånet). Trekkes etter hvert som infraarbeid utføres, nedkvitteres i takt med salg.</div>
            </div>''')

    render_section("3. Rentevilkår og finansstruktur", "Rentebetingelser fra bankens tilbud.", "Rente")
    cr1, cr2, cr3, cr4 = st.columns(4)
    with cr1:
        nibor_margin = st.number_input("NIBOR-margin (%)", min_value=0.0, value=float(pf.get("nibor_margin_pst", 1.75) or 1.75), step=0.05, format="%.2f")
    with cr2:
        provisjon = st.number_input("Provisjon (% kvartal)", min_value=0.0, value=float(pf.get("provisjon_pst_kvartal", 0.15) or 0.15), step=0.01, format="%.2f")
    with cr3:
        etablering = st.number_input("Etableringsgebyr (kr)", min_value=0, value=int(pf.get("etableringsgebyr_nok", 0) or 0), step=50000)
    with cr4:
        loepetid = st.number_input("Løpetid (mnd)", min_value=0, value=int(pf.get("loepetid_mnd", 24) or 24), step=6)

    render_section("4. Økonomi og sikkerheter", "Inntekter, gjeld og pantesikkerhet.", "Økonomi")
    c5, c6 = st.columns(2)
    with c5:
        inntekt = st.number_input("Forventet salgs-/leieinntekt (MNOK)", min_value=0.0, value=float(pf.get("inntekt_mnok", 0) or 0), step=1.0, format="%.1f")
        eksisterende_gjeld = st.number_input("Eksisterende gjeld (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f")
    with c6:
        pantesikkerhet = st.selectbox("Primær pantesikkerhet", ["1. prioritet pant i eiendom", "2. prioritet pant", "Pant i tomt + fremtidig bygg", "Selvskyldnergaranti", "Kombinert"])
        garanti = st.multiselect("Tilleggsgarantier", ["Bankgaranti §12", "Morselskapsgaranti", "Personlig garanti", "Depositum", "Ingen"])

    # ── Kausjoner (dynamic rows) ──────────────────────────────────────────
    render_section("5. Kausjoner og morselskapsgarantier", "Selvskyldner- eller simpelkausjoner fra eiere, morselskap eller andre parter.", "Kausjoner")
    render_html('''<div style="background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.18);border-radius:10px;padding:0.7rem 1rem;margin-bottom:0.8rem;font-size:0.83rem;color:#9fb0c3;">
        Kausjoner kan løfte en rød finansieringsvurdering til gul eller grønn. Oppgi kausjonist, beløp og type.
        AI-analysen søker opp regnskapstallene og vurderer kausjonistens kapasitet.
    </div>''')

    kausjon_rows = st.session_state.kausjon_rows
    # Ensure all rows have unique IDs
    for row in kausjon_rows:
        if "_uid" not in row:
            import uuid as _uuid
            row["_uid"] = str(_uuid.uuid4())[:8]

    rows_to_delete = []
    updated_rows = []

    for i, row in enumerate(kausjon_rows):
        uid = row.get("_uid", str(i))
        is_first = (i == 0)
        n_rows = len(kausjon_rows)
        kc1, kc2, kc3, kc4, kc5, kc6 = st.columns([3, 2, 1.2, 1.5, 0.6, 0.6])
        with kc1:
            kn = st.text_input("Kausjonist", value=row.get("kausjonist", ""), key=f"kn_{uid}",
                               placeholder="Selskapsnavn AS", label_visibility="collapsed" if not is_first else "visible")
        with kc2:
            ko = st.text_input("Org.nr.", value=row.get("orgnr", ""), key=f"ko_{uid}",
                               placeholder="999 888 777", label_visibility="collapsed" if not is_first else "visible")
        with kc3:
            kb = st.number_input("Beløp (MNOK)", value=float(row.get("beloep_mnok", 0) or 0), key=f"kb_{uid}",
                                 step=5.0, format="%.1f", label_visibility="collapsed" if not is_first else "visible")
        with kc4:
            kt_opts = ["Selvskyldner", "Simpel"]
            kt_idx = 0 if row.get("type", "Selvskyldner") == "Selvskyldner" else 1
            kt = st.selectbox("Type", kt_opts, key=f"kt_{uid}", index=kt_idx,
                              label_visibility="collapsed" if not is_first else "visible")
        with kc5:
            if is_first:
                st.markdown("<div style='height:27px'></div>", unsafe_allow_html=True)
            if st.button("🔎", key=f"klu_{uid}", use_container_width=True, help="Slå opp org.nr. fra Brreg"):
                if kn:
                    try:
                        info = lookup_company(kn)
                        if info.get("orgnr"):
                            row["orgnr"] = info["orgnr"]
                            row["kausjonist"] = info.get("navn", kn) or kn
                            st.rerun()
                    except Exception:
                        pass
        with kc6:
            if is_first:
                st.markdown("<div style='height:27px'></div>", unsafe_allow_html=True)
            if n_rows > 1:
                if st.button("✕", key=f"kdel_{uid}", use_container_width=True):
                    rows_to_delete.append(uid)
            else:
                st.write("")

        # Use latest lookup result if orgnr was just updated via button
        ko_final = row.get("orgnr", ko) if row.get("orgnr") and row["orgnr"] != ko else ko
        updated_rows.append({"_uid": uid, "kausjonist": kn, "orgnr": ko_final, "beloep_mnok": kb, "type": kt})

    # Apply deletes AFTER the loop (not during iteration)
    if rows_to_delete:
        updated_rows = [r for r in updated_rows if r.get("_uid") not in rows_to_delete]
        if not updated_rows:
            import uuid as _uuid
            updated_rows = [{"_uid": str(_uuid.uuid4())[:8], "kausjonist": "", "orgnr": "", "beloep_mnok": 0.0, "type": "Selvskyldner"}]
        st.session_state.kausjon_rows = updated_rows
        st.rerun()

    st.session_state.kausjon_rows = updated_rows
    if st.button("+ Legg til kausjon", use_container_width=True):
        import uuid as _uuid
        st.session_state.kausjon_rows.append({"_uid": str(_uuid.uuid4())[:8], "kausjonist": "", "orgnr": "", "beloep_mnok": 0.0, "type": "Selvskyldner"})
        st.rerun()

    total_kausjon_display = sum(r.get("beloep_mnok", 0) or 0 for r in updated_rows if r.get("kausjonist"))
    if total_kausjon_display > 0:
        render_html(f'<div style="text-align:right;font-size:0.85rem;color:#f59e0b;font-weight:700;margin-top:4px;">Sum kausjoner: {total_kausjon_display:.1f} MNOK</div>')

    # ── Kausjonist-analyse ──────────────────────────────────────────────────
    aktive_kausjoner = [r for r in updated_rows if r.get("kausjonist") and (r.get("orgnr") or r.get("kausjonist"))]
    if aktive_kausjoner:
        ka_col1, ka_col2 = st.columns([2, 1])
        with ka_col1:
            do_kausjon_analyse = st.button(
                "📊  Analyser kausjonister (Brreg)",
                use_container_width=True,
                help="Henter regnskap fra Brønnøysundregistrene og vurderer soliditet og kausjonskapasitet"
            )
        with ka_col2:
            if st.button("Nullstill analyse", use_container_width=True):
                st.session_state.pop("kausjon_analyser", None)
                st.rerun()

        if do_kausjon_analyse:
            with st.spinner("Henter regnskap fra Brreg for alle kausjonister..."):
                analyser = {}
                for r in aktive_kausjoner:
                    key = r.get("orgnr") or r.get("kausjonist", "")
                    if key:
                        analyser[key] = analyse_kausjonist(
                            r.get("orgnr") or r.get("kausjonist"),
                            float(r.get("beloep_mnok") or 0)
                        )
                        analyser[key]["_kausjon_beloep"] = float(r.get("beloep_mnok") or 0)
                        analyser[key]["_type"] = r.get("type", "Selvskyldner")
                st.session_state["kausjon_analyser"] = analyser
            st.rerun()

        if "kausjon_analyser" in st.session_state and st.session_state["kausjon_analyser"]:
            for key, ka in st.session_state["kausjon_analyser"].items():
                farge = ka.get("farge", "#9fb0c3")
                vurd  = ka.get("vurdering", "ukjent")
                navn  = ka.get("navn") or key

                # Regnskap mini-tabell
                reg_html = ""
                for r in ka.get("regnskap", [])[:3]:
                    aarsres_raw = r.get("_aarsresultat_nok") or 0
                    res_color = "#22c55e" if aarsres_raw >= 0 else "#ef4444"
                    reg_html += (
                        f'<tr style="border-bottom:1px solid rgba(120,145,170,0.1);">'
                        f'<td style="padding:3px 8px;color:#c8d3df;font-weight:600;">{r["aar"]}</td>'
                        f'<td style="padding:3px 8px;text-align:right;color:#f5f7fb;">{r["omsetning"]}</td>'
                        f'<td style="padding:3px 8px;text-align:right;color:{res_color};font-weight:700;">{r["aarsresultat"]}</td>'
                        f'<td style="padding:3px 8px;text-align:right;color:#f5f7fb;">{r["egenkapital"]}</td>'
                        f'<td style="padding:3px 8px;text-align:right;color:#9fb0c3;">{r.get("gjeld", "-")}</td>'
                        f'<td style="padding:3px 8px;text-align:right;color:#9fb0c3;">{r["ek_andel"]}</td>'
                        f'<td style="padding:3px 8px;text-align:right;color:#9fb0c3;">{r["totalkapital"]}</td>'
                        f'</tr>'
                    )
                if reg_html:
                    reg_html = (
                        '<table style="width:100%;border-collapse:collapse;font-size:0.8rem;margin-top:0.5rem;">'
                        '<tr style="color:#9fb0c3;border-bottom:1px solid rgba(120,145,170,0.2);">'
                        '<th style="padding:3px 8px;text-align:left;">År</th>'
                        '<th style="padding:3px 8px;text-align:right;">Omsetning</th>'
                        '<th style="padding:3px 8px;text-align:right;">Årsresultat</th>'
                        '<th style="padding:3px 8px;text-align:right;">EK</th>'
                        '<th style="padding:3px 8px;text-align:right;">Gjeld</th>'
                        '<th style="padding:3px 8px;text-align:right;">EK-andel</th>'
                        '<th style="padding:3px 8px;text-align:right;">Totalkapital</th>'
                        '</tr>'
                        + reg_html + '</table>'
                    )

                punkter_html = "".join(
                    f'<div style="font-size:0.82rem;color:#c8d3df;margin-bottom:3px;">{p}</div>'
                    for p in ka.get("begrunnelse", [])
                )

                ek_dekning = ""
                if ka.get("ek_vs_kausjon") is not None:
                    ek_dekning = f' · EK-dekning: <span style="color:{farge};font-weight:700;">{ka["ek_vs_kausjon"]:.1f}x kausjonsbeløpet</span>'

                gjeld_info = ""
                if ka.get("gjeldsgrad") is not None:
                    gjeld_info = f' · Gjeldsgrad: <span style="color:#c8d3df;font-weight:600;">{ka["gjeldsgrad"]:.1f}x</span>'
                if ka.get("totalkapital_mnok") is not None:
                    gjeld_info += f' · Totalkapital: <span style="color:#c8d3df;font-weight:600;">{ka["totalkapital_mnok"]:.0f} MNOK</span>'

                render_html(f'''
                <div style="background:rgba(10,22,35,0.6);border:1px solid {farge}44;border-left:4px solid {farge};
                            border-radius:12px;padding:1rem 1.2rem;margin-bottom:0.8rem;">
                    <div style="display:flex;align-items:baseline;gap:0.6rem;margin-bottom:0.4rem;flex-wrap:wrap;">
                        <span style="font-weight:700;font-size:0.95rem;color:#f5f7fb;">{navn}</span>
                        <span style="font-size:0.78rem;color:#9fb0c3;">{ka.get("orgnr","")}</span>
                        <span style="background:{farge}22;border:1px solid {farge}55;border-radius:5px;
                                     padding:1px 8px;font-size:0.75rem;font-weight:700;color:{farge};text-transform:uppercase;">
                            {vurd}
                        </span>
                        <span style="font-size:0.8rem;color:#9fb0c3;">{ka.get("_type","Selvskyldner")} · {ka.get("_kausjon_beloep",0):.1f} MNOK{ek_dekning}{gjeld_info}</span>
                    </div>
                    <div style="margin-bottom:0.5rem;">{punkter_html}</div>
                    {reg_html}
                    <div style="font-size:0.7rem;color:#555;margin-top:5px;">Kilde: {ka.get("kilde","Brreg")}</div>
                </div>''')

    render_section("6. Verdivurdering", "For bolig: residualverdi. For næring: yield-metode.", "Verdivurdering")

    is_bolig = prosjekttype in ["Bolig - salg", "Mixed-use"]
    is_naering = prosjekttype in ["Bolig - utleie", "Kontor", "Handel/retail", "Logistikk", "Hotell", "Mixed-use", "Annet"]

    cv1, cv2 = st.columns(2)
    with cv1:
        har_takst = st.toggle("Foreligger det takst?", value=False)
        takst_mnok = st.number_input("Takstverdi (MNOK)", min_value=0.0, value=0.0, step=1.0, format="%.1f") if har_takst else 0.0
        if har_takst:
            takst_kilde = st.selectbox("Takstkilde", ["Ekstern takstmann", "Internvurdering bank", "Megler", "Utbyggers eget estimat", "Annet"])
            takst_dato = st.date_input("Takstdato", value=date.today())
        else:
            takst_kilde = "Ikke oppgitt"
    with cv2:
        tomtekost_mnok = st.number_input("Betalt / avtalt tomtepris (MNOK)", min_value=0.0, value=float(pf.get("tomtekost_mnok", 0) or 0), step=1.0, format="%.1f")
        entreprisekost_mnok = st.number_input("Entreprisekost (MNOK)", min_value=0.0, value=float(pf.get("entreprisekost_mnok", 0) or 0), step=1.0, format="%.1f")

    if is_bolig:
        render_html("""<div style="background:rgba(56,194,201,0.06);border:1px solid rgba(56,194,201,0.18);border-radius:12px;padding:0.8rem 1.1rem;margin:0.6rem 0;">
            <div style="font-weight:700;font-size:0.85rem;color:#38bdf8;margin-bottom:3px;">Residualverdimetode — BRA-i</div>
            <div style="font-size:0.82rem;color:#9fb0c3;">Salgspris per kvm BRA-i × BRA-i = total salgsverdi. Entreprisekost per kvm BRA-i × BRA-i = total byggekost.</div>
        </div>""")
        cb1, cb2 = st.columns(2)
        with cb1:
            forventet_salgspris_kvm = st.number_input("Salgspris (kr/kvm BRA-i)", min_value=0, value=int(pf.get("forventet_salgspris_kvm", 0) or 0), step=1000)
            bra_kvm = bra_i_kvm  # same field
        with cb2:
            byggekost_kvm = st.number_input("Byggekost entreprise (kr/kvm BRA-i)", min_value=0, value=int(pf.get("byggekost_kvm_bra_i", 0) or 0), step=500)
            target_margin = st.number_input("Minimum utviklermargin (%)", min_value=0.0, value=12.0, step=1.0, format="%.1f")
    else:
        forventet_salgspris_kvm = 0; bra_kvm = bra_i_kvm; byggekost_kvm = 0; target_margin = 12.0

    if is_naering:
        render_html("""<div style="background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.18);border-radius:12px;padding:0.8rem 1.1rem;margin:0.6rem 0;">
            <div style="font-weight:700;font-size:0.85rem;color:#f59e0b;margin-bottom:3px;">Yield-metode (Næring)</div>
            <div style="font-size:0.82rem;color:#9fb0c3;">Verdi = Netto leieinntekt / Markedsyield. Yield on cost &gt; markedsyield for positiv verdiskaping.</div>
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

    spesielle_forhold = st.text_area(
        "Spesielle forhold / merknader",
        value=pf.get("spesielle_forhold", ""),
        placeholder="Krav om rekkefølgebestemmelser, kulturminner, pågående tvister, særskilte vilkår...",
        height=80,
    )

    run_analysis = st.button("⚡  Generer kredittnotat", type="primary", use_container_width=True)

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
        "bra_i_kvm": bra_i_kvm,
        "tomt_kvm": tomt_kvm,
        "gnr_bnr": gnr_bnr,
        "kommune": kommune,
        "planident": planident,
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
        # Multi-lån
        "tomtelaan_mnok": tomtelaan_mnok,
        "tomtelaan_tomt_takst_mnok": tomtelaan_tomt_takst,
        "infralaan_mnok": infralaan_mnok,
        # Rentevilkår
        "nibor_margin_pst": nibor_margin,
        "provisjon_pst_kvartal": provisjon,
        "etableringsgebyr_nok": etablering,
        "loepetid_mnd": loepetid,
        # Kausjoner
        "kausjoner": [{k: v for k, v in r.items() if k != "_uid"} for r in st.session_state.kausjon_rows if r.get("kausjonist")],
        # Selskapssøk-resultat
        "selskapsinfo": st.session_state.selskap_info,
        # Verdivurdering
        "har_takst": har_takst,
        "takst_mnok": takst_mnok,
        "takst_kilde": takst_kilde,
        "tomtekost_mnok": tomtekost_mnok,
        "entreprisekost_mnok": entreprisekost_mnok,
        # Bolig residual (BRA-i)
        "forventet_salgspris_kvm": forventet_salgspris_kvm,
        "bra_kvm": bra_i_kvm,
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
        with st.spinner("Leser dokumenter for AI-analyse..."):
            doc_text = extract_text_from_uploads(uploads)

    with st.spinner("Genererer kredittnotat..."):
        analysis = run_credit_analysis(client_type, client, project_info, doc_text)

    if not analysis:
        st.error("Analysen returnerte ingen resultater. Sjekk dokumentgrunnlaget og prøv igjen.")
        st.stop()

    st.session_state["credit_analysis"] = analysis
    st.session_state["credit_project_info"] = project_info
    st.session_state["traffic_light"] = compute_traffic_light(project_info, analysis)


# ── Display results ──
if "credit_analysis" in st.session_state:
    analysis = st.session_state["credit_analysis"]
    project_info = st.session_state.get("credit_project_info", {})
    tl = st.session_state.get("traffic_light", {})

    render_section("Kredittnotat", "Strukturert beslutningsgrunnlag basert på innsendt dokumentasjon og prosjektdata.", "Resultat")

    # ── TRAFFIC LIGHT BANNER ────────────────────────────────────────────────
    if tl:
        farge = tl.get("farge", "grønn")
        tl_colors = {
            "grønn": ("#22c55e", "rgba(34,197,94,0.08)", "rgba(34,197,94,0.25)", "✅"),
            "gul": ("#f59e0b", "rgba(245,158,11,0.08)", "rgba(245,158,11,0.25)", "⚠️"),
            "rød-betinget": ("#f97316", "rgba(249,115,22,0.08)", "rgba(249,115,22,0.25)", "🔴"),
            "rød": ("#ef4444", "rgba(239,68,68,0.08)", "rgba(239,68,68,0.25)", "🔴"),
        }
        tc, tbg, tborder, ticon = tl_colors.get(farge, tl_colors["grønn"])

        flags_html = ""
        for f in tl.get("red_flags", []):
            flags_html += f'<li style="color:#ef4444;font-size:0.84rem;">🔴 {f}</li>'
        for f in tl.get("yellow_flags", []):
            flags_html += f'<li style="color:#f59e0b;font-size:0.84rem;">⚠️ {f}</li>'
        betingelser_html = ""
        for b in tl.get("betingelser", []):
            betingelser_html += f'<li style="color:#c8d3df;font-size:0.83rem;margin-bottom:3px;">→ {b}</li>'

        tilbud_html = ""
        if tl.get("bankens_tilbud"):
            tilbud_html = f'<div style="margin-top:0.7rem;padding:0.6rem 0.9rem;background:rgba(56,194,201,0.06);border:1px solid rgba(56,194,201,0.2);border-radius:8px;font-size:0.85rem;color:#38bdf8;font-weight:600;">🏦 Bankens tilbud: {tl["bankens_tilbud"]}</div>'

        render_html(f'''
        <div style="background:{tbg};border:2px solid {tborder};border-radius:18px;padding:1.4rem 1.8rem;margin-bottom:1.5rem;">
            <div style="display:flex;align-items:center;gap:0.7rem;margin-bottom:0.7rem;">
                <span style="font-size:1.6rem;">{ticon}</span>
                <div>
                    <div style="font-size:1.1rem;font-weight:800;color:{tc};">{tl.get("status","")}</div>
                    <div style="font-size:0.8rem;color:#9fb0c3;">85%-regel: Bankens maksgrense {tl.get("bank_max_mnok",0):.1f} MNOK · Kausjoner {tl.get("total_kausjon_mnok",0):.1f} MNOK</div>
                </div>
            </div>
            {(f'<ul style="margin:0 0 0.5rem 0;padding-left:1rem;">{flags_html}</ul>') if flags_html else ""}
            {(f'<ul style="margin:0;padding-left:1rem;">{betingelser_html}</ul>') if betingelser_html else ""}
            {tilbud_html}
        </div>''')

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
            if isinstance(br, dict) and (safe_get(br, "residual_tomteverdi_mnok", 0) or safe_get(br, "forventet_salgsverdi_mnok", 0)):
                st.markdown("---")
                st.markdown("**Residualverdiberegning (Bolig)**")
                tomte_ok = safe_get(br, "tomtekost_innenfor_residual", True)

                # Full calculation breakdown
                salgsverdi = float(safe_get(br, 'forventet_salgsverdi_mnok', 0) or 0)
                utbygg_eks_tomt = float(safe_get(br, 'total_utbyggingskost_eks_tomt_mnok', 0) or 0)
                margin_12 = float(safe_get(br, 'minimummargin_12pst_mnok', 0) or 0)
                residual = float(safe_get(br, 'residual_tomteverdi_mnok', 0) or 0)
                tomtekost = float(safe_get(br, 'oppgitt_tomtekost_mnok', 0) or 0)
                faktisk_margin = float(safe_get(br, 'faktisk_margin_pst', 0) or 0)

                render_html(f'''<div style="background:rgba(56,194,201,0.06);border:1px solid rgba(56,194,201,0.18);border-radius:12px;padding:1rem 1.2rem;margin:0.6rem 0;">
                    <div style="font-weight:700;font-size:0.85rem;color:#38bdf8;margin-bottom:8px;">Beregning: Residual tomteverdi</div>
                    <table style="width:100%;font-size:0.85rem;color:#c8d3df;border-collapse:collapse;">
                        <tr><td style="padding:3px 0;">Forventet salgsverdi</td><td style="text-align:right;font-weight:600;">{salgsverdi} MNOK</td></tr>
                        <tr><td style="padding:3px 0;color:#9fb0c3;">− Utbyggingskost ekskl. tomt</td><td style="text-align:right;color:#9fb0c3;">− {utbygg_eks_tomt} MNOK</td></tr>
                        <tr><td style="padding:3px 0;color:#9fb0c3;">− Minimum utviklermargin (12%)</td><td style="text-align:right;color:#9fb0c3;">− {margin_12} MNOK</td></tr>
                        <tr style="border-top:1px solid rgba(120,145,170,0.3);"><td style="padding:5px 0;font-weight:700;color:#38bdf8;">= Residual tomteverdi</td><td style="text-align:right;font-weight:700;color:#38bdf8;">{residual} MNOK</td></tr>
                        <tr style="border-top:1px solid rgba(120,145,170,0.15);"><td style="padding:5px 0;">Oppgitt tomtekostnad</td><td style="text-align:right;font-weight:600;">{tomtekost} MNOK</td></tr>
                        <tr><td style="padding:3px 0;">Faktisk utviklermargin</td><td style="text-align:right;font-weight:600;color:{"#22c55e" if faktisk_margin >= 12 else "#f59e0b" if faktisk_margin >= 10 else "#ef4444"};">{faktisk_margin}%</td></tr>
                    </table>
                </div>''')

                render_metric_cards([
                    (f"{salgsverdi} MNOK", "Forventet salgsverdi", f"{safe_get(br, 'salgsverdi_per_kvm_bra', 0)} kr/kvm BRA"),
                    (f"{residual} MNOK", "Residual tomteverdi", "Maks tomteverdi med 12% margin"),
                    (f"{tomtekost} MNOK", "Oppgitt tomtekost", "✓ OK" if tomte_ok else "⚠ Over residual"),
                    (f"{faktisk_margin}%", "Faktisk margin", "Minimum 12% for boligutvikling"),
                ])

                if not tomte_ok:
                    diff = round(tomtekost - residual, 1) if residual else 0
                    render_html(f"""<div style="background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.25);border-radius:12px;padding:0.8rem 1rem;margin:0.5rem 0;">
                        <div style="font-size:0.85rem;color:#f59e0b;font-weight:700;">⚠ Tomtekost {tomtekost} MNOK overstiger residualverdi {residual} MNOK med {diff} MNOK</div>
                        <div style="font-size:0.82rem;color:#9fb0c3;margin-top:4px;">
                            Residualverdimetoden tilsier at tomten er verdt maks {residual} MNOK for å oppnå 12% margin.
                            Faktisk margin er {faktisk_margin}%. {"Marginen er akseptabel men under 12%-kravet." if faktisk_margin >= 10 else "Marginen er for lav — vurder om kausjoner kompenserer."}
                            Bankrådgiver bør vurdere om avviket er akseptabelt gitt prosjektets øvrige styrker (forhåndssalg, kausjoner, beliggenhet).
                        </div>
                    </div>""")
                else:
                    render_html(f"""<div style="background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.25);border-radius:12px;padding:0.8rem 1rem;margin:0.5rem 0;">
                        <div style="font-size:0.85rem;color:#22c55e;font-weight:700;">✓ Tomtekost innenfor residualverdi</div>
                        <div style="font-size:0.82rem;color:#9fb0c3;margin-top:4px;">Oppgitt tomtekost {tomtekost} MNOK er innenfor residualverdi {residual} MNOK. Faktisk margin {faktisk_margin}%.</div>
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
        project_info["_traffic_light"] = st.session_state.get("traffic_light", {})
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
