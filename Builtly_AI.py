import os
from pathlib import Path
import streamlit as st

# -------------------------------------------------
# 1) PAGE CONFIG
# -------------------------------------------------
st.set_page_config(
    page_title="Builtly | Kundeportal",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# -------------------------------------------------
# 2) PAGE MAP
# Tilpass bare disse hvis filnavnene dine er annerledes.
# st.page_link() bruker filsti relativt til hovedscriptet.
# -------------------------------------------------
PAGES = {
    "mulighetsstudie": "pages/Mulighetsstudie.py",
    "geo": "pages/Geo.py",
    "konstruksjon": "pages/Konstruksjon.py",
    "brann": "pages/Brannkonsept.py",
    "akustikk": "pages/Akustikk.py",
    "review": "pages/Review.py",
}

# -------------------------------------------------
# 3) HELPERS
# -------------------------------------------------
def page_exists(page_path: str) -> bool:
    return Path(page_path).exists()

def nav_link(page_key: str, label: str, icon: str = None, help_text: str = None):
    page_path = PAGES.get(page_key)
    if page_path and page_exists(page_path):
        st.page_link(page_path, label=label, icon=icon, help=help_text, width="stretch")
    else:
        st.markdown(
            f"""
            <div class="disabled-link">
                <span>{label}</span>
                <span class="disabled-tag">ikke koblet</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

# -------------------------------------------------
# 4) CSS
# -------------------------------------------------
st.markdown(
    """
    <style>
    :root {
        --bg: #06111a;
        --panel: rgba(10, 22, 35, 0.78);
        --panel-2: rgba(13, 27, 42, 0.9);
        --stroke: rgba(120, 145, 170, 0.18);
        --text: #f5f7fb;
        --muted: #9fb0c3;
        --soft: #c8d3df;
        --accent: #38c2c9;
        --accent-2: #78dce1;
        --accent-3: #112c3f;
        --ok: #7ee081;
        --warn: #f4bf4f;
        --shadow: 0 20px 80px rgba(0,0,0,0.35);
        --radius-xl: 28px;
        --radius-lg: 18px;
        --radius-md: 14px;
    }

    html, body, [class*="css"]  {
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    .stApp {
        background:
            radial-gradient(1100px 500px at 15% -5%, rgba(56,194,201,0.18), transparent 50%),
            radial-gradient(900px 500px at 100% 0%, rgba(64,170,255,0.12), transparent 45%),
            linear-gradient(180deg, #071018 0%, #08131d 35%, #071018 100%);
        color: var(--text);
    }

    header[data-testid="stHeader"] {
        visibility: hidden;
        height: 0;
    }

    [data-testid="stSidebar"] {
        background: rgba(7, 16, 24, 0.96);
        border-right: 1px solid var(--stroke);
    }

    .block-container {
        max-width: 1280px !important;
        padding-top: 2rem !important;
        padding-bottom: 4rem !important;
    }

    .topbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 1.25rem;
    }

    .brand-wrap {
        display: flex;
        align-items: center;
        gap: 1rem;
    }

    .brand-kicker {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.45rem 0.8rem;
        border: 1px solid rgba(56,194,201,0.24);
        background: rgba(56,194,201,0.08);
        border-radius: 999px;
        font-size: 0.82rem;
        color: var(--accent-2);
        letter-spacing: 0.02em;
    }

    .hero {
        position: relative;
        overflow: hidden;
        background:
            linear-gradient(180deg, rgba(13,27,42,0.96), rgba(8,18,28,0.96));
        border: 1px solid rgba(120,145,170,0.16);
        border-radius: var(--radius-xl);
        padding: 2.2rem;
        box-shadow: var(--shadow);
        margin-bottom: 1.25rem;
    }

    .hero::before {
        content: "";
        position: absolute;
        inset: -80px -120px auto auto;
        width: 420px;
        height: 420px;
        background: radial-gradient(circle, rgba(56,194,201,0.16) 0%, transparent 62%);
        pointer-events: none;
    }

    .eyebrow {
        color: var(--accent-2);
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-size: 0.78rem;
        font-weight: 700;
        margin-bottom: 1rem;
    }

    .hero-title {
        font-size: clamp(2.5rem, 5vw, 4.6rem);
        line-height: 0.95;
        letter-spacing: -0.05em;
        font-weight: 800;
        margin: 0;
        color: var(--text);
        max-width: 11ch;
    }

    .hero-title .accent {
        color: var(--accent-2);
    }

    .hero-subtitle {
        margin-top: 1.2rem;
        max-width: 60ch;
        font-size: 1.08rem;
        line-height: 1.8;
        color: var(--soft);
    }

    .hero-note {
        margin-top: 1rem;
        font-size: 0.95rem;
        color: var(--muted);
    }

    .hero-panel {
        background: rgba(255,255,255,0.03);
        border: 1px solid var(--stroke);
        border-radius: 22px;
        padding: 1.25rem;
        height: 100%;
    }

    .panel-title {
        font-size: 0.86rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--muted);
        margin-bottom: 0.85rem;
    }

    .mini-stat {
        background: rgba(255,255,255,0.03);
        border: 1px solid var(--stroke);
        border-radius: 16px;
        padding: 0.95rem 1rem;
        margin-bottom: 0.75rem;
    }

    .mini-stat-value {
        font-size: 1.35rem;
        font-weight: 700;
        color: var(--text);
        line-height: 1.1;
    }

    .mini-stat-label {
        margin-top: 0.25rem;
        color: var(--muted);
        font-size: 0.88rem;
        line-height: 1.5;
    }

    .section-head {
        margin-top: 1.4rem;
        margin-bottom: 1rem;
    }

    .section-kicker {
        color: var(--accent-2);
        text-transform: uppercase;
        letter-spacing: 0.12em;
        font-size: 0.74rem;
        font-weight: 700;
        margin-bottom: 0.4rem;
    }

    .section-title {
        font-size: 1.8rem;
        font-weight: 700;
        letter-spacing: -0.03em;
        color: var(--text);
        margin: 0;
    }

    .section-subtitle {
        margin-top: 0.35rem;
        color: var(--muted);
        line-height: 1.75;
        max-width: 72ch;
    }

    .trust-grid, .loop-grid, .module-grid {
        display: grid;
        gap: 1rem;
    }

    .trust-grid {
        grid-template-columns: repeat(4, minmax(0, 1fr));
        margin-top: 0.75rem;
        margin-bottom: 0.6rem;
    }

    .trust-card {
        background: var(--panel);
        border: 1px solid var(--stroke);
        border-radius: 18px;
        padding: 1rem 1rem 1.05rem 1rem;
        min-height: 132px;
    }

    .trust-title {
        font-size: 1rem;
        font-weight: 650;
        color: var(--text);
        margin-bottom: 0.45rem;
    }

    .trust-desc {
        font-size: 0.92rem;
        line-height: 1.65;
        color: var(--muted);
    }

    .loop-grid {
        grid-template-columns: repeat(4, minmax(0, 1fr));
        margin-top: 0.8rem;
    }

    .loop-card {
        background: var(--panel-2);
        border: 1px solid var(--stroke);
        border-radius: 18px;
        padding: 1rem;
        min-height: 160px;
        position: relative;
    }

    .loop-number {
        width: 34px;
        height: 34px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 999px;
        background: rgba(56,194,201,0.12);
        border: 1px solid rgba(56,194,201,0.22);
        color: var(--accent-2);
        font-weight: 700;
        font-size: 0.92rem;
        margin-bottom: 0.8rem;
    }

    .loop-title {
        font-size: 1rem;
        font-weight: 650;
        color: var(--text);
        margin-bottom: 0.45rem;
    }

    .loop-desc {
        font-size: 0.92rem;
        line-height: 1.65;
        color: var(--muted);
    }

    .module-grid {
        grid-template-columns: repeat(3, minmax(0, 1fr));
        margin-top: 0.8rem;
    }

    .module-card {
        background: linear-gradient(180deg, rgba(12,25,39,0.96), rgba(8,18,28,0.96));
        border: 1px solid var(--stroke);
        border-radius: 22px;
        padding: 1.15rem;
        min-height: 270px;
        box-shadow: 0 12px 38px rgba(0,0,0,0.18);
    }

    .module-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 0.75rem;
        margin-bottom: 0.85rem;
    }

    .module-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        padding: 0.32rem 0.6rem;
        border-radius: 999px;
        border: 1px solid rgba(120,145,170,0.18);
        background: rgba(255,255,255,0.03);
        color: var(--muted);
        font-size: 0.75rem;
        font-weight: 600;
    }

    .module-icon {
        width: 44px;
        height: 44px;
        border-radius: 14px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: rgba(56,194,201,0.1);
        border: 1px solid rgba(56,194,201,0.18);
        color: var(--accent-2);
        font-size: 1.3rem;
    }

    .module-title {
        font-size: 1.08rem;
        font-weight: 700;
        color: var(--text);
        margin-bottom: 0.45rem;
    }

    .module-desc {
        font-size: 0.93rem;
        line-height: 1.7;
        color: var(--muted);
        margin-bottom: 0.9rem;
    }

    .module-meta {
        font-size: 0.85rem;
        line-height: 1.7;
        color: var(--soft);
        padding-top: 0.75rem;
        border-top: 1px solid rgba(120,145,170,0.14);
    }

    .cta-band {
        margin-top: 1.3rem;
        background:
            linear-gradient(135deg, rgba(56,194,201,0.12), rgba(18,49,76,0.28));
        border: 1px solid rgba(56,194,201,0.18);
        border-radius: 24px;
        padding: 1.4rem;
    }

    .cta-title {
        font-size: 1.3rem;
        font-weight: 700;
        color: var(--text);
        margin-bottom: 0.3rem;
    }

    .cta-desc {
        color: var(--muted);
        line-height: 1.7;
        max-width: 70ch;
    }

    .disabled-link {
        width: 100%;
        margin-top: 0.45rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        border: 1px dashed rgba(120,145,170,0.22);
        border-radius: 12px;
        padding: 0.8rem 0.95rem;
        color: var(--muted);
        font-size: 0.92rem;
        background: rgba(255,255,255,0.02);
    }

    .disabled-tag {
        font-size: 0.75rem;
        color: var(--warn);
    }

    @media (max-width: 1100px) {
        .trust-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .loop-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .module-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }

    @media (max-width: 720px) {
        .trust-grid, .loop-grid, .module-grid { grid-template-columns: 1fr; }
        .hero-title { max-width: none; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 5) TOP / BRAND
# -------------------------------------------------
top_left, top_right = st.columns([0.75, 0.25])

with top_left:
    brand_cols = st.columns([0.1, 0.9])
    with brand_cols[0]:
        if os.path.exists("logo.png"):
            st.image("logo.png", width=68)
    with brand_cols[1]:
        st.markdown(
            """
            <div class="brand-kicker">AI-assistert prosjektering · Human-verified · Compliance-grade</div>
            """,
            unsafe_allow_html=True,
        )

with top_right:
    st.markdown("")
    nav_link("review", "Åpne review", icon=":material/verified:")

# -------------------------------------------------
# 6) HERO
# -------------------------------------------------
left, right = st.columns([1.35, 0.8], gap="large")

with left:
    st.markdown(
        """
        <div class="hero">
            <div class="eyebrow">Builtly Loop</div>
            <h1 class="hero-title">
                Fra <span class="accent">rådata</span> til signert
                ingeniørleveranse.
            </h1>
            <div class="hero-subtitle">
                Builtly er kundeportalen for AI-assistert prosjektering og dokumentasjon.
                Kunden laster opp rådata, plattformen gjør analyse, regelverkskontroll og
                rapportutkast — før junior QA og senior sign-off sikrer at leveransen er rask,
                konsistent og sporbar.
            </div>
            <div class="hero-note">
                Designet for byggesak, utførelse og profesjonell etterlevelse — ikke bare som en AI-demo.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with right:
    st.markdown(
        """
        <div class="hero-panel">
            <div class="panel-title">Hvorfor Builtly</div>

            <div class="mini-stat">
                <div class="mini-stat-value">80–90 %</div>
                <div class="mini-stat-label">reduksjon i manuelt skrivearbeid per rapport</div>
            </div>

            <div class="mini-stat">
                <div class="mini-stat-value">Junior + senior</div>
                <div class="mini-stat-label">human-in-the-loop kvalitetssikring og digital sign-off</div>
            </div>

            <div class="mini-stat">
                <div class="mini-stat-value">PDF + DOCX</div>
                <div class="mini-stat-label">ferdig rapportpakke med vedlegg, sjekklister og sporbarhet</div>
            </div>

            <div class="mini-stat" style="margin-bottom:0;">
                <div class="mini-stat-value">Audit trail</div>
                <div class="mini-stat-label">versjoner, opplastinger, regelverksreferanser og signaturer logges</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# -------------------------------------------------
# 7) TRUST SECTION
# -------------------------------------------------
st.markdown(
    """
    <div class="section-head">
        <div class="section-kicker">Produktløfte</div>
        <h2 class="section-title">Portal først. Moduler under.</h2>
        <div class="section-subtitle">
            Forsiden skal forklare hva kunden faktisk kjøper: en sikker og sporbar portal for
            opplasting, validering, AI-behandling, QA og signert leveranse — ikke bare en samling fagtitler.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="trust-grid">
        <div class="trust-card">
            <div class="trust-title">Kundeportal</div>
            <div class="trust-desc">Prosjektopprettelse, opplasting av rådata, mangellister, dokumentgenerator og revisjonsspor i én arbeidsflyt.</div>
        </div>
        <div class="trust-card">
            <div class="trust-title">Rules-first</div>
            <div class="trust-desc">AI brukes innenfor tydelige guardrails og kombineres med eksplisitte regler, sjekkpunkter og standardmaler.</div>
        </div>
        <div class="trust-card">
            <div class="trust-title">QA & sign-off</div>
            <div class="trust-desc">Junior validerer input og plausibilitet. Senior fagperson står for siste kontroll og signatur der det kreves.</div>
        </div>
        <div class="trust-card">
            <div class="trust-title">Skalerbar leveranse</div>
            <div class="trust-desc">Nye moduler kan rulles ut vertikalt uten å endre kjernen i plattformen.</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 8) BUILTLY LOOP
# -------------------------------------------------
st.markdown(
    """
    <div class="section-head">
        <div class="section-kicker">Arbeidsflyt</div>
        <h2 class="section-title">Builtly Loop</h2>
        <div class="section-subtitle">
            Det kunden skal forstå på fem sekunder er at Builtly tar dem fra opplastede rådata
            til ferdig, kvalitetssikret dokumentpakke.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="loop-grid">
        <div class="loop-card">
            <div class="loop-number">1</div>
            <div class="loop-title">Input</div>
            <div class="loop-desc">Last opp PDF, IFC, XLSX, CSV, støykart, labfiler, tegninger og andre prosjektvedlegg.</div>
        </div>
        <div class="loop-card">
            <div class="loop-number">2</div>
            <div class="loop-title">Analyse</div>
            <div class="loop-desc">Plattformen kjører parsing, validering, regelverkssjekk og genererer rapportutkast i fast mal.</div>
        </div>
        <div class="loop-card">
            <div class="loop-number">3</div>
            <div class="loop-title">QA & sign-off</div>
            <div class="loop-desc">Junior kontroll, senior kontroll og digital signatur — med versjonering og revisjonsspor.</div>
        </div>
        <div class="loop-card">
            <div class="loop-number">4</div>
            <div class="loop-title">Output</div>
            <div class="loop-desc">Ferdig dokumentpakke i Word/PDF med vedlegg, sjekklister og dokumentert etterlevelse.</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# -------------------------------------------------
# 9) MODULES
# -------------------------------------------------
st.markdown(
    """
    <div class="section-head">
        <div class="section-kicker">Moduler og roadmap</div>
        <h2 class="section-title">Spesialiserte agenter i én felles plattform</h2>
        <div class="section-subtitle">
            Bygget modulært: hver modul har egen datainngang, regelverksgrunnlag og rapportmal,
            men deler samme plattform for validering, dokumentbygging, QA og sign-off.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

module_cols = st.columns(3, gap="large")

with module_cols[0]:
    st.markdown(
        """
        <div class="module-card">
            <div class="module-top">
                <div class="module-icon">🌍</div>
                <div class="module-badge">Fase 1 · prioritet</div>
            </div>
            <div class="module-title">RIG-M — Miljø & geo</div>
            <div class="module-desc">
                Labfiler, graveplan og stedlige vedlegg inn. Klassifisering av masser,
                forslag til disponering og tiltaksplan ut.
            </div>
            <div class="module-meta">
                <strong>Input:</strong> XLSX / CSV / PDF + graveplan<br/>
                <strong>Output:</strong> tiltaksplan, masselogikk, vedleggspakke
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    nav_link("geo", "Åpne RIG-M", icon=":material/terrain:")

with module_cols[1]:
    st.markdown(
        """
        <div class="module-card">
            <div class="module-top">
                <div class="module-icon">🔊</div>
                <div class="module-badge">Fase 2</div>
            </div>
            <div class="module-title">RIAKU — Akustikk</div>
            <div class="module-desc">
                Støykart og plantegninger inn. Krav til fasade, vinduer og anbefalte tiltak
                ut, med vurdering mot relevante lydkrav.
            </div>
            <div class="module-meta">
                <strong>Input:</strong> støykart + plantegning<br/>
                <strong>Output:</strong> rapport, tiltak, fasadevurdering
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    nav_link("akustikk", "Åpne RIAKU", icon=":material/graphic_eq:")

with module_cols[2]:
    st.markdown(
        """
        <div class="module-card">
            <div class="module-top">
                <div class="module-icon">🔥</div>
                <div class="module-badge">Fase 2</div>
            </div>
            <div class="module-title">RIBr — Brannkonsept</div>
            <div class="module-desc">
                Arkitekttegninger og bruksklasse inn. Kontroll av rømningsveier,
                brannceller og prosjekteringsgrunnlag ut.
            </div>
            <div class="module-meta">
                <strong>Input:</strong> arkitekttegninger + bruksklasse<br/>
                <strong>Output:</strong> brannstrategi, avvik, tiltak
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    nav_link("brann", "Åpne RIBr", icon=":material/local_fire_department:")

module_cols_2 = st.columns(2, gap="large")

with module_cols_2[0]:
    st.markdown(
        """
        <div class="module-card">
            <div class="module-top">
                <div class="module-icon">📐</div>
                <div class="module-badge">Tidligfase</div>
            </div>
            <div class="module-title">ARK — Mulighetsstudie</div>
            <div class="module-desc">
                Screening, volumanalyse, tomtevurdering og tidligfase beslutningsstøtte før
                prosjektering og rapportløp.
            </div>
            <div class="module-meta">
                <strong>Input:</strong> tomtedata, planer, volumgrep<br/>
                <strong>Output:</strong> feasibility, utnyttelse, tidligfasevurdering
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    nav_link("mulighetsstudie", "Åpne mulighetsstudie", icon=":material/architecture:")

with module_cols_2[1]:
    st.markdown(
        """
        <div class="module-card">
            <div class="module-top">
                <div class="module-icon">🏢</div>
                <div class="module-badge">Senere i roadmap</div>
            </div>
            <div class="module-title">RIB — Konstruksjon</div>
            <div class="module-desc">
                Konseptuelle konstruksjonssjekker, prinsippdimensjonering og videre kobling mot
                beregning, BIM og klimafotavtrykk.
            </div>
            <div class="module-meta">
                <strong>Input:</strong> modeller, tegninger, lastforutsetninger<br/>
                <strong>Output:</strong> konseptnotat, prinsipper, beslutningsgrunnlag
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    nav_link("konstruksjon", "Åpne RIB", icon=":material/apartment:")

# -------------------------------------------------
# 10) CTA BAND
# -------------------------------------------------
st.markdown(
    """
    <div class="cta-band">
        <div class="cta-title">Ikke bare analyser. Faktisk leveranse.</div>
        <div class="cta-desc">
            Builtly skal oppleves som et operativt leveransesystem: opprett prosjekt, last opp rådata,
            se validering og avvik, generer utkast, send til QA, og motta signert dokumentpakke.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

cta_cols = st.columns(3, gap="large")
with cta_cols[0]:
    nav_link("mulighetsstudie", "Start nytt prosjekt", icon=":material/add_circle:")
with cta_cols[1]:
    nav_link("review", "Gå til review", icon=":material/fact_check:")
with cta_cols[2]:
    nav_link("geo", "Åpne første modul", icon=":material/rocket_launch:")
