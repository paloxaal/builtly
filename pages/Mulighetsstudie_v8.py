"""Builtly v8 demo-side — ren kobling til v8-motoren.

Denne siden kjører v8-pipelinen direkte mot WKT-input og viser:
- Tre konseptfamilier (LINEAR_MIXED, COURTYARD_URBAN, CLUSTER_PARK)
- Valgt konsept med KPI-tall, KVARTALSTRUKTUR-SVG og MUA-SVG
- Delfelt- og bygg-tabeller
- Rapportsammendrag fra AI-pass 6 (hvis BUILTLY_ENABLE_NETWORK_AI er satt)

Hensikten er å gi et rent testmiljø for v8-motoren uten at resultatet
oversettes til legacy-format. Dagens store pages/Mulighetsstudie.py er
uberørt av denne siden og kan beholdes for legacy-flyten.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from shapely import wkt as shapely_wkt
from shapely.geometry import Polygon

from builtly.masterplan_integration import run_concept_options
from builtly.plan_regler_presets import (
    GENERISK_TEK17_NORGE,
    TRONDHEIM_KPA_2022_SONE_2,
)
from builtly.svg_diagrams import render_mua_svg, render_quartalstruktur_svg


st.set_page_config(page_title="Builtly v8 · Mulighetsstudie (demo)", layout="wide")

BUILTLY_CSS = """
<style>
.stApp { background: linear-gradient(180deg, #04111d 0%, #061423 100%); color: #dbe7f5; }
h1,h2,h3,h4 { color: #eef6ff; }
.muted { color:#9fb4c8; }
.card {
    background: linear-gradient(180deg, rgba(8,24,39,0.95) 0%, rgba(3,13,23,0.95) 100%);
    border: 1px solid rgba(56,189,248,0.18);
    border-radius: 18px;
    padding: 18px 20px;
    min-height: 200px;
    box-shadow: 0 10px 28px rgba(0,0,0,0.18);
}
.card.selected {
    border: 2px solid #38bdf8;
    box-shadow: 0 0 0 2px rgba(56,189,248,0.25);
}
.kpi {
    background: linear-gradient(180deg, rgba(7,21,34,0.96) 0%, rgba(3,13,23,0.96) 100%);
    border: 1px solid rgba(56,189,248,0.16);
    border-radius: 18px;
    padding: 20px;
    min-height: 132px;
}
.eyebrow { color:#38bdf8; font-size:0.92rem; letter-spacing:0.08em; text-transform:uppercase; font-weight:700; }
.title-xl { font-size:3rem; font-weight:800; line-height:1.05; margin:0; }
.title-lg { font-size:1.55rem; font-weight:800; line-height:1.1; margin:0; }
.score-pill {
    display:inline-block; padding:0.25rem 0.6rem; border-radius:999px;
    background:#10243c; color:#dbe7f5; border:1px solid rgba(56,189,248,0.35); font-weight:700;
}
</style>
"""
st.markdown(BUILTLY_CSS, unsafe_allow_html=True)

PRESETS = {
    "TRONDHEIM_KPA_2022_SONE_2": TRONDHEIM_KPA_2022_SONE_2,
    "GENERISK_TEK17_NORGE": GENERISK_TEK17_NORGE,
}

DEFAULT_WKT = "POLYGON ((0 0, 160 0, 160 90, 120 90, 120 170, 0 170, 0 0))"
TYHOLT_WKT = "POLYGON ((0 0, 167.5 0, 167.5 200, 0 200, 0 0))"


def _parse_wkt(text: str) -> Optional[Polygon]:
    if not text.strip():
        return None
    geom = shapely_wkt.loads(text)
    if not isinstance(geom, Polygon):
        raise ValueError("WKT må være et Polygon (ikke MultiPolygon).")
    cleaned = geom.buffer(0)
    if not isinstance(cleaned, Polygon):
        raise ValueError("Polygonet ble ugyldig etter opprydding.")
    return cleaned


def _kpi_card(label: str, value: str) -> str:
    return f'<div class="kpi"><div class="eyebrow">{label}</div><h3 class="title-lg">{value}</h3></div>'


def _concept_card(option, is_selected: bool = False) -> str:
    klasse = "card selected" if is_selected else "card"
    typologier = ", ".join(option.typology_mix) if option.typology_mix else "—"
    return f"""
    <div class="{klasse}">
      <div class="eyebrow">{option.concept_family.value}</div>
      <h3 class="title-lg">{option.title}</h3>
      <p class="muted" style="margin:6px 0 10px 0;">{option.subtitle or ''}</p>
      <p style="margin:0;"><span class="score-pill">Score {option.score:.1f}</span></p>
      <p style="margin:10px 0 4px 0;"><strong>BRA:</strong> {option.total_bra_m2:,.0f} m²</p>
      <p style="margin:4px 0;"><strong>BYA:</strong> {option.total_bya_m2:,.0f} m²</p>
      <p style="margin:4px 0;"><strong>Boliger:</strong> {option.antall_boliger}</p>
      <p style="margin:4px 0;"><strong>Typologier:</strong> {typologier}</p>
      <p style="margin:4px 0;"><strong>Sol:</strong> {option.sol_score:.0f}/100 · <strong>MUA:</strong> {option.mua_status}</p>
    </div>
    """.replace(",", " ")


def _delfelt_dataframe(plan) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Delfelt": f.field_id,
                "Typologi": f.typology.value,
                "Orientering (°)": round(f.orientation_deg, 1),
                "Etasjer": f"{f.floors_min}-{f.floors_max}",
                "Mål BRA": round(f.target_bra, 0),
                "Trinn": f.phase,
            }
            for f in plan.delfelt
        ]
    )


def _bygg_dataframe(plan) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Bygg-ID": b.display_name or b.bygg_id,
                "Typologi": b.typology.value,
                "Delfelt": b.delfelt_id,
                "Trinn": b.phase,
                "Etasjer": b.floors,
                "BRA m²": round(b.bra_m2, 0),
                "Fotavtrykk m²": round(b.footprint_m2, 0),
                "Høyde m": round(b.height_m, 1),
            }
            for b in plan.bygg
        ]
    )


def _selected_section(option, buildable_poly: Polygon) -> None:
    """Vis alle detaljer for valgt konsept."""
    plan = option.masterplan

    # KPI-rad
    st.markdown("## Anbefalt: " + option.title)
    cols = st.columns(4)
    cards = [
        ("Typologier", ", ".join(option.typology_mix) or option.concept_family.value),
        ("BRA", f"{option.total_bra_m2:,.0f} m²".replace(",", " ")),
        ("Boliger", str(option.antall_boliger)),
        ("Score", f"{option.score:.1f}/100"),
    ]
    for col, (label, value) in zip(cols, cards):
        with col:
            st.markdown(_kpi_card(label, value), unsafe_allow_html=True)

    buildable_area = float(buildable_poly.area)
    cols2 = st.columns(4)
    cards2 = [
        ("BYA", f"{option.total_bya_m2:,.0f} m²".replace(",", " ")),
        ("%-BYA", f"{option.total_bya_m2 / buildable_area * 100:.0f}%" if buildable_area > 0 else "0%"),
        ("%-BRA", f"{option.total_bra_m2 / buildable_area * 100:.0f}%" if buildable_area > 0 else "0%"),
        ("Sol-score", f"{option.sol_score:.0f}/100"),
    ]
    for col, (label, value) in zip(cols2, cards2):
        with col:
            st.markdown(_kpi_card(label, value), unsafe_allow_html=True)

    st.markdown("---")

    # KVARTALSTRUKTUR + MUA side ved side
    st.markdown("## Konsept og bestemmelser")
    left, right = st.columns(2)
    with left:
        st.markdown("### Kvartalstruktur")
        try:
            svg1 = render_quartalstruktur_svg(plan)
            components.html(svg1, height=700, scrolling=False)
        except Exception as exc:
            st.error(f"Kunne ikke rendre Kvartalstruktur-SVG: {exc}")
    with right:
        st.markdown("### Bestemmelser / MUA")
        try:
            svg2 = render_mua_svg(plan)
            components.html(svg2, height=700, scrolling=False)
        except Exception as exc:
            st.error(f"Kunne ikke rendre MUA-SVG: {exc}")

    st.markdown("---")

    # Delfelt-tabell
    st.markdown("## Delfelt")
    st.dataframe(_delfelt_dataframe(plan), use_container_width=True, hide_index=True)

    # Bygg-tabell
    st.markdown("## Bygg")
    st.dataframe(_bygg_dataframe(plan), use_container_width=True, hide_index=True)

    # Rapport
    st.markdown("## Rapportsammendrag")
    if plan.report_summary:
        st.markdown(plan.report_summary)
    else:
        st.caption("Ingen rapporttekst generert.")
    if plan.report_recommendation:
        st.markdown("**Anbefaling:** " + plan.report_recommendation)
    if plan.report_risks:
        st.warning("Risikoer:\n\n" + "\n".join(f"- {r}" for r in plan.report_risks))


def main() -> None:
    st.markdown('<div class="eyebrow">Builtly v8</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="title-xl">Mulighetsstudie — ren v8-demo</h1>', unsafe_allow_html=True)
    st.markdown(
        '<p class="muted">Kjører v8-motoren direkte mot WKT-input. '
        'Tre konseptfamilier (LINEAR_MIXED, COURTYARD_URBAN, CLUSTER_PARK) genereres. '
        'AI-pass 2 og 6 aktiveres når BUILTLY_ENABLE_NETWORK_AI=1.</p>',
        unsafe_allow_html=True,
    )

    with st.container():
        col_left, col_right = st.columns([1.4, 1.0])
        with col_left:
            wkt_input = st.text_area(
                "Buildable polygon (WKT)",
                value=DEFAULT_WKT,
                height=140,
                help="Lim inn en WKT-polygon. Ruten under viser Tyholt-eksempel (33 500 m²).",
            )
            preset_col1, preset_col2 = st.columns(2)
            with preset_col1:
                if st.button("Last Tyholt-eksempel"):
                    st.session_state["_v8_demo_wkt"] = TYHOLT_WKT
                    st.rerun()
            with preset_col2:
                if st.button("Last L-polygon-eksempel"):
                    st.session_state["_v8_demo_wkt"] = DEFAULT_WKT
                    st.rerun()
            if "_v8_demo_wkt" in st.session_state:
                wkt_input = st.session_state.pop("_v8_demo_wkt")
        with col_right:
            preset_name = st.selectbox(
                "PlanRegler-preset",
                list(PRESETS.keys()),
                index=0,
                help="Regelverk for MUA, parkering og avstandskrav.",
            )
            target_bra = st.number_input(
                "Mål BRA (m²)",
                min_value=1000,
                max_value=200000,
                value=40000,
                step=500,
            )
            avg_unit_bra = st.number_input(
                "Snitt BRA per bolig",
                min_value=20.0,
                max_value=150.0,
                value=55.0,
                step=1.0,
            )
            requested_delfelt = st.number_input(
                "Overstyr delfelt-antall (0 = auto)",
                min_value=0,
                max_value=12,
                value=0,
                step=1,
            )

    run = st.button("Kjør v8-konseptgenerator", type="primary", use_container_width=True)

    if run:
        try:
            buildable_poly = _parse_wkt(wkt_input)
            if buildable_poly is None:
                st.error("WKT er tomt.")
                return
        except Exception as exc:
            st.error(f"Kunne ikke parse WKT: {exc}")
            return

        st.session_state["_v8_demo_buildable_poly"] = buildable_poly

        with st.spinner("Kjører v8-motor (tre konsepter × opptil seks pass)..."):
            try:
                options = run_concept_options(
                    buildable_poly,
                    target_bra_m2=float(target_bra),
                    plan_regler=PRESETS[preset_name],
                    requested_delfelt_count=(int(requested_delfelt) if requested_delfelt > 0 else None),
                    avg_unit_bra_m2=float(avg_unit_bra),
                    latitude_deg=63.42,
                    longitude_deg=10.43,
                    site_area_m2=float(buildable_poly.area),
                )
            except Exception as exc:
                st.error(f"V8-motor feilet: {type(exc).__name__}: {exc}")
                import traceback
                with st.expander("Teknisk traceback"):
                    st.code(traceback.format_exc())
                return

        if not options:
            st.warning("V8 returnerte ingen konsepter.")
            return

        st.session_state["_v8_demo_options"] = options
        st.session_state["_v8_demo_selected"] = options[0].concept_family.value

    options = st.session_state.get("_v8_demo_options", [])
    buildable_poly = st.session_state.get("_v8_demo_buildable_poly")

    if not options:
        st.info("Kjør motoren for å generere LINEAR_MIXED, COURTYARD_URBAN og CLUSTER_PARK.")
        return

    # Vis de tre konseptkortene
    st.markdown("---")
    st.markdown("## Konseptalternativer")
    current = st.session_state.get("_v8_demo_selected", options[0].concept_family.value)
    cols = st.columns(len(options))
    for col, option in zip(cols, options):
        is_sel = option.concept_family.value == current
        with col:
            st.markdown(_concept_card(option, is_selected=is_sel), unsafe_allow_html=True)
            if st.button(
                f"Velg {option.title}" if not is_sel else f"✓ {option.title}",
                key=f"v8_select_{option.concept_family.value}",
                use_container_width=True,
            ):
                st.session_state["_v8_demo_selected"] = option.concept_family.value
                st.rerun()

    selected_option = next(
        (o for o in options if o.concept_family.value == current),
        options[0],
    )

    st.markdown("---")
    _selected_section(selected_option, buildable_poly)


if __name__ == "__main__":
    main()
