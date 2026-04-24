"""
Builtly site planner compatibility layer.

This module intentionally no longer contains the old 4-pass coordinate planner.
It delegates geometry to the deterministic Builtly masterplan engine, where AI is
limited to concept/parameter choices and Python owns all footprints.

Exports plan_site() with the legacy signature used by Mulighetsstudie.py.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional, Sequence

try:
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except Exception:  # pragma: no cover
    HAS_SHAPELY = False
    Polygon = MultiPolygon = object  # type: ignore
    unary_union = None  # type: ignore

logger = logging.getLogger(__name__)
DEFAULT_MODEL = "builtly-masterplan-v8"


def is_available() -> bool:
    """The deterministic masterplan engine is available without an API key."""
    return True


def _coord_groups(poly: Any):
    if poly is None or getattr(poly, "is_empty", True):
        return []
    if HAS_SHAPELY and isinstance(poly, Polygon):
        return [[[float(x), float(y)] for x, y in list(poly.exterior.coords)]]
    if HAS_SHAPELY and isinstance(poly, MultiPolygon):
        return [[ [float(x), float(y)] for x, y in list(part.exterior.coords) ] for part in poly.geoms if not part.is_empty]
    return []


def _concept_family_for_typology(typology: str, buildable_polygon: Any, target_bta_m2: float):
    from builtly.masterplan_types import ConceptFamily
    typ = (typology or "").lower()
    area = float(getattr(buildable_polygon, "area", 1.0) or 1.0)
    density = float(target_bta_m2 or 0.0) / max(area, 1.0)
    if "karr" in typ or "kvartal" in typ or "tun" in typ or density >= 1.05:
        return ConceptFamily.COURTYARD_URBAN
    if "punkt" in typ or "tårn" in typ or "tarn" in typ:
        return ConceptFamily.CLUSTER_PARK
    return ConceptFamily.LINEAR_MIXED


def _safe_plan_regler(max_bya_pct: float, max_floors: int, max_height_m: float, floor_to_floor_m: float):
    from builtly.masterplan_types import PlanRegler
    return PlanRegler(
        max_bya_pct=float(max_bya_pct) if max_bya_pct else None,
        max_floors=int(max_floors) if max_floors else None,
        max_height_m=float(max_height_m) if max_height_m else None,
        brann_avstand_m=8.0,
        avstand_bygg_bygg_m=8.0,
        custom_rules={"floor_to_floor_m": float(floor_to_floor_m or 3.2)},
        source_name="Legacy wrapper / Builtly v8",
    )


def _buildings_from_masterplan(plan: Any, typology: str) -> list[dict]:
    out = []
    for idx, b in enumerate(getattr(plan, "bygg", []) or []):
        fp = getattr(b, "footprint", None)
        c = fp.centroid if fp is not None and not fp.is_empty else None
        out.append({
            "polygon": fp,
            "name": getattr(b, "display_name", None) or getattr(b, "bygg_id", None) or f"{typology} {idx+1}",
            "role": getattr(b, "typology", typology).value if hasattr(getattr(b, "typology", None), "value") else str(getattr(b, "typology", typology)),
            "floors": int(getattr(b, "floors", 1) or 1),
            "height_m": round(float(getattr(b, "height_m", 0.0) or 0.0), 1),
            "width_m": round(math.sqrt(max(float(getattr(fp, "area", 0.0) or 0.0), 1.0)), 1),
            "depth_m": round(math.sqrt(max(float(getattr(fp, "area", 0.0) or 0.0), 1.0)), 1),
            "angle_deg": round(float(getattr(b, "orientation_deg", 0.0) or 0.0), 1),
            "area_m2": round(float(getattr(b, "footprint_m2", getattr(fp, "area", 0.0)) or 0.0), 1),
            "notes": getattr(b, "delfelt_id", ""),
            "cx": round(float(c.x), 1) if c else 0.0,
            "cy": round(float(c.y), 1) if c else 0.0,
            "pos_id": getattr(b, "delfelt_id", ""),
            "phase": int(getattr(b, "phase", 1) or 1),
        })
    return out


def plan_site(site_polygon, buildable_polygon, typology, *, neighbors: Optional[Sequence[dict]] = None, terrain=None,
              site_intelligence=None, site_inputs=None, target_bta_m2=5000.0, max_floors=5,
              max_height_m=16.0, max_bya_pct=35.0, floor_to_floor_m=3.2, model=DEFAULT_MODEL) -> Dict[str, Any]:
    if not HAS_SHAPELY or buildable_polygon is None or getattr(buildable_polygon, "is_empty", True):
        return {"buildings": [], "footprint": None, "error": "Shapely/polygon mangler"}

    try:
        from builtly.masterplan_engine import plan_masterplan_geometry
        rules = _safe_plan_regler(max_bya_pct, max_floors, max_height_m, floor_to_floor_m)
        target_bra_m2 = float(target_bta_m2 or 0.0) * 0.78
        concept_family = _concept_family_for_typology(typology, buildable_polygon, float(target_bta_m2 or 0.0))
        latitude = float((site_inputs or {}).get("latitude_deg", 63.42))
        longitude = float((site_inputs or {}).get("longitude_deg", 10.43))
        plan = plan_masterplan_geometry(
            buildable_polygon.buffer(0),
            concept_family=concept_family,
            target_bra_m2=target_bra_m2,
            plan_regler=rules,
            avg_unit_bra_m2=float((site_inputs or {}).get("avg_unit_bra_m2", 55.0)),
            latitude_deg=latitude,
            longitude_deg=longitude,
            neighbor_buildings=list(neighbors or []),
            site_area_m2=float(getattr(site_polygon, "area", buildable_polygon.area) or buildable_polygon.area),
        )
        buildings = _buildings_from_masterplan(plan, typology)
        polys = [b["polygon"] for b in buildings if b.get("polygon") is not None]
        footprint = unary_union(polys).buffer(0) if polys else None
        return {
            "buildings": buildings,
            "footprint": footprint,
            "building_count": len(buildings),
            "total_footprint_m2": round(sum(b.get("area_m2", 0.0) for b in buildings), 1),
            "total_bta_m2": round(sum(b.get("area_m2", 0.0) * b.get("floors", 1) for b in buildings), 1),
            "source": f"Builtly masterplan engine ({concept_family.value})",
            "concept": getattr(plan, "display_title", concept_family.value),
            "positions_evaluated": 0,
            "positions_usable": 0,
            "prompt": "",
            "raw_response": "",
            "raw_parsed": [],
            "masterplan_ref": plan,
            "diagram_layers": {
                "site_polygon_coords": _coord_groups(buildable_polygon),
                "massing_parts": [{"name": b["name"], "coords": _coord_groups(b["polygon"]), "height_m": b["height_m"], "floors": b["floors"]} for b in buildings],
            },
        }
    except Exception as exc:
        logger.exception("Builtly masterplan wrapper failed")
        return {"buildings": [], "footprint": None, "error": f"Masterplanmotor feilet: {exc}"}
