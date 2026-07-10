"""intern-radar orchestrator: fetch -> filter -> dedupe -> archive -> alert.

Usage:
  python src/main.py            # normal run (alerts on new postings)
  python src/main.py --dry-run  # fetch + filter + report only; no writes, no alerts
  python src/main.py --seed     # write archive/state but send NO alerts (first-run seed)

First run (no data/seen.json) auto-seeds silently so you don't get flooded.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eligibility  # noqa: E402
from dedupe import dedupe_within_run, load_seen, save_seen, split_new  # noqa: E402
from fetch_aggregators import fetch_aggregators  # noqa: E402
from fetch_ats import fetch_ats  # noqa: E402
from filters import classify_and_filter  # noqa: E402
from notify_discord import send_discord  # noqa: E402
from render import load_archive, merge_archive, save_archive, write_markdown  # noqa: E402


def run_eligibility(postings: list[dict], cfg: dict) -> None:
    """Fetch each posting's page and annotate alive/eligibility in place."""
    if not cfg.get("enabled", True) or not postings:
        for p in postings:
            p["alive"], p["eligibility"], p["elig_reason"] = True, "unknown", "check disabled"
        return
    timeout = cfg.get("timeout", 15)
    to_check = postings[: cfg.get("max_checks", 150)]

    def _one(p):
        return p, eligibility.check(p["url"], p["title"], timeout=timeout)

    with ThreadPoolExecutor(max_workers=10) as ex:
        for p, res in ex.map(_one, to_check):
            p["alive"], p["eligibility"], p["elig_reason"] = (
                res["alive"], res["eligibility"], res["reason"],
            )
    for p in postings[cfg.get("max_checks", 150):]:  # over the cap -> keep, unchecked
        p["alive"], p["eligibility"], p["elig_reason"] = True, "unknown", "over max_checks"


def is_alertable(p: dict, cfg: dict) -> bool:
    if not p.get("alive", True):
        return False
    elig = p.get("eligibility") or "unknown"
    if elig == "ok":
        return True
    if elig == "unknown":
        return cfg.get("keep_unknown", True)
    return False  # phd_only / grad_only / underclass_only

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")
ARCHIVE_PATH = os.path.join(DATA_DIR, "postings.json")
SEEN_PATH = os.path.join(DATA_DIR, "seen.json")
MD_PATH = os.path.join(ROOT, "postings.md")


def _load_yaml(name: str) -> dict:
    with open(os.path.join(CONFIG_DIR, name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only; no writes/alerts")
    ap.add_argument("--seed", action="store_true", help="write state but send no alerts")
    args = ap.parse_args()

    sources = _load_yaml("sources.yaml")
    filters_cfg = _load_yaml("filters.yaml")

    # 1) Fetch everything.
    raw = fetch_aggregators(sources.get("aggregators", []))
    raw += fetch_ats(sources.get("ats_targets", []))

    # 2) Collapse cross-source duplicates, then apply relevance filters.
    candidates = dedupe_within_run(raw)
    relevant = classify_and_filter(candidates, filters_cfg)

    # 3) What's new vs. what we've already seen?
    first_run = not os.path.exists(SEEN_PATH)
    seen = load_seen(SEEN_PATH)
    new = split_new(relevant, seen)

    # 3b) Eligibility + liveness check on the new postings (drops dead links and
    #     PhD/master's/underclassmen-only roles; keeps undergrad + unknown).
    elig_cfg = filters_cfg.get("eligibility", {})
    run_eligibility(new, elig_cfg)
    alertable = [p for p in new if is_alertable(p, elig_cfg)]
    alert_ids = {id(p) for p in alertable}
    suppressed = [p for p in new if id(p) not in alert_ids]
    from collections import Counter
    reasons = Counter(("dead" if not p.get("alive", True) else p["eligibility"]) for p in suppressed)

    print(
        f"\n== summary ==\n"
        f"fetched(raw)={len(raw)}  candidates={len(candidates)}  relevant={len(relevant)}  "
        f"new={len(new)}  alertable={len(alertable)}  "
        f"suppressed={len(suppressed)}{' ' + str(dict(reasons)) if suppressed else ''}  "
        f"first_run={first_run}"
    )
    for p in alertable[:25]:
        print(f"  + {p['company']} | {p['title']} | {', '.join(p['locations']) or '—'}")
    if len(alertable) > 25:
        print(f"  ... and {len(alertable) - 25} more")

    if args.dry_run:
        print("\n[dry-run] no files written, no alerts sent.")
        return 0

    # 4) Persist archive + human-readable table + seen state.
    now = int(time.time())
    for p in new:
        p["date_found"] = now
    archive = merge_archive(load_archive(ARCHIVE_PATH), new)
    save_archive(ARCHIVE_PATH, archive)
    write_markdown(MD_PATH, archive)
    save_seen(SEEN_PATH, seen | {p["id"] for p in relevant})

    # 5) Alert — but never on the seeding run.
    if first_run or args.seed:
        print(f"[seed] recorded {len(alertable)} eligible postings silently (no alerts).")
    else:
        send_discord(alertable)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
