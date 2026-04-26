"""v9 arkitekturscore — målbare kvalitetsmetrikker for en plan.

Ikke en "god/dårlig"-dom, men 6 konkrete tall som beskriver planens
arkitektoniske egenskaper:

- frontage_continuity: hvor godt bygg møter gatelinjen
- courtyard_clarity: tydelighet av gårdsrom (lukket/halvåpen/åpen)
- rhythm: er bygghøyder rytmiske eller kaotiske?
- form_entropy: bygg-dimensjonsvariasjon
- scale_consistency: har delfeltene sammenlignbar byggskala?
- hierarchy: er det tydelige "hoved" og "under"-bygg?

Alle scores er normalisert til [0, 1] hvor 1 er best.
"""
from __future__ import annotations

import math
from typing import Any, List, Optional, Tuple, Dict

from shapely.geometry import Polygon, LineString, Point


def score_frontage_continuity(
    buildings: List[Any],  # List[Bygg]
    composition: Optional[Any] = None,  # CompositionPlan
    snap_distance_m: float = 6.0,
) -> float:
    """Hvor godt møter bygg en felles frontage-linje?

    Gradert score:
    - Bygg innen 8m: full poeng (1.0)
    - Bygg 8-20m: delvis poeng (0.5-1.0 lineært)
    - Bygg 20-40m: lav poeng (0.1-0.5)
    - Bygg over 40m: 0.0

    Returnerer gjennomsnitt av alle bygg.
    """
    if not buildings or composition is None or not composition.street_frontages:
        return 0.0

    total_score = 0.0
    for b in buildings:
        min_dist = float('inf')
        for frontage in composition.street_frontages:
            try:
                d = b.footprint.distance(frontage)
                if d < min_dist:
                    min_dist = d
            except Exception:
                continue
        # Gradert score
        if min_dist <= 8.0:
            bldg_score = 1.0
        elif min_dist <= 20.0:
            # Lineær 0.5-1.0 i rangen 8-20m
            bldg_score = 1.0 - 0.5 * (min_dist - 8.0) / 12.0
        elif min_dist <= 40.0:
            # Lineær 0.1-0.5 i rangen 20-40m
            bldg_score = 0.5 - 0.4 * (min_dist - 20.0) / 20.0
        else:
            bldg_score = 0.0
        total_score += bldg_score

    return total_score / len(buildings)


def score_courtyard_clarity(
    buildings: List[Any],
    composition: Optional[Any] = None,
) -> float:
    """Tydelighet av gårdsrom.

    Hvis composition har courtyards: teller hvor mange av courtyard-
    kantene som har bygg innen 15m (lukkes visuelt).
    4 sider dekket = 1.0, 0 sider = 0.0.

    Hvis ingen composition eller courtyards: returner 0.0.
    """
    if not buildings or composition is None or not composition.courtyards:
        return 0.0

    courtyard = composition.courtyards[0]
    minx, miny, maxx, maxy = courtyard.bounds
    # De 4 sidene som "bygg langs" linjer
    sides = [
        LineString([(minx, maxy), (maxx, maxy)]),  # nord
        LineString([(maxx, miny), (maxx, maxy)]),  # øst
        LineString([(minx, miny), (maxx, miny)]),  # sør
        LineString([(minx, miny), (minx, maxy)]),  # vest
    ]

    closed_sides = 0
    for side in sides:
        has_building = False
        for b in buildings:
            try:
                if b.footprint.distance(side) < 15.0:
                    has_building = True
                    break
            except Exception:
                continue
        if has_building:
            closed_sides += 1

    return closed_sides / 4.0


def score_rhythm(buildings: List[Any]) -> float:
    """Er bygghøydene rytmiske? Minst 70% av byggene skal ha samme eller
    trappevis (±1 etasje) høyde. Punkthus som aksenter teller ikke.

    Score = 1.0 hvis bygghøyder følger gjenkjennelig mønster
    Score = 0.0 hvis kaos
    """
    if not buildings or len(buildings) < 2:
        return 0.5  # nøytral for få bygg

    floors_list = [b.floors for b in buildings if hasattr(b, 'floors')]
    if not floors_list:
        return 0.5

    # Finn dominant-høyde: modusen (mest vanlige)
    from collections import Counter
    counter = Counter(floors_list)
    most_common_floors, most_common_count = counter.most_common(1)[0]

    # Hvor mange bygg er innen ±1 etasje av dominant?
    near_dominant = sum(1 for f in floors_list if abs(f - most_common_floors) <= 1)
    return near_dominant / len(floors_list)


def score_form_entropy(buildings: List[Any]) -> float:
    """Byggdimensjonsvariasjon.

    Måler hvor mange distinkt-størrelse bygg det er.
    Score = 1.0 hvis det er 3-5 tydelige størrelser (velorganisert variasjon)
    Score = 0.5 hvis alle er samme (kjedelig)
    Score = 0.0 hvis det er 10+ ulike (kaotisk)
    """
    if not buildings:
        return 0.5

    # Ta fotprint-areal avrundet til nærmeste 50m²
    sizes = set()
    for b in buildings:
        try:
            area = b.footprint.area
            bucket = round(area / 50.0) * 50
            sizes.add(bucket)
        except Exception:
            continue

    n_unique = len(sizes)
    if n_unique == 0:
        return 0.0
    if n_unique <= 2:
        return 0.5  # for ensartet
    if n_unique <= 5:
        return 1.0  # sweet spot
    if n_unique <= 8:
        return 0.7
    return 0.3  # for kaotisk


def score_scale_consistency(buildings: List[Any], fields: List[Any]) -> float:
    """Har delfeltene sammenlignbar byggskala?

    For hvert delfelt: beregn gjennomsnittlig bygg-fotprint.
    Score = 1.0 hvis alle felter har byggskala i samme størrelsesorden.
    Score faller når noen felter har 10x skjæv skala vs andre.
    """
    if not buildings or not fields:
        return 0.5

    # Grupper bygg per delfelt
    per_field_sizes: Dict[str, List[float]] = {}
    for b in buildings:
        delfelt_id = getattr(b, 'delfelt_id', None)
        if delfelt_id:
            per_field_sizes.setdefault(delfelt_id, []).append(b.footprint.area)

    if len(per_field_sizes) < 2:
        return 1.0  # bare ett felt — ikke relevant

    avg_sizes = [sum(s) / len(s) for s in per_field_sizes.values() if s]
    if not avg_sizes:
        return 0.5

    max_avg = max(avg_sizes)
    min_avg = min(avg_sizes)
    if min_avg < 1:
        return 0.0
    ratio = max_avg / min_avg

    # Ratio 1.0 = identisk (score 1.0)
    # Ratio 3.0 = moderat variasjon (score 0.7)
    # Ratio 10.0+ = ekstrem variasjon (score 0.1)
    if ratio <= 2.0:
        return 1.0
    if ratio <= 4.0:
        return 0.7
    if ratio <= 7.0:
        return 0.4
    return 0.1


def score_hierarchy(buildings: List[Any]) -> float:
    """Er det tydelig bygg-hierarki?

    Planen har god hierarki hvis:
    - Det finnes minst ett "aksent"-bygg (høyere enn median)
    - Det finnes en klar "grunnlinje" (flesteparten har samme høyde)

    Score = 1.0 hvis begge er sant, 0.5 hvis kun grunnlinje, 0.3 hvis alt er tilfeldig.
    """
    if not buildings or len(buildings) < 3:
        return 0.5

    floors_list = sorted([b.floors for b in buildings if hasattr(b, 'floors')])
    if not floors_list:
        return 0.5

    median = floors_list[len(floors_list) // 2]
    max_floors = max(floors_list)
    n_at_median = sum(1 for f in floors_list if abs(f - median) <= 1)
    has_accent = max_floors > median + 2
    has_baseline = n_at_median / len(floors_list) > 0.55

    if has_accent and has_baseline:
        return 1.0
    if has_baseline:
        return 0.7
    if has_accent:
        return 0.5
    return 0.3


def architecture_score(
    plan: Any,  # MasterPlan
    composition: Optional[Any] = None,
) -> Dict[str, float]:
    """Kjør alle scoringfunksjoner og returner sammendrag.

    Returnerer dict med individuelle scores + total vektet snitt.
    """
    buildings = plan.bygg if hasattr(plan, 'bygg') else []
    fields = plan.delfelt if hasattr(plan, 'delfelt') else []

    scores = {
        "frontage_continuity": score_frontage_continuity(buildings, composition),
        "courtyard_clarity": score_courtyard_clarity(buildings, composition),
        "rhythm": score_rhythm(buildings),
        "form_entropy": score_form_entropy(buildings),
        "scale_consistency": score_scale_consistency(buildings, fields),
        "hierarchy": score_hierarchy(buildings),
    }

    # Vektet totalscore. Frontage + courtyard = urban hovedgrep.
    weights = {
        "frontage_continuity": 0.25,
        "courtyard_clarity": 0.15,
        "rhythm": 0.15,
        "form_entropy": 0.10,
        "scale_consistency": 0.20,
        "hierarchy": 0.15,
    }
    total = sum(scores[k] * weights[k] for k in scores)
    scores["total"] = total
    return scores
