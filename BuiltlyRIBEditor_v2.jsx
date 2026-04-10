import { useState, useRef, useCallback, useEffect } from "react";

/*
 * BuiltlyRIBEditor v2 — Structural System Editor for Builtly RIB module
 *
 * Outputs sketch elements in the SAME normalized (0–1) coordinate format
 * used by Konstruksjon.py's sketch system:
 *   column:     { type: "column", x, y, label }
 *   core:       { type: "core", x, y, w, h, label }
 *   wall/beam:  { type: "wall"|"beam", x1, y1, x2, y2, label }
 *   span_arrow: { type: "span_arrow", x1, y1, x2, y2, label }
 *   grid:       { type: "grid", orientation, x|y, label }
 *
 * When embedded as a Streamlit component, it receives:
 *   - image_data: base64 data URI of the plan drawing
 *   - natural_width / natural_height: pixel dimensions
 *   - initial_elements: existing sketch elements (normalized)
 *
 * And sends back via setComponentValue:
 *   - { elements: [...], updated_at: ISO string }
 */

const GRID = 16;
const snap = (v) => Math.round(v / GRID) * GRID;
const uid = () => Math.random().toString(36).slice(2, 8);
const dist = (x1, y1, x2, y2) => Math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2);

const TOOLS = [
  { id: "select",       label: "Velg / Flytt",    icon: "⬚", key: "V" },
  { id: "bearing_wall", label: "Bærevegg",         icon: "█", key: "B" },
  { id: "wall",         label: "Lett vegg",         icon: "▬", key: "W" },
  { id: "column",       label: "Søyle",             icon: "◉", key: "S" },
  { id: "beam",         label: "Bjelke / Spenn",    icon: "⟷", key: "J" },
  { id: "span_arrow",   label: "Dekkeretning",      icon: "≡", key: "D" },
  { id: "load",         label: "Lastpil",           icon: "↓", key: "L" },
  { id: "opening",      label: "Åpning",            icon: "⊔", key: "O" },
  { id: "core",         label: "Kjerne/sjakt",      icon: "▣", key: "K" },
  { id: "grid_v",       label: "Akse vertikal",     icon: "│", key: "A" },
  { id: "grid_h",       label: "Akse horisontal",   icon: "─", key: "H" },
  { id: "measure",      label: "Mål",               icon: "↔", key: "M" },
];

const MATERIAL_COLORS = {
  concrete: { fill: "#94a3b8", stroke: "#64748b", label: "Betong" },
  steel:    { fill: "#38bdf8", stroke: "#0ea5e9", label: "Stål" },
  timber:   { fill: "#d4a574", stroke: "#b8834a", label: "Tre/KL-tre" },
  masonry:  { fill: "#e8a87c", stroke: "#c47f58", label: "Mur" },
};

// Convert normalized (0-1) to canvas px
const normToCanvas = (norm, size) => norm * size;
// Convert canvas px to normalized (0-1)
const canvasToNorm = (px, size) => Math.max(0, Math.min(1, px / size));

const NEXT_GRID_LABELS = "ABCDEFGHJKLMNPQRSTUVWXYZ".split("");

function nextLabel(elements, type, prefix) {
  const existing = elements.filter((e) => e.type === type).map((e) => e.label || "");
  if (type === "column") {
    const nums = existing.map((l) => parseInt(l.replace(/\D/g, ""), 10)).filter((n) => !isNaN(n));
    const next = nums.length ? Math.max(...nums) + 1 : 1;
    return `C${next}`;
  }
  if (type === "grid") {
    for (const l of NEXT_GRID_LABELS) {
      if (!existing.includes(l)) return l;
    }
    return `${NEXT_GRID_LABELS[0]}${existing.length}`;
  }
  return prefix || "";
}

export default function BuiltlyRIBEditor() {
  const svgRef = useRef(null);
  const fileRef = useRef(null);
  const [tool, setTool] = useState("bearing_wall");
  const [elements, setElements] = useState([]);
  const [selected, setSelected] = useState(null);
  const [drawing, setDrawing] = useState(null);
  const [bgImage, setBgImage] = useState(null);
  const [bgOpacity, setBgOpacity] = useState(0.45);
  const [material, setMaterial] = useState("concrete");
  const [canvasW] = useState(1200);
  const [canvasH] = useState(800);
  const [dragOffset, setDragOffset] = useState(null);
  const [showExport, setShowExport] = useState(false);

  // ─── Keyboard shortcuts ───
  useEffect(() => {
    const handler = (e) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
      const t = TOOLS.find((t) => t.key === e.key.toUpperCase());
      if (t) { setTool(t.id); e.preventDefault(); }
      if ((e.key === "Delete" || e.key === "Backspace") && selected) {
        setElements((el) => el.filter((x) => x.id !== selected));
        setSelected(null);
      }
      if (e.key === "Escape") { setSelected(null); setDrawing(null); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [selected]);

  // ─── Coordinate helpers ───
  const getPos = useCallback((e) => {
    const rect = svgRef.current.getBoundingClientRect();
    const sx = canvasW / rect.width;
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    return { x: snap((clientX - rect.left) * sx), y: snap((clientY - rect.top) * sx) };
  }, [canvasW]);

  // ─── Hit testing ───
  const hitTest = useCallback((pos) => {
    for (let i = elements.length - 1; i >= 0; i--) {
      const el = elements[i];
      if ((el.type === "column" || el.type === "load") && dist(pos.x, pos.y, el.cx, el.cy) < 20) return el;
      if (el.type === "core" && pos.x >= el.cx && pos.x <= el.cx + el.cw && pos.y >= el.cy && pos.y <= el.cy + el.ch) return el;
      if (el.cx1 !== undefined && distToSeg(pos.x, pos.y, el.cx1, el.cy1, el.cx2, el.cy2) < 14) return el;
    }
    return null;
  }, [elements]);

  // ─── Mouse handlers ───
  const handleDown = useCallback((e) => {
    e.preventDefault();
    const pos = getPos(e);

    if (tool === "select") {
      const hit = hitTest(pos);
      setSelected(hit ? hit.id : null);
      if (hit) setDragOffset({ dx: pos.x - (hit.cx || hit.cx1 || 0), dy: pos.y - (hit.cy || hit.cy1 || 0) });
      return;
    }

    // Point tools: column, load
    if (tool === "column") {
      const label = nextLabel(elements, "column", "C");
      setElements((prev) => [...prev, {
        id: uid(), type: "column", cx: pos.x, cy: pos.y,
        material, label, size_mm: 300,
      }]);
      return;
    }
    if (tool === "load") {
      setElements((prev) => [...prev, {
        id: uid(), type: "load", cx: pos.x, cy: pos.y,
        value: 10, unit: "kN",
      }]);
      return;
    }

    // Grid axes: single click places a full-width/height line
    if (tool === "grid_v") {
      const label = nextLabel(elements, "grid", "A");
      setElements((prev) => [...prev, {
        id: uid(), type: "grid", orientation: "vertical",
        cx1: pos.x, cy1: 0, cx2: pos.x, cy2: canvasH, label,
      }]);
      return;
    }
    if (tool === "grid_h") {
      const label = nextLabel(elements.filter((e) => e.orientation === "horizontal"), "grid", "1");
      setElements((prev) => [...prev, {
        id: uid(), type: "grid", orientation: "horizontal",
        cx1: 0, cy1: pos.y, cx2: canvasW, cy2: pos.y,
        label: `${elements.filter((e) => e.type === "grid" && e.orientation === "horizontal").length + 1}`,
      }]);
      return;
    }

    setDrawing({ startX: pos.x, startY: pos.y, curX: pos.x, curY: pos.y });
  }, [tool, getPos, hitTest, material, elements, canvasW, canvasH]);

  const handleMove = useCallback((e) => {
    if (drawing) {
      const pos = getPos(e);
      setDrawing((d) => d ? { ...d, curX: pos.x, curY: pos.y } : null);
    }
    if (dragOffset && selected) {
      const pos = getPos(e);
      setElements((els) => els.map((el) => {
        if (el.id !== selected) return el;
        if (el.cx !== undefined && el.cx1 === undefined) {
          return { ...el, cx: pos.x - dragOffset.dx, cy: pos.y - dragOffset.dy };
        }
        if (el.cx1 !== undefined) {
          const ddx = (pos.x - dragOffset.dx) - el.cx1;
          const ddy = (pos.y - dragOffset.dy) - el.cy1;
          return { ...el, cx1: el.cx1 + ddx, cy1: el.cy1 + ddy, cx2: el.cx2 + ddx, cy2: el.cy2 + ddy };
        }
        return el;
      }));
      setDragOffset({ dx: pos.x - (pos.x - dragOffset.dx), dy: pos.y - (pos.y - dragOffset.dy) });
    }
  }, [drawing, dragOffset, selected, getPos]);

  const handleUp = useCallback(() => {
    if (drawing) {
      const d = drawing;
      const len = dist(d.startX, d.startY, d.curX, d.curY);
      const w = Math.abs(d.curX - d.startX);
      const h = Math.abs(d.curY - d.startY);

      if (tool === "core" && w > GRID && h > GRID) {
        setElements((prev) => [...prev, {
          id: uid(), type: "core",
          cx: Math.min(d.startX, d.curX), cy: Math.min(d.startY, d.curY),
          cw: w, ch: h, material: "concrete", label: "Kjerne",
        }]);
      } else if (len > 10 && ["bearing_wall", "wall", "beam", "span_arrow", "opening", "measure"].includes(tool)) {
        const elType = tool === "bearing_wall" ? "wall" : tool;
        setElements((prev) => [...prev, {
          id: uid(),
          type: elType,
          cx1: d.startX, cy1: d.startY, cx2: d.curX, cy2: d.curY,
          material,
          bearing: tool === "bearing_wall",
          label: elType === "beam" ? `${(len * 0.025).toFixed(1)}m` : "",
        }]);
      }
      setDrawing(null);
    }
    setDragOffset(null);
  }, [drawing, tool, material]);

  // ─── Upload background ───
  const handleBgUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => setBgImage(ev.target.result);
    reader.readAsDataURL(file);
  };

  // ─── Update selected element property ───
  const updateSel = (key, val) => setElements((els) => els.map((el) => el.id === selected ? { ...el, [key]: val } : el));
  const selEl = elements.find((e) => e.id === selected);

  // ─── Export to Konstruksjon.py sketch format (normalized 0-1 coords) ───
  const exportSketchElements = () => {
    return elements
      .filter((el) => el.type !== "measure") // measures are visual only
      .map((el) => {
        if (el.type === "column") {
          return {
            type: "column",
            x: canvasToNorm(el.cx, canvasW),
            y: canvasToNorm(el.cy, canvasH),
            label: el.label || "C",
            material: el.material,
            size_mm: el.size_mm,
          };
        }
        if (el.type === "core") {
          return {
            type: "core",
            x: canvasToNorm(el.cx, canvasW),
            y: canvasToNorm(el.cy, canvasH),
            w: canvasToNorm(el.cw, canvasW),
            h: canvasToNorm(el.ch, canvasH),
            label: el.label || "Kjerne",
            material: el.material,
          };
        }
        if (el.type === "grid") {
          if (el.orientation === "vertical") {
            return { type: "grid", orientation: "vertical", x: canvasToNorm(el.cx1, canvasW), label: el.label };
          }
          return { type: "grid", orientation: "horizontal", y: canvasToNorm(el.cy1, canvasH), label: el.label };
        }
        if (el.type === "load") {
          return {
            type: "load",
            x: canvasToNorm(el.cx, canvasW),
            y: canvasToNorm(el.cy, canvasH),
            value: el.value, unit: el.unit,
          };
        }
        // wall, beam, span_arrow, opening
        return {
          type: el.type,
          x1: canvasToNorm(el.cx1, canvasW),
          y1: canvasToNorm(el.cy1, canvasH),
          x2: canvasToNorm(el.cx2, canvasW),
          y2: canvasToNorm(el.cy2, canvasH),
          label: el.label || "",
          material: el.material,
          bearing: el.bearing,
        };
      });
  };

  const handleExport = () => {
    const sketchElements = exportSketchElements();
    const payload = {
      elements: sketchElements,
      updated_at: new Date().toISOString(),
      summary: {
        columns: sketchElements.filter((e) => e.type === "column").length,
        walls: sketchElements.filter((e) => e.type === "wall" && e.bearing).length,
        light_walls: sketchElements.filter((e) => e.type === "wall" && !e.bearing).length,
        beams: sketchElements.filter((e) => e.type === "beam").length,
        cores: sketchElements.filter((e) => e.type === "core").length,
        grids: sketchElements.filter((e) => e.type === "grid").length,
      },
    };
    navigator.clipboard?.writeText(JSON.stringify(payload, null, 2));
    setShowExport(true);
    setTimeout(() => setShowExport(false), 2500);

    // If running as Streamlit component, send value back
    try {
      window.parent?.postMessage({
        isStreamlitMessage: true,
        type: "streamlit:setComponentValue",
        value: payload,
      }, "*");
    } catch (_) { /* not in Streamlit */ }
  };

  // ─── Stats ───
  const walls = elements.filter((e) => e.type === "wall" || e.bearing);
  const bearingCount = elements.filter((e) => e.bearing).length;
  const colCount = elements.filter((e) => e.type === "column").length;
  const beamCount = elements.filter((e) => e.type === "beam").length;
  const coreCount = elements.filter((e) => e.type === "core").length;
  const gridCount = elements.filter((e) => e.type === "grid").length;

  // ─── Rendering ───
  const renderEl = (el) => {
    const mc = MATERIAL_COLORS[el.material] || MATERIAL_COLORS.concrete;
    const isSel = el.id === selected;
    const hi = isSel ? "#fbbf24" : null;

    if (el.type === "column") {
      return (
        <g key={el.id} onClick={(e) => { e.stopPropagation(); setSelected(el.id); }} style={{ cursor: "pointer" }}>
          <circle cx={el.cx} cy={el.cy} r={14} fill={mc.fill} stroke={hi || mc.stroke} strokeWidth={isSel ? 3 : 2} />
          <circle cx={el.cx} cy={el.cy} r={5} fill={mc.stroke} />
          <text x={el.cx} y={el.cy - 18} textAnchor="middle" fill={mc.stroke} fontSize="10" fontFamily="monospace" fontWeight="700">{el.label}</text>
        </g>
      );
    }

    if (el.type === "wall") {
      const thick = el.bearing ? 10 : 4;
      const color = el.bearing ? mc.stroke : "#475569";
      return (
        <g key={el.id} onClick={(e) => { e.stopPropagation(); setSelected(el.id); }} style={{ cursor: "pointer" }}>
          {el.bearing && <line x1={el.cx1} y1={el.cy1} x2={el.cx2} y2={el.cy2} stroke={hi || mc.stroke} strokeWidth={thick + 4} strokeLinecap="round" opacity="0.4" />}
          <line x1={el.cx1} y1={el.cy1} x2={el.cx2} y2={el.cy2} stroke={hi || color} strokeWidth={thick} strokeLinecap="round" />
          {el.bearing && renderHatch(el.cx1, el.cy1, el.cx2, el.cy2, mc.stroke)}
          {el.bearing && (() => {
            const mx = (el.cx1 + el.cx2) / 2, my = (el.cy1 + el.cy2) / 2;
            const l = dist(el.cx1, el.cy1, el.cx2, el.cy2);
            return <text x={mx} y={my - 10} textAnchor="middle" fill={mc.stroke} fontSize="9" fontFamily="monospace">{(l * 0.025).toFixed(1)}m</text>;
          })()}
          {!el.bearing && <line x1={el.cx1} y1={el.cy1} x2={el.cx2} y2={el.cy2} stroke="#334155" strokeWidth={2} strokeDasharray="6 4" />}
        </g>
      );
    }

    if (el.type === "beam") {
      const l = dist(el.cx1, el.cy1, el.cx2, el.cy2);
      const mx = (el.cx1 + el.cx2) / 2, my = (el.cy1 + el.cy2) / 2;
      return (
        <g key={el.id} onClick={(e) => { e.stopPropagation(); setSelected(el.id); }} style={{ cursor: "pointer" }}>
          <line x1={el.cx1} y1={el.cy1} x2={el.cx2} y2={el.cy2} stroke={hi || "#f59e0b"} strokeWidth={4} strokeLinecap="round" />
          <polygon points={`${el.cx1},${el.cy1} ${el.cx1 - 6},${el.cy1 + 10} ${el.cx1 + 6},${el.cy1 + 10}`} fill="#f59e0b" opacity="0.7" />
          <polygon points={`${el.cx2},${el.cy2} ${el.cx2 - 6},${el.cy2 + 10} ${el.cx2 + 6},${el.cy2 + 10}`} fill="#f59e0b" opacity="0.7" />
          <rect x={mx - 30} y={my - 20} width={60} height={16} rx={3} fill="#0a1929ee" stroke="#f59e0b" strokeWidth="0.5" />
          <text x={mx} y={my - 8} textAnchor="middle" fill="#f59e0b" fontSize="10" fontFamily="monospace" fontWeight="700">{(l * 0.025).toFixed(1)}m</text>
        </g>
      );
    }

    if (el.type === "span_arrow") {
      const ang = Math.atan2(el.cy2 - el.cy1, el.cx2 - el.cx1);
      const mx = (el.cx1 + el.cx2) / 2, my = (el.cy1 + el.cy2) / 2;
      const px = -Math.sin(ang) * 8, py = Math.cos(ang) * 8;
      return (
        <g key={el.id} onClick={(e) => { e.stopPropagation(); setSelected(el.id); }} style={{ cursor: "pointer" }}>
          <line x1={el.cx1} y1={el.cy1} x2={el.cx2} y2={el.cy2} stroke={hi || "#22c55e"} strokeWidth={2} strokeDasharray="10 4" />
          <line x1={mx - px * 2} y1={my - py * 2} x2={mx + px * 2} y2={my + py * 2} stroke="#22c55e" strokeWidth={2} />
          <text x={mx + px * 3} y={my + py * 3} textAnchor="middle" fill="#22c55e" fontSize="9" fontFamily="monospace">DEKKE</text>
        </g>
      );
    }

    if (el.type === "load") {
      return (
        <g key={el.id} onClick={(e) => { e.stopPropagation(); setSelected(el.id); }} style={{ cursor: "pointer" }}>
          <line x1={el.cx} y1={el.cy - 32} x2={el.cx} y2={el.cy} stroke={hi || "#ef4444"} strokeWidth={3} />
          <polygon points={`${el.cx},${el.cy} ${el.cx - 5},${el.cy - 8} ${el.cx + 5},${el.cy - 8}`} fill="#ef4444" />
          <rect x={el.cx - 22} y={el.cy - 48} width={44} height={14} rx={2} fill="#0a1929ee" stroke="#ef4444" strokeWidth="0.5" />
          <text x={el.cx} y={el.cy - 38} textAnchor="middle" fill="#ef4444" fontSize="9" fontFamily="monospace" fontWeight="700">{el.value}{el.unit}</text>
        </g>
      );
    }

    if (el.type === "opening") {
      return (
        <g key={el.id} onClick={(e) => { e.stopPropagation(); setSelected(el.id); }} style={{ cursor: "pointer" }}>
          <line x1={el.cx1} y1={el.cy1} x2={el.cx2} y2={el.cy2} stroke={hi || "#fb923c"} strokeWidth={6} strokeLinecap="round" />
          <line x1={el.cx1} y1={el.cy1} x2={el.cx2} y2={el.cy2} stroke="#0d1b2a" strokeWidth={3} />
        </g>
      );
    }

    if (el.type === "core") {
      return (
        <g key={el.id} onClick={(e) => { e.stopPropagation(); setSelected(el.id); }} style={{ cursor: "pointer" }}>
          <rect x={el.cx} y={el.cy} width={el.cw} height={el.ch}
            fill="rgba(100,116,139,0.2)" stroke={hi || "#64748b"} strokeWidth={isSel ? 3 : 2} strokeDasharray="6 3" />
          <line x1={el.cx} y1={el.cy} x2={el.cx + el.cw} y2={el.cy + el.ch} stroke="#64748b" strokeWidth="1" opacity="0.4" />
          <line x1={el.cx + el.cw} y1={el.cy} x2={el.cx} y2={el.cy + el.ch} stroke="#64748b" strokeWidth="1" opacity="0.4" />
          <text x={el.cx + el.cw / 2} y={el.cy + el.ch / 2 + 4} textAnchor="middle" fill="#94a3b8" fontSize="10" fontWeight="600">{el.label || "KJERNE"}</text>
        </g>
      );
    }

    if (el.type === "grid") {
      const isVert = el.orientation === "vertical";
      return (
        <g key={el.id} onClick={(e) => { e.stopPropagation(); setSelected(el.id); }} style={{ cursor: "pointer" }}>
          <line x1={el.cx1} y1={el.cy1} x2={el.cx2} y2={el.cy2}
            stroke={hi || "rgba(56,189,248,0.25)"} strokeWidth={1} strokeDasharray="8 6" />
          <circle cx={isVert ? el.cx1 : 14} cy={isVert ? 14 : el.cy1} r={10} fill="#0a1929" stroke="rgba(56,189,248,0.5)" strokeWidth="1" />
          <text x={isVert ? el.cx1 : 14} y={isVert ? 18 : el.cy1 + 4} textAnchor="middle" fill="#38bdf8" fontSize="10" fontWeight="700" fontFamily="monospace">{el.label}</text>
        </g>
      );
    }

    if (el.type === "measure") {
      const l = dist(el.cx1, el.cy1, el.cx2, el.cy2);
      const mx = (el.cx1 + el.cx2) / 2, my = (el.cy1 + el.cy2) / 2;
      return (
        <g key={el.id}>
          <line x1={el.cx1} y1={el.cy1} x2={el.cx2} y2={el.cy2} stroke="#a78bfa" strokeWidth={1} strokeDasharray="5 3" />
          <circle cx={el.cx1} cy={el.cy1} r={3} fill="#a78bfa" />
          <circle cx={el.cx2} cy={el.cy2} r={3} fill="#a78bfa" />
          <rect x={mx - 26} y={my - 10} width={52} height={16} rx={3} fill="#0a1929ee" stroke="#a78bfa" strokeWidth="0.5" />
          <text x={mx} y={my + 2} textAnchor="middle" fill="#a78bfa" fontSize="10" fontFamily="monospace">{(l * 0.025).toFixed(1)}m</text>
        </g>
      );
    }

    return null;
  };

  const renderHatch = (x1, y1, x2, y2, color) => {
    const l = dist(x1, y1, x2, y2);
    if (l < 20) return null;
    const ang = Math.atan2(y2 - y1, x2 - x1);
    const px = -Math.sin(ang) * 4, py = Math.cos(ang) * 4;
    const lines = [];
    const count = Math.floor(l / 12);
    for (let i = 1; i < count; i++) {
      const t = i / count;
      const cx = x1 + (x2 - x1) * t, cy = y1 + (y2 - y1) * t;
      lines.push(<line key={i} x1={cx - px} y1={cy - py} x2={cx + px} y2={cy + py} stroke={color} strokeWidth="1" opacity="0.4" />);
    }
    return <>{lines}</>;
  };

  // ─── UI ───
  return (
    <div style={{ fontFamily: "'DM Sans', 'Segoe UI', sans-serif", background: "#06111a", color: "#e2e8f0", height: "100vh", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 16px", borderBottom: "1px solid #1a2a3a", background: "#0a1929", flexShrink: 0 }}>
        <div style={{ width: 26, height: 26, borderRadius: 5, background: "linear-gradient(135deg, #38bdf8, #0ea5e9)", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, fontSize: 13, color: "#06111a" }}>B</div>
        <span style={{ fontWeight: 700, fontSize: 15 }}>Builtly</span>
        <span style={{ color: "#475569", fontSize: 12 }}>RIB Bæresystem-editor</span>
        <div style={{ flex: 1 }} />
        <div style={{ fontSize: 10, color: "#334155", fontFamily: "monospace" }}>
          Elementer: {elements.length} | Format: Konstruksjon.py sketch
        </div>
        <button onClick={handleExport} style={{
          padding: "5px 14px", borderRadius: 5, border: "none", fontWeight: 600, fontSize: 11, cursor: "pointer", transition: "all 0.2s",
          background: showExport ? "#22c55e" : "#38bdf8", color: "#06111a",
        }}>
          {showExport ? "✓ Kopiert + sendt!" : "Lagre bæresystem →"}
        </button>
      </div>

      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* Left toolbar */}
        <div style={{ width: 170, padding: "12px 10px", borderRight: "1px solid #1a2a3a", background: "#0a1929", overflowY: "auto", flexShrink: 0 }}>
          <div style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: "0.12em", color: "#475569", marginBottom: 6, fontWeight: 700 }}>Verktøy</div>
          {TOOLS.map((t) => (
            <button key={t.id} onClick={() => setTool(t.id)} style={{
              display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", borderRadius: 6, border: "none", width: "100%", textAlign: "left", marginBottom: 1, cursor: "pointer",
              background: tool === t.id ? "rgba(56,189,248,0.12)" : "transparent",
              color: tool === t.id ? "#38bdf8" : "#94a3b8",
              outline: tool === t.id ? "1px solid rgba(56,189,248,0.25)" : "none",
              fontSize: 12, fontWeight: 500,
            }}>
              <span style={{ fontSize: 14, width: 18, textAlign: "center" }}>{t.icon}</span>
              <span style={{ flex: 1 }}>{t.label}</span>
              <span style={{ fontSize: 9, color: "#475569", fontFamily: "monospace" }}>{t.key}</span>
            </button>
          ))}

          {/* Material */}
          <div style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: "0.12em", color: "#475569", marginTop: 16, marginBottom: 6, fontWeight: 700 }}>Materiale</div>
          {Object.entries(MATERIAL_COLORS).map(([id, mc]) => (
            <button key={id} onClick={() => setMaterial(id)} style={{
              display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", borderRadius: 5, border: "none", width: "100%", textAlign: "left", marginBottom: 1, cursor: "pointer",
              background: material === id ? "rgba(56,189,248,0.08)" : "transparent",
              color: material === id ? "#e2e8f0" : "#64748b", fontSize: 11,
            }}>
              <span style={{ width: 10, height: 10, borderRadius: 2, background: mc.fill, border: `1px solid ${mc.stroke}` }} />
              {mc.label}
            </button>
          ))}

          {/* Background */}
          <div style={{ borderTop: "1px solid #1a2a3a", marginTop: 16, paddingTop: 12 }}>
            <div style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: "0.12em", color: "#475569", marginBottom: 6, fontWeight: 700 }}>Plantegning</div>
            <button onClick={() => fileRef.current?.click()} style={{
              width: "100%", padding: "7px 10px", borderRadius: 5, border: "1px dashed #334155", background: "transparent", color: "#64748b", fontSize: 10, cursor: "pointer",
            }}>📎 Last opp tegning</button>
            <input ref={fileRef} type="file" accept="image/*,.pdf" onChange={handleBgUpload} style={{ display: "none" }} />
            {bgImage && (
              <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 6, fontSize: 10, color: "#475569" }}>
                <span>Synlighet</span>
                <input type="range" min="5" max="95" value={bgOpacity * 100} onChange={(e) => setBgOpacity(e.target.value / 100)} style={{ flex: 1, accentColor: "#38bdf8" }} />
              </div>
            )}
          </div>
        </div>

        {/* Canvas */}
        <div style={{ flex: 1, overflow: "auto", background: "#0d1b2a" }}>
          <svg ref={svgRef} viewBox={`0 0 ${canvasW} ${canvasH}`}
            style={{ width: "100%", height: "100%", cursor: tool === "select" ? "default" : "crosshair", display: "block" }}
            onMouseDown={handleDown} onMouseMove={handleMove} onMouseUp={handleUp}
            onTouchStart={handleDown} onTouchMove={handleMove} onTouchEnd={handleUp}>
            <defs>
              <pattern id="g" width={GRID} height={GRID} patternUnits="userSpaceOnUse">
                <path d={`M ${GRID} 0 L 0 0 0 ${GRID}`} fill="none" stroke="#162231" strokeWidth="0.4" />
              </pattern>
              <pattern id="gM" width={GRID * 5} height={GRID * 5} patternUnits="userSpaceOnUse">
                <rect width={GRID * 5} height={GRID * 5} fill="url(#g)" />
                <path d={`M ${GRID * 5} 0 L 0 0 0 ${GRID * 5}`} fill="none" stroke="#1a2d40" strokeWidth="0.8" />
              </pattern>
            </defs>
            {bgImage && <image href={bgImage} x="0" y="0" width={canvasW} height={canvasH} opacity={bgOpacity} preserveAspectRatio="xMidYMid meet" />}
            <rect width={canvasW} height={canvasH} fill="url(#gM)" />

            {/* Scale bar */}
            <g transform="translate(16,16)" opacity="0.6">
              <line x1="0" y1="0" x2="40" y2="0" stroke="#38bdf8" strokeWidth="2" />
              <line x1="0" y1="-3" x2="0" y2="3" stroke="#38bdf8" strokeWidth="1.5" />
              <line x1="40" y1="-3" x2="40" y2="3" stroke="#38bdf8" strokeWidth="1.5" />
              <text x="20" y="-6" textAnchor="middle" fill="#38bdf8" fontSize="9" fontFamily="monospace">1.0 m</text>
            </g>

            {elements.map(renderEl)}

            {/* Drawing preview */}
            {drawing && ["bearing_wall", "wall", "beam", "span_arrow", "opening", "measure"].includes(tool) && (
              <line x1={drawing.startX} y1={drawing.startY} x2={drawing.curX} y2={drawing.curY}
                stroke={tool === "bearing_wall" ? "#94a3b8" : tool === "beam" ? "#f59e0b" : tool === "span_arrow" ? "#22c55e" : tool === "measure" ? "#a78bfa" : "#475569"}
                strokeWidth={tool === "bearing_wall" ? 8 : 3} strokeDasharray="8 4" opacity="0.6" />
            )}
            {drawing && tool === "core" && (
              <rect x={Math.min(drawing.startX, drawing.curX)} y={Math.min(drawing.startY, drawing.curY)}
                width={Math.abs(drawing.curX - drawing.startX)} height={Math.abs(drawing.curY - drawing.startY)}
                fill="rgba(100,116,139,0.1)" stroke="#64748b" strokeWidth="1.5" strokeDasharray="6 3" />
            )}
          </svg>
        </div>

        {/* Right panel */}
        <div style={{ width: 220, padding: "12px 14px", borderLeft: "1px solid #1a2a3a", background: "#0a1929", overflowY: "auto", flexShrink: 0, display: "flex", flexDirection: "column", gap: 14 }}>
          {/* Summary */}
          <div>
            <div style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: "0.12em", color: "#475569", marginBottom: 6, fontWeight: 700 }}>Bæresystem</div>
            <div style={{ background: "#0d1b2a", borderRadius: 6, padding: 10, border: "1px solid #1a2a3a", fontSize: 11 }}>
              {[
                ["Bærevegger", bearingCount, "#94a3b8"],
                ["Lette vegger", elements.filter((e) => e.type === "wall" && !e.bearing).length, "#475569"],
                ["Søyler", colCount, "#38bdf8"],
                ["Bjelker", beamCount, "#f59e0b"],
                ["Kjerner", coreCount, "#64748b"],
                ["Akser", gridCount, "#38bdf8"],
              ].map(([label, val, col]) => (
                <div key={label} style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                  <span style={{ color: "#64748b" }}>{label}</span>
                  <span style={{ color: col, fontWeight: 600, fontFamily: "monospace" }}>{val}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Selected properties */}
          {selEl && (
            <div>
              <div style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: "0.12em", color: "#475569", marginBottom: 6, fontWeight: 700 }}>Egenskaper</div>
              <div style={{ background: "#0d1b2a", borderRadius: 6, padding: 10, border: "1px solid #fbbf24", fontSize: 11 }}>
                <div style={{ color: "#fbbf24", fontWeight: 700, marginBottom: 6, fontSize: 12 }}>
                  {TOOLS.find((t) => t.id === selEl.type || (t.id === "bearing_wall" && selEl.type === "wall" && selEl.bearing))?.label || selEl.type}
                </div>

                {selEl.label !== undefined && (
                  <div style={{ marginBottom: 6 }}>
                    <div style={{ color: "#64748b", fontSize: 10, marginBottom: 2 }}>Merkelapp</div>
                    <input value={selEl.label || ""} onChange={(e) => updateSel("label", e.target.value)}
                      style={{ width: "100%", background: "#1a2a3a", border: "1px solid #334155", borderRadius: 4, color: "#e2e8f0", padding: "3px 6px", fontSize: 11 }} />
                  </div>
                )}

                {selEl.material && (
                  <div style={{ marginBottom: 6 }}>
                    <div style={{ color: "#64748b", fontSize: 10, marginBottom: 2 }}>Materiale</div>
                    <select value={selEl.material} onChange={(e) => updateSel("material", e.target.value)}
                      style={{ width: "100%", background: "#1a2a3a", border: "1px solid #334155", borderRadius: 4, color: "#e2e8f0", padding: "3px 6px", fontSize: 11 }}>
                      {Object.entries(MATERIAL_COLORS).map(([id, mc]) => (<option key={id} value={id}>{mc.label}</option>))}
                    </select>
                  </div>
                )}

                {selEl.type === "column" && (
                  <div style={{ marginBottom: 6 }}>
                    <div style={{ color: "#64748b", fontSize: 10, marginBottom: 2 }}>Dimensjon (mm)</div>
                    <input type="number" value={selEl.size_mm || 300} onChange={(e) => updateSel("size_mm", parseInt(e.target.value) || 300)}
                      style={{ width: "100%", background: "#1a2a3a", border: "1px solid #334155", borderRadius: 4, color: "#e2e8f0", padding: "3px 6px", fontSize: 11 }} />
                  </div>
                )}

                {selEl.type === "wall" && (
                  <label style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6, color: "#64748b", fontSize: 11, cursor: "pointer" }}>
                    <input type="checkbox" checked={!!selEl.bearing} onChange={(e) => updateSel("bearing", e.target.checked)} style={{ accentColor: "#38bdf8" }} />
                    Bærende vegg
                  </label>
                )}

                {selEl.type === "load" && (
                  <div style={{ display: "flex", gap: 4, marginBottom: 6 }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ color: "#64748b", fontSize: 10, marginBottom: 2 }}>Verdi</div>
                      <input type="number" value={selEl.value} onChange={(e) => updateSel("value", parseFloat(e.target.value) || 0)}
                        style={{ width: "100%", background: "#1a2a3a", border: "1px solid #334155", borderRadius: 4, color: "#e2e8f0", padding: "3px 6px", fontSize: 11 }} />
                    </div>
                    <div>
                      <div style={{ color: "#64748b", fontSize: 10, marginBottom: 2 }}>Enhet</div>
                      <select value={selEl.unit} onChange={(e) => updateSel("unit", e.target.value)}
                        style={{ background: "#1a2a3a", border: "1px solid #334155", borderRadius: 4, color: "#e2e8f0", padding: "3px 6px", fontSize: 11 }}>
                        <option value="kN">kN</option><option value="kN/m">kN/m</option><option value="kN/m²">kN/m²</option>
                      </select>
                    </div>
                  </div>
                )}

                <button onClick={() => { setElements((el) => el.filter((x) => x.id !== selected)); setSelected(null); }}
                  style={{ marginTop: 6, width: "100%", padding: "5px", borderRadius: 4, border: "none", background: "rgba(239,68,68,0.15)", color: "#ef4444", fontSize: 10, cursor: "pointer" }}>
                  Slett (Del)
                </button>
              </div>
            </div>
          )}

          {/* Legend */}
          <div>
            <div style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: "0.12em", color: "#475569", marginBottom: 6, fontWeight: 700 }}>Tegnforklaring</div>
            <div style={{ fontSize: 10, color: "#475569", lineHeight: 2 }}>
              {[
                ["━━", "#94a3b8", "Bærevegg"], ["╌╌", "#475569", "Lett vegg"],
                ["●", "#38bdf8", "Søyle"], ["━━", "#f59e0b", "Bjelke"],
                ["≡→", "#22c55e", "Dekkeretning"], ["↓", "#ef4444", "Last"],
                ["╔═╗", "#64748b", "Kjerne"], ["│─", "#38bdf8", "Akse"],
              ].map(([sym, col, label]) => (
                <div key={label} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ color: col, fontFamily: "monospace", width: 22, textAlign: "center", fontSize: 11 }}>{sym}</span>
                  <span>{label}</span>
                </div>
              ))}
            </div>
          </div>

          <div style={{ marginTop: "auto", padding: "8px 0", borderTop: "1px solid #1a2a3a" }}>
            <div style={{ fontSize: 9, color: "#1e293b", lineHeight: 1.8, fontFamily: "monospace" }}>
              Output: sketch.elements[] format<br />
              Kompatibel med Konstruksjon.py
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function distToSeg(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return dist(px, py, x1, y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return dist(px, py, x1 + t * dx, y1 + t * dy);
}
