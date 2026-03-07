import os
import base64
import streamlit as st

# --- 1. GRUNNINNSTILLINGER ---
st.set_page_config(
    page_title="Builtly | Engineering Portal",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Laster inn logoen din i koden (så den kan gjøres så stor vi vil)
logo_html = '<h1 style="margin:0; color:#f8fafc; font-size:2.5rem;">Builtly</h1>'
if os.path.exists("logo.png"):
    try:
        with open("logo.png", "rb") as img_file:
            b64_logo = base64.b64encode(img_file.read()).decode()
        logo_html = f'<img src="data:image/png;base64,{b64_logo}" alt="Builtly Logo">'
    except Exception:
        pass

# --- 2. SKUDDSIKKER PREMIUM CSS ---
css = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
    
    /* Tvinger mørkt tema globalt */
    .stApp { background-color: #090e17 !important; font-family: 'Inter', sans-serif; }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    [data-testid="stSidebar"] { background-color: #0d131f !important; border-right: 1px solid #1e293b !important; }
    .block-container { max-width: 1300px !important; padding-top: 2rem !important; padding-bottom: 5rem !important; }

    /* Farger på tekst som Streamlit ikke får røre */
    h1, h2, h3, h4, div, span { color: #f8fafc !important; }
    p { color: #94a3b8 !important; }

    /* --- RESPONSIV TOPBAR --- */
    .topbar { 
        display: flex; justify-content: space-between; align-items: center; 
        margin-bottom: 3rem; padding-bottom: 1.5rem; border-bottom: 1px solid #1e293b; 
    }
    .topbar img { height: 75px; object-fit: contain; }
    
    .topbar-buttons { display: flex; gap: 1rem; align-items: center; }
    .btn { 
        padding: 10px 20px; border-radius: 8px; text-decoration: none !important; 
        font-weight: 600; font-size: 0.95rem; transition: 0.2s; 
    }
    .btn-outline { border: 1px solid #38bdf8; color: #38bdf8 !important; background: transparent; }
    .btn-outline:hover { background: rgba(56, 189, 248, 0.1); }
    .btn-primary { background: #38bdf8; color: #090e17 !important; border: 1px solid #38bdf8; }
    .btn-primary:hover { background: #0ea5e9; border-color: #0ea5e9; }

    /* --- HERO SEKSJON --- */
    .hero { 
        background: linear-gradient(145deg, #111827 0%, #0f172a 100%); 
        border: 1px solid #1e293b; border-radius: 20px; padding: 3rem; margin-bottom: 3rem; 
    }
    .hero-kicker { color: #38bdf8 !important; text-transform: uppercase; letter-spacing: 2px; font-size: 0.8rem; font-weight: 700; margin-bottom: 1rem; display: block;}
    .hero h1 { font-size: 3rem; font-weight: 800; margin-bottom: 1rem; letter-spacing: -0.03em; }
    .hero p { font-size: 1.1rem; line-height: 1.7; max-width: 800px; color: #cbd5e1 !important;}

    /* --- MODULE GRID (Perfekt symmetri) --- */
    .section-title { font-size: 1.8rem; font-weight: 700; margin-bottom: 2rem; }
    .module-grid { 
        display: grid; 
        grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); 
        gap: 1.5rem; margin-bottom: 4rem; 
    }
    
    /* Selve kortene - klikkbare flater */
    .module-card { 
        background: #111827; border: 1px solid #1e293b; border-radius: 16px; 
        padding: 2rem; text-decoration: none !important; display: flex; 
        flex-direction: column; transition: all 0.3s ease; height: 100%;
    }
    .module-card:hover { 
        transform: translateY(-5px); border-color: #38bdf8; 
        box-shadow: 0 10px 40px -10px rgba(56, 189, 248, 0.15); background: #131c2c; 
    }
    
    .mod-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1.5rem; }
    .mod-icon { 
        font-size: 2rem; background: #1e293b; width: 54px; height: 54px; 
        display: flex; align-items: center; justify-content: center; 
        border-radius: 12px; border: 1px solid #334155; 
    }
    .mod-badge { 
        background: rgba(56, 189, 248, 0.1); color: #38bdf8 !important; padding: 4px 12px; 
        border-radius: 20px; font-size: 0.75rem; font-weight: 600; border: 1px solid rgba(56, 189, 248, 0.2); 
    }
    
    .mod-title { font-size: 1.25rem; font-weight: 700; margin-bottom: 0.5rem; }
    .mod-desc { font-size: 0.95rem; line-height: 1.6; flex-grow: 1; margin-bottom: 1.5rem; color: #94a3b8 !important;}
    
    .mod-meta { font-size: 0.85rem; border-top: 1px solid #1e293b; padding-top: 1rem; color: #64748b !important;}
    .mod-meta strong { color: #cbd5e1 !important; font-weight: 600;}

    /* --- FOOTER --- */
    .footer { text-align: center; margin-top: 4rem; padding-top: 3rem; border-top: 1px solid #1e293b; }
    .footer h3 { font-size: 1.2rem; margin-bottom: 0.5rem; color: #e2e8f0 !important; font-weight: 700;}
    .footer p { font-size: 0.9rem; color: #64748b !important; }

    /* Mobiljusteringer */
    @media (max-width: 768px) {
        .topbar { flex-direction: column; gap: 1.5rem; align-items: flex-start; }
        .topbar img { height: 50px; }
        .hero { padding: 2rem 1.5rem; }
        .hero h1 { font-size: 2.2rem; }
    }
</style>
"""
st.markdown(css, unsafe_allow_html=True)

# --- 3. HTML INNHOLD (Garanterer designet) ---
html_content = f"""
<div class="topbar">
    {logo_html}
    <div class="topbar-buttons">
        <a href="Project" target="_self" class="btn btn-outline">⚙️ Project Setup</a>
        <a href="Review" target="_self" class="btn btn-primary">✅ QA & Sign-off</a>
    </div>
</div>

<div class="hero">
    <span class="hero-kicker">The Builtly Loop</span>
    <h1>From raw data to signed deliverables.</h1>
    <p>
        Builtly is the client portal for AI-assisted engineering and documentation. 
        Upload raw data, let the platform handle complex calculations and compliance checks, 
        and generate draft reports — before human QA ensures fast, consistent, and traceable delivery.
    </p>
</div>

<h2 class="section-title">Active Engineering Modules</h2>

<div class="module-grid">
    
    <a href="Mulighetsstudie" target="_self" class="module-card">
        <div class="mod-header">
            <div class="mod-icon">📐</div>
            <div class="mod-badge">Fase 0</div>
        </div>
        <div class="mod-title">ARK — Mulighetsstudie</div>
        <div class="mod-desc">Site screening, volume analysis, and early-phase decision support prior to full engineering design.</div>
        <div class="mod-meta"><strong>Input:</strong> Site data, zoning plans<br><strong>Output:</strong> Feasibility report</div>
    </a>

    <a href="Geo" target="_self" class="module-card">
        <div class="mod-header">
            <div class="mod-icon">🌍</div>
            <div class="mod-badge">Priority</div>
        </div>
        <div class="mod-title">GEO / ENV — Ground Conditions</div>
        <div class="mod-desc">Analyze lab files and excavation plans. Outputs soil classification, disposal proposals, and action plans.</div>
        <div class="mod-meta"><strong>Input:</strong> XLSX Lab data, plans<br><strong>Output:</strong> Environmental action plan</div>
    </a>

    <a href="Konstruksjon" target="_self" class="module-card">
        <div class="mod-header">
            <div class="mod-icon">🏢</div>
            <div class="mod-badge">Eurocode</div>
        </div>
        <div class="mod-title">STRUC — Structural Concept</div>
        <div class="mod-desc">Conceptual structural checks, principle dimensioning, and integration with carbon footprint estimations.</div>
        <div class="mod-meta"><strong>Input:</strong> Models, loads<br><strong>Output:</strong> Concept memo, grid layouts</div>
    </a>

    <a href="Brannkonsept" target="_self" class="module-card">
        <div class="mod-header">
            <div class="mod-icon">🔥</div>
            <div class="mod-badge">TEK17</div>
        </div>
        <div class="mod-title">FIRE — Safety Strategy</div>
        <div class="mod-desc">Evaluate architectural drawings against building codes. Generates escape routes, fire cell division, and strategy.</div>
        <div class="mod-meta"><strong>Input:</strong> Architectural drawings<br><strong>Output:</strong> Fire strategy concept</div>
    </a>

    <a href="Akustikk" target="_self" class="module-card">
        <div class="mod-header">
            <div class="mod-icon">🔊</div>
            <div class="mod-badge">NS 8175</div>
        </div>
        <div class="mod-title">ACOUSTICS — Noise & Sound</div>
        <div class="mod-desc">Ingest noise maps and floor plans. Generates facade requirements, window specifications, and mitigation strategies.</div>
        <div class="mod-meta"><strong>Input:</strong> Noise map, floor plan<br><strong>Output:</strong> Acoustics report</div>
    </a>

    <a href="trafikk" target="_self" class="module-card">
        <div class="mod-header">
            <div class="mod-icon">🚦</div>
            <div class="mod-badge">N100</div>
        </div>
        <div class="mod-title">TRAFFIC — Mobility</div>
        <div class="mod-desc">Traffic generation (ÅDT), parking requirements, access control, and soft mobility planning.</div>
        <div class="mod-meta"><strong>Input:</strong> Site plans, local norms<br><strong>Output:</strong> Traffic memo</div>
    </a>

</div>

<div class="footer">
    <h3>Builtly AS</h3>
    <p>Building the future of compliance-grade engineering.</p>
    <p style="margin-top: 10px; font-size: 0.8rem; opacity: 0.5;">© 2026 Builtly. All rights reserved.</p>
</div>
"""
st.markdown(html_content, unsafe_allow_html=True)
