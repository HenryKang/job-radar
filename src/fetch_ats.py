"""Fetch postings directly from company ATS public APIs.

Supported: Greenhouse (verified), Lever, Ashby. Each returns structured JSON —
no scraping, no LLM. A bad slug / network error for one target is logged and
skipped so it never breaks the run.
"""
from __future__ import annotations

import requests

from normalize import make_posting
from fetch_uber import fetch_uber

_HEADERS = {"User-Agent": "job-radar (github actions bot)"}


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


_WD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.myworkdayjobs.com",
    "Referer": "https://www.myworkdayjobs.com/",
}


def _workday(company: str, slug: str) -> list[dict]:
    """slug format: tenant.wdN/site  e.g. adobe.wd5/external_experienced
    The tenant portion (before /) is the full subdomain prefix including .wdN."""
    host_part, site = slug.split("/", 1)
    # host_part is e.g. "adobe.wd5" -> subdomain of myworkdayjobs.com
    # CxS path uses just the company name (before the .wdN)
    tenant_name = host_part.split(".")[0]
    url = f"https://{host_part}.myworkdayjobs.com/wday/cxs/{tenant_name}/{site}/jobs"
    out = []
    offset = 0
    limit = 50
    while True:
        r = requests.post(url, headers=_WD_HEADERS, timeout=30,
                          json={"appliedFacets": {}, "limit": limit,
                                "offset": offset, "searchText": " "})
        r.raise_for_status()
        d = r.json()
        postings = d.get("jobPostings", [])
        for j in postings:
            loc = j.get("locationsText", "") or j.get("bulletFields", [""])[0] if j.get("bulletFields") else ""
            path = j.get("externalPath", "")
            base = f"https://{tenant}.myworkdayjobs.com/en-US/{site}"
            out.append(make_posting(
                company=company,
                title=j.get("title", ""),
                locations=[loc] if loc else [],
                url=f"{base}{path}" if path else "",
                season="",
                source=f"ats:workday:{slug}",
                date_posted=j.get("postedOn"),
            ))
        if len(postings) < limit or offset + limit >= d.get("total", 0):
            break
        offset += limit
    return out


def _uber(company: str, slug: str) -> list[dict]:
    return fetch_uber(role_type=slug)  # slug = "intern" or "new_grad"


_EF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json",
}


def _eightfold(company: str, slug: str) -> list[dict]:
    """slug format: domain:pid  e.g. mlp.com:755936615453
    Polls the Eightfold AI talent API used by Millennium and others.
    """
    domain, pid = slug.split(":", 1)
    subdomain = domain.split(".")[0]
    base_url = f"https://{subdomain}.eightfold.ai/api/apply/v2/jobs"
    out: list[dict] = []
    start = 0
    num = 50
    while True:
        try:
            r = requests.get(
                base_url, headers=_EF_HEADERS, timeout=20,
                params={"domain": domain, "pid": pid, "start": start, "num": num},
            )
            r.raise_for_status()
            d = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] eightfold {company} ({slug}) failed at start={start}: {e}")
            break
        positions = d.get("positions", [])
        for j in positions:
            loc = j.get("location", "") or ""
            out.append(make_posting(
                company=company,
                title=j.get("name", ""),
                locations=[loc] if loc else [],
                url=j.get("canonicalPositionUrl", ""),
                season="",
                source=f"ats:eightfold:{slug}",
                date_posted=j.get("t_create"),
            ))
        total = d.get("count", 0)
        start += len(positions)
        if start >= total or not positions:
            break
    return out


_ADAPTERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby,
             "workday": _workday, "uber_custom": _uber, "eightfold": _eightfold}


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
