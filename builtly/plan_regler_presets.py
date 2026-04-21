
from __future__ import annotations

from .masterplan_types import PlanRegler


TRONDHEIM_KPA_2022_SONE_2 = PlanRegler(
    max_bra_pct=100.0,
    max_bya_pct=35.0,
    max_floors=7,
    max_height_m=24.0,
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
    max_bya_pct=35.0,
    max_floors=6,
    max_height_m=21.0,
    mua_per_bolig_m2=35.0,
    mua_min_felles_pct=50.0,
    mua_min_bakke_pct=40.0,
    brann_avstand_m=8.0,
    source_name="Generisk TEK17 / norsk fallback",
)

NO_RULES = PlanRegler(source_name="Ingen preset / alle regler åpne")

PRESETS = {
    "TRONDHEIM_KPA_2022_SONE_2": TRONDHEIM_KPA_2022_SONE_2,
    "GENERISK_TEK17_NORGE": GENERISK_TEK17_NORGE,
    "NO_RULES": NO_RULES,
}
