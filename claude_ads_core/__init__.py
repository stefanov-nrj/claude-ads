"""Deterministic Claude Ads contracts and scoring engine."""

from .contracts import ContractError, load_contract, validate_contract
from .adapters import Adapter, AdapterCapabilities, GenericCSVExportAdapter, MutationDisabledError
from .models import AccountSnapshot, ControlDefinition, Finding, ReportBundle, RunManifest
from .reporting import (
    PDFDependencyError,
    ReportRenderError,
    atomic_write_report,
    render_html,
    render_markdown,
    render_pdf,
    render_report,
    resolve_report_path,
    write_report_bundle,
)
from .scoring import (
    CATEGORY_WEIGHT_TOTAL,
    SEVERITY_WEIGHTS,
    PortfolioResult,
    ScoreResult,
    ScoringError,
    score_account,
    score_portfolio,
)

__all__ = [
    "CATEGORY_WEIGHT_TOTAL",
    "SEVERITY_WEIGHTS",
    "AccountSnapshot",
    "Adapter",
    "AdapterCapabilities",
    "ContractError",
    "ControlDefinition",
    "Finding",
    "GenericCSVExportAdapter",
    "MutationDisabledError",
    "PDFDependencyError",
    "PortfolioResult",
    "ReportRenderError",
    "ReportBundle",
    "RunManifest",
    "ScoreResult",
    "ScoringError",
    "load_contract",
    "atomic_write_report",
    "render_html",
    "render_markdown",
    "render_pdf",
    "render_report",
    "resolve_report_path",
    "score_account",
    "score_portfolio",
    "validate_contract",
    "write_report_bundle",
]

__version__ = "2.0.0"
