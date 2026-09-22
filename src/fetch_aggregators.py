"""Fetch postings from community aggregator repos.

Two shapes are supported:

listings_json (SimplifyJobs / vanshb03): a JSON array of objects with fields
    like company_name, title, locations[], url, season, sponsorship, active,
    is_visible, date_posted.

markdown_table (speedyapply): repos that publish only rendered markdown tables.
    Their structured data sits behind a private backend, so the committed
    tables are the sole public artifact and we parse them directly.
"""
from __future__ import annotations

import re
import time

import requests

from normalize import make_posting

_HEADERS = {"User-Agent": "job-radar (github actions bot)"}


def _get_json(url: str, timeout: int = 30):
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _fetch_listings_json(source: dict) -> list[dict]:
    data = _get_json(source["listings_url"])
    role_type = source.get("role_type", "intern")
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
                role_type=role_type,
            )
        )
    return out


# | <a href="site"><strong>Company</strong></a> | Title | Location | Salary | <a href="apply"><img ...></a> | 13d |
_CELL_LINK = re.compile(r'<a\s+href="([^"]+)"', re.I)
_CELL_TEXT = re.compile(r"<[^>]+>")
_AGE = re.compile(r"^(\d+)\s*([dhmo])", re.I)


def _strip_html(cell: str) -> str:
    return _CELL_TEXT.sub("", cell).replace("&amp;", "&").strip()


def _age_to_ts(cell: str, now: int) -> int | None:
    """'13d' / '4h' / '2mo' -> approximate unix seconds."""
    m = _AGE.match(cell.strip())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    if cell.strip().lower().startswith(f"{n}mo"):
        return now - n * 30 * 86400
    return now - n * {"d": 86400, "h": 3600, "m": 60, "o": 30 * 86400}.get(unit, 86400)


def _fetch_markdown_table(source: dict) -> list[dict]:
    r = requests.get(source["listings_url"], headers=_HEADERS, timeout=30)
    r.raise_for_status()
    role_type = source.get("role_type", "new_grad")
    now = int(time.time())
    out: list[dict] = []
    seen_urls: set[str] = set()

    for line in r.text.splitlines():
        line = line.strip()
        # A separator row is only pipes/dashes/colons; testing for "---"
        # anywhere would also drop real rows, since Workday encodes " - " as
        # "---" inside its job URLs.
        if not line.startswith("|") or re.fullmatch(r"[|\s:-]+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        company = _strip_html(cells[0])
        title = _strip_html(cells[1])
        if not company or not title or company.lower() == "company":
            continue
        # Column layouts differ between sections: the FAANG+/Quant tables carry
        # a Salary column and the Other table does not, so find the posting cell
        # by content instead of a fixed index. The company cell (0) links to a
        # corporate homepage, not a posting, so start past it.
        url = ""
        for cell in cells[3:]:
            links = _CELL_LINK.findall(cell)
            if links:
                url = links[0]
                break
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        location = _strip_html(cells[2])
        out.append(
            make_posting(
                company=company,
                title=title,
                locations=[location] if location and location != "-" else [],
                url=url,
                season="",
                source=f"agg:{source['name']}",
                date_posted=_age_to_ts(cells[-1], now),
                role_type=role_type,
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
            elif s.get("type") == "markdown_table":
                got = _fetch_markdown_table(s)
                out.extend(got)
                print(f"[agg] {s['name']}: {len(got)} postings (markdown)")
            else:
                print(f"[agg] {s['name']}: unknown type {s.get('type')!r}, skipped")
        except Exception as e:  # noqa: BLE001 - one bad source must not kill the run
            print(f"[warn] aggregator {s.get('name')!r} failed: {e}")
    return out
