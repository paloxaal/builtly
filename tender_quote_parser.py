# -*- coding: utf-8 -*-
"""
Builtly | Tender Quote Parser
─────────────────────────────────────────────────────────────────
Leser inn pristilbud fra underentreprenører (UE-respons) i PDF,
DOCX og XLSX-format, henter ut priser, forbehold, gyldighet og
alternativer med AI, og konsoliderer per RFQ-pakke.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tender_document_parser import extract_document
from tender_ai_engine import call_ai, safe_json_loads, _claude_model_for_level


# ─── Supabase ────────────────────────────────────────────────────
try:
    from supabase import create_client, Client as SupabaseClient
except ImportError:
    create_client = None
    SupabaseClient = None  # type: ignore

_SB: Optional["SupabaseClient"] = None


def _sb():
    global _SB
    if _SB is not None:
        return _SB
    if not create_client:
        return None
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not (url and key):
        return None
    try:
        _SB = create_client(url, key)
        return _SB
    except Exception:
        return None


# ─── AI-ekstraksjon ──────────────────────────────────────────────
QUOTE_EXTRACT_SYSTEM = """Du er en norsk anbudsanalytiker. Du mottar teksten fra ETT pristilbud
fra en underentreprenør (UE) og skal trekke ut strukturerte data i JSON.

Vær presis. Ikke gjett på beløp som ikke står i teksten. Bruk null for felt du
ikke finner. Alle beløp oppgis i NOK eksklusive mva med mindre annet er eksplisitt
angitt i teksten."""

QUOTE_EXTRACT_SCHEMA = """{
  "supplier_name": "navn på leverandør/UE",
  "supplier_org_no": "organisasjonsnummer hvis oppgitt, ellers null",
  "contact_person": "kontaktperson hvis oppgitt",
  "contact_email": "epost hvis oppgitt",
  "quote_date": "YYYY-MM-DD hvis oppgitt",
  "validity_until": "YYYY-MM-DD hvis oppgitt, ellers null",
  "currency": "NOK|EUR|SEK/osv",
  "total_price_ex_vat": tallverdi i NOK eksklusive mva (null hvis uklart),
  "total_price_inc_vat": tallverdi inkl. mva hvis oppgitt separat, ellers null,
  "price_basis": "fastpris|regningsarbeid|enhetspriser|kombinasjon|uklart",
  "scope_description": "kort beskrivelse av hva tilbudet dekker, 1-2 setninger",
  "line_items": [
    {"description": "...", "quantity": tallverdi eller null, "unit": "...", "unit_price": null eller tall, "line_total": null eller tall}
  ],
  "options": [
    {"description": "...", "price": null eller tall, "comment": "..."}
  ],
  "exclusions": ["...", "..."],
  "reservations": ["konkrete forbehold, ett per element"],
  "assumptions": ["forutsetninger tilbudet hviler på"],
  "payment_terms": "betalingsbetingelser som tekst, ellers null",
  "delivery_time": "leveringstid/fremdrift som tekst, ellers null",
  "warranty": "garantitid/-betingelser som tekst, ellers null",
  "risk_flags": [
    {"severity": "HIGH|MEDIUM|LOW", "issue": "kort beskrivelse", "impact": "hvorfor det betyr noe"}
  ]
}"""


def parse_quote_with_ai(
    document: Dict[str, Any],
    qa_level: str = "standard",
) -> Dict[str, Any]:
    """
    Kjør AI-ekstraksjon på ETT UE-pristilbud.
    """
    text = document.get("text") or ""
    if not text.strip():
        return {
            "ok": False,
            "error": "Tomt tekstinnhold — kunne ikke parse pristilbudet.",
        }

    # Cap input — tilbud er sjelden over 30 sider
    text_capped = text[:80_000]

    user_msg = (
        f"FILNAVN: {document.get('filename', '(ukjent)')}\n"
        f"DOKUMENTTYPE: {document.get('extension', '')}\n"
        f"ANTALL SIDER: {document.get('page_count', 0)}\n\n"
        f"INNHOLD:\n{text_capped}\n\n"
        f"Ekstraher tilbudsdataene som JSON med nøyaktig denne strukturen:\n"
        f"{QUOTE_EXTRACT_SCHEMA}\n\n"
        f"Svar KUN med JSON-objektet, ingen forklaring utenfor."
    )

    ai_response, meta = call_ai(
        system=QUOTE_EXTRACT_SYSTEM,
        user=user_msg,
        qa_level=qa_level,
        max_tokens=4096,
        temperature=0.0,
    )

    if not ai_response:
        return {
            "ok": False,
            "error": f"AI-backend feilet: {meta.get('error', 'ukjent')}",
            "backend": meta.get("backend"),
        }

    parsed = safe_json_loads(ai_response)
    if not parsed:
        return {
            "ok": False,
            "error": "Kunne ikke parse AI-output som JSON",
            "raw": ai_response[:500],
            "backend": meta.get("backend"),
        }

    return {
        "ok": True,
        "data": parsed,
        "backend": meta.get("backend"),
        "model": meta.get("model"),
    }


# ─── Konsolidering per RFQ-pakke ─────────────────────────────────
def consolidate_quotes_by_package(
    quotes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Aggregater pristilbud per RFQ-pakke. Hver quote må ha
    'package_name' + 'parsed' (output fra parse_quote_with_ai).
    """
    by_package: Dict[str, List[Dict[str, Any]]] = {}
    for q in quotes:
        pkg = q.get("package_name") or "Uspesifisert pakke"
        by_package.setdefault(pkg, []).append(q)

    summary: List[Dict[str, Any]] = []
    for pkg, pkg_quotes in by_package.items():
        prices = []
        for q in pkg_quotes:
            data = (q.get("parsed") or {}).get("data") or {}
            if isinstance(data.get("total_price_ex_vat"), (int, float)):
                prices.append({
                    "supplier": data.get("supplier_name") or "(ukjent)",
                    "price": float(data["total_price_ex_vat"]),
                    "validity": data.get("validity_until"),
                    "reservations_count": len(data.get("reservations") or []),
                    "risk_count": len(data.get("risk_flags") or []),
                })

        if prices:
            prices_sorted = sorted(prices, key=lambda p: p["price"])
            lowest = prices_sorted[0]
            highest = prices_sorted[-1]
            mean_price = sum(p["price"] for p in prices) / len(prices)
            spread_pct = (
                100.0 * (highest["price"] - lowest["price"]) / lowest["price"]
                if lowest["price"] > 0 else 0.0
            )
        else:
            lowest = highest = None
            mean_price = 0.0
            spread_pct = 0.0

        summary.append({
            "package_name": pkg,
            "num_quotes": len(pkg_quotes),
            "num_priced": len(prices),
            "lowest": lowest,
            "highest": highest,
            "mean_price": round(mean_price, 0) if mean_price else None,
            "spread_pct": round(spread_pct, 1),
            "quotes": pkg_quotes,
        })

    return {
        "packages": summary,
        "total_quotes": len(quotes),
        "consolidated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── Persistering (Supabase + lokal JSONL) ───────────────────────
def persist_quote(
    project_name: str,
    run_id: Optional[str],
    package_name: Optional[str],
    filename: str,
    parsed: Dict[str, Any],
) -> Dict[str, Any]:
    """Lagre parset UE-tilbud."""
    quote_id = str(uuid.uuid4())
    record = {
        "quote_id": quote_id,
        "project_name": project_name,
        "run_id": run_id,
        "package_name": package_name,
        "filename": filename,
        "parsed_data": parsed.get("data") if parsed.get("ok") else None,
        "parse_error": parsed.get("error") if not parsed.get("ok") else None,
        "backend": parsed.get("backend"),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }

    stored_in = None
    sb = _sb()
    if sb:
        try:
            sb.table("tender_quotes").insert(record).execute()
            stored_in = "supabase"
        except Exception:
            pass

    if stored_in != "supabase":
        local_path = Path("qa_database/tender_quotes")
        local_path.mkdir(parents=True, exist_ok=True)
        jsonl = local_path / "quotes.jsonl"
        try:
            with jsonl.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            stored_in = "local"
        except Exception:
            stored_in = "ikke lagret"

    record["stored_in"] = stored_in
    return record


def load_quotes(project_name: str, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Hent lagrede pristilbud for prosjektet."""
    records: List[Dict[str, Any]] = []

    sb = _sb()
    if sb:
        try:
            q = sb.table("tender_quotes").select("*").eq("project_name", project_name)
            if run_id:
                q = q.eq("run_id", run_id)
            resp = q.order("received_at", desc=True).execute()
            if resp.data:
                records.extend(resp.data)
        except Exception:
            pass

    # Merge local
    local_jsonl = Path("qa_database/tender_quotes/quotes.jsonl")
    if local_jsonl.exists():
        try:
            with local_jsonl.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if rec.get("project_name") != project_name:
                            continue
                        if run_id and rec.get("run_id") != run_id:
                            continue
                        if not any(r.get("quote_id") == rec.get("quote_id") for r in records):
                            records.append(rec)
                    except Exception:
                        continue
        except Exception:
            pass

    records.sort(key=lambda r: r.get("received_at") or "", reverse=True)
    return records


# ─── Entry point for UI ──────────────────────────────────────────
def parse_and_persist_quote(
    project_name: str,
    run_id: Optional[str],
    package_name: Optional[str],
    filename: str,
    file_bytes: bytes,
    qa_level: str = "standard",
) -> Dict[str, Any]:
    """
    Full pipeline: ekstraher innhold → AI-parse → persister.
    Returnerer hele recorden (inkl. stored_in).
    """
    # 1. Ekstraher tekst fra fila
    doc = extract_document(filename, file_bytes)
    if doc.get("error") and not doc.get("text"):
        return persist_quote(
            project_name, run_id, package_name, filename,
            {"ok": False, "error": f"Ekstraksjon feilet: {doc['error']}"},
        )

    # 2. AI-parse
    parsed = parse_quote_with_ai(doc, qa_level=qa_level)

    # 3. Persister
    record = persist_quote(project_name, run_id, package_name, filename, parsed)
    record["parsed"] = parsed
    return record
