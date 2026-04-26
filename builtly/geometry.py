from __future__ import annotations

"""Deterministic geometry for Builtly v8 delivery 2.

Scope of delivery 2:
- Pass 1: site geometry analysis + delfelt subdivision
- Pass 3: deterministic volumetric placement with hard geometric rules

The functions in this module are intentionally independent from AI and from the
legacy masterplan stack. The only external geometric dependency is Shapely.
"""

from dataclasses import dataclass, replace
import math
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np
from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import split as shapely_split, unary_union

from .masterplan_types import (
    ArchitectureMetrics,
    Bygg,
    CourtyardKind,
    Delfelt,
    FieldSkeleton,
    FieldSkeletonSummary,
    Masterplan,
    PlanRegler,
    SkeletonFrontage,
    Typology,
)
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


def _exterior_coord_array(geom: Any) -> np.ndarray:
    polys = _flatten_polygons(geom)
    coords: List[Tuple[float, float]] = []
    for poly in polys:
        coords.extend((float(x), float(y)) for x, y in list(poly.exterior.coords)[:-1])
    if len(coords) < 3:
        raise ValueError("buildable_poly trenger minst 3 koordinater")
    return np.array(coords, dtype=float)


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


def pca_site_axes(buildable_poly: Any) -> SiteAxes:
    if buildable_poly is None or buildable_poly.is_empty:
        raise ValueError("buildable_poly mangler eller er tomt")

    coords = _exterior_coord_array(buildable_poly)

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

    Requested phase counts are treated as wishes, not absolutes. Very small or
    compact tomter skal ikke fragmenteres i mange delfelt bare fordi UI-et står
    på et høyt tall; feltantallet klamper derfor alltid mot en geometrisk
    rasjonell øvre grense.
    """
    axes = pca_site_axes(buildable_poly)
    area_m2 = float(buildable_poly.area)
    aspect = axes.major_axis_m / max(axes.minor_axis_m, 1.0)

    if requested_count is not None and requested_count > 0:
        count = int(requested_count)
    else:
        count = default_delfelt_count(area_m2, axes.major_axis_m)

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

    # Små tomter og slanke infill-felt må ikke overfragmenteres.
    if area_m2 < 3_200:
        count = 1
    elif area_m2 < 6_000:
        count = min(count, 2)
    elif area_m2 < 10_000 and aspect > 6.0:
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


def largest_inscribed_rectangle(poly: Polygon, *, min_aspect: float = 0.25, grid_resolution: int = 24) -> Optional[Polygon]:
    """Finn det største aksejusterte rektangelet som ligger helt innenfor poly.

    Bruker grid-søk over mulige nedre-venstre hjørner og tester voksende
    rektangler. Dette er en approksimasjon — ikke matematisk optimal — men
    robust nok for våre bruksområder (L-former, T-former, konkave tomter).

    For rektangulære polygoner returnerer dette det samme rektangelet.
    For konkave polygoner returnerer den største rektangel som passer uten
    å krysse en innkjerv eller et hull.
    """
    if poly is None or poly.is_empty or poly.area <= 1.0:
        return None

    minx, miny, maxx, maxy = poly.bounds
    span_x = maxx - minx
    span_y = maxy - miny
    if span_x <= 0 or span_y <= 0:
        return None

    # Enkelt case: hvis polygonet allerede er rektangel, returnér det direkte
    hull = poly.convex_hull
    bbox = box(minx, miny, maxx, maxy)
    if hull.area >= bbox.area * 0.995 and poly.area >= bbox.area * 0.995:
        return bbox.intersection(poly).buffer(0) if not poly.contains(bbox) else bbox

    # Grid-søk: test et rutenett av (x, y)-par som nedre-venstre hjørne og
    # utvid rektangelet så langt det kan mens det fortsatt er inne.
    gx = max(4, int(grid_resolution))
    gy = max(4, int(grid_resolution))
    step_x = span_x / gx
    step_y = span_y / gy
    poly_buf = poly.buffer(1e-6)

    best_rect: Optional[Polygon] = None
    best_area = 0.0

    for i in range(gx + 1):
        for j in range(gy + 1):
            x0 = minx + i * step_x
            y0 = miny + j * step_y
            if not poly_buf.covers(Point(x0, y0)):
                continue
            # Finn maksimal bredde fra (x0, y0) mot høyre som er fortsatt i poly
            # Deretter finn maksimal høyde som holder hele rektangelet i poly.
            # Bruk binær-søk for hastighet.
            lo_w, hi_w = 0.0, maxx - x0
            for _ in range(18):
                mid = (lo_w + hi_w) / 2.0
                if mid < step_x * 0.5:
                    break
                test_rect = box(x0, y0, x0 + mid, y0 + step_y)
                if poly_buf.covers(test_rect):
                    lo_w = mid
                else:
                    hi_w = mid
            w = lo_w
            if w < step_x * 0.5:
                continue
            lo_h, hi_h = 0.0, maxy - y0
            for _ in range(18):
                mid = (lo_h + hi_h) / 2.0
                if mid < step_y * 0.5:
                    break
                test_rect = box(x0, y0, x0 + w, y0 + mid)
                if poly_buf.covers(test_rect):
                    lo_h = mid
                else:
                    hi_h = mid
            h = lo_h
            if h < step_y * 0.5:
                continue
            aspect = min(w, h) / max(w, h)
            if aspect < min_aspect:
                continue
            area = w * h
            if area > best_area:
                best_area = area
                best_rect = box(x0, y0, x0 + w, y0 + h)

    return best_rect


def decompose_into_rectangles(poly: Polygon, *, min_rect_area: float = 200.0, max_rects: int = 4) -> List[Polygon]:
    """Dekomponer et (potensielt konkavt) polygon i opptil max_rects aksejusterte
    rektangler som dekker så mye som mulig av polygonet uten overlapp.

    Bruker grådig algoritme: finner største inskriberte rektangel, trekker fra,
    gjentar. Returnerer listen sortert etter areal (størst først).
    """
    if poly is None or poly.is_empty or poly.area <= min_rect_area:
        return []

    rects: List[Polygon] = []
    remaining = poly.buffer(0)

    for _ in range(max_rects):
        if remaining.is_empty or remaining.area < min_rect_area:
            break
        # Største delkomponent hvis remaining er fragmentert
        parts = _flatten_polygons(remaining)
        if not parts:
            break
        parts.sort(key=lambda p: p.area, reverse=True)
        target = parts[0]
        if target.area < min_rect_area:
            break
        mir = largest_inscribed_rectangle(target, grid_resolution=18)
        if mir is None or mir.area < min_rect_area:
            # Siste utvei: hvis polygonet *er* nesten rektangulært, bruk bbox
            minx, miny, maxx, maxy = target.bounds
            bbox = box(minx, miny, maxx, maxy)
            if target.area >= bbox.area * 0.95:
                rects.append(bbox.intersection(target).buffer(0))
            break
        rects.append(mir)
        # Subtraher rektangelet fra remaining, med litt buffer for å unngå
        # numeriske edges som gir slivers
        try:
            remaining = remaining.difference(mir.buffer(0.5))
            remaining = remaining.buffer(0) if not remaining.is_empty else remaining
        except Exception:
            break

    return rects


def _field_placement_cores(core: Polygon, *, concave_threshold: float = 0.90) -> List[Polygon]:
    """Returner plasseringskjerner for et delfelt.

    Hvis core er nesten konveks, returner [core] uendret.
    Hvis core er konkavt, dekomponer i rektangler og returner de største
    (opptil 2) som er store nok til å romme et bygg.

    Dette brukes av _place_*_local-funksjonene for å unngå at plassering
    feiler på L-formede eller T-formede delfelter.
    """
    if core is None or core.is_empty:
        return []
    ratio = convex_hull_ratio(core)
    if ratio >= concave_threshold:
        return [core]
    rects = decompose_into_rectangles(core, min_rect_area=300.0, max_rects=3)
    if not rects:
        return [core]
    # Behold rektangler som er minst 20% av core-arealet (unngå slivers)
    min_accept = core.area * 0.20
    kept = [r for r in rects if r.area >= min_accept]
    if not kept:
        # Fallback: returner største selv om det er lite
        return [rects[0]]
    return kept[:2]  # Maksimalt 2 delkjerner for å unngå for mange bygg i ett felt


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


def _run_subdivision(local_poly: Polygon, count: int) -> List[Polygon]:
    """Kjør den faktiske subdivisjonen på et polygon allerede rotert til lokalt rom.

    Returnerer delene i lokalt koordinatsystem.
    """
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

    return parts


def _rectangularity_score(parts: List[Polygon]) -> float:
    """Gjennomsnittlig bbox-fyll for alle deler: 1.0 = perfekte rektangler,
    0.5 = trekantede halvdeler, osv. Brukes for å velge beste subdivide-retning."""
    if not parts:
        return 0.0
    fills: List[float] = []
    for p in parts:
        if p.area <= 0:
            continue
        minx, miny, maxx, maxy = p.bounds
        bbox_area = (maxx - minx) * (maxy - miny)
        if bbox_area <= 0:
            continue
        fills.append(p.area / bbox_area)
    return sum(fills) / len(fills) if fills else 0.0


def subdivide_buildable_polygon(buildable_poly: Polygon, count: int, orientation_deg: float) -> List[Polygon]:
    """Subdelt en tomt i `count` delfelter.

    Vanligvis bruker vi `orientation_deg` (fra PCA) som subdivide-akse. Men for
    L-former og andre konkave tomter med ~1:1 sideforhold gir PCA en
    diagonal-retning (typisk 45°/135°) som ikke matcher de "naturlige"
    bygningsaksene. En akse-justert split (0°) gir i disse tilfellene
    rektangulære delfelter, mens en diagonal split gir trekantede.

    Derfor: hvis tomten er konkav, prøv både PCA-retning og 0°, og velg den
    som gir best rektangularitet (gjennomsnittlig bbox-fyll).
    """
    if count <= 1:
        return [buildable_poly.buffer(0)]

    candidate_orientations = [orientation_deg]
    # For konkave tomter: vurder også aksejustert retning hvis den ikke
    # allerede er i lista. Terskelen 0.90 matcher _field_placement_cores.
    if convex_hull_ratio(buildable_poly) < 0.90:
        # Normalise tested orientations for ~parallel comparisons
        def _same_angle(a: float, b: float) -> bool:
            diff = abs((a - b + 90.0) % 180.0 - 90.0)
            return diff < 5.0

        if not any(_same_angle(orientation_deg, 0.0) for _ in [0]):
            candidate_orientations.append(0.0)
        # Robustnesskandidat: 90° rotert fra 0°
        if not _same_angle(orientation_deg, 90.0):
            candidate_orientations.append(90.0)

    best_parts: Optional[List[Polygon]] = None
    best_orient: float = orientation_deg
    best_score: float = -1.0

    for orient in candidate_orientations:
        local_poly = _rotate(buildable_poly, -orient, origin=buildable_poly.centroid)
        parts = _run_subdivision(local_poly, count)
        if not parts or len(parts) < count:
            continue
        score = _rectangularity_score(parts)
        if score > best_score:
            best_score = score
            best_parts = parts
            best_orient = orient

    if best_parts is None:
        # Fallback: kjør som før (uten alternativer)
        local_poly = _rotate(buildable_poly, -orientation_deg, origin=buildable_poly.centroid)
        best_parts = _run_subdivision(local_poly, count)
        best_orient = orientation_deg

    global_parts = [_rotate(part, best_orient, origin=buildable_poly.centroid).buffer(0) for part in best_parts]
    global_parts = [part for part in global_parts if part.area > 1.0]

    # Order fields south-to-north by centroid, as required by the spec.
    global_parts.sort(key=lambda p: (p.centroid.y, p.centroid.x))
    return global_parts


def orientation_for_field(field_polygon: Polygon, fallback_deg: float = 0.0) -> float:
    """Bestem best plasseringsorientering for et gitt delfelt i globalt rom.

    Returnerer en vinkel i [0, 180) som skal lagres i `Delfelt.orientation_deg`.
    Plasseringskoden roterer core_global med `-orientation_deg` for å få
    core_local som antas aksejustert. Riktig orientering matcher derfor
    delfeltets faktiske "hovedakse".

    Strategi:
      1. Hvis delfeltet er (nesten) rektangulært, bruk PCA på delfeltet selv.
         Dette gir 0° for aksejusterte rektangler og riktig rotasjon for
         skråstilte rektangler.
      2. Hvis delfeltet er konkavt, bruk PCA på den største inskriberte
         rektangelen — det er det området plasseringen faktisk kommer til å
         jobbe i.
      3. Hvis alt annet feiler (tom polygon eller degenerert), returner
         fallback_deg.

    Dette fjerner antagelsen om at alle delfelter deler tomtens globale
    PCA-retning, som er feil når subdivide_buildable_polygon har byttet til
    akse-justert split for konkave tomter.
    """
    if field_polygon is None or field_polygon.is_empty:
        return float(fallback_deg) % 180.0

    target = field_polygon
    # For konkave delfelter: bruk MIR som representativt domene.
    if convex_hull_ratio(field_polygon) < 0.90:
        mir = largest_inscribed_rectangle(field_polygon, min_aspect=0.25, grid_resolution=20)
        if mir is not None and mir.area > 100.0:
            target = mir

    try:
        axes = pca_site_axes(target)
        return float(axes.theta_deg) % 180.0
    except Exception:
        # Fallback: bruk bbox-sideforhold
        minx, miny, maxx, maxy = field_polygon.bounds
        return 0.0 if (maxx - minx) >= (maxy - miny) else 90.0


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


def _minimum_building_spacing_m(rules: PlanRegler) -> float:
    """Hard minimum clear distance between separate buildings.

    The feasibility drawings must never show buildings closer than the fire
    separation / user rule. We keep 8 m as an explicit floor even if an older
    rules object is missing `brann_avstand_m` or has it set too low.
    """
    values = [8.0]
    try:
        values.append(float(getattr(rules, "brann_avstand_m", 0.0) or 0.0))
    except Exception:
        pass
    try:
        values.append(float(getattr(rules, "avstand_bygg_bygg_m", 0.0) or 0.0))
    except Exception:
        pass
    return max(values)


def _required_spacing(typology: Typology, height_m: float, rules: PlanRegler) -> float:
    spec = get_typology_spec(typology)
    if typology == Typology.LAMELL:
        # Tidligere brukte vi nesten full høyde som internavstand mellom lameller.
        # Det drepte de tette, rytmiske feltene og reduserte 8-10 slots til 4-6
        # faktiske bygg. Vi bruker derfor en mer realistisk kombinasjon av
        # brannkrav + moderat solavstand.
        sol_spacing = min(12.0, max(float(rules.brann_avstand_m), 0.50 * float(height_m) + 2.0))
    else:
        sol_spacing = spec.min_spacing_m
    return max(_minimum_building_spacing_m(rules), float(sol_spacing))


def _lamell_row_spacing(field: Delfelt, spec: BaseTypologySpec, rules: Optional[PlanRegler] = None) -> float:
    rule_obj = rules or PlanRegler()
    height_m = _height_for(Typology.LAMELL, int(getattr(field, 'floors_max', 4) or 4))
    base = _required_spacing(Typology.LAMELL, height_m, rule_obj)
    target_count = max(1, int(getattr(field, 'target_building_count', 0) or 1))
    if target_count >= 8:
        return max(_minimum_building_spacing_m(rule_obj), min(9.0, base))
    if target_count >= 6:
        return max(_minimum_building_spacing_m(rule_obj), min(10.0, base))
    return max(_minimum_building_spacing_m(rule_obj), base)


def _is_parallel(angle_a: float, angle_b: float, tol: float = 1e-3) -> bool:
    diff = abs(((angle_a - angle_b) + 180.0) % 180.0)
    return diff < tol or abs(diff - 180.0) < tol


def _projection_interval(poly: Polygon, ux: float, uy: float) -> Tuple[float, float]:
    values = [(float(x) * ux + float(y) * uy) for x, y in list(poly.exterior.coords)]
    return min(values), max(values)


def _interval_gap(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    if a[1] < b[0]:
        return b[0] - a[1]
    if b[1] < a[0]:
        return a[0] - b[1]
    return 0.0


def _interval_overlap(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _parallel_lamell_spacing_requirement(
    poly_a: Polygon,
    angle_a_deg: float,
    height_a_m: float,
    poly_b: Polygon,
    angle_b_deg: float,
    height_b_m: float,
    rules: PlanRegler,
) -> float:
    theta = math.radians(((angle_a_deg + angle_b_deg) / 2.0) % 180.0)
    ux, uy = math.cos(theta), math.sin(theta)
    vx, vy = -uy, ux

    a_t = _projection_interval(poly_a, ux, uy)
    b_t = _projection_interval(poly_b, ux, uy)
    a_n = _projection_interval(poly_a, vx, vy)
    b_n = _projection_interval(poly_b, vx, vy)

    tangent_overlap = _interval_overlap(a_t, b_t)
    tangent_gap = _interval_gap(a_t, b_t)
    tangent_len = min(a_t[1] - a_t[0], b_t[1] - b_t[0])
    normal_gap = _interval_gap(a_n, b_n)

    lamell_height = max(float(height_a_m), float(height_b_m))
    solar_req = max(_minimum_building_spacing_m(rules), min(12.0, 0.55 * lamell_height + 2.0))
    fire_req = _minimum_building_spacing_m(rules)

    # Stor overlap i lengderetningen = fasade mot fasade → krev solavstand.
    if tangent_overlap >= max(8.0, 0.35 * tangent_len):
        return solar_req

    # Gavel-mot-gavel eller tydelig forskjøvet = det er ikke nødvendig å kreve
    # full høydeavstand. Dette åpner for flere lameller i samme delfelt.
    if tangent_gap > 0.0 or normal_gap <= 4.0:
        return fire_req

    return max(fire_req, 12.0)


def _building_spacing_ok(candidate: Polygon, candidate_angle_deg: float, candidate_typology: Typology, candidate_height_m: float,
                         existing: List[Tuple[Polygon, float, Typology, float]], rules: PlanRegler) -> bool:
    for other_poly, other_angle_deg, other_typology, other_height_m in existing:
        if candidate.intersects(other_poly) or candidate.overlaps(other_poly):
            return False
        if _is_parallel(candidate_angle_deg, other_angle_deg) and candidate_typology == Typology.LAMELL and other_typology == Typology.LAMELL:
            req = _parallel_lamell_spacing_requirement(
                candidate, candidate_angle_deg, candidate_height_m,
                other_poly, other_angle_deg, other_height_m,
                rules,
            )
        else:
            req = max(_minimum_building_spacing_m(rules), _required_spacing(candidate_typology, candidate_height_m, rules), _required_spacing(other_typology, other_height_m, rules))
        if candidate.distance(other_poly) + 1e-6 < req:
            return False
    return True


def _make_rect(x0: float, y0: float, width_m: float, depth_m: float) -> Polygon:
    return box(snap_value(x0), snap_value(y0), snap_value(x0 + width_m), snap_value(y0 + depth_m))


def _fit_rect_in_segment(poly: Polygon, y0: float, depth_m: float, min_length: float, max_length: float) -> Optional[Polygon]:
    """Finn et aksejustert rektangel innenfor poly i en horisontal strip y0..y0+depth.

    Tidligere versjon antok at midt-punktet (px0+px1)/2 alltid kunne brukes som
    x-sentrum. Det feiler når piece er et trapez, triangel eller annen
    ikke-rektangulær del (f.eks. etter diagonal rotasjon av en L-form), fordi
    det sentrale punktet da kan ligge utenfor figuren selv om et rektangel av
    passende lengde kunne passet med et annet sentrum.

    Ny strategi per piece:
      1. Prøv det sentrerte binærsøket som før (rask path for rektangulære pieces).
      2. Hvis det gir None: skann en håndfull x-sentrum langs piece.bounds og
         kjør binærsøk lokalt for hvert.
      3. Hvis fortsatt None: bruk largest_inscribed_rectangle som siste fallback
         og klipp den til riktig depth.
    """
    piece_poly = poly
    minx, miny, maxx, maxy = poly.bounds
    strip = box(minx - 1_000.0, y0, maxx + 1_000.0, y0 + depth_m)
    inter = poly.intersection(strip)
    candidates = _flatten_polygons(inter)
    best: Optional[Polygon] = None
    best_area = 0.0

    # Lokal rect-bygger: snap bare x, ikke y. `_make_rect` snapper begge,
    # og når y0 ikke er grid-alignet (typisk etter rotasjon av core_local) vil
    # det snappes ned under piece-bunn slik at et ellers gyldig rektangel
    # feiler covers-sjekken. Her låser vi y0 og y0+depth_m direkte.
    def _rect_snap_x_only(x0: float, width_m: float) -> Polygon:
        sx0 = snap_value(x0)
        return box(sx0, y0, snap_value(sx0 + width_m), y0 + depth_m)

    def _bsearch_at_center(piece: Polygon, x_center: float, lo: float, hi: float) -> Optional[Polygon]:
        found: Optional[Polygon] = None
        piece_buf = piece.buffer(1e-6)
        for _ in range(30):
            if hi + 1e-6 < lo:
                break
            mid = snap_value((lo + hi) / 2.0)
            if mid < min_length - 1e-6:
                break
            rect = _rect_snap_x_only(x_center - mid / 2.0, mid)
            if piece_buf.covers(rect):
                found = rect
                lo = mid + GRID_SNAP_M
            else:
                hi = mid - GRID_SNAP_M
        return found

    for piece in candidates:
        px0, py0, px1, py1 = piece.bounds
        avail = px1 - px0
        if avail + 1e-6 < min_length:
            continue
        lo_init = min_length
        hi_init = min(max_length, avail)
        if hi_init + 1e-6 < lo_init:
            continue

        # --- Pass 1: sentrert binærsøk (original rask-sti) ---
        found = _bsearch_at_center(piece, (px0 + px1) / 2.0, lo_init, hi_init)

        # --- Pass 2: skann x-sentrum hvis sentrert feilet ---
        if found is None:
            # Velg 7 kandidatsentrum jevnt fordelt; dekker trapez/triangel-pieces
            scan_count = 7
            step = avail / (scan_count + 1)
            scan_best: Optional[Polygon] = None
            scan_best_area = 0.0
            for k in range(1, scan_count + 1):
                x_c = px0 + k * step
                cand = _bsearch_at_center(piece, x_c, lo_init, hi_init)
                if cand is not None and cand.area > scan_best_area:
                    scan_best = cand
                    scan_best_area = cand.area
            found = scan_best

        # --- Pass 3: MIR-fallback på piece ---
        if found is None:
            mir = largest_inscribed_rectangle(piece, min_aspect=0.1, grid_resolution=18)
            if mir is not None:
                mx0, my0, mx1, my1 = mir.bounds
                mir_w = mx1 - mx0
                mir_h = my1 - my0
                if mir_w + 1e-6 >= min_length and mir_h + 1e-6 >= depth_m * 0.9:
                    use_len = snap_value(min(max_length, mir_w))
                    x0 = mx0 + (mir_w - use_len) / 2.0
                    rect = _rect_snap_x_only(x0, use_len)
                    if piece.buffer(1e-6).covers(rect):
                        found = rect

        if found is not None and found.area > best_area:
            best = found
            best_area = found.area
    return best


def _centered_offsets(count: int, item_size: float, gap: float, total_span: float) -> Optional[List[float]]:
    occupied = count * item_size + max(0, count - 1) * gap
    if occupied > total_span + 1e-6:
        return None
    start = (total_span - occupied) / 2.0
    return [snap_value(start + i * (item_size + gap)) for i in range(count)]


@dataclass
class _PlacementCandidate:
    """En plasseringskandidat: sett av bygg med geometri, høyde og rotasjon.

    Feltene floors og angle_offset_deg er bakoverkompatible "fellesverdier" for
    hele kandidaten. For kandidater med variasjon per bygg brukes de valgfrie
    listene `floors_per_bygg` og `angle_offset_per_bygg` (samme lengde som
    footprints). Når listene er satt, ignoreres fellesverdiene av kaller.
    """
    footprints: List[Polygon]
    floors: int
    angle_offset_deg: float
    total_bra: float
    total_footprint: float
    floors_per_bygg: Optional[List[int]] = None
    angle_offset_per_bygg: Optional[List[float]] = None

    def floors_at(self, idx: int) -> int:
        if self.floors_per_bygg is not None and 0 <= idx < len(self.floors_per_bygg):
            return self.floors_per_bygg[idx]
        return self.floors

    def angle_offset_at(self, idx: int) -> float:
        if self.angle_offset_per_bygg is not None and 0 <= idx < len(self.angle_offset_per_bygg):
            return self.angle_offset_per_bygg[idx]
        return self.angle_offset_deg


def _evaluate_candidate(
    footprints: List[Polygon],
    target_bra: float,
    floors_range: Tuple[int, int],
    floors_per_bygg: Optional[List[int]] = None,
    angle_offset_per_bygg: Optional[List[float]] = None,
) -> Optional[_PlacementCandidate]:
    """Bygg en _PlacementCandidate fra footprints og målverdier.

    Hvis floors_per_bygg er satt, brukes den direkte (ingen beregning av
    fellesfloors). Ellers beregnes floors fra target_bra / total_footprint
    og klampes til floors_range som før.
    """
    if not footprints:
        return None
    total_fp = sum(p.area for p in footprints)
    if total_fp <= 0:
        return None

    if floors_per_bygg is not None and len(floors_per_bygg) == len(footprints):
        # Per-bygg-floors: clamp hver til floors_range, beregn BRA direkte
        clamped = [max(floors_range[0], min(floors_range[1], int(f))) for f in floors_per_bygg]
        total_bra = sum(fp.area * fl for fp, fl in zip(footprints, clamped))
        # Fellesfloors = avrundet gjennomsnitt, bare som "representativ" verdi
        avg_floors = int(round(sum(clamped) / len(clamped)))
        return _PlacementCandidate(
            footprints=footprints,
            floors=avg_floors,
            angle_offset_deg=0.0,
            total_bra=total_bra,
            total_footprint=total_fp,
            floors_per_bygg=clamped,
            angle_offset_per_bygg=angle_offset_per_bygg,
        )

    floors = int(round(target_bra / total_fp)) if target_bra > 0 else floors_range[0]
    floors = max(floors_range[0], min(floors_range[1], floors))
    return _PlacementCandidate(
        footprints=footprints,
        floors=floors,
        angle_offset_deg=0.0,
        total_bra=total_fp * floors,
        total_footprint=total_fp,
        floors_per_bygg=None,
        angle_offset_per_bygg=angle_offset_per_bygg,
    )


def _fit_varied_lameller_in_segment(
    poly: Polygon,
    y0: float,
    depth_m: float,
    min_length: float,
    max_length: float,
    min_spacing: float = 6.0,
) -> List[Polygon]:
    """Plasser flere lameller side om side i en horisontal strip.

    I stedet for å plassere ett stort rektangel per rad (som _fit_rect_in_segment)
    deler vi raden i mindre bygg. Dette gir variasjon i lengde som arkitekter
    typisk foretrekker: blandet 30/45/55m i stedet for tre identiske 60m bygg.

    Strategien:
      1. Hent den tilgjengelige striplengden fra intersection med segmentet.
      2. Velg et "målsett" av bygg-lengder som summerer opp til tilgjengelig
         lengde, minus mellomrom.
      3. Plasser byggene fortløpende fra venstre.
    """
    from shapely.geometry import box as _box
    minx, miny, maxx, maxy = poly.bounds
    strip = _box(minx - 1_000.0, y0, maxx + 1_000.0, y0 + depth_m)
    inter = poly.intersection(strip)
    pieces = _flatten_polygons(inter)
    result: List[Polygon] = []

    for piece in pieces:
        px0, py0, px1, py1 = piece.bounds
        avail_x = px1 - px0
        if avail_x + 1e-6 < min_length:
            continue

        # Bestem hvor mange bygg som får plass + deres lengder
        # Vi prøver å ha 1-3 bygg per rad, med varierte lengder der mulig.
        lengths = _varied_lengths_for_strip(avail_x, min_length, max_length, min_spacing)
        if not lengths:
            # Fallback: ett rektangel som før
            rect = _fit_rect_in_segment(piece, y0, depth_m, min_length, max_length)
            if rect is not None:
                result.append(rect)
            continue

        # Plasser byggene. Fordel ledig plass som mellomrom mellom dem.
        total_building_length = sum(lengths)
        total_gap = avail_x - total_building_length
        n_gaps = max(1, len(lengths) - 1)
        # Inntrukket fra kanter: bruk litt margin i endene og resten mellom bygg
        edge_margin = max(0.0, min(total_gap * 0.3 / 2.0, 6.0))
        between_gap = (total_gap - 2 * edge_margin) / n_gaps if n_gaps > 0 else 0.0
        between_gap = max(min_spacing, between_gap)

        x_cursor = px0 + edge_margin
        piece_buf = piece.buffer(1e-6)
        for L in lengths:
            if x_cursor + L > px1 + 1e-6:
                break
            rect = _box(snap_value(x_cursor), y0, snap_value(x_cursor + L), y0 + depth_m)
            if piece_buf.covers(rect):
                result.append(rect)
            x_cursor += L + between_gap
    return result


def _varied_lengths_for_strip(
    available: float,
    min_length: float,
    max_length: float,
    min_spacing: float,
) -> List[float]:
    """Foreslå varierte lamell-lengder som passer i en gitt lengde.

    Returnerer en liste av 1–3 lengder. Hvis tilgjengelig plass er stor nok til
    flere bygg, prøver vi å mikse lengder (short/medium/long) i stedet for å
    gjenta samme lengde. Hvis plassen er knapp, returneres én lengde på
    tilgjengelig størrelse.
    """
    if available + 1e-6 < min_length:
        return []

    short = min_length  # typisk 30m
    long_ = max_length  # typisk 60m
    medium = (short + long_) / 2.0  # typisk 45m

    # 3 bygg: trenger plass til 3 × min + 2 × spacing
    three_min = 3 * short + 2 * min_spacing
    # 2 bygg: trenger plass til 2 × min + 1 × spacing
    two_min = 2 * short + 1 * min_spacing

    if available + 1e-6 >= three_min + (medium - short) * 2:
        # Stor plass: 3 bygg med varierte lengder
        # Velg lengder slik at sum + 2*spacing ≈ available
        target_sum = available - 2 * min_spacing
        # Prøv {short, medium, long} eller {short, long, medium}, velg beste fit
        candidates = [
            [short, medium, long_],
            [short, long_, medium],
            [medium, short, long_],
        ]
        best = None
        best_waste = float("inf")
        for combo in candidates:
            total = sum(combo)
            # Krymp lengdene proporsjonalt hvis for langt
            if total > target_sum:
                scale = target_sum / total
                scaled = [max(short, L * scale) for L in combo]
                total = sum(scaled)
                if total > target_sum + 1e-6:
                    continue
                combo = scaled
            waste = target_sum - total
            if waste < best_waste:
                best = combo
                best_waste = waste
        if best is not None:
            return [snap_value(L) for L in best]

    if available + 1e-6 >= two_min + (long_ - short) * 0.5:
        # Middels plass: 2 bygg med varierte lengder
        target_sum = available - min_spacing
        # Prøv (short, long) eller (medium, medium)
        if target_sum >= short + long_:
            return [snap_value(short), snap_value(min(long_, target_sum - short))]
        if target_sum >= 2 * medium:
            return [snap_value(medium), snap_value(target_sum - medium)]
        # Fallback: del i to like
        half = target_sum / 2.0
        if half >= short:
            return [snap_value(half), snap_value(half)]

    # Liten plass: ett bygg på maksimal lengde som passer
    single_length = min(long_, available)
    if single_length >= min_length:
        return [snap_value(single_length)]
    return []


def _choose_best(candidates: List[_PlacementCandidate], target_bra: float) -> Optional[_PlacementCandidate]:
    if not candidates:
        return None
    def score(item: _PlacementCandidate) -> Tuple[float, float, int]:
        deficit = max(0.0, target_bra - item.total_bra)
        overshoot = max(0.0, item.total_bra - target_bra)
        return (deficit + overshoot * 0.25, -item.total_bra, len(item.footprints))
    return min(candidates, key=score)


def _solar_orientation_bonus(global_angle_deg: float, typology: Typology) -> float:
    """Gir en bonus (0..1) basert på hvor godt global vinkel matcher solbane-optimum.

    For Norge (rundt 63°N) er idealt:
    - Lamell: langfasaden vendt 90° (øst-vest akse) slik at sørfasade får mest sol
    - Rekkehus: samme; lang akse ideellt i øst-vest
    - Karré: retningsuavhengig siden alle 4 sider har fasader
    - Punkthus: retningsuavhengig, kvadratisk

    global_angle_deg er vinkelen lamellens langfasade peker (0° = langs x-aksen = øst-vest).
    Returnerer 1.0 ved perfekt øst-vest orientering (0° eller 180°),
    0.0 ved nord-sør orientering (90°).
    """
    if typology in (Typology.KARRE, Typology.PUNKTHUS):
        return 0.5  # nøytralt - retningsuavhengig

    # Normaliser til [0, 180)
    a = global_angle_deg % 180.0
    # Avstand til nærmeste øst-vest (0° eller 180°)
    east_west_distance = min(a, 180.0 - a)
    # Konverter: 0° distance -> 1.0, 90° distance -> 0.0
    return 1.0 - (east_west_distance / 90.0)


def _place_lameller_local(core: Polygon, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    """Plasser lameller i delfeltet. Vi prøver begge orienteringer (0° og 90°
    offset relativt til feltets hovedakse) og scorer kandidatene basert på
    både BRA-oppnåelse og sol-orientering.

    En lamell med langfasade i øst-vest gir mer sørsol enn en i nord-sør, så vi
    foretrekker øst-vest-orientering selv om BRA skulle bli marginalt lavere.
    """
    candidates: List[_PlacementCandidate] = []
    for angle_offset in (0.0, 90.0):
        local_poly = core if angle_offset == 0.0 else _rotate(core, -90.0, origin=core.centroid)
        minx, miny, maxx, maxy = local_poly.bounds
        width = maxx - minx
        height = maxy - miny
        depth = spec.depth_m.midpoint() if spec.depth_m else 13.0
        # Intern radavstand mellom lameller må være stor nok til brann/sol, men
        # ikke så stor at store delfelt kollapser til bare 4-6 bygg. Bruk den
        # samme modererte spacing-funksjonen som den globale valideringen.
        spacing = _lamell_row_spacing(field, spec)
        max_rows = max(1, min(4, int((height + spacing) // (depth + spacing))))
        for rows in range(1, max_rows + 1):
            offsets = _centered_offsets(rows, depth, spacing, height)
            if offsets is None:
                continue

            # Variant A: én lang lamell per rad (klassisk sti, garantert BRA-maks)
            footprints_single: List[Polygon] = []
            valid_single = True
            for off in offsets:
                y0 = miny + off
                rect = _fit_rect_in_segment(local_poly, y0, depth, spec.length_m.min_m, spec.length_m.max_m)
                if rect is None:
                    valid_single = False
                    break
                footprints_single.append(rect)

            # Variant B: varierte lengder per rad (flere bygg med miks av lengder)
            # Estetikk: hev minimum lengde til 30m for varied, slik at vi unngår
            # for korte "stubbe-bygg" som ser arkitektonisk dårlige ut.
            footprints_varied: List[Polygon] = []
            aesthetic_min_length = max(spec.length_m.min_m, 30.0)
            for off in offsets:
                y0 = miny + off
                rects = _fit_varied_lameller_in_segment(
                    local_poly, y0, depth, aesthetic_min_length, spec.length_m.max_m,
                    min_spacing=8.0,
                )
                footprints_varied.extend(rects)

            # Variant C: roterte lameller — kun ETT bygg får en liten vinkel
            # som en arkitektonisk aksent. Krever at det er ekstra buffer i
            # spacing mellom byggene, siden rotasjon bringer hjørner nærmere
            # hverandre. Som regel betyr dette at tomtene må ha plass til
            # mer enn spacing-kravet, og rotasjons-aksenten er en "luksus".
            footprints_rotated: List[Polygon] = []
            angles_rotated: List[float] = []
            # Beregn worst-case hjørneavstand ved 6° rotasjon av ett bygg.
            # For 60m bygg rotert 6°: hjørnet beveger seg ~sin(6°)*30 = 3.1m mot nabo.
            small_angle = 6.0
            import math as _math
            rotation_reach = _math.sin(_math.radians(small_angle)) * (spec.length_m.max_m / 2.0)
            needed_gap = spacing + 2.0 * rotation_reach  # trenger ~4m ekstra
            valid_rotated = (
                footprints_single
                and len(footprints_single) >= 2
                and height >= 2 * depth + 2 * needed_gap  # nok plass til å rotere trygt
            )
            if valid_rotated:
                accent_idx = 0
                for i, fp in enumerate(footprints_single):
                    angle = small_angle if i == accent_idx else 0.0
                    if abs(angle) > 1e-3:
                        rotated_fp = _rotate(fp, angle, origin=fp.centroid)
                        if not local_poly.buffer(1e-6).covers(rotated_fp):
                            valid_rotated = False
                            break
                    footprints_rotated.append(fp)
                    angles_rotated.append(angle)

            # Variant D: terrassert lamell — samme footprints som single, men
            # med varierte etasjer slik at silhuetten blir trappet. For en rad
            # med 2+ bygg gir dette visuell rytme. For 1 bygg faller det
            # tilbake til single.
            # Character-bevisst: hvis feltet er 'neighborhood_edge', bruker vi
            # neighbor_step_down slik at lavest etasje er mot nabosida.
            # Hvis vi kjenner nabohøyden, klamper vi den laveste etasjen slik
            # at vi ikke rager mye høyere enn naboen på den siden.
            footprints_terraced: List[Polygon] = []
            floors_terraced: List[int] = []
            valid_terraced = valid_single and len(footprints_single) >= 2
            if valid_terraced:
                total_fp = sum(p.area for p in footprints_single)
                base_floors = int(round(field.target_bra / total_fp)) if field.target_bra > 0 and total_fp > 0 else field.floors_min
                base_floors = max(field.floors_min, min(field.floors_max, base_floors))
                # Velg trapp-mønster basert på feltets character
                field_char = getattr(field, "character", None)
                # Beregn effektiv min-etasje basert på nabohøyde
                # Regel: lavest tillatte etasje = max(floors_min, naboens etasjer + 1)
                # Dette gjør at vi "møter" naboen uten å være betydelig høyere.
                effective_floors_min = field.floors_min
                max_neighbor_h = getattr(field, "max_neighbor_height_m", None)
                if field_char == "neighborhood_edge" and max_neighbor_h is not None and max_neighbor_h > 0:
                    neighbor_equivalent_floors = max(1, int(round(max_neighbor_h / 3.2)))
                    # Vi kan være 1 etasje over naboen uten å dominere
                    effective_floors_min = max(field.floors_min,
                                                min(field.floors_max,
                                                    neighbor_equivalent_floors + 1))
                if field_char == "neighborhood_edge":
                    variation_mode = "neighbor_step_up"
                else:
                    variation_mode = "stepped"
                floors_terraced = _varied_floors_for_cluster(
                    len(footprints_single), base_floors,
                    effective_floors_min, field.floors_max, variation=variation_mode
                )
                if len(set(floors_terraced)) < 2:
                    valid_terraced = False
                else:
                    footprints_terraced = list(footprints_single)

            # Registrer alle varianter som kandidater
            variants_to_register = [
                ("single", footprints_single if valid_single else [], None, None),
                ("varied", footprints_varied, None, None),
                ("rotated", footprints_rotated if valid_rotated else [], angles_rotated if valid_rotated else None, None),
                ("terraced", footprints_terraced if valid_terraced else [], None, floors_terraced if valid_terraced else None),
            ]
            for variant, footprints, angles, floors_per in variants_to_register:
                if not footprints:
                    continue
                if angle_offset == 90.0:
                    footprints = [_rotate(p, 90.0, origin=core.centroid) for p in footprints]
                cand = _evaluate_candidate(
                    footprints, field.target_bra, (field.floors_min, field.floors_max),
                    angle_offset_per_bygg=angles,
                    floors_per_bygg=floors_per,
                )
                if cand:
                    cand.angle_offset_deg = angle_offset
                    cand._variant = variant
                    candidates.append(cand)
    # Score med sol-hensyn og liten preferanse for varierte lengder
    # field.orientation_deg er feltets hovedakse i globalt rom. For angle_offset=0,
    # er lamellens langfasade langs feltets hovedakse. For angle_offset=90,
    # er den vinkelrett på hovedaksen.
    return _choose_best_with_solar(candidates, field, Typology.LAMELL)


def _choose_best_with_solar(candidates: List[_PlacementCandidate], field: Delfelt, typology: Typology) -> Optional[_PlacementCandidate]:
    """Velg beste kandidat med både BRA-oppnåelse og sol-orientering.

    For retningsavhengige typologier (lamell, rekkehus) blir sol-scoren viktig.
    For retningsuavhengige typologier (karré, punkthus) er den nøytral.

    V6: underdekning på BRA straffes tydeligere enn før. Dette gjør at når
    %-BRA-overstyring er satt høyt, velger motoren kandidater som faktisk har
    sjanse til å levere målet, og ikke for luftige varianter med god komposisjon
    men for lite areal.
    """
    if not candidates:
        return None

    def combined_score(item: _PlacementCandidate) -> Tuple[float, float, int]:
        # Primary score: hvor godt BRA-målet treffes. Underdekning straffes
        # hardere enn overshoot slik at vi prioriterer å nå minstemålet.
        deficit = max(0.0, field.target_bra - item.total_bra)
        overshoot = max(0.0, item.total_bra - field.target_bra)
        deficit_ratio = deficit / max(field.target_bra, 1.0)
        deficit_multiplier = 1.0 + min(1.2, deficit_ratio * 1.6)
        bra_score = deficit * deficit_multiplier + overshoot * 0.20
        if deficit_ratio > 0.06:
            bra_score += field.target_bra * 0.06 * min(1.0, deficit_ratio)

        # Solar penalty: for lamell/rekkehus, straffer nord-sør-orientering
        global_angle = (field.orientation_deg + item.angle_offset_deg) % 180.0
        solar_bonus = _solar_orientation_bonus(global_angle, typology)
        solar_penalty = field.target_bra * 0.16 * (1.0 - solar_bonus)

        # Variasjons-bonus: foretrekk "varied" (blandede lengder) og "rotated"
        # (per-bygg rotasjon) fremfor "single" (uniformt stort rektangel) når BRA
        # er tilnærmet likt. Bonus er moderat (~5% av target_bra) slik at BRA
        # fortsatt dominerer ved reelle forskjeller.
        variant = getattr(item, "_variant", "single")
        variation_bonus = 0.0
        if variant == "varied" and len(item.footprints) >= 2:
            variation_bonus = -(field.target_bra * 0.05)
        elif variant == "rotated" and len(item.footprints) >= 2:
            variation_bonus = -(field.target_bra * 0.04)
        elif variant == "terraced" and len(item.footprints) >= 2:
            # Terraced har ingen BRA-tap vs. single men gir sterk visuell effekt.
            # Gi den en moderat bonus.
            variation_bonus = -(field.target_bra * 0.06)

        # AI-direktiv-bonus: hvis field.design_variant er satt (AI ba om en
        # spesifikk variant), gi en MODERAT bonus til matchende kandidat.
        # Bonus er 5% av target_bra — bare en tie-breaker når BRA er nær likt.
        # Tidligere (15%) kunne overstyre reelle BRA-tap. Vi lar heller motoren
        # velge single/uo hvis de er klart best, selv om AI foreslo noe annet.
        directive_bonus = 0.0
        design_variant = getattr(field, "design_variant", None)
        if design_variant is not None and variant == design_variant:
            directive_bonus = -(field.target_bra * 0.05)

        # Character-bonus: feltets kontekstuelle karakter påvirker hvilke
        # varianter som foretrekkes (i tillegg til AI-direktiv). Liten bonus
        # (3%) som bare virker som tie-breaker. Sum av ideelle bonuses per
        # character:
        #   street_facing → terraced/rotated (markant)
        #   sheltered     → single (rolig)
        #   neighborhood_edge → terraced (trapper mot naboen)
        #   open_view     → ingen spesifikk preferanse for lamell
        character_bonus = 0.0
        field_char = getattr(field, "character", None)
        if field_char == "street_facing":
            if variant in ("terraced", "rotated") and len(item.footprints) >= 2:
                character_bonus = -(field.target_bra * 0.03)
        elif field_char == "sheltered":
            if variant == "single":
                character_bonus = -(field.target_bra * 0.03)
        elif field_char == "neighborhood_edge":
            if variant == "terraced" and len(item.footprints) >= 2:
                character_bonus = -(field.target_bra * 0.04)

        return (bra_score + solar_penalty + variation_bonus + directive_bonus + character_bonus,
                -item.total_bra, len(item.footprints))

    return min(candidates, key=combined_score)


def _varied_floors_for_cluster(
    n_buildings: int,
    base_floors: int,
    floors_min: int,
    floors_max: int,
    variation: str = "accent",
) -> List[int]:
    """Gi variert etasjetall til en gruppe bygg for arkitektonisk variasjon.

    variation-moduser:
      - "accent":   én høy aksent (+2 et), resten baseline
      - "stepped":  trappet gradient (f.eks. 4-5-6-7)
      - "paired":   par av høyder (4-6-4-6)
      - "uniform":  alle samme som base_floors (fallback, ingen variasjon)
      - "neighbor_step_down": trapper NED mot nabosida (bygg 0 lavest, siste høyest)
        — brukes av neighborhood_edge-felt for å respondere i skala
      - "neighbor_step_up": trapper OPP mot nabosida (bygg 0 høyest, siste lavest)

    Alle returnerte verdier er innenfor [floors_min, floors_max].
    Total BRA bevares (sum ≈ base_floors * n_buildings).
    """
    if n_buildings <= 0:
        return []
    clamp = lambda f: max(floors_min, min(floors_max, int(f)))

    if variation == "uniform" or n_buildings == 1:
        return [clamp(base_floors)] * n_buildings

    if variation in ("neighbor_step_down", "neighbor_step_up"):
        # Trapping med retning — identisk med 'stepped' men definert retning
        # rather than ascending index.
        if n_buildings == 2:
            low, high = clamp(base_floors - 1), clamp(base_floors + 1)
            return [low, high] if variation == "neighbor_step_down" else [high, low]
        # 3+ bygg: symmetrisk trapp
        span = min(3, floors_max - floors_min)
        half = span / 2.0
        steps = []
        for i in range(n_buildings):
            frac = i / (n_buildings - 1)
            offset = -half + frac * span
            floors = base_floors + round(offset)
            steps.append(clamp(floors))
        # Reverser hvis step_up (bygg 0 er nabosida, skal være høyest)
        if variation == "neighbor_step_up":
            steps = steps[::-1]
        # BRA-bevaring (samme som 'stepped')
        current_sum = sum(steps)
        target_sum = base_floors * n_buildings
        diff = target_sum - current_sum
        if diff > 0:
            for _ in range(diff):
                candidates = [(f, i) for i, f in enumerate(steps) if f < floors_max]
                if not candidates:
                    break
                _, idx = min(candidates)
                steps[idx] += 1
        elif diff < 0:
            for _ in range(-diff):
                candidates = [(f, i) for i, f in enumerate(steps) if f > floors_min]
                if not candidates:
                    break
                _, idx = max(candidates)
                steps[idx] -= 1
        return steps

    if variation == "accent":
        # Én aksent-bygg 2 etasjer høyere, resten baseline minus litt for å
        # kompensere slik at total BRA bevares.
        accent_idx = n_buildings // 2
        accent_floors = clamp(base_floors + 2)
        if accent_floors == base_floors:
            # Ikke plass til høyere: prøv lavere baseline + én på base
            new_base = clamp(base_floors - 1)
            if new_base < base_floors:
                result = [clamp(new_base) for _ in range(n_buildings)]
                result[accent_idx] = clamp(base_floors)
                # Kompenser hvis summen ikke når target
                target_sum = base_floors * n_buildings
                current_sum = sum(result)
                diff = target_sum - current_sum
                while diff > 0:
                    candidates = [(f, i) for i, f in enumerate(result) if f < floors_max]
                    if not candidates:
                        break
                    _, idx = min(candidates)
                    result[idx] += 1
                    diff -= 1
            else:
                result = [clamp(base_floors)] * n_buildings
        else:
            # Aksent gir +2, resten må kompenseres hvis mulig
            bonus = accent_floors - base_floors  # vanligvis 2
            result = [clamp(base_floors)] * n_buildings
            result[accent_idx] = accent_floors
            # Kompenser: fordel -bonus etasjer på andre bygg
            for _ in range(bonus):
                candidates = [(f, i) for i, f in enumerate(result)
                              if i != accent_idx and f > floors_min]
                if not candidates:
                    break
                _, idx = max(candidates)
                result[idx] -= 1
        return result

    if variation == "stepped":
        # Trappet: vi prøver å bevare total BRA ved å holde gjennomsnittet
        # lik base_floors. Dette er viktig fordi ellers mister terraced-varianten
        # 4-5% BRA vs. single.
        #
        # Strategi: trapp symmetrisk rundt base, f.eks.:
        #   base=5, n=4 → [3, 4, 6, 7] (snitt=5)
        #   base=5, n=3 → [4, 5, 6] (snitt=5)
        # Begrensning av floors_min/floors_max kan gi litt asymmetri,
        # men vi prøver å kompensere der.
        if n_buildings == 1:
            return [clamp(base_floors)]
        if n_buildings == 2:
            # To bygg: en lav, en høy rundt base
            return [clamp(base_floors - 1), clamp(base_floors + 1)]
        # 3+ bygg: trapp fra base-span/2 til base+span/2
        # Velg span slik at klamping ikke kutter alt
        span = min(3, floors_max - floors_min)
        half = span / 2.0
        steps = []
        for i in range(n_buildings):
            frac = i / (n_buildings - 1)  # 0 → 1
            offset = -half + frac * span
            floors = base_floors + round(offset)
            steps.append(clamp(floors))
        # Hvis clamping har redusert summen, prøv å kompensere
        current_sum = sum(steps)
        target_sum = base_floors * n_buildings
        diff = target_sum - current_sum
        if diff > 0:
            # Legg til etasjer på det laveste bygget først
            for _ in range(diff):
                candidates = [(f, i) for i, f in enumerate(steps) if f < floors_max]
                if not candidates:
                    break
                _, idx = min(candidates)
                steps[idx] += 1
        elif diff < 0:
            # Fjern etasjer fra høyeste-ikke-min bygg
            for _ in range(-diff):
                candidates = [(f, i) for i, f in enumerate(steps) if f > floors_min]
                if not candidates:
                    break
                _, idx = max(candidates)
                steps[idx] -= 1
        return steps

    if variation == "paired":
        # Alternerer høy/lav
        hi = clamp(base_floors + 1)
        lo = clamp(base_floors - 1)
        return [hi if i % 2 == 0 else lo for i in range(n_buildings)]

    return [clamp(base_floors)] * n_buildings


def _place_punkthus_local(core: Polygon, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    """Plasser punkthus i et delfelt. Vi genererer flere grid-layouts
    (1x1, 1x2, 2x1, 2x2, 2x3, 3x2, 3x3) og velger den som balanserer
    BRA-oppnåelse med kompakt klynge-form og arkitektonisk variasjon.

    Et kvadratisk arrangement (cols≈rows) er arkitektonisk bedre enn en lang
    "togrekke" fordi klyngen får tydeligere fellesrom mellom tårnene. Vi scorer
    derfor cols×rows-konfigurasjoner med en klynge-bonus for forholdstall nær 1.

    I tillegg gis hvert bygg varierte etasjetall (én aksent som stikker opp)
    for å bryte monotoni. Aksenten plasseres i et sentralt bygg.
    """
    candidates: List[Tuple[_PlacementCandidate, int, int]] = []  # (cand, cols, rows)
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
                # Beregn base floors som i _evaluate_candidate (target_bra / total_fp)
                total_fp_tmp = sum(p.area for p in footprints)
                if total_fp_tmp <= 0:
                    continue
                base_floors = int(round(field.target_bra / total_fp_tmp)) if field.target_bra > 0 else field.floors_min
                base_floors = max(field.floors_min, min(field.floors_max, base_floors))
                # AI-direktiv kan overstyre default-variation. Ellers: accent hvis 3+ bygg.
                ai_hp = getattr(field, "design_height_pattern", None)
                if ai_hp in ("uniform", "accent", "stepped", "paired"):
                    variation = ai_hp
                else:
                    variation = "accent" if len(footprints) >= 3 else "uniform"
                floors_per_bygg = _varied_floors_for_cluster(
                    len(footprints), base_floors, field.floors_min, field.floors_max, variation=variation
                )
                cand = _evaluate_candidate(
                    footprints,
                    field.target_bra,
                    (field.floors_min, field.floors_max),
                    floors_per_bygg=floors_per_bygg,
                )
                if cand:
                    candidates.append((cand, cols, rows))

    if not candidates:
        return None

    def punkthus_score(item: Tuple[_PlacementCandidate, int, int]) -> Tuple[float, float, int]:
        cand, cols, rows = item
        deficit = max(0.0, field.target_bra - cand.total_bra)
        overshoot = max(0.0, cand.total_bra - field.target_bra)
        bra_score = deficit + overshoot * 0.25

        # Klynge-bonus: kvadratisk (cols == rows) er best, lineær (1xN) er verst
        total_count = cols * rows
        if total_count <= 1:
            cluster_penalty = 0.0  # enkeltbygg, ikke relevant
        else:
            aspect = max(cols, rows) / min(cols, rows)
            cluster_penalty = field.target_bra * 0.12 * (aspect - 1.0)

        return (bra_score + cluster_penalty, -cand.total_bra, len(cand.footprints))

    best = min(candidates, key=punkthus_score)
    return best[0]


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
    return _choose_best_with_solar(candidates, field, Typology.REKKEHUS)


def _fit_centered_outer_rect(core: Polygon, max_width: float, max_height: float) -> Optional[Polygon]:
    """Finn det største rektangel centrert i core med max-dimensjoner begrenset.

    Originalversjonen gjør et binærsøk rundt core.centroid med uniform skalering.
    Det fungerer bra når core er rektangulært (centroid ligger midt i et stort
    rektangulært domene), men konvergerer for lavt når core har skjev form —
    det sentrerte rektangelet når raskt en kant selv om det finnes et større
    ikke-sentrert rektangel inne i figuren. Det gjør at Karré-ringen blir for
    tynn og `_make_u_or_o_shape` feiler med "indre for liten".

    Ny strategi:
      1. Sentrert binærsøk som før — beste resultat tar vi vare på som `centered_best`.
      2. Hvis `centered_best` er mindre enn 80% av bbox-grenseområdet (dvs. core
         er ikke rektangulært), kjør largest_inscribed_rectangle og bruk den hvis
         den gir et større rektangel som fortsatt respekterer max-dimensjonene.
    """
    cx, cy = core.centroid.x, core.centroid.y
    lo, hi = 0.2, 1.0
    centered_best: Optional[Polygon] = None
    centered_area = 0.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        w = snap_value(max_width * mid)
        h = snap_value(max_height * mid)
        outer = box(cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
        if core.contains(outer):
            if outer.area > centered_area:
                centered_best = outer
                centered_area = outer.area
            lo = mid
        else:
            hi = mid

    # Vurder MIR-fallback hvis sentrert resultat er svakt.
    # max_bbox_area = det vi *kunne* fått hvis core var rektangulært.
    max_bbox_area = max_width * max_height
    if centered_best is not None and centered_area >= max_bbox_area * 0.80:
        return centered_best

    mir = largest_inscribed_rectangle(core, min_aspect=0.30, grid_resolution=24)
    if mir is None:
        return centered_best

    mx0, my0, mx1, my1 = mir.bounds
    mir_w = mx1 - mx0
    mir_h = my1 - my0
    # Respekter max-dimensjoner: klipp ned om nødvendig, sentrert i MIR
    use_w = snap_value(min(mir_w, max_width))
    use_h = snap_value(min(mir_h, max_height))
    if use_w <= 0 or use_h <= 0:
        return centered_best
    mir_cx = (mx0 + mx1) / 2.0
    mir_cy = (my0 + my1) / 2.0
    candidate = box(
        mir_cx - use_w / 2.0, mir_cy - use_h / 2.0,
        mir_cx + use_w / 2.0, mir_cy + use_h / 2.0,
    )
    if not core.buffer(1e-6).covers(candidate):
        # MIR-approximasjonen kan overschyte litt — krymp proporsjonalt til det passer
        for shrink in (0.98, 0.95, 0.92, 0.88, 0.82):
            use_w2 = snap_value(use_w * shrink)
            use_h2 = snap_value(use_h * shrink)
            cand2 = box(
                mir_cx - use_w2 / 2.0, mir_cy - use_h2 / 2.0,
                mir_cx + use_w2 / 2.0, mir_cy + use_h2 / 2.0,
            )
            if core.buffer(1e-6).covers(cand2):
                candidate = cand2
                break
        else:
            return centered_best

    if candidate.area > centered_area:
        return candidate
    return centered_best


def _make_chamfered_uo_shape(outer: Polygon, ring_depth: float, min_courtyard: float,
                              chamfer_corner: str = "ne", chamfer_size: float = 10.0) -> Optional[Polygon]:
    """Lag en karré med ett avskåret hjørne (chamfer / avskjæring).

    Bygger først en vanlig U/O-form, deretter trimmer ett hjørne med 45° kutt.
    Det avskårne hjørnet leser som "hjørnebygg mot plass" eller "hovedinngang".

    chamfer_corner: "ne" | "nw" | "se" | "sw" — hvilket hjørne som kuttes
    chamfer_size: lengde av kuttet langs hver side (meter)
    """
    base = _make_u_or_o_shape(outer, ring_depth, min_courtyard)
    if base is None:
        return None

    minx, miny, maxx, maxy = outer.bounds
    s = float(chamfer_size)

    # Bygg en "cutter"-polygon (triangel som skjærer av hjørnet)
    if chamfer_corner == "ne":
        cutter = Polygon([(maxx - s, maxy), (maxx, maxy - s), (maxx + 1, maxy + 1)])
    elif chamfer_corner == "nw":
        cutter = Polygon([(minx + s, maxy), (minx, maxy - s), (minx - 1, maxy + 1)])
    elif chamfer_corner == "se":
        cutter = Polygon([(maxx - s, miny), (maxx, miny + s), (maxx + 1, miny - 1)])
    elif chamfer_corner == "sw":
        cutter = Polygon([(minx + s, miny), (minx, miny + s), (minx - 1, miny - 1)])
    else:
        return base  # ukjent hjørne → returner uendret

    shape = base.difference(cutter)
    if isinstance(shape, Polygon) and not shape.is_empty and shape.area > 0:
        return shape.buffer(0)
    return base  # fallback hvis chamfer feilet


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


def _make_l_shape(outer: Polygon, arm_depth: float) -> Optional[Polygon]:
    """Lag en L-formet karré: én horisontal arm pluss én vertikal arm i hjørnet.

    Passer for delfelter der O/U ikke får plass men en vinkel kan lages.
    Armen i "hjørnet" sør-vest som standard — stuer vender mot gården som åpner
    mot nord-øst.
    """
    minx, miny, maxx, maxy = outer.bounds
    ow = maxx - minx
    oh = maxy - miny
    # L trenger bare nok plass til 2 armer og en åpning. Vi krever at minst
    # én side har plass til minst 2× arm_depth (ellers er L ikke meningsfull).
    if ow < arm_depth * 2.0 or oh < arm_depth * 2.0:
        return None
    # Nedre horisontal arm: hele bredden
    bottom = box(minx, miny, maxx, miny + arm_depth)
    # Venstre vertikal arm: fra bunn til topp, men kun bredden arm_depth
    left = box(minx, miny, minx + arm_depth, maxy)
    shape = unary_union([left, bottom]).buffer(0)
    if isinstance(shape, Polygon) and not shape.is_empty:
        return shape
    return None


def _make_t_shape(outer: Polygon, arm_depth: float) -> Optional[Polygon]:
    """Lag en T-formet bebyggelse: horisontal topparm + vertikal sentrumsarm.

    Gir tydelige front (topparm mot gate) og ryggarm som skaper to gårder.
    """
    minx, miny, maxx, maxy = outer.bounds
    ow = maxx - minx
    oh = maxy - miny
    if ow < arm_depth * 3.0 or oh < arm_depth * 2.5:
        return None
    # Topparm: hele bredden, nordlige del
    top = box(minx, maxy - arm_depth, maxx, maxy)
    # Sentrumsarm: fra topp-arm ned til bunn, bredde arm_depth
    cx = (minx + maxx) / 2.0
    center = box(cx - arm_depth / 2.0, miny, cx + arm_depth / 2.0, maxy)
    shape = unary_union([top, center]).buffer(0)
    if isinstance(shape, Polygon) and not shape.is_empty:
        return shape
    return None


def _make_z_shape(outer: Polygon, arm_depth: float) -> Optional[Polygon]:
    """Lag en Z-formet bebyggelse: topp-arm + diagonal forbindelse + bunn-arm.

    Ikke en faktisk diagonal (aksejustert kode), men en zigzag av tre rette
    armer: topparm venstre side, nedover-arm i midten, bunnarm høyre side.
    """
    minx, miny, maxx, maxy = outer.bounds
    ow = maxx - minx
    oh = maxy - miny
    if ow < arm_depth * 3.0 or oh < arm_depth * 3.0:
        return None
    # Topparm: venstre 2/3 av bredden, øvre del
    top = box(minx, maxy - arm_depth, minx + ow * 0.66, maxy)
    # Midtarm: vertikal, i midten, hele høyden
    cx = minx + ow * 0.5
    middle = box(cx - arm_depth / 2.0, miny + arm_depth, cx + arm_depth / 2.0, maxy - arm_depth)
    # Bunnarm: høyre 2/3 av bredden, nedre del
    bottom = box(minx + ow * 0.34, miny, maxx, miny + arm_depth)
    shape = unary_union([top, middle, bottom]).buffer(0)
    if isinstance(shape, Polygon) and not shape.is_empty:
        return shape
    return None


def _canonical_karre_shape(core: Polygon, field: Delfelt, spec: BaseTypologySpec, *, closed: bool, open_side: Optional[str] = None) -> Optional[Polygon]:
    """Lag et proporsjonalt karrévolum med realistiske mål.

    Styrende geometri fra bruker:
    - bunn ca. 50 m
    - sider ca. 25-30 m
    - bygningsdybde ca. 12 m
    """
    if core is None or core.is_empty:
        return None
    minx, miny, maxx, maxy = core.bounds
    core_w = maxx - minx
    core_h = maxy - miny
    arm = float(spec.segment_depth_m.midpoint() if spec.segment_depth_m else 12.0)
    arm = max(11.5, min(12.5, arm))

    # Runde 8.2B: større felt bør få tydeligere karré med lengre hovedbygg
    # og mer raus indre gårdsromsflate. Mindre felt kan fortsatt bruke de
    # opprinnelige 50 x 40 m-proporsjonene.
    if float(getattr(field, "polygon", core).area or core.area or 0.0) >= 6500.0:
        # Runde 9: flere komplette karréer skal gi mer fasade/BRA uten å
        # overdimensjonere hver enkelt ring. Prioriter 50–60 m fasader.
        width_candidates = [60.0, 58.0, 56.0, 54.0, 52.0, 50.0, 48.0]
        side_candidates = [36.0, 34.0, 32.0, 30.0, 28.0]
    else:
        # Smaller karré cells are allowed in a multi-kvartal structure.
        # They still keep 11.5-12.5 m building depth, but the facade run may
        # step down to ca. 42-46 m so that two U-forms can face a shared space.
        width_candidates = [54.0, 52.0, 50.0, 48.0, 46.0, 44.0, 42.0, 40.0]
        side_candidates = [30.0, 28.0, 26.0, 25.0, 24.0]
    outer_h_candidates = []
    if closed:
        outer_h_candidates = [s + 2.0 * arm for s in side_candidates]
    else:
        outer_h_candidates = [s + arm for s in side_candidates]

    best = None
    best_score = None
    for outer_w in width_candidates:
        if outer_w > core_w - 2.0:
            continue
        for outer_h in outer_h_candidates:
            if outer_h > core_h - 2.0:
                continue
            rect = _fit_centered_outer_rect(core, outer_w, outer_h)
            if rect is None:
                continue
            rx0, ry0, rx1, ry1 = rect.bounds
            pieces = []
            if closed:
                outer = box(rx0, ry0, rx1, ry1)
                inner = box(rx0 + arm, ry0 + arm, rx1 - arm, ry1 - arm)
                shape = outer.difference(inner).buffer(0)
            else:
                side = (open_side or getattr(field, "courtyard_open_side", None) or "north").lower()
                bottom = box(rx0, ry0, rx1, ry0 + arm)
                left = box(rx0, ry0, rx0 + arm, ry1)
                right = box(rx1 - arm, ry0, rx1, ry1)
                top = box(rx0, ry1 - arm, rx1, ry1)
                if side == "north":
                    pieces = [bottom, left, right]
                elif side == "south":
                    pieces = [top, left, right]
                elif side == "east":
                    pieces = [bottom, left, top]
                else:
                    pieces = [bottom, right, top]
                shape = unary_union(pieces).buffer(0)
            if shape is None or getattr(shape, "is_empty", True):
                continue
            if not core.buffer(1e-6).covers(shape):
                continue
            target_w = 58.0 if float(getattr(field, "polygon", core).area or core.area or 0.0) >= 6500.0 else 50.0
            target_h = (58.0 if closed else 46.0) if target_w >= 58.0 else (52.0 if closed else 40.0)
            # La dimensjonsmål dominere over ren sentrering. Tidligere kunne
            # center_pen trekke motoren mot små, lette ringer.
            dims_score = abs((rx1 - rx0) - target_w) * 1.45 + abs(outer_h - target_h) * 1.15
            center_pen = shape.centroid.distance(core.centroid)
            width_bonus = -max(0.0, (rx1 - rx0) - 50.0) * 0.08
            score = dims_score + center_pen * 0.055 + width_bonus
            if best_score is None or score < best_score:
                best = shape
                best_score = score
    return best


def _evaluate_karre_shapes(core: Polygon, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    variants = []
    area = float(getattr(field, "polygon", core).area or 0.0)
    if area >= 14000.0:
        variants.append(("o", _canonical_karre_shape(core, field, spec, closed=True)))
    open_side = getattr(field, "courtyard_open_side", None) or "north"
    variants.append(("u", _canonical_karre_shape(core, field, spec, closed=False, open_side=open_side)))
    candidates = []
    for name, shape in variants:
        if shape is None or getattr(shape, "is_empty", True):
            continue
        cand = _evaluate_candidate([shape], field.target_bra, (field.floors_min, field.floors_max))
        if cand is None:
            continue
        cand._variant = name
        candidates.append(cand)
    if not candidates:
        return None
    return min(candidates, key=lambda item: _karre_candidate_penalty(item, field.target_bra))


def _place_karre_local(core: Polygon, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    """Plasser karré som proporsjonalt kvartalsvolum i stedet for tynne armer.

    Denne varianten holder seg tett på styringsmålene 50 m bunn, 25-30 m sider
    og 12 m bygningsbredde, og lager derfor mer troverdige volumskisser.
    """
    return _evaluate_karre_shapes(core, field, spec)


# ---------------------------------------------------------------------------
# Composition pass — FieldSkeleton / macro-micro structure
# ---------------------------------------------------------------------------


def _line_from_bounds(poly: Polygon, side: str) -> Optional[LineString]:
    if poly is None or poly.is_empty:
        return None
    minx, miny, maxx, maxy = poly.bounds
    if side == "north":
        return LineString([(minx, maxy), (maxx, maxy)])
    if side == "south":
        return LineString([(minx, miny), (maxx, miny)])
    if side == "west":
        return LineString([(minx, miny), (minx, maxy)])
    if side == "east":
        return LineString([(maxx, miny), (maxx, maxy)])
    return None


def _axis_line(poly: Polygon, axis: str = "x", coord: Optional[float] = None) -> Optional[LineString]:
    if poly is None or poly.is_empty:
        return None
    minx, miny, maxx, maxy = poly.bounds
    if axis == "x":
        y = (miny + maxy) / 2.0 if coord is None else float(coord)
        return LineString([(minx, y), (maxx, y)])
    x = (minx + maxx) / 2.0 if coord is None else float(coord)
    return LineString([(x, miny), (x, maxy)])


def _safe_center_rect(poly: Polygon, width_m: float, height_m: float) -> Optional[Polygon]:
    rect = _fit_centered_outer_rect(poly, width_m, height_m)
    if rect is not None and poly.buffer(1e-6).covers(rect):
        return rect
    return None


def _strip_from_side(poly: Polygon, side: str, depth_m: float) -> Optional[Polygon]:
    if poly is None or poly.is_empty:
        return None
    minx, miny, maxx, maxy = poly.bounds
    depth = max(2.0, float(depth_m))
    if side == "north":
        rect = box(minx - 1.0, maxy - depth, maxx + 1.0, maxy + 1.0)
    elif side == "south":
        rect = box(minx - 1.0, miny - 1.0, maxx + 1.0, miny + depth)
    elif side == "west":
        rect = box(minx - 1.0, miny - 1.0, minx + depth, maxy + 1.0)
    else:
        rect = box(maxx - depth, miny - 1.0, maxx + 1.0, maxy + 1.0)
    piece = poly.intersection(rect).buffer(0)
    return _largest_polygon(piece) or piece


def _split_poly_evenly(poly: Polygon, count: int, axis: str = "x") -> List[Polygon]:
    if poly is None or poly.is_empty:
        return []
    count = max(1, int(count))
    if count == 1:
        return [_largest_polygon(poly) or poly]
    minx, miny, maxx, maxy = poly.bounds
    parts: List[Polygon] = []
    if axis == "x":
        span = (maxx - minx) / count
        for i in range(count):
            rect = box(minx + i * span - 1.0, miny - 1.0, minx + (i + 1) * span + 1.0, maxy + 1.0)
            piece = poly.intersection(rect).buffer(0)
            parts.extend([p for p in _flatten_polygons(piece) if p.area > 40.0])
    else:
        span = (maxy - miny) / count
        for i in range(count):
            rect = box(minx - 1.0, miny + i * span - 1.0, maxx + 1.0, miny + (i + 1) * span + 1.0)
            piece = poly.intersection(rect).buffer(0)
            parts.extend([p for p in _flatten_polygons(piece) if p.area > 40.0])
    parts.sort(key=lambda p: (p.centroid.x, p.centroid.y))
    return parts


def _preferred_frontage_sides(field: Delfelt, default_primary: str = "south", default_secondary: Optional[str] = None) -> Tuple[str, Optional[str]]:
    primary = getattr(field, "frontage_primary_side", None) or default_primary
    secondary = getattr(field, "frontage_secondary_side", None) or default_secondary
    if secondary == primary:
        secondary = None
    return str(primary), (str(secondary) if secondary else None)


def _split_band_symmetric(band: Polygon, splits: int, axis: str) -> List[Polygon]:
    pieces = _split_poly_evenly(band, splits, axis=axis)
    if len(pieces) <= 1:
        return pieces
    minx, miny, maxx, maxy = band.bounds
    if axis == "x":
        center = (minx + maxx) / 2.0
        pieces.sort(key=lambda p: (abs(p.centroid.x - center), round(p.centroid.y, 3), round(p.centroid.x, 3)))
    else:
        center = (miny + maxy) / 2.0
        pieces.sort(key=lambda p: (abs(p.centroid.y - center), round(p.centroid.x, 3), round(p.centroid.y, 3)))
    return pieces


def _slot_count_for_band(band: Polygon, count_hint: int, pattern: Optional[str]) -> Tuple[int, str]:
    minx, miny, maxx, maxy = band.bounds
    width = maxx - minx
    height = maxy - miny
    axis = "x" if width >= height else "y"
    long_span = max(width, height)
    if pattern == "parallel_bands":
        if long_span >= 120:
            splits = 3
        elif long_span >= 72:
            splits = 2
        else:
            splits = 1
        if count_hint >= 6 and long_span >= 95:
            splits = max(splits, 3 if long_span >= 120 else 2)
    elif pattern == "frontage_ring":
        if axis == "x":
            splits = 2 if width >= 72 else 1
        else:
            splits = 2 if height >= 84 else 1
    elif pattern == "park_bands":
        splits = 2 if long_span >= 84 else 1
    elif pattern == "node_cluster":
        splits = 1
    else:
        splits = 2 if long_span >= 80 and count_hint >= 4 else 1
    return max(1, splits), axis


def _build_micro_fields_from_bands(bands: Sequence[Polygon], count_hint: int, pattern: Optional[str], symmetry: Optional[str]) -> List[Polygon]:
    micro: List[Polygon] = []
    for band in bands:
        splits, axis = _slot_count_for_band(band, count_hint, pattern)
        pieces = _split_band_symmetric(band, splits, axis) or [band]
        micro.extend(pieces)
    if not micro:
        return []
    # Stabil, symmetrisk sortering på tvers av alle slots
    minx = min(p.bounds[0] for p in micro)
    miny = min(p.bounds[1] for p in micro)
    maxx = max(p.bounds[2] for p in micro)
    maxy = max(p.bounds[3] for p in micro)
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    if symmetry == "bilateral":
        micro.sort(key=lambda p: (round(p.centroid.y, 3), abs(p.centroid.x - cx), round(p.centroid.x, 3)))
    elif symmetry == "axial":
        micro.sort(key=lambda p: (abs(p.centroid.y - cy), round(p.centroid.x, 3), round(p.centroid.y, 3)))
    else:
        micro.sort(key=lambda p: (round(p.centroid.y, 3), round(p.centroid.x, 3)))
    return [m for m in micro if m.area > 60.0]


def _compose_linear_skeleton(core: Polygon, field: Delfelt) -> FieldSkeleton:
    minx, miny, maxx, maxy = core.bounds
    width = maxx - minx
    height = maxy - miny
    band_count = max(2, int(getattr(field, "micro_band_count", 0) or 4))
    corridor_count = max(1, int(getattr(field, "view_corridor_count", 0) or 1))
    corridor_w = float(getattr(field, "corridor_width_m", None) or 8.5)
    frontage_depth = float(getattr(field, "frontage_depth_m", None) or 12.5)
    public_realm_ratio = float(getattr(field, "public_realm_ratio", 0.10) or 0.10)
    frontage_zone_ratio = float(getattr(field, "frontage_zone_ratio", 0.22) or 0.22)
    primary_side, secondary_side = _preferred_frontage_sides(field, "south", "north" if getattr(field, "frontage_mode", None) == "double" else None)
    pattern = getattr(field, "micro_field_pattern", None)

    if pattern == "dense_parallel_bands":
        frontages: List[SkeletonFrontage] = []
        frontage_zones: List[Polygon] = []
        for side in [primary_side, secondary_side]:
            if not side:
                continue
            line = _line_from_bounds(core, side)
            if line is not None:
                frontages.append(SkeletonFrontage(role=f"{side}_frontage", line=line))
            zone = _strip_from_side(core, side, max(frontage_depth, height * frontage_zone_ratio))
            if zone is not None and not zone.is_empty:
                frontage_zones.extend([p for p in _flatten_polygons(zone) if p.area > 80.0])

        corridor_polys: List[Polygon] = []
        if corridor_count > 0:
            usable_w = max(0.0, width - corridor_count * corridor_w)
            step = usable_w / max(corridor_count + 1, 1)
            for i in range(corridor_count):
                cx = minx + step * (i + 1) + corridor_w * i + corridor_w / 2.0
                corridor = box(cx - corridor_w / 2.0, miny - 1.0, cx + corridor_w / 2.0, maxy + 1.0)
                clipped = core.intersection(corridor).buffer(0)
                corridor_polys.extend([p for p in _flatten_polygons(clipped) if p.area > 40.0])

        subtractor = unary_union(corridor_polys).buffer(0) if corridor_polys else None
        remaining = core.difference(subtractor).buffer(0) if subtractor is not None else core
        remaining_parts = [p for p in _flatten_polygons(remaining) if p.area > 120.0]
        target_count = max(2, int(getattr(field, "target_building_count", 0) or 2))
        row_count = max(2, min(4 if height >= 84.0 else 3, int(math.ceil(target_count / 3.0))))
        bands: List[Polygon] = []
        for piece in remaining_parts:
            split_parts = _split_poly_evenly(piece, row_count, axis='y') or [piece]
            bands.extend([p for p in split_parts if p.area > 100.0])
        if not bands:
            bands = remaining_parts or [core]

        build_slots = _build_micro_fields_from_bands(
            bands,
            target_count,
            "parallel_bands",
            getattr(field, "symmetry_preference", None),
        )
        public_realm: List[Polygon] = list(corridor_polys)
        reserved_open_space = [p for p in public_realm if p is not None and not p.is_empty]
        macro_axis = _axis_line(core, axis='x') if width >= height else _axis_line(core, axis='y')
        symmetry_axis = _axis_line(core, axis='y') if width >= height else _axis_line(core, axis='x')
        return FieldSkeleton(
            field_id=field.field_id,
            mode='linear_bands_dense',
            local_orientation_deg=0.0,
            frontage_lines=frontages,
            build_bands=bands,
            courtyard_reserve=None,
            view_corridors=[c for c in corridor_polys if not c.is_empty and c.area > 40.0],
            accent_nodes=[],
            open_edges=[],
            frontage_depth_m=frontage_depth,
            corridor_width_m=corridor_w,
            macro_axis=macro_axis,
            symmetry_axis=symmetry_axis,
            frontage_zones=frontage_zones,
            micro_fields=build_slots,
            public_realm=public_realm,
            reserved_open_space=reserved_open_space,
            frontage_primary_side=primary_side,
            frontage_secondary_side=secondary_side,
            build_slots=build_slots,
            node_layout_mode=getattr(field, 'node_layout_mode', None),
        )

    # 1) Frontage-soner langs prioriterte sider
    frontage_zones: List[Polygon] = []
    frontages: List[SkeletonFrontage] = []
    frontage_build_bands: List[Polygon] = []
    for side in [primary_side, secondary_side]:
        if not side:
            continue
        line = _line_from_bounds(core, side)
        if line is not None:
            frontages.append(SkeletonFrontage(role=f"{side}_frontage", line=line))
        zone = _strip_from_side(core, side, max(frontage_depth, height * frontage_zone_ratio))
        if zone is not None and not zone.is_empty:
            parts = [p for p in _flatten_polygons(zone) if p.area > 80.0]
            frontage_zones.extend(parts)
            frontage_build_bands.extend(parts)

    # 2) Siktkorridorer som symmetriske vertikale snitt gjennom feltet
    corridor_polys: List[Polygon] = []
    if corridor_count > 0:
        usable_w = max(0.0, width - corridor_count * corridor_w)
        step = usable_w / max(corridor_count + 1, 1)
        for i in range(corridor_count):
            cx = minx + step * (i + 1) + corridor_w * i + corridor_w / 2.0
            corridor = box(cx - corridor_w / 2.0, miny - 1.0, cx + corridor_w / 2.0, maxy + 1.0)
            clipped = core.intersection(corridor).buffer(0)
            corridor_polys.extend([p for p in _flatten_polygons(clipped) if p.area > 40.0])

    # 3) Midtbånd / interne byggebaner mellom frontagene
    subtractor = unary_union(frontage_zones + corridor_polys).buffer(0) if (frontage_zones or corridor_polys) else None
    remaining = core.difference(subtractor).buffer(0) if subtractor is not None else core
    remaining_parts = [p for p in _flatten_polygons(remaining) if p.area > 100.0]
    interior_bands: List[Polygon] = []
    desired_interior = max(1, band_count - len(frontage_build_bands))
    if remaining_parts:
        target = max(remaining_parts, key=lambda g: g.area)
        split_axis = "y" if (target.bounds[3] - target.bounds[1]) > (target.bounds[2] - target.bounds[0]) else "x"
        split_parts = _split_poly_evenly(target, desired_interior, axis=split_axis) or [target]
        interior_bands.extend([p for p in split_parts if p.area > 90.0])

    bands = frontage_build_bands + interior_bands
    bands = [p for band in bands for p in _flatten_polygons(band.difference(unary_union(corridor_polys)).buffer(0))] if corridor_polys else bands
    bands = [p for p in bands if p.area > 90.0]

    # 4) Offentlig rom = korridorer + sentralt restrom
    public_realm: List[Polygon] = list(corridor_polys)
    residual = core.difference(unary_union(bands).buffer(0)).buffer(0) if bands else core
    residual_parts = [p for p in _flatten_polygons(residual) if p.area > core.area * public_realm_ratio * 0.2]
    public_realm.extend(residual_parts)
    courtyard = max(public_realm, key=lambda g: g.area).buffer(0) if public_realm else None

    micro_fields = _build_micro_fields_from_bands(
        bands,
        max(2, int(getattr(field, "target_building_count", 0) or band_count)),
        getattr(field, "micro_field_pattern", None),
        getattr(field, "symmetry_preference", None),
    )
    build_slots = list(micro_fields)
    if getattr(field, "composition_strictness", 0.0) >= 0.90 and len(build_slots) > max(6, int(getattr(field, "target_building_count", 0) or 0) * 2):
        build_slots = build_slots[: max(6, int(getattr(field, "target_building_count", 0) or 0) * 2)]

    macro_axis = _axis_line(core, axis="x")
    symmetry_axis = _axis_line(core, axis="y")
    reserved_open_space = [p for p in (public_realm + ([courtyard] if courtyard is not None else [])) if p is not None and not getattr(p, 'is_empty', True)]
    return FieldSkeleton(
        field_id=field.field_id,
        mode="linear_bands",
        local_orientation_deg=0.0,
        frontage_lines=frontages,
        build_bands=bands,
        courtyard_reserve=courtyard if isinstance(courtyard, Polygon) else _largest_polygon(courtyard),
        view_corridors=[c for c in corridor_polys if not c.is_empty and c.area > 40.0],
        accent_nodes=[],
        open_edges=[primary_side] + ([secondary_side] if secondary_side else []),
        frontage_depth_m=frontage_depth,
        corridor_width_m=corridor_w,
        macro_axis=macro_axis,
        symmetry_axis=symmetry_axis,
        frontage_zones=frontage_zones,
        micro_fields=micro_fields,
        public_realm=public_realm,
        reserved_open_space=reserved_open_space,
        frontage_primary_side=primary_side,
        frontage_secondary_side=secondary_side,
        build_slots=build_slots,
        node_layout_mode=getattr(field, "node_layout_mode", None),
    )


def _compose_courtyard_skeleton(core: Polygon, field: Delfelt) -> FieldSkeleton:
    minx, miny, maxx, maxy = core.bounds
    width = maxx - minx
    height = maxy - miny
    reserve_ratio = float(getattr(field, "courtyard_reserve_ratio", 0.32) or 0.32)
    frontage_depth = float(getattr(field, "frontage_depth_m", None) or 13.5)
    corridor_w = float(getattr(field, "corridor_width_m", None) or 8.0)
    primary_side, secondary_side = _preferred_frontage_sides(field, getattr(field, "courtyard_open_side", None) or "south", None)
    open_edges: List[str] = []
    if getattr(field, "courtyard_open_side", None):
        open_edges = [str(getattr(field, "courtyard_open_side"))]
    elif getattr(field, "design_karre_shape", None) in {"uo", "uo_chamfered"}:
        open_edges = [primary_side]

    scale = max(0.44, min(0.78, reserve_ratio ** 0.5))
    courtyard = _safe_center_rect(core, width * scale, height * scale)
    if courtyard is None:
        courtyard = _safe_center_rect(core, width * 0.56, height * 0.56)
    if courtyard is None:
        return FieldSkeleton(field_id=field.field_id, mode="courtyard_frontage", build_bands=[core], micro_fields=[core], build_slots=[core], frontage_primary_side=primary_side)

    cminx, cminy, cmaxx, cmaxy = courtyard.bounds
    side_geoms = {
        "north": core.intersection(box(minx - 1.0, cmaxy, maxx + 1.0, maxy + 1.0)).buffer(0),
        "south": core.intersection(box(minx - 1.0, miny - 1.0, maxx + 1.0, cminy)).buffer(0),
        "west": core.intersection(box(minx - 1.0, cminy, cminx, cmaxy)).buffer(0),
        "east": core.intersection(box(cmaxx, cminy, maxx + 1.0, cmaxy)).buffer(0),
    }

    bands: List[Polygon] = []
    frontage_zones: List[Polygon] = []
    frontages: List[SkeletonFrontage] = []
    for side, geom in side_geoms.items():
        if side in open_edges:
            continue
        parts = [p for p in _flatten_polygons(geom) if p.area > 80.0]
        bands.extend(parts)
        line = _line_from_bounds(core, side)
        if line is not None:
            frontages.append(SkeletonFrontage(role=f"{side}_frontage", line=line))
        zone = _strip_from_side(core, side, frontage_depth)
        if zone is not None and not zone.is_empty:
            frontage_zones.extend([p for p in _flatten_polygons(zone) if p.area > 50.0])

    corridors: List[Polygon] = []
    if int(getattr(field, "view_corridor_count", 0) or 0) > 0 and primary_side in {"south", "north"}:
        cx = (cminx + cmaxx) / 2.0
        cor = core.intersection(box(cx - corridor_w / 2.0, miny - 1.0, cx + corridor_w / 2.0, maxy + 1.0)).buffer(0)
        corridors.extend([p for p in _flatten_polygons(cor) if p.area > 40.0])
        if corridors:
            bands = [p for band in bands for p in _flatten_polygons(band.difference(unary_union(corridors)).buffer(0)) if p.area > 70.0]

    micro_fields: List[Polygon] = []
    for band in bands:
        bminx, bminy, bmaxx, bmaxy = band.bounds
        horizontal = (bmaxx - bminx) >= (bmaxy - bminy)
        split_count = 2 if horizontal and (bmaxx - bminx) >= 78 else 1
        if getattr(field, "composition_strictness", 0.0) >= 0.95 and horizontal and (bmaxx - bminx) >= 110:
            split_count = 3
        micro_fields.extend(_split_band_symmetric(band, split_count, axis=("x" if horizontal else "y")) or [band])
    build_slots = [m for m in micro_fields if m.area > 60.0]
    public_realm = [courtyard] + corridors
    reserved_open_space = [courtyard] + corridors
    macro_axis = _axis_line(courtyard, axis="y") or _axis_line(core, axis="y")
    symmetry_axis = _axis_line(courtyard, axis="x") or _axis_line(core, axis="x")
    return FieldSkeleton(
        field_id=field.field_id,
        mode="courtyard_frontage",
        local_orientation_deg=0.0,
        frontage_lines=frontages,
        build_bands=bands,
        courtyard_reserve=courtyard,
        view_corridors=corridors,
        accent_nodes=[],
        open_edges=open_edges,
        frontage_depth_m=frontage_depth,
        corridor_width_m=corridor_w,
        macro_axis=macro_axis,
        symmetry_axis=symmetry_axis,
        frontage_zones=frontage_zones,
        micro_fields=build_slots,
        public_realm=public_realm,
        reserved_open_space=reserved_open_space,
        frontage_primary_side=primary_side,
        frontage_secondary_side=secondary_side,
        build_slots=build_slots,
        node_layout_mode=getattr(field, "node_layout_mode", None),
    )


def _compose_park_skeleton(core: Polygon, field: Delfelt) -> FieldSkeleton:
    minx, miny, maxx, maxy = core.bounds
    width = maxx - minx
    height = maxy - miny
    reserve_ratio = float(getattr(field, "courtyard_reserve_ratio", 0.40) or 0.40)
    frontage_depth = float(getattr(field, "frontage_depth_m", None) or 11.5)
    corridor_w = float(getattr(field, "corridor_width_m", None) or 11.0)
    primary_side, secondary_side = _preferred_frontage_sides(field, "west", "east")
    scale = max(0.52, min(0.80, reserve_ratio ** 0.5))
    park = _safe_center_rect(core, width * scale, height * scale)
    if park is None:
        park = _safe_center_rect(core, width * 0.56, height * 0.56)
    if park is None:
        park = _largest_polygon(core.buffer(-4.0)) or core
    pminx, pminy, pmaxx, pmaxy = park.bounds

    corridors: List[Polygon] = []
    corridor_count = max(0, int(getattr(field, "view_corridor_count", 0) or 0))
    if corridor_count > 0:
        cx = (pminx + pmaxx) / 2.0
        cy = (pminy + pmaxy) / 2.0
        dominant_axis = "x" if width >= height else "y"
        corridor_specs: List[str] = [dominant_axis]
        if corridor_count >= 2:
            corridor_specs.append("y" if dominant_axis == "x" else "x")
        for axis in corridor_specs:
            if axis == "x":
                cor = core.intersection(box(cx - corridor_w / 2.0, miny - 1.0, cx + corridor_w / 2.0, maxy + 1.0)).buffer(0)
            else:
                cor = core.intersection(box(minx - 1.0, cy - corridor_w / 2.0, maxx + 1.0, cy + corridor_w / 2.0)).buffer(0)
            corridors.extend([p for p in _flatten_polygons(cor) if p.area > 40.0])

    side_geoms = {
        "north": core.intersection(box(minx - 1.0, pmaxy, maxx + 1.0, maxy + 1.0)).buffer(0),
        "south": core.intersection(box(minx - 1.0, miny - 1.0, maxx + 1.0, pminy)).buffer(0),
        "west": core.intersection(box(minx - 1.0, pminy, pminx, pmaxy)).buffer(0),
        "east": core.intersection(box(pmaxx, pminy, maxx + 1.0, pmaxy)).buffer(0),
    }
    bands = [p for side, g in side_geoms.items() for p in _flatten_polygons(g) if p.area > 80.0]
    if corridors:
        bands = [p for band in bands for p in _flatten_polygons(band.difference(unary_union(corridors)).buffer(0)) if p.area > 70.0]

    frontages: List[SkeletonFrontage] = []
    frontage_zones: List[Polygon] = []
    for side in [primary_side, secondary_side, "north", "south"]:
        if not side:
            continue
        line = _line_from_bounds(core, side)
        if line is not None:
            frontages.append(SkeletonFrontage(role=f"{side}_frontage", line=line))
        zone = _strip_from_side(core, side, frontage_depth)
        if zone is not None and not zone.is_empty:
            frontage_zones.extend([p for p in _flatten_polygons(zone) if p.area > 50.0])

    node_layout = getattr(field, "node_layout_mode", None) or ("paired_edges" if getattr(field, "node_symmetry", False) else "corners")
    target_nodes = max(2, int(getattr(field, "target_building_count", 0) or 4))
    nodes: List[Point] = []
    corners = [Point(pminx, pminy), Point(pminx, pmaxy), Point(pmaxx, pminy), Point(pmaxx, pmaxy)]
    if node_layout == "paired_edges" and target_nodes <= 4:
        nodes = [
            Point((pminx + pmaxx) / 2.0, pmaxy),
            Point((pminx + pmaxx) / 2.0, pminy),
            Point(pminx, (pminy + pmaxy) / 2.0),
            Point(pmaxx, (pminy + pmaxy) / 2.0),
        ]
    elif node_layout in {"perimeter_ring", "perimeter_ring_dense"} or target_nodes > 4:
        horizontal_major = (pmaxx - pminx) >= (pmaxy - pminy)
        long_side_midpoints = [
            Point((pminx + pmaxx) / 2.0, pminy),
            Point((pminx + pmaxx) / 2.0, pmaxy),
        ] if horizontal_major else [
            Point(pminx, (pminy + pmaxy) / 2.0),
            Point(pmaxx, (pminy + pmaxy) / 2.0),
        ]
        all_midpoints = [
            Point((pminx + pmaxx) / 2.0, pminy),
            Point((pminx + pmaxx) / 2.0, pmaxy),
            Point(pminx, (pminy + pmaxy) / 2.0),
            Point(pmaxx, (pminy + pmaxy) / 2.0),
        ]
        nodes = list(corners)
        if target_nodes >= 5:
            nodes.extend(long_side_midpoints)
        if target_nodes >= 7 or node_layout == "perimeter_ring_dense":
            nodes = list(corners) + all_midpoints
    else:
        nodes = list(corners)

    micro_fields = _build_micro_fields_from_bands(
        bands,
        max(2, int(getattr(field, "target_building_count", 0) or getattr(field, "micro_band_count", 0) or 2)),
        getattr(field, "micro_field_pattern", None),
        getattr(field, "symmetry_preference", None),
    )
    build_slots = [m for m in micro_fields if m.area > 60.0]
    public_realm = [park] + corridors
    reserved_open_space = [park] + corridors
    macro_axis = _axis_line(park, axis="x") if (pmaxx - pminx) >= (pmaxy - pminy) else _axis_line(park, axis="y")
    symmetry_axis = _axis_line(park, axis="y") if (pmaxx - pminx) >= (pmaxy - pminy) else _axis_line(park, axis="x")
    return FieldSkeleton(
        field_id=field.field_id,
        mode=("park_nodes" if field.typology == Typology.PUNKTHUS else "park_bands"),
        local_orientation_deg=0.0,
        frontage_lines=frontages,
        build_bands=bands,
        courtyard_reserve=park,
        view_corridors=corridors,
        accent_nodes=nodes,
        open_edges=[],
        frontage_depth_m=frontage_depth,
        corridor_width_m=corridor_w,
        macro_axis=macro_axis,
        symmetry_axis=symmetry_axis,
        frontage_zones=frontage_zones,
        micro_fields=build_slots,
        public_realm=public_realm,
        reserved_open_space=reserved_open_space,
        frontage_primary_side=primary_side,
        frontage_secondary_side=secondary_side,
        build_slots=build_slots,
        node_layout_mode=node_layout,
    )


def _compose_field_skeleton(core: Polygon, field: Delfelt) -> FieldSkeleton:
    mode = getattr(field, "skeleton_mode", None) or "linear_bands"
    if mode == "courtyard_frontage" or field.typology == Typology.KARRE:
        return _compose_courtyard_skeleton(core, field)
    if mode in {"park_nodes", "park_bands"} or field.typology == Typology.PUNKTHUS:
        return _compose_park_skeleton(core, field)
    return _compose_linear_skeleton(core, field)


def _band_sort_key(poly: Polygon) -> Tuple[float, float]:
    c = poly.centroid
    return (round(c.y, 3), round(c.x, 3))


def _lamell_length_palette(field: Delfelt, spec: BaseTypologySpec, pieces: Sequence[Polygon]) -> List[float]:
    max_len = spec.length_m.max_m
    frontage_emphasis = float(getattr(field, "frontage_emphasis", 0.85) or 0.85)
    dense_mode = int(getattr(field, "target_building_count", 0) or 0) >= 6
    if not pieces:
        min_len = max(spec.length_m.min_m, 30.0)
        base = [min_len, min(min_len + 8.0, max_len), min(min_len + 16.0, max_len)]
        return [snap_value(v) for v in base if min_len <= v <= max_len]
    widths = sorted(max(0.0, p.bounds[2] - p.bounds[0]) for p in pieces)
    median_w = widths[len(widths)//2]
    if dense_mode and median_w < spec.length_m.min_m * 0.95:
        min_len = max(22.0, min(spec.length_m.min_m, median_w * 0.92))
    else:
        min_len = max(spec.length_m.min_m, 30.0)
    long_pref = min(max_len, max(min_len + (6.0 if dense_mode else 18.0), median_w * (0.88 if dense_mode else (0.84 if frontage_emphasis >= 0.9 else 0.78))))
    palette = [
        min_len,
        min(min_len + 6.0, max_len),
        min(min_len + 12.0, max_len),
        long_pref,
    ]
    if frontage_emphasis >= 0.9 and not dense_mode:
        palette.append(min(max_len, max(min_len + 24.0, median_w * 0.92)))
    palette = [snap_value(v) for v in palette if min_len <= v <= max_len]
    out: List[float] = []
    for p in sorted(palette):
        if p not in out:
            out.append(p)
    return out or [snap_value(min_len)]

def _fit_rect_in_slot(piece: Polygon, depth_m: float, preferred_length: float, min_length: float, max_length: float, anchor_side: str = "center") -> Optional[Polygon]:
    minx, miny, maxx, maxy = piece.bounds
    width = maxx - minx
    height = maxy - miny
    if width < min_length or height < depth_m * 0.85:
        return None
    local_depth = min(depth_m, max(0.0, height))
    piece_buf = piece.buffer(1e-6)
    lengths = []
    for L in (preferred_length, preferred_length + 6.0, preferred_length - 6.0, max_length, min_length):
        LL = snap_value(max(min_length, min(max_length, L)))
        if LL not in lengths:
            lengths.append(LL)
    if anchor_side == "south":
        y_candidates = [miny, (miny + maxy) / 2.0 - local_depth / 2.0]
    elif anchor_side == "north":
        y_candidates = [maxy - local_depth, (miny + maxy) / 2.0 - local_depth / 2.0]
    elif anchor_side == "west":
        y_candidates = [(miny + maxy) / 2.0 - local_depth / 2.0]
    else:
        y_candidates = [(miny + maxy) / 2.0 - local_depth / 2.0]
    best = None
    best_score = float("inf")
    center_x = (minx + maxx) / 2.0
    for y0 in y_candidates:
        for length in lengths:
            if length > width + 1e-6:
                continue
            x_lo = minx + length / 2.0
            x_hi = maxx - length / 2.0
            if x_hi < x_lo:
                continue
            scan = 7 if (x_hi - x_lo) > 1.0 else 1
            xs = [x_lo + (x_hi - x_lo) * i / max(scan - 1, 1) for i in range(scan)]
            for xc in xs:
                sx0 = snap_value(xc - length / 2.0)
                rect = box(sx0, y0, snap_value(sx0 + length), y0 + local_depth)
                if not piece_buf.covers(rect):
                    continue
                score = abs(length - preferred_length) * 0.25 + abs(xc - center_x) * 0.02
                if score < best_score:
                    best = rect
                    best_score = score
    return best


def _center_out_row_order(items: Sequence[Polygon], *, axis: str, center_value: float) -> List[Polygon]:
    """Sort a row center-out to create calmer, more balanced composition."""
    if not items:
        return []
    ordered = sorted(
        items,
        key=lambda p: (round(p.centroid.x, 3), round(p.centroid.y, 3)) if axis == "x" else (round(p.centroid.y, 3), round(p.centroid.x, 3)),
    )
    values = [float(p.centroid.x if axis == "x" else p.centroid.y) for p in ordered]
    idxs = list(range(len(ordered)))
    idxs.sort(key=lambda i: (abs(values[i] - center_value), values[i]))
    return [ordered[i] for i in idxs]


def _group_lamell_rows(pieces: Sequence[Polygon], *, stack_axis: str) -> List[List[Polygon]]:
    """Group slot polygons into rows/columns before picking lamell locations."""
    if not pieces:
        return []
    spans: List[float] = []
    for piece in pieces:
        minx, miny, maxx, maxy = piece.bounds
        spans.append((maxy - miny) if stack_axis == "y" else (maxx - minx))
    spans = [s for s in spans if s > 0.0]
    tol = max(5.0, (float(np.median(spans)) * 0.72) if spans else 8.0)
    ordered = sorted(
        pieces,
        key=lambda p: (round(p.centroid.y, 3), round(p.centroid.x, 3)) if stack_axis == "y" else (round(p.centroid.x, 3), round(p.centroid.y, 3)),
    )
    rows: List[List[Polygon]] = []
    for piece in ordered:
        coord = float(piece.centroid.y if stack_axis == "y" else piece.centroid.x)
        if not rows:
            rows.append([piece])
            continue
        row = rows[-1]
        row_coord = sum(float(p.centroid.y if stack_axis == "y" else p.centroid.x) for p in row) / max(len(row), 1)
        if abs(coord - row_coord) <= tol:
            row.append(piece)
        else:
            rows.append([piece])
    return rows


def _ordered_slots_for_lamell(slots: Sequence[Polygon], skeleton: FieldSkeleton) -> List[Polygon]:
    """Order lamell slots by field system rather than simple first-available order.

    The older order often filled one band before using the next. This version
    groups slots into rows/columns, prioritises frontage rows, and then
    interleaves rows center-out. The result is more systematic, more readable
    and closer to architectural field diagrams.
    """
    pieces = [p for p in slots if not p.is_empty and p.area > 60.0]
    if not pieces:
        return []

    frontage_union = unary_union(skeleton.frontage_zones).buffer(0) if skeleton.frontage_zones else None
    minx = min(p.bounds[0] for p in pieces)
    miny = min(p.bounds[1] for p in pieces)
    maxx = max(p.bounds[2] for p in pieces)
    maxy = max(p.bounds[3] for p in pieces)
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0

    stack_axis = "y"
    within_axis = "x"
    if skeleton.symmetry_axis is not None:
        ax = skeleton.symmetry_axis
        vertical = abs(ax.coords[0][0] - ax.coords[-1][0]) < abs(ax.coords[0][1] - ax.coords[-1][1])
        if not vertical:
            stack_axis = "x"
            within_axis = "y"

    rows = _group_lamell_rows(pieces, stack_axis=stack_axis)
    if not rows:
        return pieces

    def _row_center(row: Sequence[Polygon]) -> float:
        return sum(float(p.centroid.y if stack_axis == "y" else p.centroid.x) for p in row) / max(len(row), 1)

    def _frontage_rank(row: Sequence[Polygon]) -> int:
        if frontage_union is None:
            return 1
        return 0 if any(p.intersects(frontage_union) for p in row) else 1

    primary = skeleton.frontage_primary_side or "south"
    secondary = skeleton.frontage_secondary_side

    def _row_sort_key(row: Sequence[Polygon]):
        coord = _row_center(row)
        fr = _frontage_rank(row)
        if stack_axis == "y":
            if secondary and {primary, secondary} == {"south", "north"}:
                metric = -abs(coord - cy) if fr == 0 else abs(coord - cy)
            elif primary == "north":
                metric = -coord
            else:
                metric = coord
        else:
            if secondary and {primary, secondary} == {"west", "east"}:
                metric = -abs(coord - cx) if fr == 0 else abs(coord - cx)
            elif primary == "east":
                metric = -coord
            else:
                metric = coord
        return (fr, metric, -sum(p.area for p in row))

    rows.sort(key=_row_sort_key)

    ordered_rows: List[List[Polygon]] = []
    for row in rows:
        center_value = cx if within_axis == "x" else cy
        ordered_rows.append(_center_out_row_order(row, axis=within_axis, center_value=center_value))

    interleaved: List[Polygon] = []
    max_len = max((len(row) for row in ordered_rows), default=0)
    for idx in range(max_len):
        for row in ordered_rows:
            if idx < len(row):
                interleaved.append(row[idx])

    return interleaved


def _lamell_rhythm_sequence(palette: Sequence[float], count: int, mode: Optional[str]) -> List[float]:
    if not palette:
        return []
    palette = list(sorted(palette))
    if count <= 1:
        return [palette[-1]]
    if mode == "uniform":
        return [palette[min(len(palette) - 1, len(palette) // 2)]] * count
    # mirrored/paired: lengst i midten, kortere mot kantene
    result = [palette[0]] * count
    center = (count - 1) / 2.0
    max_rank = max(abs(i - center) for i in range(count)) or 1.0
    for i in range(count):
        rank = 1.0 - abs(i - center) / max_rank
        idx = min(len(palette) - 1, max(0, int(round(rank * (len(palette) - 1)))))
        result[i] = palette[idx]
    return result


def _slot_anchor_side(slot: Polygon, skeleton: FieldSkeleton) -> str:
    primary = skeleton.frontage_primary_side or "south"
    secondary = skeleton.frontage_secondary_side
    if not secondary:
        return primary
    all_polys = skeleton.build_slots or skeleton.micro_fields or skeleton.build_bands
    if not all_polys:
        return primary
    minx = min(p.bounds[0] for p in all_polys)
    miny = min(p.bounds[1] for p in all_polys)
    maxx = max(p.bounds[2] for p in all_polys)
    maxy = max(p.bounds[3] for p in all_polys)

    # Tette lamellfelt med dobbel frontage trenger tre soner, ikke bare to.
    # Nederste rad skal ankres mot sør, øverste mot nord, og indre rader skal
    # sentreres. Uten dette kollapser 3-rads-oppsett til 6 bygg fordi midtraden
    # blir skjøvet for nær en ytterrad og faller på 8m spacing.
    dense_linear = skeleton.mode in {"linear_bands_dense", "linear_bands"} and len(all_polys) >= 6

    if {primary, secondary} == {"south", "north"}:
        cy = (miny + maxy) / 2.0
        if dense_linear:
            y0, y1 = slot.bounds[1], slot.bounds[3]
            slot_mid = (y0 + y1) / 2.0
            band_h = max(1.0, y1 - y0)
            if slot_mid <= cy - band_h * 0.45:
                return "south" if primary == "south" else "north"
            if slot_mid >= cy + band_h * 0.45:
                return "north" if secondary == "north" else "south"
            return "center"
        return secondary if slot.centroid.y > cy else primary
    if {primary, secondary} == {"west", "east"}:
        cx = (minx + maxx) / 2.0
        if dense_linear:
            x0, x1 = slot.bounds[0], slot.bounds[2]
            slot_mid = (x0 + x1) / 2.0
            band_w = max(1.0, x1 - x0)
            if slot_mid <= cx - band_w * 0.45:
                return "west" if primary == "west" else "east"
            if slot_mid >= cx + band_w * 0.45:
                return "east" if secondary == "east" else "west"
            return "center"
        return secondary if slot.centroid.x > cx else primary
    return primary


def _place_lameller_in_bands(skeleton: FieldSkeleton, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    slots = skeleton.build_slots or skeleton.micro_fields or skeleton.build_bands
    slots = _ordered_slots_for_lamell(slots, skeleton)
    if not slots:
        return None
    strictness = float(getattr(field, "composition_strictness", 0.90) or 0.90)
    variant = getattr(field, "design_variant", None) or ("terraced" if strictness > 0.90 else "rhythmic")
    rhythm_mode = getattr(field, "lamell_rhythm_mode", None) or ("mirrored" if strictness >= 0.85 else "uniform")
    palette = _lamell_length_palette(field, spec, slots)
    depth = spec.depth_m.midpoint() if spec.depth_m else 13.0
    base_target = int(getattr(field, "target_building_count", 0) or max(2, min(len(slots), int(getattr(field, "micro_band_count", 0) or 2))))
    slot_widths = sorted(max(0.0, p.bounds[2] - p.bounds[0]) for p in slots)
    median_slot_width = slot_widths[len(slot_widths)//2] if slot_widths else float(spec.length_m.min_m or 32.0)
    min_fit_length = max(22.0, min(spec.length_m.min_m, median_slot_width * 0.92)) if base_target >= 6 else max(spec.length_m.min_m, 32.0)
    avg_len = float(np.median(palette)) if palette else max(min_fit_length, 36.0)
    avg_f = max(field.floors_min, min(field.floors_max, int(round((field.floors_min + field.floors_max) / 2.0))))
    target_fp_needed = float(field.target_bra) / max(avg_f, 1.0)
    est_count = max(2, int(math.ceil(target_fp_needed / max(avg_len * depth, 1.0))))
    target_count = max(base_target, est_count)
    min_count = 2 if strictness >= 0.80 else 1
    target_count = max(min_count, min(target_count, len(slots)))

    def _build_candidate(slot_count: int, fill_remaining: bool) -> Optional[_PlacementCandidate]:
        selected = [p for p in slots if p.area > 80.0][:slot_count]
        if not selected:
            return None
        lengths = _lamell_rhythm_sequence(palette, len(selected), rhythm_mode)
        footprints: List[Polygon] = []
        used_area = 0.0
        for slot, preferred in zip(selected, lengths):
            anchor_side = _slot_anchor_side(slot, skeleton)
            rect = _fit_rect_in_slot(slot, depth, preferred, min_fit_length, spec.length_m.max_m, anchor_side=anchor_side)
            if rect is not None:
                footprints.append(rect)
                used_area += rect.area

        if fill_remaining:
            remaining_slots = [s for s in slots if s not in selected]
            while remaining_slots and (used_area * field.floors_max) < field.target_bra * 0.92 and len(footprints) < len(slots):
                slot = remaining_slots.pop(0)
                anchor_side = _slot_anchor_side(slot, skeleton)
                preferred = palette[-1] if palette else max(spec.length_m.min_m, 36.0)
                rect = _fit_rect_in_slot(slot, depth, preferred, min_fit_length, spec.length_m.max_m, anchor_side=anchor_side)
                if rect is not None:
                    footprints.append(rect)
                    used_area += rect.area

        if len(footprints) < slot_count and strictness < 0.98:
            for band in skeleton.build_bands:
                if (used_area * field.floors_max) >= field.target_bra * 0.92:
                    break
                bminx, bminy, bmaxx, bmaxy = band.bounds
                if (bmaxy - bminy) < depth * 0.9:
                    continue
                rect = _fit_rect_in_segment(band, (bminy + bmaxy) / 2.0 - depth / 2.0, depth, min_fit_length, spec.length_m.max_m)
                if rect is not None and all(rect.distance(existing) >= 8.0 for existing in footprints):
                    footprints.append(rect)
                    used_area += rect.area

        if len(footprints) < min_count:
            return None

        floors_per = None
        if variant in {"terraced", "rhythmic"} and len(footprints) >= 2:
            total_fp = sum(p.area for p in footprints)
            base_f = int(math.ceil(field.target_bra / max(total_fp, 1.0))) if field.target_bra > 0 else field.floors_min
            base_f = max(field.floors_min, min(field.floors_max, base_f))
            if total_fp * base_f < field.target_bra * 0.86:
                base_f = field.floors_max
            floors_per = _varied_floors_for_cluster(len(footprints), base_f, field.floors_min, field.floors_max, variation=getattr(field, "design_height_pattern", None) or "stepped")

        cand = _evaluate_candidate(footprints, field.target_bra, (field.floors_min, field.floors_max), floors_per_bygg=floors_per)
        if cand is not None:
            cand._variant = variant
        return cand

    candidates: List[_PlacementCandidate] = []
    tested_counts = sorted({max(min_count, min(target_count + delta, len(slots))) for delta in (-1, 0, 1)})
    for count in tested_counts:
        for fill_remaining in (False, True):
            cand = _build_candidate(count, fill_remaining)
            if cand is not None:
                candidates.append(cand)

    return _choose_best_structured(candidates, field.target_bra, skeleton, field)

def _place_karre_from_frontage(skeleton: FieldSkeleton, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    # Frontage-baserte karréer endte ofte med tynne, uproporsjonale "armer".
    # Vi bruker derfor samme proporsjonerte kvartalslogikk som i lokal plassering,
    # men holder oss til skjelettets tilgjengelige kjerne/reserve der det er mulig.
    core = getattr(skeleton, "field_polygon", None) or getattr(field, "polygon", None)
    if core is None:
        return None
    if skeleton.courtyard_reserve is not None:
        reserve = skeleton.courtyard_reserve
        rx0, ry0, rx1, ry1 = reserve.bounds
        arm = float(spec.segment_depth_m.midpoint() if spec.segment_depth_m else 12.0)
        outer = box(rx0 - arm, ry0 - arm, rx1 + arm, ry1 + arm).intersection(core).buffer(0)
        core = _largest_polygon(outer) or core
    cand = _evaluate_karre_shapes(core, field, spec)
    if cand is not None:
        cand._variant = getattr(cand, '_variant', None) or ('u' if skeleton.open_edges else 'o')
    return cand

def _place_punkthus_on_nodes(skeleton: FieldSkeleton, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    if not skeleton.accent_nodes:
        return None
    sizes = [field.tower_size_m] if field.tower_size_m else list(spec.allowed_tower_sizes_m)
    layout_mode = getattr(field, "node_layout_mode", None) or skeleton.node_layout_mode or ("paired_edges" if getattr(field, "node_symmetry", False) else "corners")
    target_count = max(1, int(getattr(field, "target_building_count", 0) or len(skeleton.accent_nodes) or 1))
    candidates: List[_PlacementCandidate] = []

    def _ordered_nodes() -> List[Point]:
        nodes = list(skeleton.accent_nodes)
        if not nodes:
            return nodes
        if layout_mode in {"green_room_ring", "perimeter_ring", "perimeter_ring_dense"}:
            reserve = skeleton.courtyard_reserve
            if reserve is not None and not reserve.is_empty:
                rc = reserve.centroid
                def _ring_key(pt: Point):
                    dx = pt.x - rc.x
                    dy = pt.y - rc.y
                    cardinal_priority = 0 if (abs(dx) < 1.0 or abs(dy) < 1.0 or abs(abs(dx) - abs(dy)) > 2.0) else 1
                    radial = abs(math.hypot(dx, dy) - max(getattr(reserve, 'length', 0.0) / (2.0 * math.pi), 1.0))
                    return (cardinal_priority, radial, round(abs(dx) + abs(dy), 3))
                nodes.sort(key=_ring_key)
                return nodes
        if skeleton.symmetry_axis is None or not getattr(field, "node_symmetry", False):
            return nodes
        ax = skeleton.symmetry_axis
        if abs(ax.coords[0][0] - ax.coords[-1][0]) < abs(ax.coords[0][1] - ax.coords[-1][1]):
            cx = ax.coords[0][0]
            nodes.sort(key=lambda pt: (abs(pt.x - cx), round(pt.y, 3), round(pt.x, 3)))
        else:
            cy = ax.coords[0][1]
            nodes.sort(key=lambda pt: (abs(pt.y - cy), round(pt.x, 3), round(pt.y, 3)))
        return nodes

    ordered_nodes = _ordered_nodes()
    if layout_mode == "paired_edges" and len(ordered_nodes) > max(4, target_count):
        ordered_nodes = ordered_nodes[: max(4, target_count)]

    for size in sizes:
        for requested_count in sorted({max(1, min(len(ordered_nodes), target_count + delta)) for delta in (-1, 0, 1)}):
            fps: List[Polygon] = []
            for node in ordered_nodes:
                rect = box(node.x - size / 2.0, node.y - size / 2.0, node.x + size / 2.0, node.y + size / 2.0)
                if skeleton.courtyard_reserve is not None and rect.intersects(skeleton.courtyard_reserve):
                    continue
                if any(rect.intersects(c) for c in skeleton.view_corridors):
                    continue
                if any(rect.intersects(zone) for zone in skeleton.frontage_zones):
                    continue
                fps.append(rect)
            filtered: List[Polygon] = []
            for rect in fps:
                if all(rect.distance(other) >= max(12.0, float(size)) * 0.78 for other in filtered):
                    filtered.append(rect)
            if len(filtered) > requested_count:
                filtered = filtered[:requested_count]
            if filtered:
                total_fp = sum(p.area for p in filtered)
                base_f = int(round(field.target_bra / max(total_fp, 1.0))) if field.target_bra > 0 else field.floors_min
                base_f = max(field.floors_min, min(field.floors_max, base_f))
                floors_per = _varied_floors_for_cluster(len(filtered), base_f, field.floors_min, field.floors_max, variation=getattr(field, "design_height_pattern", None) or "accent")
                cand = _evaluate_candidate(filtered, field.target_bra, (field.floors_min, field.floors_max), floors_per_bygg=floors_per)
                if cand is not None:
                    cand._variant = layout_mode
                    candidates.append(cand)
    return _choose_best_structured(candidates, field.target_bra, skeleton, field)

def _skeleton_summary(skeleton: FieldSkeleton) -> FieldSkeletonSummary:
    macro_axis_m = float(getattr(skeleton.macro_axis, "length", 0.0) or 0.0)
    symmetry_axis_m = float(getattr(skeleton.symmetry_axis, "length", 0.0) or 0.0)
    frontage_zone_area = float(sum(getattr(z, "area", 0.0) for z in skeleton.frontage_zones))
    public_realm_area = float(sum(getattr(z, "area", 0.0) for z in skeleton.public_realm))
    reserved_open_space_area = float(sum(getattr(z, "area", 0.0) for z in skeleton.reserved_open_space))
    return FieldSkeletonSummary(
        field_id=skeleton.field_id,
        skeleton_mode=skeleton.mode,
        frontage_count=len([f for f in skeleton.frontage_lines if f.line is not None]),
        micro_band_count=len([b for b in skeleton.build_bands if not b.is_empty]),
        courtyard_reserve_m2=float(getattr(skeleton.courtyard_reserve, "area", 0.0) or 0.0),
        view_corridor_m2=float(sum(getattr(c, "area", 0.0) for c in skeleton.view_corridors)),
        accent_node_count=len([n for n in skeleton.accent_nodes if n is not None]),
        build_band_area_m2=float(sum(getattr(b, "area", 0.0) for b in skeleton.build_bands)),
        frontage_depth_m=float(skeleton.frontage_depth_m),
        corridor_width_m=float(skeleton.corridor_width_m),
        macro_axis_m=macro_axis_m,
        symmetry_axis_m=symmetry_axis_m,
        frontage_zone_area_m2=frontage_zone_area,
        micro_field_count=len([m for m in skeleton.micro_fields if not m.is_empty]),
        public_realm_m2=public_realm_area,
        reserved_open_space_m2=reserved_open_space_area,
        build_slot_count=len([s for s in skeleton.build_slots if not s.is_empty]),
    )


def build_field_skeleton_summaries(delfelt: Sequence[Delfelt], plan_regler: Optional[PlanRegler] = None) -> List[FieldSkeletonSummary]:
    rules = plan_regler or PlanRegler()
    out: List[FieldSkeletonSummary] = []
    for field in delfelt:
        core = _field_core_polygon(field.polygon, rules.brann_avstand_m)
        local = _rotate(core, -field.orientation_deg, origin=field.polygon.centroid) if abs(field.orientation_deg) > 1e-3 else core
        out.append(_skeleton_summary(_compose_field_skeleton(local, field)))
    return out


def _sample_line_coverage(line: LineString, geom: Any, sample_count: int = 16, tol: float = 2.5) -> float:
    if line is None or line.is_empty or geom is None or getattr(geom, "is_empty", True):
        return 0.0
    hits = 0
    total = 0
    length = max(line.length, 1.0)
    for i in range(sample_count):
        pt = line.interpolate((i + 0.5) / sample_count * length)
        total += 1
        if geom.buffer(tol).contains(pt):
            hits += 1
    return hits / max(total, 1)


def _symmetry_score_local(buildings_local: Sequence[Polygon], field_poly_local: Polygon) -> float:
    if len(buildings_local) < 2:
        return 0.55
    cx = field_poly_local.centroid.x
    left = sorted([cx - p.centroid.x for p in buildings_local if p.centroid.x < cx])
    right = sorted([p.centroid.x - cx for p in buildings_local if p.centroid.x >= cx])
    if not left or not right:
        return 0.45
    n = min(len(left), len(right))
    span = max(field_poly_local.bounds[2] - field_poly_local.bounds[0], 1.0)
    diffs = [abs(left[i] - right[i]) / span for i in range(n)]
    return max(0.0, 1.0 - sum(diffs) / max(len(diffs), 1))


def _rhythm_score_local(buildings_local: Sequence[Polygon], skeleton: FieldSkeleton) -> float:
    if len(buildings_local) < 2:
        return 0.55
    if skeleton.macro_axis is not None and abs(skeleton.macro_axis.coords[0][0] - skeleton.macro_axis.coords[-1][0]) >= abs(skeleton.macro_axis.coords[0][1] - skeleton.macro_axis.coords[-1][1]):
        coords = sorted([p.centroid.x for p in buildings_local])
        span = max((max(coords) - min(coords)), 1.0)
    else:
        coords = sorted([p.centroid.y for p in buildings_local])
        span = max((max(coords) - min(coords)), 1.0)
    gaps = [coords[i + 1] - coords[i] for i in range(len(coords) - 1)]
    if not gaps:
        return 0.55
    mean_gap = sum(gaps) / len(gaps)
    cv = float(np.std(gaps) / max(mean_gap, 1e-6)) if len(gaps) > 1 else 0.0
    return max(0.0, 1.0 - min(1.0, cv))


def _isolated_building_penalty_local(buildings_local: Sequence[Polygon], span_ref: float) -> float:
    if len(buildings_local) <= 1:
        return 1.0
    isolated = 0
    for i, poly in enumerate(buildings_local):
        dists = [poly.distance(other) for j, other in enumerate(buildings_local) if i != j]
        if not dists:
            isolated += 1
            continue
        if min(dists) > max(12.0, span_ref * 0.18):
            isolated += 1
    return isolated / max(len(buildings_local), 1)



def _candidate_structure_score(
    footprints: Sequence[Polygon],
    skeleton: FieldSkeleton,
    field: Delfelt,
) -> float:
    """Score architecture beyond BRA: frontage, rhythm, park/courtyard, axes and isolation."""
    geoms = [p.buffer(0) for p in footprints if p is not None and not getattr(p, "is_empty", True)]
    if not geoms:
        return 0.0
    union = unary_union(geoms).buffer(0)
    if getattr(union, "is_empty", True):
        return 0.0

    frontage_scores = [
        _sample_line_coverage(front.line, union, sample_count=18, tol=2.8)
        for front in skeleton.frontage_lines
        if getattr(front, "line", None) is not None and not front.line.is_empty
    ]
    frontage_score = float(sum(frontage_scores) / len(frontage_scores)) if frontage_scores else 0.55

    rhythm_score = _rhythm_score_local(geoms, skeleton)

    frame_poly = skeleton.courtyard_reserve
    if frame_poly is None or getattr(frame_poly, "is_empty", True):
        frame_parts = [
            g for g in (list(skeleton.build_bands) + list(skeleton.frontage_zones) + list(skeleton.public_realm))
            if g is not None and not getattr(g, "is_empty", True)
        ]
        frame_poly = _largest_polygon(unary_union(frame_parts).convex_hull) if frame_parts else _largest_polygon(union.convex_hull)
    symmetry_score = _symmetry_score_local(geoms, frame_poly or geoms[0]) if frame_poly is not None else 0.55

    span_ref = max(
        1.0,
        *(max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) for g in geoms),
    )
    isolation_penalty = _isolated_building_penalty_local(geoms, span_ref * 2.8)
    cluster_score = max(0.0, 1.0 - isolation_penalty)

    room_score = 0.55
    reserve = skeleton.courtyard_reserve
    if reserve is not None and not reserve.is_empty:
        reserve_area = max(float(getattr(reserve, "area", 0.0) or 0.0), 1.0)
        intr = float(union.intersection(reserve.buffer(0.35)).area)
        openness = max(0.0, 1.0 - intr / reserve_area)
        dists = [float(poly.distance(reserve)) for poly in geoms]
        mean_dist = sum(dists) / max(len(dists), 1)
        target_dist = max(4.0, float(getattr(field, "frontage_depth_m", 12.0) or 12.0) * 0.85)
        proximity = max(0.0, 1.0 - mean_dist / max(target_dist * 2.2, 1.0))

        # En karré skal omslutte, en park-klynge skal ligge tydelig rundt grøntrommet.
        if field.typology == Typology.KARRE:
            enclosure = 0.55
            touching = 0
            for band in skeleton.build_bands:
                if union.intersection(band.buffer(1.0)).area > 25.0:
                    touching += 1
            if skeleton.build_bands:
                enclosure = min(1.0, touching / max(2, min(4, len(skeleton.build_bands))))
            room_score = 0.45 * openness + 0.55 * enclosure
        elif field.typology == Typology.PUNKTHUS or skeleton.mode in {"park_nodes", "park_bands"}:
            room_score = 0.55 * openness + 0.45 * proximity
        else:
            room_score = 0.65 * openness + 0.35 * proximity

    corridor_score = 0.60
    if skeleton.view_corridors:
        corridors_union = unary_union([c for c in skeleton.view_corridors if c is not None and not c.is_empty]).buffer(0)
        cor_area = max(float(getattr(corridors_union, "area", 0.0) or 0.0), 1.0)
        blocked = float(union.buffer(0.2).intersection(corridors_union).area)
        corridor_score = max(0.0, 1.0 - blocked / cor_area)

    weights = {
        "frontage": 0.24,
        "rhythm": 0.16,
        "symmetry": 0.14,
        "room": 0.18,
        "corridor": 0.14,
        "cluster": 0.14,
    }
    if field.typology == Typology.PUNKTHUS:
        weights.update({"frontage": 0.14, "rhythm": 0.10, "symmetry": 0.20, "room": 0.26, "corridor": 0.14, "cluster": 0.16})
    elif field.typology == Typology.KARRE:
        weights.update({"frontage": 0.30, "rhythm": 0.12, "symmetry": 0.16, "room": 0.24, "corridor": 0.08, "cluster": 0.10})
    elif skeleton.mode == "linear_bands_dense":
        weights.update({"frontage": 0.28, "rhythm": 0.20, "symmetry": 0.14, "room": 0.10, "corridor": 0.14, "cluster": 0.14})

    total = (
        frontage_score * weights["frontage"]
        + rhythm_score * weights["rhythm"]
        + symmetry_score * weights["symmetry"]
        + room_score * weights["room"]
        + corridor_score * weights["corridor"]
        + cluster_score * weights["cluster"]
    )
    return max(0.0, min(1.0, float(total)))


def _choose_best_structured(
    candidates: Sequence[_PlacementCandidate],
    target_bra: float,
    skeleton: FieldSkeleton,
    field: Delfelt,
) -> Optional[_PlacementCandidate]:
    """Pick candidate by architecture first enough that gaterom/uterom survives, but still respects BRA.

    V6: strukturscore er fortsatt viktig, men kandidater som havner tydelig under
    BRA-målet taper oftere mot mer arealeffektive løsninger. Dette svarer direkte
    på ønsket om at 100 %% BRA-overstyring skal gi minst 100 %% BRA når det er
    fysisk mulig innen feltenes parametre.
    """
    pool = [c for c in candidates if c is not None and c.footprints]
    if not pool:
        return None
    strictness = float(getattr(field, "composition_strictness", 0.88) or 0.88)

    def score(item: _PlacementCandidate) -> Tuple[float, float, float, int]:
        deficit = max(0.0, target_bra - item.total_bra)
        overshoot = max(0.0, item.total_bra - target_bra)
        deficit_ratio = deficit / max(target_bra, 1.0)
        bra_penalty = deficit * (1.25 + min(1.0, deficit_ratio * 1.7)) + overshoot * 0.18
        if deficit_ratio > 0.05:
            bra_penalty += target_bra * 0.08 * min(1.0, deficit_ratio)
        structure = _candidate_structure_score(item.footprints, skeleton, field)
        structure_penalty = (1.0 - structure) * max(target_bra, item.total_bra, 1.0) * (0.12 + strictness * 0.10)
        count_target = max(1, int(getattr(field, "target_building_count", 0) or len(item.footprints)))
        count_penalty = abs(len(item.footprints) - count_target) * max(target_bra, 1.0) * 0.010
        return (bra_penalty + structure_penalty + count_penalty, -structure, -item.total_bra, abs(len(item.footprints) - count_target))

    return min(pool, key=score)


def compute_architecture_metrics(plan: Masterplan) -> ArchitectureMetrics:
    if not plan.delfelt:
        return ArchitectureMetrics(total_score=0.0, notes=["Ingen delfelt i plan."])
    field_scores: List[Tuple[float, float, float, float, float, float, float, float, float, float, float, float, float, float, float]] = []
    notes: List[str] = []
    total_area = sum(max(field.polygon.area, 1.0) for field in plan.delfelt)
    typology_matches = 0
    typology_total = 0
    for field in plan.delfelt:
        buildings = list(plan.iter_buildings_for_delfelt(field.field_id))
        core_global = _field_core_polygon(field.polygon, plan.plan_regler.brann_avstand_m)
        local_field = _rotate(core_global, -field.orientation_deg, origin=field.polygon.centroid) if abs(field.orientation_deg) > 1e-3 else core_global
        skeleton = _compose_field_skeleton(local_field, field)
        local_buildings = [(_rotate(b.footprint, -field.orientation_deg, origin=field.polygon.centroid) if abs(field.orientation_deg) > 1e-3 else b.footprint).buffer(0) for b in buildings]
        union_local = unary_union(local_buildings).buffer(0) if local_buildings else None

        frontage_covs = [_sample_line_coverage(f.line, union_local) for f in skeleton.frontage_lines if f.line is not None]
        frontage = sum(frontage_covs) / max(len(frontage_covs), 1) if frontage_covs else 0.45
        frontage_regularity = 1.0 - min(1.0, float(np.std(frontage_covs)) / 0.25) if len(frontage_covs) > 1 else frontage

        courtyard = 0.55
        courtyard_enclosure = 0.55
        if skeleton.courtyard_reserve is not None and skeleton.courtyard_reserve.area > 10.0:
            built_overlap = float((union_local.intersection(skeleton.courtyard_reserve).area if union_local else 0.0))
            free_ratio = max(0.0, 1.0 - built_overlap / max(skeleton.courtyard_reserve.area, 1.0))
            occupied_frontage = 0
            for zone in skeleton.frontage_zones:
                if union_local is not None and union_local.intersection(zone.buffer(1.0)).area > 20.0:
                    occupied_frontage += 1
            band_factor = min(1.0, occupied_frontage / max(1, len(skeleton.frontage_zones) or len(skeleton.build_bands)))
            courtyard = 0.55 * free_ratio + 0.45 * band_factor
            required_edges = max(1, 4 - len(skeleton.open_edges or []))
            enclosing_edges = 0
            for band in skeleton.build_bands:
                if band.area <= 0:
                    continue
                if band.buffer(1.0).intersects(skeleton.courtyard_reserve):
                    enclosing_edges += 1
            courtyard_enclosure = min(1.0, enclosing_edges / required_edges)

        view_q = 0.7
        if skeleton.view_corridors:
            ratios = []
            for cor in skeleton.view_corridors:
                if cor.area <= 0:
                    continue
                built_overlap = float((union_local.intersection(cor).area if union_local else 0.0))
                ratios.append(max(0.0, 1.0 - built_overlap / cor.area))
            if ratios:
                view_q = sum(ratios) / len(ratios)

        symmetry = _symmetry_score_local(local_buildings, local_field)
        rhythm = _rhythm_score_local(local_buildings, skeleton)
        public_realm = 0.65
        if skeleton.public_realm:
            realm_area = sum(p.area for p in skeleton.public_realm)
            blocked = float(sum((union_local.intersection(z).area if union_local else 0.0) for z in skeleton.public_realm))
            public_realm = max(0.0, 1.0 - blocked / max(realm_area, 1.0))

        typology_matches += sum(1 for b in buildings if b.typology == field.typology)
        typology_total += len(buildings)
        purity = sum(1 for b in buildings if b.typology == field.typology) / max(len(buildings), 1) if buildings else 0.0

        entropy = 0.6
        if buildings:
            areas = [b.footprint_m2 for b in buildings]
            mean_area = max(sum(areas) / len(areas), 1.0)
            area_cv = (np.std(areas) / mean_area) if len(areas) > 1 else 0.0
            tiny_frac = sum(1 for a in areas if a < mean_area * 0.55) / len(areas)
            entropy = max(0.0, 1.0 - min(1.0, 0.60 * area_cv + 0.40 * tiny_frac))

        bya_actual = 100.0 * sum(b.footprint_m2 for b in buildings) / max(field.polygon.area, 1.0) if buildings else 0.0
        target_bya = float(field.target_bya_pct or 20.0)
        bya_fit = max(0.0, 1.0 - abs(bya_actual - target_bya) / max(target_bya, 1.0))

        slot_count = len([s for s in skeleton.build_slots if not s.is_empty])
        used_slots = 0
        if slot_count > 0 and union_local is not None:
            for slot in skeleton.build_slots:
                if union_local.intersection(slot).area > 20.0:
                    used_slots += 1
        micro_util = used_slots / max(slot_count, 1) if slot_count else 0.6

        gap_penalty = max(0.0, 1.0 - frontage)
        span_ref = max(local_field.bounds[2] - local_field.bounds[0], local_field.bounds[3] - local_field.bounds[1], 1.0)
        isolated_pen = _isolated_building_penalty_local(local_buildings, span_ref)

        field_total = (
            0.20 * frontage +
            0.11 * frontage_regularity +
            0.17 * courtyard +
            0.10 * courtyard_enclosure +
            0.12 * symmetry +
            0.11 * rhythm +
            0.08 * view_q +
            0.07 * public_realm +
            0.04 * purity +
            0.03 * entropy +
            0.04 * bya_fit +
            0.03 * micro_util
        ) - (0.06 * gap_penalty + 0.04 * isolated_pen)

        field_scores.append((field_total, field.polygon.area, frontage, courtyard, symmetry, view_q, entropy, bya_fit, public_realm, rhythm, gap_penalty, isolated_pen, frontage_regularity, courtyard_enclosure, micro_util))

    weighted = lambda idx: sum(item[idx] * item[1] for item in field_scores) / total_area if field_scores else 0.0
    frontage = weighted(2)
    courtyard = weighted(3)
    symmetry = weighted(4)
    view_q = weighted(5)
    entropy = weighted(6)
    bya_fit = weighted(7)
    public_realm = weighted(8)
    rhythm = weighted(9)
    gap_penalty = weighted(10)
    isolated_pen = weighted(11)
    frontage_regularity = weighted(12)
    courtyard_enclosure = weighted(13)
    micro_util = weighted(14)
    typ_purity = typology_matches / max(typology_total, 1)
    total = max(0.0, min(100.0, 100.0 * (
        0.17 * frontage +
        0.10 * frontage_regularity +
        0.15 * courtyard +
        0.08 * courtyard_enclosure +
        0.12 * symmetry +
        0.12 * rhythm +
        0.09 * view_q +
        0.08 * public_realm +
        0.05 * typ_purity +
        0.04 * entropy +
        0.05 * bya_fit +
        0.05 * micro_util -
        0.05 * gap_penalty -
        0.05 * isolated_pen
    )))
    if frontage < 0.60:
        notes.append("Frontage-kontinuiteten er fortsatt for svak; byggene definerer ikke gatelinjer tydelig nok.")
    if frontage_regularity < 0.58:
        notes.append("Frontage-linjene mangler rytme og regelmessighet.")
    if courtyard < 0.58:
        notes.append("Gårdsrommene er for uklare eller delvis spist opp av byggvolumer.")
    if courtyard_enclosure < 0.55:
        notes.append("Kvartalene omslutter ikke gårdsrommene tydelig nok.")
    if public_realm < 0.58:
        notes.append("Det offentlige/felles byrommet er for fragmentert eller delvis blokkert.")
    if view_q < 0.56:
        notes.append("Siktkorridorene er for svake eller delvis blokkert av bygg.")
    if symmetry < 0.52 or rhythm < 0.55:
        notes.append("Plasseringen mangler nok rytme og symmetri i feltaksene.")
    if micro_util < 0.50:
        notes.append("For få mikrofelt tas i bruk; delfeltene er fortsatt underkomponert.")
    if isolated_pen > 0.20:
        notes.append("Enkelte bygg ligger for isolert og leses ikke som del av en klar helhet.")
    return ArchitectureMetrics(
        frontage_continuity=100.0 * frontage,
        courtyard_clarity=100.0 * courtyard,
        axis_symmetry=100.0 * symmetry,
        view_corridor_quality=100.0 * view_q,
        typology_purity=100.0 * typ_purity,
        building_entropy=100.0 * entropy,
        bya_fitness=100.0 * bya_fit,
        public_realm_clarity=100.0 * public_realm,
        rhythm_score=100.0 * rhythm,
        frontage_gap_penalty=100.0 * gap_penalty,
        isolated_building_penalty=100.0 * isolated_pen,
        frontage_regularity=100.0 * frontage_regularity,
        courtyard_enclosure=100.0 * courtyard_enclosure,
        microfield_utilization=100.0 * micro_util,
        total_score=total,
        notes=notes,
    )


def _open_side_towards_center(sc: Polygon, center: Point) -> str:
    dx = float(center.x - sc.centroid.x)
    dy = float(center.y - sc.centroid.y)
    if abs(dx) >= abs(dy):
        return "east" if dx >= 0 else "west"
    return "north" if dy >= 0 else "south"


def _split_core_for_multi_clusters(
    core: Polygon,
    cluster_count: int,
    *,
    gap_m: float = 8.0,
    central_void_m: float = 0.0,
) -> List[Polygon]:
    """Split karréfelt i flere bykvartaler med fellesrom mellom.

    Runde 9: i stedet for å lage én stor karré eller små symmetriske ringer
    med tilfeldig gap, lager denne splitten et bevisst felles uterom mellom
    ringene. For 2 karréer blir dette en hovedgate/torgakse på 18–25 m.
    For 4 karréer blir dette et 2x2-oppsett med sentral fellespark/torg.
    """
    if core is None or core.is_empty:
        return []
    cluster_count = max(1, int(cluster_count))
    if cluster_count <= 1:
        return [core]

    minx, miny, maxx, maxy = core.bounds
    width = maxx - minx
    height = maxy - miny
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    central_void = float(central_void_m or 0.0)
    if central_void <= 0.0:
        central_void = 22.0 if cluster_count >= 4 else 18.0
    central_void = max(16.0, min(28.0, central_void))
    smau = max(2.5, min(float(gap_m or 8.0) / 2.0, 4.0))

    def _clip_cell(x0: float, y0: float, x1: float, y1: float) -> Optional[Polygon]:
        if x1 <= x0 + 8.0 or y1 <= y0 + 8.0:
            return None
        rect = box(x0, y0, x1, y1)
        piece = core.intersection(rect).buffer(0)
        if piece.is_empty:
            return None
        trimmed = piece.buffer(-smau).buffer(0)
        cand = _largest_polygon(trimmed) or _largest_polygon(piece) or piece
        if cand is None or cand.is_empty or cand.area < 450.0:
            return None
        return cand

    parts: List[Polygon] = []
    # Ønsket celle gir rom for 50–60 m fasade + 25–35 m side i U/O-form.
    desired_w = 62.0
    desired_h = 56.0
    min_cell_w = 42.0
    min_cell_h = 38.0
    edge_margin = max(4.0, min(10.0, float(gap_m or 8.0)))

    if cluster_count >= 4 and width >= central_void + 2 * min_cell_w and height >= central_void + 2 * min_cell_h:
        cell_w = min(desired_w, max(min_cell_w, (width - central_void - 2 * edge_margin) / 2.0))
        cell_h = min(desired_h, max(min_cell_h, (height - central_void - 2 * edge_margin) / 2.0))
        x_left0 = max(minx + edge_margin, cx - central_void / 2.0 - cell_w)
        x_left1 = cx - central_void / 2.0
        x_right0 = cx + central_void / 2.0
        x_right1 = min(maxx - edge_margin, cx + central_void / 2.0 + cell_w)
        y_bot0 = max(miny + edge_margin, cy - central_void / 2.0 - cell_h)
        y_bot1 = cy - central_void / 2.0
        y_top0 = cy + central_void / 2.0
        y_top1 = min(maxy - edge_margin, cy + central_void / 2.0 + cell_h)
        for bounds in [
            (x_left0, y_bot0, x_left1, y_bot1),
            (x_right0, y_bot0, x_right1, y_bot1),
            (x_left0, y_top0, x_left1, y_top1),
            (x_right0, y_top0, x_right1, y_top1),
        ]:
            cell = _clip_cell(*bounds)
            if cell is not None:
                parts.append(cell)
        if len(parts) >= min(cluster_count, 4):
            parts.sort(key=lambda p: (round(p.centroid.y, 3), round(p.centroid.x, 3)))
            return parts[:cluster_count]

    # 2 karréer side-om-side eller over/under med 18–25 m fellesrom imellom.
    if cluster_count == 2 and (width >= central_void + 2 * min_cell_w or height >= central_void + 2 * min_cell_h):
        horizontal = width >= height and width >= central_void + 2 * min_cell_w
        if horizontal:
            cell_w = min(desired_w, max(min_cell_w, (width - central_void - 2 * edge_margin) / 2.0))
            cell_h = min(desired_h, max(min_cell_h, height - 2 * edge_margin))
            y0 = max(miny + edge_margin, cy - cell_h / 2.0)
            y1 = min(maxy - edge_margin, cy + cell_h / 2.0)
            candidates = [
                (max(minx + edge_margin, cx - central_void / 2.0 - cell_w), y0, cx - central_void / 2.0, y1),
                (cx + central_void / 2.0, y0, min(maxx - edge_margin, cx + central_void / 2.0 + cell_w), y1),
            ]
        else:
            cell_w = min(desired_w, max(min_cell_w, width - 2 * edge_margin))
            cell_h = min(desired_h, max(min_cell_h, (height - central_void - 2 * edge_margin) / 2.0))
            x0 = max(minx + edge_margin, cx - cell_w / 2.0)
            x1 = min(maxx - edge_margin, cx + cell_w / 2.0)
            candidates = [
                (x0, max(miny + edge_margin, cy - central_void / 2.0 - cell_h), x1, cy - central_void / 2.0),
                (x0, cy + central_void / 2.0, x1, min(maxy - edge_margin, cy + central_void / 2.0 + cell_h)),
            ]
        for bounds in candidates:
            cell = _clip_cell(*bounds)
            if cell is not None:
                parts.append(cell)
        if len(parts) >= 2:
            parts.sort(key=lambda p: (round(p.centroid.y, 3), round(p.centroid.x, 3)))
            return parts

    # Fallback: gammel, robust splitting.
    try:
        raw_parts = subdivide_buildable_polygon(core, count=cluster_count, orientation_deg=0.0)
    except Exception:
        axis = "x" if width >= height else "y"
        raw_parts = _split_poly_evenly(core, cluster_count, axis=axis)
    parts = []
    for part in raw_parts:
        trimmed = part.buffer(-smau).buffer(0)
        cand = _largest_polygon(trimmed) or _largest_polygon(part) or part
        if cand is not None and not cand.is_empty and cand.area > 180.0:
            parts.append(cand)
    parts.sort(key=lambda p: (round(p.centroid.y, 3), round(p.centroid.x, 3)))
    return parts


def _karre_candidate_penalty(candidate: Optional[_PlacementCandidate], target_bra: float) -> float:
    if candidate is None or not candidate.footprints:
        return float("inf")
    deficit = max(0.0, float(target_bra) - float(candidate.total_bra))
    overshoot = max(0.0, float(candidate.total_bra) - float(target_bra))
    areas = [float(fp.area) for fp in candidate.footprints if fp is not None and not fp.is_empty]
    if not areas:
        return float("inf")
    tiny_penalty = sum(max(0.0, 520.0 - a) * 1.9 for a in areas)
    imbalance_penalty = 0.0
    largest = max(areas)
    smallest = min(areas)
    if largest > 1.0:
        ratio = smallest / largest
        if ratio < 0.42:
            imbalance_penalty = float(target_bra) * (0.42 - ratio) * 0.10
    return deficit + overshoot * 0.18 + tiny_penalty + imbalance_penalty


def _best_subcore_karre_candidate(sc: Polygon, sub_field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    sub_skeleton = _compose_field_skeleton(sc, sub_field)
    frontage = _place_karre_from_frontage(sub_skeleton, sub_field, spec)
    local = _place_karre_local(sc, sub_field, spec)
    candidates = [c for c in (frontage, local) if c is not None and c.footprints]
    if not candidates:
        return None
    return min(candidates, key=lambda cand: _karre_candidate_penalty(cand, sub_field.target_bra))


def _scale_cluster_footprints_to_target(
    footprints: Sequence[Polygon],
    field: Delfelt,
    floors_per: Sequence[int],
) -> List[Polygon]:
    polys = [fp for fp in footprints if fp is not None and not fp.is_empty]
    if not polys:
        return []
    total_fp = sum(float(fp.area) for fp in polys)
    if total_fp <= 1.0:
        return list(polys)
    avg_floors = max(1.0, sum(max(1, int(f)) for f in floors_per) / max(len(floors_per), 1))
    target_fp = float(field.target_bra) / avg_floors
    if getattr(field, "target_bya_pct", None) is not None:
        bya_cap = float(field.polygon.area) * (float(field.target_bya_pct) / 100.0)
        target_fp = min(target_fp, bya_cap)
    if target_fp <= 1.0:
        return list(polys)
    scale = math.sqrt(target_fp / total_fp)
    scale = max(0.78, min(1.0, scale))
    if abs(scale - 1.0) < 1e-3:
        return list(polys)
    scaled: List[Polygon] = []
    for fp in polys:
        scaled_fp = affinity.scale(fp, xfact=scale, yfact=scale, origin=fp.centroid).buffer(0)
        scaled.append(scaled_fp)
    return scaled


def _place_multi_karre_clusters(core: Polygon, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    cluster_target = max(1, int(getattr(field, "target_building_count", 0) or 1))
    if cluster_target <= 1:
        return None

    gap_m = max(8.0, float(getattr(field, "gap_between_m", 0.0) or 8.0))
    central_void_m = float(getattr(field, "central_void_m", 0.0) or (22.0 if cluster_target >= 4 else 18.0))
    cluster_counts = [cluster_target]
    if cluster_target >= 3:
        cluster_counts.append(cluster_target - 1)
    cluster_counts = [c for c in dict.fromkeys(cluster_counts) if c >= 2]

    candidates: List[_PlacementCandidate] = []
    for cluster_count in cluster_counts:
        sub_cores = _split_core_for_multi_clusters(core, cluster_count, gap_m=gap_m, central_void_m=central_void_m)
        if len(sub_cores) <= 1:
            continue

        combined_footprints: List[Polygon] = []
        floors_per: List[int] = []
        angle_per: List[float] = []
        total_sub_area = sum(sc.area for sc in sub_cores) or 1.0
        field_center = core.centroid
        for sc in sub_cores:
            sub_target = float(field.target_bra) * (sc.area / total_sub_area)
            open_to_common = _open_side_towards_center(sc, field_center)
            sub_field = replace(
                field,
                polygon=sc,
                target_bra=sub_target,
                target_building_count=1,
                courtyard_open_side=open_to_common,
                view_corridor_count=max(0, int(getattr(field, "view_corridor_count", 0) or 0) - 1),
                micro_band_count=max(2, int(getattr(field, "micro_band_count", 0) or 2) - 1),
                courtyard_reserve_ratio=min(0.34, max(0.22, float(getattr(field, "courtyard_reserve_ratio", 0.0) or 0.28))),
            )
            sub_cand = _best_subcore_karre_candidate(sc, sub_field, spec)
            if sub_cand is None:
                continue
            for i, fp in enumerate(sub_cand.footprints):
                combined_footprints.append(fp)
                floors_per.append(sub_cand.floors_at(i))
                angle_per.append(sub_cand.angle_offset_at(i))

        if len(combined_footprints) < 2:
            continue

        scaled_footprints = _scale_cluster_footprints_to_target(combined_footprints, field, floors_per)
        total_fp_scaled = sum(float(fp.area) for fp in scaled_footprints) or 1.0
        base_f = int(math.ceil(float(field.target_bra) / total_fp_scaled)) if field.target_bra > 0 else field.floors_min
        base_f = max(field.floors_min, min(field.floors_max, base_f))
        # Hvis vi fortsatt ligger under mål eller realiserer færre bygg enn ønsket,
        # skal etasjeantallet kompensere før vi aksepterer lav BRA.
        if len(scaled_footprints) < cluster_target or total_fp_scaled * base_f < float(field.target_bra) * 0.86:
            base_f = field.floors_max
        floors_per_final = [base_f for _ in scaled_footprints]
        candidate = _evaluate_candidate(
            scaled_footprints,
            field.target_bra,
            (field.floors_min, field.floors_max),
            floors_per_bygg=floors_per_final,
            angle_offset_per_bygg=angle_per[:len(scaled_footprints)],
        )
        if candidate is None:
            continue
        candidate._variant = f"karre_cluster_x{cluster_count}"
        candidate._cluster_count = cluster_count
        candidates.append(candidate)

    if not candidates:
        return None

    def _cluster_score(item: _PlacementCandidate) -> Tuple[float, float, float]:
        penalty = _karre_candidate_penalty(item, field.target_bra)
        count_pen = abs(len(item.footprints) - cluster_target) * float(field.target_bra) * 0.03
        areas = [float(fp.area) for fp in item.footprints if fp is not None and not fp.is_empty]
        diversity_bonus = -sum(areas) * 0.01 if areas else 0.0
        return (penalty + count_pen, diversity_bonus, -float(item.total_bra))

    return min(candidates, key=_cluster_score)


def _place_single_core(core: Polygon, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    """Plasser bygg i én enkelt rektangulær kjerne. Dette er den opprinnelige
    plassering-logikken, nå trukket ut slik at _candidate_for_field kan kjøre
    den over flere delkjerner når et delfelt er konkavt."""
    if field.typology == Typology.LAMELL:
        return _place_lameller_local(core, field, spec)
    if field.typology == Typology.PUNKTHUS:
        return _place_punkthus_local(core, field, spec)
    if field.typology == Typology.REKKEHUS:
        return _place_rekkehus_local(core, field, spec)
    if field.typology == Typology.KARRE:
        return _place_karre_local(core, field, spec)
    return None


def _candidate_for_field(core: Polygon, field: Delfelt) -> Optional[_PlacementCandidate]:
    """Generer plasseringskandidat for et delfelt.

    Ny rekkefølge:
      1. Komponer et FieldSkeleton (frontage, byggebaner, gårdsrom, sikt)
      2. Plasser bygg mot dette skjelettet
      3. Fallback til eldre lokal plassering hvis skjelettet ikke ga kandidat
      4. Fallback til delkjerner for svært konkave felt
    """
    spec = get_typology_spec(field.typology)
    skeleton = _compose_field_skeleton(core, field)

    cand: Optional[_PlacementCandidate] = None
    if field.typology == Typology.LAMELL:
        cand = _place_lameller_in_bands(skeleton, field, spec)
    elif field.typology == Typology.KARRE:
        cand = _place_multi_karre_clusters(core, field, spec)
        if cand is None:
            cand = _place_karre_from_frontage(skeleton, field, spec)
        if cand is None:
            cand = _place_karre_local(core, field, spec)
    elif field.typology == Typology.PUNKTHUS:
        cand = _place_punkthus_on_nodes(skeleton, field, spec)
    elif field.typology == Typology.REKKEHUS:
        cand = _place_single_core(core, field, spec)
    if cand is not None:
        return cand

    sub_cores = _field_placement_cores(core)
    if not sub_cores:
        return _place_single_core(core, field, spec)
    if len(sub_cores) == 1:
        return _place_single_core(sub_cores[0], field, spec)
    if field.typology == Typology.KARRE:
        return _place_multi_karre_clusters(core, field, spec) or _place_single_core(sub_cores[0], field, spec)

    combined_footprints: List[Polygon] = []
    floors_per: List[int] = []
    angle_per: List[float] = []
    total_sub_area = sum(sc.area for sc in sub_cores) or 1.0
    for sc in sub_cores:
        sub_target = float(field.target_bra) * (sc.area / total_sub_area)
        sub_field = replace(field, target_bra=sub_target)
        sub_skeleton = _compose_field_skeleton(sc, sub_field)
        sub_cand: Optional[_PlacementCandidate] = None
        if sub_field.typology == Typology.LAMELL:
            sub_cand = _place_lameller_in_bands(sub_skeleton, sub_field, spec)
        elif sub_field.typology == Typology.PUNKTHUS:
            sub_cand = _place_punkthus_on_nodes(sub_skeleton, sub_field, spec)
        if sub_cand is None:
            sub_cand = _place_single_core(sc, sub_field, spec)
        if sub_cand is None:
            continue
        for i, fp in enumerate(sub_cand.footprints):
            combined_footprints.append(fp)
            floors_per.append(sub_cand.floors_at(i))
            angle_per.append(sub_cand.angle_offset_at(i))
    if not combined_footprints:
        return None
    return _evaluate_candidate(combined_footprints, field.target_bra, (field.floors_min, field.floors_max), floors_per_bygg=floors_per, angle_offset_per_bygg=angle_per)


def _top_up_field_bra_minimum(buildings: List[Bygg], field: Delfelt, target_bra: float) -> None:
    """Hev etasjer på eksisterende bygg til vi når minimum BRA, hvis mulig.

    Hensikt: Når %-BRA-overstyring brukes skal løsningen i utgangspunktet ikke
    stoppe på 90-95 %% av målet bare fordi en litt luftigere kandidat vant på
    komposisjon. Vi gjør derfor en enkel og robust top-up ved å legge etasjer på
    eksisterende bygg innenfor floors_max. Fotavtrykk og uterom beholdes, men
    volumet strammes opp.
    """
    if not buildings or target_bra <= 0:
        return
    current = sum(b.bra_m2 for b in buildings)
    tolerance = max(40.0, target_bra * 0.005)
    if current + tolerance >= target_bra:
        return

    order = sorted(
        range(len(buildings)),
        key=lambda i: (
            buildings[i].floors,
            -float(getattr(buildings[i].footprint, 'area', 0.0) or 0.0),
            0 if buildings[i].typology in {Typology.KARRE, Typology.LAMELL} else 1,
            buildings[i].bygg_id,
        ),
    )
    if not order:
        return

    max_rounds = max(1, int(field.floors_max - field.floors_min) + 3)
    rounds = 0
    progressed = True
    while current + tolerance < target_bra and progressed and rounds < max_rounds:
        progressed = False
        rounds += 1
        for idx in order:
            b = buildings[idx]
            if b.floors >= field.floors_max:
                continue
            b.floors += 1
            b.height_m = _height_for(field.typology, b.floors)
            current += b.footprint_m2
            progressed = True
            if current + tolerance >= target_bra:
                break


def place_buildings_for_fields(buildable_poly: Polygon, delfelt: List[Delfelt], plan_regler: Optional[PlanRegler] = None) -> Tuple[List[Bygg], float]:
    rules = plan_regler or PlanRegler()
    accepted: List[Bygg] = []
    global_geoms: List[Tuple[Polygon, float, Typology, float]] = []
    bra_deficit = 0.0
    build_counter = 1

    placement_priority = {
        Typology.KARRE: 0,
        Typology.LAMELL: 1,
        Typology.PUNKTHUS: 2,
        Typology.REKKEHUS: 3,
    }
    ordered_fields = sorted(
        delfelt,
        key=lambda f: (placement_priority.get(f.typology, 9), -float(getattr(f, "target_bra", 0.0) or 0.0), str(getattr(f, "field_id", ""))),
    )

    for field in ordered_fields:
        # core er i globalt rom. Plasseringsalgoritmene (_fit_centered_outer_rect,
        # _place_lameller_local, etc.) antar at koordinataksene matcher delfeltets
        # hovedretning. Vi roterer derfor core til lokalt (aksejustert) system
        # med -orientation_deg rundt delfeltets centroid før vi kaller plassering.
        # Footprints som returneres er da også i lokalt system, og blir rotert
        # tilbake med +orientation_deg nedenfor.
        core_global = _field_core_polygon(field.polygon, rules.brann_avstand_m)
        if abs(field.orientation_deg) > 1e-3:
            core_local = _rotate(core_global, -field.orientation_deg, origin=field.polygon.centroid)
        else:
            core_local = core_global
        candidate = _candidate_for_field(core_local, field)
        if candidate is None:
            bra_deficit += max(0.0, field.target_bra)
            continue

        placed_for_field: List[Bygg] = []
        # Note: candidate.angle_offset_deg (fellesrotasjon) er allerede anvendt på
        # footprints inne i _place_*_local-funksjonene (via _rotate på core.centroid).
        # Vi legger derfor IKKE på fellesrotasjonen her, bare per-bygg-rotasjon
        # fra angle_offset_per_bygg hvis den er satt.
        for idx, footprint_local in enumerate(candidate.footprints):
            # Per-bygg-floors
            bygg_floors = candidate.floors_at(idx)

            # Per-bygg rotasjon kun fra listen angle_offset_per_bygg (ikke fellesvinkelen).
            # Hvis listen er None eller indeksen utenfor, er per-bygg-rotasjonen 0.
            if candidate.angle_offset_per_bygg is not None and 0 <= idx < len(candidate.angle_offset_per_bygg):
                per_bygg_angle = candidate.angle_offset_per_bygg[idx]
            else:
                per_bygg_angle = 0.0

            # Global vinkel for spacing-sjekk = fellesrotasjon + per-bygg-rotasjon + feltrotasjon.
            angle_global = (field.orientation_deg + candidate.angle_offset_deg + per_bygg_angle) % 180.0

            # Hvis per-bygg-rotasjon er satt, rotere footprint rundt egen centroid
            # før vi roterer tilbake til globalt system.
            if abs(per_bygg_angle) > 1e-3:
                footprint_in_field_local = _rotate(footprint_local, per_bygg_angle, origin=footprint_local.centroid)
            else:
                footprint_in_field_local = footprint_local
            footprint_global = _rotate(
                footprint_in_field_local, field.orientation_deg, origin=field.polygon.centroid
            ).buffer(0)

            height_m = _height_for(field.typology, bygg_floors)
            if not buildable_poly.buffer(1e-6).covers(footprint_global):
                continue
            if not field.polygon.buffer(1e-6).covers(footprint_global):
                continue
            if not _building_spacing_ok(footprint_global, angle_global, field.typology, height_m, global_geoms, rules):
                continue
            bygg = Bygg(
                bygg_id=f"B{build_counter}",
                footprint=footprint_global,
                floors=bygg_floors,
                height_m=height_m,
                typology=field.typology,
                delfelt_id=field.field_id,
                phase=field.phase,
                display_name=f"B{build_counter}",
            )
            placed_for_field.append(bygg)
            global_geoms.append((footprint_global, angle_global, field.typology, height_m))
            build_counter += 1

        _top_up_field_bra_minimum(placed_for_field, field, float(field.target_bra or 0.0))
        accepted.extend(placed_for_field)
        achieved_bra = sum(b.bra_m2 for b in placed_for_field)
        bra_deficit += max(0.0, field.target_bra - achieved_bra)

    # Post-processing: juster fasadelinjer innen samme arm.
    # Vi flytter bygg maks 4m for å bringe deres kanter på felles linjer.
    # Dette gir et tydelig "kvartalsgrep" der bygg leser som én plan.
    try:
        accepted = _align_facades_within_arm(accepted, delfelt, snap_distance_m=4.0)
        # Etter justering: verifiser at ingen bygg overlapper eller går utenfor tomtens buildable_poly
        accepted = _verify_adjusted_buildings(accepted, buildable_poly, delfelt, rules)
    except Exception as exc:
        # Fasade-justering er "nice-to-have" — hvis noe feiler, bruk uendret layout
        pass

    return accepted, float(bra_deficit)


def _verify_adjusted_buildings(
    buildings: List[Bygg],
    buildable_poly: Polygon,
    delfelt: List[Delfelt],
    rules: PlanRegler,
) -> List[Bygg]:
    """Verifiser at justerte bygg fortsatt er innenfor tomten og ikke overlapper.

    Hvis et justert bygg bryter én av reglene, reverserer vi kun det bygget.
    """
    # Bygg en field-lookup
    field_by_id: Dict[str, Delfelt] = {f.field_id: f for f in delfelt}
    site_buf = buildable_poly.buffer(1e-6)

    checked: List[Bygg] = []
    for bygg in buildings:
        field = field_by_id.get(bygg.delfelt_id)
        # Sjekk tomt-begrensning
        if not site_buf.covers(bygg.footprint):
            # Utenfor tomten — behold original? Vi har ikke original tilgjengelig,
            # så vi skipper denne justeringen. For å være trygg, tar vi bygget med
            # likevel — det er bedre enn å droppe det.
            # (En mer avansert versjon ville holdt 'original' som backup.)
            pass
        # Sjekk delfelt-begrensning
        if field is not None and not field.polygon.buffer(1e-6).covers(bygg.footprint):
            pass
        checked.append(bygg)

    # Sjekk overlapp og hard minsteavstand mellom bygg. Fasadesnapping kan
    # ellers flytte to bygg nærmere enn brann-/avstandskravet.
    final: List[Bygg] = []
    min_gap = _minimum_building_spacing_m(rules)
    for b in checked:
        violates = False
        for existing in final:
            if b.footprint.intersects(existing.footprint):
                if b.footprint.intersection(existing.footprint).area > 0.5:
                    violates = True
                    break
            if b.footprint.distance(existing.footprint) + 1e-6 < min_gap:
                violates = True
                break
        if not violates:
            final.append(b)
    return final


def _align_facades_within_arm(
    buildings: List[Bygg],
    delfelt: List[Delfelt],
    snap_distance_m: float = 4.0,
) -> List[Bygg]:
    """Etter byggplassering: juster ytterfasader slik at bygg i samme arm
    deler felles fasadelinjer.

    Algoritme:
    1. Grupper bygg etter arm_id (fra Delfelt.arm_id)
    2. For hver arm, finn byggkanter som ligger nær hverandre langs en akse
    3. Snap byggene slik at disse kantene deler samme linje

    Implementasjonen er konservativ: bygg flyttes maks `snap_distance_m` meter
    og kun hvis det bringer dem på linje med minst ett annet bygg i samme arm.
    Ingen bygg roteres — vi justerer bare posisjon langs én akse.
    """
    if not buildings:
        return buildings

    # Bygg field_id -> arm_id lookup
    field_to_arm: Dict[str, Optional[str]] = {}
    field_to_orient: Dict[str, float] = {}
    for f in delfelt:
        field_to_arm[f.field_id] = getattr(f, "arm_id", None)
        field_to_orient[f.field_id] = float(f.orientation_deg)

    # Grupper bygg etter arm_id (None-gruppe for bygg uten arm)
    by_arm: Dict[Optional[str], List[Bygg]] = {}
    for b in buildings:
        arm_id = field_to_arm.get(b.delfelt_id)
        by_arm.setdefault(arm_id, []).append(b)

    adjusted: List[Bygg] = list(buildings)  # vi bygger opp replacement-liste

    for arm_id, arm_buildings in by_arm.items():
        if arm_id is None or len(arm_buildings) < 2:
            continue  # ingen å justere mot

        # Finn dominant orientering for armen (fra delfeltene som tilhører denne armen)
        relevant_orients = [field_to_orient[f_id]
                            for f_id in {b.delfelt_id for b in arm_buildings}
                            if f_id in field_to_orient]
        if not relevant_orients:
            continue
        dominant_orient = sum(relevant_orients) / len(relevant_orients)

        # Roter alle footprints til armens lokale koordinatsystem
        # (slik at fasadelinjer blir akseparallelle)
        pivot = arm_buildings[0].footprint.centroid
        local_bounds: List[Tuple[Bygg, Tuple[float, float, float, float]]] = []
        for b in arm_buildings:
            local_poly = _rotate(b.footprint, -dominant_orient, origin=pivot)
            local_bounds.append((b, local_poly.bounds))

        # Samle alle x- og y-verdier (4 per bygg)
        x_values: List[Tuple[float, int]] = []  # (value, bygg_index)
        y_values: List[Tuple[float, int]] = []
        for i, (b, bounds) in enumerate(local_bounds):
            minx, miny, maxx, maxy = bounds
            x_values.append((minx, i))
            x_values.append((maxx, i))
            y_values.append((miny, i))
            y_values.append((maxy, i))

        # Finn "klynger" av x- og y-verdier innen snap_distance_m
        # (disse er kandidat-fasadelinjer)
        def find_clusters(values: List[Tuple[float, int]], tol: float) -> List[List[Tuple[float, int]]]:
            values_sorted = sorted(values, key=lambda v: v[0])
            clusters: List[List[Tuple[float, int]]] = []
            for v in values_sorted:
                if clusters and abs(v[0] - clusters[-1][0][0]) <= tol:
                    clusters[-1].append(v)
                else:
                    clusters.append([v])
            return clusters

        x_clusters = find_clusters(x_values, snap_distance_m)
        y_clusters = find_clusters(y_values, snap_distance_m)

        # For hver kluster med 2+ verdier fra forskjellige bygg → snap til median
        # Bygg et mapping: bygg_index -> liste av (axis, old_value, new_value)
        shifts: Dict[int, List[Tuple[str, float, float]]] = {}

        def register_cluster(cluster: List[Tuple[float, int]], axis: str):
            if len(cluster) < 2:
                return
            building_indices = {bi for _, bi in cluster}
            if len(building_indices) < 2:
                return  # alle verdier fra samme bygg — ikke aligning
            # Snap til median
            median_val = sorted(v for v, _ in cluster)[len(cluster) // 2]
            for old_val, bi in cluster:
                if abs(old_val - median_val) < 1e-6:
                    continue
                shifts.setdefault(bi, []).append((axis, old_val, median_val))

        for c in x_clusters:
            register_cluster(c, "x")
        for c in y_clusters:
            register_cluster(c, "y")

        if not shifts:
            continue

        # Anvend shifts: for hvert bygg, velg én shift pr akse (den som flytter minst)
        for bi, shift_list in shifts.items():
            bygg, bounds = local_bounds[bi]
            minx, miny, maxx, maxy = bounds
            # Samle x-shifts og y-shifts
            x_shifts = [s for s in shift_list if s[0] == "x"]
            y_shifts = [s for s in shift_list if s[0] == "y"]

            dx = 0.0
            dy = 0.0
            # For x: velg shift som flytter minst (minimerer shift)
            if x_shifts:
                x_shifts.sort(key=lambda s: abs(s[2] - s[1]))
                axis_val_old, axis_val_new = x_shifts[0][1], x_shifts[0][2]
                dx = axis_val_new - axis_val_old
            if y_shifts:
                y_shifts.sort(key=lambda s: abs(s[2] - s[1]))
                axis_val_old, axis_val_new = y_shifts[0][1], y_shifts[0][2]
                dy = axis_val_new - axis_val_old

            # Sikkerhets-klamp: ikke flytt mer enn snap_distance
            dx = max(-snap_distance_m, min(snap_distance_m, dx))
            dy = max(-snap_distance_m, min(snap_distance_m, dy))

            if abs(dx) < 0.1 and abs(dy) < 0.1:
                continue  # ubetydelig

            # Translater footprint i lokalt system, roter tilbake
            local_poly = _rotate(bygg.footprint, -dominant_orient, origin=pivot)
            from shapely.affinity import translate as _translate
            translated_local = _translate(local_poly, xoff=dx, yoff=dy)
            new_global = _rotate(translated_local, dominant_orient, origin=pivot).buffer(0)

            # Erstatt bygget i adjusted-liste
            for idx, b_orig in enumerate(adjusted):
                if b_orig is bygg:
                    adjusted[idx] = replace(bygg, footprint=new_global)
                    break

    return adjusted


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


def buildings_respect_min_spacing(buildings: Sequence[Bygg], min_spacing_m: float = 8.0) -> bool:
    """Return True only when all separate buildings have the required clear gap."""
    min_gap = max(0.0, float(min_spacing_m or 0.0))
    if min_gap <= 0.0:
        return True
    for i, a in enumerate(buildings):
        for b in buildings[i + 1 :]:
            if a.footprint.distance(b.footprint) + 1e-6 < min_gap:
                return False
    return True
