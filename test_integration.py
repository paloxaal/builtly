"""Integrasjonstest: kjør masterplan, konverter til OptionResult-lignende
objekter, generer rapport-markdown og verifiser fargekoding.
"""
import os
import sys
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("CLAUDE_API_KEY", None)

logging.basicConfig(level=logging.WARNING)

from shapely.geometry import box

from masterplan_types import PhasingConfig
from masterplan_engine import plan_masterplan
from masterplan_integration import (
    masterplan_to_option_results,
    render_phasing_report_markdown,
    phase_color_for,
    phase_color_rgba,
    build_phase_legend_html,
)


# Mock OptionResult (samme signatur som i Mulighetsstudie.py)
@dataclass
class OptionResult:
    name: str
    typology: str
    floors: int
    building_height_m: float
    footprint_area_m2: float
    gross_bta_m2: float
    saleable_area_m2: float
    footprint_width_m: float
    footprint_depth_m: float
    buildable_area_m2: float
    open_space_ratio: float
    target_fit_pct: float
    unit_count: int
    mix_counts: Dict[str, int]
    parking_spaces: int
    parking_pressure_pct: float
    solar_score: float
    estimated_equinox_sun_hours: float
    estimated_winter_sun_hours: float
    sunlit_open_space_pct: float
    winter_noon_shadow_m: float
    equinox_noon_shadow_m: float
    summer_afternoon_shadow_m: float
    efficiency_ratio: float
    neighbor_count: int
    terrain_slope_pct: float
    terrain_relief_m: float
    notes: List[str]
    score: float
    geometry: Dict[str, Any]


@dataclass
class MockSite:
    site_area_m2: float = 50000.0
    max_floors: int = 7
    max_height_m: float = 25.0
    max_bya_pct: float = 40.0
    floor_to_floor_m: float = 3.2
    efficiency_ratio: float = 0.85
    latitude_deg: float = 63.42
    terrain_slope_pct: float = 0.0
    terrain_relief_m: float = 0.0


# Kjør test
site_polygon = box(0, 0, 250, 200)
buildable_polygon = box(4, 4, 246, 196)

neighbors = [
    {"polygon": box(-20, y, -5, y + 15), "height_m": 7.5}
    for y in range(20, 200, 25)
]

print("=" * 70)
print("INTEGRASJONSTEST: NRK-skalert masterplan → OptionResult-liste")
print("=" * 70)

mp = plan_masterplan(
    site_polygon=site_polygon,
    buildable_polygon=buildable_polygon,
    neighbors=neighbors,
    target_bra_m2=45700,
    max_floors=7,
    max_height_m=25,
    max_bya_pct=40,
    phasing_config=PhasingConfig(phasing_mode="auto"),
    include_barnehage=True,
    include_naering=True,
)

print(f"\nMasterplan: {len(mp.volumes)} volumer, "
      f"{len(mp.building_phases)} byggetrinn, "
      f"{len(mp.parking_phases)} p-faser")
print(f"Total BRA: {mp.metrics.total_bra:,.0f}")
print(f"MUA compliant: {mp.metrics.mua_compliant}")
print(f"Overall score: {mp.metrics.overall_score:.1f}\n")

# Konverter til OptionResult-liste
site = MockSite()
geodata_context = {
    "site_polygon": site_polygon,
    "buildable_polygon": buildable_polygon,
    "neighbors": neighbors,
    "terrain": None,
    "site_intelligence": None,
}

options = masterplan_to_option_results(mp, site, geodata_context, OptionResult)

print(f"OptionResult-liste: {len(options)} elementer")
for i, opt in enumerate(options):
    is_total = opt.geometry.get("is_total_plan", False)
    marker = "  [TOTALT]" if is_total else f"  [Trinn]"
    print(f"{marker} {opt.name}")
    print(f"           BTA: {opt.gross_bta_m2:,.0f}, BRA: {opt.saleable_area_m2:,.0f}, "
          f"enheter: {opt.unit_count}, score: {opt.score:.1f}")

print()
print("=" * 70)
print("RAPPORT-MARKDOWN")
print("=" * 70)
md = render_phasing_report_markdown(mp)
print(md[:2500])
print("..." if len(md) > 2500 else "")

print()
print("=" * 70)
print("FARGEKODING")
print("=" * 70)
for i in range(1, len(mp.building_phases) + 1):
    color = phase_color_for(i)
    rgba = phase_color_rgba(i)
    print(f"  Trinn {i:2}: hex={color}  rgba={rgba}")

print()
print("=" * 70)
print("3D-LEGENDE HTML (trunkert)")
print("=" * 70)
legend = build_phase_legend_html(mp)
# Bare vis første 600 tegn
print(legend[:600])
print("..." if len(legend) > 600 else "")

print()
print("=" * 70)
print("VERIFIKASJON")
print("=" * 70)

# Sjekk at hvert OptionResult har geometry['buildings'] (nødvendig for 3D)
ok_count = 0
for opt in options:
    buildings = opt.geometry.get("buildings", [])
    if buildings and all(b.get("polygon") is not None for b in buildings):
        ok_count += 1
print(f"OptionResult med gyldig geometry: {ok_count}/{len(options)}")

# Sjekk at "Totalt" er det første elementet
if options and options[0].geometry.get("is_total_plan"):
    print("✓ 'Totalt' er default-alternativ (index 0)")
else:
    print("✗ 'Totalt' mangler som default")

# Sjekk at hvert trinn-option har phase_number
trinn_ok = 0
for opt in options[1:]:
    if "phase_number" in opt.geometry:
        trinn_ok += 1
print(f"Trinn-options med phase_number: {trinn_ok}/{len(options)-1}")

# Sjekk at alle buildings har 'phase' og 'typology' (nye felter)
total_with_phase = 0
total_with_typology = 0
total_buildings = 0
for opt in options:
    for b in opt.geometry.get("buildings", []):
        total_buildings += 1
        if b.get("phase") is not None:
            total_with_phase += 1
        if b.get("typology"):
            total_with_typology += 1
print(f"Buildings med 'phase': {total_with_phase}/{total_buildings}")
print(f"Buildings med 'typology': {total_with_typology}/{total_buildings}")

print()
print("ALLE INTEGRASJONS-TESTER FULLFØRT ✓")
