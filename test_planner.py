"""
test_planner_v2.py — Unit tests for ai_site_planner_v2.py

Tests Pass 2 (deterministic placement) and Pass 4 (validation) 
without requiring Anthropic API calls.
"""

import json
import math
import sys
sys.path.insert(0, "/home/claude")

from shapely.geometry import Polygon, box
from ai_site_planner_v2 import (
    BuildingFootprint,
    SiteConcept,
    PlacementResult,
    pass2_deterministic_placement,
    pass4_validate_and_fix,
    _compute_coverage_score,
    _find_placement,
    _push_building_inward,
    _resolve_overlaps,
    _enforce_spacing,
    TYPOLOGY_LIMITS,
    MIN_BUILDING_SPACING,
    MIN_BOUNDARY_SETBACK,
)

import logging
logging.basicConfig(level=logging.INFO)

PASS = "✅"
FAIL = "❌"
results = []


def test(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, condition))
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))
    return condition


# ──────────────────────────────────────────────
# Test plots
# ──────────────────────────────────────────────

# Rectangular plot: 100m x 80m = 8000 m²
RECT_PLOT = box(0, 0, 100, 80)

# Large plot: 200m x 150m = 30000 m²
LARGE_PLOT = box(0, 0, 200, 150)

# L-shaped plot
L_PLOT = Polygon([
    (0, 0), (120, 0), (120, 60), (60, 60), (60, 100), (0, 100), (0, 0)
])

# Narrow plot: 200m x 30m = 6000 m²
NARROW_PLOT = box(0, 0, 200, 30)

# Standard regulations
STD_REGS = {
    "max_bya_pct": 40,
    "max_floors": 5,
    "allowed_typologies": ["blokk", "lamell", "punkthus", "rekkehus"],
    "min_uteoppholdsareal_pct": 25,
    "parking_per_unit": 1.0,
    "road_edges": [0],
}


def make_concept(buildings_spec: list[dict], strategy: str = "test") -> SiteConcept:
    """Helper to create a SiteConcept from a building spec list."""
    return SiteConcept(
        strategy=strategy,
        zones=[],
        buildings=buildings_spec,
        total_target_bya_pct=35,
        max_floors=5,
    )


# ──────────────────────────────────────────────
# TEST 1: Basic placement on rectangular plot
# ──────────────────────────────────────────────
print("\n═══ TEST 1: Basic rectangular plot placement ═══")

concept1 = make_concept([
    {"typology": "blokk", "count": 2, "target_width": 30, "target_depth": 16, "target_floors": 4},
    {"typology": "punkthus", "count": 2, "target_width": 18, "target_depth": 18, "target_floors": 5},
])

buildings1 = pass2_deterministic_placement(RECT_PLOT, concept1, STD_REGS)

test("Buildings placed", len(buildings1) > 0, f"{len(buildings1)} buildings")
test("At least 3 buildings", len(buildings1) >= 3, f"got {len(buildings1)}")

# Check all buildings within plot (with setback)
inset1 = RECT_PLOT.buffer(-MIN_BOUNDARY_SETBACK)
all_inside = all(inset1.contains(b.polygon) for b in buildings1)
test("All buildings within setback", all_inside)

# Check BYA
total_bya1 = sum(b.footprint_m2 for b in buildings1)
max_bya1 = RECT_PLOT.area * 0.40
test("BYA within limit", total_bya1 <= max_bya1, f"{total_bya1:.0f} / {max_bya1:.0f} m²")

# Check no overlaps
for i, b1 in enumerate(buildings1):
    for j, b2 in enumerate(buildings1):
        if j <= i:
            continue
        overlap = b1.polygon.intersection(b2.polygon).area
        test(f"No overlap {b1.id}↔{b2.id}", overlap < 1.0, f"{overlap:.1f} m²")

# Check spacing
for i, b1 in enumerate(buildings1):
    for j, b2 in enumerate(buildings1):
        if j <= i:
            continue
        dist = b1.polygon.distance(b2.polygon)
        test(f"Spacing {b1.id}↔{b2.id} ≥ {MIN_BUILDING_SPACING}m", 
             dist >= MIN_BUILDING_SPACING - 0.1, f"{dist:.1f}m")


# ──────────────────────────────────────────────
# TEST 2: Large plot — full coverage
# ──────────────────────────────────────────────
print("\n═══ TEST 2: Large plot coverage distribution ═══")

concept2 = make_concept([
    {"typology": "lamell", "count": 4, "target_width": 50, "target_depth": 14, "target_floors": 4},
    {"typology": "punkthus", "count": 3, "target_width": 20, "target_depth": 20, "target_floors": 6},
])

buildings2 = pass2_deterministic_placement(LARGE_PLOT, concept2, STD_REGS)
test("Buildings placed on large plot", len(buildings2) >= 4, f"{len(buildings2)} buildings")

coverage2 = _compute_coverage_score(buildings2, LARGE_PLOT)
test("Coverage score ≥ 0.4", coverage2 >= 0.4, f"score={coverage2:.2f}")
test("Coverage score ≥ 0.5 (good)", coverage2 >= 0.5, f"score={coverage2:.2f}")


# ──────────────────────────────────────────────
# TEST 3: L-shaped plot
# ──────────────────────────────────────────────
print("\n═══ TEST 3: L-shaped plot handling ═══")

concept3 = make_concept([
    {"typology": "blokk", "count": 3, "target_width": 25, "target_depth": 14, "target_floors": 4},
])

buildings3 = pass2_deterministic_placement(L_PLOT, concept3, STD_REGS)
test("Buildings placed on L-plot", len(buildings3) >= 2, f"{len(buildings3)} buildings")

inset3 = L_PLOT.buffer(-MIN_BOUNDARY_SETBACK)
all_inside3 = all(inset3.contains(b.polygon) for b in buildings3)
test("All buildings within L-plot setback", all_inside3)


# ──────────────────────────────────────────────
# TEST 4: Narrow plot
# ──────────────────────────────────────────────
print("\n═══ TEST 4: Narrow plot (200x30m) ═══")

concept4 = make_concept([
    {"typology": "rekkehus", "count": 3, "target_width": 40, "target_depth": 10, "target_floors": 2},
])

buildings4 = pass2_deterministic_placement(NARROW_PLOT, concept4, STD_REGS)
test("Buildings placed on narrow plot", len(buildings4) >= 1, f"{len(buildings4)} buildings")

inset4 = NARROW_PLOT.buffer(-MIN_BOUNDARY_SETBACK)
all_inside4 = all(inset4.contains(b.polygon) for b in buildings4)
test("All within narrow plot setback", all_inside4)


# ──────────────────────────────────────────────
# TEST 5: BYA enforcement
# ──────────────────────────────────────────────
print("\n═══ TEST 5: BYA enforcement ═══")

# Try to place way too many buildings on a small plot
concept5 = make_concept([
    {"typology": "blokk", "count": 10, "target_width": 30, "target_depth": 16, "target_floors": 5},
])

# Small plot: 60x50 = 3000 m², max BYA = 1200 m²
small_plot = box(0, 0, 60, 50)
buildings5 = pass2_deterministic_placement(small_plot, concept5, STD_REGS)
total_bya5 = sum(b.footprint_m2 for b in buildings5)
max_bya5 = small_plot.area * 0.40
test("BYA respected on overloaded plot", total_bya5 <= max_bya5 + 1, 
     f"{total_bya5:.0f} / {max_bya5:.0f} m²")


# ──────────────────────────────────────────────
# TEST 6: Typology dimension clamping
# ──────────────────────────────────────────────
print("\n═══ TEST 6: Dimension clamping ═══")

concept6 = make_concept([
    {"typology": "blokk", "count": 1, "target_width": 200, "target_depth": 50, "target_floors": 20},
])

buildings6 = pass2_deterministic_placement(LARGE_PLOT, concept6, STD_REGS)
if buildings6:
    b = buildings6[0]
    limits = TYPOLOGY_LIMITS["blokk"]
    test("Width clamped", b.width <= limits["w_max"], f"w={b.width}")
    test("Depth clamped", b.depth <= limits["d_max"], f"d={b.depth}")
    test("Floors clamped", b.floors <= limits["floors_max"], f"floors={b.floors}")


# ──────────────────────────────────────────────
# TEST 7: Pass 4 validation — overlap removal
# ──────────────────────────────────────────────
print("\n═══ TEST 7: Pass 4 overlap removal ═══")

# Create two overlapping buildings manually
overlap_buildings = [
    BuildingFootprint("B001", "blokk", 50, 40, 30, 16, 12, 4, 0, 16, 1632, "Blokk 1"),
    BuildingFootprint("B002", "blokk", 55, 42, 30, 16, 12, 4, 0, 16, 1632, "Blokk 2"),  # overlaps B001
    BuildingFootprint("B003", "punkthus", 90, 40, 18, 18, 15, 5, 0, 15, 1377, "Punkt 3"),
]

result7 = pass4_validate_and_fix(RECT_PLOT, overlap_buildings, STD_REGS)
test("Overlap resolved", len(result7.buildings) < 3, f"{len(result7.buildings)} remain")
test("No remaining overlaps",
     all(
         result7.buildings[i].polygon.intersection(result7.buildings[j].polygon).area < 1.0
         for i in range(len(result7.buildings))
         for j in range(i + 1, len(result7.buildings))
     ))


# ──────────────────────────────────────────────
# TEST 8: Pass 4 — push inward
# ──────────────────────────────────────────────
print("\n═══ TEST 8: Pass 4 push-inward ═══")

# Building partially outside
outside_building = BuildingFootprint(
    "B001", "blokk", 2, 40, 30, 16, 12, 4, 0, 16, 1632, "Outside"
)
inset8 = RECT_PLOT.buffer(-MIN_BOUNDARY_SETBACK)
test("Building initially outside", not inset8.contains(outside_building.polygon))

fixed = _push_building_inward(outside_building, inset8)
test("Push-inward succeeded", fixed is not None)
if fixed:
    test("Fixed building inside", inset8.contains(fixed.polygon))


# ──────────────────────────────────────────────
# TEST 9: Pass 4 — spacing enforcement
# ──────────────────────────────────────────────
print("\n═══ TEST 9: Pass 4 spacing enforcement ═══")

close_buildings = [
    BuildingFootprint("B001", "blokk", 40, 40, 20, 14, 12, 4, 0, 16, 952, "A"),
    BuildingFootprint("B002", "blokk", 65, 40, 20, 14, 12, 4, 0, 16, 952, "B"),  # 5m gap (< 8m)
    BuildingFootprint("B003", "punkthus", 90, 40, 18, 18, 15, 5, 0, 15, 1377, "C"),  # OK distance
]

warnings9 = []
fixed9 = _enforce_spacing(close_buildings, MIN_BUILDING_SPACING, warnings9)
test("Close building removed", len(fixed9) < 3, f"{len(fixed9)} remain")
test("Spacing warning issued", len(warnings9) > 0, f"{len(warnings9)} warnings")


# ──────────────────────────────────────────────
# TEST 10: Full pipeline (Pass 2 → 4, no API)
# ──────────────────────────────────────────────
print("\n═══ TEST 10: Full deterministic pipeline (Pass 2 → Pass 4) ═══")

concept10 = make_concept(
    buildings_spec=[
        {"typology": "lamell", "count": 3, "target_width": 45, "target_depth": 14, "target_floors": 4},
        {"typology": "punkthus", "count": 2, "target_width": 20, "target_depth": 20, "target_floors": 5},
        {"typology": "rekkehus", "count": 2, "target_width": 40, "target_depth": 10, "target_floors": 2},
    ],
    strategy="Mixed development with lameller along edges, punkthus as accents",
)

buildings10 = pass2_deterministic_placement(LARGE_PLOT, concept10, STD_REGS)
result10 = pass4_validate_and_fix(LARGE_PLOT, buildings10, STD_REGS)

test("Pipeline produces buildings", len(result10.buildings) >= 4, f"{len(result10.buildings)}")
test("BYA within limit", result10.bya_pct <= 40.0, f"{result10.bya_pct}%")
test("Coverage ≥ 0.3", result10.coverage_score >= 0.3, f"{result10.coverage_score}")
test("No validation errors", len(result10.validation_errors) == 0, 
     f"{len(result10.validation_errors)} errors")
test("Units calculated", result10.units_total > 0, f"{result10.units_total} units")
test("BRA calculated", result10.bra_total > 0, f"{result10.bra_total:.0f} m²")


# ──────────────────────────────────────────────
# TEST 11: Building polygon correctness
# ──────────────────────────────────────────────
print("\n═══ TEST 11: Building polygon geometry ═══")

b_test = BuildingFootprint("T1", "blokk", 50, 40, 30, 16, 12, 4, 0)
test("Footprint area correct", abs(b_test.footprint_m2 - 480) < 0.1, f"{b_test.footprint_m2}")
test("Polygon area matches", abs(b_test.polygon.area - 480) < 0.1, f"{b_test.polygon.area}")

# Rotated building should have same area
b_rot = BuildingFootprint("T2", "blokk", 50, 40, 30, 16, 12, 4, 45)
test("Rotated area preserved", abs(b_rot.polygon.area - 480) < 0.1, f"{b_rot.polygon.area}")


# ──────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────
print("\n" + "═" * 50)
passed = sum(1 for _, ok in results if ok)
total = len(results)
print(f"RESULTS: {passed}/{total} passed")
if passed == total:
    print("🎉 All tests passed!")
else:
    failed_tests = [name for name, ok in results if not ok]
    print(f"Failed: {', '.join(failed_tests)}")
print("═" * 50)
