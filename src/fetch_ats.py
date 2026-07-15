"""Fetch postings directly from company ATS public APIs.

Supported: Greenhouse (verified), Lever, Ashby. Each returns structured JSON —
no scraping, no LLM. A bad slug / network error for one target is logged and
skipped so it never breaks the run.
"""
from __future__ import annotations

import requests

from normalize import make_posting

_HEADERS = {"User-Agent": "intern-radar (github actions bot)"}


def _get_json(url: str, timeout: int = 30):
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _greenhouse(company: str, slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    data = _get_json(url)
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append(
            make_posting(
                company=company,
                title=j.get("title", ""),
                locations=[loc] if loc else [],
                url=j.get("absolute_url", ""),
                season="",
                source=f"ats:greenhouse:{slug}",
                date_posted=j.get("updated_at"),
            )
        )
    return out


def _lever(company: str, slug: str) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _get_json(url)
    out = []
    for j in data:
        cats = j.get("categories", {}) or {}
        loc = cats.get("location", "")
        out.append(
            make_posting(
                company=company,
                title=j.get("text", ""),
                locations=[loc] if loc else [],
                url=j.get("hostedUrl", "") or j.get("applyUrl", ""),
                season="",
                source=f"ats:lever:{slug}",
                date_posted=j.get("createdAt"),
            )
        )
    return out


def _ashby(company: str, slug: str) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    data = _get_json(url)
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location", "") or j.get("locationName", "")
        out.append(
            make_posting(
                company=company,
                title=j.get("title", ""),
                locations=[loc] if loc else [],
                url=j.get("jobUrl", "") or j.get("applyUrl", ""),
                season="",
                source=f"ats:ashby:{slug}",
                date_posted=j.get("publishedAt") or j.get("updatedAt"),
            )
        )
    return out


_ADAPTERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def fetch_ats(targets: list[dict]) -> list[dict]:
    out: list[dict] = []
    for t in targets:
        if not t.get("enabled", False):
            continue
        ats = t.get("ats")
        adapter = _ADAPTERS.get(ats)
        if adapter is None:
            print(f"[warn] unknown ats {ats!r} for {t.get('company')!r}, skipped")
            continue
        role_type = t.get("role_type", "intern")
        try:
            got = adapter(t.get("company", t["slug"]), t["slug"])
            for p in got:
                p["role_type"] = role_type
                # Re-compute id to include role_type
                from normalize import make_id
                p["id"] = make_id(p["company"], p["title"], p["url"], role_type)
            out.extend(got)
            print(f"[ats:{role_type}] {t.get('company')} ({ats}:{t['slug']}): {len(got)} jobs")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] ats {t.get('company')!r} ({ats}:{t.get('slug')!r}) failed: {e}")
    return out
