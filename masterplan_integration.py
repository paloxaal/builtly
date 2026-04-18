"""
Masterplan-integrasjon for Mulighetsstudie.py.

Denne modulen kobler den nye masterplan-motoren til dagens Streamlit-UI og
OptionResult-pipeline uten å brekke bakoverkompatibilitet.

Hovedpunkter:
  1. build_phasing_ui() — Streamlit-widgets for fase-valg (auto/single/manuelt)
     + parkeringsstrategi-valg. Skal kalles inne i samme expander som
     max_bya/max_floors i Seksjon 2.

  2. run_masterplan_from_site_inputs() — kjører plan_masterplan() med input
     konvertert fra SiteInputs + geodata_context.

  3. masterplan_to_option_results() — splitter masterplan i OptionResult-objekter
     per byggetrinn, slik at dagens 3D-scene, rapport og sammenligningsfunksjon
     kan plukke opp byggetrinnene som "alternativer" (en per trinn).

  4. render_phasing_report_section() — rapportsnutt (Markdown) med Gantt-lignende
     oversikt over byggetrinn. Kan legges inn før eller etter dagens
     "ALTERNATIVER"-seksjon i PDF-rapporten.

INTEGRASJON I Mulighetsstudie.py:
   Se PATCHES.md for hvor hver del skal inn.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

try:
    import streamlit as st
    HAS_STREAMLIT = True
except Exception:
    st = None  # type: ignore
    HAS_STREAMLIT = False

from masterplan_types import (
    BuildingPhase,
    Masterplan,
    ParkingPhase,
    PhasingConfig,
    Volume,
)
import masterplan_engine

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# 1. UI: fase-valg i Streamlit
# ─────────────────────────────────────────────────────────────────────

def build_phasing_ui(target_bra_m2: float,
                     key_prefix: str = "phasing") -> Tuple[PhasingConfig, Dict[str, Any]]:
    """Streamlit-widget for fase- og parkeringsvalg.

    Returnerer (PhasingConfig, info_dict) hvor info_dict inneholder
    visningsdata (anbefalt antall, advarsler osv).

    Kalles inne i en expander eller columns. Forventer at st er tilgjengelig.
    """
    if not HAS_STREAMLIT:
        return PhasingConfig(), {}

    # Anbefal antall faser basert på målet
    default_cfg = PhasingConfig()
    rec, mn, mx = default_cfg.recommended_phase_count(target_bra_m2)
    avg_per_phase = target_bra_m2 / max(rec, 1)

    st.markdown("### Byggetrinn-strategi")
    st.caption(
        "Hovedregel: et byggetrinn ligger typisk på 3 500–4 500 m² BRA. "
        "Motoren grupperer volumer slik at hvert trinn kan stå som selvstendig "
        "bomiljø — egne oppganger, dedikert uterom, trygg adkomst."
    )

    col1, col2 = st.columns([1.4, 1])

    with col1:
        phasing_mode_label = st.radio(
            "Fasering",
            options=["Auto (anbefalt)", "Ett byggetrinn", "Manuelt antall"],
            index=0,
            key=f"{key_prefix}_mode",
            horizontal=True,
        )

    mode_map = {
        "Auto (anbefalt)": "auto",
        "Ett byggetrinn": "single",
        "Manuelt antall": "manual",
    }
    phasing_mode = mode_map[phasing_mode_label]

    manual_count: Optional[int] = None
    with col2:
        if phasing_mode == "manual":
            manual_count = int(st.number_input(
                "Antall byggetrinn",
                min_value=1,
                max_value=30,
                value=rec,
                step=1,
                key=f"{key_prefix}_manual_count",
            ))
        else:
            st.markdown(
                f"<div style='padding:12px 10px;background:#0f172a;border-radius:8px;'>"
                f"<div style='color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.05em;'>Anbefalt</div>"
                f"<div style='color:#e2e8f0;font-size:22px;font-weight:600;'>{rec} trinn</div>"
                f"<div style='color:#64748b;font-size:12px;'>≈ {avg_per_phase:,.0f} m² per trinn</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # Advarsler for manuelt valg
    warnings: List[str] = []
    if phasing_mode == "manual" and manual_count:
        warnings = default_cfg.validate_manual_choice(target_bra_m2, manual_count)
        for w in warnings:
            st.warning(w)

    # Parkeringsvalg
    st.markdown("**Parkeringsstrategi**")
    parking_label = st.radio(
        "Parkeringskjeller",
        options=[
            "Auto — motoren foreslår P-faser",
            "Én sammenhengende p-kjeller",
            "Manuelt antall p-faser",
        ],
        index=0,
        key=f"{key_prefix}_parking_mode",
        horizontal=True,
        label_visibility="collapsed",
    )
    parking_map = {
        "Auto — motoren foreslår P-faser": "auto",
        "Én sammenhengende p-kjeller": "single_garage",
        "Manuelt antall p-faser": "manual",
    }
    parking_mode = parking_map[parking_label]
    manual_parking: Optional[int] = None
    if parking_mode == "manual":
        manual_parking = int(st.number_input(
            "Antall p-faser",
            min_value=1,
            max_value=10,
            value=2,
            step=1,
            key=f"{key_prefix}_manual_parking",
        ))

    cfg = PhasingConfig(
        phasing_mode=phasing_mode,  # type: ignore
        manual_phase_count=manual_count,
        parking_mode=parking_mode,  # type: ignore
        manual_parking_phase_count=manual_parking,
    )

    info = {
        "recommended": rec,
        "min_reasonable": mn,
        "max_reasonable": mx,
        "avg_per_phase": avg_per_phase,
        "warnings": warnings,
        "resolved_phase_count": cfg.resolve_phase_count(target_bra_m2),
    }
    return cfg, info


# ─────────────────────────────────────────────────────────────────────
# 2. Kjør masterplan fra SiteInputs + geodata_context
# ─────────────────────────────────────────────────────────────────────

def run_masterplan_from_site_inputs(
    site: Any,  # SiteInputs fra Mulighetsstudie.py
    geodata_context: Dict[str, Any],
    phasing_config: PhasingConfig,
    target_bra_m2: float,
    include_barnehage: bool = False,
    include_naering: bool = False,
    byggesone: str = "2",
) -> Tuple[Optional[Masterplan], Optional[str]]:
    """Bro mellom Mulighetsstudie-input og plan_masterplan().

    Leser ut relevante felt fra SiteInputs + geodata_context og kaller motoren.
    Returnerer (Masterplan, None) ved suksess, eller (None, error_message) ved feil.
    """
    site_polygon = geodata_context.get("site_polygon")
    buildable_polygon = geodata_context.get("buildable_polygon")
    neighbors = geodata_context.get("neighbors", [])
    terrain = geodata_context.get("terrain")
    site_intelligence = geodata_context.get("site_intelligence")

    # Sanity-checks: fang vanlige inputs-feil før motoren kjøres
    if buildable_polygon is None or buildable_polygon.is_empty:
        msg = "buildable_polygon mangler eller er tom. Last opp tomt eller juster setbacks."
        logger.warning(f"run_masterplan: {msg}")
        return None, msg

    if target_bra_m2 <= 0:
        msg = (
            f"Mål-BRA er {target_bra_m2:.0f} m² — må være større enn 0. "
            f"Aktiver %-BRA-override i seksjon 2A, eller sett maks BRA."
        )
        return None, msg

    buildable_area = float(buildable_polygon.area)
    if buildable_area < 100:
        msg = f"Byggbart areal er kun {buildable_area:.0f} m² — for lite til en masterplan."
        return None, msg

    # Avled max_floors fra site.max_height_m og floor_to_floor hvis fornuftig
    max_floors = int(getattr(site, "max_floors", 5))
    max_height_m = float(getattr(site, "max_height_m", 16.0))
    max_bya_pct = float(getattr(site, "max_bya_pct", 35.0))
    floor_to_floor = float(getattr(site, "floor_to_floor_m", 3.2))

    # Sjekk at vi har nok BYA-kapasitet til å realisere target_bra
    max_footprint = buildable_area * (max_bya_pct / 100.0)
    max_theoretical_bra = max_footprint * max_floors * 0.85
    if target_bra_m2 > max_theoretical_bra * 1.1:
        msg = (
            f"Mål-BRA {target_bra_m2:.0f} m² er urealistisk høyt gitt "
            f"byggbart areal ({buildable_area:.0f} m²), maks BYA {max_bya_pct:.0f}%, "
            f"og maks {max_floors} etasjer. Teoretisk maks BRA er ca {max_theoretical_bra:.0f} m². "
            f"Øk maks etasjer eller BYA, eller senk mål-BRA."
        )
        return None, msg

    site_inputs_dict = {
        "latitude_deg": float(getattr(site, "latitude_deg", 63.4)),
        "site_area_m2": float(getattr(site, "site_area_m2", 0.0)),
        "avg_unit_bra": 70.0,
        "terrain": terrain,
        "site_intelligence": site_intelligence,
    }

    try:
        masterplan = masterplan_engine.plan_masterplan(
            site_polygon=site_polygon,
            buildable_polygon=buildable_polygon,
            neighbors=neighbors,
            terrain=terrain,
            site_intelligence=site_intelligence,
            site_inputs=site_inputs_dict,
            target_bra_m2=target_bra_m2,
            max_floors=max_floors,
            max_height_m=max_height_m,
            max_bya_pct=max_bya_pct,
            floor_to_floor_m=floor_to_floor,
            phasing_config=phasing_config,
            include_barnehage=include_barnehage,
            include_naering=include_naering,
            byggesone=byggesone,
        )

        # Post-sjekk: fikk vi faktisk volumer?
        if not masterplan.volumes:
            return None, (
                "Motoren kjørte, men plasserte 0 volumer. "
                "Sannsynlig årsak: byggbart polygon er for lite eller smalt for "
                "den valgte typologien. Sjekk polygonbuffer og byggegrenser."
            )
        if not masterplan.building_phases:
            return None, (
                "Motoren plasserte volumer, men klarte ikke å danne byggetrinn. "
                "Prøv å endre fase-valg i seksjon 2C."
            )

        return masterplan, None

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Masterplan-kjøring feilet: {exc}\n{tb}")
        return None, f"{type(exc).__name__}: {str(exc)[:400]}"


# ─────────────────────────────────────────────────────────────────────
# 3. Konverter masterplan → OptionResult per byggetrinn
# ─────────────────────────────────────────────────────────────────────

def _polygon_to_coords_groups(poly) -> List[List[List[float]]]:
    """Konverter shapely Polygon/MultiPolygon til coords_groups-format.

    Format: list of rings, hver ring er list av [x, y]-par.
    Matcher det Mulighetsstudie.py forventer (se geometry_to_coord_groups).
    """
    if poly is None or poly.is_empty:
        return []
    from shapely.geometry import Polygon, MultiPolygon
    groups = []
    try:
        if isinstance(poly, Polygon):
            exterior = [[round(p[0], 2), round(p[1], 2)] for p in poly.exterior.coords]
            groups.append(exterior)
        elif isinstance(poly, MultiPolygon):
            for subp in poly.geoms:
                if subp.is_empty:
                    continue
                exterior = [[round(p[0], 2), round(p[1], 2)] for p in subp.exterior.coords]
                groups.append(exterior)
    except Exception:
        pass
    return groups


def masterplan_to_option_results(
    masterplan: Masterplan,
    site: Any,
    geodata_context: Dict[str, Any],
    OptionResult_cls: Any,  # dagens OptionResult dataclass
) -> List[Any]:
    """Splitter masterplan i OptionResult-objekter, én per byggetrinn.

    Dette gjør at dagens 3D-scene, rapport og sammenligningsfunksjon kan
    plukke opp byggetrinnene uten å endre signatur — hvert "alternativ"
    blir et byggetrinn i stedet for en typologi-variant.

    I tillegg lager vi ett "Totalt" alternativ som viser hele masterplanen.
    """
    results: List[Any] = []

    from shapely.ops import unary_union

    mix_specs_dummy = {
        "1-rom": 0, "2-rom": 0, "3-rom": 0, "4-rom+": 0,
    }

    # Hjelp for BTA/BRA
    efficiency = float(getattr(site, "efficiency_ratio", 0.85))

    # --- KRITISK: forhåndsbyg coord-groups for site, buildable, neighbors ---
    # 3D-scenen (build_geodata_scene_payload) leter etter disse feltene i
    # option.geometry og projiserer dem til lon/lat. Hvis de mangler,
    # faller scenen tilbake til Oslo-default (10.75, 59.91).
    site_polygon_coords: List[List[List[float]]] = []
    buildable_polygon_coords: List[List[List[float]]] = []
    neighbor_polygons_coords: List[Dict[str, Any]] = []

    if masterplan.site_polygon is not None:
        site_polygon_coords = _polygon_to_coords_groups(masterplan.site_polygon)
    if masterplan.buildable_polygon is not None:
        buildable_polygon_coords = _polygon_to_coords_groups(masterplan.buildable_polygon)

    # Naboene fra geodata_context
    for nb in geodata_context.get("neighbors", []):
        nb_poly = nb.get("polygon")
        if nb_poly is None:
            continue
        nb_coords = _polygon_to_coords_groups(nb_poly)
        if nb_coords:
            neighbor_polygons_coords.append({
                "coords": nb_coords,
                "height_m": float(nb.get("height_m", 9.0)),
                "distance_m": float(nb.get("distance_m", 0.0)),
            })

    # Bygg en kart-over from volume_id → coord_groups slik at vi ikke konverterer
    # samme polygon flere ganger
    volume_coord_cache: Dict[str, List[List[List[float]]]] = {}
    for v in masterplan.volumes:
        if v.polygon is not None:
            volume_coord_cache[v.volume_id] = _polygon_to_coords_groups(v.polygon)

    # Lag ett option per byggetrinn
    for phase in masterplan.building_phases:
        phase_volumes = [v for v in masterplan.volumes
                         if v.volume_id in phase.volume_ids and v.polygon is not None]
        if not phase_volumes:
            continue

        phase_footprint = unary_union([v.polygon for v in phase_volumes])
        phase_fp_area = float(phase_footprint.area) if phase_footprint else 0.0
        phase_bta = sum(v.footprint_m2 * v.floors for v in phase_volumes)
        phase_bra = sum(v.bra_m2 for v in phase_volumes)
        max_floors_in_phase = max(v.floors for v in phase_volumes)
        max_height = max(v.height_m for v in phase_volumes)
        phase_footprint_coords = _polygon_to_coords_groups(phase_footprint)

        # Bygg geometry-dict slik 3D-pipeline forventer
        # Både 'buildings' (nytt format) og 'massing_parts' (dagens format)
        from masterplan_integration import phase_color_rgba  # self-ref ok
        buildings_dicts = []
        massing_parts_dicts = []
        for v in phase_volumes:
            # Fase-farge for 3D
            color_rgba = phase_color_rgba(v.assigned_phase, alpha=220)
            buildings_dicts.append({
                "polygon": v.polygon,
                "name": v.name,
                "role": "main",
                "floors": v.floors,
                "height_m": v.height_m,
                "width_m": v.width_m,
                "depth_m": v.depth_m,
                "angle_deg": v.angle_deg,
                "area_m2": v.footprint_m2,
                "cx": v.cx,
                "cy": v.cy,
                "courtyard": v.has_courtyard,
                "ring_depth": v.ring_depth_m,
                "notes": v.notes,
                "pos_id": v.volume_id,
                "phase": v.assigned_phase,
                "typology": v.typology,
                "program": v.program,
                "ground_floor_program": v.ground_floor_program,
            })
            # Massing_parts-format for Three.js og Google Maps 3D
            if v.polygon is not None:
                try:
                    coords_groups = _polygon_to_coords_groups(v.polygon)
                    massing_parts_dicts.append({
                        "name": v.name,
                        "height_m": float(v.height_m),
                        "floors": int(v.floors),
                        "color": color_rgba,  # fase-farge
                        "coords": coords_groups,
                        "phase": v.assigned_phase,
                        "typology": v.typology,
                        "program": v.program,
                    })
                except Exception as exc:
                    logger.warning(f"Kunne ikke konvertere polygon for {v.name}: {exc}")

        # Majoritetstypologi for label
        typ_counts: Dict[str, int] = {}
        for v in phase_volumes:
            typ_counts[v.typology] = typ_counts.get(v.typology, 0) + 1
        dominant_typ = max(typ_counts.items(), key=lambda x: x[1])[0]

        unit_est = phase.units_estimate or sum(v.units_estimate for v in phase_volumes)

        # Notater som senere rendres i rapport
        notes = [
            phase.label,
            f"BRA: {phase_bra:,.0f} m² ({unit_est} boliger)",
            f"Programmer: {', '.join(phase.programs_included)}",
            f"Standalone-uterom: {phase.standalone_outdoor_m2:,.0f} m²",
            f"Parkering: P-fase {phase.parking_served_by}",
        ]
        if phase.standalone_issues:
            notes.append("Merknader:")
            for issue in phase.standalone_issues:
                notes.append(f"  • {issue}")

        # Vektet score (gjenbruk habitability)
        trinn_score = 0.0
        if masterplan.metrics and masterplan.metrics.standalone_habitability_score:
            trinn_score = float(masterplan.metrics.standalone_habitability_score)

        result = OptionResult_cls(
            name=f"Trinn {phase.phase_number} — {dominant_typ}",
            typology=dominant_typ,
            floors=max_floors_in_phase,
            building_height_m=max_height,
            footprint_area_m2=phase_fp_area,
            gross_bta_m2=phase_bta,
            saleable_area_m2=phase_bra,
            footprint_width_m=max((v.width_m for v in phase_volumes), default=0),
            footprint_depth_m=max((v.depth_m for v in phase_volumes), default=0),
            buildable_area_m2=float(masterplan.buildable_polygon.area),
            open_space_ratio=0.0,  # beregnes i dagens motor; vi slår av her
            target_fit_pct=100.0 if phase.standalone_habitable else 70.0,
            unit_count=unit_est,
            mix_counts=dict(mix_specs_dummy),
            parking_spaces=0,  # vises ikke per trinn — ligger på P-fase
            parking_pressure_pct=0.0,
            solar_score=trinn_score * 0.33,  # rough proxy
            estimated_equinox_sun_hours=4.0,
            estimated_winter_sun_hours=2.0,
            sunlit_open_space_pct=50.0,
            winter_noon_shadow_m=0.0,
            equinox_noon_shadow_m=0.0,
            summer_afternoon_shadow_m=0.0,
            efficiency_ratio=efficiency,
            neighbor_count=len(geodata_context.get("neighbors", [])),
            terrain_slope_pct=float(getattr(site, "terrain_slope_pct", 0.0)),
            terrain_relief_m=float(getattr(site, "terrain_relief_m", 0.0)),
            notes=notes,
            score=trinn_score,
            geometry={
                "buildings": buildings_dicts,
                "massing_parts": massing_parts_dicts,  # for Three.js og Google Maps 3D
                "footprint": phase_footprint,
                # Coord-groups som 3D-pipeline (build_geodata_scene_payload) forventer
                "site_polygon_coords": site_polygon_coords,
                "buildable_polygon_coords": buildable_polygon_coords,
                "footprint_polygon_coords": phase_footprint_coords,
                "neighbor_polygons": neighbor_polygons_coords,
                "placement": {
                    "n_buildings": len(phase_volumes),
                    "source": "Builtly Masterplan",
                },
                "source": "Builtly Masterplan",
                # Ekstra masterplan-kontekst
                "phase_number": phase.phase_number,
                "phase_label": phase.label,
                "phase_volumes": phase_volumes,
                "phase_dependencies": phase.depends_on_phases,
                "phase_parking_served_by": phase.parking_served_by,
                "phase_standalone_outdoor_m2": phase.standalone_outdoor_m2,
                "phase_standalone_habitable": phase.standalone_habitable,
                "phase_standalone_issues": phase.standalone_issues,
                "phase_construction_barrier_zone": phase.construction_barrier_zone,
                "masterplan_ref": masterplan,
            },
        )
        results.append(result)

    # Legg til "Totalt" som siste/første alternativ
    if masterplan.volumes:
        total_footprint = unary_union([v.polygon for v in masterplan.volumes if v.polygon])
        total_buildings = []
        total_massing_parts = []
        for v in masterplan.volumes:
            color_rgba = phase_color_rgba(v.assigned_phase, alpha=220)
            total_buildings.append({
                "polygon": v.polygon,
                "name": v.name,
                "role": "main",
                "floors": v.floors,
                "height_m": v.height_m,
                "width_m": v.width_m,
                "depth_m": v.depth_m,
                "angle_deg": v.angle_deg,
                "area_m2": v.footprint_m2,
                "cx": v.cx,
                "cy": v.cy,
                "courtyard": v.has_courtyard,
                "ring_depth": v.ring_depth_m,
                "notes": v.notes,
                "pos_id": v.volume_id,
                "phase": v.assigned_phase,
                "typology": v.typology,
                "program": v.program,
            })
            if v.polygon is not None:
                try:
                    coords_groups = _polygon_to_coords_groups(v.polygon)
                    total_massing_parts.append({
                        "name": v.name,
                        "height_m": float(v.height_m),
                        "floors": int(v.floors),
                        "color": color_rgba,
                        "coords": coords_groups,
                        "phase": v.assigned_phase,
                        "typology": v.typology,
                        "program": v.program,
                    })
                except Exception:
                    pass

        total_result = OptionResult_cls(
            name=f"Totalt — {len(masterplan.building_phases)} byggetrinn",
            typology="Masterplan",
            floors=max((v.floors for v in masterplan.volumes), default=0),
            building_height_m=max((v.height_m for v in masterplan.volumes), default=0),
            footprint_area_m2=float(total_footprint.area) if total_footprint else 0,
            gross_bta_m2=masterplan.metrics.total_bta,
            saleable_area_m2=masterplan.metrics.total_bra,
            footprint_width_m=0,
            footprint_depth_m=0,
            buildable_area_m2=float(masterplan.buildable_polygon.area),
            open_space_ratio=0.0,
            target_fit_pct=100.0 if masterplan.metrics.mua_compliant else 70.0,
            unit_count=masterplan.metrics.units_total,
            mix_counts=dict(mix_specs_dummy),
            parking_spaces=sum(p.num_spaces for p in masterplan.parking_phases),
            parking_pressure_pct=0.0,
            solar_score=masterplan.metrics.standalone_habitability_score * 0.33,
            estimated_equinox_sun_hours=4.0,
            estimated_winter_sun_hours=2.0,
            sunlit_open_space_pct=50.0,
            winter_noon_shadow_m=0.0,
            equinox_noon_shadow_m=0.0,
            summer_afternoon_shadow_m=0.0,
            efficiency_ratio=efficiency,
            neighbor_count=len(geodata_context.get("neighbors", [])),
            terrain_slope_pct=float(getattr(site, "terrain_slope_pct", 0.0)),
            terrain_relief_m=float(getattr(site, "terrain_relief_m", 0.0)),
            notes=[
                f"Total masterplan: {len(masterplan.volumes)} volumer, "
                f"{len(masterplan.building_phases)} byggetrinn, "
                f"{len(masterplan.parking_phases)} p-faser",
                f"MUA: {masterplan.metrics.mua_total_m2:,.0f} m² "
                f"(krav {masterplan.metrics.mua_required_m2:,.0f}), "
                f"{'compliant' if masterplan.metrics.mua_compliant else 'UNDERSKUDD'}",
                f"BYA: {masterplan.metrics.bya_percent:.1f}%",
                f"Snitt per trinn: {masterplan.metrics.avg_phase_bra:,.0f} m² "
                f"(min {masterplan.metrics.min_phase_bra:,.0f}, "
                f"max {masterplan.metrics.max_phase_bra:,.0f})",
            ],
            score=masterplan.metrics.overall_score,
            geometry={
                "buildings": total_buildings,
                "massing_parts": total_massing_parts,
                "footprint": total_footprint,
                # Coord-groups for 3D-pipeline
                "site_polygon_coords": site_polygon_coords,
                "buildable_polygon_coords": buildable_polygon_coords,
                "footprint_polygon_coords": _polygon_to_coords_groups(total_footprint),
                "neighbor_polygons": neighbor_polygons_coords,
                "placement": {
                    "n_buildings": len(masterplan.volumes),
                    "source": "Builtly Masterplan",
                },
                "source": "Builtly Masterplan",
                "is_total_plan": True,
                "masterplan_ref": masterplan,
            },
        )
        # Totalt-alternativet settes først slik at det er default
        results.insert(0, total_result)

    return results


# ─────────────────────────────────────────────────────────────────────
# 4. Rapportseksjon: Gantt + fase-kort
# ─────────────────────────────────────────────────────────────────────

def render_phasing_report_markdown(masterplan: Masterplan) -> str:
    """Generer markdown-seksjon om byggetrinn for rapport-teksten.

    Brukes i generate_report_markdown-pipeline før eller etter
    "ALTERNATIVER"-seksjonen.
    """
    lines = ["", "## BYGGETRINN OG FASERING", ""]
    m = masterplan.metrics
    lines.append(
        f"Masterplanen er delt i **{m.phase_count_buildings} byggetrinn** "
        f"à snitt **{m.avg_phase_bra:,.0f} m² BRA** (min {m.min_phase_bra:,.0f}, "
        f"max {m.max_phase_bra:,.0f}). "
        f"Parkering løses i **{m.phase_count_parking} fase(r)**. "
        f"Hvert trinn er vurdert for standalone-bokvalitet: "
        f"gjennomsnittsscore {m.standalone_habitability_score:.0f}/100."
    )
    lines.append("")

    lines.append("### Byggetrinn (rekkefølge)")
    lines.append("")
    for phase in masterplan.building_phases:
        habitable_mark = "✅ Standalone OK" if phase.standalone_habitable else "⚠ Se merknader"
        progs = ", ".join(phase.programs_included) if phase.programs_included else "bolig"
        deps = (", avhenger av trinn " + ", ".join(str(d) for d in phase.depends_on_phases)
                if phase.depends_on_phases else "")
        lines.append(
            f"- **Trinn {phase.phase_number}** ({phase.actual_bra:,.0f} m² BRA, "
            f"{phase.units_estimate} boliger) — {progs}. P-fase {phase.parking_served_by}. "
            f"Uterom {phase.standalone_outdoor_m2:,.0f} m². {habitable_mark}{deps}."
        )
        for issue in phase.standalone_issues:
            lines.append(f"    - {issue}")
    lines.append("")

    lines.append("### Parkeringsfaser")
    lines.append("")
    for pp in masterplan.parking_phases:
        ramp_txt = (
            f"{len(pp.ramps)} rampe(r)"
            if pp.ramps
            else f"utvider P-fase {pp.extends_parking_phases}"
        )
        lines.append(
            f"- **P{pp.phase_number}**: {pp.num_spaces} plasser, {ramp_txt}. "
            f"Betjener byggetrinn {pp.serves_building_phases}."
        )
    lines.append("")

    lines.append("### Uterom og MUA")
    lines.append("")
    mua_bakke = masterplan.outdoor_system.mua_on_ground_felles()
    mua_tak = masterplan.outdoor_system.mua_on_roof()
    mua_total = masterplan.outdoor_system.mua_total()
    compliant_mark = "✅" if masterplan.metrics.mua_compliant else "⚠"
    lines.append(
        f"- {compliant_mark} **Uteoppholdsareal**: {mua_total:,.0f} m² totalt, "
        f"derav {mua_bakke:,.0f} m² som felles på bakkeplan og {mua_tak:,.0f} m² på tak. "
        f"Krav (byggesone 2, 40 m²/bolig): {masterplan.metrics.mua_required_m2:,.0f} m²."
    )
    if masterplan.outdoor_system.diagonal_linestring:
        lines.append(
            "- **Diagonal**: Hovedferdselsåre gjennom tomta som grønn og sosial korridor "
            "(inspirert av LPO-mønster)."
        )
    lines.append("")

    if masterplan.warnings:
        lines.append("### Merknader")
        lines.append("")
        for w in masterplan.warnings:
            lines.append(f"- {w}")
        lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# 5. Gantt-visualisering (Streamlit chart)
# ─────────────────────────────────────────────────────────────────────

def render_phasing_gantt_streamlit(masterplan: Masterplan) -> None:
    """Gantt-lignende strømvisning i Streamlit som viser byggetrinn og
    parkeringsfaser langs tidsaksen. Bruker pure HTML + CSS slik at det
    render pent også i mørkt tema.
    """
    if not HAS_STREAMLIT or not masterplan.building_phases:
        return

    # Estimerte durasjoner: 24 måneder per byggetrinn (kan overstyres senere)
    DEFAULT_DURATION_MONTHS = 24
    # Tidslinje: regn måneder fra start av trinn 1
    month_schedule: Dict[int, Tuple[int, int]] = {}  # fase_nr → (start_month, end_month)
    current_start = 0
    for phase in masterplan.building_phases:
        dur = phase.estimated_duration_months or DEFAULT_DURATION_MONTHS
        month_schedule[phase.phase_number] = (current_start, current_start + dur)
        # Neste trinn starter 6 mnd inn i forrige (overlap for realisme)
        current_start += max(1, dur - 6)

    total_months = max(e for s, e in month_schedule.values()) if month_schedule else DEFAULT_DURATION_MONTHS

    # Bygg HTML-Gantt
    rows_html = []

    # Byggetrinn-rader
    for phase in masterplan.building_phases:
        s, e = month_schedule[phase.phase_number]
        left_pct = (s / total_months) * 100
        width_pct = ((e - s) / total_months) * 100
        color = "#38bdf8" if phase.standalone_habitable else "#fbbf24"
        label = f"T{phase.phase_number}: {phase.actual_bra:,.0f} m²"
        if phase.programs_included and "barnehage" in phase.programs_included:
            color = "#a78bfa"
            label += " 🏫"
        rows_html.append(
            f"<div style='display:grid;grid-template-columns:120px 1fr;gap:8px;align-items:center;margin:4px 0;'>"
            f"<div style='color:#cbd5e1;font-size:12px;'>Trinn {phase.phase_number}</div>"
            f"<div style='position:relative;background:#1e293b;height:28px;border-radius:4px;'>"
            f"<div style='position:absolute;left:{left_pct:.1f}%;width:{width_pct:.1f}%;"
            f"top:2px;bottom:2px;background:{color};border-radius:3px;padding:4px 8px;"
            f"color:#0f172a;font-size:11px;font-weight:600;white-space:nowrap;overflow:hidden;'>{label}</div>"
            f"</div></div>"
        )

    # Parkeringsfaser (plasseres typisk rett før tilsvarende byggefaser)
    for pp in masterplan.parking_phases:
        if not pp.serves_building_phases:
            continue
        first_served = min(pp.serves_building_phases)
        if first_served not in month_schedule:
            continue
        b_start = month_schedule[first_served][0]
        # P-fase bygges i 12 mnd før første tilhørende B-fase
        p_duration = 12
        p_start = max(0, b_start - p_duration)
        p_end = b_start
        left_pct = (p_start / total_months) * 100
        width_pct = ((p_end - p_start) / total_months) * 100
        label = f"P{pp.phase_number}: {pp.num_spaces} pl"
        rows_html.append(
            f"<div style='display:grid;grid-template-columns:120px 1fr;gap:8px;align-items:center;margin:4px 0;'>"
            f"<div style='color:#94a3b8;font-size:12px;'>P-fase {pp.phase_number}</div>"
            f"<div style='position:relative;background:#0f172a;height:24px;border-radius:4px;'>"
            f"<div style='position:absolute;left:{left_pct:.1f}%;width:{width_pct:.1f}%;"
            f"top:2px;bottom:2px;background:#64748b;border-radius:3px;padding:2px 8px;"
            f"color:#f1f5f9;font-size:10px;font-weight:500;white-space:nowrap;overflow:hidden;'>{label}</div>"
            f"</div></div>"
        )

    # Tids-akse (år-markører)
    years = max(1, total_months // 12 + 1)
    axis_html = "<div style='display:grid;grid-template-columns:120px 1fr;gap:8px;margin-top:12px;border-top:1px solid #334155;padding-top:6px;'>"
    axis_html += "<div style='color:#64748b;font-size:10px;'>Tidsakse</div>"
    axis_html += "<div style='display:flex;justify-content:space-between;'>"
    for yr in range(years + 1):
        axis_html += f"<span style='color:#64748b;font-size:10px;'>År {yr}</span>"
    axis_html += "</div></div>"

    gantt_html = (
        "<div style='background:#06111a;padding:18px;border-radius:10px;"
        "border:1px solid #1e293b;margin:14px 0;'>"
        "<div style='color:#e2e8f0;font-size:14px;font-weight:600;margin-bottom:10px;'>"
        "Byggetrinn-sekvens</div>"
        + "".join(rows_html)
        + axis_html
        + "</div>"
    )
    st.markdown(gantt_html, unsafe_allow_html=True)
    st.caption(
        "Estimert rekkefølge: 24 mnd per byggetrinn med 6 mnd overlapp. "
        "P-faser bygges ferdig før tilhørende byggetrinn tas i bruk. "
        "Faktisk tidsplan bestemmes i prosjekteringsfasen."
    )


# ─────────────────────────────────────────────────────────────────────
# 6. 3D-scene fargekoding etter byggetrinn
# ─────────────────────────────────────────────────────────────────────

PHASE_COLORS_HEX = [
    "#38bdf8",  # lysblå
    "#a78bfa",  # lilla
    "#34d399",  # grønn
    "#fbbf24",  # gul
    "#f87171",  # rød
    "#60a5fa",  # mørkere blå
    "#f472b6",  # rosa
    "#fb923c",  # oransje
    "#22d3ee",  # cyan
    "#c084fc",  # lilla-2
    "#84cc16",  # lime
    "#e879f9",  # magenta
]


def phase_color_for(phase_number: Optional[int]) -> str:
    """Returnér en hex-farge for gitt fase-nummer (1-indeksert)."""
    if not phase_number or phase_number < 1:
        return "#94a3b8"
    idx = (phase_number - 1) % len(PHASE_COLORS_HEX)
    return PHASE_COLORS_HEX[idx]


def phase_color_rgba(phase_number: Optional[int], alpha: int = 200) -> List[int]:
    """Returnér RGBA-array (0-255, 0-255, 0-255, alpha) for gitt fase."""
    hex_color = phase_color_for(phase_number)
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return [r, g, b, alpha]


def build_phase_legend_html(masterplan: Masterplan) -> str:
    """HTML-legende med fargekoding per byggetrinn — overlegges 3D-scenen."""
    if not masterplan.building_phases:
        return ""
    items = []
    for phase in masterplan.building_phases:
        color = phase_color_for(phase.phase_number)
        label = f"Trinn {phase.phase_number}"
        items.append(
            f"<div style='display:flex;align-items:center;gap:8px;margin:3px 0;'>"
            f"<span style='display:inline-block;width:14px;height:14px;background:{color};"
            f"border-radius:3px;'></span>"
            f"<span style='color:#e2e8f0;font-size:11px;'>{label} — {phase.actual_bra:,.0f} m²</span>"
            f"</div>"
        )
    return (
        "<div style='position:absolute;top:14px;right:14px;background:rgba(6,17,26,0.92);"
        "padding:10px 14px;border-radius:8px;border:1px solid #1e293b;max-height:280px;"
        "overflow-y:auto;z-index:100;'>"
        "<div style='color:#94a3b8;font-size:10px;text-transform:uppercase;"
        "letter-spacing:.05em;margin-bottom:6px;'>Byggetrinn</div>"
        + "".join(items)
        + "</div>"
    )
