"""Builtly keep-alive utility — holder Streamlit WebSocket-koblinger levende.

Problemet:
    Render og andre proxy-tjenester dropper idle TCP-koblinger etter typisk
    60 sekunder. Streamlit sender ikke WebSocket ping frames
    (https://github.com/streamlit/streamlit/issues/8660), så når brukeren
    ikke samhandler med siden på en stund går WebSocket-koblingen tapt og
    session state forsvinner. Brukeren havner dermed tilbake i start-
    tilstand (f.eks. project setup).

Løsningen (denne modulen):
    Injiserer en JavaScript-snutt som:
    1. Pinger `/_stcore/health` hvert 20. sekund (3x margin til 60s timeout)
    2. Pauser når tab er skjult og pinger umiddelbart ved visibility-retur
    3. Detekterer når flere pings feiler på rad og reloader siden automatisk
       for å restaurere session før brukeren ser en tom app
    4. Logger diagnostikk til browser-console slik at Pål kan debug ved behov

MERK: Keep-alive løser ikke server-restart (deploys, OOM). For det trengs
session-persistering til disk via SSOT-mønsteret i Mulighetsstudie.py.

Bruk (samme mønster på alle Builtly-sider):
    import streamlit as st
    from builtly.keepalive import inject_keepalive

    st.set_page_config(...)
    inject_keepalive()   # kall én gang per side, direkte etter page_config

    # resten av sidekoden

Modulen er idempotent — flere kall på samme side har ingen effekt ut over
det første.
"""

from __future__ import annotations

# Standard intervall: 20 sekunder gir 3x margin under 60s proxy-timeouts.
# Tidligere 30s var for nærme grensen — én tapt ping betydde disconnect.
DEFAULT_PING_INTERVAL_MS = 20_000

# Antall påfølgende ping-feil før vi antar at websocket er død og reloader.
# 3 feil × 20s = 60s uten hell → pålitelig disconnect-signal.
DEFAULT_MAX_FAILURES_BEFORE_RELOAD = 3


def _build_keepalive_script(
    interval_ms: int,
    endpoint: str,
    max_failures: int,
    enable_auto_reload: bool,
    debug: bool,
) -> str:
    """Bygger JavaScript-snutten som injiseres på siden.

    Vi bruker `window.parent` siden Streamlit-komponenter kjører i iframe.
    Flagget `__builtlyKeepAlive` på parent-vinduet sikrer at bare én
    setInterval registreres uansett hvor mange ganger denne funksjonen
    kalles (viktig for Streamlit-rerenders som re-initialiserer komponenter).

    Feilhåndtering er stille i prod, verbose hvis debug=True.
    """
    debug_js = "true" if debug else "false"
    auto_reload_js = "true" if enable_auto_reload else "false"
    return f"""
<script>
(function(){{
  try {{
    var parentWin = window.parent;
    if (parentWin.__builtlyKeepAlive) return;
    parentWin.__builtlyKeepAlive = true;

    var state = {{
      consecutive_failures: 0,
      last_success_ms: Date.now(),
      last_ping_ms: 0,
      total_pings: 0,
      total_failures: 0,
    }};
    parentWin.__builtlyKeepAliveState = state;

    var MAX_FAILURES = {max_failures};
    var DEBUG = {debug_js};
    var AUTO_RELOAD = {auto_reload_js};
    var INTERVAL_MS = {interval_ms};

    function log(msg) {{
      if (DEBUG) {{
        try {{ console.log('[BuiltlyKeepAlive] ' + msg); }} catch(e) {{}}
      }}
    }}

    function ping() {{
      state.last_ping_ms = Date.now();
      state.total_pings += 1;
      try {{
        fetch({endpoint!r}, {{
          method: 'GET',
          cache: 'no-store',
          credentials: 'same-origin',
        }})
        .then(function(resp) {{
          if (resp.ok) {{
            state.consecutive_failures = 0;
            state.last_success_ms = Date.now();
            log('ping OK (total=' + state.total_pings + ')');
          }} else {{
            handleFailure('HTTP ' + resp.status);
          }}
        }})
        .catch(function(err) {{
          handleFailure(err && err.message ? err.message : 'network');
        }});
      }} catch (e) {{
        handleFailure('exception: ' + e.message);
      }}
    }}

    function handleFailure(reason) {{
      state.consecutive_failures += 1;
      state.total_failures += 1;
      log('ping FAIL: ' + reason + ' (streak=' + state.consecutive_failures +
          ', total_fail=' + state.total_failures + ')');

      if (state.consecutive_failures >= MAX_FAILURES && AUTO_RELOAD) {{
        log('WebSocket antatt død — reloader siden for å restaurere session');
        try {{
          // Bruk location.reload fra parent-window for å reload hele siden,
          // ikke bare iframe-komponenten.
          parentWin.location.reload();
        }} catch (e) {{
          try {{ window.location.reload(); }} catch (e2) {{}}
        }}
      }}
    }}

    // Hoved-ping-intervall
    setInterval(ping, INTERVAL_MS);
    log('registrert med ' + INTERVAL_MS + 'ms intervall, max_failures=' + MAX_FAILURES);

    // Ping umiddelbart når tab blir synlig igjen — bruker kan ha vært borte
    // lenge, og koblingen kan allerede være på grensen.
    try {{
      parentWin.document.addEventListener('visibilitychange', function() {{
        if (!parentWin.document.hidden) {{
          log('tab synlig igjen, pinger umiddelbart');
          ping();
        }}
      }});
    }} catch (e) {{
      log('kunne ikke registrere visibility-listener: ' + e.message);
    }}

    // Første ping etter kort delay for å verifisere at endpoint fungerer
    setTimeout(ping, 2000);

  }} catch (e) {{
    /* Hvis parent-vinduet ikke er tilgjengelig (usannsynlig på claude.ai
       eller vanlig browser), bare fortsett — Streamlit vil fungere uten
       keep-alive, men kan miste session ved lange idle-perioder. */
    try {{ console.log('[BuiltlyKeepAlive] init error: ' + e.message); }} catch (e2) {{}}
  }}
}})();
</script>
"""


def inject_keepalive(
    interval_ms: int = DEFAULT_PING_INTERVAL_MS,
    endpoint: str = "/_stcore/health",
    max_failures: int = DEFAULT_MAX_FAILURES_BEFORE_RELOAD,
    enable_auto_reload: bool = True,
    debug: bool = False,
) -> None:
    """Injiser keep-alive-skript på gjeldende Streamlit-side.

    Skal kalles én gang per side, typisk rett etter `st.set_page_config(...)`.
    Flere kall på samme side er trygge, men unødvendige.

    Args:
        interval_ms: Hvor ofte keep-alive skal sendes, i millisekunder.
            Default 20 000 (20 sekunder). 3x margin under typisk 60s
            proxy-timeout.
        endpoint: URL som skal pinges. Default `/_stcore/health` —
            Streamlits innebygde health-check.
        max_failures: Antall påfølgende ping-feil før siden reloades
            automatisk for å restaurere session. Default 3 (= ~60s
            uten hell). Sett til 0 for å deaktivere auto-reload.
        enable_auto_reload: Hvis True (default), reload siden automatisk
            når max_failures nås. Hvis False, pings bare logges og
            koblingen forsøkes holdt i live.
        debug: Hvis True, logg alle ping-events til browser-console.
            Nyttig for debugging av hvorfor session dør.

    Raises:
        Ingenting direkte. Om Streamlit ikke er installert eller siden
        ikke er i en Streamlit-kontekst, logges bare en advarsel og
        funksjonen returnerer stille.
    """
    try:
        from streamlit.components.v1 import html as _components_html
    except Exception:
        return

    # Normaliser argumenter defensivt
    try:
        interval_ms_int = int(interval_ms)
        if interval_ms_int < 5_000:
            interval_ms_int = 5_000  # minimum 5s for å unngå server-stampede
    except Exception:
        interval_ms_int = DEFAULT_PING_INTERVAL_MS

    try:
        max_failures_int = max(0, int(max_failures))
    except Exception:
        max_failures_int = DEFAULT_MAX_FAILURES_BEFORE_RELOAD

    # Hvis auto_reload er slått av, ignorer max_failures i JS
    if not enable_auto_reload:
        max_failures_int = 0

    script = _build_keepalive_script(
        interval_ms_int,
        str(endpoint),
        max_failures_int if enable_auto_reload else 9999,
        enable_auto_reload,
        bool(debug),
    )

    try:
        _components_html(script, height=0)
    except Exception:
        return


__all__ = ["inject_keepalive", "DEFAULT_PING_INTERVAL_MS",
           "DEFAULT_MAX_FAILURES_BEFORE_RELOAD"]
