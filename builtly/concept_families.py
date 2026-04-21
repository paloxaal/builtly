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
        floor_band=(4, 6),
        bya_target_pct=(20, 28),
    ),
    ConceptFamily.COURTYARD_URBAN: ConceptPreset(
        title="Urban kvartalsstruktur med gårdsrom",
        subtitle="Karrébebyggelse som dominerer, med tydelige urbane kanter og rolige gårdsrom.",
        dominant_typology=Typology.KARRE,
        floor_band=(5, 6),
        bya_target_pct=(22, 30),
    ),
    ConceptFamily.CLUSTER_PARK: ConceptPreset(
        title="Boligklynger rundt grønt fellesrom",
        subtitle="Lameller og punkthus grupperes rundt et felles grønt parkrom med siktlinjer gjennom bebyggelsen.",
        dominant_typology=Typology.LAMELL,
        floor_band=(4, 6),
        bya_target_pct=(16, 24),
    ),
}


def _linear_mixed_typologies(n: int) -> list[Typology]:
    """Lamell must be the clear main typology."""
    typologies = [Typology.LAMELL for _ in range(n)]
    if n <= 2:
        return typologies
    accent_count = 1 if n < 6 else 2
    accent_positions = [0] if accent_count == 1 else [0, n - 1]
    for idx in accent_positions:
        if 0 <= idx < n:
            typologies[idx] = Typology.PUNKTHUS
    return typologies


def _courtyard_urban_typologies(fields: list[Delfelt]) -> list[Typology]:
    """Karré should dominate and only fall back where geometry is truly narrow."""
    n = len(fields)
    typologies = [Typology.KARRE for _ in range(n)]
    for idx, field in enumerate(fields):
        minx, miny, maxx, maxy = field.polygon.bounds
        w = maxx - minx
        h = maxy - miny
        # Only allow lamell fallback for very slender pieces.
        if min(w, h) < 30 and max(w, h) / max(min(w, h), 1.0) > 2.6:
            typologies[idx] = Typology.LAMELL
    min_karre = max(1, int((n * 2 + 2) // 3))
    if sum(1 for t in typologies if t == Typology.KARRE) < min_karre:
        for idx in range(n):
            typologies[idx] = Typology.KARRE
            if sum(1 for t in typologies if t == Typology.KARRE) >= min_karre:
                break
    return typologies


def _cluster_park_typologies(n: int) -> list[Typology]:
    typologies: list[Typology] = []
    for idx in range(n):
        typologies.append(Typology.PUNKTHUS if idx % 3 == 1 else Typology.LAMELL)
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

    # First pass: derive weights, then renormalise so the sum always matches total_target_bra.
    raw_weights: list[float] = []
    for i, field in enumerate(fields):
        typology = typologies[i]
        area_weight = max(1.0, field.area_m2) / max(1.0, area_total)
        typology_factor = 1.0
        if concept_family == ConceptFamily.COURTYARD_URBAN and typology == Typology.KARRE:
            typology_factor = 1.18
        elif concept_family == ConceptFamily.COURTYARD_URBAN and typology == Typology.LAMELL:
            typology_factor = 0.72
        elif concept_family == ConceptFamily.LINEAR_MIXED and typology == Typology.LAMELL:
            typology_factor = 1.10
        elif concept_family == ConceptFamily.LINEAR_MIXED and typology == Typology.PUNKTHUS:
            typology_factor = 0.68
        elif concept_family == ConceptFamily.CLUSTER_PARK and typology == Typology.PUNKTHUS:
            typology_factor = 0.85
        raw_weights.append(area_weight * typology_factor)

    raw_sum = sum(raw_weights) or 1.0
    output: List[Delfelt] = []
    for i, field in enumerate(fields):
        typology = typologies[i]
        courtyard = courtyards[i]
        floors_min, floors_max = preset.floor_band
        target_bra = round(total_target_bra * (raw_weights[i] / raw_sum), 1)
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
