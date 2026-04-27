from __future__ import annotations

"""Concept-family strategies for Builtly.

These strategies do not place geometry themselves; they define a strong
architectural envelope that the deterministic geometry pass must follow.
"""

from dataclasses import dataclass
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .masterplan_types import ConceptFamily, CourtyardKind, Delfelt, FieldParameterChoice, PlanRegler, Typology


@dataclass(frozen=True)
class FieldEnvelope:
    allowed_typologies: Tuple[Typology, ...]
    default_typology: Typology
    default_orientation_offset_deg: float = 0.0
    default_floors: Tuple[int, int] = (4, 5)
    courtyard_kind: Optional[CourtyardKind] = None
    tower_size_m: Optional[int] = None
    field_role: str = ""
    character: str = ""
    design_variant: Optional[str] = None
    design_karre_shape: Optional[str] = None
    design_height_pattern: Optional[str] = None
    target_bya_pct: Optional[float] = None
    skeleton_mode: Optional[str] = None
    frontage_mode: Optional[str] = None
    micro_band_count: int = 0
    view_corridor_count: int = 0
    courtyard_reserve_ratio: float = 0.0
    frontage_depth_m: Optional[float] = None
    corridor_width_m: Optional[float] = None
    central_void_m: float = 0.0
    gap_between_m: float = 8.0
    macro_structure: Optional[str] = None
    micro_field_pattern: Optional[str] = None
    symmetry_preference: Optional[str] = None
    composition_strictness: float = 0.0
    frontage_zone_ratio: float = 0.0
    public_realm_ratio: float = 0.0
    node_symmetry: bool = False
    frontage_primary_side: Optional[str] = None
    frontage_secondary_side: Optional[str] = None
    lamell_rhythm_mode: Optional[str] = None
    node_layout_mode: Optional[str] = None
    courtyard_open_side: Optional[str] = None
    target_building_count: int = 0
    frontage_emphasis: float = 0.0
    rhythm_strength: float = 0.0


# Feltstørrelse per bygg/volum.
# V6 pre-deploy:
# - Karré er ikke absolutt låst til store felt, men krever at feltformen faktisk
#   kan bære et proporsjonalt kvartalsgrep.
# - Lamell styres mot lengre hus (typisk 55–65 x 12–14 m), men får noe spillerom
#   på mindre felt.
# - Punkthus styres mot ca. 20 x 20 m.
_AREA_PER_BUILDING_RULES: Dict[Typology, Tuple[float, float]] = {
    # Runde 2 tetthet: store tomter skal kunne få flere bygg og høyere
    # utnyttelse. Disse tallene er ikke absolutte minstekrav, men styrer
    # hvor tidlig motoren forsøker flere volum per felt.
    Typology.KARRE: (2400.0, 5600.0),
    Typology.LAMELL: (1300.0, 2600.0),
    Typology.PUNKTHUS: (950.0, 1800.0),
    Typology.REKKEHUS: (350.0, 700.0),
}

_COUNT_CEILINGS: Dict[Typology, int] = {
    Typology.KARRE: 7,
    Typology.LAMELL: 16,
    Typology.PUNKTHUS: 12,
    Typology.REKKEHUS: 16,
}


def _minimum_rotated_dims(poly: Any) -> Tuple[float, float]:
    if poly is None or getattr(poly, 'is_empty', True):
        return 0.0, 0.0
    try:
        rect = poly.minimum_rotated_rectangle
        coords = list(rect.exterior.coords)
        lengths: List[float] = []
        for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
            edge = math.hypot(float(x2) - float(x1), float(y2) - float(y1))
            if edge > 1e-6:
                lengths.append(edge)
        if lengths:
            return float(max(lengths)), float(min(lengths))
    except Exception:
        pass
    try:
        minx, miny, maxx, maxy = poly.bounds
        return float(max(maxx - minx, maxy - miny)), float(min(maxx - minx, maxy - miny))
    except Exception:
        return 0.0, 0.0


def _is_compact_infill(field_area_m2: float, target_bra_m2: float) -> bool:
    density = float(target_bra_m2 or 0.0) / max(field_area_m2, 1.0)
    # Runde 8.1: Ikke tolk hvert lite delfelt i en stor masterplan som infill.
    # Kompakt infill skal bare slå inn når både feltet er lite og utnyttelsen er høy.
    return (field_area_m2 <= 2500.0 and density >= 1.90) or (field_area_m2 <= 4500.0 and density >= 2.35)


def _karre_shape_support(poly: Any) -> float:
    area_m2 = float(getattr(poly, 'area', 0.0) or 0.0)
    major, minor = _minimum_rotated_dims(poly)
    if area_m2 < 2400.0:
        return 0.0
    if major >= 58.0 and minor >= 45.0:
        return 1.0
    if major >= 54.0 and minor >= 42.0:
        return 0.82
    if major >= 50.0 and minor >= 40.0:
        return 0.62
    return 0.0


def _lamell_field_fit_possible(poly: Any) -> bool:
    major, minor = _minimum_rotated_dims(poly)
    return major >= 48.0 and minor >= 16.0


def _fallback_typology_for_field(poly: Any, density: float) -> Typology:
    area_m2 = float(getattr(poly, 'area', 0.0) or 0.0)
    major, minor = _minimum_rotated_dims(poly)
    if area_m2 <= 3200.0 or major < 48.0 or minor < 18.0:
        return Typology.PUNKTHUS
    if density >= 2.0 and area_m2 <= 7000.0:
        return Typology.PUNKTHUS
    return Typology.LAMELL


def _karre_field_fit_possible(poly: Any) -> bool:
    return _karre_shape_support(poly) >= 0.60


def _count_bounds_for_field_area(field_area_m2: float, typology: Typology) -> Tuple[int, int]:
    min_area, max_area = _AREA_PER_BUILDING_RULES.get(typology, (1200.0, 2200.0))
    if field_area_m2 <= 0:
        return 0, 0

    if typology == Typology.KARRE:
        # Karré styres først og fremst av proporsjon, ikke bare av areal. Disse
        # intervallene er derfor anbefalingsspenn – ikke absolutte sannheter.
        if field_area_m2 < 2400.0:
            return 0, 0
        if field_area_m2 < 6000.0:
            return 0, 1
        if field_area_m2 < 12000.0:
            return 0, 2

    min_count = max(1, int(math.ceil(field_area_m2 / max_area)))
    if typology == Typology.KARRE:
        max_count = max(min_count, int(math.ceil(field_area_m2 / min_area)))
    else:
        max_count = max(min_count, int(math.floor(field_area_m2 / min_area)))
        if max_count == min_count and (field_area_m2 / max(min_area, 1.0)) > (max_count + 0.40):
            max_count += 1

    ceiling = _COUNT_CEILINGS.get(typology, 12)
    return min(min_count, ceiling), min(max_count, ceiling)


def _recommended_building_count(
    *,
    field_area_m2: float,
    typology: Typology,
    target_bra_m2: float,
    floors_range: Tuple[int, int],
    fallback_count: int,
) -> int:
    min_count, max_count = _count_bounds_for_field_area(field_area_m2, typology)
    if max_count <= 0:
        return 0

    mid_area = sum(_AREA_PER_BUILDING_RULES.get(typology, (1200.0, 2200.0))) / 2.0
    target = max(min_count, int(round(field_area_m2 / max(mid_area, 1.0))))

    density = float(target_bra_m2 or 0.0) / max(field_area_m2, 1.0)
    fmin, fmax = floors_range
    avg_f = max(1.0, (float(fmin) + float(fmax)) / 2.0)
    if typology == Typology.LAMELL:
        typical_bra_per_building = 60.0 * 13.0 * avg_f
    elif typology == Typology.PUNKTHUS:
        typical_bra_per_building = 20.0 * 20.0 * avg_f
    elif typology == Typology.KARRE:
        typical_bra_per_building = 1600.0 * avg_f
    else:
        typical_bra_per_building = 6.5 * 10.0 * avg_f

    if typology == Typology.KARRE:
        # Runde 2 tetthet: et karré-alternativ skal leses som kvartalsstruktur,
        # ikke som tre isolerte objekter. På store felt og ved høy %-BRA må vi
        # derfor starte tidligere med 3-6 karré-/U-volumer.
        if field_area_m2 < 4200.0:
            target = 1 if density < 1.75 else 2
        elif field_area_m2 < 7200.0:
            target = 2 if density < 1.65 else 3
        elif field_area_m2 < 11000.0:
            target = 3 if density < 1.85 else 4
        elif field_area_m2 < 16500.0:
            target = 4 if density < 1.75 else 5
        elif field_area_m2 < 24000.0:
            target = 5 if density < 1.60 else 6
        else:
            target = max(5, int(round(field_area_m2 / 4700.0)))
            if density >= 1.25 and target < max_count:
                target += 1
        if fallback_count > 0:
            target = max(target, min(fallback_count, max_count))
        return max(min_count, min(max_count, target))

    if typical_bra_per_building > 0:
        bra_based = max(1, int(round(float(target_bra_m2 or 0.0) / typical_bra_per_building)))
        target = max(target, bra_based)

    if density >= 1.18 and target < max_count:
        target += 1
    elif density <= 0.72 and target > min_count:
        target -= 1

    # Høy %-BRA på stor tomt må gi flere bygg, ikke bare høyere etasjer.
    if typology == Typology.LAMELL:
        if field_area_m2 >= 7000.0:
            baseline = 4 if density >= 0.90 else 3
            if density >= 1.10:
                baseline = 5
            if field_area_m2 >= 14000.0 and density >= 1.15:
                baseline = 6
            if field_area_m2 >= 22000.0 and density >= 1.20:
                baseline = 7
            target = max(target, min(max_count, baseline))
        if density >= 1.35 and target < max_count:
            target += 1
    elif typology == Typology.PUNKTHUS:
        if field_area_m2 >= 6000.0:
            baseline = 4 if density >= 0.90 else 3
            if density >= 1.10:
                baseline = 5
            if field_area_m2 >= 14000.0 and density >= 1.15:
                baseline = 6
            if field_area_m2 >= 22000.0 and density >= 1.25:
                baseline = 7
            target = max(target, min(max_count, baseline))
        if density >= 1.30 and target < max_count:
            target += 1

    if fallback_count > 0:
        if density >= 1.0 or field_area_m2 <= mid_area * 1.35:
            target = max(target, min(fallback_count, max_count))

    return max(min_count, min(max_count, target))


def _scaled_micro_band_count(base_count: int, typology: Typology, target_count: int) -> int:
    base = max(1, int(base_count or 0))
    if typology == Typology.LAMELL:
        return max(base, min(12, int(math.ceil(target_count / 2.0)) + 2))
    if typology == Typology.PUNKTHUS:
        return max(base, min(8, int(math.ceil(target_count / 2.5)) + 1))
    if typology == Typology.KARRE:
        return max(base, min(8, target_count + 1))
    return max(base, target_count)


def _scaled_view_corridor_count(base_count: int, typology: Typology, target_count: int) -> int:
    base = max(0, int(base_count or 0))
    if typology == Typology.LAMELL:
        return max(base, min(3, int(math.ceil(target_count / 3.0))))
    if typology == Typology.PUNKTHUS:
        return max(base, min(3, int(math.ceil(target_count / 4.0))))
    if typology == Typology.KARRE:
        return max(base, min(2, max(0, target_count - 1)))
    return base


def _scaled_node_layout_mode(base_mode: Optional[str], typology: Typology, target_count: int) -> Optional[str]:
    if typology != Typology.PUNKTHUS:
        return base_mode
    if target_count >= 7:
        return 'perimeter_ring_dense'
    if target_count >= 5:
        return 'perimeter_ring'
    return base_mode or 'paired_edges'


class ConceptStrategy:
    family: ConceptFamily
    ui_label: str
    fallback_title: str
    fallback_tagline: str

    def envelope_for_field(self, index: int, count: int, field: Optional[Delfelt] = None) -> FieldEnvelope:
        raise NotImplementedError

    def _clamp_floors(self, floors_range: Tuple[int, int], plan_regler: Optional[PlanRegler]) -> Tuple[int, int]:
        fmin, fmax = floors_range
        if plan_regler and plan_regler.max_floors is not None:
            fmax = min(fmax, int(plan_regler.max_floors))
            fmin = min(fmin, fmax)
        return max(1, fmin), max(1, fmax)

    def _field_rationale(self, index: int, count: int, env: FieldEnvelope) -> str:
        return f"{self.family.value}: {env.default_typology.value} i felt {index + 1} av {count}."

    def _make_choices(self, delfelt: Sequence[Delfelt], target_bra_m2: float, plan_regler: Optional[PlanRegler], shares: Sequence[float]) -> List[FieldParameterChoice]:
        out: List[FieldParameterChoice] = []
        for idx, field in enumerate(delfelt):
            env = self.envelope_for_field(idx, len(delfelt), field)
            floors_min, floors_max = self._clamp_floors(env.default_floors, plan_regler)
            field_target_bra = float(target_bra_m2 * shares[idx])
            field_area_m2 = float(getattr(field.polygon, 'area', 0.0) or 0.0)
            density = field_target_bra / max(field_area_m2, 1.0)
            compact_infill = _is_compact_infill(field_area_m2, field_target_bra)
            karre_support = _karre_shape_support(field.polygon)

            selected_typology = env.default_typology
            tower_size_m = env.tower_size_m
            design_variant = env.design_variant
            design_karre_shape = env.design_karre_shape
            design_height_pattern = env.design_height_pattern
            target_bya_pct = env.target_bya_pct
            skeleton_mode = env.skeleton_mode
            frontage_mode = env.frontage_mode
            micro_band_count_base = env.micro_band_count
            view_corridor_count_base = env.view_corridor_count
            courtyard_reserve_ratio = env.courtyard_reserve_ratio
            frontage_depth_m = env.frontage_depth_m
            corridor_width_m = env.corridor_width_m
            central_void_m = env.central_void_m
            gap_between_m = env.gap_between_m
            macro_structure = env.macro_structure
            micro_field_pattern = env.micro_field_pattern
            symmetry_preference = env.symmetry_preference
            composition_strictness = env.composition_strictness
            frontage_zone_ratio = env.frontage_zone_ratio
            public_realm_ratio = env.public_realm_ratio
            node_symmetry = env.node_symmetry
            frontage_primary_side = env.frontage_primary_side
            frontage_secondary_side = env.frontage_secondary_side
            lamell_rhythm_mode = env.lamell_rhythm_mode
            node_layout_mode = env.node_layout_mode
            courtyard_open_side = env.courtyard_open_side
            rationale = self._field_rationale(idx, len(delfelt), env)

            if selected_typology == Typology.KARRE:
                if karre_support <= 0.0:
                    selected_typology = _fallback_typology_for_field(field.polygon, density)
                    rationale += " Feltformen er for smal eller for liten for et proporsjonalt karrégrep; feltet faller derfor tilbake til lamell/punkthus."
                elif field_area_m2 < 8_000.0 and karre_support < 0.82 and density < 1.65:
                    selected_typology = _fallback_typology_for_field(field.polygon, density)
                    rationale += " Under ca. 10 000 m² får lamell/punkthus normalt prioritet; karré beholdes bare når både form og tetthet støtter det tydelig."
                else:
                    rationale += " Karré beholdes fordi delfeltet faktisk har nok bredde og dybde til å bære et proporsjonalt kvartalsgrep."
                    if field_area_m2 < 6_000.0:
                        design_karre_shape = 'u'
                        courtyard_open_side = courtyard_open_side or 'south'

            if selected_typology == Typology.LAMELL and not _lamell_field_fit_possible(field.polygon):
                selected_typology = Typology.PUNKTHUS
                rationale += " Feltet er for kompakt for en troverdig lamell; punkthus velges i stedet."

            target_building_count = _recommended_building_count(
                field_area_m2=field_area_m2,
                typology=selected_typology,
                target_bra_m2=field_target_bra,
                floors_range=(floors_min, floors_max),
                fallback_count=env.target_building_count,
            )

            # Runde 8.1: arealheuristikken alene ga for få bygg per delfelt på
            # store masterplan-tomter. Bruk konseptstrategiens mål som nedre
            # komposisjonsambisjon, men hold igjen på små infillfelt.
            if not compact_infill and env.target_building_count:
                if selected_typology == Typology.LAMELL:
                    cap = 2 if field_area_m2 < 2500.0 else (3 if field_area_m2 < 5200.0 else env.target_building_count)
                    target_building_count = max(target_building_count, min(env.target_building_count, cap))
                elif selected_typology == Typology.PUNKTHUS:
                    cap = 2 if field_area_m2 < 2500.0 else (3 if field_area_m2 < 5200.0 else env.target_building_count)
                    target_building_count = max(target_building_count, min(env.target_building_count, cap))
                elif selected_typology == Typology.KARRE and field_area_m2 >= 4800.0:
                    target_building_count = max(target_building_count, min(env.target_building_count, 5))

            if selected_typology == Typology.KARRE and field_area_m2 < 3_400.0 and density < 1.75:
                target_building_count = 1

            if selected_typology == Typology.KARRE:
                # Høy tetthet på mellomstore felt må kunne gi flere karréer
                # med et felles uterom imellom, ellers kollapser BRA og planen
                # leses som to isolerte objektbygg.
                if field_area_m2 < 5_200.0:
                    target_building_count = min(max(target_building_count, 1), 1 if density < 2.15 else 2)
                elif field_area_m2 < 9_000.0:
                    target_building_count = min(max(target_building_count, 2), 3 if density >= 1.75 else 2)
                elif field_area_m2 < 14_000.0:
                    target_building_count = min(max(target_building_count, 2), 4 if density >= 1.65 else 3)
                elif field_area_m2 < 22_000.0:
                    target_building_count = min(max(target_building_count, 3), 5)
            elif compact_infill and selected_typology in {Typology.LAMELL, Typology.PUNKTHUS}:
                target_building_count = max(1, min(target_building_count, 2 if field_area_m2 <= 3_500.0 else 3))

            # Runde 2: tetthetsstyrt kvalitetslag. Når brukeren setter høy %-BRA
            # på store felt, skal planen faktisk forsøke å nå målet gjennom flere
            # bygg, tettere kanter og tydelige uterom – ikke la store restflater bli
            # ubebygd. Småhusnære felt holdes noe mer tilbake.
            field_character = env.character or field.character
            field_role_effective = env.field_role or field.field_role
            large_plot = field_area_m2 >= 7_000.0
            dense_target = density >= 1.05
            very_dense_target = density >= 1.25
            smallhouse_edge = field_character == 'neighborhood_edge'
            urban_edge_like = field_role_effective in {'urban_edge', 'street_edge', 'urban_core', 'linear_mixed_core'} or not smallhouse_edge
            if not compact_infill and large_plot and dense_target:
                composition_strictness = max(composition_strictness, 0.90)
                if selected_typology == Typology.LAMELL:
                    lamell_min = 5 if field_area_m2 < 14_000.0 else 6
                    if very_dense_target and field_area_m2 >= 20_000.0:
                        lamell_min = 7
                    if smallhouse_edge:
                        lamell_min = max(4, lamell_min - 1)
                    target_building_count = max(target_building_count, lamell_min)
                    target_bya_pct = max(float(target_bya_pct or 0.0), 30.0 if urban_edge_like else 26.0)
                    courtyard_reserve_ratio = min(float(courtyard_reserve_ratio or 0.0), 0.16 if urban_edge_like else 0.22)
                    public_realm_ratio = min(float(public_realm_ratio or 0.0), 0.12 if urban_edge_like else 0.16)
                    frontage_mode = frontage_mode or 'double'
                    # Økt 1: green-room-first. Ved høy utnyttelse skal lameller
                    # ikke bare fylle feltet i parallelle rekker; de skal kranse
                    # et hovedrom som kan leses som felles grønt uterom.
                    macro_structure = 'room_frame_masterplan'
                    micro_field_pattern = 'room_frame_edges'
                    lamell_rhythm_mode = lamell_rhythm_mode or 'staggered'
                    courtyard_reserve_ratio = min(float(courtyard_reserve_ratio or 0.0), 0.14 if urban_edge_like else 0.18)
                    public_realm_ratio = min(float(public_realm_ratio or 0.0), 0.11 if urban_edge_like else 0.15)
                    rationale += " Høy utnyttelse gir flere lameller som kranser ett tydelig grønt hovedrom i stedet for å spre restareal."
                elif selected_typology == Typology.PUNKTHUS:
                    tower_min = 5 if field_area_m2 < 14_000.0 else 6
                    if very_dense_target and field_area_m2 >= 20_000.0:
                        tower_min = 7
                    if smallhouse_edge:
                        tower_min = max(4, tower_min - 1)
                    target_building_count = max(target_building_count, tower_min)
                    target_bya_pct = max(float(target_bya_pct or 0.0), 28.0 if urban_edge_like else 24.0)
                    courtyard_reserve_ratio = min(float(courtyard_reserve_ratio or 0.0), 0.18 if urban_edge_like else 0.22)
                    public_realm_ratio = min(float(public_realm_ratio or 0.0), 0.13 if urban_edge_like else 0.17)
                    macro_structure = 'room_frame_masterplan'
                    node_layout_mode = 'perimeter_ring_dense' if target_building_count >= 6 else (node_layout_mode or 'perimeter_ring')
                    courtyard_reserve_ratio = min(float(courtyard_reserve_ratio or 0.0), 0.18 if urban_edge_like else 0.22)
                    public_realm_ratio = min(float(public_realm_ratio or 0.0), 0.13 if urban_edge_like else 0.17)
                    rationale += " Høy utnyttelse gir punkthus langs en grønn ring, slik at tårnene definerer fellesrommet tydeligere."
                elif selected_typology == Typology.KARRE:
                    karre_min = 3 if field_area_m2 < 12_000.0 else 4
                    if very_dense_target and field_area_m2 >= 18_000.0:
                        karre_min = 5
                    if smallhouse_edge:
                        karre_min = max(2, karre_min - 1)
                    target_building_count = max(target_building_count, karre_min)
                    target_bya_pct = max(float(target_bya_pct or 0.0), 38.0 if urban_edge_like else 32.0)
                    # Arbeidsøkt 4: større og tydeligere uterom uten å miste BRA.
                    # Vi reserverer et reelt hovedrom, men bruker perimeterbygg og
                    # etasjer for å hente volumet tilbake langs robuste kanter.
                    courtyard_reserve_ratio = max(0.20 if urban_edge_like else 0.24, min(float(courtyard_reserve_ratio or 0.0), 0.25 if urban_edge_like else 0.29))
                    public_realm_ratio = max(0.13 if urban_edge_like else 0.16, min(float(public_realm_ratio or 0.0), 0.18 if urban_edge_like else 0.21))
                    central_void_m = max(22.0, min(float(central_void_m or 22.0), 26.0))
                    gap_between_m = max(8.0, min(float(gap_between_m or 8.0), 12.0))
                    macro_structure = 'room_frame_masterplan'
                    micro_field_pattern = 'quarter_field_edges'
                    rationale += " Høy utnyttelse løses som kvartalsfelt: kompakte karré-/blokkvolumer og smalere lamellkanter kranser felles grønne rom og robuste tomtekanter."
                if urban_edge_like and not smallhouse_edge:
                    floors_max = max(floors_max, min(floors_max + 1, 8))

            micro_band_count = _scaled_micro_band_count(micro_band_count_base, selected_typology, max(target_building_count, 1))
            view_corridor_count = _scaled_view_corridor_count(view_corridor_count_base, selected_typology, max(target_building_count, 1))
            node_layout_mode = _scaled_node_layout_mode(node_layout_mode, selected_typology, max(target_building_count, 1))

            if compact_infill:
                target_bya_pct = max(float(target_bya_pct or 0.0), min(88.0, 60.0 + density * 8.0))
                courtyard_reserve_ratio = min(float(courtyard_reserve_ratio or 0.0), 0.08)
                public_realm_ratio = min(float(public_realm_ratio or 0.0), 0.10)
                view_corridor_count = 0
                frontage_secondary_side = None
                composition_strictness = max(composition_strictness, 0.95)
                if selected_typology == Typology.LAMELL:
                    frontage_mode = 'single'
                    micro_field_pattern = 'parallel_bands'
                    micro_band_count = max(1, min(micro_band_count, 3))
                    rationale += " Feltet behandles som kompakt infill; volumet får prioritet foran gårdsrom og store MUA-reserver."
                elif selected_typology == Typology.PUNKTHUS:
                    node_layout_mode = 'paired_edges' if max(target_building_count, 1) <= 2 else (node_layout_mode or 'corners')
                    public_realm_ratio = min(public_realm_ratio, 0.06)
                    rationale += " Feltet behandles som kompakt infill; parkrom og korridorer nedtones for å gi høy tetthet."
                elif selected_typology == Typology.KARRE:
                    target_building_count = 1
                    design_karre_shape = 'u'
                    rationale += " Karré tolkes som tett infill-grep med begrenset gårdsrom, ikke som stort åpent kvartal."

            if selected_typology == Typology.LAMELL and target_building_count >= 4:
                design_variant = design_variant or 'rhythmic'
                if macro_structure == 'room_frame_masterplan':
                    micro_field_pattern = 'room_frame_edges'
                    rationale += f" Feltet rommer {target_building_count} lameller; de organiseres som romkanter rundt et hoveduterom framfor bare parallelle rekker."
                elif target_building_count >= 6:
                    micro_field_pattern = 'dense_parallel_bands'
                    rationale += f" Feltet rommer {target_building_count} lameller; komposisjonen vris derfor mot rytmisk radstruktur."
                else:
                    rationale += f" Feltet rommer {target_building_count} lameller; komposisjonen vris derfor mot rytmisk radstruktur."
                composition_strictness = min(0.97, max(composition_strictness, 0.88))
            elif selected_typology == Typology.PUNKTHUS:
                tower_size_m = 20 if target_building_count >= 1 else tower_size_m
                if target_building_count >= 5:
                    rationale += f" Feltet prioriteres som fler-nodig punktfelt med {target_building_count} tårn."
            elif selected_typology == Typology.KARRE and target_building_count >= 2:
                micro_field_pattern = micro_field_pattern or 'clustered_frontage_ring'
                # Arbeidsøkt 2: flere karréer skal ikke gi tynne armer og små restgårdsrom.
                # Vi reserverer derfor litt større hovedrom og lar BYA-målet ta volumtrykket.
                if central_void_m <= 0.0:
                    central_void_m = 26.0 if target_building_count >= 4 else 20.0
                else:
                    central_void_m = max(float(central_void_m), 24.0 if target_building_count >= 4 else 19.0)
                gap_between_m = max(9.0, float(gap_between_m or 9.0))
                courtyard_reserve_ratio = max(float(courtyard_reserve_ratio or 0.0), 0.24 if target_building_count >= 4 else 0.22)
                target_bya_pct = max(float(target_bya_pct or 0.0), 40.0 if target_building_count >= 3 else 36.0)
                macro_structure = 'room_frame_masterplan'
                micro_field_pattern = 'quarter_field_edges'
                rationale += f" Feltet vurderes som kvartalsfelt med {target_building_count} romkantbygg: kompakte bygg beholdes som karré, mens smale romkanter behandles som lamell/blokk."

            out.append(FieldParameterChoice(
                field_id=field.field_id,
                typology=selected_typology,
                orientation_deg=(field.orientation_deg + env.default_orientation_offset_deg) % 180.0,
                floors_min=floors_min,
                floors_max=floors_max,
                target_bra=field_target_bra,
                courtyard_kind=env.courtyard_kind,
                tower_size_m=tower_size_m,
                rationale=rationale,
                field_role=env.field_role or field.field_role,
                character=env.character or field.character,
                arm_id=field.arm_id,
                design_variant=design_variant,
                design_karre_shape=design_karre_shape,
                design_height_pattern=design_height_pattern,
                target_bya_pct=target_bya_pct,
                skeleton_mode=skeleton_mode,
                frontage_mode=frontage_mode,
                micro_band_count=micro_band_count,
                view_corridor_count=view_corridor_count,
                courtyard_reserve_ratio=courtyard_reserve_ratio,
                frontage_depth_m=frontage_depth_m,
                corridor_width_m=corridor_width_m,
                central_void_m=central_void_m,
                gap_between_m=gap_between_m,
                macro_structure=macro_structure,
                micro_field_pattern=micro_field_pattern,
                symmetry_preference=symmetry_preference,
                composition_strictness=composition_strictness,
                frontage_zone_ratio=frontage_zone_ratio,
                public_realm_ratio=public_realm_ratio,
                node_symmetry=node_symmetry,
                frontage_primary_side=frontage_primary_side,
                frontage_secondary_side=frontage_secondary_side,
                lamell_rhythm_mode=lamell_rhythm_mode,
                node_layout_mode=node_layout_mode,
                courtyard_open_side=courtyard_open_side,
                target_building_count=target_building_count,
                frontage_emphasis=env.frontage_emphasis,
                rhythm_strength=env.rhythm_strength,
            ))
        return out

    def propose(self, delfelt: Sequence[Delfelt], target_bra_m2: float, plan_regler: Optional[PlanRegler] = None, neighbors: Optional[Sequence[dict]] = None) -> List[FieldParameterChoice]:
        raise NotImplementedError


class LinearMixedStrategy(ConceptStrategy):
    family = ConceptFamily.LINEAR_MIXED
    ui_label = "Lineært blandet grep"
    fallback_title = "Lineært blandet boliggrep"
    fallback_tagline = "Lameller danner hovedstrukturen, mens punktbygg og karré gir variasjon rundt den interne byaksen."

    def _area_shares(self, count: int) -> List[float]:
        if count <= 1:
            return [1.0]
        if count == 2:
            return [0.48, 0.52]
        if count == 3:
            return [0.24, 0.52, 0.24]
        if count == 4:
            return [0.16, 0.30, 0.34, 0.20]
        if count == 5:
            return [0.11, 0.21, 0.34, 0.22, 0.12]
        if count == 6:
            return [0.09, 0.16, 0.24, 0.24, 0.17, 0.10]
        edge = 0.08
        center = 0.22
        remaining = max(0.0, 1.0 - 2 * edge - center)
        shoulders = max(0, count - 3)
        shoulder_share = remaining / max(shoulders, 1)
        out = [edge]
        for _ in range(shoulders // 2):
            out.append(shoulder_share)
        out.append(center)
        while len(out) < count - 1:
            out.append(shoulder_share)
        out.append(edge)
        return out[:count]

    def envelope_for_field(self, index: int, count: int, field: Optional[Delfelt] = None) -> FieldEnvelope:
        edge = index == 0 or index == count - 1
        center_index = count // 2
        # Force a real typological mix: one central karré/courtyard field and
        # one edge/node field with punkthus where the macro subdivision allows it.
        core = index == center_index and count >= 3
        near_core = abs(index - center_index) <= 1
        use_karre = core
        use_punkthus = (index == 0 and count >= 4) or (edge and count >= 5)
        default_typology = Typology.KARRE if use_karre else (Typology.PUNKTHUS if use_punkthus else Typology.LAMELL)
        field_role = "linear_mixed_core" if use_karre else ("linear_node" if use_punkthus else ("linear_edge" if edge else "linear_band"))
        return FieldEnvelope(
            allowed_typologies=(Typology.LAMELL, Typology.PUNKTHUS, Typology.KARRE),
            default_typology=default_typology,
            default_orientation_offset_deg=0.0,
            default_floors=((5, 6) if default_typology == Typology.LAMELL else ((5, 7) if default_typology == Typology.PUNKTHUS else (5, 6))),
            courtyard_kind=(CourtyardKind.URBAN_TORG if use_karre else CourtyardKind.FELLES_BOLIG),
            tower_size_m=(20 if use_punkthus else None),
            field_role=field_role,
            character=("street_facing" if near_core or use_karre else "open_view"),
            design_variant=(None if (use_karre or use_punkthus) else ("rhythmic" if near_core else "terraced")),
            design_karre_shape=("uo" if use_karre else None),
            design_height_pattern=("accent" if use_punkthus else ("stepped" if near_core else "neighbor_step_down")),
            target_bya_pct=(33.0 if use_karre else (27.0 if use_punkthus else (35.0 if near_core else 32.0))),
            skeleton_mode=("courtyard_frontage" if use_karre else ("park_nodes" if use_punkthus else ("linear_bands_dense" if count >= 4 else "linear_bands"))),
            frontage_mode=("ring" if use_karre else ("node" if use_punkthus else ("double" if near_core else "single"))),
            micro_band_count=(4 if use_karre else (0 if use_punkthus else (7 if near_core else 5))),
            view_corridor_count=(1 if use_karre else (1 if edge else 2)),
            courtyard_reserve_ratio=(0.24 if use_karre else (0.22 if use_punkthus else (0.10 if near_core else 0.08))),
            frontage_depth_m=(14.0 if use_karre else (12.0 if use_punkthus else 13.0)),
            corridor_width_m=7.2,
            central_void_m=(18.0 if use_karre else 0.0),
            gap_between_m=9.0,
            macro_structure=("room_frame_masterplan" if (use_karre or not use_punkthus) else "park_cluster"),
            micro_field_pattern=("quarter_field_edges" if use_karre else ("node_cluster" if use_punkthus else "room_frame_edges")),
            symmetry_preference=("axial" if use_karre else "bilateral"),
            composition_strictness=(0.985 if use_karre else 0.96),
            frontage_zone_ratio=(0.30 if use_karre else 0.24),
            public_realm_ratio=(0.16 if use_karre else (0.18 if use_punkthus else 0.12)),
            node_symmetry=(use_punkthus or use_karre),
            frontage_primary_side="south",
            frontage_secondary_side=("west" if use_karre or near_core else None),
            lamell_rhythm_mode=(None if default_typology != Typology.LAMELL else ("mirrored" if near_core else "paired")),
            node_layout_mode=("green_room_ring" if use_punkthus else None),
            courtyard_open_side=("south" if use_karre else None),
            target_building_count=(1 if use_karre else (3 if use_punkthus else (5 if near_core else 4))),
            frontage_emphasis=(0.99 if use_karre else 0.95),
            rhythm_strength=(0.94 if use_punkthus else 0.98),
        )

    def propose(self, delfelt: Sequence[Delfelt], target_bra_m2: float, plan_regler: Optional[PlanRegler] = None, neighbors: Optional[Sequence[dict]] = None) -> List[FieldParameterChoice]:
        del neighbors
        return self._make_choices(delfelt, target_bra_m2, plan_regler, self._area_shares(len(delfelt)))

class CourtyardUrbanStrategy(ConceptStrategy):
    family = ConceptFamily.COURTYARD_URBAN
    ui_label = "Urban kvartalsstruktur"
    fallback_title = "Urban kvartalsstruktur med gårdsrom"
    fallback_tagline = "Sterkere kvartalsgrep med større gårdsrom, sørvestvendt orientering og høyere total arealutnyttelse."

    def _area_shares(self, count: int) -> List[float]:
        if count <= 1:
            return [1.0]
        if count == 2:
            return [0.52, 0.48]
        if count == 3:
            return [0.34, 0.36, 0.30]
        if count == 4:
            return [0.28, 0.28, 0.24, 0.20]
        if count == 5:
            return [0.22, 0.22, 0.21, 0.19, 0.16]
        base = [0.15] * count
        for i in range(min(4, count)):
            base[i] += 0.025
        s = sum(base)
        return [b / s for b in base]

    def envelope_for_field(self, index: int, count: int, field: Optional[Delfelt] = None) -> FieldEnvelope:
        role = field.field_role if field else "urban_core"
        edge = role in {"street_edge", "urban_edge"} or index in {0, count - 1}
        field_area = float(getattr(getattr(field, "polygon", None), "area", 0.0) or 0.0)
        use_karre = field_area >= 3_500.0 or not edge
        if role == "neighborhood_edge" and field_area < 4_000.0:
            use_karre = False
        karre_count = 1
        if use_karre and field_area >= 4_800.0:
            karre_count = 2
        if use_karre and field_area >= 8_500.0:
            karre_count = 3
        if use_karre and field_area >= 14_000.0:
            karre_count = 4
        if use_karre and field_area >= 22_000.0:
            karre_count = 5

        return FieldEnvelope(
            allowed_typologies=(Typology.KARRE, Typology.LAMELL),
            default_typology=Typology.KARRE if use_karre else Typology.LAMELL,
            default_orientation_offset_deg=0.0,
            default_floors=(5, 7) if edge else (5, 6),
            courtyard_kind=CourtyardKind.URBAN_TORG if edge else CourtyardKind.FELLES_BOLIG,
            field_role=("urban_edge" if edge else "urban_core"),
            character=("street_facing" if edge else "sheltered"),
            design_karre_shape=("uo_chamfered" if edge else "uo"),
            design_height_pattern=("neighbor_step_down" if (field and field.character == "neighborhood_edge") else "stepped"),
            design_variant=(None if use_karre else "terraced"),
            target_bya_pct=(42.0 if karre_count >= 3 else (40.0 if karre_count >= 2 else 36.0)),
            skeleton_mode=("courtyard_frontage" if use_karre else "linear_bands"),
            frontage_mode=("quad" if edge and use_karre else ("ring" if use_karre else "double")),
            micro_band_count=(7 if karre_count >= 4 else 6 if karre_count >= 2 else 5),
            view_corridor_count=(1 if karre_count <= 2 else 2),
            courtyard_reserve_ratio=(0.30 if edge else 0.32),
            frontage_depth_m=(15.0 if use_karre else 13.0),
            corridor_width_m=7.5,
            central_void_m=(26.0 if karre_count >= 4 else (20.0 if karre_count >= 2 else 0.0)),
            gap_between_m=9.0,
            macro_structure="room_frame_masterplan",
            micro_field_pattern=("quarter_field_edges" if karre_count >= 2 else ("room_frame_edges" if use_karre else "parallel_bands")),
            symmetry_preference="axial",
            composition_strictness=0.988,
            frontage_zone_ratio=0.30,
            public_realm_ratio=0.21,
            node_symmetry=True,
            frontage_primary_side="south",
            frontage_secondary_side="west",
            courtyard_open_side=("west" if edge else "south"),
            target_building_count=(karre_count if use_karre else (4 if field_area >= 6_000.0 else 3)),
            frontage_emphasis=0.99,
            rhythm_strength=0.90,
        )

    def propose(self, delfelt: Sequence[Delfelt], target_bra_m2: float, plan_regler: Optional[PlanRegler] = None, neighbors: Optional[Sequence[dict]] = None) -> List[FieldParameterChoice]:
        del neighbors
        return self._make_choices(delfelt, target_bra_m2, plan_regler, self._area_shares(len(delfelt)))

class ClusterParkStrategy(ConceptStrategy):
    family = ConceptFamily.CLUSTER_PARK
    ui_label = "Klynger rundt park"
    fallback_title = "Boligklynger rundt grønt fellesrom"
    fallback_tagline = "Systematiske klynger av lameller og punkthus rundt ett tydelig, sammenhengende grønt fellesrom."

    def _area_shares(self, count: int) -> List[float]:
        if count <= 1:
            return [1.0]
        if count == 2:
            return [0.48, 0.52]
        if count == 3:
            return [0.27, 0.46, 0.27]
        if count == 4:
            return [0.22, 0.28, 0.28, 0.22]
        if count == 5:
            return [0.16, 0.23, 0.22, 0.23, 0.16]
        edge = 0.10
        middle_total = 1.0 - 2 * edge
        middle = count - 2
        mid_share = middle_total / max(middle, 1)
        return [edge] + [mid_share] * middle + [edge]

    def envelope_for_field(self, index: int, count: int, field: Optional[Delfelt] = None) -> FieldEnvelope:
        center_like = (index == count // 2) if count <= 3 else index in {max(0, count // 2 - 1), count // 2}
        use_punkthus = center_like and count >= 3
        return FieldEnvelope(
            allowed_typologies=(Typology.LAMELL, Typology.PUNKTHUS),
            default_typology=Typology.PUNKTHUS if use_punkthus else Typology.LAMELL,
            default_orientation_offset_deg=90.0 if (not use_punkthus and index % 2) else 0.0,
            default_floors=(6, 7) if use_punkthus else (5, 6),
            courtyard_kind=CourtyardKind.PARKKANT,
            tower_size_m=20 if use_punkthus else None,
            field_role=("park_node" if use_punkthus else "park_edge"),
            character=("open_view" if use_punkthus else "sheltered"),
            design_variant=(None if use_punkthus else "varied"),
            design_height_pattern=("accent" if use_punkthus else "stepped"),
            target_bya_pct=(27.0 if use_punkthus else 31.0),
            skeleton_mode=("park_nodes" if use_punkthus else "park_bands"),
            frontage_mode=("node" if use_punkthus else "edge"),
            micro_band_count=(0 if use_punkthus else 4),
            view_corridor_count=(1 if use_punkthus else 2),
            courtyard_reserve_ratio=(0.23 if use_punkthus else 0.18),
            frontage_depth_m=(12.0 if use_punkthus else 13.0),
            corridor_width_m=6.4,
            macro_structure="room_frame_masterplan",
            micro_field_pattern=("node_cluster" if use_punkthus else "room_frame_edges"),
            symmetry_preference="axial",
            composition_strictness=0.96,
            frontage_zone_ratio=0.18,
            public_realm_ratio=0.18,
            node_symmetry=use_punkthus,
            frontage_primary_side=(None if use_punkthus else "south"),
            frontage_secondary_side=(None if use_punkthus else "west"),
            node_layout_mode=("green_room_ring" if use_punkthus else "green_room_edges"),
            target_building_count=(5 if use_punkthus else 5),
            frontage_emphasis=0.90,
            rhythm_strength=0.88,
        )

    def propose(self, delfelt: Sequence[Delfelt], target_bra_m2: float, plan_regler: Optional[PlanRegler] = None, neighbors: Optional[Sequence[dict]] = None) -> List[FieldParameterChoice]:
        del neighbors
        return self._make_choices(delfelt, target_bra_m2, plan_regler, self._area_shares(len(delfelt)))

STRATEGIES: Dict[ConceptFamily, ConceptStrategy] = {
    ConceptFamily.LINEAR_MIXED: LinearMixedStrategy(),
    ConceptFamily.COURTYARD_URBAN: CourtyardUrbanStrategy(),
    ConceptFamily.CLUSTER_PARK: ClusterParkStrategy(),
}


def get_strategy(family: ConceptFamily) -> ConceptStrategy:
    return STRATEGIES[family]


def all_concept_families() -> List[ConceptFamily]:
    return [ConceptFamily.LINEAR_MIXED, ConceptFamily.COURTYARD_URBAN, ConceptFamily.CLUSTER_PARK]
