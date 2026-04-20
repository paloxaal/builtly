from __future__ import annotations

"""Builtly v8 delivery 2 — deterministic pass 1 + pass 3 engine.

This module intentionally avoids all AI and legacy masterplan logic. It only
implements:
- pass 1: geometric site analysis and delfelt subdivision
- pass 3: deterministic building placement with hard geometric rules

Pass 2 (AI parameter choice), pass 4 (solar) and pass 5 (MUA) are delivered in
later increments. Delivery 2 returns empty SolReport / MUAReport placeholders so
that the `Masterplan` object is already structurally stable.
"""

from dataclasses import replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from shapely.geometry import Polygon

from .geometry import (
    building_geometry_is_orthogonal_to_field,
    buildings_do_not_overlap,
    pca_site_axes,
    place_buildings_for_fields,
    resolve_delfelt_count,
    subdivide_buildable_polygon,
)
from .masterplan_types import (
    BarnehageConfig,
    ConceptFamily,
    Delfelt,
    Masterplan,
    MUAReport,
    PlanRegler,
    SolReport,
    Typology,
)
from .typology_library import get_typology_spec


# ---------------------------------------------------------------------------
# Deterministic parameter seeding for delivery 2
# ---------------------------------------------------------------------------


def _default_field_typology_sequence(concept_family: ConceptFamily, count: int) -> List[Typology]:
    if concept_family == ConceptFamily.LINEAR_MIXED:
        base = [Typology.LAMELL, Typology.PUNKTHUS, Typology.LAMELL, Typology.PUNKTHUS]
    elif concept_family == ConceptFamily.COURTYARD_URBAN:
        base = [Typology.KARRE, Typology.KARRE, Typology.LAMELL, Typology.REKKEHUS]
    else:  # CLUSTER_PARK
        base = [Typology.LAMELL, Typology.PUNKTHUS, Typology.LAMELL, Typology.PUNKTHUS]

    seq: List[Typology] = []
    while len(seq) < count:
        seq.extend(base)
    return seq[:count]


_DEF_FLOORS: Dict[Typology, Tuple[int, int]] = {
    Typology.LAMELL: (4, 6),
    Typology.PUNKTHUS: (5, 6),
    Typology.KARRE: (5, 6),
    Typology.REKKEHUS: (2, 3),
}


def _seed_fields_for_concept(
    field_polygons: Sequence[Polygon],
    concept_family: ConceptFamily,
    target_bra_m2: float,
) -> List[Delfelt]:
    total_area = sum(p.area for p in field_polygons) or 1.0
    types = _default_field_typology_sequence(concept_family, len(field_polygons))
    seeded: List[Delfelt] = []
    for idx, poly in enumerate(field_polygons, start=1):
        typ = types[idx - 1]
        floors_min, floors_max = _DEF_FLOORS[typ]
        share = poly.area / total_area
        field_target = target_bra_m2 * share
        tower_size = 17 if typ == Typology.PUNKTHUS else None
        seeded.append(
            Delfelt(
                field_id=f"DF{idx}",
                polygon=poly.buffer(0),
                typology=typ,
                orientation_deg=0.0,  # overwritten in pass 1
                floors_min=floors_min,
                floors_max=floors_max,
                target_bra=float(field_target),
                tower_size_m=tower_size,
                phase=idx,
                phase_label=f"Delfelt {idx}",
            )
        )
    return seeded


# ---------------------------------------------------------------------------
# Pass 1
# ---------------------------------------------------------------------------


def pass1_generate_delfelt(
    buildable_poly: Polygon,
    concept_family: ConceptFamily,
    target_bra_m2: float,
    requested_count: Optional[int] = None,
) -> List[Delfelt]:
    axes = pca_site_axes(buildable_poly)
    count = resolve_delfelt_count(buildable_poly, requested_count=requested_count)
    polygons = subdivide_buildable_polygon(buildable_poly, count=count, orientation_deg=axes.theta_deg)
    seeded = _seed_fields_for_concept(polygons, concept_family, target_bra_m2)
    return [replace(field, orientation_deg=axes.theta_deg, phase=idx, phase_label=f"Delfelt {idx}") for idx, field in enumerate(seeded, start=1)]


# ---------------------------------------------------------------------------
# Pass 3
# ---------------------------------------------------------------------------


def pass3_place_buildings(
    buildable_poly: Polygon,
    delfelt: Sequence[Delfelt],
    plan_regler: Optional[PlanRegler] = None,
    barnehage_config: Optional[BarnehageConfig] = None,
):
    del barnehage_config  # handled in later deliveries
    return place_buildings_for_fields(buildable_poly, list(delfelt), plan_regler=plan_regler)


# ---------------------------------------------------------------------------
# Delivery 2 entrypoint
# ---------------------------------------------------------------------------


def plan_masterplan_geometry(
    buildable_poly: Polygon,
    *,
    concept_family: ConceptFamily = ConceptFamily.LINEAR_MIXED,
    target_bra_m2: float = 0.0,
    plan_regler: Optional[PlanRegler] = None,
    requested_delfelt_count: Optional[int] = None,
    avg_unit_bra_m2: float = 55.0,
    barnehage_config: Optional[BarnehageConfig] = None,
) -> Masterplan:
    if buildable_poly is None or buildable_poly.is_empty:
        raise ValueError("buildable_poly mangler eller er tom")
    rules = plan_regler or PlanRegler()
    fields = pass1_generate_delfelt(
        buildable_poly=buildable_poly,
        concept_family=concept_family,
        target_bra_m2=target_bra_m2,
        requested_count=requested_delfelt_count,
    )
    buildings, bra_deficit = pass3_place_buildings(
        buildable_poly=buildable_poly,
        delfelt=fields,
        plan_regler=rules,
        barnehage_config=barnehage_config,
    )

    total_bra = sum(b.bra_m2 for b in buildings)
    total_bya = sum(b.footprint_m2 for b in buildings)
    units = int(round(total_bra / avg_unit_bra_m2)) if avg_unit_bra_m2 > 0 else 0

    return Masterplan(
        concept_family=concept_family,
        delfelt=list(fields),
        bygg=list(buildings),
        sol_report=SolReport(),
        mua_report=MUAReport(),
        total_bra_m2=float(total_bra),
        total_bya_m2=float(total_bya),
        antall_boliger=max(0, units),
        display_title=concept_family.value,
        plan_regler=rules,
        barnehage_config=barnehage_config or BarnehageConfig(),
        bra_deficit=float(bra_deficit),
    )


# ---------------------------------------------------------------------------
# Validation helpers for tests / integration
# ---------------------------------------------------------------------------


def validate_masterplan_geometry(plan: Masterplan, buildable_poly: Polygon) -> List[str]:
    errors: List[str] = []
    if not buildings_do_not_overlap(plan.bygg):
        errors.append("Bygg overlapper hverandre.")

    for field in plan.delfelt:
        buildings = [b for b in plan.bygg if b.delfelt_id == field.field_id]
        for building in buildings:
            if not buildable_poly.contains(building.footprint):
                errors.append(f"{building.bygg_id} ligger utenfor buildable_poly.")
            if not field.polygon.contains(building.footprint):
                errors.append(f"{building.bygg_id} ligger utenfor delfelt {field.field_id}.")
            if not building_geometry_is_orthogonal_to_field(building, field):
                errors.append(f"{building.bygg_id} bryter ortogonalitet i {field.field_id}.")
    return errors
