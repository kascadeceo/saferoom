# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | yes |

## Reporting a vulnerability

**Do not open a public issue for vulnerabilities.** Email
[security@kascadesecurity.com](mailto:security@kascadesecurity.com) with
details. You will get a response within **48 hours**.

Include reproduction steps and, if you have one, the session audit
(`.saferoom/sessions/<id>/report.md`) that demonstrates the problem.

## Scope

SafeRoom's security boundaries are:

- **The credential swap** — real `.env*` values must never enter the sandbox;
  `saferoom init` / `saferoom run` replace them with generated dummies.
- **The approve gate** — `saferoom approve` applies a reviewed patch and must
  never write `.env*` files or `.saferoom/` back to your real repo.

We especially welcome reports of these heuristics being bypassed — e.g. an
agent smuggling live credential values past the swap, or a crafted patch that
slips unreviewed changes through `approve` (binary files, `.gitattributes`
tricks, misleading diffs).

Out of scope: container/kernel escapes (that is Docker's isolation model, not
SafeRoom's) and anything in hosted or cloud features.
