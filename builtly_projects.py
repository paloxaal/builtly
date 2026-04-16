"""
builtly_projects.py
--------------------
API for å lagre og hente navngitte prosjekt-oppsett i Supabase.

Hvert prosjekt har:
- en unik ID (UUID)
- et navn ("Saga", "Industriveien 1b")
- en slug ("saga", "industriveien-1b") — URL/filsystem-trygg
- en SSOT (JSON med all prosjektdata)
- tilknyttede filer (bilder, PDFer) i Storage bucket "project-files"

Filbane-konvensjon i Storage:
    <user_id>/<project_slug>/<type>/<filename>
    eks: 7b3a.../saga/images/flyfoto.jpg
         7b3a.../saga/files/støyrapport.pdf
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st


# ----------------------------------------------------------------------------
# UTILITIES
# ----------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Gjør prosjektnavn om til URL/filsystem-trygg slug.
    'Saga Park' -> 'saga-park', 'Industriveien 1b' -> 'industriveien-1b'"""
    if not name:
        return "untitled"
    # Normaliser og fjern accents
    s = unicodedata.normalize("NFKD", name)
    s = s.encode("ascii", "ignore").decode("ascii")
    # Lowercase
    s = s.lower().strip()
    # Erstatt ikke-alfanumeriske tegn med bindestrek
    s = re.sub(r"[^a-z0-9]+", "-", s)
    # Fjern bindestreker i start/slutt
    s = s.strip("-")
    return s or "untitled"


def _sb():
    """Hent Supabase anon-client. Importerer lokalt for å unngå sirkulær import."""
    try:
        from builtly_auth import _sb as _sb_func
        return _sb_func()
    except Exception:
        return None


def _sb_admin():
    """Hent Supabase service-key-client."""
    try:
        from builtly_auth import _sb_admin as _sb_admin_func
        return _sb_admin_func()
    except Exception:
        return None


def _uid() -> str:
    """Hent nåværende bruker-ID fra session_state."""
    return st.session_state.get("user_id", "") or ""


# ----------------------------------------------------------------------------
# CRUD — PROJECTS TABLE
# ----------------------------------------------------------------------------

def list_projects() -> List[Dict[str, Any]]:
    """Returnerer alle prosjekter for nåværende bruker, nyeste først.

    Cacher i session_state for ytelse. Kalles fra dashboardet og Project.py.
    """
    uid = _uid()
    if not uid:
        return []
    sb = _sb()
    if not sb:
        return []
    try:
        res = sb.table("projects")\
            .select("id, name, slug, created_at, updated_at")\
            .eq("user_id", uid)\
            .order("updated_at", desc=True)\
            .execute()
        return res.data or []
    except Exception:
        return []


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    """Hent et spesifikt prosjekt inkludert SSOT."""
    uid = _uid()
    if not uid or not project_id:
        return None
    sb = _sb()
    if not sb:
        return None
    try:
        res = sb.table("projects")\
            .select("*")\
            .eq("id", project_id)\
            .eq("user_id", uid)\
            .single()\
            .execute()
        return res.data
    except Exception:
        return None


def get_project_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    """Hent et prosjekt basert på slug (for lesbare URLer)."""
    uid = _uid()
    if not uid or not slug:
        return None
    sb = _sb()
    if not sb:
        return None
    try:
        res = sb.table("projects")\
            .select("*")\
            .eq("slug", slug)\
            .eq("user_id", uid)\
            .single()\
            .execute()
        return res.data
    except Exception:
        return None


def create_project(name: str, ssot: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, Optional[str]]:
    """Opprett et nytt prosjekt.

    Returnerer (success, message, project_id).

    Håndterer slug-kollisjoner ved å legge til -2, -3 osv.
    """
    uid = _uid()
    if not uid:
        return False, "Ikke innlogget", None

    name = (name or "").strip()
    if not name:
        return False, "Navn er påkrevd", None

    sb = _sb()
    if not sb:
        return False, "Supabase ikke konfigurert", None

    # Finn unik slug
    base_slug = _slugify(name)
    slug = base_slug
    suffix = 2
    while True:
        try:
            check = sb.table("projects")\
                .select("id")\
                .eq("user_id", uid)\
                .eq("slug", slug)\
                .execute()
            if not check.data:
                break
            slug = f"{base_slug}-{suffix}"
            suffix += 1
            if suffix > 50:
                return False, "Kunne ikke generere unik slug", None
        except Exception as e:
            return False, f"Feil ved slug-sjekk: {e}", None

    entry = {
        "user_id": uid,
        "name": name,
        "slug": slug,
        "ssot": ssot or {},
    }

    try:
        res = sb.table("projects").insert(entry).execute()
        if res.data and len(res.data) > 0:
            return True, "", res.data[0]["id"]
        return False, "Ingen data returnert", None
    except Exception as e:
        return False, f"Kunne ikke opprette prosjekt: {e}", None


def update_project_ssot(project_id: str, ssot: Dict[str, Any]) -> Tuple[bool, str]:
    """Oppdater SSOT-JSON for et prosjekt."""
    uid = _uid()
    if not uid or not project_id:
        return False, "Ikke innlogget eller manglende prosjekt-ID"

    sb = _sb()
    if not sb:
        return False, "Supabase ikke konfigurert"

    try:
        sb.table("projects")\
            .update({"ssot": ssot})\
            .eq("id", project_id)\
            .eq("user_id", uid)\
            .execute()
        return True, ""
    except Exception as e:
        return False, f"Kunne ikke oppdatere: {e}"


def rename_project(project_id: str, new_name: str) -> Tuple[bool, str]:
    """Endre navn på et prosjekt. Slug endres IKKE for å bevare fil-stier."""
    uid = _uid()
    if not uid or not project_id:
        return False, "Ikke innlogget"
    new_name = (new_name or "").strip()
    if not new_name:
        return False, "Navn er påkrevd"

    sb = _sb()
    if not sb:
        return False, "Supabase ikke konfigurert"

    try:
        sb.table("projects")\
            .update({"name": new_name})\
            .eq("id", project_id)\
            .eq("user_id", uid)\
            .execute()
        return True, ""
    except Exception as e:
        return False, f"Kunne ikke oppdatere navn: {e}"


def delete_project(project_id: str) -> Tuple[bool, str]:
    """Slett et prosjekt. Sletter også alle tilknyttede storage-filer."""
    uid = _uid()
    if not uid or not project_id:
        return False, "Ikke innlogget"

    sb = _sb()
    if not sb:
        return False, "Supabase ikke konfigurert"

    # Hent slug først slik at vi kan slette storage-filer
    proj = get_project(project_id)
    if not proj:
        return False, "Fant ikke prosjektet"

    slug = proj.get("slug", "")

    try:
        # Slett alle storage-filer
        if slug:
            prefix = f"{uid}/{slug}/"
            try:
                files = sb.storage.from_("project-files").list(prefix)
                if files:
                    paths = [f"{prefix}{f['name']}" for f in files if f.get('name')]
                    if paths:
                        sb.storage.from_("project-files").remove(paths)
            except Exception:
                pass  # Fortsett selv om storage-sletting feiler
        # Slett prosjekt-rad (CASCADE sletter eventuelle reports.project_id)
        sb.table("projects")\
            .delete()\
            .eq("id", project_id)\
            .eq("user_id", uid)\
            .execute()
        return True, ""
    except Exception as e:
        return False, f"Kunne ikke slette: {e}"


# ----------------------------------------------------------------------------
# STORAGE — PROJECT FILES
# ----------------------------------------------------------------------------

def _storage_prefix(project_slug: str, subfolder: str = "") -> str:
    """Bygg storage-prefix: <uid>/<slug>/[<subfolder>/]"""
    uid = _uid()
    base = f"{uid}/{project_slug}"
    if subfolder:
        return f"{base}/{subfolder}/"
    return f"{base}/"


def upload_project_file(project_slug: str, filename: str, data: bytes,
                        subfolder: str = "files",
                        content_type: Optional[str] = None) -> Tuple[bool, str]:
    """Last opp en fil til prosjektets storage-mappe.

    subfolder: vanligvis "images" eller "files"
    Returnerer (success, path_or_error).
    """
    if not _uid():
        return False, "Ikke innlogget"

    sb = _sb()
    if not sb:
        return False, "Supabase ikke konfigurert"

    path = f"{_storage_prefix(project_slug, subfolder)}{filename}"
    try:
        options = {"upsert": "true"}
        if content_type:
            options["content-type"] = content_type
        sb.storage.from_("project-files").upload(
            path=path, file=data, file_options=options
        )
        return True, path
    except Exception as e:
        return False, f"Upload feilet: {e}"


def download_project_file(storage_path: str) -> Optional[bytes]:
    """Last ned en fil fra storage gitt full path."""
    sb = _sb()
    if not sb:
        return None
    try:
        return sb.storage.from_("project-files").download(storage_path)
    except Exception:
        return None


def list_project_files(project_slug: str, subfolder: str = "") -> List[Dict[str, Any]]:
    """List alle filer i en prosjekt-mappe (eller underkatalog)."""
    if not _uid():
        return []
    sb = _sb()
    if not sb:
        return []
    prefix = _storage_prefix(project_slug, subfolder).rstrip("/")
    try:
        return sb.storage.from_("project-files").list(prefix) or []
    except Exception:
        return []


def delete_project_file(storage_path: str) -> bool:
    sb = _sb()
    if not sb:
        return False
    try:
        sb.storage.from_("project-files").remove([storage_path])
        return True
    except Exception:
        return False


def get_signed_url(storage_path: str, expires_in: int = 3600) -> Optional[str]:
    """Lag en signert URL for en storage-fil (gyldig i expires_in sekunder)."""
    sb = _sb()
    if not sb:
        return None
    try:
        res = sb.storage.from_("project-files").create_signed_url(
            storage_path, expires_in
        )
        if isinstance(res, dict):
            return res.get("signedURL") or res.get("signed_url")
        return None
    except Exception:
        return None


# ----------------------------------------------------------------------------
# HELPERS FOR UI
# ----------------------------------------------------------------------------

def get_active_project_id() -> str:
    """Hent ID til prosjektet som er åpent i nåværende session."""
    return st.session_state.get("active_project_id", "") or ""


def set_active_project(project_id: str) -> bool:
    """Sett aktivt prosjekt og last SSOT inn i session_state.project_data.

    Dette er hovedmekanismen for å "åpne" et prosjekt fra dashboardet.
    """
    proj = get_project(project_id)
    if not proj:
        return False
    st.session_state["active_project_id"] = proj["id"]
    st.session_state["active_project_name"] = proj["name"]
    st.session_state["active_project_slug"] = proj["slug"]
    # Last SSOT inn i project_data
    ssot = proj.get("ssot") or {}
    if ssot:
        st.session_state["project_data"] = ssot
    # Tving Project.py til å re-laste filer fra riktig mappe
    st.session_state["_project_loaded_for_uid"] = ""
    return True


def clear_active_project():
    """Lukk aktivt prosjekt (f.eks. for å starte et nytt)."""
    for key in ("active_project_id", "active_project_name", "active_project_slug"):
        st.session_state.pop(key, None)
    st.session_state["project_data"] = {}
    st.session_state["_project_loaded_for_uid"] = ""
