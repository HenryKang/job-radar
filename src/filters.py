"""Relevance filtering: decide which normalized postings are worth alerting on.

Bias: missing a real posting is worse than an extra alert, so ambiguous cases
(unknown location, missing season) are KEPT.
"""
from __future__ import annotations


def _category_of(title: str, cfg: dict) -> str | None:
    t = title.lower()
    for cat, kws in cfg["category_keywords"].items():
        if any(kw in t for kw in kws):
            return cat
    return None


def _looks_us(locations, cfg: dict) -> bool:
    loc_cfg = cfg["locations"]
    if not locations:
        return True  # unknown -> keep
    us_markers = [m.lower() for m in loc_cfg.get("us_markers", [])]
    state_markers = [m.lower() for m in loc_cfg.get("us_state_markers", [])]
    non_us = [m.lower() for m in loc_cfg.get("non_us_markers", [])]

    any_us = False
    any_non_us = False
    for loc in locations:
        l = loc.lower()
        if "remote" in l and loc_cfg.get("allow_remote", True):
            any_us = True
        if any(m in l for m in us_markers) or any(m in l for m in state_markers):
            any_us = True
        if any(m in l for m in non_us):
            any_non_us = True
    if any_us:
        return True
    if any_non_us:
        return False
    return True  # ambiguous -> keep


def _season_ok_aggregator(season: str, cfg: dict) -> bool:
    if cfg.get("include_off_season"):
        return True
    if not season:
        return True  # unknown -> keep
    return season.strip().lower() in [s.lower() for s in cfg["seasons"]]


def _season_ok_ats(title: str, cfg: dict) -> bool:
    t = title.lower()
    target = str(cfg.get("target_year", "")).lower()
    if target and target in t:
        return True
    for old in cfg.get("stale_years", []):
        if str(old) in t:
            return False  # names an old cycle, not the target year
    return True  # no year mentioned -> keep


def classify_and_filter(postings: list[dict], cfg: dict) -> list[dict]:
    """Return the kept postings, each with `category` populated."""
    include_kw = [k.lower() for k in cfg.get("title_include_any", [])]
    exclude_kw = [k.lower() for k in cfg.get("title_exclude_any", [])]
    us_only = cfg["locations"].get("us_only", False)

    kept: list[dict] = []
    for p in postings:
        title = p["title"].lower()
        is_ats = p["source"].startswith("ats:")

        # ATS returns all jobs -> require an internship keyword.
        if is_ats and include_kw and not any(k in title for k in include_kw):
            continue
        if any(k in title for k in exclude_kw):
            continue

        cat = _category_of(p["title"], cfg)
        if cat is None:
            continue
        p["category"] = cat

        if is_ats:
            if not _season_ok_ats(title, cfg):
                continue
        else:
            if not _season_ok_aggregator(p["season"], cfg):
                continue

        if us_only and not _looks_us(p["locations"], cfg):
            continue

        kept.append(p)
    return kept
