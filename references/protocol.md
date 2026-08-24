# Evidence-Backed Completion Protocol

## Authority boundary

This protocol coordinates formal evidence-gated work. It does not expand scope
or authorize destructive, external, publishing, purchasing, credential, or
third-party actions.

Interactive mode requires a user-supplied task file. If absent, ask for it and
offer a structured interview when the user has none. Explicit `--auto` mode is
the exception: derive the task/evidence contract from the request and repository
authority, record material choices, and stop only for a genuine authority gap.

For a user-authorized cancellation, limit override, model ranking, or non-auto
post-lock task, first register an authority receipt containing the exact user
instruction and action/target/value. Later commands accept only the matching
receipt ID. The receipt supplies auditable traceability; the state machine does
not cryptographically authenticate the chat transport, so the lead remains
responsible for copying the exact instruction rather than inventing approval.

## Lifecycle

1. `AWAITING_TASKS`: no task contract exists.
2. `TASKS_DRAFT`: task definitions and evidence requirements are being created.
3. `TASKS_LOCKED`: dependencies are acyclic/known and the initial task-contract
   hash and unique count are frozen.
4. `EXECUTING`: dependency-ready tandems produce work, findings, corrections,
   and machine-bound evidence.
5. `FINAL_VALIDATION`: all task IDs have candidate valid dispositions.
6. `COMPLETE`: the state machine generated the final report. This state is
   immutable; start a new run for further work.
7. `NEEDS_USER_DECISION`: authority, capability, task budget, or the correction
   limit prevents safe autonomous progress.

An integrity mismatch fails closed before a normal transition. Do not dispatch
new work or rebaseline automatically.

## Task discovery and change control

When an interactive user lacks `tasks.jsonl`, interview in short rounds for:

- requested outcome and non-goals;
- operational tasks, dependencies, and write ownership;
- acceptance criteria and required proof per task;
- permission, tool, and environment constraints;
- decisions that only the user can make.

Every task needs a stable ID, non-empty acceptance criteria, and non-empty
evidence requirements. Unknown, self, or cyclic dependencies fail task lock.
Dependent work cannot start or validate until every prerequisite has a delivered
lead-validated closure. A direct active prerequisite is delivered only by its
current `completed` disposition. A superseded prerequisite is delivered only
when its current `superseded` disposition agrees with its non-empty ledger
replacement set and every replacement recursively has a delivered closure.
Every branch must terminate at an active, currently lead-validated `completed`
task. Missing nodes, cycles, blocked or stale endpoints, unvalidated superseded
nodes, and user-cancelled endpoints are not delivered. Apply this same rule to
work start, evidence submission, preflight failure, clean review, lead
validation, and final completion. Historical dependencies of a task being
validated `superseded` use this closure rule rather than requiring the retired
dependency ID itself to be active and completed.

Retain a task ID for title, acceptance, or evidence clarification that preserves
the outcome. After lock, record a decision bound to the locked task-contract
hash. Objective, task type, or dependency changes require a new task ID.

Any revision, including such a clarification, still bumps the task's revision
counter and invalidates every prior `tandem_assigned`, `evidence_submitted`,
`review_clean`, and `lead_validated` event for that task, so the tandem-review-
validate cycle restarts from scratch; weigh that reset cost before revising.

Post-lock new IDs consume the autonomous task budget. Default: 20;
`--createTaskLimit N` overrides it. Superseded or cancelled IDs never refund the
budget. Every post-lock auto creation needs a recorded
`within_locked_scope` decision. A non-auto creation additionally needs a
matching user authority receipt.

## Tandem and capability contract

Map tasks to independent roles:

- development: implementation author + test/adversarial author;
- planning: planner + feasibility/requirements challenger;
- design: designer + constraints/usability/architecture challenger;
- research: researcher + source/counterevidence verifier;
- documentation: author + factual/structural reviewer;
- investigation: investigator + competing-hypothesis reviewer.

Start both from the task contract. Before reading the author's solution, the
reviewer derives tests, rubric, or challenge packet. For code, preserve
red/green/refactor and never weaken tests to force green.

Capability comparison must use one fail-closed basis:

- `same_model_same_or_higher_effort`;
- `comparable_runtime_score` with matching authority, scale, and version plus a
  registered authority receipt approving that score authority;
- `user_provided_ranking` with a task-scoped authority receipt.

No comparable proof means no valid assignment. The lead validator is distinct
from both tandem agents and satisfies the same capability rule.

## Evidence and artifact binding

Workers place candidate artifacts under `artifacts/<task-id>/` and candidate
event inputs under `inbox/<task-id>/`. The state machine is sole writer of all
managed files.

An `evidence_submitted` event names artifact paths, not caller-invented hashes.
The state machine verifies each file is under the task artifact directory,
computes its SHA-256, records a canonical manifest, and returns the aggregate
manifest fingerprint. One latest reviewed submission must satisfy every current
evidence requirement; requirements cannot be combined across unrelated hashes.

The adversary records `review_clean` only after all material findings are
resolved and uses the returned manifest fingerprint. The lead validates that
same live manifest. A later assignment, finding, correction, block/unblock,
submission, or clean review invalidates the earlier lead validation until a new
lead pass. A direct artifact change also makes status non-completable.

## Correction and convergence

Technical defects found before candidate submission are not review corrections.
Record compile/import, disposable-build, capture-setup, and pre-submission-audit
defects as `preflight_failure` with the assigned agent, a stable category/signature,
and diagnostic summary. Optional diagnostic artifacts are named by path so the state
machine computes their manifest and fingerprint. A preflight failure changes proof
freshness, but it neither requires a reviewer finding nor increments the review-cycle
limit.

Material findings affect correctness, acceptance, required evidence, safety,
regression prevention, source authority, scope, or a task-required usability,
performance, accessibility, or maintainability property. Style preferences,
speculative adjacent work, and merely different solutions are non-blocking
follow-up candidates.

The assigned reviewer submits one `review_finding_packet` per candidate fingerprint.
It contains all material findings, with each root-cause family consolidated rather
than split into artificial cycles. Duplicate observations are folded into that packet;
they do not create another packet or cycle. The state machine rejects a correction
until the assigned author binds it to that packet ID and the same machine-computed
candidate fingerprint. A packet or candidate is consumed by at most one cycle. A
clean review after correction requires a newly submitted corrected candidate. A later
correction additionally requires a new packet bound to that new submission.

Before accepting the reviewer packet, the state machine re-hashes the submitted
candidate's live artifact files and rejects drift. It repeats that verification before
accepting the bound correction cycle, so changed bytes cannot consume a review packet
or bounded cycle under an old fingerprint.

Correction cycles are exact, monotonic integers. With the default limit five,
cycles 1-4 may continue; recording a fifth unsuccessful cycle enters
`NEEDS_USER_DECISION`. Duplicates, skips, and cycles beyond the limit are
rejected. A user may raise a limit through a matching authority receipt.

The tandem clean pass is first. The independent lead pass is second. Any
proof-changing event or artifact change resets convergence.

## Dispositions and completion

Every unique task ID remains in the denominator and needs one current
lead-validated disposition:

- `completed`: active work passed both clean reviews;
- `superseded`: the replacement graph is acyclic and terminates in one or more
  active lead-validated completed tasks;
- `user_cancelled`: the cancellation cites a matching user authority receipt.

A retired-only replacement chain does not deliver the outcome. Blocked tasks,
stale artifacts, undelivered dependency closures, unresolved integrity failures,
and `NEEDS_USER_DECISION` prevent final completion.

`report --final` succeeds only after task lock and every invariant. It writes
state `COMPLETE` before rendering so the report states the terminal status.
Ordinary mutations are rejected afterward.

## Run-local state and commands

The bootstrap creates one workspace-compliant run:

```text
./runs/<date>-<run-name>-<short-run-id>/
|-- state_machine.py
|-- run.json
|-- state.json
|-- tasks.jsonl
|-- evidence.jsonl
|-- authority.jsonl
|-- DECISIONS.md
|-- RUNLOG.md
|-- integrity.json
|-- report.html              generated at reporting
|-- locks/state.lock
|-- inbox/<task-id>/
|-- artifacts/<task-id>/
`-- receipts/
```

Initialize from the installed skill script:

**PowerShell:**

```powershell
python "<skill-dir>\scripts\evidence_state.py" init --repo-root "<root>" --run-name "<name>" --tasks-file "<tasks.jsonl>" [--auto] [--createTaskLimit 20] [--reviewCycleLimit 5]
```

**bash/zsh:**

```bash
python "<skill-dir>/scripts/evidence_state.py" init --repo-root "<root>" --run-name "<name>" --tasks-file "<tasks.jsonl>" [--auto] [--createTaskLimit 20] [--reviewCycleLimit 5]
```

Then use the copied run-local script:

**PowerShell:**

```powershell
python ".\runs\...\state_machine.py" authority-register --authority-file "<authority.json>"
python ".\runs\...\state_machine.py" decision-add --decision-file "<decision.json>"
python ".\runs\...\state_machine.py" task-add --task-file "<task.json>" [--decision-id DEC-...] [--authority-receipt-id AUTH-...]
python ".\runs\...\state_machine.py" task-revise --task-id TASK-001 --changes-file "<changes.json>" --decision-id DEC-...
python ".\runs\...\state_machine.py" task-lock
python ".\runs\...\state_machine.py" evidence-add --event-file "<single-event.json>"
python ".\runs\...\state_machine.py" runlog-add --kind activity --message "<status or action>"
python ".\runs\...\state_machine.py" configure --createTaskLimit 30 --authority-receipt-id AUTH-...
python ".\runs\...\state_machine.py" status
python ".\runs\...\state_machine.py" verify
python ".\runs\...\state_machine.py" report --final
```

**bash/zsh:**

```bash
python "./runs/.../state_machine.py" authority-register --authority-file "<authority.json>"
python "./runs/.../state_machine.py" decision-add --decision-file "<decision.json>"
python "./runs/.../state_machine.py" task-add --task-file "<task.json>" [--decision-id DEC-...] [--authority-receipt-id AUTH-...]
python "./runs/.../state_machine.py" task-revise --task-id TASK-001 --changes-file "<changes.json>" --decision-id DEC-...
python "./runs/.../state_machine.py" task-lock
python "./runs/.../state_machine.py" evidence-add --event-file "<single-event.json>"
python "./runs/.../state_machine.py" runlog-add --kind activity --message "<status or action>"
python "./runs/.../state_machine.py" configure --createTaskLimit 30 --authority-receipt-id AUTH-...
python "./runs/.../state_machine.py" status
python "./runs/.../state_machine.py" verify
python "./runs/.../state_machine.py" report --final
```

`task-supersede` and `task-cancel` use the same disposition rules. Each input
template under `assets/templates/` is either accepted by the named command or
explicitly marked as an illustrative generated-output shape.

Every mutating command above acquires a single `locks/state.lock` file lock
for the whole run directory (default 10-second timeout, 50ms retry interval),
so when multiple dependency-ready tandems submit evidence at the same time
their writes serialize on this one lock rather than running in parallel. A
`LockTimeout` error means another writer held the lock past the timeout —
treat it as transient and retry the command, not as a protocol failure.

## Five-minute status and run log

While agents work, query `status` at least every five minutes and send the user:

- run state;
- total unique IDs and validated completed/retired counts;
- blocked, stale-artifact, and remaining active IDs;
- auto-created count and limit;
- correction activity and next action.

Use `runlog-add` to maintain objective, activity, outcome, and blocker history
without violating integrity. Report completion/failure immediately rather than
waiting for the interval. Do not use a blocking sleep that prevents user
communication.

The final HTML is a review surface, not a substitute for inspecting the actual
work and focused evidence. Targeted checks prove only their selected behavior.
