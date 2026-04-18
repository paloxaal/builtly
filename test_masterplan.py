"""End-to-end test av masterplan-motoren uten Claude-API.

Bruker kun Python-fallback-paths for å verifisere at motoren
henger sammen og produserer fornuftig output.
"""
import logging
import os
import sys

# Sørg for at ai_site_planner ikke kalles med ekte API
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("CLAUDE_API_KEY", None)

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")

from shapely.geometry import Polygon, box

from masterplan_types import PhasingConfig
from masterplan_engine import plan_masterplan


# ─── Test case: syntetisk NRK-lignende tomt ───
# Tomt ca 160m x 160m kvadrat (≈ 25 500 m² som i Builtly-rapporten)
site_polygon = box(0, 0, 160, 160)
# Byggefelt etter 4m setback
buildable_polygon = box(4, 4, 156, 156)

# Syntetiske naboer — småhus vest, høyere bebyggelse øst
neighbors = []
# Småhus i vest
for y in range(20, 160, 25):
    neighbors.append({
        "polygon": box(-20, y, -5, y + 15),
        "height_m": 7.5,  # 2-etg enebolig
    })
# Høyere bygg øst
for y in range(30, 140, 40):
    neighbors.append({
        "polygon": box(165, y, 185, y + 25),
        "height_m": 15,
    })

print("=" * 70)
print("TEST 1: Lite prosjekt — 4000 m² BRA, 1 fase forventet")
print("=" * 70)

mp_small = plan_masterplan(
    site_polygon=site_polygon,
    buildable_polygon=buildable_polygon,
    neighbors=neighbors,
    target_bra_m2=4000,
    max_floors=5,
    max_height_m=16,
    max_bya_pct=35,
    phasing_config=PhasingConfig(phasing_mode="auto"),
)
print(f"Volumer: {len(mp_small.volumes)}")
print(f"Byggefaser: {len(mp_small.building_phases)}")
print(f"P-faser: {len(mp_small.parking_phases)}")
print(f"Total BRA: {mp_small.metrics.total_bra:.0f}")
print(f"BYA: {mp_small.metrics.bya_percent:.1f}%")
print(f"Typologi-soner: {len(mp_small.typology_zones)}")
print(f"Uterom-soner: {len(mp_small.outdoor_system.zones)}")
print(f"Standalone-score: {mp_small.metrics.standalone_habitability_score:.1f}")
print(f"Overall-score: {mp_small.metrics.overall_score:.1f}")
for p in mp_small.building_phases:
    print(f"  Trinn {p.phase_number}: {p.actual_bra:.0f} m² BRA, {p.units_estimate} enheter, "
          f"habitable={p.standalone_habitable}")
print()

print("=" * 70)
print("TEST 2: NRK-skalaen — 45 700 m² BRA, forventer ~11 faser")
print("=" * 70)

# Større tomt for dette scenarioet
big_site = box(0, 0, 250, 200)
big_buildable = box(4, 4, 246, 196)

mp_big = plan_masterplan(
    site_polygon=big_site,
    buildable_polygon=big_buildable,
    neighbors=neighbors,
    target_bra_m2=45700,
    max_floors=7,
    max_height_m=25,
    max_bya_pct=40,
    phasing_config=PhasingConfig(phasing_mode="auto"),
    include_barnehage=True,
    include_naering=True,
)
print(f"Volumer: {len(mp_big.volumes)}")
print(f"Byggefaser: {len(mp_big.building_phases)}")
print(f"P-faser: {len(mp_big.parking_phases)}")
print(f"Total BRA: {mp_big.metrics.total_bra:.0f}")
print(f"Total BTA: {mp_big.metrics.total_bta:.0f}")
print(f"BYA: {mp_big.metrics.bya_percent:.1f}%")
print(f"Enheter: {mp_big.metrics.units_total}")
print(f"MUA tilgjengelig: {mp_big.metrics.mua_total_m2:.0f} (krav: {mp_big.metrics.mua_required_m2:.0f})")
print(f"MUA compliant: {mp_big.metrics.mua_compliant}")
print(f"Snitt BRA per fase: {mp_big.metrics.avg_phase_bra:.0f} "
      f"(min {mp_big.metrics.min_phase_bra:.0f}, max {mp_big.metrics.max_phase_bra:.0f})")
print(f"Standalone-score: {mp_big.metrics.standalone_habitability_score:.1f}")
print(f"Overall-score: {mp_big.metrics.overall_score:.1f}")
print()
print("Typologi-soner:")
for z in mp_big.typology_zones:
    print(f"  {z.zone_id}: {z.typology} ({z.floors_min}-{z.floors_max} et), "
          f"{z.polygon.area:.0f} m² — {z.rationale}")
print()
print("Byggefaser:")
for p in mp_big.building_phases[:15]:
    print(f"  Trinn {p.phase_number}: {p.label[:60]}")
    print(f"    BRA: {p.actual_bra:.0f} m², enheter: {p.units_estimate}, "
          f"programmer: {p.programs_included}")
    print(f"    Uterom: {p.standalone_outdoor_m2:.0f} m², "
          f"parkering: P{p.parking_served_by}, "
          f"standalone: {p.standalone_habitable}")
    if p.standalone_issues:
        for issue in p.standalone_issues:
            print(f"    ⚠ {issue}")
print()
print("Parkeringsfaser:")
for pp in mp_big.parking_phases:
    print(f"  P{pp.phase_number}: {pp.num_spaces} plasser, "
          f"{len(pp.ramps)} rampe(r), betjener B{pp.serves_building_phases}")
print()
print("Uterom-system:")
for oz in mp_big.outdoor_system.zones:
    loc = "tak" if not oz.on_ground else "bakke"
    felles = "felles" if oz.is_felles else "privat"
    print(f"  {oz.zone_id}: {oz.kind} ({loc}/{felles}) — {oz.area_m2:.0f} m²")
print(f"Sum MUA bakke/felles: {mp_big.outdoor_system.mua_on_ground_felles():.0f} m²")
print(f"Sum MUA tak: {mp_big.outdoor_system.mua_on_roof():.0f} m²")
print()
print("Advarsler:")
for w in mp_big.warnings:
    print(f"  ⚠ {w}")
print()

print("=" * 70)
print("TEST 3: Manuelt valg — 45 700 m² i 7 faser")
print("=" * 70)

mp_manual = plan_masterplan(
    site_polygon=big_site,
    buildable_polygon=big_buildable,
    neighbors=neighbors,
    target_bra_m2=45700,
    max_floors=7,
    max_height_m=25,
    max_bya_pct=40,
    phasing_config=PhasingConfig(phasing_mode="manual", manual_phase_count=7),
    include_barnehage=True,
)
print(f"Byggefaser: {len(mp_manual.building_phases)}")
print(f"Snitt BRA per fase: {mp_manual.metrics.avg_phase_bra:.0f}")
for p in mp_manual.building_phases:
    print(f"  Trinn {p.phase_number}: {p.actual_bra:.0f} m² BRA")
print()

print("=" * 70)
print("TEST 4: Single garage mode — én p-kjeller under alt")
print("=" * 70)

mp_single_p = plan_masterplan(
    site_polygon=big_site,
    buildable_polygon=big_buildable,
    neighbors=neighbors,
    target_bra_m2=20000,
    max_floors=5,
    max_height_m=18,
    max_bya_pct=35,
    phasing_config=PhasingConfig(
        phasing_mode="auto",
        parking_mode="single_garage",
    ),
)
print(f"Byggefaser: {len(mp_single_p.building_phases)}")
print(f"P-faser: {len(mp_single_p.parking_phases)}")
for pp in mp_single_p.parking_phases:
    print(f"  P{pp.phase_number}: {pp.num_spaces} plasser, "
          f"{len(pp.ramps)} rampe(r), betjener B{pp.serves_building_phases}")
    print(f"    Notes: {pp.notes}")
print()

print("=" * 70)
print("TEST 5: Legacy-kompatibilitet — to_legacy_result()")
print("=" * 70)

legacy = mp_big.to_legacy_result()
print(f"Buildings (legacy-format): {len(legacy['buildings'])}")
print(f"Footprint type: {type(legacy['footprint']).__name__}")
print(f"Har nye felter:")
print(f"  masterplan: {type(legacy.get('masterplan')).__name__}")
print(f"  building_phases: {len(legacy.get('building_phases', []))}")
print(f"  parking_phases: {len(legacy.get('parking_phases', []))}")
print(f"Første bygg har extra felter:")
if legacy["buildings"]:
    b = legacy["buildings"][0]
    print(f"  phase: {b.get('phase')}")
    print(f"  typology: {b.get('typology')}")
    print(f"  program: {b.get('program')}")
    print(f"  zone_id: {b.get('zone_id')}")

print()
print("ALLE TESTER FULLFØRT")
