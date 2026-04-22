from __future__ import annotations

"""Builtly v8 delivery 4 — deterministic engine with AI hooks.

Passes implemented in this module:
- Pass 1: deterministic delfelt geometry
- Pass 2: parameter selection from locked menus (AI hook + deterministic fallback)
- Pass 3: deterministic volume placement
- Pass 4: deterministic solar analysis
- Pass 5: deterministic MUA/compliance analysis
- Pass 6: narrative/report generation (AI hook + deterministic fallback)

Important design guardrails:
- AI is never allowed to place coordinates or draw footprints.
- Geometry remains deterministic and reproducible.
- Concept alternatives are generated at whole-site level, not by phase.
"""

import json
import logging
import math
import os
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from shapely.geometry import Polygon

from .concept_families import all_concept_families, get_strategy
from .geometry import (
    building_geometry_is_orthogonal_to_field,
    buildings_do_not_overlap,
    orientation_for_field,
    place_buildings_for_fields,
    pca_site_axes,
    resolve_delfelt_count,
    subdivide_buildable_polygon,
)
from .masterplan_types import (
    BarnehageConfig,
    ConceptFamily,
    CourtyardKind,
    Delfelt,
    FieldParameterChoice,
    Masterplan,
    MUAReport,
    PlanRegler,
    ReportNarrative,
    StructuredPassClient,
    Typology,
)
from .mua import calculate_mua
from .sol import compute_sol_report

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = os.environ.get("BUILTLY_CLAUDE_MODEL", "claude-opus-4-7")
FALLBACK_MODEL = os.environ.get("BUILTLY_CLAUDE_FALLBACK_MODEL", "claude-sonnet-4-6")

_PASS1_DEFAULT_FLOORS: Dict[Typology, Tuple[int, int]] = {
    Typology.LAMELL: (4, 6),
    Typology.PUNKTHUS: (5, 6),
    Typology.KARRE: (5, 6),
    Typology.REKKEHUS: (2, 3),
}

_FALLBACK_TITLES: Dict[ConceptFamily, str] = {
    ConceptFamily.LINEAR_MIXED: "Lineært blandet boliggrep",
    ConceptFamily.COURTYARD_URBAN: "Urban kvartalsstruktur med gårdsrom",
    ConceptFamily.CLUSTER_PARK: "Boligklynger rundt grønt fellesrom",
}

_FALLBACK_SUBTITLES: Dict[ConceptFamily, str] = {
    ConceptFamily.LINEAR_MIXED: "Lameller og punkthus organisert langs tomtas hovedakse.",
    ConceptFamily.COURTYARD_URBAN: "Karréer mot kantene og roligere boliger i innsiden.",
    ConceptFamily.CLUSTER_PARK: "Klynger av lameller og punkthus rundt et felles grøntrom.",
}


def _clamp_float(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _safe_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(line for line in cleaned.splitlines() if not line.strip().startswith("```"))
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _should_retry_with_fallback(status_code: Optional[int], body: str) -> bool:
    if status_code == 429:
        return True
    body_l = (body or "").lower()
    model_markers = ("model", "not found", "unknown model", "invalid model", "unsupported model")
    return bool(status_code and status_code >= 400 and any(marker in body_l for marker in model_markers))


def _anthropic_json_call(*, system_prompt: str, user_payload: Dict[str, Any], api_key: Optional[str] = None, model: Optional[str] = None, max_tokens: int = 1600) -> Optional[Dict[str, Any]]:
    enable_flag = str(os.environ.get("BUILTLY_ENABLE_NETWORK_AI", "")).lower()
    if enable_flag not in {"1", "true", "yes"}:
        logger.warning("AI-pass skipped: BUILTLY_ENABLE_NETWORK_AI=%r (må være 1/true/yes)", enable_flag)
        return None
    key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not key:
        logger.warning("AI-pass skipped: ingen API-nøkkel funnet i ANTHROPIC_API_KEY eller CLAUDE_API_KEY")
        return None

    def _post(model_name: str) -> requests.Response:
        return requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model_name,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}],
            },
            timeout=60,
        )

    import time
    primary_model = model or DEFAULT_MODEL
    tried = [primary_model]
    t0 = time.time()
    logger.warning("AI-pass starter: model=%s max_tokens=%s", primary_model, max_tokens)
    try:
        resp = _post(primary_model)
        elapsed = time.time() - t0
        if resp.ok:
            blocks = resp.json().get("content", [])
            text = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")
            result = _safe_json_from_text(text)
            if result is None:
                logger.warning("AI-pass %s returnerte OK men kunne ikke parse JSON (%.1fs). Tekst: %r", primary_model, elapsed, text[:300])
            else:
                logger.warning("AI-pass %s OK (parset JSON, %.1fs)", primary_model, elapsed)
            return result
        body = resp.text
        logger.warning("AI-pass %s HTTP %s (%.1fs) body: %s", primary_model, resp.status_code, elapsed, body[:400])
        if FALLBACK_MODEL and FALLBACK_MODEL != primary_model and _should_retry_with_fallback(resp.status_code, body):
            tried.append(FALLBACK_MODEL)
            logger.warning("Retrying med fallback-modell %s", FALLBACK_MODEL)
            fallback_resp = _post(FALLBACK_MODEL)
            elapsed2 = time.time() - t0
            if fallback_resp.ok:
                blocks = fallback_resp.json().get("content", [])
                text = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")
                result = _safe_json_from_text(text)
                if result is not None:
                    logger.warning("AI-pass %s (fallback) OK (%.1fs total)", FALLBACK_MODEL, elapsed2)
                return result
            logger.warning("Fallback %s HTTP %s body: %s", FALLBACK_MODEL, fallback_resp.status_code, fallback_resp.text[:400])
            return None
        return None
    except Exception as exc:  # pragma: no cover - network dependent
        elapsed = time.time() - t0
        logger.warning("AI-pass exception (tried %s, %.1fs): %s", tried, elapsed, exc)
        return None


# ---------------------------------------------------------------------------
# Pass 1 — site delfelt geometry
# ---------------------------------------------------------------------------


def _seed_neutral_fields(field_polygons: Sequence[Polygon], target_bra_m2: float, fallback_orientation_deg: float) -> List[Delfelt]:
    """Seed delfelter med orientering bestemt av hvert delfelt selv.

    Tidligere brukte vi tomtens PCA-theta som orientering for alle delfelter.
    Det ble feil etter at subdivide_buildable_polygon fikk lov å bytte til
    aksejustert split for konkave tomter (L-former etc.) — delfeltene er da
    aksejusterte selv om tomtens PCA peker diagonalt. Vi kaller derfor
    orientation_for_field for hvert delfelt og faller tilbake til tomtens PCA
    bare hvis helperen ikke kan bestemme noe.
    """
    total_area = sum(p.area for p in field_polygons) or 1.0
    out: List[Delfelt] = []
    for idx, poly in enumerate(field_polygons, start=1):
        share = poly.area / total_area
        orient = orientation_for_field(poly, fallback_deg=fallback_orientation_deg)
        out.append(
            Delfelt(
                field_id=f"DF{idx}",
                polygon=poly.buffer(0),
                typology=Typology.LAMELL,
                orientation_deg=orient,
                floors_min=4,
                floors_max=5,
                target_bra=float(target_bra_m2 * share),
                courtyard_kind=CourtyardKind.FELLES_BOLIG,
                tower_size_m=None,
                phase=idx,
                phase_label=f"Delfelt {idx}",
            )
        )
    return out


def pass1_generate_delfelt(buildable_poly: Polygon, concept_family: ConceptFamily, target_bra_m2: float, requested_count: Optional[int] = None) -> List[Delfelt]:
    del concept_family  # concept is applied in pass 2, not pass 1.
    axes = pca_site_axes(buildable_poly)
    count = resolve_delfelt_count(buildable_poly, requested_count=requested_count)
    polygons = subdivide_buildable_polygon(buildable_poly, count=count, orientation_deg=axes.theta_deg)
    seeded = _seed_neutral_fields(polygons, target_bra_m2, axes.theta_deg)
    return [replace(field, phase=idx, phase_label=f"Delfelt {idx}") for idx, field in enumerate(seeded, start=1)]


def pass1_generate_delfelt_contextual(
    buildable_poly: Polygon,
    concept_family: ConceptFamily,
    target_bra_m2: float,
    site_context: Optional[Any] = None,
    requested_count: Optional[int] = None,
) -> List[Delfelt]:
    """Pass 1 v2 — kontekstuell delfelt-inndeling basert på SiteContext.

    Strategi:
    - Hvis site_context har 2+ armer, bruker vi armene som grunndelfelt
    - Store armer (>6000 m²) deles videre i 2 delfelt langs sin egen akse
    - Små armer er 1 delfelt (rekkehus passer ofte her)
    - Hvert delfelt får en FieldCharacter basert på armens retning og
      nabolaget langs armens kanter
    - Fallback: hvis pass 0 feilet eller tomten er rektangulær, bruker vi
      gammel pass1_generate_delfelt

    Returnerer delfelter med arm_id og character fylt ut.
    """
    del concept_family  # concept brukes i pass 2
    from .site_analysis import _cardinal_from_bearing

    # Fallback til gammel logikk hvis ikke kontekstdata eller enkel tomt
    if site_context is None or not site_context.has_arms:
        return pass1_generate_delfelt(buildable_poly, concept_family,
                                       target_bra_m2, requested_count)

    arms = site_context.arms
    total_arm_area = sum(a.area_m2 for a in arms) or 1.0

    # Inndeling: hver arm blir 1-2 delfelt avhengig av størrelse
    field_polys_with_meta: List[Tuple[Polygon, str, str, float]] = []
    # tuple: (polygon, arm_id, character, target_bra_share)

    for arm in arms:
        arm_share = arm.area_m2 / total_arm_area
        arm_target = target_bra_m2 * arm_share
        arm_poly = arm.polygon

        if arm_poly is None:
            continue

        # Bestem karakter basert på armens retning fra tomtens senter
        card = _cardinal_from_bearing(arm.bearing_from_site_center_deg)
        # Sør-arm: street_facing (typisk hovedadkomst)
        # Nord-arm: sheltered (innvendig)
        # Øst/vest: neighborhood_edge
        character_map = {
            "south": "street_facing",
            "southeast": "street_facing",
            "southwest": "street_facing",
            "north": "sheltered",
            "northeast": "neighborhood_edge",
            "northwest": "neighborhood_edge",
            "east": "neighborhood_edge",
            "west": "neighborhood_edge",
        }
        character = character_map.get(card, "sheltered")

        # Splitt store armer i 2 delfelt langs armens egen akse
        if arm.area_m2 > 6000 and arm.aspect_ratio > 1.4:
            sub_polys = subdivide_buildable_polygon(
                arm_poly, count=2, orientation_deg=arm.dominant_axis_deg
            )
            for i, sub in enumerate(sub_polys):
                share = sub.area / arm.area_m2
                field_polys_with_meta.append(
                    (sub.buffer(0), arm.arm_id, character, arm_target * share)
                )
        else:
            # Små armer er 1 delfelt
            field_polys_with_meta.append(
                (arm_poly.buffer(0), arm.arm_id, character, arm_target)
            )

    # Bygg Delfelt-objekter
    out: List[Delfelt] = []
    for idx, (poly, arm_id, character, target) in enumerate(field_polys_with_meta, start=1):
        orient = orientation_for_field(poly, fallback_deg=0.0)
        # Beregn max nabohøyde for dette feltet ved å finne kantene som ligger
        # nær feltets yttergrense og lese av deres avg_neighbor_height_m.
        max_neighbor_h = _estimate_max_neighbor_height(poly, site_context)
        out.append(Delfelt(
            field_id=f"DF{idx}",
            polygon=poly,
            typology=Typology.LAMELL,  # pass 2 setter riktig typologi
            orientation_deg=orient,
            floors_min=4,
            floors_max=5,
            target_bra=float(target),
            courtyard_kind=CourtyardKind.FELLES_BOLIG,
            tower_size_m=None,
            phase=idx,
            phase_label=f"Delfelt {idx} ({arm_id})",
            arm_id=arm_id,
            character=character,
            max_neighbor_height_m=max_neighbor_h,
        ))
    return out


def _estimate_max_neighbor_height(field_poly: Polygon, site_context: Any,
                                  buffer_m: float = 30.0) -> Optional[float]:
    """Finn nabohøyden som feltet "ser" mot — gjennomsnitt av nabohøyder
    på kanter som ligger nær dette feltets yttergrense.

    Raskt: itererer kun over kanter med ikke-None avg_neighbor_height_m.
    """
    if site_context is None or not site_context.edges:
        return None
    try:
        # Filtrer først ut kanter uten høyde — unngå unødvendig buffer-beregning
        heights_candidates = [e for e in site_context.edges
                              if e.avg_neighbor_height_m is not None]
        if not heights_candidates:
            return None
        from shapely.geometry import LineString
        field_boundary_buf = field_poly.boundary.buffer(buffer_m)
        heights: List[float] = []
        for edge in heights_candidates:
            edge_line = LineString([edge.p0, edge.p1])
            if field_boundary_buf.intersects(edge_line):
                heights.append(edge.avg_neighbor_height_m)
        if heights:
            return sum(heights) / len(heights)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Pass 2 — parameter selection
# ---------------------------------------------------------------------------


def _apply_parameter_choices(base_fields: Sequence[Delfelt], choices: Sequence[FieldParameterChoice]) -> List[Delfelt]:
    choice_by_id = {c.field_id: c for c in choices}
    out: List[Delfelt] = []
    for field in base_fields:
        choice = choice_by_id.get(field.field_id)
        if choice is None:
            out.append(field)
            continue
        out.append(
            replace(
                field,
                typology=choice.typology,
                orientation_deg=float(choice.orientation_deg),
                floors_min=int(choice.floors_min),
                floors_max=int(choice.floors_max),
                target_bra=float(choice.target_bra),
                courtyard_kind=choice.courtyard_kind,
                tower_size_m=choice.tower_size_m,
            )
        )
    return out


def _normalize_choices(
    concept_family: ConceptFamily,
    base_fields: Sequence[Delfelt],
    fallback_choices: Sequence[FieldParameterChoice],
    raw_data: Optional[Dict[str, Any]],
    target_bra_m2: float,
    plan_regler: Optional[PlanRegler],
) -> List[FieldParameterChoice]:
    strategy = get_strategy(concept_family)
    fallback_by_id = {c.field_id: c for c in fallback_choices}
    allowed_by_id = {
        field.field_id: strategy.envelope_for_field(idx, len(base_fields))
        for idx, field in enumerate(base_fields)
    }

    if not raw_data or raw_data.get("concept_family") != concept_family.value:
        return list(fallback_choices)

    raw_fields = raw_data.get("fields")
    if not isinstance(raw_fields, list):
        return list(fallback_choices)

    normalized: List[FieldParameterChoice] = []
    total_requested = 0.0
    for raw_field in raw_fields:
        if not isinstance(raw_field, dict):
            continue
        field_id = str(raw_field.get("field_id", ""))
        if field_id not in fallback_by_id:
            continue
        fb = fallback_by_id[field_id]
        envelope = allowed_by_id[field_id]

        typology_raw = raw_field.get("typology", fb.typology.value)
        try:
            parsed_typology = Typology(str(typology_raw))
        except Exception:
            parsed_typology = fb.typology
        if parsed_typology not in envelope.allowed_typologies:
            parsed_typology = envelope.default_typology

        base_angles = [fb.orientation_deg % 180.0, (fb.orientation_deg + 90.0) % 180.0]
        raw_orientation = float(raw_field.get("orientation_deg", fb.orientation_deg)) % 180.0
        snapped_orientation = min(base_angles, key=lambda a: min(abs(a - raw_orientation), 180.0 - abs(a - raw_orientation)))

        floors_min = int(raw_field.get("floors_min", fb.floors_min))
        floors_max = int(raw_field.get("floors_max", fb.floors_max))
        floors_min = max(1, floors_min)
        floors_max = max(floors_min, floors_max)
        if plan_regler and plan_regler.max_floors is not None:
            floors_max = min(floors_max, int(plan_regler.max_floors))
            floors_min = min(floors_min, floors_max)

        courtyard_kind_raw = raw_field.get("courtyard_kind")
        try:
            courtyard_kind = CourtyardKind(courtyard_kind_raw) if courtyard_kind_raw else fb.courtyard_kind
        except Exception:
            courtyard_kind = fb.courtyard_kind

        tower_size_m = raw_field.get("tower_size_m", fb.tower_size_m)
        if parsed_typology != Typology.PUNKTHUS or tower_size_m not in {17, 21}:
            tower_size_m = fb.tower_size_m if parsed_typology == Typology.PUNKTHUS else None

        target_bra = float(raw_field.get("target_bra", fb.target_bra) or 0.0)
        if target_bra <= 0:
            target_bra = fb.target_bra
        total_requested += target_bra

        normalized.append(
            FieldParameterChoice(
                field_id=field_id,
                typology=parsed_typology,
                orientation_deg=snapped_orientation,
                floors_min=floors_min,
                floors_max=floors_max,
                target_bra=target_bra,
                courtyard_kind=courtyard_kind,
                tower_size_m=tower_size_m,
                rationale=str(raw_field.get("rationale", "") or fb.rationale),
            )
        )

    if len(normalized) != len(base_fields) or total_requested <= 0:
        return list(fallback_choices)

    # Re-normalize target BRA to preserve deterministic total target.
    scale = target_bra_m2 / total_requested
    normalized = [replace(choice, target_bra=float(choice.target_bra * scale)) for choice in normalized]
    normalized.sort(key=lambda c: c.field_id)
    return normalized


def pass2_select_field_parameters(
    concept_family: ConceptFamily,
    base_fields: Sequence[Delfelt],
    target_bra_m2: float,
    plan_regler: Optional[PlanRegler] = None,
    neighbors: Optional[Sequence[dict]] = None,
    ai_selector: Optional[StructuredPassClient] = None,
) -> Tuple[List[Delfelt], str]:
    strategy = get_strategy(concept_family)
    fallback_choices = strategy.propose(base_fields, target_bra_m2, plan_regler=plan_regler, neighbors=neighbors)

    payload = {
        "concept_family": concept_family.value,
        "fields": [field.to_dict() for field in base_fields],
        "fallback_choices": [
            {
                "field_id": c.field_id,
                "typology": c.typology.value,
                "orientation_deg": c.orientation_deg,
                "floors_min": c.floors_min,
                "floors_max": c.floors_max,
                "target_bra": c.target_bra,
                "courtyard_kind": c.courtyard_kind.value if c.courtyard_kind else None,
                "tower_size_m": c.tower_size_m,
                "rationale": c.rationale,
            }
            for c in fallback_choices
        ],
        "allowed_envelopes": {
            field.field_id: {
                "allowed_typologies": [typ.value for typ in strategy.envelope_for_field(idx, len(base_fields)).allowed_typologies],
                "allowed_angles": [field.orientation_deg % 180.0, (field.orientation_deg + 90.0) % 180.0],
                "allowed_tower_sizes_m": [17, 21],
            }
            for idx, field in enumerate(base_fields)
        },
        "target_bra_m2": target_bra_m2,
        "plan_regler": {
            "max_floors": plan_regler.max_floors if plan_regler else None,
            "max_height_m": plan_regler.max_height_m if plan_regler else None,
        },
    }

    raw_data: Optional[Dict[str, Any]] = None
    source = "fallback"
    if ai_selector is not None:
        try:
            raw_data = ai_selector(payload)
            if raw_data:
                source = "ai_selector"
        except Exception as exc:
            logger.warning("pass2 ai_selector failed: %s", exc)
            raw_data = None
    if raw_data is None:
        raw_data = _anthropic_json_call(
            system_prompt=(
                "You are pass 2 in a deterministic masterplan engine. "
                "Return JSON only. Never generate coordinates. Never invent new field ids. "
                "Pick only from the allowed typologies/angles/tower sizes."
            ),
            user_payload=payload,
        )
        if raw_data:
            source = "anthropic"

    normalized_choices = _normalize_choices(concept_family, base_fields, fallback_choices, raw_data, target_bra_m2, plan_regler)
    configured_fields = _apply_parameter_choices(base_fields, normalized_choices)
    return configured_fields, source


# ---------------------------------------------------------------------------
# Pass 3 AI — design directives (NEW, optional layer before geometry)
# ---------------------------------------------------------------------------


_DESIGN_VARIANT_ALLOWED = {"single", "varied", "rotated", "terraced"}
_DESIGN_KARRE_SHAPE_ALLOWED = {"uo", "uo_chamfered", "l", "t", "z"}
_DESIGN_HEIGHT_PATTERN_ALLOWED = {"uniform", "accent", "stepped", "paired"}


def _polygon_to_compact_dict(poly: Polygon) -> Dict[str, Any]:
    """Reduser en polygon til et kompakt sammendrag AI kan resonnere over uten
    koordinatstøy. Returnerer bounding-box, areal og sideforhold."""
    if poly is None or poly.is_empty:
        return {}
    minx, miny, maxx, maxy = poly.bounds
    w = maxx - minx
    h = maxy - miny
    return {
        "width_m": round(w, 1),
        "height_m": round(h, 1),
        "area_m2": round(poly.area, 0),
        "aspect_ratio": round(max(w, h) / max(1e-6, min(w, h)), 2),
    }


def pass3_design_directives(
    base_fields: Sequence[Delfelt],
    *,
    buildable_poly: Optional[Polygon] = None,
    latitude_deg: Optional[float] = None,
    target_bra_m2: Optional[float] = None,
    site_context: Optional[Any] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[List[Delfelt], str]:
    """AI-pass som berikger delfeltene med arkitektoniske designdirektiv.

    Direktivene styrer hvordan motoren velger varianter (single/varied/rotert
    lamell, U/O/L/T/Z karré, høydegradient for punkthus). AI får:
    - Delfelt-sammendrag (form, størrelse, typologi, target_bra, orientering)
    - Total tomtekontekst (bredde/lengde/areal)
    - Latitude for solvinkel

    AI returnerer JSON med design_* felt per delfelt. Hvis AI feiler/ikke er
    tilgjengelig, beholder vi feltene uendret (motoren bruker sin egen default).

    Returnerer (berikede delfelter, kilde-tag).
    """
    if not base_fields:
        return list(base_fields), "empty"

    # Bygg payload til AI
    field_payload = []
    for f in base_fields:
        geom = _polygon_to_compact_dict(f.polygon)
        entry = {
            "field_id": f.field_id,
            "typology": f.typology.value,
            "orientation_deg": round(f.orientation_deg, 1),
            "floors_min": f.floors_min,
            "floors_max": f.floors_max,
            "target_bra": round(f.target_bra, 0),
            "phase": f.phase,
            "geometry": geom,
        }
        # Legg til kontekst fra pass 0 hvis tilgjengelig
        if f.arm_id is not None:
            entry["arm_id"] = f.arm_id
        if f.character is not None:
            entry["character"] = f.character
        field_payload.append(entry)

    site_payload = _polygon_to_compact_dict(buildable_poly) if buildable_poly is not None else {}

    # Utvid site_payload med SiteContext-informasjon hvis tilgjengelig
    if site_context is not None:
        site_payload["label"] = site_context.site_label
        site_payload["has_arms"] = site_context.has_arms
        site_payload["n_arms"] = len(site_context.arms)
        site_payload["dominant_axis_deg"] = round(site_context.site_bearing_deg, 1)
        if site_context.dominant_view_direction:
            site_payload["dominant_view_direction"] = site_context.dominant_view_direction
        # Armer som kompakt liste
        if site_context.arms:
            site_payload["arms"] = [
                {
                    "arm_id": a.arm_id,
                    "area_m2": round(a.area_m2, 0),
                    "bearing_from_center_deg": round(a.bearing_from_site_center_deg, 0),
                    "dominant_axis_deg": round(a.dominant_axis_deg, 0),
                    "aspect_ratio": round(a.aspect_ratio, 1),
                }
                for a in site_context.arms
            ]
        # Edges som sammendrag (hvilke retninger har naboer/åpning)
        if site_context.edges:
            edge_summary = {"neighbor_dense": [], "neighbor_sparse": [], "open": []}
            from .site_analysis import _cardinal_from_bearing
            for e in site_context.edges:
                card = _cardinal_from_bearing(e.outward_bearing_deg)
                key = e.character.value if hasattr(e.character, 'value') else str(e.character)
                if key in edge_summary:
                    if card not in edge_summary[key]:
                        edge_summary[key].append(card)
            site_payload["edge_summary"] = edge_summary

    payload = {
        "task": "assign_design_directives",
        "schema_version": 1,
        "allowed": {
            "design_variant": sorted(list(_DESIGN_VARIANT_ALLOWED)),
            "design_karre_shape": sorted(list(_DESIGN_KARRE_SHAPE_ALLOWED)),
            "design_height_pattern": sorted(list(_DESIGN_HEIGHT_PATTERN_ALLOWED)),
            "design_rotation_deg_range": [-15, 15],
        },
        "site": site_payload,
        "latitude_deg": latitude_deg,
        "target_bra_m2": target_bra_m2,
        "fields": field_payload,
        "response_format": {
            "df_directives": {
                "<field_id>": {
                    "design_variant": "single|varied|rotated (only for LAMELL)",
                    "design_karre_shape": "uo|l|t|z (only for KARRE)",
                    "design_height_pattern": "uniform|accent|stepped|paired (only for PUNKTHUS)",
                    "design_rotation_deg": "number in [-15, 15] or null",
                    "design_reasoning": "short string, one sentence"
                }
            },
            "global_reasoning": "one paragraph"
        },
    }

    system_prompt = (
        "You are the design-directive AI for a Nordic masterplan engine. "
        "For each delfelt you assign design variants that the deterministic "
        "geometry engine will realize. Never invent field ids. Never generate "
        "coordinates. Stay within the 'allowed' lists in the payload. "
        "\n\n"
        "SITE CONTEXT: The 'site' object may contain 'arms' (L/U/T-shaped sites "
        "are decomposed into arms), an 'edge_summary' (which compass directions "
        "have dense neighbors vs open views), and 'label' (rectangle/narrow_rectangle/"
        "multi_arm_N/irregular). Each field may have 'arm_id' and 'character': "
        "(a) 'street_facing' fields should be ARCHITECTURALLY MARKANT — prefer "
        "uo_chamfered karré or terraced lamell with a subtle rotation toward the "
        "dominant access direction. "
        "(b) 'sheltered' fields (inner/enclosed) should be CALM — prefer single "
        "lamell or plain uo karré with modest variation. "
        "(c) 'neighborhood_edge' fields should RESPOND TO SCALE — avoid rotations "
        "and L-shapes that could feel aggressive toward neighbors. Keep heights "
        "uniform or stepping DOWN toward the neighbor side. "
        "(d) 'open_view' fields can use taller punkthus or height_pattern='accent' "
        "to exploit the view. "
        "\n\n"
        "CRITICAL CONSTRAINTS: Volume (BRA) matters as much as design. "
        "The following variants typically COST BRA on constrained fields: "
        "(1) Karré shape='l' costs ~30% BRA vs 'uo' — use only on fields too "
        "narrow for U/O, or when site context demands it. "
        "(2) Lamell variant='varied' splits rows into shorter buildings and "
        "can cost 10-25% BRA on narrow fields — only use on fields wider than "
        "120m where variation is architecturally meaningful. "
        "(3) Lamell variant='rotated' needs extra spacing buffer — on tight "
        "fields the rotation won't fit and the field falls back to fewer "
        "buildings. Only use on fields with lots of space. "
        "\n\n"
        "LOW-COST VARIANTS (use these for architectural variation without BRA loss): "
        "(A) Karré shape='uo_chamfered' — U/O with one corner chamfered at 45°. "
        "Loses only ~2% BRA but adds a strong architectural gesture, perfect "
        "for fields facing a street intersection or public space. "
        "(B) Lamell variant='terraced' — same footprints as 'single' but with "
        "stepped heights (e.g. 5-6-7 floors or 7-6-5). Zero BRA loss, strong "
        "visual rhythm. Use liberally on fields with 2+ lamell rows. "
        "\n\n"
        "STRATEGY: Default to 'terraced' for Lamell (good variation, no cost) "
        "and 'uo_chamfered' for Karré on prominent street-facing fields. Only "
        "use 'single' or 'uo' if the field is too small for rhythm (1-bygg field). "
        "Use 'varied'/'rotated'/'l'/'t'/'z' only on genuinely large fields "
        "(site_area > 3000 m²) where architectural eccentricity is justified. "
        "\n\n"
        "Nordic sun rules: prefer east-west lamell orientations (90° angle_offset). "
        "Cluster punkthus rather than line them up (use height_pattern='accent'). "
        "Keep rotations small (±3–6°) and only on one or two fields per concept. "
        "Return JSON only."
    )

    raw = _anthropic_json_call(
        system_prompt=system_prompt,
        user_payload=payload,
        api_key=api_key,
        model=model,
        max_tokens=2400,
    )
    source = "anthropic_pass3" if raw else "fallback"

    if not raw or "df_directives" not in raw:
        logger.warning("Pass 3 design AI did not return directives — keeping motor defaults.")
        return list(base_fields), source

    directives = raw.get("df_directives") or {}
    if not isinstance(directives, dict):
        logger.warning("Pass 3 design AI returned non-dict df_directives — ignoring.")
        return list(base_fields), "fallback"

    enriched: List[Delfelt] = []
    applied_count = 0
    for f in base_fields:
        dct = directives.get(f.field_id) or {}
        if not isinstance(dct, dict):
            enriched.append(f)
            continue

        # Sanitize alle felt
        variant = dct.get("design_variant")
        if variant is not None and variant not in _DESIGN_VARIANT_ALLOWED:
            variant = None

        karre_shape = dct.get("design_karre_shape")
        if karre_shape is not None and karre_shape not in _DESIGN_KARRE_SHAPE_ALLOWED:
            karre_shape = None

        hp = dct.get("design_height_pattern")
        if hp is not None and hp not in _DESIGN_HEIGHT_PATTERN_ALLOWED:
            hp = None

        rot = dct.get("design_rotation_deg")
        try:
            rot_val: Optional[float] = float(rot) if rot is not None else None
            if rot_val is not None:
                rot_val = max(-15.0, min(15.0, rot_val))
        except (TypeError, ValueError):
            rot_val = None

        reasoning = dct.get("design_reasoning")
        reasoning_str = str(reasoning)[:300] if reasoning else None

        # Kun anvend direktiv som matcher typologi
        if f.typology == Typology.LAMELL:
            applied_variant = variant
            applied_shape = None
            applied_hp = None
        elif f.typology == Typology.KARRE:
            applied_variant = None
            applied_shape = karre_shape
            applied_hp = None
        elif f.typology == Typology.PUNKTHUS:
            applied_variant = None
            applied_shape = None
            applied_hp = hp
        else:
            applied_variant = None
            applied_shape = None
            applied_hp = None

        new_field = Delfelt(
            field_id=f.field_id,
            polygon=f.polygon,
            typology=f.typology,
            orientation_deg=f.orientation_deg,
            floors_min=f.floors_min,
            floors_max=f.floors_max,
            target_bra=f.target_bra,
            courtyard_kind=f.courtyard_kind,
            tower_size_m=f.tower_size_m,
            phase=f.phase,
            phase_label=f.phase_label,
            design_variant=applied_variant,
            design_karre_shape=applied_shape,
            design_height_pattern=applied_hp,
            design_rotation_deg=rot_val,
            design_reasoning=reasoning_str,
        )
        enriched.append(new_field)
        if applied_variant or applied_shape or applied_hp or rot_val is not None:
            applied_count += 1

    logger.warning(
        "Pass 3 design directives: %d/%d fields got AI-specific direction (source=%s).",
        applied_count, len(base_fields), source,
    )
    return enriched, source


# ---------------------------------------------------------------------------
# Pass 3 / 4 / 5 — deterministic core
# ---------------------------------------------------------------------------


def pass3_place_buildings(buildable_poly: Polygon, delfelt: Sequence[Delfelt], plan_regler: Optional[PlanRegler] = None, barnehage_config: Optional[BarnehageConfig] = None):
    del barnehage_config
    return place_buildings_for_fields(buildable_poly, list(delfelt), plan_regler=plan_regler)


def pass4_calculate_solar(*, buildings, buildable_poly: Polygon, latitude_deg: float, longitude_deg: float, neighbors: Optional[Sequence[dict]] = None, year: Optional[int] = None, rules: Optional[PlanRegler] = None):
    return compute_sol_report(buildable_poly, list(buildings), latitude_deg=latitude_deg, longitude_deg=longitude_deg, neighbors=neighbors, rules=rules, year=(year if year is not None else 2026))


def pass5_calculate_mua(plan: Masterplan, plan_regler: Optional[PlanRegler] = None) -> MUAReport:
    return calculate_mua(plan, regler=plan_regler or plan.plan_regler)


# ---------------------------------------------------------------------------
# Pass 6 — narrative/report generation
# ---------------------------------------------------------------------------


def _fallback_narrative(plan: Masterplan, target_bra_m2: float) -> ReportNarrative:
    fit_pct = 100.0 * plan.total_bra_m2 / max(target_bra_m2, 1.0)
    typology_mix = sorted({b.typology.value for b in plan.bygg})
    mua_state = "oppfyller" if plan.mua_report.compliant else "har avklaringspunkter i"
    title = _FALLBACK_TITLES[plan.concept_family]
    summary = (
        f"{title} fordeler bebyggelsen i {len(plan.delfelt)} delfelt med "
        f"{', '.join(typology_mix)} som bærende typologier. "
        f"Planen oppnår ca. {fit_pct:.0f}% av mål-BRA, estimerer {plan.antall_boliger} boliger "
        f"og har solscore {plan.sol_report.total_sol_score:.0f}/100."
    )
    arch = (
        f"Grepet leses som {plan.concept_family.value} med tydelig feltorientering og "
        f"deterministisk, ortogonal geometri. Planen {mua_state} MUA-kravene og "
        f"har BRA-avvik på {plan.bra_deficit:.0f} m²."
    )
    recommendation = (
        "Anbefales som utgangspunkt hvis utvikler ønsker et reproduserbart konseptgrep "
        "som senere kan fases og detaljutvikles videre uten å miste strukturen."
    )
    risks: List[str] = []
    if plan.bra_deficit > 0:
        risks.append(f"BRA-mål nås ikke fullt ut; underskudd ca. {plan.bra_deficit:.0f} m².")
    if not plan.mua_report.compliant:
        risks.append("MUA/compliance har ett eller flere åpne punkter som må verifiseres videre.")
    if plan.sol_report.total_sol_score < 40:
        risks.append("Solforholdene er svake og bør forbedres med orientering, avstand eller høydereduksjon.")
    return ReportNarrative(title=title, summary=summary, architectural_assessment=arch, recommendation=recommendation, risks=risks, source="fallback")


def pass6_generate_report_text(plan: Masterplan, target_bra_m2: float, ai_reporter: Optional[StructuredPassClient] = None) -> ReportNarrative:
    payload = {
        "concept_family": plan.concept_family.value,
        "display_title_default": _FALLBACK_TITLES[plan.concept_family],
        "display_subtitle_default": _FALLBACK_SUBTITLES[plan.concept_family],
        "metrics": {
            "target_bra_m2": target_bra_m2,
            "total_bra_m2": plan.total_bra_m2,
            "total_bya_m2": plan.total_bya_m2,
            "antall_boliger": plan.antall_boliger,
            "sol_score": plan.sol_report.total_sol_score,
            "mua_compliant": plan.mua_report.compliant,
            "bra_deficit": plan.bra_deficit,
            "field_count": len(plan.delfelt),
            "typology_mix": sorted({b.typology.value for b in plan.bygg}),
        },
        "requirements": {
            "return_json_only": True,
            "keys": ["title", "summary", "architectural_assessment", "recommendation", "risks"],
        },
    }

    raw_data: Optional[Dict[str, Any]] = None
    source = "fallback"
    if ai_reporter is not None:
        try:
            raw_data = ai_reporter(payload)
            if raw_data:
                source = "ai_reporter"
        except Exception as exc:
            logger.warning("pass6 ai_reporter failed: %s", exc)
    if raw_data is None:
        raw_data = _anthropic_json_call(
            system_prompt=(
                "You are pass 6 in a deterministic feasibility engine. "
                "Write concise Norwegian report text based only on provided metrics. "
                "Return JSON only with keys title, summary, architectural_assessment, recommendation, risks."
            ),
            user_payload=payload,
            max_tokens=1200,
        )
        if raw_data:
            source = "anthropic"

    fallback = _fallback_narrative(plan, target_bra_m2)
    if not raw_data:
        return fallback
    title = str(raw_data.get("title") or fallback.title).strip() or fallback.title
    summary = str(raw_data.get("summary") or fallback.summary).strip() or fallback.summary
    arch = str(raw_data.get("architectural_assessment") or fallback.architectural_assessment).strip() or fallback.architectural_assessment
    rec = str(raw_data.get("recommendation") or fallback.recommendation).strip() or fallback.recommendation
    risks_raw = raw_data.get("risks")
    risks = [str(item).strip() for item in risks_raw if str(item).strip()] if isinstance(risks_raw, list) else fallback.risks
    return ReportNarrative(title=title, summary=summary, architectural_assessment=arch, recommendation=rec, risks=risks, source=source)


# ---------------------------------------------------------------------------
# Whole-concept planning
# ---------------------------------------------------------------------------


def plan_masterplan_geometry(
    buildable_poly: Polygon,
    *,
    concept_family: ConceptFamily = ConceptFamily.LINEAR_MIXED,
    target_bra_m2: float = 0.0,
    plan_regler: Optional[PlanRegler] = None,
    requested_delfelt_count: Optional[int] = None,
    avg_unit_bra_m2: float = 55.0,
    barnehage_config: Optional[BarnehageConfig] = None,
    latitude_deg: float = 63.42,
    longitude_deg: float = 10.43,
    neighbor_buildings: Optional[Sequence[dict]] = None,
    solar_year: Optional[int] = None,
    parkering_areal: float = 0.0,
    vei_areal: float = 0.0,
    site_area_m2: Optional[float] = None,
    ai_selector: Optional[StructuredPassClient] = None,
    ai_reporter: Optional[StructuredPassClient] = None,
) -> Masterplan:
    if buildable_poly is None or buildable_poly.is_empty:
        raise ValueError("buildable_poly mangler eller er tom")
    rules = plan_regler or PlanRegler()

    # Pass 0: Analyser tomten — finn armer, kanter, naboskap
    # SiteContext brukes fra uke 2 og fremover av pass 1 og AI-pass 3.
    try:
        import time as _time
        from .site_analysis import analyze_site
        _t0 = _time.perf_counter()
        site_context = analyze_site(buildable_poly, neighbor_buildings=neighbor_buildings)
        _elapsed_ms = (_time.perf_counter() - _t0) * 1000
        logger.warning(
            "Pass 0 site analysis: label=%s arms=%d edges=%d rectangular=%s view=%s (%.0fms, %d naboer)",
            site_context.site_label,
            len(site_context.arms),
            len(site_context.edges),
            site_context.is_rectangular,
            site_context.dominant_view_direction,
            _elapsed_ms,
            len(neighbor_buildings) if neighbor_buildings else 0,
        )
    except Exception as exc:
        logger.warning("Pass 0 site analysis failed: %s", exc)
        site_context = None

    # Pass 1: Delfelt-inndeling.
    # Hvis site_context har armer (L/U/kompleks), bruker vi kontekstuell
    # inndeling som snitter etter armene i stedet for generiske bånd.
    # Ellers faller vi tilbake til gammel pass1_generate_delfelt.
    if site_context is not None and site_context.has_arms:
        base_fields = pass1_generate_delfelt_contextual(
            buildable_poly, concept_family, target_bra_m2,
            site_context=site_context,
            requested_count=requested_delfelt_count,
        )
        logger.warning(
            "Pass 1 contextual: %d fields from %d arms",
            len(base_fields), len(site_context.arms),
        )
    else:
        base_fields = pass1_generate_delfelt(
            buildable_poly, concept_family, target_bra_m2, requested_delfelt_count
        )
    configured_fields, pass2_source = pass2_select_field_parameters(
        concept_family,
        base_fields,
        target_bra_m2,
        plan_regler=rules,
        neighbors=neighbor_buildings,
        ai_selector=ai_selector,
    )

    # AI-pass 3 design-direktiv: beriker delfelter med arkitektoniske
    # variant-direktiver (rotert/varied lamell, L/T/Z karré, høydegradient).
    # Motoren bruker disse som sterk preferanse i plassering. Hvis AI ikke er
    # tilgjengelig eller returnerer ugyldige direktiv, beholder vi konfigurerte
    # felt som de er — motoren faller da tilbake til sine default-valg.
    try:
        enriched_fields, pass3_design_source = pass3_design_directives(
            configured_fields,
            buildable_poly=buildable_poly,
            latitude_deg=latitude_deg,
            target_bra_m2=target_bra_m2,
            site_context=site_context,  # ny: gir kontekst til AI
        )
    except Exception as exc:
        logger.warning("pass3_design_directives failed, keeping unmodified fields: %s", exc)
        enriched_fields = configured_fields
        pass3_design_source = "error"

    buildings, bra_deficit = pass3_place_buildings(buildable_poly, enriched_fields, rules, barnehage_config)
    total_bra = sum(b.bra_m2 for b in buildings)
    total_bya = sum(b.footprint_m2 for b in buildings)
    units = int(round(total_bra / avg_unit_bra_m2)) if avg_unit_bra_m2 > 0 else 0
    sol_report = pass4_calculate_solar(
        buildings=buildings,
        buildable_poly=buildable_poly,
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        neighbors=neighbor_buildings,
        year=solar_year,
        rules=rules,
    )
    plan = Masterplan(
        concept_family=concept_family,
        delfelt=list(enriched_fields),
        bygg=list(buildings),
        sol_report=sol_report,
        mua_report=MUAReport(),
        total_bra_m2=float(total_bra),
        total_bya_m2=float(total_bya),
        antall_boliger=max(0, units),
        display_title=_FALLBACK_TITLES[concept_family],
        display_subtitle=_FALLBACK_SUBTITLES[concept_family],
        buildable_polygon=buildable_poly.buffer(0),
        site_area_m2=float(site_area_m2 or buildable_poly.area),
        parkering_areal=float(parkering_areal),
        vei_areal=float(vei_areal),
        plan_regler=rules,
        barnehage_config=barnehage_config or BarnehageConfig(),
        bra_deficit=float(bra_deficit),
        latitude_deg=float(latitude_deg),
        longitude_deg=float(longitude_deg),
        pass2_source=pass2_source,
    )
    plan.mua_report = pass5_calculate_mua(plan, rules)
    narrative = pass6_generate_report_text(plan, target_bra_m2, ai_reporter=ai_reporter)
    plan.display_title = narrative.title
    plan.display_subtitle = _FALLBACK_SUBTITLES[concept_family]
    plan.report_summary = narrative.summary
    plan.report_architectural_assessment = narrative.architectural_assessment
    plan.report_recommendation = narrative.recommendation
    plan.report_risks = list(narrative.risks)
    plan.pass6_source = narrative.source
    return plan


def generate_concept_masterplans(
    buildable_poly: Polygon,
    *,
    target_bra_m2: float,
    plan_regler: Optional[PlanRegler] = None,
    requested_delfelt_count: Optional[int] = None,
    avg_unit_bra_m2: float = 55.0,
    barnehage_config: Optional[BarnehageConfig] = None,
    latitude_deg: float = 63.42,
    longitude_deg: float = 10.43,
    neighbor_buildings: Optional[Sequence[dict]] = None,
    solar_year: Optional[int] = None,
    parkering_areal: float = 0.0,
    vei_areal: float = 0.0,
    site_area_m2: Optional[float] = None,
    ai_selector: Optional[StructuredPassClient] = None,
    ai_reporter: Optional[StructuredPassClient] = None,
) -> List[Masterplan]:
    """Generer masterplan for alle konsept-familier.

    Kjører konseptene PARALLELT via ThreadPoolExecutor siden hvert konsept
    er uavhengig og består av flere HTTP-kall til AI. Dette reduserer
    total wallclock fra ~45s (sekvensielt) til ~15s (parallelt) på Render.
    """
    import concurrent.futures
    import time

    families = list(all_concept_families())

    def _run_one(family):
        t0 = time.time()
        try:
            result = plan_masterplan_geometry(
                buildable_poly,
                concept_family=family,
                target_bra_m2=target_bra_m2,
                plan_regler=plan_regler,
                requested_delfelt_count=requested_delfelt_count,
                avg_unit_bra_m2=avg_unit_bra_m2,
                barnehage_config=barnehage_config,
                latitude_deg=latitude_deg,
                longitude_deg=longitude_deg,
                neighbor_buildings=neighbor_buildings,
                solar_year=solar_year,
                parkering_areal=parkering_areal,
                vei_areal=vei_areal,
                site_area_m2=site_area_m2,
                ai_selector=ai_selector,
                ai_reporter=ai_reporter,
            )
            logger.warning("Konsept %s ferdig på %.1fs", family.value, time.time() - t0)
            return (family, result, None)
        except Exception as exc:
            logger.warning("Konsept %s feilet på %.1fs: %s", family.value, time.time() - t0, exc)
            return (family, None, exc)

    # Kjør 3 konsepter parallelt
    t_start = time.time()
    results_by_family = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(families)) as executor:
        futures = [executor.submit(_run_one, f) for f in families]
        for future in concurrent.futures.as_completed(futures):
            family, result, exc = future.result()
            if result is not None:
                results_by_family[family] = result

    logger.warning("Alle %d konsepter ferdig på %.1fs totalt", len(families), time.time() - t_start)

    # Returner i opprinnelig rekkefølge
    plans: List[Masterplan] = []
    for family in families:
        if family in results_by_family:
            plans.append(results_by_family[family])
    return plans


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_masterplan_geometry(plan: Masterplan, buildable_poly: Polygon) -> List[str]:
    errors: List[str] = []
    if not buildings_do_not_overlap(plan.bygg):
        errors.append("Bygg overlapper hverandre.")
    for field in plan.delfelt:
        for building in [b for b in plan.bygg if b.delfelt_id == field.field_id]:
            if not buildable_poly.buffer(1e-6).covers(building.footprint):
                errors.append(f"{building.bygg_id} ligger utenfor buildable_poly.")
            if not field.polygon.buffer(1e-6).covers(building.footprint):
                errors.append(f"{building.bygg_id} ligger utenfor delfelt {field.field_id}.")
            if not building_geometry_is_orthogonal_to_field(building, field):
                errors.append(f"{building.bygg_id} bryter ortogonalitet i {field.field_id}.")
    return errors
