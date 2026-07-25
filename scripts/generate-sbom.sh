#!/usr/bin/env bash
set -euo pipefail

if (( $# != 5 )); then
    echo "usage: $0 output.spdx umicollapse.jar dependencies.lock version build-receipt" >&2
    exit 1
fi

OUTPUT_FILE=$1
JAR_FILE=$2
LOCK_FILE=$3
VERSION=$4
BUILD_RECEIPT=$5
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SHA256="$ROOT_DIR/scripts/sha256.sh"

for input in "$JAR_FILE" "$LOCK_FILE" "$BUILD_RECEIPT"; do
    if [[ ! -f $input ]]; then
        echo "error: required SBOM input is missing: $input" >&2
        exit 1
    fi
done

if [[ ! $VERSION =~ ^[0-9A-Za-z][0-9A-Za-z._+-]*$ ]]; then
    echo "error: invalid version '$VERSION'" >&2
    exit 1
fi

receipt_property() {
    local key=$1
    awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }' \
        "$BUILD_RECEIPT"
}

build_input_hash=$(receipt_property build.inputs.sha256)
source_date_epoch=$(receipt_property source.date.epoch)
if [[ -z $build_input_hash || -z $source_date_epoch ]]; then
    echo "error: build receipt is missing build.inputs.sha256 or source.date.epoch" >&2
    exit 1
fi
created=$("$ROOT_DIR/scripts/format-epoch-utc.sh" "$source_date_epoch")
jar_hash=$("$SHA256" "$JAR_FILE")

output_dir=$(cd -- "$(dirname -- "$OUTPUT_FILE")" && pwd)
output_name=$(basename -- "$OUTPUT_FILE")
temporary_output="$output_dir/.$output_name.tmp.$$"
trap 'rm -f -- "$temporary_output"' EXIT

{
    printf '%s\n' \
        "SPDXVersion: SPDX-2.3" \
        "DataLicense: CC0-1.0" \
        "SPDXID: SPDXRef-DOCUMENT" \
        "DocumentName: dUMI-$VERSION" \
        "DocumentNamespace: https://spdx.org/spdxdocs/dumi-$VERSION-$build_input_hash" \
        "Creator: Tool: dUMI-build-release" \
        "Created: $created" \
        "" \
        "PackageName: dUMI" \
        "SPDXID: SPDXRef-Package-dUMI" \
        "PackageVersion: $VERSION" \
        "PackageFileName: umicollapse.jar" \
        "PackageDownloadLocation: NOASSERTION" \
        "FilesAnalyzed: false" \
        "PackageChecksum: SHA256: $jar_hash" \
        "PackageLicenseConcluded: NOASSERTION" \
        "PackageLicenseDeclared: MIT" \
        "PackageCopyrightText: NOASSERTION" \
        "Relationship: SPDXRef-DOCUMENT DESCRIBES SPDXRef-Package-dUMI"

    dependency_index=0
    while read -r filename expected_sha256 url extra; do
        [[ -z ${filename:-} || $filename == \#* ]] && continue
        dependency_index=$((dependency_index + 1))
        dependency_id="SPDXRef-Dependency-$dependency_index"
        package_name=$filename
        package_version=
        purl=
        declared_license=NOASSERTION

        maven_path=${url#*/maven2/}
        if [[ $maven_path != "$url" ]]; then
            maven_directory=${maven_path%/*}
            package_version=${maven_directory##*/}
            artifact_directory=${maven_directory%/*}
            package_name=${artifact_directory##*/}
            group_path=${artifact_directory%/*}
            group_id=${group_path//\//.}
            purl="pkg:maven/$group_id/$package_name@$package_version"
            if [[ $purl == pkg:maven/org.xerial.snappy/snappy-java@* ]]; then
                declared_license=Apache-2.0
            fi
        fi

        printf '\n%s\n' \
            "PackageName: $package_name" \
            "SPDXID: $dependency_id"
        if [[ -n $package_version ]]; then
            printf '%s\n' "PackageVersion: $package_version"
        fi
        printf '%s\n' \
            "PackageFileName: lib/$filename" \
            "PackageDownloadLocation: $url" \
            "FilesAnalyzed: false" \
            "PackageChecksum: SHA256: $expected_sha256" \
            "PackageLicenseConcluded: NOASSERTION" \
            "PackageLicenseDeclared: $declared_license" \
            "PackageCopyrightText: NOASSERTION"
        if [[ -n $purl ]]; then
            printf '%s\n' "ExternalRef: PACKAGE-MANAGER purl $purl"
        fi
        printf '%s\n' "Relationship: SPDXRef-Package-dUMI DEPENDS_ON $dependency_id"
    done < "$LOCK_FILE"
} > "$temporary_output"

mv -- "$temporary_output" "$OUTPUT_FILE"
trap - EXIT

echo "Generated SPDX SBOM: $OUTPUT_FILE"
