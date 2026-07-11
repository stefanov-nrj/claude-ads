"""Regression tests for prompt and local-path minimization in shipped JSON."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import generate_image  # noqa: E402


def _lifecycle() -> dict:
    return {
        "schema_version": "1.0.0",
        "lifecycle_id": "private-generation-test",
        "classification": "internal",
        "retention": {"minimum_seconds": 0, "mode": "operator-defined", "delete_after": "2026-07-12T16:00:00Z", "purpose": "Verify sanitized generation output", "exception_reason": None},
        "encryption": {"at_rest": "verified", "in_transit": "verified", "evidence_refs": ["operator-attestation:test-encryption"]},
        "access": {"owner": "test-owner", "authorized_roles": ["test-runner"], "access_log_locator": None},
        "deletion": {"status": "scheduled", "method": "Test cleanup", "verification_required": True, "verification_artifact_locator": None},
        "incident": {"owner": "test-owner", "reporting_channel": "Private test channel", "status": "not-triggered", "record_locator": None},
    }


def test_batch_json_contains_hashes_and_relative_locators_only(tmp_path, monkeypatch, capsys):
    raw_prompt = "private customer launch prompt with unreleased offer"
    batch = tmp_path / "jobs.json"
    batch.write_text(json.dumps([{"prompt": raw_prompt, "output": "creative.png"}]), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_ADS_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setattr(generate_image, "generate_image", lambda *args, **kwargs: (b"private-image", 100, 100))

    generate_image.run_batch(
        str(batch), "artifacts", "gemini", "test-model", "ephemeral-test-key", True, _lifecycle()
    )

    payload = json.loads(capsys.readouterr().out)
    shipped = json.dumps(payload)
    assert raw_prompt not in shipped
    assert str(tmp_path) not in shipped
    assert payload[0]["prompt_sha256"] == hashlib.sha256(raw_prompt.encode()).hexdigest()
    assert payload[0]["prompt_summary"] == generate_image._PROMPT_SUMMARY
    assert payload[0]["file_locator"] == "artifacts/creative.png"
    assert payload[0]["model"] == "test-model"
    assert "prompt" not in payload[0]
    assert "file" not in payload[0]
    assert (tmp_path / "artifacts/creative.png").stat().st_mode & 0o777 == 0o600


def test_failed_capture_result_does_not_echo_raw_url_or_local_path(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    import capture_screenshot

    monkeypatch.setenv("CLAUDE_ADS_OUTPUT_ROOT", str(tmp_path))
    raw_url = "https://example.com/private/customer-42?token=top-secret"
    private_path = "/private/customer/workstation/capture.png"
    lifecycle = _lifecycle()
    lifecycle["classification"] = "confidential"

    result = capture_screenshot.capture_screenshot(
        raw_url,
        private_path,
        data_lifecycle=lifecycle,
        egress_attestation=None,
    )

    shipped = json.dumps(result)
    assert raw_url not in shipped
    assert "customer-42" not in shipped
    assert private_path not in shipped
    assert result["success"] is False
