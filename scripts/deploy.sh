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

# Render first, so a kustomize failure is reported as one and nothing half-rendered reaches kubectl apply.
"$KUBECTL" kustomize "$RENDER" > "$RENDER.yaml" || { echo "error: kustomize failed to render deploy/k8s" >&2; rm -f "$RENDER.yaml"; exit 1; }
trap 'rm -rf "$RENDER" "$RENDER.yaml"' EXIT
kc apply -f "$RENDER.yaml"
kc -n "$NAMESPACE" rollout status deployment/web --timeout=180s

STATUS=0
if [ "${1:-}" = "--refresh" ]; then
  JOB="refresh-now-$(date +%s)"
  kc -n "$NAMESPACE" create job "$JOB" --from=cronjob/refresh
  # Wait for the Job to succeed or fail (backoffLimit 0 makes a failed pod a failed Job); never longer than its 1 h deadline.
  DEADLINE=$(( $(date +%s) + 3660 ))
  STATUS=1
  while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    # a transient API error counts as "not yet"; the next poll retries and the deadline still bounds the loop
    SUCCEEDED="$(kc -n "$NAMESPACE" get "job/$JOB" -o jsonpath='{.status.succeeded}')" || true
    FAILED="$(kc -n "$NAMESPACE" get "job/$JOB" -o jsonpath='{.status.failed}')" || true
    if [ -n "$SUCCEEDED" ] && [ "$SUCCEEDED" -gt 0 ]; then STATUS=0; break; fi
    if [ -n "$FAILED" ] && [ "$FAILED" -gt 0 ]; then break; fi
    sleep 10
  done
  kc -n "$NAMESPACE" logs "job/$JOB" --tail=50 || true
  [ "$STATUS" -eq 0 ] || echo "error: refresh job $JOB failed or did not finish in time" >&2
fi

"$ROOT/scripts/check_isolation.sh" verify
exit "$STATUS"
