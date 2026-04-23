from __future__ import annotations

"""Concept-level integration for Builtly.

OptionResult is a whole-site concept alternative, not a building phase.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

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
    architecture_score: float = 0.0
    public_realm_score: float = 0.0
    rhythm_score: float = 0.0
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
    return max(0.0, 100.0 - 15.0 * len(errors))


def _bya_score(plan: Masterplan) -> float:
    site_area = float(plan.site_area_m2 or 0.0)
    if site_area <= 0:
        return 50.0
    bya_pct = 100.0 * float(plan.total_bya_m2) / site_area
    if plan.concept_family == ConceptFamily.COURTYARD_URBAN:
        lo, hi = 20.0, 32.0
    elif plan.concept_family == ConceptFamily.LINEAR_MIXED:
        lo, hi = 18.0, 28.0
    else:
        lo, hi = 14.0, 24.0
    if lo <= bya_pct <= hi:
        return 100.0
    if bya_pct < lo:
        return max(0.0, 100.0 - (lo - bya_pct) * 6.0)
    return max(0.0, 100.0 - (bya_pct - hi) * 4.0)


def _dominance_score(plan: Masterplan) -> float:
    if not plan.bygg:
        return 0.0
    counts = {}
    for b in plan.bygg:
        counts[b.typology] = counts.get(b.typology, 0) + 1
    total = sum(counts.values()) or 1
    shares = {k: v / total for k, v in counts.items()}
    if plan.concept_family == ConceptFamily.COURTYARD_URBAN:
        return min(100.0, 100.0 * shares.get(Typology.KARRE, 0.0) / 0.60)
    if plan.concept_family == ConceptFamily.LINEAR_MIXED:
        lam = shares.get(Typology.LAMELL, 0.0)
        punk = shares.get(Typology.PUNKTHUS, 0.0)
        val = 100.0 * (0.75 * min(1.0, lam / 0.70) + 0.25 * (1.0 - max(0.0, punk - 0.30)))
        return max(0.0, min(100.0, val))
    lam = shares.get(Typology.LAMELL, 0.0)
    punk = shares.get(Typology.PUNKTHUS, 0.0)
    mix = min(1.0, lam / 0.45) * 0.5 + min(1.0, punk / 0.20) * 0.5
    other_pen = 1.0 - sum(v for k, v in shares.items() if k in {Typology.LAMELL, Typology.PUNKTHUS})
    return max(0.0, min(100.0, 100.0 * mix * (1.0 - other_pen)))


def score_masterplan(plan: Masterplan, target_bra_m2: float) -> float:
    fit = max(0.0, 100.0 - (abs(plan.total_bra_m2 - target_bra_m2) / max(target_bra_m2, 1.0)) * 100.0)
    sol = float(plan.sol_report.total_sol_score)
    mua = _mua_score(plan)
    geo = _geometry_score(plan)
    bya = _bya_score(plan)
    dominance = _dominance_score(plan)
    arch = float(plan.architecture_report.total_score)
    score = 0.20 * fit + 0.12 * sol + 0.12 * mua + 0.08 * geo + 0.12 * bya + 0.10 * dominance + 0.26 * arch
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
        architecture_score=plan.architecture_report.total_score,
        public_realm_score=plan.architecture_report.public_realm_clarity,
        rhythm_score=plan.architecture_report.rhythm_score,
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
