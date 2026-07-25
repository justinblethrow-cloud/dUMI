#!/usr/bin/env bash
set -euo pipefail

if (( $# != 1 )) || [[ ! $1 =~ ^[0-9]+$ ]]; then
    echo "usage: $0 epoch-seconds" >&2
    exit 1
fi

epoch=$1
if formatted=$(LC_ALL=C date -u -d "@$epoch" '+%Y%m%d%H%M.%S' 2>/dev/null); then
    printf '%s\n' "$formatted"
elif formatted=$(LC_ALL=C date -u -r "$epoch" '+%Y%m%d%H%M.%S' 2>/dev/null); then
    printf '%s\n' "$formatted"
else
    echo "error: unable to convert epoch timestamp with the system date command" >&2
    exit 1
fi
