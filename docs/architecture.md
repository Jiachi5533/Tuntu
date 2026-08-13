# Architecture

Rankarr separates discovery from downloading so every boundary can be replaced
without changing the orchestration core.

```text
RankingSource -> RankedItem -> CandidateSource -> Candidate
                                              -> Rule[]
                                              -> DownloadClient
```

## Extension points

- `RankingSource`: returns ordered content identities for a period.
- `CandidateSource`: resolves one identity into downloadable candidates.
- `Rule`: rejects a candidate with a human-readable reason or accepts it.
- `DownloadClient`: submits an accepted candidate to a remote downloader.

Core identities are deliberately content-neutral. A key may be a catalog code,
an IMDb/TMDB identifier, a podcast GUID, a book ISBN, or a provider-defined
stable key. Candidate identities should be content-addressed when possible,
such as a BitTorrent info hash.

## Safety boundary

Discovery never triggers a download. `Pipeline.discover()` produces an
auditable list of accepted and rejected candidates. A caller must explicitly
invoke `Pipeline.submit()` to create external download tasks.

## Planned first-party plugins

- JAV rankings and public BitTorrent metadata sources.
- Generic RSS/Atom rankings.
- TMDB popular/trending movies.
- CloudDrive2/115 and qBittorrent download clients.

