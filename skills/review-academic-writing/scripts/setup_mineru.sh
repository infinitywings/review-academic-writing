#!/usr/bin/env bash
# Install and start the local MinerU parse server that scripts/extract_pdf.py uses.
#
# Idempotent: every step first checks the current state and only acts when needed.
#   1. uv present (never installed here; exit 2 with the hint when missing)
#   2. mineru installed with `uv tool install "mineru==<version>"` when the command is missing
#   3. models verified with `mineru-kit models verify --tier <tier>`; downloaded when they fail
#   4. server started when it is not running (`telemetry` and `config` need a running server)
#   5. telemetry disabled
#   6. parse_server.local.mode set to managed, then the server restarted so it takes effect
#   7. polled until healthy
#
# Never uses MinerU's remote service. Exit 0 when the local parse server is ready,
# 1 when it is not after the wait, 2 when a prerequisite is missing.
#
# Usage: setup_mineru.sh [--check] [--version 4.0.12] [--tier standard] [--python 3.12] [--wait 180]
#   --check   report each step and the readiness verdict without changing anything

set -u

VERSION="4.0.12"
TIER="standard"
PYTHON="3.12"
WAIT=180
CHECK=0

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --version) VERSION="$2"; shift ;;
    --tier) TIER="$2"; shift ;;
    --python) PYTHON="$2"; shift ;;
    --wait) WAIT="$2"; shift ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "setup_mineru.sh: unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
step() { printf -- '- %s\n' "$*"; }
changed=0

# uv's tool binaries live in ~/.local/bin; make them visible for this run.
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac
export PATH

# 1. uv
if ! command -v uv >/dev/null 2>&1; then
  step "uv: missing. Install it first (https://docs.astral.sh/uv/ ; macOS: brew install uv), then rerun."
  exit 2
fi
step "uv: $(command -v uv)"

# 2. mineru
if command -v mineru >/dev/null 2>&1; then
  installed="$(mineru --version 2>/dev/null | sed -n 's/^MinerU version: *//p' | head -1)"
  step "mineru: ${installed:-unknown version} at $(command -v mineru)"
  if [ -n "$installed" ] && [ "$installed" != "$VERSION" ]; then
    step "mineru: installed version differs from the tested $VERSION; leaving it as is (uv tool install \"mineru==$VERSION\" --python $PYTHON to pin)"
  fi
else
  if [ "$CHECK" = 1 ]; then
    step "mineru: missing (would run: uv tool install \"mineru==$VERSION\" --python $PYTHON)"
    say "not ready: mineru is not installed"
    exit 1
  fi
  step "mineru: missing; installing with uv tool install \"mineru==$VERSION\" --python $PYTHON"
  if ! uv tool install "mineru==$VERSION" --python "$PYTHON"; then
    say "not ready: uv tool install failed"
    exit 1
  fi
  hash -r 2>/dev/null || true
  if ! command -v mineru >/dev/null 2>&1; then
    step "mineru: still not on PATH after install; add $HOME/.local/bin to PATH and rerun"
    exit 1
  fi
  changed=1
fi

# 3. models
if mineru-kit models verify --tier "$TIER" >/dev/null 2>&1; then
  step "models ($TIER): verified"
else
  if [ "$CHECK" = 1 ]; then
    step "models ($TIER): missing or incomplete (would run: mineru-kit models download --tier $TIER)"
  else
    step "models ($TIER): missing or incomplete; downloading (about 850 MB for standard)"
    if ! mineru-kit models download --tier "$TIER"; then
      say "not ready: model download failed"
      exit 1
    fi
    changed=1
  fi
fi

# 4. server: `mineru telemetry ...` and `mineru config ...` answer "Local mineru server is not
# running" until it is up, so start it before touching either setting.
status_json() { mineru server status --json 2>/dev/null; }
# Prints "ready", "starting", "stopped" or "unhealthy" from the status JSON.
status_word() {
  status_json | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("stopped"); sys.exit()
local = (d.get("parse_server") or {}).get("local") or {}
if not d.get("running"):
    print("stopped")
elif local.get("starting"):
    print("starting")
elif local.get("healthy") and local.get("mode") not in (None, "off"):
    print("ready")
else:
    print("unhealthy")
' 2>/dev/null || echo "stopped"
}

state="$(status_word)"
if [ "$state" = "stopped" ] && [ "$CHECK" != 1 ]; then
  step "server: stopped; starting"
  mineru server start >/dev/null 2>&1 || { say "not ready: mineru server start failed"; exit 1; }
  changed=1
  state="$(status_word)"
else
  step "server: $state"
fi

# 5. telemetry
if mineru telemetry status 2>/dev/null | grep -q '^state: *disabled'; then
  step "telemetry: disabled"
else
  if [ "$CHECK" = 1 ]; then
    step "telemetry: enabled or unset (would run: mineru telemetry disable)"
  else
    step "telemetry: disabling"
    mineru telemetry disable >/dev/null 2>&1 || step "telemetry: disable failed (continuing)"
  fi
fi

# 6. managed mode
need_restart=0
if mineru config get parse_server.local.mode 2>/dev/null | grep -q '= *managed'; then
  step "parse_server.local.mode: managed"
else
  if [ "$CHECK" = 1 ]; then
    step "parse_server.local.mode: not managed (would run: mineru config set parse_server.local.mode managed)"
  else
    step "parse_server.local.mode: setting managed"
    if ! mineru config set parse_server.local.mode managed >/dev/null 2>&1; then
      say "not ready: could not set parse_server.local.mode"
      exit 1
    fi
    need_restart=1
    changed=1
  fi
fi

if [ "$CHECK" = 1 ]; then
  if [ "$state" = "ready" ]; then say "ready: local MinerU parse server is healthy"; exit 0; fi
  say "not ready: server is $state"
  exit 1
fi

if [ "$need_restart" = 1 ]; then
  step "server: restarting so the managed mode takes effect"
  mineru server restart >/dev/null 2>&1 || { say "not ready: mineru server restart failed"; exit 1; }
fi

# 7. wait until healthy
elapsed=0
state="$(status_word)"
while [ "$state" != "ready" ] && [ "$elapsed" -lt "$WAIT" ]; do
  sleep 5
  elapsed=$((elapsed + 5))
  state="$(status_word)"
done
if [ "$state" = "ready" ]; then
  if [ "$changed" = 1 ]; then say "ready: local MinerU parse server is healthy (setup made changes)"; else say "ready: local MinerU parse server is healthy (nothing to do)"; fi
  exit 0
fi
say "not ready: server is $state after ${elapsed}s; check: mineru server status --json"
exit 1
