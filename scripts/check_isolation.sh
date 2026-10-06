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

# Decides once, before anything is written, whether the cluster section runs. Sets CLUSTER_SKIP (a reason, empty when the
# cluster is inspected). Never fails open: a skip is announced on the terminal, and a kubeconfig that exists but lacks the
# kind-invest context or whose cluster cannot be reached stops the script.
cluster_preflight() {
  CLUSTER_SKIP=""
  if [ ! -x "$KUBECTL" ]; then
    CLUSTER_SKIP="pinned kubectl missing at $KUBECTL; run scripts/install_tools.sh"
  elif [ ! -f "$KUBECONFIG_FILE" ]; then
    CLUSTER_SKIP="no invest kubeconfig at $KUBECONFIG_FILE"
  fi
  if [ -n "$CLUSTER_SKIP" ]; then
    echo "NOTE: cluster section skipped ($CLUSTER_SKIP)" >&2
    return
  fi
  "$KUBECTL" --kubeconfig "$KUBECONFIG_FILE" config get-contexts -o name 2>/dev/null | grep -qx kind-invest || {
    echo "error: $KUBECONFIG_FILE has no context kind-invest; refusing to continue" >&2
    exit 1
  }
  NAMESPACES="$("$KUBECTL" --kubeconfig "$KUBECONFIG_FILE" --context kind-invest get namespaces -o name)" || {
    echo "error: cannot list namespaces in kind-invest (cluster down or unreachable); cannot check isolation" >&2
    exit 1
  }
}

cluster_snapshot() {
  # explicit kubeconfig and context, as in scripts/lib.sh
  [ -z "$CLUSTER_SKIP" ] || { echo "(no invest cluster to inspect)"; return; }
  kc() { "$KUBECTL" --kubeconfig "$KUBECONFIG_FILE" --context kind-invest "$@"; }
  echo "## invest cluster namespaces (dgi excluded)"
  echo "$NAMESPACES" | grep -v '/dgi$' | sort
  for ns in $(echo "$NAMESPACES" | sed 's|^namespace/||' | grep -v '^dgi$' | sort); do
    echo "## objects in $ns"
    out="$(kc -n "$ns" get deployments,statefulsets,daemonsets,cronjobs,services,configmaps,persistentvolumeclaims -o name)" || {
      echo "error: cannot list objects in namespace $ns; cannot check isolation" >&2
      exit 1
    }
    echo "$out" | sort
    out="$(kc -n "$ns" get deployments -o custom-columns=NAME:.metadata.name,GENERATION:.metadata.generation --no-headers)" || {
      echo "error: cannot list deployments in namespace $ns; cannot check isolation" >&2
      exit 1
    }
    echo "$out" | sort
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
  echo "## podman containers (the kind node of the invest cluster is expected and unchanged)"
  podman ps -a --format '{{.Names}} {{.Image}} {{.Ports}}' 2>/dev/null | sort
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
    cluster_preflight
    PARTIAL="$(mktemp)"
    trap 'rm -f "$PARTIAL"' EXIT
    snapshot > "$PARTIAL" || exit 1
    mv "$PARTIAL" "$BASELINE"   # a failed snapshot never replaces a good baseline
    echo "Baseline saved to $BASELINE ($(wc -l < "$BASELINE" | tr -d ' ') lines)"
    ;;
  verify)
    if [ ! -f "$BASELINE" ]; then
      echo "No baseline found. Run: scripts/check_isolation.sh baseline" >&2
      exit 2
    fi
    cluster_preflight
    CURRENT="$(mktemp)"
    trap 'rm -f "$CURRENT"' EXIT
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
