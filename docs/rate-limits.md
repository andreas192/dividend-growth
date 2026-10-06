# Rate limits

The only upstream here is the local investment API (`../investment`, `invest serve`). It is our own server, not a rate-limited source; external-source limits (SEC EDGAR, Yahoo) stay in `../investment/docs/rate-limits.md`.

## Rules

- Pull in bulk, never per ticker: Arrow pages of up to `page_limit` rows (default 100,000, setting `DGI_PAGE_LIMIT`), cursor-paged, one request at a time. No concurrent hammering of the API.
- The only per-ticker request is the company page's daily price series, held in an in-memory cache keyed by (upstream key, ticker).
- A bulk pull never asks for the whole `price_daily` table: latest prices come from a narrow `trade_date` window and year-end closes from December windows.
- If the API answers 503, `dgi refresh` stops and keeps the old cache; it never retries in a tight loop (the CronJob schedule is the retry).

## Tests

Paging and error mapping are covered with a fake transport (`tests/test_client_http.py`). No real sleeps and no network.
