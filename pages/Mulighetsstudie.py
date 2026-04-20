from __future__ import annotations

import json
from typing import Optional

import streamlit as st
from shapely.geometry import Polygon
from shapely import wkt as shapely_wkt

from builtly.masterplan_integration import run_concept_options
from builtly.plan_regler_presets import GENERISK_TEK17_NORGE, TRONDHEIM_KPA_2022_SONE_2
from builtly.svg_diagrams import render_mua_svg, render_quartalstruktur_svg


st.set_page_config(page_title="Builtly v8 Mulighetsstudie", layout="wide")
st.title("Builtly v8 · Mulighetsstudie")
st.caption("Konseptalternativer på konseptnivå — ikke byggetrinn. AI brukes kun i pass 2 og pass 6 når nettverks-AI er aktivert.")


def _parse_wkt(text: str) -> Optional[Polygon]:
    if not text.strip():
        return None
    geom = shapely_wkt.loads(text)
    if not isinstance(geom, Polygon):
        raise ValueError("WKT må være et Polygon")
    return geom.buffer(0)


def _demo_polygon() -> str:
    return "POLYGON ((0 0, 160 0, 160 90, 120 90, 120 170, 0 170, 0 0))"


with st.sidebar:
    st.header("Input")
    target_bra = st.number_input("Mål BRA m²", min_value=1000, max_value=200000, value=30000, step=500)
    avg_unit_bra = st.number_input("Snitt BRA per bolig", min_value=20.0, max_value=150.0, value=55.0, step=1.0)
    requested_count = st.number_input("Overstyr delfelt (0 = auto)", min_value=0, max_value=12, value=0, step=1)
    preset_name = st.selectbox("PlanRegler preset", ["TRONDHEIM_KPA_2022_SONE_2", "GENERISK_TEK17_NORGE"])
    latitude = st.number_input("Latitude", value=63.42, step=0.01)
    longitude = st.number_input("Longitude", value=10.43, step=0.01)
    enable_network_ai = st.toggle("Aktiver nettverks-AI", value=False)
    if enable_network_ai:
        st.info("Sett BUILTLY_ENABLE_NETWORK_AI=1 og relevante API-nøkler i miljøet i faktisk drift.")

poly_text = st.text_area("Buildable polygon (WKT)", value=_demo_polygon(), height=140)
run = st.button("Kjør konseptalternativer", type="primary")

if run:
    try:
        buildable_poly = _parse_wkt(poly_text)
    except Exception as exc:
        st.error(f"Klarte ikke lese WKT: {exc}")
        st.stop()

    rules = TRONDHEIM_KPA_2022_SONE_2 if preset_name == "TRONDHEIM_KPA_2022_SONE_2" else GENERISK_TEK17_NORGE
    options = run_concept_options(
        buildable_poly,
        target_bra_m2=float(target_bra),
        plan_regler=rules,
        requested_delfelt_count=(int(requested_count) if int(requested_count) > 0 else None),
        avg_unit_bra_m2=float(avg_unit_bra),
        latitude_deg=float(latitude),
        longitude_deg=float(longitude),
        site_area_m2=float(buildable_poly.area),
    )

    st.subheader("Konseptalternativer")
    cols = st.columns(len(options))
    for col, opt in zip(cols, options):
        with col:
            st.markdown(f"### {opt.title}")
            st.caption(opt.subtitle)
            st.metric("Score", f"{opt.score:.1f}")
            st.write(f"BRA: {opt.total_bra_m2:,.0f} m²")
            st.write(f"Boliger: {opt.antall_boliger}")
            st.write(f"Sol: {opt.sol_score:.0f}/100")
            st.write(f"MUA: {opt.mua_status}")

    chosen_label = st.selectbox("Velg konsept", [opt.title for opt in options], index=0)
    chosen = next(opt for opt in options if opt.title == chosen_label)
    plan = chosen.masterplan

    if plan is not None:
        st.markdown("---")
        st.subheader("Kvartalstruktur")
        st.components.v1.html(render_quartalstruktur_svg(plan), height=720, scrolling=False)

        st.subheader("Bestemmelser / MUA")
        st.components.v1.html(render_mua_svg(plan), height=720, scrolling=False)

        st.subheader("Delfelt")
        fields = [
            {
                "field_id": f.field_id,
                "typology": f.typology.value,
                "orientation_deg": round(f.orientation_deg, 1),
                "floors": f"{f.floors_min}-{f.floors_max}",
                "target_bra": round(f.target_bra, 1),
                "phase": f.phase,
            }
            for f in plan.delfelt
        ]
        st.dataframe(fields, use_container_width=True)

        st.subheader("Bygg")
        buildings = [
            {
                "bygg_id": b.display_name or b.bygg_id,
                "typology": b.typology.value,
                "delfelt": b.delfelt_id,
                "phase": b.phase,
                "floors": b.floors,
                "bra_m2": round(b.bra_m2, 1),
                "footprint_m2": round(b.footprint_m2, 1),
            }
            for b in plan.bygg
        ]
        st.dataframe(buildings, use_container_width=True)

        st.subheader("Rapportsammendrag")
        st.write(plan.report_summary or "—")
        st.write(plan.report_architectural_assessment or "—")
        if plan.report_risks:
            st.warning("\n".join(plan.report_risks))
