"""Relevance filtering: decide which normalized postings are worth alerting on.

Bias: missing a real posting is worse than an extra alert, so ambiguous cases
(missing season) are KEPT. Location is the exception: with `us_only` on we keep a
posting only when it shows a positive US signal (or is unrecognized *and*
`keep_unknown_location` is true), because a non-US role is never useful here.
"""
from __future__ import annotations

import re

# Trailing 2-letter state code, e.g. "New York, NY". The negative lookahead stops
# "Bengaluru, India" from reading "In" as Indiana.
_STATE_ABBR_RE = re.compile(r",\s*([A-Za-z]{2})(?![A-Za-z])")
_REMOTE_RE = re.compile(r"\bremote\b", re.I)


def _category_of(title: str, cfg: dict) -> str | None:
    t = title.lower()
    for cat, kws in cfg["category_keywords"].items():
        if any(kw in t for kw in kws):
            return cat
    return None


def _word_in(text: str, words) -> bool:
    """True if any `words` entry appears in `text` as a whole token.

    Word-boundary matching (not substring) so "India" doesn't match "Indiana"
    and "US" doesn't match "Austin".
    """
    return any(
        re.search(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", text, re.I)
        for w in words
    )


def _has_us_place(loc: str, abbr: set[str], names, cities, us_words) -> bool:
    if _word_in(loc, us_words) or _word_in(loc, names) or _word_in(loc, cities):
        return True
    if any(m.group(1).upper() in abbr for m in _STATE_ABBR_RE.finditer(loc)):
        return True
    # Bare state code as its own token, e.g. "NJ" or "Austin, TX; NY".
    return any(tok.strip().upper() in abbr for tok in re.split(r"[;/,]", loc))


def _looks_us(locations, cfg: dict) -> bool:
    """With `us_only`, keep only postings with a positive US signal.

    A posting is US if ANY of its locations names a US place (so "London, New York"
    is kept — a US option exists). A location counts as US-remote only when it does
    not also name a non-US place, so "Remote - India" is dropped. Locations with no
    US and no known non-US signal fall to `keep_unknown_location`.
    """
    lc = cfg["locations"]
    keep_unknown = lc.get("keep_unknown_location", False)
    if not locations:
        return keep_unknown  # no location data at all
    abbr = {s.upper() for s in lc.get("us_state_abbr", [])}
    names = lc.get("us_state_names", [])
    cities = lc.get("us_cities", [])
    us_words = lc.get("us_words", [])
    non_us = lc.get("non_us_markers", [])
    allow_remote = lc.get("allow_remote", True)

    any_us = any_non_us = False
    for loc in locations:
        non_us_here = _word_in(loc, non_us)
        remote_here = allow_remote and bool(_REMOTE_RE.search(loc)) and not non_us_here
        if _has_us_place(loc, abbr, names, cities, us_words) or remote_here:
            any_us = True
        elif non_us_here:
            any_non_us = True
    if any_us:
        return True
    if any_non_us:
        return False
    return keep_unknown  # locations present but unrecognized


def _season_ok_aggregator(season: str, cfg: dict) -> bool:
    if cfg.get("include_off_season"):
        return True
    if not season:
        return True  # unknown -> keep
    return season.strip().lower() in [s.lower() for s in cfg["seasons"]]


def _year_ok(blob: str, cfg: dict) -> bool:
    """Reject a posting that explicitly names an old cycle year and not the target.

    `blob` should include both title and url (aggregators leak the year in URLs).
    No year mentioned -> keep (the source repo is already target-year scoped).
    """
    b = blob.lower()
    target = str(cfg.get("target_year", "")).lower()
    if target and target in b:
        return True
    for old in cfg.get("stale_years", []):
        if str(old) in b:
            return False  # names an old cycle, not the target year
    return True


def classify_and_filter(postings: list[dict], cfg: dict) -> list[dict]:
    """Return the kept postings, each with `category` populated."""
    include_kw = [k.lower() for k in cfg.get("title_include_any", [])]
    exclude_kw = [k.lower() for k in cfg.get("title_exclude_any", [])]
    us_only = cfg["locations"].get("us_only", False)

    ng_include_kw = [k.lower() for k in cfg.get("new_grad_title_include_any", [])]
    ng_exclude_kw = [k.lower() for k in cfg.get("new_grad_title_exclude_any", [])]

    kept: list[dict] = []
    for p in postings:
        title = p["title"].lower()
        is_ats = p["source"].startswith("ats:")
        is_ng = p.get("role_type") == "new_grad"

        if is_ng:
            # ATS new grad: require a new-grad keyword; aggregator is already scoped.
            if is_ats and ng_include_kw and not any(k in title for k in ng_include_kw):
                continue
            if any(k in title for k in ng_exclude_kw):
                continue
            # New grad: drop anything that looks like an internship
            if any(k in title for k in include_kw):
                continue
        else:
            # ATS intern: require an internship keyword.
            if is_ats and include_kw and not any(k in title for k in include_kw):
                continue
            if any(k in title for k in exclude_kw):
                continue

        cat = _category_of(p["title"], cfg)
        if cat is None:
            continue
        p["category"] = cat

        # Drop anything explicitly tagged to an old cycle (checks title + url).
        if not _year_ok(f"{p['title']} {p['url']}", cfg):
            continue
        # Aggregators also carry a season field; enforce the target season.
        if not is_ats and not _season_ok_aggregator(p["season"], cfg):
            continue

        if us_only and not _looks_us(p["locations"], cfg):
            continue

        kept.append(p)
    return kept
