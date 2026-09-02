from __future__ import annotations

from .magnet import parse_magnet
from .models import Candidate, CandidateEvidence


def candidate_from_magnet(
    *, item_identity: tuple[str, str], evidence: CandidateEvidence
) -> Candidate:
    parsed = parse_magnet(evidence.magnet_uri)
    normalized_item_identity = tuple(part.strip().casefold() for part in item_identity)
    return Candidate(
        item_identity=normalized_item_identity,
        btih=parsed.btih,
        evidence=[evidence],
    )
