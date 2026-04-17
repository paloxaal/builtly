# -*- coding: utf-8 -*-
"""
Builtly | Tender Radar — Worker
─────────────────────────────────────────────────────────────────
Kombinerer poll + screen + notify i én Render Cron Job.

Render Cron-oppsett:
    Command: python tender_radar_worker.py
    Schedule: */30 * * * *        (hvert 30. minutt)

Alternativt kan hver av de tre stegene kjøres separat:
    python tender_radar_poller.py      # bare poll
    python tender_radar_screener.py    # bare AI-screening
    python tender_radar_notifier.py    # bare notifikasjoner
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict


def run_worker(sources=None) -> Dict[str, Any]:
    t0 = time.time()
    result: Dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "steps": {},
    }

    # 1. Poll
    try:
        from tender_radar_poller import run_full_poll
        poll_result = run_full_poll(sources=sources or ["doffin"])
        result["steps"]["poll"] = poll_result
        new_source_ids = []
        for src_summary in (poll_result.get("per_source") or {}).values():
            new_source_ids.extend(src_summary.get("new_source_row_ids") or [])
    except Exception as e:
        result["steps"]["poll"] = {"error": str(e)}
        new_source_ids = []

    # 2. Screen (kun nye kunngjøringer)
    try:
        from tender_radar_screener import screen_new_sources_against_all_watches
        screen_result = screen_new_sources_against_all_watches(
            source_row_ids=new_source_ids if new_source_ids else None,
        )
        result["steps"]["screen"] = screen_result
    except Exception as e:
        result["steps"]["screen"] = {"error": str(e)}

    # 3. Notify
    try:
        from tender_radar_notifier import process_notifications
        notify_result = process_notifications()
        result["steps"]["notify"] = notify_result
    except Exception as e:
        result["steps"]["notify"] = {"error": str(e)}

    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    result["duration_sec"] = round(time.time() - t0, 1)
    return result


if __name__ == "__main__":
    # CLI: --source doffin,mercell
    sources = None
    for i, arg in enumerate(sys.argv):
        if arg == "--source" and i + 1 < len(sys.argv):
            sources = [s.strip() for s in sys.argv[i + 1].split(",") if s.strip()]

    result = run_worker(sources=sources)
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    # Ikke-null exit-kode hvis noe feilet — lar Render flagge det
    any_error = any(
        isinstance(step, dict) and step.get("error")
        for step in (result.get("steps") or {}).values()
    )
    sys.exit(1 if any_error else 0)
