from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import pandas as pd
from pypdf import PdfReader

from builtly_ai_fallback import generate_json_with_fallback
from builtly_module_prompts import DISCLAIMER_BY_LEVEL, MODULE_CONFIGS, format_prompt_context, module_schema, module_system_prompt
from builtly_public_data import gather_tdd_public_snapshot, run_tdd_portfolio_batch
from builtly_rulepacks import (
    TDD_CATEGORY_HINTS,
    TENDER_CATEGORY_HINTS,
    extract_keyword_windows,
    infer_category,
    merge_tender_rules,
)

TEXT_EXTENSIONS = {".txt", ".md", ".json", ".yaml", ".yml", ".csv", ".ifc", ".log"}
SPREADSHEET_EXTENSIONS = {".xlsx", ".xls"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_FILE_KB = 100 * 1024


def _safe_bytes(uploaded_file) -> bytes:
    if uploaded_file is None:
        return b""
    if hasattr(uploaded_file, "getvalue"):
        return uploaded_file.getvalue()
    if isinstance(uploaded_file, (str, Path)):
        return Path(uploaded_file).read_bytes()
    raise TypeError("Unsupported uploaded file type")


def sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def infer_revision_tag(filename: str) -> str:
    stem = Path(filename).stem
    patterns = [
        r"(?i)(rev[ _-]*[a-z0-9]+)",
        r"(?i)(v[ _-]*\d+(?:\.\d+)?)",
        r"(?i)(r[ _-]*\d+)",
        r"(20\d{2}[._-]\d{2}[._-]\d{2})",
        r"(\d{4}[._-]\d{2}[._-]\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, stem)
        if match:
            return match.group(1).replace("_", " ").replace("-", " ").strip()
    return "Base"


def canonical_stem(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"(?i)[ _-]*(rev[ _-]*[a-z0-9]+|v[ _-]*\d+(?:\.\d+)?|r[ _-]*\d+)$", "", stem)
    stem = re.sub(r"[ _-]*(20\d{2}[._-]\d{2}[._-]\d{2}|\d{4}[._-]\d{2}[._-]\d{2})$", "", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" _-")
    return stem.lower()


def _trim_text(text: str, max_chars: int = 5000) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n..."
    return text


def _extract_docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            raw = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        raw = re.sub(r"</w:p>", "\n", raw)
        raw = re.sub(r"<[^>]+>", "", raw)
        return _trim_text(raw)
    except Exception:
        return ""


def _extract_pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages: List[str] = []
        for page in reader.pages[:6]:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                continue
        return _trim_text("\n\n".join(pages))
    except Exception:
        return ""


def _extract_spreadsheet_text(data: bytes, ext: str) -> str:
    try:
        engine = "openpyxl" if ext == ".xlsx" else None
        sheet_map = pd.read_excel(io.BytesIO(data), sheet_name=None, engine=engine)
        parts: List[str] = []
        for idx, (sheet_name, df) in enumerate(sheet_map.items()):
            if idx >= 3:
                break
            sample = df.head(12).fillna("")
            parts.append(f"[Sheet: {sheet_name}]\n{sample.to_csv(index=False)}")
        return _trim_text("\n\n".join(parts))
    except Exception:
        return ""


def _extract_csv_text(data: bytes) -> str:
    try:
        df = pd.read_csv(io.BytesIO(data)).head(30).fillna("")
        return _trim_text(df.to_csv(index=False))
    except Exception:
        try:
            return _trim_text(data.decode("utf-8", errors="ignore"))
        except Exception:
            return ""


def _extract_ifc_text(data: bytes) -> str:
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return ""
    counts: Dict[str, int] = {}
    for entity in ["IFCWALL", "IFCSLAB", "IFCWINDOW", "IFCDOOR", "IFCSPACE", "IFCBUILDINGSTOREY"]:
        counts[entity] = len(re.findall(entity, text, flags=re.IGNORECASE))
    parts = [f"IFC entity counts: {json.dumps({k: v for k, v in counts.items() if v}, ensure_ascii=False)}"]
    areas = re.findall(r"(?i)(BRA|BTA|GFA|NTA)[^0-9]{0,8}(\d+[\.,]?\d*)", text)
    if areas:
        parts.append("Area markers: " + ", ".join(f"{a[0]}={a[1]}" for a in areas[:10]))
    parts.append("\n".join(text.splitlines()[:160]))
    return _trim_text("\n\n".join(parts))


def _extract_dxf_text(data: bytes) -> str:
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return ""
    entity_names = ["LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "TEXT", "MTEXT", "INSERT", "HATCH"]
    counts = {name: len(re.findall(rf"(?m)^\s*0\s*$\n\s*{name}\s*$", text, flags=re.IGNORECASE)) for name in entity_names}
    layers: List[str] = []
    pairs = text.splitlines()
    for idx in range(len(pairs) - 1):
        if pairs[idx].strip() == "8":
            layer = pairs[idx + 1].strip()
            if layer:
                layers.append(layer)
    preview = [f"DXF entity counts: {json.dumps({k: v for k, v in counts.items() if v}, ensure_ascii=False)}"]
    if layers:
        preview.append("Layers: " + ", ".join(list(dict.fromkeys(layers))[:12]))
    preview.append("\n".join(text.splitlines()[:120]))
    return _trim_text("\n\n".join(preview))


def _extract_dwg_text(data: bytes) -> str:
    header = data[:64]
    marker = re.findall(rb"AC1\d{3}", header)
    version = marker[0].decode("ascii", errors="ignore") if marker else "unknown"
    return f"DWG binary detected. Version marker: {version}. Connect external DWG parser for geometry-level extraction."


def _extract_text_preview(filename: str, data: bytes, max_chars: int = 5000) -> Dict[str, Any]:
    ext = Path(filename).suffix.lower()
    method = "metadata"
    preview = ""
    warning = ""
    nested_records: List[Dict[str, Any]] = []

    if ext == ".docx":
        method = "docx"
        preview = _extract_docx_text(data)
    elif ext == ".pdf":
        method = "pdf"
        preview = _extract_pdf_text(data)
    elif ext in SPREADSHEET_EXTENSIONS:
        method = "spreadsheet"
        preview = _extract_spreadsheet_text(data, ext)
    elif ext == ".csv":
        method = "csv"
        preview = _extract_csv_text(data)
    elif ext == ".ifc":
        method = "ifc"
        preview = _extract_ifc_text(data)
    elif ext == ".dxf":
        method = "dxf"
        preview = _extract_dxf_text(data)
    elif ext == ".dwg":
        method = "dwg"
        preview = _extract_dwg_text(data)
        warning = "DWG er indeksert som metadata og header-info i denne versjonen."
    elif ext in TEXT_EXTENSIONS:
        method = "text"
        preview = _trim_text(data.decode("utf-8", errors="ignore"), max_chars=max_chars)
    elif ext == ".zip":
        method = "zip"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for name in zf.namelist()[:10]:
                    if name.endswith("/"):
                        continue
                    blob = zf.read(name)
                    nested = _extract_text_preview(name, blob, max_chars=1800)
                    nested_records.append(
                        {
                            "filename": f"{filename}::{name}",
                            "ext": Path(name).suffix.lower(),
                            "size_kb": round(len(blob) / 1024, 1),
                            "sha12": sha12(blob),
                            "revision": infer_revision_tag(name),
                            "canonical_stem": canonical_stem(name),
                            "preview_method": nested["method"],
                            "preview_warning": nested["warning"],
                            "text_preview": nested["preview"],
                            "text_chars": len(nested["preview"] or ""),
                            "parent_archive": filename,
                        }
                    )
                preview = _trim_text("\n\n".join(f"[{item['filename']}]\n{item['text_preview']}" for item in nested_records[:6]))
        except Exception as exc:
            warning = f"Klarte ikke lese zip: {exc}"
    elif ext in IMAGE_EXTENSIONS:
        method = "image"
        warning = "Bildefiler er indeksert som metadata. OCR er ikke brukt i denne motoren."
        preview = ""
    else:
        warning = f"Filtypen {ext or 'ukjent'} er kun indeksert som metadata i denne versjonen."

    return {"method": method, "preview": preview, "warning": warning, "nested_records": nested_records}


def normalize_uploaded_files(files: Sequence) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for uploaded_file in files or []:
        try:
            name = getattr(uploaded_file, "name", "unnamed")
            data = _safe_bytes(uploaded_file)
        except Exception:
            continue
        ext = Path(name).suffix.lower()
        extracted = _extract_text_preview(name, data)
        record = {
            "filename": name,
            "ext": ext or "",
            "size_kb": round(len(data) / 1024, 1),
            "sha12": sha12(data),
            "revision": infer_revision_tag(name),
            "canonical_stem": canonical_stem(name),
            "preview_method": extracted["method"],
            "preview_warning": extracted["warning"],
            "text_preview": extracted["preview"],
            "text_chars": len(extracted["preview"] or ""),
            "too_large": len(data) > MAX_FILE_KB * 1024,
            "parent_archive": "",
        }
        records.append(record)
        if extracted.get("nested_records"):
            records.extend(extracted["nested_records"])
    return records


def manifest_dataframe(records: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=["Fil", "Type", "Revisjon", "KB", "Preview", "Hash"])
    return pd.DataFrame([
        {
            "Fil": r["filename"],
            "Type": r["ext"] or "-",
            "Revisjon": r["revision"],
            "KB": r["size_kb"],
            "Preview": r["preview_method"],
            "Hash": r["sha12"],
        }
        for r in records
    ])


def revision_dataframe(records: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=["Dokumentfamilie", "Revisjoner", "Ulike hashes", "Kommentar"])
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["canonical_stem"], []).append(record)
    rows: List[Dict[str, Any]] = []
    for stem, items in grouped.items():
        revisions = ", ".join(item["revision"] for item in items)
        unique_hashes = len({item["sha12"] for item in items})
        rows.append({
            "Dokumentfamilie": stem or "ukjent",
            "Revisjoner": revisions,
            "Ulike hashes": unique_hashes,
            "Kommentar": "Endringer funnet" if unique_hashes > 1 and len(items) > 1 else "Kun en kjent versjon",
        })
    rows.sort(key=lambda row: row["Dokumentfamilie"])
    return pd.DataFrame(rows)


def estimate_context_chars(records: Sequence[Dict[str, Any]]) -> int:
    return sum(int(r.get("text_chars", 0) or 0) for r in records)


def documents_to_ai_context(records: Sequence[Dict[str, Any]], max_docs: int = 8, max_chars: int = 18000) -> str:
    if not records:
        return "Ingen dokumenter er lastet opp enna."
    chunks: List[str] = []
    running = 0
    for record in records[:max_docs]:
        preview = _trim_text(record.get("text_preview") or "", max_chars=2200)
        warning = record.get("preview_warning") or "-"
        chunk = (
            f"[Dokument] {record['filename']}\n"
            f"- Type: {record['ext'] or '-'}\n"
            f"- Revisjon: {record['revision']}\n"
            f"- Hash: {record['sha12']}\n"
            f"- Preview-metode: {record['preview_method']}\n"
            f"- Warning: {warning}\n"
            f"- Utdrag:\n{preview or '[Ingen tekstpreview tilgjengelig]'}"
        )
        if running + len(chunk) > max_chars and chunks:
            break
        chunks.append(chunk)
        running += len(chunk)
    return "\n\n".join(chunks)


def default_delivery_level(module_key: str) -> str:
    return MODULE_CONFIGS.get(module_key, {}).get("default_delivery_level", "reviewed")


def disclaimer_for_level(level: str) -> str:
    return DISCLAIMER_BY_LEVEL.get(level, DISCLAIMER_BY_LEVEL["reviewed"])


def _find_source_for_keyword(records: Sequence[Dict[str, Any]], keywords: Sequence[str]) -> str:
    for record in records:
        hay = f"{record.get('filename', '')}\n{record.get('text_preview', '')}".lower()
        for keyword in keywords:
            if keyword.lower() in hay:
                return record.get("filename", "")
    return ""


def _extract_tender_contract_fields(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    text = "\n".join(record.get("text_preview") or "" for record in records)
    filename_text = "\n".join(record.get("filename") or "" for record in records)
    fields: List[Tuple[str, str, str]] = []
    patterns = {
        "Prosjektnavn": [r"(?i)prosjektnavn[:\s]+([^\n]{3,80})", r"(?i)project[:\s]+([^\n]{3,80})"],
        "Byggherre": [r"(?i)byggherre[:\s]+([^\n]{3,80})", r"(?i)client[:\s]+([^\n]{3,80})"],
        "Tilbudsfrist": [r"(?i)tilbudsfrist[:\s]+([^\n]{3,50})", r"(?i)submission deadline[:\s]+([^\n]{3,50})"],
        "Kontraktsstandard": [r"(?i)(NS\s*8405|NS\s*8407|NS\s*3430|NF|Design & Build)"]
    }
    for label, patterns_list in patterns.items():
        value = ""
        for pattern in patterns_list:
            match = re.search(pattern, text)
            if match:
                value = match.group(1).strip()
                break
        if not value and label == "Kontraktsstandard":
            for candidate in ["NS 8405", "NS 8407", "NS 3420"]:
                if candidate.lower().replace(" ", "") in text.lower().replace(" ", "") or candidate.lower() in filename_text.lower():
                    value = candidate
                    break
        if value:
            fields.append({"field": label, "value": value, "source": "Dokumentgrunnlag"})
    return fields


def tender_rules_payload(project: Dict[str, Any], records: Sequence[Dict[str, Any]], user_inputs: Dict[str, Any], custom_rules: Sequence[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    classified = []
    for record in records:
        inferred = infer_category(record.get("filename", ""), record.get("text_preview", ""), TENDER_CATEGORY_HINTS)
        classified.append({"filename": record.get("filename", ""), **inferred})

    mandatory_categories = ["contract", "technical_description", "sha_plan", "price_sheet"]
    present_categories = {row["category"] for row in classified if row.get("confidence", 0) >= 0.35}
    missing_categories = [cat for cat in mandatory_categories if cat not in present_categories]

    combined_text = "\n\n".join(record.get("text_preview") or "" for record in records)
    rules = merge_tender_rules(custom_rules)
    checklist_items: List[Dict[str, Any]] = []
    for rule in rules:
        windows = extract_keyword_windows(combined_text, rule.get("keywords") or [], window=220)
        status = "OK" if windows else "MANGLER"
        source = _find_source_for_keyword(records, rule.get("keywords") or [])
        checklist_items.append({
            "topic": rule.get("topic", ""),
            "status": status,
            "severity": rule.get("severity_if_missing", "MEDIUM"),
            "paragraph_ref": windows[0][:120] if windows else "",
            "reason": windows[0] if windows else rule.get("note", ""),
            "source": source,
        })

    too_large = [record.get("filename") for record in records if record.get("too_large")]
    if too_large:
        checklist_items.append({
            "topic": "Filstorrelseskontroll",
            "status": "AVVIK",
            "severity": "HIGH",
            "paragraph_ref": "",
            "reason": f"Disse filene er over 100 MB og bor komprimeres eller splittes: {', '.join(too_large[:5])}",
            "source": ", ".join(too_large[:5]),
        })

    risk_items: List[Dict[str, Any]] = []
    for item in checklist_items:
        if item["status"] in {"MANGLER", "AVVIK"}:
            risk_items.append({
                "title": item["topic"],
                "severity": item["severity"],
                "impact": item["reason"],
                "recommendation": "Avklar forholdet og lag et eksplisitt forbehold eller RFI for innlevering.",
                "source": item.get("source", ""),
                "paragraph_ref": item.get("paragraph_ref", ""),
            })
    for category in missing_categories:
        risk_items.append({
            "title": f"Manglende dokumentkategori: {category}",
            "severity": "HIGH" if category in {"sha_plan", "contract"} else "MEDIUM",
            "impact": "Tilbudsgrunnlaget er ikke komplett, og analysen kan bli svak eller misvisende.",
            "recommendation": "Be om oppdatert dokumentpakke eller marker mangelen tydelig i avklaringslogg.",
            "source": "Dokumentmanifest",
            "paragraph_ref": "",
        })

    rfi_suggestions = []
    for idx, risk in enumerate(risk_items[:6], start=1):
        rfi_suggestions.append({
            "priority": idx,
            "question": f"Kan oppdragsgiver bekrefte og presisere forhold knyttet til {risk['title']}?",
            "why": risk["impact"],
            "owner": "Tilbudsleder",
        })

    completeness = max(0.0, 1.0 - (len(missing_categories) * 0.15) - (0.05 if too_large else 0.0))
    return {
        "project": {"project_name": project.get("p_name", ""), "client": project.get("c_name", "")},
        "classified_documents": classified,
        "missing_categories": missing_categories,
        "contract_fields": _extract_tender_contract_fields(records),
        "checklist_items": checklist_items,
        "risk_items": risk_items,
        "rfi_suggestions": rfi_suggestions,
        "data_completeness_score": round(max(0.0, min(1.0, completeness)), 2),
        "user_inputs": dict(user_inputs or {}),
    }


def _detect_condition_signal(text: str) -> int:
    low = text.lower()
    if any(token in low for token in ["lekkasje", "fukt", "sprekker", "korrosjon", "svikt", "mangel", "skade"]):
        return 3
    if any(token in low for token in ["slitasje", "eldre", "oppgradering", "avvik", "utbedring"]):
        return 2
    if any(token in low for token in ["rehabilitert", "utskiftet", "nytt", "oppgradert"]):
        return 1
    return 1


def _cost_range_for_tg(tg_num: int) -> str:
    mapping = {0: "0-50 000", 1: "50 000-250 000", 2: "250 000-1 000 000", 3: "1 000 000-3 000 000"}
    return mapping.get(tg_num, "100 000-500 000")


def _life_range_for_tg(tg_num: int) -> str:
    mapping = {0: "15-25 ar", 1: "10-15 ar", 2: "5-10 ar", 3: "0-5 ar"}
    return mapping.get(tg_num, "5-10 ar")


def tdd_rules_payload(project: Dict[str, Any], records: Sequence[Dict[str, Any]], user_inputs: Dict[str, Any]) -> Dict[str, Any]:
    classified = []
    for record in records:
        inferred = infer_category(record.get("filename", ""), record.get("text_preview", ""), TDD_CATEGORY_HINTS)
        classified.append({"filename": record.get("filename", ""), **inferred})

    present_categories = {row["category"] for row in classified if row.get("confidence", 0) >= 0.35}
    recommended = ["condition_report", "completion_certificate", "energy_certificate", "fdv", "drawings"]
    missing_categories = [cat for cat in recommended if cat not in present_categories]

    public_snapshot = gather_tdd_public_snapshot({
        "address": project.get("adresse"),
        "municipality": project.get("kommune"),
        "gnr": project.get("gnr"),
        "bnr": project.get("bnr"),
        "matrikkel_id": user_inputs.get("matrikkel_id", ""),
    })
    public_rows = public_snapshot.get("rows", [])
    public_hit_ratio = sum(1 for row in public_rows if row.get("status") in {"ok", "partial"}) / max(1, len(public_rows))
    completeness = max(0.0, min(1.0, 0.35 + 0.1 * len(present_categories) + 0.25 * public_hit_ratio - 0.05 * len(missing_categories)))

    text_by_category = {row["category"]: "" for row in classified}
    for row in classified:
        if row.get("category") and row["category"] != "unknown":
            source_record = next((r for r in records if r.get("filename") == row.get("filename")), None)
            if source_record:
                text_by_category[row["category"]] += "\n" + (source_record.get("text_preview") or "")

    parts = [
        ("Tak", text_by_category.get("condition_report", "")),
        ("Fasade", text_by_category.get("condition_report", "")),
        ("Vinduer / dorer", text_by_category.get("condition_report", "")),
        ("VVS", text_by_category.get("fdv", "")),
        ("Elektro", text_by_category.get("fdv", "")),
        ("Heis / transport", text_by_category.get("fdv", "")),
        ("Brann", text_by_category.get("drawings", "") + "\n" + text_by_category.get("completion_certificate", "")),
        ("Grunn / fundamenter", text_by_category.get("drawings", "") + "\n" + json.dumps(public_snapshot.get("resolved", {}), ensure_ascii=False)),
    ]
    building_parts = []
    remediation_total = 0
    tg_values = []
    for part_name, evidence in parts:
        tg_num = _detect_condition_signal(evidence)
        if completeness < 0.45 and part_name in {"Heis / transport", "Brann", "Grunn / fundamenter"}:
            tg = "Unknown"
            reason = "Underlaget er for svakt til sikker gradering."
            life = "Uavklart"
            cost = "Uavklart"
        else:
            tg = f"TG{tg_num}"
            reason = "Heuristisk vurdering basert pa dokumentgrunnlag og offentlige data."
            life = _life_range_for_tg(tg_num)
            cost = _cost_range_for_tg(tg_num)
            remediation_total += int(cost.split("-")[0].strip().replace(" ", "")) if cost != "Uavklart" else 0
            tg_values.append(tg_num)
        building_parts.append({
            "part": part_name,
            "tg": tg,
            "remaining_life_years": life,
            "remediation_cost_range_nok": cost,
            "reason": reason,
            "source": "Dokumentgrunnlag + snapshot",
        })

    tek17_deviations = []
    build_year = int(user_inputs.get("build_year") or 2008)
    if build_year < 2010:
        tek17_deviations.append({"title": "Byggear tilsier kontroll mot nyere energikrav og tilgjengelighet", "category": "VESENTLIG", "recommendation": "Verifiser gap mot gjeldende TEK17 ved transaksjon eller ombygging.", "source": "Byggear"})
    if public_snapshot["resolved"].get("energy", {}).get("status") != "ok":
        tek17_deviations.append({"title": "Energidata mangler eller er ikke verifisert", "category": "ANBEFALT", "recommendation": "Koble energiproxy eller innhent gyldig energimerke / EPC.", "source": "Energi / EPC"})
    if public_snapshot["resolved"].get("plan", {}).get("status") not in {"ok", "partial"}:
        tek17_deviations.append({"title": "Planstatus og hensynssoner ikke avklart", "category": "VESENTLIG", "recommendation": "Koble planoppslag mot Fellestjenester plan og bygg og verifiser planforhold.", "source": "Planoppslag"})

    technical_risk_num = sum(tg_values) / max(1, len(tg_values)) if tg_values else 2.0
    market_value_nok = float(user_inputs.get("market_value_mnok") or 145.0) * 1_000_000
    financial_ratio = remediation_total / max(1.0, market_value_nok)
    financial_risk_num = 1.2 + min(2.8, financial_ratio * 12.0)
    regulatory_risk_num = 1.0 + len([d for d in tek17_deviations if d["category"] in {"KRITISK", "VESENTLIG"}]) * 0.9
    overall_num = technical_risk_num * 0.45 + financial_risk_num * 0.35 + regulatory_risk_num * 0.20
    overall_class = "HOY" if overall_num >= 3.2 else "MIDDELS" if overall_num >= 2.0 else "LAV"

    portfolio_preview = None
    if user_inputs.get("include_portfolio"):
        portfolio_preview = run_tdd_portfolio_batch([
            {"matrikkel_id": project.get("gnr") or "", "label": project.get("p_name") or "Eiendom", "address": project.get("adresse") or "", "municipality": project.get("kommune") or ""}
        ], partner_id="demo-partner")

    return {
        "classified_documents": classified,
        "missing_categories": missing_categories,
        "data_completeness_score": round(completeness, 2),
        "public_data_snapshot": public_rows,
        "building_parts": building_parts,
        "tek17_deviations": tek17_deviations,
        "risk_matrix": {
            "technical_risk": f"{technical_risk_num:.2f}",
            "financial_risk": f"{financial_risk_num:.2f}",
            "regulatory_risk": f"{regulatory_risk_num:.2f}",
            "overall_class": overall_class,
            "remediation_cost_total_nok": remediation_total,
        },
        "portfolio_preview": portfolio_preview,
        "public_snapshot_resolved": public_snapshot.get("resolved", {}),
        "user_inputs": dict(user_inputs or {}),
    }


def _base_result(module_key: str, delivery_level: str) -> Dict[str, Any]:
    data = module_schema(module_key)
    data["delivery_level"] = delivery_level
    data["recommended_status"] = "Need review"
    data["confidence"] = "Middels"
    data["executive_summary"] = "AI-utkast ikke generert enda. Bruk rules-first payload som grunnlag."
    return data


def _heuristic_result(module_key: str, rules_payload: Dict[str, Any], delivery_level: str) -> Dict[str, Any]:
    data = _base_result(module_key, delivery_level)
    if module_key == "tender":
        risks = rules_payload.get("risk_items", [])
        checklist = rules_payload.get("checklist_items", [])
        data.update({
            "executive_summary": "Builtly har klassifisert dokumentpakken, sjekket obligatoriske tema og laget en forelopig risikoprofil for tilbudsarbeidet.",
            "recommended_status": "Proceed with reservations" if risks else "Proceed",
            "confidence": "Middels",
            "gaps": rules_payload.get("missing_categories", []),
            "questions": rules_payload.get("rfi_suggestions", []),
            "next_actions": [{"action": "Lukk mangelliste og send RFI", "owner": "Tilbudsleder", "priority": "High", "why": "Forbedrer submission readiness."}],
            "export_recommendations": ["CSV avviksmatrise", "JSON for review queue", "DOCX tilbudsgrunnlag"],
            "document_categories": rules_payload.get("classified_documents", []),
            "contract_fields": rules_payload.get("contract_fields", []),
            "checklist_items": checklist,
            "risk_items": risks,
            "rfi_suggestions": rules_payload.get("rfi_suggestions", []),
        })
    elif module_key == "tdd":
        risk = rules_payload.get("risk_matrix", {})
        data.update({
            "executive_summary": "Builtly har sammenstilt dokumentgrunnlag og offentlige data til et forelopig TDD-utkast med TG-indikasjoner og risikomatrise.",
            "recommended_status": "Proceed with reservations" if risk.get("overall_class") in {"MIDDELS", "HOY"} else "Proceed",
            "confidence": "Middels" if rules_payload.get("data_completeness_score", 0) >= 0.55 else "Lav",
            "gaps": rules_payload.get("missing_categories", []),
            "questions": [{"priority": 1, "question": "Hvilke manglende kilder ma inn for transaksjonsklar TDD?", "owner": "Transaksjonsteam"}],
            "next_actions": [{"action": "Verifiser offentlige data og bygningsdeler med hoy usikkerhet", "owner": "Fagperson", "priority": "High", "why": "Reduserer usikkerhet og forbedrer risikoestimat."}],
            "export_recommendations": ["JSON risikomatrise", "CSV bygningsdeler", "Markdown TDD-utkast"],
            "data_completeness_score": rules_payload.get("data_completeness_score", 0.0),
            "public_data_snapshot": rules_payload.get("public_data_snapshot", []),
            "building_parts": rules_payload.get("building_parts", []),
            "tek17_deviations": rules_payload.get("tek17_deviations", []),
            "risk_matrix": risk,
        })
    return data


def run_module_analysis(module_key: str, project: Dict[str, Any], records: Sequence[Dict[str, Any]], rules_payload: Dict[str, Any], delivery_level: str) -> Dict[str, Any]:
    module_conf = MODULE_CONFIGS.get(module_key, {})
    system_prompt = module_system_prompt(module_key, delivery_level)
    schema_hint = module_schema(module_key)
    context_payload = {
        "project": {
            "project_name": project.get("p_name"),
            "client": project.get("c_name"),
            "address": project.get("adresse"),
            "municipality": project.get("kommune"),
            "bta": project.get("bta"),
            "storeys": project.get("etasjer"),
            "building_type": project.get("b_type"),
        },
        "delivery_level": delivery_level,
        "rules_payload": rules_payload,
        "documents": documents_to_ai_context(records),
    }
    result = generate_json_with_fallback(
        system_prompt=system_prompt,
        user_prompt=format_prompt_context(context_payload),
        schema_hint=schema_hint,
        task=module_conf.get("task", "structured_review"),
        preferred_providers=module_conf.get("preferred_providers"),
        estimated_context_chars=estimate_context_chars(records) + len(json.dumps(rules_payload, ensure_ascii=False)),
        max_output_tokens=2200 if module_key == "tdd" else 1800,
        temperature=0.15,
    )
    if not result.get("ok") or "data" not in result:
        return {
            "ok": True,
            "provider": result.get("provider", "rules-first"),
            "model": result.get("model", "heuristic"),
            "attempt_log": result.get("attempt_log", []),
            "data": _heuristic_result(module_key, rules_payload, delivery_level),
            "error": result.get("error", ""),
            "fallback_used": True,
        }
    payload = result["data"]
    if isinstance(payload, dict):
        payload.setdefault("delivery_level", delivery_level)
        payload.setdefault("gaps", rules_payload.get("missing_categories", []))
    return result


def audit_log_dataframe(module_key: str, delivery_level: str, records: Sequence[Dict[str, Any]], ai_result: Dict[str, Any]) -> pd.DataFrame:
    rows = [{
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "module": module_key,
        "delivery_level": delivery_level,
        "document_count": len(records),
        "provider": ai_result.get("provider", "rules-first"),
        "model": ai_result.get("model", "heuristic"),
        "status": "ok" if ai_result.get("ok", True) else "error",
        "fallback_used": bool(ai_result.get("fallback_used")),
        "error": ai_result.get("error", ""),
    }]
    for attempt in ai_result.get("attempt_log", []) or []:
        rows.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "module": module_key,
            "delivery_level": delivery_level,
            "document_count": len(records),
            "provider": attempt.get("label") or attempt.get("provider"),
            "model": attempt.get("model", ""),
            "status": attempt.get("status", ""),
            "fallback_used": True,
            "error": attempt.get("error", ""),
        })
    return pd.DataFrame(rows)


def build_markdown_report(*, module_title: str, project: Dict[str, Any], manifest_records: Sequence[Dict[str, Any]], revision_records: Sequence[Dict[str, Any]], ai_payload: Dict | None) -> str:
    project_name = project.get("p_name") or "Nytt prosjekt"
    client_name = project.get("c_name") or "Ikke angitt"
    address = ", ".join(filter(None, [project.get("adresse", ""), project.get("kommune", "")])) or "Ikke angitt"
    parts: List[str] = [
        f"# {module_title}",
        "",
        "## Prosjektkontekst",
        f"- Prosjekt: {project_name}",
        f"- Klient: {client_name}",
        f"- Adresse: {address}",
        f"- BTA: {project.get('bta', '-')}",
        f"- Etasjer: {project.get('etasjer', '-')}",
        "",
        "## Dokumentmanifest",
    ]
    if manifest_records:
        for record in manifest_records:
            parts.append(f"- {record['filename']} ({record['revision']}, {record['ext'] or '-'}, {record['sha12']})")
    else:
        parts.append("- Ingen dokumenter lastet opp")
    parts.extend(["", "## Revisjoner"])
    if revision_records:
        for record in revision_records:
            parts.append(f"- {record['Dokumentfamilie']}: {record['Revisjoner']} ({record['Kommentar']})")
    else:
        parts.append("- Ingen revisjonskjeder tilgjengelig")
    if ai_payload:
        data = ai_payload.get("data") or {}
        parts.extend(["", "## AI-utkast", ""])
        parts.append(data.get("executive_summary") or "Ingen oppsummering generert")
        parts.append("")
        parts.append(f"- Anbefalt status: {data.get('recommended_status', '-')}")
        parts.append(f"- Faglig trygghet: {data.get('confidence', '-')}")
        if ai_payload.get("provider"):
            parts.append(f"- Generert med: {ai_payload.get('provider')} / {ai_payload.get('model', '-')}")
        for section_key, label in [
            ("key_findings", "Nokkelfunn"),
            ("gaps", "Mangler"),
            ("questions", "Sporsmal"),
            ("next_actions", "Neste steg"),
            ("export_recommendations", "Eksportpakke"),
        ]:
            items = data.get(section_key) or []
            parts.extend(["", f"### {label}"])
            if items:
                for item in items:
                    parts.append(f"- {json.dumps(item, ensure_ascii=False)}")
            else:
                parts.append("- Ingen punkter")
    return "\n".join(parts).strip() + "\n"
