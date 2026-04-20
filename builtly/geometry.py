from __future__ import annotations

"""Deterministic geometry for Builtly v8 delivery 2.

Scope of delivery 2:
- Pass 1: site geometry analysis + delfelt subdivision
- Pass 3: deterministic volumetric placement with hard geometric rules

The functions in this module are intentionally independent from AI and from the
legacy masterplan stack. The only external geometric dependency is Shapely.
"""

from dataclasses import dataclass
import math
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np
from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import split as shapely_split, unary_union

from .masterplan_types import Bygg, CourtyardKind, Delfelt, PlanRegler, Typology
from .typology_library import BaseTypologySpec, get_typology_spec

GRID_SNAP_M = 0.5
DEFAULT_HEIGHT_PER_FLOOR_M = {
    Typology.LAMELL: 3.2,
    Typology.PUNKTHUS: 3.1,
    Typology.KARRE: 3.2,
    Typology.REKKEHUS: 2.9,
}


@dataclass(frozen=True)
class SiteAxes:
    theta_deg: float
    major_axis_m: float
    minor_axis_m: float
    centroid_x: float
    centroid_y: float


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------


def snap_value(value: float, grid_m: float = GRID_SNAP_M) -> float:
    return round(float(value) / grid_m) * grid_m


def _rotate(geom, angle_deg: float, origin) -> Any:
    return affinity.rotate(geom, angle_deg, origin=origin, use_radians=False)


def _largest_polygon(geom: Any) -> Optional[Polygon]:
    if geom is None or getattr(geom, "is_empty", True):
        return None
    if isinstance(geom, Polygon):
        return geom.buffer(0)
    if isinstance(geom, MultiPolygon):
        if not geom.geoms:
            return None
        return max((g.buffer(0) for g in geom.geoms if not g.is_empty), key=lambda g: g.area, default=None)
    if isinstance(geom, GeometryCollection):
        polys = [g.buffer(0) for g in geom.geoms if isinstance(g, Polygon) and not g.is_empty]
        if not polys:
            multi_parts = [g for g in geom.geoms if isinstance(g, MultiPolygon)]
            for mp in multi_parts:
                polys.extend([p.buffer(0) for p in mp.geoms if not p.is_empty])
        if polys:
            return max(polys, key=lambda g: g.area)
    return None


def _flatten_polygons(geom: Any) -> List[Polygon]:
    if geom is None or getattr(geom, "is_empty", True):
        return []
    if isinstance(geom, Polygon):
        return [geom.buffer(0)]
    if isinstance(geom, MultiPolygon):
        return [g.buffer(0) for g in geom.geoms if not g.is_empty]
    if isinstance(geom, GeometryCollection):
        parts: List[Polygon] = []
        for g in geom.geoms:
            parts.extend(_flatten_polygons(g))
        return parts
    return []


def _signed_area(coords: Sequence[Tuple[float, float]]) -> float:
    area = 0.0
    pts = list(coords)
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _is_ccw(coords: Sequence[Tuple[float, float]]) -> bool:
    return _signed_area(coords) > 0


def _cross(prev_pt: Tuple[float, float], curr_pt: Tuple[float, float], next_pt: Tuple[float, float]) -> float:
    ax = curr_pt[0] - prev_pt[0]
    ay = curr_pt[1] - prev_pt[1]
    bx = next_pt[0] - curr_pt[0]
    by = next_pt[1] - curr_pt[1]
    return ax * by - ay * bx


# ---------------------------------------------------------------------------
# Pass 1 — site axes and field count
# ---------------------------------------------------------------------------


def pca_site_axes(buildable_poly: Polygon) -> SiteAxes:
    if buildable_poly is None or buildable_poly.is_empty:
        raise ValueError("buildable_poly mangler eller er tomt")

    coords = np.array(list(buildable_poly.exterior.coords)[:-1], dtype=float)
    if len(coords) < 3:
        raise ValueError("buildable_poly trenger minst 3 koordinater")

    centroid = coords.mean(axis=0)
    centered = coords - centroid
    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    vec = eigvecs[:, int(np.argmax(eigvals))]
    theta_deg = math.degrees(math.atan2(float(vec[1]), float(vec[0]))) % 180.0

    # Project points onto principal axis and its perpendicular to get extents.
    perp = np.array([-vec[1], vec[0]], dtype=float)
    major_proj = centered @ vec
    minor_proj = centered @ perp
    major_axis_m = float(np.max(major_proj) - np.min(major_proj))
    minor_axis_m = float(np.max(minor_proj) - np.min(minor_proj))

    # Normalize angle so the major span corresponds to local x-axis in later code.
    if major_axis_m < minor_axis_m:
        theta_deg = (theta_deg + 90.0) % 180.0
        major_axis_m, minor_axis_m = minor_axis_m, major_axis_m

    return SiteAxes(
        theta_deg=theta_deg,
        major_axis_m=major_axis_m,
        minor_axis_m=minor_axis_m,
        centroid_x=float(buildable_poly.centroid.x),
        centroid_y=float(buildable_poly.centroid.y),
    )


def default_delfelt_count(area_m2: float, major_axis_m: float) -> int:
    if area_m2 < 5_000:
        area_n = 1
    elif area_m2 < 12_000:
        area_n = 2
    elif area_m2 < 22_000:
        area_n = 3
    elif area_m2 < 35_000:
        area_n = 4
    elif area_m2 < 55_000:
        area_n = 5
    else:
        area_n = 6

    length_n = max(1, math.ceil(major_axis_m / 80.0))
    return max(area_n, length_n)


def resolve_delfelt_count(buildable_poly: Polygon, requested_count: Optional[int] = None) -> int:
    """Resolve a practical field count.

    The spec's default formula is preserved in `default_delfelt_count()`, but
    actual subdivision moderates the count for very long, narrow sites to avoid
    irrational micro-fields. This is the deterministic hook that makes the
    "8 000 m² × 250 m" edge case land on 2–3 fields instead of 4.
    """
    if requested_count is not None and requested_count > 0:
        return int(requested_count)

    axes = pca_site_axes(buildable_poly)
    area_m2 = float(buildable_poly.area)
    count = default_delfelt_count(area_m2, axes.major_axis_m)
    aspect = axes.major_axis_m / max(axes.minor_axis_m, 1.0)

    area_based = 1
    if area_m2 < 5_000:
        area_based = 1
    elif area_m2 < 12_000:
        area_based = 2
    elif area_m2 < 22_000:
        area_based = 3
    elif area_m2 < 35_000:
        area_based = 4
    elif area_m2 < 55_000:
        area_based = 5
    else:
        area_based = 6

    # Slender plots should not over-fragment beyond one step above area-based.
    if area_m2 < 10_000 and aspect > 6.0:
        count = min(count, area_based + 1)
    # Avoid fields that become too small to carry coherent urban structure.
    min_rational_area = 2_400.0
    while count > 1 and area_m2 / count < min_rational_area:
        count -= 1

    return max(1, int(count))


# ---------------------------------------------------------------------------
# Pass 1 — polygon subdivision
# ---------------------------------------------------------------------------


def convex_hull_ratio(poly: Polygon) -> float:
    if poly.is_empty or poly.convex_hull.area <= 0:
        return 1.0
    return float(poly.area / poly.convex_hull.area)


def _reflex_vertices(local_poly: Polygon) -> List[Tuple[float, float]]:
    coords = list(local_poly.exterior.coords)[:-1]
    if len(coords) < 4:
        return []
    ccw = _is_ccw(coords)
    reflex: List[Tuple[float, float]] = []
    for idx, curr in enumerate(coords):
        prev_pt = coords[idx - 1]
        next_pt = coords[(idx + 1) % len(coords)]
        cross_val = _cross(prev_pt, curr, next_pt)
        if (ccw and cross_val < -1e-6) or ((not ccw) and cross_val > 1e-6):
            reflex.append((float(curr[0]), float(curr[1])))
    return reflex


def _split_polygon_with_line(poly: Polygon, line: LineString) -> List[Polygon]:
    try:
        result = shapely_split(poly, line)
    except Exception:
        return []
    parts = [p.buffer(0) for p in _flatten_polygons(result) if p.area > 1.0]
    if len(parts) < 2:
        return []
    return parts


def _best_knee_split(local_poly: Polygon) -> Optional[List[Polygon]]:
    if local_poly.is_empty:
        return None
    minx, miny, maxx, maxy = local_poly.bounds
    reflex = _reflex_vertices(local_poly)
    if not reflex:
        return None

    best_parts: Optional[List[Polygon]] = None
    best_score = float("inf")
    span_x = maxx - minx
    span_y = maxy - miny

    for vx, vy in reflex:
        candidates = [
            LineString([(vx, miny - span_y - 10.0), (vx, maxy + span_y + 10.0)]),
            LineString([(minx - span_x - 10.0, vy), (maxx + span_x + 10.0, vy)]),
        ]
        for line in candidates:
            parts = _split_polygon_with_line(local_poly, line)
            if len(parts) != 2:
                continue
            areas = sorted([p.area for p in parts])
            if areas[0] < local_poly.area * 0.15:
                continue
            score = abs(areas[1] - areas[0])
            if score < best_score:
                best_score = score
                best_parts = parts
    return best_parts


def _balanced_axis_split(local_poly: Polygon, axis: str = "x") -> List[Polygon]:
    minx, miny, maxx, maxy = local_poly.bounds
    total_area = local_poly.area
    if total_area <= 0:
        return [local_poly]

    if axis == "x":
        lo, hi = minx, maxx
        def left_area(cut: float) -> float:
            slab = box(minx - 10_000.0, miny - 10_000.0, cut, maxy + 10_000.0)
            return float(local_poly.intersection(slab).area)

        target = total_area / 2.0
        for _ in range(50):
            mid = (lo + hi) / 2.0
            if left_area(mid) < target:
                lo = mid
            else:
                hi = mid
        cut = (lo + hi) / 2.0
        line = LineString([(cut, miny - 10_000.0), (cut, maxy + 10_000.0)])
    else:
        lo, hi = miny, maxy
        def bottom_area(cut: float) -> float:
            slab = box(minx - 10_000.0, miny - 10_000.0, maxx + 10_000.0, cut)
            return float(local_poly.intersection(slab).area)

        target = total_area / 2.0
        for _ in range(50):
            mid = (lo + hi) / 2.0
            if bottom_area(mid) < target:
                lo = mid
            else:
                hi = mid
        cut = (lo + hi) / 2.0
        line = LineString([(minx - 10_000.0, cut), (maxx + 10_000.0, cut)])

    parts = _split_polygon_with_line(local_poly, line)
    if len(parts) >= 2:
        return parts[:2]
    return [local_poly]


def subdivide_buildable_polygon(buildable_poly: Polygon, count: int, orientation_deg: float) -> List[Polygon]:
    if count <= 1:
        return [buildable_poly.buffer(0)]

    local_poly = _rotate(buildable_poly, -orientation_deg, origin=buildable_poly.centroid)
    parts: List[Polygon] = [local_poly.buffer(0)]

    if convex_hull_ratio(local_poly) < 0.75:
        while len(parts) < count:
            idx = max(range(len(parts)), key=lambda i: parts[i].area)
            knee_split = _best_knee_split(parts[idx])
            if not knee_split:
                break
            parts = parts[:idx] + knee_split + parts[idx + 1 :]
            if len(parts) >= count:
                break

    while len(parts) < count:
        idx = max(range(len(parts)), key=lambda i: parts[i].area)
        piece = parts[idx]
        pminx, pminy, pmaxx, pmaxy = piece.bounds
        axis = "x" if (pmaxx - pminx) >= (pmaxy - pminy) else "y"
        split_parts = _balanced_axis_split(piece, axis=axis)
        if len(split_parts) < 2:
            break
        parts = parts[:idx] + split_parts + parts[idx + 1 :]

    if len(parts) > count:
        parts = sorted(parts, key=lambda p: p.area, reverse=True)[:count]

    global_parts = [_rotate(part, orientation_deg, origin=buildable_poly.centroid).buffer(0) for part in parts]
    global_parts = [part for part in global_parts if part.area > 1.0]

    # Order fields south-to-north by centroid, as required by the spec.
    global_parts.sort(key=lambda p: (p.centroid.y, p.centroid.x))
    return global_parts


# ---------------------------------------------------------------------------
# Pass 3 — deterministic placement
# ---------------------------------------------------------------------------


def _field_core_polygon(field_polygon: Polygon, brann_avstand_m: float) -> Polygon:
    margin = max(2.0, brann_avstand_m / 2.0)
    core = field_polygon.buffer(-margin)
    candidate = _largest_polygon(core)
    if candidate is not None and candidate.area > 25.0:
        return candidate
    core = field_polygon.buffer(-2.0)
    candidate = _largest_polygon(core)
    if candidate is not None and candidate.area > 25.0:
        return candidate
    return field_polygon.buffer(0)


def _height_for(typology: Typology, floors: int) -> float:
    return float(floors) * DEFAULT_HEIGHT_PER_FLOOR_M[typology]


def _required_spacing(typology: Typology, height_m: float, rules: PlanRegler) -> float:
    spec = get_typology_spec(typology)
    sol_spacing = spec.min_spacing_m * height_m if typology == Typology.LAMELL else spec.min_spacing_m
    return max(float(rules.brann_avstand_m), float(sol_spacing))


def _is_parallel(angle_a: float, angle_b: float, tol: float = 1e-3) -> bool:
    diff = abs(((angle_a - angle_b) + 180.0) % 180.0)
    return diff < tol or abs(diff - 180.0) < tol


def _building_spacing_ok(candidate: Polygon, candidate_angle_deg: float, candidate_typology: Typology, candidate_height_m: float,
                         existing: List[Tuple[Polygon, float, Typology, float]], rules: PlanRegler) -> bool:
    for other_poly, other_angle_deg, other_typology, other_height_m in existing:
        if candidate.intersects(other_poly) or candidate.overlaps(other_poly):
            return False
        if _is_parallel(candidate_angle_deg, other_angle_deg) and candidate_typology == Typology.LAMELL and other_typology == Typology.LAMELL:
            req = max(float(rules.brann_avstand_m), 1.2 * max(candidate_height_m, other_height_m))
        else:
            req = max(float(rules.brann_avstand_m), _required_spacing(candidate_typology, candidate_height_m, rules), _required_spacing(other_typology, other_height_m, rules))
        if candidate.distance(other_poly) + 1e-6 < req:
            return False
    return True


def _make_rect(x0: float, y0: float, width_m: float, depth_m: float) -> Polygon:
    return box(snap_value(x0), snap_value(y0), snap_value(x0 + width_m), snap_value(y0 + depth_m))


def _fit_rect_in_segment(poly: Polygon, y0: float, depth_m: float, min_length: float, max_length: float) -> Optional[Polygon]:
    minx, miny, maxx, maxy = poly.bounds
    strip = box(minx - 1_000.0, y0, maxx + 1_000.0, y0 + depth_m)
    inter = poly.intersection(strip)
    candidates = _flatten_polygons(inter)
    best: Optional[Polygon] = None
    best_len = 0.0
    for piece in candidates:
        px0, py0, px1, py1 = piece.bounds
        avail = px1 - px0
        if avail + 1e-6 < min_length:
            continue
        lo, hi = min_length, min(max_length, avail)
        found: Optional[Polygon] = None
        for _ in range(35):
            mid = snap_value((lo + hi) / 2.0)
            x0 = snap_value((px0 + px1 - mid) / 2.0)
            rect = _make_rect(x0, y0, mid, depth_m)
            if piece.buffer(1e-6).covers(rect):
                found = rect
                lo = mid + GRID_SNAP_M
            else:
                hi = mid - GRID_SNAP_M
        if found is not None and found.area > best_len:
            best = found
            best_len = found.area
    return best


def _centered_offsets(count: int, item_size: float, gap: float, total_span: float) -> Optional[List[float]]:
    occupied = count * item_size + max(0, count - 1) * gap
    if occupied > total_span + 1e-6:
        return None
    start = (total_span - occupied) / 2.0
    return [snap_value(start + i * (item_size + gap)) for i in range(count)]


@dataclass
class _PlacementCandidate:
    footprints: List[Polygon]
    floors: int
    angle_offset_deg: float
    total_bra: float
    total_footprint: float


def _evaluate_candidate(footprints: List[Polygon], target_bra: float, floors_range: Tuple[int, int]) -> Optional[_PlacementCandidate]:
    if not footprints:
        return None
    total_fp = sum(p.area for p in footprints)
    if total_fp <= 0:
        return None
    floors = int(round(target_bra / total_fp)) if target_bra > 0 else floors_range[0]
    floors = max(floors_range[0], min(floors_range[1], floors))
    return _PlacementCandidate(
        footprints=footprints,
        floors=floors,
        angle_offset_deg=0.0,
        total_bra=total_fp * floors,
        total_footprint=total_fp,
    )


def _choose_best(candidates: List[_PlacementCandidate], target_bra: float) -> Optional[_PlacementCandidate]:
    if not candidates:
        return None
    def score(item: _PlacementCandidate) -> Tuple[float, float, int]:
        deficit = max(0.0, target_bra - item.total_bra)
        overshoot = max(0.0, item.total_bra - target_bra)
        return (deficit + overshoot * 0.25, -item.total_bra, len(item.footprints))
    return min(candidates, key=score)


def _place_lameller_local(core: Polygon, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    candidates: List[_PlacementCandidate] = []
    for angle_offset in (0.0, 90.0):
        local_poly = core if angle_offset == 0.0 else _rotate(core, -90.0, origin=core.centroid)
        minx, miny, maxx, maxy = local_poly.bounds
        width = maxx - minx
        height = maxy - miny
        depth = spec.depth_m.midpoint() if spec.depth_m else 13.0
        spacing = max(8.0, spec.min_spacing_m * _height_for(Typology.LAMELL, field.floors_max))
        max_rows = max(1, min(4, int((height + spacing) // (depth + spacing))))
        for rows in range(1, max_rows + 1):
            offsets = _centered_offsets(rows, depth, spacing, height)
            if offsets is None:
                continue
            footprints: List[Polygon] = []
            valid = True
            for off in offsets:
                y0 = miny + off
                rect = _fit_rect_in_segment(local_poly, y0, depth, spec.length_m.min_m, spec.length_m.max_m)
                if rect is None:
                    valid = False
                    break
                footprints.append(rect)
            if not valid:
                continue
            if angle_offset == 90.0:
                footprints = [_rotate(p, 90.0, origin=core.centroid) for p in footprints]
            cand = _evaluate_candidate(footprints, field.target_bra, (field.floors_min, field.floors_max))
            if cand:
                cand.angle_offset_deg = angle_offset
                candidates.append(cand)
    return _choose_best(candidates, field.target_bra)


def _place_punkthus_local(core: Polygon, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    candidates: List[_PlacementCandidate] = []
    sizes = [field.tower_size_m] if field.tower_size_m else list(spec.allowed_tower_sizes_m)
    minx, miny, maxx, maxy = core.bounds
    width = maxx - minx
    height = maxy - miny
    for size in sizes:
        spacing = max(float(spec.min_spacing_m), float(field.floors_max) * 1.5)
        max_cols = max(1, min(3, int((width + spacing) // (size + spacing))))
        max_rows = max(1, min(3, int((height + spacing) // (size + spacing))))
        for cols in range(1, max_cols + 1):
            for rows in range(1, max_rows + 1):
                x_offsets = _centered_offsets(cols, float(size), spacing, width)
                y_offsets = _centered_offsets(rows, float(size), spacing, height)
                if x_offsets is None or y_offsets is None:
                    continue
                footprints: List[Polygon] = []
                valid = True
                for xoff in x_offsets:
                    for yoff in y_offsets:
                        rect = _make_rect(minx + xoff, miny + yoff, float(size), float(size))
                        if not core.buffer(1e-6).covers(rect):
                            valid = False
                            break
                        footprints.append(rect)
                    if not valid:
                        break
                if not valid:
                    continue
                cand = _evaluate_candidate(footprints, field.target_bra, (field.floors_min, field.floors_max))
                if cand:
                    candidates.append(cand)
    return _choose_best(candidates, field.target_bra)


def _place_rekkehus_local(core: Polygon, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    candidates: List[_PlacementCandidate] = []
    depth = spec.depth_m.midpoint() if spec.depth_m else 8.0
    unit_length = spec.length_m.midpoint() if spec.length_m else 6.0
    spacing = max(float(spec.min_spacing_m), 8.0)
    for angle_offset in (0.0, 90.0):
        local_poly = core if angle_offset == 0.0 else _rotate(core, -90.0, origin=core.centroid)
        minx, miny, maxx, maxy = local_poly.bounds
        width = maxx - minx
        height = maxy - miny
        max_rows = max(1, min(3, int((height + spacing) // (depth + spacing))))
        for rows in range(1, max_rows + 1):
            row_offsets = _centered_offsets(rows, depth, spacing, height)
            if row_offsets is None:
                continue
            footprints: List[Polygon] = []
            valid = True
            for row_off in row_offsets:
                y0 = miny + row_off
                strip = box(minx - 1_000.0, y0, maxx + 1_000.0, y0 + depth)
                inter = local_poly.intersection(strip)
                row_piece = _largest_polygon(inter)
                if row_piece is None:
                    valid = False
                    break
                rx0, _, rx1, _ = row_piece.bounds
                run = rx1 - rx0
                units = max(1, min(8, int((run + spacing) // (unit_length + spacing))))
                unit_offsets = _centered_offsets(units, unit_length, 1.0, run)
                if unit_offsets is None:
                    valid = False
                    break
                for unit_off in unit_offsets:
                    rect = _make_rect(rx0 + unit_off, y0, unit_length, depth)
                    if not local_poly.buffer(1e-6).covers(rect):
                        valid = False
                        break
                    footprints.append(rect)
                if not valid:
                    break
            if not valid:
                continue
            if angle_offset == 90.0:
                footprints = [_rotate(p, 90.0, origin=core.centroid) for p in footprints]
            cand = _evaluate_candidate(footprints, field.target_bra, (field.floors_min, field.floors_max))
            if cand:
                cand.angle_offset_deg = angle_offset
                candidates.append(cand)
    return _choose_best(candidates, field.target_bra)


def _fit_centered_outer_rect(core: Polygon, max_width: float, max_height: float) -> Optional[Polygon]:
    cx, cy = core.centroid.x, core.centroid.y
    lo, hi = 0.2, 1.0
    best: Optional[Polygon] = None
    for _ in range(50):
        mid = (lo + hi) / 2.0
        w = snap_value(max_width * mid)
        h = snap_value(max_height * mid)
        outer = box(cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
        if core.contains(outer):
            best = outer
            lo = mid
        else:
            hi = mid
    return best


def _make_u_or_o_shape(outer: Polygon, ring_depth: float, min_courtyard: float) -> Optional[Polygon]:
    minx, miny, maxx, maxy = outer.bounds
    ow = maxx - minx
    oh = maxy - miny
    inner_w = ow - 2.0 * ring_depth
    inner_h = oh - 2.0 * ring_depth
    if inner_w >= min_courtyard and inner_h >= min_courtyard:
        inner = box(minx + ring_depth, miny + ring_depth, maxx - ring_depth, maxy - ring_depth)
        ring = outer.difference(inner)
        if isinstance(ring, Polygon) and not ring.is_empty:
            return ring.buffer(0)
    # fall back to U shape open to south
    if inner_w < min_courtyard:
        return None
    bottom_clear = max(min_courtyard, ring_depth * 1.5)
    left = box(minx, miny, minx + ring_depth, maxy)
    right = box(maxx - ring_depth, miny, maxx, maxy)
    top = box(minx, maxy - ring_depth, maxx, maxy)
    shape = unary_union([left, right, top]).buffer(0)
    if isinstance(shape, Polygon) and not shape.is_empty:
        return shape
    return None


def _place_karre_local(core: Polygon, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    minx, miny, maxx, maxy = core.bounds
    width = min(maxx - minx, spec.max_block_length_m or (maxx - minx))
    height = min(maxy - miny, spec.max_block_length_m or (maxy - miny))
    outer = _fit_centered_outer_rect(core, width * 0.92, height * 0.92)
    if outer is None:
        return None
    ring_depth = spec.segment_depth_m.midpoint() if spec.segment_depth_m else 13.0
    shape = _make_u_or_o_shape(outer, ring_depth, spec.min_courtyard_side_m or 18.0)
    if shape is None or not core.buffer(1e-6).covers(shape):
        return None
    cand = _evaluate_candidate([shape], field.target_bra, (field.floors_min, field.floors_max))
    if cand:
        return cand
    return None


def _candidate_for_field(core: Polygon, field: Delfelt) -> Optional[_PlacementCandidate]:
    spec = get_typology_spec(field.typology)
    if field.typology == Typology.LAMELL:
        return _place_lameller_local(core, field, spec)
    if field.typology == Typology.PUNKTHUS:
        return _place_punkthus_local(core, field, spec)
    if field.typology == Typology.REKKEHUS:
        return _place_rekkehus_local(core, field, spec)
    if field.typology == Typology.KARRE:
        return _place_karre_local(core, field, spec)
    return None


def place_buildings_for_fields(buildable_poly: Polygon, delfelt: List[Delfelt], plan_regler: Optional[PlanRegler] = None) -> Tuple[List[Bygg], float]:
    rules = plan_regler or PlanRegler()
    accepted: List[Bygg] = []
    global_geoms: List[Tuple[Polygon, float, Typology, float]] = []
    bra_deficit = 0.0
    build_counter = 1

    for field in delfelt:
        core = _field_core_polygon(field.polygon, rules.brann_avstand_m)
        candidate = _candidate_for_field(core, field)
        if candidate is None:
            bra_deficit += max(0.0, field.target_bra)
            continue

        placed_for_field: List[Bygg] = []
        angle_global = (field.orientation_deg + candidate.angle_offset_deg) % 180.0
        for footprint_local in candidate.footprints:
            footprint_global = _rotate(footprint_local, field.orientation_deg, origin=field.polygon.centroid) if candidate.angle_offset_deg == 0.0 else _rotate(footprint_local, field.orientation_deg, origin=field.polygon.centroid)
            footprint_global = footprint_global.buffer(0)
            height_m = _height_for(field.typology, candidate.floors)
            if not buildable_poly.buffer(1e-6).covers(footprint_global):
                continue
            if not field.polygon.buffer(1e-6).covers(footprint_global):
                continue
            if not _building_spacing_ok(footprint_global, angle_global, field.typology, height_m, global_geoms, rules):
                continue
            bygg = Bygg(
                bygg_id=f"B{build_counter}",
                footprint=footprint_global,
                floors=candidate.floors,
                height_m=height_m,
                typology=field.typology,
                delfelt_id=field.field_id,
                phase=field.phase,
                display_name=f"B{build_counter}",
            )
            placed_for_field.append(bygg)
            global_geoms.append((footprint_global, angle_global, field.typology, height_m))
            build_counter += 1

        accepted.extend(placed_for_field)
        achieved_bra = sum(b.bra_m2 for b in placed_for_field)
        bra_deficit += max(0.0, field.target_bra - achieved_bra)

    return accepted, float(bra_deficit)


def building_geometry_is_orthogonal_to_field(bygg: Bygg, field: Delfelt, tol: float = 1e-6) -> bool:
    local = _rotate(bygg.footprint, -field.orientation_deg, origin=field.polygon.centroid)
    from .typology_library import is_axis_aligned_rectilinear
    return is_axis_aligned_rectilinear(local, tol=tol)


def buildings_do_not_overlap(buildings: Sequence[Bygg]) -> bool:
    for i, a in enumerate(buildings):
        for b in buildings[i + 1 :]:
            if a.footprint.intersects(b.footprint) or a.footprint.overlaps(b.footprint):
                return False
    return True
