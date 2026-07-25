# Reproducible upstream comparison

This directory contains the public benchmark harness used to compare dUMI
with canonical
[UMICollapse](https://github.com/Daniel-Liu-c0deb0t/UMICollapse). It is
designed to answer two separate questions:

1. How much time and peak memory does the Java deduplication step require?
2. What is the end-to-end cost when every output is made coordinate-sorted
   and indexable for downstream use?

Correctness is a prerequisite, not a performance metric. A run is rejected if
an implementation disagrees with the deterministic workload oracle or with
the other implementations.

## Compared revisions

The runner pins canonical UMICollapse to commit
[`efeab35f5d29dec1d496ade3f681eeb34d9c2057`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/commit/efeab35f5d29dec1d496ade3f681eeb34d9c2057).
This is the canonical upstream baseline from which dUMI was forked.

With `--include-intermediate`, the paired-read sweep also includes
[`aeacd8231cf8e77c03d03139ed6e65a4c2845015`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32/commits/aeacd8231cf8e77c03d03139ed6e65a4c2845015)
from upstream PR
[#32](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32). That
comparison separates paired-read improvements inherited from PR #32 from
later dUMI work. The intermediate commit is not described as a canonical
UMICollapse release.

All compared production sources are archived from immutable Git commits,
compiled with the same `javac --release 11` command, and run with the same
locked dependencies, Java executable, classpath layout, heap settings, and
logical processor limit. This source-normalized comparison isolates code-path
differences under a common runtime. It does **not** reproduce the historical
launcher, dependency bundle, or JVM defaults of an old installation.

The dUMI revision must be a committed object. A dirty checkout is permitted so
the runner can be launched during development, but uncommitted production
sources are never benchmarked. Use an explicit frozen commit for reportable
results.

## Requirements

- Python 3.9 or newer;
- Git and `curl`;
- JDK 11 or newer (`java` and `javac`);
- `samtools`;
- GNU `time` (`/usr/bin/time` on Linux or Homebrew `gtime` on macOS);
- enough memory for the configured `-Xmx` value and enough temporary disk for
  generated SAM/BAM data.

The runner uses the dependency versions and SHA-256 digests in the selected
dUMI commit's `dependencies.lock`. Downloads use HTTPS and are rejected on a
checksum mismatch.

## Prerequisite smoke test

Run the tiny profile before scheduling a full comparison:

```bash
python3 scripts/benchmark/run_benchmark.py \
  --profile tiny \
  --repetitions 1 \
  --include-intermediate \
  --output-dir /tmp/dumi-benchmark-smoke
```

The tiny profile is only a fast end-to-end check of tools, compilation,
contracts, correctness gates, and evidence generation. Its timing and memory
values are not benchmark results and must not be reported as such.

## Reportable run

Use a quiet host, a frozen dUMI commit, and an output directory outside the
repository:

```bash
python3 scripts/benchmark/run_benchmark.py \
  --dumi-ref FINAL_DUMI_COMMIT_SHA \
  --include-intermediate \
  --output-dir /absolute/path/outside/the/repository/dumi-benchmark-final
```

The standard profile uses seven repetitions. It generates:

| Workload | Standard scale | Purpose |
| --- | ---: | --- |
| `sparse` | 100,000 and 1,000,000 reads | Many singleton coordinate groups; exposes whole-file bookkeeping overhead. |
| `moderate` | 4,096 coordinate groups × 16 families | Repeated, error-connected directional UMI families at a middle per-coordinate graph width. |
| `hotspot` | 65,536 UMI families at one coordinate | Adversarial high-density group; tests the case where streaming cannot bound the dominant per-coordinate state. |
| `paired` | 10 and 1,000 references, five pairs per reference | Paired-read correctness and reference-transition behavior; streaming is ineligible. |

Each synthetic UMI is 12 bases. Family parents come from a deterministic
quaternary code with minimum distance at least four; their one-edit children
cannot become ambiguous neighbors of another family. Mapping qualities and
record contents select an unambiguous expected representative.

At the standard defaults, the six workload cells contain 2,945,108 input
alignment records and 474,979,763 bytes of generated SAM before BAM
conversion. The runner records the realized byte counts and hashes; these
figures describe workload volume, not a performance result.

Treatments use a repeated cyclic Latin schedule, shifted by workload and
reversed on alternating complete blocks. The schedule is interleaved within
each repetition to distribute warm-host and order effects. Seven repetitions
give each treatment position counts that differ by at most one; the design is
not claimed to be a fully carryover-balanced Williams design.

Runtime and disk usage depend heavily on the host, Java runtime, storage, and
configured scales. For the standard profile, reserve roughly one to two hours,
at least 10 GiB of free disk, and the configured 4 GiB Java heap. These are
planning allowances, not expected performance. Add `--keep-outputs` only when
the individual result BAMs are needed; reserving 25 GiB is prudent in that
case.

## What is timed

Every Java invocation is a fresh process. Before timing begins for a workload,
each implementation receives one untimed warm-up invocation, with the warm-up
order shifted by workload. The input BAM is read immediately before each
measured Java invocation to reduce arbitrary page-cache differences.

Every matrix cell runs BAM mode with a 12-base UMI, directional clustering,
`k=1`, `p=0.5`, the `ngrambktree` data structure, and `mapqual`
representative selection. The default common JVM options are:

```text
-XX:-UsePerfData -server -Xms64m -Xmx4g -Xss20m -XX:ActiveProcessorCount=8
```

Overrides are recorded in `manifest.json`. Comparisons should be made within
one completed evidence bundle; absolute timing and maximum-RSS values should
not be compared across different hosts or operating systems as though they
were matched observations.

Elapsed time is measured around the complete process invocation with Python's
monotonic nanosecond clock. GNU `time` independently supplies user time,
system time, CPU utilization, process exit status, and peak resident memory.

The evidence reports three stages:

- `raw`: only the Java deduplication command;
- `ready`: the additional operation needed to produce a coordinate-sorted,
  indexed BAM (`samtools sort` plus `samtools index` for streaming output, or
  indexing alone for an already coordinate-sorted output);
- `raw_plus_ready`: derived elapsed, user, and system time sums, with peak RSS
  taken as the larger of the two stages.

Raw timing exposes the algorithm and streaming implementation. The
`raw_plus_ready` result is usually the more relevant comparison for a workflow
that requires a coordinate-sorted, indexable BAM.

The single-end matrix includes canonical upstream plus dUMI
`--streaming-mode off`, `on`, and `auto`. The paired matrix includes canonical
upstream, optional PR #32 intermediate, and dUMI `off` and `auto`. A separate
contract check verifies that paired `--streaming-mode on` is rejected.

## Correctness gates

For every generated workload, the generator records the exact expected output
record count and a SHA-256 digest of the byte-sorted, non-header SAM record
multiset. Duplicate records remain significant. The runner requires:

- `samtools quickcheck` success;
- the generator-declared record count and semantic digest;
- equality across all implementations and repetitions;
- equality of the ordered `@SQ` reference dictionary;
- the expected raw BAM sort order for the selected route;
- absence of an automatic streaming fallback in a result labeled streaming;
- coordinate sort order after the downstream-ready stage;
- a successful no-flag/default-`auto` result matching the same oracle;
- an exact match between the precomputed schedule and all measured cells.

Header sort order is checked independently of record-multiset equivalence.
Streaming output must declare `SO:unsorted`; canonical upstream, dUMI
`--streaming-mode off`, and paired outputs must declare `SO:coordinate`.

## Evidence bundle

The output directory contains:

- `STATUS.json`: `RUNNING`, `FAILED`, or `COMPLETE`;
- `manifest.json`: revisions, source bindings, dependency digests, runtime
  identity, JVM options, configuration, and harness-file hashes;
- `environment.json` and `environment.txt`: host and tool versions; the JSON
  receipt also records load average, CPU affinity, and the CPU scaling governor
  when the host exposes them;
- `harness/`: an exact snapshot of the scripts and this README used for the
  run;
- `sources/`, `classes/`, `dependencies/`, and `build-commands/`: archived
  source, normalized builds, locked jars, and build receipts;
- `inputs/`: generated workloads, metadata, commands, and hashes;
- `design.tsv`: the complete schedule fixed before measurement;
- `measurements.tsv`: unaggregated process measurements and semantic receipts;
- `summary.tsv`: median, minimum, maximum, range, and median absolute deviation
  by implementation and stage;
- `comparisons.tsv`: matched-repetition comparisons with canonical upstream,
  including elapsed speedup, elapsed percent change, and peak-RSS percent
  reduction;
- `correctness.tsv`: per-cell correctness status and diagnostics;
- `runs/`, `warmups/`, and `contracts/`: exact commands, stdout, stderr,
  timing receipts, and semantic checks;
- `evidence.sha256` and `MANIFEST.sha256`: hashes for the primary outputs and
  all sealed evidence. `STATUS.json` is written last and intentionally
  excluded from the tree manifest so `COMPLETE` cannot appear before sealing
  succeeds.

Before a bundle can be sealed `COMPLETE`, the runner scans its textual
evidence for private absolute-path roots, exact run-local paths, and local
user/host identifiers. Paths in recorded commands are represented with
placeholders such as `<EVIDENCE_DIR>` and `<JAVA>`; the exact tool versions
remain in the environment receipt. Project-specific public-release language
review remains a separate repository-level gate.

Large per-run result BAMs are discarded by default after semantic validation;
their record counts and semantic digests remain in `measurements.tsv`, and
their commands, logs, timing receipts, and checks are retained. Consequently,
`output_file` is blank in those rows. `--keep-outputs` retains the BAMs and
populates that field.

The full raw bundle, generated inputs, build trees, and optional result BAMs
belong in the external evidence directory and should not be committed to the
source repository. A deliberately curated, path-neutral result summary and
its hashes may be committed or attached to a release when it is useful for
review; it must identify the immutable source bundle from which it was
derived.

After a successful run, verify the retained bundle:

```bash
cd /absolute/path/to/dumi-benchmark-final
sha256sum --check MANIFEST.sha256
```

On macOS:

```bash
shasum -a 256 --check MANIFEST.sha256
```

Only a run with `STATUS.json` equal to `COMPLETE`, a passing manifest check,
all scheduled repetitions present, and every `correctness.tsv` row marked
`pass` is suitable for reporting.

Comparison statistics are calculated within repetition before aggregation:

```text
elapsed_speedup = canonical_elapsed / treatment_elapsed
elapsed_change_pct = 100 * (treatment_elapsed - canonical_elapsed) / canonical_elapsed
max_rss_reduction_pct = 100 * (canonical_max_rss - treatment_max_rss) / canonical_max_rss
```

Thus a speedup greater than one is faster, while a positive RSS reduction uses
less peak resident memory. `comparisons.tsv` reports the median, range, and MAD
of these paired values and the exact number of successful pairs. It does not
divide independently summarized medians.

These fixed-seed synthetic workloads isolate algorithmic scaling and route
overhead. Their highly regular reads are intentionally reproducible and
compressible; they are not a substitute for throughput validation on
representative real BAMs. Public claims should label them synthetic and keep
the workload name and scale attached to every result.

## Utilities

- `generate_workload.py` creates one deterministic coordinate-sorted SAM
  workload and its oracle receipt.
- `semantic_check.py` checks one SAM/BAM and emits its exact record-multiset
  fingerprint.
- `summarize_results.py` validates and summarizes an existing raw
  `measurements.tsv`.
- `run_benchmark.py` orchestrates the immutable, source-normalized comparison.

Run any utility with `--help` for its complete command-line contract.
