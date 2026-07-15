# dUMI repository provenance

This repository is the migrated, independently owned dUMI fork used for DGE
UMI collapsing. The migration preserves the complete optimization branch and
separates it from the upstream UMICollapse remote.

## Git lineage

- Canonical upstream remote: `upstream` (`Daniel-Liu-c0deb0t/UMICollapse`)
- Intermediate fork remote: `origin` (`siddharthab/UMICollapse`)
- Owned dUMI remote: `fork` (`justinblethrow-cloud/dUMI`)
- Upstream base: `aeacd82`, tagged `v1.1.0`
- Active and GitHub default branch: `optimization/streaming-fastpath`
- Guarded fast-path tag: `dge-streamingfastpath-guarded-20260522`

Optimization commits carried by the migration:

1. `d56aa49` — accepted DGE UMICollapse optimizations
2. `92cccc2` — coordinate-sorted SAM streaming fast path
3. `5b52d6a` — explicit streaming-mode switch
4. `56e63f2` — coordinate-order guard
5. `f133fd8` — Java test launcher repair
6. `77ea792` — migrated-fork hardening and acceptance gate
7. `d59df6d` — Node 24-compatible CI actions

The default-branch hardening following migration adds cross-platform build
controls, resource-safe streaming and paired-reader cleanup, indexed-BAM
regressions, artifact provenance, and Linux/macOS CI. The exact final commit is
recorded by Git history rather than duplicated in this document.

The consolidated upstream submission is
[UMICollapse PR #37](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/37).
That PR defaults streaming to `off` for upstream compatibility; dUMI
intentionally retains guarded `auto` streaming as its default.

## Producer and artifact contract

`src/umicollapse/` is the production source of truth. `./build.sh` creates
`umicollapse.jar` from a clean class directory and embeds
`META-INF/dumi-build.properties`, whose `source.sha256` value covers every
production Java source path and file content. `./scripts/verify-artifact.sh`
rejects a stale JAR or one containing test classes.

The runtime dependency contract is `dependencies.lock`. Downloaded JARs live
under ignored `lib/` and are accepted only when their SHA-256 digest matches the
lock file.

## Streaming contract

Streaming is eligible only for coordinate-declared, single-end, sequential,
untagged SAM/BAM runs using sequential algorithms and data structures. It
validates actual reference/start monotonicity while reading.

Positive-strand grouping uses unclipped starts. The default retained window is
10,000 leading clipped bases. Every positive read is checked before any group
is flushed. In `auto` mode, a record outside that bound or an actual order
regression causes the temporary streaming output to be discarded and the run
to restart through the legacy path. In forced `on` mode, the command fails and
does not replace an existing destination. Negative-strand groups flush at their
unclipped ends and do not require an analogous lag.

Streaming can change output record order, so its output header is explicitly
`SO:unsorted`. Non-streaming, two-pass, paired, and tagged paths retain the
upstream HTSJDK sort contract.

## Acceptance gate

Before publishing a change, run:

```
./scripts/check.sh
```

The gate requires checksum-verified dependencies, a clean Java 11-targeted
build, a source-matching production JAR, unit/randomized equivalence tests,
indexed-BAM and paired-reader regressions, and streaming parity plus error-path
tests. GitHub Actions runs it on Linux with Java 11 and 21 and on macOS with
Java 11. See `VALIDATION.md` for the latest recorded run.
