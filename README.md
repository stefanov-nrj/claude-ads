<p align="center">
  <img src="assets/banner.svg" alt="Claude Ads" width="100%">
</p>

# Claude Ads

Claude-first, portable paid-media operations for professional agencies,
consultants, and in-house performance teams.

Claude Ads turns authorized account exports or reads into source-grounded audits,
plans, experiments, creative workflows, monitoring, and client reports. Versioned
JSON is the system of record; Markdown and HTML are deterministic renderings, and
PDF is rendered from the same HTML through the declared WeasyPrint dependency.
Live account changes are disabled by default and released per platform only after
approval, idempotency, verification, audit, and rollback pass.

## Platforms

The v2 contract treats these as first-class surfaces:

| Search, video, and social | Commerce and retail media |
| --- | --- |
| Google Ads | Apple Ads |
| Meta Ads | Amazon Ads |
| YouTube Ads | Pinterest Ads |
| LinkedIn Ads |  |
| TikTok Ads |  |
| Microsoft Advertising |  |
| Reddit Ads |  |
| Snapchat Ads |  |
| X Ads |  |

Each platform has a focused skill, audit worker, control reference, capability
declaration, and testable routing surface. The machine-readable
`control-plane/manifests/capability-manifest.json` is authoritative when a live
read or write integration differs by platform.

## What changed in v2

- Category-first deterministic scoring replaces duplicated prompt math.
- Health, evidence coverage, regulatory exposure, and opportunities are separate.
- Twelve platform workers return one versioned finding contract.
- Missing evidence yields provisional or insufficient-evidence results instead of
  invented certainty.
- Source, claim, capability, safety, maturity, and ecosystem-disposition manifests
  gate product claims.
- HTTP access pins validated DNS answers through the connection boundary. Browser
  dispatch fails closed unless an external OS/container egress sandbox is attested.
- Installations use a managed environment and ownership manifest; uninstall never
  deletes unrelated `ads-*` skills.
- Account mutations follow draft → approve → apply → verify → audit → rollback.
- Raw private research, captured prompts, credentials, and client exports stay out
  of Git and release packages.

## Commands

An installed standalone skill uses the canonical `/ads` command. Claude Code
plugins are always namespaced, so a marketplace or `--plugin-dir` installation
uses `/claude-ads:ads`. Both surfaces load the same `ads/SKILL.md` contract.

| Command | Outcome |
| --- | --- |
| `/ads setup` | Create the client, account, KPI, privacy, and guardrail profile |
| `/ads audit [all\|platform\|scope]` | Run a complete or scoped evidence-backed audit |
| `/ads plan` | Build channel, campaign, budget, competitor, and measurement plans |
| `/ads create` | Produce copy, image, video, or product-photo assets |
| `/ads launch --draft` | Produce a campaign mutation plan without changing the account |
| `/ads monitor` | Review pacing, delivery, tracking, fatigue, policy, and performance |
| `/ads optimize --draft` | Produce evidence-backed optimization changes |
| `/ads experiment` | Design or read out a controlled test |
| `/ads report` | Render a validated JSON run bundle |
| `/ads research refresh` | Refresh platform, policy, API, benchmark, and ecosystem evidence |
| `/ads validate` | Validate contracts, runs, capabilities, maturity, or release readiness |
| `/ads status`, `/ads next` | Show current status and the highest-priority blocker |

Platform shortcuts such as `/ads google`, `/ads meta`, `/ads amazon`, and
`/ads reddit` route to the corresponding platform audit.

## Installation

Claude Code is canonical. Codex, Gemini, Cursor, Windsurf, Goose, and portable
Agent Skills layouts are supported where their runtime can consume the same files.

Prefer your host's plugin installation flow or a tagged release archive whose
SHA-256 checksum you verified. With the Claude Code plugin flow, invoke
`/claude-ads:ads`; the managed installer below creates the standalone `/ads`
surface. Never pipe a remote installer directly to a shell.

From an authenticated local checkout:

```bash
git clone https://github.com/AI-Marketing-Hub/claude-ads.git
cd claude-ads
bash install.sh --source=local
```

Select another host explicitly:

```bash
bash install.sh --target=codex --source=local
bash install.sh --target=gemini --source=local --no-deps
```

The installer:

- Detects the source checkout instead of downloading an unrelated mirror.
- Copies the main skill, sub-skills, references, interface metadata, agents, and
  helper scripts.
- Installs deterministic core tooling and optional runtime dependencies into a
  managed virtual environment for supported hosts.
- Records every owned file and directory.

Uninstall only manifest-owned files:

```bash
bash uninstall.sh --target=claude
```

PowerShell equivalents are `install.ps1` and `uninstall.ps1`.

## Operating model

```text
Exports / APIs / MCP
        ↓
sanitize + normalize
        ↓
AccountSnapshot + RunManifest
        ↓
bounded platform and cross-platform workers
        ↓
schema-valid Findings
        ↓
deterministic scoring + ReportBundle
        ↓
Markdown / HTML / PDF
        ↓ optional
MutationPlan → approve → apply → verify → rollback/log
```

Workers analyze bounded scopes and return JSON. One conductor owns aggregation and
final artifacts. Required-worker failure makes the bundle partial; it is never
silently presented as a complete audit.

## Scoring

Controls use `pass`, `fail`, `unknown`, or `not_applicable`.

- Critical, high, medium, and informational severity weights are `5`, `3`, `1`,
  and `0`.
- Controls are scored inside their category before category weights are applied.
- Unknown controls lower evidence coverage without silently lowering or raising
  known health.
- At least 80% weighted coverage is required for a normal score; 60–79% is
  provisional; below 60% is insufficient evidence.
- Optional, beta, unavailable, premium, or ineligible features are unscored
  opportunities.

See `ads/references/scoring-system.md` and the production implementation in
`claude_ads_core/scoring.py`.

## Account safety

All adapters are read-only by default. Applying a change requires:

1. A tested, enabled capability for that exact operation.
2. Explicit account and object IDs.
3. A human-readable before/after diff and blast radius.
4. Approval from the configured owner within account-defined ceilings.
5. An idempotency key, audit destination, rollback, and verification window.
6. Verification that remote state still matches the mutation precondition.

Missing ceilings mean no write. Permanent deletion is not supported in v2.

## Evidence and control plane

The public-safe `control-plane/` records:

- Product and publishing boundaries.
- Dated source and claim ledgers.
- Truthful platform capabilities.
- Safety and privacy requirements.
- Multi-agent orchestration rules.
- Issue, pull-request, fork, and repository dispositions.
- Maturity and release gates.

Maturity progresses through `inventory-baselined`, `source-grounded`,
`domain-integrated`, `eval-verified`, and `release-ready`. Stale load-bearing
sources, failed security checks, missing capability tests, or unavailable required
remote CI demote the current state.

## Development

Create a virtual environment and run the complete suite:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e . -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Useful focused checks:

```bash
python -m claude_ads_core --version
python -m claude_ads_core validate finding path/to/finding.json
bash -n install.sh uninstall.sh
```

Release readiness additionally requires source freshness, package scanning,
cross-platform installation tests, remote CI, and fresh-context verification.

## Repository map

```text
ads/                  main skill, interface metadata, and shared references
skills/               platform and lifecycle skills
agents/               platform, cross-platform, research, and verifier workers
claude_ads_core/      typed contracts, adapters, validation, and scoring
control-plane/        source, claim, capability, safety, maturity, and release state
scripts/              browser, landing-page, creative, and reporting helpers
evals/                routing and behavioral evaluation cases
tests/                deterministic, routing, security, installer, and adapter tests
```

## Privacy and publication

This repository remains private until the owner approves a separate public-release
gate. Client data, raw private research, captured prompts, credentials, account
exports, and agent transcripts must never enter Git history or release archives.

## License

Original Claude Ads code and documentation are available under the MIT License.
Third-party APIs, trademarks, documentation, and cited artifacts remain subject to
their respective terms. See the control-plane source ledger and third-party notices
before importing external material.
