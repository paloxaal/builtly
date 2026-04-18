# -*- coding: utf-8 -*-
"""
Builtly Anbudskontroll — Prosjekt-CRUD
──────────────────────────────────────────────────────────────────────
Lagring og gjenoppretting av anbudsprosjekter per bruker.

Speiler mønsteret fra builtly_projects.py, men for anbudsspesifikk
datamodell. Primær lagring: Supabase tender_projects-tabell.
Fallback: lokal JSONL for standalone-kjøring.

API:
    list_tender_projects(user_email)
    get_tender_project(tender_id)
    create_tender_project(user_email, name, **fields)
    update_tender_project(tender_id, patch)
    delete_tender_project(tender_id)
    set_active_tender(tender_id)  — lagres i st.session_state
    get_active_tender_id()
    clear_active_tender()
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from supabase import create_client, Client as SupabaseClient
except ImportError:
    create_client = None
    SupabaseClient = None  # type: ignore

try:
    import streamlit as st
    HAS_STREAMLIT = True
except ImportError:
    st = None  # type: ignore
    HAS_STREAMLIT = False


_SB: Optional["SupabaseClient"] = None
_LOCAL_STORE = Path("/tmp/builtly_tender_projects.jsonl")

VALID_STATUSES = ("draft", "in_analysis", "ready", "submitted", "won", "lost", "archived")


def _sb():
    global _SB
    if _SB is not None:
        return _SB
    if not create_client:
        return None
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not (url and key):
        return None
    try:
        _SB = create_client(url, key)
        return _SB
    except Exception:
        return None


# ─── Auth helper ─────────────────────────────────────────────────
def _current_user_email() -> str:
    """
    Hent innlogget brukers epost. Speiler samme mønster som Project.py:
    - Primær: st.session_state['user_email'] (satt av auth-flow)
    - Sekundær: BUILTLY_USER env-var
    - Fallback: demo@builtly.ai (kun i dev-mode)
    """
    if HAS_STREAMLIT and st is not None:
        email = (
            st.session_state.get("user_email")
            or st.session_state.get("current_user_email")
            or ""
        )
        if email:
            return email
    return os.environ.get("BUILTLY_USER", "demo@builtly.ai")


# ─── Core CRUD ───────────────────────────────────────────────────
def list_tender_projects(
    user_email: Optional[str] = None,
    include_archived: bool = False,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Hent alle anbud for en bruker, nyeste først."""
    user_email = user_email or _current_user_email()

    sb = _sb()
    if sb:
        try:
            q = (
                sb.table("tender_projects")
                .select("tender_id, name, buyer_name, doffin_id, deadline, "
                        "contract_form, estimated_value_mnok, status, "
                        "created_at, updated_at, last_analysis_at, submitted_at")
                .eq("user_email", user_email)
                .order("updated_at", desc=True)
                .limit(limit)
            )
            if not include_archived:
                q = q.neq("status", "archived")
            resp = q.execute()
            return resp.data or []
        except Exception:
            pass

    # Lokal fallback
    return _local_list(user_email, include_archived, limit)


def get_tender_project(tender_id: str) -> Optional[Dict[str, Any]]:
    """Hent full record for ett anbud, inkludert all jsonb-data."""
    sb = _sb()
    if sb:
        try:
            resp = (
                sb.table("tender_projects")
                .select("*")
                .eq("tender_id", tender_id)
                .limit(1)
                .execute()
            )
            if resp.data:
                return _normalize_record(resp.data[0])
        except Exception:
            pass

    # Lokal fallback
    return _local_get(tender_id)


def create_tender_project(
    user_email: Optional[str] = None,
    name: str = "Nytt anbud",
    buyer_name: Optional[str] = None,
    doffin_id: Optional[str] = None,
    doffin_url: Optional[str] = None,
    deadline: Optional[str] = None,
    contract_form: Optional[str] = None,
    estimated_value_mnok: Optional[float] = None,
    intake: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[str], str]:
    """
    Opprett nytt anbudsprosjekt.
    Returnerer (ok, tender_id, error_message).
    """
    user_email = user_email or _current_user_email()

    payload: Dict[str, Any] = {
        "user_email": user_email,
        "name": name,
        "buyer_name": buyer_name,
        "doffin_id": doffin_id,
        "doffin_url": doffin_url,
        "deadline": deadline,
        "contract_form": contract_form,
        "estimated_value_mnok": estimated_value_mnok,
        "intake": intake or {},
        "status": "draft",
    }

    sb = _sb()
    if sb:
        try:
            resp = sb.table("tender_projects").insert(payload).execute()
            if resp.data:
                return True, resp.data[0]["tender_id"], ""
        except Exception as e:
            # Falle tilbake til lokal lagring
            pass

    # Lokal fallback
    return _local_create(payload)


def update_tender_project(
    tender_id: str,
    patch: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Oppdater deler av et anbud. Mtime oppdateres automatisk via trigger i Supabase.
    Returnerer (ok, error_message).
    """
    if not tender_id:
        return False, "tender_id mangler"

    # Beskytt mot utilsiktet overstyring av immutable-felt
    patch = {k: v for k, v in patch.items() if k not in ("tender_id", "user_email", "created_at")}
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()

    sb = _sb()
    if sb:
        try:
            sb.table("tender_projects").update(patch).eq("tender_id", tender_id).execute()
            return True, ""
        except Exception as e:
            return _local_update(tender_id, patch)

    return _local_update(tender_id, patch)


def delete_tender_project(tender_id: str) -> Tuple[bool, str]:
    """Soft-delete: settes til archived-status."""
    return update_tender_project(tender_id, {"status": "archived"})


def hard_delete_tender_project(tender_id: str) -> Tuple[bool, str]:
    """Faktisk sletting fra database."""
    sb = _sb()
    if sb:
        try:
            sb.table("tender_projects").delete().eq("tender_id", tender_id).execute()
            return True, ""
        except Exception as e:
            return False, str(e)
    return _local_delete(tender_id)


def rename_tender_project(tender_id: str, new_name: str) -> Tuple[bool, str]:
    return update_tender_project(tender_id, {"name": new_name.strip()})


# ─── Session state helpers (aktivt anbud) ───────────────────────
def set_active_tender(tender_id: str, name: str = "") -> None:
    """Marker et anbud som aktivt i session state."""
    if HAS_STREAMLIT and st is not None:
        st.session_state["active_tender_id"] = tender_id
        if name:
            st.session_state["active_tender_name"] = name


def get_active_tender_id() -> str:
    if HAS_STREAMLIT and st is not None:
        return st.session_state.get("active_tender_id", "") or ""
    return ""


def get_active_tender_name() -> str:
    if HAS_STREAMLIT and st is not None:
        return st.session_state.get("active_tender_name", "") or ""
    return ""


def clear_active_tender() -> None:
    if HAS_STREAMLIT and st is not None:
        st.session_state.pop("active_tender_id", None)
        st.session_state.pop("active_tender_name", None)


# ─── High-level: lagre hele state og laste det opp igjen ────────
def save_current_state(
    project: Dict[str, Any],
    documents: List[Dict[str, Any]],
    analysis: Dict[str, Any],
    readiness: Dict[str, Any],
    rule_findings: List[Dict[str, Any]],
    rfi_queue: List[Dict[str, Any]],
    tender_id: Optional[str] = None,
) -> Tuple[bool, Optional[str], str]:
    """
    Lagre gjeldende anbudsstate til Supabase. Hvis tender_id mangler,
    opprettes nytt prosjekt. Ellers oppdateres eksisterende.

    Merk: documents slankes til manifest (ingen rå tekst eller base64).
    DOCX-filer genereres on-demand og lagres IKKE her.

    Returnerer (ok, tender_id, error_message).
    """
    # Bygg slimmed-down dokumentmanifest — ikke store tekstblob-er
    manifest = []
    for d in (documents or []):
        manifest.append({
            "filename": d.get("filename"),
            "category": d.get("category"),
            "extension": d.get("extension"),
            "size_kb": d.get("size_kb"),
            "page_count": d.get("page_count", 0),
            "ocr_pages": d.get("ocr_pages", 0),
            "converted_from_dwg": d.get("converted_from_dwg", False),
            "zip_source": d.get("zip_source"),
            "zip_path": d.get("zip_path"),
            "source": d.get("source"),
            "error": d.get("error"),
            "has_tables": bool(d.get("tables")),
            "table_count": len(d.get("tables") or []),
            "sheet_count": len(d.get("sheets") or []),
            # Behold en kort tekst-excerpt for å kunne vise i UI uten ny opplasting
            "text_excerpt": (d.get("text_excerpt") or (d.get("text") or "")[:500]),
        })

    # Hent key metadata fra project-dict
    name = project.get("name") or f"Anbud {datetime.now().strftime('%d.%m.%Y')}"
    buyer_name = project.get("buyer_name") or ""
    deadline = project.get("deadline") or ""
    contract_form = project.get("contract_form") or ""
    est_value = project.get("estimated_value_mnok")
    try:
        est_value = float(est_value) if est_value is not None else None
    except (ValueError, TypeError):
        est_value = None

    payload = {
        "name": name,
        "buyer_name": buyer_name,
        "deadline": deadline,
        "contract_form": contract_form,
        "estimated_value_mnok": est_value,
        "intake": project,
        "documents_manifest": manifest,
        "analysis": analysis or {},
        "readiness": readiness or {},
        "rule_findings": rule_findings or [],
        "rfi_queue": rfi_queue or [],
        "last_analysis_at": datetime.now(timezone.utc).isoformat() if analysis else None,
        "status": "in_analysis" if analysis else "draft",
    }

    if tender_id:
        ok, err = update_tender_project(tender_id, payload)
        return ok, tender_id if ok else None, err
    else:
        # Opprett nytt
        ok, new_id, err = create_tender_project(
            name=name,
            buyer_name=buyer_name,
            deadline=deadline,
            contract_form=contract_form,
            estimated_value_mnok=est_value,
            intake=project,
        )
        if ok and new_id:
            # Fyll også alle de andre feltene etter opprettelse
            _, err2 = update_tender_project(new_id, payload)
            if err2:
                return True, new_id, f"Opprettet, men update feilet: {err2}"
            return True, new_id, ""
        return False, None, err


def load_into_session(tender_id: str) -> Tuple[bool, str]:
    """
    Last et lagret anbud inn i st.session_state.
    Returnerer (ok, error_message).
    """
    if not HAS_STREAMLIT:
        return False, "Streamlit ikke tilgjengelig"

    record = get_tender_project(tender_id)
    if not record:
        return False, f"Fant ikke anbud {tender_id}"

    # Kjente session state-nøkler i TenderControl
    st.session_state["tender_project"] = record.get("intake") or {}
    st.session_state["tender_documents"] = record.get("documents_manifest") or []
    st.session_state["tender_analysis"] = record.get("analysis") or {}
    st.session_state["tender_readiness"] = record.get("readiness") or {}
    st.session_state["tender_rule_findings"] = record.get("rule_findings") or []
    st.session_state["tender_rfi_queue"] = record.get("rfi_queue") or []

    set_active_tender(tender_id, name=record.get("name", ""))
    return True, ""


# ─── Lokal fallback (JSONL) ──────────────────────────────────────
def _local_list(user_email: str, include_archived: bool, limit: int) -> List[Dict[str, Any]]:
    if not _LOCAL_STORE.exists():
        return []
    records = []
    try:
        with _LOCAL_STORE.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("user_email") != user_email:
                        continue
                    if not include_archived and rec.get("status") == "archived":
                        continue
                    records.append(rec)
                except Exception:
                    continue
    except Exception:
        return []

    records.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return records[:limit]


def _local_get(tender_id: str) -> Optional[Dict[str, Any]]:
    if not _LOCAL_STORE.exists():
        return None
    try:
        with _LOCAL_STORE.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("tender_id") == tender_id:
                        return _normalize_record(rec)
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _local_create(payload: Dict[str, Any]) -> Tuple[bool, Optional[str], str]:
    import uuid as _uuid
    payload["tender_id"] = str(_uuid.uuid4())
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    payload["updated_at"] = payload["created_at"]

    try:
        _LOCAL_STORE.parent.mkdir(parents=True, exist_ok=True)
        with _LOCAL_STORE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True, payload["tender_id"], ""
    except Exception as e:
        return False, None, str(e)


def _local_update(tender_id: str, patch: Dict[str, Any]) -> Tuple[bool, str]:
    if not _LOCAL_STORE.exists():
        return False, "Lokal lagring eksisterer ikke"

    try:
        lines = _LOCAL_STORE.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found = False
        for line in lines:
            try:
                rec = json.loads(line)
                if rec.get("tender_id") == tender_id:
                    rec.update(patch)
                    new_lines.append(json.dumps(rec, ensure_ascii=False))
                    found = True
                else:
                    new_lines.append(line)
            except Exception:
                new_lines.append(line)

        if not found:
            return False, f"Fant ikke tender_id {tender_id}"

        _LOCAL_STORE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return True, ""
    except Exception as e:
        return False, str(e)


def _local_delete(tender_id: str) -> Tuple[bool, str]:
    if not _LOCAL_STORE.exists():
        return False, "Lokal lagring eksisterer ikke"

    try:
        lines = _LOCAL_STORE.read_text(encoding="utf-8").splitlines()
        new_lines = [
            l for l in lines
            if not (l and json.loads(l).get("tender_id") == tender_id)
        ]
        _LOCAL_STORE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return True, ""
    except Exception as e:
        return False, str(e)


def _normalize_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Sørg for at alle json-felt er dict/list, ikke str."""
    for field in ("intake", "analysis", "readiness"):
        v = rec.get(field)
        if isinstance(v, str):
            try:
                rec[field] = json.loads(v)
            except Exception:
                rec[field] = {}
        elif v is None:
            rec[field] = {}

    for field in ("documents_manifest", "rule_findings", "rfi_queue",
                  "pricing_dispatches", "quotes_received",
                  "generated_packages_meta", "generated_response_meta"):
        v = rec.get(field)
        if isinstance(v, str):
            try:
                rec[field] = json.loads(v)
            except Exception:
                rec[field] = []
        elif v is None:
            rec[field] = []

    return rec
