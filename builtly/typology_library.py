
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Tuple

from shapely.geometry import Polygon

from .masterplan_types import Typology


@dataclass(frozen=True)
class LamellSpec:
    depth_m: Tuple[float, float] = (12.0, 14.0)
    length_m: Tuple[float, float] = (30.0, 60.0)
    min_spacing_height_factor: float = 1.2


@dataclass(frozen=True)
class PunkthusSpec:
    size_m: Literal[17, 21] = 17
    min_spacing_m: float = 15.0


@dataclass(frozen=True)
class KarreSpec:
    segment_depth_m: Tuple[float, float] = (12.0, 15.0)
    min_courtyard_side_m: float = 18.0
    max_block_length_m: float = 70.0


@dataclass(frozen=True)
class RekkehusSpec:
    depth_m: Tuple[float, float] = (7.0, 9.0)
    length_per_unit_m: Tuple[float, float] = (5.0, 7.0)
    floors: Tuple[int, int] = (2, 3)


TYPOLOGY_LIBRARY: Dict[Typology, object] = {
    Typology.LAMELL: LamellSpec(),
    Typology.PUNKTHUS: PunkthusSpec(),
    Typology.KARRE: KarreSpec(),
    Typology.REKKEHUS: RekkehusSpec(),
}


def building_height_for_floors(floors: int) -> float:
    return round(max(1, floors) * 3.1, 1)


def is_orthogonal_rectangle(poly: Polygon, tol: float = 1e-6) -> bool:
    if poly.is_empty or not poly.is_valid:
        return False
    rect = poly.minimum_rotated_rectangle
    return abs(rect.area - poly.area) <= max(tol, poly.area * 0.01)


def aspect_ratio(poly: Polygon) -> float:
    rect = poly.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)
    sides = []
    for i in range(4):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        side = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        sides.append(side)
    sides = sorted(sides)
    if not sides or sides[0] == 0:
        return 0.0
    return sides[-1] / sides[0]


def validate_typology_footprint(poly: Polygon, typology: Typology) -> bool:
    if poly.is_empty or not poly.is_valid:
        return False
    if typology == Typology.LAMELL:
        ratio = aspect_ratio(poly)
        return 2.5 <= ratio <= 5.0
    ratio = aspect_ratio(poly)
    return ratio >= 1.0 and min(poly.bounds[2] - poly.bounds[0], poly.bounds[3] - poly.bounds[1]) > 4.0
