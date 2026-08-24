from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "evidence_state.py"
SPEC = importlib.util.spec_from_file_location("evidence_state", SCRIPT)
STATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(STATE)


def task(task_id: str, requirement_id: str | None = None) -> dict:
    requirement_id = requirement_id or f"REQ-{task_id}"
    return {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "objective": f"Deliver the outcome for {task_id}.",
        "task_type": "development",
        "acceptance_criteria": [f"{task_id} behaves as specified."],
        "evidence_requirements": [
            {
                "requirement_id": requirement_id,
                "description": f"Focused proof for {task_id}.",
                "kind": "test",
            }
        ],
        "dependencies": [],
    }


def assignment(task_id: str) -> dict:
    return {
        "event_type": "tandem_assigned",
        "task_id": task_id,
        "author": {
            "agent_id": f"author-{task_id}",
            "model": "model-x",
            "reasoning_effort": "high",
        },
        "reviewer": {
            "agent_id": f"reviewer-{task_id}",
            "model": "model-x",
            "reasoning_effort": "high",
        },
        "comparison_basis": "same_model_same_or_higher_effort",
        "capability_requirement_met": True,
    }


def auto_decision(machine, task_id: str) -> str:
    return machine.add_decision(
        {
            "title": f"Add {task_id} within the locked outcome",
            "task_ids": [task_id],
            "stage": "EXECUTING",
            "decision": f"Create {task_id} as an in-scope operational task.",
            "rationale": "The task is required by the existing requested outcome.",
            "authority_basis": "Approved --auto policy",
            "scope_impact": "No expansion of the locked task contract outcome.",
            "scope_classification": "within_locked_scope",
            "reversibility": "reversible",
            "confidence": "high",
        }
    )


def authority(machine, receipt_id: str, action: str, target: str, value=None) -> str:
    machine.register_authority(
        {
            "receipt_id": receipt_id,
            "action": action,
            "target": target,
            "value": value,
            "exact_user_instruction": f"Authorize {action} for {target} with value {value}.",
            "lead_agent_id": "lead-authority",
        }
    )
    return receipt_id


def write_artifact(machine, task_id: str, name: str = "result.txt", content: str = "proof") -> str:
    path = machine.run_dir / "artifacts" / task_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path.relative_to(machine.run_dir).as_posix()


def complete_task(machine, task_id: str, artifact_content: str = "proof") -> None:
    requirement_id = f"REQ-{task_id}"
    artifact_path = write_artifact(machine, task_id, content=artifact_content)
    machine.record_evidence(assignment(task_id))
    submission = machine.record_evidence(
        {
            "event_type": "evidence_submitted",
            "task_id": task_id,
            "agent_id": f"author-{task_id}",
            "requirements_satisfied": [requirement_id],
            "artifact_paths": [artifact_path],
            "summary": "Focused proof passed.",
        }
    )
    fingerprint = submission["artifact_fingerprint"]
    machine.record_evidence(
        {
            "event_type": "review_clean",
            "task_id": task_id,
            "reviewer_agent_id": f"reviewer-{task_id}",
            "artifact_fingerprint": fingerprint,
            "statement": "no_material_improvement_found",
        }
    )
    machine.record_evidence(
        {
            "event_type": "lead_validated",
            "task_id": task_id,
            "lead_agent_id": f"lead-{task_id}",
            "lead": {
                "agent_id": f"lead-{task_id}",
                "model": "model-x",
                "reasoning_effort": "high",
            },
            "comparison_basis": "same_model_same_or_higher_effort",
            "capability_requirement_met": True,
            "disposition": "completed",
            "artifact_fingerprint": fingerprint,
            "summary": "Independent lead validation passed.",
        }
    )


def validate_superseded(machine, task_id: str, replacement_task_ids: list[str]) -> None:
    machine.record_evidence(
        {
            "event_type": "lead_validated",
            "task_id": task_id,
            "lead_agent_id": f"lead-{task_id}",
            "disposition": "superseded",
            "replacement_task_ids": replacement_task_ids,
            "summary": "Replacement closure checked.",
        }
    )


def cancel_and_validate(machine, task_id: str) -> None:
    receipt_id = authority(
        machine,
        f"AUTH-CANCEL-{task_id}",
        "cancel_task",
        task_id,
    )
    machine.cancel_task(task_id, receipt_id)
    machine.record_evidence(
        {
            "event_type": "lead_validated",
            "task_id": task_id,
            "lead_agent_id": f"lead-{task_id}",
            "disposition": "user_cancelled",
            "authority_receipt_id": receipt_id,
            "summary": "Cancellation checked.",
        }
    )


def submit_candidate(machine, task_id: str, name: str = "candidate.txt") -> str:
    artifact_path = write_artifact(machine, task_id, name=name, content=name)
    submission = machine.record_evidence(
        {
            "event_type": "evidence_submitted",
            "task_id": task_id,
            "agent_id": f"author-{task_id}",
            "requirements_satisfied": [f"REQ-{task_id}"],
            "artifact_paths": [artifact_path],
            "summary": "Submitted candidate proof.",
        }
    )
    return submission["artifact_fingerprint"]


def submit_finding_packet(machine, task_id: str, fingerprint: str, packet_id: str) -> None:
    machine.record_evidence(
        {
            "event_type": "review_finding_packet",
            "task_id": task_id,
            "packet_id": packet_id,
            "reviewer_agent_id": f"reviewer-{task_id}",
            "artifact_fingerprint": fingerprint,
            "material": True,
            "findings": [
                {
                    "finding_id": f"{packet_id}-FINDING",
                    "severity": "major",
                    "root_cause_family": "correctness",
                    "summary": "The candidate has one consolidated material defect.",
                }
            ],
            "summary": "Consolidated material review packet.",
        }
    )


def record_bound_cycle(machine, task_id: str, cycle: int) -> None:
    fingerprint = submit_candidate(machine, task_id, name=f"candidate-{cycle}.txt")
    packet_id = f"PACKET-{cycle}"
    submit_finding_packet(machine, task_id, fingerprint, packet_id)
    machine.record_evidence(
        {
            "event_type": "correction_cycle",
            "task_id": task_id,
            "agent_id": f"author-{task_id}",
            "cycle": cycle,
            "candidate_fingerprint": fingerprint,
            "finding_packet_id": packet_id,
            "summary": f"Correction cycle {cycle} responds to the consolidated packet.",
        }
    )


class EvidenceStateTests(unittest.TestCase):
    def make_run(self, root: Path, **kwargs):
        run_dir = STATE.initialize_run(
            root,
            "focused-proof",
            run_id=kwargs.pop("run_id", "abc12345"),
            now="2026-08-22T12:00:00Z",
            **kwargs,
        )
        return run_dir, STATE.StateMachine(run_dir)

    def test_initialize_creates_isolated_run_and_managed_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            run_dir, machine = self.make_run(root)

            self.assertEqual(
                root / "runs" / "2026-08-22-focused-proof-abc12345",
                run_dir,
            )
            for relative in (
                "state_machine.py",
                "run.json",
                "state.json",
                "tasks.jsonl",
                "evidence.jsonl",
                "authority.jsonl",
                "DECISIONS.md",
                "RUNLOG.md",
                "integrity.json",
                "locks/state.lock",
            ):
                self.assertTrue((run_dir / relative).exists(), relative)
            for relative in ("inbox", "artifacts", "receipts"):
                self.assertTrue((run_dir / relative).is_dir(), relative)
            self.assertTrue(machine.verify_integrity()["valid"])

    def test_same_named_runs_receive_distinct_run_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            first = STATE.initialize_run(root, "same-name")
            second = STATE.initialize_run(root, "same-name")

            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())

    def test_cli_bootstrap_hands_off_to_run_local_state_machine(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tasks_file = root / "input-tasks.jsonl"
            tasks_file.write_text(json.dumps(task("TASK-001")) + "\n", encoding="utf-8")

            initialized = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "init",
                    "--repo-root",
                    str(root),
                    "--run-name",
                    "cli-handoff",
                    "--tasks-file",
                    str(tasks_file),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            run_dir = Path(json.loads(initialized.stdout)["run_dir"])
            local_machine = run_dir / "state_machine.py"
            subprocess.run(
                [sys.executable, str(local_machine), "task-lock"],
                check=True,
                capture_output=True,
                text=True,
            )
            status_result = subprocess.run(
                [sys.executable, str(local_machine), "status"],
                check=True,
                capture_output=True,
                text=True,
            )

            status = json.loads(status_result.stdout)
            self.assertEqual(1, status["initial_task_count"])
            self.assertEqual("TASKS_LOCKED", status["run_state"])
            subprocess.run(
                [
                    sys.executable,
                    str(local_machine),
                    "runlog-add",
                    "--kind",
                    "objective",
                    "--message",
                    "Exercise the run-local CLI boundary.",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn(
                "Exercise the run-local CLI boundary.",
                (run_dir / "RUNLOG.md").read_text(encoding="utf-8"),
            )
            verified = subprocess.run(
                [sys.executable, str(local_machine), "verify"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(json.loads(verified.stdout)["valid"])

    def test_manual_managed_file_edit_is_detected_before_next_transition(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            with (run_dir / "tasks.jsonl").open("a", encoding="utf-8") as handle:
                handle.write('{"external":true}\n')

            with self.assertRaises(STATE.IntegrityViolation):
                machine.lock_tasks()

            report = machine.verify_integrity()
            self.assertFalse(report["valid"])
            self.assertIn("tasks.jsonl", report["mismatches"])

    def test_evidence_count_uses_unique_lead_validated_task_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            complete_task(machine, "TASK-001")
            machine.record_evidence(
                {
                    "event_type": "status_observation",
                    "task_id": "TASK-001",
                    "agent_id": "lead-TASK-001",
                    "summary": "Extra evidence line must not inflate completion.",
                }
            )

            status = machine.status()

            self.assertEqual(1, status["total_task_ids"])
            self.assertGreater(status["evidence_event_count"], 1)
            self.assertEqual(1, status["lead_validated_task_ids"])
            self.assertEqual(1, status["validated_completed"])
            self.assertTrue(status["can_complete"])

    def test_lead_validation_requires_matching_clean_review_and_independent_agents(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))
            artifact_path = write_artifact(machine, "TASK-001")
            submission = machine.record_evidence(
                {
                    "event_type": "evidence_submitted",
                    "task_id": "TASK-001",
                    "agent_id": "author-TASK-001",
                    "requirements_satisfied": ["REQ-TASK-001"],
                    "artifact_paths": [artifact_path],
                    "summary": "Focused proof passed.",
                }
            )
            machine.record_evidence(
                {
                    "event_type": "review_clean",
                    "task_id": "TASK-001",
                    "reviewer_agent_id": "reviewer-TASK-001",
                    "artifact_fingerprint": submission["artifact_fingerprint"],
                    "statement": "no_material_improvement_found",
                }
            )

            with self.assertRaises(STATE.TransitionError):
                machine.record_evidence(
                    {
                        "event_type": "lead_validated",
                        "task_id": "TASK-001",
                        "lead_agent_id": "author-TASK-001",
                        "lead": {
                            "agent_id": "author-TASK-001",
                            "model": "model-x",
                            "reasoning_effort": "high",
                        },
                        "comparison_basis": "same_model_same_or_higher_effort",
                        "capability_requirement_met": True,
                        "disposition": "completed",
                        "artifact_fingerprint": f"sha256:{'b' * 64}",
                        "summary": "Invalid validation.",
                    }
                )

            self.assertEqual(0, machine.status()["lead_validated_task_ids"])

    def test_unprovable_tandem_capability_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            event = assignment("TASK-001")
            event["capability_requirement_met"] = False

            with self.assertRaises(STATE.CapabilityError):
                machine.record_evidence(event)

    def test_claimed_capability_parity_must_match_declared_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            event = assignment("TASK-001")
            event["reviewer"]["reasoning_effort"] = "low"

            with self.assertRaises(STATE.CapabilityError):
                machine.record_evidence(event)

    def test_runtime_scores_require_registered_comparability_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            event = assignment("TASK-001")
            event["comparison_basis"] = "comparable_runtime_score"
            event["author"].update(
                {
                    "capability_score": 80,
                    "capability_score_authority": "runtime-router",
                    "capability_score_scale": "0-100",
                    "capability_score_version": "v1",
                }
            )
            event["reviewer"].update(
                {
                    "capability_score": 90,
                    "capability_score_authority": "runtime-router",
                    "capability_score_scale": "0-100",
                    "capability_score_version": "v1",
                }
            )

            with self.assertRaises(STATE.CapabilityError):
                machine.record_evidence(event)

            receipt_id = authority(
                machine,
                "AUTH-SCORE-1",
                "capability_score_authority",
                "runtime-router",
            )
            event["authority_receipt_id"] = receipt_id
            machine.record_evidence(event)

    def test_clean_review_fingerprint_must_match_submitted_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))
            artifact_path = write_artifact(machine, "TASK-001")
            machine.record_evidence(
                {
                    "event_type": "evidence_submitted",
                    "task_id": "TASK-001",
                    "agent_id": "author-TASK-001",
                    "requirements_satisfied": ["REQ-TASK-001"],
                    "artifact_paths": [artifact_path],
                    "summary": "Focused proof passed.",
                }
            )

            with self.assertRaises(STATE.TransitionError):
                machine.record_evidence(
                    {
                        "event_type": "review_clean",
                        "task_id": "TASK-001",
                        "reviewer_agent_id": "reviewer-TASK-001",
                        "artifact_fingerprint": f"sha256:{'b' * 64}",
                        "statement": "no_material_improvement_found",
                    }
                )

    def test_post_lock_auto_task_limit_is_not_refunded_by_supersession(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(
                Path(temp), auto=True, create_task_limit=2
            )
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.add_task(task("TASK-002"), decision_id=auto_decision(machine, "TASK-002"))
            machine.add_task(task("TASK-003"), decision_id=auto_decision(machine, "TASK-003"))
            machine.supersede_task("TASK-002", ["TASK-003"])

            with self.assertRaises(STATE.TaskLimitExceeded):
                machine.add_task(
                    task("TASK-004"), decision_id=auto_decision(machine, "TASK-004")
                )

            status = machine.status()
            self.assertEqual(2, status["auto_created_tasks"])
            self.assertEqual(2, status["create_task_limit"])
            self.assertEqual("NEEDS_USER_DECISION", status["run_state"])

    def test_user_can_raise_task_limit_with_recorded_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir, machine = self.make_run(
                Path(temp), auto=True, create_task_limit=1
            )
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.add_task(task("TASK-002"), decision_id=auto_decision(machine, "TASK-002"))
            with self.assertRaises(STATE.TaskLimitExceeded):
                machine.add_task(
                    task("TASK-003"), decision_id=auto_decision(machine, "TASK-003")
                )

            receipt_id = authority(
                machine,
                "AUTH-LIMIT-1",
                "configure_limit",
                "create_task_limit",
                3,
            )
            machine.configure_limits(
                create_task_limit=3,
                authority_receipt_id=receipt_id,
            )
            machine.add_task(task("TASK-003"), decision_id=auto_decision(machine, "TASK-003"))

            status = machine.status()
            self.assertEqual(3, status["create_task_limit"])
            self.assertEqual(2, status["auto_created_tasks"])
            self.assertIn(
                "AUTH-LIMIT-1",
                (run_dir / "DECISIONS.md").read_text(encoding="utf-8"),
            )

    def test_task_revision_invalidates_prior_revision_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            complete_task(machine, "TASK-001")

            machine.revise_task(
                "TASK-001",
                {"acceptance_criteria": ["Revised behavior is proven."]},
                evidence_invalidated=["REQ-TASK-001"],
                decision_id=auto_decision(machine, "TASK-001"),
            )

            status = machine.status()
            self.assertEqual(0, status["lead_validated_task_ids"])
            self.assertEqual(1, status["remaining_active_tasks"])
            self.assertFalse(status["can_complete"])

    def test_validated_retired_dispositions_preserve_count_and_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp), auto=True)
            machine.add_task(task("TASK-001"))
            machine.add_task(task("TASK-002"))
            machine.add_task(task("TASK-003"))
            machine.lock_tasks()
            complete_task(machine, "TASK-001")
            machine.supersede_task("TASK-002", ["TASK-001"])
            cancel_receipt = authority(
                machine, "AUTH-CANCEL-3", "cancel_task", "TASK-003"
            )
            machine.cancel_task("TASK-003", cancel_receipt)
            machine.record_evidence(
                {
                    "event_type": "lead_validated",
                    "task_id": "TASK-002",
                    "lead_agent_id": "lead-retirement",
                    "disposition": "superseded",
                    "replacement_task_ids": ["TASK-001"],
                    "summary": "Replacement relationship is valid.",
                }
            )
            machine.record_evidence(
                {
                    "event_type": "lead_validated",
                    "task_id": "TASK-003",
                    "lead_agent_id": "lead-retirement",
                    "disposition": "user_cancelled",
                    "authority_receipt_id": cancel_receipt,
                    "summary": "User cancellation authority verified.",
                }
            )

            status = machine.status()

            self.assertEqual(3, status["total_task_ids"])
            self.assertEqual(3, status["lead_validated_task_ids"])
            self.assertEqual(1, status["validated_completed"])
            self.assertEqual(1, status["validated_superseded"])
            self.assertEqual(1, status["validated_user_cancelled"])
            self.assertTrue(status["can_complete"])

    def test_blocked_task_prevents_final_report_until_resolved(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(
                {
                    "event_type": "task_blocked",
                    "task_id": "TASK-001",
                    "agent_id": "author-TASK-001",
                    "summary": "Missing authority.",
                }
            )

            with self.assertRaises(STATE.IncompleteRunError):
                machine.generate_report(final=True)

            machine.record_evidence(
                {
                    "event_type": "task_unblocked",
                    "task_id": "TASK-001",
                    "agent_id": "lead-TASK-001",
                    "summary": "Authority supplied.",
                }
            )
            complete_task(machine, "TASK-001")

            report = machine.generate_report(final=True)

            self.assertEqual(run_dir / "report.html", report)
            self.assertIn("Evidence-Backed Completion Report", report.read_text(encoding="utf-8"))
            self.assertTrue(machine.status()["can_complete"])

    def test_runlog_is_updated_only_through_integrity_preserving_command(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir, machine = self.make_run(Path(temp))

            machine.update_runlog("objective", "Deliver a focused evidence workflow.")
            machine.update_runlog("activity", "Initialized the task contract.")
            machine.update_runlog("blocker", "Awaiting reviewer capability metadata.")
            machine.update_runlog("outcome", "Focused initialization succeeded.")

            log = (run_dir / "RUNLOG.md").read_text(encoding="utf-8")
            self.assertIn("Deliver a focused evidence workflow.", log)
            self.assertIn("Initialized the task contract.", log)
            self.assertIn("Awaiting reviewer capability metadata.", log)
            self.assertIn("Focused initialization succeeded.", log)
            self.assertTrue(machine.verify_integrity()["valid"])

    def test_execution_and_completion_are_rejected_before_task_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))

            with self.assertRaises(STATE.TransitionError):
                machine.record_evidence(assignment("TASK-001"))
            with self.assertRaises(STATE.IncompleteRunError):
                machine.generate_report(final=True)

            self.assertFalse(machine.status()["can_complete"])
            self.assertEqual(0, machine.status()["initial_task_count"])

    def test_late_material_finding_invalidates_lead_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            complete_task(machine, "TASK-001")

            machine.record_evidence(
                {
                    "event_type": "review_finding",
                    "task_id": "TASK-001",
                    "finding_id": "FIND-LATE",
                    "material": True,
                    "severity": "major",
                    "summary": "A late material defect was found.",
                }
            )

            status = machine.status()
            self.assertEqual(0, status["lead_validated_task_ids"])
            self.assertFalse(status["can_complete"])

    def test_late_changed_evidence_invalidates_lead_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            complete_task(machine, "TASK-001")
            changed = write_artifact(
                machine, "TASK-001", name="changed.txt", content="changed proof"
            )

            machine.record_evidence(
                {
                    "event_type": "evidence_submitted",
                    "task_id": "TASK-001",
                    "agent_id": "author-TASK-001",
                    "requirements_satisfied": ["REQ-TASK-001"],
                    "artifact_paths": [changed],
                    "summary": "Artifact changed after validation.",
                }
            )

            self.assertEqual(0, machine.status()["lead_validated_task_ids"])

    def test_direct_artifact_change_invalidates_lead_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            complete_task(machine, "TASK-001")
            artifact_path = machine.run_dir / "artifacts" / "TASK-001" / "result.txt"

            artifact_path.write_text("externally changed proof", encoding="utf-8")

            status = machine.status()
            self.assertEqual(0, status["lead_validated_task_ids"])
            self.assertEqual(["TASK-001"], status["stale_artifact_task_ids"])

    def test_late_assignment_and_preflight_failure_each_invalidate_validation(self):
        for late_event in (
            assignment("TASK-001"),
            {
                "event_type": "preflight_failure",
                "task_id": "TASK-001",
                "agent_id": "author-TASK-001",
                "category": "pre-submission-audit",
                "failure_signature": "audit.late-stale-proof",
                "summary": "Post-validation preflight failure.",
            },
        ):
            with self.subTest(event_type=late_event["event_type"]):
                with tempfile.TemporaryDirectory() as temp:
                    _, machine = self.make_run(Path(temp))
                    machine.add_task(task("TASK-001"))
                    machine.lock_tasks()
                    complete_task(machine, "TASK-001")

                    machine.record_evidence(late_event)

                    self.assertEqual(0, machine.status()["lead_validated_task_ids"])

    def test_nonexistent_artifact_and_invented_hash_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))

            with self.assertRaises(STATE.TransitionError):
                machine.record_evidence(
                    {
                        "event_type": "evidence_submitted",
                        "task_id": "TASK-001",
                        "agent_id": "author-TASK-001",
                        "requirements_satisfied": ["REQ-TASK-001"],
                        "artifact_paths": ["artifacts/TASK-001/missing.txt"],
                        "artifact_fingerprint": f"sha256:{'a' * 64}",
                        "summary": "Invented proof.",
                    }
                )

    def test_one_reviewed_submission_must_satisfy_all_requirements(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            definition = task("TASK-001")
            definition["evidence_requirements"].append(
                {
                    "requirement_id": "REQ-TASK-001-SECOND",
                    "description": "Second focused proof.",
                    "kind": "test",
                }
            )
            machine.add_task(definition)
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))
            first_path = write_artifact(machine, "TASK-001", "first.txt", "first")
            second_path = write_artifact(machine, "TASK-001", "second.txt", "second")
            machine.record_evidence(
                {
                    "event_type": "evidence_submitted",
                    "task_id": "TASK-001",
                    "agent_id": "author-TASK-001",
                    "requirements_satisfied": ["REQ-TASK-001"],
                    "artifact_paths": [first_path],
                    "summary": "First partial proof.",
                }
            )
            second = machine.record_evidence(
                {
                    "event_type": "evidence_submitted",
                    "task_id": "TASK-001",
                    "agent_id": "author-TASK-001",
                    "requirements_satisfied": ["REQ-TASK-001-SECOND"],
                    "artifact_paths": [second_path],
                    "summary": "Second partial proof.",
                }
            )

            with self.assertRaises(STATE.TransitionError):
                machine.record_evidence(
                    {
                        "event_type": "review_clean",
                        "task_id": "TASK-001",
                        "reviewer_agent_id": "reviewer-TASK-001",
                        "artifact_fingerprint": second["artifact_fingerprint"],
                        "statement": "no_material_improvement_found",
                    }
                )

    def test_preflight_failure_is_accepted_before_submission_without_consuming_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp), review_cycle_limit=1)
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))

            event = machine.record_evidence(
                {
                    "event_type": "preflight_failure",
                    "task_id": "TASK-001",
                    "agent_id": "author-TASK-001",
                    "category": "disposable-build",
                    "failure_signature": "builder.import.missing-module",
                    "summary": "Disposable build could not import the builder.",
                }
            )

            self.assertEqual("preflight_failure", event["event_type"])
            self.assertEqual("EXECUTING", machine.status()["run_state"])
            self.assertFalse(machine.status()["needs_user_decision"])
            self.assertFalse(
                any(
                    item["event_type"] == "correction_cycle"
                    for item in machine._evidence_events()
                )
            )
            with self.assertRaisesRegex(STATE.TransitionError, "computed only"):
                machine.record_evidence(
                    {
                        "event_type": "preflight_failure",
                        "task_id": "TASK-001",
                        "agent_id": "author-TASK-001",
                        "category": "capture-setup",
                        "failure_signature": "capture.camera.missing",
                        "diagnostic_artifact_fingerprint": f"sha256:{'f' * 64}",
                        "summary": "Caller attempted to invent diagnostic identity.",
                    }
                )

    def test_preflight_artifact_identity_is_machine_computed_and_late_failure_invalidates(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            complete_task(machine, "TASK-001")
            diagnostic = write_artifact(
                machine, "TASK-001", "preflight.txt", "compile failure"
            )

            event = machine.record_evidence(
                {
                    "event_type": "preflight_failure",
                    "task_id": "TASK-001",
                    "agent_id": "author-TASK-001",
                    "category": "compile-import",
                    "failure_signature": "compile.syntax.line-3",
                    "diagnostic_artifact_paths": [diagnostic],
                    "summary": "A late compile preflight failed.",
                }
            )

            self.assertRegex(event["diagnostic_artifact_fingerprint"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(0, machine.status()["lead_validated_task_ids"])

    def test_correction_requires_bound_candidate_and_consolidated_material_packet(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))

            base = {
                "event_type": "correction_cycle",
                "task_id": "TASK-001",
                "agent_id": "author-TASK-001",
                "cycle": 1,
                "candidate_fingerprint": f"sha256:{'a' * 64}",
                "finding_packet_id": "PACKET-1",
                "summary": "Attempted correction.",
            }
            with self.assertRaisesRegex(STATE.TransitionError, "submitted candidate"):
                machine.record_evidence(dict(base))

            fingerprint = submit_candidate(machine, "TASK-001")
            base["candidate_fingerprint"] = fingerprint
            with self.assertRaisesRegex(STATE.TransitionError, "finding packet"):
                machine.record_evidence(dict(base))

            submit_finding_packet(machine, "TASK-001", fingerprint, "PACKET-1")
            accepted = machine.record_evidence(dict(base))
            self.assertEqual(1, accepted["cycle"])
            with self.assertRaisesRegex(STATE.TransitionError, "already consumed|monotonic"):
                machine.record_evidence(dict(base))

    def test_finding_packet_rejects_non_material_stale_reviewer_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))
            fingerprint = submit_candidate(machine, "TASK-001")
            packet = {
                "event_type": "review_finding_packet",
                "task_id": "TASK-001",
                "packet_id": "PACKET-1",
                "reviewer_agent_id": "reviewer-TASK-001",
                "artifact_fingerprint": fingerprint,
                "material": False,
                "findings": [{"finding_id": "FIND-1", "severity": "minor", "root_cause_family": "style", "summary": "Preference."}],
                "summary": "Not material.",
            }
            with self.assertRaisesRegex(STATE.TransitionError, "material"):
                machine.record_evidence(packet)
            packet["material"] = True
            packet["reviewer_agent_id"] = "author-TASK-001"
            with self.assertRaisesRegex(STATE.TransitionError, "assigned reviewer"):
                machine.record_evidence(packet)
            packet["reviewer_agent_id"] = "reviewer-TASK-001"
            packet["artifact_fingerprint"] = f"sha256:{'f' * 64}"
            with self.assertRaisesRegex(STATE.TransitionError, "submitted candidate"):
                machine.record_evidence(packet)
            packet["artifact_fingerprint"] = fingerprint
            machine.record_evidence(packet)
            duplicate = dict(packet)
            duplicate["packet_id"] = "PACKET-2"
            with self.assertRaisesRegex(STATE.TransitionError, "one consolidated"):
                machine.record_evidence(duplicate)

    def test_correction_rejects_author_reviewer_fingerprint_and_packet_mismatches(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))
            fingerprint = submit_candidate(machine, "TASK-001")
            submit_finding_packet(machine, "TASK-001", fingerprint, "PACKET-1")
            event = {
                "event_type": "correction_cycle",
                "task_id": "TASK-001",
                "agent_id": "reviewer-TASK-001",
                "cycle": 1,
                "candidate_fingerprint": fingerprint,
                "finding_packet_id": "PACKET-1",
                "summary": "Wrong author.",
            }
            with self.assertRaisesRegex(STATE.TransitionError, "assigned author"):
                machine.record_evidence(event)
            event["agent_id"] = "author-TASK-001"
            event["candidate_fingerprint"] = f"sha256:{'e' * 64}"
            with self.assertRaisesRegex(STATE.TransitionError, "submitted candidate"):
                machine.record_evidence(event)
            event["candidate_fingerprint"] = fingerprint
            event["finding_packet_id"] = "INVENTED"
            with self.assertRaisesRegex(STATE.TransitionError, "finding packet"):
                machine.record_evidence(event)

    def test_finding_packet_rejects_direct_candidate_artifact_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))
            fingerprint = submit_candidate(machine, "TASK-001")
            candidate = machine.run_dir / "artifacts" / "TASK-001" / "candidate.txt"
            candidate.write_text("mutated after submission", encoding="utf-8")

            with self.assertRaisesRegex(STATE.TransitionError, "stale or changed"):
                submit_finding_packet(machine, "TASK-001", fingerprint, "PACKET-1")

    def test_correction_rejects_candidate_drift_after_finding_packet(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))
            fingerprint = submit_candidate(machine, "TASK-001")
            submit_finding_packet(machine, "TASK-001", fingerprint, "PACKET-1")
            candidate = machine.run_dir / "artifacts" / "TASK-001" / "candidate.txt"
            candidate.write_text("mutated after reviewer packet", encoding="utf-8")

            with self.assertRaisesRegex(STATE.TransitionError, "stale or changed"):
                machine.record_evidence(
                    {
                        "event_type": "correction_cycle",
                        "task_id": "TASK-001",
                        "agent_id": "author-TASK-001",
                        "cycle": 1,
                        "candidate_fingerprint": fingerprint,
                        "finding_packet_id": "PACKET-1",
                        "summary": "Attempted correction against stale bytes.",
                    }
                )

    def test_clean_review_after_correction_requires_new_candidate_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))
            fingerprint = submit_candidate(machine, "TASK-001")
            submit_finding_packet(machine, "TASK-001", fingerprint, "PACKET-1")
            machine.record_evidence(
                {
                    "event_type": "correction_cycle",
                    "task_id": "TASK-001",
                    "agent_id": "author-TASK-001",
                    "cycle": 1,
                    "candidate_fingerprint": fingerprint,
                    "finding_packet_id": "PACKET-1",
                    "summary": "Responded to consolidated findings.",
                }
            )
            with self.assertRaisesRegex(STATE.TransitionError, "newly submitted"):
                machine.record_evidence(
                    {
                        "event_type": "review_clean",
                        "task_id": "TASK-001",
                        "reviewer_agent_id": "reviewer-TASK-001",
                        "artifact_fingerprint": fingerprint,
                        "statement": "no_material_improvement_found",
                    }
                )

            corrected = submit_candidate(machine, "TASK-001", "corrected.txt")
            machine.record_evidence(
                {
                    "event_type": "review_clean",
                    "task_id": "TASK-001",
                    "reviewer_agent_id": "reviewer-TASK-001",
                    "artifact_fingerprint": corrected,
                    "statement": "no_material_improvement_found",
                }
            )

    def test_correction_cycles_are_monotonic_and_fifth_requires_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp), review_cycle_limit=5)
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))
            for cycle in range(1, 5):
                record_bound_cycle(machine, "TASK-001", cycle)
            with self.assertRaises(STATE.TransitionError):
                machine.record_evidence(
                    {
                        "event_type": "correction_cycle",
                        "task_id": "TASK-001",
                        "agent_id": "author-TASK-001",
                        "cycle": 4,
                        "summary": "Duplicate cycle.",
                    }
                )
            with self.assertRaises(STATE.TransitionError):
                record_bound_cycle(machine, "TASK-001", 5)

            self.assertEqual("NEEDS_USER_DECISION", machine.status()["run_state"])
            cycles = [
                event["cycle"]
                for event in machine._evidence_events()
                if event["event_type"] == "correction_cycle"
            ]
            self.assertEqual([1, 2, 3, 4, 5], cycles)

    def test_unrelated_limit_receipt_cannot_clear_correction_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp), auto=True, review_cycle_limit=5)
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-001"))
            for cycle in range(1, 5):
                record_bound_cycle(machine, "TASK-001", cycle)
            with self.assertRaises(STATE.TransitionError):
                record_bound_cycle(machine, "TASK-001", 5)

            unrelated = authority(
                machine,
                "AUTH-CREATE-LIMIT",
                "configure_limit",
                "create_task_limit",
                30,
            )
            machine.configure_limits(
                create_task_limit=30,
                authority_receipt_id=unrelated,
            )
            self.assertEqual("NEEDS_USER_DECISION", machine.status()["run_state"])

            matching = authority(
                machine,
                "AUTH-REVIEW-LIMIT",
                "configure_limit",
                "review_cycle_limit",
                6,
            )
            machine.configure_limits(
                review_cycle_limit=6,
                authority_receipt_id=matching,
            )
            self.assertEqual("EXECUTING", machine.status()["run_state"])

    def test_unknown_and_cyclic_dependencies_fail_task_lock(self):
        with tempfile.TemporaryDirectory() as first_temp:
            _, machine = self.make_run(Path(first_temp))
            dependent = task("TASK-001")
            dependent["dependencies"] = ["TASK-UNKNOWN"]
            machine.add_task(dependent)
            with self.assertRaises(STATE.TransitionError):
                machine.lock_tasks()

        with tempfile.TemporaryDirectory() as second_temp:
            _, machine = self.make_run(Path(second_temp))
            first = task("TASK-001")
            second = task("TASK-002")
            first["dependencies"] = ["TASK-002"]
            second["dependencies"] = ["TASK-001"]
            machine.add_task(first)
            machine.add_task(second)
            with self.assertRaises(STATE.TransitionError):
                machine.lock_tasks()

    def test_dependent_task_cannot_start_before_dependency_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            first = task("TASK-001")
            second = task("TASK-002")
            second["dependencies"] = ["TASK-001"]
            machine.add_task(first)
            machine.add_task(second)
            machine.lock_tasks()
            machine.record_evidence(assignment("TASK-002"))

            with self.assertRaises(STATE.TransitionError):
                machine.record_evidence(
                    {
                        "event_type": "work_started",
                        "task_id": "TASK-002",
                        "agent_id": "author-TASK-002",
                        "summary": "Started too early.",
                    }
                )

            complete_task(machine, "TASK-001")
            machine.record_evidence(
                {
                    "event_type": "work_started",
                    "task_id": "TASK-002",
                    "agent_id": "author-TASK-002",
                    "summary": "Dependency is complete.",
                }
            )
            complete_task(machine, "TASK-002")

    def test_one_hop_superseded_dependency_allows_start_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            dependency = task("TASK-001")
            replacement = task("TASK-002")
            dependent = task("TASK-003")
            dependent["dependencies"] = ["TASK-001"]
            for definition in (dependency, replacement, dependent):
                machine.add_task(definition)
            machine.lock_tasks()
            complete_task(machine, "TASK-002")
            machine.supersede_task("TASK-001", ["TASK-002"])
            validate_superseded(machine, "TASK-001", ["TASK-002"])

            machine.record_evidence(
                {
                    "event_type": "work_started",
                    "task_id": "TASK-003",
                    "agent_id": "author-TASK-003",
                    "summary": "Delivered replacement closure is ready.",
                }
            )
            complete_task(machine, "TASK-003")

            self.assertIn("TASK-003", machine.status()["completed_task_ids"])

    def test_recursive_superseded_dependency_allows_start_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            dependent = task("TASK-004")
            dependent["dependencies"] = ["TASK-001"]
            for task_id in ("TASK-001", "TASK-002", "TASK-003"):
                machine.add_task(task(task_id))
            machine.add_task(dependent)
            machine.lock_tasks()
            complete_task(machine, "TASK-003")
            machine.supersede_task("TASK-002", ["TASK-003"])
            validate_superseded(machine, "TASK-002", ["TASK-003"])
            machine.supersede_task("TASK-001", ["TASK-002"])
            validate_superseded(machine, "TASK-001", ["TASK-002"])

            machine.record_evidence(
                {
                    "event_type": "work_started",
                    "task_id": "TASK-004",
                    "agent_id": "author-TASK-004",
                    "summary": "Recursive replacement closure is ready.",
                }
            )
            complete_task(machine, "TASK-004")

            self.assertIn("TASK-004", machine.status()["completed_task_ids"])

    def test_fan_out_superseded_dependency_requires_every_completed_branch(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            dependent = task("TASK-004")
            dependent["dependencies"] = ["TASK-001"]
            for task_id in ("TASK-001", "TASK-002", "TASK-003"):
                machine.add_task(task(task_id))
            machine.add_task(dependent)
            machine.lock_tasks()
            complete_task(machine, "TASK-002")
            complete_task(machine, "TASK-003")
            machine.supersede_task("TASK-001", ["TASK-002", "TASK-003"])
            validate_superseded(machine, "TASK-001", ["TASK-002", "TASK-003"])

            machine.record_evidence(
                {
                    "event_type": "work_started",
                    "task_id": "TASK-004",
                    "agent_id": "author-TASK-004",
                    "summary": "Every replacement branch is delivered.",
                }
            )

        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            dependent = task("TASK-004")
            dependent["dependencies"] = ["TASK-001"]
            for task_id in ("TASK-001", "TASK-002", "TASK-003"):
                machine.add_task(task(task_id))
            machine.add_task(dependent)
            machine.lock_tasks()
            complete_task(machine, "TASK-002")
            machine.supersede_task("TASK-001", ["TASK-002", "TASK-003"])
            validate_superseded(machine, "TASK-001", ["TASK-002", "TASK-003"])

            self.assert_dependency_closure_rejected(machine, "TASK-004", "TASK-001")

    def test_unvalidated_superseded_root_rejects_start_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            dependent = task("TASK-003")
            dependent["dependencies"] = ["TASK-001"]
            for definition in (task("TASK-001"), task("TASK-002"), dependent):
                machine.add_task(definition)
            machine.lock_tasks()
            complete_task(machine, "TASK-002")
            machine.supersede_task("TASK-001", ["TASK-002"])

            self.assert_dependency_closure_rejected(machine, "TASK-003", "TASK-001")

    def test_unvalidated_nested_replacement_rejects_start_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            dependent = task("TASK-004")
            dependent["dependencies"] = ["TASK-001"]
            for task_id in ("TASK-001", "TASK-002", "TASK-003"):
                machine.add_task(task(task_id))
            machine.add_task(dependent)
            machine.lock_tasks()
            complete_task(machine, "TASK-003")
            machine.supersede_task("TASK-002", ["TASK-003"])
            machine.supersede_task("TASK-001", ["TASK-002"])
            validate_superseded(machine, "TASK-001", ["TASK-002"])

            self.assert_dependency_closure_rejected(machine, "TASK-004", "TASK-001")

    def test_blocked_replacement_rejects_start_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            dependent = task("TASK-003")
            dependent["dependencies"] = ["TASK-001"]
            for definition in (task("TASK-001"), task("TASK-002"), dependent):
                machine.add_task(definition)
            machine.lock_tasks()
            complete_task(machine, "TASK-002")
            machine.record_evidence(
                {
                    "event_type": "task_blocked",
                    "task_id": "TASK-002",
                    "agent_id": "author-TASK-002",
                    "blocker": "A later blocker invalidates the endpoint validation.",
                }
            )
            machine.supersede_task("TASK-001", ["TASK-002"])
            validate_superseded(machine, "TASK-001", ["TASK-002"])

            self.assert_dependency_closure_rejected(machine, "TASK-003", "TASK-001")

    def test_user_cancelled_replacement_is_not_a_delivered_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            dependent = task("TASK-003")
            dependent["dependencies"] = ["TASK-001"]
            for definition in (task("TASK-001"), task("TASK-002"), dependent):
                machine.add_task(definition)
            machine.lock_tasks()
            cancel_and_validate(machine, "TASK-002")
            machine.supersede_task("TASK-001", ["TASK-002"])
            validate_superseded(machine, "TASK-001", ["TASK-002"])

            self.assert_dependency_closure_rejected(machine, "TASK-003", "TASK-001")
            self.assertFalse(machine.status()["can_complete"])

    def test_missing_replacement_and_cycle_are_undelivered_in_persisted_graph(self):
        missing_snapshots = {
            "TASK-001": {
                "status": "superseded",
                "replacement_task_ids": ["TASK-MISSING"],
            }
        }
        missing_validations = {
            "TASK-001": {
                "disposition": "superseded",
                "replacement_task_ids": ["TASK-MISSING"],
            }
        }
        cyclic_snapshots = {
            "TASK-001": {"status": "superseded", "replacement_task_ids": ["TASK-002"]},
            "TASK-002": {"status": "superseded", "replacement_task_ids": ["TASK-001"]},
        }
        cyclic_validations = {
            "TASK-001": {"disposition": "superseded", "replacement_task_ids": ["TASK-002"]},
            "TASK-002": {"disposition": "superseded", "replacement_task_ids": ["TASK-001"]},
        }

        self.assertFalse(
            STATE.StateMachine._delivered_task_closure(
                "TASK-001", missing_snapshots, missing_validations
            )
        )
        self.assertFalse(
            STATE.StateMachine._delivered_task_closure(
                "TASK-001", cyclic_snapshots, cyclic_validations
            )
        )

    def test_superseded_validation_must_agree_with_ledger_replacements(self):
        snapshots = {
            "TASK-001": {"status": "superseded", "replacement_task_ids": ["TASK-002"]},
            "TASK-002": {"status": "active"},
            "TASK-003": {"status": "active"},
        }
        validations = {
            "TASK-001": {"disposition": "superseded", "replacement_task_ids": ["TASK-003"]},
            "TASK-002": {"disposition": "completed"},
            "TASK-003": {"disposition": "completed"},
        }

        delivered = STATE.StateMachine._delivered_task_closure(
            "TASK-001", snapshots, validations
        )

        self.assertFalse(delivered)

    def test_superseded_historical_dependency_accepts_retirement_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            historical = task("TASK-003")
            historical["dependencies"] = ["TASK-001"]
            for definition in (
                task("TASK-001"),
                task("TASK-002"),
                historical,
                task("TASK-004"),
            ):
                machine.add_task(definition)
            machine.lock_tasks()
            complete_task(machine, "TASK-002")
            complete_task(machine, "TASK-004")
            machine.supersede_task("TASK-001", ["TASK-002"])
            validate_superseded(machine, "TASK-001", ["TASK-002"])
            machine.supersede_task("TASK-003", ["TASK-004"])

            validate_superseded(machine, "TASK-003", ["TASK-004"])

            self.assertEqual(2, machine.status()["validated_superseded"])

    def assert_dependency_closure_rejected(
        self, machine, dependent_task_id: str, unresolved_dependency_id: str
    ) -> None:
        machine.record_evidence(assignment(dependent_task_id))
        for event in (
            {
                "event_type": "work_started",
                "task_id": dependent_task_id,
                "agent_id": f"author-{dependent_task_id}",
                "summary": "Attempted start with unresolved dependency closure.",
            },
            {
                "event_type": "lead_validated",
                "task_id": dependent_task_id,
                "lead_agent_id": f"lead-{dependent_task_id}",
                "disposition": "completed",
                "summary": "Attempted validation with unresolved dependency closure.",
            },
        ):
            with self.assertRaisesRegex(
                STATE.TransitionError,
                rf"delivered.*{unresolved_dependency_id}",
            ):
                machine.record_evidence(event)

    def test_supersession_cycle_and_retired_only_closure_cannot_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp), auto=True)
            machine.add_task(task("TASK-001"))
            machine.add_task(task("TASK-002"))
            machine.lock_tasks()
            with self.assertRaises(STATE.TransitionError):
                machine.supersede_task("TASK-001", ["TASK-UNKNOWN"])
            with self.assertRaises(STATE.TransitionError):
                machine.supersede_task("TASK-001", ["TASK-001"])
            machine.supersede_task("TASK-001", ["TASK-002"])
            with self.assertRaises(STATE.TransitionError):
                machine.supersede_task("TASK-002", ["TASK-001"])

        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp), auto=True)
            machine.add_task(task("TASK-001"))
            machine.add_task(task("TASK-002"))
            machine.lock_tasks()
            machine.supersede_task("TASK-001", ["TASK-002"])
            cancel_receipt = authority(
                machine, "AUTH-CANCEL-2", "cancel_task", "TASK-002"
            )
            machine.cancel_task("TASK-002", cancel_receipt)
            machine.record_evidence(
                {
                    "event_type": "lead_validated",
                    "task_id": "TASK-001",
                    "lead_agent_id": "lead-retirement",
                    "disposition": "superseded",
                    "replacement_task_ids": ["TASK-002"],
                    "summary": "Replacement link checked.",
                }
            )
            machine.record_evidence(
                {
                    "event_type": "lead_validated",
                    "task_id": "TASK-002",
                    "lead_agent_id": "lead-retirement",
                    "disposition": "user_cancelled",
                    "authority_receipt_id": cancel_receipt,
                    "summary": "Cancellation checked.",
                }
            )

            self.assertFalse(machine.status()["can_complete"])

    def test_authority_receipts_are_action_and_target_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.add_task(task("TASK-002"))
            machine.lock_tasks()

            with self.assertRaises(STATE.TransitionError):
                machine.cancel_task("TASK-001", "invented-string")

            receipt_id = authority(
                machine, "AUTH-CANCEL-1", "cancel_task", "TASK-001"
            )
            with self.assertRaises(STATE.TransitionError):
                machine.cancel_task("TASK-002", receipt_id)
            machine.cancel_task("TASK-001", receipt_id)

    def test_post_lock_auto_task_requires_recorded_scope_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp), auto=True)
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()

            with self.assertRaises(STATE.TransitionError):
                machine.add_task(task("TASK-002"))

            decision_id = auto_decision(machine, "TASK-002")
            with self.assertRaises(STATE.TransitionError):
                machine.add_task(task("TASK-999"), decision_id=decision_id)
            with self.assertRaises(STATE.TransitionError):
                machine.revise_task(
                    "TASK-001",
                    {"acceptance_criteria": ["Clarified evidence boundary."]},
                    decision_id=decision_id,
                )
            machine.add_task(task("TASK-002"), decision_id=decision_id)
            self.assertEqual(2, machine.status()["total_task_ids"])

            with self.assertRaises(STATE.TransitionError):
                machine.revise_task(
                    "TASK-001",
                    {"objective": "A materially different outcome."},
                    decision_id=auto_decision(machine, "TASK-001"),
                )

    def test_bundled_json_templates_parse_and_task_template_initializes(self):
        templates = Path(__file__).resolve().parents[2] / "assets" / "templates"
        for name in (
            "tandem-assigned.template.json",
            "evidence-submitted.template.json",
            "review-clean.template.json",
            "authority.template.json",
            "decision-input.template.json",
        ):
            with self.subTest(template=name):
                self.assertIsInstance(json.loads((templates / name).read_text(encoding="utf-8")), dict)
        with tempfile.TemporaryDirectory() as temp:
            run_dir = STATE.initialize_run(
                Path(temp),
                "template-input",
                tasks_file=templates / "tasks.template.jsonl",
            )
            machine = STATE.StateMachine(run_dir)
            self.assertEqual(1, machine.status()["total_task_ids"])

    def test_operational_templates_are_accepted_by_run_local_commands(self):
        templates = Path(__file__).resolve().parents[2] / "assets" / "templates"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = STATE.initialize_run(
                root,
                "template-commands",
                tasks_file=templates / "tasks.template.jsonl",
            )
            local_machine = run_dir / "state_machine.py"

            def invoke(*arguments: str) -> dict:
                result = subprocess.run(
                    [sys.executable, str(local_machine), *arguments],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return json.loads(result.stdout)

            invoke("task-lock")
            invoke(
                "authority-register",
                "--authority-file",
                str(templates / "authority.template.json"),
            )
            invoke(
                "decision-add",
                "--decision-file",
                str(templates / "decision-input.template.json"),
            )
            invoke(
                "evidence-add",
                "--event-file",
                str(templates / "tandem-assigned.template.json"),
            )
            artifact = run_dir / "artifacts" / "TASK-001" / "focused-test-result.txt"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("focused test passed", encoding="utf-8")
            submission = invoke(
                "evidence-add",
                "--event-file",
                str(templates / "evidence-submitted.template.json"),
            )
            review = json.loads(
                (templates / "review-clean.template.json").read_text(encoding="utf-8")
            )
            review["artifact_fingerprint"] = submission["artifact_fingerprint"]
            review_path = root / "review-clean.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            invoke("evidence-add", "--event-file", str(review_path))

            self.assertTrue(invoke("verify")["valid"])

    def test_complete_report_is_terminal_and_names_complete_state(self):
        with tempfile.TemporaryDirectory() as temp:
            _, machine = self.make_run(Path(temp))
            machine.add_task(task("TASK-001"))
            machine.lock_tasks()
            complete_task(machine, "TASK-001")

            report = machine.generate_report(final=True)

            self.assertIn("state COMPLETE", report.read_text(encoding="utf-8"))
            self.assertEqual("COMPLETE", machine.status()["run_state"])
            with self.assertRaises(STATE.TransitionError):
                machine.record_evidence(
                    {
                        "event_type": "status_observation",
                        "task_id": "TASK-001",
                        "agent_id": "lead-TASK-001",
                        "summary": "Attempted mutation after completion.",
                    }
                )


if __name__ == "__main__":
    unittest.main()
