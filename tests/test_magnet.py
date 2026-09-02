import base64
import unittest

from tuntu.magnet import InvalidMagnet, normalize_btih, parse_magnet
from tuntu.models import ContentItem, RankingEvidence


class MagnetTests(unittest.TestCase):
    def test_normalizes_hex_and_base32_btih_to_the_same_identity(self):
        expected = "0123456789abcdef0123456789abcdef01234567"
        base32 = base64.b32encode(bytes.fromhex(expected)).decode("ascii")

        self.assertEqual(normalize_btih(expected.upper()), expected)
        self.assertEqual(normalize_btih(base32.lower()), expected)
        self.assertEqual(
            parse_magnet(f"magnet:?dn=Example&xt=urn:btih:{base32}").btih,
            expected,
        )

    def test_rejects_magnets_without_one_unambiguous_v1_btih(self):
        invalid_values = (
            "https://example.test/file",
            "magnet:?dn=no-hash",
            "magnet:?xt=urn:btmh:1220" + "ab" * 32,
            "magnet:?xt=urn:btih:not-a-hash",
            "magnet:?xt=urn:btih:" + "a" * 40 + "&xt=urn:btih:" + "b" * 40,
        )

        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(InvalidMagnet):
                parse_magnet(value)

    def test_btih_identity_ignores_display_name_trackers_and_parameter_order(self):
        btih = "a" * 40
        first = parse_magnet(
            f"magnet:?xt=urn:btih:{btih}&dn=First&tr=udp%3A%2F%2Ftracker.test"
        )
        second = parse_magnet(
            f"magnet:?tr=https%3A%2F%2Fother.test&dn=Second&xt=urn:btih:{btih.upper()}"
        )

        self.assertEqual(first.btih, second.btih)

    def test_canonical_uri_preserves_transport_hints_and_normalizes_v1_btih(self):
        btih = "0123456789abcdef0123456789abcdef01234567"
        base32 = base64.b32encode(bytes.fromhex(btih)).decode("ascii")
        magnet = parse_magnet(
            "magnet:?"
            f"xt=urn:btih:{base32}&"
            "dn=Public+domain+fixture&"
            "tr=udp%3A%2F%2Ftracker-one.test%3A80&"
            "tr=https%3A%2F%2Ftracker-two.test%2Fannounce&"
            "ws=https%3A%2F%2Fcdn.test%2Ffixture.txt&"
            "xs=https%3A%2F%2Fcdn.test%2Ffixture.torrent&"
            f"xt=urn:btih:{btih.upper()}&"
            "xt=urn:btmh:1220" + "ab" * 32
        )

        canonical = magnet.canonical_uri
        self.assertEqual(canonical.count("xt=urn:btih:"), 1)
        self.assertIn(f"xt=urn:btih:{btih}", canonical)
        self.assertIn("dn=Public+domain+fixture", canonical)
        self.assertEqual(canonical.count("tr="), 2)
        self.assertIn("ws=https%3A%2F%2Fcdn.test%2Ffixture.txt", canonical)
        self.assertIn("xs=https%3A%2F%2Fcdn.test%2Ffixture.torrent", canonical)
        self.assertIn("xt=urn%3Abtmh%3A1220", canonical)

    def test_transport_hints_survive_reparsing(self):
        btih = "b" * 40
        original = parse_magnet(
            f"magnet:?xt=urn:btih:{btih}&dn=Example&ws=https%3A%2F%2Fcdn.test%2Ffile"
        )

        reparsed = parse_magnet(original.canonical_uri)

        self.assertEqual(reparsed.btih, btih)
        self.assertEqual(reparsed.canonical_uri, original.canonical_uri)


class ContentIdentityTests(unittest.TestCase):
    def test_merges_rankings_without_losing_source_rank_or_raw_key(self):
        item = ContentItem(
            namespace="jav",
            raw_key="ABC-1",
            normalized_key="ABC-001",
            rankings=[RankingEvidence(source="weekly-a", rank=8, raw_key="ABC-1")],
        )
        duplicate = ContentItem(
            namespace="JAV",
            raw_key="abc001",
            normalized_key="abc-001",
            rankings=[RankingEvidence(source="weekly-b", rank=2, raw_key="abc001")],
        )

        item.merge_from(duplicate)

        self.assertEqual(item.identity, ("jav", "abc-001"))
        self.assertEqual(item.best_rank, 2)
        self.assertEqual(
            {(entry.source, entry.rank, entry.raw_key) for entry in item.rankings},
            {("weekly-a", 8, "ABC-1"), ("weekly-b", 2, "abc001")},
        )


if __name__ == "__main__":
    unittest.main()
