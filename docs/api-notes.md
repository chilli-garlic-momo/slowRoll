# Flagsmith API notes

Checked on 2026-11-27 against the self-hosted instance running image tag 2.292.0.

| # | Item | Result |
|---|---|---|
| 1 | Feature id lookup: `GET /api/v1/projects/{project}/features/?search=<name>` | Confirmed. Exact match on `name`, numeric `id` (new_checkout is 1, inject_errors is 2). |
| 2 | Segment lookup: `GET /api/v1/projects/{project}/segments/?q=<name>` | Confirmed, including names containing a colon (`slowroll:probe-tmp`). |
| 3 | Segment create: `POST /api/v1/projects/{project}/segments/` | Confirmed with a correction. The nested rule type is `ANY`, not `ALL`, and the condition carries `"property": "$.identity.key"`. Shape: `rules: [{type: ALL, rules: [{type: ANY, conditions: [{operator: PERCENTAGE_SPLIT, property: "$.identity.key", value: "<percent as string>"}]}], conditions: []}]`. Returns 201 with the segment. |
| 4 | Segment update | `PATCH /api/v1/projects/{project}/segments/{id}/` with `{"rules": [...]}` replaces the rules. Rule ids are regenerated and nothing is duplicated. |
| 5 | Identities: `POST /api/v1/identities/` with `X-Environment-Key` | Confirmed. Body `{"identifier": "user-1", "traits": []}`. The response lists `flags`, each with `feature.name` and `enabled`. |
| 6 | Whether % Split accepts 0 | This instance accepted 0. The docs say 1 to 100, so Slowroll uses 1 to 100. |
| 7 | `Authorization: Api-Key <key>` on self-hosted | Confirmed: 200 on `/api/v1/projects/`. No fallback needed. |

Also confirmed:

- `GET /api/__future__/environments/{env_key}/features/{feature_id}/` returns `environment_default` (`enabled`, `value`, `variants`) and `segment_overrides` (each with `segment.id`, `priority`, `enabled`, `value`, `variants`).
- `PATCH` on the same URL with `{"segment_overrides": [{"segment": {"id": N}, "enabled": true}]}` creates the override (the server assigned priority 0) and toggles it on later calls.
- `DELETE /api/v1/projects/{project}/segments/{id}/` removes a segment.

Not exercised: `DELETE .../segment-overrides/{segment_id}/`. Slowroll turns overrides off with `enabled: false` and never calls it.
