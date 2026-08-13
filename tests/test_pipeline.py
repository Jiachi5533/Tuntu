import unittest

from rankarr.models import Candidate, RankedItem
from rankarr.pipeline import Pipeline
from rankarr.rules import MinSeeders, SizeRange, TagPolicy


class FakeRankingSource:
    def __init__(self, name, items):
        self.name = name
        self.items = items

    def fetch(self, period):
        return self.items


class FakeCandidateSource:
    def __init__(self, name, items):
        self.name = name
        self.items = items

    def search(self, item):
        return [candidate for candidate in self.items if candidate.item_key == item.key]


class PipelineTests(unittest.TestCase):
    def test_deduplicates_rankings_and_candidates(self):
        rankings = [
            FakeRankingSource("one", [RankedItem("MOVIE-1", 2, ["one"])]),
            FakeRankingSource("two", [RankedItem("movie-1", 1, ["two"])]),
        ]
        candidates = [
            FakeCandidateSource(
                "a",
                [Candidate("HASH-1", "MOVIE-1", "Movie 1080p", "magnet:a", ["a"], 1000, 3)],
            ),
            FakeCandidateSource(
                "b",
                [Candidate("hash-1", "MOVIE-1", "Movie 1080p Chinese", "magnet:b", ["b"], 1200, 9, {"chinese"})],
            ),
        ]

        ranked, evaluated = Pipeline(rankings, candidates).discover("weekly")

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].rank, 1)
        self.assertEqual(ranked[0].sources, ["one", "two"])
        self.assertEqual(len(evaluated), 1)
        self.assertEqual(evaluated[0].candidate.sources, ["a", "b"])
        self.assertEqual(evaluated[0].candidate.seeders, 9)
        self.assertEqual(evaluated[0].candidate.tags, {"chinese"})

    def test_applies_rules_and_keeps_rejection_reasons(self):
        ranking = FakeRankingSource("rank", [RankedItem("MOVIE-1", 1, ["rank"])])
        candidates = FakeCandidateSource(
            "search",
            [
                Candidate("good", "MOVIE-1", "Movie", "magnet:good", ["search"], 1500, 10, {"hd"}),
                Candidate("bad", "MOVIE-1", "Movie", "magnet:bad", ["search"], 500, 0, {"cam"}),
            ],
        )
        rules = [MinSeeders(1), SizeRange(min_mb=800, max_mb=3000), TagPolicy(exclude_any={"cam"})]

        _, evaluated = Pipeline([ranking], [candidates], rules).discover("weekly")

        self.assertTrue(evaluated[0].accepted)
        self.assertFalse(evaluated[1].accepted)
        self.assertEqual(len(evaluated[1].reasons), 3)


if __name__ == "__main__":
    unittest.main()

