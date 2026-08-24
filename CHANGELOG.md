# Changelog

All notable changes to this plugin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-08-24

### Fixed

- **Critical:** Retired the `review_finding` and `finding_resolved` evidence
  events, which had no reviewer-identity or candidate-fingerprint binding
  (unlike `review_finding_packet`/`correction_cycle`). They allowed an
  unregistered agent to un-complete a lead-validated task, the task's own
  author to self-resolve the finding, and a stale `review_clean` to be
  replayed into a new `lead_validated: completed` with no genuinely new
  independent review. `review_finding_packet` + `correction_cycle` already
  cover the same "a late material finding invalidates completion" need with
  proper binding. Also closed a related replay gap: a `review_clean` event can
  no longer be consumed by more than one `lead_validated: completed`.
- **High:** `NEEDS_USER_DECISION` could be silently cleared back to
  `EXECUTING`/`TASKS_DRAFT` by unrelated task or evidence activity
  (`add_task`, `revise_task`, `supersede_task`, `cancel_task`, and several
  `record_evidence` event types), with no authority receipt tied to the
  actual blocking reason. Only `configure_limits` with a matching receipt can
  now clear it.
- **High:** `init` (`initialize_run`) was not transactional — a failure
  partway through importing a `--tasks-file` (a duplicate task ID, malformed
  JSON, or a missing file) left a partially built, integrity-valid-looking
  run directory behind. A failed `init` now rolls back the run directory it
  created before the error propagates.
- **Medium:** `status()`'s `needs_user_decision` field changed from a single
  object to a list of blocker records, so two independent blockers (e.g. two
  different tasks separately exhausting `review_cycle_limit`, or a
  `review_cycle_limit` hit alongside an unrelated `create_task_limit` hit) no
  longer overwrite each other in live status reporting. `configure_limits`
  now clears only the entry matching the limit it raised.

### Changed

- `VALID_EVIDENCE_EVENTS` no longer includes `review_finding` or
  `finding_resolved` — a **breaking change** to the accepted evidence-event
  schema. Use `review_finding_packet` + `correction_cycle` instead.
- `status()`'s `needs_user_decision` output shape changed from a single
  object (or `null`) to a list of blocker objects (empty list when none are
  outstanding) — a **breaking change** to `status()`'s output shape.

### Documentation

- `SKILL.md` and `references/protocol.md` now state that any task revision —
  including a non-material title/acceptance/evidence clarification — bumps
  the task's revision and invalidates all prior tandem/review/lead-validation
  evidence for that task, so leads can weigh that cost before revising.
- `SKILL.md`'s bootstrap section and `references/protocol.md`'s run-local
  command reference now include bash/zsh command examples alongside the
  existing PowerShell ones.
- `references/protocol.md` now documents the run's single per-run
  `locks/state.lock` file lock (10-second default timeout, 50ms retry
  interval) and that concurrent tandems should expect serialized writes and
  treat a `LockTimeout` as transient.
- `README.md`'s "Why" section now states explicitly that the ledger verifies
  evidence and completion claims but does not sandbox the agents doing the
  work — the auto-mode boundary in `SKILL.md` is an instruction, not a
  technical control.

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
