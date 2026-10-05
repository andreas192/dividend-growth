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
