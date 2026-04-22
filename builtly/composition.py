"""v9 Composition pass — lager rammeplan før byggplassering.

Pass 0.5 i masterplan-pipelinen. Lager en CompositionPlan som definerer:
- Gatekanter (frontages) som bygg skal møte
- Gårdsrom (courtyards) som skal forbli ubebygd
- Aksentposisjoner (accent_points) for markante bygg

CompositionPlan brukes videre av pass 1 (delfelt-inndeling) og pass 4
(byggplassering) for å generere en plan med tydelig romlig hierarki.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple, Any

from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import unary_union

from .masterplan_types import (
    CompositionPlan,
    SiteContext,
    EdgeCharacter,
    ConceptFamily,
)


# ---------------------------------------------------------------------------
# Hjelpefunksjoner
# ---------------------------------------------------------------------------

def _edge_length(p0: Tuple[float, float], p1: Tuple[float, float]) -> float:
    return math.hypot(p1[0] - p0[0], p1[1] - p0[1])


def _edge_as_linestring(p0, p1) -> LineString:
    return LineString([p0, p1])


def _find_primary_frontage(site_poly: Polygon, site_context: Optional[SiteContext]) -> Optional[LineString]:
    """Finn den dominante gatefronten — lengste kant som IKKE er neighbor_dense.

    Hvis site_context mangler, returnerer vi den lengste ytterkanten.
    """
    if site_context is None or not site_context.edges:
        # Fallback: bruk kanter fra site_poly.boundary
        coords = list(site_poly.exterior.coords)
        best_len = 0
        best_edge = None
        for i in range(len(coords) - 1):
            p0, p1 = coords[i], coords[i + 1]
            l = _edge_length(p0, p1)
            if l > best_len:
                best_len = l
                best_edge = _edge_as_linestring(p0, p1)
        return best_edge

    # Sorter kanter etter lengde og filtrer bort neighbor_dense
    candidates = [
        e for e in site_context.edges
        if e.character != EdgeCharacter.NEIGHBOR_DENSE
    ]
    if not candidates:
        # Ingen åpen kant — bruk alle kanter og ta lengste
        candidates = list(site_context.edges)

    # Lengste først
    candidates.sort(key=lambda e: e.length_m, reverse=True)
    if not candidates:
        return None
    best = candidates[0]
    return _edge_as_linestring(best.p0, best.p1)


def _find_secondary_frontage(
    site_poly: Polygon,
    site_context: Optional[SiteContext],
    primary: LineString,
) -> Optional[LineString]:
    """Finn sekundær frontage — lengste kant som er noenlunde vinkelrett på primær."""
    if site_context is None or not site_context.edges:
        return None

    # Primærets retning
    px0, py0 = primary.coords[0]
    px1, py1 = primary.coords[1]
    primary_angle = math.degrees(math.atan2(py1 - py0, px1 - px0)) % 180.0

    candidates = []
    for e in site_context.edges:
        if e.character == EdgeCharacter.NEIGHBOR_DENSE:
            continue
        # Beregn kantens retning
        e_angle = math.degrees(math.atan2(e.p1[1] - e.p0[1], e.p1[0] - e.p0[0])) % 180.0
        angle_diff = min(abs(e_angle - primary_angle), 180 - abs(e_angle - primary_angle))
        # Vinkelrett-het: 90° er mål, innenfor 30° fra vinkelrett er akseptabelt
        perpendicularity = 1.0 - abs(angle_diff - 90.0) / 90.0
        if perpendicularity > 0.5 and e.length_m > 20:
            candidates.append((e.length_m * perpendicularity, e))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    best = candidates[0][1]
    return _edge_as_linestring(best.p0, best.p1)


def _reserve_courtyard(
    site_poly: Polygon,
    frontage: Optional[LineString] = None,
    min_side_m: float = 20.0,
    ring_depth_m: float = 15.0,
) -> Optional[Polygon]:
    """Reserver et indre gårdsrom ved å trekke tilbake fra alle tomt-kanter.

    Enkel versjon: krymper hele tomten med ring_depth_m og tar største
    inskriberte rektangel på minst min_side × min_side.

    `frontage` er valgfri og brukes ikke i enkel variant — reservert for
    utvidelse (f.eks. for å prioritere gårdsrom bak en frontage).
    """
    try:
        shrunk = site_poly.buffer(-ring_depth_m)
        if shrunk.is_empty:
            return None
        # Hvis shrunk er MultiPolygon, ta største
        if hasattr(shrunk, 'geoms'):
            shrunk = max(shrunk.geoms, key=lambda g: g.area)

        # Finn inskribert rektangel (approksimasjon via bounds + testing)
        minx, miny, maxx, maxy = shrunk.bounds
        w = maxx - minx
        h = maxy - miny
        if w < min_side_m or h < min_side_m:
            return None

        # Prøv forskjellige størrelser OG forskjellige sentrum-kandidater
        # (L-former har centroid forskjøvet fra bounds-senter).
        best_rect: Optional[Polygon] = None
        best_area = 0.0

        # Kandidat-sentrum: (1) shrunk.centroid, (2) bounds-senter, (3) centroid
        # med lokal optimalisering
        candidates_center = []
        try:
            sc = shrunk.centroid
            candidates_center.append((sc.x, sc.y))
        except Exception:
            pass
        candidates_center.append(((minx + maxx) / 2, (miny + maxy) / 2))

        # Også prøv et grid av sentra for L/T/U-former
        for frac_x in (0.3, 0.5, 0.7):
            for frac_y in (0.3, 0.5, 0.7):
                candidates_center.append((minx + w * frac_x, miny + h * frac_y))

        for cx, cy in candidates_center:
            for scale in (0.8, 0.7, 0.6, 0.5, 0.4, 0.3):
                half_w = w * scale / 2
                half_h = h * scale / 2
                rect = box(cx - half_w, cy - half_h, cx + half_w, cy + half_h)
                # Krev at rect er minst min_side_m på begge sider
                if rect.bounds[2] - rect.bounds[0] < min_side_m:
                    continue
                if rect.bounds[3] - rect.bounds[1] < min_side_m:
                    continue
                if shrunk.covers(rect):
                    if rect.area > best_area:
                        best_rect = rect
                        best_area = rect.area
                        break  # for denne center — ta første som passer

        return best_rect
    except Exception:
        return None


def _find_accent_point(
    site_poly: Polygon,
    primary_frontage: LineString,
    secondary_frontage: Optional[LineString] = None,
) -> Optional[Tuple[Point, float]]:
    """Finn aksentposisjon — hjørnet mellom primary og secondary frontage.

    Hvis secondary mangler, bruker vi enden av primary som er nærmest
    tomtens centroid (mest prominent).
    """
    if secondary_frontage is not None:
        # Hjørnepunktet mellom de to frontages
        p_coords = list(primary_frontage.coords)
        s_coords = list(secondary_frontage.coords)
        # Finn delte/nærmeste endepunkter
        min_dist = float('inf')
        corner = None
        for p in p_coords:
            for s in s_coords:
                d = math.hypot(p[0] - s[0], p[1] - s[1])
                if d < min_dist:
                    min_dist = d
                    corner = Point((p[0] + s[0]) / 2, (p[1] + s[1]) / 2)
        if corner is not None:
            return (corner, 20.0)

    # Fallback: enden av primary som er nærmest tomtens centroid
    c = site_poly.centroid
    coords = list(primary_frontage.coords)
    best = min(coords, key=lambda p: math.hypot(p[0] - c.x, p[1] - c.y))
    return (Point(best), 20.0)


# ---------------------------------------------------------------------------
# Konseptspesifikke komposisjoner
# ---------------------------------------------------------------------------

def compose_courtyard_urban(
    site_poly: Polygon,
    site_context: Optional[SiteContext],
    target_bra_m2: float,
) -> CompositionPlan:
    """Komposisjon for COURTYARD_URBAN — urban kvartalsstruktur.

    Strategi:
    1. Finn primær frontage (lengste ikke-nabolag-tette kant)
    2. Finn sekundær frontage vinkelrett hvis mulig
    3. Reserver et tydelig gårdsrom innerst
    4. Aksentposisjon i hjørnet mellom frontages
    """
    plan = CompositionPlan.empty(concept_name="COURTYARD_URBAN")

    primary = _find_primary_frontage(site_poly, site_context)
    if primary is None:
        return plan
    plan.street_frontages.append(primary)

    secondary = _find_secondary_frontage(site_poly, site_context, primary)
    if secondary is not None:
        plan.street_frontages.append(secondary)

    courtyard = _reserve_courtyard(site_poly, primary, min_side_m=18.0, ring_depth_m=15.0)
    if courtyard is not None:
        plan.courtyards.append(courtyard)

    accent = _find_accent_point(site_poly, primary, secondary)
    if accent is not None:
        plan.accent_points.append(accent)

    plan.concept_rules = {
        "min_frontage_continuity": 0.60,
        "frontage_setback_m": 3.0,
        "courtyard_setback_m": 6.0,
        "accent_height_bonus_floors": 2,
    }
    return plan


def compose_linear_mixed(
    site_poly: Polygon,
    site_context: Optional[SiteContext],
    target_bra_m2: float,
) -> CompositionPlan:
    """Komposisjon for LINEAR_MIXED — parallelle lameller med aksentstrukturer.

    Strategi:
    1. Identifiser tomtens lengste akse
    2. Definer 2-4 strip-lines vinkelrett på aksen
    3. Aksentposisjoner i ender
    """
    plan = CompositionPlan.empty(concept_name="LINEAR_MIXED")

    # Lengste akse av tomten
    mrr = site_poly.minimum_rotated_rectangle
    coords = list(mrr.exterior.coords)[:-1]
    sides = []
    for i in range(len(coords)):
        p0 = coords[i]
        p1 = coords[(i + 1) % len(coords)]
        sides.append((p0, p1, _edge_length(p0, p1)))
    sides.sort(key=lambda s: s[2], reverse=True)
    long_side = sides[0]

    # Primær akse = lengste side
    primary_axis = _edge_as_linestring(long_side[0], long_side[1])
    plan.street_frontages.append(primary_axis)

    # Aksentpunkter ved begge ender av aksen
    plan.accent_points.append((Point(long_side[0]), 18.0))
    plan.accent_points.append((Point(long_side[1]), 18.0))

    plan.concept_rules = {
        "n_lamell_strips": 3,
        "strip_spacing_m": 18.0,
        "accent_at_ends": True,
    }
    return plan


def compose_cluster_park(
    site_poly: Polygon,
    site_context: Optional[SiteContext],
    target_bra_m2: float,
) -> CompositionPlan:
    """Komposisjon for CLUSTER_PARK — klynger rundt sentralt parkrom.

    Strategi:
    1. Reserver sentralt parkrom (ca 20% av tomten)
    2. Ingen eksplisitt frontage
    3. Aksentpunkt på parkens kant
    """
    plan = CompositionPlan.empty(concept_name="CLUSTER_PARK")

    # Sentralt park: større enn courtyard. Vi bruker samme helper men
    # ignorerer primary_frontage (den brukes ikke i enkel variant).
    park = _reserve_courtyard(site_poly, frontage=None,  # type: ignore
                              min_side_m=25.0, ring_depth_m=20.0)
    if park is not None:
        plan.courtyards.append(park)
        # Aksent på hjørnet av parken
        minx, miny, maxx, maxy = park.bounds
        plan.accent_points.append((Point(maxx, maxy), 15.0))

    plan.concept_rules = {
        "park_clearance_m": 6.0,
        "cluster_count_min": 3,
        "cluster_count_max": 5,
    }
    return plan


# ---------------------------------------------------------------------------
# Hovedfunksjon
# ---------------------------------------------------------------------------

def orientation_for_role(
    field_poly: Polygon,
    role: Optional[str],
    facing_frontage_idx: Optional[int],
    composition: Optional[CompositionPlan],
    fallback_deg: float = 0.0,
) -> float:
    """Gi orienteringsvinkel for et delfelt basert på role og komposisjon.

    For frontage-felter: bygg orienteres PARALLELT med frontage-linjen
    (slik at lange lameller ligger langs gata).

    For courtyard-felter: bygg orienteres slik at dypde vender MOT gårdsrommet.

    For accent: bruker feltets egen hovedakse.

    Returnerer vinkelen i grader [0, 180).
    """
    # Uten komposisjon: fallback til feltets egen akse
    if composition is None or not role:
        return fallback_deg

    # Frontage-roles: bruk frontage-linjens vinkel
    if role in ("frontage_primary", "frontage_secondary"):
        if (facing_frontage_idx is not None
                and 0 <= facing_frontage_idx < len(composition.street_frontages)):
            frontage = composition.street_frontages[facing_frontage_idx]
            coords = list(frontage.coords)
            if len(coords) >= 2:
                dx = coords[1][0] - coords[0][0]
                dy = coords[1][1] - coords[0][1]
                angle = math.degrees(math.atan2(dy, dx)) % 180.0
                return angle

    # Courtyard-side: dybden skal vende INN mot gården, så orient
    # parallelt med nærmeste gårdsromskant
    if role == "courtyard_side" and composition.courtyards:
        courtyard = composition.courtyards[0]
        cy_minx, cy_miny, cy_maxx, cy_maxy = courtyard.bounds
        # Feltets centroid
        fc = field_poly.centroid
        # Hvilken gårdsromskant er nærmest? Horisontal eller vertikal?
        horizontal_dist = min(abs(fc.y - cy_miny), abs(fc.y - cy_maxy))
        vertical_dist = min(abs(fc.x - cy_minx), abs(fc.x - cy_maxx))
        if horizontal_dist < vertical_dist:
            return 0.0  # parallelt med horisontal gårdsromskant
        else:
            return 90.0  # parallelt med vertikal gårdsromskant

    # Fallback
    return fallback_deg


# ---------------------------------------------------------------------------
# Hovedfunksjon: compose_plan
# ---------------------------------------------------------------------------

def compose_plan(
    site_poly: Polygon,
    concept_family: ConceptFamily,
    target_bra_m2: float,
    site_context: Optional[SiteContext] = None,
) -> CompositionPlan:
    """Pass 0.5: Lag komposisjonsplan basert på konsept og tomt.

    Returnerer alltid en CompositionPlan (kan være tom).
    """
    if site_poly is None or site_poly.is_empty:
        return CompositionPlan.empty()

    try:
        if concept_family == ConceptFamily.COURTYARD_URBAN:
            return compose_courtyard_urban(site_poly, site_context, target_bra_m2)
        elif concept_family == ConceptFamily.LINEAR_MIXED:
            return compose_linear_mixed(site_poly, site_context, target_bra_m2)
        elif concept_family == ConceptFamily.CLUSTER_PARK:
            return compose_cluster_park(site_poly, site_context, target_bra_m2)
        else:
            return CompositionPlan.empty(concept_name=str(concept_family))
    except Exception:
        return CompositionPlan.empty(concept_name=str(concept_family))


# ---------------------------------------------------------------------------
# Pass 1: delfelt-generator basert på komposisjonsplan
# ---------------------------------------------------------------------------

def generate_delfelt_polygons_from_composition(
    site_poly: Polygon,
    composition: CompositionPlan,
    target_count: int = 4,
) -> List[Tuple[Polygon, str, Optional[int]]]:
    """Gir tilbake polygon + role + facing_frontage_idx for hvert delfelt.

    For COURTYARD_URBAN: fire felter rundt gårdsrommet.
    For LINEAR_MIXED: strip-felter vinkelrett på primærakse.
    For CLUSTER_PARK: sektor-felter rundt parken.

    Fallback: hvis komposisjonen er tom eller manglende, returnerer vi
    én stor rekt-delfelt med role=None, slik at gammel logikk kan overta.
    """
    if not composition.has_composition():
        return [(site_poly, "generic", None)]

    if composition.concept_name == "COURTYARD_URBAN":
        return _split_around_courtyard(site_poly, composition)
    elif composition.concept_name == "LINEAR_MIXED":
        return _split_into_strips(site_poly, composition)
    elif composition.concept_name == "CLUSTER_PARK":
        return _split_into_clusters(site_poly, composition)
    else:
        return [(site_poly, "generic", None)]


def _split_around_courtyard(
    site_poly: Polygon,
    composition: CompositionPlan,
) -> List[Tuple[Polygon, str, Optional[int]]]:
    """For COURTYARD_URBAN: del tomten i ringsoner rundt gårdsrommet.

    Hvis det er ett gårdsrom: tomt.difference(courtyard) gir en ring.
    Vi deler ringen i 4 sektorer (N/Ø/S/V) basert på frontages.
    """
    result: List[Tuple[Polygon, str, Optional[int]]] = []

    if not composition.courtyards:
        # Ingen gårdsrom — bare bruk tomten som én felt med primary frontage
        role = "frontage_primary" if composition.street_frontages else "generic"
        return [(site_poly, role, 0 if composition.street_frontages else None)]

    courtyard = composition.courtyards[0]

    try:
        # Ring = tomt minus gårdsrom, med litt buffer slik at vi får en
        # virkelig ring ikke en difference som etterlater en liten linje.
        courtyard_buf = courtyard.buffer(composition.concept_rules.get("courtyard_setback_m", 6.0))
        ring = site_poly.difference(courtyard_buf)
    except Exception:
        return [(site_poly, "generic", None)]

    if ring.is_empty or ring.area < 100:
        return [(site_poly, "generic", None)]

    # Hvis ring er MultiPolygon, bruk hver del som delfelt
    ring_parts: List[Polygon] = []
    if hasattr(ring, 'geoms'):
        ring_parts = [p for p in ring.geoms if isinstance(p, Polygon) and p.area > 100]
    elif isinstance(ring, Polygon):
        ring_parts = [ring]

    # Hvis ring er én sammenhengende polygon, del den i 4 sektorer
    # basert på tomtens senter og kompassretningene
    if len(ring_parts) == 1 and ring_parts[0].area > site_poly.area * 0.3:
        ring_parts = _split_ring_into_sectors(ring_parts[0], site_poly, courtyard)

    # Forenkle hver ring-del til aksejusterte rektangler slik at motoren
    # kan plassere lameller/karréer inne. Dette taper litt areal men gjør
    # plasseringen robust. Hver ring-del kan bli 1-2 rektangler.
    rectangularized: List[Polygon] = []
    for p in ring_parts:
        rects = _rectangularize_ring_part(p)
        rectangularized.extend([r for r in rects if r.area > 100])
    ring_parts = rectangularized

    # Tildel role basert på plassering mot frontages
    for part in ring_parts:
        role, frontage_idx = _classify_ring_part(part, composition)
        result.append((part, role, frontage_idx))

    if not result:
        return [(site_poly, "generic", None)]
    return result


def _rectangularize_ring_part(part: Polygon) -> List[Polygon]:
    """Gjør en (potensielt) kompleks ring-sektor om til en LISTE av
    aksejusterte rektangler som sammen dekker det meste av sektorens areal.

    Ring-sektorer fra courtyard-subtraksjon er typisk L-formede (med rundet
    indre hjørne). Vi dekomponerer dem i to rektangler: en "lang" (langs
    lengste kant) og evt. en "kort" (langs kortere kant).
    """
    if part is None or part.is_empty or part.area < 100:
        return []

    try:
        minx, miny, maxx, maxy = part.bounds
        w = maxx - minx
        h = maxy - miny
        if w < 5 or h < 5:
            return []

        # Trinn 1: prøv bounds-rektangelet direkte
        bounds_rect = box(minx, miny, maxx, maxy)
        if part.covers(bounds_rect):
            return [bounds_rect]

        # Trinn 2: Finn største aksejusterte rektangel langs TOP-kant og
        # langs RIGHT-kant (og BOTTOM/LEFT), og velg de to beste.
        rects: List[Tuple[Polygon, float]] = []  # (rect, area)

        # Prøv horisontale bånd — start med størst og gå ned til noe passer
        for y_start in (maxy - 30, maxy - 25, maxy - 20, maxy - 16, maxy - 14, maxy - 12, maxy - 10, maxy - 8):
            if y_start < miny + 1:
                continue
            band = box(minx, y_start, maxx, maxy)
            if part.covers(band):
                rects.append((band, band.area))
                break
        for y_end in (miny + 30, miny + 25, miny + 20, miny + 16, miny + 14, miny + 12, miny + 10, miny + 8):
            if y_end > maxy - 1:
                continue
            band = box(minx, miny, maxx, y_end)
            if part.covers(band):
                rects.append((band, band.area))
                break

        # Prøv vertikale bånd
        for x_start in (maxx - 30, maxx - 25, maxx - 20, maxx - 16, maxx - 14, maxx - 12, maxx - 10, maxx - 8):
            if x_start < minx + 1:
                continue
            band = box(x_start, miny, maxx, maxy)
            if part.covers(band):
                rects.append((band, band.area))
                break
        for x_end in (minx + 30, minx + 25, minx + 20, minx + 16, minx + 14, minx + 12, minx + 10, minx + 8):
            if x_end > maxx - 1:
                continue
            band = box(minx, miny, x_end, maxy)
            if part.covers(band):
                rects.append((band, band.area))
                break

        if not rects:
            # Fallback: krymp hele bounds til noe passer
            for shrink in (2, 4, 6, 10, 15):
                if w <= 2 * shrink or h <= 2 * shrink:
                    continue
                shrunk = box(minx + shrink, miny + shrink, maxx - shrink, maxy - shrink)
                if part.covers(shrunk):
                    return [shrunk]
            return []

        # Velg de to rektanglene med størst areal som IKKE overlapper (eller
        # overlapper minimalt)
        rects.sort(key=lambda r: r[1], reverse=True)
        chosen: List[Polygon] = [rects[0][0]]
        for r, _ in rects[1:]:
            # Sjekk overlapp med allerede valgte
            overlap_area = 0.0
            for c in chosen:
                overlap_area = max(overlap_area, r.intersection(c).area)
            # Tillat maks 20% overlapp
            if overlap_area < r.area * 0.2:
                chosen.append(r)
            if len(chosen) >= 2:
                break
        return chosen

    except Exception:
        return []


def _split_ring_into_sectors(
    ring: Polygon,
    site_poly: Polygon,
    courtyard: Polygon,
) -> List[Polygon]:
    """Del en sammenhengende ring i 4 sektorer basert på kompassretninger."""
    try:
        c = courtyard.centroid
        minx, miny, maxx, maxy = site_poly.bounds
        # Diagonale linjer gjennom gårdsromsentrum
        # Nord-sør og øst-vest split
        # Vi bruker skråstilte linjer for å lage 4 sektorer
        from shapely.geometry import Polygon as ShPolygon
        # Sektor N: over horisontal linje gjennom c.y OG nord for courtyard
        # Enklere: del i 4 basert på vertikal + horisontal linje

        # Horisontal halvdel nord
        north_half = ShPolygon([
            (minx - 10, c.y), (maxx + 10, c.y),
            (maxx + 10, maxy + 10), (minx - 10, maxy + 10)
        ])
        # Sør
        south_half = ShPolygon([
            (minx - 10, miny - 10), (maxx + 10, miny - 10),
            (maxx + 10, c.y), (minx - 10, c.y)
        ])
        # Øst
        east_half = ShPolygon([
            (c.x, miny - 10), (maxx + 10, miny - 10),
            (maxx + 10, maxy + 10), (c.x, maxy + 10)
        ])
        # Vest
        west_half = ShPolygon([
            (minx - 10, miny - 10), (c.x, miny - 10),
            (c.x, maxy + 10), (minx - 10, maxy + 10)
        ])

        sectors = []
        for half_a, half_b in [(north_half, east_half), (south_half, east_half),
                                (south_half, west_half), (north_half, west_half)]:
            sector = ring.intersection(half_a).intersection(half_b)
            if not sector.is_empty and sector.area > 100:
                if hasattr(sector, 'geoms'):
                    for g in sector.geoms:
                        if isinstance(g, Polygon) and g.area > 100:
                            sectors.append(g)
                elif isinstance(sector, Polygon):
                    sectors.append(sector)

        return sectors if sectors else [ring]
    except Exception:
        return [ring]


def _classify_ring_part(
    part: Polygon,
    composition: CompositionPlan,
) -> Tuple[str, Optional[int]]:
    """Bestem role for en ring-del.

    Regel: hvis sektorens geometri BERØRER (buffer 2m) en frontage-linje,
    det er et frontage-felt. Bruk avstand fra selve polygonet, ikke centroid.
    """
    if not composition.street_frontages:
        return ("courtyard_side", None)

    # Avstand fra selve geometrien til hver frontage
    best_frontage_idx = 0
    best_frontage_dist = float('inf')
    for i, f in enumerate(composition.street_frontages):
        dist = part.distance(f)
        if dist < best_frontage_dist:
            best_frontage_dist = dist
            best_frontage_idx = i

    # Hvis polygonet berører frontage (innen 2m), det er frontage-felt
    if best_frontage_dist < 2.0:
        role = "frontage_primary" if best_frontage_idx == 0 else "frontage_secondary"
        return (role, best_frontage_idx)
    else:
        return ("courtyard_side", best_frontage_idx)


def _split_into_strips(
    site_poly: Polygon,
    composition: CompositionPlan,
) -> List[Tuple[Polygon, str, Optional[int]]]:
    """For LINEAR_MIXED: del tomten i parallelle strip-felter."""
    if not composition.street_frontages:
        return [(site_poly, "generic", None)]

    n_strips = composition.concept_rules.get("n_lamell_strips", 3)
    primary = composition.street_frontages[0]

    # Retning av primary
    p0, p1 = primary.coords[0], primary.coords[1]
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length < 1:
        return [(site_poly, "generic", None)]

    # Perpendikulær retning
    perp_x = -dy / length
    perp_y = dx / length

    # Bounds i perpendikulær retning
    minx, miny, maxx, maxy = site_poly.bounds
    # Projiser tomt-hjørner på perp-retningen for å finne bredde
    corners = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    projs = [(x * perp_x + y * perp_y) for x, y in corners]
    p_min, p_max = min(projs), max(projs)
    total_width = p_max - p_min

    strips: List[Tuple[Polygon, str, Optional[int]]] = []
    strip_width = total_width / n_strips

    # For hver strip: lag en stor rektangel i perp-retningen og intersect med tomt
    for i in range(n_strips):
        p_low = p_min + i * strip_width
        p_high = p_min + (i + 1) * strip_width

        # Lag en "slab" i perp-retningen
        big = 10000
        # Parallelt-senter langs primær-retningen
        cx = (minx + maxx) / 2
        cy = (miny + maxy) / 2
        # Hjørner av slab
        along_x = dx / length
        along_y = dy / length

        mid_p = (p_low + p_high) / 2
        slab_center = (mid_p * perp_x, mid_p * perp_y)

        corners_slab = [
            (slab_center[0] - big * along_x + (p_low - mid_p) * perp_x,
             slab_center[1] - big * along_y + (p_low - mid_p) * perp_y),
            (slab_center[0] + big * along_x + (p_low - mid_p) * perp_x,
             slab_center[1] + big * along_y + (p_low - mid_p) * perp_y),
            (slab_center[0] + big * along_x + (p_high - mid_p) * perp_x,
             slab_center[1] + big * along_y + (p_high - mid_p) * perp_y),
            (slab_center[0] - big * along_x + (p_high - mid_p) * perp_x,
             slab_center[1] - big * along_y + (p_high - mid_p) * perp_y),
        ]
        slab = Polygon(corners_slab)
        try:
            clipped = site_poly.intersection(slab)
            if clipped.is_empty or clipped.area < 100:
                continue
            # Ta kun Polygon-deler
            if hasattr(clipped, 'geoms'):
                for g in clipped.geoms:
                    if isinstance(g, Polygon) and g.area > 100:
                        role = "frontage_primary" if i == 0 else "frontage_secondary"
                        strips.append((g, role, 0))
            elif isinstance(clipped, Polygon):
                role = "frontage_primary" if i == 0 else "frontage_secondary"
                strips.append((clipped, role, 0))
        except Exception:
            continue

    return strips if strips else [(site_poly, "generic", None)]


def _split_into_clusters(
    site_poly: Polygon,
    composition: CompositionPlan,
) -> List[Tuple[Polygon, str, Optional[int]]]:
    """For CLUSTER_PARK: del tomten i sektorer rundt parken."""
    if not composition.courtyards:
        return [(site_poly, "generic", None)]

    park = composition.courtyards[0]
    cluster_count = composition.concept_rules.get("cluster_count_min", 3)

    # Samme som around_courtyard, men med role="cluster"
    try:
        park_buf = park.buffer(composition.concept_rules.get("park_clearance_m", 6.0))
        ring = site_poly.difference(park_buf)
    except Exception:
        return [(site_poly, "generic", None)]

    if ring.is_empty or ring.area < 100:
        return [(site_poly, "generic", None)]

    parts: List[Polygon] = []
    if hasattr(ring, 'geoms'):
        parts = [p for p in ring.geoms if isinstance(p, Polygon) and p.area > 100]
    elif isinstance(ring, Polygon):
        # Del i sektorer som around_courtyard
        parts = _split_ring_into_sectors(ring, site_poly, park)

    return [(p, "cluster", None) for p in parts]
