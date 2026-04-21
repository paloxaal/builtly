
from __future__ import annotations

import html
from typing import Iterable, List

from shapely.geometry import Polygon

from .masterplan_types import ComplianceItem, Masterplan

BG = "#061423"
PANEL = "#0a1b2e"
STROKE = "#38bdf8"
TEXT = "#dbe7f5"
MUTED = "#9fb4c8"
FIELD_COLORS = ["#0f766e", "#1d4ed8", "#6d28d9", "#78716c", "#0f766e", "#1e3a8a"]
BUILDING = "#e8eef7"
BAKKE = "#0f766e"
TAK = "#1d4ed8"
PRIVAT = "#fb7185"
TOTAL = "#38bdf8"


def _project_shapes(polygons: Iterable[Polygon], frame) -> list[str]:
    minx, miny, maxx, maxy = frame
    width = max(maxx - minx, 1.0)
    height = max(maxy - miny, 1.0)
    pad = 30
    canvas_w = 860
    canvas_h = 620
    scale = min((canvas_w - pad * 2) / width, (canvas_h - pad * 2) / height)

    def point(x, y):
        sx = pad + (x - minx) * scale
        sy = canvas_h - (pad + (y - miny) * scale)
        return sx, sy

    svg_polys: list[str] = []
    for poly in polygons:
        pts = " ".join(f"{point(x, y)[0]:.1f},{point(x, y)[1]:.1f}" for x, y in poly.exterior.coords)
        svg_polys.append(pts)
    return svg_polys


def render_concept_svg(plan: Masterplan) -> str:
    frame = plan.buildable_polygon.bounds
    field_pts = _project_shapes([f.polygon for f in plan.delfelt], frame)
    building_pts = _project_shapes([b.footprint for b in plan.bygg], frame)
    labels = []
    minx, miny, maxx, maxy = frame
    width = max(maxx - minx, 1.0)
    height = max(maxy - miny, 1.0)
    pad = 30
    canvas_w = 860
    canvas_h = 620
    scale = min((canvas_w - pad * 2) / width, (canvas_h - pad * 2) / height)

    def point(x, y):
        sx = pad + (x - minx) * scale
        sy = canvas_h - (pad + (y - miny) * scale)
        return sx, sy

    for idx, f in enumerate(plan.delfelt):
        cx, cy = point(f.polygon.centroid.x, f.polygon.centroid.y)
        labels.append(
            f'<g><rect x="{cx-88:.1f}" y="{cy-20:.1f}" width="176" height="40" rx="10" fill="#10243c" stroke="{STROKE}" opacity="0.95"/>'
            f'<text x="{cx:.1f}" y="{cy+6:.1f}" text-anchor="middle" font-size="18" fill="{TEXT}" font-family="Inter, Arial">'
            f'{html.escape(f"{f.field_id} · {f.typology.value} · trinn {f.phase}")}</text></g>'
        )

    bullets = [
        f"{len(plan.delfelt)} delfelt organiserer bebyggelsen i et lesbart system.",
        f"Typologimiks: {', '.join(sorted({f.typology.value for f in plan.delfelt}))}.",
        f"Total BRA {plan.total_bra_m2:,.0f} m² og BYA {plan.total_bya_m2:,.0f} m².".replace(",", " "),
        f"Solscore {plan.sol_report.total_score:.0f}/100 og MUA-status {'JA' if all(c.status != 'NEI' for c in plan.mua_report.compliant if c.required is not None) else 'DELVIS'}.",
    ]

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="760" viewBox="0 0 1400 760">',
        f'<rect width="1400" height="760" fill="{BG}"/>',
        f'<rect x="18" y="18" width="1364" height="724" rx="26" fill="{PANEL}" stroke="#12365b"/>',
        f'<rect x="405" y="58" width="940" height="640" rx="22" fill="#04111d" stroke="#12365b"/>',
        f'<text x="70" y="90" fill="{MUTED}" font-size="34" font-family="Inter, Arial" font-weight="600">BUILTLY · KONSEPT</text>',
        f'<text x="70" y="154" fill="{TEXT}" font-size="64" font-family="Inter, Arial" font-weight="800">KVARTALSTRUKTUR</text>',
        f'<text x="70" y="208" fill="{MUTED}" font-size="28" font-family="Inter, Arial">{html.escape(plan.display_title or plan.concept_family.value)}</text>',
    ]
    y = 270
    for bullet in bullets:
        svg.append(f'<text x="80" y="{y}" fill="{TEXT}" font-size="24" font-family="Inter, Arial">• {html.escape(bullet)}</text>')
        y += 54

    for idx, pts in enumerate(field_pts):
        svg.append(f'<polygon points="{pts}" fill="{FIELD_COLORS[idx % len(FIELD_COLORS)]}" opacity="0.34" stroke="{STROKE}" stroke-width="3"/>')
    for pts in building_pts:
        svg.append(f'<polygon points="{pts}" fill="{BUILDING}" stroke="#dfe7f4" stroke-width="1.5"/>')
    svg.extend(labels)
    svg.append('</svg>')
    return "".join(svg)


def render_mua_svg(plan: Masterplan) -> str:
    frame = plan.buildable_polygon.bounds
    buildings = _project_shapes([b.footprint for b in plan.bygg], frame)
    minx, miny, maxx, maxy = frame
    width = max(maxx - minx, 1.0)
    height = max(maxy - miny, 1.0)
    pad = 30
    canvas_w = 860
    canvas_h = 620
    scale = min((canvas_w - pad * 2) / width, (canvas_h - pad * 2) / height)

    def point(x, y):
        sx = pad + (x - minx) * scale
        sy = canvas_h - (pad + (y - miny) * scale)
        return sx, sy

    site_pts = " ".join(f"{point(x, y)[0]:.1f},{point(x, y)[1]:.1f}" for x, y in plan.buildable_polygon.exterior.coords)
    checks: list[ComplianceItem] = plan.mua_report.compliant
    legend_items = [
        ("Bakke", plan.mua_report.bakke, BAKKE),
        ("Tak", plan.mua_report.fellesareal - plan.mua_report.bakke, TAK),
        ("Privat", plan.mua_report.privat, PRIVAT),
        ("Totalt", plan.mua_report.total, TOTAL),
    ]

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="760" viewBox="0 0 1400 760">',
        f'<rect width="1400" height="760" fill="{BG}"/>',
        f'<rect x="18" y="18" width="1364" height="724" rx="26" fill="{PANEL}" stroke="#12365b"/>',
        f'<rect x="500" y="58" width="780" height="640" rx="22" fill="#04111d" stroke="#12365b"/>',
        f'<text x="70" y="90" fill="{MUTED}" font-size="34" font-family="Inter, Arial" font-weight="600">BUILTLY · BESTEMMELSER</text>',
        f'<text x="70" y="154" fill="{TEXT}" font-size="64" font-family="Inter, Arial" font-weight="800">MUA</text>',
        f'<text x="70" y="260" fill="{TEXT}" font-size="26" font-family="Inter, Arial" font-weight="700">Kontrollregler</text>',
    ]
    y = 320
    for item in checks:
        color = "#38bdf8" if item.status == "JA" else "#94a3b8" if item.status == "IKKE VURDERT" else "#fb7185"
        svg.append(f'<text x="70" y="{y}" fill="{color}" font-size="24" font-family="Inter, Arial">• {html.escape(item.key)}: {html.escape(item.status)}</text>')
        y += 46

    svg.append(f'<polygon points="{site_pts}" fill="{BAKKE}" opacity="0.45" stroke="{STROKE}" stroke-width="3"/>')
    for pts in buildings:
        svg.append(f'<polygon points="{pts}" fill="{BUILDING}" stroke="#dfe7f4" stroke-width="1.5"/>')

    svg.append(f'<rect x="1110" y="110" width="190" height="220" rx="18" fill="#081827" stroke="#21496f"/>')
    svg.append(f'<text x="1135" y="150" fill="{TEXT}" font-size="22" font-family="Inter, Arial" font-weight="700">MUA-oversikt</text>')
    ly = 190
    for name, value, color in legend_items:
        svg.append(f'<rect x="1135" y="{ly-18}" width="22" height="22" fill="{color}"/>')
        svg.append(f'<text x="1170" y="{ly}" fill="{TEXT}" font-size="18" font-family="Inter, Arial">{html.escape(name)}: {value:,.0f} m²</text>'.replace(",", " "))
        ly += 42

    svg.append('</svg>')
    return "".join(svg)
