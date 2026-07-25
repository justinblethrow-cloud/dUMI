#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SHA256="$ROOT_DIR/scripts/sha256.sh"

fixed_inputs=(
    Manifest.txt
    build.sh
    dependencies.lock
    scripts/bootstrap-dependencies.sh
    scripts/build-input-hash.sh
    scripts/format-epoch-touch.sh
    scripts/format-epoch-utc.sh
    scripts/sha256.sh
    scripts/source-date-epoch.sh
    scripts/source-hash.sh
)

{
    find "$ROOT_DIR/src/umicollapse" -type f -name '*.java' \
        | LC_ALL=C sort \
        | while IFS= read -r absolute_path; do
            relative_path=${absolute_path#"$ROOT_DIR"/}
            printf '%s  %s\n' "$("$SHA256" "$absolute_path")" "$relative_path"
        done

    for relative_path in "${fixed_inputs[@]}"; do
        absolute_path="$ROOT_DIR/$relative_path"
        if [[ ! -f $absolute_path ]]; then
            echo "error: build input is missing: $relative_path" >&2
            exit 1
        fi
        printf '%s  %s\n' "$("$SHA256" "$absolute_path")" "$relative_path"
    done
} | LC_ALL=C sort | "$SHA256"
