#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/.."

URL="${DEMO_URL:-https://slowroll-demo.vercel.app}"
CFG=/tmp/slowroll-demo.yml
RAMP_SECONDS="${RAMP_SECONDS:-75}"
HALT_SECONDS="${HALT_SECONDS:-40}"
GATE_SECONDS="${GATE_SECONDS:-75}"
FINISH_SECONDS="${FINISH_SECONDS:-60}"

for v in FLAGSMITH_API_URL FLAGSMITH_API_KEY GITHUB_TOKEN GITHUB_REPOSITORY; do
  if [ -z "${!v:-}" ]; then
    echo "$v is not set. Load .env.local and export GITHUB_TOKEN and GITHUB_REPOSITORY first (see docs/DEMO.md)." >&2
    exit 2
  fi
done

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

cleanup() { [ -n "${LOADGEN_PID:-}" ] && kill "$LOADGEN_PID" 2>/dev/null; }
trap cleanup EXIT

sed -e 's/^soak:.*/soak: 15s/' -e "s#^  url: .*#  url: $URL/api/health#" rollout.yml > "$CFG"
echo "demo config:"; cat "$CFG"

slowroll reset "$CFG"; slowroll apply "$CFG"
pause "open the demo page ($URL), the GitHub issue and the Flagsmith dashboard in your browser"

python demo/loadgen.py --url "$URL" --rps 12 --users 200 --duration 1500 > /tmp/loadgen.log 2>&1 &
LOADGEN_PID=$!
echo "simulated traffic started (log: /tmp/loadgen.log)"
wait_healthy || exit 1

echo; echo "=== PART 1: live ramp ==="
ticks "$RAMP_SECONDS"

pause "turn the inject_errors flag ON in the Flagsmith dashboard (Development)"
echo "=== PART 1: bad health, automatic revert ==="
ticks "$HALT_SECONDS"
slowroll status "$CFG"

pause "turn the inject_errors flag OFF again in the dashboard"
slowroll reset "$CFG"; slowroll apply "$CFG"
wait_healthy || exit 1

echo; echo "=== PART 2: the approval gate ==="
ticks "$GATE_SECONDS"

pause "add the label slowroll/approve to the open issue on GitHub"
ticks "$FINISH_SECONDS"

pause "optional: show the MCP server (needs the launcher from docs/DEMO.md)"
if [ -x "$HOME/slowroll-mcp.sh" ]; then python scripts/mcp_demo.py "$HOME/slowroll-mcp.sh"; fi

echo; echo "cleaning up: flag back to idle"
slowroll reset "$CFG"; slowroll apply "$CFG"
