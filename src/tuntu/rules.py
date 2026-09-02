from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import Candidate, Evaluation, RuleReason, TruthValue


class RuleMode(StrEnum):
    ANY = "any"
    ONLY = "only"
    EXCLUDE = "exclude"


@dataclass(frozen=True, slots=True)
class RuleSet:
    chinese_subtitles: RuleMode = RuleMode.ANY
    uncensored: RuleMode = RuleMode.ANY
    uhd: RuleMode = RuleMode.ANY
    min_size_mb: float | None = None
    max_size_mb: float | None = None
    min_seeders: int | None = None
    include_keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("chinese_subtitles", "uncensored", "uhd"):
            if not isinstance(getattr(self, field_name), RuleMode):
                raise ValueError(f"{field_name} must be a RuleMode")
        if self.min_size_mb is not None and self.min_size_mb < 0:
            raise ValueError("min_size_mb cannot be negative")
        if self.max_size_mb is not None and self.max_size_mb < 0:
            raise ValueError("max_size_mb cannot be negative")
        if (
            self.min_size_mb is not None
            and self.max_size_mb is not None
            and self.min_size_mb > self.max_size_mb
        ):
            raise ValueError("min_size_mb cannot exceed max_size_mb")
        if self.min_seeders is not None and self.min_seeders < 0:
            raise ValueError("min_seeders cannot be negative")

    def evaluate(self, candidate: Candidate) -> Evaluation:
        reasons: list[RuleReason] = []
        self._evaluate_truth(
            reasons,
            field="chinese_subtitles",
            label="中文字幕",
            mode=self.chinese_subtitles,
            value=candidate.chinese_subtitles,
        )
        self._evaluate_truth(
            reasons,
            field="uncensored",
            label="无码",
            mode=self.uncensored,
            value=candidate.uncensored,
        )
        self._evaluate_truth(
            reasons,
            field="uhd",
            label="UHD",
            mode=self.uhd,
            value=candidate.uhd,
        )

        if self.min_size_mb is not None or self.max_size_mb is not None:
            if candidate.size_mb is None:
                reasons.append(RuleReason("size_unknown", "体积未知，无法证明满足体积规则"))
            else:
                if self.min_size_mb is not None and candidate.size_mb < self.min_size_mb:
                    reasons.append(RuleReason("size_below_min", "体积低于最小值"))
                if self.max_size_mb is not None and candidate.size_mb > self.max_size_mb:
                    reasons.append(RuleReason("size_above_max", "体积高于最大值"))

        if self.min_seeders is not None:
            if candidate.seeders is None:
                reasons.append(RuleReason("seeders_unknown", "做种数未知，无法证明满足规则"))
            elif candidate.seeders < self.min_seeders:
                reasons.append(RuleReason("seeders_below_min", "做种数低于最小值"))

        search_text = candidate.search_text.casefold()
        include_keywords = tuple(value.strip() for value in self.include_keywords if value.strip())
        exclude_keywords = tuple(value.strip() for value in self.exclude_keywords if value.strip())
        if include_keywords and not any(value.casefold() in search_text for value in include_keywords):
            reasons.append(RuleReason("keyword_required", "标题未命中任一必须包含关键词"))
        matched_exclude = next(
            (value for value in exclude_keywords if value.casefold() in search_text), None
        )
        if matched_exclude is not None:
            reasons.append(RuleReason("keyword_excluded", f"标题命中排除关键词：{matched_exclude}"))

        return Evaluation(candidate=candidate, accepted=not reasons, reasons=reasons)

    @staticmethod
    def _evaluate_truth(
        reasons: list[RuleReason],
        *,
        field: str,
        label: str,
        mode: RuleMode,
        value: TruthValue,
    ) -> None:
        if mode is RuleMode.ONLY and value is not TruthValue.YES:
            reasons.append(RuleReason(f"{field}_required", f"仅接受已确认的{label}候选"))
        elif mode is RuleMode.EXCLUDE and value is TruthValue.YES:
            reasons.append(RuleReason(f"{field}_excluded", f"排除已确认的{label}候选"))
