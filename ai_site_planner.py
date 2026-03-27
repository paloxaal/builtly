"""
AI-drevet tomteplanlegger for Builtly.

Bruker Claude (Anthropic API) til aa analysere tomtegeometri, nabobebyggelse,
terreng, solforhold og regulering — og generere realistiske bygningsplasseringer
som en arkitekt ville gjort, ikke bare et mekanisk rutenett.

Returnerer en liste med bygningsplasseringer som geometrimotoren
kan konvertere til Shapely-polygoner og bruke i volumstudie/3D.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    from shapely.geometry import Polygon
    from shapely import affinity
    HAS_SHAPELY = True
except Exception:
    HAS_SHAPELY = False

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-opus-4-6"


def _get_api_key() -> Optional[str]:
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")


def is_available() -> bool:
    return bool(_get_api_key())


def _safe_round(val: Any, decimals: int = 1) -> float:
    try:
        return round(float(val), decimals)
    except Exception:
        return 0.0


def _build_site_description(
    site_polygon: Any,
    buildable_polygon: Any,
    neighbors: List[Dict[str, Any]],
    terrain: Optional[Dict[str, Any]],
    site_intelligence: Optional[Dict[str, Any]],
    site_inputs: Optional[Dict[str, Any]],
    typology: str,
    target_bta_m2: float,
    max_floors: int,
    max_height_m: float,
    max_bya_pct: float,
    floor_to_floor_m: float,
) -> str:
    """Bygg en detaljert romlig beskrivelse for AI-arkitekten."""

    if HAS_SHAPELY and site_polygon is not None:
        site_area = float(site_polygon.area)
        bp_area = float(buildable_polygon.area) if buildable_polygon else site_area
        bounds = site_polygon.bounds
        site_width = bounds[2] - bounds[0]
        site_depth = bounds[3] - bounds[1]
        centroid = site_polygon.centroid
        cx, cy = centroid.x, centroid.y

        # BYGGBART POLYGONS FAKTISKE KOORDINATER — dette er noekkelen
        bp = buildable_polygon if buildable_polygon else site_polygon
        bp_coords = list(bp.exterior.coords)[:30]
        bp_coord_str = "\n".join([f"    ({c[0]:.1f}, {c[1]:.1f})" for c in bp_coords])
        bp_bounds = bp.bounds
        bp_minx, bp_miny, bp_maxx, bp_maxy = bp_bounds

        # Beregn plasseringssoner (del tomten i et grid)
        n_zones_x = max(2, int(site_width / 60))
        n_zones_y = max(2, int(site_depth / 60))
        zones = []
        zone_w = (bp_maxx - bp_minx) / n_zones_x
        zone_h = (bp_maxy - bp_miny) / n_zones_y
        from shapely.geometry import box as shapely_box
        for ix in range(n_zones_x):
            for iy in range(n_zones_y):
                zx = bp_minx + ix * zone_w + zone_w / 2
                zy = bp_miny + iy * zone_h + zone_h / 2
                zone_box = shapely_box(bp_minx + ix * zone_w, bp_miny + iy * zone_h,
                                       bp_minx + (ix+1) * zone_w, bp_miny + (iy+1) * zone_h)
                overlap = zone_box.intersection(bp)
                if overlap.area > zone_w * zone_h * 0.3:
                    zones.append({"cx": round(zx, 1), "cy": round(zy, 1),
                                  "w": round(zone_w, 1), "h": round(zone_h, 1),
                                  "area": round(float(overlap.area), 0)})
        zone_str = "\n".join([f"    Sone ({z['cx']:.0f}, {z['cy']:.0f}): {z['w']:.0f}x{z['h']:.0f}m, {z['area']:.0f}m2 tilgjengelig" for z in zones])
    else:
        site_area = float((site_inputs or {}).get('site_area_m2', 1000))
        bp_area = site_area * 0.85
        site_width = site_depth = math.sqrt(site_area)
        cx = cy = 0.0
        bp_coord_str = "    ikke tilgjengelig"
        bp_minx = bp_miny = 0.0
        bp_maxx = site_width
        bp_maxy = site_depth
        zone_str = "    Hele tomten"
        zones = []

    # Nabobygg med retning og posisjon
    neighbor_lines = []
    for nb in neighbors[:20]:
        h = float(nb.get('height_m', 9.0))
        d = float(nb.get('distance_m', 0.0))
        if HAS_SHAPELY and nb.get('polygon') and site_polygon:
            nc = nb['polygon'].centroid
            sc = site_polygon.centroid
            dx, dy = nc.x - sc.x, nc.y - sc.y
            angle = math.degrees(math.atan2(dy, dx))
            if angle < 0: angle += 360
            compass = ["ost", "nord-ost", "nord", "nord-vest", "vest", "sor-vest", "sor", "sor-ost"][int((angle + 22.5) / 45) % 8]
            neighbor_lines.append(f"    {compass}: {h:.0f}m hoyde, {d:.0f}m fra tomtegrense, pos ({nc.x:.0f}, {nc.y:.0f})")
        else:
            neighbor_lines.append(f"    {h:.0f}m hoyde, {d:.0f}m unna")
    neighbor_text = "\n".join(neighbor_lines[:15]) if neighbor_lines else "    Ingen naboer registrert"

    # Terreng
    terrain_text = "Flatt"
    if terrain and terrain.get('point_count', 0) > 0:
        terrain_text = f"Fall {terrain.get('slope_pct', 0):.1f}%, relieff {terrain.get('relief_m', 0):.1f}m, helning N-S {terrain.get('grade_ns_pct', 0):.2f}%, O-V {terrain.get('grade_ew_pct', 0):.2f}%"

    # Site intelligence
    si_text = ""
    if site_intelligence and site_intelligence.get('available'):
        transport = site_intelligence.get('transport', {})
        si_text = f"Mobilitet {transport.get('mobility_score', 50):.0f}/100, kollektiv innen 600m: {transport.get('transit_within_600_m', 0)}"

    # Typologispesifikke instruksjoner
    typo_instructions = {
        "Lamell": """LAMELL-REGLER:
    - Hver lamell: 35-55m lang, 12-14m dyp
    - Orienter ALLE lameller med langfasaden mot sor/sorvest for maks dagslys
    - Minimum 18m mellom parallelle lameller (TEK17 dagslys + utsyn)
    - Plasser i parallelle rader — IKKE i klynge
    - Spre radene over HELE tomtens bredde og dybde
    - Varier lengden paa lamellene for aa skape variasjon""",

        "Punkthus": """PUNKTHUS-REGLER:
    - Hvert punkthus: 16-22m x 16-22m (tilnaermet kvadratisk)
    - Minimum 22m mellom punkthus
    - Plasser i et aapent grid-monster over HELE tomten
    - Roter annen-hvert punkthus 15-30 grader for variasjon
    - La det vaere tydelige siktlinjer mellom bygningene
    - Posisjoner saa hvert hus faar sol fra minst to sider""",

        "Karré": """KARRE-REGLER:
    - Hvert kvartal: 40-50m ytre side, 10-12m ringdybde
    - Gaardrom i midten (ca 18-28m x 18-28m)
    - Aapning i sorvest-hjornet for sol inn i gaardsrommet
    - Sett "courtyard": true og "ring_depth": 11 i JSON
    - Minimum 20m mellom kvartaler
    - Plasser 2-4 kvartaler spredt over tomten""",

        "Tårn": """TAARN-REGLER:
    - Hvert taarn: 18-25m x 18-25m
    - Minimum 28m mellom taarn
    - Plasser spredt over hele tomten
    - Varier hoyde mellom taarnene (noen lavere, noen hoyere)
    - Orienter for aa unngaa skygge paa nabotaarn""",

        "Rekke": """REKKE-REGLER:
    - Hver rad: 40-60m lang, 9-11m dyp
    - Radene skal vaere parallelle
    - 14-18m mellom rader
    - Orienter radene ost-vest slik at alle faar sorfasade
    - Spre radene over HELE tomtens utstrekning""",

        "Tun": """TUN-REGLER:
    - Hovedbygg: 35-45m langt, 10-12m dypt
    - 1-2 sidefloyer vinkelrett paa hovedbygget, 20-30m lange
    - Floyene danner et beskyttet uterom (tun) aapent mot sor
    - Lag 2-3 separate tun-grupper spredt over tomten""",

        "Podium + Tårn": """PODIUM+TAARN-REGLER:
    - Podium: 40-55m x 18-25m, 2-3 etasjer
    - Taarn: 16-20m x 16-20m, plassert paa podiumet, full hoyde
    - Sett role="main" paa podium og role="tower" paa taarn
    - Lag 1-3 slike kombinasjoner spredt over tomten""",
    }

    typo_rules = typo_instructions.get(typology, "Plasser realistiske bygningskropper spredt over hele tomten.")

    # Beregn maks fotavtrykk og antall bygninger
    max_footprint = bp_area * (max_bya_pct / 100.0)
    floors_estimate = min(max_floors, max(2, int(target_bta_m2 / max(max_footprint, 1.0)) + 1))

    prompt = f"""Du er Norges beste arkitekt for volumstudier. Du planlegger bebyggelse som maksimerer
bokvalitet, dagslys, uterom og arealutnyttelse paa en reell tomt i Trondheim.

═══════════════════════════════════════════════════════
TOMTENS BYGGBARE OMRAADE (UTM33 EUREF89 koordinater)
═══════════════════════════════════════════════════════
Senterpunkt: ({cx:.1f}, {cy:.1f})
Byggbart areal: {bp_area:.0f} m2
Tomteareal totalt: {site_area:.0f} m2
Bounding box: x=[{bp_minx:.0f} til {bp_maxx:.0f}], y=[{bp_miny:.0f} til {bp_maxy:.0f}]
Bredde: {site_width:.0f}m, Dybde: {site_depth:.0f}m

Byggbart polygon (ALLE bygninger SKAL ligge INNENFOR disse koordinatene):
{bp_coord_str}

TILGJENGELIGE PLASSERINGSSONER (bruk disse som guide for aa spre bygninger):
{zone_str}

═══════════════════════════════════════════════════════
REGULERING
═══════════════════════════════════════════════════════
Maks BYA: {max_bya_pct:.0f}% (maks samlet fotavtrykk: {max_footprint:.0f} m2)
Maks etasjer: {max_floors}
Maks hoyde: {max_height_m:.0f}m
Onsket total BTA: {target_bta_m2:.0f} m2
Etasjehoyde: {floor_to_floor_m:.1f}m
Estimert etasjer for aa naa BTA: {floors_estimate}

═══════════════════════════════════════════════════════
NABOBEBYGGELSE
═══════════════════════════════════════════════════════
{neighbor_text}

═══════════════════════════════════════════════════════
TERRENG OG KONTEKST
═══════════════════════════════════════════════════════
Terreng: {terrain_text}
Breddegrad: {(site_inputs or {}).get('latitude_deg', 63.4):.1f} (sol staar lavt — sorfasade er kritisk)
{si_text}

═══════════════════════════════════════════════════════
TYPOLOGI: {typology.upper()}
═══════════════════════════════════════════════════════
{typo_rules}

═══════════════════════════════════════════════════════
KRITISKE REGLER (BRUDD = FORKASTET)
═══════════════════════════════════════════════════════
1. ALLE bygninger SKAL ha cx/cy-koordinater INNENFOR det byggbare polygonet ovenfor
2. Bygningene SKAL spre seg over HELE tomtens utstrekning — IKKE klumpe seg i ett hjorne
3. Bruk sonene ovenfor: plasser minst en bygning i HVER tilgjengelige sone der det er plass
4. Maks samlet fotavtrykk: {max_footprint:.0f} m2 (BYA {max_bya_pct:.0f}%)
5. Maks {max_floors} etasjer, maks {max_height_m:.0f}m hoyde per bygning
6. Minimum avstand mellom bygninger: se typologiregler ovenfor
7. Orienter for maks sol paa breddegrad {(site_inputs or {}).get('latitude_deg', 63.4):.1f}

═══════════════════════════════════════════════════════
SVAR-FORMAT
═══════════════════════════════════════════════════════
Returner BARE en JSON-array. Ingen annen tekst, ingen forklaring.

[
  {{
    "name": "Bygning A",
    "cx": {cx:.0f},
    "cy": {cy:.0f},
    "width": 45.0,
    "depth": 14.0,
    "angle_deg": 15.0,
    "floors": {floors_estimate},
    "role": "main",
    "notes": "Sorvest-orientert for optimal sol"
  }}
]

Felter: name (string), cx/cy (UTM33 floats), width/depth (meter), angle_deg (grader fra ost-aksen),
floors (int), role ("main"|"wing"|"tower"|"row"), notes (kort begrunnelse).
{"Legg til courtyard: true og ring_depth: 11 for karre-blokker." if typology == "Karré" else ""}
"""
    return prompt

def _call_claude(prompt: str, api_key: str, model: str = DEFAULT_MODEL) -> Optional[str]:
    """Kall Anthropic API og returner svarteksten."""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    try:
        resp = requests.post(ANTHROPIC_API_URL, headers=headers, json=body, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block["text"]
    except Exception as exc:
        print(f"[ai_site_planner] API-kall feilet: {exc}")
    return None


def _parse_buildings_json(text: str) -> List[Dict[str, Any]]:
    """Parse AI-svaret som JSON-array."""
    if not text:
        return []
    # Strip markdown fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    # Find JSON array
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start < 0 or end < 0:
        return []
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return []


def _buildings_to_polygons(
    buildings: List[Dict[str, Any]],
    buildable_polygon: Any,
    floor_to_floor_m: float = 3.2,
    max_height_m: float = 25.0,
) -> List[Dict[str, Any]]:
    """Konverter AI-genererte bygningsplasseringer til Shapely-polygoner."""
    if not HAS_SHAPELY:
        return []

    results: List[Dict[str, Any]] = []
    for bld in buildings:
        try:
            cx = float(bld["cx"])
            cy = float(bld["cy"])
            width = float(bld.get("width", 14.0))
            depth = float(bld.get("depth", 14.0))
            angle = float(bld.get("angle_deg", 0.0))
            floors = int(bld.get("floors", 4))
            name = str(bld.get("name", f"Bygning {len(results)+1}"))
            role = str(bld.get("role", "main"))
            notes = str(bld.get("notes", ""))

            # Valider dimensjoner
            width = max(6.0, min(65.0, width))
            depth = max(6.0, min(50.0, depth))
            floors = max(1, min(int(max_height_m / floor_to_floor_m), floors))
            height_m = floors * floor_to_floor_m

            # Lag polygon
            angle_rad = math.radians(angle)
            hw, hd = width / 2.0, depth / 2.0
            cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
            corners = [
                (cx + hw * cos_a - hd * sin_a, cy + hw * sin_a + hd * cos_a),
                (cx - hw * cos_a - hd * sin_a, cy - hw * sin_a + hd * cos_a),
                (cx - hw * cos_a + hd * sin_a, cy - hw * sin_a - hd * cos_a),
                (cx + hw * cos_a + hd * sin_a, cy + hw * sin_a - hd * cos_a),
            ]
            poly = Polygon(corners)
            if poly.is_empty or poly.area < 20:
                continue

            # Klipp til byggbart areal
            if buildable_polygon is not None:
                clipped = poly.intersection(buildable_polygon).buffer(0)
                if clipped.is_empty or clipped.area < 20:
                    continue
                poly = clipped

            # Haandter karre med gaardrom
            if bld.get("courtyard") or (role == "main" and "gardsrom" in notes.lower()):
                ring_depth = float(bld.get("ring_depth", 11.0))
                inner = poly.buffer(-ring_depth)
                if inner is not None and not inner.is_empty and inner.area > 30:
                    poly = poly.difference(inner).buffer(0)

            results.append({
                "polygon": poly,
                "name": name,
                "role": role,
                "floors": floors,
                "height_m": round(height_m, 1),
                "width_m": round(width, 1),
                "depth_m": round(depth, 1),
                "angle_deg": round(angle, 1),
                "area_m2": round(float(poly.area), 1),
                "notes": notes,
            })
        except (KeyError, ValueError, TypeError) as exc:
            continue

    return results


def plan_site(
    site_polygon: Any,
    buildable_polygon: Any,
    typology: str,
    *,
    neighbors: Optional[List[Dict[str, Any]]] = None,
    terrain: Optional[Dict[str, Any]] = None,
    site_intelligence: Optional[Dict[str, Any]] = None,
    site_inputs: Optional[Dict[str, Any]] = None,
    target_bta_m2: float = 5000.0,
    max_floors: int = 5,
    max_height_m: float = 16.0,
    max_bya_pct: float = 35.0,
    floor_to_floor_m: float = 3.2,
    model: str = DEFAULT_MODEL,
) -> Dict[str, Any]:
    """
    Hovedfunksjon: AI-drevet tomteplanlegging.

    Returnerer dict med:
        buildings: List[Dict] — bygningsplasseringer med Shapely-polygoner
        footprint: Polygon — samlet fotavtrykk (union av alle bygninger)
        raw_response: str — raa AI-svar for debugging
        prompt: str — prompten som ble sendt
        source: str — "AI (Claude)" eller "Fallback (geometrimotor)"
    """
    api_key = _get_api_key()
    if not api_key:
        return {"buildings": [], "footprint": None, "source": "Ingen API-noekkel",
                "error": "ANTHROPIC_API_KEY ikke satt"}

    prompt = _build_site_description(
        site_polygon=site_polygon,
        buildable_polygon=buildable_polygon,
        neighbors=neighbors or [],
        terrain=terrain,
        site_intelligence=site_intelligence,
        site_inputs=site_inputs,
        typology=typology,
        target_bta_m2=target_bta_m2,
        max_floors=max_floors,
        max_height_m=max_height_m,
        max_bya_pct=max_bya_pct,
        floor_to_floor_m=floor_to_floor_m,
    )

    raw = _call_claude(prompt, api_key, model=model)
    if not raw:
        return {"buildings": [], "footprint": None, "source": "API-kall feilet",
                "prompt": prompt, "error": "Ingen svar fra Claude"}

    parsed = _parse_buildings_json(raw)
    if not parsed:
        return {"buildings": [], "footprint": None, "source": "Parsing feilet",
                "prompt": prompt, "raw_response": raw,
                "error": "Kunne ikke parse JSON fra AI-svar"}

    buildings = _buildings_to_polygons(
        parsed, buildable_polygon,
        floor_to_floor_m=floor_to_floor_m,
        max_height_m=max_height_m,
    )

    footprint = None
    if buildings and HAS_SHAPELY:
        from shapely.ops import unary_union
        polys = [b["polygon"] for b in buildings if b.get("polygon")]
        if polys:
            footprint = unary_union(polys).buffer(0)

    return {
        "buildings": buildings,
        "footprint": footprint,
        "building_count": len(buildings),
        "total_footprint_m2": round(sum(b.get("area_m2", 0) for b in buildings), 1),
        "total_bta_m2": round(sum(b.get("area_m2", 0) * b.get("floors", 1) for b in buildings), 1),
        "source": f"AI (Claude {model})",
        "prompt": prompt,
        "raw_response": raw,
        "raw_parsed": parsed,
    }
