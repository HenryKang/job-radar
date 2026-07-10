"""Eligibility + liveness pre-check for a posting.

Fetches the posting page and applies free heuristics (no LLM) to answer:
  - Is the link still live? (aggregators keep stale postings marked active, and
    some ATSes serve a tiny JS "job expired" redirect stub with HTTP 200)
  - Does it fit an undergraduate junior seeking a bachelor's-level SWE/quant role?
    (drop PhD-only / master's-only / underclassmen-only)

Bias: only exclude on a POSITIVE disqualifier. Unknown / unfetchable -> keep
(better an extra alert than a missed application).

eligibility values: ok | unknown | phd_only | grad_only | underclass_only
"""
from __future__ import annotations

import re

import requests

_HEADERS = {"User-Agent": "Mozilla/5.0 (intern-radar; +https://github.com/HenryKang/intern-radar)"}

GOOD = {"ok", "unknown"}  # what we still alert on

# "This posting is gone" phrases — matched against RAW html (redirects live in
# <script>), so dead regardless of page size.
_CLOSED_MARKERS = [
    "jobexpired",
    "job-expired",
    "no longer accepting",
    "no longer available",
    "no longer open",
    "position has been filled",
    "this position is closed",
    "this posting is closed",
    "posting is closed",
    "position is no longer",
    "job not found",
    "page not found",
    "we couldn't find",
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

# Grad-level signal in the *title* (e.g. Apple posts separate "Undergrad" and
# "Masters" intern reqs).
_GRAD_TITLE = re.compile(r"\b(master'?s?|mba|ph\.?d|doctoral)\b")
_UNDERGRAD_TITLE = re.compile(r"\b(undergrad\w*|bachelor'?s?)\b")


def _visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"<[^>]+>", " ", html).lower()


def check(url: str, title: str = "", timeout: int = 15) -> dict:
    title_low = (title or "").lower()

    # Title-level grad signal is reliable and needs no fetch.
    if _GRAD_TITLE.search(title_low) and not _UNDERGRAD_TITLE.search(title_low):
        return {"alive": True, "eligibility": "grad_only", "reason": "grad-level title"}

    if not url:
        return {"alive": True, "eligibility": "unknown", "reason": "no url"}
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        return {"alive": True, "eligibility": "unknown", "reason": f"fetch failed: {e}"}

    raw_low = (r.text or "").lower()
    text = _visible_text(r.text or "")           # script-free, for eligibility
    small = len(r.text or "") < 1500

    # ---- Liveness (scan raw so we catch JS redirects) ----
    if r.status_code in (404, 410):
        return {"alive": False, "eligibility": "na", "reason": f"http {r.status_code}"}
    hit = next((m for m in _CLOSED_MARKERS if m in raw_low), None)
    if hit:
        return {"alive": False, "eligibility": "na", "reason": f"closed: {hit!r}"}
    if small and any(w in raw_low for w in ("expired", "closed", "not found", "redirect")):
        return {"alive": False, "eligibility": "na", "reason": "small page + closed/redirect marker"}

    # ---- Eligibility (description) ----
    undergrad = ("bachelor" in text) or ("undergrad" in text)
    junior_senior = ("junior" in text) or ("senior" in text)

    uc = next((p for p in _UNDERCLASS_PHRASES if p in text), None)
    if uc and not junior_senior:
        return {"alive": True, "eligibility": "underclass_only", "reason": f"{uc!r}"}

    if not undergrad:
        phd_phrase = next((p for p in _PHD_PHRASES if p in text), None)
        phd_hits = len(re.findall(r"ph\.?\s?d", text))
        if phd_phrase or phd_hits >= 3:
            return {"alive": True, "eligibility": "phd_only",
                    "reason": f"phd signal ({phd_phrase or f'{phd_hits} mentions'})"}
        mp = next((p for p in _MASTERS_PHRASES if p in text), None)
        if mp:
            return {"alive": True, "eligibility": "grad_only", "reason": f"{mp!r}"}

    return {"alive": True, "eligibility": "ok" if undergrad else "unknown",
            "reason": "undergrad markers" if undergrad else "no clear disqualifier"}


if __name__ == "__main__":
    import sys
    for u in sys.argv[1:]:
        print(u, "->", check(u))
