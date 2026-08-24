# Changelog

All notable changes to this plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-08-24

### Added

- Initial public release of the `evidence-backed-completion` skill: a
  machine-readable task/evidence ledger protocol that coordinates locked task
  contracts, independent author/reviewer tandems, SHA-256-bound evidence
  artifacts, and lead-validated completion.
- `scripts/evidence_state.py`, the run-local state machine and bootstrap CLI
  that owns the managed ledgers, decisions, run log, and integrity
  fingerprints for each isolated run.
- Reference documentation: `references/protocol.md` (lifecycle, authority,
  tandem, and convergence rules) and `references/schemas.md` (task, authority,
  decision, and evidence input schemas).
- Input/output templates under `assets/templates/`.
- Eval suites under `evals/` covering skill-trigger behavior
  (`trigger-evals.json`) and end-to-end protocol behavior (`evals.json`).
