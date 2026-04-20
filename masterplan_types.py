from __future__ import annotations

"""Core datatypes for the Builtly v8 masterplan engine.

Design goals:
- deterministic geometry / sol / MUA pipeline
- project-agnostic concept families
- rule-engine agnostic `PlanRegler`
- optional program elements are opt-in

The module is intentionally small and stable so later passes can depend on it
without importing legacy concepts such as volume phases or AI-generated zones.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple

try:
    from shapely.geometry import Polygon
    from shapely import wkt as shapely_wkt
    HAS_SHAPELY = True
except Exception:  # pragma: no cover - environment fallback
    Polygon = object  # type: ignore[assignment]
    shapely_wkt = None  # type: ignore[assignment]
    HAS_SHAPELY = False


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConceptFamily(str, Enum):
    """Project-independent concept families.

    These values are engine-facing. UI titles may be more human-friendly and are
    expected to be generated later from actual outputs.
    """

    LINEAR_MIXED = "LINEAR_MIXED"
    COURTYARD_URBAN = "COURTYARD_URBAN"
    CLUSTER_PARK = "CLUSTER_PARK"


class Typology(str, Enum):
    KARRE = "Karré"
    LAMELL = "Lamell"
    PUNKTHUS = "Punkthus"
    REKKEHUS = "Rekkehus"


class CourtyardKind(str, Enum):
    FELLES_BOLIG = "felles_bolig"
    PARKKANT = "parkkant"
    URBAN_TORG = "urban_torg"


class ComplianceState(str, Enum):
    JA = "JA"
    NEI = "NEI"
    IKKE_VURDERT = "IKKE VURDERT"


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _serialize_polygon(poly: Optional[Polygon]) -> Optional[str]:
    if poly is None:
        return None
    if not HAS_SHAPELY:
        raise RuntimeError("Shapely is required to serialize geometry.")
    return poly.wkt


def _deserialize_polygon(text: Optional[str]) -> Optional[Polygon]:
    if not text:
        return None
    if not HAS_SHAPELY or shapely_wkt is None:
        raise RuntimeError("Shapely is required to deserialize geometry.")
    geom = shapely_wkt.loads(text)
    if not isinstance(geom, Polygon):
        raise TypeError("Expected Polygon WKT.")
    return geom


# ---------------------------------------------------------------------------
# Main datatypes
# ---------------------------------------------------------------------------


@dataclass
class Delfelt:
    field_id: str
    polygon: Polygon
    typology: Typology
    orientation_deg: float
    floors_min: int
    floors_max: int
    target_bra: float
    courtyard_kind: Optional[CourtyardKind] = None
    tower_size_m: Optional[Literal[17, 21]] = None
    phase: int = 1
    phase_label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_id": self.field_id,
            "polygon_wkt": _serialize_polygon(self.polygon),
            "typology": self.typology.value,
            "orientation_deg": float(self.orientation_deg),
            "floors_min": int(self.floors_min),
            "floors_max": int(self.floors_max),
            "target_bra": float(self.target_bra),
            "courtyard_kind": self.courtyard_kind.value if self.courtyard_kind else None,
            "tower_size_m": self.tower_size_m,
            "phase": int(self.phase),
            "phase_label": self.phase_label,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Delfelt":
        return cls(
            field_id=str(data["field_id"]),
            polygon=_deserialize_polygon(data.get("polygon_wkt")) or Polygon(),
            typology=Typology(data["typology"]),
            orientation_deg=float(data.get("orientation_deg", 0.0)),
            floors_min=int(data.get("floors_min", 1)),
            floors_max=int(data.get("floors_max", 1)),
            target_bra=float(data.get("target_bra", 0.0)),
            courtyard_kind=(CourtyardKind(data["courtyard_kind"]) if data.get("courtyard_kind") else None),
            tower_size_m=data.get("tower_size_m"),
            phase=int(data.get("phase", 1)),
            phase_label=str(data.get("phase_label", "")),
        )


@dataclass
class Bygg:
    bygg_id: str
    footprint: Polygon
    floors: int
    height_m: float
    typology: Typology
    delfelt_id: str
    phase: int
    barnehage_i_sokkel: bool = False
    barnehage_inne_m2: float = 0.0
    barnehage_ute_delfelt_id: Optional[str] = None
    display_name: str = ""

    @property
    def footprint_m2(self) -> float:
        return float(getattr(self.footprint, "area", 0.0))

    @property
    def bra_m2(self) -> float:
        return self.footprint_m2 * float(self.floors)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bygg_id": self.bygg_id,
            "footprint_wkt": _serialize_polygon(self.footprint),
            "floors": int(self.floors),
            "height_m": float(self.height_m),
            "typology": self.typology.value,
            "delfelt_id": self.delfelt_id,
            "phase": int(self.phase),
            "barnehage_i_sokkel": bool(self.barnehage_i_sokkel),
            "barnehage_inne_m2": float(self.barnehage_inne_m2),
            "barnehage_ute_delfelt_id": self.barnehage_ute_delfelt_id,
            "display_name": self.display_name,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bygg":
        return cls(
            bygg_id=str(data["bygg_id"]),
            footprint=_deserialize_polygon(data.get("footprint_wkt")) or Polygon(),
            floors=int(data.get("floors", 1)),
            height_m=float(data.get("height_m", 0.0)),
            typology=Typology(data["typology"]),
            delfelt_id=str(data.get("delfelt_id", "")),
            phase=int(data.get("phase", 1)),
            barnehage_i_sokkel=bool(data.get("barnehage_i_sokkel", False)),
            barnehage_inne_m2=float(data.get("barnehage_inne_m2", 0.0)),
            barnehage_ute_delfelt_id=data.get("barnehage_ute_delfelt_id"),
            display_name=str(data.get("display_name", "")),
        )


@dataclass
class BarnehageConfig:
    enabled: bool = False
    inne_m2: float = 0.0
    ute_m2: float = 0.0
    placement: Literal["sokkel", "frittstaende"] = "sokkel"
    preferred_delfelt_id: Optional[str] = None


@dataclass
class PlanRegler:
    """Abstract rule container.

    All fields are optional unless a safe fallback is legally or technically
    meaningful. `None` must be interpreted as *not evaluated* later in the
    compliance tables.
    """

    # Utilisation
    max_bya_pct: Optional[float] = None
    max_bra_pct: Optional[float] = None
    max_floors: Optional[int] = None
    max_height_m: Optional[float] = None

    # Open space / MUA
    mua_per_bolig_m2: Optional[float] = None
    mua_min_felles_pct: Optional[float] = None
    mua_min_bakke_pct: Optional[float] = None
    mua_min_sol_timer: Optional[float] = None

    # Parking
    parkering_per_100m2_bra: Optional[Tuple[float, float]] = None
    parkering_sykkel_per_bolig: Optional[float] = None

    # Distances
    avstand_nabogrense_m: Optional[float] = None
    avstand_bygg_bygg_m: Optional[float] = None

    # Fire / safety
    brann_avstand_m: float = 8.0

    # Solar requirements
    sol_krav_timer_varjevndogn: Optional[float] = None

    # Additional rule slots
    custom_rules: Dict[str, Any] = field(default_factory=dict)

    # Provenance
    source_name: str = ""
    source_url: Optional[str] = None


@dataclass
class SolBuildingResult:
    bygg_id: str
    sol_score: float = 0.0
    soltimer_varjevndogn: float = 0.0
    tek17_mua_compliant: Optional[bool] = None
    per_leilighet: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SolReport:
    per_building: List[SolBuildingResult] = field(default_factory=list)
    total_sol_score: float = 0.0
    notes: List[str] = field(default_factory=list)

    def result_for(self, bygg_id: str) -> Optional[SolBuildingResult]:
        for item in self.per_building:
            if item.bygg_id == bygg_id:
                return item
        return None


@dataclass
class ComplianceCheck:
    rule_key: str
    status: ComplianceState
    actual_value: Optional[float] = None
    required_value: Optional[float] = None
    unit: str = ""
    note: str = ""


@dataclass
class MUAReport:
    total: float = 0.0
    krav_total: Optional[float] = None
    bakke: float = 0.0
    krav_bakke: Optional[float] = None
    fellesareal: float = 0.0
    krav_fellesareal: Optional[float] = None
    privat: float = 0.0
    checks: List[ComplianceCheck] = field(default_factory=list)

    @property
    def compliant(self) -> bool:
        relevant = [check for check in self.checks if check.status != ComplianceState.IKKE_VURDERT]
        return bool(relevant) and all(check.status == ComplianceState.JA for check in relevant)


@dataclass
class Masterplan:
    concept_family: ConceptFamily
    delfelt: List[Delfelt]
    bygg: List[Bygg]
    sol_report: SolReport
    mua_report: MUAReport
    total_bra_m2: float
    total_bya_m2: float
    antall_boliger: int
    display_title: str = ""
    plan_regler: PlanRegler = field(default_factory=PlanRegler)
    barnehage_config: BarnehageConfig = field(default_factory=BarnehageConfig)
    bra_deficit: float = 0.0

    def iter_buildings_for_delfelt(self, field_id: str) -> Iterable[Bygg]:
        return (bygg for bygg in self.bygg if bygg.delfelt_id == field_id)

    def delfelt_by_id(self, field_id: str) -> Optional[Delfelt]:
        for field_obj in self.delfelt:
            if field_obj.field_id == field_id:
                return field_obj
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept_family": self.concept_family.value,
            "delfelt": [item.to_dict() for item in self.delfelt],
            "bygg": [item.to_dict() for item in self.bygg],
            "sol_report": {
                "per_building": [
                    {
                        "bygg_id": res.bygg_id,
                        "sol_score": res.sol_score,
                        "soltimer_varjevndogn": res.soltimer_varjevndogn,
                        "tek17_mua_compliant": res.tek17_mua_compliant,
                        "per_leilighet": res.per_leilighet,
                    }
                    for res in self.sol_report.per_building
                ],
                "total_sol_score": self.sol_report.total_sol_score,
                "notes": list(self.sol_report.notes),
            },
            "mua_report": {
                "total": self.mua_report.total,
                "krav_total": self.mua_report.krav_total,
                "bakke": self.mua_report.bakke,
                "krav_bakke": self.mua_report.krav_bakke,
                "fellesareal": self.mua_report.fellesareal,
                "krav_fellesareal": self.mua_report.krav_fellesareal,
                "privat": self.mua_report.privat,
                "checks": [
                    {
                        "rule_key": check.rule_key,
                        "status": check.status.value,
                        "actual_value": check.actual_value,
                        "required_value": check.required_value,
                        "unit": check.unit,
                        "note": check.note,
                    }
                    for check in self.mua_report.checks
                ],
            },
            "total_bra_m2": self.total_bra_m2,
            "total_bya_m2": self.total_bya_m2,
            "antall_boliger": self.antall_boliger,
            "display_title": self.display_title,
            "plan_regler": self.plan_regler.__dict__,
            "barnehage_config": self.barnehage_config.__dict__,
            "bra_deficit": self.bra_deficit,
        }
