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
    """Bygg en strukturert beskrivelse av tomten for AI-modellen."""

    # Tomt-dimensjoner
    if HAS_SHAPELY and site_polygon is not None:
        from shapely.geometry import Polygon as ShapelyPolygon
        site_area = float(site_polygon.area)
        bp_area = float(buildable_polygon.area) if buildable_polygon else site_area
        bounds = site_polygon.bounds
        site_width = bounds[2] - bounds[0]
        site_depth = bounds[3] - bounds[1]

        # Tomtens hjornepunkter (forenklet)
        coords = list(site_polygon.exterior.coords)[:20]
        coord_str = ", ".join([f"({c[0]:.1f}, {c[1]:.1f})" for c in coords[:8]])
    else:
        site_area = float((site_inputs or {}).get('site_area_m2', 1000))
        bp_area = site_area * 0.85
        site_width = site_depth = math.sqrt(site_area)
        coord_str = "ikke tilgjengelig"

    # Nabobygg-sammendrag
    neighbor_summary = []
    for nb in neighbors[:25]:
        h = float(nb.get('height_m', 9.0))
        d = float(nb.get('distance_m', 0.0))
        a = float(getattr(nb.get('polygon'), 'area', 0.0)) if nb.get('polygon') else 0.0
        direction = ""
        if HAS_SHAPELY and nb.get('polygon') and site_polygon:
            nc = nb['polygon'].centroid
            sc = site_polygon.centroid
            dx, dy = nc.x - sc.x, nc.y - sc.y
            if abs(dx) > abs(dy):
                direction = "ost" if dx > 0 else "vest"
            else:
                direction = "nord" if dy > 0 else "sor"
        neighbor_summary.append(f"  - {direction}: {h:.0f}m hoey, {d:.0f}m unna, {a:.0f}m2 fotavtrykk")

    neighbor_text = "\n".join(neighbor_summary[:15]) if neighbor_summary else "  Ingen nabobygg registrert."

    # Terreng
    terrain_text = "Flatt (ingen terrengdata)"
    if terrain and terrain.get('point_count', 0) > 0:
        terrain_text = (
            f"Fall: {terrain.get('slope_pct', 0):.1f}%, "
            f"relieff: {terrain.get('relief_m', 0):.1f}m, "
            f"helning ost-vest: {terrain.get('grade_ew_pct', 0):.2f}%, "
            f"helning nord-sor: {terrain.get('grade_ns_pct', 0):.2f}%"
        )

    # Site intelligence
    si_text = "Ingen site intelligence tilgjengelig."
    if site_intelligence and site_intelligence.get('available'):
        plan = site_intelligence.get('plan', {})
        transport = site_intelligence.get('transport', {})
        si_text = (
            f"Site score: {site_intelligence.get('site_score', 0):.0f}/100\n"
            f"  Regulering: {plan.get('dominant_plan', 'ukjent')}\n"
            f"  Risikoflagger: {', '.join(plan.get('risk_flags', [])) or 'ingen'}\n"
            f"  Kollektiv innen 600m: {transport.get('transit_within_600_m', 0)}\n"
            f"  Mobilitetsscore: {transport.get('mobility_score', 50):.0f}/100\n"
            f"  Vegadkomst: {'ja' if transport.get('road_access') else 'nei'}"
        )

    prompt = f"""Du er en erfaren norsk arkitekt som gjor volumstudier og mulighetsstudier for boligutvikling.

TOMT:
  Totalareal: {site_area:.0f} m2
  Byggbart areal (etter inntrekk): {bp_area:.0f} m2
  Bredde: {site_width:.0f}m, Dybde: {site_depth:.0f}m
  Koordinater (UTM33): {coord_str}
  Breddegrad: {(site_inputs or {}).get('latitude_deg', 63.4):.2f} (sol/skygge)

REGULERING:
  Maks BYA: {max_bya_pct:.0f}%
  Maks etasjer: {max_floors}
  Maks hoyde: {max_height_m:.1f}m
  Onsket BTA: {target_bta_m2:.0f} m2
  Etasjehoyde: {floor_to_floor_m:.1f}m

NABOBEBYGGELSE (retning, hoyde, avstand, fotavtrykk):
{neighbor_text}

TERRENG:
  {terrain_text}

STEDSKONTEKST:
  {si_text}

TYPOLOGI: {typology}

OPPGAVE:
Plasser bygninger av typen "{typology}" paa denne tomten. Tenk som en arkitekt:
- Maksimer dagslys og soltilgang (breddegrad {(site_inputs or {}).get('latitude_deg', 63.4):.1f})
- Skap gode uterom og siktlinjer mellom bygningene
- Respekter avstand til naboer og byggegrenser
- Optimaliser for den valgte typologien
- Tilpass til terrengfall
- Orienter bygningene for best mulig sol (sorvest-eksponering er ideelt paa denne breddegraden)

{"For Karre: lag kvartaler med gaardrom, typisk 35-50m ytre side, 10-12m ringbredde." if typology == "Karré" else ""}
{"For Tun: lag L- eller U-form med tydelig beskyttet uterom." if typology == "Tun" else ""}
{"For Podium + Taarn: lag et lavt podium (2-3 etasjer) med et smalere taarn (full hoyde) oppaa." if typology == "Podium + Tårn" else ""}

Returner BARE en JSON-array med bygninger. Ingen annen tekst.
Hvert element:
{{
  "name": "Bygning A",
  "cx": <UTM33 x-koordinat for senterpunkt>,
  "cy": <UTM33 y-koordinat for senterpunkt>,
  "width": <bredde i meter (langs fasade)>,
  "depth": <dybde i meter (vinkelrett paa fasade)>,
  "angle_deg": <orientering i grader fra nord>,
  "floors": <antall etasjer>,
  "role": "main" | "wing" | "tower" | "row",
  "notes": "<kort arkitektfaglig begrunnelse>"
}}

Regler:
- Lamell: maks 55m bred, 12-14m dyp
- Punkthus: 16-22m x 16-22m
- Karre: 35-50m per side, ring 10-12m, med gaardrom
- Taarn: 18-25m x 18-25m
- Rekke: rader paa maks 60m, 9-11m dype, 14m mellom rader
- Alle bygninger SKAL ligge innenfor byggbart areal
- Maks {max_floors} etasjer, maks {max_height_m:.0f}m hoyde
- Minimum 16m avstand mellom parallelle bygningsfasader (dagslysregel TEK17)
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
