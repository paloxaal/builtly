import streamlit as st
import json
import os
import base64
from pathlib import Path
from datetime import datetime

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="QA & Sign-off | Builtly", layout="wide", initial_sidebar_state="collapsed")

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

# --- 2. LOKAL FIL-DATABASE LOGIKK ---
DB_DIR = Path("qa_database")
PDF_DIR = DB_DIR / "pdfs"
DB_FILE = DB_DIR / "reviews.json"

def init_db():
    """Oppretter mapper og databasefil hvis de ikke eksisterer."""
    DB_DIR.mkdir(exist_ok=True)
    PDF_DIR.mkdir(exist_ok=True)
    if not DB_FILE.exists():
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)

def load_db():
    init_db()
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

init_db()

# --- 3. INGESTION ENGINE (Magi som flytter data fra RAM til Harddisk) ---
if "pending_reviews" in st.session_state and st.session_state.pending_reviews:
    db_data = load_db()
    for doc_id, review in st.session_state.pending_reviews.items():
        if doc_id not in db_data:
            # 1. Lagre selve PDF-filen fysisk
            pdf_path = PDF_DIR / f"{doc_id}.pdf"
            pdf_bytes = review["pdf_bytes"]
            
            # Sikkerhetsnett for ulike FPDF-versjoner (bytes vs string)
            if isinstance(pdf_bytes, str):
                pdf_bytes = pdf_bytes.encode('latin1')
                
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)
            
            # 2. Lagre metadata til JSON-databasen (Uten de tunge bytene)
            meta = review.copy()
            if "pdf_bytes" in meta:
                del meta["pdf_bytes"]
            meta["date_added"] = datetime.now().strftime("%d. %b %Y kl %H:%M")
            
            db_data[doc_id] = meta
            
    # Lagrer databasen og tømmer RAM-en!
    save_db(db_data)
    st.session_state.pending_reviews = {} 

# --- 4. PREMIUM CSS ---
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
    
    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }

    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 8px 24px !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 8px 24px !important; transition: all 0.2s; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important;}

    /* Styling for Tabs (Til Behandling, Godkjent, Avvist) */
    .stTabs [data-baseweb="tab-list"] { background-color: transparent !important; gap: 2rem; }
    .stTabs [data-baseweb="tab"] { color: #9fb0c3 !important; font-weight: 650 !important; font-size: 1.1rem !important; padding-bottom: 1rem !important; }
    .stTabs [aria-selected="true"] { color: #38bdf8 !important; border-bottom-color: #38bdf8 !important; }
    
    .qa-card { background: rgba(12, 21, 32, 0.9); border: 1px solid rgba(120,145,170,0.2); border-radius: 16px; padding: 1.5rem; margin-bottom: 1rem; transition: all 0.2s ease; }
    .qa-card:hover { border-color: rgba(56, 189, 248, 0.4); box-shadow: 0 8px 24px rgba(0,0,0,0.2); }
</style>
""", unsafe_allow_html=True)

# --- 5. HEADER ---
top_l, top_r = st.columns([4, 1])
with top_l:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(logo_html)
with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("← Tilbake til SSOT", use_container_width=True, type="secondary"):
        st.switch_page(find_page("Project"))

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)
st.markdown(f"<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🛡️ Kvalitetssikring (QA) & Sign-off</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: var(--muted); font-size: 1.1rem; margin-bottom: 2rem;'>Senter for seniorgjennomgang av AI-genererte konseptnotater.</p>", unsafe_allow_html=True)

# --- 6. HENT DATA FRA HARDDISK ---
db_data = load_db()

# Sorter rapportene i riktig bøtte
pending = {k: v for k, v in db_data.items() if v.get("status") not in ["Godkjent", "Avvist"]}
approved = {k: v for k, v in db_data.items() if v.get("status") == "Godkjent"}
rejected = {k: v for k, v in db_data.items() if v.get("status") == "Avvist"}

# --- 7. TABS & UI ---
tab1, tab2, tab3 = st.tabs([f"📥 Til Behandling ({len(pending)})", f"✅ Godkjent ({len(approved)})", f"❌ Avvist ({len(rejected)})"])

def render_qa_card(doc_id, meta, show_actions=False):
    st.markdown('<div class="qa-card">', unsafe_allow_html=True)
    
    # Header rad
    st.markdown(f"<h3 style='margin-top:0; margin-bottom:0.5rem; color:#f5f7fb;'>📄 {meta['title']} <span style='color:#9fb0c3; font-size:1.1rem;'>— {meta['module']}</span></h3>", unsafe_allow_html=True)
    st.markdown(f"<p style='color:#9fb0c3; font-size:0.9rem; margin-bottom:1.5rem;'><b>ID:</b> <code>{doc_id}</code> &nbsp;|&nbsp; <b>Opprettet:</b> {meta.get('date_added', 'Ukjent')} &nbsp;|&nbsp; <b>Status:</b> <span style='color:#f4bf4f;'>{meta['status']}</span></p>", unsafe_allow_html=True)
    
    pdf_path = PDF_DIR / f"{doc_id}.pdf"
    
    c1, c2, c3, _ = st.columns([2, 1.5, 1.5, 4])
    with c1:
        if pdf_path.exists():
            with open(pdf_path, "rb") as f:
                st.download_button("⬇️ Last ned PDF for gjennomlesning", f.read(), file_name=f"{doc_id}.pdf", key=f"dl_{doc_id}", type="secondary", use_container_width=True)
        else:
            st.error("PDF-fil mangler på server!")
            
    if show_actions:
        with c2:
            if st.button("✅ Godkjenn (Sign-off)", key=f"app_{doc_id}", type="primary", use_container_width=True):
                db_data[doc_id]["status"] = "Godkjent"
                save_db(db_data)
                st.rerun()
        with c3:
            if st.button("❌ Avvis / Retur", key=f"rej_{doc_id}", use_container_width=True):
                db_data[doc_id]["status"] = "Avvist"
                save_db(db_data)
                st.rerun()
                
    st.markdown('</div>', unsafe_allow_html=True)


with tab1:
    if not pending:
        st.info("Køen er tom! Ingen nye rapporter til vurdering.")
    for doc_id, meta in reversed(pending.items()): # Viser nyeste først
        render_qa_card(doc_id, meta, show_actions=True)

with tab2:
    if not approved:
        st.info("Ingen godkjente rapporter enda.")
    for doc_id, meta in reversed(approved.items()):
        render_qa_card(doc_id, meta, show_actions=False)

with tab3:
    if not rejected:
        st.info("Ingen avviste rapporter.")
    for doc_id, meta in reversed(rejected.items()):
        render_qa_card(doc_id, meta, show_actions=False)
