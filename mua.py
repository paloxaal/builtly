from __future__ import annotations

from typing import List, Tuple

from shapely.ops import unary_union

from .masterplan_types import ComplianceCheck, ComplianceState, MUAReport


def _bool_state(ok: bool) -> ComplianceState:
    return ComplianceState.JA if ok else ComplianceState.NEI


def _resolve_mua_context(site_area: float, total_bra: float, total_bya: float, regler=None) -> Tuple[str, float, float, str]:
    """Bestem hvor hardt MUA skal håndheves.

    Målet er å unngå at små, tette infill-prosjekter blir systematisk vraket bare
    fordi standard MUA-krav er skrevet for romsligere boligfelt. Samtidig skal vi
    fortsatt beholde normal strenghet på større tomter.

    Returnerer:
      mode: strict | reduced | advisory
      requirement_factor: skalerer MUA-kravene
      score_weight: hvor mye MUA skal telle i konseptscoren
      note: forklaring
    """
    density = float(total_bra or 0.0) / max(float(site_area or 0.0), 1.0)
    coverage = float(total_bya or 0.0) / max(float(site_area or 0.0), 1.0)
    custom = getattr(regler, "custom_rules", {}) or {}

    if custom.get("mua_priority") == "advisory":
        return (
            "advisory",
            0.0,
            0.0,
            "MUA er satt til rådgivende via custom_rules; BRA- og bygningsgrep prioriteres foran uteoppholdsareal i dette scenariet.",
        )
    if custom.get("mua_priority") == "reduced":
        return (
            "reduced",
            0.45,
            0.35,
            "MUA er satt til redusert via custom_rules; tett bymessig/infill-preget utvikling tillater lavere utearealandel enn standard boligfelt.",
        )

    # Svært tett småtomt / infill: MUA er rådgivende.
    if site_area <= 3_000.0 and (density >= 2.0 or coverage >= 0.65):
        return (
            "advisory",
            0.0,
            0.0,
            "Tomten leses som tett infill med høy utnyttelse; MUA behandles derfor som rådgivende i konseptfasen.",
        )

    # Kompakt tomt med høy utnyttelse: reduser kravene, men behold dem.
    if site_area <= 5_500.0 and (density >= 1.4 or coverage >= 0.50):
        return (
            "reduced",
            0.45,
            0.35,
            "Tomten er liten/tett og vurderes som urban infill; MUA-kravene skaleres ned slik at BRA-mål ikke automatisk underkjennes.",
        )

    if site_area <= 9_000.0 and (density >= 1.0 or coverage >= 0.42):
        return (
            "reduced",
            0.70,
            0.60,
            "Tomten er relativt kompakt og tett; MUA-kravene reduseres moderat i tidligfase.",
        )

    return (
        "strict",
        1.0,
        1.0,
        "Standard MUA-logikk brukes; tomten har nok størrelse/luft til at uteoppholdsareal skal vurderes som ordinært krav.",
    )


def calculate_mua(plan, regler=None):
    buildable = getattr(plan, "buildable_polygon", None)
    site_area = float(getattr(plan, "site_area_m2", 0.0) or getattr(buildable, "area", 0.0) or 0.0)
    bygg = list(getattr(plan, "bygg", []) or [])
    footprints = [getattr(b, "footprint", None) for b in bygg if getattr(b, "footprint", None) is not None]
    footprint_union = unary_union(footprints).buffer(0) if footprints else None
    total_bya = float(getattr(plan, "total_bya_m2", 0.0) or getattr(footprint_union, "area", 0.0) or 0.0)
    total_bra = float(getattr(plan, "total_bra_m2", 0.0) or 0.0)
    open_ground = max(
        0.0,
        site_area
        - total_bya
        - float(getattr(plan, "parkering_areal", 0.0) or 0.0)
        - float(getattr(plan, "vei_areal", 0.0) or 0.0),
    )

    private = 0.0
    for b in bygg:
        typ = getattr(getattr(b, "typology", None), "value", str(getattr(b, "typology", "")))
        fp = float(getattr(b, "footprint_m2", 0.0) or 0.0)
        if typ == "Rekkehus":
            private += fp * 0.24
        elif typ == "Karré":
            private += fp * 0.05
        else:
            private += fp * 0.08
    tak = sum(float(getattr(b, "tak_mua_m2", 0.0) or 0.0) for b in bygg)
    felles_ground = max(0.0, open_ground - private)
    total = felles_ground + private + tak

    units = int(getattr(plan, "antall_boliger", 0) or 0)
    req_per_unit = float(getattr(regler, "mua_per_bolig_m2", 0.0) or 0.0) if regler is not None else 0.0
    min_bakke_pct = float(getattr(regler, "mua_min_bakke_pct", 0.0) or 0.0) if regler is not None else 0.0
    min_felles_pct = float(getattr(regler, "mua_min_felles_pct", 0.0) or 0.0) if regler is not None else 0.0

    mode, req_factor, score_weight, context_note = _resolve_mua_context(site_area, total_bra, total_bya, regler)

    raw_krav_total = req_per_unit * units if req_per_unit > 0 and units > 0 else None
    krav_total = raw_krav_total * req_factor if raw_krav_total is not None and req_factor > 0 else None
    krav_bakke = krav_total * min_bakke_pct if krav_total is not None and min_bakke_pct > 0 else None
    krav_felles = krav_total * min_felles_pct if krav_total is not None and min_felles_pct > 0 else None

    checks: List[ComplianceCheck] = []
    notes = [
        "MUA er beregnet deterministisk fra buildable polygon minus bygg, vei og parkering.",
        "Private soner og takterrasser er anslått ut fra typologi og etasjetall.",
        context_note,
    ]

    if mode == "advisory":
        checks.append(
            ComplianceCheck(
                "mua_context_override",
                ComplianceState.JA,
                actual_value=total,
                required_value=raw_krav_total,
                unit="m²",
                note="Tett infill / høy utnyttelse: MUA er rådgivende i konseptfasen og skal ikke automatisk underkjenne volumgrepet.",
            )
        )
    else:
        if krav_total is not None:
            checks.append(ComplianceCheck("mua_total", _bool_state(total >= krav_total), total, krav_total, "m²", "Totalt uteoppholdsareal"))
        if krav_bakke is not None:
            checks.append(ComplianceCheck("mua_bakke", _bool_state(open_ground >= krav_bakke), open_ground, krav_bakke, "m²", "Areal på bakkeplan"))
        if krav_felles is not None:
            checks.append(ComplianceCheck("mua_felles", _bool_state(felles_ground >= krav_felles), felles_ground, krav_felles, "m²", "Felles tilgjengelig uteareal"))
        if mode == "reduced":
            notes.append(
                f"Effektivt MUA-krav er skalert til {req_factor:.0%} av standardkravet for å håndtere kompakt/tett bymessig utvikling."
            )

    return MUAReport(
        total=total,
        krav_total=krav_total,
        bakke=open_ground,
        krav_bakke=krav_bakke,
        fellesareal=felles_ground,
        krav_fellesareal=krav_felles,
        privat=private,
        tak=tak,
        open_ground_area=open_ground,
        checks=checks,
        notes=notes,
        mode=mode,
        effective_requirement_factor=req_factor,
        score_weight=score_weight,
        advisory_override=(mode == "advisory"),
    )
