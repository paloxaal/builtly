
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

from .masterplan_types import ConceptFamily, CourtyardKind, Delfelt, Typology


@dataclass(frozen=True)
class ConceptPreset:
    title: str
    subtitle: str
    dominant_typology: Typology
    typology_cycle: List[Typology]
    courtyard_cycle: List[CourtyardKind]
    floor_band: tuple[int, int]
    bya_target_pct: tuple[float, float]


PRESETS: Dict[ConceptFamily, ConceptPreset] = {
    ConceptFamily.LINEAR_MIXED: ConceptPreset(
        title="Lineært blandet boliggrep",
        subtitle="Lameller som hovedtypologi langs hovedaksen, med enkelte punkthus som aksenter.",
        dominant_typology=Typology.LAMELL,
        typology_cycle=[Typology.LAMELL, Typology.LAMELL, Typology.PUNKTHUS, Typology.LAMELL, Typology.LAMELL],
        courtyard_cycle=[
            CourtyardKind.PARKKANT,
            CourtyardKind.FELLES_BOLIG,
            CourtyardKind.FELLES_BOLIG,
            CourtyardKind.PARKKANT,
        ],
        floor_band=(4, 6),
        bya_target_pct=(20, 26),
    ),
    ConceptFamily.COURTYARD_URBAN: ConceptPreset(
        title="Urban kvartalsstruktur med gårdsrom",
        subtitle="Karrébebyggelse som dominerer, med tydelige urbane kanter og rolige gårdsrom.",
        dominant_typology=Typology.KARRE,
        typology_cycle=[Typology.KARRE, Typology.KARRE, Typology.KARRE, Typology.LAMELL, Typology.KARRE],
        courtyard_cycle=[
            CourtyardKind.URBAN_TORG,
            CourtyardKind.FELLES_BOLIG,
            CourtyardKind.FELLES_BOLIG,
            CourtyardKind.URBAN_TORG,
        ],
        floor_band=(5, 7),
        bya_target_pct=(24, 30),
    ),
    ConceptFamily.CLUSTER_PARK: ConceptPreset(
        title="Boligklynger rundt grønt fellesrom",
        subtitle="Lameller og punkthus grupperes rundt grønt parkrom med siktkiler og luftige mellomrom.",
        dominant_typology=Typology.LAMELL,
        typology_cycle=[Typology.PUNKTHUS, Typology.LAMELL, Typology.PUNKTHUS, Typology.LAMELL, Typology.PUNKTHUS],
        courtyard_cycle=[
            CourtyardKind.PARKKANT,
            CourtyardKind.FELLES_BOLIG,
            CourtyardKind.PARKKANT,
            CourtyardKind.FELLES_BOLIG,
        ],
        floor_band=(4, 6),
        bya_target_pct=(18, 24),
    ),
}


def apply_concept_defaults(
    concept_family: ConceptFamily,
    fields: Iterable[Delfelt],
    total_target_bra: float,
) -> List[Delfelt]:
    preset = PRESETS[concept_family]
    fields = list(fields)
    n = max(1, len(fields))
    weights: List[float]
    if concept_family == ConceptFamily.COURTYARD_URBAN:
        weights = [1.15 if i < max(1, n - 1) else 0.85 for i in range(n)]
    elif concept_family == ConceptFamily.LINEAR_MIXED:
        weights = [1.05 if i in (1, max(1, n - 2)) else 0.95 for i in range(n)]
    else:
        weights = [1.0 for _ in range(n)]
    total_w = sum(weights) or 1.0
    output: List[Delfelt] = []
    for i, field in enumerate(fields):
        typology = preset.typology_cycle[i % len(preset.typology_cycle)]
        courtyard = preset.courtyard_cycle[i % len(preset.courtyard_cycle)]
        floors_min, floors_max = preset.floor_band
        target_share = weights[i] / total_w
        target_bra = round(total_target_bra * target_share, 1)
        tower_size = 21 if typology == Typology.PUNKTHUS and concept_family != ConceptFamily.LINEAR_MIXED else 17
        output.append(
            Delfelt(
                field_id=field.field_id,
                polygon=field.polygon,
                typology=typology,
                orientation_deg=field.orientation_deg,
                floors_min=floors_min,
                floors_max=floors_max,
                target_bra=target_bra,
                courtyard_kind=courtyard,
                tower_size_m=tower_size if typology == Typology.PUNKTHUS else None,
                phase=field.phase,
                phase_label=field.phase_label or f"Trinn {field.phase}",
            )
        )
    return output


def concept_title(concept_family: ConceptFamily) -> str:
    return PRESETS[concept_family].title


def concept_subtitle(concept_family: ConceptFamily) -> str:
    return PRESETS[concept_family].subtitle
