#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BUILD_DIR="$ROOT_DIR/build"
MAIN_CLASSES="$BUILD_DIR/classes"
TEST_CLASSES="$BUILD_DIR/test-classes"
LOCK_FILE="$ROOT_DIR/dependencies.lock"
JAR_FILE="$ROOT_DIR/umicollapse.jar"

"$ROOT_DIR/scripts/bootstrap-dependencies.sh"

dependency_names=()
while read -r filename expected_sha256 url extra; do
    [[ -z ${filename:-} || $filename == \#* ]] && continue

    if [[ -n ${extra:-} || ! $filename =~ ^[0-9A-Za-z._+-]+[.]jar$ ]]; then
        echo "error: malformed dependency entry in dependencies.lock: $filename" >&2
        exit 1
    fi

    for existing in "${dependency_names[@]}"; do
        if [[ $existing == "$filename" ]]; then
            echo "error: duplicate dependency filename in dependencies.lock: $filename" >&2
            exit 1
        fi
    done
    dependency_names+=("$filename")
done < "$LOCK_FILE"

if (( ${#dependency_names[@]} == 0 )); then
    echo "error: dependencies.lock does not contain any JAR dependencies" >&2
    exit 1
fi

dependency_paths=()
for filename in "${dependency_names[@]}"; do
    dependency_paths+=("$ROOT_DIR/lib/$filename")
done
CLASSPATH=$(IFS=:; printf '%s' "${dependency_paths[*]}")

if [[ -n ${JAVA_HOME:-} && -x $JAVA_HOME/bin/javac && -x $JAVA_HOME/bin/jar ]]; then
    JAVAC="$JAVA_HOME/bin/javac"
    JAR="$JAVA_HOME/bin/jar"
elif command -v javac >/dev/null 2>&1 && command -v jar >/dev/null 2>&1; then
    JAVAC=$(command -v javac)
    JAR=$(command -v jar)
else
    echo "error: javac and jar were not found; install a JDK (Java 11 or newer)" >&2
    exit 1
fi

rm -rf -- "$BUILD_DIR"
mkdir -p "$MAIN_CLASSES/META-INF" "$TEST_CLASSES"

main_sources=()
while IFS= read -r source; do
    main_sources+=("$source")
done < <(find "$ROOT_DIR/src/umicollapse" -type f -name '*.java' | LC_ALL=C sort)

test_sources=()
while IFS= read -r source; do
    test_sources+=("$source")
done < <(find "$ROOT_DIR/src/test" -type f -name '*.java' | LC_ALL=C sort)

"$JAVAC" --release 11 -Xlint:all -Werror \
    -cp "$CLASSPATH" -d "$MAIN_CLASSES" "${main_sources[@]}"
"$JAVAC" --release 11 -Xlint:all -Werror \
    -cp "$MAIN_CLASSES:$CLASSPATH" -d "$TEST_CLASSES" "${test_sources[@]}"

source_hash=$("$ROOT_DIR/scripts/source-hash.sh")
printf 'source.sha256=%s\ntarget.java=11\n' "$source_hash" > "$MAIN_CLASSES/META-INF/umicollapse-build.properties"

temporary_jar="$BUILD_DIR/umicollapse.jar.tmp"
trap 'rm -f -- "$temporary_jar"' EXIT
"$JAR" -c -m "$ROOT_DIR/Manifest.txt" -f "$temporary_jar" -C "$MAIN_CLASSES" .
mv -- "$temporary_jar" "$JAR_FILE"
trap - EXIT

echo "Built umicollapse.jar from source hash $source_hash"
