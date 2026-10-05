#!/bin/sh
# Forwards the web UI to http://127.0.0.1:8760 (no host port mapping; that is fixed when the cluster is created).
set -eu
. "$(dirname "$0")/lib.sh"
require_cluster
echo "UI on http://127.0.0.1:8760 (Ctrl-C to stop)"
exec kubectl --kubeconfig "$KUBECONFIG_FILE" --context "$CONTEXT" -n "$NAMESPACE" port-forward --address 127.0.0.1 svc/web 8760:8760
