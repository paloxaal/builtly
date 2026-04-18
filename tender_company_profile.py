# -*- coding: utf-8 -*-
"""
Builtly Anbudskontroll — Selskapsprofil
──────────────────────────────────────────────────────────────────────
Lagrer selskapsinformasjon som brukes av:
  - tender_packages.py (UE-tilbudsgrunnlag) — firmabrev-header
  - tender_response.py (tilbudsbesvarelse)  — kvalifikasjonssvar

Data lagres i Supabase-tabellen tender_company_profiles.
Faller tilbake til lokal JSONL hvis Supabase ikke er tilgjengelig.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from supabase import create_client, Client as SupabaseClient
except ImportError:
    create_client = None
    SupabaseClient = None  # type: ignore


_SB: Optional["SupabaseClient"] = None
_LOCAL_STORE = Path("/tmp/builtly_company_profiles.jsonl")


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


# ─── Standardfelt for en tom profil ──────────────────────────────
EMPTY_PROFILE: Dict[str, Any] = {
    "company_name": "",
    "company_org_no": "",
    "company_address": "",
    "company_postcode": "",
    "company_city": "",
    "contact_person": "",
    "contact_title": "",
    "contact_email": "",
    "contact_phone": "",
    "ceo_name": "",
    "approval_areas": [],           # ['Tiltaksklasse 3 Bygningsfysikk', ...]
    "reference_projects": [],       # [{name, value_mnok, year, description, role}, ...]
    "certifications": [],           # ['ISO 9001', 'StartBANK', 'Achilles', ...]
    "hms_policy": "",
    "quality_policy": "",
    "company_description": "",
    "logo_url": "",
}


# ─── Vanlige verdier for dropdown-hjelp ──────────────────────────
COMMON_CERTIFICATIONS: List[str] = [
    "ISO 9001 (kvalitet)",
    "ISO 14001 (miljø)",
    "ISO 45001 (HMS)",
    "StartBANK godkjent",
    "Achilles JQS",
    "Sellihca",
    "BREEAM NOR-akkreditert",
    "BRL 1999 sentralgodkjenning",
    "Maskinentreprenørenes Forbund",
    "Miljøfyrtårn",
    "Norsk Fjernvarmeforening",
]


COMMON_APPROVAL_AREAS: List[str] = [
    "Tiltaksklasse 1 – Bygninger",
    "Tiltaksklasse 2 – Bygninger",
    "Tiltaksklasse 3 – Bygninger",
    "Tiltaksklasse 1 – Anlegg",
    "Tiltaksklasse 2 – Anlegg",
    "Tiltaksklasse 3 – Anlegg",
    "Prosjekterende – Arkitektur",
    "Prosjekterende – Bygningsfysikk",
    "Prosjekterende – Konstruksjonssikkerhet",
    "Prosjekterende – Geoteknikk",
    "Prosjekterende – Brannsikkerhet",
    "Utførende – Tømrerarbeid",
    "Utførende – Murarbeid",
    "Utførende – Betongarbeid",
    "Utførende – Grunn- og terrengarbeid",
    "Utførende – Ventilasjonsarbeid",
    "Utførende – Rørleggerarbeid",
    "Utførende – Elektriske installasjoner",
    "Kontrollerende – Bygningsfysikk",
    "Kontrollerende – Konstruksjonssikkerhet",
    "Kontrollerende – Brannsikkerhet",
]


# ─── CRUD ────────────────────────────────────────────────────────
def get_profile(user_email: str) -> Dict[str, Any]:
    """Hent selskapsprofil for en bruker. Returnerer tom profil hvis ingen finnes."""
    sb = _sb()
    if sb:
        try:
            resp = (
                sb.table("tender_company_profiles")
                .select("*")
                .eq("user_email", user_email)
                .limit(1)
                .execute()
            )
            if resp.data:
                return _normalize_profile(resp.data[0])
        except Exception:
            pass

    # Lokal fallback
    if _LOCAL_STORE.exists():
        try:
            with _LOCAL_STORE.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        if rec.get("user_email") == user_email:
                            return _normalize_profile(rec)
                    except Exception:
                        continue
        except Exception:
            pass

    return {**EMPTY_PROFILE, "user_email": user_email}


def save_profile(user_email: str, profile_data: Dict[str, Any]) -> bool:
    """Lagre selskapsprofil. Upsert basert på user_email."""
    payload = {
        **EMPTY_PROFILE,
        **profile_data,
        "user_email": user_email,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    sb = _sb()
    if sb:
        try:
            existing = (
                sb.table("tender_company_profiles")
                .select("profile_id")
                .eq("user_email", user_email)
                .limit(1)
                .execute()
            )
            if existing.data:
                profile_id = existing.data[0]["profile_id"]
                sb.table("tender_company_profiles").update(payload).eq(
                    "profile_id", profile_id
                ).execute()
            else:
                sb.table("tender_company_profiles").insert(payload).execute()
            return True
        except Exception as e:
            print(f"Supabase-feil: {e}")

    # Lokal fallback
    try:
        _LOCAL_STORE.parent.mkdir(parents=True, exist_ok=True)
        # Les alle eksisterende, fjern gammel for denne brukeren
        existing_lines: List[str] = []
        if _LOCAL_STORE.exists():
            with _LOCAL_STORE.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        if rec.get("user_email") != user_email:
                            existing_lines.append(line.rstrip("\n"))
                    except Exception:
                        continue
        existing_lines.append(json.dumps(payload, ensure_ascii=False))
        with _LOCAL_STORE.open("w", encoding="utf-8") as f:
            f.write("\n".join(existing_lines) + "\n")
        return True
    except Exception:
        return False


def profile_is_complete(profile: Dict[str, Any]) -> bool:
    """Sjekk om profilen har minimumskrav for å kunne generere dokumenter."""
    required = ["company_name", "company_org_no", "contact_person", "contact_email"]
    return all(profile.get(field, "").strip() for field in required)


def _normalize_profile(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Sørg for at alle forventede felt finnes, og at lister er lister."""
    out = {**EMPTY_PROFILE, **rec}
    for list_field in ["approval_areas", "reference_projects", "certifications"]:
        v = out.get(list_field)
        if isinstance(v, str):
            try:
                out[list_field] = json.loads(v)
            except Exception:
                out[list_field] = []
        elif not isinstance(v, list):
            out[list_field] = []
    return out


# ─── Hjelpere for dokumentgenerering ─────────────────────────────
def format_letterhead(profile: Dict[str, Any]) -> str:
    """
    Bygg et tekstbasert firmabrev-header for DOCX-utskrift.
    Returnerer streng klar for å settes inn øverst i et dokument.
    """
    lines = []
    if profile.get("company_name"):
        lines.append(profile["company_name"])
    address_line = " ".join(x for x in [
        profile.get("company_address", ""),
        " ".join(x for x in [
            profile.get("company_postcode", ""),
            profile.get("company_city", ""),
        ] if x).strip(),
    ] if x).strip()
    if address_line:
        lines.append(address_line)
    if profile.get("company_org_no"):
        lines.append(f"Org.nr.: {profile['company_org_no']}")
    contact_parts = []
    if profile.get("contact_phone"):
        contact_parts.append(f"Tlf: {profile['contact_phone']}")
    if profile.get("contact_email"):
        contact_parts.append(f"E-post: {profile['contact_email']}")
    if contact_parts:
        lines.append(" · ".join(contact_parts))
    return "\n".join(lines)


def format_signature_block(profile: Dict[str, Any]) -> str:
    """Bygg en signatur-block for slutten av dokumenter."""
    lines = ["Med vennlig hilsen", ""]
    if profile.get("contact_person"):
        lines.append(profile["contact_person"])
    if profile.get("contact_title"):
        lines.append(profile["contact_title"])
    if profile.get("company_name"):
        lines.append(profile["company_name"])
    return "\n".join(lines)


def get_supabase_schema() -> str:
    """Returnerer SQL for å opprette tabellen — til bruk i setup-dokumentasjon."""
    return """
create table if not exists tender_company_profiles (
    profile_id uuid primary key default gen_random_uuid(),
    user_email text not null unique,
    company_name text,
    company_org_no text,
    company_address text,
    company_postcode text,
    company_city text,
    contact_person text,
    contact_title text,
    contact_email text,
    contact_phone text,
    ceo_name text,
    approval_areas jsonb default '[]',
    reference_projects jsonb default '[]',
    certifications jsonb default '[]',
    hms_policy text,
    quality_policy text,
    company_description text,
    logo_url text,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists idx_tender_company_profiles_email
    on tender_company_profiles(user_email);
""".strip()
