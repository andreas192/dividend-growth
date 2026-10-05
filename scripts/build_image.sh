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
"$KIND" load image-archive "$TMP/dgi.tar" --name "$CLUSTER"
echo "image $IMAGE loaded into kind cluster $CLUSTER"
