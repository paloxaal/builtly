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

    Hvis delfeltets core-polygon er konkavt (L-form, T-form, innhakk) vil
    plasseringen direkte ofte feile fordi indre _fit_centered_outer_rect og
    tilsvarende hjelpere antar et rektangulært domene. Vi dekomponerer da
    kjernen i aksejusterte rektangler og prøver plassering i hver, og slår
    resultatene sammen.
    """
    spec = get_typology_spec(field.typology)

    # Hvis core er (nesten) konveks, bruk den direkte — rask sti.
    if convex_hull_ratio(core) >= 0.90:
        return _place_single_core(core, field, spec)

    # Konkav core: dekomponer i rektangler og plasser i hver.
    sub_cores = _field_placement_cores(core)
    if not sub_cores:
        return _place_single_core(core, field, spec)

    # Hvis dekomposition gir bare én kjerne, kjør vanlig plassering på den.
    if len(sub_cores) == 1:
        return _place_single_core(sub_cores[0], field, spec)

    # Karré trenger et sammenhengende rektangulært område for ring/U-form;
    # kjør bare plassering i den største delkjernen, ikke flere.
    if field.typology == Typology.KARRE:
        return _place_single_core(sub_cores[0], field, spec)

    # For Lamell / Punkthus / Rekkehus: tillatt å plassere bygg i flere
    # delkjerner og slå sammen footprintene til én kandidat. Vi bruker
    # samme etasje-antall på alle bygg for å holde kandidaten sammenhengende.
    combined_footprints: List[Polygon] = []
    chosen_floors: Optional[int] = None
    proportional_target_total = float(field.target_bra)
    total_sub_area = sum(sc.area for sc in sub_cores) or 1.0

    for sc in sub_cores:
        # Pro rata target_bra per delkjerne så hver får rimelig andel
        sub_target = proportional_target_total * (sc.area / total_sub_area)
        sub_field = replace(field, target_bra=sub_target)  # type: ignore[name-defined]
        sub_cand = _place_single_core(sc, sub_field, spec)
        if sub_cand is None:
            continue
        if chosen_floors is None:
            chosen_floors = sub_cand.floors
        combined_footprints.extend(sub_cand.footprints)

    if not combined_footprints or chosen_floors is None:
        return None

    cand = _evaluate_candidate(combined_footprints, field.target_bra, (field.floors_min, field.floors_max))
    if cand is None:
        return None
    return cand


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
