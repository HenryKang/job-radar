# Plan: Intern-Radar — SWE Internship Monitor & Alerter

## Context

Henry (UMich junior, applying Summer 2027 SWE/quant internships) needs to stop manually
checking company career pages and instead be **alerted within 12h** whenever a new prominent
internship posting appears (quant firms, FAANG+, F50). Constraints: minimize Claude token/credit
usage (Pro plan), archive everything to a **private GitHub repo**, and leave room to later add
resume auto-tailoring and (cautiously) auto-apply.

**Key design finding (validated via live fetches):** the entire "never miss a posting" core loop
can run on **$0 of Claude credits**. Community aggregator repos expose structured JSON
(`vanshb03/Summer2027-Internships/.github/scripts/listings.json` — confirmed fields:
`company_name, title, locations, url, season, sponsorship, active, date_posted, date_updated`),
and quant/tech firms expose structured JSON via their ATS (Greenhouse confirmed live for Jane
Street: `title, location, absolute_url, updated_at`, salary metadata). So the pipeline is pure
JSON fetch + diff + notify — **no LLM required**. Claude tokens are reserved solely for the
future resume-tailoring feature.

**Locked decisions:** Alerts = **Discord webhook** (Henry already uses Discord); Sources =
**Both** (aggregator repos + direct ATS APIs); Repo auth = **install `gh` + browser login**.

---

## Immediate Next Step — Bootstrap local git repo (to unblock Ultraplan)

Ultraplan runs as a background task and needs a **local git repository**; `/Users/henry` (home)
is not one and must not become one. So the very first action on approval is a minimal bootstrap —
**no feature code yet**, just enough to launch the remote planning session:

1. `mkdir ~/job-radar`
2. `git init` inside it (+ set default branch `main`)
3. Copy this plan into the repo as `PLAN.md` and add a stub `README.md` so the repo has content
4. Initial commit (`git add -A && git commit`) so there's a valid `HEAD`

Then **you relaunch Ultraplan from `~/job-radar`** to refine the plan in the browser. All actual
implementation (Phases 0–4 below) happens *after* the refined cloud plan is approved and teleported
back. The GitHub **remote** (private repo) is created later, in Phase 0 — not needed for launch.

---

## Cost & Credit Model (important — sets expectations)

- **Core monitor loop: $0 Claude credits, $0 hosting.** Runs on GitHub Actions cron. Private-repo
  free tier = 2,000 min/month; a ~1–2 min run every 2h ≈ 360–720 min/month. Comfortably free.
- **Discord + ATS + GitHub APIs are all free.** No API keys with billing.
- **Claude Pro vs Anthropic API — the one thing to be clear on:** the Pro plan powers *interactive*
  Claude Code / claude.ai use, not headless automated API calls. Therefore:
  - Phase 3 resume tailoring is designed to run **interactively inside Claude Code** (uses the Pro
    plan you already pay for) — you paste/point at a posting, Claude tailors the resume on demand.
  - A fully-automated "tailor every posting via API" path would need separate pay-as-you-go API
    credits; we deliberately **do not** build that, to protect the Pro plan. On-demand only, with
    prompt caching of the base resume if ever automated.

---

## Architecture

```
GitHub Actions cron (every 2h)  ->  main.py
   ├── fetch_aggregators.py   (JSON from vanshb03 + others; markdown fallback for speedyapply)
   ├── fetch_ats.py           (Greenhouse / Lever / Ashby adapters for curated target list)
   ├── normalize.py           (both source types -> one common schema)
   ├── dedupe.py              (diff vs data/seen.json by stable id)
   ├── render.py              (write postings.md table + data/postings.json archive)
   └── notify_discord.py      (POST embed w/ clickable apply link for each NEW posting)
   -> git commit & push updated data back to the repo (GITHUB_TOKEN, contents: write)
```

**Common normalized schema (per posting):**
`{ id, company, title, locations[], url, season, source, category, sponsorship, date_posted, date_found }`
where `id = sha1(company + "|" + title + "|" + url)` for cross-source dedup.

**Why cron every 2h:** 12 runs/day → worst-case detection latency ~2h, far inside the 12h SLA
(tunable to hourly if desired). GitHub cron can be delayed minutes under load — irrelevant at this
cadence. Committing data back each run also keeps the scheduled workflow from being auto-disabled
for repo inactivity.

**State/dedup:** `data/seen.json` holds the set of known `id`s. Each run: fetch → normalize →
filter → drop ids already in seen → the remainder are NEW → alert + append to archive + add to seen
→ commit. Idempotent; a re-run sends no duplicate alerts.

**Filtering (`config/filters.yaml`):**
- season ∈ {Summer 2027} (+ optional off-season toggle)
- category = SWE/quant via title keywords: match `intern|internship` AND
  `software|swe|engineer|developer|quant|research|ml|machine learning|data`; year hint `2027`.
- `active == true`; US locations (toggle to include remote/other).
- Aggregators are already internship-scoped; keyword filter matters most for raw ATS results.

---

## Repo Layout (`job-radar`, private)

```
.github/workflows/crawl.yml     # cron + manual dispatch; runs main.py; commits data back
config/sources.yaml             # aggregator repo URLs + direct ATS targets [{company, ats, slug}]
config/filters.yaml             # seasons, keyword sets, location rules
src/main.py                     # orchestrator
src/fetch_aggregators.py
src/fetch_ats.py                # greenhouse|lever|ashby adapters (one function each)
src/normalize.py
src/dedupe.py
src/render.py
src/notify_discord.py
data/postings.json              # full archive (machine-readable)
data/seen.json                  # dedup state
resume/base_resume.md           # you add later (Phase 3)
postings.md                     # human-readable table — "check the repo" view
README.md                       # setup + how it works
requirements.txt                # requests, pyyaml (minimal)
```

**Secrets (GitHub Actions):** `DISCORD_WEBHOOK_URL`. (Commit-back uses the built-in
`GITHUB_TOKEN` with `permissions: contents: write` — no PAT needed.)

**Direct-ATS starter target list** (exact slug/ATS verified per-company at build time; many quant
firms are on Greenhouse/Lever/Ashby): Jane Street (Greenhouse ✓ confirmed), Citadel/Citadel
Securities, Hudson River Trading, Two Sigma, Optiver, IMC, DRW, Jump Trading, SIG, DE Shaw, Akuna,
Five Rings, Point72; plus select big-tech not always fast in aggregators. Adding a target = one
config line.

---

## Execution Phases

### Phase 0 — Prerequisites (one-time, some steps are yours)
1. I install `gh` via Homebrew.
2. **You run** `gh auth login` (browser) — suggest typing `! gh auth login` in the prompt.
3. I create the **private** repo `job-radar` under HenryKang and scaffold the files above.
4. **You create** a Discord webhook (Server → Integrations → Webhooks → New → Copy URL) and add it
   as repo secret `DISCORD_WEBHOOK_URL` (I'll give exact click-path + the `gh secret set` command).

### Phase 1 — MVP: aggregator monitor + Discord alerts (working "never miss" system, ~1 hr)
- Implement fetch_aggregators, normalize, dedupe, render, notify_discord, main.
- `crawl.yml` cron `0 */2 * * *` + `workflow_dispatch` (manual run button).
- First run seeds `seen.json` **silently** (no alert flood) and publishes the initial `postings.md`.
- Verify a manual dispatch posts a test alert to Discord with a clickable link.

### Phase 2 — Direct ATS adapters + curated targets
- Add Greenhouse/Lever/Ashby adapters in fetch_ats.py; verify each target's slug/ATS.
- Populate `config/sources.yaml` targets; keyword-filter raw ATS output.
- Confirm dedup correctly merges a posting appearing in both an aggregator and its ATS.

### Phase 3 — Resume auto-tailor (interactive, uses Pro plan; later)
- You drop `resume/base_resume.md`. A helper (`/tailor <posting-id>` style) run **inside Claude
  Code** produces a tailored resume from the base + the archived posting. On-demand only → bounded,
  cheap, stays on Pro. No automated per-posting API calls.

### Phase 4 — Assisted apply (later, with caveats)
- **Not full auto-apply.** Workday/greenhouse forms, captchas, and account creation make robust
  full automation brittle and often against site ToS. Recommend **semi-automation**: a saved
  profile + a one-click "open application pre-filled" helper, keeping you in the loop. Revisit scope
  when we get here.

---

## Verification

- **Unit-ish, local:** run `python src/main.py --dry-run` locally (no commit, no alert) → prints how
  many postings fetched/normalized/filtered/new; confirms parsing of live JSON.
- **Alert path:** `python src/notify_discord.py --test` posts a sample embed → confirm it pushes to
  your phone with a clickable link.
- **End-to-end:** trigger the Action via `workflow_dispatch`; confirm (a) `postings.md` + `data/`
  updated and committed, (b) new postings (if any) alerted exactly once, (c) a second immediate run
  sends **no** duplicate alerts (dedup works).
- **SLA check:** confirm cron `0 */2 * * *` is registered under the repo's Actions tab.

## Open items deferred to build time (non-blocking)
- Exact ATS slug per target company (verify each; skip unverifiable ones).
- Whether to include off-season/US-only toggles on by default (sensible defaults set; easily flipped
  in `filters.yaml`).
