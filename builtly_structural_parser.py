# -*- coding: utf-8 -*-
"""
builtly_structural_parser.py
============================
Builtly RIB Structural Parser — Erstatter bildebasert gjetting med
deterministisk ekstraksjon av strukturell informasjon fra DWG/DXF/PDF.

Steg 1 i ny arkitektur:
  DWG/DXF/PDF → strukturell datamodell (JSON)
  Ingen AI. Ren parsing av hva som faktisk står i filene.

Bruk:
  from builtly_structural_parser import parse_structural_model
  model = parse_structural_model(filepath_or_bytes, filetype="dwg")
"""
from __future__ import annotations

import io
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---- Lazy imports (graceful if missing) ----
try:
    import ezdxf as _ezdxf
except ImportError:
    _ezdxf = None

try:
    import fitz as _fitz  # PyMuPDF
except ImportError:
    _fitz = None

try:
    from pypdf import PdfReader as _PdfReader
except ImportError:
    _PdfReader = None


# ============================================================
# 1. REGEX-MØNSTRE FOR STRUKTURELLE ANNOTASJONER
# ============================================================

# Dekketykkelse: "T=250 UK +115,475" eller "T=200 UK+123,00"
RE_SLAB = re.compile(
    r"T\s*=\s*(\d{2,4})(?:\s*-\s*\d+)?\s*(?:UK|OK)?\s*\+?\s*([\d]+[,.]?\d*)?",
    re.IGNORECASE,
)

# Armering: "24ø12 c250 B18" eller "ø10 c200 B15" eller "OK 24ø12 c250 B18"
RE_REINFORCEMENT = re.compile(
    r"(?:OK|UK|EKSTRA\s*(?:OK|UK))?\s*(\d+)?ø(\d+)\s*c(\d+)\s*(B\d+)?",
    re.IGNORECASE,
)

# Søyleprofil: "SØYLE: KF HUP 100x100x6" eller "SØYLE:KF HUP 100x100x6"
RE_COLUMN_PROFILE = re.compile(
    r"(?:SØYLE|SOYLE|COLUMN|PILLAR|S[ØO]YLE)\s*[:\s]*(?:KF\s+)?([A-Z]+\s*[\dx\.]+(?:x[\d\.]+)*)",
    re.IGNORECASE,
)

# Bjelkeprofil: "BJELKE HEB 200" eller "IPE 300"
RE_BEAM_PROFILE = re.compile(
    r"(?:BJELKE|BEAM|DRAGER)?\s*((?:HEB|HEA|IPE|UPE|UNP|HUP|KKR|RHS|SHS)\s*[\dx\.]+(?:x[\d\.]+)*)",
    re.IGNORECASE,
)

# Isokorb / kuldebrobryter
RE_THERMAL_BREAK = re.compile(
    r"(?:v/)?\s*ISOKORB",
    re.IGNORECASE,
)

# Kote / nivå: "UK +118,725" eller "OK FG +119,40" eller "OK +121,52"
RE_LEVEL = re.compile(
    r"(UK|OK)\s*(?:FG)?\s*\+\s*([\d]+[,.]?\d*)",
    re.IGNORECASE,
)

# Sjakt / kjerne: "SJAKTVEGG", "HEISSJAKT", "TRAPPEROM"
RE_CORE = re.compile(
    r"(?:SJAKT|HEIS|TRAPP|CORE|SHAFT|ELEVATOR|STAIR)",
    re.IGNORECASE,
)

# Skjærarmering: "SKJÆRARM V/SØYLE" eller "SKJÆRARMERING B 23ø8 B20"
RE_SHEAR = re.compile(
    r"SKJ[ÆE]R\s*ARM",
    re.IGNORECASE,
)

# Overhøyde: "15mm OVERHØYDE"
RE_CAMBER = re.compile(
    r"(\d+)\s*mm\s*OVERH[ØO]YDE",
    re.IGNORECASE,
)

# Vegger støpes: "VEGGER STØPES TIL OK DEKKE"
RE_WALL_CAST = re.compile(
    r"VEGGER?\s*ST[ØO]PES",
    re.IGNORECASE,
)

# Slissarmering: "SLISSARM KFR ARM TEGNING"
RE_SLISSARM = re.compile(
    r"SLISS\s*ARM",
    re.IGNORECASE,
)

# Bøyler: "Bøyler ø10 c200"
RE_STIRRUP = re.compile(
    r"B[ØO]YLER\s*ø(\d+)\s*c(\d+)",
    re.IGNORECASE,
)

# Fordelingsarmering: "FORDELING OK ø10 c300 B22"
RE_DISTRIBUTION = re.compile(
    r"FORDELING\s*(OK|UK)?\s*ø(\d+)\s*c(\d+)\s*(B\d+)?",
    re.IGNORECASE,
)

# Akselabel i tittelfelt: "B-01", "B-A", eller frittstående "A", "1"
RE_AXIS_LABEL = re.compile(
    r"^[A-Z](?:-\d{1,3}|-[A-Z])?$|^[A-Z]$|^\d{1,3}$",
)

# Mål (frittstående tall som sannsynligvis er mm): "6911", "5911", "9000"
RE_DIMENSION_VALUE = re.compile(
    r"^(\d{3,6})$",
)


# ============================================================
# 2. DWG/DXF PARSER (ezdxf)
# ============================================================

def _parse_dxf_entities(filepath: str) -> Dict[str, Any]:
    """Leser alle relevante entities fra en DXF/DWG-fil via ezdxf."""
    if _ezdxf is None:
        return {"error": "ezdxf er ikke installert", "entities": {}}

    try:
        doc = _ezdxf.readfile(filepath)
    except Exception as exc:
        return {"error": f"Kunne ikke lese fil: {type(exc).__name__}: {exc}", "entities": {}}

    msp = doc.modelspace()
    result: Dict[str, Any] = {
        "dimensions": [],
        "texts": [],
        "lines_by_layer": {},
        "inserts": [],
        "circles": [],
        "layers": [],
        "error": None,
    }

    # Samle lag-info
    try:
        result["layers"] = [
            {"name": layer.dxf.name, "color": getattr(layer.dxf, "color", 7)}
            for layer in doc.layers
        ]
    except Exception:
        pass

    for entity in msp:
        try:
            layer = str(getattr(entity.dxf, "layer", "0")).upper()
            etype = entity.dxftype()
        except Exception:
            continue

        try:
            # ---- DIMENSION ----
            if etype == "DIMENSION":
                measurement = getattr(entity.dxf, "actual_measurement", None)
                defpoint = getattr(entity.dxf, "defpoint", None)
                defpoint4 = getattr(entity.dxf, "defpoint4", None)
                result["dimensions"].append({
                    "layer": layer,
                    "measurement": float(measurement) if measurement else None,
                    "defpoint": (float(defpoint.x), float(defpoint.y)) if defpoint else None,
                    "defpoint4": (float(defpoint4.x), float(defpoint4.y)) if defpoint4 else None,
                    "text_override": str(getattr(entity.dxf, "text", "") or ""),
                })

            # ---- TEXT / MTEXT ----
            elif etype in ("TEXT", "MTEXT"):
                if etype == "TEXT":
                    text = str(getattr(entity.dxf, "text", "") or "")
                    insert = getattr(entity.dxf, "insert", None)
                    height = float(getattr(entity.dxf, "height", 2.5) or 2.5)
                else:
                    text = str(getattr(entity, "text", "") or "")
                    insert = getattr(entity.dxf, "insert", None)
                    height = float(getattr(entity.dxf, "char_height", 2.5) or 2.5)

                if text.strip():
                    pos = (float(insert.x), float(insert.y)) if insert else (0.0, 0.0)
                    result["texts"].append({
                        "text": text.strip(),
                        "layer": layer,
                        "position": pos,
                        "height": height,
                    })

            # ---- LINE ----
            elif etype == "LINE":
                start = entity.dxf.start
                end = entity.dxf.end
                if layer not in result["lines_by_layer"]:
                    result["lines_by_layer"][layer] = []
                result["lines_by_layer"][layer].append({
                    "type": "line",
                    "start": (float(start.x), float(start.y)),
                    "end": (float(end.x), float(end.y)),
                    "length": math.hypot(end.x - start.x, end.y - start.y),
                })

            # ---- LWPOLYLINE ----
            elif etype == "LWPOLYLINE":
                points = [(float(p[0]), float(p[1])) for p in entity.get_points(format="xy")]
                if points and layer not in result["lines_by_layer"]:
                    result["lines_by_layer"][layer] = []
                if points:
                    result["lines_by_layer"][layer].append({
                        "type": "polyline",
                        "points": points,
                        "closed": entity.closed,
                    })

            # ---- INSERT (blokk-referanse) ----
            elif etype == "INSERT":
                insert = entity.dxf.insert
                result["inserts"].append({
                    "block_name": str(getattr(entity.dxf, "name", "") or ""),
                    "layer": layer,
                    "position": (float(insert.x), float(insert.y)),
                    "xscale": float(getattr(entity.dxf, "xscale", 1.0) or 1.0),
                    "yscale": float(getattr(entity.dxf, "yscale", 1.0) or 1.0),
                    "rotation": float(getattr(entity.dxf, "rotation", 0.0) or 0.0),
                })

            # ---- CIRCLE / ARC ----
            elif etype in ("CIRCLE", "ARC"):
                center = entity.dxf.center
                result["circles"].append({
                    "type": etype.lower(),
                    "layer": layer,
                    "center": (float(center.x), float(center.y)),
                    "radius": float(entity.dxf.radius),
                })

        except Exception:
            continue

    return result


# ============================================================
# 3. TEKSTKLASSIFISERING
# ============================================================

def classify_texts(texts: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Klassifiserer tekst-annotasjoner i strukturelle kategorier."""
    classified: Dict[str, List[Dict[str, Any]]] = {
        "slabs": [],
        "reinforcement": [],
        "columns": [],
        "beams": [],
        "levels": [],
        "core_elements": [],
        "thermal_breaks": [],
        "shear_reinforcement": [],
        "distribution_reinforcement": [],
        "stirrups": [],
        "camber": [],
        "wall_casting": [],
        "axis_labels": [],
        "dimension_values": [],
        "unclassified": [],
    }

    for item in texts:
        text = item["text"].strip()
        if not text or len(text) > 200:
            continue

        matched = False

        # Prioritert rekkefølge — mest spesifikke først
        for pattern, category, label in [
            (RE_THERMAL_BREAK, "thermal_breaks", "isokorb"),
            (RE_SHEAR, "shear_reinforcement", "shear"),
            (RE_DISTRIBUTION, "distribution_reinforcement", "distribution"),
            (RE_STIRRUP, "stirrups", "stirrup"),
            (RE_CAMBER, "camber", "camber"),
            (RE_WALL_CAST, "wall_casting", "wall_cast"),
            (RE_SLISSARM, "wall_casting", "slissarm"),
            (RE_COLUMN_PROFILE, "columns", "column"),
            (RE_BEAM_PROFILE, "beams", "beam"),
            (RE_SLAB, "slabs", "slab"),
            (RE_REINFORCEMENT, "reinforcement", "rebar"),
            (RE_LEVEL, "levels", "level"),
            (RE_CORE, "core_elements", "core"),
        ]:
            match = pattern.search(text)
            if match:
                classified[category].append({
                    **item,
                    "match_groups": match.groups(),
                    "match_text": match.group(0),
                    "category": label,
                })
                matched = True
                break

        if not matched:
            # Sjekk akse-label og dimensjonsverdier
            clean = text.strip().replace(" ", "")
            if RE_AXIS_LABEL.match(clean):
                classified["axis_labels"].append({**item, "category": "axis_label", "clean_label": clean})
                matched = True
            elif RE_DIMENSION_VALUE.match(clean):
                classified["dimension_values"].append({
                    **item,
                    "category": "dimension_value",
                    "value_mm": int(clean),
                })
                matched = True

        if not matched:
            classified["unclassified"].append(item)

    return classified


# ============================================================
# 4. AKSESYSTEM-REKONSTRUKSJON
# ============================================================

def _find_axis_lines(lines_by_layer: Dict[str, List], layers: List[Dict]) -> Tuple[List, List]:
    """Finner akselinjer fra lag-navn."""
    axis_keywords = ["GRID", "AXIS", "AKSE", "AKSER", "S-GRID", "A-GRID", "XREF"]
    x_lines: List[float] = []  # vertikale (konstant x)
    y_lines: List[float] = []  # horisontale (konstant y)

    # Finn lag som sannsynligvis er akser
    candidate_layers = []
    for layer_name, lines in lines_by_layer.items():
        if any(kw in layer_name.upper() for kw in axis_keywords):
            candidate_layers.append(layer_name)

    # Hvis ingen akselag funnet, prøv lag med lange linjer som spenner hele tegningen
    if not candidate_layers:
        for layer_name, lines in lines_by_layer.items():
            long_lines = [l for l in lines if l.get("type") == "line" and l.get("length", 0) > 5000]
            if len(long_lines) >= 2:
                candidate_layers.append(layer_name)

    for layer_name in candidate_layers:
        for item in lines_by_layer.get(layer_name, []):
            if item.get("type") != "line":
                continue
            sx, sy = item["start"]
            ex, ey = item["end"]
            dx = abs(ex - sx)
            dy = abs(ey - sy)
            length = math.hypot(dx, dy)
            if length < 1000:
                continue  # for kort til å være akselinje

            if dx < dy * 0.05:  # vertikal linje
                avg_x = (sx + ex) / 2.0
                x_lines.append(round(avg_x, 1))
            elif dy < dx * 0.05:  # horisontal linje
                avg_y = (sy + ey) / 2.0
                y_lines.append(round(avg_y, 1))

    return x_lines, y_lines


def _cluster_values(values: List[float], tolerance: float = 50.0) -> List[float]:
    """Grupperer nærliggende verdier og returnerer gjennomsnitt per kluster."""
    if not values:
        return []
    sorted_vals = sorted(set(values))
    clusters: List[List[float]] = [[sorted_vals[0]]]
    for v in sorted_vals[1:]:
        if v - clusters[-1][-1] < tolerance:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [round(sum(c) / len(c), 1) for c in clusters]


def _match_labels_to_positions(
    positions: List[float],
    axis_labels: List[Dict],
    direction: str,  # "x" or "y"
) -> List[Dict[str, Any]]:
    """Matcher akselabels til posisjoner basert på nærhet."""
    result = []
    used_labels = set()

    for i, pos in enumerate(positions):
        best_label = None
        best_dist = float("inf")

        for label_item in axis_labels:
            lbl = label_item["clean_label"]
            if lbl in used_labels:
                continue
            lx, ly = label_item["position"]
            dist = abs(lx - pos) if direction == "x" else abs(ly - pos)
            if dist < best_dist and dist < 2000:  # innenfor 2m
                best_dist = dist
                best_label = lbl

        if best_label:
            used_labels.add(best_label)
        else:
            # Auto-generer label
            if direction == "x":
                best_label = str(i + 1)
            else:
                best_label = chr(65 + i) if i < 26 else f"Y{i+1}"

        distance = round(pos - positions[i - 1]) if i > 0 else 0
        result.append({
            "label": best_label,
            "pos_mm": round(pos),
            "distance_mm": round(abs(distance)),
        })

    return result


def reconstruct_axes(
    raw_entities: Dict[str, Any],
    classified: Dict[str, List],
) -> Dict[str, Any]:
    """Rekonstruerer aksesystemet fra DWG-data."""

    x_lines, y_lines = _find_axis_lines(
        raw_entities.get("lines_by_layer", {}),
        raw_entities.get("layers", []),
    )

    # Kluster nærliggende akselinjer
    x_positions = _cluster_values(x_lines, tolerance=100)
    y_positions = _cluster_values(y_lines, tolerance=100)

    # Supplér med dimensjonsverdier
    if not x_positions and not y_positions:
        # Fallback: bruk dimensjoner
        for dim in raw_entities.get("dimensions", []):
            m = dim.get("measurement")
            if m and m > 500:  # over 500mm
                dp = dim.get("defpoint")
                dp4 = dim.get("defpoint4")
                if dp and dp4:
                    if abs(dp[1] - dp4[1]) < abs(dp[0] - dp4[0]) * 0.1:
                        # horisontal dimensjon → x-akser
                        x_positions.extend([round(dp[0]), round(dp4[0])])
                    else:
                        y_positions.extend([round(dp[1]), round(dp4[1])])

        x_positions = _cluster_values(x_positions, tolerance=100)
        y_positions = _cluster_values(y_positions, tolerance=100)

    # Supplér med store frittstående dimensjonsverdier i tekst
    dim_values = classified.get("dimension_values", [])

    # Match labels til posisjoner
    axis_labels = classified.get("axis_labels", [])
    x_axes = _match_labels_to_positions(x_positions, axis_labels, "x")
    y_axes = _match_labels_to_positions(y_positions, axis_labels, "y")

    return {
        "x_axes": x_axes,
        "y_axes": y_axes,
        "total_width_mm": round(x_positions[-1] - x_positions[0]) if len(x_positions) >= 2 else 0,
        "total_depth_mm": round(y_positions[-1] - y_positions[0]) if len(y_positions) >= 2 else 0,
        "n_x": len(x_axes),
        "n_y": len(y_axes),
    }


# ============================================================
# 5. STRUKTURELL MODELL
# ============================================================

def _detect_material(classified: Dict[str, List]) -> Tuple[str, str]:
    """Utleder primærmateriale fra tekstfunn."""
    n_rebar = len(classified.get("reinforcement", []))
    n_shear = len(classified.get("shear_reinforcement", []))
    n_dist = len(classified.get("distribution_reinforcement", []))
    n_stirrups = len(classified.get("stirrups", []))
    n_slabs = len(classified.get("slabs", []))

    n_steel_cols = sum(
        1 for c in classified.get("columns", [])
        if any(kw in c.get("match_text", "").upper() for kw in ["HUP", "HEB", "HEA", "IPE", "KKR", "RHS", "SHS"])
    )
    n_steel_beams = sum(
        1 for b in classified.get("beams", [])
        if any(kw in b.get("match_text", "").upper() for kw in ["HEB", "HEA", "IPE", "UPE", "UNP"])
    )

    total_concrete = n_rebar + n_shear + n_dist + n_stirrups
    total_steel = n_steel_cols + n_steel_beams

    if total_concrete > 5 and n_slabs > 0:
        if total_steel > 2:
            return "plasstøpt betong med stålsøyler/bjelker", "høy"
        return "plasstøpt betong", "høy"
    elif total_concrete > 2:
        return "betong (sannsynlig plasstøpt)", "middels"
    elif total_steel > 3:
        return "stålkonstruksjon", "middels"
    elif n_slabs > 0:
        return "betong (dekketykkelse funnet)", "middels"
    else:
        return "ukjent — utilstrekkelig data", "lav"


def _detect_bearing_system(
    classified: Dict[str, List],
    raw_entities: Dict[str, Any],
) -> Tuple[str, str]:
    """Utleder bæresystem fra funn."""
    n_columns = len(classified.get("columns", []))
    n_cores = len(classified.get("core_elements", []))
    n_wall_cast = len(classified.get("wall_casting", []))

    # Tell vegger fra lag
    n_wall_lines = 0
    for layer, lines in raw_entities.get("lines_by_layer", {}).items():
        if any(kw in layer.upper() for kw in ["WALL", "VEGG", "BÆREV", "BAREV", "S-WALL"]):
            n_wall_lines += len(lines)

    if n_wall_lines > n_columns * 2 or n_wall_cast > 0:
        if n_cores > 0:
            return "bærevegger og kjerner", "middels"
        return "bæreveggsystem", "middels"
    elif n_columns >= 4:
        return "søyle-bjelke-system", "middels"
    elif n_columns > 0 and n_wall_lines > 0:
        return "hybrid (søyler + vegger)", "middels"
    elif n_cores > 0:
        return "kjernebasert", "lav"
    else:
        return "ukjent — utilstrekkelig data", "lav"


def _extract_slab_info(classified: Dict[str, List]) -> List[Dict[str, Any]]:
    """Henter dekkeinfo fra klassifiserte tekster."""
    slabs = []
    for item in classified.get("slabs", []):
        groups = item.get("match_groups", ())
        thickness = int(groups[0]) if groups and groups[0] else None
        level_str = groups[1] if len(groups) > 1 and groups[1] else None
        level = None
        if level_str:
            try:
                level = float(level_str.replace(",", "."))
            except ValueError:
                pass

        slabs.append({
            "thickness_mm": thickness,
            "level_m": level,
            "raw_text": item.get("text", ""),
            "position": item.get("position"),
        })
    return slabs


def _extract_column_info(classified: Dict[str, List], raw_entities: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Henter søyleinfo fra tekst og INSERT-blokker."""
    columns = []

    # Fra tekstgjenkjenning
    for item in classified.get("columns", []):
        columns.append({
            "profile": item.get("match_text", ""),
            "position": item.get("position"),
            "source": "text_annotation",
        })

    # Fra INSERT-blokker med søyle-lignende blokknavn
    for insert in raw_entities.get("inserts", []):
        bname = insert.get("block_name", "").upper()
        if any(kw in bname for kw in ["COL", "SØYLE", "SOYLE", "PILLAR", "COLUMN", "HUP", "HEB"]):
            columns.append({
                "profile": bname,
                "position": insert.get("position"),
                "source": "block_insert",
            })

    return columns


def _extract_reinforcement_summary(classified: Dict[str, List]) -> Dict[str, Any]:
    """Oppsummerer armeringsinfo."""
    rebar = classified.get("reinforcement", [])
    shear = classified.get("shear_reinforcement", [])
    dist = classified.get("distribution_reinforcement", [])
    stirrups = classified.get("stirrups", [])

    # Finn unike armeringsdiametre
    diameters = set()
    spacings = set()
    for item in rebar:
        groups = item.get("match_groups", ())
        if len(groups) >= 3:
            if groups[1]:
                diameters.add(int(groups[1]))
            if groups[2]:
                spacings.add(int(groups[2]))

    return {
        "total_annotations": len(rebar),
        "shear_annotations": len(shear),
        "distribution_annotations": len(dist),
        "stirrup_annotations": len(stirrups),
        "diameters_mm": sorted(diameters),
        "spacings_mm": sorted(spacings),
        "has_isokorb": len(classified.get("thermal_breaks", [])) > 0,
        "has_camber": len(classified.get("camber", [])) > 0,
    }


def build_structural_model(
    raw_entities: Dict[str, Any],
    classified: Dict[str, List],
    axis_system: Dict[str, Any],
    source_file: str = "",
) -> Dict[str, Any]:
    """Bygger den komplette strukturelle datamodellen."""

    material, material_confidence = _detect_material(classified)
    bearing_system, bearing_confidence = _detect_bearing_system(classified, raw_entities)
    slabs = _extract_slab_info(classified)
    columns = _extract_column_info(classified, raw_entities)
    reinforcement = _extract_reinforcement_summary(classified)

    # Beregn spenn fra aksesystem
    spans = []
    for i in range(1, len(axis_system.get("x_axes", []))):
        ax_prev = axis_system["x_axes"][i - 1]
        ax_curr = axis_system["x_axes"][i]
        spans.append({
            "from_axis": ax_prev["label"],
            "to_axis": ax_curr["label"],
            "length_mm": ax_curr["distance_mm"],
            "direction": "x",
        })
    for i in range(1, len(axis_system.get("y_axes", []))):
        ax_prev = axis_system["y_axes"][i - 1]
        ax_curr = axis_system["y_axes"][i]
        spans.append({
            "from_axis": ax_prev["label"],
            "to_axis": ax_curr["label"],
            "length_mm": ax_curr["distance_mm"],
            "direction": "y",
        })

    # Nivåer
    levels = []
    for item in classified.get("levels", []):
        groups = item.get("match_groups", ())
        if len(groups) >= 2 and groups[1]:
            try:
                level_val = float(groups[1].replace(",", "."))
                levels.append({
                    "type": groups[0].upper(),
                    "level_m": level_val,
                    "raw_text": item.get("text", ""),
                })
            except ValueError:
                pass

    # Unik nivåer → estimert antall etasjer
    unique_levels = sorted(set(round(l["level_m"], 1) for l in levels))
    estimated_floors = max(1, len(unique_levels))

    # Statistikk
    total_text_items = sum(len(v) for v in classified.values())
    structural_items = total_text_items - len(classified.get("unclassified", []))

    return {
        "source_file": source_file,
        "primary_material": material,
        "bearing_system": bearing_system,
        "axis_system": axis_system,
        "slabs": slabs,
        "columns": columns,
        "reinforcement": reinforcement,
        "core_elements": [
            {"text": item.get("text", ""), "position": item.get("position")}
            for item in classified.get("core_elements", [])
        ],
        "thermal_breaks": [
            {"text": item.get("text", ""), "position": item.get("position")}
            for item in classified.get("thermal_breaks", [])
        ],
        "spans": spans,
        "levels": levels,
        "unique_levels": unique_levels,
        "estimated_floors": estimated_floors,
        "confidence": {
            "material": material_confidence,
            "bearing_system": bearing_confidence,
            "axes": "høy" if axis_system.get("n_x", 0) >= 2 and axis_system.get("n_y", 0) >= 2 else "lav",
            "overall": material_confidence if material_confidence != "lav" else bearing_confidence,
        },
        "statistics": {
            "total_texts": total_text_items,
            "structural_texts": structural_items,
            "unclassified_texts": len(classified.get("unclassified", [])),
            "dimensions_found": len(raw_entities.get("dimensions", [])),
            "layers_found": len(raw_entities.get("layers", [])),
            "line_layers": len(raw_entities.get("lines_by_layer", {})),
            "inserts_found": len(raw_entities.get("inserts", [])),
        },
    }


# ============================================================
# 6. PDF TEKST-EKSTRAKSJON
# ============================================================

def _parse_pdf_texts(filepath: str, max_pages: int = 10) -> List[Dict[str, Any]]:
    """
    Ekstraherer tekst med posisjon fra PDF.
    Prøver PyMuPDF først (posisjon per span), deretter pypdf (linjebasert).
    """
    texts = _parse_pdf_texts_fitz(filepath, max_pages)
    if not texts:
        texts = _parse_pdf_texts_pypdf(filepath, max_pages)
    return texts


def _parse_pdf_texts_fitz(filepath: str, max_pages: int = 10) -> List[Dict[str, Any]]:
    """PyMuPDF-basert ekstraksjon med posisjon per tekstspan."""
    if _fitz is None:
        return []
    texts = []
    try:
        doc = _fitz.open(filepath)
        for page_num in range(min(len(doc), max_pages)):
            page = doc[page_num]
            blocks = page.get_text("dict", flags=_fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", [])
            for block in blocks:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if text and len(text) > 0:
                            bbox = span.get("bbox", (0, 0, 0, 0))
                            texts.append({
                                "text": text,
                                "layer": f"PDF_PAGE_{page_num + 1}",
                                "position": (float(bbox[0]), float(bbox[1])),
                                "height": float(span.get("size", 8)),
                                "page": page_num + 1,
                            })
        doc.close()
    except Exception:
        pass
    return texts


def _parse_pdf_texts_pypdf(filepath: str, max_pages: int = 10) -> List[Dict[str, Any]]:
    """
    pypdf-basert ekstraksjon. Gir ikke posisjon per span, men
    splitter teksten i individuelle annotasjoner som kan klassifiseres.
    """
    if _PdfReader is None:
        return []

    texts = []
    try:
        reader = _PdfReader(filepath)
        for page_num in range(min(len(reader.pages), max_pages)):
            page = reader.pages[page_num]
            raw_text = page.extract_text() or ""
            if not raw_text.strip():
                continue

            # Pre-prosesser: slå sammen linjer som hører sammen
            raw_text = _merge_continuation_lines(raw_text)

            line_num = 0
            for line in raw_text.split("\n"):
                line = line.strip()
                if not line:
                    continue

                # Splitt sammensatte linjer med flere annotasjoner
                # F.eks. "OK 24ø12 c250 B18 OK 15ø12 c250 B18" → to separate
                sub_items = _split_compound_line(line)
                for sub in sub_items:
                    sub = sub.strip()
                    if not sub or len(sub) < 1:
                        continue
                    texts.append({
                        "text": sub,
                        "layer": f"PDF_PAGE_{page_num + 1}",
                        "position": (0.0, float(line_num * 10)),  # estimert posisjon
                        "height": 2.5,
                        "page": page_num + 1,
                    })
                line_num += 1
    except Exception:
        pass
    return texts


def _merge_continuation_lines(text: str) -> str:
    """
    Slår sammen linjer som hører sammen i konstruksjonstegninger.

    Eksempler:
      "SØYLE:\\nKF HUP 100x100x6" → "SØYLE: KF HUP 100x100x6"
      "FORDELING\\nOK ø10 c300 B22" → "FORDELING OK ø10 c300 B22"
      "PLATE:\\nUK PL1" → "PLATE: UK PL1"
      "VEGGER STØPES\\nTIL OK DEKKE" → "VEGGER STØPES TIL OK DEKKE"
      "T=250-220\\nUK +115,475" → "T=250-220 UK +115,475"
    """
    lines = text.split("\n")
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()

            # SØYLE: / PLATE: / BJELKE: etterfulgt av profil
            if re.search(r'(?:SØYLE|SOYLE|PLATE|BJELKE|DRAGER)\s*:\s*$', line, re.IGNORECASE):
                merged.append(f"{line} {next_line}")
                i += 2
                continue

            # FORDELING etterfulgt av OK/UK ø...
            if re.search(r'FORDELING\s*$', line, re.IGNORECASE) and re.match(r'(?:OK|UK)\s', next_line, re.IGNORECASE):
                merged.append(f"{line} {next_line}")
                i += 2
                continue

            # VEGGER STØPES / TIL OK DEKKE
            if re.search(r'(?:STØPES|STOPES)\s*$', line, re.IGNORECASE) and re.match(r'TIL\s', next_line, re.IGNORECASE):
                merged.append(f"{line} {next_line}")
                i += 2
                continue

            # SLISSARM KFR / ARM TEGNING
            if re.search(r'KFR\s*$', line, re.IGNORECASE) and re.match(r'ARM\s', next_line, re.IGNORECASE):
                merged.append(f"{line} {next_line}")
                i += 2
                continue

            # T=250-220 / UK +115,475
            if re.match(r'T\s*=\s*\d', line) and re.match(r'(?:UK|OK)\s*\+', next_line, re.IGNORECASE):
                merged.append(f"{line} {next_line}")
                i += 2
                continue

            # SJAKTVEGGER / STØPES TIL ...
            if re.search(r'(?:SJAKT|HEIS)', line, re.IGNORECASE) and re.search(r'ST[ØO]PES', next_line, re.IGNORECASE):
                merged.append(f"{line} {next_line}")
                i += 2
                continue

        merged.append(line)
        i += 1

    return "\n".join(merged)


def _split_compound_line(line: str) -> List[str]:
    """
    Splitter sammensatte linjer i individuelle annotasjoner.
    F.eks:
      "OK 24ø12 c250 B18 OK 15ø12 c250 B18" → to separate
      "6911 6531" → to separate
      "T=250 UK +115,475" → holdes samlet
      "SØYLE:\\nKF HUP 100x100x6" → holdes samlet
    """
    items = []

    # Splitt på mellomrom der begge deler er frittstående tall (mål)
    parts = line.split()
    if all(RE_DIMENSION_VALUE.match(p) for p in parts) and len(parts) > 1:
        return parts

    # Splitt sammensatte armeringsangivelser
    # "OK 24ø12 c250 B18 OK 15ø12 c250 B18"
    rebar_splits = re.split(r'(?=(?:OK|UK|EKSTRA\s*(?:OK|UK))\s+\d*ø)', line)
    if len(rebar_splits) > 1:
        return [s.strip() for s in rebar_splits if s.strip()]

    # Splitt linjer med "SØYLE:\nKF HUP..." (to-linjers)
    if re.search(r'SØYLE\s*:\s*$', line, re.IGNORECASE):
        return [line]  # hold samlet med neste linje

    return [line]


# ============================================================
# 7. HOVEDFUNKSJON
# ============================================================

def parse_structural_model(
    filepath_or_bytes: Any,
    filetype: str = "auto",
    filename: str = "",
) -> Dict[str, Any]:
    """
    Hovedfunksjon: Parser en tegningsfil og returnerer strukturell datamodell.

    Args:
        filepath_or_bytes: Filsti (str/Path) eller bytes
        filetype: "dwg", "dxf", "pdf", eller "auto" (detekterer fra filnavn)
        filename: Brukes for auto-deteksjon hvis bytes

    Returns:
        Strukturell datamodell (dict) med materiale, aksesystem, elementer osv.
    """
    # Bestem filtype
    if filetype == "auto":
        name = str(filename or filepath_or_bytes or "").lower()
        if name.endswith(".dxf"):
            filetype = "dxf"
        elif name.endswith(".dwg"):
            filetype = "dwg"
        elif name.endswith(".pdf"):
            filetype = "pdf"
        else:
            filetype = "dxf"

    # Skriv bytes til temp-fil hvis nødvendig
    tmp_path = None
    if isinstance(filepath_or_bytes, (bytes, bytearray)):
        suffix = f".{filetype}" if filetype != "auto" else ".dxf"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(filepath_or_bytes)
            tmp.flush()
            tmp_path = tmp.name
        filepath = tmp_path
    else:
        filepath = str(filepath_or_bytes)

    try:
        if filetype == "pdf":
            # PDF: ekstraher tekst, klassifiser, bygg modell
            texts = _parse_pdf_texts(filepath)
            classified = classify_texts(texts)
            axis_system = {"x_axes": [], "y_axes": [], "n_x": 0, "n_y": 0,
                           "total_width_mm": 0, "total_depth_mm": 0}
            raw_entities = {"dimensions": [], "texts": texts, "lines_by_layer": {},
                            "inserts": [], "circles": [], "layers": []}

            # For PDF: prøv å rekonstruere akser fra dimensjonsverdier i teksten
            dim_values = classified.get("dimension_values", [])
            if dim_values:
                # Sorter etter posisjon for å estimere akser
                x_sorted = sorted(dim_values, key=lambda d: d["position"][0])
                y_sorted = sorted(dim_values, key=lambda d: d["position"][1])
                # Bruk store dimensjonsverdier som akseavstander
                big_dims = [d for d in dim_values if d["value_mm"] > 2000]
                if big_dims:
                    x_pos = [0.0]
                    for d in big_dims[:6]:
                        x_pos.append(x_pos[-1] + d["value_mm"])
                    axis_system["x_axes"] = [
                        {"label": str(i + 1), "pos_mm": round(p), "distance_mm": round(p - x_pos[max(0, i-1)]) if i > 0 else 0}
                        for i, p in enumerate(x_pos[:8])
                    ]
                    axis_system["n_x"] = len(axis_system["x_axes"])
                    axis_system["total_width_mm"] = round(x_pos[-1]) if len(x_pos) > 1 else 0

        else:
            # DWG/DXF: full parsing
            raw_entities = _parse_dxf_entities(filepath)
            if raw_entities.get("error"):
                return {
                    "error": raw_entities["error"],
                    "source_file": filename or filepath,
                    "filetype": filetype,
                }

            texts = raw_entities.get("texts", [])
            classified = classify_texts(texts)
            axis_system = reconstruct_axes(raw_entities, classified)

        model = build_structural_model(
            raw_entities=raw_entities,
            classified=classified,
            axis_system=axis_system,
            source_file=filename or os.path.basename(filepath),
        )
        model["filetype"] = filetype
        model["classified_texts"] = {
            k: len(v) for k, v in classified.items()
        }

        return model

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def format_model_for_llm(model: Dict[str, Any]) -> str:
    """
    Formaterer den strukturelle modellen som lesbar tekst for LLM-prompt.
    Dette erstatter å sende bilder til LLM-en.
    """
    lines = []
    lines.append(f"KILDEFIL: {model.get('source_file', 'ukjent')}")
    lines.append(f"FILTYPE: {model.get('filetype', 'ukjent')}")
    lines.append("")

    lines.append("UTLEDET BÆRESYSTEM OG MATERIALE:")
    lines.append(f"  Primærmateriale: {model.get('primary_material', 'ukjent')}")
    lines.append(f"  Bæresystem: {model.get('bearing_system', 'ukjent')}")
    lines.append(f"  Konfidensgrad materiale: {model.get('confidence', {}).get('material', 'ukjent')}")
    lines.append(f"  Konfidensgrad bæresystem: {model.get('confidence', {}).get('bearing_system', 'ukjent')}")
    lines.append("")

    axes = model.get("axis_system", {})
    if axes.get("x_axes"):
        lines.append("AKSESYSTEM (vertikale akser / x-retning):")
        for ax in axes["x_axes"]:
            lines.append(f"  {ax['label']}: posisjon {ax['pos_mm']} mm (avstand {ax['distance_mm']} mm fra forrige)")
        lines.append(f"  Total bredde: {axes.get('total_width_mm', 0)} mm")
    if axes.get("y_axes"):
        lines.append("AKSESYSTEM (horisontale akser / y-retning):")
        for ax in axes["y_axes"]:
            lines.append(f"  {ax['label']}: posisjon {ax['pos_mm']} mm (avstand {ax['distance_mm']} mm fra forrige)")
        lines.append(f"  Total dybde: {axes.get('total_depth_mm', 0)} mm")
    lines.append("")

    slabs = model.get("slabs", [])
    if slabs:
        lines.append("DEKKER:")
        for s in slabs:
            t = f"T={s['thickness_mm']}mm" if s.get("thickness_mm") else "ukjent tykkelse"
            l = f"kote {s['level_m']}m" if s.get("level_m") else ""
            lines.append(f"  {t} {l} ({s.get('raw_text', '')})")
    lines.append("")

    cols = model.get("columns", [])
    if cols:
        lines.append(f"SØYLER ({len(cols)} stk):")
        for c in cols[:10]:
            lines.append(f"  {c.get('profile', 'ukjent')} ved posisjon {c.get('position', 'ukjent')}")
    lines.append("")

    rebar = model.get("reinforcement", {})
    if rebar.get("total_annotations", 0) > 0:
        lines.append("ARMERING:")
        lines.append(f"  Hoved: {rebar['total_annotations']} annotasjoner")
        lines.append(f"  Skjær: {rebar.get('shear_annotations', 0)}")
        lines.append(f"  Fordeling: {rebar.get('distribution_annotations', 0)}")
        lines.append(f"  Bøyler: {rebar.get('stirrup_annotations', 0)}")
        if rebar.get("diameters_mm"):
            lines.append(f"  Diametere: ø{', ø'.join(str(d) for d in rebar['diameters_mm'])} mm")
        if rebar.get("spacings_mm"):
            lines.append(f"  Senteravstander: c{', c'.join(str(s) for s in rebar['spacings_mm'])} mm")
        if rebar.get("has_isokorb"):
            lines.append("  Isokorb/kuldebrobryter: JA")
    lines.append("")

    spans = model.get("spans", [])
    if spans:
        lines.append("SPENNVIDDER:")
        for sp in spans:
            lines.append(f"  {sp['from_axis']} → {sp['to_axis']}: {sp['length_mm']} mm ({sp['direction']}-retning)")
    lines.append("")

    cores = model.get("core_elements", [])
    if cores:
        lines.append(f"KJERNEELEMENTER ({len(cores)} stk):")
        for c in cores:
            lines.append(f"  {c.get('text', 'kjerne')}")
    lines.append("")

    levels = model.get("unique_levels", [])
    if levels:
        lines.append(f"NIVÅER (estimert {model.get('estimated_floors', '?')} etasjer):")
        for lev in levels:
            lines.append(f"  +{lev} m")
    lines.append("")

    stats = model.get("statistics", {})
    lines.append("STATISTIKK:")
    lines.append(f"  Totalt tekstfunn: {stats.get('total_texts', 0)}")
    lines.append(f"  Strukturelle funn: {stats.get('structural_texts', 0)}")
    lines.append(f"  Uklassifiserte: {stats.get('unclassified_texts', 0)}")
    lines.append(f"  Dimensjoner: {stats.get('dimensions_found', 0)}")
    lines.append(f"  Lag: {stats.get('layers_found', 0)}")

    return "\n".join(lines)


# ============================================================
# 8. CLI for testing
# ============================================================

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Bruk: python builtly_structural_parser.py <fil.dwg|.dxf|.pdf>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"Parser: {filepath}")

    model = parse_structural_model(filepath, filename=os.path.basename(filepath))

    if model.get("error"):
        print(f"FEIL: {model['error']}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("STRUKTURELL MODELL")
    print("=" * 60)
    print(format_model_for_llm(model))

    # Lagre full JSON
    out_path = filepath + ".structural.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nFull JSON lagret: {out_path}")
