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
    disclaimer_for_level,
    manifest_dataframe,
    normalize_uploaded_files,
    revision_dataframe,
    run_module_analysis,
    tender_rules_payload,
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
    render_panel,
    render_project_snapshot,
    render_section,
)
from builtly_rulepacks import load_rulepack_from_bytes, normalise_rulepack

project = configure_page("Builtly | Anbudskontroll", "B")

render_hero(
    eyebrow="Tender Control",
    title="Anbudskontroll som finner hull for markedet gjor det.",
    subtitle=(
        "Sammenstill konkurransegrunnlag, beskrivelser, tegninger, IFC/PDF og tilbudsdokumenter i en arbeidsflate. "
        "Modulen lager avviksmatrise, mangelliste, uklarhetslogg, scopesammenstilling og forslag til sporsmal for innlevering."
    ),
    pills=["Entreprenor", "Radgiver", "Utbygger", "Audit trail", "RFI-generator"],
    badge="Horizontal engine",
)

left, right = st.columns([1.25, 0.75], gap="large")
with left:
    render_section("Inntak og kontrollparametere", "Definer hva som inngar i tilbudspakken og hvilke kontroller Builtly skal prioritere.", "Tender intake")
    with st.form("tender_control_form"):
        c1, c2 = st.columns(2)
        with c1:
            procurement_mode = st.selectbox("Anskaffelsesform", ["Totalentreprise", "Utforelsesentreprise", "Samspillsentreprise", "Design & Build"], index=0)
            discipline_focus = st.multiselect("Fag / scope som skal kvalitetssikres forst", ["ARK", "RIB", "RIV", "RIE", "Brann", "Akustikk", "Geo", "Trafikk", "SHA", "MOP", "BREEAM"], default=["ARK", "RIB", "Geo", "Brann"])
            delivery_level = st.selectbox("Leveranseniva", ["auto", "reviewed", "attested"], index=1)
            include_bid_documents = st.toggle("Tilbudsdokumenter er lastet opp", value=True)
        with c2:
            packages = st.multiselect("Pakker / delentrepriser", ["Grunnarbeid", "Betong", "Stal", "Fasade", "Tomrer", "Tak", "VVS", "Elektro", "Utomhus"], default=["Grunnarbeid", "Betong", "Fasade", "VVS", "Elektro"])
            qa_level = st.select_slider("Kontrolldybde", options=["Lett", "Standard", "Dyp", "Pre-bid review"], value="Dyp")
            required_outputs = st.multiselect(
                "Onskede leveranser",
                ["Avviksmatrise", "Mangelliste", "Uklarhetslogg", "Scopesammenstilling", "Forslag til sporsmal/RFI", "Submission readiness"],
                default=["Avviksmatrise", "Mangelliste", "Uklarhetslogg", "Scopesammenstilling", "Forslag til sporsmal/RFI"],
            )
            bid_value_mnok = st.number_input("Estimert tilbudsverdi (MNOK)", min_value=1.0, value=120.0, step=1.0)
        files = st.file_uploader(
            "Last opp konkurransegrunnlag, tegninger, IFC/PDF og tilbudsdokumenter",
            type=["pdf", "ifc", "xlsx", "xls", "docx", "csv", "zip", "dwg", "dxf"],
            accept_multiple_files=True,
            key="tender_files_v5",
        )
        rulepack_upload = st.file_uploader("Valgfritt: last opp eget regelbibliotek (JSON/CSV/XLSX)", type=["json", "csv", "xlsx", "xls"], key="tender_rulepack")
        notes = st.text_area(
            "Prosjektspesifikke forhold som bor vektes hoyt",
            value="Saerskilt fokus pa grensesnitt mellom grunnarbeid, betong og fasade. Kontroller at rigg/logistikk, SHA og ytre miljo er konsistente i alle dokumenter.",
            height=110,
        )
        submitted = st.form_submit_button("Kjor anbudskontroll")

render_disclaimer_banner(delivery_level, disclaimer_for_level(delivery_level))
records = normalize_uploaded_files(files if submitted else files or [])
custom_rules = []
if rulepack_upload is not None:
    custom_rules = normalise_rulepack(load_rulepack_from_bytes(rulepack_upload.name, rulepack_upload.getvalue()))
rules = tender_rules_payload(
    project,
    records,
    {
        "procurement_mode": procurement_mode,
        "discipline_focus": discipline_focus,
        "packages": packages,
        "qa_level": qa_level,
        "required_outputs": required_outputs,
        "bid_value_mnok": bid_value_mnok,
        "notes": notes,
        "include_bid_documents": include_bid_documents,
    },
    custom_rules=custom_rules,
)
ai_result = run_module_analysis("tender", project, records, rules, delivery_level)
report_markdown = build_markdown_report(
    module_title="Tender Control",
    project=project,
    manifest_records=records,
    revision_records=revision_dataframe(records).to_dict(orient="records"),
    ai_payload=ai_result,
)

with right:
    render_project_snapshot(project, badge="Tender context")
    risk_items = (ai_result.get("data") or {}).get("risk_items") or rules.get("risk_items", [])
    readiness = max(35, int(rules.get("data_completeness_score", 0.0) * 100) - 4 * len(rules.get("missing_categories", [])) - 3 * len([r for r in risk_items if r.get("severity") == "HIGH"]))
    render_metric_cards([
        {"label": "Dokumenter", "value": str(len(records)), "desc": "Konkurransegrunnlag, tegninger og tilbudsdokumenter i samme kontrollslayfe."},
        {"label": "Manglende kategorier", "value": str(len(rules.get("missing_categories", []))), "desc": "Obligatoriske eller forventede dokumenttyper som ikke er funnet."},
        {"label": "Hoy risiko", "value": str(len([r for r in risk_items if r.get('severity') == 'HIGH'])), "desc": "Risikoer som bor lukkes eller adresseres i forbehold/RFI."},
        {"label": "Submission readiness", "value": f"{readiness}%", "desc": "Forelopig modenhet basert pa kompletthet, avvik og dokumentkonsistens."},
    ])
    render_json_preview({"delivery_level": delivery_level, "procurement_mode": procurement_mode, "packages": packages, "required_outputs": required_outputs}, "Bestillingspayload")

render_section("Analyse og eksport", "Rules-first sjekkliste, AI-oppsummering og revisjonsspor i samme arbeidsflate.", "Review")
tabs = st.tabs(["AI-utkast", "Dokumentmanifest", "Sjekkliste", "Risiko og RFI", "Scope / pakker", "Audit trail"])

with tabs[0]:
    st.markdown("### Sammendrag")
    st.write((ai_result.get("data") or {}).get("executive_summary") or "Ingen AI-oppsummering tilgjengelig.")
    st.dataframe(list_to_dataframe((ai_result.get("data") or {}).get("contract_fields") or rules.get("contract_fields", []), ["field", "value", "source"]), use_container_width=True, hide_index=True)
    markdown_download(report_markdown, "Last ned anbudsrapport (.md)", "tender_report.md")
    json_download(ai_result.get("data") or {}, "Last ned AI-resultat (.json)", "tender_result.json")
    render_attempt_log(ai_result.get("attempt_log", []))

with tabs[1]:
    st.dataframe(manifest_dataframe(records), use_container_width=True, hide_index=True)
    st.dataframe(revision_dataframe(records), use_container_width=True, hide_index=True)
    dataframe_download(manifest_dataframe(records), "Last ned dokumentmanifest (.csv)", "tender_manifest.csv")

with tabs[2]:
    checklist_df = list_to_dataframe((ai_result.get("data") or {}).get("checklist_items") or rules.get("checklist_items", []), ["topic", "status", "severity", "paragraph_ref", "reason", "source"])
    st.dataframe(checklist_df, use_container_width=True, hide_index=True)
    dataframe_download(checklist_df, "Last ned sjekkliste (.csv)", "tender_checklist.csv")
    if rules.get("missing_categories"):
        st.warning("Manglende kategorier: " + ", ".join(rules["missing_categories"]))

with tabs[3]:
    risk_df = list_to_dataframe((ai_result.get("data") or {}).get("risk_items") or rules.get("risk_items", []), ["title", "severity", "impact", "recommendation", "source", "paragraph_ref"])
    rfi_df = list_to_dataframe((ai_result.get("data") or {}).get("rfi_suggestions") or rules.get("rfi_suggestions", []), ["priority", "question", "why", "owner"])
    st.dataframe(risk_df, use_container_width=True, hide_index=True)
    st.dataframe(rfi_df, use_container_width=True, hide_index=True)
    dataframe_download(risk_df, "Last ned risikologg (.csv)", "tender_risk.csv")
    dataframe_download(rfi_df, "Last ned RFI-utkast (.csv)", "tender_rfi.csv")

with tabs[4]:
    scope_df = pd.DataFrame([
        {"pakke": pkg, "status": "Dekket" if idx < max(1, len(packages) - 1) else "Krevende grensesnitt", "kommentar": "Kontroller mengder, delingslinjer og ansvar mot dokumentgrunnlaget."}
        for idx, pkg in enumerate(packages or ["Grunnarbeid", "Betong", "Fasade"])
    ])
    st.dataframe(scope_df, use_container_width=True, hide_index=True)
    dataframe_download(scope_df, "Last ned scopeoversikt (.csv)", "tender_scope.csv")

with tabs[5]:
    st.dataframe(audit_log_dataframe("tender", delivery_level, records, ai_result), use_container_width=True, hide_index=True)

render_panel(
    "Neste naturlige steg i pilot",
    "Denne modulen er laget som en horisontal motor for entreprenor, radgiver og utbygger. Knytt parser, review og eksport enda tettere til live konkurransegrunnlag i neste iterasjon.",
    [
        "Koble dokumentindeks og revisjonssammenligning til tilbudsbok og kontraktsgrunnlag.",
        "Legg til klikkbar avvikslogg med manuell overstyring og RFI-status.",
        "Bygg pre-fylt tilbudsgrunnlag for DOCX og signert review-flyt.",
        "Koble batch/API-laget mot partner- eller bankkanaler uten a fronte det offentlig.",
    ],
    tone="gold",
    badge="Pilot backlog",
)
