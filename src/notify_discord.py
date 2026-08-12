"""Discord webhook alerts. One embed per posting, batched (Discord max 10/msg).

Env:
  DISCORD_WEBHOOK_URL       required to actually send
  DISCORD_MENTION_USER_ID   optional; if set, pings you so the alert isn't missed
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

import requests

_COLOR = 0x5865F2  # Discord blurple


def _fmt_date(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _embed(p: dict) -> dict:
    loc = ", ".join(p.get("locations", [])) or "—"
    # Prefer the actual posting date; fall back to when we scraped it
    date_ts = p.get("date_posted") or p.get("date_found")
    return {
        "title": f"{p.get('company', '')} — {p.get('title', '')}"[:250] or "New posting",
        "url": p.get("url") or None,
        "color": _COLOR,
        "fields": [
            {"name": "Location", "value": loc[:1000], "inline": True},
            {"name": "Posted", "value": _fmt_date(date_ts), "inline": True},
            {"name": "Season", "value": p.get("season") or "—", "inline": True},
            {"name": "Category", "value": p.get("category") or "—", "inline": True},
        ],
        "footer": {"text": p.get("source", "")},
    }


def _match(company_low: str, names) -> bool:
    for n in names or []:
        n = str(n).lower().strip()
        if n and re.search(rf"\b{re.escape(n)}\b", company_low):
            return True
    return False


def route(p: dict, cfg: dict | None) -> str:
    """Pick a channel: role_type + company -> quant/faang/other (+ _ng suffix for new grad)."""
    if not cfg:
        return "other"
    company = (p.get("company") or "").lower()
    is_ng = p.get("role_type") == "new_grad"
    suffix = "_ng" if is_ng else ""
    fallback = cfg.get("fallback_ng", "other_ng") if is_ng else cfg.get("fallback", "other")

    if _match(company, cfg.get("quant_companies")) or p.get("category") == "quant":
        return f"quant{suffix}"
    if _match(company, cfg.get("faang_companies")):
        return f"faang{suffix}"
    return fallback


def _post_batches(postings: list[dict], webhook: str, mention: str | None, label: str) -> bool:
    ok = True
    for i in range(0, len(postings), 10):
        batch = postings[i : i + 10]
        payload: dict = {"embeds": [_embed(p) for p in batch]}
        if mention:
            tag = f"{label} " if label else ""
            payload["content"] = f"<@{mention}> {len(batch)} new {tag}posting(s) 🚨"
            payload["allowed_mentions"] = {"users": [mention]}
        try:
            r = requests.post(webhook, json=payload, timeout=30)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            print(f"[error] discord send failed ({label} batch {i // 10}): {e}")
            ok = False
    return ok


def send(postings: list[dict], routing_cfg: dict | None = None,
         mention: str | None = None, default_env: str = "DISCORD_WEBHOOK_URL") -> bool:
    """Route postings to per-channel webhooks; fall back to DISCORD_WEBHOOK_URL.

    Never raises. Returns False if any configured webhook POST failed, so the
    caller keeps those postings unseen to retry.
    """
    mention = mention or os.environ.get("DISCORD_MENTION_USER_ID")
    if not postings:
        return True

    groups: dict[str, list] = defaultdict(list)
    for p in postings:
        groups[route(p, routing_cfg)].append(p)

    default_hook = os.environ.get(default_env)
    channels = (routing_cfg or {}).get("channels", {})
    ok = True
    for channel, items in groups.items():
        env_cfg = channels.get(channel, {}).get("webhook_env") or []
        # accept either a single string or a list
        envs = [env_cfg] if isinstance(env_cfg, str) else list(env_cfg)
        hooks = [os.environ.get(e) for e in envs if os.environ.get(e)]
        if not hooks:
            hooks = [default_hook] if default_hook else []
        if not hooks:
            print(f"[warn] no webhook for channel '{channel}' and no {default_env}; "
                  f"skipping {len(items)} posting(s)")
            continue
        for hook in hooks:
            ok = _post_batches(items, hook, mention, channel) and ok
    print(f"[discord] routed {len(postings)} posting(s) -> "
          f"{ {c: len(v) for c, v in groups.items()} }; ok={ok}")
    return ok


def send_discord(postings: list[dict], webhook: str | None = None,
                 mention: str | None = None) -> bool:
    """Single-webhook send (used by --test)."""
    webhook = webhook or os.environ.get("DISCORD_WEBHOOK_URL")
    mention = mention or os.environ.get("DISCORD_MENTION_USER_ID")
    if not postings:
        return True
    if not webhook:
        print("[warn] DISCORD_WEBHOOK_URL not set — skipping alerts")
        return True
    ok = _post_batches(postings, webhook, mention, "")
    print(f"[discord] {'sent' if ok else 'attempted'} {len(postings)} alert(s); ok={ok}")
    return ok


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
