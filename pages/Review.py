import streamlit as st
import os
import base64
from pathlib import Path
import time
from datetime import datetime  # <--- HER ER FIKSEN SOM MANGLET!

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="QA & Sign-off | Builtly", page_icon="✅", layout="wide", initial_sidebar_state="collapsed")

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

# --- 2. SESSION STATE (Hukommelse for QA) ---
if "active_review" not in st.session_state:
    st.session_state.active_review = None

if "signed_docs" not in st.session_state:
    st.session_state.signed_docs = []

# Vår "database" over dokumenter
DOCS = {
    "PRJ-2026-A1": {"title": "Saga Park Næringsbygg", "module": "RIG-M (Miljø)", "drafter": "Builtly AI", "reviewer": "Ola Nordmann (Junior)", "status": "Pending Senior Review", "class": "badge-pending"},
    "PRJ-2026-B4": {"title": "Fjordveien Boligsameie", "module": "RIBr (Brann)", "drafter": "Builtly AI", "reviewer": "Kari Nilsen (Junior)", "status": "Pending Senior Review", "class": "badge-pending"},
    "PRJ-2026-C2": {"title": "Sentrumsterminalen", "module": "RIB (Konstruksjon)", "drafter": "Builtly AI", "reviewer": "Ola Nordmann (Junior)", "status": "Pending Senior Review", "class": "badge-pending"}
}

# --- 3. PREMIUM CSS ---
st.markdown("""
<style>
    :root {
        --bg: #06111a; --panel: rgba(10, 22, 35, 0.78);
        --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38bdf8; --radius-lg: 16px; --radius-xl: 24px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }
    
    /* TOP SHELL & LOGO */
    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }
    
    /* TOPPKNAPPER */
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

    /* STATISTIKK KORT */
    .stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.5rem; margin-bottom: 3rem; }
    .card { background: linear-gradient(180deg, rgba(16,30,46,0.8), rgba(10,18,28,0.8)); border: 1px solid var(--stroke); border-radius: var(--radius-lg); padding: 1.5rem; box-shadow: 0 12px 30px rgba(0,0,0,0.2); }
    .stat-title { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); margin-bottom: 0.8rem; }
    .stat-value { font-size: 2.2rem; font-weight: 750; color: #fff; margin-bottom: 0.3rem; line-height: 1; }

    /* --- REVIEW LISTE-KORTENE --- */
    [data-testid="column"]:has(.review-card-hook) {
        background: linear-gradient(180deg, rgba(12,25,39,0.98), rgba(8,18,28,0.98)) !important;
        border: 1px solid rgba(120, 145, 170, 0.18) !important; border-radius: 16px !important;
        padding: 1.8rem 2rem !important; margin-bottom: 1.2rem !important;
        box-shadow: 0 8px 24px rgba(0,0,0,0.15) !important; transition: all 0.2s ease !important;
    }
    [data-testid="column"]:has(.review-card-hook):hover { border-color: rgba(56,194,201,0.3) !important; box-shadow: 0 12px 32px rgba(0,0,0,0.25) !important; transform: translateY(-2px); }
    
    /* --- STREAMLIT KNAPPER GENERELT --- */
    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 8px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 8px 20px rgba(56,194,201,0.25) !important; }
    
    button[kind="secondary"] { background-color: #0d1824 !important; border: 1px solid rgba(120,145,170,0.4) !important; border-radius: 8px !important; padding: 8px 24px !important; transition: all 0.2s ease !important; }
    button[kind="secondary"] * { color: #f5f7fb !important; font-weight: 650 !important; font-size: 0.95rem !important; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: rgba(56,194,201,0.8) !important; }
    button[kind="secondary"]:hover * { color: var(--accent) !important; }

    /* STATUS BADGES */
    .status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px; border-radius: 999px; font-size: 0.75rem; font-weight: 650; text-transform: uppercase; letter-spacing: 0.05em; }
    .badge-pending { background: rgba(244, 191, 79, 0.1); border: 1px solid rgba(244, 191, 79, 0.3); color: #f4bf4f; }
    .badge-approved { background: rgba(126, 224, 129, 0.1); border: 1px solid rgba(126, 224, 129, 0.3); color: #7ee081; }

    /* INPUTS FOR REVIEW-SIDEN */
    .stTextArea textarea { background-color: #0d1824 !important; color: #ffffff !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextArea textarea:focus { border-color: #38bdf8 !important; box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.5) !important; }
    
    /* DOKUMENT MOCKUP CSS */
    .doc-mockup { background: #ffffff; border-radius: 8px; padding: 3rem 2rem; color: #000; font-family: 'Times New Roman', serif; min-height: 600px; box-shadow: inset 0 0 20px rgba(0,0,0,0.1); }
    .doc-header { border-bottom: 2px solid #ccc; padding-bottom: 1rem; margin-bottom: 2rem; }
    .doc-title { font-size: 1.8rem; font-weight: bold; margin: 0; color: #1a2b48;}
    .doc-meta { font-size: 0.9rem; color: #666; margin-top: 0.5rem; }
    .doc-body { font-size: 1rem; line-height: 1.6; color: #333; }
    .doc-h2 { font-size: 1.2rem; font-weight: bold; margin-top: 1.5rem; margin-bottom: 0.5rem; color: #1a2b48; }
</style>
""", unsafe_allow_html=True)

# --- 4. HEADER UI ---
logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'

render_html(f"""
<div class="top-shell">
    <div>{logo_html}</div>
    <div class="topbar-right">
        <a href="/" target="_self" class="top-link ghost">← Tilbake til Portal</a>
    </div>
</div>
""")

# =====================================================================
# RUTE 1: HOVED-DASHBOARD (LISTE)
# =====================================================================
if st.session_state.active_review is None:
    st.markdown("<h1 style='font-size: 2.8rem; margin-bottom: 0.2rem; letter-spacing: -0.02em;'>QA & Sign-off Dashboard</h1>", unsafe_allow_html=True)
    st.markdown("<p style='color: var(--muted); font-size: 1.1rem; margin-bottom: 2.5rem;'>Kvalitetssikring av AI-genererte dokumenter før endelig leveranse.</p>", unsafe_allow_html=True)

    # Beregner levende statistikk
    pending_count = len(DOCS) - len(st.session_state.signed_docs)
    signed_count = len(st.session_state.signed_docs)
    
    render_html(f"""
    <div class="stat-grid">
        <div class="card">
            <div class="stat-title">Pending Sign-offs</div>
            <div class="stat-value">{pending_count}</div>
        </div>
        <div class="card">
            <div class="stat-title">Signed Today</div>
            <div class="stat-value">{signed_count}</div>
        </div>
        <div class="card">
            <div class="stat-title">AI Confidence Avg.</div>
            <div class="stat-value" style="color: var(--accent);">97.2%</div>
        </div>
    </div>
    """)

    st.markdown("<h2 style='font-size: 1.6rem; margin-bottom: 1.5rem; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 0.5rem;'>Dokumenter til behandling</h2>", unsafe_allow_html=True)

    if pending_count == 0:
        st.success("🎉 Fantastisk! Det er ingen dokumenter i køen. Alle rapporter er ferdig signert.")
    else:
        def review_card(doc_id, info):
            col, _ = st.columns([1, 0.001])
            with col:
                st.markdown('<div class="review-card-hook"></div>', unsafe_allow_html=True)
                st.markdown(f"""
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 1rem;">
                    <div style="font-size: 1.35rem; font-weight: 750; color: #fff;">{info["title"]}</div>
                    <div class="status-badge {info["class"]}">⏳ {info["status"]}</div>
                </div>
                <div style="color: #9fb0c3; font-size: 0.95rem; line-height: 1.6; margin-bottom: 0.5rem;">
                    <strong>ID:</strong> {doc_id} &nbsp;&bull;&nbsp; <strong>Modul:</strong> {info["module"]} <br>
                    <strong>Draftet:</strong> {info["drafter"]} &nbsp;&bull;&nbsp; <strong>Sjekket av:</strong> {info["reviewer"]}
                </div>
                """, unsafe_allow_html=True)
                
                if st.button("🔍 Åpne for kontroll", key=f"btn_{doc_id}", type="secondary"):
                    st.session_state.active_review = doc_id
                    st.rerun()

        # Renderer kun dokumenter som ikke er signert enda
        for doc_id, info in DOCS.items():
            if doc_id not in st.session_state.signed_docs:
                review_card(doc_id, info)

# =====================================================================
# RUTE 2: AKTIV KONTROLL (DETALJVISNING)
# =====================================================================
else:
    doc_id = st.session_state.active_review
    doc_info = DOCS[doc_id]
    
    # Tilbakeknapp (Avbryt kontroll)
    if st.button("← Avbryt og gå tilbake til listen", type="secondary"):
        st.session_state.active_review = None
        st.rerun()
        
    st.markdown(f"<h1 style='font-size: 2.2rem; margin-top: 1rem; margin-bottom: 0;'>Kontroll: {doc_info['title']}</h1>", unsafe_allow_html=True)
    st.markdown(f"<p style='color: var(--muted); margin-bottom: 2rem;'><strong>{doc_id}</strong> | {doc_info['module']} | Utkast fra: {doc_info['drafter']}</p>", unsafe_allow_html=True)
    
    # Deler skjermen i dokumentvisning (venstre) og QA-verktøy (høyre)
    col_doc, col_qa = st.columns([1.6, 1], gap="large")
    
    with col_doc:
        st.markdown("<h3 style='font-size: 1.1rem; margin-bottom: 1rem;'>📄 Dokumentforhåndsvisning (Utkast)</h3>", unsafe_allow_html=True)
        # Simulert PDF-visning ved hjelp av CSS. 
        # Nå fungerer datofunksjonen!
        render_html(f"""
        <div class="doc-mockup">
            <div class="doc-header">
                <h1 class="doc-title">{doc_info['title'].upper()}</h1>
                <div class="doc-meta">Dokumentnr: {doc_id} | Disiplin: {doc_info['module']} | Dato: {datetime.now().strftime("%d.%m.%Y")}</div>
            </div>
            <div class="doc-body">
                <div class="doc-h2">1. Sammendrag og Konklusjon</div>
                Dette dokumentet representerer det AI-genererte utkastet for <strong>{doc_info['title']}</strong>. 
                Rapporten er validert opp mot gjeldende regelverk og prosjektets Master Data (SSOT).
                <br><br>
                Konklusjonen er at prosjektet lar seg gjennomføre innenfor de gitte tekniske rammene, forutsatt at 
                avbøtende tiltak nevnt i kapittel 4 implementeres.
                
                <div class="doc-h2">2. Prosjektbeskrivelse</div>
                Bygningsmassen består av et bygg der beregningsforutsetningene er lagt til grunn basert 
                på innsendte plantegninger og underlag.
                <br><br>
                <em>[Resten av den genererte rapporten vil normalt fylle flere sider her nedover...]</em>
            </div>
        </div>
        """)
        
    with col_qa:
        st.markdown("<div class='card' style='padding: 2rem;'>", unsafe_allow_html=True)
        st.markdown("<h3 style='font-size: 1.2rem; margin-bottom: 1.5rem; margin-top: 0;'>Formell Godkjenning (QA)</h3>", unsafe_allow_html=True)
        
        # Sjekkliste
        st.markdown("**Sjekkliste for Senior:**")
        chk1 = st.checkbox("Kontrollert at premisser fra SSOT er riktig anvendt.")
        chk2 = st.checkbox("Faglig vurdering av konklusjon er validert.")
        chk3 = st.checkbox("Eventuelle tegninger/kart er tolket riktig av AI.")
        
        st.markdown("<br>", unsafe_allow_html=True)
        
        # Kommentarer
        kommentar = st.text_area("Kommentarer til revisjon (Valgfritt):", placeholder="Skriv inn eventuelle avvik eller rettelser her...")
        
        st.markdown("<br><br>", unsafe_allow_html=True)
        
        # Handling: Godkjenn
        if st.button("✅ Godkjenn & Signer digitalt", type="primary", use_container_width=True):
            if not (chk1 and chk2 and chk3):
                st.error("Du må huke av for alle punktene i sjekklisten før du kan signere!")
            else:
                # Legger til i listen over signerte dokumenter
                st.session_state.signed_docs.append(doc_id)
                st.session_state.active_review = None # Lukker visningen
                st.success(f"Dokument '{doc_info['title']}' er nå signert og ferdigstilt!")
                time.sleep(1) # Liten forsinkelse for effekt
                st.rerun()
                
        st.markdown("<div style='margin-top: 0.8rem;'></div>", unsafe_allow_html=True)
        
        # Handling: Avvis
        if st.button("❌ Send tilbake for revisjon", type="secondary", use_container_width=True):
            if not kommentar:
                st.warning("Du må skrive en kommentar for å kunne sende dokumentet tilbake.")
            else:
                st.session_state.active_review = None
                st.info(f"Dokumentet er sendt tilbake til {doc_info['reviewer']} med dine kommentarer.")
                time.sleep(1)
                st.rerun()
                
        st.markdown("</div>", unsafe_allow_html=True)
