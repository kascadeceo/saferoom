# AGENTS.md — SafeRoom development guide for AI coding agents

You are working on **SafeRoom**, the sign-off layer for AI coding agents.
It clones a user's repo into an isolated Docker container with dummy
credentials, logs everything an agent does, and gates the results behind
human approval (`saferoom approve`). Auditability is the product. Yes, you
are an AI agent improving a tool that supervises AI agents — hold yourself
to its standard.

## Repo layout

```
saferoom.py                  # THE product — single-file CLI, stdlib only
install.sh                   # Debian/Ubuntu-first installer
.github/workflows/ci.yml     # syntax check + smoke tests (security assertions)
README.md / CONTRIBUTING.md  # public docs — keep in sync with behavior
launch/                      # founder's marketing playbook — NEVER commit or edit
```

`saferoom.py` internal map (top to bottom):
- constants, `say/die/sh` helpers, `repo_root`, `load_config`
- **init**: `DUMMY_PATTERNS`, `dummy_value`, `cmd_init` — .env scanning + templating
- **run helpers**: `clone_repo`, `swap_credentials`, `baseline_commit`,
  `IN_CONTAINER_SHELL`, `collect_audit`, `take_screenshot`
- **cmd_run**: session lifecycle (clone → swap → baseline → container → audit)
- **review / approve / sessions**: report rendering and the git-apply gate
- `main()`: argparse wiring

## Non-negotiable invariants

Violating any of these is a rejected change, no matter how good the feature:

1. **Zero runtime dependencies.** `saferoom.py` uses the Python stdlib only
   (3.8+). Optional integrations (playwright screenshots) must import lazily
   and degrade gracefully with a helpful message.
2. **Single file.** All product code stays in `saferoom.py`, readable in one
   sitting. Do not split into a package. If a function grows past ~40 lines,
   refactor within the file.
3. **Real credentials never enter the sandbox.** `swap_credentials` deletes
   every `.env*` from the clone and mounts only generated dummies. Any change
   to cloning, mounting, or env handling must preserve this and extend the CI
   assertions that prove it.
4. **`approve` never writes `.env*` or `.saferoom/` back** to the real repo.
   The exclude list in `cmd_approve` is a security control, not a convenience.
5. **Local-first, no telemetry.** No network calls from the CLI itself, no
   accounts, no analytics. `--offline` must keep meaning `--network none`.
6. **Audit integrity.** Everything the sandboxed process does must land in the
   session's audit (diff, command history). New capabilities must extend the
   audit, never bypass it.
7. **Git identity is pinned** via `GIT_ENV` so sandbox commits never use the
   user's identity. Keep it.

## Threat model (so you build the right thing)

The adversary is a **well-intentioned agent making catastrophic mistakes** with
live credentials and a live target — not hostile code escaping a kernel.
Docker is the isolation layer for this model; do not add complexity chasing
microVM-grade guarantees. Do, however, treat the credential swap and the
approve gate as security boundaries and reason about how an agent inside the
sandbox could trick a hasty reviewer (e.g., changes hidden in binary files,
misleading diffs, `.gitattributes` tricks). Hardening those paths is
high-value work.

## Build & test

No build step. Verify every change with:

```bash
python3 -m py_compile saferoom.py

# Smoke test WITHOUT Docker (--local = no isolation; test-only flag):
rm -rf /tmp/sr && mkdir /tmp/sr && cd /tmp/sr && git init -q
printf 'API_KEY=real-secret\n' > .env && printf 'print("hi")\n' > app.py
git add -A && git commit -qm init
python3 /path/to/saferoom.py init
python3 /path/to/saferoom.py run --local -- "echo new > new.txt && cat .env" | tee out.log
grep -q sr-dummy-secret out.log && ! grep -q real-secret out.log   # MUST both hold
python3 /path/to/saferoom.py review | grep -q new.txt
python3 /path/to/saferoom.py approve && grep -q real-secret .env    # real env untouched
```

If Docker is available, also run the container path:
`saferoom run --offline --image python:3.12-slim -- "cat .env"` and confirm
only dummies appear. CI (`.github/workflows/ci.yml`) mirrors these checks —
**when you add a security-relevant feature, add a CI assertion for it in the
same PR.**

Needed tools on Debian/Ubuntu: `apt-get install -y git python3 docker.io`.

## Roadmap (work top-down unless directed otherwise)

1. **DB mutation capture** — run a disposable Postgres/MySQL in the sandbox
   network, seed from a schema file, log queries (e.g. via `log_statement=all`),
   include a mutations section in `report.md`.
2. **API-call interception** — optional HTTP(S) proxy container; log outbound
   requests when not `--offline`; audit section listing hosts/methods.
3. **HTML audit report** — render `report.md` data to a single self-contained
   HTML file (inline CSS, no CDN). Keep the markdown report as source of truth.
4. **Podman support** — detect `podman` when `docker` is absent; same UX.
5. **Shell completions** (bash first), **more `DUMMY_PATTERNS`**.
6. **Compliance-mode logs** — flag that emits audit in an evidence-friendly
   format (timestamped JSONL). Design doc first; ask before implementing.

Do NOT build: hosted/cloud features, accounts, telemetry, or anything in
`launch/`. Those are founder decisions.

## Conventions

- Style: match the existing file — 4-space indent, f-strings, `pathlib`,
  double quotes, functions grouped under the `# ---- section` banners.
- Errors: user-facing failures go through `die(msg, hint=...)` with an
  actionable hint (Debian/Ubuntu install commands first, then macOS).
- CLI: new flags need `--help` text, a README table row, and a CONTRIBUTING
  or CI update if security-relevant. Keep flag names boring and lowercase.
- Commits: imperative mood, scope prefix — `run: add podman detection`,
  `audit: capture db mutations`, `ci: assert proxy log redaction`.
- Compatibility: don't break existing session dirs; `review`/`approve` must
  still work on sessions created by older versions where feasible.
- Every PR: state what changed, why, how you tested it, and — for anything
  touching invariants 3–6 — a one-paragraph threat note.

## When unsure

Prefer the smaller change. Prefer the auditable change. If a feature requires
breaking an invariant, stop and write up the tradeoff in the PR/issue instead
of implementing it.
