#!/usr/bin/env python3
"""
SafeRoom — a controlled playground for AI coding agents.

Clones your repo into an isolated sandbox, swaps live credentials for dummies,
runs the agent inside a Docker container, and produces a full audit trail:
files changed, commands run, clean git diffs. You approve what ships.

Usage:
  saferoom init                      Prepare config + dummy-credential template
  saferoom run [opts] [-- CMD...]    Start a sandboxed session (interactive or one-shot)
  saferoom review [SESSION] [--html] Show the audit report; --html writes report.html (Pro)
  saferoom approve [SESSION]         Apply the approved diff to your real repo
  saferoom export [SESSION]          --compliance writes hash-chained evidence.jsonl (Pro)
  saferoom sessions                  List past sessions
  saferoom clean [--keep N]          Remove old sessions, keep the newest N (default 10)
  saferoom license ...               eval | activate KEY | status (activate is the only
                                     network call the CLI ever makes, run explicitly once)

Run options:
  --image IMAGE      Docker image for the sandbox (default: from config, else python:3.12-slim)
  --offline          No network inside the container (--network none)
  --local            DEV/TEST ONLY: run on host with no container isolation
  --screenshot URL   Capture a UI screenshot after the run (needs playwright installed)

MVP scope: Docker orchestration, .env credential swapping, command logging,
git-based diff auditing. Linux-first (Debian/Ubuntu), works anywhere Docker runs.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from pathlib import Path

__version__ = "0.2.0"

SR_DIR = ".saferoom"
CONFIG_FILE = "saferoom.json"
ENV_TEMPLATE = ".env.saferoom"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "SafeRoom",
    "GIT_AUTHOR_EMAIL": "audit@saferoom.local",
    "GIT_COMMITTER_NAME": "SafeRoom",
    "GIT_COMMITTER_EMAIL": "audit@saferoom.local",
}

C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "b": "\033[36m", "0": "\033[0m"}


def say(msg, color="b"):
    print(f"{C[color]}[saferoom]{C['0']} {msg}")


def die(msg, hint=None):
    say(msg, "r")
    if hint:
        print(f"  hint: {hint}")
    sys.exit(1)


def sh(cmd, cwd=None, check=True, capture=True, env=None):
    e = os.environ.copy()
    e.update(GIT_ENV)
    if env:
        e.update(env)
    return subprocess.run(
        cmd, cwd=cwd, check=check, env=e,
        capture_output=capture, text=True,
    )


def repo_root():
    root = Path.cwd()
    result = sh(["git", "rev-parse", "--show-toplevel"], cwd=root, check=False)
    if result.returncode == 0:
        return Path(result.stdout.strip())
    if not (root / ".git").exists():
        say("current directory is not a git repo — SafeRoom will still sandbox it, "
            "but your real repo should be under git.", "y")
    return root


def load_config(root):
    cfg_path = root / CONFIG_FILE
    if cfg_path.exists():
        return json.loads(cfg_path.read_text())
    return {}


# ---------------------------------------------------------------- init

DUMMY_PATTERNS = [
    (re.compile(r"key|secret|token|password|passwd|pwd", re.I), "sr-dummy-secret"),
    (re.compile(r"url|uri|host|endpoint", re.I), "http://saferoom-stub.local"),
    (re.compile(r"user|email", re.I), "saferoom-user"),
    (re.compile(r"port", re.I), "5432"),
]


def dummy_value(key):
    for pat, val in DUMMY_PATTERNS:
        if pat.search(key):
            return val
    return "sr-dummy-value"


def find_env_files(root):
    """All .env* files under root, recursively — excluding the SafeRoom
    template, session state (.saferoom/), and git internals (.git/)."""
    found = []
    for p in sorted(root.rglob(".env*")):
        top = p.relative_to(root).parts[0]
        if p.is_file() and p.name != ENV_TEMPLATE and top not in (SR_DIR, ".git"):
            found.append(p)
    return found


def cmd_init(args):
    root = repo_root()
    env_files = find_env_files(root)
    lines = ["# SafeRoom dummy credentials — mounted into every sandbox as .env",
             "# Real values never enter the container. Edit stand-ins as needed.", ""]
    keys = 0
    for ef in env_files:
        lines.append(f"# from {ef.relative_to(root)}")
        for raw in ef.read_text().splitlines():
            s = raw.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key = s.split("=", 1)[0].strip()
            lines.append(f"{key}={dummy_value(key)}")
            keys += 1
        lines.append("")
    if not env_files:
        lines += ["# No .env files found — add stand-in variables your app expects:",
                  "# DATABASE_URL=http://saferoom-stub.local", ""]
    (root / ENV_TEMPLATE).write_text("\n".join(lines))

    cfg = load_config(root)
    cfg.setdefault("image", "python:3.12-slim")
    cfg.setdefault("workdir", "/workspace")
    cfg.setdefault("offline", False)
    (root / CONFIG_FILE).write_text(json.dumps(cfg, indent=2) + "\n")

    gi = root / ".gitignore"
    marker = f"{SR_DIR}/"
    if not gi.exists() or marker not in gi.read_text():
        with gi.open("a") as f:
            f.write(f"\n# SafeRoom sandbox sessions\n{marker}\n")

    say(f"wrote {ENV_TEMPLATE} ({keys} live credential(s) replaced with dummies)", "g")
    say(f"wrote {CONFIG_FILE} (sandbox image: {cfg['image']})", "g")
    say("next: saferoom run   (interactive)   or   saferoom run -- <agent command>", "g")


# ---------------------------------------------------------------- run helpers

def clone_repo(root, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if (root / ".git").exists():
        sh(["git", "clone", "--local", "--no-hardlinks", "--quiet", str(root), str(dest)])
    else:
        shutil.copytree(root, dest, ignore=shutil.ignore_patterns(SR_DIR, ".git"))


def swap_credentials(root, sandbox_repo):
    env_files = find_env_files(sandbox_repo)
    swapped = [str(ef.relative_to(sandbox_repo)) for ef in env_files]
    for ef in env_files:
        ef.unlink()
    template = root / ENV_TEMPLATE
    if template.exists():
        shutil.copy(template, sandbox_repo / ".env")
    return swapped


def baseline_commit(sandbox_repo):
    if not (sandbox_repo / ".git").exists():
        sh(["git", "init", "--quiet"], cwd=sandbox_repo)
    sh(["git", "add", "-A"], cwd=sandbox_repo)
    sh(["git", "commit", "--quiet", "--allow-empty", "-m", "saferoom: baseline"],
       cwd=sandbox_repo)
    return sh(["git", "rev-parse", "HEAD"], cwd=sandbox_repo).stdout.strip()


def docker_available():
    return shutil.which("docker") is not None


IN_CONTAINER_SHELL = (
    'export HISTFILE=/workspace/{sr}/history; export HISTTIMEFORMAT="%F %T  "; '
    'export PROMPT_COMMAND="history -a"; '
    'if command -v script >/dev/null 2>&1; then '
    'exec script -q -a /workspace/{sr}/tty.log -c "bash -i"; '
    'else exec bash -i; fi'
)


def collect_audit(sandbox_repo, base, session_dir, meta):
    sh(["git", "add", "-A"], cwd=sandbox_repo)
    spec = ["--", ".", f":!{SR_DIR}", ":!.env", ":!.env.*"]
    diff = sh(["git", "diff", "--cached", base, *spec], cwd=sandbox_repo).stdout
    stat = sh(["git", "diff", "--cached", "--stat", base, *spec], cwd=sandbox_repo).stdout
    names = sh(["git", "diff", "--cached", "--name-status", base, *spec], cwd=sandbox_repo).stdout

    (session_dir / "changes.patch").write_text(diff)
    hist = sandbox_repo / SR_DIR / "history"
    commands = hist.read_text() if hist.exists() else ""
    cmd_log = session_dir / "commands.log"
    if commands:
        cmd_log.write_text(commands)

    meta.update({
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files_changed": [l for l in names.splitlines() if l.strip()],
    })
    (session_dir / "report.json").write_text(json.dumps(meta, indent=2) + "\n")

    lines = [
        f"# SafeRoom audit — session {meta['session']}",
        "",
        f"- Started:  {meta['started_utc']}",
        f"- Finished: {meta['finished_utc']}",
        f"- Isolation: {meta['isolation']}",
        f"- Image: {meta.get('image', '—')}",
        f"- Credentials swapped: {', '.join(meta['credentials_swapped']) or 'none found'}",
        "",
        "## Files changed",
        "```",
        names.strip() or "(no changes)",
        "",
        stat.strip(),
        "```",
        "",
        "## Commands run in sandbox",
        "```",
        commands.strip() or "(no shell history captured)",
        "```",
        "",
        "## Full diff",
        "```diff",
        diff.strip() or "(empty)",
        "```",
        "",
        f"Approve with:  saferoom approve {meta['session']}",
    ]
    report = session_dir / "report.md"
    report.write_text("\n".join(lines))
    return report, names


def take_screenshot(url, session_dir):
    try:
        from playwright.sync_api import sync_playwright  # noqa
    except ImportError:
        say("screenshot skipped — install with: pip install playwright && playwright install chromium", "y")
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1280, "height": 800})
        page.goto(url, wait_until="networkidle")
        out = session_dir / "screenshot.png"
        page.screenshot(path=str(out), full_page=True)
        b.close()
    say(f"screenshot saved: {out}", "g")


# ---------------------------------------------------------------- run

def cmd_run(args):
    root = repo_root()
    cfg = load_config(root)
    image = args.image or cfg.get("image", "python:3.12-slim")
    offline = args.offline or cfg.get("offline", False)

    session = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    session_dir = root / SR_DIR / "sessions" / session
    sandbox_repo = session_dir / "repo"
    session_dir.mkdir(parents=True, exist_ok=True)

    say(f"session {session} — cloning repo into sandbox")
    clone_repo(root, sandbox_repo)
    swapped = swap_credentials(root, sandbox_repo)
    (sandbox_repo / SR_DIR).mkdir(exist_ok=True)
    base = baseline_commit(sandbox_repo)
    say(f"credentials swapped: {', '.join(swapped) or 'none found'} "
        f"({'dummy .env mounted' if (sandbox_repo / '.env').exists() else 'no .env in sandbox'})")

    meta = {
        "session": session,
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "baseline": base,
        "credentials_swapped": swapped,
        "isolation": "none (--local, testing only)" if args.local
                     else f"docker ({'network: none' if offline else 'network: bridge'})",
        "image": None if args.local else image,
        "one_shot": args.cmd or None,
    }

    if args.local:
        say("LOCAL MODE — no container isolation. Testing only.", "y")
        env = os.environ.copy()
        env["HISTFILE"] = str(sandbox_repo / SR_DIR / "history")
        if args.cmd:
            joined = " ".join(args.cmd)
            (sandbox_repo / SR_DIR / "history").write_text(joined + "\n")
            meta["exit_code"] = subprocess.run(joined, shell=True, cwd=sandbox_repo, env=env).returncode
        else:
            say("interactive sandbox shell — exit to finish and audit")
            subprocess.run(
                ["bash", "-c",
                 f'export HISTFILE="{sandbox_repo / SR_DIR / "history"}"; '
                 'export PROMPT_COMMAND="history -a"; exec bash -i'],
                cwd=sandbox_repo, env=env)
    else:
        if not docker_available():
            die("docker not found.",
                "Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y docker.io "
                "(or Docker Engine from docs.docker.com). macOS/Windows: Docker Desktop.")
        name = f"saferoom-{session}"
        run_cmd = ["docker", "run", "-d", "--name", name,
                   "-v", f"{sandbox_repo}:/workspace", "-w", "/workspace"]
        if offline:
            run_cmd += ["--network", "none"]
        run_cmd += [image, "sleep", "infinity"]
        say(f"starting container {name} ({image}"
            f"{', offline' if offline else ''})")
        try:
            sh(run_cmd)
            if args.cmd:
                joined = " ".join(args.cmd)
                (sandbox_repo / SR_DIR / "history").write_text(joined + "\n")
                say(f"running: {joined}")
                meta["exit_code"] = subprocess.run(["docker", "exec", name, "bash", "-lc", joined]).returncode
            else:
                say("interactive sandbox shell — exit to finish and audit")
                shell = IN_CONTAINER_SHELL.format(sr=SR_DIR)
                subprocess.run(["docker", "exec", "-it", name, "bash", "-c", shell])
        finally:
            subprocess.run(["docker", "rm", "-f", name],
                           capture_output=True, text=True)

    if args.screenshot:
        take_screenshot(args.screenshot, session_dir)

    report, names = collect_audit(sandbox_repo, base, session_dir, meta)
    say(f"audit ready: {report}", "g")
    print()
    print(names.strip() or "(no file changes)")
    print()
    say(f"review:  saferoom review {session}")
    say(f"approve: saferoom approve {session}")


# ---------------------------------------------------------------- review / approve / sessions

def latest_session(root):
    d = root / SR_DIR / "sessions"
    if not d.exists():
        die("no sessions yet — run: saferoom run")
    sessions = sorted(p.name for p in d.iterdir() if p.is_dir())
    if not sessions:
        die("no sessions yet — run: saferoom run")
    return sessions[-1]


def cmd_review(args):
    root = repo_root()
    session = args.session or latest_session(root)
    session_dir = root / SR_DIR / "sessions" / session
    report = session_dir / "report.md"
    if not report.exists():
        die(f"no report for session {session}")
    print(report.read_text())
    if not args.html:
        return
    payload = require_pro(root, "review --html")
    out = session_dir / "report.html"
    out.write_text(report_page(report.read_text(), session, payload["tier"] == "eval"))
    say(f"HTML report written: {out}", "g")


def host_git_config(root, key, fallback):
    """Read the host user's git config value (fallback if unset)."""
    out = sh(["git", "config", "--get", key], cwd=root, check=False).stdout.strip()
    return out or fallback


def record_approval(root, session, patch, decision="approved", reason=None, binding=None):
    """Append the review evidence: who decided, when, exactly what (patch hash)."""
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    name = host_git_config(root, "user.name", os.environ.get("USER", "unknown"))
    email = host_git_config(root, "user.email", "unknown")
    approved_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    files_changed = sum(1 for line in patch.read_text().splitlines()
                        if line.startswith("diff --git "))
    record = {
        "session": session,
        "decision": decision,
        "decided_utc": approved_utc,
        "approver_name": name,
        "approver_email": email,
        "patch_sha256": digest,
        "files_changed_count": files_changed,
    }
    if binding:
        record.update(binding)
    if reason:
        record["reason"] = reason
    session_dir = patch.parent
    with (session_dir / "approvals.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")
    report = session_dir / "report.md"
    if report.exists():
        verb = {"approved": "Approved", "rejected": "Rejected", "dismissed": "Dismissed"}[decision]
        lead = "" if report.read_text().endswith("\n") else "\n"
        with report.open("a") as f:
            f.write(f"{lead}**{verb}** by {name} at {approved_utc} — "
                    f"patch sha256 {digest}\n")
    return digest


def session_path(root, name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
        die("invalid session identifier")
    path = root / SR_DIR / "sessions" / name
    if path.is_symlink():
        die("session symlinks are not supported")
    return path


def load_decisions(session_dir):
    f = session_dir / "approvals.jsonl"
    if not f.exists():
        return []
    records = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    if any(not isinstance(r, dict) for r in records):
        raise ValueError("invalid decision record")
    return records


def review_state(session_dir):
    """Fail closed. Only the latest explicit, hash-matched decision is usable."""
    try:
        patch = session_dir / "changes.patch"
        if patch.is_symlink() or (session_dir / "approvals.jsonl").is_symlink():
            return "invalid", None
        if not patch.is_file():
            return "missing_patch", None
        data = patch.read_bytes()
        records = load_decisions(session_dir)
        if not records:
            return ("pending" if data.strip() else "no_changes"), None
        r = records[-1]
        if r.get("decision") not in ("approved", "rejected", "dismissed"):
            return "unknown", None
        if r.get("patch_sha256") != hashlib.sha256(data).hexdigest():
            return "stale", None
        if r["decision"] == "approved" and not all(
                isinstance(r.get(k), str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", r[k])
                for k in ("base_commit", "approved_tree")):
            return "unbound_approval", None
        return r["decision"], r
    except (OSError, ValueError, TypeError):
        return "invalid", None


def session_dirs(root):
    d = root / SR_DIR / "sessions"
    if d.is_symlink():
        die("session store symlinks are not supported")
    return sorted(session_path(root, p.name) for p in d.iterdir() if p.is_dir()) if d.exists() else []


def pending_sessions(root):
    return [p.name for p in session_dirs(root)
            if review_state(p)[0] not in ("approved", "rejected", "dismissed", "no_changes")]


def approval_binding(root, patch):
    if sh(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root).stdout.strip():
        die("approval requires a clean tracked worktree and index; commit or resolve existing work first")
    base = sh(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    # A disposable index computes exactly the reviewed result, never staging other work.
    with tempfile.TemporaryDirectory(prefix="saferoom-index-") as d:
        env = {"GIT_INDEX_FILE": str(Path(d) / "index")}
        sh(["git", "read-tree", base], cwd=root, env=env)
        sh(["git", "apply", "--cached", *approval_excludes(), str(patch)], cwd=root, env=env)
        tree = sh(["git", "write-tree"], cwd=root, env=env).stdout.strip()
    return {"base_commit": base, "approved_tree": tree}


def approval_excludes():
    return ["--exclude=.env", "--exclude=.env.*", "--exclude=**/.env", "--exclude=**/.env.*",
            f"--exclude={SR_DIR}/*", "--exclude=.git/*"]


def cmd_approve(args):
    root = repo_root()
    session = args.session or latest_session(root)
    patch = session_path(root, session) / "changes.patch"
    if patch.is_symlink() or not patch.is_file() or not patch.read_bytes().strip():
        die("session has no regular patch to approve")
    try:
        binding = approval_binding(root, patch)
        sh(["git", "apply", "--check", *approval_excludes(), str(patch)], cwd=root)
        sh(["git", "apply", *approval_excludes(), str(patch)], cwd=root)
    except subprocess.CalledProcessError:
        die("reviewed patch does not apply cleanly; no approval recorded")
    record_approval(root, session, patch, binding=binding)
    say("approved patch applied to working tree; commit exactly this tree before pushing", "g")


def cmd_sessions(args):
    root = repo_root()
    rows = []
    for p in session_dirs(root):
        state, decision = review_state(p)
        try:
            meta = json.loads((p / "report.json").read_text())
            if not isinstance(meta, dict):
                meta = {}
        except (OSError, ValueError):
            meta = {}
        files = meta.get("files_changed", [])
        code = meta.get("exit_code")
        rows.append({"session": p.name, "files_changed": len(files) if isinstance(files, list) else 0,
                     "decision": state, "has_patch": state != "no_changes",
                     "checks": "command_passed" if type(code) is int and code == 0 else
                               "command_failed" if type(code) is int else "not_recorded",
                     "patch_sha256": (decision or {}).get("patch_sha256"),
                     "report": str(p / "report.md"),
                     "decisions": str(p / "approvals.jsonl")})
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{r['session']} files={r['files_changed']} review={r['decision']} checks={r['checks']}")


def gate_config(root):
    directory = Path(sh(["git", "rev-parse", "--absolute-git-dir"], cwd=root).stdout.strip())
    return directory / "saferoom-gate.json"


def install_gate(root, trusted_base):
    if not trusted_base:
        die("install requires --trusted-base COMMIT: explicitly trust existing history")
    if sh(["git", "config", "--get", "core.hooksPath"], cwd=root, check=False).stdout.strip():
        die("custom core.hooksPath detected; refuse to replace existing hook configuration")
    config = gate_config(root)
    hook = config.parent / "hooks" / "pre-push"
    if hook.exists() or config.exists():
        die("hook or gate configuration already exists; review manually instead of overwriting")
    base = sh(["git", "rev-parse", "--verify", trusted_base + "^{commit}"], cwd=root).stdout.strip()
    if sh(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=root, check=False).returncode:
        die("trusted base must be an ancestor of HEAD")
    hook.parent.mkdir(parents=True, exist_ok=True)
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), "gate", "--pre-push"])
    hook.write_text("#!/bin/sh\n# SafeRoom local review gate; do not claim server-side enforcement.\nexec " + command + "\n")
    hook.chmod(0o700)
    config.write_text(json.dumps({"version": 1, "trusted_base": base}) + "\n")
    config.chmod(0o600)
    say("local pre-push gate installed; history through the explicit base is trusted", "g")


def check_outgoing(root, approvals, lines):
    try:
        config = json.loads(gate_config(root).read_text())
        base = config["trusted_base"]
        if config.get("version") != 1 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base):
            raise ValueError()
        for line in lines:
            local_ref, local_oid, remote_ref, remote_oid = line.split()
            if not remote_ref.startswith("refs/heads/"):
                die("gate blocks tag pushes and ref deletions; separate review required")
            if not all(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", v) for v in (local_oid, remote_oid)):
                raise ValueError()
            if set(local_oid) == {"0"}:
                die("gate blocks ref deletion")
            if sh(["git", "merge-base", "--is-ancestor", base, local_oid], cwd=root, check=False).returncode:
                die("outgoing history is outside the trusted baseline")
            start = base
            if set(remote_oid) != {"0"}:
                if sh(["git", "merge-base", "--is-ancestor", remote_oid, local_oid], cwd=root, check=False).returncode:
                    die("non-fast-forward or unavailable remote history requires separate review")
                if sh(["git", "merge-base", "--is-ancestor", base, remote_oid], cwd=root, check=False).returncode == 0:
                    start = remote_oid
            commits = sh(["git", "rev-list", "--reverse", f"{start}..{local_oid}"], cwd=root).stdout.splitlines()
            for commit in commits:
                parents = sh(["git", "show", "-s", "--format=%P", commit], cwd=root).stdout.split()
                tree = sh(["git", "show", "-s", "--format=%T", commit], cwd=root).stdout.strip()
                if len(parents) != 1 or not any(r["base_commit"] == parents[0] and r["approved_tree"] == tree for r in approvals):
                    die("outgoing commit is not exactly covered by a current approval; push blocked")
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        die("invalid push input or gate configuration; push blocked")


def cmd_gate(args):
    root = repo_root()
    if args.install_hook:
        install_gate(root, args.trusted_base)
        return
    if args.dismiss or args.reject:
        session = args.dismiss or args.reject
        patch = session_path(root, session) / "changes.patch"
        if patch.is_symlink() or not patch.is_file() or not patch.read_bytes().strip():
            die("no regular patch to dismiss or reject")
        if review_state(patch.parent)[0] == "approved":
            die("applied approval cannot be dismissed; inspect/revert the work explicitly")
        decision = "dismissed" if args.dismiss else "rejected"
        record_approval(root, session, patch, decision=decision, reason=args.reason)
        say("review closed without applying work; this decision authorizes no outgoing commit", "y")
        return
    pending = pending_sessions(root)
    if pending:
        die("unreviewed, stale, unknown or malformed session evidence; push blocked")
    if args.pre_push:
        approvals = [r for p in session_dirs(root) for state, r in [review_state(p)] if state == "approved"]
        check_outgoing(root, approvals, sys.stdin.read().splitlines())
    say("gate passed" + (" for the supplied outgoing commits" if args.pre_push else " (session review only; not push authorization)"), "g")


def cmd_clean(args):
    root = repo_root()
    d = root / SR_DIR / "sessions"
    if not d.exists():
        say("no sessions yet — nothing to clean")
        return
    dirs = sorted(p for p in d.iterdir() if p.is_dir())
    keep = max(args.keep, 0)
    doomed = dirs if keep == 0 else dirs[:-keep]
    if not doomed:
        say(f"nothing to clean — {len(dirs)} session(s) on disk, keeping {keep}")
        return
    freed = sum(os.path.getsize(f) for p in doomed for f in p.rglob("*") if f.is_file())
    for p in doomed:
        shutil.rmtree(p)
    say(f"removed {len(doomed)} session(s), kept the newest {keep} — "
        f"{freed / 1048576:.1f} MiB freed", "g")


# ---------------------------------------------------------------- pro: license

# A license key is "SR-<base64url payload>-<base64url HMAC-SHA256(payload)>",
# signed with a secret held by the seller. Eval keys are signed with the public
# secret below so anyone can try Pro locally; paid keys are verified once
# against the hosted endpoint during explicit `license activate`, then cached.
EVAL_SECRET = b"saferoom-eval-v1"
LICENSE_TIERS = ("eval", "pro-mo", "pro-yr", "lifetime")
DEFAULT_LICENSE_URL = "https://getsaferoom.netlify.app/api/license/verify"


def b64u_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def decode_license(key):
    # Signature is base64url(32 bytes) = exactly 43 chars; anchoring it makes
    # the split unambiguous because payload bodies may themselves contain "-".
    m = re.fullmatch(r"SR-([A-Za-z0-9_-]+)-([A-Za-z0-9_-]{43})", (key or "").strip())
    if not m:
        die("not a SafeRoom license key", "expected shape: SR-<payload>-<signature>")
    try:
        payload = json.loads(b64u_decode(m.group(1)))
    except (ValueError, TypeError):
        die("license payload is unreadable", "copy the full key from your purchase email")
    if not isinstance(payload, dict) or payload.get("v") != 1 \
            or payload.get("tier") not in LICENSE_TIERS:
        die("unsupported license payload")
    return payload, m.group(1), m.group(2)


def license_sig_ok(body, sig, secret):
    want = b64u_encode(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return hmac.compare_digest(want, sig)


def mint_eval_license(email, days):
    exp = (datetime.now(timezone.utc) + timedelta(days=max(days, 1))).strftime("%Y-%m-%d")
    payload = {"v": 1, "tier": "eval", "email": email, "exp": exp}
    body = b64u_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = b64u_encode(hmac.new(EVAL_SECRET, body.encode("ascii"), hashlib.sha256).digest())
    return f"SR-{body}-{sig}"


def hosted_verify(key):
    """The CLI's only network call, ever: one explicit POST during activate."""
    import urllib.request  # local import keeps the network surface grep-auditable
    url = os.environ.get("SAFEROOM_LICENSE_URL", DEFAULT_LICENSE_URL)
    req = urllib.request.Request(url, data=json.dumps({"key": key}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("valid") is True
    except Exception as exc:
        die("hosted license check failed",
            f"retry later, or activate without the check: saferoom license activate <KEY> --offline ({exc})")


def require_pro(root, feature):
    """Pro features stay local; an activated license unlocks them (goodwill, not DRM)."""
    lic = root / SR_DIR / "license.json"
    if not lic.exists():
        die(f"{feature} is a SafeRoom Pro feature",
            "free eval: saferoom license eval --email you@team, then saferoom license activate <KEY>")
    rec = json.loads(lic.read_text())
    payload, _, _ = decode_license(rec.get("key"))
    exp = payload.get("exp")
    if exp and exp < datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        die("license expired", "renew at https://getsaferoom.netlify.app or mint a fresh eval key")
    return payload


def cmd_license(args):
    root = repo_root()
    lic = root / SR_DIR / "license.json"
    if args.action == "eval":
        print(mint_eval_license(args.email, args.days))
        return
    if args.action == "status":
        if not lic.exists():
            die("no license activated", "start free: saferoom license eval --email you@team")
        rec = json.loads(lic.read_text())
        p = rec.get("payload", {})
        say(f"tier={p.get('tier')} seat={p.get('email', '—')} expires={p.get('exp', 'never')} "
            f"hosted_verified={rec.get('hosted_verified')} activated={rec.get('activated_utc')}")
        return
    if not args.key:
        die("activate needs a key", "saferoom license activate SR-...")
    payload, body, sig = decode_license(args.key)
    rec = {"key": args.key.strip(), "payload": payload,
           "activated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "hosted_verified": False}
    if payload["tier"] == "eval":
        if not license_sig_ok(body, sig, EVAL_SECRET):
            die("evaluation key signature invalid",
                "mint a fresh one: saferoom license eval --email you@team")
        rec["hosted_verified"] = True
    elif not args.offline:
        rec["hosted_verified"] = hosted_verify(rec["key"])
    lic.parent.mkdir(parents=True, exist_ok=True)
    lic.write_text(json.dumps(rec, indent=2) + "\n")
    say(f"license activated — tier={payload['tier']}, expires={payload.get('exp', 'never')}", "g")


# ---------------------------------------------------------------- pro: html report

REPORT_CSS = (
    "body{font:15px/1.6 -apple-system,'Segoe UI',Roboto,sans-serif;color:#e6edf3;"
    "background:#0d1117;max-width:60rem;margin:2rem auto;padding:0 1.25rem}"
    "h1{font-size:1.5rem;border-bottom:1px solid #30363d;padding-bottom:.4rem}"
    "h2{font-size:1.15rem;margin-top:2rem;color:#79c0ff}"
    "ul{list-style:square}li{margin:.15rem 0}"
    ".code{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:.9rem;"
    "overflow-x:auto;white-space:pre-wrap;word-break:break-word}"
    ".diff .add{color:#3fb950}.diff .del{color:#f85149}.diff .meta{color:#bb8009}"
    ".eval{background:#bb8009;color:#0d1117;font-weight:600;padding:.5rem .9rem;"
    "border-radius:6px;margin-bottom:1rem}"
    "footer{margin-top:3rem;color:#8b949e;font-size:.85rem;border-top:1px solid #30363d;"
    "padding-top:.8rem}"
)


def render_inline(line):
    line = html_escape(line)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)


def diff_span(line):
    cls = ("add" if line.startswith("+") else "del" if line.startswith("-")
           else "meta" if line.startswith("@@") else None)
    return f'<span class="{cls}">{html_escape(line)}</span>' if cls else html_escape(line)


def report_body_html(md_text):
    out, in_code, in_list, code_diff = [], False, False, False
    for line in md_text.splitlines():
        if line.startswith("```"):
            if in_code:
                out.append("</pre>")
            else:
                code_diff = line.strip() == "```diff"
                out.append(f'<pre class="code{" diff" if code_diff else ""}">')
            in_code, in_list = not in_code, False
            continue
        if in_code:
            out.append(diff_span(line) if code_diff else html_escape(line))
            continue
        if line.startswith("## "):
            if in_list:
                out.append("</ul>")
            out.append(f"<h2>{render_inline(line[3:])}</h2>")
            in_list = False
        elif line.startswith("# "):
            if in_list:
                out.append("</ul>")
            out.append(f"<h1>{render_inline(line[2:])}</h1>")
            in_list = False
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
            out.append(f"<li>{render_inline(line[2:])}</li>")
            in_list = True
        elif line.strip():
            if in_list:
                out.append("</ul>")
            out.append(f"<p>{render_inline(line)}</p>")
            in_list = False
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def report_page(md_text, session, eval_copy):
    banner = ('<div class="eval">Evaluation copy — free eval license. '
              'Pro: https://getsaferoom.netlify.app</div>') if eval_copy else ""
    return ("<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            f"<title>SafeRoom audit — {html_escape(session)}</title>"
            f"<style>{REPORT_CSS}</style></head><body>{banner}"
            f"{report_body_html(md_text)}"
            "<footer>SafeRoom — the sign-off layer for AI coding agents</footer>"
            "</body></html>\n")


# ---------------------------------------------------------------- pro: compliance export


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def chained_record(seq, prev, event):
    rec = {"seq": seq, "prev_sha256": prev, **event}
    rec["sha256"] = hashlib.sha256(canonical_json(rec).encode()).hexdigest()
    return rec


def verify_evidence(path):
    prev, count = "0" * 64, 0
    for count, line in enumerate(path.read_text().splitlines(), 1):
        rec = json.loads(line)
        claimed = rec.pop("sha256", None)
        if rec.get("seq") != count or rec.get("prev_sha256") != prev \
                or claimed != hashlib.sha256(canonical_json(rec).encode()).hexdigest():
            die(f"evidence chain broken at record {count}")
        prev = claimed
    if not count:
        die("evidence file is empty")
    say(f"evidence chain verified — {count} record(s), tail sha256 {prev[:16]}…", "g")


def export_events(session_dir, meta):
    """One event per recorded fact, in the order the session produced them.
    Command timestamps use the session's UTC clock: shell history carries no
    reliable timezone, so the raw line is kept verbatim in detail.command."""
    session = meta.get("session", session_dir.name)
    events = [
        {"ts": meta.get("started_utc"), "actor": "saferoom", "event": "run", "session": session,
         "detail": {k: meta.get(k) for k in
                    ("isolation", "image", "baseline", "exit_code", "finished_utc")}},
        {"ts": meta.get("finished_utc"), "actor": "sandbox-agent", "event": "diff",
         "session": session,
         "detail": {"files": meta.get("files_changed"),
                    "patch_sha256": file_sha256(session_dir / "changes.patch")}},
    ]
    cmdlog = session_dir / "commands.log"
    if cmdlog.exists():
        for line in cmdlog.read_text().splitlines():
            if line.strip():
                events.append({"ts": meta.get("finished_utc"), "actor": "sandbox-agent",
                               "event": "command", "session": session, "detail": {"command": line}})
    for rec in load_decisions(session_dir):
        events.append({"ts": rec.get("decided_utc"),
                       "actor": f"{rec.get('approver_name')} <{rec.get('approver_email')}>",
                       "event": "decision", "session": session, "detail": rec})
    return events


def cmd_export(args):
    root = repo_root()
    if args.verify is not None:
        target = Path(args.verify) if args.verify else \
            (session_path(root, args.session or latest_session(root)) / "evidence.jsonl")
        if not target.is_file():
            die(f"no evidence file at {target}", "write one first: saferoom export --compliance")
        verify_evidence(target)
        return
    if not args.compliance:
        die("nothing to export", "try: saferoom export --compliance")
    payload = require_pro(root, "export --compliance")
    session = args.session or latest_session(root)
    session_dir = session_path(root, session)
    meta = json.loads((session_dir / "report.json").read_text())
    events = export_events(session_dir, meta)
    events.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "actor": "saferoom-cli", "event": "export", "session": session,
                   "detail": {"version": __version__, "eval": payload["tier"] == "eval"}})
    prev, lines = "0" * 64, []
    for seq, event in enumerate(events, 1):
        rec = chained_record(seq, prev, event)
        lines.append(canonical_json(rec))
        prev = rec["sha256"]
    out = session_dir / "evidence.jsonl"
    out.write_text("\n".join(lines) + "\n")
    say(f"evidence written: {out} ({len(lines)} records)", "g")
    verify_evidence(out)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(prog="saferoom", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"saferoom {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="prepare config + dummy-credential template")

    rp = sub.add_parser("run", help="start a sandboxed session")
    rp.add_argument("--image")
    rp.add_argument("--offline", action="store_true")
    rp.add_argument("--local", action="store_true")
    rp.add_argument("--screenshot", metavar="URL")
    rp.add_argument("cmd", nargs="*", help="one-shot command after --")

    vp = sub.add_parser("review", help="show audit report")
    vp.add_argument("session", nargs="?")
    vp.add_argument("--html", action="store_true",
                    help="also write a self-contained report.html (Pro)")

    apv = sub.add_parser("approve", help="apply approved diff to real repo")
    apv.add_argument("session", nargs="?")

    ep = sub.add_parser("export", help="export session evidence for auditors")
    ep.add_argument("session", nargs="?")
    ep.add_argument("--compliance", action="store_true",
                    help="write hash-chained evidence.jsonl (Pro)")
    ep.add_argument("--verify", metavar="FILE", nargs="?", const="", default=None,
                    help="verify an evidence chain (default: latest session's evidence.jsonl)")

    lp = sub.add_parser("license", help="manage the SafeRoom Pro license")
    lp.add_argument("action", choices=["eval", "activate", "status"])
    lp.add_argument("key", nargs="?", help="license key (for activate)")
    lp.add_argument("--email", default="eval@localhost", help="seat email for eval keys")
    lp.add_argument("--days", type=int, default=30, help="eval key validity (default 30)")
    lp.add_argument("--offline", action="store_true",
                    help="activate without the one-time hosted check (paid keys activate unverified)")

    sp = sub.add_parser("sessions", help="list sessions")
    sp.add_argument("--json", action="store_true",
                    help="machine-readable session list with review state")

    gp = sub.add_parser("gate", help="validate session reviews and exact outgoing commits")
    mode = gp.add_mutually_exclusive_group()
    mode.add_argument("--dismiss", metavar="SESSION", help="close unapplied work; grants no shipping approval")
    mode.add_argument("--reject", metavar="SESSION", help="reject unapplied work; grants no shipping approval")
    mode.add_argument("--pre-push", action="store_true", help="read Git pre-push updates on stdin")
    mode.add_argument("--install-hook", action="store_true", help="install opt-in local hook; refuses overwrites")
    gp.add_argument("--trusted-base", help="existing commit whose history is explicitly trusted at installation")
    gp.add_argument("--reason", help="local review reason; not shown in task summaries")

    cp = sub.add_parser("clean", help="remove old sessions, keep the newest N")
    cp.add_argument("--keep", type=int, default=10, metavar="N",
                    help="how many newest sessions to keep (default: 10)")

    args = ap.parse_args()
    {"init": cmd_init, "run": cmd_run, "review": cmd_review,
     "approve": cmd_approve, "sessions": cmd_sessions, "gate": cmd_gate,
     "clean": cmd_clean, "export": cmd_export, "license": cmd_license}[args.command](args)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
