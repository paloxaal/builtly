
from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable, List

from .masterplan_types import Bygg, PlanRegler, SolMetrics, SolReport


def evaluate_solar_report(bygg: Iterable[Bygg], regler: PlanRegler | None = None) -> SolReport:
    buildings = list(bygg)
    by_results: List[SolMetrics] = []
    total_weighted = 0.0
    total_area = 0.0
    for b in buildings:
        compactness_penalty = max(0.0, min(20.0, (b.footprint.length ** 2 / max(b.footprint.area, 1.0)) - 16.0))
        base = 82.0 if b.typology.value in {"Lamell", "Karré"} else 78.0
        floors_penalty = max(0, b.floors - 5) * 3.5
        score = max(25.0, min(98.0, base - floors_penalty - compactness_penalty))
        hours = round(max(2.5, min(7.0, 3.5 + (score - 50.0) / 18.0)), 1)
        by_results.append(
            SolMetrics(
                bygg_id=b.bygg_id,
                sol_score=round(score, 1),
                soltimer_varjevndogn=hours,
                tek17_mua_compliant=(regler.sol_krav_timer_varjevndogn is None or hours >= regler.sol_krav_timer_varjevndogn) if regler else None,
            )
        )
        total_weighted += score * b.bra_m2
        total_area += b.bra_m2
    total_score = round(total_weighted / total_area, 1) if total_area else 0.0
    solar_hours_equinoct = round(3.8 + max(0.0, total_score - 50.0) / 25.0, 1) if total_area else 0.0
    winter_hours = round(max(1.5, solar_hours_equinoct - 1.8), 1) if total_area else 0.0
    summer_shadow = round(max(0.0, 40.0 - total_score / 2.0), 1)
    winter_shadow = round(max(5.0, 55.0 - total_score / 1.8), 1)
    return SolReport(
        total_score=total_score,
        solar_hours_equinoct=solar_hours_equinoct,
        winter_hours=winter_hours,
        summer_shadow_m=summer_shadow,
        winter_shadow_m=winter_shadow,
        by_building=by_results,
        diagnostics={"method": "deterministic-heuristic", "evaluated_at": datetime.utcnow().isoformat()},
    )
