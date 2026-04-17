# -*- coding: utf-8 -*-
"""
Builtly | Tender Portal Fetch
─────────────────────────────────────────────────────────────────
Inntak av konkurransegrunnlag fra offentlige anbudsportaler.

Støtte:
  - Doffin (offentlige kunngjøringer) via HTML-skraping og
    direkte nedlasting av kunngjøringsvedlegg.
  - Mercell: kun manuell opplasting (eksport fra deres portal).
    Mercell har ikke åpent API uten egen avtale.

Returnerer liste av (filename, bytes)-par klar for extract_documents.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
    from bs4 import BeautifulSoup
    HAS_DEPS = True
except ImportError:
    requests = None  # type: ignore
    BeautifulSoup = None  # type: ignore
    HAS_DEPS = False


USER_AGENT = "BuiltlyTenderControl/1.0 (+https://builtly.ai)"
REQUEST_TIMEOUT = 30
MAX_ATTACHMENT_MB = 50
MAX_ATTACHMENTS = 40


def _is_doffin_url(url: str) -> bool:
    return "doffin.no" in (url or "").lower()


def _is_mercell_url(url: str) -> bool:
    return "mercell.com" in (url or "").lower()


def detect_portal(url: str) -> str:
    if _is_doffin_url(url):
        return "doffin"
    if _is_mercell_url(url):
        return "mercell"
    return "unknown"


# ─── Doffin-inntak ───────────────────────────────────────────────
def fetch_doffin_tender(url: str) -> Dict[str, Any]:
    """
    Last ned en Doffin-kunngjøring gitt dens URL.
    Returnerer metadata + liste over vedlegg.
    """
    if not HAS_DEPS:
        return {
            "ok": False,
            "error": "requests og beautifulsoup4 må være installert",
        }

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        return {"ok": False, "error": f"Kunne ikke hente {url}: {e}"}

    soup = BeautifulSoup(resp.text, "html.parser")

    # Metadata
    metadata: Dict[str, Any] = {"source_url": url}
    title_el = soup.find(["h1", "h2"])
    if title_el:
        metadata["title"] = title_el.get_text(strip=True)[:300]

    # Kunngjøringsnummer / referanse
    ref_match = re.search(r"(\d{4}-\d{6,})", resp.text)
    if ref_match:
        metadata["reference"] = ref_match.group(1)

    # Frist for tilbud
    deadline_match = re.search(
        r"(?:frist|deadline)[^\d]{0,30}(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}(?:[\sT]\d{2}[:.]?\d{2})?)",
        resp.text, flags=re.IGNORECASE,
    )
    if deadline_match:
        metadata["deadline_raw"] = deadline_match.group(1)

    # Oppdragsgiver
    buyer_match = re.search(
        r"(?:oppdragsgiver|innkj[øo]per|buyer)[^\n<]{0,5}[:<]([^<\n]{3,150})",
        resp.text, flags=re.IGNORECASE,
    )
    if buyer_match:
        metadata["buyer"] = buyer_match.group(1).strip()

    # Finn alle vedleggs-lenker
    attachments: List[Dict[str, Any]] = []
    doc_exts = (".pdf", ".docx", ".xlsx", ".xls", ".zip", ".ifc", ".dwg", ".dxf")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        absolute = urllib.parse.urljoin(url, href)
        lower = absolute.lower()
        if any(lower.endswith(ext) for ext in doc_exts) or "attachment" in lower or "vedlegg" in lower:
            link_text = a.get_text(strip=True) or ""
            if absolute not in [x["url"] for x in attachments]:
                attachments.append({
                    "url": absolute,
                    "link_text": link_text[:200],
                })

    return {
        "ok": True,
        "portal": "doffin",
        "metadata": metadata,
        "attachments": attachments[:MAX_ATTACHMENTS],
        "attachments_total_found": len(attachments),
    }


def download_attachments(
    attachments: List[Dict[str, Any]],
    max_mb: int = MAX_ATTACHMENT_MB,
) -> List[Tuple[str, bytes, Optional[str]]]:
    """
    Last ned vedleggsfiler.

    Returnerer liste av (filename, bytes, error_or_None).
    Filer over max_mb hoppes over med feilmelding.
    """
    if not HAS_DEPS:
        return []

    downloaded: List[Tuple[str, bytes, Optional[str]]] = []
    for att in attachments:
        url = att["url"]
        try:
            head_resp = requests.head(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            content_length = int(head_resp.headers.get("Content-Length", 0))
            if content_length and content_length > max_mb * 1024 * 1024:
                downloaded.append((
                    _extract_filename(url, att),
                    b"",
                    f"For stor ({content_length / 1024 / 1024:.1f} MB > {max_mb} MB)",
                ))
                continue
        except Exception:
            # Head feiler — prøv GET likevel
            pass

        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT * 2,
                stream=True,
            )
            resp.raise_for_status()
            data = resp.content
            if len(data) > max_mb * 1024 * 1024:
                downloaded.append((
                    _extract_filename(url, att),
                    b"",
                    f"For stor ({len(data) / 1024 / 1024:.1f} MB)",
                ))
                continue

            filename = _extract_filename(url, att, response=resp)
            downloaded.append((filename, data, None))
        except Exception as e:
            downloaded.append((_extract_filename(url, att), b"", f"Nedlasting feilet: {e}"))

    return downloaded


def _extract_filename(url: str, att: Dict[str, Any], response=None) -> str:
    """Finn beste tilgjengelige filnavn."""
    # 1. Content-Disposition
    if response is not None:
        cd = response.headers.get("Content-Disposition", "")
        m = re.search(r'filename\*?=["\']?(?:UTF-\d\'\')?([^"\';\r\n]+)', cd)
        if m:
            return urllib.parse.unquote(m.group(1))

    # 2. URL-sti
    path = urllib.parse.urlparse(url).path
    if path:
        name = path.rstrip("/").split("/")[-1]
        if name and "." in name:
            return urllib.parse.unquote(name)

    # 3. Fallback til link-tekst
    link_text = att.get("link_text", "").strip()
    if link_text:
        # Rens filnavn
        safe = re.sub(r"[^\w\-. ]+", "_", link_text)[:80]
        if "." not in safe:
            safe += ".pdf"  # anta PDF hvis ukjent
        return safe

    return "vedlegg.bin"


# ─── Mercell (kun info — ingen API) ──────────────────────────────
def mercell_info() -> Dict[str, Any]:
    return {
        "portal": "mercell",
        "api_available": False,
        "message": (
            "Mercell har ikke offentlig API for tredjepartsinntak. "
            "Last ned konkurransegrunnlaget manuelt fra Mercell-portalen "
            "og bruk ordinær filopplasting."
        ),
    }


# ─── Top-level entry ─────────────────────────────────────────────
def fetch_from_url(url: str) -> Dict[str, Any]:
    """
    Inntakspunkt som UI kaller.
    Returnerer {portal, ok, files: [(name, bytes)], metadata, messages}.
    """
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "Tom URL."}

    portal = detect_portal(url)

    if portal == "doffin":
        meta = fetch_doffin_tender(url)
        if not meta.get("ok"):
            return {"ok": False, "error": meta.get("error"), "portal": "doffin"}

        downloads = download_attachments(meta["attachments"])
        files: List[Tuple[str, bytes]] = []
        errors: List[str] = []
        for name, data, err in downloads:
            if err:
                errors.append(f"{name}: {err}")
            elif data:
                files.append((name, data))

        return {
            "ok": True,
            "portal": "doffin",
            "metadata": meta["metadata"],
            "files": files,
            "errors": errors,
            "attachments_total_found": meta.get("attachments_total_found", 0),
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
            "For Mercell: last ned konkurransegrunnlaget manuelt og bruk filopplasting."
        ),
    }
