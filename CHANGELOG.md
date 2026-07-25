# Changelog

Notable dUMI changes are documented here.

This file describes the maintained fork, not historical canonical UMICollapse
releases. See [`PROVENANCE.md`](PROVENANCE.md) for the exact lineage.

## Unreleased

## 2.0.0 - 2026-07-25

### Added

- Guarded streaming for compatible coordinate-sorted, single-end SAM/BAM
  input, with `auto`, `on`, and `off` control.
- Runtime coordinate-order and leading-clipping validation.
- Transactionally staged output for every advertised CLI route and direct
  Java entry point, plus an inner streaming-attempt boundary for safe legacy
  retry in `auto` mode.
- Packed-key `NgramBKTree` routing with a general object-key fallback.
- Differential data-structure tests, randomized reference comparisons, and
  regression coverage for deep trees, `N`-containing keys, threshold edges,
  parallel traversal, resource cleanup, and output transactions.
- SAM/BAM/FASTQ command-line integration tests and a SAM/BAM compatibility and
  failure-safety matrix.
- A public, reproducible benchmark harness with workload provenance,
  correctness gates, structured raw output, and paired baseline/candidate
  execution.
- Java Flight Recorder allocation-profiling tools and aggregation guidance.
- Cross-platform CI on supported Java runtimes, repository-hygiene checks, and
  byte-for-byte reproducible-build verification.
- Checksum-locked runtime dependencies, a Java 11-targeted build, and an
  embedded build receipt identifying the exact production-source inputs.
- A release pipeline that tests the tagged source, assembles a complete
  source-plus-binary archive, verifies the extracted archive, publishes
  checksums, an SPDX SBOM, and the build receipt, and creates a provenance
  attestation.
- Contributor, security, citation, provenance, third-party licensing,
  architecture, optimization, limitations, and presentation-reference
  documentation.
- Structured bug-report and feature-request forms, a pull-request template,
  and automated GitHub Actions dependency updates.

### Changed

- Compatible SAM/BAM runs default to guarded `auto` streaming.
- Default underscore-delimited UMIs use a direct parser and encoder.
- Average base quality is calculated lazily.
- Sparse streaming groups bypass unnecessary general clustering setup.
- Algorithm traversal and quality-based representative ties are deterministic;
  the `any` merge policy remains intentionally arbitrary.
- Component and BK-tree traversal use explicit work queues rather than
  input-sized recursion.
- Paired mate recovery supports indexed queries and sequential recovery for SAM
  or unindexed BAM input.
- The launcher uses JVM heap defaults unless
  `UMICOLLAPSE_JAVA_OPTS` is supplied.
- Streaming output is explicitly declared `SO:unsorted`.
- Runtime dependencies are HTSJDK 3.0.5 and snappy-java 1.1.10.8.
- Generated JARs are no longer tracked; builds and releases are produced from
  source with deterministic archive metadata.

### Incorporated

- The persistent paired-BAM reader change from canonical upstream PR
  [#32](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32), preserving
  its original commit authorship.
- The n-gram object-allocation opportunity independently described in upstream
  PR [#34](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/34).

### Fixed

- Resource cleanup around streaming fallback, paired readers, iterators, and
  writers.
- Output preservation when forced streaming fails.
- Custom UMI separators are interpreted literally.
- Invalid explicit UMI lengths and UMIs shorter than the effective length are
  rejected rather than silently producing ambiguous encodings.
- Invalid modes, options, ranges, strategy combinations, and same-file
  input/output requests fail before output processing, including path aliases
  and hard links that identify the same file.
- Equality/hash contracts, generated-key metadata, and `BitSet` cloning
  preserve all encoded state.
- Threshold arithmetic, packed-key boundaries, queue capacity, and deep-input
  traversal avoid overflow and stack-exhaustion failures.
- Singleton inputs through extension entry points retain the requested merge
  behavior.
- Cross-platform shell behavior for Bash 3 and Linux/macOS SHA-256 tooling.

### Security and supply chain

- GitHub Actions are pinned to immutable commit SHAs and run with restricted
  job permissions.
- Locked Maven artifacts are audited against OSV on relevant pull requests, on
  demand, and on a weekly schedule.
- Release inputs and dependencies are checksum-verified, generated artifacts
  are rejected from source control, and published assets are verified after
  upload.
- Third-party notices and dependency license texts are included in the source
  tree and release archive.

## Canonical upstream baseline

dUMI diverges from canonical UMICollapse commit
[`efeab35`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/commit/efeab35f5d29dec1d496ade3f681eeb34d9c2057).
The subsequent `aeacd82` commit incorporated here comes from unmerged upstream
PR #32 and is not a canonical upstream release.
