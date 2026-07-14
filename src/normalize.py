"""Common posting schema + helpers shared by every fetcher.

Every source (aggregator repo or ATS API) is normalized into the same dict so
the rest of the pipeline (filter -> dedupe -> render -> notify) is source-agnostic.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime

_SEASON_RE = re.compile(r"\b(summer|fall|spring|winter)\b.*?(20\d\d)", re.IGNORECASE)


def infer_season(title: str) -> str:
    """Extract season from title, e.g. 'Summer Intern 2027 - SWE' -> 'Summer 2027'."""
    m = _SEASON_RE.search(title or "")
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    return ""


def make_id(company: str, title: str, url: str) -> str:
    """Stable id used for cross-source dedup."""
    key = f"{company}|{title}|{url}".lower().strip()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def parse_ts(value) -> int | None:
    """Best-effort -> unix seconds. Accepts epoch (s or ms) or ISO-8601."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        return n // 1000 if n > 1_000_000_000_000 else n
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        return n // 1000 if n > 1_000_000_000_000 else n
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except ValueError:
        return None


def make_posting(
    company: str,
    title: str,
    locations,
    url: str,
    season: str,
    source: str,
    sponsorship: str = "",
    date_posted=None,
) -> dict:
    company = (company or "").strip()
    title = (title or "").strip()
    url = (url or "").strip()
    locations = [str(l).strip() for l in (locations or []) if str(l).strip()]
    return {
        "id": make_id(company, title, url),
        "company": company,
        "title": title,
        "locations": locations,
        "url": url,
        "season": (season or "").strip() or infer_season(title),
        "source": source,
        "category": "",           # filled in by the filter step
        "sponsorship": (sponsorship or "").strip(),
        "date_posted": parse_ts(date_posted),
        "date_found": None,       # filled in by main when first seen
        "alive": None,            # filled in by the eligibility step
        "eligibility": "",        # ok | unknown | phd_only | grad_only | underclass_only
        "elig_reason": "",
    }
