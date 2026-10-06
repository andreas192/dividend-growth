# Next steps

State at the end of v1 (branch `feat/dgi-v1`): tasks 1-24 and 26 of the plan are implemented and accepted locally against the real API (`acceptance-local.md`). Task 25 (run in the cluster) and the items below are open. Ideas for new features live in `superpowers/specs/2026-10-05-dgi-roadmap.md`; this file lists work on what v1 already does.

## 1. Deploy to the cluster (plan Task 25)

Blocked until the `invest` kind cluster exists (`../investment/deploy/...` and its kubeconfig). The manifests, image and guard scripts are tested offline only.

- Run Task 25 from `superpowers/plans/dgi-screener/` as written: build the image with Podman, `kind load image-archive`, `scripts/deploy.sh`, then a cold refresh job and a port-forward.
- Verify what is unproven: `DGI_INVEST_API_URL=http://api.invest.svc.cluster.local:8750` (service name and port), PVC sharing between the refresh CronJob and the web pod, the CronJob schedule, and memory against the limits (refresh 832Mi, web 192Mi; web is about 171 MiB with the series cache full).
- Record the in-cluster results in `acceptance-local.md` (or a sibling `acceptance-cluster.md`).
- Run `scripts/check_isolation.sh baseline` before and `verify` after. The cluster section of the check is skipped today because no invest kubeconfig exists.

## 2. Correctness to investigate (real data)

1. **PG raise streak is 42; the real record is about 69.** Find out whether it comes from missing early dividend history in gold, a tolerance in `config/scoring.yaml` (`metrics:`), or the split re-adjustment. Pin the answer with a unit test.
2. **KDP still carries `suspect_dividend`.** Its 2018 payment (103.75) is not classed as special because a special needs more payments than usual that year (2018 had 3 against a usual 4); the 2018 annual dividend per share comes out at 104.48. Decide whether the red flag is the right outcome; if not, the fix belongs in `price_dividend.sql`.
3. **Interest coverage:** 586 companies show the 99 cap (135 of the 147 scored ones are debt-free) and 522 scored companies have NULL. Check that NULL lowers coverage as intended and that the screener explains a NULL, not a blank.
4. **`dividend_suspended` fires on 216 companies (4 scored).** Spot-check that none are payers with a long payment gap in the data.

## 3. Hardening and small gaps

Done on `fix/dgi-hardening`: the screener form carries `size=`; `dgi refresh` rescores a scoring-only edit from the cache when the API is unreachable (it still asks `/health` first, so new upstream data is never skipped); the DuckDB error guard, `dgi status` and `dgi check` only treat storage failures as cache errors (SQL bugs show a traceback); the 404 and 422 pages carry the footer; tests use a frozen copy of the scoring config (`tests/frozen/scoring.yaml`); `install_tools.sh` re-hashes the installed binaries on every run.

- Record the upstream contract `content_hash` in `acceptance-local.md` so contract drift is visible in review (needs the real API running).
- Pin the base image by digest in the `Dockerfile` (needs a registry lookup; `python:3.12-slim` and `ghcr.io/astral-sh/uv:0.12` are still tags).
- The `no_cache` page has no footer by design: with no cache there is nothing to compare the scoring config with.
- Acceptance steps not repeated after the final fixes (second run says "up to date", scoring edit without an API pull, revert): repeat them once on the next real run.

## 4. Process

- Two early commits (b20da78, fd9ceac) have the `Co-Authored-By` trailer on the subject line. Cosmetic; fix only if the history is rewritten for another reason.
- Before merging to `develop`, confirm the isolation baseline (`scripts/check_isolation.sh verify`) ends OK on your machine.
