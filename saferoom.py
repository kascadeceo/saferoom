#!/usr/bin/env python3
"""
SafeRoom — a controlled playground for AI coding agents.

Clones your repo into an isolated sandbox, swaps live credentials for dummies,
runs the agent inside a Docker container, and produces a full audit trail:
submitted entrypoint or interactive history, captured output, files changed,
and a reviewable git diff. You approve what ships.

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

MVP scope: Docker orchestration, recursive .env* credential swapping, run
transcripts or interactive history, and git-based diff auditing. Linux-first
(Debian/Ubuntu), works anywhere Docker runs.
"""

import argparse
import hashlib
import json
import os
import re
import selectors
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
        try:
            config = json.loads(cfg_path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            die(f"could not read {CONFIG_FILE}: {error}",
                f"fix or remove {CONFIG_FILE}, then retry")
        if not isinstance(config, dict):
            die(f"could not read {CONFIG_FILE}: top-level value must be an object",
                f"fix or remove {CONFIG_FILE}, then retry")
        if "image" in config and (not isinstance(config["image"], str)
                                  or not config["image"].strip()):
            die(f"could not read {CONFIG_FILE}: image must be a non-empty string",
                f"fix or remove {CONFIG_FILE}, then retry")
        if "offline" in config and not isinstance(config["offline"], bool):
            die(f"could not read {CONFIG_FILE}: offline must be true or false",
                f"fix or remove {CONFIG_FILE}, then retry")
        return config
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


def is_protected_path(path):
    """True for any .env* file or anything inside a .saferoom directory."""
    parts = Path(path).parts
    return SR_DIR in parts or bool(parts and parts[-1].startswith(".env"))


def find_env_files(root):
    """All .env* files under root, recursively — excluding the SafeRoom
    template, session state (.saferoom/), and git internals (.git/)."""
    found = []
    for p in sorted(root.rglob(".env*")):
        relative = p.relative_to(root)
        if (p.is_file() and p.name != ENV_TEMPLATE
                and is_protected_path(relative)
                and SR_DIR not in relative.parts and ".git" not in relative.parts):
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


class RunFailure(Exception):
    def __init__(self, message, hint):
        super().__init__(message)
        self.hint = hint


def docker_error_detail(error):
    output = error.stderr or error.stdout or "unknown Docker error"
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return (lines[-1] if lines else "unknown Docker error")[:500]


def remove_container(container_id):
    subprocess.run(
        ["docker", "rm", "-f", container_id], capture_output=True, text=True)


def require_docker():
    if not docker_available():
        raise RunFailure(
            "docker not found.",
            "Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y docker.io; "
            "macOS/Windows: install and start Docker Desktop.")
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, text=True)
    except OSError as error:
        raise RunFailure(
            f"could not run Docker: {error}",
            "Debian/Ubuntu: reinstall docker.io and check executable permissions; "
            "macOS/Windows: reinstall Docker Desktop.")
    if result.returncode:
        raise RunFailure(
            "cannot connect to the Docker daemon.",
            "Debian/Ubuntu: sudo systemctl start docker; if permission is denied, "
            "run sudo usermod -aG docker $USER and sign in again; "
            "macOS/Windows: start Docker Desktop.")


def allocate_session(root):
    parent = root / SR_DIR / "sessions"
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        die(f"could not create session directory: {error}",
            f"check write permissions for {root / SR_DIR}")
    stem = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for sequence in range(1000):
        session = stem if sequence == 0 else f"{stem}-{sequence:03d}"
        session_dir = parent / session
        try:
            session_dir.mkdir()
            return session, session_dir
        except FileExistsError:
            continue
        except OSError as error:
            die(f"could not create session directory: {error}",
                f"check write permissions for {parent}")
    die("could not allocate a unique session directory",
        "wait one second and retry; if this persists, run: saferoom clean")


def tee_process(cmd, transcript, cwd=None, env=None):
    """Stream both child output channels while preserving a merged byte transcript."""
    process = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    streams = selectors.DefaultSelector()
    streams.register(process.stdout, selectors.EVENT_READ, sys.stdout.buffer)
    streams.register(process.stderr, selectors.EVENT_READ, sys.stderr.buffer)
    try:
        with transcript.open("wb") as log:
            while streams.get_map():
                for key, _ in streams.select():
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        streams.unregister(key.fileobj)
                        continue
                    key.data.write(chunk)
                    key.data.flush()
                    log.write(chunk)
                    log.flush()
    except KeyboardInterrupt:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        streams.close()
    return process.wait()


IN_CONTAINER_SHELL = (
    'export HISTFILE=/workspace/{sr}/history; export HISTTIMEFORMAT="%F %T  "; '
    'export PROMPT_COMMAND="history -a"; '
    'if command -v script >/dev/null 2>&1; then '
    'exec script -q -a /workspace/{sr}/tty.log -c "bash -i"; '
    'else exec bash -i; fi'
)


def render_audit_report(meta, names, stat, commands, diff):
    lines = [
        f"# SafeRoom audit — session {meta['session']}", "",
        f"- Started:  {meta['started_utc']}",
        f"- Finished: {meta['finished_utc']}",
        f"- Isolation: {meta['isolation']}",
        f"- Image: {meta.get('image', '—')}",
        f"- Exit code: {meta.get('exit_code', '—')}",
        f"- Credentials swapped: {', '.join(meta['credentials_swapped']) or 'none found'}",
        "", "## Files changed", "```", names.strip() or "(no changes)", "",
        stat.strip(), "```", "",
    ]
    if meta.get("runtime_error"):
        lines += ["## Runtime error", meta["runtime_error"], ""]
    warnings = meta.get("review_warnings", [])
    if warnings:
        lines += ["## Review warnings", *(f"- **{warning}**" for warning in warnings), ""]
    entrypoint = meta.get("submitted_entrypoint")
    lines += [
        "## Submitted one-shot entrypoint", "```",
        entrypoint or "(interactive session)", "```", "",
        "## Interactive shell history", "```",
        commands.strip() or "(not captured for a one-shot run)", "```", "",
    ]
    if meta.get("transcript"):
        lines += ["## Captured transcript", "Full stdout/stderr: `transcript.log`", ""]
    lines += [
        "## Full diff", "```diff", diff.strip() or "(empty)", "```", "",
        f"Approve with:  saferoom approve {meta['session']}",
    ]
    return "\n".join(lines)


def collect_audit(sandbox_repo, base, session_dir, meta):
    sh(["git", "add", "-A"], cwd=sandbox_repo)
    changed = sh(["git", "diff", "--cached", "--name-only", "-z", "--no-renames", base],
                 cwd=sandbox_repo).stdout.split("\0")
    safe_paths = [path for path in changed if path and not is_protected_path(path)]
    spec = ["--", *(f":(literal){path}" for path in safe_paths)]
    common = ["git", "diff", "--cached", "--no-ext-diff", "--no-textconv",
              "--no-renames"]
    diff = sh([*common, "--binary", "--full-index", base, *spec],
              cwd=sandbox_repo).stdout if safe_paths else ""
    stat = sh([*common, "--stat", base, *spec],
              cwd=sandbox_repo).stdout if safe_paths else ""
    names = sh([*common, "--name-status", base, *spec],
               cwd=sandbox_repo).stdout if safe_paths else ""

    (session_dir / "changes.patch").write_text(diff)
    hist = sandbox_repo / SR_DIR / "history"
    commands = hist.read_text() if hist.exists() else ""
    cmd_log = session_dir / "commands.log"
    if commands:
        cmd_log.write_text(commands)

    warnings = []
    if "GIT binary patch" in diff or re.search(r"^Binary files ", diff, re.M):
        warnings.append("Binary content is encoded in changes.patch; inspect it before approval.")
    meta.update({
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files_changed": [l for l in names.splitlines() if l.strip()],
        "review_warnings": warnings,
    })
    (session_dir / "report.json").write_text(json.dumps(meta, indent=2) + "\n")
    report = session_dir / "report.md"
    report.write_text(render_audit_report(meta, names, stat, commands, diff))
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

def run_local(args, sandbox_repo, transcript):
    say("LOCAL MODE — no container isolation. Testing only.", "y")
    env = os.environ.copy()
    env["HISTFILE"] = str(sandbox_repo / SR_DIR / "history")
    if args.cmd:
        return tee_process(["bash", "-lc", " ".join(args.cmd)], transcript,
                           cwd=sandbox_repo, env=env)
    say("interactive sandbox shell — exit to finish and audit")
    result = subprocess.run(
        ["bash", "-c", f'export HISTFILE="{env["HISTFILE"]}"; '
         'export PROMPT_COMMAND="history -a"; exec bash -i'],
        cwd=sandbox_repo, env=env)
    return result.returncode


def create_container(sandbox_repo, session, image, offline):
    name = f"saferoom-{session}"
    create_cmd = ["docker", "create", "--name", name,
                  "-v", f"{sandbox_repo}:/workspace", "-w", "/workspace"]
    if offline:
        create_cmd += ["--network", "none"]
    create_cmd += [image, "sleep", "infinity"]
    say(f"starting container {name} ({image}{', offline' if offline else ''})")
    try:
        container_id = sh(create_cmd).stdout.strip()
    except subprocess.CalledProcessError as error:
        raise RunFailure(
            f"could not create Docker container from image {image}: "
            f"{docker_error_detail(error)}",
            f"Debian/Ubuntu: start Docker and run docker pull {image}; "
            "macOS/Windows: start Docker Desktop and verify the image name.")
    if not container_id:
        raise RunFailure(
            "Docker did not return a container ID.",
            "Debian/Ubuntu: check docker info; macOS/Windows: restart Docker Desktop.")
    try:
        sh(["docker", "start", container_id])
    except subprocess.CalledProcessError as error:
        remove_container(container_id)
        raise RunFailure(
            f"could not start Docker container: {docker_error_detail(error)}",
            "Debian/Ubuntu: check docker info and the image entrypoint; "
            "macOS/Windows: restart Docker Desktop and verify the image.")
    except BaseException:
        remove_container(container_id)
        raise
    return container_id


def run_docker(args, sandbox_repo, transcript, session, image, offline):
    container_id = create_container(sandbox_repo, session, image, offline)
    try:
        if args.cmd:
            joined = " ".join(args.cmd)
            say(f"running: {joined}")
            return tee_process(
                ["docker", "exec", container_id, "bash", "-lc", joined], transcript)
        say("interactive sandbox shell — exit to finish and audit")
        shell = IN_CONTAINER_SHELL.format(sr=SR_DIR)
        return subprocess.run(
            ["docker", "exec", "-it", container_id, "bash", "-c", shell]).returncode
    finally:
        if container_id:
            remove_container(container_id)


def cmd_run(args):
    root = repo_root()
    cfg = load_config(root)
    image = args.image or cfg.get("image", "python:3.12-slim")
    offline = args.offline or cfg.get("offline", False)

    session, session_dir = allocate_session(root)
    sandbox_repo = session_dir / "repo"
    transcript = session_dir / "transcript.log"

    say(f"session {session} — cloning repo into sandbox")
    clone_repo(root, sandbox_repo)
    swapped = swap_credentials(root, sandbox_repo)
    (sandbox_repo / SR_DIR).mkdir(exist_ok=True)
    base = baseline_commit(sandbox_repo)
    say(f"credentials swapped: {', '.join(swapped) or 'none found'} "
        f"({'dummy .env mounted' if (sandbox_repo / '.env').exists() else 'no .env in sandbox'})")

    entrypoint = " ".join(args.cmd) if args.cmd else None
    if entrypoint:
        (session_dir / "entrypoint.log").write_text(entrypoint + "\n")
    meta = {
        "session": session,
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "baseline": base,
        "credentials_swapped": swapped,
        "isolation": "none (--local, testing only)" if args.local
                     else f"docker ({'network: none' if offline else 'network: bridge'})",
        "image": None if args.local else image,
        "submitted_entrypoint": entrypoint,
        "submitted_argv": args.cmd or None,
        "transcript": "transcript.log" if args.cmd else None,
    }

    failure = None
    interrupted = False
    try:
        if args.local:
            exit_code = run_local(args, sandbox_repo, transcript)
        else:
            require_docker()
            exit_code = run_docker(args, sandbox_repo, transcript, session, image, offline)
    except RunFailure as error:
        failure = error
        exit_code = 1
    except KeyboardInterrupt:
        interrupted = True
        exit_code = 130

    meta["exit_code"] = exit_code
    if failure:
        meta["runtime_error"] = str(failure)
    elif interrupted:
        meta["runtime_error"] = "sandbox command interrupted"

    if args.screenshot:
        take_screenshot(args.screenshot, session_dir)

    report, names = collect_audit(sandbox_repo, base, session_dir, meta)
    say(f"audit ready: {report}", "g")
    print()
    print(names.strip() or "(no file changes)")
    print()
    say(f"review:  saferoom review {session}")
    say(f"approve: saferoom approve {session}")
    if failure:
        die(str(failure), hint=failure.hint)
    if exit_code:
        say(f"sandbox command exited with status {exit_code}; audit preserved", "r")
        raise SystemExit(exit_code)


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


def host_git_config(root, key, fallback):
    """Read the host user's git config value (fallback if unset)."""
    out = sh(["git", "config", "--get", key], cwd=root, check=False).stdout.strip()
    return out or fallback


def record_approval(root, session, patch):
    """Append the approval evidence: who approved, when, exactly what (patch hash)."""
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    name = host_git_config(root, "user.name", os.environ.get("USER", "unknown"))
    email = host_git_config(root, "user.email", "unknown")
    approved_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    files_changed = sum(1 for line in patch.read_text().splitlines()
                        if line.startswith("diff --git "))
    record = {
        "session": session,
        "approved_utc": approved_utc,
        "approver_name": name,
        "approver_email": email,
        "patch_sha256": digest,
        "files_changed_count": files_changed,
    }
    session_dir = patch.parent
    with (session_dir / "approvals.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")
    report = session_dir / "report.md"
    if report.exists():
        lead = "" if report.read_text().endswith("\n") else "\n"
        with report.open("a") as f:
            f.write(f"{lead}**Approved** by {name} at {approved_utc} — "
                    f"patch sha256 {digest}\n")
    return digest


def patch_paths(root, patch):
    """Apply to a disposable index and return every old/new path touched."""
    index = patch.parent / ".approval.index"
    env = {"GIT_INDEX_FILE": str(index)}
    source = Path(sh(["git", "rev-parse", "--git-path", "index"],
                     cwd=root).stdout.strip())
    if not source.is_absolute():
        source = root / source
    if source.exists():
        shutil.copyfile(source, index)
    try:
        sh(["git", "add", "-A"], cwd=root, env=env)
        before = sh(["git", "write-tree"], cwd=root, env=env).stdout.strip()
        sh(["git", "apply", "--cached", "--check", str(patch)], cwd=root, env=env)
        sh(["git", "apply", "--cached", str(patch)], cwd=root, env=env)
        after = sh(["git", "write-tree"], cwd=root, env=env).stdout.strip()
        raw = sh(["git", "diff-tree", "--no-commit-id", "--name-status", "-z",
                  "-r", "-M", before, after], cwd=root).stdout
    finally:
        index.unlink(missing_ok=True)
        index.with_name(index.name + ".lock").unlink(missing_ok=True)
    fields = [field for field in raw.split("\0") if field]
    paths = []
    i = 0
    while i < len(fields):
        status = fields[i]
        count = 2 if status[:1] in ("R", "C") else 1
        paths.extend(fields[i + 1:i + 1 + count])
        i += count + 1
    return paths


def cmd_approve(args):
    root = repo_root()
    session = args.session or latest_session(root)
    patch = root / SR_DIR / "sessions" / session / "changes.patch"
    if not patch.exists() or not patch.read_text().strip():
        die(f"session {session} has no changes to approve")
    try:
        protected = [path for path in patch_paths(root, patch) if is_protected_path(path)]
    except subprocess.CalledProcessError as e:
        die(f"patch does not apply cleanly:\n{e.stderr.strip()}",
            "review the diff and apply hunks manually, or re-run against a clean tree")
    if protected:
        die(f"patch touches protected path: {protected[0]}",
            "protected .env* and .saferoom paths cannot be approved; re-run the session")
    excludes = ["--exclude=.env*", "--exclude=*/.env*",
                f"--exclude={SR_DIR}/**", f"--exclude=*/{SR_DIR}/**"]
    try:
        sh(["git", "apply", "--check", *excludes, str(patch)], cwd=root)
    except subprocess.CalledProcessError as e:
        die(f"patch does not apply cleanly:\n{e.stderr.strip()}",
            "review the diff and apply hunks manually, or re-run against a clean tree")
    sh(["git", "apply", *excludes, str(patch)], cwd=root)
    digest = record_approval(root, session, patch)
    say(f"session {session} approved — changes applied to your working tree "
        "(env files excluded)", "g")
    say(f"evidence recorded: patch sha256 {digest} "
        f"→ .saferoom/sessions/{session}/approvals.jsonl", "g")
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
