#!/usr/bin/env python3
"""
SafeRoom — a controlled playground for AI coding agents.

Clones your repo into an isolated sandbox, swaps live credentials for dummies,
runs the agent inside a Docker container, and produces a full audit trail:
files changed, commands run, clean git diffs. You approve what ships.

Usage:
  saferoom init                      Prepare config + dummy-credential template
  saferoom run [opts] [-- CMD...]    Start a sandboxed session (interactive or one-shot)
  saferoom review [SESSION]          Show the audit report for a session
  saferoom approve [SESSION]         Apply the approved diff to your real repo
  saferoom sessions                  List past sessions
  saferoom clean [--keep N]          Remove old sessions, keep the newest N (default 10)

Run options:
  --image IMAGE      Docker image for the sandbox (default: from config, else python:3.12-slim)
  --offline          No network inside the container (--network none)
  --local            DEV/TEST ONLY: run on host with no container isolation
  --screenshot URL   Capture a UI screenshot after the run (needs playwright installed)

MVP scope: Docker orchestration, .env credential swapping, command logging,
git-based diff auditing. Linux-first (Debian/Ubuntu), works anywhere Docker runs.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = "0.1.0"

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


def cmd_init(args):
    root = repo_root()
    env_files = sorted(p for p in root.glob(".env*") if p.is_file() and p.name != ENV_TEMPLATE)
    lines = ["# SafeRoom dummy credentials — mounted into every sandbox as .env",
             "# Real values never enter the container. Edit stand-ins as needed.", ""]
    keys = 0
    for ef in env_files:
        lines.append(f"# from {ef.name}")
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
    swapped = []
    for ef in list(sandbox_repo.glob(".env*")):
        if ef.is_file() and ef.name != ENV_TEMPLATE:
            ef.unlink()
            swapped.append(ef.name)
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
            subprocess.run(joined, shell=True, cwd=sandbox_repo, env=env)
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
                subprocess.run(["docker", "exec", name, "bash", "-lc", joined])
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
    report = root / SR_DIR / "sessions" / session / "report.md"
    if not report.exists():
        die(f"no report for session {session}")
    print(report.read_text())


def cmd_approve(args):
    root = repo_root()
    session = args.session or latest_session(root)
    patch = root / SR_DIR / "sessions" / session / "changes.patch"
    if not patch.exists() or not patch.read_text().strip():
        die(f"session {session} has no changes to approve")
    excludes = ["--exclude=.env", "--exclude=.env.*", f"--exclude={SR_DIR}/*"]
    try:
        sh(["git", "apply", "--check", *excludes, str(patch)], cwd=root)
    except subprocess.CalledProcessError as e:
        die(f"patch does not apply cleanly:\n{e.stderr.strip()}",
            "review the diff and apply hunks manually, or re-run against a clean tree")
    sh(["git", "apply", *excludes, str(patch)], cwd=root)
    say(f"session {session} approved — changes applied to your working tree "
        "(env files excluded)", "g")
    say("review with git diff, then commit when satisfied.")


def cmd_sessions(args):
    root = repo_root()
    d = root / SR_DIR / "sessions"
    if not d.exists():
        say("no sessions yet")
        return
    for p in sorted(p for p in d.iterdir() if p.is_dir()):
        rj = p / "report.json"
        n = "?"
        if rj.exists():
            n = len(json.loads(rj.read_text()).get("files_changed", []))
        print(f"{p.name}   files changed: {n}")


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

    apv = sub.add_parser("approve", help="apply approved diff to real repo")
    apv.add_argument("session", nargs="?")

    sub.add_parser("sessions", help="list sessions")

    cp = sub.add_parser("clean", help="remove old sessions, keep the newest N")
    cp.add_argument("--keep", type=int, default=10, metavar="N",
                    help="how many newest sessions to keep (default: 10)")

    args = ap.parse_args()
    {"init": cmd_init, "run": cmd_run, "review": cmd_review,
     "approve": cmd_approve, "sessions": cmd_sessions,
     "clean": cmd_clean}[args.command](args)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
