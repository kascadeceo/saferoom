# Contributing to SafeRoom

Thanks for helping make agent workflows safer.

## Ground rules

- **Zero runtime dependencies.** `saferoom.py` stays stdlib-only. Optional
  features (screenshots) may soft-depend on installed packages but must degrade
  gracefully.
- **Single file, readable in one sitting.** Auditability is the product. If a
  change makes the file meaningfully harder to read, split the PR discussion
  from the code.
- **Security first.** Anything touching credential handling, the approve gate,
  or container flags gets extra scrutiny. Include a short threat note in the PR
  description for these.

## Dev setup

```bash
git clone https://github.com/kascadeceo/saferoom && cd saferoom
python3 saferoom.py --help
```

Smoke test without Docker (uses `--local`, no isolation — never use with a
real agent):

```bash
mkdir /tmp/sr-demo && cd /tmp/sr-demo && git init -q
printf 'API_KEY=real-secret\n' > .env && printf 'print("hi")\n' > app.py
git add -A && git commit -qm init
python3 /path/to/saferoom.py init
python3 /path/to/saferoom.py run --local -- "echo test > new.txt && cat .env"
python3 /path/to/saferoom.py review
```

Expected: `cat .env` shows dummies, the audit lists `new.txt`, approve applies it.

## Reporting security issues

Do not open a public issue for vulnerabilities. Email security@kascadesecurity.com with
details; you'll get a response within 48 hours.

## Good first issues

- Additional dummy-value patterns for `saferoom init`
- Shell-completion scripts (bash/zsh)
- HTML rendering of `report.md`
- Podman support
