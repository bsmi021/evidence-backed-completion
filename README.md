# Evidence-Backed Completion

A [Claude Code](https://code.claude.com) plugin that prevents partial work,
off-task activity, and self-attestation from being reported as a finished
multi-task outcome.

It coordinates formal, non-trivial work through a machine-readable
task/evidence ledger: task contracts are locked before execution, every task
gets an independent author/reviewer tandem, evidence artifacts are bound to
SHA-256 fingerprints computed by a state machine (not by the agents doing the
work), and a task only counts as done after a distinct, independent lead
validation against that same live artifact manifest.

## Why

Agentic coding sessions can drift: a task gets marked "done" on the author's
own say-so, a reviewer reuses the author's test run instead of an independent
one, or a final summary claims broad completion off the back of one targeted
check. This plugin closes those gaps by making completion a property of a
verified ledger, not of a chat transcript.

The ledger verifies that evidence was produced and independently reviewed,
and that completion claims match that evidence — it does not sandbox the
agents doing the work. Nothing in the tooling can detect or prevent a worker
agent from taking a destructive, external, or credentialed action while it
produces that evidence; the auto-mode boundary documented in `SKILL.md` is an
instruction the agent is expected to follow, not a technical control.

## Installation

```bash
claude plugin marketplace add bsmi021/evidence-backed-completion
claude plugin install evidence-backed-completion@evidence-backed-completion
```

Or, from inside a Claude Code session:

```
/plugin marketplace add bsmi021/evidence-backed-completion
/plugin install evidence-backed-completion@evidence-backed-completion
```

## Usage

Invoke the skill when you want formal, evidence-gated coordination — not for
an ordinary one-step edit or a quick adversarial review:

> Use $evidence-backed-completion to execute this work with task-level
> evidence and independent validation.

The skill supports two modes:

- **Interactive** — requires a user-supplied task file (or runs a structured
  discovery interview to build one). No implementation starts before the
  task/evidence contract is locked.
- **`--auto`** — derives the initial task/evidence contract from the request
  and available authority, recording material choices in `DECISIONS.md`
  instead of interviewing. It never grants scope expansion, destructive or
  external action, publication, credential use, weaker evidence, or fabricated
  approval.

Each run is bootstrapped into its own isolated directory:

```powershell
python "<plugin-dir>\scripts\evidence_state.py" init `
  --repo-root "<repository-root>" `
  --run-name "<short-run-name>" `
  --tasks-file "<tasks-input.jsonl>"
```

This creates `./runs/<date>-<run-name>-<short-run-id>/` containing a copied
`state_machine.py`, which is the sole writer of that run's managed ledgers
(`tasks.jsonl`, `evidence.jsonl`, `authority.jsonl`, `DECISIONS.md`,
`RUNLOG.md`, `state.json`, `integrity.json`). Workers submit candidate work
through per-task inboxes and artifact directories; they never hand-edit the
managed files directly.

See [`references/protocol.md`](references/protocol.md) for the full lifecycle,
authority, tandem, and convergence rules, and
[`references/schemas.md`](references/schemas.md) for the exact task,
authority, decision, and evidence input shapes.

## How completion is decided

- A task needs a clean, independent adversarial review **and** a distinct,
  qualifying lead validation pass against the same machine-computed artifact
  manifest.
- Completion is counted by unique lead-validated task IDs, never by evidence
  line count.
- Any later proof-changing event (a new finding, a direct artifact change, a
  fresh submission) invalidates a prior validation.
- Blocked work, stale artifacts, unresolved dependencies, or integrity
  mismatches block final completion — `report --final` only succeeds once
  every invariant holds.

## Repository layout

```
.claude-plugin/
  plugin.json          Plugin manifest
  marketplace.json      Single-plugin marketplace manifest
SKILL.md                 Skill definition (name, description, workflow)
references/
  protocol.md            Lifecycle, authority, tandem, and convergence rules
  schemas.md              Task/authority/decision/evidence input schemas
scripts/
  evidence_state.py       Run-local state machine and bootstrap CLI
  tests/                  Unit tests for the state machine
assets/templates/         Command input templates and illustrative output shapes
agents/openai.yaml        Agent interface metadata
evals/                    Skill-trigger and behavioral eval suites
```

## Testing

Run the state machine's unit tests:

```bash
python -m pytest scripts/tests/
```

Run the plugin's eval suites with the Claude Code CLI:

```bash
claude plugin eval evidence-backed-completion
```

## Releasing

Version bumps, tagging, and GitHub releases follow the process documented in
[`CLAUDE.md`](CLAUDE.md).

## License

[MIT](LICENSE)
