#!/bin/sh
# Vendors Apache ECharts into the web app so the UI needs no CDN at run time.
# The npm tarball is checked against the integrity hash the npm registry publishes for that version.
#   scripts/vendor_echarts.sh            # ECHARTS_VERSION defaults to the pinned version below
#   ECHARTS_VERSION=5.6.0 DGI_VENDOR_DEST=/some/dir scripts/vendor_echarts.sh
set -eu

VERSION="${ECHARTS_VERSION:-5.6.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${DGI_VENDOR_DEST:-$ROOT/src/dgi/web/static/vendor}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

json_field() {  # json_field FILE dist.key
  uv run --no-project python -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["dist"][sys.argv[2]])' "$1" "$2"
}

curl -fsSL "https://registry.npmjs.org/echarts/$VERSION" -o "$TMP/meta.json"
TARBALL="$(json_field "$TMP/meta.json" tarball)"
EXPECTED="$(json_field "$TMP/meta.json" integrity)"
curl -fsSL "$TARBALL" -o "$TMP/echarts.tgz"
ACTUAL="sha512-$(openssl dgst -sha512 -binary "$TMP/echarts.tgz" | openssl base64 -A)"
if [ "$ACTUAL" != "$EXPECTED" ]; then
  echo "integrity mismatch for echarts@$VERSION: expected $EXPECTED, got $ACTUAL" >&2
  exit 1
fi
tar -xzf "$TMP/echarts.tgz" -C "$TMP" package/dist/echarts.min.js package/LICENSE package/NOTICE
mkdir -p "$DEST"
cp "$TMP/package/dist/echarts.min.js" "$DEST/echarts.min.js"
cp "$TMP/package/LICENSE" "$DEST/ECHARTS_LICENSE"
cp "$TMP/package/NOTICE" "$DEST/ECHARTS_NOTICE"
echo "$VERSION" > "$DEST/ECHARTS_VERSION"
shasum -a 256 "$DEST/echarts.min.js" | awk '{print $1}' > "$DEST/echarts.min.js.sha256"
echo "vendored echarts@$VERSION into $DEST"
