# P5.6 Blind Validation Runbook

P5.6 uses a five-stage protocol. Model output is not computed until every human label is frozen.

1. Import 45 to 55 authorized or properly anonymized Shanghai listings priced from RMB 2.3M to 3.3M.
2. Create a run with `POST /api/v1/decisions/validation/runs`.
3. Submit blind labels with `POST /api/v1/decisions/validation/runs/{id}/labels`.
4. Freeze them with `POST /api/v1/decisions/validation/runs/{id}/freeze-labels`.
5. Generate model results with `POST /api/v1/decisions/validation/runs/{id}/generate`.
6. Reveal with `POST /api/v1/decisions/validation/runs/{id}/reveal`.
7. Review every listing with `POST /api/v1/decisions/validation/runs/{id}/reviews`.
8. Finalize with `POST /api/v1/decisions/validation/runs/{id}/finalize`.

All mutation endpoints require the private API key. A run rejects DEMO data, unknown source versions, incomplete geography, weak archetype diversity, and listings outside the price band.

Finalization writes `P56_REAL_WORLD_VALIDATION_REPORT.md` plus an append-only error review under `data/exports/validation/`. Corrections submitted after reveal are stored separately and never replace frozen blind labels.

For P5.7 Set A, reviewers can fill the prepared CSV through the resume-safe terminal tool without
seeing model fields:

```bash
python scripts/p57_label_cli.py --status
python scripts/p57_label_cli.py --reviewer REVIEWER_ID
```

It saves after every listing, refuses model-output columns, does not overwrite completed labels
without `--edit-existing`, and stops accepting edits after the run manifest leaves
`blind_labeling`. When status reports `ready_to_freeze: true`, continue with the existing
`p57_set_a.py freeze-labels` command. The tool assists data entry only; it never creates reviewer
judgments.
