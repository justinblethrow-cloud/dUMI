#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if [[ ! -d "$ROOT_DIR/build/test-classes" ]]; then
    echo "error: test classes are missing; run ./build.sh first" >&2
    exit 1
fi

classpath_entries=(
    "$ROOT_DIR/build/test-classes"
    "$ROOT_DIR/umicollapse.jar"
)
while read -r filename expected_sha256 url extra; do
    [[ -z ${filename:-} || $filename == \#* ]] && continue
    dependency="$ROOT_DIR/lib/$filename"
    if [[ ! -f $dependency ]]; then
        echo "error: locked dependency is missing: $filename; run ./build.sh first" >&2
        exit 1
    fi
    classpath_entries+=("$dependency")
done < "$ROOT_DIR/dependencies.lock"
CLASSPATH=$(IFS=:; printf '%s' "${classpath_entries[*]}")

if [[ -n ${JAVA:-} ]]; then
    JAVA_BIN=$JAVA
elif [[ -n ${JAVA_HOME:-} && -x $JAVA_HOME/bin/java ]]; then
    JAVA_BIN="$JAVA_HOME/bin/java"
elif [[ -x $ROOT_DIR/.tools/jdk/bin/java ]]; then
    JAVA_BIN="$ROOT_DIR/.tools/jdk/bin/java"
else
    JAVA_BIN=java
fi

if [[ -n ${UMICOLLAPSE_JAVA_OPTS:-} ]]; then
    java_opts=()
    read -r -a java_opts <<< "$UMICOLLAPSE_JAVA_OPTS"
    exec "$JAVA_BIN" "${java_opts[@]}" -cp "$CLASSPATH" "$@"
fi

exec "$JAVA_BIN" -cp "$CLASSPATH" "$@"
