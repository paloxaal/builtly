import streamlit as st
import os
import base64
from pathlib import Path
import requests
from datetime import datetime

# --- 1. GRUNNINNSTILLINGER ---
st.set_page_config(
    page_title="Project Setup | Builtly", 
    page_icon="⚙️", 
    layout="wide", 
    initial_sidebar_state="collapsed"
)

def render_html(html_string: str):
    st.markdown(html_string.replace('\n', ' '), unsafe_allow_html=True)

def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            suffix = Path(candidate).suffix.lower().replace(".", "") or "png"
            with open(candidate, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/{suffix};base64,{encoded}"
    return ""

def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{name}.py")
        if p.exists(): return str(p)
    return ""

# --- 2. PREMIUM CSS (Nå med integrert kort-design for Launchpad!) ---
st.markdown(
    """
<style>
    :root {
        --bg: #06111a; --panel: rgba(10, 22, 35, 0.78); 
        --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38c2c9; --radius-xl: 24px; --radius-lg: 16px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }

    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); margin-bottom: 1rem; }
    
    /* Native Streamlit Knapper */
    button[kind="primary"] {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important; border: none !important; font-weight: 750 !important;
        border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    
    button[kind="secondary"] {
        background: rgba(255,255,255,0.05) !important; color: #f8fafc !important;
        border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; 
        font-weight: 650 !important; padding: 10px 20px !important; transition: all 0.2s ease !important;
    }
    button[kind="secondary"]:hover { 
        background: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; 
        color: var(--accent) !important; transform: translateY(-2px) !important;
    }

    /* INPUT-FELT DESIGN */
    .stTextInput input, .stNumberInput input, .stTextArea textarea {
        background-color: #0d1824 !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important;
        border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {
        border-color: var(--accent) !important; box-shadow: 0 0 0 1px rgba(56, 194, 201, 0.5) !important;
    }
    div[data-baseweb="select"] > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    div[data-baseweb="select"] span { color: #ffffff !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label {
        color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important;
    }

    /* DASHBOARD BOKSER */
    .dash-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 1.5rem; margin-bottom: 1.5rem; }
    .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1.5rem; margin-bottom: 2.5rem; }
    .card { background: linear-gradient(180deg, rgba(16,30,46,0.8), rgba(10,18,28,0.8)); border: 1px solid var(--stroke); border-radius: var(--radius-lg); padding: 1.8rem; box-shadow: 0 12px 30px rgba(0,0,0,0.2); }
    .card-hero { background: linear-gradient(135deg, rgba(16,30,46,0.9), rgba(6,17,26,0.9)); position: relative; overflow: hidden; border-radius: var(--radius-xl);}
    
    .hero-kicker { display: inline-flex; align-items: center; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 1rem; border: 1px solid var(--stroke); padding: 4px 12px; border-radius: 999px; background: rgba(255,255,255,0.02); }
    .hero-title { font-size: 2.8rem; font-weight: 800; margin: 0 0 0.5rem 0; letter-spacing: -0.03em; color: #fff; }
    .hero-sub { color: var(--soft); font-size: 1.05rem; line-height: 1.6; max-width: 50ch; margin-bottom: 1.5rem; }
    
    .status-kicker { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 0.2rem; }
    .status-title { font-size: 2rem; font-weight: 750; color: #fff; margin-bottom: 1rem; }
    .status-desc { color: var(--soft); font-size: 0.9rem; line-height: 1.5; margin-bottom: 1.5rem; }
    .prog-bar-bg { width: 100%; height: 6px; background: rgba(255,255,255,0.1); border-radius: 999px; overflow: hidden; margin-bottom: 1.5rem; }
    
    .meta-row { display: flex; justify-content: space-between; padding: 0.6rem 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 0.85rem; }
    .meta-row:last-child { border-bottom: none; }
    .meta-label { color: var(--muted); }
    .meta-value { color: var(--text); font-weight: 600; text-align: right; }

    .stat-title { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 0.8rem; }
    .stat-value { font-size: 1.8rem; font-weight: 750; color: #fff; margin-bottom: 0.3rem; }
    .stat-desc { font-size: 0.85rem; color: var(--soft); line-height: 1.4; }

    .snap-row { display: flex; justify-content: space-between; align-items: flex-start; padding: 0.8rem 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 0.9rem; }
    .snap-label { color: var(--muted); width: 35%; flex-shrink: 0; }
    .snap-val { color: var(--text); font-weight: 500; text-align: right; width: 65%; word-wrap: break-word;}
    
    /* ALERTS */
    [data-testid="stAlert"] { background-color: rgba(56, 194, 201, 0.1) !important; border: 1px solid rgba(56, 194, 201, 0.3) !important; border-radius: 8px !important; }
    [data-testid="stAlert"] * { color: #f5f7fb !important; }

    /* --- MAGISK CSS FOR LAUNCHPAD KORTENE --- */
    .module-badge {
        display: inline-flex; align-items: center; justify-content: center;
        padding: 0.32rem 0.62rem; border-radius: 999px;
        border: 1px solid rgba(120,145,170,0.18); background: rgba(255,255,255,0.03);
        color: var(--muted); font-size: 0.75rem; font-weight: 650;
    }
    .badge-priority { color: #8ef0c0; border-color: rgba(142,240,192,0.25); background: rgba(126,224,129,0.08); }
    .badge-phase2 { color: #9fe7ff; border-color: rgba(120,220,225,0.22); background: rgba(56,194,201,0.08); }
    .badge-early { color: #d7def7; border-color: rgba(215,222,247,0.18); background: rgba(255,255,255,0.03); }
    .badge-roadmap { color: #f4bf4f; border-color: rgba(244,191,79,0.22); background: rgba(244,191,79,0.08); }

    .module-icon {
        width: 46px; height: 46px; border-radius: 14px;
        display: inline-flex; align-items: center; justify-content: center;
        background: rgba(56,194,201,0.1); border: 1px solid rgba(56,194,201,0.18);
        font-size: 1.32rem; flex-shrink: 0;
    }

    /* Styler den native Streamlit kolonnen til å bli et perfekt kort */
    [data-testid="column"]:has(.module-card-hook) {
        background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98)) !important;
        border: 1px solid rgba(120, 145, 170, 0.18) !important;
        border-radius: 22px !important;
        padding: 1.5rem !important;
        box-shadow: 0 12px 38px rgba(0,0,0,0.18) !important;
        transition: all 0.2s ease !important;
        margin-bottom: 1rem !important;
    }
    [data-testid="column"]:has(.module-card-hook):hover {
        border-color: rgba(56,194,201,0.24) !important;
        box-shadow: 0 16px 42px rgba(0,0,0,0.24) !important;
    }

    /* Tvinger kortet til å fylle hele høyden og dytte knappen nederst */
    [data-testid="column"]:has(.module-card-hook) > div {
        height: 100% !important; display: flex !important; flex-direction: column !important;
    }
    [data-testid="column"]:has(.module-card-hook) [data-testid="stButton"] {
        margin-top: auto !important; width: 100% !important; padding-top: 1rem;
    }

    /* Styler den native st.button i kortet slik at den ser ut som front-page lenken */
    [data-testid="column"]:has(.module-card-hook) button[kind="secondary"] {
        background: rgba(56,194,201,0.1) !important;
        border: 1px solid rgba(56,194,201,0.28) !important;
        color: #f5f7fb !important;
        border-radius: 12px !important;
        min-height: 46px !important;
        font-weight: 650 !important;
        font-size: 0.94rem !important;
        width: 100% !important;
    }
    [data-testid="column"]:has(.module-card-hook) button[kind="secondary"]:hover {
        border-color: rgba(56,194,201,0.8) !important;
        background: rgba(56,194,201,0.2) !important;
        transform: translateY(-2px) !important;
    }
</style>
""", unsafe_allow_html=True)


# --- 3. SESSION STATE LOGIKK (Hjernen i SSOT) ---
if "project_data" not in st.session_state:
    st.session_state.project_data = {
        "land": "Norge (TEK17 / Kartverket)", "p_name": "", "c_name": "", "p_desc": "",
        "adresse": "", "kommune": "", "gnr": "", "bnr": "",
        "b_type": "Næring / Kontor", "etasjer": 4, "bta": 2500, "last_sync": "Ikke synket enda"
    }

pd_state = st.session_state.project_data

fields_to_check = ["p_name", "c_name", "p_desc", "adresse", "kommune", "gnr", "bnr", "b_type", "etasjer", "bta"]
filled_fields = sum(1 for field in fields_to_check if bool(pd_state[field]))
completeness = int((filled_fields / len(fields_to_check)) * 100)
sync_status = "Draft" if completeness < 100 else "Ready"
progress_color = "#38c2c9" if completeness > 80 else "#f4bf4f" if completeness > 40 else "#ef4444"

def fetch_from_kartverket(sok_adresse="", kommune="", gnr="", bnr=""):
    params = {'treffPerSide': 1, 'utkoordsys': 25833}
    if sok_adresse: params['sok'] = sok_adresse
    if kommune: params['kommunenavn'] = kommune
    if gnr: params['gardsnummer'] = gnr
    if bnr: params['bruksnummer'] = bnr
    try:
        r = requests.get("https://ws.geonorge.no/adresser/v1/sok", params=params, timeout=5)
        if r.status_code == 200 and r.json().get('adresser'):
            hit = r.json()['adresser'][0]
            return {"adresse": hit.get('adressetekst', ''), "kommune": hit.get('kommunenavn', ''), "gnr": str(hit.get('gardsnummer', '')), "bnr": str(hit.get('bruksnummer', ''))}
    except Exception: return None
    return None

# --- 4. HEADER ---
c1, c2, c3 = st.columns([2.5, 1, 1])
with c1:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(logo_html)
with c2:
    st.markdown("<div style='margin-top: 0.8rem;'></div>", unsafe_allow_html=True)
    render_html('<a href="/" target="_self" style="display:block; text-align:center; padding:10px; color:#9fb0c3; text-decoration:none; font-weight:600; border:1px solid rgba(120,145,170,0.3); border-radius:12px; background:rgba(255,255,255,0.05);">← Til Portal</a>')
with c3:
    st.markdown("<div style='margin-top: 0.8rem;'></div>", unsafe_allow_html=True)
    if find_page("Review"):
        if st.button("QA & Sign-off", type="primary", use_container_width=True):
            st.switch_page(find_page("Review"))

# --- 5. DASHBOARD UI ---
render_html(f"""
<div class="dash-grid">
    <div class="card card-hero">
        <div class="hero-kicker">✦ Builtly AI • Project SSOT</div>
        <h1 class="hero-title">Project Configuration</h1>
        <div class="hero-sub">Ett kontrollsenter for prosjektets kjerneparametere. Oppdater disse feltene én gang, og la Builtly synke kontekst til analyse, kalkyle og dokumentasjon.</div>
    </div>
    
    <div class="card">
        <div class="status-kicker">Sync Status</div>
        <div class="status-title">{sync_status}</div>
        <div class="status-desc">{filled_fields} av {len(fields_to_check)} nøkkelfelt er fylt ut og tilgjengelige for AI-modulene.</div>
        
        <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:0.5rem; color: #c8d3df;">
            <span>Kompletthet</span><span style="font-weight:700;">{completeness}%</span>
        </div>
        <div class="prog-bar-bg"><div style="width: {completeness}%; height: 100%; background: {progress_color}; border-radius: 999px;"></div></div>
        
        <div class="meta-row"><span class="meta-label">Sist oppdatert</span><span class="meta-value">{pd_state["last_sync"]}</span></div>
        <div class="meta-row"><span class="meta-label">Lokasjon satt</span><span class="meta-value">{"Ja" if pd_state["adresse"] or pd_state["gnr"] else "Nei"}</span></div>
    </div>
</div>

<div class="stat-grid">
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Datakompletthet</div><div class="stat-value" style="color:{progress_color};">{completeness}%</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Primær Bruk</div><div class="stat-value" style="font-size:1.4rem; padding-top:0.4rem;">{pd_state["b_type"]}</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Bruttoareal</div><div class="stat-value">{pd_state["bta"]} m²</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Etasjer</div><div class="stat-value">{pd_state["etasjer"]}</div>
    </div>
</div>
""")

# --- 6. INPUT SEKSJON ---
st.markdown("<h3 style='margin-top: 1rem; margin-bottom: 0.2rem;'>Oppdater prosjektets kontrollsenter</h3>", unsafe_allow_html=True)
st.markdown("<p style='color:#9fb0c3; margin-bottom: 1.5rem;'>Fyll ut dataene under. Dette mates automatisk inn i alle AI-agenter for å sikre samsvar.</p>", unsafe_allow_html=True)

input_col, snap_col = st.columns([2, 1], gap="large")

with input_col:
    st.markdown("""<div style="margin-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);"><h4 style="color: #f5f7fb; margin: 0;">📌 01 Generelt</h4></div>""", unsafe_allow_html=True)
    
    land_options = ["Norge (TEK17 / Kartverket)", "Sverige (BBR)", "Danmark (BR18)", "UK (Building Regs)"]
    try: l_idx = land_options.index(pd_state["land"])
    except: l_idx = 0
    new_land = st.selectbox("🌍 Land / Lokalt Regelverk", land_options, index=l_idx)
    
    c1, c2 = st.columns(2)
    new_p_name = c1.text_input("Prosjektnavn", value=pd_state["p_name"])
    new_c_name = c2.text_input("Tiltakshaver / Oppdragsgiver", value=pd_state["c_name"])
    
    new_p_desc = st.text_area("Prosjektbeskrivelse / Narrativ", value=pd_state["p_desc"], height=140)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""<div style="margin-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);"><h4 style="color: #f5f7fb; margin: 0;">🌍 02 Lokasjon & API</h4></div>""", unsafe_allow_html=True)
    
    if "Norge" in new_land: st.info("💡 **Kartverket API:** Skriv inn adresse *eller* Gnr/Bnr og trykk på knappen for å autoutfylle resten.")
        
    c3, c4 = st.columns(2)
    new_adresse = c3.text_input("Gateadresse", value=pd_state["adresse"])
    new_kommune = c4.text_input("Kommune", value=pd_state["kommune"])
    
    c5, c6 = st.columns(2)
    new_gnr = c5.text_input("Gårdsnummer (Gnr)", value=pd_state["gnr"])
    new_bnr = c6.text_input("Bruksnummer (Bnr)", value=pd_state["bnr"])
    
    if "Norge" in new_land:
        if st.button("🔍 Søk i Matrikkel (Kartverket)", type="secondary"):
            st.session_state.project_data.update({"p_name": new_p_name, "c_name": new_c_name, "p_desc": new_p_desc})
            with st.spinner("Henter fra Nasjonalt Adresseregister..."):
                res = fetch_from_kartverket(new_adresse, new_kommune, new_gnr, new_bnr)
                if res:
                    st.session_state.project_data.update({"adresse": res['adresse'], "kommune": res['kommune'], "gnr": res['gnr'], "bnr": res['bnr']})
                    st.success("✅ Fant eiendom!")
                    st.rerun()
                else: st.warning("Fant ingen treff i Matrikkelen. Sjekk skrivemåten.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("""<div style="margin-bottom: 1rem; border-bottom: 1px solid rgba(255,255,255,0.1);"><h4 style="color: #f5f7fb; margin: 0;">🏢 03 Byggdata</h4></div>""", unsafe_allow_html=True)
    
    c7, c8, c9 = st.columns(3)
    type_options = ["Bolig (Blokk/Rekkehus)", "Næring / Kontor", "Handel / Kjøpesenter", "Offentlig / Skole", "Industri / Lager"]
    try: default_idx = type_options.index(pd_state["b_type"])
    except: default_idx = 1
    
    new_b_type = c7.selectbox("Primær Bruk", type_options, index=default_idx)
    new_etasjer = c8.number_input("Antall Etasjer", value=int(pd_state["etasjer"]), min_value=1)
    new_bta = c9.number_input("Bruttoareal (BTA m²)", value=int(pd_state["bta"]), step=100)

    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("💾 Lagre & Synkroniser SSOT Data", type="primary", use_container_width=True):
        st.session_state.project_data.update({
            "land": new_land, "p_name": new_p_name, "c_name": new_c_name, "p_desc": new_p_desc,
            "adresse": new_adresse, "kommune": new_kommune, "gnr": new_gnr, "bnr": new_bnr,
            "b_type": new_b_type, "etasjer": new_etasjer, "bta": new_bta,
            "last_sync": datetime.now().strftime("%d. %b %Y kl %H:%M")
        })
        st.success(f"✅ Data lagret! Prosjektet '{new_p_name}' er nå synkronisert med alle AI-moduler.")
        st.rerun()

with snap_col:
    render_html(f"""
    <div class="card" style="padding: 1.5rem; position: sticky; top: 2rem;">
        <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.1em; color:var(--muted); margin-bottom:0.2rem;">Live Snapshot</div>
        <h3 style="margin-top:0; margin-bottom:0.5rem; font-size:1.2rem;">Prosjektsammendrag</h3>
        <p style="color:var(--soft); font-size:0.85rem; margin-bottom:1.5rem; line-height:1.5;">Et raskt overblikk over SSOT-dataene slik de ligger i minnet akkurat nå.</p>
        <div class="snap-row"><div class="snap-label">Regelverk</div><div class="snap-val" style="color:var(--accent);">{pd_state["land"].split(' ')[0]}</div></div>
        <div class="snap-row"><div class="snap-label">Prosjekt</div><div class="snap-val">{pd_state["p_name"] or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Oppdragsgiver</div><div class="snap-val">{pd_state["c_name"] or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Adresse</div><div class="snap-val">{pd_state["adresse"] or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Kommune</div><div class="snap-val">{pd_state["kommune"] or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Gnr / Bnr</div><div class="snap-val">{' / '.join(filter(None, [pd_state["gnr"], pd_state["bnr"]])) or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Type</div><div class="snap-val">{pd_state["b_type"]}</div></div>
        <div class="snap-row" style="border-bottom:none;"><div class="snap-label">Volum</div><div class="snap-val">{pd_state["etasjer"]} etg / {pd_state["bta"]} m²</div></div>
    </div>
    """)


# --- 7. LAUNCHPAD (NÅ MED FRONT PAGE KORT-DESIGN!) ---
def render_module_card(col, icon, badge, badge_class, title, desc, input_txt, output_txt, btn_label, page_target):
    """En smart funksjon som tegner et vakkert HTML-kort, og legger en usynlig/kamuflert native-knapp over"""
    with col:
        # Hook for CSS
        st.markdown('<div class="module-card-hook"></div>', unsafe_allow_html=True)
        # Visuelt innhold
        st.markdown(f"""
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:1rem;">
                <div class="module-icon">{icon}</div>
                <div class="module-badge {badge_class}">{badge}</div>
            </div>
            <div style="font-size:1.08rem; font-weight:720; color:#f5f7fb; margin-bottom:0.5rem; line-height: 1.35;">{title}</div>
            <div style="font-size:0.95rem; color:#9fb0c3; line-height:1.6; margin-bottom:1rem;">{desc}</div>
            <div style="font-size:0.86rem; color:#c8d3df; padding-top:0.95rem; border-top:1px solid rgba(120,145,170,0.14);">
                <strong>Input:</strong> {input_txt}<br>
                <strong>Output:</strong> {output_txt}
            </div>
        """, unsafe_allow_html=True)

        # Native knapp som bevarer minnet og er stylet som CTA via CSS
        if page_target and find_page(page_target):
            if st.button(btn_label, key=f"btn_{page_target}", type="secondary", use_container_width=True):
                st.switch_page(find_page(page_target))
        else:
            st.button("In development", key=f"btn_{page_target}_dev", type="secondary", disabled=True, use_container_width=True)

if completeness > 30:
    st.markdown("<hr style='border-color: rgba(120,145,170,0.2); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    st.markdown("<h3 style='text-align:center; margin-bottom: 2.5rem; font-weight:750;'>🚀 Prosjektet er synkronisert! Velg fagmodul under:</h3>", unsafe_allow_html=True)
    
    # RAD 1
    r1c1, r1c2, r1c3 = st.columns(3)
    render_module_card(r1c1, "🌍", "Phase 1 - Priority", "badge-priority", "GEO / ENV - Ground Conditions", 
                       "Analyze lab files and excavation plans. Classifies masses, proposes disposal logic, and drafts environmental action plans.", 
                       "XLSX / CSV / PDF + plans", "Environmental action plan, logs", "Open Geo & Env", "Geo")
    render_module_card(r1c2, "🔊", "Phase 2", "badge-phase2", "ACOUSTICS - Noise & Sound", 
                       "Ingest noise maps and floor plans. Generates facade requirements, window specifications, and mitigation strategies.", 
                       "Noise map + floor plan", "Acoustics report, facade eval.", "Open Acoustics", "Akustikk")
    render_module_card(r1c3, "🔥", "Phase 2", "badge-phase2", "FIRE - Safety Strategy", 
                       "Evaluate architectural drawings against building codes. Generates escape routes, fire cell division, and fire strategy.", 
                       "Architectural drawings + class", "Fire strategy concept, deviations", "Open Fire Strategy", "Brannkonsept")

    st.markdown("<br>", unsafe_allow_html=True)

    # RAD 2
    r2c1, r2c2, r2c3 = st.columns(3)
    render_module_card(r2c1, "📐", "Early phase", "badge-early", "ARK - Feasibility Study", 
                       "Site screening, volume analysis, and early-phase decision support before full engineering design.", 
                       "Site data, zoning plans", "Feasibility report, utilization metrics", "Open Feasibility", "Mulighetsstudie")
    render_module_card(r2c2, "🏢", "Roadmap", "badge-roadmap", "STRUC - Structural Concept", 
                       "Conceptual structural checks, principle dimensioning, and integration with carbon footprint estimations.", 
                       "Models, load parameters", "Concept memo, grid layouts", "Open Structural", "Konstruksjon")
    render_module_card(r2c3, "🚦", "Roadmap", "badge-roadmap", "TRAFFIC - Mobility", 
                       "Traffic generation, parking requirements, access logic, and soft-mobility planning for early project phases.", 
                       "Site plans, local norms", "Traffic memo, mobility plan", "Open Traffic & Mobility", "Trafikk")
