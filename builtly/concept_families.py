from __future__ import annotations

"""Concept-family strategies for Builtly v8 delivery 4.

Strategies are deterministic defaults. Pass 2 may optionally ask an AI model to
adjust these parameters, but only within the strategy envelopes.
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


class ConceptStrategy:
    family: ConceptFamily
    ui_label: str
    fallback_title: str
    fallback_tagline: str

    def propose(
        self,
        delfelt: Sequence[Delfelt],
        target_bra_m2: float,
        plan_regler: Optional[PlanRegler] = None,
        neighbors: Optional[Sequence[dict]] = None,
    ) -> List[FieldParameterChoice]:
        raise NotImplementedError

    def envelope_for_field(self, index: int, count: int) -> FieldEnvelope:
        raise NotImplementedError

    def _area_shares(self, count: int) -> List[float]:
        return [1.0 / max(count, 1)] * max(count, 1)

    def _clamp_floors(self, floors_range: Tuple[int, int], plan_regler: Optional[PlanRegler]) -> Tuple[int, int]:
        fmin, fmax = floors_range
        if plan_regler and plan_regler.max_floors is not None:
            fmax = min(fmax, int(plan_regler.max_floors))
            fmin = min(fmin, fmax)
        return max(1, fmin), max(1, fmax)

    def _make_choices(
        self,
        delfelt: Sequence[Delfelt],
        target_bra_m2: float,
        plan_regler: Optional[PlanRegler],
        shares: Sequence[float],
    ) -> List[FieldParameterChoice]:
        out: List[FieldParameterChoice] = []
        for idx, field in enumerate(delfelt):
            env = self.envelope_for_field(idx, len(delfelt))
            floors_min, floors_max = self._clamp_floors(env.default_floors, plan_regler)
            out.append(
                FieldParameterChoice(
                    field_id=field.field_id,
                    typology=env.default_typology,
                    orientation_deg=(field.orientation_deg + env.default_orientation_offset_deg) % 180.0,
                    floors_min=floors_min,
                    floors_max=floors_max,
                    target_bra=float(target_bra_m2 * shares[idx]),
                    courtyard_kind=env.courtyard_kind,
                    tower_size_m=env.tower_size_m,
                    rationale=self._field_rationale(idx, len(delfelt), env),
                )
            )
        return out

    def _field_rationale(self, index: int, count: int, env: FieldEnvelope) -> str:
        return f"{self.family.value}: {env.default_typology.value} i felt {index + 1} av {count}."


class LinearMixedStrategy(ConceptStrategy):
    family = ConceptFamily.LINEAR_MIXED
    ui_label = "Lineært blandet grep"
    fallback_title = "Lineært grep med lameller og punkthus"
    fallback_tagline = "Lameller og punkthus organisert langs tomtas hovedakse."

    def _area_shares(self, count: int) -> List[float]:
        if count <= 1:
            return [1.0]
        if count == 2:
            return [0.55, 0.45]
        if count == 3:
            return [0.24, 0.52, 0.24]
        if count == 4:
            return [0.18, 0.32, 0.32, 0.18]
        # outer accents + linear middle
        middle = count - 2
        mid_share = 0.64 / max(middle, 1)
        return [0.18] + [mid_share] * middle + [0.18]

    def envelope_for_field(self, index: int, count: int) -> FieldEnvelope:
        edge = index == 0 or index == count - 1
        if edge and count >= 3:
            return FieldEnvelope(
                allowed_typologies=(Typology.PUNKTHUS, Typology.LAMELL),
                default_typology=Typology.PUNKTHUS,
                default_orientation_offset_deg=0.0,
                default_floors=(5, 6),
                courtyard_kind=CourtyardKind.PARKKANT,
                tower_size_m=17 if index == 0 else 21,
            )
        return FieldEnvelope(
            allowed_typologies=(Typology.LAMELL, Typology.PUNKTHUS),
            default_typology=Typology.LAMELL,
            default_orientation_offset_deg=0.0,
            default_floors=(4, 5),
            courtyard_kind=CourtyardKind.FELLES_BOLIG,
        )

    def propose(self, delfelt: Sequence[Delfelt], target_bra_m2: float, plan_regler: Optional[PlanRegler] = None, neighbors: Optional[Sequence[dict]] = None) -> List[FieldParameterChoice]:
        del neighbors
        return self._make_choices(delfelt, target_bra_m2, plan_regler, self._area_shares(len(delfelt)))


class CourtyardUrbanStrategy(ConceptStrategy):
    family = ConceptFamily.COURTYARD_URBAN
    ui_label = "Urbane gårdsrom"
    fallback_title = "Urbane gårdsrom med tydelig kant"
    fallback_tagline = "Karréer mot kantene og roligere boliger i innsiden."

    def _area_shares(self, count: int) -> List[float]:
        if count <= 1:
            return [1.0]
        if count == 2:
            return [0.58, 0.42]
        if count == 3:
            return [0.38, 0.34, 0.28]
        if count == 4:
            return [0.30, 0.30, 0.24, 0.16]
        base = [0.24, 0.24, 0.20]
        remaining = max(count - len(base), 0)
        return base + [max(0.32 / max(remaining, 1), 0.08)] * remaining

    def envelope_for_field(self, index: int, count: int) -> FieldEnvelope:
        if index in {0, min(1, count - 1)}:
            return FieldEnvelope(
                allowed_typologies=(Typology.KARRE, Typology.LAMELL),
                default_typology=Typology.KARRE,
                default_orientation_offset_deg=0.0 if index % 2 == 0 else 90.0,
                default_floors=(5, 6),
                courtyard_kind=CourtyardKind.URBAN_TORG,
            )
        if index == count - 1 and count >= 4:
            return FieldEnvelope(
                allowed_typologies=(Typology.REKKEHUS, Typology.LAMELL),
                default_typology=Typology.REKKEHUS,
                default_orientation_offset_deg=90.0,
                default_floors=(2, 3),
                courtyard_kind=CourtyardKind.FELLES_BOLIG,
            )
        return FieldEnvelope(
            allowed_typologies=(Typology.KARRE, Typology.LAMELL, Typology.REKKEHUS),
            default_typology=Typology.LAMELL,
            default_orientation_offset_deg=90.0,
            default_floors=(4, 5),
            courtyard_kind=CourtyardKind.FELLES_BOLIG,
        )

    def propose(self, delfelt: Sequence[Delfelt], target_bra_m2: float, plan_regler: Optional[PlanRegler] = None, neighbors: Optional[Sequence[dict]] = None) -> List[FieldParameterChoice]:
        del neighbors
        return self._make_choices(delfelt, target_bra_m2, plan_regler, self._area_shares(len(delfelt)))


class ClusterParkStrategy(ConceptStrategy):
    family = ConceptFamily.CLUSTER_PARK
    ui_label = "Klynger rundt park"
    fallback_title = "Boligklynger rundt grøntrom"
    fallback_tagline = "Klynger av lameller og punkthus rundt et tydelig felles grøntdrag."

    def _area_shares(self, count: int) -> List[float]:
        if count <= 1:
            return [1.0]
        if count == 2:
            return [0.46, 0.54]
        if count == 3:
            return [0.27, 0.46, 0.27]
        if count == 4:
            return [0.20, 0.30, 0.30, 0.20]
        middle = count - 2
        mid_share = 0.60 / max(middle, 1)
        return [0.20] + [mid_share] * middle + [0.20]

    def envelope_for_field(self, index: int, count: int) -> FieldEnvelope:
        center_like = index in {max(0, count // 2 - 1), count // 2}
        if center_like and count >= 3:
            return FieldEnvelope(
                allowed_typologies=(Typology.LAMELL, Typology.PUNKTHUS),
                default_typology=Typology.PUNKTHUS,
                default_orientation_offset_deg=0.0,
                default_floors=(5, 6),
                courtyard_kind=CourtyardKind.PARKKANT,
                tower_size_m=17 if index % 2 == 0 else 21,
            )
        return FieldEnvelope(
            allowed_typologies=(Typology.LAMELL, Typology.PUNKTHUS),
            default_typology=Typology.LAMELL,
            default_orientation_offset_deg=90.0 if index % 2 else 0.0,
            default_floors=(4, 5),
            courtyard_kind=CourtyardKind.PARKKANT,
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
    return [
        ConceptFamily.LINEAR_MIXED,
        ConceptFamily.COURTYARD_URBAN,
        ConceptFamily.CLUSTER_PARK,
    ]
