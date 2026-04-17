# -*- coding: utf-8 -*-
"""
Builtly | Tender Radar — Notifier
─────────────────────────────────────────────────────────────────
Sender varsler (email via Resend, Slack webhook) om alerts som
overstiger bruker-terskel og ikke er varslet ennå.

Støtter tre moduser (bruker-konfig):
  - instant        : varsle umiddelbart per treff
  - daily_digest   : én epost per dag med alle nye treff
  - weekly_digest  : én epost per uke
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    requests = None  # type: ignore
    HAS_REQUESTS = False

try:
    from supabase import create_client, Client as SupabaseClient
except ImportError:
    create_client = None
    SupabaseClient = None  # type: ignore


RESEND_API_URL = "https://api.resend.com/emails"
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "radar@builtly.ai")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")

BUILTLY_BASE_URL = os.environ.get("BUILTLY_BASE_URL", "https://builtly.ai")


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


# ─── Hent alerts som trenger varsling ────────────────────────────
def get_pending_alerts(mode: str = "instant") -> List[Dict[str, Any]]:
    """
    Hent alerts som skal varsles nå, basert på watch.notification_mode.

    - instant: status = 'new' AND fit_score >= terskel AND notified_at IS NULL
    - daily_digest: samme + siste varsling > 24t siden
    - weekly_digest: samme + siste varsling > 7d siden
    """
    sb = _sb()
    if not sb:
        return []

    try:
        # Hent alle new/unnotified alerts, joine inn watch + profile
        resp = (
            sb.table("tender_alerts")
            .select("*, tender_watches(*, tender_profiles(*)), tender_sources(*)")
            .eq("status", "new")
            .is_("notified_at", "null")
            .order("fit_score", desc=True)
            .execute()
        )
        return resp.data or []
    except Exception:
        return []


def filter_by_threshold(alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Behold bare alerts som overstiger watchens fit_threshold."""
    kept = []
    for a in alerts:
        watch = a.get("tender_watches") or {}
        profile = watch.get("tender_profiles") or {}
        threshold = watch.get("fit_threshold") or profile.get("default_fit_threshold") or 70
        if (a.get("fit_score") or 0) >= threshold:
            kept.append(a)
    return kept


# ─── Varslingsrender ─────────────────────────────────────────────
def render_alert_email_html(alert: Dict[str, Any]) -> str:
    """Bygg HTML-epost for ett alert (instant-mode)."""
    source = alert.get("tender_sources") or {}
    watch = alert.get("tender_watches") or {}

    fit = alert.get("fit_score") or 0
    fit_color = "#10b981" if fit >= 80 else "#f59e0b" if fit >= 60 else "#64748b"

    title = source.get("title") or "(uten tittel)"
    buyer = source.get("buyer_name") or ""
    deadline = source.get("submission_deadline") or ""
    value = source.get("estimated_value_nok")
    value_str = f"{value:,.0f} NOK".replace(",", " ") if value else ""

    summary = alert.get("quick_summary") or ""
    reasoning = alert.get("fit_reasoning") or ""
    why = alert.get("why_interesting") or ""

    risk_html = ""
    for r in (alert.get("quick_risk_flags") or [])[:5]:
        sev = (r.get("severity") or "").upper()
        sev_color = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#64748b"}.get(sev, "#64748b")
        risk_html += (
            f'<li style="margin-bottom:6px;"><span style="color:{sev_color};font-weight:600;">[{sev}]</span> '
            f'{r.get("issue", "")}</li>'
        )

    source_url = source.get("source_url") or source.get("kgv_url") or ""
    alert_url = f"{BUILTLY_BASE_URL}/TenderRadar?alert_id={alert.get('alert_id', '')}"

    return f"""<!doctype html>
<html><body style="font-family:Inter,system-ui,sans-serif;background:#f8fafc;padding:32px 16px;margin:0;">
<div style="max-width:620px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0;">
  <div style="background:#06111a;color:#f5f7fb;padding:24px;">
    <div style="font-size:11px;letter-spacing:0.1em;text-transform:uppercase;color:#38bdf8;font-weight:600;">
      BUILTLY TENDER RADAR · {watch.get('name', 'Overvåking')}
    </div>
    <div style="font-size:20px;font-weight:700;margin-top:8px;line-height:1.3;">{title}</div>
    <div style="margin-top:12px;display:flex;align-items:center;gap:12px;">
      <span style="background:{fit_color};color:#06111a;padding:4px 12px;border-radius:6px;font-weight:700;font-size:14px;">
        Fit: {fit}/100
      </span>
      <span style="color:#9fb0c3;font-size:14px;">{alert.get('go_no_go_hint', '')}</span>
    </div>
  </div>

  <div style="padding:24px;color:#1e293b;">
    <table style="width:100%;border-collapse:collapse;margin-bottom:20px;font-size:14px;">
      <tr><td style="padding:4px 0;color:#64748b;width:120px;">Oppdragsgiver:</td><td style="padding:4px 0;font-weight:600;">{buyer}</td></tr>
      <tr><td style="padding:4px 0;color:#64748b;">Frist:</td><td style="padding:4px 0;">{deadline}</td></tr>
      <tr><td style="padding:4px 0;color:#64748b;">Estimert verdi:</td><td style="padding:4px 0;">{value_str}</td></tr>
      <tr><td style="padding:4px 0;color:#64748b;">Sted:</td><td style="padding:4px 0;">{source.get('location', '')}</td></tr>
    </table>

    {"<p style='font-size:15px;line-height:1.6;margin:0 0 16px 0;'>" + summary + "</p>" if summary else ""}

    {"<div style='background:#f0f9ff;border-left:3px solid #38bdf8;padding:12px 16px;border-radius:4px;margin-bottom:16px;font-size:14px;color:#0c4a6e;'><strong>Hvorfor interessant:</strong> " + why + "</div>" if why else ""}

    {"<div style='margin-bottom:16px;'><div style='font-size:13px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;'>Vurdering</div><div style='font-size:14px;color:#334155;line-height:1.5;'>" + reasoning + "</div></div>" if reasoning else ""}

    {"<div style='margin-bottom:20px;'><div style='font-size:13px;color:#64748b;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;'>Risikoflagg</div><ul style='margin:0;padding-left:20px;font-size:14px;color:#334155;'>" + risk_html + "</ul></div>" if risk_html else ""}

    <div style="display:flex;gap:12px;margin-top:24px;">
      <a href="{alert_url}" style="background:#38bdf8;color:#06111a;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;">
        Åpne i Builtly →
      </a>
      {f'<a href="{source_url}" style="background:#f1f5f9;color:#334155;padding:10px 20px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;border:1px solid #e2e8f0;">Se kunngjøring</a>' if source_url else ''}
    </div>

    <div style="margin-top:32px;padding-top:16px;border-top:1px solid #e2e8f0;font-size:12px;color:#94a3b8;">
      Dette varselet kom fra overvåkingsregelen "{watch.get('name', '')}".
      Justér innstillinger eller pause varsler i Builtly → Tender Radar.
    </div>
  </div>
</div>
</body></html>"""


def render_digest_email_html(alerts: List[Dict[str, Any]], profile: Dict[str, Any]) -> str:
    """Bygg sammendrag-epost (daily/weekly digest)."""
    rows_html = ""
    for a in alerts[:30]:
        src = a.get("tender_sources") or {}
        fit = a.get("fit_score") or 0
        fit_color = "#10b981" if fit >= 80 else "#f59e0b" if fit >= 60 else "#64748b"
        alert_url = f"{BUILTLY_BASE_URL}/TenderRadar?alert_id={a.get('alert_id', '')}"

        rows_html += f"""
        <tr>
            <td style="padding:12px 8px;border-bottom:1px solid #e2e8f0;vertical-align:top;">
                <div style="font-weight:600;color:#1e293b;font-size:14px;">{src.get('title', '(uten tittel)')[:120]}</div>
                <div style="font-size:12px;color:#64748b;margin-top:4px;">{src.get('buyer_name', '')}</div>
            </td>
            <td style="padding:12px 8px;border-bottom:1px solid #e2e8f0;vertical-align:top;text-align:center;">
                <span style="background:{fit_color};color:#06111a;padding:3px 10px;border-radius:4px;font-weight:700;font-size:12px;">{fit}</span>
            </td>
            <td style="padding:12px 8px;border-bottom:1px solid #e2e8f0;vertical-align:top;text-align:right;">
                <a href="{alert_url}" style="color:#38bdf8;text-decoration:none;font-weight:600;font-size:13px;">Åpne →</a>
            </td>
        </tr>
        """

    display = profile.get("display_name") or profile.get("user_email", "")

    return f"""<!doctype html>
<html><body style="font-family:Inter,system-ui,sans-serif;background:#f8fafc;padding:32px 16px;margin:0;">
<div style="max-width:680px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e2e8f0;">
  <div style="background:#06111a;color:#f5f7fb;padding:24px;">
    <div style="font-size:11px;letter-spacing:0.1em;text-transform:uppercase;color:#38bdf8;font-weight:600;">
      BUILTLY TENDER RADAR · DIGEST
    </div>
    <div style="font-size:22px;font-weight:700;margin-top:8px;">{len(alerts)} nye relevante anbud</div>
    <div style="color:#9fb0c3;font-size:14px;margin-top:4px;">Hei {display} — her er siste skår:</div>
  </div>
  <div style="padding:8px 24px 24px 24px;">
    <table style="width:100%;border-collapse:collapse;">
      <tr>
        <th style="text-align:left;padding:8px;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;">Anbud</th>
        <th style="text-align:center;padding:8px;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;">Fit</th>
        <th style="padding:8px;"></th>
      </tr>
      {rows_html}
    </table>
    <div style="margin-top:24px;text-align:center;">
      <a href="{BUILTLY_BASE_URL}/TenderRadar" style="background:#38bdf8;color:#06111a;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px;">
        Se alle i Builtly →
      </a>
    </div>
  </div>
</div>
</body></html>"""


# ─── Send ────────────────────────────────────────────────────────
def send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY or not HAS_REQUESTS:
        return False
    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": RESEND_FROM_EMAIL,
                "to": [to],
                "subject": subject,
                "html": html,
            },
            timeout=30,
        )
        return resp.status_code in (200, 202)
    except Exception:
        return False


def send_slack(webhook_url: str, alert: Dict[str, Any]) -> bool:
    if not HAS_REQUESTS or not webhook_url:
        return False
    src = alert.get("tender_sources") or {}
    fit = alert.get("fit_score") or 0
    try:
        resp = requests.post(
            webhook_url,
            json={
                "text": f"*Nytt anbud • Fit {fit}/100* — {src.get('title', '')}",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{src.get('title', '')}*\n"
                                f"Oppdragsgiver: {src.get('buyer_name', '')}\n"
                                f"Frist: {src.get('submission_deadline', '')}\n"
                                f"Fit-score: *{fit}/100* · {alert.get('go_no_go_hint', '')}\n\n"
                                f"{alert.get('quick_summary', '')}"
                            ),
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Åpne i Builtly"},
                                "url": f"{BUILTLY_BASE_URL}/TenderRadar?alert_id={alert.get('alert_id', '')}",
                                "style": "primary",
                            }
                        ],
                    },
                ],
            },
            timeout=15,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


def mark_notified(alert_id: str) -> None:
    sb = _sb()
    if not sb:
        return
    try:
        sb.table("tender_alerts").update({
            "status": "notified",
            "notified_at": datetime.now(timezone.utc).isoformat(),
        }).eq("alert_id", alert_id).execute()
    except Exception:
        pass


# ─── Orchestration ───────────────────────────────────────────────
def process_notifications() -> Dict[str, Any]:
    """
    Topnivå: hent alle pending alerts, filtrér på terskel,
    send varsler i henhold til hver bruker sin notification_mode.
    """
    pending = get_pending_alerts()
    pending = filter_by_threshold(pending)

    # Grupper per profil + mode
    by_profile_mode: Dict[tuple, List[Dict[str, Any]]] = {}
    for a in pending:
        watch = a.get("tender_watches") or {}
        profile = watch.get("tender_profiles") or {}
        mode = watch.get("notification_mode") or profile.get("default_notification_mode") or "instant"
        profile_id = profile.get("profile_id")
        if not profile_id:
            continue
        by_profile_mode.setdefault((profile_id, mode), []).append(a)

    emails_sent = 0
    slack_sent = 0

    for (profile_id, mode), alerts in by_profile_mode.items():
        first = alerts[0]
        watch = first.get("tender_watches") or {}
        profile = watch.get("tender_profiles") or {}
        email = watch.get("notification_email") or profile.get("default_email")
        slack = watch.get("notification_slack_webhook") or profile.get("default_slack_webhook")

        if mode == "instant":
            # Én epost per alert
            for a in alerts:
                if email:
                    html = render_alert_email_html(a)
                    title = (a.get("tender_sources") or {}).get("title", "")[:80]
                    if send_email(email, f"[Builtly Radar] {title}", html):
                        emails_sent += 1
                        mark_notified(a["alert_id"])
                if slack:
                    if send_slack(slack, a):
                        slack_sent += 1
                        if not email:
                            mark_notified(a["alert_id"])
        else:
            # Digest-mode
            if email:
                html = render_digest_email_html(alerts, profile)
                subj = f"[Builtly Radar] {len(alerts)} nye anbud"
                if send_email(email, subj, html):
                    emails_sent += 1
                    for a in alerts:
                        mark_notified(a["alert_id"])

    return {
        "pending_count": len(pending),
        "emails_sent": emails_sent,
        "slack_sent": slack_sent,
    }


if __name__ == "__main__":
    result = process_notifications()
    print(json.dumps(result, indent=2, default=str))
