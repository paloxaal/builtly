
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from shapely.geometry import Polygon


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

    @property
    def area_m2(self) -> float:
        return float(self.polygon.area)


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
    barnehage_inne_m2: float = 0
    barnehage_ute_delfelt_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def footprint_m2(self) -> float:
        return float(self.footprint.area)

    @property
    def bra_m2(self) -> float:
        return float(self.footprint.area * self.floors)


@dataclass
class BarnehageConfig:
    enabled: bool = False
    inne_m2: float = 0
    ute_m2: float = 0
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
    parkering_per_100m2_bra: Optional[tuple] = None
    parkering_sykkel_per_bolig: Optional[float] = None
    avstand_nabogrense_m: Optional[float] = None
    avstand_bygg_bygg_m: Optional[float] = None
    brann_avstand_m: float = 8.0
    sol_krav_timer_varjevndogn: Optional[float] = None
    custom_rules: dict = field(default_factory=dict)
    source_name: str = ""
    source_url: Optional[str] = None


@dataclass
class SolMetrics:
    bygg_id: str
    sol_score: float
    soltimer_varjevndogn: float
    tek17_mua_compliant: Optional[bool] = None


@dataclass
class SolReport:
    total_score: float
    solar_hours_equinoct: float
    winter_hours: float
    summer_shadow_m: float
    winter_shadow_m: float
    by_building: List[SolMetrics] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComplianceItem:
    key: str
    status: str
    actual: Optional[float]
    required: Optional[float]
    note: str = ""


@dataclass
class MUAReport:
    total: float
    krav_total: Optional[float]
    bakke: float
    krav_bakke: Optional[float]
    fellesareal: float
    krav_fellesareal: Optional[float]
    privat: float
    compliant: List[ComplianceItem] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


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
    buildable_polygon: Polygon
    display_title: str = ""
    display_subtitle: str = ""
    report_summary: str = ""
    plan_regler: PlanRegler = field(default_factory=PlanRegler)
    barnehage_config: BarnehageConfig = field(default_factory=BarnehageConfig)
    bra_deficit: float = 0.0
    target_bra_m2: float = 0.0
    score: float = 0.0
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    @property
    def bya_pct(self) -> float:
        if self.buildable_polygon.area <= 0:
            return 0.0
        return (self.total_bya_m2 / float(self.buildable_polygon.area)) * 100.0

    @property
    def bra_pct(self) -> float:
        if self.buildable_polygon.area <= 0:
            return 0.0
        return (self.total_bra_m2 / float(self.buildable_polygon.area)) * 100.0


@dataclass
class OptionResult:
    concept_family: ConceptFamily
    title: str
    subtitle: str
    score: float
    masterplan: Masterplan
    concept_svg: str
    mua_svg: str
    summary: str
    stats: Dict[str, Any]
