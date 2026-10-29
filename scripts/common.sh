#!/usr/bin/env bash
set -euo pipefail

BUILDER_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export BUILDER_DIR
export WORKSPACE="${CNB_BUILD_WORKSPACE:-${WORKSPACE:-$PWD}}"
export GKI_ROOT="${GKI_ROOT:-$WORKSPACE/gki-src}"
export KERNEL_SRC="$GKI_ROOT/common"
export DIST_DIR="${DIST_DIR:-$WORKSPACE/kernel-dist}"
export OUT_DIR="${OUT_DIR:-$WORKSPACE/kernel-out}"
export ARTIFACT_DIR="${ARTIFACT_DIR:-$WORKSPACE/artifact}"
export STATE_DIR="${STATE_DIR:-$WORKSPACE/.gki-state}"
export CACHE_ROOT="${GKI_CACHE_DIR:-/cache/gki-builder}"
export CI_COMMIT_SHA="${CNB_COMMIT:-${CI_COMMIT_SHA:-}}"
export ARCH="${GKI_ARCH:-}"
export GKI_SYNC_JOBS="${GKI_SYNC_JOBS:-8}"
export GKI_BUILD_JOBS="${GKI_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN)}"
export GIT_TERMINAL_PROMPT=0

die() { printf '错误：%s\n' "$*" >&2; exit 1; }
log() { printf '[kernel-builder] %s\n' "$*"; }
require_command() { command -v "$1" >/dev/null || die "缺少依赖：$1"; }
download_https() {
  curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
    --connect-timeout 30 --max-time 1800 --retry 3 --max-filesize "${3:-2147483648}" \
    --output "$2" "$1"
}
