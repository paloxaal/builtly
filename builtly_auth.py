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
import html as _html
import json
import base64
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, List
import streamlit as st
import streamlit.components.v1 as components

REPORT_RETENTION_DAYS = 30

# ── I18N — user-facing messages ──────────────────────────────────────────────

_MSG = {
    "🇳🇴 Norsk": {
        "sb_not_configured": "Supabase er ikke konfigurert. Kontakt post@builtly.ai.",
        "account_created": "✅ Konto opprettet! Sjekk e-posten din og klikk bekreftelseslenken for å aktivere kontoen. Sjekk spam/søppelpost.",
        "account_create_fail": "Kunne ikke opprette konto.",
        "email_exists": "E-postadressen er allerede registrert. Prøv å logge inn.",
        "register_error": "Registreringsfeil: {e}",
        "wrong_credentials": "Feil e-post eller passord.",
        "email_not_confirmed": "E-posten er ikke bekreftet ennå. Sjekk innboksen din.",
        "login_error": "Innloggingsfeil: {e}",
        "verification_sent": "Ny bekreftelseslenke sendt!",
        "error_generic": "Feil: {e}",
        "stripe_not_configured": "Stripe er ikke konfigurert.",
        "price_missing": "Pris-ID for '{plan}' mangler i Render env vars.",
        "stripe_error": "Stripe-feil: {e}",
        "payment_ok": "✅ Betaling godkjent! Kontoen din er nå aktiv.",
        "payment_incomplete": "Betaling ikke fullført.",
        "verify_error": "Verifiseringsfeil: {e}",
        "invoice_ok": "📄 Bestilling mottatt! Faktura sendes til din e-post. Kontoen aktiveres når betaling er registrert (1–3 virkedager).",
        "admin_not_configured": "Admin ikke konfigurert.",
        "user_activated": "Bruker aktivert.",
    },
    "🇬🇧 English (UK)": {
        "sb_not_configured": "Supabase is not configured. Contact post@builtly.ai.",
        "account_created": "✅ Account created! Check your email and click the verification link to activate your account. Check spam/junk.",
        "account_create_fail": "Could not create account.",
        "email_exists": "This email is already registered. Try logging in.",
        "register_error": "Registration error: {e}",
        "wrong_credentials": "Wrong email or password.",
        "email_not_confirmed": "Email not yet confirmed. Check your inbox.",
        "login_error": "Login error: {e}",
        "verification_sent": "New verification link sent!",
        "error_generic": "Error: {e}",
        "stripe_not_configured": "Stripe is not configured.",
        "price_missing": "Price ID for '{plan}' is missing in Render env vars.",
        "stripe_error": "Stripe error: {e}",
        "payment_ok": "✅ Payment approved! Your account is now active.",
        "payment_incomplete": "Payment not completed.",
        "verify_error": "Verification error: {e}",
        "invoice_ok": "📄 Order received! Invoice will be sent to your email. Account activates when payment is registered (1–3 business days).",
        "admin_not_configured": "Admin not configured.",
        "user_activated": "User activated.",
    },
    "🇺🇸 English (US)": {},  # falls back to UK English
    "🇸🇪 Svenska": {
        "sb_not_configured": "Supabase är inte konfigurerat. Kontakta post@builtly.ai.",
        "account_created": "✅ Konto skapad! Kontrollera din e-post och klicka på verifieringslänken för att aktivera kontot. Kontrollera skräppost.",
        "account_create_fail": "Kunde inte skapa konto.",
        "email_exists": "E-postadressen är redan registrerad. Försök logga in.",
        "register_error": "Registreringsfel: {e}",
        "wrong_credentials": "Fel e-post eller lösenord.",
        "email_not_confirmed": "E-posten är inte bekräftad ännu. Kontrollera din inkorg.",
        "login_error": "Inloggningsfel: {e}",
        "verification_sent": "Ny verifieringslänk skickad!",
        "error_generic": "Fel: {e}",
        "stripe_not_configured": "Stripe är inte konfigurerat.",
        "price_missing": "Pris-ID för '{plan}' saknas i Render env vars.",
        "stripe_error": "Stripe-fel: {e}",
        "payment_ok": "✅ Betalning godkänd! Ditt konto är nu aktivt.",
        "payment_incomplete": "Betalning ej slutförd.",
        "verify_error": "Verifieringsfel: {e}",
        "invoice_ok": "📄 Beställning mottagen! Faktura skickas till din e-post. Kontot aktiveras när betalning registrerats (1–3 arbetsdagar).",
        "admin_not_configured": "Admin inte konfigurerad.",
        "user_activated": "Användare aktiverad.",
    },
    "🇩🇰 Dansk": {
        "sb_not_configured": "Supabase er ikke konfigureret. Kontakt post@builtly.ai.",
        "account_created": "✅ Konto oprettet! Tjek din e-mail og klik på bekræftelseslinket for at aktivere kontoen. Tjek spam/uønsket post.",
        "account_create_fail": "Kunne ikke oprette konto.",
        "email_exists": "E-mailadressen er allerede registreret. Prøv at logge ind.",
        "register_error": "Registreringsfejl: {e}",
        "wrong_credentials": "Forkert e-mail eller adgangskode.",
        "email_not_confirmed": "E-mailen er ikke bekræftet endnu. Tjek din indbakke.",
        "login_error": "Loginfejl: {e}",
        "verification_sent": "Nyt bekræftelseslink sendt!",
        "error_generic": "Fejl: {e}",
        "stripe_not_configured": "Stripe er ikke konfigureret.",
        "price_missing": "Pris-ID for '{plan}' mangler i Render env vars.",
        "stripe_error": "Stripe-fejl: {e}",
        "payment_ok": "✅ Betaling godkendt! Din konto er nu aktiv.",
        "payment_incomplete": "Betaling ikke gennemført.",
        "verify_error": "Bekræftelsesfejl: {e}",
        "invoice_ok": "📄 Bestilling modtaget! Faktura sendes til din e-mail. Kontoen aktiveres, når betaling er registreret (1–3 hverdage).",
        "admin_not_configured": "Admin ikke konfigureret.",
        "user_activated": "Bruger aktiveret.",
    },
    "🇫🇮 Suomi": {
        "sb_not_configured": "Supabasea ei ole määritetty. Ota yhteyttä post@builtly.ai.",
        "account_created": "✅ Tili luotu! Tarkista sähköpostisi ja napsauta vahvistuslinkkiä aktivoidaksesi tilisi. Tarkista roskaposti.",
        "account_create_fail": "Tilin luonti epäonnistui.",
        "email_exists": "Sähköpostiosoite on jo rekisteröity. Yritä kirjautua sisään.",
        "register_error": "Rekisteröintivirhe: {e}",
        "wrong_credentials": "Väärä sähköposti tai salasana.",
        "email_not_confirmed": "Sähköpostia ei ole vielä vahvistettu. Tarkista saapuneet-kansiosi.",
        "login_error": "Kirjautumisvirhe: {e}",
        "verification_sent": "Uusi vahvistuslinkki lähetetty!",
        "error_generic": "Virhe: {e}",
        "stripe_not_configured": "Stripeä ei ole määritetty.",
        "price_missing": "Hinta-ID puuttuu '{plan}' — aseta Render env vars.",
        "stripe_error": "Stripe-virhe: {e}",
        "payment_ok": "✅ Maksu hyväksytty! Tilisi on nyt aktiivinen.",
        "payment_incomplete": "Maksua ei suoritettu loppuun.",
        "verify_error": "Vahvistusvirhe: {e}",
        "invoice_ok": "📄 Tilaus vastaanotettu! Lasku lähetetään sähköpostiisi. Tili aktivoidaan, kun maksu on rekisteröity (1–3 arkipäivää).",
        "admin_not_configured": "Adminia ei ole määritetty.",
        "user_activated": "Käyttäjä aktivoitu.",
    },
    "🇩🇪 Deutsch": {
        "sb_not_configured": "Supabase ist nicht konfiguriert. Kontaktieren Sie post@builtly.ai.",
        "account_created": "✅ Konto erstellt! Überprüfen Sie Ihre E-Mail und klicken Sie auf den Bestätigungslink, um Ihr Konto zu aktivieren. Prüfen Sie auch den Spam-Ordner.",
        "account_create_fail": "Konto konnte nicht erstellt werden.",
        "email_exists": "Diese E-Mail-Adresse ist bereits registriert. Versuchen Sie sich anzumelden.",
        "register_error": "Registrierungsfehler: {e}",
        "wrong_credentials": "Falsche E-Mail oder Passwort.",
        "email_not_confirmed": "E-Mail noch nicht bestätigt. Überprüfen Sie Ihren Posteingang.",
        "login_error": "Anmeldefehler: {e}",
        "verification_sent": "Neuer Bestätigungslink gesendet!",
        "error_generic": "Fehler: {e}",
        "stripe_not_configured": "Stripe ist nicht konfiguriert.",
        "price_missing": "Preis-ID für '{plan}' fehlt in Render env vars.",
        "stripe_error": "Stripe-Fehler: {e}",
        "payment_ok": "✅ Zahlung bestätigt! Ihr Konto ist jetzt aktiv.",
        "payment_incomplete": "Zahlung nicht abgeschlossen.",
        "verify_error": "Bestätigungsfehler: {e}",
        "invoice_ok": "📄 Bestellung eingegangen! Rechnung wird an Ihre E-Mail gesendet. Konto wird aktiviert, sobald die Zahlung registriert ist (1–3 Werktage).",
        "admin_not_configured": "Admin nicht konfiguriert.",
        "user_activated": "Benutzer aktiviert.",
    },
}

# UK English is the full fallback
_MSG_FALLBACK = _MSG["🇬🇧 English (UK)"]
# US English inherits from UK
_MSG["🇺🇸 English (US)"] = {**_MSG_FALLBACK}


def _m(key: str, lang: str = "", **kwargs) -> str:
    """Retrieve a translated message. Falls back to English if key missing."""
    if not lang:
        lang = st.session_state.get("app_lang", "🇬🇧 English (UK)")
    bundle = _MSG.get(lang, _MSG_FALLBACK)
    template = bundle.get(key) or _MSG_FALLBACK.get(key, key)
    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError):
        return template

def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()

# ── Supabase ─────────────────────────────────────────────────────────────────

# VIKTIG om multi-bruker-sikkerhet:
# Tidligere var disse funksjonene dekorert med @st.cache_resource, som deler
# ÉN enkelt Supabase-client mellom ALLE brukere på serveren. Dette er farlig
# fordi Supabase-clienten har intern auth-state (access/refresh token). Når
# Bruker A logger inn muterer sb.auth.sign_in_with_password() clientens
# token globalt — Bruker B's spørringer ville dermed kunne bruke A's token.
#
# Løsning: hver Streamlit-session får sin egen client-instans, lagret i
# st.session_state. Streamlit garanterer at session_state er isolert per
# browser-session, så to brukere kan ikke dele client lenger.

def _sb_client():
    """Anon Supabase-client, isolert per Streamlit-session."""
    existing = st.session_state.get("_sb_anon_client")
    if existing is not None:
        return existing
    url, key = _env("SUPABASE_URL"), _env("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        client = create_client(url, key)
        st.session_state["_sb_anon_client"] = client
        return client
    except Exception:
        return None


def _sb_admin():
    """Service-key (admin) Supabase-client, isolert per Streamlit-session.

    Service-key autentiseres ikke som en bruker (har full DB-tilgang),
    så den har ikke samme token-mutation-problemet som anon-clienten.
    Men vi holder den session-isolert uansett for konsistens og for å
    unngå at admin-client-instansen deles via Streamlits globale cache.
    """
    existing = st.session_state.get("_sb_admin_client")
    if existing is not None:
        return existing
    url, key = _env("SUPABASE_URL"), _env("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        client = create_client(url, key)
        st.session_state["_sb_admin_client"] = client
        return client
    except Exception:
        return None

def _sb():
    return _sb_client()

def services_ok() -> Dict[str, bool]:
    return {"supabase": _sb() is not None, "stripe": bool(_env("STRIPE_SECRET_KEY"))}

# ── AUTH ─────────────────────────────────────────────────────────────────────

def register(email: str, password: str, name: str, company: str,
             org_nr: str, phone: str, countries: list,
             lang: str = "") -> Tuple[bool, str]:
    sb = _sb()
    if not sb:
        return False, _m("sb_not_configured", lang)
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
            return True, _m("account_created", lang)
        return False, _m("account_create_fail", lang)
    except Exception as e:
        msg = str(e).lower()
        if "already registered" in msg or "already exists" in msg:
            return False, _m("email_exists", lang)
        return False, _m("register_error", lang, e=e)


def login(email: str, password: str, lang: str = "") -> Tuple[bool, str]:
    sb = _sb()
    if not sb:
        return False, _m("sb_not_configured", lang)
    try:
        res = sb.auth.sign_in_with_password({"email": email, "password": password})
        if not res.user:
            return False, _m("wrong_credentials", lang)
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
            _persist_tokens_to_browser(res.session.refresh_token)
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
            return False, _m("email_not_confirmed", lang)
        if "invalid" in msg or "credentials" in msg:
            return False, _m("wrong_credentials", lang)
        return False, _m("login_error", lang, e=e)


def logout():
    sb = _sb()
    if sb:
        try: sb.auth.sign_out()
        except Exception: pass
    _clear_browser_tokens()
    for key in ["user_authenticated","user_email","user_name","user_company",
                "user_countries","user_plan","user_payment_method",
                "user_account_status","user_id","user_reports",
                "_sb_access_token","_sb_refresh_token"]:
        if key in st.session_state:
            if isinstance(st.session_state[key], bool): st.session_state[key] = False
            elif isinstance(st.session_state[key], list): st.session_state[key] = []
            else: st.session_state[key] = ""
    # Fjern client-instansene helt fra session_state slik at neste bruker
    # (hvis samme browser brukes for login på ny) får en ren, uautentisert client.
    for client_key in ("_sb_anon_client", "_sb_admin_client"):
        if client_key in st.session_state:
            del st.session_state[client_key]


def resend_verification(email: str, lang: str = "") -> Tuple[bool, str]:
    sb = _sb()
    if not sb:
        return False, _m("sb_not_configured", lang)
    try:
        sb.auth.resend({"type": "signup", "email": email})
        return True, _m("verification_sent", lang)
    except Exception as e:
        return False, _m("error_generic", lang, e=e)


def restore_session() -> bool:
    """Restore Supabase auth session from stored tokens on page reload.

    Call this early in the Streamlit script. If session_state has valid tokens
    and the user is flagged as authenticated, the Supabase session is
    re-established (refreshed if expired). Returns True if the session
    was successfully restored, False otherwise.

    NOTE: Does NOT clear auth state on failure — the caller decides whether
    to log the user out. This prevents aggressive logouts on transient
    Supabase API errors.
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
            _persist_tokens_to_browser(res.session.refresh_token)
            return True
        return False
    except Exception:
        # Refresh failed — try once more with refresh_session
        try:
            res = sb.auth.refresh_session(refresh)
            if res and res.session:
                st.session_state["_sb_access_token"] = res.session.access_token
                st.session_state["_sb_refresh_token"] = res.session.refresh_token
                _persist_tokens_to_browser(res.session.refresh_token)
                return True
        except Exception:
            pass
        return False


def try_restore_from_browser() -> bool:
    """Restore session from browser cookie (or fallback _brt query param).

    Reads the 'builtly_rt' cookie from the HTTP request headers. The cookie
    is set by _persist_tokens_to_browser() after login. Because cookies are
    sent automatically with every HTTP request, no JS redirect is needed.

    Returns True if session was restored, False otherwise.
    """
    # Primary: read refresh token from HTTP cookie header
    rt = _read_cookie("builtly_rt")

    # Fallback: check _brt query param (legacy localStorage redirect flow)
    if not rt:
        try:
            rt = st.query_params.get("_brt", "")
        except Exception:
            rt = ""

    if not rt:
        return False

    sb = _sb()
    if not sb:
        _remove_brt_param()
        return False

    try:
        res = sb.auth.refresh_session(rt)
        if res and res.session and res.user:
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
                "_sb_access_token": res.session.access_token,
                "_sb_refresh_token": res.session.refresh_token,
            })

            # Load reports
            try:
                reps = sb.table("reports").select("*").eq("user_id", res.user.id)\
                    .order("created_at", desc=True).execute()
                st.session_state.user_reports = reps.data or []
            except Exception:
                st.session_state.user_reports = []

            # Update browser cookie with fresh tokens
            _persist_tokens_to_browser(res.session.refresh_token)
            _remove_brt_param()
            return True
    except Exception:
        pass

    # Failed — clear browser tokens and query param
    _clear_browser_tokens()
    _remove_brt_param()
    return False


def _read_cookie(name: str) -> str:
    """Read a cookie value from the HTTP request headers."""
    cookie_str = ""
    try:
        cookie_str = st.context.headers.get("Cookie", "")
    except (AttributeError, Exception):
        pass
    if not cookie_str:
        return ""
    for part in cookie_str.split(";"):
        part = part.strip()
        if part.startswith(f"{name}="):
            return part[len(name) + 1:]
    return ""


def inject_browser_token_reader():
    """No-op kept for backward compatibility.

    With the cookie-based approach, tokens are read directly from HTTP
    headers via try_restore_from_browser() — no JS redirect needed.
    """
    pass


def _persist_tokens_to_browser(refresh_token: str):
    """Write refresh token to browser cookie AND localStorage (belt & suspenders)."""
    safe_rt = _html.escape(refresh_token, quote=True)
    max_age = 60 * 60 * 24 * 7  # 7 days
    components.html(f"""<script>
    (function() {{
        try {{
            // Set cookie on parent document (readable by Streamlit server)
            var cookieStr = 'builtly_rt={safe_rt}; path=/; max-age={max_age}; SameSite=Lax';
            try {{ window.parent.document.cookie = cookieStr; }} catch(e) {{
                document.cookie = cookieStr;
            }}
            // Also keep localStorage as fallback
            localStorage.setItem('builtly_rt', '{safe_rt}');
        }} catch(e) {{}}
    }})();
    </script>""", height=0)


def _clear_browser_tokens():
    """Remove auth tokens from browser cookie and localStorage."""
    components.html("""<script>
    (function() {
        try {
            // Expire cookie
            var cookieStr = 'builtly_rt=; path=/; max-age=0; SameSite=Lax';
            try { window.parent.document.cookie = cookieStr; } catch(e) {
                document.cookie = cookieStr;
            }
            localStorage.removeItem('builtly_rt');
        } catch(e) {}
    })();
    </script>""", height=0)


def _remove_brt_param():
    """Remove the _brt query parameter after processing."""
    try:
        params = dict(st.query_params)
        params.pop("_brt", None)
        st.query_params.update(params)
        if "_brt" in st.query_params:
            del st.query_params["_brt"]
    except Exception:
        pass


def _clear_auth_state():
    """Reset all auth-related session state to logged-out defaults."""
    _clear_browser_tokens()
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
    # Fjern client-instansene så neste bruker får ren, uautentisert client
    for client_key in ("_sb_anon_client", "_sb_admin_client"):
        if client_key in st.session_state:
            del st.session_state[client_key]

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

def create_checkout(plan_key: str, n_countries: int = 1,
                    lang: str = "") -> Tuple[Optional[str], str]:
    if not _init_stripe():
        return None, _m("stripe_not_configured", lang)
    import stripe
    price_id = _env(PLAN_PRICE_ENVS.get(plan_key, ""))
    if not price_id:
        return None, _m("price_missing", lang, plan=plan_key)
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
        return None, _m("stripe_error", lang, e=e)


def verify_checkout(session_id: str, lang: str = "") -> Tuple[bool, str]:
    if not _init_stripe():
        return False, _m("stripe_not_configured", lang)
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
            return True, _m("payment_ok", lang)
        return False, _m("payment_incomplete", lang)
    except Exception as e:
        return False, _m("verify_error", lang, e=e)


def request_invoice(plan_key: str, n_countries: int = 1,
                    lang: str = "") -> Tuple[bool, str]:
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
    return True, _m("invoice_ok", lang)


def activate_invoice_user(user_id: str, lang: str = "") -> Tuple[bool, str]:
    sb = _sb_admin()
    if not sb: return False, _m("admin_not_configured", lang)
    try:
        sb.table("profiles").update({
            "account_status": "active",
            "activated_at": datetime.utcnow().isoformat(),
        }).eq("id", user_id).execute()
        return True, _m("user_activated", lang)
    except Exception as e:
        return False, _m("error_generic", lang, e=e)

# ── REPORTS ──────────────────────────────────────────────────────────────────

def save_report(project_name: str, report_name: str, module: str,
                file_path: str = "", download_url: str = "") -> bool:
    sb_admin = _sb_admin()
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
    saved = False
    debug = f"save_report: uid={'set' if uid else 'EMPTY'}, admin={'ok' if sb_admin else 'None'}, anon={'ok' if sb else 'None'}"
    if uid:
        db_entry = {**entry, "user_id": uid}
        for client, label in [(sb_admin, "admin"), (sb, "anon")]:
            if client and not saved:
                try:
                    client.table("reports").insert(db_entry).execute()
                    saved = True
                    debug += f", insert={label}=OK"
                except Exception as e:
                    debug += f", insert={label}=FAIL({str(e)[:50]})"
    else:
        debug += ", SKIPPED (no uid)"
    # Store debug info in session_state so it survives st.rerun()
    st.session_state["_report_save_debug"] = debug
    if "user_reports" not in st.session_state:
        st.session_state.user_reports = []
    st.session_state.user_reports.append(entry)
    return saved


def reload_user_reports():
    """Reload reports from Supabase for the current user."""
    sb = _sb()
    uid = st.session_state.get("user_id", "")
    if sb and uid:
        try:
            reps = sb.table("reports").select("*").eq("user_id", uid)\
                .order("created_at", desc=True).execute()
            st.session_state.user_reports = reps.data or []
            return True
        except Exception:
            pass
    return False

def delete_expired_reports():
    sb = _sb()
    if not sb: return
    try:
        cutoff = datetime.utcnow().isoformat()
        sb.table("reports").delete().lt("expires_at", cutoff).execute()
    except Exception: pass
