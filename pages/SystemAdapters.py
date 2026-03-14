from __future__ import annotations

import io
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
    render_hero,
    render_json_preview,
    render_metric_cards,
    render_panel,
    render_section,
)
from builtly_public_data import (
    adapter_status,
    gather_climate_snapshot,
    gather_tdd_public_snapshot,
    geocode_address,
    run_climate_portfolio_batch,
    run_tdd_portfolio_batch,
)
from builtly_api_surface import build_openapi_preview

project = configure_page("Builtly | System adapters", "🛰️")

render_hero(
    eyebrow="Internal setup",
    title="Test offentlige adapters, proxyer og batch-endepunkter.",
    subtitle=(
        "Bruk denne siden internt for aa verifisere at adresseoppslag, planoppslag, Matrikkel-proxy, energidata og batchflyter er koblet korrekt. "
        "Siden er ment for oppsett, feilsoking og demo av de nye integrasjonene."
    ),
    pills=["Kartverket", "NVE", "DiBK plan", "Matrikkel proxy", "Energy proxy", "Batch APIs"],
    badge="Internal",
)

status_rows = adapter_status()
configured = sum(1 for row in status_rows if row.get("configured"))
render_metric_cards(
    [
        {"label": "Koblede kilder", "value": f"{configured}/{len(status_rows)}", "desc": "Miljostyrte adapters og proxyer funnet via env-vars."},
        {"label": "NVE / GIS", "value": "Klar" if any("NVE" in row.get("source", "") and row.get("configured") for row in status_rows) else "Trenger oppsett", "desc": "Brukes for klimarisiko og faresoner."},
        {"label": "TDD offentlige data", "value": "Klar" if any("Matrikkel" in row.get("source", "") and row.get("configured") for row in status_rows) else "Delvis", "desc": "Matrikkel, plan og energidata for TDD."},
        {"label": "API surface", "value": "Preview ready", "desc": "OpenAPI-lignende overflate for interne tester og partnerarbeid."},
    ]
)

render_section(
    "Adapterstatus",
    "Oversikt over hvilke offentlige og private endepunkter som er konfigurert i miljoet akkurat naa.",
    "Status",
)
status_df = pd.DataFrame(status_rows)
st.dataframe(status_df, use_container_width=True, hide_index=True)
dataframe_download(status_df, "Last ned adapterstatus (.csv)", "builtly_adapter_status.csv")

left, right = st.columns([1.15, 0.85], gap="large")
with left:
    render_section(
        "Enkeltoppslag",
        "Test geokoding, klimarisiko og TDD-data med en adresse eller et matrikkeloppslag.",
        "Live checks",
    )
    address = st.text_input("Adresse", value=project.get("adresse") or "Kjopmannsgata 34, Trondheim")
    municipality = st.text_input("Kommune", value=project.get("kommune") or "Trondheim")
    gnr = st.text_input("Gnr", value=str(project.get("gnr") or "") )
    bnr = st.text_input("Bnr", value=str(project.get("bnr") or "") )
    scenario = st.selectbox("Klimascenario", ["RCP 4.5", "RCP 8.5"], index=0)
    horizon = st.selectbox("Horisont", ["2050", "2100"], index=0)

    c1, c2, c3 = st.columns(3)
    with c1:
        run_geo = st.button("Test geokoding", use_container_width=True)
    with c2:
        run_climate = st.button("Test klimarisiko", use_container_width=True)
    with c3:
        run_tdd = st.button("Test TDD-oppslag", use_container_width=True)

    if run_geo:
        geo = geocode_address(address, municipality=municipality)
        render_json_preview(geo, "Geokoding")
        json_download(geo, "Eksporter geokoding (.json)", "builtly_geocoding.json")

    if run_climate:
        climate = gather_climate_snapshot(
            {
                "asset_id": f"{gnr}-{bnr}" if gnr and bnr else address,
                "address": address,
                "municipality": municipality,
                "scenario": scenario,
                "horizon": horizon,
            },
            scenario=scenario,
            horizon=horizon,
        )
        render_json_preview(climate, "Klimarisiko - enkeltanalyse")
        json_download(climate, "Eksporter klimarisiko (.json)", "builtly_climate_single.json")

    if run_tdd:
        snap = gather_tdd_public_snapshot(
            {
                "address": address,
                "municipality": municipality,
                "gnr": gnr,
                "bnr": bnr,
                "matrikkel_id": f"{gnr}/{bnr}" if gnr and bnr else "",
            }
        )
        render_json_preview(snap, "TDD offentlig datasnapshot")
        json_download(snap, "Eksporter TDD-snapshot (.json)", "builtly_tdd_snapshot.json")

    render_section(
        "Batchtester",
        "Simuler bank- og portefoljelop for TDD og klimarisiko basert paa en enkel CSV/XLSX med adresse, kommune, gnr, bnr eller koordinater.",
        "Portfolio",
    )
    upload = st.file_uploader("Last opp portefoljefil", type=["csv", "xlsx"])
    batch_type = st.radio("Batchtype", ["Klimarisiko", "TDD"], horizontal=True)
    partner_id = st.text_input("Partner / kunde-ID", value="pilot-bank-001")
    if upload is not None:
        if upload.name.lower().endswith(".csv"):
            batch_df = pd.read_csv(upload)
        else:
            batch_df = pd.read_excel(upload)
        st.dataframe(batch_df.head(25), use_container_width=True, hide_index=True)
        props = batch_df.fillna("").to_dict(orient="records")
        if st.button("Kjor batchtest", use_container_width=True):
            if batch_type == "Klimarisiko":
                result = run_climate_portfolio_batch(props, partner_id=partner_id, scenario=scenario, horizon=horizon)
            else:
                result = run_tdd_portfolio_batch(props, partner_id=partner_id)
            render_json_preview(result, f"{batch_type} - batchresultat")
            json_download(result, f"Eksporter {batch_type.lower()} batch (.json)", f"builtly_{batch_type.lower()}_batch.json")

with right:
    render_panel(
        "Hva denne siden er for",
        "Denne siden er et internt oppsettspunkt. Den skal hjelpe dere aa koble private nøkler og proxyer uten at dette ma frontes i den offentlige opplevelsen.",
        [
            "Verifiser DiBK plan-endepunkt og app key",
            "Test Matrikkel-proxy uten aa eksponere private nøkler i Streamlit-koden",
            "Bekreft energidata- eller EPC-proxy for TDD",
            "Kjor batchtester for bank- og portefoljekunder",
            "Kontroller hvilke kilder som mangler før demo eller produksjon",
        ],
        tone="blue",
        badge="Internal only",
    )
    render_panel(
        "Anbefalt oppsett",
        "Hold API-nøkler og partnerspesifikke URL-er i miljovariabler. Da kan samme kodebase brukes i demo, staging og produksjon.",
        [
            "BUILTLY_PLAN_API_URL og BUILTLY_PLAN_API_KEY",
            "BUILTLY_MATRIKKEL_PROXY_URL og token",
            "BUILTLY_ENERGY_PROXY_URL og token",
            "BUILTLY_NVE_FLOOD_SERVICE_URL og BUILTLY_NVE_LANDSLIDE_SERVICE_URL",
            "Filer med pre-indekserte snapshots for klima ved behov",
        ],
        tone="gold",
        badge="Ops",
    )
    render_json_preview(build_openapi_preview(), "API surface preview")

st.divider()
render_section(
    "Eksempel paa portefoljefil",
    "Bruk kolonner som adresse, kommune, gnr, bnr, matrikkel_id, lat, lon eller label. Batchmotoren er tolerant for manglende felt og markerer dem i output i stedet for aa stoppe hele jobben.",
    "Template",
)
example_df = pd.DataFrame(
    [
        {"label": "Eiendom A", "address": "Kjopmannsgata 34", "municipality": "Trondheim", "gnr": "410", "bnr": "22", "lat": 63.4305, "lon": 10.3951},
        {"label": "Eiendom B", "address": "Dronning Eufemias gate 16", "municipality": "Oslo", "gnr": "230", "bnr": "15", "lat": 59.9074, "lon": 10.7610},
    ]
)
st.dataframe(example_df, use_container_width=True, hide_index=True)
dataframe_download(example_df, "Last ned eksempel (.csv)", "builtly_portfolio_example.csv")
