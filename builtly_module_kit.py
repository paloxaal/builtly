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
