"""Eligibility + liveness pre-check for a posting.

Fetches the posting page and applies free heuristics (no LLM) to answer:
  - Is the link still live? (aggregators keep stale postings marked active; some
    ATSes serve a tiny JS "job expired" stub with HTTP 200; Workday hides the
    real status behind a JSON API)
  - Does it fit an undergraduate junior seeking a bachelor's-level SWE/quant role?
    (drop PhD-only / master's-only / underclassmen-only)
  - Bonus: extract an accurate posted date when the source exposes one (Workday).

Bias: only exclude on a POSITIVE disqualifier. Unknown / unfetchable -> keep.

eligibility values: ok | unknown | phd_only | grad_only | underclass_only
Return dict: {alive, eligibility, reason, [date_posted]}
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import requests

from normalize import parse_ts

_HEADERS = {"User-Agent": "Mozilla/5.0 (job-radar; +https://github.com/HenryKang/job-radar)"}
_WD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

GOOD = {"ok", "unknown"}  # what we still alert on

_CLOSED_MARKERS = [
    "jobexpired", "job-expired", "no longer accepting", "no longer available",
    "no longer open", "position has been filled", "this position is closed",
    "this posting is closed", "posting is closed", "position is no longer",
    "job not found", "page not found", "we couldn't find",
]
_PHD_PHRASES = [
    "pursuing a phd", "phd candidate", "phd student", "ph.d. candidate",
    "ph.d. student", "doctoral candidate", "doctoral student",
    "enrolled in a phd", "phd degree", "phd in ", "ph.d. in ",
]
_MASTERS_PHRASES = [
    "master's degree required", "must be pursuing a master",
    "currently pursuing a master", "enrolled in a master's",
    "master's or phd", "graduate degree required",
]
_UNDERCLASS_PHRASES = [
    "freshmen and sophomores", "freshman and sophomore", "first- and second-year",
    "first and second year", "first-year and second-year", "rising sophomore",
    "underclassmen only", "1st and 2nd year",
]
_GRAD_TITLE = re.compile(r"\b(master'?s?|mba|ph\.?d|doctoral)\b")
_UNDERGRAD_TITLE = re.compile(r"\b(undergrad\w*|bachelor'?s?)\b")


def _visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"<[^>]+>", " ", html).lower()


def _title_text(html: str) -> str:
    """Server-rendered <title>. Empty when the page resolved no job at all."""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    return re.sub(r"\s+", " ", m.group(1)).strip().lower() if m else ""


def _classify_text(text: str) -> tuple[str, str]:
    """Eligibility from a description blob. text should be lowercased visible text."""
    undergrad = ("bachelor" in text) or ("undergrad" in text)
    junior_senior = ("junior" in text) or ("senior" in text)

    uc = next((p for p in _UNDERCLASS_PHRASES if p in text), None)
    if uc and not junior_senior:
        return "underclass_only", repr(uc)
    if not undergrad:
        phd_phrase = next((p for p in _PHD_PHRASES if p in text), None)
        phd_hits = len(re.findall(r"ph\.?\s?d", text))
        if phd_phrase or phd_hits >= 3:
            return "phd_only", f"phd signal ({phd_phrase or f'{phd_hits} mentions'})"
        mp = next((p for p in _MASTERS_PHRASES if p in text), None)
        if mp:
            return "grad_only", repr(mp)
    return ("ok", "undergrad markers") if undergrad else ("unknown", "no clear disqualifier")


_GH_API_URL_RE = re.compile(
    r"https?://(?:boards|job-boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)", re.I
)
_GH_API_HEADERS = {"User-Agent": "job-radar (github actions bot)"}


def _check_greenhouse(url: str, timeout: int) -> dict | None:
    """Use the Greenhouse public API to check a boards.greenhouse.io job URL.

    Avoids custom-domain redirect chains (e.g. boards.greenhouse.io → careers.roblox.com)
    and bot-detection on the career site. Returns 404 for removed jobs.
    """
    m = _GH_API_URL_RE.search(url)
    if not m:
        return None
    slug, job_id = m.group(1), m.group(2)
    api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
    try:
        r = requests.get(api, headers=_GH_API_HEADERS, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"alive": True, "eligibility": "unknown", "reason": f"greenhouse api failed: {e}"}
    if r.status_code == 404:
        return {"alive": False, "eligibility": "na", "reason": "greenhouse api: job not found"}
    if not r.ok:
        return {"alive": True, "eligibility": "unknown", "reason": f"greenhouse api http {r.status_code}"}
    try:
        data = r.json()
        desc = data.get("content", "") or ""
        elig, reason = _classify_text(_visible_text(desc))
        return {"alive": True, "eligibility": elig, "reason": f"greenhouse api: {reason}"}
    except Exception:  # noqa: BLE001
        return {"alive": True, "eligibility": "unknown", "reason": "greenhouse api: json parse error"}


def _workday_cxs(url: str) -> str | None:
    """Map a public Workday job URL to its CxS JSON detail endpoint."""
    u = urlparse(url)
    if not u.netloc.endswith("myworkdayjobs.com"):
        return None
    tenant = u.netloc.split(".")[0]
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2 or parts[1] != "job":
        return None
    site, rest = parts[0], "/".join(parts[1:])
    return f"https://{u.netloc}/wday/cxs/{tenant}/{site}/{rest}"


def _check_workday(url: str, timeout: int) -> dict | None:
    cxs = _workday_cxs(url)
    if not cxs:
        return None
    r = None
    for _ in range(2):  # retry once to avoid a transient 403 marking a live job dead
        try:
            r = requests.get(cxs, headers=_WD_HEADERS, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return {"alive": True, "eligibility": "unknown", "reason": f"workday fetch failed: {e}"}
        if r.status_code not in (403, 404):
            break
    if r.status_code in (403, 404):  # Workday returns 403/404 for removed jobs
        return {"alive": False, "eligibility": "na", "reason": f"workday gone (http {r.status_code})"}
    if not r.ok:
        return {"alive": True, "eligibility": "unknown", "reason": f"workday http {r.status_code}"}
    try:
        info = (r.json() or {}).get("jobPostingInfo", {}) or {}
    except ValueError:
        return {"alive": True, "eligibility": "unknown", "reason": "workday bad json"}
    elig, reason = _classify_text(_visible_text(info.get("jobDescription", "") or ""))
    out = {"alive": True, "eligibility": elig, "reason": f"workday: {reason}"}
    dp = parse_ts(info.get("startDate"))
    if dp:
        out["date_posted"] = dp
    return out


def check(url: str, title: str = "", timeout: int = 15) -> dict:
    title_low = (title or "").lower()

    if _GRAD_TITLE.search(title_low) and not _UNDERGRAD_TITLE.search(title_low):
        return {"alive": True, "eligibility": "grad_only", "reason": "grad-level title"}
    if not url:
        return {"alive": True, "eligibility": "unknown", "reason": "no url"}

    wd = _check_workday(url, timeout)  # Workday HTML is a JS shell; use its API
    if wd is not None:
        return wd

    gh = _check_greenhouse(url, timeout)  # Greenhouse: use API to avoid custom-domain redirects
    if gh is not None:
        return gh

    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        return {"alive": True, "eligibility": "unknown", "reason": f"fetch failed: {e}"}

    raw_low = (r.text or "").lower()
    text = _visible_text(r.text or "")
    title = _title_text(r.text or "")
    small = len(r.text or "") < 1500

    if r.status_code in (404, 410):
        return {"alive": False, "eligibility": "na", "reason": f"http {r.status_code}"}

    # Greenhouse removes expired jobs by redirecting /jobs/<id> to the board index.
    # The redirect destination returns 200 with no closed-markers, so we catch it here.
    if r.url and r.url.rstrip("/") != url.rstrip("/"):
        orig_path = urlparse(url).path
        final_path = urlparse(r.url).path
        if (("greenhouse.io" in url or "lever.co" in url)
                and "/jobs/" in orig_path
                and "/jobs/" not in final_path):
            return {"alive": False, "eligibility": "na", "reason": "redirected away from job page"}

    # Closed markers only count when they appear in SERVER-RENDERED content (the
    # <title> or visible text). Scanning raw HTML marked live jobs dead on SPA
    # career sites: lifeattiktok.com ships a "404 Page not found" error
    # component inside the Next.js payload of EVERY page, live or not, which
    # silently suppressed 32 real TikTok internships (Aug 2026).
    hit = next((m for m in _CLOSED_MARKERS if m in title or m in text), None)
    if hit:
        return {"alive": False, "eligibility": "na", "reason": f"closed: {hit!r}"}

    # JS-rendered 404: marker exists only in the script payload AND the server
    # resolved no title — nothing was found to render. A live SPA posting still
    # returns its real job title, so this stays precise.
    if not title and any(m in raw_low for m in _CLOSED_MARKERS):
        return {"alive": False, "eligibility": "na", "reason": "js-rendered 404 (no title)"}

    if small and any(w in title or w in text for w in ("expired", "closed", "not found", "redirect")):
        return {"alive": False, "eligibility": "na", "reason": "small page + closed/redirect marker"}

    elig, reason = _classify_text(text)
    return {"alive": True, "eligibility": elig, "reason": reason}


if __name__ == "__main__":
    import sys
    for u in sys.argv[1:]:
        print(u, "->", check(u))
