#!/usr/bin/env bash
set -euo pipefail

if (( $# != 1 )); then
    echo "usage: $0 version" >&2
    exit 1
fi

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
LOCK_FILE="$ROOT_DIR/dependencies.lock"
SHA256="$ROOT_DIR/scripts/sha256.sh"
requested_version=$1
version=${requested_version#v}

if [[ ! $version =~ ^[0-9]+[.][0-9]+[.][0-9]+([.-][0-9A-Za-z][0-9A-Za-z.-]*)?$ ]]; then
    echo "error: release version must be a semantic version such as v1.2.0" >&2
    exit 1
fi

for command in git tar gzip ln mktemp python3 awk grep; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "error: '$command' is required to build a release archive" >&2
        exit 1
    fi
done

if ! git -C "$ROOT_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
    echo "error: a Git checkout is required to build a release" >&2
    exit 1
fi

release_commit=$(git -C "$ROOT_DIR" rev-parse HEAD)
worktree_status=$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all)
if [[ -n $worktree_status ]]; then
    echo "error: release source must exactly match a clean Git commit" >&2
    printf '%s\n' "$worktree_status" >&2
    exit 1
fi

required_tracked_paths=(
    LICENSE
    README.md
    THIRD_PARTY_NOTICES.md
    dependencies.lock
    scripts/check-local-markdown-links.py
    third_party/licenses
)
for relative_path in "${required_tracked_paths[@]}"; do
    if ! git -C "$ROOT_DIR" cat-file -e "HEAD:$relative_path" 2>/dev/null; then
        echo "error: required release path is not tracked at HEAD: $relative_path" >&2
        exit 1
    fi
done
for generated_path in umicollapse.jar BUILD-RECEIPT.properties SBOM.spdx lib; do
    if git -C "$ROOT_DIR" cat-file -e "HEAD:$generated_path" 2>/dev/null; then
        echo "error: generated release path must not be tracked: $generated_path" >&2
        exit 1
    fi
done
tracked_generated=$(git -C "$ROOT_DIR" ls-tree -r --name-only HEAD \
    | awk '
        /(^|\/)(build|dist|lib|test-output|__pycache__)\// ||
        /^\.tools\// ||
        /(^|\/)umicollapse[.]jar$/ ||
        /[.](class|py[co])$/ {
            print
        }
    ')
if [[ -n $tracked_generated ]]; then
    echo "error: generated build paths are tracked at HEAD" >&2
    printf '%s\n' "$tracked_generated" >&2
    exit 1
fi

if ! tar --help 2>&1 | grep -q -- '--sort'; then
    echo "error: GNU tar is required to create a reproducible release archive" >&2
    exit 1
fi

DUMI_VERSION="$version" "$ROOT_DIR/build.sh"
"$ROOT_DIR/scripts/verify-artifact.sh"
if ! grep -Fxq 'git.input.state=clean' "$ROOT_DIR/build/BUILD-RECEIPT.properties"; then
    echo "error: release build receipt does not describe a clean source tree" >&2
    exit 1
fi

dist_dir="$ROOT_DIR/dist"
package_name="dumi-$version"
package_dir="$dist_dir/$package_name"
rm -rf -- "$dist_dir"
mkdir -p "$dist_dir"

git -C "$ROOT_DIR" archive --format=tar --prefix="$package_name/" HEAD \
    | tar -xf - -C "$dist_dir"
mkdir -p "$package_dir/lib"

cp -- "$ROOT_DIR/umicollapse.jar" "$package_dir/umicollapse.jar"
cp -- "$ROOT_DIR/build/BUILD-RECEIPT.properties" "$package_dir/BUILD-RECEIPT.properties"

while read -r filename expected_sha256 url extra; do
    [[ -z ${filename:-} || $filename == \#* ]] && continue
    dependency="$ROOT_DIR/lib/$filename"
    if [[ ! -f $dependency ]]; then
        echo "error: locked dependency is missing after build: $filename" >&2
        exit 1
    fi
    actual_sha256=$("$SHA256" "$dependency")
    if [[ $actual_sha256 != "$expected_sha256" ]]; then
        echo "error: locked dependency checksum changed during release assembly: $filename" >&2
        exit 1
    fi
    cp -- "$dependency" "$package_dir/lib/$filename"
done < "$LOCK_FILE"

"$ROOT_DIR/scripts/generate-sbom.sh" \
    "$package_dir/SBOM.spdx" \
    "$ROOT_DIR/umicollapse.jar" \
    "$LOCK_FILE" \
    "$version" \
    "$ROOT_DIR/build/BUILD-RECEIPT.properties"

smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/dumi-release-smoke.XXXXXX")
trap 'rm -rf -- "$smoke_dir"' EXIT
ln -s "$package_dir/umicollapse" "$smoke_dir/umicollapse"
version_output=$("$smoke_dir/umicollapse" --version)
if [[ $version_output != *"$version"* ]]; then
    echo "error: release launcher symlink smoke test returned: $version_output" >&2
    exit 1
fi
rm -rf -- "$smoke_dir"
trap - EXIT

cp -- "$package_dir/SBOM.spdx" "$dist_dir/$package_name.spdx"
cp -- "$package_dir/BUILD-RECEIPT.properties" \
    "$dist_dir/BUILD-RECEIPT-$version.properties"

source_date_epoch=$("$ROOT_DIR/scripts/source-date-epoch.sh")
archive="$dist_dir/$package_name.tar.gz"
(
    cd "$dist_dir"
    tar \
        --sort=name \
        --mtime="@$source_date_epoch" \
        --owner=0 \
        --group=0 \
        --numeric-owner \
        --mode='u+rwX,go+rX,go-w' \
        --format=posix \
        --pax-option=delete=atime,delete=ctime \
        -cf - "$package_name" \
        | gzip -n > "$archive"
)

rm -rf -- "$package_dir"

archive_smoke_dir=$(mktemp -d "${TMPDIR:-/tmp}/dumi-archive-smoke.XXXXXX")
trap 'rm -rf -- "$archive_smoke_dir"' EXIT
tar -xzf "$archive" -C "$archive_smoke_dir"
archive_root="$archive_smoke_dir/$package_name"
ln -s "$archive_root/umicollapse" "$archive_smoke_dir/umicollapse"
archive_version_output=$("$archive_smoke_dir/umicollapse" --version)
archive_help_output=$("$archive_smoke_dir/umicollapse" --help)
if [[ $archive_version_output != *"$version"* ]]; then
    echo "error: archived launcher returned unexpected version: $archive_version_output" >&2
    exit 1
fi
if [[ $archive_help_output != *"Usage: umicollapse"* ]]; then
    echo "error: archived launcher help smoke test failed" >&2
    exit 1
fi
"$archive_root/scripts/verify-artifact.sh"
python3 "$archive_root/scripts/check-local-markdown-links.py" "$archive_root"
echo "Archived launcher smoke test passed: $archive_version_output"
rm -rf -- "$archive_smoke_dir"
trap - EXIT

checksum_file="$dist_dir/SHA256SUMS"
: > "$checksum_file"
while IFS= read -r artifact; do
    printf '%s  %s\n' "$("$SHA256" "$artifact")" "$(basename -- "$artifact")" \
        >> "$checksum_file"
done < <(
    find "$dist_dir" -maxdepth 1 -type f ! -name SHA256SUMS -print | LC_ALL=C sort
)

(
    cd "$dist_dir"
    "$SHA256" "$package_name.tar.gz" >/dev/null
    tar -tzf "$package_name.tar.gz" >/dev/null
)

echo "Release assets are ready in dist/:"
echo "Source commit: $release_commit"
find "$dist_dir" -maxdepth 1 -type f -print | LC_ALL=C sort
