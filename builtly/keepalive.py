"""Builtly keep-alive utility — holder Streamlit WebSocket-koblinger levende.

Problemet:
    Render og andre proxy-tjenester dropper idle TCP-koblinger etter typisk
    60 sekunder. Streamlit sender ikke WebSocket ping frames
    (https://github.com/streamlit/streamlit/issues/8660), så når brukeren
    ikke samhandler med siden på en stund går WebSocket-koblingen tapt og
    session state forsvinner. Brukeren havner dermed tilbake i start-
    tilstand (f.eks. project setup).

Løsningen:
    Injiserer en liten JavaScript-snutt som pinger `/_stcore/health` hvert
    30. sekund. Denne endepunkten eksisterer allerede i Streamlit og er
    ekstremt lett (returnerer ~270 bytes). Pinger forhindrer at proxy-en
    anser koblingen som idle.

Bruk (samme mønster på alle Builtly-sider):
    import streamlit as st
    from builtly.keepalive import inject_keepalive

    st.set_page_config(...)
    inject_keepalive()   # kall én gang per side, direkte etter page_config

    # resten av sidekoden

Modulen er idempotent — flere kall på samme side har ingen effekt ut over
det første. JavaScript-siden bruker et window-flagg (`__builtlyKeepAlive`)
for å unngå dobbel-registrering ved Streamlit-rerenders.
"""

from __future__ import annotations

# Standard intervall for ping i millisekunder. 30 sekunder er trygt under
# de fleste proxy-timeouts (vanligvis 60s) og gir moderat server-last
# (2 pings/min per åpne browser-fane).
DEFAULT_PING_INTERVAL_MS = 30_000


def _build_keepalive_script(interval_ms: int, endpoint: str) -> str:
    """Bygger JavaScript-snutten som injiseres på siden.

    Vi bruker `window.parent` siden Streamlit-komponenter kjører i iframe.
    Flagget `__builtlyKeepAlive` på parent-vinduet sikrer at bare én
    setInterval registreres uansett hvor mange ganger denne funksjonen
    kalles (viktig for Streamlit-rerenders som re-initialiserer komponenter).

    Feilhåndtering er stille: nettverksfeil eller manglende parent-
    tilgang skal ikke krasje appen eller logge støy til brukeren.
    """
    return f"""
<script>
(function(){{
  try {{
    var parentWin = window.parent;
    if (parentWin.__builtlyKeepAlive) return;
    parentWin.__builtlyKeepAlive = true;

    // Pinger health-endpoint med jevne mellomrom for å holde WebSocket
    // og underliggende TCP-kobling levende gjennom proxy-er (Render, CDN, etc).
    setInterval(function(){{
      try {{
        fetch({endpoint!r}, {{
          method: 'GET',
          cache: 'no-store',
          credentials: 'same-origin',
        }}).catch(function(){{ /* stille nettverksfeil */ }});
      }} catch (e) {{ /* stille */ }}
    }}, {interval_ms});
  }} catch (e) {{
    /* Hvis parent-vinduet ikke er tilgjengelig (usannsynlig på claude.ai
       eller vanlig browser), bare fortsett — Streamlit vil fungere uten
       keep-alive, men kan miste session ved lange idle-perioder. */
  }}
}})();
</script>
"""


def inject_keepalive(
    interval_ms: int = DEFAULT_PING_INTERVAL_MS,
    endpoint: str = "/_stcore/health",
) -> None:
    """Injiser keep-alive-skript på gjeldende Streamlit-side.

    Skal kalles én gang per side, typisk rett etter `st.set_page_config(...)`.
    Flere kall på samme side er trygge, men unødvendige.

    Args:
        interval_ms: Hvor ofte keep-alive skal sendes, i millisekunder.
            Default 30 000 (30 sekunder). Bør være under proxy-timeout
            (typisk 60 sek på Render).
        endpoint: URL som skal pinges. Default `/_stcore/health` —
            Streamlits innebygde health-check. Kan overstyres om man kjører
            bak en annen path-struktur, men for vanlige oppsett skal
            default-en virke.

    Raises:
        Ingenting direkte. Om Streamlit ikke er installert eller siden
        ikke er i en Streamlit-kontekst, logges bare en advarsel og
        funksjonen returnerer stille. Dette er viktig fordi modulen
        importeres av alle sider — en feil her skal ikke krasje appen.
    """
    try:
        from streamlit.components.v1 import html as _components_html
    except Exception:
        # Kjører ikke i Streamlit-kontekst — ingenting å injisere.
        return

    # Normaliser argumenter defensivt
    try:
        interval_ms_int = int(interval_ms)
        if interval_ms_int < 5_000:
            interval_ms_int = 5_000  # minimum 5s for å unngå server-stampede
    except Exception:
        interval_ms_int = DEFAULT_PING_INTERVAL_MS

    script = _build_keepalive_script(interval_ms_int, str(endpoint))

    try:
        _components_html(script, height=0)
    except Exception:
        # components.html kan feile i enkelte uvanlige miljøer (f.eks.
        # under pytest uten Streamlit-server). Det er greit — sesjoner
        # fungerer uten keep-alive, bare med lavere levetid.
        return


__all__ = ["inject_keepalive", "DEFAULT_PING_INTERVAL_MS"]
