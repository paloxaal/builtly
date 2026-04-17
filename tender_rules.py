# -*- coding: utf-8 -*-
"""
Builtly | Tender Rules, Readiness & Audit
─────────────────────────────────────────────────────────────────
Faktabasert readiness-score, deterministisk regelmotor og
persistent audit trail (Supabase om tilgjengelig, lokal fallback).
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ─── Supabase (optional) ─────────────────────────────────────────
try:
    from supabase import create_client, Client as SupabaseClient
except ImportError:
    create_client = None
    SupabaseClient = None  # type: ignore

_SUPABASE: Optional["SupabaseClient"] = None


def _get_supabase():
    global _SUPABASE
    if _SUPABASE is None and create_client is not None:
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = (
            os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
            or os.environ.get("SUPABASE_ANON_KEY", "").strip()
        )
        if url and key:
            try:
                _SUPABASE = create_client(url, key)
            except Exception:
                _SUPABASE = None
    return _SUPABASE


# ═════════════════════════════════════════════════════════════════
# RULES ENGINE — deterministic checks that don't need AI
# ═════════════════════════════════════════════════════════════════
EXPECTED_CATEGORIES_BASE = [
    "konkurransegrunnlag",
    "beskrivelse",
    "tegning",
    "prisskjema",
]


def expected_categories(procurement_mode: str, packages: List[str]) -> List[str]:
    base = list(EXPECTED_CATEGORIES_BASE)
    if procurement_mode in ("Totalentreprise", "Design & Build"):
        base.append("kontrakt")
    if procurement_mode == "Utførelsesentreprise":
        base.extend(["kontrakt", "prisskjema"])
    if len(packages) >= 3:
        base.append("tildelingskriterier")
    base.extend(["sha", "miljo"])
    # de-dupe preserving order
    seen = set()
    return [c for c in base if not (c in seen or seen.add(c))]


def build_rule_findings(
    documents: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Deterministic findings based on parsed document manifest + metadata.
    Runs independently of AI; used to complement AI output.
    """
    procurement_mode = config.get("procurement_mode", "Totalentreprise")
    packages = config.get("packages", [])
    discipline_focus = config.get("discipline_focus", [])
    bid_value = float(config.get("bid_value_mnok", 0) or 0)

    found_cats = {d.get("category") for d in documents}
    expected = expected_categories(procurement_mode, packages)
    missing = [c for c in expected if c not in found_cats]

    # ── Checklist ────────────────────────────────────────────────
    checklist: List[Dict[str, Any]] = []
    for cat in expected:
        present = cat in found_cats
        critical = cat in ("konkurransegrunnlag", "beskrivelse", "prisskjema")
        checklist.append({
            "topic": f"Dokumentkategori: {cat}",
            "status": "OK" if present else "MANGLER",
            "severity": (
                "LOW" if present else
                "HIGH" if critical else
                "MEDIUM"
            ),
            "reason": (
                "Dokument identifisert basert på innhold" if present
                else "Kategori forventet for denne anskaffelsesformen"
            ),
            "source": "Regelmotor",
        })

    # ── Package coverage by content ──────────────────────────────
    for pkg in packages:
        pkg_low = pkg.lower()
        match = any(
            pkg_low in (d.get("text_excerpt", "") or "").lower()
            or pkg_low in (d.get("filename", "") or "").lower()
            for d in documents
        )
        checklist.append({
            "topic": f"Pakkedekning: {pkg}",
            "status": "OK" if match else "ADVARSEL",
            "severity": "LOW" if match else "MEDIUM",
            "reason": (
                "Referanse funnet i dokumenter" if match
                else f"Ingen dokumenter refererer eksplisitt til {pkg}"
            ),
            "source": "Regelmotor",
        })

    # ── Discipline coverage ──────────────────────────────────────
    for disc in discipline_focus:
        disc_low = disc.lower()
        match = any(
            disc_low in (d.get("text_excerpt", "") or "").lower()
            or disc_low in (d.get("filename", "") or "").lower()
            for d in documents
        )
        checklist.append({
            "topic": f"Fagdekning: {disc}",
            "status": "OK" if match else "ADVARSEL",
            "severity": "LOW" if match else "MEDIUM",
            "reason": (
                "Fagreferanse identifisert" if match
                else f"Fag {disc} prioritert men ikke funnet i dokumentene"
            ),
            "source": "Regelmotor",
        })

    # ── Risk items (deterministic) ───────────────────────────────
    risks: List[Dict[str, Any]] = []
    if len(missing) >= 3:
        risks.append({
            "title": "Ufullstendig dokumentgrunnlag",
            "severity": "HIGH",
            "category": "scope",
            "impact": f"{len(missing)} av {len(expected)} forventede dokumentkategorier mangler.",
            "mitigation": "Etterspør manglende dokumenter før kalkulasjon igangsettes.",
            "rfi_needed": True,
            "source": "Regelmotor",
        })

    if bid_value >= 200:
        risks.append({
            "title": "Høy tilbudsverdi krever forsterket kontroll",
            "severity": "HIGH" if bid_value >= 400 else "MEDIUM",
            "category": "pris",
            "impact": f"Tilbudsverdi {bid_value:.0f} MNOK — anbefalt pre-bid review.",
            "mitigation": "Vurder tredjeparts kontroll før innlevering.",
            "rfi_needed": False,
            "source": "Regelmotor",
        })

    if "sha" in missing:
        risks.append({
            "title": "SHA-dokumentasjon mangler",
            "severity": "HIGH",
            "category": "hms",
            "impact": "Byggherreforskriften § 8 krever SHA-plan for utførelsesfasen.",
            "mitigation": "Etterspør SHA-plan og risikovurdering fra byggherre.",
            "rfi_needed": True,
            "source": "Regelmotor",
        })

    # ── Contract fields summary ──────────────────────────────────
    contract_fields = [
        {"field": "Anskaffelsesform", "value": procurement_mode, "source": "Konfigurasjon"},
        {"field": "Pakker", "value": ", ".join(packages) or "-", "source": "Konfigurasjon"},
        {"field": "Estimert tilbudsverdi", "value": f"{bid_value:.0f} MNOK", "source": "Konfigurasjon"},
        {"field": "Prioriterte fag", "value": ", ".join(discipline_focus) or "-", "source": "Konfigurasjon"},
        {"field": "Antall dokumenter", "value": str(len(documents)), "source": "Opplasting"},
        {"field": "Manglende kategorier", "value": ", ".join(missing) if missing else "Ingen", "source": "Regelmotor"},
    ]

    return {
        "checklist_items": checklist,
        "risk_items": risks,
        "missing_categories": missing,
        "expected_categories": expected,
        "contract_fields": contract_fields,
    }


# ═════════════════════════════════════════════════════════════════
# READINESS SCORE — weighted, fact-based
# ═════════════════════════════════════════════════════════════════
READINESS_WEIGHTS = {
    "document_completeness": 0.20,
    "scope_clarity": 0.20,
    "contract_risk": 0.20,
    "pricing_readiness": 0.25,
    "qualification_fit": 0.15,
}


def compute_readiness(
    rule_findings: Dict[str, Any],
    pass2_data: Optional[Dict[str, Any]],
    pass3_data: Optional[Dict[str, Any]],
    documents: List[Dict[str, Any]],
    config: Dict[str, Any],
    rfi_state: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Weighted readiness score using both deterministic rules and AI output.
    Returns per-component scores and overall.

    rfi_state: optional dict keyed by 'rfi_{idx}' mapping to
        {'status': 'open'|'answered', 'answer': str, 'answered_at': str}.
        Besvarte RFI-er løfter scope_clarity og bygger ned det effektive
        antallet HIGH-risk-flagg brukt i sanity caps.
    """
    components: Dict[str, float] = {}

    # ── 1. Document completeness (deterministic baseline) ───────
    expected = rule_findings.get("expected_categories", [])
    missing = rule_findings.get("missing_categories", [])
    if expected:
        components["document_completeness"] = max(
            0.0,
            100.0 * (len(expected) - len(missing)) / len(expected),
        )
    else:
        components["document_completeness"] = 0.0

    # ── 2. AI-sourced components (if available) ─────────────────
    ai_comps = (pass3_data or {}).get("tender_readiness_components") if pass3_data else None

    if isinstance(ai_comps, dict):
        for key in ("scope_clarity", "contract_risk", "pricing_readiness", "qualification_fit"):
            val = ai_comps.get(key)
            if isinstance(val, (int, float)):
                components[key] = max(0.0, min(100.0, float(val)))
            else:
                components[key] = _fallback_component(key, pass2_data, documents, config, rule_findings)
    else:
        for key in ("scope_clarity", "contract_risk", "pricing_readiness", "qualification_fit"):
            components[key] = _fallback_component(key, pass2_data, documents, config, rule_findings)

    # ── 2b. RFI-drevet justering ───────────────────────────────
    rfi_stats = {"total": 0, "answered": 0, "answered_high": 0}
    if rfi_state and pass3_data:
        rfis = pass3_data.get("rfi_queue") or []
        rfi_stats["total"] = len(rfis)
        for idx, rfi in enumerate(rfis):
            state = rfi_state.get(f"rfi_{idx}", {})
            if state.get("status") == "answered":
                rfi_stats["answered"] += 1
                if (rfi.get("priority") or "").upper() == "HIGH":
                    rfi_stats["answered_high"] += 1

        if rfi_stats["total"] > 0:
            # Hver besvart RFI gir scope_clarity-boost,
            # med ekstra vekt til HIGH-prioritet
            share_answered = rfi_stats["answered"] / rfi_stats["total"]
            high_bonus = 4.0 * rfi_stats["answered_high"]
            boost = min(25.0, 20.0 * share_answered + high_bonus)
            components["scope_clarity"] = min(100.0, components["scope_clarity"] + boost)

            # Også mild boost på pricing_readiness ettersom avklaringer
            # typisk reduserer prisingsusikkerhet
            components["pricing_readiness"] = min(
                100.0,
                components["pricing_readiness"] + min(10.0, 10.0 * share_answered),
            )

    # ── 3. Weighted overall ─────────────────────────────────────
    overall = sum(components[k] * w for k, w in READINESS_WEIGHTS.items())

    # ── 4. Sanity caps ──────────────────────────────────────────
    high_risks = sum(
        1 for r in rule_findings.get("risk_items", [])
        if r.get("severity") == "HIGH"
    )
    # Besvarte HIGH-RFI-er reduserer effektivt antall HIGH-risk-flagg
    # (RFI-svar lukker typisk et risk-flagg fra regelmotoren)
    effective_high_risks = max(0, high_risks - rfi_stats["answered_high"])

    # Hard cap if critical docs missing
    critical_missing = [c for c in missing if c in ("konkurransegrunnlag", "beskrivelse", "prisskjema")]
    if critical_missing:
        overall = min(overall, 45.0)
    if effective_high_risks >= 3:
        overall = min(overall, 60.0)

    return {
        "overall": round(overall, 1),
        "components": {k: round(v, 1) for k, v in components.items()},
        "weights": READINESS_WEIGHTS,
        "band": _readiness_band(overall),
        "rfi_stats": rfi_stats,
    }


def _fallback_component(
    key: str,
    pass2_data: Optional[Dict[str, Any]],
    documents: List[Dict[str, Any]],
    config: Dict[str, Any],
    rule_findings: Dict[str, Any],
) -> float:
    """Heuristic fallback when AI didn't provide the component."""
    if key == "scope_clarity":
        if not pass2_data:
            return 40.0 if documents else 0.0
        gaps = pass2_data.get("scope_gaps", []) if isinstance(pass2_data, dict) else []
        conflicts = pass2_data.get("cross_document_conflicts", []) if isinstance(pass2_data, dict) else []
        score = 90.0 - 8.0 * len(gaps) - 5.0 * len(conflicts)
        return max(0.0, min(100.0, score))

    if key == "contract_risk":
        high_risks = sum(
            1 for r in rule_findings.get("risk_items", [])
            if r.get("severity") == "HIGH" and r.get("category") in ("kontrakt", "pris", None)
        )
        return max(0.0, 80.0 - 15.0 * high_risks)

    if key == "pricing_readiness":
        has_prisskjema = any(d.get("category") == "prisskjema" for d in documents)
        has_beskrivelse = any(d.get("category") == "beskrivelse" for d in documents)
        score = 20.0
        if has_beskrivelse:
            score += 30.0
        if has_prisskjema:
            score += 40.0
        return score

    if key == "qualification_fit":
        # Neutral default — this is hard to judge without company profile
        return 60.0

    return 50.0


def _readiness_band(score: float) -> str:
    if score >= 90:
        return "Fullt klar"
    if score >= 70:
        return "Klar med mindre avklaringer"
    if score >= 50:
        return "Krever RFI før innlevering"
    if score >= 30:
        return "Betydelige hull"
    return "Uforsvarlig å levere"


# ═════════════════════════════════════════════════════════════════
# AUDIT TRAIL — Supabase + local JSONL fallback
# ═════════════════════════════════════════════════════════════════
AUDIT_DIR = Path("qa_database/tender_audit")
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG_FILE = AUDIT_DIR / "tender_runs.jsonl"


def _hash_documents(documents: List[Dict[str, Any]]) -> str:
    """Deterministic hash of the document set for audit fingerprinting."""
    parts = []
    for d in sorted(documents, key=lambda x: x.get("filename", "")):
        t = d.get("text") or ""
        h = hashlib.sha256(t.encode("utf-8", errors="replace")).hexdigest()[:12]
        parts.append(f"{d.get('filename')}:{d.get('size_kb')}:{h}")
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def persist_run(
    project_name: str,
    user_id: str,
    config: Dict[str, Any],
    documents: List[Dict[str, Any]],
    rule_findings: Dict[str, Any],
    ai_result: Dict[str, Any],
    readiness: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Persist a full tender analysis run.
    Returns {run_id, timestamp, stored_in: 'supabase'|'local'|'both'}.
    """
    run_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    doc_hash = _hash_documents(documents)

    manifest = [
        {
            "filename": d.get("filename"),
            "category": d.get("category"),
            "size_kb": d.get("size_kb"),
            "page_count": d.get("page_count"),
            "error": d.get("error"),
        }
        for d in documents
    ]

    ai_summary = {
        "pass1_docs": len(ai_result.get("pass1", []) or []),
        "pass2_has_data": bool((ai_result.get("pass2") or {}).get("data")),
        "pass3_has_data": bool((ai_result.get("pass3") or {}).get("data")),
        "backend": (ai_result.get("backend_summary") or {}).get("primary"),
    }

    record = {
        "run_id": run_id,
        "timestamp": timestamp,
        "project_name": project_name,
        "user_id": user_id,
        "module": "TenderControl",
        "config": config,
        "document_count": len(documents),
        "document_hash": doc_hash,
        "manifest": manifest,
        "readiness_overall": readiness.get("overall"),
        "readiness_components": readiness.get("components"),
        "readiness_band": readiness.get("band"),
        "rule_findings_summary": {
            "missing_categories": rule_findings.get("missing_categories", []),
            "high_risks": sum(
                1 for r in rule_findings.get("risk_items", [])
                if r.get("severity") == "HIGH"
            ),
        },
        "ai_summary": ai_summary,
    }

    stored = []

    # Supabase
    sb = _get_supabase()
    if sb is not None:
        try:
            sb.table("tender_runs").insert(record).execute()
            stored.append("supabase")
        except Exception:
            pass

    # Local JSONL fallback (always write)
    try:
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        stored.append("local")
    except Exception:
        pass

    return {
        "run_id": run_id,
        "timestamp": timestamp,
        "stored_in": ",".join(stored) or "none",
        "document_hash": doc_hash,
    }


def load_run_history(project_name: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Load past runs for this project (local + Supabase if configured)."""
    runs: List[Dict[str, Any]] = []

    # Try Supabase first
    sb = _get_supabase()
    if sb is not None:
        try:
            resp = (
                sb.table("tender_runs")
                .select("run_id, timestamp, user_id, document_count, readiness_overall, readiness_band, document_hash")
                .eq("project_name", project_name)
                .order("timestamp", desc=True)
                .limit(limit)
                .execute()
            )
            runs.extend(resp.data or [])
        except Exception:
            pass

    # Fallback / supplement from local
    if AUDIT_LOG_FILE.exists():
        try:
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()[-200:]
            for line in reversed(lines):
                try:
                    rec = json.loads(line)
                    if rec.get("project_name") == project_name:
                        slim = {
                            "run_id": rec.get("run_id"),
                            "timestamp": rec.get("timestamp"),
                            "user_id": rec.get("user_id"),
                            "document_count": rec.get("document_count"),
                            "readiness_overall": rec.get("readiness_overall"),
                            "readiness_band": rec.get("readiness_band"),
                            "document_hash": rec.get("document_hash"),
                        }
                        # de-dupe by run_id
                        if not any(r.get("run_id") == slim["run_id"] for r in runs):
                            runs.append(slim)
                except Exception:
                    continue
        except Exception:
            pass

    runs.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    return runs[:limit]
