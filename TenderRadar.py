# -*- coding: utf-8 -*-
"""
Builtly | Tender Radar (Streamlit page)
─────────────────────────────────────────────────────────────────
UI for å konfigurere overvåkinger og se treff-feed.
Plasseres som pages/TenderRadar.py i Builtly-root.

Avhenger av:
  - tender_radar_profile.py
  - tender_radar_poller.py (kun for manuell trigger fra UI)
  - tender_radar_screener.py (kun for manuell trigger fra UI)

Bakgrunnsjobber (poll + screen + notify) kjører via Render Cron
og skrives til Supabase — UI-et leser derfra.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from tender_radar_profile import (
    get_or_create_profile,
    update_profile,
    create_watch,
    list_watches,
    update_watch,
    delete_watch,
    CPV_CATEGORIES,
    NORWEGIAN_REGIONS,
    DISCIPLINES,
    PROCUREMENT_TYPES,
    cpv_label,
)

try:
    from supabase import create_client
    _SB_URL = os.environ.get("SUPABASE_URL")
    _SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    _sb = create_client(_SB_URL, _SB_KEY) if (_SB_URL and _SB_KEY) else None
except Exception:
    _sb = None


# ─── Page config + styling ───────────────────────────────────────
st.set_page_config(
    page_title="Builtly | Tender Radar",
    layout="wide",
    initial_sidebar_state="collapsed",
)


st.markdown(
    """
    <style>
    .stApp { background-color: #06111a; color: #f5f7fb; }
    .radar-hero {
        background: linear-gradient(135deg, rgba(56,189,248,0.1), rgba(56,189,248,0.02));
        border: 1px solid rgba(56,189,248,0.25);
        border-radius: 16px;
        padding: 2rem;
        margin-bottom: 2rem;
    }
    .radar-hero h1 { margin: 0; font-size: 1.8rem; color: #f5f7fb; }
    .radar-hero p { color: #c8d3df; margin: 0.5rem 0 0 0; }
    .alert-card {
        background: rgba(10,22,35,0.6);
        border: 1px solid rgba(120,145,170,0.2);
        border-radius: 12px;
        padding: 1.2rem;
        margin-bottom: 0.8rem;
    }
    .fit-badge {
        display: inline-block;
        padding: 3px 12px;
        border-radius: 6px;
        font-weight: 700;
        font-size: 0.85rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── Auth (placeholder — Builtly har egen) ───────────────────────
def current_user_email() -> str:
    """Hent bruker-epost fra Builtly-auth. Her: placeholder."""
    return st.session_state.get("builtly_user_email") or os.environ.get("BUILTLY_USER", "demo@builtly.ai")


user_email = current_user_email()
profile = get_or_create_profile(user_email=user_email)

if not profile:
    st.error(
        "Kunne ikke hente eller opprette profil. Sjekk at Supabase-tilkobling fungerer "
        "og at tender_radar-tabellene er opprettet (se supabase_schema.sql)."
    )
    st.stop()


# ─── Hero ────────────────────────────────────────────────────────
st.markdown(
    f"""
    <div class="radar-hero">
        <div style="font-size:11px;letter-spacing:0.1em;text-transform:uppercase;color:#38bdf8;font-weight:600;">
            BUILTLY TENDER RADAR
        </div>
        <h1>Proaktiv anbudsovervåking med AI-screening</h1>
        <p>
            Definer profilen din. Builtly overvåker Doffin og Mercell kontinuerlig, kjører
            AI-screening på hver ny kunngjøring, og varsler deg når noe matcher.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)


# ─── Tabs ────────────────────────────────────────────────────────
tab_feed, tab_watches, tab_profile, tab_ops = st.tabs([
    "Treff-feed",
    "Overvåkinger",
    "Profil",
    "Ops",
])


# ═════════════════════════════════════════════════════════════════
# TAB 1: TREFF-FEED
# ═════════════════════════════════════════════════════════════════
with tab_feed:
    st.markdown("### Siste treff")

    # Filtre
    fcol1, fcol2, fcol3 = st.columns(3)
    with fcol1:
        status_filter = st.multiselect(
            "Status",
            ["new", "notified", "viewed", "in_analysis", "dismissed"],
            default=["new", "notified", "viewed"],
        )
    with fcol2:
        min_fit = st.slider("Minimum fit-score", 0, 100, 50, step=5)
    with fcol3:
        days_back = st.slider("Dager tilbake", 1, 60, 14)

    # Hent alerts for denne profilen
    alerts: List[Dict[str, Any]] = []
    if _sb:
        try:
            watches = list_watches(profile["profile_id"])
            watch_ids = [w["watch_id"] for w in watches]
            if watch_ids:
                since = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
                resp = (
                    _sb.table("tender_alerts")
                    .select("*, tender_sources(*), tender_watches(name)")
                    .in_("watch_id", watch_ids)
                    .gte("created_at", since)
                    .gte("fit_score", min_fit)
                    .order("fit_score", desc=True)
                    .execute()
                )
                alerts = resp.data or []
                if status_filter:
                    alerts = [a for a in alerts if a.get("status") in status_filter]
        except Exception as e:
            st.error(f"Kunne ikke hente alerts: {e}")

    if not alerts:
        st.info(
            "Ingen treff enda. Sjekk at du har minst én aktiv overvåking, "
            "og vent til neste poll-kjøring (hvert 30. min)."
        )
    else:
        st.caption(f"Viser {len(alerts)} treff")
        for alert in alerts:
            src = alert.get("tender_sources") or {}
            watch_meta = alert.get("tender_watches") or {}
            fit = alert.get("fit_score") or 0
            fit_color = "#10b981" if fit >= 80 else "#f59e0b" if fit >= 60 else "#64748b"

            with st.expander(
                f"Fit {fit} · {src.get('title', '(uten tittel)')[:100]}  ·  {src.get('buyer_name', '')[:40]}",
                expanded=False,
            ):
                mcol1, mcol2 = st.columns([3, 1])
                with mcol1:
                    if alert.get("quick_summary"):
                        st.markdown(alert["quick_summary"])
                    if alert.get("why_interesting"):
                        st.info(f"**Hvorfor interessant:** {alert['why_interesting']}")
                    if alert.get("fit_reasoning"):
                        st.caption(f"*Vurdering:* {alert['fit_reasoning']}")

                with mcol2:
                    st.markdown(
                        f'<div class="fit-badge" style="background:{fit_color};color:#06111a;">'
                        f'Fit {fit}/100</div>',
                        unsafe_allow_html=True,
                    )
                    st.caption(f"**Frist:** {src.get('submission_deadline', '')}")
                    if src.get("estimated_value_nok"):
                        st.caption(f"**Verdi:** {src['estimated_value_nok']:,.0f} NOK".replace(",", " "))
                    st.caption(f"**Watch:** {watch_meta.get('name', '')}")
                    st.caption(f"**Status:** `{alert.get('status', 'new')}`")

                # Risikoflagg
                risks = alert.get("quick_risk_flags") or []
                if risks:
                    st.markdown("**Risikoflagg:**")
                    for r in risks[:5]:
                        sev = (r.get("severity") or "").upper()
                        sev_emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(sev, "⚪")
                        st.caption(f"{sev_emoji} {r.get('issue', '')}")

                # Handlinger
                acol1, acol2, acol3, acol4 = st.columns([1, 1, 1, 2])
                if acol1.button("✓ Sett som lest", key=f"view_{alert['alert_id']}"):
                    if _sb:
                        _sb.table("tender_alerts").update({
                            "status": "viewed",
                            "viewed_at": datetime.now(timezone.utc).isoformat(),
                        }).eq("alert_id", alert["alert_id"]).execute()
                    st.rerun()
                if acol2.button("✗ Avvis", key=f"dismiss_{alert['alert_id']}"):
                    if _sb:
                        _sb.table("tender_alerts").update({"status": "dismissed"}).eq(
                            "alert_id", alert["alert_id"]
                        ).execute()
                    st.rerun()
                if src.get("source_url"):
                    acol3.markdown(f"[Doffin →]({src['source_url']})")
                if src.get("kgv_url"):
                    acol4.markdown(f"[{src.get('kgv_provider', 'KGV')} →]({src['kgv_url']})")


# ═════════════════════════════════════════════════════════════════
# TAB 2: OVERVÅKINGER
# ═════════════════════════════════════════════════════════════════
with tab_watches:
    st.markdown("### Aktive overvåkinger")
    watches = list_watches(profile["profile_id"], include_inactive=True)

    if watches:
        for w in watches:
            is_active = w.get("is_active", True)
            with st.expander(
                f"{'🟢' if is_active else '⚪'} {w['name']}",
                expanded=False,
            ):
                st.caption(f"ID: `{w['watch_id']}`")
                st.caption(f"CPV: {', '.join(w.get('cpv_codes') or []) or '—'}")
                st.caption(f"Regioner: {', '.join(w.get('regions') or []) or '—'}")
                st.caption(f"Fag: {', '.join(w.get('discipline_focus') or []) or '—'}")
                if w.get("min_value_nok") or w.get("max_value_nok"):
                    lo = w.get("min_value_nok") or 0
                    hi = w.get("max_value_nok") or "∞"
                    st.caption(f"Verdi: {lo:,.0f} – {hi} NOK".replace(",", " "))
                st.caption(f"Kilder: {', '.join(w.get('sources') or [])}")

                if is_active and st.button("Pauser", key=f"pause_{w['watch_id']}"):
                    delete_watch(w["watch_id"])
                    st.rerun()
                elif not is_active and st.button("Aktiver igjen", key=f"reactivate_{w['watch_id']}"):
                    update_watch(w["watch_id"], {"is_active": True})
                    st.rerun()
    else:
        st.info("Ingen overvåkinger enda. Legg til en under.")

    st.markdown("---")
    st.markdown("### Legg til ny overvåking")

    with st.form("new_watch_form"):
        name = st.text_input("Navn", placeholder="F.eks. 'Bygg og anlegg Trøndelag'")

        col1, col2 = st.columns(2)
        with col1:
            cpv_options = list(CPV_CATEGORIES.keys())
            cpv_selected = st.multiselect(
                "CPV-koder (inkluder)",
                cpv_options,
                format_func=lambda c: f"{c} — {CPV_CATEGORIES.get(c, '')}",
            )
            regions_selected = st.multiselect("Regioner", NORWEGIAN_REGIONS)
            disciplines_selected = st.multiselect("Fagfokus", DISCIPLINES)

        with col2:
            proc_types = st.multiselect("Kontraktstype", PROCUREMENT_TYPES)
            min_val = st.number_input("Min verdi (NOK)", min_value=0, value=0, step=100_000)
            max_val = st.number_input("Max verdi (NOK, 0 = ingen)", min_value=0, value=0, step=1_000_000)
            sources_selected = st.multiselect(
                "Kilder",
                ["doffin", "mercell"],
                default=["doffin"],
            )

        col3, col4 = st.columns(2)
        with col3:
            kw_positive = st.text_area(
                "Positive nøkkelord (én per linje)",
                placeholder="betong\nfasade\nrehabilitering",
                height=100,
            )
        with col4:
            kw_negative = st.text_area(
                "Negative nøkkelord (én per linje)",
                placeholder="sykehus\nfengsel",
                height=100,
            )

        company_caps = st.text_area(
            "Selskapskapasiteter (hjelper AI med matching)",
            placeholder="Vi er totalentreprenør med 50 ansatte. Spesialisert på bolig og næring i Midt-Norge. Omsetter 250 MNOK/år. Sertifisert innen BREEAM NOR.",
            height=80,
        )

        fit_threshold = st.slider("Varsle når fit-score er over", 30, 95, 70, step=5)
        notif_mode = st.selectbox("Varslingsmodus", ["instant", "daily_digest", "weekly_digest"])

        submitted = st.form_submit_button("Opprett overvåking", type="primary")
        if submitted:
            if not name:
                st.error("Gi overvåkingen et navn.")
            else:
                watch_data = {
                    "name": name,
                    "cpv_codes": cpv_selected,
                    "regions": regions_selected,
                    "discipline_focus": disciplines_selected,
                    "procurement_types": proc_types,
                    "min_value_nok": min_val if min_val > 0 else None,
                    "max_value_nok": max_val if max_val > 0 else None,
                    "sources": sources_selected,
                    "keywords_positive": [k.strip() for k in kw_positive.split("\n") if k.strip()],
                    "keywords_negative": [k.strip() for k in kw_negative.split("\n") if k.strip()],
                    "company_capabilities": company_caps or None,
                    "fit_threshold": fit_threshold,
                    "notification_mode": notif_mode,
                }
                created = create_watch(profile["profile_id"], watch_data)
                if created:
                    st.success(f"Overvåking '{name}' opprettet.")
                    st.rerun()
                else:
                    st.error("Kunne ikke opprette overvåking.")


# ═════════════════════════════════════════════════════════════════
# TAB 3: PROFIL
# ═════════════════════════════════════════════════════════════════
with tab_profile:
    st.markdown("### Profilinnstillinger")

    with st.form("profile_form"):
        pcol1, pcol2 = st.columns(2)
        with pcol1:
            new_name = st.text_input("Navn", value=profile.get("display_name") or "")
            new_company = st.text_input("Selskap", value=profile.get("company_name") or "")
            new_org_no = st.text_input("Org.nr", value=profile.get("company_org_no") or "")
        with pcol2:
            new_email = st.text_input(
                "Varsel-epost",
                value=profile.get("default_email") or profile.get("user_email", ""),
            )
            new_slack = st.text_input(
                "Slack webhook (valgfritt)",
                value=profile.get("default_slack_webhook") or "",
                help="https://hooks.slack.com/services/...",
            )
            new_threshold = st.slider(
                "Standard fit-terskel",
                30, 95,
                profile.get("default_fit_threshold") or 70,
                step=5,
            )

        saved = st.form_submit_button("Lagre", type="primary")
        if saved:
            update_profile(profile["profile_id"], {
                "display_name": new_name,
                "company_name": new_company,
                "company_org_no": new_org_no,
                "default_email": new_email,
                "default_slack_webhook": new_slack or None,
                "default_fit_threshold": new_threshold,
            })
            st.success("Profil oppdatert.")
            st.rerun()


# ═════════════════════════════════════════════════════════════════
# TAB 4: OPS (debug + manuell trigger)
# ═════════════════════════════════════════════════════════════════
with tab_ops:
    st.markdown("### Operasjoner og debugging")
    st.caption("Kun for administratorer. Cron-worker kjører normalt hvert 30. min.")

    ocol1, ocol2, ocol3 = st.columns(3)
    if ocol1.button("Kjør poll nå (Doffin)"):
        with st.spinner("Poller Doffin..."):
            try:
                from tender_radar_poller import run_full_poll
                result = run_full_poll(sources=["doffin"], max_notices_per_source=50)
                st.json(result)
            except Exception as e:
                st.error(str(e))
    if ocol2.button("Kjør AI-screening nå"):
        with st.spinner("Screener nye kunngjøringer..."):
            try:
                from tender_radar_screener import screen_new_sources_against_all_watches
                result = screen_new_sources_against_all_watches(max_screenings=200)
                st.json(result)
            except Exception as e:
                st.error(str(e))
    if ocol3.button("Send ventende varsler"):
        with st.spinner("Sender varsler..."):
            try:
                from tender_radar_notifier import process_notifications
                result = process_notifications()
                st.json(result)
            except Exception as e:
                st.error(str(e))

    st.markdown("---")
    st.markdown("### Siste poller-kjøringer")
    if _sb:
        try:
            runs_resp = (
                _sb.table("tender_radar_runs")
                .select("*")
                .order("started_at", desc=True)
                .limit(20)
                .execute()
            )
            runs = runs_resp.data or []
            if runs:
                st.dataframe(pd.DataFrame(runs), use_container_width=True, hide_index=True)
            else:
                st.caption("Ingen kjøringer logget enda.")
        except Exception as e:
            st.error(str(e))
