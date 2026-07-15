#!/usr/bin/env bash
set -euo pipefail

if (( $# > 1 )); then
    echo "usage: $0 [file]" >&2
    exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
    if (( $# == 1 )); then
        sha256sum -- "$1" | awk '{print $1}'
    else
        sha256sum | awk '{print $1}'
    fi
elif command -v shasum >/dev/null 2>&1; then
    if (( $# == 1 )); then
        shasum -a 256 -- "$1" | awk '{print $1}'
    else
        shasum -a 256 | awk '{print $1}'
    fi
else
    echo "error: sha256sum or shasum is required" >&2
    exit 1
fi
