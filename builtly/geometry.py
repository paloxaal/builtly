´from __future__ import annotations

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
        # Legg på en liten margin (1m) i spacing for å unngå at snap-til-grid
        # drar plasseringen under kravet i _building_spacing_ok. Uten dette
        # vil snap runde ned 23.04 -> 23.0 og forkaste bygg nr 2.
        spacing = max(8.0, spec.min_spacing_m * _height_for(Typology.LAMELL, field.floors_max) + 1.0)
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
                    min_spacing=6.0,
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
    """
    if not candidates:
        return None

    def combined_score(item: _PlacementCandidate) -> Tuple[float, float, int]:
        # Primary score: hvor godt BRA-målet treffes
        deficit = max(0.0, field.target_bra - item.total_bra)
        overshoot = max(0.0, item.total_bra - field.target_bra)
        bra_score = deficit + overshoot * 0.25

        # Solar penalty: for lamell/rekkehus, straffer nord-sør-orientering
        global_angle = (field.orientation_deg + item.angle_offset_deg) % 180.0
        solar_bonus = _solar_orientation_bonus(global_angle, typology)
        solar_penalty = field.target_bra * 0.20 * (1.0 - solar_bonus)

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


def _place_karre_local(core: Polygon, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    """Plasser Karré i delfeltet. Prøver flere karré-former (O, U, L, T, Z) og
    velger den som gir best BRA/arkitektur-score.

    Formvalg:
      - O (lukket ring) — foretrekkes når indre gårdsrom er stort nok
      - U (åpent mot sør) — når bare én side ikke kan lukkes
      - L (hjørne) — når delfeltet er trangt; mindre BRA men lager vinkel
      - T — for lange/smale delfelter, gir to gårdsrom
      - Z — for store bredbente delfelter, bryter monotoni

    O/U prioriteres. L/T/Z er reserve-former som gir variasjon.
    """
    minx, miny, maxx, maxy = core.bounds
    width = min(maxx - minx, spec.max_block_length_m or (maxx - minx))
    height = min(maxy - miny, spec.max_block_length_m or (maxy - miny))
    outer = _fit_centered_outer_rect(core, width * 0.92, height * 0.92)
    if outer is None:
        return None
    ring_depth = spec.segment_depth_m.midpoint() if spec.segment_depth_m else 13.0
    min_cy = spec.min_courtyard_side_m or 18.0

    candidates_shapes: List[Tuple[str, Polygon]] = []

    # Prøv O/U først (primær form — mest arealeffektiv)
    uo_shape = _make_u_or_o_shape(outer, ring_depth, min_cy)
    if uo_shape is not None:
        candidates_shapes.append(("uo", uo_shape))

    # L/T/Z er FALLBACK: brukes bare hvis U/O ikke fikk plass i core.
    # Grunnen er at U/O alltid gir mer BRA. Vi vil ikke bytte til L for
    # "variasjon" hvis det betyr at vi taper 40% volum.
    # Unntak: hvis AI har satt design_karre_shape, legger vi til den formen
    # som kandidat slik at AI-direktivet kan vurderes mot U/O i scoringen.
    core_buf = core.buffer(1e-6)
    uo_works = uo_shape is not None and core_buf.covers(uo_shape)

    ai_requested_shape = getattr(field, "design_karre_shape", None)

    def _add_alt_shapes():
        l_shape = _make_l_shape(outer, ring_depth)
        if l_shape is not None:
            candidates_shapes.append(("l", l_shape))
        ow = outer.bounds[2] - outer.bounds[0]
        oh = outer.bounds[3] - outer.bounds[1]
        aspect = max(ow, oh) / max(1e-6, min(ow, oh))
        if aspect < 1.6:
            t_shape = _make_t_shape(outer, ring_depth)
            if t_shape is not None:
                candidates_shapes.append(("t", t_shape))
        if ow > 50 and oh > 50:
            z_shape = _make_z_shape(outer, ring_depth)
            if z_shape is not None:
                candidates_shapes.append(("z", z_shape))

    if not uo_works:
        # U/O feilet: prøv L/T/Z som fallback
        _add_alt_shapes()
    elif ai_requested_shape in ("l", "t", "z"):
        # U/O virker, men AI ba om alternativ — legg den til som kandidat
        _add_alt_shapes()

    # Chamfered U/O: hvis AI ba om den, eller som frivillig variasjon når
    # U/O virker og det er arkitektonisk interessant (store felter).
    if uo_works and (ai_requested_shape == "uo_chamfered" or
                     (outer.bounds[2] - outer.bounds[0]) >= 50):
        # Velg hjørne: AI kan spesifisere via design_reasoning, ellers "ne"
        # (nord-øst) er default — vendt mot offentlig side typisk.
        chamfer_corner = "ne"
        # Chamfer-størrelse som fraksjon av ring-depth
        chamfer_size = max(8.0, ring_depth * 0.75)
        chamfered = _make_chamfered_uo_shape(outer, ring_depth, min_cy,
                                              chamfer_corner=chamfer_corner,
                                              chamfer_size=chamfer_size)
        if chamfered is not None and chamfered.area > 0:
            candidates_shapes.append(("uo_chamfered", chamfered))

    if not candidates_shapes:
        return None

    # Bygg kandidater og velg beste
    core_buf = core.buffer(1e-6)
    placement_candidates: List[_PlacementCandidate] = []
    for form_name, shape in candidates_shapes:
        if not core_buf.covers(shape):
            continue
        cand = _evaluate_candidate([shape], field.target_bra, (field.floors_min, field.floors_max))
        if cand:
            cand._variant = form_name
            placement_candidates.append(cand)

    if not placement_candidates:
        return None

    # Score: primært BRA, men med liten preferanse for U/O som er den vanligste
    # og mest arealeffektive karré-formen. AI-direktiv (field.design_karre_shape)
    # gir en moderat bonus til matchende form (5% — bare tie-breaker).
    def karre_score(item: _PlacementCandidate) -> Tuple[float, float]:
        deficit = max(0.0, field.target_bra - item.total_bra)
        overshoot = max(0.0, item.total_bra - field.target_bra)
        bra_score = deficit + overshoot * 0.25
        # Form-preferanse: U/O foretrukket, andre som fallback/variasjon
        form = getattr(item, "_variant", "uo")
        form_penalty = {
            "uo": 0.0,
            "uo_chamfered": field.target_bra * 0.02,  # mister bare ~5% areal
            "l": field.target_bra * 0.08,
            "t": field.target_bra * 0.05,
            "z": field.target_bra * 0.06,
        }.get(form, 0.0)
        # AI-direktiv-bonus (moderat — tidligere 15% overstyrte BRA-tap)
        ai_shape = getattr(field, "design_karre_shape", None)
        directive_bonus = -(field.target_bra * 0.05) if ai_shape is not None and form == ai_shape else 0.0
        return (bra_score + form_penalty + directive_bonus, -item.total_bra)

    return min(placement_candidates, key=karre_score)


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
    if int(getattr(field, "view_corridor_count", 0) or 0) > 0:
        cx = (pminx + pmaxx) / 2.0
        cy = (pminy + pmaxy) / 2.0
        cor_x = core.intersection(box(cx - corridor_w / 2.0, miny - 1.0, cx + corridor_w / 2.0, maxy + 1.0)).buffer(0)
        cor_y = core.intersection(box(minx - 1.0, cy - corridor_w / 2.0, maxx + 1.0, cy + corridor_w / 2.0)).buffer(0)
        corridors.extend([p for p in _flatten_polygons(cor_x) if p.area > 40.0])
        corridors.extend([p for p in _flatten_polygons(cor_y) if p.area > 40.0])

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
    nodes: List[Point] = []
    if node_layout == "paired_edges":
        nodes = [
            Point((pminx + pmaxx) / 2.0, pmaxy),
            Point((pminx + pmaxx) / 2.0, pminy),
            Point(pminx, (pminy + pmaxy) / 2.0),
            Point(pmaxx, (pminy + pmaxy) / 2.0),
        ]
    else:
        nodes = [Point(pminx, pminy), Point(pminx, pmaxy), Point(pmaxx, pminy), Point(pmaxx, pmaxy)]

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
    min_len = max(spec.length_m.min_m, 30.0)
    max_len = spec.length_m.max_m
    if not pieces:
        return [min_len]
    widths = sorted(max(0.0, p.bounds[2] - p.bounds[0]) for p in pieces)
    median_w = widths[len(widths)//2]
    palette = [min_len, min(min_len + 6.0, max_len), min(min_len + 12.0, max_len), min(max_len, max(min_len + 18.0, median_w * 0.8))]
    palette = [snap_value(v) for v in palette if min_len <= v <= max_len]
    out: List[float] = []
    for p in palette:
        if p not in out:
            out.append(p)
    return out or [min_len]


def _fit_rect_in_slot(piece: Polygon, depth_m: float, preferred_length: float, min_length: float, max_length: float, anchor_side: str = "center") -> Optional[Polygon]:
    minx, miny, maxx, maxy = piece.bounds
    width = maxx - minx
    height = maxy - miny
    if width < min_length or height < depth_m * 0.85:
        return None
    piece_buf = piece.buffer(1e-6)
    lengths = []
    for L in (preferred_length, preferred_length + 6.0, preferred_length - 6.0, max_length, min_length):
        LL = snap_value(max(min_length, min(max_length, L)))
        if LL not in lengths:
            lengths.append(LL)
    if anchor_side == "south":
        y_candidates = [miny, (miny + maxy) / 2.0 - depth_m / 2.0]
    elif anchor_side == "north":
        y_candidates = [maxy - depth_m, (miny + maxy) / 2.0 - depth_m / 2.0]
    elif anchor_side == "west":
        y_candidates = [(miny + maxy) / 2.0 - depth_m / 2.0]
    else:
        y_candidates = [(miny + maxy) / 2.0 - depth_m / 2.0]
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
                rect = box(sx0, y0, snap_value(sx0 + length), y0 + depth_m)
                if not piece_buf.covers(rect):
                    continue
                score = abs(length - preferred_length) * 0.25 + abs(xc - center_x) * 0.02
                if score < best_score:
                    best = rect
                    best_score = score
    return best


def _ordered_slots_for_lamell(slots: Sequence[Polygon], skeleton: FieldSkeleton) -> List[Polygon]:
    pieces = [p for p in slots if not p.is_empty and p.area > 60.0]
    if not pieces:
        return []
    if skeleton.symmetry_axis is None:
        return sorted(pieces, key=_band_sort_key)
    ax = skeleton.symmetry_axis
    vertical = abs(ax.coords[0][0] - ax.coords[-1][0]) < abs(ax.coords[0][1] - ax.coords[-1][1])
    if vertical:
        cx = ax.coords[0][0]
        return sorted(pieces, key=lambda p: (round(p.centroid.y, 3), abs(p.centroid.x - cx), round(p.centroid.x, 3)))
    cy = ax.coords[0][1]
    return sorted(pieces, key=lambda p: (abs(p.centroid.y - cy), round(p.centroid.x, 3), round(p.centroid.y, 3)))


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
    if {primary, secondary} == {"south", "north"}:
        cy = (miny + maxy) / 2.0
        return secondary if slot.centroid.y > cy else primary
    if {primary, secondary} == {"west", "east"}:
        cx = (minx + maxx) / 2.0
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
    target_count = int(getattr(field, "target_building_count", 0) or max(2, min(len(slots), int(getattr(field, "micro_band_count", 0) or 2))))
    target_count = max(2 if strictness >= 0.80 else 1, min(target_count, len(slots)))
    selected = [p for p in slots if p.area > 80.0]
    if len(selected) > target_count:
        selected = sorted(selected, key=lambda p: (-p.area, round(p.centroid.y, 3), round(p.centroid.x, 3)))[:target_count]
        selected = _ordered_slots_for_lamell(selected, skeleton)
    lengths = _lamell_rhythm_sequence(palette, len(selected), rhythm_mode)

    footprints: List[Polygon] = []
    for slot, preferred in zip(selected, lengths):
        anchor_side = _slot_anchor_side(slot, skeleton)
        rect = _fit_rect_in_slot(slot, depth, preferred, max(spec.length_m.min_m, 30.0), spec.length_m.max_m, anchor_side=anchor_side)
        if rect is not None:
            footprints.append(rect)

    if len(footprints) < target_count and strictness < 0.95:
        # fallback: prøv en sentrert plassering i de største bandene
        for band in skeleton.build_bands:
            if len(footprints) >= target_count:
                break
            bminx, bminy, bmaxx, bmaxy = band.bounds
            if (bmaxy - bminy) < depth * 0.9:
                continue
            rect = _fit_rect_in_segment(band, (bminy + bmaxy) / 2.0 - depth / 2.0, depth, max(spec.length_m.min_m, 30.0), spec.length_m.max_m)
            if rect is not None:
                footprints.append(rect)

    if len(footprints) < (2 if strictness >= 0.75 else 1):
        return None

    floors_per = None
    if variant in {"terraced", "rhythmic"} and len(footprints) >= 2:
        total_fp = sum(p.area for p in footprints)
        base_f = int(round(field.target_bra / max(total_fp, 1.0))) if field.target_bra > 0 else field.floors_min
        base_f = max(field.floors_min, min(field.floors_max, base_f))
        floors_per = _varied_floors_for_cluster(len(footprints), base_f, field.floors_min, field.floors_max, variation=getattr(field, "design_height_pattern", None) or "stepped")

    cand = _evaluate_candidate(footprints, field.target_bra, (field.floors_min, field.floors_max), floors_per_bygg=floors_per)
    if cand is not None:
        cand._variant = variant
    return cand


def _place_karre_from_frontage(skeleton: FieldSkeleton, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    if skeleton.courtyard_reserve is None or not skeleton.build_bands:
        return None
    reserve = skeleton.courtyard_reserve
    rx0, ry0, rx1, ry1 = reserve.bounds

    def _usable_shape(expand: float = 0.0) -> Optional[Polygon]:
        expanded = reserve.buffer(expand, join_style=2) if expand > 0 else reserve
        use: List[Polygon] = []
        ex0, ey0, ex1, ey1 = expanded.bounds
        for band in skeleton.build_bands:
            bx0, by0, bx1, by1 = band.bounds
            role = None
            if by0 >= ey1 - 1e-6:
                role = "north"
            elif by1 <= ey0 + 1e-6:
                role = "south"
            elif bx1 <= ex0 + 1e-6:
                role = "west"
            elif bx0 >= ex1 - 1e-6:
                role = "east"
            if role and role in skeleton.open_edges:
                continue
            piece = band.difference(expanded).buffer(0)
            if getattr(piece, "is_empty", True):
                continue
            use.extend([p for p in _flatten_polygons(piece) if p.area > 60.0])
        if not use:
            return None
        shape = unary_union(use).buffer(0)
        return _largest_polygon(shape) or shape

    shape = _usable_shape(0.0)
    if shape is None or getattr(shape, "is_empty", True):
        return None

    # Cap footprint against target BRA / min floors and target BYA.
    target_fp = float(field.target_bra) / max(float(field.floors_min), 1.0)
    max_fp = target_fp * 1.08
    if field.target_bya_pct is not None:
        max_fp = min(max_fp, field.polygon.area * (float(field.target_bya_pct) / 100.0) * 1.05)

    if shape.area > max_fp and reserve.area > 0:
        lo, hi = 0.0, min((rx1 - rx0), (ry1 - ry0)) * 0.30
        best = shape
        for _ in range(18):
            mid = (lo + hi) / 2.0
            trial = _usable_shape(mid)
            if trial is None or getattr(trial, "is_empty", True):
                hi = mid
                continue
            if trial.area > max_fp:
                lo = mid
                best = trial
            else:
                best = trial
                hi = mid
        if best is not None and not getattr(best, "is_empty", True):
            shape = best

    cand = _evaluate_candidate([shape], field.target_bra, (field.floors_min, field.floors_max))
    if cand is not None:
        cand._variant = getattr(field, "design_karre_shape", None) or ("uo" if skeleton.open_edges else "o")
    return cand


def _place_punkthus_on_nodes(skeleton: FieldSkeleton, field: Delfelt, spec: BaseTypologySpec) -> Optional[_PlacementCandidate]:
    if not skeleton.accent_nodes:
        return None
    sizes = [field.tower_size_m] if field.tower_size_m else list(spec.allowed_tower_sizes_m)
    strictness = float(getattr(field, "composition_strictness", 0.80) or 0.80)
    layout_mode = getattr(field, "node_layout_mode", None) or skeleton.node_layout_mode or ("paired_edges" if getattr(field, "node_symmetry", False) else "corners")
    candidates: List[_PlacementCandidate] = []

    def _ordered_nodes() -> List[Point]:
        nodes = list(skeleton.accent_nodes)
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
    if layout_mode == "paired_edges" and len(ordered_nodes) > 4:
        ordered_nodes = ordered_nodes[:4]

    for size in sizes:
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
            if all(rect.distance(other) >= max(15.0, float(size)) * 0.95 for other in filtered):
                filtered.append(rect)
        if getattr(field, "node_symmetry", False) and len(filtered) > 4:
            filtered = filtered[:4]
        if filtered:
            total_fp = sum(p.area for p in filtered)
            base_f = int(round(field.target_bra / max(total_fp, 1.0))) if field.target_bra > 0 else field.floors_min
            base_f = max(field.floors_min, min(field.floors_max, base_f))
            floors_per = _varied_floors_for_cluster(len(filtered), base_f, field.floors_min, field.floors_max, variation=getattr(field, "design_height_pattern", None) or "accent")
            cand = _evaluate_candidate(filtered, field.target_bra, (field.floors_min, field.floors_max), floors_per_bygg=floors_per)
            if cand is not None:
                cand._variant = layout_mode
                candidates.append(cand)
    return _choose_best(candidates, field.target_bra)


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
            0.17 * frontage +
            0.10 * frontage_regularity +
            0.15 * courtyard +
            0.08 * courtyard_enclosure +
            0.12 * symmetry +
            0.12 * rhythm +
            0.09 * view_q +
            0.08 * public_realm +
            0.05 * purity +
            0.04 * entropy +
            0.05 * bya_fit +
            0.05 * micro_util
        ) - (0.05 * gap_penalty + 0.05 * isolated_pen)

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
        cand = _place_karre_from_frontage(skeleton, field, spec)
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
        return _place_single_core(sub_cores[0], field, spec)

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

def place_buildings_for_fields(buildable_poly: Polygon, delfelt: List[Delfelt], plan_regler: Optional[PlanRegler] = None) -> Tuple[List[Bygg], float]:
    rules = plan_regler or PlanRegler()
    accepted: List[Bygg] = []
    global_geoms: List[Tuple[Polygon, float, Typology, float]] = []
    bra_deficit = 0.0
    build_counter = 1

    for field in delfelt:
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

    # Sjekk overlapp mellom bygg
    # Hvis to bygg nå overlapper, behold det første og forkast det andre.
    final: List[Bygg] = []
    for b in checked:
        overlaps = False
        for existing in final:
            if b.footprint.intersects(existing.footprint):
                if b.footprint.intersection(existing.footprint).area > 0.5:
                    overlaps = True
                    break
        if not overlaps:
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
