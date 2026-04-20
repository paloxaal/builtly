from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional

from shapely.geometry import Polygon
from shapely.ops import unary_union

from .masterplan_types import ComplianceState, Masterplan, Typology

_BG = "#06111d"
_PANEL = "#0b1726"
_TEXT = "#e6f0f8"
_MUTED = "#95a8bc"
_ACCENT = "#38bdf8"
_LIGHT = "#dbeafe"
_BUILDING = "#f8fafc"
_BUILDING_STROKE = "#94a3b8"
_PRIVATE = "#fb7185"
_ROOF = "#1d4ed8"
_GROUND = "#14b8a6"

_FIELD_COLORS = [
    "rgba(56,189,248,0.18)",
    "rgba(16,185,129,0.16)",
    "rgba(250,204,21,0.16)",
    "rgba(168,85,247,0.16)",
    "rgba(244,114,182,0.16)",
    "rgba(251,146,60,0.16)",
]


def _bounds(plan: Masterplan) -> Tuple[float, float, float, float]:
    poly = plan.buildable_polygon
    if poly is None or poly.is_empty:
        raise ValueError("Masterplan mangler buildable_polygon")
    return poly.bounds


def _project(x: float, y: float, *, bounds: Tuple[float, float, float, float], width: int, height: int, margin: int) -> Tuple[float, float]:
    minx, miny, maxx, maxy = bounds
    dx = max(maxx - minx, 1.0)
    dy = max(maxy - miny, 1.0)
    sx = (width - 2 * margin) / dx
    sy = (height - 2 * margin) / dy
    s = min(sx, sy)
    px = margin + (x - minx) * s
    py = height - margin - (y - miny) * s
    return px, py


def _poly_to_svg_points(poly: Polygon, *, bounds: Tuple[float, float, float, float], width: int, height: int, margin: int) -> str:
    pts = [_project(x, y, bounds=bounds, width=width, height=height, margin=margin) for x, y in poly.exterior.coords]
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


@dataclass
class _LabelBox:
    x: float
    y: float
    w: float
    h: float

    def overlaps(self, other: "_LabelBox") -> bool:
        return not (self.x + self.w < other.x or other.x + other.w < self.x or self.y + self.h < other.y or other.y + other.h < self.y)


def _place_labels(plan: Masterplan, *, bounds: Tuple[float, float, float, float], width: int, height: int, margin: int) -> List[str]:
    labels: List[str] = []
    occupied: List[_LabelBox] = []
    for idx, field in enumerate(plan.delfelt):
        cx, cy = field.polygon.centroid.x, field.polygon.centroid.y
        px, py = _project(cx, cy, bounds=bounds, width=width, height=height, margin=margin)
        text = f"{field.field_id} · {field.typology.value} · trinn {field.phase}"
        w = max(120.0, min(260.0, 7.2 * len(text)))
        h = 26.0
        candidates = [
            _LabelBox(px - w / 2, py - h / 2, w, h),
            _LabelBox(px + 12, py - 10, w, h),
            _LabelBox(px - w - 12, py - 10, w, h),
            _LabelBox(px - w / 2, py - 40, w, h),
            _LabelBox(px - w / 2, py + 14, w, h),
        ]
        chosen = candidates[0]
        for candidate in candidates:
            if candidate.x < 8 or candidate.y < 8 or candidate.x + candidate.w > width - 8 or candidate.y + candidate.h > height - 8:
                continue
            if all(not candidate.overlaps(prev) for prev in occupied):
                chosen = candidate
                break
        occupied.append(chosen)
        line = f'<g><rect x="{chosen.x:.1f}" y="{chosen.y:.1f}" rx="6" ry="6" width="{chosen.w:.1f}" height="{chosen.h:.1f}" fill="#0b1726" fill-opacity="0.88" stroke="#38bdf8" stroke-opacity="0.6"/><text x="{chosen.x + 10:.1f}" y="{chosen.y + 17:.1f}" font-family="Inter, Arial, sans-serif" font-size="12" fill="{_TEXT}">{text}</text></g>'
        labels.append(line)
    return labels


def render_quartalstruktur_svg(plan: Masterplan, *, width: int = 1100, height: int = 680) -> str:
    bounds = _bounds(plan)
    margin = 40
    items: List[str] = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    items.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="{_BG}"/>')
    items.append(f'<rect x="12" y="12" width="{width-24}" height="{height-24}" rx="18" fill="{_PANEL}" stroke="#0f2740"/>')
    items.append(f'<text x="40" y="56" font-family="Inter, Arial, sans-serif" font-size="18" fill="{_MUTED}" letter-spacing="1.2">BUILTLY · KONSEPT</text>')
    items.append(f'<text x="40" y="90" font-family="Inter, Arial, sans-serif" font-size="34" font-weight="700" fill="{_TEXT}">KVARTALSTRUKTUR</text>')
    items.append(f'<text x="40" y="122" font-family="Inter, Arial, sans-serif" font-size="14" fill="{_MUTED}">{plan.display_title or plan.concept_family.value}</text>')

    # Right-side drawing area
    draw_x = 330
    draw_y = 40
    draw_w = width - draw_x - 40
    draw_h = height - 80
    items.append(f'<rect x="{draw_x}" y="{draw_y}" width="{draw_w}" height="{draw_h}" rx="12" fill="#09131f" stroke="#12283f"/>')

    # Transform helper for drawing area
    def project_local(x: float, y: float) -> Tuple[float, float]:
        px, py = _project(x, y, bounds=bounds, width=draw_w, height=draw_h, margin=40)
        return px + draw_x, py + draw_y

    def poly_points_local(poly: Polygon) -> str:
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in [project_local(px, py) for px, py in poly.exterior.coords])

    # Site boundary
    site_points = poly_points_local(plan.buildable_polygon)
    items.append(f'<polygon points="{site_points}" fill="none" stroke="{_ACCENT}" stroke-width="3"/>')

    # Delfelt and buildings
    for idx, field in enumerate(plan.delfelt):
        color = _FIELD_COLORS[idx % len(_FIELD_COLORS)]
        items.append(f'<polygon points="{poly_points_local(field.polygon)}" fill="{color}" stroke="#38bdf8" stroke-opacity="0.55" stroke-width="1.4"/>')

    for building in plan.bygg:
        items.append(f'<polygon points="{poly_points_local(building.footprint)}" fill="{_BUILDING}" stroke="{_BUILDING_STROKE}" stroke-width="1.0"/>')

    labels = _place_labels(plan, bounds=plan.buildable_polygon.bounds, width=draw_w, height=draw_h, margin=40)
    for label in labels:
        # shift each label group into draw area by wrapping in translate
        items.append(f'<g transform="translate({draw_x},{draw_y})">{label}</g>')

    bullets = [
        f"• {len(plan.delfelt)} delfelt organiserer bebyggelsen i et lesbart system.",
        f"• Typologimiks: {', '.join(sorted({b.typology.value for b in plan.bygg}))}.",
        f"• Total BRA {plan.total_bra_m2:,.0f} m² og BYA {plan.total_bya_m2:,.0f} m².",
        f"• Solscore {plan.sol_report.total_sol_score:.0f}/100 og MUA-status {'JA' if plan.mua_report.compliant else 'NEI/IKKE VURDERT'}."
    ]
    y = 180
    for bullet in bullets:
        items.append(f'<text x="40" y="{y}" font-family="Inter, Arial, sans-serif" font-size="15" fill="{_TEXT}">{bullet}</text>')
        y += 28

    items.append('</svg>')
    return ''.join(items)


def render_mua_svg(plan: Masterplan, *, width: int = 1100, height: int = 680) -> str:
    bounds = _bounds(plan)
    margin = 40
    items: List[str] = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    items.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="{_BG}"/>')
    items.append(f'<rect x="12" y="12" width="{width-24}" height="{height-24}" rx="18" fill="{_PANEL}" stroke="#0f2740"/>')
    items.append(f'<text x="40" y="56" font-family="Inter, Arial, sans-serif" font-size="18" fill="{_MUTED}" letter-spacing="1.2">BUILTLY · BESTEMMELSER</text>')
    items.append(f'<text x="40" y="90" font-family="Inter, Arial, sans-serif" font-size="34" font-weight="700" fill="{_TEXT}">MUA</text>')

    draw_x = 330
    draw_y = 40
    draw_w = width - draw_x - 40
    draw_h = height - 80
    items.append(f'<rect x="{draw_x}" y="{draw_y}" width="{draw_w}" height="{draw_h}" rx="12" fill="#09131f" stroke="#12283f"/>')

    def project_local(x: float, y: float) -> Tuple[float, float]:
        px, py = _project(x, y, bounds=bounds, width=draw_w, height=draw_h, margin=40)
        return px + draw_x, py + draw_y

    def poly_points_local(poly: Polygon) -> str:
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in [project_local(px, py) for px, py in poly.exterior.coords])

    buildable = plan.buildable_polygon
    union_fp = unary_union([b.footprint for b in plan.bygg]) if plan.bygg else None
    open_ground = buildable.difference(union_fp).buffer(0) if union_fp else buildable

    # ground MUA areas
    if hasattr(open_ground, 'geoms'):
        parts = list(open_ground.geoms)
    else:
        parts = [open_ground]
    for part in parts:
        if part.is_empty:
            continue
        items.append(f'<polygon points="{poly_points_local(part)}" fill="{_GROUND}" fill-opacity="0.22" stroke="{_GROUND}" stroke-opacity="0.4" stroke-width="1"/>')

    for building in plan.bygg:
        items.append(f'<polygon points="{poly_points_local(building.footprint)}" fill="{_BUILDING}" stroke="{_BUILDING_STROKE}" stroke-width="1.0"/>')
        if building.tak_mua_m2 > 0:
            items.append(f'<polygon points="{poly_points_local(building.footprint)}" fill="{_ROOF}" fill-opacity="0.12" stroke="{_ROOF}" stroke-opacity="0.35" stroke-width="0.8"/>')
        if building.privat_mua_m2 > 0:
            items.append(f'<polygon points="{poly_points_local(building.footprint)}" fill="none" stroke="{_PRIVATE}" stroke-width="1.2" stroke-dasharray="4 3"/>')

    checks = plan.mua_report.checks
    if checks:
        y = 172
        items.append(f'<text x="40" y="146" font-family="Inter, Arial, sans-serif" font-size="16" fill="{_TEXT}">Kontrollregler</text>')
        for check in checks:
            color = _ACCENT if check.status == ComplianceState.JA else (_PRIVATE if check.status == ComplianceState.NEI else _MUTED)
            items.append(f'<text x="40" y="{y}" font-family="Inter, Arial, sans-serif" font-size="14" fill="{color}">• {check.rule_key}: {check.status.value}</text>')
            y += 22
    else:
        items.append(f'<text x="40" y="160" font-family="Inter, Arial, sans-serif" font-size="15" fill="{_MUTED}">Ingen MUA-regler vurdert.</text>')

    legend_x = width - 270
    legend_y = 70
    items.append(f'<rect x="{legend_x}" y="{legend_y}" width="210" height="150" rx="10" fill="#0b1726" stroke="#1e3a5f"/>')
    items.append(f'<text x="{legend_x+16}" y="{legend_y+28}" font-family="Inter, Arial, sans-serif" font-size="15" fill="{_TEXT}">MUA-oversikt</text>')
    rows = [
        (_GROUND, f"Bakke: {plan.mua_report.bakke:,.0f} m²"),
        (_ROOF, f"Tak: {plan.mua_report.tak:,.0f} m²"),
        (_PRIVATE, f"Privat: {plan.mua_report.privat:,.0f} m²"),
        (_ACCENT, f"Totalt: {plan.mua_report.total:,.0f} m²"),
    ]
    y = legend_y + 56
    for color, label in rows:
        items.append(f'<rect x="{legend_x+16}" y="{y-10}" width="14" height="14" fill="{color}"/>')
        items.append(f'<text x="{legend_x+40}" y="{y+2}" font-family="Inter, Arial, sans-serif" font-size="13" fill="{_TEXT}">{label}</text>')
        y += 24

    items.append('</svg>')
    return ''.join(items)
