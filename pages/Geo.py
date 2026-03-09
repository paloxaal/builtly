import streamlit as st
import pandas as pd
import google.generativeai as genai
from fpdf import FPDF
import openpyxl
import os
import base64
from datetime import datetime
import tempfile
import re
import requests
import urllib.parse
import io
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

# --- 1. TEKNISK OPPSETT ---
st.set_page_config(page_title="Geo & Miljø (RIG-M) | Builtly", layout="wide", initial_sidebar_state="collapsed")

google_key = os.environ.get("GOOGLE_API_KEY")
if google_key:
    genai.configure(api_key=google_key)
else:
    st.error("Kritisk feil: Fant ingen API-nøkkel! Sjekk 'Environment Variables' i Render.")
    st.stop()


def render_html(html_string: str):
    st.markdown(html_string.replace('\n', ' '), unsafe_allow_html=True)

def logo_data_uri() -> str:
    for candidate in ["logo-white.png", "logo.png"]:
        if os.path.exists(candidate):
            suffix = Path(candidate).suffix.lower().replace(".", "") or "png"
            with open(candidate, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/{suffix};base64,{encoded}"
    return ""

def find_page(base_name: str) -> str:
    for name in [base_name, base_name.lower(), base_name.capitalize()]:
        p = Path(f"pages/{name}.py")
        if p.exists(): return str(p)
    return ""

def clean_pdf_text(text):
    if text is None: return ""
    text = str(text)
    rep = {"–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "•": "-", "≤": "<=", "≥": ">="}
    for old, new in rep.items(): text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")

def ironclad_text_formatter(text):
    text = clean_pdf_text(text)
    text = text.replace("$", "").replace("*", "").replace("_", "")
    text = re.sub(r"[-|=]{3,}", " ", text)
    text = re.sub(r"([^\s]{40})", r"\1 ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def nb_value(value):
    if value is None or value == "": return "-"
    if isinstance(value, float):
        if value.is_integer(): return str(int(value))
        if abs(value) >= 100: txt = f"{value:.0f}"
        elif abs(value) >= 10: txt = f"{value:.1f}"
        else: txt = f"{value:.3f}".rstrip("0").rstrip(".")
        return txt.replace(".", ",")
    if isinstance(value, int): return str(value)
    return clean_pdf_text(str(value))

def parse_numeric(value):
    if value is None: return None, None
    if isinstance(value, (int, float)): return float(value), None
    txt = str(value).strip()
    if not txt: return None, None
    qualifier = None
    txt = txt.replace(" ", "").replace(",", ".")
    low = txt.lower()
    if low in {"nd", "n.d.", "n.d", "na", "nan"}: return None, "nd"
    if txt.startswith("<"):
        qualifier = "<"
        txt = txt[1:]
    elif txt.startswith(">"):
        qualifier = ">"
        txt = txt[1:]
    txt = txt.replace("mg/kg", "")
    try: return float(txt), qualifier
    except Exception: return None, qualifier

def strip_empty_edges(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    df2 = df.copy()
    df2 = df2.dropna(axis=0, how="all")
    df2 = df2.dropna(axis=1, how="all")
    return df2

ANALYTE_COLUMN_MAP = {
    "TOC (%)": 7, "As": 8, "Pb": 9, "Cd": 12, "Cr (tot)": 13, "Cu": 14, "Hg": 17, 
    "Ni": 18, "Zn": 19, "Bensen": 22, "Toluen": 23, "Etylbensen": 24, "Xylener": 25, 
    "C10-C12": 26, "C12-C35": 28, "Sum 16": 30, "B(a)p": 34, "Beskrivelse": 35,
}

DISPLAY_ANALYTES = ["As", "Pb", "Ni", "Zn", "C12-C35", "Sum 16", "B(a)p"]
CLASS_ORDER = {"TK1": 1, "TK2": 2, "TK3": 3, "TK4": 4, "TK5": 5, "TK>5": 6}
CLASS_FILL = {
    "TK1": (214, 236, 255), "TK2": (196, 235, 176), "TK3": (255, 242, 153),
    "TK4": (255, 202, 128), "TK5": (255, 153, 153), "TK>5": (232, 97, 97),
}

def get_font(size: int, bold: bool = False):
    candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in candidates:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size=size)
            except Exception: pass
    return ImageFont.load_default()

def wrap_text_px(text: str, font, max_width: int):
    text = clean_pdf_text(text)
    if not text: return [""]
    words = text.split()
    if not words: return [""]
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.getbbox(candidate)[2] <= max_width: current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    final_lines = []
    for line in lines:
        while font.getbbox(line)[2] > max_width and len(line) > 10:
            cut = max(8, len(line) // 2)
            probe = line[:cut]
            while cut > 8 and font.getbbox(probe + "...")[2] > max_width:
                cut -= 1
                probe = line[:cut]
            final_lines.append(probe + "...")
            line = line[cut:]
        final_lines.append(line)
    return final_lines or [""]

def class_rank(class_code: str) -> int:
    return CLASS_ORDER.get(class_code or "", 0)

def classify_value(value, analyte: str, thresholds: dict):
    if analyte not in thresholds.get("TK1", {}): return None
    num, qualifier = parse_numeric(value)
    if num is None: return None
    tk1, tk2, tk3, tk4, tk5 = [thresholds[tk].get(analyte) if tk in thresholds else None for tk in ["TK1", "TK2", "TK3", "TK4", "TK5"]]
    if tk1 is None: return None
    if qualifier == "<" and num <= tk1: return "TK1"
    if num <= tk1: return "TK1"
    if tk2 is not None and num <= tk2: return "TK2"
    if tk3 is not None and num <= tk3: return "TK3"
    if tk4 is not None and num <= tk4: return "TK4"
    if tk5 is not None and num <= tk5: return "TK5"
    return "TK>5"

def split_dataframe(df: pd.DataFrame, chunk_size: int):
    if df is None or df.empty: return []
    return [df.iloc[start:start + chunk_size].reset_index(drop=True) for start in range(0, len(df), chunk_size)]

# --- 4. BILDER OG TABELL RENDERER ---
def render_table_image(df: pd.DataFrame, title: str, subtitle: str = "", row_class_column: str = None, cell_fill_lookup: dict = None, note: str = ""):
    df = df.copy().fillna("")
    title, subtitle, note = clean_pdf_text(title), clean_pdf_text(subtitle), clean_pdf_text(note)

    font_title, font_subtitle = get_font(34, bold=True), get_font(18, bold=False)
    font_header, font_body = get_font(18, bold=True), get_font(17, bold=False)

    side_pad, top_pad, cell_pad_x, cell_pad_y, table_width = 28, 24, 10, 9, 1520

    width_weights = []
    for col in df.columns:
        col_txt = str(col)
        if col_txt in {"Prøvepunkt", "Dybde", "Dybde (m)", "Fil", "Ark", "Høyeste klasse", "Styrende parameter"}: width_weights.append(1.0)
        elif col_txt in DISPLAY_ANALYTES or col_txt in {"Styrende verdi", "Klasse"}: width_weights.append(0.9)
        elif "Beskrivelse" in col_txt or "Kommentar" in col_txt: width_weights.append(2.8)
        elif col_txt in {"Datatype", "Innhold"}: width_weights.append(1.8)
        else: width_weights.append(1.3)
        
    total_weight = sum(width_weights) or 1
    col_widths = [max(95, int(table_width * w / total_weight)) for w in width_weights]

    header_height = 0
    header_wrapped = {}
    for col, width in zip(df.columns, col_widths):
        wrapped = wrap_text_px(str(col), font_header, width - (cell_pad_x * 2))
        header_wrapped[col] = wrapped
        header_height = max(header_height, len(wrapped) * 24 + (cell_pad_y * 2))

    row_heights, wrapped_cells = [], []
    for ridx in range(len(df)):
        row = df.iloc[ridx]
        row_wrap, row_height = {}, 0
        for col, width in zip(df.columns, col_widths):
            wrapped = wrap_text_px(str(row[col]), font_body, width - (cell_pad_x * 2))
            row_wrap[col] = wrapped
            row_height = max(row_height, len(wrapped) * 22 + (cell_pad_y * 2))
        row_heights.append(max(34, row_height))
        wrapped_cells.append(row_wrap)

    title_height = 66
    subtitle_height = 26 if subtitle else 0
    note_height = 32 if note else 0
    total_height = top_pad + title_height + subtitle_height + 14 + header_height + sum(row_heights) + note_height + 28
    
    image_width, image_height = table_width + side_pad * 2, total_height + 10
    img = Image.new("RGB", (image_width, image_height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    band_fill, header_fill, alt_fill = (236, 240, 245), (46, 62, 84), (248, 250, 252)
    grid_fill, title_fill, subtitle_fill, text_fill = (205, 212, 220), (29, 45, 68), (96, 108, 122), (35, 38, 43)

    draw.rounded_rectangle((12, 12, image_width - 12, image_height - 12), radius=18, outline=(219, 225, 232), width=2, fill=(255, 255, 255))
    draw.rounded_rectangle((18, 18, image_width - 18, 18 + title_height + subtitle_height + 10), radius=16, fill=band_fill)
    draw.text((side_pad, 28), title, font=font_title, fill=title_fill)
    if subtitle: draw.text((side_pad, 28 + 40), subtitle, font=font_subtitle, fill=subtitle_fill)

    x, y = side_pad, top_pad + title_height + subtitle_height + 10
    for col, width in zip(df.columns, col_widths):
        draw.rectangle((x, y, x + width, y + header_height), fill=header_fill)
        yy = y + cell_pad_y
        for line in header_wrapped[col]:
            draw.text((x + cell_pad_x, yy), clean_pdf_text(line), font=font_header, fill=(255, 255, 255))
            yy += 24
        x += width
    draw.rectangle((side_pad, y, side_pad + sum(col_widths), y + header_height), outline=grid_fill, width=1)

    y += header_height
    for ridx in range(len(df)):
        row = df.iloc[ridx]
        base_fill = alt_fill if ridx % 2 else (255, 255, 255)
        if row_class_column and row_class_column in row and str(row[row_class_column]) in CLASS_FILL:
            rf = CLASS_FILL[str(row[row_class_column])]
            base_fill = tuple(int((c + 255 * 3) / 4) for c in rf)
        x, row_height = side_pad, row_heights[ridx]
        for col, width in zip(df.columns, col_widths):
            cell_fill = cell_fill_lookup.get((ridx, str(col)), base_fill) if cell_fill_lookup else base_fill
            draw.rectangle((x, y, x + width, y + row_height), fill=cell_fill, outline=grid_fill, width=1)
            yy = y + cell_pad_y
            for line in wrapped_cells[ridx][col]:
                draw.text((x + cell_pad_x, yy), clean_pdf_text(line), font=font_body, fill=text_fill)
                yy += 22
            x += width
        y += row_height

    if note: draw.text((side_pad, y + 8), note, font=font_subtitle, fill=subtitle_fill)
    return img


def save_temp_image(img: Image.Image, suffix: str = ".png"):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    img.save(tmp.name)
    tmp.close()
    return tmp.name

# --- 5. DATA EXTRACTION LOGIC ---
def read_generic_table(file_name: str, file_bytes: bytes):
    try:
        df = pd.read_csv(io.BytesIO(file_bytes)) if file_name.lower().endswith(".csv") else pd.read_excel(io.BytesIO(file_bytes), sheet_name=0)
        df = strip_empty_edges(df)
        if df.empty: return None
        df = df.head(30).copy()
        df.columns = [clean_pdf_text(str(c)) for c in df.columns]
        return df
    except Exception:
        return None

def is_multiconsult_summary_sheet(ws) -> bool:
    checks = [clean_pdf_text(ws.cell(18, 1).value), clean_pdf_text(ws.cell(19, 8).value), clean_pdf_text(ws.cell(20, 28).value)]
    haystack = " ".join(checks)
    return "Prøvepunkt" in haystack and "TUNGMETALLER" in haystack and "C12-C35" in haystack

def extract_metadata_lines(ws):
    lines = []
    for row in range(1, 11):
        vals = [clean_pdf_text(ws.cell(row, col).value) for col in [1, 21] if ws.cell(row, col).value]
        if vals: lines.append(" | ".join(vals))
    return lines

def extract_multiconsult_summary(file_name: str, file_bytes: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    target_sheet = next((ws for ws in wb.worksheets if is_multiconsult_summary_sheet(ws)), None)
    if not target_sheet: return None

    ws, rows, thresholds, metadata_lines = target_sheet, [], {}, extract_metadata_lines(target_sheet)
    current_sample, table_started = None, False

    for r in range(1, min(ws.max_row, 220) + 1):
        a_txt = clean_pdf_text(ws.cell(r, 1).value).strip()
        e_val = ws.cell(r, 5).value

        if a_txt == "Prøvepunkt":
            table_started = True
            continue
        if not table_started: continue
        if a_txt == "Analyse" and rows: break
        
        if a_txt.startswith("Tilstandsklasse"):
            if match := re.search(r"(\d)", a_txt):
                tk = f"TK{match.group(1)}"
                thresholds[tk] = {}
                for analyte, col_idx in ANALYTE_COLUMN_MAP.items():
                    if analyte not in {"TOC (%)", "Beskrivelse"}:
                        val, _ = parse_numeric(ws.cell(r, col_idx).value)
                        thresholds[tk][analyte] = val
            continue
            
        if re.match(r"^SK\d+", a_txt): current_sample = a_txt
        if current_sample and e_val:
            row = {
                "Fil": clean_pdf_text(file_name), "Ark": clean_pdf_text(ws.title),
                "Prøvepunkt": current_sample, "Dybde (m)": clean_pdf_text(e_val),
                "Beskrivelse": clean_pdf_text(ws.cell(r, ANALYTE_COLUMN_MAP["Beskrivelse"]).value)
            }
            any_result = False
            for analyte, col_idx in ANALYTE_COLUMN_MAP.items():
                if analyte == "Beskrivelse": continue
                value = ws.cell(r, col_idx).value
                row[analyte] = value
                if value not in (None, ""): any_result = True
            if any_result: rows.append(row)

    if not rows: return None

    for row in rows:
        row.update({"class_rank": 0, "Høyeste klasse": "-", "Styrende parameter": "-", "Styrende verdi": "-", "_cell_classes": {}})
        for analyte in DISPLAY_ANALYTES:
            ccode = classify_value(row.get(analyte), analyte, thresholds)
            row["_cell_classes"][analyte] = ccode
            if class_rank(ccode) > row["class_rank"]:
                row.update({"class_rank": class_rank(ccode), "Høyeste klasse": ccode, "Styrende parameter": analyte, "Styrende verdi": nb_value(row.get(analyte))})
        if row["Høyeste klasse"] == "-" and row.get("Beskrivelse"):
            row.update({"Høyeste klasse": "TK1", "class_rank": 1})

    detail_df = pd.DataFrame(rows)
    detail_df["class_rank"] = detail_df["class_rank"].fillna(0).astype(int)
    
    summary_rows = []
    for sample, grp in detail_df.groupby("Prøvepunkt", sort=True):
        best = grp.sort_values(["class_rank"], ascending=[False]).iloc[0]
        summary_rows.append({k: best[k] for k in ["Prøvepunkt", "Høyeste klasse", "Dybde (m)", "Styrende parameter", "Styrende verdi", "Beskrivelse", "class_rank"]})
        
    summary_df = pd.DataFrame(summary_rows).sort_values(["class_rank", "Prøvepunkt"], ascending=[False, True]).reset_index(drop=True)
    exceedance_df = summary_df[summary_df["class_rank"] >= 2].copy()
    if exceedance_df.empty: exceedance_df = summary_df.head(8).copy()
    
    excerpt_df = detail_df[["Prøvepunkt", "Dybde (m)", "Beskrivelse"] + DISPLAY_ANALYTES + ["Høyeste klasse"]].copy()
    for col in DISPLAY_ANALYTES: excerpt_df[col] = excerpt_df[col].map(nb_value)

    cell_fill_lookup = {}
    for ridx, row in detail_df.reset_index(drop=True).iterrows():
        for analyte in DISPLAY_ANALYTES:
            if ccode := row["_cell_classes"].get(analyte):
                if ccode in CLASS_FILL: cell_fill_lookup[(ridx, analyte)] = CLASS_FILL[ccode]
        if (ccode := row.get("Høyeste klasse")) in CLASS_FILL:
            cell_fill_lookup[(ridx, "Høyeste klasse")] = CLASS_FILL[ccode]

    threshold_df = pd.DataFrame([{"Klasse": tk, **{k: nb_value(thresholds[tk].get(k)) for k in DISPLAY_ANALYTES}} for tk in ["TK1", "TK2", "TK3", "TK4", "TK5"] if tk in thresholds])
    counts = {tk: int((summary_df["Høyeste klasse"] == tk).sum()) for tk in ["TK1", "TK2", "TK3", "TK4", "TK5", "TK>5"]}

    prompt_lines = [f"KILDE: {file_name} | Ark: {ws.title}"] + metadata_lines[:8] + [
        f"Antall delprøver i analysetabellen: {len(detail_df)}", f"Antall prøvepunkt i analysetabellen: {summary_df['Prøvepunkt'].nunique()}", "Høyeste registrerte klasser per prøvepunkt:"
    ] + [f"- {r['Prøvepunkt']} | Dybde {r['Dybde (m)']} | {r['Styrende parameter']} = {r['Styrende verdi']} | {r['Høyeste klasse']} | {r['Beskrivelse']}" for _, r in summary_df.head(12).iterrows()]

    if not threshold_df.empty:
        prompt_lines.append("Tilstandsklassegrenser (utdrag):")
        for _, r in threshold_df.iterrows(): prompt_lines.append(f"- {r['Klasse']}: As {r['As']}, Pb {r['Pb']}, Ni {r['Ni']}, Zn {r['Zn']}, C12-C35 {r['C12-C35']}, Sum16 {r['Sum 16']}, B(a)p {r['B(a)p']}")

    source_overview_df = pd.DataFrame([{"Fil": clean_pdf_text(file_name), "Datatype": "Miljøteknisk analysetabell", "Ark": clean_pdf_text(ws.title), "Innhold": f"{summary_df['Prøvepunkt'].nunique()} prøvepunkt / {len(detail_df)} delprøver", "Kommentar": "Gjenkjent Multiconsult-oppsett med tilstandsklassegrenser og massebeskrivelser"}])

    return {"type": "multiconsult_summary", "prompt_text": "\n".join(prompt_lines), "source_overview_df": source_overview_df, "detail_df": detail_df, "sample_summary_df": summary_df.drop(columns=["class_rank"], errors="ignore"), "exceedance_df": exceedance_df.drop(columns=["class_rank"], errors="ignore"), "excerpt_df": excerpt_df, "threshold_df": threshold_df, "counts": counts, "cell_fill_lookup": cell_fill_lookup, "metadata_lines": metadata_lines}

def extract_drill_data(files):
    if not files: return {"prompt_text": "Ingen Excel/CSV-data ble lastet opp.", "source_overview_df": pd.DataFrame(), "sample_summary_df": pd.DataFrame(), "exceedance_df": pd.DataFrame(), "excerpt_df": pd.DataFrame(), "threshold_df": pd.DataFrame(), "counts": {}, "cell_fill_lookup": {}, "metadata_lines": []}
    
    prompt_parts, source_overview, sample_summaries, exceedances, excerpts, thresholds, counts, cell_fill_lookup, metadata_lines, excerpt_offset = [], [], [], [], [], [], {tk: 0 for tk in ["TK1", "TK2", "TK3", "TK4", "TK5", "TK>5"]}, {}, [], 0

    for f in files:
        file_name, file_bytes = clean_pdf_text(f.name), f.getvalue() if hasattr(f, "getvalue") else f.read()
        extracted = extract_multiconsult_summary(file_name, file_bytes) if file_name.lower().endswith((".xlsx", ".xlsm", ".xls")) else None

        if extracted:
            prompt_parts.append(extracted["prompt_text"])
            if not extracted["source_overview_df"].empty: source_overview.append(extracted["source_overview_df"])
            if not extracted["sample_summary_df"].empty: sample_summaries.append(extracted["sample_summary_df"])
            if not extracted["exceedance_df"].empty: exceedances.append(extracted["exceedance_df"])
            if not extracted["excerpt_df"].empty:
                excerpts.append(extracted["excerpt_df"])
                for (ridx, col), fill in extracted["cell_fill_lookup"].items(): cell_fill_lookup[(excerpt_offset + ridx, col)] = fill
                excerpt_offset += len(extracted["excerpt_df"])
            if not extracted["threshold_df"].empty: thresholds.append(extracted["threshold_df"])
            metadata_lines.extend(extracted.get("metadata_lines", []))
            for tk, val in extracted.get("counts", {}).items(): counts[tk] = counts.get(tk, 0) + int(val)
            continue

        generic_df = read_generic_table(file_name, file_bytes)
        if generic_df is not None:
            prompt_parts.append(f"KILDE: {file_name}\n{generic_df.head(20).to_csv(index=False, sep=';')}")
            source_overview.append(pd.DataFrame([{"Fil": file_name, "Datatype": "Generisk tabellfil", "Ark": "Første ark", "Innhold": f"{len(generic_df)} rader / {len(generic_df.columns)} kolonner", "Kommentar": "Ikke gjenkjent som standard analysematrise - vist som strukturert utdrag"}]))
            preview = generic_df.head(15).copy()
            if len(preview.columns) > 8: preview = preview.iloc[:, :8]
            preview.insert(0, "Fil", file_name)
            excerpts.append(preview)
            excerpt_offset += len(preview)
        else:
            prompt_parts.append(f"KILDE: {file_name}\n[Kunne ikke lese filinnholdet strukturert]")

    return {
        "prompt_text": "\n\n".join(part for part in prompt_parts if part) or "Ingen Excel/CSV-data ble lastet opp.",
        "source_overview_df": pd.concat(source_overview, ignore_index=True) if source_overview else pd.DataFrame(),
        "sample_summary_df": pd.concat(sample_summaries, ignore_index=True) if sample_summaries else pd.DataFrame(),
        "exceedance_df": pd.concat(exceedances, ignore_index=True) if exceedances else pd.DataFrame(),
        "excerpt_df": pd.concat(excerpts, ignore_index=True) if excerpts else pd.DataFrame(),
        "threshold_df": thresholds[0] if thresholds else pd.DataFrame(),
        "counts": counts, "cell_fill_lookup": cell_fill_lookup, "metadata_lines": metadata_lines
    }

# --- 6. DYNAMISK PDF MOTOR (CORPORATE LAYOUT) ---
def split_ai_sections(content: str):
    sections = []
    current = None
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            if current: sections.append(current)
            current = {"title": ironclad_text_formatter(line.lstrip("#").strip()), "lines": []}
            continue
        if current is None: current = {"title": "1. SAMMENDRAG OG KONKLUSJON", "lines": []}
        current["lines"].append(raw_line.rstrip())
    if current: sections.append(current)
    return sections

def is_subheading_line(line: str) -> bool:
    clean = line.strip()
    if not clean: return False
    if clean.startswith("##"): return True
    if clean.endswith(":") and len(clean) < 80 and len(clean.split()) <= 7: return True
    if clean == clean.upper() and any(ch.isalpha() for ch in clean) and len(clean) < 70: return True
    return False

def is_bullet_line(line: str) -> bool:
    return bool(re.match(r"^([-*•]|\d+\.)\s+", line.strip()))

def strip_bullet(line: str) -> str:
    return re.sub(r"^([-*•]|\d+\.)\s+", "", line.strip())

class BuiltlyCorporatePDF(FPDF):
    def header(self):
        if self.page_no() == 1: return
        self.set_y(11)
        self.set_text_color(88, 94, 102)
        self.set_font("Helvetica", "", 8)
        self.cell(0, 4, clean_pdf_text(self.header_left), 0, 0, "L")
        self.cell(0, 4, clean_pdf_text(self.header_right), 0, 1, "R")
        self.set_draw_color(188, 192, 197)
        self.line(18, 18, 192, 18)
        self.set_y(24)

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(210, 214, 220)
        self.line(18, 285, 192, 285)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(110, 114, 119)
        self.cell(60, 5, clean_pdf_text(self.doc_code), 0, 0, "L")
        self.cell(70, 5, clean_pdf_text("Utkast - krever faglig kontroll"), 0, 0, "C")
        self.cell(0, 5, clean_pdf_text(f"Side {self.page_no()}"), 0, 0, "R")

    def ensure_space(self, needed_height: float):
        if self.get_y() + needed_height > 274:
            self.add_page()
            self.set_y(26)

    def body_paragraph(self, text, first=False):
        text = ironclad_text_formatter(text)
        if not text: return
        self.set_x(20)
        self.set_font("Helvetica", "", 10.2 if not first else 10.6)
        self.set_text_color(35, 39, 43)
        self.multi_cell(170, 5.5 if not first else 5.7, text)
        self.ln(1.6)

    def subheading(self, text):
        text = ironclad_text_formatter(text.replace("##", "").rstrip(":"))
        self.ensure_space(14)
        self.ln(2)
        self.set_x(20)
        self.set_font("Helvetica", "B", 10.8)
        self.set_text_color(48, 64, 86)
        self.cell(0, 6, clean_pdf_text(text.upper()), 0, 1)
        self.set_draw_color(225, 229, 234)
        self.line(20, self.get_y(), 190, self.get_y())
        self.ln(2)

    def bullets(self, items, numbered=False):
        for idx, item in enumerate(items, start=1):
            clean = ironclad_text_formatter(item)
            if not clean: continue
            self.ensure_space(10)
            self.set_font("Helvetica", "", 10.1)
            self.set_text_color(35, 39, 43)
            start_y = self.get_y()
            self.set_xy(22, start_y)
            self.cell(6, 5.2, f"{idx}." if numbered else "-", 0, 0, "L")
            self.set_xy(28, start_y)
            self.multi_cell(162, 5.2, clean)
            self.ln(0.8)

    def section_title(self, title: str):
        self.ensure_space(35)
        self.ln(2)
        title = ironclad_text_formatter(title)
        num_match = re.match(r"^(\d+\.?\d*)\s*(.*)$", title)
        number, text = (num_match.group(1).rstrip("."), num_match.group(2).strip()) if num_match and (num_match.group(1).endswith(".") or num_match.group(2)) else (None, title)
        
        self.set_font("Helvetica", "B", 17)
        self.set_text_color(36, 50, 72)
        start_y = self.get_y()
        if number:
            self.set_xy(20, start_y)
            self.cell(12, 8, clean_pdf_text(number), 0, 0, "L")
            self.set_xy(34, start_y)
            self.multi_cell(156, 8, clean_pdf_text(text.upper()), 0, "L")
        else:
            self.set_xy(20, start_y)
            self.multi_cell(170, 8, clean_pdf_text(text.upper()), 0, "L")
        self.set_draw_color(204, 209, 216)
        self.line(20, self.get_y() + 1, 190, self.get_y() + 1)
        self.ln(5)

    def rounded_rect(self, x, y, w, h, r, style="", corners="1234"):
        try: super().rounded_rect(x, y, w, h, r, style, corners)
        except Exception: self.rect(x, y, w, h, style if style in {"F", "FD", "DF"} else "")

    def kv_card(self, items, x=None, width=80, title=None):
        if x is None: x = self.get_x()
        height = 10 + (len(items) * 6.3) + (7 if title else 0)
        self.ensure_space(height + 3)
        start_y = self.get_y()
        self.set_fill_color(245, 247, 249)
        self.set_draw_color(214, 219, 225)
        self.rounded_rect(x, start_y, width, height, 4, "1234", "DF")
        yy = start_y + 5
        if title:
            self.set_xy(x + 4, yy)
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(48, 64, 86)
            self.cell(width - 8, 5, clean_pdf_text(title.upper()), 0, 1)
            yy += 7
        for label, value in items:
            self.set_xy(x + 4, yy)
            self.set_font("Helvetica", "B", 8.6)
            self.set_text_color(72, 79, 87)
            self.cell(28, 5, clean_pdf_text(label), 0, 0)
            self.set_font("Helvetica", "", 8.6)
            self.set_text_color(35, 39, 43)
            self.multi_cell(width - 34, 5, clean_pdf_text(value))
            yy = self.get_y() + 1
        self.set_y(max(self.get_y(), start_y + height))

    def highlight_box(self, title: str, items, fill=(245, 247, 250), accent=(50, 77, 106)):
        box_h = 16 + (len(items) * 6.5)
        self.ensure_space(box_h + 5)
        x, y = 20, self.get_y()
        self.set_fill_color(*fill)
        self.set_draw_color(217, 223, 230)
        self.rounded_rect(x, y, 170, box_h, 4, "1234", "DF")
        self.set_fill_color(*accent)
        self.rect(x, y, 3, box_h, "F")
        self.set_xy(x + 6, y + 4)
        self.set_font("Helvetica", "B", 10.5)
        self.set_text_color(*accent)
        self.cell(0, 5, clean_pdf_text(title.upper()), 0, 1)
        self.set_text_color(35, 39, 43)
        self.set_font("Helvetica", "", 10)
        yy = y + 10
        for item in items:
            self.set_xy(x + 8, yy)
            self.cell(5, 5, "-", 0, 0)
            self.multi_cell(154, 5, clean_pdf_text(item))
            yy = self.get_y() + 1
        self.set_y(y + box_h + 3)

    def stats_row(self, stats):
        if not stats: return
        self.ensure_space(26)
        box_w, gap, x0, y = 40, 3.3, 20, self.get_y()
        for idx, (label, value, class_code) in enumerate(stats):
            x = x0 + idx * (box_w + gap)
            self.set_fill_color(*CLASS_FILL.get(class_code, (245, 247, 249)))
            self.set_draw_color(216, 220, 226)
            self.rounded_rect(x, y, box_w, 20, 3, "1234", "DF")
            self.set_xy(x, y + 3)
            self.set_font("Helvetica", "B", 15)
            self.set_text_color(33, 39, 45)
            self.cell(box_w, 7, clean_pdf_text(str(value)), 0, 1, "C")
            self.set_x(x)
            self.set_font("Helvetica", "", 7.8)
            self.set_text_color(75, 80, 87)
            self.multi_cell(box_w, 4, clean_pdf_text(label), 0, "C")
        self.set_y(y + 24)

    def figure_image(self, image_path, width=82, caption=""):
        img = Image.open(image_path)
        height = width * (img.height / img.width)
        self.ensure_space(height + 15)
        x, y = self.get_x(), self.get_y()
        self.set_draw_color(219, 223, 228)
        self.rect(x, y, width, height)
        self.image(image_path, x=x, y=y, w=width)
        self.set_y(y + height + 2)
        if caption:
            self.set_x(x)
            self.set_font("Helvetica", "I", 7.7)
            self.set_text_color(104, 109, 116)
            self.multi_cell(width, 4, clean_pdf_text(caption), 0, "C")
        self.set_y(y + height + 10)

    def table_image(self, img_path, width=170, caption=""):
        img = Image.open(img_path)
        height = width * (img.height / img.width)
        self.ensure_space(height + 15)
        x, y = 20, self.get_y()
        self.image(img_path, x=x, y=y, w=width)
        self.set_y(y + height + 2)
        if caption:
            self.set_x(20)
            self.set_font("Helvetica", "I", 7.7)
            self.set_text_color(104, 109, 116)
            self.multi_cell(width, 4, clean_pdf_text(caption), 0, "L")
        self.ln(6)


def build_cover_page(pdf, project_data, client, recent_img, hist_img, source_text):
    pdf.add_page()
    pdf.set_draw_color(120, 124, 130)
    pdf.line(18, 18, 192, 18)
    if os.path.exists("logo.png"):
        try: pdf.image("logo.png", x=156, y=242, w=28)
        except: pass

    pdf.set_xy(20, 28)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(86, 90, 95)
    pdf.cell(80, 6, clean_pdf_text("RAPPORT"), 0, 1, "L")

    pdf.set_x(20)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(28, 33, 41)
    pdf.multi_cell(100, 10, clean_pdf_text(project_data.get("p_name", "Geo & Miljø")))
    pdf.set_x(20)
    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(64, 68, 74)
    pdf.multi_cell(95, 7, clean_pdf_text("Miljøteknisk grunnundersøkelse, geoteknisk vurdering og overordnet tiltaksplan"))

    pdf.set_xy(118, 34)
    pdf.kv_card([("Oppdragsgiver", client or "-"), ("Emne", "Geo & Miljø (RIG-M)"), ("Dato / revisjon", datetime.now().strftime("%d.%m.%Y") + " / 01"), ("Dokumentkode", "Builtly-RIGM-001")], x=118, width=64)

    img_paths = []
    if recent_img:
        try: img_paths.append((save_temp_image(recent_img.convert("RGB"), ".jpg"), f"Nyere ortofoto ({source_text})"))
        except: pass
    elif hist_img: # Kun som fallback hvis recent mangler
        try: img_paths.append((save_temp_image(hist_img.convert("RGB"), ".jpg"), "Historisk flyfoto"))
        except: pass

    if img_paths:
        img_path, caption = img_paths[0]
        with Image.open(img_path) as tmp_img:
            aspect = tmp_img.height / max(tmp_img.width, 1)
        
        # Maksimer bildet på forsiden (maks bredde 162, maks høyde 110)
        w = 162
        h = w * aspect
        if h > 110:
            h = 110
            w = h / aspect
        
        x = 20 + (162 - w) / 2
        y = 115
        
        pdf.set_xy(x, y)
        pdf.figure_image(img_path, width=w, caption=caption)
    else:
        pdf.set_fill_color(244, 246, 248)
        pdf.set_draw_color(220, 224, 228)
        pdf.rounded_rect(20, 118, 162, 78, 4, "1234", "DF")
        pdf.set_xy(24, 146)
        pdf.set_font("Helvetica", "I", 12)
        pdf.set_text_color(112, 117, 123)
        pdf.multi_cell(150, 6, clean_pdf_text("Kartgrunnlag legges inn automatisk eller via manuell opplasting i modulen."), 0, "C")

    # Justerer ansvarsfraskrivelsen slik at den alltid ligger trygt i bunnen (uavhengig av bildehøyde)
    pdf.set_xy(20, 255)
    pdf.set_font("Helvetica", "", 8.8)
    pdf.set_text_color(104, 109, 116)
    pdf.multi_cell(160, 4.5, clean_pdf_text("Rapporten er generert av Builtly RIG-M AI på bakgrunn av prosjektdata, opplastet laboratoriemateriale og tilgjengelig kartgrunnlag. Dokumentet er et arbeidsutkast og skal underlegges faglig kontroll før bruk i prosjektering, byggesak eller myndighetsdialog."))

def build_toc_page(pdf, include_appendices=False):
    pdf.add_page()
    pdf.section_title("INNHOLDSFORTEGNELSE")
    items = ["1. Sammendrag og konklusjon", "2. Innledning og prosjektbeskrivelse", "3. Kartverket og historisk lokasjon", "4. Utførte grunnundersøkelser", "5. Resultater: grunnforhold og forurensning", "6. Geotekniske vurderinger", "7. Tiltaksplan og massehåndtering"]
    if include_appendices: items.extend(["Vedlegg A. Sammenstilling av analyseresultater", "Vedlegg B. Tilstandsklassegrenser (utdrag)"])
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_text_color(45, 49, 55)
    for item in items:
        pdf.ensure_space(9)
        y = pdf.get_y()
        pdf.set_x(22)
        pdf.cell(0, 6, clean_pdf_text(item), 0, 0, "L")
        pdf.set_draw_color(225, 229, 234)
        pdf.line(22, y + 6, 188, y + 6)
        pdf.ln(8)
    pdf.ln(4)
    pdf.highlight_box("Dokumentoppsett", ["Rapporten er bygget med tydelig seksjonshierarki, figurtekster og dedikerte tabellvedlegg for laboratoriedata.", "Opplastede lab-data sammenstilles i egne analyseresultattabeller fremfor å ligge skjult som råtekst i brødteksten."])

def render_maps(pdf, recent_img, hist_img, source_text):
    paths = []
    if recent_img: paths.append((save_temp_image(recent_img.convert("RGB"), ".jpg"), f"Figur 1. Nyere ortofoto. Kilde: {source_text}"))
    if hist_img: paths.append((save_temp_image(hist_img.convert("RGB"), ".jpg"), "Figur 2. Historisk flyfoto"))
    if not paths:
        pdf.highlight_box("Kartgrunnlag", ["Ingen kart- eller flyfoto ble lagt ved i denne genereringen."])
        return
    
    widths, x_positions, max_height = [82, 82], [20, 108], 0
    for idx, (img_path, caption) in enumerate(paths[:2]):
        img = Image.open(img_path)
        max_height = max(max_height, widths[idx] * (img.height / img.width))
    
    pdf.ensure_space(max_height + 18)
    start_y = pdf.get_y()
    for idx, (img_path, caption) in enumerate(paths[:2]):
        pdf.set_xy(x_positions[idx], start_y)
        pdf.figure_image(img_path, width=widths[idx], caption=caption)
    pdf.set_y(start_y + max_height + 14)

def render_ai_section_body(pdf, lines):
    paragraph_buffer, bullet_buffer, first_para, empty_line_count = [], [], True, 0

    def flush_paragraph():
        nonlocal paragraph_buffer, first_para
        if paragraph_buffer:
            text = " ".join(line.strip() for line in paragraph_buffer if line.strip())
            if text:
                pdf.body_paragraph(text, first=first_para)
                first_para = False
        paragraph_buffer = []

    def flush_bullets():
        nonlocal bullet_buffer
        if bullet_buffer:
            pdf.bullets([strip_bullet(item) for item in bullet_buffer], numbered=all(re.match(r"^\d+\.\s+", item.strip()) for item in bullet_buffer))
        bullet_buffer = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_bullets()
            empty_line_count += 1
            if empty_line_count == 1: pdf.ln(3) # Unngå evig lange blanke avsnitt (Fikser "Luften")
            continue
        empty_line_count = 0
        if is_subheading_line(line):
            flush_paragraph()
            flush_bullets()
            pdf.subheading(line)
            continue
        if is_bullet_line(line):
            flush_paragraph()
            bullet_buffer.append(line)
            continue
        flush_bullets()
        paragraph_buffer.append(line)

    flush_paragraph()
    flush_bullets()

def build_lab_summary_texts(lab_package):
    counts = lab_package.get("counts", {})
    summary_items = []
    if counts:
        summary_items.append(f"Prøvepunkter i TK2 eller høyere: {sum([counts.get(tk, 0) for tk in ['TK2', 'TK3', 'TK4', 'TK5', 'TK>5']])}")
        summary_items.append(f"Prøvepunkter med høyeste nivå i TK3 eller høyere: {sum([counts.get(tk, 0) for tk in ['TK3', 'TK4', 'TK5', 'TK>5']])}")
        summary_items.append(f"Prøvepunkter med høyeste nivå i TK5 eller over: {counts.get('TK5', 0) + counts.get('TK>5', 0)}")
    if not lab_package.get("exceedance_df", pd.DataFrame()).empty:
        for _, row in lab_package["exceedance_df"].head(3).iterrows():
            summary_items.append(f"{row['Prøvepunkt']} ({row['Dybde (m)']} m): {row['Styrende parameter']} = {row['Styrende verdi']} ({row['Høyeste klasse']}).")
    return summary_items[:6]

def create_full_report_pdf(name, client, content, recent_img, hist_img, source_text, lab_package, project_data):
    pdf = BuiltlyCorporatePDF("P", "mm", "A4")
    pdf.set_auto_page_break(False)
    pdf.set_margins(18, 18, 18)
    pdf.header_left, pdf.header_right, pdf.doc_code = clean_pdf_text(project_data.get("p_name", name)), clean_pdf_text("Builtly | RIG-M"), clean_pdf_text("Builtly-RIGM-001")

    build_cover_page(pdf, project_data, client, recent_img, hist_img, source_text)
    build_toc_page(pdf, include_appendices=not lab_package.get("excerpt_df", pd.DataFrame()).empty)

    sections = split_ai_sections(content) or [{"title": "1. SAMMENDRAG OG KONKLUSJON", "lines": [content]}]

    # DYNAMISK SIDEFLYTT (Fikser de halvtomme sidene!)
    pdf.add_page()
    for idx, section in enumerate(sections):
        title = section.get("title", "")
        
        # Hvis det er et vedlegg, tvang-start på ny side, ellers la det flyte med sikret avstand
        if title.startswith("Vedlegg"):
            pdf.add_page()
        elif idx > 0:
            pdf.ensure_space(35)
            if pdf.get_y() > 35: pdf.ln(8)

        pdf.section_title(title)

        if title.startswith("1."):
            # Dynamisk plassering av side-by-side kort (Fikser overlapp/gap)
            pdf.ensure_space(50)
            start_y = pdf.get_y()
            pdf.kv_card([("Prosjekt", project_data.get("p_name", name)), ("Lokasjon", f"{project_data.get('adresse', '')}, {project_data.get('kommune', '')}".strip(", ")), ("Gnr/Bnr", f"{project_data.get('gnr', '-')}/{project_data.get('bnr', '-')}") , ("Byggtype", project_data.get("b_type", "-")), ("BTA", f"{project_data.get('bta', 0)} m2")], x=20, width=82, title="Prosjektgrunnlag")
            end_left = pdf.get_y()
            
            pdf.set_xy(108, start_y)
            pdf.kv_card([("Kartgrunnlag", "Nyere + historisk" if recent_img and hist_img else "Delvis kartgrunnlag" if recent_img or hist_img else "Ikke vedlagt"), ("Lab-data", "Opplastet" if not lab_package.get("source_overview_df", pd.DataFrame()).empty else "Ikke opplastet"), ("Regelverk", project_data.get("land", "Norge"))], x=108, width=82, title="Datagrunnlag")
            end_right = pdf.get_y()
            
            pdf.set_y(max(end_left, end_right) + 6)
            
            if summary_items := build_lab_summary_texts(lab_package):
                pdf.highlight_box("Nøkkelfunn fra lab-data", summary_items)
                pdf.ln(4)

        if title.startswith("3."): render_maps(pdf, recent_img, hist_img, source_text)

        if title.startswith("4.") and not lab_package.get("source_overview_df", pd.DataFrame()).empty:
            source_table = render_table_image(lab_package["source_overview_df"], title="Opplastet analysegrunnlag", subtitle="Maskinelt lest og strukturert for rapportering", note="Tabellen viser hvilke kilder som faktisk ligger til grunn for vurderingene i denne genereringen.")
            pdf.table_image(save_temp_image(source_table), width=170, caption="Tabell 1. Oversikt over opplastet lab- og tabellgrunnlag.")

        render_ai_section_body(pdf, section.get("lines", []))

        if title.startswith("5.") and not lab_package.get("sample_summary_df", pd.DataFrame()).empty:
            pdf.stats_row([("TK1 / rene", lab_package.get("counts", {}).get("TK1", 0), "TK1"), ("TK2", lab_package.get("counts", {}).get("TK2", 0), "TK2"), ("TK3", lab_package.get("counts", {}).get("TK3", 0), "TK3"), ("TK4-5", sum([lab_package.get("counts", {}).get(tk, 0) for tk in ["TK4", "TK5", "TK>5"]]), "TK5")])

            if not lab_package.get("exceedance_df", pd.DataFrame()).empty:
                top_table = render_table_image(lab_package["exceedance_df"].head(12), title="Høyeste påviste nivå per prøvepunkt", subtitle="Styrende parameter og klassifisering", row_class_column="Høyeste klasse", note="Radfarge følger høyeste registrerte tilstandsklasse per prøvepunkt.")
                pdf.table_image(save_temp_image(top_table), width=170, caption="Tabell 2. Sammendrag av styrende funn i opplastet laboratoriedata.")

            if not (excerpt_df := lab_package.get("excerpt_df", pd.DataFrame())).empty:
                preview = excerpt_df.head(12).copy()
                preview_img = render_table_image(preview, title="Analyseresultater og massebeskrivelser (utdrag)", subtitle="Fremstilt som rapporttabell i stedet for rå tekstutskrift", row_class_column="Høyeste klasse" if "Høyeste klasse" in preview.columns else None, cell_fill_lookup={(ridx, col): fill for (ridx, col), fill in lab_package.get("cell_fill_lookup", {}).items() if ridx < len(preview)}, note="Celler med farge markerer klassifiserte analyseresultater for de mest styrende parameterne.")
                pdf.table_image(save_temp_image(preview_img), width=170, caption="Tabell 3. Laboratoriedata presentert i vedleggsformat med klassifiserte nøkkelparametere.")

    if not (excerpt_df := lab_package.get("excerpt_df", pd.DataFrame())).empty:
        chunks = split_dataframe(excerpt_df, 12)
        for idx, chunk in enumerate(chunks, start=1):
            pdf.add_page()
            pdf.section_title(f"Vedlegg A. Sammenstilling av analyseresultater ({idx}/{len(chunks)})")
            raw_img = render_table_image(chunk.reset_index(drop=True), title="Vedleggstabell - analyseresultater", subtitle="Opplastet lab-data i rapportvennlig vedleggsformat", row_class_column="Høyeste klasse" if "Høyeste klasse" in chunk.columns else None, cell_fill_lookup={(ridx - ((idx - 1) * 12), col): fill for (ridx, col), fill in {(r, c): f for (r, c), f in lab_package.get("cell_fill_lookup", {}).items() if r >= (idx - 1) * 12 and r < idx * 12}.items()}, note="Utvalgte analyttkolonner er beholdt for å gjøre vedlegget lesbart i A4-format.")
            pdf.table_image(save_temp_image(raw_img), width=170, caption=f"Vedlegg A{idx}. Strukturert tabellutdrag fra opplastet laboratoriedata.")

    if not (threshold_df := lab_package.get("threshold_df", pd.DataFrame())).empty:
        pdf.add_page()
        pdf.section_title("Vedlegg B. Tilstandsklassegrenser (utdrag)")
        threshold_img = render_table_image(threshold_df, title="Tilstandsklassegrenser brukt i klassifisering", subtitle="Utvalgte parametere fra opplastet vedlegg", row_class_column="Klasse", note="Grenseverdiene er brukt til å markere relevante analyttceller i tabellene over.")
        pdf.table_image(save_temp_image(threshold_img), width=165, caption="Vedlegg B. Utdrag av tilstandsklassegrenser for sentrale analyttgrupper.")

    out = pdf.output(dest="S")
    return bytes(out) if isinstance(out, (bytes, bytearray)) else out.encode("latin-1")

# --- 7. UI OG RESTERENDE KODE ---
st.markdown("<style>/* Skjuler Streamlit-branding */\n#MainMenu {visibility: hidden;}\nfooter {visibility: hidden;}\nheader {visibility: hidden;}</style>", unsafe_allow_html=True)
st.markdown(
    """
<style>
    :root {
        --bg: #06111a; --panel: rgba(10, 22, 35, 0.78);
        --stroke: rgba(120, 145, 170, 0.18); --text: #f5f7fb; --muted: #9fb0c3; --soft: #c8d3df;
        --accent: #38bdf8; --radius-lg: 16px; --radius-xl: 24px;
    }
    html, body, [class*="css"] { font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .stApp { background-color: var(--bg) !important; color: var(--text); }
    header[data-testid="stHeader"] { visibility: hidden; height: 0; }
    .block-container { max-width: 1280px !important; padding-top: 1.5rem !important; padding-bottom: 4rem !important; }

    .brand-logo { height: 65px; filter: drop-shadow(0 0 18px rgba(120,220,225,0.08)); }

    .top-shell { margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center; }

    button[kind="primary"] { background: linear-gradient(135deg, rgba(56,194,201,0.96), rgba(120,220,225,0.96)) !important; color: #041018 !important; border: none !important; font-weight: 750 !important; border-radius: 12px !important; padding: 12px 24px !important; font-size: 1.05rem !important; transition: all 0.2s ease !important; }
    button[kind="primary"]:hover { transform: translateY(-2px) !important; box-shadow: 0 12px 24px rgba(56,194,201,0.25) !important; }
    button[kind="secondary"] { background-color: rgba(255,255,255,0.05) !important; color: #f8fafc !important; border: 1px solid rgba(120,145,170,0.3) !important; border-radius: 12px !important; font-weight: 650 !important; padding: 10px 24px !important; transition: all 0.2s; }
    button[kind="secondary"]:hover { background-color: rgba(56,194,201,0.1) !important; border-color: var(--accent) !important; color: var(--accent) !important; transform: translateY(-2px) !important;}

    div[data-baseweb="base-input"], div[data-baseweb="select"] > div, .stTextArea > div > div > div { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; border-radius: 8px !important; }
    .stTextInput input, .stNumberInput input, .stTextArea textarea, div[data-baseweb="select"] * { background-color: transparent !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; border: none !important; box-shadow: none !important; }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus { border: none !important; }
    div[data-baseweb="base-input"]:focus-within, div[data-baseweb="select"] > div:focus-within, .stTextArea > div > div > div:focus-within { border-color: var(--accent) !important; box-shadow: 0 0 0 1px rgba(56, 194, 201, 0.5) !important; }
    ul[data-baseweb="menu"] { background-color: #0d1824 !important; border: 1px solid rgba(120, 145, 170, 0.4) !important; }
    ul[data-baseweb="menu"] li { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
    ul[data-baseweb="menu"] li:hover { background-color: rgba(56, 194, 201, 0.1) !important; }
    div[data-testid="InputInstructions"], div[data-testid="InputInstructions"] > span { color: #9fb0c3 !important; -webkit-text-fill-color: #9fb0c3 !important; }
    .stTextInput label, .stSelectbox label, .stNumberInput label, .stTextArea label, .stFileUploader label { color: #c8d3df !important; font-weight: 600 !important; font-size: 0.95rem !important; margin-bottom: 4px !important; }

    div[data-testid="stExpander"] details, div[data-testid="stExpander"] details summary, div[data-testid="stExpander"] { background-color: #0c1520 !important; color: #f5f7fb !important; border-radius: 12px !important; }
    div[data-testid="stExpander"] details summary:hover { background-color: rgba(255,255,255,0.03) !important; }
    div[data-testid="stExpander"] details summary p { color: #f5f7fb !important; font-weight: 650 !important; }
    div[data-testid="stExpander"] { border: 1px solid rgba(120,145,170,0.2) !important; margin-bottom: 1rem !important; }
    div[data-testid="stExpanderDetails"] { background: transparent !important; color: #f5f7fb !important; }
    div[data-testid="stExpanderDetails"] > div > div > div { background-color: transparent !important; }

    [data-testid="stFileUploaderDropzone"] { background-color: #0d1824 !important; border: 1px dashed rgba(120, 145, 170, 0.6) !important; border-radius: 12px !important; padding: 2rem !important; }
    [data-testid="stFileUploaderDropzone"]:hover { border-color: #38c2c9 !important; background-color: rgba(56, 194, 201, 0.05) !important; }
    [data-testid="stFileUploaderDropzone"] * { color: #c8d3df !important; }
    [data-testid="stFileUploaderFileData"] { background-color: rgba(255,255,255,0.02) !important; color: #f5f7fb !important; border-radius: 8px !important;}
    [data-testid="stAlert"] { background-color: rgba(56, 194, 201, 0.05) !important; border: 1px solid rgba(56, 194, 201, 0.2) !important; border-radius: 12px !important; }
    [data-testid="stAlert"] * { color: #f5f7fb !important; }
</style>
""",
    unsafe_allow_html=True,
)

# --- 8. SESSION STATE (UI) ---
if "project_data" not in st.session_state:
    st.session_state.project_data = {
        "p_name": "", "c_name": "", "p_desc": "", "adresse": "", "kommune": "", "gnr": "", "bnr": "", "b_type": "Næring", "etasjer": 1, "bta": 0, "land": "Norge"
    }

if "geo_maps" not in st.session_state:
    st.session_state.geo_maps = {"recent": None, "historical": None, "source": "Ikke hentet"}

if st.session_state.project_data.get("p_name") == "":
    if SSOT_FILE.exists():
        with open(SSOT_FILE, "r", encoding="utf-8") as f:
            st.session_state.project_data = json.load(f)

if st.session_state.project_data.get("p_name") in ["", "Nytt Prosjekt"]:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(f"<div style='margin-bottom:2rem;'>{logo_html}</div>")
    st.warning("⚠️ **Handling kreves:** Du må sette opp prosjektdataen før du kan bruke denne modulen.")
    if find_page("Project"):
        if st.button("⚙️ Gå til Project Setup", type="primary"):
            st.switch_page(find_page("Project"))
    st.stop()

# SUPERVIKTIG: Definerer variabelen!
pd_state = st.session_state.project_data

# --- 9. HEADER ---
top_l, top_r = st.columns([4, 1])
with top_l:
    logo_html = f'<img src="{logo_data_uri()}" class="brand-logo">' if logo_data_uri() else '<h2 style="margin:0; color:white;">Builtly</h2>'
    render_html(logo_html)
with top_r:
    st.markdown("<div style='margin-top: 0.5rem;'></div>", unsafe_allow_html=True)
    if st.button("← Tilbake til SSOT", use_container_width=True, type="secondary"):
        st.switch_page(find_page("Project"))

st.markdown("<hr style='border-color: rgba(120,145,170,0.1); margin-top: -1rem; margin-bottom: 2rem;'>", unsafe_allow_html=True)

# --- 10. KARTVERKET + GOOGLE MAPS FALLBACK ---
def fetch_kartverket_og_google(adresse, kommune, gnr, bnr, api_key):
    nord, ost = None, None
    adr_clean = adresse.replace(",", "").strip() if adresse else ""
    kom_clean = kommune.replace(",", "").strip() if kommune else ""

    queries = []
    if adr_clean and kom_clean:
        queries.append(f"{adr_clean} {kom_clean}")
    if adr_clean:
        queries.append(adr_clean)
    if gnr and bnr and kom_clean:
        queries.append(f"{kom_clean} {gnr}/{bnr}")

    for q in queries:
        safe_query = urllib.parse.quote(q)
        url = f"https://ws.geonorge.no/adresser/v1/sok?sok={safe_query}&fuzzy=true&utkoordsys=25833&treffPerSide=1"
        try:
            resp = requests.get(url, timeout=4)
            if resp.status_code == 200 and resp.json().get("adresser"):
                hit = resp.json()["adresser"][0]
                nord = hit.get("representasjonspunkt", {}).get("nord")
                ost = hit.get("representasjonspunkt", {}).get("øst")
                break
        except Exception:
            pass

    if nord and ost:
        min_x, max_x = float(ost) - 150, float(ost) + 150
        min_y, max_y = float(nord) - 150, float(nord) + 150
        url_orto = (
            "https://wms.geonorge.no/skwms1/wms.nib?service=WMS&request=GetMap&version=1.1.1"
            f"&layers=ortofoto&styles=&srs=EPSG:25833&bbox={min_x},{min_y},{max_x},{max_y}&width=800&height=800&format=image/png"
        )
        try:
            r1 = requests.get(url_orto, timeout=5)
            if r1.status_code == 200 and len(r1.content) > 5000:
                return Image.open(io.BytesIO(r1.content)).convert("RGB"), "Kartverket (Norge i Bilder)"
        except Exception:
            pass

    if api_key and (adr_clean or kom_clean):
        query = f"{adr_clean}, {kom_clean}, Norway"
        safe_query = urllib.parse.quote(query)
        url_gmaps = f"https://maps.googleapis.com/maps/api/staticmap?center={safe_query}&zoom=19&size=600x600&maptype=satellite&key={api_key}"
        try:
            r2 = requests.get(url_gmaps, timeout=5)
            if r2.status_code == 200:
                return Image.open(io.BytesIO(r2.content)).convert("RGB"), "Google Maps Satellite"
        except Exception:
            pass

    return None, "Kunne ikke hente kart."


# --- 11. UI FOR GEO MODUL ---
st.markdown("<h1 style='font-size: 2.5rem; margin-bottom: 0;'>🌍 Geo & Miljø (RIG-M)</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #9fb0c3; font-size: 1.1rem; margin-bottom: 2rem;'>AI-agent for miljøteknisk grunnundersøkelse og tiltaksplan.</p>", unsafe_allow_html=True)

st.success(f"✅ Prosjektdata for **{pd_state['p_name']}** er synkronisert fra Project SSOT.")

with st.expander("1. Prosjekt & Lokasjon (SSOT)", expanded=False):
    c1, c2 = st.columns(2)
    st.text_input("Prosjektnavn", value=pd_state["p_name"], disabled=True)
    st.text_input("Gnr/Bnr", value=f"{pd_state['gnr']} / {pd_state['bnr']}", disabled=True)

with st.expander("2. Kartgrunnlag & Ortofoto (Påkrevd)", expanded=True):
    st.markdown("For å vurdere potensialet for forurenset grunn, krever veilederen en visuell bedømming av nyere og historiske flyfoto. AI-en integrerer disse i rapporten.")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🌐 Hent kart automatisk", type="secondary"):
            with st.spinner("Søker i Matrikkel og Kartkatalog..."):
                img, source = fetch_kartverket_og_google(pd_state["adresse"], pd_state["kommune"], pd_state["gnr"], pd_state["bnr"], google_key)
                if img:
                    st.session_state.geo_maps["recent"] = img
                    st.session_state.geo_maps["source"] = source
                    st.success(f"✅ Hentet fra {source}!")
                else:
                    st.error("Fant ikke kart. Vennligst last opp manuelt.")

        if st.session_state.geo_maps["recent"]:
            st.image(st.session_state.geo_maps["recent"], caption=f"Valgt: {st.session_state.geo_maps['source']}", use_container_width=True)

    with col_b:
        st.markdown("##### ⚠️ Manuell opplasting (Fallback)")
        man_recent = st.file_uploader("Last opp nyere Ortofoto (Valgfritt)", type=["png", "jpg", "jpeg"])
        if man_recent:
            st.session_state.geo_maps["recent"] = Image.open(man_recent).convert("RGB")
            st.session_state.geo_maps["source"] = "Manuelt opplastet"

        man_hist = st.file_uploader("Last opp historisk flyfoto (F.eks. fra 1950 for å sjekke tidl. industri)", type=["png", "jpg", "jpeg"])
        if man_hist:
            st.session_state.geo_maps["historical"] = Image.open(man_hist).convert("RGB")

with st.expander("3. Laboratoriedata & Plantegninger", expanded=True):
    st.info("Slipp Excel/CSV-filer med prøvesvar her. AI-en leser verdiene og tilstandsklassifiserer massene.")

    if "project_images" in st.session_state and len(st.session_state.project_images) > 0:
        st.success(f"📎 Auto-hentet {len(st.session_state.project_images)} arkitekttegninger fra Project Setup for vurdering av gravegrenser!")

    files = st.file_uploader("Last opp Excel/CSV med boreresultater:", accept_multiple_files=True, type=["xlsx", "csv", "xls"])

st.markdown("<br>", unsafe_allow_html=True)
if st.button("🚀 GENERER GEOTEKNISK & MILJØTEKNISK RAPPORT", type="primary", use_container_width=True):
    if not st.session_state.geo_maps["recent"] and not st.session_state.geo_maps["historical"]:
        st.error("🛑 **Stopp:** Du må enten hente kart automatisk eller laste opp manuelt i Steg 2.")
        st.stop()

    with st.spinner("📊 Tolker lab-data, kart og arkitekttegninger..."):
        lab_package = extract_drill_data(files) if files else extract_drill_data([])
        extracted_data = lab_package["prompt_text"] if files else "Ingen opplastet lab-data. Vurderingen baseres på visuell befaring og historikk."

        images_for_geo = []
        if st.session_state.geo_maps["recent"]:
            images_for_geo.append(st.session_state.geo_maps["recent"])
        if st.session_state.geo_maps["historical"]:
            images_for_geo.append(st.session_state.geo_maps["historical"])
        if "project_images" in st.session_state and isinstance(st.session_state.project_images, list):
            images_for_geo.extend(st.session_state.project_images)

        try:
            valid_models = [m.name for m in genai.list_models() if "generateContent" in m.supported_generation_methods]
            valgt_modell = valid_models[0]
            for fav in ["models/gemini-1.5-pro", "models/gemini-1.5-flash"]:
                if fav in valid_models:
                    valgt_modell = fav
                    break
        except Exception:
            st.error("Kunne ikke koble til Google AI.")
            st.stop()

        model = genai.GenerativeModel(valgt_modell)
        hist_tekst = "Et historisk flyfoto er lagt ved." if st.session_state.geo_maps["historical"] else "Historisk flyfoto mangler, gjør en kvalifisert antakelse."

        prompt = f"""
        Du er Builtly RIG-M AI, en presis senior miljørådgiver og geotekniker.
        Skriv en formell, stram og troverdig "Miljøteknisk grunnundersøkelse og tiltaksplan for forurenset grunn" for:

        PROSJEKT: {pd_state['p_name']} ({pd_state['b_type']}, {pd_state['bta']} m2)
        LOKASJON: {pd_state['adresse']}, {pd_state['kommune']}. Gnr {pd_state['gnr']}/Bnr {pd_state['bnr']}.
        REGELVERK: {pd_state['land']}

        KUNDENS PROSJEKTNARRATIV: "{pd_state['p_desc']}"
        KARTSTATUS: {hist_tekst}

        STRUKTURERT LAB-DATA OG DOKUMENTGRUNNLAG:
        {extracted_data}

        KRITISKE INSTRUKSER FOR FORM:
        - Skriv med kortere avsnitt og tydelig faghierarki.
        - Bruk punktlister når du beskriver funn, risiko, usikkerhet og tiltak.
        - Ikke bruk markdown-tabeller.
        - Bruk underoverskrifter der det er naturlig, gjerne på formatet "## Datagrunnlag", "## Vurdering", "## Konsekvens" eller "## Anbefalte tiltak".
        - Vær konkret med analyttnavn, prøvepunkt, dybde og verdi når du omtaler laboratoriedata.
        - Hvis datagrunnlaget gjelder en annen eiendom eller har svakheter, skal dette beskrives tydelig og kritisk.

        KRITISKE INSTRUKSER FOR BEVIS:
        Jeg har lagt ved kart og potensielt arkitekttegninger.
        Du MÅ aktivt bevise i teksten at du har sett på bildene og analysert tallene fra tabellgrunnlaget.
        Skriv blant annet setninger som:
        - "Ut fra vedlagte kart/flyfoto observeres det at ..."
        - "Basert på opplastet analysetabell fremgår det at ..."
        - "Prøvepunkt SK.. i dybde ... viser ..."

        STRUKTUR (bruk kun disse overskriftene):
        # 1. SAMMENDRAG OG KONKLUSJON
        # 2. INNLEDNING OG PROSJEKTBESKRIVELSE
        # 3. KARTVERKET OG HISTORISK LOKASJON
        # 4. UTFØRTE GRUNNUNDERSØKELSER
        # 5. RESULTATER: GRUNNFORHOLD OG FORURENSNING
        # 6. GEOTEKNISKE VURDERINGER
        # 7. TILTAKSPLAN OG MASSEHÅNDTERING
        """

        try:
            res = model.generate_content([prompt] + images_for_geo)
            with st.spinner("Kompilerer RIG-PDF og sender til QA-kø..."):
                pdf_data = create_full_report_pdf(
                    pd_state["p_name"],
                    pd_state["c_name"],
                    res.text,
                    st.session_state.geo_maps["recent"],
                    st.session_state.geo_maps["historical"],
                    st.session_state.geo_maps["source"],
                    lab_package,
                    pd_state,
                )

                if "pending_reviews" not in st.session_state:
                    st.session_state.pending_reviews = {}
                if "review_counter" not in st.session_state:
                    st.session_state.review_counter = 1

                doc_id = f"PRJ-{datetime.now().strftime('%y')}-GEO{st.session_state.review_counter:03d}"
                st.session_state.review_counter += 1

                st.session_state.pending_reviews[doc_id] = {
                    "title": pd_state["p_name"],
                    "module": "RIG-M (Geo & Miljø)",
                    "drafter": "Builtly AI",
                    "reviewer": "Senior Miljørådgiver",
                    "status": "Pending Senior Review",
                    "class": "badge-pending",
                    "pdf_bytes": pdf_data,
                }

                st.session_state.generated_geo_pdf = pdf_data
                st.session_state.generated_geo_filename = f"Builtly_GEO_{pd_state['p_name'].replace(' ', '_')}.pdf"
                st.rerun()

        except Exception as e:
            st.error(f"Kritisk feil under generering: {e}")

# --- NEDLASTING OG NAVIGASJON ---
if "generated_geo_pdf" in st.session_state:
    st.success("✅ RIG-M Rapport er ferdigstilt og sendt til QA-køen!")

    col_dl, col_qa = st.columns(2)
    with col_dl:
        st.download_button("📄 Last ned Geo/Miljø-rapport", st.session_state.generated_geo_pdf, st.session_state.generated_geo_filename, type="primary", use_container_width=True)
    with col_qa:
        if find_page("Review"):
            if st.button("🔍 Gå til QA for å godkjenne", type="secondary", use_container_width=True):
                st.switch_page(find_page("Review"))
