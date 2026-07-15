# dUMI validation record

## 2026-07-15 default-branch hardening acceptance

This acceptance covers the maintained `optimization/streaming-fastpath` tree
prepared as the public default branch. dUMI branding, the project-local JDK
fallback, and guarded `auto` streaming remain intentional fork behavior.

Local environment and artifact receipts:

- Full gate passed under OpenJDK `11.0.31` and OpenJDK `21.0.11`; compilation
  targets Java 11 class version 55.
- Production source receipt:
  `07fa663989b7072b3fca25246cb45830e9aa045231250940f77aa5495a881392`
- Java 11-built `umicollapse.jar` SHA-256:
  `1c33941e652f9eaa08479ccce321b70b36dbcf2101e43c104f322527f856f7de`
- Manifest identity: `Implementation-Title: dUMI`,
  `Implementation-Version: streaming-fastpath`.

The expanded gate adds:

- two explicit packed-key boundary scenarios at UMI lengths 255 and 256, for
  86 deterministic `NgramBKTree` versus `Naive` scenarios in total;
- indexed BAM input parity for the streaming path;
- paired indexed-BAM coverage for cross-reference flushing and final-pass mate
  recovery;
- a no-flag regression proving that dUMI still selects guarded `auto`
  streaming and declares reordered output `SO:unsorted`;
- Bash 3-compatible launchers and builds, portable Linux/macOS SHA-256 tooling,
  and Linux Java 11/21 plus macOS Java 11 CI.

The stable branch-specific CI history is available at
[GitHub Actions](https://github.com/justinblethrow-cloud/dUMI/actions/workflows/ci.yml?query=branch%3Aoptimization%2Fstreaming-fastpath).

### Refreshed streaming smoke benchmark

Command:

```
./scripts/benchmark-streaming.sh 100000
```

| Mode | Records | Elapsed seconds | Maximum RSS (KiB) | Record SHA-256 |
| --- | ---: | ---: | ---: | --- |
| `off` | 100,000 | 1.42 | 272,608 | `6ca5d46803557b3bc48b30cad22b8e9b42793acfb721a864792dd2cf3ce6de47` |
| `on` | 100,000 | 0.79 | 192,168 | `6ca5d46803557b3bc48b30cad22b8e9b42793acfb721a864792dd2cf3ce6de47` |

For this synthetic workload, forced streaming reduced elapsed time by about
44% and maximum RSS by about 30%, with identical record output. These figures
remain a smoke-test regression signal rather than a production DGE claim.

## 2026-07-15 migration-remediation acceptance

The remediation was validated in the migrated checkout at
`/mnt/datavault/Agentic/dUMI`, based on Git commit `f133fd8` plus the changes
described by the eventual remediation commit.

Environment:

- Linux `6.8.0-134-generic`, x86-64
- AMD EPYC 7742 host
- Full gate passed under OpenJDK `11.0.31` and OpenJDK `21.0.11`; compilation
  targets Java 11 class version 55
- HTSJDK `2.19.0`, SHA-256
  `06390af88c23d06d69521f0f88c06236bcd527dc0c6bd51e36fc2525d0a47819`
- snappy-java `1.1.7.3`, SHA-256
  `7eea31c0a25d35cd092d8aec08bed04f22152409b58d63d43839074a9ab7ab97`

The host had Java runtimes but no system JDK and sudo installation was not
available. A complete OpenJDK 21 toolchain was therefore provisioned under the
ignored `.tools/jdk/` path. A temporary OpenJDK 11.0.31 toolchain was also used
to execute the full minimum-version gate. `build.sh` discovers `JAVA_HOME`, the
project-local toolchain, or system `javac`/`jar`, in that order.

Accepted artifact receipts:

- Production source receipt:
  `3dab98b4dc482f296d7d66c15856ebd39457a150f9f8ea99736de2f862969b10`
- Bundled Java 11-built `umicollapse.jar` SHA-256:
  `e334d6af446975a4a13adf1cff6e467590ffd163b998da6b9400f1b7e79b13e5`

## Acceptance gate

Command:

```
./scripts/check.sh
```

Required results:

- dependency checksums verified;
- production and test classes compiled from a clean build directory;
- production-only JAR generated with an embedded source hash;
- embedded source hash matches the current production source tree;
- Java class major version is 55;
- assertion-based BitSet, optimized-parser, singleton-algorithm, sequential,
  and parallel data-structure tests pass;
- 84 deterministic randomized `NgramBKTree` versus `Naive` scenarios pass
  across UMI lengths 4 through 64, edit bounds 0 through 2, finite frequency
  limits, present queries, and absent queries;
- streaming record multisets match the legacy path across all three
  algorithms, all three merge policies, and all ten sequential data
  structures;
- both SAM and BAM output are readable and equivalent;
- the exact 10,000-base clipping boundary succeeds;
- declared-unsorted input, false coordinate metadata, and excessive clipping
  take their specified reject or automatic-fallback paths;
- forced streaming incompatibilities reject explicitly;
- failed forced-streaming runs preserve an existing destination;
- legacy and two-pass output retain coordinate sort metadata, while reordered
  streaming output declares `SO:unsorted`.

GitHub Actions runs this same gate on Java 11 and Java 21 after publication.

## Deterministic streaming smoke benchmark

Command:

```
./scripts/benchmark-streaming.sh 100000
```

The harness generated 100,000 coordinate-sorted, single-end SAM records with
one alignment group per coordinate. `/usr/bin/time` measured each dUMI process;
record-only output hashes were required to match.

| Mode | Records | Elapsed seconds | Maximum RSS (KiB) | Record SHA-256 |
| --- | ---: | ---: | ---: | --- |
| `off` | 100,000 | 1.38 | 264,192 | `6ca5d46803557b3bc48b30cad22b8e9b42793acfb721a864792dd2cf3ce6de47` |
| `on` | 100,000 | 0.75 | 207,236 | `6ca5d46803557b3bc48b30cad22b8e9b42793acfb721a864792dd2cf3ce6de47` |

In this smoke workload, forced streaming reduced elapsed time by about 46% and
maximum RSS by about 22%, with identical record output. These numbers are a
repeatable regression signal, not a production DGE performance claim; real
results depend on alignment density, UMI distribution, CIGAR structure,
storage, and JVM settings.

## Known boundaries

- The dependency versions are now locked and reproducible, but this work did
  not upgrade their age or security posture. Any upgrade should be evaluated
  through the same SAM/BAM equivalence gate.
- No production-sized DGE BAM is stored in this repository. Production
  throughput and memory validation remains a separate workload-level gate.
- Raw JAR bytes can vary across JDK patch versions because ZIP timestamps and
  compiler metadata are not promised byte-for-byte deterministic. The embedded
  source receipt establishes source correspondence instead.
