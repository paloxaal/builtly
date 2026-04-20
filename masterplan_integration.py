from __future__ import annotations

"""Concept-level integration for Builtly v8 delivery 4.

OptionResult is now a *whole concept alternative*, not a building phase.
This module intentionally does not split masterplans by phase.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from shapely.geometry import Polygon

from .masterplan_engine import generate_concept_masterplans, validate_masterplan_geometry
from .masterplan_types import BarnehageConfig, ConceptFamily, Masterplan, PlanRegler, StructuredPassClient, Typology


@dataclass
class OptionResult:
    option_id: str
    concept_family: ConceptFamily
    title: str
    subtitle: str
    score: float
    total_bra_m2: float
    total_bya_m2: float
    antall_boliger: int
    sol_score: float
    mua_status: str
    typology_mix: List[str] = field(default_factory=list)
    field_count: int = 0
    phase_count: int = 0
    summary: str = ""
    recommendation: str = ""
    risks: List[str] = field(default_factory=list)
    masterplan: Optional[Masterplan] = None


def _mua_score(plan: Masterplan) -> float:
    checks = plan.mua_report.checks
    if not checks:
        return 60.0
    relevant = [c for c in checks if c.status.value != "IKKE VURDERT"]
    if not relevant:
        return 60.0
    passed = sum(1 for c in relevant if c.status.value == "JA")
    return 100.0 * passed / max(len(relevant), 1)


def _geometry_score(plan: Masterplan) -> float:
    errors = validate_masterplan_geometry(plan, plan.buildable_polygon or Polygon())
    if not errors:
        return 100.0
    base = max(0.0, 100.0 - 15.0 * len(errors))
    return base


def score_masterplan(plan: Masterplan, target_bra_m2: float) -> float:
    fit = max(0.0, 100.0 - (abs(plan.total_bra_m2 - target_bra_m2) / max(target_bra_m2, 1.0)) * 100.0)
    sol = float(plan.sol_report.total_sol_score)
    mua = _mua_score(plan)
    geo = _geometry_score(plan)
    variety_bonus = 5.0 if len({b.typology for b in plan.bygg}) >= 2 else 0.0
    score = 0.35 * fit + 0.25 * sol + 0.20 * mua + 0.20 * geo + variety_bonus
    return max(0.0, min(100.0, score))


def masterplan_to_option_result(plan: Masterplan, target_bra_m2: float) -> OptionResult:
    score = score_masterplan(plan, target_bra_m2)
    plan.score = score
    typology_mix = [typ.value for typ in sorted({b.typology for b in plan.bygg}, key=lambda t: t.value)]
    unique_phases = sorted({field.phase for field in plan.delfelt})
    mua_status = "JA" if plan.mua_report.compliant else ("IKKE VURDERT" if not plan.mua_report.checks else "NEI")
    return OptionResult(
        option_id=plan.concept_family.value,
        concept_family=plan.concept_family,
        title=plan.display_title or plan.concept_family.value,
        subtitle=plan.display_subtitle,
        score=score,
        total_bra_m2=plan.total_bra_m2,
        total_bya_m2=plan.total_bya_m2,
        antall_boliger=plan.antall_boliger,
        sol_score=plan.sol_report.total_sol_score,
        mua_status=mua_status,
        typology_mix=typology_mix,
        field_count=len(plan.delfelt),
        phase_count=len(unique_phases),
        summary=plan.report_summary,
        recommendation=plan.report_recommendation,
        risks=list(plan.report_risks),
        masterplan=plan,
    )


def masterplans_to_option_results(plans: Sequence[Masterplan], target_bra_m2: float) -> List[OptionResult]:
    options = [masterplan_to_option_result(plan, target_bra_m2) for plan in plans]
    return sorted(options, key=lambda opt: (-opt.score, opt.title))


def run_concept_options(
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
) -> List[OptionResult]:
    plans = generate_concept_masterplans(
        buildable_poly,
        target_bra_m2=target_bra_m2,
        plan_regler=plan_regler,
        requested_delfelt_count=requested_delfelt_count,
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
    return masterplans_to_option_results(plans, target_bra_m2)
