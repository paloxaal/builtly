# -*- coding: utf-8 -*-
"""
Builtly | Tender Portal Fetch
─────────────────────────────────────────────────────────────────
Inntak av metadata fra Doffin via det offentlige JSON-API-et
(betaapi.doffin.no/public/v2).

Siden Doffin selv kun inneholder metadata (ikke selve
konkurransegrunnlaget), returnerer vi:
  - strukturerte metadata (tittel, frist, oppdragsgiver, CPV, beskrivelse)
  - lenke til oppdragsgivers KGV (Mercell, Visma TendSign, etc.)
    der selve konkurransegrunnlaget ligger

Mercell-integrasjon via API krever egen avtale med Mercell — se
den separate API-forespørselen. Inntil den er på plass, må brukeren
laste ned fra Mercell manuelt og dra ZIP-en inn i Builtly.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    requests = None  # type: ignore
    HAS_REQUESTS = False


USER_AGENT = "BuiltlyTenderControl/1.0 (+https://builtly.ai)"
REQUEST_TIMEOUT = 30

DOFFIN_API_BASES = [
    "https://betaapi.doffin.no/public/v2",
    "https://www.doffin.no/api/public/v2",
]


# ─── URL / ID parsing ────────────────────────────────────────────
def _is_doffin_url(url: str) -> bool:
    return "doffin.no" in (url or "").lower()


def _is_mercell_url(url: str) -> bool:
    return "mercell.com" in (url or "").lower()


def _extract_doffin_id(url: str) -> Optional[str]:
    """
    Hent Doffin-ID (f.eks. '2025-115155') fra en URL som
    'https://doffin.no/notices/2025-115155' eller
    'https://www.doffin.no/nb/notice/2025-115155/...'
    """
    m = re.search(r"(\d{4}-\d{5,})", url or "")
    return m.group(1) if m else None


def detect_portal(url: str) -> str:
    if _is_doffin_url(url):
        return "doffin"
    if _is_mercell_url(url):
        return "mercell"
    return "unknown"


# ─── Doffin JSON API ─────────────────────────────────────────────
def _fetch_doffin_notice_json(doffin_id: str) -> Dict[str, Any]:
    """
    Hent den offentlige JSON-representasjonen av en Doffin-kunngjøring.
    """
    if not HAS_REQUESTS:
        return {"ok": False, "error": "requests ikke installert"}

    last_error: Optional[str] = None
    for base in DOFFIN_API_BASES:
        url = f"{base}/notices/{doffin_id}"
        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                try:
                    return {"ok": True, "data": resp.json(), "source_url": url}
                except ValueError:
                    last_error = f"Ugyldig JSON fra {url}"
                    continue
            else:
                last_error = f"{url} ga HTTP {resp.status_code}"
        except Exception as e:
            last_error = f"{url}: {e}"

    return {"ok": False, "error": last_error or "Ukjent feil"}


def _pick_nested(obj: Any, *paths: str, default=None) -> Any:
    """Hjelpefunksjon: prøv flere dot-paths inn i nested dict."""
    for path in paths:
        cur: Any = obj
        for key in path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            elif isinstance(cur, list) and key.isdigit() and int(key) < len(cur):
                cur = cur[int(key)]
            else:
                cur = None
                break
        if cur is not None:
            return cur
    return default


def _norwegian_text(node: Any) -> Optional[str]:
    """
    eForms-data kommer ofte som {"nor": "tekst", "eng": "text"}
    eller som en liste av {lang, value}. Hent norsk primært.
    """
    if node is None:
        return None
    if isinstance(node, str):
        return node.strip() or None
    if isinstance(node, dict):
        for lang in ("nor", "no", "nb", "nno", "eng", "en"):
            val = node.get(lang)
            if val:
                return str(val).strip()
        # Fallback: any string-valued entry
        for v in node.values():
            if isinstance(v, str) and v.strip():
                return v.strip()
    if isinstance(node, list) and node:
        for entry in node:
            if isinstance(entry, dict):
                for lang_key in ("language", "lang", "locale"):
                    if entry.get(lang_key) in ("nor", "no", "nb", "NOR"):
                        return str(entry.get("value") or entry.get("text") or "").strip() or None
        first = node[0]
        if isinstance(first, dict):
            for k in ("value", "text", "label"):
                if first.get(k):
                    return str(first[k]).strip()
        elif isinstance(first, str):
            return first.strip() or None
    return None


def _extract_structured_metadata(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliser Doffin JSON til en flat dict med feltene Builtly bryr seg om.
    Doffin-API-et har variasjoner i struktur mellom eForms-skjemaer,
    så vi prøver flere stier per felt.
    """
    meta: Dict[str, Any] = {}

    title = _norwegian_text(_pick_nested(
        raw,
        "title",
        "heading",
        "notice.title",
        "contract.title",
        "procurementProject.title",
    ))
    if title:
        meta["title"] = title

    buyer = _norwegian_text(_pick_nested(
        raw,
        "buyer.name",
        "buyer.officialName",
        "contractingAuthority.name",
        "organisations.0.name",
        "organization.name",
    ))
    if buyer:
        meta["buyer"] = buyer

    buyer_org = _pick_nested(
        raw,
        "buyer.organizationId",
        "buyer.identifier",
        "contractingAuthority.organizationNumber",
        "organisations.0.identifier",
    )
    if buyer_org:
        meta["buyer_org_no"] = str(buyer_org)

    deadline = _pick_nested(
        raw,
        "submissionDeadline",
        "tenderSubmissionDeadline",
        "deadlineForReceiptOfTenders",
        "dates.submissionDeadline",
        "notice.submissionDeadline",
    )
    if deadline:
        meta["deadline"] = str(deadline)

    pub_date = _pick_nested(raw, "publicationDate", "datePublished", "notice.publicationDate")
    if pub_date:
        meta["publication_date"] = str(pub_date)

    est_value = _pick_nested(
        raw,
        "estimatedValue.amount",
        "contract.estimatedValue.amount",
        "procurementProject.estimatedValue.amount",
    )
    if est_value:
        meta["estimated_value"] = est_value
        currency = _pick_nested(
            raw,
            "estimatedValue.currency",
            "contract.estimatedValue.currency",
            default="NOK",
        )
        meta["currency"] = currency

    cpv = _pick_nested(raw, "cpv", "cpvCodes", "mainCpv", "procurementProject.cpv")
    if cpv:
        if isinstance(cpv, list):
            meta["cpv_codes"] = [str(c) for c in cpv if c]
        else:
            meta["cpv_codes"] = [str(cpv)]

    activity = _norwegian_text(_pick_nested(
        raw,
        "buyer.mainActivity",
        "contractingAuthority.mainActivity",
        "mainActivity",
    ))
    if activity:
        meta["main_activity"] = activity

    notice_type = _norwegian_text(_pick_nested(
        raw,
        "noticeType",
        "formType",
        "notice.type",
    ))
    if notice_type:
        meta["notice_type"] = notice_type

    procedure = _norwegian_text(_pick_nested(
        raw,
        "procedureType",
        "procurementProject.procedureType",
    ))
    if procedure:
        meta["procedure_type"] = procedure

    ref = _pick_nested(raw, "noticeId", "reference", "id")
    if ref:
        meta["reference"] = str(ref)

    description = _norwegian_text(_pick_nested(
        raw,
        "description",
        "shortDescription",
        "notice.description",
        "procurementProject.description",
    ))
    if description:
        meta["description"] = description[:2000]

    # Lenke til konkurransegrunnlag (KGV) — dette er det viktigste feltet
    kgv_url = _pick_nested(
        raw,
        "documentsUrl",
        "procurementDocumentsUrl",
        "electronicAccessUrl",
        "accessToProcurementDocumentsUrl",
        "contract.procurementDocumentsUrl",
    )
    if kgv_url:
        meta["kgv_url"] = kgv_url
        meta["kgv_provider"] = _detect_kgv_provider(kgv_url)

    location = _norwegian_text(_pick_nested(
        raw,
        "performanceLocation",
        "placeOfPerformance",
        "contract.placeOfPerformance",
    ))
    if location:
        meta["location"] = location

    return meta


def _detect_kgv_provider(url: str) -> str:
    """Gjett hvilket KGV som hoster konkurransegrunnlaget."""
    low = (url or "").lower()
    if "mercell" in low:
        return "Mercell"
    if "tendsign" in low or "visma" in low:
        return "Visma TendSign"
    if "eu-supply" in low:
        return "EU-Supply"
    if "eavrop" in low or "e-avrop" in low:
        return "e-Avrop"
    if "ajour" in low:
        return "Ajour System"
    if "offentligeinnkjop" in low:
        return "Offentlige innkjøp"
    return "Ekstern KGV"


# ─── Mercell ─────────────────────────────────────────────────────
def mercell_info() -> Dict[str, Any]:
    return {
        "portal": "mercell",
        "api_available": False,
        "message": (
            "Mercell krever egen API-avtale for programmatisk tilgang. "
            "Builtly har sendt forespørsel. Inntil avtale er på plass, "
            "åpne konkurransegrunnlaget i Mercell, last ned ZIP-en og "
            "dra filene inn i Builtly via filopplasteren."
        ),
    }


# ─── Top-level entry ─────────────────────────────────────────────
def fetch_from_url(url: str) -> Dict[str, Any]:
    """
    Inntakspunkt UI kaller.

    For Doffin: Henter metadata via JSON-API. Konkurransegrunnlag
    ligger alltid i ekstern KGV (Mercell/TendSign/etc), så vi
    returnerer KGV-lenken som tydelig neste steg.

    For Mercell: Viser melding om at API-integrasjon ikke er på plass.
    """
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "Tom URL."}

    portal = detect_portal(url)

    if portal == "doffin":
        doffin_id = _extract_doffin_id(url)
        if not doffin_id:
            return {
                "ok": False,
                "portal": "doffin",
                "error": "Kunne ikke lese Doffin-ID fra URL. Sjekk at lenken er på formen https://doffin.no/notices/YYYY-NNNNNN.",
            }

        api_response = _fetch_doffin_notice_json(doffin_id)
        if not api_response.get("ok"):
            return {
                "ok": False,
                "portal": "doffin",
                "doffin_id": doffin_id,
                "source_url": url,
                "error": f"Henting fra Doffin JSON-API feilet: {api_response.get('error')}",
            }

        raw = api_response.get("data") or {}
        meta = _extract_structured_metadata(raw)
        meta["doffin_id"] = doffin_id
        meta["doffin_url"] = f"https://www.doffin.no/notices/{doffin_id}"
        meta["api_source"] = api_response.get("source_url")

        return {
            "ok": True,
            "portal": "doffin",
            "metadata": meta,
            "files": [],  # Doffin selv har ingen vedlegg
            "kgv_url": meta.get("kgv_url"),
            "kgv_provider": meta.get("kgv_provider"),
            "next_step": (
                f"Konkurransegrunnlaget ligger i {meta.get('kgv_provider', 'ekstern KGV')}. "
                f"Åpne lenken, last ned alle vedlegg (vanligvis som ZIP) og dra filene inn under."
                if meta.get("kgv_url")
                else "Ingen KGV-lenke funnet i Doffin-metadataen. Dokumenter må lastes opp manuelt."
            ),
        }

    if portal == "mercell":
        return {
            "ok": False,
            "portal": "mercell",
            "error": mercell_info()["message"],
        }

    return {
        "ok": False,
        "portal": "unknown",
        "error": (
            "Ukjent portal. Støttet: Doffin (lim inn lenke til kunngjøringssiden). "
            "For Mercell/Visma TendSign: last ned konkurransegrunnlaget manuelt "
            "og dra filene inn i filopplasteren."
        ),
    }
