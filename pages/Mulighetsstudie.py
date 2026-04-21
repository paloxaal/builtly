
from __future__ import annotations

import math
from typing import Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from shapely import wkt
from shapely.geometry import Polygon

from builtly.masterplan_integration import build_option_results
from builtly.plan_regler_presets import PRESETS


st.set_page_config(page_title="Builtly v8 · Mulighetsstudie", layout="wide")

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
    min-height: 182px;
    box-shadow: 0 10px 28px rgba(0,0,0,0.18);
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
.title-lg { font-size:1.75rem; font-weight:800; line-height:1.1; margin:0; }
.score-pill {
    display:inline-block; padding:0.25rem 0.6rem; border-radius:999px;
    background:#10243c; color:#dbe7f5; border:1px solid rgba(56,189,248,0.35); font-weight:700;
}
</style>
"""
st.markdown(BUILTLY_CSS, unsafe_allow_html=True)

DEFAULT_WKT = "POLYGON ((0 0, 160 0, 160 90, 120 90, 120 170, 0 170, 0 0))"


def _site_figure(option) -> go.Figure:
    plan = option.masterplan
    fig = go.Figure()
    x, y = plan.buildable_polygon.exterior.xy
    fig.add_trace(
        go.Scatter(
            x=x, y=y, fill="toself", mode="lines",
            line=dict(color="#38bdf8", width=3),
            fillcolor="rgba(56,189,248,0.10)",
            name="Tomt",
        )
    )
    field_palette = ["rgba(15,118,110,0.35)", "rgba(29,78,216,0.35)", "rgba(109,40,217,0.35)", "rgba(161,98,7,0.35)", "rgba(14,165,233,0.35)"]
    for idx, field in enumerate(plan.delfelt):
        fx, fy = field.polygon.exterior.xy
        fig.add_trace(
            go.Scatter(
                x=fx, y=fy, fill="toself", mode="lines",
                line=dict(color="#38bdf8", width=1.5),
                fillcolor=field_palette[idx % len(field_palette)],
                name=field.field_id,
                hovertemplate=f"{field.field_id} · {field.typology.value}<extra></extra>",
            )
        )
    for b in plan.bygg:
        bx, by = b.footprint.exterior.xy
        fig.add_trace(
            go.Scatter(
                x=bx, y=by, fill="toself", mode="lines",
                line=dict(color="#e8eef7", width=1.2),
                fillcolor="rgba(232,238,247,0.88)",
                name=b.bygg_id,
                hovertemplate=f"{b.bygg_id} · {b.typology.value} · {b.floors} et · {b.bra_m2:,.0f} m² BRA<extra></extra>".replace(",", " "),
                showlegend=False,
            )
        )
    fig.update_layout(
        height=700,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="#061423",
        plot_bgcolor="#061423",
        xaxis=dict(visible=False, scaleanchor="y", scaleratio=1),
        yaxis=dict(visible=False),
    )
    return fig


def _small_volume_card(option) -> str:
    plan = option.masterplan
    return f"""
    <div class="card">
      <div class="eyebrow">{plan.concept_family.value}</div>
      <h3 class="title-lg">{option.title}</h3>
      <p class="muted">{option.subtitle}</p>
      <p><span class="score-pill">Score {option.score:.1f}</span></p>
      <p>BRA {plan.total_bra_m2:,.0f} m² · BYA {plan.total_bya_m2:,.0f} m² · {plan.antall_boliger} boliger</p>
      <p>Sol {plan.sol_report.total_score:.0f}/100 · MUA {'JA' if all(c.status != 'NEI' for c in plan.mua_report.compliant if c.required is not None) else 'DELVIS'}</p>
    </div>
    """.replace(",", " ")


def _render_svg(svg: str, height: int) -> None:
    components.html(svg, height=height, scrolling=False)


def _build_delfelt_df(plan) -> pd.DataFrame:
    return pd.DataFrame(
        [
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
    )


def _build_bygg_df(plan) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "bygg_id": b.bygg_id,
                "typology": b.typology.value,
                "delfelt": b.delfelt_id,
                "phase": b.phase,
                "floors": b.floors,
                "bra_m2": round(b.bra_m2, 1),
                "footprint_m2": round(b.footprint_m2, 1),
            }
            for b in plan.bygg
        ]
    )


def _summary_kpis(option) -> None:
    stats = option.stats
    cols = st.columns(4)
    cards = [
        ("Typologi", option.title),
        ("BRA", f"{stats['bra_m2']:,.0f} m²".replace(",", " ")),
        ("Boliger", f"{stats['boliger']}"),
        ("Score", f"{option.score:.1f}/100"),
        ("%BRA", f"{stats['bra_pct']:.0f}%"),
        ("BYA", f"{stats['bya_pct']:.0f}%"),
        ("Solscore", f"{stats['sol_score']:.0f}/100"),
        ("BRA-avvik", f"{stats['bra_deficit']:,.0f} m²".replace(",", " ")),
    ]
    for col, (label, value) in zip(cols * 2, cards):
        with col:
            st.markdown(f'<div class="kpi"><div class="eyebrow">{label}</div><h3 class="title-lg">{value}</h3></div>', unsafe_allow_html=True)

def _selected_option_section(option) -> None:
    st.markdown("## Anbefalt alternativ")
    _summary_kpis(option)
    st.markdown("---")
    st.markdown("## Volumskisser")
    cols = st.columns(3)
    for col, card in zip(cols, st.session_state["option_results"]):
        with col:
            st.markdown(_small_volume_card(card), unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("## 2D planvisning")
    st.plotly_chart(_site_figure(option), use_container_width=True)

    st.markdown("---")
    st.markdown("## Konsept og bestemmelser")
    left, right = st.columns(2)
    with left:
        st.markdown("### Kvartalsstruktur")
        _render_svg(option.concept_svg, 760)
    with right:
        st.markdown("### Bestemmelser / MUA")
        _render_svg(option.mua_svg, 760)

    st.markdown("---")
    st.markdown("## Delfelt")
    st.dataframe(_build_delfelt_df(option.masterplan), use_container_width=True, hide_index=True)
    st.markdown("## Bygg")
    st.dataframe(_build_bygg_df(option.masterplan), use_container_width=True, hide_index=True)
    st.markdown("## Rapportsammendrag")
    st.write(option.summary)


def main() -> None:
    st.markdown('<div class="eyebrow">Builtly v8</div>', unsafe_allow_html=True)
    st.markdown('<h1 class="title-xl">Mulighetsstudie</h1>', unsafe_allow_html=True)
    st.markdown('<p class="muted">Reell V8-flyt: tre konseptfamilier, delfelt som struktur og deterministisk geometri/MUA.</p>', unsafe_allow_html=True)

    with st.container():
        left, right = st.columns([1.2, 0.8])
        with left:
            wkt_str = st.text_area("Buildable polygon (WKT)", value=DEFAULT_WKT, height=160)
        with right:
            preset_name = st.selectbox("PlanRegler-preset", list(PRESETS.keys()), index=0)
            target_bra = st.number_input("Mål BRA (m²)", min_value=1000, max_value=150000, value=40000, step=500)
            use_ai = st.toggle("Bruk nettverks-AI i pass 2 og 6", value=False)

    run = st.button("Kjør konseptalternativer", type="primary", use_container_width=False)

    if run:
        try:
            buildable_poly = wkt.loads(wkt_str)
            if not isinstance(buildable_poly, Polygon):
                raise ValueError("WKT må være én Polygon.")
            option_results = build_option_results(
                buildable_poly=buildable_poly,
                plan_regler=PRESETS[preset_name],
                target_bra_m2=float(target_bra),
                context={"preset": preset_name},
                use_ai=use_ai,
            )
            st.session_state["option_results"] = option_results
            st.session_state["selected_concept"] = option_results[0].concept_family.value if option_results else None
        except Exception as exc:
            st.error(f"Klarte ikke å kjøre v8-motoren: {exc}")

    option_results = st.session_state.get("option_results", [])
    if option_results:
        st.markdown("---")
        st.markdown("## Konseptalternativer")
        cols = st.columns(3)
        current = st.session_state.get("selected_concept", option_results[0].concept_family.value)
        for col, option in zip(cols, option_results):
            with col:
                st.markdown(_small_volume_card(option), unsafe_allow_html=True)
                if st.button(f"Velg {option.title}", key=f"select_{option.concept_family.value}", use_container_width=True):
                    st.session_state["selected_concept"] = option.concept_family.value
        selected = next((o for o in option_results if o.concept_family.value == st.session_state.get("selected_concept")), option_results[0])
        st.markdown("---")
        _selected_option_section(selected)
    else:
        st.info("Kjør motoren for å generere LINEAR_MIXED, COURTYARD_URBAN og CLUSTER_PARK.")

if __name__ == "__main__":
    main()
