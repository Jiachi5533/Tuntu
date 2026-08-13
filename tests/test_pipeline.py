import unittest

from tuntu.models import Candidate, ContentItem, DownloadReceipt, TransferKind
from tuntu.pipeline import Pipeline
from tuntu.routing import Route
from tuntu.rules import MinSeeders, SizeRange, TagPolicy


class FakeDiscoverySource:
    def __init__(self, name, items):
        self.name = name
        self.items = items

    def collect(self, scope):
        return self.items


class FakeCandidateSource:
    def __init__(self, name, items):
        self.name = name
        self.items = items

    def search(self, item):
        return [candidate for candidate in self.items if candidate.item_key == item.key]


class PipelineTests(unittest.TestCase):
    def test_deduplicates_rankings_and_candidates(self):
        discoveries = [
            FakeDiscoverySource("one", [ContentItem("MOVIE-1", 2, ["one"])]),
            FakeDiscoverySource("two", [ContentItem("movie-1", 1, ["two"])]),
        ]
        candidates = [
            FakeCandidateSource(
                "a",
                [
                    Candidate(
                        "HASH-1", "MOVIE-1", "Movie 1080p", "magnet:a",
                        TransferKind.MAGNET, ["a"], 1000, 3,
                    )
                ],
            ),
            FakeCandidateSource(
                "b",
                [
                    Candidate(
                        "hash-1", "MOVIE-1", "Movie 1080p Chinese", "magnet:b",
                        TransferKind.MAGNET, ["b"], 1200, 9, {"chinese"},
                    )
                ],
            ),
        ]

        ranked, evaluated = Pipeline(discoveries, candidates).discover("weekly")

        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].priority, 1)
        self.assertEqual(ranked[0].sources, ["one", "two"])
        self.assertEqual(len(evaluated), 1)
        self.assertEqual(evaluated[0].candidate.sources, ["a", "b"])
        self.assertEqual(evaluated[0].candidate.seeders, 9)
        self.assertEqual(evaluated[0].candidate.tags, {"chinese"})

    def test_applies_rules_and_keeps_rejection_reasons(self):
        ranking = FakeDiscoverySource("rank", [ContentItem("MOVIE-1", 1, ["rank"])])
        candidates = FakeCandidateSource(
            "search",
            [
                Candidate(
                    "good", "MOVIE-1", "Movie", "magnet:good",
                    TransferKind.MAGNET, ["search"], 1500, 10, {"hd"},
                ),
                Candidate(
                    "bad", "MOVIE-1", "Movie", "magnet:bad",
                    TransferKind.MAGNET, ["search"], 500, 0, {"cam"},
                ),
            ],
        )
        rules = [MinSeeders(1), SizeRange(min_mb=800, max_mb=3000), TagPolicy(exclude_any={"cam"})]

        _, evaluated = Pipeline([ranking], [candidates], rules).discover("weekly")

        self.assertTrue(evaluated[0].accepted)
        self.assertFalse(evaluated[1].accepted)
        self.assertEqual(len(evaluated[1].reasons), 3)

    def test_routes_public_magnets_and_private_torrents_to_different_clients(self):
        class FakeClient:
            def __init__(self, name):
                self.name = name

            def submit(self, candidate, destination):
                return DownloadReceipt(candidate.identity, "submitted", self.name, destination)

        cloud = Route(
            "public-cloud",
            FakeClient("clouddrive2"),
            "/115open/tuntu",
            transfer_kinds={TransferKind.MAGNET},
            exclude_tags={"private"},
        )
        local = Route(
            "private-local",
            FakeClient("qbittorrent"),
            "/downloads/pt",
            require_tags={"private"},
        )
        candidates = [
            Candidate("public", "A", "A", "magnet:public", TransferKind.MAGNET, ["dht"]),
            Candidate(
                "private", "B", "B", "https://tracker/file.torrent",
                TransferKind.TORRENT, ["pt"], tags={"private"},
            ),
        ]

        receipts = Pipeline([], [], routes=[cloud, local]).submit_candidates(candidates)

        self.assertEqual([receipt.external_id for receipt in receipts], ["clouddrive2", "qbittorrent"])


if __name__ == "__main__":
    unittest.main()
