import streamlit as st
import os

# --- 1. GRUNNINNSTILLINGER ---
st.set_page_config(page_title="Builtly Portal | Enterprise", page_icon="🏗️", layout="wide", initial_sidebar_state="expanded")

# --- 2. NEON / DARK MODE CSS (Forbedret over ChatGPT) ---
st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
        
        /* Tvinger global styling */
        .stApp {
            background-color: #090e17 !important; /* Enda dypere svart/blå */
            font-family: 'Inter', sans-serif;
        }
        
        /* Skjul default UI */
        header {visibility: hidden;}
        .block-container {padding-top: 1.5rem !important; max-width: 1400px !important;}
        
        /* Sidebar oppgradering */
        [data-testid="stSidebar"] {
            background-color: #0d131f !important;
            border-right: 1px solid #1e293b !important;
        }
        
        /* --- DASHBOARD HEADER --- */
        .dash-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            margin-bottom: 2rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid #1e293b;
        }
        .dash-title {
            font-size: 2rem;
            font-weight: 700;
            color: #f8fafc;
            margin: 0;
            letter-spacing: -0.5px;
        }
        .dash-subtitle {
            color: #64748b;
            font-size: 0.95rem;
            margin-top: 4px;
        }
        
        /* --- METRICS ROW (Live data look) --- */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 3rem;
        }
        .metric-card {
            background: linear-gradient(145deg, #111827 0%, #0f172a 100%);
            border: 1px solid #1e293b;
            border-radius: 12px;
            padding: 1.5rem;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .metric-value {
            font-size: 2rem;
            font-weight: 800;
            color: #e2e8f0;
            margin-bottom: 4px;
        }
        .metric-label {
            font-size: 0.85rem;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 600;
        }
        .metric-trend {
            font-size: 0.85rem;
            color: #10b981; /* Grønn trend */
            margin-top: 8px;
            display: flex;
            align-items: center;
            gap: 4px;
        }

        /* --- MODULE CARDS (Glassmorphism & Glow) --- */
        .module-section-title {
            font-size: 1.25rem;
            font-weight: 600;
            color: #e2e8f0;
            margin-bottom: 1.5rem;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .card-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 1.5rem;
        }
        .module-card {
            background-color: #111827;
            border: 1px solid #1e293b;
            border-radius: 14px;
            padding: 24px;
            text-decoration: none !important;
            display: flex;
            flex-direction: column;
            position: relative;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            overflow: hidden;
        }
        /* Magisk Glow-effekt på Hover */
        .module-card:hover {
            transform: translateY(-4px);
            border-color: #38bdf8;
            box-shadow: 0 10px 30px -10px rgba(56, 189, 248, 0.15), inset 0 0 20px rgba(56, 189, 248, 0.02);
            background-color: #131c2c;
        }
        
        .card-top {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 16px;
        }
        .card-icon-title {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .icon-box {
            width: 48px; height: 48px;
            background: #1e293b;
            border-radius: 10px;
            display: flex; align-items: center; justify-content: center;
            font-size: 1.4rem;
            border: 1px solid #334155;
        }
        .card-title {
            font-size: 1.1rem;
            font-weight: 700;
            color: #f8fafc;
            margin: 0;
        }
        .card-subtitle {
            font-size: 0.85rem;
            color: #64748b;
        }
        
        /* Status Pulsering */
        .status-badge {
            display: flex; align-items: center; gap: 6px;
            background: #064e3b; color: #34d399;
            padding: 4px 10px; border-radius: 20px;
            font-size: 0.75rem; font-weight: 600;
            border: 1px solid rgba(16, 185, 129, 0.2);
        }
        .status-dot {
            width: 6px; height: 6px; background-color: #34d399;
            border-radius: 50%;
            box-shadow: 0 0 8px #34d399;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0% { transform: scale(0.95); opacity: 0.5; }
            50% { transform: scale(1.2); opacity: 1; }
            100% { transform: scale(0.95); opacity: 0.5; }
        }

        .card-body {
            font-size: 0.95rem;
            color: #cbd5e1;
            line-height: 1.5;
            margin-bottom: 20px;
            flex-grow: 1;
        }
        
        .io-section {
            background: #0b1120;
            border-radius: 8px;
            padding: 12px 16px;
            border: 1px solid #1e293b;
        }
        .io-row {
            display: flex; justify-content: space-between;
            font-size: 0.85rem; padding: 4px 0;
        }
        .io-label { color: #64748b; font-weight: 600; text-transform: uppercase; font-size: 0.7rem;}
        .io-value { color: #94a3b8; }
        .io-output { color: #38bdf8; font-weight: 500;}
        
        /* Sidebar Falsk Meny */
        .fake-menu {
            color: #94a3b8; font-size: 0.9rem; margin-top: 2rem;
            display: flex; flex-direction: column; gap: 12px; padding: 0 10px;
        }
        .fake-menu-item {
            display: flex; align-items: center; gap: 10px;
            padding: 8px 12px; border-radius: 6px; cursor: pointer;
            transition: 0.2s;
        }
        .fake-menu-item:hover { background: #1e293b; color: #f8fafc;}
        .active-menu { background: #1e293b; color: #f8fafc; font-weight: 600; border-left: 3px solid #38bdf8;}
    </style>
""", unsafe_allow_html=True)

# --- 3. SIDEBAR MED LOGO OG "FALSK" NAVIGASJON ---
with st.sidebar:
    st.markdown("<br>", unsafe_allow_html=True)
    if os.path.exists("logo.png"):
        st.image("logo.png", use_container_width=True)
    else:
        st.markdown("<h2 style='text-align: center; color: white;'>Builtly<span style='color:#38bdf8;'>AI</span></h2>", unsafe_allow_html=True)
    
    st.markdown("""
        <div class="fake-menu">
            <div class="io-label" style="margin-bottom: 4px; padding-left: 12px;">Main Navigation</div>
            <div class="fake-menu-item active-menu">📊 Dashboard (Home)</div>
            <div class="fake-menu-item">📁 Project Files</div>
            <div class="fake-menu-item">✍️ Review & Sign-off</div>
            <div class="io-label" style="margin-top: 1rem; margin-bottom: 4px; padding-left: 12px;">Organization</div>
            <div class="fake-menu-item">👥 Team Workspace</div>
            <div class="fake-menu-item">⚙️ Settings</div>
        </div>
    """, unsafe_allow_html=True)

# --- 4. HOVEDPORTAL HTML ---
st.markdown("""
    <div class="dash-header">
        <div>
            <h1 class="dash-title">Engineering Portal</h1>
            <div class="dash-subtitle">Welcome back, senior@builtly.ai. System systems are operating normally.</div>
        </div>
        <div>
            <a href="#" style="background: #38bdf8; color: #020617; padding: 10px 20px; border-radius: 8px; font-weight: 600; text-decoration: none; font-size: 0.9rem;">+ New Project</a>
        </div>
    </div>

    <div class="metrics-grid">
        <div class="metric-card">
            <div class="metric-label">Total Projects</div>
            <div class="metric-value">124</div>
            <div class="metric-trend">↑ 12 active this week</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Reports Generated</div>
            <div class="metric-value">8,092</div>
            <div class="metric-trend">↑ Automated hours saved</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">API Status</div>
            <div class="metric-value">99.9%</div>
            <div class="metric-trend" style="color: #64748b;">Kartverket latency: 120ms</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Compliance AI</div>
            <div class="metric-value">TEK17</div>
            <div class="metric-trend" style="color: #64748b;">Eurocode logic loaded</div>
        </div>
    </div>

    <div class="module-section-title">
        ⚡ Active AI Modules
    </div>

    <div class="card-grid">
        
        <a href="Mulighetsstudie" target="_self" class="module-card">
            <div class="card-top">
                <div class="card-icon-title">
                    <div class="icon-box">📐</div>
                    <div>
                        <h3 class="card-title">ARK — Mulighetsstudie</h3>
                        <div class="card-subtitle">Fase 0 Volumanalyse</div>
                    </div>
                </div>
                <div class="status-badge"><div class="status-dot"></div>Online</div>
            </div>
            <div class="card-body">
                Utnyttelsesgrad (NS3940), volumanalyse, MUA-krav og automatisk overordnet tomt- og solforholdsvurdering.
            </div>
            <div class="io-section">
                <div class="io-label">Data Pipeline</div>
                <div class="io-row"><span class="io-value">In:</span> <span class="io-value">Situasjonskart & KPA-parametere</span></div>
                <div class="io-row"><span class="io-value">Out:</span> <span class="io-output">Mulighetsstudie (PDF)</span></div>
            </div>
        </a>

        <a href="Geo_&_Miljø" target="_self" class="module-card">
            <div class="card-top">
                <div class="card-icon-title">
                    <div class="icon-box">🌍</div>
                    <div>
                        <h3 class="card-title">RIG-M — Miljø & Geo</h3>
                        <div class="card-subtitle">Grunnundersøkelse</div>
                    </div>
                </div>
                <div class="status-badge"><div class="status-dot"></div>Online</div>
            </div>
            <div class="card-body">
                Klassifisering av masser og generering av tiltaksplan iht. forurensningsforskriften kap. 2. og geoteknisk rapport.
            </div>
            <div class="io-section">
                <div class="io-label">Data Pipeline</div>
                <div class="io-row"><span class="io-value">In:</span> <span class="io-value">ALS Lab Excel & Historiske Flyfoto</span></div>
                <div class="io-row"><span class="io-value">Out:</span> <span class="io-output">Tiltaksplan & Historikk (PDF)</span></div>
            </div>
        </a>

        <a href="Konstruksjon_(RIB)" target="_self" class="module-card">
            <div class="card-top">
                <div class="card-icon-title">
                    <div class="icon-box">🏢</div>
                    <div>
                        <h3 class="card-title">RIB — Konstruksjon</h3>
                        <div class="card-subtitle">Bæresystemer</div>
                    </div>
                </div>
                <div class="status-badge"><div class="status-dot"></div>Online</div>
            </div>
            <div class="card-body">
                Konseptuelle konstruksjonsjekker (Eurokode), lastnedføring, prinsippdimensjonering og klimagassregnskap.
            </div>
            <div class="io-section">
                <div class="io-label">Data Pipeline</div>
                <div class="io-row"><span class="io-value">In:</span> <span class="io-value">Arkitektur PDF & Miljølaster</span></div>
                <div class="io-row"><span class="io-value">Out:</span> <span class="io-output">Søylenett & Analyse (PDF)</span></div>
            </div>
        </a>
        
        <a href="Brannkonsept" target="_self" class="module-card">
            <div class="card-top">
                <div class="card-icon-title">
                    <div class="icon-box">🔥</div>
                    <div>
                        <h3 class="card-title">RIBr — Brannkonsept</h3>
                        <div class="card-subtitle">Sikkerhetsstrategi</div>
                    </div>
                </div>
                <div class="status-badge"><div class="status-dot"></div>Online</div>
            </div>
            <div class="card-body">
                Konseptuell brannstrategi: rømning, brannceller, slokkeutstyr og rasjonelle TEK17-vurderinger for best prosjektøkonomi.
            </div>
            <div class="io-section">
                <div class="io-label">Data Pipeline</div>
                <div class="io-row"><span class="io-value">In:</span> <span class="io-value">Plantegninger & BTA</span></div>
                <div class="io-row"><span class="io-value">Out:</span> <span class="io-output">Brannteknisk konsept (PDF)</span></div>
            </div>
        </a>

        <a href="Akustikk" target="_self" class="module-card">
            <div class="card-top">
                <div class="card-icon-title">
                    <div class="icon-box">🔊</div>
                    <div>
                        <h3 class="card-title">RIAku — Akustikk</h3>
                        <div class="card-subtitle">NS 8175 / T-1442</div>
                    </div>
                </div>
                <div class="status-badge"><div class="status-dot"></div>Online</div>
            </div>
            <div class="card-body">
                Støy og lydisolasjon med anbefalte fasadetiltak, romakustikk og verifisering mot offentlige grenseverdier.
            </div>
            <div class="io-section">
                <div class="io-label">Data Pipeline</div>
                <div class="io-row"><span class="io-value">In:</span> <span class="io-value">Støykart (PDF) & Fasade</span></div>
                <div class="io-row"><span class="io-value">Out:</span> <span class="io-output">Akustikkrapport (PDF)</span></div>
            </div>
        </a>

    </div>
""", unsafe_allow_html=True)
