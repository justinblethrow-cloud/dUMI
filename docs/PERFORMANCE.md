# Performance and allocation evidence

The accepted dUMI v2.0.0 evidence compares frozen production commit
[`2995329`](https://github.com/justinblethrow-cloud/dUMI/commit/299532964a57905c835bd750563988a09af6e1df)
with canonical UMICollapse commit
[`efeab35`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/commit/efeab35f5d29dec1d496ade3f681eeb34d9c2057).
The paired-read matrix also includes
[`aeacd82`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32/commits/aeacd8231cf8e77c03d03139ed6e65a4c2845015)
from unmerged upstream PR #32.

All results below are fixed-seed synthetic scaling measurements, not claims
about every production BAM. Every reported cell passed the generator oracle,
exact non-header record-multiset comparison, reference-dictionary comparison,
sort-order contract, and process-exit checks.

## Result at a glance

For eligible single-end input, the default guarded `auto` route was faster
than canonical upstream on every tested workload:

| Synthetic workload | Input records | Upstream raw median (s) | dUMI `auto` raw median (s) | Matched raw speedup | Upstream median RSS (KiB) | dUMI `auto` median RSS (KiB) | Matched RSS reduction | Matched raw-plus-ready speedup |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Sparse, 100,000 singleton groups | 100,000 | 1.808 | 0.729 | 2.56x | 258,176 | 138,376 | 46.09% | 1.96x |
| Sparse, 1,000,000 singleton groups | 1,000,000 | 9.241 | 3.408 | 2.75x | 1,649,268 | 265,228 | 83.46% | 1.89x |
| Moderate, 4,096 groups x 16 families | 917,504 | 3.220 | 2.132 | 1.48x | 553,532 | 428,804 | 23.33% | 1.40x |
| Hotspot, 65,536 families at one coordinate | 917,504 | 4.538 | 3.435 | 1.30x | 570,436 | 480,780 | 15.83% | 1.27x |

“Matched” means the statistic is the median of seven within-repetition ratios,
not a ratio calculated from the independently summarized medians displayed in
the adjacent columns. `raw-plus-ready` includes the extra work required to
produce a coordinate-sorted, indexed BAM.

The results show two distinct effects:

- With streaming disabled, shared-core changes alone produced raw speedups of
  1.10x to 1.21x and peak-RSS reductions of 11.88% to 23.05% across the four
  single-end workloads.
- Guarded streaming added the largest gain when coordinates were sparse. On
  the million-record sparse workload, forced `on` produced a 2.68x raw
  speedup and an 84.18% peak-RSS reduction.

The hotspot deliberately concentrates every UMI family at one coordinate.
That group cannot be flushed early, so it demonstrates the boundary of the
streaming design: `off` used less memory there than `on` or `auto`, although
all three dUMI routes remained faster and used less peak RSS than upstream.

## Shared core, streaming, and downstream-ready cost

The table below reports the matched comparisons against canonical upstream.
Elapsed and RSS values are medians of seven paired ratios.

| Workload | Candidate | Raw speedup | Raw elapsed change | Raw RSS reduction | Raw-plus-ready speedup |
| --- | --- | ---: | ---: | ---: | ---: |
| Sparse 100,000 | dUMI `off` | 1.10x | 8.77% faster | 11.93% | 1.10x |
| Sparse 100,000 | dUMI `on` | 2.44x | 59.01% faster | 46.40% | 1.92x |
| Sparse 100,000 | dUMI `auto` | 2.56x | 60.98% faster | 46.09% | 1.96x |
| Sparse 1,000,000 | dUMI `off` | 1.10x | 8.96% faster | 11.88% | 1.10x |
| Sparse 1,000,000 | dUMI `on` | 2.68x | 62.71% faster | 84.18% | 1.86x |
| Sparse 1,000,000 | dUMI `auto` | 2.75x | 63.59% faster | 83.46% | 1.89x |
| Moderate | dUMI `off` | 1.21x | 17.11% faster | 23.05% | 1.21x |
| Moderate | dUMI `on` | 1.52x | 34.35% faster | 22.68% | 1.44x |
| Moderate | dUMI `auto` | 1.48x | 32.60% faster | 23.33% | 1.40x |
| Hotspot | dUMI `off` | 1.21x | 17.25% faster | 21.50% | 1.21x |
| Hotspot | dUMI `on` | 1.32x | 24.00% faster | 16.59% | 1.28x |
| Hotspot | dUMI `auto` | 1.30x | 23.02% faster | 15.83% | 1.27x |

Streaming output is correctly declared `SO:unsorted`, so making it
downstream-ready requires `samtools sort` and `samtools index`. Upstream and
dUMI `off` already emit coordinate-sorted output and need only indexing. The
ready-only stage was therefore 4.9x to 11.2x as long for the streaming
single-end cells and used more memory than indexing alone. That cost reduces
but does not reverse the overall advantage in these workloads, as the final
column shows.

This distinction matters operationally: use the raw comparison when an
unsorted representative-read BAM is acceptable, and use `raw-plus-ready` when
the next consumer requires coordinate order and an index.

## Paired-read result and PR #32 attribution

Paired mode is not eligible for streaming. Its comparison isolates the
persistent-reader change incorporated from upstream PR #32.

| Paired workload | Candidate | Raw median (s) | Matched raw speedup | Matched raw RSS reduction | Matched raw-plus-ready speedup |
| --- | --- | ---: | ---: | ---: | ---: |
| 10 references, 100 records | PR #32 `aeacd82` | 0.232 | 1.05x | 3.70% | 1.05x |
| 10 references, 100 records | dUMI `off` | 0.265 | 0.93x | 3.57% | 0.93x |
| 10 references, 100 records | dUMI `auto` | 0.263 | 0.92x | 0.00% | 0.92x |
| 1,000 references, 10,000 records | PR #32 `aeacd82` | 0.679 | 4.78x | 79.98% | 4.71x |
| 1,000 references, 10,000 records | dUMI `off` | 0.672 | 4.86x | 80.54% | 4.77x |
| 1,000 references, 10,000 records | dUMI `auto` | 0.672 | 4.83x | 80.16% | 4.77x |

The 1,000-reference result is essentially present in the PR #32 intermediate
before the later dUMI changes. The appropriate attribution is therefore that
dUMI incorporates and hardens the persistent-reader proposal; it did not
independently originate that paired-mode performance gain.

Both paired fixtures contain unique pairs. They test mate traversal,
reference-transition scaling, and record preservation; they do not measure
paired duplicate collapse or representative selection.

The 100-record cell is dominated by JVM startup and fixed setup costs. dUMI
was 7% to 9% slower there, while the PR #32 intermediate was about 5% faster.
This small cell is retained as a visible tradeoff and correctness boundary,
not promoted as representative throughput.

## Method

The reportable run used:

- seven repetitions per implementation and workload;
- a cyclic Latin schedule, shifted by workload and reversed on alternating
  complete blocks;
- OpenJDK 21.0.11 for both compilation and execution, with production classes
  compiled using `javac --release 11`;
- the same HTSJDK 3.0.5 and snappy-java 1.1.10.8 artifacts for every compared
  source revision;
- `-XX:-UsePerfData -server -Xms64m -Xmx4g -Xss20m
  -XX:ActiveProcessorCount=8` for every Java invocation;
- `samtools` 1.19.2 for input conversion, semantic inspection, sorting, and
  indexing;
- Python monotonic nanosecond timing around the full process invocation and
  GNU `time` for CPU and peak resident memory.

The baseline and candidates were archived from exact commits, compiled through
the same normalized command, and invoked as fresh JVM processes. This isolates
code-path differences under one runtime. It does not reproduce an old
UMICollapse installation's dependency bundle, launcher defaults, or historical
JDK.

The rotating schedule balances marginal treatment position to within one
exposure; it is not fully carryover-balanced. The host was not exclusive or
CPU-pinned. The JVM was limited to eight logical processors, while affinity,
governor, startup load, and per-cell dispersion were retained in the evidence.

The six deterministic inputs cover:

- 100,000 and 1,000,000 singleton coordinate groups;
- 4,096 coordinates with 16 error-connected UMI families per coordinate;
- 65,536 error-connected families at one coordinate;
- paired reads across 10 and 1,000 references.

All UMIs are 12 bases. The family generator gives each parent a strictly higher
count than its children and uses minimum-distance parent codes so the expected
representative is unambiguous.

## Correctness and evidence quality

The accepted bundle contains:

- 336 predeclared and observed measurements: 168 raw and 168 ready;
- 72 summary and 72 correctness cells;
- 54 matched candidate-versus-upstream comparisons;
- seven successful repetitions or pairs in every applicable row;
- zero failed processes, correctness cells, or comparison pairs.

Every output was checked against exact generator-declared record counts and
byte-sorted record-multiset SHA-256 values. Reference sequence counts and
ordered `@SQ` dictionary hashes also matched. Streaming selection markers,
absence of fallback, raw sort-order contracts, downstream-ready coordinate
order, and paired forced-streaming rejection were checked separately.
This semantic contract does not require bit-identical BAM encoding or complete
identity of non-`@SQ` header records.

The runner came from a clean detached checkout of `2995329`. Its manifest
records `worktree_was_dirty: false`, excludes uncommitted sources by
construction, and uses the path-neutral runtime identifier
`04385711a6838b779a934a4d3b0ae9d2a71106e108b465895f284a1fa0aa4566`.
Both full-bundle and primary-evidence checksum manifests passed before
curation, and the generated privacy scan passed.

## Final allocation profile

Allocation profiling is diagnostic evidence, not an elapsed-time benchmark.
Three Java Flight Recorder runs used the frozen dUMI commit, Java 21, forced
streaming, and the one-million-record sparse workload. All three outputs passed
the same exact record and reference-dictionary checks.

The aggregate contained 2,504 sampled allocation events. In every run:

- general clustering setup was absent from the sampled singleton path;
- UMI-map promotion under `addStreamingRead` was absent;
- reflective data-structure construction was absent;
- streaming-group and alignment-key positive controls were present.

Those zero sentinels are direct evidence that the singleton bypass and lazy
one-entry accumulator operated as intended on this workload. They are not
proof that the same sites can never execute on multi-UMI groups.

The largest remaining sampled allocation sites were HTSJDK BAM decoding,
byte-array copying, CIGAR/list construction, and BAM-record creation. The
largest dUMI-owned sites were UMI encoding, streaming-group construction,
alignment-key construction, and `SAMRead` construction. JFR sample weights are
statistical estimates of allocation pressure; they are not exact allocated
bytes, retained heap, or peak RSS.

The final profile does not expose another comparably low-risk setup bug.
Eliminating the remaining objects would require broader representation or
HTSJDK parsing changes, with a much larger semantic and maintenance surface.
Those ideas remain reasonable future experiments only when a representative
workload and differential test justify them.

No before/after numeric profile claim is made. Earlier diagnostic recordings
used a different workload and were not retained as public evidence.

## Reproduce and inspect

The benchmark runner and workload generator are documented in
[`scripts/benchmark/README.md`](../scripts/benchmark/README.md). The allocation
procedure and aggregate schema are documented in
[`scripts/profile/README.md`](../scripts/profile/README.md).

The curated evidence is under
[`docs/benchmarks/2026-07-25/`](benchmarks/2026-07-25/). From the repository
root:

```bash
(
    cd docs/benchmarks/2026-07-25/benchmark
    sha256sum -c evidence.sha256
)
(
    cd docs/benchmarks/2026-07-25
    sha256sum -c SHA256SUMS
)
```

The curated `measurements.tsv` retains relative references to per-run logs in
the sealed full bundle; those large logs and generated SAM/BAM/JFR files are
deliberately not source controlled. The copied
`full-bundle.MANIFEST.sha256` records the full sealed inventory but is not
expected to verify against the smaller curated tree.

Machine-readable presentation data is in
[`benchmark-summary.csv`](presentation-reference/benchmark-summary.csv).

## Claim boundary

These results support claims about the exact commits, runtime, and synthetic
workloads recorded here. They do not establish:

- a universal speed or memory improvement for every BAM;
- streaming gains for FASTQ, paired, tagged, two-pass, or parallel modes;
- constant or absolute bounded memory;
- preservation of record order;
- construction of base-level consensus reads;
- an upstream endorsement of dUMI.

See [`LIMITATIONS.md`](LIMITATIONS.md) for the complete compatibility boundary.
