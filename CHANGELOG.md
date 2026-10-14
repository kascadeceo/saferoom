# Changelog

## 0.2.0 — 2026-10-14

The evidence layer (Pro features) — everything an auditor — or your future self — can read.

- **HTML audit report** — `saferoom review --html` writes a single self-contained
  `report.html` (inline CSS, no CDN, safe to email) with diff-colored changes,
  command log, and any decision stamp.
- **Compliance evidence export** — `saferoom export --compliance` emits
  `evidence.jsonl`: one record per session event (run / command / diff /
  decision / export), each carrying UTC timestamp, actor, and a SHA-256 hash
  chain. `saferoom export --verify [FILE]` re-checks the chain; any later edit
  to a record breaks it loudly.
- **Licensing (goodwill, not DRM)** — `saferoom license eval --email you@team`
  mints a free, locally-signed eval key (watermarked output, 30 days default);
  `license activate KEY` verifies paid keys once against the hosted endpoint
  (the CLI's only network call, explicit and skippable with `--offline`);
  `license status` shows the cached state. Pro features are gated on an active
  license, never on obfuscation.
- CI now asserts the full Pro surface: eval mint/activate, self-contained
  report, evidence chain integrity, and tamper detection. 27 unit tests.

## 0.1.0 — 2026-09-13

First public release. The sign-off layer for AI coding agents: sandbox, audit, approve.

- **Credential swap** — `saferoom init` scans `.env*` files recursively (root and
  subdirectories), generates a dummy-credential template; `saferoom run` deletes every
  real `.env*` from the sandbox clone before the container starts and mounts only
  dummies. Real values never enter the container.
- **Session audit** — every session records files changed, commands run, and the full
  git diff (`report.md` + machine-readable `report.json`).
- **Approve gate** — `saferoom approve` applies only the reviewed patch to your working
  tree; `.env*` and `.saferoom/` are hard-excluded.
- **Approval evidence** — each approval records approver identity, UTC timestamp, and
  the sha256 of the exact patch applied (`approvals.jsonl`).
- **Isolation options** — `--offline` (`--network none`), custom `--image`,
  `--screenshot URL` (optional playwright), `--local` (testing only, no isolation).
- **CLI hygiene** — `--version`, `saferoom clean [--keep N]` session pruning,
  `saferoom sessions` listing.
- Single-file Python, stdlib only, Python 3.8+. Linux-first; works anywhere Docker runs.

[MIT licensed](LICENSE). Security reports: see [SECURITY.md](SECURITY.md).
