from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

try:
    import cv2
except Exception:
    cv2 = None


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _copy_rgb(img: Image.Image) -> Image.Image:
    return img.convert("RGB") if img.mode != "RGB" else img.copy()


def _bbox(points: Sequence[Tuple[int, int]]) -> Tuple[int, int, int, int]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def _px_to_rel(x: float, y: float, w: int, h: int) -> Tuple[float, float]:
    return _clamp(x / max(1.0, float(w)), 0.0, 1.0), _clamp(y / max(1.0, float(h)), 0.0, 1.0)


def _ensure_closed(points: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if not points:
        return []
    if points[0] != points[-1]:
        return points + [points[0]]
    return points


def _normalized_text(*parts: Any) -> str:
    txt = " ".join(_clean(p) for p in parts if _clean(p))
    txt = txt.lower().replace("å", "a").replace("ø", "o").replace("æ", "ae")
    txt = re.sub(r"\s+", " ", txt)
    return txt


def _point_in_polygon(point: Tuple[float, float], polygon_px: Sequence[Tuple[int, int]]) -> bool:
    if len(polygon_px) < 3:
        return False
    if cv2 is not None:
        cnt = np.array(polygon_px, dtype=np.int32).reshape((-1, 1, 2))
        return cv2.pointPolygonTest(cnt, point, False) >= 0

    x, y = point
    inside = False
    j = len(polygon_px) - 1
    for i in range(len(polygon_px)):
        xi, yi = polygon_px[i]
        xj, yj = polygon_px[j]
        cross = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / max((yj - yi), 1e-9) + xi
        )
        if cross:
            inside = not inside
        j = i
    return inside


def _dark_bbox_polygon(gray: np.ndarray, w: int, h: int) -> List[Tuple[int, int]]:
    ys, xs = np.where(gray < 225)
    if len(xs) == 0:
        return [
            (int(0.08 * w), int(0.08 * h)),
            (int(0.92 * w), int(0.08 * h)),
            (int(0.92 * w), int(0.92 * h)),
            (int(0.08 * w), int(0.92 * h)),
        ]
    x1, x2 = int(xs.min()), int(xs.max())
    y1, y2 = int(ys.min()), int(ys.max())
    pad = max(8, int(min(w, h) * 0.02))
    return [
        (max(0, x1 - pad), max(0, y1 - pad)),
        (min(w - 1, x2 + pad), max(0, y1 - pad)),
        (min(w - 1, x2 + pad), min(h - 1, y2 + pad)),
        (max(0, x1 - pad), min(h - 1, y2 + pad)),
    ]


def classify_plan_type(
    drawing_name: str,
    drawing_label: str = "",
    notes: Optional[Sequence[str]] = None,
    img: Optional[Image.Image] = None,
) -> str:
    text = _normalized_text(drawing_name, drawing_label, " ".join(notes or []))

    basement_keys = [
        "kjeller", "u1", "u2", "u. etg", "underetasje", "garage", "garasje",
        "park", "p-kjeller", "rampe", "sokkel", "plan -1", "teknisk plan"
    ]
    roof_keys = ["tak", "roof", "takplan", "takterrasse", "loft"]
    open_keys = ["lobby", "naring", "kontor", "retail", "fellesareal", "atrium", "open"]
    residential_keys = ["bolig", "leilighet", "1. etg", "2. etg", "3. etg", "etg", "korridor"]

    if any(k in text for k in roof_keys):
        return "roof"
    if any(k in text for k in basement_keys):
        return "basement_open"
    if any(k in text for k in open_keys):
        return "ground_open"
    if any(k in text for k in residential_keys):
        return "residential"

    if img is not None:
        arr = np.asarray(_copy_rgb(img), dtype=np.uint8)
        gray = arr.mean(axis=2)
        dark_ratio = float(np.mean(gray < 225))
        h, w = gray.shape[:2]
        aspect = w / max(1.0, float(h))
        if dark_ratio < 0.065 and aspect > 0.95:
            return "ground_open"
        if dark_ratio > 0.12:
            return "residential"

    return "unknown"


def detect_building_contour(img: Image.Image) -> Dict[str, Any]:
    base = _copy_rgb(img)
    w, h = base.size
    arr = np.asarray(base, dtype=np.uint8)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if cv2 is not None else arr.mean(axis=2).astype(np.uint8)

    if cv2 is None:
        poly = _dark_bbox_polygon(gray, w, h)
        return {"polygon_px": _ensure_closed(poly), "bbox_px": _bbox(poly), "method": "bbox_fallback"}

    dark = (gray < 220).astype(np.uint8) * 255

    margin = max(4, int(min(w, h) * 0.012))
    dark[:margin, :] = 0
    dark[-margin:, :] = 0
    dark[:, :margin] = 0
    dark[:, -margin:] = 0

    close_k = max(9, int(min(w, h) * 0.035))
    k_close = cv2.getStructuringElement(cv2.MORPH_RECT, (close_k, close_k))
    k_open = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, close_k // 4), max(3, close_k // 4)))

    mass = cv2.dilate(dark, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)
    mass = cv2.morphologyEx(mass, cv2.MORPH_CLOSE, k_close, iterations=1)
    mass = cv2.morphologyEx(mass, cv2.MORPH_OPEN, k_open, iterations=1)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mass, 8)
    img_area = float(w * h)

    best_mask = None
    best_score = -1.0

    for label in range(1, num_labels):
        x, y, bw, bh, area = stats[label]
        area_ratio = area / max(1.0, img_area)
        if area_ratio < 0.03 or area_ratio > 0.95:
            continue

        cx = x + bw / 2.0
        cy = y + bh / 2.0
        title_penalty = 0.35 if (cx > 0.88 * w and cy > 0.80 * h) else 1.0
        border_penalty = 0.85 if (x < margin * 2 or y < margin * 2 or x + bw > w - margin * 2 or y + bh > h - margin * 2) else 1.0
        score = area_ratio * 100.0 * title_penalty * border_penalty

        if score > best_score:
            best_score = score
            best_mask = (labels == label).astype(np.uint8) * 255

    if best_mask is None:
        poly = _dark_bbox_polygon(gray, w, h)
        return {"polygon_px": _ensure_closed(poly), "bbox_px": _bbox(poly), "method": "dark_bbox"}

    contours, _ = cv2.findContours(best_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        poly = _dark_bbox_polygon(gray, w, h)
        return {"polygon_px": _ensure_closed(poly), "bbox_px": _bbox(poly), "method": "dark_bbox"}

    cnt = max(contours, key=cv2.contourArea)
    peri = cv2.arcLength(cnt, True)
    approx = cv2.approxPolyDP(cnt, max(4.0, 0.012 * peri), True)
    poly = [(int(p[0][0]), int(p[0][1])) for p in approx]

    if len(poly) < 4:
        x, y, bw, bh = cv2.boundingRect(cnt)
        poly = [(x, y), (x + bw, y), (x + bw, y + bh), (x, y + bh)]

    poly = _ensure_closed(poly)
    return {"polygon_px": poly, "bbox_px": _bbox(poly), "method": "morph_contour"}


def _safe_polygon_mask(size: Tuple[int, int], polygon_px: Sequence[Tuple[int, int]], margin_px: int) -> np.ndarray:
    w, h = size
    mask = np.zeros((h, w), dtype=np.uint8)

    if len(polygon_px) < 3:
        mask[:, :] = 255
        return mask

    if cv2 is None:
        x1, y1, x2, y2 = _bbox(polygon_px)
        mask[max(0, y1 + margin_px):min(h, y2 - margin_px), max(0, x1 + margin_px):min(w, x2 - margin_px)] = 255
        return mask

    cv2.fillPoly(mask, [np.array(polygon_px, dtype=np.int32)], 255)
    if margin_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(3, margin_px * 2 + 1), max(3, margin_px * 2 + 1)))
        mask = cv2.erode(mask, k, iterations=1)
    return mask


def detect_wall_lines(img: Image.Image, polygon_px: Sequence[Tuple[int, int]]) -> List[Dict[str, Any]]:
    if cv2 is None:
        return []

    base = _copy_rgb(img)
    w, h = base.size
    arr = np.asarray(base, dtype=np.uint8)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 9
    )

    safe = _safe_polygon_mask((w, h), polygon_px, margin_px=max(2, int(min(w, h) * 0.01)))
    binary = cv2.bitwise_and(binary, safe)

    lines = cv2.HoughLinesP(
        binary,
        rho=1,
        theta=np.pi / 180,
        threshold=max(30, int(min(w, h) * 0.07)),
        minLineLength=max(32, int(min(w, h) * 0.14)),
        maxLineGap=max(8, int(min(w, h) * 0.015)),
    )

    if lines is None:
        return []

    raw_lines: List[Dict[str, Any]] = []
    for line in lines[:, 0, :]:
        x1, y1, x2, y2 = [int(v) for v in line]
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length < max(32, int(min(w, h) * 0.14)):
            continue

        mx = (x1 + x2) / 2.0
        my = (y1 + y2) / 2.0
        if not _point_in_polygon((mx, my), polygon_px):
            continue

        if abs(dx) <= max(6, abs(dy) * 0.18):
            raw_lines.append({
                "orientation": "vertical",
                "coord": float((x1 + x2) / 2.0),
                "start": int(min(y1, y2)),
                "end": int(max(y1, y2)),
                "length": float(length),
            })
        elif abs(dy) <= max(6, abs(dx) * 0.18):
            raw_lines.append({
                "orientation": "horizontal",
                "coord": float((y1 + y2) / 2.0),
                "start": int(min(x1, x2)),
                "end": int(max(x1, x2)),
                "length": float(length),
            })

    tol = max(8, int(min(w, h) * 0.018))
    merged: List[Dict[str, Any]] = []

    for orientation in ("vertical", "horizontal"):
        subset = [l for l in raw_lines if l["orientation"] == orientation]
        buckets: Dict[int, List[Dict[str, Any]]] = {}
        for line in subset:
            key = int(round(line["coord"] / tol))
            buckets.setdefault(key, []).append(line)

        for group in buckets.values():
            coord = float(sum(g["coord"] * g["length"] for g in group) / max(1.0, sum(g["length"] for g in group)))
            start = min(g["start"] for g in group)
            end = max(g["end"] for g in group)
            strength = sum(g["length"] for g in group)
            merged.append({
                "orientation": orientation,
                "coord": coord,
                "start": start,
                "end": end,
                "length": float(end - start),
                "strength": float(strength),
            })

    merged.sort(key=lambda x: (x["orientation"], -x.get("strength", x["length"])))
    return merged


def detect_core_boxes(
    img: Image.Image,
    polygon_px: Sequence[Tuple[int, int]],
    plan_type: str,
    max_cores: int = 2,
) -> List[Tuple[int, int, int, int]]:
    bx1, by1, bx2, by2 = _bbox(polygon_px)
    width = bx2 - bx1
    height = by2 - by1

    if cv2 is None:
        if plan_type in {"basement_open", "ground_open"}:
            return [(int(bx1 + width * 0.66), int(by1 + height * 0.20), int(width * 0.12), int(height * 0.18))]
        return [
            (int(bx1 + width * 0.44), int(by1 + height * 0.30), int(width * 0.15), int(height * 0.18)),
            (int(bx1 + width * 0.60), int(by1 + height * 0.30), int(width * 0.11), int(height * 0.16)),
        ]

    base = _copy_rgb(img)
    w, h = base.size
    arr = np.asarray(base, dtype=np.uint8)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 7
    )
    safe = _safe_polygon_mask((w, h), polygon_px, margin_px=max(2, int(min(w, h) * 0.015)))
    bw = cv2.bitwise_and(bw, safe)

    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(5, int(min(w, h) * 0.025)), max(5, int(min(w, h) * 0.025))),
    )
    mass = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel, iterations=1)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mass, 8)

    cx = (bx1 + bx2) / 2.0
    cy = (by1 + by2) / 2.0
    candidates: List[Tuple[float, Tuple[int, int, int, int]]] = []

    for label in range(1, num_labels):
        x, y, bwc, bhc, area = stats[label]
        area_ratio = area / max(1.0, float(w * h))
        if area_ratio < 0.004 or area_ratio > 0.14:
            continue
        if x <= bx1 + 2 or y <= by1 + 2 or x + bwc >= bx2 - 2 or y + bhc >= by2 - 2:
            continue

        aspect = bwc / max(1.0, float(bhc))
        aspect_penalty = 0.45 if aspect < 0.45 or aspect > 2.8 else 1.0
        dist = math.hypot((x + bwc / 2.0) - cx, (y + bhc / 2.0) - cy) / max(math.hypot(w, h), 1.0)
        centrality = 1.0 - _clamp(dist, 0.0, 1.0)
        score = (area_ratio * 100.0 * 0.55 + centrality * 18.0) * aspect_penalty
        candidates.append((score, (x, y, bwc, bhc)))

    candidates.sort(key=lambda item: item[0], reverse=True)

    out: List[Tuple[int, int, int, int]] = []
    for _, rect in candidates:
        rx, ry, rw, rh = rect
        duplicate = False
        for ox, oy, ow, oh in out:
            ix1 = max(rx, ox)
            iy1 = max(ry, oy)
            ix2 = min(rx + rw, ox + ow)
            iy2 = min(ry + rh, oy + oh)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            union = max(1, rw * rh + ow * oh - inter)
            if inter / union > 0.30:
                duplicate = True
                break
        if not duplicate:
            out.append(rect)
        if len(out) >= max_cores:
            break

    if out:
        return out

    if plan_type in {"basement_open", "ground_open"}:
        return [(int(bx1 + width * 0.66), int(by1 + height * 0.20), int(width * 0.12), int(height * 0.18))]
    if plan_type == "roof":
        return [(int(bx1 + width * 0.45), int(by1 + height * 0.38), int(width * 0.14), int(height * 0.16))]
    return [
        (int(bx1 + width * 0.44), int(by1 + height * 0.30), int(width * 0.15), int(height * 0.18)),
        (int(bx1 + width * 0.60), int(by1 + height * 0.30), int(width * 0.11), int(height * 0.16)),
    ]


def _regularized_axes(start: float, end: float, candidates: Sequence[float], target_count: int, tol: float) -> List[float]:
    if end <= start:
        return []
    target_count = max(2, int(target_count))
    ideals = [start + i * ((end - start) / max(1, target_count - 1)) for i in range(target_count)]
    coords: List[float] = []

    available = sorted(set(float(c) for c in candidates))
    used: set[int] = set()

    for ideal in ideals:
        chosen = ideal
        best_idx = None
        best_dist = 1e9
        for idx, candidate in enumerate(available):
            if idx in used:
                continue
            dist = abs(candidate - ideal)
            if dist <= tol and dist < best_dist:
                best_idx = idx
                best_dist = dist
        if best_idx is not None:
            chosen = available[best_idx]
            used.add(best_idx)
        coords.append(float(chosen))

    coords = sorted(set(round(c, 1) for c in coords))
    if len(coords) < 2:
        return [float(start), float(end)]
    return coords


def propose_structural_axes(
    polygon_px: Sequence[Tuple[int, int]],
    wall_lines: Sequence[Dict[str, Any]],
    plan_type: str,
) -> Dict[str, List[float]]:
    bx1, by1, bx2, by2 = _bbox(polygon_px)
    width = max(1.0, float(bx2 - bx1))
    height = max(1.0, float(by2 - by1))

    mx = max(16.0, width * 0.10)
    my = max(16.0, height * 0.10)

    ax1, ax2 = bx1 + mx, bx2 - mx
    ay1, ay2 = by1 + my, by2 - my

    v_candidates = [
        l["coord"] for l in wall_lines
        if l["orientation"] == "vertical" and l["length"] >= height * 0.28
    ]
    h_candidates = [
        l["coord"] for l in wall_lines
        if l["orientation"] == "horizontal" and l["length"] >= width * 0.28
    ]

    if plan_type in {"basement_open", "ground_open"}:
        target_x = 5 if width >= height and width / max(height, 1.0) > 1.35 else 4
        target_y = 4 if width >= height else 5
        target_y = max(3, target_y)
    elif plan_type == "roof":
        target_x, target_y = 2, 2
    else:
        target_x, target_y = 3, 3

    axes_x = _regularized_axes(ax1, ax2, v_candidates, target_x, tol=max(16.0, width * 0.08))
    axes_y = _regularized_axes(ay1, ay2, h_candidates, target_y, tol=max(16.0, height * 0.08))

    if plan_type == "residential":
        if len(axes_x) > 3:
            axes_x = [axes_x[0], axes_x[len(axes_x) // 2], axes_x[-1]]
        if len(axes_y) > 3:
            axes_y = [axes_y[0], axes_y[len(axes_y) // 2], axes_y[-1]]

    if plan_type == "roof":
        axes_x = [axes_x[0], axes_x[-1]] if len(axes_x) >= 2 else axes_x
        axes_y = [axes_y[0], axes_y[-1]] if len(axes_y) >= 2 else axes_y

    return {"x": axes_x, "y": axes_y}


def _point_hits_box(px: float, py: float, boxes: Sequence[Tuple[int, int, int, int]], pad: float = 0.0) -> bool:
    for x, y, w, h in boxes:
        if x - pad <= px <= x + w + pad and y - pad <= py <= y + h + pad:
            return True
    return False


def pick_column_points(
    img_size: Tuple[int, int],
    polygon_px: Sequence[Tuple[int, int]],
    axes: Dict[str, List[float]],
    core_boxes: Sequence[Tuple[int, int, int, int]],
    plan_type: str,
) -> List[Tuple[float, float]]:
    if plan_type not in {"basement_open", "ground_open"}:
        return []

    w, h = img_size
    safe = _safe_polygon_mask((w, h), polygon_px, margin_px=max(10, int(min(w, h) * 0.035)))

    columns: List[Tuple[float, float]] = []
    for y in axes.get("y", []):
        for x in axes.get("x", []):
            ix, iy = int(round(x)), int(round(y))
            if ix < 0 or iy < 0 or ix >= w or iy >= h:
                continue
            if safe[iy, ix] == 0:
                continue
            if _point_hits_box(ix, iy, core_boxes, pad=max(8.0, min(w, h) * 0.03)):
                continue
            if any(math.hypot(ix - px, iy - py) < max(18.0, min(w, h) * 0.04) for px, py in columns):
                continue
            columns.append((float(ix), float(iy)))
    return columns


def _polygon_wall_elements(points: Sequence[Tuple[int, int]], img_w: int, img_h: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    closed = _ensure_closed(list(points))
    for a, b in zip(closed[:-1], closed[1:]):
        x1, y1 = _px_to_rel(a[0], a[1], img_w, img_h)
        x2, y2 = _px_to_rel(b[0], b[1], img_w, img_h)
        out.append({"type": "wall", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "label": "Yttervegg"})
    return out


def _core_elements(core_boxes: Sequence[Tuple[int, int, int, int]], img_w: int, img_h: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, (x, y, w, h) in enumerate(core_boxes, start=1):
        rx, ry = _px_to_rel(x, y, img_w, img_h)
        rw = _clamp(w / max(1.0, float(img_w)), 0.03, 0.40)
        rh = _clamp(h / max(1.0, float(img_h)), 0.04, 0.40)
        out.append({"type": "core", "x": rx, "y": ry, "w": rw, "h": rh, "label": f"K{i}"})
    return out


def _grid_elements(axes: Dict[str, List[float]], img_w: int, img_h: int, plan_type: str) -> List[Dict[str, Any]]:
    if plan_type == "roof":
        return []

    out: List[Dict[str, Any]] = []
    for i, x in enumerate(axes.get("x", [])[:6], start=1):
        rx, _ = _px_to_rel(x, 0, img_w, img_h)
        out.append({"type": "grid", "orientation": "vertical", "x": rx, "label": chr(64 + i)})
    for i, y in enumerate(axes.get("y", [])[:6], start=1):
        _, ry = _px_to_rel(0, y, img_w, img_h)
        out.append({"type": "grid", "orientation": "horizontal", "y": ry, "label": str(i)})
    return out


def _column_elements(columns: Sequence[Tuple[float, float]], img_w: int, img_h: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, (x, y) in enumerate(columns, start=1):
        rx, ry = _px_to_rel(x, y, img_w, img_h)
        out.append({"type": "column", "x": rx, "y": ry, "label": f"C{i}"})
    return out


def _interior_wall_elements(
    wall_lines: Sequence[Dict[str, Any]],
    img_w: int,
    img_h: int,
    plan_type: str,
    max_walls: int = 3,
) -> List[Dict[str, Any]]:
    if plan_type in {"basement_open", "ground_open"}:
        return []

    ranked = sorted(wall_lines, key=lambda l: l.get("strength", l.get("length", 0.0)), reverse=True)
    out: List[Dict[str, Any]] = []

    for line in ranked[:max_walls]:
        if line["orientation"] == "vertical":
            x = line["coord"]
            y1 = line["start"]
            y2 = line["end"]
            rx1, ry1 = _px_to_rel(x, y1, img_w, img_h)
            rx2, ry2 = _px_to_rel(x, y2, img_w, img_h)
            out.append({"type": "wall", "x1": rx1, "y1": ry1, "x2": rx2, "y2": ry2, "label": "Bærende vegg / skive"})
        else:
            y = line["coord"]
            x1 = line["start"]
            x2 = line["end"]
            rx1, ry1 = _px_to_rel(x1, y, img_w, img_h)
            rx2, ry2 = _px_to_rel(x2, y, img_w, img_h)
            out.append({"type": "wall", "x1": rx1, "y1": ry1, "x2": rx2, "y2": ry2, "label": "Bærende vegg / skive"})

    return out


def _beam_and_span_elements(
    axes: Dict[str, List[float]],
    img_w: int,
    img_h: int,
    plan_type: str,
) -> List[Dict[str, Any]]:
    xs = axes.get("x", [])
    ys = axes.get("y", [])
    if plan_type not in {"basement_open", "ground_open"} or len(xs) < 2 or not ys:
        return []

    y = ys[len(ys) // 2]
    x1 = xs[0]
    x2 = xs[-1]

    rx1, ry = _px_to_rel(x1, y, img_w, img_h)
    rx2, _ = _px_to_rel(x2, y, img_w, img_h)

    sx1, sy = _px_to_rel(xs[0], max(ys) + max(10.0, (max(ys) - min(ys)) * 0.08), img_w, img_h)
    sx2, _ = _px_to_rel(xs[1], max(ys) + max(10.0, (max(ys) - min(ys)) * 0.08), img_w, img_h)

    return [
        {"type": "beam", "x1": rx1, "y1": ry, "x2": rx2, "y2": ry, "label": "Primær søylerekke"},
        {"type": "span_arrow", "x1": sx1, "y1": sy, "x2": sx2, "y2": sy, "label": "typisk 6-8 m"},
    ]


def generate_rib_sketch(
    record: Dict[str, Any],
    concept_name: str,
    recommended_system: Optional[Dict[str, Any]] = None,
    ai_notes: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(record, dict):
        return None

    img = record.get("analysis_image") or record.get("image")
    if img is None:
        return None

    base = _copy_rgb(img)
    img_w, img_h = base.size

    plan_type = classify_plan_type(
        record.get("name", ""),
        record.get("label", ""),
        ai_notes,
        base,
    )

    contour = detect_building_contour(base)
    polygon_px = contour.get("polygon_px") or [(0, 0), (img_w - 1, 0), (img_w - 1, img_h - 1), (0, img_h - 1)]
    wall_lines = detect_wall_lines(base, polygon_px)
    core_boxes = detect_core_boxes(base, polygon_px, plan_type=plan_type, max_cores=2)
    axes = propose_structural_axes(polygon_px, wall_lines, plan_type=plan_type)
    column_points = pick_column_points((img_w, img_h), polygon_px, axes, core_boxes, plan_type=plan_type)

    elements: List[Dict[str, Any]] = []
    elements.extend(_polygon_wall_elements(polygon_px, img_w, img_h))
    elements.extend(_core_elements(core_boxes, img_w, img_h))
    elements.extend(_interior_wall_elements(wall_lines, img_w, img_h, plan_type=plan_type))
    elements.extend(_grid_elements(axes, img_w, img_h, plan_type=plan_type))
    elements.extend(_column_elements(column_points, img_w, img_h))
    elements.extend(_beam_and_span_elements(axes, img_w, img_h, plan_type=plan_type))

    notes = [
        f"Plan klassifisert som {plan_type}.",
        "Bygningskontur er detektert før bærende punkter er satt.",
    ]

    if plan_type in {"basement_open", "ground_open"}:
        notes.append("Søyler er kun lagt i aksekryss innenfor verifisert polygon og med sikker avstand til yttervegg.")
        notes.append("Gridet er regularisert mot rasjonell og repeterbar spennlogikk, med kjerner som primær avstivning.")
    elif plan_type == "residential":
        notes.append("Boligplan prioriterer bærende vegger og kjerner. Søylebruk er bevisst tonet ned.")
    elif plan_type == "roof":
        notes.append("Takplan skisseres via lastvei til underliggende vegger og kjerner, ikke som nytt søylesystem.")
    else:
        notes.append("Uklart grunnlag gir konservativ skisse med få bærende punkter og tydelig usikkerhet.")

    for note in ai_notes or []:
        txt = _clean(note)
        if txt and txt not in notes:
            notes.append(txt)

    return {
        "page_index": int(record.get("page_index", 0)),
        "page_label": _clean(record.get("label") or record.get("name") or "Plan"),
        "notes": notes[:5],
        "elements": elements,
        "meta": {
            "plan_type": plan_type,
            "contour_method": contour.get("method", "unknown"),
            "wall_count": len(wall_lines),
            "column_count": len(column_points),
        },
    }


def generate_rib_sketches_for_analysis(
    drawings: Sequence[Dict[str, Any]],
    analysis_result: Dict[str, Any],
    max_sketches: int = 3,
) -> List[Dict[str, Any]]:
    recommended_system = (analysis_result or {}).get("recommended_system", {}) or {}
    concept_name = recommended_system.get("system_name", "Anbefalt system")

    ai_sketch_map: Dict[int, Dict[str, Any]] = {}
    for sketch in (analysis_result or {}).get("sketches", []):
        if not isinstance(sketch, dict):
            continue
        try:
            page_index = int(sketch.get("page_index"))
        except Exception:
            continue
        ai_sketch_map[page_index] = sketch

    plan_records = [r for r in drawings if str(r.get("hint", "")).lower() == "plan"]
    if not plan_records:
        plan_records = list(drawings)

    out: List[Dict[str, Any]] = []
    for record in plan_records[:max_sketches]:
        ai_notes = []
        ai_sketch = ai_sketch_map.get(int(record.get("page_index", -1)))
        if ai_sketch:
            ai_notes = ai_sketch.get("notes", []) or []

        sketch = generate_rib_sketch(
            record,
            concept_name=concept_name,
            recommended_system=recommended_system,
            ai_notes=ai_notes,
        )
        if sketch:
            out.append(sketch)

    return outtry:
    from rib_geometry_engine import generate_rib_sketches_for_analysis
except Exception:
    generate_rib_sketches_for_analysis = Nonedef build_overlay_package(
    drawings: List[Dict[str, Any]],
    analysis_result: Dict[str, Any],
    max_sketches: int = 3,
) -> List[Dict[str, Any]]:
    concept_name = analysis_result.get("recommended_system", {}).get("system_name", "Anbefalt system")
    out: List[Dict[str, Any]] = []

    sketches: List[Dict[str, Any]] = []

    # Geometrimotoren er autoritativ for plan-skisser.
    # AI kan fortsatt velge system og skrive notater, men ikke plassere søyler fritt.
    if generate_rib_sketches_for_analysis is not None and drawings and analysis_result.get("grunnlag_status") != "FOR_SVAKT":
        try:
            sketches = generate_rib_sketches_for_analysis(
                drawings=drawings,
                analysis_result=analysis_result,
                max_sketches=max_sketches,
            )
        except Exception:
            sketches = []

    # Hvis geometrimotoren ikke gir resultat, bruk eksisterende AI-skisser.
    if not sketches:
        sketches = analysis_result.get("sketches", [])[:max_sketches]

    # Siste fallback.
    if not sketches and drawings and analysis_result.get("grunnlag_status") != "FOR_SVAKT":
        fallback = build_fallback_sketch(drawings, concept_name)
        if fallback:
            sketches = [fallback]

    for sketch in sketches:
        record = lookup_record_by_page(drawings, int(sketch.get("page_index", -1)))
        if record is None:
            continue

        overlay = render_overlay_image(
            record,
            sketch,
            concept_name,
            analysis_result.get("grunnlag_status", "-"),
        )

        out.append(
            {
                "page_index": record["page_index"],
                "caption": clean_pdf_text(
                    f"Konseptskisse på {record['name']}{' (auto-crop)' if record.get('analysis_crop_applied') else ''} - {short_text(sketch.get('page_label', record['label']), 80)}"
                ),
                "image": overlay,
            }
        )

    return out
