from __future__ import annotations

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

project = configure_page("Builtly | Areal & Yield Optimizer", "🏙️")

render_hero(
    eyebrow="Areal & Yield Optimizer",
    title="Beslutningsmotor for mer salgbart, mer utleibart og mer effektivt areal.",
    subtitle=(
        "Start med analyse av brutto/netto, salgbart areal, utleibart areal, kjerneandel, tekniske rom og kommunikasjon. "
        "Først en trygg og sporbar beslutningsmotor – senere kan samme motor drive mer generative scenarier."
    ),
    pills=["Brutto/netto", "Yield", "Scenarioforslag", "Developer-first", "No black box"],
    badge="Decision engine",
)

base_bta = float(project.get("bta", 2500) or 2500)
use_case = project.get("b_type") or "Næring / Kontor"

left, right = st.columns([1.25, 0.75], gap="large")
with left:
    render_section(
        "Baseline og scenarioparametere",
        "Modulen er laget for å hjelpe utbyggere og beslutningstagere med å se hvor arealet lekker – og hva som realistisk kan optimaliseres uten å love magisk planløsnings-AI fra dag én.",
        "Yield setup",
    )

    a1, a2, a3 = st.columns(3)
    with a1:
        gross_area = st.number_input("Bruttoareal (m²)", min_value=300.0, value=base_bta, step=50.0)
        building_use = st.selectbox("Primær bruk", ["Bolig", "Kontor", "Mixed-use", "Hotell"], index=1 if "Kontor" in use_case else 0)
    with a2:
        value_per_sqm = st.number_input("Verdi per m² (NOK)", min_value=10000, value=45000, step=1000)
        optimization_target = st.select_slider("Primært mål", ["Mer salgbart areal", "Mer utleibart areal", "Mindre kjerne", "Mindre tekniske rom", "Bedre miks"], value="Mer utleibart areal")
    with a3:
        scenario_mode = st.selectbox("Scenario-modus", ["Konservativ", "Balansert", "Ambisiøs"], index=1)
        floors = st.number_input("Etasjer", min_value=1, value=int(project.get("etasjer", 4) or 4), step=1)

    st.markdown("### Dagens arealfordeling")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        net_area = st.number_input("Nettoareal (m²)", min_value=0.0, value=round(gross_area * 0.81, 1), step=10.0)
    with c2:
        core_area = st.number_input("Kjerne (m²)", min_value=0.0, value=round(gross_area * 0.10, 1), step=5.0)
    with c3:
        technical_area = st.number_input("Tekniske rom (m²)", min_value=0.0, value=round(gross_area * 0.08, 1), step=5.0)
    with c4:
        circulation_area = st.number_input("Kommunikasjon (m²)", min_value=0.0, value=round(gross_area * 0.12, 1), step=5.0)
    with c5:
        common_area = st.number_input("Felles / støtte (m²)", min_value=0.0, value=round(gross_area * 0.06, 1), step=5.0)

    marketability_penalty = st.slider("Markedsbarhetsfriksjon (0 = lav, 10 = høy)", 0, 10, 4)

    saleable_baseline = max(net_area - common_area * 0.4 - technical_area * 0.2, 0)
    lettable_baseline = max(net_area - common_area * 0.2, 0)
    efficiency_ratio = saleable_baseline / max(gross_area, 1)
    service_ratio = (core_area + technical_area + circulation_area) / max(gross_area, 1)

    scenario_factors = {
        "Konservativ": {"core": 0.02, "tech": 0.05, "circulation": 0.03, "common": 0.02},
        "Balansert": {"core": 0.04, "tech": 0.09, "circulation": 0.05, "common": 0.04},
        "Ambisiøs": {"core": 0.06, "tech": 0.14, "circulation": 0.08, "common": 0.05},
    }
    f = scenario_factors[scenario_mode]

    def scenario_row(name: str, multiplier: float) -> dict:
        core_new = max(core_area * (1 - f["core"] * multiplier), 0)
        tech_new = max(technical_area * (1 - f["tech"] * multiplier), 0)
        circulation_new = max(circulation_area * (1 - f["circulation"] * multiplier), 0)
        common_new = max(common_area * (1 - f["common"] * multiplier), 0)
        saleable = max(net_area + (core_area - core_new) + (technical_area - tech_new) + (circulation_area - circulation_new) * 0.45 + (common_area - common_new) * 0.35, 0)
        extra_saleable = max(saleable - saleable_baseline, 0)
        uplift = extra_saleable * value_per_sqm * max(0.82, 1 - marketability_penalty / 100)
        return {
            "Scenario": name,
            "Salgbart/utleibart areal (m²)": round(saleable, 1),
            "Ekstra areal (m²)": round(extra_saleable, 1),
            "Potensiell verdi (NOK)": round(uplift),
            "Kjerneandel": f"{(core_new / gross_area) * 100:.1f}%",
            "Teknisk andel": f"{(tech_new / gross_area) * 100:.1f}%",
        }

    scenario_df = pd.DataFrame(
        [
            scenario_row("Scenario A", 0.8),
            scenario_row("Scenario B", 1.0),
            scenario_row("Scenario C", 1.3),
        ]
    )
    best_row = scenario_df.iloc[scenario_df["Potensiell verdi (NOK)"].idxmax()]

    opportunities = pd.DataFrame(
        [
            {"Mulighet": "Kompakter kjerne", "Effekt": "Frigir mer effektivt plateareal", "Tillit": "Høy", "Kommentar": "Best egnet der vertikalkommunikasjon er overdimensjonert"},
            {"Mulighet": "Samle tekniske rom", "Effekt": "Mindre fragmentering av nettoareal", "Tillit": "Middels", "Kommentar": "Krever koordinering mot RIV/RIE og sjakter"},
            {"Mulighet": "Strammere kommunikasjon", "Effekt": "Bedre netto/brutto-forhold", "Tillit": "Middels", "Kommentar": "Må vurderes mot brann, universell utforming og drift"},
            {"Mulighet": "Omdisponer fellesareal", "Effekt": "Mer utleibart eller salgbart areal", "Tillit": "Lav til middels", "Kommentar": "Avhenger av marked, konsept og brukeropplevelse"},
        ]
    )

    render_metric_cards(
        [
            {"label": "Yield baseline", "value": f"{efficiency_ratio * 100:.1f}%", "desc": "Andel salgbart/utleibart areal av brutto i dagens løsning."},
            {"label": "Service ratio", "value": f"{service_ratio * 100:.1f}%", "desc": "Kjerne, teknikk og kommunikasjon samlet som andel av bruttoareal."},
            {"label": "Beste scenario", "value": f"{best_row['Ekstra areal (m²)']} m²", "desc": "Foreløpig identifisert ekstra areal i beste scenario."},
            {"label": "Potensiell verdi", "value": f"{int(best_row['Potensiell verdi (NOK)']):,} NOK".replace(',', ' '), "desc": "Illustrativ verdiøkning gitt valgt kvadratmeterverdi."},
        ]
    )

    tabs = st.tabs(["Baseline", "Scenarioer", "Tiltak", "Beslutningslogg"])
    baseline_df = pd.DataFrame(
        [
            {"Kategori": "Bruttoareal", "m²": round(gross_area, 1), "Andel": "100%"},
            {"Kategori": "Nettoareal", "m²": round(net_area, 1), "Andel": f"{(net_area / gross_area) * 100:.1f}%"},
            {"Kategori": "Kjerne", "m²": round(core_area, 1), "Andel": f"{(core_area / gross_area) * 100:.1f}%"},
            {"Kategori": "Teknikk", "m²": round(technical_area, 1), "Andel": f"{(technical_area / gross_area) * 100:.1f}%"},
            {"Kategori": "Kommunikasjon", "m²": round(circulation_area, 1), "Andel": f"{(circulation_area / gross_area) * 100:.1f}%"},
            {"Kategori": "Felles/støtte", "m²": round(common_area, 1), "Andel": f"{(common_area / gross_area) * 100:.1f}%"},
            {"Kategori": "Salgbart / utleibart baseline", "m²": round(saleable_baseline, 1), "Andel": f"{(saleable_baseline / gross_area) * 100:.1f}%"},
        ]
    )
    with tabs[0]:
        st.dataframe(baseline_df, use_container_width=True, hide_index=True)
        dataframe_download(baseline_df, "Last ned baseline (.csv)", "yield_baseline.csv")
    with tabs[1]:
        st.dataframe(scenario_df, use_container_width=True, hide_index=True)
        dataframe_download(scenario_df, "Last ned scenarioer (.csv)", "yield_scenarios.csv")
    with tabs[2]:
        st.dataframe(opportunities, use_container_width=True, hide_index=True)
        dataframe_download(opportunities, "Last ned tiltaksliste (.csv)", "yield_opportunities.csv")
    with tabs[3]:
        st.markdown(
            "- Legg inn manuell faglig vurdering før scenarier brukes eksternt.\n"
            "- Koble etter hvert til brann, akustikk, bærende struktur og teknikk for å justere realisme.\n"
            "- Dette er en beslutningsmotor, ikke automatisk planløsning fra første dag."
        )

    json_download(
        {
            "module": "Areal & Yield Optimizer",
            "building_use": building_use,
            "optimization_target": optimization_target,
            "scenario_mode": scenario_mode,
            "gross_area": gross_area,
            "net_area": net_area,
            "core_area": core_area,
            "technical_area": technical_area,
            "circulation_area": circulation_area,
            "common_area": common_area,
        },
        "Eksporter scenariooppsett (.json)",
        "yield_optimizer_summary.json",
    )

with right:
    render_section(
        "Utbyggernært og høyverdig",
        "Dette er modulen som kan gjøre Builtly attraktiv tidlig i utviklingsløpet: ikke som AI som tegner alt for deg, men som verktøyet som viser hvor verdien faktisk ligger.",
        "Market fit",
    )
    render_project_snapshot(project)
    render_panel(
        "Hva modulen bør love i fase 1",
        "Bygg tillit med en robust beslutningsmotor. Unngå å overselge full automatisert designoptimalisering før datagrunnlag og regelmotor er modne nok.",
        [
            "Analyse av brutto/netto og arealfordeling",
            "Yield- og verdipotensial per scenario",
            "Forslag til grep for kjerne, teknikk, kommunikasjon og fellesareal",
            "Eksport av scenarioer til prosjektteam og investeringsbeslutning",
        ],
        tone="blue",
        badge="Phase 1 promise",
    )
    render_panel(
        "Hvordan dette kan kobles videre",
        "Når motoren er pålitelig kan den kobles på vertikale fagmoduler og generativ design senere.",
        [
            "Koble mot Mengde & Scope for bedre areal- og objektdisiplin",
            "Bruk brann, akustikk og RIB som begrensninger i senere scenarioer",
            "La Tender Control bruke yield-data i kommersielle vurderinger",
            "Gi enterprise-kunder porteføljevis yield-screening over flere prosjekter",
        ],
        tone="gold",
        badge="Next layer",
    )
    st.metric("Primær kjøper", "Utbygger / developer", "Høy verdi i tidligfase og investeringsbeslutninger")
    st.metric("Strategisk rolle", "Decision engine", "Ikke bare rapport – men beslutningsgrunnlag")

render_section(
    "Hvorfor dette passer Builtly",
    "Builtly skal ikke være et konsulentselskap som skalerer med mennesker i hvert marked. Denne modulen er et godt eksempel på software som kan selges på abonnement på tvers av landegrenser, fordi effekten handler om areal, beslutning og økonomi – ikke bare lokal fagrådgivning.",
    "Strategy",
)

c1, c2 = st.columns(2, gap="large")
with c1:
    render_panel(
        "Kundeverdi i ett bilde",
        "Målet er at brukeren skal forstå tre ting raskt: hvor arealet lekker, hvilke grep som er realistiske, og hva det kan være verdt.",
        [
            "Mer salgbart eller utleibart areal",
            "Mindre tekniske og ikke-verdiskapende soner",
            "Mer konsistent tidligfasebeslutning på tvers av team",
        ],
        tone="green",
        badge="Value",
    )
with c2:
    render_panel(
        "Neste utviklingssteg",
        "Etter MVP kan modulen modnes videre uten å miste tillit i markedet.",
        [
            "Regelsett for typologier og arealeffektivitet",
            "Scenarioer per plan og etasje, ikke bare aggregert prosjekt",
            "Kobling til klimagass, teknikk og kostnad",
            "Senere generativ planløsningsstøtte med menneskelig overstyring",
        ],
        tone="blue",
        badge="Roadmap",
    )
