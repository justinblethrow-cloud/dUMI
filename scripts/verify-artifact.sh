#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
JAR_FILE="$ROOT_DIR/umicollapse.jar"
SHA256="$ROOT_DIR/scripts/sha256.sh"

for command in unzip awk; do
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

manifest_attribute() {
    local key=$1
    unzip -p "$JAR_FILE" META-INF/MANIFEST.MF \
        | awk -v key="$key" '
            {
                sub(/\r$/, "")
            }
            index($0, key ": ") == 1 {
                found = 1
                value = substr($0, length(key) + 3)
                next
            }
            found && /^ / {
                value = value substr($0, 2)
                next
            }
            found {
                exit
            }
            END {
                if(found)
                    print value
            }
        '
}

verification_failed=false
check_equal() {
    local label=$1
    local actual=$2
    local expected=$3

    if [[ -z $actual || $actual != "$expected" ]]; then
        echo "error: artifact $label does not match" >&2
        echo "actual:   ${actual:-missing}" >&2
        echo "expected: $expected" >&2
        verification_failed=true
    fi
}

expected_class_path=()
while read -r filename expected_sha256 url extra; do
    [[ -z ${filename:-} || $filename == \#* ]] && continue
    expected_class_path+=("lib/$filename")

    dependency="$ROOT_DIR/lib/$filename"
    if [[ ! -f $dependency ]]; then
        echo "error: locked dependency is missing: $filename" >&2
        verification_failed=true
    elif [[ $("$SHA256" "$dependency") != "$expected_sha256" ]]; then
        echo "error: locked dependency checksum does not match: $filename" >&2
        verification_failed=true
    fi
done < "$ROOT_DIR/dependencies.lock"

if (( ${#expected_class_path[@]} == 0 )); then
    echo "error: dependencies.lock does not contain any JAR dependencies" >&2
    exit 1
fi
expected_class_path_value=$(IFS=' '; printf '%s' "${expected_class_path[*]}")

check_equal "Main-Class" \
    "$(manifest_attribute Main-Class)" "umicollapse.main.Main"
check_equal "Class-Path" \
    "$(manifest_attribute Class-Path)" "$expected_class_path_value"
check_equal "Implementation-Title" \
    "$(manifest_attribute Implementation-Title)" "UMICollapse"

if unzip -Z1 "$JAR_FILE" | awk '/^test\// { found = 1 } END { exit !found }'; then
    echo "error: production artifact contains test classes" >&2
    verification_failed=true
fi

if [[ $verification_failed == true ]]; then
    exit 1
fi

echo "Artifact matches production sources and locked dependencies; it contains no test classes."
