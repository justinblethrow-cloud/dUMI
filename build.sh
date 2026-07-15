#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BUILD_DIR="$ROOT_DIR/build"
MAIN_CLASSES="$BUILD_DIR/classes"
TEST_CLASSES="$BUILD_DIR/test-classes"
CLASSPATH="$ROOT_DIR/lib/htsjdk-2.19.0.jar:$ROOT_DIR/lib/snappy-java-1.1.7.3.jar"

"$ROOT_DIR/scripts/bootstrap-dependencies.sh"

if [[ -n ${JAVA_HOME:-} && -x $JAVA_HOME/bin/javac && -x $JAVA_HOME/bin/jar ]]; then
    JAVAC="$JAVA_HOME/bin/javac"
    JAR="$JAVA_HOME/bin/jar"
elif [[ -x $ROOT_DIR/.tools/jdk/bin/javac && -x $ROOT_DIR/.tools/jdk/bin/jar ]]; then
    JAVAC="$ROOT_DIR/.tools/jdk/bin/javac"
    JAR="$ROOT_DIR/.tools/jdk/bin/jar"
elif command -v javac >/dev/null 2>&1 && command -v jar >/dev/null 2>&1; then
    JAVAC=$(command -v javac)
    JAR=$(command -v jar)
else
    echo "error: javac and jar were not found; install a JDK (Java 11 or newer)" >&2
    exit 1
fi

rm -rf -- "$BUILD_DIR"
mkdir -p "$MAIN_CLASSES/META-INF" "$TEST_CLASSES"

mapfile -d '' main_sources < <(find "$ROOT_DIR/src/umicollapse" -name '*.java' -print0 | sort -z)
mapfile -d '' test_sources < <(find "$ROOT_DIR/src/test" -name '*.java' -print0 | sort -z)

"$JAVAC" --release 11 -cp "$CLASSPATH" -d "$MAIN_CLASSES" "${main_sources[@]}"
"$JAVAC" --release 11 -cp "$MAIN_CLASSES:$CLASSPATH" -d "$TEST_CLASSES" "${test_sources[@]}"

source_hash=$("$ROOT_DIR/scripts/source-hash.sh")
printf 'source.sha256=%s\ntarget.java=11\n' "$source_hash" > "$MAIN_CLASSES/META-INF/dumi-build.properties"

"$JAR" -c -m "$ROOT_DIR/Manifest.txt" -f "$ROOT_DIR/umicollapse.jar" -C "$MAIN_CLASSES" .

echo "Built umicollapse.jar from source hash $source_hash"
