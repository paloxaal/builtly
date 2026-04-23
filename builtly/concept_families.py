from __future__ import annotations

"""Concept-family strategies for Builtly.

These strategies do not place geometry themselves; they define a strong
architectural envelope that the deterministic geometry pass must follow.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

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
            out.append(FieldParameterChoice(
                field_id=field.field_id,
                typology=env.default_typology,
                orientation_deg=(field.orientation_deg + env.default_orientation_offset_deg) % 180.0,
                floors_min=floors_min,
                floors_max=floors_max,
                target_bra=float(target_bra_m2 * shares[idx]),
                courtyard_kind=env.courtyard_kind,
                tower_size_m=env.tower_size_m,
                rationale=self._field_rationale(idx, len(delfelt), env),
                field_role=env.field_role or field.field_role,
                character=env.character or field.character,
                arm_id=field.arm_id,
                design_variant=env.design_variant,
                design_karre_shape=env.design_karre_shape,
                design_height_pattern=env.design_height_pattern,
                target_bya_pct=env.target_bya_pct,
                skeleton_mode=env.skeleton_mode,
                frontage_mode=env.frontage_mode,
                micro_band_count=env.micro_band_count,
                view_corridor_count=env.view_corridor_count,
                courtyard_reserve_ratio=env.courtyard_reserve_ratio,
                frontage_depth_m=env.frontage_depth_m,
                corridor_width_m=env.corridor_width_m,
                macro_structure=env.macro_structure,
                micro_field_pattern=env.micro_field_pattern,
                symmetry_preference=env.symmetry_preference,
                composition_strictness=env.composition_strictness,
                frontage_zone_ratio=env.frontage_zone_ratio,
                public_realm_ratio=env.public_realm_ratio,
                node_symmetry=env.node_symmetry,
                frontage_primary_side=env.frontage_primary_side,
                frontage_secondary_side=env.frontage_secondary_side,
                lamell_rhythm_mode=env.lamell_rhythm_mode,
                node_layout_mode=env.node_layout_mode,
                courtyard_open_side=env.courtyard_open_side,
                target_building_count=env.target_building_count,
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
    fallback_tagline = "Lameller og få punkthus organisert langs tomtas hovedakse."

    def _area_shares(self, count: int) -> List[float]:
        if count <= 1:
            return [1.0]
        if count == 2:
            return [0.52, 0.48]
        if count == 3:
            return [0.28, 0.44, 0.28]
        if count == 4:
            return [0.20, 0.30, 0.30, 0.20]
        if count == 5:
            return [0.13, 0.24, 0.26, 0.24, 0.13]
        if count == 6:
            return [0.10, 0.18, 0.22, 0.22, 0.18, 0.10]
        edge = 0.07
        middle_total = 1.0 - 2 * edge
        middle = count - 2
        mid_share = middle_total / max(middle, 1)
        return [edge] + [mid_share] * middle + [edge]

    def envelope_for_field(self, index: int, count: int, field: Optional[Delfelt] = None) -> FieldEnvelope:
        edge = index == 0 or index == count - 1
        field_role = "linear_edge" if edge else "linear_band"
        return FieldEnvelope(
            allowed_typologies=(Typology.LAMELL, Typology.PUNKTHUS),
            default_typology=Typology.LAMELL,
            default_orientation_offset_deg=0.0,
            default_floors=(4, 6),
            courtyard_kind=CourtyardKind.FELLES_BOLIG,
            field_role=field_role,
            character=("open_view" if edge else "street_facing"),
            design_variant=("terraced" if edge else "rhythmic"),
            design_height_pattern=("neighbor_step_down" if edge else "stepped"),
            target_bya_pct=26.0,
            skeleton_mode="linear_bands",
            frontage_mode=("single" if edge else "double"),
            micro_band_count=(4 if edge else 5),
            view_corridor_count=(1 if edge else 2),
            courtyard_reserve_ratio=(0.08 if edge else 0.10),
            frontage_depth_m=12.5,
            corridor_width_m=8.5,
            macro_structure="spine",
            micro_field_pattern="parallel_bands",
            symmetry_preference="bilateral",
            composition_strictness=0.92,
            frontage_zone_ratio=0.22,
            public_realm_ratio=0.10,
            node_symmetry=True,
            frontage_primary_side="south",
            frontage_secondary_side=("north" if not edge else None),
            lamell_rhythm_mode=("paired" if edge else "mirrored"),
            target_building_count=(2 if edge else 3),
            frontage_emphasis=0.90,
            rhythm_strength=0.90,
        )

    def propose(self, delfelt: Sequence[Delfelt], target_bra_m2: float, plan_regler: Optional[PlanRegler] = None, neighbors: Optional[Sequence[dict]] = None) -> List[FieldParameterChoice]:
        del neighbors
        return self._make_choices(delfelt, target_bra_m2, plan_regler, self._area_shares(len(delfelt)))


class CourtyardUrbanStrategy(ConceptStrategy):
    family = ConceptFamily.COURTYARD_URBAN
    ui_label = "Urban kvartalsstruktur"
    fallback_title = "Urban kvartalsstruktur med gårdsrom"
    fallback_tagline = "Karré-dominerte kvartaler med tydelige kanter og gårdsrom."

    def _area_shares(self, count: int) -> List[float]:
        if count <= 1:
            return [1.0]
        if count == 2:
            return [0.54, 0.46]
        if count == 3:
            return [0.36, 0.34, 0.30]
        if count == 4:
            return [0.28, 0.28, 0.24, 0.20]
        if count == 5:
            return [0.22, 0.22, 0.20, 0.20, 0.16]
        base = [0.16] * count
        for i in range(min(3, count)):
            base[i] += 0.02
        s = sum(base)
        return [b / s for b in base]

    def envelope_for_field(self, index: int, count: int, field: Optional[Delfelt] = None) -> FieldEnvelope:
        role = field.field_role if field else "urban_core"
        edge = role in {"street_edge", "urban_edge"} or index in {0, count - 1}
        use_karre = not (count >= 7 and role == "neighborhood_edge" and index == count - 1)
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
            target_bya_pct=29.0,
            skeleton_mode="courtyard_frontage",
            frontage_mode=("quad" if edge else "ring"),
            micro_band_count=4,
            view_corridor_count=(1 if edge else 0),
            courtyard_reserve_ratio=(0.28 if edge else 0.32),
            frontage_depth_m=13.5,
            corridor_width_m=8.0,
            macro_structure="perimeter_block",
            micro_field_pattern="frontage_ring",
            symmetry_preference="axial",
            composition_strictness=0.98,
            frontage_zone_ratio=0.26,
            public_realm_ratio=0.16,
            node_symmetry=True,
            frontage_primary_side=("south" if edge else None),
            frontage_secondary_side=("east" if edge else None),
            courtyard_open_side=("south" if edge else None),
            target_building_count=(1 if use_karre else 2),
            frontage_emphasis=0.96,
            rhythm_strength=0.82,
        )

    def propose(self, delfelt: Sequence[Delfelt], target_bra_m2: float, plan_regler: Optional[PlanRegler] = None, neighbors: Optional[Sequence[dict]] = None) -> List[FieldParameterChoice]:
        del neighbors
        return self._make_choices(delfelt, target_bra_m2, plan_regler, self._area_shares(len(delfelt)))


class ClusterParkStrategy(ConceptStrategy):
    family = ConceptFamily.CLUSTER_PARK
    ui_label = "Klynger rundt park"
    fallback_title = "Boligklynger rundt grønt fellesrom"
    fallback_tagline = "Lameller og enkelte punkthus rundt et sentralt grønt parkrom."

    def _area_shares(self, count: int) -> List[float]:
        if count <= 1:
            return [1.0]
        if count == 2:
            return [0.48, 0.52]
        if count == 3:
            return [0.26, 0.48, 0.26]
        if count == 4:
            return [0.18, 0.32, 0.32, 0.18]
        if count == 5:
            return [0.14, 0.24, 0.24, 0.24, 0.14]
        edge = 0.10
        middle_total = 1.0 - 2 * edge
        middle = count - 2
        mid_share = middle_total / max(middle, 1)
        return [edge] + [mid_share] * middle + [edge]

    def envelope_for_field(self, index: int, count: int, field: Optional[Delfelt] = None) -> FieldEnvelope:
        center_like = index in {max(0, count // 2 - 1), count // 2}
        use_punkthus = center_like and count >= 4
        return FieldEnvelope(
            allowed_typologies=(Typology.LAMELL, Typology.PUNKTHUS),
            default_typology=Typology.PUNKTHUS if use_punkthus else Typology.LAMELL,
            default_orientation_offset_deg=90.0 if (not use_punkthus and index % 2) else 0.0,
            default_floors=(5, 6) if use_punkthus else (4, 5),
            courtyard_kind=CourtyardKind.PARKKANT,
            tower_size_m=21 if use_punkthus else None,
            field_role=("park_node" if use_punkthus else "park_edge"),
            character=("open_view" if use_punkthus else "sheltered"),
            design_variant=(None if use_punkthus else "varied"),
            design_height_pattern=("accent" if use_punkthus else "stepped"),
            target_bya_pct=22.0,
            skeleton_mode=("park_nodes" if use_punkthus else "park_bands"),
            frontage_mode=("node" if use_punkthus else "edge"),
            micro_band_count=(0 if use_punkthus else 3),
            view_corridor_count=(2 if use_punkthus else 1),
            courtyard_reserve_ratio=0.40,
            frontage_depth_m=11.5,
            corridor_width_m=11.0,
            macro_structure="park_cluster",
            micro_field_pattern=("node_cluster" if use_punkthus else "park_bands"),
            symmetry_preference="bilateral",
            composition_strictness=0.86,
            frontage_zone_ratio=0.18,
            public_realm_ratio=0.26,
            node_symmetry=use_punkthus,
            frontage_primary_side=(None if use_punkthus else "west"),
            frontage_secondary_side=(None if use_punkthus else "east"),
            node_layout_mode=("paired_edges" if use_punkthus else None),
            target_building_count=(2 if use_punkthus else 3),
            frontage_emphasis=0.72,
            rhythm_strength=0.70,
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
