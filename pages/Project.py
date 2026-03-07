import streamlit as st
import os

# --- 1. GRUNNINNSTILLINGER ---
st.set_page_config(page_title="Project Setup | Builtly", page_icon="⚙️", layout="wide", initial_sidebar_state="expanded")

if os.path.exists("logo.png"):
    st.logo("logo.png", size="large")

# --- 2. PREMIUM CSS ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    .stApp { background-color: #050505 !important; font-family: 'Inter', sans-serif; color: #e4e4e7; }
    header {visibility: hidden;}
    [data-testid="stSidebar"] { background-color: #0a0a0b !important; border-right: 1px solid #1f1f22 !important; }
    .block-container { padding-top: 3rem !important; max-width: 900px !important; }
    
    .header-title { font-size: 2.2rem; font-weight: 700; color: #ffffff; letter-spacing: -0.02em; margin-bottom: 0.2rem; }
    .header-sub { color: #a1a1aa; font-size: 1rem; margin-bottom: 2rem; border-bottom: 1px solid #27272a; padding-bottom: 1.5rem; }
    
    /* Styling av knapper og paneler */
    div[data-testid="stExpander"] { background: rgba(24, 24, 27, 0.4); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; margin-bottom: 1rem; }
</style>
""", unsafe_allow_html=True)

# --- 3. SESSION STATE LOGIKK (Hjernen i SSOT) ---
if "project_data" not in st.session_state:
    st.session_state.project_data = {
        "p_name": "Nytt Prosjekt",
        "c_name": "",
        "p_desc": "", # <--- Her lagres prosjektbeskrivelsen!
        "adresse": "",
        "kommune": "",
        "gnr": "",
        "bnr": "",
        "b_type": "Næring / Kontor",
        "etasjer": 4,
        "bta": 2500
    }

pd = st.session_state.project_data # Snarvei for enklere kode

# --- 4. BRUKERGRENSESNITT ---
st.markdown("<div class='header-title'>Project Configuration</div>", unsafe_allow_html=True)
st.markdown("<div class='header-sub'>Single Source of Truth (SSOT). Define project parameters here to auto-sync across all AI engineering modules.</div>", unsafe_allow_html=True)

with st.expander("📝 General Project Info", expanded=True):
    c1, c2 = st.columns(2)
    new_p_name = c1.text_input("Project Name", value=pd["p_name"])
    new_c_name = c2.text_input("Client / Developer", value=pd["c_name"])
    
    # NYTT: Stort tekstfelt for prosjektbeskrivelse
    new_p_desc = st.text_area(
        "Project Description / Narrative", 
        value=pd["p_desc"],
        height=150,
        placeholder="Describe the project scope, main functions, special requirements, or architectural vision... (e.g. 'A 4-story office building with an underground parking garage and a public cafeteria on the ground floor.')"
    )

with st.expander("🌍 Location & Cadastral Data (Matrikkel)", expanded=True):
    c3, c4 = st.columns(2)
    new_adresse = c3.text_input("Street Address", value=pd["adresse"])
    new_kommune = c4.text_input("Municipality (Kommune)", value=pd["kommune"])
    
    c5, c6 = st.columns(2)
    new_gnr = c5.text_input("Cadastral Number (Gnr)", value=pd["gnr"])
    new_bnr = c6.text_input("Property Number (Bnr)", value=pd["bnr"])

with st.expander("🏢 Building Metrics", expanded=True):
    c7, c8, c9 = st.columns(3)
    
    type_options = ["Bolig (Blokk/Rekkehus)", "Næring / Kontor", "Handel / Kjøpesenter", "Offentlig / Skole", "Industri / Lager"]
    try: default_idx = type_options.index(pd["b_type"])
    except: default_idx = 1
    
    new_b_type = c7.selectbox("Primary Use Case", type_options, index=default_idx)
    new_etasjer = c8.number_input("Number of Floors", value=int(pd["etasjer"]), min_value=1)
    new_bta = c9.number_input("Gross Area (BTA m2)", value=int(pd["bta"]), step=100)

st.markdown("<br>", unsafe_allow_html=True)

# --- 5. LAGRE-KNAPP ---
if st.button("💾 Save & Sync Master Data", type="primary", use_container_width=True):
    st.session_state.project_data.update({
        "p_name": new_p_name,
        "c_name": new_c_name,
        "p_desc": new_p_desc, # <--- Sender teksten til minnet
        "adresse": new_adresse,
        "kommune": new_kommune,
        "gnr": new_gnr,
        "bnr": new_bnr,
        "b_type": new_b_type,
        "etasjer": new_etasjer,
        "bta": new_bta
    })
    st.success(f"✅ Success! **{new_p_name}** is now globally synced. Open any engineering module and the data will be pre-filled.")
    st.balloons()
