import streamlit as st
import os

# Må være første kommando
st.set_page_config(page_title="Builtly.ai", page_icon="🏗️", layout="wide")

# --- KOSMETIKK OG ENTERPRISE-CSS ---
st.markdown("""
    <style>
        /* Skjuler standard toppbjelke for et renere app-design */
        header {visibility: hidden;}
        
        /* Gjør sidemenyen bredere slik at logo og slagord får plass */
        [data-testid="stSidebar"] {
            min-width: 320px;
            background-color: #f8f9fa;
        }
        
        /* Eksklusiv tagline-boks for hovedsiden */
        .slogan-container {
            background: linear-gradient(135deg, #0F2027, #203A43, #2C5364);
            padding: 4rem 2rem;
            border-radius: 12px;
            text-align: center;
            box-shadow: 0 10px 30px rgba(0,0,0,0.15);
            margin-bottom: 3rem;
            color: white;
        }
        .slogan-title {
            font-size: 3.5rem;
            font-weight: 800;
            margin-bottom: 10px;
            letter-spacing: 2px;
        }
        .slogan-text {
            font-size: 1.4rem;
            font-weight: 300;
            color: #d1d5db;
            font-style: italic;
        }
    </style>
""", unsafe_allow_html=True)

# --- SIDEMENY (Logo og meny) ---
with st.sidebar:
    if os.path.exists("logo.png"):
        # use_container_width=True tvinger logoen til å bruke hele plassen, 
        # slik at eventuell tekst/slagord i selve logobildet blir lesbart!
        st.image("logo.png", use_container_width=True)
    else:
        st.markdown("## 🏗️ Builtly.ai")
    
    st.markdown("---")
    # Streamlit legger automatisk sidene fra 'pages'-mappen her under.

# --- HOVEDSIDE (Landing Page) ---
st.markdown("""
    <div class="slogan-container">
        <div class="slogan-title">Builtly.ai</div>
        <div class="slogan-text">Compliance-grade engineering deliverables — generated fast, signed by professionals.</div>
    </div>
""", unsafe_allow_html=True)

st.markdown("### Velkommen til din digitale prosjekteringsavdeling")
st.info("👈 Velg ønsket ingeniørdisiplin i menyen til venstre for å starte prosjekteringen.")

st.markdown("---")

# Dashbord-oversikt med pene kolonner
c1, c2, c3 = st.columns(3)
c1.success("**📐 FASE 0:**\n\nMulighetsstudier, Utnyttelse (NS3940) og Volumskisser.")
c2.warning("**🌍 GRUNNARBEID:**\n\nGeoteknikk (RIG), Miljø (RIM) og Tiltaksplaner.")
c3.error("**🔥 SIKKERHET:**\n\nBrannteknisk konsept (RIBr), Brannceller og Rømning.")

st.write("")

c4, c5, c6 = st.columns(3)
c4.info("**🏢 KONSTRUKSJON:**\n\nBæresystem, Laster (Eurokode) og Klimagass (RIB).")
c5.info("**🔊 MILJØ & HELSE:**\n\nLydklasser, Romakustikk og Støyvurdering (RIAku).")
c6.markdown("") # Tom kolonne for symmetri

st.markdown("---")
st.caption("© 2026 Builtly.ai – The future of PropTech.")
