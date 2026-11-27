# Slowroll

Health-gated progressive rollout for self-hosted, open-source [Flagsmith](https://flagsmith.com).

Slowroll ramps a feature flag through a percentage segment step by step, checks a health URL between steps, reverts automatically on bad health, pauses for a human approval before a chosen step, and keeps its status and audit trail in one GitHub issue. There is one config file and no server to run.

## Why this exists

Release Pipelines for open-source Flagsmith. At the time of writing, Flagsmith's own Release Pipelines is a closed beta whose code lives in a private enterprise package, and the open-source edition has no audit log and no change requests. Slowroll is a small independent controller that covers the progressive-rollout part using only Flagsmith's admin API and GitHub. It is not affiliated with Flagsmith.

What you get on top of plain open-source Flagsmith:

| Need | How Slowroll does it |
|---|---|
| Gradual ramp | A `% Split` segment whose percentage is raised step by step |
| Automatic revert | Turns the segment override off after consecutive unhealthy checks |
| Human approval | A GitHub label, checked against the labeler's repo permission |
| Audit trail | One GitHub issue: a live status comment plus a timestamped comment per action |

What you give up: there is no UI, decisions happen once per tick (every few minutes), and Slowroll uses Flagsmith endpoints that are marked experimental (see [Limitations](#limitations)).

## How it works

```
rollout.yml (git)
      |
      v
slowroll tick  <-- GitHub Actions (schedule, workflow_dispatch, repository_dispatch) or a local loop
      | \
      |  +--> GitHub issue: status comment (state), approval label, audit trail
      v
Flagsmith: segment "slowroll:<flag>" with one % Split rule, plus a segment override on the flag
      | health check (HTTP GET)
      v
your health URL
```

The flag stays off in the environment default. A segment named `slowroll:<flag>` holds one `% Split` rule, and a segment override turns the flag on for that segment. Ramping raises the percentage. Halting turns the override off.

## Quickstart

You need a running Flagsmith (tested on self-hosted 2.292.0, see [deploy-render.md](docs/deploy-render.md)), Python 3.11+, and a GitHub repo you can write issues to. It should take about five minutes.

**1. In Flagsmith**

- Create a boolean flag, for example `quickstart_demo`, and leave it **off** in your environment.
- Note the **project id** (the number after `/project/` in the dashboard URL) and the environment's **client-side environment key** (Environment settings, Keys).
- Create an organisation **Admin API key** (Organisation Settings, API Keys).

**2. Set your credentials**

```bash
export FLAGSMITH_API_URL=https://your-flagsmith.example.com
export FLAGSMITH_API_KEY=your-admin-api-key
export GITHUB_TOKEN=a-token-with-issues-write
export GITHUB_REPOSITORY=your-name/your-repo
```

For `GITHUB_TOKEN`, use `gh auth token` on a recent GitHub CLI, or a fine-grained token with Issues read and write on that repo.

**3. Write `rollout.yml`** and a stand-in health endpoint

```bash
cat > rollout.yml <<'YML'
flag: quickstart_demo
project: 1
environment: YOUR_ENVIRONMENT_CLIENT_KEY
steps: [10, 50, 100]
soak: 10s
health:
  url: http://127.0.0.1:8000/health.json
YML

echo '{"healthy": true}' > health.json
python3 -m http.server 8000 &
```

**4. Check, plan, apply**

```bash
uvx --from git+https://github.com/chilli-garlic-momo/slowRoll slowroll doctor rollout.yml
uvx --from git+https://github.com/chilli-garlic-momo/slowRoll slowroll plan rollout.yml
uvx --from git+https://github.com/chilli-garlic-momo/slowRoll slowroll apply rollout.yml
```

`doctor` prints one line per check. `plan` shows what `apply` would do and writes nothing. `apply` creates the segment, a disabled override, two labels and the tracking issue. It is safe to run again.

**5. Ramp**

```bash
uvx --from git+https://github.com/chilli-garlic-momo/slowRoll slowroll loop rollout.yml --every 10s
```

You should see `ADVANCE` to 10%, 50% and 100%, then `FINISH`. Open the issue `Slowroll: quickstart_demo` to read the audit trail. To see a revert, change `health.json` to `{"healthy": false}` during a run: two unhealthy ticks in a row halt the rollout.

Prefer `pip`? `pip install git+https://github.com/chilli-garlic-momo/slowRoll` gives you the `slowroll` command.

## Commands

| Command | What it does |
|---|---|
| `slowroll doctor rollout.yml` | Checks reachability, auth, project, feature id, that the environment default is off, and whether the segment exists |
| `slowroll plan rollout.yml` | Same as `apply --dry-run` |
| `slowroll apply rollout.yml [--dry-run]` | Creates the segment, a disabled override and the issue if missing. Refuses if the flag is on in the environment default |
| `slowroll tick rollout.yml [--dry-run]` | One decision step |
| `slowroll loop rollout.yml --every 10s` | Runs `tick` forever. For demos |
| `slowroll status rollout.yml` | Prints state and current percent |
| `slowroll halt rollout.yml [--reason text]` | Override off, status halted |
| `slowroll reset rollout.yml` | Override off, segment back to the first step, state idle |

Exit codes: `0` success (a tick that halted is still a success), `2` invalid config, `1` anything else. Errors are one line on stderr, and a halt also prints a GitHub Actions `::warning::` line.

After a rollout finishes, its issue is closed and later ticks say so and exit 0. To run the same flag again, use `reset` and then `apply`.

## Configuration

```yaml
flag: new_checkout
project: 12
environment: <environment-client-key>
steps: [5, 25, 50, 100]
soak: 10m
approval:
  before_step: 50
health:
  url: https://demo.example.app/api/health
  fail_after: 2
  unknown_comment_every: 6
github:
  repo: owner/name
```

| Key | Meaning |
|---|---|
| `flag` | Name of a boolean feature |
| `project` | Flagsmith project id |
| `environment` | Environment client key |
| `steps` | Strictly increasing percentages from 1 to 100 |
| `soak` | Minimum time at a step before the next one, like `30s`, `10m` or `2h` |
| `approval.before_step` | Optional. One of `steps`. Steps at or above it need an approval |
| `health.url` | Must start with `http://` or `https://` |
| `health.fail_after` | Consecutive unhealthy ticks that halt the rollout. Default 2 |
| `health.unknown_comment_every` | Post a comment on every Nth consecutive unknown tick. Default 6 |
| `github.repo` | `owner/name`. Overrides `GITHUB_REPOSITORY` |

Every validation error is reported at once, and unknown keys are an error. Environment variables: `FLAGSMITH_API_URL` (no trailing slash), `FLAGSMITH_API_KEY` (admin key, sent as `Authorization: Api-Key`), `GITHUB_TOKEN` (issues read and write) and `GITHUB_REPOSITORY`.

## Health contract

Slowroll sends `GET` to `health.url` with a 10 second timeout. The response must be JSON:

```json
{"healthy": true, "detail": "optional text"}
```

`healthy` is `true`, `false` or `null`, where `null` means unknown. Anything else (timeout, non-200, bad JSON, wrong type) is treated as unknown, with the reason as the detail. **Slowroll never reverts on unknown.** It holds the current step and reports every Nth unknown tick in the issue.

## Approval gate and trust model

Set `approval.before_step` and Slowroll stops before that step with status `waiting`. A human continues the rollout by adding the label `slowroll/approve` to the issue.

- The label counts only if the **latest** labeled event has no later unlabeled event, the person who added it is a real user (not a bot), and their repository permission is `admin`, `maintain` or `write`.
- Removing the label withdraws the approval.
- Slowroll itself never adds the label.
- **Anyone with write access to the repo can approve.** There is no separate approver list.
- There is one gate. An approval covers every step at or above `before_step`.

## GitHub Actions

Copy `examples/slowroll-workflow.yml` to `.github/workflows/slowroll.yml`, add the repository secrets `FLAGSMITH_API_URL` and `FLAGSMITH_API_KEY`, and commit a `rollout.yml`. The workflow runs on a schedule, on `workflow_dispatch`, and on `repository_dispatch` (`slowroll-tick` and `slowroll-halt`). Runs never overlap.

GitHub's cron is best effort and can run late, so use an external pinger such as cron-job.org that calls `workflow_dispatch` every five minutes. Give it a fine-grained token limited to your repo with only **Actions: Read and write**:

```bash
curl -X POST \
  -H "Authorization: Bearer $PINGER_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/OWNER/REPO/actions/workflows/slowroll.yml/dispatches \
  -d '{"ref":"main"}'
```

An emergency halt from anywhere, with a token that can send `repository_dispatch` (it needs Contents write, so keep it away from the pinger):

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/OWNER/REPO/dispatches \
  -d '{"event_type":"slowroll-halt"}'
```

## Demo

`demo/` is a small Vercel app with simulated traffic. `/api/checkout` returns a control or treatment variant from Flagsmith, a chaos flag (`inject_errors`) makes treatment requests fail, and `/api/health` compares the two error rates using counters in Upstash Redis. `demo/loadgen.py` generates the traffic. **All demo traffic is simulated.**

The demo's health endpoint says `healthy: null` when treatment has between 1 and 9 requests in the window, and `true` when treatment has none yet but control has baseline traffic. Without that second rule a rollout could never leave its idle state, because treatment only gets traffic after the first step. The side effect is that a very short `soak` can advance a step before treatment traffic exists. Use a `soak` of at least a minute or two with real traffic.

## Limitations

- **Experimental Flagsmith endpoints.** Slowroll uses `/api/__future__/environments/...`, which Flagsmith documents as experimental. They do not work when change requests are enabled, and they do not support identity overrides. Pin your Flagsmith image tag. Tested only against self-hosted 2.292.0. Flagsmith Cloud is untested.
- **Boolean flags only.**
- **One approval gate.**
- **1 percent granularity.** Percentages are 1 to 100, even though one instance we tested also accepted 0.
- **GitHub cron is best effort.** Use an external pinger.
- **Tick resolution.** Decisions happen once per tick, so a `soak` shorter than your tick interval is effectively one tick.
- **GitHub API version header.** Slowroll sends `X-GitHub-Api-Version: 2022-11-28`, which GitHub currently marks deprecated with a sunset date in 2028.

## Troubleshooting

Start with `slowroll doctor rollout.yml`. It prints `ok` or `FAIL` per check.

| Symptom | Likely cause and fix |
|---|---|
| `FAIL reachable` | Wrong `FLAGSMITH_API_URL`, or a free-tier instance that is asleep. Retry for a minute |
| `FAIL auth` with 401 or 403 | Wrong or revoked admin API key |
| `FAIL project` | Wrong `project` id |
| `FAIL feature id` | The flag name is wrong or it is in a different project |
| `FAIL environment default` | The flag is on in the environment default. Turn it off, then `apply` |
| `segment: missing` | Not an error. `apply` creates it |
| `no open issue ...; run slowroll apply` | Nothing was applied yet |
| `status comment has no state marker` | The first issue comment was edited by hand. Run `slowroll apply` |
| GitHub error ending in `(needs: ...)` | Give the token or workflow exactly that permission |

## Development

```bash
pip install -e '.[dev]'
python -m pytest
```

MIT licensed.
