"""
ai_site_planner_v2.py — Multi-Pass AI Site Planner for Builtly Mulighetsstudie

Architecture:
  Pass 1: Claude generates a high-level bebyggelseskonsept (concept)
  Pass 2: Python places buildings deterministically based on the concept
  Pass 3: Claude refines — adjusts rotation, height variation, courtyard openings, solar
  Pass 4: Python validates everything and auto-fixes violations

Author: Builtly AS
"""

import json
import math
import logging
from typing import Optional
from dataclasses import dataclass, field, asdict

import anthropic
from shapely.geometry import Polygon, MultiPolygon, box, Point
from shapely.ops import unary_union
from shapely.affinity import rotate, translate

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Data models
# ──────────────────────────────────────────────

@dataclass
class BuildingFootprint:
    """A single building placement."""
    id: str
    typology: str          # "blokk", "rekkehus", "punkthus", "lamell", "naering"
    x: float               # center x in meters (local coord)
    y: float               # center y in meters (local coord)
    width: float           # meters (along x-axis before rotation)
    depth: float           # meters (along y-axis before rotation)
    height: float          # meters
    floors: int
    rotation_deg: float    # degrees CCW from east
    units: int = 0         # estimated dwelling units
    bra_m2: float = 0.0
    label: str = ""

    @property
    def footprint_m2(self) -> float:
        return self.width * self.depth

    @property
    def polygon(self) -> Polygon:
        """Return the rotated rectangle as a Shapely polygon."""
        b = box(-self.width / 2, -self.depth / 2, self.width / 2, self.depth / 2)
        b = rotate(b, self.rotation_deg, origin=(0, 0))
        b = translate(b, self.x, self.y)
        return b


@dataclass
class SiteConcept:
    """Output of Pass 1 — the AI-generated concept."""
    strategy: str                    # e.g. "perimeter block with central courtyard"
    zones: list                      # list of zone dicts
    buildings: list                  # list of building type specs
    total_target_bya_pct: float
    max_floors: int
    notes: str = ""


@dataclass
class PlacementResult:
    """Output of Pass 2/3/4 — validated building placements."""
    buildings: list                  # list of BuildingFootprint
    bya_m2: float
    bya_pct: float
    bra_total: float
    units_total: int
    coverage_score: float            # 0-1, how well the plot is utilized
    validation_errors: list = field(default_factory=list)
    validation_warnings: list = field(default_factory=list)


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

# Typology dimension constraints (TEK17 + practical)
TYPOLOGY_LIMITS = {
    "blokk":     {"w_min": 14, "w_max": 60, "d_min": 14, "d_max": 20, "h_floor": 3.0, "floors_min": 3, "floors_max": 8,  "units_per_floor": 4},
    "lamell":    {"w_min": 30, "w_max": 80, "d_min": 12, "d_max": 16, "h_floor": 3.0, "floors_min": 3, "floors_max": 6,  "units_per_floor": 3},
    "punkthus":  {"w_min": 16, "w_max": 24, "d_min": 16, "d_max": 24, "h_floor": 3.0, "floors_min": 4, "floors_max": 12, "units_per_floor": 3},
    "rekkehus":  {"w_min": 30, "w_max": 80, "d_min": 8,  "d_max": 12, "h_floor": 2.8, "floors_min": 2, "floors_max": 3,  "units_per_floor": 1},
    "naering":   {"w_min": 20, "w_max": 80, "d_min": 15, "d_max": 40, "h_floor": 3.5, "floors_min": 1, "floors_max": 5,  "units_per_floor": 0},
}

# Minimum distances (meters)
MIN_BUILDING_SPACING = 8.0      # Between buildings (TEK17 brannkrav ~8m)
MIN_BOUNDARY_SETBACK = 4.0      # From plot boundary
MIN_ROAD_SETBACK = 6.0          # From road/vei
MIN_COURTYARD_WIDTH = 15.0      # Minimum gårdsrom width to count as "real"

# Solar direction (south = 180° from north, in math coords south ≈ 270°)
SOUTH_DIRECTION_DEG = 180.0     # Compass bearing for south


# ──────────────────────────────────────────────
# Pass 1: Claude Concept Generation
# ──────────────────────────────────────────────

def pass1_generate_concept(
    plot_polygon: Polygon,
    regulations: dict,
    client: anthropic.Anthropic,
    model: str = "claude-opus-4-6",
) -> SiteConcept:
    """
    Pass 1: Ask Claude to generate a high-level bebyggelseskonsept.

    Args:
        plot_polygon: Shapely Polygon of the site (in meters, local coords)
        regulations: dict with keys like 'max_bya_pct', 'max_floors', 'allowed_typologies',
                     'min_uteoppholdsareal_pct', 'parking_per_unit', 'road_edges' (list of edge indices)
        client: Anthropic API client
        model: Claude model to use

    Returns:
        SiteConcept with strategy, zones, and building specs
    """
    bounds = plot_polygon.bounds  # (minx, miny, maxx, maxy)
    plot_w = bounds[2] - bounds[0]
    plot_d = bounds[3] - bounds[1]
    plot_area = plot_polygon.area

    # Build the inset polygon for reference
    inset = plot_polygon.buffer(-MIN_BOUNDARY_SETBACK)
    inset_area = inset.area if not inset.is_empty else 0

    # Determine plot shape characteristics
    coords = list(plot_polygon.exterior.coords)
    aspect_ratio = plot_w / plot_d if plot_d > 0 else 1.0

    prompt = f"""Du er en norsk arkitekt/byplanlegger som skal lage et bebyggelseskonsept for en tomt.

TOMT:
- Areal: {plot_area:.0f} m² ({plot_area/1000:.1f} dekar)
- Bounding box: {plot_w:.0f} x {plot_d:.0f} m
- Aspect ratio: {aspect_ratio:.2f}
- Byggbart areal (etter 4m tilbaketrekking): {inset_area:.0f} m²
- Polygon-koordinater (meter, lokalt): {json.dumps([[round(c[0],1), round(c[1],1)] for c in coords])}

REGULERINGSBESTEMMELSER:
- Maks BYA: {regulations.get('max_bya_pct', 40)}%
- Maks etasjer: {regulations.get('max_floors', 5)}
- Tillatte typologier: {json.dumps(regulations.get('allowed_typologies', ['blokk', 'lamell', 'punkthus', 'rekkehus']))}
- Min uteoppholdsareal: {regulations.get('min_uteoppholdsareal_pct', 25)}% av tomten
- Parkering: {regulations.get('parking_per_unit', 1.0)} plass per boenhet
- Veikanter (indeks i polygon): {json.dumps(regulations.get('road_edges', [0]))}

OPPGAVE:
Velg et bebyggelseskonsept som:
1. Utnytter hele tomten jevnt (ikke klumper alt på én side)
2. Skaper gode uterom og gårdsrom
3. Åpner mot sør der mulig
4. Holder BYA innenfor reguleringsgrensen
5. Gir realistisk antall boenheter

Svar BARE med JSON i dette formatet (ingen markdown, ingen forklaring):
{{
  "strategy": "kort beskrivelse av konseptet",
  "zones": [
    {{
      "id": "zone_1",
      "purpose": "bolig" | "naering" | "parkering" | "uteopphold",
      "approximate_bounds": [x_min, y_min, x_max, y_max],
      "notes": "..."
    }}
  ],
  "buildings": [
    {{
      "typology": "blokk" | "lamell" | "punkthus" | "rekkehus" | "naering",
      "count": 2,
      "target_width": 40,
      "target_depth": 16,
      "target_floors": 4,
      "preferred_rotation_deg": 0,
      "zone_id": "zone_1"
    }}
  ],
  "total_target_bya_pct": 35,
  "max_floors": 5,
  "notes": "..."
}}"""

    logger.info("Pass 1: Generating site concept via Claude...")
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        temperature=0.3,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

    concept_data = json.loads(raw)
    logger.info(f"Pass 1 concept: {concept_data.get('strategy', 'N/A')}")

    return SiteConcept(
        strategy=concept_data["strategy"],
        zones=concept_data.get("zones", []),
        buildings=concept_data.get("buildings", []),
        total_target_bya_pct=concept_data.get("total_target_bya_pct", 35),
        max_floors=concept_data.get("max_floors", 5),
        notes=concept_data.get("notes", ""),
    )


# ──────────────────────────────────────────────
# Pass 2: Deterministic Python Placement
# ──────────────────────────────────────────────

def pass2_deterministic_placement(
    plot_polygon: Polygon,
    concept: SiteConcept,
    regulations: dict,
) -> list[BuildingFootprint]:
    """
    Pass 2: Place buildings deterministically based on the AI concept.

    Strategy:
    1. Create inset polygon (boundary setback)
    2. For each zone, compute the usable sub-polygon
    3. Within each zone, place buildings in a grid with required spacing
    4. Clip any buildings that extend outside the inset polygon
    5. Enforce BYA limits

    Returns list of BuildingFootprint objects.
    """
    logger.info("Pass 2: Deterministic placement...")

    setback = MIN_BOUNDARY_SETBACK
    road_setback = MIN_ROAD_SETBACK

    # Create inset polygon with boundary setback
    inset = plot_polygon.buffer(-setback)
    if inset.is_empty or not inset.is_valid:
        logger.error("Plot too small for setback")
        return []

    # Handle road edges with extra setback
    road_edges = regulations.get("road_edges", [])
    if road_edges:
        inset = _apply_road_setback(plot_polygon, inset, road_edges, road_setback - setback)

    if inset.is_empty:
        logger.error("No buildable area after setbacks")
        return []

    # Ensure we work with a single polygon
    if isinstance(inset, MultiPolygon):
        inset = max(inset.geoms, key=lambda g: g.area)

    max_bya_pct = regulations.get("max_bya_pct", 40)
    max_bya_m2 = plot_polygon.area * max_bya_pct / 100.0

    buildings = []
    total_bya = 0.0
    building_id = 0

    # Sort building specs by size (largest first for better packing)
    building_specs = sorted(
        concept.buildings,
        key=lambda b: b.get("target_width", 30) * b.get("target_depth", 15),
        reverse=True,
    )

    # Collect all placed polygons for overlap checking
    placed_polygons = []

    for spec in building_specs:
        typology = spec["typology"]
        count = spec.get("count", 1)
        limits = TYPOLOGY_LIMITS.get(typology, TYPOLOGY_LIMITS["blokk"])

        # Clamp dimensions to typology limits
        w = _clamp(spec.get("target_width", limits["w_min"]), limits["w_min"], limits["w_max"])
        d = _clamp(spec.get("target_depth", limits["d_min"]), limits["d_min"], limits["d_max"])
        floors = _clamp(spec.get("target_floors", limits["floors_min"]), limits["floors_min"], limits["floors_max"])
        rotation = spec.get("preferred_rotation_deg", 0)

        # Determine zone sub-polygon
        zone_id = spec.get("zone_id")
        zone_poly = _get_zone_polygon(inset, concept.zones, zone_id)

        # Place buildings in a grid within the zone
        for i in range(count):
            if total_bya + w * d > max_bya_m2:
                logger.warning(f"BYA limit reached ({total_bya:.0f}/{max_bya_m2:.0f} m²), skipping remaining")
                break

            placement = _find_placement(
                zone_poly, w, d, rotation, placed_polygons, MIN_BUILDING_SPACING
            )
            if placement is None:
                # Try alternate rotation (90°)
                placement = _find_placement(
                    zone_poly, w, d, rotation + 90, placed_polygons, MIN_BUILDING_SPACING
                )
            if placement is None:
                # Try smaller building
                w_reduced = max(limits["w_min"], w * 0.8)
                d_reduced = max(limits["d_min"], d * 0.8)
                placement = _find_placement(
                    zone_poly, w_reduced, d_reduced, rotation, placed_polygons, MIN_BUILDING_SPACING
                )
                if placement:
                    w, d = w_reduced, d_reduced

            if placement is None:
                logger.warning(f"Could not place {typology} #{i+1} in zone {zone_id}")
                continue

            cx, cy, final_rot = placement
            building_id += 1
            h = floors * limits["h_floor"]

            bldg = BuildingFootprint(
                id=f"B{building_id:03d}",
                typology=typology,
                x=cx, y=cy,
                width=w, depth=d,
                height=h,
                floors=floors,
                rotation_deg=final_rot,
                units=floors * limits["units_per_floor"] if typology != "naering" else 0,
                bra_m2=w * d * floors * 0.85,  # ~85% BTA→BRA
                label=f"{typology.capitalize()} {building_id}",
            )

            placed_polygons.append(bldg.polygon)
            total_bya += bldg.footprint_m2
            buildings.append(bldg)

    logger.info(f"Pass 2: Placed {len(buildings)} buildings, BYA={total_bya:.0f} m² ({total_bya/plot_polygon.area*100:.1f}%)")
    return buildings


def _apply_road_setback(
    plot_polygon: Polygon, inset: Polygon, road_edges: list[int], extra_setback: float
) -> Polygon:
    """Apply extra setback along road-facing edges."""
    coords = list(plot_polygon.exterior.coords)
    for edge_idx in road_edges:
        if edge_idx >= len(coords) - 1:
            continue
        p1 = coords[edge_idx]
        p2 = coords[edge_idx + 1]
        # Create a buffer strip along this edge
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        length = math.sqrt(dx**2 + dy**2)
        if length < 1:
            continue
        # Normal vector pointing inward
        nx = -dy / length
        ny = dx / length
        # Build a polygon strip to subtract
        strip = Polygon([
            p1,
            p2,
            (p2[0] + nx * extra_setback, p2[1] + ny * extra_setback),
            (p1[0] + nx * extra_setback, p1[1] + ny * extra_setback),
        ])
        inset = inset.difference(strip)
    return inset


def _get_zone_polygon(inset: Polygon, zones: list[dict], zone_id: Optional[str]) -> Polygon:
    """Get the sub-polygon for a specific zone, or the full inset if no zone specified."""
    if not zone_id or not zones:
        return inset

    for z in zones:
        if z.get("id") == zone_id and "approximate_bounds" in z:
            b = z["approximate_bounds"]
            zone_box = box(b[0], b[1], b[2], b[3])
            clipped = inset.intersection(zone_box)
            if not clipped.is_empty and clipped.area > 100:
                if isinstance(clipped, MultiPolygon):
                    return max(clipped.geoms, key=lambda g: g.area)
                return clipped
    return inset


def _find_placement(
    zone_poly: Polygon,
    width: float,
    depth: float,
    rotation_deg: float,
    placed: list[Polygon],
    min_spacing: float,
) -> Optional[tuple[float, float, float]]:
    """
    Find a valid placement for a building within the zone polygon.

    Uses a grid scan approach:
    1. Compute the zone bounding box
    2. Scan grid points at (width + spacing) intervals
    3. For each point, check if the rotated building fits within zone AND
       maintains spacing from all placed buildings
    4. Return the first valid placement, preferring positions that
       maximize distance from edges (better urban design)

    Returns (cx, cy, rotation_deg) or None.
    """
    if zone_poly.is_empty:
        return None

    bounds = zone_poly.bounds
    # Effective footprint size considering rotation
    rad = math.radians(rotation_deg)
    eff_w = abs(width * math.cos(rad)) + abs(depth * math.sin(rad))
    eff_d = abs(width * math.sin(rad)) + abs(depth * math.cos(rad))

    step_x = eff_w + min_spacing
    step_y = eff_d + min_spacing

    # Generate candidate positions
    candidates = []
    x = bounds[0] + eff_w / 2 + min_spacing / 2
    while x < bounds[2] - eff_w / 2:
        y = bounds[1] + eff_d / 2 + min_spacing / 2
        while y < bounds[3] - eff_d / 2:
            candidates.append((x, y))
            y += step_y
        x += step_x

    # Score candidates by distance from zone centroid (prefer spread-out placement)
    centroid = zone_poly.centroid
    # Sort: prefer positions farther from already-placed buildings (better distribution)
    def score(pos):
        px, py = pos
        if not placed:
            # Prefer positions near centroid initially
            return -math.sqrt((px - centroid.x)**2 + (py - centroid.y)**2)
        # Prefer positions that maximize minimum distance to placed buildings
        min_dist = min(
            Point(px, py).distance(p) for p in placed
        )
        return min_dist  # higher = better (more spread out)

    candidates.sort(key=score, reverse=True)

    for cx, cy in candidates:
        bldg_box = box(-width / 2, -depth / 2, width / 2, depth / 2)
        bldg_poly = rotate(bldg_box, rotation_deg, origin=(0, 0))
        bldg_poly = translate(bldg_poly, cx, cy)

        # Check 1: Must be fully within zone
        if not zone_poly.contains(bldg_poly):
            continue

        # Check 2: Must maintain spacing from all placed buildings
        too_close = False
        for p in placed:
            if bldg_poly.distance(p) < min_spacing:
                too_close = True
                break
        if too_close:
            continue

        return (cx, cy, rotation_deg)

    return None


def _clamp(val, lo, hi):
    return max(lo, min(hi, val))


# ──────────────────────────────────────────────
# Pass 3: Claude Refinement
# ──────────────────────────────────────────────

def pass3_refine_with_claude(
    plot_polygon: Polygon,
    buildings: list[BuildingFootprint],
    regulations: dict,
    client: anthropic.Anthropic,
    model: str = "claude-opus-4-6",
    north_bearing_deg: float = 0.0,
) -> list[BuildingFootprint]:
    """
    Pass 3: Ask Claude to refine the deterministic placements.

    Claude can:
    - Rotate buildings for better solar orientation
    - Vary heights for skyline interest
    - Open courtyards toward south
    - Adjust spacing for better uterom
    - Suggest removing a building to improve quality

    Returns updated list of BuildingFootprint.
    """
    logger.info("Pass 3: Claude refinement...")

    buildings_json = []
    for b in buildings:
        buildings_json.append({
            "id": b.id,
            "typology": b.typology,
            "x": round(b.x, 1),
            "y": round(b.y, 1),
            "width": round(b.width, 1),
            "depth": round(b.depth, 1),
            "floors": b.floors,
            "rotation_deg": round(b.rotation_deg, 1),
            "footprint_m2": round(b.footprint_m2, 1),
        })

    coords = list(plot_polygon.exterior.coords)
    plot_area = plot_polygon.area

    prompt = f"""Du er en norsk arkitekt som skal kvalitetssikre og forbedre en maskinell bygningsplassering.

TOMT:
- Areal: {plot_area:.0f} m²
- Polygon: {json.dumps([[round(c[0],1), round(c[1],1)] for c in coords])}
- Nord-retning: {north_bearing_deg}° (0=opp på kartet)

PLASSERINGER FRA MASKINELL PLASSERING:
{json.dumps(buildings_json, indent=2)}

REGULERING:
- Maks BYA: {regulations.get('max_bya_pct', 40)}%
- Maks etasjer: {regulations.get('max_floors', 5)}

DU KAN JUSTERE:
1. **rotation_deg** — Roter bygninger for bedre solforhold (sør-orientering). 
   Sør er ca. {(180 + north_bearing_deg) % 360:.0f}° i kartkoordinater.
2. **floors** — Varier høyder for interessant skyline. Trapp ned mot sør, opp mot nord.
3. **x, y** — Flytt maks ±5m for å åpne gårdsrom mot sør eller forbedre siktlinjer.
4. **remove** — Sett "remove": true for å fjerne en bygning som ødelegger kvaliteten.

REGLER:
- Ikke endre width/depth (det gjøres deterministisk)
- Hold alle bygninger innenfor tomten (4m fra kant)
- Hold min 8m mellom bygninger
- Behold total BYA innenfor grensen

Svar BARE med JSON-array av justerte bygninger (ingen markdown):
[
  {{
    "id": "B001",
    "rotation_deg": 15,
    "floors": 4,
    "x": 50.5,
    "y": 30.2,
    "remove": false
  }},
  ...
]"""

    response = client.messages.create(
        model=model,
        max_tokens=3000,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

    adjustments = json.loads(raw)
    adj_map = {a["id"]: a for a in adjustments}

    refined = []
    for b in buildings:
        adj = adj_map.get(b.id, {})
        if adj.get("remove", False):
            logger.info(f"Pass 3: Removing {b.id} ({b.typology}) per Claude refinement")
            continue

        # Apply adjustments
        if "rotation_deg" in adj:
            b.rotation_deg = adj["rotation_deg"]
        if "floors" in adj:
            limits = TYPOLOGY_LIMITS.get(b.typology, TYPOLOGY_LIMITS["blokk"])
            b.floors = _clamp(adj["floors"], limits["floors_min"], limits["floors_max"])
            b.height = b.floors * limits["h_floor"]
            b.bra_m2 = b.width * b.depth * b.floors * 0.85
            b.units = b.floors * limits["units_per_floor"] if b.typology != "naering" else 0
        if "x" in adj:
            # Limit movement to ±5m
            dx = _clamp(adj["x"] - b.x, -5.0, 5.0)
            b.x += dx
        if "y" in adj:
            dy = _clamp(adj["y"] - b.y, -5.0, 5.0)
            b.y += dy

        refined.append(b)

    logger.info(f"Pass 3: {len(refined)} buildings after refinement (was {len(buildings)})")
    return refined


# ──────────────────────────────────────────────
# Pass 4: Python Validation & Auto-Fix
# ──────────────────────────────────────────────

def pass4_validate_and_fix(
    plot_polygon: Polygon,
    buildings: list[BuildingFootprint],
    regulations: dict,
) -> PlacementResult:
    """
    Pass 4: Hard validation and auto-fix.

    Checks:
    1. All buildings within plot (with setback)
    2. No overlapping buildings
    3. Minimum spacing between buildings
    4. BYA within limits
    5. Floor count within limits
    6. Coverage distribution (anti-clumping)
    7. Courtyard validity (if enclosed spaces exist)

    Auto-fixes:
    - Shrinks buildings that overlap
    - Pushes buildings inward if outside boundary
    - Removes buildings if BYA exceeded (smallest first)

    Returns PlacementResult with final buildings and diagnostics.
    """
    logger.info("Pass 4: Validation and auto-fix...")

    errors = []
    warnings = []
    max_bya_pct = regulations.get("max_bya_pct", 40)
    max_bya_m2 = plot_polygon.area * max_bya_pct / 100.0
    max_floors = regulations.get("max_floors", 5)

    inset = plot_polygon.buffer(-MIN_BOUNDARY_SETBACK)
    if isinstance(inset, MultiPolygon):
        inset = max(inset.geoms, key=lambda g: g.area)

    # ── Check 1: Containment ──
    fixed_buildings = []
    for b in buildings:
        poly = b.polygon
        if inset.contains(poly):
            fixed_buildings.append(b)
        else:
            # Try to push inward
            fixed_b = _push_building_inward(b, inset)
            if fixed_b:
                warnings.append(f"{b.id}: Pushed inward to fit within setback")
                fixed_buildings.append(fixed_b)
            else:
                errors.append(f"{b.id}: Could not fit within plot, REMOVED")

    buildings = fixed_buildings

    # ── Check 2: Floor limits ──
    for b in buildings:
        if b.floors > max_floors:
            warnings.append(f"{b.id}: Reduced from {b.floors} to {max_floors} floors")
            limits = TYPOLOGY_LIMITS.get(b.typology, TYPOLOGY_LIMITS["blokk"])
            b.floors = max_floors
            b.height = b.floors * limits["h_floor"]
            b.bra_m2 = b.width * b.depth * b.floors * 0.85
            b.units = b.floors * limits["units_per_floor"] if b.typology != "naering" else 0

    # ── Check 3: Overlap resolution ──
    buildings = _resolve_overlaps(buildings, warnings)

    # ── Check 4: Minimum spacing ──
    buildings = _enforce_spacing(buildings, MIN_BUILDING_SPACING, warnings)

    # ── Check 5: BYA limit ──
    total_bya = sum(b.footprint_m2 for b in buildings)
    while total_bya > max_bya_m2 and buildings:
        # Remove smallest building
        smallest = min(buildings, key=lambda b: b.footprint_m2)
        warnings.append(f"{smallest.id}: Removed to meet BYA limit ({total_bya:.0f} > {max_bya_m2:.0f})")
        buildings.remove(smallest)
        total_bya = sum(b.footprint_m2 for b in buildings)

    # ── Check 6: Coverage distribution ──
    coverage_score = _compute_coverage_score(buildings, plot_polygon)
    if coverage_score < 0.4:
        warnings.append(f"Coverage score {coverage_score:.2f} — buildings may be clustered")

    # ── Check 7: Courtyard validation ──
    _validate_courtyards(buildings, warnings)

    # ── Final metrics ──
    total_bya = sum(b.footprint_m2 for b in buildings)
    total_bra = sum(b.bra_m2 for b in buildings)
    total_units = sum(b.units for b in buildings)
    bya_pct = total_bya / plot_polygon.area * 100 if plot_polygon.area > 0 else 0

    result = PlacementResult(
        buildings=buildings,
        bya_m2=round(total_bya, 1),
        bya_pct=round(bya_pct, 1),
        bra_total=round(total_bra, 1),
        units_total=total_units,
        coverage_score=round(coverage_score, 2),
        validation_errors=errors,
        validation_warnings=warnings,
    )

    logger.info(
        f"Pass 4 complete: {len(buildings)} buildings, "
        f"BYA={bya_pct:.1f}%, BRA={total_bra:.0f} m², "
        f"{total_units} units, coverage={coverage_score:.2f}"
    )
    return result


def _push_building_inward(b: BuildingFootprint, inset: Polygon) -> Optional[BuildingFootprint]:
    """Try to push a building inward so it fits within the inset polygon."""
    poly = b.polygon
    if inset.contains(poly):
        return b

    # Compute direction from building centroid to inset centroid
    bc = poly.centroid
    ic = inset.centroid
    dx = ic.x - bc.x
    dy = ic.y - bc.y
    dist = math.sqrt(dx**2 + dy**2)
    if dist < 0.1:
        return None

    # Try incremental pushes
    for step in [1, 2, 3, 5, 8, 12, 16, 20]:
        new_x = b.x + dx / dist * step
        new_y = b.y + dy / dist * step
        test_b = BuildingFootprint(
            id=b.id, typology=b.typology,
            x=new_x, y=new_y,
            width=b.width, depth=b.depth,
            height=b.height, floors=b.floors,
            rotation_deg=b.rotation_deg,
            units=b.units, bra_m2=b.bra_m2, label=b.label,
        )
        if inset.contains(test_b.polygon):
            return test_b

    return None


def _resolve_overlaps(buildings: list[BuildingFootprint], warnings: list) -> list[BuildingFootprint]:
    """Remove buildings that overlap with larger buildings."""
    kept = []
    polys = [(b, b.polygon) for b in buildings]
    # Sort by footprint size descending (keep larger buildings)
    polys.sort(key=lambda bp: bp[0].footprint_m2, reverse=True)

    kept_polys = []
    for b, poly in polys:
        overlaps = False
        for kp in kept_polys:
            if poly.intersects(kp) and poly.intersection(kp).area > 1.0:
                overlaps = True
                break
        if overlaps:
            warnings.append(f"{b.id}: Removed due to overlap")
        else:
            kept.append(b)
            kept_polys.append(poly)

    return kept


def _enforce_spacing(
    buildings: list[BuildingFootprint], min_spacing: float, warnings: list
) -> list[BuildingFootprint]:
    """Remove buildings that violate minimum spacing (smallest first)."""
    # Check all pairs
    violations = set()
    for i, b1 in enumerate(buildings):
        for j, b2 in enumerate(buildings):
            if j <= i:
                continue
            dist = b1.polygon.distance(b2.polygon)
            if dist < min_spacing:
                # Mark the smaller one for removal
                if b1.footprint_m2 < b2.footprint_m2:
                    violations.add(b1.id)
                else:
                    violations.add(b2.id)

    result = []
    for b in buildings:
        if b.id in violations:
            warnings.append(f"{b.id}: Removed — too close to neighbor ({min_spacing}m min)")
        else:
            result.append(b)
    return result


def _compute_coverage_score(buildings: list[BuildingFootprint], plot_polygon: Polygon) -> float:
    """
    Compute how evenly buildings are distributed across the plot.

    Divides the plot bounding box into a 4x4 grid.
    Score = fraction of cells that contain at least part of a building.
    """
    if not buildings:
        return 0.0

    bounds = plot_polygon.bounds
    bw = bounds[2] - bounds[0]
    bh = bounds[3] - bounds[1]
    if bw < 1 or bh < 1:
        return 0.0

    grid_n = 4
    cell_w = bw / grid_n
    cell_h = bh / grid_n

    occupied = 0
    valid_cells = 0

    for gx in range(grid_n):
        for gy in range(grid_n):
            cell = box(
                bounds[0] + gx * cell_w,
                bounds[1] + gy * cell_h,
                bounds[0] + (gx + 1) * cell_w,
                bounds[1] + (gy + 1) * cell_h,
            )
            # Only count cells that overlap with the plot
            if not plot_polygon.intersects(cell):
                continue
            cell_in_plot = plot_polygon.intersection(cell)
            if cell_in_plot.area < cell_w * cell_h * 0.25:
                continue
            valid_cells += 1

            for b in buildings:
                if b.polygon.intersects(cell):
                    occupied += 1
                    break

    return occupied / valid_cells if valid_cells > 0 else 0.0


def _validate_courtyards(buildings: list[BuildingFootprint], warnings: list):
    """Check if any enclosed spaces between buildings are too narrow to be real courtyards."""
    if len(buildings) < 3:
        return

    # Union of all building polygons
    all_polys = unary_union([b.polygon for b in buildings])
    # The convex hull minus the buildings gives potential courtyard spaces
    hull = all_polys.convex_hull
    open_space = hull.difference(all_polys)

    if open_space.is_empty:
        return

    # Check if any enclosed space is too narrow
    if hasattr(open_space, 'geoms'):
        spaces = list(open_space.geoms)
    else:
        spaces = [open_space]

    for i, space in enumerate(spaces):
        if space.area < 50:  # Less than 50m² is not a usable courtyard
            continue
        # Check minimum width using negative buffer
        narrowed = space.buffer(-MIN_COURTYARD_WIDTH / 2)
        if narrowed.is_empty:
            warnings.append(
                f"Courtyard space {i+1} ({space.area:.0f} m²) is narrower than "
                f"{MIN_COURTYARD_WIDTH}m — may not function as real gårdsrom"
            )


# ──────────────────────────────────────────────
# Main orchestrator
# ──────────────────────────────────────────────

def run_multi_pass_planner(
    plot_polygon: Polygon,
    regulations: dict,
    api_key: Optional[str] = None,
    model: str = "claude-opus-4-6",
    north_bearing_deg: float = 0.0,
) -> PlacementResult:
    """
    Run the full 4-pass site planning pipeline.

    Args:
        plot_polygon: Shapely Polygon in local meter coordinates
        regulations: dict with regulation parameters
        api_key: Anthropic API key (or uses ANTHROPIC_API_KEY env var)
        model: Claude model ID
        north_bearing_deg: compass bearing of "up" on the coordinate system

    Returns:
        PlacementResult with final validated placements
    """
    import os
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)

    # Pass 1: Claude concept
    concept = pass1_generate_concept(plot_polygon, regulations, client, model)
    logger.info(f"Concept: {concept.strategy}")

    # Pass 2: Deterministic placement
    buildings = pass2_deterministic_placement(plot_polygon, concept, regulations)
    if not buildings:
        logger.error("Pass 2 produced no buildings!")
        return PlacementResult(
            buildings=[], bya_m2=0, bya_pct=0, bra_total=0,
            units_total=0, coverage_score=0,
            validation_errors=["No buildings could be placed"],
        )

    # Pass 3: Claude refinement
    buildings = pass3_refine_with_claude(
        plot_polygon, buildings, regulations, client, model, north_bearing_deg
    )

    # Pass 4: Validation and fix
    result = pass4_validate_and_fix(plot_polygon, buildings, regulations)

    return result


# ──────────────────────────────────────────────
# Conversion helpers for Builtly integration
# ──────────────────────────────────────────────

def result_to_threejs_json(result: PlacementResult, plot_polygon: Polygon) -> dict:
    """Convert PlacementResult to JSON for Three.js viewer."""
    return {
        "plot": {
            "coordinates": [list(c) for c in plot_polygon.exterior.coords],
            "area_m2": round(plot_polygon.area, 1),
        },
        "buildings": [
            {
                "id": b.id,
                "typology": b.typology,
                "position": [round(b.x, 1), round(b.y, 1)],
                "dimensions": [round(b.width, 1), round(b.depth, 1), round(b.height, 1)],
                "rotation_deg": round(b.rotation_deg, 1),
                "floors": b.floors,
                "units": b.units,
                "bra_m2": round(b.bra_m2, 1),
                "label": b.label,
            }
            for b in result.buildings
        ],
        "metrics": {
            "bya_m2": result.bya_m2,
            "bya_pct": result.bya_pct,
            "bra_total": result.bra_total,
            "units_total": result.units_total,
            "coverage_score": result.coverage_score,
        },
        "diagnostics": {
            "errors": result.validation_errors,
            "warnings": result.validation_warnings,
        },
    }


def result_to_isometric_data(result: PlacementResult) -> list[dict]:
    """Convert PlacementResult to the format expected by the isometric PIL renderer."""
    return [
        {
            "x": b.x,
            "y": b.y,
            "width": b.width,
            "depth": b.depth,
            "height": b.height,
            "floors": b.floors,
            "typology": b.typology,
            "rotation": b.rotation_deg,
            "label": b.label,
        }
        for b in result.buildings
    ]
def is_available() -> bool:
    """Check if AI site planner dependencies are available."""
    try:
        import anthropic
        import shapely
        return True
    except ImportError:
        return False
