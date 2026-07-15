#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

find "$ROOT_DIR/src/umicollapse" -type f -name '*.java' -printf '%P\0' \
    | sort -z \
    | while IFS= read -r -d '' relative_path; do
        printf '%s  %s\n' "$(sha256sum "$ROOT_DIR/src/umicollapse/$relative_path" | awk '{print $1}')" "$relative_path"
    done \
    | sha256sum \
    | awk '{print $1}'
