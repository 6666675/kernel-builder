#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
for script in scripts/*.sh tools/*.sh; do bash -n "$script"; done
python3 -m compileall -q scripts tools tests
python3 -m unittest discover -s tests -v
