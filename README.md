# dUMI

[![CI](https://github.com/justinblethrow-cloud/dUMI/actions/workflows/ci.yml/badge.svg)](https://github.com/justinblethrow-cloud/dUMI/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

dUMI is an independently maintained fork of
[UMICollapse](https://github.com/Daniel-Liu-c0deb0t/UMICollapse), a Java tool
for deduplicating reads with Unique Molecular Identifiers (UMIs). It retains
the UMICollapse command-line model and clustering choices while adding:

- guarded streaming for compatible coordinate-sorted, single-end SAM/BAM
  inputs;
- optimized UMI parsing, representation, neighbor search, and clustering
  paths;
- deterministic quality-based representative selection;
- transactional output handling, stricter input validation, and broader
  regression coverage;
- checksum-locked dependencies and reproducible Java 11-targeted builds.

The installed executable remains named `umicollapse` for command-line
compatibility.

dUMI is based on canonical UMICollapse commit
[`efeab35`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/commit/efeab35f5d29dec1d496ade3f681eeb34d9c2057)
and directly incorporates commit
[`aeacd82`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32/commits/aeacd8231cf8e77c03d03139ed6e65a4c2845015)
from upstream [PR #32](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32).
The latter is incorporated proposal history, not a canonical upstream
`v1.1.0` release. See [Project provenance](PROVENANCE.md) for the complete
lineage and the consolidated upstream submission.

## How deduplication works

For single-end SAM/BAM input, dUMI:

1. groups mapped reads by reference, strand, and unclipped alignment
   coordinate;
2. merges reads with the same UMI at that coordinate;
3. clusters nearby UMIs with the selected directional, adjacency, or connected
   components algorithm; and
4. writes one selected input read for each retained cluster, unless `--tag` is
   requested.

The result is a **representative read**, selected by the configured merge
policy. dUMI does not construct a new base-level consensus sequence.
Paired mode extends the alignment key with template information and recovers
the retained representative's reverse mate.

FASTQ mode instead groups reads by length and treats the entire read sequence
as the UMI-like clustering key.

## Installation

### Versioned release

For a published version, the preferred installation is the self-contained
archive from [GitHub Releases](https://github.com/justinblethrow-cloud/dUMI/releases).
Download `dumi-VERSION.tar.gz` and `SHA256SUMS`, verify the archive checksum,
and then run:

```bash
grep ' dumi-VERSION.tar.gz$' SHA256SUMS | sha256sum --check -
tar -xzf dumi-VERSION.tar.gz
cd dumi-VERSION
./umicollapse --version
```

The release archive contains the launcher, production JAR, locked runtime
dependencies, [third-party notices](THIRD_PARTY_NOTICES.md), license material,
software bill of materials, and build receipt. It requires Bash and a Java 11
or newer runtime. On macOS, replace `sha256sum --check` with
`shasum -a 256 --check`.

If no versioned dUMI release is listed yet, build from source.

### Build from source

Source builds require a JDK 11 or newer, Bash, `curl`, and either `sha256sum`
(Linux) or `shasum` (macOS):

```bash
git clone https://github.com/justinblethrow-cloud/dUMI.git
cd dUMI
./build.sh
./umicollapse --version
```

`build.sh` downloads only the artifacts named in
[`dependencies.lock`](dependencies.lock), verifies their SHA-256 digests, and
compiles Java 11-compatible bytecode.

As of 2026-07-25, Bioconda's `umicollapse` package and the nf-core
[`umicollapse` module](https://nf-co.re/modules/umicollapse) resolve to the
existing `1.1.0` package built from the intermediate
`siddharthab/UMICollapse` fork, not dUMI. See
[Project provenance](PROVENANCE.md) for why that distinction matters.

## Quick start

SAM/BAM mode expects the UMI in each read name. With the default separator, a
read name can end in an underscore followed by bases such as
`read-0001_ACGTACGT`.

For a coordinate-sorted, single-end BAM:

```bash
./umicollapse bam \
  -i input.coordinate.bam \
  -o deduplicated.unsorted.bam
```

The default `--streaming-mode auto` uses the guarded streaming route when the
input and option combination are eligible. Streaming output is deliberately
marked `SO:unsorted`, because completed coordinate groups may be emitted in a
different order. Sort it before indexing or passing it to a consumer that
requires coordinate order:

```bash
samtools sort -o deduplicated.coordinate.bam deduplicated.unsorted.bam
samtools index deduplicated.coordinate.bam
```

`samtools` is needed only for this downstream sort/index example, not to run
dUMI itself.

To deduplicate FASTQ reads by their full sequence:

```bash
./umicollapse fastq -i input.fastq -o deduplicated.fastq
```

Run `./umicollapse --help` for the built-in synopsis.

## Input and output contracts

### SAM/BAM UMIs

- UMIs are read from the read name, not from tags such as `RX`.
- `--umi-sep` is a literal string; regular-expression characters have no
  special meaning.
- `-u -1` sets the effective UMI length from the first eligible read.
  Subsequent shorter UMIs are rejected and longer UMIs are truncated to that
  length. A positive `-u` establishes the same contract explicitly.
- `-k` must satisfy `0 <= k < effective UMI length`; with autodetection, that
  relationship can be checked only after the first eligible read is parsed.
- The UMI alphabet accepted by the read-name parser is `A`, `T`, `C`, `G`, and
  `N`, case-insensitively.
- Unmapped reads are removed by default. `--keep-unmapped` retains them in
  single-end SAM/BAM mode.

Both `sam` and `bam` select the aligned-read pipeline. HTSJDK detects the input
format; an output name ending in `.sam` produces text SAM, while other aligned
output names produce BAM.

### Representative selection

The default merge policy is `mapqual` for SAM/BAM and `avgqual` for FASTQ.
Mapping-quality and average-quality ties use stable record-content ordering.
The `any` policy intentionally keeps an arbitrary encounter-order
representative. Stable quality ties and stable equal-frequency UMI ordering
are deliberate dUMI refinements: when multiple choices are equally valid
under the configured rules, dUMI can select a different representative or
cluster seed than upstream's encounter- or hash-order-dependent choice.

With `--tag`, reads are retained and annotated with cluster information instead
of removing duplicate records. In SAM/BAM output, duplicate records are also
marked with the SAM duplicate flag. Tagging uses a non-streaming path.

### Transactional output

Every command writes to a temporary file beside the requested destination and
promotes it only after successful processing. Invalid invocations are rejected
before processing, input and output cannot identify the same file, and a
processing failure does not replace an existing destination.

## Streaming behavior

Streaming is eligible when all of the following are true:

- the mode is `sam` or `bam`;
- the input header declares `SO:coordinate`;
- processing is single-end and sequential;
- `--tag` and `--two-pass` are not enabled.

`--streaming-mode` controls the route:

| Value | Behavior |
| --- | --- |
| `auto` | Default. Use streaming when eligible; if actual coordinate order or the clipping-window contract is violated, discard the attempt and retry through the legacy path. |
| `on` | Require streaming. An incompatible configuration or runtime contract violation fails without promoting incomplete output. |
| `off` | Always use the compatible legacy path. |

Positive-strand unclipped starts can precede coordinate-sort positions because
of leading clipping. The streaming route therefore retains groups behind a
10,000-base default window and validates each read before releasing a group.
This produces a **coordinate-window-bounded working set**, not a constant-memory
guarantee. Density, unique UMIs per coordinate, read payloads, and JVM behavior
still affect peak memory.

The clipping allowance can be raised with a JVM system property:

```bash
UMICOLLAPSE_JAVA_OPTS='-Dumicollapse.streaming.positiveLag=20000' \
  ./umicollapse bam -i input.bam -o output.bam
```

`UMICOLLAPSE_JAVA_OPTS` is split on whitespace by the launcher; shell quoting
embedded inside its value is not parsed a second time. The launcher otherwise
uses the JVM's heap defaults. For incompatible or unusually dense workloads,
use `--streaming-mode off`, consider `--two-pass`, or set an explicit heap
limit appropriate for the input.

## Command-line reference

Usage:

```text
umicollapse <fastq|sam|bam> -i INPUT -o OUTPUT [options]
umicollapse --help
umicollapse --version
```

### Core options

| Option | Meaning | Default |
| --- | --- | --- |
| `-i PATH` | Input file; required. | — |
| `-o PATH` | Output file; required and different from the input. | — |
| `-k N` | Maximum substitution edits used to find neighboring UMIs. | `1` |
| `-u N` | UMI length; `-1` autodetects in SAM/BAM. In FASTQ, a positive value trims that prefix from emitted sequence and quality without changing the full-sequence clustering key. | `-1` |
| `-p FRACTION` | Non-negative directional-algorithm frequency threshold. | `0.5` |
| `--algo NAME` | `dir`, `adj`, or `cc`. | `dir` |
| `--data NAME` | Neighbor-search data structure; see below. | `ngrambktree` |
| `--merge NAME` | `mapqual`, `avgqual`, or `any`. `mapqual` is not available in FASTQ mode. | `mapqual` for SAM/BAM; `avgqual` for FASTQ |
| `-t N` | Parallelize separate alignment or read-length groups with `N` threads. | sequential |
| `-T N` | Parallelize clustering within a group with `N` threads. | sequential |
| `--tag` | Retain and annotate cluster members rather than removing duplicates. | off |

Sequential `--data` choices are `naive`, `combo`, `ngram`, `delete`, `trie`,
`bktree`, `sortbktree`, `ngrambktree`, `sortngrambktree`, and
`fenwickbktree`. With `-T`, the supported choices are `naive`, `bktree`, and
`fenwickbktree`; if `--data` is omitted, `-T` defaults to `bktree`.

### SAM/BAM options

| Option | Meaning | Default |
| --- | --- | --- |
| `--umi-sep STRING` | Literal separator immediately before the read-name UMI. | `_` |
| `--two-pass` | Use the alternative sequential two-pass path. | off |
| `--paired` | Deduplicate paired alignments using the forward read and template length, then recover retained reverse mates. | off |
| `--remove-unpaired` | Remove unpaired reads; requires `--paired`. | off |
| `--remove-chimeric` | Remove pairs mapped to different references; requires `--paired`. | off |
| `--keep-unmapped` | Retain unmapped single-end reads. | off |
| `--streaming-mode MODE` | `auto`, `on`, or `off`, as described above. | `auto` |

The CLI rejects unsupported combinations before opening an output writer.
Notably, `-t` and `-T` are mutually exclusive; paired mode cannot use `-t` or
`--keep-unmapped`; `--tag` cannot use a parallel mode or `--two-pass`; and
forced streaming cannot use paired, parallel, tagging, or two-pass routes.

## Build, test, and validation

The runtime dependency set is intentionally small:

- HTSJDK 3.0.5;
- snappy-java 1.1.10.8.

Exact download locations and SHA-256 digests are in
[`dependencies.lock`](dependencies.lock).

Run the complete local acceptance gate with:

```bash
./scripts/check.sh
```

The gate performs a clean strict build, verifies the packaged artifact and
Java 11 bytecode target, and runs unit, differential, CLI, SAM/BAM, streaming,
failure-safety, and paired-reader regressions. CI runs the gate on Linux with
Java 11 and Java 21 and on macOS with Java 11.

Check byte-for-byte build reproducibility separately with:

```bash
./scripts/check-reproducible-build.sh
```

Measured comparisons with canonical upstream, including exact commits,
workloads, repetition counts, runtime settings, output-equivalence checks, and
limitations, are recorded in [Performance](docs/PERFORMANCE.md). The small
[`benchmark-streaming.sh`](scripts/benchmark-streaming.sh) harness is a local
on/off regression signal, not a production-wide performance claim. The
[reproducible benchmark harness](scripts/benchmark/README.md) also supports
privacy-hardened, manifest-driven replication on representative external BAMs;
its full external evidence bundles remain restricted and require deliberate
curation before any public release.

## Documentation

| Document | Purpose |
| --- | --- |
| [Architecture](docs/ARCHITECTURE.md) | Canonical upstream and resulting dUMI architectures, including streaming invariants. |
| [Optimization traceability](docs/OPTIMIZATIONS.md) | Opportunity, implementation, semantic contract, validation, and evidence for each change. |
| [Performance](docs/PERFORMANCE.md) | Reproducible benchmark methodology and workload-scoped results. |
| [Validation](VALIDATION.md) | Current correctness, compatibility, build, and dependency acceptance record. |
| [Limitations](docs/LIMITATIONS.md) | Eligibility, memory, ordering, input, and platform boundaries. |
| [Project provenance](PROVENANCE.md) | Canonical baseline, incorporated proposals, fork lineage, attribution, and license. |
| [Allocation profiling](scripts/profile/README.md) | Reproducible, path-neutral JFR sampling and aggregation for the sparse streaming path. |
| [Changelog](CHANGELOG.md) | User-visible changes by release. |
| [Presentation reference](docs/presentation-reference/STORYBOARD.md) | Neutral source material, figure specifications, and claims ledger for a short technical presentation. |

## Known limitations

- The streaming route does not cover FASTQ, paired, tagging, two-pass, or
  parallel modes. Those modes use compatible non-streaming paths.
- Streaming output is unsorted and must be sorted before coordinate indexing.
- UMIs must be present in read names; SAM UMI tags are not currently an input
  source.
- CRAM, SRA access, and other optional HTSJDK surfaces are outside the tested
  and supported dUMI interface.
- dUMI selects existing representative reads rather than constructing
  consensus sequences.
- Performance is workload- and environment-dependent. Synthetic results do not
  imply a universal production gain.

See [Limitations and compatibility boundaries](docs/LIMITATIONS.md) for
details.

## Citation

If you use dUMI, cite the software metadata in [`CITATION.cff`](CITATION.cff)
and the original UMICollapse publication:

> Daniel Liu. “Algorithms for efficiently collapsing reads with Unique
> Molecular Identifiers.” *PeerJ* 7:e8275 (2019).
> <https://doi.org/10.7717/peerj.8275>

## Contributing, support, and security

Bug reports and feature requests are welcome through
[GitHub Issues](https://github.com/justinblethrow-cloud/dUMI/issues). Before
submitting a change, read [`CONTRIBUTING.md`](CONTRIBUTING.md).

Do not disclose vulnerability details or sensitive sequencing data in a public
issue. Follow [`SECURITY.md`](SECURITY.md) for private reporting guidance.

## License

dUMI remains available under the upstream [MIT License](LICENSE). Retained
upstream copyright and permission terms apply.
