from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

from shapely import affinity
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .masterplan_types import SolBuildingResult, SolKeyMoment, SolReport, Typology


def _axis_angle_deg(poly: Polygon) -> float:
    try:
        rect = poly.minimum_rotated_rectangle
        coords = list(rect.exterior.coords)
        if len(coords) < 3:
            return 0.0
        edges = []
        for a, b in zip(coords, coords[1:]):
            dx = b[0] - a[0]
            dy = b[1] - a[1]
            length = math.hypot(dx, dy)
            edges.append((length, dx, dy))
        _, dx, dy = max(edges, key=lambda item: item[0])
        return math.degrees(math.atan2(dy, dx)) % 180.0
    except Exception:
        return 0.0


def _orientation_bonus(poly: Polygon, typology: Typology | str) -> float:
    typ = typology.value if hasattr(typology, "value") else str(typology)
    if typ in {"Karré", "Punkthus"}:
        return 0.72
    angle = _axis_angle_deg(poly)
    east_west_distance = min(angle, 180.0 - angle)
    return max(0.15, 1.0 - east_west_distance / 90.0)


def _translated_shadow(footprint: Polygon, height_m: float, azimuth_deg: float, altitude_deg: float) -> Optional[Polygon]:
    if footprint is None or footprint.is_empty or height_m <= 0.1 or altitude_deg <= 0.1:
        return None
    length = height_m / max(math.tan(math.radians(altitude_deg)), 0.12)
    dx = math.sin(math.radians(azimuth_deg)) * length
    dy = math.cos(math.radians(azimuth_deg)) * length
    moved = affinity.translate(footprint, xoff=dx, yoff=dy)
    return unary_union([footprint, moved]).convex_hull.buffer(0)


def _blocking_penalty(subject, other_items: Sequence[Tuple[Polygon, float]], direction_deg: float = 180.0) -> float:
    fp = getattr(subject, "footprint", None)
    if fp is None or fp.is_empty:
        return 0.0
    sc = fp.centroid
    rad = math.radians(direction_deg)
    dir_x = math.sin(rad)
    dir_y = math.cos(rad)
    penalty = 0.0
    lateral_dir = (-dir_y, dir_x)
    for other_poly, other_h in other_items:
        if other_poly is None or other_poly.is_empty:
            continue
        oc = other_poly.centroid
        vx = oc.x - sc.x
        vy = oc.y - sc.y
        forward = vx * dir_x + vy * dir_y
        lateral = abs(vx * lateral_dir[0] + vy * lateral_dir[1])
        if forward < 0:
            continue
        influence = max(12.0, float(other_h) * 2.1)
        lateral_limit = max(10.0, float(other_h) * 0.8)
        if forward <= influence and lateral <= lateral_limit:
            penalty += max(0.0, (1.0 - forward / influence)) * 22.0
    return penalty


def _neighbor_polys(neighbors: Optional[Sequence[dict]]) -> List[Tuple[Polygon, float]]:
    out: List[Tuple[Polygon, float]] = []
    for nb in neighbors or []:
        poly = nb.get("polygon")
        if poly is None:
            coords_groups = nb.get("coords") or nb.get("polygon_coords") or []
            try:
                if coords_groups and isinstance(coords_groups[0], list) and coords_groups[0] and isinstance(coords_groups[0][0], (list, tuple)):
                    ring = coords_groups[0]
                else:
                    ring = coords_groups
                if ring and len(ring) >= 3:
                    poly = Polygon([(float(x), float(y)) for x, y in ring])
            except Exception:
                poly = None
        if poly is None or poly.is_empty:
            continue
        out.append((poly.buffer(0), float(nb.get("height_m", 9.0) or 9.0)))
    return out


def compute_sol_report(buildable_poly, buildings, latitude_deg=63.42, longitude_deg=10.43, neighbors=None, rules=None, year=2026):
    del longitude_deg, year, rules
    bygg_list = list(buildings or [])
    neighbor_items = _neighbor_polys(neighbors)
    per_building: List[SolBuildingResult] = []
    # Midday spring equinox-ish assumption for Norway.
    alt_eqx = max(18.0, 28.0 - abs(float(latitude_deg) - 59.0) * 1.2)
    alt_winter = max(8.0, alt_eqx * 0.45)
    alt_summer = min(45.0, alt_eqx * 1.45)
    other_pairs: List[Tuple[Polygon, float]] = []
    for b in bygg_list:
        fp = getattr(b, "footprint", None)
        if fp is not None and not fp.is_empty:
            other_pairs.append((fp.buffer(0), float(getattr(b, "height_m", 0.0) or 0.0)))

    for b in bygg_list:
        fp = getattr(b, "footprint", None)
        if fp is None or fp.is_empty:
            per_building.append(SolBuildingResult(bygg_id=getattr(b, "bygg_id", ""), sol_score=0.0))
            continue
        own_typ = getattr(b, "typology", "")
        orient = _orientation_bonus(fp, own_typ)
        remaining = [(poly, h) for poly, h in other_pairs if not poly.equals(fp)]
        penalty_from_buildings = _blocking_penalty(b, remaining, direction_deg=180.0)
        penalty_from_neighbors = _blocking_penalty(b, neighbor_items, direction_deg=180.0) * 0.8
        score = 58.0 + orient * 24.0 - penalty_from_buildings - penalty_from_neighbors
        if getattr(b, "floors", 1) and int(getattr(b, "floors", 1)) >= 7:
            score -= 3.0
        score = max(28.0, min(94.0, score))
        sun_hours = 1.4 + 3.8 * (score / 100.0)
        facade_fraction = max(0.18, min(0.92, 0.28 + orient * 0.52 - (penalty_from_buildings + penalty_from_neighbors) / 140.0))
        per_building.append(
            SolBuildingResult(
                bygg_id=getattr(b, "bygg_id", ""),
                sol_score=score,
                soltimer_varjevndogn=sun_hours,
                tek17_mua_compliant=sun_hours >= 2.5,
                facade_sun_fraction=facade_fraction,
                possible_samples=16,
                sunlit_samples=int(round(16 * facade_fraction)),
                facade_results={"south": facade_fraction, "east": max(0.0, facade_fraction - 0.12), "west": max(0.0, facade_fraction - 0.10)},
            )
        )

    shadow_polys_eqx = [
        _translated_shadow(getattr(b, "footprint", None), float(getattr(b, "height_m", 0.0) or 0.0), 0.0, alt_eqx)
        for b in bygg_list
    ]
    shadow_polys_eqx = [p for p in shadow_polys_eqx if p is not None and not p.is_empty]
    if buildable_poly is not None and not getattr(buildable_poly, "is_empty", True):
        buildable = buildable_poly.buffer(0)
        footprint_union = unary_union([getattr(b, "footprint", None) for b in bygg_list if getattr(b, "footprint", None) is not None]).buffer(0) if bygg_list else None
        ground_area = max(1.0, float(buildable.area) - float(getattr(footprint_union, "area", 0.0) or 0.0))
        shadow_union_eqx = unary_union(shadow_polys_eqx).intersection(buildable).buffer(0) if shadow_polys_eqx else None
        shadow_ground_eqx = max(0.0, float(getattr(shadow_union_eqx, "area", 0.0) or 0.0) - float(getattr(footprint_union, "area", 0.0) or 0.0))
        solbelyst_ute_pct = max(0.0, min(100.0, 100.0 * (1.0 - shadow_ground_eqx / ground_area)))
    else:
        solbelyst_ute_pct = 60.0

    avg_building_score = sum(item.sol_score for item in per_building) / max(len(per_building), 1)
    total_sol_score = max(0.0, min(100.0, 0.72 * avg_building_score + 0.28 * solbelyst_ute_pct))
    project_soltimer = 1.6 + 3.6 * (total_sol_score / 100.0)
    rep_shadow_eqx = max((float(getattr(b, "height_m", 0.0) or 0.0) / max(math.tan(math.radians(alt_eqx)), 0.12) for b in bygg_list), default=0.0)
    rep_shadow_winter = max((float(getattr(b, "height_m", 0.0) or 0.0) / max(math.tan(math.radians(alt_winter)), 0.10) for b in bygg_list), default=0.0)
    rep_shadow_summer = max((float(getattr(b, "height_m", 0.0) or 0.0) / max(math.tan(math.radians(alt_summer)), 0.20) for b in bygg_list), default=0.0)

    notes = [
        "Deterministisk solheuristikk basert på orientering, blokkering mot sør og skyggeutslag på terreng.",
        f"Latitude {float(latitude_deg):.2f}° gir vårjevndøgn-vinkel {alt_eqx:.1f}° i modellen.",
    ]
    return SolReport(
        per_building=per_building,
        total_sol_score=total_sol_score,
        project_soltimer_varjevndogn=project_soltimer,
        mua_soltimer_varjevndogn=project_soltimer * 0.92,
        mua_sun_compliant=project_soltimer >= 2.5,
        solbelyst_uteareal_pct=solbelyst_ute_pct,
        vinter_skygge_kl_12_m=rep_shadow_winter,
        sommerskygge_kl_15_m=rep_shadow_summer,
        key_moments=[
            SolKeyMoment("21. mars kl. 12", alt_eqx, 180.0, rep_shadow_eqx),
            SolKeyMoment("21. desember kl. 12", alt_winter, 180.0, rep_shadow_winter),
            SolKeyMoment("21. juni kl. 15", alt_summer, 235.0, rep_shadow_summer),
        ],
        analysis_samples=max(1, len(per_building) * 16),
        notes=notes,
    )
