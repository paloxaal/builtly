"""
Site intelligence-lag for Builtly.

Denne modulen tar generiske feature payloads fra GeodataOnlineClient og
konverterer dem til et beslutningsgrunnlag som kan brukes i UI, rapport
og rangering av volumalernativer.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from shapely.geometry import Point, Polygon
    HAS_SHAPELY = True
except Exception:
    HAS_SHAPELY = False
    Point = Polygon = None  # type: ignore[assignment]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = text.replace("ø", "o").replace("Ø", "O")
    text = text.replace("å", "a").replace("Å", "A")
    text = text.replace("æ", "ae").replace("Æ", "AE")
    return re.sub(r"[^a-zA-Z0-9]+", " ", text).lower().strip()


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        cleaned = str(value).strip().replace(" ", "").replace(",", ".")
        match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if match:
            return float(match.group(0))
    except Exception:
        pass
    return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _pick_attr(attrs: Dict[str, Any], include: Sequence[str], exclude: Sequence[str] = ()) -> Any:
    if not attrs:
        return None
    normalized = {str(key): _clean_text(key) for key in attrs.keys()}
    include_terms = [_clean_text(item) for item in include]
    exclude_terms = [_clean_text(item) for item in exclude]
    for key, cleaned in normalized.items():
        if any(term in cleaned for term in include_terms) and not any(term in cleaned for term in exclude_terms):
            value = attrs.get(key)
            if value not in (None, "", " "):
                return value
    return None


def _counter_to_dict(counter: Counter, limit: int = 8) -> Dict[str, int]:
    return {str(key): int(value) for key, value in counter.most_common(limit) if str(key).strip()}


def _distance_band(distance_m: Optional[float]) -> str:
    if distance_m is None:
        return "ukjent"
    if distance_m <= 150:
        return "0-150 m"
    if distance_m <= 300:
        return "150-300 m"
    if distance_m <= 500:
        return "300-500 m"
    if distance_m <= 800:
        return "500-800 m"
    return ">800 m"


def _feature_distance(feature: Dict[str, Any], site_polygon: Any) -> Optional[float]:
    if not HAS_SHAPELY or site_polygon is None:
        return None
    geom = feature.get("geometry")
    if geom is None:
        return None
    try:
        return float(geom.distance(site_polygon))
    except Exception:
        return None


def _feature_overlap_pct(feature: Dict[str, Any], site_polygon: Any) -> float:
    if not HAS_SHAPELY or site_polygon is None:
        return 0.0
    geom = feature.get("geometry")
    if geom is None or getattr(geom, "geom_type", "") not in {"Polygon", "MultiPolygon"}:
        return 0.0
    try:
        site_area = float(site_polygon.area)
        if site_area <= 0:
            return 0.0
        return float((geom.intersection(site_polygon).area / site_area) * 100.0)
    except Exception:
        return 0.0


def _dedupe_keep_order(items: Iterable[str], limit: int = 8) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        cleaned = item.strip()
        if not cleaned:
            continue
        key = _clean_text(cleaned)
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def _title_or_default(value: Any, default: str) -> str:
    text = str(value).strip() if value not in (None, "") else ""
    return text if text else default


PLAN_RISK_KEYWORDS = {
    "fare": ["fare", "risiko", "kvikkleire", "ras", "skred", "flom", "stormflo", "overvann"],
    "stoy": ["stoy", "gul sone", "rod sone", "trafikkstoy", "flystoy"],
    "vern": ["vern", "bevaring", "hensyn kultur", "kulturminne", "fredning"],
    "byggegrense": ["byggegrense", "avstand", "grense mot vei", "grense mot sj"],
    "miljo": ["forurens", "radon", "markfukt", "drenering"],
}

TRANSIT_KEYWORDS = ["holdeplass", "stasjon", "bus", "kollektiv", "tram", "jernbane", "tog", "ferge"]
PARKING_KEYWORDS = ["parkering", "p plass", "parkeringsplass", "innfartsparkering"]
ROAD_KEYWORDS = ["veg", "vei", "road", "gate", "fartsgrense", "trafikk"]
CYCLE_KEYWORDS = ["sykkel", "bike"]


def summarize_plan_payload(payload: Dict[str, Any], site_polygon: Any) -> Dict[str, Any]:
    features = list(payload.get("features") or [])
    status_counts: Counter = Counter()
    purpose_counts: Counter = Counter()
    flag_counts: Counter = Counter()
    plan_items: List[Dict[str, Any]] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        layer_name = _title_or_default(feature.get("layer_name"), "Planlag")
        distance_m = _feature_distance(feature, site_polygon)
        overlap_pct = round(_feature_overlap_pct(feature, site_polygon), 2)

        plan_name = _pick_attr(attrs, ["plannavn", "plannavn", "navn", "plan", "planid"], ["kommune"])
        plan_id = _pick_attr(attrs, ["planid", "planident", "plannr", "plan_nr", "id"])
        status = _pick_attr(attrs, ["status", "ikraft", "vedtak", "plantype", "fase", "plankategori"])
        purpose = _pick_attr(attrs, ["formaal", "formal", "arealbruk", "areal", "bruk"])
        legal_effect = _pick_attr(attrs, ["rettsvirk", "bestemm", "hensyn", "restriks", "fare", "vern"])

        combined_text = " ".join(
            [
                layer_name,
                str(plan_name or ""),
                str(status or ""),
                str(purpose or ""),
                str(legal_effect or ""),
            ]
        )
        cleaned = _clean_text(combined_text)
        flags: List[str] = []
        for flag, keywords in PLAN_RISK_KEYWORDS.items():
            if any(keyword in cleaned for keyword in [_clean_text(word) for word in keywords]):
                flags.append(flag)
                flag_counts[flag] += 1

        status_text = _title_or_default(status, "Ukjent status")
        purpose_text = _title_or_default(purpose, layer_name)
        status_counts[status_text] += 1
        purpose_counts[purpose_text] += 1

        if overlap_pct > 0 or (distance_m is not None and distance_m <= 200):
            plan_items.append(
                {
                    "name": _title_or_default(plan_name, layer_name),
                    "plan_id": str(plan_id) if plan_id not in (None, "") else "",
                    "layer_name": layer_name,
                    "status": status_text,
                    "purpose": purpose_text,
                    "distance_m": round(float(distance_m or 0.0), 1) if distance_m is not None else None,
                    "overlap_pct": overlap_pct,
                    "flags": flags,
                }
            )

    plan_items.sort(key=lambda item: (-float(item.get("overlap_pct") or 0.0), float(item.get("distance_m") or 999999.0), item.get("name", "")))
    top_items = plan_items[:12]

    dominant_plan = top_items[0]["name"] if top_items else "Ingen identifisert plan"
    regulatory_risk_score = _clamp((sum(flag_counts.values()) * 9.0) + (8.0 if len(status_counts) > 2 else 0.0), 0.0, 100.0)
    if any(item.get("overlap_pct", 0) > 25 for item in top_items) and (flag_counts.get("fare", 0) or flag_counts.get("stoy", 0)):
        regulatory_risk_score = _clamp(regulatory_risk_score + 12.0, 0.0, 100.0)

    notes: List[str] = []
    if top_items:
        notes.append(f"Dominerende plankontekst er {dominant_plan}.")
    if flag_counts.get("fare", 0):
        notes.append("Plan- eller hensynsdata indikerer faretema som boer kontrolleres tidlig.")
    if flag_counts.get("stoy", 0):
        notes.append("Det finnes stoyrelaterte signaler i plankonteksten som kan paavirke uteareal og planloesning.")
    if flag_counts.get("vern", 0):
        notes.append("Plankonteksten peker paa vern/bevaring som kan begrense volum eller fasadegrep.")
    if not notes and top_items:
        notes.append("Reguleringskontekst er funnet og kan brukes som et mye sterkere beslutningsgrunnlag enn fri tekst alene.")

    return {
        "available": bool(features),
        "source": payload.get("source", "Geodata Online DOK Plan"),
        "service": payload.get("service", ""),
        "feature_count": len(features),
        "nearby_plan_count": len(top_items),
        "dominant_plan": dominant_plan,
        "plans": top_items,
        "status_counts": _counter_to_dict(status_counts),
        "purpose_counts": _counter_to_dict(purpose_counts),
        "risk_flags": list(flag_counts.keys()),
        "regulatory_risk_score": round(float(regulatory_risk_score), 1),
        "notes": _dedupe_keep_order(notes, limit=6),
        "errors": list(payload.get("errors") or []),
    }


def summarize_project_payload(payload: Dict[str, Any], site_polygon: Any) -> Dict[str, Any]:
    features = list(payload.get("features") or [])
    status_counts: Counter = Counter()
    category_counts: Counter = Counter()
    distance_counts: Counter = Counter()
    nearby: List[Dict[str, Any]] = []

    for feature in features:
        attrs = feature.get("attributes") or {}
        layer_name = _title_or_default(feature.get("layer_name"), "Utbygging")
        distance_m = _feature_distance(feature, site_polygon)
        if distance_m is None or distance_m > 900:
            continue

        name = _pick_attr(attrs, ["navn", "prosjekt", "objekt", "adresse", "plan"], ["kommunenavn"])
        status = _pick_attr(attrs, ["status", "fase", "bygningstatus", "vedtak", "sak"])
        category = _pick_attr(attrs, ["type", "kategori", "bygningstype", "tiltak", "formaal", "formal"])
        label = _title_or_default(name, layer_name)
        status_text = _title_or_default(status, "Ukjent status")
        category_text = _title_or_default(category, layer_name)

        status_counts[status_text] += 1
        category_counts[category_text] += 1
        distance_counts[_distance_band(distance_m)] += 1
        nearby.append(
            {
                "name": label,
                "layer_name": layer_name,
                "status": status_text,
                "category": category_text,
                "distance_m": round(float(distance_m), 1),
            }
        )

    nearby.sort(key=lambda item: (float(item.get("distance_m") or 999999.0), item.get("name", "")))
    within_150 = sum(1 for item in nearby if float(item.get("distance_m") or 999999.0) <= 150.0)
    within_300 = sum(1 for item in nearby if float(item.get("distance_m") or 999999.0) <= 300.0)
    within_500 = sum(1 for item in nearby if float(item.get("distance_m") or 999999.0) <= 500.0)

    development_pressure_score = _clamp((within_150 * 20.0) + (max(0, within_300 - within_150) * 8.0) + (max(0, within_500 - within_300) * 3.0), 0.0, 100.0)

    notes: List[str] = []
    if within_150 >= 3:
        notes.append("Det er betydelig utbyggingsaktivitet tett paa tomten, noe som boer brukes aktivt i markeds- og naboskapsvurderingen.")
    elif within_300 >= 2:
        notes.append("Det finnes naerliggende utviklingsaktivitet som boer tas med i vurderingen av konkurranse og byutviklingsretning.")
    elif nearby:
        notes.append("Omraadet viser registrert utbyggingsaktivitet, som gir bedre kontekst enn a analysere tomten isolert.")

    return {
        "available": bool(features),
        "source": payload.get("source", "Geodata Online Utbygger"),
        "service": payload.get("service", ""),
        "feature_count": len(features),
        "nearby_count": len(nearby),
        "within_150_m": within_150,
        "within_300_m": within_300,
        "within_500_m": within_500,
        "distance_counts": _counter_to_dict(distance_counts),
        "status_counts": _counter_to_dict(status_counts),
        "category_counts": _counter_to_dict(category_counts),
        "development_pressure_score": round(float(development_pressure_score), 1),
        "nearby_projects": nearby[:12],
        "notes": _dedupe_keep_order(notes, limit=5),
        "errors": list(payload.get("errors") or []),
    }


def _classify_transport_feature(feature: Dict[str, Any]) -> str:
    attrs = feature.get("attributes") or {}
    layer_name = _clean_text(feature.get("layer_name", ""))
    full_text = " ".join([layer_name] + [_clean_text(value) for value in attrs.values() if value is not None])
    if any(word in full_text for word in TRANSIT_KEYWORDS):
        return "Transit"
    if any(word in full_text for word in PARKING_KEYWORDS):
        return "Parkering"
    if any(word in full_text for word in CYCLE_KEYWORDS):
        return "Sykkel"
    if any(word in full_text for word in ROAD_KEYWORDS):
        return "Veg/adkomst"
    return "Annet"


def summarize_transport_payload(payload: Dict[str, Any], site_polygon: Any) -> Dict[str, Any]:
    features = list(payload.get("features") or [])
    category_counts: Counter = Counter()
    nearest_by_category: Dict[str, float] = {}
    items: List[Dict[str, Any]] = []

    for feature in features:
        distance_m = _feature_distance(feature, site_polygon)
        if distance_m is None or distance_m > 1500:
            continue
        attrs = feature.get("attributes") or {}
        layer_name = _title_or_default(feature.get("layer_name"), "Samferdsel")
        category = _classify_transport_feature(feature)
        name = _pick_attr(attrs, ["navn", "name", "holdeplass", "stasjon", "adresse", "objekt"], ["kommunenavn"])
        label = _title_or_default(name, layer_name)

        category_counts[category] += 1
        nearest_by_category[category] = min(nearest_by_category.get(category, float("inf")), float(distance_m))
        items.append(
            {
                "name": label,
                "layer_name": layer_name,
                "category": category,
                "distance_m": round(float(distance_m), 1),
            }
        )

    items.sort(key=lambda item: (float(item.get("distance_m") or 999999.0), item.get("category", ""), item.get("name", "")))
    transit_within_300 = sum(1 for item in items if item["category"] == "Transit" and float(item["distance_m"]) <= 300.0)
    transit_within_600 = sum(1 for item in items if item["category"] == "Transit" and float(item["distance_m"]) <= 600.0)
    parking_within_300 = sum(1 for item in items if item["category"] == "Parkering" and float(item["distance_m"]) <= 300.0)
    cycle_within_500 = sum(1 for item in items if item["category"] == "Sykkel" and float(item["distance_m"]) <= 500.0)
    road_access = any(item["category"] == "Veg/adkomst" and float(item["distance_m"]) <= 60.0 for item in items)

    mobility_score = 35.0
    mobility_score += min(22.0, transit_within_300 * 9.0)
    mobility_score += min(18.0, max(0, transit_within_600 - transit_within_300) * 4.5)
    mobility_score += min(8.0, parking_within_300 * 2.0)
    mobility_score += min(6.0, cycle_within_500 * 2.0)
    mobility_score += 8.0 if road_access else 0.0
    if transit_within_600 == 0:
        mobility_score -= 7.0
    mobility_score = _clamp(mobility_score, 0.0, 100.0)

    notes: List[str] = []
    if transit_within_300 >= 2:
        notes.append("Tomten har sterk kollektivnaerhet, som taler for tettere boligkonsept og redusert bilavhengighet.")
    elif transit_within_600 >= 1:
        notes.append("Tomten har brukbar kollektivdekning i gangavstand.")
    else:
        notes.append("Kollektivdekningen fremstaer svakere i denne radiusen, noe som trekker vurderingen mot mer bilavhengige konsepter.")
    if road_access:
        notes.append("Samferdselsdata indikerer direkte veg/adkomstnaerhet til tomten.")

    nearest_transit = nearest_by_category.get("Transit")
    nearest_parking = nearest_by_category.get("Parkering")

    return {
        "available": bool(features),
        "source": payload.get("source", "Geodata Online Samferdsel"),
        "service": payload.get("service", ""),
        "feature_count": len(features),
        "category_counts": _counter_to_dict(category_counts),
        "nearest_transit_m": round(float(nearest_transit), 1) if nearest_transit is not None and nearest_transit != float("inf") else None,
        "nearest_parking_m": round(float(nearest_parking), 1) if nearest_parking is not None and nearest_parking != float("inf") else None,
        "transit_within_300_m": transit_within_300,
        "transit_within_600_m": transit_within_600,
        "parking_within_300_m": parking_within_300,
        "cycle_within_500_m": cycle_within_500,
        "road_access": bool(road_access),
        "mobility_score": round(float(mobility_score), 1),
        "items": items[:12],
        "notes": _dedupe_keep_order(notes, limit=5),
        "errors": list(payload.get("errors") or []),
    }


def _compute_typology_adjustments(plan_ctx: Dict[str, Any], project_ctx: Dict[str, Any], transport_ctx: Dict[str, Any]) -> Dict[str, float]:
    mobility = float(transport_ctx.get("mobility_score") or 50.0)
    plan_risk = float(plan_ctx.get("regulatory_risk_score") or 0.0)
    development = float(project_ctx.get("development_pressure_score") or 0.0)

    dense_bonus = (mobility - 50.0) / 10.0
    risk_penalty = plan_risk / 18.0
    urbanity_bonus = min(2.5, development / 24.0)

    adjustments = {
        "Lamell": dense_bonus + (urbanity_bonus * 0.6) - (risk_penalty * 0.8),
        "Punkthus": (dense_bonus * 1.1) + (urbanity_bonus * 0.4) - (risk_penalty * 1.0),
        "Tun": (dense_bonus * 0.45) - (risk_penalty * 0.6) + 0.8,
        "Rekke": (-dense_bonus * 0.25) - (urbanity_bonus * 0.15) - (risk_penalty * 0.25) + 1.8,
    }
    return {key: round(_clamp(value, -8.0, 8.0), 1) for key, value in adjustments.items()}


def build_site_intelligence_bundle(gdo: Any, site_polygon: Any, search_buffer_m: float = 300.0) -> Dict[str, Any]:
    context = gdo.fetch_site_context_bundle(
        site_polygon=site_polygon,
        neighbor_buffer_m=max(250.0, float(search_buffer_m)),
        transport_buffer_m=max(600.0, float(search_buffer_m) * 2.0),
    )

    plan_ctx = summarize_plan_payload(context.get("plan", {}), site_polygon)
    project_ctx = summarize_project_payload(context.get("utbygging", {}), site_polygon)
    transport_ctx = summarize_transport_payload(context.get("transport", {}), site_polygon)

    overall_risk = _clamp((float(plan_ctx.get("regulatory_risk_score") or 0.0) * 0.68) + (float(project_ctx.get("development_pressure_score") or 0.0) * 0.32), 0.0, 100.0)
    opportunity_score = _clamp((float(transport_ctx.get("mobility_score") or 0.0) * 0.58) + ((100.0 - float(plan_ctx.get("regulatory_risk_score") or 0.0)) * 0.22) + (min(float(project_ctx.get("development_pressure_score") or 0.0), 60.0) * 0.20), 0.0, 100.0)
    site_score = _clamp(50.0 + ((opportunity_score - overall_risk) * 0.35), 0.0, 100.0)

    notes = _dedupe_keep_order(
        list(plan_ctx.get("notes") or []) + list(project_ctx.get("notes") or []) + list(transport_ctx.get("notes") or []),
        limit=8,
    )

    return {
        "available": True,
        "plan": plan_ctx,
        "projects": project_ctx,
        "transport": transport_ctx,
        "risk_score": round(float(overall_risk), 1),
        "opportunity_score": round(float(opportunity_score), 1),
        "site_score": round(float(site_score), 1),
        "typology_score_adjustments": _compute_typology_adjustments(plan_ctx, project_ctx, transport_ctx),
        "notes": notes,
        "source_services": [
            item.get("service", "")
            for item in [context.get("plan", {}), context.get("utbygging", {}), context.get("transport", {}), context.get("eiendom", {})]
            if item.get("service")
        ],
        "raw_errors": {
            "plan": plan_ctx.get("errors") or [],
            "projects": project_ctx.get("errors") or [],
            "transport": transport_ctx.get("errors") or [],
        },
    }


def apply_site_intelligence_to_options(options: List[Any], bundle: Dict[str, Any]) -> List[Any]:
    adjustments = dict(bundle.get("typology_score_adjustments") or {})
    if not options or not adjustments:
        return options

    for option in options:
        delta = float(adjustments.get(getattr(option, "typology", ""), 0.0))
        option.score = round(float(getattr(option, "score", 0.0)) + delta, 1)
        if abs(delta) >= 0.4:
            sign = "+" if delta > 0 else ""
            if delta > 0:
                option.notes.append(f"Geodata-kontekst styrker denne typologien ({sign}{delta:.1f} poeng) basert paa plan, mobilitet og omraadeutvikling.")
            else:
                option.notes.append(f"Geodata-kontekst trekker denne typologien noe ned ({sign}{delta:.1f} poeng) basert paa plan- og stedsrisiko.")

    options.sort(key=lambda option: float(getattr(option, "score", 0.0)), reverse=True)
    return options


def build_site_intelligence_markdown(bundle: Dict[str, Any]) -> str:
    if not bundle or not bundle.get("available"):
        return ""

    plan_ctx = bundle.get("plan") or {}
    project_ctx = bundle.get("projects") or {}
    transport_ctx = bundle.get("transport") or {}
    adjustments = bundle.get("typology_score_adjustments") or {}

    lines: List[str] = []
    lines.append("## GEODATA-KONTEKST")
    lines.append(
        f"Site intelligence fra Geodata gir en samlet stedsvurdering paa {bundle.get('site_score', 0):.0f}/100, med mulighetsscore {bundle.get('opportunity_score', 0):.0f}/100 og risikoscore {bundle.get('risk_score', 0):.0f}/100."
    )
    lines.append("")
    lines.append("### REGULERING OG PLAN")
    if plan_ctx.get("plans"):
        lines.append(
            f"Dominerende plankontekst er {plan_ctx.get('dominant_plan', 'ukjent')}. Det er registrert {plan_ctx.get('nearby_plan_count', 0)} relevante planobjekter i eller tett ved tomten."
        )
    else:
        lines.append("Ingen tydelig plankontekst ble identifisert via Geodata i denne kjøringen.")
    if plan_ctx.get("risk_flags"):
        lines.append(f"Plan-/hensynsdata viser signaler om: {', '.join(plan_ctx.get('risk_flags', []))}.")
    for note in plan_ctx.get("notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("### OMRÅDEUTVIKLING")
    if project_ctx.get("nearby_count"):
        lines.append(
            f"Det er registrert {project_ctx.get('nearby_count', 0)} utbyggings- eller byggendringsobjekter innenfor analysert radius, hvorav {project_ctx.get('within_300_m', 0)} ligger innen 300 meter."
        )
    else:
        lines.append("Ingen tydelige utviklingsobjekter ble funnet i nær radius i denne kjøringen.")
    for note in project_ctx.get("notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("### MOBILITET OG ADKOMST")
    if transport_ctx.get("feature_count"):
        nearest_transit = transport_ctx.get("nearest_transit_m")
        if nearest_transit is not None:
            lines.append(
                f"Naermeste identifiserte kollektivpunkt ligger ca. {float(nearest_transit):.0f} meter fra tomten. {transport_ctx.get('transit_within_600_m', 0)} kollektivobjekter ligger innen 600 meter."
            )
        else:
            lines.append("Ingen kollektivpunkt ble identifisert i denne radiusen.")
    for note in transport_ctx.get("notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("### TYPOLOGI-IMPLIKASJONER")
    if adjustments:
        ordered = sorted(adjustments.items(), key=lambda item: item[1], reverse=True)
        best_name, best_delta = ordered[0]
        lines.append(f"Stedsdata favoriserer mest {best_name} i denne runden ({best_delta:+.1f} poeng).")
        for name, delta in ordered:
            lines.append(f"- {name}: {delta:+.1f} poeng")

    return "\n".join(lines)
