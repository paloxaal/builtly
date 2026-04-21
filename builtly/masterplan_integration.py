
from __future__ import annotations

from typing import Iterable, List

from .masterplan_engine import run_masterplan_suite
from .masterplan_types import Masterplan, OptionResult, PlanRegler
from .svg_diagrams import render_concept_svg, render_mua_svg


def masterplan_to_option_result(plan: Masterplan) -> OptionResult:
    concept_svg = render_concept_svg(plan)
    mua_svg = render_mua_svg(plan)
    return OptionResult(
        concept_family=plan.concept_family,
        title=plan.display_title or plan.concept_family.value,
        subtitle=plan.display_subtitle,
        score=plan.score,
        masterplan=plan,
        concept_svg=concept_svg,
        mua_svg=mua_svg,
        summary=plan.report_summary,
        stats={
            "bra_m2": plan.total_bra_m2,
            "bya_m2": plan.total_bya_m2,
            "bya_pct": plan.bya_pct,
            "bra_pct": plan.bra_pct,
            "boliger": plan.antall_boliger,
            "sol_score": plan.sol_report.total_score,
            "mua_status": "JA" if all(c.status != "NEI" for c in plan.mua_report.compliant if c.required is not None) else "DELVIS",
            "bra_deficit": plan.bra_deficit,
        },
    )


def build_option_results(*args, **kwargs) -> List[OptionResult]:
    plans = run_masterplan_suite(*args, **kwargs)
    return [masterplan_to_option_result(plan) for plan in plans]
