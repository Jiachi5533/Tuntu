"""Tuntu core package."""

from .magnet import InvalidMagnet, Magnet, normalize_btih, parse_magnet
from .models import (
    Candidate,
    CandidateEvidence,
    ContentItem,
    ContentResult,
    ContentResultStatus,
    Evaluation,
    RankingEvidence,
    RuleReason,
    TruthValue,
)
from .normalization import candidate_from_magnet
from .pipeline import Pipeline
from .rules import RuleMode, RuleSet

__all__ = [
    "Candidate",
    "CandidateEvidence",
    "ContentItem",
    "ContentResult",
    "ContentResultStatus",
    "Evaluation",
    "InvalidMagnet",
    "Magnet",
    "Pipeline",
    "RankingEvidence",
    "RuleMode",
    "RuleReason",
    "RuleSet",
    "TruthValue",
    "candidate_from_magnet",
    "normalize_btih",
    "parse_magnet",
]
__version__ = "0.1.0"
