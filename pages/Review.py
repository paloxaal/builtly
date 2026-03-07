import streamlit as st
import os
import time

# --- 1. GRUNNINNSTILLINGER ---
st.set_page_config(page_title="QA & Sign-off | Builtly", page_icon="✍️", layout="wide", initial_sidebar_state="expanded")

# Låser logoen øverst til venstre
if os.path.exists("logo.png"):
    st.logo("logo.png", size="large")

# --- 2. SESSION STATE (For å huske signaturer mens du tester) ---
if "signed_projects" not in st.session_state:
    st.session_state.signed_projects = []
if "viewing_project" not in st.session_state:
    st.session_state.viewing_project = None

# --- 3. PREMIUM DARK MODE CSS ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    
    .stApp { background-color: #050505 !important; font-family: 'Inter', sans-serif; color: #e4e4e7; }
    header {visibility: hidden;}
    [data-testid="stSidebar"] { background-color: #0a0a0b !important; border-right: 1px solid #1f1f22 !important; }
    .block-container { padding-top: 3rem !important; max-width: 1200px !important; }
    
    .header-title { font-size: 2.2rem; font-weight: 700; color: #ffffff; letter-spacing: -0.02em; margin-bottom: 0.2rem; }
    .header-sub { color: #a1a1aa; font-size: 1rem; margin-bottom: 2rem; border-bottom: 1px solid #27272a; padding-bottom: 1.5rem; }
    
    /* Tabell-design for pending reviews */
    .task-row {
        background: rgba(24, 24, 27, 0.4); border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px; padding: 1.2rem; margin-bottom: 0.8rem;
        display: flex; justify-content: space-between; align-items: center;
        transition: all 0.2s ease;
    }
    .task-row:hover { background: rgba(24, 24, 27, 0.8); border-color: rgba(56, 189, 248, 0.4); transform: translateX(4px); }
    .task-info h4 { margin: 0 0 0.3rem 0; color: #fafafa; font-size: 1.1rem; }
    .task-info p { margin: 0; color: #71717a; font-size: 0.9rem; }
    
    /* Badges */
    .badge { padding: 4px 10px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
    .badge-pending { background: rgba(245, 158, 11, 0.1); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.2); }
    .badge-signed { background: rgba(16, 185, 129, 0.1); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.2); }
    .badge-module { background: #18181b; color: #a1a1aa; border: 1px solid #27272a; }

    /* Review Interface */
    .review-panel { background: #09090b; border: 1px solid #27272a; border-radius: 12px; padding: 2rem; }
    .doc-preview { background: #ffffff; border-radius: 8px; padding: 2rem; color: #000000; height: 500px; overflow-y: auto; font-family: 'Times New Roman', serif; }
    .check-item { display: flex; align-items: center; gap: 10px; margin-bottom: 1rem; color: #e4e4e7; font-size: 0.95rem; }
    
</style>
""", unsafe_allow_html=True)

# --- 4. DATA MOCKUP ---
mock_projects = {
    "PRJ-2026-A1": {"name": "Saga Park Næringsbygg", "module": "RIG-M (Miljø)", "date": "07. Mar 2026", "drafter": "Builtly AI", "qa": "Ola Nordmann (Junior)"},
    "PRJ-2026-B4": {"name": "Fjordveien Boligsameie", "module": "RIBr (Brann)", "date": "06. Mar 2026", "drafter": "Builtly AI", "qa": "Kari Nilsen (Junior)"},
    "PRJ-2026-C2": {"name": "Sentrumsterminalen", "module": "RIB (Konstruksjon)", "date": "05. Mar 2026", "drafter": "Builtly AI", "qa": "Ola Nordmann (Junior)"}
}

# --- 5. LOGIKK FOR Å VISE DETALJER ELLER LISTE ---

# Funksjon for å "gå tilbake"
def go_back():
    st.session_state.viewing_project = None

# Funksjon for å signere
def sign_document(p_id):
    with st.spinner("Krypterer og påfører digital signatur..."):
        time.sleep(1.5) # Fake forsinkelse for å se ut som systemet jobber
    st.session_state.signed_projects.append(p_id)
    st.session_state.viewing_project = None
    st.balloons() # Litt konfetti for en god følelse!
    st.success(f"✅ Dokument for {mock_projects[p_id]['name']} ble godkjent og signert!")

# --- VISNING 1: DETALJERT REVIEW INTERFACE ---
if st.session_state.viewing_project:
    p_id = st.session_state.viewing_project
    proj = mock_projects[p_id]
    
    st.button("← Tilbake til Dashboard", on_click=go_back)
    
    st.markdown(f"<div class='header-title'>Review: {proj['name']}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='header-sub'>Dokument-ID: {p_id} | Modul: <span class='badge badge-module'>{proj['module']}</span></div>", unsafe_allow_html=True)
    
    c1, c2 = st.columns([0.65, 0.35], gap="large")
    
    with c1:
        st.markdown("### AI-Generert Utkast")
        st.markdown("""
        <div class="doc-preview">
            <h2 style="color: #1a2b4c; border-bottom: 2px solid #ccc; padding-bottom: 10px;">SAMMENDRAG OG KONKLUSJON</h2>
            <p><strong>Dato:</strong> 07. Mars 2026<br>
            <strong>Utarbeidet av:</strong> Builtly AI Engine<br>
            <strong>Kontrollert av:</strong> Venter på Senior Sign-off</p>
            <p>Basert på de opplastede laboratorieanalysene fra ALS Laboratory Group, er grunnforholdene vurdert i henhold til forurensningsforskriften kapittel 2. Prøvene viser forhøyede verdier av bly (Pb) i prøvepunkt BP1-3, som tilsvarer tilstandsklasse 3 (Moderat forurenset).</p>
            <p><strong>Anbefalt tiltak:</strong> Massene må håndteres som forurenset og leveres til godkjent deponi. Det anbefales en dedikert tiltaksplan for gravearbeidene.</p>
            <br>
            <h3 style="color: #1a2b4c;">KVALITETSSIKRING (HUMAN-IN-THE-LOOP)</h3>
            <p><em>Notat fra Junioringeniør (Ola Nordmann):</em> Har krysssjekket lab-verdiene mot tabellverdiene i Miljødirektoratets veileder. AI-en har hentet ut riktig tilstandsklasse. Anbefaler godkjenning.</p>
        </div>
        """, unsafe_allow_html=True)

    with c2:
        st.markdown("### Compliance Check")
        st.info("💡 Builtly AI Confidence Score: **98.4%**")
        
        st.markdown("<div class='review-panel'>", unsafe_allow_html=True)
        st.markdown("#### Sjekkliste for Godkjenning")
        st.checkbox("Rådata fra lab er verifisert mot uttrekk", value=True, disabled=True)
        st.checkbox("Regelverksreferanser (TEK17/Miljødir) er korrekte", value=True, disabled=True)
        st.checkbox("Plausibilitetskontroll utført av Junior", value=True, disabled=True)
        
        st.markdown("---")
        st.markdown("#### Digital Sign-off")
        st.markdown("<p style='font-size: 0.9rem; color: #a1a1aa;'>Ved å klikke under, bekrefter du faglig ansvar for innholdet og påfører din kryptografiske signatur på PDF-en.</p>", unsafe_allow_html=True)
        
        if st.button("✍️ Godkjenn og Signer Dokument", type="primary", use_container_width=True):
            sign_document(p_id)
        
        st.button("Avvis og send tilbake til Junior", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

# --- VISNING 2: DASHBOARD (LISTE OVER OPPGAVER) ---
else:
    st.markdown("<div class='header-title'>QA & Sign-off Dashboard</div>", unsafe_allow_html=True)
    st.markdown("<div class='header-sub'>Kvalitetssikring av AI-genererte dokumenter før endelig leveranse.</div>", unsafe_allow_html=True)
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Pending Sign-offs", len(mock_projects) - len(st.session_state.signed_projects))
    c2.metric("Signed Today", len(st.session_state.signed_projects))
    c3.metric("AI Confidence Avg.", "97.2%")
    
    st.markdown("### Dokumenter til behandling")
    
    for p_id, proj in mock_projects.items():
        is_signed = p_id in st.session_state.signed_projects
        
        badge_html = "<span class='badge badge-signed'>✅ Approved & Signed</span>" if is_signed else "<span class='badge badge-pending'>⏳ Pending Senior Review</span>"
        
        st.markdown(f"""
        <div class="task-row">
            <div class="task-info">
                <h4>{proj['name']}</h4>
                <p>ID: {p_id} • Modul: {proj['module']} • Draftet: {proj['drafter']} • Sjekket av: {proj['qa']}</p>
            </div>
            <div>
                {badge_html}
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # Hvis den IKKE er signert, gi en knapp for å åpne den
        if not is_signed:
            if st.button(f"Åpne for kontroll", key=f"btn_{p_id}"):
                st.session_state.viewing_project = p_id
                st.rerun()
