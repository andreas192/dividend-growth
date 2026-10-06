#!/bin/sh
# Removes namespace dgi (and with it the disposable cache volume). Touches nothing else in the cluster.
set -eu
. "$(dirname "$0")/lib.sh"
require_cluster
kc delete namespace "$NAMESPACE" --wait --ignore-not-found
"$ROOT/scripts/check_isolation.sh" verify
