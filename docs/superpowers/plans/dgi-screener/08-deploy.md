# Phase 8: Local Kubernetes deployment (Tasks 24-26)

Part of `../2026-10-05-dgi-screener.md`. Spec: `../../specs/2026-10-05-dgi-screener-design.md`, section "Deployment". Investment's own deployment spec (a draft when this plan was written): `../investment/docs/superpowers/specs/2026-10-05-container-deployment-design.md`.

**Gate.** Task 24 is pure files and offline checks and can be done as soon as Task 23 has recorded the memory numbers. Tasks 25-26 need investment's `invest` kind cluster to exist, which it did not when this plan was written (`../investment/deploy/` was absent). Do not create or change that cluster from here: it belongs to the investment project. Everything in this phase touches only namespace `dgi`.

Rules (from the spec and CLAUDE.md), enforced by the scripts and by tests:

- Image `python:3.12-slim` + `uv`, non-root (uid 10001), entrypoint `dgi`, built with Podman, loaded with `kind load image-archive`, `imagePullPolicy: Never`, no registry. Podman names a local image `localhost/dgi:<tag>`, and that full name is what kind loads and the pods use (a bare `dgi` would be resolved as `docker.io/library/dgi` and never found).
- Image tag = 12 hex characters of a hash over the Dockerfile, `pyproject.toml`, `uv.lock`, `src/` and `config/`.
- Every `kubectl` call names the kubeconfig file and the `kind-invest` context explicitly (`kc` in `scripts/lib.sh`); the scripts refuse to run if that context or the kind cluster is missing, never use the current context, never touch the GKE context. `kubectl` and `kind` are pinned binaries in gitignored `.tools/`, checksum-verified; no Homebrew change.
- Objects: namespace `dgi`; PVC `cache` (1 Gi, the cluster's default class, disposable); ConfigMap `scoring` generated from `config/scoring.yaml` (a change rolls the pods, no image rebuild needed); Deployment `web` (1 replica, `Recreate`, cache mounted read-only, readiness and liveness on `/health`); Service `web` (ClusterIP 8760); CronJob `refresh` (`0 7 * * *`, `Europe/Bucharest`, `concurrencyPolicy: Forbid`, deadline 3600 s). `DGI_INVEST_API_URL=http://api.invest.svc.cluster.local:8750` (spec open decision 1; it is an env value, change it in the two manifests if investment names its Service differently).
- UI access is `kubectl port-forward` to `127.0.0.1:8760` (`scripts/open.sh`); no host port mapping, because that is fixed when the cluster is created.
- Resource limits come from Task 23's measurements, not guesses.

---

### Task 24: Image, scripts and manifests

> **Changed by Task 31 (`09-hardening.md`):** `scripts/install_tools.sh` decides "already installed" by re-hashing both binaries against the pins, not from the `PINS` marker file.

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `scripts/lib.sh`, `scripts/install_tools.sh`, `scripts/image_tag.sh`, `scripts/build_image.sh`, `scripts/deploy.sh`, `scripts/delete.sh`, `scripts/open.sh`, `deploy/k8s/{kustomization,namespace,pvc,web,service,refresh}.yaml`
- Modify: `scripts/check_isolation.sh` (replace with the version that also covers the cluster's other namespaces)
- Test: `tests/test_deploy_manifests.py`

**Interfaces:**
- Consumes: the working app (Tasks 1-22), Task 23's measurements in `docs/acceptance-local.md`.
- Produces: a reproducible image build, deploy scripts with the context guard, and manifests that `kubectl kustomize` renders. `scripts/image_tag.sh` prints the tag; `scripts/deploy.sh [--refresh]`; `scripts/delete.sh`; `scripts/open.sh`; `scripts/check_isolation.sh baseline|verify` (cluster section skipped when there is no cluster).

- [ ] **Step 1: Write the Dockerfile and verify it builds and runs the way the cluster will run it**

The base image's own Python is used (the project's `python-preference = "only-managed"` is for development; `UV_PYTHON_PREFERENCE=only-system` overrides it inside the image).

`Dockerfile`:
```dockerfile
# One image for `dgi serve` and `dgi refresh`. Built with Podman; loaded into kind, never pushed.
FROM ghcr.io/astral-sh/uv:0.12 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
# The project pins uv-managed Python for development; in the image the base image's Python is the one to use.
ENV UV_PYTHON_PREFERENCE=only-system UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY config ./config
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin dgi
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" HOME=/tmp DGI_DATA_DIR=/data DGI_SCORING_CONFIG=/app/config/scoring.yaml PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8760
ENTRYPOINT ["dgi"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8760"]
```

`.dockerignore`:
```text
.git
.venv
.tools
data
tests
docs
deploy
scripts
__pycache__
*.pyc
.pytest_cache
.claude
```

```bash
podman build -t localhost/dgi:plan-test .
podman run --rm localhost/dgi:plan-test --help | head -5
podman run -d --name dgi-plan-test --read-only --tmpfs /tmp --user 10001 -p 127.0.0.1:18760:8760 localhost/dgi:plan-test
sleep 4
curl -s http://127.0.0.1:18760/health
curl -s -o /dev/null -w "%{http_code} %{size_download}\n" http://127.0.0.1:18760/static/vendor/echarts.min.js
podman rm -f dgi-plan-test
podman rmi localhost/dgi:plan-test
podman ps -a --format '{{.Names}}'
```
Expected: the help text lists `refresh`, `status`, `check`, `serve`; `/health` answers `{"status":"no_cache"}`; the vendored ECharts file answers `200` and about a megabyte; the last command lists only the containers that were running before (this project's test container is gone). Only containers named `dgi-*` may be removed; never anything else on this machine.

- [ ] **Step 2: Write the scripts**

`scripts/lib.sh`:
```sh
#!/bin/sh
# Shared by the deploy scripts: source it, do not run it. Every kubectl call goes through `kc`, which names the
# kubeconfig file and the kind-invest context explicitly, so the current kubectl context (or a GKE one) is never used.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$ROOT/.tools:$PATH"
export KIND_EXPERIMENTAL_PROVIDER=podman
CLUSTER="invest"
CONTEXT="kind-$CLUSTER"
NAMESPACE="dgi"
# investment's cluster kubeconfig, read only; override with DGI_KUBECONFIG
KUBECONFIG_FILE="${DGI_KUBECONFIG:-$ROOT/../investment/deploy/terraform/.kube/invest.config}"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "error: $1 not found; run scripts/install_tools.sh" >&2; exit 1; }
}

kc() {
  kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" "$@"
}

require_cluster() {
  need kubectl
  need kind
  [ -f "$KUBECONFIG_FILE" ] || {
    echo "error: no kubeconfig at $KUBECONFIG_FILE (does investment's cluster exist? set DGI_KUBECONFIG)" >&2
    exit 1
  }
  kubectl --kubeconfig "$KUBECONFIG_FILE" config get-contexts -o name | grep -qx "$CONTEXT" || {
    echo "error: $KUBECONFIG_FILE has no context $CONTEXT; refusing to continue" >&2
    exit 1
  }
  kind get clusters 2>/dev/null | grep -qx "$CLUSTER" || {
    echo "error: the kind cluster $CLUSTER does not exist" >&2
    exit 1
  }
}
```

`install_tools.sh` pins kubectl `v1.37.1` and kind `v0.33.0` with the SHA-256 values published for them (checked against the publishers' checksum files on 2026-10-05). If investment pins other versions for its cluster by the time this is done, use the same ones and update both pins and checksums from the publishers' files.

`scripts/install_tools.sh`:
```sh
#!/bin/sh
# Installs pinned kubectl and kind into .tools/ (gitignored) after checking each file's SHA-256.
# No Homebrew, no system change. Pins verified against the publishers' checksum files on 2026-10-05.
# If ../investment pins other versions for its cluster, use the same ones here.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$ROOT/.tools"
KUBECTL_VERSION="v1.37.1"
KIND_VERSION="v0.33.0"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)
    ARCH=arm64
    KUBECTL_SHA="fd65982c97ddad3106754b69ffa196d0e543aa591930ae52aed1adfb92f8c77f"
    KIND_SHA="0c8c7dbe5e23594a198b786c4bc13dacc101fa6196b0cb0b23a1ca44e61f4b4f"
    ;;
  Darwin-x86_64)
    ARCH=amd64
    KUBECTL_SHA="6851381c486ff6edd691623e3d65c87cb9a5b02887ff8fbbb38d8a031b748387"
    KIND_SHA="5a99f26f57246dc9319dd294803313197a0f34d33c525b3ea8b655db5916ece0"
    ;;
  *)
    echo "error: unsupported platform $(uname -s)-$(uname -m); the pins cover macOS arm64 and x86_64" >&2
    exit 1
    ;;
esac

fetch() {  # fetch URL DEST EXPECTED_SHA256
  tmp="$(mktemp)"
  curl -fsSL "$1" -o "$tmp"
  actual="$(shasum -a 256 "$tmp" | awk '{print $1}')"
  if [ "$actual" != "$3" ]; then
    echo "error: checksum mismatch for $1: expected $3, got $actual" >&2
    rm -f "$tmp"
    exit 1
  fi
  chmod +x "$tmp"
  mv "$tmp" "$2"
}

mkdir -p "$TOOLS"
PINS="kubectl=$KUBECTL_VERSION kind=$KIND_VERSION arch=$ARCH"
if [ -x "$TOOLS/kubectl" ] && [ -x "$TOOLS/kind" ] && [ "$(cat "$TOOLS/PINS" 2>/dev/null)" = "$PINS" ]; then
  echo "tools already installed: $PINS"
  exit 0
fi
fetch "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/darwin/$ARCH/kubectl" "$TOOLS/kubectl" "$KUBECTL_SHA"
fetch "https://github.com/kubernetes-sigs/kind/releases/download/$KIND_VERSION/kind-darwin-$ARCH" "$TOOLS/kind" "$KIND_SHA"
echo "$PINS" > "$TOOLS/PINS"
echo "installed: $PINS into $TOOLS"
```

`scripts/image_tag.sh`:
```sh
#!/bin/sh
# Prints the image tag: 12 hex characters of a hash over the Dockerfile, pyproject.toml, uv.lock, src/ and config/.
# An unchanged tree gives the same tag, so a rebuild of unchanged code is a cached no-op.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
{
  cat Dockerfile pyproject.toml uv.lock
  find src config -type f ! -name '*.pyc' ! -path '*/__pycache__/*' | LC_ALL=C sort | while read -r file; do
    shasum -a 256 "$file"
  done
} | shasum -a 256 | cut -c1-12
```

`scripts/build_image.sh`:
```sh
#!/bin/sh
# Builds the image with Podman and loads it into the kind cluster. No registry. Needs the cluster to exist.
set -eu
. "$(dirname "$0")/lib.sh"
need podman
require_cluster

TAG="$("$ROOT/scripts/image_tag.sh")"
IMAGE="localhost/dgi:$TAG"   # podman names local images localhost/..., and that is the name kind loads and pods use
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

podman build -t "$IMAGE" "$ROOT"
podman save -o "$TMP/dgi.tar" "$IMAGE"
kind load image-archive "$TMP/dgi.tar" --name "$CLUSTER"
echo "image $IMAGE loaded into kind cluster $CLUSTER"
```

`scripts/deploy.sh`:
```sh
#!/bin/sh
# Builds and loads the image, then applies deploy/k8s into namespace dgi of the kind-invest cluster.
#   scripts/deploy.sh             apply
#   scripts/deploy.sh --refresh   apply, then run one refresh Job now and wait for it
set -eu
. "$(dirname "$0")/lib.sh"
require_cluster

"$ROOT/scripts/build_image.sh"
TAG="$("$ROOT/scripts/image_tag.sh")"

RENDER="$(mktemp -d)"
trap 'rm -rf "$RENDER"' EXIT
cp -R "$ROOT/deploy/k8s/." "$RENDER/"
cp "$ROOT/config/scoring.yaml" "$RENDER/scoring.yaml"
sed -i '' "s/__IMAGE_TAG__/$TAG/" "$RENDER/kustomization.yaml"   # BSD sed (macOS)

kubectl kustomize "$RENDER" | kc apply -f -
kc -n "$NAMESPACE" rollout status deployment/web --timeout=180s

if [ "${1:-}" = "--refresh" ]; then
  JOB="refresh-now-$(date +%s)"
  kc -n "$NAMESPACE" create job "$JOB" --from=cronjob/refresh
  kc -n "$NAMESPACE" wait --for=condition=complete "job/$JOB" --timeout=3600s
  kc -n "$NAMESPACE" logs "job/$JOB" | tail -20
fi

"$ROOT/scripts/check_isolation.sh" verify
```

`scripts/delete.sh`:
```sh
#!/bin/sh
# Removes namespace dgi (and with it the disposable cache volume). Touches nothing else in the cluster.
set -eu
. "$(dirname "$0")/lib.sh"
require_cluster
kc delete namespace "$NAMESPACE" --wait --ignore-not-found
"$ROOT/scripts/check_isolation.sh" verify
```

`scripts/open.sh`:
```sh
#!/bin/sh
# Forwards the web UI to http://127.0.0.1:8760 (no host port mapping; that is fixed when the cluster is created).
set -eu
. "$(dirname "$0")/lib.sh"
require_cluster
echo "UI on http://127.0.0.1:8760 (Ctrl-C to stop)"
exec kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" -n "$NAMESPACE" port-forward --address 127.0.0.1 svc/web 8760:8760
```

```bash
chmod +x scripts/*.sh
for f in scripts/*.sh; do sh -n "$f" && echo "syntax ok: $f"; done
scripts/install_tools.sh
scripts/install_tools.sh
.tools/kubectl version --client
.tools/kind version
scripts/image_tag.sh
scripts/image_tag.sh
scripts/deploy.sh
```
Expected: every script passes `sh -n`; the first `install_tools.sh` downloads and verifies both binaries, the second says `tools already installed`; the two `image_tag.sh` runs print the same 12 characters; `scripts/deploy.sh` stops with `error: no kubeconfig at ...` (no cluster yet) or, when the cluster exists, continues.

- [ ] **Step 3: Replace `scripts/check_isolation.sh`**

It keeps the Python and Podman checks, adds the investment repo, and, when `.tools/kubectl` and the cluster's kubeconfig exist, the names of everything in the cluster's other namespaces (namespace `dgi` is this project's own and is excluded).

`scripts/check_isolation.sh`:
```sh
#!/bin/sh
# Checks that work in this repo has not changed anything ../capital-trading, ../investment or the invest cluster's
# other namespaces depend on.
#   scripts/check_isolation.sh baseline   record the current state (run before installing anything)
#   scripts/check_isolation.sh verify     re-record and diff against the baseline; exit 1 on any difference
# Read-only: it only inspects. Namespace dgi is this project's own and is left out of the cluster section. If the only
# difference is in a "repo" section, check whether you were editing that repo yourself.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CT="$ROOT/../capital-trading"
INV="$ROOT/../investment"
BASELINE="${TMPDIR:-/tmp}/dgi-isolation-baseline.txt"
KUBECTL="$ROOT/.tools/kubectl"
KUBECONFIG_FILE="${DGI_KUBECONFIG:-$INV/deploy/terraform/.kube/invest.config}"

cluster_snapshot() {
  # explicit kubeconfig and context, as in scripts/lib.sh; skipped when there is no cluster to look at
  [ -x "$KUBECTL" ] && [ -f "$KUBECONFIG_FILE" ] || { echo "(no invest cluster to inspect)"; return; }
  kc() { "$KUBECTL" --kubeconfig "$KUBECONFIG_FILE" --context kind-invest "$@"; }
  echo "## invest cluster namespaces (dgi excluded)"
  kc get namespaces -o name 2>/dev/null | grep -v '/dgi$' | sort
  for ns in $(kc get namespaces -o name 2>/dev/null | sed 's|^namespace/||' | grep -v '^dgi$' | sort); do
    echo "## objects in $ns"
    kc -n "$ns" get deployments,statefulsets,daemonsets,cronjobs,services,configmaps,persistentvolumeclaims -o name 2>/dev/null | sort
    kc -n "$ns" get deployments -o custom-columns=NAME:.metadata.name,GENERATION:.metadata.generation --no-headers 2>/dev/null | sort
  done
}

snapshot() {
  echo "## python and pip on PATH"
  which -a python3 python pip pip3 2>/dev/null
  echo "## Homebrew python packages"
  /opt/homebrew/bin/python3 -m pip freeze 2>/dev/null | sort
  echo "## miniconda base packages"
  "$HOME/miniconda3/bin/python" -m pip freeze 2>/dev/null | sort
  echo "## miniconda environments"
  ls -1 "$HOME/miniconda3/envs" 2>/dev/null
  echo "## Homebrew formulae (uv excluded)"
  HOMEBREW_NO_AUTO_UPDATE=1 brew list --versions 2>/dev/null | grep -v '^uv ' | sort
  echo "## ~/.local/bin"
  ls -1 "$HOME/.local/bin" 2>/dev/null
  echo "## shell startup files"
  shasum "$HOME/.zshrc" "$HOME/.zprofile" "$HOME/.bash_profile" 2>/dev/null
  echo "## podman containers (the kind node of the invest cluster is expected and unchanged) and machine"
  podman ps -a --format '{{.Names}} {{.Image}} {{.Ports}}' 2>/dev/null | sort
  podman machine list --format '{{.Name}} running={{.Running}}' 2>/dev/null
  echo "## capital-trading repo"
  git -C "$CT" rev-parse HEAD 2>/dev/null
  git -C "$CT" status --porcelain 2>/dev/null
  shasum "$CT/requirements.txt" 2>/dev/null
  echo "## investment repo"
  git -C "$INV" rev-parse HEAD 2>/dev/null
  git -C "$INV" status --porcelain 2>/dev/null
  cluster_snapshot
}

case "${1:-}" in
  baseline)
    snapshot > "$BASELINE"
    echo "Baseline saved to $BASELINE ($(wc -l < "$BASELINE" | tr -d ' ') lines)"
    ;;
  verify)
    if [ ! -f "$BASELINE" ]; then
      echo "No baseline found. Run: scripts/check_isolation.sh baseline" >&2
      exit 2
    fi
    CURRENT="$(mktemp)"
    snapshot > "$CURRENT"
    if diff -u "$BASELINE" "$CURRENT"; then
      echo "OK: ../capital-trading, ../investment and the invest cluster's other namespaces are unchanged."
      rm -f "$CURRENT"
    else
      echo "DIFFERENCE DETECTED (see diff above). Stop and report it." >&2
      rm -f "$CURRENT"
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 baseline|verify" >&2
    exit 2
    ;;
esac
```

```bash
sh -n scripts/check_isolation.sh
scripts/check_isolation.sh baseline
scripts/check_isolation.sh verify
```
Expected: `OK: ../capital-trading, ../investment and the invest cluster's other namespaces are unchanged.`

- [ ] **Step 4: Write the failing manifest test**

`tests/test_deploy_manifests.py` (UNIT: the manifests keep the rules):
```python
"""The Kubernetes manifests keep the rules in CLAUDE.md and the spec. Offline: reads the YAML files, no cluster, no kubectl."""

from pathlib import Path

import pytest
import yaml

K8S = Path(__file__).resolve().parent.parent / "deploy" / "k8s"
API_URL = "http://api.invest.svc.cluster.local:8750"


def load(name: str) -> dict:
    return yaml.safe_load((K8S / name).read_text())


def containers(doc: dict) -> list[dict]:
    spec = doc["spec"]
    pod = spec["template"]["spec"] if doc["kind"] == "Deployment" else spec["jobTemplate"]["spec"]["template"]["spec"]
    return pod["containers"]


def pod_spec(doc: dict) -> dict:
    spec = doc["spec"]
    return spec["template"]["spec"] if doc["kind"] == "Deployment" else spec["jobTemplate"]["spec"]["template"]["spec"]


WORKLOADS = ["web.yaml", "refresh.yaml"]


def test_the_base_lists_exactly_the_files_that_exist():
    kustomization = load("kustomization.yaml")
    assert kustomization["namespace"] == "dgi"
    assert sorted(kustomization["resources"]) == sorted(p.name for p in K8S.glob("*.yaml") if p.name != "kustomization.yaml")
    assert kustomization["images"] == [{"name": "localhost/dgi", "newTag": "__IMAGE_TAG__"}]
    assert kustomization["configMapGenerator"][0]["name"] == "scoring"


def test_the_only_namespace_created_is_dgi_and_nothing_else_is_cluster_wide():
    assert load("namespace.yaml")["metadata"]["name"] == "dgi"
    for p in K8S.glob("*.yaml"):
        if p.name != "kustomization.yaml":
            assert yaml.safe_load(p.read_text())["kind"] in {"Namespace", "PersistentVolumeClaim", "Deployment", "Service", "CronJob"}


@pytest.mark.parametrize("name", WORKLOADS)
def test_images_are_local_never_pulled_and_run_as_a_locked_down_non_root_user(name):
    doc = load(name)
    pod = pod_spec(doc)
    assert pod["securityContext"]["runAsNonRoot"] is True and pod["securityContext"]["runAsUser"] == 10001
    for c in containers(doc):
        assert c["image"] == "localhost/dgi" and c["imagePullPolicy"] == "Never"
        assert c["securityContext"]["readOnlyRootFilesystem"] is True and c["securityContext"]["allowPrivilegeEscalation"] is False
        assert c["securityContext"]["capabilities"]["drop"] == ["ALL"]
        assert "memory" in c["resources"]["limits"], "memory limit comes from measurement (docs/acceptance-local.md)"
        env = {e["name"]: e["value"] for e in c["env"]}
        assert env["DGI_INVEST_API_URL"] == API_URL and env["DGI_DATA_DIR"] == "/data" and env["DGI_SCORING_CONFIG"] == "/config/scoring.yaml"


def test_the_web_deployment_is_one_recreated_replica_reading_the_cache_read_only():
    web = load("web.yaml")
    assert web["spec"]["replicas"] == 1 and web["spec"]["strategy"]["type"] == "Recreate"
    c = containers(web)[0]
    mounts = {m["name"]: m for m in c["volumeMounts"]}
    assert mounts["cache"]["readOnly"] is True and mounts["config"]["readOnly"] is True
    assert c["readinessProbe"]["httpGet"]["path"] == "/health" and c["livenessProbe"]["httpGet"]["path"] == "/health"
    assert c["ports"][0]["containerPort"] == 8760
    assert "hostPort" not in c["ports"][0], "the UI is reached by port-forward; no host port"


def test_the_refresh_cronjob_runs_daily_in_bucharest_time_and_never_overlaps():
    job = load("refresh.yaml")
    assert job["spec"]["schedule"] == "0 7 * * *" and job["spec"]["timeZone"] == "Europe/Bucharest"
    assert job["spec"]["concurrencyPolicy"] == "Forbid"
    template = job["spec"]["jobTemplate"]["spec"]
    assert template["backoffLimit"] == 0 and template["activeDeadlineSeconds"] > 0
    assert template["template"]["spec"]["restartPolicy"] == "Never"
    c = containers(job)[0]
    assert c["args"] == ["refresh"]
    assert {m["name"]: m for m in c["volumeMounts"]}["cache"].get("readOnly") is not True   # the job writes the cache


def test_both_workloads_share_the_one_cache_volume_and_config_map():
    for name in WORKLOADS:
        volumes = {v["name"]: v for v in pod_spec(load(name))["volumes"]}
        assert volumes["cache"]["persistentVolumeClaim"]["claimName"] == "cache"
        assert volumes["config"]["configMap"]["name"] == "scoring"
        assert volumes["tmp"] == {"name": "tmp", "emptyDir": {}}


def test_the_service_is_cluster_internal_and_the_volume_is_disposable():
    service = load("service.yaml")
    assert service["spec"]["type"] == "ClusterIP" and service["spec"]["ports"][0]["port"] == 8760
    pvc = load("pvc.yaml")
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"] and "storageClassName" not in pvc["spec"]   # the cluster's default class
```

Run: `uv run pytest tests/test_deploy_manifests.py`
Expected: FAIL (`FileNotFoundError` for `deploy/k8s/kustomization.yaml`).

- [ ] **Step 5: Write the manifests**

`deploy/k8s/namespace.yaml`:
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: dgi
```

`deploy/k8s/pvc.yaml`:
```yaml
# The cache is derived and rebuildable, so this volume is disposable: deleting the namespace deletes it.
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: cache
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
```

`deploy/k8s/web.yaml`:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app.kubernetes.io/name: web
  template:
    metadata:
      labels:
        app.kubernetes.io/name: web
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: web
          image: localhost/dgi
          imagePullPolicy: Never
          args: ["serve", "--host", "0.0.0.0", "--port", "8760"]
          ports:
            - name: http
              containerPort: 8760
          env:
            - {name: DGI_INVEST_API_URL, value: "http://api.invest.svc.cluster.local:8750"}
            - {name: DGI_DATA_DIR, value: /data}
            - {name: DGI_SCORING_CONFIG, value: /config/scoring.yaml}
          readinessProbe:
            httpGet: {path: /health, port: http}
            periodSeconds: 10
          livenessProbe:
            httpGet: {path: /health, port: http}
            initialDelaySeconds: 10
            periodSeconds: 30
          resources:
            requests: {cpu: 50m, memory: 128Mi}
            limits: {memory: 256Mi}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: [ALL]
          volumeMounts:
            - {name: cache, mountPath: /data, readOnly: true}
            - {name: config, mountPath: /config, readOnly: true}
            - {name: tmp, mountPath: /tmp}
      volumes:
        - name: cache
          persistentVolumeClaim:
            claimName: cache
        - name: config
          configMap:
            name: scoring
        - name: tmp
          emptyDir: {}
```

`deploy/k8s/service.yaml`:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: web
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: web
  ports:
    - name: http
      port: 8760
      targetPort: http
```

`deploy/k8s/refresh.yaml`:
```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: refresh
spec:
  schedule: "0 7 * * *"
  timeZone: Europe/Bucharest
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 3600
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 0
      activeDeadlineSeconds: 3600
      template:
        metadata:
          labels:
            app.kubernetes.io/name: refresh
        spec:
          restartPolicy: Never
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            fsGroup: 10001
            seccompProfile:
              type: RuntimeDefault
          containers:
            - name: refresh
              image: localhost/dgi
              imagePullPolicy: Never
              args: ["refresh"]
              env:
                - {name: DGI_INVEST_API_URL, value: "http://api.invest.svc.cluster.local:8750"}
                - {name: DGI_DATA_DIR, value: /data}
                - {name: DGI_SCORING_CONFIG, value: /config/scoring.yaml}
              resources:
                requests: {cpu: 200m, memory: 512Mi}
                limits: {memory: 1Gi}
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities:
                  drop: [ALL]
              volumeMounts:
                - {name: cache, mountPath: /data}
                - {name: config, mountPath: /config, readOnly: true}
                - {name: tmp, mountPath: /tmp}
          volumes:
            - name: cache
              persistentVolumeClaim:
                claimName: cache
            - name: config
              configMap:
                name: scoring
            - name: tmp
              emptyDir: {}
```

`deploy/k8s/kustomization.yaml`:
```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: dgi
resources:
  - namespace.yaml
  - pvc.yaml
  - web.yaml
  - service.yaml
  - refresh.yaml
configMapGenerator:
  - name: scoring
    files:
      - scoring.yaml   # deploy.sh copies config/scoring.yaml here; a change rolls the pods and needs no image rebuild
images:
  - name: localhost/dgi
    newTag: __IMAGE_TAG__   # deploy.sh substitutes the content-hash tag
labels:
  - pairs:
      app.kubernetes.io/part-of: dgi
```

Run: `uv run pytest tests/test_deploy_manifests.py`
Expected: PASS (8 passed).

- [ ] **Step 6: Render offline**

```bash
R="$(mktemp -d)"
cp -R deploy/k8s/. "$R/" && cp config/scoring.yaml "$R/scoring.yaml"
sed -i '' "s/__IMAGE_TAG__/$(scripts/image_tag.sh)/" "$R/kustomization.yaml"
.tools/kubectl kustomize "$R" | grep -E "^kind:|image:|^  name:|namespace:"
rm -rf "$R"
```
Expected: kinds `Namespace`, `ConfigMap` (name with a hash suffix), `Service`, `PersistentVolumeClaim`, `Deployment`, `CronJob`; both workloads use `localhost/dgi:<tag>`; every namespaced object is in `dgi`; the ConfigMap's hashed name appears in both pod specs.

- [ ] **Step 7: Set the memory limits from Task 23**

Open `docs/acceptance-local.md`, take the two limits it derived (`peak x 1.25`, rounded up to a multiple of 64 MiB) and put them in `deploy/k8s/refresh.yaml` (`resources.limits.memory`, and a request about half of it) and `deploy/k8s/web.yaml` (limit; request about half). The files ship with `1Gi` for the refresh and `256Mi` for the web as starting values; the dry run measured about 650 MiB and 125 MiB, which gives roughly `832Mi` and `192Mi`. If the derived refresh limit is above 1.5 GiB, stop: that is the redesign threshold in the spec. Re-run `uv run pytest tests/test_deploy_manifests.py` (it checks that a memory limit exists).

- [ ] **Step 8: Commit**

```bash
git add Dockerfile .dockerignore scripts deploy tests/test_deploy_manifests.py docs/acceptance-local.md
git commit -m "feat: image, deploy scripts with context guard, and kustomize manifests"
```

---

### Task 25: Deploy to the invest cluster and accept it there

Needs investment's `invest` cluster. Everything below happens in namespace `dgi` only.

**Files:**
- Modify (only if the checks below require it): `deploy/k8s/web.yaml`, `deploy/k8s/refresh.yaml` (API URL, limits), `docs/acceptance-local.md` (append the in-cluster results)

**Interfaces:**
- Consumes: Task 24, the running cluster and investment's API Service.
- Produces: a working in-cluster deployment, verified; an in-cluster section in `docs/acceptance-local.md`.

- [ ] **Step 1: Preconditions (stop if any fails; none is this project's to fix)**

```bash
ls ../investment/deploy                                   # the cluster's definition exists
export KIND_EXPERIMENTAL_PROVIDER=podman
.tools/kind get clusters                                  # lists: invest
KC="$(ls ../investment/deploy/terraform/.kube/invest.config)"   # or the path investment documents; export DGI_KUBECONFIG if it differs
.tools/kubectl --kubeconfig "$KC" --context kind-invest get nodes
.tools/kubectl --kubeconfig "$KC" --context kind-invest -n invest get deploy,svc
```
Expected: a `Ready` node; in namespace `invest` a Service named `api` exposing port 8750 and a ready Deployment. If the Service has another name or namespace, set `DGI_INVEST_API_URL` in `deploy/k8s/web.yaml` and `deploy/k8s/refresh.yaml` (and `API_URL` in `tests/test_deploy_manifests.py`) to the real address. Check the Kubernetes version is at least 1.27 (CronJob `timeZone`): `kubectl version` shows the server version. Check investment's gold data is present and its API answers: `kubectl -n invest port-forward svc/api 18750:8750` in another terminal, then `curl -s localhost:18750/health`.

- [ ] **Step 2: Fresh isolation baseline with the cluster present**

```bash
scripts/check_isolation.sh baseline
```
Expected: `Baseline saved ... (N lines)`; the snapshot now includes the `invest` cluster's namespaces and objects.

- [ ] **Step 3: Deploy and run the first refresh**

```bash
scripts/deploy.sh --refresh
```
Expected: the image builds and loads (`image localhost/dgi:<tag> loaded into kind cluster invest`), the objects are created, `deployment "web" successfully rolled out`, the refresh Job completes and its log tail shows `rebuilt: no usable cache` and `checks: 6 of 6 passed`, and the script ends with `OK: ... unchanged.` If a pod shows `ErrImageNeverPull`, the image name does not match what the node has: compare `podman exec invest-control-plane crictl images | grep dgi` (a read-only look at the kind node) with the manifest's `localhost/dgi:<tag>`.

- [ ] **Step 4: Verify the running system**

```bash
KCTL=".tools/kubectl --kubeconfig ../investment/deploy/terraform/.kube/invest.config --context kind-invest -n dgi"   # or the path in DGI_KUBECONFIG
$KCTL get pods,cronjob,pvc
$KCTL exec deploy/web -- dgi status
$KCTL exec deploy/web -- dgi check
scripts/open.sh &
sleep 3
curl -s http://127.0.0.1:8760/health
for u in "/" "/company/KO" "/api/company/KO/daily" "/methodology"; do curl -s -m 30 -o /dev/null -w "%{http_code} $u\n" "http://127.0.0.1:8760$u"; done
kill %1
```
Expected: pods `Running`/`Completed`, the CronJob scheduled, the PVC `Bound`; `dgi status` shows the same counts as Task 23's local run (the data is the same gold); `dgi check` prints six `PASS`; every URL answers 200, the daily endpoint included (the pod reaches investment's API at `api.invest.svc.cluster.local`). Open the UI in a browser and look at the screener and a company page once, as in Task 21 Step C5.

- [ ] **Step 5: Resource checks**

```bash
$KCTL describe pod -l app.kubernetes.io/name=web | grep -E "Last State|Reason|OOM|Restart Count"
$KCTL get jobs
$KCTL describe job -l app.kubernetes.io/name=refresh 2>/dev/null | grep -E "Pods Statuses|OOM"
podman stats --no-stream --format '{{.Name}} {{.MemUsage}}'
```
Expected: no `OOMKilled`, zero restarts, the refresh Job `1 Succeeded`. From `podman stats`, add up every container's memory: the sum plus 500 MB must stay under the Podman machine's memory (about 3.8 GB, shared with capital-trading's containers). Record the numbers. Do not run any `podman machine` command.

- [ ] **Step 6: The scheduled path, idempotence and failure handling**

```bash
$KCTL create job refresh-check-1 --from=cronjob/refresh
$KCTL wait --for=condition=complete job/refresh-check-1 --timeout=600s
$KCTL logs job/refresh-check-1 | tail -4                       # expect: up to date
# failure: the same job against an address nothing listens on
$KCTL create job refresh-bad-api --from=cronjob/refresh --dry-run=client -o yaml \
  | sed 's|http://api.invest.svc.cluster.local:8750|http://api.nowhere.svc.cluster.local:8750|' | $KCTL apply -f -
sleep 60
$KCTL logs job/refresh-bad-api | tail -3                       # expect: error: cannot reach the investment API
$KCTL get job refresh-bad-api                                  # expect: Failed (0/1 completions)
scripts/open.sh & sleep 3; curl -s http://127.0.0.1:8760/health; kill %1
$KCTL delete job refresh-check-1 refresh-bad-api
```
Expected: the second run logs `up to date`; the bad-API Job fails with the named error and exit code 1; the web app still serves the previous cache (`/health` reports the same `built_at` as before).

- [ ] **Step 7: Isolation after apply, then delete and redeploy**

```bash
scripts/check_isolation.sh verify
scripts/delete.sh
scripts/deploy.sh --refresh
scripts/check_isolation.sh verify
```
Expected: `OK` every time; after `delete.sh` the namespace is gone (`kubectl get ns` shows no `dgi`) and investment's namespaces and objects are as in the baseline; the redeploy rebuilds the cache from scratch and completes.

- [ ] **Step 8: Record and commit**

Append an "In-cluster" section to `docs/acceptance-local.md` (the refresh Job's duration and the node's `podman stats` totals before and after, whether any limit was adjusted, the Kubernetes server version, the API Service address used). If a limit or the API URL had to change, edit the manifest, re-run `uv run pytest tests/test_deploy_manifests.py`, and redeploy once.

```bash
git add deploy docs tests
git commit -m "docs: in-cluster acceptance results"
```

**If something fails**

| Symptom | Likely cause | What to do |
|---|---|---|
| `ErrImageNeverPull` | the image name on the node differs from `localhost/dgi:<tag>` | compare with `crictl images` inside the node (read-only); `build_image.sh` must load the archive podman saved |
| Refresh pod `Permission denied` writing `/data` | the default storage class makes root-owned directories | add an `initContainer` that `chown`s `/data` to 10001, or set the pod `fsGroup` (already 10001); kind's default local-path class normally makes world-writable directories |
| CronJob rejected for `timeZone` | the cluster is older than Kubernetes 1.27 | report it to investment's owner; do not change the cluster from here |
| `cannot reach the investment API` from the pod | the Service name or namespace differs | set `DGI_INVEST_API_URL` in both manifests |
| Web pod `Ready` but the page says "no data yet" | the first refresh has not run | `scripts/deploy.sh --refresh` or create a Job from the CronJob |
| `OOMKilled` | a limit below the measured peak | raise it from `docs/acceptance-local.md`'s formula, not by guess; above 1.5 GiB for the refresh means redesign |

---

### Task 26: Documentation, final verification and the pull request

**Files:**
- Modify: `docs/command.md`, `README.md`, `CLAUDE.md`, `docs/superpowers/plans/2026-10-05-dgi-screener.md` (status line)

**Interfaces:**
- Consumes: everything.
- Produces: docs that match what exists, a green suite, a pull request against `develop`.

- [ ] **Step 1: Update `docs/command.md`**

Replace its "Cluster (added in Task 25-27)" section with:

````markdown
## Cluster (namespace `dgi` in the kind cluster `invest`)

The cluster belongs to `../investment`; this project owns only namespace `dgi`. Scripts use investment's kubeconfig
(`../investment/deploy/terraform/.kube/invest.config`, override with `DGI_KUBECONFIG`) and always the `kind-invest`
context, never the current one.

```bash
scripts/install_tools.sh         # pinned kubectl and kind into .tools/ (checksum-verified)
scripts/deploy.sh                # build the image, load it into kind, apply deploy/k8s
scripts/deploy.sh --refresh      # the same, then run one refresh Job now and wait for it
scripts/open.sh                  # port-forward the UI to http://127.0.0.1:8760
scripts/delete.sh                # remove namespace dgi (the cache is rebuildable)
scripts/image_tag.sh             # the content-hash tag of the current tree
scripts/check_isolation.sh verify
```

Settings (env) also include `DGI_PAGE_LIMIT` (rows per API page, default 100000).
````

- [ ] **Step 2: Update `README.md`**

Append:

````markdown
## Running it

Locally, against `invest serve` on 127.0.0.1:8750:

```bash
uv sync
uv run dgi refresh      # pull, score, check, swap the cache in (about 15 s on the full data set)
uv run dgi serve        # http://127.0.0.1:8760
```

In the `invest` kind cluster: `scripts/install_tools.sh`, `scripts/deploy.sh --refresh`, then `scripts/open.sh`. A CronJob refreshes the cache daily at 07:00 Europe/Bucharest. See `docs/command.md`.

How the score works is on the `/methodology` page and in `config/scoring.yaml`. Measured numbers and what was checked against the real data are in `docs/acceptance-local.md`.
````

- [ ] **Step 3: Update `CLAUDE.md` and the plan index status**

In `CLAUDE.md`, replace the Status paragraph's first sentence with "Implemented per the plan in `docs/superpowers/plans/` (`2026-10-05-dgi-screener.md` and its phase files), accepted locally and in the `invest` cluster (`docs/acceptance-local.md`)." In the plan index `docs/superpowers/plans/2026-10-05-dgi-screener.md`, add a line `**Status:** implemented` under the title.

- [ ] **Step 4: Final verification**

```bash
uv run pytest
git status --short
scripts/check_isolation.sh verify
git log --oneline develop..HEAD | head -40
```
Expected: the whole suite passes (about 350 tests, none skipped), the working tree is clean apart from this task's edits, isolation is `OK`.

- [ ] **Step 5: Commit, and open the pull request when the owner says so**

```bash
git add docs README.md CLAUDE.md
git commit -m "docs: running it, cluster commands and final status"
```
Pushing and opening a pull request are visible to others: ask the owner first. When they agree:

```bash
git push -u origin feat/dgi-v1
gh pr create --base develop --title "DGI screener and company analyzer" --body "Implements docs/superpowers/specs/2026-10-05-dgi-screener-design.md per docs/superpowers/plans/2026-10-05-dgi-screener.md. Acceptance results: docs/acceptance-local.md."
```
