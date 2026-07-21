"""Fetch Uber job postings from jobs.uber.com.

Uber runs a custom Next.js job board with no public JSON API. The sitemap at
https://jobs.uber.com/en/jobs/sitemap.xml exposes all job IDs and page titles
(in <loc> URLs + <title> via the page). We parse the sitemap and filter titles
for intern/new-grad keywords, then fetch individual pages for location + date.
"""
from __future__ import annotations

import re
import concurrent.futures

import requests

from normalize import make_posting

_SITEMAP = "https://jobs.uber.com/en/jobs/sitemap.xml"
_JOB_URL = "https://jobs.uber.com/en/jobs/{jid}/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "text/html,*/*",
}
_INTERN_KW   = re.compile(r"intern|internship|co-op", re.I)
_NEWGRAD_KW  = re.compile(r"new\s*grad|entry.level|university\s+grad", re.I)
_TITLE_STRIP = re.compile(r",\s*[A-Z][^,]*,\s*(United States|US|Brazil|.*)\s*$")


def _job_title_from_sitemap_url(url: str) -> str:
    """Sitemap <loc> is just the URL — title comes from page, use URL slug as fallback."""
    return ""


def _fetch_page(jid: str) -> dict | None:
    """Fetch an individual job page and extract title, location, date."""
    try:
        r = requests.get(_JOB_URL.format(jid=jid), headers=_HEADERS, timeout=15)
        if not r.ok:
            return None
        html = r.text
        title_m = re.search(r"<title>([^<]+)</title>", html)
        if not title_m:
            return None
        raw_title = title_m.group(1).strip()
        # title format: "Role Title, City, Country" — strip location suffix
        title = _TITLE_STRIP.sub("", raw_title).strip().rstrip(",").strip()

        # Location: Uber embeds it in the page title as "Role, City, Country"
        # Extract from raw_title after the role portion
        loc_parts = raw_title.split(",")
        location = ", ".join(p.strip() for p in loc_parts[1:] if p.strip()) if len(loc_parts) > 1 else ""

        # Date from meta
        date_m = re.search(r'"datePosted"\s*:\s*"([^"]+)"', html)
        date_str = date_m.group(1) if date_m else None

        return {"jid": jid, "title": title, "raw_title": raw_title,
                "location": location, "date_posted": date_str}
    except Exception:  # noqa: BLE001
        return None


def fetch_uber(role_type: str = "intern") -> list[dict]:
    """Fetch Uber postings via sitemap scan + page fetch for matching roles."""
    try:
        r = requests.get(_SITEMAP, headers=_HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] uber sitemap fetch failed: {e}")
        return []

    # Parse all job IDs and their URL slugs from sitemap
    entries = re.findall(r"<loc>https://jobs\.uber\.com/en/jobs/(\d+)/</loc>", r.text)

    # We can't filter by title without fetching each page, but we can use a
    # heuristic: intern IDs tend to be clustered. Fetch all pages in parallel
    # with a generous thread pool — the sitemap is ~580 entries, a quick
    # title grep on the page is fast.
    # To keep CI runtime reasonable, only fetch pages where the URL slug
    # doesn't hint at a non-intern role (no slug text available from sitemap).
    # Instead: fetch in batches of 30 threads; stop early if we have enough.
    kw = _INTERN_KW if role_type == "intern" else _NEWGRAD_KW

    out: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(_fetch_page, jid): jid for jid in entries}
        for fut in concurrent.futures.as_completed(futs):
            info = fut.result()
            if not info:
                continue
            if not kw.search(info["raw_title"]):
                continue
            out.append(make_posting(
                company="Uber",
                title=info["title"],
                locations=[info["location"]] if info["location"] else [],
                url=_JOB_URL.format(jid=info["jid"]),
                season="",
                source=f"ats:uber:{role_type}",
                date_posted=info["date_posted"],
                role_type=role_type,
            ))

    print(f"[ats:uber:{role_type}] scanned {len(entries)} sitemap entries -> {len(out)} matches")
    return out
