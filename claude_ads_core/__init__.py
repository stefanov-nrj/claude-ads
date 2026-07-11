"""Deterministic Claude Ads contracts and scoring engine."""

from .contracts import ContractError, load_contract, validate_contract
from .adapters import Adapter, AdapterCapabilities, GenericCSVExportAdapter, MutationDisabledError
from .models import AccountSnapshot, ControlDefinition, Finding, ReportBundle, RunManifest
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
    "PortfolioResult",
    "ReportBundle",
    "RunManifest",
    "ScoreResult",
    "ScoringError",
    "load_contract",
    "score_account",
    "score_portfolio",
    "validate_contract",
]

__version__ = "2.0.0a1"
