"""Discord webhook alerts. One embed per posting, batched (Discord max 10/msg).

Env:
  DISCORD_WEBHOOK_URL       required to actually send
  DISCORD_MENTION_USER_ID   optional; if set, pings you so the alert isn't missed
"""
from __future__ import annotations

import os
import sys

import requests

_COLOR = 0x5865F2  # Discord blurple


def _embed(p: dict) -> dict:
    loc = ", ".join(p.get("locations", [])) or "—"
    return {
        "title": f"{p.get('company', '')} — {p.get('title', '')}"[:250] or "New posting",
        "url": p.get("url") or None,
        "color": _COLOR,
        "fields": [
            {"name": "Location", "value": loc[:1000], "inline": True},
            {"name": "Season", "value": p.get("season") or "—", "inline": True},
            {"name": "Category", "value": p.get("category") or "—", "inline": True},
        ],
        "footer": {"text": p.get("source", "")},
    }


def send_discord(postings: list[dict], webhook: str | None = None,
                 mention: str | None = None) -> None:
    webhook = webhook or os.environ.get("DISCORD_WEBHOOK_URL")
    mention = mention or os.environ.get("DISCORD_MENTION_USER_ID")
    if not postings:
        return
    if not webhook:
        print("[warn] DISCORD_WEBHOOK_URL not set — skipping alerts")
        return

    for i in range(0, len(postings), 10):
        batch = postings[i : i + 10]
        payload: dict = {"embeds": [_embed(p) for p in batch]}
        if mention:
            payload["content"] = f"<@{mention}> {len(batch)} new internship posting(s) 🚨"
            payload["allowed_mentions"] = {"users": [mention]}
        r = requests.post(webhook, json=payload, timeout=30)
        r.raise_for_status()
    print(f"[discord] sent {len(postings)} alert(s)")


def _test() -> None:
    sample = {
        "company": "Example Corp",
        "title": "Software Engineer Intern (Summer 2027)",
        "locations": ["New York, NY", "Remote"],
        "url": "https://example.com/apply",
        "season": "Summer",
        "category": "swe",
        "source": "test",
    }
    send_discord([sample])


if __name__ == "__main__":
    if "--test" in sys.argv:
        _test()
    else:
        print("usage: python src/notify_discord.py --test")
