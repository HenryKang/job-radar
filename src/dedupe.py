"""Seen-state persistence + new-posting detection."""
from __future__ import annotations

import json
import os


def load_seen(path: str) -> set[str]:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(path: str, ids: set[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, indent=0)


def dedupe_within_run(postings: list[dict]) -> list[dict]:
    """Collapse the same posting arriving from multiple sources (first wins)."""
    uniq: dict[str, dict] = {}
    for p in postings:
        uniq.setdefault(p["id"], p)
    return list(uniq.values())


def split_new(postings: list[dict], seen: set[str]) -> list[dict]:
    return [p for p in postings if p["id"] not in seen]
