#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CLASSPATH="$ROOT_DIR/build/test-classes:$ROOT_DIR/umicollapse.jar:$ROOT_DIR/lib/*"

if [[ ! -d "$ROOT_DIR/build/test-classes" ]]; then
    echo "error: test classes are missing; run ./build.sh first" >&2
    exit 1
fi

if [[ -n ${JAVA:-} ]]; then
    JAVA_BIN=$JAVA
elif [[ -n ${JAVA_HOME:-} && -x $JAVA_HOME/bin/java ]]; then
    JAVA_BIN="$JAVA_HOME/bin/java"
elif [[ -x $ROOT_DIR/.tools/jdk/bin/java ]]; then
    JAVA_BIN="$ROOT_DIR/.tools/jdk/bin/java"
else
    JAVA_BIN=java
fi

java_opts=()
if [[ -n ${UMICOLLAPSE_JAVA_OPTS:-} ]]; then
    read -r -a java_opts <<< "$UMICOLLAPSE_JAVA_OPTS"
fi

exec "$JAVA_BIN" "${java_opts[@]}" -cp "$CLASSPATH" "$@"
