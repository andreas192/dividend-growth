# dividend-growth

A personal, read-only dividend growth investing (DGI) screener and company analyzer. It ranks US companies by a transparent score and shows one company from a DGI angle (dividend record, safety, growth and quality, valuation). It reads only the `../investment` platform's HTTP API.

```
investment API  ->  dgi refresh  ->  dgi.duckdb cache  ->  dgi serve (http://127.0.0.1:8760)
```

- Design: `docs/superpowers/specs/2026-10-05-dgi-screener-design.md`
- Commands: `docs/command.md`
- Scoring is configured in `config/scoring.yaml` (weights, bands, filters). The `/methodology` page shows the active values.
- Banks, insurers and REITs are listed as "not scored" in v1.

This is research tooling, not investment advice.

## Running it

Locally, against `invest serve` on 127.0.0.1:8750:

```bash
uv sync
uv run dgi refresh      # pull, score, check, swap the cache in (about 15 s on the full data set)
uv run dgi serve        # http://127.0.0.1:8760
```

Kubernetes (the `invest` kind cluster): `scripts/install_tools.sh`, `scripts/deploy.sh --refresh` and `scripts/open.sh` exist, and a CronJob manifest refreshes the cache daily at 07:00 Europe/Bucharest. These scripts and manifests are untested against a live cluster (plan Task 25 is pending until the `invest` cluster exists). Before the first deploy, check `DGI_INVEST_API_URL` (default in `deploy/k8s`: `http://api.invest.svc.cluster.local:8750`) against investment's actual Service. See `docs/command.md`.

How the score works is on the `/methodology` page and in `config/scoring.yaml`. Measured numbers and what was checked against the real data are in `docs/acceptance-local.md`.
