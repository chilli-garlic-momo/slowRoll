# Slowroll

Health-gated progressive rollout for self-hosted, open-source Flagsmith.

Slowroll ramps a feature flag through a percentage segment step by step, checks a health URL between steps, reverts automatically on bad health, pauses for a human approval before a chosen step, and keeps its status and audit trail in one GitHub issue.

Flagsmith's own Release Pipelines is a closed beta whose code lives in a private enterprise package, and the open-source edition has no audit log or change requests. Slowroll brings the same idea to open-source Flagsmith.

Status: work in progress.
