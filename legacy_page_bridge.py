from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence

from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon, box

from .masterplan_engine import generate_concept_masterplans, validate_masterplan_geometry
from .masterplan_types import BarnehageConfig, Masterplan, PlanRegler

PHASE_COLORS_HEX = ["#1f375f", "#42679b", "#49b4dc", "#2aa999", "#6e849e", "#94a3b8"]

# Builtly-tilpassede presentasjonsfarger for arkitektdiagrammer.
# Holder volumene rolige i rapport/PDF og unngår neon-fasepreg.
TYPOLOGY_DIAGRAM_COLORS: Dict[str, List[int]] = {
    "Lamell": [64, 126, 188, 232],
    "Punkthus": [74, 184, 179, 232],
    "Karré": [66, 103, 155, 232],
    "Rekkehus": [92, 149, 120, 232],
    "Tun": [116, 132, 164, 232],
}




def _footprint_dims(poly: Any) -> tuple[float, float]:
    if poly is None or getattr(poly, "is_empty", True):
        return 0.0, 0.0
    try:
        rect = poly.minimum_rotated_rectangle
        coords = list(rect.exterior.coords)
        lengths = []
        for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
            edge = math.hypot(float(x2) - float(x1), float(y2) - float(y1))
            if edge > 1e-6:
                lengths.append(edge)
        if not lengths:
            return 0.0, 0.0
        return float(max(lengths)), float(min(lengths))
    except Exception:
        try:
            minx, miny, maxx, maxy = poly.bounds
            return float(maxx - minx), float(maxy - miny)
        except Exception:
            return 0.0, 0.0

GENERISK_TEK17_NORGE = PlanRegler(
    max_bya_pct=35.0,
    max_floors=6,
    max_height_m=21.0,
    mua_per_bolig_m2=40.0,
    mua_min_felles_pct=0.5,
    mua_min_bakke_pct=0.5,
    brann_avstand_m=8.0,
    source_name="Generisk TEK17",
)

TRONDHEIM_KPA_2022_SONE_2 = PlanRegler(
    max_bya_pct=35.0,
    max_floors=6,
    max_height_m=21.0,
    mua_per_bolig_m2=40.0,
    mua_min_felles_pct=0.5,
    mua_min_bakke_pct=0.5,
    brann_avstand_m=8.0,
    source_name="Trondheim KPA 2022 sone 2",
)


@dataclass
class PhasingConfig:
    requested_delfelt_count: Optional[int] = None
    phasing_mode: str = "delfelt"
    parking_mode: str = "auto"

    def resolve_phase_count(self, target_bra_m2: float) -> int:
        if self.requested_delfelt_count and self.requested_delfelt_count > 0:
            return int(self.requested_delfelt_count)
        if target_bra_m2 < 5000:
            return 1
        if target_bra_m2 < 12000:
            return 2
        if target_bra_m2 < 22000:
            return 3
        if target_bra_m2 < 35000:
            return 4
        if target_bra_m2 < 55000:
            return 5
        return 6


@dataclass
class V8OptionResult:
    option_id: str
    title: str
    score: float
    masterplan: Masterplan
    score_breakdown: Dict[str, float] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.title

    @property
    def total_bra_m2(self) -> float:
        return float(getattr(self.masterplan, "total_bra_m2", 0.0) or 0.0)

    @property
    def total_bya_m2(self) -> float:
        return float(getattr(self.masterplan, "total_bya_m2", 0.0) or 0.0)

    @property
    def antall_boliger(self) -> int:
        return int(getattr(self.masterplan, "antall_boliger", 0) or 0)

    @property
    def sol_score(self) -> float:
        return float(getattr(getattr(self.masterplan, "sol_report", None), "total_sol_score", 0.0) or 0.0)

    @property
    def typology_mix(self) -> List[str]:
        bygg = list(getattr(self.masterplan, "bygg", []) or [])
        return sorted({getattr(getattr(b, "typology", None), "value", str(getattr(b, "typology", ""))) for b in bygg if b is not None})


@dataclass
class SyntheticParkingPhase:
    phase_number: int
    num_spaces: int
    serves_building_phases: List[int] = field(default_factory=list)
    ramps: List[str] = field(default_factory=list)
    extends_parking_phases: List[int] = field(default_factory=list)
    notes: str = ""


@dataclass
class SyntheticMetrics:
    phase_count_buildings: int
    avg_phase_bra: float
    min_phase_bra: float
    max_phase_bra: float
    phase_count_parking: int
    standalone_habitability_score: float
    mua_required_m2: float
    mua_compliant: bool


class SyntheticOutdoorSystem:
    def __init__(self, plan: Masterplan):
        self._plan = plan
        self.diagonal_linestring = self._build_diagonal(plan)

    @staticmethod
    def _build_diagonal(plan: Masterplan) -> Optional[LineString]:
        poly = getattr(plan, "buildable_polygon", None)
        if poly is None or getattr(poly, "is_empty", True):
            return None
        minx, miny, maxx, maxy = poly.bounds
        line = LineString([(minx, miny), (maxx, maxy)])
        try:
            clipped = line.intersection(poly.buffer(0))
            return clipped if not getattr(clipped, "is_empty", True) else line
        except Exception:
            return line

    def mua_on_ground_felles(self) -> float:
        report = getattr(self._plan, "mua_report", None)
        return float(getattr(report, "bakke", 0.0) or 0.0)

    def mua_on_roof(self) -> float:
        report = getattr(self._plan, "mua_report", None)
        return float(getattr(report, "tak", 0.0) or 0.0)

    def mua_total(self) -> float:
        report = getattr(self._plan, "mua_report", None)
        return float(getattr(report, "total", 0.0) or 0.0)


class LegacyMasterplanBundle:
    def __init__(self, best_plan: Masterplan, option_results: Sequence[V8OptionResult]):
        self.best_plan = best_plan
        self.option_results = list(option_results)
        self.building_phases = self._build_phases(best_plan)
        self.parking_phases = self._build_parking_phases(best_plan)
        self.metrics = self._build_metrics(best_plan)
        self.outdoor_system = SyntheticOutdoorSystem(best_plan)
        self.warnings = self._build_warnings(best_plan)

    def _build_phases(self, plan: Masterplan) -> List[Dict[str, Any]]:
        phases: List[Dict[str, Any]] = []
        phase_ids = sorted({int(getattr(f, "phase", 1) or 1) for f in plan.delfelt})
        phase_count = max(1, len(phase_ids))
        for p in phase_ids:
            fields = [f for f in plan.delfelt if int(getattr(f, "phase", 1) or 1) == p]
            field_ids = [f.field_id for f in fields]
            buildings = [b for b in plan.bygg if int(getattr(b, "phase", 1) or 1) == p or getattr(b, "delfelt_id", "") in field_ids]
            actual_bra = float(sum(getattr(b, "bra_m2", 0.0) or 0.0 for b in buildings))
            units_estimate = int(round(actual_bra / 55.0)) if actual_bra > 0 else 0
            field_area = float(sum(getattr(f.polygon, "area", 0.0) or 0.0 for f in fields))
            built_area = float(sum(getattr(b, "footprint_m2", 0.0) or 0.0 for b in buildings))
            standalone_outdoor_m2 = max(0.0, field_area - built_area)
            phase_target = float(sum(getattr(f, "target_bra", 0.0) or 0.0 for f in fields))
            fit_ratio = actual_bra / max(phase_target, 1.0) if phase_target > 0 else 1.0
            standalone_issues: List[str] = []
            if standalone_outdoor_m2 < max(600.0, units_estimate * 14.0):
                standalone_issues.append("Uteromsdekningen er knapp i dette trinnet.")
            if fit_ratio < 0.70:
                standalone_issues.append("Trinnet leverer lite program i forhold til feltets mål.")
            standalone_habitable = not standalone_issues
            phase_label = ", ".join(field_ids) or f"Trinn {p}"
            programs = ["bolig"]
            if getattr(plan.barnehage_config, "enabled", False) and p == 1:
                programs.append("barnehage")
            phases.append({
                "phase": p,
                "phase_number": p,
                "label": phase_label,
                "field_ids": field_ids,
                "bta_m2": float(sum((getattr(b, "footprint_m2", 0.0) or 0.0) * (getattr(b, "floors", 1) or 1) for b in buildings)),
                "bra_m2": actual_bra,
                "actual_bra": actual_bra,
                "building_count": len(buildings),
                "units_estimate": units_estimate,
                "programs_included": programs,
                "parking_served_by": [max(1, math.ceil(p / 2.0))],
                "standalone_outdoor_m2": standalone_outdoor_m2,
                "standalone_habitable": standalone_habitable,
                "standalone_issues": standalone_issues,
                "volume_ids": [getattr(b, "display_name", "") or getattr(b, "bygg_id", "") for b in buildings],
                "estimated_duration_months": 18 if actual_bra < 3500 else 24 if actual_bra < 7000 else 30,
                "depends_on_phases": [p - 1] if p > 1 else [],
            })
        if not phases:
            phases.append({
                "phase": 1,
                "phase_number": 1,
                "label": "Trinn 1",
                "field_ids": [],
                "bta_m2": 0.0,
                "bra_m2": 0.0,
                "actual_bra": 0.0,
                "building_count": 0,
                "units_estimate": 0,
                "programs_included": ["bolig"],
                "parking_served_by": [1],
                "standalone_outdoor_m2": 0.0,
                "standalone_habitable": False,
                "standalone_issues": ["Ingen bygninger i planen."],
                "volume_ids": [],
                "estimated_duration_months": 18,
                "depends_on_phases": [],
            })
        return phases

    def _build_parking_phases(self, plan: Masterplan) -> List[SyntheticParkingPhase]:
        phase_count = max(1, math.ceil(len(self.building_phases) / 2.0))
        total_units = max(0, int(getattr(plan, "antall_boliger", 0) or 0))
        total_spaces = int(round(total_units * 0.70))
        if total_spaces <= 0 and total_units > 0:
            total_spaces = total_units
        parking_phases: List[SyntheticParkingPhase] = []
        assigned = 0
        for idx in range(phase_count):
            phase_number = idx + 1
            serves = [bp["phase_number"] for bp in self.building_phases[idx * 2:(idx + 1) * 2]]
            remaining = max(0, total_spaces - assigned)
            share = remaining if phase_number == phase_count else int(round(total_spaces / phase_count))
            assigned += share
            parking_phases.append(
                SyntheticParkingPhase(
                    phase_number=phase_number,
                    num_spaces=share,
                    serves_building_phases=serves,
                    ramps=[],
                    extends_parking_phases=[phase_number - 1] if phase_number > 1 else [],
                    notes="Deterministisk faseestimat. Kapasitet bør detaljprosjekteres i neste trinn.",
                )
            )
        return parking_phases

    def _build_metrics(self, plan: Masterplan) -> SyntheticMetrics:
        phase_bra = [float(ph.get("actual_bra", 0.0) or 0.0) for ph in self.building_phases]
        avg_phase = sum(phase_bra) / max(len(phase_bra), 1)
        standalone_scores = [100.0 if ph.get("standalone_habitable", False) else 65.0 for ph in self.building_phases]
        rules = getattr(plan, "plan_regler", None) or PlanRegler()
        mua_report = getattr(plan, "mua_report", None)
        mua_required = float(getattr(mua_report, "krav_total", 0.0) or 0.0) if mua_report is not None else 0.0
        if mua_required <= 0.0:
            mua_required = float(getattr(rules, "mua_per_bolig_m2", 40.0) or 40.0) * float(getattr(plan, "antall_boliger", 0) or 0)
        mua_compliant = bool(getattr(mua_report, "compliant", False)) if mua_report is not None else False
        return SyntheticMetrics(
            phase_count_buildings=len(self.building_phases),
            avg_phase_bra=avg_phase,
            min_phase_bra=min(phase_bra) if phase_bra else 0.0,
            max_phase_bra=max(phase_bra) if phase_bra else 0.0,
            phase_count_parking=len(self.parking_phases),
            standalone_habitability_score=sum(standalone_scores) / max(len(standalone_scores), 1),
            mua_required_m2=mua_required,
            mua_compliant=mua_compliant,
        )

    def _build_warnings(self, plan: Masterplan) -> List[str]:
        warnings: List[str] = []
        try:
            buildable = getattr(plan, "buildable_polygon", None)
            if buildable is not None and not getattr(buildable, "is_empty", True):
                warnings.extend(validate_masterplan_geometry(plan, buildable))
        except Exception:
            pass
        for item in list(getattr(plan, "report_risks", []) or []):
            text = str(item).strip()
            if text and text not in warnings:
                warnings.append(text)
        return warnings

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        best_plan = self.__dict__.get("best_plan")
        if best_plan is None:
            raise AttributeError(name)
        return getattr(best_plan, name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concept_family": getattr(getattr(self.best_plan, "concept_family", None), "value", None),
            "display_title": getattr(self.best_plan, "display_title", ""),
            "display_subtitle": getattr(self.best_plan, "display_subtitle", ""),
            "total_bra_m2": float(getattr(self.best_plan, "total_bra_m2", 0.0) or 0.0),
            "total_bya_m2": float(getattr(self.best_plan, "total_bya_m2", 0.0) or 0.0),
            "antall_boliger": int(getattr(self.best_plan, "antall_boliger", 0) or 0),
            "score": float(getattr(self.best_plan, "score", 0.0) or 0.0),
            "phase_count": len(getattr(self, "building_phases", []) or []),
            "field_count": len(getattr(self.best_plan, "delfelt", []) or []),
            "architecture_score": float(getattr(getattr(self.best_plan, "architecture_report", None), "total_score", 0.0) or 0.0),
        }


def _coord_groups(poly: Any) -> List[List[List[float]]]:
    if poly is None or getattr(poly, "is_empty", True):
        return []
    if isinstance(poly, Polygon):
        return [[[float(x), float(y)] for x, y in list(poly.exterior.coords)]]
    if isinstance(poly, MultiPolygon):
        return [[[float(x), float(y)] for x, y in list(part.exterior.coords)] for part in poly.geoms if not part.is_empty]
    if isinstance(poly, GeometryCollection):
        groups: List[List[List[float]]] = []
        for g in poly.geoms:
            groups.extend(_coord_groups(g))
        return groups
    return []


def _line_coords(line: Any) -> List[List[float]]:
    if line is None or getattr(line, "is_empty", True):
        return []
    try:
        return [[float(x), float(y)] for x, y in list(line.coords)]
    except Exception:
        return []


def _pick_plan_regler(byggesone: str) -> PlanRegler:
    return TRONDHEIM_KPA_2022_SONE_2 if str(byggesone) == "2" else GENERISK_TEK17_NORGE


def _adapt_plan_regler_for_context(regler: PlanRegler, buildable_poly: Polygon, target_bra_m2: float) -> PlanRegler:
    area = float(getattr(buildable_poly, 'area', 0.0) or 0.0)
    density = float(target_bra_m2 or 0.0) / max(area, 1.0)
    compact_infill = area <= 3500.0 or (area <= 7000.0 and density >= 1.75)
    urban_infill = not compact_infill and area <= 9000.0 and density >= 1.30
    custom = dict(getattr(regler, 'custom_rules', {}) or {})
    max_floors = int(getattr(regler, 'max_floors', 5) or 5)
    avg_floors = max(2.5, (max_floors + 2.0) / 2.0)
    required_bya_pct = float(target_bra_m2 or 0.0) / max(avg_floors * max(area, 1.0), 1.0) * 100.0

    if compact_infill:
        custom.update({'site_mode': 'compact_infill', 'mua_priority': 'advisory', 'allow_full_site_fill': True})
        return replace(
            regler,
            max_bya_pct=max(float(getattr(regler, 'max_bya_pct', 0.0) or 0.0), min(90.0, max(65.0, required_bya_pct * 1.10))),
            mua_per_bolig_m2=0.0,
            mua_min_felles_pct=0.0,
            mua_min_bakke_pct=0.0,
            custom_rules=custom,
        )

    if urban_infill:
        custom.update({'site_mode': 'urban_infill', 'mua_priority': 'reduced'})
        current_mua = float(getattr(regler, 'mua_per_bolig_m2', 0.0) or 0.0)
        return replace(
            regler,
            max_bya_pct=max(float(getattr(regler, 'max_bya_pct', 0.0) or 0.0), min(78.0, max(42.0, required_bya_pct * 1.05))),
            mua_per_bolig_m2=min(current_mua, 15.0) if current_mua > 0.0 else 0.0,
            mua_min_felles_pct=min(float(getattr(regler, 'mua_min_felles_pct', 0.0) or 0.0), 0.20),
            mua_min_bakke_pct=min(float(getattr(regler, 'mua_min_bakke_pct', 0.0) or 0.0), 0.20),
            custom_rules=custom,
        )

    return regler


def build_phasing_ui(*, target_bra_m2: float, key_prefix: str = "masterplan"):
    import streamlit as st

    default_count = PhasingConfig().resolve_phase_count(float(target_bra_m2))
    c1, c2, c3 = st.columns(3)
    requested = c1.number_input(
        "Antall delfelt / trinn",
        min_value=1,
        max_value=8,
        value=int(default_count),
        step=1,
        key=f"{key_prefix}_requested_delfelt_count",
    )
    mode = c2.selectbox(
        "Struktur",
        options=["delfelt", "kompakt"],
        index=0,
        key=f"{key_prefix}_phasing_mode",
        help="Masterplanmotoren bruker delfelt som primær struktur.",
    )
    parking_mode = c3.selectbox(
        "Parkering",
        options=["auto", "redusert", "maks"],
        index=0,
        key=f"{key_prefix}_parking_mode",
    )
    cfg = PhasingConfig(requested_delfelt_count=int(requested), phasing_mode=str(mode), parking_mode=str(parking_mode))
    info = {"resolved_phase_count": cfg.resolve_phase_count(target_bra_m2), "requested": int(requested)}
    return cfg, info


def _neighbor_buildings_from_context(geodata_context: Optional[Dict[str, Any]]) -> List[dict]:
    out: List[dict] = []
    for n in (geodata_context or {}).get("neighbors", []) or []:
        coords = n.get("coords") or n.get("polygon_coords") or []
        poly = n.get("polygon")
        if not coords and poly is not None:
            coords = _coord_groups(poly)
        if not coords:
            continue
        out.append(
            {
                "coords": coords,
                "height_m": float(n.get("height_m", n.get("height", 9.0)) or 9.0),
                "distance_m": float(n.get("distance_m", 0.0) or 0.0),
            }
        )
    return out


def _poly_from_context(site: Any, geodata_context: Optional[Dict[str, Any]]) -> Polygon:
    gc = geodata_context or {}
    for key in ("buildable_polygon", "site_polygon"):
        poly = gc.get(key)
        if isinstance(poly, Polygon) and not poly.is_empty:
            return poly.buffer(0)
        if isinstance(poly, MultiPolygon) and not poly.is_empty:
            return poly.buffer(0)
    return box(0, 0, float(getattr(site, "site_width_m", 50.0)), float(getattr(site, "site_depth_m", 50.0))).buffer(0)


def _score_plan(plan: Masterplan, target_bra_m2: float) -> Dict[str, float]:
    arch = float(getattr(getattr(plan, "architecture_report", None), "total_score", 0.0) or 0.0)
    sol = float(getattr(getattr(plan, "sol_report", None), "total_sol_score", 0.0) or 0.0)
    total_bra = float(getattr(plan, "total_bra_m2", 0.0) or 0.0)
    fit = max(0.0, 100.0 - abs(total_bra - target_bra_m2) / max(target_bra_m2, 1.0) * 100.0)
    mua_report = getattr(plan, "mua_report", None)
    mua_mode = str(getattr(mua_report, "mode", "strict") or "strict")
    if mua_report is not None and getattr(mua_report, "krav_total", None):
        mua = min(100.0, 100.0 * float(getattr(mua_report, "total", 0.0) or 0.0) / max(float(getattr(mua_report, "krav_total", 1.0) or 1.0), 1.0))
    else:
        mua = 100.0 if getattr(mua_report, "compliant", False) else 72.0
    bya_limit_pct = float(getattr(getattr(plan, "plan_regler", None), "max_bya_pct", 35.0) or 35.0)
    bya_ratio = 100.0 * float(getattr(plan, "total_bya_m2", 0.0) or 0.0) / max(float(getattr(getattr(plan, "buildable_polygon", None), "area", 1.0) or 1.0), 1.0)
    bya_fit = max(0.0, 100.0 - abs(bya_ratio - bya_limit_pct * 0.88) * 2.2)

    if mua_mode == "advisory":
        weights = (0.56, 0.24, 0.00, 0.10, 0.10)
    elif mua_mode == "reduced":
        weights = (0.52, 0.22, 0.08, 0.10, 0.08)
    else:
        weights = (0.50, 0.20, 0.14, 0.10, 0.06)

    total = weights[0] * arch + weights[1] * fit + weights[2] * mua + weights[3] * sol + weights[4] * bya_fit
    if total_bra < target_bra_m2 * 0.75:
        total -= 8.0
    if arch >= 75.0:
        total += 3.0
    return {
        "architecture": arch,
        "fit": fit,
        "mua": mua,
        "mua_mode": mua_mode,
        "solar": sol,
        "bya_fit": bya_fit,
        "total": max(0.0, min(100.0, total)),
    }


def run_masterplan_from_site_inputs(
    *,
    site: Any,
    geodata_context: Optional[Dict[str, Any]],
    phasing_config: PhasingConfig,
    target_bra_m2: float,
    include_barnehage: bool = False,
    include_naering: bool = False,
    byggesone: str = "2",
):
    del include_naering
    try:
        buildable_poly = _poly_from_context(site, geodata_context)
        regler = _pick_plan_regler(byggesone)
        regler = _adapt_plan_regler_for_context(regler, buildable_poly, float(target_bra_m2))
        barnehage = BarnehageConfig(
            enabled=bool(include_barnehage),
            inne_m2=1279.0 if include_barnehage else 0.0,
            ute_m2=2448.0 if include_barnehage else 0.0,
        )
        plans = generate_concept_masterplans(
            buildable_poly,
            target_bra_m2=float(target_bra_m2),
            plan_regler=regler,
            requested_delfelt_count=phasing_config.requested_delfelt_count,
            avg_unit_bra_m2=float(getattr(site, "avg_unit_bra_m2", 55.0) or 55.0),
            barnehage_config=barnehage,
            latitude_deg=float(getattr(site, "latitude_deg", 63.42)),
            longitude_deg=float((geodata_context or {}).get("longitude_deg", 10.43)),
            neighbor_buildings=_neighbor_buildings_from_context(geodata_context),
            parkering_areal=0.0,
            vei_areal=0.0,
            site_area_m2=float(getattr(site, "site_area_m2", buildable_poly.area)),
        )
        options: List[V8OptionResult] = []
        for idx, plan in enumerate(plans, start=1):
            breakdown = _score_plan(plan, float(target_bra_m2))
            plan.score = float(breakdown["total"])
            options.append(
                V8OptionResult(
                    option_id=f"mp_{idx}",
                    title=getattr(plan, "display_title", "") or getattr(getattr(plan, "concept_family", None), "value", f"Konsept {idx}"),
                    score=float(breakdown["total"]),
                    masterplan=plan,
                    score_breakdown=breakdown,
                )
            )
        options.sort(key=lambda item: item.score, reverse=True)
        if not options:
            return None, "Ingen konsepter ble generert"
        best = options[0].masterplan
        if best is None:
            return None, "Beste konsept mangler masterplan"
        best.buildable_polygon = buildable_poly
        bundle = LegacyMasterplanBundle(best, options)
        return bundle, None
    except Exception as exc:
        return None, str(exc)


def _architecture_layers_for_plan(plan: Masterplan, site_poly: Any) -> Dict[str, Any]:
    try:
        from .geometry import _compose_field_skeleton, _field_core_polygon, _rotate
    except Exception:
        return {
            "field_polygons": [],
            "public_realm_polygons": [],
            "courtyard_polygons": [],
            "view_corridor_polygons": [],
            "macro_axes": [],
            "symmetry_axes": [],
        }

    field_polygons: List[dict] = []
    public_realm: List[dict] = []
    courtyards: List[dict] = []
    corridors: List[dict] = []
    macro_axes: List[dict] = []
    symmetry_axes: List[dict] = []
    building_counts_by_field: Dict[str, int] = {}
    for b in getattr(plan, "bygg", []) or []:
        fid = str(getattr(b, "delfelt_id", "") or "")
        if not fid:
            continue
        building_counts_by_field[fid] = building_counts_by_field.get(fid, 0) + 1
    rules = getattr(plan, "plan_regler", None) or PlanRegler()
    for f in getattr(plan, "delfelt", []) or []:
        field_polygons.append(
            {
                "field_id": f.field_id,
                "label": f.phase_label or f.field_id,
                "phase": int(getattr(f, "phase", 1) or 1),
                "typology": getattr(getattr(f, "typology", None), "value", str(getattr(f, "typology", ""))),
                "target_building_count": int(getattr(f, "target_building_count", 0) or 0),
                "realized_building_count": int(building_counts_by_field.get(f.field_id, 0)),
                "target_bra_m2": float(getattr(f, "target_bra", 0.0) or 0.0),
                "coords": _coord_groups(f.polygon),
            }
        )
        try:
            core = _field_core_polygon(f.polygon, rules.brann_avstand_m)
            local = _rotate(core, -f.orientation_deg, origin=f.polygon.centroid) if abs(float(f.orientation_deg or 0.0)) > 1e-3 else core
            sk = _compose_field_skeleton(local, f)

            def world(g: Any) -> Any:
                return _rotate(g, f.orientation_deg, origin=f.polygon.centroid) if abs(float(f.orientation_deg or 0.0)) > 1e-3 else g

            for p in getattr(sk, "public_realm", []) or []:
                wg = world(p).intersection(site_poly).buffer(0)
                if not getattr(wg, "is_empty", True) and getattr(wg, "area", 0.0) > 10.0:
                    public_realm.append({"field_id": f.field_id, "coords": _coord_groups(wg)})
            c = getattr(sk, "courtyard_reserve", None)
            if c is not None and not getattr(c, "is_empty", True):
                wg = world(c).intersection(site_poly).buffer(0)
                if not getattr(wg, "is_empty", True) and getattr(wg, "area", 0.0) > 10.0:
                    courtyards.append({"field_id": f.field_id, "coords": _coord_groups(wg)})
            for vc in getattr(sk, "view_corridors", []) or []:
                wg = world(vc).intersection(site_poly).buffer(0)
                if not getattr(wg, "is_empty", True) and getattr(wg, "area", 0.0) > 10.0:
                    corridors.append({"field_id": f.field_id, "coords": _coord_groups(wg)})
            ma = getattr(sk, "macro_axis", None)
            if ma is not None:
                macro_axes.append({"field_id": f.field_id, "coords": _line_coords(world(ma))})
            sa = getattr(sk, "symmetry_axis", None)
            if sa is not None:
                symmetry_axes.append({"field_id": f.field_id, "coords": _line_coords(world(sa))})
        except Exception:
            continue
    return {
        "field_polygons": field_polygons,
        "public_realm_polygons": public_realm,
        "courtyard_polygons": courtyards,
        "view_corridor_polygons": corridors,
        "macro_axes": macro_axes,
        "symmetry_axes": symmetry_axes,
    }


def _legacy_geometry_for_plan(bundle: LegacyMasterplanBundle, plan: Masterplan, geodata_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    site_poly = getattr(bundle.best_plan, "buildable_polygon", None) or getattr(plan, "buildable_polygon", None)
    if site_poly is None:
        site_poly = plan.bygg[0].footprint.envelope.buffer(10) if plan.bygg else box(0, 0, 50, 50)
    massing_parts: List[Dict[str, Any]] = []
    for b in plan.bygg:
        typ = getattr(getattr(b, "typology", None), "value", str(getattr(b, "typology", "Lamell")))
        color = TYPOLOGY_DIAGRAM_COLORS.get(typ, [166, 187, 205, 220])
        width_m, depth_m = _footprint_dims(getattr(b, "footprint", None))
        massing_parts.append(
            {
                "name": getattr(b, "display_name", "") or getattr(b, "bygg_id", ""),
                "coords": _coord_groups(b.footprint),
                "height_m": float(getattr(b, "height_m", 0.0) or 0.0),
                "floors": int(getattr(b, "floors", 1) or 1),
                "phase": int(getattr(b, "phase", 1) or 1),
                "delfelt_id": getattr(b, "delfelt_id", ""),
                "typology": typ,
                "width_m": round(width_m, 1),
                "depth_m": round(depth_m, 1),
                "area_m2": round(float(getattr(getattr(b, "footprint", None), "area", 0.0) or 0.0), 1),
                "color": color,
            }
        )
    shadow = []
    if hasattr(plan, "sol_report") and getattr(plan.sol_report, "key_moments", None):
        rep = max((km.representative_shadow_m for km in plan.sol_report.key_moments), default=0.0)
        if rep > 0:
            shadow_poly = site_poly.buffer(rep * 0.15).intersection(site_poly.envelope.buffer(rep * 0.10))
            if not getattr(shadow_poly, "is_empty", True):
                shadow = _coord_groups(shadow_poly)
    layers = _architecture_layers_for_plan(plan, site_poly)
    out = {
        "site_polygon_coords": _coord_groups(site_poly),
        "buildable_polygon_coords": _coord_groups(site_poly),
        "footprint_polygon_coords": _coord_groups(plan.bygg[0].footprint) if plan.bygg else [],
        "winter_shadow_polygon_coords": shadow,
        "massing_parts": massing_parts,
        "masterplan_ref": bundle,
        "site_source": "Builtly masterplanmotor — arkitektdiagram",
        "neighbor_polygons": _neighbor_buildings_from_context(geodata_context),
    }
    out.update(layers)
    return out


def _option_to_legacy(
    option: V8OptionResult,
    bundle: LegacyMasterplanBundle,
    site: Any,
    geodata_context: Optional[Dict[str, Any]],
    OptionResultCls: Any,
    mix_specs: Optional[Sequence[Any]] = None,
):
    plan = option.masterplan or bundle.best_plan
    buildings = list(plan.bygg)
    floors = max((int(getattr(b, "floors", 0) or 0) for b in buildings), default=0)
    height = max((float(getattr(b, "height_m", 0.0) or 0.0) for b in buildings), default=0.0)
    footprint = sum((float(getattr(b, "footprint_m2", 0.0) or 0.0) for b in buildings), 0.0)
    mix_counts: Dict[str, int] = {}
    if mix_specs:
        labels = [getattr(m, "name", "Mix") for m in mix_specs]
        shares = [max(0.0, float(getattr(m, "share_pct", 0.0) or 0.0)) for m in mix_specs]
        total = sum(shares) or 1.0
        counts = [int(round(option.antall_boliger * s / total)) for s in shares]
        diff = int(option.antall_boliger - sum(counts))
        if counts:
            counts[0] += diff
        mix_counts = {label: max(0, cnt) for label, cnt in zip(labels, counts)}
    notes: List[str] = []
    if getattr(plan, "report_summary", ""):
        notes.append(str(plan.report_summary))
    if getattr(plan, "report_recommendation", ""):
        notes.append(str(plan.report_recommendation))
    notes.extend([str(item) for item in (getattr(plan, "report_risks", []) or []) if str(item).strip()])
    target_fit = max(0.0, min(100.0, option.score_breakdown.get("fit", 0.0)))
    return OptionResultCls(
        name=option.title,
        typology=", ".join(option.typology_mix) or getattr(getattr(plan, "concept_family", None), "value", "Konsept"),
        floors=int(floors),
        building_height_m=float(height),
        footprint_area_m2=float(footprint),
        gross_bta_m2=float(option.total_bra_m2 / 0.78) if option.total_bra_m2 else 0.0,
        saleable_area_m2=float(option.total_bra_m2),
        footprint_width_m=0.0,
        footprint_depth_m=0.0,
        buildable_area_m2=float(getattr(bundle.best_plan.buildable_polygon, "area", footprint)),
        open_space_ratio=max(0.0, 1.0 - (footprint / max(getattr(bundle.best_plan.buildable_polygon, "area", footprint + 1.0), 1.0))),
        target_fit_pct=float(target_fit),
        unit_count=int(option.antall_boliger),
        mix_counts=mix_counts,
        parking_spaces=sum(pp.num_spaces for pp in bundle.parking_phases),
        parking_pressure_pct=0.0,
        solar_score=float(option.sol_score),
        estimated_equinox_sun_hours=float(getattr(plan.sol_report, "project_soltimer_varjevndogn", 0.0) or 0.0),
        estimated_winter_sun_hours=float((getattr(plan.sol_report, "project_soltimer_varjevndogn", 0.0) or 0.0) * 0.55),
        sunlit_open_space_pct=float(getattr(plan.sol_report, "solbelyst_uteareal_pct", 0.0) or 0.0),
        winter_noon_shadow_m=float(getattr(plan.sol_report, "vinter_skygge_kl_12_m", 0.0) or 0.0),
        equinox_noon_shadow_m=float(plan.sol_report.key_moments[0].representative_shadow_m if getattr(plan.sol_report, "key_moments", None) else 0.0),
        summer_afternoon_shadow_m=float(getattr(plan.sol_report, "sommerskygge_kl_15_m", 0.0) or 0.0),
        efficiency_ratio=0.78,
        neighbor_count=len((geodata_context or {}).get("neighbors", []) or []),
        terrain_slope_pct=float(getattr(site, "terrain_slope_pct", 0.0) or 0.0),
        terrain_relief_m=float(getattr(site, "terrain_relief_m", 0.0) or 0.0),
        notes=notes[:8],
        score=float(option.score),
        geometry=_legacy_geometry_for_plan(bundle, plan, geodata_context=geodata_context),
        is_total_plan=(option.option_id == bundle.option_results[0].option_id),
    )


def masterplan_to_option_results(
    bundle: LegacyMasterplanBundle,
    site: Any,
    geodata_context: Optional[Dict[str, Any]],
    OptionResultCls: Any,
    mix_specs: Optional[Sequence[Any]] = None,
):
    return [
        _option_to_legacy(opt, bundle, site, geodata_context, OptionResultCls, mix_specs=mix_specs)
        for opt in bundle.option_results
    ]


def build_phase_legend_html(bundle: LegacyMasterplanBundle) -> str:
    items = []
    for idx, ph in enumerate(bundle.building_phases):
        color = PHASE_COLORS_HEX[idx % len(PHASE_COLORS_HEX)]
        items.append(
            f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;'>"
            f"<span style='width:12px;height:12px;border-radius:3px;background:{color};display:inline-block;flex-shrink:0;'></span>"
            f"<span style='color:#e6edf3;font:11px/1.4 -apple-system,sans-serif;'>{ph['label']} · {ph['bra_m2']:.0f} m² BRA</span>"
            f"</div>"
        )
    return (
        "<div style='position:absolute;top:12px;left:14px;background:rgba(6,17,26,0.88);"
        "border:1px solid rgba(56,189,248,0.25);border-radius:8px;padding:10px 12px;"
        "max-width:260px;z-index:10;'>"
        + "".join(items)
        + "</div>"
    )


def render_phasing_report_markdown(bundle: LegacyMasterplanBundle) -> str:
    lines = ["### Delfelt og gjennomføring"]
    for ph in bundle.building_phases:
        status = "klar som selvstendig bomiljø" if ph.get("standalone_habitable", False) else "krever oppfølging"
        lines.append(
            f"- Trinn {ph['phase_number']}: {ph['label']} — {ph['actual_bra']:.0f} m² BRA, "
            f"{ph['units_estimate']} boliger, {status}."
        )
    return "\n".join(lines)


def render_phasing_gantt_streamlit(bundle: LegacyMasterplanBundle) -> None:
    import pandas as pd
    import streamlit as st

    rows = []
    for ph in bundle.building_phases:
        rows.append(
            {
                "Trinn": ph["phase_number"],
                "Delfelt": ph["label"],
                "BRA m²": round(float(ph.get("actual_bra", 0.0) or 0.0), 1),
                "Boliger": int(ph.get("units_estimate", 0) or 0),
                "Uterom m²": round(float(ph.get("standalone_outdoor_m2", 0.0) or 0.0), 1),
                "Status": "OK" if ph.get("standalone_habitable", False) else "Merknad",
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True)
