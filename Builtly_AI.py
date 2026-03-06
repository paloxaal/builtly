import streamlit as st
import os

# 1. Grunninnstillinger - Tvinger Sidebar åpen og fjerner unødvendig Streamlit-UI
st.set_page_config(page_title="Builtly.ai | Enterprise Platform", page_icon="🏗️", layout="wide", initial_sidebar_state="expanded")

# 2. EKSTREMT PREMIUM CSS
st.markdown("""
    <style>
        /* Importerer sylskarp font */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;700;900&display=swap');
        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif;
        }
        
        /* Fjerner Streamlit-headere for 100% clean SaaS look */
        header {visibility: hidden;}
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        .block-container {
            padding-top: 1rem;
            padding-bottom: 2rem;
            max-width: 1400px;
        }

        /* Hvit, ren sidebar for massiv logo */
        [data-testid="stSidebar"] {
            background-color: #ffffff;
            border-right: 1px solid #f1f5f9;
        }
        
        /* HERO BANNER - Dyp og autoritær */
        .hero-section {
            background: radial-gradient(circle at 10% 20%, #0f172a 0%, #020617 100%);
            border-radius: 20px;
            padding: 6rem 5rem;
            margin-top: 1rem;
            margin-bottom: 4rem;
            color: #ffffff;
            box-shadow: 0 25px 50px -12px rgba(0,0,0,0.3);
            position: relative;
            overflow: hidden;
        }
        .hero-section::after {
            content: '';
            position: absolute;
            top: -50%; right: -10%; width: 60%; height: 200%;
            background: radial-gradient(circle, rgba(56,189,248,0.05) 0%, transparent 60%);
            transform: rotate(-15deg);
        }
        .hero-badge {
            display: inline-block;
            background: rgba(56, 189, 248, 0.1);
            color: #38bdf8;
            padding: 0.5rem 1rem;
            border-radius: 50px;
            font-size: 0.85rem;
            font-weight: 700;
            letter-spacing: 1px;
            text-transform: uppercase;
            margin-bottom: 1.5rem;
            border: 1px solid rgba(56, 189, 248, 0.2);
        }
        .hero-title {
            font-size: 4.5rem;
            font-weight: 900;
            line-height: 1.1;
            letter-spacing: -0.04em;
            margin-bottom: 1.5rem;
            background: linear-gradient(to right, #ffffff, #94a3b8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .hero-subtitle {
            font-size: 1.35rem;
            font-weight: 300;
            color: #94a3b8;
            max-width: 700px;
            line-height: 1.6;
        }

        /* KORT-SYSTEMET */
        .saas-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
            gap: 2rem;
            padding-bottom: 2rem;
        }
        
        .saas-card {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 16px;
            padding: 2.5rem;
            transition: all 0.3s ease;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
            position: relative;
            overflow: hidden;
        }
        .saas-card::before {
            content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px;
            background: linear-gradient(to right, #e2e8f0, #e2e8f0);
            transition: all 0.3s ease;
        }
        .saas-card:hover {
            transform: translateY(-8px);
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
            border-color: #cbd5e1;
        }
        .saas-card:hover::before {
            background: linear-gradient(to right, #38bdf8, #3b82f6);
        }
        .icon-wrapper {
            width: 60px; height: 60px; background: #f8fafc; border-radius: 14px;
            display: flex; align-items: center; justify-content: center;
            font-size: 1.8rem; margin-bottom: 1.5rem; border: 1px solid #f1f5f9;
        }
        .card-title {
            font-size: 1.4rem; font-weight: 700; color: #0f172a; margin-bottom: 0.75rem;
        }
        .card-text {
            font-size: 1rem; color: #64748b; line-height: 1.6;
        }
    </style>
""", unsafe_allow_html=True)

# 3. STOR LOGO I SIDEMENY
with st.sidebar:
    st.markdown("<br>", unsafe_allow_html=True)
    if os.path.exists("logo.png"):
        # use_container_width gjør at den fyller hele margen perfekt
        st.image("logo.png", use_container_width=True)
    else:
        st.markdown("<h2 style='text-align: center; color: #0f172a; font-weight: 900;'>Builtly.ai</h2>", unsafe_allow_html=True)
    st.markdown("<br><br>", unsafe_allow_html=True)

# 4. HOVEDINNHOLD HTML
st.markdown("""
    <div class="hero-section">
        <div class="hero-badge">Enterprise Engineering Platform</div>
        <div class="hero-title">Automate the invisible.<br>Build the impossible.</div>
        <div class="hero-subtitle">Compliance-grade engineering deliverables — generated fast, signed by professionals. Select a module in the sidebar to begin processing.</div>
    </div>
    
    <div style="margin-bottom: 2rem;">
        <h2 style="font-size: 1.8rem; font-weight: 700; color: #0f172a;">Engineering Modules</h2>
        <p style="color: #64748b; font-size: 1.1rem;">Active disciplines ready for deployment.</p>
    </div>

    <div class="saas-grid">
        <div class="saas-card">
            <div class="icon-wrapper">📐</div>
            <div class="card-title">Mulighetsstudie</div>
            <div class="card-text">Fase 0 volumanalyse, utnyttelsesgrad (NS3940) og automatisk integrasjon av Kartverket.</div>
        </div>
        
        <div class="saas-card">
            <div class="icon-wrapper">🌍</div>
            <div class="card-title">Geo & Miljø (RIG/RIM)</div>
            <div class="card-text">Boreplaner, geoteknisk utredning og miljøtekniske tiltaksplaner basert på opplastet lab-data.</div>
        </div>
        
        <div class="saas-card">
            <div class="icon-wrapper">🏢</div>
            <div class="card-title">Konstruksjon (RIB)</div>
            <div class="card-text">Konseptuelt bæresystem, klimalaster (Eurokode) og CO2/klimagass-vurderinger.</div>
        </div>
        
        <div class="saas-card">
            <div class="icon-wrapper">🔥</div>
            <div class="card-title">Brannsikkerhet (RIBr)</div>
            <div class="card-text">Rømningsveier, branncelleinndeling og kostnadsoptimale TEK17-løsninger.</div>
        </div>
        
        <div class="saas-card">
            <div class="icon-wrapper">🔊</div>
            <div class="card-title">Akustikk (RIAku)</div>
            <div class="card-text">Romakustikk, lydklasser og støyvurderinger med automatisk grenseverdi-sjekk.</div>
        </div>
    </div>
""", unsafe_allow_html=True)
