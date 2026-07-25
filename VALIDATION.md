# dUMI v2.0.0 validation record

Status: **source acceptance and v2.0.0 release publication complete**.

This record accepts the exact production-source and measured-harness freeze
below and records the completed v2.0.0 publication. Because tagged source
cannot self-identify workflow runs or assets generated after its commit, the
final identities, hashes, and attestations are recorded here from Git, GitHub
Actions, the published release, and independently downloaded assets.

| Field | Value |
| --- | --- |
| Record finalized | 2026-07-25 UTC |
| Release identity | [`v2.0.0`](https://github.com/justinblethrow-cloud/dUMI/releases/tag/v2.0.0) |
| Production-source and measured-harness freeze | [`299532964a57905c835bd750563988a09af6e1df`](https://github.com/justinblethrow-cloud/dUMI/commit/299532964a57905c835bd750563988a09af6e1df) |
| Final release commit | [`92680cd98addce59f39da2dc39215b63e40ce58b`](https://github.com/justinblethrow-cloud/dUMI/commit/92680cd98addce59f39da2dc39215b63e40ce58b) |
| Canonical upstream baseline | [`efeab35f5d29dec1d496ade3f681eeb34d9c2057`](https://github.com/Daniel-Liu-c0deb0t/UMICollapse/commit/efeab35f5d29dec1d496ade3f681eeb34d9c2057) |
| Final acceptance decision | **Published and externally verified** |

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
proposals. Production sources from the frozen commit above, not a mutable
branch name or working tree, are the authority for the code accepted by this
record. The sealed benchmark retains the exact measured harness snapshot. The
final tag target may add documentation, curated evidence, and neutral
publication-policy cleanup; its production-source hash must remain the
accepted value recorded below.

## Acceptance environments

The final local gates ran on this environment:

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

The CI workflow and its third-party actions are commit-SHA pinned. All
publication workflows completed successfully:

- [main-branch CI run 30175044210](https://github.com/justinblethrow-cloud/dUMI/actions/runs/30175044210);
- [dependency-audit run 30175085188](https://github.com/justinblethrow-cloud/dUMI/actions/runs/30175085188);
- [tag CI run 30175110090](https://github.com/justinblethrow-cloud/dUMI/actions/runs/30175110090); and
- [release run 30175110095, attempt 2](https://github.com/justinblethrow-cloud/dUMI/actions/runs/30175110095/attempts/2).

The release workflow built and verified the archive, SBOM, checksums, receipt,
and GitHub provenance attestations from the final tag target.

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
| Java 11 `./scripts/check.sh` | Exit 0; all tests pass; class major version 55 | **Pass** — final line: `Full verification passed with Java 11-compatible bytecode.` |
| Java 21 `./scripts/check.sh` | Exit 0; all tests pass; class major version 55 | **Pass** — final line: `Full verification passed with Java 11-compatible bytecode.` |
| `git diff --check` | Exit 0 | **Pass** |
| Shell, Python, and workflow syntax checks | Exit 0 | **Pass** — Bash syntax, Python compilation, and workflow YAML parsing succeeded |
| Neutral-branding and path scan | No organization-specific branding or private path references | **Pass** — repository scan and benchmark privacy scan succeeded |

## Test and regression matrix

[`test.sh`](test.sh) is the manifest for the test suite. The frozen candidate
must pass every row below on both local acceptance JDKs.

| Test surface | Evidence | Contract exercised | Candidate result |
| --- | --- | --- | --- |
| Encoded UMI state | [`TestBitSet.java`](src/test/TestBitSet.java) | Distance, mutation, cloning, equality, hashing, ordering, and `N` metadata | **Pass on Java 11 and Java 21** |
| Sequential structures | [`TestDataStructures.java`](src/test/TestDataStructures.java) | Reference behavior across the sequential data-structure implementations | **Pass on Java 11 and Java 21** |
| Parallel structures | [`TestParallelDataStructures.java`](src/test/TestParallelDataStructures.java) | Parallel data-structure behavior | **Pass on Java 11 and Java 21** |
| Optimized parser and core | [`TestOptimizedRegressions.java`](src/test/TestOptimizedRegressions.java) | Fast UMI parsing, lazy quality calculation, singleton semantics, and packed n-gram routing | **Pass on Java 11 and Java 21** |
| Generated-key N handling | [`TestNKeyRegressions.java`](src/test/TestNKeyRegressions.java) | `Combo`, `Trie`, and `SymmetricDelete` parity with `Naive` for `N`-containing UMIs | **Pass on Java 11 and Java 21** |
| Threshold and parallel ties | [`TestThresholdParallelRegressions.java`](src/test/TestThresholdParallelRegressions.java) | Historical float rounding, saturation, and reverse-insertion determinism for the parallel algorithms | **Pass on Java 11 and Java 21** |
| Parallel traversal scheduling | [`TestParallelTraversalScheduling.java`](src/test/TestParallelTraversalScheduling.java) | Dense-component single scheduling plus sequential/parallel representative parity | **Pass on Java 11 and Java 21** |
| Deep BK-tree traversal | [`TestBKTreeDepthRegressions.java`](src/test/TestBKTreeDepthRegressions.java) | Stack-safe deep search, removal, statistics, sequential/parallel parity, and concurrent queries | **Pass on Java 11 and Java 21** |
| Resource and bound handling | [`TestResourceAndBoundsRegressions.java`](src/test/TestResourceAndBoundsRegressions.java) | Malformed-input cleanup, repeated file-descriptor checks, auto-detected `k` parity, construction failure, and exact counter overflow | **Pass on Java 11 and Java 21** |
| Release correctness | [`TestReleaseRegressions.java`](src/test/TestReleaseRegressions.java) | Literal separators, short-UMI rejection, deterministic ties, equality contracts, argument bounds, and packed-map capacity | **Pass on Java 11 and Java 21** |
| SAM/BAM hardening | [`TestDeduplicateSAMHardening.java`](src/test/TestDeduplicateSAMHardening.java) | Selected-pair mate recovery for SAM, indexed BAM, and unindexed BAM; singleton routing; output-format fallback; and alignment-key contracts | **Pass on Java 11 and Java 21** |
| N-gram differential matrix | [`TestNgramBKTreeRegression.java`](src/test/TestNgramBKTreeRegression.java) | `NgramBKTree` and `SortNgramBKTree` versus `Naive` over 86 deterministic randomized scenarios, including packed-key boundaries at UMI lengths 255 and 256 | **Pass on Java 11 and Java 21** |
| Streaming and route integration | [`test/test-streaming.sh`](test/test-streaming.sh) | SAM/BAM parity, algorithms, merge policies, ten sequential structures, header semantics, runtime guards, fallback, legacy/two-pass/parallel parity, tagging, paired recovery, and destination preservation | **Pass on Java 11 and Java 21** |
| CLI, FASTQ, and transactions | [`test/test-cli.sh`](test/test-cli.sh) | Help/version, invalid values and combinations, same-file and hard-link rejection, literal separators, output-format rules, runtime failure cleanup, multi-read FASTQ collapse/tagging, gzip, and atomic destination replacement | **Pass on Java 11 and Java 21** |

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

Final benchmark semantic-equivalence result: **Pass**. All 336 predeclared
measurements were present; all 72 correctness cells and all 54 matched
comparisons passed, with seven successful repetitions or pairs and zero
failures in every applicable row.

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

Final transactional/CLI result: **Pass on Java 11 and Java 21**.

### Paired and deterministic behavior

Paired regressions cover indexed BAM query recovery, sequential recovery from
SAM and unindexed BAM, cross-reference flushing, and final-reference mate
recovery. Output must retain both ends of every selected pair in the fixtures.

Mapping-quality and average-quality ties use a stable record-content ordering.
Directional, adjacency, connected-components, and their applicable parallel
variants use stable UMI ordering and iterative component traversal. The
`any` merge policy remains intentionally arbitrary and is not represented as
deterministic.

Final paired and determinism result: **Pass on Java 11 and Java 21**.

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

Final local OSV result: **Pass on 2026-07-25 UTC**. The live query returned no
active advisories for either locked Maven package. The published
[dependency-audit workflow](https://github.com/justinblethrow-cloud/dUMI/actions/runs/30175085188)
repeated the network check successfully before release.

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
| Complete build-input SHA-256 | `b5811dc7a957935c36093003523b86fc95fdffc06ff9ee1e5dde2b22c70352d3` |
| Production-source SHA-256 | `6d5eadb3ca7c033775e3d5e5dc12abd344a2c57f4b5bf619b3829429b859a679` |
| JAR SHA-256, first Java 11 build | `ca3da2914342d365cb3dbec065fc68743ca45a0de0032aa40e3f076ad3c1807e` |
| JAR SHA-256, second Java 11 build | `ca3da2914342d365cb3dbec065fc68743ca45a0de0032aa40e3f076ad3c1807e` |
| Byte-for-byte JAR comparison | **Pass** |
| Published `BUILD-RECEIPT-2.0.0.properties` SHA-256 | `3cdff74cac0815e02cc989803f201d3a35745a7db9eaf1a44dab322e1e7b56a6` |
| Published `SHA256SUMS` SHA-256 | `dc32c0b2696efe049192858cd547b2864479dd248e26b02f97daf20cc63ac456` |
| Published `dumi-2.0.0.spdx` SHA-256 | `bc1844ced45e0b1374ae70e75fa3d40a0c301b0f5d69670dad8b819b853f10a0` |
| Published `dumi-2.0.0.tar.gz` SHA-256 | `5a8b0ac6260286c8b45715ba2f727b445db140afd252185080912f86501439a9` |
| Downloaded release checksum verification | **Pass** |
| GitHub provenance attestation | **Pass** — all four published assets bind to tag `v2.0.0` and commit `92680cd98addce59f39da2dc39215b63e40ce58b` |

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

Final benchmark evidence:
[`docs/benchmarks/2026-07-25/`](docs/benchmarks/2026-07-25/). The reportable
run used frozen dUMI commit `2995329`, runtime ID
`04385711a6838b779a934a4d3b0ae9d2a71106e108b465895f284a1fa0aa4566`,
the standard profile, and seven repetitions. The original full-bundle
manifest, the primary-evidence manifest, and the curated package
`SHA256SUMS` all verified.

## Profiling evidence

Profiling is used to examine allocation pressure; it is not substituted for
correctness tests or end-to-end benchmarks. Earlier diagnostic recordings
motivated the singleton bypass and lazy one-entry accumulator, but they used a
different workload and are not reported as before/after release evidence.

The final public profile used frozen commit `2995329`, Java 21, three forced
streaming runs, and the deterministic one-million-record sparse workload
documented in [`scripts/profile/README.md`](scripts/profile/README.md). All
three outputs passed `samtools quickcheck`, exact record-multiset and reference
dictionary checks, selected the streaming route without fallback, and had the
required `SO:unsorted` declaration.

The path-neutral aggregate contains 2,504 sampled allocation events. In each
run, all three expected-absent singleton-path sentinels had zero observations,
while both positive-control sentinels were observed. A zero sampled weight is
not proof that an allocation can never occur; it means that this statistical
sampler did not observe the site under the accepted workload.

Final post-change profile result: **Pass**. The aggregate, semantic receipt,
source/build/runtime receipt, and checksums are retained under
[`docs/benchmarks/2026-07-25/profile/`](docs/benchmarks/2026-07-25/profile/).
Raw JFR files are intentionally excluded because they can contain runtime
metadata and are not required to reproduce the published aggregate.

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

## Source acceptance checklist

- [x] The production-source and measured-harness freeze is recorded by
      immutable commit; the sealed bundle retains the exact harness snapshot.
- [x] Generated JARs and build outputs are excluded from source control.
- [x] Java 11 and Java 21 local gates pass from the frozen source.
- [x] Strict compilation and all regression suites pass.
- [x] Consecutive Java 11 release-candidate builds are byte-for-byte identical.
- [x] Locked dependency checksums and the live local OSV audit pass.
- [x] Benchmark outputs are semantically equivalent and both retained checksum
      layers verify.
- [x] Final benchmark results and limitations are published in
      [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md).
- [x] The post-change profile receipt is retained and interpreted.
- [x] Public documentation and curated evidence contain no
      organization-specific branding, private paths, or unsupported
      performance claims.
- [x] Independent code, benchmark, documentation, and release-workflow reviews
      have no unresolved release-blocking findings.
- [x] Main and tag CI, the network dependency audit, and release workflow pass
      for the final tag target.
- [x] All four release assets verify after download and have GitHub provenance
      attestations bound to `v2.0.0` and its target commit.

## Publication outcome

The annotated `v2.0.0` tag targets
[`92680cd98addce59f39da2dc39215b63e40ce58b`](https://github.com/justinblethrow-cloud/dUMI/commit/92680cd98addce59f39da2dc39215b63e40ce58b).
The final main and tag CI matrices, live dependency audit, reproducible release
build, remote-asset digest checks, downloaded checksums, and provenance
attestations all passed. The resulting
[GitHub release](https://github.com/justinblethrow-cloud/dUMI/releases/tag/v2.0.0)
contains the four verified assets recorded above.

Final acceptance decision: **the frozen production source, measured validation
tool snapshot, benchmark, profile, neutral publication cleanup, public
documentation, and published v2.0.0 artifacts are accepted and verified.**
