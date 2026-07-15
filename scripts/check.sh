#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

"$ROOT_DIR/build.sh"
"$ROOT_DIR/scripts/verify-artifact.sh"
"$ROOT_DIR/test.sh"

if [[ -n ${JAVA_HOME:-} && -x $JAVA_HOME/bin/javap ]]; then
    JAVAP="$JAVA_HOME/bin/javap"
elif command -v javap >/dev/null 2>&1; then
    JAVAP=$(command -v javap)
else
    echo "error: javap was not found; install a JDK (Java 11 or newer)" >&2
    exit 1
fi

major_version=$("$JAVAP" -verbose -classpath "$ROOT_DIR/build/classes" umicollapse.main.Main \
    | awk '/major version:/ { print $3; exit }')

if [[ $major_version != 55 ]]; then
    echo "error: expected Java 11 class major version 55, found $major_version" >&2
    exit 1
fi

echo "Full verification passed with Java 11-compatible bytecode."
