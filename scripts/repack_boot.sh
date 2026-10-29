#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_command python3
download_https "$BOOT_IMG_URL" "$STATE_DIR/baseline-boot.img"
python3 "$BUILDER_DIR/scripts/boot_image.py"
