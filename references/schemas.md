# Task, Authority, Decision, and Evidence Schemas

The state machine adds `sequence`, `previous_event_hash`, `event_hash`, and
`occurred_at` to accepted ledgers. Do not supply those managed fields.

## Task input

```json
{
  "task_id": "TASK-001",
  "title": "Implement bounded retry behavior",
  "objective": "Retry transient failures without hiding terminal errors.",
  "task_type": "development",
  "acceptance_criteria": [
    "Transient failures retry no more than the configured limit.",
    "Terminal failures remain visible to the caller."
  ],
  "evidence_requirements": [
    {
      "requirement_id": "REQ-TASK-001-TEST",
      "description": "Focused tests prove retry and terminal failure behavior.",
      "kind": "test"
    }
  ],
  "dependencies": []
}
```

Task IDs are stable. Evidence requirement IDs are unique within a task. Lock
fails on unknown, self, or cyclic dependencies.

## Authority receipt input

```json
{
  "receipt_id": "AUTH-001",
  "action": "configure_limit",
  "target": "create_task_limit",
  "value": 30,
  "exact_user_instruction": "Increase the create task limit to 30.",
  "lead_agent_id": "lead-1"
}
```

Supported action/target pairs are checked by the consuming command. Examples:
`cancel_task` + task ID, `create_task` + new task ID, `configure_limit` + limit
name/value, `model_ranking` + task ID, and `capability_score_authority` + score
authority name. A receipt cannot authorize a different action, target, or value.

The machine hashes the exact instruction for traceability. It cannot
cryptographically authenticate the chat source; the lead must register only an
actual user instruction.

## Auto scope decision input

```json
{
  "title": "Add an in-scope operational task",
  "task_ids": ["TASK-004"],
  "stage": "EXECUTING",
  "decision": "Create TASK-004 within the locked outcome.",
  "rationale": "It is required by an existing acceptance criterion.",
  "authority_basis": "Approved --auto policy",
  "scope_impact": "No expansion of the locked task contract outcome.",
  "scope_classification": "within_locked_scope",
  "reversibility": "reversible",
  "confidence": "high"
}
```

The state machine binds the decision ID to the current locked task-contract
hash. A post-lock auto task or revision needs that decision ID.

## Tandem assignment

```json
{
  "event_type": "tandem_assigned",
  "task_id": "TASK-001",
  "author": {"agent_id": "author-1", "model": "model-x", "reasoning_effort": "high"},
  "reviewer": {"agent_id": "reviewer-1", "model": "model-x", "reasoning_effort": "high"},
  "comparison_basis": "same_model_same_or_higher_effort",
  "capability_requirement_met": true
}
```

For `comparable_runtime_score`, both agents need equal score-authority, scale,
and version metadata plus numeric scores and an authority receipt ID approving
that score authority. For `user_provided_ranking`, include a task-scoped
`authority_receipt_id`.

## Evidence submission

```json
{
  "event_type": "evidence_submitted",
  "task_id": "TASK-001",
  "agent_id": "author-1",
  "requirements_satisfied": ["REQ-TASK-001-TEST"],
  "artifact_paths": ["artifacts/TASK-001/focused-test-result.txt"],
  "summary": "The focused retry tests pass."
}
```

The machine verifies paths, computes per-file hashes, stores the canonical
manifest, and returns `artifact_fingerprint`. Do not invent a fingerprint.
One latest submission must satisfy all current evidence requirements.

## Preflight failures, findings, and correction cycles

```json
{
  "event_type": "preflight_failure",
  "task_id": "TASK-001",
  "agent_id": "author-1",
  "category": "disposable-build",
  "failure_signature": "builder.import.missing-module",
  "diagnostic_artifact_paths": ["artifacts/TASK-001/preflight.txt"],
  "summary": "The disposable build could not import the builder."
}
```

The state machine computes optional diagnostic artifact hashes. Omit
`diagnostic_artifact_paths` when the structured diagnostic needs no file. A
preflight failure changes proof freshness but does not consume a correction cycle.

After `evidence_submitted` returns its machine-computed fingerprint, the assigned
reviewer may submit one consolidated material packet for that candidate:

```json
{
  "event_type": "review_finding_packet",
  "task_id": "TASK-001",
  "packet_id": "PACKET-001",
  "reviewer_agent_id": "reviewer-1",
  "artifact_fingerprint": "sha256:<fingerprint returned by evidence-add>",
  "material": true,
  "findings": [
    {
      "finding_id": "FIND-001",
      "severity": "major",
      "root_cause_family": "terminal-error-propagation",
      "summary": "Terminal error is swallowed."
    }
  ],
  "summary": "Consolidated material findings for this candidate."
}
```

The assigned author binds the response to the same candidate and packet:

```json
{
  "event_type": "correction_cycle",
  "task_id": "TASK-001",
  "agent_id": "author-1",
  "cycle": 1,
  "candidate_fingerprint": "sha256:<same candidate fingerprint>",
  "finding_packet_id": "PACKET-001",
  "summary": "Applied and rechecked the consolidated correction."
}
```

Each candidate and packet may be consumed once. Cycles are monotonic. The configured terminal cycle enters
`NEEDS_USER_DECISION` when still unsuccessful.
The state machine re-verifies the live submitted artifact manifest before accepting
both `review_finding_packet` and `correction_cycle`; direct file drift fails closed.

## Clean adversarial review

```json
{
  "event_type": "review_clean",
  "task_id": "TASK-001",
  "reviewer_agent_id": "reviewer-1",
  "artifact_fingerprint": "sha256:<fingerprint returned by evidence-add>",
  "statement": "no_material_improvement_found"
}
```

## Lead validation

```json
{
  "event_type": "lead_validated",
  "task_id": "TASK-001",
  "lead_agent_id": "lead-1",
  "lead": {"agent_id": "lead-1", "model": "model-x", "reasoning_effort": "high"},
  "comparison_basis": "same_model_same_or_higher_effort",
  "capability_requirement_met": true,
  "disposition": "completed",
  "artifact_fingerprint": "sha256:<same live manifest hash>",
  "summary": "Independent lead validation passed."
}
```

Superseded validation needs matching `replacement_task_ids`. User-cancelled
validation needs the same `authority_receipt_id` registered by `task-cancel`.

## Other evidence events

- `work_started`: agent ID and summary; dependencies must already be completed.
- `task_blocked`: agent ID and blocker summary.
- `task_unblocked`: agent ID and resolution summary.
- `status_observation`: non-mutating proof observation; it does not invalidate a
  lead validation.

## Counting and terminal invariants

`total_task_ids` counts unique IDs regardless of revision or event-line volume.
`lead_validated_task_ids` counts only current, non-stale terminal dispositions.
Superseded and cancelled IDs remain in the denominator. Final completion also
requires task lock, completed active endpoints for replacement closures,
completed dependencies, no block/stale artifact/integrity error, and no pending
user decision. `COMPLETE` is immutable.
