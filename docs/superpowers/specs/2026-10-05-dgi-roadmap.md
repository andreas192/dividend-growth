# Roadmap: deferred from v1

Date: 2026-10-05
Status: Ideas only. Each item needs its own brainstorm, spec and plan before work starts.
Parent: `2026-10-05-dgi-screener-design.md`

| Item | Why it was deferred | Depends on |
|---|---|---|
| Compare view (2-4 companies side by side) | Natural next step once the company page exists | v1 cache and charts |
| Watchlist | Needs a small state store and change tracking (score drops, dividend cuts) | v1, decision on where state lives |
| Holdings and income projection (yield on cost, concentration, forward income) | Largest scope, persistent user data | Watchlist |
| Alerts (dividend cut, payout breach, score drop) | Needs a watchlist and a delivery channel | Watchlist |
| Backtesting of the score | Needs point-in-time (`as_of`) screening and accepts survivorship bias | `as_of` screening |
| `as_of` screening ("what would the screener have said in 2019") | Needs the refresh to rebuild metrics per date | v1 metrics, investment `*_as_of` views |
| Scoring of REITs, banks and insurers | Investment has no concept design for FFO, interest income, regulatory capital | Investment project roadmap |
| Dividend pay dates and calendar | Yahoo provides no pay date | A new source in investment |
| Host port for the UI | Needs a port mapping in investment's `cluster.tf` and a cluster recreate | Owner decision |
| Closer match to the Dividendology dashboards | The channel could not be read in the design session | Example screenshots from the owner |
