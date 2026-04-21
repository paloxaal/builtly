from __future__ import annotations

"""Deterministic solar analysis for Builtly v8 delivery 3.

- Uses pvlib when installed and timezone-aware timestamps are provided.
- Falls back to a deterministic solar-geometry approximation otherwise.
- Models buildings/neighbours as extruded prisms and evaluates shading in 2.5D.
"""

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # pragma: no cover - optional dependency
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore[assignment]

try:  # pragma: no cover - optional dependency
    import pvlib  # type: ignore
    HAS_PVLIB = True
except Exception:  # pragma: no cover
    pvlib = None  # type: ignore[assignment]
    HAS_PVLIB = False

from shapely.affinity import translate
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from .masterplan_types import Bygg, PlanRegler, SolBuildingResult, SolKeyMoment, SolReport

DEFAULT_LATITUDE_DEG = 63.42
DEFAULT_LONGITUDE_DEG = 10.43
DEFAULT_TZ = "Europe/Oslo"
DEFAULT_ANALYSIS_DATES: Tuple[Tuple[int, int], ...] = ((3, 21), (6, 21), (9, 21), (12, 21))
DEFAULT_ANALYSIS_HOURS: Tuple[int, ...] = tuple(range(24))
DEFAULT_OPEN_SPACE_GRID_M = 10.0
DEFAULT_SHADOW_REF_HEIGHT_M = 16.0


@dataclass(frozen=True)
class SolarPosition:
    timestamp: datetime
    elevation_deg: float
    azimuth_deg: float

    @property
    def altitude_deg(self) -> float:
        return self.elevation_deg


# Backward-friendly alias
SolarInstant = SolarPosition


# ---------------------------------------------------------------------------
# Solar position
# ---------------------------------------------------------------------------


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()  # pandas.Timestamp
    raise TypeError("timestamp må være datetime eller pandas.Timestamp")


def _day_of_year(when: datetime) -> int:
    return int(when.timetuple().tm_yday)


def _fallback_solar_position(latitude_deg: float, longitude_deg: float, when: datetime) -> SolarPosition:
    day = _day_of_year(when)
    civil_hour = when.hour + when.minute / 60.0 + when.second / 3600.0

    # If timezone info is known, approximate local solar time from civil time.
    if when.tzinfo is not None and when.utcoffset() is not None:
        tz_offset_h = when.utcoffset().total_seconds() / 3600.0
        local_std_meridian = 15.0 * tz_offset_h
        b = math.radians((360.0 / 365.0) * (day - 81))
        equation_of_time_min = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
        time_correction_min = 4.0 * (longitude_deg - local_std_meridian) + equation_of_time_min
        solar_hour = civil_hour + time_correction_min / 60.0
    else:
        solar_hour = civil_hour

    decl_deg = 23.45 * math.sin(math.radians(360.0 * (284 + day) / 365.0))
    hour_angle_deg = 15.0 * (solar_hour - 12.0)

    lat = math.radians(latitude_deg)
    dec = math.radians(decl_deg)
    ha = math.radians(hour_angle_deg)

    sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(ha)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt = math.asin(sin_alt)
    elevation_deg = math.degrees(alt)

    if abs(math.cos(alt)) < 1e-9:
        azimuth_deg = 180.0
    else:
        sin_az = -math.sin(ha) * math.cos(dec) / max(math.cos(alt), 1e-9)
        cos_az = (math.sin(dec) - math.sin(alt) * math.sin(lat)) / max(math.cos(alt) * math.cos(lat), 1e-9)
        azimuth_deg = math.degrees(math.atan2(sin_az, cos_az)) % 360.0

    return SolarPosition(timestamp=when, elevation_deg=float(elevation_deg), azimuth_deg=float(azimuth_deg))


def solar_position(latitude_deg: float, longitude_deg: float, when: Any) -> SolarPosition:
    dt = _to_datetime(when)
    if HAS_PVLIB and pvlib is not None and pd is not None and getattr(dt, "tzinfo", None) is not None:
        try:  # pragma: no cover - optional path
            idx = pd.DatetimeIndex([dt])
            sp = pvlib.solarposition.get_solarposition(idx, latitude_deg, longitude_deg)
            row = sp.iloc[0]
            return SolarPosition(timestamp=dt, elevation_deg=float(row["apparent_elevation"]), azimuth_deg=float(row["azimuth"]))
        except Exception:
            pass
    return _fallback_solar_position(latitude_deg, longitude_deg, dt)


def solar_position_for_timestamp(latitude_deg: float, longitude_deg: float, timestamp: Any) -> SolarPosition:
    return solar_position(latitude_deg, longitude_deg, timestamp)


# ---------------------------------------------------------------------------
# Shadows / occlusion
# ---------------------------------------------------------------------------


def horizontal_shadow_length(height_m: float, elevation_deg: float) -> float:
    if elevation_deg <= 0.0:
        return float("inf")
    tan_val = math.tan(math.radians(elevation_deg))
    if tan_val <= 1e-9:
        return float("inf")
    return float(height_m / tan_val)


# alias expected by some callers
def shadow_length_m(height_m: float, elevation_deg: float) -> float:
    return horizontal_shadow_length(height_m, elevation_deg)


def project_shadow_polygon(footprint: Polygon, height_m: float, solar_or_azimuth: Any, elevation_deg: Optional[float] = None) -> Polygon:
    if isinstance(solar_or_azimuth, SolarPosition):
        azimuth_deg = solar_or_azimuth.azimuth_deg
        elevation = solar_or_azimuth.elevation_deg
    else:
        azimuth_deg = float(solar_or_azimuth)
        if elevation_deg is None:
            raise TypeError("elevation_deg må oppgis når solar_or_azimuth ikke er SolarPosition")
        elevation = float(elevation_deg)

    if footprint.is_empty or height_m <= 0.0 or elevation <= 0.0:
        return footprint.buffer(0)

    length = horizontal_shadow_length(height_m, elevation)
    dx = -math.sin(math.radians(azimuth_deg)) * length
    dy = -math.cos(math.radians(azimuth_deg)) * length
    shifted = translate(footprint, xoff=dx, yoff=dy)
    coords = list(footprint.exterior.coords)
    pieces = [footprint, shifted]
    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i + 1]
        quad = Polygon([p1, p2, (p2[0] + dx, p2[1] + dy), (p1[0] + dx, p1[1] + dy)])
        if quad.is_valid and not quad.is_empty:
            pieces.append(quad)
    return unary_union(pieces).buffer(0)


def obstacle_shadow_union(obstacles: Sequence[Tuple[Polygon, float]], solar: SolarPosition):
    if not obstacles:
        return None
    polys = [project_shadow_polygon(poly, height, solar) for poly, height in obstacles]
    return unary_union(polys).buffer(0) if polys else None


def _flatten_polygons(geom: Any) -> List[Polygon]:
    if geom is None or getattr(geom, "is_empty", True):
        return []
    if isinstance(geom, Polygon):
        return [geom.buffer(0)]
    if isinstance(geom, MultiPolygon):
        return [g.buffer(0) for g in geom.geoms if not g.is_empty]
    if hasattr(geom, "geoms"):
        polys: List[Polygon] = []
        for part in geom.geoms:
            polys.extend(_flatten_polygons(part))
        return polys
    return []


def _grid_points_in_geometry(geom: Any, spacing_m: float = DEFAULT_OPEN_SPACE_GRID_M) -> List[Point]:
    polys = _flatten_polygons(geom)
    pts: List[Point] = []
    for poly in polys:
        minx, miny, maxx, maxy = poly.bounds
        x = minx + spacing_m / 2.0
        while x <= maxx:
            y = miny + spacing_m / 2.0
            while y <= maxy:
                p = Point(x, y)
                if poly.buffer(1e-6).covers(p):
                    pts.append(p)
                y += spacing_m
            x += spacing_m
    if not pts:
        for poly in polys:
            if not poly.is_empty:
                pts.append(poly.representative_point())
    return pts


def _is_ccw(poly: Polygon) -> bool:
    coords = list(poly.exterior.coords)
    area = 0.0
    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        area += x1 * y2 - x2 * y1
    return area > 0.0


def _facade_samples(bygg: Bygg, max_step_m: float = 10.0) -> List[Tuple[Point, Tuple[float, float], float]]:
    coords = list(bygg.footprint.exterior.coords)
    if len(coords) < 2:
        return []
    ccw = _is_ccw(bygg.footprint)
    z = max(1.5, bygg.height_m * 0.5)
    samples: List[Tuple[Point, Tuple[float, float], float]] = []
    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        dx = x2 - x1
        dy = y2 - y1
        seg_len = math.hypot(dx, dy)
        if seg_len < 2.0:
            continue
        nx, ny = (dy / seg_len, -dx / seg_len) if ccw else (-dy / seg_len, dx / seg_len)
        count = max(1, int(math.ceil(seg_len / max_step_m)))
        for j in range(count):
            t = (j + 0.5) / count
            px = x1 + dx * t
            py = y1 + dy * t
            samples.append((Point(px, py), (nx, ny), z))
    return samples


def _sun_dir_xy(azimuth_deg: float) -> Tuple[float, float]:
    return math.sin(math.radians(azimuth_deg)), math.cos(math.radians(azimuth_deg))


def _point_blocked_by_occluders(point: Point, sample_height_m: float, sun_azimuth_deg: float, sun_elevation_deg: float, occluders: Sequence[Tuple[Polygon, float]], *, max_distance_m: float = 400.0) -> bool:
    dx, dy = _sun_dir_xy(sun_azimuth_deg)
    ray = LineString([(point.x, point.y), (point.x + dx * max_distance_m, point.y + dy * max_distance_m)])
    for poly, height_m in occluders:
        if poly.buffer(1e-6).covers(point):
            continue
        if not poly.intersects(ray):
            continue
        inter = poly.intersection(ray)
        if inter.is_empty:
            continue
        dist = point.distance(inter)
        if dist <= 1e-6:
            continue
        blocker_angle = math.degrees(math.atan2(height_m - sample_height_m, dist))
        if blocker_angle >= sun_elevation_deg - 1e-6:
            return True
    return False


def _coerce_neighbors(neighbors: Optional[Sequence[object]]) -> List[Tuple[Polygon, float]]:
    out: List[Tuple[Polygon, float]] = []
    for item in neighbors or []:
        if isinstance(item, dict):
            poly = item.get("polygon")
            height = float(item.get("height_m", 0.0) or 0.0)
        else:
            poly = getattr(item, "polygon", None)
            height = float(getattr(item, "height_m", 0.0) or 0.0)
        if poly is not None and not getattr(poly, "is_empty", True) and height > 0.0:
            out.append((poly.buffer(0), height))
    return out


def _iter_analysis_datetimes(year: int, tz_name: str = DEFAULT_TZ) -> Iterable[datetime]:
    for month, day in DEFAULT_ANALYSIS_DATES:
        for hour in DEFAULT_ANALYSIS_HOURS:
            if pd is not None:
                yield pd.Timestamp(year=year, month=month, day=day, hour=hour, tz=tz_name).to_pydatetime()
            else:
                yield datetime(year, month, day, hour, 0, 0)


def _open_ground_polygon(buildable_poly: Optional[Polygon], buildings: Sequence[Bygg]) -> Optional[Polygon]:
    if buildable_poly is None or getattr(buildable_poly, "is_empty", True):
        return None
    built = unary_union([b.footprint for b in buildings]).buffer(0) if buildings else None
    if built is None or getattr(built, "is_empty", True):
        return buildable_poly.buffer(0)
    return buildable_poly.difference(built).buffer(0)


def compute_sol_report(
    buildable_poly: Polygon,
    buildings: Sequence[Bygg],
    *,
    latitude_deg: float,
    longitude_deg: float,
    neighbors: Optional[Sequence[Dict[str, Any]]] = None,
    rules: Optional[PlanRegler] = None,
    year: int = 2026,
) -> SolReport:
    if not buildings:
        return SolReport(notes=["Ingen bygg å analysere."], analysis_samples=0)

    neighbour_occluders = _coerce_neighbors(neighbors)
    building_occluders = [(b.footprint.buffer(0), float(b.height_m)) for b in buildings]
    instants = [solar_position(latitude_deg, longitude_deg, when) for when in _iter_analysis_datetimes(year) if solar_position(latitude_deg, longitude_deg, when).elevation_deg > 0.0]
    building_samples = {b.bygg_id: _facade_samples(b) for b in buildings}

    per_building: List[SolBuildingResult] = []
    weighted_score = 0.0
    weighted_hours = 0.0
    weighted_bra = 0.0

    for building in buildings:
        samples = building_samples[building.bygg_id]
        if not samples or not instants:
            per_building.append(SolBuildingResult(bygg_id=building.bygg_id))
            continue
        occluders = [(poly, h) for poly, h in building_occluders if not poly.equals(building.footprint)] + neighbour_occluders
        lit = possible = march_lit = 0
        facade_hits: Dict[str, float] = {f"facade_{idx+1}": 0.0 for idx in range(max(1, len(samples)))}
        facade_possible: Dict[str, float] = {f"facade_{idx+1}": 0.0 for idx in range(max(1, len(samples)))}
        for inst in instants:
            sun_dx, sun_dy = _sun_dir_xy(inst.azimuth_deg)
            for idx, (point, normal, z) in enumerate(samples):
                if normal[0] * sun_dx + normal[1] * sun_dy <= 0.05:
                    continue
                key = f"facade_{(idx % max(1, len(facade_hits))) + 1}"
                possible += 1
                facade_possible[key] += 1.0
                if not _point_blocked_by_occluders(point, z, inst.azimuth_deg, inst.elevation_deg, occluders):
                    lit += 1
                    facade_hits[key] += 1.0
                    if inst.timestamp.month == 3 and inst.timestamp.day == 21:
                        march_lit += 1
        score = 100.0 * lit / possible if possible else 0.0
        eqx_hours = march_lit / max(1, len(samples))
        req_hours = None if rules is None else (rules.sol_krav_timer_varjevndogn if rules.sol_krav_timer_varjevndogn is not None else rules.mua_min_sol_timer)
        res = SolBuildingResult(
            bygg_id=building.bygg_id,
            sol_score=round(score, 1),
            soltimer_varjevndogn=round(eqx_hours, 2),
            tek17_mua_compliant=(None if req_hours is None else bool(eqx_hours >= req_hours)),
            facade_sun_fraction=round(lit / possible, 4) if possible else 0.0,
            possible_samples=int(possible),
            sunlit_samples=int(lit),
            facade_results={k: round(100.0 * facade_hits[k] / facade_possible[k], 1) if facade_possible[k] > 0 else 0.0 for k in facade_hits},
            per_leilighet=[],
        )
        per_building.append(res)
        weighted_score += res.sol_score * building.bra_m2
        weighted_hours += res.soltimer_varjevndogn * building.bra_m2
        weighted_bra += building.bra_m2

    open_ground = _open_ground_polygon(buildable_poly, buildings)
    open_points = _grid_points_in_geometry(open_ground, spacing_m=DEFAULT_OPEN_SPACE_GRID_M) if open_ground is not None and not open_ground.is_empty else []
    open_hits = 0.0
    open_totals = 0.0
    open_equinox_hits = 0.0
    for inst in instants:
        shadow_union = obstacle_shadow_union(building_occluders + neighbour_occluders, inst)
        is_equinox = (inst.timestamp.month, inst.timestamp.day) == (3, 21)
        for point in open_points:
            open_totals += 1.0
            if shadow_union is None or not shadow_union.buffer(1e-6).covers(point):
                open_hits += 1.0
                if is_equinox:
                    open_equinox_hits += 1.0

    key_moments: List[SolKeyMoment] = []
    for month, day, hour, label in [
        (3, 21, 12, "21. mars 12:00"),
        (3, 21, 15, "21. mars 15:00"),
        (6, 21, 12, "21. juni 12:00"),
        (6, 21, 15, "21. juni 15:00"),
        (12, 21, 12, "21. desember 12:00"),
    ]:
        inst = solar_position(latitude_deg, longitude_deg, datetime(year, month, day, hour, 0, 0))
        key_moments.append(SolKeyMoment(label=label, elevation_deg=inst.elevation_deg, azimuth_deg=inst.azimuth_deg, representative_shadow_m=round(horizontal_shadow_length(DEFAULT_SHADOW_REF_HEIGHT_M, inst.elevation_deg), 1)))

    req_hours = None if rules is None else (rules.sol_krav_timer_varjevndogn if rules.sol_krav_timer_varjevndogn is not None else rules.mua_min_sol_timer)
    mua_hours = open_equinox_hits / max(1, len(open_points)) if open_points else 0.0
    mua_fraction = (open_equinox_hits / max(1.0, open_totals / max(1, sum(1 for inst in instants if (inst.timestamp.month, inst.timestamp.day)==(3,21))))) if open_points and open_totals > 0 else 0.0
    return SolReport(
        per_building=per_building,
        total_sol_score=round(weighted_score / weighted_bra, 1) if weighted_bra > 0 else 0.0,
        project_soltimer_varjevndogn=round(weighted_hours / weighted_bra, 2) if weighted_bra > 0 else 0.0,
        mua_soltimer_varjevndogn=round(mua_hours, 2) if open_points else None,
        mua_sun_compliant=(None if req_hours is None else bool(mua_hours >= req_hours and mua_fraction >= 0.5)),
        solbelyst_uteareal_pct=round(100.0 * open_hits / open_totals, 1) if open_totals > 0 else 0.0,
        vinter_skygge_kl_12_m=next((km.representative_shadow_m for km in key_moments if km.label == "21. desember 12:00"), 0.0),
        sommerskygge_kl_15_m=next((km.representative_shadow_m for km in key_moments if km.label == "21. juni 15:00"), 0.0),
        key_moments=key_moments,
        analysis_samples=len(instants),
        notes=[f"Solar backend: {'pvlib' if HAS_PVLIB else 'fallback'}"],
    )


def calculate_sol_report(buildable_poly: Polygon, buildings: Sequence[Bygg], *, latitude_deg: float, longitude_deg: float, neighbors: Optional[Sequence[Dict[str, Any]]] = None, rules: Optional[PlanRegler] = None, year: int = 2026) -> SolReport:
    return compute_sol_report(buildable_poly, buildings, latitude_deg=latitude_deg, longitude_deg=longitude_deg, neighbors=neighbors, rules=rules, year=year)


def analyze_solar(*, bygg: Sequence[Bygg], delfelt: Sequence[object], latitude_deg: float, longitude_deg: Optional[float] = None, neighbors: Optional[Sequence[object]] = None, terrain: Optional[object] = None, plan_regler: Optional[PlanRegler] = None, year: int = 2026, buildable_polygon: Optional[Polygon] = None, **_: Any) -> SolReport:
    del terrain
    if buildable_polygon is None and delfelt:
        buildable_polygon = unary_union([getattr(field, 'polygon') for field in delfelt if getattr(field, 'polygon', None) is not None]).buffer(0)
    if buildable_polygon is None:
        buildable_polygon = Polygon()
    return compute_sol_report(buildable_polygon, list(bygg), latitude_deg=latitude_deg, longitude_deg=float(DEFAULT_LONGITUDE_DEG if longitude_deg is None else longitude_deg), neighbors=[n for n in (neighbors or []) if isinstance(n, dict)], rules=plan_regler, year=year)
