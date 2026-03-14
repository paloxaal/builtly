from __future__ import annotations

import math
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
    tone_from_score,
)

project = configure_page("Builtly | Klimarisiko", "🌊")

render_hero(
    eyebrow="Climate Risk",
    title="Porteføljevennlig klimarisiko som banker, forsikring og eiendom faktisk kan abonnere på.",
    subtitle=(
        "Klimarisikomodulen er bygget for offentlig tilgjengelige datasett og lav dataanskaffelseskostnad. "
        "Kombiner eiendomskoordinater med flom, skred, havnivå og varmestress – og map resultatene til EU Taxonomy, SFDR og bankrapportering."
    ),
    pills=["Portfolio screening", "EU Taxonomy", "SFDR", "Bank API", "Public data first"],
    badge="Portfolio engine",
)

project_address = project.get("adresse", "")
project_municipality = project.get("kommune", "")

left, right = st.columns([1.2, 0.8], gap="large")
with left:
    render_section(
        "Eiendom eller portefølje",
        "Her starter dere med et enkelt men troverdig analyseoppsett. Senere kan samme motor brukes til maskinell screening av større bank- og eiendomsporteføljer via API.",
        "Risk setup",
    )

    c1, c2 = st.columns(2)
    with c1:
        analysis_mode = st.radio("Analysemodus", ["Enkeltobjekt", "Portefølje"], horizontal=True)
        address = st.text_input("Adresse / lokasjon", value=project_address)
        municipality = st.text_input("Kommune", value=project_municipality)
        asset_class = st.selectbox("Aktivaklasse", ["Bolig", "Kontor", "Logistikk", "Hotell", "Mixed-use"], index=1)
        horizon = st.selectbox("Tidshorisont", ["2030", "2050", "2100"], index=1)
    with c2:
        climate_scenario = st.selectbox("Klimascenario", ["RCP 4.5", "RCP 8.5"], index=0)
        portfolio_size = st.number_input("Antall eiendommer i portefølje", min_value=1, value=1 if analysis_mode == "Enkeltobjekt" else 240, step=1)
        loan_exposure_mnok = st.number_input("Eksponering / verdi (MNOK)", min_value=1.0, value=85.0 if analysis_mode == "Enkeltobjekt" else 4200.0, step=5.0)
        reporting_target = st.multiselect(
            "Rapporteringsmål",
            ["EU Taxonomy / DNSH", "SFDR artikkel 8/9", "ECB Climate Stress Test", "Finanstilsynet klimarisiko", "Intern investeringspolicy"],
            default=["EU Taxonomy / DNSH", "ECB Climate Stress Test"] if analysis_mode == "Portefølje" else ["EU Taxonomy / DNSH"],
        )

    st.markdown("### Geofaktorer og antatte forhold")
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        elevation_m = st.number_input("Høyde over havet (m)", min_value=0.0, value=14.0, step=1.0)
        distance_coast_km = st.number_input("Avstand til kyst (km)", min_value=0.0, value=1.8, step=0.1)
    with g2:
        distance_river_km = st.number_input("Avstand til bekk/elv (km)", min_value=0.0, value=0.45, step=0.05)
        flood_zone = st.toggle("I eller nær flomsone", value=True)
    with g3:
        slope_deg = st.number_input("Terrenghelning (grader)", min_value=0.0, value=12.0, step=1.0)
        landslide_zone = st.toggle("I eller nær skred-/rasfareområde", value=False)
    with g4:
        urban_heat = st.slider("Urban varmestress (0–10)", 0, 10, 6)
        soil_type = st.selectbox("Jordsmonn / grunnforhold", ["Morene", "Leire", "Berg", "Marine avsetninger", "Fyllmasser"], index=1)

    flood_base = 4.5 if flood_zone else 1.8
    flood_score = min(5.0, max(1.0, flood_base + max(0.0, 0.6 - distance_river_km) * 2 - elevation_m / 90))
    landslide_base = 4.2 if landslide_zone else 1.6
    soil_modifier = {"Leire": 0.7, "Marine avsetninger": 0.8, "Fyllmasser": 0.5, "Morene": 0.1, "Berg": -0.3}[soil_type]
    landslide_score = min(5.0, max(1.0, landslide_base + slope_deg / 18 + soil_modifier))
    sea_score = min(5.0, max(1.0, 4.2 - elevation_m / 22 - distance_coast_km / 1.8 + (0.35 if climate_scenario == "RCP 8.5" else 0.0)))
    heat_score = min(5.0, max(1.0, 1.4 + urban_heat / 2 + (0.35 if horizon == "2100" else 0.1 if horizon == "2050" else 0.0)))
    weighted_score = round(flood_score * 0.33 + landslide_score * 0.27 + sea_score * 0.20 + heat_score * 0.20, 2)
    uncertainty = round(max(0.35, 1.4 - len(reporting_target) * 0.12 - (0.2 if analysis_mode == "Portefølje" else 0.0)), 2)

    risk_df = pd.DataFrame(
        [
            {"Faktor": "Flom", "Score (1–5)": round(flood_score, 2), "Kommentar": "Basert på flomsone, elvenærhet, høyde og valgt scenario."},
            {"Faktor": "Skred / ras", "Score (1–5)": round(landslide_score, 2), "Kommentar": "Basert på skredstatus, helning og grunnforhold."},
            {"Faktor": "Havnivåstigning", "Score (1–5)": round(sea_score, 2), "Kommentar": "Basert på avstand til kyst, høyde og valgt klimascenario."},
            {"Faktor": "Varmestress / kjølebehov", "Score (1–5)": round(heat_score, 2), "Kommentar": "Basert på urban varmelast og horisont."},
        ]
    )

    taxonomy_df = pd.DataFrame(
        [
            {"Kravområde": "Climate change adaptation", "Status": "Må dokumenteres", "Kommentar": "Vis at klimarisiko er kartlagt og tilpasningstiltak vurdert."},
            {"Kravområde": "DNSH – flom/skred", "Status": "Foreløpig vurdering", "Kommentar": "Krever dokumentert aktsomhetsvurdering og eventuelle tiltak."},
            {"Kravområde": "SFDR datapunkter", "Status": "Kan genereres", "Kommentar": "Strukturerte felt kan eksporteres videre til fond-/porteføljerapportering."},
            {"Kravområde": "ECB / Finanstilsynet", "Status": "API-klart", "Kommentar": "Modulen kan levere porteføljeuttrekk med score per aktivum."},
        ]
    )

    if analysis_mode == "Enkeltobjekt":
        unit_price = 5500 if weighted_score < 3 else 7500
        pricing_text = f"Anbefalt pris pr. eiendom: ca. {unit_price:,} NOK".replace(',', ' ')
    else:
        unit_price = min(500000, max(150000, int(portfolio_size * 900)))
        pricing_text = f"Årlig API-/porteføljeabonnement: ca. {unit_price:,} NOK".replace(',', ' ')

    estimated_damage = round(loan_exposure_mnok * 1_000_000 * (weighted_score / 5) * 0.025)

    render_metric_cards(
        [
            {"label": "Klimarisikoscore", "value": f"{weighted_score}/5", "desc": "Aggregert score på tvers av flom, skred, havnivå og varmestress."},
            {"label": "Usikkerhetsintervall", "value": f"± {uncertainty}", "desc": "Foreløpig spenn basert på datadekning og modenhet."},
            {"label": "Estimert skadekost", "value": f"{estimated_damage:,} NOK".replace(',', ' '), "desc": "Illustrativ stresstest basert på eksponering og risikonivå."},
            {"label": "Forretningsmodell", "value": pricing_text, "desc": "Sterkest potensial i API- og porteføljeabonnement mot banker/forsikring."},
        ]
    )

    tabs = st.tabs(["Risikofaktorer", "Taxonomy / SFDR", "Portefølje", "API-felter"])
    with tabs[0]:
        st.dataframe(risk_df, use_container_width=True, hide_index=True)
        dataframe_download(risk_df, "Last ned risikofaktorer (.csv)", "climate_risk_factors.csv")
    with tabs[1]:
        st.dataframe(taxonomy_df, use_container_width=True, hide_index=True)
        dataframe_download(taxonomy_df, "Last ned taxonomy-mapping (.csv)", "climate_taxonomy_mapping.csv")
    with tabs[2]:
        portfolio_df = pd.DataFrame(
            [
                {"Segment": "Lav risiko", "Andel": "42%", "Tiltak": "Rutinemessig monitorering"},
                {"Segment": "Middels risiko", "Andel": "37%", "Tiltak": "Tiltaksplan og følsomhetsanalyse"},
                {"Segment": "Høy risiko", "Andel": "21%", "Tiltak": "Prioritert screening og tilpasningstiltak"},
            ]
        )
        st.dataframe(portfolio_df, use_container_width=True, hide_index=True)
        st.markdown(f"**Porteføljescope:** {portfolio_size} eiendommer &nbsp; | &nbsp; **Eksponering:** {loan_exposure_mnok:,.0f} MNOK".replace(',', ' '))
    with tabs[3]:
        api_payload = {
            "asset_id": "BN-TRD-001",
            "address": address,
            "municipality": municipality,
            "scores": {
                "flood": round(flood_score, 2),
                "landslide": round(landslide_score, 2),
                "sea_level": round(sea_score, 2),
                "heat_stress": round(heat_score, 2),
                "climate_risk_total": weighted_score,
            },
            "taxonomy_flags": {
                "adaptation_screened": True,
                "dnsh_review_needed": weighted_score >= 3.0,
            },
        }
        st.code(api_payload, language="json")

    json_download(
        {
            "module": "Climate Risk",
            "analysis_mode": analysis_mode,
            "address": address,
            "municipality": municipality,
            "asset_class": asset_class,
            "horizon": horizon,
            "climate_scenario": climate_scenario,
            "risk_score": weighted_score,
            "uncertainty": uncertainty,
            "pricing_hint_nok": unit_price,
        },
        "Eksporter klimarisiko-sammendrag (.json)",
        "builtly_climate_risk_summary.json",
    )

with right:
    render_section(
        "Hvorfor dette er en spesielt god softwaremodul",
        "Klimarisiko har mye offentlig tilgjengelig data og lav dataanskaffelseskostnad. Det gjør dette til en av modulene som lettest kan bli et skalerbart produkt – særlig mot banker og forsikringsselskap.",
        "Strategic fit",
    )
    render_project_snapshot(project, badge="Portfolio-ready")
    render_panel(
        "Datakilder som modulen bør orkestrere",
        "Poenget er ikke at Builtly må være kilden til alt, men at plattformen blir laget som samler, normaliserer og eksporterer strukturerte datapunkter og rapporter.",
        [
            "NVE flomsonekart og skredfarekart",
            "Kartverkets høydemodeller og terrengdata",
            "Klimaatlas / havnivå og klimascenarioer",
            "NGU / NIBIO for grunn- og jordsmonnforhold",
        ],
        tone=tone_from_score(weighted_score),
        badge="Public data first",
    )
    render_panel(
        "Kommersiell logikk",
        "Enkeltobjekt kan selges som rapport. Den store oppsiden ligger i porteføljeabonnement og API-integrasjon i bankens eller forsikringsselskapets egne systemer.",
        [
            "3 000–8 000 NOK per eiendom ved enkeltrapport",
            "50 000–200 000 NOK+ per år for porteføljescreening",
            "150 000–500 000 NOK+ for større API-kunder med mange aktiva",
            "Sterkt MRR-potensial fordi kunden trenger løpende screening, ikke bare engangsrapport",
        ],
        tone="gold",
        badge="Pricing potential",
    )
    st.metric("Betalerklasse", "Bank / forsikring / større eiendom", "Ny kundetype utover klassiske prosjektaktører")
    st.metric("Internasjonal skalerbarhet", "Høy", "Kan lokaliseres via datasett og API-lag")

render_section(
    "Hva som bør bygges i MVP",
    "Fokuser på risikoscore, tydelig kildegrunnlag, strukturerte eksportfelt og portefølje-API før dere overbygger det med for mye fancy visualisering.",
    "MVP scope",
)

c1, c2 = st.columns(2, gap="large")
with c1:
    render_panel(
        "MVP",
        "Den første versjonen bør være et robust screeningsverktøy, ikke en altomfattende klimamotor.",
        [
            "Eiendom til score per risikofaktor",
            "Samlet klimarisikoscore med usikkerhet",
            "Taxonomy / DNSH mapping",
            "API-uttrekk for bank og portefølje",
        ],
        tone="green",
        badge="Build",
    )
with c2:
    render_panel(
        "Neste utviklingssteg",
        "Etter MVP kan modulen bli dypere og mer finmasket uten å endre den kommersielle logikken.",
        [
            "Batch-opplasting og automatisk porteføljesegmentering",
            "Scenario- og skadekostsimulering per horisont",
            "Webhooks og white-label-rapporter mot bank/partner",
            "Kobling til finansiell rapportering og policy-motor",
        ],
        tone="blue",
        badge="Expand",
    )
