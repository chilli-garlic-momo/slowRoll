# Run Slowroll locally

This runs the whole demo on your machine: self-hosted Flagsmith in Docker, a local stand-in for the demo app, and the real Slowroll controller. The only outside service is GitHub, because Slowroll keeps its status and audit trail in a GitHub issue.

You need Docker with Compose, Python 3.11 or newer, and a GitHub repository you can write to (a throwaway one is fine). Allow about 15 minutes.

`docker-compose.yml` pins Flagsmith 2.292.0, the version Slowroll is tested against. Slowroll uses endpoints Flagsmith marks experimental, so do not switch it to `latest`.

## 1. Start Flagsmith

```bash
docker compose up -d
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/health/liveness/
```

The first start runs migrations and can take a minute or two. Repeat the `curl` until it prints `200`. If port 8000 is taken, run `FLAGSMITH_PORT=8001 docker compose up -d` and use that port everywhere below.

## 2. Create the admin account, the project and an API key

1. Open http://localhost:8000. On a fresh instance you get a welcome screen. Choose **Get Started**, enter your name, email and a password, then name the organisation.
2. Create a project (open-source Flagsmith allows one). Any name works.
3. Open **Organisation Settings**, then **API Keys**, create an **Admin API key** and copy it.

## 3. Install Slowroll

```bash
uv venv && source .venv/bin/activate
uv pip install -e '.[mcp]'        # or: python -m venv .venv && . .venv/bin/activate && pip install -e '.[mcp]'
```

## 4. Set your credentials

Put these in `.env.local` (it is git-ignored) or export them in your shell:

```bash
export FLAGSMITH_API_URL=http://localhost:8000
export FLAGSMITH_API_KEY=<the admin API key from step 2>
export GITHUB_TOKEN=<fine-grained token: Issues read and write on your repo>
export GITHUB_REPOSITORY=your-name/your-repo
```

If you use `.env.local`, load it with `set -a; . ./.env.local; set +a`. The MCP and agent steps below also need the token to have Contents and Pull requests write.

## 5. Create the flags and the rollout config

```bash
python scripts/local_setup.py --github-repo "$GITHUB_REPOSITORY"
```

This finds your project, creates `new_checkout` and `inject_errors` if they are missing (both off, never switched on), reads the Development environment's client-side key and writes `rollout.local.yml`. It prints the next commands. Run the `export FLAGSMITH_ENVIRONMENT_KEY=...` line it shows.

## 6. Start the local demo app and check everything

```bash
python demo/local_demo.py &
curl -s http://127.0.0.1:8080/api/health        # {"healthy":null,...} until traffic arrives
slowroll doctor rollout.local.yml               # each check prints ok or FAIL; "segment: missing" is fine, apply creates it
```

The app mirrors the Vercel demo: `/api/checkout?uid=...` returns a control or treatment variant from Flagsmith, the `inject_errors` flag makes treatment requests fail, and `/api/health` compares the two error rates. Counters live in memory. Open http://127.0.0.1:8080 to see it. All traffic is simulated.

## 7. Run the full scripted demo

```bash
ROLLOUT_FILE=rollout.local.yml DEMO_URL=http://127.0.0.1:8080 ./scripts/demo.sh
```

It applies the config, starts simulated load, ramps the flag, pauses so you can switch `inject_errors` on in the Flagsmith dashboard (Slowroll then reverts on its own), and pauses again at the approval gate so you can add the `slowroll/approve` label to the GitHub issue. See [DEMO.md](DEMO.md) for what each pause is for.

To try it by hand instead: `slowroll apply rollout.local.yml`, then `slowroll loop rollout.local.yml --every 10s`, with `python demo/loadgen.py --url http://127.0.0.1:8080 --rps 4 --users 40` running in another terminal.

## 8. Let an AI model drive the MCP server (optional)

`scripts/agent_demo.py` is a small agent loop. It starts `slowroll-mcp`, offers its three tools to a model and runs the calls the model makes. It works with any provider that offers an OpenAI-compatible chat API with tool calling, including free tiers and local models. Check your provider's documentation for the exact base URL and for a model that supports tool calling.

```bash
export LLM_BASE_URL=<your provider's OpenAI-compatible base URL>   # for example http://localhost:11434/v1 for Ollama
export LLM_API_KEY=<your key>                                       # for Ollama any non-empty text
export LLM_MODEL=<a model that supports tool calling>
slowroll apply rollout.local.yml        # so the status tool finds an open issue
python scripts/agent_demo.py --config rollout.local.yml --log agent-run.md
```

The model can plan a rollout (this opens a real branch and pull request in `GITHUB_REPOSITORY`), read its status and halt it. It has no approve tool. A person still merges the pull request, runs `slowroll apply` and adds the approval label. Close the pull request afterwards if you were only trying it. The transcript goes to `agent-run.md`.

## Clean up

```bash
slowroll reset rollout.local.yml   # first, while Flagsmith is still up: override off, rollout back to idle
pkill -f demo/local_demo.py
docker compose down -v             # -v also deletes the Flagsmith database
```

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `curl` to `/health/liveness/` never returns 200 | Wait a little longer on first start, then read `docker compose logs flagsmith`. |
| `local_setup.py`: `this key sees no projects` | Create the project in the dashboard first (step 2). |
| `local_setup.py`: `environment 'Development' not found` | Pass the name of an environment you have: `--environment Production`. |
| `slowroll doctor` says `FAIL environment default` | The flag is on in the environment. Turn it off in the dashboard, then run `apply`. |
| The demo page shows `degraded` | The app cannot reach Flagsmith. Check `FLAGSMITH_API_URL` and `FLAGSMITH_ENVIRONMENT_KEY`, which must be the client-side key. |
| `agent_demo.py` says HTTP 400 or 404 | The base URL or model name is wrong for your provider, or the model does not support tool calling. |
| `agent_demo.py` says HTTP 429 | A free tier is rate limiting you. The script retries a few times; wait a minute and run it again. |
