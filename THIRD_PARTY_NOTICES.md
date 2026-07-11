# Third-Party Notices

Claude Ads is MIT-licensed. Product names, APIs, documentation, public issues,
pull requests, and referenced repositories remain the property of their respective
owners and are not relicensed by this repository.

## Design and research provenance

- Brainstein is Apache-2.0. Claude Ads v2 uses an original domain-specific
  implementation of source, capability, maturity, orchestration, and release-gate
  ideas; no Brainstein source code is copied by the v2 control plane.
- Anthropic's public skill-creator material is Apache-2.0 at the skill level. It
  informed progressive disclosure, trigger evaluation, and deterministic helper
  guidance; Claude Ads prompts and implementation are original.
- Private Fable research is used only as non-redistributed design input. No raw or
  captured prompt, private corpus, close paraphrase, or restricted artifact is
  included.
- GitHub issues and pull requests are summarized and linked in the ecosystem
  disposition ledger. Text and patches are not copied without repository-license
  and contributor-attribution review.

## Runtime and development dependencies

Dependencies are installed from their publishers and retain their own licenses:

- Requests and urllib3: Apache-2.0.
- Playwright for Python: Apache-2.0.
- Cryptography: Apache-2.0 or BSD-3-Clause.
- Pillow: HPND-style license.
- ReportLab: BSD-style license.
- WeasyPrint: BSD-style license.
- Matplotlib: PSF-based license.
- PyYAML and pytest: MIT.

The release package must generate an SBOM and license inventory from the versions
actually resolved for that release. This notice is not a substitute for that
machine-generated inventory.

## Platform interfaces

Google, Meta, YouTube, LinkedIn, TikTok, Microsoft, Apple, Amazon, Reddit,
Pinterest, Snapchat, and X names and APIs are third-party products. Capability
references document interoperability and do not imply affiliation, endorsement,
or permission to bypass platform terms, access controls, policies, or review.
