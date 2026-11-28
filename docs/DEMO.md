# Demo script (2 minutes)

Everything in the demo is real except the traffic. A load generator simulates it, and the demo page says so.

## Before you record

1. Stop GitHub's own cron from ticking while you work: `gh workflow disable slowroll.yml`.
2. Keep the Flagsmith keep-warm job enabled and open the Flagsmith URL once, because a free instance sleeps.
3. In the terminal:

   ```bash
   cd ~/projects/slowRoll && source .venv/bin/activate
   set -a; . ./.env.local; set +a
   unset GH_TOKEN
   export GITHUB_TOKEN=$(grep -m1 oauth_token ~/.config/gh/hosts.yml | awk '{print $2}')
   export GITHUB_REPOSITORY=chilli-garlic-momo/slowRoll
   ```

4. Browser tabs: the demo page, the repo's issues, and the Flagsmith features page (Development).
5. `./scripts/demo.sh` runs the whole sequence and pauses where you act. The timers can be changed with `RAMP_SECONDS`, `HALT_SECONDS`, `GATE_SECONDS` and `FINISH_SECONDS`.

The real ramp takes about a minute, so record in short takes and cut them together.

## Script and timing

| Time | On screen | Say |
|---|---|---|
| 0:00 | The README title | Releasing to everyone at once is how outages happen. Open-source Flagsmith has no built-in progressive rollout, audit log or approvals. Its own Release Pipelines is a closed beta. Slowroll adds that for open source. |
| 0:15 | `rollout.yml`, then `slowroll apply` | One file describes the ramp. Apply creates the segment, a disabled override and one GitHub issue that holds all status. |
| 0:30 | The ramp ticks, the demo page, the issue | Slowroll raises the percentage one step at a time and checks health between steps. The traffic here is simulated, and the page says so. |
| 1:00 | The `inject_errors` toggle, health turning red, the issue | I switch on a bug that only treatment users hit. Two unhealthy checks in a row and Slowroll turns the flag off by itself, and the issue says why. |
| 1:20 | `WAIT_APPROVAL`, then the label | Before 50 percent the rollout waits for a human. Adding the label `slowroll/approve` counts only if you have write access. |
| 1:40 | `python scripts/mcp_demo.py ~/slowroll-mcp.sh` | An MCP client can plan a rollout as a pull request, check status and halt, through three tools. There is deliberately no approve tool. |
| 1:55 | The repo and the README quickstart | One config file, no server to run, MIT licensed. |

## Honesty notes

- Say that the traffic is simulated.
- Do not say Release Pipelines does not exist.
- The MCP step uses a short script as the client. Call it an MCP client unless an AI agent really does the calls.

## MCP launcher (one time)

```bash
cat > ~/slowroll-mcp.sh <<'SCRIPT'
#!/usr/bin/env bash
cd ~/projects/slowRoll
set -a; . ./.env.local; set +a
unset GH_TOKEN
export GITHUB_TOKEN=$(grep -m1 oauth_token ~/.config/gh/hosts.yml | awk '{print $2}')
export GITHUB_REPOSITORY=chilli-garlic-momo/slowRoll
exec .venv/bin/slowroll-mcp
SCRIPT
chmod +x ~/slowroll-mcp.sh
```
