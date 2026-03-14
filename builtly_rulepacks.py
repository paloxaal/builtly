from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

DEFAULT_TENDER_RULES: List[Dict] = [
    {
        "topic": "Dagmulkt",
        "standard": "NS 8405 / NS 8407",
        "keywords": ["dagmulkt", "daily penalty", "forsinkelsesmulkt"],
        "severity_if_missing": "HIGH",
        "note": "Kontroller om dagmulkt er tydelig regulert og om sats/grunnlag er forståelig.",
    },
    {
        "topic": "Garantiperiode",
        "standard": "NS 8405 / NS 8407",
        "keywords": ["garanti", "garantiperiode", "warranty", "garantitid"],
        "severity_if_missing": "HIGH",
        "note": "Garantiperiode og sikkerhetsstillelse bør være tydelig beskrevet.",
    },
    {
        "topic": "Betalingsplan",
        "standard": "NS 8405 / NS 8407",
        "keywords": ["betalingsplan", "payment plan", "fakturaplan", "avdragsplan"],
        "severity_if_missing": "MEDIUM",
        "note": "Manglende betalingsmekanikk gjør kalkyle og risikoallokering svakere.",
    },
    {
        "topic": "Reklamasjonsfrist",
        "standard": "NS 8405 / NS 8407",
        "keywords": ["reklamasjon", "reklamasjonsfrist", "defects liability", "mangel"],
        "severity_if_missing": "MEDIUM",
        "note": "Reklamasjonsfrist og mangelsoppfølging må finnes i kontraktsgrunnlaget.",
    },
    {
        "topic": "Force majeure",
        "standard": "NS 8405 / NS 8407",
        "keywords": ["force majeure", "hindring", "ekstraordinære forhold"],
        "severity_if_missing": "MEDIUM",
        "note": "Force majeure og varslingsregime bør være eksplisitt omtalt.",
    },
    {
        "topic": "SHA-plan",
        "standard": "Byggherreforskriften / SHA-krav",
        "keywords": ["sha-plan", "sha plan", "sikkerhet, helse og arbeidsmiljø", "byggherreforskriften"],
        "severity_if_missing": "HIGH",
        "note": "SHA-grunnlaget må være til stede og konsistent med tilbudsarbeidet.",
    },
    {
        "topic": "Tegningsliste / revisjonsstyring",
        "standard": "Dokumentkontroll",
        "keywords": ["tegningsliste", "drawing list", "revisjon", "revision"],
        "severity_if_missing": "MEDIUM",
        "note": "Tilbudsgrunnlaget bør ha en konsistent tegningsliste og revisjonsstatus.",
    },
]

TENDER_CATEGORY_HINTS: Dict[str, List[str]] = {
    "contract": ["kontrakt", "kontraktsbestemmelser", "general conditions", "ns 8405", "ns 8407", "aia a201"],
    "technical_description": ["teknisk beskrivelse", "beskrivelse", "specification", "ytelsesbeskrivelse", "ns 3420", "masterformat"],
    "sha_plan": ["sha", "safety plan", "construction phase plan", "hms-plan"],
    "drawing_list": ["tegningsliste", "drawing list", "drawing register", "revisjonsliste"],
    "price_sheet": ["prisskjema", "tilbudsskjema", "price sheet", "bill of quantities", "boq", "mengdebeskrivelse"],
    "regulation": ["reguleringsplan", "zoning", "planning", "regulation"],
    "tender_portal_export": ["mercell", "statsbygg", "ebyggesak", "konkurransegrunnlag eksport", "tender export"],
}

TDD_CATEGORY_HINTS: Dict[str, List[str]] = {
    "condition_report": ["tilstandsrapport", "condition report", "ns 3600", "ns 3424", "building survey", "pca"],
    "completion_certificate": ["ferdigattest", "midlertidig brukstillatelse", "completion certificate", "occupancy"],
    "energy_certificate": ["energimerke", "energy performance certificate", "epc", "energiattest"],
    "fdv": ["fdv", "operation and maintenance", "driftsinstruks", "maintenance manual"],
    "drawings": ["tegning", "drawing", "ifc", "plan", "snitt", "fasade"],
    "maintenance_history": ["vedlikehold", "maintenance", "rehabilitering", "utskifting"],
    "inspection_photo": ["foto", "photo", "inspection image", "tilstandsbilde"],
}


def _safe_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def infer_category(filename: str, text: str, category_hints: Dict[str, Sequence[str]]) -> Dict[str, object]:
    haystack = f"{filename}\n{text}".lower()
    best_category = "unknown"
    best_score = 0
    matched_keywords: List[str] = []

    for category, keywords in category_hints.items():
        hits = [keyword for keyword in keywords if keyword.lower() in haystack]
        score = len(hits)
        if score > best_score:
            best_category = category
            best_score = score
            matched_keywords = hits

    confidence = min(0.98, 0.35 + best_score * 0.15) if best_score else 0.0
    return {
        "category": best_category,
        "confidence": round(confidence, 2),
        "matched_keywords": matched_keywords,
    }



def load_rulepack_from_bytes(filename: str, data: bytes) -> List[Dict]:
    ext = Path(filename).suffix.lower()
    if not data:
        return []

    try:
        if ext == ".json":
            payload = json.loads(data.decode("utf-8", errors="ignore"))
            if isinstance(payload, dict):
                payload = payload.get("rules") or payload.get("items") or []
            if isinstance(payload, list):
                return [dict(item) for item in payload if isinstance(item, dict)]
        if ext in {".csv", ".txt"}:
            text = data.decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(text))
            return [dict(row) for row in reader]
        if ext in {".xlsx", ".xls"}:
            import pandas as pd
            df = pd.read_excel(io.BytesIO(data)).fillna("")
            return df.to_dict(orient="records")
    except Exception:
        return []
    return []



def normalise_rulepack(rows: Iterable[Dict]) -> List[Dict]:
    result: List[Dict] = []
    for row in rows:
        topic = row.get("topic") or row.get("Tema") or row.get("title") or row.get("Rule")
        if not topic:
            continue
        keywords = row.get("keywords") or row.get("Keywords") or row.get("nokkelsord") or row.get("Nøkkelord") or []
        if isinstance(keywords, str):
            keywords = [part.strip() for part in re.split(r"[;,|]", keywords) if part.strip()]
        elif not isinstance(keywords, list):
            keywords = []
        result.append(
            {
                "topic": str(topic).strip(),
                "standard": (row.get("standard") or row.get("Standard") or "Tilpasset bibliotek").strip(),
                "keywords": keywords,
                "severity_if_missing": (row.get("severity_if_missing") or row.get("Alvorlighet") or "MEDIUM").strip().upper(),
                "note": (row.get("note") or row.get("Kommentar") or "").strip(),
            }
        )
    return result



def merge_tender_rules(custom_rules: Sequence[Dict] | None = None) -> List[Dict]:
    merged = list(DEFAULT_TENDER_RULES)
    if custom_rules:
        merged.extend(normalise_rulepack(custom_rules))
    return merged



def extract_keyword_windows(text: str, keywords: Sequence[str], window: int = 180) -> List[str]:
    snippets: List[str] = []
    lower = text.lower()
    for keyword in keywords:
        key = keyword.lower().strip()
        if not key:
            continue
        idx = lower.find(key)
        if idx < 0:
            continue
        start = max(0, idx - window // 2)
        end = min(len(text), idx + len(keyword) + window // 2)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        if snippet and snippet not in snippets:
            snippets.append(snippet)
    return snippets[:3]
