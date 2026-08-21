# Demo recording script — "agent tries DROP TABLE, SafeRoom catches it"

Target: a ~45-second terminal recording that becomes `docs/demo.gif` and is
embedded in the README (uncomment the demo block). The scenario below was
dry-run against `saferoom.py` with Docker 29.x on Debian/Ubuntu; every line of
output shown is what actually appears.

## Prerequisites

```bash
sudo apt-get install -y docker.io            # daemon running, user in docker group
docker pull python:3.12-slim                  # pre-pull so the recording has no download pause
sudo ./install.sh                             # puts `saferoom` on PATH
# Recorder — pick one:
sudo apt-get install -y asciinema             # then: cargo install --git https://github.com/asciinema/agg   (asciicast -> gif)
#   or
go install github.com/charmbracelet/vhs@latest   # needs ttyd + ffmpeg; renders the .tape straight to gif
```

Terminal: 100x28, dark theme, 14-16pt mono font. Type at a human pace; pause
~1.5s after each command so viewers can read the output.

## Scene 0 — scratch repo with a real-looking .env (do this OFF camera)

```bash
rm -rf /tmp/billing && mkdir /tmp/billing && cd /tmp/billing && git init -q
cat > .env <<'ENV'
DATABASE_URL=postgres://admin:Pr0dPassw0rd@prod-db.internal:5432/billing
STRIPE_SECRET_KEY=sk_live_51HxREALKEY
ENV
printf 'import os\nprint("billing service", os.environ["DATABASE_URL"])\n' > app.py
git add -A && git commit -qm "billing service"
clear
```

## Scene 1 — start recording

```bash
asciinema rec -c bash --cols 100 --rows 28 -t "SafeRoom: agent tries DROP TABLE" demo.cast
```

(or `vhs demo.tape` using the tape at the bottom of this file)

## Scene 2 — keystrokes, in order

1. Show the stakes:
   ```bash
   cat .env
   ```
   → real-looking prod creds on screen.

2. Initialize SafeRoom:
   ```bash
   saferoom init
   ```
   → `wrote .env.saferoom (2 live credential(s) replaced with dummies)`

3. Show the stand-ins the agent will get:
   ```bash
   cat .env.saferoom
   ```
   → `DATABASE_URL=http://saferoom-stub.local`, `STRIPE_SECRET_KEY=sr-dummy-secret`

4. Run the "agent" in an offline sandbox. This one-shot command stands in for
   an agent that hallucinates a cleanup migration and tries to run it:
   ```bash
   saferoom run --offline -- 'cat .env; mkdir -p migrations; echo "DROP TABLE customers;" > migrations/001_cleanup.sql; psql "$DATABASE_URL" -c "DROP TABLE customers;"'
   ```
   → the container prints the DUMMY `.env` (no `Pr0dPassw0rd`, no `sk_live_`),
     `psql: command not found` / nothing reachable (network: none),
     then `A  migrations/001_cleanup.sql` and `audit ready: ...`.

5. Review what it did:
   ```bash
   saferoom review
   ```
   → report shows `Isolation: docker (network: none)`, `Credentials swapped: .env`,
     the full command line containing `DROP TABLE customers;` under
     **Commands run in sandbox**, and the `+DROP TABLE customers;` hunk under
     **Full diff**. Pause here ~3s — this is the money shot.

6. Prove nothing shipped:
   ```bash
   git status --short && cat .env
   ```
   → working tree clean (only the untracked `.env.saferoom`, `saferoom.json`,
     `.saferoom/`), real `.env` intact. Do NOT run `saferoom approve`.

7. Stop recording: `exit` (asciinema) — or let the tape end (vhs).

## Scene 3 — convert to gif and wire into README

```bash
# asciinema route
agg --cols 100 --rows 28 --font-size 16 demo.cast docs/demo.gif
# vhs route: the tape's `Output docs/demo.gif` line does this for you
```

Then in `README.md`, replace the two comment lines

```
<!-- DEMO: record with `vhs` or asciinema — agent tries DROP TABLE, SafeRoom catches it -->
<!-- ![demo](docs/demo.gif) -->
```

with

```
![demo — agent tries DROP TABLE, SafeRoom catches it in the audit](docs/demo.gif)
```

Keep the gif under ~2 MB (trim idle time with `agg --idle-time-limit 2`).

## vhs tape (optional)

Save as `demo.tape` next to the scratch repo and run `vhs demo.tape`:

```
Output docs/demo.gif
Set FontSize 16
Set Width 1200
Set Height 700
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms

Type "cd /tmp/billing && clear" Enter Sleep 500ms
Type "cat .env" Enter Sleep 2s
Type "saferoom init" Enter Sleep 2s
Type "cat .env.saferoom" Enter Sleep 2s
Type `saferoom run --offline -- 'cat .env; mkdir -p migrations; echo "DROP TABLE customers;" > migrations/001_cleanup.sql; psql "$DATABASE_URL" -c "DROP TABLE customers;"'` Enter Sleep 6s
Type "clear && saferoom review" Enter Sleep 5s
Type "git status --short && cat .env" Enter Sleep 3s
```
