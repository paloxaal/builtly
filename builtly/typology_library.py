from __future__ import annotations

"""Deterministic typology library for Builtly v8.

This module deliberately contains no AI. It defines allowed dimensions,
aspect ratios and footprint families for the small, closed typology set used
in pass 3.

A deliberate simplifying assumption in v8 delivery 1:
footprints are validated in *local field coordinates* after rotation into the
field orientation. In that coordinate system, legal edges are axis-aligned.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Literal, Optional, Sequence, Set, Tuple

try:
    from shapely.geometry import Polygon, box as shapely_box
    HAS_SHAPELY = True
except Exception:  # pragma: no cover - environment fallback
    Polygon = object  # type: ignore[assignment]
    shapely_box = None  # type: ignore[assignment]
    HAS_SHAPELY = False

from .masterplan_types import Typology

FootprintKind = Literal["rectangle", "l_shape", "u_shape", "o_shape"]


@dataclass(frozen=True)
class DimensionRange:
    min_m: float
    max_m: float

    def contains(self, value: float) -> bool:
        return self.min_m <= value <= self.max_m

    def midpoint(self) -> float:
        return (self.min_m + self.max_m) / 2.0


@dataclass(frozen=True)
class BaseTypologySpec:
    typology: Typology
    allowed_floors: Tuple[int, int]
    width_m: Optional[DimensionRange] = None
    depth_m: Optional[DimensionRange] = None
    length_m: Optional[DimensionRange] = None
    segment_depth_m: Optional[DimensionRange] = None
    length_per_unit_m: Optional[DimensionRange] = None
    allowed_tower_sizes_m: Tuple[int, ...] = ()
    min_spacing_m: float = 8.0
    aspect_ratio_range: Tuple[float, float] = (0.25, 100.0)
    allowed_footprint_kinds: Set[FootprintKind] = field(default_factory=lambda: {"rectangle"})
    min_courtyard_side_m: Optional[float] = None
    max_block_length_m: Optional[float] = None

    def validate_dimensions(self, *, width_m: float, depth_m: float, floors: int) -> List[str]:
        errors: List[str] = []
        if floors < self.allowed_floors[0] or floors > self.allowed_floors[1]:
            errors.append(
                f"Etasjer {floors} er utenfor tillatt intervall {self.allowed_floors[0]}-{self.allowed_floors[1]}."
            )
        if self.width_m and not self.width_m.contains(width_m):
            errors.append(
                f"Bredde {width_m:.1f} m er utenfor tillatt intervall {self.width_m.min_m:.1f}-{self.width_m.max_m:.1f} m."
            )
        if self.depth_m and not self.depth_m.contains(depth_m):
            errors.append(
                f"Dybde {depth_m:.1f} m er utenfor tillatt intervall {self.depth_m.min_m:.1f}-{self.depth_m.max_m:.1f} m."
            )
        if self.length_m and not self.length_m.contains(width_m):
            # For lamell/rekkehus is the long side stored in width_m for local coords.
            errors.append(
                f"Lengde {width_m:.1f} m er utenfor tillatt intervall {self.length_m.min_m:.1f}-{self.length_m.max_m:.1f} m."
            )
        aspect = _safe_aspect_ratio(width_m, depth_m)
        if aspect < self.aspect_ratio_range[0] or aspect > self.aspect_ratio_range[1]:
            errors.append(
                f"Aspect ratio {aspect:.2f} er utenfor tillatt intervall {self.aspect_ratio_range[0]:.2f}-{self.aspect_ratio_range[1]:.2f}."
            )
        return errors

    def make_rectangle(self, *, width_m: float, depth_m: float) -> Polygon:
        if not HAS_SHAPELY or shapely_box is None:
            raise RuntimeError("Shapely is required to create footprints.")
        return shapely_box(0.0, 0.0, float(width_m), float(depth_m))

    def validate_footprint(self, footprint: Polygon) -> List[str]:
        return validate_footprint_for_spec(footprint, self)


# ---------------------------------------------------------------------------
# Library entries
# ---------------------------------------------------------------------------


LAMELL_SPEC = BaseTypologySpec(
    typology=Typology.LAMELL,
    allowed_floors=(3, 7),
    length_m=DimensionRange(30.0, 60.0),
    depth_m=DimensionRange(12.0, 14.0),
    min_spacing_m=1.2,
    aspect_ratio_range=(2.5, 5.0),
    allowed_footprint_kinds={"rectangle"},
)

PUNKTHUS_SPEC = BaseTypologySpec(
    typology=Typology.PUNKTHUS,
    allowed_floors=(4, 12),
    allowed_tower_sizes_m=(17, 21),
    min_spacing_m=15.0,
    aspect_ratio_range=(0.95, 1.05),
    allowed_footprint_kinds={"rectangle"},
)

KARRE_SPEC = BaseTypologySpec(
    typology=Typology.KARRE,
    allowed_floors=(3, 7),
    segment_depth_m=DimensionRange(12.0, 15.0),
    min_spacing_m=8.0,
    aspect_ratio_range=(0.25, 4.0),
    allowed_footprint_kinds={"rectangle", "l_shape", "u_shape", "o_shape"},
    min_courtyard_side_m=18.0,
    max_block_length_m=70.0,
)

REKKEHUS_SPEC = BaseTypologySpec(
    typology=Typology.REKKEHUS,
    allowed_floors=(2, 3),
    length_m=DimensionRange(5.0, 7.0),
    depth_m=DimensionRange(7.0, 9.0),
    min_spacing_m=8.0,
    aspect_ratio_range=(0.55, 4.0),
    allowed_footprint_kinds={"rectangle"},
)


TYPOLOGY_LIBRARY: Dict[Typology, BaseTypologySpec] = {
    Typology.LAMELL: LAMELL_SPEC,
    Typology.PUNKTHUS: PUNKTHUS_SPEC,
    Typology.KARRE: KARRE_SPEC,
    Typology.REKKEHUS: REKKEHUS_SPEC,
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def get_typology_spec(typology: Typology) -> BaseTypologySpec:
    return TYPOLOGY_LIBRARY[typology]


def _safe_aspect_ratio(a: float, b: float) -> float:
    lo = min(abs(a), abs(b))
    hi = max(abs(a), abs(b))
    if lo <= 0.0:
        return float("inf")
    return hi / lo


def _unique_vertices(poly: Polygon, tol: float = 1e-6) -> List[Tuple[float, float]]:
    coords = list(poly.exterior.coords)
    if len(coords) > 1 and _points_close(coords[0], coords[-1], tol):
        coords = coords[:-1]
    unique: List[Tuple[float, float]] = []
    for x, y in coords:
        pt = (float(x), float(y))
        if not unique or not _points_close(unique[-1], pt, tol):
            unique.append(pt)
    return unique


def _points_close(a: Sequence[float], b: Sequence[float], tol: float = 1e-6) -> bool:
    return abs(float(a[0]) - float(b[0])) <= tol and abs(float(a[1]) - float(b[1])) <= tol


def is_axis_aligned_rectilinear(poly: Polygon, tol: float = 1e-6) -> bool:
    vertices = _unique_vertices(poly, tol=tol)
    if len(vertices) < 4:
        return False
    for start, end in zip(vertices, vertices[1:] + [vertices[0]]):
        dx = abs(end[0] - start[0])
        dy = abs(end[1] - start[1])
        if dx > tol and dy > tol:
            return False
    return True


def classify_rectilinear_footprint(poly: Polygon, tol: float = 1e-6) -> Optional[FootprintKind]:
    if poly.is_empty or not poly.is_valid:
        return None
    if not is_axis_aligned_rectilinear(poly, tol=tol):
        return None
    vertices = _unique_vertices(poly, tol=tol)
    count = len(vertices)
    if count == 4:
        return "rectangle"
    if count == 6:
        return "l_shape"
    if count == 8:
        # Distinguish U/O later in the pipeline if needed. Delivery 1 only needs
        # to accept rectilinear courtyard-capable footprints.
        return "u_shape"
    return None


def side_lengths_from_bounds(poly: Polygon) -> Tuple[float, float]:
    minx, miny, maxx, maxy = poly.bounds
    return float(maxx - minx), float(maxy - miny)


def validate_footprint_for_spec(footprint: Polygon, spec: BaseTypologySpec) -> List[str]:
    errors: List[str] = []
    if footprint.is_empty:
        return ["Footprint er tomt."]
    if not footprint.is_valid:
        errors.append("Footprint er ikke geometrisk gyldig.")
        return errors

    kind = classify_rectilinear_footprint(footprint)
    if kind is None:
        errors.append("Footprint må være rektangulært eller enkel L/U/O-komposisjon i lokale koordinater.")
        return errors
    if kind not in spec.allowed_footprint_kinds:
        errors.append(f"Footprint-type {kind} er ikke tillatt for {spec.typology.value}.")

    width_m, depth_m = side_lengths_from_bounds(footprint)
    aspect = _safe_aspect_ratio(width_m, depth_m)
    if aspect < spec.aspect_ratio_range[0] or aspect > spec.aspect_ratio_range[1]:
        errors.append(
            f"Aspect ratio {aspect:.2f} er utenfor tillatt intervall {spec.aspect_ratio_range[0]:.2f}-{spec.aspect_ratio_range[1]:.2f}."
        )

    if spec.typology == Typology.LAMELL:
        long_side = max(width_m, depth_m)
        short_side = min(width_m, depth_m)
        if spec.length_m and not spec.length_m.contains(long_side):
            errors.append(
                f"Lamell-lengde {long_side:.1f} m er utenfor {spec.length_m.min_m:.1f}-{spec.length_m.max_m:.1f} m."
            )
        if spec.depth_m and not spec.depth_m.contains(short_side):
            errors.append(
                f"Lamell-dybde {short_side:.1f} m er utenfor {spec.depth_m.min_m:.1f}-{spec.depth_m.max_m:.1f} m."
            )
    elif spec.typology == Typology.PUNKTHUS:
        rounded_sides = {int(round(width_m)), int(round(depth_m))}
        if len(rounded_sides) != 1 or next(iter(rounded_sides)) not in spec.allowed_tower_sizes_m:
            errors.append(
                f"Punkthus må være {spec.allowed_tower_sizes_m}, fikk {width_m:.1f}×{depth_m:.1f} m."
            )
    elif spec.typology == Typology.REKKEHUS:
        long_side = max(width_m, depth_m)
        short_side = min(width_m, depth_m)
        if spec.depth_m and not spec.depth_m.contains(short_side):
            errors.append(
                f"Rekkehus-dybde {short_side:.1f} m er utenfor {spec.depth_m.min_m:.1f}-{spec.depth_m.max_m:.1f} m."
            )
        # Rekkehus kan settes sammen senere, så her godtas bredere total-lengder.
        if long_side <= short_side:
            errors.append("Rekkehus må ha en tydelig lengderetning.")
    elif spec.typology == Typology.KARRE:
        if kind == "rectangle" and spec.min_courtyard_side_m is not None:
            # A plain rectangle is only acceptable as a placeholder in delivery 1.
            pass

    return errors


def validate_dimensions_for_typology(
    typology: Typology,
    *,
    width_m: float,
    depth_m: float,
    floors: int,
) -> List[str]:
    spec = get_typology_spec(typology)
    if typology == Typology.LAMELL:
        long_side = max(width_m, depth_m)
        short_side = min(width_m, depth_m)
        return spec.validate_dimensions(width_m=long_side, depth_m=short_side, floors=floors)
    if typology == Typology.PUNKTHUS:
        side = max(width_m, depth_m)
        errors = spec.validate_dimensions(width_m=side, depth_m=min(width_m, depth_m), floors=floors)
        rounded_sides = {int(round(width_m)), int(round(depth_m))}
        if len(rounded_sides) != 1 or next(iter(rounded_sides)) not in spec.allowed_tower_sizes_m:
            errors.append(
                f"Punkthus må være kvadratisk med side {spec.allowed_tower_sizes_m}, fikk {width_m:.1f}×{depth_m:.1f} m."
            )
        return errors
    return spec.validate_dimensions(width_m=width_m, depth_m=depth_m, floors=floors)
