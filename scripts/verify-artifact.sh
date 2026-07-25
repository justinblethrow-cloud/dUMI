#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
JAR_FILE="$ROOT_DIR/umicollapse.jar"
SHA256="$ROOT_DIR/scripts/sha256.sh"

for command in unzip awk cmp; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "error: '$command' is required to verify the release artifact" >&2
        exit 1
    fi
done

if [[ ! -f "$JAR_FILE" ]]; then
    echo "error: umicollapse.jar is missing; run ./build.sh first" >&2
    exit 1
fi

receipt_property() {
    local key=$1
    unzip -p "$JAR_FILE" META-INF/dumi-build.properties \
        | awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }'
}

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
                if (found) {
                    print value
                }
            }
        '
}

embedded_source_hash=$(receipt_property source.sha256)
embedded_build_input_hash=$(receipt_property build.inputs.sha256)
embedded_manifest_template_hash=$(receipt_property manifest.template.sha256)
embedded_manifest_hash=$(receipt_property manifest.effective.sha256)
embedded_lock_hash=$(receipt_property dependencies.lock.sha256)
embedded_target=$(receipt_property target.java)
embedded_commit=$(receipt_property git.commit)
embedded_javac=$(receipt_property compiler.javac)
embedded_jar=$(receipt_property archiver.jar)
embedded_version=$(receipt_property implementation.version)
embedded_timestamp=$(receipt_property archive.timestamp)

current_source_hash=$("$ROOT_DIR/scripts/source-hash.sh")
current_build_input_hash=$("$ROOT_DIR/scripts/build-input-hash.sh")
current_manifest_template_hash=$("$SHA256" "$ROOT_DIR/Manifest.txt")
current_lock_hash=$("$SHA256" "$ROOT_DIR/dependencies.lock")
actual_manifest_hash=$(unzip -p "$JAR_FILE" META-INF/MANIFEST.MF | "$SHA256")

verification_failed=false
check_equal() {
    local label=$1
    local embedded=$2
    local expected=$3

    if [[ -z $embedded || $embedded != "$expected" ]]; then
        echo "error: artifact $label does not match" >&2
        echo "embedded: ${embedded:-missing}" >&2
        echo "expected: $expected" >&2
        verification_failed=true
    fi
}

check_equal "production source hash" "$embedded_source_hash" "$current_source_hash"
check_equal "complete build-input hash" "$embedded_build_input_hash" "$current_build_input_hash"
check_equal "manifest template hash" "$embedded_manifest_template_hash" "$current_manifest_template_hash"
check_equal "dependency-lock hash" "$embedded_lock_hash" "$current_lock_hash"
check_equal "effective manifest hash" "$embedded_manifest_hash" "$actual_manifest_hash"
check_equal "target Java version" "$embedded_target" "11"
if git -C "$ROOT_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
    check_equal "Git commit" "$embedded_commit" "$(git -C "$ROOT_DIR" rev-parse HEAD)"
fi

for required_value in \
    "git commit:$embedded_commit" \
    "javac version:$embedded_javac" \
    "jar version:$embedded_jar" \
    "implementation version:$embedded_version" \
    "archive timestamp:$embedded_timestamp"; do
    label=${required_value%%:*}
    value=${required_value#*:}
    if [[ -z $value ]]; then
        echo "error: artifact receipt is missing $label" >&2
        verification_failed=true
    fi
done

expected_class_path=()
while read -r filename expected_sha256 url extra; do
    [[ -z ${filename:-} || $filename == \#* ]] && continue
    expected_class_path+=("lib/$filename")
    dependency="$ROOT_DIR/lib/$filename"
    if [[ ! -f $dependency ]]; then
        echo "error: locked dependency is missing: $filename" >&2
        verification_failed=true
    else
        actual_dependency_hash=$("$SHA256" "$dependency")
        if [[ $actual_dependency_hash != "$expected_sha256" ]]; then
            echo "error: locked dependency checksum does not match: $filename" >&2
            verification_failed=true
        fi
    fi
done < "$ROOT_DIR/dependencies.lock"
if (( ${#expected_class_path[@]} == 0 )); then
    echo "error: dependencies.lock does not contain any JAR dependencies" >&2
    exit 1
fi
expected_class_path_value=$(IFS=' '; printf '%s' "${expected_class_path[*]}")

check_equal "manifest Main-Class" \
    "$(manifest_attribute Main-Class)" "umicollapse.main.Main"
check_equal "manifest Class-Path" \
    "$(manifest_attribute Class-Path)" "$expected_class_path_value"
check_equal "manifest Implementation-Version" \
    "$(manifest_attribute Implementation-Version)" "$embedded_version"
check_equal "manifest Created-By" \
    "$(manifest_attribute Created-By)" "dUMI reproducible build"

if unzip -Z1 "$JAR_FILE" | awk '/^test\// { found = 1 } END { exit !found }'; then
    echo "error: production artifact contains test classes" >&2
    verification_failed=true
fi

if [[ -f "$ROOT_DIR/build/BUILD-RECEIPT.properties" ]]; then
    temporary_receipt=$(mktemp "${TMPDIR:-/tmp}/dumi-receipt.XXXXXX")
    trap 'rm -f -- "$temporary_receipt"' EXIT
    unzip -p "$JAR_FILE" META-INF/dumi-build.properties > "$temporary_receipt"
    if ! cmp -s "$temporary_receipt" "$ROOT_DIR/build/BUILD-RECEIPT.properties"; then
        echo "error: embedded build receipt differs from build/BUILD-RECEIPT.properties" >&2
        verification_failed=true
    fi
    rm -f -- "$temporary_receipt"
    trap - EXIT
fi

if [[ $verification_failed == true ]]; then
    exit 1
fi

echo "Artifact matches build inputs $current_build_input_hash and contains no test classes."
