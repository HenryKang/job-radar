"""job-radar orchestrator: fetch -> filter -> dedupe -> archive -> alert.

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
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eligibility  # noqa: E402
from dedupe import dedupe_within_run, load_seen, save_seen, split_new  # noqa: E402
from fetch_aggregators import fetch_aggregators  # noqa: E402
from fetch_ats import fetch_ats  # noqa: E402
from filters import classify_and_filter  # noqa: E402
import notify_discord  # noqa: E402
import pending as pending_mod  # noqa: E402
from render import load_archive, merge_archive, save_archive, write_markdown  # noqa: E402


def group_postings(postings: list[dict]) -> list[dict]:
    """Merge same (company, title) postings into one embed with combined locations.

    Handles two duplicate sources:
    - Same job posted at multiple cities (different ATS job IDs, same title)
    - Same job in both aggregator and direct ATS (different URLs, same title)

    Location format: "New York, NY +2" (first location + count of rest).
    ATS source wins over aggregator for URL and date_posted.
    """
    groups: dict[tuple, dict] = OrderedDict()

    for p in postings:
        key = (p["company"].lower(), p["title"].lower().strip())
        if key not in groups:
            canon = dict(p)
            canon["_locs"] = list(p.get("locations", []))
            groups[key] = canon
        else:
            canon = groups[key]
            canon["_locs"].extend(p.get("locations", []))
            p_is_ats = p["source"].startswith("ats:")
            c_is_ats = canon["source"].startswith("ats:")
            if p_is_ats and not c_is_ats:
                # Upgrade to ATS source for better URL and date
                canon["source"] = p["source"]
                canon["url"] = p["url"]
                if p.get("date_posted"):
                    canon["date_posted"] = p["date_posted"]
            elif p.get("date_posted") and (
                not canon.get("date_posted") or p["date_posted"] < canon["date_posted"]
            ):
                canon["date_posted"] = p["date_posted"]

    result = []
    for canon in groups.values():
        locs = list(dict.fromkeys(canon.pop("_locs")))  # dedup, preserve order
        if len(locs) > 1:
            canon["locations"] = [f"{locs[0]} +{len(locs) - 1}"]
        else:
            canon["locations"] = locs
        result.append(canon)
    return result


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
            if res.get("date_posted"):  # more accurate source (e.g. Workday)
                p["date_posted"] = res["date_posted"]
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
PENDING_PATH = os.path.join(DATA_DIR, "pending.json")
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
    routing_path = os.path.join(CONFIG_DIR, "routing.yaml")
    routing_cfg = _load_yaml("routing.yaml") if os.path.exists(routing_path) else None

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

    # 3b) Re-check the pending watchlist first — aggregator-sourced dead links
    #     that may have become live since we last saw them.
    now = int(time.time())
    elig_cfg = filters_cfg.get("eligibility", {})
    timeout = elig_cfg.get("timeout", 15)
    pending_items = pending_mod.load_pending(PENDING_PATH)
    still_pending, newly_live = pending_mod.recheck_pending(
        pending_items, eligibility.check, now, timeout
    )
    if not args.dry_run:
        pending_mod.save_pending(PENDING_PATH, still_pending)
    if newly_live:
        print(f"[pending] {len(newly_live)} posting(s) came alive — adding to alerts")

    # 3c) Eligibility + liveness check on brand-new postings.
    run_eligibility(new, elig_cfg)
    alertable = [p for p in new if is_alertable(p, elig_cfg)]

    # Add dead aggregator postings (not yet in seen) to the watchlist for retry.
    dead_from_agg = [
        p for p in new
        if not p.get("alive", True)
        and not p["source"].startswith("ats:")
        and p["dedup_key"] not in seen
    ]
    if dead_from_agg and not args.dry_run:
        for p in dead_from_agg:
            pending_mod.add_to_pending(still_pending, p, now)
        pending_mod.save_pending(PENDING_PATH, still_pending)
        print(f"[pending] added {len(dead_from_agg)} dead aggregator posting(s) to watchlist")

    # Combine normally-alertable with any that just came alive from the watchlist.
    alertable = alertable + [p for p in newly_live if is_alertable(p, elig_cfg)]

    alert_ids = {id(p) for p in alertable}
    suppressed = [p for p in new if id(p) not in alert_ids]
    from collections import Counter
    reasons = Counter(("dead" if not p.get("alive", True) else p.get("eligibility","")) for p in suppressed)

    # Group same (company, title) across sources / locations into one embed each.
    grouped = group_postings(alertable)

    print(
        f"\n== summary ==\n"
        f"fetched(raw)={len(raw)}  candidates={len(candidates)}  relevant={len(relevant)}  "
        f"new={len(new)}  alertable={len(alertable)}  grouped={len(grouped)}  "
        f"suppressed={len(suppressed)}{' ' + str(dict(reasons)) if suppressed else ''}  "
        f"pending_watched={len(still_pending)}  first_run={first_run}"
    )
    for p in grouped[:25]:
        print(f"  + {p['company']} | {p['title']} | {', '.join(p['locations']) or '—'}")
    if len(grouped) > 25:
        print(f"  ... and {len(grouped) - 25} more")

    if args.dry_run:
        print("\n[dry-run] no files written, no alerts sent.")
        return 0

    # 4) Persist archive + human-readable table.
    for p in new:
        p["date_found"] = now
    archive = merge_archive(load_archive(ARCHIVE_PATH), new)
    save_archive(ARCHIVE_PATH, archive)
    write_markdown(MD_PATH, archive)

    # 5) Alert — but never on the seeding run.
    if first_run or args.seed:
        print(f"[seed] recorded {len(alertable)} eligible postings silently ({len(grouped)} grouped; no alerts).")
        alert_ok = True
    else:
        alert_ok = notify_discord.send(grouped, routing_cfg)

    # 6) Update seen. Dead postings are NOT marked seen — they stay absent so the
    #    pending watchlist can retry them. PhD/grad/underclass postings ARE marked seen
    #    so they never re-alert. Delivery failures keep alertable ids unseen to retry.
    newly_seen = {p["dedup_key"] for p in relevant if p.get("alive", True)}
    # Also mark newly-live pending items as seen (they just alerted)
    newly_seen |= {p["dedup_key"] for p in newly_live}
    if not alert_ok:
        newly_seen -= {p["dedup_key"] for p in alertable}
        print(f"[warn] delivery failed; {len(alertable)} posting(s) kept unseen to retry.")
    save_seen(SEEN_PATH, seen | newly_seen)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
