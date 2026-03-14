from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from builtly_ai_fallback import generate_json_with_fallback
from builtly_module_kit import (
    configure_page,
    dataframe_download,
    json_download,
    render_attempt_log,
    render_hero,
    render_json_preview,
    render_metric_cards,
    render_panel,
    render_project_snapshot,
    render_section,
    tone_from_score,
)
from builtly_module_prompts import module_schema, module_system_prompt
from builtly_public_data import adapter_status, gather_climate_snapshot, run_climate_portfolio_batch


def _load_portfolio(upload) -> list[dict]:
    if upload is None:
        return []
    name = getattr(upload, "name", "").lower()
    data = upload.getvalue()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(data))
        else:
            df = pd.read_excel(io.BytesIO(data))
    except Exception:
        return []
    df = df.fillna("")
    rows = []
    for _, row in df.head(5000).iterrows():
        rows.append({
            "id": row.get("id") or row.get("asset_id") or row.get("matrikkel_id") or row.get("address") or "asset",
            "asset_id": row.get("asset_id") or row.get("id") or "",
            "address": row.get("address") or row.get("adresse") or "",
            "municipality": row.get("municipality") or row.get("kommune") or "",
            "lat": row.get("lat") or row.get("latitude") or None,
            "lon": row.get("lon") or row.get("longitude") or None,
        })
    return rows


project = configure_page("Builtly | Klimarisiko", "B")

render_hero(
    eyebrow="Climate Risk",
    title="Portefoljevennlig klimarisiko som banker, forsikring og eiendom faktisk kan abonnere pa.",
    subtitle=(
        "Klimarisikomodulen er bygget for offentlige datasett og lave dataanskaffelseskostnader. "
        "Kombiner koordinater med flom, skred, havniva og varmestress, og map resultatene til Taxonomy, SFDR og bankrapportering."
    ),
    pills=["Portfolio screening", "EU Taxonomy", "SFDR", "Bank API", "Public data first"],
    badge="Portfolio engine",
)

project_address = project.get("adresse", "")
project_municipality = project.get("kommune", "")

left, right = st.columns([1.2, 0.8], gap="large")
with left:
    render_section("Eiendom eller portefolje", "Start med enkeltobjekt og skal er videre til batch og webhook-logikk.", "Risk setup")
    c1, c2 = st.columns(2)
    with c1:
        analysis_mode = st.radio("Analysemodus", ["Enkeltobjekt", "Portefolje"], horizontal=True)
        address = st.text_input("Adresse / lokasjon", value=project_address)
        municipality = st.text_input("Kommune", value=project_municipality)
        asset_class = st.selectbox("Aktivaklasse", ["Bolig", "Kontor", "Logistikk", "Hotell", "Mixed-use"], index=1)
        horizon = st.selectbox("Tidshorisont", ["2030", "2050", "2100"], index=1)
    with c2:
        climate_scenario = st.selectbox("Klimascenario", ["RCP 4.5", "RCP 8.5"], index=0)
        portfolio_size = st.number_input("Antall eiendommer i portefolje", min_value=1, value=1 if analysis_mode == "Enkeltobjekt" else 240, step=1)
        reporting_target = st.multiselect(
            "Rapporteringsmal",
            ["EU Taxonomy / DNSH", "SFDR artikkel 8/9", "ECB Climate Stress Test", "Finanstilsynet klimarisiko", "Intern investeringspolicy"],
            default=["EU Taxonomy / DNSH", "ECB Climate Stress Test"] if analysis_mode == "Portefolje" else ["EU Taxonomy / DNSH"],
        )

    st.markdown("### Geofaktorer og vektsetting")
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        elevation_m = st.number_input("Hoyde over havet (m)", min_value=0.0, value=14.0, step=1.0)
        distance_coast_km = st.number_input("Avstand til kyst (km)", min_value=0.0, value=1.8, step=0.1)
    with g2:
        distance_river_km = st.number_input("Avstand til bekk/elv (km)", min_value=0.0, value=0.45, step=0.05)
        slope_deg = st.number_input("Terrenghelning (grader)", min_value=0.0, value=12.0, step=1.0)
    with g3:
        urban_heat = st.slider("Urban varmestress (0-10)", 0, 10, 6)
        soil_type = st.selectbox("Jordsmonn / grunnforhold", ["Morene", "Leire", "Berg", "Marine avsetninger", "Fyllmasser"], index=1)
    with g4:
        weight_flood = st.slider("Vekt flom", 0.0, 1.0, 0.35, 0.05)
        weight_landslide = st.slider("Vekt skred", 0.0, 1.0, 0.25, 0.05)
        weight_sea = st.slider("Vekt havniva", 0.0, 1.0, 0.25, 0.05)
        weight_heat = st.slider("Vekt varme", 0.0, 1.0, 0.15, 0.05)

    portfolio_upload = None
    if analysis_mode == "Portefolje":
        portfolio_upload = st.file_uploader("Last opp portefolje (CSV/XLSX med address, municipality, lat/lon eller id)", type=["csv", "xlsx", "xls"], key="climate_portfolio_upload")

weights = {"flood": weight_flood, "landslide": weight_landslide, "sea_level": weight_sea, "heat_stress": weight_heat}
asset = {
    "asset_id": project.get("p_name") or "asset-1",
    "address": address,
    "municipality": municipality,
    "asset_class": asset_class,
    "elevation_m": elevation_m,
    "distance_coast_km": distance_coast_km,
    "distance_river_km": distance_river_km,
    "slope_deg": slope_deg,
    "heat_index": float(urban_heat),
    "soil_type": soil_type,
}
snapshot = gather_climate_snapshot(asset, scenario=climate_scenario, horizon=horizon, weights=weights)
portfolio_rows = _load_portfolio(portfolio_upload)
if analysis_mode == "Portefolje" and not portfolio_rows:
    portfolio_rows = [{"id": f"asset-{i+1}", "address": address, "municipality": municipality} for i in range(int(portfolio_size))]
portfolio_batch = run_climate_portfolio_batch(portfolio_rows, partner_id="demo-bank", scenario=climate_scenario, horizon=horizon, weights=weights) if analysis_mode == "Portefolje" else None

ai_context = {
    "delivery_level": "auto",
    "snapshot": snapshot,
    "reporting_target": reporting_target,
    "analysis_mode": analysis_mode,
    "portfolio_batch": portfolio_batch,
}
ai_result = generate_json_with_fallback(
    system_prompt=module_system_prompt("climate", "auto"),
    user_prompt=json.dumps(ai_context, ensure_ascii=False, indent=2),
    schema_hint=module_schema("climate"),
    task="document_engine",
    preferred_providers=["openai", "anthropic", "gemini"],
    estimated_context_chars=len(json.dumps(ai_context, ensure_ascii=False)),
    max_output_tokens=1400,
    temperature=0.1,
)

with right:
    render_project_snapshot(project, badge="Portfolio-ready")
    render_metric_cards([
        {"label": "Klimarisikoscore", "value": f"{snapshot.get('aggregate_score', 0):.2f}", "desc": "Vektet score 1-5 pa tvers av flom, skred, havniva og varme."},
        {"label": "Usikkerhet", "value": f"+/- {snapshot.get('uncertainty_interval', 0):.2f}", "desc": "Konfidensintervall basert pa datakvalitet og faresonetreff."},
        {"label": "Scenario", "value": climate_scenario, "desc": f"Horisont {horizon}."},
        {"label": "Portefolje", "value": str(len(portfolio_rows) if analysis_mode == 'Portefolje' else 1), "desc": "Antall objekter i batch-preview."},
    ])
    render_json_preview({"weights": weights, "targets": reporting_target, "batch_id": (portfolio_batch or {}).get("batch_id", "")}, "Bestillingspayload")

render_section("Resultat og eksport", "Builtly holder datagrunnlag, regulatorisk mapping og AI-oppsummering atskilt.", "Output")

tabs = st.tabs(["Risikofaktorer", "Datakilder", "Regulatorisk mapping", "Portefolje-API", "AI-utkast"])
with tabs[0]:
    factors = pd.DataFrame([
        {"factor": "flood", "score": snapshot.get("flood_score"), "confidence": max(0.0, 1.0 - snapshot.get("uncertainty_interval", 0.5) / 2.0), "source": "NVE flom + asset profile", "note": "Flomrisiko"},
        {"factor": "landslide", "score": snapshot.get("landslide_score"), "confidence": max(0.0, 1.0 - snapshot.get("uncertainty_interval", 0.5) / 2.0), "source": "NVE skred + terreng", "note": "Skred- og rasfare"},
        {"factor": "sea_level", "score": snapshot.get("sea_level_score"), "confidence": max(0.0, 1.0 - snapshot.get("uncertainty_interval", 0.5) / 2.0), "source": "Hoyde + avstand til kyst", "note": "Stormflo og havniva"},
        {"factor": "heat_stress", "score": snapshot.get("heat_stress_score"), "confidence": max(0.0, 1.0 - snapshot.get("uncertainty_interval", 0.5) / 2.0), "source": "Urban heat proxy", "note": "Varmestress"},
    ])
    st.dataframe(factors, use_container_width=True, hide_index=True)
    dataframe_download(factors, "Last ned faktorscore (.csv)", "climate_factors.csv")
    if snapshot.get("map_url"):
        st.markdown(f"Kartlenke: {snapshot['map_url']}")

with tabs[1]:
    st.dataframe(pd.DataFrame(snapshot.get("source_rows", [])), use_container_width=True, hide_index=True)
    st.markdown("### Adapterstatus")
    st.dataframe(pd.DataFrame(adapter_status()), use_container_width=True, hide_index=True)

with tabs[2]:
    reg_df = pd.DataFrame(snapshot.get("regulatory_outputs", []))
    st.dataframe(reg_df, use_container_width=True, hide_index=True)
    json_download({"result": snapshot, "regulatory_outputs": snapshot.get("regulatory_outputs", [])}, "Last ned regulatorisk mapping (.json)", "climate_mapping.json")

with tabs[3]:
    if portfolio_batch:
        st.metric("Batch-ID", portfolio_batch.get("batch_id", "-"))
        st.metric("Estimert ferdig", f"{portfolio_batch.get('estimated_completion_hours', 0)} timer")
        st.dataframe(pd.DataFrame(portfolio_batch.get("properties", [])[:200]), use_container_width=True, hide_index=True)
        json_download(portfolio_batch, "Last ned batch-preview (.json)", "climate_portfolio_batch.json")
    else:
        st.info("Bytt til portefolje for a se batch-preview og webhook-metadata.")

with tabs[4]:
    if ai_result.get("ok") and ai_result.get("data"):
        st.write((ai_result.get("data") or {}).get("executive_summary") or "Ingen AI-oppsummering tilgjengelig.")
        st.dataframe(pd.DataFrame((ai_result.get("data") or {}).get("regulatory_outputs", [])), use_container_width=True, hide_index=True)
    else:
        st.info("AI-oppsummering er ikke tilgjengelig akkurat na.")
    render_attempt_log(ai_result.get("attempt_log", []))

render_panel(
    "Pris- og produktlogikk",
    "Klimarisiko er et Niva 1-dataprodukt som kan prises per eiendom eller som batch/API-abonnement mot bank og forsikring.",
    [
        "Enkeltobjekt: rapport per eiendom med PDF, JSON og maskinlesbar mapping.",
        "Portefolje: batch-API med webhook, batch-ID og eksport mot bankens egne systemer.",
        "Vekter kan styres per partner eller tenant for ulike risikoprofiler.",
    ],
    tone=tone_from_score(float(snapshot.get("aggregate_score", 0.0))),
    badge="Dataprodukt",
)
