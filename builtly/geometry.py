from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence

from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

from .masterplan_types import Bygg, ConceptFamily, Delfelt, Typology
from .typology_library import building_height_for_floors, validate_typology_footprint


@dataclass
class GeometryContext:
    area_m2: float
    major_axis_m: float
    orientation_deg: float
    delfelt_count: int


def _snap_angle(theta_deg: float, step: float = 15.0) -> float:
    return (round(theta_deg / step) * step) % 180.0


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


def density_adjusted_delfelt_count(
    area_m2: float,
    major_axis_m: float,
    target_bra_m2: float = 0.0,
    concept_family: ConceptFamily | None = None,
) -> int:
    base = default_delfelt_count(area_m2, major_axis_m)
    if target_bra_m2 <= 0:
        return base

    ratio = target_bra_m2 / max(area_m2, 1.0)
    dense_target = math.ceil(target_bra_m2 / 5_500.0)

    if ratio >= 1.0:
        base = max(base, dense_target)
    elif ratio >= 0.85:
        base = max(base, math.ceil(target_bra_m2 / 6_500.0))

    if concept_family == ConceptFamily.COURTYARD_URBAN and ratio >= 0.9:
        base = max(base, math.ceil(target_bra_m2 / 5_250.0))
    if concept_family == ConceptFamily.LINEAR_MIXED and ratio >= 0.9:
        base = max(base, math.ceil(target_bra_m2 / 5_750.0))

    # Keep very large counts in check for small sites.
    upper_bound = max(2, int(math.ceil(area_m2 / 2_500.0)))
    return max(1, min(base, upper_bound))


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
    return major_axis_m, _snap_angle(theta_deg)


def _grid_dimensions(n: int, width: float, height: float) -> tuple[int, int]:
    aspect = max(width, 1.0) / max(height, 1.0)
    best_score: tuple[float, int, int] | None = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        cell_w = width / cols
        cell_h = height / rows
        cell_aspect = max(cell_w, cell_h) / max(min(cell_w, cell_h), 1.0)
        score = abs(cell_aspect - min(max(aspect, 1 / max(aspect, 1e-6)), 2.0)) + abs(cols * rows - n) * 0.15
        candidate = (score, cols, rows)
        if best_score is None or candidate < best_score:
            best_score = candidate
    assert best_score is not None
    return best_score[1], best_score[2]


def _normalise_piece(piece) -> Polygon | None:
    if piece.is_empty:
        return None
    if isinstance(piece, MultiPolygon):
        polys = [g for g in piece.geoms if g.area > 10.0]
        if not polys:
            return None
        piece = unary_union(polys)
    if isinstance(piece, MultiPolygon):
        piece = max(piece.geoms, key=lambda g: g.area)
    if piece.is_empty or piece.area <= 10.0:
        return None
    return piece


def split_buildable_into_delfelt(
    poly: Polygon,
    delfelt_count: int | None = None,
    orientation_deg: float | None = None,
) -> list[Polygon]:
    major_axis_m, theta_deg = pca_orientation(poly)
    n = max(1, delfelt_count or default_delfelt_count(poly.area, major_axis_m))
    theta_deg = orientation_deg if orientation_deg is not None else theta_deg
    rotated = affinity.rotate(poly, -theta_deg, origin="centroid")
    minx, miny, maxx, maxy = rotated.bounds
    width = max(maxx - minx, 1.0)
    height = max(maxy - miny, 1.0)
    cols, rows = _grid_dimensions(n, width, height)

    pieces: list[Polygon] = []
    # Split into axis-aligned grid cells in rotated space for legible, orthogonal delfelt.
    for r in range(rows):
        for c in range(cols):
            if len(pieces) >= n:
                break
            x0 = minx + width * (c / cols)
            x1 = minx + width * ((c + 1) / cols)
            y0 = miny + height * (r / rows)
            y1 = miny + height * ((r + 1) / rows)
            cell = box(x0 - 0.2, y0 - 0.2, x1 + 0.2, y1 + 0.2)
            piece = _normalise_piece(rotated.intersection(cell))
            if piece is None:
                continue
            pieces.append(affinity.rotate(piece, theta_deg, origin="centroid"))

    if len(pieces) < n:
        # Top up by splitting largest current pieces along their longest local axis.
        while len(pieces) < n and pieces:
            pieces.sort(key=lambda g: g.area, reverse=True)
            largest = pieces.pop(0)
            loc = affinity.rotate(largest, -theta_deg, origin="centroid")
            lx0, ly0, lx1, ly1 = loc.bounds
            if (lx1 - lx0) >= (ly1 - ly0):
                mid = (lx0 + lx1) / 2.0
                halves = [box(lx0 - 0.2, ly0 - 0.2, mid + 0.2, ly1 + 0.2), box(mid - 0.2, ly0 - 0.2, lx1 + 0.2, ly1 + 0.2)]
            else:
                mid = (ly0 + ly1) / 2.0
                halves = [box(lx0 - 0.2, ly0 - 0.2, lx1 + 0.2, mid + 0.2), box(lx0 - 0.2, mid - 0.2, lx1 + 0.2, ly1 + 0.2)]
            new_parts = []
            for half in halves:
                part = _normalise_piece(loc.intersection(half))
                if part is not None:
                    new_parts.append(affinity.rotate(part, theta_deg, origin="centroid"))
            if len(new_parts) >= 2:
                pieces.extend(new_parts)
            else:
                pieces.append(largest)
                break

    if not pieces:
        return [poly]
    pieces.sort(key=lambda g: (g.centroid.y, g.centroid.x))
    return pieces[:n]


def geometry_context(
    poly: Polygon,
    target_bra_m2: float = 0.0,
    concept_family: ConceptFamily | None = None,
) -> GeometryContext:
    major_axis_m, theta_deg = pca_orientation(poly)
    n = density_adjusted_delfelt_count(poly.area, major_axis_m, target_bra_m2=target_bra_m2, concept_family=concept_family)
    return GeometryContext(area_m2=float(poly.area), major_axis_m=float(major_axis_m), orientation_deg=float(theta_deg), delfelt_count=n)


def build_default_fields(
    poly: Polygon,
    target_bra_m2: float = 0.0,
    concept_family: ConceptFamily | None = None,
) -> list[Delfelt]:
    ctx = geometry_context(poly, target_bra_m2=target_bra_m2, concept_family=concept_family)
    delfelt_polys = split_buildable_into_delfelt(poly, delfelt_count=ctx.delfelt_count, orientation_deg=ctx.orientation_deg)
    fields: list[Delfelt] = []
    for idx, piece in enumerate(delfelt_polys, start=1):
        fields.append(
            Delfelt(
                field_id=f"DF{idx}",
                polygon=piece,
                typology=Typology.LAMELL,
                orientation_deg=ctx.orientation_deg,
                floors_min=4,
                floors_max=5,
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


def _centered_positions(span: float, item: float, gap: float, count: int, origin: float) -> list[float]:
    if count <= 0:
        return []
    total = count * item + max(0, count - 1) * gap
    start = origin + max(0.0, (span - total) / 2.0)
    return [start + i * (item + gap) for i in range(count)]


def _lamell_schemes(rotated_field: Polygon, floors: int, target_bra: float, existing: list[Polygon]) -> list[tuple[Polygon, int]]:
    minx, miny, maxx, maxy = rotated_field.bounds
    width = maxx - minx
    height = maxy - miny
    min_spacing = max(8.0, 1.2 * building_height_for_floors(floors))
    schemes: list[list[tuple[Polygon, int]]] = []

    # Scheme A: bars along x-axis.
    for axis in ("x", "y"):
        local: list[tuple[Polygon, int]] = []
        if axis == "x":
            length = max(28.0, min(58.0, width - 8.0))
            if length < 26.0 or height < 18.0:
                continue
            depth = 13.0
            capacity = max(1, int((height - 8.0 + min_spacing) // (depth + min_spacing)))
            desired = max(2, min(capacity, math.ceil(target_bra / max(length * depth * floors, 1.0))))
            ys = _centered_positions(height - 8.0, depth, min_spacing, desired, miny + 4.0)
            x = minx + max(4.0, (width - length) / 2.0)
            for y in ys:
                candidate = _rect(x, y, length, depth)
                if _candidate_ok(candidate, rotated_field, existing + [p for p, _ in local], min_spacing):
                    local.append((candidate, floors))
        else:
            length = max(28.0, min(58.0, height - 8.0))
            if length < 26.0 or width < 18.0:
                continue
            depth = 13.0
            capacity = max(1, int((width - 8.0 + min_spacing) // (depth + min_spacing)))
            desired = max(2, min(capacity, math.ceil(target_bra / max(length * depth * floors, 1.0))))
            xs = _centered_positions(width - 8.0, depth, min_spacing, desired, minx + 4.0)
            y = miny + max(4.0, (height - length) / 2.0)
            for x in xs:
                candidate = _rect(x, y, depth, length)
                if _candidate_ok(candidate, rotated_field, existing + [p for p, _ in local], min_spacing):
                    local.append((candidate, floors))
        if local:
            schemes.append(local)

    if not schemes:
        return []
    return max(schemes, key=lambda s: (sum(p.area * f for p, f in s), len(s)))


def _place_lameller(rotated_field: Polygon, floors: int, target_bra: float, existing: list[Polygon]) -> list[tuple[Polygon, int]]:
    placements = _lamell_schemes(rotated_field, floors, target_bra, existing)
    if placements:
        return placements
    minx, miny, maxx, maxy = rotated_field.bounds
    candidate = _rect(minx + 4.0, miny + 4.0, max(24.0, min(40.0, maxx - minx - 8.0)), 13.0)
    if _candidate_ok(candidate, rotated_field, existing, 8.0):
        return [(candidate, floors)]
    return []


def _place_punkthus(rotated_field: Polygon, floors: int, target_bra: float, size: float, existing: list[Polygon]) -> list[tuple[Polygon, int]]:
    minx, miny, maxx, maxy = rotated_field.bounds
    width = maxx - minx
    height = maxy - miny
    min_spacing = max(15.0, 0.8 * building_height_for_floors(floors))
    desired = max(2, math.ceil(target_bra / max(size * size * floors, 1.0)))
    max_cols = max(1, int((width - 8.0 + min_spacing) // (size + min_spacing)))
    max_rows = max(1, int((height - 8.0 + min_spacing) // (size + min_spacing)))
    capacity = max_cols * max_rows
    count = min(desired, capacity)
    if count <= 0:
        return []
    cols = min(max_cols, math.ceil(math.sqrt(count)))
    rows = min(max_rows, math.ceil(count / cols))
    xs = _centered_positions(width - 8.0, size, min_spacing, cols, minx + 4.0)
    ys = _centered_positions(height - 8.0, size, min_spacing, rows, miny + 4.0)

    outputs: list[tuple[Polygon, int]] = []
    for y in ys:
        for x in xs:
            if len(outputs) >= count:
                break
            candidate = _rect(x, y, size, size)
            if _candidate_ok(candidate, rotated_field, existing + [p for p, _ in outputs], min_spacing):
                outputs.append((candidate, floors))
    return outputs


def _place_rekkehus(rotated_field: Polygon, floors: int, target_bra: float, existing: list[Polygon]) -> list[tuple[Polygon, int]]:
    minx, miny, maxx, maxy = rotated_field.bounds
    width = maxx - minx
    height = maxy - miny
    depth = 8.0
    length = max(24.0, min(36.0, width * 0.55))
    min_spacing = 8.0
    outputs: list[tuple[Polygon, int]] = []
    desired = max(2, math.ceil(target_bra / max(length * depth * floors, 1.0)))
    ys = _centered_positions(height - 8.0, depth, min_spacing, min(desired, max(1, int((height - 8.0 + min_spacing) // (depth + min_spacing)))), miny + 4.0)
    x = minx + 4.0
    for y in ys:
        candidate = _rect(x, y, length, depth)
        if _candidate_ok(candidate, rotated_field, existing + [p for p, _ in outputs], min_spacing):
            outputs.append((candidate, floors))
    return outputs


def _single_karre_block(x0: float, y0: float, x1: float, y1: float, segment_d: float) -> list[Polygon]:
    return [
        box(x0, y1 - segment_d, x1, y1),
        box(x0, y0, x1, y0 + segment_d),
        box(x0, y0 + segment_d, x0 + segment_d, y1 - segment_d),
        box(x1 - segment_d, y0 + segment_d, x1, y1 - segment_d),
    ]


def _place_karre(rotated_field: Polygon, floors: int, target_bra: float, existing: list[Polygon]) -> list[tuple[Polygon, int]]:
    minx, miny, maxx, maxy = rotated_field.bounds
    width = maxx - minx
    height = maxy - miny
    segment_d = 13.0
    min_spacing = max(8.0, 1.0 * building_height_for_floors(floors))
    outputs: list[tuple[Polygon, int]] = []

    block_count = 2 if max(width, height) > 95 and min(width, height) > 45 and target_bra > 6_500 else 1
    if block_count == 1 and (width < 42 or height < 42):
        return _place_lameller(rotated_field, floors, target_bra, existing)

    if width >= height:
        block_w = min((width - 8.0 - (block_count - 1) * min_spacing) / block_count, 70.0)
        block_h = height - 8.0
        xs = _centered_positions(width - 8.0, block_w, min_spacing, block_count, minx + 4.0)
        ys = [miny + 4.0]
    else:
        block_w = width - 8.0
        block_h = min((height - 8.0 - (block_count - 1) * min_spacing) / block_count, 70.0)
        xs = [minx + 4.0]
        ys = _centered_positions(height - 8.0, block_h, min_spacing, block_count, miny + 4.0)

    for y in ys:
        for x in xs:
            x1 = x + block_w
            y1 = y + block_h
            if block_w < 42 or block_h < 42:
                continue
            courtyard_w = block_w - 2 * segment_d
            courtyard_h = block_h - 2 * segment_d
            if courtyard_w < 18 or courtyard_h < 18:
                continue
            local_rects: list[tuple[Polygon, int]] = []
            existing_geoms = existing + [p for p, _ in outputs]
            for candidate in _single_karre_block(x, y, x1, y1, segment_d):
                if candidate.is_empty or not candidate.is_valid or not candidate.within(rotated_field.buffer(1e-6)):
                    continue
                if any(candidate.distance(other) < min_spacing for other in existing_geoms):
                    local_rects = []
                    break
                local_rects.append((candidate, floors))
            outputs.extend(local_rects)

    if outputs:
        achieved = sum(poly.area * poly_floors for poly, poly_floors in outputs)
        if achieved < target_bra * 0.85:
            infill = _place_lameller(rotated_field, max(3, floors - 1), max(0.0, target_bra - achieved), existing + [p for p, _ in outputs])
            for poly, poly_floors in infill:
                if all(poly.distance(existing_poly) >= 8.0 for existing_poly, _ in outputs):
                    outputs.append((poly, poly_floors))
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
