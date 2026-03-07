import streamlit as st
import os
import base64
from pathlib import Path

# --- 1. GRUNNINNSTILLINGER ---
st.set_page_config(
    page_title="Builtly | Engineering Portal",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# --- 2. SESSION STATE LOGIKK (GLOBAL HJERNEN) ---
# Setter opp standard prosjektdata hvis det ikke finnes
if "project_data" not in st.session_state:
    st.session_state.project_data = {
        "land": "Norge (TEK17 / Kartverket)",
        "p_name": "", "c_name": "", "p_desc": "",
        "adresse": "", "kommune": "", "gnr": "", "bnr": "",
        "b_type": "Næring / Kontor", "etasjer": 4, "bta": 2500,
        "last_sync": "Ikke synket enda"
    }

# Laster valgt språk for plattformen (Standard: Norsk)
if "app_lang" not in st.session_state:
    st.session_state.app_lang = "🇳🇴 Norsk"

# --- 3. OVERSETTELSESMOTOR (i18n) ---
# Her definerer vi språkene og hvilke regelverk som automatisk skal kobles til dem
TEXTS = {
    "🇳🇴 Norsk": {
        "rule_set": "Norge (TEK17 / Kartverket)",
        "eyebrow": "Builtly Arbeidsflyt",
        "title": "Fra <span class='accent'>rådata</span> til signerte leveranser.",
        "subtitle": "Builtly er kundeportalen for teknisk rådgivning. Last opp prosjektdata, la AI validere, beregne og utarbeide rapporten – før junior-QA og senior-signering gjør det klart til innsending.",
        "btn_setup": "Start i Project Setup",
        "btn_qa": "Åpne QA & Sign-off",
        "proofs": ["Regelstyrt AI", "Revisjonsspor", "PDF + DOCX", "Digital Signering", "Strukturert QA"],
        "stat1_t": "Tidsbesparelse", "stat1_v": "80-90%", "stat1_d": "Reduksjon i manuelt rapportarbeid",
        "stat2_t": "Kvalitetssikring", "stat2_v": "Human-in-the-loop", "stat2_d": "Digital QA og signering av fagansvarlig",
        "stat3_t": "Dokumentasjon", "stat3_v": "Sporbarhet", "stat3_d": "Versjonskontroll fra input til PDF",
        "sec1_kicker": "Kjerneprodukt", "sec1_title": "Portal først. Moduler under.",
        "sec1_sub": "Builtly er ett felles system for prosjektoppsett, AI-prosessering og faglig kvalitetssikring.",
        "dev": "Under utvikling"
    },
    "🇸🇪 Svensk": {
        "rule_set": "Sverige (BBR)",
        "eyebrow": "Builtly Arbetsflöde",
        "title": "Från <span class='accent'>rådata</span> till signerade leveranser.",
        "subtitle": "Builtly är kundportalen för teknisk rådgivning. Ladda upp projektdata, låt AI validera och utarbeta rapporten – innan junior-QA och senior-signering gör det klart för inlämning.",
        "btn_setup": "Starta i Project Setup",
        "btn_qa": "Öppna QA & Sign-off",
        "proofs": ["Regelstyrd AI", "Revisionsspår", "PDF + DOCX", "Digital Signering", "Strukturerad QA"],
        "stat1_t": "Tidsbesparing", "stat1_v": "80-90%", "stat1_d": "Minskning av manuellt rapportarbete",
        "stat2_t": "Kvalitetssäkring", "stat2_v": "Human-in-the-loop", "stat2_d": "Digital QA och signering av ansvarig",
        "stat3_t": "Dokumentation", "stat3_v": "Spårbarhet", "stat3_d": "Versionshantering från input till PDF",
        "sec1_kicker": "Kärnprodukt", "sec1_title": "Portal först. Moduler under.",
        "sec1_sub": "Builtly är ett gemensamt system för projektuppsättning, AI-bearbetning och kvalitetssäkring.",
        "dev": "Under utveckling"
    },
    "🇩🇰 Dansk": {
        "rule_set": "Danmark (BR18)",
        "eyebrow": "Builtly Workflow",
        "title": "Fra <span class='accent'>rådata</span> til underskrevne leverancer.",
        "subtitle": "Builtly er kundeportalen for teknisk rådgivning. Upload projektdata, lad AI validere og udarbejde rapporten – før junior-QA og senior-signering gør det klar.",
        "btn_setup": "Start i Project Setup",
        "btn_qa": "Åbn QA & Sign-off",
        "proofs": ["Regelstyret AI", "Revisionsspor", "PDF + DOCX", "Digital Signatur", "Struktureret QA"],
        "stat1_t": "Tidsbesparelse", "stat1_v": "80-90%", "stat1_d": "Reduktion af manuelt rapportarbejde",
        "stat2_t": "Kvalitetssikring", "stat2_v": "Human-in-the-loop", "stat2_d": "Digital QA og signering af fagansvarlig",
        "stat3_t": "Dokumentation", "stat3_v": "Sporbarhed", "stat3_d": "Versionskontrol fra input til PDF",
        "sec1_kicker": "Kerneprodukt", "sec1_title": "Portal først. Moduler under.",
        "sec1_sub": "Builtly er et fælles system for projektoprettelse, AI-behandling og kvalitetssikring.",
        "dev": "Under udvikling"
    },
    "🇬🇧 English": {
        "rule_set": "UK (Building Regs)",
        "eyebrow": "The Builtly Loop",
        "title": "From <span class='accent'>raw data</span> to signed deliverables.",
        "subtitle": "Builtly is the customer portal for compliance-grade engineering. Upload project inputs, let AI validate and draft the report - before human QA makes it submission-ready.",
        "btn_setup": "Start in Project Setup",
        "btn_qa": "Open QA & Sign-off",
        "proofs": ["Rules-first AI", "Audit trail", "PDF + DOCX", "Digital sign-off", "Structured QA"],
        "stat1_t": "Time Saved", "stat1_v": "80-90%", "stat1_d": "Reduction in manual reporting",
        "stat2_t": "Quality Control", "stat2_v": "Human-in-the-loop", "stat2_d": "Digital QA and senior sign-off",
        "stat3_t": "Documentation", "stat3_v": "Traceability", "stat3_d": "Version control from input to PDF",
        "sec1_kicker": "Core Value", "sec1_title": "Portal first. Modules under.",
        "sec1_sub": "Builtly is one secure portal for project setup, AI processing, and professional review.",
        "dev": "In development"
    }
}

# Sett aktivt språk
lang = TEXTS.get(st.session_state.app_lang, TEXTS["🇳🇴 Norsk"])

# --- 4. HJELPERE OG ANTI-BUG ---
def render_html(html_string: str):
    st.markdown(html_string.replace('\n', ' '), unsafe_allow_html=True)

def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{name}.py")
        if p.exists(): return str(p)
    return f"pages/{base_name}.py"

def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            suffix = Path(candidate).suffix.lower().replace(".", "") or "png"
            with open(candidate, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/{suffix};base64,{encoded}"
    return ""

# --- 5. PREMIUM CSS (Samme lekre stil som modulene) ---
st.markdown("""
<style>
    :root {
        --bg: #06111a; --panel: rgba(10, 22, 35, 0.78); --panel-2: rgba(13, 27, 42, 0.94);
        --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38c2c9; --accent-2: #78dce1; --radius-xl: 28px; --radius-lg: 22px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp {
        background: radial-gradient(1100px 500px at 15% -5%, rgba(56,194,201,0.18), transparent 50%),
                    radial-gradient(900px 500px at 100% 0%, rgba(64,170,255,0.12), transparent 45%),
                    linear-gradient(180deg, #071018 0%, #08131d 35%, #071018 100%);
        color: var(--text);
    }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1300px !important; padding-top: 1.35rem !important; padding-bottom: 4rem !important; }

    /* NATIVE SELECTBOX FOR SPRÅK PÅ TOPPEN */
    [data-testid="stSelectbox"] { margin-bottom: 0 !important; }
    [data-testid="stSelectbox"] > div > div {
        background-color: rgba(255,255,255,0.05) !important; color: #f5f7fb !important;
        border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important;
        min-height: 42px !important; padding-left: 10px !important;
    }
    [data-testid="stSelectbox"] > div > div:hover { border-color: var(--accent) !important; }
    
    /* KNAPPER */
    button[kind="primary"] {
        background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important;
        color: #041018 !important; border: none !important; font-weight: 750 !important;
        border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    
    button[kind="secondary"] {
        background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important;
        border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; 
        font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s;
    }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important;}

    /* LAYOUT */
    .brand-logo { height: 75px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    
    .hero {
        position: relative; overflow: hidden; background: linear-gradient(180deg, rgba(13,27,42,0.96), rgba(8,18,28,0.96));
        border: 1px solid rgba(120,145,170,0.16); border-radius: var(--radius-xl); padding: 3rem;
        box-shadow: 0 24px 90px rgba(0,0,0,0.35); margin-bottom: 1.25rem; min-height: 520px;
        display: flex; flex-direction: column; justify-content: center;
    }
    .hero::before {
        content: ""; position: absolute; inset: -80px -120px auto auto; width: 420px; height: 420px;
        background: radial-gradient(circle, rgba(56,194,201,0.16) 0%, transparent 62%); pointer-events: none;
    }
    .hero-panel {
        background: rgba(20, 35, 50, 0.4); border: 1px solid var(--stroke); border-radius: var(--radius-xl);
        padding: 2.5rem; min-height: 520px; display: flex; flex-direction: column;
    }

    .eyebrow { color: var(--accent-2); text-transform: uppercase; letter-spacing: 0.14em; font-size: 0.78rem; font-weight: 700; margin-bottom: 1rem; }
    .hero-title { font-size: clamp(2.55rem, 5vw, 4.35rem); line-height: 1.05; letter-spacing: -0.03em; font-weight: 800; margin: 0 0 1rem 0; color: var(--text); }
    .hero-title .accent { color: var(--accent-2); }
    .hero-subtitle { max-width: 58ch; font-size: 1.08rem; line-height: 1.8; color: var(--soft); margin-bottom: 2rem; }
    
    .proof-strip { display: flex; flex-wrap: wrap; gap: 0.55rem; margin-top: 2rem; }
    .proof-chip { display: inline-flex; align-items: center; padding: 0.42rem 0.8rem; border-radius: 999px; background: rgba(255,255,255,0.04); border: 1px solid rgba(120,145,170,0.16); color: var(--soft); font-size: 0.82rem; }

    .mini-stat { background: rgba(255,255,255,0.02); border: 1px solid var(--stroke); border-radius: 16px; padding: 1.1rem 1.2rem; margin-bottom: 0.8rem; flex: 1; display: flex; flex-direction: column; justify-content: center; }
    .mini-stat-value { font-size: 1.35rem; font-weight: 750; color: var(--text); line-height: 1.1; }
    .mini-stat-label { margin-top: 0.25rem; color: var(--muted); font-size: 0.88rem; line-height: 1.5; }

    /* MODULE CARDS (Med hook for Streamlit-knapper) */
    .module-badge { display: inline-flex; align-items: center; justify-content: center; padding: 0.32rem 0.62rem; border-radius: 999px; border: 1px solid rgba(120,145,170,0.18); background: rgba(255,255,255,0.03); color: var(--muted); font-size: 0.75rem; font-weight: 650; }
    .badge-priority { color: #8ef0c0; border-color: rgba(142,240,192,0.25); background: rgba(126,224,129,0.08); }
    .badge-phase2 { color: #9fe7ff; border-color: rgba(120,220,225,0.22); background: rgba(56,194,201,0.08); }
    .badge-early { color: #d7def7; border-color: rgba(215,222,247,0.18); background: rgba(255,255,255,0.03); }
    .badge-roadmap { color: #f4bf4f; border-color: rgba(244,191,79,0.22); background: rgba(244,191,79,0.08); }

    .module-icon { width: 46px; height: 46px; border-radius: 14px; display: inline-flex; align-items: center; justify-content: center; background: rgba(56,194,201,0.1); border: 1px solid rgba(56,194,201,0.18); font-size: 1.32rem; flex-shrink: 0; }

    [data-testid="column"]:has(.module-card-hook) { background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98)) !important; border: 1px solid rgba(120, 145, 170, 0.18) !important; border-radius: 22px !important; padding: 1.5rem !important; box-shadow: 0 12px 38px rgba(0,0,0,0.18) !important; transition: all 0.2s ease !important; margin-bottom: 1rem !important; }
    [data-testid="column"]:has(.module-card-hook):hover { border-color: rgba(56,194,201,0.24) !important; box-shadow: 0 16px 42px rgba(0,0,0,0.24) !important; transform: translateY(-2px); }
    [data-testid="column"]:has(.module-card-hook) > div { height: 100% !important; display: flex !important; flex-direction: column !important; }
    [data-testid="column"]:has(.module-card-hook) [data-testid="stButton"] { margin-top: auto !important; width: 100% !important; padding-top: 1rem; }
    [data-testid="column"]:has(.module-card-hook) button[kind="secondary"] { background: rgba(56,194,201,0.1) !important; border: 1px solid rgba(56,194,201,0.28) !important; color: #f5f7fb !important; border-radius: 12px !important; min-height: 46px !important; font-weight: 650 !important; width: 100% !important; }
    [data-testid="column"]:has(.module-card-hook) button[kind="secondary"]:hover { border-color: rgba(56,194,201,0.8) !important; background: rgba(56,194,201,0.2) !important; }
</style>
""", unsafe_allow_html=True)

# --- 6. TOPPMENY MED SPRÅKVELGER ---
c_logo, c_space, c_lang, c_nav = st.columns([3, 1, 1, 1.2])

with c_logo:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(logo_html)

with c_lang:
    st.markdown("<div style='margin-top: 0.8rem;'></div>", unsafe_allow_html=True)
    # SPRÅKVELGER: Endrer app_lang og oppdaterer SSOT-regelverket automatisk!
    valgt_språk = st.selectbox(
        "Språk", 
        list(TEXTS.keys()), 
        index=list(TEXTS.keys()).index(st.session_state.app_lang),
        label_visibility="collapsed"
    )
    if valgt_språk != st.session_state.app_lang:
        st.session_state.app_lang = valgt_språk
        # OPPDATERER SSOT AUTOMATISK BASERT PÅ SPRÅK/FLAGG:
        st.session_state.project_data["land"] = TEXTS[valgt_språk]["rule_set"]
        st.rerun()

with c_nav:
    st.markdown("<div style='margin-top: 0.8rem;'></div>", unsafe_allow_html=True)
    if st.button("QA & Sign-off", type="primary", use_container_width=True):
        st.switch_page(find_page("Review"))

# --- 7. HERO SEKSJON ---
left, right = st.columns([1.2, 0.8], gap="large")

with left:
    st.markdown(f"""
    <div class="hero">
        <div class="eyebrow">{lang["eyebrow"]}</div>
        <h1 class="hero-title">{lang["title"]}</h1>
        <div class="hero-subtitle">{lang["subtitle"]}</div>
    """, unsafe_allow_html=True)
    
    # Native knapper for routing
    hc1, hc2 = st.columns([1, 1])
    with hc1:
        if st.button(lang["btn_setup"], type="primary", use_container_width=True): st.switch_page(find_page("Project"))
    with hc2:
        if st.button(lang["btn_qa"], type="secondary", use_container_width=True): st.switch_page(find_page("Review"))
    
    st.markdown(f"""
        <div class="proof-strip">
            {"".join([f'<div class="proof-chip">{p}</div>' for p in lang["proofs"]])}
        </div>
    </div>
    """, unsafe_allow_html=True)

with right:
    render_html(f"""
    <div class="hero-panel">
        <div class="mini-stat"><div class="mini-stat-value">{lang["stat1_v"]}</div><div class="mini-stat-label"><b>{lang["stat1_t"]}</b><br>{lang["stat1_d"]}</div></div>
        <div class="mini-stat"><div class="mini-stat-value">{lang["stat2_v"]}</div><div class="mini-stat-label"><b>{lang["stat2_t"]}</b><br>{lang["stat2_d"]}</div></div>
        <div class="mini-stat"><div class="mini-stat-value">{lang["stat3_v"]}</div><div class="mini-stat-label"><b>{lang["stat3_t"]}</b><br>{lang["stat3_d"]}</div></div>
    </div>
    """)

# --- 8. MODULER ---
st.markdown(f"""
<div style="margin-top: 3rem; margin-bottom: 1.5rem;">
    <div style="color: var(--accent-2); text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.74rem; font-weight: 700; margin-bottom: 0.4rem;">{lang["sec1_kicker"]}</div>
    <h2 style="font-size: 1.86rem; font-weight: 750; letter-spacing: -0.03em; color: var(--text); margin: 0;">{lang["sec1_title"]}</h2>
    <div style="margin-top: 0.35rem; color: var(--muted); line-height: 1.75;">{lang["sec1_sub"]}</div>
</div>
""", unsafe_allow_html=True)

def render_module_card(col, icon, badge, badge_class, title, desc, input_txt, output_txt, btn_label, page_target):
    with col:
        st.markdown('<div class="module-card-hook"></div>', unsafe_allow_html=True)
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
        
        if page_target and find_page(page_target):
            if st.button(btn_label, key=f"btn_{page_target}", type="secondary", use_container_width=True):
                st.switch_page(find_page(page_target))
        else:
            st.button(lang["dev"], key=f"btn_{page_target}_dev", type="secondary", disabled=True, use_container_width=True)

r1c1, r1c2, r1c3 = st.columns(3)
render_module_card(r1c1, "🌍", "Phase 1 - Priority", "badge-priority", "GEO / ENV - Ground Conditions", "Analyze lab files and excavation plans. Classifies masses, proposes disposal logic.", "XLSX / CSV / PDF", "Environmental action plan", "Open Geo & Env", "Geo")
render_module_card(r1c2, "🔊", "Phase 2", "badge-phase2", "ACOUSTICS - Noise & Sound", "Ingest noise maps and floor plans. Generates facade requirements and mitigation.", "Noise map + floor plan", "Acoustics report", "Open Acoustics", "Akustikk")
render_module_card(r1c3, "🔥", "Phase 2", "badge-phase2", "FIRE - Safety Strategy", "Evaluate architectural drawings against building codes. Generates escape routes.", "Arch drawings + class", "Fire strategy concept", "Open Fire Strategy", "Brannkonsept")

st.markdown("<br>", unsafe_allow_html=True)

r2c1, r2c2, r2c3 = st.columns(3)
render_module_card(r2c1, "📐", "Early phase", "badge-early", "ARK - Feasibility Study", "Site screening, volume analysis, and early-phase decision support.", "Site data, zoning plans", "Feasibility report", "Open Feasibility", "Mulighetsstudie")
render_module_card(r2c2, "🏢", "Roadmap", "badge-roadmap", "STRUC - Structural Concept", "Conceptual structural checks, principle dimensioning, and integration with carbon.", "Models, loads", "Concept memo", "Open Structural", "Konstruksjon")
render_module_card(r2c3, "🚦", "Roadmap", "badge-roadmap", "TRAFFIC - Mobility", "Traffic generation, parking requirements, access logic, and soft-mobility planning.", "Site plans, local norms", "Traffic memo", "Open Traffic", "Trafikk")
