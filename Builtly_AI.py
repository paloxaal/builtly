import os
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
# 3) CSS
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
        --radius-xl: 28px;
    }

    html, body, [class*="css"] {
        font-family: Inter, ui-sans-serif, system-ui, sans-serif;
    }

    .stApp {
        background: radial-gradient(1100px 500px at 15% -5%, rgba(56,194,201,0.18), transparent 50%),
                    radial-gradient(900px 500px at 100% 0%, rgba(64,170,255,0.12), transparent 45%),
                    linear-gradient(180deg, #071018 0%, #08131d 35%, #071018 100%);
        color: var(--text);
    }

    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    [data-testid="stSidebar"] { background: rgba(7, 16, 24, 0.96); border-right: 1px solid var(--stroke); }
    .block-container { max-width: 1280px !important; padding-top: 2rem !important; padding-bottom: 4rem !important; }

    .hero {
        position: relative; overflow: hidden;
        background: linear-gradient(180deg, rgba(13,27,42,0.96), rgba(8,18,28,0.96));
        border: 1px solid rgba(120,145,170,0.16); border-radius: var(--radius-xl);
        padding: 2.2rem; margin-bottom: 1.25rem;
    }
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

    .section-head { margin-top: 3rem; margin-bottom: 1.5rem; }
    .section-kicker { color: var(--accent-2); text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.74rem; font-weight: 700; margin-bottom: 0.4rem; }
    .section-title { font-size: 1.8rem; font-weight: 700; letter-spacing: -0.03em; color: var(--text); margin: 0; }
    .section-subtitle { margin-top: 0.35rem; color: var(--muted); line-height: 1.75; max-width: 72ch; }

    .loop-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1rem; margin-top: 0.8rem; }
    .loop-card { background: var(--panel-2); border: 1px solid var(--stroke); border-radius: 18px; padding: 1rem; min-height: 160px; }
    .loop-number { width: 34px; height: 34px; display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; background: rgba(56,194,201,0.12); border: 1px solid rgba(56,194,201,0.22); color: var(--accent-2); font-weight: 700; font-size: 0.92rem; margin-bottom: 0.8rem; }
    .loop-title { font-size: 1rem; font-weight: 650; color: var(--text); margin-bottom: 0.45rem; }
    .loop-desc { font-size: 0.92rem; line-height: 1.65; color: var(--muted); }

    .module-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1.5rem; margin-top: 1rem; }
    
    .module-card {
        background: linear-gradient(180deg, rgba(12,25,39,0.96), rgba(8,18,28,0.96));
        border: 1px solid var(--stroke);
        border-radius: 22px;
        padding: 1.8rem;
        display: flex;
        flex-direction: column;
        text-decoration: none !important;
        transition: all 0.3s ease;
        height: 100%; /* Dette sikrer symmetri */
    }
    .module-card:hover {
        border-color: rgba(56,194,201,0.5);
        transform: translateY(-5px);
        box-shadow: 0 15px 35px rgba(0,0,0,0.4);
    }
    
    .module-top { display: flex; align-items: center; gap: 1rem; margin-bottom: 1.2rem; }
    .module-icon { 
        width: 50px; height: 50px; border-radius: 14px; display: inline-flex; 
        align-items: center; justify-content: center; background: rgba(56,194,201,0.1); 
        border: 1px solid rgba(56,194,201,0.18); color: var(--accent-2); font-size: 1.5rem; 
    }
    .module-title { font-size: 1.15rem; font-weight: 700; color: #ffffff !important; margin: 0; }
    
    /* flex-grow: 1 dytter meta-data ned, så boksene blir identiske i høyde */
    .module-desc { font-size: 0.95rem; line-height: 1.6; color: var(--muted) !important; flex-grow: 1; }
    
    .module-meta { 
        font-size: 0.85rem; line-height: 1.7; color: var(--soft) !important; 
        padding-top: 1rem; border-top: 1px solid rgba(120,145,170,0.14); margin-top: 1rem; 
    }
    
    .review-btn {
        display: inline-block; padding: 0.5rem 1rem; background-color: rgba(56,194,201,0.1);
        border: 1px solid rgba(56,194,201,0.3); border-radius: 8px; color: #ffffff !important;
        text-decoration: none !important; font-weight: 600; font-size: 0.9rem; text-align: center;
        transition: all 0.2s;
    }
    .review-btn:hover { background-color: rgba(56,194,201,0.2); border-color: rgba(56,194,201,0.6); }

    .cta-band { margin-top: 3rem; margin-bottom: 1.5rem; background: linear-gradient(135deg, rgba(56,194,201,0.12), rgba(18,49,76,0.28)); border: 1px solid rgba(56,194,201,0.18); border-radius: 24px; padding: 1.4rem; }
    .cta-title { font-size: 1.3rem; font-weight: 700; color: var(--text); margin-bottom: 0.3rem; }
    .cta-desc { color: var(--muted); line-height: 1.7; max-width: 80ch; }

    @media (max-width: 1000px) { .module-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .loop-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    @media (max-width: 600px) { .module-grid { grid-template-columns: 1fr; } .loop-grid { grid-template-columns: 1fr; } }
</style>
""", unsafe_allow_html=True)

# -------------------------------------------------
# 4) TOP / BRAND
# -------------------------------------------------
st.write("")
top_left, top_right = st.columns([0.85, 0.15])
with top_left:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=280)
with top_right:
    st.markdown('<div style="margin-top: 10px;"><a href="Review" target="_self" class="review-btn">✅ QA & Sign-off</a></div>', unsafe_allow_html=True)
st.write("")

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
A self-serve portal that turns raw project inputs into signed, submission-ready deliverables. The platform handles complex calculations, compliance checks, and drafting — before human QA ensures fast, consistent, and traceable delivery.
</div>
<div class="hero-note">Designed for engineering analysis, complex calculations, and professional compliance. Builtly is a regulated production system, not "just a chat UI".</div>
</div>
""", unsafe_allow_html=True)

with right:
    st.markdown("""
<div class="hero-panel">
<div class="panel-title">Why Builtly?</div>
<div class="mini-stat">
<div class="mini-stat-value">80–90%</div>
<div class="mini-stat-label">Reduction in manual drafting & calculation time</div>
</div>
<div class="mini-stat">
<div class="mini-stat-value">Junior + Senior</div>
<div class="mini-stat-label">Human-in-the-loop QA and digital sign-off</div>
</div>
<div class="mini-stat" style="margin-bottom:0;">
<div class="mini-stat-value">Immutable Log</div>
<div class="mini-stat-label">Full audit trail of versions, code references, and signatures</div>
</div>
</div>
""", unsafe_allow_html=True)

# -------------------------------------------------
# 6) BUILTLY LOOP
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
<div class="loop-desc">Upload PDFs, IFC models, XLSX lab files, and architectural drawings.</div>
</div>
<div class="loop-card">
<div class="loop-number">2</div>
<div class="loop-title">Analyze & Calculate</div>
<div class="loop-desc">The platform executes parsing, structural/environmental calculations, compliance checks, and drafts the report.</div>
</div>
<div class="loop-card">
<div class="loop-number">3</div>
<div class="loop-title">QA & Sign-off</div>
<div class="loop-desc">Junior assessment, senior technical review, and digital signature.</div>
</div>
<div class="loop-card">
<div class="loop-number">4</div>
<div class="loop-title">Output</div>
<div class="loop-desc">Finalized, submission-ready document package ready for municipal or site execution.</div>
</div>
</div>
""", unsafe_allow_html=True)

# -------------------------------------------------
# 7) MODULES (Ingen innrykk, 100% trygt for Markdown)
# -------------------------------------------------
st.markdown("""
<div class="section-head" style="margin-top: 4rem;">
<div class="section-kicker">Core Disciplines</div>
<h2 class="section-title">Engineering Modules</h2>
<div class="section-subtitle">
Select a specialized agent below. Each module features dedicated data ingestion and local regulatory frameworks.
</div>
</div>
""", unsafe_allow_html=True)

# Absolutt ingen mellomrom foran HTML-taggene her!
st.markdown("""
<div class="module-grid">
<a href="Geo" target="_self" class="module-card">
<div class="module-top">
<div class="module-icon">🌍</div>
<div class="module-title">GEO / ENV — Ground Conditions</div>
</div>
<div class="module-desc">Analyze lab files and excavation plans. Calculates and classifies soil, producing disposal proposals and action plans.</div>
<div class="module-meta">
<strong>Input:</strong> XLSX / CSV / PDF + plans<br/>
<strong>Output:</strong> Environmental action plan
</div>
</a>
<a href="Akustikk" target="_self" class="module-card">
<div class="module-top">
<div class="module-icon">🔊</div>
<div class="module-title">ACOUSTICS — Noise & Sound</div>
</div>
<div class="module-desc">Ingest noise maps and floor plans. Generates facade requirements, window specifications, and mitigation strategies.</div>
<div class="module-meta">
<strong>Input:</strong> Noise map + floor plan<br/>
<strong>Output:</strong> Acoustics report
</div>
</a>
<a href="Brannkonsept" target="_self" class="module-card">
<div class="module-top">
<div class="module-icon">🔥</div>
<div class="module-title">FIRE — Safety Strategy</div>
</div>
<div class="module-desc">Evaluate architectural drawings against building codes. Generates escape routes, fire cell division, and strategy.</div>
<div class="module-meta">
<strong>Input:</strong> Architectural drawings<br/>
<strong>Output:</strong> Fire strategy concept
</div>
</a>
<a href="Konstruksjon" target="_self" class="module-card">
<div class="module-top">
<div class="module-icon">🏢</div>
<div class="module-title">STRUC — Structural Concept</div>
</div>
<div class="module-desc">Conceptual structural calculations, principle dimensioning, load evaluations, and carbon footprint estimations.</div>
<div class="module-meta">
<strong>Input:</strong> Models, load parameters<br/>
<strong>Output:</strong> Concept memo, grid layouts
</div>
</a>
<a href="Mulighetsstudie" target="_self" class="module-card">
<div class="module-top">
<div class="module-icon">📐</div>
<div class="module-title">ARK — Feasibility Study</div>
</div>
<div class="module-desc">Site screening, volume calculations, and early-phase decision support prior to full engineering design.</div>
<div class="module-meta">
<strong>Input:</strong> Site data, zoning plans<br/>
<strong>Output:</strong> Feasibility report
</div>
</a>
</div>
""", unsafe_allow_html=True)

# -------------------------------------------------
# 8) CTA BAND
# -------------------------------------------------
st.markdown("""
<div class="cta-band">
<div class="cta-title">Become the global standard for compliance-grade engineering deliverables.</div>
<div class="cta-desc">
A platform where customers self-serve inputs and professionals certify outputs. Create a project, upload raw data, execute QA, and download the signed documentation package.
</div>
</div>
""", unsafe_allow_html=True)
