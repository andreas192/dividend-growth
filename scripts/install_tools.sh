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
