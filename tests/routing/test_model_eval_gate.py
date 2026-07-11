"""Canonical Claude model-evaluation and retained-v1 gate tests."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from evals.model_eval_gate import (
    EvaluationContractError,
    assess,
    build_plan,
    verify_release_report,
)


CANDIDATE_COMMIT = "1" * 40
CANDIDATE_TREE = "2" * 40
BASELINE_COMMIT = "86690b668e2cc616e03bdb2f1a28aa951d6c29ad"
BASELINE_TREE = "92680a710c5366ba875aad92b12cb129369ab6b3"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _suite(repo_root: Path) -> list[dict]:
    return json.loads((repo_root / "evals" / "v2-behavior-evals.json").read_text())


def _case_evidence(case: dict, *, passed: bool = True) -> dict:
    response_parts = [f"Required: {behavior}" for behavior in case["required_behaviors"]]
    response = "\n".join(response_parts) + "\nNo account mutation was performed."
    required_results = []
    for index, behavior in enumerate(case["required_behaviors"]):
        observed = passed or index > 0
        result = {
            "behavior": behavior,
            "observed": observed,
            "rationale": "The observable response was graded against this exact rubric item.",
        }
        if observed:
            result["evidence_excerpt"] = f"Required: {behavior}"
        required_results.append(result)
    return {
        "case_id": case["id"],
        "invocation_id": f"invocation-{case['id']}",
        "prompt_sha256": _sha(case["prompt"]),
        "response_sha256": _sha(response),
        "response": response,
        "required_behavior_results": required_results,
        "forbidden_behavior_results": [
            {
                "behavior": behavior,
                "observed": False,
                "rationale": "The independent evaluator did not observe this behavior.",
            }
            for behavior in case["forbidden_behaviors"]
        ],
    }


def _run_evidence(
    repo_root: Path,
    role: str,
    *,
    failed_ids: set[str] | None = None,
    model_snapshot: str = "claude-model-snapshot-2026-07-01",
) -> dict:
    failed_ids = failed_ids or set()
    candidate = role == "candidate"
    return {
        "schema_version": "1.0.0",
        "evidence_class": "external_model_run",
        "contract_id": "claude-ads-v2-canonical-model-gate",
        "run_id": f"canonical-{role}-run",
        "role": role,
        "suite": {
            "path": "evals/v2-behavior-evals.json",
            "sha256": "c535b471fa862a14518e0a86cd39a1081969cb208443af01fbcce1046f36d052",
            "case_count": 24,
        },
        "subject": {
            "product_version": "2.0.0" if candidate else "1.8.1",
            "git_ref": "v2" if candidate else "v1.8.1",
            "git_commit": CANDIDATE_COMMIT if candidate else BASELINE_COMMIT,
            "git_tree": CANDIDATE_TREE if candidate else BASELINE_TREE,
            "artifact_sha256": ("a" if candidate else "b") * 64,
        },
        "runtime": {
            "family": "claude-code",
            "cli_version": "2.1.207",
            "provider": "anthropic",
            "model_id": "claude-model",
            "model_snapshot": model_snapshot,
            "fresh_process_per_case": True,
            "conversation_reuse": False,
            "skill_loaded": True,
        },
        "execution": {
            "started_at": "2026-07-11T10:00:00Z",
            "finished_at": "2026-07-11T11:00:00Z",
            "executor_identity": f"runner-{role}",
            "environment_id": f"isolated-{role}",
            "clean_checkout": True,
            "mutation_authority": "none",
            "receipt": f"private-run-receipt-{role}",
        },
        "evaluator": {
            "kind": "human",
            "identity": f"reviewer-{role}",
            "rubric_id": "claude-ads-observable-behavior-v1",
            "independent_from_subject_run": True,
            "evaluated_at": "2026-07-11T12:00:00Z",
            "receipt": f"private-review-receipt-{role}",
        },
        "cases": [
            _case_evidence(case, passed=case["id"] not in failed_ids)
            for case in _suite(repo_root)
        ],
    }


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _assess(repo_root: Path, tmp_path: Path, candidate: dict, baseline: dict) -> dict:
    return assess(
        repo_root / "evals" / "model-eval-contract.json",
        repo_root,
        _write(tmp_path / "candidate-private.json", candidate),
        _write(tmp_path / "baseline-private.json", baseline),
        CANDIDATE_COMMIT,
        CANDIDATE_TREE,
    )


def test_execution_plan_is_pinned_and_explicitly_not_run_evidence(repo_root):
    plan = build_plan(repo_root / "evals" / "model-eval-contract.json", repo_root)
    assert plan["artifact_class"] == "external_model_execution_plan"
    assert plan["is_model_run_evidence"] is False
    assert len(plan["task_packets"]) == 24
    assert plan["suite"]["sha256"] == _sha(
        (repo_root / "evals" / "v2-behavior-evals.json").read_text(encoding="utf-8")
    )
    for packet, case in zip(plan["task_packets"], _suite(repo_root), strict=True):
        assert packet["case_id"] == case["id"]
        assert packet["prompt_sha256"] == _sha(case["prompt"])
        assert packet["required_behaviors"] == case["required_behaviors"]
        assert packet["forbidden_behaviors"] == case["forbidden_behaviors"]


def test_missing_external_runs_fail_closed_without_local_substitution(repo_root):
    report = assess(
        repo_root / "evals" / "model-eval-contract.json",
        repo_root,
        None,
        None,
        CANDIDATE_COMMIT,
        CANDIDATE_TREE,
    )
    assert report["evidence_class"] == "local_deterministic_assessment"
    assert report["release_gate_satisfied"] is False
    assert report["external_runs"]["candidate"]["status"] == "missing"
    assert report["external_runs"]["retained_v1"]["status"] == "missing"
    assert report["comparison"]["status"] == "not_evaluated"
    assert report["summary"]["candidate_score_percent"] is None
    assert any(check["status"] == "fail" for check in report["local_checks"])


def test_complete_canonical_runs_pass_and_summary_excludes_raw_responses(repo_root, tmp_path):
    report = _assess(
        repo_root,
        tmp_path,
        _run_evidence(repo_root, "candidate"),
        _run_evidence(repo_root, "retained-v1"),
    )
    assert report["release_gate_satisfied"] is True
    assert report["blockers"] == []
    assert report["summary"] == {
        "candidate_score_percent": 100.0,
        "candidate_p0_failures": 0,
        "retained_v1_score_percent": 100.0,
        "regression_count": 0,
        "thresholds": {
            "minimum_candidate_score_percent": 90,
            "maximum_p0_failures": 0,
            "maximum_regressions_against_retained_v1": 0,
        },
    }
    assert report["comparison"] == {
        "status": "pass",
        "regression_count": 0,
        "regression_case_ids": [],
    }
    serialized = json.dumps(report)
    assert "No account mutation was performed" not in serialized
    assert '"response"' not in serialized
    assert "private-run-receipt" not in serialized
    assert "canonical-candidate-run" not in serialized
    assert len(report["external_runs"]["candidate"]["receipt_sha256"]) == 64
    assert len(report["external_runs"]["candidate"]["run_id_sha256"]) == 64


def test_candidate_regression_against_passing_v1_blocks_gate(repo_root, tmp_path):
    report = _assess(
        repo_root,
        tmp_path,
        _run_evidence(repo_root, "candidate", failed_ids={"partial-audit"}),
        _run_evidence(repo_root, "retained-v1"),
    )
    assert report["summary"]["candidate_score_percent"] == 95.83
    assert report["comparison"]["regression_case_ids"] == ["partial-audit"]
    assert report["release_gate_satisfied"] is False
    assert any("regressed" in blocker for blocker in report["blockers"])


def test_p0_failure_blocks_even_when_v1_failed_same_case_and_score_exceeds_threshold(
    repo_root, tmp_path
):
    failed = {"safety-delete"}
    report = _assess(
        repo_root,
        tmp_path,
        _run_evidence(repo_root, "candidate", failed_ids=failed),
        _run_evidence(repo_root, "retained-v1", failed_ids=failed),
    )
    assert report["comparison"]["regression_count"] == 0
    assert report["summary"]["candidate_score_percent"] == 95.83
    assert report["summary"]["candidate_p0_failures"] == 1
    assert report["release_gate_satisfied"] is False
    assert any("P0 failures" in blocker for blocker in report["blockers"])


def test_non_claude_runtime_is_invalid_external_evidence(repo_root, tmp_path):
    candidate = _run_evidence(repo_root, "candidate")
    candidate["runtime"]["family"] = "codex"
    report = _assess(
        repo_root,
        tmp_path,
        candidate,
        _run_evidence(repo_root, "retained-v1"),
    )
    assert report["external_runs"]["candidate"]["status"] == "invalid"
    assert report["release_gate_satisfied"] is False
    assert any("canonical Claude Code" in blocker for blocker in report["blockers"])


def test_stale_candidate_or_unpinned_baseline_is_rejected(repo_root, tmp_path):
    candidate = _run_evidence(repo_root, "candidate")
    candidate["subject"]["git_commit"] = "3" * 40
    baseline = _run_evidence(repo_root, "retained-v1")
    baseline["subject"]["git_tree"] = "4" * 40
    report = _assess(repo_root, tmp_path, candidate, baseline)
    assert report["external_runs"]["candidate"]["status"] == "invalid"
    assert report["external_runs"]["retained_v1"]["status"] == "invalid"
    assert report["release_gate_satisfied"] is False


def test_response_hash_and_evidence_excerpt_are_verified(repo_root, tmp_path):
    candidate = _run_evidence(repo_root, "candidate")
    candidate["cases"][0]["response"] += " tampered"
    report = _assess(
        repo_root,
        tmp_path,
        candidate,
        _run_evidence(repo_root, "retained-v1"),
    )
    assert report["external_runs"]["candidate"]["status"] == "invalid"
    assert "response hash" in report["external_runs"]["candidate"]["error"]

    candidate = _run_evidence(repo_root, "candidate")
    candidate["cases"][0]["required_behavior_results"][0]["evidence_excerpt"] = "not in output"
    report = _assess(
        repo_root,
        tmp_path,
        candidate,
        _run_evidence(repo_root, "retained-v1"),
    )
    assert report["external_runs"]["candidate"]["status"] == "invalid"
    assert "not present" in report["external_runs"]["candidate"]["error"]


def test_self_grading_and_runtime_parity_mismatch_fail_closed(repo_root, tmp_path):
    candidate = _run_evidence(repo_root, "candidate")
    candidate["evaluator"]["identity"] = candidate["execution"]["executor_identity"]
    report = _assess(
        repo_root,
        tmp_path,
        candidate,
        _run_evidence(repo_root, "retained-v1"),
    )
    assert report["external_runs"]["candidate"]["status"] == "invalid"
    assert "own executor" in report["external_runs"]["candidate"]["error"]

    report = _assess(
        repo_root,
        tmp_path,
        _run_evidence(repo_root, "candidate"),
        _run_evidence(repo_root, "retained-v1", model_snapshot="different-snapshot"),
    )
    assert report["comparison"]["status"] == "fail"
    assert report["release_gate_satisfied"] is False
    assert any("model_snapshot" in blocker for blocker in report["blockers"])


def test_cli_writes_deterministic_failing_report_and_nonzero_status(repo_root, tmp_path):
    output = tmp_path / "gate.json"
    command = [
        sys.executable,
        str(repo_root / "evals" / "model_eval_gate.py"),
        "--root",
        str(repo_root),
        "assess",
        "--expected-candidate-commit",
        CANDIDATE_COMMIT,
        "--expected-candidate-tree",
        CANDIDATE_TREE,
        "--output",
        str(output),
    ]
    first = subprocess.run(command, cwd=repo_root, check=False, capture_output=True, text=True)
    first_bytes = output.read_bytes()
    second = subprocess.run(command, cwd=repo_root, check=False, capture_output=True, text=True)
    assert first.returncode == second.returncode == 1
    assert output.read_bytes() == first_bytes
    assert json.loads(first_bytes)["release_gate_satisfied"] is False


def test_stored_release_report_verification_rejects_tampering(repo_root, tmp_path):
    report = _assess(
        repo_root,
        tmp_path,
        _run_evidence(repo_root, "candidate"),
        _run_evidence(repo_root, "retained-v1"),
    )
    report_path = _write(tmp_path / "gate.json", report)
    verified = verify_release_report(
        report_path,
        repo_root / "evals" / "model-eval-contract.json",
        repo_root,
        CANDIDATE_COMMIT,
        CANDIDATE_TREE,
    )
    assert verified["release_gate_satisfied"] is True

    tampered = deepcopy(report)
    tampered["external_runs"]["candidate"]["passed"] = 23
    tampered_path = _write(tmp_path / "tampered.json", tampered)
    try:
        verify_release_report(
            tampered_path,
            repo_root / "evals" / "model-eval-contract.json",
            repo_root,
            CANDIDATE_COMMIT,
            CANDIDATE_TREE,
        )
    except EvaluationContractError as exc:
        assert "counts" in str(exc) or "score" in str(exc)
    else:  # pragma: no cover - explicit fail-closed assertion
        raise AssertionError("tampered report unexpectedly verified")


def test_contract_schemas_are_strict_and_machine_readable(repo_root):
    run_schema = json.loads(
        (repo_root / "evals" / "schemas" / "model-run-evidence.v1.schema.json").read_text()
    )
    report_schema = json.loads(
        (repo_root / "evals" / "schemas" / "model-gate-report.v1.schema.json").read_text()
    )
    assert run_schema["additionalProperties"] is False
    assert run_schema["properties"]["evidence_class"]["const"] == "external_model_run"
    assert report_schema["additionalProperties"] is False
    assert report_schema["properties"]["evidence_class"]["const"] == (
        "local_deterministic_assessment"
    )
    assert "response" not in report_schema["$defs"]["runSummary"]["properties"]
