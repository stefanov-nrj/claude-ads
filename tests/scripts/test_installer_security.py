"""Installer ownership and dependency-isolation regression tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / script), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _install(tmp_path: Path) -> tuple[Path, Path]:
    skills = tmp_path / "skills"
    agents = tmp_path / "agents"
    result = _run(
        "install.sh",
        "--target=claude",
        "--source=local",
        "--no-deps",
        f"--skill-dir={skills}",
        f"--agent-dir={agents}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return skills, agents


def test_bash_installer_syntax_and_no_global_pip_escape_hatch():
    for script in ("install.sh", "uninstall.sh"):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / script)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr

    installer = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "--break-system-packages" not in installer
    assert "curl -fsSL" not in installer
    assert "python3 -m venv" in installer


def test_manifest_owned_uninstall_preserves_unrelated_ads_skill(tmp_path):
    skills, agents = _install(tmp_path)
    unrelated = skills / "ads-user-owned"
    unrelated.mkdir()
    (unrelated / "SKILL.md").write_text("user data", encoding="utf-8")

    result = _run(
        "uninstall.sh",
        "--target=claude",
        f"--skill-dir={skills}",
        f"--agent-dir={agents}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (skills / "ads" / "SKILL.md").exists()
    assert unrelated.joinpath("SKILL.md").read_text(encoding="utf-8") == "user data"
    assert not (skills / ".claude-ads-claude.manifest").exists()


def test_installer_includes_portable_interface_and_all_platform_surfaces(tmp_path):
    skills, agents = _install(tmp_path)
    assert (skills / "ads" / "agents" / "openai.yaml").is_file()
    for platform in (
        "google", "meta", "youtube", "linkedin", "tiktok", "microsoft",
        "apple", "amazon", "reddit", "pinterest", "snapchat", "x",
    ):
        assert (skills / f"ads-{platform}" / "SKILL.md").is_file()
        assert (agents / f"audit-{platform}.md").is_file()


def test_tampered_manifest_fails_before_removing_files(tmp_path):
    skills, agents = _install(tmp_path)
    manifest = skills / ".claude-ads-claude.manifest"
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(f"F\t{tmp_path.parent / 'outside.txt'}\n")

    result = _run(
        "uninstall.sh",
        "--target=claude",
        f"--skill-dir={skills}",
        f"--agent-dir={agents}",
    )
    assert result.returncode != 0
    assert "Unsafe ownership-manifest path" in result.stderr
    assert (skills / "ads" / "SKILL.md").exists()


def test_installer_refuses_symlink_escape(tmp_path):
    skills = tmp_path / "skills"
    agents = tmp_path / "agents"
    outside = tmp_path / "outside"
    skills.mkdir()
    outside.mkdir()
    (skills / "ads").symlink_to(outside, target_is_directory=True)

    result = _run(
        "install.sh",
        "--target=claude",
        "--source=local",
        "--no-deps",
        f"--skill-dir={skills}",
        f"--agent-dir={agents}",
    )
    assert result.returncode != 0
    assert "Refusing symlinked install directory" in result.stderr
    assert not (outside / "SKILL.md").exists()


def test_manifest_traversal_is_rejected_before_removal(tmp_path):
    skills, agents = _install(tmp_path)
    manifest = skills / ".claude-ads-claude.manifest"
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(f"F\t{skills / 'ads' / '..' / '..' / 'outside.txt'}\n")

    result = _run(
        "uninstall.sh",
        "--target=claude",
        f"--skill-dir={skills}",
        f"--agent-dir={agents}",
    )
    assert result.returncode != 0
    assert (skills / "ads" / "SKILL.md").exists()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("script", ["install.ps1", "uninstall.ps1"])
def test_powershell_scripts_parse(script):
    command = f"[void][scriptblock]::Create((Get-Content -Raw '{ROOT / script}'))"
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is not installed")
def test_powershell_install_uninstall_round_trip_preserves_unrelated_skill(tmp_path):
    skills = tmp_path / "skills"
    agents = tmp_path / "agents"
    install = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(ROOT / "install.ps1"),
            "-Target",
            "claude",
            "-SkillDir",
            str(skills),
            "-AgentDir",
            str(agents),
            "-Source",
            "local",
            "-RepoDir",
            str(ROOT),
            "-NoDeps",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    assert (skills / "ads" / "SKILL.md").is_file()
    assert (skills / ".claude-ads-claude.manifest.json").is_file()

    unrelated = skills / "ads-weather"
    unrelated.mkdir()
    unrelated.joinpath("SKILL.md").write_text("user-owned\n", encoding="utf-8")
    uninstall = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(ROOT / "uninstall.ps1"),
            "-Target",
            "claude",
            "-SkillDir",
            str(skills),
            "-AgentDir",
            str(agents),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
    assert not (skills / "ads" / "SKILL.md").exists()
    assert unrelated.joinpath("SKILL.md").read_text(encoding="utf-8") == "user-owned\n"
