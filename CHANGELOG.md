# Changelog

Notable dUMI changes are documented here. The project has not yet published a
versioned dUMI release, so current work remains under **Unreleased**.

This file describes the maintained fork, not historical canonical UMICollapse
releases. See [`PROVENANCE.md`](PROVENANCE.md) for the exact lineage.

## Unreleased

### Added

- Guarded streaming for compatible coordinate-sorted, single-end SAM/BAM
  input, with `auto`, `on`, and `off` control.
- Runtime coordinate-order and leading-clipping validation.
- Transactionally staged output for every advertised CLI route, plus an inner
  streaming attempt boundary for legacy retry in `auto` mode.
- Packed-key `NgramBKTree` routing with a general object-key fallback.
- Differential data-structure tests and a SAM/BAM compatibility and
  failure-safety matrix.
- Checksum-locked runtime dependencies, a clean Java 11-targeted build, and an
  embedded production-source receipt.
- Cross-platform CI coverage.

### Changed

- Compatible SAM/BAM runs default to guarded `auto` streaming.
- Default underscore-delimited UMIs use a direct parser and encoder.
- Average base quality is calculated lazily.
- Sparse streaming groups bypass unnecessary general clustering setup.
- Algorithm traversal and quality-based representative ties are deterministic;
  the `any` merge policy remains intentionally arbitrary.
- Component traversal uses explicit work queues rather than component-sized
  recursion.
- Paired mate recovery supports indexed queries and sequential recovery for SAM
  or unindexed BAM input.
- The launcher uses JVM heap defaults unless
  `UMICOLLAPSE_JAVA_OPTS` is supplied.
- Streaming output is explicitly declared `SO:unsorted`.

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
  input/output requests fail before output processing.
- Equality/hash contracts and `BitSet` cloning preserve all encoded state.
- Cross-platform shell behavior for Bash 3 and Linux/macOS SHA-256 tooling.
- Packed-key boundary and capacity handling.

## Canonical upstream baseline

dUMI diverges from canonical UMICollapse commit
[`efeab35`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/commit/efeab35f5d29dec1d496ade3f681eeb34d9c2057).
The subsequent `aeacd82` commit incorporated here comes from unmerged upstream
PR #32 and is not a canonical upstream release.
