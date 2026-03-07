import base64
import html
import os
from pathlib import Path
from textwrap import dedent

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
# 2) ROUTES
# Update only if your deployed page paths differ.
# -------------------------------------------------
PAGES = {
    "mulighetsstudie": {"route": "Mulighetsstudie"},
    "geo": {"route": "Geo"},
    "konstruksjon": {"route": "Konstruksjon"},
    "brann": {"route": "Brannkonsept"},
    "akustikk": {"route": "Akustikk"},
    "trafikk": {"route": "Trafikk.py"},
    "project": {"route": "Project"},
    "review": {"route": "Review"},
}


def page_url(page_key: str) -> str:
    route = PAGES[page_key]["route"]
    if route.startswith("http://") or route.startswith("https://"):
        return route
    if route.startswith("/"):
        return route
    return f"/{route}"


# -------------------------------------------------
# 3) HELPERS
# -------------------------------------------------
def clean_html(markup: str) -> str:
    lines = dedent(markup).splitlines()
    return "\n".join(line.strip() for line in lines if line.strip())



def logo_markup() -> str:
    for candidate in ("logo-white.png", "logo.png"):
        if os.path.exists(candidate):
            ext = Path(candidate).suffix.lower().replace(".", "") or "png"
            mime = "image/png" if ext == "png" else f"image/{ext}"
            data = base64.b64encode(Path(candidate).read_bytes()).decode("utf-8")
            return (
                f'<img src="data:{mime};base64,{data}" '
                'alt="Builtly" class="brand-logo-img"/>'
            )
    return '<div class="brand-fallback">Builtly</div>'



def top_button(label: str, href: str, primary: bool = False) -> str:
    cls = "top-button top-button-primary" if primary else "top-button top-button-secondary"
    return f'<a class="{cls}" href="{html.escape(href)}" target="_self">{html.escape(label)}</a>'



def workflow_card(number: int, title: str, desc: str) -> str:
    return clean_html(
        f"""
        <div class="workflow-card">
            <div class="workflow-number">{number}</div>
            <div class="workflow-title">{html.escape(title)}</div>
            <div class="workflow-desc">{html.escape(desc)}</div>
        </div>
        """
    )



def module_card(
    title: str,
    eyebrow: str,
    icon: str,
    description: str,
    input_text: str,
    output_text: str,
    cta_label: str,
    href: str,
) -> str:
    return clean_html(
        f"""
        <a href="{html.escape(href)}" target="_self" class="module-card">
            <div class="module-top">
                <div class="module-icon">{icon}</div>
                <div class="module-eyebrow">{html.escape(eyebrow)}</div>
            </div>
            <div class="module-title">{html.escape(title)}</div>
            <div class="module-desc">{html.escape(description)}</div>
            <div class="module-spacer"></div>
            <div class="module-meta">
                <div><strong>Input</strong> {html.escape(input_text)}</div>
                <div><strong>Output</strong> {html.escape(output_text)}</div>
            </div>
            <div class="module-cta-wrap">
                <span class="module-cta">{html.escape(cta_label)}</span>
            </div>
        </a>
        """
    )


# -------------------------------------------------
# 4) CSS
# -------------------------------------------------
st.markdown(
    clean_html(
        """
        <style>
        :root {
            --bg-0: #050d16;
            --bg-1: #071220;
            --bg-2: #081727;
            --panel: rgba(9, 19, 31, 0.94);
            --panel-2: rgba(12, 25, 39, 0.94);
            --panel-3: rgba(14, 30, 46, 0.88);
            --stroke: rgba(127, 152, 180, 0.18);
            --stroke-strong: rgba(127, 152, 180, 0.28);
            --text: #f3f7fb;
            --muted: #9fb2c6;
            --soft: #cad5e0;
            --accent: #50d4e1;
            --accent-2: #7be7f0;
            --accent-3: #123146;
            --shadow-xl: 0 30px 90px rgba(0, 0, 0, 0.42);
            --shadow-lg: 0 18px 50px rgba(0, 0, 0, 0.28);
            --radius-xl: 28px;
            --radius-lg: 22px;
            --radius-md: 16px;
            --radius-sm: 12px;
        }

        html, body, [class*="css"] {
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }

        .stApp {
            background:
                radial-gradient(1200px 520px at 10% 0%, rgba(80, 212, 225, 0.14), transparent 52%),
                radial-gradient(1000px 540px at 100% 0%, rgba(32, 118, 201, 0.10), transparent 42%),
                linear-gradient(180deg, var(--bg-1) 0%, var(--bg-0) 100%);
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
            max-width: 1360px !important;
            padding-top: 1.35rem !important;
            padding-bottom: 4rem !important;
        }

        .topbar {
            display: grid;
            grid-template-columns: minmax(220px, 380px) 1fr auto;
            align-items: center;
            gap: 1.25rem;
            margin-bottom: 1.35rem;
        }

        .brand-wrap {
            display: flex;
            align-items: center;
            min-height: 80px;
        }

        .brand-logo-img {
            width: min(330px, 100%);
            height: auto;
            display: block;
            filter: drop-shadow(0 10px 28px rgba(0,0,0,0.16));
        }

        .brand-fallback {
            font-size: 1.8rem;
            font-weight: 800;
            letter-spacing: -0.03em;
            color: var(--text);
        }

        .topbar-chip {
            justify-self: center;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            min-height: 54px;
            padding: 0 1rem;
            border-radius: 999px;
            border: 1px solid rgba(80, 212, 225, 0.22);
            background: rgba(80, 212, 225, 0.07);
            color: var(--accent-2);
            font-size: 0.92rem;
            font-weight: 600;
            letter-spacing: 0.01em;
            text-align: center;
            white-space: nowrap;
        }

        .topbar-actions {
            justify-self: end;
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.5rem;
            border-radius: 22px;
            background: rgba(255,255,255,0.03);
            border: 1px solid var(--stroke);
            box-shadow: var(--shadow-lg);
        }

        .top-button {
            min-height: 56px;
            padding: 0.9rem 1.2rem;
            border-radius: 16px;
            text-decoration: none !important;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 0.96rem;
            font-weight: 700;
            transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
            border: 1px solid transparent;
        }

        .top-button:hover {
            transform: translateY(-1px);
        }

        .top-button-secondary {
            background: linear-gradient(180deg, rgba(20, 32, 47, 0.98), rgba(13, 23, 36, 0.98));
            border-color: var(--stroke);
            color: var(--text) !important;
        }

        .top-button-secondary:hover {
            border-color: var(--stroke-strong);
        }

        .top-button-primary {
            background: linear-gradient(135deg, rgba(92, 221, 232, 0.96), rgba(72, 209, 225, 0.96));
            border-color: rgba(92, 221, 232, 0.4);
            color: #04131d !important;
            box-shadow: 0 14px 34px rgba(80, 212, 225, 0.18);
        }

        .hero-grid {
            display: grid;
            grid-template-columns: 1.62fr 0.88fr;
            gap: 1.35rem;
            align-items: stretch;
            margin-bottom: 3rem;
        }

        .hero-card,
        .value-card {
            min-height: 760px;
            height: 100%;
            border-radius: 32px;
            border: 1px solid rgba(127, 152, 180, 0.16);
            box-shadow: var(--shadow-xl);
        }

        .hero-card {
            position: relative;
            overflow: hidden;
            background:
                radial-gradient(600px 600px at 82% 22%, rgba(80, 212, 225, 0.18), transparent 48%),
                linear-gradient(180deg, rgba(9, 22, 36, 0.98), rgba(7, 17, 28, 0.98));
            padding: 2.6rem 2.35rem 2.15rem 2.35rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        .hero-card::after {
            content: "";
            position: absolute;
            inset: auto -120px -180px auto;
            width: 420px;
            height: 420px;
            background: radial-gradient(circle, rgba(80, 212, 225, 0.10) 0%, transparent 62%);
            pointer-events: none;
        }

        .hero-inner {
            position: relative;
            z-index: 1;
        }

        .eyebrow {
            color: var(--accent-2);
            text-transform: uppercase;
            letter-spacing: 0.14em;
            font-size: 0.82rem;
            font-weight: 800;
            margin-bottom: 1.15rem;
        }

        .hero-title {
            margin: 0;
            color: var(--text);
            font-size: clamp(3rem, 5.3vw, 5rem);
            line-height: 0.97;
            letter-spacing: -0.055em;
            font-weight: 850;
            max-width: 9ch;
        }

        .hero-title .accent {
            color: var(--accent-2);
        }

        .hero-subtitle {
            margin-top: 1.6rem;
            max-width: 15ch;
            color: var(--soft);
            font-size: 1.17rem;
            line-height: 1.86;
            letter-spacing: -0.01em;
        }

        .hero-note {
            margin-top: 1.15rem;
            color: var(--muted);
            font-size: 1.01rem;
            line-height: 1.75;
            max-width: 15ch;
        }

        .hero-actions {
            margin-top: 1.7rem;
            display: flex;
            flex-wrap: wrap;
            gap: 0.8rem;
        }

        .hero-cta {
            min-height: 58px;
            padding: 0.95rem 1.25rem;
            border-radius: 18px;
            text-decoration: none !important;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 0.98rem;
            font-weight: 750;
            transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
            border: 1px solid transparent;
        }

        .hero-cta:hover {
            transform: translateY(-1px);
        }

        .hero-cta-primary {
            background: linear-gradient(135deg, rgba(92, 221, 232, 0.96), rgba(72, 209, 225, 0.96));
            border-color: rgba(92, 221, 232, 0.4);
            color: #04131d !important;
            box-shadow: 0 16px 36px rgba(80, 212, 225, 0.18);
        }

        .hero-cta-secondary {
            background: rgba(255,255,255,0.035);
            border-color: var(--stroke);
            color: var(--text) !important;
        }

        .hero-cta-secondary:hover {
            border-color: var(--stroke-strong);
        }

        .proof-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.65rem;
            margin-top: 1.4rem;
        }

        .proof-chip {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 40px;
            padding: 0.5rem 0.78rem;
            border-radius: 999px;
            border: 1px solid rgba(127, 152, 180, 0.16);
            background: rgba(255,255,255,0.035);
            color: var(--soft);
            font-size: 0.84rem;
            font-weight: 600;
            letter-spacing: 0.01em;
        }

        .value-card {
            background: linear-gradient(180deg, rgba(12, 24, 38, 0.96), rgba(9, 18, 29, 0.96));
            padding: 1.45rem;
            display: flex;
            flex-direction: column;
        }

        .value-title {
            color: var(--soft);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-size: 0.8rem;
            font-weight: 800;
            margin-bottom: 1rem;
        }

        .metric-stack {
            display: grid;
            gap: 0.95rem;
            height: 100%;
        }

        .metric-card {
            background: linear-gradient(180deg, rgba(28, 39, 54, 0.78), rgba(19, 31, 45, 0.78));
            border: 1px solid rgba(127, 152, 180, 0.16);
            border-radius: 24px;
            padding: 1.35rem 1.25rem;
            min-height: 132px;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }

        .metric-value {
            color: var(--text);
            font-size: 1.28rem;
            font-weight: 800;
            letter-spacing: -0.03em;
            margin-bottom: 0.35rem;
        }

        .metric-label {
            color: var(--muted);
            font-size: 0.98rem;
            line-height: 1.65;
        }

        .section-block {
            margin-top: 3.1rem;
        }

        .section-kicker {
            color: var(--accent-2);
            text-transform: uppercase;
            letter-spacing: 0.14em;
            font-size: 0.8rem;
            font-weight: 800;
            margin-bottom: 0.55rem;
        }

        .section-title {
            color: var(--text);
            font-size: clamp(2rem, 3.3vw, 3rem);
            line-height: 1.06;
            letter-spacing: -0.045em;
            font-weight: 820;
            margin: 0;
        }

        .section-subtitle {
            margin-top: 0.95rem;
            color: var(--muted);
            font-size: 1.04rem;
            line-height: 1.85;
            max-width: 74ch;
        }

        .workflow-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 1rem;
            margin-top: 1.25rem;
        }

        .workflow-card {
            min-height: 250px;
            background: linear-gradient(180deg, rgba(11, 22, 35, 0.98), rgba(8, 18, 29, 0.98));
            border: 1px solid rgba(127, 152, 180, 0.16);
            border-radius: 24px;
            padding: 1.25rem;
            box-shadow: var(--shadow-lg);
        }

        .workflow-number {
            width: 42px;
            height: 42px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            background: rgba(80, 212, 225, 0.10);
            border: 1px solid rgba(80, 212, 225, 0.22);
            color: var(--accent-2);
            font-size: 1rem;
            font-weight: 800;
            margin-bottom: 1rem;
        }

        .workflow-title {
            color: var(--text);
            font-size: 1.02rem;
            font-weight: 760;
            margin-bottom: 0.5rem;
        }

        .workflow-desc {
            color: var(--muted);
            font-size: 0.97rem;
            line-height: 1.72;
        }

        .modules-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 1.2rem;
            margin-top: 1.35rem;
        }

        .module-card {
            min-height: 392px;
            background: linear-gradient(180deg, rgba(11, 22, 35, 0.98), rgba(8, 18, 29, 0.98));
            border: 1px solid rgba(127, 152, 180, 0.16);
            border-radius: 26px;
            padding: 1.28rem;
            box-shadow: var(--shadow-lg);
            text-decoration: none !important;
            display: flex;
            flex-direction: column;
            transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
        }

        .module-card:hover {
            transform: translateY(-3px);
            border-color: rgba(80, 212, 225, 0.26);
            box-shadow: 0 24px 54px rgba(0, 0, 0, 0.32);
        }

        .module-top {
            display: flex;
            align-items: center;
            gap: 0.9rem;
            margin-bottom: 1rem;
        }

        .module-icon {
            width: 50px;
            height: 50px;
            flex: 0 0 50px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 16px;
            background: rgba(80, 212, 225, 0.10);
            border: 1px solid rgba(80, 212, 225, 0.18);
            color: var(--accent-2);
            font-size: 1.35rem;
        }

        .module-eyebrow {
            color: var(--accent-2);
            font-size: 0.79rem;
            font-weight: 750;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .module-title {
            color: var(--text);
            font-size: 1.17rem;
            line-height: 1.28;
            letter-spacing: -0.02em;
            font-weight: 780;
            margin-bottom: 0.6rem;
        }

        .module-desc {
            color: var(--muted);
            font-size: 0.99rem;
            line-height: 1.8;
        }

        .module-spacer {
            flex: 1 1 auto;
            min-height: 0.9rem;
        }

        .module-meta {
            margin-top: 0.5rem;
            padding-top: 0.9rem;
            border-top: 1px solid rgba(127, 152, 180, 0.12);
            color: var(--soft);
            font-size: 0.9rem;
            line-height: 1.8;
        }

        .module-meta strong {
            color: var(--text);
            display: inline-block;
            min-width: 58px;
        }

        .module-cta-wrap {
            margin-top: 1rem;
        }

        .module-cta {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            min-height: 48px;
            padding: 0.75rem 0.95rem;
            border-radius: 14px;
            border: 1px solid rgba(80, 212, 225, 0.22);
            background: rgba(80, 212, 225, 0.08);
            color: var(--accent-2);
            font-size: 0.92rem;
            font-weight: 730;
            letter-spacing: 0.01em;
            text-decoration: none !important;
        }

        .band-card {
            margin-top: 1.5rem;
            background: linear-gradient(135deg, rgba(80, 212, 225, 0.10), rgba(17, 36, 54, 0.62));
            border: 1px solid rgba(80, 212, 225, 0.16);
            border-radius: 30px;
            padding: 1.65rem 1.6rem;
            box-shadow: var(--shadow-lg);
        }

        .band-title {
            color: var(--text);
            font-size: 1.55rem;
            line-height: 1.15;
            letter-spacing: -0.03em;
            font-weight: 800;
            margin-bottom: 0.45rem;
        }

        .band-desc {
            color: var(--muted);
            font-size: 1rem;
            line-height: 1.8;
            max-width: 78ch;
        }

        .footer {
            margin-top: 2.4rem;
            padding-top: 1.7rem;
            border-top: 1px solid rgba(127, 152, 180, 0.12);
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            color: var(--muted);
            font-size: 0.9rem;
        }

        .footer strong {
            color: var(--text);
            font-weight: 700;
        }

        @media (max-width: 1220px) {
            .hero-grid {
                grid-template-columns: 1fr;
            }
            .hero-card,
            .value-card {
                min-height: auto;
            }
            .workflow-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .modules-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .topbar {
                grid-template-columns: 1fr;
            }
            .topbar-chip {
                justify-self: start;
            }
            .topbar-actions {
                justify-self: start;
            }
        }

        @media (max-width: 760px) {
            .block-container {
                padding-top: 1rem !important;
            }
            .workflow-grid,
            .modules-grid {
                grid-template-columns: 1fr;
            }
            .hero-card {
                padding: 1.55rem 1.25rem;
            }
            .value-card {
                padding: 1rem;
            }
            .hero-title {
                max-width: 10ch;
            }
            .hero-subtitle,
            .hero-note {
                max-width: none;
            }
            .topbar-actions {
                width: 100%;
                justify-content: stretch;
            }
            .top-button {
                flex: 1 1 auto;
            }
            .footer {
                flex-direction: column;
                align-items: flex-start;
            }
        }
        </style>
        """
    ),
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 5) TOPBAR
# -------------------------------------------------
st.markdown(
    clean_html(
        f"""
        <div class="topbar">
            <div class="brand-wrap">{logo_markup()}</div>
            <div class="topbar-chip">Customer portal • Rules-first workflows • Signed outputs</div>
            <div class="topbar-actions">
                {top_button("Project Setup", page_url("project"), primary=False)}
                {top_button("QA & Sign-off", page_url("review"), primary=True)}
            </div>
        </div>
        """
    ),
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 6) HERO + VALUE PANEL
# -------------------------------------------------
st.markdown(
    clean_html(
        f"""
        <div class="hero-grid">
            <div class="hero-card">
                <div class="hero-inner">
                    <div class="eyebrow">The Builtly Loop</div>
                    <h1 class="hero-title">From <span class="accent">raw data</span> to signed deliverables.</h1>
                    <div class="hero-subtitle">
                        Builtly is the customer portal for compliance-grade engineering delivery. Upload project inputs,
                        let the platform validate, calculate, apply rule checks, and draft the deliverable - before junior QA
                        and senior sign-off turn it into a consistent, traceable, submission-ready package.
                    </div>
                    <div class="hero-note">
                        Built for building applications, execution, and professional compliance - designed as a production workflow,
                        not a showcase UI.
                    </div>
                </div>
                <div class="hero-inner">
                    <div class="hero-actions">
                        <a href="{html.escape(page_url('project'))}" target="_self" class="hero-cta hero-cta-primary">Open project setup</a>
                        <a href="{html.escape(page_url('review'))}" target="_self" class="hero-cta hero-cta-secondary">Open QA and sign-off</a>
                    </div>
                    <div class="proof-row">
                        <div class="proof-chip">Rules-first</div>
                        <div class="proof-chip">Audit trail</div>
                        <div class="proof-chip">PDF + DOCX output</div>
                        <div class="proof-chip">Digital sign-off</div>
                        <div class="proof-chip">Structured QA workflow</div>
                    </div>
                </div>
            </div>

            <div class="value-card">
                <div class="value-title">Why Builtly?</div>
                <div class="metric-stack">
                    <div class="metric-card">
                        <div class="metric-value">80-90%</div>
                        <div class="metric-label">Reduction in manual drafting and repetitive report production</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">Junior + Senior</div>
                        <div class="metric-label">Human-in-the-loop QA, technical control, and digital sign-off</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">PDF + DOCX</div>
                        <div class="metric-label">Complete report packages with appendices and traceability</div>
                    </div>
                    <div class="metric-card">
                        <div class="metric-value">Full Traceability</div>
                        <div class="metric-label">Inputs, versions, compliance checks, and signatures logged end-to-end</div>
                    </div>
                </div>
            </div>
        </div>
        """
    ),
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 7) WORKFLOW
# -------------------------------------------------
workflow_cards = "".join(
    [
        workflow_card(1, "Input", "Upload PDFs, IFC models, XLSX lab files, drawings, and project-specific data in one place."),
        workflow_card(2, "Validate and analyze", "The platform parses, validates, applies local rule checks, performs calculations, and drafts the deliverable."),
        workflow_card(3, "QA and sign-off", "Junior review, senior technical assessment, and digital sign-off - with version control throughout."),
        workflow_card(4, "Output", "Finalized documentation package in standard formats, ready for municipal submission or execution."),
    ]
)

st.markdown(
    clean_html(
        f"""
        <div class="section-block">
            <div class="section-kicker">Workflow</div>
            <h2 class="section-title">A deterministic delivery engine</h2>
            <div class="section-subtitle">
                The Builtly Loop moves a project from fragmented raw material to structured output through one controlled,
                repeatable production flow.
            </div>
            <div class="workflow-grid">{workflow_cards}</div>
        </div>
        """
    ),
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 8) MODULES
# -------------------------------------------------
modules_html = "".join(
    [
        module_card(
            title="Geo / Env - Ground Conditions",
            eyebrow="Site and mass handling",
            icon="🌍",
            description="Analyze lab files and excavation plans. Classifies masses, proposes disposal logic, and drafts environmental action plans.",
            input_text="XLSX / CSV / PDF + plans",
            output_text="Environmental action plan, logs",
            cta_label="Open Geo & Env",
            href=page_url("geo"),
        ),
        module_card(
            title="Acoustics - Noise & Sound",
            eyebrow="Facade and room performance",
            icon="🔊",
            description="Ingest noise maps and floor plans. Generates facade requirements, window specifications, and mitigation strategies.",
            input_text="Noise map + floor plan",
            output_text="Acoustics report, facade evaluation",
            cta_label="Open Acoustics",
            href=page_url("akustikk"),
        ),
        module_card(
            title="Fire - Safety Strategy",
            eyebrow="Escape and code logic",
            icon="🔥",
            description="Evaluate architectural drawings against building code logic. Generates escape routes, fire cell division, and deviations.",
            input_text="Architectural drawings + class",
            output_text="Fire strategy concept, deviations",
            cta_label="Open Fire Strategy",
            href=page_url("brann"),
        ),
        module_card(
            title="Traffic - Mobility",
            eyebrow="Access and movement",
            icon="🚦",
            description="Traffic generation, parking requirements, access control, and soft mobility planning from project and site inputs.",
            input_text="Site plans, local norms",
            output_text="Traffic memo, mobility plan",
            cta_label="Open Traffic & Mobility",
            href=page_url("trafikk"),
        ),
        module_card(
            title="Structural - Concept",
            eyebrow="Load and system concepts",
            icon="🏢",
            description="Conceptual structural checks, principle dimensioning, and integration with carbon footprint estimations.",
            input_text="Models, load parameters",
            output_text="Concept memo, grid layouts",
            cta_label="Open Structural",
            href=page_url("konstruksjon"),
        ),
        module_card(
            title="ARK - Feasibility Study",
            eyebrow="Early-phase site intelligence",
            icon="📐",
            description="Site screening, utilization analysis, and early-phase decision support before full engineering design.",
            input_text="Site data, zoning plans",
            output_text="Feasibility report, utilization metrics",
            cta_label="Open Feasibility",
            href=page_url("mulighetsstudie"),
        ),
    ]
)

st.markdown(
    clean_html(
        f"""
        <div class="section-block">
            <div class="section-kicker">Modules and roadmap</div>
            <h2 class="section-title">Specialized agents in one platform</h2>
            <div class="section-subtitle">
                Each module has dedicated ingestion logic, discipline-specific rules, and output templates while sharing the same
                portal, validation, QA, and sign-off backbone.
            </div>
            <div class="modules-grid">{modules_html}</div>
            <div class="band-card">
                <div class="band-title">Not just analysis. Actual deliverables.</div>
                <div class="band-desc">
                    Builtly operates as a full-stack delivery system: create a project, upload raw data, review deviations,
                    generate drafts, execute QA, and download the signed documentation package.
                </div>
            </div>
        </div>
        """
    ),
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 9) FOOTER
# -------------------------------------------------
st.markdown(
    clean_html(
        """
        <div class="footer">
            <div><strong>Builtly AS</strong> · Compliance-grade engineering delivery</div>
            <div>© 2026 Builtly. All rights reserved.</div>
        </div>
        """
    ),
    unsafe_allow_html=True,
)
