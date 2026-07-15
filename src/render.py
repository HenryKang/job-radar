"""Persist the archive (data/postings.json) and the human-readable postings.md."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

_MD_LIMIT = 400  # most-recent N rows shown in postings.md


def load_archive(path: str) -> list[dict]:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


def merge_archive(existing: list[dict], new_postings: list[dict]) -> list[dict]:
    by_id = {p["id"]: p for p in existing}
    for p in new_postings:
        by_id[p["id"]] = p
    return list(by_id.values())


def save_archive(path: str, archive: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ordered = sorted(archive, key=lambda p: p.get("date_found") or 0, reverse=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)


def _fmt_date(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _esc(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def _shown(p: dict) -> bool:
    """Only live postings that fit an undergrad (ok/unknown eligibility)."""
    if p.get("alive") is False:
        return False
    return (p.get("eligibility") or "unknown") in ("ok", "unknown")


def write_markdown(path: str, archive: list[dict]) -> None:
    shown = [p for p in archive if _shown(p)]
    rows = sorted(shown, key=lambda p: p.get("date_found") or 0, reverse=True)
    total_shown = len(rows)
    hidden = len(archive) - total_shown
    rows = rows[:_MD_LIMIT]
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# 📡 job-radar — tracked postings",
        "",
        f"_Last updated: {now} · {total_shown} live & eligible postings "
        f"(showing {len(rows)}); {hidden} hidden (dead links / PhD / grad / underclassmen)._",
        "",
        "| Found | Posted | Company | Role | Location | Season | Category | Fit | Apply |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in rows:
        loc = _esc(", ".join(p.get("locations", []))) or "—"
        apply = f"[apply]({p['url']})" if p.get("url") else "—"
        fit = (p.get("eligibility") or "unknown")
        lines.append(
            f"| {_fmt_date(p.get('date_found'))} "
            f"| {_fmt_date(p.get('date_posted'))} "
            f"| {_esc(p.get('company'))} "
            f"| {_esc(p.get('title'))} "
            f"| {loc} "
            f"| {_esc(p.get('season')) or '—'} "
            f"| {_esc(p.get('category')) or '—'} "
            f"| {fit} "
            f"| {apply} |"
        )
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
