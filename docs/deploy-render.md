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
- **A free instance sleeps** after about 15 idle minutes, and the first request afterwards takes roughly a minute. Slowroll waits up to 90 seconds per request and retries on connection errors and 502, 503 and 504.
- **Do not host a long-running demo on Codespaces.** Codespaces stop when idle and have a monthly free quota.
