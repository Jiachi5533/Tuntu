import unittest

from tuntu.models import CandidateEvidence, TruthValue
from tuntu.normalization import candidate_from_magnet
from tuntu.rules import RuleMode, RuleSet


def make_candidate(
    *,
    btih="1" * 40,
    title="Example",
    chinese_subtitles=TruthValue.UNKNOWN,
    uncensored=TruthValue.UNKNOWN,
    uhd=TruthValue.UNKNOWN,
    size_mb=None,
    seeders=None,
):
    return candidate_from_magnet(
        item_identity=("test", "item-1"),
        evidence=CandidateEvidence(
            source="fixture",
            magnet_uri=f"magnet:?xt=urn:btih:{btih}",
            title=title,
            chinese_subtitles=chinese_subtitles,
            uncensored=uncensored,
            uhd=uhd,
            size_mb=size_mb,
            seeders=seeders,
        ),
    )


class TriStateRuleTests(unittest.TestCase):
    def test_any_only_and_exclude_follow_the_three_state_truth_table(self):
        expectations = {
            RuleMode.ANY: {
                TruthValue.YES: True,
                TruthValue.NO: True,
                TruthValue.UNKNOWN: True,
            },
            RuleMode.ONLY: {
                TruthValue.YES: True,
                TruthValue.NO: False,
                TruthValue.UNKNOWN: False,
            },
            RuleMode.EXCLUDE: {
                TruthValue.YES: False,
                TruthValue.NO: True,
                TruthValue.UNKNOWN: True,
            },
        }

        for mode, values in expectations.items():
            for value, accepted in values.items():
                with self.subTest(mode=mode, value=value):
                    candidate = make_candidate(chinese_subtitles=value)
                    result = RuleSet(chinese_subtitles=mode).evaluate(candidate)
                    self.assertEqual(result.accepted, accepted)

    def test_conflicting_attribute_evidence_becomes_unknown(self):
        candidate = make_candidate(chinese_subtitles=TruthValue.YES)
        candidate.merge_from(
            candidate_from_magnet(
                item_identity=candidate.item_identity,
                evidence=CandidateEvidence(
                    source="conflicting-source",
                    magnet_uri=candidate.magnet_uri,
                    title="Example",
                    chinese_subtitles=TruthValue.NO,
                ),
            )
        )

        self.assertEqual(candidate.chinese_subtitles, TruthValue.UNKNOWN)
        self.assertEqual(len(candidate.evidence), 2)
        self.assertFalse(RuleSet(chinese_subtitles=RuleMode.ONLY).evaluate(candidate).accepted)

    def test_conflicting_size_evidence_is_not_silently_overwritten(self):
        candidate = make_candidate(size_mb=100)
        candidate.merge_from(
            candidate_from_magnet(
                item_identity=candidate.item_identity,
                evidence=CandidateEvidence(
                    source="conflicting-source",
                    magnet_uri=candidate.magnet_uri,
                    title="Example",
                    size_mb=200,
                ),
            )
        )

        self.assertIsNone(candidate.size_mb)
        result = RuleSet(max_size_mb=500).evaluate(candidate)
        self.assertEqual([reason.code for reason in result.reasons], ["size_unknown"])


class NumericAndKeywordRuleTests(unittest.TestCase):
    def test_default_rule_set_has_no_preferences(self):
        self.assertTrue(RuleSet().evaluate(make_candidate()).accepted)

    def test_enabled_numeric_rules_reject_unknown_values(self):
        result = RuleSet(min_size_mb=100, min_seeders=1).evaluate(make_candidate())

        self.assertEqual(
            {reason.code for reason in result.reasons},
            {"size_unknown", "seeders_unknown"},
        )

    def test_numeric_boundaries_are_inclusive(self):
        rules = RuleSet(min_size_mb=100, max_size_mb=200, min_seeders=3)

        self.assertTrue(rules.evaluate(make_candidate(size_mb=100, seeders=3)).accepted)
        self.assertTrue(rules.evaluate(make_candidate(size_mb=200, seeders=3)).accepted)

    def test_numeric_values_outside_bounds_keep_specific_reasons(self):
        too_small = RuleSet(min_size_mb=100, min_seeders=3).evaluate(
            make_candidate(size_mb=99, seeders=2)
        )
        too_large = RuleSet(max_size_mb=200).evaluate(make_candidate(size_mb=201))

        self.assertEqual(
            {reason.code for reason in too_small.reasons},
            {"size_below_min", "seeders_below_min"},
        )
        self.assertEqual([reason.code for reason in too_large.reasons], ["size_above_max"])

    def test_keywords_are_plain_case_insensitive_any_include_and_any_exclude(self):
        rules = RuleSet(
            include_keywords=("CHS", "subtitle"),
            exclude_keywords=("CAM", "sample"),
        )

        self.assertTrue(rules.evaluate(make_candidate(title="Movie chs release")).accepted)
        rejected = rules.evaluate(make_candidate(title="Movie SUBTITLE Sample"))
        self.assertEqual([reason.code for reason in rejected.reasons], ["keyword_excluded"])
        missing = rules.evaluate(make_candidate(title="Movie release"))
        self.assertEqual([reason.code for reason in missing.reasons], ["keyword_required"])

    def test_keeps_all_rejection_reasons(self):
        result = RuleSet(
            chinese_subtitles=RuleMode.ONLY,
            uncensored=RuleMode.ONLY,
            uhd=RuleMode.ONLY,
            min_size_mb=1,
            min_seeders=1,
            include_keywords=("required",),
            exclude_keywords=("blocked",),
        ).evaluate(make_candidate(title="blocked"))

        self.assertEqual(
            {reason.code for reason in result.reasons},
            {
                "chinese_subtitles_required",
                "uncensored_required",
                "uhd_required",
                "size_unknown",
                "seeders_unknown",
                "keyword_required",
                "keyword_excluded",
            },
        )


if __name__ == "__main__":
    unittest.main()
