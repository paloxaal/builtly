from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from builtly_module_kit import (
    configure_page,
    dataframe_download,
    json_download,
    render_metric_cards,
    render_panel,
    render_project_snapshot,
    render_section,
    render_hero,
)

project = configure_page("Builtly | White-label API & Partnerprogram", "🔌")

render_hero(
    eyebrow="White-label API & Partnerprogram",
    title="Skaleringsmotoren som lar Builtly vokse via partnere, ikke bare egne folk.",
    subtitle=(
        "Legg et tenant-basert API-lag over eksisterende plattform slik at Norconsult-, AECOM- eller bank-lignende partnere kan kalle Builtly med egne data, "
        "få tilbake brandede leveranser og bruke egne fagpersoner til sign-off. Dette er workflow infrastructure – ikke bare enda en modul."
    ),
    pills=["Multi-tenant", "OAuth / API keys", "Webhooks", "Own sign-off", "White-label"],
    badge="Scale engine",
)

left, right = st.columns([1.15, 0.85], gap="large")
with left:
    render_section(
        "Tenantoppsett og partnerprofil",
        "Sett opp en partner som får egne maler, branding, rate limits, webhooks og sign-off-regler. Dette bør bygges som et abstrahert lag over samme plattform – ikke som egne installasjoner per kunde.",
        "Partner setup",
    )

    p1, p2 = st.columns(2)
    with p1:
        partner_name = st.text_input("Partnernavn", value="Nordic Engineering Group")
        partner_type = st.selectbox("Partnertype", ["Rådgiver", "Entreprenør", "Bank / forsikring", "Enterprise integrator"], index=0)
        auth_mode = st.selectbox("Autentisering", ["OAuth 2.0", "API keys", "SAML + service account"], index=0)
        signoff_mode = st.radio("Sign-off", ["Partner signerer selv", "Builtly-nettverk signerer", "Hybrid"], horizontal=False)
    with p2:
        tenant_tier = st.selectbox("Tenant tier", ["Launch", "Growth", "Enterprise"], index=1)
        monthly_calls = st.number_input("API-kall / måned", min_value=100, value=25000, step=100)
        rate_limit = st.number_input("Rate limit (requests/min)", min_value=10, value=180, step=10)
        webhook_enabled = st.toggle("Webhook-støtte", value=True)

    b1, b2 = st.columns(2)
    with b1:
        report_branding = st.text_input("Rapportbranding", value=f"{partner_name} | Powered by Builtly")
        primary_modules = st.multiselect(
            "Moduler partneren skal ha tilgang til",
            [
                "Geo", "Brann", "Akustikk", "Trafikk", "RIB", "SHA", "MOP", "BREEAM", "Tender Control", "Quantity & Scope", "Yield", "Climate Risk"
            ],
            default=["Geo", "Tender Control", "Quantity & Scope", "Climate Risk"],
        )
    with b2:
        project_sync = st.multiselect(
            "Integrasjonsmål",
            ["CRM", "Prosjektstyring", "Dokumentarkiv", "ERP/kalkyle", "PowerBI / data warehouse"],
            default=["Prosjektstyring", "Dokumentarkiv", "CRM"],
        )
        billing_mode = st.selectbox("Fakturering", ["Fast abonnement", "Abonnement + usage", "Revenue share"], index=1)

    request_template = {
        "tenant_id": partner_name.lower().replace(" ", "-")[:18],
        "brand": report_branding,
        "modules": primary_modules,
        "project": {
            "name": project.get("p_name", "Nytt Prosjekt"),
            "client": project.get("c_name", ""),
            "address": project.get("adresse", ""),
            "municipality": project.get("kommune", ""),
        },
        "signoff": signoff_mode,
        "auth": auth_mode,
        "webhooks": webhook_enabled,
    }

    response_template = {
        "job_id": "blt_job_20260314_001",
        "status": "review_ready",
        "tenant": request_template["tenant_id"],
        "documents": [
            {"module": module, "url": f"https://api.builtly.ai/v1/jobs/blt_job_20260314_001/{module.lower().replace(' ', '-')}.pdf"}
            for module in primary_modules[:3]
        ],
        "signoff_owner": "partner_user" if signoff_mode == "Partner signerer selv" else "builtly_network",
        "audit_trail": True,
    }

    monthly_price = {
        "Launch": 45000,
        "Growth": 120000,
        "Enterprise": 280000,
    }[tenant_tier]
    usage_uplift = int(monthly_calls * 1.8)
    estimated_arr = (monthly_price * 12) + usage_uplift * 12

    render_metric_cards(
        [
            {"label": "Aktive moduler", "value": f"{len(primary_modules)}", "desc": "Alle moduler bør kalles via samme tenant- og QA-ryggrad."},
            {"label": "API-kapasitet", "value": f"{monthly_calls:,}/mnd".replace(',', ' '), "desc": "Planlagt volum for batch-kjøring, partnerbruk og white-label-leveranser."},
            {"label": "Rate limit", "value": f"{rate_limit}/min", "desc": "Bør styres tenant-vis med isolert ressursbruk og observability."},
            {"label": "Potensiell ARR", "value": f"{estimated_arr:,} NOK".replace(',', ' '), "desc": "Illustrativ kontraktsverdi for én større partner-tenant."},
        ]
    )

    tabs = st.tabs(["Tenant blueprint", "API / webhooks", "Branding", "Commercial model"])
    with tabs[0]:
        tenant_df = pd.DataFrame(
            [
                {"Område": "Tenant-ID", "Valg": request_template["tenant_id"], "Kommentar": "Eget miljø, egne maler og egen fakturalogikk"},
                {"Område": "Autentisering", "Valg": auth_mode, "Kommentar": "API gateway med rate limits og sporbarhet"},
                {"Område": "Sign-off", "Valg": signoff_mode, "Kommentar": "Partneren bør som hovedregel eie faglig ansvar"},
                {"Område": "Webhook", "Valg": "På" if webhook_enabled else "Av", "Kommentar": "Muliggjør integrasjon mot partnerens systemer"},
                {"Område": "Integrasjoner", "Valg": ", ".join(project_sync), "Kommentar": "Minst CRM og dokumentarkiv for operativ bruk"},
            ]
        )
        st.dataframe(tenant_df, use_container_width=True, hide_index=True)
        dataframe_download(tenant_df, "Last ned tenant blueprint (.csv)", "partner_tenant_blueprint.csv")
    with tabs[1]:
        endpoint_df = pd.DataFrame(
            [
                {"Endpoint": "POST /v1/jobs", "Beskrivelse": "Oppretter ny Builtly-jobb for valgt modul og tenant"},
                {"Endpoint": "GET /v1/jobs/{job_id}", "Beskrivelse": "Henter status, QA-state og dokumentlenker"},
                {"Endpoint": "POST /v1/webhooks/test", "Beskrivelse": "Validerer partnerens webhook-endepunkt"},
                {"Endpoint": "GET /v1/tenants/{tenant_id}/usage", "Beskrivelse": "Henter forbruk, fakturagrunnlag og rate limit-status"},
            ]
        )
        st.dataframe(endpoint_df, use_container_width=True, hide_index=True)
        st.code(json.dumps(request_template, indent=2, ensure_ascii=False), language="json")
        st.code(json.dumps(response_template, indent=2, ensure_ascii=False), language="json")
    with tabs[2]:
        brand_df = pd.DataFrame(
            [
                {"Element": "Logo og topplinje", "Løsning": "Tenant-spesifikk brandpakke"},
                {"Element": "Rapportmal", "Løsning": "Partnerens struktur og formatstandard"},
                {"Element": "Signatur og ansvar", "Løsning": "Partnerbruker eller hybrid"},
                {"Element": "Portalopplevelse", "Løsning": "Tenant-spesifikke navn, tekster og modulvalg"},
            ]
        )
        st.dataframe(brand_df, use_container_width=True, hide_index=True)
    with tabs[3]:
        pricing_df = pd.DataFrame(
            [
                {"Tier": "Launch", "Månedlig pris": "45 000 NOK", "Typisk kunde": "Mindre partner som tester 1–2 moduler"},
                {"Tier": "Growth", "Månedlig pris": "120 000 NOK", "Typisk kunde": "Rådgiver/entreprenør med flere prosjektteam"},
                {"Tier": "Enterprise", "Månedlig pris": "280 000 NOK+", "Typisk kunde": "Stor enterprise med white-label, API og compliancekrav"},
            ]
        )
        st.dataframe(pricing_df, use_container_width=True, hide_index=True)
        dataframe_download(pricing_df, "Last ned prisoversikt (.csv)", "partner_pricing.csv")

    json_download(
        {
            "module": "White-label API & Partnerprogram",
            "partner_name": partner_name,
            "partner_type": partner_type,
            "tenant_tier": tenant_tier,
            "auth_mode": auth_mode,
            "signoff_mode": signoff_mode,
            "modules": primary_modules,
            "integrations": project_sync,
            "monthly_calls": monthly_calls,
            "estimated_arr_nok": estimated_arr,
        },
        "Eksporter partneroppsett (.json)",
        "partner_api_blueprint.json",
    )

with right:
    render_section(
        "Den egentlige skaleringsmotoren",
        "Hvis målet er at store miljøer en dag skal kjøpe eller bygge Builtly inn i sine egne systemer, må plattformen tenkes som workflow infrastructure. White-label-laget gjør nettopp det.",
        "Why it matters",
    )
    render_project_snapshot(project, badge="White-label ready")
    render_panel(
        "Hvorfor dette er viktigere enn enda en isolert fagmodul",
        "Et partner- og API-lag lar dere vokse via eksisterende distribusjon, kundetillit og fagpersoner hos andre – uten å bygge full lokal leveranseorganisasjon i hvert marked.",
        [
            "Partneren beholder kunden, brand og gjerne sign-off",
            "Builtly leverer motor, QA-flyt, audit trail og dokumentorkestrering",
            "Tenant-modell gjør det mulig å skalere mange partnere på samme plattform",
            "API og webhooks gjør løsningen integrerbar i eksisterende systemlandskap",
        ],
        tone="blue",
        badge="Scale thesis",
    )
    render_panel(
        "Hva som må være på plass i MVP",
        "Ikke start med alt. Start med et robust partnerlag som kan brukes av de første 1–3 designpartnerne.",
        [
            "Tenant-arkitektur med isolert branding og rapportmaler",
            "OAuth/API-keys og tydelige rate limits",
            "Webhook-støtte for status og dokumentleveranse",
            "Partnerportal med onboarding, nøkler, dokumentasjon og fakturagrunnlag",
        ],
        tone="gold",
        badge="Build now",
    )
    st.metric("Primær effekt", "Skalerbar distribusjon", "Vekst uten å ansette tilsvarende i hvert marked")
    st.metric("M&A / exit-relevans", "Høy", "Gjør Builtly lettere å adoptere av store enterprise-aktører")

render_section(
    "Anbefalt rekkefølge",
    "Først utbyggere og entreprenører. Deretter rådgivere. Så enterprise/white-label. Dette partnerlaget er ikke første salgsprodukt i markedet – men det er laget som gjør at alt annet kan vokse globalt uten at dere bygger et nytt konsulentselskap hver gang.",
    "Go-to-market",
)

c1, c2 = st.columns(2, gap="large")
with c1:
    render_panel(
        "Designprinsipper",
        "White-label må være leverandøruavhengig, sporbar og enkel å rulle ut til nye tenants.",
        [
            "Samme plattform, flere tenants",
            "Samme API-kontrakt, ulike brandpakker",
            "Manuell overstyring og revisjonslogg må bevares også i white-label",
            "Partneren må kunne velge egne fagpersoner for sign-off",
        ],
        tone="green",
        badge="Principles",
    )
with c2:
    render_panel(
        "Kommersiell modell",
        "Tenk faste tiers + usage + eventuelt partner-revshare i enkelte kanaler.",
        [
            "Launch: onboarding av pilotpartnere",
            "Growth: flerbrukere, flere moduler, høyere kallvolum",
            "Enterprise: white-label, API, webhooks, SSO og avansert støtte",
            "Kan senere kombineres med partner marketplace og certification program",
        ],
        tone="blue",
        badge="Commercial",
    )
