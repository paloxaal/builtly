"""
builtly_pdf_engine.py
─────────────────────
Profesjonell PDF-rapport-generator for Builtly-moduler.

Bruk:
    from builtly_pdf_engine import build_tdd_pdf

    pdf_bytes = build_tdd_pdf(
        project=project,
        ai_result=ai_result,
        rules=rules,
        delivery_level=delivery_level,
        disclaimer_text=disclaimer_for_level(delivery_level),
    )
    st.download_button("Last ned PDF", pdf_bytes, "tdd_rapport.pdf", "application/pdf")
"""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ─── Brand colours ────────────────────────────────────────────────────────────
NAVY   = colors.HexColor("#1B2D4F")
TEAL   = colors.HexColor("#0F6E56")
AMBER  = colors.HexColor("#BA7517")
BLUE   = colors.HexColor("#185FA5")
LGRAY  = colors.HexColor("#F1EFE8")
MGRAY  = colors.HexColor("#D3D1C7")
DGRAY  = colors.HexColor("#5F5E5A")
WHITE  = colors.white
BLACK  = colors.HexColor("#1A1A1A")

RISK_COLOURS = {
    "LAV":    colors.HexColor("#3B6D11"),
    "LÅV":    colors.HexColor("#3B6D11"),
    "LOW":    colors.HexColor("#3B6D11"),
    "MIDDELS":colors.HexColor("#BA7517"),
    "MEDIUM": colors.HexColor("#BA7517"),
    "HØY":    colors.HexColor("#A32D2D"),
    "HIGH":   colors.HexColor("#A32D2D"),
}

TG_COLOURS = {
    "TG0": colors.HexColor("#3B6D11"),
    "TG1": colors.HexColor("#0F6E56"),
    "TG2": colors.HexColor("#BA7517"),
    "TG3": colors.HexColor("#A32D2D"),
}

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

# ─── Filename sanitizer ───────────────────────────────────────────────────────
def sanitize_filename(name: str) -> str:
    """
    Konverterer filnavn med norske/spesielle tegn til ASCII-safe versjon.
    Bevarer originalt navn for visning – bruk sanitert navn mot HTTP API.
    """
    name = unicodedata.normalize("NFC", name)
    replacements = {
        "æ": "ae", "ø": "o", "å": "a",
        "Æ": "Ae", "Ø": "O", "Å": "A",
        "ä": "a", "ö": "o", "ü": "u",
        "Ä": "A", "Ö": "O", "Ü": "U",
    }
    for char, rep in replacements.items():
        name = name.replace(char, rep)
    # Fjern alle gjenværende non-ASCII
    name = re.sub(r"[^\x00-\x7F]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name


# ─── Style factory ────────────────────────────────────────────────────────────
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "cover_title", parent=base["Normal"],
            fontSize=28, leading=34, textColor=WHITE,
            fontName="Helvetica-Bold", alignment=TA_LEFT,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", parent=base["Normal"],
            fontSize=12, leading=16, textColor=colors.HexColor("#B4B2A9"),
            fontName="Helvetica", alignment=TA_LEFT,
        ),
        "cover_meta": ParagraphStyle(
            "cover_meta", parent=base["Normal"],
            fontSize=10, leading=14, textColor=colors.HexColor("#D3D1C7"),
            fontName="Helvetica",
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Normal"],
            fontSize=16, leading=20, textColor=NAVY,
            fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=4,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Normal"],
            fontSize=13, leading=17, textColor=NAVY,
            fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=3,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Normal"],
            fontSize=11, leading=15, textColor=TEAL,
            fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=2,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"],
            fontSize=10, leading=14, textColor=BLACK,
            fontName="Helvetica", spaceAfter=4,
        ),
        "body_muted": ParagraphStyle(
            "body_muted", parent=base["Normal"],
            fontSize=9, leading=13, textColor=DGRAY,
            fontName="Helvetica", spaceAfter=3,
        ),
        "disclaimer": ParagraphStyle(
            "disclaimer", parent=base["Normal"],
            fontSize=8.5, leading=12, textColor=DGRAY,
            fontName="Helvetica-Oblique",
            borderColor=AMBER, borderWidth=0.5,
            borderPadding=(6, 8, 6, 8),
            backColor=colors.HexColor("#FAEEDA"),
        ),
        "table_header": ParagraphStyle(
            "table_header", parent=base["Normal"],
            fontSize=9, leading=12, textColor=WHITE,
            fontName="Helvetica-Bold",
        ),
        "table_cell": ParagraphStyle(
            "table_cell", parent=base["Normal"],
            fontSize=9, leading=12, textColor=BLACK,
            fontName="Helvetica",
        ),
        "tag": ParagraphStyle(
            "tag", parent=base["Normal"],
            fontSize=8, leading=10, textColor=WHITE,
            fontName="Helvetica-Bold", alignment=TA_CENTER,
        ),
        "footer": ParagraphStyle(
            "footer", parent=base["Normal"],
            fontSize=8, leading=10, textColor=DGRAY,
            fontName="Helvetica",
        ),
        "metric_val": ParagraphStyle(
            "metric_val", parent=base["Normal"],
            fontSize=20, leading=24, textColor=NAVY,
            fontName="Helvetica-Bold", alignment=TA_CENTER,
        ),
        "metric_lbl": ParagraphStyle(
            "metric_lbl", parent=base["Normal"],
            fontSize=8, leading=11, textColor=DGRAY,
            fontName="Helvetica", alignment=TA_CENTER,
        ),
    }


def _hr(color=MGRAY, thickness=0.5) -> HRFlowable:
    return HRFlowable(width="100%", thickness=thickness, color=color, spaceAfter=4, spaceBefore=4)


def _sp(h: float = 4) -> Spacer:
    return Spacer(1, h * mm)


# ─── Header / footer callback ─────────────────────────────────────────────────
class _PageDecorator:
    def __init__(self, project: dict, delivery_level: str):
        self.project = project
        self.level = delivery_level
        self.level_colours = {"auto": TEAL, "reviewed": AMBER, "attested": BLUE}

    def __call__(self, canvas, doc):
        canvas.saveState()
        w, h = A4

        # ── Top bar
        canvas.setFillColor(NAVY)
        canvas.rect(0, h - 14 * mm, w, 14 * mm, fill=True, stroke=False)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColor(WHITE)
        canvas.drawString(MARGIN, h - 9 * mm, "BUILTLY  |  Teknisk Due Diligence")
        proj_name = self.project.get("name") or self.project.get("project_name") or "Rapport"
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#9c9a92"))
        canvas.drawRightString(w - MARGIN, h - 9 * mm, proj_name)

        # ── Delivery level badge (top right pill)
        lc = self.level_colours.get(self.level, TEAL)
        badge_w, badge_h = 28 * mm, 5 * mm
        bx = w - MARGIN - badge_w
        by = h - 12.5 * mm
        canvas.setFillColor(lc)
        canvas.roundRect(bx, by, badge_w, badge_h, 2 * mm, fill=True, stroke=False)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.setFillColor(WHITE)
        label = {"auto": "AUTO-RAPPORT", "reviewed": "REVIEWED", "attested": "ATTESTERT"}.get(self.level, self.level.upper())
        canvas.drawCentredString(bx + badge_w / 2, by + 1.5 * mm, label)

        # ── Bottom bar
        canvas.setFillColor(LGRAY)
        canvas.rect(0, 0, w, 10 * mm, fill=True, stroke=False)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(DGRAY)
        canvas.drawString(MARGIN, 3.5 * mm, f"Generert: {datetime.now().strftime('%d.%m.%Y %H:%M')}  ·  AI-assisted engineering. Human-verified.")
        canvas.drawRightString(w - MARGIN, 3.5 * mm, f"Side {doc.page}")

        canvas.restoreState()


# ─── Section helpers ──────────────────────────────────────────────────────────
def _section_heading(title: str, S: dict) -> list:
    return [
        _sp(3),
        _hr(NAVY, 1.0),
        Paragraph(title, S["h1"]),
        _hr(MGRAY, 0.4),
        _sp(2),
    ]


def _kv_table(rows: list[tuple[str, str]], S: dict) -> Table:
    """Key-value info table with alternating rows."""
    data = [[Paragraph(k, S["body_muted"]), Paragraph(str(v), S["body"])] for k, v in rows]
    col_w = [50 * mm, CONTENT_W - 50 * mm]
    t = Table(data, colWidths=col_w)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LGRAY),
        ("GRID",        (0, 0), (-1, -1), 0.3, MGRAY),
        ("VALIGN",      (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        *[("BACKGROUND", (0, i), (-1, i), WHITE) for i in range(1, len(data), 2)],
    ]))
    return t


def _data_table(headers: list[str], rows: list[list], S: dict,
                col_widths: list[float] | None = None,
                row_colours: dict[int, Any] | None = None) -> Table:
    """Generic data table with navy header row."""
    header_row = [Paragraph(h, S["table_header"]) for h in headers]
    body_rows  = [[Paragraph(str(cell or "–"), S["table_cell"]) for cell in row] for row in rows]
    data = [header_row] + body_rows

    if col_widths is None:
        col_widths = [CONTENT_W / len(headers)] * len(headers)

    t = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND",   (0, 0), (-1, 0),  NAVY),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  WHITE),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, 0),  9),
        ("GRID",         (0, 0), (-1, -1), 0.3, MGRAY),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LGRAY]),
    ]
    if row_colours:
        for row_idx, colour in row_colours.items():
            style_cmds.append(("BACKGROUND", (0, row_idx + 1), (-1, row_idx + 1), colour))
    t.setStyle(TableStyle(style_cmds))
    return t


def _metric_cards(metrics: list[dict], S: dict) -> Table:
    """
    Renders a row of metric cards.
    metrics = [{"label": str, "value": str, "sub": str}, ...]
    """
    card_w = CONTENT_W / max(len(metrics), 1)
    cells = []
    for m in metrics:
        val_col = RISKVAL_COLOUR(m.get("value", ""))
        inner = [
            [Paragraph(m.get("value", "–"), ParagraphStyle(
                "mv", parent=S["metric_val"], textColor=val_col))],
            [Paragraph(m.get("label", ""), S["metric_lbl"])],
        ]
        if m.get("sub"):
            inner.append([Paragraph(m["sub"], S["body_muted"])])
        inner_t = Table(inner, colWidths=[card_w - 4 * mm])
        inner_t.setStyle(TableStyle([
            ("ALIGN",   (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",  (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 3),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ]))
        cells.append(inner_t)

    t = Table([cells], colWidths=[card_w] * len(metrics))
    t.setStyle(TableStyle([
        ("BOX",        (0, 0), (-1, -1), 0.4, MGRAY),
        ("INNERGRID",  (0, 0), (-1, -1), 0.4, MGRAY),
        ("BACKGROUND", (0, 0), (-1, -1), LGRAY),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def RISKVAL_COLOUR(val: str) -> Any:
    """Return a colour based on risk/TG value string."""
    up = str(val).upper()
    for k, c in RISK_COLOURS.items():
        if k in up:
            return c
    for k, c in TG_COLOURS.items():
        if k in up:
            return c
    return NAVY


# ─── Cover page ───────────────────────────────────────────────────────────────
def _cover_page(project: dict, delivery_level: str, disclaimer_text: str, S: dict) -> list:
    story = []
    w, h = A4

    # Full-bleed navy background drawn via a Frame callback is complex in Platypus.
    # Use a tall coloured table instead.
    level_labels = {"auto": "Auto-rapport", "reviewed": "Reviewed rapport", "attested": "Signert attestasjon"}
    level_colours_hex = {"auto": "#0F6E56", "reviewed": "#BA7517", "attested": "#185FA5"}

    proj_name = project.get("name") or project.get("project_name") or "Prosjekt"
    address   = project.get("address") or project.get("adresse") or "–"
    client    = project.get("client")  or project.get("klient")  or "–"
    bta       = project.get("bta")     or project.get("area")    or "–"
    prop_type = project.get("type")    or project.get("property_type") or "–"
    date_str  = datetime.now().strftime("%d. %B %Y")

    cover_inner = [
        [Paragraph("BUILTLY AS", ParagraphStyle("cov0", fontSize=10, textColor=colors.HexColor("#9c9a92"), fontName="Helvetica-Bold", leading=14))],
        [Spacer(1, 8 * mm)],
        [Paragraph("Teknisk Due Diligence", ParagraphStyle("cov1", fontSize=11, textColor=TEAL, fontName="Helvetica-Bold", leading=14))],
        [Paragraph(proj_name, ParagraphStyle("cov2", fontSize=28, textColor=WHITE, fontName="Helvetica-Bold", leading=34, spaceAfter=2))],
        [Paragraph(address, ParagraphStyle("cov3", fontSize=13, textColor=colors.HexColor("#B4B2A9"), fontName="Helvetica", leading=17))],
        [Spacer(1, 10 * mm)],
        [_hr(colors.HexColor("#2E4A6B"), 0.5)],
        [Spacer(1, 5 * mm)],
        [_kv_table([
            ("Klient", client),
            ("Eiendomstype", prop_type),
            ("BTA", str(bta)),
            ("Dato", date_str),
            ("Leveransenivå", level_labels.get(delivery_level, delivery_level)),
        ], {**S, "body_muted": ParagraphStyle("bm_cov", fontSize=9, textColor=colors.HexColor("#7a9ab5"), fontName="Helvetica"),
                  "body":      ParagraphStyle("b_cov",  fontSize=9, textColor=colors.HexColor("#ccd8e4"), fontName="Helvetica")})],
        [Spacer(1, 8 * mm)],
        [Paragraph(
            disclaimer_text or "Dette er et Builtly-generert dokument. Se disclaimerboks for fullstendig ansvarsfraskrivelse.",
            ParagraphStyle("cov_disc", fontSize=8, textColor=colors.HexColor("#7a9ab5"), fontName="Helvetica-Oblique", leading=12)
        )],
    ]

    cover_data = [[item[0]] for item in cover_inner]
    cover_table = Table(cover_data, colWidths=[CONTENT_W])
    cover_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))

    story.append(cover_table)
    story.append(PageBreak())
    return story


# ─── Section builders ─────────────────────────────────────────────────────────
def _section_project_info(project: dict, rules: dict, S: dict) -> list:
    story = _section_heading("Prosjektinformasjon", S)
    rows = [
        ("Prosjektnavn",     project.get("name") or project.get("project_name") or "–"),
        ("Adresse",          project.get("address") or "–"),
        ("Klient",           project.get("client") or "–"),
        ("Eiendomstype",     rules.get("property_type") or "–"),
        ("Byggeår",          str(rules.get("build_year") or "–")),
        ("Markedsverdi",     f"{rules.get('market_value_mnok', '–')} MNOK"),
        ("Brukssituasjon",   rules.get("transaction_stage") or "–"),
        ("Datakompletthet",  f"{int((rules.get('data_completeness_score') or 0) * 100)} %"),
        ("Matrikkelnummer",  rules.get("matrikkel_id") or "–"),
    ]
    story.append(_kv_table(rows, S))
    return story


def _section_disclaimer(disclaimer_text: str, delivery_level: str, S: dict) -> list:
    story = _section_heading("Leveransenivå og ansvarsforhold", S)
    level_desc = {
        "auto":     "Nivå 1 – Auto-rapport: Fullt automatisert. Ingen fagperson-review. Dette er et AI-generert dataprodukt og ikke en faglig attestasjon. Kan ikke benyttes som grunnlag for byggesøknad eller juridisk bindende avtale.",
        "reviewed": "Nivå 2 – Reviewed rapport: AI-utkast gjennomgått av fagperson (30–90 min). Fagperson attesterer at output er plausibelt og rimelig. Ikke signert med ansvarsrett. Ikke egnet som grunnlag for byggesøknad.",
        "attested": "Nivå 3 – Signert attestasjon: Full HITL med faglig signatur og ansvarsrett. Fagperson og foretak bærer profesjonsansvar dekket av profesjonsansvarsforsikring.",
    }
    story.append(Paragraph(level_desc.get(delivery_level, ""), S["disclaimer"]))
    if disclaimer_text:
        story.append(_sp(2))
        story.append(Paragraph(disclaimer_text, S["body_muted"]))
    return story


def _section_executive_summary(ai_data: dict, S: dict) -> list:
    story = _section_heading("Sammendrag", S)
    summary = ai_data.get("executive_summary") or "Sammendrag ikke tilgjengelig. Last opp dokumentasjon og kjør analysen på nytt."
    story.append(Paragraph(summary, S["body"]))
    return story


def _section_metrics(rules: dict, ai_data: dict, S: dict) -> list:
    story = _section_heading("Nøkkeltall", S)
    risk_matrix = ai_data.get("risk_matrix") or rules.get("risk_matrix") or {}
    metrics = [
        {"label": "Datakompletthet",  "value": f"{int((rules.get('data_completeness_score') or 0) * 100)} %",
         "sub": "Andel av nødvendig TDD-underlag"},
        {"label": "Samlet klasse",    "value": risk_matrix.get("overall_class") or "–",
         "sub": "Aggregert risikoklasse"},
        {"label": "Utbedringskost",   "value": f"{int(risk_matrix.get('remediation_cost_total_nok') or 0):,} NOK".replace(",", " "),
         "sub": "Estimert totalutbedring"},
        {"label": "Teknisk risiko",   "value": risk_matrix.get("technical_risk") or "–",
         "sub": "Basert på TG-vurderinger"},
        {"label": "Finansiell risiko","value": risk_matrix.get("financial_risk") or "–",
         "sub": "Kostnad vs. markedsverdi"},
        {"label": "Regulatorisk",     "value": risk_matrix.get("regulatory_risk") or "–",
         "sub": "TEK17-avvik"},
    ]
    story.append(_metric_cards(metrics, S))
    return story


def _section_building_parts(ai_data: dict, rules: dict, S: dict) -> list:
    story = _section_heading("Bygningsdeler – Tilstandsgradering", S)
    parts = ai_data.get("building_parts") or rules.get("building_parts") or []
    if not parts:
        story.append(Paragraph("Ingen bygningsdeldata tilgjengelig.", S["body_muted"]))
        return story

    headers = ["Bygningsdel", "TG", "Restlevetid (år)", "Utbedringskost (NOK)", "Merknad", "Kilde"]
    keys    = ["part", "tg", "remaining_life_years", "remediation_cost_range_nok", "reason", "source"]
    rows    = [[str(p.get(k) or "–") for k in keys] for p in parts]
    col_w   = [35*mm, 12*mm, 22*mm, 32*mm, 52*mm, 25*mm]

    # Colour TG column
    row_colours = {}
    for i, p in enumerate(parts):
        tg = str(p.get("tg") or "").upper()
        c = TG_COLOURS.get(tg)
        if c:
            row_colours[i] = colors.Color(c.red, c.green, c.blue, alpha=0.12)

    story.append(_data_table(headers, rows, S, col_widths=col_w, row_colours=row_colours))
    story.append(_sp(2))
    story.append(Paragraph("TG0 = Ingen avvik  ·  TG1 = Svake avvik  ·  TG2 = Middels avvik  ·  TG3 = Store/kritiske avvik", S["body_muted"]))
    return story


def _section_tek17(ai_data: dict, rules: dict, S: dict) -> list:
    story = _section_heading("TEK17-avvik", S)
    devs = ai_data.get("tek17_deviations") or rules.get("tek17_deviations") or []
    if not devs:
        story.append(Paragraph("Ingen TEK17-avvik identifisert.", S["body_muted"]))
        return story

    headers = ["Tittel", "Kategori", "Anbefaling", "Kilde"]
    keys    = ["title", "category", "recommendation", "source"]
    rows    = [[str(d.get(k) or "–") for k in keys] for d in devs]
    col_w   = [40*mm, 25*mm, 80*mm, 33*mm]

    row_colours = {}
    for i, d in enumerate(devs):
        cat = str(d.get("category") or "").upper()
        if "KRITISK" in cat or "CRITICAL" in cat:
            row_colours[i] = colors.Color(0.64, 0.18, 0.18, alpha=0.10)
        elif "VESENTLIG" in cat or "SIGNIFICANT" in cat:
            row_colours[i] = colors.Color(0.73, 0.46, 0.09, alpha=0.10)

    story.append(_data_table(headers, rows, S, col_widths=col_w, row_colours=row_colours))
    return story


def _section_risk_matrix(ai_data: dict, rules: dict, S: dict) -> list:
    story = _section_heading("Risikomatrise", S)
    rm = ai_data.get("risk_matrix") or rules.get("risk_matrix") or {}
    rows = [
        ("Teknisk risiko",     rm.get("technical_risk")         or "–"),
        ("Finansiell risiko",  rm.get("financial_risk")         or "–"),
        ("Regulatorisk risiko",rm.get("regulatory_risk")        or "–"),
        ("Samlet klasse",      rm.get("overall_class")          or "–"),
        ("Utbedringskost",     f"{int(rm.get('remediation_cost_total_nok') or 0):,} NOK".replace(",", " ")),
    ]
    story.append(_kv_table(rows, S))
    return story


def _section_next_actions(ai_data: dict, S: dict) -> list:
    story = _section_heading("Anbefalte neste steg", S)
    actions = ai_data.get("next_actions") or []
    if not actions:
        story.append(Paragraph("Ingen anbefalinger tilgjengelig.", S["body_muted"]))
        return story
    headers = ["Tiltak", "Ansvar", "Prioritet", "Begrunnelse"]
    keys    = ["action", "owner", "priority", "why"]
    rows    = [[str(a.get(k) or "–") for k in keys] for a in actions]
    col_w   = [55*mm, 28*mm, 22*mm, 73*mm]
    story.append(_data_table(headers, rows, S, col_widths=col_w))
    return story


def _section_gaps(ai_data: dict, S: dict) -> list:
    story = _section_heading("Identifiserte mangler i underlaget", S)
    gaps = ai_data.get("gaps") or []
    if not gaps:
        story.append(Paragraph("Ingen mangler identifisert.", S["body_muted"]))
        return story
    for g in gaps:
        val = g.get("value") or str(g)
        story.append(Paragraph(f"• {val}", S["body"]))
    return story


def _section_public_data(rules: dict, S: dict) -> list:
    story = _section_heading("Offentlige datakilder", S)
    snap = rules.get("public_data_snapshot") or []
    if not snap:
        story.append(Paragraph("Ingen offentlige datakilder registrert.", S["body_muted"]))
        return story
    headers = ["Kilde", "Status", "Merknad", "Versjon"]
    keys    = ["source", "status", "note", "version"]
    rows    = [[str(s.get(k) or "–") for k in keys] for s in snap]
    col_w   = [40*mm, 20*mm, 95*mm, 23*mm]
    story.append(_data_table(headers, rows, S, col_widths=col_w))
    return story


def _section_manifest(records: list, S: dict) -> list:
    story = _section_heading("Dokumentoversikt", S)
    if not records:
        story.append(Paragraph("Ingen dokumenter lastet opp.", S["body_muted"]))
        return story
    headers = ["Filnavn", "Type", "Størrelse"]
    rows    = [
        [
            r.get("original_name") or r.get("name") or "–",
            r.get("type") or "–",
            f"{int((r.get('size') or 0) / 1024)} KB",
        ]
        for r in records
    ]
    col_w = [100*mm, 40*mm, 38*mm]
    story.append(_data_table(headers, rows, S, col_widths=col_w))
    return story


def _section_audit(module_key: str, delivery_level: str, records: list, ai_result: dict, S: dict) -> list:
    story = _section_heading("Revisjonslogg (Audit Trail)", S)
    entries = [
        ("Tidspunkt",       datetime.now().strftime("%d.%m.%Y %H:%M:%S")),
        ("Modul",           module_key.upper()),
        ("Leveransenivå",   delivery_level),
        ("Antall filer",    str(len(records))),
        ("AI-modell",       ai_result.get("model") or "–"),
        ("Forsøk",          str(len(ai_result.get("attempt_log") or []))),
        ("Status",          ai_result.get("status") or "–"),
    ]
    story.append(_kv_table(entries, S))
    return story


# ─── Main builder ─────────────────────────────────────────────────────────────
def build_tdd_pdf(
    project: dict,
    ai_result: dict,
    rules: dict,
    delivery_level: str = "auto",
    disclaimer_text: str = "",
    records: list | None = None,
) -> bytes:
    """
    Bygger en komplett PDF-rapport for TDD-modulen og returnerer den som bytes.

    Parametere
    ----------
    project         : Prosjektdata-dict fra configure_page()
    ai_result       : Returverdien fra run_module_analysis()
    rules           : Returverdien fra tdd_rules_payload()
    delivery_level  : "auto" | "reviewed" | "attested"
    disclaimer_text : Disclaimer-tekst fra disclaimer_for_level()
    records         : Liste av normalize_uploaded_files()

    Returnerer
    ----------
    bytes  –  PDF som kan sendes direkte til st.download_button()
    """
    buf = io.BytesIO()
    S   = _styles()
    decorator = _PageDecorator(project, delivery_level)
    records = records or []
    ai_data = (ai_result.get("data") or {})

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=18 * mm,
        bottomMargin=15 * mm,
        title=f"TDD – {project.get('name') or 'Rapport'}",
        author="Builtly AS",
        subject="Teknisk Due Diligence",
        creator="Builtly PDF Engine v1.0",
    )

    story: list = []

    # 1 – Forside
    story += _cover_page(project, delivery_level, disclaimer_text, S)

    # 2 – Prosjektinfo
    story += _section_project_info(project, rules, S)
    story.append(_sp(4))

    # 3 – Leveransenivå / disclaimer
    story += _section_disclaimer(disclaimer_text, delivery_level, S)
    story.append(_sp(4))

    # 4 – Sammendrag
    story += _section_executive_summary(ai_data, S)
    story.append(_sp(4))

    # 5 – Nøkkeltall (metrics dashboard)
    story += _section_metrics(rules, ai_data, S)
    story.append(PageBreak())

    # 6 – Bygningsdeler
    story += _section_building_parts(ai_data, rules, S)
    story.append(_sp(4))

    # 7 – Risikomatrise
    story += _section_risk_matrix(ai_data, rules, S)
    story.append(_sp(4))

    # 8 – TEK17-avvik
    story += _section_tek17(ai_data, rules, S)
    story.append(PageBreak())

    # 9 – Anbefalinger
    story += _section_next_actions(ai_data, S)
    story.append(_sp(4))

    # 10 – Mangler
    story += _section_gaps(ai_data, S)
    story.append(_sp(4))

    # 11 – Offentlige datakilder
    story += _section_public_data(rules, S)
    story.append(PageBreak())

    # 12 – Dokumentoversikt
    story += _section_manifest(records, S)
    story.append(_sp(4))

    # 13 – Audit trail
    story += _section_audit("tdd", delivery_level, records, ai_result, S)

    doc.build(story, onFirstPage=decorator, onLaterPages=decorator)
    return buf.getvalue()
