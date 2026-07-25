#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
ZIP_EPOCH=315532800

if [[ -n ${SOURCE_DATE_EPOCH:-} ]]; then
    if [[ ! $SOURCE_DATE_EPOCH =~ ^[0-9]+$ ]]; then
        echo "error: SOURCE_DATE_EPOCH must be a non-negative integer" >&2
        exit 1
    fi
    epoch=$SOURCE_DATE_EPOCH
elif git -C "$ROOT_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
    epoch=$(git -C "$ROOT_DIR" show -s --format=%ct HEAD)
else
    epoch=$ZIP_EPOCH
fi

# ZIP/JAR timestamps cannot represent dates before 1980.
if (( epoch < ZIP_EPOCH )); then
    epoch=$ZIP_EPOCH
fi

printf '%s\n' "$epoch"
