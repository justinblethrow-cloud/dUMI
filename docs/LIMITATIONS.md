# Limitations and compatibility boundaries

This fork deliberately preserves the broad UMICollapse interface. Its fastest
execution route is narrower than the full feature set.

## Streaming eligibility

Guarded streaming applies only to coordinate-declared, single-end SAM/BAM
input using sequential processing without cluster tagging or `--two-pass`.
These routes use the compatible legacy architecture instead:

- FASTQ;
- paired-end SAM/BAM;
- `--tag`;
- `--two-pass`;
- `-t` or `-T` parallel processing;
- input not declared coordinate sorted;
- explicit `--streaming-mode off`.

Forced `--streaming-mode on` rejects an incompatible configuration. In `auto`
mode, an input-order or clipping-window violation discards the temporary output
and restarts through the legacy path.

## Working-set scope

Streaming provides a coordinate-window-bounded working set, not a
constant-memory guarantee. Peak memory still depends on:

- alignment-group density inside the active window;
- unique UMIs per group;
- read-representative payloads;
- JVM and HTSJDK behavior.

The default positive-strand allowance is 10,000 leading clipped bases. A larger
observed value requires a larger
`-Dumicollapse.streaming.positiveLag=...` setting or the legacy path. A late
`auto` fallback rereads the input and can increase total elapsed time.

## Output order

Streaming can flush alignment groups outside coordinate order. Its SAM/BAM
header is therefore declared `SO:unsorted`. Pipelines that require coordinate
order must sort the result before indexing or consuming it as coordinate
sorted.

Legacy and two-pass routes retain their HTSJDK sort behavior. Record order
should not be used as a proxy for biological equivalence; validation compares
record content and separately checks header semantics.

## Representative selection

Merge policies retain one existing read as the representative of exact-UMI
duplicates. They do not calculate a base-level consensus.

Mapping-quality and average-quality ties use stable record-content ordering,
and algorithm traversal uses stable UMI ordering when frequencies tie. The
`any` merge policy is intentionally arbitrary and may select a different
representative when input order changes.

These deterministic tie policies are intentional behavioral refinements, not
a promise of byte-for-byte identity with canonical upstream. For equal-scoring
reads or equal-frequency competing UMIs, dUMI may select a different
representative or cluster seed than upstream's encounter- or hash-order
choice, while applying the same configured edit-distance and frequency rules.

## Input conventions

SAM/BAM UMI extraction expects the UMI in the read name. The optimized parser
targets the default underscore convention. Custom `--umi-sep` values are
treated as literal strings; regular-expression metacharacters have no special
meaning.

An explicit `-u` value must be `-1` for autodetection or a positive length. A
parsed UMI shorter than the effective length is rejected instead of being
silently padded or aliased to another encoded UMI.

The code does not currently read UMIs directly from SAM tags such as `RX`.

Invalid option names, unsupported strategy combinations, out-of-range numeric
values, and identical input/output paths are rejected before processing.

## Unoptimized modes

FASTQ, paired, tagging, two-pass, and parallel modes benefit from shared core
changes where applicable, but they do not use the streaming route. No
performance improvement should be claimed for one of these modes without a
mode-specific benchmark.

## Dependencies and platform scope

The dependency versions are checksum locked for reproducibility, but locking
does not establish that they are current or free of vulnerabilities. Dependency
upgrades require the same SAM/BAM compatibility gate.

The project targets Java 11 bytecode and tests multiple supported JDK/platform
combinations in CI. That matrix is evidence for those combinations, not for
every JVM, operating system, filesystem, or storage device.

CRAM, SRA access, and other optional HTSJDK surfaces are outside the tested
and supported dUMI interface. The locked HTSJDK dependency may contain such
capabilities, but their presence is not a dUMI compatibility commitment.

## Benchmark scope

Synthetic benchmarks are repeatable regression and comparison workloads. They
do not predict every dataset. Results can change with alignment density, UMI
length and distribution, CIGAR structure, merge policy, compression, storage,
and JVM settings.

The validation suite establishes implementation compatibility and stated
invariants on the recorded fixtures. It does not independently revalidate the
biological model or scientific conclusions of the original UMICollapse
publication.

See [`PERFORMANCE.md`](PERFORMANCE.md) for exact benchmark provenance and
[`VALIDATION.md`](../VALIDATION.md) for correctness and safety evidence.
