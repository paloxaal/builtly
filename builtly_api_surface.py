from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Sequence

from builtly_document_engine import (
    build_markdown_report,
    manifest_dataframe,
    normalize_uploaded_files,
    revision_dataframe,
    run_module_analysis,
    tender_rules_payload,
    tdd_rules_payload,
)
from builtly_public_data import adapter_status, gather_climate_snapshot, run_climate_portfolio_batch, run_tdd_portfolio_batch

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
except Exception:  # pragma: no cover
    FastAPI = None
    HTTPException = Exception
    JSONResponse = dict


SCOPE_TO_MODULE = {
    "tdd:read": ["tdd"],
    "tdd:write": ["tdd"],
    "anbud:read": ["tender"],
    "anbud:write": ["tender"],
    "climate:read": ["climate"],
    "climate:write": ["climate"],
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id(prefix: str, payload: Dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def issue_partner_token(client_id: str, client_secret: str, scope: str) -> Dict[str, Any]:
    seed = f"{client_id}|{scope}|{datetime.now(timezone.utc).date().isoformat()}"
    token = base64.urlsafe_b64encode(hashlib.sha256(seed.encode("utf-8")).digest()).decode("ascii").rstrip("=")
    return {
        "access_token": token,
        "expires_in": 3600,
        "token_type": "Bearer",
        "scope": scope,
        "issued_at": _utc_now(),
    }


def submit_tender_job(project: Dict[str, Any], files: Sequence, user_inputs: Dict[str, Any], delivery_level: str = "reviewed") -> Dict[str, Any]:
    records = normalize_uploaded_files(files)
    rules = tender_rules_payload(project, records, user_inputs)
    ai = run_module_analysis("tender", project, records, rules, delivery_level)
    tender_id = _short_id("tender", {"project": project.get("p_name"), "docs": [r["sha12"] for r in records], "level": delivery_level})
    report_markdown = build_markdown_report(
        module_title="Tender Control",
        project=project,
        manifest_records=records,
        revision_records=revision_dataframe(records).to_dict(orient="records"),
        ai_payload=ai,
    )
    return {
        "tender_id": tender_id,
        "status": "processing_complete",
        "estimated_completion_minutes": 0,
        "manifest": manifest_dataframe(records).to_dict(orient="records"),
        "rules_payload": rules,
        "analysis": ai,
        "output_files": [
            {"type": "markdown", "download_url": f"/api/v1/tenders/{tender_id}/report.md"},
            {"type": "json", "download_url": f"/api/v1/tenders/{tender_id}/report.json"},
        ],
        "markdown": report_markdown,
    }


def submit_tdd_job(project: Dict[str, Any], files: Sequence, user_inputs: Dict[str, Any], delivery_level: str = "reviewed") -> Dict[str, Any]:
    records = normalize_uploaded_files(files)
    rules = tdd_rules_payload(project, records, user_inputs)
    ai = run_module_analysis("tdd", project, records, rules, delivery_level)
    tdd_id = _short_id("tdd", {"project": project.get("p_name"), "docs": [r["sha12"] for r in records], "level": delivery_level})
    report_markdown = build_markdown_report(
        module_title="Teknisk Due Diligence",
        project=project,
        manifest_records=records,
        revision_records=revision_dataframe(records).to_dict(orient="records"),
        ai_payload=ai,
    )
    return {
        "tdd_id": tdd_id,
        "status": "processing_complete",
        "data_completeness_score": rules.get("data_completeness_score", 0.0),
        "risk_matrix": (ai.get("data") or {}).get("risk_matrix") or rules.get("risk_matrix"),
        "rules_payload": rules,
        "analysis": ai,
        "markdown": report_markdown,
    }


def submit_climate_job(asset: Dict[str, Any], delivery_level: str = "auto") -> Dict[str, Any]:
    snapshot = gather_climate_snapshot(asset, scenario=asset.get("scenario", "RCP 4.5"), horizon=asset.get("horizon", "2050"), weights=asset.get("weights"))
    return {
        "climate_id": _short_id("climate", asset),
        "status": "processing_complete",
        "delivery_level": delivery_level,
        "result": snapshot,
    }


def partner_stats_preview(from_date: str = "2026-01-01", to_date: str = "2026-03-31") -> Dict[str, Any]:
    return {
        "reports_total": 47,
        "by_module": {"tdd": 23, "anbud": 18, "klima": 6},
        "revenue_share_nok": 320000,
        "from": from_date,
        "to": to_date,
    }


def build_openapi_preview() -> Dict[str, Any]:
    return {
        "title": "Builtly API preview",
        "version": "0.5",
        "servers": [{"url": "/api/v1"}],
        "auth": {"token_endpoint": "/auth/token", "grant_type": "client_credentials"},
        "paths": {
            "/tenders": {"post": "Submit tender package and receive checklist, risks and output file references."},
            "/tenders/{tender_id}": {"get": "Get tender status, checklist and report references."},
            "/tdd": {"post": "Submit TDD job and receive completeness score and risk matrix."},
            "/tdd/portfolio-batch": {"post": "Submit portfolio batch for bank screening."},
            "/climate-risk": {"post": "Run single climate risk analysis."},
            "/climate-risk/portfolio": {"post": "Run climate portfolio batch with webhook-style callback metadata."},
            "/partner/stats": {"get": "Read partner volume and revenue-share preview."},
            "/system/adapters": {"get": "Inspect current public-data adapter setup."},
        },
    }


def create_app() -> Any:
    if FastAPI is None:
        return None
    app = FastAPI(title="Builtly API preview", version="0.5")

    @app.get("/api/v1/system/adapters")
    def system_adapters() -> Dict[str, Any]:
        return {"adapters": adapter_status()}

    @app.post("/auth/token")
    def auth_token(body: Dict[str, Any]) -> Dict[str, Any]:
        return issue_partner_token(body.get("client_id", "demo"), body.get("client_secret", "demo"), body.get("scope", ""))

    @app.post("/api/v1/tenders")
    def tenders(body: Dict[str, Any]) -> Dict[str, Any]:
        project = body.get("project") or {}
        return submit_tender_job(project, [], body, body.get("delivery_level", "reviewed"))

    @app.post("/api/v1/tdd")
    def tdd(body: Dict[str, Any]) -> Dict[str, Any]:
        project = body.get("project") or {}
        return submit_tdd_job(project, [], body, body.get("delivery_level", "reviewed"))

    @app.post("/api/v1/tdd/portfolio-batch")
    def tdd_batch(body: Dict[str, Any]) -> Dict[str, Any]:
        return run_tdd_portfolio_batch(body.get("properties", []), partner_id=body.get("partner_id", ""))

    @app.post("/api/v1/climate-risk")
    def climate(body: Dict[str, Any]) -> Dict[str, Any]:
        return submit_climate_job(body, body.get("delivery_level", "auto"))

    @app.post("/api/v1/climate-risk/portfolio")
    def climate_batch(body: Dict[str, Any]) -> Dict[str, Any]:
        return run_climate_portfolio_batch(
            body.get("properties", []),
            partner_id=body.get("partner_id", ""),
            scenario=body.get("scenario", "RCP 4.5"),
            horizon=body.get("horizon", "2050"),
            weights=body.get("weights"),
        )

    @app.get("/api/v1/partner/stats")
    def partner_stats(from_date: str = "2026-01-01", to_date: str = "2026-03-31") -> Dict[str, Any]:
        return partner_stats_preview(from_date, to_date)

    return app


app = create_app()
