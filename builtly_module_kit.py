from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd
import streamlit as st

DEFAULT_PROJECT_STATE = {
    "land": "Norge (TEK17 / plan- og bygningsloven)",
    "p_name": "Nytt Prosjekt",
    "c_name": "",
    "p_desc": "Modulært prosjekt i tidligfase med behov for mer effektiv dokumentasjon og QA.",
    "adresse": "Kjøpmannsgata 34",
    "kommune": "Trondheim",
    "gnr": "231",
    "bnr": "442",
    "b_type": "Næring / Kontor",
    "etasjer": 4,
    "bta": 2500,
    "last_sync": "Synket for 2 min siden",
}


def ensure_project_state() -> dict:
    if "project_data" not in st.session_state:
        st.session_state.project_data = dict(DEFAULT_PROJECT_STATE)
    else:
        merged = dict(DEFAULT_PROJECT_STATE)
        merged.update(st.session_state.project_data)
        st.session_state.project_data = merged
    return st.session_state.project_data


CSS = """
<style>
    :root {
        --bg: #06080d;
        --panel: rgba(11, 16, 27, 0.88);
        --panel-strong: rgba(13, 19, 32, 0.96);
        --stroke: rgba(255,255,255,0.08);
        --stroke-strong: rgba(110,168,254,0.28);
        --text: #f5f7fb;
        --muted: #98a3b8;
        --accent: #6ea8fe;
        --accent-2: #7c5cff;
        --success: #31d0aa;
        --warning: #ffcc66;
        --danger: #ff8b8b;
        --shadow: 0 24px 80px rgba(0,0,0,0.34);
    }

    .stApp {
        color: var(--text);
        background:
            radial-gradient(circle at 12% -8%, rgba(110,168,254,0.18), transparent 28%),
            radial-gradient(circle at 94% 0%, rgba(124,92,255,0.16), transparent 25%),
            linear-gradient(180deg, #05070b 0%, #070b12 42%, #06070a 100%);
    }

    header { visibility: hidden; }
    .block-container {
        max-width: 1460px !important;
        padding-top: 2rem !important;
        padding-bottom: 4rem !important;
    }

    .builtly-hero {
        position: relative;
        overflow: hidden;
        border-radius: 26px;
        padding: 2rem;
        margin-bottom: 1.1rem;
        border: 1px solid rgba(255,255,255,0.08);
        background:
            linear-gradient(180deg, rgba(16,22,37,0.92), rgba(10,14,23,0.96)),
            linear-gradient(135deg, rgba(110,168,254,0.12), rgba(124,92,255,0.06));
        box-shadow: var(--shadow);
    }

    .builtly-hero::before {
        content: "";
        position: absolute;
        width: 320px;
        height: 320px;
        top: -120px;
        right: -80px;
        border-radius: 999px;
        background: radial-gradient(circle, rgba(110,168,254,0.30), transparent 68%);
        filter: blur(12px);
    }

    .builtly-eyebrow {
        display: inline-flex;
        align-items: center;
        gap: 0.45rem;
        padding: 0.5rem 0.85rem;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,0.12);
        background: rgba(255,255,255,0.04);
        color: #dce7fa;
        font-size: 0.82rem;
        font-weight: 700;
        letter-spacing: 0.02em;
    }

    .builtly-hero-title {
        margin: 1rem 0 0.7rem 0;
        font-size: clamp(2.35rem, 3.8vw, 3.6rem);
        line-height: 0.98;
        letter-spacing: -0.04em;
        font-weight: 800;
        color: #ffffff;
        max-width: 860px;
    }

    .builtly-hero-sub {
        margin: 0;
        max-width: 900px;
        color: var(--muted);
        font-size: 1.02rem;
        line-height: 1.7;
    }

    .builtly-pills {
        display: flex;
        flex-wrap: wrap;
        gap: 0.65rem;
        margin-top: 1.25rem;
    }

    .builtly-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.45rem;
        border-radius: 999px;
        padding: 0.48rem 0.8rem;
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(255,255,255,0.035);
        color: #dbe4f7;
        font-size: 0.82rem;
        font-weight: 600;
    }

    .builtly-section-head { margin: 1.6rem 0 0.75rem 0; }
    .builtly-section-kicker {
        color: #9fb3d8;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        font-size: 0.72rem;
        font-weight: 800;
    }
    .builtly-section-title {
        margin: 0.35rem 0 0.25rem 0;
        font-size: 1.4rem;
        font-weight: 800;
        letter-spacing: -0.03em;
        color: #fff;
    }
    .builtly-section-sub {
        color: var(--muted);
        font-size: 0.96rem;
        line-height: 1.65;
        max-width: 980px;
    }

    .builtly-metric {
        border-radius: 22px;
        border: 1px solid rgba(255,255,255,0.08);
        background: linear-gradient(180deg, rgba(13,18,30,0.90), rgba(10,14,24,0.94));
        box-shadow: var(--shadow);
        padding: 1.1rem 1rem;
        min-height: 118px;
    }
    .builtly-metric-label {
        color: #9fb3d8;
        text-transform: uppercase;
        letter-spacing: 0.10em;
        font-size: 0.68rem;
        font-weight: 800;
        margin-bottom: 0.6rem;
    }
    .builtly-metric-value {
        font-size: 1.95rem;
        font-weight: 800;
        line-height: 1;
        color: #fff;
        letter-spacing: -0.04em;
        margin-bottom: 0.45rem;
    }
    .builtly-metric-desc {
        color: var(--muted);
        font-size: 0.88rem;
        line-height: 1.5;
    }

    .builtly-panel {
        border-radius: 24px;
        border: 1px solid rgba(255,255,255,0.08);
        background: linear-gradient(180deg, rgba(13,18,30,0.90), rgba(10,14,24,0.94));
        box-shadow: var(--shadow);
        padding: 1.2rem 1.2rem 1rem 1.2rem;
        margin-bottom: 1rem;
    }
    .builtly-panel-title {
        margin: 0;
        color: #fff;
        font-size: 1.05rem;
        font-weight: 800;
        letter-spacing: -0.02em;
    }
    .builtly-panel-sub {
        margin: 0.35rem 0 0 0;
        color: var(--muted);
        font-size: 0.92rem;
        line-height: 1.6;
    }

    .builtly-list { margin: 0.9rem 0 0 0; padding-left: 1rem; }
    .builtly-list li {
        margin: 0.25rem 0;
        color: #dfe5ef;
        line-height: 1.55;
    }

    .builtly-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 0.36rem 0.68rem;
        border-radius: 999px;
        font-size: 0.74rem;
        font-weight: 800;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        margin-bottom: 0.7rem;
    }
    .tone-blue { background: rgba(110, 168, 254, 0.12); color: #9bc3ff; border: 1px solid rgba(110, 168, 254, 0.24); }
    .tone-green { background: rgba(49, 208, 170, 0.12); color: #84f0d4; border: 1px solid rgba(49, 208, 170, 0.24); }
    .tone-gold { background: rgba(255, 204, 102, 0.12); color: #ffd480; border: 1px solid rgba(255, 204, 102, 0.22); }
    .tone-red { background: rgba(255, 139, 139, 0.12); color: #ffb0b0; border: 1px solid rgba(255, 139, 139, 0.22); }

    .builtly-code {
        border-radius: 18px;
        border: 1px solid rgba(255,255,255,0.06);
        background: rgba(7, 11, 18, 0.92);
        padding: 1rem;
    }

    .stDataFrame, [data-testid="stDataFrame"] {
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 18px !important;
        overflow: hidden !important;
        background: rgba(8, 12, 18, 0.88) !important;
    }

    [data-testid="stMetric"] {
        background: linear-gradient(180deg, rgba(13,18,30,0.90), rgba(10,14,24,0.94));
        border: 1px solid rgba(255,255,255,0.08);
        padding: 1rem;
        border-radius: 18px;
    }

    [data-testid="stMetricLabel"] {
        color: #9fb3d8 !important;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }

    [data-testid="stExpander"] {
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 18px;
        background: rgba(10,14,23,0.78);
    }

    .stTabs [data-baseweb="tab-list"] { gap: 0.45rem; }
    .stTabs [data-baseweb="tab"] {
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,0.08);
        background: rgba(255,255,255,0.03);
        color: #dce7fa;
        padding: 0.55rem 0.95rem;
    }
    .stTabs [aria-selected="true"] {
        background: rgba(110,168,254,0.16) !important;
        border-color: rgba(110,168,254,0.24) !important;
        color: #fff !important;
    }
</style>
"""


def configure_page(title: str, icon: str = "🏗️") -> dict:
    st.set_page_config(
        page_title=title,
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(CSS, unsafe_allow_html=True)
    project = ensure_project_state()
    return project


def render_html(html: str) -> None:
    st.markdown(html, unsafe_allow_html=True)



def render_hero(
    eyebrow: str,
    title: str,
    subtitle: str,
    pills: Sequence[str] | None = None,
    badge: str = "Pilot module",
) -> None:
    pills_html = "".join(f'<span class="builtly-pill">{item}</span>' for item in (pills or []))
    render_html(
        f"""
        <div class="builtly-hero">
            <div class="builtly-eyebrow">{badge}</div>
            <div class="builtly-hero-title">{title}</div>
            <div class="builtly-hero-sub">{subtitle}</div>
            <div class="builtly-pills">{pills_html}</div>
        </div>
        """
    )



def render_section(title: str, subtitle: str = "", kicker: str = "") -> None:
    kicker_html = f'<div class="builtly-section-kicker">{kicker}</div>' if kicker else ""
    subtitle_html = f'<div class="builtly-section-sub">{subtitle}</div>' if subtitle else ""
    render_html(
        f"""
        <div class="builtly-section-head">
            {kicker_html}
            <div class="builtly-section-title">{title}</div>
            {subtitle_html}
        </div>
        """
    )



def render_metric_cards(metrics: Sequence[dict]) -> None:
    cols = st.columns(len(metrics))
    for col, metric in zip(cols, metrics):
        with col:
            render_html(
                f"""
                <div class="builtly-metric">
                    <div class="builtly-metric-label">{metric.get('label', '')}</div>
                    <div class="builtly-metric-value">{metric.get('value', '')}</div>
                    <div class="builtly-metric-desc">{metric.get('desc', '')}</div>
                </div>
                """
            )



def render_panel(title: str, subtitle: str = "", items: Iterable[str] | None = None, tone: str = "blue", badge: str | None = None) -> None:
    badge_html = f'<div class="builtly-badge tone-{tone}">{badge}</div>' if badge else ""
    items_html = ""
    if items:
        items_html = "<ul class=\"builtly-list\">" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"
    render_html(
        f"""
        <div class="builtly-panel">
            {badge_html}
            <div class="builtly-panel-title">{title}</div>
            <div class="builtly-panel-sub">{subtitle}</div>
            {items_html}
        </div>
        """
    )



def render_project_snapshot(project: dict, badge: str = "SSOT synced") -> None:
    lines = [
        f"<strong>Prosjekt:</strong> {project.get('p_name', '-')}",
        f"<strong>Klient:</strong> {project.get('c_name') or 'Ikke angitt'}",
        f"<strong>Adresse:</strong> {project.get('adresse') or '-'}, {project.get('kommune') or '-'}",
        f"<strong>Type:</strong> {project.get('b_type') or '-'}",
        f"<strong>BTA:</strong> {project.get('bta') or '-'} m² &nbsp; | &nbsp; <strong>Etasjer:</strong> {project.get('etasjer') or '-'}",
        f"<strong>Sist synket:</strong> {project.get('last_sync') or '-'}",
    ]
    render_panel(
        "Prosjektsnapshot",
        "Modulen henter prosjektkontekst direkte fra Builtlys SSOT slik at dokumenter, QA og sporbarhet bygger på samme grunnlag.",
        lines,
        tone="green",
        badge=badge,
    )



def dataframe_download(df: pd.DataFrame, label: str, filename: str) -> None:
    st.download_button(label, df.to_csv(index=False).encode("utf-8"), file_name=filename, mime="text/csv")



def json_download(payload: dict, label: str, filename: str) -> None:
    st.download_button(
        label,
        json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"),
        file_name=filename,
        mime="application/json",
    )



def sample_revision_trace() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"Revisjon": "A", "Kilde": "Situasjonsplan v1", "Status": "Indeksert", "Kommentar": "Første baseline"},
            {"Revisjon": "B", "Kilde": "PDF-sett v2", "Status": "Sammenlignet", "Kommentar": "Endringslogg generert"},
            {"Revisjon": "C", "Kilde": "IFC 2026-03", "Status": "QA pågår", "Kommentar": "Manuell kontroll av avvik"},
        ]
    )



def tone_from_score(score: float) -> str:
    if score >= 4.0:
        return "red"
    if score >= 3.0:
        return "gold"
    if score >= 2.0:
        return "blue"
    return "green"


def _default_analysis_schema() -> dict:
    return {
        "executive_summary": "",
        "confidence": "Lav | Middels | Høy",
        "recommended_status": "Draft | Review Needed | Ready for Human Review",
        "key_findings": [
            {
                "topic": "",
                "severity": "Lav | Middels | Høy",
                "detail": "",
                "source_refs": ["filename.ext"],
            }
        ],
        "gaps": [
            {"item": "", "impact": "", "recommended_action": ""}
        ],
        "questions": [
            {"priority": "1", "question": "", "owner": ""}
        ],
        "next_actions": [
            {"action": "", "owner": "", "timing": ""}
        ],
        "export_recommendations": [
            {"artifact": "", "purpose": "", "status": ""}
        ],
    }



def _items_to_dataframe(items) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()
    if isinstance(items, list):
        rows = []
        for item in items:
            rows.append(item if isinstance(item, dict) else {"value": str(item)})
        return pd.DataFrame(rows)
    if isinstance(items, dict):
        return pd.DataFrame([items])
    return pd.DataFrame([{"value": str(items)}])



def _render_ai_result(ai_payload: dict) -> None:
    data = ai_payload.get("data") or {}
    summary = data.get("executive_summary") or "Ingen AI-oppsummering generert ennå."
    provider = ai_payload.get("provider") or "-"
    model = ai_payload.get("model") or "-"
    render_panel(
        "AI-utkast",
        summary,
        [
            f"Anbefalt status: {data.get('recommended_status', '-')}",
            f"Faglig trygghet: {data.get('confidence', '-')}",
            f"Generert med: {provider} / {model}",
        ],
        tone="green",
        badge="Draft ready",
    )

    section_map = [
        ("key_findings", "Nøkkelfunn"),
        ("gaps", "Mangler"),
        ("questions", "Spørsmål"),
        ("next_actions", "Neste steg"),
        ("export_recommendations", "Eksportpakke"),
    ]
    tabs = st.tabs([label for _, label in section_map])
    for tab, (section_key, label) in zip(tabs, section_map):
        with tab:
            df = _items_to_dataframe(data.get(section_key) or [])
            if df.empty:
                st.info("Ingen punkter generert i denne seksjonen ennå.")
            else:
                st.dataframe(df, use_container_width=True, hide_index=True)

    attempt_log = ai_payload.get("attempt_log") or []
    if attempt_log:
        with st.expander("Vis AI-fallback-logg"):
            st.dataframe(pd.DataFrame(attempt_log), use_container_width=True, hide_index=True)



def render_shared_document_engine(
    *,
    module_key: str,
    module_title: str,
    objective: str,
    focus_points: Sequence[str],
    desired_outputs: Sequence[str],
    task: str = "document_engine",
    default_notes: str = "",
    extra_instruction: str = "",
) -> dict:
    from builtly_ai_fallback import (
        ai_service_ready,
        generate_json_with_fallback,
        provider_labels,
        provider_order_for_task,
    )
    from builtly_document_engine import (
        build_markdown_report,
        documents_to_ai_context,
        estimate_context_chars,
        manifest_dataframe,
        normalize_uploaded_files,
        revision_dataframe,
    )

    state_key = f"{module_key}_shared_doc_engine"
    if state_key not in st.session_state:
        st.session_state[state_key] = {
            "records": [],
            "notes": default_notes,
            "compare_mode": "Standard",
            "ai_payload": None,
        }
    state = st.session_state[state_key]
    project = ensure_project_state()

    render_section(
        "Felles dokumentmotor",
        "Én delt motor for opplasting, manifest, revisjonssammenligning, AI-utkast og eksport. Dette gjør at nye moduler kan bruke samme dataflyt i stedet for å bygge alt på nytt.",
        "Shared engine",
    )

    tabs = st.tabs(["Inntak", "Manifest", "Revisjoner", "AI-utkast", "Eksport"])

    with tabs[0]:
        c1, c2 = st.columns([1.2, 0.8], gap="large")
        with c1:
            uploaded_files = st.file_uploader(
                "Last opp dokumentgrunnlag for denne modulen",
                type=["pdf", "docx", "xlsx", "xls", "csv", "txt", "ifc", "json", "md", "png", "jpg", "jpeg", "zip"],
                accept_multiple_files=True,
                key=f"{module_key}_shared_files",
            )
            notes = st.text_area(
                "Notater til AI-motoren",
                value=state.get("notes", default_notes),
                height=120,
                key=f"{module_key}_shared_notes",
            )
            compare_mode = st.selectbox(
                "Revisjonsmodus",
                ["Standard", "Revisjon mot revisjon", "Scope-sjekk", "Eksportpakke"],
                index=["Standard", "Revisjon mot revisjon", "Scope-sjekk", "Eksportpakke"].index(state.get("compare_mode", "Standard")),
                key=f"{module_key}_shared_compare_mode",
            )
            if st.button("Indekser dokumenter", key=f"{module_key}_shared_index"):
                state["records"] = normalize_uploaded_files(uploaded_files)
                state["notes"] = notes
                state["compare_mode"] = compare_mode
                state["ai_payload"] = state.get("ai_payload")
        with c2:
            records = state.get("records", [])
            unique_stems = len({record.get("canonical_stem") for record in records}) if records else 0
            revision_groups = sum(1 for stem in {record.get("canonical_stem") for record in records} if sum(1 for r in records if r.get("canonical_stem") == stem) > 1)
            preview_chars = estimate_context_chars(records)
            render_metric_cards(
                [
                    {"label": "Dokumenter", "value": f"{len(records)}", "desc": "Indeksert i felles document engine."},
                    {"label": "Dokumentfamilier", "value": f"{unique_stems}", "desc": "Unike dokumentstammer for versjon og sporbarhet."},
                    {"label": "Revisjonskjeder", "value": f"{revision_groups}", "desc": "Familier med flere versjoner eller endrede hashes."},
                    {"label": "AI-kontekst", "value": f"{preview_chars:,} tegn".replace(',', ' '), "desc": "Tekstgrunnlag tilgjengelig for førsteutkast og QA."},
                ]
            )
            render_panel(
                "Hva denne motoren gjør",
                "Den samler dokumentgrunnlag, lager manifest, oppdager revisjoner, sender et strukturert sammendrag til AI og pakker resultatet klart for eksport.",
                [
                    "Tydelig kildegrunnlag",
                    "Manuell overstyring og nye runder når grunnlaget endres",
                    "Revisjonslogg og sammenligning",
                    "Samme eksportlogikk på tvers av moduler",
                ],
                tone="blue",
                badge="Backbone",
            )

    with tabs[1]:
        records = state.get("records", [])
        manifest_df = manifest_dataframe(records)
        if manifest_df.empty:
            st.info("Ingen dokumenter indeksert ennå.")
        else:
            st.dataframe(manifest_df, use_container_width=True, hide_index=True)
            dataframe_download(manifest_df, "Last ned manifest (.csv)", f"{module_key}_manifest.csv")
            for record in records[:6]:
                with st.expander(record.get("filename", "Dokument")):
                    warning = record.get("preview_warning") or ""
                    if warning:
                        st.warning(warning)
                    preview = record.get("text_preview") or "Ingen tekstpreview tilgjengelig i denne versjonen."
                    st.code(preview[:2500], language="text")

    with tabs[2]:
        records = state.get("records", [])
        revisions_df = revision_dataframe(records)
        if revisions_df.empty:
            st.info("Ingen revisjonsdata tilgjengelig ennå.")
        else:
            st.dataframe(revisions_df, use_container_width=True, hide_index=True)
            dataframe_download(revisions_df, "Last ned revisjonslogg (.csv)", f"{module_key}_revisions.csv")

    with tabs[3]:
        records = state.get("records", [])
        context_chars = estimate_context_chars(records)
        providers = provider_order_for_task(task, context_chars)
        st.caption(
            "AI-stack: " + (" → ".join(provider_labels(providers)) if providers else "Ingen leverandører konfigurert")
        )
        analysis_angle = st.selectbox(
            "Analyseprofil",
            ["Teknisk QA", "Beslutningsnotat", "Ledelsesoppsummering"],
            index=0,
            key=f"{module_key}_analysis_angle",
        )
        extra_questions = st.text_area(
            "Ekstra spørsmål eller fokusområder",
            value="",
            height=90,
            key=f"{module_key}_extra_questions",
        )

        if st.button("Kjør AI-utkast", key=f"{module_key}_run_ai"):
            if not records:
                st.warning("Last opp og indekser minst ett dokument først.")
            elif not ai_service_ready():
                st.error("Sett minst én av OPENAI_API_KEY, ANTHROPIC_API_KEY eller GEMINI_API_KEY i miljøet før AI-utkast kan kjøres.")
            else:
                schema_hint = _default_analysis_schema()
                focus_text = "\n".join(f"- {item}" for item in focus_points)
                output_text = "\n".join(f"- {item}" for item in desired_outputs)
                system_prompt = f"""
Du er Builtlys modulmotor for {module_title}.
Mål: {objective}

Viktige arbeidsregler:
- Skriv som en erfaren faglig assistent som hjelper kunden raskere frem, ikke som intern forretningsutvikling.
- Skill mellom bekreftede forhold, antakelser og åpne spørsmål.
- Ikke påstå at noe er godkjent eller endelig signert.
- Hold tonen konkret, tillitsvekkende og egnet for deling med kunde eller prosjektteam.
- Prioriter praktiske anbefalinger som kan gjennomføres i prosjektet.

Fokuser spesielt på:
{focus_text}

Ønskede leveranser:
{output_text}

{extra_instruction.strip()}
                """.strip()
                user_prompt = f"""
Prosjekt-SSOT:
{json.dumps(project, ensure_ascii=False, indent=2)}

Analyseprofil: {analysis_angle}
Revisjonsmodus: {state.get('compare_mode', 'Standard')}
Notater fra bruker:
{state.get('notes', '') or '-'}

Ekstra spørsmål:
{extra_questions or '-'}

Dokumentgrunnlag:
{documents_to_ai_context(records)}
                """.strip()
                state["ai_payload"] = generate_json_with_fallback(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema_hint=schema_hint,
                    task=task,
                    estimated_context_chars=context_chars,
                    max_output_tokens=1800,
                )

        ai_payload = state.get("ai_payload")
        if ai_payload:
            if ai_payload.get("ok") and ai_payload.get("data") is not None:
                _render_ai_result(ai_payload)
            else:
                st.error(ai_payload.get("error") or "AI-motoren klarte ikke å generere et gyldig utkast.")
                attempt_log = ai_payload.get("attempt_log") or []
                if attempt_log:
                    st.dataframe(pd.DataFrame(attempt_log), use_container_width=True, hide_index=True)
        else:
            render_panel(
                "Klar for AI-utkast",
                "Når dokumentene er indeksert kan Builtly lage et førsteutkast som samler nøkkelfunn, mangler, spørsmål, neste steg og anbefalt eksportpakke.",
                [
                    "Best leverandør velges automatisk",
                    "Fallback kjøres hvis første leverandør feiler",
                    "Samme outputstruktur på tvers av moduler",
                ],
                tone="gold",
                badge="AI fallback",
            )

    with tabs[4]:
        records = state.get("records", [])
        ai_payload = state.get("ai_payload") or {}
        manifest_df = manifest_dataframe(records)
        revisions_df = revision_dataframe(records)
        export_bundle = {
            "module": module_title,
            "project": project,
            "records": records,
            "revisions": revisions_df.to_dict("records") if not revisions_df.empty else [],
            "ai": ai_payload.get("data") or {},
            "provider_meta": {
                "provider": ai_payload.get("provider"),
                "model": ai_payload.get("model"),
                "attempt_log": ai_payload.get("attempt_log") or [],
            },
        }
        markdown_report = build_markdown_report(
            module_title=module_title,
            project=project,
            manifest_records=records,
            revision_records=revisions_df.to_dict("records") if not revisions_df.empty else [],
            ai_payload=ai_payload,
        )
        if not manifest_df.empty:
            dataframe_download(manifest_df, "Eksporter manifest (.csv)", f"{module_key}_manifest_export.csv")
        if not revisions_df.empty:
            dataframe_download(revisions_df, "Eksporter revisjoner (.csv)", f"{module_key}_revisions_export.csv")
        json_download(export_bundle, "Eksporter samlet pakke (.json)", f"{module_key}_bundle.json")
        st.download_button(
            "Eksporter AI-notat (.md)",
            markdown_report.encode("utf-8"),
            file_name=f"{module_key}_note.md",
            mime="text/markdown",
        )
        st.code(markdown_report[:4000], language="markdown")

    return state
