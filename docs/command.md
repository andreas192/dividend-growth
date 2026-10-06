# Commands

Every command assumes the repo root as working directory. Run Python only through `uv run` (CLAUDE.md "Isolation").

## Setup (once)

```bash
uv python install 3.12 --no-bin
uv sync                                  # deps into ./.venv only
scripts/check_isolation.sh baseline      # before installing anything new
```

## App (local, against `invest serve`)

```bash
(cd ../investment && uv run invest serve)   # in another terminal: API on 127.0.0.1:8750
uv run dgi refresh                          # pull, build metrics, score, check, swap; --force ignores the keys
uv run dgi status                           # cache upstream key, build time, counts, not-scored by reason
uv run dgi check                            # quality checks on the live cache
uv run dgi serve                            # web UI on 127.0.0.1:8760
```

A scoring-only edit of `config/scoring.yaml` needs no new pull but still needs `dgi refresh` (it asks the API's `/health` first so new upstream data is not skipped; if the API is unreachable it rescores from the cache and prints a warning) and a restart of `dgi serve` (the app loads the file once; the footer warns when the stored scores came from a different file).

Settings (env): `DGI_INVEST_API_URL` (default `http://127.0.0.1:8750`), `DGI_DATA_DIR` (default `data`), `DGI_SCORING_CONFIG` (default `config/scoring.yaml`), `DGI_HOST`, `DGI_PORT`.

## Tests

```bash
uv run pytest                              # whole offline suite
uv run pytest tests/test_dividends.py -q   # one file
uv run pytest tests/test_dividends.py -k streak
```

## Isolation

```bash
scripts/check_isolation.sh baseline   # before installing
scripts/check_isolation.sh verify     # after; exit 1 on any difference
```

## Git and PRs

- Branch from `develop`, open PRs against `develop` (`gh pr create --base develop`). `main` is the main branch.
- Never commit anything under `data/` or `.tools/`.

## Cluster (namespace `dgi` in the kind cluster `invest`)

The cluster belongs to `../investment`; this project owns only namespace `dgi`. Scripts use investment's kubeconfig
(`../investment/deploy/terraform/.kube/invest.config`, override with `DGI_KUBECONFIG`) and always the `kind-invest`
context, never the current one.

The cluster path is unverified until plan Task 25 is done: the scripts and manifests are built and tested offline, but they have not been run against a live cluster.

```bash
scripts/install_tools.sh         # pinned kubectl and kind into .tools/ (checksum-verified; installed files are re-hashed on every run)
scripts/deploy.sh                # build the image, load it into kind, apply deploy/k8s
scripts/deploy.sh --refresh      # the same, then run one refresh Job now and wait for it
scripts/open.sh                  # port-forward the UI to http://127.0.0.1:8760
scripts/delete.sh                # remove namespace dgi (the cache is rebuildable)
scripts/image_tag.sh             # the content-hash tag of the current tree
scripts/check_isolation.sh verify
```

Settings (env) also include `DGI_PAGE_LIMIT` (rows per API page, default 100000).
