#!/usr/bin/env python3
"""
Capture screenshots of ad landing pages for creative audit.

Usage:
    python capture_screenshot.py https://example.com/landing --egress-attestation attestation.json
    python capture_screenshot.py https://example.com/landing --viewport mobile --egress-attestation attestation.json
    python capture_screenshot.py https://example.com/landing --all --egress-attestation attestation.json
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlparse

from url_utils import (
    EgressSandboxAttestation,
    create_guarded_browser_context,
    load_egress_sandbox_attestation,
    resolve_output_path,
    sanitize_error,
    sanitize_url,
    validate_browser_url,
)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("Error: playwright required. Install with: pip install -r requirements.txt && playwright install chromium")
    sys.exit(1)


VIEWPORTS = {
    "desktop": {"width": 1920, "height": 1080},
    "tablet": {"width": 768, "height": 1024},
    "mobile": {"width": 375, "height": 812},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _target_origin(validated_url: str) -> str:
    parsed = urlparse(validated_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _write_capture_receipt(
    screenshot_path: Path,
    validated_url: str,
    attestation: EgressSandboxAttestation,
    screenshot_data: bytes | None = None,
) -> Path:
    """Atomically bind a screenshot digest to its verified egress authority."""
    screenshot_data = screenshot_data if screenshot_data is not None else screenshot_path.read_bytes()
    if not screenshot_data:
        raise ValueError("Screenshot artifact is empty; receipt was not written.")
    receipt = {
        "schema_version": "1.0.0",
        "operation": "browser-screenshot-capture",
        "privacy_class": "confidential",
        "recorded_at": _utc_now(),
        "target": {
            "origin": _target_origin(validated_url),
            "url_sha256": hashlib.sha256(validated_url.encode("utf-8")).hexdigest(),
        },
        "artifact": {
            "filename": screenshot_path.name,
            "sha256": hashlib.sha256(screenshot_data).hexdigest(),
            "size": len(screenshot_data),
        },
        "egress_attestation": attestation.audit_reference(),
    }
    receipt_path = resolve_output_path(
        screenshot_path.with_suffix(screenshot_path.suffix + ".receipt.json"),
        create_parent=True,
    )
    _atomic_write_private(
        receipt_path,
        (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return receipt_path


def _atomic_write_private(path: Path, data: bytes) -> None:
    """Write private bytes through an exclusive same-directory temporary file."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def capture_screenshot(
    url: str,
    output_path: str,
    viewport: str = "desktop",
    full_page: bool = False,
    timeout: int = 30000,
    *,
    egress_attestation: EgressSandboxAttestation | None = None,
) -> dict:
    """
    Capture a screenshot of an ad landing page.

    Returns:
        Dictionary with url, output, viewport, success, error
    """
    result = {
        "url": sanitize_url(url),
        "output": str(output_path),
        "viewport": viewport,
        "success": False,
        "error": None,
        "receipt": None,
        "egress_attestation": None,
    }

    if viewport not in VIEWPORTS:
        result["error"] = f"Invalid viewport: {viewport}. Choose from: {list(VIEWPORTS.keys())}"
        return result

    vp = VIEWPORTS[viewport]

    try:
        url = validate_browser_url(url, egress_attestation=egress_attestation)
        output_path = resolve_output_path(output_path, create_parent=True)
    except ValueError as e:
        result["error"] = sanitize_error(e)
        return result

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context, blocked_requests = create_guarded_browser_context(
                browser,
                viewport={"width": vp["width"], "height": vp["height"]},
                device_scale_factor=2 if viewport == "mobile" else 1,
                egress_attestation=egress_attestation,
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout)
            # Playwright follows redirects silently. Re-validate the final URL
            # against the SSRF blocklist (initial URL was already checked).
            validate_browser_url(page.url, egress_attestation=egress_attestation)
            page.wait_for_timeout(1000)
            if blocked_requests:
                blocked = blocked_requests[0]
                raise ValueError(f"Blocked browser request to {blocked['url']}: {blocked['error']}")
            screenshot_data = page.screenshot(full_page=full_page)
            if not isinstance(screenshot_data, bytes) or not screenshot_data:
                raise ValueError("Browser returned an empty screenshot artifact.")
            _atomic_write_private(output_path, screenshot_data)
            try:
                receipt_path = _write_capture_receipt(
                    output_path,
                    url,
                    egress_attestation,
                    screenshot_data,
                )
            except Exception:
                output_path.unlink(missing_ok=True)
                raise
            result["output"] = str(output_path)
            result["receipt"] = str(receipt_path)
            result["egress_attestation"] = egress_attestation.audit_reference()
            result["success"] = True
            browser.close()

    except PlaywrightTimeout:
        result["error"] = f"Page load timed out after {timeout}ms"
    except Exception as e:
        result["error"] = sanitize_error(e)

    return result


def main():
    parser = argparse.ArgumentParser(description="Capture ad landing page screenshots")
    parser.add_argument("url", help="URL to capture")
    parser.add_argument("--output", "-o", default="screenshots", help="Output directory")
    parser.add_argument("--viewport", "-v", default="desktop", choices=VIEWPORTS.keys())
    parser.add_argument("--all", "-a", action="store_true", help="Capture all viewports")
    parser.add_argument("--full", "-f", action="store_true", help="Capture full page")
    parser.add_argument("--timeout", "-t", type=int, default=30000, help="Timeout in ms")
    parser.add_argument(
        "--egress-attestation",
        required=True,
        help=(
            "Path to a signed, short-lived egress-sandbox attestation. Trust key, "
            "key ID, and environment ID must be provisioned through environment variables."
        ),
    )

    args = parser.parse_args()

    try:
        egress_attestation = load_egress_sandbox_attestation(args.egress_attestation)
    except ValueError as exc:
        print(f"Error: {sanitize_error(exc)}", file=sys.stderr)
        sys.exit(1)

    try:
        output_dir = resolve_output_path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
    except ValueError as exc:
        print(f"Error: {sanitize_error(exc)}", file=sys.stderr)
        sys.exit(1)

    parsed = urlparse(args.url)
    base_name = (parsed.hostname or "landing-page").replace(".", "_")

    viewports = VIEWPORTS.keys() if args.all else [args.viewport]

    for viewport in viewports:
        filename = f"{base_name}_{viewport}.png"
        output_path = output_dir / filename

        print(f"Capturing {viewport} screenshot...")
        result = capture_screenshot(
            args.url,
            output_path,
            viewport=viewport,
            full_page=args.full,
            timeout=args.timeout,
            egress_attestation=egress_attestation,
        )

        if result["success"]:
            print(f"  Saved to {output_path}")
            print(f"  Receipt: {result['receipt']}")
        else:
            print(f"  Failed: {result['error']}")


if __name__ == "__main__":
    main()
