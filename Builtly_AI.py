import os
import base64
import textwrap
from pathlib import Path
from typing import Optional

import streamlit as st

# -------------------------------------------------
# 1) PAGE CONFIG
# -------------------------------------------------
st.set_page_config(
    page_title="Builtly | Engineering Portal",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# -------------------------------------------------
# 2) PAGE MAP
# -------------------------------------------------
PAGES = {
    "mulighetsstudie": "pages/Mulighetsstudie.py",
    "geo": "pages/Geo.py",
    "konstruksjon": "pages/Konstruksjon.py",
    "brann": "pages/Brannkonsept.py",
    "akustikk": "pages/Akustikk.py",
    "trafikk": "pages/trafikk.py",
    "project": "pages/Project.py",
    "review": "pages/Review.py",
}


# -------------------------------------------------
# 3) HELPERS
# -------------------------------------------------
def page_exists(page_path: str) -> bool:
    return Path(page_path).exists()


def page_route(page_key: str) -> Optional[str]:
    page_path = PAGES.get(page_key)
    if not page_path or not page_exists(page_path):
        return None
    return Path(page_path).stem


def html_dedent(s: str) -> str:
    return textwrap.dedent(s).strip()


def href_or_none(page_key: str) -> Optional[str]:
    return page_route(page_key)


def top_link(page_key: str, label: str, kind: str = "ghost") -> str:
    href = href_or_none(page_key)
    if href:
        return f'<a href="{href}" target="_self" class="top-link {kind}">{label}</a>'
    return f'<span class="top-link {kind} disabled">{label}</span>'


def hero_action(page_key: str, label: str, kind: str = "primary") -> str:
    href = href_or_none(page_key)
    if href:
        return f'<a href="{href}" target="_self" class="hero-action {kind}">{label}</a>'
    return f'<span class="hero-action {kind} disabled">{label}</span>'


def module_card(
    page_key: str,
    icon: str,
    badge: str,
    badge_class: str,
    title: str,
    description: str,
    input_text: str,
    output_text: str,
    cta_label: str,
) -> str:
    href = href_or_none(page_key)
    action_html = (
        f'<a href="{href}" target="_self" class="module-cta">{cta_label}</a>'
        if href
        else '<span class="module-cta disabled">In development</span>'
    )

    return html_dedent(
        f"""
        <div class="module-card">
            <div class="module-header">
                <div class="module-icon">{icon}</div>
                <div class="module-badge {badge_class}">{badge}</div>
            </div>
            <div class="module-title">{title}</div>
            <div class="module-desc">{description}</div>
            <div class="module-spacer"></div>
            <div class="module-meta">
                <strong>Input:</strong> {input_text}<br/>
                <strong>Output:</strong> {output_text}
            </div>
            <div class="module-cta-wrap">
                {action_html}
            </div>
        </div>
        """
    )


def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            suffix = Path(candidate).suffix.lower().replace(".", "") or "png"
            with open(candidate, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/{suffix};base64,{encoded}"
    return ""


# -------------------------------------------------
# 4) CSS
# -------------------------------------------------
st.markdown(
    """
<style>
    :root {
        --bg: #06111a;
        --panel: rgba(10, 22, 35, 0.78);
        --panel-2: rgba(13, 27, 42, 0.94);
        --stroke: rgba(120, 145, 170, 0.18);
        --stroke-strong: rgba(120, 145, 170, 0.28);
        --text: #f5f7fb;
        --muted: #9fb0c3;
        --soft: #c8d3df;
        --accent: #38c2c9;
        --accent-2: #78dce1;
        --accent-3: #112c3f;
        --ok: #7ee081;
        --warn: #f4bf4f;
        --shadow: 0 24px 90px rgba(0,0,0,0.35);
        --radius-xl: 28px;
        --radius-lg: 22px;
        --radius-md: 14px;
    }

    html, body, [class*="css"] {
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    .stApp {
        background:
            radial-gradient(1100px 500px at 15% -5%, rgba(56,194,201,0.18), transparent 50%),
            radial-gradient(900px 500px at 100% 0%, rgba(64,170,255,0.12), transparent 45%),
            linear-gradient(180deg, #071018 0%, #08131d 35%, #071018 100%);
        color: var(--text);
    }

    header[data-testid="stHeader"] {
        visibility: hidden;
        height: 0;
    }

    [data-testid="stSidebar"] {
        background: rgba(7, 16, 24, 0.96);
        border-right: 1px solid var(--stroke);
    }

    .block-container {
        max-width: 1280px !important;
        padding-top: 1.35rem !important;
        padding-bottom: 4rem !important;
    }

    .top-shell {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1.25rem;
        margin-bottom: 1rem;
    }

    .brand-left {
        display: flex;
        align-items: center;
        gap: 0.9rem;
        min-width: 0;
    }

    .brand-logo {
        display: block;
        height: 62px;
        width: auto;
        flex-shrink: 0;
        filter: drop-shadow(0 0 18px rgba(120,220,225,0.08));
    }

    .brand-text {
        min-width: 0;
    }

    .brand-name {
        color: var(--text);
        font-weight: 750;
        font-size: 1.05rem;
        line-height: 1.1;
        letter-spacing: -0.02em;
    }

    .brand-sub {
        margin-top: 0.22rem;
        color: var(--muted);
        font-size: 0.84rem;
        line-height: 1.45;
    }

    .brand-kicker {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 0.45rem;
        padding: 0.5rem 0.95rem;
        border: 1px solid rgba(56,194,201,0.24);
        background: rgba(56,194,201,0.08);
        border-radius: 999px;
        font-size: 0.84rem;
        color: var(--accent-2);
        letter-spacing: 0.01em;
        white-space: nowrap;
    }

    .topbar-right {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 0.65rem;
        padding: 0.35rem;
        border-radius: 18px;
        background: rgba(255,255,255,0.025);
        border: 1px solid rgba(120,145,170,0.12);
        flex-wrap: wrap;
    }

    .top-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 42px;
        padding: 0.72rem 1rem;
        border-radius: 12px;
        text-decoration: none !important;
        font-weight: 650;
        font-size: 0.93rem;
        transition: all 0.2s ease;
        border: 1px solid transparent;
        white-space: nowrap;
    }

    .top-link.ghost {
        color: var(--soft) !important;
        background: rgba(255,255,255,0.04);
        border-color: rgba(120,145,170,0.18);
    }

    .top-link.ghost:hover {
        color: #ffffff !important;
        border-color: rgba(56,194,201,0.38);
        background: rgba(255,255,255,0.06);
    }

    .top-link.primary {
        color: #041018 !important;
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96));
        border-color: rgba(120,220,225,0.45);
    }

    .top-link.primary:hover {
        transform: translateY(-1px);
        box-shadow: 0 10px 24px rgba(56,194,201,0.18);
    }

    .top-link.disabled,
    .hero-action.disabled,
    .module-cta.disabled {
        opacity: 0.45;
        pointer-events: none;
        cursor: default;
    }

    .hero {
        position: relative;
        overflow: hidden;
        background: linear-gradient(180deg, rgba(13,27,42,0.96), rgba(8,18,28,0.96));
        border: 1px solid rgba(120,145,170,0.16);
        border-radius: var(--radius-xl);
        padding: 2.3rem;
        box-shadow: var(--shadow);
        margin-bottom: 1.25rem;
        min-height: 100%;
    }

    .hero::before {
        content: "";
        position: absolute;
        inset: -80px -120px auto auto;
        width: 420px;
        height: 420px;
        background: radial-gradient(circle, rgba(56,194,201,0.16) 0%, transparent 62%);
        pointer-events: none;
    }

    .eyebrow {
        color: var(--accent-2);
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-size: 0.78rem;
        font-weight: 700;
        margin-bottom: 1rem;
    }

    .hero-title {
        font-size: clamp(2.55rem, 5vw, 4.35rem);
        line-height: 1.02;
        letter-spacing: -0.045em;
        font-weight: 800;
        margin: 0;
        color: var(--text);
        max-width: 12ch;
    }

    .hero-title .accent {
        color: var(--accent-2);
    }

    .hero-subtitle {
        margin-top: 1.2rem;
        max-width: 58ch;
        font-size: 1.08rem;
        line-height: 1.8;
        color: var(--soft);
    }

    .hero-note {
        margin-top: 0.95rem;
        font-size: 0.95rem;
        color: var(--muted);
    }

    .hero-actions {
        display: flex;
        gap: 0.75rem;
        flex-wrap: wrap;
        margin-top: 1.35rem;
    }

    .hero-action {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 46px;
        padding: 0.78rem 1rem;
        border-radius: 12px;
        text-decoration: none !important;
        font-weight: 650;
        font-size: 0.95rem;
        transition: all 0.2s ease;
        border: 1px solid transparent;
    }

    .hero-action.primary {
        color: #041018 !important;
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96));
        border-color: rgba(120,220,225,0.45);
    }

    .hero-action.secondary {
        color: #ffffff !important;
        background: rgba(255,255,255,0.05);
        border-color: rgba(120,145,170,0.22);
    }

    .hero-action:hover,
    .module-cta:hover {
        transform: translateY(-1px);
    }

    .proof-strip {
        display: flex;
        flex-wrap: wrap;
        gap: 0.55rem;
        margin-top: 1rem;
    }

    .proof-chip {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.42rem 0.7rem;
        border-radius: 999px;
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(120,145,170,0.16);
        color: var(--soft);
        font-size: 0.82rem;
    }

    .hero-panel {
        background: rgba(255,255,255,0.03);
        border: 1px solid var(--stroke);
        border-radius: 22px;
        padding: 1.25rem;
        min-height: 100%;
    }

    .panel-title {
        font-size: 0.86rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--muted);
        margin-bottom: 0.85rem;
    }

    .mini-stat {
        background: rgba(255,255,255,0.03);
        border: 1px solid var(--stroke);
        border-radius: 16px;
        padding: 0.95rem 1rem;
        margin-bottom: 0.75rem;
    }

    .mini-stat-value {
        font-size: 1.38rem;
        font-weight: 750;
        color: var(--text);
        line-height: 1.1;
    }

    .mini-stat-label {
        margin-top: 0.25rem;
        color: var(--muted);
        font-size: 0.9rem;
        line-height: 1.5;
    }

    .section-head {
        margin-top: 2.25rem;
        margin-bottom: 1rem;
    }

    .section-kicker {
        color: var(--accent-2);
        text-transform: uppercase;
        letter-spacing: 0.12em;
        font-size: 0.74rem;
        font-weight: 700;
        margin-bottom: 0.4rem;
    }

    .section-title {
        font-size: 1.86rem;
        font-weight: 750;
        letter-spacing: -0.03em;
        color: var(--text);
        margin: 0;
    }

    .section-subtitle {
        margin-top: 0.35rem;
        color: var(--muted);
        line-height: 1.75;
        max-width: 74ch;
    }

    .trust-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 1rem;
        margin-top: 0.8rem;
    }

    .trust-card {
        background: var(--panel);
        border: 1px solid var(--stroke);
        border-radius: 18px;
        padding: 1rem;
        min-height: 136px;
    }

    .trust-title {
        font-size: 1rem;
        font-weight: 650;
        color: var(--text);
        margin-bottom: 0.45rem;
    }

    .trust-desc {
        font-size: 0.92rem;
        line-height: 1.65;
        color: var(--muted);
    }

    .loop-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 1rem;
        margin-top: 0.8rem;
    }

    .loop-card {
        background: var(--panel-2);
        border: 1px solid var(--stroke);
        border-radius: 18px;
        padding: 1rem;
        min-height: 172px;
    }

    .loop-number {
        width: 34px;
        height: 34px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 999px;
        background: rgba(56,194,201,0.12);
        border: 1px solid rgba(56,194,201,0.22);
        color: var(--accent-2);
        font-weight: 700;
        font-size: 0.92rem;
        margin-bottom: 0.8rem;
    }

    .loop-title {
        font-size: 1rem;
        font-weight: 650;
        color: var(--text);
        margin-bottom: 0.45rem;
    }

    .loop-desc {
        font-size: 0.92rem;
        line-height: 1.65;
        color: var(--muted);
    }

    .subsection-title {
        margin-top: 1.1rem;
        margin-bottom: 0.9rem;
        font-size: 1.02rem;
        font-weight: 700;
        color: var(--text);
    }

    .module-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 1.2rem;
        align-items: stretch;
        margin-top: 0.8rem;
    }

    .module-card {
        background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98));
        border: 1px solid var(--stroke);
        border-radius: 22px;
        padding: 1.15rem;
        box-shadow: 0 12px 38px rgba(0,0,0,0.18);
        display: flex;
        flex-direction: column;
        min-height: 360px;
        height: 100%;
    }

    .module-card:hover {
        border-color: rgba(56,194,201,0.24);
        box-shadow: 0 16px 42px rgba(0,0,0,0.24);
    }

    .module-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        margin-bottom: 1rem;
    }

    .module-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 0.32rem 0.62rem;
        border-radius: 999px;
        border: 1px solid rgba(120,145,170,0.18);
        background: rgba(255,255,255,0.03);
        color: var(--muted);
        font-size: 0.75rem;
        font-weight: 650;
        letter-spacing: 0.01em;
        white-space: nowrap;
    }

    .badge-priority {
        color: #8ef0c0;
        border-color: rgba(142,240,192,0.25);
        background: rgba(126,224,129,0.08);
    }

    .badge-phase2 {
        color: #9fe7ff;
        border-color: rgba(120,220,225,0.22);
        background: rgba(56,194,201,0.08);
    }

    .badge-early {
        color: #d7def7;
        border-color: rgba(215,222,247,0.18);
        background: rgba(255,255,255,0.03);
    }

    .badge-roadmap {
        color: #f4bf4f;
        border-color: rgba(244,191,79,0.22);
        background: rgba(244,191,79,0.08);
    }

    .module-icon {
        width: 46px;
        height: 46px;
        border-radius: 14px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: rgba(56,194,201,0.1);
        border: 1px solid rgba(56,194,201,0.18);
        color: var(--accent-2);
        font-size: 1.32rem;
        flex-shrink: 0;
    }

    .module-title {
        font-size: 1.08rem;
        font-weight: 720;
        line-height: 1.35;
        color: var(--text);
        margin-bottom: 0.5rem;
    }

    .module-desc {
        font-size: 0.95rem;
        line-height: 1.72;
        color: var(--muted);
    }

    .module-spacer {
        flex: 1;
        min-height: 1rem;
    }

    .module-meta {
        font-size: 0.86rem;
        line-height: 1.75;
        color: var(--soft);
        padding-top: 0.95rem;
        border-top: 1px solid rgba(120,145,170,0.14);
    }

    .module-cta-wrap {
        margin-top: 1rem;
    }

    .module-cta {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        min-height: 46px;
        padding: 0.78rem 1rem;
        border-radius: 12px;
        background: rgba(56,194,201,0.1);
        border: 1px solid rgba(56,194,201,0.28);
        color: #f5f7fb !important;
        text-decoration: none !important;
        font-weight: 650;
        font-size: 0.94rem;
        transition: all 0.2s ease;
    }

    .module-cta:hover {
        background: rgba(56,194,201,0.18);
        border-color: rgba(56,194,201,0.5);
    }

    .cta-band {
        margin-top: 3rem;
        margin-bottom: 1.5rem;
        background: linear-gradient(135deg, rgba(56,194,201,0.12), rgba(18,49,76,0.28));
        border: 1px solid rgba(56,194,201,0.18);
        border-radius: 24px;
        padding: 1.5rem;
    }

    .cta-title {
        font-size: 1.35rem;
        font-weight: 750;
        color: var(--text);
        margin-bottom: 0.35rem;
    }

    .cta-desc {
        color: var(--muted);
        line-height: 1.78;
        max-width: 72ch;
    }

    .footer-block {
        text-align: center;
        margin-top: 3.6rem;
        padding-top: 2rem;
        border-top: 1px solid rgba(120,145,170,0.18);
    }

    .footer-name {
        font-size: 1.02rem;
        color: var(--text);
        margin-bottom: 0.35rem;
        font-weight: 650;
    }

    .footer-copy {
        color: var(--muted);
        font-size: 0.9rem;
        margin-bottom: 0.35rem;
    }

    .footer-meta {
        color: rgba(159,176,195,0.55);
        font-size: 0.8rem;
    }

    @media (max-width: 1180px) {
        .top-shell {
            flex-direction: column;
            align-items: flex-start;
        }
        .topbar-right {
            width: 100%;
            justify-content: flex-start;
        }
        .trust-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .loop-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .module-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }

    @media (max-width: 760px) {
        .trust-grid,
        .loop-grid,
        .module-grid {
            grid-template-columns: 1fr;
        }
        .hero-title {
            max-width: none;
        }
        .topbar-right {
            flex-direction: column;
            align-items: stretch;
        }
        .top-link {
            width: 100%;
        }
        .brand-kicker {
            white-space: normal;
        }
        .brand-logo {
            height: 54px;
        }
    }
</style>
""",
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 5) TOP BAR
# -------------------------------------------------
logo_html = ""
logo_uri = logo_data_uri()
if logo_uri:
    logo_html = f'<img src="{logo_uri}" class="brand-logo" alt="Builtly logo" />'
else:
    logo_html = '<div class="brand-name">Builtly</div>'

st.markdown(
    f"""
<div class="top-shell">
    <div class="brand-left">
        {logo_html}
        <div class="brand-text">
            <div class="brand-sub">AI-assisted engineering. Human-verified.</div>
        </div>
    </div>
    <div class="brand-kicker">AI-assisted engineering · Human-verified · Compliance-grade</div>
    <div class="topbar-right">
        {top_link('project', 'Project Setup', 'ghost')}
        {top_link('review', 'QA & Sign-off', 'primary')}
    </div>
</div>
""",
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 6) HERO
# -------------------------------------------------
left, right = st.columns([1.42, 0.78], gap="large")

with left:
    st.markdown(
        f"""
<div class="hero">
    <div class="eyebrow">The Builtly Loop</div>
    <h1 class="hero-title">From <span class="accent">raw data</span> to signed deliverables.</h1>
    <div class="hero-subtitle">
        Builtly is the customer portal for compliance-grade engineering delivery.
        Upload project inputs, let the platform validate, calculate, check rules, and draft the report -
        before junior QA and senior sign-off turn it into a consistent, traceable, submission-ready package.
    </div>
    <div class="hero-note">
        Built for building applications, execution, and professional compliance - designed as a production workflow, not a showcase UI.
    </div>
    <div class="hero-actions">
        {hero_action('project', 'Open project setup', 'primary')}
        {hero_action('review', 'Open QA and sign-off', 'secondary')}
    </div>
    <div class="proof-strip">
        <div class="proof-chip">Rules-first</div>
        <div class="proof-chip">Audit trail</div>
        <div class="proof-chip">PDF + DOCX output</div>
        <div class="proof-chip">Digital sign-off</div>
        <div class="proof-chip">Structured QA workflow</div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

with right:
    st.markdown(
        """
<div class="hero-panel">
    <div class="panel-title">Why Builtly?</div>
    <div class="mini-stat">
        <div class="mini-stat-value">80-90%</div>
        <div class="mini-stat-label">Reduction in manual drafting and repetitive report production</div>
    </div>
    <div class="mini-stat">
        <div class="mini-stat-value">Junior + Senior</div>
        <div class="mini-stat-label">Human-in-the-loop QA, technical control, and digital sign-off</div>
    </div>
    <div class="mini-stat">
        <div class="mini-stat-value">PDF + DOCX</div>
        <div class="mini-stat-label">Complete report packages with appendices and traceability</div>
    </div>
    <div class="mini-stat" style="margin-bottom:0;">
        <div class="mini-stat-value">Full Traceability</div>
        <div class="mini-stat-label">Inputs, versions, compliance checks, and signatures logged end-to-end</div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

# -------------------------------------------------
# 7) VALUE PROPOSITION
# -------------------------------------------------
st.markdown(
    """
<div class="section-head">
    <div class="section-kicker">Core value proposition</div>
    <h2 class="section-title">Portal first. Modules under.</h2>
    <div class="section-subtitle">
        Builtly is not a collection of disconnected tools. It is one secure portal for project setup,
        data ingestion, validation, AI processing, review, sign-off, and final delivery.
    </div>
</div>
<div class="trust-grid">
    <div class="trust-card">
        <div class="trust-title">Client portal</div>
        <div class="trust-desc">Project creation, input uploads, missing-data follow-up, document generation, and audit trails in one workflow.</div>
    </div>
    <div class="trust-card">
        <div class="trust-title">Rules-first AI</div>
        <div class="trust-desc">AI operates inside explicit regulatory guardrails, checklists, and standard templates - not as free-form guesswork.</div>
    </div>
    <div class="trust-card">
        <div class="trust-title">QA and sign-off</div>
        <div class="trust-desc">Junior engineers validate plausibility and structure. Senior engineers provide final review and certification.</div>
    </div>
    <div class="trust-card">
        <div class="trust-title">Scalable delivery</div>
        <div class="trust-desc">Each discipline plugs into the same validation, documentation, and sign-off backbone.</div>
    </div>
</div>
""",
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 8) WORKFLOW
# -------------------------------------------------
st.markdown(
    """
<div class="section-head">
    <div class="section-kicker">Workflow</div>
    <h2 class="section-title">The Builtly Loop</h2>
    <div class="section-subtitle">
        A deterministic four-step workflow that takes you from fragmented project data to a reviewable,
        compliant engineering package.
    </div>
</div>
<div class="loop-grid">
    <div class="loop-card">
        <div class="loop-number">1</div>
        <div class="loop-title">Input</div>
        <div class="loop-desc">Upload PDFs, IFC models, XLSX lab files, drawings, and project-specific data in one place.</div>
    </div>
    <div class="loop-card">
        <div class="loop-number">2</div>
        <div class="loop-title">Validate and analyze</div>
        <div class="loop-desc">The platform parses, validates, applies local rule checks, performs calculations, and drafts the deliverable.</div>
    </div>
    <div class="loop-card">
        <div class="loop-number">3</div>
        <div class="loop-title">QA and sign-off</div>
        <div class="loop-desc">Junior review, senior technical assessment, and digital sign-off - with version control throughout.</div>
    </div>
    <div class="loop-card">
        <div class="loop-number">4</div>
        <div class="loop-title">Output</div>
        <div class="loop-desc">Finalized documentation package in standard formats, ready for municipal submission or execution.</div>
    </div>
</div>
""",
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 9) MODULES
# -------------------------------------------------
available_cards = [
    module_card(
        "geo",
        "🌍",
        "Phase 1 - Priority",
        "badge-priority",
        "GEO / ENV - Ground Conditions",
        "Analyze lab files and excavation plans. Classifies masses, proposes disposal logic, and drafts environmental action plans.",
        "XLSX / CSV / PDF + plans",
        "Environmental action plan, logs",
        "Open Geo & Env",
    ),
    module_card(
        "akustikk",
        "🔊",
        "Phase 2",
        "badge-phase2",
        "ACOUSTICS - Noise & Sound",
        "Ingest noise maps and floor plans. Generates facade requirements, window specifications, and mitigation strategies.",
        "Noise map + floor plan",
        "Acoustics report, facade evaluation",
        "Open Acoustics",
    ),
    module_card(
        "brann",
        "🔥",
        "Phase 2",
        "badge-phase2",
        "FIRE - Safety Strategy",
        "Evaluate architectural drawings against building codes. Generates escape routes, fire cell division, and fire strategy.",
        "Architectural drawings + class",
        "Fire strategy concept, deviations",
        "Open Fire Strategy",
    ),
]

roadmap_cards = [
    module_card(
        "mulighetsstudie",
        "📐",
        "Early phase",
        "badge-early",
        "ARK - Feasibility Study",
        "Site screening, volume analysis, and early-phase decision support before full engineering design.",
        "Site data, zoning plans",
        "Feasibility report, utilization metrics",
        "Open Feasibility",
    ),
    module_card(
        "konstruksjon",
        "🏢",
        "Roadmap",
        "badge-roadmap",
        "STRUC - Structural Concept",
        "Conceptual structural checks, principle dimensioning, and integration with carbon footprint estimations.",
        "Models, load parameters",
        "Concept memo, grid layouts",
        "Open Structural",
    ),
    module_card(
        "trafikk",
        "🚦",
        "Roadmap",
        "badge-roadmap",
        "TRAFFIC - Mobility",
        "Traffic generation, parking requirements, access logic, and soft-mobility planning for early project phases.",
        "Site plans, local norms",
        "Traffic memo, mobility plan",
        "Open Traffic & Mobility",
    ),
]

st.markdown(
    html_dedent(
        f"""
        <div class="section-head">
            <div class="section-kicker">Modules and roadmap</div>
            <h2 class="section-title">Specialized agents in one platform</h2>
            <div class="section-subtitle">
                Each module has dedicated ingestion logic, discipline-specific rules, and output templates while sharing the same portal, validation, QA, and sign-off backbone.
            </div>
        </div>

        <div class="subsection-title">Available now and pilot-ready</div>
        <div class="module-grid">{''.join(available_cards)}</div>

        <div class="subsection-title">Roadmap and early-phase tools</div>
        <div class="module-grid">{''.join(roadmap_cards)}</div>
        """
    ),
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 10) CTA BAND
# -------------------------------------------------
st.markdown(
    f"""
<div class="cta-band">
    <div class="cta-title">Not just analysis. Actual deliverables.</div>
    <div class="cta-desc">
        Builtly operates as a full-stack delivery system: create a project, upload raw data, review deviations,
        generate drafts, execute QA, and download the signed documentation package.
    </div>
    <div class="hero-actions" style="margin-top:1rem;">
        {hero_action('project', 'Start in project setup', 'primary')}
        {hero_action('review', 'Go to review queue', 'secondary')}
    </div>
</div>
""",
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 11) FOOTER
# -------------------------------------------------
st.markdown(
    """
<div class="footer-block">
    <div class="footer-name">Builtly AS</div>
    <div class="footer-copy">AI-assisted engineering. Human-verified. Compliance-grade.</div>
    <div class="footer-meta">© 2026 Builtly. All rights reserved.</div>
</div>
""",
    unsafe_allow_html=True,
)
