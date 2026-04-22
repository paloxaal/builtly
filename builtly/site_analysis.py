"""Pass 0: Site analysis — tomtens arkitektoniske intelligens.

Denne modulen analyserer tomtens geometri og nabolagsdata og produserer
en SiteContext som pass 1 (delfeltinndeling), pass 2 (parametervalg) og
AI-pass 3 (designdirektiver) bruker videre.

Designprinsipper:
- Alt skal være defensivt: tomter kan være L-formede, trekantede, uten
  naboer, eller manglende data. Vi svarer alltid med noe sensibelt.
- Ingen eksterne avhengigheter utover shapely og numpy.
- Tester kjører på syntetiske tomter (se test_site_analysis.py).
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from shapely.geometry import Polygon, LineString, Point, MultiPolygon, box
from shapely.ops import unary_union

from .masterplan_types import (
    SiteContext,
    SiteArm,
    SiteEdge,
    EdgeCharacter,
)


# ---------------------------------------------------------------------------
# Hjelpefunksjoner
# ---------------------------------------------------------------------------

def _bearing_deg(dx: float, dy: float) -> float:
    """Retning i grader fra nord (klokkeretning), der dy=1,dx=0 → 0° (nord).

    Matematisk atan2(dy,dx) gir 0° når vektoren peker østover (dx=1,dy=0).
    Vi roterer til kompass-konvensjon: 0°=N, 90°=Ø, 180°=S, 270°=V.
    """
    # Matematisk bearing (0=Ø, 90=N) → kompass (0=N, 90=Ø)
    angle_rad = math.atan2(dx, dy)  # bytter dx/dy for å få N som 0
    deg = math.degrees(angle_rad) % 360.0
    return deg


def _edge_normal_outward(p0: Tuple[float, float], p1: Tuple[float, float],
                         poly: Polygon) -> Tuple[float, float]:
    """Normal-vektor (enhetsvektor) som peker ut fra tomten for gitt kant.

    Rotasjon av kantvektoren 90° mot urviseren gir én normal. Vi sjekker om
    et punkt litt forskjøvet i den retningen er utenfor polygonen — hvis ja,
    er det riktig retning (outward). Hvis nei, vender vi.
    """
    ex = p1[0] - p0[0]
    ey = p1[1] - p0[1]
    length = math.hypot(ex, ey)
    if length < 1e-9:
        return (1.0, 0.0)
    # Venstre-normal (rotasjon 90° mot urviseren)
    nx = -ey / length
    ny = ex / length
    # Midtpunkt + liten forskyvning i normalretning
    mid_x = (p0[0] + p1[0]) / 2.0
    mid_y = (p0[1] + p1[1]) / 2.0
    test_point = Point(mid_x + nx * 1.0, mid_y + ny * 1.0)
    if poly.contains(test_point):
        # Feil retning — vend
        nx, ny = -nx, -ny
    return (nx, ny)


def _dominant_axis_of_polygon(poly: Polygon) -> float:
    """Finn den dominante aksen (hovedretningen) av en polygon, grader fra nord.

    Bruker minimum rotated bounding rectangle (MRR). Aksen er retningen av
    den lengste siden av MRR.
    """
    try:
        mrr = poly.minimum_rotated_rectangle
        coords = list(mrr.exterior.coords)[:-1]  # fire hjørner, ikke repetere
        if len(coords) < 4:
            return 0.0
        # Finn lengste side
        best_len = 0.0
        best_dx = 1.0
        best_dy = 0.0
        for i in range(len(coords)):
            p0 = coords[i]
            p1 = coords[(i + 1) % len(coords)]
            dx = p1[0] - p0[0]
            dy = p1[1] - p0[1]
            length = math.hypot(dx, dy)
            if length > best_len:
                best_len = length
                best_dx = dx
                best_dy = dy
        return _bearing_deg(best_dx, best_dy) % 180.0  # akse = modulo 180
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Steg 1: Analysere tomtens kanter
# ---------------------------------------------------------------------------

def extract_site_edges(site_poly: Polygon, min_edge_length_m: float = 3.0,
                       simplify_tolerance_m: float = 1.5) -> List[SiteEdge]:
    """Hent alle vesentlige kanter av tomten med orientering og normalretning.

    Tomtepolygonet forenkles først med simplify_tolerance_m for å fjerne
    GIS-støy (kollinære punkter, små forskyvninger). Uten dette kan en
    enkel L-form ha 20-30 kanter pga. vektordata-upresishet.

    Kanter kortere enn min_edge_length_m filtreres ut etter simplify.
    """
    edges: List[SiteEdge] = []
    try:
        # Forenkle polygonet for å redusere antall kanter (GIS-støy)
        try:
            simplified = site_poly.simplify(simplify_tolerance_m, preserve_topology=True)
            # Faller tilbake hvis simplify ødelegger geometrien
            if simplified.is_empty or not simplified.is_valid:
                working_poly = site_poly
            else:
                working_poly = simplified
        except Exception:
            working_poly = site_poly

        coords = list(working_poly.exterior.coords)
    except Exception:
        return edges

    for i in range(len(coords) - 1):
        p0 = (float(coords[i][0]), float(coords[i][1]))
        p1 = (float(coords[i + 1][0]), float(coords[i + 1][1]))
        length = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        if length < min_edge_length_m:
            continue
        # Normal-beregning gjøres mot originalt polygon (mer nøyaktig)
        nx, ny = _edge_normal_outward(p0, p1, site_poly)
        outward_bearing = _bearing_deg(nx, ny)
        edges.append(SiteEdge(
            p0=p0, p1=p1, length_m=length,
            outward_bearing_deg=outward_bearing,
            character=EdgeCharacter.UNKNOWN,
            neighbor_count=0,
        ))
    return edges


# ---------------------------------------------------------------------------
# Steg 2: Finne "armer" ved konveks-decomposition
# ---------------------------------------------------------------------------

def _is_approx_rectangular(poly: Polygon, tolerance: float = 0.92) -> bool:
    """Sjekk om en polygon er tilnærmet rektangulær.

    Forholdet mellom polygonets areal og dens minimum bounding rect = 1.0
    for en perfekt rektangel. Under 0.92 betyr det er betydelig kuttet/L-form.

    Terskelverdi 0.92 er valgt fordi typiske L-former (som Tyholt) har ~0.85-0.88,
    mens svakt skjeve rektangler fra GIS har 0.95+.
    """
    try:
        mrr = poly.minimum_rotated_rectangle
        if mrr.area <= 0:
            return False
        ratio = poly.area / mrr.area
        return ratio >= tolerance
    except Exception:
        return True  # feil-toleranse: anta rektangulær


def decompose_to_arms(site_poly: Polygon, min_arm_area_m2: float = 400.0) -> List[SiteArm]:
    """Dekomponer tomten til arkitektoniske "armer".

    Algoritme:
    - Hvis tomten er tilnærmet rektangulær → 1 arm (hele tomten)
    - Ellers: finn konveksiteter ved å sammenligne tomten med dens convex hull
      og identifisere "hakk" — disse er skille mellom armer
    - Fallback: båndsnitt langs dominant akse i 2 deler

    Den enkleste og mest robuste starten er å sjekke om den er rektangulær
    eller ikke, og håndtere kun rektangel + L-form i første versjon. Mer
    komplekse former får "1 arm = hele tomten"-fallback.
    """
    if site_poly is None or site_poly.is_empty:
        return []

    centroid = site_poly.centroid
    site_center = (centroid.x, centroid.y)

    # Hvis rektangulær: 1 arm
    if _is_approx_rectangular(site_poly):
        return [_make_arm("arm_main", site_poly, site_center)]

    # Ikke-rektangulær: forsøk L/T-dekomposisjon via convex hull-differanse
    try:
        hull = site_poly.convex_hull
        missing = hull.difference(site_poly)

        if missing.is_empty or missing.area < site_poly.area * 0.05:
            # Bare små avvik — behandle som rektangulær
            return [_make_arm("arm_main", site_poly, site_center)]

        # Den manglende delen er et "hakk". For å dekomponere til armer,
        # prøver vi å kutte tomten med linjer gjennom hakkets hjørner.
        arms = _split_by_notch(site_poly, missing, min_arm_area_m2)
        if arms:
            # Navngi armene etter retning fra tomtens senter
            labeled = []
            for i, arm_poly in enumerate(arms):
                arm_id = f"arm_{_cardinal_from_bearing(_bearing_from_point(site_center, arm_poly.centroid))}"
                # Unikt id hvis flere armer i samme retning
                arm_id_unique = arm_id
                idx = 1
                existing_ids = {a.arm_id for a in labeled}
                while arm_id_unique in existing_ids:
                    idx += 1
                    arm_id_unique = f"{arm_id}_{idx}"
                labeled.append(_make_arm(arm_id_unique, arm_poly, site_center))
            return labeled
    except Exception:
        pass

    # Alt annet feiler — returner én arm
    return [_make_arm("arm_main", site_poly, site_center)]


def _find_concave_vertices(poly: Polygon) -> List[Tuple[float, float]]:
    """Finn "inngående" (konkave) hjørner på polygonet — hjørner som vender
    innover mot tomten.

    En vertex er konkav hvis kryssproduktet av forrige og neste kant peker
    motsatt retning av polygonets omløpsretning. For en CCW-polygon (positiv
    areal) er konkav vertex der cross-product er negativt.
    """
    coords = list(poly.exterior.coords)
    if len(coords) < 4:
        return []
    # Sørg for CCW (positiv areal)
    if poly.exterior.is_ccw:
        pts = coords[:-1]
    else:
        pts = coords[:-1][::-1]

    concave: List[Tuple[float, float]] = []
    n = len(pts)
    for i in range(n):
        prev = pts[(i - 1) % n]
        curr = pts[i]
        next_ = pts[(i + 1) % n]
        # Kryssprodukt av (curr - prev) og (next - curr)
        ax = curr[0] - prev[0]
        ay = curr[1] - prev[1]
        bx = next_[0] - curr[0]
        by = next_[1] - curr[1]
        cross = ax * by - ay * bx
        if cross < -1e-6:  # konkav (inngående)
            concave.append(curr)
    return concave


def _split_by_notch(site_poly: Polygon, notch,
                    min_arm_area_m2: float) -> List[Polygon]:
    """Bruk den inngående vertex-en i site_poly til å kutte tomten i armer.

    Strategi:
    - Finn alle konkave (inngående) hjørner i site_poly
    - For hvert slikt hjørne, trekk linjer i tomtens to hovedretninger
      (horisontalt og vertikalt) og velg den som gir best to-veis split
    - Returner armene sortert etter areal
    """
    concave_pts = _find_concave_vertices(site_poly)
    if not concave_pts:
        return []

    # Start med det første konkave punktet (vanligvis kun ett i L-former)
    pivot = concave_pts[0]

    minx, miny, maxx, maxy = site_poly.bounds
    diag = math.hypot(maxx - minx, maxy - miny) * 3

    from shapely.ops import split as shapely_split

    # Kandidatlinjer: horisontal og vertikal gjennom pivot
    candidates = [
        LineString([(pivot[0] - diag, pivot[1]), (pivot[0] + diag, pivot[1])]),  # horisontal
        LineString([(pivot[0], pivot[1] - diag), (pivot[0], pivot[1] + diag)]),  # vertikal
    ]

    best_parts: List[Polygon] = []
    best_score = 0.0  # balansert split er best — produkt av areal

    for line in candidates:
        try:
            result = shapely_split(site_poly, line)
            parts = [g for g in result.geoms
                     if isinstance(g, Polygon) and g.area >= min_arm_area_m2]
            if len(parts) >= 2:
                # Score = produkt av areal (balansert er høyest)
                product = 1.0
                for p in parts:
                    product *= p.area
                if product > best_score:
                    best_score = product
                    best_parts = sorted(parts, key=lambda p: p.area, reverse=True)
        except Exception:
            continue

    return best_parts


def _make_arm(arm_id: str, poly: Polygon, site_center: Tuple[float, float]) -> SiteArm:
    """Lag en SiteArm fra en polygon."""
    c = poly.centroid
    bearing = _bearing_from_point(site_center, c)
    dom_axis = _dominant_axis_of_polygon(poly)
    # Aspect ratio fra MRR
    try:
        mrr = poly.minimum_rotated_rectangle
        coords = list(mrr.exterior.coords)[:-1]
        sides = []
        for i in range(len(coords)):
            p0 = coords[i]
            p1 = coords[(i + 1) % len(coords)]
            sides.append(math.hypot(p1[0] - p0[0], p1[1] - p0[1]))
        sides.sort()
        aspect = sides[-1] / max(1e-6, sides[0])
    except Exception:
        aspect = 1.0
    return SiteArm(
        arm_id=arm_id,
        polygon=poly,
        centroid=(c.x, c.y),
        area_m2=float(poly.area),
        bearing_from_site_center_deg=bearing,
        dominant_axis_deg=dom_axis,
        aspect_ratio=aspect,
    )


def _bearing_from_point(origin: Tuple[float, float], target) -> float:
    """Retning i grader fra nord fra origin til target (Point eller tuple)."""
    if hasattr(target, 'x') and hasattr(target, 'y'):
        tx, ty = target.x, target.y
    else:
        tx, ty = target[0], target[1]
    dx = tx - origin[0]
    dy = ty - origin[1]
    return _bearing_deg(dx, dy)


def _cardinal_from_bearing(bearing_deg: float) -> str:
    """Konverter grader-fra-nord til nærmeste hovedretning: n/ne/e/se/s/sw/w/nw."""
    b = bearing_deg % 360.0
    dirs = [
        (22.5, "north"), (67.5, "northeast"), (112.5, "east"), (157.5, "southeast"),
        (202.5, "south"), (247.5, "southwest"), (292.5, "west"), (337.5, "northwest"),
        (360.0, "north"),
    ]
    for threshold, name in dirs:
        if b < threshold:
            return name
    return "north"


# ---------------------------------------------------------------------------
# Steg 3: Klassifisere kanter fra nabobygg
# ---------------------------------------------------------------------------

def classify_edges_from_neighbors(
    site_poly: Polygon,
    edges: List[SiteEdge],
    neighbor_buildings: Optional[Sequence[dict]] = None,
    buffer_m: float = 25.0,
) -> List[SiteEdge]:
    """Sett karakter på hver kant basert på nabobygg-tettheten inntil.

    Optimalisert med STRtree for å unngå O(n*e) intersection-tester.
    På Tyholt med 333 naboer + 26 kanter reduserer dette fra 8 658 tester
    til ca 26 × log2(333) ≈ 220 tester.
    """
    if not neighbor_buildings:
        return edges

    # Normaliser neighbor-polygoner + behold original dict for høyde-ekstraksjon
    neighbor_pairs: List[Tuple[Polygon, dict]] = []
    for nb in neighbor_buildings:
        geom = _normalize_neighbor_geom(nb)
        if geom is not None:
            # Filtrer ut naboer som er inne på tomten (skal ikke skje,
            # men håndterer edge cases fra Vantor-data)
            try:
                if site_poly.contains(geom.centroid):
                    continue
            except Exception:
                continue
            neighbor_pairs.append((geom, nb if isinstance(nb, dict) else {}))

    if not neighbor_pairs:
        return edges

    # Bygg spatial index for raskt oppslag
    try:
        from shapely.strtree import STRtree
        neighbor_polys_only = [p for p, _ in neighbor_pairs]
        tree = STRtree(neighbor_polys_only)
        use_tree = True
    except Exception:
        tree = None
        use_tree = False

    for edge in edges:
        edge_line = LineString([edge.p0, edge.p1])
        buffered = edge_line.buffer(buffer_m)
        count = 0
        heights: List[float] = []

        if use_tree:
            # STRtree.query returnerer kandidater som skjærer buffered-bbox
            try:
                candidate_indices = tree.query(buffered)
                for idx in candidate_indices:
                    try:
                        npoly = neighbor_polys_only[idx]
                        nb_raw = neighbor_pairs[idx][1]
                    except (IndexError, TypeError):
                        continue
                    try:
                        if buffered.intersects(npoly):
                            count += 1
                            h = _extract_neighbor_height(nb_raw)
                            if h is not None and h > 0:
                                heights.append(h)
                    except Exception:
                        continue
            except Exception:
                # STRtree returnerte annet enn indekser — fall tilbake til objektliste
                try:
                    candidates = tree.query(buffered)
                    for npoly in candidates:
                        # Finn matching nb_raw
                        nb_raw = {}
                        for p, raw in neighbor_pairs:
                            if p is npoly:
                                nb_raw = raw
                                break
                        try:
                            if buffered.intersects(npoly):
                                count += 1
                                h = _extract_neighbor_height(nb_raw)
                                if h is not None and h > 0:
                                    heights.append(h)
                        except Exception:
                            continue
                except Exception:
                    use_tree = False

        if not use_tree:
            # Fallback: O(n) loop
            for npoly, nb_raw in neighbor_pairs:
                try:
                    if buffered.intersects(npoly):
                        count += 1
                        h = _extract_neighbor_height(nb_raw)
                        if h is not None and h > 0:
                            heights.append(h)
                except Exception:
                    continue

        edge.neighbor_count = count
        if heights:
            edge.avg_neighbor_height_m = sum(heights) / len(heights)
        if count == 0:
            edge.character = EdgeCharacter.OPEN
        elif count <= 2:
            edge.character = EdgeCharacter.NEIGHBOR_SPARSE
        else:
            edge.character = EdgeCharacter.NEIGHBOR_DENSE
    return edges


def _extract_neighbor_height(nb: dict) -> Optional[float]:
    """Hent høyde fra et nabo-dict. Tåler ulike formater (etasjer × 3.2m,
    eller direkte height_m-felt)."""
    if not isinstance(nb, dict):
        return None
    # Direkte høyde-felt
    for key in ("height_m", "height", "bygningshoyde_m"):
        val = nb.get(key)
        if val is not None:
            try:
                h = float(val)
                if h > 0:
                    return h
            except (TypeError, ValueError):
                pass
    # Etasjer * 3.2m
    for key in ("floors", "etasjer", "antall_etasjer"):
        val = nb.get(key)
        if val is not None:
            try:
                n = int(val)
                if n > 0:
                    return float(n) * 3.2
            except (TypeError, ValueError):
                pass
    return None


def _normalize_neighbor_geom(nb: dict) -> Optional[Polygon]:
    """Hent Polygon ut av et nabobygg-dict. Tåler ulike formater."""
    try:
        geom = nb.get("geometry")
        if geom is None:
            return None
        if isinstance(geom, Polygon):
            return geom
        if isinstance(geom, str):
            from shapely import wkt
            poly = wkt.loads(geom)
            if isinstance(poly, Polygon):
                return poly
            if isinstance(poly, MultiPolygon):
                # Ta den største delen
                return max(poly.geoms, key=lambda g: g.area)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Hovedfunksjon — pass 0
# ---------------------------------------------------------------------------

def analyze_site(
    site_poly: Polygon,
    neighbor_buildings: Optional[Sequence[dict]] = None,
) -> SiteContext:
    """Pass 0: Analyser tomten og returner SiteContext.

    Robust mot:
    - Manglende eller tom site_poly
    - Manglende neighbor_buildings
    - Uregelmessige geometrier (faller tilbake til enkel rektangel-analyse)
    - Polygoner med mange micro-kanter fra GIS-data (vi forenkler før analyse)
    """
    if site_poly is None or site_poly.is_empty:
        return SiteContext.empty(site_poly)

    # Forenkle polygonet: fjern vertices som ligger på nesten rett linje.
    # Toleranse 1.5m er liten nok til å bevare L/T/U-former men fjerner
    # GIS-støy (micro-kanter < 1m som ofte finnes i tomteregistrering).
    try:
        simplified = site_poly.simplify(1.5, preserve_topology=True)
        if isinstance(simplified, Polygon) and not simplified.is_empty and simplified.area > 0:
            analysis_poly = simplified
        else:
            analysis_poly = site_poly
    except Exception:
        analysis_poly = site_poly

    try:
        # Grunnleggende geometri
        c = analysis_poly.centroid
        site_center = (c.x, c.y)
        dom_axis = _dominant_axis_of_polygon(analysis_poly)
        is_rect = _is_approx_rectangular(analysis_poly)

        # Armer
        arms = decompose_to_arms(analysis_poly)

        # Kanter — bruk min-lengde 5m for å filtrere bort micro-kanter
        edges = extract_site_edges(analysis_poly, min_edge_length_m=5.0)
        edges = classify_edges_from_neighbors(analysis_poly, edges, neighbor_buildings)

        # Oppsummeringer
        n_neighbors = len([nb for nb in (neighbor_buildings or [])
                          if _normalize_neighbor_geom(nb) is not None])

        # Find dominant view: retning med flest OPEN-kanter vektet etter lengde
        open_bearing_weights: dict = {}
        for e in edges:
            if e.character == EdgeCharacter.OPEN:
                card = _cardinal_from_bearing(e.outward_bearing_deg)
                open_bearing_weights[card] = open_bearing_weights.get(card, 0.0) + e.length_m
        dominant_view = None
        if open_bearing_weights:
            dominant_view = max(open_bearing_weights.items(), key=lambda kv: kv[1])[0]

        # Site-label
        if is_rect:
            mrr = site_poly.minimum_rotated_rectangle
            mrr_coords = list(mrr.exterior.coords)[:-1]
            sides = [math.hypot(mrr_coords[(i + 1) % 4][0] - mrr_coords[i][0],
                                mrr_coords[(i + 1) % 4][1] - mrr_coords[i][1])
                     for i in range(4)]
            sides.sort()
            aspect = sides[-1] / max(1e-6, sides[0])
            if aspect > 2.5:
                site_label = "narrow_rectangle"
            elif aspect > 1.5:
                site_label = "rectangle"
            else:
                site_label = "square"
        elif len(arms) >= 2:
            site_label = f"multi_arm_{len(arms)}"
        else:
            site_label = "irregular"

        return SiteContext(
            site_area_m2=float(site_poly.area),
            site_centroid=site_center,
            site_bearing_deg=dom_axis,
            arms=arms,
            edges=edges,
            is_rectangular=is_rect,
            has_arms=len(arms) >= 2,
            n_neighbors=n_neighbors,
            dominant_view_direction=dominant_view,
            access_bearing_deg=None,  # kommer i uke 2 (trenger road-data)
            site_label=site_label,
        )
    except Exception as exc:
        # Siste fallback
        return SiteContext.empty(site_poly)
