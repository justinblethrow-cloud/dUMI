# Allocation-profile tooling

This directory contains the public, diagnostic allocation-profile tooling for
dUMI. It is deliberately separate from the performance benchmark:

- [`allocation-only.jfc`](allocation-only.jfc) enables only Java Flight
  Recorder's `jdk.ObjectAllocationSample` event, with stack traces and the
  documented high sampling throttle of 1,000 samples per second.
- [`aggregate_jfr.py`](aggregate_jfr.py) converts one to 32 recordings into a
  bounded JSON aggregate without retaining individual events or local
  identity.

The profile identifies where sampled allocation pressure remains in one exact
run configuration. It does not measure runtime, peak resident memory, retained
heap, or production-wide performance.

## Reproducible sparse-singleton profile

Use an exact committed dUMI revision, Java 21, `samtools`, and Python 3.9 or
newer. Build that revision in a clean detached worktree. In the commands below,
replace the three angle-bracket placeholders; keep the workload and JVM
settings unchanged for the reportable profile.

```bash
set -euo pipefail

ROOT=<DUMI_REPOSITORY>
REV=<40_HEX_COMMIT>
JDK21=<JDK_21_HOME>
PROFILE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/dumi-allocation-profile.XXXXXX")
SOURCE="$PROFILE_ROOT/source"
RUN="$PROFILE_ROOT/run"

git -C "$ROOT" worktree add --detach "$SOURCE" "$REV"
mkdir -p "$RUN"

(
    cd "$SOURCE"
    JAVA_HOME="$JDK21" ./build.sh
    JAVA_HOME="$JDK21" ./scripts/verify-artifact.sh
    test "$(git rev-parse HEAD)" = "$REV"
    grep -Fx 'git.input.state=clean' build/BUILD-RECEIPT.properties
)

cd "$RUN"
python3 "$SOURCE/scripts/benchmark/generate_workload.py" sparse \
    --records 1000000 \
    --seed 1729 \
    --output sparse.sam \
    --metadata workload.json \
    2> generator.stderr
samtools view -b -o sparse.bam sparse.sam
samtools quickcheck -v sparse.bam

for repetition in 1 2 3; do
    env -u JAVA_TOOL_OPTIONS -u _JAVA_OPTIONS -u JDK_JAVA_OPTIONS -u CLASSPATH \
        LANG=C LC_ALL=C TZ=UTC \
        "$JDK21/bin/java" \
        -XX:-UsePerfData \
        -server \
        -Xms64m \
        -Xmx4g \
        -XX:ActiveProcessorCount=8 \
        "-XX:StartFlightRecording=filename=profile-${repetition}.jfr,settings=$SOURCE/scripts/profile/allocation-only.jfc,dumponexit=true,maxsize=16m" \
        -jar "$SOURCE/umicollapse.jar" \
        bam \
        -i sparse.bam \
        -o "output-${repetition}.bam" \
        -u 12 \
        -k 1 \
        -p 0.5 \
        --algo dir \
        --data ngrambktree \
        --merge mapqual \
        --streaming-mode on \
        > "run-${repetition}.stdout" \
        2> "run-${repetition}.stderr"

    grep -Fxq 'Using coordinate-sorted single-end streaming fast path' \
        "run-${repetition}.stdout"
    if grep -Fq 'retrying with --streaming-mode off' \
        "run-${repetition}.stderr"; then
        echo "error: unexpected streaming fallback" >&2
        exit 1
    fi

    python3 "$SOURCE/scripts/benchmark/semantic_check.py" \
        --reference sparse.bam \
        --report "semantic-${repetition}.json" \
        "output-${repetition}.bam"
done

python3 "$SOURCE/scripts/profile/aggregate_jfr.py" \
    --jfr-tool "$JDK21/bin/jfr" \
    --configuration "$SOURCE/scripts/profile/allocation-only.jfc" \
    --output allocation-aggregate.json \
    profile-1.jfr profile-2.jfr profile-3.jfr

python3 - workload.json \
    semantic-1.json semantic-2.json semantic-3.json <<'PY'
import json
import hashlib
from pathlib import Path
import sys

workload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = workload["expected_output"]
safe_runs = []

for run_number, path_string in enumerate(sys.argv[2:], start=1):
    result = json.loads(Path(path_string).read_text(encoding="utf-8"))
    assert result["quickcheck_status"] == "pass"
    assert result["output_records"] == expected["records"]
    assert result["semantic_sha256"] == expected["canonical_record_sha256"]
    assert result["record_equivalent"] is True
    assert result["reference_dictionary_equivalent"] is True
    assert result["sort_order"] == "unsorted"

    safe_runs.append({
        "run": run_number,
        "forced_streaming_selected": True,
        "streaming_fallback": False,
        "quickcheck_status": result["quickcheck_status"],
        "output_records": result["output_records"],
        "semantic_sha256": result["semantic_sha256"],
        "sort_order": result["sort_order"],
        "reference_sequences": result["reference_sequences"],
        "reference_dictionary_sha256": result["reference_dictionary_sha256"],
        "record_equivalent": result["record_equivalent"],
        "reference_dictionary_equivalent":
            result["reference_dictionary_equivalent"],
    })

digest = hashlib.sha256()
with Path("sparse.bam").open("rb") as handle:
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)

safe_workload = {
    "workload_id": workload["workload_id"],
    "workload": workload["workload"],
    "parameters": workload["parameters"],
    "generator": {
        "version": workload["generator"]["version"],
        "source": workload["generator"]["source"],
        "source_sha256": workload["generator"]["source_sha256"],
        "python": workload["generator"]["python"],
    },
    "input_sam": {
        key: workload["input"][key]
        for key in (
            "format",
            "sort_order",
            "bytes",
            "records",
            "sha256",
            "reference_sequences",
            "reference_dictionary_sha256",
        )
    },
    "input_bam": {
        "bytes": Path("sparse.bam").stat().st_size,
        "sha256": digest.hexdigest(),
    },
    "expected_output": expected,
}

Path("profile-correctness.json").write_text(
    json.dumps(
        {
            "schema": "dumi-allocation-profile-correctness",
            "schema_version": 1,
            "workload": safe_workload,
            "runs": safe_runs,
        },
        indent=2,
        sort_keys=True,
    ) + "\n",
    encoding="utf-8",
)
PY

python3 - allocation-aggregate.json <<'PY'
import json
from pathlib import Path
import sys

profile = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

for sentinel in profile["aggregate"]["sentinels"]:
    observations = sentinel["per_run_event_count"]
    if sentinel["kind"] == "expected_present":
        assert all(value > 0 for value in observations)
    else:
        assert all(value == 0 for value in observations)
PY
```

Before accepting the run, require all three semantic reports to have:

- `quickcheck_status` equal to `pass`;
- `output_records` and `semantic_sha256` equal to
  `workload.json.expected_output`;
- `record_equivalent` and `reference_dictionary_equivalent` equal to `true`;
- `sort_order` equal to `unsorted`, as required for streaming output.

Also require the two positive-control sentinels in
`allocation-aggregate.json` to have sampled events. For the exact singleton
workload, the three expected-absent sentinels should be zero in every run. A
nonzero result is a reason to investigate rather than something to suppress.

The 1,000,000-record workload is approximately 160 MB as SAM and 13 MB as BAM.
On an ordinary development host, plan for several seconds per profiled run and
roughly one to three minutes end to end, including semantic checks. Reserve 10
minutes and 2 GiB of temporary disk as conservative allowances. These are
planning estimates, not benchmark results. Profiled elapsed times must not be
reported as performance measurements.

## Aggregate schema

The aggregate is JSON with this top-level shape:

```text
schema, schema_version
aggregator { version, source, source_sha256 }
configuration {
  source, source_sha256, event, throttle, stack_depth, jfr_tool_version
}
interpretation
limits
runs[] {
  run, recording_bytes, recording_sha256,
  event_count, sample_weight_bytes, sentinels[]
}
aggregate {
  event_count, sample_weight_bytes,
  top_allocated_classes[],
  top_allocation_sites[],
  top_dumi_ancestor_sites[],
  sentinels[]
}
```

Each top-entry list is capped at 20 rows and sorted by descending summed sample
weight, then stable label. Hidden-class runtime identifiers are normalized.
Labels are capped at 512 characters with a digest suffix when necessary. The
aggregator rejects duplicate inputs, recordings larger than 16 MiB, more than
32 inputs, a configuration different from the shipped JFC, and an output that
would overwrite a recording, the aggregator, the configuration, or the JFR
executable. It also refuses symbolic links and unrecognized existing outputs.
Only an ordinary prior aggregate with the current schema may be replaced
atomically.

`sample_weight_bytes` is the sum of JFR's statistical `weight` field. It is an
estimate of allocation pressure. Despite the unit in its name, it must not be
presented as an exact byte count.

## Privacy and retention

The aggregate is constructed from a strict allowlist. It contains:

- class and method names, without source paths or line numbers;
- weighted counts and bounded top lists;
- recording sizes and SHA-256 digests;
- stable sentinel definitions and observations;
- hashes of the aggregator and JFC.

It does not contain event timestamps, thread identities, command lines,
environment variables, input or output paths, usernames, or hostnames. Raw JFR
files can contain runtime metadata and are therefore local evidence: retain
them only in the external run directory, and do not commit or attach them to a
public release. Raw JSON from `jfr print` is held in memory and is not written
by the aggregator. The raw `semantic-*.json` files and run logs can contain
local paths; publish only the whitelisted `profile-correctness.json` summary.
The aggregator strips standard JVM and dynamic-loader injection variables from
its own `jfr` subprocesses and disables JVM performance-data files, preventing
those settings and related launcher warnings from corrupting JSON extraction.

A minimal public evidence package should contain:

- the path-neutral aggregate;
- a workload receipt reduced to relative names, generator/source hashes,
  record counts, UMI length, and input/expected-output digests;
- the frozen dUMI revision, JAR and build-input hashes, Java 21 and `samtools`
  versions, and the normalized JVM options above;
- `profile-correctness.json`, the path-neutral semantic/workload summary
  produced above;
- a checksum manifest for those files.

Record only whitelisted operating-system fields such as OS family, kernel
release, and architecture. Do not record `uname -a`, environment dumps,
absolute paths, usernames, or hostnames.

## Claim boundary

For the exact frozen revision and workload, this evidence can support:

- that forced streaming was selected without fallback;
- that output records and reference dictionaries passed the declared semantic
  checks;
- which classes and method sites dominated sampled allocation pressure;
- when observed in every run, that no allocation samples were attributed to
  the expected-absent singleton-path sentinels.

It cannot support:

- exact allocated bytes, object counts, live or retained heap, leaks, or peak
  RSS;
- a runtime speedup or memory reduction, because profiling adds overhead and
  no matched comparator is included;
- a before/after or upstream comparison without rerunning both frozen
  revisions under this exact workload and configuration;
- behavior of dense groups, paired reads, FASTQ, non-streaming modes, or
  production data;
- proof that an allocation never occurred. A zero sampled weight means only
  that the configured statistical sampler did not observe it.

Numeric speed and peak-RSS claims belong exclusively to the reproducible
benchmark evidence, not this diagnostic profile.
