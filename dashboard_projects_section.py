"""
dashboard_projects_section.py
-----------------------------
Drop-in-snippet for Builtly-dashboardet. Viser en "Mine prosjekter"-seksjon
med kort per prosjekt. Klikk på kort setter aktivt prosjekt og åpner
Project.py med prosjektet forhåndslastet.

Legg inn slik i Builtly_AI_frontpage_access_gate_expanded.py, over
"Mine rapporter"-seksjonen:

    import dashboard_projects_section
    dashboard_projects_section.render()

Alternativt: kopier render_projects_section()-funksjonen inline.
"""

from __future__ import annotations

import streamlit as st
from datetime import datetime


def _format_date(iso_str: str) -> str:
    """Vis dato i lett format: '16. apr 2026' fra ISO-timestamp."""
    if not iso_str:
        return "—"
    try:
        # Håndter både '2026-04-16T22:30:00Z' og '2026-04-16T22:30:00+00:00'
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        months = {1: "jan", 2: "feb", 3: "mar", 4: "apr", 5: "mai", 6: "jun",
                  7: "jul", 8: "aug", 9: "sep", 10: "okt", 11: "nov", 12: "des"}
        return f"{dt.day}. {months.get(dt.month, '')} {dt.year}"
    except Exception:
        return iso_str[:10] if len(iso_str) >= 10 else iso_str


def render():
    """Hovedfunksjon — ring fra dashboardet for å vise prosjekt-seksjonen."""
    try:
        from builtly_projects import (
            list_projects,
            set_active_project,
            clear_active_project,
            create_project,
        )
    except ImportError:
        return  # Modul ikke tilgjengelig

    if not st.session_state.get("user_authenticated"):
        return

    projects = list_projects()

    # Header
    st.markdown("""<div style="margin-top:2rem; margin-bottom:1rem;">
        <h2 style="margin:0; color:#f5f7fb; font-size:1.6rem;">Mine prosjekter</h2>
        <p style="margin:0.3rem 0 0; color:#9fb0c3; font-size:0.95rem;">
            Klikk på et prosjekt for å fortsette der du slapp.
        </p>
    </div>""", unsafe_allow_html=True)

    # "Nytt prosjekt"-knapp
    _nc1, _nc2, _nc3 = st.columns([2, 1, 2])
    with _nc2:
        if st.button("＋ Nytt prosjekt", type="primary", use_container_width=True, key="dash_new_proj"):
            st.session_state["_dash_show_new_proj"] = True

    if st.session_state.get("_dash_show_new_proj"):
        with st.form("dash_new_project_form", clear_on_submit=True):
            _name = st.text_input("Prosjektnavn", placeholder="F.eks. Saga Park")
            _c1, _c2 = st.columns(2)
            _submit = _c1.form_submit_button("Opprett og åpne", type="primary", use_container_width=True)
            _cancel = _c2.form_submit_button("Avbryt", use_container_width=True)
            if _submit and _name.strip():
                ok, err, pid = create_project(_name.strip(), ssot={})
                if ok and pid:
                    set_active_project(pid)
                    st.session_state["_dash_show_new_proj"] = False
                    # Navigér til Project.py
                    try:
                        st.switch_page("pages/Project.py")
                    except Exception:
                        try:
                            st.switch_page("Project.py")
                        except Exception:
                            st.success(f"Prosjekt «{_name}» opprettet. Åpne Project-siden manuelt.")
                else:
                    st.error(f"Kunne ikke opprette: {err}")
            elif _cancel:
                st.session_state["_dash_show_new_proj"] = False
                st.rerun()

    if not projects:
        st.markdown("""<div style="padding:2rem; text-align:center; color:#9fb0c3;
            background:rgba(255,255,255,0.02); border:1px dashed rgba(255,255,255,0.1);
            border-radius:16px; margin-top:1rem;">
            Du har ingen lagrede prosjekter enda. Klikk <strong>＋ Nytt prosjekt</strong> for å starte.
        </div>""", unsafe_allow_html=True)
        return

    # Rutenett — 3 kolonner
    cols_per_row = 3
    for i in range(0, len(projects), cols_per_row):
        row = projects[i:i + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, proj in zip(cols, row):
            with col:
                pid = proj["id"]
                name = proj.get("name", "Uten navn")
                updated = _format_date(proj.get("updated_at", ""))
                ssot = proj.get("ssot") or {}
                # Utdrag fra SSOT for kortinfo
                adresse = ssot.get("adresse") or ssot.get("kommune") or "Ingen adresse"
                b_type = ssot.get("b_type") or ""

                st.markdown(f"""<div style="padding:1.2rem; background:rgba(255,255,255,0.03);
                    border:1px solid rgba(120,145,170,0.18); border-radius:16px; margin-bottom:0.6rem;
                    min-height:130px;">
                    <div style="font-size:0.7rem; text-transform:uppercase; letter-spacing:0.1em;
                        color:#9fb0c3; margin-bottom:0.3rem;">Prosjekt</div>
                    <div style="font-size:1.15rem; font-weight:700; color:#f5f7fb; margin-bottom:0.4rem;
                        overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{name}</div>
                    <div style="font-size:0.85rem; color:#c8d3df; margin-bottom:0.2rem;
                        overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">📍 {adresse}</div>
                    <div style="font-size:0.75rem; color:#9fb0c3;">Sist endret: {updated}</div>
                </div>""", unsafe_allow_html=True)

                if st.button("Åpne", key=f"open_proj_{pid}", use_container_width=True):
                    set_active_project(pid)
                    try:
                        st.switch_page("pages/Project.py")
                    except Exception:
                        try:
                            st.switch_page("Project.py")
                        except Exception:
                            st.rerun()
