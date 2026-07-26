# Reproducible upstream comparison

This directory contains the benchmark harness used to compare dUMI with canonical
[UMICollapse](https://github.com/Daniel-Liu-c0deb0t/UMICollapse). It is
designed to answer two separate questions:

1. How much time and peak memory does the Java deduplication step require?
2. What is the end-to-end cost when every output is made coordinate-sorted
   and indexable for downstream use?

Correctness is a prerequisite, not a performance metric. Synthetic outputs
must agree with the deterministic generator oracle. External-input outputs
must agree exactly with their implementation-specific oracle, and dUMI
`--streaming-mode off` must match the independent source-BAM Directional
oracle for UMI-cluster membership, RX/root assignment, ordered `@SQ`, and
normalized order-independent `@RG`. Canonical-upstream agreement and pairwise implementation
comparisons are diagnostics. An output-count mismatch does not invalidate
scientific correctness, but it makes the paired performance comparison
`not_comparable` and suppresses its speed and memory statistics.

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

The dUMI revision must be a committed object. A dirty checkout is permitted
for synthetic development runs, but uncommitted production sources are never
benchmarked. External mode additionally fails closed unless every snapshotted
benchmark harness file is tracked, clean, and byte-identical to its path in
the archived dUMI commit. Use an explicit frozen commit for reportable results.

## Requirements

- Python 3.9 or newer;
- Git and `curl`;
- JDK 11 or newer (`java` and `javac`);
- `samtools`;
- GNU `time` (`/usr/bin/time` on Linux or Homebrew `gtime` on macOS);
- GNU coreutils `sort` for external mode (`sort` on Linux or Homebrew `gsort`
  on macOS);
- enough memory for the configured `-Xmx` value and enough temporary disk for
  generated SAM/BAM data. External mode also performs two untimed `--tag`
  runs after all timing is complete; size their heap and external-sort scratch
  for the complete pre-deduplication input.

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

The standard profile uses eight repetitions. It generates:

| Workload | Standard scale | Purpose |
| --- | ---: | --- |
| `sparse` | 100,000 and 1,000,000 reads | Many singleton coordinate groups; exposes whole-file bookkeeping overhead. |
| `moderate` | 4,096 coordinate groups × 16 families | Repeated, error-connected directional UMI families at a middle per-coordinate graph width. |
| `hotspot` | 65,536 UMI families at one coordinate | Adversarial high-density group; tests the case where streaming cannot bound the dominant per-coordinate state. |
| `paired` | 10 and 1,000 references, five pairs per reference | Paired-read correctness and reference-transition behavior; streaming is ineligible. |

## External BAM mode

`--external-bam-manifest` runs the same source-normalized builds, independently
offset stage schedules (a Williams design for the four-treatment reportable
matrix), direct `raw` and `end_to_end_ready` timing, comparisons, correctness
gates, and evidence sealing on complete coordinate-sorted
**pre-deduplication** BAMs. This mode is intended for representative-workload
replication after the synthetic benchmark is complete.

The input must be the aligner output before UMI collapsing, duplicate marking,
representative selection, or any other read-removal step. Coordinate sorting
and indexing are permitted. A delivered or pipeline-final deduplicated BAM is
not a valid benchmark input: re-running either implementation on it would
create a circular, artificially easy workload. The pre-dedup BAM must still
carry the original parseable QNAME UMI on every mapped read considered by
the configured mode.

Keep a private provenance ledger beside the manifest and supply both
`--external-provenance-ledger PATH` and
`--external-provenance-ledger-sha256 SHA256`. The file is required in external
mode and forbidden otherwise. It may contain private source accessions,
FASTQ-to-BAM transformations, reference and index digests, aligner and sorting
versions, UMI extraction rules, and other private fields outside the
`workloads` rows. Each workload row has exactly four fields. Its required JSON
contract is:

```json
{
  "schema": "dumi-external-provenance-ledger",
  "version": 1,
  "workloads": [
    {
      "workload_id": "demo-small",
      "authorization_confirmed": true,
      "pre_deduplication_confirmed": true,
      "bam_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ]
}
```

The workload IDs must cover the external manifest exactly; duplicates,
missing IDs, extras, missing workload-row fields, and unknown workload-row
fields are rejected. Each `bam_sha256` must be exactly 64 lowercase hexadecimal
characters and must exactly equal the digest in the corresponding external
manifest row. The runner verifies the required ledger-file hash before creating
the output directory and again before sealing. It retains only that ledger-file
hash, schema/version, workload count, aggregate confirmations, and the explicit
facts `path_recorded=false` and `content_retained=false` in the restricted
evidence. It does not retain any per-BAM hash from the ledger. The path,
content, and arbitrary private fields are never copied. A curated public export
may disclose only schema/version and that authorization evidence was present;
it must not disclose the ledger hash or any BAM hash.

The manifest may be TSV or JSON. TSV requires this header:

```text
workload_id	bam_path	bam_sha256	paired	umi_length	umi_separator	rationale
```

JSON may be an array of equivalent objects or an object with `"format": 1`
and a `"workloads"` array. `rationale` is optional; all other fields are
required. Relative BAM paths resolve from the manifest directory. Boolean
values are exactly `true` or `false`.

Use a neutral lowercase alias such as `demo-small`, not an order, customer,
sample, project, or organization identifier. Each BAM path, BAM hash, and
alias must be unique. `umi_length` must be greater than the fixed edit
distance `k=1`; `umi_separator` must contain one to eight characters drawn
only from `._:+-` and cannot begin with `-`, which the pinned canonical
upstream parser would treat as another option. UMIs must be present in mapped
read QNAMEs after that literal separator. An `RX` tag alone is not sufficient.

The runner can validate only the alias syntax, not whether its meaning is
actually neutral. Receipts therefore mark alias neutrality as not
machine-verified; the publication review must check every alias.

Example invocation:

```bash
python3 scripts/benchmark/run_benchmark.py \
  --external-bam-manifest /private/manifests/representative-bams.tsv \
  --external-provenance-ledger /private/manifests/provenance.private.json \
  --external-provenance-ledger-sha256 REQUIRED_LEDGER_SHA256 \
  --dumi-ref FINAL_DUMI_COMMIT_SHA \
  --repetitions 8 \
  --xmx 48g \
  --cluster-tag-xmx 48g \
  --sort-command /absolute/path/to/gnu-sort \
  --output-dir /private/evidence/dumi-representative-v2
```

If `--sort-command` is omitted, the runner checks `gsort` and then `sort` and
accepts only a command whose `--version` identifies GNU coreutils. The resolved
path is passed explicitly to the partition checker, recorded as
`<GNU_SORT>`, and accompanied by a path-neutral `gnu_sort` version receipt.
BSD `sort` is not sufficient because the bounded external-sort gate uses GNU
`--buffer-size` and `--temporary-directory`.

A publication-grade v2 external schedule requires exactly four treatments and
eight repetitions. The fourth treatment is forced streaming `on`, so a
workload for which the untimed eligibility probe validly rejects forced
streaming remains useful diagnostic evidence but is classified as exploratory
and nonreportable by the public exporter. Paired inputs are always ineligible
for forced streaming and therefore have only three treatments under the
current contract. A two-treatment or independently validated odd-treatment
publication design is future work, not part of timing design v2.

External mode rejects `--workloads`, `--profile tiny`, `--keep-outputs`,
`--include-intermediate`, and repository-contained output directories. For paired inputs, exactly one
adjacent `.bai` or `.csi` is required because its presence selects the paired
mate-recovery route; the sidecar is validated, hashed, and checked again after
timing.

Before any source build or timing, the runner:

- verifies the manifest SHA-256 against the complete BAM;
- requires `samtools quickcheck` success and `SO:coordinate`;
- builds and deletes a temporary index, which also rejects physical disorder;
- scans every record to confirm declared pairedness and parseable QNAME UMIs;
- makes a verified, read-only private copy of the BAM, copies the required
  paired index or builds a private timing index for single-end input, then
  uses only that snapshot for oracles, warm-ups, contracts, and timing;
- fails early unless scratch capacity covers a complete retained treatment
  block, including Java intermediates, final BAMs, one sort-scratch allowance,
  and headroom, and also covers the deferred Directional-oracle stage's two
  tagged BAMs, source/tagged key streams, canonical membership/root streams,
  external-sort merge scratch, and headroom. The count-based estimate and
  observed free bytes are recorded in the restricted receipt and manifest;
  an insufficient estimate is written before the run fails closed;
- records only a normalized, path-free validation receipt.

For each workload, dUMI `--streaming-mode off` and canonical upstream are each
run once without timing. The dUMI-off result is the exact oracle for dUMI
`off`, `on`, `auto`, and the no-flag default. The canonical result is the exact
oracle only for canonical-upstream cells. Each implementation must match its
own oracle's byte-sorted SAM record multiset (including duplicate
multiplicity), ordered `@SQ` lines, and normalized order-independent `@RG`
lines. Cached sorted
streams are accompanied by private receipts that bind the raw oracle hash,
canonical stream hash and count, ordered `@SQ` and normalized
order-independent `@RG` dictionaries,
alignment-group mode, and alignment-group output-count signature. The checker
validates that receipt before reusing a cache; both streams and receipts are
deleted before sealing.

The two untimed implementation-specific oracles are also compared through a
bounded diagnostic. It records output-count equality, ordered `@SQ` equality,
normalized order-independent `@RG` equality, and the alignment-group
output-count multiset. The
single-end group key is reference, strand, and unclipped 5-prime coordinate;
paired mode also includes template length and counts the non-second mapped
record. Hashes and per-group output multiplicities are retained, while QNAMEs
and record text are not. Exact cross-implementation record equality is
reported separately and is **not** required when the implementations choose
different tied representatives.

This diagnostic does **not** prove equal UMI-cluster membership, equal cluster
partitions, or biological correctness. A false exact-match, header, or
alignment-group diagnostic is reported without redefining the scientific
gate. A false output-count diagnostic marks the comparison
`not_comparable`: `successful_pairs=0`, `failed_pairs=0`,
`noncomparable_pairs=attempted_pairs`, every performance metric has `n=0`,
and its statistics are blank. Scientific `correctness_status` remains `pass`
when the independent gate below passes.

### Independent Directional-oracle gate

After **all** timed cells for **all** external workloads finish, the runner
uses each verified private input snapshot to run canonical upstream and dUMI
`--streaming-mode off` once each with `--tag`. It invokes
`directional_oracle_check.py` on the original source BAM plus both tagged
outputs. The required gate is dUMI-off versus an independently reconstructed
Directional oracle: UMI-cluster membership, RX/root assignment, ordered
`@SQ`, and normalized order-independent `@RG` must all match.
Canonical-upstream versus oracle and
canonical-upstream versus dUMI-off remain diagnostics and may differ. The
runner then invokes `cluster_partition_check.py` on the two tagged outputs as
a legacy pairwise diagnostic; a validated difference does not fail the run.

The helper can also be invoked directly on three private inputs:

```bash
python3 scripts/benchmark/directional_oracle_check.py \
  /private/pre-dedup-source.bam \
  /private/upstream.tagged.bam \
  /private/dumi-off.tagged.bam \
  --umi-length 14 \
  --umi-separator _ \
  --edit-distance 1 \
  --percentage 0.5 \
  --sort-command /absolute/path/to/gnu-sort \
  --receipt /private/directional-oracle.json
```

Use `--mode paired` when both tag runs used `--paired`; pass
`--remove-unpaired` and `--remove-chimeric` exactly when those options were
used to create the tagged outputs. The checker mirrors `DeduplicateSAM`:
single-end alignment groups use reference, strand, and unclipped 5-prime
coordinate, while paired groups also include signed template length and omit
paired records marked second-of-pair. Mate-unmapped records are omitted in
paired mode, unpaired records remain eligible unless explicitly removed, and
cross-reference pairs remain eligible unless explicitly removed.

For each eligible source record, the helper extracts the original QNAME UMI,
counts UMI frequencies per alignment group, and reconstructs Directional
clusters with ordinary string Hamming distance over `A/T/C/G/N`. It mirrors
dUMI's binary32 threshold and deterministic total order without calling the
production clustering or UMI-distance data structures. For each tagged
record, it validates `MI:Z` and `RX:Z`, rejects one UMI split across multiple
MI clusters, and requires each uniform RX root to be a member of its cluster.
Private canonical streams encode both membership and rooted membership as
sorted `(UMI, record-frequency)` data. MI numbers, duplicate flags,
representative choice, record order, QNAME prefix, and `@PG` provenance do
not enter the comparison.

“Independent” here applies to clustering, neighbor distance, threshold
traversal, membership, and root construction. The helper intentionally reuses
the separately audited SAM decoding, QNAME/alignment-key eligibility, header
normalization, and private external-sort serialization code from
`cluster_partition_check.py`; duplicating those transport rules would create a
second, less reviewable interpretation of the input format.

The helper exits zero only when every required dUMI gate component is true,
one for a validated dUMI membership/root/header mismatch, two for an invalid
input or tool/privacy failure, and `128+signal` after `SIGHUP` or `SIGTERM`.
It refuses hardlinked role aliases and existing receipt destinations. The
receipt schema is `dumi-directional-oracle-check-v1`, version 1. Its `gate`
object contains the required dUMI booleans; `diagnostics` contains canonical
upstream comparisons. The runner strictly cross-binds those booleans to the
receipt's counts and digests, binds source/upstream/dUMI hashes and byte counts
to the staged inputs, and binds helper hashes to the seven-file committed
harness snapshot.

The implementation streams `samtools view` through bounded, C-locale external
sorts. Private working directories are mode 0700 and files are mode 0600.
The tag runs use `--cluster-tag-xmx`; when it is omitted, the value inherits
`--xmx`. The effective value and source are recorded in `manifest.json`.
QNAMEs, SAM header text, reference/sample names, UMIs, MI/RX values, source
paths, decoded records, tagged outputs, and private sort streams are deleted
before sealing. The restricted receipt retains aggregate counts, bytes, input
hashes, partition/root fingerprints, and header fingerprints; all are private
by default and are removed from the curated public projection unless a
separate disclosure review authorizes them.

The N-state correctness fix is supported by exhaustive and randomized
synthetic oracle tests. An external representative-BAM run does not add
N-state evidence merely because its inputs contain N-bearing UMIs: a public
claim requires an independently reconstructed partition difference that is
specifically attributable to N state. Public documentation must keep any
restricted-cohort observations qualitative unless a separately reviewed
curated export explicitly approves their disclosure.

Forced `--streaming-mode on` is probed as an untimed eligibility contract
before the schedule is fixed. If it succeeds, `on` is included in the measured
matrix. If it is validly rejected, the receipt records that fact and the
canonical-upstream, dUMI-off, and dUMI-auto cells still run. Auto is classified
from its actual stdout/stderr markers and output header as `streaming`,
`fallback-off`, or `off-ineligible`; fallback is not mislabeled as streaming.
The `end_to_end_ready` route is selected from the observed raw sort order, not
from the requested mode.

The source manifest, rationale text, BAM, paired index, result/tagged BAMs,
SAMs outside the archived source/harness snapshots, and private sorted record
streams are never retained in a completed bundle.
Input-touching stdout/stderr is suppressed after its contract checks because
failure messages can contain QNAMEs. Commands use neutral placeholders, and
the privacy gate scans for the exact source manifest/BAM/index paths in
addition to common private path roots. The retained receipts contain source
content hashes, byte counts, record counts, partition digests, and neutral
aliases. Those facts may themselves be sensitive.

Accordingly, every full external bundle is explicitly marked
`publication_profile: restricted-method-auditable`,
`contains_source_content_hashes: true`, and `automatic_publication: false`.
It is an internal/restricted provenance record, not a public artifact, even
when its paths are neutral. The runner never copies external evidence into
`docs/`. Public distribution requires a separate, deliberate curated export
that removes source-derived hashes and identifiers and receives data-use and
human publication review. Authorization to use a demo or internal asset does
not itself authorize redistribution of its reads or full receipts. External
evidence records only the resolved dUMI commit SHA; a user-supplied branch or
ref spelling is not retained.

Each synthetic UMI is 12 bases. Family parents come from a deterministic
quaternary code with minimum distance at least four; their one-edit children
cannot become ambiguous neighbors of another family. Mapping qualities and
record contents select an unambiguous expected representative.

At the standard defaults, the six workload cells contain 2,945,108 input
alignment records and 474,979,763 bytes of generated SAM before BAM
conversion. The runner records the realized byte counts and hashes; these
figures describe workload volume, not a performance result.

The four-treatment matrix uses a Williams design repeated across eight
repetitions. Every treatment appears twice in every position, and every
directed first-order treatment transition appears twice. The `raw` and
`end_to_end_ready` stages use independently offset schedules rather than
matching the same order across stages. Other treatment counts use the
recorded schedule family, but they are not classified as publication-grade
external evidence.

Runtime and disk usage depend heavily on the host, Java runtime, storage, and
configured scales. For the standard profile, reserve roughly one to two hours,
at least 10 GiB of free disk, and the configured 4 GiB Java heap. These are
planning allowances, not expected performance. Add `--keep-outputs` only when
the individual result BAMs are needed; reserving 25 GiB is prudent in that
case.

## What is timed

Every Java invocation is a fresh process. Before timing begins for a workload,
each implementation receives one untimed warm-up invocation, with the warm-up
order shifted by workload. Immediately before every timed cell, the runner
hash-verifies and fully prereads the exact immutable input BAM and its timing
index. This makes the cache-conditioning operation explicit and identical
across treatments.

Every synthetic matrix cell runs BAM mode with a 12-base UMI. External cells
use the UMI length and literal separator recorded in their input manifest.
Both use directional clustering, `k=1`, `p=0.5`, the `ngrambktree` data
structure, and `mapqual` representative selection. The runner emits Java
`-k 1 -p .5` and helper `--edit-distance 1 --percentage 0.5` explicitly; it
does not rely on either implementation's defaults. The default common JVM
options are:

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

The evidence reports two directly measured stages:

- `raw`: one fresh Java deduplication process and no downstream preparation;
- `end_to_end_ready`: a separate fresh Java deduplication followed, inside the
  same timing boundary, by the route-required preparation of a
  coordinate-sorted, indexed BAM. Streaming output is written to a private
  intermediate and then sorted and indexed; output that is already coordinate
  sorted is indexed directly.

No output or timing value from `raw` is carried into `end_to_end_ready`, and no
derived sum substitutes for a direct end-to-end observation. All treatments
in one repetition-stage block are timed before any of those outputs are
semantically validated and deleted. Comparisons remain stage-matched within
repetition.

The synthetic single-end matrix includes canonical upstream plus dUMI
`--streaming-mode off`, `on`, and `auto`. The synthetic paired matrix includes
canonical upstream, optional PR #32 intermediate, and dUMI `off` and `auto`.
In external mode, forced `on` is included only when its untimed eligibility
contract succeeds; upstream, off, and auto are always retained for a valid
input.

## Correctness gates

For every generated workload, the generator records the exact expected output
record count and a SHA-256 digest of the byte-sorted, non-header SAM record
multiset. Duplicate records remain significant. The runner requires:

- `samtools quickcheck` success;
- the generator-declared record count and semantic digest;
- equality across all implementations and repetitions;
- equality of the ordered `@SQ` and normalized order-independent `@RG`
  dictionaries;
- the expected raw BAM sort order for the selected route;
- absence of an automatic streaming fallback in a result labeled streaming;
- coordinate sort order after the downstream-ready stage;
- a successful no-flag/default-`auto` result matching the same oracle;
- an exact match between the precomputed schedule and all measured cells.

Header sort order is checked independently of record-multiset equivalence.
Streaming output must declare `SO:unsorted`; canonical upstream, dUMI
`--streaming-mode off`, fallback/off-ineligible routes, and paired outputs must
declare `SO:coordinate`.

## Evidence bundle

The output directory contains:

- `STATUS.json`: `RUNNING`, `FAILED`, or `COMPLETE`;
- `manifest.json`: revisions, source bindings, dependency digests, runtime
  identity, JVM options, configuration, harness-file hashes, and the external
  harness-to-dUMI-commit binding;
- `environment.json` and `environment.txt`: host and tool versions; the JSON
  receipt also records load average, CPU affinity, and the CPU scaling governor
  when the host exposes them;
- `harness/`: an exact snapshot of the scripts and this README used for the
  run; external mode records each snapshot path, repository path, and SHA-256
  under `harness_commit_binding` after verifying it against the archived dUMI
  commit;
- `sources/`, `classes/`, `dependencies/`, and `build-commands/`: archived
  source, normalized builds, locked jars, and build receipts;
- `inputs/`: generated workloads, metadata, commands, and hashes;
- `design.tsv`: the complete schedule fixed before measurement;
- `measurements.tsv`: unaggregated process measurements and semantic receipts;
  external rows also record the actual route, implementation-specific oracle,
  exact-oracle status, bounded alignment-group output-count status, and the
  separately reported exact cross-implementation status; after timing, every
  row also records the independent Directional-oracle gate, its dUMI-versus-
  oracle membership/root results, the canonical-upstream diagnostics, and the
  directional receipt path;
- `summary.tsv`: median, minimum, maximum, range, and median absolute deviation
  by implementation and stage;
- `comparisons.tsv`: matched-repetition comparisons with canonical upstream,
  including elapsed speedup, elapsed percent change, and peak-RSS percent
  reduction;
- `correctness.tsv`: per-cell correctness status and diagnostics, including
  the independent external Directional-oracle gate and performance
  comparability classification;
- `oracles/external/*/directional-oracle-receipt.json`: restricted, path-free
  aggregate evidence from the independent source-BAM Directional oracle and
  the two untimed tagged outputs;
- `oracles/external/*/pairwise-cluster-diagnostic-receipt.json`: restricted
  legacy pairwise evidence from the untimed upstream-tagged versus
  dUMI-off-tagged comparison; differences here are diagnostic and do not
  override the independent gate;
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

The trust boundary for downstream use is the unmodified runner-produced
bundle: `STATUS.json` must say `COMPLETE`, and `MANIFEST.sha256` must verify in
full. `STATUS.json` is written only after the manifest is installed. A copied
subset, edited or manually resealed tree, or a bundle left `RUNNING` or
`FAILED` is not equivalent evidence.

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

### Public export of restricted external evidence

Do not publish the restricted external bundle. Use
`export_public_external.py` to verify the runner-produced trust boundary and
create a new hash-free projection under neutral aliases. Prepare these private
inputs first and keep them outside the public output:

- an alias-map JSON file, mode 0600, with `"format": 1` and an `"aliases"`
  object mapping every restricted workload ID to a unique neutral alias such
  as `demo-se-01` or `panel-pe-02`;
- a denylist JSON file, mode 0600, with `"format": 1` and explicit `"tokens"`,
  `"paths"`, and `"hashes"` arrays covering private identifiers, paths, and
  source BAM/index digests. An explicitly empty list means a human reviewed
  that category and found no additional entry; omitting the denylist is not
  allowed;
- a new path for the private export receipt, outside both the restricted and
  public trees, in a directory owned by the current user and inaccessible to
  group or other users.

Both the public output path and private receipt path must not already exist:

```bash
python3 scripts/benchmark/export_public_external.py \
  --bundle /private/evidence/dumi-representative-v2 \
  --output-dir /public/staging/dumi-external-evidence-v2 \
  --alias-map /private/review/aliases.private.json \
  --private-denylist /private/review/denylist.private.json \
  --private-export-receipt /private/review/export-receipt.json \
  --panel-description "Neutral representative RNA sequencing panel" \
  --evidence-set-id external-evidence-representative-v2
```

The exporter re-verifies the restricted seal before and after projection,
checks the exact runner, summarizer, correctness, schedule, dependency, route,
oracle, and input-binding schemas, binds source trees to the named Git commits,
binds dependency bytes to the committed lock, validates the exact build
commands, recomputes public summaries and matched comparisons, removes
private-data-derived hashes and counts, applies the denylist, writes atomically,
and seals the public files with `SHA256SUMS`. Compiled class-tree hashes remain
runner-attested: the exporter verifies them but does not independently rebuild
the Java bytecode. The private receipt binds the restricted manifest, review
inputs, exporter identity, and public tree; it is not part of the public
projection.

Automated export is not publication authorization. A human reviewer must
still confirm that aliases and panel text are neutral, the denylist is
complete, the evidence is authorized for aggregate publication, the schedule
is classified `publication-grade`, every public correctness row passes, no
private or organization-specific language remains, and `SHA256SUMS` verifies.
Publish only the reviewed public projection, never the alias map, denylist,
private receipt, or restricted bundle.

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
- `export_public_external.py` verifies a sealed restricted external bundle and
  creates the curated hash-free public projection described above.

Run any utility with `--help` for its complete command-line contract.
