#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
RECORDS=${1:-100000}
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/dumi-benchmark.XXXXXX")
SHA256="$ROOT_DIR/scripts/sha256.sh"
trap 'rm -rf -- "$TMP_DIR"' EXIT

if ! [[ $RECORDS =~ ^[1-9][0-9]*$ ]]; then
    echo "usage: $0 [positive-record-count]" >&2
    exit 1
fi

if command -v gtime >/dev/null 2>&1 && gtime --version 2>&1 | grep -qi 'GNU time'; then
    GNU_TIME=$(command -v gtime)
elif /usr/bin/time --version 2>&1 | grep -qi 'GNU time'; then
    GNU_TIME=/usr/bin/time
else
    echo "error: the smoke benchmark requires GNU time ('time' on Linux or 'gtime' from Homebrew)" >&2
    exit 1
fi

input="$TMP_DIR/input.sam"
awk -v records="$RECORDS" 'BEGIN {
    print "@HD\tVN:1.6\tSO:coordinate"
    print "@SQ\tSN:chr1\tLN:" (records + 100)
    bases[0] = "AAAA"; bases[1] = "CCCC"; bases[2] = "GGGG"; bases[3] = "TTTT"
    for(i = 1; i <= records; i++)
        printf "read%09d_%s\t0\tchr1\t%d\t60\t10M\t*\t0\t0\tAAAAAAAAAA\tIIIIIIIIII\n", i, bases[i % 4], i
}' > "$input"

printf 'mode\trecords\telapsed_seconds\tmax_rss_kb\trecord_sha256\n'
for mode in off on; do
    output="$TMP_DIR/$mode.sam"
    metrics="$TMP_DIR/$mode.metrics"
    "$GNU_TIME" -f '%e\t%M' -o "$metrics" \
        "$ROOT_DIR/umicollapse" sam -i "$input" -o "$output" --streaming-mode "$mode" -u 4 \
        > "$TMP_DIR/$mode.log"
    record_hash=$(awk '!/^@/' "$output" | "$SHA256")
    printf '%s\t%s\t%s\t%s\n' "$mode" "$RECORDS" "$(cat "$metrics")" "$record_hash"
done

off_hash=$(awk '!/^@/' "$TMP_DIR/off.sam" | "$SHA256")
on_hash=$(awk '!/^@/' "$TMP_DIR/on.sam" | "$SHA256")

if [[ $off_hash != "$on_hash" ]]; then
    echo "error: benchmark outputs differ" >&2
    exit 1
fi
