from __future__ import annotations

"""Typologi-primitiver (uke 2 av arkitektkvalitet-løftet).

Dette modulet definerer de tre bygningstypologiene som Builtly-motoren
skal produsere, med ekte arkitektoniske dimensjoner fra Pål's
referanseprosjekter:

  LAMELL:    55-65m lang × 12-14m dyp
  PUNKTHUS:  20×20m, 10m avstand (brann-avstand)
  KARRÉ:     50m bunn × 25-30m sider × 12m arm-dybde (U/O-form)

KARRÉ-KRAV: Feltet må være minst 10 000 m² for å bære en karré-
struktur. På mindre felt vinner lamell og punkthus. Dette er en
hard regel som selector-funksjonen håndhever.

Hovedfunksjoner:
  - select_typology_for_field(...) — velger rett typologi
  - plan_karre_for_field(...)
  - plan_lamell_for_field(...)
  - plan_punkthus_for_field(...)
  - plan_typologi_for_field(...) — orkestrator, kaller selector + rett planner

Hvert plan-resultat inneholder bygg-footprints med lengde, dybde og
etasjer — slik at rendereren kan vise dimensjoner som label.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import math

from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from .masterplan_structure import MasterplanProfile


class TypologiKind(str, Enum):
    LAMELL = "LAMELL"
    PUNKTHUS = "PUNKTHUS"
    KARRE = "KARRE"


# ---------------------------------------------------------------------------
# Parameter-bibliotek — kalibrert mot Pål's referanser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LamellParameters:
    """Lameller: lange rettvinklede blokker.

    Fra Pål: optimal lengde 55-65m, optimal dybde 12-14m.
    """
    length_min_m: float = 45.0
    length_max_m: float = 65.0
    length_preferred_m: float = 58.0
    depth_min_m: float = 12.0
    depth_max_m: float = 14.0
    depth_preferred_m: float = 13.0
    # Avstand mellom parallelle lameller (sol + brann)
    gap_between_m: float = 20.0
    # Avstand mellom lameller i samme rad (endegavler)
    gap_end_to_end_m: float = 12.0
    # Setback fra felt-kant
    setback_m: float = 4.0
    floors_default_min: int = 4
    floors_default_max: int = 6


@dataclass(frozen=True)
class PunkthusParameters:
    """Punkthus: kvadratiske tårn.

    Fra Pål: 20×20m.
    """
    side_m: float = 20.0
    # Avstand mellom punkthus (brann + sol + innsyn)
    gap_m: float = 10.0
    setback_m: float = 6.0
    floors_default_min: int = 5
    floors_default_max: int = 8


@dataclass(frozen=True)
class KvartalParameters:
    """Karré: rektangulær U- eller O-form rundt gårdsrom.

    Fra Pål:
      - Bunn (lang side):  50m
      - Sider (kortsider): 25-30m
      - Bredde på bygg:    12m (arm-dybde)

    Dette gir en klassisk Oslo-karré-proporsjon (2:1) med gårdsrom
    i midten. Gårdsrom-dimensjon = 26m × 1-6m ved full O-form;
    derfor åpner vi ofte én kortside (U-form) for å få et brukbart
    gårdsrom i delfelt mindre enn 10 000 m². Over 10 000 m² bygger vi
    flere U/O-kvartaler i rutenett.

    KARRÉ-KRAV: Feltareal >= 10 000 m². Håndheves i selector.
    """
    # Ytre rammens dimensjoner
    length_m: float = 50.0             # lang side (bunn)
    depth_short_m: float = 28.0        # kort side (sider), 25-30
    depth_short_min_m: float = 25.0
    depth_short_max_m: float = 30.0
    # Arm-dybde (byggets tykkelse)
    arm_depth_m: float = 12.0
    # Gate mellom kvartaler
    gate_m: float = 10.0
    setback_m: float = 4.0
    floors_default_min: int = 4
    floors_default_max: int = 6
    # Feltareal-krav
    min_field_area_m2: float = 10_000.0
    # O vs U (åpen karré)
    prefer_open_side_for_single: bool = True  # enkelt-kvartal -> U-form


PROFILE_LAMELL: Dict[MasterplanProfile, LamellParameters] = {
    MasterplanProfile.FORSTAD: LamellParameters(
        length_min_m=45.0, length_max_m=65.0, length_preferred_m=58.0,
        depth_min_m=12.0, depth_max_m=14.0, depth_preferred_m=13.0,
        gap_between_m=20.0, gap_end_to_end_m=12.0,
        floors_default_min=4, floors_default_max=6,
    ),
    MasterplanProfile.URBAN: LamellParameters(
        length_min_m=50.0, length_max_m=70.0, length_preferred_m=62.0,
        depth_min_m=13.0, depth_max_m=15.0, depth_preferred_m=14.0,
        gap_between_m=22.0, gap_end_to_end_m=14.0,
        floors_default_min=5, floors_default_max=7,
    ),
}

PROFILE_PUNKTHUS: Dict[MasterplanProfile, PunkthusParameters] = {
    MasterplanProfile.FORSTAD: PunkthusParameters(
        side_m=20.0, gap_m=10.0,
        floors_default_min=5, floors_default_max=7,
    ),
    MasterplanProfile.URBAN: PunkthusParameters(
        side_m=22.0, gap_m=12.0,
        floors_default_min=6, floors_default_max=9,
    ),
}

PROFILE_KVARTAL: Dict[MasterplanProfile, KvartalParameters] = {
    MasterplanProfile.FORSTAD: KvartalParameters(
        length_m=50.0, depth_short_m=28.0,
        depth_short_min_m=25.0, depth_short_max_m=30.0,
        arm_depth_m=12.0, gate_m=10.0, setback_m=4.0,
        floors_default_min=4, floors_default_max=6,
        # Pål: karré kan fint gjøres på 2 610 m² hvis formen er riktig
        # (58×45m = ramme rundt 50×28m karré med 4m setback).
        # Areal-krav er sekundært; dimension-krav er det primære (se
        # _karre_dimensions_fit nedenfor).
        min_field_area_m2=2_400.0,
    ),
    MasterplanProfile.URBAN: KvartalParameters(
        length_m=55.0, depth_short_m=32.0,
        depth_short_min_m=28.0, depth_short_max_m=36.0,
        arm_depth_m=14.0, gate_m=14.0, setback_m=5.0,
        floors_default_min=5, floors_default_max=7,
        min_field_area_m2=2_800.0,
    ),
}


# ---------------------------------------------------------------------------
# Felles datatyper
# ---------------------------------------------------------------------------


@dataclass
class Bygning:
    """En enkel bygningsfootprint med dimensjoner og etasjer.

    Brukes av alle tre typologi-plannere. `length_m` og `depth_m` er
    aksejusterte mål (før eventuell rotasjon til feltets orientering).
    """
    bygg_id: str
    polygon: Polygon
    length_m: float             # lengste side
    depth_m: float              # korteste side
    floors: int
    typologi: TypologiKind
    role: str = "standard"      # "standard" | "ring" | "arm" | "tower"

    @property
    def footprint_m2(self) -> float:
        return float(self.polygon.area)

    @property
    def bra_m2(self) -> float:
        return self.footprint_m2 * self.floors


@dataclass
class Kvartal:
    """En karré-enhet: ytre ramme + gårdsrom."""
    kvartal_id: str
    outer_polygon: Polygon
    courtyard_polygon: Polygon
    open_side: Optional[str] = None     # "north"|"south"|"east"|"west"|None (None=O-form)
    orientation_deg: float = 0.0


@dataclass
class TypologiPlan:
    """Resultatet av typologi-planning for et delfelt."""
    typologi: TypologiKind
    bygninger: List[Bygning] = field(default_factory=list)
    kvartaler: List[Kvartal] = field(default_factory=list)
    total_bra_m2: float = 0.0
    total_footprint_m2: float = 0.0
    notes: List[str] = field(default_factory=list)
    fallback_reason: str = ""           # hvis selector valgte noe annet enn ønsket

    def recompute_totals(self) -> None:
        self.total_footprint_m2 = sum(b.footprint_m2 for b in self.bygninger)
        self.total_bra_m2 = sum(b.bra_m2 for b in self.bygninger)


# ---------------------------------------------------------------------------
# Hjelpefunksjoner
# ---------------------------------------------------------------------------


def _bbox_inside_field(poly: Polygon, setback_m: float = 0.0) -> Optional[Tuple[float, float, float, float]]:
    if poly is None or poly.is_empty:
        return None
    minx, miny, maxx, maxy = poly.bounds
    minx += setback_m
    miny += setback_m
    maxx -= setback_m
    maxy -= setback_m
    if maxx - minx < 10.0 or maxy - miny < 10.0:
        return None
    return (minx, miny, maxx, maxy)


def _subtract_corridors(field_poly: Polygon, corridors: Optional[List[Polygon]]) -> Polygon:
    if not corridors:
        return field_poly
    valid = [c for c in corridors if c is not None and not c.is_empty]
    if not valid:
        return field_poly
    try:
        subtract_union = unary_union(valid)
        result = field_poly.difference(subtract_union)
        if result.is_empty or result.area < 500.0:
            return field_poly
        if hasattr(result, "geoms"):
            parts = [g for g in result.geoms if isinstance(g, Polygon)]
            if parts:
                result = max(parts, key=lambda g: g.area)
        if isinstance(result, Polygon):
            return result.buffer(0)
    except Exception:
        pass
    return field_poly


# ---------------------------------------------------------------------------
# LAMELL-planner
# ---------------------------------------------------------------------------


def plan_lamell_for_field(
    field_poly: Polygon,
    *,
    target_bra_m2: float,
    target_building_count: int = 0,
    floors_min: Optional[int] = None,
    floors_max: Optional[int] = None,
    profile: MasterplanProfile = MasterplanProfile.FORSTAD,
    axes_corridor_polygons: Optional[List[Polygon]] = None,
    orientation_deg: float = 0.0,
) -> TypologiPlan:
    """Plasser lameller i parallelle rader i et delfelt.

    Algoritme:
      1. Subtraher akse-korridor fra feltet.
      2. Velg orientering: sol-optimal (lengderetning N-S for best
         sol på begge langsider), ELLER følge feltets lange akse hvis
         den matcher bedre.
      3. Fordel lameller i rader med gap_between_m mellom radene,
         gap_end_to_end_m mellom lameller i samme rad.
      4. Hver lamell: lengde i [length_min, length_max], dybde
         depth_preferred_m.
      5. Bestem antall og etasjer for å matche target_bra.
    """
    params = PROFILE_LAMELL[profile]
    floors_min = floors_min if floors_min is not None else params.floors_default_min
    floors_max = floors_max if floors_max is not None else params.floors_default_max

    plan = TypologiPlan(typologi=TypologiKind.LAMELL)

    working_poly = _subtract_corridors(field_poly, axes_corridor_polygons)
    bbox = _bbox_inside_field(working_poly, setback_m=params.setback_m)
    if bbox is None:
        plan.notes.append(f"Kunne ikke plassere lameller (felt {field_poly.area:.0f} m² for smalt)")
        return plan
    minx, miny, maxx, maxy = bbox
    width_m = maxx - minx
    height_m = maxy - miny

    # Lamellens lengderetning følger bbox-lengste-side for best tilpasning.
    # For sol-optimal: vurder orientering senere (uke 3 stepping).
    lamell_is_horizontal = width_m >= height_m
    # Langside = lamellens lengde-akse, kortside = lamellens dybde-akse
    long_span_m = width_m if lamell_is_horizontal else height_m
    short_span_m = height_m if lamell_is_horizontal else width_m

    # Antall rader (parallelle lameller i dybderetning)
    # rad_pitch = lamell_dybde + gap_between
    rad_pitch = params.depth_preferred_m + params.gap_between_m
    n_rows = max(1, int((short_span_m + params.gap_between_m) / rad_pitch))

    # Antall lameller per rad
    # kol_pitch = lamell_lengde + gap_end_to_end
    lamell_len_try = params.length_preferred_m
    n_cols = max(1, int((long_span_m + params.gap_end_to_end_m) / (lamell_len_try + params.gap_end_to_end_m)))
    # Juster lengden for å fylle raden jevnt
    actual_len = (long_span_m - (n_cols - 1) * params.gap_end_to_end_m) / n_cols
    actual_len = max(params.length_min_m, min(params.length_max_m, actual_len))

    # Beslutning: er dette et "infill"-scenarie (høy tetthet)?
    # For infill fyller vi grid og justerer etasjer ned. For normal
    # tetthet reduserer vi antall bygg for å unngå overbygging.
    density = target_bra_m2 / max(field_poly.area, 1.0) if target_bra_m2 > 0 else 0.0
    is_infill = density >= 2.00

    # Cap antall lameller basert på target_bra-kapasitet (kun normal tetthet)
    avg_floors = (floors_min + floors_max) / 2.0
    avg_bra_per_lamell = actual_len * params.depth_preferred_m * avg_floors
    n_total = n_rows * n_cols
    if not is_infill and target_bra_m2 > 0 and avg_bra_per_lamell > 0:
        max_by_bra = max(1, int(round(target_bra_m2 / avg_bra_per_lamell)))
        if n_total > max_by_bra:
            # Reduser rader først (lavere tetthet, bedre sol)
            while n_rows > 1 and n_rows * n_cols > max_by_bra:
                n_rows -= 1
            # Hvis fortsatt for mange, reduser kolonner
            while n_cols > 1 and n_rows * n_cols > max_by_bra:
                n_cols -= 1

    if target_building_count and target_building_count > 0:
        if n_rows * n_cols > target_building_count:
            while n_rows * n_cols > target_building_count and n_rows > 1:
                n_rows -= 1
            while n_rows * n_cols > target_building_count and n_cols > 1:
                n_cols -= 1

    # Sentrer grid i bbox
    total_cols_m = n_cols * actual_len + (n_cols - 1) * params.gap_end_to_end_m
    total_rows_m = n_rows * params.depth_preferred_m + (n_rows - 1) * params.gap_between_m

    if lamell_is_horizontal:
        offset_x = minx + (width_m - total_cols_m) / 2.0
        offset_y = miny + (height_m - total_rows_m) / 2.0
    else:
        offset_x = minx + (width_m - total_rows_m) / 2.0
        offset_y = miny + (height_m - total_cols_m) / 2.0

    # Bygg lameller
    idx = 0
    for row in range(n_rows):
        for col in range(n_cols):
            if lamell_is_horizontal:
                x0 = offset_x + col * (actual_len + params.gap_end_to_end_m)
                y0 = offset_y + row * rad_pitch
                x1 = x0 + actual_len
                y1 = y0 + params.depth_preferred_m
            else:
                x0 = offset_x + row * rad_pitch
                y0 = offset_y + col * (actual_len + params.gap_end_to_end_m)
                x1 = x0 + params.depth_preferred_m
                y1 = y0 + actual_len
            rect = box(x0, y0, x1, y1)
            # Verifiser at lamellen ligger innenfor feltet
            if not working_poly.buffer(1.0).covers(rect):
                continue
            idx += 1
            plan.bygninger.append(Bygning(
                bygg_id=f"L{idx}",
                polygon=rect,
                length_m=actual_len,
                depth_m=params.depth_preferred_m,
                floors=floors_min,  # Justeres senere
                typologi=TypologiKind.LAMELL,
                role="standard",
            ))

    # Juster etasjer for å treffe target_bra
    _adjust_floors_to_target(plan, target_bra_m2, floors_min, floors_max)
    plan.recompute_totals()
    plan.notes.append(
        f"{len(plan.bygninger)} lameller á {actual_len:.0f}×{params.depth_preferred_m:.0f}m "
        f"i {n_rows} rad(er) × {n_cols} kol"
    )
    return plan


# ---------------------------------------------------------------------------
# PUNKTHUS-planner
# ---------------------------------------------------------------------------


def plan_punkthus_for_field(
    field_poly: Polygon,
    *,
    target_bra_m2: float,
    target_building_count: int = 0,
    floors_min: Optional[int] = None,
    floors_max: Optional[int] = None,
    profile: MasterplanProfile = MasterplanProfile.FORSTAD,
    axes_corridor_polygons: Optional[List[Polygon]] = None,
) -> TypologiPlan:
    """Plasser punkthus i rutenett.

    20×20m per bygning (FORSTAD) eller 22×22m (URBAN). 10m gap.
    """
    params = PROFILE_PUNKTHUS[profile]
    floors_min = floors_min if floors_min is not None else params.floors_default_min
    floors_max = floors_max if floors_max is not None else params.floors_default_max

    plan = TypologiPlan(typologi=TypologiKind.PUNKTHUS)

    working_poly = _subtract_corridors(field_poly, axes_corridor_polygons)
    bbox = _bbox_inside_field(working_poly, setback_m=params.setback_m)
    if bbox is None:
        plan.notes.append(f"Kunne ikke plassere punkthus (felt {field_poly.area:.0f} m² for lite)")
        return plan
    minx, miny, maxx, maxy = bbox
    width_m = maxx - minx
    height_m = maxy - miny

    pitch = params.side_m + params.gap_m
    n_cols = max(1, int((width_m + params.gap_m) / pitch))
    n_rows = max(1, int((height_m + params.gap_m) / pitch))

    # Infill-mode: fyll grid, juster etasjer ned
    density = target_bra_m2 / max(field_poly.area, 1.0) if target_bra_m2 > 0 else 0.0
    is_infill = density >= 2.00

    # Cap antall basert på target (kun normal tetthet)
    n_total = n_rows * n_cols
    avg_floors = (floors_min + floors_max) / 2.0
    bra_per = params.side_m * params.side_m * avg_floors
    if not is_infill and target_bra_m2 > 0 and bra_per > 0:
        max_by_bra = max(1, int(round(target_bra_m2 / bra_per)))
        while n_total > max_by_bra and (n_rows > 1 or n_cols > 1):
            if n_cols >= n_rows and n_cols > 1:
                n_cols -= 1
            elif n_rows > 1:
                n_rows -= 1
            n_total = n_rows * n_cols

    if target_building_count and target_building_count > 0:
        while n_total > target_building_count and (n_rows > 1 or n_cols > 1):
            if n_cols >= n_rows and n_cols > 1:
                n_cols -= 1
            elif n_rows > 1:
                n_rows -= 1
            n_total = n_rows * n_cols

    # Sentrer grid
    total_cols_m = n_cols * params.side_m + (n_cols - 1) * params.gap_m
    total_rows_m = n_rows * params.side_m + (n_rows - 1) * params.gap_m
    offset_x = minx + (width_m - total_cols_m) / 2.0
    offset_y = miny + (height_m - total_rows_m) / 2.0

    idx = 0
    for row in range(n_rows):
        for col in range(n_cols):
            x0 = offset_x + col * pitch
            y0 = offset_y + row * pitch
            rect = box(x0, y0, x0 + params.side_m, y0 + params.side_m)
            if not working_poly.buffer(1.0).covers(rect):
                continue
            idx += 1
            plan.bygninger.append(Bygning(
                bygg_id=f"P{idx}",
                polygon=rect,
                length_m=params.side_m,
                depth_m=params.side_m,
                floors=floors_min,
                typologi=TypologiKind.PUNKTHUS,
                role="standard",
            ))

    _adjust_floors_to_target(plan, target_bra_m2, floors_min, floors_max)
    plan.recompute_totals()
    plan.notes.append(
        f"{len(plan.bygninger)} punkthus á {params.side_m:.0f}×{params.side_m:.0f}m "
        f"i {n_rows}×{n_cols} grid"
    )
    return plan


# ---------------------------------------------------------------------------
# KARRÉ-planner (rektangulær, U/O-form, 50×25-30m)
# ---------------------------------------------------------------------------


def _build_kvartal_outer_and_courtyard(
    x_min: float, y_min: float,
    length_m: float, depth_m: float,
    arm_depth_m: float,
    open_side: Optional[str] = None,
) -> Tuple[Polygon, Polygon, Dict[str, Polygon]]:
    """Bygg ytre ramme + gårdsrom + fire arm-soner.

    length_m er lang side (bunn/topp, Ø-V når ikke rotert).
    depth_m er kort side (N-S).
    """
    x_max = x_min + length_m
    y_max = y_min + depth_m
    outer = box(x_min, y_min, x_max, y_max)
    # Gårdsrom
    gx_min = x_min + arm_depth_m
    gy_min = y_min + arm_depth_m
    gx_max = x_max - arm_depth_m
    gy_max = y_max - arm_depth_m
    if gx_max - gx_min < 2.0 or gy_max - gy_min < 2.0:
        # Ikke plass til gårdsrom — returner bare outer som solid (ingen ring)
        return outer, Polygon(), {}
    courtyard = box(gx_min, gy_min, gx_max, gy_max)

    # Fire armer (som separate polygoner — nyttig for U-form)
    arms = {
        "north": box(x_min, gy_max, x_max, y_max),
        "south": box(x_min, y_min, x_max, gy_min),
        "west":  box(x_min, gy_min, gx_min, gy_max),
        "east":  box(gx_max, gy_min, x_max, gy_max),
    }

    # Hvis open_side spesifisert, dropp den armen
    if open_side and open_side in arms:
        arms = {k: v for k, v in arms.items() if k != open_side}
        # Utvid gårdsrommet til å fylle den åpne siden
        if open_side == "north":
            courtyard = box(gx_min, gy_min, gx_max, y_max)
        elif open_side == "south":
            courtyard = box(gx_min, y_min, gx_max, gy_max)
        elif open_side == "east":
            courtyard = box(gx_min, gy_min, x_max, gy_max)
        elif open_side == "west":
            courtyard = box(x_min, gy_min, gx_max, gy_max)

    return outer, courtyard, arms


def plan_karre_for_field(
    field_poly: Polygon,
    *,
    target_bra_m2: float,
    target_building_count: int = 0,
    floors_min: Optional[int] = None,
    floors_max: Optional[int] = None,
    profile: MasterplanProfile = MasterplanProfile.FORSTAD,
    axes_corridor_polygons: Optional[List[Polygon]] = None,
) -> TypologiPlan:
    """Plasser karrésstruktur (rektangulær ring 50×25-30m).

    KARRÉ-KRAV: Feltet må være minst 10 000 m². Selector sjekker dette
    før denne funksjonen kalles; funksjonen selv aksepterer mindre
    felt men noterer det som avvik.
    """
    params = PROFILE_KVARTAL[profile]
    floors_min = floors_min if floors_min is not None else params.floors_default_min
    floors_max = floors_max if floors_max is not None else params.floors_default_max

    plan = TypologiPlan(typologi=TypologiKind.KARRE)

    working_poly = _subtract_corridors(field_poly, axes_corridor_polygons)
    bbox = _bbox_inside_field(working_poly, setback_m=params.setback_m)
    if bbox is None:
        plan.notes.append(f"Kunne ikke plassere karré (felt {field_poly.area:.0f} m² for smalt)")
        return plan
    minx, miny, maxx, maxy = bbox
    width_m = maxx - minx
    height_m = maxy - miny

    # Karré-lengderetning følger bbox-lengste-side
    karre_length = params.length_m
    karre_depth = params.depth_short_m
    kl_is_horizontal = width_m >= height_m

    if kl_is_horizontal:
        pitch_x = karre_length + params.gate_m
        pitch_y = karre_depth + params.gate_m
        n_cols = max(1, int((width_m + params.gate_m) / pitch_x))
        n_rows = max(1, int((height_m + params.gate_m) / pitch_y))
    else:
        pitch_x = karre_depth + params.gate_m
        pitch_y = karre_length + params.gate_m
        n_cols = max(1, int((width_m + params.gate_m) / pitch_x))
        n_rows = max(1, int((height_m + params.gate_m) / pitch_y))

    # Cap antall basert på target_bra
    ring_outer_area = karre_length * karre_depth
    inner_length = karre_length - 2 * params.arm_depth_m
    inner_depth = karre_depth - 2 * params.arm_depth_m
    inner_area = max(0.0, inner_length * inner_depth)
    ring_footprint_area = ring_outer_area - inner_area
    avg_floors = (floors_min + floors_max) / 2.0
    bra_per_karre = ring_footprint_area * avg_floors

    n_total = n_rows * n_cols
    if target_bra_m2 > 0 and bra_per_karre > 0:
        max_by_bra = max(1, int(round(target_bra_m2 / bra_per_karre)))
        while n_total > max_by_bra and (n_rows > 1 or n_cols > 1):
            if n_cols >= n_rows and n_cols > 1:
                n_cols -= 1
            elif n_rows > 1:
                n_rows -= 1
            n_total = n_rows * n_cols

    if target_building_count and target_building_count > 0:
        while n_total > target_building_count and (n_rows > 1 or n_cols > 1):
            if n_cols >= n_rows and n_cols > 1:
                n_cols -= 1
            elif n_rows > 1:
                n_rows -= 1
            n_total = n_rows * n_cols

    # Sentrer grid
    if kl_is_horizontal:
        total_cols_m = n_cols * karre_length + (n_cols - 1) * params.gate_m
        total_rows_m = n_rows * karre_depth + (n_rows - 1) * params.gate_m
    else:
        total_cols_m = n_cols * karre_depth + (n_cols - 1) * params.gate_m
        total_rows_m = n_rows * karre_length + (n_rows - 1) * params.gate_m
    offset_x = minx + (width_m - total_cols_m) / 2.0
    offset_y = miny + (height_m - total_rows_m) / 2.0

    # Bestem åpen side for U-form: kun for enkelt-kvartal (n_total==1)
    # og kun hvis prefer_open_side_for_single. Åpner mot sør (sol).
    use_u_form = (n_total == 1 and params.prefer_open_side_for_single
                  and inner_depth < 10.0)  # liten gårdsrom -> åpen mot sør

    idx = 0
    for row in range(n_rows):
        for col in range(n_cols):
            if kl_is_horizontal:
                x0 = offset_x + col * (karre_length + params.gate_m)
                y0 = offset_y + row * (karre_depth + params.gate_m)
                length_used = karre_length
                depth_used = karre_depth
            else:
                x0 = offset_x + col * (karre_depth + params.gate_m)
                y0 = offset_y + row * (karre_length + params.gate_m)
                length_used = karre_depth   # i roterte koordinater (kortsiden langs x)
                depth_used = karre_length

            open_side = "south" if use_u_form else None
            outer, courtyard, arms = _build_kvartal_outer_and_courtyard(
                x0, y0, length_used, depth_used, params.arm_depth_m,
                open_side=open_side,
            )
            if not working_poly.buffer(1.0).covers(outer):
                continue
            idx += 1
            kv = Kvartal(
                kvartal_id=f"K{idx}",
                outer_polygon=outer,
                courtyard_polygon=courtyard,
                open_side=open_side,
            )
            plan.kvartaler.append(kv)

            # Bygg ring-footprint som én polygon (outer - courtyard)
            if courtyard.is_empty:
                ring = outer
            else:
                ring = outer.difference(courtyard)
            if hasattr(ring, "geoms"):
                parts = [g for g in ring.geoms if isinstance(g, Polygon)]
                if parts:
                    ring = max(parts, key=lambda g: g.area)
            if not isinstance(ring, Polygon) or ring.is_empty:
                continue
            plan.bygninger.append(Bygning(
                bygg_id=f"K{idx}",
                polygon=ring.buffer(0),
                length_m=length_used,
                depth_m=depth_used,
                floors=floors_min,
                typologi=TypologiKind.KARRE,
                role="ring" if not open_side else "u_ring",
            ))

    _adjust_floors_to_target(plan, target_bra_m2, floors_min, floors_max)
    plan.recompute_totals()
    plan.notes.append(
        f"{len(plan.kvartaler)} karré(er) á {karre_length:.0f}×{karre_depth:.0f}m, "
        f"arm {params.arm_depth_m:.0f}m, form {'U' if use_u_form else 'O'}"
    )
    return plan


# ---------------------------------------------------------------------------
# Etasje-justering (felles)
# ---------------------------------------------------------------------------


def _adjust_floors_to_target(
    plan: TypologiPlan,
    target_bra_m2: float,
    floors_min: int,
    floors_max: int,
) -> None:
    """Juster etasjer trinnvis for å nærme seg target BRA.

    Cap: innenfor ±10% av target, innenfor [floors_min, floors_max+2].
    """
    if target_bra_m2 <= 0 or not plan.bygninger:
        return

    def total_bra() -> float:
        return sum(b.footprint_m2 * b.floors for b in plan.bygninger)

    # Øk etasjer hvis under 90% av target
    cur = total_bra()
    max_allowed = floors_max + 2
    iterations = 0
    while cur < target_bra_m2 * 0.90 and iterations < 20:
        iterations += 1
        increased_any = False
        for b in plan.bygninger:
            if b.floors < max_allowed:
                b.floors += 1
                increased_any = True
                cur = total_bra()
                if cur >= target_bra_m2 * 0.95:
                    return
        if not increased_any:
            break

    # Reduser etasjer hvis over 110% av target
    iterations = 0
    while cur > target_bra_m2 * 1.10 and iterations < 20:
        iterations += 1
        reduced_any = False
        for b in plan.bygninger:
            if b.floors > floors_min:
                b.floors -= 1
                reduced_any = True
                cur = total_bra()
                if cur <= target_bra_m2 * 1.05:
                    return
        if not reduced_any:
            break


# ---------------------------------------------------------------------------
# SELECTOR — velger rett typologi basert på felt-areal og krav
# ---------------------------------------------------------------------------


def _karre_dimensions_fit(field_poly: Polygon, params: KvartalParameters) -> Tuple[bool, str]:
    """Sjekk om feltets bbox rommer minst én karré-ramme.

    Krav (fra Pål): minimum 58×45m (ramme) for én karré med 4m setback rundt.
    Dette matcher karré 50×28m + ~4m gang-sone på alle kanter.

    Returnerer (passer, begrunnelse).
    """
    if field_poly is None or field_poly.is_empty:
        return (False, "Felt er tomt")
    minx, miny, maxx, maxy = field_poly.bounds
    w = maxx - minx
    h = maxy - miny
    # Minste ramme: karré-lengde + 2×setback (lang side) × karré-kort + 2×setback (kort)
    min_long = params.length_m + 2 * params.setback_m            # 50 + 8 = 58m
    min_short = params.depth_short_min_m + 2 * params.setback_m  # 25 + 8 = 33m
    long_side = max(w, h)
    short_side = min(w, h)
    if long_side < min_long:
        return (False,
                f"Langside {long_side:.0f}m < minimum {min_long:.0f}m for karré")
    if short_side < min_short:
        return (False,
                f"Kortside {short_side:.0f}m < minimum {min_short:.0f}m for karré")
    return (True, f"Bbox {w:.0f}×{h:.0f}m rommer karré")


def select_typology_for_field(
    field_poly: Polygon,
    *,
    requested: Optional[TypologiKind] = None,
    target_building_count: int = 0,
    target_bra_m2: float = 0.0,
    profile: MasterplanProfile = MasterplanProfile.FORSTAD,
) -> Tuple[TypologiKind, str]:
    """Velg rett typologi for et delfelt — dimensjon-basert.

    REGLER (fra Pål's feedback):

    1. **Karré krever dimensjon-minimum, ikke areal-minimum.**
       En karré (50×28m + 4m setback = 58×36m ramme) kan plasseres
       selv på ~2 600 m² felt hvis formen er riktig. Areal-regel
       alene er for grov.

    2. **Infill-tomter (2 000 m² med 300% BRA) er legitime.**
       Selectoren må ikke degradere til punkthus bare fordi tomten
       er liten. Hvis formen og tetthet tilsier karré eller lamell,
       velg det.

    3. **MUA-konflikt er brukerens avgjørelse.**
       Ved veldig høy tetthet må BYA ofte gå over det MUA-reglene
       ideelt tillater. Selectoren velger typologi basert på
       arkitektonisk passform; MUA-avvik flagges som notat.

    Prioritet:
      a) Eksplisitt requested → respekteres hvis dimension tillater
      b) Auto-valg:
         - Karré-dimensjoner OK + tetthet ≥ 100% → KARRÉ
         - Høy tetthet (≥ 150%) ELLER ≥ 4 bygg → LAMELL
         - Ellers → PUNKTHUS
    """
    field_area = field_poly.area
    karre_params = PROFILE_KVARTAL[profile]
    density = target_bra_m2 / max(field_area, 1.0) if target_bra_m2 > 0 else 0.0
    karre_fits, karre_reason = _karre_dimensions_fit(field_poly, karre_params)

    # Eksplisitt requested
    if requested == TypologiKind.KARRE:
        if karre_fits and field_area >= karre_params.min_field_area_m2:
            return (TypologiKind.KARRE,
                    f"Karré bekreftet ({karre_reason}, {field_area:.0f} m², "
                    f"tetthet {density*100:.0f}%)")
        # Degrader: form eller areal tillater ikke karré
        if density >= 2.00:
            # Svært høy tetthet (infill) → lameller er mest effektivt
            return (TypologiKind.LAMELL,
                    f"Karré ikke mulig ({karre_reason}); "
                    f"høy tetthet ({density*100:.0f}%) → LAMELL for infill")
        elif density >= 1.50 or target_building_count >= 4:
            return (TypologiKind.LAMELL,
                    f"Karré ikke mulig ({karre_reason}); valgte LAMELL")
        else:
            return (TypologiKind.PUNKTHUS,
                    f"Karré ikke mulig ({karre_reason}); valgte PUNKTHUS")

    if requested in (TypologiKind.LAMELL, TypologiKind.PUNKTHUS):
        return (requested, f"Valgt eksplisitt: {requested.value}")

    # Auto-valg
    # Infill-scenarie (tetthet >= 200%): prioriter typologi som dekker
    # mest fotavtrykk → lameller eller karré
    if density >= 2.00:
        if karre_fits:
            return (TypologiKind.KARRE,
                    f"Infill med karré-form ({field_area:.0f} m², "
                    f"tetthet {density*100:.0f}% → karré dekker godt)")
        return (TypologiKind.LAMELL,
                f"Infill ({field_area:.0f} m², tetthet {density*100:.0f}%) "
                f"uten karré-form → LAMELL fyller opp")

    # Normal tetthet (100-200%): karré hvis dim OK, ellers lamell/punkthus
    if karre_fits and density >= 1.00 and target_building_count >= 1:
        return (TypologiKind.KARRE,
                f"Karré OK ({karre_reason}, tetthet {density*100:.0f}%)")

    if density >= 1.50 or target_building_count >= 4:
        return (TypologiKind.LAMELL,
                f"Høy tetthet eller mange bygg ({target_building_count}) i "
                f"{field_area:.0f} m² → lameller")

    return (TypologiKind.PUNKTHUS,
            f"Lav tetthet ({density*100:.0f}%) og få bygg ({target_building_count}) "
            f"i {field_area:.0f} m² → punkthus")


def plan_typologi_for_field(
    field_poly: Polygon,
    *,
    target_bra_m2: float,
    target_building_count: int = 0,
    requested_typologi: Optional[TypologiKind] = None,
    floors_min: Optional[int] = None,
    floors_max: Optional[int] = None,
    profile: MasterplanProfile = MasterplanProfile.FORSTAD,
    axes_corridor_polygons: Optional[List[Polygon]] = None,
) -> TypologiPlan:
    """Orkestrator: velg typologi og lag plan.

    Kjører selector, kaller rett plan-funksjon, og lagrer
    fallback_reason hvis selector valgte noe annet enn ønsket.
    """
    chosen, reason = select_typology_for_field(
        field_poly,
        requested=requested_typologi,
        target_building_count=target_building_count,
        target_bra_m2=target_bra_m2,
        profile=profile,
    )

    if chosen == TypologiKind.LAMELL:
        plan = plan_lamell_for_field(
            field_poly,
            target_bra_m2=target_bra_m2,
            target_building_count=target_building_count,
            floors_min=floors_min,
            floors_max=floors_max,
            profile=profile,
            axes_corridor_polygons=axes_corridor_polygons,
        )
    elif chosen == TypologiKind.PUNKTHUS:
        plan = plan_punkthus_for_field(
            field_poly,
            target_bra_m2=target_bra_m2,
            target_building_count=target_building_count,
            floors_min=floors_min,
            floors_max=floors_max,
            profile=profile,
            axes_corridor_polygons=axes_corridor_polygons,
        )
    else:  # KARRE
        plan = plan_karre_for_field(
            field_poly,
            target_bra_m2=target_bra_m2,
            target_building_count=target_building_count,
            floors_min=floors_min,
            floors_max=floors_max,
            profile=profile,
            axes_corridor_polygons=axes_corridor_polygons,
        )

    if requested_typologi and requested_typologi != chosen:
        plan.fallback_reason = reason
    plan.notes.insert(0, reason)

    # MUA-awareness: vurder om plassering bryter typiske bakke-MUA-krav.
    # Typisk regel: 25-40 m² MUA per boenhet, eller min 25% av tomtearealet.
    # Her flagger vi grovt basert på resterende bakkeareal.
    if plan.bygninger and plan.total_footprint_m2 > 0:
        field_area = float(field_poly.area)
        bya_pct = plan.total_footprint_m2 / field_area * 100
        density_pct = plan.total_bra_m2 / field_area * 100
        # Estimert bakke-MUA (tomteareal minus footprint, før takareal)
        bakke_available_m2 = field_area - plan.total_footprint_m2
        bakke_pct = bakke_available_m2 / field_area * 100

        plan.notes.append(
            f"BYA {bya_pct:.0f}%, bakke-restareal {bakke_pct:.0f}% "
            f"({bakke_available_m2:.0f} m²), tetthet {density_pct:.0f}%"
        )

        # Grov MUA-regel: for boliger trenger vi min 25% av tomtearealet
        # som MUA (kan inkludere tak). Hvis bakke-restareal er under 25%
        # OG tetthet er over 200% (infill), er dette en akseptabel
        # trade-off som må dekkes av tak-MUA.
        if bakke_pct < 25.0 and density_pct >= 200.0:
            plan.notes.append(
                f"⚠ MUA-trade-off: bakke-restareal {bakke_pct:.0f}% < 25% ved "
                f"infill-tetthet. Tak-MUA må dekke differanse (gjelder ofte "
                f"300%-prosjekter i bysentrum)."
            )
        elif bakke_pct < 25.0:
            plan.notes.append(
                f"⚠ MUA-advarsel: bakke-restareal {bakke_pct:.0f}% < 25% ved "
                f"normal tetthet. Vurder færre/mindre bygg eller sjekk "
                f"at tak-MUA kompenserer."
            )

    return plan


# ---------------------------------------------------------------------------
# Diagnostikk
# ---------------------------------------------------------------------------


def plan_debug_summary(plan: TypologiPlan) -> Dict[str, Any]:
    bygg_dims = [(b.length_m, b.depth_m, b.floors) for b in plan.bygninger]
    return {
        "typologi": plan.typologi.value,
        "bygg_count": len(plan.bygninger),
        "kvartal_count": len(plan.kvartaler),
        "total_footprint_m2": round(plan.total_footprint_m2, 1),
        "total_bra_m2": round(plan.total_bra_m2, 1),
        "fallback_reason": plan.fallback_reason,
        "bygg_dimensjoner": [
            f"{l:.0f}×{d:.0f}m×{f}et" for l, d, f in bygg_dims
        ][:8],  # Vis maks 8 for kompakt output
        "notes": list(plan.notes),
    }


# ---------------------------------------------------------------------------
# Uke 3: Rotasjon til felt-orientering
# ---------------------------------------------------------------------------


def rotate_plan(
    plan: TypologiPlan,
    *,
    angle_deg: float,
    origin: Point,
) -> TypologiPlan:
    """Rotér alle bygg og kvartaler i plan rundt `origin` med `angle_deg`.

    Returnerer ny TypologiPlan med roterte geometri-objekter. Brukes når
    feltets orientering avviker fra aksejustert — typologi-plannerne
    bygger alltid aksejustert, så rotasjon skjer i etterkant.
    """
    from shapely import affinity as _aff
    if abs(angle_deg) < 0.01:
        return plan

    def rot(geom):
        if geom is None or getattr(geom, "is_empty", True):
            return geom
        return _aff.rotate(geom, angle_deg, origin=origin, use_radians=False)

    new_bygg: List[Bygning] = []
    for b in plan.bygninger:
        new_bygg.append(Bygning(
            bygg_id=b.bygg_id,
            polygon=rot(b.polygon),
            length_m=b.length_m,
            depth_m=b.depth_m,
            floors=b.floors,
            typologi=b.typologi,
            role=b.role,
        ))

    new_kvartaler: List[Kvartal] = []
    for kv in plan.kvartaler:
        new_kvartaler.append(Kvartal(
            kvartal_id=kv.kvartal_id,
            outer_polygon=rot(kv.outer_polygon),
            courtyard_polygon=rot(kv.courtyard_polygon),
            open_side=kv.open_side,
            orientation_deg=kv.orientation_deg + angle_deg,
        ))

    rotated = TypologiPlan(
        typologi=plan.typologi,
        bygninger=new_bygg,
        kvartaler=new_kvartaler,
        notes=list(plan.notes),
        fallback_reason=plan.fallback_reason,
    )
    rotated.recompute_totals()
    return rotated


def plan_typologi_for_field_with_rotation(
    field_poly: Polygon,
    *,
    target_bra_m2: float,
    target_building_count: int = 0,
    requested_typologi: Optional[TypologiKind] = None,
    floors_min: Optional[int] = None,
    floors_max: Optional[int] = None,
    profile: MasterplanProfile = MasterplanProfile.FORSTAD,
    axes_corridor_polygons: Optional[List[Polygon]] = None,
    orientation_deg: float = 0.0,
) -> TypologiPlan:
    """Som plan_typologi_for_field, men roterer output til gitt orientering.

    Algoritme:
      1. Roter field_poly og corridor-polygoner `-orientation_deg` til lokal.
      2. Kjør plan_typologi_for_field i lokalt koordinatsystem (aksejustert).
      3. Roter resultat `+orientation_deg` tilbake til globalt.
    """
    from shapely import affinity as _aff
    if abs(orientation_deg) < 0.01:
        return plan_typologi_for_field(
            field_poly,
            target_bra_m2=target_bra_m2,
            target_building_count=target_building_count,
            requested_typologi=requested_typologi,
            floors_min=floors_min,
            floors_max=floors_max,
            profile=profile,
            axes_corridor_polygons=axes_corridor_polygons,
        )

    origin = field_poly.centroid
    local_field = _aff.rotate(field_poly, -orientation_deg, origin=origin, use_radians=False)
    local_corridors = None
    if axes_corridor_polygons:
        local_corridors = [
            _aff.rotate(c, -orientation_deg, origin=origin, use_radians=False)
            for c in axes_corridor_polygons if c is not None and not c.is_empty
        ]

    plan = plan_typologi_for_field(
        local_field,
        target_bra_m2=target_bra_m2,
        target_building_count=target_building_count,
        requested_typologi=requested_typologi,
        floors_min=floors_min,
        floors_max=floors_max,
        profile=profile,
        axes_corridor_polygons=local_corridors,
    )
    return rotate_plan(plan, angle_deg=orientation_deg, origin=origin)


# ---------------------------------------------------------------------------
# Uke 3: Høyde-rytme
# ---------------------------------------------------------------------------


class HeightRhythm(str, Enum):
    UNIFORM = "uniform"                      # Alle like
    SYMMETRIC_LOW_HIGH_LOW = "3_5_3"         # Lavt-høyt-lavt (palindrome)
    SYMMETRIC_HIGH_LOW_HIGH = "5_3_5"        # Høyt-lavt-høyt
    ALTERNATING = "alternating"              # 4-5-4-5-4-5...
    STEPPED_UP = "stepped_up"                # Gradvis høyere langs aksen
    STEPPED_DOWN = "stepped_down"            # Gradvis lavere langs aksen
    CORNER_TOWERS = "corner_towers"          # Endebygg høyere


def _sort_bygg_along_axis(
    bygninger: List[Bygning],
    axis_direction: Tuple[float, float],
) -> List[Bygning]:
    """Sorter bygg etter projeksjon på akse-retning (sør→nord, vest→øst, osv).

    `axis_direction` er en (dx, dy) enhetsvektor. Projeksjon gir skalarverdi
    som brukes til sortering. Returnerer ny liste.
    """
    dx, dy = axis_direction

    def project(b: Bygning) -> float:
        c = b.polygon.centroid
        return float(c.x) * dx + float(c.y) * dy

    return sorted(bygninger, key=project)


def apply_height_rhythm(
    plan: TypologiPlan,
    *,
    rhythm: HeightRhythm = HeightRhythm.UNIFORM,
    floors_min: int = 4,
    floors_max: int = 7,
    axis_direction: Tuple[float, float] = (1.0, 0.0),
    target_bra_m2: float = 0.0,
) -> TypologiPlan:
    """Tildel etasjer til bygg etter mønster langs en akse.

    Muterer plan.bygninger in-place og returnerer samme plan for chaining.
    Hvis target_bra_m2 > 0, skaleres pattern-høyder slik at total BRA
    treffer målet innenfor ±10%.
    """
    if not plan.bygninger:
        return plan

    # Sorter bygg langs aksen
    sorted_bygg = _sort_bygg_along_axis(plan.bygninger, axis_direction)
    n = len(sorted_bygg)

    low = floors_min
    high = floors_max
    mid = (low + high) // 2

    # Beregn floor pattern
    pattern: List[int] = []
    if rhythm == HeightRhythm.UNIFORM:
        pattern = [mid] * n
    elif rhythm == HeightRhythm.SYMMETRIC_LOW_HIGH_LOW:
        # f.eks 3-5-5-5-3 eller 4-6-6-4
        if n <= 2:
            pattern = [low] * n
        elif n <= 4:
            pattern = [low] + [high] * (n - 2) + [low]
        else:
            edges = 1
            pattern = [low] * edges + [high] * (n - 2 * edges) + [low] * edges
    elif rhythm == HeightRhythm.SYMMETRIC_HIGH_LOW_HIGH:
        if n <= 2:
            pattern = [high] * n
        elif n <= 4:
            pattern = [high] + [low] * (n - 2) + [high]
        else:
            edges = 1
            pattern = [high] * edges + [low] * (n - 2 * edges) + [high] * edges
    elif rhythm == HeightRhythm.ALTERNATING:
        pattern = [low if i % 2 == 0 else high for i in range(n)]
    elif rhythm == HeightRhythm.STEPPED_UP:
        if n == 1:
            pattern = [mid]
        else:
            step = (high - low) / max(n - 1, 1)
            pattern = [int(round(low + step * i)) for i in range(n)]
    elif rhythm == HeightRhythm.STEPPED_DOWN:
        if n == 1:
            pattern = [mid]
        else:
            step = (high - low) / max(n - 1, 1)
            pattern = [int(round(high - step * i)) for i in range(n)]
    elif rhythm == HeightRhythm.CORNER_TOWERS:
        # Endebygg = høyest, resten = median
        if n == 1:
            pattern = [high]
        elif n == 2:
            pattern = [high, high]
        else:
            pattern = [high] + [mid] * (n - 2) + [high]
    else:
        pattern = [mid] * n

    # Anvend pattern
    for b, f in zip(sorted_bygg, pattern):
        b.floors = max(floors_min, min(floors_max, int(f)))

    plan.recompute_totals()

    # Hvis target_bra er gitt og vi er for langt unna: skalér alle bygg
    # med SAMME delta for å bevare rytme-differensen. Clamp mot floors-range.
    if target_bra_m2 > 0:
        cur = plan.total_bra_m2
        # Opp: legg til delta på alle bygg samtidig til target treffes eller alle er på max
        delta_up = 0
        while cur < target_bra_m2 * 0.90 and delta_up < 4:
            if all(b.floors + 1 > floors_max for b in plan.bygninger):
                break
            delta_up += 1
            for b in plan.bygninger:
                if b.floors + 1 <= floors_max:
                    b.floors += 1
            plan.recompute_totals()
            cur = plan.total_bra_m2
        # Ned: samme logikk
        delta_down = 0
        while cur > target_bra_m2 * 1.10 and delta_down < 4:
            if all(b.floors - 1 < floors_min for b in plan.bygninger):
                break
            delta_down += 1
            for b in plan.bygninger:
                if b.floors - 1 >= floors_min:
                    b.floors -= 1
            plan.recompute_totals()
            cur = plan.total_bra_m2

    plan.notes.append(f"Høyde-rytme: {rhythm.value} → {', '.join(str(b.floors) for b in sorted_bygg)}et")
    return plan


# ---------------------------------------------------------------------------
# Integrasjonshjelper: konvertér TypologiPlan til Bygg-objekter
# ---------------------------------------------------------------------------


def typologiplan_to_bygg_list(
    plan: TypologiPlan,
    *,
    field_id: str,
    phase: int = 1,
    phase_label: str = "",
    floor_height_m: float = 3.1,
) -> List[Any]:
    """Konverter TypologiPlan.bygninger til Bygg-objekter.

    Bygg-dataklassen er definert i masterplan_types; vi importerer lokalt
    for å unngå sirkulær import på modul-nivå.
    """
    from .masterplan_types import Bygg, Typology

    typologi_map = {
        TypologiKind.LAMELL: Typology.LAMELL,
        TypologiKind.PUNKTHUS: Typology.PUNKTHUS,
        TypologiKind.KARRE: Typology.KARRE,
    }

    bygg_list = []
    for idx, b in enumerate(plan.bygninger, start=1):
        typology_enum = typologi_map.get(b.typologi, Typology.LAMELL)
        floors = int(b.floors)
        height_m = floors * floor_height_m
        bygg_list.append(Bygg(
            bygg_id=f"{field_id}_{b.bygg_id}",
            footprint=b.polygon.buffer(0),
            floors=floors,
            height_m=height_m,
            typology=typology_enum,
            delfelt_id=field_id,
            phase=phase,
            display_name=f"{field_id} {b.bygg_id}",
        ))
    return bygg_list
