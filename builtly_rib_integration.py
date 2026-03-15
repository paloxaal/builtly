# -*- coding: utf-8 -*-
"""
builtly_rib_integration.py
==========================
Integrasjon mellom builtly_structural_parser og Konstruksjon_RIB_geometri.

Legg denne filen i samme mappe som hovedfilen.
Importer og bruk i Konstruksjon_RIB_geometri:

    from builtly_rib_integration import (
        run_structural_analysis_v2,
        build_llm_assessment_prompt,
        structural_model_to_sketch_elements,
    )

Erstatt kallet til run_structured_drawing_analysis med run_structural_analysis_v2.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

try:
    from builtly_structural_parser import (
        parse_structural_model,
        format_model_for_llm,
    )
    HAS_STRUCTURAL_PARSER = True
except ImportError:
    HAS_STRUCTURAL_PARSER = False


# ============================================================
# 1. KJØR STRUKTURELL ANALYSE (erstatter bildebasert)
# ============================================================

def run_structural_analysis_v2(
    drawings: List[Dict[str, Any]],
    project_data: Dict[str, Any],
    material_preference: str = "Automatisk",
    foundation_preference: str = "Automatisk",
    optimization_mode: str = "Rasjonalitet",
) -> Dict[str, Any]:
    """
    Steg 1: Parser alle tegningsfiler og bygger strukturell modell.
    Returnerer en samlet modell for alle filer.
    
    Denne funksjonen erstatter bildebasert analyse.
    """
    if not HAS_STRUCTURAL_PARSER:
        return {
            "error": "builtly_structural_parser er ikke tilgjengelig",
            "models": [],
        }

    models = []
    for record in drawings:
        # Sjekk om det er en DWG/DXF-fil med rå bytes tilgjengelig
        raw_bytes = record.get("raw_bytes")
        filepath = record.get("filepath")
        name = record.get("name", "ukjent")
        fmt = record.get("drawing_format", "")

        if fmt in ("dxf", "dwg") and (raw_bytes or filepath):
            source = raw_bytes if raw_bytes else filepath
            model = parse_structural_model(
                source,
                filetype=fmt,
                filename=name,
            )
            if not model.get("error"):
                models.append(model)

        elif fmt == "pdf" and (raw_bytes or filepath):
            source = raw_bytes if raw_bytes else filepath
            model = parse_structural_model(
                source,
                filetype="pdf",
                filename=name,
            )
            if not model.get("error") and model.get("statistics", {}).get("structural_texts", 0) > 0:
                models.append(model)

    # Kombiner modeller til én samlet
    if not models:
        return {
            "error": "Ingen strukturell data kunne ekstraheres fra tegningene",
            "models": [],
            "combined": None,
        }

    combined = _combine_models(models, project_data)
    return {
        "error": None,
        "models": models,
        "combined": combined,
    }


def _combine_models(models: List[Dict[str, Any]], project_data: Dict[str, Any]) -> Dict[str, Any]:
    """Kombinerer flere strukturelle modeller til én."""
    
    # Bruk modellen med flest funn som primær
    primary = max(models, key=lambda m: m.get("statistics", {}).get("structural_texts", 0))
    
    # Samle alle slabs, columns, spans fra alle modeller
    all_slabs = []
    all_columns = []
    all_cores = []
    all_rebar_count = 0
    all_levels = set()
    
    for m in models:
        all_slabs.extend(m.get("slabs", []))
        all_columns.extend(m.get("columns", []))
        all_cores.extend(m.get("core_elements", []))
        all_rebar_count += m.get("reinforcement", {}).get("total_annotations", 0)
        for lev in m.get("unique_levels", []):
            all_levels.add(lev)
    
    combined = {
        "source_files": [m.get("source_file", "") for m in models],
        "primary_material": primary.get("primary_material", "ukjent"),
        "bearing_system": primary.get("bearing_system", "ukjent"),
        "axis_system": primary.get("axis_system", {}),
        "slabs": all_slabs,
        "columns": all_columns,
        "core_elements": all_cores,
        "reinforcement_total": all_rebar_count,
        "spans": primary.get("spans", []),
        "unique_levels": sorted(all_levels),
        "estimated_floors": max(1, len(all_levels)),
        "thermal_breaks": any(m.get("reinforcement", {}).get("has_isokorb") for m in models),
        "confidence": primary.get("confidence", {}),
        "project_name": project_data.get("p_name", ""),
        "project_type": project_data.get("b_type", ""),
    }
    
    return combined


# ============================================================
# 2. LLM-PROMPT FOR FAGLIG VURDERING (steg 2)
# ============================================================

def build_llm_assessment_prompt(
    structural_result: Dict[str, Any],
    project_data: Dict[str, Any],
    material_preference: str = "Automatisk",
    foundation_preference: str = "Automatisk",
    optimization_mode: str = "Rasjonalitet",
) -> str:
    """
    Bygger LLM-prompt for faglig vurdering.
    LLM-en mottar strukturert data, IKKE bilder.
    """
    combined = structural_result.get("combined", {})
    models = structural_result.get("models", [])
    
    # Formater alle modeller som tekst
    model_texts = []
    for m in models:
        model_texts.append(format_model_for_llm(m))
    
    all_models_text = "\n\n---\n\n".join(model_texts) if model_texts else "Ingen strukturell data tilgjengelig."
    
    return f"""
Du er en senior rådgivende ingeniør konstruksjon (RIB) i Norge.

Du har mottatt maskinelt ekstrahert strukturell informasjon fra tegningsfiler.
VIKTIG: Dataene er ekstrahert programmatisk fra DWG/DXF/PDF. Du trenger IKKE
gjette noe fra bilder. Vurder det som er funnet og gi en faglig vurdering.

PROSJEKT:
- Navn: {project_data.get('p_name', 'Ukjent')}
- Type: {project_data.get('b_type', 'Ukjent')}
- Etasjer: {project_data.get('etasjer', 'Ukjent')}
- BTA: {project_data.get('bta', 0)} m²
- Sted: {project_data.get('adresse', '')}, {project_data.get('kommune', '')}

BRUKERENS FØRINGER:
- Foretrukket materiale: {material_preference}
- Fundamentering: {foundation_preference}
- Optimaliser for: {optimization_mode}

UTLEDET FRA TEGNINGER:
- Primærmateriale: {combined.get('primary_material', 'ukjent')}
- Bæresystem: {combined.get('bearing_system', 'ukjent')}
- Antall søyler funnet: {len(combined.get('columns', []))}
- Antall kjerneelementer: {len(combined.get('core_elements', []))}
- Armeringsannotasjoner: {combined.get('reinforcement_total', 0)}
- Estimerte etasjer: {combined.get('estimated_floors', '?')}
- Isokorb/kuldebrobryter: {'Ja' if combined.get('thermal_breaks') else 'Nei'}
- Konfidensgrad: {json.dumps(combined.get('confidence', {}), ensure_ascii=False)}

DETALJERT EKSTRAKSJON PER FIL:
{all_models_text}

OPPGAVE:
Basert på denne strukturelle modellen, gi en faglig vurdering.
Referer alltid til akser (f.eks. "mellom akse 1 og 2"), IKKE koordinater.

Returner KUN gyldig JSON:
{{
    "grunnlag_status": "FULLSTENDIG | DELVIS | FOR_SVAKT",
    "grunnlag_begrunnelse": "kort tekst om datakvalitet",
    "material_confirmed": true/false,
    "material_comment": "hvorfor du er enig/uenig med utledet materiale",
    "bearing_system_confirmed": true/false,
    "bearing_system_comment": "vurdering av bæresystem",
    "recommended_system": {{
        "system_name": "navn på anbefalt/bekreftet system",
        "material": "materiale",
        "deck_type": "dekkeprinsipp",
        "vertical_system": "vertikalt bæresystem",
        "stability_system": "stabilitetsprinsipp",
        "foundation_strategy": "fundamenteringsstrategi",
        "typical_span_m": "spennvidder fra aksesystemet",
        "rationality_reason": "hvorfor dette er rasjonelt",
        "safety_reason": "robusthet",
        "buildability_notes": ["..."],
        "load_path": ["beskriv lastvei med aksreferanser"]
    }},
    "observasjoner": ["hva du ser i de strukturelle dataene"],
    "mangler": ["hva som mangler for videre prosjektering"],
    "risk_register": [
        {{"topic": "risiko", "severity": "Lav | Middels | Høy", "mitigation": "tiltak"}}
    ],
    "next_steps": ["anbefalte neste steg"]
}}

Returner kun JSON.
""".strip()


# ============================================================
# 3. KONVERTER MODELL TIL SKISSE-ELEMENTER (steg 3)
# ============================================================

def structural_model_to_sketch_elements(
    structural_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Konverterer den strukturelle modellen til skisse-elementer
    som kan brukes i konseptskisse/overlay.
    
    Elementer plasseres basert på aksesystemet, aldri gjettet.
    """
    combined = structural_result.get("combined")
    if not combined:
        return []
    
    axes = combined.get("axis_system", {})
    x_axes = axes.get("x_axes", [])
    y_axes = axes.get("y_axes", [])
    
    if not x_axes or not y_axes:
        return []
    
    elements = []
    
    bearing = combined.get("bearing_system", "")
    
    # ---- Søyler (fra data, ikke gjettet) ----
    if combined.get("columns"):
        for i, col in enumerate(combined["columns"]):
            pos = col.get("position")
            if pos:
                elements.append({
                    "type": "column",
                    "x": float(pos[0]),
                    "y": float(pos[1]),
                    "label": f"S{i+1}",
                    "profile": col.get("profile", ""),
                    "source": "ekstrahert",
                })
    elif "søyle" in bearing.lower():
        # Generer søyler på aksekryss
        col_nr = 1
        for xax in x_axes:
            for yax in y_axes:
                elements.append({
                    "type": "column",
                    "x": float(xax["pos_mm"]),
                    "y": float(yax["pos_mm"]),
                    "label": f"S{col_nr}",
                    "axis_ref": f"{xax['label']}/{yax['label']}",
                    "source": "generert_fra_akser",
                })
                col_nr += 1
    
    # ---- Kjerner ----
    for i, core in enumerate(combined.get("core_elements", [])):
        pos = core.get("position")
        if pos:
            elements.append({
                "type": "core",
                "x": float(pos[0]),
                "y": float(pos[1]),
                "w": 3000,  # standardbredde for kjerne
                "h": 3000,
                "label": f"Kjerne {i+1}",
                "source": "ekstrahert",
            })
    
    # ---- Bærevegger (langs indre akser) ----
    if "vegg" in bearing.lower() or "kjerne" in bearing.lower():
        for xax in x_axes[1:-1]:  # indre vertikale akser
            elements.append({
                "type": "wall",
                "x1": float(xax["pos_mm"]),
                "y1": float(y_axes[0]["pos_mm"]),
                "x2": float(xax["pos_mm"]),
                "y2": float(y_axes[-1]["pos_mm"]),
                "label": f"Bærevegg {xax['label']}",
                "axis_ref": xax["label"],
                "source": "generert_fra_akser",
            })
    
    # ---- Spennpiler ----
    if len(x_axes) >= 2:
        below_y = float(y_axes[-1]["pos_mm"]) + 1500
        for i in range(1, len(x_axes)):
            dist = x_axes[i]["distance_mm"]
            elements.append({
                "type": "span_arrow",
                "x1": float(x_axes[i-1]["pos_mm"]),
                "y1": below_y,
                "x2": float(x_axes[i]["pos_mm"]),
                "y2": below_y,
                "label": f"{dist} mm",
                "source": "fra_aksesystem",
            })
    
    return elements


def sketch_elements_to_normalized(
    elements: List[Dict[str, Any]],
    axis_system: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Konverterer mm-baserte elementer til normaliserte 0-1 koordinater
    for bruk i overlay-rendering på tegningsbilder.
    """
    x_axes = axis_system.get("x_axes", [])
    y_axes = axis_system.get("y_axes", [])
    
    if not x_axes or not y_axes:
        return elements
    
    max_x = max(float(a["pos_mm"]) for a in x_axes) or 1.0
    max_y = max(float(a["pos_mm"]) for a in y_axes) or 1.0
    # Legg til margin for spennpiler etc
    max_x *= 1.15
    max_y *= 1.25
    
    pad = 0.08
    draw_w = 1.0 - pad * 2
    draw_h = 1.0 - pad * 2
    
    def norm(x_mm, y_mm):
        nx = pad + (x_mm / max_x) * draw_w
        ny = pad + (y_mm / max_y) * draw_h
        return round(max(0.02, min(0.98, nx)), 4), round(max(0.02, min(0.98, ny)), 4)
    
    normalized = []
    for el in elements:
        el_type = el.get("type", "")
        
        if el_type == "column":
            nx, ny = norm(el.get("x", 0), el.get("y", 0))
            normalized.append({"type": "column", "x": nx, "y": ny, "label": el.get("label", "")})
        
        elif el_type == "core":
            nx, ny = norm(el.get("x", 0), el.get("y", 0))
            nw = (el.get("w", 3000) / max_x) * draw_w
            nh = (el.get("h", 3000) / max_y) * draw_h
            normalized.append({"type": "core", "x": nx, "y": ny, "w": round(nw, 4), "h": round(nh, 4), "label": el.get("label", "")})
        
        elif el_type in ("wall", "beam"):
            nx1, ny1 = norm(el.get("x1", 0), el.get("y1", 0))
            nx2, ny2 = norm(el.get("x2", 0), el.get("y2", 0))
            normalized.append({"type": el_type, "x1": nx1, "y1": ny1, "x2": nx2, "y2": ny2, "label": el.get("label", "")})
        
        elif el_type == "span_arrow":
            nx1, ny1 = norm(el.get("x1", 0), el.get("y1", 0))
            nx2, ny2 = norm(el.get("x2", 0), el.get("y2", 0))
            normalized.append({"type": "span_arrow", "x1": nx1, "y1": ny1, "x2": nx2, "y2": ny2, "label": el.get("label", "")})
    
    return normalized
