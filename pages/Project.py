import os
from datetime import datetime

import streamlit as st


# -----------------------------
# 1. PAGE SETUP
# -----------------------------
st.set_page_config(
    page_title="Project Control Center | Builtly",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)

if os.path.exists("logo.png"):
    st.logo("logo.png", size="large")


# -----------------------------
# 2. THEME / BRAND CSS
# -----------------------------
st.markdown(
    """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

        :root {
            --bg: #06080d;
            --panel: rgba(11, 16, 27, 0.88);
            --panel-strong: rgba(13, 19, 32, 0.96);
            --panel-soft: rgba(255,255,255,0.03);
            --stroke: rgba(255,255,255,0.08);
            --stroke-strong: rgba(110,168,254,0.28);
            --text: #f5f7fb;
            --muted: #98a3b8;
            --accent: #6ea8fe;
            --accent-2: #7c5cff;
            --success: #31d0aa;
            --warning: #ffcc66;
            --shadow: 0 24px 80px rgba(0,0,0,0.34);
        }

        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif;
        }

        .stApp {
            color: var(--text);
            background:
                radial-gradient(circle at 12% -8%, rgba(110,168,254,0.18), transparent 28%),
                radial-gradient(circle at 94% 0%, rgba(124,92,255,0.16), transparent 25%),
                linear-gradient(180deg, #05070b 0%, #070b12 42%, #06070a 100%);
        }

        header {
            visibility: hidden;
        }

        .block-container {
            max-width: 1440px !important;
            padding-top: 2.2rem !important;
            padding-bottom: 4.5rem !important;
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, rgba(8,11,18,0.98), rgba(10,13,22,0.98)) !important;
            border-right: 1px solid rgba(255,255,255,0.06) !important;
        }

        [data-testid="stSidebarNav"] {
            padding-top: 1rem;
        }

        [data-testid="stSidebar"] .block-container {
            padding-top: 0.5rem !important;
            padding-left: 1rem !important;
            padding-right: 1rem !important;
        }

        .hero-shell {
            position: relative;
            overflow: hidden;
            border-radius: 26px;
            padding: 2rem;
            min-height: 260px;
            border: 1px solid var(--stroke);
            background:
                linear-gradient(180deg, rgba(16,22,37,0.92), rgba(10,14,23,0.96)),
                linear-gradient(135deg, rgba(110,168,254,0.12), rgba(124,92,255,0.06));
            box-shadow: var(--shadow);
        }

        .hero-shell::before {
            content: "";
            position: absolute;
            width: 280px;
            height: 280px;
            top: -120px;
            right: -80px;
            border-radius: 999px;
            background: radial-gradient(circle, rgba(110,168,254,0.30), transparent 68%);
            filter: blur(12px);
        }

        .badge {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            padding: 0.5rem 0.85rem;
            border-radius: 999px;
            border: 1px solid rgba(255,255,255,0.12);
            background: rgba(255,255,255,0.04);
            color: #dce7fa;
            font-size: 0.82rem;
            font-weight: 700;
            letter-spacing: 0.02em;
        }

        .hero-title {
            margin: 1rem 0 0.7rem 0;
            font-size: clamp(2.4rem, 4vw, 3.7rem);
            line-height: 0.98;
            letter-spacing: -0.04em;
            font-weight: 800;
            color: #ffffff;
            max-width: 780px;
        }

        .hero-sub {
            margin: 0;
            max-width: 740px;
            color: var(--muted);
            font-size: 1.03rem;
            line-height: 1.7;
        }

        .hero-pills {
            display: flex;
            flex-wrap: wrap;
            gap: 0.65rem;
            margin-top: 1.4rem;
        }

        .pill {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            border-radius: 999px;
            padding: 0.48rem 0.8rem;
            border: 1px solid rgba(255,255,255,0.08);
            background: rgba(255,255,255,0.035);
            color: #dbe4f7;
            font-size: 0.82rem;
            font-weight: 600;
        }

        .mini-panel,
        .metric-card,
        .summary-card,
        .sidebar-card,
        .tip-card,
        .derived-card,
        .action-strip {
            border-radius: 22px;
            border: 1px solid var(--stroke);
            background: linear-gradient(180deg, rgba(13,18,30,0.90), rgba(10,14,24,0.94));
            box-shadow: var(--shadow);
        }

        .mini-panel {
            padding: 1.35rem 1.3rem;
            min-height: 260px;
        }

        .eyebrow {
            color: #9fb3d8;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            font-size: 0.72rem;
            font-weight: 800;
        }

        .mini-value {
            margin-top: 0.55rem;
            font-size: 2rem;
            line-height: 1;
            font-weight: 800;
            letter-spacing: -0.03em;
            color: #ffffff;
        }

        .mini-copy,
        .muted {
            color: var(--muted);
        }

        .split-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 1rem;
            margin-top: 1rem;
            color: #dce6f7;
            font-size: 0.92rem;
        }

        .bar-wrap {
            width: 100%;
            height: 10px;
            border-radius: 999px;
            margin-top: 0.85rem;
            overflow: hidden;
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.05);
        }

        .bar-fill {
            height: 100%;
            border-radius: 999px;
            background: linear-gradient(90deg, var(--accent) 0%, var(--accent-2) 100%);
        }

        .status-chip {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 0.4rem 0.7rem;
            min-width: 92px;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            margin-top: 1rem;
        }

        .status-draft {
            background: rgba(255, 204, 102, 0.12);
            color: #ffd480;
            border: 1px solid rgba(255, 204, 102, 0.22);
        }

        .status-progress {
            background: rgba(110, 168, 254, 0.12);
            color: #9bc3ff;
            border: 1px solid rgba(110, 168, 254, 0.24);
        }

        .status-ready {
            background: rgba(49, 208, 170, 0.12);
            color: #84f0d4;
            border: 1px solid rgba(49, 208, 170, 0.24);
        }

        .metric-card {
            padding: 1.15rem 1.05rem;
            min-height: 128px;
        }

        .metric-label {
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.1em;
            font-size: 0.72rem;
            font-weight: 700;
        }

        .metric-value {
            color: #ffffff;
            font-size: 1.65rem;
            line-height: 1.05;
            margin-top: 0.65rem;
            font-weight: 800;
            letter-spacing: -0.03em;
        }

        .metric-helper {
            color: #ccd8eb;
            margin-top: 0.45rem;
            font-size: 0.9rem;
            line-height: 1.5;
        }

        div[data-testid="stForm"] {
            padding: 1.2rem 1.25rem 1.35rem 1.25rem;
            border-radius: 28px;
            border: 1px solid var(--stroke);
            background: linear-gradient(180deg, rgba(10,14,24,0.92), rgba(8,11,18,0.96));
            box-shadow: var(--shadow);
        }

        .section-kicker {
            color: #9fb3d8;
            font-size: 0.76rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.14em;
        }

        .section-title {
            color: #ffffff;
            font-size: 1.4rem;
            line-height: 1.15;
            margin: 0.45rem 0 0.35rem 0;
            font-weight: 800;
            letter-spacing: -0.03em;
        }

        .section-copy {
            color: var(--muted);
            margin: 0 0 1rem 0;
            max-width: 62ch;
            line-height: 1.7;
        }

        [data-testid="stTabs"] [role="tablist"] {
            gap: 0.65rem;
            margin-bottom: 1.1rem;
            flex-wrap: wrap;
        }

        button[data-baseweb="tab"] {
            height: 52px;
            padding: 0 1rem;
            border-radius: 16px;
            border: 1px solid rgba(255,255,255,0.08);
            background: rgba(255,255,255,0.03);
            color: var(--muted);
            font-size: 0.95rem;
            font-weight: 700;
        }

        button[data-baseweb="tab"][aria-selected="true"] {
            color: #ffffff;
            border-color: rgba(110,168,254,0.28);
            background: linear-gradient(180deg, rgba(110,168,254,0.18), rgba(124,92,255,0.10));
            box-shadow: inset 0 0 0 1px rgba(255,255,255,0.02), 0 12px 24px rgba(0,0,0,0.18);
        }

        label[data-testid="stWidgetLabel"] p {
            color: #dbe6fb !important;
            font-size: 0.94rem !important;
            font-weight: 700 !important;
            margin-bottom: 0.2rem !important;
        }

        div[data-baseweb="input"] > div,
        div[data-baseweb="base-input"] > div,
        div[data-baseweb="select"] > div,
        textarea {
            min-height: 54px;
            border-radius: 16px !important;
            background: rgba(8, 12, 20, 0.92) !important;
            border: 1px solid rgba(255,255,255,0.09) !important;
            color: #ffffff !important;
            box-shadow: none !important;
        }

        div[data-baseweb="input"] input,
        div[data-baseweb="base-input"] input,
        div[data-baseweb="select"] input,
        div[data-baseweb="select"] span,
        textarea {
            color: #ffffff !important;
        }

        textarea {
            min-height: 180px !important;
            line-height: 1.6 !important;
        }

        div[data-baseweb="input"] > div:focus-within,
        div[data-baseweb="base-input"] > div:focus-within,
        div[data-baseweb="select"] > div:focus-within,
        textarea:focus {
            border-color: rgba(110,168,254,0.48) !important;
            box-shadow: 0 0 0 1px rgba(110,168,254,0.24), 0 0 0 6px rgba(110,168,254,0.08) !important;
        }

        [data-testid="stNumberInput"] button {
            background: transparent !important;
            color: #d9e5fb !important;
        }

        [data-testid="stNumberInput"] svg,
        [data-testid="stSelectbox"] svg {
            fill: #d9e5fb !important;
        }

        div[data-testid="stFormSubmitButton"] button,
        div.stButton > button {
            min-height: 56px;
            border-radius: 16px;
            border: 1px solid rgba(110,168,254,0.22) !important;
            background: linear-gradient(135deg, #6ea8fe 0%, #7c5cff 100%) !important;
            color: #ffffff !important;
            font-size: 0.98rem;
            font-weight: 800;
            letter-spacing: 0.01em;
            box-shadow: 0 18px 36px rgba(67, 97, 238, 0.24);
            transition: transform 0.18s ease, box-shadow 0.18s ease;
        }

        div[data-testid="stFormSubmitButton"] button:hover,
        div.stButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 20px 40px rgba(67, 97, 238, 0.28);
        }

        .summary-card,
        .sidebar-card,
        .tip-card,
        .derived-card,
        .action-strip {
            padding: 1.2rem 1.15rem;
        }

        .summary-card {
            position: sticky;
            top: 1.25rem;
        }

        .summary-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 0.85rem;
            margin-top: 1rem;
        }

        .summary-row {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            align-items: baseline;
            padding-bottom: 0.75rem;
            border-bottom: 1px solid rgba(255,255,255,0.06);
        }

        .summary-key {
            color: var(--muted);
            font-size: 0.85rem;
        }

        .summary-val {
            color: #ffffff;
            font-size: 0.95rem;
            font-weight: 700;
            text-align: right;
        }

        .chip-wrap {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            margin-top: 0.85rem;
        }

        .chip {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 0.48rem 0.78rem;
            background: rgba(255,255,255,0.045);
            border: 1px solid rgba(255,255,255,0.07);
            color: #dce7f8;
            font-size: 0.8rem;
            font-weight: 600;
        }

        .tip-card ul,
        .sidebar-card ul {
            margin: 0.75rem 0 0 1rem;
            padding: 0;
            color: #d7e2f5;
            line-height: 1.65;
        }

        .tip-card li,
        .sidebar-card li {
            margin-bottom: 0.45rem;
        }

        .action-strip {
            margin-top: 1.2rem;
            background: linear-gradient(135deg, rgba(110,168,254,0.14), rgba(124,92,255,0.10));
        }

        .action-title {
            color: #ffffff;
            font-size: 1rem;
            font-weight: 800;
            margin-bottom: 0.25rem;
        }

        .action-copy {
            color: #d5e0f3;
            font-size: 0.92rem;
            line-height: 1.6;
            margin: 0;
        }

        .flash-shell > div {
            border-radius: 18px;
        }

        .footer-note {
            color: var(--muted);
            text-align: center;
            margin-top: 1rem;
            font-size: 0.84rem;
        }

        @media (max-width: 1100px) {
            .hero-shell,
            .mini-panel {
                min-height: auto;
            }

            .summary-card {
                position: relative;
                top: auto;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# -----------------------------
# 3. STATE
# -----------------------------
DEFAULT_PROJECT_DATA = {
    "p_name": "Nytt prosjekt",
    "c_name": "",
    "p_desc": "",
    "adresse": "",
    "kommune": "",
    "gnr": "",
    "bnr": "",
    "b_type": "Næring / Kontor",
    "etasjer": 4,
    "bta": 2500,
}

TYPE_OPTIONS = [
    "Bolig (Blokk/Rekkehus)",
    "Næring / Kontor",
    "Handel / Kjøpesenter",
    "Offentlig / Skole",
    "Industri / Lager",
]

AI_MODULES = [
    "Konsept",
    "Kalkyle",
    "Regulering",
    "Massing",
    "BIM",
    "Dokumentasjon",
]

if "project_data" not in st.session_state:
    st.session_state.project_data = DEFAULT_PROJECT_DATA.copy()

if "sync_status" not in st.session_state:
    st.session_state.sync_status = "Draft"

if "last_sync" not in st.session_state:
    st.session_state.last_sync = "Ikke synket enda"

pd = st.session_state.project_data


# -----------------------------
# 4. HELPERS
# -----------------------------
def format_number(value: int | float | str) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def safe_text(value: str, fallback: str = "—") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def completion_stats(data: dict) -> tuple[int, int, int]:
    fields = [
        "p_name",
        "c_name",
        "p_desc",
        "adresse",
        "kommune",
        "gnr",
        "bnr",
        "b_type",
        "etasjer",
        "bta",
    ]
    filled = 0
    for field in fields:
        value = data.get(field)
        if isinstance(value, str):
            if value.strip():
                filled += 1
        elif value not in (None, 0):
            filled += 1

    total = len(fields)
    percent = int(round((filled / total) * 100)) if total else 0
    return percent, filled, total


def sync_label(percent: int) -> str:
    if percent >= 85:
        return "AI-ready"
    if percent >= 45:
        return "In progress"
    return "Draft"


def sync_class(status: str) -> str:
    mapping = {
        "AI-ready": "status-ready",
        "In progress": "status-progress",
        "Draft": "status-draft",
    }
    return mapping.get(status, "status-draft")


def metric_card(label: str, value: str, helper: str) -> str:
    return f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-helper">{helper}</div>
        </div>
    """


def chips(labels: list[str]) -> str:
    return "".join([f'<span class="chip">{label}</span>' for label in labels])


pct_complete, filled_fields, total_fields = completion_stats(pd)
status = st.session_state.sync_status
status_css = sync_class(status)
last_sync = st.session_state.last_sync
bta_per_floor = int(pd["bta"] / pd["etasjer"]) if pd.get("etasjer") else 0
location_fingerprint = (
    f"{pd['kommune']} • {pd['gnr']}/{pd['bnr']}"
    if safe_text(pd.get("kommune")) != "—" and safe_text(pd.get("gnr")) != "—" and safe_text(pd.get("bnr")) != "—"
    else safe_text(pd.get("adresse"), "Lokasjon ikke satt")
)


# -----------------------------
# 5. SIDEBAR
# -----------------------------
with st.sidebar:
    st.markdown(
        f"""
        <div class="sidebar-card">
            <div class="eyebrow">Builtly SSOT</div>
            <div class="section-title" style="font-size:1.15rem; margin-top:0.5rem;">Project pulse</div>
            <div class="muted">Denne siden fungerer som masterdata-lag for AI-modulene på tvers av prosjektløpet.</div>
            <div class="split-row" style="margin-top:1rem;">
                <span>Datakompletthet</span>
                <strong>{pct_complete}%</strong>
            </div>
            <div class="bar-wrap"><div class="bar-fill" style="width:{pct_complete}%;"></div></div>
            <div class="status-chip {status_css}">{status}</div>
            <div class="split-row"><span>Sist synk</span><strong>{last_sync}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="sidebar-card" style="margin-top: 0.9rem;">
            <div class="eyebrow">Forbrukere</div>
            <div class="section-title" style="font-size:1.05rem; margin-top:0.45rem;">Moduler som bruker dataene</div>
            <div class="chip-wrap">{chips(AI_MODULES)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="sidebar-card" style="margin-top: 0.9rem;">
            <div class="eyebrow">Best practice</div>
            <div class="section-title" style="font-size:1.05rem; margin-top:0.45rem;">Hva gir mest AI-verdi?</div>
            <ul>
                <li>Et tydelig narrativ gir bedre konsepter og mer relevante forslag.</li>
                <li>Adresse og matrikkel åpner for mer treffsikker sted- og tomteanalyse.</li>
                <li>BTA, etasjer og brukstype forbedrer kalkyle- og massing-moduler.</li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )


# -----------------------------
# 6. FLASH MESSAGES
# -----------------------------
if "flash_message" in st.session_state:
    st.markdown("<div class='flash-shell'>", unsafe_allow_html=True)
    st.success(st.session_state.pop("flash_message"))
    st.markdown("</div>", unsafe_allow_html=True)


# -----------------------------
# 7. HERO
# -----------------------------
hero_left, hero_right = st.columns([2.25, 1], gap="large")

with hero_left:
    st.markdown(
        """
        <div class="hero-shell">
            <div class="badge">✦ Builtly AI • Project SSOT</div>
            <div class="hero-title">Project Configuration</div>
            <p class="hero-sub">
                Ett kontrollsenter for prosjektets kjerneparametre. Oppdater disse feltene én gang,
                og la Builtly synke kontekst til analyse, kalkyle, konseptutvikling og dokumentasjon.
            </p>
            <div class="hero-pills">
                <span class="pill">AI-native proptech UX</span>
                <span class="pill">Enterprise-grade dataflyt</span>
                <span class="pill">Single Source of Truth</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with hero_right:
    st.markdown(
        f"""
        <div class="mini-panel">
            <div class="eyebrow">Sync status</div>
            <div class="mini-value">{status}</div>
            <div class="mini-copy" style="margin-top:0.55rem; line-height:1.65;">
                {filled_fields} av {total_fields} nøkkelfelt er fylt ut og tilgjengelige for AI-modulene.
            </div>
            <div class="split-row"><span>Kompletthet</span><strong>{pct_complete}%</strong></div>
            <div class="bar-wrap"><div class="bar-fill" style="width:{pct_complete}%;"></div></div>
            <div class="split-row"><span>Sist oppdatert</span><strong>{last_sync}</strong></div>
            <div class="split-row"><span>Prosjektfingeravtrykk</span><strong>{location_fingerprint}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# -----------------------------
# 8. KPI STRIP
# -----------------------------
k1, k2, k3, k4 = st.columns(4, gap="medium")

with k1:
    st.markdown(
        metric_card(
            "Datakompletthet",
            f"{pct_complete}%",
            f"{filled_fields} / {total_fields} SSOT-felt er fylt ut.",
        ),
        unsafe_allow_html=True,
    )
with k2:
    st.markdown(
        metric_card(
            "Primær bruk",
            safe_text(pd.get("b_type")),
            "Brukes av konsept-, kalkyle- og massing-moduler.",
        ),
        unsafe_allow_html=True,
    )
with k3:
    st.markdown(
        metric_card(
            "Bruttoareal",
            f"{format_number(pd.get('bta'))} m²",
            "Samlet BTA som grunnlag for analyse og estimering.",
        ),
        unsafe_allow_html=True,
    )
with k4:
    st.markdown(
        metric_card(
            "Etasjer",
            str(pd.get("etasjer", "—")),
            f"Ca. {format_number(bta_per_floor)} m² per etasje basert på dagens tall.",
        ),
        unsafe_allow_html=True,
    )

st.markdown("<div style='height: 0.55rem;'></div>", unsafe_allow_html=True)


# -----------------------------
# 9. MAIN LAYOUT
# -----------------------------
main_col, side_col = st.columns([3.05, 1.15], gap="large")

with main_col:
    with st.form("project_masterdata_form", clear_on_submit=False):
        st.markdown(
            """
            <div class="section-kicker">Masterdata</div>
            <div class="section-title">Oppdater prosjektets kontrollsenter</div>
            <p class="section-copy">
                Designet for et mer premium, AI-først Builtly-uttrykk: tydeligere hierarki, bedre luft,
                skarpere inputfelter og sterkere kobling til hvordan dataene faktisk brukes i plattformen.
            </p>
            """,
            unsafe_allow_html=True,
        )

        tab_project, tab_location, tab_building = st.tabs(
            ["01  Prosjekt", "02  Lokasjon", "03  Byggdata"]
        )

        with tab_project:
            st.markdown(
                """
                <div class="section-kicker">Kontekst</div>
                <div class="section-title">Generell prosjektinformasjon</div>
                <p class="section-copy">
                    Dette er kjernen i prosjektforståelsen. Jo tydeligere narrativ, desto bedre forslag,
                    analyser og automatiserte leveranser fra AI-modulene.
                </p>
                """,
                unsafe_allow_html=True,
            )

            c1, c2 = st.columns(2, gap="medium")
            new_p_name = c1.text_input(
                "Prosjektnavn",
                value=pd["p_name"],
                placeholder="F.eks. Kilen Kontorpark",
            )
            new_c_name = c2.text_input(
                "Kunde / utvikler",
                value=pd["c_name"],
                placeholder="F.eks. Nordic Property AS",
            )
            new_p_desc = st.text_area(
                "Prosjektbeskrivelse / narrativ",
                value=pd["p_desc"],
                height=200,
                placeholder=(
                    "Beskriv scope, målgruppe, hovedfunksjoner, volum, arkitektoniske grep, "
                    "tekniske krav og eventuelle spesielle rammer."
                ),
            )

            st.markdown(
                """
                <div class="tip-card" style="margin-top: 0.9rem;">
                    <div class="eyebrow">Tips</div>
                    <div class="section-title" style="font-size:1.02rem; margin-top:0.45rem;">Hva AI-en trenger her</div>
                    <ul>
                        <li>Program og brukere: kontor, skole, bolig, handel eller miks.</li>
                        <li>Ambisjon: standard, premium, bærekraft, fleksibilitet eller hurtig utvikling.</li>
                        <li>Særkrav: parkering, logistikk, universell utforming, energikrav eller reguleringshensyn.</li>
                    </ul>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with tab_location:
            st.markdown(
                """
                <div class="section-kicker">Tomt og sted</div>
                <div class="section-title">Lokasjon og matrikkeldata</div>
                <p class="section-copy">
                    Denne delen er avgjørende for stedstilpassede analyser. Adressen og matrikkeldataene
                    danner grunnlag for geodata, regulering, tomteforståelse og videre automatisering.
                </p>
                """,
                unsafe_allow_html=True,
            )

            c3, c4 = st.columns(2, gap="medium")
            new_adresse = c3.text_input(
                "Gateadresse",
                value=pd["adresse"],
                placeholder="F.eks. Dronning Eufemias gate 16",
            )
            new_kommune = c4.text_input(
                "Kommune",
                value=pd["kommune"],
                placeholder="F.eks. Oslo",
            )

            c5, c6 = st.columns(2, gap="medium")
            new_gnr = c5.text_input(
                "Gårdsnummer (Gnr)",
                value=pd["gnr"],
                placeholder="F.eks. 209",
            )
            new_bnr = c6.text_input(
                "Bruksnummer (Bnr)",
                value=pd["bnr"],
                placeholder="F.eks. 447",
            )

            st.markdown(
                f"""
                <div class="tip-card" style="margin-top: 0.9rem;">
                    <div class="eyebrow">Status</div>
                    <div class="section-title" style="font-size:1.02rem; margin-top:0.45rem;">Lokasjonsfingeravtrykk</div>
                    <div class="summary-grid">
                        <div class="summary-row">
                            <span class="summary-key">Adresse</span>
                            <span class="summary-val">{safe_text(pd.get('adresse'), 'Ikke satt')}</span>
                        </div>
                        <div class="summary-row">
                            <span class="summary-key">Kommune</span>
                            <span class="summary-val">{safe_text(pd.get('kommune'), 'Ikke satt')}</span>
                        </div>
                        <div class="summary-row" style="border-bottom:none; padding-bottom:0;">
                            <span class="summary-key">Matrikkel</span>
                            <span class="summary-val">{safe_text(pd.get('gnr'), '—')}/{safe_text(pd.get('bnr'), '—')}</span>
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with tab_building:
            st.markdown(
                """
                <div class="section-kicker">Volum og program</div>
                <div class="section-title">Byggdata og nøkkelmetrikker</div>
                <p class="section-copy">
                    Disse tallene brukes direkte i volumstudier, kalkyler, grovestimater og i prompt-kontekst
                    for flere av Builtly-modulene.
                </p>
                """,
                unsafe_allow_html=True,
            )

            try:
                default_idx = TYPE_OPTIONS.index(pd["b_type"])
            except ValueError:
                default_idx = 1

            c7, c8, c9 = st.columns([1.5, 1, 1], gap="medium")
            new_b_type = c7.selectbox(
                "Primær bruk",
                TYPE_OPTIONS,
                index=default_idx,
            )
            new_etasjer = c8.number_input(
                "Antall etasjer",
                value=int(pd["etasjer"]),
                min_value=1,
                step=1,
            )
            new_bta = c9.number_input(
                "BTA (m²)",
                value=int(pd["bta"]),
                min_value=100,
                step=100,
            )

            est_bta_floor = int(new_bta / new_etasjer) if new_etasjer else 0
            d1, d2, d3 = st.columns(3, gap="medium")
            with d1:
                st.markdown(
                    f"""
                    <div class="derived-card">
                        <div class="metric-label">BTA / etasje</div>
                        <div class="metric-value" style="font-size:1.45rem;">{format_number(est_bta_floor)} m²</div>
                        <div class="metric-helper">Brukes som rask volumindikator i tidligfase.</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with d2:
                st.markdown(
                    f"""
                    <div class="derived-card">
                        <div class="metric-label">Bygningstype</div>
                        <div class="metric-value" style="font-size:1.2rem; line-height:1.25;">{new_b_type}</div>
                        <div class="metric-helper">Setter rammen for relevante AI-arbeidsflyter.</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with d3:
                st.markdown(
                    f"""
                    <div class="derived-card">
                        <div class="metric-label">Volumstatus</div>
                        <div class="metric-value" style="font-size:1.45rem;">{format_number(new_bta)} m²</div>
                        <div class="metric-helper">Skalert over {new_etasjer} etasjer i dagens oppsett.</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        st.markdown(
            """
            <div class="action-strip">
                <div class="action-title">Klar til å synke oppdatert masterdata?</div>
                <p class="action-copy">
                    Ett klikk oppdaterer prosjektets SSOT og gjør den tilgjengelig for resten av Builtly-plattformen.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        save_clicked = st.form_submit_button(
            "Save & Sync Master Data",
            type="primary",
            use_container_width=True,
        )

        if save_clicked:
            st.session_state.project_data.update(
                {
                    "p_name": new_p_name,
                    "c_name": new_c_name,
                    "p_desc": new_p_desc,
                    "adresse": new_adresse,
                    "kommune": new_kommune,
                    "gnr": new_gnr,
                    "bnr": new_bnr,
                    "b_type": new_b_type,
                    "etasjer": int(new_etasjer),
                    "bta": int(new_bta),
                }
            )

            new_pct, _, _ = completion_stats(st.session_state.project_data)
            st.session_state.sync_status = sync_label(new_pct)
            st.session_state.last_sync = datetime.now().strftime("%d.%m.%Y • %H:%M")
            st.session_state.flash_message = (
                f"✅ Masterdata for **{new_p_name}** er nå synket på tvers av Builtly-modulene."
            )
            st.rerun()

with side_col:
    st.markdown(
        f"""
        <div class="summary-card">
            <div class="eyebrow">Live snapshot</div>
            <div class="section-title" style="font-size:1.2rem; margin-top:0.45rem;">Prosjektsammendrag</div>
            <div class="muted">Et raskt overblikk over SSOT-dataene slik de ligger akkurat nå.</div>

            <div class="summary-grid">
                <div class="summary-row">
                    <span class="summary-key">Prosjekt</span>
                    <span class="summary-val">{safe_text(pd.get('p_name'))}</span>
                </div>
                <div class="summary-row">
                    <span class="summary-key">Kunde</span>
                    <span class="summary-val">{safe_text(pd.get('c_name'), 'Ikke satt')}</span>
                </div>
                <div class="summary-row">
                    <span class="summary-key">Lokasjon</span>
                    <span class="summary-val">{safe_text(pd.get('adresse'), 'Ikke satt')}</span>
                </div>
                <div class="summary-row">
                    <span class="summary-key">Kommune</span>
                    <span class="summary-val">{safe_text(pd.get('kommune'), 'Ikke satt')}</span>
                </div>
                <div class="summary-row">
                    <span class="summary-key">Matrikkel</span>
                    <span class="summary-val">{safe_text(pd.get('gnr'), '—')}/{safe_text(pd.get('bnr'), '—')}</span>
                </div>
                <div class="summary-row">
                    <span class="summary-key">Bruk</span>
                    <span class="summary-val">{safe_text(pd.get('b_type'))}</span>
                </div>
                <div class="summary-row">
                    <span class="summary-key">Etasjer</span>
                    <span class="summary-val">{pd.get('etasjer', '—')}</span>
                </div>
                <div class="summary-row" style="border-bottom:none; padding-bottom:0;">
                    <span class="summary-key">BTA</span>
                    <span class="summary-val">{format_number(pd.get('bta'))} m²</span>
                </div>
            </div>

            <div class="eyebrow" style="margin-top:1.15rem;">AI-forbruk</div>
            <div class="chip-wrap">{chips(AI_MODULES)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown(
    """
    <div class="footer-note">
        Builtly Project Control Center • designet for en mer premium, AI-native produktopplevelse.
    </div>
    """,
    unsafe_allow_html=True,
)
