# -*- coding: utf-8 -*-
"""
builtly_click_editor.py
=======================
Docker-kompatibel klikk-editor for RIB-modulen.

Bruker st.components.v1.declare_component med en stabil mappe
som inneholder all HTML/JS. Fungerer i Docker, Render og lokalt.

v15: Fikset komponent-lasting i Docker/Render ved å bruke stabil sti
     i stedet for tempfile.mkdtemp (som ikke alltid er tilgjengelig
     for Streamlit sin statiske filserver).

Bruk:
    from builtly_click_editor import render_click_editor
    click = render_click_editor(image, key="editor_1")
    if click:
        print(f"Klikk: x={click['x']}, y={click['y']}")
"""
from __future__ import annotations

import base64
import io
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st

try:
    import streamlit.components.v1 as components
except Exception:
    components = None

from PIL import Image

# ---- Component cache ----
_COMPONENT_DIR: Optional[Path] = None
_COMPONENT_FUNC: Optional[Any] = None


def _get_component_dir() -> Path:
    """Create (once) a directory with the editor's index.html.

    v15: Tries multiple locations in priority order:
    1. Subdirectory next to this script file (most reliable in Docker)
    2. Subdirectory in current working directory
    3. Temp directory (original approach, less reliable in Docker)
    """
    global _COMPONENT_DIR
    if _COMPONENT_DIR is not None and _COMPONENT_DIR.exists():
        # Verify the index.html still exists
        if (_COMPONENT_DIR / "index.html").exists():
            return _COMPONENT_DIR

    # Priority 1: next to the script file
    script_dir = Path(__file__).parent
    candidates = [
        script_dir / "_builtly_click_editor_component",
        Path.cwd() / "_builtly_click_editor_component",
    ]

    for candidate_dir in candidates:
        try:
            candidate_dir.mkdir(parents=True, exist_ok=True)
            index_html = candidate_dir / "index.html"
            index_html.write_text(_EDITOR_HTML, encoding="utf-8")
            _COMPONENT_DIR = candidate_dir
            return candidate_dir
        except (OSError, PermissionError):
            continue

    # Priority 3: temp directory (original approach)
    try:
        tmpdir = Path(tempfile.mkdtemp(prefix="builtly_click_editor_"))
        index_html = tmpdir / "index.html"
        index_html.write_text(_EDITOR_HTML, encoding="utf-8")
        _COMPONENT_DIR = tmpdir
        return tmpdir
    except Exception:
        pass

    # Last resort: use /tmp directly
    fallback = Path("/tmp/builtly_click_editor_component")
    fallback.mkdir(parents=True, exist_ok=True)
    index_html = fallback / "index.html"
    index_html.write_text(_EDITOR_HTML, encoding="utf-8")
    _COMPONENT_DIR = fallback
    return fallback


def _get_component():
    """Get or create the Streamlit component."""
    global _COMPONENT_FUNC
    if _COMPONENT_FUNC is not None:
        return _COMPONENT_FUNC
    if components is None:
        return None

    try:
        comp_dir = _get_component_dir()
        _COMPONENT_FUNC = components.declare_component(
            "builtly_click_editor",
            path=str(comp_dir),
        )
        return _COMPONENT_FUNC
    except Exception:
        return None


def _image_to_data_url(img: Image.Image, max_width: int = 1400) -> str:
    """Convert PIL image to base64 data URL, resizing if needed."""
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def render_click_editor(
    image: Image.Image,
    key: str = "builtly_click_editor_0",
    height: int = 600,
    status_text: str = "Klikk i planutsnittet for å plassere eller flytte elementer.",
) -> Optional[Dict[str, float]]:
    """
    Render an interactive click editor.
    Returns {"x": float, "y": float, "event_id": str} on click, or None.
    Coordinates are in original image pixels.
    """
    comp = _get_component()
    if comp is None:
        # Ultimate fallback: show static image
        st.image(image, use_container_width=True)
        st.caption("Interaktiv editor er ikke tilgjengelig. Bruk tabellredigering.")
        return None

    data_url = _image_to_data_url(image)

    try:
        result = comp(
            image_data=data_url,
            natural_width=image.width,
            natural_height=image.height,
            status_text=status_text,
            key=key,
            default=None,
        )
    except Exception:
        # v15: If the component fails to render, reset and show fallback
        global _COMPONENT_FUNC
        _COMPONENT_FUNC = None
        st.image(image, use_container_width=True)
        st.caption("Interaktiv editor feilet under lasting. Bruk tabellredigering.")
        return None

    if isinstance(result, dict) and "x" in result and "y" in result:
        return {
            "x": float(result["x"]),
            "y": float(result["y"]),
            "event_id": str(result.get("event_id", "")),
        }
    return None


# ---- The actual editor HTML/JS ----
_EDITOR_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: transparent; overflow: hidden; }
#wrap {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 4px;
}
canvas {
    width: 100%;
    height: auto;
    display: block;
    cursor: crosshair;
    border-radius: 8px;
    background: #0a1520;
}
#status {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 12px;
    line-height: 1.4;
    color: rgba(200, 210, 220, 0.7);
    padding: 2px 4px;
}
</style>
</head>
<body>
<div id="wrap">
    <canvas id="canvas"></canvas>
    <div id="status">Laster editor...</div>
</div>
<script>
(function() {
    const canvas = document.getElementById('canvas');
    const status = document.getElementById('status');
    const ctx = canvas.getContext('2d');
    let img = null;
    let lastClick = null;
    let naturalW = 960;
    let naturalH = 540;

    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

    function drawCrosshair(x, y) {
        ctx.save();
        ctx.strokeStyle = 'rgba(255,255,255,0.95)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, 10, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(x - 14, y);
        ctx.lineTo(x + 14, y);
        ctx.moveTo(x, y - 14);
        ctx.lineTo(x, y + 14);
        ctx.stroke();
        // Shadow for visibility on light backgrounds
        ctx.strokeStyle = 'rgba(0,0,0,0.4)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(x, y, 11, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
    }

    function render() {
        canvas.width = naturalW;
        canvas.height = naturalH;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (img) {
            ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        } else {
            ctx.fillStyle = '#0a1520';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.fillStyle = '#dbe7f0';
            ctx.font = '16px sans-serif';
            ctx.fillText('Laster bilde...', 24, 34);
        }
        if (lastClick) {
            drawCrosshair(lastClick.x, lastClick.y);
        }
    }

    function getClickCoords(event) {
        const rect = canvas.getBoundingClientRect();
        const clientX = event.clientX !== undefined ? event.clientX :
                        (event.touches && event.touches.length ? event.touches[0].clientX : 0);
        const clientY = event.clientY !== undefined ? event.clientY :
                        (event.touches && event.touches.length ? event.touches[0].clientY : 0);
        const scaleX = canvas.width / Math.max(rect.width, 1);
        const scaleY = canvas.height / Math.max(rect.height, 1);
        return {
            x: clamp((clientX - rect.left) * scaleX, 0, canvas.width),
            y: clamp((clientY - rect.top) * scaleY, 0, canvas.height),
        };
    }

    function handleClick(event) {
        if (event.preventDefault) event.preventDefault();
        if (event.stopPropagation) event.stopPropagation();
        const point = getClickCoords(event);
        const payload = {
            x: Math.round(point.x * 100) / 100,
            y: Math.round(point.y * 100) / 100,
            event_id: String(Date.now()) + '-' + Math.random().toString(36).slice(2, 8),
        };
        lastClick = payload;
        render();
        status.textContent = 'Klikk registrert: x=' + payload.x.toFixed(1) + ', y=' + payload.y.toFixed(1);

        // Send to Streamlit
        if (window.Streamlit) {
            window.Streamlit.setComponentValue(payload);
        }
    }

    canvas.addEventListener('pointerdown', handleClick);
    canvas.addEventListener('touchstart', handleClick, { passive: false });

    // Receive data from Streamlit
    function onRender(event) {
        if (!event || !event.detail) return;
        const data = event.detail.args || {};
        const imageData = data.image_data || '';
        naturalW = Number(data.natural_width || 960);
        naturalH = Number(data.natural_height || 540);
        status.textContent = data.status_text || 'Klikk i planutsnittet.';

        if (imageData && (!img || img._src !== imageData)) {
            const newImg = new Image();
            newImg._src = imageData;
            newImg.onload = function() {
                img = newImg;
                render();
            };
            newImg.onerror = function() {
                img = null;
                render();
                status.textContent = 'Kunne ikke laste bildet.';
            };
            newImg.src = imageData;
        } else {
            render();
        }

        if (window.Streamlit) {
            window.Streamlit.setFrameHeight();
        }
    }

    // Streamlit component protocol
    if (window.Streamlit) {
        window.Streamlit.events.addEventListener(
            window.Streamlit.RENDER_EVENT, onRender
        );
        window.Streamlit.setComponentReady();
        window.Streamlit.setFrameHeight();
    } else {
        // Fallback: listen for message events
        window.addEventListener('message', function(e) {
            if (e.data && e.data.type === 'streamlit:render') {
                onRender({ detail: e.data });
            }
        });
    }
})();
</script>
</body>
</html>"""
