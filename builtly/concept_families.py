from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

from .masterplan_types import ConceptFamily, CourtyardKind, Delfelt, Typology


@dataclass(frozen=True)
class ConceptPreset:
    title: str
    subtitle: str
    dominant_typology: Typology
    floor_band: tuple[int, int]
    bya_target_pct: tuple[float, float]


PRESETS: Dict[ConceptFamily, ConceptPreset] = {
    ConceptFamily.LINEAR_MIXED: ConceptPreset(
        title="Lineært blandet boliggrep",
        subtitle="Lameller som hovedtypologi langs hovedaksen, med enkelte punkthus som aksenter.",
        dominant_typology=Typology.LAMELL,
        floor_band=(4, 5),
        bya_target_pct=(20, 28),
    ),
    ConceptFamily.COURTYARD_URBAN: ConceptPreset(
        title="Urban kvartalsstruktur med gårdsrom",
        subtitle="Karrébebyggelse som dominerer, med tydelige urbane kanter og rolige gårdsrom.",
        dominant_typology=Typology.KARRE,
        floor_band=(4, 5),
        bya_target_pct=(22, 30),
    ),
    ConceptFamily.CLUSTER_PARK: ConceptPreset(
        title="Boligklynger rundt grønt fellesrom",
        subtitle="Lameller og punkthus grupperes rundt et felles grønt parkrom med siktlinjer gjennom bebyggelsen.",
        dominant_typology=Typology.LAMELL,
        floor_band=(3, 5),
        bya_target_pct=(16, 24),
    ),
}


def _linear_mixed_typologies(n: int) -> list[Typology]:
    typologies = [Typology.LAMELL for _ in range(n)]
    accent_indices = {0, n - 1} if n >= 2 else {0}
    if n >= 10:
        accent_indices |= {n // 2}
    accent_indices = {idx for idx in accent_indices if 0 <= idx < n}
    for idx in accent_indices:
        typologies[idx] = Typology.PUNKTHUS
    return typologies


def _courtyard_urban_typologies(fields: list[Delfelt]) -> list[Typology]:
    n = len(fields)
    typologies = [Typology.KARRE for _ in range(n)]
    # Keep narrow edge parcels buildable with lamell fallback, but karré must dominate.
    for idx, field in enumerate(fields):
        minx, miny, maxx, maxy = field.polygon.bounds
        w = maxx - minx
        h = maxy - miny
        if min(w, h) < 40 and n > 4:
            typologies[idx] = Typology.LAMELL
    # Guarantee at least 75% karré share.
    min_karre = max(1, int(round(n * 0.75)))
    if sum(1 for t in typologies if t == Typology.KARRE) < min_karre:
        for idx in range(n):
            typologies[idx] = Typology.KARRE
            if sum(1 for t in typologies if t == Typology.KARRE) >= min_karre:
                break
    return typologies


def _cluster_park_typologies(n: int) -> list[Typology]:
    typologies: list[Typology] = []
    for idx in range(n):
        if idx % 3 == 1:
            typologies.append(Typology.PUNKTHUS)
        else:
            typologies.append(Typology.LAMELL)
    return typologies


def _courtyard_cycle_for(concept_family: ConceptFamily, n: int) -> list[CourtyardKind]:
    if concept_family == ConceptFamily.COURTYARD_URBAN:
        return [CourtyardKind.URBAN_TORG if i in (0, n - 1) else CourtyardKind.FELLES_BOLIG for i in range(n)]
    if concept_family == ConceptFamily.CLUSTER_PARK:
        return [CourtyardKind.PARKKANT if i % 2 == 0 else CourtyardKind.FELLES_BOLIG for i in range(n)]
    return [CourtyardKind.PARKKANT if i in (0, n - 1) else CourtyardKind.FELLES_BOLIG for i in range(n)]


def apply_concept_defaults(
    concept_family: ConceptFamily,
    fields: Iterable[Delfelt],
    total_target_bra: float,
) -> List[Delfelt]:
    preset = PRESETS[concept_family]
    fields = list(fields)
    n = max(1, len(fields))

    if concept_family == ConceptFamily.LINEAR_MIXED:
        typologies = _linear_mixed_typologies(n)
    elif concept_family == ConceptFamily.COURTYARD_URBAN:
        typologies = _courtyard_urban_typologies(fields)
    else:
        typologies = _cluster_park_typologies(n)

    courtyards = _courtyard_cycle_for(concept_family, n)

    area_total = sum(max(1.0, f.area_m2) for f in fields)
    output: List[Delfelt] = []
    for i, field in enumerate(fields):
        typology = typologies[i]
        courtyard = courtyards[i]
        floors_min, floors_max = preset.floor_band
        area_weight = max(1.0, field.area_m2) / max(1.0, area_total)

        # Urban concepts push more area into karré parcels; park concepts keep more reserve in open-space fields.
        typology_factor = 1.0
        if concept_family == ConceptFamily.COURTYARD_URBAN and typology == Typology.KARRE:
            typology_factor = 1.08
        elif concept_family == ConceptFamily.LINEAR_MIXED and typology == Typology.PUNKTHUS:
            typology_factor = 0.82
        elif concept_family == ConceptFamily.CLUSTER_PARK and typology == Typology.PUNKTHUS:
            typology_factor = 0.88

        target_bra = round(total_target_bra * area_weight * typology_factor, 1)
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
