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
