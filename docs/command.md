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

## Cluster (added in Tasks 24-26)

```bash
scripts/install_tools.sh      # pinned kubectl and kind into .tools/
scripts/build_image.sh        # podman build, kind load
scripts/deploy.sh             # kustomize apply into namespace dgi
scripts/open.sh               # port-forward web to 127.0.0.1:8760
```
