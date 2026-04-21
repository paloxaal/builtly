
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import split, unary_union

from .masterplan_types import Bygg, Delfelt, Typology
from .typology_library import building_height_for_floors, validate_typology_footprint


@dataclass
class GeometryContext:
    area_m2: float
    major_axis_m: float
    orientation_deg: float
    delfelt_count: int


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


def pca_orientation(poly: Polygon) -> tuple[float, float]:
    coords = list(poly.exterior.coords)[:-1]
    if len(coords) < 2:
        return 0.0, 0.0
    cx = sum(x for x, _ in coords) / len(coords)
    cy = sum(y for _, y in coords) / len(coords)
    xx = yy = xy = 0.0
    for x, y in coords:
        dx = x - cx
        dy = y - cy
        xx += dx * dx
        yy += dy * dy
        xy += dx * dy
    if abs(xy) < 1e-9 and abs(xx - yy) < 1e-9:
        theta = 0.0
    else:
        theta = 0.5 * math.atan2(2.0 * xy, xx - yy)
    theta_deg = (math.degrees(theta) + 180.0) % 180.0
    rot = affinity.rotate(poly, -theta_deg, origin="centroid")
    minx, miny, maxx, maxy = rot.bounds
    major_axis_m = max(maxx - minx, maxy - miny)
    if (maxy - miny) > (maxx - minx):
        theta_deg = (theta_deg + 90.0) % 180.0
    return major_axis_m, theta_deg


def convex_hull_ratio(poly: Polygon) -> float:
    hull_area = poly.convex_hull.area
    if hull_area <= 0:
        return 1.0
    return float(poly.area / hull_area)


def split_buildable_into_delfelt(poly: Polygon) -> list[Polygon]:
    major_axis_m, theta_deg = pca_orientation(poly)
    n = default_delfelt_count(poly.area, major_axis_m)
    rotated = affinity.rotate(poly, -theta_deg, origin="centroid")
    minx, miny, maxx, maxy = rotated.bounds
    width = maxx - minx
    height = maxy - miny
    pieces: list[Polygon] = []

    ratio = convex_hull_ratio(poly)
    # Prefer near-square grid fields for urban concepts on reasonably compact sites.
    if ratio >= 0.82 and n >= 4 and major_axis_m < 260:
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)
        for r in range(rows):
            for c in range(cols):
                if len(pieces) >= n:
                    break
                x0 = minx + width * (c / cols)
                x1 = minx + width * ((c + 1) / cols)
                y0 = miny + height * (r / rows)
                y1 = miny + height * ((r + 1) / rows)
                cell = box(x0 - 0.1, y0 - 0.1, x1 + 0.1, y1 + 0.1)
                piece = rotated.intersection(cell)
                if piece.is_empty:
                    continue
                if isinstance(piece, MultiPolygon):
                    piece = unary_union([g for g in piece.geoms if g.area > 8.0])
                if piece.is_empty:
                    continue
                if isinstance(piece, MultiPolygon):
                    piece = max(piece.geoms, key=lambda g: g.area)
                if piece.area > 20:
                    pieces.append(affinity.rotate(piece, theta_deg, origin="centroid"))
        if len(pieces) >= n:
            return pieces[:n]

    # Fallback to strips for long/slender sites.
    strips: list[Polygon] = []
    for i in range(n):
        x0 = minx + width * (i / n)
        x1 = minx + width * ((i + 1) / n)
        slice_box = box(x0 - 0.1, miny - 10.0, x1 + 0.1, maxy + 10.0)
        piece = rotated.intersection(slice_box)
        if piece.is_empty:
            continue
        if isinstance(piece, MultiPolygon):
            piece = unary_union([g for g in piece.geoms if g.area > 1.0])
        if piece.is_empty:
            continue
        if isinstance(piece, MultiPolygon):
            piece = max(piece.geoms, key=lambda g: g.area)
        if piece.area > 10:
            strips.append(affinity.rotate(piece, theta_deg, origin="centroid"))
    if strips:
        return strips[:n]
    return [poly]


def geometry_context(poly: Polygon) -> GeometryContext:
    major_axis_m, theta_deg = pca_orientation(poly)
    n = default_delfelt_count(poly.area, major_axis_m)
    return GeometryContext(area_m2=float(poly.area), major_axis_m=float(major_axis_m), orientation_deg=float(theta_deg), delfelt_count=n)


def build_default_fields(poly: Polygon) -> list[Delfelt]:
    ctx = geometry_context(poly)
    delfelt_polys = split_buildable_into_delfelt(poly)
    fields: list[Delfelt] = []
    for idx, piece in enumerate(delfelt_polys, start=1):
        fields.append(
            Delfelt(
                field_id=f"DF{idx}",
                polygon=piece,
                typology=Typology.LAMELL,
                orientation_deg=ctx.orientation_deg,
                floors_min=4,
                floors_max=6,
                target_bra=0.0,
                phase=idx,
                phase_label=f"Trinn {idx}",
            )
        )
    return fields


def _candidate_ok(candidate: Polygon, container: Polygon, existing: Sequence[Polygon], min_spacing: float) -> bool:
    if candidate.is_empty or not candidate.is_valid:
        return False
    if not candidate.within(container.buffer(1e-6)):
        return False
    for other in existing:
        if candidate.distance(other) < min_spacing:
            return False
    return True


def _rect(x: float, y: float, w: float, h: float) -> Polygon:
    return box(x, y, x + w, y + h)


def _place_lameller(rotated_field: Polygon, floors: int, target_bra: float, existing: list[Polygon]) -> list[tuple[Polygon, int]]:
    minx, miny, maxx, maxy = rotated_field.bounds
    width = maxx - minx
    height = maxy - miny
    depth = 13.0
    length = max(30.0, min(60.0, width * 0.7))
    min_spacing = max(8.0, 1.2 * building_height_for_floors(floors))
    step_y = depth + min_spacing
    rows = max(1, int((height - depth) // step_y) + 1)
    outputs: list[tuple[Polygon, int]] = []
    achieved = 0.0
    for row in range(rows):
        y = miny + 4.0 + row * step_y
        x = minx + max(4.0, (width - length) / 2.0)
        candidate = _rect(x, y, length, depth)
        if _candidate_ok(candidate, rotated_field, existing + [p for p, _ in outputs], min_spacing):
            outputs.append((candidate, floors))
            achieved += candidate.area * floors
            if achieved >= target_bra * 0.92:
                break
    if not outputs:
        candidate = _rect(minx + 4.0, miny + 4.0, min(max(40.0, width - 8.0), 60.0), min(depth, max(10.0, height - 8.0)))
        if _candidate_ok(candidate, rotated_field, existing, min_spacing):
            outputs.append((candidate, floors))
    return outputs


def _place_punkthus(rotated_field: Polygon, floors: int, target_bra: float, size: float, existing: list[Polygon]) -> list[tuple[Polygon, int]]:
    minx, miny, maxx, maxy = rotated_field.bounds
    width = maxx - minx
    height = maxy - miny
    min_spacing = max(15.0, 0.8 * building_height_for_floors(floors))
    outputs: list[tuple[Polygon, int]] = []
    achieved = 0.0
    cols = max(1, int(width // (size + min_spacing)))
    rows = max(1, int(height // (size + min_spacing)))
    for r in range(rows):
        for c in range(cols):
            x = minx + 4.0 + c * (size + min_spacing)
            y = miny + 4.0 + r * (size + min_spacing)
            candidate = _rect(x, y, size, size)
            if _candidate_ok(candidate, rotated_field, existing + [p for p, _ in outputs], min_spacing):
                outputs.append((candidate, floors))
                achieved += candidate.area * floors
                if achieved >= target_bra * 0.92:
                    return outputs
    return outputs


def _place_rekkehus(rotated_field: Polygon, floors: int, target_bra: float, existing: list[Polygon]) -> list[tuple[Polygon, int]]:
    minx, miny, maxx, maxy = rotated_field.bounds
    width = maxx - minx
    height = maxy - miny
    depth = 8.0
    length = max(24.0, min(42.0, width * 0.55))
    min_spacing = 8.0
    outputs: list[tuple[Polygon, int]] = []
    achieved = 0.0
    step_y = depth + min_spacing
    rows = max(1, int((height - depth) // step_y) + 1)
    for row in range(rows):
        y = miny + 4.0 + row * step_y
        x = minx + 4.0
        candidate = _rect(x, y, length, depth)
        if _candidate_ok(candidate, rotated_field, existing + [p for p, _ in outputs], min_spacing):
            outputs.append((candidate, floors))
            achieved += candidate.area * floors
            if achieved >= target_bra * 0.92:
                break
    return outputs

def _place_karre(rotated_field: Polygon, floors: int, target_bra: float, existing: list[Polygon]) -> list[tuple[Polygon, int]]:
    minx, miny, maxx, maxy = rotated_field.bounds
    width = maxx - minx
    height = maxy - miny
    if width < 42 or height < 42:
        return _place_lameller(rotated_field, floors, target_bra, existing)
    margin = 3.0
    block = box(minx + margin, miny + margin, maxx - margin, maxy - margin)
    bx0, by0, bx1, by1 = block.bounds
    segment_d = 13.0
    courtyard_w = max(18.0, (bx1 - bx0) - 2 * segment_d)
    courtyard_h = max(18.0, (by1 - by0) - 2 * segment_d)
    if courtyard_w < 18 or courtyard_h < 18:
        return _place_lameller(rotated_field, floors, target_bra, existing)
    rects = [
        box(bx0, by1 - segment_d, bx1, by1),            # north
        box(bx0, by0, bx1, by0 + segment_d),            # south
        box(bx0, by0 + segment_d, bx0 + segment_d, by1 - segment_d),  # west
        box(bx1 - segment_d, by0 + segment_d, bx1, by1 - segment_d),  # east
    ]
    outputs: list[tuple[Polygon, int]] = []
    min_spacing = max(8.0, 1.2 * building_height_for_floors(floors))
    for candidate in rects:
        if _candidate_ok(candidate, rotated_field, existing + [p for p, _ in outputs], min_spacing):
            outputs.append((candidate, floors))
    if outputs:
        return outputs
    return _place_lameller(rotated_field, floors, target_bra, existing)


def place_buildings_for_field(field: Delfelt, existing_buildings: Sequence[Bygg] | None = None) -> tuple[list[Bygg], float]:
    existing_buildings = list(existing_buildings or [])
    rotated_field = affinity.rotate(field.polygon, -field.orientation_deg, origin="centroid")
    existing_polys = [affinity.rotate(b.footprint, -field.orientation_deg, origin="centroid") for b in existing_buildings]
    floors = field.floors_max
    placements: list[tuple[Polygon, int]] = []
    if field.typology == Typology.LAMELL:
        placements = _place_lameller(rotated_field, floors, field.target_bra, existing_polys)
    elif field.typology == Typology.PUNKTHUS:
        size = float(field.tower_size_m or 17)
        placements = _place_punkthus(rotated_field, floors, field.target_bra, size, existing_polys)
    elif field.typology == Typology.REKKEHUS:
        floors = min(3, field.floors_max)
        placements = _place_rekkehus(rotated_field, floors, field.target_bra, existing_polys)
    else:
        placements = _place_karre(rotated_field, floors, field.target_bra, existing_polys)

    buildings: list[Bygg] = []
    total_bra = 0.0
    start_idx = len(existing_buildings) + 1
    for idx, (poly, poly_floors) in enumerate(placements, start=start_idx):
        rotated_back = affinity.rotate(poly, field.orientation_deg, origin="centroid")
        if not validate_typology_footprint(rotated_back, field.typology):
            continue
        buildings.append(
            Bygg(
                bygg_id=f"B{idx}",
                footprint=rotated_back,
                floors=poly_floors,
                height_m=building_height_for_floors(poly_floors),
                typology=field.typology,
                delfelt_id=field.field_id,
                phase=field.phase,
            )
        )
        total_bra += rotated_back.area * poly_floors
    return buildings, total_bra
