from __future__ import annotations

import json
from typing import Any, Dict

MODULE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "tender": {
        "title": "Anbudsmodul",
        "task": "document_engine",
        "default_delivery_level": "reviewed",
        "allowed_delivery_levels": ["auto", "reviewed", "attested"],
        "preferred_providers": ["gemini", "anthropic", "openai"],
    },
    "quantity_scope": {
        "title": "Mengde & Scope Intelligence",
        "task": "structured_review",
        "default_delivery_level": "reviewed",
        "allowed_delivery_levels": ["auto", "reviewed"],
        "preferred_providers": ["openai", "anthropic", "gemini"],
    },
    "yield": {
        "title": "Areal- og yield-optimalisering",
        "task": "structured_review",
        "default_delivery_level": "reviewed",
        "allowed_delivery_levels": ["auto", "reviewed"],
        "preferred_providers": ["openai", "anthropic", "gemini"],
    },
    "climate": {
        "title": "Klimarisikomodul",
        "task": "document_engine",
        "default_delivery_level": "auto",
        "allowed_delivery_levels": ["auto"],
        "preferred_providers": ["openai", "anthropic", "gemini"],
    },
    "tdd": {
        "title": "Teknisk Due Diligence",
        "task": "long_context",
        "default_delivery_level": "reviewed",
        "allowed_delivery_levels": ["auto", "reviewed", "attested"],
        "preferred_providers": ["anthropic", "openai", "gemini"],
    },
    "partner_api": {
        "title": "Partnerintegrasjon & API",
        "task": "structured_review",
        "default_delivery_level": "reviewed",
        "allowed_delivery_levels": ["reviewed"],
        "preferred_providers": ["openai", "anthropic", "gemini"],
    },
}

DISCLAIMER_BY_LEVEL = {
    "auto": "Dette er et AI-generert dataprodukt. Det er ikke en faglig attestasjon og kan ikke brukes som grunnlag for byggesøknad eller juridisk bindende avtale.",
    "reviewed": "Dette utkastet er ment for fagperson-gjennomgang. Det er ikke signert med ansvarsrett og er ikke juridisk bindende.",
    "attested": "Denne leveransen er ment å inngå i en attestert arbeidsflyt. Endelig attestasjon og ansvar må utføres av relevant fagperson med korrekt godkjenning.",
}

BASE_SYSTEM_PROMPT = """
Du er en fagspesialisert Builtly-agent.

Arbeidsprinsipper du MÅ følge:
- Rules-first: bygg på eksplisitte sjekker, mangellister og parserfunn før du trekker konklusjoner.
- RAG-first: hvis konteksten inneholder regelverksreferanser eller standardhenvisninger, bruk disse aktivt før generelle antakelser.
- Audit trail: skill tydelig mellom bekreftede funn, antakelser og åpne spørsmål.
- Leverandøruavhengig LLM: svar strukturert og robust, uten å anta at ett bestemt API-format alltid finnes.
- Ikke presenter juridisk rådgivning, myndighetsgodkjenning eller formell sign-off som utført.
- Skriv på norsk bokmål, kortfattet og praktisk.

Outputregler:
- Returner kun gyldig JSON.
- Ikke finn på dokumenter, standarder eller tall som ikke støttes av input.
- Når data mangler, si det eksplisitt i relevante felter.
""".strip()


def module_schema(module_key: str) -> Dict[str, Any]:
    common = {
        "delivery_level": "auto|reviewed|attested",
        "executive_summary": "Kort oppsummering på 3-6 setninger",
        "recommended_status": "Proceed|Proceed with reservations|Need review|Stop",
        "confidence": "Lav|Middels|Høy",
        "assumptions": ["Liste over eksplisitte antakelser"],
        "gaps": ["Manglende data eller uavklarte forhold"],
        "questions": [{"priority": 1, "question": "", "owner": ""}],
        "next_actions": [{"action": "", "owner": "", "priority": "High|Medium|Low", "why": ""}],
        "export_recommendations": ["Relevante eksportformater eller vedlegg"],
    }

    module_specific = {
        "tender": {
            "document_categories": [{"filename": "", "category": "contract|technical_description|sha_plan|drawing_list|price_sheet|regulation|other|unknown", "confidence": 0.0}],
            "contract_fields": [{"field": "", "value": "", "source": ""}],
            "checklist_items": [{"topic": "", "status": "OK|AVVIK|MANGLER", "severity": "LOW|MEDIUM|HIGH", "paragraph_ref": "", "reason": "", "source": ""}],
            "risk_items": [{"title": "", "severity": "HIGH|MEDIUM|LOW", "impact": "", "recommendation": "", "source": "", "paragraph_ref": ""}],
            "rfi_suggestions": [{"priority": 1, "question": "", "why": "", "owner": ""}],
        },
        "quantity_scope": {
            "model_summary": {"ifc_entities": {}, "dxf_entities": {}, "document_roles": {}},
            "area_schedule": [{"label": "", "value_m2": 0.0, "source": ""}],
            "quantity_findings": [{"item": "", "value": "", "source": "", "confidence": "Low|Medium|High"}],
            "revision_deltas": [{"document_family": "", "change": "", "impact": "", "source": ""}],
            "scope_risks": [{"title": "", "severity": "HIGH|MEDIUM|LOW", "why": "", "recommendation": ""}],
        },
        "yield": {
            "current_metrics": {"gross_m2": 0.0, "net_m2": 0.0, "saleable_m2": 0.0, "lettable_m2": 0.0, "core_ratio_pct": 0.0, "technical_ratio_pct": 0.0, "circulation_ratio_pct": 0.0},
            "bottlenecks": [{"title": "", "impact": "", "source": ""}],
            "scenario_options": [{"name": "", "uplift_m2": 0.0, "uplift_pct": 0.0, "tradeoff": "", "action": ""}],
            "decision_note": "Kort notat om hva som bør testes først",
        },
        "climate": {
            "asset_count": 0,
            "risk_factors": [{"factor": "flood|landslide|sea_level|heat_stress", "score": 0.0, "confidence": 0.0, "source": "", "note": ""}],
            "aggregate_score": 0.0,
            "uncertainty_interval": 0.0,
            "regulatory_outputs": [{"framework": "EU Taxonomy|SFDR|ECB|Finanstilsynet", "status": "ready|partial|needs_review", "note": ""}],
            "portfolio_notes": [{"asset": "", "score": 0.0, "note": ""}],
        },
        "tdd": {
            "data_completeness_score": 0.0,
            "public_data_snapshot": [{"source": "", "status": "ok|missing|manual", "note": ""}],
            "building_parts": [{"part": "", "tg": "TG0|TG1|TG2|TG3|Unknown", "remaining_life_years": "", "remediation_cost_range_nok": "", "reason": "", "source": ""}],
            "tek17_deviations": [{"title": "", "category": "KRITISK|VESENTLIG|ANBEFALT", "recommendation": "", "source": ""}],
            "risk_matrix": {"technical_risk": "", "financial_risk": "", "regulatory_risk": "", "overall_class": "LAV|MIDDELS|HØY", "remediation_cost_total_nok": 0},
        },
        "partner_api": {
            "integration_blueprint": [{"topic": "tenant|auth|branding|webhook|billing|signoff", "recommendation": "", "priority": "High|Medium|Low"}],
            "security_checks": [{"check": "", "status": "ready|partial|missing", "note": ""}],
            "rollout_plan": [{"phase": "", "deliverable": "", "owner": ""}],
        },
    }

    schema = dict(common)
    schema.update(module_specific[module_key])
    return schema


def module_system_prompt(module_key: str, delivery_level: str) -> str:
    module_titles = {
        "tender": "Anbudsmodul",
        "quantity_scope": "Mengde & Scope Intelligence",
        "yield": "Areal- og yield-optimalisering",
        "climate": "Klimarisikomodul",
        "tdd": "Teknisk Due Diligence",
        "partner_api": "Partnerintegrasjon & API",
    }
    module_instructions = {
        "tender": "Analyser anbudsdokumenter uten å gi juridisk rådgivning. Prioriter kontraktsrisiko, mangellister, SHA-referanser, prisgrunnlag, grensesnitt og konkrete RFI-spørsmål. Vurder avvik opp mot NS-standarder kun når input faktisk refererer til dem.",
        "quantity_scope": "Bygg et sporingsbart bilde av mengder, arealer og revisjonsendringer. Skill tydelig mellom faktiske parserfunn og antatte mengder. Ikke finn på presise mengder hvis modellgrunnlaget er svakt.",
        "yield": "Vær en beslutningsmotor for arealeffektivitet, ikke en generativ arkitekt. Prioriter brutto/netto, salgbart/utleibart, kjerneandel, tekniske rom og scenarioer med tydelige trade-offs.",
        "climate": "Behandle resultatet som et Nivå 1 dataprodukt. Ikke påstå regulatorisk godkjenning. Vurder flom, skred, havnivå og varmestress, og map kun til relevante rapporteringsrammer.",
        "tdd": "Lag et transaksjonsnært TDD-utkast. Vurder datakompletthet, offentlige datasnapshots, TG per bygningsdel, TEK17-avvik og samlet risikomatrise. Ikke signer eller påstå ansvarsrett.",
        "partner_api": "Lag et privat partnerutkast for integrering av Builtly-motoren i partnerens arbeidsflyt. Ikke bruk ordet white-label i output. Prioriter tenant-isolasjon, auth, sign-off, webhooker, billing og onboarding.",
    }
    return (
        f"{BASE_SYSTEM_PROMPT}\n\n"
        f"Modul: {module_titles[module_key]}.\n"
        f"Leveransenivå: {delivery_level}.\n"
        f"Disclaimer som skal reflekteres i vurderingen: {DISCLAIMER_BY_LEVEL[delivery_level]}\n\n"
        f"Modulspesifikke instrukser: {module_instructions[module_key]}"
    ).strip()


def format_prompt_context(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)
