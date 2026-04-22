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
            footprints_varied: List[Polygon] = []
            for off in offsets:
                y0 = miny + off
                rects = _fit_varied_lameller_in_segment(
                    local_poly, y0, depth, spec.length_m.min_m, spec.length_m.max_m,
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

            # Registrer alle varianter som kandidater
            variants_to_register = [
                ("single", footprints_single if valid_single else [], None),
                ("varied", footprints_varied, None),
                ("rotated", footprints_rotated if valid_rotated else [], angles_rotated if valid_rotated else None),
            ]
            for variant, footprints, angles in variants_to_register:
                if not footprints:
                    continue
                if angle_offset == 90.0:
                    footprints = [_rotate(p, 90.0, origin=core.centroid) for p in footprints]
                cand = _evaluate_candidate(
                    footprints, field.target_bra, (field.floors_min, field.floors_max),
                    angle_offset_per_bygg=angles,
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

        # AI-direktiv-bonus: hvis field.design_variant er satt (AI ba om en
        # spesifikk variant), gi en sterk bonus til matchende kandidat. Bonus
        # er 15% av target_bra — stor nok til å overstyre lette variasjons-
        # fordeler, men ikke stor nok til å velge katastrofalt dårligere BRA.
        directive_bonus = 0.0
        design_variant = getattr(field, "design_variant", None)
        if design_variant is not None and variant == design_variant:
            directive_bonus = -(field.target_bra * 0.15)

        return (bra_score + solar_penalty + variation_bonus + directive_bonus, -item.total_bra, len(item.footprints))

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

    Alle returnerte verdier er innenfor [floors_min, floors_max].
    """
    if n_buildings <= 0:
        return []
    clamp = lambda f: max(floors_min, min(floors_max, int(f)))

    if variation == "uniform" or n_buildings == 1:
        return [clamp(base_floors)] * n_buildings

    if variation == "accent":
        # Én aksent-bygg 2 etasjer høyere (hvis mulig)
        accent_idx = n_buildings // 2  # midtbygget
        result = [clamp(base_floors) for _ in range(n_buildings)]
        accent_floors = clamp(base_floors + 2)
        if accent_floors == base_floors:
            # Ikke plass til høyere, prøv lavere baseline i stedet
            new_base = clamp(base_floors - 1)
            if new_base < base_floors:
                result = [clamp(new_base) for _ in range(n_buildings)]
                result[accent_idx] = clamp(base_floors)
        else:
            result[accent_idx] = accent_floors
        return result

    if variation == "stepped":
        # Trappet: fordeler fra base-1 til base+2
        steps = []
        for i in range(n_buildings):
            frac = i / max(1, n_buildings - 1)  # 0 → 1
            floors = base_floors - 1 + int(frac * 3)
            steps.append(clamp(floors))
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
    # gir en sterk bonus til matchende form.
    def karre_score(item: _PlacementCandidate) -> Tuple[float, float]:
        deficit = max(0.0, field.target_bra - item.total_bra)
        overshoot = max(0.0, item.total_bra - field.target_bra)
        bra_score = deficit + overshoot * 0.25
        # Form-preferanse: U/O foretrukket, andre som fallback/variasjon
        form = getattr(item, "_variant", "uo")
        form_penalty = {
            "uo": 0.0,
            "l": field.target_bra * 0.08,
            "t": field.target_bra * 0.05,
            "z": field.target_bra * 0.06,
        }.get(form, 0.0)
        # AI-direktiv-bonus
        ai_shape = getattr(field, "design_karre_shape", None)
        directive_bonus = -(field.target_bra * 0.15) if ai_shape is not None and form == ai_shape else 0.0
        return (bra_score + form_penalty + directive_bonus, -item.total_bra)

    return min(placement_candidates, key=karre_score)


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
