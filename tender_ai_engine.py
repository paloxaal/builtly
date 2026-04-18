# -*- coding: utf-8 -*-
"""
Builtly | Tender AI Engine
─────────────────────────────────────────────────────────────────
3-pass analyse av anbudsgrunnlag med Claude som primær backend.

Pass 1: Per-dokument ekstraksjon (frister, beløp, krav, klausuler).
Pass 2: Krysskontroll mellom dokumenter (motstrid, gap, dobbeltkrav).
Pass 3: Tilbudsstrategi (go/no-go, pris-sensitive punkter, RFI-prio,
        hvilke fag bør sendes til ekstern prising).

Fallback-rekkefølge: Claude → OpenAI → Gemini.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# ─── Backend probes ──────────────────────────────────────────────
try:
    import anthropic
except ImportError:
    anthropic = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    import google.generativeai as genai
except ImportError:
    genai = None


ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
GOOGLE_KEY = os.environ.get("GOOGLE_API_KEY", "").strip()

HAS_CLAUDE = bool(ANTHROPIC_KEY and anthropic is not None)
HAS_OPENAI = bool(OPENAI_KEY and OpenAI is not None)
HAS_GEMINI = bool(GOOGLE_KEY and genai is not None)
HAS_ANY_AI = HAS_CLAUDE or HAS_OPENAI or HAS_GEMINI

# Configure Gemini once if present
if HAS_GEMINI:
    try:
        genai.configure(api_key=GOOGLE_KEY)
    except Exception:
        HAS_GEMINI = False


# ─── Model selection ─────────────────────────────────────────────
def _claude_model_for_level(qa_level: str) -> str:
    """Pick Claude model based on control depth."""
    if qa_level in ("Pre-bid review", "Dyp"):
        return os.environ.get("ANTHROPIC_MODEL_DEEP", "claude-opus-4-6")
    return os.environ.get("ANTHROPIC_MODEL_FAST", "claude-sonnet-4-6")


# ─── Low-level call wrappers ─────────────────────────────────────
_CLAUDE_CLIENT = None
_OPENAI_CLIENT = None


def _get_claude():
    global _CLAUDE_CLIENT
    if _CLAUDE_CLIENT is None and HAS_CLAUDE:
        try:
            _CLAUDE_CLIENT = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        except Exception:
            return None
    return _CLAUDE_CLIENT


def _get_openai():
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is None and HAS_OPENAI:
        try:
            _OPENAI_CLIENT = OpenAI(api_key=OPENAI_KEY)
        except Exception:
            return None
    return _OPENAI_CLIENT


def _call_claude(
    system: str,
    user: str,
    model: str,
    max_tokens: int = 4096,
    temperature: float = 0.1,
) -> Tuple[str, Dict[str, Any]]:
    """Call Claude. Returns (text, meta)."""
    client = _get_claude()
    if not client:
        return "", {"backend": "claude", "status": "no_client"}
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        usage = getattr(resp, "usage", None)
        return text, {
            "backend": "claude",
            "model": model,
            "status": "ok",
            "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
            "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
        }
    except Exception as e:
        return "", {"backend": "claude", "status": "error", "error": f"{type(e).__name__}: {e}"}


def _call_openai(system: str, user: str, max_tokens: int = 4096, temperature: float = 0.1) -> Tuple[str, Dict[str, Any]]:
    client = _get_openai()
    if not client:
        return "", {"backend": "openai", "status": "no_client"}
    try:
        model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content or ""
        return text, {"backend": "openai", "model": model, "status": "ok"}
    except Exception as e:
        return "", {"backend": "openai", "status": "error", "error": f"{type(e).__name__}: {e}"}


def _call_gemini(system: str, user: str, temperature: float = 0.1) -> Tuple[str, Dict[str, Any]]:
    if not HAS_GEMINI:
        return "", {"backend": "gemini", "status": "no_client"}
    try:
        model_name = os.environ.get("GEMINI_MODEL", "models/gemini-2.0-flash")
        model = genai.GenerativeModel(model_name, system_instruction=system)
        resp = model.generate_content(user, generation_config={"temperature": temperature})
        return getattr(resp, "text", "") or "", {"backend": "gemini", "model": model_name, "status": "ok"}
    except Exception as e:
        return "", {"backend": "gemini", "status": "error", "error": f"{type(e).__name__}: {e}"}


def call_ai(
    system: str,
    user: str,
    qa_level: str = "Standard",
    max_tokens: int = 4096,
    temperature: float = 0.1,
) -> Tuple[str, Dict[str, Any]]:
    """
    Call the best available AI backend with fallback chain.
    Claude → OpenAI → Gemini.
    """
    if HAS_CLAUDE:
        text, meta = _call_claude(
            system, user,
            model=_claude_model_for_level(qa_level),
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if text:
            return text, meta
        # fall through on empty/error

    if HAS_OPENAI:
        text, meta = _call_openai(system, user, max_tokens=max_tokens, temperature=temperature)
        if text:
            return text, meta

    if HAS_GEMINI:
        text, meta = _call_gemini(system, user, temperature=temperature)
        if text:
            return text, meta

    return "", {"backend": "none", "status": "no_backend"}


# ─── JSON parsing helpers ────────────────────────────────────────
def _extract_json(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"```$", "", t).strip()
    # Try to find largest {...} block
    first = t.find("{")
    last = t.rfind("}")
    if first != -1 and last > first:
        t = t[first:last + 1]
    t = re.sub(r",\s*([}\]])", r"\1", t)
    return t


def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    blob = _extract_json(text)
    if not blob:
        return None
    try:
        return json.loads(blob)
    except Exception:
        # One repair attempt
        try:
            repaired = blob.replace("\t", " ").replace("\r", " ")
            repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
            return json.loads(repaired)
        except Exception:
            return None


# ═════════════════════════════════════════════════════════════════
# PASS 1 — Per-document structured extraction
# ═════════════════════════════════════════════════════════════════
PASS1_SYSTEM = """Du er Builtly Tender AI, en ekspert på norsk anbudsanalyse.
Du analyserer ETT dokument om gangen og trekker ut strukturert informasjon.

Fokusområder:
- Frister (tilbudsfrist, vedståelsesfrist, oppstart, ferdigstillelse)
- Økonomiske størrelser (kontraktsverdi, opsjoner, dagmulkt, sikkerhetsstillelse)
- Kontraktsbestemmelser (NS 8405/8406/8407, reguleringsbestemmelser)
- Tildelings- og kvalifikasjonskriterier
- Spesielle krav (materialer, utførelse, ansvarsrett, lærlinger)
- Mengdeposter og prisstruktur hvis prisskjema
- Usikkerheter, uklare krav, motstridige henvisninger INNE i dokumentet

Returner KUN gyldig JSON. Ingen forklarende tekst utenfor JSON-blokken."""

PASS1_SCHEMA = """{
  "doc_summary": "2-3 setninger om hva dokumentet inneholder",
  "doc_type_confirmed": "konkurransegrunnlag|beskrivelse|tegning|kontrakt|prisskjema|sha|miljo|rigg|brann|geo|ifc|kvalifikasjon|tildelingskriterier|annet",
  "deadlines": [
    {"type": "tilbudsfrist|vedståelsesfrist|oppstart|ferdigstillelse|befaring|annet", "date": "DD.MM.YYYY eller tekst", "quote": "kort sitat fra dokumentet (<15 ord)"}
  ],
  "contract": {
    "ns_standard": "NS 8405 | NS 8406 | NS 8407 | null",
    "contract_value_mnok": 0.0,
    "dagmulkt": "kort beskrivelse eller null",
    "sikkerhetsstillelse": "kort beskrivelse eller null",
    "vedstaelsesfrist": "kort beskrivelse eller null"
  },
  "evaluation": {
    "award_criteria": [{"name": "...", "weight_pct": 0}],
    "qualification_requirements": ["..."]
  },
  "scope_items": [
    {"package": "grunnarbeid|betong|stal|fasade|tomrer|tak|vvs|elektro|utomhus|annet", "description": "...", "quantity": "... eller null", "unit": "m2|m3|stk|rs|null"}
  ],
  "special_requirements": ["..."],
  "internal_inconsistencies": [
    {"issue": "...", "severity": "HIGH|MEDIUM|LOW", "location": "side X / kapittel Y"}
  ],
  "price_sensitivities": [
    {"topic": "...", "why_sensitive": "...", "recommended_action": "egen prising|RFI|kontrollregning"}
  ]
}"""


def pass1_extract(
    doc: Dict[str, Any],
    qa_level: str = "Standard",
) -> Dict[str, Any]:
    """
    Pass 1: Structured extraction from a single document.
    `doc` is the output from tender_document_parser.extract_document().
    """
    filename = doc.get("filename", "?")
    filename_cat = doc.get("category_filename", "annet")
    text = doc.get("text") or ""

    if not text.strip():
        return {
            "filename": filename,
            "extraction": None,
            "meta": {"status": "skipped", "reason": "no text extracted"},
        }

    # Cap text to ~80k chars for a single doc (≈20k tokens)
    if len(text) > 80_000:
        text = text[:78_000] + "\n\n[... dokument kuttet — for langt for enkeltanalyse ...]"

    # Include table summaries if present
    table_summary = ""
    if doc.get("tables"):
        table_summary = f"\n\nTABELLER I DOKUMENTET: {len(doc['tables'])} stk"
    if doc.get("sheets"):
        sheet_names = [s.get("name", "") for s in doc["sheets"]]
        table_summary = f"\n\nXLSX-ARK: {', '.join(sheet_names[:10])}"

    user_prompt = f"""FILNAVN: {filename}
FORELØPIG KATEGORI (fra filnavn): {filename_cat}{table_summary}

DOKUMENTINNHOLD:
\"\"\"
{text}
\"\"\"

Returner KUN JSON på dette skjemaet:
{PASS1_SCHEMA}"""

    raw, meta = call_ai(
        system=PASS1_SYSTEM,
        user=user_prompt,
        qa_level=qa_level,
        max_tokens=6000,
        temperature=0.1,
    )

    parsed = safe_json_loads(raw) if raw else None
    return {
        "filename": filename,
        "extraction": parsed,
        "meta": meta,
        "raw_length": len(raw),
    }


def run_pass1(
    documents: List[Dict[str, Any]],
    qa_level: str = "Standard",
    progress_callback=None,
    max_workers: int = 4,
) -> List[Dict[str, Any]]:
    """
    Run pass-1 extraction on all documents in parallel.

    Bruker ThreadPoolExecutor med max_workers=4 slik at opptil 4 Claude-kall
    kjører samtidig. Dette gir 3-4x speedup på typiske anbudsbunker med
    20-30 dokumenter.

    progress_callback(i, total, filename) kalles når hvert dokument er ferdig.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = len(documents)
    if total == 0:
        return []

    # For små bunker — kjør sekvensielt (less overhead)
    if total <= 3:
        results = []
        for i, doc in enumerate(documents):
            if progress_callback:
                progress_callback(i, total, doc.get("filename", ""))
            result = pass1_extract(doc, qa_level=qa_level)
            results.append(result)
        if progress_callback:
            progress_callback(total, total, "ferdig")
        return results

    # Parallell-kjør større bunker. Bevar original rekkefølge.
    results: List[Dict[str, Any]] = [None] * total  # type: ignore
    completed = 0

    # Rapporter første fil umiddelbart
    if progress_callback:
        progress_callback(0, total, documents[0].get("filename", ""))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(pass1_extract, doc, qa_level): idx
            for idx, doc in enumerate(documents)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {
                    "filename": documents[idx].get("filename", "?"),
                    "extraction": None,
                    "meta": {"status": "error", "reason": f"{type(e).__name__}: {e}"},
                }
            completed += 1
            if progress_callback:
                progress_callback(
                    completed, total,
                    results[idx].get("filename", "?") if results[idx] else "?",
                )

    return results


# ═════════════════════════════════════════════════════════════════
# PASS 2 — Cross-document consistency check
# ═════════════════════════════════════════════════════════════════
PASS2_SYSTEM = """Du er Builtly Tender AI. Du har fått strukturerte sammendrag av alle
dokumentene i et anbudsgrunnlag og skal finne MOTSTRIDIGHETER og GAP mellom dem.

Typiske funn:
- Ulik tilbudsfrist i konkurransegrunnlag vs. vedlegg
- Krav i beskrivelse som ikke er priset i prisskjema
- Tegninger som avviker fra mengdeliste
- Kontraktsklausuler som motsier reguleringsbestemmelser
- Manglende SHA-dokumentasjon selv om byggherreforskriften gjelder
- Parkeringskrav som kolliderer med kommuneplan (KPA)
- Uklart grensesnitt mellom pakker/fag

Returner KUN gyldig JSON."""

PASS2_SCHEMA = """{
  "cross_document_conflicts": [
    {
      "title": "Kort tittel",
      "severity": "HIGH|MEDIUM|LOW",
      "involved_documents": ["filnavn1", "filnavn2"],
      "description": "Hva er konflikten",
      "economic_impact": "Hvordan dette kan påvirke pris/risiko",
      "recommended_rfi": "Konkret spørsmål til byggherre eller null"
    }
  ],
  "scope_gaps": [
    {
      "package": "...",
      "gap": "Hva mangler i grunnlaget",
      "severity": "HIGH|MEDIUM|LOW",
      "must_be_clarified_before_bid": true
    }
  ],
  "completeness_assessment": {
    "konkurransegrunnlag": "komplett|delvis|mangler",
    "beskrivelse": "komplett|delvis|mangler",
    "tegninger": "komplett|delvis|mangler",
    "prisskjema": "komplett|delvis|mangler",
    "kontrakt": "komplett|delvis|mangler",
    "sha": "komplett|delvis|mangler",
    "miljo": "komplett|delvis|mangler"
  },
  "unified_deadlines": [
    {"type": "...", "date": "...", "source_document": "...", "conflicting": false}
  ],
  "unified_contract_terms": {
    "ns_standard": "...",
    "dagmulkt": "...",
    "sikkerhetsstillelse": "...",
    "notes": "..."
  }
}"""


def _build_pass2_payload(pass1_results: List[Dict[str, Any]]) -> str:
    """Compress pass-1 outputs into a prompt-friendly payload."""
    parts = []
    for r in pass1_results:
        ex = r.get("extraction")
        if not ex:
            continue
        summary = {
            "filename": r.get("filename"),
            "doc_type_confirmed": ex.get("doc_type_confirmed"),
            "doc_summary": ex.get("doc_summary"),
            "deadlines": ex.get("deadlines", []),
            "contract": ex.get("contract", {}),
            "evaluation": ex.get("evaluation", {}),
            "scope_items": ex.get("scope_items", [])[:30],  # cap
            "special_requirements": ex.get("special_requirements", [])[:20],
            "internal_inconsistencies": ex.get("internal_inconsistencies", []),
            "price_sensitivities": ex.get("price_sensitivities", []),
        }
        parts.append(summary)
    return json.dumps(parts, ensure_ascii=False, indent=2)


def run_pass2(
    pass1_results: List[Dict[str, Any]],
    config: Dict[str, Any],
    qa_level: str = "Standard",
) -> Dict[str, Any]:
    """Cross-document consistency pass."""
    valid = [r for r in pass1_results if r.get("extraction")]
    if not valid:
        return {
            "data": None,
            "meta": {"status": "skipped", "reason": "no pass-1 data"},
        }

    payload = _build_pass2_payload(pass1_results)

    user_prompt = f"""PROSJEKT-KONFIGURASJON:
- Anskaffelsesform: {config.get('procurement_mode', '?')}
- Pakker / delentrepriser: {', '.join(config.get('packages', []))}
- Estimert tilbudsverdi: {config.get('bid_value_mnok', '?')} MNOK
- Prioriterte fag: {', '.join(config.get('discipline_focus', []))}
- Kontrolldybde: {config.get('qa_level', 'Standard')}
- Prosjektnotater: {config.get('notes', '')}

STRUKTURERTE DOKUMENTSAMMENDRAG FRA PASS 1:
{payload}

Analyser disse samlet og finn motstrid, gap og uklarheter på tvers av dokumentene.
Returner KUN JSON på dette skjemaet:
{PASS2_SCHEMA}"""

    raw, meta = call_ai(
        system=PASS2_SYSTEM,
        user=user_prompt,
        qa_level=qa_level,
        max_tokens=8000,
        temperature=0.15,
    )

    parsed = safe_json_loads(raw) if raw else None
    return {"data": parsed, "meta": meta, "raw_length": len(raw)}


# ═════════════════════════════════════════════════════════════════
# PASS 3 — Bid strategy (go/no-go + pricing packages)
# ═════════════════════════════════════════════════════════════════
PASS3_SYSTEM = """Du er Builtly Tender AI, rådgiver for tilbyder.
Basert på alt som er funnet i pass 1 og pass 2 skal du gi en strategisk
tilbudsvurdering på et nivå som hjelper CEO/kalkulasjonsleder å ta
go/no-go-beslutning og planlegge prisingsprosessen.

Returner KUN gyldig JSON. Vær konkret, handlingsorientert og sitatbar
for styret/byggherre."""

PASS3_SCHEMA = """{
  "executive_summary": "3-5 setninger — kan brukes direkte i styresak",
  "go_no_go": {
    "recommendation": "GO|GO_WITH_CONDITIONS|NO_GO|INSUFFICIENT_DATA",
    "confidence": "HIGH|MEDIUM|LOW",
    "rationale": "...",
    "conditions": ["Hvilke forhold må avklares for å gi GO"]
  },
  "risk_matrix": [
    {
      "title": "...",
      "severity": "HIGH|MEDIUM|LOW",
      "category": "kontrakt|scope|grensesnitt|pris|frist|hms|miljo|kvalifikasjon|annet",
      "impact": "...",
      "mitigation": "...",
      "rfi_needed": true,
      "paragraph_ref": "doc / side / §"
    }
  ],
  "rfi_queue": [
    {
      "priority": "HIGH|MEDIUM|LOW",
      "question": "Klart formulert spørsmål til byggherre",
      "why_it_matters": "Økonomisk eller kontraktsmessig begrunnelse",
      "owner": "Byggherre|Prosjekterende|Intern",
      "deadline_before": "ISO-dato eller relativ frist"
    }
  ],
  "pricing_packages": [
    {
      "package": "grunnarbeid|betong|stal|fasade|tomrer|tak|vvs|elektro|utomhus|annet",
      "send_to_external": true,
      "rationale": "Hvorfor dette skal/ikke skal til ekstern UE",
      "estimated_value_mnok": 0.0,
      "key_specifications": ["..."],
      "open_questions": ["Spørsmål UE-leverandører må svare på"],
      "suggested_suppliers_hint": "Type leverandør det bør sendes til"
    }
  ],
  "win_probability_factors": {
    "strengths": ["..."],
    "weaknesses": ["..."],
    "competitive_considerations": ["..."]
  },
  "submission_checklist": [
    {"item": "...", "status": "OK|TODO|BLOCKED", "owner": "..."}
  ],
  "tender_readiness_components": {
    "document_completeness": 0,
    "scope_clarity": 0,
    "contract_risk": 0,
    "pricing_readiness": 0,
    "qualification_fit": 0
  }
}

Alle tall i tender_readiness_components skal være 0-100. Disse brukes til
vektet readiness-score i UI, så vær kalibrert:
- 90-100: fullt klar, ingen vesentlige hull
- 70-89: klar med mindre avklaringer
- 50-69: krever flere RFI før innlevering
- 30-49: betydelige hull, vurder pass
- 0-29: uforsvarlig å levere"""


def run_pass3(
    pass1_results: List[Dict[str, Any]],
    pass2_result: Dict[str, Any],
    config: Dict[str, Any],
    qa_level: str = "Standard",
) -> Dict[str, Any]:
    """Strategic bid analysis."""
    valid = [r for r in pass1_results if r.get("extraction")]
    if not valid and not pass2_result.get("data"):
        return {"data": None, "meta": {"status": "skipped", "reason": "no prior pass data"}}

    pass1_compressed = _build_pass2_payload(pass1_results)
    pass2_json = json.dumps(pass2_result.get("data") or {}, ensure_ascii=False, indent=2)

    user_prompt = f"""PROSJEKT-KONFIGURASJON:
- Anskaffelsesform: {config.get('procurement_mode', '?')}
- Pakker / delentrepriser: {', '.join(config.get('packages', []))}
- Estimert tilbudsverdi: {config.get('bid_value_mnok', '?')} MNOK
- Prioriterte fag: {', '.join(config.get('discipline_focus', []))}
- Kontrolldybde: {config.get('qa_level', 'Standard')}
- Prosjektnotater: {config.get('notes', '')}

PASS 1 — Per-dokument ekstraksjon:
{pass1_compressed}

PASS 2 — Krysskontroll:
{pass2_json}

Gi strategisk tilbudsvurdering. Returner KUN JSON på dette skjemaet:
{PASS3_SCHEMA}"""

    raw, meta = call_ai(
        system=PASS3_SYSTEM,
        user=user_prompt,
        qa_level=qa_level,
        max_tokens=10000,
        temperature=0.2,
    )

    parsed = safe_json_loads(raw) if raw else None
    return {"data": parsed, "meta": meta, "raw_length": len(raw)}


# ═════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═════════════════════════════════════════════════════════════════
def run_full_analysis(
    documents: List[Dict[str, Any]],
    config: Dict[str, Any],
    progress_callback=None,
) -> Dict[str, Any]:
    """
    Run all three passes and return a consolidated result.

    Args:
        documents: output from tender_document_parser.extract_documents()
        config: UI config dict (procurement_mode, packages, qa_level, etc.)
        progress_callback: optional fn(stage: str, pct: float, detail: str)

    Returns:
        {
            "pass1": [ ... ],
            "pass2": { ... },
            "pass3": { ... },
            "attempt_log": [ ... ],
            "backend_summary": { ... },
        }
    """
    qa_level = config.get("qa_level", "Standard")
    attempt_log: List[Dict[str, Any]] = []

    if not HAS_ANY_AI:
        return {
            "pass1": [],
            "pass2": {"data": None},
            "pass3": {"data": None},
            "attempt_log": [{"stage": "init", "status": "no_backend"}],
            "backend_summary": {"available": []},
        }

    # Pass 1
    def _p1_cb(i, total, name):
        if progress_callback:
            pct = 0.6 * (i / max(total, 1))
            progress_callback("pass1", pct, f"Leser {name} ({i}/{total})")

    if progress_callback:
        progress_callback("pass1", 0.0, "Starter per-dokument-analyse")
    p1 = run_pass1(documents, qa_level=qa_level, progress_callback=_p1_cb)
    attempt_log.append({
        "stage": "pass1",
        "docs": len(p1),
        "succeeded": sum(1 for r in p1 if r.get("extraction")),
    })

    # Pass 2
    if progress_callback:
        progress_callback("pass2", 0.65, "Krysskontroll mellom dokumenter")
    p2 = run_pass2(p1, config, qa_level=qa_level)
    attempt_log.append({
        "stage": "pass2",
        "status": p2.get("meta", {}).get("status", "?"),
        "has_data": bool(p2.get("data")),
    })

    # Pass 3
    if progress_callback:
        progress_callback("pass3", 0.85, "Tilbudsstrategi og prisingspakker")
    p3 = run_pass3(p1, p2, config, qa_level=qa_level)
    attempt_log.append({
        "stage": "pass3",
        "status": p3.get("meta", {}).get("status", "?"),
        "has_data": bool(p3.get("data")),
    })

    if progress_callback:
        progress_callback("done", 1.0, "Analyse ferdig")

    return {
        "pass1": p1,
        "pass2": p2,
        "pass3": p3,
        "attempt_log": attempt_log,
        "backend_summary": {
            "claude": HAS_CLAUDE,
            "openai": HAS_OPENAI,
            "gemini": HAS_GEMINI,
            "primary": "claude" if HAS_CLAUDE else ("openai" if HAS_OPENAI else "gemini"),
        },
    }
