---
name: evidence-backed-completion
description: Coordinate formal, non-trivial work through machine-readable task/evidence ledgers, monitored execution, independent adversarial review, and lead-validated completion. Use when the user explicitly requests evidence-backed completion, a task/evidence ledger, `--auto` monitored execution, or a formal adversarial workflow with proof for every task. Do not trigger for an ordinary adversarial review, one-step edit, or conversational answer without the formal ledger and completion protocol.
---

# Evidence-Backed Completion

Use this skill to prevent partial work, off-task activity, and self-attestation
from being presented as a finished multi-task outcome.

Read [protocol.md](references/protocol.md) completely before starting a run. It
owns the lifecycle, authority, tandem, monitoring, convergence, and command
rules. Read [schemas.md](references/schemas.md) before creating task, authority,
decision, or evidence inputs.

## Choose the mode

- Interactive: require a user-supplied task file. If it is absent, ask for one;
  if the user has none, offer the structured discovery interview in the
  protocol. Do not begin actual work before the task/evidence contract is
  locked.
- Explicit `--auto`: derive the initial task/evidence contract from the request
  and available authority without the interview. Record material choices in
  `DECISIONS.md`. Stop for missing authority rather than guessing between
  materially different outcomes.

Preserve the user's requested outcome and non-goals. Auto mode does not grant
scope expansion, destructive or external action, publication, credential use,
weaker evidence, or fabricated approval.

## Bootstrap one isolated run

Use the installed script only to bootstrap:

```powershell
python "<skill-dir>\scripts\evidence_state.py" init `
  --repo-root "<repository-root>" `
  --run-name "<short-run-name>" `
  --tasks-file "<tasks-input.jsonl>"
```

Add `--auto`, `--createTaskLimit N`, or `--reviewCycleLimit N` only when the
user selected those settings. The created
`./runs/<date>-<run-name>-<short-run-id>/` is the single run directory for this
formal workflow and satisfies the workspace run-record requirement. Invoke its
copied `state_machine.py` for every later managed-state change.

## Preserve these invariants

- Use the user's operational tasks or decompose only within the requested
  outcome. Lock tasks before execution and retain `initial_task_count` in the
  lead's active session context.
- Give each actual work task a concurrent author/adversary tandem. The reviewer
  derives tests or a challenge packet independently before reading the author's
  solution. Reviewer and lead capability must satisfy the fail-closed rules in
  the protocol; a solo pass is not independent.
- Treat the run-local state machine as sole writer of managed ledgers, decisions,
  run log, state, and fingerprints. Workers submit candidate packets through
  task inboxes. Never hand-edit managed files.
- Record compile/import, disposable-build, capture-setup, and pre-submission
  audit defects as `preflight_failure`; they are proof-changing events but do
  not consume a correction cycle.
- Accept a `correction_cycle` only after the assigned author responds to one
  submitted candidate and the assigned reviewer has bound one consolidated
  material finding packet to that machine-computed candidate fingerprint. Reverify
  the live candidate artifact manifest before accepting both the packet and cycle.
- Query `status` and update the user at least every five minutes while agents
  work. Report sooner on completion, failure, or integrity violation.
- A task needs a clean adversarial pass and a distinct qualifying lead pass
  against the same machine-computed artifact manifest. Later proof-changing
  events invalidate the prior validation.
- Count unique current task IDs with lead-validated dispositions, never evidence
  lines. Blocked work, stale artifacts, invalid retirement chains, incomplete
  dependencies, or integrity mismatches prevent completion.
- Treat a dependency as delivered only when it is either an active,
  lead-validated completed task or a lead-validated superseded task whose every
  replacement branch recursively ends at active lead-validated completed tasks.
  Missing, cyclic, blocked, unvalidated, or cancellation-only branches do not
  satisfy a dependency.
- Generate `report --final` only after the state machine accepts every completion
  invariant. Inspect the actual work and report before responding to the user;
  targeted checks prove only their selected behavior.

The input templates under [assets/templates](assets/templates) are either
direct command inputs or explicitly marked illustrative output shapes. Do not
copy illustrative Markdown templates into an active run.
