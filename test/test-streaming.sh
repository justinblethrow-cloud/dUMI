#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
FIXTURES="$ROOT_DIR/test/fixtures"
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/umicollapse-streaming-test.XXXXXX")
trap 'rm -rf -- "$TMP_DIR"' EXIT

fail(){
    echo "error: $*" >&2
    exit 1
}

inspect(){
    "$ROOT_DIR/run.sh" test.InspectAlignmentFile "$@"
}

assert_records_equal(){
    local expected=$1
    local actual=$2
    local description=$3

    inspect records "$expected" > "$TMP_DIR/expected.records"
    inspect records "$actual" > "$TMP_DIR/actual.records"
    diff -u "$TMP_DIR/expected.records" "$TMP_DIR/actual.records" \
        || fail "$description"
}

assert_fails_without_replacing(){
    local expected_message=$1
    local output=$2
    shift 2

    printf 'existing-output\n' > "$output"
    if "$@" > "$TMP_DIR/failure.stdout" 2> "$TMP_DIR/failure.stderr"; then
        fail "command unexpectedly succeeded: $*"
    fi
    grep -Fq -- "$expected_message" "$TMP_DIR/failure.stderr" \
        || fail "failure did not contain '$expected_message'"
    [[ $(cat "$output") == existing-output ]] \
        || fail "failed streaming command replaced its destination"
}

core=$FIXTURES/streaming-core.sam
common=(sam -i "$core" --keep-unmapped -u 4 -k 1)

"$ROOT_DIR/umicollapse" "${common[@]}" -o "$TMP_DIR/off.sam" --streaming-mode off > "$TMP_DIR/off.log"
"$ROOT_DIR/umicollapse" "${common[@]}" -o "$TMP_DIR/on.sam" --streaming-mode on > "$TMP_DIR/on.log"
"$ROOT_DIR/umicollapse" "${common[@]}" -o "$TMP_DIR/auto.sam" --streaming-mode auto > "$TMP_DIR/auto.log"
"$ROOT_DIR/umicollapse" "${common[@]}" -o "$TMP_DIR/default.sam" > "$TMP_DIR/default.log"

assert_records_equal "$TMP_DIR/off.sam" "$TMP_DIR/on.sam" "streaming on differs from legacy output"
assert_records_equal "$TMP_DIR/off.sam" "$TMP_DIR/auto.sam" "streaming auto differs from legacy output"
assert_records_equal "$TMP_DIR/off.sam" "$TMP_DIR/default.sam" "default mode differs from explicit streaming off"

[[ $(inspect sort-order "$TMP_DIR/off.sam") == coordinate ]] \
    || fail "legacy path did not preserve coordinate sort order"
[[ $(inspect sort-order "$TMP_DIR/on.sam") == unsorted ]] \
    || fail "streaming path did not declare its reordered output unsorted"
[[ $(inspect sort-order "$TMP_DIR/default.sam") == coordinate ]] \
    || fail "default mode did not preserve coordinate sort order"
grep -Fq 'Using coordinate-sorted single-end streaming fast path' "$TMP_DIR/on.log"
grep -Fq 'Using coordinate-sorted single-end streaming fast path' "$TMP_DIR/auto.log"
if grep -Fq 'streaming fast path' "$TMP_DIR/off.log"; then
    fail "streaming marker appeared with --streaming-mode off"
fi
if grep -Fq 'streaming fast path' "$TMP_DIR/default.log"; then
    fail "streaming marker appeared without an explicit streaming mode"
fi

[[ $(inspect count "$TMP_DIR/on.sam") == 6 ]] || fail "unexpected core output record count"
inspect names "$TMP_DIR/on.sam" > "$TMP_DIR/core.names"
diff -u - "$TMP_DIR/core.names" <<'EOF' || fail "unexpected core output representatives"
after_GGGG
chr2a_ACGT
n2_CCCC
p500_TTTT
pboundary_AAAA
u1_NNNN
EOF

for metric in \
    $'Number of input reads\t12' \
    $'Number of removed unmapped reads\t1' \
    $'Number of unremoved reads\t11' \
    $'Number of unique alignment positions\t5' \
    $'Average number of UMIs per alignment position\t1.2' \
    $'Max number of UMIs over all alignment positions\t2' \
    $'Number of reads after deduplicating\t5'; do
    grep -Fxq "$metric" "$TMP_DIR/on.log" || fail "missing streaming metric: $metric"
    grep -Fxq "$metric" "$TMP_DIR/off.log" || fail "missing legacy metric: $metric"
done

for algorithm in dir adj cc; do
    "$ROOT_DIR/umicollapse" "${common[@]}" -o "$TMP_DIR/off-$algorithm.sam" --streaming-mode off --algo "$algorithm" > /dev/null
    "$ROOT_DIR/umicollapse" "${common[@]}" -o "$TMP_DIR/on-$algorithm.sam" --streaming-mode on --algo "$algorithm" > /dev/null
    assert_records_equal "$TMP_DIR/off-$algorithm.sam" "$TMP_DIR/on-$algorithm.sam" "streaming parity failed for algorithm $algorithm"
done

for merge in mapqual avgqual any; do
    "$ROOT_DIR/umicollapse" "${common[@]}" -o "$TMP_DIR/off-$merge.sam" --streaming-mode off --merge "$merge" > /dev/null
    "$ROOT_DIR/umicollapse" "${common[@]}" -o "$TMP_DIR/on-$merge.sam" --streaming-mode on --merge "$merge" > /dev/null
    assert_records_equal "$TMP_DIR/off-$merge.sam" "$TMP_DIR/on-$merge.sam" "streaming parity failed for merge $merge"
done

for mode in off on; do
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURES/mapqual-tie-forward.sam" \
        -o "$TMP_DIR/mapqual-forward-$mode.sam" --streaming-mode "$mode" -u 4 -k 0 > /dev/null
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURES/mapqual-tie-reverse.sam" \
        -o "$TMP_DIR/mapqual-reverse-$mode.sam" --streaming-mode "$mode" -u 4 -k 0 > /dev/null
    assert_records_equal "$TMP_DIR/mapqual-forward-$mode.sam" "$TMP_DIR/mapqual-reverse-$mode.sam" \
        "mapqual tie representative depends on coordinate-tie order in $mode mode"
    [[ $(inspect names "$TMP_DIR/mapqual-forward-$mode.sam") == alpha_AAAA ]] \
        || fail "mapqual tie did not select the stable lexical representative in $mode mode"
done

for data in naive combo ngram delete trie bktree sortbktree ngrambktree sortngrambktree fenwickbktree; do
    "$ROOT_DIR/umicollapse" "${common[@]}" -o "$TMP_DIR/off-$data.sam" --streaming-mode off --data "$data" > /dev/null
    "$ROOT_DIR/umicollapse" "${common[@]}" -o "$TMP_DIR/on-$data.sam" --streaming-mode on --data "$data" > /dev/null
    assert_records_equal "$TMP_DIR/off-$data.sam" "$TMP_DIR/on-$data.sam" "streaming parity failed for data structure $data"
done

"$ROOT_DIR/umicollapse" "${common[@]}" -o "$TMP_DIR/on.bam" --streaming-mode on > /dev/null
assert_records_equal "$TMP_DIR/off.sam" "$TMP_DIR/on.bam" "BAM streaming output differs from SAM legacy output"

"$ROOT_DIR/run.sh" test.CreateIndexedBam "$core" "$TMP_DIR/core-input.bam"
"$ROOT_DIR/umicollapse" bam -i "$TMP_DIR/core-input.bam" -o "$TMP_DIR/bam-input-on.sam" \
    --streaming-mode on --keep-unmapped -u 4 -k 1 > /dev/null
assert_records_equal "$TMP_DIR/off.sam" "$TMP_DIR/bam-input-on.sam" \
    "streaming BAM input differs from SAM legacy output"

"$ROOT_DIR/run.sh" test.CreateIndexedBam "$FIXTURES/paired-coordinate.sam" "$TMP_DIR/paired-input.bam"
"$ROOT_DIR/umicollapse" bam -i "$TMP_DIR/paired-input.bam" -o "$TMP_DIR/paired-output.bam" \
    --paired --streaming-mode off -u 4 > /dev/null
[[ $(inspect count "$TMP_DIR/paired-output.bam") == 4 ]] \
    || fail "paired indexed-BAM path did not retain both mates"
inspect records "$TMP_DIR/paired-output.bam" > "$TMP_DIR/paired.records"
grep -Fq $'pair1_AAAA\t147\tchr1\t150' "$TMP_DIR/paired.records" \
    || fail "paired cross-reference flush did not recover the first mate"
grep -Fq $'pair2_CCCC\t147\tchr2\t150' "$TMP_DIR/paired.records" \
    || fail "paired final pass did not recover the last mate"

"$ROOT_DIR/umicollapse" sam -i "$FIXTURES/declared-unsorted.sam" -o "$TMP_DIR/unsorted-auto.sam" --streaming-mode auto > "$TMP_DIR/unsorted-auto.log"
if grep -Fq 'streaming fast path' "$TMP_DIR/unsorted-auto.log"; then
    fail "auto mode streamed an input declared unsorted"
fi
assert_fails_without_replacing 'observed sortOrder=unsorted' "$TMP_DIR/unsorted-on.sam" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURES/declared-unsorted.sam" -o "$TMP_DIR/unsorted-on.sam" --streaming-mode on

"$ROOT_DIR/umicollapse" sam -i "$FIXTURES/lying-coordinate.sam" -o "$TMP_DIR/lying-off.sam" --streaming-mode off > /dev/null
"$ROOT_DIR/umicollapse" sam -i "$FIXTURES/lying-coordinate.sam" -o "$TMP_DIR/lying-auto.sam" --streaming-mode auto > "$TMP_DIR/lying-auto.log" 2> "$TMP_DIR/lying-auto.err"
assert_records_equal "$TMP_DIR/lying-off.sam" "$TMP_DIR/lying-auto.sam" "auto mode did not safely fall back for false coordinate metadata"
grep -Fq 'retrying with --streaming-mode off' "$TMP_DIR/lying-auto.err" \
    || fail "auto mode did not report coordinate-order fallback"
assert_fails_without_replacing 'requires records to be in coordinate order' "$TMP_DIR/lying-on.sam" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURES/lying-coordinate.sam" -o "$TMP_DIR/lying-on.sam" --streaming-mode on

"$ROOT_DIR/umicollapse" sam -i "$FIXTURES/lag-exceeded.sam" -o "$TMP_DIR/lag-off.sam" --streaming-mode off > /dev/null
"$ROOT_DIR/umicollapse" sam -i "$FIXTURES/lag-exceeded.sam" -o "$TMP_DIR/lag-auto.sam" --streaming-mode auto > /dev/null 2> "$TMP_DIR/lag-auto.err"
assert_records_equal "$TMP_DIR/lag-off.sam" "$TMP_DIR/lag-auto.sam" "auto mode did not safely fall back for excessive clipping"
grep -Fq 'retrying with --streaming-mode off' "$TMP_DIR/lag-auto.err" \
    || fail "auto mode did not report clipping fallback"
assert_fails_without_replacing 'positive-lag window is too small' "$TMP_DIR/lag-on.sam" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURES/lag-exceeded.sam" -o "$TMP_DIR/lag-on.sam" --streaming-mode on

"$ROOT_DIR/umicollapse" sam -i "$core" -o "$TMP_DIR/two-pass.sam" --streaming-mode off --two-pass -u 4 > /dev/null
[[ $(inspect sort-order "$TMP_DIR/two-pass.sam") == coordinate ]] \
    || fail "two-pass path did not preserve coordinate sort order"

assert_fails_without_replacing '--streaming-mode on cannot be combined with --two-pass' "$TMP_DIR/incompatible.sam" \
    "$ROOT_DIR/umicollapse" sam -i "$core" -o "$TMP_DIR/incompatible.sam" --streaming-mode on --two-pass
assert_fails_without_replacing '--streaming-mode is only supported in sam or bam mode' "$TMP_DIR/incompatible.fastq" \
    "$ROOT_DIR/umicollapse" fastq -i "$core" -o "$TMP_DIR/incompatible.fastq" --streaming-mode on

for options in '--tag' '--paired' '-t 2' '-T 2 --data naive'; do
    read -r -a option_array <<< "$options"
    output="$TMP_DIR/incompatible-${options//[^A-Za-z0-9]/_}.sam"
    assert_fails_without_replacing 'Streaming mode requires' "$output" \
        "$ROOT_DIR/umicollapse" sam -i "$core" -o "$output" --streaming-mode on "${option_array[@]}"
done

echo "Passed: streaming integration and compatibility matrix"
