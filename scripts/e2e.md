# Live end-to-end run

Reproduces the three tick scenarios against the real Flagsmith instance and GitHub repo, using a 5 second soak and a fake health server. Needs `FLAGSMITH_API_URL`, `FLAGSMITH_API_KEY` and `GITHUB_TOKEN` in the environment.

## Setup

    sed -e 's/^soak:.*/soak: 5s/' -e 's#^  url: .*#  url: http://127.0.0.1:8099/health#' -e '/^approval:/,+1d' examples/rollout.yml > /tmp/e2e-a.yml
    sed -e 's/^soak:.*/soak: 5s/' -e 's#^  url: .*#  url: http://127.0.0.1:8099/health#' examples/rollout.yml > /tmp/e2e-c.yml
    python scripts/fake_health.py --file /tmp/health --port 8099 &
    echo healthy > /tmp/health

## A: ramps to done

    slowroll reset /tmp/e2e-a.yml; slowroll apply /tmp/e2e-a.yml
    for i in 1 2 3 4 5 6; do slowroll tick /tmp/e2e-a.yml; sleep 6; done

Expect ADVANCE to 5, 25, 50 and 100, then FINISH (status done, issue closed), then NOOP.

## B: halts on bad health

    echo healthy > /tmp/health
    slowroll reset /tmp/e2e-a.yml; slowroll apply /tmp/e2e-a.yml
    slowroll tick /tmp/e2e-a.yml
    echo unhealthy > /tmp/health
    slowroll tick /tmp/e2e-a.yml
    slowroll tick /tmp/e2e-a.yml
    slowroll tick /tmp/e2e-a.yml

Expect ADVANCE to 5, STRIKE, HALT with a `::warning::` line, then NOOP. The override is off in the dashboard and the issue says why.

## C: waits at the gate

    echo healthy > /tmp/health
    slowroll reset /tmp/e2e-c.yml; slowroll apply /tmp/e2e-c.yml
    slowroll tick /tmp/e2e-c.yml; sleep 6
    slowroll tick /tmp/e2e-c.yml; sleep 6
    slowroll tick /tmp/e2e-c.yml
    slowroll tick /tmp/e2e-c.yml

Expect ADVANCE to 5, ADVANCE to 25, WAIT_APPROVAL, then NOOP. Add the label `slowroll/approve` to the issue on GitHub, then:

    slowroll tick /tmp/e2e-c.yml; sleep 6
    slowroll tick /tmp/e2e-c.yml; sleep 6
    slowroll tick /tmp/e2e-c.yml

Expect ADVANCE to 50, ADVANCE to 100, FINISH.

## Cleanup

    kill %1
    slowroll reset /tmp/e2e-a.yml; slowroll apply /tmp/e2e-a.yml
