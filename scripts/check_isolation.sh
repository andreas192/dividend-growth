#!/bin/sh
# Checks that work in this repo has not changed anything ../capital-trading or ../investment depends on.
#   scripts/check_isolation.sh baseline   record the current state (run before installing anything)
#   scripts/check_isolation.sh verify     re-record and diff against the baseline; exit 1 on any difference
# Read-only: it only inspects. If the only difference is in a "repo" section, check whether you were
# editing that repo yourself.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CT="$ROOT/../capital-trading"
INV="$ROOT/../investment"
BASELINE="${TMPDIR:-/tmp}/dgi-isolation-baseline.txt"

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
  echo "## podman containers and machine"
  podman ps -a --format '{{.Names}} {{.Image}} {{.Ports}}' 2>/dev/null | sort
  podman machine list --format '{{.Name}} running={{.Running}}' 2>/dev/null
  echo "## capital-trading repo"
  git -C "$CT" rev-parse HEAD 2>/dev/null
  git -C "$CT" status --porcelain 2>/dev/null
  shasum "$CT/requirements.txt" 2>/dev/null
  echo "## investment repo"
  git -C "$INV" rev-parse HEAD 2>/dev/null
  git -C "$INV" status --porcelain 2>/dev/null
}

case "${1:-}" in
  baseline)
    snapshot > "$BASELINE"
    echo "Baseline saved to $BASELINE ($(wc -l < "$BASELINE" | tr -d ' ') lines)"
    ;;
  verify)
    if [ ! -f "$BASELINE" ]; then
      echo "No baseline found. Run: scripts/check_isolation.sh baseline" >&2
      exit 2
    fi
    CURRENT="$(mktemp)"
    snapshot > "$CURRENT"
    if diff -u "$BASELINE" "$CURRENT"; then
      echo "OK: ../capital-trading and ../investment are unchanged."
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
