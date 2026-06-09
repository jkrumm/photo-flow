#!/usr/bin/env bash
# Photo-Flow Control Panel — RALPH Loop Runner
#
# Usage:
#   ./scripts/ralph.sh              # Run all pending groups
#   ./scripts/ralph.sh 3            # Run only group 3
#   ./scripts/ralph.sh --reset 3    # Reset group 3 to pending, then run
#   ./scripts/ralph.sh --status     # Print status and exit
#
# Logs: .ralph-logs/group-N.log   (watch: tail -f .ralph-logs/group-N.log)
# Prereqs: brew install coreutils (gtimeout); claude CLI in PATH; repo venv/ present.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCS_DIR="$REPO_ROOT/docs/ralph"
PROMPTS_DIR="$DOCS_DIR/prompts"
STATE_FILE="$REPO_ROOT/.ralph-tasks.json"
LOGS_DIR="$REPO_ROOT/.ralph-logs"
REPORT_FILE="$DOCS_DIR/RALPH_REPORT.md"

MAX_RETRIES=3
CLAUDE_TIMEOUT=2700  # 45 minutes per group

RALPH_MODEL="${RALPH_MODEL:-sonnet}"
RALPH_EFFORT="${RALPH_EFFORT:-high}"
RALPH_TRANSPORT="${RALPH_TRANSPORT:-max}"            # max | bridge
LITELLM_BRIDGE_URL="${LITELLM_BRIDGE_URL:-http://127.0.0.1:4000}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

TOTAL_GROUPS=13

GROUP_TITLES=(
  ""  # 1-indexed
  "Foundation — packaging, deps, FastAPI skeleton, test harness"
  "Event seam part 1 — ProgressReporter + RichReporter (import + finalize)"
  "Event seam part 2 — sync/cleanup/rclone backup + console routing"
  "Job manager, QueueReporter, SSE, status endpoints"
  "Operation endpoints — dry-run preview + job-backed runs"
  "SQLite metadata index — schema + incremental indexer"
  "Analytics API — aggregation endpoints + index refresh"
  "SPA scaffold — Vite/React/Mantine, strict tooling, typed client"
  "Vendor argo visx charts + Blueprint token system"
  "Pipeline hero — animated Camera→Staging→Final→Publish"
  "Operations UI — dry-run confirm modals + live SSE progress"
  "Analytics + Library health UI"
  "photoflow serve, static serving, LaunchAgent, docs"
)

log_info()    { echo -e "${BLUE}[ralph]${NC} $*"; }
log_success() { echo -e "${GREEN}[ralph]${NC} $*"; }
log_warn()    { echo -e "${YELLOW}[ralph]${NC} $*"; }
log_error()   { echo -e "${RED}[ralph]${NC} $*"; }

require_commands() {
  local missing=0
  for cmd in claude gtimeout python3; do
    if ! command -v "$cmd" &>/dev/null; then
      log_error "$cmd not found."
      missing=1
    fi
  done
  [[ $missing -eq 0 ]] || { echo "Install: brew install coreutils"; exit 1; }
}

# ── Pre-flight guards ─────────────────────────────────────────────────────────

ORIG_GPGSIGN=""
GPGSIGN_TOUCHED=false

disable_commit_signing() {
  cd "$REPO_ROOT"
  ORIG_GPGSIGN="$(git config --local --get commit.gpgsign || echo '__unset__')"
  local effective
  effective="$(git config --get commit.gpgsign || echo 'false')"
  if [[ "$effective" == "true" ]]; then
    log_warn "commit.gpgsign=true — disabling for the loop (would block on Touch ID)."
    git config --local commit.gpgsign false
    GPGSIGN_TOUCHED=true
  fi
}

restore_commit_signing() {
  $GPGSIGN_TOUCHED || return 0
  cd "$REPO_ROOT" 2>/dev/null || return 0
  if [[ "$ORIG_GPGSIGN" == "__unset__" ]]; then
    git config --local --unset commit.gpgsign 2>/dev/null || true
  else
    git config --local commit.gpgsign "$ORIG_GPGSIGN"
  fi
  log_info "Restored commit.gpgsign (was: $ORIG_GPGSIGN)."
}

refuse_default_branch() {
  cd "$REPO_ROOT"
  local current
  current="$(git rev-parse --abbrev-ref HEAD)"
  case "$current" in
    master|main)
      log_error "Refusing to run on '$current' — autonomous commits to the default branch are unsafe."
      log_error "Switch to a feature branch first: git checkout -b feat/control-panel"
      exit 1
      ;;
  esac
  log_info "Running on branch: $current"
}

PUSH_GUARD_INSTALLED=false

install_push_guard() {
  cd "$REPO_ROOT"
  PRE_PUSH_HOOK="$(git rev-parse --git-path hooks)/pre-push"
  if [[ -f "$PRE_PUSH_HOOK" ]]; then
    mv "$PRE_PUSH_HOOK" "${PRE_PUSH_HOOK}.ralph-backup"
  fi
  cat > "$PRE_PUSH_HOOK" <<'HOOK'
#!/usr/bin/env bash
echo "[ralph] pre-push hook: autonomous push blocked." >&2
exit 1
HOOK
  chmod +x "$PRE_PUSH_HOOK"
  PUSH_GUARD_INSTALLED=true
}

remove_push_guard() {
  $PUSH_GUARD_INSTALLED || return 0
  cd "$REPO_ROOT" 2>/dev/null || return 0
  PRE_PUSH_HOOK="$(git rev-parse --git-path hooks)/pre-push"
  rm -f "$PRE_PUSH_HOOK"
  if [[ -f "${PRE_PUSH_HOOK}.ralph-backup" ]]; then
    mv "${PRE_PUSH_HOOK}.ralph-backup" "$PRE_PUSH_HOOK"
  fi
}

LOCK_FILE="$REPO_ROOT/.ralph-lock"
LOCK_ACQUIRED=false

acquire_lock() {
  cd "$REPO_ROOT"
  if [[ -f "$LOCK_FILE" ]]; then
    local other_pid
    other_pid="$(cat "$LOCK_FILE" 2>/dev/null || echo '')"
    if [[ -n "$other_pid" ]] && kill -0 "$other_pid" 2>/dev/null; then
      log_error "Another ralph.sh is already running (PID $other_pid)."
      log_error "Refusing a second runner — concurrent loops corrupt state and can fork-bomb."
      log_error "If you are certain that PID is dead: rm $LOCK_FILE"
      exit 1
    fi
    log_warn "Stale lock from dead PID '$other_pid' — reclaiming."
  fi
  echo "$$" > "$LOCK_FILE"
  LOCK_ACQUIRED=true
  log_info "Acquired runner lock (PID $$)."
}

release_lock() {
  $LOCK_ACQUIRED || return 0
  [[ -f "$LOCK_FILE" && "$(cat "$LOCK_FILE" 2>/dev/null)" == "$$" ]] || return 0
  rm -f "$LOCK_FILE"
}

cleanup_on_exit() {
  restore_commit_signing
  remove_push_guard
  release_lock
}
trap cleanup_on_exit EXIT

# ── State management ──────────────────────────────────────────────────────────

init_state() {
  [[ -f "$STATE_FILE" ]] && { log_info "Resuming from existing state."; return; }
  log_info "Initializing task state..."
  python3 - "$STATE_FILE" "${GROUP_TITLES[@]:1}" <<'PYEOF'
import json, sys, datetime
state_file = sys.argv[1]
titles = sys.argv[2:]
groups = [{"id": i+1, "title": t, "status": "pending", "attempts": 0,
           "started_at": None, "completed_at": None}
          for i, t in enumerate(titles)]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
state = {"groups": groups, "created_at": now}
with open(state_file, "w") as f:
    json.dump(state, f, indent=2)
print("State initialized.")
PYEOF
}

get_field() {
  python3 - "$STATE_FILE" "$1" "$2" <<'PYEOF'
import json, sys
state_file, gid, field = sys.argv[1], int(sys.argv[2]), sys.argv[3]
with open(state_file) as f:
    state = json.load(f)
for g in state['groups']:
    if g['id'] == gid:
        print(g.get(field, ''))
        break
PYEOF
}

set_field() {
  python3 - "$STATE_FILE" "$1" "$2" "$3" <<'PYEOF'
import json, sys
state_file, gid, field, raw = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
val = {'True': True, 'False': False, 'None': None}.get(raw, raw)
with open(state_file) as f:
    state = json.load(f)
for g in state['groups']:
    if g['id'] == gid:
        g[field] = val
        break
with open(state_file, 'w') as f:
    json.dump(state, f, indent=2)
PYEOF
}

inc_attempts() {
  python3 - "$STATE_FILE" "$1" <<'PYEOF'
import json, sys
state_file, gid = sys.argv[1], int(sys.argv[2])
with open(state_file) as f:
    state = json.load(f)
for g in state['groups']:
    if g['id'] == gid:
        g['attempts'] = g.get('attempts', 0) + 1
        break
with open(state_file, 'w') as f:
    json.dump(state, f, indent=2)
PYEOF
}

dec_attempts() {
  python3 - "$STATE_FILE" "$1" <<'PYEOF'
import json, sys
state_file, gid = sys.argv[1], int(sys.argv[2])
with open(state_file) as f:
    state = json.load(f)
for g in state['groups']:
    if g['id'] == gid:
        g['attempts'] = max(0, g.get('attempts', 0) - 1)
        break
with open(state_file, 'w') as f:
    json.dump(state, f, indent=2)
PYEOF
}

print_status() {
  python3 - "$STATE_FILE" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    state = json.load(f)
icons = {'complete': '✅', 'blocked': '🚫', 'pending': '⬜', 'in_progress': '🔄'}
total = len(state['groups'])
done = sum(1 for g in state['groups'] if g['status'] == 'complete')
blocked = sum(1 for g in state['groups'] if g['status'] == 'blocked')
pending = total - done - blocked
print(f"  {total} groups | {done} complete | {pending} pending | {blocked} blocked")
print()
for g in state['groups']:
    icon = icons.get(g['status'], '⬜')
    attempts = f"  (attempt {g['attempts']})" if g['attempts'] > 0 else ""
    print(f"  {icon}  Group {g['id']}: {g['title']}{attempts}")
PYEOF
}

# ── Validation (polyglot: Python always; Node when web/ exists) ────────────────

validate() {
  local label=${1:-""}
  log_info "Validation${label:+ ($label)}..."
  cd "$REPO_ROOT"
  mkdir -p "$LOGS_DIR"
  local vlog="$LOGS_DIR/validate${label:+-${label// /-}}.log"

  local PY="$REPO_ROOT/venv/bin/python"
  [[ -x "$PY" ]] || PY="python3"

  {
    echo "### python import smoke"
    "$PY" -c "import photo_flow.cli, photo_flow.workflow, photo_flow.metadata_extractor" || exit 1
    if [[ -d "$REPO_ROOT/tests" ]]; then
      echo "### pytest"
      "$PY" -m pytest -q || exit 1
    fi
    if [[ -f "$REPO_ROOT/control_panel/web/package.json" ]]; then
      cd "$REPO_ROOT/control_panel/web"
      if [[ ! -d node_modules ]]; then
        echo "### npm install (node_modules missing)"
        npm install || exit 1
      fi
      echo "### npm run build"
      npm run build || exit 1
      echo "### npm run lint"
      npm run lint || exit 1
    fi
  } > "$vlog" 2>&1 || {
    log_error "Validation failed — last 40 lines of $vlog:"
    tail -n 40 "$vlog" >&2
    return 1
  }
  log_success "Validation passed (full output: $vlog)"
  return 0
}

# ── Claude invocation ─────────────────────────────────────────────────────────

run_group() {
  local group_id=$1
  local prompt_file="$PROMPTS_DIR/group-$group_id.md"
  local context_file="$DOCS_DIR/shared-context.md"
  local log_file="$LOGS_DIR/group-$group_id.log"

  mkdir -p "$LOGS_DIR"

  if [[ ! -f "$prompt_file" ]]; then
    log_error "Prompt not found: $prompt_file"
    return 1
  fi

  local full_prompt
  full_prompt="$(cat "$context_file")"$'\n\n---\n\n'"$(cat "$prompt_file")"

  log_info "Claude running (model: $RALPH_MODEL, transport: $RALPH_TRANSPORT, timeout: ${CLAUDE_TIMEOUT}s) → log: .ralph-logs/group-$group_id.log"
  log_info "Watch live: tail -f .ralph-logs/group-$group_id.log"
  echo ""

  local -a group_env=(CLAUDE_CODE_ENABLE_TASKS=true CLAUDECODE=)
  if [[ "$RALPH_TRANSPORT" == "bridge" ]]; then
    if ! curl -fsS -m 3 "${LITELLM_BRIDGE_URL}/health/liveliness" >/dev/null 2>&1; then
      log_error "RALPH_TRANSPORT=bridge but LiteLLM bridge unreachable at $LITELLM_BRIDGE_URL — run 'make litellm-restart' in dotfiles."
      return 1
    fi
    group_env+=(ANTHROPIC_BASE_URL="$LITELLM_BRIDGE_URL" ANTHROPIC_AUTH_TOKEN=sk-litellm-master-key CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1)
  fi

  local exit_code=0
  if env -u ANTHROPIC_API_KEY "${group_env[@]}" gtimeout "$CLAUDE_TIMEOUT" claude \
    -p "$full_prompt" \
    --model "$RALPH_MODEL" \
    --effort "$RALPH_EFFORT" \
    --dangerously-skip-permissions \
    --output-format stream-json \
    --verbose \
    --no-session-persistence \
    < /dev/null > "$log_file" 2>&1; then
    exit_code=0
  else
    exit_code=$?
  fi

  grep -q "RALPH_TASK_COMPLETE: Group $group_id" "$log_file" && return 0
  grep -q "RALPH_TASK_BLOCKED: Group $group_id" "$log_file" && return 2

  if grep -qiE "Claude AI usage limit reached|usage limit reached|5-hour limit|limit will reset" "$log_file"; then
    log_error "Claude usage/session limit reached — pausing loop (this attempt does not count)."
    grep -iE "usage limit|limit will reset|reset at" "$log_file" | tail -n 2 >&2 || true
    return 3
  fi

  [[ $exit_code -eq 124 ]] && { log_error "Timed out after ${CLAUDE_TIMEOUT}s"; return 1; }

  log_warn "Claude finished but no completion signal in log."
  return 1
}

# ── Report ────────────────────────────────────────────────────────────────────

generate_report() {
  python3 - "$STATE_FILE" "$REPORT_FILE" <<'PYEOF'
import json, sys, datetime
state_file, report_file = sys.argv[1], sys.argv[2]
with open(state_file) as f:
    state = json.load(f)
icons = {'complete': '✅', 'blocked': '🚫', 'pending': '⬜', 'in_progress': '🔄'}
total = len(state['groups'])
done = sum(1 for g in state['groups'] if g['status'] == 'complete')
blocked = sum(1 for g in state['groups'] if g['status'] == 'blocked')
pending = total - done - blocked
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
lines = [
    "# RALPH Report", "",
    f"Generated: {now}",
    f"Groups: {total} total | {done} complete | {pending} pending | {blocked} blocked",
    "", "## Status", "",
]
for g in state['groups']:
    icon = icons.get(g['status'], '⬜')
    attempts = f" (attempts: {g['attempts']})" if g['attempts'] > 0 else ""
    lines.append(f"- {icon} **Group {g['id']}**: {g['title']}{attempts}")
lines += ["", "## Next Steps", ""]
if done == total:
    lines += ["All groups complete.", "", "1. Review: `git log --oneline -20`", "2. Run full E2E", "3. Create PR: `/pr`"]
elif pending > 0:
    lines.append("Run `./scripts/ralph.sh` to continue.")
with open(report_file, 'w') as f:
    f.write('\n'.join(lines) + '\n')
print(f"Report: {report_file}")
PYEOF
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
  local target_group=""
  local do_reset=false
  local status_only=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --status) status_only=true; shift ;;
      --reset) do_reset=true; target_group="${2:?'--reset requires a group number'}"; shift 2 ;;
      [0-9]*) target_group="$1"; shift ;;
      *) echo "Unknown: $1"; echo "Usage: ralph.sh [group] [--reset group] [--status]"; exit 1 ;;
    esac
  done

  echo ""
  echo -e "${BOLD}  RALPH Loop — Photo-Flow Control Panel${NC}"
  echo ""

  require_commands
  cd "$REPO_ROOT"
  init_state

  if $status_only; then print_status; exit 0; fi

  refuse_default_branch

  if $do_reset; then
    log_info "Resetting Group $target_group to pending..."
    set_field "$target_group" "status" "pending"
    set_field "$target_group" "attempts" "0"
  fi

  print_status; echo ""

  disable_commit_signing
  install_push_guard
  acquire_lock

  local groups_to_run=()
  if [[ -n "$target_group" ]]; then
    groups_to_run=("$target_group")
  else
    for i in $(seq 1 $TOTAL_GROUPS); do groups_to_run+=("$i"); done
  fi

  for group_id in "${groups_to_run[@]}"; do
    local status
    status=$(get_field "$group_id" "status")

    if [[ "$status" == "complete" ]]; then
      echo -e "  ✅  Group $group_id: ${GROUP_TITLES[$group_id]} — skipped (complete)"
      continue
    fi
    if [[ "$status" == "blocked" ]]; then
      echo -e "  🚫  Group $group_id: ${GROUP_TITLES[$group_id]} — skipped (blocked)"
      continue
    fi

    local attempts
    attempts=$(get_field "$group_id" "attempts")

    if [[ "$attempts" -ge "$MAX_RETRIES" ]]; then
      log_warn "Group $group_id reached max retries. Marking blocked."
      set_field "$group_id" "status" "blocked"
      continue
    fi

    echo ""
    echo "  ────────────────────────────────────────────"
    echo -e "  ${BOLD}Group $group_id: ${GROUP_TITLES[$group_id]}${NC}"
    echo "  Attempt: $((attempts + 1)) / $MAX_RETRIES"
    echo "  ────────────────────────────────────────────"
    echo ""

    if [[ "$group_id" -gt 1 ]]; then
      if ! validate "pre-group $group_id"; then
        log_error "Pre-group validation failed. Fix before continuing."
        exit 1
      fi
      echo ""
    fi

    set_field "$group_id" "status" "in_progress"
    set_field "$group_id" "started_at" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    inc_attempts "$group_id"

    run_result=0
    run_group "$group_id" || run_result=$?
    echo ""

    if [[ $run_result -eq 0 ]]; then
      log_success "Group $group_id complete."
      set_field "$group_id" "status" "complete"
      set_field "$group_id" "completed_at" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo ""
      if validate "post-group $group_id"; then
        log_success "Post-group validation passed ✓"
      else
        log_warn "Post-group validation FAILED. Review log and fix."
        log_warn "Retry: ./scripts/ralph.sh --reset $group_id"
      fi
    elif [[ $run_result -eq 2 ]]; then
      log_warn "Group $group_id blocked. See: .ralph-logs/group-$group_id.log"
      set_field "$group_id" "status" "blocked"
    elif [[ $run_result -eq 3 ]]; then
      set_field "$group_id" "status" "pending"
      dec_attempts "$group_id"
      log_error "Group $group_id paused: Claude usage/session limit reached."
      log_error "Re-run ./scripts/ralph.sh once the limit resets — it resumes here."
      break
    else
      log_error "Group $group_id failed (attempt $((attempts + 1)) / $MAX_RETRIES)"
      set_field "$group_id" "status" "pending"
      log_info "Log: .ralph-logs/group-$group_id.log"
      new_attempts=$(get_field "$group_id" "attempts")
      if [[ "$new_attempts" -ge "$MAX_RETRIES" ]]; then
        set_field "$group_id" "status" "blocked"
      elif [[ -z "$target_group" ]]; then
        log_warn "Stopping. Fix Group $group_id before proceeding."
        break
      fi
    fi

    echo ""
  done

  echo ""
  generate_report
  echo ""
  echo -e "${BOLD}  RALPH loop done.${NC}"
  echo ""
  print_status
  echo ""
}

main "$@"
