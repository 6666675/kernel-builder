#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
for tool in repo git python3 flock; do require_command "$tool"; done
git check-ref-format --branch "$GKI_BRANCH" >/dev/null || die 'GKI_BRANCH 不是有效分支名'
python3 - "$GKI_ROOT" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
if root.exists() and any(root.iterdir()):
    sys.exit('源码目录必须为空；每次使用新的工作区，缓存只保存 mirror 和 Bazel 数据。')
PY
cache_key=$(python3 - <<'PY'
import hashlib, os
print(hashlib.sha256((os.environ['GKI_MANIFEST_URL'] + '\n' + os.environ['GKI_BRANCH']).encode()).hexdigest())
PY
)
mirror="$CACHE_ROOT/mirrors/$cache_key"
mkdir -p "$mirror" "$GKI_ROOT" "$STATE_DIR"
sync_with_retry() {
  local attempt
  for attempt in 1 2 3; do
    if repo sync -c --no-clone-bundle --fail-fast -j"$GKI_SYNC_JOBS"; then return 0; fi
    [[ "$attempt" != 3 ]] || return 1
    log "源码同步失败，重试 $attempt/3"
    sleep "$((attempt * 5))"
  done
}
(
  flock -x 9
  cd "$mirror"
  log '更新 repo mirror 缓存'
  repo init --mirror -u "$GKI_MANIFEST_URL" -b "$GKI_BRANCH" --no-clone-bundle
  sync_with_retry
  cd "$GKI_ROOT"
  repo init -u "$GKI_MANIFEST_URL" -b "$GKI_BRANCH" --reference="$mirror" --dissociate --no-clone-bundle
  sync_with_retry
  repo manifest -r -o "$STATE_DIR/manifest.xml"
  repo list -p > "$STATE_DIR/projects.txt"
) 9>"$mirror.sync.lock"
log '源码同步完成'
