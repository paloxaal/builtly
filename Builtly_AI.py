import streamlit as st
import os

# --- 1. GRUNNINNSTILLINGER ---
st.set_page_config(page_title="Builtly | Engineering Portal", page_icon="🏗️", layout="wide", initial_sidebar_state="expanded")

# --- 2. LOGO ØVERST TIL VENSTRE ---
if os.path.exists("logo.png"):
    st.logo("logo.png", size="large")

# --- 3. MINIMALISTISK PREMIUM CSS ---
css = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
.stApp {
    background-color: #09090b !important;
    font-family: 'Inter', sans-serif;
    color: #fafafa;
}
header {visibility: hidden;}
[data-testid="stSidebar"] {
    background-color: #09090b !important;
    border-right: 1px solid #27272a !important;
}
.block-container {
    padding-top: 3rem !important;
    max-width: 1200px !important;
}
.main-header {
    margin-bottom: 3rem;
    border-bottom: 1px solid #27272a;
    padding-bottom: 1.5rem;
}
.main-title {
    font-size: 2.5rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    margin-bottom: 0.5rem;
    color: #ffffff;
}
.main-subtitle {
    font-size: 1.1rem;
    color: #a1a1aa;
    font-weight: 300;
}
.card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 1.5rem;
}
.module-card {
    background-color: #09090b;
    border: 1px solid #27272a;
    border-radius: 8px;
    padding: 2rem;
    text-decoration: none !important;
    display: block;
    transition: border-color 0.2s ease, background-color 0.2s ease;
}
.module-card:hover {
    border-color: #52525b;
    background-color: #18181b;
}
.card-header {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin-bottom: 1rem;
}
.card-icon {
    font-size: 1.5rem;
    background: #18181b;
    border: 1px solid #27272a;
    width: 44px; height: 44px;
    display: flex; justify-content: center; align-items: center;
    border-radius: 6px;
}
.card-title {
    font-size: 1.1rem;
    font-weight: 500;
    color: #fafafa;
    margin: 0;
}
.card-desc {
    font-size: 0.95rem;
    color: #a1a1aa;
    line-height: 1.6;
    margin-bottom: 0;
}
</style>
"""
st.markdown(css, unsafe_allow_html=True)

# --- 4. HTML LAYOUT (Uten innrykk for å drepe Streamlit Markdown-feilen) ---
html_content = """
<div class="main-header">
<div class="main-title">Engineering Modules</div>
<div class="main-subtitle">Compliance-grade engineering deliverables — generated fast, signed by professionals.</div>
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
