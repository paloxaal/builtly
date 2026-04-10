"""
builtly_rib_editor_bridge.py
─────────────────────────────
Drop-in replacement for render_inline_click_canvas_editor in Konstruksjon.py.

Renders the new BuiltlyRIBEditor (React/SVG) as a Streamlit bi-directional
component. The editor sends back the full sketch elements array in the same
normalized (0–1) format that Konstruksjon.py already uses, so it plugs
straight into:
  - sketch_elements_to_editor_df()
  - build_structural_system_candidates()
  - run_structured_drawing_analysis()
  - render_overlay_image()

INTEGRATION (in Konstruksjon.py):
─────────────────────────────────
1. Add import:
       from builtly_rib_editor_bridge import render_rib_editor

2. Replace every call to render_inline_click_canvas_editor() and
   render_plotly_sketch_editor() with render_rib_editor().

3. The old single-click + apply_click_edit_to_sketch() flow is no longer
   needed — the editor handles all edits internally and returns the
   complete element set.

Example replacement (around line 5285 in Konstruksjon.py):
───────────────────────────────────────────────────────────
BEFORE:
    click = render_plotly_sketch_editor(drawing_record, selected_sketch, editor_key=...)
    if click and tool != "none":
        click_sig = click_event_signature(selected_sketch_uid, tool, click)
        if st.session_state.get("rib_draft_last_click_sig") != click_sig:
            changed, message, updated_sketch = apply_click_edit_to_sketch(...)
            ...

AFTER:
    updated_elements = render_rib_editor(drawing_record, selected_sketch, editor_key=...)
    if updated_elements is not None:
        draft_sketches[sketch_idx]["elements"] = updated_elements
        st.session_state.rib_draft_sketches = draft_sketches
        st.session_state.rib_draft_updated_at = datetime.now().isoformat()
        st.success(f"Bæresystem oppdatert — {len(updated_elements)} elementer.")
        st.rerun()
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import streamlit as st
    import streamlit.components.v1 as components
except ImportError:
    st = None
    components = None

try:
    from PIL import Image
except ImportError:
    Image = None


# ─── Component directory ────────────────────────────────────────────

DB_DIR = Path(os.environ.get("BUILTLY_DB_DIR", "qa_database"))
_COMPONENT_DIR = DB_DIR / "_inline_components" / "rib_editor_v2"
_VERSION_MARKER = "Builtly RIB Editor v2.0"


def _image_to_data_uri(img: Image.Image, max_dim: int = 1600) -> str:
    """Convert PIL Image to base64 data URI, resizing if needed."""
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _ensure_component_html() -> Path:
    """Write the editor's index.html (wrapping the React component as
    a Streamlit bi-directional component).
    """
    _COMPONENT_DIR.mkdir(parents=True, exist_ok=True)
    index_path = _COMPONENT_DIR / "index.html"

    # Only rewrite if version changed
    if index_path.exists():
        try:
            if _VERSION_MARKER in index_path.read_text("utf-8", errors="ignore"):
                return _COMPONENT_DIR
        except Exception:
            pass

    html = f"""<!DOCTYPE html>
<html lang="no">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <!-- {_VERSION_MARKER} -->
  <style>
    html, body {{ margin: 0; padding: 0; overflow: hidden; background: #06111a; }}
    #root {{ width: 100%; height: 100%; }}
  </style>
</head>
<body>
  <div id="root"></div>
  <script>
    // ─── Streamlit Component Protocol ───
    const root = document.getElementById("root");
    let argsState = {{}};
    let editorElements = [];

    function sendMessage(type, data) {{
      window.parent.postMessage(
        Object.assign({{ isStreamlitMessage: true, type: type }}, data || {{}}),
        "*"
      );
    }}

    function setReady() {{ sendMessage("streamlit:componentReady", {{ apiVersion: 1 }}); }}
    function setHeight(h) {{ sendMessage("streamlit:setFrameHeight", {{ height: h }}); }}
    function setValue(val) {{ sendMessage("streamlit:setComponentValue", {{ value: val }}); }}

    // ─── Minimal editor (inline SVG version for Streamlit) ───
    // This embeds the same logic as BuiltlyRIBEditor_v2.jsx but in
    // vanilla JS for direct Streamlit component use without a React build step.

    const GRID = 16;
    const snap = v => Math.round(v / GRID) * GRID;
    const uid = () => Math.random().toString(36).slice(2, 8);
    const dist = (x1, y1, x2, y2) => Math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2);

    let state = {{
      tool: "bearing_wall",
      elements: [],
      selected: null,
      drawing: null,
      material: "concrete",
      bgImage: null,
      bgOpacity: 0.45,
    }};

    const TOOLS = [
      {{ id: "select", label: "Velg", key: "V" }},
      {{ id: "bearing_wall", label: "Bærevegg", key: "B" }},
      {{ id: "wall", label: "Lett vegg", key: "W" }},
      {{ id: "column", label: "Søyle", key: "S" }},
      {{ id: "beam", label: "Bjelke", key: "J" }},
      {{ id: "core", label: "Kjerne", key: "K" }},
    ];

    let canvasW = 960, canvasH = 640;

    function applyArgs(args) {{
      argsState = args || {{}};
      canvasW = Number(args.natural_width || 960);
      canvasH = Number(args.natural_height || 640);

      // Load background image
      if (args.image_data) {{
        state.bgImage = args.image_data;
      }}

      // Load initial elements (convert from normalized to canvas px)
      if (args.initial_elements && Array.isArray(args.initial_elements) && state.elements.length === 0) {{
        state.elements = args.initial_elements.map(el => {{
          const out = {{ ...el, id: uid() }};
          if (el.type === "column" || el.type === "load") {{
            out.cx = (el.x || 0) * canvasW;
            out.cy = (el.y || 0) * canvasH;
          }} else if (el.type === "core") {{
            out.cx = (el.x || 0) * canvasW;
            out.cy = (el.y || 0) * canvasH;
            out.cw = (el.w || 0.1) * canvasW;
            out.ch = (el.h || 0.1) * canvasH;
          }} else if (el.type === "grid") {{
            if (el.orientation === "vertical") {{
              out.cx1 = (el.x || 0) * canvasW;
              out.cy1 = 0; out.cx2 = out.cx1; out.cy2 = canvasH;
            }} else {{
              out.cy1 = (el.y || 0) * canvasH;
              out.cx1 = 0; out.cx2 = canvasW; out.cy2 = out.cy1;
            }}
          }} else {{
            out.cx1 = (el.x1 || 0) * canvasW;
            out.cy1 = (el.y1 || 0) * canvasH;
            out.cx2 = (el.x2 || 0) * canvasW;
            out.cy2 = (el.y2 || 0) * canvasH;
          }}
          return out;
        }});
      }}

      render();
    }}

    function exportElements() {{
      return state.elements.map(el => {{
        if (el.type === "column") {{
          return {{ type: "column", x: el.cx / canvasW, y: el.cy / canvasH, label: el.label || "C", material: el.material }};
        }}
        if (el.type === "core") {{
          return {{ type: "core", x: el.cx / canvasW, y: el.cy / canvasH, w: el.cw / canvasW, h: el.ch / canvasH, label: el.label || "Kjerne" }};
        }}
        if (el.type === "grid") {{
          if (el.orientation === "vertical") return {{ type: "grid", orientation: "vertical", x: el.cx1 / canvasW, label: el.label }};
          return {{ type: "grid", orientation: "horizontal", y: el.cy1 / canvasH, label: el.label }};
        }}
        if (el.type === "load") {{
          return {{ type: "load", x: el.cx / canvasW, y: el.cy / canvasH, value: el.value, unit: el.unit }};
        }}
        return {{
          type: el.type, x1: el.cx1 / canvasW, y1: el.cy1 / canvasH,
          x2: el.cx2 / canvasW, y2: el.cy2 / canvasH,
          label: el.label || "", bearing: el.bearing, material: el.material,
        }};
      }});
    }}

    function commitToStreamlit() {{
      const elements = exportElements();
      setValue({{
        elements: elements,
        updated_at: new Date().toISOString(),
        element_count: elements.length,
      }});
    }}

    function render() {{
      // Simplified render — builds SVG string
      let svg = `<svg viewBox="0 0 ${{canvasW}} ${{canvasH}}" style="width:100%;cursor:${{state.tool === 'select' ? 'default' : 'crosshair'}};background:#0d1b2a" id="editorSvg">`;
      svg += `<defs><pattern id="g" width="${{GRID}}" height="${{GRID}}" patternUnits="userSpaceOnUse"><path d="M ${{GRID}} 0 L 0 0 0 ${{GRID}}" fill="none" stroke="#162231" stroke-width="0.4"/></pattern></defs>`;

      if (state.bgImage) {{
        svg += `<image href="${{state.bgImage}}" x="0" y="0" width="${{canvasW}}" height="${{canvasH}}" opacity="${{state.bgOpacity}}" preserveAspectRatio="xMidYMid meet"/>`;
      }}
      svg += `<rect width="${{canvasW}}" height="${{canvasH}}" fill="url(#g)"/>`;

      // Render elements
      for (const el of state.elements) {{
        const sel = el.id === state.selected;
        const hi = sel ? "#fbbf24" : null;

        if (el.type === "column") {{
          svg += `<circle cx="${{el.cx}}" cy="${{el.cy}}" r="14" fill="#94a3b8" stroke="${{hi || '#64748b'}}" stroke-width="${{sel ? 3 : 2}}"/>`;
          svg += `<circle cx="${{el.cx}}" cy="${{el.cy}}" r="5" fill="#64748b"/>`;
          svg += `<text x="${{el.cx}}" y="${{el.cy - 18}}" text-anchor="middle" fill="#64748b" font-size="10" font-family="monospace" font-weight="700">${{el.label || 'C'}}</text>`;
        }}
        else if (el.type === "wall") {{
          const w = el.bearing ? 10 : 4;
          const c = el.bearing ? (hi || "#64748b") : (hi || "#475569");
          svg += `<line x1="${{el.cx1}}" y1="${{el.cy1}}" x2="${{el.cx2}}" y2="${{el.cy2}}" stroke="${{c}}" stroke-width="${{w}}" stroke-linecap="round"/>`;
        }}
        else if (el.type === "beam") {{
          svg += `<line x1="${{el.cx1}}" y1="${{el.cy1}}" x2="${{el.cx2}}" y2="${{el.cy2}}" stroke="${{hi || '#f59e0b'}}" stroke-width="4" stroke-linecap="round"/>`;
        }}
        else if (el.type === "core") {{
          svg += `<rect x="${{el.cx}}" y="${{el.cy}}" width="${{el.cw}}" height="${{el.ch}}" fill="rgba(100,116,139,0.2)" stroke="${{hi || '#64748b'}}" stroke-width="2" stroke-dasharray="6 3"/>`;
          svg += `<text x="${{el.cx + el.cw / 2}}" y="${{el.cy + el.ch / 2 + 4}}" text-anchor="middle" fill="#94a3b8" font-size="10" font-weight="600">KJERNE</text>`;
        }}
        else if (el.type === "grid") {{
          svg += `<line x1="${{el.cx1}}" y1="${{el.cy1}}" x2="${{el.cx2}}" y2="${{el.cy2}}" stroke="rgba(56,189,248,0.2)" stroke-width="1" stroke-dasharray="8 6"/>`;
        }}
      }}

      svg += `</svg>`;

      // Toolbar
      let toolbar = `<div style="display:flex;gap:4px;padding:8px;background:#0a1929;border-bottom:1px solid #1a2a3a;flex-wrap:wrap">`;
      for (const t of TOOLS) {{
        const active = state.tool === t.id;
        toolbar += `<button onclick="setTool('${{t.id}}')" style="padding:4px 12px;border-radius:5px;border:none;font-size:11px;font-weight:600;cursor:pointer;background:${{active ? '#38bdf8' : '#1a2a3a'}};color:${{active ? '#06111a' : '#94a3b8'}}">${{t.label}} (${{t.key}})</button>`;
      }}
      toolbar += `<div style="flex:1"></div>`;
      toolbar += `<button onclick="commitToStreamlit()" style="padding:4px 14px;border-radius:5px;border:none;background:#38bdf8;color:#06111a;font-weight:700;font-size:11px;cursor:pointer">Lagre bæresystem →</button>`;
      toolbar += `</div>`;

      // Status
      let status = `<div style="padding:4px 8px;font-size:10px;color:#475569;font-family:monospace;background:#0a1929;border-top:1px solid #1a2a3a">${{state.elements.length}} elementer | Klikk Lagre for å oppdatere Konstruksjon.py</div>`;

      root.innerHTML = toolbar + svg + status;
      setHeight(Math.ceil(root.getBoundingClientRect().height + 4));

      // Attach SVG event listeners
      const svgEl = document.getElementById("editorSvg");
      if (svgEl) {{
        svgEl.addEventListener("mousedown", handleDown);
        svgEl.addEventListener("mousemove", handleMove);
        svgEl.addEventListener("mouseup", handleUp);
      }}
    }}

    // Make setTool global for inline onclick
    window.setTool = function(id) {{ state.tool = id; render(); }};
    window.commitToStreamlit = commitToStreamlit;

    function getPos(e) {{
      const rect = document.getElementById("editorSvg").getBoundingClientRect();
      const sx = canvasW / rect.width;
      return {{ x: snap((e.clientX - rect.left) * sx), y: snap((e.clientY - rect.top) * sx) }};
    }}

    function handleDown(e) {{
      const pos = getPos(e);
      if (state.tool === "select") {{
        // Simple hit test
        let hit = null;
        for (let i = state.elements.length - 1; i >= 0; i--) {{
          const el = state.elements[i];
          if (el.cx !== undefined && dist(pos.x, pos.y, el.cx, el.cy) < 20) {{ hit = el; break; }}
          if (el.cx1 !== undefined) {{
            const dx = el.cx2 - el.cx1, dy = el.cy2 - el.cy1;
            const lenSq = dx * dx + dy * dy;
            let t = lenSq === 0 ? 0 : ((pos.x - el.cx1) * dx + (pos.y - el.cy1) * dy) / lenSq;
            t = Math.max(0, Math.min(1, t));
            if (dist(pos.x, pos.y, el.cx1 + t * dx, el.cy1 + t * dy) < 14) {{ hit = el; break; }}
          }}
        }}
        state.selected = hit ? hit.id : null;
        render();
        return;
      }}
      if (state.tool === "column") {{
        const count = state.elements.filter(e => e.type === "column").length;
        state.elements.push({{ id: uid(), type: "column", cx: pos.x, cy: pos.y, label: `C${{count + 1}}`, material: state.material }});
        render();
        return;
      }}
      state.drawing = {{ startX: pos.x, startY: pos.y, curX: pos.x, curY: pos.y }};
    }}

    function handleMove(e) {{
      if (state.drawing) {{
        const pos = getPos(e);
        state.drawing.curX = pos.x;
        state.drawing.curY = pos.y;
      }}
    }}

    function handleUp(e) {{
      if (!state.drawing) return;
      const d = state.drawing;
      const len = dist(d.startX, d.startY, d.curX, d.curY);
      const w = Math.abs(d.curX - d.startX), h = Math.abs(d.curY - d.startY);

      if (state.tool === "core" && w > GRID && h > GRID) {{
        state.elements.push({{
          id: uid(), type: "core",
          cx: Math.min(d.startX, d.curX), cy: Math.min(d.startY, d.curY),
          cw: w, ch: h, material: "concrete", label: "Kjerne",
        }});
      }} else if (len > 10 && ["bearing_wall", "wall", "beam"].includes(state.tool)) {{
        state.elements.push({{
          id: uid(),
          type: state.tool === "bearing_wall" ? "wall" : state.tool,
          cx1: d.startX, cy1: d.startY, cx2: d.curX, cy2: d.curY,
          material: state.material,
          bearing: state.tool === "bearing_wall",
          label: "",
        }});
      }}
      state.drawing = null;
      render();
    }}

    // Keyboard
    document.addEventListener("keydown", function(e) {{
      const t = TOOLS.find(t => t.key === e.key.toUpperCase());
      if (t) {{ state.tool = t.id; render(); }}
      if ((e.key === "Delete" || e.key === "Backspace") && state.selected) {{
        state.elements = state.elements.filter(el => el.id !== state.selected);
        state.selected = null;
        render();
      }}
    }});

    // Streamlit message listener
    window.addEventListener("message", function(e) {{
      if (e.data && e.data.type === "streamlit:render") {{
        applyArgs(e.data.args || {{}});
      }}
    }});

    setReady();
    render();
  </script>
</body>
</html>"""
    index_path.write_text(html, encoding="utf-8")
    return _COMPONENT_DIR


# ─── Component loading ──────────────────────────────────────────────

_COMPONENT_CACHE: dict = {}


def _get_component():
    """Get or create the Streamlit component for the RIB editor."""
    if components is None:
        return None
    cache_key = "builtly_rib_editor_v2"
    if cache_key not in _COMPONENT_CACHE:
        comp_dir = _ensure_component_html()
        try:
            _COMPONENT_CACHE[cache_key] = components.declare_component(
                "rib_editor_v2", path=str(comp_dir.resolve())
            )
        except Exception as exc:
            st.warning(f"Kunne ikke laste RIB-editor: {exc}")
            return None
    return _COMPONENT_CACHE[cache_key]


# ─── Public API ─────────────────────────────────────────────────────

def render_rib_editor(
    drawing_record: Dict[str, Any],
    sketch: Dict[str, Any],
    editor_key: str = "rib_editor",
) -> Optional[List[Dict[str, Any]]]:
    """
    Render the interactive RIB structural editor.

    Returns:
        List of sketch elements (normalized 0–1 coords) if the user
        clicked "Lagre bæresystem", else None.

    This is a DROP-IN replacement for render_inline_click_canvas_editor().
    """
    component = _get_component()
    if component is None:
        st.info(
            "RIB-editoren kunne ikke startes. "
            "Bruk tabellredigeringen som fallback."
        )
        return None

    # Prepare image
    image = drawing_record.get("image")
    if image is None:
        st.warning("Ingen tegning tilgjengelig for editoren.")
        return None

    image_data = _image_to_data_uri(image)
    iw, ih = image.size

    # Existing sketch elements
    initial_elements = sketch.get("elements", [])

    # Calculate editor height
    aspect = ih / max(iw, 1)
    editor_height = int(min(800, max(400, 920 * aspect))) + 60  # +60 for toolbar

    try:
        value = component(
            image_data=image_data,
            natural_width=iw,
            natural_height=ih,
            initial_elements=initial_elements,
            version_marker=st.session_state.get("rib_draft_updated_at", ""),
            key=f"{editor_key}_rib_editor_v2",
            default=None,
            height=editor_height,
        )
    except Exception as exc:
        st.warning(f"RIB-editor feil: {exc}")
        return None

    # Process return value
    if isinstance(value, dict) and "elements" in value:
        elements = value["elements"]
        if isinstance(elements, list) and len(elements) > 0:
            count = len(elements)
            walls = sum(1 for e in elements if e.get("type") == "wall" and e.get("bearing"))
            cols = sum(1 for e in elements if e.get("type") == "column")
            st.caption(
                f"RIB-editor: {count} elementer "
                f"({walls} bærevegger, {cols} søyler) — "
                f"oppdatert {value.get('updated_at', '?')}"
            )
            return elements

    return None
