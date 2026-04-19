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
import math
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
        "avg_unit_bra": 55.0,
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
        # v1.6: Pin-flagg for å holde Totalt øverst i UI uansett score.
        # Brukes setattr for å være bakoverkompatibel med OptionResult-varianter
        # som ikke har feltet i dataclass-definisjonen (f.eks. i tester).
        try:
            setattr(total_result, "is_total_plan", True)
        except Exception:
            pass
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

# =====================================================================
# Builtly patch v2 — bedre BRA-treff, konsistent effektivitet og strengere
# masterplan-rapportering for store tomter / flerfaseprosjekter.
# =====================================================================

_ORIG_RUN_MASTERPLAN_FROM_SITE_INPUTS = run_masterplan_from_site_inputs
_ORIG_MASTERPLAN_TO_OPTION_RESULTS = masterplan_to_option_results
_ORIG_RENDER_PHASING_REPORT_MARKDOWN = render_phasing_report_markdown


def _site_efficiency_ratio_v2(site: Any) -> float:
    try:
        eff = float(getattr(site, "efficiency_ratio", 0.85) or 0.85)
    except Exception:
        eff = 0.85
    return max(0.65, min(0.95, eff))


def run_masterplan_from_site_inputs(
    site: Any,
    geodata_context: Dict[str, Any],
    phasing_config: PhasingConfig,
    target_bra_m2: float,
    include_barnehage: bool = False,
    include_naering: bool = False,
    byggesone: str = "2",
) -> Tuple[Optional[Masterplan], Optional[str]]:
    """Patch: send faktisk efficiency_ratio inn i motoren og bruk samme faktor
    i sanity-checks som i resten av appen."""
    site_polygon = geodata_context.get("site_polygon")
    buildable_polygon = geodata_context.get("buildable_polygon")
    neighbors = geodata_context.get("neighbors", [])
    terrain = geodata_context.get("terrain")
    site_intelligence = geodata_context.get("site_intelligence")

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

    max_floors = int(getattr(site, "max_floors", 5))
    max_height_m = float(getattr(site, "max_height_m", 16.0))
    max_bya_pct = float(getattr(site, "max_bya_pct", 35.0))
    floor_to_floor = float(getattr(site, "floor_to_floor_m", 3.2))
    efficiency_ratio = _site_efficiency_ratio_v2(site)
    avg_unit_bra = float(getattr(site, "avg_unit_bra", 55.0) or 55.0)

    max_footprint = buildable_area * (max_bya_pct / 100.0)
    max_theoretical_bra = max_footprint * max_floors * efficiency_ratio
    if target_bra_m2 > max_theoretical_bra * 1.1:
        msg = (
            f"Mål-BRA {target_bra_m2:.0f} m² er urealistisk høyt gitt "
            f"byggbart areal ({buildable_area:.0f} m²), maks BYA {max_bya_pct:.0f}%, "
            f"maks {max_floors} etasjer og effektivitet {efficiency_ratio:.2f}. "
            f"Teoretisk maks BRA er ca {max_theoretical_bra:.0f} m². "
            f"Øk maks etasjer eller BYA, eller senk mål-BRA."
        )
        return None, msg

    site_inputs_dict = {
        "latitude_deg": float(getattr(site, "latitude_deg", 63.4)),
        "site_area_m2": float(getattr(site, "site_area_m2", 0.0)),
        "avg_unit_bra": avg_unit_bra,
        "efficiency_ratio": efficiency_ratio,
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


def _phase_efficiency_ratio_v2(phase, masterplan: Masterplan, fallback: float) -> float:
    vids = set(getattr(phase, "volume_ids", []) or [])
    vols = [v for v in masterplan.volumes if v.volume_id in vids]
    bta = sum(float(v.footprint_m2) * float(v.floors) for v in vols)
    bra = sum(float(v.bra_m2) for v in vols)
    if bta <= 0:
        return fallback
    return max(0.65, min(0.95, bra / bta))



def _phase_target_fit_pct_v2(phase) -> float:
    tgt = float(getattr(phase, "target_bra", 0.0) or 0.0)
    act = float(getattr(phase, "actual_bra", 0.0) or 0.0)
    if tgt <= 0:
        return 100.0
    return max(0.0, min(130.0, act / tgt * 100.0))



def masterplan_to_option_results(
    masterplan: Masterplan,
    site: Any,
    geodata_context: Dict[str, Any],
    OptionResult_cls: Any,
) -> List[Any]:
    """Patch: skriv tilbake reell efficiency_ratio og target-fit til OptionResults
    slik at rapport og dashboard viser konsistente BRA/BTA-tall."""
    results = _ORIG_MASTERPLAN_TO_OPTION_RESULTS(masterplan, site, geodata_context, OptionResult_cls)
    site_eff = _site_efficiency_ratio_v2(site)

    total_bta = float(getattr(masterplan.metrics, "total_bta", 0.0) or 0.0)
    total_bra = float(getattr(masterplan.metrics, "total_bra", 0.0) or 0.0)
    total_eff = (total_bra / total_bta) if total_bta > 0 else site_eff
    total_target_fit = float(getattr(masterplan.metrics, "target_fit_pct", 0.0) or 0.0)
    if total_target_fit <= 0:
        prog_target = float(getattr(masterplan.program, "total_bra", 0.0) or 0.0)
        total_target_fit = (total_bra / prog_target * 100.0) if prog_target > 0 else 100.0

    for res in results:
        geom = getattr(res, "geometry", {}) or {}
        is_total = bool(geom.get("is_total_plan") or getattr(res, "typology", "") == "Masterplan")
        if is_total:
            eff = total_eff
            target_fit = total_target_fit
            score = float(getattr(masterplan.metrics, "overall_score", getattr(res, "score", 0.0)) or 0.0)
            extra_notes = [
                f"Måloppnåelse BRA: {target_fit:.0f}%",
                f"BRA/BTA-effektivitet: {eff:.2f}",
            ]
        else:
            phase_n = geom.get("phase_number")
            phase = masterplan.phase_by_number(int(phase_n)) if phase_n is not None else None
            eff = _phase_efficiency_ratio_v2(phase, masterplan, site_eff) if phase is not None else site_eff
            target_fit = _phase_target_fit_pct_v2(phase) if phase is not None else 100.0
            score = float(getattr(res, "score", 0.0) or 0.0)
            extra_notes = [
                f"Måloppnåelse BRA: {target_fit:.0f}%",
                f"BRA/BTA-effektivitet: {eff:.2f}",
            ]

        try:
            setattr(res, "efficiency_ratio", eff)
        except Exception:
            pass
        try:
            setattr(res, "target_fit_pct", target_fit)
        except Exception:
            pass
        try:
            setattr(res, "score", score)
        except Exception:
            pass

        notes = list(getattr(res, "notes", []) or [])
        existing = {str(n) for n in notes}
        for note in extra_notes:
            if note not in existing:
                notes.append(note)
        try:
            setattr(res, "notes", notes)
        except Exception:
            pass

    return results



def render_phasing_report_markdown(masterplan: Masterplan) -> str:
    text = _ORIG_RENDER_PHASING_REPORT_MARKDOWN(masterplan)
    m = masterplan.metrics
    target_fit = float(getattr(m, "target_fit_pct", 0.0) or 0.0)
    if target_fit <= 0:
        prog_target = float(getattr(masterplan.program, "total_bra", 0.0) or 0.0)
        target_fit = (float(getattr(m, "total_bra", 0.0) or 0.0) / prog_target * 100.0) if prog_target > 0 else 100.0

    addon = [
        "",
        "### Måloppnåelse",
        "",
        f"- Oppnådd BRA mot mål: **{target_fit:.0f}%**",
        f"- BRA/BTA-effektivitet: **{(float(getattr(m, 'total_bra', 0.0) or 0.0) / max(float(getattr(m, 'total_bta', 0.0) or 1.0), 1.0)):.2f}**",
    ]
    if target_fit < 90.0:
        addon.append("- Planen bør densifiseres videre før den presenteres som anbefalt alternativ.")
    return text + "\n" + "\n".join(addon) + "\n"


# =====================================================================
# Builtly patch v3 — tydeliggjør lokalt vs delt uterom i rapport/UI.
# =====================================================================

_ORIG_V2_MASTERPLAN_TO_OPTION_RESULTS = masterplan_to_option_results
_ORIG_V2_RENDER_PHASING_REPORT_MARKDOWN = render_phasing_report_markdown


def masterplan_to_option_results(
    masterplan: Masterplan,
    site: Any,
    geodata_context: Dict[str, Any],
    OptionResult_cls: Any,
) -> List[Any]:
    results = _ORIG_V2_MASTERPLAN_TO_OPTION_RESULTS(masterplan, site, geodata_context, OptionResult_cls)
    for res in results:
        geom = getattr(res, "geometry", {}) or {}
        phase_num = geom.get("phase_number")
        if phase_num is None:
            continue
        phase = masterplan.phase_by_number(int(phase_num))
        if phase is None:
            continue

        local_out = float(getattr(phase, "local_outdoor_m2", 0.0) or 0.0)
        shared_credit = float(getattr(phase, "shared_outdoor_credit_m2", 0.0) or 0.0)
        total_credit = local_out + shared_credit

        geom["phase_local_outdoor_m2"] = local_out
        geom["phase_shared_outdoor_credit_m2"] = shared_credit
        geom["phase_standalone_outdoor_m2"] = total_credit

        notes = list(getattr(res, "notes", []) or [])
        note = (
            f"Uterom kreditert: {total_credit:,.0f} m² "
            f"(lokalt {local_out:,.0f} + delt {shared_credit:,.0f})"
        )
        if note not in notes:
            notes.append(note)
        try:
            setattr(res, "notes", notes)
        except Exception:
            pass
    return results



def render_phasing_report_markdown(masterplan: Masterplan) -> str:
    text = _ORIG_V2_RENDER_PHASING_REPORT_MARKDOWN(masterplan)
    lines = ["", "### Uteromskreditt per trinn", ""]
    added = False
    for phase in masterplan.building_phases:
        local_out = float(getattr(phase, "local_outdoor_m2", 0.0) or 0.0)
        shared_credit = float(getattr(phase, "shared_outdoor_credit_m2", 0.0) or 0.0)
        total_credit = local_out + shared_credit
        if total_credit <= 0:
            continue
        added = True
        lines.append(
            f"- T{phase.phase_number}: **{total_credit:.0f} m²** kreditert uterom "
            f"(lokalt {local_out:.0f} + delt {shared_credit:.0f})"
        )
    if not added:
        return text
    return text + "\n" + "\n".join(lines) + "\n"


# =====================================================================
# V4 PATCHES — bruk valgt gjennomsnittlig boligstørrelse i OptionResults.
# =====================================================================

_ORIG_V4_MASTERPLAN_TO_OPTION_RESULTS = masterplan_to_option_results


def _avg_unit_bra_from_site_v4(site: Any) -> float:
    try:
        val = float(getattr(site, "avg_unit_bra", 55.0) or 55.0)
    except Exception:
        val = 55.0
    return max(35.0, min(120.0, val))


def _residential_bra_from_phase_geometry_v4(phase_volumes: List[Any]) -> float:
    total = 0.0
    for v in phase_volumes or []:
        try:
            if getattr(v, "program", "bolig") != "bolig":
                continue
            floors = float(getattr(v, "floors", 0) or 0)
            gf = getattr(v, "ground_floor_program", None)
            if gf and gf != "bolig":
                floors = max(0.0, floors - 1.0)
            eff = float(getattr(v, "bra_efficiency_ratio", 0.85) or 0.85)
            total += float(getattr(v, "footprint_m2", 0.0) or 0.0) * floors * eff
        except Exception:
            continue
    return total


def masterplan_to_option_results(
    masterplan: Masterplan,
    site: Any,
    geodata_context: Dict[str, Any],
    OptionResult_cls: Any,
) -> List[Any]:
    results = _ORIG_V4_MASTERPLAN_TO_OPTION_RESULTS(masterplan, site, geodata_context, OptionResult_cls)
    avg_unit_bra = _avg_unit_bra_from_site_v4(site)
    total_res_bra = _residential_bra_from_phase_geometry_v4(masterplan.volumes)
    total_units = int(round(total_res_bra / avg_unit_bra)) if total_res_bra > 0 else 0

    for res in results:
        geom = getattr(res, "geometry", {}) or {}
        is_total = bool(geom.get("is_total_plan") or getattr(res, "typology", "") == "Masterplan")
        if is_total:
            new_units = total_units
        else:
            phase_volumes = geom.get("phase_volumes", []) or []
            res_bra = _residential_bra_from_phase_geometry_v4(phase_volumes)
            new_units = int(round(res_bra / avg_unit_bra)) if res_bra > 0 else int(getattr(res, "unit_count", 0) or 0)
        if new_units > 0:
            try:
                setattr(res, "unit_count", new_units)
            except Exception:
                pass
            notes = list(getattr(res, "notes", []) or [])
            note = f"Boligantall v4: ca. {new_units} boliger ved {avg_unit_bra:.1f} m² snitt"
            if note not in notes:
                notes.append(note)
            try:
                setattr(res, "notes", notes)
            except Exception:
                pass
    return results

# =====================================================================
# V5 PATCHES — delfeltmetadata tilbake til UI/rapport/3D-scene.
# =====================================================================

_ORIG_V5_MASTERPLAN_TO_OPTION_RESULTS = masterplan_to_option_results


def _development_fields_payload_v5(masterplan: Masterplan) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for f in getattr(masterplan, 'development_fields', []) or []:
        try:
            payload.append({
                'field_id': getattr(f, 'field_id', ''),
                'name': getattr(f, 'name', ''),
                'context': getattr(f, 'context', ''),
                'side_hint': getattr(f, 'side_hint', ''),
                'target_bra': float(getattr(f, 'target_bra', 0.0) or 0.0),
                'target_phase_count': int(getattr(f, 'target_phase_count', 1) or 1),
                'polygon_coords': _polygon_to_coords_groups(getattr(f, 'polygon', None)),
                'courtyard_coords': _polygon_to_coords_groups(getattr(f, 'courtyard_polygon', None)),
                'zone_ids': list(getattr(f, 'zone_ids', []) or []),
                'primary_outdoor_name': getattr(f, 'primary_outdoor_name', ''),
                'primary_outdoor_program': getattr(f, 'primary_outdoor_program', ''),
                'notes': getattr(f, 'notes', ''),
            })
        except Exception:
            continue
    return payload


def masterplan_to_option_results(
    masterplan: Masterplan,
    site: Any,
    geodata_context: Dict[str, Any],
    OptionResult_cls: Any,
) -> List[Any]:
    results = _ORIG_V5_MASTERPLAN_TO_OPTION_RESULTS(masterplan, site, geodata_context, OptionResult_cls)
    field_payload = _development_fields_payload_v5(masterplan)
    field_name_map = {f.get('field_id'): f.get('name') for f in field_payload}

    for res in results:
        geom = getattr(res, 'geometry', {}) or {}
        is_total = bool(geom.get('is_total_plan') or getattr(res, 'typology', '') == 'Masterplan')
        notes = list(getattr(res, 'notes', []) or [])

        if is_total:
            geom['development_fields'] = field_payload
            geom['development_field_count'] = len(field_payload)
            if field_payload:
                field_names = [f.get('name', '') for f in field_payload if f.get('name')]
                note = f"Delfelt v5: {len(field_payload)} felt — " + ", ".join(field_names)
                if note not in notes:
                    notes.append(note)
        else:
            phase_num = geom.get('phase_number')
            phase = masterplan.phase_by_number(int(phase_num)) if phase_num is not None else None
            if phase is not None:
                field_ids = list(getattr(phase, 'field_ids', []) or [])
                field_names = list(getattr(phase, 'field_names', []) or [])
                if not field_names:
                    field_names = [field_name_map.get(fid, fid) for fid in field_ids if fid]
                geom['phase_field_ids'] = field_ids
                geom['phase_field_names'] = field_names
                geom['phase_field_count'] = len(field_names)
                geom['phase_is_field_coherent'] = len(field_names) <= 1
                if field_names:
                    note = 'Delfelt: ' + ' + '.join(field_names)
                    if note not in notes:
                        notes.append(note)
                try:
                    if getattr(phase, 'label', ''):
                        setattr(res, 'name', phase.label)
                except Exception:
                    pass

        try:
            setattr(res, 'geometry', geom)
        except Exception:
            pass
        try:
            setattr(res, 'notes', notes)
        except Exception:
            pass

    return results

# =====================================================================
# V6 FINAL PATCHES — lesbare husnavn, meningsfull trinntabell og
# presentasjonsmetadata for 2D/3D/UI/PDF.
# =====================================================================

_ORIG_V6_MASTERPLAN_TO_OPTION_RESULTS = masterplan_to_option_results


def _avg_unit_bra_from_site_v6(site: Any) -> float:
    try:
        val = float(getattr(site, 'avg_unit_bra', 55.0) or 55.0)
    except Exception:
        val = 55.0
    return max(35.0, min(120.0, val))


def _phase_summary_row_v6(phase: BuildingPhase, phase_volumes: List[Volume]) -> Dict[str, Any]:
    bta = sum(float(v.footprint_m2 or 0.0) * float(v.floors or 0.0) for v in phase_volumes)
    bra = sum(float(v.bra_m2 or 0.0) for v in phase_volumes)
    footprint = sum(float(v.footprint_m2 or 0.0) for v in phase_volumes)
    max_floors = max((int(v.floors or 0) for v in phase_volumes), default=0)
    min_floors = min((int(v.floors or 0) for v in phase_volumes), default=0)
    floor_text = f"{min_floors}-{max_floors}" if min_floors and min_floors != max_floors else str(max_floors or min_floors or 0)
    return {
        'phase_number': int(getattr(phase, 'phase_number', 0) or 0),
        'label': getattr(phase, 'label', '') or f"Trinn {getattr(phase, 'phase_number', '?')}",
        'field_ids': list(getattr(phase, 'field_ids', []) or []),
        'field_names': list(getattr(phase, 'field_names', []) or []),
        'bta_m2': round(bta, 1),
        'bra_m2': round(bra, 1),
        'footprint_m2': round(footprint, 1),
        'units': int(sum(int(getattr(v, 'units_estimate', 0) or 0) for v in phase_volumes)),
        'segments': len(phase_volumes),
        'floors_text': floor_text,
        'max_floors': max_floors,
        'duration_months': int(getattr(phase, 'estimated_duration_months', 0) or 0),
        'programs': list(dict.fromkeys([str(getattr(v, 'program', '') or '') for v in phase_volumes if getattr(v, 'program', None)])),
    }


def _building_roster_v6(masterplan: Masterplan) -> List[Dict[str, Any]]:
    roster: List[Dict[str, Any]] = []
    by_phase: Dict[str, int] = {}
    for p in getattr(masterplan, 'building_phases', []) or []:
        for vid in getattr(p, 'volume_ids', []) or []:
            by_phase[vid] = int(getattr(p, 'phase_number', 0) or 0)
    ordered = sorted(
        list(getattr(masterplan, 'volumes', []) or []),
        key=lambda v: (str(getattr(v, 'house_id', '') or getattr(v, 'name', '')), -float(getattr(v, 'cy', 0.0) or 0.0), float(getattr(v, 'cx', 0.0) or 0.0)),
    )
    for v in ordered:
        roster.append({
            'house_id': getattr(v, 'house_id', '') or getattr(v, 'name', ''),
            'name': getattr(v, 'display_name', '') or getattr(v, 'name', ''),
            'internal_name': getattr(v, 'internal_name', '') or getattr(v, 'volume_id', ''),
            'field_id': getattr(v, 'field_id', '') or '',
            'field_name': getattr(v, 'field_name', '') or '',
            'typology': getattr(v, 'typology', ''),
            'program': getattr(v, 'program', ''),
            'floors': int(getattr(v, 'floors', 0) or 0),
            'height_m': round(float(getattr(v, 'height_m', 0.0) or 0.0), 1),
            'footprint_m2': round(float(getattr(v, 'footprint_m2', 0.0) or 0.0), 1),
            'bra_m2': round(float(getattr(v, 'bra_m2', 0.0) or 0.0), 1),
            'phase_number': int(by_phase.get(getattr(v, 'volume_id', ''), getattr(v, 'assigned_phase', 0) or 0)),
            'cx': round(float(getattr(v, 'cx', 0.0) or 0.0), 1),
            'cy': round(float(getattr(v, 'cy', 0.0) or 0.0), 1),
            'coords': _polygon_to_coords_groups(getattr(v, 'polygon', None)),
        })
    return roster


def _outdoor_payload_v6(masterplan: Masterplan) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for z in getattr(getattr(masterplan, 'outdoor_system', None), 'zones', []) or []:
        payload.append({
            'zone_id': getattr(z, 'zone_id', ''),
            'kind': getattr(z, 'kind', ''),
            'area_m2': round(float(getattr(z, 'area_m2', 0.0) or 0.0), 1),
            'is_felles': bool(getattr(z, 'is_felles', False)),
            'on_ground': bool(getattr(z, 'on_ground', False)),
            'counts_toward_mua': bool(getattr(z, 'counts_toward_mua', False)),
            'requires_sun_hours': float(getattr(z, 'requires_sun_hours', 0.0) or 0.0),
            'serves_building_phases': list(getattr(z, 'serves_building_phases', []) or []),
            'notes': getattr(z, 'notes', '') or '',
            'coords': _polygon_to_coords_groups(getattr(z, 'geometry', None)),
        })
    return payload


def masterplan_to_option_results(
    masterplan: Masterplan,
    site: Any,
    geodata_context: Dict[str, Any],
    OptionResult_cls: Any,
) -> List[Any]:
    results = _ORIG_V6_MASTERPLAN_TO_OPTION_RESULTS(masterplan, site, geodata_context, OptionResult_cls)
    avg_unit_bra = _avg_unit_bra_from_site_v6(site)
    field_payload = _development_fields_payload_v5(masterplan)
    field_name_map = {f.get('field_id'): f.get('name') for f in field_payload}
    outdoor_payload = _outdoor_payload_v6(masterplan)
    building_roster = _building_roster_v6(masterplan)
    phase_rows: List[Dict[str, Any]] = []
    for phase in getattr(masterplan, 'building_phases', []) or []:
        vols = [v for v in getattr(masterplan, 'volumes', []) or [] if v.volume_id in (phase.volume_ids or [])]
        phase_rows.append(_phase_summary_row_v6(phase, vols))
    target_fit_total = float(getattr(getattr(masterplan, 'metrics', None), 'target_fit_pct', 0.0) or 0.0)

    roster_by_phase: Dict[int, List[Dict[str, Any]]] = {}
    for row in building_roster:
        roster_by_phase.setdefault(int(row.get('phase_number', 0) or 0), []).append(row)

    for res in results:
        geom = getattr(res, 'geometry', {}) or {}
        is_total = bool(geom.get('is_total_plan') or getattr(res, 'typology', '') == 'Masterplan')
        notes = list(getattr(res, 'notes', []) or [])

        # Sørg for at massing_parts bruker lesbare navn og kompakt metadata.
        new_parts = []
        for part in list(geom.get('massing_parts', []) or []):
            part_name = part.get('name', '')
            roster_match = next((r for r in building_roster if r.get('house_id') == part_name or r.get('internal_name') == part_name or r.get('name') == part_name), None)
            if roster_match is not None:
                part = dict(part)
                part['name'] = roster_match.get('house_id') or part.get('name')
                part['house_id'] = roster_match.get('house_id', '')
                part['display_name'] = roster_match.get('name', '')
                part['internal_name'] = roster_match.get('internal_name', '')
                part['field_name'] = roster_match.get('field_name', '')
                part['bra_m2'] = roster_match.get('bra_m2', 0.0)
                part['phase'] = roster_match.get('phase_number', part.get('phase'))
            new_parts.append(part)
        if new_parts:
            geom['massing_parts'] = new_parts

        if is_total:
            geom['development_fields'] = field_payload
            geom['development_field_count'] = len(field_payload)
            geom['building_roster'] = building_roster
            geom['phase_summary_rows'] = phase_rows
            geom['outdoor_zones'] = outdoor_payload
            geom['mua_summary'] = {
                'ground_felles_m2': round(float(masterplan.outdoor_system.mua_on_ground_felles() if masterplan.outdoor_system else 0.0), 1),
                'roof_m2': round(float(masterplan.outdoor_system.mua_on_roof() if masterplan.outdoor_system else 0.0), 1),
                'private_m2': round(float(masterplan.outdoor_system.mua_privat() if masterplan.outdoor_system else 0.0), 1),
                'total_m2': round(float(masterplan.outdoor_system.mua_total() if masterplan.outdoor_system else 0.0), 1),
                'required_m2': round(float(getattr(getattr(masterplan, 'metrics', None), 'mua_required_m2', 0.0) or 0.0), 1),
                'compliant': bool(getattr(getattr(masterplan, 'metrics', None), 'mua_compliant', False)),
                'avg_unit_bra': round(avg_unit_bra, 1),
                'units': int(getattr(res, 'unit_count', 0) or 0),
            }
            if target_fit_total > 0:
                try:
                    setattr(res, 'target_fit_pct', round(target_fit_total, 1))
                except Exception:
                    pass
            if building_roster:
                note = f"Husoversikt v6: {len(building_roster)} bygg — {', '.join(r.get('house_id', '') for r in building_roster[:8])}"
                if note not in notes:
                    notes.append(note)
        else:
            phase_num = int(geom.get('phase_number') or 0)
            phase = masterplan.phase_by_number(phase_num) if phase_num else None
            phase_roster = roster_by_phase.get(phase_num, [])
            if phase is not None:
                field_ids = list(getattr(phase, 'field_ids', []) or [])
                field_names = list(getattr(phase, 'field_names', []) or [])
                if not field_names:
                    field_names = [field_name_map.get(fid, fid) for fid in field_ids if fid]
                geom['phase_field_ids'] = field_ids
                geom['phase_field_names'] = field_names
                geom['phase_field_count'] = len(field_names)
                geom['phase_is_field_coherent'] = len(field_names) <= 1
                geom['phase_summary_row'] = _phase_summary_row_v6(phase, [v for v in masterplan.volumes if v.volume_id in (phase.volume_ids or [])])
                geom['building_roster'] = phase_roster
                try:
                    setattr(res, 'name', phase.label or getattr(res, 'name', ''))
                    setattr(res, 'target_fit_pct', round(100.0 * float(getattr(phase, 'actual_bra', 0.0) or 0.0) / max(float(getattr(phase, 'target_bra', 0.0) or 1.0), 1.0), 1))
                except Exception:
                    pass
                if field_names:
                    note = 'Delfelt: ' + ' + '.join(field_names)
                    if note not in notes:
                        notes.append(note)
            if phase_roster:
                note = f"Hus i trinn {phase_num}: " + ', '.join(r.get('house_id', '') for r in phase_roster[:8])
                if note not in notes:
                    notes.append(note)

        try:
            setattr(res, 'geometry', geom)
            setattr(res, 'notes', notes)
        except Exception:
            pass

    return results

# =====================================================================
# V7 PATCH — Builtly presentasjonslag: færre, renere husformer i UI/rapport,
# korrekt MUA-oppsummering og leilighetsmiks per alternativ.
# =====================================================================

_ORIG_V7_MASTERPLAN_TO_OPTION_RESULTS = masterplan_to_option_results


def _mix_specs_from_site_v7(site: Any) -> List[Tuple[str, float, float]]:
    raw = list(getattr(site, 'mix_specs', []) or [])
    specs: List[Tuple[str, float, float]] = []
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get('name', '') or '').strip()
            share = float(item.get('share_pct', 0.0) or 0.0)
            size = float(item.get('avg_size_m2', 0.0) or 0.0)
        else:
            name = str(getattr(item, 'name', '') or '').strip()
            share = float(getattr(item, 'share_pct', 0.0) or 0.0)
            size = float(getattr(item, 'avg_size_m2', 0.0) or 0.0)
        if name and size > 0 and share >= 0:
            specs.append((name, share, size))
    if not specs:
        specs = [('1-rom', 15.0, 38.0), ('2-rom', 35.0, 52.0), ('3-rom', 35.0, 72.0), ('4-rom+', 15.0, 95.0)]
    total = sum(s[1] for s in specs) or 1.0
    return [(name, share / total * 100.0, size) for name, share, size in specs]


def _allocate_mix_counts_v7(saleable_area_m2: float, specs: List[Tuple[str, float, float]]) -> Dict[str, int]:
    counts = {name: 0 for name, _, _ in specs}
    if saleable_area_m2 <= 0:
        return counts
    target_areas = [saleable_area_m2 * (share / 100.0) for _, share, _ in specs]
    count_list = [max(0, int(area // max(size, 1.0))) for area, (_, _, size) in zip(target_areas, specs)]
    used = sum(count * size for count, (_, _, size) in zip(count_list, specs))
    remainder = max(0.0, saleable_area_m2 - used)
    # Fyll restareal på mest arealeffektive enhetstyper først.
    order = sorted(range(len(specs)), key=lambda i: specs[i][2])
    while order and remainder >= min(specs[i][2] for i in order):
        placed = False
        for i in order:
            size = specs[i][2]
            if remainder + 1e-6 >= size * 0.92:
                count_list[i] += 1
                remainder -= size
                placed = True
                break
        if not placed:
            break
    for (name, _, _), cnt in zip(specs, count_list):
        counts[name] = int(cnt)
    return counts


def _component_groups_v7(volumes: List[Volume], gap_m: float = 5.0) -> List[List[Volume]]:
    from shapely.geometry import MultiPolygon as _MP
    from shapely.ops import unary_union as _uu
    if not volumes:
        return []
    buffered = [v.polygon.buffer(gap_m / 2.0) for v in volumes if getattr(v, 'polygon', None) is not None]
    if not buffered:
        return []
    merged = _uu(buffered).buffer(0)
    comps = list(merged.geoms) if isinstance(merged, _MP) else [merged]
    groups: List[List[Volume]] = []
    remaining = list(volumes)
    for comp in comps:
        members = [v for v in remaining if getattr(v, 'polygon', None) is not None and v.polygon.buffer(gap_m / 2.0).intersects(comp)]
        if members:
            groups.append(members)
            remaining = [v for v in remaining if v not in members]
    for v in remaining:
        groups.append([v])
    return groups


def _clean_presentation_polygon_v7(polys: List[Any], typology: str, field_poly: Any = None):
    from shapely import affinity as _aff
    from shapely.ops import unary_union as _uu
    if not polys:
        return None
    geom = _uu([p for p in polys if p is not None]).buffer(0)
    if getattr(geom, 'is_empty', True):
        return None
    typ = str(typology or '')
    clean = geom
    try:
        if typ in {'Lamell', 'LamellSegmentert', 'Rekke', 'Punkthus'}:
            rect = geom.minimum_rotated_rectangle
            if float(getattr(rect, 'area', 0.0) or 0.0) > 1.0:
                scale = math.sqrt(max(float(geom.area), 1.0) / max(float(rect.area), 1.0))
                clean = _aff.scale(rect, xfact=scale, yfact=scale, origin='center').buffer(0)
        elif typ in {'Karré', 'HalvåpenKarré'}:
            clean = geom.buffer(0.8).buffer(-0.8).simplify(0.6, preserve_topology=True).buffer(0)
        else:
            clean = geom.simplify(0.6, preserve_topology=True).buffer(0)
        if field_poly is not None and not getattr(field_poly, 'is_empty', True):
            clean = clean.intersection(field_poly.buffer(0.5)).buffer(0)
        if getattr(clean, 'is_empty', True):
            clean = geom.buffer(0)
    except Exception:
        clean = geom.buffer(0)
    return clean


def _presentation_buildings_v7(masterplan: Masterplan) -> List[Dict[str, Any]]:
    from shapely.ops import unary_union as _uu
    phase_by_vid: Dict[str, int] = {}
    for p in getattr(masterplan, 'building_phases', []) or []:
        for vid in getattr(p, 'volume_ids', []) or []:
            phase_by_vid[vid] = int(getattr(p, 'phase_number', 0) or 0)
    field_polys = {f.field_id: getattr(f, 'polygon', None) for f in getattr(masterplan, 'development_fields', []) or []}

    buckets: Dict[Tuple[int, str, str, int], List[Volume]] = {}
    for v in getattr(masterplan, 'volumes', []) or []:
        if getattr(v, 'polygon', None) is None:
            continue
        phase = int(phase_by_vid.get(getattr(v, 'volume_id', ''), getattr(v, 'assigned_phase', 0) or 0))
        field_id = str(getattr(v, 'field_id', '') or '')
        typology = str(getattr(v, 'typology', '') or '')
        angle_bucket = int(round((float(getattr(v, 'angle_deg', 0.0) or 0.0) % 180.0) / 15.0))
        buckets.setdefault((phase, field_id, typology, angle_bucket), []).append(v)

    houses: List[Dict[str, Any]] = []
    for (phase, field_id, typology, _), vols in buckets.items():
        field_poly = field_polys.get(field_id)
        comps = _component_groups_v7(vols, gap_m=5.0)
        for comp in comps:
            poly = _clean_presentation_polygon_v7([v.polygon for v in comp], typology, field_poly=field_poly)
            if poly is None or getattr(poly, 'is_empty', True):
                continue
            bra = sum(float(getattr(v, 'bra_m2', 0.0) or 0.0) for v in comp)
            footprint = float(getattr(poly, 'area', 0.0) or 0.0)
            floors_vals = [int(getattr(v, 'floors', 0) or 0) for v in comp]
            floors = max(floors_vals or [0])
            height = max(float(getattr(v, 'height_m', 0.0) or 0.0) for v in comp)
            cx = float(getattr(poly.centroid, 'x', 0.0) or 0.0)
            cy = float(getattr(poly.centroid, 'y', 0.0) or 0.0)
            field_name = next((str(getattr(v, 'field_name', '') or '') for v in comp if getattr(v, 'field_name', '')), '')
            internal_name = ', '.join([str(getattr(v, 'internal_name', '') or getattr(v, 'volume_id', '') or '') for v in comp[:4]])
            program = next((str(getattr(v, 'program', '') or '') for v in comp if getattr(v, 'program', '')), 'bolig')
            houses.append({
                'phase_number': int(phase),
                'field_id': field_id,
                'field_name': field_name,
                'typology': typology,
                'program': program,
                'floors': int(floors),
                'height_m': round(height, 1),
                'footprint_m2': round(footprint, 1),
                'bra_m2': round(bra, 1),
                'units_estimate': int(sum(int(getattr(v, 'units_estimate', 0) or 0) for v in comp)),
                'cx': round(cx, 1),
                'cy': round(cy, 1),
                'internal_name': internal_name,
                'source_volume_ids': [getattr(v, 'volume_id', '') for v in comp],
                'polygon': poly,
                'coords': _polygon_to_coords_groups(poly),
            })

    houses.sort(key=lambda r: (-float(r.get('cy', 0.0) or 0.0), float(r.get('cx', 0.0) or 0.0), str(r.get('field_name', '') or '')))
    for idx, row in enumerate(houses):
        row['house_id'] = f"HUS {chr(65 + (idx % 26))}" if idx < 26 else f"HUS {chr(65 + (idx // 26) - 1)}{chr(65 + (idx % 26))}"
        role = 'Bygg'
        typ = str(row.get('typology', '') or '')
        if typ in {'Lamell', 'LamellSegmentert'}:
            role = 'Lamell'
        elif typ in {'Punkthus', 'Tårn'}:
            role = 'Punkthus'
        elif typ in {'Karré', 'HalvåpenKarré'}:
            role = 'Kvartalhus'
        elif typ == 'Rekke':
            role = 'Rekkehus'
        elif typ == 'Gårdsklynge':
            role = 'Tunhus'
        field_name = str(row.get('field_name', '') or '').strip()
        row['name'] = f"{field_name} – {role}".strip(' –') if field_name else role
    return houses


def _massing_from_houses_v7(houses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for h in houses:
        out.append({
            'name': h.get('house_id', h.get('name', '')),
            'house_id': h.get('house_id', ''),
            'display_name': h.get('name', ''),
            'internal_name': h.get('internal_name', ''),
            'height_m': float(h.get('height_m', 0.0) or 0.0),
            'floors': int(h.get('floors', 0) or 0),
            'coords': h.get('coords', []),
            'phase': int(h.get('phase_number', 0) or 0),
            'typology': h.get('typology', ''),
            'program': h.get('program', 'bolig'),
            'field_name': h.get('field_name', ''),
            'bra_m2': float(h.get('bra_m2', 0.0) or 0.0),
            'color': phase_color_rgba(int(h.get('phase_number', 0) or 0), alpha=220),
        })
    return out


def _phase_summary_rows_from_houses_v7(masterplan: Masterplan, houses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_phase: Dict[int, List[Dict[str, Any]]] = {}
    for h in houses:
        by_phase.setdefault(int(h.get('phase_number', 0) or 0), []).append(h)
    rows: List[Dict[str, Any]] = []
    for phase in getattr(masterplan, 'building_phases', []) or []:
        phase_num = int(getattr(phase, 'phase_number', 0) or 0)
        ph_houses = by_phase.get(phase_num, [])
        if ph_houses:
            bta = sum(float(h.get('footprint_m2', 0.0) or 0.0) * float(h.get('floors', 0) or 0.0) for h in ph_houses)
            bra = sum(float(h.get('bra_m2', 0.0) or 0.0) for h in ph_houses)
            footprint = sum(float(h.get('footprint_m2', 0.0) or 0.0) for h in ph_houses)
            floors = [int(h.get('floors', 0) or 0) for h in ph_houses]
            floor_text = f"{min(floors)}-{max(floors)}" if floors and min(floors) != max(floors) else str(max(floors or [0]))
            rows.append({
                'phase_number': phase_num,
                'label': getattr(phase, 'label', '') or f"Trinn {phase_num}",
                'field_ids': list(getattr(phase, 'field_ids', []) or []),
                'field_names': list(getattr(phase, 'field_names', []) or []),
                'bta_m2': round(bta, 1),
                'bra_m2': round(bra, 1),
                'footprint_m2': round(footprint, 1),
                'units': int(sum(int(h.get('units_estimate', 0) or 0) for h in ph_houses)),
                'segments': len(ph_houses),
                'floors_text': floor_text,
                'max_floors': max(floors or [0]),
                'duration_months': int(getattr(phase, 'estimated_duration_months', 0) or 0),
                'programs': list(dict.fromkeys(str(h.get('program', '') or '') for h in ph_houses if h.get('program'))),
            })
        else:
            rows.append(_phase_summary_row_v6(phase, [v for v in masterplan.volumes if v.volume_id in (phase.volume_ids or [])]))
    return rows


def _mua_summary_from_outdoor_v7(masterplan: Masterplan, outdoor_payload: List[Dict[str, Any]], units: int, avg_unit_bra: float) -> Dict[str, Any]:
    ground_common = sum(float(z.get('area_m2', 0.0) or 0.0) for z in outdoor_payload if z.get('counts_toward_mua') and z.get('is_felles') and z.get('on_ground'))
    roof_mua = sum(float(z.get('area_m2', 0.0) or 0.0) for z in outdoor_payload if z.get('counts_toward_mua') and not z.get('on_ground'))
    private_mua = sum(float(z.get('area_m2', 0.0) or 0.0) for z in outdoor_payload if z.get('counts_toward_mua') and not z.get('is_felles'))
    total_mua = ground_common + roof_mua + private_mua
    required = float(getattr(getattr(masterplan, 'metrics', None), 'mua_required_m2', 0.0) or units * 40.0)
    compliant = total_mua >= required and ground_common >= required * 0.25 and (ground_common + roof_mua) >= required * 0.5
    return {
        'ground_common_m2': round(ground_common, 1),
        'ground_felles_m2': round(ground_common, 1),
        'roof_m2': round(roof_mua, 1),
        'private_m2': round(private_mua, 1),
        'total_mua_m2': round(total_mua, 1),
        'total_m2': round(total_mua, 1),
        'required_m2': round(required, 1),
        'compliant': bool(compliant),
        'avg_unit_bra': round(avg_unit_bra, 1),
        'units': int(units),
        'diagnostic_status': 'ok' if ground_common > 0 else 'missing_ground_mua',
        'diagnostic_message': '' if ground_common > 0 else 'Bakke-MUA kunne ikke beregnes sikkert fra uteromssonene.',
    }


def masterplan_to_option_results(
    masterplan: Masterplan,
    site: Any,
    geodata_context: Dict[str, Any],
    OptionResult_cls: Any,
) -> List[Any]:
    results = _ORIG_V7_MASTERPLAN_TO_OPTION_RESULTS(masterplan, site, geodata_context, OptionResult_cls)
    avg_unit_bra = _avg_unit_bra_from_site_v6(site)
    mix_specs = _mix_specs_from_site_v7(site)
    field_payload = _development_fields_payload_v5(masterplan)
    field_payload_map = {f.get('field_id'): f for f in field_payload}
    outdoor_payload = _outdoor_payload_v6(masterplan)
    presentation_houses = _presentation_buildings_v7(masterplan)
    houses_by_phase: Dict[int, List[Dict[str, Any]]] = {}
    for h in presentation_houses:
        houses_by_phase.setdefault(int(h.get('phase_number', 0) or 0), []).append(h)
    phase_rows = _phase_summary_rows_from_houses_v7(masterplan, presentation_houses)
    phase_row_map = {int(r.get('phase_number', 0) or 0): r for r in phase_rows}

    for res in results:
        geom = getattr(res, 'geometry', {}) or {}
        is_total = bool(geom.get('is_total_plan') or getattr(res, 'typology', '') == 'Masterplan')
        try:
            mix_counts = _allocate_mix_counts_v7(float(getattr(res, 'saleable_area_m2', 0.0) or 0.0), mix_specs)
            setattr(res, 'mix_counts', mix_counts)
            if sum(mix_counts.values()) > 0:
                setattr(res, 'unit_count', int(sum(mix_counts.values())))
        except Exception:
            pass
        notes = list(getattr(res, 'notes', []) or [])

        if is_total:
            total_units = int(getattr(res, 'unit_count', 0) or 0)
            mua_summary = _mua_summary_from_outdoor_v7(masterplan, outdoor_payload, total_units, avg_unit_bra)
            geom['development_fields'] = field_payload
            geom['development_field_count'] = len(field_payload)
            geom['building_roster'] = presentation_houses
            geom['phase_summary_rows'] = phase_rows
            geom['outdoor_zones'] = outdoor_payload
            geom['mua_summary'] = mua_summary
            geom['massing_parts'] = _massing_from_houses_v7(presentation_houses)
            geom['buildings'] = []
            if mua_summary.get('diagnostic_status') != 'ok':
                notes.append(mua_summary.get('diagnostic_message', ''))
        else:
            phase_num = int(geom.get('phase_number') or 0)
            phase_houses = houses_by_phase.get(phase_num, [])
            geom['building_roster'] = phase_houses
            geom['massing_parts'] = _massing_from_houses_v7(phase_houses)
            geom['phase_summary_row'] = phase_row_map.get(phase_num, geom.get('phase_summary_row'))
            phase = masterplan.phase_by_number(phase_num) if phase_num else None
            if phase is not None:
                field_ids = list(getattr(phase, 'field_ids', []) or [])
                geom['phase_field_polygons'] = [field_payload_map[fid].get('polygon_coords') for fid in field_ids if fid in field_payload_map]
            if phase_houses:
                note = f"Presentasjonshus: {len(phase_houses)} bygg — " + ', '.join(h.get('house_id', '') for h in phase_houses[:8])
                if note not in notes:
                    notes.append(note)
        try:
            setattr(res, 'geometry', geom)
            setattr(res, 'notes', notes)
        except Exception:
            pass
    return results


# =====================================================================
# V8 PATCH — reelle sol-/skyggetall, robust MUA-fallback og roligere
# presentasjonshus for UI/PDF.
# =====================================================================
try:
    from shapely.geometry import Polygon, Point
    from shapely.ops import unary_union
    from shapely import affinity
except Exception:
    Polygon = Point = None  # type: ignore
    unary_union = None  # type: ignore
    affinity = None  # type: ignore
import math

_ORIG_V8_RUN_MASTERPLAN = run_masterplan_from_site_inputs
_ORIG_V8_MASTERPLAN_TO_OPTION_RESULTS = masterplan_to_option_results


def run_masterplan_from_site_inputs(site: Any, geodata_context: Dict[str, Any], phasing_config: PhasingConfig,
                                    target_bra_m2: float, include_barnehage: bool = False,
                                    include_naering: bool = False, byggesone: str = '2'):
    mp, err = _ORIG_V8_RUN_MASTERPLAN(site, geodata_context, phasing_config, target_bra_m2, include_barnehage, include_naering, byggesone)
    if mp is not None:
        try:
            mp.site_inputs = dict(getattr(mp, 'site_inputs', {}) or {})
            mp.site_inputs['target_bra_m2'] = float(target_bra_m2)
            mp.site_inputs['avg_unit_bra'] = float(getattr(site, 'avg_unit_bra', 55.0) or 55.0)
        except Exception:
            pass
    return mp, err


def _rings_to_polys_v8(groups_like: Any) -> List[Any]:
    polys: List[Any] = []
    if Polygon is None:
        return polys
    if not groups_like:
        return polys
    # groups_like may be coords_groups or list of dicts containing coords
    if isinstance(groups_like, list) and groups_like and isinstance(groups_like[0], dict):
        iterable = [g.get('coords') or [] for g in groups_like]
    else:
        iterable = [groups_like]
    for groups in iterable:
        try:
            if groups and isinstance(groups[0], list) and groups[0] and isinstance(groups[0][0], (int, float)):
                groups = [groups]
        except Exception:
            pass
        if not isinstance(groups, list):
            continue
        for ring in groups:
            try:
                if len(ring) >= 3:
                    poly = Polygon([(float(x), float(y)) for x, y in ring]).buffer(0)
                    if not poly.is_empty and float(poly.area) > 1.0:
                        polys.append(poly)
            except Exception:
                continue
    return polys


def _sample_points_v8(poly, spacing_m: float = 10.0) -> List[Any]:
    pts: List[Any] = []
    if poly is None or getattr(poly, 'is_empty', True) or Point is None:
        return pts
    minx, miny, maxx, maxy = poly.bounds
    x = minx + spacing_m / 2.0
    while x <= maxx:
        y = miny + spacing_m / 2.0
        while y <= maxy:
            p = Point(x, y)
            if poly.covers(p):
                pts.append(p)
            y += spacing_m
        x += spacing_m
    if not pts:
        try:
            pts = [poly.representative_point()]
        except Exception:
            pts = []
    return pts


def _day_angle_v8(day_of_year: int) -> float:
    return 2 * math.pi * (day_of_year - 81) / 365.0


def _solar_declination_deg_v8(day_of_year: int) -> float:
    return 23.45 * math.sin(_day_angle_v8(day_of_year))


def _solar_altitude_deg_v8(latitude_deg: float, day_of_year: int, solar_hour: float) -> float:
    phi = math.radians(latitude_deg)
    delta = math.radians(_solar_declination_deg_v8(day_of_year))
    h = math.radians(15.0 * (solar_hour - 12.0))
    sin_alt = math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.cos(h)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))


def _solar_azimuth_deg_v8(latitude_deg: float, day_of_year: int, solar_hour: float) -> float:
    phi = math.radians(latitude_deg)
    delta = math.radians(_solar_declination_deg_v8(day_of_year))
    h = math.radians(15.0 * (solar_hour - 12.0))
    alt = math.radians(max(_solar_altitude_deg_v8(latitude_deg, day_of_year, solar_hour), 0.1))
    cos_az = (math.sin(delta) - math.sin(phi) * math.sin(alt)) / max(math.cos(phi) * math.cos(alt), 1e-6)
    cos_az = max(-1.0, min(1.0, cos_az))
    az = math.degrees(math.acos(cos_az))
    return 360.0 - az if solar_hour > 12.0 else az


def _build_shadow_poly_v8(poly, height_m: float, sun_azimuth_deg: float, sun_altitude_deg: float):
    if poly is None or getattr(poly, 'is_empty', True) or affinity is None or sun_altitude_deg <= 0.5:
        return None
    length = float(height_m) / max(math.tan(math.radians(sun_altitude_deg)), 0.02)
    az = math.radians((sun_azimuth_deg + 180.0) % 360.0)
    dx = math.sin(az) * length
    dy = math.cos(az) * length
    translated = affinity.translate(poly, xoff=dx, yoff=dy)
    try:
        return unary_union([poly, translated]).convex_hull.buffer(0)
    except Exception:
        return translated.buffer(0)


def _evaluate_option_solar_v8(site: Any, option: Any, geodata_context: Dict[str, Any]) -> Dict[str, float]:
    if Polygon is None or unary_union is None:
        return {}
    geom = getattr(option, 'geometry', {}) or {}
    eval_groups = geom.get('phase_field_polygons') or geom.get('buildable_polygon_coords') or geom.get('site_polygon_coords') or []
    eval_polys = _rings_to_polys_v8(eval_groups)
    eval_poly = unary_union(eval_polys).buffer(0) if eval_polys else geodata_context.get('buildable_polygon') or geodata_context.get('site_polygon')
    if eval_poly is None or getattr(eval_poly, 'is_empty', True):
        return {}
    building_polys: List[Any] = []
    building_parts = list(geom.get('massing_parts', []) or [])
    heights: List[float] = []
    if building_parts:
        for part in building_parts:
            polys = _rings_to_polys_v8(part.get('coords') or [])
            for p in polys:
                building_polys.append(p)
                heights.append(float(part.get('height_m', getattr(option, 'building_height_m', 0.0)) or 0.0))
    elif geom.get('footprint_polygon_coords'):
        for p in _rings_to_polys_v8(geom.get('footprint_polygon_coords') or []):
            building_polys.append(p)
            heights.append(float(getattr(option, 'building_height_m', 0.0) or 0.0))
    if not building_polys:
        return {}
    footprint = unary_union(building_polys).buffer(0)
    try:
        open_space = eval_poly.difference(footprint).buffer(0)
    except Exception:
        open_space = eval_poly.buffer(0)
    if getattr(open_space, 'is_empty', True):
        open_space = eval_poly
    spacing = max(6.0, min(14.0, math.sqrt(max(float(getattr(open_space, 'area', 1.0) or 1.0), 1.0) / 80.0)))
    sample_points = _sample_points_v8(open_space, spacing_m=spacing)
    if not sample_points:
        return {}
    latitude = float(getattr(site, 'latitude_deg', 63.42) or 63.42)
    north_rot = float(getattr(site, 'north_rotation_deg', 0.0) or 0.0)
    neighbors = list(geodata_context.get('neighbors', []) or [])

    def sunlit_fraction(day_of_year: int, solar_hour: float) -> float:
        alt = _solar_altitude_deg_v8(latitude, day_of_year, solar_hour)
        if alt <= 0.5:
            return 0.0
        az = (_solar_azimuth_deg_v8(latitude, day_of_year, solar_hour) - north_rot) % 360.0
        shadow_polys = []
        for poly, h in zip(building_polys, heights):
            sh = _build_shadow_poly_v8(poly, h, az, alt)
            if sh is not None and not getattr(sh, 'is_empty', True):
                shadow_polys.append(sh)
        for nb in neighbors:
            sh = _build_shadow_poly_v8(nb.get('polygon'), float(nb.get('height_m', 9.0) or 9.0), az, alt)
            if sh is not None and not getattr(sh, 'is_empty', True):
                shadow_polys.append(sh)
        if not shadow_polys:
            return 1.0
        shadow_union = unary_union(shadow_polys).buffer(0)
        sunlit = 0
        for point in sample_points:
            if not shadow_union.covers(point):
                sunlit += 1
        return float(sunlit / max(1, len(sample_points)))

    equinox_hours = [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
    winter_hours = [10.0, 11.0, 12.0, 13.0, 14.0]
    eq_fracs = [sunlit_fraction(80, hour) for hour in equinox_hours]
    wi_fracs = [sunlit_fraction(355, hour) for hour in winter_hours]
    mean_eq = sum(eq_fracs) / max(len(eq_fracs), 1)
    winter_noon = sunlit_fraction(355, 12.0)
    eq_noon = sunlit_fraction(80, 12.0)
    typology_bonus = {'Punkthus': 0.06, 'Lamell': 0.04, 'Tun': -0.02, 'Rekke': 0.05}.get(str(getattr(option, 'typology', '') or ''), 0.0)
    neighbor_penalty = min(0.12, 0.012 * len(neighbors))
    solar_score = 100.0 * max(0.18, min(1.0, 0.54 * mean_eq + 0.26 * winter_noon + 0.14 * eq_noon + typology_bonus - neighbor_penalty))
    max_h = max(heights or [float(getattr(option, 'building_height_m', 0.0) or 0.0)])
    winter_alt = _solar_altitude_deg_v8(latitude, 355, 12.0)
    summer_alt = _solar_altitude_deg_v8(latitude, 172, 15.0)
    eq_alt = _solar_altitude_deg_v8(latitude, 80, 12.0)
    return {
        'solar_score': round(max(18.0, min(100.0, solar_score)), 1),
        'estimated_equinox_sun_hours': round(sum(eq_fracs), 2),
        'estimated_winter_sun_hours': round(sum(wi_fracs), 2),
        'sunlit_open_space_pct': round(mean_eq * 100.0, 1),
        'winter_noon_shadow_m': round(float(max_h) / max(math.tan(math.radians(max(winter_alt, 1.0))), 0.02), 1),
        'equinox_noon_shadow_m': round(float(max_h) / max(math.tan(math.radians(max(eq_alt, 1.0))), 0.02), 1),
        'summer_afternoon_shadow_m': round(float(max_h) / max(math.tan(math.radians(max(summer_alt, 1.0))), 0.02), 1),
    }


def _presentation_buildings_v7(masterplan: Masterplan) -> List[Dict[str, Any]]:
    """V8: grupper roligere etter delfelt + hovedrolle, ikke mange små vinkelbøtter."""
    phase_by_vid: Dict[str, int] = {}
    for p in getattr(masterplan, 'building_phases', []) or []:
        for vid in getattr(p, 'volume_ids', []) or []:
            phase_by_vid[vid] = int(getattr(p, 'phase_number', 0) or 0)
    field_polys = {f.field_id: getattr(f, 'polygon', None) for f in getattr(masterplan, 'development_fields', []) or []}

    def role_of(v: Volume) -> str:
        typ = str(getattr(v, 'typology', '') or '')
        prog = str(getattr(v, 'program', '') or '')
        if 'barnehage' in prog:
            return 'barnehage'
        if typ in {'Karré', 'HalvåpenKarré'}:
            return 'kvartal'
        if typ in {'Punkthus', 'Tårn'}:
            return 'punkt'
        if typ in {'Rekke'}:
            return 'rekke'
        return 'lamell'

    buckets: Dict[Tuple[int, str, str], List[Volume]] = {}
    for v in getattr(masterplan, 'volumes', []) or []:
        if getattr(v, 'polygon', None) is None:
            continue
        phase = int(phase_by_vid.get(getattr(v, 'volume_id', ''), getattr(v, 'assigned_phase', 0) or 0))
        field_id = str(getattr(v, 'field_id', '') or '')
        buckets.setdefault((phase, field_id, role_of(v)), []).append(v)

    houses: List[Dict[str, Any]] = []
    for (phase, field_id, role), vols in buckets.items():
        field_poly = field_polys.get(field_id)
        comps = _component_groups_v7(vols, gap_m=9.0)
        for comp in comps:
            typology = str(getattr(comp[0], 'typology', '') or '') if comp else ''
            poly = _clean_presentation_polygon_v7([v.polygon for v in comp], typology, field_poly=field_poly)
            if poly is None or getattr(poly, 'is_empty', True):
                continue
            bra = sum(float(getattr(v, 'bra_m2', 0.0) or 0.0) for v in comp)
            footprint = float(getattr(poly, 'area', 0.0) or 0.0)
            floors_vals = [int(getattr(v, 'floors', 0) or 0) for v in comp]
            floors = max(floors_vals or [0])
            height = max(float(getattr(v, 'height_m', 0.0) or 0.0) for v in comp)
            cx = float(getattr(poly.centroid, 'x', 0.0) or 0.0)
            cy = float(getattr(poly.centroid, 'y', 0.0) or 0.0)
            field_name = next((str(getattr(v, 'field_name', '') or '') for v in comp if getattr(v, 'field_name', '')), '')
            internal_name = ', '.join([str(getattr(v, 'internal_name', '') or getattr(v, 'volume_id', '') or '') for v in comp[:4]])
            program = next((str(getattr(v, 'program', '') or '') for v in comp if getattr(v, 'program', '')), 'bolig')
            houses.append({
                'phase_number': int(phase),
                'field_id': field_id,
                'field_name': field_name,
                'typology': typology,
                'program': program,
                'floors': int(floors),
                'height_m': round(height, 1),
                'footprint_m2': round(footprint, 1),
                'bra_m2': round(bra, 1),
                'units_estimate': int(sum(int(getattr(v, 'units_estimate', 0) or 0) for v in comp)),
                'cx': round(cx, 1),
                'cy': round(cy, 1),
                'internal_name': internal_name,
                'source_volume_ids': [getattr(v, 'volume_id', '') for v in comp],
                'polygon': poly,
                'coords': _polygon_to_coords_groups(poly),
            })

    houses.sort(key=lambda r: (-float(r.get('cy', 0.0) or 0.0), float(r.get('cx', 0.0) or 0.0), str(r.get('field_name', '') or '')))
    for idx, row in enumerate(houses):
        row['house_id'] = f"HUS {chr(65 + (idx % 26))}" if idx < 26 else f"HUS {chr(65 + (idx // 26) - 1)}{chr(65 + (idx % 26))}"
        role = 'Lamell'
        typ = str(row.get('typology', '') or '')
        if typ in {'Punkthus', 'Tårn'}:
            role = 'Punkthus'
        elif typ in {'Karré', 'HalvåpenKarré'}:
            role = 'Kvartalhus'
        elif typ == 'Rekke':
            role = 'Rekkehus'
        elif 'barnehage' in str(row.get('program', '') or ''):
            role = 'Barnehage'
        field_name = str(row.get('field_name', '') or '').strip()
        row['name'] = f"{field_name} – {role}".strip(' –') if field_name else role
    return houses


def masterplan_to_option_results(masterplan: Masterplan, site: Any, geodata_context: Dict[str, Any], OptionResult_cls: Any) -> List[Any]:
    results = _ORIG_V8_MASTERPLAN_TO_OPTION_RESULTS(masterplan, site, geodata_context, OptionResult_cls)
    target_bra = float(getattr(getattr(masterplan, 'site_inputs', {}), 'get', lambda _k, _d=None: _d)('target_bra_m2', None) or (getattr(masterplan, 'site_inputs', {}) or {}).get('target_bra_m2', 0.0) or 0.0)
    if target_bra <= 0:
        target_bra = float(getattr(getattr(masterplan, 'program', None), 'total_bra', 0.0) or 0.0)
    for res in results:
        try:
            metrics = _evaluate_option_solar_v8(site, res, geodata_context)
            for key, value in metrics.items():
                setattr(res, key, value)
        except Exception:
            pass
        try:
            if bool((getattr(res, 'geometry', {}) or {}).get('is_total_plan')) and target_bra > 0:
                setattr(res, 'target_fit_pct', round(float(getattr(res, 'saleable_area_m2', 0.0) or 0.0) / max(target_bra, 1.0) * 100.0, 1))
        except Exception:
            pass
        try:
            geom = getattr(res, 'geometry', {}) or {}
            if bool(geom.get('is_total_plan')):
                summary = _mua_summary_fallback_v8(site, res, dict(geom.get('mua_summary', {}) or {}), list(geom.get('outdoor_zones', []) or []))
                geom['mua_summary'] = summary
                setattr(res, 'geometry', geom)
        except Exception:
            pass
    return results
