from __future__ import annotations

"""Core datatypes for the Builtly masterplan engine.

This version adds an explicit intermediate composition layer:
- FieldSkeleton: frontage lines, build bands, courtyard reserve, view corridors
- FieldSkeletonSummary: serializable summary used by UI/report
- ArchitectureMetrics: deterministic architectural quality score
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Literal, Optional, Protocol, Tuple

try:
    from shapely.geometry import LineString, Point, Polygon
    from shapely import wkt as shapely_wkt
    HAS_SHAPELY = True
except Exception:  # pragma: no cover
    LineString = Point = Polygon = object  # type: ignore[assignment]
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


def _serialize_geom(geom: Any) -> Optional[str]:
    if geom is None:
        return None
    if not HAS_SHAPELY:
        raise RuntimeError("Shapely is required to serialize geometry.")
    return geom.wkt


def _deserialize_geom(text: Optional[str]) -> Any:
    if not text:
        return None
    if not HAS_SHAPELY or shapely_wkt is None:
        raise RuntimeError("Shapely is required to deserialize geometry.")
    return shapely_wkt.loads(text)


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
    field_role: str = ""
    character: str = ""
    arm_id: Optional[str] = None
    design_variant: Optional[str] = None
    design_karre_shape: Optional[str] = None
    design_height_pattern: Optional[str] = None
    target_bya_pct: Optional[float] = None
    skeleton_mode: Optional[str] = None
    frontage_mode: Optional[str] = None
    micro_band_count: int = 0
    view_corridor_count: int = 0
    courtyard_reserve_ratio: float = 0.0
    frontage_depth_m: Optional[float] = None
    corridor_width_m: Optional[float] = None
    max_neighbor_height_m: Optional[float] = None
    min_neighbor_distance_m: Optional[float] = None
    macro_structure: Optional[str] = None
    micro_field_pattern: Optional[str] = None
    symmetry_preference: Optional[str] = None
    composition_strictness: float = 0.0
    frontage_zone_ratio: float = 0.0
    public_realm_ratio: float = 0.0
    node_symmetry: bool = False
    frontage_primary_side: Optional[str] = None
    frontage_secondary_side: Optional[str] = None
    lamell_rhythm_mode: Optional[str] = None
    node_layout_mode: Optional[str] = None
    courtyard_open_side: Optional[str] = None
    target_building_count: int = 0
    frontage_emphasis: float = 0.0
    rhythm_strength: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_id": self.field_id,
            "polygon_wkt": _serialize_geom(self.polygon),
            "typology": self.typology.value,
            "orientation_deg": float(self.orientation_deg),
            "floors_min": int(self.floors_min),
            "floors_max": int(self.floors_max),
            "target_bra": float(self.target_bra),
            "courtyard_kind": self.courtyard_kind.value if self.courtyard_kind else None,
            "tower_size_m": self.tower_size_m,
            "phase": int(self.phase),
            "phase_label": self.phase_label,
            "field_role": self.field_role,
            "character": self.character,
            "arm_id": self.arm_id,
            "design_variant": self.design_variant,
            "design_karre_shape": self.design_karre_shape,
            "design_height_pattern": self.design_height_pattern,
            "target_bya_pct": self.target_bya_pct,
            "skeleton_mode": self.skeleton_mode,
            "frontage_mode": self.frontage_mode,
            "micro_band_count": int(self.micro_band_count),
            "view_corridor_count": int(self.view_corridor_count),
            "courtyard_reserve_ratio": float(self.courtyard_reserve_ratio),
            "frontage_depth_m": self.frontage_depth_m,
            "corridor_width_m": self.corridor_width_m,
            "max_neighbor_height_m": self.max_neighbor_height_m,
            "min_neighbor_distance_m": self.min_neighbor_distance_m,
            "macro_structure": self.macro_structure,
            "micro_field_pattern": self.micro_field_pattern,
            "symmetry_preference": self.symmetry_preference,
            "composition_strictness": float(self.composition_strictness),
            "frontage_zone_ratio": float(self.frontage_zone_ratio),
            "public_realm_ratio": float(self.public_realm_ratio),
            "node_symmetry": bool(self.node_symmetry),
            "frontage_primary_side": self.frontage_primary_side,
            "frontage_secondary_side": self.frontage_secondary_side,
            "lamell_rhythm_mode": self.lamell_rhythm_mode,
            "node_layout_mode": self.node_layout_mode,
            "courtyard_open_side": self.courtyard_open_side,
            "target_building_count": int(self.target_building_count),
            "frontage_emphasis": float(self.frontage_emphasis),
            "rhythm_strength": float(self.rhythm_strength),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Delfelt":
        return cls(
            field_id=str(data["field_id"]),
            polygon=_deserialize_geom(data.get("polygon_wkt")) or Polygon(),
            typology=Typology(data["typology"]),
            orientation_deg=float(data.get("orientation_deg", 0.0)),
            floors_min=int(data.get("floors_min", 1)),
            floors_max=int(data.get("floors_max", 1)),
            target_bra=float(data.get("target_bra", 0.0)),
            courtyard_kind=CourtyardKind(data["courtyard_kind"]) if data.get("courtyard_kind") else None,
            tower_size_m=data.get("tower_size_m"),
            phase=int(data.get("phase", 1)),
            phase_label=str(data.get("phase_label", "")),
            field_role=str(data.get("field_role", "")),
            character=str(data.get("character", "")),
            arm_id=data.get("arm_id"),
            design_variant=data.get("design_variant"),
            design_karre_shape=data.get("design_karre_shape"),
            design_height_pattern=data.get("design_height_pattern"),
            target_bya_pct=float(data["target_bya_pct"]) if data.get("target_bya_pct") is not None else None,
            skeleton_mode=data.get("skeleton_mode"),
            frontage_mode=data.get("frontage_mode"),
            micro_band_count=int(data.get("micro_band_count", 0) or 0),
            view_corridor_count=int(data.get("view_corridor_count", 0) or 0),
            courtyard_reserve_ratio=float(data.get("courtyard_reserve_ratio", 0.0) or 0.0),
            frontage_depth_m=float(data["frontage_depth_m"]) if data.get("frontage_depth_m") is not None else None,
            corridor_width_m=float(data["corridor_width_m"]) if data.get("corridor_width_m") is not None else None,
            max_neighbor_height_m=float(data["max_neighbor_height_m"]) if data.get("max_neighbor_height_m") is not None else None,
            min_neighbor_distance_m=float(data["min_neighbor_distance_m"]) if data.get("min_neighbor_distance_m") is not None else None,
            macro_structure=data.get("macro_structure"),
            micro_field_pattern=data.get("micro_field_pattern"),
            symmetry_preference=data.get("symmetry_preference"),
            composition_strictness=float(data.get("composition_strictness", 0.0) or 0.0),
            frontage_zone_ratio=float(data.get("frontage_zone_ratio", 0.0) or 0.0),
            public_realm_ratio=float(data.get("public_realm_ratio", 0.0) or 0.0),
            node_symmetry=bool(data.get("node_symmetry", False)),
            frontage_primary_side=data.get("frontage_primary_side"),
            frontage_secondary_side=data.get("frontage_secondary_side"),
            lamell_rhythm_mode=data.get("lamell_rhythm_mode"),
            node_layout_mode=data.get("node_layout_mode"),
            courtyard_open_side=data.get("courtyard_open_side"),
            target_building_count=int(data.get("target_building_count", 0) or 0),
            frontage_emphasis=float(data.get("frontage_emphasis", 0.0) or 0.0),
            rhythm_strength=float(data.get("rhythm_strength", 0.0) or 0.0),
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
    # Arkitektoniske dimensjoner (valgfrie — settes eksplisitt av
    # typologi_primitiver, ellers beregnes fra footprint via properties).
    length_m_explicit: Optional[float] = None
    depth_m_explicit: Optional[float] = None

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

    @property
    def length_m(self) -> float:
        """Lang side (lengde) av bygget. Fra eksplisitt felt hvis satt,
        ellers fra minimum rotated rectangle av footprint.

        Merk: for ring-formede karré-bygg vil footprint være en polygon
        med hull (O-form), og bounding-box gir dermed dimensjoner av
        HELE karréen (ytre ramme), ikke armens faktiske bredde.
        """
        explicit = getattr(self, "length_m_explicit", None)
        if explicit is not None:
            return float(explicit)
        return _compute_bbox_dims(self.footprint)[0]

    @property
    def depth_m(self) -> float:
        """Kort side (dybde) av bygget. Se length_m."""
        explicit = getattr(self, "depth_m_explicit", None)
        if explicit is not None:
            return float(explicit)
        return _compute_bbox_dims(self.footprint)[1]

    @property
    def dimension_label(self) -> str:
        """Menneskelig-lesbar dim-label: '52×13m × 6et / 18.6m' """
        return f"{self.length_m:.0f}×{self.depth_m:.0f}m × {self.floors}et / {self.height_m:.1f}m"


def _compute_bbox_dims(footprint: Polygon) -> Tuple[float, float]:
    """Returnér (lang_side, kort_side) fra polygonets minimum rotated rectangle.

    For roterte bygg gir shapely bounds aksejusterte mål som er feil; vi
    bruker `minimum_rotated_rectangle` for å få riktig lengde og bredde.
    """
    if footprint is None or footprint.is_empty:
        return (0.0, 0.0)
    try:
        mrr = footprint.minimum_rotated_rectangle
        coords = list(mrr.exterior.coords)
        if len(coords) < 4:
            return (0.0, 0.0)
        # MRR har 5 punkter (lukket). Beregn sidelengder.
        import math as _m
        side_a = _m.hypot(coords[1][0] - coords[0][0], coords[1][1] - coords[0][1])
        side_b = _m.hypot(coords[2][0] - coords[1][0], coords[2][1] - coords[1][1])
        return (max(side_a, side_b), min(side_a, side_b))
    except Exception:
        # Fallback: aksejustert bbox
        minx, miny, maxx, maxy = footprint.bounds
        w = maxx - minx
        h = maxy - miny
        return (max(w, h), min(w, h))


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
    # Kontekst-adaptive felter (fra mua.py _resolve_mua_context):
    # Gjør at MUA-vurderingen kan være strict/reduced/advisory basert på
    # tomtens tetthet og størrelse. Tette infill-prosjekter får redusert
    # eller rådgivende MUA-krav; større boligfelt får strict.
    mode: str = "strict"  # "strict" | "reduced" | "advisory"
    effective_requirement_factor: float = 1.0
    score_weight: float = 1.0
    advisory_override: bool = False

    @property
    def compliant(self) -> bool:
        relevant = [check for check in self.checks if check.status != ComplianceState.IKKE_VURDERT]
        return bool(relevant) and all(check.status == ComplianceState.JA for check in relevant)


@dataclass
class SkeletonFrontage:
    role: str = ""
    line: Optional[LineString] = None


@dataclass
class FieldSkeleton:
    field_id: str = ""
    mode: str = ""
    local_orientation_deg: float = 0.0
    frontage_lines: List[SkeletonFrontage] = field(default_factory=list)
    build_bands: List[Polygon] = field(default_factory=list)
    courtyard_reserve: Optional[Polygon] = None
    view_corridors: List[Polygon] = field(default_factory=list)
    accent_nodes: List[Point] = field(default_factory=list)
    open_edges: List[str] = field(default_factory=list)
    frontage_depth_m: float = 0.0
    corridor_width_m: float = 0.0
    macro_axis: Optional[LineString] = None
    symmetry_axis: Optional[LineString] = None
    frontage_zones: List[Polygon] = field(default_factory=list)
    micro_fields: List[Polygon] = field(default_factory=list)
    public_realm: List[Polygon] = field(default_factory=list)
    reserved_open_space: List[Polygon] = field(default_factory=list)
    frontage_primary_side: Optional[str] = None
    frontage_secondary_side: Optional[str] = None
    build_slots: List[Polygon] = field(default_factory=list)
    node_layout_mode: Optional[str] = None


@dataclass
class FieldSkeletonSummary:
    field_id: str = ""
    skeleton_mode: str = ""
    frontage_count: int = 0
    micro_band_count: int = 0
    courtyard_reserve_m2: float = 0.0
    view_corridor_m2: float = 0.0
    accent_node_count: int = 0
    build_band_area_m2: float = 0.0
    frontage_depth_m: float = 0.0
    corridor_width_m: float = 0.0
    macro_axis_m: float = 0.0
    symmetry_axis_m: float = 0.0
    frontage_zone_area_m2: float = 0.0
    micro_field_count: int = 0
    public_realm_m2: float = 0.0
    reserved_open_space_m2: float = 0.0
    build_slot_count: int = 0


@dataclass
class ArchitectureMetrics:
    frontage_continuity: float = 0.0
    courtyard_clarity: float = 0.0
    axis_symmetry: float = 0.0
    view_corridor_quality: float = 0.0
    typology_purity: float = 0.0
    building_entropy: float = 0.0
    bya_fitness: float = 0.0
    public_realm_clarity: float = 0.0
    rhythm_score: float = 0.0
    frontage_gap_penalty: float = 0.0
    isolated_building_penalty: float = 0.0
    frontage_regularity: float = 0.0
    courtyard_enclosure: float = 0.0
    microfield_utilization: float = 0.0
    total_score: float = 0.0
    notes: List[str] = field(default_factory=list)


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
    field_role: str = ""
    character: str = ""
    arm_id: Optional[str] = None
    design_variant: Optional[str] = None
    design_karre_shape: Optional[str] = None
    design_height_pattern: Optional[str] = None
    target_bya_pct: Optional[float] = None
    skeleton_mode: Optional[str] = None
    frontage_mode: Optional[str] = None
    micro_band_count: int = 0
    view_corridor_count: int = 0
    courtyard_reserve_ratio: float = 0.0
    frontage_depth_m: Optional[float] = None
    corridor_width_m: Optional[float] = None
    macro_structure: Optional[str] = None
    micro_field_pattern: Optional[str] = None
    symmetry_preference: Optional[str] = None
    composition_strictness: float = 0.0
    frontage_zone_ratio: float = 0.0
    public_realm_ratio: float = 0.0
    node_symmetry: bool = False
    frontage_primary_side: Optional[str] = None
    frontage_secondary_side: Optional[str] = None
    lamell_rhythm_mode: Optional[str] = None
    node_layout_mode: Optional[str] = None
    courtyard_open_side: Optional[str] = None
    target_building_count: int = 0
    frontage_emphasis: float = 0.0
    rhythm_strength: float = 0.0


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
    skeleton_summaries: List[FieldSkeletonSummary] = field(default_factory=list)
    architecture_report: ArchitectureMetrics = field(default_factory=ArchitectureMetrics)
    # --- Masterplan-struktur (uke 1 av arkitektkvalitet-løftet) ---
    # Bevisst løse felter (ikke en MasterplanAxes-referanse) for å unngå
    # sirkulær import mellom masterplan_structure og masterplan_types.
    axes_primary_line: Optional[LineString] = None
    axes_secondary_line: Optional[LineString] = None
    axes_corridor_polygons: List[Polygon] = field(default_factory=list)
    axes_torg_polygons: List[Polygon] = field(default_factory=list)
    axes_torg_points: List[Point] = field(default_factory=list)
    axes_type: str = ""              # "diagonal" | "orthogonal" | "none" | ""
    axes_profile: str = ""           # "FORSTAD" | "URBAN"
    axes_rationale: str = ""
    axes_elongation: float = 0.0
    axes_neighbor_asymmetry: float = 0.0
    axes_primary_orientation_deg: float = 0.0

    def iter_buildings_for_delfelt(self, field_id: str) -> Iterable[Bygg]:
        return (bygg for bygg in self.bygg if bygg.delfelt_id == field_id)

    def delfelt_by_id(self, field_id: str) -> Optional[Delfelt]:
        for field_obj in self.delfelt:
            if field_obj.field_id == field_id:
                return field_obj
        return None


class StructuredPassClient(Protocol):
    def __call__(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]: ...
