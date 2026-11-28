# Self-hosting Flagsmith on Render (free tier)

This is the setup Slowroll was built and tested against: self-hosted open-source Flagsmith 2.292.0 on Render's free plan with a free Postgres database.

1. **Database.** Render, New, Postgres. Name `flagsmith-db`, a region near you, instance type Free. Wait until it shows Available and copy the Internal Database URL. Render's free Postgres expires after 30 days, so note the date.
2. **Web service.** New, Web Service, Existing Image: `docker.io/flagsmith/flagsmith:2.292.0`. Same region as the database, instance type Free. Pin a tag and never use `latest`, because the endpoints Slowroll uses are experimental.
3. **Environment variables.**

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | the Internal Database URL |
   | `PORT` | `8000` |
   | `DJANGO_ALLOWED_HOSTS` | `*` |
   | `GUNICORN_TIMEOUT` | `300` |
   | `GUNICORN_WORKERS` | `1` |
   | `DJANGO_SECRET_KEY` | the output of `python3 -c 'import secrets; print(secrets.token_urlsafe(50))'` |
   | `PROMETHEUS_MULTIPROC_DIR` | `/tmp` |

4. Set the **Health Check Path** to `/health/liveness/`. If the deploy fails its health check, try `/health`.
5. Create the service and watch the logs. The first boot runs migrations and is slow on the free CPU.
6. Check it:

   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' $FLAGSMITH_API_URL/health/liveness/
   curl -s $FLAGSMITH_API_URL/version/
   ```

   The first should print 200 and the second returns JSON that includes `image_tag`. Then open the URL and create the admin account and an organisation. The open-source edition allows one project.

## Things that went wrong

- **The first boot on 2.295.0 crashed** with `FileNotFoundError: /tmp/prometheus/counter_1.db` while importing Flagsmith's metrics module. We added `PROMETHEUS_MULTIPROC_DIR=/tmp` and pinned 2.292.0 at the same time, so we do not know which of the two fixed it. Both are in the table above.
- **Restarts under load.** While the demo's load generator ran at 12 requests per second over 200 users, Render restarted the instance every couple of minutes, and clients saw 502 errors for about a minute each time. The service's Events tab said `HTTP health check failed (timed out after 5 seconds)`. With one gunicorn worker on the free plan, a busy instance cannot answer `/health/liveness/` in time, and Render treats it as dead. When idle, the same endpoint answered in about 0.5 to 0.7 seconds from our network. Lowering the demo load to 4 requests per second over 40 users fixed it, and nothing else changed. Fewer distinct users also means fewer flag lookups, because the demo app caches each user's flags for 10 seconds. We did not test the dashboard's effect, but keeping its tab closed during a demo avoids extra requests.
- **Slowroll rides out restarts.** Its Flagsmith client retries connection errors and 502, 503 and 504 responses with waits of 2, 6, 15, 30 and 30 seconds, about 83 seconds in total. That covers a free-tier restart, which matters most for `halt`.
- **A free instance sleeps** after about 15 idle minutes, and the first request afterwards takes roughly a minute. A cheap fix is an external job that requests `/health/liveness/` every ten minutes, for example on cron-job.org. It carries no secrets.
- **Do not host a long-running demo on Codespaces.** Codespaces stop when idle and have a monthly free quota.

## Before a demo

Close the Flagsmith dashboard tab, wait five minutes, then check that liveness stays fast:

```bash
for i in 1 2 3 4 5; do curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' $FLAGSMITH_API_URL/health/liveness/; sleep 2; done
```

Anything near 5 seconds means the instance is overloaded, and you should stop any load generator first (`pkill -f demo/loadgen.py`).
