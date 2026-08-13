# Architecture

Tuntu separates content discovery, release resolution, filtering, and download
routing. Every boundary can be extended without changing the orchestration
core.

```text
DiscoverySource -> ContentItem -> CandidateSource -> Candidate
                                                -> Rule[]
                                                -> Route -> DownloadClient
```

## Extension points

- `DiscoverySource`: collects wanted content from rankings, RSS, subscriptions,
  searches, or manual input.
- `CandidateSource`: resolves a wanted item into downloadable releases.
- `Rule`: rejects a candidate with a human-readable reason or accepts it.
- `Route`: matches transfer kinds and tags, then chooses a destination.
- `DownloadClient`: submits the matched candidate to CloudDrive2,
  qBittorrent, or another client.

Content keys are deliberately neutral. A key may be a catalog code, an
IMDb/TMDB identifier, a podcast GUID, an ISBN, or another stable identifier.
Candidate identities should be content-addressed when possible, such as a
BitTorrent info hash.

## Transfer types

Tuntu does not assume every release is a magnet:

- `magnet`: common for public DHT-based BitTorrent releases.
- `torrent`: required by many private trackers because DHT is disabled and the
  tracker/passkey metadata must be preserved.
- `url`: an explicit downloadable resource handled by a supporting client.

## Safety boundary

Discovery never triggers a download. `Pipeline.discover()` produces an
auditable list of accepted and rejected candidates. A caller must explicitly
invoke `Pipeline.submit()` or `Pipeline.submit_candidates()` to create external
download tasks.

## Planned first-party plugins

- JAV rankings and public BitTorrent metadata sources.
- Generic RSS/Atom and manual-input discovery.
- TMDB popular/trending movies.
- CloudDrive2/115 and qBittorrent download clients.
- Private tracker adapters configured by users with authorized credentials.

