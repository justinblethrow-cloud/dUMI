#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SHA256="$ROOT_DIR/scripts/sha256.sh"

for command in cmp mktemp; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "error: '$command' is required for the reproducible-build check" >&2
        exit 1
    fi
done

temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/dumi-reproducible.XXXXXX")
trap 'rm -rf -- "$temporary_dir"' EXIT

version=${DUMI_VERSION:-reproducibility-check}

DUMI_VERSION="$version" "$ROOT_DIR/build.sh"
cp -- "$ROOT_DIR/umicollapse.jar" "$temporary_dir/first.jar"
first_hash=$("$SHA256" "$temporary_dir/first.jar")

DUMI_VERSION="$version" "$ROOT_DIR/build.sh"
second_hash=$("$SHA256" "$ROOT_DIR/umicollapse.jar")

if ! cmp -s "$temporary_dir/first.jar" "$ROOT_DIR/umicollapse.jar"; then
    echo "error: consecutive builds produced different JAR files" >&2
    echo "first:  $first_hash" >&2
    echo "second: $second_hash" >&2
    exit 1
fi

"$ROOT_DIR/scripts/verify-artifact.sh"

echo "Reproducible-build check passed: $first_hash"
