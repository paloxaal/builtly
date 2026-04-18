"""
Datastrukturer for Builtly masterplan-motor.

Målet med denne modulen er å erstatte den gamle A/B/C-alternativtenkningen
med én helhetlig masterplan som:
  - Har programmiks (bolig + barnehage + næring)
  - Typologisoner (lamell+punkt+rekke kan sameksistere)
  - Uterom-system (diagonal, tun, MUA fordelt bakke/tak/privat)
  - Byggefaser à 3500-4500 m² BRA (standalone bokvalitet)
  - Parkeringsfaser (separat men koblet til byggefaser)

Alle geometri-felt bruker shapely-objekter i lokal meter-projeksjon (EPSG:25833).
Serialisering til JSON håndteres via to_dict()/from_dict() som konverterer
polygoner til WKT-strenger.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional, Tuple
import json
import math

try:
    from shapely.geometry import (
        Polygon, MultiPolygon, Point, LineString, mapping, shape
    )
    from shapely import wkt as shapely_wkt
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except Exception:
    HAS_SHAPELY = False
    Polygon = MultiPolygon = Point = LineString = object  # type: ignore


# ─────────────────────────────────────────────────────────────────────
# Program og typologi
# ─────────────────────────────────────────────────────────────────────

ProgramKind = Literal["bolig", "barnehage", "naering", "service", "felleshus"]
TypologyKind = Literal["Lamell", "Punkthus", "Karré", "Rekke", "Tun", "Tårn", "Podium + Tårn"]


@dataclass
class ProgramAllocation:
    """Hvor mye areal av hver type skal prosjektet ha."""
    bolig_bra: float = 0.0
    barnehage_bra: float = 0.0  # f.eks. 1279 for 6-base
    barnehage_uteareal_m2: float = 0.0  # tilknyttet ute-areal
    naering_bra: float = 0.0  # service, dagligvare etc
    service_bra: float = 0.0  # fellesfunksjoner for beboere (trening, co-working)
    felleshus_bra: float = 0.0

    # Avledede krav
    mua_total_required: float = 0.0  # totalt uteoppholdsareal krav (byggesone 2: 40 m² per bolig)
    mua_bakke_min: float = 0.0       # min 50% på bakkeplan
    mua_felles_min: float = 0.0      # min 50% som fellesareal
    parking_spaces_required: int = 0

    notes: str = ""

    @property
    def total_bra(self) -> float:
        return (
            self.bolig_bra + self.barnehage_bra + self.naering_bra
            + self.service_bra + self.felleshus_bra
        )

    def unit_estimate(self, avg_unit_bra: float = 70.0) -> int:
        if avg_unit_bra <= 0:
            return 0
        return max(0, int(round(self.bolig_bra / avg_unit_bra)))


@dataclass
class TypologyZone:
    """En sone på tomta der en gitt typologi passer.

    Sonene tegnes av Pass 2 (Typology Zoning) basert på kontekst:
    - nærhet til småhus → lavere typologi (Rekke, Lamell 3-4 et)
    - nærhet til hovedveg/urbane kanter → høyere (Punkthus, Tårn)
    - gårdsrom-kandidater → Karré
    """
    zone_id: str
    typology: TypologyKind
    polygon: Any                       # shapely Polygon
    floors_min: int = 3
    floors_max: int = 5
    target_bra: float = 0.0            # hvor mye BRA som forventes i denne sonen
    rationale: str = ""

    # Valgfrie føringer
    sun_orientation_priority: Literal["S", "SV", "SØ", "Ø", "V"] = "S"
    distance_to_nearest_smallhouse_m: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────
# Uterom-systemet
# ─────────────────────────────────────────────────────────────────────

OutdoorKind = Literal[
    "diagonal",          # hovedferdselsåre (LPO-mønster)
    "tun",               # lokalt gårdsrom mellom bygg
    "park",              # offentlig-lignende park
    "lek",               # lekeplass
    "barnehage_ute",     # barnehage-uteareal (må være sammenhengende)
    "privat_forhage",    # buffer mellom bygg og felles
    "tak_mua",           # uterom på tak (teller som MUA)
    "gangforbindelse",   # linje, ikke flate
]


@dataclass
class OutdoorZone:
    zone_id: str
    kind: OutdoorKind
    geometry: Any                      # Polygon for flater, LineString for forbindelser
    area_m2: float = 0.0
    counts_toward_mua: bool = True
    is_felles: bool = True             # felles vs privat
    on_ground: bool = True             # False = tak
    serves_building_phases: List[int] = field(default_factory=list)
    requires_sun_hours: float = 0.0    # 4t ved vårjevndøgn kl 12 = TEK-krav
    notes: str = ""


@dataclass
class OutdoorSystem:
    zones: List[OutdoorZone] = field(default_factory=list)
    diagonal_linestring: Optional[Any] = None  # hovedaksens geometri
    gangnett: List[Any] = field(default_factory=list)  # list av LineString

    def mua_on_ground_felles(self) -> float:
        return sum(z.area_m2 for z in self.zones
                   if z.counts_toward_mua and z.is_felles and z.on_ground)

    def mua_on_roof(self) -> float:
        return sum(z.area_m2 for z in self.zones
                   if z.counts_toward_mua and not z.on_ground)

    def mua_privat(self) -> float:
        return sum(z.area_m2 for z in self.zones
                   if z.counts_toward_mua and not z.is_felles)

    def mua_total(self) -> float:
        return sum(z.area_m2 for z in self.zones if z.counts_toward_mua)


# ─────────────────────────────────────────────────────────────────────
# Volum (ett bygg i masterplan)
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Volume:
    volume_id: str                     # f.eks. "V-A1" (brukes på tvers av faser)
    name: str                          # visningsnavn f.eks. "Hus A"
    polygon: Any                       # fotavtrykk som shapely Polygon
    typology: TypologyKind
    floors: int
    height_m: float
    width_m: float
    depth_m: float
    angle_deg: float
    cx: float
    cy: float
    footprint_m2: float
    program: ProgramKind = "bolig"     # hoved-program i volumet
    ground_floor_program: Optional[ProgramKind] = None  # kan skille 1. etg fra resten
    has_courtyard: bool = False
    ring_depth_m: float = 0.0
    oppganger: int = 1                 # antall trapp/heis-oppganger i volumet
    units_estimate: int = 0

    # Metadata for fasering
    assigned_phase: Optional[int] = None     # hvilken byggefase volumet hører til
    zone_id: Optional[str] = None            # hvilken typologisone

    notes: str = ""

    @property
    def bra_m2(self) -> float:
        return self.footprint_m2 * self.floors * 0.85  # grov BRA-faktor


# ─────────────────────────────────────────────────────────────────────
# Parkering
# ─────────────────────────────────────────────────────────────────────

@dataclass
class ParkingRamp:
    """En rampe inn til p-kjeller fra offentlig veg."""
    ramp_id: str
    point: Any                         # shapely Point
    access_from_road: str = ""         # f.eks. "Paul Fjermstands veg"
    handles_construction_traffic: bool = True


@dataclass
class ParkingPhase:
    """En byggefase for p-kjeller. Kan serve flere byggefaser over bakken."""
    phase_number: int                  # P1, P2, ...
    polygon: Any                       # utstrekning av kjelleren (under bakken)
    num_spaces: int
    ramps: List[ParkingRamp] = field(default_factory=list)
    extends_parking_phases: List[int] = field(default_factory=list)  # P2 kan koble på P1
    serves_building_phases: List[int] = field(default_factory=list)
    construction_sequence: int = 0     # rekkefølge i tid (0 = først)
    must_complete_before_building_phase: Optional[int] = None
    notes: str = ""


# ─────────────────────────────────────────────────────────────────────
# Byggefase (over bakken)
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Entrance:
    """En oppgang / inngang til et volum."""
    entrance_id: str
    point: Any                         # shapely Point (ved fotavtrykk-kant)
    volume_id: str
    serves_units: int = 0
    access_path: Optional[Any] = None  # LineString fra offentlig fortau


@dataclass
class BuildingPhase:
    """En byggefase over bakken.

    Hovedregel: BRA per fase = 3500-4500 m² (snitt 4000),
    myk nedre grense 2500, myk øvre 6500.

    Hver fase må kunne stå som selvstendig bomiljø: egne oppganger,
    dedikert uterom, myk-adkomst uten å krysse byggeplass.
    """
    phase_number: int                  # 1, 2, 3...
    label: str = ""                    # f.eks. "Trinn 1 — vestre lameller + barnehage"
    volume_ids: List[str] = field(default_factory=list)

    # Program og areal
    target_bra: float = 0.0
    actual_bra: float = 0.0
    programs_included: List[ProgramKind] = field(default_factory=list)
    units_estimate: int = 0

    # Adkomst og drift
    oppganger: List[Entrance] = field(default_factory=list)
    soft_access_paths: List[Any] = field(default_factory=list)  # LineStrings fra offentlig fortau

    # Uterom tilknyttet denne fasen
    standalone_outdoor_zone_ids: List[str] = field(default_factory=list)
    standalone_outdoor_m2: float = 0.0
    standalone_outdoor_has_sun: bool = False

    # Parkering
    parking_served_by: List[int] = field(default_factory=list)  # P-fase-numre

    # Rekkefølge og byggeplass
    depends_on_phases: List[int] = field(default_factory=list)
    estimated_start_year: Optional[int] = None
    estimated_duration_months: Optional[int] = None
    construction_barrier_zone: Optional[Any] = None  # polygon rundt fasen der naboer får byggeplass

    # Validering
    standalone_habitable: bool = False           # passerer alle standalone-sjekkene
    standalone_issues: List[str] = field(default_factory=list)
    neighboring_construction_risk: float = 0.0   # 0-1, hvor mye byggeplass er rundt senere

    notes: str = ""


# ─────────────────────────────────────────────────────────────────────
# Faseringskonfigurasjon (ny bruker-input)
# ─────────────────────────────────────────────────────────────────────

PhasingMode = Literal["auto", "single", "manual"]
ParkingMode = Literal["auto", "single_garage", "manual"]


@dataclass
class PhasingConfig:
    """Bruker-innstillinger for fasering, settes i UI før motoren kjører."""
    phasing_mode: PhasingMode = "auto"
    manual_phase_count: Optional[int] = None

    parking_mode: ParkingMode = "auto"
    manual_parking_phase_count: Optional[int] = None

    # Harde grenser som ikke kan overstyres av bruker
    MIN_PHASE_BRA: float = 2500.0
    MAX_PHASE_BRA: float = 6500.0
    TARGET_PHASE_BRA_LOW: float = 3500.0
    TARGET_PHASE_BRA_HIGH: float = 4500.0
    SINGLE_PHASE_MAX_BRA: float = 7500.0  # over dette er det minst 2 faser uansett

    def recommended_phase_count(self, target_bra: float) -> Tuple[int, int, int]:
        """Returner (anbefalt, min_rimelig, max_rimelig) antall faser."""
        if target_bra <= 0:
            return (1, 1, 1)
        if target_bra <= 5000:
            return (1, 1, 2)
        if target_bra <= self.SINGLE_PHASE_MAX_BRA:
            # 5000-7500: kan være 1 eller 2
            return (2, 1, 2)
        # over 7500: minst 2 faser
        target_avg = (self.TARGET_PHASE_BRA_LOW + self.TARGET_PHASE_BRA_HIGH) / 2.0  # 4000
        recommended = max(2, int(round(target_bra / target_avg)))
        min_reasonable = max(2, int(math.ceil(target_bra / self.MAX_PHASE_BRA)))
        max_reasonable = max(min_reasonable, int(math.floor(target_bra / self.MIN_PHASE_BRA)))
        return (recommended, min_reasonable, max_reasonable)

    def resolve_phase_count(self, target_bra: float) -> int:
        """Løs faktisk antall faser basert på modus og mål-BRA."""
        if self.phasing_mode == "single":
            return 1
        if self.phasing_mode == "manual" and self.manual_phase_count:
            return max(1, self.manual_phase_count)
        # auto
        return self.recommended_phase_count(target_bra)[0]

    def validate_manual_choice(self, target_bra: float, k: int) -> List[str]:
        """Returner liste med advarsler om manuelt valg. Tom liste = OK."""
        warnings = []
        if k < 1:
            warnings.append("Antall faser må være minst 1.")
            return warnings
        if target_bra <= 0:
            return warnings

        avg_per_phase = target_bra / k
        if avg_per_phase > self.MAX_PHASE_BRA:
            warnings.append(
                f"Snitt {avg_per_phase:.0f} m² per fase er større enn {self.MAX_PHASE_BRA:.0f}. "
                f"Vurder flere faser for å holde byggetrinnene håndterbare."
            )
        elif avg_per_phase > self.TARGET_PHASE_BRA_HIGH + 500:
            warnings.append(
                f"Snitt {avg_per_phase:.0f} m² per fase er over målområdet "
                f"({self.TARGET_PHASE_BRA_LOW:.0f}–{self.TARGET_PHASE_BRA_HIGH:.0f}). "
                f"Byggetrinn kan bli tunge å finansiere og selge i bolker."
            )

        if avg_per_phase < self.MIN_PHASE_BRA:
            warnings.append(
                f"Snitt {avg_per_phase:.0f} m² per fase er mindre enn {self.MIN_PHASE_BRA:.0f}. "
                f"Små byggetrinn gir ofte ulønnsom drift og vanskelig standalone-uterom."
            )

        if k == 1 and target_bra > self.SINGLE_PHASE_MAX_BRA:
            warnings.append(
                f"Ett byggetrinn på {target_bra:.0f} m² BRA er over anbefalt maksgrense "
                f"({self.SINGLE_PHASE_MAX_BRA:.0f}). Del i minst 2 faser."
            )

        return warnings


# ─────────────────────────────────────────────────────────────────────
# Masterplan — øverste nivå
# ─────────────────────────────────────────────────────────────────────

@dataclass
class MasterplanMetrics:
    total_bra: float = 0.0
    total_bta: float = 0.0
    total_footprint_m2: float = 0.0
    bya_percent: float = 0.0
    units_total: int = 0
    phase_count_buildings: int = 0
    phase_count_parking: int = 0
    mua_total_m2: float = 0.0
    mua_required_m2: float = 0.0
    mua_compliant: bool = False
    avg_phase_bra: float = 0.0
    min_phase_bra: float = 0.0
    max_phase_bra: float = 0.0
    standalone_habitability_score: float = 0.0  # 0-100: andel av faser som er standalone OK
    overall_score: float = 0.0


@dataclass
class Masterplan:
    site_polygon: Any
    buildable_polygon: Any

    program: ProgramAllocation
    typology_zones: List[TypologyZone]
    volumes: List[Volume]
    outdoor_system: OutdoorSystem
    building_phases: List[BuildingPhase]
    parking_phases: List[ParkingPhase]

    metrics: MasterplanMetrics = field(default_factory=MasterplanMetrics)

    phasing_config: PhasingConfig = field(default_factory=PhasingConfig)
    site_inputs: Dict[str, Any] = field(default_factory=dict)
    concept_narrative: str = ""
    warnings: List[str] = field(default_factory=list)
    source: str = "Builtly Masterplan v1"

    # ---------------------------------------------------------------
    # Convenience lookups
    # ---------------------------------------------------------------
    def volume_by_id(self, volume_id: str) -> Optional[Volume]:
        for v in self.volumes:
            if v.volume_id == volume_id:
                return v
        return None

    def phase_by_number(self, n: int) -> Optional[BuildingPhase]:
        for p in self.building_phases:
            if p.phase_number == n:
                return p
        return None

    def parking_by_number(self, n: int) -> Optional[ParkingPhase]:
        for p in self.parking_phases:
            if p.phase_number == n:
                return p
        return None

    def volumes_in_phase(self, phase_number: int) -> List[Volume]:
        phase = self.phase_by_number(phase_number)
        if not phase:
            return []
        return [self.volume_by_id(vid) for vid in phase.volume_ids if self.volume_by_id(vid)]

    # ---------------------------------------------------------------
    # Legacy-kompatibel output for Mulighetsstudie.py
    # ---------------------------------------------------------------
    def to_legacy_buildings(self) -> List[Dict[str, Any]]:
        """Konverter volumes til det gamle buildings-formatet som rapport/3D-scene bruker."""
        out = []
        for v in self.volumes:
            out.append({
                "polygon": v.polygon,
                "name": v.name,
                "role": "main",
                "floors": v.floors,
                "height_m": v.height_m,
                "width_m": v.width_m,
                "depth_m": v.depth_m,
                "angle_deg": v.angle_deg,
                "area_m2": v.footprint_m2,
                "notes": v.notes,
                "cx": v.cx,
                "cy": v.cy,
                "courtyard": v.has_courtyard,
                "ring_depth": v.ring_depth_m,
                "pos_id": v.volume_id,
                # nye felter for masterplan
                "phase": v.assigned_phase,
                "typology": v.typology,
                "program": v.program,
                "zone_id": v.zone_id,
            })
        return out

    def to_legacy_result(self) -> Dict[str, Any]:
        """Returner legacy-formatet som plan_site() gir — for drop-in i eksisterende pipeline."""
        buildings = self.to_legacy_buildings()
        footprint = None
        if HAS_SHAPELY and self.volumes:
            polys = [v.polygon for v in self.volumes if v.polygon is not None]
            if polys:
                footprint = unary_union(polys).buffer(0)
        return {
            "buildings": buildings,
            "footprint": footprint,
            "building_count": len(buildings),
            "total_footprint_m2": round(self.metrics.total_footprint_m2, 1),
            "total_bta_m2": round(self.metrics.total_bta, 1),
            "source": self.source,
            "concept": self.concept_narrative,
            "positions_evaluated": 0,
            "positions_usable": 0,
            # nye masterplan-felter (ikke i gammelt format, men pipeline kan plukke opp)
            "masterplan": self,
            "building_phases": self.building_phases,
            "parking_phases": self.parking_phases,
            "outdoor_system": self.outdoor_system,
            "typology_zones": self.typology_zones,
            "program": self.program,
            "metrics": asdict(self.metrics) if self.metrics else {},
            "warnings": self.warnings,
        }


# ─────────────────────────────────────────────────────────────────────
# Serialisering
# ─────────────────────────────────────────────────────────────────────

def _geom_to_wkt(geom) -> Optional[str]:
    if geom is None:
        return None
    try:
        return geom.wkt
    except Exception:
        return None


def _wkt_to_geom(wkt: Optional[str]):
    if not wkt or not HAS_SHAPELY:
        return None
    try:
        return shapely_wkt.loads(wkt)
    except Exception:
        return None


def masterplan_to_json(mp: Masterplan) -> Dict[str, Any]:
    """Konverter masterplan til JSON-serialiserbar dict (polygoner → WKT)."""
    def _vol(v: Volume) -> Dict[str, Any]:
        d = asdict(v)
        d["polygon"] = _geom_to_wkt(v.polygon)
        return d

    def _zone(z: TypologyZone) -> Dict[str, Any]:
        d = asdict(z)
        d["polygon"] = _geom_to_wkt(z.polygon)
        return d

    def _out(o: OutdoorZone) -> Dict[str, Any]:
        d = asdict(o)
        d["geometry"] = _geom_to_wkt(o.geometry)
        return d

    def _bphase(p: BuildingPhase) -> Dict[str, Any]:
        d = asdict(p)
        d["construction_barrier_zone"] = _geom_to_wkt(p.construction_barrier_zone)
        d["soft_access_paths"] = [_geom_to_wkt(g) for g in p.soft_access_paths]
        d["oppganger"] = [
            {**asdict(e), "point": _geom_to_wkt(e.point),
             "access_path": _geom_to_wkt(e.access_path)}
            for e in p.oppganger
        ]
        return d

    def _pphase(p: ParkingPhase) -> Dict[str, Any]:
        d = asdict(p)
        d["polygon"] = _geom_to_wkt(p.polygon)
        d["ramps"] = [
            {**asdict(r), "point": _geom_to_wkt(r.point)} for r in p.ramps
        ]
        return d

    return {
        "site_polygon": _geom_to_wkt(mp.site_polygon),
        "buildable_polygon": _geom_to_wkt(mp.buildable_polygon),
        "program": asdict(mp.program),
        "typology_zones": [_zone(z) for z in mp.typology_zones],
        "volumes": [_vol(v) for v in mp.volumes],
        "outdoor_system": {
            "zones": [_out(z) for z in mp.outdoor_system.zones],
            "diagonal_linestring": _geom_to_wkt(mp.outdoor_system.diagonal_linestring),
            "gangnett": [_geom_to_wkt(g) for g in mp.outdoor_system.gangnett],
        },
        "building_phases": [_bphase(p) for p in mp.building_phases],
        "parking_phases": [_pphase(p) for p in mp.parking_phases],
        "metrics": asdict(mp.metrics),
        "phasing_config": asdict(mp.phasing_config),
        "site_inputs": mp.site_inputs,
        "concept_narrative": mp.concept_narrative,
        "warnings": mp.warnings,
        "source": mp.source,
    }
