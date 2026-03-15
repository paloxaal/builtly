from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from builtly_document_engine import (
    audit_log_dataframe,
    build_markdown_report,
    default_delivery_level,
    disclaimer_for_level,
    manifest_dataframe,
    normalize_uploaded_files,
    revision_dataframe,
    run_module_analysis,
    tdd_rules_payload,
)
from builtly_module_kit import (
    configure_page,
    dataframe_download,
    json_download,
    list_to_dataframe,
    markdown_download,
    render_attempt_log,
    render_disclaimer_banner,
    render_hero,
    render_json_preview,
    render_metric_cards,
    render_project_snapshot,
    render_section,
)
from builtly_pdf_engine import build_tdd_pdf, sanitize_filename
from builtly_public_data import adapter_status

# ─── Filnavnsaniterer – fix for AxiosError 400 på norske tegn ────────────────
# Filnavn med æ/ø/å og andre non-ASCII-tegn feiler i HTTP Content-Disposition
# header. sanitize_filename() konverterer til ASCII-safe navn for API-kall
# mens original_name bevares for visning i UI-et.

def _safe_records(uploads) -> list[dict]:
    """
    Wrapper rundt normalize_uploaded_files() som sikrer at hvert
    filnavn er ASCII-safe for API-kall, men bevarer originalt navn for visning.
    """
    records = normalize_uploaded_files(uploads)
    for r in records:
        if "original_name" not in r:
            r["original_name"] = r.get("name", "")
        r["name"] = sanitize_filename(r.get("name", ""))
    return records


# ─── Sideconfigurasjon ────────────────────────────────────────────────────────
project = configure_page("Builtly | Teknisk Due Diligence", "B")
module_key = "tdd"
levels = ["auto", "reviewed", "attested"]
default_level = default_delivery_level(module_key)

render_hero(
    eyebrow="Teknisk Due Diligence",
    title="Få oversikt over teknisk tilstand, risiko og vedlikeholdsbehov før du kjøper.",
    subtitle=(
        "Last opp tegninger, ferdigattest, tilstandsrapport, energimerke og FDV. "
        "Du får en strukturert gjennomgang med tilstandsgrader, TEK17-avvik, estimert utbedringskost og komplett dokumentoversikt."
    ),
    pills=["NS 3600/3424", "TEK17", "TG0-3", "Portefølje", "Sporbarhet"],
    badge="Teknisk Due Diligence",
)

left, right = st.columns([1.35, 0.65], gap="large")

# ─── Venstre kolonne: input ───────────────────────────────────────────────────
with left:
    render_section(
        "1. Last opp dokumentasjon",
        "Last opp alt tilgjengelig underlag for eiendommen. Jo mer dokumentasjon, desto bedre analyse.",
        "Input",
    )
    c1, c2 = st.columns(2)
    with c1:
        delivery_level = st.selectbox("Leveransenivå", levels, index=levels.index(default_level))
        transaction_stage = st.selectbox(
            "Brukssituasjon",
            ["Screening", "Transaksjon", "Bank / kreditt", "Portefølje"],
            index=1,
        )
        property_type = st.selectbox(
            "Eiendomstype",
            ["Bolig", "Kontor", "Retail", "Logistikk", "Mixed-use"],
            index=1,
        )
    with c2:
        build_year = st.number_input("Byggeår", min_value=1850, max_value=2100, value=2008, step=1)
        market_value_mnok = st.number_input("Estimert markedsverdi (MNOK)", min_value=1.0, value=145.0, step=1.0)
        include_portfolio = st.toggle(
            "Inkluder i porteføljeanalyse",
            value=transaction_stage in {"Bank / kreditt", "Portefølje"},
        )

    notes = st.text_area(
        "Er det noe spesielt du ønsker at analysen skal fokusere på?",
        value=(
            "Jeg ønsker vurdering av tilstandsgrad per bygningsdel, eventuelle TEK17-avvik, "
            "estimert utbedringskostnad og en oversikt over hva som mangler av dokumentasjon."
        ),
        height=120,
    )

    uploads = st.file_uploader(
        "Last opp tegninger, ferdigattest, tilstandsrapport, energimerke, FDV og tidligere rapporter",
        type=["pdf", "docx", "xlsx", "xls", "csv", "ifc", "dwg", "dxf", "zip"],
        accept_multiple_files=True,
        key="tdd_uploads_real",
        help=(
            "Filer med norske tegn (æ, ø, å) i filnavnet håndteres automatisk. "
            "Originalt filnavn vises i UI-et og dokumentoversikten."
        ),
    )

    # ── Filnavn-feil-visning ──────────────────────────────────────────────────
    # Vis bruker hvilke filnavn som er sanitert, slik at det er transparent.
    if uploads:
        sanitised_any = False
        for f in uploads:
            safe = sanitize_filename(f.name)
            if safe != f.name:
                sanitised_any = True
                break
        if sanitised_any:
            with st.expander("ℹ️ Filnavn tilpasset for opplasting", expanded=False):
                st.caption(
                    "Norske tegn (æ, ø, å) i filnavn er konvertert for sikker opplasting. "
                    "Originalt filnavn bevares i rapporten."
                )
                name_data = [
                    {"Originalt filnavn": f.name, "Sendt til API som": sanitize_filename(f.name)}
                    for f in uploads
                    if sanitize_filename(f.name) != f.name
                ]
                if name_data:
                    st.dataframe(name_data, use_container_width=True, hide_index=True)


# ─── Kjerneworkflow ───────────────────────────────────────────────────────────
records = _safe_records(uploads)

rules = tdd_rules_payload(
    project,
    records,
    {
        "transaction_stage":  transaction_stage,
        "property_type":      property_type,
        "build_year":         build_year,
        "market_value_mnok":  market_value_mnok,
        "include_portfolio":  include_portfolio,
        "notes":              notes,
        "matrikkel_id":       f"{project.get('gnr','')}/{project.get('bnr','')}",
    },
)

ai_result = run_module_analysis(module_key, project, records, rules, delivery_level)

report_markdown = build_markdown_report(
    module_title="Teknisk Due Diligence",
    project=project,
    manifest_records=records,
    revision_records=revision_dataframe(records).to_dict(orient="records"),
    ai_payload=ai_result,
)

render_disclaimer_banner(delivery_level, disclaimer_for_level(delivery_level))

# ─── Høyre kolonne: kontekst og metrics ───────────────────────────────────────
with right:
    render_project_snapshot(project, badge="TDD context")
    risk_matrix = (ai_result.get("data") or {}).get("risk_matrix") or rules.get("risk_matrix", {})
    render_metric_cards([
        {
            "label": "Datakompletthet",
            "value": f"{int(rules.get('data_completeness_score', 0) * 100)}%",
            "desc":  "Andel av nødvendig TDD-underlag som er identifisert.",
        },
        {
            "label": "Samlet klasse",
            "value": risk_matrix.get("overall_class", "-"),
            "desc":  "Foreløpig risikoklassifisering basert på tilgjengelig underlag.",
        },
        {
            "label": "Utbedringskost",
            "value": f"{int(risk_matrix.get('remediation_cost_total_nok', 0)):,.0f} NOK".replace(",", " "),
            "desc":  "Estimert samlet kostnad for utbedring av identifiserte forhold.",
        },
        {
            "label": "Dokumenter",
            "value": str(len(records)),
            "desc":  "Antall opplastede filer i analysen.",
        },
    ])
    render_json_preview(
        {
            "delivery_level": delivery_level,
            "risk_matrix":    risk_matrix,
            "completeness":   rules.get("data_completeness_score", 0),
        },
        "Analysegrunnlag",
    )


# ─── Analyse-seksjoner (tabs) ─────────────────────────────────────────────────
render_section(
    "2. Analyse og resultater",
    "Gjennomgang av tilstand, risiko, dokumentasjon og offentlig data for eiendommen.",
    "Review",
)

tabs = st.tabs([
    "Sammendrag",
    "Dokumentoversikt",
    "Offentlige data",
    "Bygningsdeler",
    "Risikomatrise",
    "Portefølje",
    "Endringslogg",
])

# ── Tab 0: Sammendrag ─────────────────────────────────────────────────────────
with tabs[0]:
    st.markdown("### Sammendrag")
    st.write(
        (ai_result.get("data") or {}).get("executive_summary")
        or "Sammendrag genereres når du laster opp dokumentasjon."
    )

    st.markdown("### Anbefalte neste steg")
    st.dataframe(
        list_to_dataframe(
            (ai_result.get("data") or {}).get("next_actions", []),
            ["action", "owner", "priority", "why"],
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("### Identifiserte mangler i underlaget")
    st.dataframe(
        list_to_dataframe(
            (ai_result.get("data") or {}).get("gaps", []),
            ["value"],
        ),
        use_container_width=True,
        hide_index=True,
    )

    # ── Nedlastingsknapper ────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("**Last ned rapport**")

    dl_col1, dl_col2, dl_col3 = st.columns(3)

    # Markdown
    with dl_col1:
        markdown_download(report_markdown, "Markdown (.md)", "tdd_report.md")

    # JSON
    with dl_col2:
        json_download(ai_result.get("data") or {}, "Strukturert JSON (.json)", "tdd_result.json")

    # PDF
    with dl_col3:
        if st.button("📄 Generer PDF-rapport", key="generate_pdf_btn", use_container_width=True):
            with st.spinner("Bygger PDF-rapport..."):
                try:
                    pdf_bytes = build_tdd_pdf(
                        project=project,
                        ai_result=ai_result,
                        rules=rules,
                        delivery_level=delivery_level,
                        disclaimer_text=disclaimer_for_level(delivery_level),
                        records=records,
                    )
                    st.session_state["tdd_pdf_bytes"] = pdf_bytes
                    st.success(f"PDF klar – {len(pdf_bytes) // 1024} KB")
                except Exception as e:
                    st.error(f"PDF-generering feilet: {e}")

        # Vis nedlastingsknapp når PDF er generert
        if "tdd_pdf_bytes" in st.session_state and st.session_state["tdd_pdf_bytes"]:
            proj_slug = (
                (project.get("name") or "tdd")
                .lower()
                .replace(" ", "_")
                .replace("/", "-")
            )
            filename = f"builtly_tdd_{proj_slug}_{delivery_level}.pdf"
            st.download_button(
                label="⬇️ Last ned PDF",
                data=st.session_state["tdd_pdf_bytes"],
                file_name=filename,
                mime="application/pdf",
                use_container_width=True,
                key="download_pdf_btn",
            )

    render_attempt_log(ai_result.get("attempt_log", []))


# ── Tab 1: Dokumentoversikt ───────────────────────────────────────────────────
with tabs[1]:
    # Vis originale filnavn i UI – ikke de saniterte
    display_records = [
        {**r, "name": r.get("original_name") or r.get("name")}
        for r in records
    ]
    st.dataframe(manifest_dataframe(display_records), use_container_width=True, hide_index=True)
    st.dataframe(revision_dataframe(display_records), use_container_width=True, hide_index=True)
    dataframe_download(
        manifest_dataframe(display_records),
        "Last ned dokumentoversikt (.csv)",
        "tdd_manifest.csv",
    )

# ── Tab 2: Offentlige data ────────────────────────────────────────────────────
with tabs[2]:
    st.dataframe(
        list_to_dataframe(
            rules.get("public_data_snapshot", []),
            ["source", "status", "note", "version"],
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.markdown("### Adapterstatus")
    st.dataframe(pd.DataFrame(adapter_status()), use_container_width=True, hide_index=True)
    resolved = rules.get("public_snapshot_resolved", {})
    if resolved.get("map_url"):
        st.markdown(f"Kartlenke: {resolved['map_url']}")

# ── Tab 3: Bygningsdeler ──────────────────────────────────────────────────────
with tabs[3]:
    bp_data = (ai_result.get("data") or {}).get("building_parts") or rules.get("building_parts", [])
    bp_df = list_to_dataframe(
        bp_data,
        ["part", "tg", "remaining_life_years", "remediation_cost_range_nok", "reason", "source"],
    )

    # Fargemarkér TG-kolonnen
    def _tg_colour(val: str) -> str:
        colours = {"TG0": "background-color: #EAF3DE", "TG1": "background-color: #E1F5EE",
                   "TG2": "background-color: #FAEEDA", "TG3": "background-color: #FCEBEB"}
        return colours.get(str(val).upper(), "")

    if not bp_df.empty and "tg" in bp_df.columns:
        st.dataframe(
            bp_df.style.applymap(_tg_colour, subset=["tg"]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.dataframe(bp_df, use_container_width=True, hide_index=True)

    dataframe_download(bp_df, "Last ned bygningsdeler (.csv)", "tdd_building_parts.csv")

# ── Tab 4: Risikomatrise ──────────────────────────────────────────────────────
with tabs[4]:
    st.dataframe(
        list_to_dataframe(
            [risk_matrix],
            ["technical_risk", "financial_risk", "regulatory_risk", "overall_class", "remediation_cost_total_nok"],
        ),
        use_container_width=True,
        hide_index=True,
    )
    tek_data = (ai_result.get("data") or {}).get("tek17_deviations") or rules.get("tek17_deviations", [])
    tek_df = list_to_dataframe(tek_data, ["title", "category", "recommendation", "source"])

    # Fargemarkér kategori
    def _cat_colour(val: str) -> str:
        v = str(val).upper()
        if "KRITISK" in v or "CRITICAL" in v:
            return "background-color: #FCEBEB; color: #A32D2D; font-weight: 500"
        if "VESENTLIG" in v or "SIGNIFICANT" in v:
            return "background-color: #FAEEDA; color: #BA7517"
        return ""

    if not tek_df.empty and "category" in tek_df.columns:
        st.dataframe(
            tek_df.style.applymap(_cat_colour, subset=["category"]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.dataframe(tek_df, use_container_width=True, hide_index=True)

# ── Tab 5: Portefølje ─────────────────────────────────────────────────────────
with tabs[5]:
    portfolio_preview = rules.get("portfolio_preview") or {}
    if portfolio_preview:
        st.metric("Referanse-ID", portfolio_preview.get("batch_id", "-"))
        st.metric(
            "Estimert behandlingstid",
            f"{portfolio_preview.get('estimated_completion_hours', 0)} timer",
        )
        st.dataframe(
            pd.DataFrame(portfolio_preview.get("properties", [])),
            use_container_width=True,
            hide_index=True,
        )
        json_download(
            portfolio_preview,
            "Last ned porteføljeoversikt (.json)",
            "tdd_portfolio_batch.json",
        )
    else:
        st.info("Aktivér porteføljeflyt ovenfor for å se samlet oversikt over flere eiendommer.")

# ── Tab 6: Endringslogg ───────────────────────────────────────────────────────
with tabs[6]:
    st.dataframe(
        audit_log_dataframe(module_key, delivery_level, records, ai_result),
        use_container_width=True,
        hide_index=True,
    )
