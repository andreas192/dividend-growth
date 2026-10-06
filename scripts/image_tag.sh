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
