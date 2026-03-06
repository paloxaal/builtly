import streamlit as st

st.set_page_config(page_title="Builtly AI | Enterprise", layout="wide")

# CSS for Enterprise-look
st.markdown("""
    <style>
    .block-container { max-width: 1000px; margin: auto; padding-top: 3rem; }
    .stButton>button { 
        height: 120px !important; 
        border: none !important;
        background-color: #f8f9fa !important;
        border-radius: 12px !important;
        font-weight: 600 !important;
    }
    .stButton>button:hover { background-color: #e9ecef !important; transform: translateY(-2px); }
    </style>
""", unsafe_allow_html=True)

st.image("logo.png", width=250)
st.title("Builtly AI | Engineering Suite")
st.markdown("---")

# Modul-rutenett (Nå inkludert Mulighetsstudie)
c1, c2, c3, c4 = st.columns(4)
with c1:
    if st.button("🌍\nMiljø (RIG-M)"): st.switch_page("pages/miljo.py")
with c2:
    if st.button("🔥\nBrann (RIBr)"): st.switch_page("pages/brann.py")
with c3:
    if st.button("🔊\nAkustikk (RIAKU)"): st.switch_page("pages/akustikk.py")
with c4:
    if st.button("📐\nMulighetsstudie"): st.switch_page("pages/plan.py")