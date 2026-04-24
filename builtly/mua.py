from __future__ import annotations

from typing import List

from shapely.ops import unary_union

from .masterplan_types import ComplianceCheck, ComplianceState, MUAReport


def _bool_state(ok: bool) -> ComplianceState:
    return ComplianceState.JA if ok else ComplianceState.NEI


def calculate_mua(plan, regler=None):
    buildable = getattr(plan, "buildable_polygon", None)
    site_area = float(getattr(plan, "site_area_m2", 0.0) or getattr(buildable, "area", 0.0) or 0.0)
    bygg = list(getattr(plan, "bygg", []) or [])
    footprints = [getattr(b, "footprint", None) for b in bygg if getattr(b, "footprint", None) is not None]
    footprint_union = unary_union(footprints).buffer(0) if footprints else None
    total_bya = float(getattr(plan, "total_bya_m2", 0.0) or getattr(footprint_union, "area", 0.0) or 0.0)
    open_ground = max(0.0, site_area - total_bya - float(getattr(plan, "parkering_areal", 0.0) or 0.0) - float(getattr(plan, "vei_areal", 0.0) or 0.0))

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
    krav_total = req_per_unit * units if req_per_unit > 0 and units > 0 else None
    min_bakke_pct = float(getattr(regler, "mua_min_bakke_pct", 0.0) or 0.0) if regler is not None else 0.0
    min_felles_pct = float(getattr(regler, "mua_min_felles_pct", 0.0) or 0.0) if regler is not None else 0.0
    krav_bakke = krav_total * min_bakke_pct if krav_total is not None and min_bakke_pct > 0 else None
    krav_felles = krav_total * min_felles_pct if krav_total is not None and min_felles_pct > 0 else None

    checks: List[ComplianceCheck] = []
    if krav_total is not None:
        checks.append(ComplianceCheck("mua_total", _bool_state(total >= krav_total), total, krav_total, "m²", "Totalt uteoppholdsareal"))
    if krav_bakke is not None:
        checks.append(ComplianceCheck("mua_bakke", _bool_state(open_ground >= krav_bakke), open_ground, krav_bakke, "m²", "Areal på bakkeplan"))
    if krav_felles is not None:
        checks.append(ComplianceCheck("mua_felles", _bool_state(felles_ground >= krav_felles), felles_ground, krav_felles, "m²", "Felles tilgjengelig uteareal"))

    notes = [
        "MUA er beregnet deterministisk fra buildable polygon minus bygg, vei og parkering.",
        "Private soner og takterrasser er anslått ut fra typologi og etasjetall.",
    ]

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
    )
