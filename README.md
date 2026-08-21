# SafeRoom

[![CI](https://github.com/kascadeceo/saferoom/actions/workflows/ci.yml/badge.svg)](https://github.com/kascadeceo/saferoom/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org)
[![No dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](saferoom.py)

**The sign-off layer for AI coding agents.** Cloud sandboxes solve where agent
code runs. SafeRoom solves what ships.

SafeRoom clones your repo into an isolated Docker container, swaps live
credentials for dummies, lets your agent run free, and hands you a full audit
trail — files changed, commands run, clean diffs. Nothing reaches your real
code until you approve it.

```
your repo ──clone──▶ sandbox container ──agent works──▶ audit report ──you approve──▶ your repo
                     (dummy .env, no prod access)       (diff + command log)
```

<!-- DEMO: record with `vhs` or asciinema — agent tries DROP TABLE, SafeRoom catches it -->
<!-- ![demo](docs/demo.gif) -->

## Why

Nearly half of AI-generated code fails security tests (Veracode, 2025). Claude
Code and Cursor can refactor your whole backend from one prompt — and one
hallucinated migration can drop your production tables. Sandboxes contain the
blast. SafeRoom contains the decision: what the agent produced only ships after
you've seen the diff.

## Install

**Linux (Debian/Ubuntu):**

```bash
sudo apt-get update && sudo apt-get install -y git python3 docker.io
git clone https://github.com/kascadeceo/saferoom && cd saferoom
sudo ./install.sh
```

**macOS:** install [Docker Desktop](https://www.docker.com/products/docker-desktop/),
then `sudo ./install.sh`.

Single-file Python, stdlib only. If you can read `saferoom.py` in one sitting —
and you can — you know exactly what runs on your machine.

## Quick start

```bash
cd your-project
saferoom init                  # scans .env*, writes dummy-credential template + config
saferoom run                   # interactive sandbox shell — point your agent here
saferoom run -- claude -p "refactor the billing module"   # or one-shot
saferoom review                # audit: files changed, commands run, full diff
saferoom approve               # git-apply the reviewed patch (.env* always excluded)
```

## Commands

| Command | What it does |
|---|---|
| `saferoom init` | Build dummy `.env` template and `saferoom.json` config |
| `saferoom run` | Clone repo → mount dummy creds → Docker sandbox → log every command |
| `saferoom run --offline` | Same, with no network in the container |
| `saferoom run --image node:22` | Choose the sandbox image |
| `saferoom run --screenshot URL` | Full-page UI capture after the run (playwright) |
| `saferoom review [session]` | Print the audit report |
| `saferoom approve [session]` | Apply the session patch to your working tree |
| `saferoom sessions` | List past sessions |

## Guarantees

- Real credential values never enter the container — only generated dummies do.
- `approve` never writes `.env*` files back, even if the agent edited them.
- The sandbox is a throwaway clone; your working tree is untouched until approval.
- Everything is local. No telemetry, no account, no cloud.

## How SafeRoom relates to sandboxes you already use

E2B, Modal, and Daytona provide isolated compute for teams building agent
products. Claude Code's `/sandbox` and Cursor's sandbox mode contain the agent
while it runs. SafeRoom wraps around any of them: credential stripping, an
auditable record, and a required approval step before anything touches prod.
Use both.

## Roadmap

- [ ] Database mutation capture (proxy Postgres/MySQL in the sandbox)
- [ ] API-call interception and logging
- [ ] HTML audit reports
- [ ] Hosted session history + CI hooks (paid tier)
- [ ] Compliance-mode logs (HIPAA / SOC 2 evidence format)

## Pro

A hosted tier ($49/mo — session history, CI hooks, priority support) funds
development. The CLI stays MIT-licensed and fully functional forever.
→ https://getsaferoom.netlify.app

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and PRs welcome — especially
war stories about agents touching things they shouldn't have.

---
Built by [Kascade Security](https://www.kascadesecurity.com). MIT licensed.
