"""
Builtly Masterplan Engine — 6-pass arkitektur.

Erstatter den gamle A/B/C-alternativtenkningen med én helhetlig masterplan
bestående av programmiks, typologisoner, volumer, uterom og byggefaser.

PASS-OVERSIKT:
  Pass 0: Phase Count Recommendation (rask Claude-kall, bare i auto-modus)
  Pass 1: Site Program Synthesis (Claude)
          → bolig/barnehage/næring-fordeling, MUA-krav, parkering
  Pass 2: Typology Zoning (Claude + Shapely)
          → soner for lamell/punkt/rekke basert på kontekst
  Pass 3: Volume Placement (Python deterministisk)
          → plasser volumer innen soner, utnytter eksisterende Pass 2 fra ai_site_planner
  Pass 4: Phasing — Building + Parking (kombinert optimalisering)
          → grupper volumer i K byggefaser, P-kjeller i M parkeringsfaser
  Pass 5: Outdoor System (Claude + geometri)
          → diagonal, tun, MUA bakke/tak/privat, gangnett
  Pass 6: Validation & Refinement (Claude-review + hard TEK-validering)
          → standalone-bokvalitet per fase, brannkrav, BYA, MUA-compliance

Eksporterer: plan_masterplan() — hovedentry.
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

import requests

from masterplan_types import (
    BuildingPhase,
    Entrance,
    Masterplan,
    MasterplanMetrics,
    OutdoorSystem,
    OutdoorZone,
    ParkingPhase,
    ParkingRamp,
    PhasingConfig,
    ProgramAllocation,
    ProgramKind,
    TypologyKind,
    TypologyZone,
    Volume,
    HAS_SHAPELY,
)

logger = logging.getLogger(__name__)

try:
    from shapely.geometry import (
        Polygon, MultiPolygon, Point, LineString, box as shapely_box
    )
    from shapely.ops import unary_union, nearest_points, split as shapely_split
    from shapely import affinity
except Exception:
    HAS_SHAPELY = False

# Import av dagens 4-pass for volumplassering (Pass 3 i vår arkitektur)
try:
    import ai_site_planner as legacy_planner
    HAS_LEGACY = True
except Exception:
    HAS_LEGACY = False
    legacy_planner = None


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-opus-4-6"

# Typologi-dimensjoner (kopiert fra ai_site_planner, men her er de masterplan-eide)
TYPOLOGY_DIMS = {
    "Lamell":         {"w_min": 30, "w_max": 60, "d_min": 12, "d_max": 16, "f_min": 3, "f_max": 7,  "ftf": 3.2, "units_per_floor": 3},
    "Punkthus":       {"w_min": 18, "w_max": 26, "d_min": 18, "d_max": 26, "f_min": 4, "f_max": 12, "ftf": 3.0, "units_per_floor": 3},
    "Karré":          {"w_min": 38, "w_max": 55, "d_min": 38, "d_max": 55, "f_min": 3, "f_max": 7,  "ftf": 3.2, "units_per_floor": 6},
    "Rekke":          {"w_min": 35, "w_max": 60, "d_min": 8,  "d_max": 12, "f_min": 2, "f_max": 3,  "ftf": 2.8, "units_per_floor": 1},
    "Tun":            {"w_min": 25, "w_max": 50, "d_min": 10, "d_max": 14, "f_min": 3, "f_max": 6,  "ftf": 3.0, "units_per_floor": 3},
    "Tårn":           {"w_min": 18, "w_max": 26, "d_min": 18, "d_max": 26, "f_min": 6, "f_max": 16, "ftf": 3.0, "units_per_floor": 4},
    "Podium + Tårn":  {"w_min": 35, "w_max": 55, "d_min": 18, "d_max": 28, "f_min": 2, "f_max": 4,  "ftf": 3.5, "units_per_floor": 0},
    # Komponerte typologier — flere segmenter som sammen danner en komposisjon
    "LamellSegmentert": {"w_min": 15, "w_max": 25, "d_min": 12, "d_max": 16, "f_min": 3, "f_max": 6,  "ftf": 3.2, "units_per_floor": 2},
    "HalvåpenKarré":    {"w_min": 25, "w_max": 45, "d_min": 12, "d_max": 14, "f_min": 3, "f_max": 6,  "ftf": 3.2, "units_per_floor": 3},
    "Gårdsklynge":      {"w_min": 18, "w_max": 28, "d_min": 12, "d_max": 14, "f_min": 3, "f_max": 5,  "ftf": 3.2, "units_per_floor": 2},
}

# Komposisjons-metadata for sammensatte typologier. Definerer hvordan
# segmenter skal arrangeres rundt et sentralt uterom (gårdsrom).
#
# "layout" kan være:
#   - "linear":     segmenter i rad med gap mellom (~8m) — som LPO nordblokk
#   - "u_shape":    U-form rundt gårdsrom åpent mot én retning
#   - "o_shape":    O-form (halvåpen karré) med små gap i hjørnene
#   - "cluster":    fri klynge av 4-6 segmenter rundt et felles uterom
TYPOLOGY_COMPOSITIONS = {
    "LamellSegmentert": {
        "layout": "linear",
        "min_segments": 3,
        "max_segments": 6,
        "segment_gap_m": 8.0,
        "gap_rhythm_m": 10.0,      # bredde på siktakser mellom segmenter
        "orientation": "long_axis", # parallelt med sonens lengste akse
        "description": "Kortere lameller i rad med siktakser mellom — LPO Tyholt nordblokk-stil",
    },
    "HalvåpenKarré": {
        "layout": "o_shape",
        "min_segments": 3,
        "max_segments": 4,
        "segment_gap_m": 6.0,       # små åpninger mellom L-segmenter
        "courtyard_min_dim_m": 18.0, # minimum gårdsrom-dimensjon
        "courtyard_target_ratio": 0.35, # gårdsrom = 35% av komposisjonens totalareal
        "description": "L-segmenter rundt gårdsrom med gap i hjørnene — Tyholt midtblokk-stil",
    },
    "Gårdsklynge": {
        "layout": "cluster",
        "min_segments": 4,
        "max_segments": 6,
        "segment_gap_m": 8.0,
        "courtyard_min_dim_m": 15.0,
        "description": "Klynge av kortere segmenter rundt delt uterom — Tyholt sørblokk-stil",
    },
}

# TEK17 / KPA-konstanter
MIN_BUILDING_SPACING = 8.0
MIN_BOUNDARY_SETBACK = 4.0

# Byggesone 2 (Trondheim KPA 2022-34)
BYGGESONE2_MUA_PER_BOLIG = 40.0
BYGGESONE2_MUA_FELLES_MIN_FRAC = 0.5
BYGGESONE2_MUA_BAKKE_MIN_FRAC = 0.5    # av fellesarealet, min 50% på bakkeplan

# Barnehage (6-base, typisk norsk)
BARNEHAGE_6BASE_INDOOR_BRA = 1279.0
BARNEHAGE_6BASE_OUTDOOR_M2 = 2448.0

# Parkeringsnorm byggesone 2 (KPA): min 0.2 / maks 0.8 bil per 100 m² BRA
PARKING_MIN_PER_100_BRA = 0.2
PARKING_MAX_PER_100_BRA = 0.8
PARKING_DEFAULT_PER_100_BRA = 0.5      # typisk brukt i utviklingsprosjekter


# ─────────────────────────────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────────────────────────────

def _get_api_key() -> Optional[str]:
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")


def is_available() -> bool:
    return bool(_get_api_key()) and HAS_SHAPELY


def _call_claude(prompt: str, api_key: str, model: str = DEFAULT_MODEL,
                 temperature: float = 0.3, max_tokens: int = 4000) -> Optional[str]:
    """Lavt-nivå Claude-kall. Returnerer ren tekst eller None."""
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
            },
            timeout=180,
        )
        resp.raise_for_status()
        for block in resp.json().get("content", []):
            if block.get("type") == "text":
                return block["text"]
    except Exception as exc:
        logger.error(f"Claude API error: {exc}")
    return None


def _parse_json(text: Optional[str]) -> Any:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(l for l in cleaned.split("\n") if not l.strip().startswith("```"))
    # Try object first, then array
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        s = cleaned.find(start_char)
        e = cleaned.rfind(end_char)
        if s >= 0 and e > s:
            try:
                return json.loads(cleaned[s:e+1])
            except json.JSONDecodeError:
                continue
    return None


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────────────────────────────
# Kontekstsammendrag for Claude-prompter
# ─────────────────────────────────────────────────────────────────────

def _summarize_neighbor_context(neighbors: Optional[List[Dict[str, Any]]],
                                site_polygon) -> Tuple[str, List[Dict[str, Any]]]:
    """Bygg tekstlig nabosammendrag og typologi-hint per verdensretning."""
    if not neighbors or not site_polygon:
        return "NABOER: Ingen registrerte", []

    sc = site_polygon.centroid
    nb_by_compass: Dict[str, List[float]] = {k: [] for k in
                                             ["N", "NØ", "Ø", "SØ", "S", "SV", "V", "NV"]}
    nb_polys = []
    for nb in neighbors[:50]:
        p = nb.get("polygon")
        if not p or p.is_empty:
            continue
        if site_polygon and p.intersection(site_polygon).area / max(float(p.area), 1.0) >= 0.3:
            continue
        nc = p.centroid
        dist = nc.distance(sc)
        if dist > 200:  # for langt unna til å påvirke typologi-sonering
            continue
        ang = math.degrees(math.atan2(nc.y - sc.y, nc.x - sc.x)) % 360
        compass_idx = int((ang + 22.5) / 45) % 8
        compass = ["Ø", "NØ", "N", "NV", "V", "SV", "S", "SØ"][compass_idx]
        h = float(nb.get("height_m", 9.0))
        nb_by_compass[compass].append(h)
        nb_polys.append({"polygon": p, "height_m": h, "dist": dist, "compass": compass})

    lines = []
    for compass in ["N", "NØ", "Ø", "SØ", "S", "SV", "V", "NV"]:
        heights = nb_by_compass[compass]
        if not heights:
            continue
        avg_h = sum(heights) / len(heights)
        max_h = max(heights)
        lines.append(f"  {compass}: {len(heights)} bygg, snitt {avg_h:.0f}m, høyest {max_h:.0f}m")

    text = "NÆRKONTEKST (innen 200m):\n" + ("\n".join(lines) if lines else "  Ingen registrerte")
    return text, nb_polys


def _classify_smallhouse_proximity(nb_polys: List[Dict[str, Any]]) -> Dict[str, float]:
    """Finn nærmeste småhus-avstand per verdensretning (for nedtrapping)."""
    # Småhus = høyde ≤ 10m (typisk enebolig/2-mannsbolig/rekkehus)
    smallhouse_distances: Dict[str, float] = {}
    for item in nb_polys:
        if item["height_m"] > 10:
            continue
        c = item["compass"]
        d = item["dist"]
        if c not in smallhouse_distances or d < smallhouse_distances[c]:
            smallhouse_distances[c] = d
    return smallhouse_distances


def _analyze_terrain(terrain: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Trekk ut nyttige terreng-karakteristikker for typologi-sonering.

    Tar Mulighetsstudie.py-format: {point_count, slope_pct, relief_m, a, b, c,
    min_elev_m, max_elev_m, sample_points, ...}

    Returnerer:
    {
      has_data: bool,
      slope_pct: float,          # helning i prosent (regresjonsplan)
      relief_m: float,           # total høydeforskjell
      is_steep: bool,            # > 15%
      is_very_steep: bool,       # > 25%
      is_sloped: bool,           # > 8%
      slope_azimuth_deg: float,  # retning hellingen går NEDOVER
      slope_compass: str,        # "N", "NØ", "Ø", osv. — hvilken vei hellingen går
      rationale: str,            # kort tekstlig beskrivelse
    }
    """
    out = {
        "has_data": False,
        "slope_pct": 0.0,
        "relief_m": 0.0,
        "is_steep": False,
        "is_very_steep": False,
        "is_sloped": False,
        "slope_azimuth_deg": 180.0,
        "slope_compass": "S",
        "rationale": "Flatt terreng (eller ikke angitt)",
    }
    if not terrain or not isinstance(terrain, dict):
        return out
    pc = terrain.get("point_count", 0) or 0
    if pc <= 0:
        return out

    slope_pct = float(terrain.get("slope_pct", 0.0) or 0.0)
    relief_m = float(terrain.get("relief_m", 0.0) or 0.0)

    # Retning hellingen peker (nedover) kan avledes fra regresjonsplanets
    # koeffisienter a (∂z/∂x) og b (∂z/∂y). Hvis a > 0 betyr det at z øker
    # med x — dvs terrenget stiger mot øst, som betyr hellingen GÅR NED mot VEST.
    a = float(terrain.get("a", 0.0) or 0.0)
    b = float(terrain.get("b", 0.0) or 0.0)
    # Nedoverretning = -grad = (-a, -b) i (x, y) → azimut fra N målt med klokken:
    # N er +y, Ø er +x, azimut_deg = math.degrees(atan2(dx, dy)) med dx=(-a), dy=(-b)
    if abs(a) > 1e-6 or abs(b) > 1e-6:
        import math as _m
        az = _m.degrees(_m.atan2(-a, -b)) % 360.0
        out["slope_azimuth_deg"] = az
        idx = int((az + 22.5) / 45) % 8
        out["slope_compass"] = ["N", "NØ", "Ø", "SØ", "S", "SV", "V", "NV"][idx]

    out["has_data"] = True
    out["slope_pct"] = slope_pct
    out["relief_m"] = relief_m
    out["is_sloped"] = slope_pct > 8
    out["is_steep"] = slope_pct > 15
    out["is_very_steep"] = slope_pct > 25

    if out["is_very_steep"]:
        out["rationale"] = (
            f"Meget bratt terreng ({slope_pct:.1f}% fall, "
            f"{relief_m:.1f}m relieff, nedover mot {out['slope_compass']}). "
            f"Krever omfattende terrengarbeid, støttemurer og dyr fundamentering. "
            f"Anbefal lave typologier og forskyving langs koter."
        )
    elif out["is_steep"]:
        out["rationale"] = (
            f"Bratt terreng ({slope_pct:.1f}% fall, "
            f"{relief_m:.1f}m relieff, nedover mot {out['slope_compass']}). "
            f"Høye volumer skaper skygge nedover hellingen. "
            f"Vurder nedtrapping av etasjeantall langs fall-retning."
        )
    elif out["is_sloped"]:
        out["rationale"] = (
            f"Moderat helning ({slope_pct:.1f}% fall mot {out['slope_compass']}). "
            f"Håndterbart, men krever oppmerksomhet på sokkel-/bakkeplan og "
            f"tilgjengelighet."
        )
    else:
        out["rationale"] = f"Relativt flat tomt ({slope_pct:.1f}%, relieff {relief_m:.1f}m)."
    return out


# ─────────────────────────────────────────────────────────────────────
# PASS 0: Phase Count Recommendation
# ─────────────────────────────────────────────────────────────────────

def pass0_recommend_phase_count(
    target_bra: float,
    buildable_polygon,
    phasing_config: PhasingConfig,
    neighbor_summary: str = "",
    site_program: Optional[ProgramAllocation] = None,
) -> Dict[str, Any]:
    """Rask anbefaling av antall faser. Bruker Claude KUN i auto-modus.

    Returnerer: {recommended, min, max, reasoning, avg_bra_per_phase}
    """
    rec, mn, mx = phasing_config.recommended_phase_count(target_bra)

    # I single/manual-modus bruker vi regelen direkte, ingen Claude.
    if phasing_config.phasing_mode != "auto":
        k = phasing_config.resolve_phase_count(target_bra)
        return {
            "recommended": k,
            "min": k,
            "max": k,
            "reasoning": f"Manuelt satt til {k} faser.",
            "avg_bra_per_phase": target_bra / max(k, 1),
        }

    # I auto-modus kan vi la Claude justere basert på tomt-form og adkomst.
    # Men for små prosjekter er regelen alene god nok.
    if target_bra < phasing_config.SINGLE_PHASE_MAX_BRA:
        return {
            "recommended": rec,
            "min": mn,
            "max": mx,
            "reasoning": (
                f"Målet er {target_bra:.0f} m² BRA. Under {phasing_config.SINGLE_PHASE_MAX_BRA:.0f} "
                f"kan prosjektet gå som ett eller to byggetrinn."
            ),
            "avg_bra_per_phase": target_bra / max(rec, 1),
        }

    # Større prosjekter: la Claude vurdere tomt-geometri
    api_key = _get_api_key()
    if not api_key or buildable_polygon is None:
        return {
            "recommended": rec,
            "min": mn,
            "max": mx,
            "reasoning": "Standard regel: ~4000 m² BRA per byggetrinn.",
            "avg_bra_per_phase": target_bra / max(rec, 1),
        }

    bounds = buildable_polygon.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]
    area = buildable_polygon.area
    aspect_ratio = max(bw, bh) / max(min(bw, bh), 1.0)

    prog_text = ""
    if site_program:
        prog_text = (
            f"\nPROGRAM: bolig {site_program.bolig_bra:.0f} m², "
            f"barnehage {site_program.barnehage_bra:.0f} m², "
            f"næring {site_program.naering_bra:.0f} m²"
        )

    prompt = f"""Du er en norsk boligutvikler som skal vurdere faseinndeling for et utviklingsprosjekt.

TOMT:
- Byggbart areal: {area:.0f} m²
- Bounding box: {bw:.0f} x {bh:.0f} m (aspekt {aspect_ratio:.1f})
- Mål BRA: {target_bra:.0f} m²
{prog_text}

{neighbor_summary}

REGEL:
- Typisk byggetrinn: 3500-4500 m² BRA (snitt 4000)
- Min 2500, maks 6500 per trinn
- Små tomter under 7500 m² BRA kan være 1-2 trinn
- Anbefaling fra regelen: {rec} trinn (snitt {target_bra/max(rec,1):.0f} m² per trinn)

VURDER:
- Tomte-form (smal/lang ≠ kompakt/kvadratisk)
- Realistisk antall adkomstpunkter fra offentlig veg
- Rekkefølgekrav (barnehage/park må ofte i tidlige trinn)
- Salgsrytme (50-80 enheter per trinn er vanlig for utvikler)

OPPGAVE:
Gi anbefalt antall byggetrinn og begrunnelse. Juster regelens {rec} om tomt-geometri
eller kontekst tilsier det. Svar KUN med JSON:

{{
  "recommended_phase_count": {rec},
  "min_reasonable": {mn},
  "max_reasonable": {mx},
  "reasoning": "kort begrunnelse på norsk"
}}"""

    raw = _call_claude(prompt, api_key, temperature=0.2, max_tokens=800)
    parsed = _parse_json(raw)

    if not parsed or "recommended_phase_count" not in parsed:
        return {
            "recommended": rec,
            "min": mn,
            "max": mx,
            "reasoning": "Standard regel: ~4000 m² BRA per byggetrinn.",
            "avg_bra_per_phase": target_bra / max(rec, 1),
        }

    # Clamp Claudes forslag til rimelige grenser
    k = _clamp(int(parsed.get("recommended_phase_count", rec)), mn, mx)
    return {
        "recommended": k,
        "min": int(parsed.get("min_reasonable", mn)),
        "max": int(parsed.get("max_reasonable", mx)),
        "reasoning": str(parsed.get("reasoning", "")),
        "avg_bra_per_phase": target_bra / max(k, 1),
    }


# ─────────────────────────────────────────────────────────────────────
# PASS 1: Site Program Synthesis
# ─────────────────────────────────────────────────────────────────────

def pass1_program_synthesis(
    target_bra: float,
    buildable_polygon,
    site_inputs: Dict[str, Any],
    neighbor_summary: str = "",
    byggesone: Literal["1", "2", "3", "4"] = "2",
    include_barnehage: bool = False,
    include_naering: bool = False,
) -> ProgramAllocation:
    """Bestem programmiks: bolig / barnehage / næring + avledede krav."""

    # Default: alt til bolig
    prog = ProgramAllocation(
        bolig_bra=target_bra,
        notes=f"Byggesone {byggesone}. Ren bolig som utgangspunkt."
    )

    if include_barnehage:
        prog.barnehage_bra = BARNEHAGE_6BASE_INDOOR_BRA
        prog.barnehage_uteareal_m2 = BARNEHAGE_6BASE_OUTDOOR_M2
        prog.bolig_bra = max(0, target_bra - BARNEHAGE_6BASE_INDOOR_BRA)

    if include_naering:
        # Næring typisk 2-5% av total BRA, maks 1500 m² dagligvare i byggesone 2
        naering_target = min(1500.0, target_bra * 0.03)
        prog.naering_bra = naering_target
        prog.bolig_bra = max(0, prog.bolig_bra - naering_target)

    # Beregn MUA-krav (byggesone 2: 40 m² per bolig)
    units = prog.unit_estimate(avg_unit_bra=float(site_inputs.get("avg_unit_bra", 55.0)))
    prog.mua_total_required = units * BYGGESONE2_MUA_PER_BOLIG
    prog.mua_felles_min = prog.mua_total_required * BYGGESONE2_MUA_FELLES_MIN_FRAC
    prog.mua_bakke_min = prog.mua_felles_min * BYGGESONE2_MUA_BAKKE_MIN_FRAC

    # Parkering
    prog.parking_spaces_required = int(round(
        (target_bra / 100.0) * PARKING_DEFAULT_PER_100_BRA
    ))

    # Valgfri AI-forfining hvis API er tilgjengelig og kompleks kontekst
    api_key = _get_api_key()
    if api_key and (include_barnehage or include_naering) and target_bra > 10000:
        prog = _refine_program_with_claude(prog, target_bra, site_inputs,
                                           neighbor_summary, api_key)

    return prog


def _refine_program_with_claude(prog: ProgramAllocation, target_bra: float,
                                site_inputs: Dict[str, Any], neighbor_summary: str,
                                api_key: str) -> ProgramAllocation:
    """Claude justerer programmiks basert på kontekst."""
    prompt = f"""Du er en erfaren norsk byplanlegger. Vurder programmiksen for dette prosjektet.

MÅL: {target_bra:.0f} m² BRA totalt
UTGANGSPUNKT:
- Bolig: {prog.bolig_bra:.0f} m²
- Barnehage: {prog.barnehage_bra:.0f} m²
- Næring/service: {prog.naering_bra:.0f} m²

{neighbor_summary}

Juster arealene hvis kontekst tilsier det (f.eks. mer næring nær sentrum,
lavere barnehage-andel hvis tomt er liten). Total må summere til ~{target_bra:.0f}.
Svar KUN med JSON:
{{
  "bolig_bra": {prog.bolig_bra:.0f},
  "barnehage_bra": {prog.barnehage_bra:.0f},
  "naering_bra": {prog.naering_bra:.0f},
  "notes": "kort begrunnelse"
}}"""

    raw = _call_claude(prompt, api_key, temperature=0.2, max_tokens=500)
    parsed = _parse_json(raw)
    if parsed:
        prog.bolig_bra = float(parsed.get("bolig_bra", prog.bolig_bra))
        prog.barnehage_bra = float(parsed.get("barnehage_bra", prog.barnehage_bra))
        prog.naering_bra = float(parsed.get("naering_bra", prog.naering_bra))
        prog.notes = str(parsed.get("notes", prog.notes))
        # Oppdater avledede krav
        avg_unit = float(site_inputs.get("avg_unit_bra", 55.0))
        units = prog.unit_estimate(avg_unit)
        prog.mua_total_required = units * BYGGESONE2_MUA_PER_BOLIG
        prog.mua_felles_min = prog.mua_total_required * BYGGESONE2_MUA_FELLES_MIN_FRAC
        prog.mua_bakke_min = prog.mua_felles_min * BYGGESONE2_MUA_BAKKE_MIN_FRAC
    return prog


# ─────────────────────────────────────────────────────────────────────
# PASS 2: Typology Zoning
# ─────────────────────────────────────────────────────────────────────

def pass2_typology_zoning(
    buildable_polygon,
    neighbor_summary: str,
    nb_polys: List[Dict[str, Any]],
    program: ProgramAllocation,
    max_floors: int,
    max_height_m: float,
    terrain: Optional[Dict[str, Any]] = None,
    target_bra_m2: float = 0.0,
    max_bya_pct: float = 35.0,
) -> List[TypologyZone]:
    """Del tomta i typologisoner basert på kontekst.

    Strategi:
    - Identifiser nærmeste småhus-retninger → lav typologi der
    - Identifiser hovedveg/urbane kanter → høy typologi der
    - Sentrum av tomta kan ha karré eller større lameller
    - Barnehage plasseres mot rolig side
    - Bratte deler av tomta (>15% helning) → lavere typologi
      (rekke/lamell 2-3 et), fordi høye bygg krever dyr fundamentering
      og skaper skyggeproblemer nedover i hellingen
    """
    if not HAS_SHAPELY or buildable_polygon is None or buildable_polygon.is_empty:
        return []

    smallhouse_dist = _classify_smallhouse_proximity(nb_polys)
    bounds = buildable_polygon.bounds
    cx = (bounds[0] + bounds[2]) / 2.0
    cy = (bounds[1] + bounds[3]) / 2.0
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]

    # Terrenganalyse: beregn helningsprosent, relieff, helningsretning
    terrain_info = _analyze_terrain(terrain)

    # Hvor er småhus i klar majoritet? Disse retningene krever nedtrapping.
    compass_vec = {
        "N": (0, 1), "NØ": (0.7, 0.7), "Ø": (1, 0), "SØ": (0.7, -0.7),
        "S": (0, -1), "SV": (-0.7, -0.7), "V": (-1, 0), "NV": (-0.7, 0.7),
    }
    low_directions = [c for c, d in smallhouse_dist.items() if d < 50]

    zones: List[TypologyZone] = []
    zone_counter = 0

    def _next_id():
        nonlocal zone_counter
        zone_counter += 1
        return f"Z{zone_counter:02d}"

    # Bruk Claude til å foreslå soner hvis vi har API-nøkkel
    api_key = _get_api_key()
    claude_zones = None
    if api_key:
        claude_zones = _pass2_claude_zones(
            buildable_polygon, bounds, bw, bh, neighbor_summary,
            program, low_directions, max_floors, max_height_m,
            terrain_info, target_bra_m2, max_bya_pct, api_key,
        )

    if claude_zones:
        # Konverter Claudes soneforslag til TypologyZone-objekter
        for spec in claude_zones:
            poly = _polygon_from_bbox_fraction(
                bounds, spec.get("bbox_fraction", [0, 0, 1, 1])
            )
            if poly is None:
                continue
            clipped = poly.intersection(buildable_polygon).buffer(0)
            if clipped.is_empty or clipped.area < 100:
                continue
            if isinstance(clipped, MultiPolygon):
                clipped = max(clipped.geoms, key=lambda g: g.area)

            typology = spec.get("typology", "Lamell")
            if typology not in TYPOLOGY_DIMS:
                typology = "Lamell"
            dims = TYPOLOGY_DIMS[typology]

            fmin = _clamp(int(spec.get("floors_min", dims["f_min"])),
                          dims["f_min"], min(max_floors, dims["f_max"]))
            fmax = _clamp(int(spec.get("floors_max", fmin + 1)),
                          fmin, min(max_floors, dims["f_max"]))

            # v1.7: Les gårdsrom-spesifikasjon hvis Claude ga oss det
            courtyard_poly = None
            court_frac = spec.get("courtyard_bbox_fraction")
            if court_frac and len(court_frac) == 4:
                cp_raw = _polygon_from_bbox_fraction(bounds, court_frac)
                if cp_raw is not None:
                    # Gårdsrommet må ligge innenfor kvartalet (clipped)
                    cp_clipped = cp_raw.intersection(clipped).buffer(0)
                    if not cp_clipped.is_empty and cp_clipped.area >= 150:
                        if isinstance(cp_clipped, MultiPolygon):
                            cp_clipped = max(cp_clipped.geoms, key=lambda g: g.area)
                        # Sjekk at gårdsrom har sunn geometri (min 12m bredde)
                        cb = cp_clipped.bounds
                        cw = cb[2] - cb[0]
                        ch = cb[3] - cb[1]
                        if cw >= 12 and ch >= 12:
                            courtyard_poly = cp_clipped

            zones.append(TypologyZone(
                zone_id=_next_id(),
                typology=typology,
                polygon=clipped,
                floors_min=fmin,
                floors_max=fmax,
                target_bra=float(spec.get("target_bra_share", 0.0)) * program.total_bra,
                rationale=str(spec.get("rationale", "")),
                courtyard_polygon=courtyard_poly,
                courtyard_name=str(spec.get("courtyard_name", "")),
                courtyard_function=str(spec.get("courtyard_function", "")),
                courtyard_program=str(spec.get("courtyard_program", "")),
            ))

    # Fallback: regelbasert sonering hvis Claude feilet eller ikke ga resultat
    if not zones:
        zones = _fallback_zoning(buildable_polygon, bounds, bw, bh,
                                 low_directions, program, max_floors, terrain_info)

    # Sikre at vi har minst én sone
    if not zones:
        zones.append(TypologyZone(
            zone_id="Z01",
            typology="Lamell",
            polygon=buildable_polygon,
            floors_min=3,
            floors_max=min(max_floors, 5),
            target_bra=program.total_bra,
            rationale="Fallback: hele tomta som én lamellsone.",
        ))

    return zones


def _polygon_from_bbox_fraction(bounds, frac):
    """Konverter [x0_frac, y0_frac, x1_frac, y1_frac] innenfor bounds til polygon."""
    if not HAS_SHAPELY or len(frac) != 4:
        return None
    x0, y0, x1, y1 = bounds
    bw = x1 - x0
    bh = y1 - y0
    px0 = x0 + _clamp(float(frac[0]), 0, 1) * bw
    py0 = y0 + _clamp(float(frac[1]), 0, 1) * bh
    px1 = x0 + _clamp(float(frac[2]), 0, 1) * bw
    py1 = y0 + _clamp(float(frac[3]), 0, 1) * bh
    if px1 <= px0 or py1 <= py0:
        return None
    return shapely_box(px0, py0, px1, py1)


def _pass2_claude_zones(buildable_polygon, bounds, bw, bh, neighbor_summary,
                        program, low_directions, max_floors, max_height_m,
                        terrain_info, target_bra_m2, max_bya_pct, api_key) -> Optional[List[Dict[str, Any]]]:
    """La Claude foreslå typologisoner.

    Kontekst til Claude inkluderer BRA-mål og nødvendig tetthet slik at
    typologi-valg ikke bare velges på estetikk, men også på kapasitet.
    Uten denne konteksten har Claude en tendens til å velge estetiske
    småskala-typologier (Tun, Rekke) selv på store tomter der de ikke
    kan bære BRA-målet.
    """
    coords = [[round(c[0] - bounds[0], 1), round(c[1] - bounds[1], 1)]
              for c in buildable_polygon.exterior.coords]

    low_text = ", ".join(low_directions) if low_directions else "ingen"

    prog_text = f"Bolig {program.bolig_bra:.0f} m²"
    if program.barnehage_bra > 0:
        prog_text += f", barnehage {program.barnehage_bra:.0f} m²"
    if program.naering_bra > 0:
        prog_text += f", næring {program.naering_bra:.0f} m²"

    terrain_text = ""
    if terrain_info and terrain_info.get("has_data"):
        terrain_text = (
            f"\nTERRENG: {terrain_info['rationale']}"
            f"\n  Fall-retning (nedover): {terrain_info['slope_compass']} "
            f"({terrain_info['slope_pct']:.1f}%)"
            f"\n  Steilhet: "
            + ("MEGET BRATT — unngå høye volumer her; lavt fundament foretrekkes"
               if terrain_info['is_very_steep']
               else ("BRATT — anbefal nedtrapping av etasjer langs fall-retning"
                     if terrain_info['is_steep']
                     else ("Moderat fall — håndterbart"
                           if terrain_info['is_sloped']
                           else "Relativt flatt — ingen terrengbegrensning")))
        )

    # Tetthets-kontekst: beregn nødvendig gjennomsnittlig etasjeantall for å bære BRA-målet
    buildable_area = float(buildable_polygon.area)
    max_fp = buildable_area * max_bya_pct / 100.0
    # Hvis tomten fylles opp til maks BYA, hvor mange etasjer trengs i snitt?
    required_avg_floors = target_bra_m2 / max(max_fp * 0.85, 1.0) if target_bra_m2 > 0 else 0
    density_text = ""
    if target_bra_m2 > 0:
        density_tier = "LAV"
        density_rec = ""
        if required_avg_floors >= 5:
            density_tier = "VELDIG HØY"
            density_rec = (
                "UTELUKKENDE høye typologier: Punkthus 5-7 et, Lamell 5-6 et, Karré 5-6 et, "
                "HalvåpenKarré 5-6 et, eller Tårn. UNNGÅ Tun, Rekke og andre småskala-typologier — "
                "de har for lavt fotavtrykk-til-BRA-forhold til å bære BRA-målet."
            )
        elif required_avg_floors >= 3.5:
            density_tier = "HØY"
            density_rec = (
                "Bruk høye urbane typologier: LamellSegmentert 4-5 et (for langstrakte kvartaler), "
                "HalvåpenKarré 4-5 et (for urbane gårdsrom som Tyholt Park), Punkthus 5-6 et, "
                "eller Gårdsklynge 4-5 et. Tun og Rekke er kun OK i små perifer-soner. "
                "UNNGÅ enkle Lamell-monolitter over 50m lange."
            )
        elif required_avg_floors >= 2.5:
            density_tier = "MIDDELS"
            density_rec = (
                "Bruk balansert miks: LamellSegmentert eller HalvåpenKarré 3-4 et som hovedtypologi. "
                "Gårdsklynge og Tun OK som supplement."
            )
        else:
            density_tier = "LAV"
            density_rec = (
                "Alle typologier OK. Tun, Rekke, Lamell 2-3 et, LamellSegmentert 2-3 et passer fint."
            )
        density_text = (
            f"\n\nTETTHETSKRAV (KRITISK):"
            f"\n- BRA-MÅL: {target_bra_m2:.0f} m² på {buildable_area:.0f} m² byggbart"
            f"\n- Maks BYA: {max_bya_pct:.0f}% = {max_fp:.0f} m² fotavtrykk"
            f"\n- Nødvendig snitt-etasjer for å nå målet: {required_avg_floors:.1f}"
            f"\n- Tetthetsklasse: {density_tier}"
            f"\n- TYPOLOGI-RETNINGSLINJE: {density_rec}"
        )

    prompt = f"""Du er Norges fremste byarkitekt. Del denne tomta i typologisoner.

TOMT (byggbart, lokal koordinater fra {bounds[0]:.0f}/{bounds[1]:.0f}):
- Størrelse: {bw:.0f} x {bh:.0f} m
- Polygon-koordinater: {json.dumps(coords[:20])}
- Maks etasjer: {max_floors}, maks høyde: {max_height_m:.0f}m

PROGRAM: {prog_text}

{neighbor_summary}{terrain_text}{density_text}

SMÅHUS-NÆRHET (krever nedtrapping): {low_text}

TYPOLOGIER:
- Lamell: lange bygg 30-60m, 3-7 et (fleksibel, én monolitt)
- LamellSegmentert: 3-6 kortere lameller à 15-25m i rad med siktakser mellom — perfekt for lange kvartaler som trenger mykere skala
- Punkthus: 18-26x18-26m, 4-12 et (urban, best lys)
- Karré: 38-55m firkant med gårdsrom, 3-7 et (tett, bymessig, sluttet)
- HalvåpenKarré: 3-4 L-segmenter rundt gårdsrom med gap i hjørnene — LPO Tyholt-stil, kombinerer karré-romlighet med gjennomsiktighet
- Gårdsklynge: 4-6 kortere segmenter i løs klynge rundt delt uterom — god for større prosjekter som trenger flere mindre gårdsrom
- Rekke: 35-60x8-12m, 2-3 et (småhus-skala)
- Tun: 25-50x10-14m, 3-6 et (klynge rundt uterom)
- Tårn: 18-26x18-26m, 6-16 et (landemerke)
- Podium + Tårn: lavere base med tårn over

STRATEGI:
1. **Foretrekk FÆRRE og STØRRE kvartaler** fremfor mange små. Et godt masterplan
   har typisk 2-3 kvartaler med tydelig identitet (f.eks. "nord langs veg",
   "midt m. gårdsrom", "syd m. barnehage"), ikke 5-6 fragmenter.
2. Nedtrapping mot småhus (lav typologi, 2-4 et)
3. Høyere typologi mot urbane kanter/veg
4. Hvert kvartal bør kunne inneholde flere segmenter (komposisjon), ikke bare ett bygg
5. Barnehage skal i rolig hjørne med egen uteplass
6. Bratt terreng (> 15% fall) → lavere typologi og nedtrapping langs koter

OPPGAVE (viktig — arkitektfaglig metode):
Du tegner tomten som et sett av 2-4 KVARTALER, der hvert kvartal er definert
av SITT gårdsrom. Dette er hvordan LPO tegnet Tyholt Park — de definerte først
tre gårdsrom (nord: "blomster/plantekasser", midt: "trær/grill/blomster",
syd: "lek/uteområde barnehage") og plasserte så bygningene som VEGGEN RUNDT
HVER gårdsrom. Du gjør det samme.

Hver sone har:
  - en YTTERRAMME (bbox_fraction, 0-1) som definerer hele kvartalet
  - et GÅRDSROM inni (courtyard_bbox_fraction) som er sentralt i kvartalet.
    Gårdsrommet må være MIN 15×15m (helst 20×30m eller større).
    Gårdsrommet skal ligge med minst 12m buffer (for bygningsdybde) fra
    kvartalsgrensen på alle sider.

Gårdsrom-funksjoner (velg én per sone):
  - "felles_bolig" → trær, benker, grill, felles utearealer
  - "barnehage_ute" → lekestativer, sandkasser (krever sol; plasser sørvendt)
  - "lek_gront" → vill natur, naturlek, grønt område
  - "plantekasser" → dyrkingsbed, felleshager

Svar KUN med JSON-array:
[
  {{
    "zone_name": "Nord — kvartal mot hovedveg",
    "typology": "LamellSegmentert",
    "bbox_fraction": [0.0, 0.55, 1.0, 1.0],
    "courtyard_bbox_fraction": [0.15, 0.65, 0.85, 0.90],
    "courtyard_name": "Nordgården",
    "courtyard_function": "plantekasser",
    "courtyard_program": "plantekasser, trær, lekeplass",
    "floors_min": 4,
    "floors_max": 5,
    "target_bra_share": 0.40,
    "rationale": "Langstrakt kvartal langs veg — lameller danner vegger rundt felles gårdsrom"
  }},
  ...
]

Sum av target_bra_share skal være ~1.0. Gårdsrom må ligge INNI sin sones
bbox_fraction — ikke utenfor."""

    raw = _call_claude(prompt, api_key, temperature=0.3, max_tokens=2500)
    parsed = _parse_json(raw)
    if isinstance(parsed, list) and parsed:
        return parsed
    return None


def _fallback_zoning(buildable_polygon, bounds, bw, bh,
                     low_directions, program, max_floors, terrain_info=None):
    """Regelbasert sonering hvis Claude feiler.

    Terreng-regel: hvis tomta er bratt (> 15% fall), legg til en "lav"-sone
    i den nedre delen av fall-retningen (nederst på hellingen), fordi:
      - Høye bygg her skaper skygge for bygg/tomter lengre nede
      - Nedre kant av hellingen er typisk der terrenget blir brattest
      - Støttemurer og fundamenter blir dyrere jo lengre ned man bygger
    """
    zones = []
    zone_counter = 0

    def _next_id():
        nonlocal zone_counter
        zone_counter += 1
        return f"Z{zone_counter:02d}"

    # Bestem terrain-bevisst max-floors
    tmax_floors = max_floors
    if terrain_info and terrain_info.get("is_very_steep"):
        tmax_floors = min(max_floors, 3)
    elif terrain_info and terrain_info.get("is_steep"):
        tmax_floors = min(max_floors, 4)

    # Sjekk om terrenget har klar fall-retning som gir terrain-sone
    terrain_sone_added = False
    if terrain_info and terrain_info.get("is_steep"):
        compass = terrain_info.get("slope_compass", "S")
        # Nedre del (der terrenget går lavest) er mot fall-retningen
        # f.eks. hvis hellingen går mot S, er det nedre tredjedel av tomta (lav Y)
        # som blir lavest. Tegn en terrain-lav-sone der.
        frac_map = {
            "N": [0.0, 0.67, 1.0, 1.0],   # nedre del er nord
            "NØ": [0.67, 0.67, 1.0, 1.0],
            "Ø": [0.67, 0.0, 1.0, 1.0],
            "SØ": [0.67, 0.0, 1.0, 0.33],
            "S": [0.0, 0.0, 1.0, 0.33],
            "SV": [0.0, 0.0, 0.33, 0.33],
            "V": [0.0, 0.0, 0.33, 1.0],
            "NV": [0.0, 0.67, 0.33, 1.0],
        }
        frac = frac_map.get(compass)
        if frac:
            poly = _polygon_from_bbox_fraction(bounds, frac)
            if poly:
                clipped = poly.intersection(buildable_polygon).buffer(0)
                if not clipped.is_empty and clipped.area > 100:
                    zones.append(TypologyZone(
                        zone_id=_next_id(),
                        typology="Rekke" if terrain_info.get("is_very_steep") else "Lamell",
                        polygon=clipped,
                        floors_min=2,
                        floors_max=min(3 if terrain_info.get("is_very_steep") else 4, max_floors),
                        target_bra=program.total_bra * 0.25,
                        rationale=(
                            f"Nedre del av tomt (mot {compass}) — terrengmessig "
                            f"lav grunnet {terrain_info['slope_pct']:.0f}% fall. "
                            f"Lavere typologi gir bedre sol til bygg lengre ned og "
                            f"billigere fundamentering."
                        ),
                    ))
                    terrain_sone_added = True

    # Delt inn etter hvilken retning som har småhus
    if "V" in low_directions or "SV" in low_directions or "NV" in low_directions:
        # Vestre tredjedel = lavere
        poly = _polygon_from_bbox_fraction(bounds, [0.0, 0.0, 0.33, 1.0])
        if poly:
            clipped = poly.intersection(buildable_polygon).buffer(0)
            if not clipped.is_empty and clipped.area > 100:
                zones.append(TypologyZone(
                    zone_id=_next_id(),
                    typology="Rekke",
                    polygon=clipped,
                    floors_min=2, floors_max=min(3, tmax_floors),
                    target_bra=program.total_bra * (0.15 if terrain_sone_added else 0.2),
                    rationale="Nedtrapping mot småhus i vest",
                ))
        # Midtre og østre = høyere
        poly = _polygon_from_bbox_fraction(bounds, [0.33, 0.0, 1.0, 1.0])
        if poly:
            clipped = poly.intersection(buildable_polygon).buffer(0)
            if not clipped.is_empty and clipped.area > 100:
                zones.append(TypologyZone(
                    zone_id=_next_id(),
                    typology="Lamell",
                    polygon=clipped,
                    floors_min=min(4, tmax_floors),
                    floors_max=min(6, tmax_floors),
                    target_bra=program.total_bra * (0.60 if terrain_sone_added else 0.8),
                    rationale="Hovedvolumer mot resten av konteksten",
                ))
    else:
        # Ingen sterk småhus-side → én hovedsone
        zones.append(TypologyZone(
            zone_id=_next_id(),
            typology="Lamell",
            polygon=buildable_polygon,
            floors_min=3, floors_max=min(5, tmax_floors),
            target_bra=program.total_bra,
            rationale="Homogen kontekst, én typologi",
        ))
    return zones


# ─────────────────────────────────────────────────────────────────────
# PASS 3: Volume Placement
# ─────────────────────────────────────────────────────────────────────

def pass3_place_volumes(
    zones: List[TypologyZone],
    program: ProgramAllocation,
    max_floors: int,
    max_height_m: float,
    max_bya_pct: float,
    floor_to_floor_m: float,
    neighbors: Optional[List[Dict[str, Any]]],
    site_polygon,
    buildable_polygon,
    site_inputs: Dict[str, Any],
) -> List[Volume]:
    """Plasser volumer i hver sone.

    Strategi per sone:
      1. Hvis Claude-API er tilgjengelig: bruk legacy ai_site_planner.plan_site()
         som gir AI-drevet plassering med søreksponering, gårdsrom osv.
      2. Hvis API feiler/mangler: fall tilbake til ren geometrisk grid-plassering
         som er deterministisk og TEK-compliant (brannavstand, BYA).

    Volumene tagges med zone_id så Pass 4 vet hvor de kommer fra.
    """
    if not zones or not HAS_SHAPELY:
        return []

    all_volumes: List[Volume] = []
    volume_counter = 0

    total_zone_target = sum(z.target_bra for z in zones) or 1.0
    max_fp_total = buildable_polygon.area * (max_bya_pct / 100.0)
    max_fp_used = 0.0

    for zone in zones:
        if zone.polygon is None or zone.polygon.is_empty:
            continue

        # Mini-BTA for denne sonen basert på andel
        zone_bta_target = (zone.target_bra / total_zone_target) * program.total_bra / 0.85
        zone_bya_pct_local = min(max_bya_pct, 45.0)
        zone_buildable = zone.polygon.buffer(0)
        if zone_buildable.area < 50:
            continue

        zone_volumes: List[Volume] = []
        zone_result = None

        # v1.7: Hvis sonen har et DESIGNET gårdsrom, plasserer vi bygninger
        # som vegger rundt det (LPO Tyholt-metoden). Hopp over AI-plannerens
        # rute helt, fordi den vet ikke om gårdsrommet og kan plassere bygg
        # midt i det.
        if zone.courtyard_polygon is not None and not zone.courtyard_polygon.is_empty:
            ring_volumes, volume_counter = _place_ring_around_courtyard(
                zone=zone,
                zone_buildable=zone_buildable,
                zone_bta_target=zone_bta_target,
                max_floors=max_floors,
                max_height_m=max_height_m,
                max_fp_remaining=max_fp_total - max_fp_used,
                floor_to_floor_m=floor_to_floor_m,
                existing_polys=[v.polygon for v in all_volumes],
                volume_counter=volume_counter,
            )
            zone_volumes.extend(ring_volumes)
            # Hvis ringen ga få volumer, suppler med komposisjonsplassering
            # i restarealet utenfor gårdsrommet (fortsatt innen kvartalsgrensa).
            # Men kun hvis vi er vesentlig under målet.
            current_bta = sum(v.footprint_m2 * v.floors for v in zone_volumes)
            if current_bta < zone_bta_target * 0.7 and zone.typology in TYPOLOGY_COMPOSITIONS:
                logger.info(
                    f"Zone {zone.zone_id}: ring ga bare {current_bta:.0f} BTA "
                    f"av {zone_bta_target:.0f}, supplerer med komposisjon"
                )
                # Lag en redusert zone-geometri som ekskluderer selve gårdsrommet
                zone_minus_courtyard = zone_buildable.difference(
                    zone.courtyard_polygon.buffer(2.0)
                )
                if not zone_minus_courtyard.is_empty:
                    extra_volumes, volume_counter = _composition_placement(
                        zone=zone,
                        zone_buildable=zone_minus_courtyard,
                        zone_bta_target=zone_bta_target - current_bta,
                        max_floors=max_floors,
                        max_height_m=max_height_m,
                        max_fp_remaining=max_fp_total - max_fp_used - sum(v.footprint_m2 for v in zone_volumes),
                        floor_to_floor_m=floor_to_floor_m,
                        existing_polys=[v.polygon for v in all_volumes + zone_volumes],
                        volume_counter=volume_counter,
                    )
                    zone_volumes.extend(extra_volumes)
        else:
            # Strategi 1: AI-drevet plassering hvis tilgjengelig og ingen
            # designet gårdsrom å respektere
            if HAS_LEGACY and legacy_planner and is_available():
                try:
                    zone_result = legacy_planner.plan_site(
                        site_polygon=site_polygon,
                        buildable_polygon=zone_buildable,
                        typology=zone.typology,
                        neighbors=neighbors,
                        terrain=site_inputs.get("terrain"),
                        target_bta_m2=zone_bta_target,
                        max_floors=zone.floors_max,
                        max_height_m=max_height_m,
                        max_bya_pct=zone_bya_pct_local,
                        floor_to_floor_m=floor_to_floor_m,
                    )
                except Exception as exc:
                    logger.warning(f"Pass 3: legacy plan_site failed for zone {zone.zone_id}: {exc}")
                    zone_result = None

                if zone_result and zone_result.get("buildings"):
                    for b in zone_result["buildings"]:
                        volume_counter += 1
                        vol = _volume_from_legacy_building(
                            b, volume_counter, zone, floor_to_floor_m
                        )
                        if vol is None:
                            continue
                        zone_volumes.append(vol)

        # Strategi 2: Deterministisk fallback hvis AI ga lite eller ingenting
        # (vi vil ha noe som fyller sonen, ikke 0 volumer ved API-feil)
        if not zone_volumes or sum(v.footprint_m2 for v in zone_volumes) < zone_bta_target * 0.15 / 0.85:
            if not zone_volumes:
                logger.info(f"Pass 3: Using deterministic fallback for zone {zone.zone_id}")
            # Komponerte typologier får spesialbehandling — segmenter rundt gårdsrom
            if zone.typology in TYPOLOGY_COMPOSITIONS:
                fallback_volumes, volume_counter = _composition_placement(
                    zone=zone,
                    zone_buildable=zone_buildable,
                    zone_bta_target=zone_bta_target,
                    max_floors=max_floors,
                    max_height_m=max_height_m,
                    max_fp_remaining=max_fp_total - max_fp_used - sum(v.footprint_m2 for v in zone_volumes),
                    floor_to_floor_m=floor_to_floor_m,
                    existing_polys=[v.polygon for v in all_volumes + zone_volumes],
                    volume_counter=volume_counter,
                )
            else:
                fallback_volumes, volume_counter = _fallback_grid_placement(
                    zone=zone,
                    zone_buildable=zone_buildable,
                    zone_bta_target=zone_bta_target,
                    max_floors=max_floors,
                    max_height_m=max_height_m,
                    max_fp_remaining=max_fp_total - max_fp_used - sum(v.footprint_m2 for v in zone_volumes),
                    floor_to_floor_m=floor_to_floor_m,
                    existing_polys=[v.polygon for v in all_volumes + zone_volumes],
                    volume_counter=volume_counter,
                )
            zone_volumes.extend(fallback_volumes)

        # Legg zone_volumes til all_volumes
        for vol in zone_volumes:
            all_volumes.append(vol)
            max_fp_used += vol.footprint_m2
            if max_fp_used > max_fp_total * 1.05:
                logger.info(f"Pass 3: Total BYA reached, stopping")
                break

        if max_fp_used > max_fp_total * 1.05:
            break

    # Allokér programmer (barnehage → sørligste rolige hjørne, næring → 1. etg mot veg)
    if program.barnehage_bra > 0 and all_volumes:
        # Barnehage plasseres i 1. etasje av ett eller flere sørlige volumer.
        # Krav: 6-base trenger ~1279 m² innendørs BRA (ca 1500 m² fotavtrykk).
        # Strategi: finn de 1-2 sørligste volumene som samlet gir nok 1.-etg-areal.
        bh_fp_needed = BARNEHAGE_6BASE_INDOOR_BRA / 0.85
        by_south = sorted(all_volumes, key=lambda v: v.cy)
        accumulated_fp = 0.0
        bh_allocated = []
        for vol in by_south:
            if vol.program != "bolig":
                continue
            bh_allocated.append(vol)
            accumulated_fp += vol.footprint_m2
            if accumulated_fp >= bh_fp_needed * 0.7:
                break

        if bh_allocated:
            for vol in bh_allocated:
                vol.ground_floor_program = "barnehage"
                if accumulated_fp >= bh_fp_needed and vol is bh_allocated[0]:
                    vol.program = "barnehage"
                    vol.notes += " (Barnehage 6-base i 1. etg)"
                else:
                    vol.notes += " (Del av barnehage i 1. etg)"

    if program.naering_bra > 0 and all_volumes:
        # Næring i 1. etg av volum nærmest hovedveg (antatt høyeste Y eller X)
        candidates = [v for v in all_volumes
                      if v.program == "bolig" and v.ground_floor_program is None]
        if candidates:
            candidates.sort(key=lambda v: -v.cy)  # høyest Y
            candidates[0].ground_floor_program = "naering"
            candidates[0].notes += " (Næring/service i 1. etg)"

    logger.info(f"Pass 3: Placed {len(all_volumes)} volumes total across {len(zones)} zones")
    return all_volumes


def _volume_from_legacy_building(b: Dict[str, Any], volume_counter: int,
                                  zone: TypologyZone, floor_to_floor_m: float) -> Optional[Volume]:
    """Konverter et bygg fra legacy plan_site() til Volume-objekt."""
    if not b.get("polygon"):
        return None
    dims = TYPOLOGY_DIMS.get(zone.typology, TYPOLOGY_DIMS["Lamell"])
    vol = Volume(
        volume_id=f"V{volume_counter:02d}",
        name=b.get("name", f"Vol {volume_counter}"),
        polygon=b["polygon"],
        typology=zone.typology,
        floors=b.get("floors", zone.floors_min),
        height_m=b.get("height_m", 0.0),
        width_m=b.get("width_m", 0.0),
        depth_m=b.get("depth_m", 0.0),
        angle_deg=b.get("angle_deg", 0.0),
        cx=b.get("cx", 0.0),
        cy=b.get("cy", 0.0),
        footprint_m2=b.get("area_m2", 0.0),
        has_courtyard=b.get("courtyard", False),
        ring_depth_m=b.get("ring_depth", 0.0),
        zone_id=zone.zone_id,
        program="bolig",
        notes=b.get("notes", ""),
    )
    vol.units_estimate = int(round(dims["units_per_floor"] * vol.floors))
    vol.oppganger = max(1, int(math.ceil(vol.width_m / 25.0)))
    return vol


def _fallback_grid_placement(
    zone: TypologyZone,
    zone_buildable,
    zone_bta_target: float,
    max_floors: int,
    max_height_m: float,
    max_fp_remaining: float,
    floor_to_floor_m: float,
    existing_polys: List[Any],
    volume_counter: int,
) -> Tuple[List[Volume], int]:
    """Deterministisk grid-plassering i en typologisone. Kalles når AI ikke er
    tilgjengelig eller feiler.

    Strategi:
      - Beregn typisk volumdimensjon for typologien
      - Bestem etasjeantall fra BTA-mål og fotavtrykk-budsjett
      - Legg et grid av volumer med 8m brannavstand
      - Begrens til zone_buildable og max_fp_remaining
    """
    volumes: List[Volume] = []
    if zone_buildable is None or zone_buildable.is_empty or max_fp_remaining <= 0:
        return volumes, volume_counter

    dims = TYPOLOGY_DIMS.get(zone.typology, TYPOLOGY_DIMS["Lamell"])
    # Velg midt-i-intervallet dimensjoner
    w = (dims["w_min"] + dims["w_max"]) / 2.0
    d = (dims["d_min"] + dims["d_max"]) / 2.0
    target_floors = _clamp(
        int(math.ceil(zone.floors_min + (zone.floors_max - zone.floors_min) * 0.5)),
        dims["f_min"],
        min(max_floors, dims["f_max"]),
    )
    ftf = dims.get("ftf", floor_to_floor_m)
    height_m = target_floors * ftf

    if height_m > max_height_m:
        target_floors = max(1, int(max_height_m / ftf))
        height_m = target_floors * ftf

    # Finn tomtas bounds og orientering (longest-axis = byggets bredderetning typisk)
    bounds = zone_buildable.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]

    # For Lamell: orienter slik at w går langs lengste akse (gir ø-v fasade = nord/sør)
    # For Punkthus/Tårn: orientering betyr mindre (kvadratiske)
    # For Rekke: alltid parallell med lengste akse
    if zone.typology in ("Punkthus", "Tårn"):
        angle_deg = 0.0
    else:
        # Lameller orienteres med lengderetning vest-øst (søreksponert)
        # dvs w er langs x-aksen → angle = 0
        # Hvis tomta er høyere enn bred → roter 90°
        angle_deg = 0.0 if bw >= bh else 90.0

    # Effektive dimensjoner etter rotasjon
    rad = math.radians(angle_deg)
    eff_w = abs(w * math.cos(rad)) + abs(d * math.sin(rad))
    eff_d = abs(w * math.sin(rad)) + abs(d * math.cos(rad))

    # Bestem grid
    spacing = MIN_BUILDING_SPACING
    cell_w = eff_w + spacing
    cell_d = eff_d + spacing

    # Antall kolonner og rader som får plass (med setback)
    inner_bw = bw - 2 * MIN_BOUNDARY_SETBACK
    inner_bh = bh - 2 * MIN_BOUNDARY_SETBACK
    if inner_bw <= 0 or inner_bh <= 0:
        return volumes, volume_counter

    cols = max(1, int((inner_bw + spacing) / cell_w))
    rows = max(1, int((inner_bh + spacing) / cell_d))

    # Mål: fyll ~zone_bta_target. Beregn hvor mange bygg vi trenger.
    fp_per_bldg = w * d
    bta_per_bldg = fp_per_bldg * target_floors
    needed_count = max(1, int(math.ceil(zone_bta_target / max(bta_per_bldg, 1.0))))

    # Ikke overskrid fotavtrykk-budsjett
    max_count_by_fp = int(max_fp_remaining / max(fp_per_bldg, 1.0))
    total_count = min(needed_count, cols * rows, max_count_by_fp)

    if total_count <= 0:
        return volumes, volume_counter

    # Beregn start-posisjon (centroid-aligned grid)
    total_grid_w = cols * eff_w + (cols - 1) * spacing
    total_grid_h = rows * eff_d + (rows - 1) * spacing
    start_x = bounds[0] + (bw - total_grid_w) / 2.0 + eff_w / 2.0
    start_y = bounds[1] + (bh - total_grid_h) / 2.0 + eff_d / 2.0

    placed = 0
    for row in range(rows):
        for col in range(cols):
            if placed >= total_count:
                break
            cx = start_x + col * (eff_w + spacing)
            cy = start_y + row * (eff_d + spacing)

            poly = _make_building_polygon(cx, cy, w, d, angle_deg)
            if poly is None:
                continue

            # Klipp mot zone_buildable
            clipped = poly.intersection(zone_buildable).buffer(0)
            # Krev at volumet fortsatt er minst 50% av opprinnelig fotavtrykk —
            # ellers får vi "mikrovolumer" på kantene som utgir seg for byggetrinn
            min_acceptable_area = max(80.0, fp_per_bldg * 0.5)
            if clipped.is_empty or clipped.area < min_acceptable_area:
                continue
            if isinstance(clipped, MultiPolygon):
                clipped = max(clipped.geoms, key=lambda g: g.area)
                # Re-sjekk etter valg av største delpolygon
                if clipped.area < min_acceptable_area:
                    continue

            # Sjekk brannavstand mot eksisterende volumer
            too_close = False
            for ep in existing_polys + [v.polygon for v in volumes]:
                if ep is not None and clipped.distance(ep) < spacing - 0.5:
                    too_close = True
                    break
            if too_close:
                continue

            # Håndter karré: hull ut senter
            is_karre = zone.typology == "Karré"
            ring_depth_m = 0.0
            if is_karre and clipped.area > 600:
                ring_depth_m = max(8.0, min(12.0, math.sqrt(clipped.area) * 0.22))
                inner = clipped.buffer(-ring_depth_m)
                if inner and not inner.is_empty and inner.area > 40:
                    clipped = clipped.difference(inner).buffer(0)

            volume_counter += 1
            vol = Volume(
                volume_id=f"V{volume_counter:02d}",
                name=f"{zone.typology} {volume_counter}",
                polygon=clipped,
                typology=zone.typology,
                floors=target_floors,
                height_m=round(height_m, 1),
                width_m=round(w, 1),
                depth_m=round(d, 1),
                angle_deg=round(angle_deg, 1),
                cx=round(cx, 1),
                cy=round(cy, 1),
                footprint_m2=round(float(clipped.area), 1),
                has_courtyard=is_karre,
                ring_depth_m=ring_depth_m,
                zone_id=zone.zone_id,
                program="bolig",
                notes=f"Deterministisk plassert i sone {zone.zone_id}",
            )
            vol.units_estimate = int(round(dims["units_per_floor"] * vol.floors))
            vol.oppganger = max(1, int(math.ceil(vol.width_m / 25.0)))
            volumes.append(vol)
            placed += 1

        if placed >= total_count:
            break

    return volumes, volume_counter


def _make_building_polygon(cx: float, cy: float, width: float, depth: float,
                           angle_deg: float) -> Optional[Any]:
    """Lag et rotert rektangel som polygon."""
    if not HAS_SHAPELY:
        return None
    try:
        b = shapely_box(-width/2, -depth/2, width/2, depth/2)
        b = affinity.rotate(b, angle_deg, origin=(0, 0))
        b = affinity.translate(b, cx, cy)
        return b if b.is_valid and not b.is_empty else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# KOMPONERTE TYPOLOGIER — segmenter rundt gårdsrom
# ─────────────────────────────────────────────────────────────────────
# Disse funksjonene plasserer flere segmenter i bestemte geometriske
# komposisjoner (linear rekke, halvåpen karré, klynge) i stedet for
# ett enkelt rektangel. Dette gjør det mulig å modellere urbane
# gårdsrom-strukturer som LPO Tyholt Park (flere korte lameller rundt
# et felles uterom).

def _composition_placement(
    zone: TypologyZone,
    zone_buildable,
    zone_bta_target: float,
    max_floors: int,
    max_height_m: float,
    max_fp_remaining: float,
    floor_to_floor_m: float,
    existing_polys: List[Any],
    volume_counter: int,
) -> Tuple[List[Volume], int]:
    """Orchestrator for komponerte typologier. Dispatcher til layout-spesifikke
    funksjoner basert på TYPOLOGY_COMPOSITIONS.
    """
    if zone_buildable is None or zone_buildable.is_empty or max_fp_remaining <= 0:
        return [], volume_counter

    comp = TYPOLOGY_COMPOSITIONS.get(zone.typology)
    if not comp:
        logger.warning(f"Ingen komposisjon definert for {zone.typology}, fallback til grid")
        return _fallback_grid_placement(
            zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
            max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter,
        )

    layout = comp.get("layout", "linear")
    if layout == "linear":
        return _compose_linear(
            zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
            max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter, comp,
        )
    elif layout in ("o_shape", "u_shape"):
        return _compose_courtyard(
            zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
            max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter, comp,
        )
    elif layout == "cluster":
        return _compose_cluster(
            zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
            max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter, comp,
        )
    else:
        logger.warning(f"Ukjent layout {layout}, fallback til grid")
        return _fallback_grid_placement(
            zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
            max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter,
        )


def _compose_linear(
    zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
    max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter, comp,
) -> Tuple[List[Volume], int]:
    """LamellSegmentert — flere kortere segmenter i rad langs sonens lengste akse.

    Eksempel LPO Tyholt nordblokk: 4 segmenter à 18m × 14m, 5 etasjer,
    med 8m gap mellom hvert for siktakser.
    """
    volumes: List[Volume] = []
    dims = TYPOLOGY_DIMS.get(zone.typology, TYPOLOGY_DIMS["Lamell"])
    d = (dims["d_min"] + dims["d_max"]) / 2.0   # segmentdybde
    gap = comp.get("segment_gap_m", 8.0)

    # Velg etasjer fra sonens rekkevidde, cap til max_height
    target_floors = _clamp(
        int(math.ceil((zone.floors_min + zone.floors_max) / 2.0)),
        dims["f_min"], min(max_floors, dims["f_max"]),
    )
    ftf = dims.get("ftf", floor_to_floor_m)
    height_m = target_floors * ftf
    if height_m > max_height_m:
        target_floors = max(1, int(max_height_m / ftf))
        height_m = target_floors * ftf

    # Finn bounds og velg orientering langs lengste akse
    bounds = zone_buildable.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]
    if bw >= bh:
        axis_len = bw - 2 * MIN_BOUNDARY_SETBACK
        across_len = bh - 2 * MIN_BOUNDARY_SETBACK
        segment_axis = 0
        angle_deg = 0.0
    else:
        axis_len = bh - 2 * MIN_BOUNDARY_SETBACK
        across_len = bw - 2 * MIN_BOUNDARY_SETBACK
        segment_axis = 1
        angle_deg = 90.0

    # Støtt dobbel rad (parallele lameller) hvis tomten er bred nok
    # Dobbel rad trenger: 2×d + gap across_len
    n_rows = 1
    if across_len >= 2 * d + gap + 4:
        n_rows = 2

    min_segments = comp.get("min_segments", 3)
    max_segments = comp.get("max_segments", 6)

    # Dynamisk valg av antall segmenter og bredde:
    # 1. Start med maksimalt antall som får plass med w=w_min
    # 2. Beregn ideell bredde slik at totalsum BTA treffer målet
    # 3. Cap bredden til w_max
    n_by_space_max = max(1, int((axis_len + gap) / (dims["w_min"] + gap)))
    n_segments_per_row = _clamp(n_by_space_max, min_segments, max_segments)

    # Total segmenter = rader × per-rad
    total_n = n_rows * n_segments_per_row

    # Hvor bredt må hvert segment være for å treffe BTA-målet?
    # total_n × w × d × floors = zone_bta_target  =>  w = target / (total_n × d × floors)
    w_ideal = zone_bta_target / max(total_n * d * target_floors, 1.0)
    w = _clamp(w_ideal, dims["w_min"], dims["w_max"])

    # Sjekk at segmenter per rad med denne bredden faktisk får plass
    while n_segments_per_row > min_segments and n_segments_per_row * w + (n_segments_per_row - 1) * gap > axis_len:
        n_segments_per_row -= 1
        total_n = n_rows * n_segments_per_row
        w_ideal = zone_bta_target / max(total_n * d * target_floors, 1.0)
        w = _clamp(w_ideal, dims["w_min"], dims["w_max"])

    while n_segments_per_row > 1 and n_segments_per_row * w + (n_segments_per_row - 1) * gap > axis_len:
        n_segments_per_row -= 1
        total_n = n_rows * n_segments_per_row
        w_ideal = zone_bta_target / max(total_n * d * target_floors, 1.0)
        w = _clamp(w_ideal, dims["w_min"], dims["w_max"])

    # Fotavtrykk-budsjett
    fp_per_seg = w * d
    total_n = n_rows * n_segments_per_row
    max_total_by_fp = int(max_fp_remaining / max(fp_per_seg, 1))
    if total_n > max_total_by_fp:
        total_n = max_total_by_fp
        # Krymp rader først hvis vi må
        if total_n < n_segments_per_row * 2 and n_rows == 2:
            n_rows = 1
            n_segments_per_row = min(n_segments_per_row, total_n)
        else:
            n_segments_per_row = total_n // n_rows

    if total_n < 1 or n_segments_per_row < 1:
        return volumes, volume_counter

    # Sentrer segmentradene
    total_axis_len = n_segments_per_row * w + (n_segments_per_row - 1) * gap
    start_along = bounds[segment_axis] + (
        (bw if segment_axis == 0 else bh) - total_axis_len
    ) / 2.0 + w / 2.0

    # For 2 rader: row_offset fra senter
    if n_rows == 2:
        across_start = bounds[1 - segment_axis] + (
            (bh if segment_axis == 0 else bw) - (2 * d + gap)
        ) / 2.0 + d / 2.0
    else:
        across_start = bounds[1 - segment_axis] + (
            (bh if segment_axis == 0 else bw)
        ) / 2.0

    for row_idx in range(n_rows):
        if n_rows == 2:
            across = across_start + row_idx * (d + gap)
        else:
            across = across_start

        for i in range(n_segments_per_row):
            along = start_along + i * (w + gap)
            if segment_axis == 0:
                cx, cy = along, across
            else:
                cx, cy = across, along

            poly = _make_building_polygon(cx, cy, w, d, angle_deg)
            if poly is None:
                continue
            clipped = poly.intersection(zone_buildable).buffer(0)
            min_area = max(80.0, fp_per_seg * 0.5)
            if clipped.is_empty or clipped.area < min_area:
                continue
            if isinstance(clipped, MultiPolygon):
                clipped = max(clipped.geoms, key=lambda g: g.area)
                if clipped.area < min_area:
                    continue

            # Brannavstandsjekk — KUN mot eksisterende polygoner utenfor komposisjonen
            too_close = False
            for ep in existing_polys:
                if ep is not None and clipped.distance(ep) < MIN_BUILDING_SPACING - 0.5:
                    too_close = True
                    break
            if too_close:
                continue

            volume_counter += 1
            seg_name = f"S{i+1}" if n_rows == 1 else f"R{row_idx+1}S{i+1}"
            vol = Volume(
                volume_id=f"V{volume_counter:02d}",
                name=f"{zone.typology} {seg_name}",
                polygon=clipped,
                typology=zone.typology,
                floors=target_floors,
                height_m=round(height_m, 1),
                width_m=round(w, 1),
                depth_m=round(d, 1),
                angle_deg=round(angle_deg, 1),
                cx=round(cx, 1), cy=round(cy, 1),
                footprint_m2=round(float(clipped.area), 1),
                zone_id=zone.zone_id,
                program="bolig",
            )
            vol.units_estimate = int(round(dims["units_per_floor"] * vol.floors))
            vol.oppganger = max(1, int(math.ceil(vol.width_m / 25.0)))
            volumes.append(vol)

    logger.info(
        f"Komposisjon LINEAR for {zone.zone_id}: {len(volumes)} segmenter "
        f"({n_rows} rader × {n_segments_per_row}), à {w:.0f}×{d:.0f}×{target_floors}et, "
        f"total BRA {sum(v.bra_m2 for v in volumes):.0f} (mål {zone_bta_target:.0f})"
    )
    return volumes, volume_counter


def _compose_courtyard(
    zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
    max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter, comp,
) -> Tuple[List[Volume], int]:
    """HalvåpenKarré — 3-4 L-formede eller I-formede segmenter rundt gårdsrom.

    Plasserer segmenter som nord-/sør-/øst-/vest-flanker med gap i hjørnene.
    Gårdsrommet blir et indre rektangel minst 18m bredt.
    """
    volumes: List[Volume] = []
    dims = TYPOLOGY_DIMS.get(zone.typology, TYPOLOGY_DIMS["Lamell"])
    d = (dims["d_min"] + dims["d_max"]) / 2.0   # segmentdybde (tilsvarende "tykkelsen")
    gap = comp.get("segment_gap_m", 6.0)
    courtyard_min = comp.get("courtyard_min_dim_m", 18.0)

    target_floors = _clamp(
        int(math.ceil((zone.floors_min + zone.floors_max) / 2.0)),
        dims["f_min"], min(max_floors, dims["f_max"]),
    )
    ftf = dims.get("ftf", floor_to_floor_m)
    height_m = target_floors * ftf
    if height_m > max_height_m:
        target_floors = max(1, int(max_height_m / ftf))
        height_m = target_floors * ftf

    # Bestem kvartals-dimensjoner i sonen — sentrert, så stor som mulig innenfor setback
    bounds = zone_buildable.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]
    kw = bw - 2 * MIN_BOUNDARY_SETBACK
    kh = bh - 2 * MIN_BOUNDARY_SETBACK

    # Gårdsrommet får courtyard_target_ratio av kvartalet, men minst courtyard_min
    court_w = max(courtyard_min, kw - 2 * d - 2 * gap)
    court_h = max(courtyard_min, kh - 2 * d - 2 * gap)
    if court_w < courtyard_min or court_h < courtyard_min:
        logger.info(f"Komposisjon COURTYARD: sone {zone.zone_id} for liten, fallback til linear")
        return _compose_linear(
            zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
            max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter, comp,
        )

    # Beregn segment-geometri
    cx_k = bounds[0] + bw / 2.0
    cy_k = bounds[1] + bh / 2.0

    # Fire segmenter: nord, sør, øst, vest — hver er en L som dekker én side
    # Vi forenkler til rektangler for nå; gap i hjørner = ikke møtte
    outer_w = court_w + 2 * d
    outer_h = court_h + 2 * d

    # Nord-segment: dekker øvre kant, litt kortere enn kvartal for gap i hjørner
    seg_long_w = outer_w - 2 * gap   # trukket inn i hjørnene
    seg_short_h = outer_h - 2 * gap

    # Fire segmenter, fire sentrum
    segment_specs = [
        # (name, cx, cy, width, depth, angle)
        ("N", cx_k, cy_k + (court_h / 2.0 + d / 2.0), seg_long_w, d, 0.0),
        ("S", cx_k, cy_k - (court_h / 2.0 + d / 2.0), seg_long_w, d, 0.0),
        ("Ø", cx_k + (court_w / 2.0 + d / 2.0), cy_k, seg_short_h, d, 90.0),
        ("V", cx_k - (court_w / 2.0 + d / 2.0), cy_k, seg_short_h, d, 90.0),
    ]

    fp_per_seg = seg_long_w * d  # approx
    max_segments_by_fp = int(max_fp_remaining / max(fp_per_seg, 1))

    placed = 0
    for seg_name, sx, sy, sw, sd, sang in segment_specs:
        if placed >= max_segments_by_fp:
            break
        poly = _make_building_polygon(sx, sy, sw, sd, sang)
        if poly is None:
            continue
        clipped = poly.intersection(zone_buildable).buffer(0)
        min_area = max(100.0, sw * sd * 0.5)
        if clipped.is_empty or clipped.area < min_area:
            continue
        if isinstance(clipped, MultiPolygon):
            clipped = max(clipped.geoms, key=lambda g: g.area)
            if clipped.area < min_area:
                continue

        # Brannsjekk kun mot eksisterende polygoner utenfor komposisjonen.
        # Komposisjons-segmenter er designet med gap mellom seg og bør ikke
        # ekskludere hverandre.
        too_close = False
        for ep in existing_polys:
            if ep is not None and clipped.distance(ep) < gap - 0.5:
                too_close = True
                break
        if too_close:
            continue

        volume_counter += 1
        vol = Volume(
            volume_id=f"V{volume_counter:02d}",
            name=f"{zone.typology} {seg_name}",
            polygon=clipped,
            typology=zone.typology,
            floors=target_floors,
            height_m=round(height_m, 1),
            width_m=round(sw, 1),
            depth_m=round(sd, 1),
            angle_deg=round(sang, 1),
            cx=round(sx, 1), cy=round(sy, 1),
            footprint_m2=round(float(clipped.area), 1),
            zone_id=zone.zone_id,
            program="bolig",
        )
        # bra_m2 er en property på Volume — beregnes automatisk fra footprint × floors × 0.85
        vol.units_estimate = int(round(dims["units_per_floor"] * vol.floors))
        vol.oppganger = max(1, int(math.ceil(sw / 25.0)))
        volumes.append(vol)
        placed += 1

    logger.info(
        f"Komposisjon COURTYARD for {zone.zone_id}: {len(volumes)} segmenter "
        f"(gårdsrom {court_w:.0f}×{court_h:.0f}m), total BRA {sum(v.bra_m2 for v in volumes):.0f}"
    )
    return volumes, volume_counter


def _compose_cluster(
    zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
    max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter, comp,
) -> Tuple[List[Volume], int]:
    """Gårdsklynge — 4-6 kortere segmenter i fri klynge rundt et felles uterom.

    Bruker samme geometri som halvåpen karré men med flere, kortere segmenter
    — LPO Tyholt sørblokk-stil.
    """
    volumes: List[Volume] = []
    dims = TYPOLOGY_DIMS.get(zone.typology, TYPOLOGY_DIMS["Lamell"])
    w = (dims["w_min"] + dims["w_max"]) / 2.0
    d = (dims["d_min"] + dims["d_max"]) / 2.0
    gap = comp.get("segment_gap_m", 8.0)
    courtyard_min = comp.get("courtyard_min_dim_m", 15.0)

    target_floors = _clamp(
        int(math.ceil((zone.floors_min + zone.floors_max) / 2.0)),
        dims["f_min"], min(max_floors, dims["f_max"]),
    )
    ftf = dims.get("ftf", floor_to_floor_m)
    height_m = target_floors * ftf
    if height_m > max_height_m:
        target_floors = max(1, int(max_height_m / ftf))
        height_m = target_floors * ftf

    bounds = zone_buildable.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]
    inner_w = bw - 2 * MIN_BOUNDARY_SETBACK
    inner_h = bh - 2 * MIN_BOUNDARY_SETBACK

    # Klyngens strategi: 2×2 eller 2×3 grid med gap, felt i midten for gårdsrom
    fp_per_seg = w * d
    bta_per_seg = fp_per_seg * target_floors
    max_segments = comp.get("max_segments", 6)
    needed_segments = max(4, int(math.ceil(zone_bta_target / max(bta_per_seg, 1))))
    n_segments = min(needed_segments, max_segments,
                      int(max_fp_remaining / max(fp_per_seg, 1)))
    n_segments = max(comp.get("min_segments", 4), n_segments) if n_segments > 0 else 0
    if n_segments < 1:
        return volumes, volume_counter

    # Velg rader/kolonner
    cols = 2 if n_segments <= 4 else 3
    rows = 2 if n_segments <= 4 else int(math.ceil(n_segments / cols))

    # Skaler segmentbredde/-dybde ned hvis klyngen ikke passer i tomten
    cluster_w = cols * w + (cols - 1) * gap
    cluster_h = rows * d + (rows - 1) * gap
    if cluster_w > inner_w or cluster_h > inner_h:
        # Prøv 90°-rotering først
        if cluster_h <= inner_w and cluster_w <= inner_h:
            angle_deg = 90.0
            cluster_w, cluster_h = cluster_h, cluster_w
        else:
            # Må skalere ned w og d
            scale_w = (inner_w - (cols - 1) * gap) / (cols * w) if cols * w > 0 else 1
            scale_h = (inner_h - (rows - 1) * gap) / (rows * d) if rows * d > 0 else 1
            scale = min(scale_w, scale_h, 1.0)
            scale = max(scale, 0.6)  # ikke mindre enn 60% av opprinnelig
            w = w * scale
            d = d * scale
            # Respekt min-grenser
            w = max(w, dims["w_min"])
            d = max(d, dims["d_min"])
            cluster_w = cols * w + (cols - 1) * gap
            cluster_h = rows * d + (rows - 1) * gap
            angle_deg = 0.0
            # Hvis selv med skalering ikke passer, reduser antall kolonner/rader
            while (cluster_w > inner_w or cluster_h > inner_h) and (cols > 1 or rows > 1):
                if cluster_w > inner_w and cols > 1:
                    cols -= 1
                elif cluster_h > inner_h and rows > 1:
                    rows -= 1
                else:
                    break
                cluster_w = cols * w + (cols - 1) * gap
                cluster_h = rows * d + (rows - 1) * gap
            n_segments = min(n_segments, cols * rows)
    else:
        angle_deg = 0.0

    fp_per_seg = w * d  # oppdatert hvis skalert

    # Sentrer klyngen
    cx_c = bounds[0] + bw / 2.0
    cy_c = bounds[1] + bh / 2.0
    start_x = cx_c - cluster_w / 2.0 + w / 2.0
    start_y = cy_c - cluster_h / 2.0 + d / 2.0

    placed = 0
    for r in range(rows):
        for c in range(cols):
            if placed >= n_segments:
                break
            sx = start_x + c * (w + gap)
            sy = start_y + r * (d + gap)

            poly = _make_building_polygon(sx, sy, w, d, angle_deg)
            if poly is None:
                continue
            clipped = poly.intersection(zone_buildable).buffer(0)
            min_area = max(80.0, fp_per_seg * 0.5)
            if clipped.is_empty or clipped.area < min_area:
                continue
            if isinstance(clipped, MultiPolygon):
                clipped = max(clipped.geoms, key=lambda g: g.area)
                if clipped.area < min_area:
                    continue

            # Brannsjekk kun mot eksisterende polygoner utenfor komposisjonen.
            too_close = False
            for ep in existing_polys:
                if ep is not None and clipped.distance(ep) < gap - 0.5:
                    too_close = True
                    break
            if too_close:
                continue

            volume_counter += 1
            vol = Volume(
                volume_id=f"V{volume_counter:02d}",
                name=f"{zone.typology} C{placed+1}",
                polygon=clipped,
                typology=zone.typology,
                floors=target_floors,
                height_m=round(height_m, 1),
                width_m=round(w, 1),
                depth_m=round(d, 1),
                angle_deg=round(angle_deg, 1),
                cx=round(sx, 1), cy=round(sy, 1),
                footprint_m2=round(float(clipped.area), 1),
                zone_id=zone.zone_id,
                program="bolig",
            )
            # bra_m2 er en property på Volume — beregnes automatisk
            vol.units_estimate = int(round(dims["units_per_floor"] * vol.floors))
            vol.oppganger = max(1, int(math.ceil(w / 25.0)))
            volumes.append(vol)
            placed += 1

    logger.info(
        f"Komposisjon CLUSTER for {zone.zone_id}: {len(volumes)} segmenter "
        f"({cols}×{rows}), total BRA {sum(v.bra_m2 for v in volumes):.0f}"
    )
    return volumes, volume_counter


# ─────────────────────────────────────────────────────────────────────
# v1.7: RINGPLASSERING RUNDT DESIGNET GÅRDSROM (LPO Tyholt-metoden)
# ─────────────────────────────────────────────────────────────────────

def _place_ring_around_courtyard(
    zone,
    zone_buildable,
    zone_bta_target: float,
    max_floors: int,
    max_height_m: float,
    max_fp_remaining: float,
    floor_to_floor_m: float,
    existing_polys: List[Any],
    volume_counter: int,
) -> Tuple[List[Volume], int]:
    """Plasser bygninger som EN RING rundt et designet gårdsromspolygon.

    Dette er grepet LPO gjorde på Tyholt Park: de tegnet gårdsrommet først
    (som "blomsterhage", "felles grill", "barnehage-uteareal"), og bygde
    så bygningene som vegger rundt det. Resultatet er et tydelig kvartal
    der uterommet IKKE er restareal, men designdriver.

    Strategi:
      1. Finn de fire hovedkantene av gårdsrommet (N, S, Ø, V)
      2. Plasser ett eller flere bygningssegmenter langs hver kant,
         med korrekt bygningsdybde (12-16m) utover fra gårdsrommet
      3. La hjørner ha gap (6-8m) slik at gårdsrommet får lys og innsyn
      4. Skaler segmentlengder slik at total BRA treffer zone_bta_target
    """
    if zone.courtyard_polygon is None or zone.courtyard_polygon.is_empty:
        return [], volume_counter
    if zone_buildable is None or zone_buildable.is_empty or max_fp_remaining <= 0:
        return [], volume_counter

    # Velg bygningsdybde og etasjeantall fra typologiens dimensjonsramme
    typology = zone.typology if zone.typology in TYPOLOGY_DIMS else "Lamell"
    dims = TYPOLOGY_DIMS[typology]
    # Komponerte typologier har smalere dybde (12-14m); Lamell/Karré bredere
    if typology in TYPOLOGY_COMPOSITIONS:
        d = (dims["d_min"] + dims["d_max"]) / 2.0
    else:
        d = min(14.0, (dims["d_min"] + dims["d_max"]) / 2.0)

    target_floors = _clamp(
        int(math.ceil((zone.floors_min + zone.floors_max) / 2.0)),
        dims["f_min"], min(max_floors, dims["f_max"]),
    )
    ftf = dims.get("ftf", floor_to_floor_m)
    height_m = target_floors * ftf
    if height_m > max_height_m:
        target_floors = max(1, int(max_height_m / ftf))
        height_m = target_floors * ftf

    # Gårdsrommets bounds
    cb = zone.courtyard_polygon.bounds
    ccx = (cb[0] + cb[2]) / 2.0
    ccy = (cb[1] + cb[3]) / 2.0
    court_w = cb[2] - cb[0]
    court_h = cb[3] - cb[1]

    # Hjørne-gap for lys og innsyn i gårdsrommet
    corner_gap = 6.0 if typology in TYPOLOGY_COMPOSITIONS else 4.0

    # Fire bygningskanter, hver sentrert på sin kant av gårdsrommet
    # Segment-senter ligger (court_half + d/2) utover fra gårdsromssenter
    # Lengden er redusert med 2×corner_gap så hjørnene får åpninger.
    wall_specs = [
        # (name, cx, cy, width_along_wall, depth, angle_deg)
        # NORD-vegg: parallell med x-aksen, over gårdsrommet
        ("N", ccx, cb[3] + d / 2.0, court_w - 2 * corner_gap, d, 0.0),
        # SYD-vegg
        ("S", ccx, cb[1] - d / 2.0, court_w - 2 * corner_gap, d, 0.0),
        # ØST-vegg: rotert 90°, høyre for gårdsrommet
        ("Ø", cb[2] + d / 2.0, ccy, court_h - 2 * corner_gap, d, 90.0),
        # VEST-vegg
        ("V", cb[0] - d / 2.0, ccy, court_h - 2 * corner_gap, d, 90.0),
    ]

    # Først lagrer vi foreslåtte vegger, så beregner vi skalering
    proposed = []
    for name, wx, wy, w_len, w_d, angle in wall_specs:
        if w_len < 15:  # for kort vegg — drop
            continue
        proposed.append((name, wx, wy, w_len, w_d, angle))

    if not proposed:
        return [], volume_counter

    # Hvis BTA-målet krever flere etasjer enn vi allerede valgte, øk etasjer.
    # Totalt fotavtrykk = sum(w_len × d)
    total_fp = sum(w_len * w_d for _, _, _, w_len, w_d, _ in proposed)
    if total_fp < 50:
        return [], volume_counter

    # Målt BTA = fp × floors. Hvis underslått, kan vi ikke gjøre mer her —
    # Pass 2 burde lagt gårdsrommet større.
    # Logging for diagnostikk:
    expected_bta = total_fp * target_floors
    logger.info(
        f"Ring for {zone.zone_id}: gårdsrom {court_w:.0f}×{court_h:.0f}, "
        f"4 vegger à d={w_d:.0f}m, target_floors={target_floors}, "
        f"expected_bta={expected_bta:.0f} (mål {zone_bta_target:.0f})"
    )

    # Fotavtrykk-budsjett
    max_walls_by_fp = max_fp_remaining / max(total_fp / max(len(proposed), 1), 1)

    volumes: List[Volume] = []
    placed = 0
    for name, wx, wy, w_len, w_d, angle in proposed:
        if placed >= max_walls_by_fp:
            break
        poly = _make_building_polygon(wx, wy, w_len, w_d, angle)
        if poly is None:
            continue
        clipped = poly.intersection(zone_buildable).buffer(0)
        if clipped.is_empty:
            continue
        if isinstance(clipped, MultiPolygon):
            clipped = max(clipped.geoms, key=lambda g: g.area)

        # Krev at minst 60% av veggens fotavtrykk ligger innenfor zone_buildable
        original_fp = w_len * w_d
        if clipped.area < original_fp * 0.5:
            # Vi kan prøve å krympe veggen — reduser w_len iterativt
            shrink_factor = clipped.area / original_fp
            if shrink_factor > 0.3:
                new_len = w_len * shrink_factor * 1.1  # litt kompensert
                if new_len >= 15:
                    poly2 = _make_building_polygon(wx, wy, new_len, w_d, angle)
                    clipped2 = poly2.intersection(zone_buildable).buffer(0) if poly2 else None
                    if clipped2 and not clipped2.is_empty and clipped2.area >= original_fp * 0.4:
                        if isinstance(clipped2, MultiPolygon):
                            clipped2 = max(clipped2.geoms, key=lambda g: g.area)
                        clipped = clipped2
                        w_len = new_len
                    else:
                        continue
                else:
                    continue
            else:
                continue

        # Brannsjekk kun mot eksisterende polygoner utenfor dette kvartalet
        too_close = False
        for ep in existing_polys:
            if ep is not None and clipped.distance(ep) < MIN_BUILDING_SPACING - 0.5:
                too_close = True
                break
        if too_close:
            continue

        # Ikke kollider med gårdsrommet (sjekk at veggen faktisk ligger utenfor)
        if clipped.intersection(zone.courtyard_polygon).area > original_fp * 0.1:
            continue

        volume_counter += 1
        vol = Volume(
            volume_id=f"V{volume_counter:02d}",
            name=f"{typology} {name} ({zone.courtyard_name or zone.zone_id})",
            polygon=clipped,
            typology=typology,
            floors=target_floors,
            height_m=round(height_m, 1),
            width_m=round(w_len, 1),
            depth_m=round(w_d, 1),
            angle_deg=round(angle, 1),
            cx=round(wx, 1), cy=round(wy, 1),
            footprint_m2=round(float(clipped.area), 1),
            zone_id=zone.zone_id,
            program="bolig",
        )
        # bra_m2 er en property — beregnes automatisk
        vol.units_estimate = int(round(dims["units_per_floor"] * vol.floors))
        vol.oppganger = max(1, int(math.ceil(w_len / 25.0)))
        volumes.append(vol)
        placed += 1

    logger.info(
        f"Ring for {zone.zone_id}: {len(volumes)} vegger plassert rundt "
        f"'{zone.courtyard_name or 'gårdsrom'}', total BRA "
        f"{sum(v.bra_m2 for v in volumes):.0f} (mål {zone_bta_target:.0f})"
    )
    return volumes, volume_counter


# ─────────────────────────────────────────────────────────────────────
# PASS 4: Phasing (Building + Parking)
# ─────────────────────────────────────────────────────────────────────

def pass4_phasing(
    volumes: List[Volume],
    buildable_polygon,
    phasing_config: PhasingConfig,
    target_phase_count: int,
    program: ProgramAllocation,
    site_polygon,
) -> Tuple[List[BuildingPhase], List[ParkingPhase]]:
    """Del volumene i K byggefaser og M parkeringsfaser.

    Algoritme for byggefaser:
      1. Sortér volumer etter en "start-kandidat-score": foretrekk de som er
         lengst fra sentrum, har egen tilgang, har uterom-potensial
      2. Grupper greedy i K klustre med target BRA per fase
      3. Respekter hardgrense (maks 6500 per fase)
      4. Tildel rekkefølge via topologisk sortering basert på nabo-risiko

    Algoritme for parkeringsfaser:
      - Single garage-modus: 1 P-fase som serves alle B-faser
      - Auto: 1-3 P-faser basert på tomtas fragmentering og B-fase-tall
    """
    if not volumes:
        return [], []

    k = max(1, target_phase_count)

    # KRITISK: Re-kalibrer K basert på FAKTISK oppnådd BRA, ikke mål-BRA.
    # Hvis Pass 3 bare plasserte 50% av målet (pga tight tomt, small typology,
    # osv.), blir target_phase_count for høyt og hvert trinn blir undermål.
    # Vi sikrer at hvert trinn ender i 3500-4500 m² BRA-målområdet.
    actual_total_bra = sum(v.bra_m2 for v in volumes)
    if actual_total_bra > 0:
        # Ideelt antall faser basert på faktisk BRA og målet om 4000 m² per trinn
        target_avg = (phasing_config.TARGET_PHASE_BRA_LOW +
                      phasing_config.TARGET_PHASE_BRA_HIGH) / 2.0
        ideal_k = max(1, round(actual_total_bra / target_avg))

        # Respekter phasing_config sine harde grenser
        min_k_hard = max(1, math.ceil(actual_total_bra / phasing_config.MAX_PHASE_BRA))
        max_k_hard = max(1, int(actual_total_bra / phasing_config.MIN_PHASE_BRA))
        ideal_k = _clamp(ideal_k, min_k_hard, max_k_hard)

        # v1.5: Respekter antall komposisjoner (zone_id-grupper) KUN hvis det
        # finnes volumer fra komponerte typologier. Lamell/Karré/Punkthus-volumer
        # plassert via grid er individuelle bygg, ikke komposisjoner, og skal
        # ikke styre K.
        composed_typologies = set(TYPOLOGY_COMPOSITIONS.keys())
        composition_zones = set()
        for v in volumes:
            if v.typology in composed_typologies:
                zid = getattr(v, "zone_id", None)
                if zid:
                    composition_zones.add(zid)
        n_compositions = len(composition_zones)

        # Juster K: respekter brukerens valg i manual/single-modus, men veiled i auto
        if phasing_config.phasing_mode == "auto":
            # Hvis komposisjoner finnes: K skal matche antall komposisjoner.
            # Unntak: hvis en enkelt komposisjon er så stor at den bryter
            # MAX_PHASE_BRA, tillater vi at den splittes til flere trinn.
            if n_compositions > 0:
                # Regn ut eksakt antall "sub-trinn" per komposisjon
                # (en komposisjon kan splittes i flere hvis BRA > MAX)
                total_composition_trinn = 0
                for zid in composition_zones:
                    zone_bra = sum(v.bra_m2 for v in volumes
                                   if getattr(v, "zone_id", None) == zid)
                    if zone_bra > phasing_config.MAX_PHASE_BRA:
                        n_splits = math.ceil(zone_bra / phasing_config.MAX_PHASE_BRA)
                        total_composition_trinn += max(2, n_splits)
                    else:
                        total_composition_trinn += 1

                # Inkluder eventuelle individuelle bygg som egne trinn
                individual_count = sum(1 for v in volumes if v.typology not in composed_typologies)
                if individual_count > 0:
                    individual_bra = sum(v.bra_m2 for v in volumes if v.typology not in composed_typologies)
                    ideal_individual_k = max(1, round(individual_bra / target_avg))
                else:
                    ideal_individual_k = 0

                k = min(total_composition_trinn + ideal_individual_k, max_k_hard, len(volumes))
                k = max(k, 1)
                logger.info(
                    f"Pass 4: K={k} trinn ({n_compositions} komposisjoner → "
                    f"{total_composition_trinn} sub-trinn + {ideal_individual_k} individ-trinn; "
                    f"BRA {actual_total_bra:.0f})"
                )
            else:
                # Ingen komposisjoner — fall tilbake til BRA-basert K
                k = ideal_k
                logger.info(
                    f"Pass 4: K={k} trinn (BRA-basert; ingen komposisjoner)"
                )
        elif phasing_config.phasing_mode == "manual":
            # Manuelt er brukerens valg lov, men cap til hard-grensen
            k = _clamp(target_phase_count, min_k_hard, max_k_hard)
            if k != target_phase_count:
                logger.warning(
                    f"Pass 4: Manuelt valg {target_phase_count} var utenfor gjennomførbar "
                    f"spenn [{min_k_hard}-{max_k_hard}] for faktisk BRA {actual_total_bra:.0f}. "
                    f"Justert til {k}."
                )
        # single-mode: k=1 som brukeren valgte, ingen endring

        # Men hvis volumtallet er < K, kan vi ikke ha flere faser enn volumer
        k = min(k, len(volumes))

    # --- Byggefaser ---
    building_phases = _group_volumes_into_phases(
        volumes, k, phasing_config, buildable_polygon, program,
    )

    # --- Parkeringsfaser ---
    parking_phases = _generate_parking_phases(
        building_phases, volumes, buildable_polygon,
        phasing_config, program,
    )

    # Koble hver B-fase til sine P-faser
    for bphase in building_phases:
        bphase.parking_served_by = [
            pp.phase_number for pp in parking_phases
            if bphase.phase_number in pp.serves_building_phases
        ]
        if not bphase.parking_served_by and parking_phases:
            # Default: første P-fase serves alt hvis ingen eksplisitt mapping
            bphase.parking_served_by = [parking_phases[0].phase_number]

    return building_phases, parking_phases


def _group_volumes_into_phases(
    volumes: List[Volume],
    k: int,
    phasing_config: PhasingConfig,
    buildable_polygon,
    program: ProgramAllocation,
) -> List[BuildingPhase]:
    """Kjernealgoritmen: grupper volumer i K faser."""
    if not volumes:
        return []

    total_bra = sum(v.bra_m2 for v in volumes)
    target_per_phase = total_bra / k

    # Finn tomta-senter for scoring
    centroid = buildable_polygon.centroid
    cx, cy = centroid.x, centroid.y

    # Identifiser "startkandidat"-volumer:
    # Volumer som er lengst unna senter og har egen kant mot ytterkanten
    # kan starte som standalone-faser
    for v in volumes:
        v._dist_to_center = math.hypot(v.cx - cx, v.cy - cy)  # type: ignore

    # Finn "naboskap"-grupper via spatial clustering
    clusters = _spatial_cluster_volumes(
        volumes, k, buildable_polygon,
        max_phase_bra=phasing_config.MAX_PHASE_BRA,
        min_phase_bra=phasing_config.MIN_PHASE_BRA,
    )

    # Bygg BuildingPhase fra hvert cluster
    phases: List[BuildingPhase] = []
    for idx, cluster_volumes in enumerate(clusters, start=1):
        if not cluster_volumes:
            continue
        phase_bra = sum(v.bra_m2 for v in cluster_volumes)
        phase_units = sum(v.units_estimate for v in cluster_volumes)
        programs_in_phase: List[ProgramKind] = []
        for v in cluster_volumes:
            if v.program not in programs_in_phase:
                programs_in_phase.append(v.program)
            if v.ground_floor_program and v.ground_floor_program not in programs_in_phase:
                programs_in_phase.append(v.ground_floor_program)

        # Label foreslås fra typologi-miks
        typ_counts: Dict[str, int] = {}
        for v in cluster_volumes:
            typ_counts[v.typology] = typ_counts.get(v.typology, 0) + 1
        label_parts = [f"{n}×{t}" for t, n in sorted(typ_counts.items(), key=lambda x: -x[1])]
        label = f"Trinn {idx} — " + ", ".join(label_parts)

        phase = BuildingPhase(
            phase_number=idx,
            label=label,
            volume_ids=[v.volume_id for v in cluster_volumes],
            target_bra=target_per_phase,
            actual_bra=phase_bra,
            programs_included=programs_in_phase,
            units_estimate=phase_units,
        )

        # Oppdater volumer med fase-tilknytning
        for v in cluster_volumes:
            v.assigned_phase = idx

        phases.append(phase)

    # Sortér faser etter startkandidat-logikk: starte nær en ytterkant
    # som har adkomst, og som er "isolerbar" fra resten under bygging.
    phases = _order_phases_by_construction_sequence(phases, volumes, buildable_polygon)

    # Beregn construction barrier-zone for hver fase
    for phase in phases:
        phase_volumes = [v for v in volumes if v.volume_id in phase.volume_ids]
        if phase_volumes:
            union = unary_union([v.polygon for v in phase_volumes])
            phase.construction_barrier_zone = union.buffer(15.0)  # 15m byggeplass-sone

    # Sett avhengigheter (enkel regel: hver fase avhenger av forrige)
    # Dette kan utvides med smartere logikk i Pass 6
    for i, phase in enumerate(phases):
        if i > 0:
            phase.depends_on_phases = [phases[i-1].phase_number]

    return phases


def _spatial_cluster_volumes(volumes: List[Volume], k: int,
                             buildable_polygon,
                             max_phase_bra: float = 6500.0,
                             min_phase_bra: float = 2500.0) -> List[List[Volume]]:
    """K-means-lignende clustering på volum-sentre for å lage K faser.

    NYTT (v1.5): Volumer som tilhører en KOMPONERT typologi (LamellSegmentert,
    HalvåpenKarré, Gårdsklynge) holdes sammen som atomic units — de er én
    kvartalsstruktur og skal bygges som ett byggetrinn. Volumer fra
    individuelle typologier (Lamell, Karré, Punkthus osv.) kan fordeles fritt
    mellom trinn slik som før.
    """
    if not volumes:
        return []
    if k <= 1 or len(volumes) <= k:
        if k <= 1:
            return [list(volumes)]
        return [[v] for v in volumes]

    # Identifiser komposisjons-volumer (de som kommer fra komponerte typologier).
    # Disse grupperes per zone_id og holdes sammen som én atomic unit.
    composed_typologies = set(TYPOLOGY_COMPOSITIONS.keys())
    composition_groups: Dict[str, List[Volume]] = {}
    individual_volumes: List[Volume] = []

    for v in volumes:
        if v.typology in composed_typologies and getattr(v, "zone_id", None):
            key = v.zone_id
            if key not in composition_groups:
                composition_groups[key] = []
            composition_groups[key].append(v)
        else:
            individual_volumes.append(v)

    n_compositions = len(composition_groups)
    n_individuals = len(individual_volumes)

    # CASE A: Alt er komposisjoner — hver komposisjon blir ett trinn (opp til k)
    if n_individuals == 0 and n_compositions > 0:
        clusters: List[List[Volume]] = []
        for key, group in composition_groups.items():
            group_bra = sum(v.bra_m2 for v in group)
            # Splitt hvis komposisjonen overstiger MAX_PHASE_BRA.
            if group_bra > max_phase_bra:
                n_splits = math.ceil(group_bra / max_phase_bra)
                n_splits = max(2, min(n_splits, len(group)))
                # Sortér segmenter langs lengste akse
                x_coords = [v.cx for v in group]
                y_coords = [v.cy for v in group]
                x_range = max(x_coords) - min(x_coords) if x_coords else 0
                y_range = max(y_coords) - min(y_coords) if y_coords else 0
                sort_key = (lambda v: v.cx) if x_range >= y_range else (lambda v: v.cy)
                group_sorted = sorted(group, key=sort_key)

                # Balansert fordeling: greedy fyll opp til MAX per sub-cluster,
                # men forsøk å matche snitt-BRA per sub-cluster
                target_bra_per_split = group_bra / n_splits
                sub_clusters: List[List[Volume]] = [[] for _ in range(n_splits)]
                sub_bra = [0.0] * n_splits
                # Tildel volumer i rekkefølge til den sub-clusteren som har lavest BRA
                for v in group_sorted:
                    # Finn sub-cluster med lavest BRA som fortsatt ikke er full
                    idx = min(range(n_splits), key=lambda i: sub_bra[i])
                    sub_clusters[idx].append(v)
                    sub_bra[idx] += v.bra_m2
                # Filtrer tomme (skal ikke skje)
                sub_clusters = [sc for sc in sub_clusters if sc]
                clusters.extend(sub_clusters)
            else:
                clusters.append(group)
        # Hvis vi nå har for mange clusters (> k), merge nærmeste naboer
        while len(clusters) > k:
            clusters = _merge_nearest_clusters(clusters)
        logger.info(
            f"Zone-aware clustering: {n_compositions} komposisjoner → {len(clusters)} trinn"
        )
        return clusters

    # CASE B: Kun individuelle volumer — bruk gammel k-means
    if n_compositions == 0 and n_individuals > 0:
        return _spatial_cluster_volumes_legacy(
            volumes, k, buildable_polygon, max_phase_bra, min_phase_bra,
        )

    # CASE C: Miks av komposisjoner og individuelle volumer.
    # Strategi: hver komposisjon blir ett trinn. Resterende k-slots fylles med
    # individuelle volumer via k-means.
    clusters = []
    slots_used = 0
    for key, group in composition_groups.items():
        clusters.append(group)
        slots_used += 1

    remaining_k = k - slots_used
    if remaining_k > 0 and individual_volumes:
        individual_clusters = _spatial_cluster_volumes_legacy(
            individual_volumes, remaining_k, buildable_polygon,
            max_phase_bra, min_phase_bra,
        )
        clusters.extend(individual_clusters)
    elif individual_volumes:
        # Ingen flere slots — legg individuelle volumer til nærmeste komposisjonsklynge
        for iv in individual_volumes:
            def cluster_dist(cl):
                if not cl:
                    return float("inf")
                c_x = sum(v.cx for v in cl) / len(cl)
                c_y = sum(v.cy for v in cl) / len(cl)
                return math.hypot(iv.cx - c_x, iv.cy - c_y)
            best_cluster = min(clusters, key=cluster_dist)
            best_cluster.append(iv)

    # Trim hvis vi har flere clusters enn k
    while len(clusters) > k:
        clusters = _merge_nearest_clusters(clusters)

    logger.info(
        f"Zone-aware clustering: {n_compositions} komposisjoner + "
        f"{n_individuals} individ. → {len(clusters)} trinn"
    )
    return clusters


def _merge_nearest_clusters(clusters: List[List[Volume]]) -> List[List[Volume]]:
    """Hjelpefunksjon: finn de to nærmeste clustrene og slå dem sammen."""
    if len(clusters) <= 1:
        return clusters

    def cluster_center(cl):
        if not cl:
            return 0, 0
        return (sum(v.cx for v in cl) / len(cl),
                sum(v.cy for v in cl) / len(cl))

    best_i, best_j, best_dist = 0, 1, float("inf")
    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            ci_x, ci_y = cluster_center(clusters[i])
            cj_x, cj_y = cluster_center(clusters[j])
            dist = math.hypot(ci_x - cj_x, ci_y - cj_y)
            if dist < best_dist:
                best_dist = dist
                best_i, best_j = i, j

    merged = clusters[:best_i] + [clusters[best_i] + clusters[best_j]] + \
             clusters[best_i+1:best_j] + clusters[best_j+1:]
    return merged


def _spatial_cluster_volumes_legacy(volumes: List[Volume], k: int,
                             buildable_polygon,
                             max_phase_bra: float = 6500.0,
                             min_phase_bra: float = 2500.0) -> List[List[Volume]]:
    """Legacy K-means-lignende clustering (brukes for individuelle volumer).

    Bruker enkel greedy-algoritme: start med K seed-volumer som er lengst fra
    hverandre, så tildel hvert resterende volum til nærmeste cluster.
    Etter første runde: balansér BRA per cluster.
    """
    if not volumes:
        return []
    if k <= 1 or len(volumes) <= k:
        if k <= 1:
            return [list(volumes)]
        # Ett volum per cluster hvis vi har færre volumer enn ønskede faser
        return [[v] for v in volumes]

    # 1. Velg K seeds som er maksimalt spredt (K-means++ style)
    seeds: List[Volume] = []
    # Første seed: volumet med størst avstand fra tomtens senter
    centroid = buildable_polygon.centroid
    cx, cy = centroid.x, centroid.y
    first = max(volumes, key=lambda v: math.hypot(v.cx - cx, v.cy - cy))
    seeds.append(first)

    while len(seeds) < k:
        # Velg neste seed som har størst min-avstand til eksisterende seeds
        def min_dist_to_seeds(v):
            return min(math.hypot(v.cx - s.cx, v.cy - s.cy) for s in seeds)
        next_seed = max(
            (v for v in volumes if v not in seeds),
            key=min_dist_to_seeds,
            default=None,
        )
        if next_seed is None:
            break
        seeds.append(next_seed)

    # 2. Tildel alle volumer til nærmeste seed
    clusters: List[List[Volume]] = [[] for _ in seeds]
    for v in volumes:
        dists = [math.hypot(v.cx - s.cx, v.cy - s.cy) for s in seeds]
        best_idx = dists.index(min(dists))
        clusters[best_idx].append(v)

    # 3. Balanser: hvis en cluster har > max_phase_bra, flytt "ytterst" volum til naboclusteren
    # Vi kjører flere iterasjoner og prøver å få alle clusters under hard-grensen
    total_bra = sum(v.bra_m2 for v in volumes)
    theoretical_avg = total_bra / k if k > 0 else 0
    # Hvis selv det teoretiske snittet overgår max, kan vi ikke hjelpe
    can_respect_max = theoretical_avg <= max_phase_bra

    for iteration in range(50):  # økt til 50 iterasjoner
        bra_per_cluster = [sum(v.bra_m2 for v in c) for c in clusters]
        max_idx = bra_per_cluster.index(max(bra_per_cluster))
        min_idx = bra_per_cluster.index(min(bra_per_cluster))

        # Stopp hvis vi er ferdig:
        # 1. Ingen cluster overstiger max_phase_bra (når det er mulig)
        # 2. Spread er liten (< 1500 m²)
        if can_respect_max and bra_per_cluster[max_idx] <= max_phase_bra:
            if bra_per_cluster[max_idx] - bra_per_cluster[min_idx] < 1500:
                break
        elif bra_per_cluster[max_idx] - bra_per_cluster[min_idx] < 1500:
            break

        if not clusters[max_idx] or not clusters[min_idx]:
            break
        if len(clusters[max_idx]) <= 1:
            # Kan ikke tømme max-clusteret mer
            break

        # Flytt volumet i max-cluster som er nærmest min-clusters centroid
        min_centroid_x = sum(v.cx for v in clusters[min_idx]) / len(clusters[min_idx])
        min_centroid_y = sum(v.cy for v in clusters[min_idx]) / len(clusters[min_idx])
        candidate = min(
            clusters[max_idx],
            key=lambda v: math.hypot(v.cx - min_centroid_x, v.cy - min_centroid_y),
        )
        clusters[max_idx].remove(candidate)
        clusters[min_idx].append(candidate)

    # Fjern tomme clusters
    clusters = [c for c in clusters if c]
    return clusters


def _order_phases_by_construction_sequence(
    phases: List[BuildingPhase],
    volumes: List[Volume],
    buildable_polygon,
) -> List[BuildingPhase]:
    """Sorter faser i rekkefølge: start ved en ytterkant, jobb innover/mot motsatt kant.

    Heuristikk: Finn tomtas lengste akse. Start-fasen er den som har senter nærmest
    den ene enden av denne aksen. Resten følger langs aksen.
    """
    if len(phases) <= 1:
        # Fortsatt sett phase_number korrekt
        for i, p in enumerate(phases, start=1):
            p.phase_number = i
        return phases

    bounds = buildable_polygon.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]

    # Bestem aksen
    if bw >= bh:
        # Horisontal akse: start vest (lav X)
        def axis_key(phase):
            phase_vols = [v for v in volumes if v.volume_id in phase.volume_ids]
            return sum(v.cx for v in phase_vols) / max(len(phase_vols), 1)
    else:
        # Vertikal akse: start sør (lav Y) — ellers nord (høy Y)?
        def axis_key(phase):
            phase_vols = [v for v in volumes if v.volume_id in phase.volume_ids]
            return sum(v.cy for v in phase_vols) / max(len(phase_vols), 1)

    phases_sorted = sorted(phases, key=axis_key)
    for i, p in enumerate(phases_sorted, start=1):
        p.phase_number = i
        p.label = p.label.replace("Trinn ", f"Trinn {i} — ", 1) if not p.label.startswith(f"Trinn {i}") else p.label
        # Opprydding: labelen ble laget før sortering; re-generer
        typ_counts: Dict[str, int] = {}
        for vid in p.volume_ids:
            vol = next((x for x in volumes if x.volume_id == vid), None)
            if vol:
                typ_counts[vol.typology] = typ_counts.get(vol.typology, 0) + 1
        label_parts = [f"{n}×{t}" for t, n in sorted(typ_counts.items(), key=lambda x: -x[1])]
        p.label = f"Trinn {i} — " + ", ".join(label_parts)

    # Oppdater volumer med nye fasenumre
    for phase in phases_sorted:
        for vid in phase.volume_ids:
            vol = next((x for x in volumes if x.volume_id == vid), None)
            if vol:
                vol.assigned_phase = phase.phase_number

    return phases_sorted


def _generate_parking_phases(
    building_phases: List[BuildingPhase],
    volumes: List[Volume],
    buildable_polygon,
    phasing_config: PhasingConfig,
    program: ProgramAllocation,
) -> List[ParkingPhase]:
    """Generer parkeringsfaser.

    Modi:
      - single_garage: 1 P-fase med hele kjelleren, ferdig før B1
      - auto/manual: 1-3 P-faser basert på tomtas størrelse og antall B-faser
    """
    if not building_phases:
        return []

    total_spaces = program.parking_spaces_required
    mode = phasing_config.parking_mode

    if mode == "single_garage":
        num_p_phases = 1
    elif mode == "manual" and phasing_config.manual_parking_phase_count:
        num_p_phases = max(1, phasing_config.manual_parking_phase_count)
    else:
        # auto: basert på B-fase-antall og tomtas spredning
        n_b = len(building_phases)
        if n_b <= 3:
            num_p_phases = 1
        elif n_b <= 6:
            num_p_phases = 2
        else:
            num_p_phases = 3

    parking_phases: List[ParkingPhase] = []
    spaces_per_p = int(math.ceil(total_spaces / num_p_phases)) if num_p_phases > 0 else 0

    if num_p_phases == 1:
        # Hele kjelleren under alt
        union_polys = [v.polygon for v in volumes if v.polygon is not None]
        if union_polys:
            kjeller_poly = unary_union(union_polys).buffer(8.0)
            # Begrens til byggbart areal
            kjeller_poly = kjeller_poly.intersection(buildable_polygon)
        else:
            kjeller_poly = buildable_polygon

        # Plasser én rampe ved kanten nærmest en antatt offentlig veg
        bounds = buildable_polygon.bounds
        ramp_point = Point(bounds[0] + 10, (bounds[1] + bounds[3]) / 2)  # vestkant default

        parking_phases.append(ParkingPhase(
            phase_number=1,
            polygon=kjeller_poly,
            num_spaces=total_spaces,
            ramps=[ParkingRamp(
                ramp_id="R1",
                point=ramp_point,
                access_from_road="(bestemmes i detaljprosjekt)",
                handles_construction_traffic=True,
            )],
            serves_building_phases=[p.phase_number for p in building_phases],
            construction_sequence=0,
            must_complete_before_building_phase=building_phases[0].phase_number,
            notes="Én sammenhengende p-kjeller under hele prosjektet, ferdigstilles før første byggefase tas i bruk."
        ))
        return parking_phases

    # Flere P-faser: del B-fasene i grupper, én P-fase per gruppe
    b_per_p = math.ceil(len(building_phases) / num_p_phases)
    for p_idx in range(num_p_phases):
        start = p_idx * b_per_p
        end = min(start + b_per_p, len(building_phases))
        served_phases = building_phases[start:end]
        if not served_phases:
            continue

        # Polygon = union av volumene i disse B-fasene, bufret
        served_vol_ids = set()
        for bp in served_phases:
            served_vol_ids.update(bp.volume_ids)
        served_polys = [v.polygon for v in volumes
                        if v.volume_id in served_vol_ids and v.polygon is not None]
        if served_polys:
            p_poly = unary_union(served_polys).buffer(8.0).intersection(buildable_polygon)
        else:
            continue

        # Plassering av rampe: kant av polygonet nærmest tomtekant
        bounds = p_poly.bounds
        # Velg side basert på p_idx for å spre ramper
        if p_idx == 0:
            ramp_pt = Point(bounds[0] + 3, (bounds[1] + bounds[3]) / 2)
        elif p_idx == 1:
            ramp_pt = Point(bounds[2] - 3, (bounds[1] + bounds[3]) / 2)
        else:
            ramp_pt = Point((bounds[0] + bounds[2]) / 2, bounds[1] + 3)

        ramps = []
        # P2 og videre kan koble på P1 uten egen rampe hvis de ligger inntil hverandre
        connects_to = []
        if p_idx > 0 and parking_phases:
            prev = parking_phases[-1]
            if p_poly.distance(prev.polygon) < 5:  # de ligger inntil hverandre
                connects_to = [prev.phase_number]

        if not connects_to:
            ramps.append(ParkingRamp(
                ramp_id=f"R{p_idx+1}",
                point=ramp_pt,
                handles_construction_traffic=True,
            ))

        parking_phases.append(ParkingPhase(
            phase_number=p_idx + 1,
            polygon=p_poly,
            num_spaces=spaces_per_p,
            ramps=ramps,
            extends_parking_phases=connects_to,
            serves_building_phases=[bp.phase_number for bp in served_phases],
            construction_sequence=p_idx,
            must_complete_before_building_phase=served_phases[0].phase_number,
            notes=(
                f"Betjener byggefase {served_phases[0].phase_number}-"
                f"{served_phases[-1].phase_number}. "
                + ("Utvider eksisterende kjeller." if connects_to else "Egen rampe.")
            )
        ))

    return parking_phases


# ─────────────────────────────────────────────────────────────────────
# PASS 5: Outdoor System
# ─────────────────────────────────────────────────────────────────────

def pass5_outdoor_system(
    buildable_polygon,
    volumes: List[Volume],
    building_phases: List[BuildingPhase],
    program: ProgramAllocation,
    site_inputs: Dict[str, Any],
    typology_zones: Optional[List[Any]] = None,  # v1.7: TypologyZone-liste
) -> OutdoorSystem:
    """Bygg uterom-systemet: diagonal, tun, MUA-flater, gangnett.

    Prinsipp (inspirert av LPO Tyholt):
    - Hovedakse/diagonal gjennom tomta som grønn og sosial korridor
    - Mellomrom mellom volumer blir tun (lokale MUA-soner)
    - Tak-arealer markeres som MUA på tak
    - Hver byggefase får sin dedikerte standalone-uterom
    - v1.7: DESIGNETE gårdsrom fra Pass 2 registreres først med sin definerte
      funksjon (felles_bolig / barnehage_ute / lek_gront), før restareal
      tildeles som tun/diagonal.
    """
    system = OutdoorSystem()
    if not HAS_SHAPELY or buildable_polygon is None or buildable_polygon.is_empty:
        return system

    # 1. Bygg fotavtrykk-unionen av alle volumer
    if not volumes:
        # Ingen volumer → ingen uterom (skal ikke skje)
        return system

    volume_polys = [v.polygon for v in volumes if v.polygon is not None]
    if not volume_polys:
        return system
    volumes_union = unary_union(volume_polys)

    # 2. Uterom på bakke = byggbart polygon minus volumene
    ground_outdoor = buildable_polygon.difference(volumes_union).buffer(0)
    if ground_outdoor.is_empty:
        return system

    # v1.7: Registrer designete gårdsrom først med eksplisitt funksjon
    designed_courtyard_union = None
    if typology_zones:
        for zone in typology_zones:
            cp = getattr(zone, "courtyard_polygon", None)
            if cp is None or cp.is_empty:
                continue
            # Gårdsrommet er allerede innenfor zone.polygon, som er innenfor
            # buildable_polygon. Trekk fra volumene som faktisk ligger rundt
            # (ringen bør ikke overlappe gårdsrommet, men robusthet: buffer 0.5m)
            court_geom = cp.difference(volumes_union.buffer(0.5)).buffer(0)
            if court_geom.is_empty or court_geom.area < 50:
                continue
            if isinstance(court_geom, MultiPolygon):
                court_geom = max(court_geom.geoms, key=lambda g: g.area)

            function_name = getattr(zone, "courtyard_function", "") or "felles_bolig"
            # Map til OutdoorKind
            outdoor_kind: OutdoorKind = "tun"
            requires_sun = 4.0
            if function_name == "barnehage_ute":
                outdoor_kind = "barnehage_ute"
                requires_sun = 5.0  # strengere sol-krav for barnehage
            elif function_name == "lek_gront":
                outdoor_kind = "lek"
                requires_sun = 4.0
            elif function_name in ("felles_bolig", "plantekasser"):
                outdoor_kind = "tun"
                requires_sun = 4.0

            zone_name = getattr(zone, "courtyard_name", "") or getattr(zone, "zone_id", "")
            zone_program = getattr(zone, "courtyard_program", "") or ""
            notes_parts = [f"Designet gårdsrom: {zone_name}"]
            if zone_program:
                notes_parts.append(f"Program: {zone_program}")

            system.zones.append(OutdoorZone(
                zone_id=f"OD-court-{getattr(zone, 'zone_id', '?')}",
                kind=outdoor_kind,
                geometry=court_geom,
                area_m2=float(court_geom.area),
                counts_toward_mua=True,
                is_felles=True,
                on_ground=True,
                serves_building_phases=[],  # fylles ut senere
                requires_sun_hours=requires_sun,
                notes=". ".join(notes_parts),
            ))

        # Union av alle designete gårdsrom — brukes for å trekke fra
        # ground_outdoor slik at de ikke dobbeltelles som tun
        court_polys = [
            z.geometry for z in system.zones
            if z.kind in ("tun", "barnehage_ute", "lek") and "OD-court-" in z.zone_id
        ]
        if court_polys:
            designed_courtyard_union = unary_union(court_polys)

    # 3. Generer diagonal: fra ett hjørne til motsatt, så bred som mulig innenfor ground_outdoor
    # Hvis designete gårdsrom finnes, går diagonalen UTENOM dem
    diagonal_search_space = ground_outdoor
    if designed_courtyard_union is not None and not designed_courtyard_union.is_empty:
        try:
            diagonal_search_space = ground_outdoor.difference(
                designed_courtyard_union.buffer(1.0)
            ).buffer(0)
        except Exception:
            diagonal_search_space = ground_outdoor

    diagonal_zone, diagonal_line = _generate_diagonal(
        buildable_polygon, volumes_union, diagonal_search_space,
    )
    if diagonal_zone and not diagonal_zone.is_empty:
        system.zones.append(OutdoorZone(
            zone_id="OD-diagonal",
            kind="diagonal",
            geometry=diagonal_zone,
            area_m2=float(diagonal_zone.area),
            counts_toward_mua=True,
            is_felles=True,
            on_ground=True,
            serves_building_phases=[bp.phase_number for bp in building_phases],
            requires_sun_hours=4.0,
            notes="Hovedferdselsåre gjennom tomta, grønn og sosial",
        ))
        system.diagonal_linestring = diagonal_line

    # 4. Tun — resterende uterom mellom volumer, delt per fase
    zone_counter = 1
    remaining_outdoor = ground_outdoor.difference(diagonal_zone) if diagonal_zone else ground_outdoor
    # v1.7: Trekk også fra designete gårdsrom slik at de ikke dobbelttelles som tun
    if designed_courtyard_union is not None and not designed_courtyard_union.is_empty:
        try:
            remaining_outdoor = remaining_outdoor.difference(
                designed_courtyard_union.buffer(0.5)
            ).buffer(0)
        except Exception:
            pass

    if isinstance(remaining_outdoor, (Polygon, MultiPolygon)):
        if isinstance(remaining_outdoor, MultiPolygon):
            subparts = list(remaining_outdoor.geoms)
        else:
            subparts = [remaining_outdoor]

        for sub in subparts:
            if sub.area < 80:
                continue
            # Tildel tunet til nærmeste byggefase
            serving_phase_num = _find_nearest_phase(sub, building_phases, volumes)
            system.zones.append(OutdoorZone(
                zone_id=f"OD-tun-{zone_counter:02d}",
                kind="tun",
                geometry=sub,
                area_m2=float(sub.area),
                counts_toward_mua=True,
                is_felles=True,
                on_ground=True,
                serves_building_phases=[serving_phase_num] if serving_phase_num else [],
                requires_sun_hours=4.0,
                notes="Lokal uteplass mellom bygg",
            ))
            zone_counter += 1

    # 5. Barnehage-uteareal (hvis barnehage finnes)
    if program.barnehage_bra > 0:
        bh_volume = next((v for v in volumes if v.program == "barnehage"
                          or v.ground_floor_program == "barnehage"), None)
        if bh_volume:
            # Tegn 2448 m² uteareal inntil barnehagevolumet, helst sør for det
            bh_poly = bh_volume.polygon
            # Bygg en buffer sørover fra volumet
            bounds = bh_poly.bounds
            width = bounds[2] - bounds[0]
            depth_needed = max(20.0, program.barnehage_uteareal_m2 / max(width, 20.0))
            # Lag rektangel sør for bh_poly
            bh_outdoor_rect = shapely_box(
                bounds[0] - 5, bounds[1] - depth_needed - 5,
                bounds[2] + 5, bounds[1] - 2,
            )
            bh_outdoor = bh_outdoor_rect.intersection(ground_outdoor).buffer(0)
            if not bh_outdoor.is_empty and bh_outdoor.area > 500:
                if isinstance(bh_outdoor, MultiPolygon):
                    bh_outdoor = max(bh_outdoor.geoms, key=lambda g: g.area)
                system.zones.append(OutdoorZone(
                    zone_id="OD-barnehage",
                    kind="barnehage_ute",
                    geometry=bh_outdoor,
                    area_m2=float(bh_outdoor.area),
                    counts_toward_mua=False,  # teller ikke som bolig-MUA
                    is_felles=False,
                    on_ground=True,
                    serves_building_phases=[bh_volume.assigned_phase] if bh_volume.assigned_phase else [],
                    notes="Barnehage-uteareal (6-base krever ~2448 m²)",
                ))

    # 6. Tak-MUA — arealer på flate tak fungerer som privat eller felles MUA
    # Estimert 30% av fotavtrykk-arealet er egnet tak-MUA
    total_tak_mua = sum(v.footprint_m2 * 0.30 for v in volumes if v.floors >= 3)
    if total_tak_mua > 0:
        system.zones.append(OutdoorZone(
            zone_id="OD-tak",
            kind="tak_mua",
            geometry=volumes_union,  # symbolsk - det ligger på takene
            area_m2=total_tak_mua,
            counts_toward_mua=True,
            is_felles=True,
            on_ground=False,
            serves_building_phases=[bp.phase_number for bp in building_phases],
            notes="Tak-uterom (~30% av takflater på 3+ etg bygg)",
        ))

    # 7. Tildel hver byggefase sitt standalone-uterom
    for phase in building_phases:
        phase_zones = [z for z in system.zones
                       if phase.phase_number in z.serves_building_phases
                       and z.is_felles and z.on_ground]
        phase.standalone_outdoor_zone_ids = [z.zone_id for z in phase_zones]
        phase.standalone_outdoor_m2 = sum(z.area_m2 for z in phase_zones)
        phase.standalone_outdoor_has_sun = True  # forenklet - utvides i Pass 6

    return system


def _generate_diagonal(buildable_polygon, volumes_union, ground_outdoor):
    """Generer diagonal-aksen som grønn hovedferdselsåre.

    Strategi: tegn en linje fra ett hjørne til motsatt, bufre til korridor-bredde
    (6-8m), klipp mot ground_outdoor for å holde den innenfor uterom.
    """
    bounds = buildable_polygon.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]

    # Finn to motsatte hjørner som gir lengst diagonal
    corners = [
        (bounds[0], bounds[1]),  # SW
        (bounds[2], bounds[1]),  # SE
        (bounds[2], bounds[3]),  # NE
        (bounds[0], bounds[3]),  # NW
    ]

    # Prøv begge diagonaler
    candidates = [
        (corners[0], corners[2]),  # SW-NE
        (corners[1], corners[3]),  # SE-NW
    ]

    best_diag = None
    best_area = 0.0
    best_line = None
    for start, end in candidates:
        line = LineString([start, end])
        corridor = line.buffer(4.0)  # 8m bred korridor
        corridor_in_outdoor = corridor.intersection(ground_outdoor).buffer(0)
        if corridor_in_outdoor.is_empty:
            continue
        # Vi vil ha mest mulig sammenhengende areal
        if isinstance(corridor_in_outdoor, MultiPolygon):
            largest = max(corridor_in_outdoor.geoms, key=lambda g: g.area)
            area = largest.area
            candidate = largest
        else:
            area = corridor_in_outdoor.area
            candidate = corridor_in_outdoor
        if area > best_area:
            best_area = area
            best_diag = candidate
            best_line = line
    return best_diag, best_line


def _find_nearest_phase(outdoor_poly, building_phases: List[BuildingPhase],
                        volumes: List[Volume]) -> Optional[int]:
    """Finn byggefasen hvis volumer er nærmest dette uterommet."""
    best_phase = None
    best_dist = float("inf")
    for phase in building_phases:
        phase_vols = [v for v in volumes if v.volume_id in phase.volume_ids]
        if not phase_vols:
            continue
        phase_union = unary_union([v.polygon for v in phase_vols if v.polygon])
        d = outdoor_poly.distance(phase_union)
        if d < best_dist:
            best_dist = d
            best_phase = phase.phase_number
    return best_phase


# ─────────────────────────────────────────────────────────────────────
# PASS 6: Validation & Refinement
# ─────────────────────────────────────────────────────────────────────

def pass6_validate(
    masterplan: Masterplan,
    max_bya_pct: float,
    max_floors: int,
    max_height_m: float,
) -> Masterplan:
    """Hard validering og metrics-beregning.

    Sjekker:
      - BYA-compliance
      - Floor/height limits per volum
      - MUA-krav per byggesone 2 (40 m²/bolig, 50% felles, 50% bakke)
      - Standalone-bokvalitet per fase
      - Brannavstander (minst 8m mellom volumer)
      - Dependencies konsistens
    """
    warnings = list(masterplan.warnings)

    # 1. Volum-validering
    for v in masterplan.volumes:
        if v.floors > max_floors:
            warnings.append(f"Volum {v.name}: {v.floors} etasjer overskrider maks {max_floors}")
            v.floors = max_floors
            v.height_m = min(v.height_m, max_floors * 3.2)
        if v.height_m > max_height_m:
            warnings.append(f"Volum {v.name}: høyde {v.height_m:.1f}m overskrider maks {max_height_m}")
            v.height_m = max_height_m
            v.floors = max(1, int(v.height_m / 3.2))

    # 2. BYA-sjekk
    total_footprint = sum(v.footprint_m2 for v in masterplan.volumes)
    buildable_area = float(masterplan.buildable_polygon.area) if masterplan.buildable_polygon else 0
    bya_pct = (total_footprint / buildable_area * 100) if buildable_area > 0 else 0
    if bya_pct > max_bya_pct + 1:
        warnings.append(f"BYA {bya_pct:.1f}% overskrider maks {max_bya_pct:.1f}%")

    # 3. MUA-sjekk
    mua_total = masterplan.outdoor_system.mua_total()
    mua_required = masterplan.program.mua_total_required
    mua_bakke = masterplan.outdoor_system.mua_on_ground_felles()
    mua_bakke_required = masterplan.program.mua_bakke_min

    mua_compliant = True
    if mua_required > 0:
        if mua_total < mua_required:
            warnings.append(
                f"MUA-underskudd: {mua_total:.0f} m² tilgjengelig, {mua_required:.0f} m² påkrevd"
            )
            mua_compliant = False
        if mua_bakke < mua_bakke_required:
            warnings.append(
                f"MUA bakke/felles-underskudd: {mua_bakke:.0f} m² tilgjengelig, "
                f"{mua_bakke_required:.0f} m² påkrevd (byggesone 2: min 50% av felles på bakke)"
            )
            mua_compliant = False

    # 4. Brannavstander
    fire_violations = 0
    for i, v1 in enumerate(masterplan.volumes):
        for v2 in masterplan.volumes[i+1:]:
            if v1.polygon and v2.polygon:
                d = v1.polygon.distance(v2.polygon)
                if d < MIN_BUILDING_SPACING - 0.1:
                    fire_violations += 1
    if fire_violations > 0:
        warnings.append(
            f"Brannavstand: {fire_violations} volum-par har < {MIN_BUILDING_SPACING}m avstand"
        )

    # 5. Standalone-bokvalitet per byggefase
    habitability_scores = []
    for phase in masterplan.building_phases:
        score, issues = _evaluate_phase_standalone(
            phase, masterplan.volumes, masterplan.building_phases,
            masterplan.outdoor_system, masterplan.parking_phases,
        )
        phase.standalone_habitable = score >= 60
        phase.standalone_issues = issues
        habitability_scores.append(score)

    avg_habitability = sum(habitability_scores) / len(habitability_scores) if habitability_scores else 0

    # 6. Byggeplass-nærhet for senere faser
    for i, phase in enumerate(masterplan.building_phases):
        # Hvor mye byggeplass er det fra senere faser rundt dette trinnets volumer?
        later_barriers = [
            p.construction_barrier_zone for p in masterplan.building_phases[i+1:]
            if p.construction_barrier_zone is not None
        ]
        if later_barriers and phase.construction_barrier_zone is not None:
            phase_vols_union = unary_union([
                v.polygon for v in masterplan.volumes
                if v.volume_id in phase.volume_ids and v.polygon is not None
            ])
            nearest_dist = min(
                phase_vols_union.distance(b) for b in later_barriers
            )
            # Jo nærmere byggeplass, jo høyere risiko (0 = byggeplass overlappet, 1 = langt unna)
            phase.neighboring_construction_risk = max(0, 1 - nearest_dist / 30.0)

    # 7. Beregn metrics
    metrics = MasterplanMetrics(
        total_bra=sum(v.bra_m2 for v in masterplan.volumes),
        total_bta=sum(v.footprint_m2 * v.floors for v in masterplan.volumes),
        total_footprint_m2=total_footprint,
        bya_percent=bya_pct,
        units_total=sum(v.units_estimate for v in masterplan.volumes),
        phase_count_buildings=len(masterplan.building_phases),
        phase_count_parking=len(masterplan.parking_phases),
        mua_total_m2=mua_total,
        mua_required_m2=mua_required,
        mua_compliant=mua_compliant,
        avg_phase_bra=(
            sum(p.actual_bra for p in masterplan.building_phases) / len(masterplan.building_phases)
            if masterplan.building_phases else 0
        ),
        min_phase_bra=min((p.actual_bra for p in masterplan.building_phases), default=0),
        max_phase_bra=max((p.actual_bra for p in masterplan.building_phases), default=0),
        standalone_habitability_score=avg_habitability,
    )
    # Overall score: vektet kombinasjon (v2 — tuned)
    #
    # Vekter basert på hva som faktisk betyr noe for utbygger/bomiljø:
    #   40% Habitability — kjernen: kan hver fase stå som selvstendig bomiljø?
    #   20% MUA compliance — hard TEK17-grense, men binær (pass/fail)
    #   15% BYA compliance — regulatorisk grense, gradert
    #   10% Brannavstand — hard TEK-grense
    #    8% Fase-balanse — belønner jevn BRA-fordeling mellom trinn (3500-4500 mål)
    #    7% Byggeplass-risiko — straffer når sene faser naboforstyrrer tidlige
    #
    # Totalsum = 100%. Skalaene er 0-100 per komponent, så overall ender i 0-100.

    # Komponent 1: Habitability (40%)
    score_habitability = avg_habitability

    # Komponent 2: MUA compliance (20%) — binær
    score_mua = 100.0 if mua_compliant else 55.0

    # Komponent 3: BYA compliance (15%) — gradert med takt
    if bya_pct <= max_bya_pct:
        score_bya = 100.0
    else:
        overshoot = bya_pct - max_bya_pct
        score_bya = max(0.0, 100.0 - overshoot * 6.0)  # 6 poeng per prosentpoeng over

    # Komponent 4: Brannavstand (10%) — bør være 0 brudd
    if fire_violations == 0:
        score_fire = 100.0
    else:
        score_fire = max(0.0, 100.0 - fire_violations * 15.0)

    # Komponent 5: Fase-balanse (8%) — belønn hvis alle faser ligger
    # nær målområdet 3500-4500 m² BRA
    if masterplan.building_phases:
        target_center = 4000.0  # midt i 3500-4500-båndet
        soft_max = 4500.0
        soft_min = 3500.0
        deviations = []
        for p in masterplan.building_phases:
            bra = p.actual_bra
            if soft_min <= bra <= soft_max:
                dev = 0.0  # i target-bånd
            elif bra < soft_min:
                dev = (soft_min - bra) / soft_min  # relativ underskudd
            else:  # bra > soft_max
                dev = (bra - soft_max) / target_center  # relativ overskudd
            deviations.append(min(dev, 1.0))
        avg_deviation = sum(deviations) / len(deviations)
        # 0 deviation = 100, deviation 1.0 = 40
        score_balance = max(40.0, 100.0 - avg_deviation * 60.0)
    else:
        score_balance = 0.0

    # Komponent 6: Byggeplass-risiko (7%) — straff når sene faser har byggeplass-
    # avtrykk nær tidlige bebodde faser. Verdiene ligger allerede på fasene.
    if masterplan.building_phases:
        risks = [p.neighboring_construction_risk for p in masterplan.building_phases]
        avg_risk = sum(risks) / len(risks)
        # risk 0 = perfekt = 100, risk 1 = alle faser forstyrrer hverandre = 40
        score_construction_risk = max(40.0, 100.0 - avg_risk * 60.0)
    else:
        score_construction_risk = 100.0

    # Vektet total
    metrics.overall_score = (
        0.40 * score_habitability
        + 0.20 * score_mua
        + 0.15 * score_bya
        + 0.10 * score_fire
        + 0.08 * score_balance
        + 0.07 * score_construction_risk
    )

    masterplan.metrics = metrics
    masterplan.warnings = warnings

    # Claude-review: la en "erfaren norsk byplanlegger" se hele masterplanen
    # og flagge inkonsekvenser eller forbedringspunkter. Resultatet legges til
    # som tilleggsadvarsler, ikke som harde valideringsfeil.
    api_key = _get_api_key()
    if api_key and masterplan.building_phases:
        try:
            review_notes = _pass6_claude_review(masterplan, api_key)
            if review_notes:
                for note in review_notes:
                    masterplan.warnings.append(f"[AI-review] {note}")
        except Exception as exc:
            logger.warning(f"Pass 6 Claude-review feilet: {exc}")

    return masterplan


def _pass6_claude_review(masterplan: Masterplan, api_key: str) -> List[str]:
    """La Claude se hele masterplanen og flagge konsistens-problemer eller
    forbedringsforslag. Returnerer liste av korte tekststrenger (max ~6 notater).
    """
    m = masterplan.metrics
    # Bygg et kompakt tekstsammendrag for Claude (ikke send hele polygonene)
    phase_lines = []
    for p in masterplan.building_phases:
        progs = ", ".join(p.programs_included) if p.programs_included else "bolig"
        phase_lines.append(
            f"  T{p.phase_number}: {p.actual_bra:.0f} m² BRA, {p.units_estimate} bol., "
            f"prog=[{progs}], P-fase={p.parking_served_by}, "
            f"uterom={p.standalone_outdoor_m2:.0f} m², "
            f"avhenger av T{p.depends_on_phases or '—'}, "
            f"standalone={'OK' if p.standalone_habitable else 'NEI'}"
        )
    parking_lines = []
    for pp in masterplan.parking_phases:
        ramp = f"{len(pp.ramps)} rampe(r)" if pp.ramps else f"utvider P{pp.extends_parking_phases}"
        parking_lines.append(
            f"  P{pp.phase_number}: {pp.num_spaces} plasser, {ramp}, "
            f"betjener T{pp.serves_building_phases}"
        )

    zone_lines = []
    for z in masterplan.typology_zones:
        zone_lines.append(f"  {z.zone_id}: {z.typology}, {z.floors_min}-{z.floors_max} et")

    prog = masterplan.program

    prompt = f"""Du er en erfaren norsk byplanlegger og boligutvikler. Vurder denne masterplanen for
konsistens og praktisk gjennomførbarhet. Pek på ting en faglig kvalitetssikrer ville reagert på.

MASTERPLAN-SAMMENDRAG:
Total BRA: {m.total_bra:,.0f} m² ({m.units_total} boliger)
BYA: {m.bya_percent:.1f}%
Antall byggetrinn: {m.phase_count_buildings} (snitt {m.avg_phase_bra:,.0f} m² BRA)
Antall p-faser: {m.phase_count_parking}
MUA: {m.mua_total_m2:.0f} m² (krav {m.mua_required_m2:.0f}, {'compliant' if m.mua_compliant else 'UNDERSKUDD'})
Standalone-score: {m.standalone_habitability_score:.0f}/100

PROGRAM:
Bolig: {prog.bolig_bra:.0f} m²
Barnehage: {prog.barnehage_bra:.0f} m²
Næring: {prog.naering_bra:.0f} m²

TYPOLOGI-SONER:
{chr(10).join(zone_lines)}

BYGGETRINN:
{chr(10).join(phase_lines)}

PARKERINGSFASER:
{chr(10).join(parking_lines)}

OPPGAVE:
Pek på inntil 5 KONKRETE ting som bør vurderes eller forbedres. Fokuser på:
- Rekkefølge-logikk (er avhengigheter realistiske? barnehage tidlig nok?)
- Byggeplass-logistikk (lever bebodde trinn side om side med byggeplasser for senere trinn?)
- Parkering-tilknytning (rekker P-fasene å bli ferdig før B-fasene de betjener?)
- Programmiks (er barnehagen plassert fornuftig ift. bolig og næring?)
- MUA-fordeling (god balanse bakke/tak? tilstrekkelig sol?)

Returner KUN en JSON-liste med korte norske setninger (hver < 140 tegn).
Hvis alt er OK, returner tom liste [].

Eksempel:
["Trinn 3 bygges samtidig med T4 — fare for anleggstrafikk gjennom ferdig bomiljø i T2.",
 "Barnehagen ligger i T6 men bør være klar før innflytting i T3 pga rekkefølgekrav."]"""

    raw = _call_claude(prompt, api_key, temperature=0.2, max_tokens=1500)
    parsed = _parse_json(raw)
    if isinstance(parsed, list):
        return [str(x)[:200] for x in parsed[:6] if x]
    return []



def _evaluate_phase_standalone(
    phase: BuildingPhase,
    all_volumes: List[Volume],
    all_phases: List[BuildingPhase],
    outdoor_system: OutdoorSystem,
    parking_phases: List[ParkingPhase],
) -> Tuple[float, List[str]]:
    """Vurder om denne byggefasen kan stå som selvstendig bomiljø.

    Kriterier:
    - Har egne oppganger / adkomst (0-30 poeng)
    - Har dedikert uterom >= 10 m² per bolig (0-25 poeng)
    - Har parkeringstilknytning (0-20 poeng)
    - Er isolerbar fra neste byggeplass (0-15 poeng)
    - Rekkefølgekrav oppfylt (0-10 poeng)
    """
    score = 0.0
    issues = []

    phase_volumes = [v for v in all_volumes if v.volume_id in phase.volume_ids]

    # Kriterium 1: Adkomst (30p). Hvis det er minst én oppgang per volum, full score.
    total_oppganger = sum(v.oppganger for v in phase_volumes)
    if total_oppganger >= len(phase_volumes):
        score += 30
    else:
        score += 15
        issues.append("Antall oppganger per volum er for lavt")

    # Kriterium 2: Uterom (25p). ~10 m² felles MUA per bolig er et bra standalone-gulv.
    mua_per_unit = phase.standalone_outdoor_m2 / max(phase.units_estimate, 1)
    if mua_per_unit >= 10:
        score += 25
    elif mua_per_unit >= 5:
        score += 15
        issues.append(f"Standalone-uterom pr enhet ({mua_per_unit:.1f} m²) er marginalt")
    else:
        score += 5
        issues.append(f"Standalone-uterom pr enhet ({mua_per_unit:.1f} m²) er for lavt")

    # Kriterium 3: Parkering (20p)
    if phase.parking_served_by:
        # Finn om minst én P-fase ferdigstilles før eller samtidig med denne B-fasen
        required_p = [p for p in parking_phases if p.phase_number in phase.parking_served_by]
        if any(p.must_complete_before_building_phase is None
               or p.must_complete_before_building_phase <= phase.phase_number
               for p in required_p):
            score += 20
        else:
            score += 10
            issues.append("P-fase ferdigstilles etter byggefasens innflytting")
    else:
        issues.append("Ingen parkering tilknyttet")

    # Kriterium 4: Isolerbarhet (15p) — lav risiko for senere byggeplasser
    if phase.neighboring_construction_risk < 0.3:
        score += 15
    elif phase.neighboring_construction_risk < 0.6:
        score += 8
        issues.append("Moderat byggeplass-risiko fra senere faser")
    else:
        issues.append("Høy byggeplass-risiko fra senere faser")

    # Kriterium 5: Rekkefølgekrav (10p) — hvis fasen inkluderer barnehage eller park
    # og kommer tidlig, bonus
    if "barnehage" in phase.programs_included and phase.phase_number <= len(all_phases) // 2:
        score += 10
    elif not any(p in phase.programs_included for p in ["barnehage", "naering"]):
        # Ren bolig — ingen rekkefølgekrav → full score
        score += 10
    else:
        score += 5

    return score, issues


# ─────────────────────────────────────────────────────────────────────
# Hovedentry: plan_masterplan
# ─────────────────────────────────────────────────────────────────────

def plan_masterplan(
    site_polygon,
    buildable_polygon,
    *,
    neighbors: Optional[List[Dict[str, Any]]] = None,
    terrain: Optional[Dict[str, Any]] = None,
    site_intelligence: Optional[Dict[str, Any]] = None,
    site_inputs: Optional[Dict[str, Any]] = None,
    target_bra_m2: float = 5000.0,
    max_floors: int = 5,
    max_height_m: float = 16.0,
    max_bya_pct: float = 35.0,
    floor_to_floor_m: float = 3.2,
    phasing_config: Optional[PhasingConfig] = None,
    include_barnehage: bool = False,
    include_naering: bool = False,
    byggesone: str = "2",
    model: str = DEFAULT_MODEL,
) -> Masterplan:
    """Bygg en komplett masterplan via 6-pass arkitektur.

    Returnerer et Masterplan-objekt. Bruk .to_legacy_result() for å få
    dict-format som dagens Mulighetsstudie-pipeline forventer.
    """
    if not HAS_SHAPELY:
        raise RuntimeError("Shapely er påkrevd for masterplan-motoren")
    if buildable_polygon is None or buildable_polygon.is_empty:
        raise ValueError("buildable_polygon kan ikke være tom")

    phasing_config = phasing_config or PhasingConfig()
    site_inputs = site_inputs or {}
    site_inputs.setdefault("terrain", terrain)
    site_inputs.setdefault("site_intelligence", site_intelligence)

    # Kontekst
    neighbor_text, nb_polys = _summarize_neighbor_context(neighbors, site_polygon)

    # --- PASS 1: Program ---
    logger.info("Masterplan Pass 1: Program synthesis")
    program = pass1_program_synthesis(
        target_bra=target_bra_m2,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
        neighbor_summary=neighbor_text,
        byggesone=byggesone if byggesone in ("1", "2", "3", "4") else "2",
        include_barnehage=include_barnehage,
        include_naering=include_naering,
    )

    # --- PASS 0 (etter program): Anbefal antall faser ---
    logger.info("Masterplan Pass 0: Phase count recommendation")
    phase_rec = pass0_recommend_phase_count(
        target_bra=target_bra_m2,
        buildable_polygon=buildable_polygon,
        phasing_config=phasing_config,
        neighbor_summary=neighbor_text,
        site_program=program,
    )
    target_phase_count = phase_rec["recommended"]

    # --- PASS 2: Typology zoning ---
    logger.info(f"Masterplan Pass 2: Typology zoning (target {target_phase_count} phases)")
    zones = pass2_typology_zoning(
        buildable_polygon=buildable_polygon,
        neighbor_summary=neighbor_text,
        nb_polys=nb_polys,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        terrain=terrain,
        target_bra_m2=target_bra_m2,
        max_bya_pct=max_bya_pct,
    )

    # v1.6 diag: skriv hva Pass 2 faktisk produserte. Dette hjelper oss å debugge
    # når produksjonen viser færre trinn enn testene våre forventer.
    pass2_diag_lines = [f"Pass 2 produserte {len(zones)} soner:"]
    for z in zones:
        try:
            z_area = float(z.polygon.area) if z.polygon else 0.0
            zb = z.polygon.bounds if z.polygon else (0, 0, 0, 0)
            zw = zb[2] - zb[0]
            zh = zb[3] - zb[1]
        except Exception:
            z_area, zw, zh = 0.0, 0.0, 0.0
        pass2_diag_lines.append(
            f"  {z.zone_id} ({z.typology}, {z.floors_min}-{z.floors_max}et): "
            f"{z_area:.0f} m² ({zw:.0f}×{zh:.0f}), mål BRA {z.target_bra:.0f}"
        )
    pass2_diag_text = "\n".join(pass2_diag_lines)
    logger.info(pass2_diag_text)

    # --- PASS 3: Volume placement ---
    logger.info(f"Masterplan Pass 3: Volume placement in {len(zones)} zones")
    volumes = pass3_place_volumes(
        zones=zones,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
        neighbors=neighbors,
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
    )

    # --- PASS 4: Phasing ---
    logger.info(f"Masterplan Pass 4: Phasing {len(volumes)} volumes into {target_phase_count} phases")
    building_phases, parking_phases = pass4_phasing(
        volumes=volumes,
        buildable_polygon=buildable_polygon,
        phasing_config=phasing_config,
        target_phase_count=target_phase_count,
        program=program,
        site_polygon=site_polygon,
    )

    # --- PASS 5: Outdoor system ---
    logger.info("Masterplan Pass 5: Outdoor system")
    outdoor = pass5_outdoor_system(
        buildable_polygon=buildable_polygon,
        volumes=volumes,
        building_phases=building_phases,
        program=program,
        site_inputs=site_inputs,
        typology_zones=zones,  # v1.7: gir tilgang til designete gårdsrom
    )

    # Bygg masterplan
    masterplan = Masterplan(
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        program=program,
        typology_zones=zones,
        volumes=volumes,
        outdoor_system=outdoor,
        building_phases=building_phases,
        parking_phases=parking_phases,
        phasing_config=phasing_config,
        site_inputs=site_inputs,
        concept_narrative=phase_rec.get("reasoning", ""),
        warnings=[],
        source=f"Builtly Masterplan v1 ({model})",
        diag_info={
            "pass2": pass2_diag_text,
            "pass3": f"Pass 3 plasserte {len(volumes)} volumer totalt",
            "pass4": f"Pass 4 grupperte til {len(building_phases)} byggetrinn",
        },
    )

    # --- PASS 6: Validate ---
    logger.info("Masterplan Pass 6: Validation")
    masterplan = pass6_validate(
        masterplan=masterplan,
        max_bya_pct=max_bya_pct,
        max_floors=max_floors,
        max_height_m=max_height_m,
    )

    return masterplan


def plan_site_via_masterplan(*args, **kwargs) -> Dict[str, Any]:
    """Drop-in-erstatning for ai_site_planner.plan_site() som returnerer
    legacy-format dict, men kjører masterplan-motoren under panseret.

    Accepts same kwargs as plan_masterplan, plus typology (ignored — zones decide),
    plus legacy kwargs for bakoverkompatibilitet.
    """
    # Filter ut gamle kwargs som ikke passer masterplan
    kwargs.pop("typology", None)  # gamle API-et tok én typologi; nå ignoreres det
    model = kwargs.pop("model", DEFAULT_MODEL)
    mp = plan_masterplan(*args, model=model, **kwargs)
    return mp.to_legacy_result()


# ====================================================================
# V2 PATCHES — tetthet, fasering og BRA-konsistens for større tomter
# ====================================================================
# Disse override-funksjonene legges nederst i filen slik at Python bruker
# dem i stedet for de tidligere definisjonene. Målet er å beholde mest mulig
# av den eksisterende motoren, men rette de viktigste svakhetene:
#   1) hardkodet BRA/BTA-effektivitet 0.85
#   2) underfylling av store tomter med komposisjonstyper
#   3) auto-fasering som kollapser K når Pass 3 underleverer
#   4) for svak score-straff når BRA-målet ikke nås
#   5) skjev uteromsallokering der fase 1 får "alt" og resten rester

_ORIG_COMPOSE_LINEAR = _compose_linear
_ORIG_COMPOSE_COURTYARD = _compose_courtyard
_ORIG_PLACE_RING = _place_ring_around_courtyard
_ORIG_PASS3_PLACE_VOLUMES = pass3_place_volumes
_ORIG_PASS4_PHASING = pass4_phasing
_ORIG_PASS5_OUTDOOR_SYSTEM = pass5_outdoor_system
_ORIG_PASS6_VALIDATE = pass6_validate


def _get_efficiency_ratio_v2(site_inputs: Optional[Dict[str, Any]] = None) -> float:
    site_inputs = site_inputs or {}
    try:
        eff = float(site_inputs.get("efficiency_ratio", 0.85) or 0.85)
    except Exception:
        eff = 0.85
    return _clamp(eff, 0.60, 0.95)


def _apply_efficiency_ratio_to_volumes_v2(volumes: List[Volume], efficiency_ratio: float) -> List[Volume]:
    for v in volumes:
        try:
            v.bra_efficiency_ratio = efficiency_ratio
        except Exception:
            pass
    return volumes


def _max_zone_floors_v2(typology: str, zone: Optional[TypologyZone], max_floors: int,
                        max_height_m: float, floor_to_floor_m: float) -> Tuple[int, float]:
    dims = TYPOLOGY_DIMS.get(typology, TYPOLOGY_DIMS["Lamell"])
    ftf = float(dims.get("ftf", floor_to_floor_m) or floor_to_floor_m)
    zone_fmax = int(getattr(zone, "floors_max", max_floors) or max_floors) if zone is not None else max_floors
    height_cap = max(1, int(max_height_m / max(ftf, 0.1)))
    return max(1, min(zone_fmax, max_floors, int(dims["f_max"]), height_cap)), ftf


def _raise_zone_volumes_to_target_v2(volumes: List[Volume], zone: TypologyZone,
                                     zone_bta_target: float, max_floors: int,
                                     max_height_m: float,
                                     floor_to_floor_m: float) -> List[Volume]:
    if not volumes:
        return volumes

    current_bta = sum(v.footprint_m2 * v.floors for v in volumes)
    if current_bta >= zone_bta_target * 0.98:
        return volumes

    remaining_bta = zone_bta_target - current_bta
    progress = True
    ordered = sorted(volumes, key=lambda v: v.footprint_m2, reverse=True)
    while remaining_bta > 1.0 and progress:
        progress = False
        for v in ordered:
            max_allowed, ftf = _max_zone_floors_v2(v.typology, zone, max_floors, max_height_m, floor_to_floor_m)
            if v.floors >= max_allowed:
                continue
            v.floors += 1
            v.height_m = round(v.floors * ftf, 1)
            remaining_bta -= v.footprint_m2
            progress = True
            if remaining_bta <= 1.0:
                break
    return volumes


def _zone_target_bta_map_v2(zones: List[TypologyZone], program: ProgramAllocation,
                            efficiency_ratio: float) -> Dict[str, float]:
    total_zone_target = sum(z.target_bra for z in zones) or 1.0
    total_target_bta = program.total_bra / max(efficiency_ratio, 1e-6)
    return {
        z.zone_id: (z.target_bra / total_zone_target) * total_target_bta
        for z in zones
    }


def _densify_volumes_to_target_v2(zones: List[TypologyZone],
                                  volumes: List[Volume],
                                  program: ProgramAllocation,
                                  max_floors: int,
                                  max_height_m: float,
                                  max_bya_pct: float,
                                  floor_to_floor_m: float,
                                  buildable_polygon,
                                  site_inputs: Dict[str, Any]) -> List[Volume]:
    if not volumes or not zones or buildable_polygon is None or buildable_polygon.is_empty:
        return volumes

    efficiency_ratio = _get_efficiency_ratio_v2(site_inputs)
    _apply_efficiency_ratio_to_volumes_v2(volumes, efficiency_ratio)

    target_total_bta = program.total_bra / max(efficiency_ratio, 1e-6)
    actual_total_bta = sum(v.footprint_m2 * v.floors for v in volumes)
    if actual_total_bta >= target_total_bta * 0.97:
        return volumes

    zone_targets_bta = _zone_target_bta_map_v2(zones, program, efficiency_ratio)
    zone_by_id = {z.zone_id: z for z in zones}

    # Trinn 1: løft etasjeantall på eksisterende volumer der sonen har underskudd.
    for _ in range(20):
        actual_total_bta = sum(v.footprint_m2 * v.floors for v in volumes)
        if actual_total_bta >= target_total_bta * 0.97:
            break

        zone_actual_bta: Dict[str, float] = {z.zone_id: 0.0 for z in zones}
        for v in volumes:
            if v.zone_id in zone_actual_bta:
                zone_actual_bta[v.zone_id] += v.footprint_m2 * v.floors

        candidates = []
        for v in volumes:
            zone = zone_by_id.get(v.zone_id)
            if zone is None:
                continue
            deficit = zone_targets_bta.get(v.zone_id, 0.0) - zone_actual_bta.get(v.zone_id, 0.0)
            if deficit <= 0:
                continue
            max_allowed, ftf = _max_zone_floors_v2(v.typology, zone, max_floors, max_height_m, floor_to_floor_m)
            if v.floors < max_allowed:
                candidates.append((deficit, v.footprint_m2, v, max_allowed, ftf))

        if not candidates:
            break

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        progress = False
        for _, _, v, max_allowed, ftf in candidates:
            if actual_total_bta >= target_total_bta * 0.97:
                break
            if v.floors >= max_allowed:
                continue
            v.floors += 1
            v.height_m = round(v.floors * ftf, 1)
            actual_total_bta += v.footprint_m2
            progress = True

        if not progress:
            break

    # Trinn 2: legg inn infill-volumer i restareal for soner som fortsatt mangler kapasitet.
    actual_total_bta = sum(v.footprint_m2 * v.floors for v in volumes)
    max_fp_total = float(buildable_polygon.area) * (max_bya_pct / 100.0)
    fp_used = sum(v.footprint_m2 for v in volumes)
    if actual_total_bta < target_total_bta * 0.97 and fp_used < max_fp_total * 0.95:
        existing_ids = []
        for v in volumes:
            try:
                if isinstance(v.volume_id, str) and v.volume_id.startswith("V"):
                    existing_ids.append(int(v.volume_id[1:]))
            except Exception:
                pass
        volume_counter = max(existing_ids, default=0)

        def zone_actual_bta(zid: str) -> float:
            return sum(v.footprint_m2 * v.floors for v in volumes if v.zone_id == zid)

        zone_order = sorted(
            zones,
            key=lambda z: zone_targets_bta.get(z.zone_id, 0.0) - zone_actual_bta(z.zone_id),
            reverse=True,
        )

        for zone in zone_order:
            actual_total_bta = sum(v.footprint_m2 * v.floors for v in volumes)
            fp_used = sum(v.footprint_m2 for v in volumes)
            if actual_total_bta >= target_total_bta * 0.97 or fp_used >= max_fp_total * 0.95:
                break

            deficit = zone_targets_bta.get(zone.zone_id, 0.0) - zone_actual_bta(zone.zone_id)
            if deficit <= 250.0:
                continue

            zone_existing = [v.polygon for v in volumes if v.zone_id == zone.zone_id and v.polygon is not None]
            remaining_poly = zone.polygon.buffer(0)
            if zone_existing:
                try:
                    remaining_poly = remaining_poly.difference(
                        unary_union(zone_existing).buffer(MIN_BUILDING_SPACING / 2.0)
                    ).buffer(0)
                except Exception:
                    remaining_poly = remaining_poly.buffer(0)

            if remaining_poly.is_empty or float(remaining_poly.area) < 150.0:
                continue

            infill_typology = zone.typology if zone.typology not in TYPOLOGY_COMPOSITIONS else "Lamell"
            infill_zone = TypologyZone(
                zone_id=zone.zone_id,
                typology=infill_typology,
                polygon=remaining_poly,
                floors_min=max(3, zone.floors_min),
                floors_max=zone.floors_max,
                target_bra=0.0,
                rationale=f"v2 infill for {zone.zone_id}",
            )

            extra_vols, volume_counter = _fallback_grid_placement(
                zone=infill_zone,
                zone_buildable=remaining_poly,
                zone_bta_target=deficit,
                max_floors=max_floors,
                max_height_m=max_height_m,
                max_fp_remaining=max_fp_total - fp_used,
                floor_to_floor_m=floor_to_floor_m,
                existing_polys=[v.polygon for v in volumes if v.polygon is not None],
                volume_counter=volume_counter,
            )

            if not extra_vols and infill_typology != "Lamell":
                infill_zone.typology = "Lamell"
                extra_vols, volume_counter = _fallback_grid_placement(
                    zone=infill_zone,
                    zone_buildable=remaining_poly,
                    zone_bta_target=deficit,
                    max_floors=max_floors,
                    max_height_m=max_height_m,
                    max_fp_remaining=max_fp_total - fp_used,
                    floor_to_floor_m=floor_to_floor_m,
                    existing_polys=[v.polygon for v in volumes if v.polygon is not None],
                    volume_counter=volume_counter,
                )

            for ev in extra_vols:
                ev.zone_id = zone.zone_id
                ev.notes = (ev.notes + " (v2 densifisering)").strip()
                ev.bra_efficiency_ratio = efficiency_ratio

            if extra_vols:
                volumes.extend(extra_vols)

    return volumes


def _compose_linear(zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
                    max_fp_remaining, floor_to_floor_m, existing_polys,
                    volume_counter, comp) -> Tuple[List[Volume], int]:
    volumes, volume_counter = _ORIG_COMPOSE_LINEAR(
        zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
        max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter, comp,
    )
    volumes = _raise_zone_volumes_to_target_v2(
        volumes, zone, zone_bta_target, max_floors, max_height_m, floor_to_floor_m,
    )
    return volumes, volume_counter


def _compose_courtyard(zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
                       max_fp_remaining, floor_to_floor_m, existing_polys,
                       volume_counter, comp) -> Tuple[List[Volume], int]:
    volumes, volume_counter = _ORIG_COMPOSE_COURTYARD(
        zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
        max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter, comp,
    )
    volumes = _raise_zone_volumes_to_target_v2(
        volumes, zone, zone_bta_target, max_floors, max_height_m, floor_to_floor_m,
    )
    return volumes, volume_counter


def _place_ring_around_courtyard(zone, zone_buildable, zone_bta_target, max_floors,
                                 max_height_m, max_fp_remaining, floor_to_floor_m,
                                 existing_polys, volume_counter) -> Tuple[List[Volume], int]:
    volumes, volume_counter = _ORIG_PLACE_RING(
        zone, zone_buildable, zone_bta_target, max_floors, max_height_m,
        max_fp_remaining, floor_to_floor_m, existing_polys, volume_counter,
    )
    volumes = _raise_zone_volumes_to_target_v2(
        volumes, zone, zone_bta_target, max_floors, max_height_m, floor_to_floor_m,
    )
    return volumes, volume_counter


def pass3_place_volumes(zones: List[TypologyZone], program: ProgramAllocation,
                        max_floors: int, max_height_m: float, max_bya_pct: float,
                        floor_to_floor_m: float,
                        neighbors: Optional[List[Dict[str, Any]]],
                        site_polygon, buildable_polygon,
                        site_inputs: Dict[str, Any]) -> List[Volume]:
    volumes = _ORIG_PASS3_PLACE_VOLUMES(
        zones=zones,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
        neighbors=neighbors,
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
    )
    efficiency_ratio = _get_efficiency_ratio_v2(site_inputs)
    _apply_efficiency_ratio_to_volumes_v2(volumes, efficiency_ratio)
    volumes = _densify_volumes_to_target_v2(
        zones=zones,
        volumes=volumes,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
    )
    return volumes


def pass4_phasing(volumes: List[Volume], buildable_polygon,
                  phasing_config: PhasingConfig, target_phase_count: int,
                  program: ProgramAllocation, site_polygon) -> Tuple[List[BuildingPhase], List[ParkingPhase]]:
    if not volumes:
        return [], []

    actual_total_bra = sum(v.bra_m2 for v in volumes)
    target_total_bra = max(float(program.total_bra), 1.0)
    target_attainment = actual_total_bra / target_total_bra

    if phasing_config.phasing_mode == "auto" and target_attainment < 0.85:
        min_k_hard = max(1, math.ceil(actual_total_bra / phasing_config.MAX_PHASE_BRA))
        max_k_hard = max(1, int(actual_total_bra / phasing_config.MIN_PHASE_BRA)) if actual_total_bra >= phasing_config.MIN_PHASE_BRA else 1
        desired_k = int(_clamp(target_phase_count, min_k_hard, max_k_hard))
        logger.warning(
            "Pass 4 v2: Pass 3 leverte bare %.0f%% av BRA-målet; beholder target-basert faseantall %s i stedet for å kollapse til færre trinn.",
            target_attainment * 100.0,
            desired_k,
        )
        patched_cfg = PhasingConfig(
            phasing_mode="manual",
            manual_phase_count=desired_k,
            parking_mode=phasing_config.parking_mode,
            manual_parking_phase_count=phasing_config.manual_parking_phase_count,
            MIN_PHASE_BRA=phasing_config.MIN_PHASE_BRA,
            MAX_PHASE_BRA=phasing_config.MAX_PHASE_BRA,
            TARGET_PHASE_BRA_LOW=phasing_config.TARGET_PHASE_BRA_LOW,
            TARGET_PHASE_BRA_HIGH=phasing_config.TARGET_PHASE_BRA_HIGH,
            SINGLE_PHASE_MAX_BRA=phasing_config.SINGLE_PHASE_MAX_BRA,
        )
        return _ORIG_PASS4_PHASING(volumes, buildable_polygon, patched_cfg, desired_k, program, site_polygon)

    return _ORIG_PASS4_PHASING(volumes, buildable_polygon, phasing_config, target_phase_count, program, site_polygon)


def pass5_outdoor_system(buildable_polygon, volumes: List[Volume],
                         building_phases: List[BuildingPhase],
                         program: ProgramAllocation, site_inputs: Dict[str, Any],
                         typology_zones: Optional[List[Any]] = None) -> OutdoorSystem:
    system = _ORIG_PASS5_OUTDOOR_SYSTEM(
        buildable_polygon=buildable_polygon,
        volumes=volumes,
        building_phases=building_phases,
        program=program,
        site_inputs=site_inputs,
        typology_zones=typology_zones,
    )
    if not building_phases or not system.zones:
        return system

    phase_unions: Dict[int, Any] = {}
    for phase in building_phases:
        phase_vols = [v.polygon for v in volumes if v.volume_id in phase.volume_ids and v.polygon is not None]
        phase_unions[phase.phase_number] = unary_union(phase_vols) if phase_vols else None

    for z in system.zones:
        geom = getattr(z, "geometry", None)
        if geom is None or getattr(geom, "is_empty", True):
            continue
        if not z.on_ground or not z.is_felles:
            continue
        if z.kind not in ("diagonal", "park", "tun", "lek"):
            continue

        served = []
        threshold = 25.0 if z.kind == "diagonal" or z.area_m2 >= 600.0 else 12.0
        for phase_num, phase_union in phase_unions.items():
            if phase_union is None:
                continue
            try:
                if geom.distance(phase_union) <= threshold:
                    served.append(phase_num)
            except Exception:
                continue
        if served:
            z.serves_building_phases = served

    for phase in building_phases:
        phase_zones = [
            z for z in system.zones
            if phase.phase_number in z.serves_building_phases and z.is_felles and z.on_ground
        ]
        phase.standalone_outdoor_zone_ids = [z.zone_id for z in phase_zones]
        phase.standalone_outdoor_m2 = sum(z.area_m2 for z in phase_zones)
        phase.standalone_outdoor_has_sun = True

    return system


def _evaluate_phase_standalone(phase: BuildingPhase, all_volumes: List[Volume],
                               all_phases: List[BuildingPhase],
                               outdoor_system: OutdoorSystem,
                               parking_phases: List[ParkingPhase]) -> Tuple[float, List[str]]:
    score = 0.0
    issues = []
    phase_volumes = [v for v in all_volumes if v.volume_id in phase.volume_ids]

    total_oppganger = sum(v.oppganger for v in phase_volumes)
    if total_oppganger >= len(phase_volumes):
        score += 30
    else:
        score += 15
        issues.append("Antall oppganger per volum er for lavt")

    zone_count = len(phase.standalone_outdoor_zone_ids or [])
    full_req = max(800.0, float(phase.units_estimate) * 12.0)
    partial_req = max(400.0, float(phase.units_estimate) * 8.0)
    if phase.standalone_outdoor_m2 >= full_req and zone_count >= 1:
        score += 25
    elif phase.standalone_outdoor_m2 >= partial_req:
        score += 14
        issues.append(
            f"Standalone-uterom ({phase.standalone_outdoor_m2:.0f} m²) er på minimumssiden for {phase.units_estimate} boliger"
        )
    else:
        score += 3
        issues.append(
            f"Standalone-uterom ({phase.standalone_outdoor_m2:.0f} m²) er for lite for {phase.units_estimate} boliger"
        )
    if zone_count == 0:
        issues.append("Ingen dedikerte uteromssoner er koblet til byggetrinnet")

    if phase.parking_served_by:
        required_p = [p for p in parking_phases if p.phase_number in phase.parking_served_by]
        if any(p.must_complete_before_building_phase is None or p.must_complete_before_building_phase <= phase.phase_number for p in required_p):
            score += 20
        else:
            score += 10
            issues.append("P-fase ferdigstilles etter byggefasens innflytting")
    else:
        issues.append("Ingen parkering tilknyttet")

    if phase.neighboring_construction_risk < 0.3:
        score += 15
    elif phase.neighboring_construction_risk < 0.6:
        score += 8
        issues.append("Moderat byggeplass-risiko fra senere faser")
    else:
        issues.append("Høy byggeplass-risiko fra senere faser")

    if "barnehage" in phase.programs_included and phase.phase_number <= max(1, len(all_phases) // 2):
        score += 10
    elif not any(p in phase.programs_included for p in ["barnehage", "naering"]):
        score += 10
    else:
        score += 5

    return score, issues


def pass6_validate(masterplan: Masterplan, max_bya_pct: float,
                   max_floors: int, max_height_m: float) -> Masterplan:
    masterplan = _ORIG_PASS6_VALIDATE(masterplan, max_bya_pct, max_floors, max_height_m)
    m = masterplan.metrics
    warnings = list(masterplan.warnings)

    target_total_bra = float(masterplan.program.total_bra or 0.0)
    target_fit_pct = (m.total_bra / target_total_bra * 100.0) if target_total_bra > 0 else 100.0
    m.target_fit_pct = target_fit_pct

    if target_fit_pct < 90.0:
        warnings.append(
            f"BRA-mål nås ikke: {m.total_bra:,.0f} m² oppnådd av {target_total_bra:,.0f} m² mål ({target_fit_pct:.0f}%)."
        )
    if target_fit_pct < 90.0 and m.bya_percent < max_bya_pct * 0.75:
        warnings.append(
            f"Planen underutnytter tomta: BYA {m.bya_percent:.1f}% av tillatt {max_bya_pct:.1f}% samtidig som BRA-målet ikke nås."
        )

    score_target_fit = 100.0 * (min(max(target_fit_pct, 0.0), 100.0) / 100.0) ** 1.5
    score_habitability = m.standalone_habitability_score
    score_mua = 100.0 if m.mua_compliant else 55.0
    score_bya = 100.0 if m.bya_percent <= max_bya_pct else max(0.0, 100.0 - (m.bya_percent - max_bya_pct) * 6.0)

    fire_violations = 0
    for i, v1 in enumerate(masterplan.volumes):
        for v2 in masterplan.volumes[i + 1:]:
            if v1.polygon and v2.polygon and v1.polygon.distance(v2.polygon) < MIN_BUILDING_SPACING - 0.1:
                fire_violations += 1
    score_fire = 100.0 if fire_violations == 0 else max(0.0, 100.0 - fire_violations * 15.0)

    if masterplan.building_phases:
        target_center = 4000.0
        soft_min = 3500.0
        soft_max = 4500.0
        deviations = []
        for p in masterplan.building_phases:
            bra = p.actual_bra
            if soft_min <= bra <= soft_max:
                dev = 0.0
            elif bra < soft_min:
                dev = (soft_min - bra) / soft_min
            else:
                dev = (bra - soft_max) / target_center
            deviations.append(min(dev, 1.0))
        avg_deviation = sum(deviations) / len(deviations)
        score_balance = max(40.0, 100.0 - avg_deviation * 60.0)
        risks = [p.neighboring_construction_risk for p in masterplan.building_phases]
        avg_risk = sum(risks) / len(risks)
        score_construction_risk = max(40.0, 100.0 - avg_risk * 60.0)
    else:
        score_balance = 0.0
        score_construction_risk = 100.0

    m.overall_score = (
        0.25 * score_target_fit
        + 0.30 * score_habitability
        + 0.15 * score_mua
        + 0.10 * score_bya
        + 0.10 * score_fire
        + 0.05 * score_balance
        + 0.05 * score_construction_risk
    )

    # Dedup warnings med stabil rekkefølge
    seen = set()
    deduped = []
    for w in warnings:
        if w not in seen:
            deduped.append(w)
            seen.add(w)
    masterplan.warnings = deduped
    return masterplan


# ====================================================================
# V3 PATCHES — Tyholt / store tomter: kvartalsgrep, delt uterom-kreditt
# og bedre tetthetsstyring i Pass 2.
# ====================================================================

_ORIG_V2_PLAN_MASTERPLAN = plan_masterplan
_ORIG_V2_PASS2_TYPOLOGY_ZONING = pass2_typology_zoning
_ORIG_V2_PASS5_OUTDOOR_SYSTEM = pass5_outdoor_system
_ORIG_V2_PASS4_PHASING = pass4_phasing
_CURRENT_PASS2_EFFICIENCY_RATIO_V3 = 0.85


def _set_pass2_efficiency_ratio_v3(site_inputs: Optional[Dict[str, Any]] = None) -> None:
    global _CURRENT_PASS2_EFFICIENCY_RATIO_V3
    _CURRENT_PASS2_EFFICIENCY_RATIO_V3 = _get_efficiency_ratio_v2(site_inputs or {})


def _required_avg_floors_v3(target_bra_m2: float, buildable_polygon, max_bya_pct: float) -> float:
    if buildable_polygon is None or buildable_polygon.is_empty or target_bra_m2 <= 0:
        return 0.0
    buildable_area = float(buildable_polygon.area)
    max_fp = buildable_area * (max_bya_pct / 100.0)
    eff = max(_CURRENT_PASS2_EFFICIENCY_RATIO_V3, 1e-6)
    return target_bra_m2 / max(max_fp * eff, 1.0)


def _is_large_urban_masterplan_v3(buildable_polygon, program: ProgramAllocation,
                                  target_bra_m2: float, max_bya_pct: float) -> bool:
    if buildable_polygon is None or buildable_polygon.is_empty:
        return False
    buildable_area = float(buildable_polygon.area)
    req_avg = _required_avg_floors_v3(target_bra_m2, buildable_polygon, max_bya_pct)
    return buildable_area >= 15000.0 and program.total_bra >= 12000.0 and req_avg >= 2.6


def _sort_zones_along_primary_axis_v3(zones: List[TypologyZone], buildable_polygon) -> List[TypologyZone]:
    if not zones:
        return []
    bounds = buildable_polygon.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]
    if bh >= bw:
        return sorted(zones, key=lambda z: z.polygon.centroid.y if z.polygon else 0.0, reverse=True)
    return sorted(zones, key=lambda z: z.polygon.centroid.x if z.polygon else 0.0)


def _promote_zone_typology_v3(zone: TypologyZone, share: float, max_floors: int,
                              req_avg_floors: float) -> None:
    promoted = False
    if zone.typology in ("Rekke", "Tun") and share >= 0.12:
        zone.typology = "LamellSegmentert" if share < 0.28 else "HalvåpenKarré"
        promoted = True
    elif zone.typology == "Lamell" and share >= 0.18:
        zone.typology = "LamellSegmentert"
        promoted = True
    elif zone.typology == "Karré":
        zone.typology = "HalvåpenKarré"
        promoted = True

    if promoted:
        min_floor_target = 4 if req_avg_floors >= 3.2 else 3
        zone.floors_min = max(zone.floors_min, min_floor_target)
        zone.floors_max = max(zone.floors_max, min(max_floors, 5))
        zone.rationale = (zone.rationale + " | v3: promotert til urban kvartalstypologi for stor tomt og høyt BRA-mål").strip()


def _auto_courtyard_polygon_v3(zone: TypologyZone):
    if zone.polygon is None or zone.polygon.is_empty:
        return None
    if zone.courtyard_polygon is not None and not zone.courtyard_polygon.is_empty:
        return zone.courtyard_polygon

    poly = zone.polygon.buffer(0)
    bounds = poly.bounds
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    if min(w, h) < 36.0 or float(poly.area) < 2200.0:
        return None

    inset_x = max(12.0, min(w * 0.18, w / 3.0))
    inset_y = max(12.0, min(h * 0.18, h / 3.0))
    if w - 2 * inset_x < 15.0 or h - 2 * inset_y < 15.0:
        return None

    raw = shapely_box(bounds[0] + inset_x, bounds[1] + inset_y, bounds[2] - inset_x, bounds[3] - inset_y)
    court = raw.intersection(poly).buffer(0)
    if court.is_empty or float(court.area) < 225.0:
        return None
    if isinstance(court, MultiPolygon):
        court = max(court.geoms, key=lambda g: g.area)
    return court


def _assign_quarter_identity_v3(ordered_zones: List[TypologyZone], program: ProgramAllocation) -> None:
    total = len(ordered_zones)
    if total == 0:
        return
    for idx, zone in enumerate(ordered_zones):
        if zone.courtyard_polygon is None or zone.courtyard_polygon.is_empty:
            continue
        if program.barnehage_bra > 0 and idx == total - 1:
            zone.courtyard_name = zone.courtyard_name or "Barnehagegården"
            zone.courtyard_function = zone.courtyard_function or "barnehage_ute"
            zone.courtyard_program = zone.courtyard_program or "lek, trær, sandkasse"
            zone.floors_max = min(zone.floors_max, 4)
        elif idx == 0:
            zone.courtyard_name = zone.courtyard_name or "Nordgården"
            zone.courtyard_function = zone.courtyard_function or "plantekasser"
            zone.courtyard_program = zone.courtyard_program or "plantekasser, trær, benker"
        elif idx == 1:
            zone.courtyard_name = zone.courtyard_name or "Hovedgården"
            zone.courtyard_function = zone.courtyard_function or "felles_bolig"
            zone.courtyard_program = zone.courtyard_program or "trær, felles grill, blomster"
        else:
            zone.courtyard_name = zone.courtyard_name or "Lekegården"
            zone.courtyard_function = zone.courtyard_function or "lek_gront"
            zone.courtyard_program = zone.courtyard_program or "lek, grønt, naturlek"


def pass2_typology_zoning(
    buildable_polygon,
    neighbor_summary: str,
    nb_polys: List[Dict[str, Any]],
    program: ProgramAllocation,
    max_floors: int,
    max_height_m: float,
    terrain: Optional[Dict[str, Any]] = None,
    target_bra_m2: float = 0.0,
    max_bya_pct: float = 35.0,
) -> List[TypologyZone]:
    zones = _ORIG_V2_PASS2_TYPOLOGY_ZONING(
        buildable_polygon=buildable_polygon,
        neighbor_summary=neighbor_summary,
        nb_polys=nb_polys,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        terrain=terrain,
        target_bra_m2=target_bra_m2,
        max_bya_pct=max_bya_pct,
    )
    if not zones:
        return zones

    if not _is_large_urban_masterplan_v3(buildable_polygon, program, target_bra_m2, max_bya_pct):
        return zones

    req_avg_floors = _required_avg_floors_v3(target_bra_m2, buildable_polygon, max_bya_pct)
    total_program = max(program.total_bra, 1.0)
    multi_zone = len(zones) >= 2

    for zone in zones:
        share = float(zone.target_bra or 0.0) / total_program
        if multi_zone:
            _promote_zone_typology_v3(zone, share, max_floors, req_avg_floors)
        else:
            # Én stor fallback-sone må forbli kapasitetssterk; ikke gjør hele tomta til ett gårdsrom.
            if zone.typology in ("Rekke", "Tun"):
                zone.typology = "Lamell"
                zone.rationale = (zone.rationale + " | v3: promotert til kapasitetssterk hovedtypologi i single-zone fallback").strip()
            zone.floors_min = max(zone.floors_min, 4 if req_avg_floors >= 3.2 else 3)
            zone.floors_max = max(zone.floors_max, min(max_floors, 5))

        if multi_zone and zone.typology in ("LamellSegmentert", "HalvåpenKarré", "Gårdsklynge"):
            zone.courtyard_polygon = _auto_courtyard_polygon_v3(zone) or zone.courtyard_polygon

    if multi_zone:
        ordered = _sort_zones_along_primary_axis_v3(zones, buildable_polygon)
        _assign_quarter_identity_v3(ordered, program)

        large_first = sorted(ordered, key=lambda z: float(z.polygon.area) if z.polygon else 0.0, reverse=True)
        for zone in large_first[:2]:
            if zone.courtyard_polygon is None or zone.courtyard_polygon.is_empty:
                zone.courtyard_polygon = _auto_courtyard_polygon_v3(zone)
                if zone.courtyard_polygon is not None and zone.typology == "Lamell":
                    zone.typology = "LamellSegmentert"
                    zone.floors_min = max(zone.floors_min, 4 if req_avg_floors >= 3.2 else 3)
                    zone.floors_max = max(zone.floors_max, min(max_floors, 5))

    return zones


def _evaluate_phase_standalone(phase: BuildingPhase, all_volumes: List[Volume],
                               all_phases: List[BuildingPhase],
                               outdoor_system: OutdoorSystem,
                               parking_phases: List[ParkingPhase]) -> Tuple[float, List[str]]:
    score = 0.0
    issues = []
    phase_volumes = [v for v in all_volumes if v.volume_id in phase.volume_ids]

    total_oppganger = sum(v.oppganger for v in phase_volumes)
    if total_oppganger >= len(phase_volumes):
        score += 30
    else:
        score += 15
        issues.append("Antall oppganger per volum er for lavt")

    local_outdoor = float(getattr(phase, "local_outdoor_m2", 0.0) or 0.0)
    shared_credit = float(getattr(phase, "shared_outdoor_credit_m2", 0.0) or 0.0)
    total_credit = local_outdoor + shared_credit
    zone_count = len(phase.standalone_outdoor_zone_ids or [])

    full_req = max(900.0, float(phase.units_estimate) * 10.0)
    min_req = max(650.0, float(phase.units_estimate) * 7.0)
    local_req = max(250.0, float(phase.units_estimate) * 2.5)

    if total_credit >= full_req and local_outdoor >= local_req and zone_count >= 1:
        score += 25
    elif total_credit >= min_req and zone_count >= 1:
        score += 14
        if local_outdoor < local_req:
            issues.append(
                f"Lokalt uterom ({local_outdoor:.0f} m²) er svakt; trinnet er avhengig av delt hoveduterom ({shared_credit:.0f} m² kreditert)"
            )
        else:
            issues.append(
                f"Standalone-uterom ({total_credit:.0f} m²) er på minimumssiden for {phase.units_estimate} boliger"
            )
    else:
        score += 3
        issues.append(
            f"Standalone-uterom ({total_credit:.0f} m² kreditert; lokalt {local_outdoor:.0f} m²) er for lite for {phase.units_estimate} boliger"
        )
    if zone_count == 0:
        issues.append("Ingen dedikerte uteromssoner er koblet til byggetrinnet")

    if phase.parking_served_by:
        required_p = [p for p in parking_phases if p.phase_number in phase.parking_served_by]
        if any(p.must_complete_before_building_phase is None or p.must_complete_before_building_phase <= phase.phase_number for p in required_p):
            score += 20
        else:
            score += 10
            issues.append("P-fase ferdigstilles etter byggefasens innflytting")
    else:
        issues.append("Ingen parkering tilknyttet")

    if phase.neighboring_construction_risk < 0.3:
        score += 15
    elif phase.neighboring_construction_risk < 0.6:
        score += 8
        issues.append("Moderat byggeplass-risiko fra senere faser")
    else:
        issues.append("Høy byggeplass-risiko fra senere faser")

    if "barnehage" in phase.programs_included and phase.phase_number <= max(1, len(all_phases) // 2):
        score += 10
    elif not any(p in phase.programs_included for p in ["barnehage", "naering"]):
        score += 10
    else:
        score += 5

    return score, issues


def pass5_outdoor_system(buildable_polygon, volumes: List[Volume],
                         building_phases: List[BuildingPhase],
                         program: ProgramAllocation, site_inputs: Dict[str, Any],
                         typology_zones: Optional[List[Any]] = None) -> OutdoorSystem:
    system = _ORIG_V2_PASS5_OUTDOOR_SYSTEM(
        buildable_polygon=buildable_polygon,
        volumes=volumes,
        building_phases=building_phases,
        program=program,
        site_inputs=site_inputs,
        typology_zones=typology_zones,
    )
    if not building_phases or not system.zones:
        return system

    for phase in building_phases:
        local_ground = 0.0
        shared_ground_credit = 0.0
        zone_ids = []
        for z in system.zones:
            if not z.on_ground or not z.is_felles:
                continue
            if phase.phase_number not in (z.serves_building_phases or []):
                continue
            zone_ids.append(z.zone_id)
            served = max(len(z.serves_building_phases or []), 1)
            if served == 1:
                local_ground += float(z.area_m2)
            else:
                shared_ground_credit += float(z.area_m2) / served

        phase.standalone_outdoor_zone_ids = zone_ids
        phase.local_outdoor_m2 = local_ground  # type: ignore[attr-defined]
        phase.shared_outdoor_credit_m2 = shared_ground_credit  # type: ignore[attr-defined]
        phase.standalone_outdoor_m2 = local_ground + shared_ground_credit
        phase.standalone_outdoor_has_sun = True

    return system


def pass4_phasing(volumes: List[Volume], buildable_polygon,
                  phasing_config: PhasingConfig, target_phase_count: int,
                  program: ProgramAllocation, site_polygon) -> Tuple[List[BuildingPhase], List[ParkingPhase]]:
    actual_total_bra = sum(v.bra_m2 for v in volumes)
    target_total_bra = max(float(program.total_bra or 0.0), 1.0)
    target_attainment = actual_total_bra / target_total_bra if target_total_bra > 0 else 1.0

    if phasing_config.phasing_mode == "auto" and target_total_bra >= 15000.0:
        min_k_hard = max(1, math.ceil(actual_total_bra / phasing_config.MAX_PHASE_BRA)) if actual_total_bra > 0 else 1
        max_k_hard = max(1, int(actual_total_bra / phasing_config.MIN_PHASE_BRA)) if actual_total_bra >= phasing_config.MIN_PHASE_BRA else 1
        desired_k = int(_clamp(round(target_total_bra / 4200.0), min_k_hard, max_k_hard))
        # På store tomter skal fasetallet i hovedsak styres av marked/produkt og mål-BRA,
        # ikke av midlertidige avvik i Pass 3-plasseringen.
        patched_cfg = PhasingConfig(
            phasing_mode="manual",
            manual_phase_count=desired_k,
            parking_mode=phasing_config.parking_mode,
            manual_parking_phase_count=phasing_config.manual_parking_phase_count,
            MIN_PHASE_BRA=phasing_config.MIN_PHASE_BRA,
            MAX_PHASE_BRA=phasing_config.MAX_PHASE_BRA,
            TARGET_PHASE_BRA_LOW=phasing_config.TARGET_PHASE_BRA_LOW,
            TARGET_PHASE_BRA_HIGH=phasing_config.TARGET_PHASE_BRA_HIGH,
            SINGLE_PHASE_MAX_BRA=phasing_config.SINGLE_PHASE_MAX_BRA,
        )
        return _ORIG_V2_PASS4_PHASING(volumes, buildable_polygon, patched_cfg, desired_k, program, site_polygon)

    return _ORIG_V2_PASS4_PHASING(volumes, buildable_polygon, phasing_config, target_phase_count, program, site_polygon)


def plan_masterplan(*args, **kwargs) -> Masterplan:
    site_inputs = kwargs.get("site_inputs") or {}
    _set_pass2_efficiency_ratio_v3(site_inputs)
    mp = _ORIG_V2_PLAN_MASTERPLAN(*args, **kwargs)
    try:
        mp.source = (getattr(mp, "source", "Builtly Masterplan") + " + v3 large-site/quarter tuning").strip()
        if isinstance(getattr(mp, "diag_info", None), dict):
            buildable_polygon = kwargs.get("buildable_polygon") if "buildable_polygon" in kwargs else (args[1] if len(args) > 1 else None)
            req_avg = _required_avg_floors_v3(
                float(kwargs.get("target_bra_m2", 0.0) or 0.0),
                buildable_polygon,
                float(kwargs.get("max_bya_pct", 35.0) or 35.0),
            )
            mp.diag_info["v3"] = (
                f"v3: efficiency={_CURRENT_PASS2_EFFICIENCY_RATIO_V3:.2f}, "
                f"required_avg_floors={req_avg:.2f}, shared-outdoor-credit aktiv"
            )
    except Exception:
        pass
    return mp


# ====================================================================
# V4 PATCHES — strukturert delfelt-logikk, diagonal-først og konsistent
# boligantall fra faktisk BRA og valgt leilighetsstørrelse.
# ====================================================================

_ORIG_V4_PLAN_MASTERPLAN = plan_masterplan
_ORIG_V4_PASS2_TYPOLOGY_ZONING = pass2_typology_zoning
_ORIG_V4_PASS4_PHASING = pass4_phasing
_ORIG_V4_PASS5_OUTDOOR_SYSTEM = pass5_outdoor_system

_CURRENT_AVG_UNIT_BRA_V4 = 55.0
_CURRENT_DIAGONAL_ZONE_V4 = None
_CURRENT_DIAGONAL_LINE_V4 = None
_CURRENT_MAJOR_AXIS_ANGLE_V4 = 0.0
_CURRENT_MAJOR_AXIS_ORIGIN_V4 = (0.0, 0.0)
_CURRENT_STRUCTURED_ZONE_IDS_V4 = []


def _get_avg_unit_bra_v4(site_inputs: Optional[Dict[str, Any]] = None) -> float:
    site_inputs = site_inputs or {}
    try:
        value = float(site_inputs.get("avg_unit_bra", site_inputs.get("avg_unit_m2", 55.0)) or 55.0)
    except Exception:
        value = 55.0
    return _clamp(value, 35.0, 120.0)


def _set_runtime_context_v4(buildable_polygon, site_inputs: Optional[Dict[str, Any]] = None) -> None:
    global _CURRENT_AVG_UNIT_BRA_V4, _CURRENT_DIAGONAL_ZONE_V4, _CURRENT_DIAGONAL_LINE_V4
    global _CURRENT_MAJOR_AXIS_ANGLE_V4, _CURRENT_MAJOR_AXIS_ORIGIN_V4, _CURRENT_STRUCTURED_ZONE_IDS_V4

    _CURRENT_AVG_UNIT_BRA_V4 = _get_avg_unit_bra_v4(site_inputs)
    _CURRENT_DIAGONAL_ZONE_V4 = None
    _CURRENT_DIAGONAL_LINE_V4 = None
    _CURRENT_STRUCTURED_ZONE_IDS_V4 = []

    if buildable_polygon is None or getattr(buildable_polygon, "is_empty", True):
        _CURRENT_MAJOR_AXIS_ANGLE_V4 = 0.0
        _CURRENT_MAJOR_AXIS_ORIGIN_V4 = (0.0, 0.0)
        return

    try:
        rect = buildable_polygon.minimum_rotated_rectangle
        coords = list(rect.exterior.coords)[:4]
        edges = []
        for i in range(4):
            x1, y1 = coords[i]
            x2, y2 = coords[(i + 1) % 4]
            length = math.hypot(x2 - x1, y2 - y1)
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            edges.append((length, angle))
        edges.sort(key=lambda item: item[0], reverse=True)
        _CURRENT_MAJOR_AXIS_ANGLE_V4 = float(edges[0][1])
    except Exception:
        _CURRENT_MAJOR_AXIS_ANGLE_V4 = 0.0

    c = buildable_polygon.centroid
    _CURRENT_MAJOR_AXIS_ORIGIN_V4 = (float(c.x), float(c.y))


def _rotate_to_local_v4(geom):
    if geom is None:
        return None
    ox, oy = _CURRENT_MAJOR_AXIS_ORIGIN_V4
    return affinity.rotate(geom, -_CURRENT_MAJOR_AXIS_ANGLE_V4, origin=(ox, oy))


def _rotate_to_world_v4(geom):
    if geom is None:
        return None
    ox, oy = _CURRENT_MAJOR_AXIS_ORIGIN_V4
    return affinity.rotate(geom, _CURRENT_MAJOR_AXIS_ANGLE_V4, origin=(ox, oy))


def _largest_polygon_v4(geom, min_area: float = 250.0):
    if geom is None or getattr(geom, "is_empty", True):
        return None
    if isinstance(geom, Polygon):
        return geom.buffer(0) if geom.area >= min_area else None
    if isinstance(geom, MultiPolygon):
        parts = [g.buffer(0) for g in geom.geoms if not g.is_empty and g.area >= min_area]
        if not parts:
            return None
        return max(parts, key=lambda g: g.area)
    try:
        geom = geom.buffer(0)
        if isinstance(geom, Polygon):
            return geom if geom.area >= min_area else None
        if isinstance(geom, MultiPolygon):
            parts = [g for g in geom.geoms if g.area >= min_area]
            return max(parts, key=lambda g: g.area) if parts else None
    except Exception:
        return None
    return None


def _structured_fields_applicable_v4(buildable_polygon, program: ProgramAllocation,
                                     target_bra_m2: float, max_bya_pct: float,
                                     max_floors: int) -> bool:
    if buildable_polygon is None or getattr(buildable_polygon, "is_empty", True):
        return False
    buildable_area = float(buildable_polygon.area)
    req_avg = _required_avg_floors_v3(target_bra_m2, buildable_polygon, max_bya_pct)
    return (
        buildable_area >= 16000.0
        and max_floors >= 4
        and float(program.total_bra or 0.0) >= 15000.0
        and req_avg >= 2.6
    )


def _make_courtyard_polygon_v4(local_zone_poly, openness: str = "S"):
    if local_zone_poly is None or getattr(local_zone_poly, "is_empty", True):
        return None
    bounds = local_zone_poly.bounds
    zw = bounds[2] - bounds[0]
    zh = bounds[3] - bounds[1]
    if zw < 32 or zh < 32:
        return None
    inset_x = max(8.0, zw * 0.18)
    inset_y = max(8.0, zh * 0.18)
    inner = shapely_box(bounds[0] + inset_x, bounds[1] + inset_y,
                        bounds[2] - inset_x, bounds[3] - inset_y)
    shrunken = local_zone_poly.buffer(-6.0)
    if shrunken is None or getattr(shrunken, "is_empty", True):
        return None
    cp = inner.intersection(shrunken).buffer(0)
    cp = _largest_polygon_v4(cp, min_area=220.0)
    if cp is None:
        return None
    return cp


def _make_structured_field_zones_v4(buildable_polygon, program: ProgramAllocation,
                                    target_bra_m2: float, max_floors: int,
                                    max_height_m: float, max_bya_pct: float) -> List[TypologyZone]:
    global _CURRENT_DIAGONAL_ZONE_V4, _CURRENT_DIAGONAL_LINE_V4, _CURRENT_STRUCTURED_ZONE_IDS_V4

    local_poly = _rotate_to_local_v4(buildable_polygon)
    if local_poly is None or getattr(local_poly, "is_empty", True):
        return []

    xmin, ymin, xmax, ymax = local_poly.bounds
    w = xmax - xmin
    h = ymax - ymin
    if w < 80 or h < 80:
        return []

    req_avg = _required_avg_floors_v3(target_bra_m2, buildable_polygon, max_bya_pct)

    diag_line_local = LineString([
        (xmin + 0.08 * w, ymin + 0.15 * h),
        (xmax - 0.06 * w, ymax - 0.12 * h),
    ])
    diag_width = _clamp(min(w, h) * 0.09, 9.0, 14.0)
    diag_poly_local = diag_line_local.buffer(diag_width, cap_style=2, join_style=2).intersection(local_poly).buffer(0)

    west_strip_type = "Rekke" if req_avg < 3.9 else "LamellSegmentert"
    west_strip_floors = (2, 3) if west_strip_type == "Rekke" else (3, 4)

    envelopes = [
        {
            "zone_id": "QV4-F0",
            "name": "Vestkant",
            "typology": west_strip_type,
            "floors": west_strip_floors,
            "mult": 0.55,
            "bbox": shapely_box(xmin, ymin, xmin + 0.16 * w, ymax),
            "courtyard": False,
            "courtyard_name": "Vesttun",
            "courtyard_function": "felles_bolig",
            "courtyard_program": "forhager, lav vegetasjon, benker",
        },
        {
            "zone_id": "QV4-F1",
            "name": "Sørvest kvartal",
            "typology": "HalvåpenKarré",
            "floors": (4, min(max_floors, 5)),
            "mult": 0.95,
            "bbox": shapely_box(xmin + 0.16 * w, ymin, xmin + 0.54 * w, ymin + 0.44 * h),
            "courtyard": True,
            "courtyard_name": "Sørgård",
            "courtyard_function": "felles_bolig",
            "courtyard_program": "trær, lek, felles grill",
        },
        {
            "zone_id": "QV4-F2",
            "name": "Nordvest kvartal",
            "typology": "HalvåpenKarré",
            "floors": (4, min(max_floors, 5)),
            "mult": 1.00,
            "bbox": shapely_box(xmin + 0.16 * w, ymin + 0.42 * h, xmin + 0.56 * w, ymax),
            "courtyard": True,
            "courtyard_name": "Nabolagsgård",
            "courtyard_function": "felles_bolig",
            "courtyard_program": "trær, plantekasser, benker",
        },
        {
            "zone_id": "QV4-F3",
            "name": "Nordøst kvartal",
            "typology": "HalvåpenKarré",
            "floors": (5 if max_floors >= 5 else 4, min(max_floors, 6)),
            "mult": 1.18,
            "bbox": shapely_box(xmin + 0.48 * w, ymin + 0.42 * h, xmax, ymax),
            "courtyard": True,
            "courtyard_name": "Bypark-gård",
            "courtyard_function": "lek_gront",
            "courtyard_program": "grønt lek, møteplass, fontene",
        },
        {
            "zone_id": "QV4-F4",
            "name": "Sørøst felt",
            "typology": "LamellSegmentert",
            "floors": (4, min(max_floors, 5)),
            "mult": 1.02,
            "bbox": shapely_box(xmin + 0.42 * w, ymin, xmax, ymin + 0.60 * h),
            "courtyard": True,
            "courtyard_name": "Sørøst tun",
            "courtyard_function": "felles_bolig",
            "courtyard_program": "lek, grønt, sykkelparkering",
        },
    ]

    used_local = diag_poly_local.buffer(0.8)
    raw_zones = []
    for spec in envelopes:
        zone_local = local_poly.intersection(spec["bbox"]).difference(diag_poly_local.buffer(0.6)).difference(used_local).buffer(0)
        zone_local = _largest_polygon_v4(zone_local, min_area=320.0)
        if zone_local is None:
            continue
        used_local = unary_union([used_local, zone_local]).buffer(0)
        raw_zones.append((spec, zone_local))

    if len(raw_zones) < 3:
        return []

    weights = []
    for spec, zone_local in raw_zones:
        weights.append(max(1.0, float(zone_local.area)) * float(spec["mult"]))
    weight_sum = sum(weights) or 1.0

    zones: List[TypologyZone] = []
    _CURRENT_STRUCTURED_ZONE_IDS_V4 = []
    for (spec, zone_local), weight in zip(raw_zones, weights):
        zone_world = _rotate_to_world_v4(zone_local)
        courtyard_world = None
        if spec.get("courtyard"):
            courtyard_local = _make_courtyard_polygon_v4(zone_local)
            if courtyard_local is not None:
                courtyard_world = _rotate_to_world_v4(courtyard_local)
        target_bra = float(program.total_bra) * (weight / weight_sum)
        floors_min, floors_max = spec["floors"]
        zone = TypologyZone(
            zone_id=spec["zone_id"],
            typology=spec["typology"],
            polygon=zone_world,
            floors_min=int(max(2, floors_min)),
            floors_max=int(max(floors_min, floors_max)),
            target_bra=target_bra,
            rationale=f"v4 strukturert delfelt: {spec['name']} rundt diagonal og gårdsrom",
        )
        zone.courtyard_polygon = courtyard_world
        zone.courtyard_name = spec.get("courtyard_name", "")
        zone.courtyard_function = spec.get("courtyard_function", "")
        zone.courtyard_program = spec.get("courtyard_program", "")
        zones.append(zone)
        _CURRENT_STRUCTURED_ZONE_IDS_V4.append(zone.zone_id)

    _CURRENT_DIAGONAL_ZONE_V4 = _rotate_to_world_v4(diag_poly_local)
    _CURRENT_DIAGONAL_LINE_V4 = _rotate_to_world_v4(diag_line_local)
    return zones


def _residential_bra_for_volume_v4(v: Volume) -> float:
    eff = float(getattr(v, "bra_efficiency_ratio", 0.85) or 0.85)
    if getattr(v, "program", "bolig") != "bolig":
        return 0.0
    residential_floors = float(getattr(v, "floors", 0) or 0)
    gf_prog = getattr(v, "ground_floor_program", None)
    if gf_prog and gf_prog != "bolig":
        residential_floors = max(0.0, residential_floors - 1.0)
    return float(getattr(v, "footprint_m2", 0.0) or 0.0) * residential_floors * eff


def _reestimate_units_v4(volumes: List[Volume], avg_unit_bra: float) -> None:
    avg_unit_bra = max(avg_unit_bra, 1.0)
    for v in volumes:
        res_bra = _residential_bra_for_volume_v4(v)
        if res_bra <= 0.0:
            v.units_estimate = 0
        else:
            v.units_estimate = max(1, int(round(res_bra / avg_unit_bra)))


def pass2_typology_zoning(
    buildable_polygon,
    neighbor_summary: str,
    nb_polys: List[Dict[str, Any]],
    program: ProgramAllocation,
    max_floors: int,
    max_height_m: float,
    terrain: Optional[Dict[str, Any]] = None,
    target_bra_m2: float = 0.0,
    max_bya_pct: float = 35.0,
) -> List[TypologyZone]:
    fallback = _ORIG_V4_PASS2_TYPOLOGY_ZONING(
        buildable_polygon=buildable_polygon,
        neighbor_summary=neighbor_summary,
        nb_polys=nb_polys,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        terrain=terrain,
        target_bra_m2=target_bra_m2,
        max_bya_pct=max_bya_pct,
    )
    if not _structured_fields_applicable_v4(buildable_polygon, program, target_bra_m2, max_bya_pct, max_floors):
        return fallback
    structured = _make_structured_field_zones_v4(
        buildable_polygon=buildable_polygon,
        program=program,
        target_bra_m2=target_bra_m2,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
    )
    if structured:
        return structured
    return fallback


def pass4_phasing(volumes: List[Volume], buildable_polygon,
                  phasing_config: PhasingConfig, target_phase_count: int,
                  program: ProgramAllocation, site_polygon) -> Tuple[List[BuildingPhase], List[ParkingPhase]]:
    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    building_phases, parking_phases = _ORIG_V4_PASS4_PHASING(
        volumes, buildable_polygon, phasing_config, target_phase_count, program, site_polygon
    )
    for phase in building_phases:
        phase_volumes = [v for v in volumes if v.volume_id in phase.volume_ids]
        phase.actual_bra = sum(v.bra_m2 for v in phase_volumes)
        phase.units_estimate = sum(v.units_estimate for v in phase_volumes)
    return building_phases, parking_phases


def pass5_outdoor_system(buildable_polygon, volumes: List[Volume],
                         building_phases: List[BuildingPhase],
                         program: ProgramAllocation, site_inputs: Dict[str, Any],
                         typology_zones: Optional[List[Any]] = None) -> OutdoorSystem:
    system = _ORIG_V4_PASS5_OUTDOOR_SYSTEM(
        buildable_polygon=buildable_polygon,
        volumes=volumes,
        building_phases=building_phases,
        program=program,
        site_inputs=site_inputs,
        typology_zones=typology_zones,
    )
    if _CURRENT_DIAGONAL_ZONE_V4 is None or getattr(_CURRENT_DIAGONAL_ZONE_V4, "is_empty", True):
        return system

    cleaned_zones = []
    for z in system.zones:
        if getattr(z, "zone_id", "") == "OD-diagonal":
            continue
        geom = getattr(z, "geometry", None)
        if geom is None or getattr(geom, "is_empty", True):
            continue
        if geom.intersects(_CURRENT_DIAGONAL_ZONE_V4) and float(geom.intersection(_CURRENT_DIAGONAL_ZONE_V4).area) > 0.65 * float(getattr(z, "area_m2", 0.0) or 0.0):
            continue
        cleaned_zones.append(z)

    cleaned_zones.insert(0, OutdoorZone(
        zone_id="OD-diagonal",
        kind="diagonal",
        geometry=_CURRENT_DIAGONAL_ZONE_V4,
        area_m2=float(_CURRENT_DIAGONAL_ZONE_V4.area),
        counts_toward_mua=True,
        is_felles=True,
        on_ground=True,
        serves_building_phases=[bp.phase_number for bp in building_phases],
        requires_sun_hours=4.0,
        notes="v4 strukturert diagonal: primært grønt/gangforløp og sosialt uterom",
    ))
    system.zones = cleaned_zones
    system.diagonal_linestring = _CURRENT_DIAGONAL_LINE_V4

    # Realloker standalone-uterom etter at diagonalen er overstyrt.
    for phase in building_phases:
        phase_zones = [
            z for z in system.zones
            if phase.phase_number in (z.serves_building_phases or []) and z.is_felles and z.on_ground
        ]
        phase.standalone_outdoor_zone_ids = [z.zone_id for z in phase_zones]
        phase.standalone_outdoor_m2 = sum(float(z.area_m2 or 0.0) for z in phase_zones)
        phase.standalone_outdoor_has_sun = True
    return system


def plan_masterplan(*args, **kwargs) -> Masterplan:
    site_inputs = kwargs.get("site_inputs") or {}
    buildable_polygon = kwargs.get("buildable_polygon") if "buildable_polygon" in kwargs else (args[1] if len(args) > 1 else None)
    _set_runtime_context_v4(buildable_polygon, site_inputs)
    mp = _ORIG_V4_PLAN_MASTERPLAN(*args, **kwargs)
    try:
        mp.source = (getattr(mp, "source", "Builtly Masterplan") + " + v4 structured-fields").strip()
        if isinstance(getattr(mp, "diag_info", None), dict):
            mp.diag_info["v4"] = (
                f"v4: strukturert delfelt-grep {'aktiv' if _CURRENT_STRUCTURED_ZONE_IDS_V4 else 'ikke aktiv'}, "
                f"avg_unit_bra={_CURRENT_AVG_UNIT_BRA_V4:.1f}, "
                f"diagonal={'ja' if _CURRENT_DIAGONAL_ZONE_V4 is not None else 'nei'}"
            )
    except Exception:
        pass
    return mp


# ====================================================================
# V4.1 PATCH — aggressiv strukturbevarende backfill for store felt.
# Legger inn flere korte lameller langs feltkanter før motoren gir opp BRA.
# ====================================================================

_ORIG_V41_PASS3_PLACE_VOLUMES = pass3_place_volumes


def _compact_bar_spec_v41(zone: TypologyZone) -> Tuple[float, float, int]:
    typ = getattr(zone, "typology", "Lamell")
    if typ == "Rekke":
        return 16.0, 8.5, max(2, min(zone.floors_max, 3))
    if typ == "LamellSegmentert":
        return 24.0, 11.5, max(zone.floors_min, min(zone.floors_max, 5))
    return 26.0, 12.5, max(zone.floors_min, min(zone.floors_max, 5))


def _local_to_world_point_v41(x: float, y: float) -> Tuple[float, float]:
    p = _rotate_to_world_v4(Point(float(x), float(y)))
    return float(p.x), float(p.y)


def _place_compact_bars_v41(zone: TypologyZone, remaining_poly, deficit_bta: float,
                            max_floors: int, max_height_m: float, floor_to_floor_m: float,
                            existing_polys: List[Any], volume_counter: int,
                            max_fp_remaining: float) -> Tuple[List[Volume], int]:
    vols: List[Volume] = []
    if remaining_poly is None or getattr(remaining_poly, "is_empty", True):
        return vols, volume_counter
    if max_fp_remaining <= 50.0 or deficit_bta <= 250.0:
        return vols, volume_counter

    local_poly = _rotate_to_local_v4(remaining_poly)
    if local_poly is None or getattr(local_poly, "is_empty", True):
        return vols, volume_counter

    bar_long, bar_depth, target_floors = _compact_bar_spec_v41(zone)
    dims = TYPOLOGY_DIMS.get("Lamell", TYPOLOGY_DIMS["Lamell"])
    ftf = float(dims.get("ftf", floor_to_floor_m) or floor_to_floor_m)
    target_floors = int(_clamp(target_floors, 2, max_floors))
    if target_floors * ftf > max_height_m:
        target_floors = max(1, int(max_height_m / max(ftf, 0.1)))

    bounds = local_poly.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]
    angle_local = 0.0 if bw >= bh else 90.0
    spacing = 8.0 if getattr(zone, "typology", "") != "Rekke" else 6.0
    max_bars = 10

    xs = []
    ys = []
    x = bounds[0] + bar_long / 2.0
    while x <= bounds[2] - bar_long / 2.0 + 0.5:
        xs.append(x)
        x += bar_long + spacing
    y = bounds[1] + bar_depth / 2.0
    while y <= bounds[3] - bar_depth / 2.0 + 0.5:
        ys.append(y)
        y += bar_depth + spacing

    if not xs or not ys:
        return vols, volume_counter

    target_fp = min(max_fp_remaining, deficit_bta / max(target_floors, 1))
    fp_added = 0.0
    bars_added = 0

    # Prioriter kantnære posisjoner først for ryddigere kvartalsstruktur.
    candidates = []
    for yy in ys:
        for xx in xs:
            edge_pref = min(xx - bounds[0], bounds[2] - xx, yy - bounds[1], bounds[3] - yy)
            candidates.append((edge_pref, xx, yy))
    candidates.sort(key=lambda item: item[0])

    for _, xx, yy in candidates:
        if bars_added >= max_bars or fp_added >= target_fp * 0.98:
            break

        local_bar = _make_building_polygon(xx, yy, bar_long, bar_depth, angle_local)
        if local_bar is None or getattr(local_bar, "is_empty", True):
            continue
        world_bar = _rotate_to_world_v4(local_bar)
        clipped = world_bar.intersection(remaining_poly).buffer(0)
        min_area = max(110.0, bar_long * bar_depth * 0.60)
        clipped = _largest_polygon_v4(clipped, min_area=min_area)
        if clipped is None:
            continue

        too_close = False
        for ep in existing_polys:
            if ep is not None and not getattr(ep, "is_empty", True) and clipped.distance(ep) < MIN_BUILDING_SPACING - 0.4:
                too_close = True
                break
        if too_close:
            continue

        volume_counter += 1
        cxw, cyw = _local_to_world_point_v41(xx, yy)
        world_angle = (_CURRENT_MAJOR_AXIS_ANGLE_V4 + angle_local) % 180.0
        vol_typ = "Rekke" if getattr(zone, "typology", "") == "Rekke" else "Lamell"
        vol = Volume(
            volume_id=f"V{volume_counter:02d}",
            name=f"{zone.zone_id} backfill {bars_added+1}",
            polygon=clipped,
            typology=vol_typ,
            floors=target_floors,
            height_m=round(target_floors * ftf, 1),
            width_m=round(bar_long, 1),
            depth_m=round(bar_depth, 1),
            angle_deg=round(world_angle, 1),
            cx=round(cxw, 1),
            cy=round(cyw, 1),
            footprint_m2=round(float(clipped.area), 1),
            zone_id=zone.zone_id,
            program="bolig",
            notes="v4.1 strukturbevarende backfill",
        )
        vols.append(vol)
        existing_polys.append(clipped)
        fp_added += float(vol.footprint_m2)
        bars_added += 1
    return vols, volume_counter


def pass3_place_volumes(zones: List[TypologyZone], program: ProgramAllocation,
                        max_floors: int, max_height_m: float, max_bya_pct: float,
                        floor_to_floor_m: float,
                        neighbors: Optional[List[Dict[str, Any]]],
                        site_polygon, buildable_polygon,
                        site_inputs: Dict[str, Any]) -> List[Volume]:
    volumes = _ORIG_V41_PASS3_PLACE_VOLUMES(
        zones=zones,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
        neighbors=neighbors,
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
    )
    structured_zones = [z for z in zones if str(getattr(z, "zone_id", "")).startswith("QV4-")]
    if not structured_zones or buildable_polygon is None or getattr(buildable_polygon, "is_empty", True):
        _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
        return volumes

    efficiency_ratio = _get_efficiency_ratio_v2(site_inputs)
    _apply_efficiency_ratio_to_volumes_v2(volumes, efficiency_ratio)
    max_fp_total = float(buildable_polygon.area) * (max_bya_pct / 100.0)
    fp_used = sum(float(v.footprint_m2 or 0.0) for v in volumes)
    zone_targets_bta = _zone_target_bta_map_v2(structured_zones, program, efficiency_ratio)

    existing_ids = []
    for v in volumes:
        try:
            if isinstance(v.volume_id, str) and v.volume_id.startswith("V"):
                existing_ids.append(int(v.volume_id[1:]))
        except Exception:
            pass
    volume_counter = max(existing_ids, default=0)

    zone_order = sorted(
        structured_zones,
        key=lambda z: zone_targets_bta.get(z.zone_id, 0.0) - sum(v.footprint_m2 * v.floors for v in volumes if v.zone_id == z.zone_id),
        reverse=True,
    )

    for zone in zone_order:
        if fp_used >= max_fp_total * 0.95:
            break
        zone_existing = [v.polygon for v in volumes if v.zone_id == zone.zone_id and v.polygon is not None]
        zone_bta = sum(v.footprint_m2 * v.floors for v in volumes if v.zone_id == zone.zone_id)
        deficit = zone_targets_bta.get(zone.zone_id, 0.0) - zone_bta
        if deficit <= 600.0:
            continue
        remaining = zone.polygon.buffer(0)
        if zone_existing:
            try:
                remaining = remaining.difference(unary_union(zone_existing).buffer(MIN_BUILDING_SPACING / 2.0)).buffer(0)
            except Exception:
                remaining = remaining.buffer(0)
        extra, volume_counter = _place_compact_bars_v41(
            zone=zone,
            remaining_poly=remaining,
            deficit_bta=deficit,
            max_floors=max_floors,
            max_height_m=max_height_m,
            floor_to_floor_m=floor_to_floor_m,
            existing_polys=[v.polygon for v in volumes if v.polygon is not None],
            volume_counter=volume_counter,
            max_fp_remaining=max_fp_total - fp_used,
        )
        if extra:
            for ev in extra:
                ev.bra_efficiency_ratio = efficiency_ratio
            volumes.extend(extra)
            fp_used += sum(float(v.footprint_m2 or 0.0) for v in extra)

    _apply_efficiency_ratio_to_volumes_v2(volumes, efficiency_ratio)
    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    return volumes


# ====================================================================
# V4.2 PATCH — tettere og mer fleksibel backfill rundt gårdsrom.
# ====================================================================

_ORIG_V42_PASS3_PLACE_VOLUMES = pass3_place_volumes


def _compact_bar_spec_v42(zone: TypologyZone) -> Tuple[float, float, int]:
    typ = getattr(zone, "typology", "Lamell")
    if typ == "Rekke":
        return 14.0, 8.0, max(2, min(zone.floors_max, 3))
    if typ == "LamellSegmentert":
        return 20.0, 10.5, max(zone.floors_min, min(zone.floors_max, 5))
    return 20.0, 10.5, max(zone.floors_min, min(zone.floors_max, 5))


def _place_compact_bars_v42(zone: TypologyZone, remaining_poly, deficit_bta: float,
                            max_floors: int, max_height_m: float, floor_to_floor_m: float,
                            existing_polys: List[Any], volume_counter: int,
                            max_fp_remaining: float) -> Tuple[List[Volume], int]:
    vols: List[Volume] = []
    if remaining_poly is None or getattr(remaining_poly, "is_empty", True):
        return vols, volume_counter
    if max_fp_remaining <= 50.0 or deficit_bta <= 250.0:
        return vols, volume_counter

    local_poly = _rotate_to_local_v4(remaining_poly)
    if local_poly is None or getattr(local_poly, "is_empty", True):
        return vols, volume_counter

    bar_long, bar_depth, target_floors = _compact_bar_spec_v42(zone)
    dims = TYPOLOGY_DIMS.get("Lamell", TYPOLOGY_DIMS["Lamell"])
    ftf = float(dims.get("ftf", floor_to_floor_m) or floor_to_floor_m)
    target_floors = int(_clamp(target_floors, 2, max_floors))
    if target_floors * ftf > max_height_m:
        target_floors = max(1, int(max_height_m / max(ftf, 0.1)))

    bounds = local_poly.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]
    spacing = 6.5 if getattr(zone, "typology", "") != "Rekke" else 5.5
    max_bars = 24
    target_fp = min(max_fp_remaining, deficit_bta / max(target_floors, 1))
    fp_added = 0.0

    orientations = [0.0, 90.0] if bw > 26 and bh > 26 else ([0.0] if bw >= bh else [90.0])
    candidates = []
    for angle_local in orientations:
        long_dim = bar_long if angle_local == 0.0 else bar_depth
        short_dim = bar_depth if angle_local == 0.0 else bar_long
        x_starts = [bounds[0] + long_dim / 2.0, bounds[0] + long_dim / 2.0 + (long_dim + spacing) / 2.0]
        y_starts = [bounds[1] + short_dim / 2.0, bounds[1] + short_dim / 2.0 + (short_dim + spacing) / 2.0]
        for x0 in x_starts:
            x = x0
            while x <= bounds[2] - long_dim / 2.0 + 0.5:
                for y0 in y_starts:
                    y = y0
                    while y <= bounds[3] - short_dim / 2.0 + 0.5:
                        edge_pref = min(x - bounds[0], bounds[2] - x, y - bounds[1], bounds[3] - y)
                        candidates.append((edge_pref, angle_local, x, y, long_dim, short_dim))
                        y += short_dim + spacing
                x += long_dim + spacing
    candidates.sort(key=lambda item: item[0])

    bars_added = 0
    used_local = []
    for _, angle_local, xx, yy, long_dim, short_dim in candidates:
        if bars_added >= max_bars or fp_added >= target_fp * 0.98:
            break
        local_bar = _make_building_polygon(xx, yy, long_dim, short_dim, angle_local)
        if local_bar is None or getattr(local_bar, "is_empty", True):
            continue
        if any(local_bar.distance(lb) < MIN_BUILDING_SPACING - 0.4 for lb in used_local):
            continue
        world_bar = _rotate_to_world_v4(local_bar)
        clipped = world_bar.intersection(remaining_poly).buffer(0)
        min_area = max(85.0, long_dim * short_dim * 0.45)
        clipped = _largest_polygon_v4(clipped, min_area=min_area)
        if clipped is None:
            continue
        too_close = False
        for ep in existing_polys:
            if ep is not None and not getattr(ep, "is_empty", True) and clipped.distance(ep) < MIN_BUILDING_SPACING - 0.4:
                too_close = True
                break
        if too_close:
            continue
        volume_counter += 1
        cxw, cyw = _local_to_world_point_v41(xx, yy)
        world_angle = (_CURRENT_MAJOR_AXIS_ANGLE_V4 + angle_local) % 180.0
        vol_typ = "Rekke" if getattr(zone, "typology", "") == "Rekke" else "Lamell"
        vol = Volume(
            volume_id=f"V{volume_counter:02d}",
            name=f"{zone.zone_id} backfill {bars_added+1}",
            polygon=clipped,
            typology=vol_typ,
            floors=target_floors,
            height_m=round(target_floors * ftf, 1),
            width_m=round(long_dim, 1),
            depth_m=round(short_dim, 1),
            angle_deg=round(world_angle, 1),
            cx=round(cxw, 1),
            cy=round(cyw, 1),
            footprint_m2=round(float(clipped.area), 1),
            zone_id=zone.zone_id,
            program="bolig",
            notes="v4.2 strukturbevarende backfill",
        )
        vols.append(vol)
        existing_polys.append(clipped)
        used_local.append(local_bar)
        fp_added += float(vol.footprint_m2 or 0.0)
        bars_added += 1
    return vols, volume_counter


def pass3_place_volumes(zones: List[TypologyZone], program: ProgramAllocation,
                        max_floors: int, max_height_m: float, max_bya_pct: float,
                        floor_to_floor_m: float,
                        neighbors: Optional[List[Dict[str, Any]]],
                        site_polygon, buildable_polygon,
                        site_inputs: Dict[str, Any]) -> List[Volume]:
    volumes = _ORIG_V42_PASS3_PLACE_VOLUMES(
        zones=zones,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
        neighbors=neighbors,
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
    )
    structured_zones = [z for z in zones if str(getattr(z, "zone_id", "")).startswith("QV4-")]
    if not structured_zones or buildable_polygon is None or getattr(buildable_polygon, "is_empty", True):
        _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
        return volumes

    efficiency_ratio = _get_efficiency_ratio_v2(site_inputs)
    _apply_efficiency_ratio_to_volumes_v2(volumes, efficiency_ratio)
    max_fp_total = float(buildable_polygon.area) * (max_bya_pct / 100.0)
    fp_used = sum(float(v.footprint_m2 or 0.0) for v in volumes)
    zone_targets_bta = _zone_target_bta_map_v2(structured_zones, program, efficiency_ratio)

    existing_ids = []
    for v in volumes:
        try:
            if isinstance(v.volume_id, str) and v.volume_id.startswith("V"):
                existing_ids.append(int(v.volume_id[1:]))
        except Exception:
            pass
    volume_counter = max(existing_ids, default=0)

    zone_order = sorted(
        structured_zones,
        key=lambda z: zone_targets_bta.get(z.zone_id, 0.0) - sum(v.footprint_m2 * v.floors for v in volumes if v.zone_id == z.zone_id),
        reverse=True,
    )

    for zone in zone_order:
        if fp_used >= max_fp_total * 0.98:
            break
        zone_existing = [v.polygon for v in volumes if v.zone_id == zone.zone_id and v.polygon is not None]
        zone_bta = sum(v.footprint_m2 * v.floors for v in volumes if v.zone_id == zone.zone_id)
        deficit = zone_targets_bta.get(zone.zone_id, 0.0) - zone_bta
        if deficit <= 350.0:
            continue
        remaining = zone.polygon.buffer(0)
        if zone_existing:
            try:
                remaining = remaining.difference(unary_union(zone_existing).buffer(MIN_BUILDING_SPACING / 2.0)).buffer(0)
            except Exception:
                remaining = remaining.buffer(0)
        extra, volume_counter = _place_compact_bars_v42(
            zone=zone,
            remaining_poly=remaining,
            deficit_bta=deficit,
            max_floors=max_floors,
            max_height_m=max_height_m,
            floor_to_floor_m=floor_to_floor_m,
            existing_polys=[v.polygon for v in volumes if v.polygon is not None],
            volume_counter=volume_counter,
            max_fp_remaining=max_fp_total - fp_used,
        )
        if extra:
            for ev in extra:
                ev.bra_efficiency_ratio = efficiency_ratio
            volumes.extend(extra)
            fp_used += sum(float(v.footprint_m2 or 0.0) for v in extra)

    _apply_efficiency_ratio_to_volumes_v2(volumes, efficiency_ratio)
    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    return volumes


# ====================================================================
# V4.3 PATCH — siste perimeter-fill for å bruke tilgjengelig BYA når målet
# fortsatt er langt unna etter feltvis backfill.
# ====================================================================

_ORIG_V43_PASS3_PLACE_VOLUMES = pass3_place_volumes


def pass3_place_volumes(zones: List[TypologyZone], program: ProgramAllocation,
                        max_floors: int, max_height_m: float, max_bya_pct: float,
                        floor_to_floor_m: float,
                        neighbors: Optional[List[Dict[str, Any]]],
                        site_polygon, buildable_polygon,
                        site_inputs: Dict[str, Any]) -> List[Volume]:
    volumes = _ORIG_V43_PASS3_PLACE_VOLUMES(
        zones=zones,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
        neighbors=neighbors,
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
    )
    structured_zones = [z for z in zones if str(getattr(z, "zone_id", "")).startswith("QV4-")]
    if not structured_zones or buildable_polygon is None or getattr(buildable_polygon, "is_empty", True):
        _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
        return volumes

    efficiency_ratio = _get_efficiency_ratio_v2(site_inputs)
    target_total_bta = float(program.total_bra) / max(efficiency_ratio, 1e-6)
    actual_total_bta = sum(v.footprint_m2 * v.floors for v in volumes)
    max_fp_total = float(buildable_polygon.area) * (max_bya_pct / 100.0)
    fp_used = sum(float(v.footprint_m2 or 0.0) for v in volumes)

    if actual_total_bta < target_total_bta * 0.88 and fp_used < max_fp_total * 0.95:
        existing_ids = []
        for v in volumes:
            try:
                if isinstance(v.volume_id, str) and v.volume_id.startswith("V"):
                    existing_ids.append(int(v.volume_id[1:]))
            except Exception:
                pass
        volume_counter = max(existing_ids, default=0)
        remaining = buildable_polygon.buffer(0)
        try:
            remaining = remaining.difference(unary_union([v.polygon for v in volumes if v.polygon is not None]).buffer(MIN_BUILDING_SPACING / 2.0)).buffer(0)
        except Exception:
            remaining = remaining.buffer(0)
        if _CURRENT_DIAGONAL_ZONE_V4 is not None and not getattr(_CURRENT_DIAGONAL_ZONE_V4, "is_empty", True):
            try:
                remaining = remaining.difference(_CURRENT_DIAGONAL_ZONE_V4.buffer(1.0)).buffer(0)
            except Exception:
                pass
        perimeter_zone = TypologyZone(
            zone_id="QV4-PERIM",
            typology="LamellSegmentert",
            polygon=remaining,
            floors_min=max(4, min(max_floors, 4)),
            floors_max=max(4, min(max_floors, 5)),
            target_bra=0.0,
            rationale="v4.3 perimeter-fill",
        )
        extra, volume_counter = _place_compact_bars_v42(
            zone=perimeter_zone,
            remaining_poly=remaining,
            deficit_bta=target_total_bta - actual_total_bta,
            max_floors=max_floors,
            max_height_m=max_height_m,
            floor_to_floor_m=floor_to_floor_m,
            existing_polys=[v.polygon for v in volumes if v.polygon is not None],
            volume_counter=volume_counter,
            max_fp_remaining=max_fp_total - fp_used,
        )
        if extra:
            for ev in extra:
                ev.bra_efficiency_ratio = efficiency_ratio
                ev.zone_id = "QV4-PERIM"
            volumes.extend(extra)

    _apply_efficiency_ratio_to_volumes_v2(volumes, efficiency_ratio)
    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    return volumes


# ====================================================================
# V4.4 PATCH — mikrolameller i siste perimeter-fill for å nå realistisk
# kapasitet uten å hoppe til tårn eller brutale bygningskropper.
# ====================================================================

_ORIG_V44_PASS3_PLACE_VOLUMES = pass3_place_volumes


def _place_microbars_v44(remaining_poly, deficit_bta: float,
                         max_height_m: float, floor_to_floor_m: float,
                         existing_polys: List[Any], volume_counter: int,
                         max_fp_remaining: float) -> Tuple[List[Volume], int]:
    vols: List[Volume] = []
    if remaining_poly is None or getattr(remaining_poly, "is_empty", True):
        return vols, volume_counter
    if max_fp_remaining <= 60.0 or deficit_bta <= 250.0:
        return vols, volume_counter

    local_poly = _rotate_to_local_v4(remaining_poly)
    if local_poly is None or getattr(local_poly, "is_empty", True):
        return vols, volume_counter

    bar_long = 16.0
    bar_depth = 9.0
    ftf = max(float(floor_to_floor_m or 3.2), 2.8)
    floors = max(3, min(5, int(max_height_m / max(ftf, 0.1))))
    spacing = 5.5
    target_fp = min(max_fp_remaining, deficit_bta / max(floors, 1))
    fp_added = 0.0

    bounds = local_poly.bounds
    candidates = []
    for angle_local in (0.0, 90.0):
        long_dim = bar_long if angle_local == 0.0 else bar_depth
        short_dim = bar_depth if angle_local == 0.0 else bar_long
        for x0 in (bounds[0] + long_dim / 2.0, bounds[0] + long_dim / 2.0 + 0.5 * (long_dim + spacing)):
            x = x0
            while x <= bounds[2] - long_dim / 2.0 + 0.5:
                for y0 in (bounds[1] + short_dim / 2.0, bounds[1] + short_dim / 2.0 + 0.5 * (short_dim + spacing)):
                    y = y0
                    while y <= bounds[3] - short_dim / 2.0 + 0.5:
                        edge_pref = min(x - bounds[0], bounds[2] - x, y - bounds[1], bounds[3] - y)
                        candidates.append((edge_pref, angle_local, x, y, long_dim, short_dim))
                        y += short_dim + spacing
                x += long_dim + spacing
    candidates.sort(key=lambda item: item[0])

    used_local = []
    for _, angle_local, xx, yy, long_dim, short_dim in candidates:
        if fp_added >= target_fp * 0.98:
            break
        local_bar = _make_building_polygon(xx, yy, long_dim, short_dim, angle_local)
        if local_bar is None or getattr(local_bar, "is_empty", True):
            continue
        if any(local_bar.distance(lb) < MIN_BUILDING_SPACING - 0.4 for lb in used_local):
            continue
        world_bar = _rotate_to_world_v4(local_bar)
        clipped = world_bar.intersection(remaining_poly).buffer(0)
        clipped = _largest_polygon_v4(clipped, min_area=55.0)
        if clipped is None:
            continue
        if any(ep is not None and not getattr(ep, "is_empty", True) and clipped.distance(ep) < MIN_BUILDING_SPACING - 0.4 for ep in existing_polys):
            continue
        volume_counter += 1
        cxw, cyw = _local_to_world_point_v41(xx, yy)
        world_angle = (_CURRENT_MAJOR_AXIS_ANGLE_V4 + angle_local) % 180.0
        vol = Volume(
            volume_id=f"V{volume_counter:02d}",
            name=f"QV4 perimeter {len(vols)+1}",
            polygon=clipped,
            typology="Lamell",
            floors=floors,
            height_m=round(floors * ftf, 1),
            width_m=round(long_dim, 1),
            depth_m=round(short_dim, 1),
            angle_deg=round(world_angle, 1),
            cx=round(cxw, 1),
            cy=round(cyw, 1),
            footprint_m2=round(float(clipped.area), 1),
            zone_id="QV4-PERIM",
            program="bolig",
            notes="v4.4 perimeter microbar",
        )
        vols.append(vol)
        existing_polys.append(clipped)
        used_local.append(local_bar)
        fp_added += float(vol.footprint_m2 or 0.0)
    return vols, volume_counter


def pass3_place_volumes(zones: List[TypologyZone], program: ProgramAllocation,
                        max_floors: int, max_height_m: float, max_bya_pct: float,
                        floor_to_floor_m: float,
                        neighbors: Optional[List[Dict[str, Any]]],
                        site_polygon, buildable_polygon,
                        site_inputs: Dict[str, Any]) -> List[Volume]:
    volumes = _ORIG_V44_PASS3_PLACE_VOLUMES(
        zones=zones,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
        neighbors=neighbors,
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
    )
    structured_zones = [z for z in zones if str(getattr(z, "zone_id", "")).startswith("QV4-")]
    if not structured_zones or buildable_polygon is None or getattr(buildable_polygon, "is_empty", True):
        _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
        return volumes

    efficiency_ratio = _get_efficiency_ratio_v2(site_inputs)
    target_total_bta = float(program.total_bra) / max(efficiency_ratio, 1e-6)
    actual_total_bta = sum(v.footprint_m2 * v.floors for v in volumes)
    max_fp_total = float(buildable_polygon.area) * (max_bya_pct / 100.0)
    fp_used = sum(float(v.footprint_m2 or 0.0) for v in volumes)

    if actual_total_bta < target_total_bta * 0.92 and fp_used < max_fp_total * 0.95:
        existing_ids = []
        for v in volumes:
            try:
                if isinstance(v.volume_id, str) and v.volume_id.startswith("V"):
                    existing_ids.append(int(v.volume_id[1:]))
            except Exception:
                pass
        volume_counter = max(existing_ids, default=0)
        remaining = buildable_polygon.buffer(0)
        try:
            remaining = remaining.difference(unary_union([v.polygon for v in volumes if v.polygon is not None]).buffer(MIN_BUILDING_SPACING / 2.0)).buffer(0)
        except Exception:
            remaining = remaining.buffer(0)
        if _CURRENT_DIAGONAL_ZONE_V4 is not None and not getattr(_CURRENT_DIAGONAL_ZONE_V4, "is_empty", True):
            try:
                remaining = remaining.difference(_CURRENT_DIAGONAL_ZONE_V4.buffer(1.0)).buffer(0)
            except Exception:
                pass
        extra, volume_counter = _place_microbars_v44(
            remaining_poly=remaining,
            deficit_bta=target_total_bta - actual_total_bta,
            max_height_m=max_height_m,
            floor_to_floor_m=floor_to_floor_m,
            existing_polys=[v.polygon for v in volumes if v.polygon is not None],
            volume_counter=volume_counter,
            max_fp_remaining=max_fp_total - fp_used,
        )
        if extra:
            for ev in extra:
                ev.bra_efficiency_ratio = efficiency_ratio
            volumes.extend(extra)

    _apply_efficiency_ratio_to_volumes_v2(volumes, efficiency_ratio)
    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    return volumes


# ====================================================================
# V4.5 PATCH — komponentvis microbar-fill med lokal orientering.
# ====================================================================

_ORIG_V45_PASS3_PLACE_VOLUMES = pass3_place_volumes


def _component_axis_v45(poly) -> Tuple[float, Tuple[float, float]]:
    c = poly.centroid
    origin = (float(c.x), float(c.y))
    try:
        rect = poly.minimum_rotated_rectangle
        coords = list(rect.exterior.coords)[:4]
        best_len = -1.0
        best_angle = 0.0
        for i in range(4):
            x1, y1 = coords[i]
            x2, y2 = coords[(i + 1) % 4]
            length = math.hypot(x2 - x1, y2 - y1)
            if length > best_len:
                best_len = length
                best_angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        return best_angle, origin
    except Exception:
        return 0.0, origin


def _place_microbars_v45(remaining_poly, deficit_bta: float,
                         max_height_m: float, floor_to_floor_m: float,
                         existing_polys: List[Any], volume_counter: int,
                         max_fp_remaining: float) -> Tuple[List[Volume], int]:
    vols: List[Volume] = []
    if remaining_poly is None or getattr(remaining_poly, "is_empty", True):
        return vols, volume_counter
    if max_fp_remaining <= 60.0 or deficit_bta <= 250.0:
        return vols, volume_counter

    components = list(remaining_poly.geoms) if isinstance(remaining_poly, MultiPolygon) else [remaining_poly]
    components = [c.buffer(0) for c in components if c is not None and not c.is_empty and c.area >= 140.0]
    components.sort(key=lambda g: g.area, reverse=True)
    if not components:
        return vols, volume_counter

    ftf = max(float(floor_to_floor_m or 3.2), 2.8)
    floors = max(3, min(5, int(max_height_m / max(ftf, 0.1))))
    bar_long = 14.0
    bar_depth = 8.5
    spacing = 4.5
    target_fp = min(max_fp_remaining, deficit_bta / max(floors, 1))
    fp_added = 0.0

    for comp in components:
        if fp_added >= target_fp * 0.98:
            break
        angle, origin = _component_axis_v45(comp)
        local_comp = affinity.rotate(comp, -angle, origin=origin)
        bounds = local_comp.bounds
        candidates = []
        for angle_local in (0.0, 90.0):
            long_dim = bar_long if angle_local == 0.0 else bar_depth
            short_dim = bar_depth if angle_local == 0.0 else bar_long
            for x0 in (bounds[0] + long_dim / 2.0, bounds[0] + long_dim / 2.0 + 0.5 * (long_dim + spacing)):
                x = x0
                while x <= bounds[2] - long_dim / 2.0 + 0.5:
                    for y0 in (bounds[1] + short_dim / 2.0, bounds[1] + short_dim / 2.0 + 0.5 * (short_dim + spacing)):
                        y = y0
                        while y <= bounds[3] - short_dim / 2.0 + 0.5:
                            edge_pref = min(x - bounds[0], bounds[2] - x, y - bounds[1], bounds[3] - y)
                            candidates.append((edge_pref, angle_local, x, y, long_dim, short_dim))
                            y += short_dim + spacing
                    x += long_dim + spacing
        candidates.sort(key=lambda item: item[0])
        used_local = []
        for _, angle_local, xx, yy, long_dim, short_dim in candidates:
            if fp_added >= target_fp * 0.98:
                break
            local_bar = _make_building_polygon(xx, yy, long_dim, short_dim, angle_local)
            if local_bar is None or getattr(local_bar, "is_empty", True):
                continue
            if any(local_bar.distance(lb) < MIN_BUILDING_SPACING - 0.4 for lb in used_local):
                continue
            world_bar = affinity.rotate(local_bar, angle, origin=origin)
            clipped = world_bar.intersection(comp).buffer(0)
            clipped = _largest_polygon_v4(clipped, min_area=45.0)
            if clipped is None:
                continue
            if any(ep is not None and not getattr(ep, "is_empty", True) and clipped.distance(ep) < MIN_BUILDING_SPACING - 0.4 for ep in existing_polys):
                continue
            volume_counter += 1
            c = clipped.centroid
            world_angle = (angle + angle_local) % 180.0
            vol = Volume(
                volume_id=f"V{volume_counter:02d}",
                name=f"QV4 microbar {len(vols)+1}",
                polygon=clipped,
                typology="Lamell",
                floors=floors,
                height_m=round(floors * ftf, 1),
                width_m=round(long_dim, 1),
                depth_m=round(short_dim, 1),
                angle_deg=round(world_angle, 1),
                cx=round(float(c.x), 1),
                cy=round(float(c.y), 1),
                footprint_m2=round(float(clipped.area), 1),
                zone_id="QV4-PERIM",
                program="bolig",
                notes="v4.5 perimeter microbar",
            )
            vols.append(vol)
            existing_polys.append(clipped)
            used_local.append(local_bar)
            fp_added += float(vol.footprint_m2 or 0.0)
    return vols, volume_counter


def pass3_place_volumes(zones: List[TypologyZone], program: ProgramAllocation,
                        max_floors: int, max_height_m: float, max_bya_pct: float,
                        floor_to_floor_m: float,
                        neighbors: Optional[List[Dict[str, Any]]],
                        site_polygon, buildable_polygon,
                        site_inputs: Dict[str, Any]) -> List[Volume]:
    volumes = _ORIG_V45_PASS3_PLACE_VOLUMES(
        zones=zones,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
        neighbors=neighbors,
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
    )
    structured_zones = [z for z in zones if str(getattr(z, "zone_id", "")).startswith("QV4-")]
    if not structured_zones or buildable_polygon is None or getattr(buildable_polygon, "is_empty", True):
        _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
        return volumes

    efficiency_ratio = _get_efficiency_ratio_v2(site_inputs)
    target_total_bta = float(program.total_bra) / max(efficiency_ratio, 1e-6)
    actual_total_bta = sum(v.footprint_m2 * v.floors for v in volumes)
    max_fp_total = float(buildable_polygon.area) * (max_bya_pct / 100.0)
    fp_used = sum(float(v.footprint_m2 or 0.0) for v in volumes)

    if actual_total_bta < target_total_bta * 0.95 and fp_used < max_fp_total * 0.98:
        existing_ids = []
        for v in volumes:
            try:
                if isinstance(v.volume_id, str) and v.volume_id.startswith("V"):
                    existing_ids.append(int(v.volume_id[1:]))
            except Exception:
                pass
        volume_counter = max(existing_ids, default=0)
        remaining = buildable_polygon.buffer(0)
        try:
            remaining = remaining.difference(unary_union([v.polygon for v in volumes if v.polygon is not None]).buffer(MIN_BUILDING_SPACING / 2.0)).buffer(0)
        except Exception:
            remaining = remaining.buffer(0)
        if _CURRENT_DIAGONAL_ZONE_V4 is not None and not getattr(_CURRENT_DIAGONAL_ZONE_V4, "is_empty", True):
            try:
                remaining = remaining.difference(_CURRENT_DIAGONAL_ZONE_V4.buffer(1.0)).buffer(0)
            except Exception:
                pass
        extra, volume_counter = _place_microbars_v45(
            remaining_poly=remaining,
            deficit_bta=target_total_bta - actual_total_bta,
            max_height_m=max_height_m,
            floor_to_floor_m=floor_to_floor_m,
            existing_polys=[v.polygon for v in volumes if v.polygon is not None],
            volume_counter=volume_counter,
            max_fp_remaining=max_fp_total - fp_used,
        )
        if extra:
            for ev in extra:
                ev.bra_efficiency_ratio = efficiency_ratio
            volumes.extend(extra)

    _apply_efficiency_ratio_to_volumes_v2(volumes, efficiency_ratio)
    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    return volumes


# ====================================================================
# V5 PATCHES — delfelt som primær struktur, fasegruppe per delfelt,
# og tydeligere feltvis arkitektur for store tomter.
# ====================================================================
from copy import deepcopy
from masterplan_types import DevelopmentField

_ORIG_V5_PASS2_TYPOLOGY_ZONING = pass2_typology_zoning
_ORIG_V5_PASS3_PLACE_VOLUMES = pass3_place_volumes
_ORIG_V5_PASS4_PHASING = pass4_phasing
_ORIG_V5_PASS5_OUTDOOR_SYSTEM = pass5_outdoor_system
_ORIG_V5_PLAN_MASTERPLAN = plan_masterplan
_ORIG_V5_PASS6_VALIDATE = pass6_validate

_CURRENT_DEVELOPMENT_FIELDS_V5: List[DevelopmentField] = []
_CURRENT_FIELD_ZONE_MAP_V5: Dict[str, DevelopmentField] = {}
_CURRENT_FIELD_DIAG_TEXT_V5: str = ""

_COMPASS_TO_VEC_V5 = {
    "N": (0.0, 1.0), "NØ": (0.707, 0.707), "Ø": (1.0, 0.0), "SØ": (0.707, -0.707),
    "S": (0.0, -1.0), "SV": (-0.707, -0.707), "V": (-1.0, 0.0), "NV": (-0.707, 0.707),
}


def _rotate_vector_to_local_v5(dx: float, dy: float) -> Tuple[float, float]:
    ang = math.radians(-float(_CURRENT_MAJOR_AXIS_ANGLE_V4 or 0.0))
    return (
        dx * math.cos(ang) - dy * math.sin(ang),
        dx * math.sin(ang) + dy * math.cos(ang),
    )



def _relative_side_name_v5(poly, site_poly) -> str:
    try:
        sc = site_poly.centroid
        c = poly.centroid
        dx = float(c.x - sc.x)
        dy = float(c.y - sc.y)
    except Exception:
        return "midt"
    if abs(dx) > abs(dy) * 1.35:
        return "øst" if dx > 0 else "vest"
    if abs(dy) > abs(dx) * 1.35:
        return "nord" if dy > 0 else "sør"
    if dx >= 0 and dy >= 0:
        return "nordøst"
    if dx >= 0 and dy < 0:
        return "sørøst"
    if dx < 0 and dy >= 0:
        return "nordvest"
    return "sørvest"



def _contextual_field_name_v5(context: str, poly, site_poly) -> str:
    side = _relative_side_name_v5(poly, site_poly)
    if context == "sensitive_edge":
        return f"Småhuskant {side}"
    if context == "urban_edge":
        return f"Bykant {side}"
    if context == "green_edge":
        return f"Parkkant {side}"
    if context == "barnehage_edge":
        return f"Rolig {side}"
    if context == "active_diagonal":
        return f"Diagonalrom {side}"
    if context == "mixed_edge":
        return f"Koblingsfelt {side}"
    return f"Nabolagstun {side}"



def _dominant_smallhouse_compass_v5(nb_polys: List[Dict[str, Any]]) -> Optional[str]:
    smallhouse_dist = _classify_smallhouse_proximity(nb_polys)
    if not smallhouse_dist:
        return None
    best_compass, best_dist = min(smallhouse_dist.items(), key=lambda item: item[1])
    if best_dist > 85.0:
        return None
    return best_compass



def _dominant_urban_compass_v5(nb_polys: List[Dict[str, Any]]) -> Optional[str]:
    if not nb_polys:
        return None
    scores: Dict[str, float] = {}
    for item in nb_polys:
        compass = str(item.get("compass", ""))
        if not compass:
            continue
        dist = max(float(item.get("dist", 30.0) or 30.0), 5.0)
        height = float(item.get("height_m", 9.0) or 9.0)
        scores[compass] = scores.get(compass, 0.0) + max(height - 8.0, 0.0) / dist
    if not scores:
        return None
    return max(scores.items(), key=lambda item: item[1])[0]



def _compass_to_local_side_v5(compass: Optional[str]) -> str:
    if not compass:
        return "west"
    dx, dy = _COMPASS_TO_VEC_V5.get(compass, (-1.0, 0.0))
    lx, ly = _rotate_vector_to_local_v5(dx, dy)
    if abs(lx) >= abs(ly):
        return "east" if lx > 0 else "west"
    return "north" if ly > 0 else "south"



def _recommended_field_count_v5(buildable_polygon, target_bra_m2: float,
                                 max_bya_pct: float, max_floors: int) -> int:
    area = float(getattr(buildable_polygon, "area", 0.0) or 0.0)
    if area <= 0:
        return 1
    bounds = getattr(buildable_polygon, "bounds", (0.0, 0.0, 0.0, 0.0))
    bw = float(bounds[2] - bounds[0])
    bh = float(bounds[3] - bounds[1])
    aspect = max(bw, bh) / max(min(bw, bh), 1.0)
    req_avg = _required_avg_floors_v3(target_bra_m2, buildable_polygon, max_bya_pct)
    if area < 10000.0:
        return 1
    if area < 20000.0:
        return 2
    if area < 30000.0:
        return 4 if (aspect >= 1.45 or req_avg >= 3.6 or area >= 25000.0) else 3
    return 5 if (area >= 38000.0 or aspect >= 1.60 or req_avg >= 3.30 or target_bra_m2 >= 24000.0 or max_floors >= 7) else 4



def _local_side_bbox_v5(bounds, side: str, frac: float):
    xmin, ymin, xmax, ymax = bounds
    w = xmax - xmin
    h = ymax - ymin
    frac = _clamp(float(frac), 0.08, 0.40)
    if side == "west":
        return shapely_box(xmin, ymin, xmin + frac * w, ymax)
    if side == "east":
        return shapely_box(xmax - frac * w, ymin, xmax, ymax)
    if side == "south":
        return shapely_box(xmin, ymin, xmax, ymin + frac * h)
    return shapely_box(xmin, ymax - frac * h, xmax, ymax)



def _core_bounds_after_strip_v5(bounds, side: str, frac: float):
    xmin, ymin, xmax, ymax = bounds
    w = xmax - xmin
    h = ymax - ymin
    if side == "west":
        return (xmin + frac * w, ymin, xmax, ymax)
    if side == "east":
        return (xmin, ymin, xmax - frac * w, ymax)
    if side == "south":
        return (xmin, ymin + frac * h, xmax, ymax)
    return (xmin, ymin, xmax, ymax - frac * h)



def _field_context_defaults_v5(context: str, max_floors: int) -> Tuple[List[str], Tuple[int, int], str, str, int]:
    if context == "sensitive_edge":
        return (["Rekke", "LamellSegmentert"], (2, min(max_floors, 4)), "Forhage", "små trær, forhager, benker", 1)
    if context == "urban_edge":
        return (["HalvåpenKarré", "LamellSegmentert"], (4, min(max_floors, 6)), "Bytun", "sykkel, møteplass, aktive hjørner", 3)
    if context == "barnehage_edge":
        return (["Gårdsklynge", "LamellSegmentert"], (3, min(max_floors, 5)), "Barnehagetun", "lek, trær, sand, rolige soner", 1)
    if context == "green_edge":
        return (["LamellSegmentert", "Gårdsklynge"], (3, min(max_floors, 5)), "Parktun", "grønt lek, plantekasser, benker", 2)
    if context == "mixed_edge":
        return (["HalvåpenKarré", "LamellSegmentert"], (4, min(max_floors, 5)), "Koblingstun", "grønt opphold, sykkel, lek", 2)
    return (["HalvåpenKarré", "LamellSegmentert"], (4, min(max_floors, 5)), "Nabolagstun", "trær, lek, felles opphold", 2)



def _make_field_object_v5(field_id: str, poly_local, site_poly, context: str,
                          target_weight: float, max_floors: int) -> Optional[DevelopmentField]:
    poly_local = _largest_polygon_v4(poly_local, min_area=420.0)
    if poly_local is None:
        return None
    poly_world = _rotate_to_world_v4(poly_local)
    courtyard_local = None if context == "sensitive_edge" else _make_courtyard_polygon_v4(poly_local)
    courtyard_world = _rotate_to_world_v4(courtyard_local) if courtyard_local is not None else None
    typ_mix, floors_range, outdoor_name, outdoor_program, phase_hint = _field_context_defaults_v5(context, max_floors)
    name = _contextual_field_name_v5(context, poly_world, site_poly)
    return DevelopmentField(
        field_id=field_id,
        name=name,
        polygon=poly_world,
        context=context,  # type: ignore[arg-type]
        side_hint=_relative_side_name_v5(poly_world, site_poly),
        target_bra=max(0.0, target_weight),
        target_phase_count=1,
        typology_mix=typ_mix,
        preferred_floors_min=floors_range[0],
        preferred_floors_max=floors_range[1],
        primary_outdoor_name=outdoor_name,
        primary_outdoor_program=outdoor_program,
        courtyard_polygon=courtyard_world,
        phase_order_hint=phase_hint,
        notes=f"v5 delfelt, kontekst={context}",
    )



def _make_base_fields_v5(buildable_polygon, nb_polys: List[Dict[str, Any]],
                         program: ProgramAllocation, target_bra_m2: float,
                         max_floors: int, max_bya_pct: float) -> Tuple[List[DevelopmentField], Any, Any]:
    global _CURRENT_DIAGONAL_ZONE_V4, _CURRENT_DIAGONAL_LINE_V4

    local_poly = _rotate_to_local_v4(buildable_polygon)
    if local_poly is None or getattr(local_poly, "is_empty", True):
        return [], None, None
    bounds = local_poly.bounds
    xmin, ymin, xmax, ymax = bounds
    w = xmax - xmin
    h = ymax - ymin
    if w < 45.0 or h < 45.0:
        return [], None, None

    field_count = _recommended_field_count_v5(buildable_polygon, target_bra_m2, max_bya_pct, max_floors)
    smallhouse_compass = _dominant_smallhouse_compass_v5(nb_polys)
    urban_compass = _dominant_urban_compass_v5(nb_polys)
    sensitive_side = _compass_to_local_side_v5(smallhouse_compass) if smallhouse_compass else "west"
    urban_side = _compass_to_local_side_v5(urban_compass) if urban_compass else ("east" if sensitive_side != "east" else "west")

    diag_line_local = LineString([
        (xmin + 0.10 * w, ymin + 0.12 * h),
        (xmax - 0.08 * w, ymax - 0.10 * h),
    ])
    diag_width = _clamp(min(w, h) * 0.085, 8.0, 14.0)
    diag_poly_local = diag_line_local.buffer(diag_width, cap_style=2, join_style=2).intersection(local_poly).buffer(0)
    _CURRENT_DIAGONAL_ZONE_V4 = _rotate_to_world_v4(diag_poly_local)
    _CURRENT_DIAGONAL_LINE_V4 = _rotate_to_world_v4(diag_line_local)

    fields: List[Tuple[DevelopmentField, float]] = []
    field_specs: List[Tuple[str, Any, str, float]] = []

    if field_count == 1:
        field_specs.append(("DF1", local_poly, "mixed_edge", 1.0))
    else:
        strip_frac = 0.16 if field_count >= 4 else 0.14
        use_strip = field_count >= 3
        core_bounds = bounds
        if use_strip:
            strip_box = _local_side_bbox_v5(bounds, sensitive_side, strip_frac)
            strip_poly = local_poly.intersection(strip_box).difference(diag_poly_local.buffer(0.8)).buffer(0)
            field_specs.append(("DF1", strip_poly, "sensitive_edge", 0.62))
            core_bounds = _core_bounds_after_strip_v5(bounds, sensitive_side, strip_frac)

        cx0, cy0, cx1, cy1 = core_bounds
        cw = cx1 - cx0
        ch = cy1 - cy0
        if cw <= 20 or ch <= 20:
            field_specs.append((f"DF{len(field_specs)+1}", local_poly.difference(diag_poly_local.buffer(0.8)).buffer(0), "mixed_edge", 1.0))
        else:
            if field_count == 2:
                if sensitive_side in ("south", "north"):
                    west_box = shapely_box(cx0, cy0, cx0 + 0.48 * cw, cy1)
                    east_box = shapely_box(cx0 + 0.48 * cw, cy0, cx1, cy1)
                    field_specs.extend([
                        ("DF2", local_poly.intersection(west_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "barnehage_edge" if program.barnehage_bra > 0 else "calm_inner", 0.95),
                        ("DF3", local_poly.intersection(east_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "urban_edge", 1.15),
                    ])
                else:
                    south_box = shapely_box(cx0, cy0, cx1, cy0 + 0.48 * ch)
                    north_box = shapely_box(cx0, cy0 + 0.48 * ch, cx1, cy1)
                    field_specs.extend([
                        ("DF2", local_poly.intersection(south_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "barnehage_edge" if program.barnehage_bra > 0 else "calm_inner", 0.95),
                        ("DF3", local_poly.intersection(north_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "urban_edge", 1.15),
                    ])
            elif field_count == 3:
                south_box = shapely_box(cx0, cy0, cx0 + 0.55 * cw, cy0 + 0.48 * ch)
                north_box = shapely_box(cx0, cy0 + 0.44 * ch, cx0 + 0.58 * cw, cy1)
                east_box = shapely_box(cx0 + 0.46 * cw, cy0, cx1, cy1)
                field_specs.extend([
                    (f"DF{len(field_specs)+1}", local_poly.intersection(south_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "barnehage_edge" if program.barnehage_bra > 0 else "calm_inner", 0.92),
                    (f"DF{len(field_specs)+2}", local_poly.intersection(north_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "green_edge", 1.00),
                    (f"DF{len(field_specs)+3}", local_poly.intersection(east_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "urban_edge", 1.18),
                ])
            elif field_count == 4:
                south_box = shapely_box(cx0, cy0, cx0 + 0.54 * cw, cy0 + 0.50 * ch)
                north_box = shapely_box(cx0, cy0 + 0.46 * ch, cx0 + 0.58 * cw, cy1)
                east_box = shapely_box(cx0 + 0.46 * cw, cy0, cx1, cy1)
                field_specs.extend([
                    (f"DF{len(field_specs)+1}", local_poly.intersection(south_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "barnehage_edge" if program.barnehage_bra > 0 else "calm_inner", 0.90),
                    (f"DF{len(field_specs)+2}", local_poly.intersection(north_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "green_edge", 0.98),
                    (f"DF{len(field_specs)+3}", local_poly.intersection(east_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "urban_edge", 1.18),
                ])
            else:
                sw_box = shapely_box(cx0, cy0, cx0 + 0.50 * cw, cy0 + 0.48 * ch)
                nw_box = shapely_box(cx0, cy0 + 0.44 * ch, cx0 + 0.50 * cw, cy1)
                se_box = shapely_box(cx0 + 0.44 * cw, cy0, cx1, cy0 + 0.52 * ch)
                ne_box = shapely_box(cx0 + 0.42 * cw, cy0 + 0.46 * ch, cx1, cy1)
                field_specs.extend([
                    (f"DF{len(field_specs)+1}", local_poly.intersection(sw_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "barnehage_edge" if program.barnehage_bra > 0 else "calm_inner", 0.88),
                    (f"DF{len(field_specs)+2}", local_poly.intersection(nw_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "green_edge", 0.96),
                    (f"DF{len(field_specs)+3}", local_poly.intersection(se_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "mixed_edge", 1.08),
                    (f"DF{len(field_specs)+4}", local_poly.intersection(ne_box).difference(diag_poly_local.buffer(0.8)).buffer(0), "urban_edge", 1.18),
                ])

    for fid, poly_local, context, wt in field_specs:
        field_obj = _make_field_object_v5(fid, poly_local, buildable_polygon, context, wt, max_floors)
        if field_obj is not None:
            fields.append((field_obj, wt))

    if len(fields) < field_count:
        return [], _CURRENT_DIAGONAL_ZONE_V4, _CURRENT_DIAGONAL_LINE_V4

    weight_sum = sum(w for _, w in fields) or 1.0
    out_fields: List[DevelopmentField] = []
    total_target = float(program.total_bra or target_bra_m2 or 0.0)
    for field_obj, wt in fields:
        field_obj.target_bra = total_target * (wt / weight_sum)
        out_fields.append(field_obj)
    return out_fields, _CURRENT_DIAGONAL_ZONE_V4, _CURRENT_DIAGONAL_LINE_V4



def _carve_strip_and_main_v5(field_poly, side: str, frac: float) -> Tuple[Optional[Any], Optional[Any]]:
    local_poly = _rotate_to_local_v4(field_poly)
    if local_poly is None or getattr(local_poly, "is_empty", True):
        return None, None
    bounds = local_poly.bounds
    strip_box = _local_side_bbox_v5(bounds, side, frac)
    strip = _largest_polygon_v4(local_poly.intersection(strip_box).buffer(0), min_area=220.0)
    if strip is None:
        return None, _rotate_to_world_v4(local_poly)
    main = _largest_polygon_v4(local_poly.difference(strip.buffer(0.4)).buffer(0), min_area=320.0)
    return (_rotate_to_world_v4(strip) if strip is not None else None,
            _rotate_to_world_v4(main) if main is not None else None)



def _field_primary_secondary_v5(field_obj: DevelopmentField) -> Tuple[str, str, float]:
    mix = list(field_obj.typology_mix or [])
    if not mix:
        mix = ["HalvåpenKarré", "LamellSegmentert"]
    primary = mix[0]
    secondary = mix[1] if len(mix) > 1 else mix[0]
    share = 0.72
    if field_obj.context == "sensitive_edge":
        primary, secondary, share = ("LamellSegmentert" if field_obj.preferred_floors_max >= 4 else "Rekke"), "Rekke", 0.58
    elif field_obj.context == "urban_edge":
        primary, secondary, share = "HalvåpenKarré", "LamellSegmentert", 0.74
    elif field_obj.context == "barnehage_edge":
        primary, secondary, share = "Gårdsklynge", "LamellSegmentert", 0.68
    elif field_obj.context == "green_edge":
        primary, secondary, share = "HalvåpenKarré", "Gårdsklynge", 0.70
    elif field_obj.context == "mixed_edge":
        primary, secondary, share = "HalvåpenKarré", "LamellSegmentert", 0.70
    return primary, secondary, share



def _make_field_zones_v5(fields: List[DevelopmentField], max_floors: int) -> List[TypologyZone]:
    zones: List[TypologyZone] = []
    zone_counter = 0
    for field_obj in fields:
        side = "east"
        local_cent = _rotate_to_local_v4(field_obj.polygon).centroid
        site_local_cent = _rotate_to_local_v4(_CURRENT_DIAGONAL_ZONE_V4 if _CURRENT_DIAGONAL_ZONE_V4 is not None else field_obj.polygon).centroid
        # centroid relativt tomt-senter gir hovedside for sekundær stripe
        try:
            site_cent = _rotate_to_local_v4(field_obj.polygon.envelope).centroid
            dx = local_cent.x - site_cent.x
            dy = local_cent.y - site_cent.y
            if abs(dx) >= abs(dy):
                side = "east" if dx >= 0 else "west"
            else:
                side = "north" if dy >= 0 else "south"
        except Exception:
            pass

        primary_typ, secondary_typ, primary_share = _field_primary_secondary_v5(field_obj)
        strip_frac = 0.24 if field_obj.context != "sensitive_edge" else 0.38
        secondary_poly, primary_poly = _carve_strip_and_main_v5(field_obj.polygon, side, strip_frac)
        if primary_poly is None:
            primary_poly = field_obj.polygon
            secondary_poly = None

        zone_counter += 1
        z_main = TypologyZone(
            zone_id=f"QV4-{field_obj.field_id}-A",
            typology=primary_typ,  # type: ignore[arg-type]
            polygon=primary_poly,
            floors_min=max(2, field_obj.preferred_floors_min),
            floors_max=max(field_obj.preferred_floors_min, field_obj.preferred_floors_max),
            target_bra=field_obj.target_bra * primary_share,
            rationale=f"v5 delfeltstruktur: {field_obj.name} hovedstruktur",
            courtyard_polygon=field_obj.courtyard_polygon,
            courtyard_name=field_obj.primary_outdoor_name,
            courtyard_function="barnehage_ute" if field_obj.context == "barnehage_edge" and field_obj.courtyard_polygon is not None else "felles_bolig",
            courtyard_program=field_obj.primary_outdoor_program,
            field_id=field_obj.field_id,
            field_name=field_obj.name,
            zone_role="primary",
            target_share_within_field=primary_share,
        )
        zones.append(z_main)
        field_obj.zone_ids.append(z_main.zone_id)

        if secondary_poly is not None and getattr(secondary_poly, "area", 0.0) >= 240.0:
            zone_counter += 1
            sec_fmin = 2 if secondary_typ == "Rekke" else max(3, field_obj.preferred_floors_min)
            sec_fmax = min(field_obj.preferred_floors_max, 3 if secondary_typ == "Rekke" else field_obj.preferred_floors_max)
            z_sec = TypologyZone(
                zone_id=f"QV4-{field_obj.field_id}-B",
                typology=secondary_typ,  # type: ignore[arg-type]
                polygon=secondary_poly,
                floors_min=sec_fmin,
                floors_max=max(sec_fmin, sec_fmax),
                target_bra=field_obj.target_bra * (1.0 - primary_share),
                rationale=f"v5 delfeltstruktur: {field_obj.name} kant-/sekundærstruktur",
                field_id=field_obj.field_id,
                field_name=field_obj.name,
                zone_role="secondary",
                target_share_within_field=(1.0 - primary_share),
            )
            zones.append(z_sec)
            field_obj.zone_ids.append(z_sec.zone_id)
    return zones



def _phase_order_overrides_v5(fields: List[DevelopmentField]) -> None:
    # prioriter rolige/barnehagefelt tidlig, deretter småhuskant, så blandet/grønt,
    # og urbane kanter senere når diagonal og første uterom er etablert.
    for field_obj in fields:
        if field_obj.context == "barnehage_edge":
            field_obj.phase_order_hint = 1
        elif field_obj.context == "sensitive_edge":
            field_obj.phase_order_hint = min(field_obj.phase_order_hint, 2)
        elif field_obj.context in ("green_edge", "calm_inner"):
            field_obj.phase_order_hint = max(2, field_obj.phase_order_hint)
        elif field_obj.context == "urban_edge":
            field_obj.phase_order_hint = max(3, field_obj.phase_order_hint)



def pass2_typology_zoning(
    buildable_polygon,
    neighbor_summary: str,
    nb_polys: List[Dict[str, Any]],
    program: ProgramAllocation,
    max_floors: int,
    max_height_m: float,
    terrain: Optional[Dict[str, Any]] = None,
    target_bra_m2: float = 0.0,
    max_bya_pct: float = 35.0,
) -> List[TypologyZone]:
    global _CURRENT_DEVELOPMENT_FIELDS_V5, _CURRENT_FIELD_ZONE_MAP_V5, _CURRENT_FIELD_DIAG_TEXT_V5, _CURRENT_STRUCTURED_ZONE_IDS_V4

    _CURRENT_DEVELOPMENT_FIELDS_V5 = []
    _CURRENT_FIELD_ZONE_MAP_V5 = {}
    _CURRENT_FIELD_DIAG_TEXT_V5 = ""

    field_count = _recommended_field_count_v5(buildable_polygon, target_bra_m2, max_bya_pct, max_floors)
    fallback = _ORIG_V5_PASS2_TYPOLOGY_ZONING(
        buildable_polygon=buildable_polygon,
        neighbor_summary=neighbor_summary,
        nb_polys=nb_polys,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        terrain=terrain,
        target_bra_m2=target_bra_m2,
        max_bya_pct=max_bya_pct,
    )

    if field_count <= 1:
        return fallback

    fields, diag_zone, diag_line = _make_base_fields_v5(
        buildable_polygon=buildable_polygon,
        nb_polys=nb_polys,
        program=program,
        target_bra_m2=target_bra_m2,
        max_floors=max_floors,
        max_bya_pct=max_bya_pct,
    )
    if len(fields) < field_count:
        return fallback

    _phase_order_overrides_v5(fields)
    zones = _make_field_zones_v5(fields, max_floors)
    if not zones:
        return fallback

    _CURRENT_DEVELOPMENT_FIELDS_V5 = fields
    _CURRENT_FIELD_ZONE_MAP_V5 = {z.zone_id: next((f for f in fields if f.field_id == z.field_id), None) for z in zones}
    _CURRENT_STRUCTURED_ZONE_IDS_V4 = [z.zone_id for z in zones]
    _CURRENT_FIELD_DIAG_TEXT_V5 = (
        f"v5 delfelt: {len(fields)} felt, {len(zones)} understrukturer, "
        f"diagonal={'ja' if diag_zone is not None else 'nei'}"
    )
    return zones



def _assign_field_metadata_to_volumes_v5(volumes: List[Volume], zones: List[TypologyZone]) -> None:
    zone_map = {z.zone_id: z for z in zones}
    for v in volumes:
        z = zone_map.get(getattr(v, "zone_id", None))
        if z is None:
            continue
        v.field_id = z.field_id
        v.field_name = z.field_name



def _field_target_bta_map_v5(fields: List[DevelopmentField], program: ProgramAllocation, eff: float) -> Dict[str, float]:
    total_target_bta = float(program.total_bra or 0.0) / max(eff, 1e-6)
    total_field_target = sum(float(f.target_bra or 0.0) for f in fields) or 1.0
    return {f.field_id: total_target_bta * (float(f.target_bra or 0.0) / total_field_target) for f in fields}



def _field_fill_typology_v5(field_obj: DevelopmentField) -> str:
    if field_obj.context == "sensitive_edge":
        return "LamellSegmentert" if field_obj.preferred_floors_max >= 4 else "Rekke"
    mix = list(field_obj.typology_mix or [])
    return mix[0] if mix else "LamellSegmentert"



def pass3_place_volumes(zones: List[TypologyZone], program: ProgramAllocation,
                        max_floors: int, max_height_m: float, max_bya_pct: float,
                        floor_to_floor_m: float,
                        neighbors: Optional[List[Dict[str, Any]]],
                        site_polygon, buildable_polygon,
                        site_inputs: Dict[str, Any]) -> List[Volume]:
    volumes = _ORIG_V5_PASS3_PLACE_VOLUMES(
        zones=zones,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
        neighbors=neighbors,
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
    )
    _assign_field_metadata_to_volumes_v5(volumes, zones)

    if not _CURRENT_DEVELOPMENT_FIELDS_V5 or buildable_polygon is None or getattr(buildable_polygon, "is_empty", True):
        return volumes

    efficiency_ratio = _get_efficiency_ratio_v2(site_inputs)
    field_targets_bta = _field_target_bta_map_v5(_CURRENT_DEVELOPMENT_FIELDS_V5, program, efficiency_ratio)
    max_fp_total = float(buildable_polygon.area) * (max_bya_pct / 100.0)
    fp_used = sum(float(v.footprint_m2 or 0.0) for v in volumes)
    existing_ids = []
    for v in volumes:
        try:
            if isinstance(v.volume_id, str) and v.volume_id.startswith("V"):
                existing_ids.append(int(v.volume_id[1:]))
        except Exception:
            pass
    volume_counter = max(existing_ids, default=0)

    for field_obj in _CURRENT_DEVELOPMENT_FIELDS_V5:
        if fp_used >= max_fp_total * 0.985:
            break
        field_vols = [v for v in volumes if getattr(v, "field_id", None) == field_obj.field_id and v.polygon is not None]
        actual_bta = sum(float(v.footprint_m2 or 0.0) * float(v.floors or 0.0) for v in field_vols)
        deficit = field_targets_bta.get(field_obj.field_id, 0.0) - actual_bta
        if deficit <= 500.0:
            continue
        remaining = field_obj.polygon.buffer(0)
        if field_vols:
            try:
                remaining = remaining.difference(unary_union([v.polygon for v in field_vols]).buffer(MIN_BUILDING_SPACING / 2.0)).buffer(0)
            except Exception:
                remaining = remaining.buffer(0)
        pseudo_zone = TypologyZone(
            zone_id=f"QV4-{field_obj.field_id}-FILL",
            typology=_field_fill_typology_v5(field_obj),  # type: ignore[arg-type]
            polygon=field_obj.polygon,
            floors_min=max(2, field_obj.preferred_floors_min),
            floors_max=max(field_obj.preferred_floors_min, field_obj.preferred_floors_max),
            target_bra=0.0,
            rationale=f"v5 delfelt-backfill {field_obj.name}",
            field_id=field_obj.field_id,
            field_name=field_obj.name,
            zone_role="field_fill",
        )
        extra, volume_counter = _place_compact_bars_v42(
            zone=pseudo_zone,
            remaining_poly=remaining,
            deficit_bta=deficit,
            max_floors=max_floors,
            max_height_m=max_height_m,
            floor_to_floor_m=floor_to_floor_m,
            existing_polys=[v.polygon for v in volumes if v.polygon is not None],
            volume_counter=volume_counter,
            max_fp_remaining=max_fp_total - fp_used,
        )
        for ev in extra:
            ev.field_id = field_obj.field_id
            ev.field_name = field_obj.name
            ev.bra_efficiency_ratio = efficiency_ratio
        if extra:
            volumes.extend(extra)
            fp_used += sum(float(v.footprint_m2 or 0.0) for v in extra)

    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    return volumes



def _group_sorted_volumes_v5(volumes: List[Volume], n_groups: int) -> List[List[Volume]]:
    if n_groups <= 1 or len(volumes) <= 1:
        return [list(volumes)]
    xs = [v.cx for v in volumes]
    ys = [v.cy for v in volumes]
    sort_key = (lambda v: v.cx) if (max(xs) - min(xs) if xs else 0.0) >= (max(ys) - min(ys) if ys else 0.0) else (lambda v: v.cy)
    ordered = sorted(volumes, key=sort_key)
    total_bra = sum(v.bra_m2 for v in ordered)
    target = total_bra / max(n_groups, 1)
    groups: List[List[Volume]] = [[]]
    running = 0.0
    for v in ordered:
        if len(groups) < n_groups and groups[-1] and running + v.bra_m2 > target * 1.08:
            groups.append([])
            running = 0.0
        groups[-1].append(v)
        running += v.bra_m2
    while len(groups) < n_groups:
        groups.append([])
    groups = [g for g in groups if g]
    return groups



def _phase_groups_from_fields_v5(volumes: List[Volume], phasing_config: PhasingConfig) -> List[Tuple[List[str], List[Volume], List[str], int]]:
    field_order = {f.field_id: f.phase_order_hint for f in _CURRENT_DEVELOPMENT_FIELDS_V5}
    field_map = {f.field_id: f for f in _CURRENT_DEVELOPMENT_FIELDS_V5}
    groups: List[Tuple[List[str], List[Volume], List[str], int]] = []

    vols_by_field: Dict[str, List[Volume]] = {}
    for v in volumes:
        fid = getattr(v, "field_id", None) or getattr(v, "zone_id", None) or "Uten delfelt"
        vols_by_field.setdefault(fid, []).append(v)

    # Først: del opp store delfelt bare når de klart overstiger håndterbar størrelse.
    for fid, field_vols in vols_by_field.items():
        field_vols = [v for v in field_vols if v.polygon is not None]
        if not field_vols:
            continue
        total_bra = sum(v.bra_m2 for v in field_vols)
        field_obj = field_map.get(fid)
        field_name = field_obj.name if field_obj else fid
        target_subphases = 1
        split_threshold = max(8500.0, phasing_config.MAX_PHASE_BRA * 1.30)
        if total_bra > split_threshold and len(field_vols) >= 5:
            target_subphases = 2
        subgroups = _group_sorted_volumes_v5(field_vols, target_subphases)
        for idx, subgroup in enumerate(subgroups, start=1):
            sg_name = field_name if len(subgroups) == 1 else f"{field_name} del {idx}"
            groups.append(([fid], subgroup, [sg_name], field_order.get(fid, 2)))

    # Så: slå sammen for små grupper med nærmeste andre gruppe hvis mulig.
    changed = True
    while changed:
        changed = False
        for i, (fids, gvols, gnames, hint) in enumerate(list(groups)):
            gbra = sum(v.bra_m2 for v in gvols)
            if gbra >= phasing_config.MIN_PHASE_BRA * 0.85 or len(groups) <= 1:
                continue
            # Finn nærmeste gruppe i rommet.
            cx = sum(v.cx for v in gvols) / max(len(gvols), 1)
            cy = sum(v.cy for v in gvols) / max(len(gvols), 1)
            best_j = None
            best_dist = float("inf")
            for j, (_, oth_vols, _, _) in enumerate(groups):
                if i == j or not oth_vols:
                    continue
                ocx = sum(v.cx for v in oth_vols) / len(oth_vols)
                ocy = sum(v.cy for v in oth_vols) / len(oth_vols)
                dist = math.hypot(cx - ocx, cy - ocy)
                if dist < best_dist:
                    best_dist = dist
                    best_j = j
            if best_j is not None:
                ofids, ovols, onames, ohint = groups[best_j]
                groups[best_j] = (ofids + fids, ovols + gvols, onames + gnames, min(hint, ohint))
                del groups[i]
                changed = True
                break

    groups.sort(key=lambda item: (item[3], min(v.cy for v in item[1]) if item[1] else 0.0, min(v.cx for v in item[1]) if item[1] else 0.0))

    # Hold totalen nede på 3-5 trinn for store tomter; merge de minste gruppene hvis vi overskrider dette.
    while len(groups) > 5:
        sizes = [sum(v.bra_m2 for v in g[1]) for g in groups]
        i = min(range(len(groups)), key=lambda idx: sizes[idx])
        cx = sum(v.cx for v in groups[i][1]) / max(len(groups[i][1]), 1)
        cy = sum(v.cy for v in groups[i][1]) / max(len(groups[i][1]), 1)
        best_j = None
        best_dist = float("inf")
        for j in range(len(groups)):
            if i == j or not groups[j][1]:
                continue
            ocx = sum(v.cx for v in groups[j][1]) / len(groups[j][1])
            ocy = sum(v.cy for v in groups[j][1]) / len(groups[j][1])
            dist = math.hypot(cx - ocx, cy - ocy)
            if dist < best_dist:
                best_dist = dist
                best_j = j
        if best_j is None:
            break
        mfids, mvols, mnames, mhint = groups[i]
        ofids, ovols, onames, ohint = groups[best_j]
        groups[best_j] = (ofids + mfids, ovols + mvols, onames + mnames, min(mhint, ohint))
        del groups[i]
        groups.sort(key=lambda item: (item[3], min(v.cy for v in item[1]) if item[1] else 0.0, min(v.cx for v in item[1]) if item[1] else 0.0))

    return groups



def pass4_phasing(volumes: List[Volume], buildable_polygon,
                  phasing_config: PhasingConfig, target_phase_count: int,
                  program: ProgramAllocation, site_polygon) -> Tuple[List[BuildingPhase], List[ParkingPhase]]:
    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    if not _CURRENT_DEVELOPMENT_FIELDS_V5:
        return _ORIG_V5_PASS4_PHASING(volumes, buildable_polygon, phasing_config, target_phase_count, program, site_polygon)

    phase_groups = _phase_groups_from_fields_v5(volumes, phasing_config)
    phases: List[BuildingPhase] = []
    for idx, (field_ids, cluster_volumes, field_names, _) in enumerate(phase_groups, start=1):
        if not cluster_volumes:
            continue
        phase_bra = sum(v.bra_m2 for v in cluster_volumes)
        phase_units = sum(v.units_estimate for v in cluster_volumes)
        programs_in_phase: List[ProgramKind] = []
        for v in cluster_volumes:
            if v.program not in programs_in_phase:
                programs_in_phase.append(v.program)
            if v.ground_floor_program and v.ground_floor_program not in programs_in_phase:
                programs_in_phase.append(v.ground_floor_program)
        label = f"Trinn {idx} — {' + '.join(field_names)}"
        phase = BuildingPhase(
            phase_number=idx,
            label=label,
            volume_ids=[v.volume_id for v in cluster_volumes],
            target_bra=phase_bra,
            actual_bra=phase_bra,
            programs_included=programs_in_phase,
            units_estimate=phase_units,
            field_ids=list(dict.fromkeys(field_ids)),
            field_names=list(dict.fromkeys(field_names)),
        )
        for v in cluster_volumes:
            v.assigned_phase = idx
        phase_union = unary_union([v.polygon for v in cluster_volumes if v.polygon is not None])
        phase.construction_barrier_zone = phase_union.buffer(15.0) if phase_union is not None else None
        if idx > 1:
            phase.depends_on_phases = [idx - 1]
        phases.append(phase)

    parking_phases = _generate_parking_phases(
        phases, volumes, buildable_polygon, phasing_config, program,
    )
    for bphase in phases:
        bphase.parking_served_by = [
            pp.phase_number for pp in parking_phases
            if bphase.phase_number in pp.serves_building_phases
        ]
        if not bphase.parking_served_by and parking_phases:
            bphase.parking_served_by = [parking_phases[0].phase_number]
    return phases, parking_phases



def pass5_outdoor_system(buildable_polygon, volumes: List[Volume],
                         building_phases: List[BuildingPhase],
                         program: ProgramAllocation, site_inputs: Dict[str, Any],
                         typology_zones: Optional[List[Any]] = None) -> OutdoorSystem:
    system = _ORIG_V5_PASS5_OUTDOOR_SYSTEM(
        buildable_polygon=buildable_polygon,
        volumes=volumes,
        building_phases=building_phases,
        program=program,
        site_inputs=site_inputs,
        typology_zones=typology_zones,
    )
    if not typology_zones or not _CURRENT_DEVELOPMENT_FIELDS_V5:
        return system

    zone_lookup = {z.zone_id: z for z in typology_zones}
    field_phase_map = {
        field_obj.field_id: [p.phase_number for p in building_phases if field_obj.field_id in (p.field_ids or [])]
        for field_obj in _CURRENT_DEVELOPMENT_FIELDS_V5
    }
    for oz in system.zones:
        zid = str(getattr(oz, "zone_id", ""))
        if zid.startswith("OD-court-"):
            source_zone_id = zid.replace("OD-court-", "", 1)
            zsrc = zone_lookup.get(source_zone_id)
            if zsrc is not None and getattr(zsrc, "field_id", None):
                oz.serves_building_phases = field_phase_map.get(zsrc.field_id, [])
                if getattr(zsrc, "courtyard_name", ""):
                    if zsrc.courtyard_name not in oz.notes:
                        oz.notes = f"{zsrc.courtyard_name}. {oz.notes}".strip()
        elif getattr(oz, "kind", None) == "diagonal":
            oz.serves_building_phases = [p.phase_number for p in building_phases]

    for phase in building_phases:
        phase_zones = [
            z for z in system.zones
            if phase.phase_number in (z.serves_building_phases or []) and z.is_felles and z.on_ground
        ]
        phase.standalone_outdoor_zone_ids = [z.zone_id for z in phase_zones]
        phase.standalone_outdoor_m2 = sum(float(z.area_m2 or 0.0) for z in phase_zones)
        phase.standalone_outdoor_has_sun = True
    return system



def pass6_validate(masterplan: Masterplan, max_bya_pct: float,
                   max_floors: int, max_height_m: float) -> Masterplan:
    masterplan = _ORIG_V5_PASS6_VALIDATE(masterplan, max_bya_pct, max_floors, max_height_m)
    if masterplan.metrics is not None:
        masterplan.metrics.field_count = len(getattr(masterplan, "development_fields", []) or _CURRENT_DEVELOPMENT_FIELDS_V5)
        # Poeng for fasekoherens: én fase per delfelt er best; sammenslåtte delfelt litt lavere.
        coherence_scores = []
        for p in masterplan.building_phases:
            n_fields = len(getattr(p, "field_ids", []) or [])
            coherence_scores.append(100.0 if n_fields <= 1 else max(55.0, 100.0 - (n_fields - 1) * 20.0))
        masterplan.metrics.field_balance_score = sum(coherence_scores) / len(coherence_scores) if coherence_scores else 0.0
        masterplan.metrics.overall_score = min(
            100.0,
            float(masterplan.metrics.overall_score or 0.0) * 0.90 + masterplan.metrics.field_balance_score * 0.10,
        )
    return masterplan



def plan_masterplan(*args, **kwargs) -> Masterplan:
    mp = _ORIG_V5_PLAN_MASTERPLAN(*args, **kwargs)
    try:
        mp.development_fields = deepcopy(_CURRENT_DEVELOPMENT_FIELDS_V5)
        for f in mp.development_fields:
            f.zone_ids = [z.zone_id for z in mp.typology_zones if getattr(z, "field_id", None) == f.field_id]
        if mp.metrics is not None:
            mp.metrics.field_count = len(mp.development_fields)
        if isinstance(getattr(mp, "diag_info", None), dict):
            phase_desc = ", ".join(
                f"T{p.phase_number}:{'/'.join(p.field_names or p.field_ids or ['?'])} {p.actual_bra:.0f} m²"
                for p in mp.building_phases
            )
            mp.diag_info["v5"] = (
                f"{_CURRENT_FIELD_DIAG_TEXT_V5}; "
                f"byggetrinn={len(mp.building_phases)}; {phase_desc}"
            )
        mp.source = (getattr(mp, "source", "Builtly Masterplan") + " + v5 delfelt/phasing").strip()
    except Exception:
        pass
    return mp

# --- v5.1 hotfix: tilordne perimeter-/backfill-volumer til nærmeste delfelt ---

def _nearest_field_for_volume_v5(volume: Volume, fields: List[DevelopmentField]) -> Optional[DevelopmentField]:
    if not fields:
        return None
    vpoly = getattr(volume, 'polygon', None)
    vc = None
    try:
        vc = vpoly.centroid if vpoly is not None else Point(float(volume.cx), float(volume.cy))
    except Exception:
        vc = Point(float(getattr(volume, 'cx', 0.0)), float(getattr(volume, 'cy', 0.0)))

    best_field = None
    best_tuple = None
    for f in fields:
        fpoly = getattr(f, 'polygon', None)
        if fpoly is None or getattr(fpoly, 'is_empty', True):
            continue
        inter_area = 0.0
        if vpoly is not None:
            try:
                inter_area = float(vpoly.intersection(fpoly).area)
            except Exception:
                inter_area = 0.0
        try:
            dist = float(vc.distance(fpoly))
        except Exception:
            dist = 1e9
        # Mest overlapp først, deretter nærmeste avstand, deretter størst felt som tie-break.
        candidate = (-inter_area, dist, -float(getattr(fpoly, 'area', 0.0) or 0.0))
        if best_tuple is None or candidate < best_tuple:
            best_tuple = candidate
            best_field = f
    return best_field


def _assign_field_metadata_to_volumes_v5(volumes: List[Volume], zones: List[TypologyZone]) -> None:
    zone_map = {z.zone_id: z for z in zones}
    for v in volumes:
        z = zone_map.get(getattr(v, 'zone_id', None))
        if z is not None:
            v.field_id = getattr(z, 'field_id', None) or getattr(v, 'field_id', None)
            v.field_name = getattr(z, 'field_name', '') or getattr(v, 'field_name', '')
        if getattr(v, 'field_id', None):
            continue
        nearest = _nearest_field_for_volume_v5(v, _CURRENT_DEVELOPMENT_FIELDS_V5)
        if nearest is not None:
            v.field_id = nearest.field_id
            v.field_name = nearest.name


def pass3_place_volumes(zones: List[TypologyZone], program: ProgramAllocation,
                        max_floors: int, max_height_m: float, max_bya_pct: float,
                        floor_to_floor_m: float,
                        neighbors: Optional[List[Dict[str, Any]]],
                        site_polygon, buildable_polygon,
                        site_inputs: Dict[str, Any]) -> List[Volume]:
    volumes = _ORIG_V5_PASS3_PLACE_VOLUMES(
        zones=zones,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
        neighbors=neighbors,
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
    )
    _assign_field_metadata_to_volumes_v5(volumes, zones)

    if not _CURRENT_DEVELOPMENT_FIELDS_V5 or buildable_polygon is None or getattr(buildable_polygon, 'is_empty', True):
        return volumes

    efficiency_ratio = _get_efficiency_ratio_v2(site_inputs)
    field_targets_bta = _field_target_bta_map_v5(_CURRENT_DEVELOPMENT_FIELDS_V5, program, efficiency_ratio)
    max_fp_total = float(buildable_polygon.area) * (max_bya_pct / 100.0)
    fp_used = sum(float(v.footprint_m2 or 0.0) for v in volumes)
    existing_ids = []
    for v in volumes:
        try:
            if isinstance(v.volume_id, str) and v.volume_id.startswith('V'):
                existing_ids.append(int(v.volume_id[1:]))
        except Exception:
            pass
    volume_counter = max(existing_ids, default=0)

    for field_obj in _CURRENT_DEVELOPMENT_FIELDS_V5:
        if fp_used >= max_fp_total * 0.985:
            break
        field_vols = [v for v in volumes if getattr(v, 'field_id', None) == field_obj.field_id and v.polygon is not None]
        actual_bta = sum(float(v.footprint_m2 or 0.0) * float(v.floors or 0.0) for v in field_vols)
        deficit = field_targets_bta.get(field_obj.field_id, 0.0) - actual_bta
        if deficit <= 500.0:
            continue
        remaining = field_obj.polygon.buffer(0)
        if field_vols:
            try:
                remaining = remaining.difference(unary_union([v.polygon for v in field_vols]).buffer(MIN_BUILDING_SPACING / 2.0)).buffer(0)
            except Exception:
                remaining = remaining.buffer(0)
        pseudo_zone = TypologyZone(
            zone_id=f'QV4-{field_obj.field_id}-FILL',
            typology=_field_fill_typology_v5(field_obj),  # type: ignore[arg-type]
            polygon=field_obj.polygon,
            floors_min=max(2, field_obj.preferred_floors_min),
            floors_max=max(field_obj.preferred_floors_min, field_obj.preferred_floors_max),
            target_bra=0.0,
            rationale=f'v5 delfelt-backfill {field_obj.name}',
            field_id=field_obj.field_id,
            field_name=field_obj.name,
            zone_role='field_fill',
        )
        extra, volume_counter = _place_compact_bars_v42(
            zone=pseudo_zone,
            remaining_poly=remaining,
            deficit_bta=deficit,
            max_floors=max_floors,
            max_height_m=max_height_m,
            floor_to_floor_m=floor_to_floor_m,
            existing_polys=[v.polygon for v in volumes if v.polygon is not None],
            volume_counter=volume_counter,
            max_fp_remaining=max_fp_total - fp_used,
        )
        for ev in extra:
            ev.field_id = field_obj.field_id
            ev.field_name = field_obj.name
            ev.bra_efficiency_ratio = efficiency_ratio
        if extra:
            volumes.extend(extra)
            fp_used += sum(float(v.footprint_m2 or 0.0) for v in extra)

    _assign_field_metadata_to_volumes_v5(volumes, zones)
    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    return volumes

# ====================================================================
# V6 FINAL PATCHES — rapportklare navn, strammere delfelt-trinn,
# bedre solavstand og produksjonsvennlig presentasjonsmetadata.
# ====================================================================

_ORIG_V6_PASS4_PHASING = pass4_phasing
_ORIG_V6_PASS6_VALIDATE = pass6_validate
_ORIG_V6_PLAN_MASTERPLAN = plan_masterplan


def _pretty_side_v6(side: str) -> str:
    mapping = {
        'midt': 'Midt', 'nord': 'Nord', 'sør': 'Sør', 'øst': 'Øst', 'vest': 'Vest',
        'nordøst': 'Nordøst', 'nordvest': 'Nordvest', 'sørøst': 'Sørøst', 'sørvest': 'Sørvest',
        'north': 'Nord', 'south': 'Sør', 'east': 'Øst', 'west': 'Vest', 'center': 'Midt',
    }
    return mapping.get((side or '').strip().lower(), 'Midt')


def _field_display_name_v6(field_obj: DevelopmentField) -> str:
    side = _pretty_side_v6(getattr(field_obj, 'side_hint', 'midt'))
    context = str(getattr(field_obj, 'context', '') or '')
    if context == 'barnehage_edge':
        return 'Barnehagekvartalet' if side == 'Midt' else f'Barnehagekvartalet {side}'
    if context == 'urban_edge':
        return 'Mot veg' if side == 'Midt' else f'Mot veg {side}'
    if context == 'green_edge':
        return 'Mot park' if side == 'Midt' else f'Mot park {side}'
    if context == 'active_diagonal':
        return 'Midt' if side == 'Midt' else f'Midt {side}'
    if context == 'mixed_edge':
        return side if side != 'Midt' else 'Midt'
    if context == 'sensitive_edge':
        return side if side != 'Midt' else 'Småhuskanten'
    return side if side != 'Midt' else 'Midt'


def _alpha_code_v6(idx: int) -> str:
    idx = max(0, int(idx))
    out = ''
    while True:
        idx, rem = divmod(idx, 26)
        out = chr(65 + rem) + out
        if idx == 0:
            break
        idx -= 1
    return out


def _volume_role_label_v6(volume: Volume, zone_lookup: Dict[str, TypologyZone], field_obj: Optional[DevelopmentField]) -> str:
    zone = zone_lookup.get(getattr(volume, 'zone_id', None))
    role = str(getattr(zone, 'zone_role', '') or '')
    typ = str(getattr(volume, 'typology', '') or '')
    notes = (str(getattr(volume, 'notes', '') or '') + ' ' + str(getattr(zone, 'rationale', '') or '')).lower()
    if 'barnehage' in str(getattr(volume, 'program', '') or '') or 'barnehage' in notes:
        return 'barnehage'
    if typ in ('Punkthus', 'Tårn'):
        return 'punkt'
    if 'fill' in role or 'backfill' in notes:
        return 'bakfelt'
    if 'secondary' in role or 'cluster' in typ.lower() or 'tun' in role:
        return 'tun'
    return 'perimeter'


def _assign_readable_names_v6(masterplan: Masterplan) -> None:
    fields = list(getattr(masterplan, 'development_fields', []) or _CURRENT_DEVELOPMENT_FIELDS_V5)
    field_map = {f.field_id: f for f in fields}
    for f in fields:
        f.name = _field_display_name_v6(f)

    zone_lookup = {z.zone_id: z for z in masterplan.typology_zones}
    role_counts: Dict[Tuple[str, str], int] = {}
    ordered = sorted(
        masterplan.volumes,
        key=lambda v: (-float(getattr(v, 'cy', 0.0) or 0.0), float(getattr(v, 'cx', 0.0) or 0.0), str(getattr(v, 'volume_id', ''))),
    )
    for idx, v in enumerate(ordered):
        if not getattr(v, 'internal_name', ''):
            v.internal_name = getattr(v, 'name', '') or getattr(v, 'volume_id', '')
        field_obj = field_map.get(getattr(v, 'field_id', None))
        if field_obj is not None:
            v.field_name = field_obj.name
        role = _volume_role_label_v6(v, zone_lookup, field_obj)
        key = (v.field_name or 'Delfelt', role)
        role_counts[key] = role_counts.get(key, 0) + 1
        seq = role_counts[key]
        v.display_name = f"{v.field_name or 'Delfelt'} {role} {seq}".strip()
        v.house_id = f"HUS {_alpha_code_v6(idx)}"
        v.name = v.house_id

    for z in masterplan.typology_zones:
        if getattr(z, 'field_id', None) and z.field_id in field_map:
            z.field_name = field_map[z.field_id].name

    for phase in masterplan.building_phases:
        vols = [v for v in masterplan.volumes if v.volume_id in phase.volume_ids]
        new_names = []
        for fid in phase.field_ids or []:
            f = field_map.get(fid)
            if f is not None:
                new_names.append(f.name)
        phase.field_names = list(dict.fromkeys(new_names or phase.field_names or []))
        if phase.field_names:
            phase.label = f"Trinn {phase.phase_number} — {' + '.join(phase.field_names)}"
        phase.units_estimate = sum(int(getattr(v, 'units_estimate', 0) or 0) for v in vols)
        phase.actual_bra = sum(float(v.bra_m2 or 0.0) for v in vols)
        phase.target_bra = phase.actual_bra
        if not getattr(phase, 'estimated_duration_months', None):
            phase.estimated_duration_months = int(_clamp(math.ceil(max(phase.actual_bra, 1.0) / 550.0), 6, 18))


def _group_sorted_volumes_v6(field_vols: List[Volume], target_subphases: int) -> List[List[Volume]]:
    if target_subphases <= 1 or len(field_vols) <= 1:
        return [list(field_vols)]
    vols = sorted(field_vols, key=lambda v: (-float(getattr(v, 'cy', 0.0) or 0.0), float(getattr(v, 'cx', 0.0) or 0.0)))
    total = sum(float(v.bra_m2 or 0.0) for v in vols)
    per = total / max(target_subphases, 1)
    groups: List[List[Volume]] = [[]]
    acc = 0.0
    for v in vols:
        cur = groups[-1]
        vbra = float(v.bra_m2 or 0.0)
        if cur and len(groups) < target_subphases and acc + vbra > per * 1.05:
            groups.append([])
            acc = 0.0
            cur = groups[-1]
        cur.append(v)
        acc += vbra
    return [g for g in groups if g]


def _phase_groups_from_fields_v6(volumes: List[Volume], phasing_config: PhasingConfig) -> List[Tuple[List[str], List[Volume], List[str], int]]:
    field_map = {f.field_id: f for f in _CURRENT_DEVELOPMENT_FIELDS_V5}
    grouped: Dict[str, List[Volume]] = {}
    for v in volumes:
        fid = getattr(v, 'field_id', None) or getattr(v, 'zone_id', None) or 'uten_delfelt'
        grouped.setdefault(fid, []).append(v)

    groups: List[Tuple[List[str], List[Volume], List[str], int]] = []
    max_target = min(float(phasing_config.MAX_PHASE_BRA or 6500.0), 6500.0)
    split_target = min(6000.0, max_target)
    for fid, field_vols in grouped.items():
        field_vols = [v for v in field_vols if getattr(v, 'polygon', None) is not None]
        if not field_vols:
            continue
        total_bra = sum(float(v.bra_m2 or 0.0) for v in field_vols)
        field_obj = field_map.get(fid)
        field_name = getattr(field_obj, 'name', fid) if field_obj else fid
        order_hint = int(getattr(field_obj, 'phase_order_hint', 2) or 2) if field_obj else 2
        target_subphases = 1
        if total_bra > split_target and len(field_vols) >= 3:
            target_subphases = max(1, int(math.ceil(total_bra / split_target)))
        target_subphases = min(target_subphases, max(1, len(field_vols) // 2) or 1)
        subgroups = _group_sorted_volumes_v6(field_vols, target_subphases)
        for idx, subgroup in enumerate(subgroups, start=1):
            gname = field_name if len(subgroups) == 1 else f"{field_name} del {idx}"
            groups.append(([fid], subgroup, [gname], order_hint))

    def _group_centroid(gvols: List[Volume]) -> Tuple[float, float]:
        return (
            sum(float(getattr(v, 'cx', 0.0) or 0.0) for v in gvols) / max(len(gvols), 1),
            sum(float(getattr(v, 'cy', 0.0) or 0.0) for v in gvols) / max(len(gvols), 1),
        )

    # Merge grupper som blir for små.
    changed = True
    while changed and len(groups) > 1:
        changed = False
        for i, (fids, gvols, gnames, hint) in enumerate(list(groups)):
            gbra = sum(float(v.bra_m2 or 0.0) for v in gvols)
            if gbra >= float(phasing_config.MIN_PHASE_BRA or 2500.0):
                continue
            cx, cy = _group_centroid(gvols)
            best_j = None
            best_dist = float('inf')
            for j, (_, ovols, _, _) in enumerate(groups):
                if i == j or not ovols:
                    continue
                ocx, ocy = _group_centroid(ovols)
                dist = math.hypot(cx - ocx, cy - ocy)
                if dist < best_dist:
                    best_dist = dist
                    best_j = j
            if best_j is not None:
                ofids, ovols, onames, ohint = groups[best_j]
                groups[best_j] = (ofids + fids, ovols + gvols, onames + gnames, min(hint, ohint))
                del groups[i]
                changed = True
                break

    groups.sort(key=lambda item: (item[3], -_group_centroid(item[1])[1], _group_centroid(item[1])[0]))

    # Hold 3-5 trinn på store tomter ved å slå sammen de minste hvis vi går over 5.
    while len(groups) > 5:
        sizes = [sum(float(v.bra_m2 or 0.0) for v in g[1]) for g in groups]
        i = min(range(len(groups)), key=lambda k: sizes[k])
        cx, cy = _group_centroid(groups[i][1])
        best_j = None
        best_dist = float('inf')
        for j in range(len(groups)):
            if i == j:
                continue
            ocx, ocy = _group_centroid(groups[j][1])
            dist = math.hypot(cx - ocx, cy - ocy)
            if dist < best_dist:
                best_dist = dist
                best_j = j
        if best_j is None:
            break
        mfids, mvols, mnames, mhint = groups[i]
        ofids, ovols, onames, ohint = groups[best_j]
        groups[best_j] = (ofids + mfids, ovols + mvols, onames + mnames, min(mhint, ohint))
        del groups[i]
        groups.sort(key=lambda item: (item[3], -_group_centroid(item[1])[1], _group_centroid(item[1])[0]))

    return groups


def pass4_phasing(volumes: List[Volume], buildable_polygon,
                  phasing_config: PhasingConfig, target_phase_count: int,
                  program: ProgramAllocation, site_polygon) -> Tuple[List[BuildingPhase], List[ParkingPhase]]:
    if not _CURRENT_DEVELOPMENT_FIELDS_V5:
        return _ORIG_V6_PASS4_PHASING(volumes, buildable_polygon, phasing_config, target_phase_count, program, site_polygon)
    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    phase_groups = _phase_groups_from_fields_v6(volumes, phasing_config)
    phases: List[BuildingPhase] = []
    for idx, (field_ids, cluster_volumes, field_names, _) in enumerate(phase_groups, start=1):
        if not cluster_volumes:
            continue
        phase_bra = sum(float(v.bra_m2 or 0.0) for v in cluster_volumes)
        phase_units = sum(int(getattr(v, 'units_estimate', 0) or 0) for v in cluster_volumes)
        programs_in_phase: List[ProgramKind] = []
        for v in cluster_volumes:
            if v.program not in programs_in_phase:
                programs_in_phase.append(v.program)
            if v.ground_floor_program and v.ground_floor_program not in programs_in_phase:
                programs_in_phase.append(v.ground_floor_program)
        phase = BuildingPhase(
            phase_number=idx,
            label=f"Trinn {idx} — {' + '.join(list(dict.fromkeys(field_names)))}",
            volume_ids=[v.volume_id for v in cluster_volumes],
            target_bra=phase_bra,
            actual_bra=phase_bra,
            programs_included=programs_in_phase,
            units_estimate=phase_units,
            field_ids=list(dict.fromkeys(field_ids)),
            field_names=list(dict.fromkeys(field_names)),
            estimated_duration_months=int(_clamp(math.ceil(max(phase_bra, 1.0) / 550.0), 6, 18)),
        )
        for v in cluster_volumes:
            v.assigned_phase = idx
        phase_union = unary_union([v.polygon for v in cluster_volumes if v.polygon is not None])
        phase.construction_barrier_zone = phase_union.buffer(15.0) if phase_union is not None else None
        if idx > 1:
            phase.depends_on_phases = [idx - 1]
        phases.append(phase)

    parking_phases = _generate_parking_phases(phases, volumes, buildable_polygon, phasing_config, program)
    for bphase in phases:
        bphase.parking_served_by = [pp.phase_number for pp in parking_phases if bphase.phase_number in pp.serves_building_phases]
        if not bphase.parking_served_by and parking_phases:
            bphase.parking_served_by = [parking_phases[0].phase_number]
    return phases, parking_phases


def _translate_volume_if_valid_v6(volume: Volume, moved_poly, field_poly, others: List[Volume]) -> bool:
    try:
        if moved_poly is None or getattr(moved_poly, 'is_empty', True):
            return False
        if field_poly is not None and not moved_poly.buffer(0).within(field_poly.buffer(0.25)):
            return False
        for other in others:
            if other.volume_id == volume.volume_id or getattr(other, 'polygon', None) is None:
                continue
            if moved_poly.intersects(other.polygon.buffer(MIN_BUILDING_SPACING * 0.45)):
                return False
        volume.polygon = moved_poly.buffer(0)
        c = volume.polygon.centroid
        volume.cx = float(c.x)
        volume.cy = float(c.y)
        return True
    except Exception:
        return False


def _apply_solar_guardrails_v6(masterplan: Masterplan) -> None:
    field_map = {f.field_id: f for f in getattr(masterplan, 'development_fields', []) or []}
    changed = False
    for fid, field_obj in field_map.items():
        field_vols = [v for v in masterplan.volumes if getattr(v, 'field_id', None) == fid and getattr(v, 'polygon', None) is not None]
        for i in range(len(field_vols)):
            for j in range(i + 1, len(field_vols)):
                a = field_vols[i]
                b = field_vols[j]
                da = math.radians(float(getattr(a, 'angle_deg', 0.0) or 0.0))
                db = math.radians(float(getattr(b, 'angle_deg', 0.0) or 0.0))
                diff = abs((da - db + math.pi / 2) % math.pi - math.pi / 2)
                if diff > math.radians(12):
                    continue
                ux, uy = math.cos(da), math.sin(da)
                nx, ny = -uy, ux
                dx = float(b.cx - a.cx)
                dy = float(b.cy - a.cy)
                along = abs(dx * ux + dy * uy)
                across = abs(dx * nx + dy * ny)
                clear = across - (float(a.depth_m or 0.0) + float(b.depth_m or 0.0)) * 0.5
                desired = max(float(MIN_BUILDING_SPACING), 1.2 * max(float(a.height_m or 0.0), float(b.height_m or 0.0)), 20.0)
                if clear >= desired:
                    continue
                if along > (float(a.width_m or 0.0) + float(b.width_m or 0.0)) * 0.55 + 10.0:
                    continue
                move = max(0.0, (desired - clear) / 2.0)
                sign = 1.0 if (dx * nx + dy * ny) >= 0 else -1.0
                moved_a = affinity.translate(a.polygon, xoff=-nx * move * sign, yoff=-ny * move * sign)
                moved_b = affinity.translate(b.polygon, xoff=nx * move * sign, yoff=ny * move * sign)
                ok_a = _translate_volume_if_valid_v6(a, moved_a, field_obj.polygon, field_vols)
                ok_b = _translate_volume_if_valid_v6(b, moved_b, field_obj.polygon, field_vols)
                if ok_a or ok_b:
                    changed = True
                    continue
                south = a if float(a.cy) < float(b.cy) else b
                if south.floors > 3:
                    south.floors -= 1
                    south.height_m = max(9.0, south.height_m - _CURRENT_FLOOR_TO_FLOOR_V4)
                    changed = True
    if changed:
        _reestimate_units_v4(masterplan.volumes, _CURRENT_AVG_UNIT_BRA_V4)
        for phase in masterplan.building_phases:
            vols = [v for v in masterplan.volumes if v.volume_id in phase.volume_ids]
            phase.actual_bra = sum(float(v.bra_m2 or 0.0) for v in vols)
            phase.target_bra = phase.actual_bra
            phase.units_estimate = sum(int(getattr(v, 'units_estimate', 0) or 0) for v in vols)
            if not phase.estimated_duration_months:
                phase.estimated_duration_months = int(_clamp(math.ceil(max(phase.actual_bra, 1.0) / 550.0), 6, 18))


def _solar_spacing_score_v6(masterplan: Masterplan) -> float:
    pairs = []
    vols = [v for v in masterplan.volumes if getattr(v, 'polygon', None) is not None]
    for i in range(len(vols)):
        for j in range(i + 1, len(vols)):
            a, b = vols[i], vols[j]
            if getattr(a, 'field_id', None) != getattr(b, 'field_id', None):
                continue
            da = math.radians(float(getattr(a, 'angle_deg', 0.0) or 0.0))
            db = math.radians(float(getattr(b, 'angle_deg', 0.0) or 0.0))
            diff = abs((da - db + math.pi / 2) % math.pi - math.pi / 2)
            if diff > math.radians(12):
                continue
            ux, uy = math.cos(da), math.sin(da)
            nx, ny = -uy, ux
            dx = float(b.cx - a.cx)
            dy = float(b.cy - a.cy)
            along = abs(dx * ux + dy * uy)
            across = abs(dx * nx + dy * ny)
            if along > (float(a.width_m or 0.0) + float(b.width_m or 0.0)) * 0.55 + 10.0:
                continue
            clear = across - (float(a.depth_m or 0.0) + float(b.depth_m or 0.0)) * 0.5
            desired = max(float(MIN_BUILDING_SPACING), 1.2 * max(float(a.height_m or 0.0), float(b.height_m or 0.0)), 20.0)
            pairs.append(_clamp(clear / max(desired, 1.0), 0.0, 1.2))
    if not pairs:
        return 82.0
    return 45.0 + (sum(pairs) / len(pairs)) * 45.0


def pass6_validate(masterplan: Masterplan, max_bya_pct: float,
                   max_floors: int, max_height_m: float) -> Masterplan:
    masterplan = _ORIG_V6_PASS6_VALIDATE(masterplan, max_bya_pct, max_floors, max_height_m)
    _apply_solar_guardrails_v6(masterplan)
    _assign_readable_names_v6(masterplan)
    if masterplan.metrics is not None:
        # Oppdater score med sol-/feltkvalitet, men behold eksisterende tung logikk.
        spacing_score = _solar_spacing_score_v6(masterplan)
        phase_scores = []
        for p in masterplan.building_phases:
            n_fields = len(getattr(p, 'field_ids', []) or [])
            coherence = 100.0 if n_fields <= 1 else max(55.0, 100.0 - (n_fields - 1) * 20.0)
            phase_scores.append(coherence)
        masterplan.metrics.field_balance_score = sum(phase_scores) / len(phase_scores) if phase_scores else masterplan.metrics.field_balance_score
        masterplan.metrics.avg_phase_bra = sum(float(p.actual_bra or 0.0) for p in masterplan.building_phases) / max(len(masterplan.building_phases), 1)
        masterplan.metrics.min_phase_bra = min((float(p.actual_bra or 0.0) for p in masterplan.building_phases), default=0.0)
        masterplan.metrics.max_phase_bra = max((float(p.actual_bra or 0.0) for p in masterplan.building_phases), default=0.0)
        masterplan.metrics.units_total = sum(int(getattr(v, 'units_estimate', 0) or 0) for v in masterplan.volumes)
        masterplan.metrics.total_bra = sum(float(v.bra_m2 or 0.0) for v in masterplan.volumes)
        masterplan.metrics.overall_score = min(100.0, float(masterplan.metrics.overall_score or 0.0) * 0.82 + spacing_score * 0.08 + masterplan.metrics.field_balance_score * 0.10)
        if spacing_score < 40.0:
            masterplan.warnings.append('Lav sol-/avstandskvalitet i enkelte delfelt. Øk avstand mellom parallelle volumer eller trapp ned sørflanker.')
    return masterplan


def plan_masterplan(*args, **kwargs) -> Masterplan:
    mp = _ORIG_V6_PLAN_MASTERPLAN(*args, **kwargs)
    try:
        _assign_readable_names_v6(mp)
        if mp.metrics is not None:
            mp.metrics.field_count = len(getattr(mp, 'development_fields', []) or [])
        if isinstance(getattr(mp, 'diag_info', None), dict):
            mp.diag_info['v6'] = '; '.join([
                f"felt={len(getattr(mp, 'development_fields', []) or [])}",
                f"trinn={len(getattr(mp, 'building_phases', []) or [])}",
                ', '.join(f"T{p.phase_number}:{p.label} {float(p.actual_bra or 0.0):.0f} m²" for p in getattr(mp, 'building_phases', [])[:8])
            ])
        mp.source = (getattr(mp, 'source', 'Builtly Masterplan') + ' + v6 final').strip()
    except Exception:
        pass
    return mp

# =====================================================================
# V7 PATCH — sterkere delfeltkoherens, roligere navn og mindre fragmenterte
# byggetrinn for presentasjon/UI.
# =====================================================================

_V7_ROLE_LABELS = {
    'perimeter': 'Bykant',
    'bakfelt': 'Tunhus',
    'tun': 'Tunhus',
    'punkt': 'Punkthus',
    'barnehage': 'Barnehage',
}


def _field_fill_typology_v5(field_obj: DevelopmentField) -> str:
    """V7: roligere og mer arkitektonisk lesbar backfill.

    Unngår at små restarealer fylles med mange LamellSegmentert-fragmenter.
    """
    ctx = str(getattr(field_obj, 'context', '') or '')
    maxf = int(getattr(field_obj, 'preferred_floors_max', 5) or 5)
    if ctx == 'sensitive_edge':
        return 'Rekke' if maxf <= 3 else 'Lamell'
    if ctx == 'barnehage_edge':
        return 'Lamell'
    if ctx in {'urban_edge', 'green_edge', 'mixed_edge'}:
        return 'Lamell'
    if ctx in {'calm_inner', 'active_diagonal'}:
        return 'HalvåpenKarré' if maxf >= 5 else 'Lamell'
    return 'Lamell'


def _dedupe_field_names_v7(fields: List[DevelopmentField]) -> None:
    groups: Dict[str, List[DevelopmentField]] = {}
    for f in fields:
        base = str(getattr(f, 'name', '') or '').strip() or 'Delfelt'
        groups.setdefault(base, []).append(f)
    for base, peers in groups.items():
        if len(peers) <= 1:
            peers[0].name = base
            continue
        peers_sorted = sorted(
            peers,
            key=lambda item: (
                -float(getattr(getattr(item, 'polygon', None), 'centroid', Point(0, 0)).y),
                float(getattr(getattr(item, 'polygon', None), 'centroid', Point(0, 0)).x),
            ),
        )
        for idx, item in enumerate(peers_sorted, start=1):
            item.name = f"{base} {idx}"


def _phase_group_centroid_v7(vols: List[Volume]) -> Tuple[float, float]:
    if not vols:
        return (0.0, 0.0)
    return (
        sum(float(getattr(v, 'cx', 0.0) or 0.0) for v in vols) / len(vols),
        sum(float(getattr(v, 'cy', 0.0) or 0.0) for v in vols) / len(vols),
    )


def _group_sorted_volumes_v6(field_vols: List[Volume], target_subphases: int) -> List[List[Volume]]:
    """V7: grupper volum langs feltets hovedretning, men ikke for aggressivt."""
    if target_subphases <= 1 or len(field_vols) <= 1:
        return [list(field_vols)]
    xs = [float(getattr(v, 'cx', 0.0) or 0.0) for v in field_vols]
    ys = [float(getattr(v, 'cy', 0.0) or 0.0) for v in field_vols]
    sort_key = (lambda v: float(getattr(v, 'cx', 0.0) or 0.0)) if (max(xs) - min(xs) >= max(ys) - min(ys)) else (lambda v: -float(getattr(v, 'cy', 0.0) or 0.0))
    ordered = sorted(field_vols, key=sort_key)
    total = sum(float(v.bra_m2 or 0.0) for v in ordered)
    per = total / max(target_subphases, 1)
    groups: List[List[Volume]] = [[]]
    acc = 0.0
    for v in ordered:
        vbra = float(v.bra_m2 or 0.0)
        cur = groups[-1]
        # Hopp bare til ny gruppe hvis vi allerede har minst 2 bygg i gruppen.
        if cur and len(cur) >= 2 and len(groups) < target_subphases and acc + vbra > per * 1.08:
            groups.append([])
            cur = groups[-1]
            acc = 0.0
        cur.append(v)
        acc += vbra
    return [g for g in groups if g]


def _merge_group_pair_v7(groups, i: int, j: int):
    fids_a, vols_a, names_a, hint_a = groups[i]
    fids_b, vols_b, names_b, hint_b = groups[j]
    merged = (
        list(dict.fromkeys(list(fids_a) + list(fids_b))),
        list(vols_a) + list(vols_b),
        list(dict.fromkeys(list(names_a) + list(names_b))),
        min(int(hint_a), int(hint_b)),
    )
    keep = min(i, j)
    drop = max(i, j)
    groups[keep] = merged
    del groups[drop]


def _phase_groups_from_fields_v6(volumes: List[Volume], phasing_config: PhasingConfig) -> List[Tuple[List[str], List[Volume], List[str], int]]:
    """V7: hold byggetrinn feltkoherente.

    Prioriterer én fase per delfelt. Felt kan deles i to ved store BRA-mengder,
    men små felt slås ikke sammen på tvers med mindre det er helt nødvendig.
    """
    field_map = {f.field_id: f for f in _CURRENT_DEVELOPMENT_FIELDS_V5}
    grouped: Dict[str, List[Volume]] = {}
    for v in volumes:
        fid = getattr(v, 'field_id', None) or getattr(v, 'zone_id', None) or 'uten_delfelt'
        grouped.setdefault(fid, []).append(v)

    groups: List[Tuple[List[str], List[Volume], List[str], int]] = []
    min_soft = max(1800.0, float(phasing_config.MIN_PHASE_BRA or 2500.0) * 0.72)
    split_target = min(6200.0, max(float(phasing_config.MAX_PHASE_BRA or 6500.0), 4800.0))

    for fid, field_vols in grouped.items():
        field_vols = [v for v in field_vols if getattr(v, 'polygon', None) is not None]
        if not field_vols:
            continue
        field_obj = field_map.get(fid)
        field_name = getattr(field_obj, 'name', fid) if field_obj else fid
        order_hint = int(getattr(field_obj, 'phase_order_hint', 2) or 2) if field_obj else 2
        total_bra = sum(float(v.bra_m2 or 0.0) for v in field_vols)
        target_subphases = 1
        if total_bra > split_target * 1.25 and len(field_vols) >= 4 and str(getattr(field_obj, 'context', '') or '') != 'barnehage_edge':
            target_subphases = int(math.ceil(total_bra / split_target))
        target_subphases = max(1, min(target_subphases, 3))
        subgroups = _group_sorted_volumes_v6(field_vols, target_subphases)
        for idx, subgroup in enumerate(subgroups, start=1):
            gname = field_name if len(subgroups) == 1 else f"{field_name} del {idx}"
            groups.append(([fid], subgroup, [gname], order_hint))

    def _group_size(g) -> float:
        return sum(float(v.bra_m2 or 0.0) for v in g[1])

    # 1) Slå først sammen undersized subgrupper med søsken fra samme felt.
    changed = True
    while changed:
        changed = False
        for i, g in enumerate(list(groups)):
            fid_list = list(g[0])
            if len(fid_list) != 1:
                continue
            gbra = _group_size(g)
            if gbra >= min_soft:
                continue
            fid = fid_list[0]
            sib_idx = [j for j, og in enumerate(groups) if j != i and list(og[0]) == [fid]]
            if not sib_idx:
                continue
            best_j = min(sib_idx, key=lambda j: _group_size(groups[j]))
            _merge_group_pair_v7(groups, i, best_j)
            changed = True
            break

    # 2) Hold oss innen 5 faser ved å slå sammen søskengrupper før alt annet.
    while len(groups) > 5:
        candidates = []
        for i, gi in enumerate(groups):
            for j in range(i + 1, len(groups)):
                gj = groups[j]
                if set(gi[0]) == set(gj[0]):
                    candidates.append((i, j, _group_size(gi) + _group_size(gj)))
        if candidates:
            i, j, _ = min(candidates, key=lambda row: row[2])
            _merge_group_pair_v7(groups, i, j)
            continue
        break

    # 3) Bare hvis vi fremdeles har veldig små grupper og for mange faser, merge med nærmeste gruppe.
    # Barnehagefelt og spesialfelt får lov til å være mindre for å beholde lesbar delfeltstruktur.
    changed = True
    while changed:
        changed = False
        for i, g in enumerate(list(groups)):
            gbra = _group_size(g)
            fid = list(g[0])[0] if len(g[0]) == 1 else ''
            field_obj = field_map.get(fid)
            ctx = str(getattr(field_obj, 'context', '') or '') if field_obj else ''
            if gbra >= min_soft or len(groups) <= 5 or ctx == 'barnehage_edge':
                continue
            cx, cy = _phase_group_centroid_v7(g[1])
            best_j = None
            best_tuple = None
            for j, other in enumerate(groups):
                if i == j:
                    continue
                ocx, ocy = _phase_group_centroid_v7(other[1])
                dist = math.hypot(cx - ocx, cy - ocy)
                resulting = gbra + _group_size(other)
                # Ikke lag monstertrinn.
                if resulting > max(float(phasing_config.MAX_PHASE_BRA or 6500.0) * 1.25, 7600.0):
                    continue
                cand = (dist, resulting)
                if best_tuple is None or cand < best_tuple:
                    best_tuple = cand
                    best_j = j
            if best_j is not None:
                _merge_group_pair_v7(groups, i, best_j)
                changed = True
                break

    groups.sort(key=lambda item: (int(item[3]), -_phase_group_centroid_v7(item[1])[1], _phase_group_centroid_v7(item[1])[0]))
    return groups


def pass4_phasing(volumes: List[Volume], buildable_polygon,
                  phasing_config: PhasingConfig, target_phase_count: int,
                  program: ProgramAllocation, site_polygon) -> Tuple[List[BuildingPhase], List[ParkingPhase]]:
    if not _CURRENT_DEVELOPMENT_FIELDS_V5:
        return _ORIG_V6_PASS4_PHASING(volumes, buildable_polygon, phasing_config, target_phase_count, program, site_polygon)
    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    phase_groups = _phase_groups_from_fields_v6(volumes, phasing_config)
    phases: List[BuildingPhase] = []
    for idx, (field_ids, cluster_volumes, field_names, _) in enumerate(phase_groups, start=1):
        if not cluster_volumes:
            continue
        phase_bra = sum(float(v.bra_m2 or 0.0) for v in cluster_volumes)
        phase_units = sum(int(getattr(v, 'units_estimate', 0) or 0) for v in cluster_volumes)
        programs_in_phase: List[ProgramKind] = []
        for v in cluster_volumes:
            if v.program not in programs_in_phase:
                programs_in_phase.append(v.program)
            if v.ground_floor_program and v.ground_floor_program not in programs_in_phase:
                programs_in_phase.append(v.ground_floor_program)
        label_names = list(dict.fromkeys(field_names))
        phase = BuildingPhase(
            phase_number=idx,
            label=f"Trinn {idx} — {' + '.join(label_names)}",
            volume_ids=[v.volume_id for v in cluster_volumes],
            target_bra=phase_bra,
            actual_bra=phase_bra,
            programs_included=programs_in_phase,
            units_estimate=phase_units,
            field_ids=list(dict.fromkeys(field_ids)),
            field_names=label_names,
            estimated_duration_months=int(_clamp(math.ceil(max(phase_bra, 1.0) / 650.0), 6, 18)),
        )
        for v in cluster_volumes:
            v.assigned_phase = idx
        phase_union = unary_union([v.polygon for v in cluster_volumes if v.polygon is not None])
        phase.construction_barrier_zone = phase_union.buffer(15.0) if phase_union is not None else None
        if idx > 1:
            phase.depends_on_phases = [idx - 1]
        phases.append(phase)

    parking_phases = _generate_parking_phases(phases, volumes, buildable_polygon, phasing_config, program)
    for bphase in phases:
        bphase.parking_served_by = [pp.phase_number for pp in parking_phases if bphase.phase_number in pp.serves_building_phases]
        if not bphase.parking_served_by and parking_phases:
            bphase.parking_served_by = [parking_phases[0].phase_number]
    return phases, parking_phases


def _assign_readable_names_v6(masterplan: Masterplan) -> None:
    fields = list(getattr(masterplan, 'development_fields', []) or _CURRENT_DEVELOPMENT_FIELDS_V5)
    field_map = {f.field_id: f for f in fields}
    for f in fields:
        f.name = _field_display_name_v6(f)
    _dedupe_field_names_v7(fields)

    zone_lookup = {z.zone_id: z for z in masterplan.typology_zones}
    role_counts: Dict[Tuple[str, str], int] = {}
    ordered = sorted(
        masterplan.volumes,
        key=lambda v: (-float(getattr(v, 'cy', 0.0) or 0.0), float(getattr(v, 'cx', 0.0) or 0.0), str(getattr(v, 'volume_id', ''))),
    )
    for idx, v in enumerate(ordered):
        if not getattr(v, 'internal_name', ''):
            v.internal_name = getattr(v, 'name', '') or getattr(v, 'volume_id', '')
        field_obj = field_map.get(getattr(v, 'field_id', None))
        if field_obj is not None:
            v.field_name = field_obj.name
        role = _volume_role_label_v6(v, zone_lookup, field_obj)
        key = (v.field_name or 'Delfelt', role)
        role_counts[key] = role_counts.get(key, 0) + 1
        seq = role_counts[key]
        pretty_role = _V7_ROLE_LABELS.get(role, role.title())
        v.display_name = f"{v.field_name or 'Delfelt'} – {pretty_role} {seq}".strip()
        v.house_id = f"HUS {_alpha_code_v6(idx)}"
        v.name = v.house_id

    for z in masterplan.typology_zones:
        if getattr(z, 'field_id', None) and z.field_id in field_map:
            z.field_name = field_map[z.field_id].name

    for phase in masterplan.building_phases:
        vols = [v for v in masterplan.volumes if v.volume_id in phase.volume_ids]
        new_names = []
        for fid in phase.field_ids or []:
            f = field_map.get(fid)
            if f is not None:
                new_names.append(f.name)
        phase.field_names = list(dict.fromkeys(new_names or phase.field_names or []))
        if phase.field_names:
            phase.label = f"Trinn {phase.phase_number} — {' + '.join(phase.field_names)}"
        phase.units_estimate = sum(int(getattr(v, 'units_estimate', 0) or 0) for v in vols)
        phase.actual_bra = sum(float(v.bra_m2 or 0.0) for v in vols)
        phase.target_bra = phase.actual_bra
        if not getattr(phase, 'estimated_duration_months', None):
            phase.estimated_duration_months = int(_clamp(math.ceil(max(phase.actual_bra, 1.0) / 650.0), 6, 18))


def pass6_validate(masterplan: Masterplan, max_bya_pct: float,
                   max_floors: int, max_height_m: float) -> Masterplan:
    masterplan = _ORIG_V6_PASS6_VALIDATE(masterplan, max_bya_pct, max_floors, max_height_m)
    _apply_solar_guardrails_v6(masterplan)
    _assign_readable_names_v6(masterplan)
    if masterplan.metrics is not None:
        spacing_score = _solar_spacing_score_v6(masterplan)
        phase_scores = []
        for p in masterplan.building_phases:
            n_fields = len(getattr(p, 'field_ids', []) or [])
            coherence = 100.0 if n_fields <= 1 else max(55.0, 100.0 - (n_fields - 1) * 20.0)
            phase_scores.append(coherence)
        masterplan.metrics.field_balance_score = sum(phase_scores) / len(phase_scores) if phase_scores else masterplan.metrics.field_balance_score
        masterplan.metrics.avg_phase_bra = sum(float(p.actual_bra or 0.0) for p in masterplan.building_phases) / max(len(masterplan.building_phases), 1)
        masterplan.metrics.min_phase_bra = min((float(p.actual_bra or 0.0) for p in masterplan.building_phases), default=0.0)
        masterplan.metrics.max_phase_bra = max((float(p.actual_bra or 0.0) for p in masterplan.building_phases), default=0.0)
        masterplan.metrics.units_total = sum(int(getattr(v, 'units_estimate', 0) or 0) for v in masterplan.volumes)
        masterplan.metrics.total_bra = sum(float(v.bra_m2 or 0.0) for v in masterplan.volumes)
        masterplan.metrics.overall_score = min(100.0, float(masterplan.metrics.overall_score or 0.0) * 0.82 + spacing_score * 0.08 + masterplan.metrics.field_balance_score * 0.10)
        if spacing_score < 40.0:
            masterplan.warnings.append('Lav sol-/avstandskvalitet i enkelte delfelt. Øk avstand mellom parallelle volumer eller trapp ned sørflanker.')
    return masterplan


def plan_masterplan(*args, **kwargs) -> Masterplan:
    mp = _ORIG_V6_PLAN_MASTERPLAN(*args, **kwargs)
    try:
        _assign_readable_names_v6(mp)
        if mp.metrics is not None:
            mp.metrics.field_count = len(getattr(mp, 'development_fields', []) or [])
        if isinstance(getattr(mp, 'diag_info', None), dict):
            mp.diag_info['v7'] = '; '.join([
                f"felt={len(getattr(mp, 'development_fields', []) or [])}",
                f"trinn={len(getattr(mp, 'building_phases', []) or [])}",
                ', '.join(f"T{p.phase_number}:{p.label} {float(p.actual_bra or 0.0):.0f} m²" for p in getattr(mp, 'building_phases', [])[:8])
            ])
        mp.source = (getattr(mp, 'source', 'Builtly Masterplan') + ' + v7 builtly').strip()
    except Exception:
        pass
    return mp


# =====================================================================
# V8 PATCH — mer feltstyrt struktur, høyere måloppnåelse og roligere
# byggetrinn for store tomter som NRK Tyholt.
# =====================================================================

_ORIG_V8_PASS3_PLACE_VOLUMES = pass3_place_volumes
_ORIG_V8_PASS4_PHASING = pass4_phasing
_ORIG_V8_PLAN_MASTERPLAN = plan_masterplan


def _recommended_field_count_v5(buildable_polygon, target_bra_m2: float,
                                 max_bya_pct: float, max_floors: int) -> int:
    area = float(getattr(buildable_polygon, 'area', 0.0) or 0.0)
    if area <= 0:
        return 1
    bounds = getattr(buildable_polygon, 'bounds', (0.0, 0.0, 0.0, 0.0))
    bw = float(bounds[2] - bounds[0])
    bh = float(bounds[3] - bounds[1])
    aspect = max(bw, bh) / max(min(bw, bh), 1.0)
    req_avg = _required_avg_floors_v3(target_bra_m2, buildable_polygon, max_bya_pct)
    if area < 10000.0:
        return 1
    if area < 20000.0:
        return 2
    if area < 30000.0:
        return 4 if (aspect >= 1.45 or req_avg >= 3.8) else 3
    # 30k–36k m²: hold normalt 4 delfelt for bedre lesbarhet.
    if area < 36000.0:
        return 4
    if area < 45000.0:
        return 5 if (aspect >= 1.80 or req_avg >= 4.5 or max_floors >= 8) else 4
    return 5


def _v8_field_priority(field_obj: DevelopmentField) -> int:
    ctx = str(getattr(field_obj, 'context', '') or '')
    return {
        'barnehage_edge': 1,
        'sensitive_edge': 2,
        'green_edge': 3,
        'mixed_edge': 4,
        'urban_edge': 5,
        'active_diagonal': 4,
        'calm_inner': 3,
    }.get(ctx, 3)


def _v8_average_angle(vols: List[Volume]) -> float:
    vals = [float(getattr(v, 'angle_deg', 0.0) or 0.0) % 180.0 for v in vols if getattr(v, 'polygon', None) is not None]
    if not vals:
        return 0.0
    # circular mean on doubled angle to handle 180-periodicity
    s = sum(math.sin(math.radians(v * 2.0)) for v in vals)
    c = sum(math.cos(math.radians(v * 2.0)) for v in vals)
    if abs(s) < 1e-6 and abs(c) < 1e-6:
        return vals[0]
    return (math.degrees(math.atan2(s, c)) / 2.0) % 180.0


def _v8_next_volume_counter(vols: List[Volume]) -> int:
    out = 0
    for v in vols:
        try:
            if isinstance(v.volume_id, str) and v.volume_id.startswith('V'):
                out = max(out, int(v.volume_id[1:]))
        except Exception:
            continue
    return out


def _v8_add_clean_infill_bars(volumes: List[Volume], fields: List[DevelopmentField], target_total_bra: float,
                              max_floors: int, max_height_m: float, floor_to_floor_m: float,
                              efficiency_ratio: float) -> List[Volume]:
    if not fields or target_total_bra <= 0:
        return volumes
    current_bra = sum(float(v.bra_m2 or 0.0) for v in volumes)
    if current_bra >= target_total_bra * 0.96:
        return volumes
    if not HAS_SHAPELY or unary_union is None:
        return volumes

    existing_polys = [v.polygon for v in volumes if getattr(v, 'polygon', None) is not None]
    volume_counter = _v8_next_volume_counter(volumes)
    diag_zone = _CURRENT_DIAGONAL_ZONE_V4
    field_lookup = {f.field_id: f for f in fields}

    for field_obj in sorted(fields, key=lambda f: (_v8_field_priority(f), -float(getattr(f, 'target_bra', 0.0) or 0.0)), reverse=True):
        if current_bra >= target_total_bra * 0.985:
            break
        ctx = str(getattr(field_obj, 'context', '') or '')
        if ctx in {'sensitive_edge', 'barnehage_edge'}:
            continue
        field_poly = getattr(field_obj, 'polygon', None)
        if field_poly is None or getattr(field_poly, 'is_empty', True):
            continue
        try:
            occupied = unary_union(existing_polys).buffer(max(MIN_BUILDING_SPACING * 0.55, 4.5)) if existing_polys else None
            residual = field_poly.buffer(0)
            if occupied is not None and not getattr(occupied, 'is_empty', True):
                residual = residual.difference(occupied).buffer(0)
            if diag_zone is not None and not getattr(diag_zone, 'is_empty', True):
                residual = residual.difference(diag_zone.buffer(2.0)).buffer(0)
        except Exception:
            residual = field_poly.buffer(0)
        comps = list(residual.geoms) if isinstance(residual, MultiPolygon) else [residual]
        comps = [c.buffer(0) for c in comps if c is not None and not c.is_empty and float(c.area) >= 320.0]
        comps.sort(key=lambda g: float(g.area), reverse=True)
        if not comps:
            continue
        field_vols = [v for v in volumes if getattr(v, 'field_id', None) == field_obj.field_id]
        base_angle = _v8_average_angle(field_vols)
        floor_cap = min(max_floors, int(getattr(field_obj, 'preferred_floors_max', max_floors) or max_floors))
        if ctx == 'urban_edge':
            floor_cap = min(max_floors, max(floor_cap, 6))
        floors = max(4, min(floor_cap, int(max_height_m / max(floor_to_floor_m, 2.8))))
        for comp in comps[:2]:
            if current_bra >= target_total_bra * 0.985:
                break
            c = comp.centroid
            attempts = [
                (34.0, 13.0, base_angle),
                (30.0, 13.0, base_angle),
                (28.0, 12.5, base_angle),
                (26.0, 12.0, (base_angle + 90.0) % 180.0),
            ]
            placed = False
            for width_m, depth_m, ang in attempts:
                poly = _make_building_polygon(float(c.x), float(c.y), width_m, depth_m, ang)
                if poly is None:
                    continue
                if not poly.buffer(0).within(comp.buffer(-0.8)):
                    continue
                too_close = False
                for ep in existing_polys:
                    try:
                        if poly.distance(ep) < max(MIN_BUILDING_SPACING - 0.6, 7.2):
                            too_close = True
                            break
                    except Exception:
                        continue
                if too_close:
                    continue
                volume_counter += 1
                cc = poly.centroid
                v = Volume(
                    volume_id=f'V{volume_counter:02d}',
                    name=f'V8 {field_obj.name} infill',
                    polygon=poly,
                    typology='Lamell',
                    floors=int(floors),
                    height_m=round(float(floors) * float(floor_to_floor_m), 1),
                    width_m=round(width_m, 1),
                    depth_m=round(depth_m, 1),
                    angle_deg=round(float(ang), 1),
                    cx=round(float(cc.x), 1),
                    cy=round(float(cc.y), 1),
                    footprint_m2=round(float(poly.area), 1),
                    bra_efficiency_ratio=float(efficiency_ratio or 0.85),
                    program='bolig',
                    zone_id=f'QV4-{field_obj.field_id}-V8FILL',
                    field_id=field_obj.field_id,
                    field_name=field_obj.name,
                    notes='v8 clean infill bar',
                )
                volumes.append(v)
                existing_polys.append(poly)
                current_bra += float(v.bra_m2 or 0.0)
                placed = True
                break
            if placed and current_bra >= target_total_bra * 0.985:
                break
    return volumes


def _v8_raise_floors_toward_target(volumes: List[Volume], fields: List[DevelopmentField], target_total_bra: float,
                                   max_floors: int, max_height_m: float, floor_to_floor_m: float) -> None:
    current_bra = sum(float(v.bra_m2 or 0.0) for v in volumes)
    if current_bra >= target_total_bra * 0.96 or not volumes:
        return
    field_map = {f.field_id: f for f in fields}
    site_centroid = unary_union([f.polygon for f in fields if getattr(f, 'polygon', None) is not None]).centroid if HAS_SHAPELY and fields else None
    candidates = []
    for v in volumes:
        if getattr(v, 'polygon', None) is None:
            continue
        field_obj = field_map.get(getattr(v, 'field_id', None))
        ctx = str(getattr(field_obj, 'context', '') or '') if field_obj else ''
        field_cap = min(max_floors, int(getattr(field_obj, 'preferred_floors_max', max_floors) or max_floors)) if field_obj else max_floors
        if ctx in {'urban_edge', 'mixed_edge'}:
            field_cap = min(max_floors, max(field_cap, 6))
        if v.floors >= field_cap:
            continue
        if float(v.height_m or 0.0) + float(floor_to_floor_m or 3.2) > float(max_height_m or 99.0) + 0.15:
            continue
        north_bonus = 0.0
        if field_obj is not None and getattr(field_obj, 'polygon', None) is not None:
            north_bonus = (float(v.cy or 0.0) - float(field_obj.polygon.centroid.y)) / 40.0
        center_penalty = 0.0
        if site_centroid is not None:
            center_penalty = math.hypot(float(v.cx or 0.0) - float(site_centroid.x), float(v.cy or 0.0) - float(site_centroid.y)) / 140.0
        ctx_bonus = {'urban_edge': 3.5, 'mixed_edge': 2.6, 'green_edge': 2.1, 'active_diagonal': 1.8, 'calm_inner': 1.6, 'barnehage_edge': 0.0, 'sensitive_edge': -2.0}.get(ctx, 1.0)
        candidates.append((ctx_bonus + north_bonus - center_penalty, v, field_cap))
    candidates.sort(key=lambda row: row[0], reverse=True)
    loops = 0
    idx = 0
    while current_bra < target_total_bra * 0.965 and candidates and loops < len(candidates) * 3:
        score, v, field_cap = candidates[idx % len(candidates)]
        if v.floors < field_cap and float(v.height_m or 0.0) + float(floor_to_floor_m or 3.2) <= float(max_height_m or 99.0) + 0.15:
            v.floors += 1
            v.height_m = round(float(v.floors) * float(floor_to_floor_m), 1)
            current_bra += float(v.footprint_m2 or 0.0) * float(v.bra_efficiency_ratio or 0.85)
        idx += 1
        loops += 1


def pass3_place_volumes(zones: List[TypologyZone], program: ProgramAllocation,
                        max_floors: int, max_height_m: float, max_bya_pct: float,
                        floor_to_floor_m: float,
                        neighbors: Optional[List[Dict[str, Any]]],
                        site_polygon, buildable_polygon,
                        site_inputs: Dict[str, Any]) -> List[Volume]:
    volumes = _ORIG_V8_PASS3_PLACE_VOLUMES(
        zones=zones,
        program=program,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
        neighbors=neighbors,
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        site_inputs=site_inputs,
    )
    fields = list(_CURRENT_DEVELOPMENT_FIELDS_V5 or [])
    if not fields:
        _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
        return volumes
    efficiency_ratio = _get_efficiency_ratio_v2(site_inputs)
    target_total_bra = float(program.total_bra or site_inputs.get('target_bra_m2', 0.0) or 0.0)
    volumes = _v8_add_clean_infill_bars(volumes, fields, target_total_bra, max_floors, max_height_m, floor_to_floor_m, efficiency_ratio)
    _v8_raise_floors_toward_target(volumes, fields, target_total_bra, max_floors, max_height_m, floor_to_floor_m)
    _apply_efficiency_ratio_to_volumes_v2(volumes, efficiency_ratio)
    _reestimate_units_v4(volumes, _CURRENT_AVG_UNIT_BRA_V4)
    return volumes


def _phase_groups_from_fields_v6(volumes: List[Volume], phasing_config: PhasingConfig) -> List[Tuple[List[str], List[Volume], List[str], int]]:
    """V8: én fase per delfelt som hovedregel; bare del ved tydelig overkapasitet."""
    field_map = {f.field_id: f for f in _CURRENT_DEVELOPMENT_FIELDS_V5}
    grouped: Dict[str, List[Volume]] = {}
    for v in volumes:
        fid = getattr(v, 'field_id', None) or getattr(v, 'zone_id', None) or 'uten_delfelt'
        grouped.setdefault(fid, []).append(v)
    groups: List[Tuple[List[str], List[Volume], List[str], int]] = []
    split_target = max(7600.0, float(phasing_config.MAX_PHASE_BRA or 6500.0) * 1.18)
    for fid, field_vols in grouped.items():
        field_vols = [v for v in field_vols if getattr(v, 'polygon', None) is not None]
        if not field_vols:
            continue
        field_obj = field_map.get(fid)
        field_name = getattr(field_obj, 'name', fid) if field_obj else fid
        order_hint = int(getattr(field_obj, 'phase_order_hint', 2) or 2) if field_obj else 2
        total_bra = sum(float(v.bra_m2 or 0.0) for v in field_vols)
        ctx = str(getattr(field_obj, 'context', '') or '') if field_obj else ''
        target_subphases = 1
        if total_bra > split_target and len(field_vols) >= 6 and ctx not in {'barnehage_edge', 'sensitive_edge'}:
            target_subphases = 2
        if target_subphases == 1:
            groups.append(([fid], list(field_vols), [field_name], order_hint))
            continue
        subgroups = _group_sorted_volumes_v6(field_vols, target_subphases)
        for idx, subgroup in enumerate(subgroups, start=1):
            gname = field_name if len(subgroups) == 1 else f"{field_name} del {idx}"
            groups.append(([fid], subgroup, [gname], order_hint))
    groups.sort(key=lambda item: (int(item[3]), -_phase_group_centroid_v7(item[1])[1], _phase_group_centroid_v7(item[1])[0]))
    # Hold samlet antall trinn nede på 4-5. Slå bare sammen nærmeste små grupper hvis vi går over 5.
    while len(groups) > 5:
        sizes = [sum(float(v.bra_m2 or 0.0) for v in g[1]) for g in groups]
        i = min(range(len(groups)), key=lambda k: sizes[k])
        cx, cy = _phase_group_centroid_v7(groups[i][1])
        best_j = None
        best_dist = float('inf')
        for j in range(len(groups)):
            if i == j:
                continue
            ocx, ocy = _phase_group_centroid_v7(groups[j][1])
            dist = math.hypot(cx - ocx, cy - ocy)
            if dist < best_dist:
                best_dist = dist
                best_j = j
        if best_j is None:
            break
        _merge_group_pair_v7(groups, i, best_j)
        groups.sort(key=lambda item: (int(item[3]), -_phase_group_centroid_v7(item[1])[1], _phase_group_centroid_v7(item[1])[0]))
    return groups


def pass4_phasing(volumes: List[Volume], buildable_polygon,
                  phasing_config: PhasingConfig, target_phase_count: int,
                  program: ProgramAllocation, site_polygon) -> Tuple[List[BuildingPhase], List[ParkingPhase]]:
    return _ORIG_V8_PASS4_PHASING(volumes, buildable_polygon, phasing_config, target_phase_count, program, site_polygon)


def plan_masterplan(*args, **kwargs) -> Masterplan:
    mp = _ORIG_V8_PLAN_MASTERPLAN(*args, **kwargs)
    try:
        target_bra = float((getattr(mp, 'site_inputs', {}) or {}).get('target_bra_m2', kwargs.get('target_bra_m2', 0.0)) or kwargs.get('target_bra_m2', 0.0) or 0.0)
        if mp.metrics is not None and target_bra > 0:
            mp.metrics.target_fit_pct = round(float(mp.metrics.total_bra or 0.0) / max(target_bra, 1.0) * 100.0, 1)
            mp.metrics.field_count = len(getattr(mp, 'development_fields', []) or [])
        if isinstance(getattr(mp, 'diag_info', None), dict):
            mp.diag_info['v8'] = '; '.join([
                f"måltreff={float(getattr(getattr(mp, 'metrics', None), 'target_fit_pct', 0.0) or 0.0):.1f}%",
                f"felt={len(getattr(mp, 'development_fields', []) or [])}",
                f"trinn={len(getattr(mp, 'building_phases', []) or [])}",
            ])
        mp.source = (getattr(mp, 'source', 'Builtly Masterplan') + ' + v8 structure/targetfit').strip()
    except Exception:
        pass
    return mp
