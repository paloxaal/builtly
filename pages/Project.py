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

# --- 2. ANTI-BUG RENDERER, LOGO & SIDE-SØKER ---
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
        if p.exists():
            return str(p)
    return ""

# --- 3. PREMIUM CSS (Fjernet Tab-styling, optimalisert for flyt) ---
st.markdown(
    """
<style>
    :root {
        --bg: #06111a; --panel: rgba(10, 22, 35, 0.78); --panel-2: rgba(13, 27, 42, 0.94);
        --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38c2c9; --radius-xl: 24px; --radius-lg: 16px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }

    /* HEADER & PILLE-KNAPPER */
    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    
    .topbar-right {
        display: flex; align-items: center; justify-content: flex-end; gap: 0.65rem;
        padding: 0.35rem; border-radius: 18px; background: rgba(255,255,255,0.025);
        border: 1px solid rgba(120,145,170,0.12); flex-wrap: nowrap !important;
    }
    .top-link {
        display: inline-flex; align-items: center; justify-content: center; min-height: 42px;
        padding: 0.72rem 1.2rem; border-radius: 12px; text-decoration: none !important;
        font-weight: 650; font-size: 0.93rem; transition: all 0.2s ease; border: 1px solid transparent;
        white-space: nowrap;
    }
    .top-link.ghost { color: var(--soft) !important; background: rgba(255,255,255,0.04); border-color: rgba(120,145,170,0.18); }
    .top-link.ghost:hover { color: #ffffff !important; border-color: rgba(56,194,201,0.38); background: rgba(255,255,255,0.06); }
    .top-link.primary {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important; border-color: rgba(120,220,225,0.45);
    }
    .top-link.primary:hover { transform: translateY(-1px); box-shadow: 0 10px 24px rgba(56,194,201,0.18); }

    /* STREAMLIT NATIVE KNAPPER (LAGRE OSV) */
    button[kind="primary"] {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important; border: none !important; font-weight: 750 !important;
        border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] {
        background: rgba(255,255,255,0.05) !important; color: #f8fafc !important;
        border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 8px !important; font-weight: 600 !important;
    }
    button[kind="secondary"]:hover { background: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; }

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

    .snap-row { display: flex; justify-content: space-between; align-items: flex-start; padding: 0.8rem 0; border-bottom: 1px solid rgba(255,255,255,0.05); font-size: 0.9rem; }
    .snap-label { color: var(--muted); width: 35%; flex-shrink: 0; }
    .snap-val { color: var(--text); font-weight: 500; text-align: right; width: 65%; word-wrap: break-word;}
    
    /* Launchpad knapper */
    [data-testid="stPageLink-NavLink"] {
        background-color: rgba(16, 30, 46, 0.8); border: 1px solid rgba(56,194,201,0.3); border-radius: 12px; padding: 16px; transition: all 0.2s; margin-top: 8px;
        display: flex; justify-content: center; text-align: center;
    }
    [data-testid="stPageLink-NavLink"]:hover { background-color: rgba(56,194,201,0.15); border-color: rgba(56,194,201,0.8); transform: translateY(-2px); }
    [data-testid="stPageLink-NavLink"] * { color: #ffffff !important; font-weight: 650 !important; font-size: 1.05rem !important;}

    @media (max-width: 1024px) { .dash-grid { grid-template-columns: 1fr; } .stat-grid { grid-template-columns: repeat(2, 1fr); } }
</style>
""", unsafe_allow_html=True)


# --- 4. SESSION STATE LOGIKK (Hjernen i SSOT) ---
if "project_data" not in st.session_state:
    st.session_state.project_data = {
        "land": "Norge (TEK17 / Kartverket)",
        "p_name": "",
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

# Kalkulerer data-kompletthet
fields_to_check = ["p_name", "c_name", "p_desc", "adresse", "kommune", "gnr", "bnr", "b_type", "etasjer", "bta"]
filled_fields = sum(1 for field in fields_to_check if bool(pd[field]))
completeness = int((filled_fields / len(fields_to_check)) * 100)
sync_status = "Draft" if completeness < 100 else "Ready"
progress_color = "#38c2c9" if completeness > 80 else "#f4bf4f" if completeness > 40 else "#ef4444"

# --- 5. KARTVERKET API ---
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

# --- 6. HEADER & DASHBOARD UI ---
logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'

render_html(f"""
<div class="top-shell">
    <div class="brand-left">
        {logo_html}
    </div>
    <div class="topbar-right">
        <a href="/" target="_self" class="top-link ghost">← Tilbake til Portal</a>
        <a href="Review" target="_self" class="top-link primary">QA & Sign-off</a>
    </div>
</div>

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

# --- 7. INPUT SEKSJON (Fjernet faner for bedre flyt!) ---
st.markdown("<h3 style='margin-top: 1rem; margin-bottom: 0.2rem;'>Oppdater prosjektets kontrollsenter</h3>", unsafe_allow_html=True)
st.markdown("<p style='color:#9fb0c3; margin-bottom: 1.5rem;'>Fyll ut dataene under. Dette mates automatisk inn i alle AI-agenter for å sikre samsvar.</p>", unsafe_allow_html=True)

input_col, snap_col = st.columns([2, 1], gap="large")

with input_col:
    # --- SEKSJON 1: GENERELT ---
    st.markdown("""
    <div style="margin-bottom: 1rem; padding-bottom: 0.5rem; border-bottom: 1px solid rgba(255,255,255,0.1);">
        <h4 style="color: #f5f7fb; margin: 0;">📌 01 Generelt</h4>
    </div>
    """, unsafe_allow_html=True)
    
    land_options = ["Norge (TEK17 / Kartverket)", "Sverige (BBR)", "Danmark (BR18)", "UK (Building Regs)"]
    try: l_idx = land_options.index(pd["land"])
    except: l_idx = 0
    new_land = st.selectbox("🌍 Land / Lokalt Regelverk", land_options, index=l_idx)
    
    c1, c2 = st.columns(2)
    new_p_name = c1.text_input("Prosjektnavn", value=pd["p_name"], placeholder="F.eks. Fjordbyen Kontorpark")
    new_c_name = c2.text_input("Tiltakshaver / Oppdragsgiver", value=pd["c_name"], placeholder="F.eks. Eiendomsutvikling AS")
    
    new_p_desc = st.text_area(
        "Prosjektbeskrivelse / Narrativ", 
        value=pd["p_desc"], height=140,
        placeholder="Beskriv prosjektet kort her..."
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # --- SEKSJON 2: LOKASJON ---
    st.markdown("""
    <div style="margin-bottom: 1rem; padding-bottom: 0.5rem; border-bottom: 1px solid rgba(255,255,255,0.1);">
        <h4 style="color: #f5f7fb; margin: 0;">🌍 02 Lokasjon & API</h4>
    </div>
    """, unsafe_allow_html=True)
    
    if "Norge" in new_land:
        st.info("💡 **Kartverket API:** Skriv inn adresse *eller* Gnr/Bnr og trykk på knappen for å autoutfylle resten.")
        
    c3, c4 = st.columns(2)
    new_adresse = c3.text_input("Gateadresse", value=pd["adresse"])
    new_kommune = c4.text_input("Kommune", value=pd["kommune"])
    
    c5, c6 = st.columns(2)
    new_gnr = c5.text_input("Gårdsnummer (Gnr)", value=pd["gnr"])
    new_bnr = c6.text_input("Bruksnummer (Bnr)", value=pd["bnr"])
    
    if "Norge" in new_land:
        if st.button("🔍 Søk i Matrikkel (Kartverket)", type="secondary"):
            # Mellomlagrer dataene du nettopp tastet inn slik at de ikke slettes ved oppdatering
            pd.update({"p_name": new_p_name, "c_name": new_c_name, "p_desc": new_p_desc})
            
            with st.spinner("Henter fra Nasjonalt Adresseregister..."):
                res = fetch_from_kartverket(new_adresse, new_kommune, new_gnr, new_bnr)
                if res:
                    # Oppdaterer minnet direkte med fasit fra Kartverket
                    pd["adresse"] = res['adresse']
                    pd["kommune"] = res['kommune']
                    pd["gnr"] = res['gnr']
                    pd["bnr"] = res['bnr']
                    st.success(f"✅ Fant eiendom: {res['adresse']}, {res['kommune']} (Gnr {res['gnr']}/Bnr {res['bnr']})")
                    st.rerun() # Tvinger siden til å oppdatere tekstfeltene med de nye verdiene
                else:
                    st.warning("Fant ingen treff i Matrikkelen. Sjekk skrivemåten.")

    st.markdown("<br>", unsafe_allow_html=True)

    # --- SEKSJON 3: BYGGDATA ---
    st.markdown("""
    <div style="margin-bottom: 1rem; padding-bottom: 0.5rem; border-bottom: 1px solid rgba(255,255,255,0.1);">
        <h4 style="color: #f5f7fb; margin: 0;">🏢 03 Byggdata</h4>
    </div>
    """, unsafe_allow_html=True)
    
    c7, c8, c9 = st.columns(3)
    type_options = ["Bolig (Blokk/Rekkehus)", "Næring / Kontor", "Handel / Kjøpesenter", "Offentlig / Skole", "Industri / Lager"]
    try: default_idx = type_options.index(pd["b_type"])
    except: default_idx = 1
    
    new_b_type = c7.selectbox("Primær Bruk", type_options, index=default_idx)
    new_etasjer = c8.number_input("Antall Etasjer", value=int(pd["etasjer"]), min_value=1)
    new_bta = c9.number_input("Bruttoareal (BTA m²)", value=int(pd["bta"]), step=100)

    st.markdown("<br>", unsafe_allow_html=True)
    
    # --- LAGRE ---
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
        
        <div class="snap-row"><div class="snap-label">Regelverk</div><div class="snap-val" style="color:var(--accent);">{pd["land"].split(' ')[0]}</div></div>
        <div class="snap-row"><div class="snap-label">Prosjekt</div><div class="snap-val">{pd["p_name"] or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Oppdragsgiver</div><div class="snap-val">{pd["c_name"] or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Adresse</div><div class="snap-val">{pd["adresse"] or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Kommune</div><div class="snap-val">{pd["kommune"] or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Gnr / Bnr</div><div class="snap-val">{' / '.join(filter(None, [pd["gnr"], pd["bnr"]])) or '-'}</div></div>
        <div class="snap-row"><div class="snap-label">Type</div><div class="snap-val">{pd["b_type"]}</div></div>
        <div class="snap-row" style="border-bottom:none;"><div class="snap-label">Volum</div><div class="snap-val">{pd["etasjer"]} etg / {pd["bta"]} m²</div></div>
    </div>
    """)

# --- 8. LAUNCHPAD (Neste steg) ---
if completeness > 30:
    st.markdown("<hr style='border-color: rgba(120,145,170,0.2); margin-top: 3rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
    st.markdown("<h3 style='text-align:center; margin-bottom: 1.5rem;'>🚀 Prosjektet er synkronisert! Hvor vil du starte?</h3>", unsafe_allow_html=True)
    
    lp1, lp2, lp3 = st.columns(3)
    
    with lp1: 
        if find_page("Mulighetsstudie"): st.page_link(find_page("Mulighetsstudie"), label="📐 Mulighetsstudie")
        if find_page("Geo"): st.page_link(find_page("Geo"), label="🌍 Geo & Miljø")
    with lp2:
        if find_page("Akustikk"): st.page_link(find_page("Akustikk"), label="🔊 Akustikk")
        if find_page("Brannkonsept"): st.page_link(find_page("Brannkonsept"), label="🔥 Brannkonsept")
    with lp3:
        if find_page("Konstruksjon"): st.page_link(find_page("Konstruksjon"), label="🏢 Konstruksjon (RIB)")
        if find_page("Trafikk"): st.page_link(find_page("Trafikk"), label="🚦 Trafikk & Mobilitet")
