import streamlit as st
import os

# --- 1. GRUNNINNSTILLINGER ---
st.set_page_config(page_title="Builtly | Engineering Portal", page_icon="🏗️", layout="wide", initial_sidebar_state="expanded")

# --- 2. LOGO ØVERST TIL VENSTRE ---
if os.path.exists("logo.png"):
    st.logo("logo.png", size="large")

# --- 3. PREMIUM "GLASS & NEON" CSS ---
css = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* Global Dark Mode */
.stApp {
    background-color: #050505 !important;
    font-family: 'Inter', sans-serif;
    color: #e4e4e7;
}

/* Fjerner Streamlit-headere */
header {visibility: hidden;}

/* Gjør sidemenyen dyp og ren */
[data-testid="stSidebar"] {
    background-color: #0a0a0b !important;
    border-right: 1px solid #1f1f22 !important;
}

/* Senter-innhold og bredde */
.block-container {
    padding-top: 4rem !important;
    max-width: 1200px !important;
}

/* --- HOVEDHEADER --- */
.main-header {
    margin-bottom: 4rem;
}
.main-title {
    font-size: 3rem;
    font-weight: 700;
    letter-spacing: -0.03em;
    margin-bottom: 0.5rem;
    background: linear-gradient(to right, #ffffff, #a1a1aa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.main-subtitle {
    font-size: 1.15rem;
    color: #71717a;
    font-weight: 400;
    max-width: 600px;
    line-height: 1.6;
}

/* --- KORT-GRID --- */
.card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 1.5rem;
}

/* --- DE NYE PREMIUM-KORTENE --- */
.module-card {
    background: rgba(24, 24, 27, 0.4);
    border: 1px solid rgba(255, 255, 255, 0.08);
    backdrop-filter: blur(10px);
    border-radius: 12px;
    padding: 2rem;
    text-decoration: none !important;
    display: block;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    overflow: hidden;
}

/* Fikser den stygge blåfargen på lenker! */
.module-card * {
    text-decoration: none !important;
}

/* Glow-effekt på hover */
.module-card:hover {
    background: rgba(24, 24, 27, 0.8);
    border-color: rgba(56, 189, 248, 0.4);
    transform: translateY(-5px);
    box-shadow: 0 10px 40px -10px rgba(56, 189, 248, 0.15);
}

/* Subtil linje i toppen av kortet på hover */
.module-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; height: 2px;
    background: transparent;
    transition: background 0.3s ease;
}
.module-card:hover::before {
    background: linear-gradient(90deg, #38bdf8, #818cf8);
}

.card-header {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 1.25rem;
}

.card-icon {
    font-size: 1.5rem;
    background: #18181b;
    border: 1px solid #27272a;
    color: #ffffff;
    width: 48px; height: 48px;
    display: flex; justify-content: center; align-items: center;
    border-radius: 10px;
    box-shadow: inset 0 1px 1px rgba(255,255,255,0.05);
}

/* Tvinger tittelen til å være hvit, uansett hva Streamlit vil */
.card-title {
    font-size: 1.15rem;
    font-weight: 600;
    color: #ffffff !important;
    margin: 0;
    letter-spacing: -0.01em;
}

/* Tvinger beskrivelsen til å være lysegrå */
.card-desc {
    font-size: 0.95rem;
    color: #a1a1aa !important;
    line-height: 1.6;
    margin-bottom: 0;
    font-weight: 300;
}
</style>
"""
st.markdown(css, unsafe_allow_html=True)

# --- 4. HTML LAYOUT (Uten innrykk) ---
html_content = """
<div class="main-header">
<div class="main-title">Engineering Modules</div>
<div class="main-subtitle">Compliance-grade deliverables — generated fast, signed by professionals.</div>
</div>
<div class="card-grid">
<a href="Mulighetsstudie" target="_self" class="module-card">
<div class="card-header">
<div class="card-icon">📐</div>
<h3 class="card-title">ARK — Mulighetsstudie</h3>
</div>
<p class="card-desc">Fase 0 volumanalyse og utnyttelsesgrad (NS3940) med automatisk KPA-integrasjon.</p>
</a>
<a href="Geo" target="_self" class="module-card">
<div class="card-header">
<div class="card-icon">🌍</div>
<h3 class="card-title">RIG-M — Miljø & Geo</h3>
</div>
<p class="card-desc">Klassifisering av masser og generering av tiltaksplan og geoteknisk rapport fra lab-data.</p>
</a>
<a href="Konstruksjon" target="_self" class="module-card">
<div class="card-header">
<div class="card-icon">🏢</div>
<h3 class="card-title">RIB — Konstruksjon</h3>
</div>
<p class="card-desc">Konseptuelle konstruksjonsjekker (Eurokode), prinsippdimensjonering og klimagassregnskap.</p>
</a>
<a href="Brannkonsept" target="_self" class="module-card">
<div class="card-header">
<div class="card-icon">🔥</div>
<h3 class="card-title">RIBr — Brannkonsept</h3>
</div>
<p class="card-desc">Konseptuell brannstrategi: rømning, brannceller og rasjonelle TEK17-vurderinger.</p>
</a>
<a href="Akustikk" target="_self" class="module-card">
<div class="card-header">
<div class="card-icon">🔊</div>
<h3 class="card-title">RIAku — Akustikk</h3>
</div>
<p class="card-desc">Støy og lydisolasjon med anbefalte fasadetiltak og verifisering mot grenseverdier.</p>
</a>
</div>
"""
st.markdown(html_content, unsafe_allow_html=True)
