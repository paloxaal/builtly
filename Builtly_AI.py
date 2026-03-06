import streamlit as st
import os

# 1. Grunninnstillinger
st.set_page_config(page_title="Builtly.ai | Enterprise", page_icon="🏗️", layout="wide")

# 2. Lås logoen HELT ØVERST over menyen (Krever Streamlit 1.35+)
if os.path.exists("logo.png"):
    st.logo("logo.png", icon_image="logo.png")

# 3. PREMIUM CSS DESIGN
st.markdown("""
    <style>
        /* Skjul default header bjelke */
        header {visibility: hidden;}
        .block-container {padding-top: 1rem; padding-bottom: 2rem;}
        
        /* Hero Banner */
        .hero {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            padding: 4rem 3rem;
            border-radius: 16px;
            text-align: center;
            margin-bottom: 3rem;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
            border: 1px solid #334155;
        }
        .hero h1 {
            color: #f8fafc;
            font-size: 3.5rem;
            font-weight: 800;
            letter-spacing: -1px;
            margin-bottom: 0.5rem;
            font-family: 'Inter', 'Helvetica Neue', sans-serif;
        }
        .hero p {
            color: #94a3b8;
            font-size: 1.25rem;
            font-weight: 300;
            letter-spacing: 0.5px;
        }
        
        /* Moderne SaaS Kort-Grid */
        .card-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1.5rem;
            margin-top: 1rem;
        }
        .saas-card {
            background-color: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            padding: 1.5rem;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
            transition: all 0.3s ease;
            height: 100%;
        }
        .saas-card:hover {
            transform: translateY(-6px);
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
            border-color: #cbd5e1;
        }
        .icon-box {
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            width: 54px;
            height: 54px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.6rem;
            margin-bottom: 1.2rem;
        }
        .card-title {
            font-size: 1.2rem;
            font-weight: 700;
            color: #0f172a;
            margin-bottom: 0.5rem;
        }
        .card-desc {
            font-size: 0.95rem;
            color: #64748b;
            line-height: 1.6;
        }
    </style>
""", unsafe_allow_html=True)

# 4. HTML STRUKTUR PÅ FORSIDEN
st.markdown("""
    <div class="hero">
        <h1>Builtly.ai</h1>
        <p>Compliance-grade engineering deliverables — generated fast, signed by professionals.</p>
    </div>
    
    <h3 style="color: #0f172a; font-weight: 700; font-size: 1.5rem; margin-bottom: 1rem;">Prosjekteringsmoduler</h3>
    <p style="color: #64748b; margin-bottom: 2rem;">Velg ønsket ingeniørdisiplin i sidemenyen for å starte.</p>
    
    <div class="card-grid">
        <div class="saas-card">
            <div class="icon-box">📐</div>
            <div class="card-title">Mulighetsstudie</div>
            <div class="card-desc">Fase 0 volumanalyse, utnyttelsesgrad (NS3940) og automatisk integrasjon av Kartverket.</div>
        </div>
        
        <div class="saas-card">
            <div class="icon-box">🌍</div>
            <div class="card-title">Geo & Miljø (RIG/RIM)</div>
            <div class="card-desc">Boreplaner, geoteknisk utredning og miljøtekniske tiltaksplaner basert på Excel-data.</div>
        </div>
        
        <div class="saas-card">
            <div class="icon-box">🏢</div>
            <div class="card-title">Konstruksjon (RIB)</div>
            <div class="card-desc">Konseptuelt bæresystem, klimalaster (Eurokode) og CO2/klimagass-vurderinger.</div>
        </div>
        
        <div class="saas-card">
            <div class="icon-box">🔥</div>
            <div class="card-title">Brannsikkerhet (RIBr)</div>
            <div class="card-desc">Rømningsveier, branncelleinndeling og kostnadsoptimale TEK17-løsninger.</div>
        </div>
        
        <div class="saas-card">
            <div class="icon-box">🔊</div>
            <div class="card-title">Akustikk (RIAku)</div>
            <div class="card-desc">Romakustikk, lydklasser og støyvurderinger med automatisk grenseverdi-sjekk.</div>
        </div>
        
        <div class="saas-card" style="background-color: #f8fafc; border: 1px dashed #cbd5e1;">
            <div class="icon-box" style="background-color: transparent; border: none; font-size: 2rem;">🚀</div>
            <div class="card-title" style="color: #64748b;">Flere moduler</div>
            <div class="card-desc" style="color: #94a3b8;">VVS, Elektroteknikk og trafikkvurderinger er under utvikling...</div>
        </div>
    </div>
""", unsafe_allow_html=True)
