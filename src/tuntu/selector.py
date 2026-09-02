from __future__ import annotations

from .models import Candidate, Evaluation


def candidate_sort_key(candidate: Candidate) -> tuple[object, ...]:
    return (
        0 if candidate.seeders is not None else 1,
        -(candidate.seeders or 0),
        0 if candidate.size_mb is not None else 1,
        candidate.size_mb or 0,
        candidate.btih,
        candidate.sources,
    )


def evaluation_sort_key(evaluation: Evaluation) -> tuple[object, ...]:
    return (0 if evaluation.accepted else 1, *candidate_sort_key(evaluation.candidate))


def select_candidate(evaluations: list[Evaluation]) -> Candidate | None:
    return next(
        (evaluation.candidate for evaluation in evaluations if evaluation.accepted),
        None,
    )
