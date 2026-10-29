#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ -d "$KERNEL_SRC" ]] || die '未找到 common 内核目录，请先同步源码'
mkdir -p "$DIST_DIR" "$OUT_DIR" "$STATE_DIR" "$CACHE_ROOT/bazel"
[[ -z "$(find "$DIST_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]] || die 'DIST_DIR 必须为空，不能复用旧产物'

if [[ -n "${SOURCE_OVERRIDES_URL:-}" ]]; then
  download_https "$SOURCE_OVERRIDES_URL" "$STATE_DIR/overrides.tar.gz" 67108864
  python3 "$BUILDER_DIR/scripts/file_overrides.py" check
fi
if [[ -n "${CUSTOM_SCRIPT_URL:-}" ]]; then
  download_https "$CUSTOM_SCRIPT_URL" "$STATE_DIR/customize.sh" 1048576
  bash -n "$STATE_DIR/customize.sh"
  log '执行自定义脚本'
  (cd "$KERNEL_SRC" && bash "$STATE_DIR/customize.sh")
fi
if [[ -n "${SOURCE_OVERRIDES_URL:-}" ]]; then
  python3 "$BUILDER_DIR/scripts/file_overrides.py" apply
fi

case "$GKI_ARCH" in arm64) target_arch=aarch64 ;; x86_64) target_arch=x86_64 ;; *) die '不支持的架构' ;; esac
cd "$GKI_ROOT"
if [[ -x tools/bazel ]]; then
  target="${BAZEL_TARGET:-//common:kernel_${target_arch}_dist}"
  [[ "$target" == //* && "$target" != *[[:space:]]* ]] || die 'BAZEL_TARGET 必须是完整的 Bazel 标签'
  tools/bazel query "$target" >/dev/null || die "当前源码不存在构建目标 $target，请设置 BAZEL_TARGET"
  extra=()
  if [[ -n "${EXTRA_BAZEL_ARGS:-}" ]]; then read -r -a extra <<< "$EXTRA_BAZEL_ARGS"; fi
  command=(tools/bazel run "--disk_cache=$CACHE_ROOT/bazel" "--jobs=$GKI_BUILD_JOBS" "${extra[@]}" --lto=thin "$target" -- "--destdir=$DIST_DIR")
  log "使用 Kleaf 编译 $target"
  "${command[@]}"
elif [[ -f build/build.sh && -f "common/build.config.gki.$target_arch" ]]; then
  log "使用 legacy build.sh 编译 $target_arch"
  export BUILD_CONFIG="common/build.config.gki.$target_arch" LTO=thin MAKEFLAGS="-j$GKI_BUILD_JOBS"
  bash build/build.sh
else
  die '源码没有可用的 Kleaf 或 legacy GKI 编译入口'
fi
log '内核编译完成'
