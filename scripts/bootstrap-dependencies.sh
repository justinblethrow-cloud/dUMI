#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
LOCK_FILE="$ROOT_DIR/dependencies.lock"
LIB_DIR="$ROOT_DIR/lib"

if ! command -v curl >/dev/null 2>&1; then
    echo "error: 'curl' is required to fetch dependencies" >&2
    exit 1
fi

SHA256="$ROOT_DIR/scripts/sha256.sh"
"$SHA256" </dev/null >/dev/null

mkdir -p "$LIB_DIR"

while read -r filename expected_sha256 url; do
    [[ -z ${filename:-} || $filename == \#* ]] && continue

    destination="$LIB_DIR/$filename"

    if [[ -f "$destination" ]]; then
        actual_sha256=$("$SHA256" "$destination")
        if [[ $actual_sha256 == "$expected_sha256" ]]; then
            continue
        fi
        echo "warning: replacing $filename because its checksum does not match dependencies.lock" >&2
    fi

    temporary="$destination.tmp.$$"
    trap 'rm -f -- "$temporary"' EXIT
    curl --fail --location --retry 3 --proto '=https' --tlsv1.2 --output "$temporary" "$url"

    actual_sha256=$("$SHA256" "$temporary")
    if [[ $actual_sha256 != "$expected_sha256" ]]; then
        echo "error: checksum mismatch for $filename" >&2
        exit 1
    fi

    mv -- "$temporary" "$destination"
    trap - EXIT
done < "$LOCK_FILE"

echo "Dependencies are present and checksum-verified."
