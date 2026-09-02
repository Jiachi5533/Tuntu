from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from tuntu.models import ContentItem, RankingEvidence

from .attributes import normalize_jav_identity
from .errors import ProviderParseError
from .http import ProviderHttpClient


_CODE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-[0-9]{1,6}\b", re.IGNORECASE)
_TITLE = re.compile(
    r"\bTitle:\s*(.+?)(?=\s+(?:Genres|Studio|Idol\(s\)|Release Date):|$)",
    re.IGNORECASE,
)


def _classes(attributes) -> set[str]:
    value = next((value for key, value in attributes if key == "class"), "") or ""
    return set(value.split())


def _same_origin_url(base_url: str, candidate: str | None) -> str | None:
    if not candidate:
        return None
    resolved = urljoin(base_url, candidate.strip())
    base = urlsplit(base_url)
    target = urlsplit(resolved)
    if target.scheme not in {"http", "https"}:
        return None
    if (target.scheme.casefold(), target.netloc.casefold()) != (
        base.scheme.casefold(),
        base.netloc.casefold(),
    ):
        return None
    return resolved


class _WeeklyRankingParser(HTMLParser):
    def __init__(self, *, article_url: str):
        super().__init__(convert_charrefs=True)
        self.article_url = article_url
        self.has_container = False
        self.entries: list[dict[str, object]] = []
        self._entry: dict[str, object] | None = None
        self._entry_div_depth = 0
        self._capture_code = False

    def handle_starttag(self, tag, attrs):
        classes = _classes(attrs)
        attributes = dict(attrs)
        if tag == "div" and "entry-content" in classes:
            self.has_container = True
        if self._entry is None:
            if tag == "div" and "list-group-item" in classes:
                self._entry = {
                    "text": [],
                    "code": [],
                    "cover_url": None,
                    "source_url": None,
                }
                self._entry_div_depth = 1
            return
        if tag == "div":
            self._entry_div_depth += 1
        elif tag == "h5":
            self._capture_code = True
        elif tag == "img" and self._entry["cover_url"] is None:
            self._entry["cover_url"] = _same_origin_url(
                self.article_url,
                attributes.get("src") or attributes.get("data-src"),
            )
        elif tag == "a" and self._entry["source_url"] is None:
            href = attributes.get("href")
            if href and "/movies/" in href:
                self._entry["source_url"] = _same_origin_url(
                    self.article_url, href
                )

    def handle_data(self, data):
        if self._entry is None:
            return
        self._entry["text"].append(data)
        if self._capture_code:
            self._entry["code"].append(data)

    def handle_endtag(self, tag):
        if self._entry is None:
            return
        if tag == "h5":
            self._capture_code = False
        if tag != "div":
            return
        self._entry_div_depth -= 1
        if self._entry_div_depth:
            return
        text = " ".join(" ".join(self._entry["text"]).split())
        rank_match = re.match(r"(\d{1,3})\b", text)
        code_match = _CODE.search("".join(self._entry["code"]))
        title_match = _TITLE.search(text)
        if rank_match and code_match:
            self.entries.append(
                {
                    "rank": int(rank_match.group(1)),
                    "code": code_match.group(0),
                    "title": title_match.group(1).strip() if title_match else code_match.group(0),
                    "cover_url": self._entry["cover_url"],
                    "source_url": self._entry["source_url"],
                }
            )
        self._entry = None
        self._capture_code = False


class JavDatabaseRankingProvider:
    name = "javdatabase_weekly"
    kind = "discovery"

    def __init__(
        self,
        http: ProviderHttpClient,
        *,
        feed_url: str = "https://www.javdatabase.com/category/top-jav-movies/feed/",
    ):
        self.http = http
        self.feed_url = feed_url

    def collect(self, scope: str, *, run_id: str) -> list[ContentItem]:
        if scope != "weekly":
            raise ProviderParseError("unsupported_scope")
        response = self.http.get(self.feed_url, run_id=run_id)
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise ProviderParseError("invalid_xml") from exc
        channel = root.find("channel")
        if channel is None:
            raise ProviderParseError("structure_changed")
        entries = channel.findall("item")
        if not entries:
            return []
        latest = entries[0]
        article_url = _same_origin_url(
            self.feed_url, latest.findtext("link", default="")
        )
        if latest.findtext("link", default="").strip() and article_url is None:
            raise ProviderParseError("unsafe_article_url")
        if article_url:
            article = self.http.get(article_url, run_id=run_id)
            parser = _WeeklyRankingParser(article_url=article_url)
            parser.feed(article.text)
            if not parser.has_container or not parser.entries:
                raise ProviderParseError("structure_changed")
            return self._items_from_entries(
                parser.entries,
                scope=scope,
                article_url=article_url,
                ranking_title=(latest.findtext("title", default="") or "").strip(),
            )

        description = html.unescape(latest.findtext("description", default=""))
        raw_codes = list(dict.fromkeys(match.group(0) for match in _CODE.finditer(description)))
        if not raw_codes:
            raise ProviderParseError("structure_changed")
        return self._items_from_entries(
            [
                {
                    "rank": rank,
                    "code": raw_key,
                    "title": raw_key,
                    "cover_url": None,
                    "source_url": None,
                }
                for rank, raw_key in enumerate(raw_codes, start=1)
            ],
            scope=scope,
            article_url=None,
            ranking_title=(latest.findtext("title", default="") or "").strip(),
        )

    def _items_from_entries(
        self,
        entries: list[dict[str, object]],
        *,
        scope: str,
        article_url: str | None,
        ranking_title: str,
    ) -> list[ContentItem]:
        items = []
        seen = set()
        for entry in sorted(entries, key=lambda value: int(value["rank"])):
            raw_key = str(entry["code"])
            namespace, normalized_key = normalize_jav_identity(
                raw_key, fallback_namespace="javdatabase"
            )
            identity = (namespace, normalized_key.casefold())
            if identity in seen:
                continue
            seen.add(identity)
            items.append(
                ContentItem(
                    namespace=namespace,
                    raw_key=raw_key,
                    normalized_key=normalized_key,
                    rankings=[
                        RankingEvidence(
                            source=self.name,
                            rank=int(entry["rank"]),
                            raw_key=raw_key,
                            scope=scope,
                        )
                    ],
                    title=str(entry["title"]),
                    metadata={
                        key: value
                        for key, value in {
                            "cover_url": entry["cover_url"],
                            "source_url": entry["source_url"],
                            "ranking_page_url": article_url,
                            "ranking_title": ranking_title,
                        }.items()
                        if value
                    },
                )
            )
        return items
