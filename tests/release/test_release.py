from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

RELEASE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"
SPEC = importlib.util.spec_from_file_location("claude_ads_release", RELEASE_SCRIPT)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)

ReleaseError = release.ReleaseError
audit_repository = release.audit_repository
build_release = release.build_release
build_sbom = release.build_sbom
evaluate_release_gate = release.evaluate_release_gate
validate_portable_path = release.validate_portable_path
verify_github_run = release.verify_github_run
verify_release = release.verify_release
check_grounding_and_capabilities = release._check_grounding_and_capabilities


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Release Test")
    _git(root, "config", "user.email", "release-test@example.invalid")
    _write(
        root,
        ".claude-plugin/plugin.json",
        json.dumps(
            {
                "name": "claude-ads",
                "version": "2.0.0",
                "license": "MIT",
                "repository": "https://example.invalid/claude-ads",
                "skills": ["./ads/", "./skills/"],
            }
        ),
    )
    _write(
        root,
        ".claude-plugin/marketplace.json",
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "claude-ads",
                        "version": "2.0.0",
                        "license": "MIT",
                        "repository": "https://example.invalid/claude-ads",
                    }
                ]
            }
        ),
    )
    _write(root, "ads/SKILL.md", "---\nname: ads\ndescription: Main skill.\n---\n# Ads\n")
    _write(
        root,
        "skills/ads-google/SKILL.md",
        "---\nname: ads-google\ndescription: Google Ads.\n---\n# Google\n",
    )
    (root / "skills").mkdir(exist_ok=True)
    _write(root, "README.md", "# Claude Ads\n")
    _write(root, "LICENSE", "MIT\n")
    _write(
        root,
        "pyproject.toml",
        '[project]\nname = "claude-ads-core"\nversion = "2.0.0"\ndependencies = []\n',
    )
    _write(root, "requirements.txt", "requests>=2.32,<3\n")
    _write(root, "requirements-dev.txt", "pytest>=8,<9\nrequests>=2.32,<3\n")
    _write(root, "ads/research-sources/raw.md", "Research output does not ship.\n")
    _write(root, "branding/internal.html", "Internal branding does not ship.\n")
    _write(root, "research/private.md", "This tracked research does not ship.\n")
    _write(root, "tests/not-packaged.txt", "Tracked tests do not ship.\n")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "fixture")
    return root


@pytest.mark.parametrize(
    "path",
    ["../escape", "/absolute", r"windows\separator", "aux.txt", "safe/../escape"],
)
def test_portable_paths_reject_unsafe_names(path: str) -> None:
    assert validate_portable_path(path)


def test_audit_checks_frontmatter_and_sensitive_content(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    assert audit_repository(root) == []

    skill = root / "skills/ads-google/SKILL.md"
    skill.write_text(
        "---\nname: wrong-name\ndescription: Google Ads.\n---\n",
        encoding="utf-8",
    )
    errors = audit_repository(root)
    assert any("frontmatter name" in error for error in errors)

    skill.write_text(
        "---\nname: ads-google\ndescription: Google Ads.\n---\n"
        "Local file: /var/ho" + "me/someone/private.txt\n",
        encoding="utf-8",
    )
    errors = audit_repository(root)
    assert any("Unix home path" in error for error in errors)


def test_package_is_deterministic_public_safe_and_verifiable(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    first = build_release(root, root / "dist-a")
    second = build_release(root, root / "dist-b")

    assert hashlib.sha256(first["archive"].read_bytes()).digest() == hashlib.sha256(
        second["archive"].read_bytes()
    ).digest()
    assert first["manifest"].read_bytes() == second["manifest"].read_bytes()
    assert first["sbom"].read_bytes() == second["sbom"].read_bytes()
    verify_release(root / "dist-a")

    with zipfile.ZipFile(first["archive"]) as archive:
        names = archive.namelist()
        assert "claude-ads-2.0.0/ads/SKILL.md" in names
        assert not any(
            excluded in name
            for name in names
            for excluded in ("research/", "research-sources/", "tests/", "branding/")
        )
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())

    sbom = json.loads(first["sbom"].read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX"
    assert [component["name"] for component in sbom["components"]] == ["pytest", "requests"]


def test_verify_detects_tampering(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    artifacts = build_release(root, root / "dist")
    artifacts["archive"].write_bytes(artifacts["archive"].read_bytes() + b"tampered")
    with pytest.raises(ReleaseError, match="checksum mismatch"):
        verify_release(root / "dist")


def test_sbom_uses_actual_manifests(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    sbom = build_sbom(root, "claude-ads", "2.0.0")
    components = {component["name"]: component for component in sbom["components"]}
    assert set(components) == {"pytest", "requests"}
    request_sources = {
        property_["value"]
        for property_ in components["requests"]["properties"]
        if property_["name"] == "claude-ads:manifest"
    }
    assert request_sources == {"requirements.txt", "requirements-dev.txt"}


def test_claude_command_contract_distinguishes_plugin_namespace() -> None:
    root = RELEASE_SCRIPT.parents[1]
    product = json.loads(
        (root / "control-plane/manifests/product-manifest.json").read_text(encoding="utf-8")
    )
    assert product["canonical_command"] == "/ads"
    assert product["runtime_commands"] == {
        "claude_standalone_skill": "/ads",
        "claude_plugin": "/claude-ads:ads",
    }
    readme = (root / "README.md").read_text(encoding="utf-8")
    boundaries = (root / "control-plane/PRODUCT_BOUNDARIES.md").read_text(encoding="utf-8")
    for text in (readme, boundaries):
        assert "/ads" in text
        assert "/claude-ads:ads" in text


def test_release_grounding_gate_validates_control_registry_and_profiles() -> None:
    root = RELEASE_SCRIPT.parents[1]
    result = check_grounding_and_capabilities(root, release.date(2026, 7, 11))
    assert result["registered_control_count"] == 412
    assert result["source_grounded_control_count"] > 0
    assert result["enabled_scoring_profile_count"] == 0
    assert result["disabled_scoring_profile_count"] == 12


def test_release_gate_fails_closed_without_external_model_review_and_ci_evidence() -> None:
    root = RELEASE_SCRIPT.parents[1]
    report = evaluate_release_gate(
        root,
        model_report=None,
        review_evidence_dir=None,
        github_run_id=None,
    )
    checks = {item["id"]: item for item in report["checks"]}
    assert report["evidence_class"] == "release-gate-assessment"
    assert report["release_gate_satisfied"] is False
    assert checks["canonical-model-evaluation"]["status"] == "fail"
    assert checks["independent-reviews"]["status"] == "fail"
    assert checks["remote-ci"]["status"] == "fail"


def test_release_gate_forwards_external_model_trust_inputs(tmp_path: Path, monkeypatch) -> None:
    root = RELEASE_SCRIPT.parents[1]
    model_report = tmp_path / "model-report.json"
    model_report.write_text("{}", encoding="utf-8")
    captured = {}

    class ModelGate:
        @staticmethod
        def verify_release_report(*args, **kwargs):
            captured.update(kwargs)
            return {"release_gate_satisfied": True}

    original = release._load_local_module

    def load(root_arg, relative, module_name):
        if relative == "evals/model_eval_gate.py":
            return ModelGate
        return original(root_arg, relative, module_name)

    monkeypatch.setattr(release, "_load_local_module", load)
    evaluate_release_gate(
        root,
        model_report=model_report,
        review_evidence_dir=None,
        github_run_id=None,
        model_trust_bundle_json='{"external":true}',
        model_implementation_principals_json='["implementer"]',
    )
    assert captured == {
        "trust_bundle_json": '{"external":true}',
        "implementation_principals_json": '["implementer"]',
    }


def test_remote_ci_verifier_requires_exact_private_subject_and_all_jobs(
    tmp_path: Path, monkeypatch
) -> None:
    root = _repository(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    required_jobs = [
        "Repository audit",
        "Core tests (Python 3.11)",
        "Core tests (Python 3.12)",
        "Full test suite",
        "Installer tests (ubuntu-latest)",
        "Installer tests (macos-latest)",
        "Installer tests (windows-latest)",
        "Reproducible package smoke test",
    ]

    def evidence(_root: Path, endpoint: str) -> dict:
        if endpoint == "repos/AI-Marketing-Hub/claude-ads":
            return {"visibility": "private", "private": True}
        if endpoint.endswith("/jobs?per_page=100"):
            return {"jobs": [{"name": name, "conclusion": "success"} for name in required_jobs]}
        return {
            "head_sha": commit,
            "head_branch": "v2",
            "status": "completed",
            "conclusion": "success",
            "path": ".github/workflows/ci.yml",
            "html_url": "https://github.example.invalid/actions/runs/123",
        }

    monkeypatch.setattr(release, "_gh_json", evidence)
    result = verify_github_run(root, "123", commit)
    assert result["head_sha"] == commit
    assert result["repository_visibility"] == "private"

    def wrong_subject(_root: Path, endpoint: str) -> dict:
        value = evidence(_root, endpoint)
        if endpoint.endswith("/actions/runs/123"):
            value = {**value, "head_sha": "0" * 40}
        return value

    monkeypatch.setattr(release, "_gh_json", wrong_subject)
    with pytest.raises(ReleaseError, match="exact private v2 subject"):
        verify_github_run(root, "123", commit)
