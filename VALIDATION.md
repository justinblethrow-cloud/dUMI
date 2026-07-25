# dUMI release-candidate validation record

Status: **not yet accepted** — final frozen-commit checks are pending.

This document defines the evidence required to accept the current dUMI release
candidate. It must not be described as a completed release acceptance while any
`PENDING_...` value remains.

| Field | Value |
| --- | --- |
| Record prepared | 2026-07-25 UTC |
| Candidate version or tag | `PENDING_RELEASE_TAG` |
| Candidate commit | `PENDING_FINAL_COMMIT` |
| Canonical upstream baseline | [`efeab35f5d29dec1d496ade3f681eeb34d9c2057`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/commit/efeab35f5d29dec1d496ade3f681eeb34d9c2057) |
| Final acceptance decision | `PENDING_FINAL_RUN` |

## Scope

Acceptance covers:

- the supported FASTQ, SAM, and BAM command-line routes;
- guarded streaming for compatible coordinate-sorted, single-end SAM/BAM
  input;
- compatibility fallback, paired, tagging, two-pass, and parallel routes;
- clustering and representative-read selection;
- input validation and transactional output replacement;
- dependency locking and advisory review;
- Java 11-compatible compilation, artifact identity, reproducible packaging,
  SBOM generation, and release provenance;
- deterministic synthetic comparison with canonical upstream UMICollapse.

Acceptance does not claim that every sequencing workload, optional HTSJDK
feature, JVM, operating system, or storage system has been tested. It also does
not turn representative-read selection into base-level consensus generation.
The suite validates implementation compatibility and documented invariants on
the recorded fixtures; it does not independently revalidate the biological
model or scientific conclusions of the original UMICollapse publication.
The precise compatibility boundaries are documented in
[`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

## Lineage under test

dUMI is an independently maintained fork of canonical UMICollapse. The merge
base with canonical upstream is `efeab35`. The history also incorporates
`aeacd8231cf8e77c03d03139ed6e65a4c2845015` from upstream
[PR #32](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/pull/32), preserving
that commit's authorship. The `v1.1.0` label found on `aeacd82` in an
intermediate fork is not a canonical upstream release.

[`PROVENANCE.md`](PROVENANCE.md) records the development lineage and upstream
proposals. The frozen candidate commit above, not a mutable branch name or
working tree, is the authority for the code accepted by this record.

## Acceptance environments

The final local gate is scheduled on this environment:

| Property | Value |
| --- | --- |
| Operating system | Linux 6.8.0-134-generic, x86-64 |
| Processor | AMD EPYC 7742 |
| Minimum-runtime gate | Eclipse Temurin 11.0.32+9 |
| Additional-runtime gate | OpenJDK 21.0.11 |
| Compiler target | Java 11 class-file major version 55 |

The published CI matrix is defined by
[`.github/workflows/ci.yml`](.github/workflows/ci.yml):

- Ubuntu 24.04 with Java 11;
- Ubuntu 24.04 with Java 21;
- macOS 15 with Java 11;
- a separate Ubuntu 24.04, Java 11 reproducible-build job.

The CI workflow and its third-party actions are commit-SHA pinned. CI evidence
is authoritative only for the frozen candidate:

- CI run: `PENDING_CI`
- dependency-audit run: `PENDING_CI`
- release run: `PENDING_RELEASE`

## Local verification gate

The primary command is:

```bash
JAVA_HOME=/path/to/jdk ./scripts/check.sh
```

It performs a clean build, verifies the generated artifact, runs the complete
Java and shell test suite, and confirms Java 11 bytecode. Production and test
classes are compiled separately with:

```text
--release 11 -Xlint:all -Werror
```

Final frozen-commit results:

| Gate | Expected result | Candidate result |
| --- | --- | --- |
| Java 11 `./scripts/check.sh` | Exit 0; all tests pass; class major version 55 | `PENDING_FINAL_RUN` |
| Java 21 `./scripts/check.sh` | Exit 0; all tests pass; class major version 55 | `PENDING_FINAL_RUN` |
| `git diff --check` | Exit 0 | `PENDING_FINAL_RUN` |
| Shell and workflow syntax checks | Exit 0 | `PENDING_FINAL_RUN` |
| Neutral-branding scan | No organization-specific or private path references | `PENDING_FINAL_RUN` |

## Test and regression matrix

[`test.sh`](test.sh) is the manifest for the test suite. The frozen candidate
must pass every row below on both local acceptance JDKs.

| Test surface | Evidence | Contract exercised | Candidate result |
| --- | --- | --- | --- |
| Encoded UMI state | [`TestBitSet.java`](src/test/TestBitSet.java) | Distance, mutation, cloning, equality, hashing, ordering, and `N` metadata | `PENDING_FINAL_RUN` |
| Sequential structures | [`TestDataStructures.java`](src/test/TestDataStructures.java) | Reference behavior across the sequential data-structure implementations | `PENDING_FINAL_RUN` |
| Parallel structures | [`TestParallelDataStructures.java`](src/test/TestParallelDataStructures.java) | Parallel data-structure behavior | `PENDING_FINAL_RUN` |
| Optimized parser and core | [`TestOptimizedRegressions.java`](src/test/TestOptimizedRegressions.java) | Fast UMI parsing, lazy quality calculation, singleton semantics, and packed n-gram routing | `PENDING_FINAL_RUN` |
| Generated-key N handling | [`TestNKeyRegressions.java`](src/test/TestNKeyRegressions.java) | `Combo`, `Trie`, and `SymmetricDelete` parity with `Naive` for `N`-containing UMIs | `PENDING_FINAL_RUN` |
| Threshold and parallel ties | [`TestThresholdParallelRegressions.java`](src/test/TestThresholdParallelRegressions.java) | Historical float rounding, saturation, and reverse-insertion determinism for the parallel algorithms | `PENDING_FINAL_RUN` |
| Parallel traversal scheduling | [`TestParallelTraversalScheduling.java`](src/test/TestParallelTraversalScheduling.java) | Dense-component single scheduling plus sequential/parallel representative parity | `PENDING_FINAL_RUN` |
| Deep BK-tree traversal | [`TestBKTreeDepthRegressions.java`](src/test/TestBKTreeDepthRegressions.java) | Stack-safe deep search, removal, statistics, sequential/parallel parity, and concurrent queries | `PENDING_FINAL_RUN` |
| Resource and bound handling | [`TestResourceAndBoundsRegressions.java`](src/test/TestResourceAndBoundsRegressions.java) | Malformed-input cleanup, repeated file-descriptor checks, auto-detected `k` parity, construction failure, and exact counter overflow | `PENDING_FINAL_RUN` |
| Release correctness | [`TestReleaseRegressions.java`](src/test/TestReleaseRegressions.java) | Literal separators, short-UMI rejection, deterministic ties, equality contracts, argument bounds, and packed-map capacity | `PENDING_FINAL_RUN` |
| SAM/BAM hardening | [`TestDeduplicateSAMHardening.java`](src/test/TestDeduplicateSAMHardening.java) | Selected-pair mate recovery for SAM, indexed BAM, and unindexed BAM; singleton routing; output-format fallback; and alignment-key contracts | `PENDING_FINAL_RUN` |
| N-gram differential matrix | [`TestNgramBKTreeRegression.java`](src/test/TestNgramBKTreeRegression.java) | `NgramBKTree` and `SortNgramBKTree` versus `Naive` over 86 deterministic randomized scenarios, including packed-key boundaries at UMI lengths 255 and 256 | `PENDING_FINAL_RUN` |
| Streaming and route integration | [`test/test-streaming.sh`](test/test-streaming.sh) | SAM/BAM parity, algorithms, merge policies, ten sequential structures, header semantics, runtime guards, fallback, legacy/two-pass/parallel parity, tagging, paired recovery, and destination preservation | `PENDING_FINAL_RUN` |
| CLI, FASTQ, and transactions | [`test/test-cli.sh`](test/test-cli.sh) | Help/version, invalid values and combinations, same-file and hard-link rejection, literal separators, output-format rules, runtime failure cleanup, multi-read FASTQ collapse/tagging, gzip, and atomic destination replacement | `PENDING_FINAL_RUN` |

### Semantic-equivalence rules

Correctness is evaluated separately from record order:

1. Integration tests decode outputs with HTSJDK in strict mode, sort complete
   SAM record strings, and compare the resulting record multisets.
2. The public benchmark decodes each SAM/BAM output with `samtools`, byte-sorts
   all non-header records under the C locale, and hashes the exact stream.
   Duplicate records remain significant.
3. Record count and semantic SHA-256 must agree across canonical upstream and
   the compared dUMI modes for a benchmark cell. A mismatch aborts the run.
4. Header semantics are checked independently. The legacy and two-pass routes
   retain coordinate-sort metadata where applicable. Streaming output is
   deliberately declared `SO:unsorted`.
5. The no-flag dUMI result must match explicit `auto` mode and must select the
   expected route for each workload.

Final benchmark semantic-equivalence result: `PENDING_FINAL_BENCHMARK`.

### Transactional and CLI safety

Every advertised command-line route writes to a same-directory staged file and
promotes it only after processing and writer closure succeed. The regression
suite verifies, at minimum:

- malformed input cannot replace an existing destination;
- a failed forced-streaming run cannot replace an existing destination;
- staged files are removed after failure;
- normalized aliases and hard links cannot make input and output the same
  file;
- unknown modes, options, strategies, invalid numeric ranges, duplicate
  options, and unsupported combinations fail before output processing;
- explicit UMI length zero and UMIs shorter than the effective length are
  rejected instead of aliasing another encoded UMI;
- custom UMI separators are interpreted literally.

Final transactional/CLI result: `PENDING_FINAL_RUN`.

### Paired and deterministic behavior

Paired regressions cover indexed BAM query recovery, sequential recovery from
SAM and unindexed BAM, cross-reference flushing, and final-reference mate
recovery. Output must retain both ends of every selected pair in the fixtures.

Mapping-quality and average-quality ties use a stable record-content ordering.
Directional, adjacency, connected-components, and their applicable parallel
variants use stable UMI ordering and iterative component traversal. The
`any` merge policy remains intentionally arbitrary and is not represented as
deterministic.

Final paired and determinism result: `PENDING_FINAL_RUN`.

## Locked dependencies and advisory review

[`dependencies.lock`](dependencies.lock) is the source of dependency names,
Maven Central URLs, and SHA-256 checksums:

| Maven artifact | Locked version | SHA-256 |
| --- | --- | --- |
| `com.github.samtools:htsjdk` | 3.0.5 | `8d03dc7672199f10fe4bad8aaf76259e36d15ed8fb145d6427ef1efb51a4da5f` |
| `org.xerial.snappy:snappy-java` | 1.1.10.8 | `50485d06037fea3d6e40c968386feeca6338cc9872e25549593ff3eb352cefcc` |

The selected versions replace HTSJDK 2.19.0 and snappy-java 1.1.7.3. A
2026-07-25 review placed the selected versions outside the affected ranges for
the following known advisories:

- HTSJDK:
  [`GHSA-96vh-4rfp-c42c`](https://github.com/advisories/GHSA-96vh-4rfp-c42c);
- snappy-java:
  [`GHSA-qcwq-55hx-v3vh`](https://github.com/advisories/GHSA-qcwq-55hx-v3vh),
  [`GHSA-fjpj-2g6w-x25r`](https://github.com/advisories/GHSA-fjpj-2g6w-x25r),
  [`GHSA-pqr6-cmr2-h8hf`](https://github.com/advisories/GHSA-pqr6-cmr2-h8hf),
  and
  [`GHSA-55g7-9cwv-5qfv`](https://github.com/advisories/GHSA-55g7-9cwv-5qfv).

The final network-based check is:

```bash
python3 scripts/audit-dependencies.py
```

It queries OSV by Maven package URL, ignores withdrawn entries, fails when an
active advisory is returned, and treats an unreachable or malformed OSV
response as an audit error rather than a clean result.

Final OSV result: `PENDING_DEPENDENCY_AUDIT`.

An advisory query is a point-in-time check, not proof that a dependency is free
of vulnerabilities. Optional HTSJDK features outside the exercised
SAM/BAM/FASTQ surface are not implicitly accepted by this record.

## Artifact, reproducibility, SBOM, and provenance

[`build.sh`](build.sh) verifies the dependency lock, cleans prior class output,
compiles production and test sources separately, and packages production
classes only. The JAR embeds `META-INF/dumi-build.properties`, which records:

- production-source and complete build-input hashes;
- manifest-template and effective-manifest hashes;
- the dependency-lock hash;
- compiler and archiver versions;
- Java target, Git commit, and clean/dirty input state;
- implementation version and normalized archive timestamp.

[`scripts/verify-artifact.sh`](scripts/verify-artifact.sh) recalculates those
inputs, verifies manifest identity and dependency checksums, and rejects test
classes in the production JAR.

[`scripts/check-reproducible-build.sh`](scripts/check-reproducible-build.sh)
builds the same version twice and requires byte-for-byte identical JARs.
[`scripts/build-release.sh`](scripts/build-release.sh) assembles the
self-contained archive, an SPDX 2.3 SBOM, build receipt, and SHA-256 checksum
file. The release workflow reruns the full gate and reproducibility check,
builds the assets from the tag, requests GitHub build-provenance attestations,
and publishes only those tagged assets.

| Receipt | Candidate value |
| --- | --- |
| Complete build-input SHA-256 | `PENDING_FINAL_RUN` |
| Production-source SHA-256 | `PENDING_FINAL_RUN` |
| JAR SHA-256, first build | `PENDING_FINAL_RUN` |
| JAR SHA-256, second build | `PENDING_FINAL_RUN` |
| Byte-for-byte JAR comparison | `PENDING_FINAL_RUN` |
| Release archive SHA-256 | `PENDING_RELEASE` |
| SPDX SBOM generation and structural inspection | `PENDING_RELEASE` |
| GitHub provenance attestation | `PENDING_RELEASE` |

The generated `umicollapse.jar`, build directories, and release directory are
not source-controlled release authority. The tagged source, dependency lock,
embedded receipt, checksums, and attestation together establish the
source-to-artifact chain.

## Benchmark acceptance

The benchmark implementation is under
[`scripts/benchmark/`](scripts/benchmark/), and the results and interpretation
belong in [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md). The acceptance design
requires:

- compilation from archived, exact Git commits, excluding uncommitted
  production sources;
- canonical upstream `efeab35` as the principal baseline;
- a common JDK, dependency set, heap, stack, and active-processor setting;
- deterministic sparse, moderate-density, adversarial hotspot, and paired
  workloads;
- seven repetitions by default with a rotating Latin-order schedule;
- separate raw deduplication and downstream-ready sort/index measurements;
- monotonic nanosecond wall timing, with GNU `time` supplying user/system CPU
  time, CPU utilization, process exit status, and maximum RSS;
- exact order-independent record-multiset hashes, counts, output sort order,
  commands, environment capture, and a checksum manifest;
- explicit comparison of dUMI `off`, `on`, and `auto` so shared-core and
  streaming effects are not conflated.

No performance number is accepted unless its exact candidate commit,
workload, runtime identity, repetition count, semantic-equivalence result, and
raw evidence are recorded. Results are workload-specific and must not be
generalized into a universal speed or memory claim.

Final benchmark evidence location: `PENDING_FINAL_BENCHMARK`.

## Profiling evidence

Profiling is used to identify allocation opportunities; it is not substituted
for correctness tests or end-to-end benchmarks.

A pre-final diagnostic Java Flight Recorder sample used a deterministic
300,000-record singleton-group BAM under forced streaming. The input BAM
SHA-256 was
`a76e369cf850c0ec131423677a87f240868a59366b1a8351bcd12b05dfa3a215`.
`jdk.ObjectAllocationSample` weights attributed approximately 272 MiB of
sampled allocation to the run, concentrated in general n-gram setup
(approximately 157 MiB), directional setup (approximately 59 MiB), disabled
cluster tracking (approximately 48 MiB), and reflective construction
(approximately 8 MiB). These weights are statistical estimates of allocation,
not exact retained bytes or peak RSS.

That diagnostic motivated a streaming singleton bypass and lazy disabled
tracking state. It predates the frozen candidate and is therefore not a release
acceptance result. The raw recording is not distributed as a repository
artifact.

The frozen candidate must be profiled on the same workload, with the command,
runtime, input hash, recording hash, summarized allocation weights, and
interpretation retained in the final evidence package.

Final post-change profile result: `PENDING_FINAL_PROFILE`.

## Known limits and deferred work

- Streaming applies only to compatible coordinate-declared, single-end,
  sequential SAM/BAM processing without tagging or `--two-pass`.
- Its working set is bounded by the active coordinate window, not by a
  constant. Dense hotspots can still require substantial memory.
- Streaming output is `SO:unsorted`; coordinate-dependent consumers must sort
  it before indexing or use.
- A late `auto` guard violation discards the staged attempt and rereads the
  input through the legacy route.
- FASTQ, paired, tagged, two-pass, and parallel modes do not use the streaming
  route. They require mode-specific benchmarks before any performance claim.
- UMIs are extracted from read names; UMI tags such as `RX` are not input
  sources.
- No new asynchronous-I/O or parallel streaming pipeline is included.
- Synthetic evidence is reproducible but does not predict every biological
  dataset, UMI distribution, alignment density, CIGAR pattern, filesystem, or
  compression behavior.
- CRAM, SRA access, variant processing, and other optional HTSJDK surfaces are
  outside the tested interface.

These are documented boundaries, not hidden acceptance exceptions. Future work
should be prioritized only when a representative workload and semantic
equivalence test justify it.

## Final acceptance checklist

- [ ] Candidate commit and release tag are frozen and recorded.
- [ ] Generated JARs and build outputs are not tracked.
- [ ] Java 11 and Java 21 local gates pass from the frozen commit.
- [ ] Linux and macOS CI jobs pass for the frozen commit.
- [ ] Strict compilation and all regression suites pass.
- [ ] Consecutive release-candidate builds are byte-for-byte identical.
- [ ] Locked dependency checksums and the final OSV audit pass.
- [ ] Benchmark outputs are semantically equivalent and the evidence manifest
      verifies.
- [ ] Final benchmark results and limitations are published in
      `docs/PERFORMANCE.md`.
- [ ] The post-change profile receipt is retained and interpreted.
- [ ] The release archive, SPDX SBOM, checksums, receipt, and provenance
      attestation are present.
- [ ] Public documentation contains no organization-specific branding,
      private paths, or unsupported performance claims.
- [ ] Independent code, benchmark, documentation, and release-workflow reviews
      have no unresolved release-blocking findings.

Final acceptance decision: `PENDING_FINAL_RUN`.
