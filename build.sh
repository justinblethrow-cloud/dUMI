#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
BUILD_DIR="$ROOT_DIR/build"
MAIN_CLASSES="$BUILD_DIR/classes"
TEST_CLASSES="$BUILD_DIR/test-classes"
EFFECTIVE_MANIFEST="$BUILD_DIR/MANIFEST.MF"
BUILD_RECEIPT="$BUILD_DIR/BUILD-RECEIPT.properties"
JAR_FILE="$ROOT_DIR/umicollapse.jar"
LOCK_FILE="$ROOT_DIR/dependencies.lock"

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

dependency_names=()
while read -r filename expected_sha256 url extra; do
    [[ -z ${filename:-} || $filename == \#* ]] && continue

    if [[ -n ${extra:-} || ! $filename =~ ^[0-9A-Za-z._+-]+[.]jar$ ]]; then
        echo "error: malformed dependency entry in dependencies.lock: $filename" >&2
        exit 1
    fi

    if (( ${#dependency_names[@]} > 0 )); then
        for existing in "${dependency_names[@]}"; do
            if [[ $existing == "$filename" ]]; then
                echo "error: duplicate dependency filename in dependencies.lock: $filename" >&2
                exit 1
            fi
        done
    fi
    dependency_names+=("$filename")
done < "$LOCK_FILE"

if (( ${#dependency_names[@]} == 0 )); then
    echo "error: dependencies.lock does not contain any JAR dependencies" >&2
    exit 1
fi

dependency_paths=()
manifest_class_path=()
for filename in "${dependency_names[@]}"; do
    dependency_paths+=("$ROOT_DIR/lib/$filename")
    manifest_class_path+=("lib/$filename")
done

CLASSPATH=$(IFS=:; printf '%s' "${dependency_paths[*]}")
MANIFEST_CLASS_PATH=$(IFS=' '; printf '%s' "${manifest_class_path[*]}")

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
build_input_hash=$("$ROOT_DIR/scripts/build-input-hash.sh")
manifest_template_hash=$("$ROOT_DIR/scripts/sha256.sh" "$ROOT_DIR/Manifest.txt")
dependency_lock_hash=$("$ROOT_DIR/scripts/sha256.sh" "$LOCK_FILE")
source_date_epoch=$("$ROOT_DIR/scripts/source-date-epoch.sh")
archive_timestamp=$("$ROOT_DIR/scripts/format-epoch-utc.sh" "$source_date_epoch")
touch_timestamp=$("$ROOT_DIR/scripts/format-epoch-touch.sh" "$source_date_epoch")

if [[ -n ${DUMI_VERSION:-} ]]; then
    implementation_version=$DUMI_VERSION
elif git -C "$ROOT_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
    implementation_version="dev-$(git -C "$ROOT_DIR" rev-parse --short=12 HEAD)"
    if [[ -n $(git -C "$ROOT_DIR" status --porcelain --untracked-files=normal -- \
        build.sh Manifest.txt dependencies.lock scripts src/umicollapse) ]]; then
        implementation_version+="-dirty"
    fi
else
    implementation_version=dev-unknown
fi

if [[ ! $implementation_version =~ ^[0-9A-Za-z][0-9A-Za-z._+-]*$ ]]; then
    echo "error: invalid DUMI_VERSION '$implementation_version'" >&2
    exit 1
fi

write_manifest_line() {
    local line=$1

    while (( ${#line} > 70 )); do
        printf '%s\r\n' "${line:0:70}"
        line=" ${line:70}"
    done
    printf '%s\r\n' "$line"
}

{
    while IFS= read -r line || [[ -n $line ]]; do
        [[ -z $line ]] && continue
        [[ $line == Class-Path:* || $line == Implementation-Version:* || $line == Created-By:* ]] \
            && continue
        write_manifest_line "$line"
    done < "$ROOT_DIR/Manifest.txt"
    write_manifest_line "Class-Path: $MANIFEST_CLASS_PATH"
    write_manifest_line "Implementation-Version: $implementation_version"
    write_manifest_line "Created-By: dUMI reproducible build"
    printf '\r\n'
} > "$EFFECTIVE_MANIFEST"

effective_manifest_hash=$("$ROOT_DIR/scripts/sha256.sh" "$EFFECTIVE_MANIFEST")
javac_version=$("$JAVAC" -version 2>&1 | head -n 1)
jar_version=$("$JAR" --version 2>&1 | head -n 1)
git_commit=unknown
git_input_state=unknown
if git -C "$ROOT_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
    git_commit=$(git -C "$ROOT_DIR" rev-parse HEAD)
    git_input_state=clean
    if [[ -n $(git -C "$ROOT_DIR" status --porcelain --untracked-files=normal -- \
        build.sh Manifest.txt dependencies.lock scripts src/umicollapse) ]]; then
        git_input_state=dirty
    fi
fi

printf '%s\n' \
    "receipt.format=1" \
    "source.sha256=$source_hash" \
    "manifest.template.sha256=$manifest_template_hash" \
    "manifest.effective.sha256=$effective_manifest_hash" \
    "dependencies.lock.sha256=$dependency_lock_hash" \
    "build.inputs.sha256=$build_input_hash" \
    "compiler.javac=$javac_version" \
    "archiver.jar=$jar_version" \
    "target.java=11" \
    "git.commit=$git_commit" \
    "git.input.state=$git_input_state" \
    "implementation.version=$implementation_version" \
    "source.date.epoch=$source_date_epoch" \
    "archive.timestamp=$archive_timestamp" \
    > "$BUILD_RECEIPT"

cp -- "$EFFECTIVE_MANIFEST" "$MAIN_CLASSES/META-INF/MANIFEST.MF"
cp -- "$BUILD_RECEIPT" "$MAIN_CLASSES/META-INF/dumi-build.properties"
TZ=UTC find "$MAIN_CLASSES" -type f -exec touch -t "$touch_timestamp" {} +

jar_entries=()
while IFS= read -r entry; do
    jar_entries+=("$entry")
done < <(cd "$MAIN_CLASSES" && find . -type f -print | LC_ALL=C sort)

temporary_jar="$BUILD_DIR/umicollapse.jar.tmp"
trap 'rm -f -- "$temporary_jar"' EXIT
(
    cd "$MAIN_CLASSES"
    TZ=UTC "$JAR" --create --no-manifest --file "$temporary_jar" "${jar_entries[@]}"
)
mv -- "$temporary_jar" "$JAR_FILE"
trap - EXIT

echo "Built umicollapse.jar from build-input hash $build_input_hash"
