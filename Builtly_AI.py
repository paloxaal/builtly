import os
import base64
from pathlib import Path
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
    "trafikk": "pages/Trafikk.py",
    "review": "pages/Review.py",
}

def page_exists(page_path: str) -> bool:
    return Path(page_path).exists()

def nav_link(page_key: str, label: str, icon: str = None, help_text: str = None):
    page_path = PAGES.get(page_key)
    if page_path and page_exists(page_path):
        st.page_link(page_path, label=label, icon=icon, help=help_text)
    else:
        st.markdown(
            f"""
            <div class="disabled-link">
                <span>{label}</span>
                <span class="disabled-tag">In Development</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

# -------------------------------------------------
# 3) CSS (Inkludert responsiv Flexbox Header)
# -------------------------------------------------
st.markdown("""
<style>
    :root {
        --bg: #06111a;
        --panel: rgba(10, 22, 35, 0.78);
        --panel-2: rgba(13, 27, 42, 0.9);
        --stroke: rgba(120, 145, 170, 0.18);
        --text: #f5f7fb;
        --muted: #9fb0c3;
        --soft: #c8d3df;
        --accent: #38c2c9;
        --accent-2: #78dce1;
        --accent-3: #112c3f;
        --warn: #f4bf4f;
        --shadow: 0 20px 80px rgba(0,0,0,0.35);
        --radius-xl: 28px;
    }

    html, body, [class*="css"] {
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
    }

    .stApp {
        background: radial-gradient(1100px 500px at 15% -5%, rgba(56,194,201,0.18), transparent 50%),
                    radial-gradient(900px 500px at 100% 0%, rgba(64,170,255,0.12), transparent 45%),
                    linear-gradient(180deg, #071018 0%, #08131d 35%, #071018 100%);
        color: var(--text);
    }

    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    [data-testid="stSidebar"] { background: rgba(7, 16, 24, 0.96); border-right: 1px solid var(--stroke); }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }

    /* NY RESPONSIV HEADER */
    .custom-topbar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 2.5rem;
        padding-bottom: 1rem;
        border-bottom: 1px solid var(--stroke);
        width: 100%;
    }
    .topbar-left {
        display: flex;
        align-items: center;
        gap: 1.5rem;
    }
    .brand-kicker {
        display: inline-flex; align-items: center; padding: 0.45rem 0.8rem;
        border: 1px solid rgba(56,194,201,0.24); background: rgba(56,194,201,0.08);
        border-radius: 999px; font-size: 0.82rem; color: var(--accent-2); letter-spacing: 0.02em; margin: 0;
    }
    .qa-btn {
        background-color: rgba(56,194,201,0.1);
        border: 1px solid rgba(56,194,201,0.3);
        border-radius: 8px;
        padding: 8px 16px;
        color: var(--text) !important;
        text-decoration: none !important;
        font-size: 0.95rem;
        font-weight: 600;
        transition: all 0.2s;
        white-space: nowrap;
        display: inline-flex;
        align-items: center;
        gap: 6px;
    }
    .qa-btn:hover {
        background-color: rgba(56,194,201,0.2);
        border-color: rgba(56,194,201,0.6);
        color: #ffffff !important;
    }

    /* MOBIL-TILPASNINGER */
    @media (max-width: 768px) {
        .hidden-mobile { display: none !important; } 
        .custom-topbar { margin-bottom: 1.5rem; padding-bottom: 0.8rem; }
        .hero-title { font-size: 2.5rem !important; }
        .trust-grid, .loop-grid, .module-grid { grid-template-columns: 1fr !important; }
    }

    /* Resten av CSS */
    .hero { position: relative; overflow: hidden; background: linear-gradient(180deg, rgba(13,27,42,0.96), rgba(8,18,28,0.96)); border: 1px solid rgba(120,145,170,0.16); border-radius: var(--radius-xl); padding: 2.2rem; box-shadow: var(--shadow); margin-bottom: 1.25rem; }
    .hero::before { content: ""; position: absolute; inset: -80px -120px auto auto; width: 420px; height: 420px; background: radial-gradient(circle, rgba(56,194,201,0.16) 0%, transparent 62%); pointer-events: none; }
    .eyebrow { color: var(--accent-2); text-transform: uppercase; letter-spacing: 0.14em; font-size: 0.78rem; font-weight: 700; margin-bottom: 1rem; }
    .hero-title { font-size: clamp(2.5rem, 5vw, 4.2rem); line-height: 1.05; letter-spacing: -0.04em; font-weight: 800; margin: 0; color: var(--text); max-width: 14ch; }
    .hero-title .accent { color: var(--accent-2); }
    .hero-subtitle { margin-top: 1.2rem; max-width: 60ch; font-size: 1.08rem; line-height: 1.8; color: var(--soft); }
    .hero-note { margin-top: 1rem; font-size: 0.95rem; color: var(--muted); }

    .hero-panel { background: rgba(255,255,255,0.03); border: 1px solid var(--stroke); border-radius: 22px; padding: 1.25rem; height: 100%; }
    .panel-title { font-size: 0.86rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 0.85rem; }
    .mini-stat { background: rgba(255,255,255,0.03); border: 1px solid var(--stroke); border-radius: 16px; padding: 0.95rem 1rem; margin-bottom: 0.75rem; }
    .mini-stat-value { font-size: 1.35rem; font-weight: 700; color: var(--text); line-height: 1.1; }
    .mini-stat-label { margin-top: 0.25rem; color: var(--muted); font-size: 0.88rem; line-height: 1.5; }

    .section-head { margin-top: 2rem; margin-bottom: 1rem; }
    .section-kicker { color: var(--accent-2); text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.74rem; font-weight: 700; margin-bottom: 0.4rem; }
    .section-title { font-size: 1.8rem; font-weight: 700; letter-spacing: -0.03em; color: var(--text); margin: 0; }
    .section-subtitle { margin-top: 0.35rem; color: var(--muted); line-height: 1.75; max-width: 72ch; }

    .trust-grid, .loop-grid, .module-grid { display: grid; gap: 1rem; }
    .trust-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 0.75rem; margin-bottom: 0.6rem; }
    .trust-card { background: var(--panel); border: 1px solid var(--stroke); border-radius: 18px; padding: 1rem; min-height: 132px; }
    .trust-title { font-size: 1rem; font-weight: 650; color: var(--text); margin-bottom: 0.45rem; }
    .trust-desc { font-size: 0.92rem; line-height: 1.65; color: var(--muted); }

    .loop-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 0.8rem; }
    .loop-card { background: var(--panel-2); border: 1px solid var(--stroke); border-radius: 18px; padding: 1rem; min-height: 160px; position: relative; }
    .loop-number { width: 34px; height: 34px; display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; background: rgba(56,194,201,0.12); border: 1px solid rgba(56,194,201,0.22); color: var(--accent-2); font-weight: 700; font-size: 0.92rem; margin-bottom: 0.8rem; }
    .loop-title { font-size: 1rem; font-weight: 650; color: var(--text); margin-bottom: 0.45rem; }
    .loop-desc { font-size: 0.92rem; line-height: 1.65; color: var(--muted); }

    .module-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 0.8rem; }
    .module-card { background: linear-gradient(180deg, rgba(12,25,39,0.96), rgba(8,18,28,0.96)); border: 1px solid var(--stroke); border-radius: 22px; padding: 1.15rem; min-height: 270px; box-shadow: 0 12px 38px rgba(0,0,0,0.18); }
    .module-top { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; margin-bottom: 0.85rem; }
    .module-badge { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.32rem 0.6rem; border-radius: 999px; border: 1px solid rgba(120,145,170,0.18); background: rgba(255,255,255,0.03); color: var(--muted); font-size: 0.75rem; font-weight: 600; }
    .module-icon { width: 44px; height: 44px; border-radius: 14px; display: inline-flex; align-items: center; justify-content: center; background: rgba(56,194,201,0.1); border: 1px solid rgba(56,194,201,0.18); color: var(--accent-2); font-size: 1.3rem; }
    .module-title { font-size: 1.08rem; font-weight: 700; color: var(--text); margin-bottom: 0.45rem; }
    .module-desc { font-size: 0.93rem; line-height: 1.7; color: var(--muted); margin-bottom: 0.9rem; }
    .module-meta { font-size: 0.85rem; line-height: 1.7; color: var(--soft); padding-top: 0.75rem; border-top: 1px solid rgba(120,145,170,0.14); }

    .cta-band { margin-top: 3rem; margin-bottom: 1.5rem; background: linear-gradient(135deg, rgba(56,194,201,0.12), rgba(18,49,76,0.28)); border: 1px solid rgba(56,194,201,0.18); border-radius: 24px; padding: 1.4rem; }
    .cta-title { font-size: 1.3rem; font-weight: 700; color: var(--text); margin-bottom: 0.3rem; }
    .cta-desc { color: var(--muted); line-height: 1.7; max-width: 70ch; }

    .disabled-link { width: 100%; margin-top: 0.45rem; display: flex; align-items: center; justify-content: space-between; border: 1px dashed rgba(120,145,170,0.22); border-radius: 12px; padding: 0.8rem 0.95rem; color: var(--muted); font-size: 0.92rem; background: rgba(255,255,255,0.02); }
    .disabled-tag { font-size: 0.75rem; color: var(--warn); border: 1px solid var(--warn); padding: 2px 6px; border-radius: 4px;}

    [data-testid="stPageLink-NavLink"] { background-color: rgba(56,194,201,0.1); border: 1px solid rgba(56,194,201,0.3); border-radius: 8px; padding: 8px 12px; transition: all 0.2s; margin-top: 8px; }
    [data-testid="stPageLink-NavLink"]:hover { background-color: rgba(56,194,201,0.2); border-color: rgba(56,194,201,0.6); }

    @media (max-width: 1100px) {
        .trust-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .loop-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .module-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
</style>
""", unsafe_allow_html=True)

# -------------------------------------------------
# 4) LÅST RESPONSIV HEADER (Løser mobil-problemet!)
# -------------------------------------------------
logo_html = '<h2 style="margin:0; color:white;">Builtly</h2>'
if os.path.exists("logo.png"):
    try:
        with open("logo.png", "rb") as img_file:
            b64_logo = base64.b64encode(img_file.read()).decode()
        logo_html = f'<img src="data:image/png;base64,{b64_logo}" style="height: 52px; object-fit: contain;">'
    except Exception:
        pass

st.markdown(f"""
<div class="custom-topbar">
    <div class="topbar-left">
        {logo_html}
        <div class="brand-kicker hidden-mobile">AI-Assisted Engineering · Compliance-Grade</div>
    </div>
    <a href="Review" target="_self" class="qa-btn">✅ QA & Sign-off</a>
</div>
""", unsafe_allow_html=True)

# -------------------------------------------------
# 5) HERO
# -------------------------------------------------
left, right = st.columns([1.35, 0.8], gap="large")

with left:
    st.markdown("""
<div class="hero">
    <div class="eyebrow">The Builtly Loop</div>
    <h1 class="hero-title">From <span class="accent">raw data</span> to signed deliverables.</h1>
    <div class="hero-subtitle">
        Builtly is the client portal for AI-assisted engineering and documentation. 
        Upload raw data, let the platform handle analysis, compliance checks, and drafting — before junior QA and senior sign-off ensure fast, consistent, and traceable delivery.
    </div>
    <div class="hero-note">Designed for building applications, execution, and professional compliance.</div>
</div>
""", unsafe_allow_html=True)

with right:
    st.markdown("""
<div class="hero-panel">
    <div class="panel-title">Why Builtly?</div>
    <div class="mini-stat">
        <div class="mini-stat-value">80–90%</div>
        <div class="mini-stat-label">Reduction in manual drafting time per report</div>
    </div>
    <div class="mini-stat">
        <div class="mini-stat-value">Junior + Senior</div>
        <div class="mini-stat-label">Human-in-the-loop QA and digital sign-off</div>
    </div>
    <div class="mini-stat">
        <div class="mini-stat-value">PDF + DOCX</div>
        <div class="mini-stat-label">Complete report packages with traceability</div>
    </div>
    <div class="mini-stat" style="margin-bottom:0;">
        <div class="mini-stat-value">Audit Trail</div>
        <div class="mini-stat-label">Versions, inputs, compliance rules, and signatures logged</div>
    </div>
</div>
""", unsafe_allow_html=True)

# -------------------------------------------------
# 6) TRUST SECTION
# -------------------------------------------------
st.markdown("""
<div class="section-head">
    <div class="section-kicker">Core Value Proposition</div>
    <h2 class="section-title">Portal First. Modules Attached.</h2>
    <div class="section-subtitle">
        Builtly provides a secure, traceable portal for uploads, validation, AI processing, QA, and signed delivery.
    </div>
</div>
<div class="trust-grid">
    <div class="trust-card">
        <div class="trust-title">Client Portal</div>
        <div class="trust-desc">Project creation, data ingestion, deficiency lists, document generation, and audit trails in a single workflow.</div>
    </div>
    <div class="trust-card">
        <div class="trust-title">Rules-First AI</div>
        <div class="trust-desc">AI operates within strict guardrails, combined with explicit regulatory checkpoints and standard templates.</div>
    </div>
    <div class="trust-card">
        <div class="trust-title">QA & Sign-off</div>
        <div class="trust-desc">Junior engineers validate input plausibility. Senior engineers provide final review and signature.</div>
    </div>
    <div class="trust-card">
        <div class="trust-title">Scalable Delivery</div>
        <div class="trust-desc">Deploy new engineering disciplines globally without altering the core platform infrastructure.</div>
    </div>
</div>
""", unsafe_allow_html=True)

# -------------------------------------------------
# 7) BUILTLY LOOP
# -------------------------------------------------
st.markdown("""
<div class="section-head">
    <div class="section-kicker">Workflow</div>
    <h2 class="section-title">The Builtly Loop</h2>
    <div class="section-subtitle">
        A deterministic four-step process taking you from fragmented project data to a fully compliant engineering package.
    </div>
</div>
<div class="loop-grid">
    <div class="loop-card">
        <div class="loop-number">1</div>
        <div class="loop-title">Input</div>
        <div class="loop-desc">Upload PDFs, IFC models, XLSX lab files, environmental data, and architectural drawings.</div>
    </div>
    <div class="loop-card">
        <div class="loop-number">2</div>
        <div class="loop-title">Analyze</div>
        <div class="loop-desc">The platform executes parsing, validation, local compliance checks, and drafts the report.</div>
    </div>
    <div class="loop-card">
        <div class="loop-number">3</div>
        <div class="loop-title">QA & Sign-off</div>
        <div class="loop-desc">Junior assessment, senior technical review, and digital signature — with full version control.</div>
    </div>
    <div class="loop-card">
        <div class="loop-number">4</div>
        <div class="loop-title">Output</div>
        <div class="loop-desc">Finalized document package in standard formats, ready for municipal submission or execution.</div>
    </div>
</div>
""", unsafe_allow_html=True)

# -------------------------------------------------
# 8) MODULES
# -------------------------------------------------
st.markdown("""
<div class="section-head">
    <div class="section-kicker">Modules & Roadmap</div>
    <h2 class="section-title">Specialized Agents in One Platform</h2>
</div>
""", unsafe_allow_html=True)

module_cols = st.columns(3, gap="large")

with module_cols[0]:
    st.markdown("""
<div class="module-card">
    <div class="module-top">
        <div class="module-icon">🌍</div>
        <div class="module-badge">Phase 1 · Priority</div>
    </div>
    <div class="module-title">GEO / ENV — Ground Conditions</div>
    <div class="module-desc">Analyze lab files and excavation plans. Outputs soil classification, disposal proposals, and action plans.</div>
    <div class="module-meta">
        <strong>Input:</strong> XLSX / CSV / PDF + plans<br/>
        <strong>Output:</strong> Environmental action plan, logs
    </div>
</div>
""", unsafe_allow_html=True)
    nav_link("geo", "Open Geo & Env", icon="🌍")

with module_cols[1]:
    st.markdown("""
<div class="module-card">
    <div class="module-top">
        <div class="module-icon">🔊</div>
        <div class="module-badge">Phase 2</div>
    </div>
    <div class="module-title">ACOUSTICS — Noise & Sound</div>
    <div class="module-desc">Ingest noise maps and floor plans. Generates facade requirements, window specifications, and mitigation strategies.</div>
    <div class="module-meta">
        <strong>Input:</strong> Noise map + floor plan<br/>
        <strong>Output:</strong> Acoustics report, facade evaluation
    </div>
</div>
""", unsafe_allow_html=True)
    nav_link("akustikk", "Open Acoustics", icon="🔊")

with module_cols[2]:
    st.markdown("""
<div class="module-card">
    <div class="module-top">
        <div class="module-icon">🔥</div>
        <div class="module-badge">Phase 2</div>
    </div>
    <div class="module-title">FIRE — Safety Strategy</div>
    <div class="module-desc">Evaluate architectural drawings against building codes. Generates escape routes, fire cell division, and strategy.</div>
    <div class="module-meta">
        <strong>Input:</strong> Architectural drawings + class<br/>
        <strong>Output:</strong> Fire strategy concept, deviations
    </div>
</div>
""", unsafe_allow_html=True)
    nav_link("brann", "Open Fire Strategy", icon="🔥")

# -------------------------------------------------
# Her legger vi inn TRAFIKK som det 6. kortet!
# Byttet til 3 kolonner for perfekt symmetri på rad 2.
# -------------------------------------------------
module_cols_2 = st.columns(3, gap="large")

with module_cols_2[0]:
    st.markdown("""
<div class="module-card">
    <div class="module-top">
        <div class="module-icon">📐</div>
        <div class="module-badge">Early Phase</div>
    </div>
    <div class="module-title">ARK — Feasibility Study</div>
    <div class="module-desc">Site screening, volume analysis, and early-phase decision support prior to full engineering design.</div>
    <div class="module-meta">
        <strong>Input:</strong> Site data, zoning plans<br/>
        <strong>Output:</strong> Feasibility report, utilization metrics
    </div>
</div>
""", unsafe_allow_html=True)
    nav_link("mulighetsstudie", "Open Feasibility", icon="📐")

with module_cols_2[1]:
    st.markdown("""
<div class="module-card">
    <div class="module-top">
        <div class="module-icon">🏢</div>
        <div class="module-badge">Roadmap</div>
    </div>
    <div class="module-title">STRUC — Structural Concept</div>
    <div class="module-desc">Conceptual structural checks, principle dimensioning, and integration with carbon footprint estimations.</div>
    <div class="module-meta">
        <strong>Input:</strong> Models, load parameters<br/>
        <strong>Output:</strong> Concept memo, grid layouts
    </div>
</div>
""", unsafe_allow_html=True)
    nav_link("konstruksjon", "Open Structural", icon="🏢")

with module_cols_2[2]:
    st.markdown("""
<div class="module-card">
    <div class="module-top">
        <div class="module-icon">🚦</div>
        <div class="module-badge">Roadmap</div>
    </div>
    <div class="module-title">TRAFFIC — Mobility</div>
    <div class="module-desc">Traffic generation (ÅDT), parking requirements, access control (N100), and soft mobility planning.</div>
    <div class="module-meta">
        <strong>Input:</strong> Site plans, local norms<br/>
        <strong>Output:</strong> Traffic memo, mobility plan
    </div>
</div>
""", unsafe_allow_html=True)
    nav_link("trafikk", "Open Traffic & Mobility", icon="🚦")

# -------------------------------------------------
# 9) CTA BAND
# -------------------------------------------------
st.markdown("""
<div class="cta-band">
    <div class="cta-title">Not just analysis. Actual deliverables.</div>
    <div class="cta-desc">
        Builtly operates as a full-stack delivery system: create a project, upload raw data, review deviations, generate drafts, execute QA, and download the signed documentation package.
    </div>
</div>
""", unsafe_allow_html=True)
