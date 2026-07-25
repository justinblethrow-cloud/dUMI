#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
FIXTURE="$ROOT_DIR/test/fixtures/streaming-core.sam"
FASTQ_FIXTURE="$ROOT_DIR/test/fixtures/collapse.fastq"
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/dumi-cli-test.XXXXXX")
trap 'rm -rf -- "$TMP_DIR"' EXIT

fail(){
    echo "error: $*" >&2
    exit 1
}

assert_fails(){
    local expected=$1
    shift

    if "$@" > "$TMP_DIR/failure.stdout" 2> "$TMP_DIR/failure.stderr"; then
        fail "command unexpectedly succeeded: $*"
    fi

    grep -Fq -- "$expected" "$TMP_DIR/failure.stderr" \
        || {
            sed -n '1,20p' "$TMP_DIR/failure.stderr" >&2
            fail "failure did not contain '$expected': $*"
        }

    if grep -Fq 'UMI collapsing finished' "$TMP_DIR/failure.stdout"; then
        fail "failed command printed a success message: $*"
    fi
}

assert_fails_without_replacing(){
    local expected=$1
    local output=$2
    shift 2

    printf 'existing-output\n' > "$output"
    assert_fails "$expected" "$@"
    [[ $(cat "$output") == existing-output ]] \
        || fail "failed command replaced its destination: $*"
}

"$ROOT_DIR/umicollapse" --help > "$TMP_DIR/help.txt"
grep -Fq 'Usage: umicollapse <fastq|sam|bam>' "$TMP_DIR/help.txt"
"$ROOT_DIR/umicollapse" --version > "$TMP_DIR/version.txt"
grep -Eq '^dUMI [^[:space:]]+$' "$TMP_DIR/version.txt"

assert_fails "Unknown mode 'unknown'" \
    "$ROOT_DIR/umicollapse" unknown -i "$FIXTURE" -o "$TMP_DIR/unknown.sam"
assert_fails "Unknown option '--unknown'" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/unknown-option.sam" --unknown
assert_fails "Unexpected positional argument 'extra'" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/extra.sam" extra
assert_fails "Option --algo requires a value" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/missing-value.sam" --algo
assert_fails "Option -i may only be specified once" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -i "$FIXTURE" -o "$TMP_DIR/duplicate.sam"
assert_fails "Option --tag may only be specified once" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/duplicate-flag.sam" --tag --tag

assert_fails "Invalid --algo 'invalid'" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/algo.sam" --algo invalid
assert_fails "Invalid --data 'invalid'" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/data.sam" --data invalid
assert_fails "Invalid --data for -T 'ngrambktree'" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/parallel-data.sam" -T 2 --data ngrambktree
assert_fails "Invalid --merge 'invalid'" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/merge.sam" --merge invalid
assert_fails "Invalid --streaming-mode 'invalid'" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/streaming.sam" --streaming-mode invalid

assert_fails "-k must be zero or greater" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/k.sam" -k -1
assert_fails "-u must be -1 (autodetect) or a positive integer" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/umi-zero.sam" -u 0
assert_fails "-u must be -1 (autodetect) or a positive integer" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/umi-negative.sam" -u -2
assert_fails "-k must be smaller than the explicitly configured UMI length" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/edit-distance.sam" -u 4 -k 4
assert_fails "-p must be a finite, non-negative number" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/percentage.sam" -p NaN
assert_fails "-t must be a positive integer" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/thread-zero.sam" -t 0
assert_fails "-T must be a positive integer" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/thread-negative.sam" -T -1
assert_fails "-t and -T are mutually exclusive" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/thread-exclusive.sam" -t 2 -T 2

assert_fails "Cannot combine -t with --tag" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/t-tag.sam" -t 2 --tag
assert_fails "Cannot combine -T with --tag" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/T-tag.sam" -T 2 --tag
assert_fails "Cannot combine -t with --two-pass" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/t-two-pass.sam" -t 2 --two-pass
assert_fails "Streaming mode requires" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/streaming-tag.sam" --streaming-mode on --tag
assert_fails "--remove-unpaired and --remove-chimeric require --paired" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/remove.sam" --remove-unpaired
assert_fails "are only supported in sam or bam mode" \
    "$ROOT_DIR/umicollapse" fastq -i "$FIXTURE" -o "$TMP_DIR/fastq-option.fastq" --umi-sep _
assert_fails "--umi-sep requires a non-empty literal separator" \
    "$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/empty-separator.sam" --umi-sep ''
assert_fails "--merge mapqual is not supported in fastq mode" \
    "$ROOT_DIR/umicollapse" fastq -i "$FIXTURE" -o "$TMP_DIR/fastq-mapqual.fastq" --merge mapqual

cp -p "$FIXTURE" "$TMP_DIR/same.sam"
same_hash=$("$ROOT_DIR/scripts/sha256.sh" "$TMP_DIR/same.sam")
assert_fails "Input and output must be different files" \
    "$ROOT_DIR/umicollapse" sam -i "$TMP_DIR/same.sam" -o "$TMP_DIR/../$(basename "$TMP_DIR")/same.sam"
[[ $("$ROOT_DIR/scripts/sha256.sh" "$TMP_DIR/same.sam") == "$same_hash" ]] \
    || fail "same-path rejection modified the input"

ln "$TMP_DIR/same.sam" "$TMP_DIR/same-hardlink.sam"
assert_fails "Input and output must be different files" \
    "$ROOT_DIR/umicollapse" sam -i "$TMP_DIR/same.sam" -o "$TMP_DIR/same-hardlink.sam"
[[ $("$ROOT_DIR/scripts/sha256.sh" "$TMP_DIR/same.sam") == "$same_hash" ]] \
    || fail "hard-link rejection modified the input"

cat > "$TMP_DIR/malformed.sam" <<'EOF'
@HD	VN:1.6	SO:coordinate
@SQ	SN:chr1	LN:1000
ok_AAAA	0	chr1	1	60	4M	*	0	0	ACGT	IIII
bad	line
EOF
assert_fails_without_replacing "Error parsing text SAM file" "$TMP_DIR/runtime-failure.sam" \
    "$ROOT_DIR/umicollapse" sam -i "$TMP_DIR/malformed.sam" \
        -o "$TMP_DIR/runtime-failure.sam" -u 4 --streaming-mode off
if compgen -G "$TMP_DIR/.dumi-output-*" > /dev/null; then
    fail "runtime failure left a staged output behind"
fi

printf 'existing-output\n' > "$TMP_DIR/success.sam"
"$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/success.sam" \
    -u 4 --algo=dir --streaming-mode off > "$TMP_DIR/success.log"
grep -Fq 'UMI collapsing finished' "$TMP_DIR/success.log"
[[ $(head -c 1 "$TMP_DIR/success.sam") == @ ]] \
    || fail ".sam destination was not written as SAM"

"$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/default-format.out" \
    -u 4 --streaming-mode off > /dev/null
magic=$(od -An -tx1 -N2 "$TMP_DIR/default-format.out" | tr -d '[:space:]')
[[ $magic == 1f8b ]] || fail "non-.sam alignment destination was not written as BAM"

"$ROOT_DIR/umicollapse" sam -i "$FIXTURE" -o "$TMP_DIR/parallel-default.sam" \
    -u 4 -T 2 > /dev/null
[[ $(head -c 1 "$TMP_DIR/parallel-default.sam") == @ ]] \
    || fail "-T did not select a valid default data structure"

cat > "$TMP_DIR/literal-separator.sam" <<'EOF'
@HD	VN:1.6	SO:coordinate
@SQ	SN:chr1	LN:1000
read1.AAAA	0	chr1	1	60	4M	*	0	0	ACGT	IIII
read2.AAAA	0	chr1	1	50	4M	*	0	0	ACGT	IIII
EOF
"$ROOT_DIR/umicollapse" sam -i "$TMP_DIR/literal-separator.sam" \
    -o "$TMP_DIR/literal-separator-output.sam" --umi-sep . -u 4 --streaming-mode off > /dev/null
grep -Fq $'read1.AAAA\t' "$TMP_DIR/literal-separator-output.sam" \
    || fail "literal '.' UMI separator was not handled literally"

"$ROOT_DIR/umicollapse" fastq -i "$FASTQ_FIXTURE" \
    -o "$TMP_DIR/output.fastq.gz" -u 1 -k 1 > "$TMP_DIR/fastq.log"
gzip -cd "$TMP_DIR/output.fastq.gz" > "$TMP_DIR/output.fastq"
diff -u - "$TMP_DIR/output.fastq" <<'EOF' \
    || fail "FASTQ exact-duplicate/error-connected collapse contract changed"
@fqA1
AAA
+
JJJ
@fqC1
CCC
+
FFF
EOF
for metric in \
    $'Number of input reads\t6' \
    $'Number of unique reads\t3' \
    $'Number of reads after deduplicating\t2'; do
    grep -Fxq "$metric" "$TMP_DIR/fastq.log" \
        || fail "missing FASTQ collapse metric: $metric"
done

"$ROOT_DIR/umicollapse" fastq -i "$FASTQ_FIXTURE" \
    -o "$TMP_DIR/tagged.fastq" -u 1 -k 1 --tag > "$TMP_DIR/fastq-tag.log"
diff -u - "$TMP_DIR/tagged.fastq" <<'EOF' \
    || fail "FASTQ tag-mode cluster contract changed"
@fqA1 cluster_id=0 cluster_size=4 same_umi=3
AAA
+
JJJ
@fqA2 cluster_id=0
AAA
+
III
@fqA3 cluster_id=0
AAA
+
HHH
@fqB1 cluster_id=0 same_umi=1
AAT
+
GGG
@fqC1 cluster_id=1 cluster_size=2 same_umi=2
CCC
+
FFF
@fqC2 cluster_id=1
CCC
+
EEE
EOF
grep -Fxq $'Number of groups of reads\t2' "$TMP_DIR/fastq-tag.log" \
    || fail "FASTQ tag mode reported an unexpected cluster count"

echo "Passed: CLI validation and transactional output"
