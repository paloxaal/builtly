from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple
from .masterplan_types import Typology

@dataclass(frozen=True)
class DimRange:
    min_m: float
    max_m: float
    def midpoint(self) -> float:
        return (float(self.min_m) + float(self.max_m)) / 2.0

@dataclass(frozen=True)
class BaseTypologySpec:
    typology: Typology
    length_m: Optional[DimRange] = None
    depth_m: Optional[DimRange] = None
    segment_depth_m: Optional[DimRange] = None
    min_spacing_m: float = 8.0
    allowed_tower_sizes_m: Tuple[int, ...] = (17, 21)
    max_block_length_m: Optional[float] = None
    min_courtyard_side_m: Optional[float] = None

_SPECS = {
    Typology.LAMELL: BaseTypologySpec(Typology.LAMELL, length_m=DimRange(32, 62), depth_m=DimRange(12, 15), min_spacing_m=0.85),
    Typology.PUNKTHUS: BaseTypologySpec(Typology.PUNKTHUS, length_m=DimRange(18, 24), depth_m=DimRange(18, 24), min_spacing_m=10.0, allowed_tower_sizes_m=(17, 21)),
    Typology.REKKEHUS: BaseTypologySpec(Typology.REKKEHUS, length_m=DimRange(5.5, 7.5), depth_m=DimRange(8, 11), min_spacing_m=8.0),
    Typology.KARRE: BaseTypologySpec(Typology.KARRE, length_m=DimRange(38, 65), depth_m=DimRange(38, 65), segment_depth_m=DimRange(12, 15), min_spacing_m=8.0, max_block_length_m=72.0, min_courtyard_side_m=18.0),
}

def get_typology_spec(typology: Typology | str) -> BaseTypologySpec:
    if not isinstance(typology, Typology):
        typology = Typology(str(typology))
    return _SPECS.get(typology, _SPECS[Typology.LAMELL])

def is_axis_aligned_rectilinear(poly, tolerance: float = 1e-6) -> bool:
    if poly is None or getattr(poly, 'is_empty', True):
        return False
    try:
        coords = list(poly.exterior.coords)
    except Exception:
        return False
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        if abs(x1 - x2) > tolerance and abs(y1 - y2) > tolerance:
            return False
    return True
