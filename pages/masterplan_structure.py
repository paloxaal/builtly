from __future__ import annotations

"""Masterplan-strukturelle grep (uke 1 av arkitektkvalitet-løftet).

Dette modulet introduserer hovedaksen og torg som eksplisitte primitiver
*før* tomten deles i delfelt. Dagens motor deler direkte i rektangulære
delfelt basert på areal og PCA-retning. Referansebildene (LPO / Kibnes
Arkitekter) viser derimot at masterplanen først etableres via
gjennomgående grøntakser og torg-noder — og at delfelt legges *langs*
disse aksene, ikke på tvers.

Modulet er bevisst atskilt fra `geometry.py` og `masterplan_engine.py`
slik at det kan testes og videreutvikles uavhengig. Det importerer
kun fra `masterplan_types` (for Polygon-alias) og `geometry` (for PCA).

Hovedfunksjoner:
    - analyze_site_axes(buildable_poly, neighbors, latitude_deg) -> MasterplanAxes
    - compute_torg_nodes(axes, buildable_poly, count_hint=2) -> List[Point]
    - axes_as_corridor_polygons(axes, profile) -> List[Polygon]

Algoritme for akse-valg (automatisk, deterministisk):
    1. PCA gir tomtas hovedakse (major/minor + theta).
    2. Nabo-asymmetri beregnes: høydeforskjell mellom sør- og nord-nabo,
       vektet med avstand. Høy asymmetri → naboskillet trekker til
       ortogonal struktur (små mot stor).
    3. Elongation = major / minor.
    4. Beslutningsregler (i rekkefølge):
         a) elongation > 1.6   → ortogonal (langs major-aksen)
         b) naboasymmetri > 0.40 → ortogonal (følger naboskillet, N-S)
         c) ellers             → diagonal (mot beste sol-akse)
    5. Sol-akse ved 63°N peker ~N-S; diagonal velges derfor som den av
       ±45° rotasjonene av N-S som gir størst lengde innenfor tomten.

Profiler (FORSTAD vs URBAN) styrer parameter-biblioteket (akse-bredder,
torg-radius, osv). FORSTAD er default og matcher Tyholt og norske
forstadskvartaler. URBAN er Oslo-storkvartal-standard.
"""

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.ops import unary_union


# ---------------------------------------------------------------------------
# Profil-bibliotek — konkrete tall fra referansebildene
# ---------------------------------------------------------------------------


class MasterplanProfile(str, Enum):
    """Parameter-profil for masterplan-struktur.

    FORSTAD: Tyholt, norske forstadsprosjekter. Matchet mot LPO/Kibnes-
    referansebilder. Smalere gater, grunnere bygg, lavere tetthet.

    URBAN: Oslo-storkvartal-standard. Bredere gater, dypere bygg, høyere
    tetthet. Aktiveres kun for tomter i sentrum av tett by, eller når
    brukeren eksplisitt velger det.
    """
    FORSTAD = "FORSTAD"
    URBAN = "URBAN"


@dataclass(frozen=True)
class ProfileParameters:
    """Konkrete mål for en masterplan-profil."""
    # Akse-geometri
    primary_axis_width_m: float          # bredde på hovedaksens grøntkorridor
    secondary_axis_width_m: float        # bredde på sekundær-akse (tversgående)
    # Torg-geometri
    torg_radius_m: float                 # radius på torg-node
    torg_count_min: int                  # min antall torg langs hovedaksen
    torg_count_max: int                  # maks antall torg langs hovedaksen
    # Nabo-skille (for ortogonal akse-valg)
    neighbor_asymmetry_threshold: float  # over denne: ortogonal akse-valg
    # Elongation-terskel (for ortogonal akse-valg)
    elongation_threshold: float          # over denne: ortogonal akse-valg
    # Minimums-andel som må gjenstå som delfelt etter korridor-subtraksjon
    min_field_ratio: float               # f.eks 0.65 = 65% av tomt må gjenstå


PROFILE_PARAMS: Dict[MasterplanProfile, ProfileParameters] = {
    MasterplanProfile.FORSTAD: ProfileParameters(
        primary_axis_width_m=10.0,      # 8-12m observert i LPO/Kibnes
        secondary_axis_width_m=8.0,
        torg_radius_m=9.0,              # 12-18m diameter observert
        torg_count_min=1,
        torg_count_max=3,
        neighbor_asymmetry_threshold=0.40,
        elongation_threshold=1.6,
        min_field_ratio=0.70,
    ),
    MasterplanProfile.URBAN: ProfileParameters(
        primary_axis_width_m=16.0,      # Oslo-standard 14-22m
        secondary_axis_width_m=12.0,
        torg_radius_m=12.0,
        torg_count_min=1,
        torg_count_max=2,
        neighbor_asymmetry_threshold=0.50,
        elongation_threshold=1.8,
        min_field_ratio=0.65,
    ),
}


# ---------------------------------------------------------------------------
# Datatyper
# ---------------------------------------------------------------------------


@dataclass
class MasterplanAxes:
    """Resultatet av masterplan-strukturell analyse.

    Dette objektet eksponeres på Masterplan og serialiseres videre til
    UI-laget (Mulighetsstudie.render_arkitekturdiagram_svg).
    """
    # Akse-linjer (globale koordinater)
    primary_axis: Optional[LineString] = None
    secondary_axis: Optional[LineString] = None
    # Grøntkorridor-polygoner (utvidet rundt aksene)
    corridor_polygons: List[Polygon] = field(default_factory=list)
    # Torg (sirkelpolygoner, ikke bebygd)
    torg_polygons: List[Polygon] = field(default_factory=list)
    torg_nodes: List[Point] = field(default_factory=list)
    # Metadata
    axis_type: str = ""                # "diagonal" | "orthogonal" | "none"
    profile: MasterplanProfile = MasterplanProfile.FORSTAD
    rationale: str = ""                # leselig forklaring (for UI/debug)
    # Numeriske mål (for debug/test)
    elongation: float = 0.0
    neighbor_asymmetry: float = 0.0
    primary_orientation_deg: float = 0.0

    def to_debug_dict(self) -> Dict[str, Any]:
        return {
            "axis_type": self.axis_type,
            "profile": self.profile.value,
            "rationale": self.rationale,
            "elongation": round(self.elongation, 3),
            "neighbor_asymmetry": round(self.neighbor_asymmetry, 3),
            "primary_orientation_deg": round(self.primary_orientation_deg, 1),
            "torg_count": len(self.torg_nodes),
            "corridor_count": len(self.corridor_polygons),
            "primary_axis_length_m": round(self.primary_axis.length, 1) if self.primary_axis else 0.0,
            "secondary_axis_length_m": round(self.secondary_axis.length, 1) if self.secondary_axis else 0.0,
        }


# ---------------------------------------------------------------------------
# Nabo-asymmetri-analyse
# ---------------------------------------------------------------------------


def _neighbor_points_with_height(neighbors: Optional[Sequence[dict]]) -> List[Tuple[Point, float]]:
    """Konverter nabo-dicts til (centroid_point, height_m)-par.

    Forventet format (fra legacy_page_bridge._neighbor_buildings_from_context):
        [{"coords": [[x,y], ...], "height_m": float}, ...]
    """
    out: List[Tuple[Point, float]] = []
    for n in (neighbors or []):
        coords = n.get("coords") or []
        if not coords or len(coords) < 3:
            continue
        try:
            poly = Polygon(coords)
            if poly.is_empty or poly.area < 1.0:
                continue
            height = float(n.get("height_m", n.get("height", 9.0)) or 9.0)
            out.append((poly.centroid, height))
        except Exception:
            continue
    return out


def compute_neighbor_asymmetry(
    buildable_poly: Polygon,
    neighbors: Optional[Sequence[dict]],
    *,
    sample_radius_m: Optional[float] = None,
) -> float:
    """Returner asymmetri-mål i [0.0, 1.0].

    Asymmetri = (h_nord - h_syd) / (h_nord + h_syd + 1.0), absoluttverdi.
    Tomter med f.eks. småhus sør (6m) og næring nord (25m) får høy
    asymmetri (~0.6), som trekker motoren mot ortogonal N-S-akse.

    Tomter med jevn nabohøyde får asymmetri ~0, og da velges diagonal
    (hvis elongation også er liten).

    Sample-radius er adaptiv: hvis ikke eksplisitt, settes den til
    max(80m, halvparten av bbox-diagonalen) slik at naboer rundt store
    tomter (Tyholt har centroid ~100m fra naboene) også fanges.
    """
    points = _neighbor_points_with_height(neighbors)
    if not points:
        return 0.0

    if sample_radius_m is None:
        minx, miny, maxx, maxy = buildable_poly.bounds
        diag = math.hypot(maxx - minx, maxy - miny)
        sample_radius_m = max(80.0, diag * 0.6)

    centroid = buildable_poly.centroid
    cx, cy = float(centroid.x), float(centroid.y)

    # Klassifiser naboer som "sør" (y < cy) eller "nord" (y > cy)
    # innenfor sample_radius_m fra tomtas centroid.
    south_heights: List[float] = []
    north_heights: List[float] = []
    for pt, h in points:
        dx = float(pt.x) - cx
        dy = float(pt.y) - cy
        dist = math.hypot(dx, dy)
        if dist > sample_radius_m:
            continue
        if dy < 0:
            south_heights.append(h)
        else:
            north_heights.append(h)

    if not south_heights or not north_heights:
        # Hvis bare én side har naboer innenfor radius, regn den andre
        # siden som "åpen" (0m høyde). Det er en svakere, men nyttig
        # asymmetri-indikator.
        h_s = max(south_heights) if south_heights else 0.0
        h_n = max(north_heights) if north_heights else 0.0
    else:
        h_s = float(np.mean(south_heights))
        h_n = float(np.mean(north_heights))

    denom = h_s + h_n + 1.0
    return abs(h_n - h_s) / denom


# ---------------------------------------------------------------------------
# Akse-analyse (hovedalgoritmen)
# ---------------------------------------------------------------------------


def _pca_axes(buildable_poly: Polygon) -> Tuple[float, float, float, float, float]:
    """Retur: (theta_deg, major_m, minor_m, centroid_x, centroid_y).

    Dette er samme beregning som geometry.pca_site_axes, men duplisert
    her for å holde masterplan_structure uavhengig av geometry.py's
    fulle importgraf (som drar inn typology_library etc).
    """
    exterior = list(buildable_poly.exterior.coords)[:-1]
    if len(exterior) < 3:
        raise ValueError("buildable_poly trenger minst 3 punkter")
    coords = np.array(exterior, dtype=float)
    centroid = coords.mean(axis=0)
    centered = coords - centroid
    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    vec = eigvecs[:, int(np.argmax(eigvals))]
    theta_deg = math.degrees(math.atan2(float(vec[1]), float(vec[0]))) % 180.0

    perp = np.array([-vec[1], vec[0]], dtype=float)
    major_proj = centered @ vec
    minor_proj = centered @ perp
    major_m = float(np.max(major_proj) - np.min(major_proj))
    minor_m = float(np.max(minor_proj) - np.min(minor_proj))
    if major_m < minor_m:
        theta_deg = (theta_deg + 90.0) % 180.0
        major_m, minor_m = minor_m, major_m

    return (
        theta_deg,
        major_m,
        minor_m,
        float(buildable_poly.centroid.x),
        float(buildable_poly.centroid.y),
    )


def _build_axis_linestring(
    buildable_poly: Polygon,
    theta_deg: float,
) -> Optional[LineString]:
    """Lag en LineString som krysser hele tomten langs vinkelen theta_deg.

    Går fra kant til kant gjennom centroiden, klippes til buildable_poly.
    """
    centroid = buildable_poly.centroid
    cx, cy = float(centroid.x), float(centroid.y)
    # Lag lang linje gjennom centroiden
    rad = math.radians(theta_deg)
    dx, dy = math.cos(rad), math.sin(rad)
    # Lengden bestemmes av bbox-diagonalen × 2 (garantert å krysse polygonet)
    minx, miny, maxx, maxy = buildable_poly.bounds
    diag = math.hypot(maxx - minx, maxy - miny)
    p1 = (cx - dx * diag, cy - dy * diag)
    p2 = (cx + dx * diag, cy + dy * diag)
    long_line = LineString([p1, p2])
    # Klipp til buildable_poly
    clipped = long_line.intersection(buildable_poly)
    if clipped.is_empty:
        return None
    # Hvis intersection gir MultiLineString, ta den lengste biten
    if isinstance(clipped, MultiLineString):
        segments = list(clipped.geoms)
        if not segments:
            return None
        return max(segments, key=lambda s: s.length)
    if isinstance(clipped, LineString):
        return clipped
    return None


def _best_diagonal_theta_for_sun(base_theta_deg: float, latitude_deg: float) -> float:
    """Returner best diagonal-vinkel.

    Strategi: For diagonal orientering tar vi major-aksens vinkel og
    roterer ±45°. Velger retningen som er nærmest sol-azimut for
    middagssol (sør = 0° i Builtly-konvensjonen der theta=0 peker
    langs x-aksen, dvs øst). Det gir best mulig sol-eksponering for
    bygg plassert langs aksen.

    For 63°N (Trondheim) står sola middag i sør. I plan-koordinater
    med y nord-peikende er sør = -y, som tilsvarer theta=270° for en
    vektor fra sør til nord. Vi måler aksens retning i [0,180), så
    en "N-S-akse" har theta=90° og en "Ø-V-akse" har theta=0°.

    En diagonal skal typisk gå NØ-SV eller NV-SØ, dvs theta ≈ 45° eller
    135°. Vi velger den som er nærmest base_theta ± 45°.
    """
    _ = latitude_deg  # reservert for framtidig sol-presisjon
    option_a = (base_theta_deg + 45.0) % 180.0
    option_b = (base_theta_deg - 45.0) % 180.0
    # Foretrekk den som er nærmere 45° eller 135° (de to "rene" diagonalene)
    def dist_to_clean_diagonal(t: float) -> float:
        return min(abs(t - 45.0), abs(t - 135.0))
    return option_a if dist_to_clean_diagonal(option_a) <= dist_to_clean_diagonal(option_b) else option_b


def analyze_site_axes(
    buildable_poly: Polygon,
    neighbors: Optional[Sequence[dict]] = None,
    latitude_deg: float = 63.42,
    *,
    profile: MasterplanProfile = MasterplanProfile.FORSTAD,
    force_axis_type: Optional[str] = None,
) -> MasterplanAxes:
    """Hovedalgoritmen: velg diagonal vs ortogonal akse automatisk.

    Parametre:
        buildable_poly: Byggbart polygon (tomtens netto).
        neighbors: Liste av nabobygg-dicts med "coords" og "height_m".
        latitude_deg: Breddegrad (for sol-akse).
        profile: FORSTAD (default) eller URBAN. Styrer korridor-bredder
            og terskler.
        force_axis_type: Hvis satt til "diagonal" eller "orthogonal",
            overstyrer auto-beslutningen (brukes av konsept-strategier
            som ønsker å tvinge akse).

    Returnerer MasterplanAxes med utfylt primær + (evt) sekundær akse,
    korridor-polygoner og torg-noder.
    """
    if buildable_poly is None or buildable_poly.is_empty:
        raise ValueError("buildable_poly mangler eller er tom")

    params = PROFILE_PARAMS[profile]

    # 1. PCA
    theta_major, major_m, minor_m, _cx, _cy = _pca_axes(buildable_poly)
    elongation = major_m / max(minor_m, 1.0)

    # 2. Naboasymmetri
    asymmetry = compute_neighbor_asymmetry(buildable_poly, neighbors)

    # 3. Beslutning
    if force_axis_type in ("diagonal", "orthogonal"):
        axis_type = force_axis_type
        rationale_prefix = "Tvunget av konseptet"
    elif elongation > params.elongation_threshold:
        axis_type = "orthogonal"
        rationale_prefix = (
            f"Elongation {elongation:.2f} > {params.elongation_threshold} "
            f"— tomten er langstrakt, ortogonal struktur gir rettere kvartalsrekker"
        )
    elif asymmetry > params.neighbor_asymmetry_threshold:
        axis_type = "orthogonal"
        rationale_prefix = (
            f"Nabo-asymmetri {asymmetry:.2f} > {params.neighbor_asymmetry_threshold} "
            f"— ortogonal N-S følger naboskillet"
        )
    else:
        axis_type = "diagonal"
        rationale_prefix = (
            f"Kompakt tomt (elongation {elongation:.2f}) med jevne naboer "
            f"(asymmetri {asymmetry:.2f}) — diagonal gir best sol-eksponering "
            f"og en tydelig grøntforbindelse"
        )

    # 4. Bestem primær-akse-vinkel
    if axis_type == "orthogonal":
        # Hvis major-aksen er nær Ø-V (theta≈0), velger vi den som primær
        # (gir lengst akse gjennom tomten). Hvis major er N-S, velger vi den.
        # I realiteten: bruk theta_major direkte som primær.
        primary_theta = theta_major
    else:
        # Diagonal: ±45° fra major
        primary_theta = _best_diagonal_theta_for_sun(theta_major, latitude_deg)

    # 5. Lag linje
    primary_line = _build_axis_linestring(buildable_poly, primary_theta)
    if primary_line is None or primary_line.length < 10.0:
        # Degenerert tomt — fall tilbake til ingen akse
        return MasterplanAxes(
            axis_type="none",
            profile=profile,
            rationale="Kunne ikke konstruere akse (tomt for liten/degenerert)",
            elongation=elongation,
            neighbor_asymmetry=asymmetry,
            primary_orientation_deg=primary_theta,
        )

    # 6. Sekundær-akse: vinkelrett på primær, gjennom samme centroid
    secondary_theta = (primary_theta + 90.0) % 180.0
    secondary_line = _build_axis_linestring(buildable_poly, secondary_theta)
    # Sekundær droppes hvis den er vesentlig kortere enn primær (mindre enn 40%)
    if secondary_line is not None and secondary_line.length < primary_line.length * 0.40:
        secondary_line = None

    # 7. Korridor-polygoner (buffret rundt aksene)
    corridors: List[Polygon] = []
    primary_corridor = primary_line.buffer(
        params.primary_axis_width_m / 2.0,
        cap_style=2,  # flat endring (ikke rund) ved enden
        join_style=2,
    )
    primary_corridor = primary_corridor.intersection(buildable_poly)
    if not primary_corridor.is_empty and primary_corridor.area > 50.0:
        # Kan bli MultiPolygon — splitt til flatliste
        if hasattr(primary_corridor, "geoms"):
            for g in primary_corridor.geoms:
                if isinstance(g, Polygon) and not g.is_empty:
                    corridors.append(g.buffer(0))
        elif isinstance(primary_corridor, Polygon):
            corridors.append(primary_corridor.buffer(0))

    if secondary_line is not None:
        secondary_corridor = secondary_line.buffer(
            params.secondary_axis_width_m / 2.0,
            cap_style=2,
            join_style=2,
        )
        secondary_corridor = secondary_corridor.intersection(buildable_poly)
        if not secondary_corridor.is_empty and secondary_corridor.area > 30.0:
            if hasattr(secondary_corridor, "geoms"):
                for g in secondary_corridor.geoms:
                    if isinstance(g, Polygon) and not g.is_empty:
                        corridors.append(g.buffer(0))
            elif isinstance(secondary_corridor, Polygon):
                corridors.append(secondary_corridor.buffer(0))

    # 8. Safety check — korridorene skal ikke sluke for mye av tomten
    total_corridor_area = sum(c.area for c in corridors)
    site_area = buildable_poly.area
    remaining_ratio = 1.0 - (total_corridor_area / site_area)
    if remaining_ratio < params.min_field_ratio:
        # Korridorene er for brede — krymp dem
        shrink_factor = (1.0 - params.min_field_ratio) / max(total_corridor_area / site_area, 1e-6)
        shrunk_corridors: List[Polygon] = []
        for c in corridors:
            # Enklere: negative buffer på korridoren selv
            shrink_m = (params.primary_axis_width_m / 2.0) * (1.0 - shrink_factor)
            shrunk = c.buffer(-max(shrink_m, 0.5))
            if not shrunk.is_empty:
                if hasattr(shrunk, "geoms"):
                    for g in shrunk.geoms:
                        if isinstance(g, Polygon) and g.area > 30.0:
                            shrunk_corridors.append(g.buffer(0))
                elif isinstance(shrunk, Polygon) and shrunk.area > 30.0:
                    shrunk_corridors.append(shrunk.buffer(0))
        if shrunk_corridors:
            corridors = shrunk_corridors

    # 9. Torg-noder
    torg_polygons, torg_nodes = compute_torg_nodes(
        primary_line=primary_line,
        secondary_line=secondary_line,
        buildable_poly=buildable_poly,
        params=params,
    )

    # 10. Rationale-tekst
    rationale = (
        f"{rationale_prefix}. Valgt: {axis_type} med hovedakse-vinkel "
        f"{primary_theta:.0f}°, lengde {primary_line.length:.0f}m."
    )
    if secondary_line is not None:
        rationale += f" Sekundær-akse {secondary_theta:.0f}°, lengde {secondary_line.length:.0f}m."
    if torg_nodes:
        rationale += f" {len(torg_nodes)} torg-noder."

    return MasterplanAxes(
        primary_axis=primary_line,
        secondary_axis=secondary_line,
        corridor_polygons=corridors,
        torg_polygons=torg_polygons,
        torg_nodes=torg_nodes,
        axis_type=axis_type,
        profile=profile,
        rationale=rationale,
        elongation=elongation,
        neighbor_asymmetry=asymmetry,
        primary_orientation_deg=primary_theta,
    )


# ---------------------------------------------------------------------------
# Torg-analyse
# ---------------------------------------------------------------------------


def compute_torg_nodes(
    *,
    primary_line: LineString,
    secondary_line: Optional[LineString],
    buildable_poly: Polygon,
    params: ProfileParameters,
) -> Tuple[List[Polygon], List[Point]]:
    """Plasser torg-noder langs primær-aksen.

    Regler:
      - Hvis sekundær-akse finnes: torg ved krysset (midt på primær).
      - I tillegg: torg nær hver ende hvis primær-aksen er > 80m.
      - Antall torg begrenses av torg_count_min/max.
      - Hvert torg er en sirkel-polygon med radius torg_radius_m,
        klippet mot buildable_poly.
    """
    if primary_line is None or primary_line.is_empty:
        return ([], [])

    candidate_points: List[Point] = []

    # 1. Krysset med sekundær-akse (hvis finnes)
    if secondary_line is not None and not secondary_line.is_empty:
        cross = primary_line.intersection(secondary_line)
        if isinstance(cross, Point) and not cross.is_empty:
            candidate_points.append(cross)

    # 2. Torg nær endene for lange akser
    total_len = primary_line.length
    if total_len > 80.0:
        # ~20% og ~80% langs aksen
        candidate_points.insert(0, primary_line.interpolate(0.22, normalized=True))
        candidate_points.append(primary_line.interpolate(0.78, normalized=True))
    elif total_len > 45.0 and not candidate_points:
        # Kort akse uten sekundær-kryss: ett torg i midten
        candidate_points.append(primary_line.interpolate(0.5, normalized=True))

    # 3. Dedupliser (fjern punkter som er < 35m fra hverandre)
    deduped: List[Point] = []
    for p in candidate_points:
        if all(p.distance(other) > 35.0 for other in deduped):
            deduped.append(p)

    # 4. Begrens til count_min/max
    if len(deduped) > params.torg_count_max:
        # Behold jevnt fordelt
        if params.torg_count_max == 1:
            deduped = [deduped[len(deduped) // 2]]
        else:
            step = (len(deduped) - 1) / (params.torg_count_max - 1)
            deduped = [deduped[int(round(i * step))] for i in range(params.torg_count_max)]
    elif len(deduped) < params.torg_count_min and total_len > 30.0:
        # Legg til midten hvis vi mangler
        mid = primary_line.interpolate(0.5, normalized=True)
        if all(mid.distance(other) > 15.0 for other in deduped):
            deduped.append(mid)

    # 5. Bygg sirkel-polygoner
    torg_polys: List[Polygon] = []
    torg_points: List[Point] = []
    for pt in deduped:
        circle = pt.buffer(params.torg_radius_m, quad_segs=16)
        clipped = circle.intersection(buildable_poly)
        if clipped.is_empty or clipped.area < 20.0:
            continue
        # Ta hovedbit hvis multi
        if hasattr(clipped, "geoms"):
            parts = [g for g in clipped.geoms if isinstance(g, Polygon)]
            if not parts:
                continue
            clipped = max(parts, key=lambda g: g.area)
        if isinstance(clipped, Polygon):
            torg_polys.append(clipped.buffer(0))
            torg_points.append(pt)

    return (torg_polys, torg_points)


# ---------------------------------------------------------------------------
# Hjelpefunksjoner for motor-integrasjon
# ---------------------------------------------------------------------------


def subtract_axes_from_buildable(
    buildable_poly: Polygon,
    axes: MasterplanAxes,
) -> Polygon:
    """Trekk korridor + torg ut av buildable_poly.

    Returnerer det polygonet delfelt-splittingen skal operere på.
    Hvis axes er tom, returnerer buildable_poly uendret.

    Hvis resultatet blir en MultiPolygon (aksene delte tomten i to),
    returneres unionen som MultiPolygon-ekvivalent Polygon hvis mulig,
    ellers største delen. Delfelt-splittingen håndterer selv
    MultiPolygon-tilfeller — se geometry.subdivide_buildable_polygon.
    """
    if axes is None or (not axes.corridor_polygons and not axes.torg_polygons):
        return buildable_poly

    subtract_union_parts: List[Polygon] = []
    subtract_union_parts.extend(axes.corridor_polygons)
    subtract_union_parts.extend(axes.torg_polygons)
    if not subtract_union_parts:
        return buildable_poly

    try:
        subtract_union = unary_union(subtract_union_parts)
        result = buildable_poly.difference(subtract_union)
        if result.is_empty:
            return buildable_poly
        return result.buffer(0)
    except Exception:
        return buildable_poly


def axes_to_export_dict(axes: MasterplanAxes) -> Dict[str, Any]:
    """Serialiser MasterplanAxes til UI-vennlig dict.

    Brukes av legacy_page_bridge._legacy_geometry_for_plan.
    Format matcher eksisterende field_polygons / public_realm_polygons-
    mønsteret (liste av dicts med "coords").
    """
    def poly_coords(poly: Polygon) -> List[List[List[float]]]:
        if poly is None or poly.is_empty:
            return []
        return [[[float(x), float(y)] for x, y in list(poly.exterior.coords)]]

    def line_coords(line: Optional[LineString]) -> List[List[float]]:
        if line is None or line.is_empty:
            return []
        return [[float(x), float(y)] for x, y in list(line.coords)]

    corridor_polygons: List[Dict[str, Any]] = []
    for idx, poly in enumerate(axes.corridor_polygons):
        corridor_polygons.append({
            "axis_role": "primary" if idx == 0 else "secondary",
            "coords": poly_coords(poly),
        })

    torg_polygons: List[Dict[str, Any]] = []
    for idx, poly in enumerate(axes.torg_polygons):
        torg_polygons.append({
            "torg_index": idx,
            "coords": poly_coords(poly),
        })

    return {
        "masterplan_axes_polygons": corridor_polygons,
        "torg_polygons": torg_polygons,
        "primary_axis_line": line_coords(axes.primary_axis),
        "secondary_axis_line": line_coords(axes.secondary_axis),
        "axis_type": axes.axis_type,
        "axis_profile": axes.profile.value,
        "axis_rationale": axes.rationale,
    }


# ---------------------------------------------------------------------------
# Konsept→akse-override-tabell
# ---------------------------------------------------------------------------


CONCEPT_AXIS_PREFERENCE: Dict[str, Optional[str]] = {
    # Konsept-familie -> foretrukket akse-type (None = bruk auto)
    "COURTYARD_URBAN": "diagonal",   # LPO-stilen — krever grøntforbindelse
    "LINEAR_MIXED": "orthogonal",    # Kibnes-stilen — rette kvartalsrekker
    "CLUSTER_PARK": None,            # Bruk auto — punkthus i park har ikke sterkt akse-behov
}


def resolve_axis_type_for_concept(
    concept_family_value: str,
    auto_axis_type: str,
    *,
    override_threshold_elongation: float = 1.9,
    elongation: float = 0.0,
) -> Optional[str]:
    """Bestem om konseptets foretrukne akse skal overstyre auto.

    Regel: Overstyr BARE hvis tomten ikke er ekstremt langstrakt
    (elongation < 1.9). For veldig lange tomter tvinger geometrien
    ortogonal uansett — da dropper vi override selv om konseptet
    foretrekker diagonal.

    Returnerer "diagonal" | "orthogonal" | None (= ikke overstyr).
    """
    preference = CONCEPT_AXIS_PREFERENCE.get(concept_family_value)
    if preference is None:
        return None
    if preference == auto_axis_type:
        return None  # Ingen override trengs
    if preference == "diagonal" and elongation >= override_threshold_elongation:
        # Ikke tving diagonal på ekstremt lange tomter
        return None
    return preference
