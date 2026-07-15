#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_DIR="$ROOT_DIR/src/umicollapse"
SHA256="$ROOT_DIR/scripts/sha256.sh"

find "$SOURCE_DIR" -type f -name '*.java' \
    | LC_ALL=C sort \
    | while IFS= read -r absolute_path; do
        relative_path=${absolute_path#"$SOURCE_DIR"/}
        printf '%s  %s\n' "$("$SHA256" "$absolute_path")" "$relative_path"
    done \
    | "$SHA256"
