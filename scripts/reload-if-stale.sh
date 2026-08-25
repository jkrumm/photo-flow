#!/usr/bin/env bash
#
# Redeploy the local Mac app, but only when something actually changed.
#
# Wired to the Claude Code `Stop` hook (.claude/settings.json), so every agent turn that
# touches the panel or the Python core ends with the running LaunchAgent serving that change.
# The premise is that "green tests" is not a delivered change here — the daemon on
# 127.0.0.1:7717 and the Dock PWA in front of it are the artifact.
#
# Two tiers, because they cost very different amounts:
#   web changed  -> `make reload`          (npm install + vite build + restart, ~15-25 s)
#   only Python  -> `make service-restart` (~1 s; the SPA bundle is untouched)
#   neither      -> exit immediately       (the common case — a few find(1) calls)
#
# Idempotence keys off a stamp file rather than dist/ mtimes: a Python-only change leaves
# dist/ alone, so comparing against it would re-restart the daemon on every single turn.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STAMP="${HOME}/.photoflow/.last-reload"
WEB="control_panel/web"

# Nothing to serve yet — a fresh clone before `make setup`. Not this script's job.
[[ -d "$WEB/node_modules" ]] || exit 0

# Newest mtime in a tree, honouring the caller's prunes. Prints nothing when unchanged.
changed_since() {
  local stamp="$1"
  shift
  [[ -f "$stamp" ]] || {
    echo "bootstrap"
    return
  }
  find "$@" -newer "$stamp" -print -quit 2>/dev/null
}

WEB_SOURCES=(
  "$WEB/src"
  "$WEB/index.html"
  "$WEB/vite.config.ts"
  "$WEB/package.json"
)
# `dist/` and `node_modules/` are outputs, not inputs — including them is a rebuild loop.
web_dirty="$(changed_since "$STAMP" "${WEB_SOURCES[@]}" -not -path "*/node_modules/*" -not -path "*/dist/*")"
# The parens are load-bearing: find's implicit AND binds tighter than -o, so an unbracketed
# `-name '*.py' -o -name '*.plist' -newer X -print -quit` attaches the test AND the actions to
# the *second* branch only — .py edits then match nothing and print nothing, and the daemon
# silently never restarts. Which is precisely the failure this whole script exists to prevent.
py_dirty="$(changed_since "$STAMP" photo_flow control_panel/launchd \
  \( -name '*.py' -o -name '*.plist' \))"

if [[ -z "$web_dirty" && -z "$py_dirty" ]]; then
  exit 0
fi

# A redeploy RESTARTS the daemon, and a restart kills whatever it was doing. The jobs the
# panel runs are minutes long and touch irreplaceable files — a `backup:final` rclone
# transfer, an `import` that moves originals off the card — so ending an agent turn on top
# of one turns a routine deploy into an interrupted transfer. (v0.4.10 makes that visible
# rather than silent: the job comes back `interrupted` and `last_run.json` is corrected.
# Visible is not the same as harmless.) Observed for real on 2026-08-25, where a restart
# issued to pick up a config change killed a `backup:staging` 106 s in.
#
# So: if the daemon reports a job queued or running, skip this turn entirely and leave the
# stamp alone. The next turn redeploys — the change is a few minutes late, which is the
# cheaper of the two failures by a wide margin.
#
# A daemon that does not answer cannot be running a job, so a failed probe proceeds. The
# probe is deliberately generous on connect (2 s) and quiet on error: an unavailable
# health endpoint must not be able to block a deploy forever.
job_in_flight() {
  local payload
  payload="$(curl -sf -m 2 http://127.0.0.1:7717/jobs 2>/dev/null)" || return 1
  printf '%s' "$payload" | python3 -c '
import json, sys

try:
    jobs = json.load(sys.stdin).get("jobs", [])
except Exception:          # a malformed body is not evidence of a running job
    sys.exit(1)
busy = [j for j in jobs if j.get("status") in ("queued", "running")]
if not busy:
    sys.exit(1)
print(", ".join("%s (%s)" % (j.get("op", "?"), j.get("status")) for j in busy))
' 2>/dev/null
}

if busy="$(job_in_flight)"; then
  echo "photoflow: skipping redeploy — a job is in flight: ${busy}"
  echo "photoflow: the stamp is untouched, so the next turn will deploy this change"
  exit 0
fi

mkdir -p "$(dirname "$STAMP")"

if [[ -n "$web_dirty" ]]; then
  echo "photoflow: panel sources changed — rebuilding and restarting the local Mac app"
  make reload
else
  echo "photoflow: Python changed — restarting the daemon (SPA bundle unchanged)"
  make service-restart
  # Poll rather than sleep: launchd takes a variable moment to bring uvicorn back, and a
  # fixed sleep races it into a false failure. Same contract as the `reload` target.
  for _ in $(seq 1 40); do
    curl -sf -o /dev/null http://127.0.0.1:7717/health && break
    sleep 0.5
  done
fi

# Stamped only on success: `set -e` above means a failed build leaves the stamp alone, so the
# next turn retries instead of quietly declaring the stale build deployed.
touch "$STAMP"
