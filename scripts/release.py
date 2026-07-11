#!/usr/bin/env python3
"""Audit and reproducibly package the public-safe Claude Ads release surface."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tomllib
import uuid
import zipfile


PACKAGE_PREFIXES = (
    ".claude-plugin/",
    "ads/",
    "agents/",
    "assets/",
    "claude_ads_core/",
    "control-plane/",
    "evals/",
    "scripts/",
    "skills/",
)
PACKAGE_FILES = {
    "CHANGELOG.md",
    "CITATION.cff",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
    "THIRD_PARTY_NOTICES.md",
    "install.ps1",
    "install.sh",
    "pyproject.toml",
    "requirements.txt",
    "uninstall.ps1",
    "uninstall.sh",
}
PACKAGE_EXCLUDED_PREFIXES = ("ads/research-sources/",)
SENSITIVE_FILENAMES = {
    ".env",
    "credentials.json",
    "service-account.json",
    "service_account.json",
}
TEXT_SUFFIXES = {
    ".cff",
    ".css",
    ".csv",
    ".html",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".svg",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = {
    "private key": re.compile(
        r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{40,}"
    ),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "Google API key": re.compile(r"\bAIza[A-Za-z0-9_-]{30,}\b"),
    "OpenAI API key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
}
PRIVATE_PATH_PATTERNS = {
    "Unix home path": re.compile(r"(?<![A-Za-z0-9])(?:/var)?/home/[A-Za-z0-9._-]+/"),
    "macOS home path": re.compile(r"(?<![A-Za-z0-9])/Users/[A-Za-z0-9._-]+/"),
    "Windows home path": re.compile(r"(?i)\b[A-Z]:\\Users\\[A-Za-z0-9._-]+\\"),
    "private Fable corpus path": re.compile(r"(?i)(?:/|\\)Desktop[/\\]Fable 5 Brain(?:/|\\)"),
    "private Brainstein vault path": re.compile(
        r"(?i)(?:/|\\)Desktop[/\\]Vaults[/\\]Brainstein(?:/|\\)"
    ),
}
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class ReleaseError(RuntimeError):
    """A release gate failed."""


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


def tracked_files(root: Path) -> list[str]:
    """Return repository-tracked regular paths in stable POSIX order."""
    paths = _git(root, "ls-files", "-z").decode("utf-8").split("\0")
    return sorted(path for path in paths if path)


def package_files(paths: list[str]) -> list[str]:
    """Select the explicitly public-safe product surface from tracked files."""
    return [
        path
        for path in paths
        if (path in PACKAGE_FILES or any(path.startswith(prefix) for prefix in PACKAGE_PREFIXES))
        and not any(path.startswith(prefix) for prefix in PACKAGE_EXCLUDED_PREFIXES)
    ]


def validate_portable_path(path: str) -> list[str]:
    errors: list[str] = []
    pure = PurePosixPath(path)
    if not path or path.startswith(("/", "\\")) or pure.is_absolute():
        errors.append("path is absolute or empty")
    if "\\" in path:
        errors.append("path contains a backslash")
    if any(part in {"", ".", ".."} for part in pure.parts):
        errors.append("path contains an empty, dot, or traversal segment")
    if any(ord(character) < 32 for character in path):
        errors.append("path contains a control character")
    for part in pure.parts:
        if re.search(r'[<>:"|?*]', part):
            errors.append(f"path segment is not Windows-portable: {part!r}")
        if part.endswith((" ", ".")):
            errors.append(f"path segment has a trailing space or dot: {part!r}")
        if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
            errors.append(f"path segment is reserved on Windows: {part!r}")
    return errors


def _read_text(path: Path) -> str | None:
    if path.suffix.casefold() not in TEXT_SUFFIXES and path.name not in {
        "LICENSE",
        "Dockerfile",
    }:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseError(f"text file is not valid UTF-8: {path}: {exc}") from exc


def _parse_yaml(text: str, path: str) -> object:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised in dependency-free installs
        raise ReleaseError("PyYAML is required for YAML and frontmatter audits") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ReleaseError(f"invalid YAML in {path}: {exc}") from exc


def _frontmatter(text: str, path: str) -> dict[str, object]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ReleaseError(f"missing YAML frontmatter in {path}")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise ReleaseError(f"unterminated YAML frontmatter in {path}") from exc
    value = _parse_yaml("\n".join(lines[1:end]), path)
    if not isinstance(value, dict):
        raise ReleaseError(f"frontmatter must be an object in {path}")
    return value


def _audit_manifest_consistency(root: Path, tracked: set[str]) -> list[str]:
    errors: list[str] = []
    plugin_path = ".claude-plugin/plugin.json"
    marketplace_path = ".claude-plugin/marketplace.json"
    if plugin_path not in tracked or marketplace_path not in tracked:
        return ["plugin and marketplace manifests must both be tracked"]
    plugin = json.loads((root / plugin_path).read_text(encoding="utf-8"))
    marketplace = json.loads((root / marketplace_path).read_text(encoding="utf-8"))
    entries = marketplace.get("plugins") if isinstance(marketplace, dict) else None
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        return ["marketplace manifest must contain exactly one plugin object"]
    entry = entries[0]
    for field in ("name", "version", "license", "repository"):
        if plugin.get(field) != entry.get(field):
            errors.append(f"plugin and marketplace disagree on {field!r}")
    for skill_root in plugin.get("skills", []):
        if not isinstance(skill_root, str):
            errors.append("plugin skills entries must be strings")
            continue
        normalized = skill_root.removeprefix("./").rstrip("/")
        if normalized not in {"ads", "skills"}:
            errors.append(f"unexpected plugin skill root: {skill_root!r}")
        if not (root / normalized).is_dir():
            errors.append(f"plugin skill root does not exist: {skill_root!r}")
    return errors


def audit_repository(root: Path) -> list[str]:
    """Audit tracked repository files without changing the repository."""
    errors: list[str] = []
    tracked = tracked_files(root)
    folded: dict[str, str] = {}
    skill_names: dict[str, str] = {}

    for relative in tracked:
        for issue in validate_portable_path(relative):
            errors.append(f"{relative}: {issue}")
        collision = folded.setdefault(relative.casefold(), relative)
        if collision != relative:
            errors.append(f"case-insensitive path collision: {collision!r} and {relative!r}")

        filename = PurePosixPath(relative).name.casefold()
        if filename in SENSITIVE_FILENAMES or filename.startswith(".env."):
            errors.append(f"sensitive filename must not be tracked: {relative}")
        if PurePosixPath(relative).suffix.casefold() in {".key", ".p12", ".pfx", ".pem"}:
            errors.append(f"credential-like file must not be tracked: {relative}")

        path = root / relative
        if path.is_symlink():
            errors.append(f"tracked symlink is not release-safe: {relative}")
            continue
        if not path.is_file():
            errors.append(f"tracked path is not a regular file: {relative}")
            continue

        text = _read_text(path)
        if text is None:
            continue
        if relative.endswith(".json"):
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"invalid JSON in {relative}: {exc}")
        if relative.endswith((".yml", ".yaml", ".cff")):
            try:
                _parse_yaml(text, relative)
            except ReleaseError as exc:
                errors.append(str(exc))

        if relative == "ads/SKILL.md" or (
            relative.startswith("skills/") and relative.endswith("/SKILL.md")
        ):
            try:
                frontmatter = _frontmatter(text, relative)
            except ReleaseError as exc:
                errors.append(str(exc))
            else:
                expected_name = PurePosixPath(relative).parent.name
                name = frontmatter.get("name")
                description = frontmatter.get("description")
                if name != expected_name:
                    errors.append(
                        f"{relative}: frontmatter name {name!r} must equal {expected_name!r}"
                    )
                if not isinstance(description, str) or not description.strip():
                    errors.append(f"{relative}: frontmatter description must be a non-empty string")
                if isinstance(name, str):
                    previous = skill_names.setdefault(name, relative)
                    if previous != relative:
                        errors.append(f"duplicate skill name {name!r}: {previous} and {relative}")

        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{relative}: possible {label}")
        for label, pattern in PRIVATE_PATH_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{relative}: contains {label}")

    errors.extend(_audit_manifest_consistency(root, set(tracked)))
    selected = package_files(tracked)
    if not selected:
        errors.append("public-safe package selection is empty")
    for required in ("LICENSE", "README.md", ".claude-plugin/plugin.json", "ads/SKILL.md"):
        if required not in selected:
            errors.append(f"required release file is missing or untracked: {required}")
    return sorted(set(errors))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _component_from_requirement(requirement: str, source: str) -> dict[str, object] | None:
    value = requirement.split("#", 1)[0].strip()
    if not value or value.startswith(("-", "git+", "http://", "https://")):
        return None
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[([^]]+)\])?(.*)$", value)
    if not match:
        return None
    name, extras, constraint = match.groups()
    component: dict[str, object] = {
        "type": "library",
        "name": name,
        "purl": f"pkg:pypi/{name.casefold().replace('_', '-')}",
        "properties": [
            {"name": "claude-ads:manifest", "value": source},
            {"name": "claude-ads:requirement", "value": value},
        ],
    }
    if source == "requirements-dev.txt":
        component["scope"] = "optional"
    if extras:
        component["properties"].append({"name": "claude-ads:extras", "value": extras})
    if constraint:
        component["properties"].append(
            {"name": "claude-ads:version-constraint", "value": constraint.strip()}
        )
    return component


def build_sbom(root: Path, product_name: str, version: str) -> dict[str, object]:
    components: dict[str, dict[str, object]] = {}
    for manifest in ("requirements.txt", "requirements-dev.txt"):
        path = root / manifest
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            component = _component_from_requirement(line, manifest)
            if component:
                key = str(component["name"]).casefold().replace("_", "-")
                if key in components:
                    existing = components[key]["properties"]
                    for prop in component["properties"]:
                        if prop not in existing:
                            existing.append(prop)
                else:
                    components[key] = component

    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    for requirement in pyproject.get("project", {}).get("dependencies", []):
        component = _component_from_requirement(requirement, "pyproject.toml")
        if component:
            components.setdefault(str(component["name"]).casefold(), component)

    ordered = [components[key] for key in sorted(components)]
    identity = json.dumps(ordered, sort_keys=True, separators=(",", ":"))
    serial = uuid.uuid5(uuid.NAMESPACE_URL, f"claude-ads:{version}:{identity}")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": product_name,
                "version": version,
            }
        },
        "components": ordered,
    }


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _product(root: Path) -> tuple[str, str]:
    manifest = json.loads((root / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    name, version = manifest.get("name"), manifest.get("version")
    if not isinstance(name, str) or not isinstance(version, str) or not name or not version:
        raise ReleaseError("plugin manifest requires string name and version")
    return name, version


def build_release(root: Path, output_dir: Path) -> dict[str, Path]:
    errors = audit_repository(root)
    if errors:
        raise ReleaseError("repository audit failed:\n- " + "\n- ".join(errors))

    name, version = _product(root)
    selected = package_files(tracked_files(root))
    archive_root = f"{name}-{version}"
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"{archive_root}.zip"
    file_records: list[dict[str, object]] = []

    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative in selected:
            path = root / relative
            if path.is_symlink() or not path.is_file():
                raise ReleaseError(f"release input is not a regular file: {relative}")
            data = path.read_bytes()
            executable = bool(path.stat().st_mode & stat.S_IXUSR)
            mode = 0o755 if executable else 0o644
            info = zipfile.ZipInfo(f"{archive_root}/{relative}", ZIP_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            file_records.append(
                {
                    "path": relative,
                    "sha256": _sha256(data),
                    "size": len(data),
                    "mode": f"{mode:04o}",
                }
            )

    archive_data = archive_path.read_bytes()
    commit = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    release_manifest = {
        "schema_version": "1.0.0",
        "product": {"name": name, "version": version},
        "source": {"commit": commit},
        "archive": {
            "file": archive_path.name,
            "root": archive_root,
            "sha256": _sha256(archive_data),
            "size": len(archive_data),
        },
        "files": file_records,
    }
    manifest_path = output_dir / "release-manifest.json"
    sbom_path = output_dir / "sbom.cdx.json"
    checksums_path = output_dir / "SHA256SUMS"
    manifest_path.write_bytes(_json_bytes(release_manifest))
    sbom_path.write_bytes(_json_bytes(build_sbom(root, name, version)))

    checksum_targets = (archive_path, manifest_path, sbom_path)
    checksums_path.write_text(
        "".join(f"{_sha256(path.read_bytes())}  {path.name}\n" for path in checksum_targets),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "archive": archive_path,
        "manifest": manifest_path,
        "sbom": sbom_path,
        "checksums": checksums_path,
    }


def verify_release(output_dir: Path) -> None:
    checksums_path = output_dir / "SHA256SUMS"
    if not checksums_path.is_file():
        raise ReleaseError("SHA256SUMS is missing")
    for line in checksums_path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        if not match:
            raise ReleaseError(f"invalid SHA256SUMS line: {line!r}")
        expected, filename = match.groups()
        path = output_dir / filename
        if not path.is_file() or _sha256(path.read_bytes()) != expected:
            raise ReleaseError(f"checksum mismatch: {filename}")

    manifest_path = output_dir / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_meta = manifest["archive"]
    archive_path = output_dir / archive_meta["file"]
    if _sha256(archive_path.read_bytes()) != archive_meta["sha256"]:
        raise ReleaseError("archive digest disagrees with release manifest")

    expected_records = {record["path"]: record for record in manifest["files"]}
    root = archive_meta["root"]
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        expected_names = [f"{root}/{path}" for path in expected_records]
        if names != expected_names:
            raise ReleaseError("archive paths or order disagree with release manifest")
        for name in names:
            relative = name.removeprefix(f"{root}/")
            for issue in validate_portable_path(relative):
                raise ReleaseError(f"unsafe archive path {name!r}: {issue}")
            record = expected_records[relative]
            data = archive.read(name)
            if len(data) != record["size"] or _sha256(data) != record["sha256"]:
                raise ReleaseError(f"archive member disagrees with manifest: {relative}")

    sbom = json.loads((output_dir / "sbom.cdx.json").read_text(encoding="utf-8"))
    if sbom.get("bomFormat") != "CycloneDX" or not isinstance(sbom.get("components"), list):
        raise ReleaseError("SBOM is not a CycloneDX component inventory")


def _root(value: str | None) -> Path:
    if value:
        return Path(value).resolve()
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="repository root (defaults to script parent)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("audit", help="audit tracked repository files")
    package_parser = subparsers.add_parser("package", help="build deterministic artifacts")
    package_parser.add_argument("--output-dir", default="dist")
    verify_parser = subparsers.add_parser("verify", help="verify built artifacts")
    verify_parser.add_argument("--output-dir", default="dist")
    args = parser.parse_args(argv)
    root = _root(args.root)

    try:
        if args.command == "audit":
            errors = audit_repository(root)
            if errors:
                raise ReleaseError("repository audit failed:\n- " + "\n- ".join(errors))
            print(f"release audit passed ({len(tracked_files(root))} tracked files)")
        elif args.command == "package":
            artifacts = build_release(root, (root / args.output_dir).resolve())
            verify_release((root / args.output_dir).resolve())
            for kind, path in artifacts.items():
                print(f"{kind}: {path}")
        else:
            verify_release((root / args.output_dir).resolve())
            print("release artifacts verified")
    except (OSError, KeyError, TypeError, ValueError, ReleaseError, zipfile.BadZipFile) as exc:
        print(f"release error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
