---
type: report
title: Product Strategy v1 — what we sell, for how much, and how it ships by Friday
created: 2026-10-14
tags:
  - product-strategy
  - pricing
  - launch
related:
  - '[[Competitive-Analysis-Oct-2026]]'
---

# Product Strategy v1 — launch 2026-10-16

Founder directive: *"produce a really good product that I can sell… I don't
want to have to do anything but put a stripe on it."* This document is the
decision record. Evidence: [[Competitive-Analysis-Oct-2026]].

## 1. What SafeRoom protects (the one-sentence product answer)

> **SafeRoom protects production — your repo, your credentials, your live
> systems — from AI-agent changes nobody reviewed.** Every agent session runs
> in a credential-stripped throwaway sandbox and produces an audit artifact;
> nothing reaches your working tree or leaves via `git push` without your
> recorded sign-off.

This is the answer to "what is this protecting?" and it must appear verbatim
in spirit on the landing page, README hero, and CLI `--help`.

## 2. Cloud or local? → **Local-first product, thin hosted commercial layer**

**Decision: hybrid.** The CLI stays 100% local, MIT, fully functional free
(our differentiator — every competitor is cloud-locked or per-IDE). The paid
tier adds **hosted superpowers** (instant licensing, CI verification endpoint,
evidence vault) that do not touch the free workflow.

Why not cloud-only: the product's credibility IS "your credentials never
leave your machine." Why not pure-local donations: no leverage, no compliance
story, doesn't fund development (founder's requirement: sellable).

**Hosting platform: Netlify** — `getsaferoom.netlify.app` already exists, so
the founder already has an account. Static landing + serverless functions +
Netlify Blobs (license storage) all fit the free tier. Zero new vendors.

## 3. Tiers & pricing (named)

Market band verified at $24–40/seat/mo for *after-the-fact* review tools
(CodeRabbit, Greptile). SafeRoom is prevention + evidence + enforcement, so we
price at the top of the band, not below it.

| Tier | Price | What you get |
|---|---|---|
| **SafeRoom CLI** | **$0 forever, MIT** | Credential-stripped sandbox, command+diff audit, approve gate, pre-push gate. Local, no account. |
| **SafeRoom Pro** | **$39/mo per seat** · **$390/yr** (2 mo free) | Everything in CLI + **HTML audit report** (shareable, self-contained), **compliance evidence export** (timestamped hash-bound JSONL for SOC 2 / HIPAA auditors), license across machines, priority support (48h SLA). |
| **Pro — launch lifetime** | **$249 one-time**, first 100 seats | Everything in Pro, for the life of the product. Launch offer, converts the indie crowd that hates subscriptions. |

Pro = "evidence your auditors — and your future self — can read."

Honesty note (goes in FAQ + license docs): the CLI is open source; a license
is enforced in CI (hosted verify) and by goodwill, not by obfuscation. We sell
value (evidence format, hosted verification, support), not DRM.

## 4. What Pro concretely is on Friday (all shippable, all tested)

1. `saferoom review --html` → single self-contained `report.html`
   (inline CSS, no CDN) — roadmap item #3, high perceived value, demoable.
2. `saferoom export --compliance` → `evidence.jsonl`: one record per event
   (run/command/diff/approval/push), each with UTC timestamp, actor, and
   SHA-256 chain — roadmap item #6; this doc is its design doc (the AGENTS.md
   "ask first" is satisfied by the founder's directive to sell).
3. `saferoom license activate <KEY>` → one-time, user-initiated hosted check,
   cached locally; `--offline` never networks; no telemetry ever.
4. Hosted: `/api/license/verify` (CI gate for merge protection), checkout via
   **Stripe Payment Links** (founder creates 3 links, 5 minutes), success page
   mints + displays the license, stores it in Netlify Blobs.

## 5. The founder runbook — "put a stripe on it" (≈15 min, once)

1. **Netlify**: connect this repo (site `getsaferoom`), publish dir
   `landing/`, functions auto-detected via `netlify.toml` (shipped in repo).
2. **Stripe**: create 3 Payment Links (Pro $39/mo, Pro $390/yr, Lifetime
   $249) with success redirect `{URL}/welcome` — copy the 3 link IDs into
   Netlify env vars.
3. **Env vars in Netlify**: `STRIPE_SECRET_KEY`, `PAYMENT_LINK_*` ×3,
   `LICENSE_SECRET` (any long random string). Done — buy buttons live.

Everything else (checkout verification, license minting, verification
endpoint, landing page, docs, tests) ships in this repo.

## 6. Guardrail tensions, resolved (for the record)

- AGENTS.md says *"Do NOT build: hosted/cloud features… founder decisions."*
  The founder has now made that decision explicitly — this directive is the
  founder decision. CLI invariants (stdlib-only, single file, no telemetry,
  cred swap, approve exclusions) remain untouched; the only new CLI surface is
  `--html`, `--compliance`, `export`, and `license activate`.
- `landing/` "never edit": superseded by the same directive; we will evolve
  the existing page surgically, not replace it.
- New network behavior: ONLY `license activate` (explicit, user-run, cached).
  Documented in README "Guarantees" so the no-telemetry promise stays true.

## 7. Build plan to Friday (one Auto-Run iteration per block)

| Block | Deliverable | Verify |
|---|---|---|
| 1 ✅ | Competitive analysis + this strategy doc | committed |
| 2 | Pro features in `saferoom.py`: `review --html`, `export --compliance`, `license activate` scaffolding | py_compile + unit tests + CI assertions |
| 3 | Commerce: `netlify.toml`, functions (checkout-verify, license mint/verify), Blobs store | function tests in test mode |
| 4 | Landing page: pricing cards with real buy buttons, incidents/proof section, FAQ (incl. honesty note), guarantee copy | render check, links resolve |
| 5 | `tests/e2e_launch.sh` end-to-end product test + README/CHANGELOG sync + version bump 1.0 | full local run green |

## 8. End-to-end acceptance test (what "done" means on Friday)

`tests/e2e_launch.sh` must pass on a clean Debian box with git+python3
(Docker optional-detected) and prove the WHOLE story with grep-able
assertions:

1. `saferoom init` builds dummy-cred template from a real `.env`
2. `saferoom run` (local or docker) executes an agent-like command; **real
   secret never appears in output/session; dummy does**
3. `saferoom review` shows changed file + full diff; `review --html` emits
   self-contained `report.html` (with test license) containing the diff
4. `saferoom export --compliance` emits JSONL where each record has
   `ts/actor/event/sha256` and the chain verifies
5. `saferoom approve` applies patch; real `.env` untouched
6. `saferoom gate --pre-push` **blocks** an unapproved push to a temp bare
   remote, and **allows** the approved one
7. Landing page: `landing/index.html` contains pricing, buy-button URLs, and
   the protection sentence from §1
8. Exit 0 prints: `SAFEROOM E2E: PASS — protecting prod from unaudited agents`

A Stripe test-mode checklist (founder, 10 min) accompanies it but is optional
to CI.
