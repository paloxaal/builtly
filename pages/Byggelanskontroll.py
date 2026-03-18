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
            try: return int(str(r.get("regnskapsperiode", {}).get("tilOgMed", "0"))[:4])
            except: return 0
        sorted_r = sorted(regnskap_data, key=_year, reverse=True)[:3]
        parsed_regnskap = []
        for r in sorted_r:
            aar = str(r.get("regnskapsperiode", {}).get("tilOgMed", ""))[:4]
            res_r = r.get("resultatregnskapResultat", {}) or {}
            bal_r = r.get("balanseregnskapSumVerdier", {}) or {}
            drift = res_r.get("driftsresultat", {}) or {}
            omsetning_raw = res_r.get("sumInntekter") or res_r.get("driftsinntekter", {}).get("sumDriftsinntekter") if isinstance(res_r.get("driftsinntekter"), dict) else res_r.get("sumInntekter")
            aarsresultat_raw = res_r.get("aarsresultat") or res_r.get("ordinaertResultatFoerSkattekostnad")
            driftsresultat_raw = drift.get("driftsresultat") if isinstance(drift, dict) else None
            ek_raw = bal_r.get("sumEgenkapital")
            totalkapital_raw = bal_r.get("sumEgenkapitalOgGjeld") or bal_r.get("sumGjeldOgEgenkapital")
            gjeld_raw = bal_r.get("sumGjeld")

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
    Henter regnskap fra Brreg og gjør en kort soliditetsanalyse av kausjonisten.
    Returnerer vurdering: sterk / akseptabel / svak / ukjent.
    """
    si = lookup_company(orgnr_or_name)
    if not si.get("navn") or not si.get("regnskap"):
        return {"navn": si.get("navn", orgnr_or_name), "orgnr": si.get("orgnr", ""), "vurdering": "ukjent",
                "farge": "#9fb0c3", "begrunnelse": "Regnskapstall ikke tilgjengelig i Brreg.", "regnskap": []}

    reg = si["regnskap"]  # siste 3 år, nyeste først
    siste = reg[0]

    ek_raw     = siste.get("_ek_nok") or 0
    res_raw    = siste.get("_aarsresultat_nok") or 0
    omset_raw  = siste.get("_omsetning_nok") or 0

    ek_mnok    = round(ek_raw / 1_000_000, 1)
    res_mnok   = round(res_raw / 1_000_000, 1)
    omset_mnok = round(omset_raw / 1_000_000, 1)

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
        "kilde": si.get("kilde", ""),
    }


def compute_traffic_light(project_info: dict, analysis: dict) -> dict:
    """Beregn finansieringsstatus. 85%-regel: bank kan alltid tilby 85% av totale prosjektkostnader."""
    nt = safe_get(analysis, "noekkeltall", {})
    oek = safe_get(analysis, "oekonomisk_analyse", {})
    anbefaling = safe_get(analysis, "anbefaling", "")

    total = float(safe_get(nt, "totalinvestering_mnok", 0) or project_info.get("totalinvestering_mnok", 0) or 0)
    soekt = float(safe_get(nt, "soekt_laan_mnok", 0) or project_info.get("soekt_laan_mnok", 0) or 0)
    ek_pst = float(safe_get(nt, "egenkapitalprosent", 0) or 0)
    ltv = float(safe_get(nt, "belaaningsgrad_ltv", 0) or 0)
    margin_pst = float(safe_get(oek, "resultatmargin_pst", 0) or 0)
    forhaandssalg = int(project_info.get("forhaandssalg_pst", 0) or 0)

    bank_max = round(total * 0.85, 1) if total > 0 else 0
    soekt_ok = soekt <= bank_max if bank_max > 0 and soekt > 0 else True

    kausjoner = project_info.get("kausjoner", [])
    total_kausjon = sum(float(k.get("beloep_mnok", 0) or 0) for k in kausjoner if isinstance(k, dict))

    red_flags, yellow_flags, betingelser = [], [], []

    if margin_pst > 0 and margin_pst < 8:
        red_flags.append(f"Margin {margin_pst:.1f}% under bankens minstekrav på 8%")
    if ek_pst > 0 and ek_pst < 15:
        red_flags.append(f"Egenkapital {ek_pst:.1f}% under minimumskravet 15%")
    if ltv > 90:
        red_flags.append(f"LTV {ltv:.1f}% er over bankens øvre grense 90%")
    if anbefaling == "Ikke anbefalt":
        red_flags.append("AI-analyse anbefaler ikke innvilgelse basert på dokumentgrunnlaget")

    if not soekt_ok and soekt > 0:
        yellow_flags.append(f"Søkt beløp {soekt:.1f} MNOK over 85%-grensen {bank_max:.1f} MNOK")
        betingelser.append(f"Banken kan tilby inntil {bank_max:.1f} MNOK (85% av {total:.1f} MNOK). Resterende {round(soekt-bank_max,1)} MNOK må dekkes av egenkapital eller kausjon.")
    if ltv > 80 and ltv <= 90:
        yellow_flags.append(f"LTV {ltv:.1f}% over anbefalt 80% — krever tilleggssikkerhet")
        betingelser.append("LTV over 80% krever kausjon eller annen tilleggssikkerhet for overskytende del")
    if forhaandssalg > 0 and forhaandssalg < 60:
        yellow_flags.append(f"Forhåndssalg {forhaandssalg}% under anbefalt 60%")
        betingelser.append(f"Forhåndssalg bør økes til 60%+ (nå {forhaandssalg}%) — alternativt kreves høyere kausjonsandel")
    if margin_pst >= 8 and margin_pst < 12:
        yellow_flags.append(f"Margin {margin_pst:.1f}% akseptabel men under preferert 12%")

    # Kausjon kan løfte rød til gul
    kan_kausjon_loefte = False
    if red_flags and total_kausjon > 0:
        gap = soekt - bank_max if soekt > bank_max else 0
        if total_kausjon >= gap * 0.5:
            kan_kausjon_loefte = True
            betingelser.append(f"Kausjoner totalt {total_kausjon:.1f} MNOK kan delvis mitigere — innvilgelse mulig med forsterkede vilkår")

    if red_flags and not kan_kausjon_loefte:
        farge, status = "rød", "Ikke anbefalt innvilget"
        kan_tilby = False
        bankens_tilbud = None
    elif red_flags and kan_kausjon_loefte:
        farge, status = "rød-betinget", "Kan innvilges under forutsetning av tilleggssikkerheter"
        kan_tilby = True
        bankens_tilbud = f"Inntil {bank_max:.1f} MNOK mot kausjon {total_kausjon:.1f} MNOK og oppfyllelse av vilkår"
    elif yellow_flags:
        farge, status = "gul", "Kan innvilges med betingelser"
        kan_tilby = True
        bankens_tilbud = f"Inntil {bank_max:.1f} MNOK mot oppfyllelse av vilkår nedenfor"
    else:
        farge, status = "grønn", "Anbefalt innvilget"
        kan_tilby = True
        bankens_tilbud = f"Opp til {bank_max:.1f} MNOK (85% av {total:.1f} MNOK prosjektkost)"

    return {
        "farge": farge, "status": status, "red_flags": red_flags,
        "yellow_flags": yellow_flags, "betingelser": betingelser,
        "bank_max_mnok": bank_max, "total_kausjon_mnok": total_kausjon,
        "kan_tilby": kan_tilby, "bankens_tilbud": bankens_tilbud,
    }

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
- BRA-i / SBRA: {project_info.get('bra_i_kvm', '')} kvm (salgbart innendørs areal — brukes for entreprisekost og salgsinntekt)
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
- Rentevilkår: NIBOR 3MND + {project_info.get('nibor_margin_pst', 0)}% margin, provisjon {project_info.get('provisjon_pst_kvartal', 0)}% per kvartal, etablering NOK {project_info.get('etableringsgebyr_nok', 0)}, løpetid {project_info.get('loepetid_mnd', 0)} mnd
- 85%-regelen: Bank kan finansiere maks 85% av prosjektkost = {round(float(project_info.get('totalinvestering_mnok', 0) or 0) * 0.85, 1)} MNOK. Vurder om søkt beløp {project_info.get('soekt_laan_mnok', 0)} MNOK er innenfor.
- Kausjoner og tilleggsgarantier: {json.dumps(project_info.get('kausjoner', []), ensure_ascii=False)}
- Selskapsinformasjon låntaker (Brreg/Proff): {json.dumps(project_info.get('selskapsinfo', {}), ensure_ascii=False)}
- Spesielle forhold: {project_info.get('spesielle_forhold', '')}

Verdivurdering og dokumentasjon:
- Har takst: {project_info.get('har_takst', False)}
- Takstverdi: {project_info.get('takst_mnok', 0)} MNOK
- Takstkilde: {project_info.get('takst_kilde', 'Ikke oppgitt')}
- Betalt/avtalt tomtepris: {project_info.get('tomtekost_mnok', 0)} MNOK
- Entreprisekost: {project_info.get('entreprisekost_mnok', 0)} MNOK

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

# ── Reportlab imports (PDF engine) ──────────────────────────────────────────
try:
    from reportlab.lib.pagesizes import A4 as _A4
    from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame,
        Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether, Image)
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm as _mm
    from reportlab.lib import colors as _colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.pdfgen import canvas as _rl_canvas
    _HAS_RL = True
except ImportError:
    _HAS_RL = False

def _safe_pdf(t):
    if t is None: return "-"
    return (str(t).replace("\u2014","-").replace("\u2013","-")
            .replace("\u2018","'").replace("\u2019","'")
            .replace("\u201c",'"').replace("\u201d",'"')
            .encode("latin-1","replace").decode("latin-1"))

def generate_credit_pdf(project_info: dict, analysis: dict) -> bytes:
    if not _HAS_RL:
        return b""
    import io as _io, os as _os

    # ── Constants ──────────────────────────────────────────────────────────
    W, H = _A4
    ML, MR, MT, MB = 18*_mm, 18*_mm, 42*_mm, 28*_mm
    IW = W - ML - MR                      # inner width
    mm = _mm

    # ── Palette ────────────────────────────────────────────────────────────
    C_INK    = _colors.HexColor("#06111A")
    C_NAVY   = _colors.HexColor("#071828")
    C_ACCENT = _colors.HexColor("#38C2C9")
    C_RULE   = _colors.HexColor("#D0D8E0")
    C_MUTED  = _colors.HexColor("#607080")
    C_SOFT   = _colors.HexColor("#384858")
    C_PANEL  = _colors.HexColor("#F0F4F8")
    C_HEAD   = _colors.HexColor("#0C1E2E")
    C_WHITE  = _colors.white
    C_GREEN  = _colors.HexColor("#16A34A")
    C_AMBER  = _colors.HexColor("#D97706")
    C_RED    = _colors.HexColor("#DC2626")
    C_ORANGE = _colors.HexColor("#EA580C")

    STATUS_PALETTE = {
        "Anbefalt innvilget":  (C_GREEN,  "#ECFDF5", "#A7F3D0"),
        "Anbefalt med vilkår": (C_AMBER,  "#FFFBEB", "#FDE68A"),
        "Ikke anbefalt":       (C_RED,    "#FEF2F2", "#FECACA"),
    }
    TL_PALETTE = {
        "grønn":        (C_GREEN,  "#ECFDF5", "#A7F3D0", "GODKJENT"),
        "gul":          (C_AMBER,  "#FFFBEB", "#FDE68A", "BETINGET"),
        "rød-betinget": (C_ORANGE, "#FFF7ED", "#FED7AA", "BETINGET MED TILLEGGSSIKKERHET"),
        "rød":          (C_RED,    "#FEF2F2", "#FECACA", "IKKE ANBEFALT"),
    }

    # ── Style factory ──────────────────────────────────────────────────────
    def S(name, **kw):
        base = dict(fontName="Helvetica", fontSize=9.5, leading=14,
                    textColor=C_INK, spaceAfter=2, spaceBefore=2)
        base.update(kw)
        return ParagraphStyle(name, **base)

    # Pre-build common styles
    s_h2  = S("h2",  fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=C_ACCENT, spaceBefore=14, spaceAfter=3)
    s_body= S("body")
    s_kv_k= S("kvk", fontName="Helvetica-Bold", fontSize=8.5, leading=12, textColor=C_SOFT)
    s_kv_v= S("kvv", fontSize=9, leading=12, textColor=C_INK)
    s_th  = S("th",  fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=C_WHITE)
    s_td  = S("td",  fontSize=8.5, leading=12, textColor=C_INK)
    s_sml = S("sml", fontSize=8, leading=11, textColor=C_MUTED)
    s_bul = S("bul", fontSize=9, leading=13, textColor=C_INK, leftIndent=8)

    # ── Logo path ──────────────────────────────────────────────────────────
    logo_candidates = ["logo.png", "logo-white.png", "logo.jpg",
                       "/app/logo.png", "/app/logo-white.png"]
    logo_path = None
    for lc in logo_candidates:
        if _os.path.exists(lc):
            logo_path = lc
            break

    proj_name  = _safe_pdf(project_info.get("navn", "Prosjekt"))
    laantaker  = _safe_pdf(project_info.get("laantaker", ""))
    laanetype  = _safe_pdf(project_info.get("laanetype", ""))
    dato       = datetime.now().strftime("%d.%m.%Y")

    # ── Numbered canvas with header/footer ────────────────────────────────
    class _NC(_rl_canvas.Canvas):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._states = []
        def showPage(self):
            self._states.append(dict(self.__dict__))
            self._startPage()
        def save(self):
            total = len(self._states)
            for i, st in enumerate(self._states):
                self.__dict__.update(st)
                self._hf(i + 1, total)
                super().showPage()
            super().save()
        def _hf(self, pg, tot):
            self.saveState()
            if pg > 1:
                # Teal header bar
                self.setFillColor(C_ACCENT)
                self.rect(0, H - 8*mm, W, 8*mm, fill=1, stroke=0)
                # Logo in header
                if logo_path:
                    try:
                        self.drawImage(logo_path, ML, H - 7*mm,
                                       width=24*mm, height=6*mm,
                                       preserveAspectRatio=True, mask="auto")
                    except Exception:
                        pass
                self.setFont("Helvetica-Bold", 7.5)
                self.setFillColor(C_INK)
                self.drawString(ML + 28*mm, H - 4.8*mm, proj_name)
                self.setFont("Helvetica-Bold", 7)
                self.setFillColor(_colors.HexColor("#1A3A50"))
                self.drawRightString(W - MR, H - 4.8*mm, "KONFIDENSIELT  |  KREDITTNOTAT")
            # Footer rule
            self.setStrokeColor(C_RULE)
            self.setLineWidth(0.4)
            self.line(ML, 18*mm, W - MR, 18*mm)
            self.setFont("Helvetica", 7)
            self.setFillColor(C_MUTED)
            self.drawString(ML, 13.5*mm, f"Builtly AS  |  AI-assisted engineering. Human-verified.  |  {dato}")
            self.drawRightString(W - MR, 13.5*mm, f"Side {pg} av {tot}")
            self.setFont("Helvetica", 6.3)
            self.setFillColor(_colors.HexColor("#A0B0C0"))
            self.drawCentredString(W/2, 9*mm,
                "Utkast - automatisk generert - krever faglig gjennomgang av kredittavdelingen for fremleggelse for kredittkomite")
            self.restoreState()

    # ── Document ───────────────────────────────────────────────────────────
    buf = _io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=_A4, leftMargin=ML, rightMargin=MR,
                          topMargin=MT, bottomMargin=MB)
    frame = Frame(ML, MB, IW, H - MT - MB, id="main")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame])])

    story = []

    # ═══════════════════════════════════════════════════════════════════════
    # COVER PAGE
    # ═══════════════════════════════════════════════════════════════════════
    def _cover():
        # Navy full-bleed background via canvas drawing (via first-page override)
        # We simulate with a big dark table
        cov = []
        # Spacer to push content to vertical centre
        cov.append(Spacer(1, 28*mm))
        # Top accent line
        cov.append(HRFlowable(width="100%", thickness=2, color=C_ACCENT, spaceAfter=8))
        # Confidential tag
        cov.append(Paragraph("KONFIDENSIELT  |  KREDITTNOTAT", S("cs",
            fontName="Helvetica-Bold", fontSize=8.5, textColor=C_ACCENT, alignment=TA_CENTER, spaceAfter=10)))
        # Title
        cov.append(Paragraph(proj_name.upper(), S("ct",
            fontName="Helvetica-Bold", fontSize=30, leading=35, textColor=C_INK, alignment=TA_CENTER, spaceAfter=4)))
        # Loan type subtitle
        cov.append(Paragraph(laanetype, S("cst",
            fontName="Helvetica", fontSize=14, leading=18, textColor=C_MUTED, alignment=TA_CENTER, spaceAfter=14)))
        cov.append(HRFlowable(width="40%", thickness=0.5, color=C_RULE, spaceAfter=14))
        # Meta block
        meta_rows = [
            ["Låntaker",  laantaker],
            ["Lånetype",  laanetype],
            ["Utarbeidet av", "Builtly AI – kredittanalysemotor"],
            ["Dato",      dato],
            ["Status",    "Utkast – krever faglig kontroll"],
        ]
        mt = Table(
            [[Paragraph(f"<b>{k}</b>", S("mk", fontName="Helvetica-Bold", fontSize=9, textColor=C_SOFT)),
              Paragraph(v, S("mv", fontSize=9, textColor=C_INK))]
             for k,v in meta_rows],
            colWidths=[50*mm, IW-50*mm],
        )
        mt.setStyle(TableStyle([
            ("ROWBACKGROUNDS",(0,0),(-1,-1),[C_PANEL, C_WHITE]),
            ("GRID",         (0,0),(-1,-1),0.3, C_RULE),
            ("TOPPADDING",   (0,0),(-1,-1),5),
            ("BOTTOMPADDING",(0,0),(-1,-1),5),
            ("LEFTPADDING",  (0,0),(-1,-1),8),
        ]))
        cov.append(KeepTogether([mt]))
        cov.append(Spacer(1, 18*mm))
        # Logo centred at bottom
        if logo_path:
            try:
                img = Image(logo_path, width=38*mm, height=10*mm)
                img.hAlign = "CENTER"
                cov.append(img)
                cov.append(Spacer(1, 3*mm))
            except Exception:
                pass
        cov.append(Paragraph("builtly.ai", S("url", fontSize=8, textColor=C_MUTED, alignment=TA_CENTER)))
        cov.append(Spacer(1, 8*mm))
        cov.append(HRFlowable(width="100%", thickness=2, color=C_ACCENT))
        from reportlab.platypus import PageBreak
        cov.append(PageBreak())
        return cov

    story += _cover()

    # ── Helpers ────────────────────────────────────────────────────────────
    def heading(num, title):
        return [
            Spacer(1, 5*mm),
            Paragraph(f'<font color="#38C2C9"><b>{_safe_pdf(str(num))}.</b></font>  <b>{_safe_pdf(title)}</b>', s_h2),
            HRFlowable(width="100%", thickness=0.5, color=C_RULE, spaceAfter=3),
        ]

    def kv(rows, col1=55*mm):
        data = [[Paragraph(_safe_pdf(k), s_kv_k), Paragraph(_safe_pdf(v), s_kv_v)] for k,v in rows]
        t = Table(data, colWidths=[col1, IW - col1])
        t.setStyle(TableStyle([
            ("ROWBACKGROUNDS",(0,0),(-1,-1),[C_PANEL, C_WHITE]),
            ("GRID",         (0,0),(-1,-1),0.3, C_RULE),
            ("TOPPADDING",   (0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("LEFTPADDING",  (0,0),(-1,-1),6),("RIGHTPADDING", (0,0),(-1,-1),6),
            ("VALIGN",       (0,0),(-1,-1),"TOP"),
        ]))
        return [t, Spacer(1, 3*mm)]

    def metrics(items):
        cw = IW / len(items)
        r_val, r_lbl, r_sub = [], [], []
        for val, lbl, sub in items:
            r_val.append(Paragraph(f'<b>{_safe_pdf(val)}</b>',
                S("mv", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=C_ACCENT, alignment=TA_CENTER)))
            r_lbl.append(Paragraph(f'<b>{_safe_pdf(lbl)}</b>',
                S("ml", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=C_SOFT, alignment=TA_CENTER)))
            r_sub.append(Paragraph(_safe_pdf(sub),
                S("ms", fontSize=7, leading=9, textColor=C_MUTED, alignment=TA_CENTER)))
        t = Table([r_val, r_lbl, r_sub], colWidths=[cw]*len(items),
                  rowHeights=[14*mm, 5*mm, 4*mm])
        t.setStyle(TableStyle([
            ("BOX",          (0,0),(-1,-1),0.5, C_RULE),
            ("INNERGRID",    (0,0),(-1,-1),0.3, C_RULE),
            ("BACKGROUND",   (0,0),(-1,-1),C_PANEL),
            ("TOPPADDING",   (0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("VALIGN",       (0,0),(-1,-1),"MIDDLE"),
        ]))
        return [KeepTogether([t, Spacer(1, 4*mm)])]

    def status_block(status, summary):
        col, bg, border = STATUS_PALETTE.get(status, (C_ACCENT, "#EFF8F8", "#A5E7EC"))
        data = [
            [Paragraph(f'<b>{_safe_pdf(status)}</b>',
                S("sh", fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=col))],
            [Paragraph(_safe_pdf(summary),
                S("sb", fontSize=9, leading=13, textColor=C_INK))],
        ]
        t = Table(data, colWidths=[IW])
        t.setStyle(TableStyle([
            ("BACKGROUND",   (0,0),(-1,-1),_colors.HexColor(bg)),
            ("BOX",          (0,0),(-1,-1),1.5, col),
            ("LEFTPADDING",  (0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
            ("TOPPADDING",   (0,0),(0,0),  8),("BOTTOMPADDING",(0,0),(0,0),  4),
            ("TOPPADDING",   (0,1),(0,-1), 4),("BOTTOMPADDING",(0,1),(0,-1), 8),
            ("LINEBELOW",    (0,0),(0,0),  0.5, col),
        ]))
        return [KeepTogether([t, Spacer(1, 4*mm)])]

    def tl_block(tl):
        if not tl: return []
        farge = tl.get("farge","grønn")
        col, bg, border, label = TL_PALETTE.get(farge, TL_PALETTE["grønn"])
        rows = []
        rows.append([Paragraph(f'<b>FINANSIERINGSVURDERING  |  {label}</b>',
            S("tlh", fontName="Helvetica-Bold", fontSize=9.5, textColor=col))])
        if tl.get("bank_max_mnok"):
            rows.append([Paragraph(
                f'Bankens 85%-grense: <b>{tl["bank_max_mnok"]:.1f} MNOK</b>   Sum kausjoner: <b>{tl.get("total_kausjon_mnok",0):.1f} MNOK</b>',
                S("tls", fontSize=8.5, leading=12, textColor=C_INK))])
        for f in tl.get("red_flags",[])+tl.get("yellow_flags",[]):
            rows.append([Paragraph(f"  \u2022  {_safe_pdf(f)}",
                S("tlf", fontSize=8, leading=11, textColor=C_RED if f in tl.get("red_flags",[]) else C_AMBER))])
        for b in tl.get("betingelser",[]):
            rows.append([Paragraph(f"  \u2192  {_safe_pdf(b)}", S("tlb", fontSize=8, leading=11, textColor=C_INK))])
        if tl.get("bankens_tilbud"):
            rows.append([Paragraph(f'  \u2192  <b>{_safe_pdf(tl["bankens_tilbud"])}</b>',
                S("tltb", fontName="Helvetica-Bold", fontSize=8.5, leading=12, textColor=col))])
        t = Table(rows, colWidths=[IW])
        t.setStyle(TableStyle([
            ("BACKGROUND",   (0,0),(-1,-1),_colors.HexColor(bg)),
            ("BOX",          (0,0),(-1,-1),2, col),
            ("LEFTPADDING",  (0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
            ("TOPPADDING",   (0,0),(0,0),  9),("BOTTOMPADDING",(0,-1),(-1,-1),9),
            ("TOPPADDING",   (0,1),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-2),3),
            ("LINEBELOW",    (0,0),(0,0),  0.5, col),
        ]))
        return [KeepTogether([t, Spacer(1, 4*mm)])]

    def dtable(headers, rows, col_widths=None):
        if not rows: return []
        if col_widths is None:
            col_widths = [IW / len(headers)] * len(headers)
        hrow = [Paragraph(f'<b>{_safe_pdf(h)}</b>', s_th) for h in headers]
        data = [hrow] + [[Paragraph(_safe_pdf(str(c)), s_td) for c in row] for row in rows]
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND",   (0,0),(-1,0), C_HEAD),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_PANEL, C_WHITE]),
            ("GRID",         (0,0),(-1,-1),0.3, C_RULE),
            ("TOPPADDING",   (0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("LEFTPADDING",  (0,0),(-1,-1),6),("RIGHTPADDING", (0,0),(-1,-1),6),
            ("VALIGN",       (0,0),(-1,-1),"TOP"),
        ]))
        return [KeepTogether([t, Spacer(1, 4*mm)])]

    def bullets(items, color=None):
        color = color or C_INK
        return [Paragraph(f"  \u2022  {_safe_pdf(item)}",
            S(f"b{i}", fontSize=9, leading=13, textColor=color, leftIndent=8))
                for i, item in enumerate(items) if item]

    sg = safe_get  # already defined in module

    # ═══════════════════════════════════════════════════════════════════════
    # 0. TRAFIKKLYS
    # ═══════════════════════════════════════════════════════════════════════
    tl = project_info.get("_traffic_light", {})
    story += tl_block(tl)

    # ═══════════════════════════════════════════════════════════════════════
    # 1. SAMMENDRAG
    # ═══════════════════════════════════════════════════════════════════════
    story += heading(1, "Sammendrag og anbefaling")
    anbefaling = sg(analysis, "anbefaling", "Ikke vurdert")
    sammendrag = sg(analysis, "sammendrag", "")
    story += status_block(anbefaling, sammendrag)

    # ═══════════════════════════════════════════════════════════════════════
    # 2. NØKKELTALL  (metric cards)
    # ═══════════════════════════════════════════════════════════════════════
    story += heading(2, "Nøkkeltall")
    nt = sg(analysis, "noekkeltall", {})
    if isinstance(nt, dict):
        story += metrics([
            (f'{sg(nt,"totalinvestering_mnok","-")} MNOK', "TOTALINVESTERING",    "Prosjektkostnad"),
            (f'{sg(nt,"soekt_laan_mnok","-")} MNOK',       "SOKT LAN",            "Omsøkt finansiering"),
            (f'{sg(nt,"egenkapitalprosent","-")}%',         "EGENKAPITAL",         f'{sg(nt,"egenkapital_mnok","-")} MNOK'),
            (f'{sg(nt,"belaaningsgrad_ltv","-")}%',         "LTV",                 "Belåningsgrad"),
        ])
        story += metrics([
            (f'{sg(nt,"dscr","-")}',                        "DSCR",                "Debt Service Coverage"),
            (f'{sg(nt,"icr","-")}',                         "ICR",                 "Interest Coverage"),
            (f'{sg(nt,"netto_yield_pst","-")}%',            "NETTO YIELD",         "Avkastning"),
            (f'{sg(nt,"forhaandssalg_utleie_pst","-")}%',   "FORHANDSSALG",        "Utleiegrad"),
        ])
        story += kv([
            ("Estimert markedsverdi:", f'{sg(nt,"estimert_markedsverdi_mnok","-")} MNOK'),
        ])

    # ═══════════════════════════════════════════════════════════════════════
    # 3. VERDIVURDERING
    # ═══════════════════════════════════════════════════════════════════════
    story += heading(3, "Verdivurdering")
    vv = sg(analysis, "verdivurdering", {})
    if isinstance(vv, dict):
        story += kv([
            ("Metode:",              sg(vv, "metode", "-")),
            ("Oppgitt takst:",       f'{sg(vv,"oppgitt_takst_mnok",0)} MNOK'),
            ("Beregnet verdi:",      f'{sg(vv,"beregnet_verdi_mnok",0)} MNOK'),
            ("Avvik takst/beregnet:",f'{sg(vv,"avvik_takst_vs_beregnet_pst",0)}%'),
            ("Takst rimelig:",       "Ja" if sg(vv,"takst_er_rimelig",True) else "NEI - se kommentar"),
            ("Bankens verdianslag:", f'{sg(vv,"bankens_verdianslag_mnok",0)} MNOK'),
            ("Forsiktig verdi 70%:", f'{sg(vv,"forsiktig_verdi_70pst_mnok",0)} MNOK'),
            ("LTV mot beregnet:",    f'{sg(vv,"ltv_mot_beregnet_verdi_pst",0)}%'),
        ])
        if sg(vv, "kommentar_takst", ""):
            story.append(Paragraph(_safe_pdf(sg(vv, "kommentar_takst", "")), s_body))
            story.append(Spacer(1, 3*mm))

        # Bolig residual
        br = sg(vv, "bolig_residual", {})
        if isinstance(br, dict) and sg(br, "residual_tomteverdi_mnok", 0):
            story.append(Paragraph("<b>Residualverdiberegning (Bolig / BRA-i)</b>",
                S("brt", fontName="Helvetica-Bold", fontSize=9.5, textColor=C_ACCENT, spaceAfter=3)))
            story += dtable(
                ["Post", "Verdi"],
                [
                    ["Forventet salgsverdi",  f'{sg(br,"forventet_salgsverdi_mnok",0)} MNOK'],
                    ["Salgspris per kvm BRA-i", f'{sg(br,"salgsverdi_per_kvm_bra",0)} kr'],
                    ["Entreprisekost per kvm BRA-i", f'{sg(br,"byggekost_per_kvm_bra_i",sg(br,"byggekost_per_kvm_bta",0))} kr'],
                    ["Utbyggingskost ekskl. tomt", f'{sg(br,"total_utbyggingskost_eks_tomt_mnok",0)} MNOK'],
                    ["Minimummargin 12%",       f'{sg(br,"minimummargin_12pst_mnok",0)} MNOK'],
                    ["Residual tomteverdi",     f'{sg(br,"residual_tomteverdi_mnok",0)} MNOK'],
                    ["Oppgitt tomtekostnad",    f'{sg(br,"oppgitt_tomtekost_mnok",0)} MNOK'],
                    ["Innenfor residual",        "Ja" if sg(br,"tomtekost_innenfor_residual",True) else "NEI"],
                    ["Faktisk margin",           f'{sg(br,"faktisk_margin_pst",0)}%'],
                ],
                col_widths=[90*mm, IW-90*mm],
            )

        # Næring yield
        ny = sg(vv, "naering_yield", {})
        if isinstance(ny, dict) and sg(ny, "yield_on_cost_pst", 0):
            story.append(Paragraph("<b>Yield-analyse (Næring)</b>",
                S("nyt", fontName="Helvetica-Bold", fontSize=9.5, textColor=C_AMBER, spaceAfter=3)))
            story += dtable(
                ["Post", "Verdi"],
                [
                    ["Brutto leieinntekt",    f'{sg(ny,"brutto_leieinntekt_mnok",0)} MNOK/ar'],
                    ["Eierkostnader",         f'{sg(ny,"eierkostnader_mnok",0)} MNOK/ar'],
                    ["Netto leieinntekt",     f'{sg(ny,"netto_leieinntekt_mnok",0)} MNOK/ar'],
                    ["Yield on cost",         f'{sg(ny,"yield_on_cost_pst",0)}%'],
                    ["Markedsyield",          f'{sg(ny,"antatt_markedsyield_pst",0)}%'],
                    ["Yield spread",          f'{sg(ny,"yield_spread_pst",0)}%'],
                    ["Verdi ved markedsyield",f'{sg(ny,"verdi_ved_markedsyield_mnok",0)} MNOK'],
                    ["WAULT",                 f'{sg(ny,"wault_aar",0)} ar'],
                    ["Vakansrisiko",          f'{sg(ny,"vakansrisiko_pst",0)}%'],
                    ["Verdiskaping",          "Positiv" if sg(ny,"verdiskaping_positiv",True) else "NEGATIV"],
                ],
                col_widths=[90*mm, IW-90*mm],
            )

    # ═══════════════════════════════════════════════════════════════════════
    # 4. REGULERING
    # ═══════════════════════════════════════════════════════════════════════
    story += heading(4, "Regulering og tomt")
    reg = sg(analysis, "regulering_og_tomt", {})
    if isinstance(reg, dict):
        story += kv([
            ("Reguleringsplan:",       sg(reg,"reguleringsplan","-")),
            ("Utnyttelsesgrad BYA:",   f'{sg(reg,"utnyttelsesgrad_bya_pst",0)}%'),
            ("Tillatt vs. planlagt:",  sg(reg,"tillatt_vs_planlagt_bta","-")),
            ("Rammegodkjenning:",      sg(reg,"rammegodkjenning_status","-")),
        ])
        if sg(reg, "kommentar", ""):
            story.append(Paragraph(_safe_pdf(sg(reg,"kommentar","")), s_body))

    # ═══════════════════════════════════════════════════════════════════════
    # 5. OKONOMI
    # ═══════════════════════════════════════════════════════════════════════
    story += heading(5, "Okonomisk analyse")
    oek = sg(analysis, "oekonomisk_analyse", {})
    if isinstance(oek, dict):
        story += dtable(
            ["Post", "MNOK"],
            [
                ["Totalkostnadskalkyle",  sg(oek,"totalkostnadskalkyle_mnok","-")],
                ["Entreprisekostnad",     sg(oek,"entreprisekostnad_mnok","-")],
                ["Tomtekostnad",          sg(oek,"tomtekostnad_mnok","-")],
                ["Offentlige avgifter",   sg(oek,"offentlige_avgifter_mnok","-")],
                ["Prosjektkostnader",     sg(oek,"prosjektkostnader_mnok","-")],
                ["Finanskostnader",       sg(oek,"finanskostnader_mnok","-")],
                ["Forventet salgsverdi",  sg(oek,"forventet_salgsverdi_mnok","-")],
                ["Forventet resultat",    sg(oek,"forventet_resultat_mnok","-")],
                ["Resultatmargin",        f'{sg(oek,"resultatmargin_pst","-")}%'],
            ],
            col_widths=[120*mm, IW-120*mm],
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 6. RENTESENSITIVITET
    # ═══════════════════════════════════════════════════════════════════════
    story += heading(6, "Rentesensitivitet")
    rente = sg(analysis, "rentesensitivitet", [])
    if rente:
        rs_rows = []
        for r in rente:
            if isinstance(r, dict):
                be = sg(r,"betjeningsevne","-")
                rs_rows.append([sg(r,"rentenivaa","-"), sg(r,"aarsresultat_mnok","-"), sg(r,"dscr","-"), be])
        story += dtable(
            ["Renteniva", "Arsresultat (MNOK)", "DSCR", "Betjeningsevne"],
            rs_rows,
            col_widths=[40*mm, 55*mm, 40*mm, IW-135*mm],
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 7. SIKKERHETER
    # ═══════════════════════════════════════════════════════════════════════
    story += heading(7, "Sikkerheter og pant")
    sik = sg(analysis, "sikkerheter", [])
    if sik:
        sik_rows = [[sg(s,"type","-"), sg(s,"prioritet","-"),
                     f'{sg(s,"verdi_mnok",0)} MNOK', sg(s,"kommentar","-")]
                    for s in sik if isinstance(s, dict)]
        story += dtable(
            ["Type", "Prioritet", "Verdi", "Kommentar"],
            sik_rows,
            col_widths=[50*mm, 32*mm, 25*mm, IW-107*mm],
        )

    # Kausjoner
    kausjoner = project_info.get("kausjoner", [])
    if kausjoner:
        story.append(Paragraph("<b>Kausjoner og morselskapsgarantier</b>",
            S("kh", fontName="Helvetica-Bold", fontSize=9.5, textColor=C_ACCENT, spaceAfter=3)))
        kaus_rows = [[sg(k,"kausjonist","-"), sg(k,"type","Selvskyldner"),
                      f'{sg(k,"beloep_mnok",0):.1f} MNOK', sg(k,"orgnr","-")]
                     for k in kausjoner if isinstance(k, dict)]
        story += dtable(
            ["Kausjonist", "Type", "Belop", "Org.nr."],
            kaus_rows,
            col_widths=[80*mm, 35*mm, 30*mm, IW-145*mm],
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 8. RISIKOVURDERING
    # ═══════════════════════════════════════════════════════════════════════
    story += heading(8, "Risikovurdering")
    risiko = sg(analysis, "risikovurdering", [])
    if risiko:
        risk_rows = [[sg(r,"risiko","-"), sg(r,"sannsynlighet","-"),
                      sg(r,"konsekvens","-"), sg(r,"mitigering","-")]
                     for r in risiko if isinstance(r, dict)]
        story += dtable(
            ["Risiko", "Sannsynlighet", "Konsekvens", "Mitigering"],
            risk_rows,
            col_widths=[55*mm, 28*mm, 28*mm, IW-111*mm],
        )

    # ═══════════════════════════════════════════════════════════════════════
    # 9. STYRKER / SVAKHETER
    # ═══════════════════════════════════════════════════════════════════════
    story += heading(9, "Styrker og svakheter")
    styrker  = sg(analysis, "styrker", [])
    svakheter = sg(analysis, "svakheter", [])

    sw_data = []
    max_rows = max(len(styrker), len(svakheter))
    for i in range(max_rows):
        s_txt = f"+ {_safe_pdf(styrker[i])}"  if i < len(styrker)   else ""
        w_txt = f"- {_safe_pdf(svakheter[i])}" if i < len(svakheter) else ""
        sw_data.append([
            Paragraph(s_txt, S(f"ss{i}", fontSize=8.5, leading=12, textColor=C_GREEN)) if s_txt else Paragraph("", s_td),
            Paragraph(w_txt, S(f"sw{i}", fontSize=8.5, leading=12, textColor=C_RED))   if w_txt else Paragraph("", s_td),
        ])
    if sw_data:
        # Header
        sw_head = [
            Paragraph("<b>Styrker</b>", S("sh", fontName="Helvetica-Bold", fontSize=8.5, textColor=C_WHITE)),
            Paragraph("<b>Svakheter</b>", S("swh", fontName="Helvetica-Bold", fontSize=8.5, textColor=C_WHITE)),
        ]
        t = Table([sw_head]+sw_data, colWidths=[IW/2, IW/2])
        t.setStyle(TableStyle([
            ("BACKGROUND",   (0,0),(-1,0), C_HEAD),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[C_PANEL, C_WHITE]),
            ("GRID",         (0,0),(-1,-1),0.3, C_RULE),
            ("TOPPADDING",   (0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
            ("LEFTPADDING",  (0,0),(-1,-1),8),("RIGHTPADDING", (0,0),(-1,-1),8),
            ("VALIGN",       (0,0),(-1,-1),"TOP"),
        ]))
        story += [KeepTogether([t, Spacer(1, 4*mm)])]

    # ═══════════════════════════════════════════════════════════════════════
    # 10. VILKAR
    # ═══════════════════════════════════════════════════════════════════════
    story += heading(10, "Foreslatte vilkar")
    vilkaar = sg(analysis, "vilkaar", [])
    for i, v in enumerate(vilkaar, 1):
        story.append(Paragraph(f"<b>{i}.</b>  {_safe_pdf(v)}",
            S(f"v{i}", fontSize=9, leading=13, textColor=C_INK, leftIndent=6, spaceAfter=3)))

    # ═══════════════════════════════════════════════════════════════════════
    # 11. COVENANTS
    # ═══════════════════════════════════════════════════════════════════════
    story += heading(11, "Covenants")
    cov_list = sg(analysis, "covenants", [])
    if cov_list:
        cov_rows = [[sg(c,"covenant","-"), sg(c,"grenseverdi","-"), sg(c,"maalefrekvens","-")]
                    for c in cov_list if isinstance(c, dict)]
        story += dtable(
            ["Covenant", "Grenseverdi", "Malefrekvens"],
            cov_rows,
            col_widths=[100*mm, 50*mm, IW-150*mm],
        )

    # ═══════════════════════════════════════════════════════════════════════
    # DISCLAIMER BOX
    # ═══════════════════════════════════════════════════════════════════════
    story.append(Spacer(1, 8*mm))
    disc_data = [[
        Paragraph("<b>UTKAST - KREVER FAGLIG KONTROLL</b>",
            S("dt", fontName="Helvetica-Bold", fontSize=8.5, textColor=C_AMBER)),
        Paragraph(
            "Dette kredittnotatet er automatisk generert av Builtly AI og skal gjennomgaes av "
            "ansvarlig kredittanalytiker foer fremleggelse for kredittkomite. Builtly AS paatar seg "
            "ikke ansvar for eventuelle feil eller mangler i det automatisk genererte innholdet.",
            S("db", fontSize=8, leading=12, textColor=C_SOFT)),
    ]]
    disc_t = Table(disc_data, colWidths=[60*mm, IW-60*mm])
    disc_t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0),(-1,-1), _colors.HexColor("#FFFBEB")),
        ("BOX",         (0,0),(-1,-1), 1, _colors.HexColor("#FDE68A")),
        ("LEFTPADDING", (0,0),(-1,-1), 10),("RIGHTPADDING",(0,0),(-1,-1),10),
        ("TOPPADDING",  (0,0),(-1,-1), 8), ("BOTTOMPADDING",(0,0),(-1,-1),8),
        ("VALIGN",      (0,0),(-1,-1), "TOP"),
    ]))
    story.append(disc_t)

    # ── Build ──────────────────────────────────────────────────────────────
    doc.build(story, canvasmaker=lambda *a, **kw: _NC(*a, **kw))
    return buf.getvalue()


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
    "kausjon_rows": [{"kausjonist": "", "orgnr": "", "beloep_mnok": 0.0, "type": "Selvskyldner"}],
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
        st.session_state.kausjon_rows = [{"kausjonist": "", "orgnr": "", "beloep_mnok": 0.0, "type": "Selvskyldner"}]
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
                    enriched.append(k)
                st.session_state.kausjon_rows = enriched
            else:
                # Keep one blank row as placeholder
                st.session_state.kausjon_rows = [{"kausjonist": "", "orgnr": "", "beloep_mnok": 0.0, "type": "Selvskyldner"}]
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

    render_section("2. Tomt og regulering", "Tomt, BRA-i, regulering og godkjenningsstatus.", "Regulering")

    c3, c4 = st.columns(2)
    with c3:
        antall_enheter = st.number_input("Antall enheter", min_value=0, value=int(pf.get("antall_enheter", 0) or 0), step=1)
        bra_i_kvm = st.number_input("BRA-i / SBRA (kvm)", min_value=0, value=int(pf.get("bra_i_kvm", 0) or 0), step=100, help="Salgbart innendørs bruksareal — brukes for entreprisekost og salgsinntekt per kvm")
        tomt_kvm = st.number_input("Tomt (kvm)", min_value=0, value=int(pf.get("tomt_kvm", 0) or 0), step=100)
        reg_opts = ["Vedtatt", "Under behandling", "Ikke påbegynt", "Krever omregulering"]
        reguleringsplan = st.selectbox("Reguleringsplan", reg_opts)
    with c4:
        rg_opts = ["Godkjent", "Søkt", "Ikke søkt"]
        rammegodkjenning = st.selectbox("Rammegodkjenning / IG", rg_opts)
        byggestart = st.date_input("Planlagt byggestart", value=date(2026, 9, 1))
        ferdigstillelse = st.date_input("Planlagt ferdigstillelse", value=date(2028, 12, 31))
        forhaandssalg = st.number_input("Forhåndssalg/utleiegrad (%)", min_value=0, max_value=100, value=int(pf.get("forhaandssalg_pst", 0) or 0), step=5)

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
    updated_rows = []
    for i, row in enumerate(kausjon_rows):
        # Header row labels only for first row
        kc1, kc2, kc3, kc4, kc5, kc6 = st.columns([3, 2, 1.5, 1.5, 0.5, 0.5])
        with kc1:
            kn = st.text_input("Kausjonist", value=row.get("kausjonist", ""), key=f"kn_{i}", placeholder="Fredensborg Bolig AS", label_visibility="collapsed" if i > 0 else "visible")
        with kc2:
            ko_val = row.get("orgnr", "")
            ko = st.text_input("Org.nr.", value=ko_val, key=f"ko_{i}", placeholder="919 998 296", label_visibility="collapsed" if i > 0 else "visible")
        with kc3:
            kb = st.number_input("Beloep (MNOK)", value=float(row.get("beloep_mnok", 0) or 0), key=f"kb_{i}", step=5.0, format="%.1f", label_visibility="collapsed" if i > 0 else "visible")
        with kc4:
            kt_opts = ["Selvskyldner", "Simpel"]
            kt = st.selectbox("Type", kt_opts, key=f"kt_{i}", index=0 if row.get("type", "Selvskyldner") == "Selvskyldner" else 1, label_visibility="collapsed" if i > 0 else "visible")
        with kc5:
            # Lookup org.nr button – visible when name is filled but no org.nr yet
            lookup_label = "🔎" if i > 0 else "Søk"
            if st.button(lookup_label, key=f"klu_{i}", use_container_width=True, help="Slå opp org.nr. fra Brreg"):
                if kn:
                    try:
                        info = lookup_company(kn)
                        if info.get("orgnr"):
                            st.session_state.kausjon_rows[i]["orgnr"] = info["orgnr"]
                            st.session_state.kausjon_rows[i]["kausjonist"] = info.get("navn", kn) or kn
                            st.rerun()
                    except Exception:
                        pass
        with kc6:
            del_label = "✕" if i > 0 else " "
            if i > 0 and st.button("✕", key=f"kdel_{i}", use_container_width=True):
                kausjon_rows.pop(i); st.session_state.kausjon_rows = kausjon_rows; st.rerun()
        # Use latest lookup result for orgnr if it was just updated
        ko_final = st.session_state.kausjon_rows[i].get("orgnr", ko) if i < len(st.session_state.kausjon_rows) else ko
        updated_rows.append({"kausjonist": kn, "orgnr": ko_final, "beloep_mnok": kb, "type": kt})

    st.session_state.kausjon_rows = updated_rows
    if st.button("+ Legg til kausjon", use_container_width=True):
        st.session_state.kausjon_rows.append({"kausjonist": "", "orgnr": "", "beloep_mnok": 0.0, "type": "Selvskyldner"})
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
                        <span style="font-size:0.8rem;color:#9fb0c3;">{ka.get("_type","Selvskyldner")} · {ka.get("_kausjon_beloep",0):.1f} MNOK{ek_dekning}</span>
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
        # Rentevilkår
        "nibor_margin_pst": nibor_margin,
        "provisjon_pst_kvartal": provisjon,
        "etableringsgebyr_nok": etablering,
        "loepetid_mnd": loepetid,
        # Kausjoner
        "kausjoner": [r for r in st.session_state.kausjon_rows if r.get("kausjonist")],
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
