from __future__ import annotations

"""Core datatypes for the Builtly v8 masterplan engine.

Delivery 4 extends the deterministic geometry/sol/MUA foundation with:
- concept-family level alternatives
- AI-pass parameter selection hooks (pass 2)
- AI/fallback report narratives (pass 6)
- concept-level OptionResult integration

The types remain intentionally compact and project-agnostic.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Literal, Optional, Protocol, Tuple

try:
    from shapely.geometry import Polygon
    from shapely import wkt as shapely_wkt
    HAS_SHAPELY = True
except Exception:
    Polygon = object  # type: ignore[assignment]
    shapely_wkt = None  # type: ignore[assignment]
    HAS_SHAPELY = False


class ConceptFamily(str, Enum):
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
    # Pass 3 AI-designdirektiv — alle valgfrie, default er "ingen føring"
    design_variant: Optional[str] = None            # "single" | "varied" | "rotated" — for LAMELL
    design_karre_shape: Optional[str] = None         # "uo" | "l" | "t" | "z" — for KARRE
    design_height_pattern: Optional[str] = None      # "uniform" | "accent" | "stepped" | "paired" — for PUNKTHUS
    design_rotation_deg: Optional[float] = None       # små vinkler, -15..+15
    design_reasoning: Optional[str] = None            # AI sin forklaring, for logging

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
            "design_variant": self.design_variant,
            "design_karre_shape": self.design_karre_shape,
            "design_height_pattern": self.design_height_pattern,
            "design_rotation_deg": self.design_rotation_deg,
            "design_reasoning": self.design_reasoning,
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
            courtyard_kind=CourtyardKind(data["courtyard_kind"]) if data.get("courtyard_kind") else None,
            tower_size_m=data.get("tower_size_m"),
            phase=int(data.get("phase", 1)),
            phase_label=str(data.get("phase_label", "")),
            design_variant=data.get("design_variant"),
            design_karre_shape=data.get("design_karre_shape"),
            design_height_pattern=data.get("design_height_pattern"),
            design_rotation_deg=data.get("design_rotation_deg"),
            design_reasoning=data.get("design_reasoning"),
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
    angle_deg: float = 0.0
    has_tak_mua: Optional[bool] = None
    tak_mua_share: float = 0.30
    privat_mua_m2: float = 0.0

    @property
    def footprint_m2(self) -> float:
        return float(getattr(self.footprint, "area", 0.0))

    @property
    def bra_m2(self) -> float:
        return self.footprint_m2 * float(self.floors)

    @property
    def tak_mua_m2(self) -> float:
        enabled = self.has_tak_mua if self.has_tak_mua is not None else self.floors >= 3
        return self.footprint_m2 * float(self.tak_mua_share) if enabled and self.floors >= 3 else 0.0

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
            "angle_deg": float(self.angle_deg),
            "has_tak_mua": self.has_tak_mua,
            "tak_mua_share": float(self.tak_mua_share),
            "privat_mua_m2": float(self.privat_mua_m2),
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
            angle_deg=float(data.get("angle_deg", 0.0)),
            has_tak_mua=data.get("has_tak_mua"),
            tak_mua_share=float(data.get("tak_mua_share", 0.30)),
            privat_mua_m2=float(data.get("privat_mua_m2", 0.0)),
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
    max_bya_pct: Optional[float] = None
    max_bra_pct: Optional[float] = None
    max_floors: Optional[int] = None
    max_height_m: Optional[float] = None
    mua_per_bolig_m2: Optional[float] = None
    mua_min_felles_pct: Optional[float] = None
    mua_min_bakke_pct: Optional[float] = None
    mua_min_sol_timer: Optional[float] = None
    parkering_per_100m2_bra: Optional[Tuple[float, float]] = None
    parkering_sykkel_per_bolig: Optional[float] = None
    avstand_nabogrense_m: Optional[float] = None
    avstand_bygg_bygg_m: Optional[float] = None
    brann_avstand_m: float = 8.0
    sol_krav_timer_varjevndogn: Optional[float] = None
    custom_rules: Dict[str, Any] = field(default_factory=dict)
    source_name: str = ""
    source_url: Optional[str] = None


@dataclass
class SolBuildingResult:
    bygg_id: str
    sol_score: float = 0.0
    soltimer_varjevndogn: float = 0.0
    tek17_mua_compliant: Optional[bool] = None
    facade_sun_fraction: float = 0.0
    possible_samples: int = 0
    sunlit_samples: int = 0
    facade_results: Dict[str, float] = field(default_factory=dict)
    per_leilighet: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SolKeyMoment:
    label: str
    elevation_deg: float
    azimuth_deg: float
    representative_shadow_m: float


@dataclass
class SolReport:
    per_building: List[SolBuildingResult] = field(default_factory=list)
    total_sol_score: float = 0.0
    project_soltimer_varjevndogn: float = 0.0
    mua_soltimer_varjevndogn: Optional[float] = None
    mua_sun_compliant: Optional[bool] = None
    solbelyst_uteareal_pct: float = 0.0
    vinter_skygge_kl_12_m: float = 0.0
    sommerskygge_kl_15_m: float = 0.0
    key_moments: List[SolKeyMoment] = field(default_factory=list)
    analysis_samples: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def mua_sun_hours_eqx(self) -> float:
        return float(self.mua_soltimer_varjevndogn or 0.0)

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
    tak: float = 0.0
    open_ground_area: float = 0.0
    checks: List[ComplianceCheck] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def compliant(self) -> bool:
        relevant = [check for check in self.checks if check.status != ComplianceState.IKKE_VURDERT]
        return bool(relevant) and all(check.status == ComplianceState.JA for check in relevant)

    def status_for(self, rule_key: str) -> ComplianceState:
        for check in self.checks:
            if check.rule_key == rule_key:
                return check.status
        return ComplianceState.IKKE_VURDERT


@dataclass
class FieldParameterChoice:
    field_id: str
    typology: Typology
    orientation_deg: float
    floors_min: int
    floors_max: int
    target_bra: float
    courtyard_kind: Optional[CourtyardKind] = None
    tower_size_m: Optional[Literal[17, 21]] = None
    rationale: str = ""


@dataclass
class ReportNarrative:
    title: str
    summary: str
    architectural_assessment: str
    recommendation: str
    risks: List[str] = field(default_factory=list)
    source: str = "fallback"


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
    display_subtitle: str = ""
    report_summary: str = ""
    report_architectural_assessment: str = ""
    report_recommendation: str = ""
    report_risks: List[str] = field(default_factory=list)
    score: float = 0.0
    buildable_polygon: Optional[Polygon] = None
    site_area_m2: float = 0.0
    parkering_areal: float = 0.0
    vei_areal: float = 0.0
    plan_regler: PlanRegler = field(default_factory=PlanRegler)
    barnehage_config: BarnehageConfig = field(default_factory=BarnehageConfig)
    bra_deficit: float = 0.0
    latitude_deg: float = 63.43
    longitude_deg: float = 10.40
    pass2_source: str = "fallback"
    pass6_source: str = "fallback"

    def iter_buildings_for_delfelt(self, field_id: str) -> Iterable[Bygg]:
        return (bygg for bygg in self.bygg if bygg.delfelt_id == field_id)

    def delfelt_by_id(self, field_id: str) -> Optional[Delfelt]:
        for field_obj in self.delfelt:
            if field_obj.field_id == field_id:
                return field_obj
        return None


class StructuredPassClient(Protocol):
    def __call__(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]: ...
