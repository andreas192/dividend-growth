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
