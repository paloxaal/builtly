from __future__ import annotations

"""Preset rule sets for Builtly v8.

These are data presets, not hardcoded behaviour in the engine.
"""

from .masterplan_types import PlanRegler

TRONDHEIM_KPA_2022_SONE_2 = PlanRegler(
    max_bra_pct=100.0,
    mua_per_bolig_m2=40.0,
    mua_min_felles_pct=50.0,
    mua_min_bakke_pct=50.0,
    parkering_per_100m2_bra=(0.2, 0.8),
    parkering_sykkel_per_bolig=2.0,
    brann_avstand_m=8.0,
    source_name="Trondheim KPA 2022-34 sone 2",
    source_url="https://www.trondheim.kommune.no/kpa",
)

GENERISK_TEK17_NORGE = PlanRegler(
    brann_avstand_m=8.0,
    source_name="Generisk TEK17 Norge",
)
