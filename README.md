# intern-radar

Automated monitor for Summer 2027 SWE/quant internship postings (quant firms, FAANG+, F50).
Polls community aggregator repos + company ATS APIs on a schedule, archives new postings to this
repo, and pushes a Discord alert with a clickable apply link — so applications are caught within
hours, not missed.

**Status:** bootstrapping. See [`PLAN.md`](./PLAN.md) for the full system design and phased build
plan. Implementation begins after the plan is refined via Ultraplan.

## Goals
- Never miss a newly-posted prominent SWE/quant internship (≤12h detection SLA).
- Run the core loop at $0 (GitHub Actions cron + free JSON APIs, no LLM tokens).
- Later: on-demand resume tailoring and assisted apply.
