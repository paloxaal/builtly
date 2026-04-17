# -*- coding: utf-8 -*-
"""
Builtly | Tender Radar — AI Screener
─────────────────────────────────────────────────────────────────
Kjører rask AI-screening av nye kunngjøringer mot brukeres
watches. Bruker Claude Haiku (billig + rask) siden volumet er
høyt og vi ikke trenger dyp analyse — bare fit-score.

Hver match i tender_sources evalueres mot hver watch som har
overlappende CPV-koder/regioner. Resultatet lagres som en rad
i tender_alerts.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    anthropic = None
    HAS_ANTHROPIC = False

try:
    from supabase import create_client, Client as SupabaseClient
except ImportError:
    create_client = None
    SupabaseClient = None  # type: ignore


ANTHROPIC_HAIKU_MODEL = os.environ.get("ANTHROPIC_HAIKU_MODEL", "claude-haiku-4-5-20251001")


_SB: Optional["SupabaseClient"] = None
_CLAUDE = None


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


def _claude():
    global _CLAUDE
    if _CLAUDE is not None:
        return _CLAUDE
    if not HAS_ANTHROPIC:
        return None
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        _CLAUDE = anthropic.Anthropic(api_key=key)
        return _CLAUDE
    except Exception:
        return None


# ─── Filtrering før AI (spar kost) ───────────────────────────────
def quick_filter_match(source_row: Dict[str, Any], watch: Dict[str, Any]) -> bool:
    """
    Rask deterministisk forfilter — hvis denne sier nei, bruker vi
    ikke AI. Sparer kost ved åpenbart irrelevante kombinasjoner.
    """
    # CPV-filter
    watch_cpv = watch.get("cpv_codes") or []
    watch_cpv_excl = watch.get("cpv_codes_exclude") or []
    source_cpv = source_row.get("cpv_codes") or []

    if watch_cpv:
        if not any(any(s.startswith(w) for s in source_cpv) for w in watch_cpv):
            return False

    if watch_cpv_excl and source_cpv:
        if any(any(s.startswith(w) for s in source_cpv) for w in watch_cpv_excl):
            return False

    # Verdi-filter
    est_value = source_row.get("estimated_value_nok")
    if est_value is not None:
        min_v = watch.get("min_value_nok")
        max_v = watch.get("max_value_nok")
        if min_v is not None and est_value < float(min_v):
            return False
        if max_v is not None and est_value > float(max_v):
            return False

    # Region-filter (hvis location-streng matcher)
    watch_regions = watch.get("regions") or []
    if watch_regions:
        location = (source_row.get("location") or "").lower()
        if location and not any(r.lower() in location for r in watch_regions):
            return False

    # Negative nøkkelord
    neg_kw = watch.get("keywords_negative") or []
    if neg_kw:
        haystack = " ".join([
            source_row.get("title") or "",
            source_row.get("description") or "",
        ]).lower()
        if any(kw.lower() in haystack for kw in neg_kw):
            return False

    return True


# ─── AI-screening ────────────────────────────────────────────────
SCREENING_SYSTEM = """Du er en senior anbudsanalytiker som hjelper norske
entreprenører og rådgivere med å screen-e nye offentlige kunngjøringer.

Du får:
1. En kunngjøring (tittel, oppdragsgiver, beskrivelse, CPV, verdi, frist)
2. En selskapsprofil (fag, kapasitet, interesseområder)

Din oppgave: Vurder hvor godt anbudet matcher selskapets profil, og
produser en kort strukturert output.

Vær realistisk. Fit-score under 40 betyr "ikke bruk tid på dette".
Over 70 betyr "dette er verdt å se nærmere på". Vær direkte og kortfattet
— tilbudsansvarlig skal kunne ta en go/no-go-beslutning på 10 sekunder."""

SCREENING_SCHEMA = """{
  "fit_score": tall mellom 0 og 100 (0 = ikke relevant, 100 = perfekt match),
  "fit_reasoning": "1-2 setninger som forklarer fit-score. Konkret, ikke generisk.",
  "quick_summary": "2-3 setninger: Hva er anbudet, hvem er oppdragsgiver, hvor stort, når er frist.",
  "quick_risk_flags": [
    {"severity": "HIGH|MEDIUM|LOW", "issue": "kort beskrivelse"}
  ],
  "estimated_effort_days": tall (estimert arbeidsdager for å lage et komplett tilbud),
  "go_no_go_hint": "GO|CONSIDER|SKIP",
  "why_interesting": "hvis fit_score >= 60, én setning som fremhever hvorfor"
}"""


def screen_one(
    source_row: Dict[str, Any],
    watch: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Kjør AI-screening på én (kunngjøring × watch)-kombinasjon."""
    client = _claude()
    if not client:
        return None

    # Bygg profilbeskrivelse fra watch
    profile_parts = []
    if watch.get("name"):
        profile_parts.append(f"Profilnavn: {watch['name']}")
    if watch.get("discipline_focus"):
        profile_parts.append(f"Fagfokus: {', '.join(watch['discipline_focus'])}")
    if watch.get("cpv_codes"):
        profile_parts.append(f"CPV-interesse: {', '.join(watch['cpv_codes'])}")
    if watch.get("regions"):
        profile_parts.append(f"Geografisk fokus: {', '.join(watch['regions'])}")
    if watch.get("min_value_nok") or watch.get("max_value_nok"):
        lo = watch.get("min_value_nok") or 0
        hi = watch.get("max_value_nok") or "ingen øvre"
        profile_parts.append(f"Kontraktstørrelse: {lo:,.0f}-{hi} NOK".replace(",", " "))
    if watch.get("typical_contract_type"):
        profile_parts.append(f"Foretrukken kontraktstype: {watch['typical_contract_type']}")
    if watch.get("keywords_positive"):
        profile_parts.append(f"Positive søkeord: {', '.join(watch['keywords_positive'])}")
    if watch.get("company_capabilities"):
        profile_parts.append(f"Kapasitetsbeskrivelse: {watch['company_capabilities']}")

    profile_text = "\n".join(profile_parts) if profile_parts else "(ingen profildata)"

    # Bygg kunngjøringsbeskrivelse
    notice_parts = [
        f"Tittel: {source_row.get('title') or '(ukjent)'}",
        f"Oppdragsgiver: {source_row.get('buyer_name') or '(ukjent)'}",
    ]
    if source_row.get("cpv_codes"):
        notice_parts.append(f"CPV: {', '.join(source_row['cpv_codes'])}")
    if source_row.get("estimated_value_nok"):
        notice_parts.append(
            f"Estimert verdi: {source_row['estimated_value_nok']:,.0f} {source_row.get('currency', 'NOK')}".replace(",", " ")
        )
    if source_row.get("submission_deadline"):
        notice_parts.append(f"Frist: {source_row['submission_deadline']}")
    if source_row.get("location"):
        notice_parts.append(f"Sted: {source_row['location']}")
    if source_row.get("procedure_type"):
        notice_parts.append(f"Prosedyre: {source_row['procedure_type']}")
    if source_row.get("description"):
        notice_parts.append(f"\nBeskrivelse:\n{source_row['description'][:1500]}")

    notice_text = "\n".join(notice_parts)

    user_msg = (
        f"SELSKAPSPROFIL:\n{profile_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"KUNNGJØRING:\n{notice_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Produser JSON med denne strukturen:\n{SCREENING_SCHEMA}\n\n"
        f"Svar KUN med JSON-objektet."
    )

    try:
        resp = client.messages.create(
            model=ANTHROPIC_HAIKU_MODEL,
            max_tokens=800,
            temperature=0.1,
            system=SCREENING_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text if resp.content else ""
        usage = resp.usage
        tokens_in = getattr(usage, "input_tokens", 0)
        tokens_out = getattr(usage, "output_tokens", 0)

        # Parse JSON
        parsed = _parse_json(text)
        if not parsed:
            return None

        return {
            "fit_score": int(parsed.get("fit_score") or 0),
            "fit_reasoning": parsed.get("fit_reasoning"),
            "quick_summary": parsed.get("quick_summary"),
            "quick_risk_flags": parsed.get("quick_risk_flags") or [],
            "estimated_effort_days": _safe_float(parsed.get("estimated_effort_days")),
            "go_no_go_hint": parsed.get("go_no_go_hint"),
            "why_interesting": parsed.get("why_interesting"),
            "ai_backend": "claude",
            "ai_model": ANTHROPIC_HAIKU_MODEL,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
    except Exception:
        return None


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    import re
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"```$", "", t).strip()
    first = t.find("{")
    last = t.rfind("}")
    if first == -1 or last == -1:
        return None
    try:
        return json.loads(t[first:last + 1])
    except Exception:
        return None


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


# ─── Alert-persistering ──────────────────────────────────────────
def persist_alert(
    watch_id: str,
    source_row_id: str,
    screening_result: Dict[str, Any],
) -> Optional[str]:
    """Lagre screening-resultatet som en tender_alerts-rad."""
    sb = _sb()
    if not sb:
        return None
    try:
        # Sjekk om alert allerede eksisterer
        existing = (
            sb.table("tender_alerts")
            .select("alert_id")
            .eq("watch_id", watch_id)
            .eq("source_row_id", source_row_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            # Oppdater eksisterende
            alert_id = existing.data[0]["alert_id"]
            sb.table("tender_alerts").update({
                "fit_score": screening_result.get("fit_score"),
                "fit_reasoning": screening_result.get("fit_reasoning"),
                "quick_summary": screening_result.get("quick_summary"),
                "quick_risk_flags": screening_result.get("quick_risk_flags"),
                "estimated_effort_days": screening_result.get("estimated_effort_days"),
                "ai_backend": screening_result.get("ai_backend"),
                "ai_model": screening_result.get("ai_model"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("alert_id", alert_id).execute()
            return alert_id

        # Opprett ny
        resp = sb.table("tender_alerts").insert({
            "watch_id": watch_id,
            "source_row_id": source_row_id,
            "fit_score": screening_result.get("fit_score"),
            "fit_reasoning": screening_result.get("fit_reasoning"),
            "quick_summary": screening_result.get("quick_summary"),
            "quick_risk_flags": screening_result.get("quick_risk_flags"),
            "estimated_effort_days": screening_result.get("estimated_effort_days"),
            "ai_backend": screening_result.get("ai_backend"),
            "ai_model": screening_result.get("ai_model"),
            "status": "new",
        }).execute()
        return resp.data[0]["alert_id"] if resp.data else None
    except Exception:
        return None


# ─── Batch-screening ─────────────────────────────────────────────
def screen_new_sources_against_all_watches(
    source_row_ids: Optional[List[str]] = None,
    max_screenings: int = 1000,
) -> Dict[str, Any]:
    """
    Hovedorchestratoren: for hver ny kunngjøring, evaluer mot hver
    aktive watch. Kalles av workeren rett etter poll-runden.

    Hvis source_row_ids er None, screenes alle kunngjøringer som ikke
    har alerts ennå for aktive watches.
    """
    from tender_radar_profile import get_all_active_watches

    sb = _sb()
    if not sb:
        return {"error": "Supabase ikke tilgjengelig"}

    watches = get_all_active_watches()
    if not watches:
        return {"watches_evaluated": 0, "alerts_created": 0}

    # Hent kildedata
    try:
        q = sb.table("tender_sources").select("*")
        if source_row_ids:
            q = q.in_("source_row_id", source_row_ids)
        else:
            # Standard: kunngjøringer fra siste 7 dager
            q = q.gte("first_seen_at", (datetime.now(timezone.utc).date()).isoformat())
        sources_resp = q.limit(max_screenings).execute()
        sources = sources_resp.data or []
    except Exception:
        return {"error": "Kunne ikke hente kunngjøringer"}

    alerts_created = 0
    ai_calls = 0
    tokens_in = 0
    tokens_out = 0

    for src in sources:
        for watch in watches:
            if not quick_filter_match(src, watch):
                continue

            result = screen_one(src, watch)
            ai_calls += 1
            if result:
                tokens_in += result.get("tokens_in", 0)
                tokens_out += result.get("tokens_out", 0)

                alert_id = persist_alert(watch["watch_id"], src["source_row_id"], result)
                if alert_id:
                    alerts_created += 1

    return {
        "sources_evaluated": len(sources),
        "watches_evaluated": len(watches),
        "ai_calls": ai_calls,
        "alerts_created": alerts_created,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "estimated_cost_usd": _estimate_cost(tokens_in, tokens_out),
    }


def _estimate_cost(tokens_in: int, tokens_out: int) -> float:
    """Haiku-priser: $0.80/M input, $4/M output."""
    return round(
        (tokens_in / 1_000_000) * 0.80 + (tokens_out / 1_000_000) * 4.0,
        4,
    )


# ─── Cron entry-point ────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    result = screen_new_sources_against_all_watches()
    print(json.dumps(result, indent=2, default=str))
