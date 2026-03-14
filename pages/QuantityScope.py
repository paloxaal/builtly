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
    sample_revision_trace,
)

project = configure_page("Builtly | Mengde & Scope Intelligence", "📏")

render_hero(
    eyebrow="Quantity & Scope Intelligence",
    title="Én motor for mengder, arealer, revisjonsdelta og sporbarhet.",
    subtitle=(
        "Bygg den horisontale kjernen som kan brukes i RIB, anbudskontroll, BREEAM, MOP og mulighetsstudier. "
        "Modulen sammenstiller modell, tegning og beskrivelse og viser hva som faktisk endret seg mellom revisjoner."
    ),
    pills=["IFC", "PDF", "BOQ", "Revisjonsdelta", "Traceability"],
    badge="Core engine",
)

base_bta = float(project.get("bta", 2500) or 2500)
base_floors = int(project.get("etasjer", 4) or 4)

left, right = st.columns([1.25, 0.75], gap="large")
with left:
    render_section(
        "Kontrolloppsett",
        "Definer hvilke kilder som inngår, hvilke revisjoner som skal sammenlignes, og hvor detaljert Builtly skal spore mengder og arealer.",
        "Data intake",
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        source_mode = st.multiselect(
            "Kilder",
            ["IFC-modell", "PDF-tegninger", "Beskrivelse/NS3420", "BOQ / mengdeliste", "Romskjema"],
            default=["IFC-modell", "PDF-tegninger", "Beskrivelse/NS3420"],
        )
        compare_revisions = st.toggle("Sammenlign revisjon A mot B", value=True)
    with c2:
        gross_area = st.number_input("Bruttoareal (m²)", min_value=100.0, value=base_bta, step=50.0)
        floors = st.number_input("Etasjer", min_value=1, value=base_floors, step=1)
    with c3:
        detail_level = st.select_slider(
            "Detajlnivå",
            options=["Konsept", "Skisse", "Forprosjekt", "Detaljprosjekt", "Utførelse"],
            value="Forprosjekt",
        )
        units = st.selectbox("Enheter", ["m² / stk / lm", "NS3451 struktur", "CCI / IFC-objekter"], index=0)

    st.file_uploader(
        "Last opp IFC, PDF, BOQ eller romskjema",
        type=["ifc", "pdf", "xlsx", "xls", "csv", "docx"],
        accept_multiple_files=True,
        key="quantity_scope_files",
    )

    st.markdown("### Arealfordeling")
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        net_internal = st.number_input("Nettoareal (m²)", min_value=0.0, value=round(gross_area * 0.82, 1), step=10.0)
    with a2:
        core_area = st.number_input("Kjerne (m²)", min_value=0.0, value=round(gross_area * 0.09, 1), step=5.0)
    with a3:
        technical_area = st.number_input("Tekniske rom (m²)", min_value=0.0, value=round(gross_area * 0.07, 1), step=5.0)
    with a4:
        circulation_area = st.number_input("Kommunikasjon (m²)", min_value=0.0, value=round(gross_area * 0.11, 1), step=5.0)

    revision_delta_pct = st.slider("Forventet revisjonsendring mot neste sett (%)", 0, 20, 6)

    saleable_area = max(net_internal - technical_area * 0.35, 0)
    trace_links = max(68, 94 - len(source_mode) * 3)
    coverage = max(70, 97 - abs(gross_area - net_internal - core_area - technical_area - circulation_area) / max(gross_area, 1) * 100)

    quantity_rows = pd.DataFrame(
        [
            {"Post": "Betong dekker", "Mengde": round(gross_area * 0.32, 1), "Enhet": "m³", "Kilde": "IFC / RIB", "Sporbarhet": "Modell-ID + sone"},
            {"Post": "Bæresystem stål", "Mengde": round(gross_area * 0.038, 1), "Enhet": "tonn", "Kilde": "IFC / RIB", "Sporbarhet": "Objektgruppe"},
            {"Post": "Fasade", "Mengde": round(gross_area * 0.58, 1), "Enhet": "m²", "Kilde": "ARK PDF", "Sporbarhet": "Tegning + akse"},
            {"Post": "Innervegger", "Mengde": round(gross_area * 1.25, 1), "Enhet": "lm", "Kilde": "ARK / beskrivelse", "Sporbarhet": "Romskjema + plan"},
            {"Post": "Dører", "Mengde": max(12, int(gross_area / 48)), "Enhet": "stk", "Kilde": "Dørskjema", "Sporbarhet": "Type + plan"},
            {"Post": "Tekniske sjakter", "Mengde": round(floors * 2.4, 1), "Enhet": "stk", "Kilde": "RIV/RIE", "Sporbarhet": "Kjerne-aksing"},
        ]
    )
    delta_rows = quantity_rows.copy()
    delta_rows["Delta mot rev. B"] = delta_rows["Mengde"].apply(lambda x: round(x * revision_delta_pct / 100, 1))
    delta_rows["Kommentar"] = [
        "Økt dekkeutstrekning i plan B",
        "Stivere spenn / justert bæresystem",
        "Fasade forskjøvet ved hjørnesone",
        "Nye romskiller i plan 2",
        "Dørtyper revidert i plan 1",
        "Ekstra sjakt for teknikk",
    ]

    area_rows = pd.DataFrame(
        [
            {"Kategori": "Bruttoareal", "Areal (m²)": round(gross_area, 1), "Andel": "100%"},
            {"Kategori": "Nettoareal", "Areal (m²)": round(net_internal, 1), "Andel": f"{(net_internal / gross_area) * 100:.1f}%"},
            {"Kategori": "Kjerne", "Areal (m²)": round(core_area, 1), "Andel": f"{(core_area / gross_area) * 100:.1f}%"},
            {"Kategori": "Tekniske rom", "Areal (m²)": round(technical_area, 1), "Andel": f"{(technical_area / gross_area) * 100:.1f}%"},
            {"Kategori": "Kommunikasjon", "Areal (m²)": round(circulation_area, 1), "Andel": f"{(circulation_area / gross_area) * 100:.1f}%"},
            {"Kategori": "Potensielt salgbart/utleibart", "Areal (m²)": round(saleable_area, 1), "Andel": f"{(saleable_area / gross_area) * 100:.1f}%"},
        ]
    )

    render_metric_cards(
        [
            {"label": "Mengdeposter", "value": f"{len(quantity_rows)}", "desc": "Grunnleggende scope og måleposter koblet til kilder og objekter."},
            {"label": "Trace links", "value": f"{trace_links}%", "desc": "Andel poster som kan spores tilbake til modell, tegning eller beskrivelse."},
            {"label": "Arealutnyttelse", "value": f"{(saleable_area / gross_area) * 100:.1f}%", "desc": "Foreløpig andel salgbart / utleibart areal av brutto."},
            {"label": "Dekningsgrad", "value": f"{coverage:.0f}%", "desc": "Hvor komplett grunnlaget er for sikker mengde- og scopeanalyse."},
        ]
    )

    tabs = st.tabs(["Mengder", "Arealer", "Revisjonsdelta", "Sporbarhet"])
    with tabs[0]:
        st.dataframe(quantity_rows, use_container_width=True, hide_index=True)
        dataframe_download(quantity_rows, "Last ned mengdeliste (.csv)", "builtly_quantity_scope.csv")
    with tabs[1]:
        st.dataframe(area_rows, use_container_width=True, hide_index=True)
        dataframe_download(area_rows, "Last ned arealoppsett (.csv)", "builtly_area_breakdown.csv")
    with tabs[2]:
        if compare_revisions:
            st.dataframe(delta_rows, use_container_width=True, hide_index=True)
            dataframe_download(delta_rows, "Last ned revisjonsdelta (.csv)", "builtly_revision_delta.csv")
        else:
            st.info("Slå på sammenligning mellom revisjoner for å generere delta-rapport.")
    with tabs[3]:
        st.dataframe(sample_revision_trace(), use_container_width=True, hide_index=True)
        st.markdown("- Hver mengdepost bør kunne spores til modell-ID, tegning eller beskrivelse.\n- Manuell overstyring og revisjonslogg må bevares for enterprise-bruk.")

    json_download(
        {
            "module": "Quantity & Scope Intelligence",
            "sources": source_mode,
            "detail_level": detail_level,
            "gross_area": gross_area,
            "net_internal": net_internal,
            "core_area": core_area,
            "technical_area": technical_area,
            "circulation_area": circulation_area,
            "revision_delta_pct": revision_delta_pct,
        },
        "Eksporter modulsammendrag (.json)",
        "builtly_quantity_scope_summary.json",
    )

with right:
    render_section(
        "Hvorfor dette er en kjernekapabilitet",
        "Mengde & Scope Intelligence er ikke bare en egen modul. Det er selve motoren som senere kan brukes i RIB, anbudskontroll, BREEAM, MOP og mulighetsstudier.",
        "Scalability",
    )
    render_project_snapshot(project)
    render_panel(
        "Det modulen gjør bedre enn enkel mengdeberegning",
        "Builtly kobler regelstyrt logikk og AI sammen med sporbarhet på tvers av modell, tegning og tekstgrunnlag.",
        [
            "Fanger revisjonsendringer og scope-avvik mellom ulike kilder",
            "Normaliserer arealer, enheter og objekter på tvers av dokumentsett",
            "Gir dokumentert traceability i stedet for bare et tallgrunnlag",
            "Kan brukes som intern motor i flere vertikale moduler",
        ],
        tone="blue",
        badge="Horizontal engine",
    )
    render_panel(
        "MVP-forslag",
        "Start med en robust versjon som gir beslutningsstøtte og dokumentert grunnlag. Ikke forsøk full AI-takeoff i første release.",
        [
            "Mengdeliste med kildehenvisning",
            "Arealfordeling og brutto/netto-oversikt",
            "Revisjonsdelta mellom to sett",
            "Varsel om scope-konflikter mellom modell, PDF og beskrivelse",
        ],
        tone="gold",
        badge="Build next",
    )
    st.metric("Egnet prismodell", "Abonnement + per prosjekt", "Kan brukes både direkte og som intern motor")
    st.metric("Potensiell verdi", "Høy", "Gir gjenbruk i flere moduler og land")

render_section(
    "Produktretning",
    "Det er smartere å la Builtly være laget som orkestrerer input, kontroll, rapport, QA og sporbarhet rundt eksisterende motorer når det er hensiktsmessig. Denne modulen er et godt eksempel på den strategien.",
    "Architecture",
)

c1, c2 = st.columns(2, gap="large")
with c1:
    render_panel(
        "Kundeverdi",
        "Først og fremst for utbyggere og entreprenører som trenger bedre beslutningsgrunnlag og kontroll. Deretter for rådgivere som vil komprimere QA og mengdearbeid.",
        [
            "Utbygger: raskere scenariovurdering og areal-/mengdekontroll",
            "Entreprenør: tryggere scope og bedre kontroll på revisjonsendringer",
            "Rådgiver: sporbar kvalitetssikring uten å gi opp faglig kontroll",
        ],
        tone="green",
        badge="Who buys",
    )
with c2:
    render_panel(
        "Videre utvidelser",
        "Når kjernen er stabil, kan den kobles direkte inn i andre moduler og enterprise-flyt.",
        [
            "Automatisk uttrekk fra IFC og geometri",
            "Dokument-sammenligning med OCR/multimodal parser ved behov",
            "Kobling mot kalkyle, anbudskontroll og yield-optimalisering",
            "API-lag for partnere og white-label-tenants",
        ],
        tone="blue",
        badge="Next layer",
    )
