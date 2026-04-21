from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shapely.geometry import Polygon, box

from .masterplan_integration import OptionResult as V8OptionResult, run_concept_options
from .masterplan_types import BarnehageConfig, Masterplan, PlanRegler
from .plan_regler_presets import GENERISK_TEK17_NORGE, TRONDHEIM_KPA_2022_SONE_2

PHASE_COLORS_HEX = ["#38bdf8", "#a78bfa", "#34d399", "#fbbf24", "#f87171", "#60a5fa"]

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

class LegacyMasterplanBundle:
    def __init__(self, best_plan: Masterplan, option_results: Sequence[V8OptionResult]):
        self.best_plan = best_plan
        self.option_results = list(option_results)
        self.building_phases = self._build_phases(best_plan)

    def _build_phases(self, plan: Masterplan) -> List[Dict[str, Any]]:
        phases: List[Dict[str, Any]] = []
        phase_ids = sorted({f.phase for f in plan.delfelt})
        for p in phase_ids:
            field_ids = [f.field_id for f in plan.delfelt if f.phase == p]
            buildings = [b for b in plan.bygg if b.phase == p or b.delfelt_id in field_ids]
            phases.append({
                "phase": p,
                "label": ", ".join(field_ids) or f"Trinn {p}",
                "field_ids": field_ids,
                "bta_m2": sum(b.footprint_m2 * b.floors for b in buildings),
                "bra_m2": sum(b.bra_m2 for b in buildings),
                "building_count": len(buildings),
            })
        return phases

    def __getattr__(self, name: str) -> Any:
        # Viktig: skjerm dunder-attributter. copy.deepcopy og dataclasses.asdict
        # spør etter __setstate__, __reduce__, __getstate__ m.fl. Hvis __getattr__
        # delegerer disse videre til self.best_plan, kan Python ende i uendelig
        # rekursjon fordi self.best_plan også er et Masterplan-objekt som kan
        # bli deepkopiert. Skjermingen gjør at copy-protokollen får korrekt
        # AttributeError og bruker sin default-path istedenfor.
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(name)
        return getattr(self.best_plan, name)

def _coord_groups(poly: Polygon) -> List[List[List[float]]]:
    if poly is None or poly.is_empty:
        return []
    ext = [[float(x), float(y)] for x, y in list(poly.exterior.coords)]
    return [ext]

def _pick_plan_regler(byggesone: str) -> PlanRegler:
    if str(byggesone) == "2":
        return TRONDHEIM_KPA_2022_SONE_2
    return GENERISK_TEK17_NORGE

def build_phasing_ui(*, target_bra_m2: float, key_prefix: str = "masterplan"):
    import streamlit as st
    default_count = PhasingConfig().resolve_phase_count(float(target_bra_m2))
    c1, c2, c3 = st.columns(3)
    requested = c1.number_input(
        "Antall delfelt / trinn", min_value=1, max_value=8, value=int(default_count), step=1, key=f"{key_prefix}_requested_delfelt_count"
    )
    mode = c2.selectbox(
        "Struktur", options=["delfelt", "kompakt"], index=0, key=f"{key_prefix}_phasing_mode", help="V8 bruker delfelt som primær struktur."
    )
    parking_mode = c3.selectbox(
        "Parkering", options=["auto", "redusert", "maks"], index=0, key=f"{key_prefix}_parking_mode"
    )
    cfg = PhasingConfig(requested_delfelt_count=int(requested), phasing_mode=str(mode), parking_mode=str(parking_mode))
    info = {"resolved_phase_count": cfg.resolve_phase_count(target_bra_m2), "requested": int(requested)}
    return cfg, info

def _neighbor_buildings_from_context(geodata_context: Optional[Dict[str, Any]]) -> List[dict]:
    out = []
    for n in (geodata_context or {}).get("neighbors", []) or []:
        coords = n.get("coords") or n.get("polygon_coords") or []
        h = float(n.get("height_m", n.get("height", 9.0)) or 9.0)
        out.append({"coords": coords, "height_m": h})
    return out

def _poly_from_context(site: Any, geodata_context: Optional[Dict[str, Any]]) -> Polygon:
    gc = geodata_context or {}
    for key in ("buildable_polygon", "site_polygon"):
        poly = gc.get(key)
        if isinstance(poly, Polygon) and not poly.is_empty:
            return poly.buffer(0)
    return box(0, 0, float(getattr(site, "site_width_m", 50.0)), float(getattr(site, "site_depth_m", 50.0))).buffer(0)

def run_masterplan_from_site_inputs(*, site: Any, geodata_context: Optional[Dict[str, Any]], phasing_config: PhasingConfig, target_bra_m2: float, include_barnehage: bool = False, include_naering: bool = False, byggesone: str = "2"):
    del include_naering
    try:
        buildable_poly = _poly_from_context(site, geodata_context)
        regler = _pick_plan_regler(byggesone)
        barnehage = BarnehageConfig(enabled=bool(include_barnehage), inne_m2=1279.0 if include_barnehage else 0.0, ute_m2=2448.0 if include_barnehage else 0.0)
        options = run_concept_options(
            buildable_poly,
            target_bra_m2=float(target_bra_m2),
            plan_regler=regler,
            requested_delfelt_count=phasing_config.requested_delfelt_count,
            avg_unit_bra_m2=float(getattr(site, "efficiency_ratio", 0.78) and getattr(site, "desired_bta_m2", 0.0) and 55.0 or 55.0),
            barnehage_config=barnehage,
            latitude_deg=float(getattr(site, "latitude_deg", 63.42)),
            longitude_deg=float((geodata_context or {}).get("longitude_deg", 10.43)),
            neighbor_buildings=_neighbor_buildings_from_context(geodata_context),
            parkering_areal=0.0,
            vei_areal=0.0,
            site_area_m2=float(getattr(site, "site_area_m2", buildable_poly.area)),
        )
        if not options:
            return None, "Ingen konsepter ble generert"
        best = options[0].masterplan
        if best is None:
            return None, "Beste konsept mangler masterplan"
        # attach buildable polygon for validation/report helpers
        best.buildable_polygon = buildable_poly
        bundle = LegacyMasterplanBundle(best, options)
        return bundle, None
    except Exception as exc:
        return None, str(exc)

def _legacy_geometry_for_plan(bundle: LegacyMasterplanBundle, plan: Masterplan) -> Dict[str, Any]:
    site_poly = getattr(bundle.best_plan, "buildable_polygon", None) or getattr(plan, "buildable_polygon", None)
    if site_poly is None:
        site_poly = plan.bygg[0].footprint.envelope.buffer(10) if plan.bygg else box(0,0,50,50)
    massing_parts = []
    for idx, b in enumerate(plan.bygg):
        color = PHASE_COLORS_HEX[(int(b.phase) - 1) % len(PHASE_COLORS_HEX)]
        rgb = tuple(int(color[i:i+2], 16) for i in (1,3,5))
        massing_parts.append({
            "name": b.display_name or b.bygg_id,
            "coords": _coord_groups(b.footprint),
            "height_m": float(b.height_m),
            "floors": int(b.floors),
            "color": [rgb[0], rgb[1], rgb[2], 220],
        })
    shadow = []
    if hasattr(plan, "sol_report") and plan.sol_report.key_moments:
        rep = max((km.representative_shadow_m for km in plan.sol_report.key_moments), default=0.0)
        if rep > 0:
            shadow_poly = site_poly.buffer(rep * 0.15).intersection(site_poly.envelope.buffer(rep * 0.10))
            if not shadow_poly.is_empty and hasattr(shadow_poly, "exterior"):
                shadow = _coord_groups(shadow_poly)
    return {
        "site_polygon_coords": _coord_groups(site_poly),
        "buildable_polygon_coords": _coord_groups(site_poly),
        "footprint_polygon_coords": _coord_groups(plan.bygg[0].footprint) if plan.bygg else [],
        "winter_shadow_polygon_coords": shadow,
        "massing_parts": massing_parts,
        "masterplan_ref": bundle,
        "site_source": "Builtly v8 konseptmotor",
        "neighbor_polygons": [],
    }

def _option_to_legacy(option: V8OptionResult, bundle: LegacyMasterplanBundle, site: Any, OptionResultCls: Any, mix_specs: Optional[Sequence[Any]] = None):
    plan = option.masterplan or bundle.best_plan
    buildings = list(plan.bygg)
    floors = max((b.floors for b in buildings), default=0)
    height = max((b.height_m for b in buildings), default=0.0)
    footprint = sum((b.footprint_m2 for b in buildings), 0.0)
    mix_counts = {}
    labels = []
    if mix_specs:
        labels = [getattr(m, "name", "Mix") for m in mix_specs]
        shares = [max(0.0, float(getattr(m, "share_pct", 0.0))) for m in mix_specs]
        total = sum(shares) or 1.0
        counts = [int(round(option.antall_boliger * s / total)) for s in shares]
        diff = int(option.antall_boliger - sum(counts))
        if counts:
            counts[0] += diff
        mix_counts = {label: max(0, cnt) for label, cnt in zip(labels, counts)}
    notes = []
    if plan.report_summary:
        notes.append(plan.report_summary)
    if plan.report_recommendation:
        notes.append(plan.report_recommendation)
    notes.extend(plan.report_risks or [])
    return OptionResultCls(
        name=option.title,
        typology=", ".join(option.typology_mix) or plan.concept_family.value,
        floors=int(floors),
        building_height_m=float(height),
        footprint_area_m2=float(footprint),
        gross_bta_m2=float(option.total_bra_m2 / max(0.78, 0.78)),
        saleable_area_m2=float(option.total_bra_m2),
        footprint_width_m=0.0,
        footprint_depth_m=0.0,
        buildable_area_m2=float(getattr(bundle.best_plan.buildable_polygon, "area", footprint)),
        open_space_ratio=max(0.0, 1.0 - (footprint / max(getattr(bundle.best_plan.buildable_polygon, "area", footprint + 1.0), 1.0))),
        target_fit_pct=float(max(0.0, min(100.0, bundle.best_plan.total_bra_m2 and (option.total_bra_m2 / max(option.total_bra_m2,1.0))*100.0))),
        unit_count=int(option.antall_boliger),
        mix_counts=mix_counts,
        parking_spaces=0,
        parking_pressure_pct=0.0,
        solar_score=float(option.sol_score),
        estimated_equinox_sun_hours=float(plan.sol_report.project_soltimer_varjevndogn),
        estimated_winter_sun_hours=float(plan.sol_report.project_soltimer_varjevndogn * 0.55),
        sunlit_open_space_pct=float(plan.sol_report.solbelyst_uteareal_pct),
        winter_noon_shadow_m=float(plan.sol_report.vinter_skygge_kl_12_m),
        equinox_noon_shadow_m=float(plan.sol_report.key_moments[0].representative_shadow_m if plan.sol_report.key_moments else 0.0),
        summer_afternoon_shadow_m=float(plan.sol_report.sommerskygge_kl_15_m),
        efficiency_ratio=0.78,
        neighbor_count=0,
        terrain_slope_pct=float(getattr(site, "terrain_slope_pct", 0.0)),
        terrain_relief_m=float(getattr(site, "terrain_relief_m", 0.0)),
        notes=notes[:8],
        score=float(option.score),
        geometry=_legacy_geometry_for_plan(bundle, plan),
        is_total_plan=(option.option_id == bundle.option_results[0].option_id),
    )

def masterplan_to_option_results(bundle: LegacyMasterplanBundle, site: Any, geodata_context: Optional[Dict[str, Any]], OptionResultCls: Any, mix_specs: Optional[Sequence[Any]] = None):
    del geodata_context
    return [_option_to_legacy(opt, bundle, site, OptionResultCls, mix_specs=mix_specs) for opt in bundle.option_results]

def build_phase_legend_html(bundle: LegacyMasterplanBundle) -> str:
    items = []
    for idx, ph in enumerate(bundle.building_phases):
        color = PHASE_COLORS_HEX[idx % len(PHASE_COLORS_HEX)]
        items.append(f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;'><span style='width:12px;height:12px;border-radius:3px;background:{color};display:inline-block;'></span><span>{ph['label']} · {ph['bra_m2']:.0f} m² BRA</span></div>")
    return "<div>" + "".join(items) + "</div>"

def render_phasing_report_markdown(bundle: LegacyMasterplanBundle) -> str:
    lines = ["### Delfelt og gjennomføring"]
    for ph in bundle.building_phases:
        lines.append(f"- Trinn {ph['phase']}: {ph['label']} — {ph['bra_m2']:.0f} m² BRA, {ph['building_count']} bygg")
    return "\n".join(lines)

def render_phasing_gantt_streamlit(bundle: LegacyMasterplanBundle) -> None:
    import pandas as pd
    import streamlit as st
    rows = [{"Trinn": ph["phase"], "Delfelt": ph["label"], "BRA m²": round(ph["bra_m2"],1), "Bygg": ph["building_count"]} for ph in bundle.building_phases]
    st.dataframe(pd.DataFrame(rows), use_container_width=True)
