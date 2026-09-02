import unittest

from tuntu.models import CandidateEvidence, ContentItem, ContentResultStatus, RankingEvidence
from tuntu.normalization import candidate_from_magnet
from tuntu.pipeline import Pipeline
from tuntu.rules import RuleSet
from tuntu.selector import candidate_sort_key


class FakeDiscoverySource:
    def __init__(self, name, items):
        self.name = name
        self.items = items

    def collect(self, scope, *, run_id):
        return self.items


class FakeCandidateSource:
    def __init__(self, name, candidates):
        self.name = name
        self.candidates = candidates

    def search(self, item, *, run_id):
        return [candidate for candidate in self.candidates if candidate.item_identity == item.identity]


def make_item(key="item-1", source="rank", rank=1):
    return ContentItem(
        namespace="test",
        raw_key=key,
        normalized_key=key,
        rankings=[RankingEvidence(source=source, rank=rank, raw_key=key)],
    )


def make_candidate(item, btih, source, *, size_mb=None, seeders=None, title="Example", tracker=""):
    tracker_query = f"&tr={tracker}" if tracker else ""
    return candidate_from_magnet(
        item_identity=item.identity,
        evidence=CandidateEvidence(
            source=source,
            magnet_uri=f"magnet:?xt=urn:btih:{btih}{tracker_query}",
            title=title,
            size_mb=size_mb,
            seeders=seeders,
        ),
    )


class SelectorTests(unittest.TestCase):
    def test_sort_order_is_known_seeders_desc_then_known_size_asc_then_stable_identity(self):
        item = make_item()
        candidates = [
            make_candidate(item, "4" * 40, "z", size_mb=100, seeders=None),
            make_candidate(item, "3" * 40, "z", size_mb=None, seeders=5),
            make_candidate(item, "2" * 40, "z", size_mb=200, seeders=5),
            make_candidate(item, "1" * 40, "z", size_mb=100, seeders=5),
        ]

        ordered = sorted(candidates, key=candidate_sort_key)

        self.assertEqual([candidate.btih[0] for candidate in ordered], ["1", "2", "3", "4"])

    def test_pipeline_merges_rankings_and_same_btih_evidence_then_selects_only_top_one(self):
        first_item = make_item("ITEM-1", "weekly-a", 8)
        duplicate_item = ContentItem(
            namespace="TEST",
            raw_key="item-1",
            normalized_key="item-1",
            rankings=[RankingEvidence(source="weekly-b", rank=2, raw_key="item-1")],
        )
        first = make_candidate(
            first_item,
            "a" * 40,
            "source-a",
            size_mb=200,
            seeders=8,
            tracker="udp%3A%2F%2Fone.test",
        )
        duplicate = make_candidate(
            first_item,
            "a" * 40,
            "source-b",
            size_mb=200,
            seeders=9,
            tracker="https%3A%2F%2Ftwo.test",
        )
        alternative = make_candidate(first_item, "b" * 40, "source-c", size_mb=100, seeders=1)
        pipeline = Pipeline(
            discovery_sources=[
                FakeDiscoverySource("weekly-a", [first_item]),
                FakeDiscoverySource("weekly-b", [duplicate_item]),
            ],
            candidate_sources=[
                FakeCandidateSource("source-a", [first]),
                FakeCandidateSource("source-b", [duplicate]),
                FakeCandidateSource("source-c", [alternative]),
            ],
        )

        results = pipeline.discover("weekly")

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.item.best_rank, 2)
        self.assertEqual(result.status, ContentResultStatus.SELECTED)
        self.assertEqual(result.selected.btih, "a" * 40)
        self.assertEqual(result.selected.sources, ("source-a", "source-b"))
        self.assertEqual(result.selected.seeders, 9)
        self.assertEqual(len(result.evaluations), 2)

    def test_no_candidate_and_filtered_are_normal_content_results(self):
        without_candidate = make_item("none")
        filtered_item = make_item("filtered")
        filtered_candidate = make_candidate(
            filtered_item, "f" * 40, "source", size_mb=None, seeders=None
        )
        pipeline = Pipeline(
            discovery_sources=[FakeDiscoverySource("rank", [without_candidate, filtered_item])],
            candidate_sources=[FakeCandidateSource("source", [filtered_candidate])],
            rules=RuleSet(min_seeders=1),
        )

        results = pipeline.discover("weekly")

        self.assertEqual(
            {result.item.normalized_key: result.status for result in results},
            {
                "none": ContentResultStatus.NO_CANDIDATE,
                "filtered": ContentResultStatus.FILTERED,
            },
        )
        self.assertTrue(all(result.selected is None for result in results))


if __name__ == "__main__":
    unittest.main()
