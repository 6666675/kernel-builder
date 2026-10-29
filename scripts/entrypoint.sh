#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

case "${1:-}" in
  validate-prepare|validate-kernel)
    python3 "$BUILDER_DIR/scripts/builder_lib.py" "${1#validate-}"
    ;;
  prepare|kernel|compile)
    mode=$1
    [[ "$mode" != compile ]] || mode=kernel
    python3 "$BUILDER_DIR/scripts/builder_lib.py" "$mode"
    mkdir -p "$STATE_DIR" "$ARTIFACT_DIR"
    # A failed rerun cannot publish a receipt left by a previous successful run.
    rm -f -- "$STATE_DIR/$mode-success.json"
    bash "$BUILDER_DIR/scripts/sync_sources.sh"
    if [[ "$mode" == prepare ]]; then
      python3 "$BUILDER_DIR/scripts/file_overrides.py" prepare
    else
      bash "$BUILDER_DIR/scripts/build_gki.sh"
      if [[ "$1" == kernel ]]; then bash "$BUILDER_DIR/scripts/repack_boot.sh"; fi
    fi
    ;;
  repack)
    python3 "$BUILDER_DIR/scripts/builder_lib.py" kernel
    rm -f -- "$STATE_DIR/kernel-success.json"
    bash "$BUILDER_DIR/scripts/repack_boot.sh"
    ;;
  publish-prepare|publish-kernel)
    python3 "$BUILDER_DIR/scripts/cnb_artifacts.py" "${1#publish-}"
    ;;
  *) die '用法：entrypoint.sh prepare|kernel|compile|repack|publish-prepare|publish-kernel|validate-prepare|validate-kernel' ;;
esac
