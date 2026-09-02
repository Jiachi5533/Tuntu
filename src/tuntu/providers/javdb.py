from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

from tuntu.magnet import InvalidMagnet
from tuntu.models import CandidateEvidence, ContentItem, RankingEvidence
from tuntu.normalization import candidate_from_magnet

from .attributes import classify_attributes, normalize_jav_identity, parse_size_mb
from .errors import ProviderParseError
from .http import ProviderHttpClient


def _classes(attributes) -> set[str]:
    value = next((value for key, value in attributes if key == "class"), "") or ""
    return set(value.split())


def _page_error_code(text: str) -> str:
    marker_text = text.casefold()
    if any(
        marker in marker_text
        for marker in (
            "captcha",
            "cloudflare",
            "verify you are human",
            "just a moment",
            "cf-chl-",
        )
    ):
        return "access_challenge"
    if any(
        marker in marker_text
        for marker in (
            'action="/user_sessions"',
            "action='/user_sessions'",
            'name="password"',
        )
    ):
        return "authentication_required"
    return "structure_changed"


def _session_headers(
    *, base_url: str, cookie: str | None, user_agent: str | None
) -> dict[str, str]:
    headers = {
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": base_url.rstrip("/") + "/",
    }
    if cookie:
        headers["Cookie"] = cookie
    if user_agent:
        headers["User-Agent"] = user_agent
    return headers


class _RankingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.has_container = False
        self.entries: list[tuple[str, str, str, str]] = []
        self._entry: dict[str, str] | None = None
        self._capture_code = False

    def handle_starttag(self, tag, attrs):
        classes = _classes(attrs)
        attributes = dict(attrs)
        if tag == "div" and "movie-list" in classes:
            self.has_container = True
        if tag == "a" and "box" in classes and (attributes.get("href") or "").startswith("/v/"):
            self._entry = {
                "path": attributes["href"].split("?", 1)[0],
                "title": attributes.get("title") or "",
                "code": "",
                "cover_url": "",
            }
        elif tag == "img" and self._entry is not None:
            self._entry["cover_url"] = (
                attributes.get("src") or attributes.get("data-src") or ""
            )
        elif tag == "strong" and self._entry is not None:
            self._capture_code = True

    def handle_data(self, data):
        if self._capture_code and self._entry is not None:
            self._entry["code"] += data

    def handle_endtag(self, tag):
        if tag == "strong":
            self._capture_code = False
        elif tag == "a" and self._entry is not None:
            code = self._entry["code"].strip()
            if code:
                self.entries.append(
                    (
                        code,
                        self._entry["title"].strip(),
                        self._entry["path"],
                        self._entry["cover_url"].strip(),
                    )
                )
            self._entry = None
            self._capture_code = False


class _MagnetParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.has_container = False
        self.entries: list[dict[str, object]] = []
        self._entry: dict[str, object] | None = None
        self._field: str | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = _classes(attrs)
        if tag == "div" and attributes.get("id") == "magnets-content":
            self.has_container = True
        if tag == "a" and (attributes.get("href") or "").casefold().startswith("magnet:?"):
            self._entry = {"uri": attributes["href"], "name": [], "meta": [], "tags": []}
        elif self._entry is not None and tag == "span":
            if "name" in classes:
                self._field = "name"
            elif "meta" in classes:
                self._field = "meta"
            elif "tag" in classes:
                self._field = "tags"

    def handle_data(self, data):
        if self._entry is not None and self._field is not None:
            self._entry[self._field].append(data)

    def handle_endtag(self, tag):
        if tag == "span":
            self._field = None
        elif tag == "a" and self._entry is not None:
            self.entries.append(self._entry)
            self._entry = None
            self._field = None


class JavDbRankingProvider:
    name = "javdb_ranking"
    kind = "discovery"
    SUPPORTED_SCOPES = {"daily", "weekly", "monthly"}

    def __init__(
        self,
        http: ProviderHttpClient,
        *,
        base_url: str = "https://javdb.com",
        cookie: str | None = None,
        user_agent: str | None = None,
    ):
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.headers = _session_headers(
            base_url=self.base_url, cookie=cookie, user_agent=user_agent
        )

    def collect(self, scope: str, *, run_id: str) -> list[ContentItem]:
        if scope not in self.SUPPORTED_SCOPES:
            raise ProviderParseError("unsupported_scope")
        response = self.http.get(
            f"{self.base_url}/rankings/movies",
            params={"p": scope},
            headers=self.headers,
            run_id=run_id,
        )
        parser = _RankingParser()
        parser.feed(response.text)
        if not parser.has_container:
            raise ProviderParseError(_page_error_code(response.text))

        items = []
        seen = set()
        for code, title, detail_path, cover_url in parser.entries:
            namespace, normalized_key = normalize_jav_identity(
                code, fallback_namespace="javdb"
            )
            identity = (namespace, normalized_key.casefold())
            if identity in seen:
                continue
            seen.add(identity)
            items.append(
                ContentItem(
                    namespace=namespace,
                    raw_key=code,
                    normalized_key=normalized_key,
                    rankings=[
                        RankingEvidence(
                            source=self.name,
                            rank=len(items) + 1,
                            raw_key=code,
                            scope=scope,
                        )
                    ],
                    title=title,
                    metadata={
                        key: value
                        for key, value in {
                            "javdb_detail_path": detail_path,
                            "source_url": urljoin(self.base_url + "/", detail_path),
                            "cover_url": (
                                urljoin(self.base_url + "/", cover_url)
                                if cover_url
                                else None
                            ),
                        }.items()
                        if value
                    },
                )
            )
        return items


class JavDbCandidateProvider:
    name = "javdb_detail"
    kind = "candidate"

    def __init__(
        self,
        http: ProviderHttpClient,
        *,
        base_url: str = "https://javdb.com",
        cookie: str | None = None,
        user_agent: str | None = None,
    ):
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.headers = _session_headers(
            base_url=self.base_url, cookie=cookie, user_agent=user_agent
        )

    def search(self, item: ContentItem, *, run_id: str):
        detail_path = item.metadata.get("javdb_detail_path")
        if not isinstance(detail_path, str) or not detail_path.startswith("/v/"):
            detail_path = self._search_detail_path(item, run_id=run_id)
        if detail_path is None:
            return []
        response = self.http.get(
            urljoin(self.base_url + "/", detail_path),
            headers=self.headers,
            run_id=run_id,
        )
        parser = _MagnetParser()
        parser.feed(response.text)
        if not parser.has_container:
            raise ProviderParseError(_page_error_code(response.text))

        candidates = []
        for entry in parser.entries:
            title = " ".join(entry["name"]).strip()
            meta = " ".join(entry["meta"]).strip()
            tags = " ".join(entry["tags"]).strip()
            chinese, uncensored, uhd = classify_attributes(f"{title} {tags}")
            evidence = CandidateEvidence(
                source=self.name,
                magnet_uri=str(entry["uri"]),
                title=title,
                size_mb=parse_size_mb(meta),
                chinese_subtitles=chinese,
                uncensored=uncensored,
                uhd=uhd,
                notes=tuple(value for value in (meta, tags) if value),
            )
            try:
                candidates.append(
                    candidate_from_magnet(item_identity=item.identity, evidence=evidence)
                )
            except InvalidMagnet:
                continue
        if parser.entries and not candidates:
            raise ProviderParseError("invalid_candidate_data")
        return candidates

    def _search_detail_path(
        self, item: ContentItem, *, run_id: str
    ) -> str | None:
        response = self.http.get(
            f"{self.base_url}/search",
            params={"q": item.normalized_key, "f": "all"},
            headers=self.headers,
            run_id=run_id,
        )
        parser = _RankingParser()
        parser.feed(response.text)
        if not parser.has_container:
            raise ProviderParseError(_page_error_code(response.text))

        target = item.identity
        for code, _title, detail_path, _cover_url in parser.entries:
            namespace, normalized_key = normalize_jav_identity(
                code, fallback_namespace="javdb"
            )
            if (namespace, normalized_key.casefold()) == target:
                return detail_path
        return None
