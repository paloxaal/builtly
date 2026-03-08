import os
import base64
from pathlib import Path
import streamlit as st

# -------------------------------------------------
# 1) PAGE CONFIG & SESSION STATE
# -------------------------------------------------
st.set_page_config(
    page_title="Builtly | Engineering Portal",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Standard prosjektdata i hjernen (SSOT)
if "project_data" not in st.session_state:
    st.session_state.project_data = {
        "land": "Norge (TEK17 / Kartverket)", "p_name": "", "c_name": "", "p_desc": "",
        "adresse": "", "kommune": "", "gnr": "", "bnr": "",
        "b_type": "Næring / Kontor", "etasjer": 4, "bta": 2500,
        "last_sync": "Ikke synket enda"
    }

# Laster valgt språk (Standard: Norsk)
if "app_lang" not in st.session_state:
    st.session_state.app_lang = "🇳🇴 Norsk"

# -------------------------------------------------
# 2) OVERSETTELSESMOTOR (i18n)
# -------------------------------------------------
TEXTS = {
    "🇳🇴 Norsk": {
        "rule_set": "Norge (TEK17 / Kartverket)",
        "eyebrow": "Builtly Arbeidsflyt",
        "title": "Fra <span class='accent'>rådata</span> til signerte leveranser.",
        "subtitle": "Builtly er kundeportalen for teknisk rådgivning. Last opp prosjektdata, la AI validere, beregne og utarbeide rapporten – før junior-QA og senior-signering gjør det klart til innsending.",
        "btn_setup": "Start i Project Setup",
        "btn_qa": "Åpne QA & Sign-off",
        "proofs": ["Regelstyrt AI", "Revisjonsspor", "PDF + DOCX", "Digital Signering", "Strukturert QA"],
        "stat1_v": "80-90%", "stat1_t": "Tidsbesparelse", "stat1_d": "Reduksjon i manuelt rapportarbeid",
        "stat2_v": "Junior + Senior", "stat2_t": "Kvalitetssikring", "stat2_d": "Digital QA og signering av fagansvarlig",
        "stat3_v": "Sporbarhet", "stat3_t": "Dokumentasjon", "stat3_d": "Versjonskontroll fra input til PDF",
        
        "sec_val_kicker": "Kjerneprodukt", "sec_val_title": "Portal først. Moduler under.", "sec_val_sub": "Builtly er ett felles system for prosjektoppsett, AI-prosessering og faglig kvalitetssikring.",
        "val_1_t": "Kundeportal", "val_1_d": "Opprettelse, input, oppfølging og dokumentgenerering i én flyt.",
        "val_2_t": "Regelstyrt AI", "val_2_d": "AI opererer innenfor eksplisitte lovkrav og standardmaler.",
        "val_3_t": "QA og Signering", "val_3_d": "Junior validerer struktur. Senior gir endelig teknisk godkjenning.",
        "val_4_t": "Skalerbarhet", "val_4_d": "Nye fagfelt plugges direkte inn i samme dokumentasjonsrammeverk.",
        
        "sec_loop_kicker": "Arbeidsflyt", "sec_loop_title": "Slik fungerer Builtly", "sec_loop_sub": "En deterministisk fire-stegs prosess som tar deg fra rådata til en ferdig pakke.",
        "loop_1_t": "Input", "loop_1_d": "Last opp PDF, IFC, Excel og prosjektdata på ett sted.",
        "loop_2_t": "AI Analyse", "loop_2_d": "Plattformen validerer, sjekker regelverk og skriver utkastet.",
        "loop_3_t": "QA & Signering", "loop_3_d": "Junior-sjekk og digital signering fra senioringeniør.",
        "loop_4_t": "Output", "loop_4_d": "Ferdig dokumentpakke klar for byggesøknad.",
        
        "mod_sec1": "Tilgjengelig nå", "mod_sec2": "Veikart og tidligfase",
        "m_geo_t": "GEO / MILJØ - Grunnforhold", "m_geo_d": "Analyserer lab-filer og graveceller. Klassifiserer masser og utarbeider tiltaksplaner.", "m_geo_in": "XLSX / CSV + Kart", "m_geo_out": "Tiltaksplan, logg", "m_geo_btn": "Åpne Geo & Miljø",
        "m_aku_t": "AKUSTIKK - Støy & Lyd", "m_aku_d": "Leser støykart og plantegninger. Genererer krav til fasade, vinduer og skjerming.", "m_aku_in": "Støykart + Plan", "m_aku_out": "Akustikkrapport", "m_aku_btn": "Åpne Akustikk",
        "m_brann_t": "BRANN - Sikkerhetskonsept", "m_brann_d": "Vurderer arkitektur mot forskrifter. Definerer rømning og brannceller.", "m_brann_in": "Tegninger + Klasse", "m_brann_out": "Brannkonsept (RIBr)", "m_brann_btn": "Åpne Brannkonsept",
        "m_ark_t": "ARK - Mulighetsstudie", "m_ark_d": "Tomteanalyse, volumvurdering og beslutningsgrunnlag for tidligfase.", "m_ark_in": "Regulering + Tomt", "m_ark_out": "Mulighetsstudie", "m_ark_btn": "Åpne ARK",
        "m_rib_t": "RIB - Konstruksjon", "m_rib_d": "Konseptuelle struktursjekker, spennvidder og bygningsfysikk.", "m_rib_in": "Snitt + Laster", "m_rib_out": "Konseptnotat RIB", "m_rib_btn": "Åpne Konstruksjon",
        "m_tra_t": "TRAFIKK - Mobilitet", "m_tra_d": "Trafikkgenerering, parkering, adkomstlogikk og myke trafikanter.", "m_tra_in": "Situasjonsplan", "m_tra_out": "Trafikknotat", "m_tra_btn": "Åpne Trafikk",
        "dev": "Under utvikling",
        
        "cta_title": "Start med ett prosjekt. Last opp data. Få et ferdig utkast.",
        "cta_desc": "Builtly kombinerer data-innsamling, deterministiske regler, AI og faglig signering i én portal.",
        "footer": "© 2026 Builtly Engineering AS. All rights reserved."
    },
    "🇸🇪 Svensk": {
        "rule_set": "Sverige (BBR)",
        "eyebrow": "Builtly Arbetsflöde",
        "title": "Från <span class='accent'>rådata</span> till signerade leveranser.",
        "subtitle": "Builtly är kundportalen för teknisk rådgivning. Ladda upp projektdata, låt AI validera och utarbeta rapporten – innan junior-QA och senior-signering gör det klart för inlämning.",
        "btn_setup": "Starta i Project Setup",
        "btn_qa": "Öppna QA & Sign-off",
        "proofs": ["Regelstyrd AI", "Revisionsspår", "PDF + DOCX", "Digital Signering", "Strukturerad QA"],
        "stat1_v": "80-90%", "stat1_t": "Tidsbesparing", "stat1_d": "Minskning av manuellt rapportarbete",
        "stat2_v": "Junior + Senior", "stat2_t": "Kvalitetssäkring", "stat2_d": "Digital QA och signering av ansvarig",
        "stat3_v": "Spårbarhet", "stat3_t": "Dokumentation", "stat3_d": "Versionshantering från input till PDF",
        
        "sec_val_kicker": "Kärnprodukt", "sec_val_title": "Portal först. Moduler under.", "sec_val_sub": "Builtly är ett gemensamt system för projektuppsättning, AI-bearbetning och kvalitetssäkring.",
        "val_1_t": "Kundportal", "val_1_d": "Upprättande, input och dokumentgenerering i ett flöde.",
        "val_2_t": "Regelstyrd AI", "val_2_d": "AI arbetar inom strikta lagkrav och mallar.",
        "val_3_t": "QA och Signering", "val_3_d": "Junior validerar. Senior ger slutgiltigt godkännande.",
        "val_4_t": "Skalbarhet", "val_4_d": "Nya discipliner ansluts till samma ramverk.",
        
        "sec_loop_kicker": "Arbetsflöde", "sec_loop_title": "Så fungerar Builtly", "sec_loop_sub": "En deterministisk fyra-stegs process.",
        "loop_1_t": "Input", "loop_1_d": "Ladda upp filer och data på ett ställe.",
        "loop_2_t": "AI Analys", "loop_2_d": "Plattformen kontrollerar regelverk och skriver utkast.",
        "loop_3_t": "QA & Signering", "loop_3_d": "Granskning och digital signering.",
        "loop_4_t": "Output", "loop_4_d": "Färdigt dokument för bygglov.",
        
        "mod_sec1": "Tillgängligt nu", "mod_sec2": "Roadmap och tidiga skeden",
        "m_geo_t": "GEO / MILJÖ - Markförhållanden", "m_geo_d": "Analyserar labbfiler. Klassificerar massor och åtgärdsplaner.", "m_geo_in": "XLSX / CSV + Karta", "m_geo_out": "Åtgärdsplan", "m_geo_btn": "Öppna Geo",
        "m_aku_t": "AKUSTIK - Buller & Ljud", "m_aku_d": "Läser bullerkartor och planritningar. Genererar fasadkrav.", "m_aku_in": "Bullerkarta + Plan", "m_aku_out": "Akustikrapport", "m_aku_btn": "Öppna Akustik",
        "m_brann_t": "BRAND - Säkerhetskoncept", "m_brann_d": "Utvärderar arkitektur mot BBR. Definierar brandceller.", "m_brann_in": "Ritningar + Klass", "m_brann_out": "Brandkoncept", "m_brann_btn": "Öppna Brand",
        "m_ark_t": "ARK - Förstudie", "m_ark_d": "Tomtanalys och volymbedömning för tidiga skeden.", "m_ark_in": "Detaljplan + Tomt", "m_ark_out": "Förstudie", "m_ark_btn": "Öppna ARK",
        "m_rib_t": "K - Konstruktion", "m_rib_d": "Konceptuella strukturkontroller och byggfysik.", "m_rib_in": "Sektion + Laster", "m_rib_out": "Koncept-PM", "m_rib_btn": "Öppna Konstruktion",
        "m_tra_t": "TRAFIK - Mobilitet", "m_tra_d": "Trafikalstring, parkering och logistik.", "m_tra_in": "Situationsplan", "m_tra_out": "Trafik-PM", "m_tra_btn": "Öppna Trafik",
        "dev": "Under utveckling",
        
        "cta_title": "Starta ett projekt. Ladda upp data. Få ett utkast.",
        "cta_desc": "Builtly kombinerar insamling, AI och professionell signering i en portal.",
        "footer": "© 2026 Builtly Engineering AS. All rights reserved."
    },
    "🇩🇰 Dansk": {
        "rule_set": "Danmark (BR18)",
        "eyebrow": "Builtly Workflow",
        "title": "Fra <span class='accent'>rådata</span> til underskrevne leverancer.",
        "subtitle": "Builtly er kundeportalen for teknisk rådgivning. Upload projektdata, lad AI validere og udarbejde rapporten – før junior-QA og senior-signering gør det klar.",
        "btn_setup": "Start i Project Setup",
        "btn_qa": "Åbn QA & Sign-off",
        "proofs": ["Regelstyret AI", "Revisionsspor", "PDF + DOCX", "Digital Signatur", "Struktureret QA"],
        "stat1_v": "80-90%", "stat1_t": "Tidsbesparelse", "stat1_d": "Reduktion af manuelt rapportarbejde",
        "stat2_v": "Junior + Senior", "stat2_t": "Kvalitetssikring", "stat2_d": "Digital QA og signering af fagansvarlig",
        "stat3_v": "Sporbarhed", "stat3_t": "Dokumentation", "stat3_d": "Versionskontrol fra input til PDF",
        
        "sec_val_kicker": "Kerneprodukt", "sec_val_title": "Portal først. Moduler under.", "sec_val_sub": "Builtly er et fælles system for projektoprettelse, AI-behandling og kvalitetssikring.",
        "val_1_t": "Kundeportal", "val_1_d": "Oprettelse, input og dokumentgenerering i ét flow.",
        "val_2_t": "Regelstyret AI", "val_2_d": "AI opererer inden for eksplicitte lovkrav og skabeloner.",
        "val_3_t": "QA og Signering", "val_3_d": "Junior validerer. Senior giver endelig godkendelse.",
        "val_4_t": "Skalerbarhed", "val_4_d": "Nye fagområder tilsluttes samme rammeværk.",
        
        "sec_loop_kicker": "Arbejdsgang", "sec_loop_title": "Sådan fungerer Builtly", "sec_loop_sub": "En deterministisk fire-trins proces.",
        "loop_1_t": "Input", "loop_1_d": "Upload filer og data ét sted.",
        "loop_2_t": "AI Analyse", "loop_2_d": "Platformen tjekker bygningsreglement og skriver udkast.",
        "loop_3_t": "QA & Signering", "loop_3_d": "Gennemgang og digital signatur.",
        "loop_4_t": "Output", "loop_4_d": "Færdigt dokument til byggetilladelse.",
        
        "mod_sec1": "Tilgængelig nu", "mod_sec2": "Roadmap",
        "m_geo_t": "GEO / MILJØ", "m_geo_d": "Analyserer lab-filer og udarbejder miljøhandlingsplaner.", "m_geo_in": "XLSX / CSV + Kort", "m_geo_out": "Handlingsplan", "m_geo_btn": "Åbn Geo",
        "m_aku_t": "AKUSTIK - Støj", "m_aku_d": "Læser støjkort. Genererer krav til facade.", "m_aku_in": "Støjkort + Plan", "m_aku_out": "Akustikrapport", "m_aku_btn": "Åbn Akustik",
        "m_brann_t": "BRAND", "m_brann_d": "Vurderer arkitektur mod BR18. Definerer brandceller.", "m_brann_in": "Tegninger + Klasse", "m_brann_out": "Brandstrategi", "m_brann_btn": "Åbn Brand",
        "m_ark_t": "ARK - Mulighedsstudie", "m_ark_d": "Grundanlyse og volumen for tidlige faser.", "m_ark_in": "Lokalplan + Grund", "m_ark_out": "Mulighedsstudie", "m_ark_btn": "Åbn ARK",
        "m_rib_t": "Konstruktion", "m_rib_d": "Konceptuelle strukturtjek og bygningsfysik.", "m_rib_in": "Snit + Laster", "m_rib_out": "Konceptnotat", "m_rib_btn": "Åbn Konstruktion",
        "m_tra_t": "TRAFIK", "m_tra_d": "Trafikgenerering og parkering.", "m_tra_in": "Situationsplan", "m_tra_out": "Trafiknotat", "m_tra_btn": "Åbn Trafik",
        "dev": "Under udvikling",
        
        "cta_title": "Start et projekt. Upload data. Få et udkast.",
        "cta_desc": "Builtly kombinerer dataindsamling, AI og faglig signering i én portal.",
        "footer": "© 2026 Builtly Engineering AS. All rights reserved."
    },
    "🇬🇧 English": {
        "rule_set": "UK (Building Regs)",
        "eyebrow": "The Builtly Loop",
        "title": "From <span class='accent'>raw data</span> to signed deliverables.",
        "subtitle": "Builtly is the customer portal for compliance-grade engineering. Upload project inputs, let AI validate and draft the report - before human QA makes it submission-ready.",
        "btn_setup": "Start in Project Setup",
        "btn_qa": "Open QA & Sign-off",
        "proofs": ["Rules-first AI", "Audit trail", "PDF + DOCX", "Digital sign-off", "Structured QA"],
        "stat1_v": "80-90%", "stat1_t": "Time Saved", "stat1_d": "Reduction in manual reporting",
        "stat2_v": "Junior + Senior", "stat2_t": "Quality Control", "stat2_d": "Digital QA and senior sign-off",
        "stat3_v": "Traceability", "stat3_t": "Documentation", "stat3_d": "Version control from input to PDF",
        
        "sec_val_kicker": "Core Value", "sec_val_title": "Portal first. Modules under.", "sec_val_sub": "Builtly is one secure portal for project setup, AI processing, and professional review.",
        "val_1_t": "Client portal", "val_1_d": "Project creation, inputs, and generation in one flow.",
        "val_2_t": "Rules-first AI", "val_2_d": "AI operates inside explicit regulatory guardrails.",
        "val_3_t": "QA and sign-off", "val_3_d": "Junior validates structure. Senior provides final review.",
        "val_4_t": "Scalable delivery", "val_4_d": "New disciplines plug into the same backbone.",
        
        "sec_loop_kicker": "Workflow", "sec_loop_title": "The Builtly Loop", "sec_loop_sub": "A deterministic four-step workflow.",
        "loop_1_t": "Input", "loop_1_d": "Upload PDFs and data in one place.",
        "loop_2_t": "AI Analysis", "loop_2_d": "Platform applies local rules and drafts report.",
        "loop_3_t": "QA & Sign-off", "loop_3_d": "Junior review and digital sign-off.",
        "loop_4_t": "Output", "loop_4_d": "Finalized documentation package.",
        
        "mod_sec1": "Available now", "mod_sec2": "Roadmap and early-phase",
        "m_geo_t": "GEO / ENV", "m_geo_d": "Analyze lab files. Proposes disposal logic.", "m_geo_in": "XLSX / CSV + Plans", "m_geo_out": "Action plan", "m_geo_btn": "Open Geo & Env",
        "m_aku_t": "ACOUSTICS", "m_aku_d": "Ingest noise maps. Generates facade requirements.", "m_aku_in": "Noise map + Plan", "m_aku_out": "Acoustics report", "m_aku_btn": "Open Acoustics",
        "m_brann_t": "FIRE STRATEGY", "m_brann_d": "Evaluate drawings against building codes.", "m_brann_in": "Drawings + Class", "m_brann_out": "Fire strategy", "m_brann_btn": "Open Fire",
        "m_ark_t": "ARK - Feasibility", "m_ark_d": "Site screening and volume analysis.", "m_ark_in": "Zoning + Site", "m_ark_out": "Feasibility report", "m_ark_btn": "Open ARK",
        "m_rib_t": "STRUC - Concept", "m_rib_d": "Conceptual structural checks and dimensioning.", "m_rib_in": "Models, loads", "m_rib_out": "Concept memo", "m_rib_btn": "Open Structural",
        "m_tra_t": "TRAFFIC", "m_tra_d": "Traffic generation and parking requirements.", "m_tra_in": "Site plans", "m_tra_out": "Traffic memo", "m_tra_btn": "Open Traffic",
        "dev": "In development",
        
        "cta_title": "Start with one project. Upload raw data.",
        "cta_desc": "Builtly combines customer self-service, deterministic checks, and AI drafts.",
        "footer": "© 2026 Builtly Engineering AS. All rights reserved."
    }
}

lang = TEXTS.get(st.session_state.app_lang, TEXTS["🇳🇴 Norsk"])

# -------------------------------------------------
# 3) HJELPERE
# -------------------------------------------------
def render_html(html_string: str):
    st.markdown(html_string.replace('\n', ' '), unsafe_allow_html=True)

def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{name}.py")
        if p.exists(): return str(p)
    return ""

def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            suffix = Path(candidate).suffix.lower().replace(".", "") or "png"
            with open(candidate, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/{suffix};base64,{encoded}"
    return ""

# -------------------------------------------------
# 4) PREMIUM CSS MED FIX FOR LAYOUT OG KNAPPER
# -------------------------------------------------
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

    .top-shell { display: flex; align-items: center; justify-content: space-between; margin-bottom: 2rem; }
    .brand-logo { height: 75px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }

    /* NATIVE SELECTBOX FOR SPRÅK */
    [data-testid="stSelectbox"] label { display: none !important; }
    [data-testid="stSelectbox"] > div > div {
        background-color: rgba(255,255,255,0.05) !important; color: #f5f7fb !important;
        border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important;
        min-height: 42px !important; padding-left: 10px !important;
    }
    [data-testid="stSelectbox"] > div > div:hover { border-color: var(--accent) !important; }

    /* HERO STYLING (Påføres Streamlit container via :has) */
    [data-testid="column"]:has(.hero-hook) {
        background: linear-gradient(180deg, rgba(13,27,42,0.96), rgba(8,18,28,0.96)) !important;
        border: 1px solid rgba(120,145,170,0.16) !important; border-radius: var(--radius-xl) !important;
        padding: 3rem !important; box-shadow: 0 24px 90px rgba(0,0,0,0.35) !important;
        position: relative; overflow: hidden; display: flex; flex-direction: column; justify-content: center;
        height: 100% !important; min-height: 520px;
    }
    [data-testid="column"]:has(.hero-hook)::before {
        content: ""; position: absolute; inset: -80px -120px auto auto; width: 420px; height: 420px;
        background: radial-gradient(circle, rgba(56,194,201,0.16) 0%, transparent 62%); pointer-events: none;
    }
    
    .hero-panel { background: rgba(20, 35, 50, 0.4); border: 1px solid var(--stroke); border-radius: var(--radius-xl); padding: 2.5rem; min-height: 520px; display: flex; flex-direction: column; }
    .eyebrow { color: var(--accent-2); text-transform: uppercase; letter-spacing: 0.14em; font-size: 0.78rem; font-weight: 700; margin-bottom: 1rem; }
    .hero-title { font-size: clamp(2.55rem, 5vw, 4.35rem); line-height: 1.05; letter-spacing: -0.03em; font-weight: 800; margin: 0 0 1rem 0; color: var(--text); }
    .hero-title .accent { color: var(--accent-2); }
    .hero-subtitle { max-width: 58ch; font-size: 1.08rem; line-height: 1.8; color: var(--soft); margin-bottom: 2rem; }
    
    /* PROOF STRIP HTML */
    .proof-strip { display: flex; flex-wrap: wrap; gap: 0.55rem; margin-top: 1.5rem; }
    .proof-chip { display: inline-flex; align-items: center; padding: 0.42rem 0.8rem; border-radius: 999px; background: rgba(255,255,255,0.04); border: 1px solid rgba(120,145,170,0.16); color: var(--soft); font-size: 0.82rem; }

    .mini-stat { background: rgba(255,255,255,0.02); border: 1px solid var(--stroke); border-radius: 16px; padding: 1.1rem 1.2rem; margin-bottom: 0.8rem; flex: 1; display: flex; flex-direction: column; justify-content: center; }
    .mini-stat-value { font-size: 1.35rem; font-weight: 750; color: var(--text); line-height: 1.1; }
    .mini-stat-label { margin-top: 0.25rem; color: var(--muted); font-size: 0.88rem; line-height: 1.5; }

    /* SECTIONS */
    .section-head { margin-top: 3.5rem; margin-bottom: 1rem; }
    .section-kicker { color: var(--accent-2); text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.74rem; font-weight: 700; margin-bottom: 0.4rem; }
    .section-title { font-size: 1.86rem; font-weight: 750; letter-spacing: -0.03em; color: var(--text); margin: 0; }
    .section-subtitle { margin-top: 0.35rem; color: var(--muted); line-height: 1.75; max-width: 74ch; }
    
    .trust-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1rem; margin-top: 0.8rem; }
    .trust-card { background: var(--panel); border: 1px solid var(--stroke); border-radius: 18px; padding: 1.2rem; min-height: 136px; }
    .trust-title { font-size: 1.05rem; font-weight: 650; color: var(--text); margin-bottom: 0.45rem; }
    .trust-desc { font-size: 0.92rem; line-height: 1.65; color: var(--muted); }
    
    .loop-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1rem; margin-top: 0.8rem; }
    .loop-card { background: var(--panel-2); border: 1px solid var(--stroke); border-radius: 18px; padding: 1.2rem; min-height: 172px; }
    .loop-number { width: 34px; height: 34px; display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; background: rgba(56,194,201,0.12); border: 1px solid rgba(56,194,201,0.22); color: var(--accent-2); font-weight: 700; font-size: 0.92rem; margin-bottom: 0.8rem; }
    
    /* MODUL KORT (MAGISK CSS) */
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
    
    /* KNAPPER GENERELT */
    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] { background: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important;}
    
    /* MODUL KNAPPER SPESIFIKK */
    [data-testid="column"]:has(.module-card-hook) button[kind="secondary"] { background: rgba(56,194,201,0.1) !important; border: 1px solid rgba(56,194,201,0.28) !important; color: #f5f7fb !important; border-radius: 12px !important; min-height: 46px !important; font-weight: 650 !important; width: 100% !important; }
    [data-testid="column"]:has(.module-card-hook) button[kind="secondary"]:hover { border-color: rgba(56,194,201,0.8) !important; background: rgba(56,194,201,0.2) !important; }

    .cta-band { margin-top: 3rem; margin-bottom: 1.5rem; background: linear-gradient(135deg, rgba(56,194,201,0.12), rgba(18,49,76,0.28)); border: 1px solid rgba(56,194,201,0.18); border-radius: 24px; padding: 2.5rem; text-align: center; }
    
    .footer-block { text-align: center; margin-top: 4rem; padding-top: 2rem; border-top: 1px solid rgba(120,145,170,0.18); }
</style>
""", unsafe_allow_html=True)

# -------------------------------------------------
# 5) TOP BAR (Kun logo og språk)
# -------------------------------------------------
top_l, top_r = st.columns([5, 1])
with top_l:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(logo_html)
with top_r:
    # Plasserer selectbox litt ned så den liner opp med logoen
    st.markdown("<div style='margin-top: 1rem;'></div>", unsafe_allow_html=True)
    valgt_språk = st.selectbox(
        "Språk", 
        list(TEXTS.keys()), 
        index=list(TEXTS.keys()).index(st.session_state.app_lang)
    )
    if valgt_språk != st.session_state.app_lang:
        st.session_state.app_lang = valgt_språk
        st.session_state.project_data["land"] = TEXTS[valgt_språk]["rule_set"]
        st.rerun()

# -------------------------------------------------
# 6) HERO SEKSJON
# -------------------------------------------------
left, right = st.columns([1.2, 0.8], gap="large")

with left:
    # Blander HTML for layout og innfødte Streamlit-knapper for navigasjon
    st.markdown('<div class="hero-hook"></div>', unsafe_allow_html=True)
    st.markdown(f"""
        <div class="eyebrow">{lang["eyebrow"]}</div>
        <h1 class="hero-title">{lang["title"]}</h1>
        <div class="hero-subtitle">{lang["subtitle"]}</div>
    """, unsafe_allow_html=True)
    
    btn_col1, btn_col2 = st.columns([1, 1])
    with btn_col1:
        if st.button(lang["btn_setup"], type="primary", use_container_width=True): 
            if find_page("Project"): st.switch_page(find_page("Project"))
    with btn_col2:
        if st.button(lang["btn_qa"], type="secondary", use_container_width=True): 
            if find_page("Review"): st.switch_page(find_page("Review"))
            
    st.markdown(f"""
        <div class="proof-strip">
            {"".join([f'<div class="proof-chip">{p}</div>' for p in lang["proofs"]])}
        </div>
    """, unsafe_allow_html=True)

with right:
    render_html(f"""
        <div class="hero-panel">
            <div style="font-size: 0.86rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin-bottom: 0.85rem;">Why Builtly?</div>
            <div class="mini-stat"><div class="mini-stat-value">{lang["stat1_v"]}</div><div class="mini-stat-label"><b>{lang["stat1_t"]}</b><br>{lang["stat1_d"]}</div></div>
            <div class="mini-stat"><div class="mini-stat-value">{lang["stat2_v"]}</div><div class="mini-stat-label"><b>{lang["stat2_t"]}</b><br>{lang["stat2_d"]}</div></div>
            <div class="mini-stat" style="margin-bottom:0;"><div class="mini-stat-value">{lang["stat3_v"]}</div><div class="mini-stat-label"><b>{lang["stat3_t"]}</b><br>{lang["stat3_d"]}</div></div>
        </div>
    """)

# -------------------------------------------------
# 7) KJERNEPRODUKT & ARBEIDSFLYT
# -------------------------------------------------
render_html(f"""
    <div class="section-head">
        <div class="section-kicker">{lang["sec_val_kicker"]}</div>
        <h2 class="section-title">{lang["sec_val_title"]}</h2>
        <div class="section-subtitle">{lang["sec_val_sub"]}</div>
    </div>
    <div class="trust-grid">
        <div class="trust-card"><div class="trust-title">{lang["val_1_t"]}</div><div class="trust-desc">{lang["val_1_d"]}</div></div>
        <div class="trust-card"><div class="trust-title">{lang["val_2_t"]}</div><div class="trust-desc">{lang["val_2_d"]}</div></div>
        <div class="trust-card"><div class="trust-title">{lang["val_3_t"]}</div><div class="trust-desc">{lang["val_3_d"]}</div></div>
        <div class="trust-card"><div class="trust-title">{lang["val_4_t"]}</div><div class="trust-desc">{lang["val_4_d"]}</div></div>
    </div>

    <div class="section-head">
        <div class="section-kicker">{lang["sec_loop_kicker"]}</div>
        <h2 class="section-title">{lang["sec_loop_title"]}</h2>
        <div class="section-subtitle">{lang["sec_loop_sub"]}</div>
    </div>
    <div class="loop-grid">
        <div class="loop-card"><div class="loop-number">1</div><div class="loop-title">{lang["loop_1_t"]}</div><div class="loop-desc">{lang["loop_1_d"]}</div></div>
        <div class="loop-card"><div class="loop-number">2</div><div class="loop-title">{lang["loop_2_t"]}</div><div class="loop-desc">{lang["loop_2_d"]}</div></div>
        <div class="loop-card"><div class="loop-number">3</div><div class="loop-title">{lang["loop_3_t"]}</div><div class="loop-desc">{lang["loop_3_d"]}</div></div>
        <div class="loop-card"><div class="loop-number">4</div><div class="loop-title">{lang["loop_4_t"]}</div><div class="loop-desc">{lang["loop_4_d"]}</div></div>
    </div>
""")

# -------------------------------------------------
# 8) MODULER (Med magisk rendering for minne)
# -------------------------------------------------
def render_module(col, icon, badge, badge_class, title, desc, input_txt, output_txt, btn_label, page_target):
    with col:
        st.markdown('<div class="module-card-hook"></div>', unsafe_allow_html=True)
        st.markdown(f"""
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:1rem;">
                <div class="module-icon">{icon}</div>
                <div class="module-badge {badge_class}">{badge}</div>
            </div>
            <div style="font-size:1.08rem; font-weight:720; color:#f5f7fb; margin-bottom:0.5rem; line-height: 1.35;">{title}</div>
            <div style="font-size:0.95rem; color:#9fb0c3; line-height:1.6; margin-bottom:1rem;">{desc}</div>
            <div style="flex:1;"></div>
            <div style="font-size:0.86rem; color:#c8d3df; padding-top:0.95rem; border-top:1px solid rgba(120,145,170,0.14);">
                <strong>Input:</strong> {input_txt}<br>
                <strong>Output:</strong> {output_txt}
            </div>
        """, unsafe_allow_html=True)
        
        if find_page(page_target):
            if st.button(btn_label, key=f"btn_{page_target}", type="secondary", use_container_width=True):
                st.switch_page(find_page(page_target))
        else:
            st.button(lang["dev"], key=f"btn_{page_target}_dev", type="secondary", disabled=True, use_container_width=True)

st.markdown(f"<div class='section-head'><h2 class='section-title'>Fagmoduler</h2></div>", unsafe_allow_html=True)
st.markdown(f"<h3 style='font-size: 1.1rem; margin-bottom: 1rem;'>{lang['mod_sec1']}</h3>", unsafe_allow_html=True)

r1c1, r1c2, r1c3 = st.columns(3)
render_module(r1c1, "🌍", "Phase 1 - Priority", "badge-priority", lang["m_geo_t"], lang["m_geo_d"], lang["m_geo_in"], lang["m_geo_out"], lang["m_geo_btn"], "Geo")
render_module(r1c2, "🔊", "Phase 2", "badge-phase2", lang["m_aku_t"], lang["m_aku_d"], lang["m_aku_in"], lang["m_aku_out"], lang["m_aku_btn"], "Akustikk")
render_module(r1c3, "🔥", "Phase 2", "badge-phase2", lang["m_brann_t"], lang["m_brann_d"], lang["m_brann_in"], lang["m_brann_out"], lang["m_brann_btn"], "Brannkonsept")

st.markdown("<br>", unsafe_allow_html=True)
st.markdown(f"<h3 style='font-size: 1.1rem; margin-bottom: 1rem;'>{lang['mod_sec2']}</h3>", unsafe_allow_html=True)

r2c1, r2c2, r2c3 = st.columns(3)
render_module(r2c1, "📐", "Early phase", "badge-early", lang["m_ark_t"], lang["m_ark_d"], lang["m_ark_in"], lang["m_ark_out"], lang["m_ark_btn"], "Mulighetsstudie")
render_module(r2c2, "🏢", "Roadmap", "badge-roadmap", lang["m_rib_t"], lang["m_rib_d"], lang["m_rib_in"], lang["m_rib_out"], lang["m_rib_btn"], "Konstruksjon")
render_module(r2c3, "🚦", "Roadmap", "badge-roadmap", lang["m_tra_t"], lang["m_tra_d"], lang["m_tra_in"], lang["m_tra_out"], lang["m_tra_btn"], "Trafikk")

# -------------------------------------------------
# 9) CTA & FOOTER
# -------------------------------------------------
st.markdown(f"""
    <div class="cta-band">
        <div style="font-size: 1.5rem; font-weight: 750; color: #fff; margin-bottom: 0.5rem;">{lang["cta_title"]}</div>
        <div style="color: var(--muted); margin-bottom: 1.5rem;">{lang["cta_desc"]}</div>
    </div>
""", unsafe_allow_html=True)

cc1, cc2, cc3 = st.columns([1, 1, 1])
with cc2:
    if st.button("🚀 " + lang["btn_setup"], type="primary", use_container_width=True):
        if find_page("Project"): st.switch_page(find_page("Project"))

render_html(f"""
    <div class="footer-block">
        <div style="font-weight: 650; color: #f5f7fb; margin-bottom: 0.3rem;">AI-assisted engineering. Human-verified. Compliance-grade.</div>
        <div style="color: rgba(159,176,195,0.55); font-size: 0.8rem;">{lang["footer"]}</div>
    </div>
""")
