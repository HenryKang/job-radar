"""Pending-watchlist: postings that failed liveness but may open for applications later.

Used for aggregator-sourced links where "apply not open yet" looks identical to
"job expired" (both return a dead/redirect page). The ATS path is excluded because
Greenhouse/Lever/Ashby 404 = genuinely removed.

Flow:
  main.py adds a posting to pending when:
    - source is aggregator (not ats:*)
    - alive=False after eligibility check
    - posting is NOT already in seen (first discovery)

  On each run, pending items are re-checked. When alive=True the posting is
  returned as newly-alertable and its dedup_key is added to seen normally.
  Items older than PENDING_TTL_DAYS are silently dropped.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

PENDING_TTL_DAYS = 21
PENDING_TTL_SECS = PENDING_TTL_DAYS * 86400


def load_pending(path: str) -> list[dict]:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_pending(path: str, items: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)


def add_to_pending(existing: list[dict], posting: dict, now: int) -> list[dict]:
    """Add a dead posting to the watchlist if not already tracked."""
    known_keys = {e["dedup_key"] for e in existing}
    if posting["dedup_key"] not in known_keys:
        existing.append({
            "dedup_key": posting["dedup_key"],
            "id": posting["id"],
            "company": posting["company"],
            "title": posting["title"],
            "url": posting["url"],
            "role_type": posting.get("role_type", "intern"),
            "source": posting["source"],
            "season": posting.get("season", ""),
            "category": posting.get("category", ""),
            "locations": posting.get("locations", []),
            "date_posted": posting.get("date_posted"),
            "date_discovered": now,
            "last_checked": now,
            "fail_reason": posting.get("elig_reason", ""),
        })
    return existing


def recheck_pending(items: list[dict], check_fn, now: int, timeout: int = 15) -> tuple[list[dict], list[dict]]:
    """Re-check all pending items. Returns (still_pending, newly_live).

    `check_fn` is eligibility.check(url, title, timeout).
    Items older than PENDING_TTL_DAYS are silently expired.
    """
    still_pending: list[dict] = []
    newly_live: list[dict] = []

    for item in items:
        age = now - item.get("date_discovered", now)
        if age > PENDING_TTL_SECS:
            disc = datetime.fromtimestamp(item["date_discovered"], tz=timezone.utc).strftime("%Y-%m-%d")
            print(f"[pending] expired ({PENDING_TTL_DAYS}d) {item['company']} | {item['title']} (discovered {disc})")
            continue

        result = check_fn(item["url"], item["title"], timeout)
        item["last_checked"] = now

        if result.get("alive"):
            print(f"[pending] NOW LIVE: {item['company']} | {item['title']}")
            # Reconstruct a minimal posting dict for the alert pipeline
            p = dict(item)
            p["alive"] = True
            p["eligibility"] = result.get("eligibility", "unknown")
            p["elig_reason"] = result.get("reason", "")
            if result.get("date_posted"):
                p["date_posted"] = result["date_posted"]
            newly_live.append(p)
        else:
            item["fail_reason"] = result.get("reason", "")
            still_pending.append(item)

    return still_pending, newly_live
