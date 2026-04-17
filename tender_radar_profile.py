# -*- coding: utf-8 -*-
"""
Builtly | Tender Radar — Profile & Watches
─────────────────────────────────────────────────────────────────
CRUD for tender_profiles og tender_watches i Supabase.
En profil = en bruker/selskap. En profil kan ha flere watches
(f.eks. en for RIB-oppdrag, en for grunnarbeid).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


try:
    from supabase import create_client, Client as SupabaseClient
except ImportError:
    create_client = None
    SupabaseClient = None  # type: ignore


_SB: Optional["SupabaseClient"] = None


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


# ─── Profile CRUD ────────────────────────────────────────────────
def get_or_create_profile(
    user_email: str,
    company_name: Optional[str] = None,
    company_org_no: Optional[str] = None,
    display_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Hent eksisterende eller opprett ny profil."""
    sb = _sb()
    if not sb:
        return None

    try:
        resp = sb.table("tender_profiles").select("*").eq("user_email", user_email).limit(1).execute()
        if resp.data:
            return resp.data[0]
    except Exception:
        return None

    # Opprett ny
    payload = {
        "user_email": user_email,
        "company_name": company_name,
        "company_org_no": company_org_no,
        "display_name": display_name or user_email,
        "default_email": user_email,
    }
    try:
        resp = sb.table("tender_profiles").insert(payload).execute()
        return resp.data[0] if resp.data else None
    except Exception:
        return None


def update_profile(profile_id: str, updates: Dict[str, Any]) -> bool:
    sb = _sb()
    if not sb:
        return False
    try:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        sb.table("tender_profiles").update(updates).eq("profile_id", profile_id).execute()
        return True
    except Exception:
        return False


# ─── Watch CRUD ──────────────────────────────────────────────────
DEFAULT_WATCH: Dict[str, Any] = {
    "name": "Ny overvåking",
    "cpv_codes": [],
    "cpv_codes_exclude": [],
    "regions": [],
    "procurement_types": [],
    "keywords_positive": [],
    "keywords_negative": [],
    "discipline_focus": [],
    "sources": ["doffin"],
    "is_active": True,
}


def create_watch(profile_id: str, watch_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Opprett en ny watch for en profil."""
    sb = _sb()
    if not sb:
        return None

    payload = {**DEFAULT_WATCH, **watch_data, "profile_id": profile_id}
    try:
        resp = sb.table("tender_watches").insert(payload).execute()
        return resp.data[0] if resp.data else None
    except Exception:
        return None


def list_watches(profile_id: str, include_inactive: bool = False) -> List[Dict[str, Any]]:
    sb = _sb()
    if not sb:
        return []
    try:
        q = sb.table("tender_watches").select("*").eq("profile_id", profile_id)
        if not include_inactive:
            q = q.eq("is_active", True)
        resp = q.order("created_at", desc=True).execute()
        return resp.data or []
    except Exception:
        return []


def update_watch(watch_id: str, updates: Dict[str, Any]) -> bool:
    sb = _sb()
    if not sb:
        return False
    try:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        sb.table("tender_watches").update(updates).eq("watch_id", watch_id).execute()
        return True
    except Exception:
        return False


def delete_watch(watch_id: str) -> bool:
    """Soft-delete (sett is_active = false). Hard-delete skal gjøres manuelt."""
    return update_watch(watch_id, {"is_active": False})


def get_all_active_watches() -> List[Dict[str, Any]]:
    """Brukes av poller-worker: hent alle aktive watches fra alle brukere."""
    sb = _sb()
    if not sb:
        return []
    try:
        resp = (
            sb.table("tender_watches")
            .select("*, tender_profiles(profile_id, user_email, company_name, default_email, default_slack_webhook, default_fit_threshold, default_notification_mode, subscription_tier, is_active)")
            .eq("is_active", True)
            .execute()
        )
        # Filtrer bort watches hvis parent profile er deaktivert
        active = []
        for w in (resp.data or []):
            profile = w.get("tender_profiles") or {}
            if profile.get("is_active", True):
                active.append(w)
        return active
    except Exception:
        return []


# ─── CPV-hjelpere ────────────────────────────────────────────────
# Mest brukte CPV-kategorier for bygg/anlegg/rådgivning i Norge
CPV_CATEGORIES: Dict[str, str] = {
    "45000000": "Bygge- og anleggsarbeider",
    "45100000": "Klargjøring av byggeplass",
    "45200000": "Hele eller deler av bygge- og anleggsarbeid",
    "45210000": "Byggearbeider",
    "45220000": "Konstruksjonsarbeider",
    "45230000": "Bygging av rørledninger, kommunikasjons- og kraftledninger",
    "45300000": "Installasjonsarbeid",
    "45400000": "Bygningsferdigstillelse",
    "45500000": "Leie av utstyr med mannskap til bygg/anlegg",
    "71000000": "Arkitekt-, bygg-, ingeniør- og inspeksjonstjenester",
    "71200000": "Arkitektvirksomhet og tilknyttede tjenester",
    "71300000": "Ingeniørvirksomhet",
    "71400000": "Byplanleggings- og landskapsarkitekttjenester",
    "71500000": "Tekniske rådgivningstjenester",
    "71600000": "Teknisk prøving, analyse- og rådgivningstjenester",
    "79000000": "Forretningstjenester",
    "72000000": "IT-tjenester",
}


def cpv_label(code: str) -> str:
    """Slå opp norsk label for en CPV-kode. Faller tilbake til koden."""
    return CPV_CATEGORIES.get((code or "").strip(), code)


# ─── Region-hjelpere ─────────────────────────────────────────────
NORWEGIAN_REGIONS: List[str] = [
    "Oslo",
    "Viken",
    "Innlandet",
    "Vestfold og Telemark",
    "Agder",
    "Rogaland",
    "Vestland",
    "Møre og Romsdal",
    "Trøndelag",
    "Nordland",
    "Troms og Finnmark",
]


DISCIPLINES: List[str] = [
    "ARK", "RIB", "RIV", "RIE", "Brann", "Akustikk",
    "Geo", "Trafikk", "SHA", "MOP", "BREEAM",
]


PROCUREMENT_TYPES: List[str] = [
    "Bygg og anlegg",
    "Rådgiving",
    "Tjenester",
    "Varer",
    "Konsesjoner",
]
