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

project = configure_page("Builtly | Areal & Yield", "🏙️")

render_hero(
    eyebrow="Areal & Yield",
    title="Se hvor arealet går, hva som kan optimaliseres og hva det er verdt.",
    subtitle=(
        "Analyser fordelingen mellom brutto, netto, kjerne, teknikk og fellesareal. "
        "Sammenlign scenarioer for å finne realistiske grep som gir mer salgbart eller utleibart areal."
    ),
    pills=["Brutto/netto", "Yield", "Scenarioer", "Arealfordeling", "Verdipotensial"],
    badge="Areal & Yield",
)

base_bta = float(project.get("bta", 2500) or 2500)
use_case = project.get("b_type") or "Næring / Kontor"

left, right = st.columns([1.25, 0.75], gap="large")
with left:
    render_section(
        "Arealfordeling og scenarioer",
        "Legg inn dagens arealfordeling og se hva som skjer med ulike optimaliseringsgrep.",
        "Oppsett",
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
            {"label": "Yield baseline", "value": f"{efficiency_ratio * 100:.1f}%", "desc": "Andel salgbart/utleibart areal av bruttoarealet i dag."},
            {"label": "Serviceandel", "value": f"{service_ratio * 100:.1f}%", "desc": "Kjerne, teknikk og kommunikasjon samlet som andel av brutto."},
            {"label": "Beste scenario", "value": f"{best_row['Ekstra areal (m²)']} m²", "desc": "Estimert ekstra areal i det mest ambisiøse scenarioet."},
            {"label": "Verdipotensial", "value": f"{int(best_row['Potensiell verdi (NOK)']):,} NOK".replace(',', ' '), "desc": "Estimert verdiøkning basert på valgt kvadratmeterpris."},
        ]
    )

    tabs = st.tabs(["Arealfordeling", "Scenarioer", "Tiltak", "Merknader"])
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
            "- Scenarioene er beregnet ut fra arealfordelingen du har lagt inn og bør faglig vurderes før de brukes i beslutninger.\n"
            "- For mer presise resultater kan analysen kobles mot brann, akustikk, bæresystem og teknisk prosjektering.\n"
            "- Alle innstillinger og resultater kan eksporteres for deling med prosjektteamet."
        )

    json_download(
        {
            "module": "Areal & Yield",
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
        "Eksporter analysegrunnlag (.json)",
        "yield_optimizer_summary.json",
    )

with right:
    render_section(
        "Om analysen",
        "Arealanalysen viser hvor det er potensial for å øke verdien av prosjektet gjennom smartere arealfordeling.",
        "Info",
    )
    render_project_snapshot(project)
    render_panel(
        "Hva du får",
        "En oversikt over dagens arealfordeling med scenarioer for optimalisering og estimert verdipotensial.",
        [
            "Analyse av brutto/netto og fordeling mellom kjerne, teknikk og fellesareal",
            "Tre scenarioer med estimert ekstra areal og verdipotensial",
            "Konkrete grep for kjerne, tekniske rom, kommunikasjon og fellesareal",
            "Eksport av scenarioer og arealdata for videre bruk",
        ],
        tone="blue",
        badge="Arealanalyse",
    )
    render_panel(
        "Slik bruker du modulen",
        "Legg inn arealfordelingen fra prosjektet og juster parameterne for å se effekten.",
        [
            "Fyll inn brutto, netto, kjerne, teknikk og fellesareal",
            "Velg optimeringsmål og scenariomodus",
            "Se scenarioer med estimert areal og verdiøkning",
            "Last ned resultater som CSV eller JSON",
        ],
        tone="gold",
        badge="Kom i gang",
    )

render_section(
    "Tips for bedre resultat",
    "Jo mer presise arealdata du legger inn, desto mer pålitelige blir scenarioene.",
    "Tips",
)

c1, c2 = st.columns(2, gap="large")
with c1:
    render_panel(
        "Slik får du mest ut av analysen",
        "Presisjonen avhenger av hvor godt du kjenner arealfordelingen i prosjektet.",
        [
            "Bruk faktiske arealtall fra modell eller tegninger, ikke bare grovt estimat",
            "Juster markedsbarhetsfriksjonen etter hvor realistisk det er å selge/leie ut ekstra areal",
            "Sammenlign alle tre scenarioer for å finne riktig ambisjonsnivå",
        ],
        tone="green",
        badge="Anbefalt",
    )
with c2:
    render_panel(
        "Eksportmuligheter",
        "Alle resultater kan lastes ned for bruk i egne verktøy og presentasjoner.",
        [
            "Arealfordeling som CSV for import i regneark eller kalkyle",
            "Scenarioer med ekstra areal og verdipotensial",
            "Tiltaksliste med tillit og kommentarer",
            "Samlet analysegrunnlag som JSON",
        ],
        tone="blue",
        badge="Eksport",
    )
