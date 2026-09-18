# Changelog

## 0.1.0 — 2026-09-13

First public release. The sign-off layer for AI coding agents: sandbox, audit, approve.

- **Credential swap** — `saferoom init` scans `.env*` files recursively (root and
  subdirectories), generates a dummy-credential template; `saferoom run` deletes every
  real `.env*` from the sandbox clone before the container starts and mounts only
  dummies. Real values never enter the container.
- **Session audit** — every session records files changed, available interactive
  shell history, and the full git diff (`report.md` + machine-readable `report.json`).
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
