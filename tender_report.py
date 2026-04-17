# -*- coding: utf-8 -*-
"""
Builtly | Tender Report Builder
─────────────────────────────────────────────────────────────────
ReportLab Platypus-basert rapport. Full norsk-støtte, Builtly
premium-stil, og innhold fra hele 3-pass-analysen.
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm, mm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageTemplate,
        Paragraph, Spacer, Table, TableStyle,
        PageBreak, KeepTogether, Image as RLImage,
    )
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False


# ─── Colors (Builtly palette) ────────────────────────────────────
BRAND_DARK = colors.HexColor("#06111a")
BRAND_INK = colors.HexColor("#1a2636")
BRAND_ACCENT = colors.HexColor("#38bdf8")
BRAND_WARM = colors.HexColor("#f59e0b")
BRAND_MUTED = colors.HexColor("#6b7a8c")
BRAND_SOFT = colors.HexColor("#c8d3df")
BRAND_LINE = colors.HexColor("#d9e2ec")
BRAND_BG_SOFT = colors.HexColor("#f4f7fa")

SEVERITY_COLORS = {
    "HIGH": colors.HexColor("#dc2626"),
    "MEDIUM": colors.HexColor("#d97706"),
    "LOW": colors.HexColor("#059669"),
}


def _register_fonts() -> str:
    """Register Inter if available, fall back to Helvetica."""
    if not REPORTLAB_OK:
        return "Helvetica"
    candidates = [
        ("Inter", "Inter-Regular.ttf", "Inter-Bold.ttf"),
        ("Inter", "fonts/Inter-Regular.ttf", "fonts/Inter-Bold.ttf"),
    ]
    for name, regular, bold in candidates:
        try:
            if Path(regular).exists() and Path(bold).exists():
                pdfmetrics.registerFont(TTFont(name, regular))
                pdfmetrics.registerFont(TTFont(f"{name}-Bold", bold))
                return name
        except Exception:
            continue
    return "Helvetica"


# ═════════════════════════════════════════════════════════════════
# PAGE TEMPLATES
# ═════════════════════════════════════════════════════════════════
class _TenderDocTemplate(BaseDocTemplate):
    def __init__(self, filename_or_buf, project_name: str, **kw):
        super().__init__(filename_or_buf, pagesize=A4, **kw)
        self.project_name = project_name
        self.allowSplitting = 1

        frame_body = Frame(
            2.2 * cm, 2.0 * cm,
            A4[0] - 4.4 * cm, A4[1] - 4.5 * cm,
            id="body", showBoundary=0,
            topPadding=0, bottomPadding=0,
        )
        frame_cover = Frame(
            2.2 * cm, 2.0 * cm,
            A4[0] - 4.4 * cm, A4[1] - 4.0 * cm,
            id="cover", showBoundary=0,
        )
        self.addPageTemplates([
            PageTemplate(id="cover", frames=[frame_cover], onPage=self._cover_frame),
            PageTemplate(id="body", frames=[frame_body], onPage=self._body_frame),
        ])

    def _cover_frame(self, canvas, doc):
        # Full-bleed header band
        canvas.saveState()
        canvas.setFillColor(BRAND_DARK)
        canvas.rect(0, A4[1] - 4.2 * cm, A4[0], 4.2 * cm, fill=1, stroke=0)
        canvas.setFillColor(BRAND_ACCENT)
        canvas.rect(0, A4[1] - 4.25 * cm, 2.8 * cm, 0.12 * cm, fill=1, stroke=0)

        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 22)
        canvas.drawString(2.2 * cm, A4[1] - 2.2 * cm, "Builtly")
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(BRAND_SOFT)
        canvas.drawString(2.2 * cm, A4[1] - 2.7 * cm, "AI-assisted engineering. Human-verified.")

        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(
            A4[0] - 2.2 * cm, A4[1] - 2.5 * cm,
            "Anbudskontroll — Tender Control"
        )
        canvas.restoreState()

    def _body_frame(self, canvas, doc):
        # Top thin line + project name
        canvas.saveState()
        canvas.setStrokeColor(BRAND_LINE)
        canvas.setLineWidth(0.3)
        canvas.line(2.2 * cm, A4[1] - 1.4 * cm, A4[0] - 2.2 * cm, A4[1] - 1.4 * cm)

        canvas.setFillColor(BRAND_MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(
            2.2 * cm, A4[1] - 1.2 * cm,
            f"Anbudskontroll — {self.project_name}"[:80]
        )
        canvas.drawRightString(
            A4[0] - 2.2 * cm, A4[1] - 1.2 * cm,
            datetime.now().strftime("%d.%m.%Y")
        )

        # Bottom page number
        canvas.setFillColor(BRAND_MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(
            A4[0] - 2.2 * cm, 1.3 * cm,
            f"Side {doc.page}"
        )
        canvas.drawString(
            2.2 * cm, 1.3 * cm,
            "Builtly — Konfidensielt utkast"
        )
        canvas.restoreState()


# ═════════════════════════════════════════════════════════════════
# STYLESHEET
# ═════════════════════════════════════════════════════════════════
def _styles(font: str) -> Dict[str, ParagraphStyle]:
    bold = f"{font}-Bold" if font != "Helvetica" else "Helvetica-Bold"
    return {
        "cover_eyebrow": ParagraphStyle(
            "cover_eyebrow", fontName=bold, fontSize=9, textColor=BRAND_ACCENT,
            leading=11, spaceAfter=6, letterSpacing=1.2,
        ),
        "cover_title": ParagraphStyle(
            "cover_title", fontName=bold, fontSize=26, textColor=BRAND_INK,
            leading=30, spaceAfter=14,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub", fontName=font, fontSize=12, textColor=BRAND_MUTED,
            leading=17, spaceAfter=18,
        ),
        "h1": ParagraphStyle(
            "h1", fontName=bold, fontSize=15, textColor=BRAND_INK,
            leading=19, spaceBefore=18, spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2", fontName=bold, fontSize=11.5, textColor=BRAND_INK,
            leading=15, spaceBefore=12, spaceAfter=6,
        ),
        "eyebrow": ParagraphStyle(
            "eyebrow", fontName=bold, fontSize=8, textColor=BRAND_ACCENT,
            leading=10, spaceAfter=3, letterSpacing=1.0,
        ),
        "body": ParagraphStyle(
            "body", fontName=font, fontSize=10, textColor=BRAND_INK,
            leading=14, spaceAfter=8, alignment=TA_JUSTIFY,
        ),
        "body_muted": ParagraphStyle(
            "body_muted", fontName=font, fontSize=9.5, textColor=BRAND_MUTED,
            leading=13, spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "bullet", fontName=font, fontSize=10, textColor=BRAND_INK,
            leading=14, leftIndent=14, bulletIndent=4, spaceAfter=3,
        ),
        "kpi_value": ParagraphStyle(
            "kpi_value", fontName=bold, fontSize=22, textColor=BRAND_ACCENT,
            leading=24, alignment=TA_CENTER,
        ),
        "kpi_label": ParagraphStyle(
            "kpi_label", fontName=bold, fontSize=8, textColor=BRAND_INK,
            leading=10, alignment=TA_CENTER, letterSpacing=0.8,
        ),
        "kpi_desc": ParagraphStyle(
            "kpi_desc", fontName=font, fontSize=8, textColor=BRAND_MUTED,
            leading=10, alignment=TA_CENTER,
        ),
        "table_cell": ParagraphStyle(
            "table_cell", fontName=font, fontSize=9, textColor=BRAND_INK,
            leading=12,
        ),
        "table_header": ParagraphStyle(
            "table_header", fontName=bold, fontSize=8.5, textColor=colors.white,
            leading=11, letterSpacing=0.6,
        ),
        "disclaimer": ParagraphStyle(
            "disclaimer", fontName=font, fontSize=8, textColor=BRAND_MUTED,
            leading=11, spaceBefore=18, alignment=TA_JUSTIFY,
        ),
    }


# ═════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════
def _esc(text: Any) -> str:
    """Safe text for ReportLab paragraphs."""
    if text is None:
        return ""
    s = str(text)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s


def _severity_badge(sev: str) -> str:
    color = {
        "HIGH": "#dc2626", "MEDIUM": "#d97706", "LOW": "#059669",
    }.get((sev or "").upper(), "#6b7a8c")
    return f'<font color="{color}"><b>{_esc(sev or "?")}</b></font>'


def _build_kpi_row(
    kpis: List[Dict[str, str]],
    styles: Dict[str, ParagraphStyle],
) -> Table:
    """Row of KPI cards."""
    cells = []
    for kpi in kpis:
        card = [
            Paragraph(_esc(kpi.get("value", "-")), styles["kpi_value"]),
            Paragraph(_esc(kpi.get("label", "")), styles["kpi_label"]),
            Paragraph(_esc(kpi.get("desc", "")), styles["kpi_desc"]),
        ]
        cells.append(card)

    width = (A4[0] - 4.4 * cm - 12) / len(kpis)
    table = Table([cells], colWidths=[width] * len(kpis))
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_BG_SOFT),
        ("BOX", (0, 0), (-1, -1), 0.4, BRAND_LINE),
        ("LINEBEFORE", (1, 0), (-1, -1), 0.4, BRAND_LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


def _data_table(
    headers: List[str],
    rows: List[List[Any]],
    styles: Dict[str, ParagraphStyle],
    col_widths: Optional[List[float]] = None,
) -> Table:
    """Styled data table."""
    header_row = [Paragraph(_esc(h), styles["table_header"]) for h in headers]
    body_rows = [
        [Paragraph(_esc(str(c)), styles["table_cell"]) for c in r]
        for r in rows
    ]
    data = [header_row] + body_rows
    total_w = A4[0] - 4.4 * cm
    if col_widths is None:
        col_widths = [total_w / len(headers)] * len(headers)

    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_INK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BRAND_BG_SOFT]),
        ("GRID", (0, 0), (-1, -1), 0.3, BRAND_LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
    ]))
    return t


# ═════════════════════════════════════════════════════════════════
# MAIN BUILDER
# ═════════════════════════════════════════════════════════════════
def build_pdf_report(
    project_info: Dict[str, Any],
    config: Dict[str, Any],
    documents: List[Dict[str, Any]],
    rule_findings: Dict[str, Any],
    ai_result: Dict[str, Any],
    readiness: Dict[str, Any],
    run_meta: Optional[Dict[str, Any]] = None,
) -> Optional[bytes]:
    """Build the full Anbudskontroll PDF."""
    if not REPORTLAB_OK:
        return None

    font = _register_fonts()
    styles = _styles(font)

    buf = io.BytesIO()
    doc = _TenderDocTemplate(buf, project_info.get("p_name", "Prosjekt"))
    story: List[Any] = []

    # ═══ COVER ═══════════════════════════════════════════════════
    story.append(Spacer(1, 6 * cm))
    story.append(Paragraph("ANBUDSKONTROLL", styles["cover_eyebrow"]))
    story.append(Paragraph(
        _esc(project_info.get("p_name", "Prosjekt")),
        styles["cover_title"],
    ))

    pass3_data = (ai_result.get("pass3") or {}).get("data") or {}
    exec_summary = pass3_data.get("executive_summary") or (
        "AI-oppsummering ikke tilgjengelig. Resultatet er basert på regelmotor "
        "og ekstrahert dokumentinnhold."
    )
    story.append(Paragraph(_esc(exec_summary), styles["cover_sub"]))

    # Cover KPIs
    band = readiness.get("band", "-")
    go = pass3_data.get("go_no_go", {}) or {}
    go_reco = go.get("recommendation", "-")
    high_risks = sum(
        1 for r in (pass3_data.get("risk_matrix") or []) + rule_findings.get("risk_items", [])
        if (r.get("severity") or "").upper() == "HIGH"
    )
    story.append(Spacer(1, 10))
    story.append(_build_kpi_row([
        {"value": f"{readiness.get('overall', 0):.0f}%", "label": "READINESS", "desc": band},
        {"value": go_reco, "label": "ANBEFALING", "desc": go.get("confidence", "-")},
        {"value": str(len(documents)), "label": "DOKUMENTER", "desc": "Analysert med AI"},
        {"value": str(high_risks), "label": "HØY RISIKO", "desc": "Bør lukkes/RFI"},
    ], styles))

    # Cover meta line
    story.append(Spacer(1, 20))
    meta_table = Table([[
        Paragraph(
            f"<b>Anskaffelsesform:</b> {_esc(config.get('procurement_mode', '-'))}<br/>"
            f"<b>Pakker:</b> {_esc(', '.join(config.get('packages', []) or ['-']))}<br/>"
            f"<b>Estimert verdi:</b> {config.get('bid_value_mnok', 0):.0f} MNOK<br/>"
            f"<b>Kontrolldybde:</b> {_esc(config.get('qa_level', '-'))}",
            styles["body_muted"],
        ),
        Paragraph(
            f"<b>Prosjekt:</b> {_esc(project_info.get('p_name', '-'))}<br/>"
            f"<b>Adresse:</b> {_esc(project_info.get('adresse', '-'))}<br/>"
            f"<b>Oppdragsgiver:</b> {_esc(project_info.get('c_name', '-'))}<br/>"
            f"<b>Generert:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            styles["body_muted"],
        ),
    ]], colWidths=[(A4[0] - 4.4 * cm) / 2] * 2)
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_BG_SOFT),
        ("BOX", (0, 0), (-1, -1), 0.4, BRAND_LINE),
        ("LINEBETWEEN", (0, 0), (0, -1), 0.4, BRAND_LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(meta_table)

    # Disclaimer on cover
    story.append(Paragraph(
        "Dette er et AI-assistert utkast for fagperson-gjennomgang. "
        "Rapporten er ikke signert med ansvarsrett og er ikke juridisk bindende. "
        "Menneskelig kontroll kreves før tilbud sendes inn.",
        styles["disclaimer"],
    ))

    # ═══ EXECUTIVE SUMMARY ═══════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("SAMMENDRAG", styles["eyebrow"]))
    story.append(Paragraph("Go / no-go og strategisk vurdering", styles["h1"]))

    if go:
        story.append(Paragraph(f"<b>Anbefaling:</b> {_esc(go.get('recommendation', '-'))}",
                               styles["body"]))
        story.append(Paragraph(f"<b>Konfidens:</b> {_esc(go.get('confidence', '-'))}",
                               styles["body"]))
        if go.get("rationale"):
            story.append(Paragraph(
                f"<b>Begrunnelse:</b> {_esc(go.get('rationale'))}",
                styles["body"],
            ))
        conds = go.get("conditions", []) or []
        if conds:
            story.append(Paragraph("<b>Betingelser for GO:</b>", styles["body"]))
            for c in conds:
                story.append(Paragraph(f"• {_esc(c)}", styles["bullet"]))

    # ═══ READINESS BREAKDOWN ═════════════════════════════════════
    story.append(Spacer(1, 14))
    story.append(Paragraph("Readiness-sammensetning", styles["h2"]))
    comp = readiness.get("components", {}) or {}
    labels = {
        "document_completeness": "Dokumentkomplettet",
        "scope_clarity": "Klart scope",
        "contract_risk": "Kontraktsrisiko",
        "pricing_readiness": "Prisingsklart",
        "qualification_fit": "Kvalifikasjoner",
    }
    weights = readiness.get("weights", {}) or {}
    readiness_rows = [
        [
            labels.get(k, k),
            f"{comp.get(k, 0):.0f}%",
            f"{weights.get(k, 0) * 100:.0f}%",
            f"{comp.get(k, 0) * weights.get(k, 0):.1f}",
        ]
        for k in labels
    ]
    readiness_rows.append([
        "Vektet total",
        f"{readiness.get('overall', 0):.1f}%",
        "100%",
        f"{readiness.get('overall', 0):.1f}",
    ])
    story.append(_data_table(
        ["Komponent", "Score", "Vekt", "Bidrag"],
        readiness_rows,
        styles,
        col_widths=[7 * cm, 2.8 * cm, 2.8 * cm, 3 * cm],
    ))

    # ═══ RISK MATRIX ═════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("RISIKO", styles["eyebrow"]))
    story.append(Paragraph("Risikomatrise og gap", styles["h1"]))

    all_risks = list(pass3_data.get("risk_matrix") or [])
    all_risks.extend(rule_findings.get("risk_items", []) or [])
    pass2_data = (ai_result.get("pass2") or {}).get("data") or {}
    for conflict in (pass2_data.get("cross_document_conflicts") or []):
        all_risks.append({
            "title": f"Motstrid: {conflict.get('title', '')}",
            "severity": conflict.get("severity", "MEDIUM"),
            "category": "grensesnitt",
            "impact": conflict.get("description", "") + " " + (conflict.get("economic_impact") or ""),
            "mitigation": conflict.get("recommended_rfi") or "Krever avklaring",
        })

    if all_risks:
        risk_rows = [
            [
                r.get("title", ""),
                r.get("severity", ""),
                r.get("category", ""),
                r.get("impact", ""),
                r.get("mitigation", ""),
            ]
            for r in all_risks[:40]
        ]
        story.append(_data_table(
            ["Tittel", "Alvorlighet", "Kategori", "Impact", "Tiltak"],
            risk_rows,
            styles,
            col_widths=[3.6 * cm, 1.8 * cm, 2.0 * cm, 4.5 * cm, 3.8 * cm],
        ))
    else:
        story.append(Paragraph("Ingen vesentlige risikoer identifisert.", styles["body"]))

    # ═══ RFI QUEUE ═══════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("AVKLARINGER", styles["eyebrow"]))
    story.append(Paragraph("RFI-kø — spørsmål til byggherre", styles["h1"]))

    rfis = list(pass3_data.get("rfi_queue") or [])
    if rfis:
        rfi_rows = [
            [
                r.get("priority", ""),
                r.get("question", ""),
                r.get("why_it_matters", ""),
                r.get("owner", ""),
            ]
            for r in rfis[:40]
        ]
        story.append(_data_table(
            ["Prio", "Spørsmål", "Hvorfor", "Eier"],
            rfi_rows,
            styles,
            col_widths=[1.5 * cm, 6.5 * cm, 5.5 * cm, 2.2 * cm],
        ))
    else:
        story.append(Paragraph(
            "Ingen RFI-forslag generert. Dette kan skyldes at dokumentgrunnlaget var "
            "for begrenset til at AI kunne identifisere uklarheter.",
            styles["body"],
        ))

    # ═══ PRICING PACKAGES ════════════════════════════════════════
    packages = pass3_data.get("pricing_packages") or []
    if packages:
        story.append(PageBreak())
        story.append(Paragraph("PRISING", styles["eyebrow"]))
        story.append(Paragraph("Pakker for ekstern prising", styles["h1"]))
        story.append(Paragraph(
            "Nedenfor er pakkene vurdert for utsendelse til underentreprenører. "
            "Full forespørselspakke per fag genereres som egen DOCX.",
            styles["body_muted"],
        ))

        pkg_rows = [
            [
                p.get("package", ""),
                "Ja" if p.get("send_to_external") else "Nei",
                f"{p.get('estimated_value_mnok') or '-'} MNOK" if p.get("estimated_value_mnok") else "-",
                p.get("rationale", "")[:200],
            ]
            for p in packages
        ]
        story.append(_data_table(
            ["Pakke", "Ekstern?", "Anslått verdi", "Begrunnelse"],
            pkg_rows,
            styles,
            col_widths=[3.0 * cm, 1.8 * cm, 2.8 * cm, 7.9 * cm],
        ))

    # ═══ DOCUMENT MANIFEST ═══════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("GRUNNLAG", styles["eyebrow"]))
    story.append(Paragraph("Dokumentmanifest", styles["h1"]))

    if documents:
        manifest_rows = [
            [
                d.get("filename", "")[:60],
                d.get("category", ""),
                f"{d.get('size_kb', 0):.0f} KB",
                str(d.get("page_count", "-")) if d.get("page_count") else "-",
                "Feil" if d.get("error") else "OK",
            ]
            for d in documents
        ]
        story.append(_data_table(
            ["Filnavn", "Kategori", "Størrelse", "Sider", "Status"],
            manifest_rows,
            styles,
            col_widths=[7.0 * cm, 3.0 * cm, 2.0 * cm, 1.6 * cm, 1.9 * cm],
        ))
    else:
        story.append(Paragraph("Ingen dokumenter analysert.", styles["body"]))

    # ═══ AUDIT FOOTER ════════════════════════════════════════════
    story.append(Spacer(1, 20))
    story.append(Paragraph("REVISJONSSPOR", styles["eyebrow"]))
    rm = run_meta or {}
    audit_para = (
        f"<b>Kjøre-ID:</b> {_esc(rm.get('run_id', '-'))}<br/>"
        f"<b>Tidsstempel:</b> {_esc(rm.get('timestamp', '-'))}<br/>"
        f"<b>Dokument-hash:</b> {_esc(rm.get('document_hash', '-'))}<br/>"
        f"<b>Lagring:</b> {_esc(rm.get('stored_in', 'ikke lagret'))}<br/>"
        f"<b>AI-backend:</b> {_esc((ai_result.get('backend_summary') or {}).get('primary', '-'))}"
    )
    story.append(Paragraph(audit_para, styles["body_muted"]))

    # Build — first page uses 'cover', rest 'body'
    def _on_first_page(canvas, doc_):
        doc_._nextPageTemplateCycle = None
    # Trigger body template after cover
    story_with_template = [*story]

    doc.build(story_with_template)
    return buf.getvalue()


# ═════════════════════════════════════════════════════════════════
# MARKDOWN REPORT (quick-share)
# ═════════════════════════════════════════════════════════════════
def build_markdown_report(
    project_info: Dict[str, Any],
    config: Dict[str, Any],
    documents: List[Dict[str, Any]],
    rule_findings: Dict[str, Any],
    ai_result: Dict[str, Any],
    readiness: Dict[str, Any],
    run_meta: Optional[Dict[str, Any]] = None,
) -> str:
    parts: List[str] = []
    pass3 = (ai_result.get("pass3") or {}).get("data") or {}
    pass2 = (ai_result.get("pass2") or {}).get("data") or {}

    parts.append(f"# Anbudskontroll — {project_info.get('p_name', 'Prosjekt')}")
    parts.append(f"*Generert {datetime.now().strftime('%d.%m.%Y %H:%M')}*\n")

    parts.append("## Sammendrag")
    parts.append(pass3.get("executive_summary") or "Ingen AI-oppsummering tilgjengelig.\n")

    go = pass3.get("go_no_go", {}) or {}
    if go:
        parts.append(f"\n**Anbefaling:** {go.get('recommendation', '-')} "
                     f"({go.get('confidence', '-')})")
        if go.get("rationale"):
            parts.append(f"\n{go['rationale']}")
        if go.get("conditions"):
            parts.append("\n**Betingelser:**")
            for c in go["conditions"]:
                parts.append(f"- {c}")

    parts.append("\n## Readiness")
    parts.append(f"**Total:** {readiness.get('overall', 0):.0f}% — {readiness.get('band', '-')}")
    for k, v in (readiness.get("components") or {}).items():
        parts.append(f"- {k}: {v:.0f}%")

    parts.append("\n## Risikomatrise")
    risks = list(pass3.get("risk_matrix") or [])
    risks.extend(rule_findings.get("risk_items", []) or [])
    for r in risks:
        parts.append(
            f"- **[{r.get('severity', '?')}] {r.get('title', '')}** — "
            f"{r.get('impact', '')} *Tiltak: {r.get('mitigation', '')}*"
        )

    parts.append("\n## RFI-kø")
    for rfi in pass3.get("rfi_queue") or []:
        parts.append(
            f"- **[{rfi.get('priority', '?')}]** {rfi.get('question', '')} "
            f"*(Hvorfor: {rfi.get('why_it_matters', '')})*"
        )

    parts.append("\n## Prisingspakker")
    for p in pass3.get("pricing_packages") or []:
        parts.append(
            f"- **{p.get('package', '').title()}** — "
            f"{'ekstern' if p.get('send_to_external') else 'internt'} — "
            f"{p.get('rationale', '')}"
        )

    parts.append("\n## Krysskontroll-funn")
    for c in pass2.get("cross_document_conflicts") or []:
        parts.append(
            f"- **[{c.get('severity', '?')}] {c.get('title', '')}** — "
            f"{c.get('description', '')}"
        )

    parts.append("\n## Dokumentmanifest")
    for d in documents:
        parts.append(
            f"- `{d.get('filename')}` — {d.get('category')} — "
            f"{d.get('size_kb', 0):.0f} KB — "
            f"{d.get('page_count', '-')} sider"
        )

    if run_meta:
        parts.append("\n## Revisjonsspor")
        parts.append(f"- Run ID: `{run_meta.get('run_id')}`")
        parts.append(f"- Document hash: `{run_meta.get('document_hash')}`")
        parts.append(f"- Lagring: {run_meta.get('stored_in')}")

    parts.append("\n---\n*Builtly Anbudskontroll. Utkast — krever menneskelig kontroll.*")
    return "\n".join(parts)
