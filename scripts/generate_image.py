#!/usr/bin/env python3
"""Generate ad creative images through an explicitly selected API adapter.

This local CLI is a fallback for environments whose approved image capability is
not exposed through a host-native tool. It has no default provider or model.
Select both from current capability evidence for every run.

Implemented adapters are Gemini, OpenAI, Stability AI, and Replicate. Adapter
availability does not imply operator approval, account access, model availability,
or fitness for a placement.

Usage:
    python generate_image.py "prompt text" --provider "$ADS_IMAGE_PROVIDER" --model "$ADS_IMAGE_MODEL" --ratio 9:16 --output ad.png
    python generate_image.py --batch prompts.json --provider "$ADS_IMAGE_PROVIDER" --model "$ADS_IMAGE_MODEL" --output-dir ./ad-assets/

Environment variables:
    ADS_IMAGE_PROVIDER   Required unless --provider is supplied
    ADS_IMAGE_MODEL      Required unless --model is supplied
    GOOGLE_API_KEY       Required for gemini provider
    OPENAI_API_KEY       Required for openai provider
    STABILITY_API_KEY    Required for stability provider
    REPLICATE_API_TOKEN  Required for replicate provider

See ads/references/image-providers.md for pricing and capability details.
"""

import argparse
import base64
import hashlib
import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from claude_ads_core.contracts import ContractError, load_contract, validate_contract
# Single source of truth for credential redaction (see scripts/url_utils.py).
# Re-exported under the local _sanitize_error name so existing call sites do
# not need to change.
from url_utils import artifact_locator, guarded_request, resolve_output_path, sanitize_error as _sanitize_error

# Aspect ratio shorthand → (width, height)
ASPECT_RATIOS = {
    "1:1":   (1080, 1080),
    "9:16":  (1080, 1920),
    "16:9":  (1920, 1080),
    "4:5":   (1080, 1350),
    "4:3":   (1200, 900),
    "3:4":   (900, 1200),
    "1.91:1": (1200, 628),  # Google PMax / LinkedIn landscape
    "4:1":   (1200, 300),   # Google Logo landscape
    "21:9":  (2520, 1080),  # Ultra-wide
}

# Gemini API ratio strings (closest supported ratio for each alias)
GEMINI_RATIO_MAP = {
    "1:1":    "1:1",
    "9:16":   "9:16",
    "16:9":   "16:9",
    "4:5":    "4:5",
    "4:3":    "4:3",
    "3:4":    "3:4",
    "1.91:1": "16:9",  # Closest Gemini supports; crop in post if needed
    "4:1":    "4:1",
    "21:9":   "21:9",
}

MAX_RETRIES = 4
RETRY_BACKOFF = [1, 2, 4, 8]  # seconds

MAX_BATCH_SIZE = 50
MAX_DIMENSION = 8192
_ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}

_PROMPT_SUMMARY = "[redacted: raw prompt is ephemeral and is not persisted]"


def _prompt_record(prompt: str) -> dict[str, str]:
    """Return the irreversible prompt identity allowed in shipped JSON."""
    return {
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_summary": _PROMPT_SUMMARY,
    }


def _reference_image_sha256(path: str | None) -> str | None:
    if not path:
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_private(path: Path, data: bytes) -> None:
    """Atomically write a generated asset with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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


def _actual_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    """
    Extract actual width/height from PNG or JPEG header without PIL.
    Returns (width, height) or None if format is unrecognised.
    """
    if len(image_bytes) < 24:
        return None
    # PNG: 8-byte signature + 4-byte IHDR length + 4-byte "IHDR" + 4-byte W + 4-byte H
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        w = struct.unpack('>I', image_bytes[16:20])[0]
        h = struct.unpack('>I', image_bytes[20:24])[0]
        return w, h
    # JPEG: scan for SOF0 (0xFF 0xC0) or SOF2 (0xFF 0xC2) marker
    i = 2  # skip FF D8 SOI
    while i < len(image_bytes) - 8:
        if image_bytes[i] != 0xFF:
            break
        marker = image_bytes[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):  # SOFn markers
            h = struct.unpack('>H', image_bytes[i + 5:i + 7])[0]
            w = struct.unpack('>H', image_bytes[i + 7:i + 9])[0]
            return w, h
        seg_len = struct.unpack('>H', image_bytes[i + 2:i + 4])[0]
        i += 2 + seg_len
    return None


def _get_api_key(provider: str) -> str:
    """Retrieve API key for the given provider from environment."""
    key_map = {
        "gemini":    ("GOOGLE_API_KEY",      "console.cloud.google.com/apis/credentials"),
        "openai":    ("OPENAI_API_KEY",       "platform.openai.com/api-keys"),
        "stability": ("STABILITY_API_KEY",    "platform.stability.ai"),
        "replicate": ("REPLICATE_API_TOKEN",  "replicate.com/account/api-tokens"),
    }

    if provider not in key_map:
        print(
            f"Error: Unknown provider '{provider}'. Valid options: gemini, openai, stability, replicate",
            file=sys.stderr,
        )
        sys.exit(1)

    env_var, url = key_map[provider]
    key = os.environ.get(env_var)

    if not key:
        print(
            f"Error: {env_var} not set.\n"
            f"To use the {provider} provider:\n"
            f"  export {env_var}=\"your-key\"\n"
            f"  Get a key at: {url}\n"
            f"\nTo use a different approved capability, set both "
            f"ADS_IMAGE_PROVIDER and ADS_IMAGE_MODEL explicitly.",
            file=sys.stderr,
        )
        sys.exit(1)

    return key


def _dims_from_ratio(ratio: str) -> tuple[int, int]:
    """Return (width, height) for a ratio string."""
    if ratio in ASPECT_RATIOS:
        return ASPECT_RATIOS[ratio]
    # Try parsing WxH directly (e.g. "1200x628")
    if "x" in ratio:
        try:
            w, h = int(ratio.lower().split("x")[0]), int(ratio.lower().split("x")[1])
            if w < 1 or h < 1 or w > MAX_DIMENSION or h > MAX_DIMENSION:
                print(f"Error: Dimensions must be 1-{MAX_DIMENSION}. Got {w}x{h}", file=sys.stderr)
                sys.exit(1)
            return w, h
        except (ValueError, IndexError):
            pass
    print(f"Error: Unknown ratio '{ratio}'. Use one of: {', '.join(ASPECT_RATIOS.keys())} or WxH (e.g. 1200x628)", file=sys.stderr)
    sys.exit(1)


def generate_gemini(prompt: str, width: int, height: int, api_key: str, model: str, reference_image_path: str | None = None) -> bytes:
    """Generate image using Gemini API (google-genai package).

    Args:
        reference_image_path: Optional path to a brand screenshot for style-guided
            generation. When provided, the image is passed as a visual style reference
            alongside the text prompt when the explicitly selected model supports it.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print(
            "Error: google-genai package required.\n"
            "Install with: pip install google-genai>=1.16.0",
            file=sys.stderr,
        )
        sys.exit(1)

    # Determine closest Gemini aspect ratio
    ratio_key = None
    for k, (w, h) in ASPECT_RATIOS.items():
        if w == width and h == height:
            ratio_key = k
            break
    gemini_ratio = GEMINI_RATIO_MAP.get(ratio_key, "1:1") if ratio_key else "1:1"

    client = genai.Client(api_key=api_key)

    # Build contents, with optional brand reference image for style guidance
    if reference_image_path and os.path.exists(reference_image_path):
        if Path(reference_image_path).suffix.lower() not in _ALLOWED_IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported reference image format: {Path(reference_image_path).suffix}")
        with open(reference_image_path, 'rb') as f:
            ref_bytes = f.read()
        mime = 'image/png' if reference_image_path.lower().endswith('.png') else 'image/jpeg'
        ref_part = types.Part.from_bytes(data=ref_bytes, mime_type=mime)
        contents = [
            ref_part,
            f"Generate an ad creative that matches the visual style, color palette, "
            f"and aesthetic of the brand shown in the reference image. {prompt}"
        ]
    else:
        contents = prompt

    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(
                        aspect_ratio=gemini_ratio,
                    ),
                ),
            )
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    return part.inline_data.data  # already bytes in google-genai >= 1.16.0
            raise RuntimeError("No image data in Gemini response")

        except Exception as e:
            err_str = _sanitize_error(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF[attempt]
                    print(f"Rate limit hit, retrying in {wait}s...", file=sys.stderr)
                    time.sleep(wait)
                    continue
            raise


def generate_openai(prompt: str, width: int, height: int, api_key: str, model: str) -> bytes:
    """Generate image using OpenAI API."""
    try:
        from openai import OpenAI
    except ImportError:
        print(
            "Error: openai package required.\n"
            "Install with: pip install openai>=1.75.0",
            file=sys.stderr,
        )
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    # OpenAI gpt-image-1 supported sizes: 1024x1024, 1536x1024, 1024x1536
    if width == height:
        size = "1024x1024"
    elif width > height:
        size = "1536x1024"
    else:
        size = "1024x1536"

    response = client.images.generate(
        model=model,
        prompt=prompt,
        n=1,
        size=size,
        response_format="b64_json",
    )
    return base64.b64decode(response.data[0].b64_json)


def generate_stability(prompt: str, width: int, height: int, api_key: str, model: str) -> bytes:
    """Generate image using Stability AI API."""
    try:
        import requests
    except ImportError:
        print("Error: requests package required. pip install requests", file=sys.stderr)
        sys.exit(1)

    url = "https://api.stability.ai/v2beta/stable-image/generate/sd3"
    headers = {
        "authorization": f"Bearer {api_key}",
        "accept": "image/*",
    }
    data = {
        "prompt": prompt,
        "model": model.split("/")[-1] if "/" in model else model,
        "aspect_ratio": _nearest_stability_ratio(width, height),
        "output_format": "png",
    }
    resp = guarded_request(
        requests,
        "POST",
        url,
        headers=headers,
        files={"none": ""},
        data=data,
        timeout=120,
    )
    if resp.status_code == 200:
        return resp.content
    raise RuntimeError(f"Stability API error {resp.status_code}: {resp.text[:200]}")


def _nearest_stability_ratio(width: int, height: int) -> str:
    """Map dimensions to Stability AI's supported aspect ratio strings."""
    ratio = width / height
    stability_ratios = {
        "1:1": 1.0,
        "16:9": 16/9,
        "9:16": 9/16,
        "4:5": 4/5,
        "5:4": 5/4,
        "3:2": 3/2,
        "2:3": 2/3,
    }
    return min(stability_ratios.keys(), key=lambda r: abs(stability_ratios[r] - ratio))


def generate_replicate(prompt: str, width: int, height: int, api_key: str, model: str) -> bytes:
    """Generate image using Replicate API."""
    try:
        import replicate
        import requests
    except ImportError:
        print(
            "Error: replicate package required.\n"
            "Install with: pip install replicate>=1.0.4",
            file=sys.stderr,
        )
        sys.exit(1)

    client = replicate.Client(api_token=api_key)
    output = client.run(
        model,
        input={
            "prompt": prompt,
            "width": width,
            "height": height,
            "output_format": "png",
        },
    )
    # Output is a URL or list of URLs
    url = output[0] if isinstance(output, list) else str(output)
    if urlparse(url).scheme != "https":
        raise RuntimeError(f"Replicate returned non-HTTPS URL: {url[:100]}")
    # Defense-in-depth: Replicate is trusted but revalidate against the SSRF
    # blocklist so an upstream compromise can't redirect us to a private IP.
    try:
        from url_utils import validate_url as _validate_url
        _validate_url(url)
    except ValueError as ve:
        raise RuntimeError(f"Replicate URL failed SSRF validation: {ve}") from ve
    # allow_redirects=False — the SSRF check above only validated the original
    # URL. A redirect target could be a private IP; refuse to follow at all.
    resp = guarded_request(requests, "GET", url, timeout=120)
    resp.raise_for_status()
    return resp.content


def generate_image(
    prompt: str,
    ratio: str,
    provider: str,
    model: str,
    api_key: str,
    reference_image_path: str | None = None,
) -> tuple[bytes, int, int]:
    """
    Generate a single image. Returns (image_bytes, width, height).
    """
    provider, model = _require_selection(provider, model)
    width, height = _dims_from_ratio(ratio)

    if provider == "gemini":
        image_bytes = generate_gemini(
            prompt, width, height, api_key, model, reference_image_path
        )
    elif provider == "openai":
        if reference_image_path:
            raise ValueError(
                "The openai adapter does not declare reference-image support; "
                "choose a verified compatible capability or omit the reference image"
            )
        image_bytes = generate_openai(prompt, width, height, api_key, model)
    elif provider == "stability":
        if reference_image_path:
            raise ValueError(
                "The stability adapter does not declare reference-image support; "
                "choose a verified compatible capability or omit the reference image"
            )
        image_bytes = generate_stability(prompt, width, height, api_key, model)
    elif provider == "replicate":
        if reference_image_path:
            raise ValueError(
                "The replicate adapter does not declare reference-image support; "
                "choose a verified compatible capability or omit the reference image"
            )
        image_bytes = generate_replicate(prompt, width, height, api_key, model)
    else:
        print(f"Error: Unknown provider '{provider}'", file=sys.stderr)
        sys.exit(1)

    # Read actual dimensions from image header. Handles ratio remapping
    # (e.g. 1.91:1 request → Gemini generates 16:9 natively)
    actual = _actual_dimensions(image_bytes)
    if actual:
        width, height = actual

    return image_bytes, width, height


def _require_selection(provider: str | None, model: str | None) -> tuple[str, str]:
    """Return explicit provider/model identifiers or fail before credentials/network."""
    selected_provider = (provider or "").strip().lower()
    selected_model = (model or "").strip()
    if not selected_provider:
        raise ValueError(
            "Image provider is required; use --provider or ADS_IMAGE_PROVIDER "
            "after capability discovery"
        )
    if not selected_model:
        raise ValueError(
            "Image model is required; use --model or ADS_IMAGE_MODEL after "
            "capability discovery"
        )
    return selected_provider, selected_model


def run_batch(
    batch_file: str,
    output_dir: str,
    provider: str,
    model: str,
    api_key: str,
    as_json: bool,
    data_lifecycle: Mapping[str, Any],
) -> None:
    """
    Process a batch JSON file of generation jobs.

    Batch file format:
    [
        {"prompt": "...", "ratio": "9:16", "output": "tiktok-ad.png"},
        {"prompt": "...", "ratio": "1:1",  "output": "meta-square.png"}
    ]
    """
    provider, model = _require_selection(provider, model)
    validate_contract("data-lifecycle", data_lifecycle)
    with open(batch_file) as f:
        jobs = json.load(f)

    if len(jobs) > MAX_BATCH_SIZE:
        print(f"Error: Batch file contains {len(jobs)} jobs, max is {MAX_BATCH_SIZE}", file=sys.stderr)
        sys.exit(1)

    output_dir_path = resolve_output_path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)
    results = []

    for i, job in enumerate(jobs):
        prompt = job.get("prompt", "")
        ratio = job.get("ratio", "1:1")
        output_name = job.get("output", f"image_{i:03d}.png")
        # Security: strip path components to prevent directory traversal
        output_name = Path(output_name).name
        output_path = resolve_output_path(output_dir_path / output_name)
        reference_image = job.get("reference_image", None)
        if reference_image and Path(reference_image).suffix.lower() not in _ALLOWED_IMAGE_EXTENSIONS:
            print("  ⚠ Skipping reference image with an unsupported extension.", file=sys.stderr)
            reference_image = None

        result = {
            "index": i,
            **_prompt_record(prompt),
            "ratio": ratio,
            "file_locator": artifact_locator(output_path),
            "provider": provider,
            "model": model,
            "reference_image_sha256": None,
            "data_lifecycle": dict(data_lifecycle),
            "generation_success": False,
            "error": None,
        }

        try:
            print(f"[{i+1}/{len(jobs)}] Generating {output_name}...", file=sys.stderr)
            reference_sha256 = _reference_image_sha256(reference_image)
            image_bytes, width, height = generate_image(prompt, ratio, provider, model, api_key, reference_image)
            _write_private(output_path, image_bytes)
            result["reference_image_sha256"] = reference_sha256
            result["generation_success"] = True
            result["width"] = width
            result["height"] = height
            print(f"  ✓ Saved artifact {artifact_locator(output_path)} ({width}×{height})", file=sys.stderr)
        except Exception as e:
            result["error"] = "generation failed; inspect private ephemeral runtime logs"
            print(f"  ✗ Error: {_sanitize_error(e)}", file=sys.stderr)

        results.append(result)

    if as_json:
        print(json.dumps(results, indent=2))
    else:
        passed = sum(1 for r in results if r["generation_success"])
        print(f"\nBatch complete: {passed}/{len(results)} images generated")
        for r in results:
            status = "✓" if r["generation_success"] else "✗"
            print(f"  {status} {r['file_locator']}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate ad creative images using a pluggable image generation API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generate_image.py "approved creative prompt" --provider "$ADS_IMAGE_PROVIDER" --model "$ADS_IMAGE_MODEL" --ratio 16:9 --output ad.png
  python generate_image.py --batch prompts.json --provider "$ADS_IMAGE_PROVIDER" --model "$ADS_IMAGE_MODEL" --output-dir ./ad-assets/

Supported ratios: 1:1  9:16  16:9  4:5  4:3  3:4  1.91:1  4:1  21:9
Or use --size WxH for exact dimensions (e.g. --size 1200x628)

Both provider and model are required. Discover an approved capability first, then
use --provider/--model or ADS_IMAGE_PROVIDER/ADS_IMAGE_MODEL.
""",
    )

    # Prompt or batch mode
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("prompt", nargs="?", help="Image generation prompt")
    group.add_argument("--batch", "-b", metavar="FILE", help="Batch JSON file with multiple generation jobs")

    # Output options
    parser.add_argument("--output", "-o", metavar="FILE", help="Output file path (default: ad_[ratio].png)")
    parser.add_argument("--output-dir", metavar="DIR", default=".", help="Output directory for batch mode")

    # Dimension options
    dim_group = parser.add_mutually_exclusive_group()
    dim_group.add_argument(
        "--ratio", "-r",
        default="1:1",
        help="Aspect ratio shorthand (e.g. 9:16, 16:9, 4:5, 1:1). Default: 1:1",
    )
    dim_group.add_argument(
        "--size", "-s",
        metavar="WxH",
        help="Exact dimensions (e.g. 1200x628). Overrides --ratio.",
    )

    # Provider / model
    parser.add_argument(
        "--provider", "-p",
        default=None,
        help="Required provider adapter ID. Overrides ADS_IMAGE_PROVIDER.",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Required model ID from current capability evidence. Overrides ADS_IMAGE_MODEL.",
    )
    parser.add_argument(
        "--reference-image", "-i",
        metavar="FILE",
        dest="reference_image",
        help="Path to a rights-cleared brand reference image. Requires explicit "
             "support from the selected provider/model adapter.",
    )

    # Output format
    parser.add_argument("--json", "-j", action="store_true", help="Output result as JSON")
    parser.add_argument(
        "--data-lifecycle",
        required=True,
        help="Path to a valid data-lifecycle JSON contract for this generation run.",
    )

    args = parser.parse_args()

    # Resolve the explicitly selected, capability-verified provider and model.
    try:
        provider, model = _require_selection(
            args.provider or os.environ.get("ADS_IMAGE_PROVIDER"),
            args.model or os.environ.get("ADS_IMAGE_MODEL"),
        )
    except ValueError as exc:
        parser.error(str(exc))
    api_key = _get_api_key(provider)

    try:
        data_lifecycle = load_contract("data-lifecycle", args.data_lifecycle)
    except ContractError as exc:
        print(f"Error: {_sanitize_error(exc)}", file=sys.stderr)
        sys.exit(1)

    # Batch mode
    if args.batch:
        run_batch(args.batch, args.output_dir, provider, model, api_key, args.json, data_lifecycle)
        return

    # Single image mode
    ratio = args.size if args.size else args.ratio

    output_path = args.output
    if not output_path:
        safe_ratio = ratio.replace(":", "-").replace(".", "_")
        output_path = f"ad_{safe_ratio}.png"

    try:
        reference_sha256 = _reference_image_sha256(args.reference_image)
        image_bytes, width, height = generate_image(
            args.prompt, ratio, provider, model, api_key, args.reference_image
        )
    except Exception as e:
        print(f"Error: {_sanitize_error(e)}", file=sys.stderr)
        sys.exit(1)

    try:
        resolved_output = resolve_output_path(output_path, create_parent=True)
    except ValueError as exc:
        print(f"Error: {_sanitize_error(exc)}", file=sys.stderr)
        sys.exit(1)
    _write_private(resolved_output, image_bytes)

    result = {
        "success": True,
        "file_locator": artifact_locator(resolved_output),
        "provider": provider,
        "model": model,
        "width": width,
        "height": height,
        "ratio": ratio,
        **_prompt_record(args.prompt),
        "reference_image_sha256": reference_sha256,
        "data_lifecycle": data_lifecycle,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"✓ Generated {width}×{height} image → {artifact_locator(resolved_output)}")


if __name__ == "__main__":
    main()
