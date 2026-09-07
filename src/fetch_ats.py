"""Fetch postings directly from company ATS public APIs.

Supported: Greenhouse (verified), Lever, Ashby. Each returns structured JSON —
no scraping, no LLM. A bad slug / network error for one target is logged and
skipped so it never breaks the run.
"""
from __future__ import annotations

import re
from datetime import datetime

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


def _workday(company: str, slug: str, role_type: str = "intern") -> list[dict]:
    """slug format: tenant.wdN/site  e.g. adobe.wd5/external_experienced
    The tenant portion (before /) is the full subdomain prefix including .wdN.

    Workday tenants carry thousands of reqs, so we push a search term server-side
    rather than paging the whole board.
    """
    host_part, site = slug.split("/", 1)
    # host_part is e.g. "adobe.wd5" -> subdomain of myworkdayjobs.com
    # CxS path uses just the company name (before the .wdN)
    tenant_name = host_part.split(".")[0]
    url = f"https://{host_part}.myworkdayjobs.com/wday/cxs/{tenant_name}/{site}/jobs"
    base = f"https://{host_part}.myworkdayjobs.com/en-US/{site}"
    search = "intern" if role_type == "intern" else "graduate"
    out = []
    offset = 0
    limit = 20  # CxS caps page size at 20
    total = None  # only the FIRST page reports it; later pages return total=0
    while True:
        r = requests.post(url, headers=_WD_HEADERS, timeout=30,
                          json={"appliedFacets": {}, "limit": limit,
                                "offset": offset, "searchText": search})
        r.raise_for_status()
        d = r.json()
        if total is None:
            total = d.get("total", 0)
        postings = d.get("jobPostings", [])
        for j in postings:
            bullets = j.get("bulletFields") or []
            loc = j.get("locationsText") or (bullets[0] if bullets else "")
            path = j.get("externalPath", "")
            out.append(make_posting(
                company=company,
                title=j.get("title", ""),
                locations=[loc] if loc else [],
                url=f"{base}{path}" if path else "",
                season="",
                source=f"ats:workday:{slug}",
                date_posted=None,  # postedOn is relative ("Posted 3 Days Ago")
            ))
        offset += limit
        if len(postings) < limit or offset >= min(total, 400):
            break
    return out


_AMZ_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json",
}


def _amazon(company: str, slug: str) -> list[dict]:
    """amazon.jobs public search API — the same endpoint the careers site calls.

    slug is the search query (e.g. "intern"). Amazon is otherwise reachable only
    through the aggregator repos, which lag by however often they refresh.
    """
    out = []
    offset, limit = 0, 100
    while offset < 500:
        r = requests.get(
            "https://www.amazon.jobs/en/search.json",
            headers=_AMZ_HEADERS, timeout=30,
            params={"base_query": slug.replace("+", " "), "result_limit": limit,
                    "offset": offset, "sort": "recent", "country[]": "USA"},
        )
        r.raise_for_status()
        d = r.json()
        jobs = d.get("jobs", [])
        for j in jobs:
            path = j.get("job_path", "")
            posted = None
            raw = (j.get("posted_date") or "").strip()
            if raw:
                try:  # "September  4, 2026" -> ISO for parse_ts
                    posted = datetime.strptime(re.sub(r"\s+", " ", raw), "%B %d, %Y").date().isoformat()
                except ValueError:
                    posted = None
            out.append(make_posting(
                company=company,
                title=j.get("title", ""),
                locations=[j.get("location", "")] if j.get("location") else [],
                url=f"https://www.amazon.jobs{path}" if path else "",
                season="",
                source=f"ats:amazon:{slug}",
                date_posted=posted,
            ))
        offset += limit
        if len(jobs) < limit or offset >= d.get("hits", 0):
            break
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
             "workday": _workday, "amazon": _amazon, "uber_custom": _uber,
             "eightfold": _eightfold}


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
            if ats == "workday":
                got = adapter(t.get("company", t["slug"]), t["slug"], role_type)
            else:
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
