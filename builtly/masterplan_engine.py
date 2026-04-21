
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, Iterable, List, Optional

try:
    import anthropic  # type: ignore
except Exception:  # pragma: no cover
    anthropic = None

from .concept_families import PRESETS as CONCEPT_PRESETS
from .concept_families import apply_concept_defaults, concept_subtitle, concept_title
from .geometry import build_default_fields, place_buildings_for_field
from .masterplan_types import (
    BarnehageConfig,
    ConceptFamily,
    Delfelt,
    Masterplan,
    PlanRegler,
)
from .mua import calculate_mua
from .sol import evaluate_solar_report

DEFAULT_MODEL = os.environ.get("BUILTLY_CLAUDE_MODEL", "claude-opus-4-7")
FALLBACK_MODEL = os.environ.get("BUILTLY_CLAUDE_FALLBACK_MODEL", "claude-sonnet-4-6")


def _fallback_json_for_fields(fields: list[Delfelt], concept_family: ConceptFamily) -> dict[str, Any]:
    return {
        "concept_family": concept_family.value,
        "fields": [
            {
                "field_id": f.field_id,
                "typology": f.typology.value,
                "orientation_deg": round(f.orientation_deg, 1),
                "floors_min": f.floors_min,
                "floors_max": f.floors_max,
                "target_bra": round(f.target_bra, 1),
                "courtyard_kind": f.courtyard_kind.value if f.courtyard_kind else None,
                "tower_size_m": f.tower_size_m,
            }
            for f in fields
        ],
    }


def _anthropic_json_call(system_prompt: str, user_prompt: str) -> Optional[dict[str, Any]]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or anthropic is None:
        return None
    client = anthropic.Anthropic(api_key=api_key)
    models = [DEFAULT_MODEL, FALLBACK_MODEL]
    last_exc: Exception | None = None
    for idx, model in enumerate(models):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=1800,
                temperature=0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            )
            match = text[text.find("{"): text.rfind("}") + 1] if "{" in text and "}" in text else text
            return json.loads(match)
        except Exception as exc:  # pragma: no cover
            last_exc = exc
            message = str(exc).lower()
            should_retry = idx == 0 and ("rate" in message or "429" in message or "model" in message or "not found" in message)
            if not should_retry:
                break
    return None


def _apply_ai_pass(fields: list[Delfelt], concept_family: ConceptFamily, context: Optional[dict[str, Any]] = None, use_ai: bool = False) -> list[Delfelt]:
    if not use_ai:
        return fields
    system_prompt = (
        "You are selecting masterplan parameters from a locked menu. "
        "Return strict JSON only. Respect the requested concept family. "
        "LINEAR_MIXED must be lamell-dominant. COURTYARD_URBAN must be karré-dominant. "
        "CLUSTER_PARK must be lamell + punkthus around shared green space."
    )
    fallback = _fallback_json_for_fields(fields, concept_family)
    user_prompt = json.dumps(
        {
            "concept_family": concept_family.value,
            "context": context or {},
            "fields": fallback["fields"],
            "allowed_typologies": ["Karré", "Lamell", "Punkthus", "Rekkehus"],
            "allowed_courtyard_kind": ["felles_bolig", "parkkant", "urban_torg"],
            "allowed_tower_sizes": [17, 21],
        },
        ensure_ascii=False,
    )
    payload = _anthropic_json_call(system_prompt, user_prompt) or fallback
    by_id = {f.field_id: f for f in fields}
    updated: list[Delfelt] = []
    for item in payload.get("fields", []):
        field = by_id.get(item.get("field_id"))
        if field is None:
            continue
        typology_map = {t.value: t for t in field.typology.__class__}
        courtyard_map = {c.value: c for c in field.courtyard_kind.__class__} if field.courtyard_kind else {}
        updated.append(
            Delfelt(
                field_id=field.field_id,
                polygon=field.polygon,
                typology=typology_map.get(item.get("typology"), field.typology),
                orientation_deg=float(item.get("orientation_deg", field.orientation_deg)),
                floors_min=int(item.get("floors_min", field.floors_min)),
                floors_max=int(item.get("floors_max", field.floors_max)),
                target_bra=float(item.get("target_bra", field.target_bra)),
                courtyard_kind=courtyard_map.get(item.get("courtyard_kind"), field.courtyard_kind),
                tower_size_m=item.get("tower_size_m", field.tower_size_m),
                phase=field.phase,
                phase_label=field.phase_label,
            )
        )
    return updated or fields


def _estimate_units(total_bra_m2: float, concept_family: ConceptFamily) -> int:
    avg_size = {
        ConceptFamily.LINEAR_MIXED: 72.0,
        ConceptFamily.COURTYARD_URBAN: 75.0,
        ConceptFamily.CLUSTER_PARK: 78.0,
    }[concept_family]
    return max(1, int(round(total_bra_m2 / avg_size)))


def _score_plan(plan: Masterplan) -> float:
    target_util = 0.0 if plan.target_bra_m2 <= 0 else min(1.0, plan.total_bra_m2 / plan.target_bra_m2)
    mua_ok = 1.0 if all(item.status != "NEI" for item in plan.mua_report.compliant if item.required is not None) else 0.55
    preset = CONCEPT_PRESETS[plan.concept_family]
    low, high = preset.bya_target_pct
    bya_alignment = 1.0
    if plan.bya_pct < low:
        bya_alignment = max(0.15, plan.bya_pct / max(low, 0.1))
    elif plan.bya_pct > high * 1.15:
        bya_alignment = max(0.35, high / max(plan.bya_pct, 0.1))
    typology_ratio = sum(1 for f in plan.delfelt if f.typology == preset.dominant_typology) / max(1, len(plan.delfelt))
    score = 100.0 * (
        0.50 * target_util
        + 0.15 * (plan.sol_report.total_score / 100.0)
        + 0.15 * bya_alignment
        + 0.15 * typology_ratio
        + 0.05 * mua_ok
    )
    if plan.plan_regler.max_bra_pct is not None and plan.bra_pct > plan.plan_regler.max_bra_pct:
        overflow_ratio = plan.bra_pct / max(plan.plan_regler.max_bra_pct, 1.0)
        score *= max(0.82, 1.0 - (overflow_ratio - 1.0) * 0.12)
    return round(score, 1)


def _report_text(plan: Masterplan, use_ai: bool = False) -> str:
    fallback = (
        f"{plan.display_title} fordeler bebyggelsen i {len(plan.delfelt)} delfelt med "
        f"{', '.join(sorted({f.typology.value for f in plan.delfelt}))} som bærende typologier. "
        f"Planen oppnår ca. {plan.total_bra_m2:,.0f} m² BRA, estimerer {plan.antall_boliger} boliger "
        f"og har solscore {plan.sol_report.total_score:.0f}/100.".replace(",", " ")
    )
    if not use_ai:
        return fallback
    payload = _anthropic_json_call(
        "Write a concise Norwegian report summary for a masterplan. Use only supplied numbers.",
        json.dumps(
            {
                "title": plan.display_title,
                "subtitle": plan.display_subtitle,
                "bra_m2": plan.total_bra_m2,
                "bya_pct": plan.bya_pct,
                "sol_score": plan.sol_report.total_score,
                "mua_checks": [{c.key: c.status} for c in plan.mua_report.compliant],
                "boliger": plan.antall_boliger,
            },
            ensure_ascii=False,
        ),
    )
    if payload and isinstance(payload, dict) and payload.get("summary"):
        return str(payload["summary"])
    return fallback


def run_single_masterplan(
    buildable_poly,
    concept_family: ConceptFamily,
    plan_regler: PlanRegler,
    target_bra_m2: float,
    context: Optional[dict[str, Any]] = None,
    barnehage_config: Optional[BarnehageConfig] = None,
    use_ai: bool = False,
) -> Masterplan:
    generation_target_bra = float(target_bra_m2)
    base_fields = build_default_fields(
        buildable_poly,
        target_bra_m2=generation_target_bra,
        concept_family=concept_family,
    )
    fields = apply_concept_defaults(concept_family, base_fields, generation_target_bra)
    fields = _apply_ai_pass(fields, concept_family, context=context, use_ai=use_ai)

    buildings = []
    achieved_bra = 0.0
    for field in fields:
        created, field_bra = place_buildings_for_field(field, buildings)
        buildings.extend(created)
        achieved_bra += field_bra

    total_bya = sum(b.footprint_m2 for b in buildings)
    units = _estimate_units(achieved_bra, concept_family)
    sol_report = evaluate_solar_report(buildings, plan_regler)
    mua_report = calculate_mua(buildable_poly, buildings, plan_regler, units)
    plan = Masterplan(
        concept_family=concept_family,
        delfelt=fields,
        bygg=buildings,
        sol_report=sol_report,
        mua_report=mua_report,
        total_bra_m2=round(achieved_bra, 1),
        total_bya_m2=round(total_bya, 1),
        antall_boliger=units,
        buildable_polygon=buildable_poly,
        display_title=concept_title(concept_family),
        display_subtitle=concept_subtitle(concept_family),
        plan_regler=plan_regler,
        barnehage_config=barnehage_config or BarnehageConfig(),
        bra_deficit=max(0.0, generation_target_bra - achieved_bra),
        target_bra_m2=generation_target_bra,
    )
    plan.report_summary = _report_text(plan, use_ai=use_ai)
    plan.score = _score_plan(plan)
    plan.diagnostics = {
        "concept_family": concept_family.value,
        "target_bra_m2": generation_target_bra,
        "dominant_typology": CONCEPT_PRESETS[concept_family].dominant_typology.value,
    }
    return plan


def run_masterplan_suite(
    buildable_poly,
    plan_regler: PlanRegler,
    target_bra_m2: float,
    context: Optional[dict[str, Any]] = None,
    barnehage_config: Optional[BarnehageConfig] = None,
    use_ai: bool = False,
) -> list[Masterplan]:
    plans = [
        run_single_masterplan(
            buildable_poly=buildable_poly,
            concept_family=concept,
            plan_regler=plan_regler,
            target_bra_m2=target_bra_m2,
            context=context,
            barnehage_config=barnehage_config,
            use_ai=use_ai,
        )
        for concept in (
            ConceptFamily.LINEAR_MIXED,
            ConceptFamily.COURTYARD_URBAN,
            ConceptFamily.CLUSTER_PARK,
        )
    ]
    return sorted(plans, key=lambda p: p.score, reverse=True)
