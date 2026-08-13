# Rankarr

Rankarr is an open-source, pluggable ranking-to-download automation engine.
It watches rankings, resolves ranked items through one or more candidate
sources, applies auditable rules, and submits accepted results to a configured
download client.

The first use case is automated JAV ranking discovery and CloudDrive2/115
downloads. The core is intentionally content-neutral: community plugins can
add popular movies, RSS charts, podcasts, books, software releases, or other
authorized sources without changing the pipeline.

## Design goals

- Ranking, candidate, rule, and downloader plugins.
- Exact identity matching and cross-source deduplication.
- Explainable filtering: every rejected candidate keeps its reasons.
- Explicit separation between discovery and external download submission.
- Persistent history for discovered, accepted, submitted, completed, skipped,
  and failed items.
- A small core without PT seeding or tracker-specific assumptions.

## Current status

Rankarr is pre-alpha. The repository currently contains the plugin contracts,
content-neutral models, deduplication pipeline, reusable rules, example
configuration, and tests. Existing JAV source probes will be migrated into
first-party plugins next.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

See [the architecture document](docs/architecture.md) and
[`config.example.yaml`](config.example.yaml) for the extension model.

## Responsible use

Only configure sources and downloads you are legally authorized to access.
Provider plugins must respect applicable terms, rate limits, and access
controls. Rankarr does not bypass authentication, age verification, paywalls,
or anti-bot challenges.

## License

MIT

