from __future__ import annotations

"""Deterministic MUA / open-space calculations for Builtly v8 delivery 3."""

from typing import List, Optional

from shapely.geometry import Polygon
from shapely.ops import unary_union

from .masterplan_types import ComplianceCheck, ComplianceState, MUAReport, Masterplan, PlanRegler


def _status_for(actual: Optional[float], required: Optional[float], *, tol: float = 1e-6) -> ComplianceState:
    if actual is None or required is None:
        return ComplianceState.IKKE_VURDERT
    return ComplianceState.JA if actual + tol >= required else ComplianceState.NEI


def _resolve_buildable_polygon(plan: Masterplan, buildable_poly: Optional[Polygon] = None) -> Optional[Polygon]:
    if buildable_poly is not None and not buildable_poly.is_empty:
        return buildable_poly.buffer(0)
    if plan.buildable_polygon is not None and not plan.buildable_polygon.is_empty:
        return plan.buildable_polygon.buffer(0)
    if plan.delfelt:
        return unary_union([field.polygon for field in plan.delfelt]).buffer(0)
    return None


def evaluate_compliance(
    *,
    total: float,
    krav_total: Optional[float],
    bakke: float,
    krav_bakke: Optional[float],
    fellesareal: float,
    krav_felles: Optional[float],
    mua_sol_timer_actual: Optional[float],
    mua_sol_timer_required: Optional[float],
) -> List[ComplianceCheck]:
    checks: List[ComplianceCheck] = [
        ComplianceCheck(
            rule_key="mua_total",
            status=_status_for(total, krav_total),
            actual_value=total,
            required_value=krav_total,
            unit="m²",
            note=("Total uteoppholdsareal." if krav_total is not None else "Ikke vurdert fordi mua_per_bolig_m2 ikke er satt."),
        ),
        ComplianceCheck(
            rule_key="mua_felles",
            status=_status_for(fellesareal, krav_felles),
            actual_value=fellesareal,
            required_value=krav_felles,
            unit="m²",
            note=("Felles bakke- og takareal." if krav_felles is not None else "Ikke vurdert fordi mua_min_felles_pct ikke er satt."),
        ),
        ComplianceCheck(
            rule_key="mua_bakke",
            status=_status_for(bakke, krav_bakke),
            actual_value=bakke,
            required_value=krav_bakke,
            unit="m²",
            note=("Felles areal på bakkeplan." if krav_bakke is not None else "Ikke vurdert fordi mua_min_bakke_pct ikke er satt."),
        ),
    ]

    if mua_sol_timer_actual is not None or mua_sol_timer_required is not None:
        checks.append(
            ComplianceCheck(
                rule_key="mua_sol_timer",
                status=_status_for(mua_sol_timer_actual, mua_sol_timer_required),
                actual_value=mua_sol_timer_actual,
                required_value=mua_sol_timer_required,
                unit="timer",
                note=("Vårjevndøgn, gjennomsnittlige soltimer på MUA." if mua_sol_timer_required is not None else "Ikke vurdert fordi solkrav for MUA ikke er satt."),
            )
        )
    return checks


def calculate_mua(
    plan: Masterplan,
    regler: Optional[PlanRegler] = None,
    buildable_poly: Optional[Polygon] = None,
    parkering_areal: Optional[float] = None,
    vei_areal: Optional[float] = None,
    privat_mua_m2: Optional[float] = None,
    sol_report=None,
) -> MUAReport:
    rules = regler or plan.plan_regler or PlanRegler()
    resolved_buildable = _resolve_buildable_polygon(plan, buildable_poly=buildable_poly)

    if resolved_buildable is None:
        return MUAReport(
            checks=[
                ComplianceCheck(
                    rule_key="mua_geometry",
                    status=ComplianceState.IKKE_VURDERT,
                    note="Manglende buildable polygon; MUA kan ikke beregnes.",
                )
            ]
        )

    built_union = unary_union([building.footprint for building in plan.bygg]) if plan.bygg else None
    built_area = float(built_union.area) if built_union is not None else 0.0
    parking_area = float(plan.parkering_areal if parkering_areal is None else parkering_areal)
    road_area = float(plan.vei_areal if vei_areal is None else vei_areal)
    bakke_mua = max(0.0, float(resolved_buildable.area) - built_area - parking_area - road_area)
    tak_mua = sum(building.tak_mua_m2 for building in plan.bygg)
    privat_mua = float(sum(max(0.0, float(building.privat_mua_m2)) for building in plan.bygg))
    if privat_mua_m2 is not None:
        privat_mua = max(privat_mua, float(privat_mua_m2))

    krav_total = None
    krav_felles = None
    krav_bakke = None

    if rules.mua_per_bolig_m2 is not None:
        krav_total = float(plan.antall_boliger) * float(rules.mua_per_bolig_m2)
        if rules.mua_min_felles_pct is not None:
            krav_felles = krav_total * (float(rules.mua_min_felles_pct) / 100.0)
            if rules.mua_min_bakke_pct is not None:
                krav_bakke = krav_felles * (float(rules.mua_min_bakke_pct) / 100.0)

    fellesareal = bakke_mua + tak_mua
    total = fellesareal + privat_mua
    req_sol = rules.sol_krav_timer_varjevndogn if rules.sol_krav_timer_varjevndogn is not None else rules.mua_min_sol_timer
    effective_sol_report = sol_report if sol_report is not None else plan.sol_report
    actual_sol = effective_sol_report.mua_soltimer_varjevndogn if effective_sol_report else None

    checks = evaluate_compliance(
        total=total,
        krav_total=krav_total,
        bakke=bakke_mua,
        krav_bakke=krav_bakke,
        fellesareal=fellesareal,
        krav_felles=krav_felles,
        mua_sol_timer_actual=actual_sol,
        mua_sol_timer_required=req_sol,
    )

    return MUAReport(
        total=total,
        krav_total=krav_total,
        bakke=bakke_mua,
        krav_bakke=krav_bakke,
        tak=tak_mua,
        fellesareal=fellesareal,
        krav_fellesareal=krav_felles,
        privat=privat_mua,
        open_ground_area=max(0.0, float(resolved_buildable.area) - built_area),
        checks=checks,
    )
