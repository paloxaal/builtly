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

# --- 2. ANTI-BUG RENDERER & LOGO HENTER ---
# Dette er magien som hindrer Streamlit i å lage "hvite kodebokser" av HTML-en!
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

# --- 3. SESSION STATE LOGIKK (Hjernen i SSOT) ---
if "project_data" not in st.session_state:
    st.session_state.project_data = {
        "land": "Norge (TEK17 / Kartverket)",
        "p_name": "Nytt Prosjekt",
        "c_name": "",
        "p_desc": "",
        "adresse": "",
        "kommune": "",
        "gnr": "",
        "bnr": "",
        "b_type": "Næring / Kontor",
        "etasjer": 4,
        "bta": 2500,
        "last_sync": "Ikke synket enda"
    }

pd = st.session_state.project_data

# Kalkulerer data-kompletthet automatisk
fields_to_check = ["p_name", "c_name", "p_desc", "adresse", "kommune", "gnr", "bnr", "b_type", "etasjer", "bta"]
filled_fields = sum(1 for field in fields_to_check if bool(pd[field]))
completeness = int((filled_fields / len(fields_to_check)) * 100)
sync_status = "Draft" if completeness < 100 else "Ready"
progress_color = "#38bdf8" if completeness > 80 else "#f4bf4f" if completeness > 40 else "#ef4444"

# --- 4. KARTVERKET API ---
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
            return {
                "adresse": hit.get('adressetekst', ''),
                "kommune": hit.get('kommunenavn', ''),
                "gnr": str(hit.get('gardsnummer', '')),
                "bnr": str(hit.get('bruksnummer', ''))
            }
    except Exception:
        return None
    return None

# --- 5. PREMIUM CSS ---
st.markdown(
    """
<style>
    :root {
        --bg: #06111a; --panel: rgba(10, 22, 35, 0.78); --panel-2: rgba(13, 27, 42, 0.94);
        --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38bdf8; --radius-xl: 24px; --radius-lg: 16px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }

    .top-shell { margin-bottom: 2rem; }
    .brand-logo { height: 60px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }

    /* DASHBOARD BOKSER */
    .dash-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 1.5rem; margin-bottom: 1.5rem; }
    .stat-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1.5rem; margin-bottom: 2.5rem; }
    
    .card { background: linear-gradient(180deg, rgba(16,30,46,0.8), rgba(10,18,28,0.8)); border: 1px solid var(--stroke); border-radius: var(--radius-lg); padding: 1.8rem; box-shadow: 0 12px 30px rgba(0,0,0,0.2); }
    .card-hero { background: linear-gradient(135deg, rgba(16,30,46,0.9), rgba(6,17,26,0.9)); position: relative; overflow: hidden; }
    .card-hero::after { content: ""; position: absolute; top: -50%; right: -20%; width: 400px; height: 400px; background: radial-gradient(circle, rgba(56,189,248,0.1) 0%, transparent 60%); pointer-events: none; }
    
    .hero-kicker { display: inline-flex; align-items: center; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 1rem; border: 1px solid var(--stroke); padding: 4px 12px; border-radius: 999px; background: rgba(255,255,255,0.02); }
    .hero-title { font-size: 2.8rem; font-weight: 800; margin: 0 0 0.5rem 0; letter-spacing: -0.03em; color: #fff; }
    .hero-sub { color: var(--soft); font-size: 1.05rem; line-height: 1.6; max-width: 50ch; margin-bottom: 1.5rem; }
    
    .tag-container { display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .tag { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); padding: 6px 14px; border-radius: 999px; font-size: 0.8rem; color: var(--soft); }
    
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

    /* Live Snapshot */
    .snap-row { display: flex; justify-content: space-between; align-items: flex-start; padding: 0.8rem 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 0.9rem; }
    .snap-label { color: var(--muted); width: 35%; flex-shrink: 0; }
    .snap-val { color: var(--text); font-weight: 500; text-align: right; width: 65%; word-wrap: break-word;}

    /* Styling av Streamlit Tabs */
    div[data-baseweb="tab-list"] { background-color: transparent; gap: 8px; }
    div[data-baseweb="tab"] { background-color: rgba(255,255,255,0.03); border: 1px solid rgba(120,145,170,0.15); border-radius: 8px; padding: 10px 16px; color: var(--muted); }
    div[data-baseweb="tab"][aria-selected="true"] { background-color: rgba(56,189,248,0.1); border-color: var(--accent); color: #fff; }
    div[data-baseweb="tab-highlight"] { display: none; }

    @media (max-width: 1024px) {
        .dash-grid { grid-template-columns: 1fr; }
        .stat-grid { grid-template-columns: repeat(2, 1fr); }
    }
</style>
""", unsafe_allow_html=True)

# --- 6. HEADER & DASHBOARD UI ---
logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0;">Builtly</h2>'

# Vi bruker render_html for å beskytte mot Streamlits hvite kodeboks-bug!
render_html(f"""
<div class="top-shell">{logo_html}</div>

<div class="dash-grid">
    <div class="card card-hero">
        <div class="hero-kicker">✦ Builtly AI • Project SSOT</div>
        <h1 class="hero-title">Project Configuration</h1>
        <div class="hero-sub">Ett kontrollsenter for prosjektets kjerneparametere. Oppdater disse feltene én gang, og la Builtly synke kontekst til analyse, kalkyle og dokumentasjon.</div>
        <div class="tag-container">
            <div class="tag">AI-native proptech UX</div>
            <div class="tag">Enterprise-grade dataflyt</div>
            <div class="tag">Single Source of Truth</div>
        </div>
    </div>
    
    <div class="card">
        <div class="status-kicker">Sync Status</div>
        <div class="status-title">{sync_status}</div>
        <div class="status-desc">{filled_fields} av {len(fields_to_check)} nøkkelfelt er fylt ut og tilgjengelige for AI-modulene.</div>
        
        <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom:0.5rem; color: #c8d3df;">
            <span>Kompletthet</span>
            <span style="font-weight:700;">{completeness}%</span>
        </div>
        <div class="prog-bar-bg">
            <div style="width: {completeness}%; height: 100%; background: {progress_color}; border-radius: 999px;"></div>
        </div>
        
        <div class="meta-row"><span class="meta-label">Sist oppdatert</span><span class="meta-value">{pd["last_sync"]}</span></div>
        <div class="meta-row"><span class="meta-label">Lokasjon satt</span><span class="meta-value">{"Ja" if pd["adresse"] or pd["gnr"] else "Nei"}</span></div>
    </div>
</div>

<div class="stat-grid">
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Datakompletthet</div>
        <div class="stat-value" style="color:{progress_color};">{completeness}%</div>
        <div class="stat-desc">{filled_fields} / {len(fields_to_check)} SSOT-felt er fylt ut.</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Primær Bruk</div>
        <div class="stat-value" style="font-size:1.4rem; padding-top:0.4rem;">{pd["b_type"]}</div>
        <div class="stat-desc">Styrer brannklasse og normer.</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Bruttoareal</div>
        <div class="stat-value">{pd["bta"]} m²</div>
        <div class="stat-desc">Grunnlag for analyse og estimering.</div>
    </div>
    <div class="card" style="padding: 1.5rem;">
        <div class="stat-title">Etasjer</div>
        <div class="stat-value">{pd["etasjer"]}</div>
        <div class="stat-desc">Ca. {int(pd["bta"]/max(1, pd["etasjer"]))} m² per etasje.</div>
    </div>
</div>
""")

# --- 7. INPUT SEKSJON & LIVE SNAPSHOT ---
st.markdown("<h3 style='margin-top: 1rem; margin-bottom: 0.2rem;'>Oppdater prosjektets kontrollsenter</h3>", unsafe_allow_html=True)
st.markdown("<p style='color:#9fb0c3; margin-bottom: 1.5rem;'>Naviger gjennom fanene under for å etablere prosjektdata. Disse mates automatisk inn i alle AI-agenter for å sikre samsvar i dokumentasjonen.</p>", unsafe_allow_html=True)

input_col, snap_col = st.columns([2, 1], gap="large")

with input_col:
    tab1, tab2, tab3 = st.tabs(["📌 01 Generelt", "🌍 02 Lokasjon & API", "🏢 03 Byggdata"])
    
    with tab1:
        st.markdown("<br>", unsafe_allow_html=True)
        # Språk/Land Selector
        land_options = ["Norge (TEK17 / Kartverket)", "Sverige (BBR)", "Danmark (BR18)", "UK (Building Regs)"]
        try: l_idx = land_options.index(pd["land"])
        except: l_idx = 0
        new_land = st.selectbox("Land / Lokalt Regelverk", land_options, index=l_idx)
        
        c1, c2 = st.columns(2)
        new_p_name = c1.text_input("Prosjektnavn", value=pd["p_name"])
        new_c_name = c2.text_input("Tiltakshaver / Oppdragsgiver", value=pd["c_name"])
        
        new_p_desc = st.text_area(
            "Prosjektbeskrivelse / Narrativ (Viktig for AI)", 
            value=pd["p_desc"],
            height=140,
            placeholder="Beskriv prosjektet kort... (f.eks. 'Oppføring av 4-etasjers kontorbygg med underjordisk parkering og kantine på gateplan.')"
        )

    with tab2:
        st.markdown("<br>", unsafe_allow_html=True)
        
        if "Norge" in new_land:
            st.info("💡 **Kartverket API:** Skriv inn adresse *eller* Gnr/Bnr og trykk Søk for å autoutfylle resten.")
            
        c3, c4 = st.columns(2)
        new_adresse = c3.text_input("Gateadresse", value=pd["adresse"])
        new_kommune = c4.text_input("Kommune", value=pd["kommune"])
        
        c5, c6 = st.columns(2)
        new_gnr = c5.text_input("Gårdsnummer (Gnr)", value=pd["gnr"])
        new_bnr = c6.text_input("Bruksnummer (Bnr)", value=pd["bnr"])
        
        if "Norge" in new_land:
            if st.button("🔍 Søk i Matrikkel (Kartverket API)"):
                with st.spinner("Henter fra Nasjonalt Adresseregister..."):
                    res = fetch_from_kartverket(new_adresse, new_kommune, new_gnr, new_bnr)
                    if res:
                        st.success(f"✅ Fant eiendom: {res['adresse']}, {res['kommune']} (Gnr {res['gnr']}/Bnr {res['bnr']})")
                        # Pre-fill
                        new_adresse, new_kommune, new_gnr, new_bnr = res['adresse'], res['kommune'], res['gnr'], res['bnr']
                    else:
                        st.warning("Fant ingen treff i Matrikkelen. Sjekk skrivemåten.")

    with tab3:
        st.markdown("<br>", unsafe_allow_html=True)
        c7, c8, c9 = st.columns(3)
        
        type_options = ["Bolig (Blokk/Rekkehus)", "Næring / Kontor", "Handel / Kjøpesenter", "Offentlig / Skole", "Industri / Lager"]
        try: default_idx = type_options.index(pd["b_type"])
        except: default_idx = 1
        
        new_b_type = c7.selectbox("Primær Bruk", type_options, index=default_idx)
        new_etasjer = c8.number_input("Antall Etasjer", value=int(pd["etasjer"]), min_value=1)
        new_bta = c9.number_input("Bruttoareal (BTA m²)", value=int(pd["bta"]), step=100)

    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("💾 Lagre & Synkroniser SSOT Data", type="primary", use_container_width=True):
        st.session_state.project_data.update({
            "land": new_land,
            "p_name": new_p_name,
            "c_name": new_c_name,
            "p_desc": new_p_desc,
            "adresse": new_adresse,
            "kommune": new_kommune,
            "gnr": new_gnr,
            "bnr": new_bnr,
            "b_type": new_b_type,
            "etasjer": new_etasjer,
            "bta": new_bta,
            "last_sync": datetime.now().strftime("%d. %b %Y kl %H:%M")
        })
        st.success(f"✅ Data lagret! Prosjektet '{new_p_name}' er nå synkronisert med alle AI-moduler.")
        st.rerun()

with snap_col:
    # Live Snapshot via render_html for å unngå hvit boks-bug!
    render_html(f"""
    <div class="card" style="padding: 1.5rem; height: 100%;">
        <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.1em; color:var(--muted); margin-bottom:0.2rem;">Live Snapshot</div>
        <h3 style="margin-top:0; margin-bottom:0.5rem; font-size:1.2rem;">Prosjektsammendrag</h3>
        <p style="color:var(--soft); font-size:0.85rem; margin-bottom:1.5rem; line-height:1.5;">Et raskt overblikk over SSOT-dataene slik de ligger i minnet akkurat nå.</p>
        
        <div class="snap-row">
            <div class="snap-label">Regelverk</div>
            <div class="snap-val" style="color:var(--accent);">{pd["land"].split(' ')[0]}</div>
        </div>
        <div class="snap-row">
            <div class="snap-label">Prosjekt</div>
            <div class="snap-val">{pd["p_name"] or '-'}</div>
        </div>
        <div class="snap-row">
            <div class="snap-label">Oppdragsgiver</div>
            <div class="snap-val">{pd["c_name"] or '-'}</div>
        </div>
        <div class="snap-row">
            <div class="snap-label">Adresse</div>
            <div class="snap-val">{pd["adresse"] or '-'}</div>
        </div>
        <div class="snap-row">
            <div class="snap-label">Kommune</div>
            <div class="snap-val">{pd["kommune"] or '-'}</div>
        </div>
        <div class="snap-row">
            <div class="snap-label">Gnr / Bnr</div>
            <div class="snap-val">{' / '.join(filter(None, [pd["gnr"], pd["bnr"]])) or '-'}</div>
        </div>
        <div class="snap-row">
            <div class="snap-label">Type</div>
            <div class="snap-val">{pd["b_type"]}</div>
        </div>
        <div class="snap-row" style="border-bottom:none;">
            <div class="snap-label">Volum</div>
            <div class="snap-val">{pd["etasjer"]} etg / {pd["bta"]} m²</div>
        </div>
    </div>
    """)
