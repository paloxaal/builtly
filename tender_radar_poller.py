# -*- coding: utf-8 -*-
"""
Builtly | Tender Radar — Poller
─────────────────────────────────────────────────────────────────
Henter nye kunngjøringer fra eksterne portaler og lagrer i
tender_sources. Kalles av Render Cron Job (typisk hvert 30. min).

Kilder:
  - doffin     — betaapi.doffin.no/public/v2 (gratis, ingen auth)
  - mercell    — krever API-nøkkel (fyll inn når avtale er signert)
  - eu_supply  — krever API-avtale per oppdragsgiver (parkert)

Bruk:
  from tender_radar_poller import run_full_poll
  result = run_full_poll(sources=["doffin"], max_notices_per_source=200)
"""
from __future__ import annotations

import hashlib
import json
import os
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    requests = None  # type: ignore
    HAS_REQUESTS = False

try:
    from supabase import create_client, Client as SupabaseClient
except ImportError:
    create_client = None
    SupabaseClient = None  # type: ignore


USER_AGENT = "BuiltlyTenderRadar/1.0 (+https://builtly.ai)"
REQUEST_TIMEOUT = 45


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


# ─── Kilde: Doffin ───────────────────────────────────────────────
DOFFIN_SEARCH_URL = "https://betaapi.doffin.no/public/v2/notices"
DOFFIN_DETAIL_URL = "https://betaapi.doffin.no/public/v2/notices/{id}"


def fetch_doffin_notices(
    since: Optional[datetime] = None,
    cpv_prefixes: Optional[List[str]] = None,
    max_results: int = 200,
) -> List[Dict[str, Any]]:
    """
    Hent nye kunngjøringer fra Doffin.

    Bruker Doffins offentlige søke-API. API-parametere kan justeres
    avhengig av hva frontend faktisk kaller — Doffin har endret seg
    flere ganger. Hvis denne kallestrukturen ikke gir treff, inspiser
    Network-fanen på doffin.no for å finne dagens korrekte parametere.
    """
    if not HAS_REQUESTS:
        return []

    params: Dict[str, Any] = {
        "take": min(max_results, 200),
        "skip": 0,
    }
    if since:
        params["publishedFrom"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    all_notices: List[Dict[str, Any]] = []
    while len(all_notices) < max_results:
        try:
            resp = requests.get(
                DOFFIN_SEARCH_URL,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
        except Exception:
            break

        # Respons-struktur kan variere: enten {items: [...], total: N} eller ren liste
        batch: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            batch = data.get("items") or data.get("notices") or data.get("data") or []
        elif isinstance(data, list):
            batch = data

        if not batch:
            break

        # CPV-filter (client-side hvis API ikke støtter det)
        if cpv_prefixes:
            filtered = []
            for n in batch:
                codes = _extract_cpv_codes(n)
                if any(c.startswith(p) for c in codes for p in cpv_prefixes):
                    filtered.append(n)
            batch = filtered

        all_notices.extend(batch)
        if len(batch) < params["take"]:
            break  # siste side
        params["skip"] += params["take"]

    return all_notices[:max_results]


def _extract_cpv_codes(notice: Dict[str, Any]) -> List[str]:
    cpv = (
        notice.get("cpv")
        or notice.get("cpvCodes")
        or notice.get("mainCpv")
        or []
    )
    if isinstance(cpv, list):
        return [str(c) for c in cpv if c]
    if isinstance(cpv, str):
        return [cpv]
    return []


# ─── Kilde: Mercell (API-avtale kreves) ──────────────────────────
MERCELL_API_BASE = os.environ.get("MERCELL_API_BASE", "https://api.mercell.com/v1")


def fetch_mercell_notices(
    since: Optional[datetime] = None,
    max_results: int = 200,
) -> List[Dict[str, Any]]:
    """
    Hent nye kunngjøringer fra Mercell.
    KREVER: MERCELL_API_KEY i environment (avtale med Mercell).

    Denne implementasjonen er skjelett — endepunktene må justeres
    når API-dokumentasjon fra Mercell er tilgjengelig.
    """
    api_key = os.environ.get("MERCELL_API_KEY")
    if not api_key or not HAS_REQUESTS:
        return []

    # Skjelett — reelle endepunkter fyller Mercell inn i API-avtalen
    try:
        resp = requests.get(
            f"{MERCELL_API_BASE}/tenders",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            params={
                "take": min(max_results, 200),
                "publishedFrom": since.isoformat() if since else None,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("items") or data.get("tenders") or []
    except Exception:
        return []


# ─── Normalisering ───────────────────────────────────────────────
def normalize_doffin_notice(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Konverter Doffin-rådata til tender_sources-skjema."""
    # Gjenbruker samme ekstraksjonslogikk som tender_portal_fetch.py
    # (import for å unngå kodeduplikasjon når deployet sammen)
    try:
        from tender_portal_fetch import _extract_structured_metadata
        meta = _extract_structured_metadata(raw)
    except ImportError:
        meta = {}

    source_notice_id = str(raw.get("noticeId") or raw.get("id") or raw.get("reference") or "")

    return {
        "source": "doffin",
        "source_notice_id": source_notice_id,
        "title": meta.get("title"),
        "buyer_name": meta.get("buyer"),
        "buyer_org_no": meta.get("buyer_org_no"),
        "publication_date": meta.get("publication_date"),
        "submission_deadline": meta.get("deadline"),
        "estimated_value_nok": _parse_number(meta.get("estimated_value")) if meta.get("currency") == "NOK" else None,
        "currency": meta.get("currency"),
        "cpv_codes": meta.get("cpv_codes") or [],
        "procedure_type": meta.get("procedure_type"),
        "notice_type": meta.get("notice_type"),
        "location": meta.get("location"),
        "description": meta.get("description"),
        "kgv_url": meta.get("kgv_url"),
        "kgv_provider": meta.get("kgv_provider"),
        "source_url": f"https://www.doffin.no/notices/{source_notice_id}" if source_notice_id else None,
        "raw_payload": raw,
        "dedup_hash": _make_dedup_hash(meta),
    }


def normalize_mercell_notice(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Placeholder — mapp Mercell-felter når API-dokumentasjon er bekreftet."""
    source_notice_id = str(raw.get("id") or raw.get("tenderId") or "")
    return {
        "source": "mercell",
        "source_notice_id": source_notice_id,
        "title": raw.get("title"),
        "buyer_name": (raw.get("buyer") or {}).get("name") if isinstance(raw.get("buyer"), dict) else raw.get("buyer"),
        "submission_deadline": raw.get("deadline") or raw.get("submissionDeadline"),
        "estimated_value_nok": _parse_number(raw.get("estimatedValue")),
        "cpv_codes": raw.get("cpvCodes") or [],
        "description": raw.get("description"),
        "kgv_url": raw.get("tenderUrl") or raw.get("url"),
        "kgv_provider": "Mercell",
        "source_url": raw.get("tenderUrl") or raw.get("url"),
        "raw_payload": raw,
        "dedup_hash": _make_dedup_hash({
            "title": raw.get("title"),
            "buyer": (raw.get("buyer") or {}).get("name") if isinstance(raw.get("buyer"), dict) else raw.get("buyer"),
            "deadline": raw.get("deadline"),
        }),
    }


def _parse_number(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except (ValueError, TypeError):
        return None


def _make_dedup_hash(meta: Dict[str, Any]) -> str:
    key = "|".join(str(meta.get(k) or "") for k in ("title", "buyer", "deadline", "estimated_value"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


# ─── Upsert til Supabase ─────────────────────────────────────────
def upsert_source_row(normalized: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Returnerer (is_new, source_row_id).
    is_new = True hvis dette var en helt ny kunngjøring.
    """
    sb = _sb()
    if not sb:
        return False, ""

    source = normalized["source"]
    source_notice_id = normalized["source_notice_id"]

    try:
        existing = (
            sb.table("tender_sources")
            .select("source_row_id")
            .eq("source", source)
            .eq("source_notice_id", source_notice_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            row_id = existing.data[0]["source_row_id"]
            sb.table("tender_sources").update({
                **normalized,
                "last_updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("source_row_id", row_id).execute()
            return False, row_id

        resp = sb.table("tender_sources").insert(normalized).execute()
        return True, resp.data[0]["source_row_id"] if resp.data else ""
    except Exception:
        return False, ""


# ─── Orchestration ───────────────────────────────────────────────
def run_full_poll(
    sources: Optional[List[str]] = None,
    max_notices_per_source: int = 200,
) -> Dict[str, Any]:
    """
    Topnivå-entrypoint. Kaller alle aktive kilder, lagrer nye
    kunngjøringer, og returnerer en oppsummering per kilde.

    Skal også trigge screening-steget (kalles separat av workeren).
    """
    sources = sources or ["doffin"]
    summary: Dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "per_source": {},
    }

    for src in sources:
        run_id = _log_run_start(src)
        try:
            if src == "doffin":
                raw_notices = fetch_doffin_notices(max_results=max_notices_per_source)
                normalize_fn = normalize_doffin_notice
            elif src == "mercell":
                raw_notices = fetch_mercell_notices(max_results=max_notices_per_source)
                normalize_fn = normalize_mercell_notice
            else:
                raw_notices = []
                normalize_fn = None  # type: ignore

            new_count = 0
            updated_count = 0
            new_source_row_ids: List[str] = []

            for raw in raw_notices:
                if not normalize_fn:
                    continue
                try:
                    normalized = normalize_fn(raw)
                    if not normalized.get("source_notice_id"):
                        continue
                    is_new, row_id = upsert_source_row(normalized)
                    if is_new:
                        new_count += 1
                        if row_id:
                            new_source_row_ids.append(row_id)
                    else:
                        updated_count += 1
                except Exception:
                    continue

            summary["per_source"][src] = {
                "fetched": len(raw_notices),
                "new": new_count,
                "updated": updated_count,
                "new_source_row_ids": new_source_row_ids,
            }

            _log_run_complete(run_id, "ok", fetched=len(raw_notices), new=new_count, updated=updated_count)

        except Exception as e:
            summary["per_source"][src] = {"error": str(e)}
            _log_run_complete(run_id, "failed", error=str(e), tb=traceback.format_exc())

    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def _log_run_start(source: str) -> Optional[str]:
    sb = _sb()
    if not sb:
        return None
    try:
        resp = sb.table("tender_radar_runs").insert({
            "source": source,
            "status": "running",
        }).execute()
        return resp.data[0]["run_id"] if resp.data else None
    except Exception:
        return None


def _log_run_complete(
    run_id: Optional[str],
    status: str,
    fetched: int = 0,
    new: int = 0,
    updated: int = 0,
    error: Optional[str] = None,
    tb: Optional[str] = None,
) -> None:
    sb = _sb()
    if not sb or not run_id:
        return
    try:
        sb.table("tender_radar_runs").update({
            "status": status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "notices_fetched": fetched,
            "notices_new": new,
            "notices_updated": updated,
            "error_message": error,
            "error_traceback": tb,
        }).eq("run_id", run_id).execute()
    except Exception:
        pass


# ─── Cron entry-point ────────────────────────────────────────────
if __name__ == "__main__":
    """
    Kan kjøres som Render Cron Job:
        python tender_radar_poller.py

    Eller via schedule i tender_radar_worker.py (som kombinerer poll + screen + notify).
    """
    result = run_full_poll(sources=["doffin"])
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
