#!/usr/bin/env python3
"""Diff Meta internship postings against the saved baseline.

metacareers.com returns HTTP 400 to non-browser clients, so the crawler cannot
poll it. A browser-driven check pipes the scraped postings in as JSON:

    echo '[{"id":"...","title":"...","locations":[...],"url":"..."}]' \
        | python3 scripts/meta_diff.py            # report new, update baseline
    ... | python3 scripts/meta_diff.py --dry-run  # report only
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / "data" / "meta_watch.json"


def main() -> int:
    dry = "--dry-run" in sys.argv
    scraped = json.load(sys.stdin)
    if not isinstance(scraped, list) or not scraped:
        print("no postings on stdin - refusing to overwrite the baseline")
        return 1

    state = json.loads(STATE.read_text())
    known = {p["id"] for p in state.get("postings", [])}
    incoming = {p["id"] for p in scraped}

    new = [p for p in scraped if p["id"] not in known]
    gone = [p for p in state.get("postings", []) if p["id"] not in incoming]

    for p in new:
        locs = ", ".join(p.get("locations", [])) or "-"
        print(f"NEW      {p['title']}  [{locs}]")
        print(f"         {p['url']}")
    for p in gone:
        print(f"CLOSED   {p['title']}")
    if not new and not gone:
        print(f"no change ({len(scraped)} postings)")

    if not dry:
        state["postings"] = scraped
        state["last_checked"] = int(time.time())
        state["last_checked_human"] = time.strftime("%Y-%m-%d %H:%M")
        STATE.write_text(json.dumps(state, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
