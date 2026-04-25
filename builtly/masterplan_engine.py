from __future__ import annotations

"""Builtly v8 delivery 4 — deterministic engine with AI hooks.

Passes implemented in this module:
- Pass 1: deterministic delfelt geometry
- Pass 2: parameter selection from locked menus (AI hook + deterministic fallback)
- Pass 3: deterministic volume placement
- Pass 4: deterministic solar analysis
- Pass 5: deterministic MUA/compliance analysis
- Pass 6: narrative/report generation (AI hook + deterministic fallback)

Important design guardrails:
- AI is never allowed to place coordinates or draw footprints.
- Geometry remains deterministic and reproducible.
- Concept alternatives are generated at whole-site level, not by phase.
"""

import json
import logging
import math
import os
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from shapely.geometry import Polygon

from .concept_families import all_concept_families, get_strategy
from .geometry import (
    build_field_skeleton_summaries,
    building_geometry_is_orthogonal_to_field,
    buildings_do_not_overlap,
    compute_architecture_metrics,
    place_buildings_for_fields,
    pca_site_axes,
    resolve_delfelt_count,
    subdivide_buildable_polygon,
)
from .masterplan_types import (
    BarnehageConfig,
    ConceptFamily,
    CourtyardKind,
    Delfelt,
    FieldParameterChoice,
    Masterplan,
    MUAReport,
    PlanRegler,
    ReportNarrative,
    StructuredPassClient,
    Typology,
)
from .mua import calculate_mua
from .sol import compute_sol_report

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = os.environ.get("BUILTLY_CLAUDE_MODEL", "claude-opus-4-7")
FALLBACK_MODEL = os.environ.get("BUILTLY_CLAUDE_FALLBACK_MODEL", "claude-sonnet-4-6")

_PASS1_DEFAULT_FLOORS: Dict[Typology, Tuple[int, int]] = {
    Typology.LAMELL: (4, 6),
    Typology.PUNKTHUS: (5, 6),
    Typology.KARRE: (5, 6),
    Typology.REKKEHUS: (2, 3),
}

_FALLBACK_TITLES: Dict[ConceptFamily, str] = {
    ConceptFamily.LINEAR_MIXED: "Lineært blandet boliggrep",
    ConceptFamily.COURTYARD_URBAN: "Urban kvartalsstruktur med gårdsrom",
    ConceptFamily.CLUSTER_PARK: "Boligklynger rundt grønt fellesrom",
}

_FALLBACK_SUBTITLES: Dict[ConceptFamily, str] = {
    ConceptFamily.LINEAR_MIXED: "Lameller og punkthus organisert langs tomtas hovedakse.",
    ConceptFamily.COURTYARD_URBAN: "Karréer mot kantene og roligere boliger i innsiden.",
    ConceptFamily.CLUSTER_PARK: "Klynger av lameller og punkthus rundt et felles grøntrom.",
}


def _clamp_float(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _safe_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(line for line in cleaned.splitlines() if not line.strip().startswith("```"))
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _should_retry_with_fallback(status_code: Optional[int], body: str) -> bool:
    if status_code == 429:
        return True
    body_l = (body or "").lower()
    model_markers = ("model", "not found", "unknown model", "invalid model", "unsupported model")
    return bool(status_code and status_code >= 400 and any(marker in body_l for marker in model_markers))


def _anthropic_json_call(*, system_prompt: str, user_payload: Dict[str, Any], api_key: Optional[str] = None, model: Optional[str] = None, max_tokens: int = 1600) -> Optional[Dict[str, Any]]:
    if str(os.environ.get("BUILTLY_ENABLE_NETWORK_AI", "")).lower() not in {"1", "true", "yes"}:
        return None
    key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not key:
        return None

    def _post(model_name: str) -> requests.Response:
        return requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model_name,
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "system": system_prompt,
                "messages": [{"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}],
            },
            timeout=60,
        )

    primary_model = model or DEFAULT_MODEL
    tried = [primary_model]
    try:
        resp = _post(primary_model)
        if resp.ok:
            blocks = resp.json().get("content", [])
            text = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")
            return _safe_json_from_text(text)
        body = resp.text
        if FALLBACK_MODEL and FALLBACK_MODEL != primary_model and _should_retry_with_fallback(resp.status_code, body):
            tried.append(FALLBACK_MODEL)
            logger.warning("Anthropic primary model %s failed (%s). Retrying once with fallback model %s.", primary_model, resp.status_code, FALLBACK_MODEL)
            fallback_resp = _post(FALLBACK_MODEL)
            if fallback_resp.ok:
                blocks = fallback_resp.json().get("content", [])
                text = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")
                return _safe_json_from_text(text)
            logger.warning("Anthropic fallback model %s failed (%s).", FALLBACK_MODEL, fallback_resp.status_code)
            return None
        logger.warning("Anthropic primary model %s failed without retry (%s).", primary_model, resp.status_code)
        return None
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("Anthropic pass call failed for models %s: %s", tried, exc)
        return None


# ---------------------------------------------------------------------------
# Pass 1 — site delfelt geometry
# ---------------------------------------------------------------------------


def _seed_neutral_fields(field_polygons: Sequence[Polygon], target_bra_m2: float, orientation_deg: float) -> List[Delfelt]:
    total_area = sum(p.area for p in field_polygons) or 1.0
    out: List[Delfelt] = []
    for idx, poly in enumerate(field_polygons, start=1):
        share = poly.area / total_area
        out.append(
            Delfelt(
                field_id=f"DF{idx}",
                polygon=poly.buffer(0),
                typology=Typology.LAMELL,
                orientation_deg=orientation_deg,
                floors_min=4,
                floors_max=5,
                target_bra=float(target_bra_m2 * share),
                courtyard_kind=CourtyardKind.FELLES_BOLIG,
                tower_size_m=None,
                phase=idx,
                phase_label=f"Delfelt {idx}",
            )
        )
    return out


def pass1_generate_delfelt(buildable_poly: Polygon, concept_family: ConceptFamily, target_bra_m2: float, requested_count: Optional[int] = None) -> List[Delfelt]:
    axes = pca_site_axes(buildable_poly)
    count = resolve_delfelt_count(buildable_poly, requested_count=requested_count)
    area = max(float(buildable_poly.area), 1.0)
    density = float(target_bra_m2) / area if target_bra_m2 > 0 else 0.0
    if requested_count is None:
        compact_infill = area <= 3500.0 or (area <= 7000.0 and density >= 1.75)
        urban_infill = not compact_infill and area <= 9000.0 and density >= 1.30

        # Små, tette tomter skal ikke over-fragmenteres. Der skjer variasjonen inne i
        # ett eller to felt – ikke gjennom mange små delfelt.
        if compact_infill:
            count = 1 if area <= 3500.0 else min(max(count, 1), 2)
        elif urban_infill:
            count = min(max(count, 2), 3)
            if density >= 1.80:
                count = min(count + 1, 3)
        else:
            if density >= 1.0:
                count += 1
            if density >= 1.15:
                count += 1
            if density >= 1.28 and concept_family != ConceptFamily.COURTYARD_URBAN:
                count += 1
            # Makro/mikro-prinsipp: hold makrofeltene rolige, men gi lineære og parkgrep
            # nok armer til å bygge rytme. Karré-grep holdes strammere for å unngå for små blokker.
            if concept_family == ConceptFamily.LINEAR_MIXED:
                # Runde 8.1: færre, større makrofelt gir mer lesbare gaterom og
                # interne akser; flere bygg håndteres inne i hvert felt.
                count = max(3, min(count, 5 if density < 1.18 else 6))
            elif concept_family == ConceptFamily.CLUSTER_PARK:
                # Ett tydelig grønt fellesrom krever større sammenhengende felt,
                # ikke 6-7 små delfelt med hver sin lokale park.
                count = max(3, min(count, 4))
            else:  # COURTYARD_URBAN
                # Karré trenger feltstørrelse for komplette kvartalsringer.
                count = max(2, min(count, 4 if density >= 1.18 else 3))
    polygons = subdivide_buildable_polygon(buildable_poly, count=count, orientation_deg=axes.theta_deg)
    seeded = _seed_neutral_fields(polygons, target_bra_m2, axes.theta_deg)
    return [replace(field, phase=idx, phase_label=f"Delfelt {idx}") for idx, field in enumerate(seeded, start=1)]


# ---------------------------------------------------------------------------
# Pass 2 — parameter selection
# ---------------------------------------------------------------------------


def _apply_parameter_choices(base_fields: Sequence[Delfelt], choices: Sequence[FieldParameterChoice]) -> List[Delfelt]:
    choice_by_id = {c.field_id: c for c in choices}
    out: List[Delfelt] = []
    for field in base_fields:
        choice = choice_by_id.get(field.field_id)
        if choice is None:
            out.append(field)
            continue
        out.append(
            replace(
                field,
                typology=choice.typology,
                orientation_deg=float(choice.orientation_deg),
                floors_min=int(choice.floors_min),
                floors_max=int(choice.floors_max),
                target_bra=float(choice.target_bra),
                courtyard_kind=choice.courtyard_kind,
                tower_size_m=choice.tower_size_m,
                field_role=choice.field_role or field.field_role,
                character=choice.character or field.character,
                arm_id=choice.arm_id or field.arm_id,
                design_variant=choice.design_variant,
                design_karre_shape=choice.design_karre_shape,
                design_height_pattern=choice.design_height_pattern,
                target_bya_pct=choice.target_bya_pct,
                skeleton_mode=choice.skeleton_mode,
                frontage_mode=choice.frontage_mode,
                micro_band_count=int(choice.micro_band_count or 0),
                view_corridor_count=int(choice.view_corridor_count or 0),
                courtyard_reserve_ratio=float(choice.courtyard_reserve_ratio or 0.0),
                frontage_depth_m=choice.frontage_depth_m,
                corridor_width_m=choice.corridor_width_m,
                macro_structure=choice.macro_structure,
                micro_field_pattern=choice.micro_field_pattern,
                symmetry_preference=choice.symmetry_preference,
                composition_strictness=float(choice.composition_strictness or 0.0),
                frontage_zone_ratio=float(choice.frontage_zone_ratio or 0.0),
                public_realm_ratio=float(choice.public_realm_ratio or 0.0),
                node_symmetry=bool(choice.node_symmetry),
                frontage_primary_side=choice.frontage_primary_side,
                frontage_secondary_side=choice.frontage_secondary_side,
                lamell_rhythm_mode=choice.lamell_rhythm_mode,
                node_layout_mode=choice.node_layout_mode,
                courtyard_open_side=choice.courtyard_open_side,
                target_building_count=int(choice.target_building_count or 0),
                frontage_emphasis=float(choice.frontage_emphasis or 0.0),
                rhythm_strength=float(choice.rhythm_strength or 0.0),
            )
        )
    return out


def _normalize_choices(
    concept_family: ConceptFamily,
    base_fields: Sequence[Delfelt],
    fallback_choices: Sequence[FieldParameterChoice],
    raw_data: Optional[Dict[str, Any]],
    target_bra_m2: float,
    plan_regler: Optional[PlanRegler],
) -> List[FieldParameterChoice]:
    strategy = get_strategy(concept_family)
    fallback_by_id = {c.field_id: c for c in fallback_choices}
    allowed_by_id = {
        field.field_id: strategy.envelope_for_field(idx, len(base_fields))
        for idx, field in enumerate(base_fields)
    }

    if not raw_data or raw_data.get("concept_family") != concept_family.value:
        return list(fallback_choices)

    raw_fields = raw_data.get("fields")
    if not isinstance(raw_fields, list):
        return list(fallback_choices)

    normalized: List[FieldParameterChoice] = []
    total_requested = 0.0
    for raw_field in raw_fields:
        if not isinstance(raw_field, dict):
            continue
        field_id = str(raw_field.get("field_id", ""))
        if field_id not in fallback_by_id:
            continue
        fb = fallback_by_id[field_id]
        envelope = allowed_by_id[field_id]

        typology_raw = raw_field.get("typology", fb.typology.value)
        try:
            parsed_typology = Typology(str(typology_raw))
        except Exception:
            parsed_typology = fb.typology
        if parsed_typology not in envelope.allowed_typologies:
            parsed_typology = envelope.default_typology

        base_angles = [fb.orientation_deg % 180.0, (fb.orientation_deg + 90.0) % 180.0]
        raw_orientation = float(raw_field.get("orientation_deg", fb.orientation_deg)) % 180.0
        snapped_orientation = min(base_angles, key=lambda a: min(abs(a - raw_orientation), 180.0 - abs(a - raw_orientation)))

        floors_min = int(raw_field.get("floors_min", fb.floors_min))
        floors_max = int(raw_field.get("floors_max", fb.floors_max))
        floors_min = max(1, floors_min)
        floors_max = max(floors_min, floors_max)
        if plan_regler and plan_regler.max_floors is not None:
            floors_max = min(floors_max, int(plan_regler.max_floors))
            floors_min = min(floors_min, floors_max)

        courtyard_kind_raw = raw_field.get("courtyard_kind")
        try:
            courtyard_kind = CourtyardKind(courtyard_kind_raw) if courtyard_kind_raw else fb.courtyard_kind
        except Exception:
            courtyard_kind = fb.courtyard_kind

        tower_size_m = raw_field.get("tower_size_m", fb.tower_size_m)
        if parsed_typology != Typology.PUNKTHUS or tower_size_m not in {17, 20, 21}:
            tower_size_m = fb.tower_size_m if parsed_typology == Typology.PUNKTHUS else None

        target_bra = float(raw_field.get("target_bra", fb.target_bra) or 0.0)
        if target_bra <= 0:
            target_bra = fb.target_bra
        total_requested += target_bra

        normalized.append(
            FieldParameterChoice(
                field_id=field_id,
                typology=parsed_typology,
                orientation_deg=snapped_orientation,
                floors_min=floors_min,
                floors_max=floors_max,
                target_bra=target_bra,
                courtyard_kind=courtyard_kind,
                tower_size_m=tower_size_m,
                rationale=str(raw_field.get("rationale", "") or fb.rationale),
                field_role=fb.field_role,
                character=fb.character,
                arm_id=fb.arm_id,
                design_variant=fb.design_variant,
                design_karre_shape=fb.design_karre_shape,
                design_height_pattern=fb.design_height_pattern,
                target_bya_pct=fb.target_bya_pct,
                skeleton_mode=fb.skeleton_mode,
                frontage_mode=fb.frontage_mode,
                micro_band_count=fb.micro_band_count,
                view_corridor_count=fb.view_corridor_count,
                courtyard_reserve_ratio=fb.courtyard_reserve_ratio,
                frontage_depth_m=fb.frontage_depth_m,
                corridor_width_m=fb.corridor_width_m,
                macro_structure=fb.macro_structure,
                micro_field_pattern=fb.micro_field_pattern,
                symmetry_preference=fb.symmetry_preference,
                composition_strictness=fb.composition_strictness,
                frontage_zone_ratio=fb.frontage_zone_ratio,
                public_realm_ratio=fb.public_realm_ratio,
                node_symmetry=fb.node_symmetry,
                frontage_primary_side=fb.frontage_primary_side,
                frontage_secondary_side=fb.frontage_secondary_side,
                lamell_rhythm_mode=fb.lamell_rhythm_mode,
                node_layout_mode=fb.node_layout_mode,
                courtyard_open_side=fb.courtyard_open_side,
                target_building_count=fb.target_building_count,
                frontage_emphasis=fb.frontage_emphasis,
                rhythm_strength=fb.rhythm_strength,
            )
        )

    if len(normalized) != len(base_fields) or total_requested <= 0:
        return list(fallback_choices)

    # Re-normalize target BRA to preserve deterministic total target.
    scale = target_bra_m2 / total_requested
    normalized = [replace(choice, target_bra=float(choice.target_bra * scale)) for choice in normalized]
    normalized.sort(key=lambda c: c.field_id)
    return normalized


def pass2_select_field_parameters(
    concept_family: ConceptFamily,
    base_fields: Sequence[Delfelt],
    target_bra_m2: float,
    plan_regler: Optional[PlanRegler] = None,
    neighbors: Optional[Sequence[dict]] = None,
    ai_selector: Optional[StructuredPassClient] = None,
) -> Tuple[List[Delfelt], str]:
    strategy = get_strategy(concept_family)
    fallback_choices = strategy.propose(base_fields, target_bra_m2, plan_regler=plan_regler, neighbors=neighbors)

    payload = {
        "concept_family": concept_family.value,
        "fields": [field.to_dict() for field in base_fields],
        "fallback_choices": [
            {
                "field_id": c.field_id,
                "typology": c.typology.value,
                "orientation_deg": c.orientation_deg,
                "floors_min": c.floors_min,
                "floors_max": c.floors_max,
                "target_bra": c.target_bra,
                "courtyard_kind": c.courtyard_kind.value if c.courtyard_kind else None,
                "tower_size_m": c.tower_size_m,
                "rationale": c.rationale,
            }
            for c in fallback_choices
        ],
        "allowed_envelopes": {
            field.field_id: {
                "allowed_typologies": [typ.value for typ in strategy.envelope_for_field(idx, len(base_fields)).allowed_typologies],
                "allowed_angles": [field.orientation_deg % 180.0, (field.orientation_deg + 90.0) % 180.0],
                "allowed_tower_sizes_m": [17, 20, 21],
            }
            for idx, field in enumerate(base_fields)
        },
        "target_bra_m2": target_bra_m2,
        "plan_regler": {
            "max_floors": plan_regler.max_floors if plan_regler else None,
            "max_height_m": plan_regler.max_height_m if plan_regler else None,
        },
    }

    raw_data: Optional[Dict[str, Any]] = None
    source = "fallback"
    if ai_selector is not None:
        try:
            raw_data = ai_selector(payload)
            if raw_data:
                source = "ai_selector"
        except Exception as exc:
            logger.warning("pass2 ai_selector failed: %s", exc)
            raw_data = None
    if raw_data is None:
        raw_data = _anthropic_json_call(
            system_prompt=(
                "You are pass 2 in a deterministic masterplan engine. "
                "Return JSON only. Never generate coordinates. Never invent new field ids. "
                "Pick only from the allowed typologies/angles/tower sizes."
            ),
            user_payload=payload,
        )
        if raw_data:
            source = "anthropic"

    normalized_choices = _normalize_choices(concept_family, base_fields, fallback_choices, raw_data, target_bra_m2, plan_regler)
    configured_fields = _apply_parameter_choices(base_fields, normalized_choices)
    return configured_fields, source


# ---------------------------------------------------------------------------
# Pass 3 / 4 / 5 — deterministic core
# ---------------------------------------------------------------------------


def pass3_place_buildings(buildable_poly: Polygon, delfelt: Sequence[Delfelt], plan_regler: Optional[PlanRegler] = None, barnehage_config: Optional[BarnehageConfig] = None):
    del barnehage_config
    return place_buildings_for_fields(buildable_poly, list(delfelt), plan_regler=plan_regler)


def pass4_calculate_solar(*, buildings, buildable_poly: Polygon, latitude_deg: float, longitude_deg: float, neighbors: Optional[Sequence[dict]] = None, year: Optional[int] = None, rules: Optional[PlanRegler] = None):
    return compute_sol_report(buildable_poly, list(buildings), latitude_deg=latitude_deg, longitude_deg=longitude_deg, neighbors=neighbors, rules=rules, year=(year if year is not None else 2026))


def pass5_calculate_mua(plan: Masterplan, plan_regler: Optional[PlanRegler] = None) -> MUAReport:
    return calculate_mua(plan, regler=plan_regler or plan.plan_regler)


# ---------------------------------------------------------------------------
# Pass 6 — narrative/report generation
# ---------------------------------------------------------------------------


def _fallback_narrative(plan: Masterplan, target_bra_m2: float) -> ReportNarrative:
    fit_pct = 100.0 * plan.total_bra_m2 / max(target_bra_m2, 1.0)
    typology_mix = sorted({b.typology.value for b in plan.bygg})
    mua_mode = str(getattr(plan.mua_report, "mode", "strict") or "strict")
    if mua_mode == "advisory":
        mua_state = "vurderer MUA rådgivende i dette tette infill-scenariet"
    elif mua_mode == "reduced":
        mua_state = "vurderes med redusert MUA-krav i tidligfase"
    else:
        mua_state = "oppfyller" if plan.mua_report.compliant else "har avklaringspunkter i"
    title = _FALLBACK_TITLES[plan.concept_family]
    summary = (
        f"{title} fordeler bebyggelsen i {len(plan.delfelt)} delfelt med "
        f"{', '.join(typology_mix)} som bærende typologier. "
        f"Planen oppnår ca. {fit_pct:.0f}% av mål-BRA, estimerer {plan.antall_boliger} boliger "
        f"og har solscore {plan.sol_report.total_sol_score:.0f}/100. "
        f"Arkitekturscore {plan.architecture_report.total_score:.0f}/100."
    )
    arch = (
        f"Grepet leses som {plan.concept_family.value} med tydelig feltorientering og "
        f"deterministisk, ortogonal geometri. Arkitekturvurderingen scorer "
        f"{plan.architecture_report.total_score:.0f}/100, og planen {mua_state} MUA-kravene med "
        f"BRA-avvik på {plan.bra_deficit:.0f} m²."
    )
    recommendation = (
        "Anbefales som utgangspunkt hvis utvikler ønsker et reproduserbart konseptgrep "
        "som senere kan fases og detaljutvikles videre uten å miste strukturen."
    )
    risks: List[str] = []
    if plan.bra_deficit > 0:
        risks.append(f"BRA-mål nås ikke fullt ut; underskudd ca. {plan.bra_deficit:.0f} m².")
    if not plan.mua_report.compliant and str(getattr(plan.mua_report, "mode", "strict") or "strict") == "strict":
        risks.append("MUA/compliance har ett eller flere åpne punkter som må verifiseres videre.")
    elif str(getattr(plan.mua_report, "mode", "strict") or "strict") != "strict":
        risks.append("MUA er håndtert som kontekstavhengig krav (tett infill / kompakt tomt) og må verifiseres videre i regulerings- eller byggesaksfase.")
    if plan.sol_report.total_sol_score < 40:
        risks.append("Solforholdene er svake og bør forbedres med orientering, avstand eller høydereduksjon.")
    return ReportNarrative(title=title, summary=summary, architectural_assessment=arch, recommendation=recommendation, risks=risks, source="fallback")


def pass6_generate_report_text(plan: Masterplan, target_bra_m2: float, ai_reporter: Optional[StructuredPassClient] = None) -> ReportNarrative:
    payload = {
        "concept_family": plan.concept_family.value,
        "display_title_default": _FALLBACK_TITLES[plan.concept_family],
        "display_subtitle_default": _FALLBACK_SUBTITLES[plan.concept_family],
        "metrics": {
            "target_bra_m2": target_bra_m2,
            "total_bra_m2": plan.total_bra_m2,
            "total_bya_m2": plan.total_bya_m2,
            "antall_boliger": plan.antall_boliger,
            "sol_score": plan.sol_report.total_sol_score,
            "mua_compliant": plan.mua_report.compliant,
            "bra_deficit": plan.bra_deficit,
            "field_count": len(plan.delfelt),
            "typology_mix": sorted({b.typology.value for b in plan.bygg}),
            "architecture_score": plan.architecture_report.total_score,
            "frontage_continuity": plan.architecture_report.frontage_continuity,
            "courtyard_clarity": plan.architecture_report.courtyard_clarity,
            "axis_symmetry": plan.architecture_report.axis_symmetry,
            "view_corridor_quality": plan.architecture_report.view_corridor_quality,
        },
        "requirements": {
            "return_json_only": True,
            "keys": ["title", "summary", "architectural_assessment", "recommendation", "risks"],
        },
    }

    raw_data: Optional[Dict[str, Any]] = None
    source = "fallback"
    if ai_reporter is not None:
        try:
            raw_data = ai_reporter(payload)
            if raw_data:
                source = "ai_reporter"
        except Exception as exc:
            logger.warning("pass6 ai_reporter failed: %s", exc)
    if raw_data is None:
        raw_data = _anthropic_json_call(
            system_prompt=(
                "You are pass 6 in a deterministic feasibility engine. "
                "Write concise Norwegian report text based only on provided metrics. "
                "Return JSON only with keys title, summary, architectural_assessment, recommendation, risks."
            ),
            user_payload=payload,
            max_tokens=1200,
        )
        if raw_data:
            source = "anthropic"

    fallback = _fallback_narrative(plan, target_bra_m2)
    if not raw_data:
        return fallback
    title = str(raw_data.get("title") or fallback.title).strip() or fallback.title
    summary = str(raw_data.get("summary") or fallback.summary).strip() or fallback.summary
    arch = str(raw_data.get("architectural_assessment") or fallback.architectural_assessment).strip() or fallback.architectural_assessment
    rec = str(raw_data.get("recommendation") or fallback.recommendation).strip() or fallback.recommendation
    risks_raw = raw_data.get("risks")
    risks = [str(item).strip() for item in risks_raw if str(item).strip()] if isinstance(risks_raw, list) else fallback.risks
    return ReportNarrative(title=title, summary=summary, architectural_assessment=arch, recommendation=rec, risks=risks, source=source)


# ---------------------------------------------------------------------------
# Whole-concept planning
# ---------------------------------------------------------------------------


def _effective_delfelt_count_for_family(
    buildable_poly: Polygon,
    family: ConceptFamily,
    requested_count: Optional[int],
    target_bra_m2: float,
) -> Optional[int]:
    """Treat phase count as a maximum, not a hard delfelt count.

    Runde 8.1: 6 phases should not automatically become 6 tiny fields. That
    destroyed gaterom, grønt fellesrom and karré rings. This maps requested
    phases to concept-appropriate macro fields.
    """
    if requested_count is None or requested_count <= 0:
        return None
    area = max(float(getattr(buildable_poly, "area", 0.0) or 0.0), 1.0)
    density = float(target_bra_m2 or 0.0) / area
    if area <= 2500.0 and density >= 1.85:
        return 1
    req = max(1, int(requested_count))
    if family == ConceptFamily.CLUSTER_PARK:
        return 3 if area >= 7000.0 else max(1, min(req, 2))
    if family == ConceptFamily.COURTYARD_URBAN:
        return max(2, min(req, 3 if density < 1.10 and area < 10000.0 else 4))
    if family == ConceptFamily.LINEAR_MIXED:
        return max(3, min(req, 4))
    return req


def plan_masterplan_geometry(
    buildable_poly: Polygon,
    *,
    concept_family: ConceptFamily = ConceptFamily.LINEAR_MIXED,
    target_bra_m2: float = 0.0,
    plan_regler: Optional[PlanRegler] = None,
    requested_delfelt_count: Optional[int] = None,
    avg_unit_bra_m2: float = 55.0,
    barnehage_config: Optional[BarnehageConfig] = None,
    latitude_deg: float = 63.42,
    longitude_deg: float = 10.43,
    neighbor_buildings: Optional[Sequence[dict]] = None,
    solar_year: Optional[int] = None,
    parkering_areal: float = 0.0,
    vei_areal: float = 0.0,
    site_area_m2: Optional[float] = None,
    ai_selector: Optional[StructuredPassClient] = None,
    ai_reporter: Optional[StructuredPassClient] = None,
) -> Masterplan:
    if buildable_poly is None or buildable_poly.is_empty:
        raise ValueError("buildable_poly mangler eller er tom")
    rules = plan_regler or PlanRegler()

    base_fields = pass1_generate_delfelt(buildable_poly, concept_family, target_bra_m2, requested_delfelt_count)
    configured_fields, pass2_source = pass2_select_field_parameters(
        concept_family,
        base_fields,
        target_bra_m2,
        plan_regler=rules,
        neighbors=neighbor_buildings,
        ai_selector=ai_selector,
    )

    buildings, bra_deficit = pass3_place_buildings(buildable_poly, configured_fields, rules, barnehage_config)
    total_bra = sum(b.bra_m2 for b in buildings)
    total_bya = sum(b.footprint_m2 for b in buildings)
    units = int(round(total_bra / avg_unit_bra_m2)) if avg_unit_bra_m2 > 0 else 0
    sol_report = pass4_calculate_solar(
        buildings=buildings,
        buildable_poly=buildable_poly,
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        neighbors=neighbor_buildings,
        year=solar_year,
        rules=rules,
    )
    plan = Masterplan(
        concept_family=concept_family,
        delfelt=list(configured_fields),
        bygg=list(buildings),
        sol_report=sol_report,
        mua_report=MUAReport(),
        total_bra_m2=float(total_bra),
        total_bya_m2=float(total_bya),
        antall_boliger=max(0, units),
        display_title=_FALLBACK_TITLES[concept_family],
        display_subtitle=_FALLBACK_SUBTITLES[concept_family],
        buildable_polygon=buildable_poly.buffer(0),
        site_area_m2=float(site_area_m2 or buildable_poly.area),
        parkering_areal=float(parkering_areal),
        vei_areal=float(vei_areal),
        plan_regler=rules,
        barnehage_config=barnehage_config or BarnehageConfig(),
        bra_deficit=float(bra_deficit),
        latitude_deg=float(latitude_deg),
        longitude_deg=float(longitude_deg),
        pass2_source=pass2_source,
    )
    plan.mua_report = pass5_calculate_mua(plan, rules)
    plan.skeleton_summaries = build_field_skeleton_summaries(plan.delfelt, rules)
    plan.architecture_report = compute_architecture_metrics(plan)
    narrative = pass6_generate_report_text(plan, target_bra_m2, ai_reporter=ai_reporter)
    plan.display_title = narrative.title
    plan.display_subtitle = _FALLBACK_SUBTITLES[concept_family]
    plan.report_summary = narrative.summary
    plan.report_architectural_assessment = narrative.architectural_assessment
    plan.report_recommendation = narrative.recommendation
    plan.report_risks = list(narrative.risks)
    plan.pass6_source = narrative.source
    return plan


def generate_concept_masterplans(
    buildable_poly: Polygon,
    *,
    target_bra_m2: float,
    plan_regler: Optional[PlanRegler] = None,
    requested_delfelt_count: Optional[int] = None,
    avg_unit_bra_m2: float = 55.0,
    barnehage_config: Optional[BarnehageConfig] = None,
    latitude_deg: float = 63.42,
    longitude_deg: float = 10.43,
    neighbor_buildings: Optional[Sequence[dict]] = None,
    solar_year: Optional[int] = None,
    parkering_areal: float = 0.0,
    vei_areal: float = 0.0,
    site_area_m2: Optional[float] = None,
    ai_selector: Optional[StructuredPassClient] = None,
    ai_reporter: Optional[StructuredPassClient] = None,
) -> List[Masterplan]:
    plans: List[Masterplan] = []
    for family in all_concept_families():
        plans.append(
            plan_masterplan_geometry(
                buildable_poly,
                concept_family=family,
                target_bra_m2=target_bra_m2,
                plan_regler=plan_regler,
                requested_delfelt_count=_effective_delfelt_count_for_family(
                    buildable_poly,
                    family,
                    requested_delfelt_count,
                    target_bra_m2,
                ),
                avg_unit_bra_m2=avg_unit_bra_m2,
                barnehage_config=barnehage_config,
                latitude_deg=latitude_deg,
                longitude_deg=longitude_deg,
                neighbor_buildings=neighbor_buildings,
                solar_year=solar_year,
                parkering_areal=parkering_areal,
                vei_areal=vei_areal,
                site_area_m2=site_area_m2,
                ai_selector=ai_selector,
                ai_reporter=ai_reporter,
            )
        )
    return plans


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_masterplan_geometry(plan: Masterplan, buildable_poly: Polygon) -> List[str]:
    errors: List[str] = []
    if not buildings_do_not_overlap(plan.bygg):
        errors.append("Bygg overlapper hverandre.")
    for field in plan.delfelt:
        for building in [b for b in plan.bygg if b.delfelt_id == field.field_id]:
            if not buildable_poly.buffer(1e-6).covers(building.footprint):
                errors.append(f"{building.bygg_id} ligger utenfor buildable_poly.")
            if not field.polygon.buffer(1e-6).covers(building.footprint):
                errors.append(f"{building.bygg_id} ligger utenfor delfelt {field.field_id}.")
            if not building_geometry_is_orthogonal_to_field(building, field):
                errors.append(f"{building.bygg_id} bryter ortogonalitet i {field.field_id}.")
    return errors
