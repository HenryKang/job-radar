"""Fetch postings from community aggregator repos that publish a listings.json.

Schema (SimplifyJobs / vanshb03): a JSON array of objects with fields like
company_name, title, locations[], url, season, sponsorship, active,
is_visible, date_posted.
"""
from __future__ import annotations

import requests

from normalize import make_posting

_HEADERS = {"User-Agent": "intern-radar (github actions bot)"}


def _get_json(url: str, timeout: int = 30):
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _fetch_listings_json(source: dict) -> list[dict]:
    data = _get_json(source["listings_url"])
    out: list[dict] = []
    for item in data:
        if not item.get("is_visible", True):
            continue
        if not item.get("active", True):
            continue
        out.append(
            make_posting(
                company=item.get("company_name", ""),
                title=item.get("title", ""),
                locations=item.get("locations", []) or [],
                url=item.get("url", "") or item.get("company_url", ""),
                season=item.get("season", ""),
                source=f"agg:{source['name']}",
                sponsorship=item.get("sponsorship", ""),
                date_posted=item.get("date_posted"),
            )
        )
    return out


def fetch_aggregators(sources: list[dict]) -> list[dict]:
    out: list[dict] = []
    for s in sources:
        if not s.get("enabled", True):
            continue
        try:
            if s.get("type") == "listings_json":
                got = _fetch_listings_json(s)
                out.extend(got)
                print(f"[agg] {s['name']}: {len(got)} active postings")
            else:
                print(f"[agg] {s['name']}: unknown type {s.get('type')!r}, skipped")
        except Exception as e:  # noqa: BLE001 - one bad source must not kill the run
            print(f"[warn] aggregator {s.get('name')!r} failed: {e}")
    return out
