import unittest

from scripts.probe_sources import (
    summarize_bitsearch,
    summarize_javdatabase_feed,
    summarize_javdb_detail,
    summarize_javdb_ranking,
    summarize_knaben,
    summarize_sukebei_feed,
)


class SourceProbeSummaryTests(unittest.TestCase):
    def test_summarizes_javdb_without_exposing_titles_or_links(self):
        ranking = b'''
        <a href="/v/alpha">One</a>
        <a href="/v/alpha?preview=1">Duplicate</a>
        <a href="/v/beta">Two</a>
        '''
        detail = b'''
        <div class="item columns is-desktop odd magnet-name">
          <a href="magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA">Release</a>
        </div>
        '''

        self.assertEqual(
            summarize_javdb_ranking(ranking),
            {"unique_detail_links": 2, "challenge_markers": 0},
        )
        self.assertEqual(
            summarize_javdb_detail(detail),
            {"magnet_uris": 1, "candidate_rows": 1},
        )

    def test_summarizes_rss_shapes(self):
        javdatabase = b'''
        <rss><channel><item><title>ABC-123 weekly</title></item></channel></rss>
        '''
        sukebei = b'''
        <rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa">
          <channel><item><nyaa:infoHash>hash</nyaa:infoHash><nyaa:seeders>4</nyaa:seeders></item></channel>
        </rss>
        '''

        self.assertEqual(
            summarize_javdatabase_feed(javdatabase),
            {"items": 1, "unique_code_shapes": 1},
        )
        self.assertEqual(
            summarize_sukebei_feed(sukebei),
            {"items": 1, "infohash_fields": 1, "seeder_fields": 1},
        )

    def test_summarizes_json_apis_with_schema_only(self):
        knaben = {"hits": [{"title": "hidden", "hash": "hidden", "seeders": 4}], "total": 1}
        bitsearch = {"success": True, "results": [], "pagination": {"total": 0}}

        self.assertEqual(
            summarize_knaben(knaben),
            {
                "root_keys": ["hits", "total"],
                "results": 1,
                "result_keys": ["hash", "seeders", "title"],
            },
        )
        self.assertEqual(
            summarize_bitsearch(bitsearch),
            {
                "root_keys": ["pagination", "results", "success"],
                "success": True,
                "results": 0,
                "result_keys": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
