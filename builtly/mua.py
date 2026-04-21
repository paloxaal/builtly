
from __future__ import annotations

from typing import Iterable, List

from shapely.ops import unary_union

from .masterplan_types import Bygg, ComplianceItem, Masterplan, MUAReport, PlanRegler


def _status(required: float | None, actual: float | None) -> str:
    if required is None:
        return "IKKE VURDERT"
    if actual is None:
        return "NEI"
    return "JA" if actual >= required else "NEI"


def calculate_mua(
    buildable_poly,
    buildings: Iterable[Bygg],
    regler: PlanRegler,
    antall_boliger: int,
    parkering_areal: float = 0.0,
    vei_areal: float = 0.0,
) -> MUAReport:
    buildings = list(buildings)
    footprints = [b.footprint for b in buildings]
    bebygd = unary_union(footprints).area if footprints else 0.0
    bakke_mua = max(0.0, float(buildable_poly.area) - float(bebygd) - parkering_areal - vei_areal)
    tak_mua = round(sum(b.footprint.area * 0.30 for b in buildings if b.floors >= 3), 1)
    privat_mua = 0.0

    krav_total = regler.mua_per_bolig_m2 * antall_boliger if regler.mua_per_bolig_m2 is not None else None
    krav_felles = krav_total * (regler.mua_min_felles_pct / 100.0) if (krav_total is not None and regler.mua_min_felles_pct is not None) else None
    krav_bakke = krav_felles * (regler.mua_min_bakke_pct / 100.0) if (krav_felles is not None and regler.mua_min_bakke_pct is not None) else None

    felles = bakke_mua + tak_mua
    total = felles + privat_mua

    compliant: List[ComplianceItem] = [
        ComplianceItem("mua_total", _status(krav_total, total), total, krav_total),
        ComplianceItem("mua_felles", _status(krav_felles, felles), felles, krav_felles),
        ComplianceItem("mua_bakke", _status(krav_bakke, bakke_mua), bakke_mua, krav_bakke),
        ComplianceItem(
            "mua_sol_timer",
            "IKKE VURDERT" if regler.mua_min_sol_timer is None else "NEI",
            None,
            regler.mua_min_sol_timer,
            note="Solkrav på MUA må vurderes mot solrapport i UI.",
        ),
    ]
    return MUAReport(
        total=round(total, 1),
        krav_total=round(krav_total, 1) if krav_total is not None else None,
        bakke=round(bakke_mua, 1),
        krav_bakke=round(krav_bakke, 1) if krav_bakke is not None else None,
        fellesareal=round(felles, 1),
        krav_fellesareal=round(krav_felles, 1) if krav_felles is not None else None,
        privat=round(privat_mua, 1),
        compliant=compliant,
        diagnostics={"bebygd_m2": round(bebygd, 1), "parkering_areal": parkering_areal, "vei_areal": vei_areal},
    )
