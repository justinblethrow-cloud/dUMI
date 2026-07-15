#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
JAR_FILE="$ROOT_DIR/umicollapse.jar"

for command in unzip grep awk; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "error: '$command' is required to verify the release artifact" >&2
        exit 1
    fi
done

if [[ ! -f "$JAR_FILE" ]]; then
    echo "error: umicollapse.jar is missing; run ./build.sh first" >&2
    exit 1
fi

embedded_hash=$(unzip -p "$JAR_FILE" META-INF/umicollapse-build.properties \
    | awk -F= '$1 == "source.sha256" { print $2 }')
current_hash=$("$ROOT_DIR/scripts/source-hash.sh")

if [[ -z $embedded_hash || $embedded_hash != "$current_hash" ]]; then
    echo "error: umicollapse.jar does not match the current production sources" >&2
    echo "embedded source hash: ${embedded_hash:-missing}" >&2
    echo "current source hash:  $current_hash" >&2
    exit 1
fi

if ! unzip -p "$JAR_FILE" META-INF/MANIFEST.MF | grep -q '^Main-Class: umicollapse.main.Main'; then
    echo "error: umicollapse.jar has an unexpected or missing Main-Class" >&2
    exit 1
fi

if unzip -Z1 "$JAR_FILE" | grep -q '^test/'; then
    echo "error: production artifact contains test classes" >&2
    exit 1
fi

echo "Artifact matches production source hash $current_hash and contains no test classes."
