"""
Builtly Auth & Payment — Supabase + Stripe
===========================================
pip install supabase stripe --break-system-packages

Render env vars:
    SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY
    STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY
    STRIPE_PRICE_MODUL, STRIPE_PRICE_TEAM, STRIPE_PRICE_ENTERPRISE
    BUILTLY_BASE_URL  (e.g. https://builtly.ai)
"""
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, List
import streamlit as st

REPORT_RETENTION_DAYS = 30

def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()

# ── Supabase ─────────────────────────────────────────────────────────────────

@st.cache_resource
def _sb_client():
    url, key = _env("SUPABASE_URL"), _env("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None

@st.cache_resource
def _sb_admin():
    url, key = _env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None

def _sb():
    return _sb_client()

def services_ok() -> Dict[str, bool]:
    return {"supabase": _sb() is not None, "stripe": bool(_env("STRIPE_SECRET_KEY"))}

# ── AUTH ─────────────────────────────────────────────────────────────────────

def register(email: str, password: str, name: str, company: str,
             org_nr: str, phone: str, countries: list) -> Tuple[bool, str]:
    sb = _sb()
    if not sb:
        return False, "Supabase er ikke konfigurert. Kontakt post@builtly.ai."
    try:
        res = sb.auth.sign_up({
            "email": email, "password": password,
            "options": {"data": {
                "full_name": name, "company": company,
                "org_nr": org_nr, "phone": phone, "countries": countries,
            }}
        })
        if res.user:
            try:
                sb.table("profiles").upsert({
                    "id": res.user.id, "email": email,
                    "full_name": name, "company": company,
                    "org_nr": org_nr, "phone": phone,
                    "countries": countries, "plan": None,
                    "payment_method": None, "account_status": "pending_verification",
                    "created_at": datetime.utcnow().isoformat(),
                }).execute()
            except Exception:
                pass
            return True, (
                "✅ Konto opprettet! Sjekk e-posten din og klikk bekreftelseslenken "
                "for å aktivere kontoen. Sjekk spam/søppelpost."
            )
        return False, "Kunne ikke opprette konto."
    except Exception as e:
        msg = str(e).lower()
        if "already registered" in msg or "already exists" in msg:
            return False, "E-postadressen er allerede registrert. Prøv å logge inn."
        return False, f"Registreringsfeil: {e}"


def login(email: str, password: str) -> Tuple[bool, str]:
    sb = _sb()
    if not sb:
        return False, "Supabase er ikke konfigurert."
    try:
        res = sb.auth.sign_in_with_password({"email": email, "password": password})
        if not res.user:
            return False, "Feil e-post eller passord."
        meta = res.user.user_metadata or {}
        profile = {}
        try:
            row = sb.table("profiles").select("*").eq("id", res.user.id).single().execute()
            profile = row.data or {}
        except Exception:
            pass
        st.session_state.update({
            "user_authenticated": True,
            "user_email": res.user.email,
            "user_id": res.user.id,
            "user_name": profile.get("full_name") or meta.get("full_name", ""),
            "user_company": profile.get("company", ""),
            "user_countries": profile.get("countries", []),
            "user_plan": profile.get("plan", "") or "",
            "user_payment_method": profile.get("payment_method", "") or "",
            "user_account_status": profile.get("account_status", "active"),
            "site_access_granted": True,
        })
        # Persist auth tokens for session restoration on page reload
        if res.session:
            st.session_state["_sb_access_token"] = res.session.access_token
            st.session_state["_sb_refresh_token"] = res.session.refresh_token
        # Load reports
        try:
            reps = sb.table("reports").select("*").eq("user_id", res.user.id)\
                .order("created_at", desc=True).execute()
            st.session_state.user_reports = reps.data or []
        except Exception:
            st.session_state.user_reports = []
        return True, ""
    except Exception as e:
        msg = str(e).lower()
        if "not confirmed" in msg or "email" in msg:
            return False, "E-posten er ikke bekreftet ennå. Sjekk innboksen din."
        if "invalid" in msg or "credentials" in msg:
            return False, "Feil e-post eller passord."
        return False, f"Innloggingsfeil: {e}"


def logout():
    sb = _sb()
    if sb:
        try: sb.auth.sign_out()
        except Exception: pass
    for key in ["user_authenticated","user_email","user_name","user_company",
                "user_countries","user_plan","user_payment_method",
                "user_account_status","user_id","user_reports",
                "_sb_access_token","_sb_refresh_token"]:
        if key in st.session_state:
            if isinstance(st.session_state[key], bool): st.session_state[key] = False
            elif isinstance(st.session_state[key], list): st.session_state[key] = []
            else: st.session_state[key] = ""


def resend_verification(email: str) -> Tuple[bool, str]:
    sb = _sb()
    if not sb:
        return False, "Supabase er ikke konfigurert."
    try:
        sb.auth.resend({"type": "signup", "email": email})
        return True, "Ny bekreftelseslenke sendt!"
    except Exception as e:
        return False, f"Feil: {e}"


def restore_session() -> bool:
    """Restore Supabase auth session from stored tokens on page reload.

    Call this early in the Streamlit script. If session_state has valid tokens
    and the user is flagged as authenticated, the Supabase session is
    re-established (refreshed if expired). Returns True if the session
    was successfully restored, False otherwise.
    """
    # Nothing to restore
    if not st.session_state.get("user_authenticated"):
        return False
    access = st.session_state.get("_sb_access_token", "")
    refresh = st.session_state.get("_sb_refresh_token", "")
    if not access or not refresh:
        return False

    sb = _sb()
    if not sb:
        return False

    try:
        # set_session restores and auto-refreshes if the access token is expired
        res = sb.auth.set_session(access, refresh)
        if res and res.session:
            # Update tokens in case they were refreshed
            st.session_state["_sb_access_token"] = res.session.access_token
            st.session_state["_sb_refresh_token"] = res.session.refresh_token
            return True
        # Token fully expired — clear auth state
        _clear_auth_state()
        return False
    except Exception:
        # Refresh failed — try once more with refresh_session
        try:
            res = sb.auth.refresh_session(refresh)
            if res and res.session:
                st.session_state["_sb_access_token"] = res.session.access_token
                st.session_state["_sb_refresh_token"] = res.session.refresh_token
                return True
        except Exception:
            pass
        _clear_auth_state()
        return False


def _clear_auth_state():
    """Reset all auth-related session state to logged-out defaults."""
    st.session_state.update({
        "user_authenticated": False,
        "user_email": "",
        "user_name": "",
        "user_company": "",
        "user_countries": [],
        "user_plan": "",
        "user_payment_method": "",
        "user_account_status": "",
        "user_id": "",
        "user_reports": [],
        "_sb_access_token": "",
        "_sb_refresh_token": "",
    })

# ── STRIPE ───────────────────────────────────────────────────────────────────

def _init_stripe() -> bool:
    key = _env("STRIPE_SECRET_KEY")
    if not key: return False
    try:
        import stripe
        stripe.api_key = key
        return True
    except ImportError:
        return False

PLAN_PRICE_ENVS = {
    "modul": "STRIPE_PRICE_MODUL",
    "team": "STRIPE_PRICE_TEAM",
    "enterprise": "STRIPE_PRICE_ENTERPRISE",
}

def create_checkout(plan_key: str, n_countries: int = 1) -> Tuple[Optional[str], str]:
    if not _init_stripe():
        return None, "Stripe er ikke konfigurert."
    import stripe
    price_id = _env(PLAN_PRICE_ENVS.get(plan_key, ""))
    if not price_id:
        return None, f"Pris-ID for '{plan_key}' mangler i Render env vars."
    base = _env("BUILTLY_BASE_URL") or "https://builtly.ai"
    uid = st.session_state.get("user_id", "")
    try:
        sess = stripe.checkout.Session.create(
            mode="subscription", payment_method_types=["card"],
            customer_email=st.session_state.get("user_email", ""),
            line_items=[{"price": price_id, "quantity": n_countries}],
            success_url=f"{base}?auth=payment_success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}?auth=plans",
            metadata={"builtly_user_id": uid, "builtly_plan": plan_key},
            subscription_data={"metadata": {"builtly_user_id": uid, "builtly_plan": plan_key}},
        )
        return sess.url, ""
    except Exception as e:
        return None, f"Stripe-feil: {e}"


def verify_checkout(session_id: str) -> Tuple[bool, str]:
    if not _init_stripe():
        return False, "Stripe ikke konfigurert."
    import stripe
    try:
        sess = stripe.checkout.Session.retrieve(session_id)
        if sess.payment_status == "paid":
            plan = sess.metadata.get("builtly_plan", "")
            uid = sess.metadata.get("builtly_user_id", "")
            sb = _sb()
            if sb and uid:
                try:
                    sb.table("profiles").update({
                        "plan": plan, "payment_method": "card",
                        "account_status": "active",
                        "stripe_customer_id": sess.customer,
                        "stripe_subscription_id": sess.subscription,
                        "activated_at": datetime.utcnow().isoformat(),
                    }).eq("id", uid).execute()
                except Exception: pass
            st.session_state.update({
                "user_plan": plan, "user_payment_method": "card",
                "user_account_status": "active",
            })
            return True, "✅ Betaling godkjent! Kontoen din er nå aktiv."
        return False, "Betaling ikke fullført."
    except Exception as e:
        return False, f"Verifiseringsfeil: {e}"


def request_invoice(plan_key: str, n_countries: int = 1) -> Tuple[bool, str]:
    sb = _sb()
    uid = st.session_state.get("user_id", "")
    if sb and uid:
        try:
            sb.table("profiles").update({
                "plan": plan_key, "payment_method": "invoice",
                "account_status": "pending_invoice",
                "invoice_requested_at": datetime.utcnow().isoformat(),
                "countries_count": n_countries,
            }).eq("id", uid).execute()
        except Exception: pass
    st.session_state.update({
        "user_plan": plan_key, "user_payment_method": "invoice",
        "user_account_status": "pending_invoice",
    })
    return True, (
        "📄 Bestilling mottatt! Faktura sendes til din e-post. "
        "Kontoen aktiveres når betaling er registrert (1–3 virkedager)."
    )


def activate_invoice_user(user_id: str) -> Tuple[bool, str]:
    sb = _sb_admin()
    if not sb: return False, "Admin ikke konfigurert."
    try:
        sb.table("profiles").update({
            "account_status": "active",
            "activated_at": datetime.utcnow().isoformat(),
        }).eq("id", user_id).execute()
        return True, "Bruker aktivert."
    except Exception as e:
        return False, f"Feil: {e}"

# ── REPORTS ──────────────────────────────────────────────────────────────────

def save_report(project_name: str, report_name: str, module: str,
                file_path: str = "", download_url: str = "") -> bool:
    sb = _sb()
    uid = st.session_state.get("user_id", "")
    now = datetime.utcnow()
    expires = now + timedelta(days=REPORT_RETENTION_DAYS)
    entry = {
        "project": project_name or "Uten prosjekt",
        "name": report_name, "module": module,
        "file_path": file_path, "download_url": download_url,
        "created_at": now.strftime("%Y-%m-%d %H:%M"),
        "expires_at": expires.strftime("%Y-%m-%d"),
    }
    if sb and uid:
        try:
            sb.table("reports").insert({**entry, "user_id": uid}).execute()
        except Exception: pass
    if "user_reports" not in st.session_state:
        st.session_state.user_reports = []
    st.session_state.user_reports.append(entry)
    return True

def delete_expired_reports():
    sb = _sb()
    if not sb: return
    try:
        cutoff = datetime.utcnow().isoformat()
        sb.table("reports").delete().lt("expires_at", cutoff).execute()
    except Exception: pass
