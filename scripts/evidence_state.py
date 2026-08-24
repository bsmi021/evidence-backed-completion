from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 1
ZERO_HASH = "0" * 64
HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
CORE_MANAGED_FILES = (
    "state_machine.py",
    "run.json",
    "state.json",
    "tasks.jsonl",
    "evidence.jsonl",
    "authority.jsonl",
    "DECISIONS.md",
    "RUNLOG.md",
)
OPTIONAL_MANAGED_FILES = ("report.html",)
VALID_DISPOSITIONS = {"completed", "superseded", "user_cancelled"}
REASONING_RANK = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "xhigh": 4,
    "max": 5,
}
VALID_EVIDENCE_EVENTS = {
    "tandem_assigned",
    "work_started",
    "evidence_submitted",
    "preflight_failure",
    "review_finding",
    "review_finding_packet",
    "finding_resolved",
    "correction_cycle",
    "review_clean",
    "task_blocked",
    "task_unblocked",
    "lead_validated",
    "status_observation",
}


class StateMachineError(RuntimeError):
    pass


class IntegrityViolation(StateMachineError):
    pass


class TransitionError(StateMachineError):
    pass


class CapabilityError(TransitionError):
    pass


class TaskLimitExceeded(TransitionError):
    pass


class IncompleteRunError(TransitionError):
    pass


class LockTimeout(StateMachineError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64] or "run"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class FileLock:
    def __init__(self, path: Path, timeout_seconds: float = 10.0):
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"\0")
            self.handle.flush()
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise LockTimeout(f"Timed out acquiring {self.path}")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, traceback):
        if self.handle is None:
            return
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


def load_jsonl(path: Path, verify_chain: bool = True) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    previous = ZERO_HASH
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise IntegrityViolation(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
        if verify_chain:
            expected_sequence = len(records) + 1
            if record.get("sequence") != expected_sequence:
                raise IntegrityViolation(f"Invalid sequence at {path}:{line_number}")
            if record.get("previous_event_hash") != previous:
                raise IntegrityViolation(f"Broken hash chain at {path}:{line_number}")
            supplied_hash = record.get("event_hash")
            payload = dict(record)
            payload.pop("event_hash", None)
            expected_hash = sha256_bytes(canonical_json(payload).encode("utf-8"))
            if supplied_hash != expected_hash:
                raise IntegrityViolation(f"Invalid event hash at {path}:{line_number}")
            previous = supplied_hash
        records.append(record)
    return records


def append_jsonl_event(path: Path, event: dict[str, Any]) -> dict[str, Any]:
    records = load_jsonl(path)
    payload = dict(event)
    for protected in ("sequence", "previous_event_hash", "event_hash"):
        payload.pop(protected, None)
    payload.setdefault("occurred_at", utc_now())
    payload["sequence"] = len(records) + 1
    payload["previous_event_hash"] = records[-1]["event_hash"] if records else ZERO_HASH
    payload["event_hash"] = sha256_bytes(canonical_json(payload).encode("utf-8"))
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(payload) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return payload


def validate_task_definition(task: dict[str, Any]) -> None:
    required = (
        "task_id",
        "title",
        "objective",
        "task_type",
        "acceptance_criteria",
        "evidence_requirements",
        "dependencies",
    )
    missing = [field for field in required if field not in task]
    if missing:
        raise TransitionError(f"Task is missing required fields: {', '.join(missing)}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", str(task["task_id"])):
        raise TransitionError("task_id must be a stable 2-128 character identifier")
    if not isinstance(task["acceptance_criteria"], list) or not task["acceptance_criteria"]:
        raise TransitionError("acceptance_criteria must be a non-empty list")
    if not isinstance(task["evidence_requirements"], list) or not task["evidence_requirements"]:
        raise TransitionError("evidence_requirements must be a non-empty list")
    requirement_ids: set[str] = set()
    for requirement in task["evidence_requirements"]:
        requirement_id = requirement.get("requirement_id")
        if not requirement_id or requirement_id in requirement_ids:
            raise TransitionError("Evidence requirement IDs must be present and unique within a task")
        requirement_ids.add(requirement_id)
        if not requirement.get("description") or not requirement.get("kind"):
            raise TransitionError("Evidence requirements need description and kind")


def initialize_run(
    repo_root: str | Path,
    run_name: str,
    *,
    auto: bool = False,
    create_task_limit: int = 20,
    review_cycle_limit: int = 5,
    run_id: str | None = None,
    now: str | None = None,
    tasks_file: str | Path | None = None,
) -> Path:
    if create_task_limit < 0:
        raise ValueError("create_task_limit cannot be negative")
    if review_cycle_limit < 1:
        raise ValueError("review_cycle_limit must be at least 1")
    root = Path(repo_root).resolve()
    runs_root = root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    created_at = now or utc_now()
    date_prefix = created_at[:10]
    slug = slugify(run_name)
    supplied_id = run_id is not None
    while True:
        candidate_id = (run_id or uuid.uuid4().hex[:8]).lower()[:8]
        run_dir = runs_root / f"{date_prefix}-{slug}-{candidate_id}"
        if not run_dir.exists():
            break
        if supplied_id:
            raise FileExistsError(run_dir)
        run_id = None
    run_dir.mkdir(parents=True)
    for directory in ("locks", "inbox", "artifacts", "receipts"):
        (run_dir / directory).mkdir()
    (run_dir / "locks" / "state.lock").write_bytes(b"\0")
    shutil.copy2(Path(__file__).resolve(), run_dir / "state_machine.py")
    atomic_write_json(
        run_dir / "run.json",
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": candidate_id,
            "run_name": run_name,
            "created_at": created_at,
            "repo_root": str(root),
            "auto": bool(auto),
            "create_task_limit": int(create_task_limit),
            "review_cycle_limit": int(review_cycle_limit),
            "status_interval_seconds": 300,
        },
    )
    atomic_write_json(
        run_dir / "state.json",
        {
            "schema_version": SCHEMA_VERSION,
            "run_state": "AWAITING_TASKS",
            "tasks_locked": False,
            "initial_task_count": 0,
            "auto_created_tasks": 0,
            "decision_ids": [],
            "decision_records": {},
            "task_contract_hash": None,
            "needs_user_decision": None,
            "last_transition": "run_initialized",
            "updated_at": created_at,
        },
    )
    atomic_write_text(run_dir / "tasks.jsonl", "")
    atomic_write_text(run_dir / "evidence.jsonl", "")
    atomic_write_text(run_dir / "authority.jsonl", "")
    atomic_write_text(
        run_dir / "DECISIONS.md",
        f"# Decisions — {run_name}\n\nRun ID: `{candidate_id}`\n\n",
    )
    atomic_write_text(
        run_dir / "RUNLOG.md",
        f"# Run Log — {run_name}\n\n"
        f"- Run ID: `{candidate_id}`\n"
        f"- Created: {created_at}\n\n"
        "## Objective\n\n- Awaiting a recorded objective.\n\n"
        "## Activity\n\n"
        f"- {created_at} Run initialized.\n\n"
        "## Outcomes\n\n- None recorded.\n\n"
        "## Blockers\n\n- None recorded.\n",
    )
    machine = StateMachine(run_dir, allow_missing_integrity=True)
    machine._refresh_integrity_unlocked()
    if tasks_file is not None:
        source = Path(tasks_file)
        for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                machine.add_task(json.loads(line))
            except Exception as exc:
                raise TransitionError(f"Unable to import {source}:{line_number}: {exc}") from exc
    return run_dir


class StateMachine:
    def __init__(self, run_dir: str | Path, allow_missing_integrity: bool = False):
        self.run_dir = Path(run_dir).resolve()
        self.tasks_path = self.run_dir / "tasks.jsonl"
        self.evidence_path = self.run_dir / "evidence.jsonl"
        self.authority_path = self.run_dir / "authority.jsonl"
        self.integrity_path = self.run_dir / "integrity.json"
        self.lock_path = self.run_dir / "locks" / "state.lock"
        if not self.run_dir.is_dir():
            raise FileNotFoundError(self.run_dir)
        if not allow_missing_integrity and not self.integrity_path.exists():
            raise IntegrityViolation(f"Missing integrity manifest: {self.integrity_path}")

    def _managed_paths(self) -> dict[str, Path]:
        paths = {name: self.run_dir / name for name in CORE_MANAGED_FILES}
        for name in OPTIONAL_MANAGED_FILES:
            path = self.run_dir / name
            if path.exists():
                paths[name] = path
        return paths

    def _integrity_report_unlocked(self) -> dict[str, Any]:
        if not self.integrity_path.exists():
            return {"valid": False, "mismatches": {"integrity.json": "missing"}}
        manifest = read_json(self.integrity_path)
        expected: dict[str, str] = manifest.get("files", {})
        current_paths = self._managed_paths()
        mismatches: dict[str, Any] = {}
        for name, expected_hash in expected.items():
            path = self.run_dir / name
            if not path.exists():
                mismatches[name] = {"expected": expected_hash, "actual": "missing"}
                continue
            actual_hash = sha256_file(path)
            if actual_hash != expected_hash:
                mismatches[name] = {"expected": expected_hash, "actual": actual_hash}
        for name in sorted(set(current_paths) - set(expected)):
            mismatches[name] = {"expected": "absent", "actual": sha256_file(current_paths[name])}
        ledger_tails = manifest.get("ledger_tails", {})
        for name, path in (
            ("tasks.jsonl", self.tasks_path),
            ("evidence.jsonl", self.evidence_path),
            ("authority.jsonl", self.authority_path),
        ):
            try:
                records = load_jsonl(path)
                actual_tail = records[-1]["event_hash"] if records else ZERO_HASH
                if ledger_tails.get(name) != actual_tail:
                    mismatches[f"{name}#chain"] = {
                        "expected": ledger_tails.get(name),
                        "actual": actual_tail,
                    }
            except IntegrityViolation as exc:
                mismatches[f"{name}#chain"] = str(exc)
        return {"valid": not mismatches, "mismatches": mismatches}

    def verify_integrity(self) -> dict[str, Any]:
        with FileLock(self.lock_path):
            return self._integrity_report_unlocked()

    def _assert_integrity_unlocked(self) -> None:
        report = self._integrity_report_unlocked()
        if not report["valid"]:
            raise IntegrityViolation(json.dumps(report["mismatches"], sort_keys=True))

    def _refresh_integrity_unlocked(self) -> None:
        paths = self._managed_paths()
        task_records = load_jsonl(self.tasks_path)
        evidence_records = load_jsonl(self.evidence_path)
        authority_records = load_jsonl(self.authority_path)
        atomic_write_json(
            self.integrity_path,
            {
                "schema_version": SCHEMA_VERSION,
                "algorithm": "sha256",
                "purpose": "accidental_or_manual_modification_detection",
                "updated_at": utc_now(),
                "files": {name: sha256_file(path) for name, path in sorted(paths.items())},
                "ledger_tails": {
                    "tasks.jsonl": task_records[-1]["event_hash"] if task_records else ZERO_HASH,
                    "evidence.jsonl": evidence_records[-1]["event_hash"] if evidence_records else ZERO_HASH,
                    "authority.jsonl": authority_records[-1]["event_hash"] if authority_records else ZERO_HASH,
                },
            },
        )

    @contextlib.contextmanager
    def _mutation(self, *, allow_complete: bool = False) -> Iterator[None]:
        with FileLock(self.lock_path):
            self._assert_integrity_unlocked()
            if not allow_complete and self._state()["run_state"] == "COMPLETE":
                raise TransitionError("A COMPLETE run is immutable")
            yield
            self._refresh_integrity_unlocked()

    def _run_config(self) -> dict[str, Any]:
        return read_json(self.run_dir / "run.json")

    def _state(self) -> dict[str, Any]:
        return read_json(self.run_dir / "state.json")

    def _write_state(self, state: dict[str, Any], transition: str) -> None:
        state["last_transition"] = transition
        state["updated_at"] = utc_now()
        atomic_write_json(self.run_dir / "state.json", state)

    def _task_events(self) -> list[dict[str, Any]]:
        return load_jsonl(self.tasks_path)

    def _evidence_events(self) -> list[dict[str, Any]]:
        return load_jsonl(self.evidence_path)

    def _authority_events(self) -> list[dict[str, Any]]:
        return load_jsonl(self.authority_path)

    def register_authority(self, receipt: dict[str, Any]) -> dict[str, Any]:
        required = ("receipt_id", "action", "target", "exact_user_instruction", "lead_agent_id")
        missing = [field for field in required if not receipt.get(field)]
        if missing:
            raise TransitionError(f"Authority receipt is missing: {', '.join(missing)}")
        with self._mutation():
            if any(
                event.get("receipt_id") == receipt["receipt_id"]
                for event in self._authority_events()
            ):
                raise TransitionError(f"Authority receipt already exists: {receipt['receipt_id']}")
            payload = dict(receipt)
            payload["event_type"] = "authority_registered"
            payload["instruction_hash"] = "sha256:" + sha256_bytes(
                receipt["exact_user_instruction"].encode("utf-8")
            )
            return append_jsonl_event(self.authority_path, payload)

    def _require_authority(
        self,
        receipt_id: str | None,
        *,
        action: str,
        target: str,
        value: Any = None,
    ) -> dict[str, Any]:
        if not receipt_id:
            raise TransitionError(f"{action} requires a registered authority receipt")
        matches = [
            event
            for event in self._authority_events()
            if event.get("receipt_id") == receipt_id
        ]
        if not matches:
            raise TransitionError(f"Unknown authority receipt: {receipt_id}")
        receipt = matches[-1]
        if receipt.get("action") != action or receipt.get("target") != target:
            raise TransitionError("Authority receipt action or target does not match")
        if value is not None and receipt.get("value") != value:
            raise TransitionError("Authority receipt value does not match")
        return receipt

    def _task_snapshots(self) -> dict[str, dict[str, Any]]:
        snapshots: dict[str, dict[str, Any]] = {}
        for event in self._task_events():
            task_id = event["task_id"]
            if event["event_type"] == "task_created":
                snapshots[task_id] = {
                    "task_id": task_id,
                    "revision": event["revision"],
                    "status": "active",
                    "definition": event["definition"],
                    "created_after_lock": event.get("created_after_lock", False),
                }
            elif event["event_type"] == "task_revised":
                snapshot = snapshots[task_id]
                snapshot["revision"] = event["revision"]
                snapshot["definition"].update(event["changes"])
                snapshot["status"] = "active"
            elif event["event_type"] == "task_superseded":
                snapshot = snapshots[task_id]
                snapshot["revision"] = event["revision"]
                snapshot["status"] = "superseded"
                snapshot["replacement_task_ids"] = event["replacement_task_ids"]
            elif event["event_type"] == "task_cancelled":
                snapshot = snapshots[task_id]
                snapshot["revision"] = event["revision"]
                snapshot["status"] = "user_cancelled"
                snapshot["authority_receipt_id"] = event["authority_receipt_id"]
        return snapshots

    @staticmethod
    def _delivered_task_closure(
        task_id: str,
        snapshots: dict[str, dict[str, Any]],
        validations: dict[str, dict[str, Any]],
        visiting: set[str] | None = None,
    ) -> bool:
        active_path = set() if visiting is None else visiting
        if task_id in active_path:
            return False
        snapshot = snapshots.get(task_id)
        if snapshot is None:
            return False
        validation = validations.get(task_id, {})
        if snapshot.get("status") == "active":
            return validation.get("disposition") == "completed"
        if snapshot.get("status") != "superseded":
            return False
        if validation.get("disposition") != "superseded":
            return False
        ledger_replacements = snapshot.get("replacement_task_ids")
        validated_replacements = validation.get("replacement_task_ids")
        if (
            not isinstance(ledger_replacements, list)
            or not ledger_replacements
            or not isinstance(validated_replacements, list)
            or set(validated_replacements) != set(ledger_replacements)
        ):
            return False
        return all(
            StateMachine._delivered_task_closure(
                replacement,
                snapshots,
                validations,
                active_path | {task_id},
            )
            for replacement in ledger_replacements
        )

    @staticmethod
    def _validate_dependency_graph(snapshots: dict[str, dict[str, Any]]) -> None:
        graph: dict[str, list[str]] = {}
        for task_id, snapshot in snapshots.items():
            dependencies = snapshot["definition"].get("dependencies")
            if not isinstance(dependencies, list) or any(
                not isinstance(item, str) for item in dependencies
            ):
                raise TransitionError(f"Dependencies must be task-ID strings for {task_id}")
            if task_id in dependencies:
                raise TransitionError(f"Task cannot depend on itself: {task_id}")
            missing = [item for item in dependencies if item not in snapshots]
            if missing:
                raise TransitionError(
                    f"Unknown dependencies for {task_id}: {', '.join(sorted(missing))}"
                )
            graph[task_id] = dependencies
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise TransitionError("Task dependency graph contains a cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)

    def add_task(
        self,
        task: dict[str, Any],
        *,
        decision_id: str | None = None,
        authority_receipt_id: str | None = None,
    ) -> dict[str, Any]:
        validate_task_definition(task)
        pending_error: TaskLimitExceeded | None = None
        result: dict[str, Any] | None = None
        with self._mutation():
            snapshots = self._task_snapshots()
            task_id = task["task_id"]
            if task_id in snapshots:
                raise TransitionError(f"Task already exists: {task_id}")
            state = self._state()
            config = self._run_config()
            after_lock = bool(state["tasks_locked"])
            candidate_snapshots = dict(snapshots)
            candidate_snapshots[task_id] = {
                "task_id": task_id,
                "revision": 1,
                "status": "active",
                "definition": dict(task),
                "created_after_lock": after_lock,
            }
            if after_lock:
                self._validate_dependency_graph(candidate_snapshots)
            if after_lock:
                if not decision_id or decision_id not in state.get("decision_ids", []):
                    raise TransitionError("Post-lock task creation requires a recorded scope decision")
                decision_record = state.get("decision_records", {}).get(decision_id, {})
                if (
                    decision_record.get("scope_classification") != "within_locked_scope"
                    or decision_record.get("task_contract_hash") != state.get("task_contract_hash")
                ):
                    raise TransitionError("Scope decision is not bound to the locked task contract")
                if task_id not in decision_record.get("task_ids", []):
                    raise TransitionError("Scope decision does not name the task being created")
                if not config["auto"]:
                    self._require_authority(
                        authority_receipt_id,
                        action="create_task",
                        target=task_id,
                    )
                else:
                    if state["auto_created_tasks"] >= config["create_task_limit"]:
                        state["run_state"] = "NEEDS_USER_DECISION"
                        state["needs_user_decision"] = {
                            "reason": "create_task_limit",
                            "task_id": task_id,
                            "observed_value": state["auto_created_tasks"],
                            "required_action": "configure_limit",
                            "target": "create_task_limit",
                            "minimum_value": state["auto_created_tasks"] + 1,
                        }
                        self._write_state(state, "create_task_limit_reached")
                        pending_error = TaskLimitExceeded(
                            f"Task limit {config['create_task_limit']} reached before adding {task_id}"
                        )
                    else:
                        state["auto_created_tasks"] += 1
            if pending_error is None:
                result = append_jsonl_event(
                    self.tasks_path,
                    {
                        "event_type": "task_created",
                        "task_id": task_id,
                        "revision": 1,
                        "created_after_lock": after_lock,
                        "decision_id": decision_id,
                        "definition": task,
                    },
                )
                if state["run_state"] in {"AWAITING_TASKS", "TASKS_DRAFT"}:
                    state["run_state"] = "TASKS_DRAFT"
                elif after_lock:
                    state["run_state"] = "EXECUTING"
                self._write_state(state, "task_created")
        if pending_error is not None:
            raise pending_error
        assert result is not None
        return result

    def revise_task(
        self,
        task_id: str,
        changes: dict[str, Any],
        evidence_invalidated: list[str] | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        if "task_id" in changes:
            raise TransitionError("A revision cannot change task_id")
        with self._mutation():
            snapshot = self._task_snapshots().get(task_id)
            if snapshot is None:
                raise TransitionError(f"Unknown task: {task_id}")
            if snapshot["status"] != "active":
                raise TransitionError("Only an active task may be revised")
            state = self._state()
            if state["tasks_locked"]:
                if not decision_id or decision_id not in state.get("decision_ids", []):
                    raise TransitionError("Post-lock revision requires a recorded scope decision")
                decision_record = state.get("decision_records", {}).get(decision_id, {})
                if (
                    decision_record.get("scope_classification") != "within_locked_scope"
                    or decision_record.get("task_contract_hash") != state.get("task_contract_hash")
                ):
                    raise TransitionError("Scope decision is not bound to the locked task contract")
                if task_id not in decision_record.get("task_ids", []):
                    raise TransitionError("Scope decision does not name the task being revised")
                prohibited = sorted(set(changes) & {"objective", "task_type", "dependencies"})
                if prohibited:
                    raise TransitionError(
                        "Material outcome/dependency changes require a new task ID: "
                        + ", ".join(prohibited)
                    )
            revised = dict(snapshot["definition"])
            revised.update(changes)
            validate_task_definition(revised)
            candidate_snapshots = self._task_snapshots()
            candidate_snapshots[task_id] = dict(candidate_snapshots[task_id])
            candidate_snapshots[task_id]["definition"] = revised
            if state["tasks_locked"]:
                self._validate_dependency_graph(candidate_snapshots)
            event = append_jsonl_event(
                self.tasks_path,
                {
                    "event_type": "task_revised",
                    "task_id": task_id,
                    "revision": snapshot["revision"] + 1,
                    "changes": changes,
                    "evidence_invalidated": evidence_invalidated or [],
                    "decision_id": decision_id,
                },
            )
            state["run_state"] = "EXECUTING" if state["tasks_locked"] else "TASKS_DRAFT"
            self._write_state(state, "task_revised")
            return event

    def lock_tasks(self) -> None:
        with self._mutation():
            snapshots = self._task_snapshots()
            if not snapshots:
                raise TransitionError("Cannot lock an empty task ledger")
            self._validate_dependency_graph(snapshots)
            state = self._state()
            if state["tasks_locked"]:
                return
            state["tasks_locked"] = True
            state["initial_task_count"] = len(snapshots)
            state["task_contract_hash"] = "sha256:" + sha256_bytes(
                canonical_json(
                    [snapshots[task_id]["definition"] for task_id in sorted(snapshots)]
                ).encode("utf-8")
            )
            state["run_state"] = "TASKS_LOCKED"
            self._write_state(state, "tasks_locked")

    def supersede_task(self, task_id: str, replacement_task_ids: list[str]) -> dict[str, Any]:
        with self._mutation():
            snapshots = self._task_snapshots()
            snapshot = snapshots.get(task_id)
            if snapshot is None:
                raise TransitionError(f"Unknown task: {task_id}")
            if snapshot["status"] != "active":
                raise TransitionError("Only an active task may be superseded")
            if not replacement_task_ids or task_id in replacement_task_ids:
                raise TransitionError("Supersession requires one or more different replacement tasks")
            missing = [item for item in replacement_task_ids if item not in snapshots]
            if missing:
                raise TransitionError(f"Unknown replacement tasks: {', '.join(missing)}")
            replacement_graph = {
                item: list(value.get("replacement_task_ids", []))
                for item, value in snapshots.items()
                if value["status"] == "superseded"
            }
            replacement_graph[task_id] = list(replacement_task_ids)

            def visit(current: str, visiting: set[str], visited: set[str]) -> None:
                if current in visiting:
                    raise TransitionError("Task supersession graph contains a cycle")
                if current in visited:
                    return
                for replacement in replacement_graph.get(current, []):
                    visit(replacement, visiting | {current}, visited)
                visited.add(current)

            visited: set[str] = set()
            for current in replacement_graph:
                visit(current, set(), visited)
            event = append_jsonl_event(
                self.tasks_path,
                {
                    "event_type": "task_superseded",
                    "task_id": task_id,
                    "revision": snapshot["revision"] + 1,
                    "replacement_task_ids": replacement_task_ids,
                },
            )
            state = self._state()
            state["run_state"] = "EXECUTING"
            self._write_state(state, "task_superseded")
            return event

    def cancel_task(self, task_id: str, authority_receipt_id: str) -> dict[str, Any]:
        with self._mutation():
            snapshot = self._task_snapshots().get(task_id)
            if snapshot is None:
                raise TransitionError(f"Unknown task: {task_id}")
            if snapshot["status"] != "active":
                raise TransitionError("Only an active task may be user-cancelled")
            self._require_authority(
                authority_receipt_id,
                action="cancel_task",
                target=task_id,
            )
            event = append_jsonl_event(
                self.tasks_path,
                {
                    "event_type": "task_cancelled",
                    "task_id": task_id,
                    "revision": snapshot["revision"] + 1,
                    "authority_receipt_id": authority_receipt_id,
                },
            )
            state = self._state()
            state["run_state"] = "EXECUTING"
            self._write_state(state, "task_cancelled")
            return event

    def _append_decision_unlocked(self, decision: dict[str, Any]) -> str:
        decision_id = decision.get("decision_id") or f"DEC-{uuid.uuid4().hex[:8].upper()}"
        path = self.run_dir / "DECISIONS.md"
        current = path.read_text(encoding="utf-8")
        task_ids = ", ".join(decision.get("task_ids", [])) or "None"
        section = (
            f"## {decision_id}: {decision['title']}\n\n"
            f"- Timestamp: {decision.get('timestamp', utc_now())}\n"
            f"- Related task IDs: {task_ids}\n"
            f"- State-machine stage: {decision.get('stage', 'unspecified')}\n"
            f"- Decision: {decision['decision']}\n"
            f"- Evidence and rationale: {decision['rationale']}\n"
            f"- Authority basis: {decision['authority_basis']}\n"
            f"- Assumptions: {decision.get('assumptions', 'None')}\n"
            f"- Scope impact: {decision.get('scope_impact', 'None')}\n"
            f"- Reversibility: {decision.get('reversibility', 'unspecified')}\n"
            f"- Confidence: {decision.get('confidence', 'unspecified')}\n\n"
        )
        atomic_write_text(path, current + section)
        return decision_id

    def add_decision(self, decision: dict[str, Any]) -> str:
        required = ("title", "decision", "rationale", "authority_basis")
        missing = [field for field in required if not decision.get(field)]
        if missing:
            raise TransitionError(f"Decision is missing required fields: {', '.join(missing)}")
        with self._mutation():
            decision_id = self._append_decision_unlocked(decision)
            state = self._state()
            state.setdefault("decision_ids", []).append(decision_id)
            state.setdefault("decision_records", {})[decision_id] = {
                "task_ids": decision.get("task_ids", []),
                "scope_classification": decision.get("scope_classification"),
                "task_contract_hash": state.get("task_contract_hash"),
            }
            self._write_state(state, "decision_recorded")
            return decision_id

    def update_runlog(self, kind: str, message: str) -> None:
        headings = {
            "objective": "Objective update",
            "activity": "Activity update",
            "outcome": "Outcome update",
            "blocker": "Blocker update",
        }
        if kind not in headings or not message.strip():
            raise TransitionError("Run-log entry requires objective, activity, outcome, or blocker")
        with self._mutation():
            path = self.run_dir / "RUNLOG.md"
            current = path.read_text(encoding="utf-8")
            entry = f"\n## {headings[kind]}\n\n- {utc_now()} {message.strip()}\n"
            atomic_write_text(path, current.rstrip() + "\n" + entry)

    def configure_limits(
        self,
        *,
        create_task_limit: int | None = None,
        review_cycle_limit: int | None = None,
        authority_receipt_id: str,
    ) -> None:
        if create_task_limit is None and review_cycle_limit is None:
            raise TransitionError("At least one limit must be provided")
        if create_task_limit is not None and review_cycle_limit is not None:
            raise TransitionError("Change one limit per authority receipt")
        with self._mutation():
            config = self._run_config()
            state = self._state()
            changes: list[str] = []
            if create_task_limit is not None:
                self._require_authority(
                    authority_receipt_id,
                    action="configure_limit",
                    target="create_task_limit",
                    value=create_task_limit,
                )
                if create_task_limit < state["auto_created_tasks"]:
                    raise TransitionError("Task limit cannot be lower than tasks already auto-created")
                config["create_task_limit"] = int(create_task_limit)
                changes.append(f"create task limit = {create_task_limit}")
            if review_cycle_limit is not None:
                self._require_authority(
                    authority_receipt_id,
                    action="configure_limit",
                    target="review_cycle_limit",
                    value=review_cycle_limit,
                )
                if review_cycle_limit < 1:
                    raise TransitionError("Review cycle limit must be at least 1")
                config["review_cycle_limit"] = int(review_cycle_limit)
                changes.append(f"review cycle limit = {review_cycle_limit}")
            atomic_write_json(self.run_dir / "run.json", config)
            self._append_decision_unlocked(
                {
                    "title": "Override run limits",
                    "decision": "; ".join(changes),
                    "rationale": "The user explicitly overrode the configured autonomous limit.",
                    "authority_basis": authority_receipt_id,
                    "stage": state["run_state"],
                    "scope_impact": "Changes the bounded autonomous execution limit only.",
                    "reversibility": "reversible",
                    "confidence": "high",
                }
            )
            if state["run_state"] == "NEEDS_USER_DECISION":
                pending = state.get("needs_user_decision") or {}
                changed_target = (
                    "create_task_limit" if create_task_limit is not None else "review_cycle_limit"
                )
                changed_value = (
                    create_task_limit if create_task_limit is not None else review_cycle_limit
                )
                if (
                    pending.get("required_action") == "configure_limit"
                    and pending.get("target") == changed_target
                    and changed_value is not None
                    and changed_value >= pending.get("minimum_value", changed_value)
                ):
                    state["run_state"] = (
                        "EXECUTING" if state["tasks_locked"] else "TASKS_DRAFT"
                    )
                    state["needs_user_decision"] = None
            self._write_state(state, "limits_configured")

    def _current_revision_events(
        self, task_id: str, evidence: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        snapshot = self._task_snapshots()[task_id]
        return [
            event
            for event in (evidence or self._evidence_events())
            if event.get("task_id") == task_id
            and event.get("task_revision") == snapshot["revision"]
        ]

    @staticmethod
    def _open_material_findings(events: list[dict[str, Any]]) -> set[str]:
        open_findings: set[str] = set()
        for event in events:
            if event["event_type"] == "review_finding" and event.get("material"):
                open_findings.add(event["finding_id"])
            elif event["event_type"] == "review_finding_packet" and event.get("material"):
                open_findings.add(event["packet_id"])
            elif event["event_type"] == "finding_resolved":
                open_findings.discard(event["finding_id"])
            elif event["event_type"] == "correction_cycle":
                open_findings.discard(event.get("finding_packet_id"))
        return open_findings

    @staticmethod
    def _blocked(events: list[dict[str, Any]]) -> bool:
        blocked = False
        for event in events:
            if event["event_type"] == "task_blocked":
                blocked = True
            elif event["event_type"] == "task_unblocked":
                blocked = False
        return blocked

    def _compute_artifact_manifest(
        self, task_id: str, artifact_paths: list[str]
    ) -> tuple[list[dict[str, Any]], str]:
        if not isinstance(artifact_paths, list) or not artifact_paths:
            raise TransitionError("Evidence submission requires one or more artifact paths")
        allowed_root = (self.run_dir / "artifacts" / task_id).resolve()
        manifest: list[dict[str, Any]] = []
        for raw_path in sorted(set(artifact_paths)):
            candidate = (self.run_dir / raw_path).resolve()
            if allowed_root != candidate.parent and allowed_root not in candidate.parents:
                raise TransitionError("Evidence artifacts must stay under artifacts/<task-id>")
            if not candidate.is_file():
                raise TransitionError(f"Evidence artifact is missing: {raw_path}")
            manifest.append(
                {
                    "path": candidate.relative_to(self.run_dir).as_posix(),
                    "sha256": sha256_file(candidate),
                    "size": candidate.stat().st_size,
                }
            )
        fingerprint = "sha256:" + sha256_bytes(canonical_json(manifest).encode("utf-8"))
        return manifest, fingerprint

    def _verify_artifact_submission(self, submission: dict[str, Any]) -> None:
        manifest, fingerprint = self._compute_artifact_manifest(
            submission["task_id"],
            [item["path"] for item in submission.get("artifact_manifest", [])],
        )
        if manifest != submission.get("artifact_manifest") or fingerprint != submission.get(
            "artifact_fingerprint"
        ):
            raise TransitionError("Submitted artifact manifest is stale or changed")

    def _validate_capability_comparison(
        self,
        source: dict[str, Any],
        reviewer: dict[str, Any],
        comparison_basis: str | None,
        requirement_met: bool,
        authority_receipt_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        if not requirement_met:
            raise CapabilityError("Reviewer capability parity is not established")
        if comparison_basis == "same_model_same_or_higher_effort":
            if not source.get("model") or source.get("model") != reviewer.get("model"):
                raise CapabilityError("Same-model comparison requires identical declared models")
            source_rank = REASONING_RANK.get(str(source.get("reasoning_effort", "")).lower())
            reviewer_rank = REASONING_RANK.get(
                str(reviewer.get("reasoning_effort", "")).lower()
            )
            if source_rank is None or reviewer_rank is None or reviewer_rank < source_rank:
                raise CapabilityError("Reviewer reasoning effort is lower or incomparable")
        elif comparison_basis == "comparable_runtime_score":
            source_score = source.get("capability_score")
            reviewer_score = reviewer.get("capability_score")
            if not isinstance(source_score, (int, float)) or not isinstance(
                reviewer_score, (int, float)
            ):
                raise CapabilityError("Runtime-score comparison requires numeric scores")
            if reviewer_score < source_score:
                raise CapabilityError("Reviewer runtime capability score is lower")
            authority = source.get("capability_score_authority")
            metadata = (
                authority,
                source.get("capability_score_scale"),
                source.get("capability_score_version"),
            )
            reviewer_metadata = (
                reviewer.get("capability_score_authority"),
                reviewer.get("capability_score_scale"),
                reviewer.get("capability_score_version"),
            )
            if not all(metadata) or metadata != reviewer_metadata:
                raise CapabilityError("Runtime scores lack shared authority, scale, or version")
            try:
                self._require_authority(
                    authority_receipt_id,
                    action="capability_score_authority",
                    target=str(authority),
                )
            except TransitionError as exc:
                raise CapabilityError(str(exc)) from exc
        elif comparison_basis == "user_provided_ranking":
            try:
                self._require_authority(
                    authority_receipt_id,
                    action="model_ranking",
                    target=str(task_id),
                )
            except TransitionError as exc:
                raise CapabilityError(str(exc)) from exc
        else:
            raise CapabilityError("Capability comparison basis is unsupported or missing")

    def _validate_evidence_event(
        self,
        event: dict[str, Any],
        snapshot: dict[str, Any],
        current_events: list[dict[str, Any]],
    ) -> None:
        event_type = event["event_type"]
        if event_type == "tandem_assigned":
            author = event.get("author", {})
            reviewer = event.get("reviewer", {})
            if not author.get("agent_id") or not reviewer.get("agent_id"):
                raise CapabilityError("Tandem assignments require author and reviewer agent IDs")
            if author["agent_id"] == reviewer["agent_id"]:
                raise CapabilityError("Author and reviewer must be different agents")
            self._validate_capability_comparison(
                author,
                reviewer,
                event.get("comparison_basis"),
                bool(event.get("capability_requirement_met")),
                event.get("authority_receipt_id"),
                snapshot["task_id"],
            )
        elif event_type == "evidence_submitted":
            known_requirements = {
                requirement["requirement_id"]
                for requirement in snapshot["definition"]["evidence_requirements"]
            }
            supplied = set(event.get("requirements_satisfied", []))
            if not supplied or not supplied <= known_requirements:
                raise TransitionError("Evidence submission contains missing or unknown requirement IDs")
        elif event_type == "preflight_failure":
            if event.get("category") not in {
                "compile-import",
                "disposable-build",
                "capture-setup",
                "pre-submission-audit",
            }:
                raise TransitionError("Preflight failure category is unsupported")
            signature = event.get("failure_signature")
            if not isinstance(signature, str) or not signature.strip():
                raise TransitionError("Preflight failure requires a stable failure_signature")
            if not event.get("agent_id") or not event.get("summary"):
                raise TransitionError("Preflight failure requires agent_id and diagnostic summary")
        elif event_type == "review_finding":
            if not event.get("finding_id") or not isinstance(event.get("material"), bool):
                raise TransitionError("Review findings require finding_id and material boolean")
        elif event_type == "review_finding_packet":
            packet_id = event.get("packet_id")
            if not isinstance(packet_id, str) or not packet_id.strip():
                raise TransitionError("Consolidated finding packet requires packet_id")
            if any(
                item.get("packet_id") == packet_id
                for item in current_events
                if item["event_type"] == "review_finding_packet"
            ):
                raise TransitionError("Consolidated finding packet ID already exists")
            if event.get("material") is not True:
                raise TransitionError("Correction finding packet must be material")
            fingerprint = str(event.get("artifact_fingerprint", ""))
            if not HASH_PATTERN.fullmatch(fingerprint):
                raise TransitionError("Finding packet requires a sha256 artifact fingerprint")
            assignments = [
                item for item in current_events if item["event_type"] == "tandem_assigned"
            ]
            if not assignments:
                raise TransitionError("Finding packet requires a valid tandem assignment")
            reviewer_id = assignments[-1]["reviewer"]["agent_id"]
            if event.get("reviewer_agent_id") != reviewer_id:
                raise TransitionError("Only the assigned reviewer may submit a finding packet")
            submissions = [
                item for item in current_events if item["event_type"] == "evidence_submitted"
            ]
            if submissions:
                self._verify_artifact_submission(submissions[-1])
            matching = [
                item for item in submissions if item.get("artifact_fingerprint") == fingerprint
            ]
            if not matching:
                raise TransitionError("Finding packet must bind a submitted candidate")
            if submissions[-1].get("artifact_fingerprint") != fingerprint:
                raise TransitionError("Finding packet cannot bind a stale submitted candidate")
            if any(
                item.get("artifact_fingerprint") == fingerprint
                for item in current_events
                if item["event_type"] == "review_finding_packet"
            ):
                raise TransitionError("Each candidate accepts one consolidated finding packet")
            findings = event.get("findings")
            if not isinstance(findings, list) or not findings:
                raise TransitionError("Consolidated finding packet requires material findings")
            finding_ids: set[str] = set()
            for finding in findings:
                if not isinstance(finding, dict):
                    raise TransitionError("Finding packet entries must be objects")
                finding_id = finding.get("finding_id")
                if not isinstance(finding_id, str) or not finding_id.strip() or finding_id in finding_ids:
                    raise TransitionError("Finding packet finding_id values must be unique strings")
                finding_ids.add(finding_id)
                for name in ("severity", "root_cause_family", "summary"):
                    if not isinstance(finding.get(name), str) or not finding[name].strip():
                        raise TransitionError(f"Finding packet entry requires {name}")
        elif event_type == "finding_resolved":
            known = {
                item["finding_id"]
                for item in current_events
                if item["event_type"] == "review_finding"
            }
            if event.get("finding_id") not in known:
                raise TransitionError("Cannot resolve an unknown finding")
        elif event_type == "correction_cycle":
            cycle = int(event.get("cycle", 0))
            if cycle < 1:
                raise TransitionError("Correction cycle must be positive")
            if cycle > self._run_config()["review_cycle_limit"]:
                raise TransitionError("Correction cycle limit exceeded; user decision required")
            assignments = [
                item for item in current_events if item["event_type"] == "tandem_assigned"
            ]
            if not assignments:
                raise TransitionError("Correction cycle requires a valid tandem assignment")
            if event.get("agent_id") != assignments[-1]["author"]["agent_id"]:
                raise TransitionError("Only the assigned author may record a correction cycle")
            fingerprint = str(event.get("candidate_fingerprint", ""))
            if not HASH_PATTERN.fullmatch(fingerprint):
                raise TransitionError("Correction cycle requires a submitted candidate fingerprint")
            submissions = [
                item for item in current_events if item["event_type"] == "evidence_submitted"
            ]
            if submissions:
                self._verify_artifact_submission(submissions[-1])
            if not submissions or submissions[-1].get("artifact_fingerprint") != fingerprint:
                raise TransitionError("Correction cycle must bind the latest submitted candidate")
            packet_id = event.get("finding_packet_id")
            packets = [
                item
                for item in current_events
                if item["event_type"] == "review_finding_packet"
                and item.get("packet_id") == packet_id
            ]
            if not packets:
                raise TransitionError("Correction cycle requires a consolidated finding packet")
            packet = packets[-1]
            if packet.get("artifact_fingerprint") != fingerprint:
                raise TransitionError("Correction cycle finding packet targets a different candidate")
            if packet.get("reviewer_agent_id") != assignments[-1]["reviewer"]["agent_id"]:
                raise TransitionError("Correction cycle finding packet reviewer does not match assignment")
            if any(
                item.get("finding_packet_id") == packet_id
                or item.get("candidate_fingerprint") == fingerprint
                for item in current_events
                if item["event_type"] == "correction_cycle"
            ):
                raise TransitionError("Candidate or finding packet was already consumed by a correction cycle")
        elif event_type == "review_clean":
            if event.get("statement") != "no_material_improvement_found":
                raise TransitionError("Clean review requires the convergence statement")
            if not HASH_PATTERN.fullmatch(str(event.get("artifact_fingerprint", ""))):
                raise TransitionError("Clean review requires a sha256 artifact fingerprint")
            assignments = [item for item in current_events if item["event_type"] == "tandem_assigned"]
            if not assignments:
                raise TransitionError("Clean review requires a valid tandem assignment")
            reviewer_id = assignments[-1]["reviewer"]["agent_id"]
            if event.get("reviewer_agent_id") != reviewer_id:
                raise TransitionError("Only the assigned reviewer may record the clean review")
            if self._open_material_findings(current_events):
                raise TransitionError("Material findings remain unresolved")
            required = {
                requirement["requirement_id"]
                for requirement in snapshot["definition"]["evidence_requirements"]
            }
            submissions = [
                item for item in current_events if item["event_type"] == "evidence_submitted"
            ]
            if not submissions:
                raise TransitionError("Clean review requires a submitted artifact manifest")
            latest_submission = submissions[-1]
            corrections = [
                item for item in current_events if item["event_type"] == "correction_cycle"
            ]
            if corrections and latest_submission["sequence"] < corrections[-1]["sequence"]:
                raise TransitionError(
                    "Clean review requires a newly submitted corrected candidate"
                )
            self._verify_artifact_submission(latest_submission)
            satisfied = set(latest_submission.get("requirements_satisfied", []))
            if not required <= satisfied:
                raise TransitionError("One reviewed submission must satisfy every requirement")
            if event["artifact_fingerprint"] != latest_submission["artifact_fingerprint"]:
                raise TransitionError("Clean review fingerprint does not match latest submission")
        elif event_type == "lead_validated":
            disposition = event.get("disposition")
            if disposition not in VALID_DISPOSITIONS:
                raise TransitionError("Invalid lead-validated disposition")
            if disposition == "completed":
                if snapshot["status"] != "active":
                    raise TransitionError("Only an active task may be validated completed")
                clean_reviews = [item for item in current_events if item["event_type"] == "review_clean"]
                if not clean_reviews:
                    raise TransitionError("Completed disposition requires a clean adversarial review")
                clean = clean_reviews[-1]
                if event.get("artifact_fingerprint") != clean["artifact_fingerprint"]:
                    raise TransitionError("Artifact changed after the clean adversarial review")
                submissions = [
                    item for item in current_events if item["event_type"] == "evidence_submitted"
                ]
                latest_submission = submissions[-1]
                self._verify_artifact_submission(latest_submission)
                if clean["artifact_fingerprint"] != latest_submission["artifact_fingerprint"]:
                    raise TransitionError("Clean review no longer matches latest submitted evidence")
                assignments = [item for item in current_events if item["event_type"] == "tandem_assigned"]
                assignment = assignments[-1]
                lead_id = event.get("lead_agent_id")
                lead = event.get("lead", {})
                if lead.get("agent_id") != lead_id:
                    raise CapabilityError("Lead validator metadata must match lead_agent_id")
                if lead_id in {
                    assignment["author"]["agent_id"],
                    assignment["reviewer"]["agent_id"],
                }:
                    raise TransitionError("Lead validation must be independent of the tandem")
                self._validate_capability_comparison(
                    assignment["author"],
                    lead,
                    event.get("comparison_basis"),
                    bool(event.get("capability_requirement_met")),
                    event.get("authority_receipt_id"),
                    snapshot["task_id"],
                )
                if self._blocked(current_events) or self._open_material_findings(current_events):
                    raise TransitionError("Blocked work or material findings prevent validation")
            elif disposition == "superseded":
                if snapshot["status"] != "superseded":
                    raise TransitionError("Task is not superseded")
                if set(event.get("replacement_task_ids", [])) != set(
                    snapshot.get("replacement_task_ids", [])
                ):
                    raise TransitionError("Replacement task IDs do not match the task ledger")
            elif disposition == "user_cancelled":
                if snapshot["status"] != "user_cancelled":
                    raise TransitionError("Task is not user-cancelled")
                if event.get("authority_receipt_id") != snapshot.get("authority_receipt_id"):
                    raise TransitionError("Cancellation authority does not match the task ledger")

    def record_evidence(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = event.get("event_type")
        if event_type not in VALID_EVIDENCE_EVENTS:
            raise TransitionError(f"Unsupported evidence event: {event_type}")
        task_id = event.get("task_id")
        result: dict[str, Any] | None = None
        pending_error: TransitionError | None = None
        with self._mutation():
            snapshots = self._task_snapshots()
            snapshot = snapshots.get(task_id)
            if snapshot is None:
                raise TransitionError(f"Unknown task: {task_id}")
            state = self._state()
            if not state["tasks_locked"]:
                raise TransitionError("Execution evidence is not accepted before TASKS_LOCKED")
            if snapshot["status"] != "active" and event_type != "lead_validated":
                raise TransitionError("Retired tasks accept only their terminal lead disposition")
            existing = self._evidence_events()
            current_events = self._current_revision_events(task_id, existing)
            payload = dict(event)
            payload["task_revision"] = snapshot["revision"]
            if event_type == "evidence_submitted":
                manifest, computed_fingerprint = self._compute_artifact_manifest(
                    task_id, payload.get("artifact_paths", [])
                )
                supplied_fingerprint = payload.get("artifact_fingerprint")
                if supplied_fingerprint and supplied_fingerprint != computed_fingerprint:
                    raise TransitionError("Caller-supplied artifact fingerprint does not match files")
                payload["artifact_manifest"] = manifest
                payload["artifact_fingerprint"] = computed_fingerprint
                payload.pop("artifact_paths", None)
            if event_type == "preflight_failure" and "diagnostic_artifact_paths" in payload:
                manifest, computed_fingerprint = self._compute_artifact_manifest(
                    task_id, payload.get("diagnostic_artifact_paths", [])
                )
                supplied_fingerprint = payload.get("diagnostic_artifact_fingerprint")
                if supplied_fingerprint and supplied_fingerprint != computed_fingerprint:
                    raise TransitionError(
                        "Caller-supplied diagnostic artifact fingerprint does not match files"
                    )
                payload["diagnostic_artifact_manifest"] = manifest
                payload["diagnostic_artifact_fingerprint"] = computed_fingerprint
                payload.pop("diagnostic_artifact_paths", None)
            elif event_type == "preflight_failure" and payload.get(
                "diagnostic_artifact_fingerprint"
            ):
                raise TransitionError(
                    "Preflight diagnostic fingerprints are computed only from artifact paths"
                )
            if event_type in {
                "work_started",
                "evidence_submitted",
                "preflight_failure",
                "review_clean",
                "lead_validated",
            }:
                dependencies = set(snapshot["definition"].get("dependencies", []))
                validations, _, _ = self._current_validation_state(snapshots, existing)
                unresolved = sorted(
                    dependency
                    for dependency in dependencies
                    if not self._delivered_task_closure(
                        dependency,
                        snapshots,
                        validations,
                    )
                )
                if unresolved:
                    raise TransitionError(
                        "Task dependencies do not have a delivered lead-validated closure: "
                        + ", ".join(unresolved)
                    )
            if event_type == "correction_cycle":
                prior_cycles = [
                    int(item["cycle"])
                    for item in current_events
                    if item["event_type"] == "correction_cycle"
                ]
                expected_cycle = len(prior_cycles) + 1
                cycle = int(payload.get("cycle", 0))
                if cycle != expected_cycle:
                    raise TransitionError(
                        f"Correction cycles must be monotonic; expected {expected_cycle}"
                    )
                self._validate_evidence_event(payload, snapshot, current_events)
                result = append_jsonl_event(self.evidence_path, payload)
                if cycle >= self._run_config()["review_cycle_limit"]:
                    state["run_state"] = "NEEDS_USER_DECISION"
                    state["needs_user_decision"] = {
                        "reason": "review_cycle_limit",
                        "task_id": task_id,
                        "observed_value": cycle,
                        "required_action": "configure_limit",
                        "target": "review_cycle_limit",
                        "minimum_value": cycle + 1,
                    }
                    self._write_state(state, "review_cycle_limit_reached")
                    pending_error = TransitionError(
                        "Correction cycle limit reached; user decision required"
                    )
                else:
                    state["run_state"] = "EXECUTING"
                    self._write_state(state, "correction_cycle")
            else:
                self._validate_evidence_event(payload, snapshot, current_events)
                result = append_jsonl_event(self.evidence_path, payload)
                if event_type == "lead_validated":
                    projected = existing + [result]
                    projected_status = self._status_unlocked(projected)
                    state["run_state"] = (
                        "FINAL_VALIDATION" if projected_status["can_complete"] else "EXECUTING"
                    )
                elif event_type in {
                    "tandem_assigned",
                    "work_started",
                    "evidence_submitted",
                    "preflight_failure",
                    "review_finding",
                    "review_finding_packet",
                    "finding_resolved",
                }:
                    state["run_state"] = "EXECUTING"
                self._write_state(state, event_type)
        if pending_error is not None:
            raise pending_error
        assert result is not None
        return result

    def _current_validation_state(
        self,
        snapshots: dict[str, dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
        validations: dict[str, dict[str, Any]] = {}
        blocked_ids: list[str] = []
        stale_artifact_ids: list[str] = []
        for task_id, snapshot in snapshots.items():
            current_events = [
                item
                for item in evidence
                if item.get("task_id") == task_id
                and item.get("task_revision") == snapshot["revision"]
            ]
            if self._blocked(current_events):
                blocked_ids.append(task_id)
            terminal = [item for item in current_events if item["event_type"] == "lead_validated"]
            if terminal:
                candidate = terminal[-1]
                later_affecting = [
                    item
                    for item in current_events
                    if item["sequence"] > candidate["sequence"]
                    and item["event_type"] != "status_observation"
                ]
                if later_affecting:
                    continue
                if candidate.get("disposition") == "completed":
                    submissions = [
                        item
                        for item in current_events
                        if item["event_type"] == "evidence_submitted"
                        and item["sequence"] < candidate["sequence"]
                    ]
                    try:
                        self._verify_artifact_submission(submissions[-1])
                    except (TransitionError, IndexError):
                        stale_artifact_ids.append(task_id)
                        continue
                validations[task_id] = candidate
        return validations, blocked_ids, stale_artifact_ids

    def _status_unlocked(
        self, evidence_events: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        state = self._state()
        config = self._run_config()
        snapshots = self._task_snapshots()
        evidence = evidence_events if evidence_events is not None else self._evidence_events()
        validations, blocked_ids, stale_artifact_ids = self._current_validation_state(
            snapshots,
            evidence,
        )
        active_ids = [item for item, snapshot in snapshots.items() if snapshot["status"] == "active"]
        completed_ids = [
            item
            for item in active_ids
            if validations.get(item, {}).get("disposition") == "completed"
        ]
        superseded_ids = [
            item
            for item, validation in validations.items()
            if validation.get("disposition") == "superseded"
        ]
        cancelled_ids = [
            item
            for item, validation in validations.items()
            if validation.get("disposition") == "user_cancelled"
        ]
        valid_replacements = all(
            self._delivered_task_closure(item, snapshots, validations)
            for item, snapshot in snapshots.items()
            if snapshot["status"] == "superseded"
        )
        remaining_active = sorted(set(active_ids) - set(completed_ids))
        can_complete = bool(snapshots) and all(
            (
                len(validations) == len(snapshots),
                not remaining_active,
                not blocked_ids,
                not stale_artifact_ids,
                valid_replacements,
                state["tasks_locked"],
                state["run_state"] != "NEEDS_USER_DECISION",
            )
        )
        return {
            "run_id": config["run_id"],
            "run_name": config["run_name"],
            "run_state": state["run_state"],
            "needs_user_decision": state.get("needs_user_decision"),
            "auto": config["auto"],
            "total_task_ids": len(snapshots),
            "active_required_tasks": len(active_ids),
            "lead_validated_task_ids": len(validations),
            "validated_completed": len(completed_ids),
            "completed_task_ids": sorted(completed_ids),
            "validated_superseded": len(superseded_ids),
            "validated_user_cancelled": len(cancelled_ids),
            "blocked_tasks": len(blocked_ids),
            "blocked_task_ids": sorted(blocked_ids),
            "stale_artifact_task_ids": sorted(stale_artifact_ids),
            "remaining_active_tasks": len(remaining_active),
            "remaining_active_task_ids": remaining_active,
            "evidence_event_count": len(evidence),
            "initial_task_count": state["initial_task_count"],
            "auto_created_tasks": state["auto_created_tasks"],
            "create_task_limit": config["create_task_limit"],
            "review_cycle_limit": config["review_cycle_limit"],
            "status_interval_seconds": config["status_interval_seconds"],
            "can_complete": can_complete,
        }

    def status(self) -> dict[str, Any]:
        with FileLock(self.lock_path):
            self._assert_integrity_unlocked()
            return self._status_unlocked()

    def generate_report(self, *, final: bool = False) -> Path:
        with self._mutation(allow_complete=True):
            existing_state = self._state()
            existing_report = self.run_dir / "report.html"
            if final and existing_state["run_state"] == "COMPLETE" and existing_report.exists():
                return existing_report
            status = self._status_unlocked()
            if final and not status["can_complete"]:
                raise IncompleteRunError("Final report requires every task to have a valid disposition")
            if final:
                state = self._state()
                state["run_state"] = "COMPLETE"
                self._write_state(state, "final_report_generated")
                status = self._status_unlocked()
            tasks = self._task_snapshots()
            decisions = (self.run_dir / "DECISIONS.md").read_text(encoding="utf-8")
            rows = []
            evidence = self._evidence_events()
            for task_id, snapshot in sorted(tasks.items()):
                current = [
                    item
                    for item in evidence
                    if item.get("task_id") == task_id
                    and item.get("task_revision") == snapshot["revision"]
                ]
                terminal = [item for item in current if item["event_type"] == "lead_validated"]
                disposition = terminal[-1]["disposition"] if terminal else "unvalidated"
                rows.append(
                    "<tr>"
                    f"<td>{html.escape(task_id)}</td>"
                    f"<td>{html.escape(snapshot['definition']['title'])}</td>"
                    f"<td>{html.escape(snapshot['status'])}</td>"
                    f"<td>{html.escape(disposition)}</td>"
                    f"<td>{snapshot['revision']}</td>"
                    "</tr>"
                )
            report = f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Evidence-Backed Completion Report</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;color:#18212b}}h1{{margin-bottom:4px}}.meta{{color:#536171}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:24px 0}}.card{{border:1px solid #d9e0e7;border-radius:10px;padding:16px;background:#f8fafc}}.value{{font-size:28px;font-weight:700}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;border-bottom:1px solid #d9e0e7;padding:10px}}pre{{white-space:pre-wrap;background:#f3f5f7;padding:16px;border-radius:8px}}</style></head>
<body><h1>Evidence-Backed Completion Report</h1><p class=\"meta\">Run {html.escape(status['run_id'])} · {html.escape(status['run_name'])} · state {html.escape(status['run_state'])}</p>
<div class=\"cards\"><div class=\"card\"><div class=\"value\">{status['total_task_ids']}</div>Total tasks</div><div class=\"card\"><div class=\"value\">{status['lead_validated_task_ids']}</div>Validated dispositions</div><div class=\"card\"><div class=\"value\">{status['remaining_active_tasks']}</div>Remaining active</div><div class=\"card\"><div class=\"value\">{status['blocked_tasks']}</div>Blocked</div></div>
<h2>Tasks</h2><table><thead><tr><th>ID</th><th>Title</th><th>Ledger status</th><th>Validated disposition</th><th>Revision</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>Decisions</h2><pre>{html.escape(decisions)}</pre></body></html>"""
            path = self.run_dir / "report.html"
            atomic_write_text(path, report)
            return path


def load_object(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def default_run_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    script = Path(__file__).resolve()
    if script.name == "state_machine.py":
        return script.parent
    raise SystemExit("--run-dir is required when invoking the skill bootstrap copy")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evidence-backed completion state machine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--repo-root", required=True)
    init.add_argument("--run-name", required=True)
    init.add_argument("--tasks-file")
    init.add_argument("--auto", action="store_true")
    init.add_argument("--createTaskLimit", type=int, default=20)
    init.add_argument("--reviewCycleLimit", type=int, default=5)
    for name in ("task-lock", "status", "verify", "report"):
        command = subparsers.add_parser(name)
        command.add_argument("--run-dir")
        if name == "report":
            command.add_argument("--final", action="store_true")
    task_add = subparsers.add_parser("task-add")
    task_add.add_argument("--run-dir")
    task_add.add_argument("--task-file", required=True)
    task_add.add_argument("--decision-id")
    task_add.add_argument("--authority-receipt-id")
    revise = subparsers.add_parser("task-revise")
    revise.add_argument("--run-dir")
    revise.add_argument("--task-id", required=True)
    revise.add_argument("--changes-file", required=True)
    revise.add_argument("--evidence-invalidated", action="append", default=[])
    revise.add_argument("--decision-id")
    supersede = subparsers.add_parser("task-supersede")
    supersede.add_argument("--run-dir")
    supersede.add_argument("--task-id", required=True)
    supersede.add_argument("--replacement-task-id", action="append", required=True)
    cancel = subparsers.add_parser("task-cancel")
    cancel.add_argument("--run-dir")
    cancel.add_argument("--task-id", required=True)
    cancel.add_argument("--authority-receipt-id", required=True)
    evidence = subparsers.add_parser("evidence-add")
    evidence.add_argument("--run-dir")
    evidence.add_argument("--event-file", required=True)
    decision = subparsers.add_parser("decision-add")
    decision.add_argument("--run-dir")
    decision.add_argument("--decision-file", required=True)
    authority_command = subparsers.add_parser("authority-register")
    authority_command.add_argument("--run-dir")
    authority_command.add_argument("--authority-file", required=True)
    runlog = subparsers.add_parser("runlog-add")
    runlog.add_argument("--run-dir")
    runlog.add_argument(
        "--kind", choices=("objective", "activity", "outcome", "blocker"), required=True
    )
    runlog.add_argument("--message", required=True)
    configure = subparsers.add_parser("configure")
    configure.add_argument("--run-dir")
    configure.add_argument("--createTaskLimit", type=int)
    configure.add_argument("--reviewCycleLimit", type=int)
    configure.add_argument("--authority-receipt-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            run_dir = initialize_run(
                args.repo_root,
                args.run_name,
                auto=args.auto,
                create_task_limit=args.createTaskLimit,
                review_cycle_limit=args.reviewCycleLimit,
                tasks_file=args.tasks_file,
            )
            print(json.dumps({"run_dir": str(run_dir)}, indent=2))
            return 0
        machine = StateMachine(default_run_dir(args.run_dir))
        if args.command == "task-add":
            result = machine.add_task(
                load_object(args.task_file),
                decision_id=args.decision_id,
                authority_receipt_id=args.authority_receipt_id,
            )
        elif args.command == "task-revise":
            result = machine.revise_task(
                args.task_id,
                load_object(args.changes_file),
                evidence_invalidated=args.evidence_invalidated,
                decision_id=args.decision_id,
            )
        elif args.command == "task-lock":
            machine.lock_tasks()
            result = machine.status()
        elif args.command == "task-supersede":
            result = machine.supersede_task(args.task_id, args.replacement_task_id)
        elif args.command == "task-cancel":
            result = machine.cancel_task(args.task_id, args.authority_receipt_id)
        elif args.command == "evidence-add":
            result = machine.record_evidence(load_object(args.event_file))
        elif args.command == "decision-add":
            result = {"decision_id": machine.add_decision(load_object(args.decision_file))}
        elif args.command == "authority-register":
            result = machine.register_authority(load_object(args.authority_file))
        elif args.command == "runlog-add":
            machine.update_runlog(args.kind, args.message)
            result = {"runlog": str(machine.run_dir / "RUNLOG.md")}
        elif args.command == "configure":
            machine.configure_limits(
                create_task_limit=args.createTaskLimit,
                review_cycle_limit=args.reviewCycleLimit,
                authority_receipt_id=args.authority_receipt_id,
            )
            result = machine.status()
        elif args.command == "status":
            result = machine.status()
        elif args.command == "verify":
            result = machine.verify_integrity()
        elif args.command == "report":
            result = {"report": str(machine.generate_report(final=args.final))}
        else:
            parser.error(f"Unknown command: {args.command}")
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except IntegrityViolation as exc:
        print(json.dumps({"error": "INTEGRITY_VIOLATION", "detail": str(exc)}), file=sys.stderr)
        return 3
    except TaskLimitExceeded as exc:
        print(json.dumps({"error": "CREATE_TASK_LIMIT_REACHED", "detail": str(exc)}), file=sys.stderr)
        return 4
    except StateMachineError as exc:
        print(json.dumps({"error": exc.__class__.__name__, "detail": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
