"""
AI-drevet tomteplanlegger for Builtly — v3 (multi-pass).

ARKITEKTUR (4-pass):
  Pass 1: Claude velger bebyggelseskonsept basert på typologi, tomt og kontekst
  Pass 2: Python plasserer bygninger deterministisk med Shapely-geometri
  Pass 3: Claude finjusterer — rotasjon, høydevariasjon, gårdsrom-åpninger
  Pass 4: Python validerer og auto-fikser alt (containment, spacing, BYA, overlap)

Eksporterer plan_site() med identisk signatur og retur-format som v2.
"""

from __future__ import annotations
import json, math, os, logging
from typing import Any, Dict, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

try:
    from shapely.geometry import Polygon, box as shapely_box, Point, MultiPolygon
    from shapely import affinity
    from shapely.ops import unary_union
    HAS_SHAPELY = True
except Exception:
    HAS_SHAPELY = False

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-opus-4-6"

# ──────────────────────────────────────────────
# Typology constraints
# ──────────────────────────────────────────────

TYPOLOGY_DIMS = {
    "Lamell":         {"w_min": 30, "w_max": 60, "d_min": 12, "d_max": 16, "f_min": 3, "f_max": 7,  "ftf": 3.2, "units_per_floor": 3},
    "Punkthus":       {"w_min": 18, "w_max": 26, "d_min": 18, "d_max": 26, "f_min": 4, "f_max": 12, "ftf": 3.0, "units_per_floor": 3},
    "Karré":          {"w_min": 38, "w_max": 55, "d_min": 38, "d_max": 55, "f_min": 3, "f_max": 7,  "ftf": 3.2, "units_per_floor": 6},
    "Rekke":          {"w_min": 35, "w_max": 60, "d_min": 8,  "d_max": 12, "f_min": 2, "f_max": 3,  "ftf": 2.8, "units_per_floor": 1},
    "Tun":            {"w_min": 25, "w_max": 50, "d_min": 10, "d_max": 14, "f_min": 3, "f_max": 6,  "ftf": 3.0, "units_per_floor": 3},
    "Tårn":           {"w_min": 18, "w_max": 26, "d_min": 18, "d_max": 26, "f_min": 6, "f_max": 16, "ftf": 3.0, "units_per_floor": 4},
    "Podium + Tårn":  {"w_min": 35, "w_max": 55, "d_min": 18, "d_max": 28, "f_min": 2, "f_max": 4,  "ftf": 3.5, "units_per_floor": 0},
}

MIN_BUILDING_SPACING = 8.0
MIN_BOUNDARY_SETBACK = 4.0

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _get_api_key():
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")

def is_available():
    return bool(_get_api_key())

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))

def _call_claude(prompt, api_key, model=DEFAULT_MODEL, temperature=0.3, max_tokens=4000):
    try:
        resp = requests.post(ANTHROPIC_API_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}], "temperature": temperature},
            timeout=120)
        resp.raise_for_status()
        for block in resp.json().get("content", []):
            if block.get("type") == "text":
                return block["text"]
    except Exception as exc:
        logger.error(f"Claude API error: {exc}")
    return None

def _parse_json(text):
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(l for l in cleaned.split("\n") if not l.strip().startswith("```"))
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        s = cleaned.find(start_char)
        e = cleaned.rfind(end_char)
        if s >= 0 and e > s:
            try:
                return json.loads(cleaned[s:e+1])
            except json.JSONDecodeError:
                continue
    return None

def _make_polygon(cx, cy, width, depth, angle_deg):
    """Create a rotated rectangle polygon."""
    if not HAS_SHAPELY:
        return None
    b = shapely_box(-width/2, -depth/2, width/2, depth/2)
    b = affinity.rotate(b, angle_deg, origin=(0, 0))
    b = affinity.translate(b, cx, cy)
    return b if b.is_valid and not b.is_empty else None

def _compute_neighbor_context(neighbors, site_polygon):
    """Build neighbor summary for prompts."""
    if not neighbors or not site_polygon:
        return "NABOER: Ingen nære", []

    nb_polys = []
    nb_lines = []
    for nb in (neighbors or [])[:20]:
        p = nb.get('polygon')
        if not p or p.is_empty:
            continue
        if site_polygon and p.intersection(site_polygon).area / max(float(p.area), 1.0) >= 0.3:
            continue
        nc = p.centroid
        sc = site_polygon.centroid
        dist = nc.distance(sc)
        ang = math.degrees(math.atan2(nc.y - sc.y, nc.x - sc.x)) % 360
        compass = ["Ø","NØ","N","NV","V","SV","S","SØ"][int((ang + 22.5) / 45) % 8]
        h = nb.get('height_m', 9.0)
        nb_lines.append(f"  {compass}: {h:.0f}m høy, {dist:.0f}m unna")
        nb_polys.append({'polygon': p, 'height_m': float(h)})

    text = "NABOER (urørlige):\n" + "\n".join(nb_lines[:10]) if nb_lines else "NABOER: Ingen nære"
    return text, nb_polys


# ──────────────────────────────────────────────
# PASS 1: Claude Concept
# ──────────────────────────────────────────────

def _pass1_concept(typology, site_area, buildable_area, buildable_polygon,
                   max_bya_pct, max_floors, max_height_m, target_bta,
                   floor_to_floor_m, latitude_deg, terrain, neighbor_text):
    """Ask Claude for a high-level placement concept."""

    api_key = _get_api_key()
    max_footprint = buildable_area * (max_bya_pct / 100.0)
    fl_est = min(max_floors, max(2, int(target_bta / max(max_footprint, 1.0)) + 1))

    bounds = buildable_polygon.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]
    coords = [[round(c[0], 1), round(c[1], 1)] for c in buildable_polygon.exterior.coords]

    terrain_text = "Flatt"
    if terrain and terrain.get('point_count', 0) > 0:
        terrain_text = f"Fall {terrain.get('slope_pct', 0):.1f}%, relieff {terrain.get('relief_m', 0):.1f}m"

    dims = TYPOLOGY_DIMS.get(typology, TYPOLOGY_DIMS["Lamell"])

    prompt = f"""Du er Norges fremste arkitekt for boligutvikling og volumstudier.

TOMT:
- Totalareal: {site_area:.0f} m²
- Byggbart areal: {buildable_area:.0f} m² (etter setback)
- Byggbart polygon (meter): {json.dumps(coords)}
- Bounding box: {bw:.0f} x {bh:.0f} m
- Terreng: {terrain_text}
- Breddegrad: {latitude_deg:.1f}° (lav sol, sørfasade er livsnødvendig)

REGULERING:
- Maks BYA: {max_bya_pct:.0f}% = {max_footprint:.0f} m² fotavtrykk
- Maks etasjer: {max_floors}, maks høyde: {max_height_m:.0f}m
- Etasjehøyde: {floor_to_floor_m}m
- Mål: {target_bta:.0f} m² BTA → estimert ~{fl_est} etasjer

{neighbor_text}

TYPOLOGI: {typology}

TYPOLOGIDIMENSJONER:
- Bredde: {dims['w_min']}-{dims['w_max']}m
- Dybde: {dims['d_min']}-{dims['d_max']}m
- Etasjer: {dims['f_min']}-{dims['f_max']}

OPPGAVE:
Bestem et bebyggelseskonsept. Du skal IKKE plassere bygninger med koordinater.
Du skal velge:
1. Antall bygninger
2. Dimensjoner for hver (bredde, dybde, etasjer)
3. Overordnet organiseringsprinsipp (grid-retning, forskyvning, åpninger)
4. Rollefordeling (main/wing/tower)
5. Rotasjonsvinkel (angle_deg) — langfasade bør ha søreksponering

Svar BARE med JSON (ingen markdown):
{{
  "strategy": "kort beskrivelse av konseptet",
  "orientation_deg": 195,
  "grid_direction": "north-south",
  "stagger_m": 10,
  "buildings": [
    {{
      "name": "Lamell A",
      "role": "main",
      "width": 45,
      "depth": 14,
      "floors": {fl_est},
      "angle_deg": 195,
      "courtyard": false,
      "ring_depth": 0,
      "notes": "Nordligste lamell, rammer inn parkdraget"
    }}
  ],
  "open_south": true,
  "notes": "..."
}}"""

    raw = _call_claude(prompt, api_key, temperature=0.3, max_tokens=3000)
    concept = _parse_json(raw)

    if not concept or not concept.get("buildings"):
        logger.warning("Pass 1: Claude concept failed, using defaults")
        concept = _default_concept(typology, buildable_area, max_footprint, fl_est, dims)

    # Clamp all building dimensions
    for b in concept.get("buildings", []):
        b["width"] = _clamp(b.get("width", dims["w_min"]), dims["w_min"], dims["w_max"])
        b["depth"] = _clamp(b.get("depth", dims["d_min"]), dims["d_min"], dims["d_max"])
        b["floors"] = _clamp(b.get("floors", fl_est), dims["f_min"], min(max_floors, dims["f_max"]))
        b["angle_deg"] = b.get("angle_deg", 0)
        b["role"] = b.get("role", "main")
        b["name"] = b.get("name", f"{typology} {concept['buildings'].index(b)+1}")

    logger.info(f"Pass 1: {concept.get('strategy', 'N/A')} — {len(concept.get('buildings', []))} bygninger")
    return concept


def _default_concept(typology, buildable_area, max_footprint, fl_est, dims):
    """Fallback concept if Claude fails."""
    w = (dims["w_min"] + dims["w_max"]) / 2
    d = (dims["d_min"] + dims["d_max"]) / 2
    fp_per_bld = w * d
    count = max(2, min(10, int(max_footprint / fp_per_bld)))

    buildings = []
    for i in range(count):
        buildings.append({
            "name": f"{typology} {i+1}",
            "role": "main",
            "width": w, "depth": d,
            "floors": fl_est,
            "angle_deg": 0,
            "courtyard": typology == "Karré",
            "ring_depth": 11 if typology == "Karré" else 0,
            "notes": "",
        })

    return {
        "strategy": f"Standard {typology.lower()}-plassering",
        "orientation_deg": 0,
        "grid_direction": "north-south",
        "stagger_m": 0,
        "buildings": buildings,
        "notes": "Fallback",
    }


# ──────────────────────────────────────────────
# PASS 2: Deterministic Python Placement
# ──────────────────────────────────────────────

def _pass2_place(concept, buildable_polygon, max_bya_m2, typology, floor_to_floor_m):
    """Place buildings deterministically within the buildable polygon."""

    inset = buildable_polygon
    if inset.is_empty:
        return []

    if isinstance(inset, MultiPolygon):
        inset = max(inset.geoms, key=lambda g: g.area)

    bounds = inset.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]

    spec_buildings = concept.get("buildings", [])
    orientation = concept.get("orientation_deg", 0)
    grid_dir = concept.get("grid_direction", "north-south")
    stagger = concept.get("stagger_m", 0)

    placed = []
    placed_polys = []
    total_fp = 0.0

    for idx, spec in enumerate(spec_buildings):
        w = spec["width"]
        d = spec["depth"]
        angle = spec.get("angle_deg", orientation)
        is_karre = spec.get("courtyard", False) or typology == "Karré"
        ring_depth = spec.get("ring_depth", 11 if is_karre else 0)

        if total_fp + w * d > max_bya_m2 * 1.02:
            logger.info(f"Pass 2: BYA limit reached at building {idx+1}")
            break

        pos = _find_best_position(inset, w, d, angle, placed_polys, idx, len(spec_buildings),
                                  grid_dir, stagger, bw, bh, bounds)
        if pos is None:
            pos = _find_best_position(inset, w, d, angle + 90, placed_polys, idx, len(spec_buildings),
                                      grid_dir, stagger, bw, bh, bounds)
        if pos is None:
            dims = TYPOLOGY_DIMS.get(typology, {})
            w2 = max(dims.get("w_min", 12), w * 0.75)
            d2 = max(dims.get("d_min", 8), d * 0.75)
            pos = _find_best_position(inset, w2, d2, angle, placed_polys, idx, len(spec_buildings),
                                      grid_dir, stagger, bw, bh, bounds)
            if pos:
                w, d = w2, d2

        if pos is None:
            logger.warning(f"Pass 2: Could not place {spec.get('name', idx)}")
            continue

        cx, cy, final_angle = pos
        poly = _make_polygon(cx, cy, w, d, final_angle)
        if poly is None:
            continue

        clipped = poly.intersection(inset).buffer(0)
        if clipped.is_empty or clipped.area < 20:
            continue

        # Handle karré courtyard
        actual_fp = clipped.area
        if is_karre and clipped.area > 300:
            rd = max(8, min(float(ring_depth), math.sqrt(clipped.area) * 0.22))
            inner = clipped.buffer(-rd)
            if inner and not inner.is_empty and inner.area > 40:
                clipped = clipped.difference(inner).buffer(0)
                actual_fp = clipped.area  # karré footprint is the ring, not the full box

        floors = spec.get("floors", 4)
        ftf = TYPOLOGY_DIMS.get(typology, {}).get("ftf", floor_to_floor_m)
        height_m = floors * ftf

        placed.append({
            "polygon": clipped,
            "name": spec.get("name", f"{typology} {idx+1}"),
            "role": spec.get("role", "main"),
            "floors": floors,
            "height_m": round(height_m, 1),
            "width_m": round(w, 1),
            "depth_m": round(d, 1),
            "angle_deg": round(final_angle, 1),
            "area_m2": round(float(actual_fp), 1),
            "notes": spec.get("notes", ""),
            "cx": round(cx, 1),
            "cy": round(cy, 1),
            "courtyard": is_karre,
            "ring_depth": ring_depth,
        })
        placed_polys.append(poly)  # Use un-hollowed poly for spacing checks
        total_fp += actual_fp

    logger.info(f"Pass 2: Placed {len(placed)}/{len(spec_buildings)} buildings, BYA={total_fp:.0f} m²")
    return placed


def _find_best_position(inset, width, depth, angle_deg, placed_polys,
                        bld_idx, bld_count, grid_dir, stagger,
                        bw, bh, bounds):
    """Find best position using structured grid-cell assignment."""
    rad = math.radians(angle_deg)
    eff_w = abs(width * math.cos(rad)) + abs(depth * math.sin(rad))
    eff_d = abs(width * math.sin(rad)) + abs(depth * math.cos(rad))

    spacing = MIN_BUILDING_SPACING

    # Grid layout based on building count and site shape
    if bld_count <= 1:
        cols, rows = 1, 1
    elif bld_count <= 3:
        if grid_dir == "north-south":
            cols, rows = 1, bld_count
        else:
            cols, rows = bld_count, 1
    elif bld_count <= 6:
        if bw > bh * 1.3:
            cols = min(bld_count, 3)
            rows = math.ceil(bld_count / cols)
        elif bh > bw * 1.3:
            rows = min(bld_count, 3)
            cols = math.ceil(bld_count / rows)
        else:
            cols = math.ceil(math.sqrt(bld_count))
            rows = math.ceil(bld_count / cols)
    else:
        if bw > bh:
            cols = max(2, min(5, math.ceil(math.sqrt(bld_count * bw / max(bh, 1)))))
        else:
            cols = max(2, min(4, math.ceil(math.sqrt(bld_count))))
        rows = math.ceil(bld_count / cols)

    col = bld_idx % cols
    row = bld_idx // cols

    cell_w = bw / cols
    cell_h = bh / rows
    cell_cx = bounds[0] + (col + 0.5) * cell_w
    cell_cy = bounds[1] + (row + 0.5) * cell_h

    if row % 2 == 1 and stagger:
        cell_cx += stagger

    # Search with expanding radius
    search_radii = [0, cell_w * 0.3, cell_w * 0.5, max(cell_w, cell_h) * 0.8, max(bw, bh) * 0.4]

    for radius in search_radii:
        candidates = _generate_candidates(cell_cx, cell_cy, radius, eff_w, eff_d, spacing)

        for cx, cy in candidates:
            poly = _make_polygon(cx, cy, width, depth, angle_deg)
            if poly is None:
                continue
            if not inset.contains(poly):
                continue
            too_close = False
            for pp in placed_polys:
                if poly.distance(pp) < spacing:
                    too_close = True
                    break
            if too_close:
                continue
            return (cx, cy, angle_deg)

    return None


def _generate_candidates(cx, cy, radius, eff_w, eff_d, spacing):
    """Generate candidate positions around a center point."""
    if radius < 1:
        return [(cx, cy)]

    step = max(eff_w, eff_d) * 0.4 + spacing * 0.3
    candidates = [(cx, cy)]

    steps = max(1, int(radius / step))
    for i in range(-steps, steps + 1):
        for j in range(-steps, steps + 1):
            if i == 0 and j == 0:
                continue
            px = cx + i * step
            py = cy + j * step
            if math.sqrt((px - cx)**2 + (py - cy)**2) <= radius * 1.2:
                candidates.append((px, py))

    candidates.sort(key=lambda p: (p[0] - cx)**2 + (p[1] - cy)**2)
    return candidates


# ──────────────────────────────────────────────
# PASS 3: Claude Refinement
# ──────────────────────────────────────────────

def _pass3_refine(buildings, buildable_polygon, max_floors, max_height_m,
                  latitude_deg, neighbor_text, typology, floor_to_floor_m):
    """Ask Claude to refine the deterministic placements."""

    if not buildings:
        return buildings

    api_key = _get_api_key()
    bounds = buildable_polygon.bounds

    bld_json = []
    for b in buildings:
        bld_json.append({
            "name": b["name"],
            "role": b["role"],
            "cx": b["cx"], "cy": b["cy"],
            "width": b["width_m"], "depth": b["depth_m"],
            "angle_deg": b["angle_deg"],
            "floors": b["floors"],
            "area_m2": b["area_m2"],
        })

    prompt = f"""Du er en norsk arkitekt som kvalitetssikrer en maskinell bygningsplassering for {typology}.

TOMT (byggbart): {buildable_polygon.area:.0f} m²
Bounds: ({bounds[0]:.0f},{bounds[1]:.0f}) til ({bounds[2]:.0f},{bounds[3]:.0f})
BREDDEGRAD: {latitude_deg:.1f}° | MAKS: {max_floors} etasjer / {max_height_m:.0f}m
{neighbor_text}

PLASSERTE BYGNINGER:
{json.dumps(bld_json, indent=2)}

JUSTER for bedre arkitektonisk kvalitet:
1. angle_deg — roter for søreksponering (langfasade ~180-200°)
2. floors — varier for skyline (lavere mot sør for å slippe sol inn, høyere mot nord)
3. cx/cy — flytt inntil ±8m for å åpne siktlinjer/gårdsrom mot sør
4. remove: true — BARE hvis det tydelig forbedrer helheten

REGLER: Ikke endre width/depth. Hold min 8m mellom bygninger.

Svar BARE med JSON-array:
[{{"name":"...","angle_deg":195,"floors":4,"cx":50.5,"cy":30.2,"remove":false}}]"""

    raw = _call_claude(prompt, api_key, temperature=0.2, max_tokens=3000)
    adjustments = _parse_json(raw)

    if not adjustments or not isinstance(adjustments, list):
        logger.warning("Pass 3: Refinement failed, keeping original")
        return buildings

    adj_map = {a.get("name"): a for a in adjustments}

    refined = []
    for b in buildings:
        adj = adj_map.get(b["name"], {})

        if adj.get("remove", False):
            logger.info(f"Pass 3: Removing {b['name']}")
            continue

        if "angle_deg" in adj:
            b["angle_deg"] = round(float(adj["angle_deg"]), 1)

        if "floors" in adj:
            dims = TYPOLOGY_DIMS.get(typology, {})
            new_floors = _clamp(int(adj["floors"]), dims.get("f_min", 2), min(max_floors, dims.get("f_max", 8)))
            b["floors"] = new_floors
            b["height_m"] = round(new_floors * dims.get("ftf", floor_to_floor_m), 1)

        if "cx" in adj:
            dx = _clamp(float(adj["cx"]) - b["cx"], -8.0, 8.0)
            b["cx"] = round(b["cx"] + dx, 1)
        if "cy" in adj:
            dy = _clamp(float(adj["cy"]) - b["cy"], -8.0, 8.0)
            b["cy"] = round(b["cy"] + dy, 1)

        # Rebuild polygon
        new_poly = _make_polygon(b["cx"], b["cy"], b["width_m"], b["depth_m"], b["angle_deg"])
        if new_poly and not new_poly.is_empty:
            clipped = new_poly.intersection(buildable_polygon).buffer(0)
            if not clipped.is_empty and clipped.area > 20:
                is_karre = b.get("courtyard", False)
                if is_karre and clipped.area > 300:
                    rd = max(8, min(float(b.get("ring_depth", 11)), math.sqrt(clipped.area) * 0.22))
                    inner = clipped.buffer(-rd)
                    if inner and not inner.is_empty and inner.area > 40:
                        clipped = clipped.difference(inner).buffer(0)
                b["polygon"] = clipped
                b["area_m2"] = round(float(clipped.area), 1)

        refined.append(b)

    logger.info(f"Pass 3: {len(refined)} buildings after refinement")
    return refined


# ──────────────────────────────────────────────
# PASS 4: Validation & Auto-Fix
# ──────────────────────────────────────────────

def _pass4_validate(buildings, buildable_polygon, max_bya_m2, max_floors, max_height_m, floor_to_floor_m):
    """Hard validation and auto-fix."""

    if not buildings:
        return []

    # 1. Containment
    valid = []
    for b in buildings:
        poly = b.get("polygon")
        if poly is None or poly.is_empty:
            continue

        if not buildable_polygon.contains(poly):
            clipped = poly.intersection(buildable_polygon).buffer(0)
            if clipped.is_empty or clipped.area < 20:
                pushed = _push_toward_centroid(b, buildable_polygon)
                if pushed:
                    b = pushed
                else:
                    logger.warning(f"Pass 4: {b['name']} removed — outside")
                    continue
            else:
                b["polygon"] = clipped
                b["area_m2"] = round(float(clipped.area), 1)
        valid.append(b)
    buildings = valid

    # 2. Floor/height limits
    for b in buildings:
        if b["floors"] > max_floors:
            b["floors"] = max_floors
            b["height_m"] = round(max_floors * floor_to_floor_m, 1)
        if b["height_m"] > max_height_m:
            b["height_m"] = max_height_m
            b["floors"] = max(1, int(max_height_m / floor_to_floor_m))

    # 3. Overlap removal (keep larger)
    buildings.sort(key=lambda b: b["area_m2"], reverse=True)
    no_overlap = []
    no_overlap_polys = []
    for b in buildings:
        poly = b["polygon"]
        overlaps = False
        for op in no_overlap_polys:
            if poly.intersects(op) and poly.intersection(op).area > 2.0:
                overlaps = True
                break
        if not overlaps:
            no_overlap.append(b)
            no_overlap_polys.append(poly)
    buildings = no_overlap

    # 4. Spacing enforcement
    spaced = []
    spaced_polys = []
    for b in buildings:
        too_close = False
        for sp in spaced_polys:
            if b["polygon"].distance(sp) < MIN_BUILDING_SPACING * 0.9:
                too_close = True
                break
        if not too_close:
            spaced.append(b)
            spaced_polys.append(b["polygon"])
    buildings = spaced

    # 5. BYA cap
    total_fp = sum(b["area_m2"] for b in buildings)
    buildings.sort(key=lambda b: b["area_m2"])
    while total_fp > max_bya_m2 * 1.05 and buildings:
        removed = buildings.pop(0)
        total_fp -= removed["area_m2"]
    buildings.sort(key=lambda b: b["name"])

    logger.info(f"Pass 4: {len(buildings)} buildings, FP={sum(b['area_m2'] for b in buildings):.0f} m²")
    return buildings


def _push_toward_centroid(b, buildable_polygon):
    """Push building toward polygon centroid until it fits."""
    cx, cy = b["cx"], b["cy"]
    tc = buildable_polygon.centroid
    dx = tc.x - cx
    dy = tc.y - cy
    dist = math.sqrt(dx**2 + dy**2)
    if dist < 0.1:
        return None

    for step in [2, 5, 10, 15, 20, 30]:
        nx = cx + dx / dist * step
        ny = cy + dy / dist * step
        poly = _make_polygon(nx, ny, b["width_m"], b["depth_m"], b["angle_deg"])
        if poly and buildable_polygon.contains(poly):
            b_copy = dict(b)
            b_copy["cx"] = round(nx, 1)
            b_copy["cy"] = round(ny, 1)
            clipped = poly.intersection(buildable_polygon).buffer(0)
            is_karre = b.get("courtyard", False)
            if is_karre and clipped.area > 300:
                rd = max(8, min(float(b.get("ring_depth", 11)), math.sqrt(clipped.area) * 0.22))
                inner = clipped.buffer(-rd)
                if inner and not inner.is_empty and inner.area > 40:
                    clipped = clipped.difference(inner).buffer(0)
            b_copy["polygon"] = clipped
            b_copy["area_m2"] = round(float(clipped.area), 1)
            return b_copy
    return None


# ──────────────────────────────────────────────
# Main: plan_site()
# ──────────────────────────────────────────────

def plan_site(site_polygon, buildable_polygon, typology, *, neighbors=None, terrain=None,
              site_intelligence=None, site_inputs=None, target_bta_m2=5000.0, max_floors=5,
              max_height_m=16.0, max_bya_pct=35.0, floor_to_floor_m=3.2, model=DEFAULT_MODEL):
    """
    Run the 4-pass site planner.

    Returns dict compatible with Mulighetsstudie.py:
      buildings: list of dicts with polygon, name, role, floors, height_m, width_m, depth_m, angle_deg, area_m2, notes, pos_id
      footprint: unary_union Shapely polygon
      building_count, total_footprint_m2, total_bta_m2, source
    """
    api_key = _get_api_key()
    if not api_key:
        return {"buildings": [], "footprint": None, "error": "ANTHROPIC_API_KEY ikke satt"}
    if not HAS_SHAPELY or buildable_polygon is None:
        return {"buildings": [], "footprint": None, "error": "Shapely/polygon mangler"}

    latitude = float((site_inputs or {}).get('latitude_deg', 63.4))
    site_area = float(site_polygon.area) if site_polygon else 1000
    bp_area = float(buildable_polygon.area)
    max_fp = bp_area * (max_bya_pct / 100.0)

    neighbor_text, nb_polys = _compute_neighbor_context(neighbors, site_polygon)

    # Pass 1
    concept = _pass1_concept(
        typology, site_area, bp_area, buildable_polygon,
        max_bya_pct, max_floors, max_height_m, target_bta_m2,
        floor_to_floor_m, latitude, terrain, neighbor_text,
    )

    # Pass 2
    buildings = _pass2_place(concept, buildable_polygon, max_fp, typology, floor_to_floor_m)

    if not buildings:
        return {"buildings": [], "footprint": None, "error": "Pass 2: ingen bygninger plassert",
                "concept": concept.get("strategy", "")}

    # Pass 3
    buildings = _pass3_refine(
        buildings, buildable_polygon, max_floors, max_height_m,
        latitude, neighbor_text, typology, floor_to_floor_m,
    )

    # Pass 4
    buildings = _pass4_validate(
        buildings, buildable_polygon, max_fp, max_floors, max_height_m, floor_to_floor_m,
    )

    footprint = None
    if buildings:
        polys = [b["polygon"] for b in buildings if b.get("polygon")]
        if polys:
            footprint = unary_union(polys).buffer(0)

    for b in buildings:
        b.setdefault("pos_id", "")

    total_fp_m2 = sum(b.get("area_m2", 0) for b in buildings)
    total_bta = sum(b.get("area_m2", 0) * b.get("floors", 1) for b in buildings)

    return {
        "buildings": buildings,
        "footprint": footprint,
        "building_count": len(buildings),
        "total_footprint_m2": round(total_fp_m2, 1),
        "total_bta_m2": round(total_bta, 1),
        "source": f"AI multi-pass (Claude {model})",
        "concept": concept.get("strategy", ""),
        "positions_evaluated": 0,
        "positions_usable": 0,
        "prompt": "",
        "raw_response": "",
        "raw_parsed": [],
    }
