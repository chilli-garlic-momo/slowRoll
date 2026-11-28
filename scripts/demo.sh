#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.."

URL="${DEMO_URL:-https://slowroll-demo.vercel.app}"
CFG=/tmp/slowroll-demo.yml
RPS="${RPS:-4}"
USERS="${USERS:-40}"
RAMP_SECONDS="${RAMP_SECONDS:-90}"
HALT_SECONDS="${HALT_SECONDS:-40}"
GATE_SECONDS="${GATE_SECONDS:-90}"
FINISH_SECONDS="${FINISH_SECONDS:-60}"

if [ ! -s rollout.yml ]; then
  echo "rollout.yml is missing or empty in $(pwd). Restore it with: git checkout -- rollout.yml" >&2
  exit 2
fi

for v in FLAGSMITH_API_URL FLAGSMITH_API_KEY GITHUB_TOKEN GITHUB_REPOSITORY; do
  if [ -z "${!v:-}" ]; then
    echo "$v is not set. Load .env.local and export GITHUB_TOKEN and GITHUB_REPOSITORY first (see docs/DEMO.md)." >&2
    exit 2
  fi
done

if pgrep -f demo/loadgen.py > /dev/null; then
  echo "another load generator is still running. Stop it with: pkill -f demo/loadgen.py" >&2
  exit 2
fi

pause() { printf '\n>>> %s\n    press Enter to continue ' "$1"; read -r; }

wait_healthy() {
  echo "waiting for the demo to report healthy..."
  for _ in $(seq 1 40); do
    if curl -s "$URL/api/health" | grep -q '"healthy":true'; then echo "healthy"; return 0; fi
    sleep 3
  done
  echo "the demo never reported healthy; check $URL/api/health and /tmp/loadgen.log" >&2
  return 1
}

ticks() { timeout -s INT "$1" slowroll loop "$CFG" --every 5s; }

fresh() { slowroll apply "$CFG" && slowroll reset "$CFG"; }

cleanup() { [ -n "${LOADGEN_PID:-}" ] && kill "$LOADGEN_PID" 2>/dev/null; }
trap cleanup EXIT

echo "checking that Flagsmith answers quickly (anything near 5s means it is overloaded)..."
for _ in 1 2 3; do
  curl -s -o /dev/null -w '  liveness %{http_code} in %{time_total}s\n' "$FLAGSMITH_API_URL/health/liveness/"
  sleep 1
done

sed -e 's/^soak:.*/soak: 15s/' -e "s#^  url: .*#  url: $URL/api/health#" rollout.yml > "$CFG"
echo "demo config:"; cat "$CFG"

fresh || exit 1
pause "open the demo page ($URL), the GitHub issue and the Flagsmith dashboard in your browser"

python demo/loadgen.py --url "$URL" --rps "$RPS" --users "$USERS" --duration 1500 > /tmp/loadgen.log 2>&1 &
LOADGEN_PID=$!
echo "simulated traffic started: $RPS requests per second over $USERS users (log: /tmp/loadgen.log)"
wait_healthy || exit 1

echo; echo "=== PART 1: live ramp ==="
ticks "$RAMP_SECONDS"

pause "turn the inject_errors flag ON in the Flagsmith dashboard (Development)"
echo "=== PART 1: bad health, automatic revert ==="
ticks "$HALT_SECONDS"
slowroll status "$CFG"

pause "turn the inject_errors flag OFF again in the dashboard"
fresh || exit 1
wait_healthy || exit 1

echo; echo "=== PART 2: the approval gate ==="
ticks "$GATE_SECONDS"

pause "add the label slowroll/approve to the open issue on GitHub"
ticks "$FINISH_SECONDS"

echo; echo "resetting: flag back to idle with a fresh issue"
fresh || exit 1

pause "optional: show the MCP server (needs the launcher from docs/DEMO.md)"
if [ -x "$HOME/slowroll-mcp.sh" ]; then python scripts/mcp_demo.py "$HOME/slowroll-mcp.sh"; fi
